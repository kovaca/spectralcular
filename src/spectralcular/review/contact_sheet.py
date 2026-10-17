"""Render review contact sheets — three static HTML pages, one per pool.

Each page is self-contained: assets are inlined or referenced relative
to the HTML file, so the reviewer can scroll a single browser tab and
not chase broken links. The pages share a tiny header bar showing
status counts so review progress is visible at a glance.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import jinja2
import numpy as np
import spyndex

from ..catalog import CatalogError, CatalogRecord, load_catalog
from ..colormaps import colormap_short_name, resolve_colormap
from ..indices import INDEX_DOMAIN
from ..pool import (
    ColormapPool,
    ColormapPoolEntry,
    IndexPool,
    IndexPoolEntry,
    LocationPool,
    LocationPoolEntry,
    ReviewStatus,
)
from ..render_assets import (
    render_colormap_oklch_charts_svg,
    render_gradient_band_png,
    render_histogram_svg,
    render_index_png,
    simulate_cvd_png,
)
from .preview import (
    ReviewPreview,
    aoi_ceiling_check,
    bbox_dimensions_km,
    fetch_review_rgb,
    feasibility_for_entry,
    page_aspect_fit,
)


if TYPE_CHECKING:
    from ..candidates import CandidateEntry

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"


# ---------------------------------------------------------------------------
# Geojson.io URL fragment
# ---------------------------------------------------------------------------

def geojsonio_url_for_bbox(bbox: tuple[float, float, float, float], name: str) -> str:
    """Build a geojson.io URL via its ``#data=`` URL fragment API.

    No upload, no auth; the geometry rides in the fragment. geojson.io
    decodes a single-feature ``FeatureCollection`` and centers the map.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": name},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [lon_min, lat_min],
                        [lon_max, lat_min],
                        [lon_max, lat_max],
                        [lon_min, lat_max],
                        [lon_min, lat_min],
                    ]],
                },
            }
        ],
    }
    encoded = urllib.parse.quote(json.dumps(fc), safe="")
    return f"https://geojson.io/#data=data:application/json,{encoded}"


# ---------------------------------------------------------------------------
# Card data builders
# ---------------------------------------------------------------------------

@dataclass
class LocationCard:
    entry: LocationPoolEntry
    preview: ReviewPreview
    rgb_rel: str | None
    width_km: float
    height_km: float
    aspect_ok: bool
    aspect_msg: str
    ceiling_ok: bool
    ceiling_msg: str
    geojsonio_url: str

    @property
    def status(self) -> str:
        return self.entry.status.value


@dataclass
class IndexCard:
    entry: IndexPoolEntry
    formula: str
    long_name: str
    bands_pretty: str
    resolution_floor_m: int
    domain: tuple[float, float] | None

    @property
    def status(self) -> str:
        return self.entry.status.value


@dataclass
class ColormapCard:
    entry: ColormapPoolEntry
    gradient_rel: str
    oklch_rel: str
    # (label, css class) chips derived from the colorcurious metrics, if any
    metric_chips: list[tuple[str, str]]
    attribution: str | None = None

    @property
    def status(self) -> str:
        return self.entry.status.value


def _catalog_record_for(entry: ColormapPoolEntry) -> CatalogRecord | None:
    if entry.source is None or not entry.source.catalog_id:
        return None
    try:
        return load_catalog().get(entry.source.catalog_id)
    except CatalogError:
        return None


# Above this OKLab chroma, blue/violet/magenta ramps are the usual sRGB→CMYK
# casualties. A hint for the reviewer only — real print safety needs an ICC
# soft-proof, so ``print_safety`` is never written automatically.
_GAMUT_RISK_CHROMA = 0.20
_GAMUT_RISK_HUES = {"blue", "violet", "magenta"}


