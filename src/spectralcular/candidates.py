"""Stage-4 candidate set: Location × Index × Colormap triples.

This is the source of truth for the matrix-gating review loop (docs/process.md
Stage 4). It mirrors the Stage-3 pool pattern in :mod:`pool` — a list of
``entries`` each carrying a ``status`` field that the editorial pass mutates
in place by editing ``config/candidates.yaml``.

``spectralcular candidates generate`` seeds this file mechanically from the *approved*
pools; ``spectralcular candidates review`` renders the contact sheet; ``spectralcular candidates
lock`` derives the flat ``spreads.csv`` + per-spread configs from the
``approved`` subset.

Validators mirror the pool's: every index must resolve through ``spyndex``
and every colormap through :func:`colormaps.check_resolvable` so a typo
surfaces at load time.
``validate_against_pools`` is a separate, heavier cross-check run at
generate/lock time (it needs the loaded pools).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .config import Normalization
from .framing import FrameSpec
from .pool import (
    ColormapPool,
    IndexPool,
    LocationPool,
    ReviewStatus,
    Theme,
)
from .truecolor import TrueColorSpec

PlateKind = Literal["index", "truecolor"]
# Slug segment (and sequencing colormap key) for true-color plates.
TRUECOLOR = "truecolor"

# Provenance tags recorded on each generated entry so the reviewer can see
# *why* a triple was proposed (and a hand-added entry stays distinguishable).
PairingSignal = Literal["pairs_with", "type_rule", "experimental", "picker"]
AffinitySignal = Literal["expressive_in", "theme_match", "manual", "picker"]


def candidate_slug(
    location_id: str,
    index: str | None,
    colormap: str | None,
    *,
    reversed: bool = False,
    kind: PlateKind = "index",
) -> str:
    """Stable id for a triple: ``<location_id>__<index>__<colormap>[_r]``.

    A true-color plate has no index or colormap: ``<location_id>__truecolor``.

    The colormap's ``namespace:name`` (and catalog ``collection.name``)
    separators are flattened to ``_`` so the slug is filesystem- and URL-safe
    (it names PDF/PNG outputs). A reversed colormap gets an ``_r`` suffix
    unless the name already carries one.
    """
    if kind == TRUECOLOR:
        return f"{location_id}__{TRUECOLOR}"
    if index is None or colormap is None:
        raise ValueError("an index plate's slug needs an index and a colormap")
    cm = colormap.replace(":", "_").replace(".", "_")
    if reversed and not cm.endswith("_r"):
        cm += "_r"
    return f"{location_id}__{index}__{cm}"


class CandidateEntry(BaseModel):
    id: str
    location_id: str
    kind: PlateKind = "index"
    index: str | None = None                # required for index plates
    colormap: str | None = None             # required for index plates
    reversed: bool = False                  # flip the colormap (picker toggle)
    theme: Theme
    status: ReviewStatus = ReviewStatus.proposed
    ordinal: int | None = None
    pairing_signal: PairingSignal = "pairs_with"
    affinity_signal: AffinitySignal = "theme_match"
    normalization: Normalization | None = None
    caption: str = ""
    notes: str = ""
    reviewer_notes: str = ""
    # --- picker-owned fields (optional; absent for generator-proposed triples)
    frame: FrameSpec | None = None          # explicit page frame incl. rotation
    scene_ids: list[str] | None = None      # pinned STAC item ids
    date_range: str | None = None           # overrides the location's season window
    # --- true-color plates
    truecolor: TrueColorSpec | None = None  # tone pipeline (resolved stretch from the picker)
    platform: str | None = None             # imagery platform id; true color has no index to infer it from
    mask_enabled: bool = True               # quality-mask classes (cloud, shadow, …) → transparent

    @model_validator(mode="after")
    def _kind_fields(self):
        if self.kind == "index":
            if self.index is None or self.colormap is None:
                raise ValueError(f"{self.id}: an index plate needs index and colormap")
            if self.truecolor is not None:
                raise ValueError(f"{self.id}: an index plate cannot carry a truecolor spec")
        else:
            if self.truecolor is None:
                raise ValueError(f"{self.id}: a true-color plate needs a truecolor spec")
            if self.platform is None:
                raise ValueError(f"{self.id}: a true-color plate needs a platform")
            if self.index is not None or self.colormap is not None:
                raise ValueError(f"{self.id}: a true-color plate has no index or colormap")
            from .platforms import get_platform

            if get_platform(self.platform).rgb_assets is None:
                raise ValueError(f"{self.id}: platform {self.platform!r} has no visible red/green/blue bands")
        return self

    @field_validator("index")
    @classmethod
    def _index_known_to_spyndex(cls, v: str | None) -> str | None:
        import spyndex

        if v is None:
            return v
        if v not in spyndex.indices:
            raise ValueError(f"index {v!r} not in spyndex.indices")
        return v

    @field_validator("colormap")
    @classmethod
    def _colormap_resolves(cls, v: str | None) -> str | None:
        from .colormaps import CUSTOM_NAMESPACE, check_resolvable

        if v is None:
            return v
        # ``custom:`` entries carry inline colors in the pool; the name alone
        # can't be resolved here, and ``validate_against_pools`` checks membership.
        if v.startswith(CUSTOM_NAMESPACE + ":"):
            return v
        check_resolvable(v)
        return v


def look_key(e: CandidateEntry) -> str:
    """What a plate *looks like* for rhythm checks: its colormap, or
    ``"truecolor"`` — consecutive true-color plates read as a run too."""
    return TRUECOLOR if e.kind == TRUECOLOR else (e.colormap or "")


class CandidateSet(BaseModel):
    entries: list[CandidateEntry] = Field(default_factory=list)

    @field_validator("entries")
    @classmethod
    def _unique_ids(cls, v: list[CandidateEntry]) -> list[CandidateEntry]:
        seen: set[str] = set()
        for e in v:
            if e.id in seen:
                raise ValueError(f"duplicate candidate id {e.id!r}")
            seen.add(e.id)
        return v

    # --- convenience views -------------------------------------------------

    def by_id(self) -> dict[str, CandidateEntry]:
        return {e.id: e for e in self.entries}

    def approved(self) -> list[CandidateEntry]:
        return [e for e in self.entries if e.status == ReviewStatus.approved]

    def status_counts(self) -> dict[str, int]:
        from collections import Counter

        c = Counter(e.status.value for e in self.entries)
        return {s.value: c.get(s.value, 0) for s in ReviewStatus}


# ---------------------------------------------------------------------------
# Load / dump
# ---------------------------------------------------------------------------

# A stable, taxonomy-aware sort so regenerated/edited YAML stays diff-friendly:
# theme order, then location, index, colormap.
THEME_ORDER: list[str] = [
    "vegetation",
    "water",
    "burn",
    "soil_geology",
    "urban",
    "snow_ice",
    "atmosphere",
    "radar",
]


def _sort_key(e: CandidateEntry) -> tuple:
    theme_rank = THEME_ORDER.index(e.theme) if e.theme in THEME_ORDER else len(THEME_ORDER)
    # True-color plates sort after the location's index plates.
    return (theme_rank, e.location_id, e.kind == TRUECOLOR, e.index or "", e.colormap or "")


def load_candidates(path: str | Path) -> CandidateSet:
    p = Path(path)
    if not p.exists():
        return CandidateSet(entries=[])
    raw = yaml.safe_load(p.read_text()) or {}
    return CandidateSet.model_validate(raw)


def dump_candidates(cs: CandidateSet, path: str | Path) -> None:
    """Write the candidate set as YAML, sorted for stable diffs.

    ``None`` fields are dropped so the file stays readable; the loader fills
    defaults back in.
    """
    ordered = sorted(cs.entries, key=_sort_key)
    dumped = [e.model_dump(exclude_none=True, mode="json") for e in ordered]
    for d in dumped:  # the common case stays implicit so the file reads cleanly
        if not d.get("reversed"):
            d.pop("reversed", None)
        if d.get("kind") == "index":
            d.pop("kind", None)
        if d.get("mask_enabled", True):
            d.pop("mask_enabled", None)
    payload = {"entries": dumped}
    Path(path).write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)
    )


# ---------------------------------------------------------------------------
# Cross-pool validation
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    """Raised by :func:`validate_against_pools` with a collected report."""


def validate_against_pools(
    cs: CandidateSet,
    loc_pool: LocationPool,
    idx_pool: IndexPool,
    cmap_pool: ColormapPool,
) -> list[str]:
    """Return a list of human-readable problems; empty list means valid.

    Checks that every reference resolves and that each entry's denormalized
    ``theme`` matches the index's theme in the pool.
    """
    loc_ids = {e.id for e in loc_pool.entries}
    idx_theme = {e.name: e.theme for e in idx_pool.entries}
    cmap_names = {e.name for e in cmap_pool.entries}

    problems: list[str] = []
    for e in cs.entries:
        if e.location_id not in loc_ids:
            problems.append(f"{e.id}: location_id {e.location_id!r} not in locations pool")
        if e.kind == TRUECOLOR:
            # No index or colormap to cross-check; the theme places the plate
            # in the book and is already a valid Theme.
            continue
        if e.index not in idx_theme:
            problems.append(f"{e.id}: index {e.index!r} not in indices pool")
        elif idx_theme[e.index] != e.theme:
            problems.append(
                f"{e.id}: theme {e.theme!r} != index {e.index} pool theme "
                f"{idx_theme[e.index]!r}"
            )
        if e.colormap not in cmap_names:
            problems.append(f"{e.id}: colormap {e.colormap!r} not in colormaps pool")
    return problems
