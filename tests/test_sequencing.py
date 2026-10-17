"""Tests for ordinal assignment + rhythm linting."""

from __future__ import annotations

from spectralcular.candidates import CandidateEntry, CandidateSet, candidate_slug
from spectralcular.pool import LocationPool, LocationPoolEntry, ReviewStatus
from spectralcular.sequencing import assign_ordinals, lint_sequence


def _cand(loc, cmap, status=ReviewStatus.approved, theme="vegetation", index="NDVI"):
    return CandidateEntry(
        id=candidate_slug(loc, index, cmap), location_id=loc, index=index,
        colormap=cmap, theme=theme, status=status,
    )


def _loc(id_, bbox):
    return LocationPoolEntry(
        id=id_, name=id_, bbox=bbox, landscape_character="x",
        themes=["vegetation"], season_windows=["2023-07-01/2023-08-31"],
        status=ReviewStatus.approved,
    )


def test_assign_ordinals_numbers_approved_only():
    cs = CandidateSet(entries=[
        _cand("a", "bids:viridis"),
        _cand("b", "bids:magma"),
        _cand("c", "bids:inferno", status=ReviewStatus.rejected),
    ])
    out = assign_ordinals(cs)
    by_id = out.by_id()
    approved_ords = sorted(e.ordinal for e in out.approved())
    assert approved_ords == [1, 2]
    # rejected entry keeps ordinal None
    assert by_id[candidate_slug("c", "NDVI", "bids:inferno")].ordinal is None


def test_assign_ordinals_breaks_colormap_runs():
    # Three viridis + three magma — greedy should interleave, not clump.
    entries = (
        [_cand(f"v{i}", "bids:viridis") for i in range(3)]
        + [_cand(f"m{i}", "bids:magma") for i in range(3)]
    )
    out = assign_ordinals(CandidateSet(entries=entries))
    ordered = sorted(out.approved(), key=lambda e: e.ordinal)
    cmaps = [e.colormap for e in ordered]
    # No two consecutive share a colormap (possible here since counts are equal).
    assert all(cmaps[i] != cmaps[i + 1] for i in range(len(cmaps) - 1))


def test_lint_flags_consecutive_colormap():
    # Force a clump by hand-assigning ordinals.
    e1 = _cand("a", "bids:viridis"); e1.ordinal = 1
    e2 = _cand("b", "bids:viridis"); e2.ordinal = 2
    cs = CandidateSet(entries=[e1, e2])
    loc_pool = LocationPool(entries=[
        _loc("a", (-1.0, -1.0, -0.5, -0.5)),
        _loc("b", (-1.0, -1.0, -0.5, -0.5)),
    ])
    violations = lint_sequence(cs, loc_pool)
    assert any(v.kind == "consecutive_colormap" for v in violations)


def test_lint_flags_missing_ordinal():
    cs = CandidateSet(entries=[_cand("a", "bids:viridis")])  # approved, ordinal None
    loc_pool = LocationPool(entries=[_loc("a", (-1.0, -1.0, -0.5, -0.5))])
    violations = lint_sequence(cs, loc_pool)
    assert any(v.kind == "missing_ordinal" for v in violations)
