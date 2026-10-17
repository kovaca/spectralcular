"""Regenerate the Stage-3 indices pool from spyndex, merge-safely.

For every platform in :data:`platforms.PLATFORMS`, admit each spyndex index
whose band parameters the platform can supply and whose application domain
maps onto a book theme. Admission is deliberately asymmetric:

* Sentinel-2 gets every eligible optical index (it out-resolves Landsat for
  anything both could compute).
* Landsat gets only the indices that need a thermal band (``T``/``T1``/``T2``).
* Sentinel-1 gets the radar indices achievable from one dual-pol product.

Regeneration never clobbers editorial state: entries already in the YAML keep
their ``status``, ``notes`` and ``expressive_in``; only ``theme``, ``platform``
and ``bands`` are refreshed. New indices arrive as ``proposed``; indices that
are no longer eligible are dropped and reported.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import spyndex
import yaml

from .indices import band_params as _index_band_params
from .platforms import PLATFORMS, Platform
from .pool import IndexPool, IndexPoolEntry, ReviewStatus, _NoAliasDumper, load_index_pool

# spyndex application_domain → book theme. ``kernel`` is deliberately
# absent: kernel indices need exp() and pre-computed kernel bands.
DOMAIN_TO_THEME: dict[str, str] = {
    "vegetation": "vegetation",
    "water": "water",
    "burn": "burn",
    "soil": "soil_geology",
    "urban": "urban",
    "snow": "snow_ice",
    "clouds": "atmosphere",
    "radar": "radar",
}

THERMAL_PARAMS = {"T", "T1", "T2"}

# Needs HH, HV *and* VV on one product; Sentinel-1 RTC is dual-pol only.
UNACHIEVABLE: dict[str, set[str]] = {"sentinel-1-rtc": {"QpRVI"}}

# Baselines validated by earlier spreads; always approved on first generation.
APPROVED_SEED = {"NDVI", "NDWI", "NDMI", "NBR", "EVI"}

POOL_HEADER = """\
# Stage-3 indices pool — derived from spyndex.indices via
# `uv run python -m spectralcular.pool_gen` (see src/spectralcular/pool_gen.py).
# Each entry maps spyndex parameter names to STAC asset keys on `platform`
# (see src/spectralcular/platforms.py). spyndex auto-supplies constants
# (g/C1/C2/L/…); band-centre wavelengths (lambda*) come from the platform.
#
# Resolution floor surfaces implicitly through `bands`: on Sentinel-2 any
# SWIR (S1/S2), N2 (B8A) or red-edge parameter pulls the grid to 20 m and
# A (coastal) to 60 m; Landsat is 30 m; Sentinel-1 RTC is 10 m.
#
# Edit `status:` / `notes:` / `expressive_in:` in place to drive the editorial
# loop — regeneration preserves them.
"""


def _band_params(index_name: str) -> set[str]:
    return set(_index_band_params(index_name))


def admits(platform: Platform, index_name: str, band_params: set[str]) -> bool:
    """Platform-specific admission rule on top of band availability."""
    if index_name in UNACHIEVABLE.get(platform.id, set()):
        return False
    if platform.id == "landsat-c2-l2":
        return bool(band_params & THERMAL_PARAMS)
    return True


def eligible_for_platform(platform: Platform) -> list[tuple[str, str, list[str]]]:
    """``[(name, theme, sorted band params), ...]`` admitted on ``platform``."""
    out: list[tuple[str, str, list[str]]] = []
    for name, idx in spyndex.indices.items():
        bands = _band_params(name)
        if not bands or not platform.supports(bands):
            continue
        theme = DOMAIN_TO_THEME.get(idx.application_domain)
        if theme is None:
            continue
        if not admits(platform, name, bands):
            continue
        out.append((name, theme, sorted(bands)))
    return out


def eligible_entries() -> dict[str, tuple[str, str, list[str]]]:
    """``{name: (platform_id, theme, band params)}`` across all platforms.

    Iteration order follows :data:`PLATFORMS`, so if two platforms ever admit
    the same index the first one wins (Sentinel-2 today).
    """
    out: dict[str, tuple[str, str, list[str]]] = {}
    for platform in PLATFORMS.values():
        for name, theme, bands in eligible_for_platform(platform):
            out.setdefault(name, (platform.id, theme, bands))
    return out


def _bands_map(platform: Platform, index_name: str) -> dict[str, str]:
    spec = spyndex.indices[index_name]
    # Preserve spyndex's documented parameter order; constants are excluded.
    return {p: platform.param_to_asset[p] for p in spec.bands if p in platform.param_to_asset}


def _auto_notes(index_name: str) -> str:
    idx = spyndex.indices[index_name]
    return f"{idx.long_name}. Formula: {idx.formula}. Reference: {idx.reference}"


@dataclass
class RegenReport:
    added: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)  # kept, but theme/platform/bands refreshed

    def summary(self) -> str:
        return (f"added {len(self.added)}, kept {len(self.kept)} "
                f"({len(self.changed)} refreshed), removed {len(self.removed)}")


def regenerate(existing: IndexPool | None) -> tuple[IndexPool, RegenReport]:
    """Merge the eligible set over ``existing``; see module docstring."""
    prior = existing.by_name() if existing is not None else {}
    report = RegenReport()
    entries: list[IndexPoolEntry] = []
    eligible = eligible_entries()

    theme_rank = {t: i for i, t in enumerate(dict.fromkeys(DOMAIN_TO_THEME.values()))}
    platform_rank = {pid: i for i, pid in enumerate(PLATFORMS)}

    for name, (platform_id, theme, _bands) in eligible.items():
        platform = PLATFORMS[platform_id]
        bands = _bands_map(platform, name)
        old = prior.get(name)
        if old is not None:
            entry = old.model_copy(update={"theme": theme, "platform": platform_id, "bands": bands})
            report.kept.append(name)
            if (old.theme, old.platform, old.bands) != (theme, platform_id, bands):
                report.changed.append(name)
        else:
            entry = IndexPoolEntry(
                name=name, theme=theme, platform=platform_id, bands=bands,
                status=ReviewStatus.approved if (name in APPROVED_SEED and existing is None) else ReviewStatus.proposed,
                notes=_auto_notes(name),
            )
            report.added.append(name)
        entries.append(entry)

    report.removed = sorted(n for n in prior if n not in eligible)
    entries.sort(key=lambda e: (platform_rank[e.platform], theme_rank[e.theme], e.name))
    return IndexPool(entries=entries), report


def dump_index_pool(pool: IndexPool, path: str | Path, *, header: str = POOL_HEADER) -> None:
    payload = {"entries": [e.model_dump(exclude_none=True, mode="json", exclude_defaults=False)
                           for e in pool.entries]}
    body = yaml.dump(payload, Dumper=_NoAliasDumper, sort_keys=False, allow_unicode=True,
                     width=110, default_flow_style=False)
    Path(path).write_text((header.rstrip("\n") + "\n\n" if header else "") + body)


def counts_by_platform(pool: IndexPool) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in pool.entries:
        out[e.platform] = out.get(e.platform, 0) + 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pool", type=Path, default=Path("config/pool/indices.yaml"))
    ap.add_argument("--dry-run", action="store_true", help="report, don't write")
    args = ap.parse_args(argv)

    existing = load_index_pool(args.pool) if args.pool.exists() else None
    pool, report = regenerate(existing)
    print(f"indices pool: {len(pool.entries)} entries — {report.summary()}")
    for pid, n in counts_by_platform(pool).items():
        print(f"  {pid}: {n}")
    if report.removed:
        print(f"  removed: {', '.join(report.removed)}")
    if report.changed:
        print(f"  refreshed: {', '.join(report.changed)}")
    if args.dry_run:
        print(f"(dry run — {args.pool} not written)")
        return 0
    dump_index_pool(pool, args.pool)
    print(f"wrote {args.pool}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
