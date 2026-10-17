"""End-to-end orchestration: one config → one printed spread."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import numpy as np
import rioxarray  # noqa: F401  (registers .rio accessor)
import spyndex

from .book import SpreadAssets, render_spread_html, render_spread_pdf
from .colormaps import resolve_colormap
from .config import SpreadConfig, load_config
from .data_loader import fetch_bands, fetch_rgb_overview, open_band
from .frame_warp import warp_to_frame
from .framing import derive_frame, frame_from_spec
from .halo import HaloParams, build_page1_overlays, compose_plate_page, render_halo_png
from .indices import apply_value_scale, compute_index, index_domain, quality_mask
from .normalize import resolve_norm
from .render_assets import (
    render_colormap_oklch_charts_svg,
    colormap_oklch_facts,
    render_gradient_band_png,
    render_histogram_svg,
    render_index_png,
    render_locator_globe_svg,
    render_rgb_thumbnail_png,
    render_scene_locator_svg,
    render_truecolor_curves_svg,
    render_truecolor_histogram_svg,
    render_truecolor_linear_png,
    render_truecolor_png,
    render_truecolor_ramp_png,
)
from .truecolor import channel_stats, load_presets, preset_label, resolve_spec

log = logging.getLogger(__name__)

# The diagnostic page's true-color slot (book.css `.feature img`: half of the
# 150 mm content width less the 4 mm gap, 40 mm tall, object-fit: cover).
FEATURE_SLOT_IN = (73.0 / 25.4, 40.0 / 25.4)


def _book_title(root: Path) -> str:
    """The book's title for page footers (``config/book.yaml``)."""
    from .book_meta import load_book_meta
    return load_book_meta(root / "config" / "book.yaml").title


def thumbnail_resolution_m(bbox: tuple[float, float, float, float], *, native_m: float = 10.0,
                           ppi: int = 300) -> int:
    """Ground resolution for the true-color thumbnail so a cover-fit into the
    feature slot lands at ``ppi`` or better (never finer than the sensor)."""
    lon_min, lat_min, lon_max, lat_max = bbox
    w_m = (lon_max - lon_min) * 111_320 * math.cos(math.radians((lat_min + lat_max) / 2))
    h_m = (lat_max - lat_min) * 110_540
    need = min(w_m / (FEATURE_SLOT_IN[0] * ppi), h_m / (FEATURE_SLOT_IN[1] * ppi))
    return max(int(math.ceil(native_m)), int(need))


def _project_root() -> Path:
    from .paths import find_project_root
    return find_project_root()


def run(config_path: str | Path, *, project_root: Path | None = None) -> Path:
    """Run the full pipeline for a single spread from a YAML config file."""
    cfg: SpreadConfig = load_config(config_path)
    return run_config(cfg, project_root or _project_root())


