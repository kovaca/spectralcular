"""Coarse-resolution review previews + STAC feasibility checks.

Reuses the existing data_loader pipeline at 240 m (one COG overview level
coarser than the diagnostic-page thumbnail at 80 m) to keep the review
loop fast: each ~26 km AOI becomes ~110 × 140 px, which loads in
seconds, not minutes. RGB only — colorized plate previews are deferred
to stage-4 matrix gating.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rioxarray  # noqa: F401  (registers .rio accessor)
from odc.stac import load as odc_load
from pystac_client import Client

from ..config import Imagery
from ..data_loader import (
    SceneSet,
    _scene_record_from_item,
    _tile_key,
)
from ..platforms import EARTH_SEARCH_URL
from ..pool import LocationPoolEntry
from ..render_assets import render_rgb_thumbnail_png

log = logging.getLogger(__name__)

# Coarse-preview resolution. 80 m matches S2 COG overview level 3 — sharp
# enough that a 25 km AOI reads as a real photo (≈300 × 350 px), not a
# postage stamp. Trades fetch time (≈10 s/location) for review usability.
REVIEW_RES_M = 80


# ---------------------------------------------------------------------------
# STAC feasibility
# ---------------------------------------------------------------------------

@dataclass
class FeasibilityResult:
    season_window: str
    n_scenes: int
    n_tiles: int
    min_cloud: float | None
    mean_cloud: float | None
    error: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def count_scenes(
    bbox: tuple[float, float, float, float],
    season_window: str,
    cloud_cover_max: float,
) -> FeasibilityResult:
    """STAC search count + cloud stats. No load, just metadata."""
    try:
        client = Client.open(EARTH_SEARCH_URL)
        items = list(
            client.search(
                collections=["sentinel-2-l2a"],
                bbox=list(bbox),
                datetime=season_window,
                query={"eo:cloud_cover": {"lt": cloud_cover_max}},
                limit=200,
            ).items()
        )
    except Exception as e:
        return FeasibilityResult(
            season_window=season_window,
            n_scenes=0,
            n_tiles=0,
            min_cloud=None,
            mean_cloud=None,
            error=f"{type(e).__name__}: {e}",
        )

    if not items:
        return FeasibilityResult(
            season_window=season_window,
            n_scenes=0,
            n_tiles=0,
            min_cloud=None,
            mean_cloud=None,
        )

    tiles = {_tile_key(it) for it in items}
    clouds = [float(it.properties.get("eo:cloud_cover", 100.0)) for it in items]
    return FeasibilityResult(
        season_window=season_window,
        n_scenes=len(items),
        n_tiles=len(tiles),
        min_cloud=min(clouds),
        mean_cloud=sum(clouds) / len(clouds),
    )


def feasibility_for_entry(entry: LocationPoolEntry) -> list[FeasibilityResult]:
    return [
        count_scenes(entry.bbox, w, entry.cloud_cover_max)
        for w in entry.season_windows
    ]


# ---------------------------------------------------------------------------
# Coarse RGB preview
# ---------------------------------------------------------------------------

@dataclass
class ReviewPreview:
    location_id: str
    rgb_png: Path | None
    scene_set: SceneSet | None
    error: str | None = None
    feasibility: list[FeasibilityResult] = field(default_factory=list)
    coverage_fraction: float | None = None  # fraction of bbox covered by valid pixels


def fetch_review_rgb(
    entry: LocationPoolEntry,
    *,
    cache_dir: Path,
    rgb_keys: tuple[str, str, str] = ("red", "green", "blue"),
    refresh: bool = False,
) -> ReviewPreview:
    """240 m RGB mosaic for the review contact sheet.

    Picks the *first* season window with cloud-feasible scenes. If no
    window has any scenes, returns an error preview with feasibility
    results attached so the caller can render the ⚠ state.

    Cache layout: ``cache_dir/<entry.id>/{scenes.json, rgb_240m.png}``.
    Second run is a no-op unless ``refresh=True``.
    """
    out_dir = cache_dir / entry.id
    out_dir.mkdir(parents=True, exist_ok=True)
    rgb_png = out_dir / "rgb_240m.png"
    scenes_json = out_dir / "scenes.json"
    feasibility_json = out_dir / "feasibility.json"

    feasibility = feasibility_for_entry(entry)
    feasibility_json.write_text(
        json.dumps([f.to_dict() for f in feasibility], indent=2)
    )

    coverage_json = out_dir / "coverage.json"
    if not refresh and rgb_png.exists() and scenes_json.exists():
        scene_set = SceneSet.from_dict(json.loads(scenes_json.read_text()))
        coverage = (
            json.loads(coverage_json.read_text())["fraction"]
            if coverage_json.exists() else None
        )
        return ReviewPreview(
            location_id=entry.id,
            rgb_png=rgb_png,
            scene_set=scene_set,
            feasibility=feasibility,
            coverage_fraction=coverage,
        )

    # Search every season window, expanded ±2 years, then pick the
    # single lowest-cloud scene that fully covers the bbox. Stage 3 is
    # editorial review — we want the *prettiest* clean preview, not the
    # closest match to a particular calendar window.
    expanded = _expand_windows(entry.season_windows, year_radius=2)

    try:
        items = _search_scenes_covering_bbox_multiyear(
            tuple(entry.bbox), expanded,
            cloud_cover_max=entry.cloud_cover_max,
            location_label=entry.name,
        )
        if not items:
            return ReviewPreview(
                location_id=entry.id,
                rgb_png=None,
                scene_set=None,
                error="no scenes with adequate spatial coverage in any year",
                feasibility=feasibility,
            )
        # Verify RGB assets exist (some collections lack them).
        if not all(k in items[0].assets for k in rgb_keys):
            missing = [k for k in rgb_keys if k not in items[0].assets]
            return ReviewPreview(
                location_id=entry.id,
                rgb_png=None,
                scene_set=None,
                error=f"RGB assets {missing} missing from STAC items",
                feasibility=feasibility,
            )

        rgb_arrays, target_crs = _load_best_coverage_rgb(
            items, tuple(entry.bbox), list(rgb_keys),
        )

        # Coverage = fraction of bbox cells with valid (non-zero, finite) data
        # in *all three* RGB bands. odc-stac fills uncovered pixels with the
        # band's nodata (0 for S2 L2A) when no scene contributes — so the
        # red-channel zero count is a good proxy for partial coverage.
        coverage_fraction = _compute_coverage(rgb_arrays)
        coverage_json.write_text(json.dumps({"fraction": coverage_fraction}, indent=2))

        render_rgb_thumbnail_png(rgb_arrays, rgb_png, target_long_edge_px=1600)

        # Persist scene set so re-renders without --no-fetch are still cheap.
        scene_records = [
            _scene_record_from_item(it, "sentinel-2-l2a", target_crs)
            for it in items
        ]
        scene_set = SceneSet(
            scenes=scene_records,
            target_crs=target_crs,
            bbox_lonlat=list(entry.bbox),
        )
        scenes_json.write_text(json.dumps(scene_set.to_dict(), indent=2))

        return ReviewPreview(
            location_id=entry.id,
            rgb_png=rgb_png,
            scene_set=scene_set,
            feasibility=feasibility,
            coverage_fraction=coverage_fraction,
        )
    except Exception as e:
        log.warning("Preview fetch failed for %s: %s", entry.id, e)
        return ReviewPreview(
            location_id=entry.id,
            rgb_png=None,
            scene_set=None,
            error=f"{type(e).__name__}: {e}",
            feasibility=feasibility,
        )


def _expand_windows(season_windows: list[str], *, year_radius: int = 2) -> list[str]:
    """Expand each ``YYYY-MM-DD/YYYY-MM-DD`` window across ±year_radius years.

    Stage 3 reviewers want the prettiest *clean* image, not the closest
    calendar match. Searching adjacent years dramatically increases the
    odds of hitting a near-zero-cloud pass. The original windows are
    preserved (and listed first) so calendar-bound features (e.g. a
    specific bloom or burn event) still get prioritized.
    """
    out: list[str] = list(season_windows)
    for w in season_windows:
        start, end = w.split("/")
        sy, ey = int(start[:4]), int(end[:4])
        for delta in range(-year_radius, year_radius + 1):
            if delta == 0:
                continue
            new_start = f"{sy + delta:04d}{start[4:]}"
            new_end = f"{ey + delta:04d}{end[4:]}"
            cand = f"{new_start}/{new_end}"
            if cand not in out:
                out.append(cand)
    return out


def _search_scenes_covering_bbox_multiyear(
    bbox: tuple[float, float, float, float],
    season_windows: list[str],
    *,
    cloud_cover_max: float,
    location_label: str = "",
    min_coverage: float = 0.98,
    max_scenes: int = 12,
) -> list:
    """Multi-window scene search → pick *single* lowest-cloud full-coverage scene.

    For each window, run ``_search_scenes_covering_bbox`` (which already
    filters by geometric coverage). Across all results, sort by
    ``eo:cloud_cover`` ascending and return the lowest-cloud scene
    individually first; same-day mosaic fallback only kicks in when no
    single scene clears the coverage threshold across any year.
    """
    full_cover_pool: list = []
    fallback_pool: list = []  # for the same-day-mosaic fallback if needed
    for w in season_windows:
        imagery_w = Imagery(
            source="sentinel-2-l2a",
            date_range=w,
            cloud_cover_max=cloud_cover_max,
        )
        items = _search_scenes_covering_bbox(
            bbox, imagery_w, location_label=f"{location_label} {w}",
            min_coverage=min_coverage, max_scenes=max_scenes,
            mosaic_fallback=False,
        )
        if items:
            full_cover_pool.extend(items)
            continue
        # Save the fallback mosaic from this year for later if needed.
        items_mosaic = _search_scenes_covering_bbox(
            bbox, imagery_w, location_label=f"{location_label} {w}",
            min_coverage=min_coverage, max_scenes=max_scenes,
            mosaic_fallback=True,
        )
        if items_mosaic:
            fallback_pool.append(items_mosaic)

    if full_cover_pool:
        # Dedup by item id; keep lowest-cloud entry per id.
        best_by_id: dict[str, object] = {}
        for it in full_cover_pool:
            cur = best_by_id.get(it.id)
            if cur is None or it.properties.get("eo:cloud_cover", 100) < cur.properties.get("eo:cloud_cover", 100):
                best_by_id[it.id] = it
        ranked = sorted(
            best_by_id.values(),
            key=lambda it: float(it.properties.get("eo:cloud_cover", 100.0)),
        )
        winner = ranked[0]
        log.info(
            "%s — best single full-coverage scene: %s cloud=%.2f%% on %s",
            location_label or "bbox", winner.id,
            float(winner.properties.get("eo:cloud_cover", 100.0)),
            winner.datetime.date().isoformat() if winner.datetime else "?",
        )
        return [winner]

    if fallback_pool:
        # Pick the same-day mosaic with highest combined coverage.
        from shapely.geometry import box, shape
        from shapely.ops import unary_union
        bbox_geom = box(*bbox)
        scored: list[tuple[float, list]] = []
        for items in fallback_pool:
            try:
                geom = unary_union([shape(it.geometry) for it in items if it.geometry])
                cov = geom.intersection(bbox_geom).area / bbox_geom.area
            except Exception:
                cov = 0.0
            scored.append((cov, items))
        scored.sort(key=lambda t: -t[0])
        log.info(
            "%s — no single full-coverage scene; best mosaic %.1f%% (%d scenes)",
            location_label or "bbox", scored[0][0] * 100, len(scored[0][1]),
        )
        return scored[0][1]

    return []


def _search_scenes_covering_bbox(
    bbox: tuple[float, float, float, float],
    imagery: Imagery,
    *,
    location_label: str = "",
    max_scenes: int = 12,
    min_coverage: float = 0.98,
    mosaic_fallback: bool = True,
) -> list:
    """STAC search filtered by actual scene footprint coverage of the bbox.

    Returns lowest-cloud-first items whose geometry covers ≥ ``min_coverage``
    of the bbox. When ``mosaic_fallback`` is True and no single scene meets
    the threshold, returns the best same-day mosaic — caller can still
    render with a partial-coverage indicator. When False, returns ``[]``
    so the multi-year search can decide whether to keep looking.
    """
    from shapely.geometry import box, shape
    bbox_geom = box(*bbox)
    bbox_area = bbox_geom.area
    if bbox_area <= 0:
        return []

    client = Client.open(EARTH_SEARCH_URL)
    candidates = list(
        client.search(
            collections=[imagery.source],
            bbox=list(bbox),
            datetime=imagery.date_range,
            query={"eo:cloud_cover": {"lt": imagery.cloud_cover_max}},
            sortby=[{"field": "properties.eo:cloud_cover", "direction": "asc"}],
            limit=200,
        ).items()
    )
    if not candidates:
        return []

    scored = []
    for it in candidates:
        if it.geometry is None:
            continue
        try:
            geom = shape(it.geometry)
            cov = geom.intersection(bbox_geom).area / bbox_area
        except Exception:
            cov = 0.0
        scored.append((it, float(cov)))

    full_cover = [it for it, cov in scored if cov >= min_coverage]
    if full_cover:
        return full_cover[:max_scenes]

    if not mosaic_fallback:
        return []

    # Fallback: cluster scenes by solar day, pick the day with the best
    # combined coverage. Handles cross-tile mosaics where no single
    # scene covers the bbox but multiple same-day scenes do.
    from collections import defaultdict
    from shapely.ops import unary_union
    by_day: dict[str, list] = defaultdict(list)
    for it, cov in scored:
        if cov <= 0:
            continue
        day = it.datetime.date().isoformat() if it.datetime else "unknown"
        by_day[day].append((it, cov))

    if by_day:
        day_scores: list[tuple[str, float, list]] = []
        for day, lst in by_day.items():
            try:
                day_geom = unary_union([shape(it.geometry) for it, _ in lst])
                day_cov = day_geom.intersection(bbox_geom).area / bbox_area
            except Exception:
                day_cov = max(c for _, c in lst)
            day_scores.append((day, day_cov, [it for it, _ in lst]))
        day_scores.sort(key=lambda t: -t[1])
        best_day, best_cov, items = day_scores[0]
        log.info(
            "%s — best same-day mosaic %.1f%% on %s (%d tile(s))",
            location_label or "bbox", best_cov * 100, best_day, len(items),
        )
        return items[:max_scenes]

    return []


def _load_best_coverage_rgb(
    items: list,
    bbox: tuple[float, float, float, float],
    rgb_keys: list[str],
) -> tuple[dict[str, np.ndarray], str]:
    """Load all solar-day mosaics for ``items`` and pick the one with
    the most bbox coverage.

    The production loader picks ``time=0`` (the earliest day among
    cloud-best-per-tile picks). When tiles' best-cloud days differ, that
    earliest day's scene only fills the part of the bbox covered by its
    own tile — leaving 80%+ of the bbox as nodata. This loader inverts
    the choice: load every day, count populated pixels, return the
    timestep that fills the bbox most completely.
    """
    ds = odc_load(
        items,
        bands=rgb_keys,
        bbox=list(bbox),
        crs="utm",
        resolution=REVIEW_RES_M,
        groupby="solar_day",
        chunks={},
        fail_on_error=False,
    )

    target_crs = str(ds.rio.crs) if ds.rio.crs else ""

    # Coverage per timestep, scored on the red band's populated cells.
    if "time" in ds.dims and ds.sizes.get("time", 1) > 1:
        red = ds[rgb_keys[0]]
        # populated = finite & nonzero
        per_t = ((red.astype("float32") > 0) & np.isfinite(red.astype("float32"))).sum(
            dim=[d for d in red.dims if d != "time"]
        ).values
        best_t = int(np.argmax(per_t))
        log.info(
            "Picked time=%d of %d for max coverage (%d populated px)",
            best_t, ds.sizes["time"], int(per_t[best_t]),
        )
        ds = ds.isel(time=best_t)
    elif "time" in ds.dims:
        ds = ds.squeeze("time", drop=True)

    out: dict[str, np.ndarray] = {}
    for band in rgb_keys:
        if band not in ds.data_vars:
            raise KeyError(f"Band {band!r} missing from odc-stac result")
        arr = np.asarray(ds[band].values)
        out[band] = arr
    return out, target_crs


def _compute_coverage(rgb_arrays: dict[str, np.ndarray]) -> float:
    """Fraction of bbox cells with finite, non-zero data in all RGB bands.

    Conservative: a pixel is "valid" only if all three channels are populated.
    Single-tile mosaics with full coverage land at ~1.0; cross-tile mosaics
    where one or more tiles missed typically land at 0.4–0.9.
    """
    masks = []
    for arr in rgb_arrays.values():
        a = np.asarray(arr, dtype="float32")
        masks.append(np.isfinite(a) & (a > 0))
    if not masks:
        return 0.0
    valid = masks[0]
    for m in masks[1:]:
        valid = valid & m
    return float(valid.sum()) / float(valid.size) if valid.size else 0.0


# ---------------------------------------------------------------------------
# Bbox geometry helpers — bbox dimensions in km, page-aspect fit
# ---------------------------------------------------------------------------

def bbox_dimensions_km(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    """Approximate width/height of a lon-lat bbox in km (small-bbox spherical)."""
    lon_min, lat_min, lon_max, lat_max = bbox
    lat_mid = (lat_min + lat_max) / 2.0
    height_km = (lat_max - lat_min) * 111.32
    width_km = (lon_max - lon_min) * 111.32 * np.cos(np.radians(lat_mid))
    return float(width_km), float(height_km)


def page_aspect_fit(
    bbox: tuple[float, float, float, float],
    *,
    page_aspect: float = 8.5 / 11.0,
    tolerance: float = 0.10,
) -> tuple[bool, str]:
    """Compare bbox aspect (W/H, geo-corrected) to portrait Letter aspect."""
    w_km, h_km = bbox_dimensions_km(bbox)
    if h_km <= 0:
        return False, "degenerate bbox"
    bbox_aspect = w_km / h_km
    delta = abs(bbox_aspect - page_aspect) / page_aspect
    if delta <= tolerance:
        return True, f"aspect {bbox_aspect:.2f} matches portrait {page_aspect:.2f}"
    direction = "wider than portrait — trim sides" if bbox_aspect > page_aspect else "taller than portrait — trim top/bottom"
    return False, f"aspect {bbox_aspect:.2f} vs portrait {page_aspect:.2f} ({direction})"


def aoi_ceiling_check(
    bbox: tuple[float, float, float, float],
    *,
    max_w_km: float = 26.25,
    max_h_km: float = 33.75,
) -> tuple[bool, str]:
    """Check the bbox stays inside the S2 10 m + Letter portrait + bleed AOI ceiling."""
    w_km, h_km = bbox_dimensions_km(bbox)
    if w_km <= max_w_km and h_km <= max_h_km:
        return True, f"{w_km:.1f} × {h_km:.1f} km inside ceiling"
    return False, (
        f"{w_km:.1f} × {h_km:.1f} km exceeds {max_w_km:.2f} × {max_h_km:.2f} km "
        f"— full-res render will resample"
    )
