"""Platform-aware loading and index computation, offline.

Uses hand-built STAC-item stand-ins and tiny synthetic rasters; never touches
the network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest
import rioxarray  # noqa: F401
import xarray as xr

from spectralcular.config import Imagery, Index
from spectralcular.data_loader import _cloud_of, _scene_record_from_item, _tile_key, _unsigned
from spectralcular.indices import apply_value_scale, compute_index, quality_mask
from spectralcular.platforms import PLATFORMS

S2 = PLATFORMS["sentinel-2-l2a"]
LANDSAT = PLATFORMS["landsat-c2-l2"]
S1 = PLATFORMS["sentinel-1-rtc"]


def _item(id_: str, props: dict, assets: dict[str, str] | None = None):
    return SimpleNamespace(
        id=id_,
        properties=props,
        datetime=datetime(2026, 9, 5, tzinfo=timezone.utc),
        geometry={"type": "Polygon", "coordinates": []},
        assets={k: SimpleNamespace(href=v) for k, v in (assets or {}).items()},
    )


def test_tile_key_per_platform():
    s2 = _item("S2A_33SVB_20230716_0_L2A", {"grid:code": "MGRS-33SVB"})
    assert _tile_key(s2, S2) == "MGRS-33SVB"
    assert _tile_key(_item("S2A_33SVB_20230716_0_L2A", {}), S2) == "S2A_33SVB"
    ls = _item("LC09_L2SP_090091_20260905_02_T2", {"landsat:wrs_path": "090", "landsat:wrs_row": "091"})
    assert _tile_key(ls, LANDSAT) == "090/091"
    s1 = _item("S1D_IW_GRDH_1SDV_20260906T100805_rtc", {"sat:orbit_state": "ascending"})
    assert _tile_key(s1, S1) == s1.id


def test_cloud_and_unsigned_hrefs():
    ls = _item("LC09", {"eo:cloud_cover": 12.5}, {"red": "https://x.blob.core.windows.net/a/red.TIF?st=1&sig=abc"})
    assert _cloud_of(ls, LANDSAT) == 12.5
    assert _cloud_of(_item("S1", {}, {}), S1) == 0.0
    rec = _scene_record_from_item(ls, LANDSAT.collection, "EPSG:32633", LANDSAT)
    assert rec.asset_hrefs["red"] == "https://x.blob.core.windows.net/a/red.TIF"
    assert _unsigned("https://a/b.tif") == "https://a/b.tif"


def test_imagery_validates_platform_and_optional_cloud():
    im = Imagery(source="sentinel-1-rtc", date_range="2026-01-01/2026-02-01", cloud_cover_max=None)
    assert im.platform is S1 and im.cloud_cover_max is None
    with pytest.raises(ValueError, match="not in"):
        Imagery(source="modis", date_range="2026-01-01/2026-02-01")


def _raster(values: np.ndarray, path, *, nodata=None) -> None:
    da = xr.DataArray(
        values,
        dims=("y", "x"),
        coords={"y": np.arange(values.shape[0])[::-1] * 30.0 + 15, "x": np.arange(values.shape[1]) * 30.0 + 15},
    )
    da = da.rio.write_crs("EPSG:32633")
    if nodata is not None:
        da = da.rio.write_nodata(nodata)
    da.rio.to_raster(path)


def test_apply_value_scale_and_quality_mask():
    dn = xr.DataArray(np.array([[0, 20000], [40000, 65535]], dtype="uint16"), dims=("y", "x"))
    refl = apply_value_scale(dn, "red", LANDSAT)
    assert np.isnan(refl.values[0, 0])                       # nodata DN 0 → NaN
    assert refl.values[0, 1] == pytest.approx(20000 * 0.0000275 - 0.2)
    kelvin = apply_value_scale(dn, "lwir11", LANDSAT)
    assert kelvin.values[1, 0] == pytest.approx(40000 * 0.00341802 + 149.0)
    gamma0 = apply_value_scale(xr.DataArray(np.array([[-32768.0, 0.25]]), dims=("y", "x")), "vv", S1)
    assert np.isnan(gamma0.values[0, 0]) and gamma0.values[0, 1] == 0.25

    qa = xr.DataArray(np.array([[0b0000, 0b1000], [0b100000, 0b1000000]], dtype="uint16"), dims=("y", "x"))
    keep = quality_mask(qa, LANDSAT)
    # clear, cloud (bit 3), snow (bit 5), clear-with-unrelated-bit-6
    assert keep.values.tolist() == [[True, False], [False, True]]
    scl = xr.DataArray(np.array([[4, 9], [0, 6]], dtype="uint8"), dims=("y", "x"))
    assert quality_mask(scl, S2).values.tolist() == [[True, False], [False, True]]


def test_compute_index_landsat_thermal_with_qa_mask(tmp_path):
    n = np.array([[3000, 3000], [3000, 3000]], dtype="uint16") * 3        # ~0.05 reflectance
    r = np.array([[2000, 2000], [2000, 2000]], dtype="uint16") * 3
    t = np.array([[45000, 45000], [45000, 0]], dtype="uint16")           # ~302.8 K; one nodata pixel
    qa = np.array([[0, 0b1000], [0, 0]], dtype="uint16")                 # one cloud pixel
    for name, arr in (("nir08", n), ("red", r), ("lwir11", t), ("qa_pixel", qa)):
        _raster(arr, tmp_path / f"{name}.tif")
    tif, sidecar = compute_index(
        "test", Index(name="NDVIT", bands={"N": "nir08", "R": "red", "T": "lwir11"}),
        {k: tmp_path / f"{k}.tif" for k in ("nir08", "red", "lwir11")},
        tmp_path / "out", mask_path=tmp_path / "qa_pixel.tif", platform=LANDSAT,
    )
    out = rioxarray.open_rasterio(tif).squeeze().values
    assert sidecar["platform"] == "landsat-c2-l2"
    assert np.isnan(out[0, 1])            # cloud masked
    assert np.isnan(out[1, 1])            # thermal nodata
    nir = 9000 * 0.0000275 - 0.2
    red = 6000 * 0.0000275 - 0.2
    kelvin = 45000 * 0.00341802 + 149.0
    expected = (nir - red * kelvin / 10000.0) / (nir + red * kelvin / 10000.0)
    assert out[0, 0] == pytest.approx(expected, rel=1e-4)
    assert sidecar["stats"]["valid_fraction"] == pytest.approx(0.5)


def test_compute_index_sentinel2_default_matches_legacy(tmp_path):
    n = np.full((2, 2), 4000, dtype="uint16")
    r = np.full((2, 2), 1000, dtype="uint16")
    for name, arr in (("nir", n), ("red", r)):
        _raster(arr, tmp_path / f"{name}.tif")
    paths = {k: tmp_path / f"{k}.tif" for k in ("nir", "red")}
    tif_a, _ = compute_index("legacy", Index(name="NDVI", bands={"N": "nir", "R": "red"}), paths, tmp_path / "a")
    tif_b, _ = compute_index("plat", Index(name="NDVI", bands={"N": "nir", "R": "red"}), paths, tmp_path / "b", platform=S2)
    a = rioxarray.open_rasterio(tif_a).values
    b = rioxarray.open_rasterio(tif_b).values
    assert np.allclose(a, b) and a.flat[0] == pytest.approx(0.6)


def test_compute_index_wavelength_constants_from_platform(tmp_path):
    for name in ("nir", "red", "swir16"):
        _raster(np.full((2, 2), 2000, dtype="uint16"), tmp_path / f"{name}.tif")
    paths = {k: tmp_path / f"{k}.tif" for k in ("nir", "red", "swir16")}
    idx = Index(name="FAI", bands={"N": "nir", "R": "red", "S1": "swir16"})
    with pytest.raises(ValueError, match="lambda"):
        compute_index("nolambda", idx, paths, tmp_path / "x")
    tif, sidecar = compute_index("lambda", idx, paths, tmp_path / "y", platform=S2)
    assert np.isfinite(rioxarray.open_rasterio(tif).values).all()
