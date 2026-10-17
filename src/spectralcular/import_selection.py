"""``spectralcular import`` — pull a plate-picker selection manifest into the config.

The web picker (``web/``) exports ``<book>.selection.json`` (kind
``spectralcular.selection``, version 1 or 2; v2 adds ``kind`` per plate and
true-color plates carrying a ``truecolor`` tone spec instead of an index and
colormap). Importing it:

* upserts ad-hoc locations into ``config/pool/locations.yaml`` (existing ids
  are left untouched — the pool is the reviewer's, we only add);
* upserts candidates into ``config/candidates.yaml`` keyed by the candidate
  slug. New entries are created ``approved`` (or ``--status``). For existing
  entries the picker-owned fields (frame, scene_ids, date_range,
  normalization, caption, ordinal, reversed; truecolor, mask_enabled for
  true-color plates) are overwritten and the reviewer-owned
  ones (status, reviewer_notes) preserved — the same spirit as
  :func:`candidate_gen.merge_candidates`.

Everything is validated through the pydantic models, and the cross-pool check
runs at the end so a theme/index mismatch surfaces immediately.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .candidates import (
    CandidateEntry,
    CandidateSet,
    candidate_slug,
    validate_against_pools,
)
from .config import Normalization
from .framing import FrameSpec
from .platforms import PLATFORMS
from .pool import (
    ColormapPool,
    IndexPool,
    LocationPool,
    LocationPoolEntry,
    ReviewStatus,
)
from .truecolor import TrueColorSpec, TrueColorStretch

log = logging.getLogger(__name__)

MANIFEST_KIND = "spectralcular.selection"
# Exports from before the project was renamed (gradient-book → spectralcular).
LEGACY_KINDS = ("gradient-book.selection",)
SUPPORTED_VERSIONS = (1, 2)


class ImportError_(Exception):
    """Raised for a malformed or unsupported manifest."""


@dataclass
class ImportReport:
    locations_added: list[str] = field(default_factory=list)
    candidates_added: list[str] = field(default_factory=list)
    candidates_updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"locations +{len(self.locations_added)} · candidates +{len(self.candidates_added)} "
            f"~{len(self.candidates_updated)} · skipped {len(self.skipped)} · problems {len(self.problems)}"
        )


def load_manifest(path: str | Path) -> dict:
    raw = json.loads(Path(path).read_text())
    if raw.get("kind") != MANIFEST_KIND and raw.get("kind") not in LEGACY_KINDS:
        raise ImportError_(f"not a {MANIFEST_KIND} manifest (kind={raw.get('kind')!r})")
    if raw.get("version") not in SUPPORTED_VERSIONS:
        raise ImportError_(f"unsupported manifest version {raw.get('version')!r}; supported: {SUPPORTED_VERSIONS}")
    if not isinstance(raw.get("plates"), list):
        raise ImportError_("manifest has no plates list")
    return raw


def _frame_from_plate(p: dict) -> FrameSpec | None:
    f = p.get("frame")
    if not f:
        return None
    return FrameSpec(
        center=(float(f["center"][0]), float(f["center"][1])),
        width_m=float(f["widthM"]),
        height_m=float(f["heightM"]),
        rotation_deg=float(f.get("rotationDeg", 0.0)),
    )


def _normalization_from_plate(p: dict) -> Normalization | None:
    n = p.get("normalization") or {}
    vmin, vmax = n.get("vmin"), n.get("vmax")
    if vmin is None or vmax is None:
        return None
    # The picker resolves whatever method it used into a concrete range; the
    # build reproduces exactly what the user saw by pinning it as `fixed`.
    return Normalization(method="fixed", vmin=float(vmin), vmax=float(vmax))


def _location_from_plate(p: dict) -> LocationPoolEntry:
    loc = p["location"]
    bbox = tuple(float(v) for v in loc["bbox"])
    date_range = p.get("dateRange") or "2023-01-01/2023-12-31"
    scene = p.get("scene") or {}
    return LocationPoolEntry(
        id=p["locationId"],
        name=loc["name"],
        bbox=bbox,  # type: ignore[arg-type]
        landscape_character=loc.get("landscapeCharacter") or "",
        themes=list(loc.get("themes") or [p.get("theme", "vegetation")]),
        season_windows=[date_range],
        cloud_cover_max=10.0,
        source="web_picker",
        status=ReviewStatus.approved,
        notes=f"Added from the plate picker ({scene.get('itemId', 'no scene')}).",
    )


PICKER_OWNED = ("frame", "scene_ids", "date_range", "normalization", "caption", "ordinal", "reversed",
                "mask_enabled")
PICKER_OWNED_TRUECOLOR = ("frame", "scene_ids", "date_range", "caption", "ordinal", "truecolor", "mask_enabled")


def _truecolor_from_plate(p: dict) -> TrueColorSpec:
    """The manifest's camelCase tone record → :class:`TrueColorSpec`."""
    tc = p["truecolor"]
    st = tc["stretch"]
    return TrueColorSpec(
        preset=tc.get("preset"),
        stretch=TrueColorStretch(
            mode=st["mode"],
            source=st.get("source", "manual"),
            p_lo=float(st.get("pLo", 2.0)),
            p_hi=float(st.get("pHi", 98.0)),
            lo=st.get("lo"),
            hi=st.get("hi"),
        ),
        gamma=float(tc["gamma"]),
        contrast=float(tc.get("contrast", 0.0)),
        midpoint=float(tc.get("midpoint", 0.5)),
        saturation=float(tc.get("saturation", 1.0)),
    )


