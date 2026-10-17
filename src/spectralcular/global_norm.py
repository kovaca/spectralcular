"""Per-index global normalization (docs/process.md Sharp Corner 8).

One ``(vmin, vmax)`` per index, applied uniformly across every location using
that index, so the book reads coherently instead of each plate being
independently stretched. Computed from the coarse preview stats so it costs
nothing extra, and reused at production lock time as a ``fixed`` normalization
written into each spread config (its description prints on the diagnostic
page — "decide once, document everywhere").

A per-candidate ``normalization`` override always wins over the global range.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from .indices import index_domain
from .normalize import NormResult

log = logging.getLogger(__name__)

NORM_RANGES_FILENAME = "norm_ranges.json"

Method = str  # "percentile_pooled" | "domain"


@dataclass
class GlobalNormRange:
    index: str
    vmin: float
    vmax: float
    method: str
    description: str
    n_locations: int

    def to_norm_result(self) -> NormResult:
        return NormResult(
            vmin=self.vmin, vmax=self.vmax, method="fixed", description=self.description,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GlobalNormRange":
        return cls(**d)


def _read_p1_p99(stats_json: Path) -> tuple[float, float] | None:
    if not stats_json.exists():
        return None
    try:
        stats = json.loads(stats_json.read_text())["stats"]
        p1, p99 = float(stats["p1"]), float(stats["p99"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None
    if p1 != p1 or p99 != p99 or p99 <= p1:  # NaN or degenerate
        return None
    return p1, p99


def compute_global_norm(
    index_name: str,
    location_ids: list[str],
    *,
    preview_cache_dir: Path,
    method: Method = "percentile_pooled",
    diverging: bool = False,
) -> GlobalNormRange:
    """Pool per-location preview stats into one range for ``index_name``.

    ``method="domain"`` uses the index's canonical domain (e.g. NDVI → [-1, 1]).
    ``method="percentile_pooled"`` takes ``min(p1)``..``max(p99)`` across the
    locations' cached ``<index>_stats.json`` sidecars, falling back to the
    domain (then to a unit range) when no stats are available.
    ``diverging`` symmetrizes the range around zero.
    """
    domain = index_domain(index_name)

    if method == "domain":
        if domain is not None:
            vmin, vmax = domain
            return _finish(index_name, vmin, vmax, "domain",
                           f"global domain [{vmin:g}, {vmax:g}]", len(location_ids), diverging)
        log.info("index %s has no canonical domain; falling back to pooled percentiles", index_name)

    p1s: list[float] = []
    p99s: list[float] = []
    for loc_id in location_ids:
        pr = _read_p1_p99(preview_cache_dir / loc_id / f"{index_name}_stats.json")
        if pr is not None:
            p1s.append(pr[0])
            p99s.append(pr[1])

    if p1s:
        vmin, vmax = min(p1s), max(p99s)
        return _finish(index_name, vmin, vmax, "percentile_pooled",
                       f"global p1–p99 across {len(p1s)} location(s)", len(p1s), diverging)

    # Nothing computed yet — fall back to domain, then to a safe unit range.
    if domain is not None:
        vmin, vmax = domain
        return _finish(index_name, vmin, vmax, "domain_fallback",
                       f"global domain [{vmin:g}, {vmax:g}] (no stats yet)", 0, diverging)
    return _finish(index_name, -1.0, 1.0, "unit_fallback",
                   "global [-1, 1] (no stats, no domain)", 0, diverging)


def _finish(
    index_name: str, vmin: float, vmax: float, method: str, desc: str,
    n: int, diverging: bool,
) -> GlobalNormRange:
    if diverging:
        bound = max(abs(vmin), abs(vmax))
        vmin, vmax = -bound, bound
        desc = f"{desc}, diverging ±{bound:.3g}"
    return GlobalNormRange(
        index=index_name, vmin=float(vmin), vmax=float(vmax),
        method=method, description=desc, n_locations=n,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_norm_ranges(ranges: dict[str, GlobalNormRange], preview_cache_dir: Path) -> Path:
    out_dir = preview_cache_dir / "_global_norm"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / NORM_RANGES_FILENAME
    out.write_text(json.dumps({k: v.to_dict() for k, v in ranges.items()}, indent=2))
    return out


def load_norm_ranges(preview_cache_dir: Path) -> dict[str, GlobalNormRange]:
    path = preview_cache_dir / "_global_norm" / NORM_RANGES_FILENAME
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {k: GlobalNormRange.from_dict(v) for k, v in raw.items()}