def _metric_chips(entry: ColormapPoolEntry, rec: CatalogRecord | None) -> list[tuple[str, str]]:
    chips: list[tuple[str, str]] = []
    if entry.source is not None:
        lic = entry.source.license or "license?"
        chips.append((f"{entry.source.collection} · {lic}", "tag"))
    if rec is None:
        return chips
    if entry.type == "sequential" and rec.lightness_monotonic is not None:
        chips.append(("L monotonic" if rec.lightness_monotonic else "L non-monotonic",
                      "tag ok" if rec.lightness_monotonic else "tag warn"))
    if rec.cvd_preserves_order:
        marks = " ".join(f"{k[0].upper()}{'✓' if v else '✗'}" for k, v in rec.cvd_preserves_order.items())
        chips.append((f"CVD order {marks}", "tag ok" if all(rec.cvd_preserves_order.values()) else "tag warn"))
    if entry.type == "diverging" and rec.diverging_pivot_t is not None:
        chips.append((f"pivot t={rec.diverging_pivot_t:.2f}", "tag"))
    if (rec.chroma_max or 0) > _GAMUT_RISK_CHROMA and set(rec.hue_quadrants) & _GAMUT_RISK_HUES:
        chips.append(("gamut risk: saturated blue/violet", "tag warn"))
    return chips


def _bands_pretty(bands: dict[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in bands.items())


def _resolution_floor(bands: dict[str, str]) -> int:
    """20 m floor when SWIR enters; 10 m otherwise."""
    swir = {"swir16", "swir22"}
    return 20 if any(v in swir for v in bands.values()) else 10


def _status_counts(entries: list) -> dict[str, int]:
    c = Counter(e.status.value for e in entries)
    return {s.value: c.get(s.value, 0) for s in ReviewStatus}


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _jinja_env() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES_DIR),
        autoescape=jinja2.select_autoescape(["html", "xml"]),
        undefined=jinja2.StrictUndefined,
    )
    env.filters["round_km"] = lambda v: f"{v:.1f}"
    return env


def render_locations_sheet(
    pool: LocationPool,
    *,
    cache_dir: Path,
    out_dir: Path,
    fetch: bool = True,
    only_id: str | None = None,
    refresh: bool = False,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)

    cards: list[LocationCard] = []
    for entry in pool.entries:
        if only_id is not None and entry.id != only_id and fetch:
            # When --only is set, leave other entries' previews to the cache.
            preview = _cached_preview_or_empty(
                entry, cache_dir, with_feasibility=False,
            )
        else:
            if fetch:
                preview = fetch_review_rgb(
                    entry, cache_dir=cache_dir, refresh=refresh,
                )
            else:
                preview = _cached_preview_or_empty(
                    entry, cache_dir, with_feasibility=False,
                )

        rgb_rel = (
            _relative_path(preview.rgb_png, out_dir)
            if preview.rgb_png is not None and preview.rgb_png.exists()
            else None
        )
        w_km, h_km = bbox_dimensions_km(tuple(entry.bbox))
        aspect_ok, aspect_msg = page_aspect_fit(tuple(entry.bbox))
        ceiling_ok, ceiling_msg = aoi_ceiling_check(tuple(entry.bbox))
        cards.append(
            LocationCard(
                entry=entry,
                preview=preview,
                rgb_rel=rgb_rel,
                width_km=w_km,
                height_km=h_km,
                aspect_ok=aspect_ok,
                aspect_msg=aspect_msg,
                ceiling_ok=ceiling_ok,
                ceiling_msg=ceiling_msg,
                geojsonio_url=geojsonio_url_for_bbox(
                    tuple(entry.bbox), entry.name
                ),
            )
        )

    template = _jinja_env().get_template("locations.html.j2")
    html = template.render(
        cards=cards,
        counts=_status_counts(pool.entries),
        css_rel=_copy_static(out_dir),
    )
    out = out_dir / "locations.html"
    out.write_text(html)
    log.info("Wrote %s", out)
    return out