def _resolve_reversal(colormap: str, reversed_flag: bool, cmap_names: set[str]) -> tuple[str, bool]:
    """Prefer a reviewed ``<name>_r`` pool entry over a runtime flip.

    A reversed pick of ``cmocean:algae`` becomes ``cmocean:algae_r`` (which has
    its own status / pairing metadata) when that entry exists; otherwise the
    ``reversed`` flag is kept on the candidate.
    """
    if not reversed_flag:
        return colormap, False
    twin = f"{colormap}_r"
    if twin in cmap_names:
        return twin, False
    return colormap, True


def _merge_status(existing: CandidateEntry, new_status: ReviewStatus) -> ReviewStatus:
    """Curating a plate in the picker *is* the review decision: promote the
    entry to ``new_status``, but never demote an approved one."""
    if existing.status == ReviewStatus.approved and new_status != ReviewStatus.approved:
        return existing.status
    return new_status


def _upsert_truecolor(
    p: dict,
    loc_id: str,
    cand_by_id: dict[str, CandidateEntry],
    report: ImportReport,
    new_status: ReviewStatus,
) -> None:
    """True-color plates: no index/colormap pool to consult. The theme places
    the plate in the book (the picker sends the location's primary theme)."""
    slug = candidate_slug(loc_id, None, None, kind="truecolor")
    try:
        picker_fields = dict(
            frame=_frame_from_plate(p),
            scene_ids=[p["scene"]["itemId"]],
            date_range=p.get("dateRange"),
            caption=p.get("caption") or "",
            ordinal=int(p["ordinal"]) if p.get("ordinal") is not None else None,
            truecolor=_truecolor_from_plate(p),
            mask_enabled=bool(p.get("maskEnabled", True)),
        )
        if slug in cand_by_id:
            existing = cand_by_id[slug]
            # Revalidate: model_copy skips validators, and the tone spec is new.
            cand_by_id[slug] = CandidateEntry.model_validate({
                **existing.model_dump(), **picker_fields, "platform": p["platform"],
                "status": _merge_status(existing, new_status),
                "pairing_signal": "picker", "affinity_signal": "picker",
            })
            report.candidates_updated.append(slug)
        else:
            theme = p.get("theme") or ((p.get("location") or {}).get("themes") or ["vegetation"])[0]
            cand_by_id[slug] = CandidateEntry(
                id=slug,
                location_id=loc_id,
                kind="truecolor",
                platform=p["platform"],
                theme=theme,
                status=new_status,
                pairing_signal="picker",
                affinity_signal="picker",
                notes=p.get("notes") or "",
                **picker_fields,
            )
            report.candidates_added.append(slug)
    except Exception as e:  # noqa: BLE001 — pydantic errors are the useful part
        report.skipped.append(f"{slug}: {e}")


