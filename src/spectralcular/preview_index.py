"""Coarse, location-keyed index rasters for the Stage-4 matrix preview.

The matrix contact sheet shows Location × Index × Colormap thumbnails. The
expensive work — STAC fetch + spyndex compute — depends only on
``(location, index)``; the colormap axis is a cheap re-colorize of the same
index raster. So we compute the coarse index **once per (location, index)**
and cache it under ``data/preview_cache/<location_id>/``.

Scene selection reuses the Stage-3 review logic (multi-year, best single
full-coverage scene) so the preview plate matches the location review sheet's
hero image. This module lives outside ``data_loader`` and ``review.preview``
to bridge them without creating an import cycle.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import Imagery, Index
from .data_loader import (
    SceneSet,
    _odc_load_to_tifs,
    _scene_record_from_item,
    search_scenes_for_bbox,
)
from .indices import compute_index
from .platforms import PLATFORMS, Platform, get_platform
from .pool import IndexPoolEntry, LocationPoolEntry
from .review.preview import (
    REVIEW_RES_M,
    _expand_windows,
    _search_scenes_covering_bbox_multiyear,
)

log = logging.getLogger(__name__)

PREVIEW_RES_M = REVIEW_RES_M  # 80 m — same as the location review sheet


def fetch_bands_coarse(
    location_id: str,
    bbox: tuple[float, float, float, float],
    season_windows: list[str],
    cloud_cover_max: float,
    band_assets: list[str],
    preview_raw_dir: Path,
    *,
    resolution: int = PREVIEW_RES_M,
    refresh: bool = False,
    platform: Platform = PLATFORMS["sentinel-2-l2a"],
) -> tuple[dict[str, Path], SceneSet]:
    """Coarse band mosaic for one location, cached under ``preview_raw_dir``.

    Mirrors :func:`data_loader.fetch_bands` but (a) keys the cache by location
    rather than asset_id, (b) loads at a coarse ``resolution``, and (c) for
    Sentinel-2 reuses the review's multi-year best-coverage scene picker so the
    preview shows the prettiest clean pass — not the closest calendar match.
    Other platforms use the plain best-per-tile search over the expanded
    season windows (the review picker's RGB coverage test is Sentinel-2 only).
    """
    preview_raw_dir.mkdir(parents=True, exist_ok=True)
    scenes_path = preview_raw_dir / "scenes.json"
    expected = {b: preview_raw_dir / f"{b}.tif" for b in band_assets}

    if not refresh and scenes_path.exists() and all(p.exists() for p in expected.values()):
        log.info("Using cached coarse bands for %s at %s", location_id, preview_raw_dir)
        return expected, SceneSet.from_dict(json.loads(scenes_path.read_text()))

    expanded = _expand_windows(season_windows, year_radius=2)
    if platform.id == "sentinel-2-l2a":
        items = _search_scenes_covering_bbox_multiyear(
            tuple(bbox), expanded,
            cloud_cover_max=cloud_cover_max,
            location_label=location_id,
        )
    else:
        items = []
        for w in expanded:
            try:
                items = search_scenes_for_bbox(
                    tuple(bbox),
                    Imagery(source=platform.id, date_range=w,
                            cloud_cover_max=cloud_cover_max if platform.cloud_property else None),
                    location_label=f"{location_id} {w}",
                )
            except RuntimeError:
                continue
            if items:
                break
    if not items:
        raise RuntimeError(
            f"No coverage-adequate {platform.collection} scenes for {location_id} in any of {len(expanded)} windows"
        )

    merged_paths, target_crs = _odc_load_to_tifs(
        items, tuple(bbox), band_assets, preview_raw_dir, resolution=resolution,
    )
    scene_records = [_scene_record_from_item(it, platform.collection, target_crs, platform) for it in items]
    scene_set = SceneSet(scenes=scene_records, target_crs=target_crs, bbox_lonlat=list(bbox))
    scenes_path.write_text(json.dumps(scene_set.to_dict(), indent=2))
    return merged_paths, scene_set


def compute_preview_index(
    location: LocationPoolEntry,
    index_entry: IndexPoolEntry,
    *,
    preview_cache_dir: Path,
    refresh: bool = False,
) -> tuple[Path, dict, SceneSet]:
    """Coarse index raster + stats for one ``(location, index)``, cached.

    Returns ``(index_tif, stats_sidecar, scene_set)``. The index raster lands at
    ``preview_cache/<location_id>/<INDEX>.tif`` and is shared across every
    colormap variant of this location+index.
    """
    platform = get_platform(index_entry.platform)
    loc_dir = preview_cache_dir / location.id
    # Sentinel-2 keeps the historical cache layout; other platforms get their own.
    raw_dir = loc_dir / "raw" if platform.id == "sentinel-2-l2a" else loc_dir / f"raw_{platform.id}"
    mask_asset = platform.mask.asset if platform.mask is not None else None
    band_assets = sorted(set(index_entry.bands.values()) | ({mask_asset} if mask_asset else set()))

    band_paths, scene_set = fetch_bands_coarse(
        location.id, tuple(location.bbox), location.season_windows,
        location.cloud_cover_max, band_assets, raw_dir, refresh=refresh, platform=platform,
    )
    band_paths_for_index = {k: v for k, v in band_paths.items() if k != mask_asset}
    index_tif, sidecar = compute_index(
        location.id,
        Index(name=index_entry.name, bands=index_entry.bands),
        band_paths_for_index,
        loc_dir,
        mask_path=band_paths.get(mask_asset) if mask_asset else None,
        out_stem=index_entry.name,
        platform=platform,
    )
    return index_tif, sidecar, scene_set
