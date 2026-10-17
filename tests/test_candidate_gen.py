"""Tests for auto-proposing candidates from the approved pools + merge safety."""

from __future__ import annotations

from spectralcular.candidate_gen import generate_candidates, merge_candidates
from spectralcular.candidates import CandidateSet
from spectralcular.pool import (
    ColormapPool,
    ColormapPoolEntry,
    IndexPool,
    IndexPoolEntry,
    LocationPool,
    LocationPoolEntry,
    ReviewStatus,
)


def _loc(id_, themes, status=ReviewStatus.approved):
    return LocationPoolEntry(
        id=id_, name=id_.title(), bbox=(-62.5, -4.5, -61.8, -3.9),
        landscape_character="x", themes=list(themes),
        season_windows=["2023-07-01/2023-08-31"], status=status,
    )


def _pools():
    locs = LocationPool(entries=[
        _loc("amazon", ["vegetation"]),
        _loc("congo", ["vegetation"]),
        _loc("sahara", ["soil_geology"]),                       # wrong theme
        _loc("rejected_one", ["vegetation"], ReviewStatus.rejected),
    ])
    idxs = IndexPool(entries=[
        IndexPoolEntry(name="NDVI", theme="vegetation", bands={"N": "nir", "R": "red"},
                       status=ReviewStatus.approved),
        IndexPoolEntry(name="EVI", theme="vegetation",
                       bands={"N": "nir", "R": "red", "B": "blue"},
                       status=ReviewStatus.proposed),                # not approved
    ])
    cmaps = ColormapPool(entries=[
        ColormapPoolEntry(name="bids:viridis", type="sequential",
                          semantic_register="neutral_analytical",
                          pairs_with=["NDVI"], status=ReviewStatus.approved),
        ColormapPoolEntry(name="bids:magma", type="sequential",
                          semantic_register="neutral_analytical",
                          pairs_with=["NDVI"], status=ReviewStatus.approved),
        ColormapPoolEntry(name="bids:inferno", type="sequential",
                          semantic_register="neutral_analytical",
                          pairs_with=["NDVI"], status=ReviewStatus.proposed),  # not approved
    ])
    return locs, idxs, cmaps


def test_generate_basic_matrix():
    locs, idxs, cmaps = _pools()
    merged, per_theme = generate_candidates(locs, idxs, cmaps, None)
    # 2 affine approved locations × NDVI × 2 approved colormaps = 4
    assert len(merged.entries) == 4
    assert per_theme["vegetation"] == 4
    # sahara (wrong theme), rejected_one (rejected), EVI/inferno (not approved) excluded
    assert all(e.index == "NDVI" for e in merged.entries)
    assert {e.location_id for e in merged.entries} == {"amazon", "congo"}
    assert {e.colormap for e in merged.entries} == {"bids:viridis", "bids:magma"}
    assert all(e.affinity_signal == "theme_match" for e in merged.entries)
    assert all(e.pairing_signal == "pairs_with" for e in merged.entries)


def test_per_location_index_cap():
    locs, idxs, cmaps = _pools()
    merged, _ = generate_candidates(locs, idxs, cmaps, None, per_location_index_cap=1)
    # 2 locations × NDVI × 1 colormap (capped)
    assert len(merged.entries) == 2


def test_regeneration_is_idempotent_and_preserves_status():
    locs, idxs, cmaps = _pools()
    first, _ = generate_candidates(locs, idxs, cmaps, None)
    # reviewer rejects one entry
    first.entries[0].status = ReviewStatus.rejected
    rejected_id = first.entries[0].id

    second, _ = generate_candidates(locs, idxs, cmaps, first)
    assert len(second.entries) == len(first.entries)  # no duplicates added
    by_id = second.by_id()
    assert by_id[rejected_id].status == ReviewStatus.rejected  # decision preserved


def test_expressive_in_overrides_theme_match():
    locs, idxs, cmaps = _pools()
    idxs.entries[0].expressive_in = ["amazon"]  # restrict NDVI to amazon only
    merged, _ = generate_candidates(locs, idxs, cmaps, None)
    assert {e.location_id for e in merged.entries} == {"amazon"}
    assert all(e.affinity_signal == "expressive_in" for e in merged.entries)


def test_merge_adds_only_new():
    a = CandidateSet(entries=[])
    locs, idxs, cmaps = _pools()
    fresh, _ = generate_candidates(locs, idxs, cmaps, None)
    merged = merge_candidates(a, fresh)
    assert len(merged.entries) == len(fresh.entries)
    # merging again adds nothing
    merged2 = merge_candidates(merged, fresh)
    assert len(merged2.entries) == len(merged.entries)
