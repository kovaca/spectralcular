"""Imagery data loader: STAC search → odc-stac mosaic → per-band rasters.

Works for every platform in :mod:`platforms` (Sentinel-2 on Earth Search,
Landsat and Sentinel-1 on Planetary Computer). odc-stac handles the
geospatial stitching for us: ``groupby="solar_day"`` collapses scenes
acquired on the same date into a single time-step, auto-aligning +
reprojecting + mosaicking adjacent tiles. We pick the best (lowest-cloud)
item per acquisition tile before handing them to odc-stac so each tile
contributes its cleanest-day data.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import rioxarray  # noqa: F401  (registers .rio accessor)
import xarray as xr
from odc.stac import load as odc_load
from pystac_client import Client

from .config import Imagery, Location  # noqa: F401  (Location re-exported for callers)
from .platforms import PLATFORMS, Platform

# Sentinel-2 native resolution (kept for callers that predate platforms.py).
S2_NATIVE_RES_M = int(PLATFORMS["sentinel-2-l2a"].native_res_m)
THUMB_RES_M = 80            # ≈ COG overview level 3 — plenty for a 30 mm thumbnail
S2_THUMB_RES_M = THUMB_RES_M

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scene metadata records
# ---------------------------------------------------------------------------

@dataclass
class SceneRecord:
    """Metadata for one STAC scene contributing to the mosaic."""

    item_id: str
    collection: str
    datetime: str
    cloud_cover: float
    crs: str
    geometry: dict | None
    platform: str | None
    grid_code: str | None
    asset_hrefs: dict[str, str]

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "collection": self.collection,
            "datetime": self.datetime,
            "cloud_cover": self.cloud_cover,
            "crs": self.crs,
            "geometry": self.geometry,
            "platform": self.platform,
            "grid_code": self.grid_code,
            "asset_hrefs": self.asset_hrefs,
        }


@dataclass
class SceneSet:
    """A set of scenes mosaicked together to cover the AOI.

    Sorted by cloud cover ascending — primary scene is first. The mosaic
    target CRS is recorded so downstream rasters know which projection
    they're in.
    """
    scenes: list[SceneRecord]
    target_crs: str
    bbox_lonlat: list[float]

    def to_dict(self) -> dict:
        return {
            "scenes": [s.to_dict() for s in self.scenes],
            "target_crs": self.target_crs,
            "bbox_lonlat": self.bbox_lonlat,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SceneSet":
        return cls(
            scenes=[SceneRecord(**s) for s in d["scenes"]],
            target_crs=d["target_crs"],
            bbox_lonlat=d["bbox_lonlat"],
        )

    @property
    def primary(self) -> SceneRecord:
        return self.scenes[0]

    @property
    def n(self) -> int:
        return len(self.scenes)

    @property
    def cloud_cover_min(self) -> float:
        return min(s.cloud_cover for s in self.scenes)

    @property
    def cloud_cover_max(self) -> float:
        return max(s.cloud_cover for s in self.scenes)

    @property
    def date_min(self) -> str:
        return min(s.datetime for s in self.scenes)

    @property
    def date_max(self) -> str:
        return max(s.datetime for s in self.scenes)

    @property
    def geometries(self) -> list[dict]:
        return [s.geometry for s in self.scenes if s.geometry]


# Backwards-compat alias — older imports referred to ``SceneMetadata``.
SceneMetadata = SceneRecord


# ---------------------------------------------------------------------------
# STAC search: best scene per MGRS tile
# ---------------------------------------------------------------------------

def stac_client(platform: Platform) -> Client:
    """STAC API client for ``platform``; Planetary Computer assets are signed in place."""
    modifier = None
    if platform.signing == "planetary-computer":
        import planetary_computer

        modifier = planetary_computer.sign_inplace
    return Client.open(platform.stac_api, modifier=modifier)


def _tile_key(item, platform: Platform | None = None) -> str:
    """Stable per-acquisition-tile identifier.

    Sentinel-2: MGRS grid code; Landsat: WRS path/row; platforms without a
    grid (Sentinel-1) fall back to the item id so no two items collapse.
    """
    p = item.properties
    grid = platform.grid_property if platform is not None else "grid:code"
    if isinstance(grid, tuple):
        vals = [p.get(k) for k in grid]
        if all(v is not None for v in vals):
            return "/".join(str(v) for v in vals)
    elif isinstance(grid, str):
        v = p.get(grid) or p.get("s2:mgrs_tile")
        if v:
            return str(v)
        if platform is None or platform.id == "sentinel-2-l2a":
            return "_".join(item.id.split("_")[:2])
    return item.id


def _cloud_of(item, platform: Platform) -> float:
    if platform.cloud_property is None:
        return 0.0
    return float(item.properties.get(platform.cloud_property, 100.0))


def _unsigned(href: str) -> str:
    """Drop a SAS token / query string so cached scene records don't expire."""
    return href.split("?", 1)[0]