def render_indices_sheet(pool: IndexPool, *, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    cards: list[IndexCard] = []
    for entry in pool.entries:
        spec = spyndex.indices[entry.name]
        cards.append(
            IndexCard(
                entry=entry,
                formula=spec.formula,
                long_name=spec.long_name,
                bands_pretty=_bands_pretty(entry.bands),
                resolution_floor_m=_resolution_floor(entry.bands),
                domain=INDEX_DOMAIN.get(entry.name),
            )
        )

    # Group by theme for the header summary.
    by_theme: dict[str, int] = Counter(e.theme for e in pool.entries)

    template = _jinja_env().get_template("indices.html.j2")
    html = template.render(
        cards=cards,
        counts=_status_counts(pool.entries),
        by_theme=dict(sorted(by_theme.items())),
        css_rel=_copy_static(out_dir),
    )
    out = out_dir / "indices.html"
    out.write_text(html)
    log.info("Wrote %s", out)
    return out


def render_colormaps_sheet(
    pool: ColormapPool,
    *,
    cache_dir: Path,
    out_dir: Path,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    cards: list[ColormapCard] = []
    for entry in pool.entries:
        slug = entry.name.replace(":", "_").replace(".", "_")
        gradient_png = cache_dir / f"{slug}_gradient.png"
        oklch_svg = cache_dir / f"{slug}_oklch.svg"
        if not gradient_png.exists() or not oklch_svg.exists():
            cmap = resolve_colormap(entry)
            render_gradient_band_png(cmap, gradient_png)
            render_colormap_oklch_charts_svg(cmap, oklch_svg)
        rec = _catalog_record_for(entry)
        cards.append(
            ColormapCard(
                entry=entry,
                gradient_rel=_relative_path(gradient_png, out_dir),
                oklch_rel=_relative_path(oklch_svg, out_dir),
                metric_chips=_metric_chips(entry, rec),
                attribution=entry.source.attribution if entry.source and entry.source.attribution else None,
            )
        )

    template = _jinja_env().get_template("colormaps.html.j2")
    html = template.render(
        cards=cards,
        counts=_status_counts(pool.entries),
        css_rel=_copy_static(out_dir),
    )
    out = out_dir / "colormaps.html"
    out.write_text(html)
    log.info("Wrote %s", out)
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cached_preview_or_empty(
    entry: LocationPoolEntry,
    cache_dir: Path,
    *,
    with_feasibility: bool = False,
) -> ReviewPreview:
    """Read cached preview without going to STAC. Used by --no-fetch / --only.

    When ``with_feasibility=False`` (the default in --no-fetch), the
    feasibility list is read from disk if a sidecar exists, otherwise
    left empty — keeps --no-fetch a true no-network operation.
    """
    out_dir = cache_dir / entry.id
    rgb_png = out_dir / "rgb_240m.png"
    scenes_json = out_dir / "scenes.json"
    feasibility_json = out_dir / "feasibility.json"

    feasibility = []
    if with_feasibility:
        feasibility = feasibility_for_entry(entry)
    elif feasibility_json.exists():
        from .preview import FeasibilityResult
        feasibility = [
            FeasibilityResult(**d) for d in json.loads(feasibility_json.read_text())
        ]

    if rgb_png.exists() and scenes_json.exists():
        from ..data_loader import SceneSet
        scene_set = SceneSet.from_dict(json.loads(scenes_json.read_text()))
        coverage_json = out_dir / "coverage.json"
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
    return ReviewPreview(
        location_id=entry.id,
        rgb_png=None,
        scene_set=None,
        feasibility=feasibility,
        error="not yet fetched (run without --no-fetch to populate cache)",
    )


def _copy_static(out_dir: Path) -> str:
    """Copy review.css next to the HTML; return its relative href."""
    src = STATIC_DIR / "review.css"
    dst = out_dir / "review.css"
    if src.exists():
        dst.write_text(src.read_text())
    return "review.css"


def _relative_path(p: Path, base: Path) -> str:
    """Best-effort relative path; falls back to absolute when outside `base`."""
    try:
        return str(p.resolve().relative_to(base.resolve()))
    except ValueError:
        # Different roots — use a file:// URL so the browser can still load it
        return p.resolve().as_uri()


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


# ===========================================================================
# Stage-4 candidate matrix contact sheet
# ===========================================================================

@dataclass
class CandidateCard:
    entry: "CandidateEntry"
    location_name: str
    index_name: str
    colormap_short: str
    thumb_rel: str | None
    cvd_rel: str | None
    hist_rel: str | None
    stats: dict | None
    print_safety: str
    colormap_type: str
    coverage_pct: float | None  # % of the index's global range this plate spans
    error: str | None

    @property
    def status(self) -> str:
        return self.entry.status.value


@dataclass
class CandidateSubgroup:
    """Colormap variants sharing one (location, index)."""
    location_name: str
    index_name: str
    norm_desc: str
    cards: list[CandidateCard]


@dataclass
class CandidateGroup:
    theme: str
    subgroups: list[CandidateSubgroup]


def render_candidates_sheet(
    candidate_set,
    loc_pool: LocationPool,
    idx_pool: IndexPool,
    cmap_pool: ColormapPool,
    *,
    preview_cache_dir: Path,
    out_dir: Path,
    fetch: bool = True,
    only_id: str | None = None,
    refresh: bool = False,
    norm_method: str = "percentile_pooled",
    cvd_kind: str = "deuteranopia",
) -> Path:
    """Render the Stage-4 matrix contact sheet, grouped theme → (location, index).

    Computes one coarse index raster per (location, index) — reused across all
    its colormap variants — applies the per-index global normalization, then
    colorizes a thumbnail + a colorblind-simulated thumbnail + a mini-histogram
    per candidate. ``--no-fetch`` skips STAC and re-colorizes from cached index
    rasters; missing rasters render an empty card.
    """
    # Local imports avoid a heavy import chain at module load.
    from ..candidate_gen import index_pairing_type
    from ..colormaps import resolve_colormap
    from ..data_loader import open_band
    from ..global_norm import compute_global_norm, save_norm_ranges
    from ..indices import index_domain
    from ..preview_index import compute_preview_index

    out_dir.mkdir(parents=True, exist_ok=True)
    locs = {e.id: e for e in loc_pool.entries}
    idxs = {e.name: e for e in idx_pool.entries}
    cmaps = {e.name: e for e in cmap_pool.entries}

    # The matrix is index × colormap; true-color plates come only from the
    # picker and have nothing to preview here.
    entries = [c for c in candidate_set.entries if c.kind != "truecolor"]

    # All (location, index) pairs referenced by any candidate.
    li_pairs: dict[tuple[str, str], list] = {}
    for cand in entries:
        li_pairs.setdefault((cand.location_id, cand.index), []).append(cand)

    # --- 1. Coarse index raster + stats per (location, index) --------------
    index_tif: dict[tuple[str, str], Path | None] = {}
    index_stats: dict[tuple[str, str], dict | None] = {}
    for (loc_id, index_name) in li_pairs:
        loc, idx = locs.get(loc_id), idxs.get(index_name)
        tif = preview_cache_dir / loc_id / f"{index_name}.tif"
        stats_json = preview_cache_dir / loc_id / f"{index_name}_stats.json"
        do_fetch = fetch and (only_id is None or only_id in (loc_id, index_name))
        if do_fetch and loc is not None and idx is not None:
            try:
                tif, sidecar, _ = compute_preview_index(
                    loc, idx, preview_cache_dir=preview_cache_dir, refresh=refresh,
                )
                index_tif[(loc_id, index_name)] = tif
                index_stats[(loc_id, index_name)] = sidecar.get("stats")
                continue
            except Exception as e:  # noqa: BLE001
                log.warning("preview index failed for %s/%s: %s", loc_id, index_name, e)
        # cache / no-fetch path
        index_tif[(loc_id, index_name)] = tif if tif.exists() else None
        index_stats[(loc_id, index_name)] = (
            json.loads(stats_json.read_text()).get("stats") if stats_json.exists() else None
        )

    # --- 2. Per-index global normalization ---------------------------------
    norm_ranges = {}
    indices_used = {idx for (_, idx) in li_pairs}
    for index_name in indices_used:
        loc_ids = [loc_id for (loc_id, idx) in li_pairs if idx == index_name]
        norm_ranges[index_name] = compute_global_norm(
            index_name, loc_ids, preview_cache_dir=preview_cache_dir,
            method=norm_method, diverging=index_pairing_type(index_name) == "diverging",
        )
    save_norm_ranges(norm_ranges, preview_cache_dir)

    # --- 3. Colorize thumbnails (cheap; reuse cached raster values) --------
    cmap_cache: dict[str, object] = {}
    values_cache: dict[tuple[str, str], object] = {}
    cards_by_li: dict[tuple[str, str], list[CandidateCard]] = {}

    for cand in entries:
        li = (cand.location_id, cand.index)
        tif = index_tif.get(li)
        gnorm = norm_ranges[cand.index]
        cmap_entry = cmaps.get(cand.colormap)
        thumb = preview_cache_dir / cand.location_id / "thumbs" / f"{cand.id}.png"
        cvd = preview_cache_dir / cand.location_id / "thumbs" / f"{cand.id}_cvd.png"
        hist = preview_cache_dir / cand.location_id / "thumbs" / f"{cand.id}_hist.svg"

        error = None
        if tif is None or not tif.exists():
            error = "no preview (run `spectralcular candidates review` without --no-fetch)"
        else:
            try:
                ck = (cand.colormap, cand.reversed)
                if ck not in cmap_cache:
                    cmap_cache[ck] = resolve_colormap(cmap_entry or cand.colormap, reversed=cand.reversed)
                cmap = cmap_cache[ck]
                norm = gnorm.to_norm_result()
                if li not in values_cache:
                    values_cache[li] = np.asarray(open_band(tif).values, dtype="float32")
                if refresh or not thumb.exists():
                    render_index_png(open_band(tif), cmap, norm, thumb)
                if refresh or not cvd.exists():
                    simulate_cvd_png(thumb, cvd, kind=cvd_kind)
                if refresh or not hist.exists():
                    render_histogram_svg(
                        values_cache[li], norm, cmap, hist,
                        display_range=index_domain(cand.index),
                    )
            except Exception as e:  # noqa: BLE001
                log.warning("colorize failed for %s: %s", cand.id, e)
                error = f"{type(e).__name__}: {e}"

        stats = index_stats.get(li)
        coverage_pct = None
        if stats is not None and gnorm.vmax > gnorm.vmin:
            span = (stats["p99"] - stats["p1"]) / (gnorm.vmax - gnorm.vmin)
            coverage_pct = max(0.0, min(1.0, span)) * 100.0

        cards_by_li.setdefault(li, []).append(CandidateCard(
            entry=cand,
            location_name=locs[cand.location_id].name if cand.location_id in locs else cand.location_id,
            index_name=cand.index,
            colormap_short=colormap_short_name(cand.colormap, reversed=cand.reversed),
            thumb_rel=_relative_path(thumb, out_dir) if thumb.exists() else None,
            cvd_rel=_relative_path(cvd, out_dir) if cvd.exists() else None,
            hist_rel=_relative_path(hist, out_dir) if hist.exists() else None,
            stats=stats,
            print_safety=cmap_entry.print_safety if cmap_entry else "unverified",
            colormap_type=cmap_entry.type if cmap_entry else "?",
            coverage_pct=coverage_pct,
            error=error,
        ))

    # --- 4. Group theme → (location, index) --------------------------------
    def _theme_rank(t: str) -> int:
        return THEME_ORDER_LOCAL.index(t) if t in THEME_ORDER_LOCAL else len(THEME_ORDER_LOCAL)

    groups: list[CandidateGroup] = []
    themes_sorted = sorted({c.theme for c in entries}, key=_theme_rank)
    for theme in themes_sorted:
        subgroups: list[CandidateSubgroup] = []
        li_in_theme = sorted(
            {(c.location_id, c.index) for c in entries if c.theme == theme},
            key=lambda li: (locs[li[0]].name if li[0] in locs else li[0], li[1]),
        )
        for li in li_in_theme:
            cards = sorted(cards_by_li.get(li, []), key=lambda c: c.colormap_short)
            if not cards:
                continue
            subgroups.append(CandidateSubgroup(
                location_name=cards[0].location_name,
                index_name=li[1],
                norm_desc=norm_ranges[li[1]].description,
                cards=cards,
            ))
        if subgroups:
            groups.append(CandidateGroup(theme=theme, subgroups=subgroups))

    template = _jinja_env().get_template("candidates.html.j2")
    html = template.render(
        groups=groups,
        counts=candidate_set.status_counts(),
        cvd_kind=cvd_kind,
        css_rel=_copy_static(out_dir),
    )
    out = out_dir / "candidates.html"
    out.write_text(html)
    log.info("Wrote %s (%d candidate cards)", out, len(entries))
    return out


# Taxonomy order for grouping (kept local to avoid importing candidates at top).
THEME_ORDER_LOCAL = [
    "vegetation", "water", "burn", "soil_geology", "urban", "snow_ice", "atmosphere", "radar",
]
