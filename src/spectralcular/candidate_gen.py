"""Auto-propose Stage-4 candidate triples from the *approved* pools.

Implements docs/process.md Stage 4 §1–2 mechanically:

* **Index↔colormap pairing.** Primary signal is the colormap's curated
  ``pairs_with`` list (``colormaps.yaml``); when an index has no curated
  pairing we fall back to a type rule (sequential index → sequential
  colormap, diverging → diverging). A diverging index additionally rejects
  any non-diverging colormap, since a diverging field on a one-sided ramp
  loses its pivot.
* **Index↔location affinity.** Primary signal is the index's
  ``expressive_in`` list. It is empty across the pool today, so the real
  default is **theme-match**: a location is affine to an index when the
  location's ``themes`` contains the index's ``theme``. ``expressive_in``
  becomes an optional tightening knob the reviewer fills in over time.

Regeneration is **merge-safe**: existing entries (and their reviewer-set
``status`` / ``ordinal`` / notes) are never clobbered, and entries no
longer proposed are kept as audit trail (mirroring the pool loop, where
``rejected`` stays in YAML).
"""

from __future__ import annotations

import logging
from collections import Counter

from .candidates import (
    AffinitySignal,
    CandidateEntry,
    CandidateSet,
    PairingSignal,
    candidate_slug,
)
from .pool import (
    ColormapPool,
    ColormapPoolEntry,
    IndexPool,
    LocationPool,
    ReviewStatus,
)

log = logging.getLogger(__name__)


# Indices whose field is naturally diverging (rendered around a pivot).
# Everything not listed defaults to sequential — the common case. The
# existing curated Lake Powell spread pairs NDWI with cmocean:balance, so
# NDWI → diverging keeps the generator consistent with hand-built examples.
INDEX_PAIRING_TYPE: dict[str, str] = {
    "NDWI": "diverging",
}

# Print-safety ranking — prefer colormaps known to survive sRGB→CMYK.
_SAFETY_RANK = {"safe": 0, "watchlist": 1, "unverified": 2, "unsafe": 3}


def index_pairing_type(name: str) -> str:
    return INDEX_PAIRING_TYPE.get(name, "sequential")


def _rank_colormaps(cmaps: list[ColormapPoolEntry]) -> list[ColormapPoolEntry]:
    """Stable order preferring print-safe palettes, then by name."""
    return sorted(
        cmaps,
        key=lambda c: (_SAFETY_RANK.get(c.print_safety, 9), c.name),
    )


def merge_candidates(existing: CandidateSet, freshly_proposed: CandidateSet) -> CandidateSet:
    """Idempotent merge: preserve existing entries verbatim, add only new ids."""
    by_id = {e.id: e for e in existing.entries}
    added = 0
    for new in freshly_proposed.entries:
        if new.id in by_id:
            continue  # keep the reviewer's status/ordinal/notes
        by_id[new.id] = new
        added += 1
    log.info("merge: %d existing + %d new = %d total candidates",
             len(existing.entries), added, len(by_id))
    return CandidateSet(entries=list(by_id.values()))


def generate_candidates(
    loc_pool: LocationPool,
    idx_pool: IndexPool,
    cmap_pool: ColormapPool,
    existing: CandidateSet | None = None,
    *,
    per_location_index_cap: int = 6,
) -> tuple[CandidateSet, dict[str, int]]:
    """Build the proposed matrix from the approved pools and merge into ``existing``.

    Returns ``(merged_set, per_theme_counts)``. ``per_theme_counts`` counts
    *newly proposed* triples per theme so an empty/lopsided matrix is obvious.
    """
    approved_locs = [e for e in loc_pool.entries if e.status == ReviewStatus.approved]
    approved_idxs = [e for e in idx_pool.entries if e.status == ReviewStatus.approved]
    approved_cmaps = [e for e in cmap_pool.entries if e.status == ReviewStatus.approved]

    log.info(
        "approved pools: %d locations, %d indices, %d colormaps",
        len(approved_locs), len(approved_idxs), len(approved_cmaps),
    )

    proposed: dict[str, CandidateEntry] = {}
    per_theme: Counter = Counter()

    for idx in approved_idxs:
        want_type = index_pairing_type(idx.name)

        # --- affinity: which locations is this index expressive in? --------
        if idx.expressive_in:
            wanted = set(idx.expressive_in)
            aff_locs = [loc for loc in approved_locs if loc.id in wanted]
            aff_signal: AffinitySignal = "expressive_in"
        else:
            aff_locs = [loc for loc in approved_locs if idx.theme in loc.themes]
            aff_signal = "theme_match"

        if not aff_locs:
            log.info("index %s: no affine approved location (theme=%s)", idx.name, idx.theme)
            continue

        # --- pairing: which colormaps go with this index? ------------------
        primary = [c for c in approved_cmaps if idx.name in c.pairs_with]
        if primary:
            paired = primary
            pair_signal: PairingSignal = "pairs_with"
        else:
            paired = [c for c in approved_cmaps if c.type == want_type]
            pair_signal = "type_rule"

        # Diverging indices reject non-diverging colormaps even from pairs_with.
        if want_type == "diverging":
            paired = [c for c in paired if c.type == "diverging"]

        paired = _rank_colormaps(paired)[:per_location_index_cap]
        if not paired:
            log.info("index %s: no eligible colormap (want_type=%s)", idx.name, want_type)
            continue

        for loc in aff_locs:
            for cmap in paired:
                slug = candidate_slug(loc.id, idx.name, cmap.name)
                if slug in proposed:
                    continue
                proposed[slug] = CandidateEntry(
                    id=slug,
                    location_id=loc.id,
                    index=idx.name,
                    colormap=cmap.name,
                    theme=idx.theme,
                    status=ReviewStatus.proposed,
                    pairing_signal=pair_signal,
                    affinity_signal=aff_signal,
                )
                per_theme[idx.theme] += 1

    for theme in sorted(per_theme):
        log.info("  theme %-12s → %d proposed triple(s)", theme, per_theme[theme])
    if not proposed:
        log.warning(
            "Generated 0 candidates — check that approved indices, locations "
            "(sharing the index theme), and colormaps (pairs_with / type) overlap."
        )

    merged = merge_candidates(existing or CandidateSet(entries=[]),
                              CandidateSet(entries=list(proposed.values())))
    return merged, dict(per_theme)
