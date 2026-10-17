from pathlib import Path

import pytest

from spectralcular.index_reading import IndexGuide, formula_bounds, load_guides, surface_values
from spectralcular.platforms import get_platform
from spectralcular.reference_surfaces import SURFACES, load_surfaces

ROOT = Path(__file__).resolve().parents[1]
S2 = get_platform("sentinel-2-l2a")
LANDSAT = get_platform("landsat-c2-l2")
S1 = get_platform("sentinel-1-rtc")


@pytest.fixture(scope="module")
def surfaces():
    sf = load_surfaces(ROOT / "config" / "reference_surfaces.yaml")
    assert sf is not None, "run `spectralcular reference-surfaces`"
    return sf


def test_normalized_differences_are_closed_on_minus_one_to_one():
    for name in ("NDVI", "NDWI", "NBR"):
        assert formula_bounds(name, S2) == {"lo": -1.0, "hi": 1.0, "loOpen": False, "hiOpen": False}


def test_ratios_are_open_above():
    b = formula_bounds("VVVHR", S1)
    assert b["lo"] == 0.0 and b["hiOpen"] and not b["loOpen"]
    assert formula_bounds("CIG", S2)["hiOpen"]


def test_every_surface_spec_was_sampled_on_sentinel_2(surfaces):
    have = {s.id for s in surfaces.surfaces if "sentinel-2-l2a" in s.samples}
    assert have == {s.id for s in SURFACES if "sentinel-2-l2a" in s.sites}


def test_surface_values_are_ordered_and_physical(surfaces):
    vals, unstable = surface_values("NDVI", S2, surfaces, formula_bounds("NDVI", S2))
    by = {d["id"]: d["value"] for d in vals}
    assert [d["value"] for d in vals] == sorted(by.values())
    assert by["rainforest"] > by["cropland"] > 0.5 > by["dry_grass"] > 0 > by["open_water"]
    assert not unstable


def test_near_zero_reflectance_is_reported_unstable(surfaces):
    # Clear water on Landsat: red and NIR sit at ~0, so NDVI there is noise.
    _, unstable = surface_values("NDVI", LANDSAT, surfaces, formula_bounds("NDVI", LANDSAT))
    assert "open_water" in unstable


def test_guides_agree_with_the_reference_surfaces(surfaces):
    guides = load_guides(ROOT / "config" / "index_guides.yaml")
    assert {"NDVI", "NDWI", "NBR", "NDMI"} <= set(guides)
    problems = []
    for name, g in guides.items():
        vals, unstable = surface_values(name, S2, surfaces, formula_bounds(name, S2))
        by = {d["id"]: d["value"] for d in vals}
        for c in g.classes:
            for sid in c.expect:
                if sid in unstable:
                    continue
                assert sid in by, f"{name}: no reference value for {sid}"
                got = g.class_for(by[sid])
                if got is not c:
                    problems.append(f"{name}: {sid}={by[sid]:.3f} lands in {got.label if got else None!r}, expected {c.label!r}")
        lo, hi = g.typical
        inside = [v for v in by.values() if lo <= v <= hi]
        if len(inside) < len(by) * 0.7:
            problems.append(f"{name}: typical {g.typical} misses most reference surfaces")
    assert not problems, "\n".join(problems)


def test_guide_classes_must_tile_the_axis():
    base = {"measures": "m", "high": "h", "low": "l", "typical": [0, 1]}
    IndexGuide.model_validate({**base, "classes": [{"hi": 0.5, "label": "a"}, {"lo": 0.5, "label": "b"}]})
    with pytest.raises(ValueError):
        IndexGuide.model_validate({**base, "classes": [{"hi": 0.5, "label": "a"}, {"lo": 0.6, "label": "b"}]})
    with pytest.raises(ValueError):
        IndexGuide.model_validate({**base, "classes": [{"lo": 0.0, "hi": 0.5, "label": "a"}, {"lo": 0.5, "label": "b"}]})
    with pytest.raises(ValueError):
        IndexGuide.model_validate({**base, "typical": [1, 0]})