def run_config(
    cfg: SpreadConfig,
    root: Path,
    *,
    band_cache_key: str | None = None,
    index_cache_key: str | None = None,
    review_status: str = "draft",
) -> Path:
    """Run the full pipeline for an in-memory config; return the primary output path.

    ``band_cache_key`` / ``index_cache_key`` override the per-asset cache keys.
    A batch over many colormap variants of one ``(location, index)`` passes the
    location id for both, so the band fetch + spyndex compute happen once and
    every colormap variant reuses them (default: keyed on ``cfg.asset_id``).
    ``review_status`` is recorded verbatim in the metadata sidecar.
    """
    log.info("=== Building spread %s ===", cfg.asset_id)
    if cfg.kind == "truecolor":
        return _run_truecolor(cfg, root, band_cache_key=band_cache_key, review_status=review_status)

    raw_dir = root / "data" / "raw"
    indices_dir = root / "data" / "indices"
    assets_dir = root / "assets"
    output_dir = root / "output" / "spreads"
    metadata_dir = root / "output" / "metadata"
    osm_cache_dir = root / "data" / "cache" / "osm-tiles"

    # 1. Mosaic the bands (and SCL) covering the user's AOI bbox. STAC is
    #    queried for every scene that intersects the bbox; the best (lowest
    #    cloud) per MGRS tile contributes to the mosaic. The on-page image
    #    frame is derived from the resulting data extent — that way the
    #    frame is always covered by imagery, even when the AOI spans tile
    #    boundaries.
    platform = cfg.imagery.platform
    mask_asset = platform.mask.asset if platform.mask is not None else None
    band_assets = sorted(set(cfg.index.bands.values()) | ({mask_asset} if mask_asset else set()))
    band_paths, scene_set, stac_items = fetch_bands(
        band_cache_key or cfg.asset_id, cfg.location.bbox, cfg.imagery, band_assets, raw_dir,
        location_label=cfg.location.name,
    )
    primary = scene_set.primary

    # 2. Compute index.
    band_paths_for_index = {k: v for k, v in band_paths.items() if k != mask_asset}
    index_tif, index_sidecar = compute_index(
        index_cache_key or cfg.asset_id,
        cfg.index,
        band_paths_for_index,
        indices_dir,
        mask_path=band_paths.get(mask_asset) if mask_asset and cfg.mask_enabled else None,
        platform=platform,
    )

    # 3. Open computed index and bring it onto the page.
    #    - Explicit frame (plate picker): warp onto the rotated page grid so
    #      the plate is exactly what the user framed, rotation included.
    #    - Otherwise: derive a north-up frame from the *actual* data extent
    #      and crop, so the PNG matches the page aspect with no nodata.
    index_da = open_band(index_tif)
    from rasterio.warp import transform_bounds
    src_crs = index_da.rio.crs
    frame_corners: list[tuple[float, float]] | None = None
    if cfg.frame is not None:
        frame = frame_from_spec(cfg.frame)
        frame_corners = cfg.frame.corners_lonlat()
        warped = warp_to_frame(index_da, cfg.frame)
        index_values = warped.values
        log.info(
            "Frame: explicit %.1f × %.1f km @ %.1f° → page grid %s (envelope %s)",
            cfg.frame.width_m / 1000, cfg.frame.height_m / 1000, cfg.frame.rotation_deg,
            index_values.shape, frame.bbox,
        )
    else:
        data_b = index_da.rio.bounds()
        data_lonlat = transform_bounds("EPSG:4326" if src_crs is None else src_crs, "EPSG:4326",
                                       *data_b, densify_pts=21) if src_crs else data_b
        frame = derive_frame(
            data_lonlat,
            page_size=cfg.layout.page_size,
            orientation=cfg.layout.orientation,
        )
        log.info(
            "Frame: AOI %s → data %s → on-page %s (rotation=%.1f°)",
            cfg.location.bbox, data_lonlat, frame.bbox, frame.rotation_deg,
        )
        # Clip to frame in the source CRS so the raster grid is preserved.
        fl, fb, fr, ft = transform_bounds("EPSG:4326", src_crs, *frame.bbox, densify_pts=21)
        index_da = index_da.rio.clip_box(minx=fl, miny=fb, maxx=fr, maxy=ft)
        index_values = np.asarray(index_da.values, dtype="float32")
    norm = resolve_norm(index_values, cfg.colormap.normalization)
    cmap = resolve_colormap(cfg.colormap)
    log.info("Normalization → vmin=%.3f vmax=%.3f (%s)", norm.vmin, norm.vmax, norm.description)

    # 4. Render image + chart assets.
    image_png = assets_dir / "images" / f"{cfg.asset_id}.png"
    halo_png = assets_dir / "images" / f"{cfg.asset_id}_halo.png"
    band_png = assets_dir / "images" / f"{cfg.asset_id}_band.png"
    rgb_thumb_png = assets_dir / "images" / f"{cfg.asset_id}_rgb_thumb.png"
    hist_svg = assets_dir / "charts" / f"{cfg.asset_id}_histogram.svg"
    loc_svg = assets_dir / "charts" / f"{cfg.asset_id}_locator.svg"
    scene_loc_svg = assets_dir / "charts" / f"{cfg.asset_id}_scene_locator.svg"
    oklch_svg = assets_dir / "charts" / f"{cfg.asset_id}_oklch.svg"

    render_index_png(index_values, cmap, norm, image_png)
    # book.css .gradient-band is 8.5 in × 12 mm: render it at 300 ppi.
    render_gradient_band_png(cmap, band_png, width_px=2550, height_px=142, angle_deg=22.5, noise=0.018)
    render_histogram_svg(
        index_values, norm, cmap, hist_svg,
        display_range=index_domain(cfg.index.name),
    )
    render_locator_globe_svg(frame.bbox, loc_svg)
    render_scene_locator_svg(
        frame.bbox, scene_set.geometries, scene_loc_svg,
        osm_cache_dir=osm_cache_dir,
        aoi_polygon=frame_corners,
    )
    render_colormap_oklch_charts_svg(cmap, oklch_svg)

    # RGB thumbnail — mosaic a coarse-resolution load via odc-stac.
    # Optional: skipped if no scene has the RGB assets.
    rgb_paths = fetch_rgb_overview(
        cfg.asset_id, frame.bbox, stac_items, scene_set, cfg.imagery, raw_dir,
        resolution=thumbnail_resolution_m(frame.bbox, native_m=platform.native_res_m),
    )
    rgb_thumbnail_png: Path | None = None
    if rgb_paths is not None:
        rgb_arrays = {k: np.asarray(open_band(p).values) for k, p in rgb_paths.items()}
        render_rgb_thumbnail_png(rgb_arrays, rgb_thumb_png)
        rgb_thumbnail_png = rgb_thumb_png

    # Page-1 halo (separate stacked PNG).
    overlays = build_page1_overlays(
        index_short=cfg.index.name,
        colormap_short_name=cmap.short_name,
        location_name=cfg.location.name,
        caption=cfg.layout.caption,
        plate_number=cfg.layout.plate_number,
    )
    render_halo_png(
        index_image_path=image_png,
        overlays=overlays,
        page_size=cfg.layout.page_size,
        orientation=cfg.layout.orientation,
        dpi=cfg.layout.dpi,
        params=HaloParams(),
        out_halo_path=halo_png,
    )
    plate_page_jpg = compose_plate_page(
        index_image_path=image_png,
        halo_path=halo_png,
        out_path=assets_dir / "images" / f"{cfg.asset_id}_page.jpg",
        page_size=cfg.layout.page_size,
        orientation=cfg.layout.orientation,
        dpi=cfg.layout.dpi,
    )

    # 5. Render outputs (PDF and/or HTML) per layout.outputs.
    spec = spyndex.indices[cfg.index.name]
    spread_pdf = output_dir / f"{cfg.asset_id}.pdf"
    spread_html = output_dir / f"{cfg.asset_id}.html"

    # Date range across the mosaic; collapsed if all scenes share a date.
    date_min = primary.datetime if scene_set.n == 1 else scene_set.date_min
    date_max = primary.datetime if scene_set.n == 1 else scene_set.date_max
    cloud_max_pct = primary.cloud_cover if scene_set.n == 1 else scene_set.cloud_cover_max

    render_kwargs: dict = dict(
        book_title=_book_title(root),
        asset_id=cfg.asset_id,
        location_name=cfg.location.name,
        bbox=frame.bbox,
        source=cfg.imagery.source,
        platform=primary.platform,
        scene_id=primary.item_id,
        acquired_iso=primary.datetime,
        cloud_cover=primary.cloud_cover,
        crs=scene_set.target_crs,
        scene_count=scene_set.n,
        scene_ids=[s.item_id for s in scene_set.scenes],
        date_range_min=date_min,
        date_range_max=date_max,
        cloud_max=cloud_max_pct,
        index_short=cfg.index.name,
        index_long_name=spec.long_name,
        index_formula=spec.formula,
        index_domain=index_domain(cfg.index.name),
        application_domain=spec.application_domain,
        bands=dict(cfg.index.bands),
        platform_id=platform.id,
        colormap_name=cmap.name,
        colormap_short_name=cmap.short_name,
        normalization_desc=norm.description,
        vmin=norm.vmin,
        vmax=norm.vmax,
        stats=index_sidecar["stats"],
        caption=cfg.layout.caption,
        plate_number=cfg.layout.plate_number,
        interpretive_note=cfg.layout.interpretive_note,
        colormap_facts=colormap_oklch_facts(cmap),
        frame_rotation_deg=cfg.frame.rotation_deg if cfg.frame is not None else 0.0,
        frame_extent_km=(f"{cfg.frame.width_m / 1000:.1f}×{cfg.frame.height_m / 1000:.1f} km"
                         if cfg.frame is not None else None),
        assets=SpreadAssets(
            image_png=image_png,
            halo_png=halo_png,
            plate_page=plate_page_jpg,
            gradient_band_png=band_png,
            histogram_svg=hist_svg,
            locator_svg=loc_svg,
            rgb_thumbnail_png=rgb_thumbnail_png,
            scene_locator_svg=scene_loc_svg,
            oklch_chart_svg=oklch_svg,
        ),
    )

    primary_output = spread_pdf
    if "pdf" in cfg.layout.outputs:
        render_spread_pdf(out_pdf=spread_pdf, **render_kwargs)
    if "html" in cfg.layout.outputs:
        render_spread_html(out_html=spread_html, **render_kwargs)
        if "pdf" not in cfg.layout.outputs:
            primary_output = spread_html

    # 6. Metadata sidecar.
    metadata_dir.mkdir(parents=True, exist_ok=True)
    sidecar = {
        "asset_id": cfg.asset_id,
        "location_name": cfg.location.name,
        "aoi_bbox": list(cfg.location.bbox),
        "frame_bbox": list(frame.bbox),
        "frame_rotation_deg": frame.rotation_deg,
        "frame_corners_lonlat": [list(c) for c in frame_corners] if frame_corners else None,
        "frame_spec": cfg.frame.model_dump(mode="json") if cfg.frame is not None else None,
        "pinned_scene_ids": list(cfg.imagery.item_ids) if cfg.imagery.item_ids else None,
        "bbox": list(frame.bbox),  # legacy alias — what's actually displayed
        "source": cfg.imagery.source,
        "date_range": cfg.imagery.date_range,
        "scene_id": primary.item_id,
        "scene_datetime": primary.datetime,
        "scene_cloud_cover": primary.cloud_cover,
        "scene_platform": primary.platform,
        "scenes": [s.to_dict() for s in scene_set.scenes],
        "scene_count": scene_set.n,
        "scene_date_range": [scene_set.date_min, scene_set.date_max],
        "scene_cloud_range": [scene_set.cloud_cover_min, scene_set.cloud_cover_max],
        "crs": scene_set.target_crs,
        "mask_enabled": cfg.mask_enabled,
        "index": cfg.index.name,
        "index_long_name": spec.long_name,
        "formula": spec.formula,
        "index_domain": list(index_domain(cfg.index.name)) if index_domain(cfg.index.name) else None,
        "bands": cfg.index.bands,
        "colormap": cmap.name,
        "colormap_short_name": cmap.short_name,
        "colormap_reversed": cmap.reversed,
        "colormap_source": cmap.source,
        "normalization": norm.description,
        "value_range": [norm.vmin, norm.vmax],
        "stats": index_sidecar["stats"],
        "image_asset": str(primary_output),
        "image_png": str(image_png),
        "halo_png": str(halo_png),
        "rgb_thumb_png": str(rgb_thumb_png) if rgb_thumbnail_png else None,
        "scene_locator_svg": str(scene_loc_svg),
        "oklch_chart_svg": str(oklch_svg),
        "index_raster": str(index_tif),
        "outputs": list(cfg.layout.outputs),
        "template": cfg.layout.template,
        "annotation_mode": cfg.layout.annotations,
        "review_status": review_status,
    }
    sidecar_path = metadata_dir / f"{cfg.asset_id}.json"
    sidecar_path.write_text(json.dumps(sidecar, indent=2))
    log.info("Wrote metadata sidecar → %s", sidecar_path)

    return primary_output