def import_selection(
    manifest: dict,
    candidates: CandidateSet,
    loc_pool: LocationPool,
    idx_pool: IndexPool,
    cmap_pool: ColormapPool,
    *,
    new_status: ReviewStatus = ReviewStatus.approved,
) -> tuple[CandidateSet, LocationPool, ImportReport]:
    """Merge ``manifest`` into the candidate set + location pool (pure; no I/O)."""
    report = ImportReport()
    loc_by_id = {e.id: e for e in loc_pool.entries}
    idx_theme = {e.name: e.theme for e in idx_pool.entries}
    cmap_names = {e.name for e in cmap_pool.entries}
    cand_by_id = {e.id: e for e in candidates.entries}

    new_locs: list[LocationPoolEntry] = []
    for p in manifest["plates"]:
        loc_id = p.get("locationId")
        kind = p.get("kind") or "index"
        index = p.get("index")
        colormap = p.get("colormap")
        if kind == "truecolor":
            if not loc_id or not p.get("truecolor"):
                report.skipped.append(f"plate #{p.get('ordinal')}: true-color plate without locationId/truecolor")
                continue
            platform = PLATFORMS.get(p.get("platform") or "")
            if platform is None or platform.rgb_assets is None:
                report.skipped.append(
                    f"plate #{p.get('ordinal')} ({loc_id}/true color): platform {p.get('platform')!r} "
                    "has no visible red/green/blue bands")
                continue
        elif kind != "index":
            report.skipped.append(f"plate #{p.get('ordinal')}: unknown plate kind {kind!r}")
            continue
        elif not (loc_id and index and colormap):
            report.skipped.append(f"plate #{p.get('ordinal')}: missing locationId/index/colormap")
            continue
        if not p.get("scene"):
            report.skipped.append(f"plate #{p.get('ordinal')} ({loc_id}/{index or 'true color'}): no scene pinned")
            continue

        if loc_id not in loc_by_id:
            try:
                entry = _location_from_plate(p)
            except Exception as e:  # noqa: BLE001 — report and continue
                report.skipped.append(f"plate #{p.get('ordinal')}: bad location ({e})")
                continue
            loc_by_id[loc_id] = entry
            new_locs.append(entry)
            report.locations_added.append(loc_id)

        if kind == "truecolor":
            _upsert_truecolor(p, loc_id, cand_by_id, report, new_status)
            continue

        theme = idx_theme.get(index) or p.get("theme")
        colormap, reversed_flag = _resolve_reversal(
            colormap, bool(p.get("colormapReversed", False)), cmap_names)
        slug = candidate_slug(loc_id, index, colormap, reversed=reversed_flag)
        picker_fields = dict(
            reversed=reversed_flag,
            frame=_frame_from_plate(p),
            scene_ids=[p["scene"]["itemId"]],
            date_range=p.get("dateRange"),
            normalization=_normalization_from_plate(p),
            mask_enabled=bool(p.get("maskEnabled", True)),
            caption=p.get("caption") or "",
            ordinal=int(p["ordinal"]) if p.get("ordinal") is not None else None,
        )
        try:
            if slug in cand_by_id:
                existing = cand_by_id[slug]
                # Keep the reviewer's notes; promote (never demote) the status.
                status = _merge_status(existing, new_status)
                merged = existing.model_copy(update={**picker_fields, "status": status,
                                                     "pairing_signal": "picker", "affinity_signal": "picker"})
                cand_by_id[slug] = merged
                report.candidates_updated.append(slug)
            else:
                cand_by_id[slug] = CandidateEntry(
                    id=slug,
                    location_id=loc_id,
                    index=index,
                    colormap=colormap,
                    theme=theme,
                    status=new_status,
                    pairing_signal="picker",
                    affinity_signal="picker",
                    notes=p.get("notes") or "",
                    **picker_fields,
                )
                report.candidates_added.append(slug)
        except Exception as e:  # noqa: BLE001 — pydantic errors are the useful part
            report.skipped.append(f"{slug}: {e}")

    merged_cands = CandidateSet(entries=list(cand_by_id.values()))
    merged_locs = LocationPool(entries=[*loc_pool.entries, *new_locs])
    report.problems = validate_against_pools(merged_cands, merged_locs, idx_pool, cmap_pool)
    return merged_cands, merged_locs, report