def search_scenes_for_bbox(
    bbox: tuple[float, float, float, float],
    imagery: Imagery,
    *,
    location_label: str = "",
    max_scenes: int = 12,
) -> list:
    """Return the best (lowest-cloud) STAC item per acquisition tile intersecting ``bbox``.

    Sorted by cloud cover ascending (primary scene first). Platforms without a
    cloud property (radar) are sorted newest first instead.
    """
    platform = imagery.platform
    client = stac_client(platform)
    collection = platform.collection
    cloud_key = lambda it: _cloud_of(it, platform)  # noqa: E731

    if imagery.item_ids:
        # Pinned scenes (plate picker): fetch exactly these ids, no filters.
        search = client.search(collections=[collection], ids=list(imagery.item_ids))
        candidates = list(search.items())
        found = {it.id for it in candidates}
        missing = [i for i in imagery.item_ids if i not in found]
        if missing:
            log.warning("Pinned scene id(s) not found in %s: %s", collection, missing)
        if not candidates:
            raise RuntimeError(f"None of the pinned scenes {imagery.item_ids} exist in {collection}")
        log.info("Using %d pinned scene(s): %s", len(candidates), [it.id for it in candidates])
        return sorted(candidates, key=cloud_key)[:max_scenes]

    query: dict = dict(platform.stac_query)
    if platform.cloud_property is not None and imagery.cloud_cover_max is not None:
        query[platform.cloud_property] = {"lt": imagery.cloud_cover_max}
    if platform.orbit_property is not None:
        query[platform.orbit_property] = {"eq": imagery.orbit_state or "ascending"}
    sortby = (
        [{"field": f"properties.{platform.cloud_property}", "direction": "asc"}]
        if platform.cloud_property is not None
        else [{"field": "properties.datetime", "direction": "desc"}]
    )
    search = client.search(
        collections=[collection],
        bbox=list(bbox),
        datetime=imagery.date_range,
        query=query or None,
        sortby=sortby,
        limit=200,
    )
    candidates = list(search.items())
    if not candidates:
        raise RuntimeError(
            f"No {collection} scenes found for {location_label or 'bbox'} "
            f"in {imagery.date_range}"
            + (f" with cloud_cover < {imagery.cloud_cover_max}%" if query.get(platform.cloud_property) else "")
        )

    # Group by tile; first occurrence wins because the search is already
    # sorted by cloud cover ascending (or newest first).
    best_per_tile: dict[str, object] = {}
    for it in candidates:
        key = _tile_key(it, platform)
        if key not in best_per_tile:
            best_per_tile[key] = it

    selected = sorted(best_per_tile.values(), key=cloud_key)[:max_scenes]
    log.info(
        "Selected %d scene(s) covering %d tile(s) (from %d candidates):",
        len(selected), len(best_per_tile), len(candidates),
    )
    for it in selected:
        log.info("  %s  tile=%s cloud=%.2f%%  %s", it.id, _tile_key(it, platform), cloud_key(it), it.datetime)
    return selected


def _scene_record_from_item(it, collection: str, target_crs: str, platform: Platform | None = None) -> SceneRecord:
    if platform is None:
        platform = next((p for p in PLATFORMS.values() if p.collection == collection), PLATFORMS["sentinel-2-l2a"])
    return SceneRecord(
        item_id=it.id,
        collection=collection,
        datetime=it.datetime.isoformat() if it.datetime else "",
        cloud_cover=_cloud_of(it, platform),
        crs=target_crs,
        geometry=getattr(it, "geometry", None),
        platform=(it.properties.get("platform")
                  or it.properties.get("constellation")),
        grid_code=_tile_key(it, platform),
        asset_hrefs={k: _unsigned(a.href) for k, a in it.assets.items()},
    )


# ---------------------------------------------------------------------------
# odc-stac mosaic
# ---------------------------------------------------------------------------

