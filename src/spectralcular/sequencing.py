"""Spread ordering + visual-rhythm linting (docs/process.md Stage 2 / Stage 4 §5).

``assign_ordinals`` lays the approved candidates out in book order: themes in
the awesome-spectral-indices taxonomy order, and within each theme a greedy
pass that avoids two consecutive spreads sharing a colormap. ``lint_sequence``
reports remaining rhythm violations without mutating anything — it's the
read-only check run by ``spectralcular candidates order --check`` and at ``lock``.

The reviewer always has the final say: ordinals live in ``candidates.yaml`` and
can be hand-edited; ``--auto`` just proposes a sensible first pass.
"""

from __future__ import annotations

from dataclasses import dataclass

from .candidates import THEME_ORDER, CandidateEntry, CandidateSet, look_key
from .pool import LocationPool
from .review.preview import bbox_dimensions_km

# Flag a monotonous stretch when this many spreads in a row share one
# location-density class (wide vs detail).
_DENSITY_RUN_LIMIT = 5


def _theme_rank(theme: str) -> int:
    return THEME_ORDER.index(theme) if theme in THEME_ORDER else len(THEME_ORDER)


def _greedy_break_colormap_runs(items: list[CandidateEntry]) -> list[CandidateEntry]:
    """Reorder so consecutive entries avoid the same colormap where possible."""
    remaining = list(items)
    ordered: list[CandidateEntry] = []
    last_cmap: str | None = None
    while remaining:
        pick = next((x for x in remaining if look_key(x) != last_cmap), None)
        if pick is None:
            pick = remaining[0]
        ordered.append(pick)
        remaining.remove(pick)
        last_cmap = look_key(pick)
    return ordered


def assign_ordinals(cs: CandidateSet) -> CandidateSet:
    """Return a copy of ``cs`` with ``ordinal`` set (1..N) on approved entries.

    Approved entries are ordered by theme taxonomy, then greedily de-duplicated
    on colormap within each theme. Non-approved entries get ``ordinal=None``.
    """
    by_id = {e.id: e.model_copy(deep=True) for e in cs.entries}
    approved = [by_id[e.id] for e in cs.approved()]

    # Stable base order inside each theme before the greedy pass.
    approved.sort(key=lambda e: (_theme_rank(e.theme), e.location_id, e.index or "", look_key(e)))

    sequence: list[CandidateEntry] = []
    for theme in sorted({e.theme for e in approved}, key=_theme_rank):
        theme_items = [e for e in approved if e.theme == theme]
        sequence.extend(_greedy_break_colormap_runs(theme_items))

    for n, e in enumerate(sequence, start=1):
        e.ordinal = n
    for e in by_id.values():
        if e.status.value != "approved":
            e.ordinal = None

    return CandidateSet(entries=list(by_id.values()))


# ---------------------------------------------------------------------------
# Rhythm linting
# ---------------------------------------------------------------------------

@dataclass
class RhythmViolation:
    ordinal: int
    kind: str  # "consecutive_colormap" | "density_monotony" | "missing_ordinal"
    detail: str


def _density_class(area_km2: float, threshold: float) -> str:
    return "wide" if area_km2 >= threshold else "detail"


def lint_sequence(cs: CandidateSet, loc_pool: LocationPool) -> list[RhythmViolation]:
    """Check the approved sequence against the Stage-2 visual-rhythm policy."""
    approved = cs.approved()
    violations: list[RhythmViolation] = []

    missing = [e for e in approved if e.ordinal is None]
    for e in missing:
        violations.append(RhythmViolation(0, "missing_ordinal",
                                          f"{e.id} is approved but has no ordinal"))
    ordered = sorted([e for e in approved if e.ordinal is not None], key=lambda e: e.ordinal)
    if not ordered:
        return violations

    # Consecutive colormap.
    for prev, cur in zip(ordered, ordered[1:]):
        if look_key(prev) == look_key(cur):
            violations.append(RhythmViolation(
                cur.ordinal, "consecutive_colormap",
                f"#{prev.ordinal} and #{cur.ordinal} both use {look_key(cur)}",
            ))

    # Density monotony — split locations at the median bbox area.
    loc_area = {
        e.id: (lambda wh: wh[0] * wh[1])(bbox_dimensions_km(tuple(e.bbox)))
        for e in loc_pool.entries
    }
    used = [loc_area.get(e.location_id, 0.0) for e in ordered]
    if used:
        srt = sorted(used)
        median = srt[len(srt) // 2]
        classes = [_density_class(loc_area.get(e.location_id, 0.0), median) for e in ordered]
        run_start = 0
        for i in range(1, len(classes) + 1):
            if i == len(classes) or classes[i] != classes[run_start]:
                run_len = i - run_start
                if run_len > _DENSITY_RUN_LIMIT:
                    violations.append(RhythmViolation(
                        ordered[run_start].ordinal, "density_monotony",
                        f"{run_len} consecutive '{classes[run_start]}' spreads "
                        f"(#{ordered[run_start].ordinal}–#{ordered[i - 1].ordinal})",
                    ))
                run_start = i
    return violations