# ---------------------------------------------------------------------------
# True-color plates
# ---------------------------------------------------------------------------

def _truecolor_reflectance(cfg: SpreadConfig, band_paths: dict[str, Path]) -> list:
    """The platform's red, green, blue bands as reflectance DataArrays on one
    grid; nodata → NaN always, quality-mask classes → NaN when the plate asks."""
    platform = cfg.imagery.platform
    chans = [apply_value_scale(open_band(band_paths[a]), a, platform) for a in platform.rgb_assets]
    mask_asset = platform.mask.asset if platform.mask is not None else None
    if cfg.mask_enabled and mask_asset and mask_asset in band_paths:
        keep = quality_mask(open_band(band_paths[mask_asset]).rio.reproject_match(chans[0]), platform)
        chans = [c.where(keep) for c in chans]
    return chans


def _run_truecolor(
    cfg: SpreadConfig,
    root: Path,
    *,
    band_cache_key: str | None = None,
    review_status: str = "draft",
) -> Path:
    """One true-color plate: red/green/blue reflectance onto the page grid,
    through the tone pipeline (:mod:`truecolor`), then the shared halo, page-2
    and sidecar steps. The spec arrives resolved from the picker; an auto
    stretch (hand-written configs) resolves on the framed full-resolution pixels."""
    raw_dir = root / "data" / "raw"
    assets_dir = root / "assets"
    output_dir = root / "output" / "spreads"
    metadata_dir = root / "output" / "metadata"
    osm_cache_dir = root / "data" / "cache" / "osm-tiles"

    platform = cfg.imagery.platform
    rgb_assets = platform.rgb_assets
    mask_asset = platform.mask.asset if platform.mask is not None else None

    # 1. Bands at native resolution (mask too, so nodata/masking match the picker).
    band_assets = sorted(set(rgb_assets) | ({mask_asset} if mask_asset else set()))
    band_paths, scene_set, _items = fetch_bands(
        band_cache_key or cfg.asset_id, cfg.location.bbox, cfg.imagery, band_assets, raw_dir,
        location_label=cfg.location.name,
    )
    primary = scene_set.primary
    chans = _truecolor_reflectance(cfg, band_paths)

    # 2. Onto the page: warp each channel onto the picker's page grid, or crop
    #    to a north-up frame derived from the data extent.
    from rasterio.warp import transform_bounds
    src_crs = chans[0].rio.crs
    frame_corners: list[tuple[float, float]] | None = None
    if cfg.frame is not None:
        frame = frame_from_spec(cfg.frame)
        frame_corners = cfg.frame.corners_lonlat()
        r, g, b = (warp_to_frame(c, cfg.frame).values for c in chans)
    else:
        data_b = chans[0].rio.bounds()
        data_lonlat = transform_bounds(src_crs, "EPSG:4326", *data_b, densify_pts=21) if src_crs else data_b
        frame = derive_frame(data_lonlat, page_size=cfg.layout.page_size, orientation=cfg.layout.orientation)
        fl, fb, fr, ft = transform_bounds("EPSG:4326", src_crs, *frame.bbox, densify_pts=21)
        r, g, b = (np.asarray(c.rio.clip_box(minx=fl, miny=fb, maxx=fr, maxy=ft).values, dtype="float32")
                   for c in chans)

    # 3. Tone spec (resolves only an auto stretch), stats for page 2.
    spec = resolve_spec((r, g, b), cfg.truecolor)
    st = spec.stretch
    log.info("True color: %s stretch %s lo=%s hi=%s γ=%.2f contrast=%.2f@%.2f sat=%.2f",
             st.mode, st.source, st.lo, st.hi, spec.gamma, spec.contrast, spec.midpoint, spec.saturation)
    stats = channel_stats(r, g, b)
    presets_yaml = root / "config" / "truecolor.yaml"
    label = preset_label(spec.preset, load_presets(presets_yaml) if presets_yaml.exists() else None)

    # 4. Assets.
    image_png = assets_dir / "images" / f"{cfg.asset_id}.png"
    halo_png = assets_dir / "images" / f"{cfg.asset_id}_halo.png"
    ramp_png = assets_dir / "images" / f"{cfg.asset_id}_band.png"
    linear_png = assets_dir / "images" / f"{cfg.asset_id}_linear.png"
    hist_svg = assets_dir / "charts" / f"{cfg.asset_id}_histogram.svg"
    loc_svg = assets_dir / "charts" / f"{cfg.asset_id}_locator.svg"
    scene_loc_svg = assets_dir / "charts" / f"{cfg.asset_id}_scene_locator.svg"
    curves_svg = assets_dir / "charts" / f"{cfg.asset_id}_tone_curves.svg"

    render_truecolor_png(r, g, b, spec, image_png)
    render_truecolor_linear_png(r, g, b, linear_png)
    render_truecolor_ramp_png(spec, ramp_png, width_px=2550, height_px=142)  # 8.5 in × 12 mm @ 300 ppi
    render_truecolor_curves_svg(spec, curves_svg)
    render_truecolor_histogram_svg(r, g, b, spec, hist_svg)
    render_locator_globe_svg(frame.bbox, loc_svg)
    render_scene_locator_svg(
        frame.bbox, scene_set.geometries, scene_loc_svg,
        osm_cache_dir=osm_cache_dir,
        aoi_polygon=frame_corners,
    )

    overlays = build_page1_overlays(
        index_short="True color",
        colormap_short_name=label,
        location_name=cfg.location.name,
        caption=cfg.layout.caption,
        plate_number=cfg.layout.plate_number,
    )
    render_halo_png(
        index_image_path=image_png,
        overlays=overlays,
        page_size=cfg.layout.page_size,
        orientation=cfg.layout.orientation,
        dpi=cfg.layout.dpi,
        params=HaloParams(),
        out_halo_path=halo_png,
    )
    plate_page_jpg = compose_plate_page(
        index_image_path=image_png,
        halo_path=halo_png,
        out_path=assets_dir / "images" / f"{cfg.asset_id}_page.jpg",
        page_size=cfg.layout.page_size,
        orientation=cfg.layout.orientation,
        dpi=cfg.layout.dpi,
    )

    # 5. Outputs.
    spread_pdf = output_dir / f"{cfg.asset_id}.pdf"
    spread_html = output_dir / f"{cfg.asset_id}.html"
    date_min = primary.datetime if scene_set.n == 1 else scene_set.date_min
    date_max = primary.datetime if scene_set.n == 1 else scene_set.date_max
    cloud_max_pct = primary.cloud_cover if scene_set.n == 1 else scene_set.cloud_cover_max
    bands = dict(zip(("R", "G", "B"), rgb_assets))

    render_kwargs: dict = dict(
        book_title=_book_title(root),
        kind="truecolor",
        asset_id=cfg.asset_id,
        location_name=cfg.location.name,
        bbox=frame.bbox,
        source=cfg.imagery.source,
        platform=primary.platform,
        scene_id=primary.item_id,
        acquired_iso=primary.datetime,
        cloud_cover=primary.cloud_cover,
        crs=scene_set.target_crs,
        scene_count=scene_set.n,
        scene_ids=[s.item_id for s in scene_set.scenes],
        date_range_min=date_min,
        date_range_max=date_max,
        cloud_max=cloud_max_pct,
        bands=bands,
        platform_id=platform.id,
        caption=cfg.layout.caption,
        plate_number=cfg.layout.plate_number,
        interpretive_note=cfg.layout.interpretive_note,
        frame_rotation_deg=cfg.frame.rotation_deg if cfg.frame is not None else 0.0,
        frame_extent_km=(f"{cfg.frame.width_m / 1000:.1f}×{cfg.frame.height_m / 1000:.1f} km"
                         if cfg.frame is not None else None),
        truecolor_spec=spec,
        truecolor_stats=stats,
        truecolor_preset_label=label,
        assets=SpreadAssets(
            image_png=image_png,
            halo_png=halo_png,
            plate_page=plate_page_jpg,
            gradient_band_png=ramp_png,
            histogram_svg=hist_svg,
            locator_svg=loc_svg,
            rgb_thumbnail_png=None,
            scene_locator_svg=scene_loc_svg,
            oklch_chart_svg=curves_svg,
            linear_thumbnail_png=linear_png,
        ),
    )
    primary_output = spread_pdf
    if "pdf" in cfg.layout.outputs:
        render_spread_pdf(out_pdf=spread_pdf, **render_kwargs)
    if "html" in cfg.layout.outputs:
        render_spread_html(out_html=spread_html, **render_kwargs)
        if "pdf" not in cfg.layout.outputs:
            primary_output = spread_html

    # 6. Metadata sidecar — the resolved spec makes the plate reproducible.
    metadata_dir.mkdir(parents=True, exist_ok=True)
    sidecar = {
        "asset_id": cfg.asset_id,
        "kind": "truecolor",
        "location_name": cfg.location.name,
        "aoi_bbox": list(cfg.location.bbox),
        "frame_bbox": list(frame.bbox),
        "frame_rotation_deg": frame.rotation_deg,
        "frame_corners_lonlat": [list(c) for c in frame_corners] if frame_corners else None,
        "frame_spec": cfg.frame.model_dump(mode="json") if cfg.frame is not None else None,
        "pinned_scene_ids": list(cfg.imagery.item_ids) if cfg.imagery.item_ids else None,
        "bbox": list(frame.bbox),
        "source": cfg.imagery.source,
        "date_range": cfg.imagery.date_range,
        "scene_id": primary.item_id,
        "scene_datetime": primary.datetime,
        "scene_cloud_cover": primary.cloud_cover,
        "scene_platform": primary.platform,
        "scenes": [s.to_dict() for s in scene_set.scenes],
        "scene_count": scene_set.n,
        "scene_date_range": [scene_set.date_min, scene_set.date_max],
        "scene_cloud_range": [scene_set.cloud_cover_min, scene_set.cloud_cover_max],
        "crs": scene_set.target_crs,
        "bands": bands,
        "mask_enabled": cfg.mask_enabled,
        "truecolor": spec.model_dump(mode="json"),
        "truecolor_preset_label": label,
        "stats": stats,
        "image_asset": str(primary_output),
        "image_png": str(image_png),
        "halo_png": str(halo_png),
        "linear_png": str(linear_png),
        "scene_locator_svg": str(scene_loc_svg),
        "tone_curves_svg": str(curves_svg),
        "outputs": list(cfg.layout.outputs),
        "template": cfg.layout.template,
        "annotation_mode": cfg.layout.annotations,
        "review_status": review_status,
    }
    sidecar_path = metadata_dir / f"{cfg.asset_id}.json"
    sidecar_path.write_text(json.dumps(sidecar, indent=2))
    log.info("Wrote metadata sidecar → %s", sidecar_path)
    return primary_output
