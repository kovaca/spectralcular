"""Lock approved candidates into the production merge sheet (Stage 4 §5 → 5).

Emits two durable artifacts from the ``approved`` subset of
``candidates.yaml``:

* ``config/spreads/<id>.yaml`` — a standalone :class:`SpreadConfig` per spread,
  so ``spectralcular run config/spreads/<id>.yaml`` works in isolation and the locked
  parameters are version-controlled and inspectable.
* ``spreads.csv`` — the flat merge sheet that ``spectralcular build``
  and ``spectralcular proof`` iterate, in book (ordinal) order.

Lock refuses to run while unresolved (`proposed` / `needs_revision`) entries
remain, unless ``force=True`` — the canonical "review is done" gate.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import yaml

from .candidate_build import candidate_to_spread_config
from .candidates import CandidateSet, validate_against_pools
from .config import SpreadConfig
from .global_norm import GlobalNormRange
from .pool import (
    ColormapPool,
    IndexPool,
    LocationPool,
    ReviewStatus,
)

log = logging.getLogger(__name__)

CSV_COLUMNS = [
    "ordinal", "id", "kind", "theme", "location_id", "index", "colormap",
    "normalization", "plate_number", "config_path", "status", "review_status",
]


class LockError(Exception):
    """Raised when the candidate set isn't ready to lock."""


def _truecolor_summary(cfg: SpreadConfig) -> str:
    """``<preset> lin[lo,hi]`` (linked) or ``<preset> rgb[lo,hi|lo,hi|lo,hi]``."""
    tc = cfg.truecolor
    st = tc.stretch
    name = tc.preset or "custom"
    if not st.resolved:
        return f"{name} auto[p{st.p_lo:g},p{st.p_hi:g}]"
    if st.mode == "linked":
        return f"{name} lin[{st.lo[0]:g},{st.hi[0]:g}]"
    return f"{name} rgb[" + "|".join(f"{l:g},{h:g}" for l, h in zip(st.lo, st.hi)) + "]"


def _norm_summary(cfg: SpreadConfig) -> str:
    if cfg.kind == "truecolor":
        return _truecolor_summary(cfg)
    n = cfg.colormap.normalization
    if n.method == "fixed" and n.vmin is not None and n.vmax is not None:
        return f"fixed[{n.vmin:g},{n.vmax:g}]"
    if n.method == "percentile":
        return f"pct[{n.lower:g},{n.upper:g}]"
    return n.method


def lock_candidates(
    candidate_set: CandidateSet,
    loc_pool: LocationPool,
    idx_pool: IndexPool,
    cmap_pool: ColormapPool,
    norm_ranges: dict[str, GlobalNormRange],
    *,
    config_spreads_dir: Path,
    spreads_csv_path: Path,
    force: bool = False,
) -> tuple[list[Path], Path]:
    """Validate + emit per-spread configs and the merge sheet.

    Returns ``(config_paths, spreads_csv_path)``.
    """
    problems = validate_against_pools(candidate_set, loc_pool, idx_pool, cmap_pool)
    if problems:
        raise LockError("candidate set references unknown pool entries:\n  - "
                        + "\n  - ".join(problems))

    unresolved = [
        e for e in candidate_set.entries
        if e.status in (ReviewStatus.proposed, ReviewStatus.needs_revision)
    ]
    if unresolved and not force:
        raise LockError(
            f"{len(unresolved)} unresolved candidate(s) "
            f"(proposed/needs_revision) — resolve them or pass force=True. "
            f"First few: {[e.id for e in unresolved[:5]]}"
        )

    locs = {e.id: e for e in loc_pool.entries}
    idxs = {e.name: e for e in idx_pool.entries}
    cmaps = {e.name: e for e in cmap_pool.entries}

    approved = candidate_set.approved()
    approved.sort(key=lambda e: (e.ordinal if e.ordinal is not None else 10**9, e.id))
    missing_ord = [e.id for e in approved if e.ordinal is None]
    if missing_ord:
        log.warning("%d approved candidate(s) have no ordinal (run `spectralcular candidates "
                    "order --auto` first): %s", len(missing_ord), missing_ord[:5])

    config_spreads_dir.mkdir(parents=True, exist_ok=True)
    spreads_csv_path.parent.mkdir(parents=True, exist_ok=True)

    config_paths: list[Path] = []
    rows: list[dict] = []
    for cand in approved:
        loc = locs[cand.location_id]
        if cand.kind == "truecolor":
            cfg = candidate_to_spread_config(cand, loc)
        else:
            idx = idxs[cand.index]
            cfg = candidate_to_spread_config(cand, loc, idx, norm_ranges.get(cand.index),
                                             cmap_entry=cmaps.get(cand.colormap))

        cfg_path = config_spreads_dir / f"{cand.id}.yaml"
        cfg_path.write_text(
            yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False, width=100)
        )
        config_paths.append(cfg_path)

        rows.append({
            "ordinal": cand.ordinal if cand.ordinal is not None else "",
            "id": cand.id,
            "kind": cand.kind,
            "theme": cand.theme,
            "location_id": cand.location_id,
            "index": cand.index or "",
            "colormap": "" if cand.kind == "truecolor" else cand.colormap + ("_r" if cand.reversed else ""),
            "normalization": _norm_summary(cfg),
            "plate_number": cfg.layout.plate_number,
            "config_path": str(cfg_path.relative_to(config_spreads_dir.parent.parent))
            if config_spreads_dir.parent.parent in cfg_path.parents else str(cfg_path),
            "status": cand.status.value,
            "review_status": "draft",
        })

    with spreads_csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    log.info("Locked %d spread(s) → %s (+ %s)",
             len(rows), spreads_csv_path, config_spreads_dir)
    return config_paths, spreads_csv_path
