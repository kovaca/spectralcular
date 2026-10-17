"""Platform registry: mappings are complete and the shipped pool resolves on it."""

from __future__ import annotations

from pathlib import Path

import pytest
import spyndex

from spectralcular.platforms import PLATFORMS, get_platform
from spectralcular.pool import IndexPool, IndexPoolEntry, load_index_pool

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("pid", sorted(PLATFORMS))
def test_platform_mappings_complete(pid):
    p = PLATFORMS[pid]
    all_params = {b for i in spyndex.indices.values() for b in i.bands} - set(spyndex.constants)
    for param, asset in p.param_to_asset.items():
        assert param in all_params or param in spyndex.bands, f"{pid}: {param} is not a spyndex band parameter"
        assert asset in p.gsd_by_asset, f"{pid}: asset {asset} has no gsd"
    if p.mask is not None:
        assert p.mask.asset in p.gsd_by_asset
    for k in p.wavelength_nm:
        assert k in spyndex.constants and k.startswith("lambda")
    assert p.scale_for("nonexistent-asset") == p.value_scale["*"]


def test_get_platform_unknown_lists_known():
    with pytest.raises(KeyError, match="sentinel-2-l2a"):
        get_platform("modis")


def test_sentinel2_defaults_match_legacy_constants():
    from spectralcular.indices import DEFAULT_SCL_MASK_CLASSES, S2_REFLECTANCE_SCALE
    from spectralcular.book import S2_GSD_M
    s2 = PLATFORMS["sentinel-2-l2a"]
    assert S2_REFLECTANCE_SCALE == 10000.0
    assert tuple(s2.mask.values) == DEFAULT_SCL_MASK_CLASSES == (0, 1, 3, 8, 9, 10, 11)
    assert S2_GSD_M["coastal"] == 60 and s2.param_to_asset["A"] == "coastal"
    assert s2.mask.bitmask == sum(1 << c for c in (0, 1, 3, 8, 9, 10, 11))


def test_landsat_thermal_mapping_and_scales():
    ls = PLATFORMS["landsat-c2-l2"]
    assert ls.param_to_asset["T"] == ls.param_to_asset["T1"] == "lwir11"
    assert ls.scale_for("lwir11") == (0.00341802, 149.0)
    assert ls.scale_for("red") == (0.0000275, -0.2)
    assert ls.mask.kind == "qa-bits" and ls.mask.bitmask == 0b111111


def test_sentinel1_has_no_mask_or_cloud_property():
    s1 = PLATFORMS["sentinel-1-rtc"]
    assert s1.mask is None and s1.cloud_property is None
    assert s1.dtype == "float32" and s1.nodata_for("vv") == -32768.0


def test_shipped_pool_resolves_on_its_platforms():
    pool = load_index_pool(ROOT / "config" / "pool" / "indices.yaml")
    names = [e.name for e in pool.entries]
    assert len(names) == len(set(names))
    for e in pool.entries:
        plat = get_platform(e.platform)
        spec_params = set(spyndex.indices[e.name].bands) - set(spyndex.constants)
        assert set(e.bands) == spec_params, e.name
        assert all(a in plat.gsd_by_asset for a in e.bands.values()), e.name


def test_pool_entry_rejects_param_not_on_platform():
    with pytest.raises(ValueError, match="not available on sentinel-2-l2a"):
        IndexPoolEntry(name="NDVIT", theme="burn", bands={"N": "nir", "R": "red", "T": "lwir11"})
    with pytest.raises(ValueError, match="assets .* unknown"):
        IndexPoolEntry(name="NDPolI", theme="radar", bands={"VV": "vv", "VH": "swir16"},
                       platform="sentinel-1-rtc")


def test_pool_rejects_duplicate_names():
    e = IndexPoolEntry(name="NDVI", theme="vegetation", bands={"N": "nir", "R": "red"})
    with pytest.raises(ValueError, match="duplicate index name"):
        IndexPool(entries=[e, e])
