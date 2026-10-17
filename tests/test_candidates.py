"""Tests for the candidate set model, slug, load/dump, and pool validation."""

from __future__ import annotations

from spectralcular.candidates import (
    CandidateEntry,
    CandidateSet,
    candidate_slug,
    dump_candidates,
    load_candidates,
    validate_against_pools,
)
from spectralcular.pool import (
    ColormapPool,
    ColormapPoolEntry,
    IndexPool,
    IndexPoolEntry,
    LocationPool,
    LocationPoolEntry,
    ReviewStatus,
)


def _loc(id_="amazon", themes=("vegetation",)):
    return LocationPoolEntry(
        id=id_, name="Amazon", bbox=(-62.5, -4.5, -61.8, -3.9),
        landscape_character="rainforest", themes=list(themes),
        season_windows=["2023-07-01/2023-08-31"], status=ReviewStatus.approved,
    )


def _idx():
    return IndexPoolEntry(name="NDVI", theme="vegetation", bands={"N": "nir", "R": "red"},
                          status=ReviewStatus.approved)


def _cmap():
    return ColormapPoolEntry(name="bids:viridis", type="sequential",
                             semantic_register="neutral_analytical",
                             pairs_with=["NDVI"], status=ReviewStatus.approved)


def test_slug_is_filesystem_safe():
    assert candidate_slug("amazon", "NDVI", "bids:viridis") == "amazon__NDVI__bids_viridis"
    assert candidate_slug("amazon", "NDVI", "colorcurious:crameri.roma") == "amazon__NDVI__colorcurious_crameri_roma"
    assert candidate_slug("amazon", "NDVI", "bids:viridis", reversed=True) == "amazon__NDVI__bids_viridis_r"
    assert candidate_slug("amazon", "NDVI", "cmocean:algae_r", reversed=True) == "amazon__NDVI__cmocean_algae_r"


def test_custom_namespace_passes_name_validator():
    e = CandidateEntry(id="a__NDVI__custom_teal", location_id="a", index="NDVI", colormap="custom:teal",
                       theme="vegetation")
    assert e.colormap == "custom:teal" and e.reversed is False


def test_round_trip(tmp_path):
    cs = CandidateSet(entries=[
        CandidateEntry(
            id=candidate_slug("amazon", "NDVI", "bids:viridis"),
            location_id="amazon", index="NDVI", colormap="bids:viridis",
            theme="vegetation", status=ReviewStatus.approved, ordinal=3,
        )
    ])
    path = tmp_path / "candidates.yaml"
    dump_candidates(cs, path)
    loaded = load_candidates(path)
    assert len(loaded.entries) == 1
    e = loaded.entries[0]
    assert e.location_id == "amazon" and e.ordinal == 3
    assert e.status == ReviewStatus.approved


def test_load_missing_file_is_empty(tmp_path):
    assert load_candidates(tmp_path / "nope.yaml").entries == []


def test_duplicate_id_rejected():
    import pytest
    eid = candidate_slug("amazon", "NDVI", "bids:viridis")
    rows = [dict(id=eid, location_id="amazon", index="NDVI", colormap="bids:viridis", theme="vegetation")] * 2
    with pytest.raises(Exception):
        CandidateSet.model_validate({"entries": rows})


def test_validate_against_pools_flags_bad_refs():
    loc_pool = LocationPool(entries=[_loc()])
    idx_pool = IndexPool(entries=[_idx()])
    cmap_pool = ColormapPool(entries=[_cmap()])

    cs = CandidateSet(entries=[
        CandidateEntry(id="x", location_id="nowhere", index="NDVI",
                       colormap="bids:viridis", theme="water"),  # bad loc + theme mismatch
    ])
    problems = validate_against_pools(cs, loc_pool, idx_pool, cmap_pool)
    assert any("location_id" in p for p in problems)
    assert any("theme" in p for p in problems)


def test_validate_clean_set_has_no_problems():
    loc_pool = LocationPool(entries=[_loc()])
    idx_pool = IndexPool(entries=[_idx()])
    cmap_pool = ColormapPool(entries=[_cmap()])
    cs = CandidateSet(entries=[
        CandidateEntry(id=candidate_slug("amazon", "NDVI", "bids:viridis"),
                       location_id="amazon", index="NDVI", colormap="bids:viridis",
                       theme="vegetation"),
    ])
    assert validate_against_pools(cs, loc_pool, idx_pool, cmap_pool) == []
