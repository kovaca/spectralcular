"""warp_to_frame: rotation 0 ≈ clip, 90° ≈ rotated grid, shape follows the frame."""

import numpy as np
import pytest
import rioxarray  # noqa: F401
import xarray as xr
from affine import Affine
from rasterio.warp import transform as warp_transform

from spectralcular.frame_warp import page_grid, warp_to_frame
from spectralcular.framing import FrameSpec

CRS = "EPSG:32633"
RES = 10.0


def _raster(nx=400, ny=400, x0=500_000.0, y0=4_180_000.0):
    """Synthetic north-up UTM raster whose value is its easting in km (a ramp)."""
    xs = x0 + (np.arange(nx) + 0.5) * RES
    ys = y0 - (np.arange(ny) + 0.5) * RES
    vals = np.broadcast_to((xs - x0) / 1000.0, (ny, nx)).astype("float32")
    da = xr.DataArray(vals.copy(), dims=("y", "x"), coords={"y": ys, "x": xs})
    da = da.rio.write_crs(CRS).rio.write_transform(Affine(RES, 0, x0, 0, -RES, y0))
    return da, x0, y0


def _center_lonlat(x, y):
    lon, lat = warp_transform(CRS, "EPSG:4326", [x], [y])
    return (lon[0], lat[0])


def test_page_grid_shape_and_orientation():
    da, x0, y0 = _raster()
    c = _center_lonlat(x0 + 2000, y0 - 2000)
    spec = FrameSpec(center=c, width_m=1400, height_m=1800, rotation_deg=0)
    tf, rows, cols, corners = page_grid(spec, da.rio.crs, RES)
    assert (rows, cols) == (180, 140)
    # north-up: x step is +RES east, y step is -RES (down)
    assert abs(tf.a - RES) < 0.05 and abs(tf.e + RES) < 0.05
    assert abs(tf.b) < 0.05 and abs(tf.d) < 0.05


def test_warp_north_up_matches_ramp():
    da, x0, y0 = _raster()
    c = _center_lonlat(x0 + 2000, y0 - 2000)
    spec = FrameSpec(center=c, width_m=1400, height_m=1800, rotation_deg=0)
    out = warp_to_frame(da, spec)
    assert out.values.shape == (180, 140)
    assert np.isfinite(out.values).all()
    # value increases left → right by ~RES/1000 per column
    row = out.values[90]
    assert np.allclose(np.diff(row), RES / 1000.0, atol=1e-3)
    assert abs(row[0] - (2000 - 700 + RES / 2) / 1000.0) < 0.02


def test_warp_rotated_90_puts_easting_ramp_along_columns():
    da, x0, y0 = _raster()
    c = _center_lonlat(x0 + 2000, y0 - 2000)
    spec = FrameSpec(center=c, width_m=1400, height_m=1800, rotation_deg=90)
    out = warp_to_frame(da, spec)
    assert out.values.shape == (180, 140)
    assert np.isfinite(out.values).mean() > 0.99
    # page top faces east → easting now varies along rows (top→bottom decreasing)
    col = out.values[:, 70]
    assert col[0] > col[-1]
    assert np.allclose(np.diff(col), -RES / 1000.0, atol=2e-3)
    # and is (nearly) constant across each row
    assert np.nanstd(out.values[90]) < 0.01


def test_warp_outside_data_is_nan():
    da, x0, y0 = _raster()
    c = _center_lonlat(x0 + 100, y0 - 2000)  # hugs the western edge
    spec = FrameSpec(center=c, width_m=1400, height_m=1800, rotation_deg=0)
    out = warp_to_frame(da, spec)
    assert np.isnan(out.values[:, :50]).all()
    assert np.isfinite(out.values[:, -50:]).all()


def test_rejects_raster_without_crs():
    da = xr.DataArray(np.zeros((4, 4), dtype="float32"), dims=("y", "x"))
    with pytest.raises(ValueError):
        warp_to_frame(da, FrameSpec(center=(0, 0), width_m=10, height_m=10))