def _odc_load_to_tifs(
    items: list,
    bbox: tuple[float, float, float, float],
    band_assets: list[str],
    out_dir: Path,
    *,
    resolution: int,
) -> tuple[dict[str, Path], str]:
    """Load ``band_assets`` for ``items`` via odc-stac and write each as a GeoTIFF.

    ``groupby="solar_day"`` collapses same-day acquisitions into a single
    time-step (mosaicking adjacent tiles). The resulting Dataset is
    aligned + reprojected to a single UTM CRS before we squeeze the time
    dimension and persist each band.

    Returns the per-band paths and the target CRS string.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info(
        "odc.stac.load: items=%d bands=%s resolution=%dm groupby=solar_day",
        len(items), band_assets, resolution,
    )
    ds = odc_load(
        items,
        bands=band_assets,
        bbox=list(bbox),
        crs="utm",
        resolution=resolution,
        groupby="solar_day",
        chunks={},
        fail_on_error=False,
    )

    # Pick the first (best-cloud) timestep — same-day mosaic when there's
    # one solar day among the selected items, otherwise the lowest-cloud
    # day (items were sorted ahead of time).
    if "time" in ds.dims and ds.sizes.get("time", 1) > 1:
        log.info("odc-stac returned %d time-steps; using the first.", ds.sizes["time"])
        ds = ds.isel(time=0)
    elif "time" in ds.dims:
        ds = ds.squeeze("time", drop=True)

    target_crs = str(ds.rio.crs) if ds.rio.crs else ""
    out_paths: dict[str, Path] = {}
    for band in band_assets:
        if band not in ds.data_vars:
            raise KeyError(
                f"Band {band!r} not in odc-stac result; loaded vars: {list(ds.data_vars)}"
            )
        da = ds[band]
        # odc-stac sometimes leaves both ``_FillValue`` attr and encoding;
        # xarray refuses to write that.
        da.attrs.pop("_FillValue", None)
        path = out_dir / f"{band}.tif"
        da.rio.to_raster(path, compress="deflate")
        out_paths[band] = path
    return out_paths, target_crs


def fetch_bands(
    asset_id: str,
    bbox: tuple[float, float, float, float],
    imagery: Imagery,
    band_assets: list[str],
    raw_dir: Path,
    *,
    location_label: str = "",
) -> tuple[dict[str, Path], SceneSet, list]:
    """Multi-scene mosaic of ``band_assets`` clipped to ``bbox``.

    On first run: STAC search → best per tile → ``odc.stac.load`` with
    ``groupby="solar_day"``. Cache layout under ``raw_dir/<asset_id>/``:
      * ``scenes.json`` — list of contributing :class:`SceneRecord` dicts
      * ``<band>.tif`` — merged mosaic across scenes, in the target CRS

    Returns ``(merged_paths, scene_set, items)``. ``items`` are the live
    pystac.Item objects (used by :func:`fetch_rgb_overview` to avoid a
    second STAC round-trip); ``[]`` when results came from cache.
    """
    out_dir = raw_dir / asset_id
    out_dir.mkdir(parents=True, exist_ok=True)
    scenes_path = out_dir / "scenes.json"
    expected_merged = {b: out_dir / f"{b}.tif" for b in band_assets}

    if scenes_path.exists() and all(p.exists() for p in expected_merged.values()):
        log.info("Using cached scene set + mosaicked bands at %s", out_dir)
        scene_set = SceneSet.from_dict(json.loads(scenes_path.read_text()))
        return expected_merged, scene_set, []

    platform = imagery.platform
    items = search_scenes_for_bbox(bbox, imagery, location_label=location_label)

    merged_paths, target_crs = _odc_load_to_tifs(
        items, bbox, band_assets, out_dir, resolution=int(platform.native_res_m),
    )

    scene_records = [_scene_record_from_item(it, platform.collection, target_crs, platform) for it in items]
    scene_set = SceneSet(
        scenes=scene_records,
        target_crs=target_crs,
        bbox_lonlat=list(bbox),
    )
    scenes_path.write_text(json.dumps(scene_set.to_dict(), indent=2))
    return merged_paths, scene_set, items


# ---------------------------------------------------------------------------
# RGB overview mosaic for the diagnostic thumbnail
# ---------------------------------------------------------------------------

def fetch_rgb_overview(
    asset_id: str,
    bbox: tuple[float, float, float, float],
    items: list,
    scene_set: SceneSet,
    imagery: Imagery,
    raw_dir: Path,
    *,
    rgb_keys: tuple[str, str, str] = ("red", "green", "blue"),
    resolution: int = THUMB_RES_M,
) -> dict[str, Path] | None:
    """Mosaicked low-resolution RGB bands for the page-2 thumbnail.

    Same odc-stac path as :func:`fetch_bands`, just at coarser resolution.
    If ``items`` is empty (cache hit upstream meant no live items kept),
    we re-search STAC and filter back to the items in ``scene_set``.
    """
    # Keyed by resolution: the thumbnail's ground resolution follows the
    # frame size (see pipeline.thumbnail_resolution_m).
    out_dir = raw_dir / asset_id / ("rgb_thumb" if resolution == THUMB_RES_M else f"rgb_thumb_{resolution}m")
    out_dir.mkdir(parents=True, exist_ok=True)
    expected = {k: out_dir / f"{k}.tif" for k in rgb_keys}
    if all(p.exists() for p in expected.values()):
        return expected

    if not items:
        all_items = search_scenes_for_bbox(bbox, imagery)
        wanted = {s.item_id for s in scene_set.scenes}
        items = [it for it in all_items if it.id in wanted]
        if not items:
            log.warning("Couldn't recover STAC items for RGB thumbnail; skipping.")
            return None

    # Verify the items have the RGB assets (not all collections do).
    if not all(k in items[0].assets for k in rgb_keys):
        missing = [k for k in rgb_keys if k not in items[0].assets]
        log.warning("RGB asset(s) %s missing from %s; skipping thumbnail.",
                    missing, items[0].id)
        return None

    paths, _ = _odc_load_to_tifs(
        items, bbox, list(rgb_keys), out_dir, resolution=resolution,
    )
    return paths


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def open_band(path: Path) -> xr.DataArray:
    """Eagerly load a band raster into an xarray DataArray."""
    da = rioxarray.open_rasterio(path, masked=True)
    if isinstance(da, list):
        da = da[0]
    if "band" in da.dims and da.sizes["band"] == 1:
        da = da.squeeze("band", drop=True)
    return da.load()
