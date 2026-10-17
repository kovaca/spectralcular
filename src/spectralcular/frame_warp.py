"""Warp an index raster onto an explicit (possibly rotated) page grid.

The plate picker hands us a :class:`FrameSpec` — center, width, height and the
compass bearing of the page top. We build the destination grid *in the source
raster's CRS* (Sentinel-2 UTM) so no reprojection is involved, only a rotated
affine: pixel rows run along the page's horizontal axis, columns down the
page. ``rasterio.warp.reproject`` then resamples the north-up index onto it.

Rotation 0 reproduces a plain clip (up to resampling); the result is always a
page-aligned array whose shape matches the frame at the raster's native GSD.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
import xarray as xr
from affine import Affine
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform as warp_transform

from .framing import FrameSpec

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WarpedPlate:
    values: np.ndarray            # float32 (rows, cols), NaN outside data
    transform: Affine             # dst grid → source CRS
    crs: str
    res_m: float
    corners_crs: list[tuple[float, float]]  # TL, TR, BR, BL in source CRS


def _frame_corners_in_crs(spec: FrameSpec, crs) -> list[tuple[float, float]]:
    lonlat = spec.corners_lonlat()
    xs, ys = warp_transform("EPSG:4326", crs, [p[0] for p in lonlat], [p[1] for p in lonlat])
    return list(zip(xs, ys))


def page_grid(spec: FrameSpec, crs, res_m: float) -> tuple[Affine, int, int, list[tuple[float, float]]]:
    """Destination affine + shape for the page frame in ``crs`` at ``res_m``.

    The grid's x axis follows the page's top edge (TL → TR) and its y axis the
    left edge (TL → BL), measured in the projected CRS so the warp is exact
    even where the UTM grid north differs slightly from true north.
    """
    tl, tr, br, bl = _frame_corners_in_crs(spec, crs)
    ex, ey = tr[0] - tl[0], tr[1] - tl[1]          # page-x direction (meters)
    dx, dy = bl[0] - tl[0], bl[1] - tl[1]          # page-y (down) direction
    width_m = math.hypot(ex, ey)
    height_m = math.hypot(dx, dy)
    cols = max(1, int(round(width_m / res_m)))
    rows = max(1, int(round(height_m / res_m)))
    # Unit vectors scaled to one pixel step.
    ax_, ay_ = ex / width_m * res_m, ey / width_m * res_m
    bx_, by_ = dx / height_m * res_m, dy / height_m * res_m
    # x_crs = tl.x + col*ax + row*bx ; y_crs = tl.y + col*ay + row*by
    transform = Affine(ax_, bx_, tl[0], ay_, by_, tl[1])
    return transform, rows, cols, [tl, tr, br, bl]


def warp_to_frame(
    index_da: xr.DataArray,
    spec: FrameSpec,
    *,
    resampling: Resampling = Resampling.bilinear,
) -> WarpedPlate:
    """Resample a north-up index raster onto the page grid described by ``spec``."""
    crs = index_da.rio.crs
    if crs is None:
        raise ValueError("index raster has no CRS")
    res_x, res_y = index_da.rio.resolution()
    res_m = float(abs(res_x))
    transform, rows, cols, corners = page_grid(spec, crs, res_m)

    src = np.asarray(index_da.values, dtype="float32")
    src = np.where(np.isfinite(src), src, np.nan).astype("float32")
    dst = np.full((rows, cols), np.nan, dtype="float32")
    reproject(
        source=src,
        destination=dst,
        src_transform=index_da.rio.transform(),
        src_crs=crs,
        src_nodata=np.nan,
        dst_transform=transform,
        dst_crs=crs,
        dst_nodata=np.nan,
        resampling=resampling,
    )
    log.info(
        "Warped index → page grid %d×%d px @ %.1f m, rotation %.1f° (%.1f%% valid)",
        cols, rows, res_m, spec.rotation_deg, 100 * np.isfinite(dst).mean(),
    )
    return WarpedPlate(values=dst, transform=transform, crs=str(crs), res_m=res_m, corners_crs=corners)
