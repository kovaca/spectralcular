"""Turn approved candidates into full-resolution spreads (Stage 5 production).

Maps a :class:`CandidateEntry` plus its pool records onto a
:class:`SpreadConfig`, then runs the pipeline. The batch sorts by
``(location_id, index)`` so every colormap variant of one location+index
reuses a single band fetch and index compute (see :func:`pipeline.run_config`
cache keys).
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from .candidates import CandidateEntry, CandidateSet
from .platforms import get_platform
from .config import (
    Colormap,
    Imagery,
    Index,
    Layout,
    Location,
    Normalization,
    SpreadConfig,
)
from .global_norm import GlobalNormRange
from .pipeline import run_config
from .colormaps import colormap_short_name
from .pool import ColormapPool, ColormapPoolEntry, IndexPool, IndexPoolEntry, LocationPool, LocationPoolEntry

log = logging.getLogger(__name__)


def _auto_caption(loc: LocationPoolEntry, idx: IndexPoolEntry, cmap_short: str) -> str:
    return f"{loc.name} — {idx.name}"


def _normalization_for(
    cand: CandidateEntry, global_norm: GlobalNormRange | None
) -> Normalization:
    """Per-candidate override wins; else the index global range as ``fixed``;
    else the schema default (per-image percentile)."""
    if cand.normalization is not None:
        return cand.normalization
    if global_norm is not None:
        return Normalization(method="fixed", vmin=global_norm.vmin, vmax=global_norm.vmax)
    return Normalization()


def _imagery_for(cand: CandidateEntry, loc: LocationPoolEntry, platform_id: str) -> Imagery:
    return Imagery(
        source=platform_id,
        date_range=cand.date_range or loc.season_windows[0],
        cloud_cover_max=loc.cloud_cover_max if get_platform(platform_id).cloud_property else None,
        item_ids=cand.scene_ids or None,
    )


def _layout_for(cand: CandidateEntry, caption: str) -> Layout:
    return Layout(
        template="full_image_plus_diagnostics",
        page_size="Letter",
        orientation="portrait",
        dpi=300,
        annotations="minimal",
        caption=cand.caption or caption,
        plate_number=f"{cand.ordinal:03d}" if cand.ordinal is not None else "000",
        outputs=["pdf"],
    )


def truecolor_to_spread_config(cand: CandidateEntry, loc: LocationPoolEntry) -> SpreadConfig:
    """A true-color candidate needs no index or colormap pool record: the
    platform comes from the candidate and the tone spec travels verbatim."""
    if cand.kind != "truecolor" or cand.truecolor is None or cand.platform is None:
        raise ValueError(f"{cand.id} is not a true-color candidate")
    bbox = cand.frame.padded_bbox() if cand.frame is not None else tuple(loc.bbox)
    return SpreadConfig(
        asset_id=cand.id,
        location=Location(name=loc.name, bbox=bbox),
        imagery=_imagery_for(cand, loc, cand.platform),
        frame=cand.frame,
        truecolor=cand.truecolor,
        mask_enabled=cand.mask_enabled,
        layout=_layout_for(cand, f"{loc.name} — True color"),
    )


def candidate_to_spread_config(
    cand: CandidateEntry,
    loc: LocationPoolEntry,
    idx: IndexPoolEntry | None = None,
    global_norm: GlobalNormRange | None = None,
    cmap_entry: ColormapPoolEntry | None = None,
) -> SpreadConfig:
    """``cmap_entry`` (the pool record) supplies inline ``colors`` so the
    emitted spread config stays standalone. True-color candidates ignore
    ``idx`` / ``global_norm`` / ``cmap_entry``."""
    if cand.kind == "truecolor":
        return truecolor_to_spread_config(cand, loc)
    if idx is None:
        raise ValueError(f"{cand.id}: an index plate needs its index pool record")
    short_cmap = colormap_short_name(cand.colormap, reversed=cand.reversed)
    # A pinned frame fetches its own (padded) envelope rather than the pool bbox.
    bbox = cand.frame.padded_bbox() if cand.frame is not None else tuple(loc.bbox)
    return SpreadConfig(
        asset_id=cand.id,
        location=Location(name=loc.name, bbox=bbox),
        imagery=_imagery_for(cand, loc, idx.platform),
        frame=cand.frame,
        index=Index(name=idx.name, bands=idx.bands),
        colormap=Colormap(
            name=cand.colormap,
            reversed=cand.reversed,
            colors=cmap_entry.colors if cmap_entry is not None else None,
            normalization=_normalization_for(cand, global_norm),
        ),
        mask_enabled=cand.mask_enabled,
        layout=_layout_for(cand, _auto_caption(loc, idx, short_cmap)),
    )


def cache_key_for(cand: CandidateEntry) -> str:
    """Band/index cache key: the location, plus a hash when the plate pins its
    own scenes or frame so picker plates never reuse another plate's mosaic."""
    if not cand.scene_ids and cand.frame is None:
        return cand.location_id
    h = hashlib.sha1()
    for sid in sorted(cand.scene_ids or []):
        h.update(sid.encode())
    if cand.frame is not None:
        h.update(repr(cand.frame.model_dump()).encode())
    return f"{cand.location_id}__{h.hexdigest()[:8]}"


def index_cache_key_for(cand: CandidateEntry) -> str:
    """Index cache key: the band key, kept apart when the quality mask is off
    (the masked and unmasked rasters share bands but not values)."""
    key = cache_key_for(cand)
    return key if cand.mask_enabled else f"{key}__nomask"


class BuildIncomplete(RuntimeError):
    """Some spreads failed under ``keep_going``; the rest were built."""

    def __init__(self, outputs: list[Path], failures: list[tuple[str, str]]):
        self.outputs, self.failures = outputs, failures
        super().__init__(f"{len(failures)} spread(s) failed: " + ", ".join(i for i, _ in failures))


def _build_one(cand, locs, idxs, cmaps, norm_ranges, root: Path, review_status: str) -> Path | None:
    loc = locs.get(cand.location_id)
    if cand.kind == "truecolor":
        if loc is None:
            log.warning("skipping %s: location %s not in the pool", cand.id, cand.location_id)
            return None
        cfg = candidate_to_spread_config(cand, loc)
        log.info("Building %s (%s / true color · %s)", cand.id, cand.location_id,
                 cand.truecolor.preset if cand.truecolor else "custom")
        return run_config(cfg, root, band_cache_key=cache_key_for(cand), review_status=review_status)
    idx = idxs.get(cand.index)
    if loc is None or idx is None:
        log.warning("skipping %s: missing pool record (loc=%s idx=%s)",
                    cand.id, loc is not None, idx is not None)
        return None
    cfg = candidate_to_spread_config(cand, loc, idx, norm_ranges.get(cand.index),
                                     cmap_entry=cmaps.get(cand.colormap))
    log.info("Building %s (%s / %s / %s%s)", cand.id, cand.location_id, cand.index, cand.colormap,
             " reversed" if cand.reversed else "")
    return run_config(
        cfg, root,
        band_cache_key=cache_key_for(cand),
        index_cache_key=index_cache_key_for(cand),
        review_status=review_status,
    )


def build_candidates(
    candidate_set: CandidateSet,
    loc_pool: LocationPool,
    idx_pool: IndexPool,
    norm_ranges: dict[str, GlobalNormRange],
    root: Path,
    *,
    theme: str | None = None,
    only_id: str | None = None,
    review_status: str = "draft",
    cmap_pool: ColormapPool | None = None,
    skip_existing: bool = False,
    keep_going: bool = False,
) -> list[Path]:
    """Render the approved candidates at full resolution. Returns output paths.

    ``skip_existing`` leaves spreads that already have a PDF alone, so an
    interrupted book build resumes; ``keep_going`` builds the rest when one
    fails and raises :class:`BuildIncomplete` listing the failures at the end.

    ``review_status`` is the Stage-5 *rendered-output* review state recorded in
    each sidecar (``draft`` → editorial pass over the printed proof promotes it);
    it is distinct from the candidate's editorial selection status.
    """
    locs = {e.id: e for e in loc_pool.entries}
    idxs = {e.name: e for e in idx_pool.entries}
    cmaps = {e.name: e for e in cmap_pool.entries} if cmap_pool is not None else {}

    targets = candidate_set.approved()
    if theme is not None:
        targets = [c for c in targets if c.theme == theme]
    if only_id is not None:
        targets = [c for c in targets if c.id == only_id]

    # Group by (location, index) so the shared band/index cache is paid once.
    targets.sort(key=lambda c: (c.location_id, c.index or "", c.ordinal or 0))

    spreads_dir = root / "output" / "spreads"
    outputs: list[Path] = []
    failures: list[tuple[str, str]] = []
    for i, cand in enumerate(targets, 1):
        done = spreads_dir / f"{cand.id}.pdf"
        if skip_existing and done.exists() and done.stat().st_size > 0:
            log.info("[%d/%d] %s: already built, skipped", i, len(targets), cand.id)
            outputs.append(done)
            continue
        t0 = time.monotonic()
        try:
            out = _build_one(cand, locs, idxs, cmaps, norm_ranges, root, review_status)
        except Exception as e:  # noqa: BLE001 — keep_going reports every failure at the end
            if not keep_going:
                raise
            log.error("[%d/%d] %s FAILED: %s", i, len(targets), cand.id, e)
            failures.append((cand.id, f"{type(e).__name__}: {e}"))
            continue
        if out is not None:
            log.info("[%d/%d] %s built in %.0f s", i, len(targets), cand.id, time.monotonic() - t0)
            outputs.append(out)
    if failures:
        raise BuildIncomplete(outputs, failures)
    log.info("Built %d spread(s).", len(outputs))
    return outputs
