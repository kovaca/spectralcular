"""Reading aids for spectral indices: theoretical bounds, reference-surface
values and hand-written interpretation guides.

All three are exported with each index by ``spectralcular export-web`` so the plate picker
can show what an index's values mean before and while a colormap is applied:

* **bounds**: the range the formula can produce from physically plausible band
  values (reflectance in (0, 1], kelvin for thermal, linear gamma0 for radar),
  found by evaluating the formula over the corners and interior of that box.
  A side whose extreme keeps growing as bands approach zero is *open*
  (unbounded), e.g. simple ratios.
* **surfaces**: the index evaluated with spyndex on the measured spectra in
  ``config/reference_surfaces.yaml``. A value that swings by more than a tenth
  of the index's span when each band moves by its noise level is reported as
  unstable instead (clear water on Landsat, where red and NIR sit at ~0);
  for open-ended indices the swing is judged relative to the value.
* **guides**: ``config/index_guides.yaml``, written by hand for the indices a
  plate is most likely to use.
"""

from __future__ import annotations

import itertools
import logging
import warnings
from pathlib import Path

import numpy as np
import spyndex
import yaml
from pydantic import BaseModel, Field, model_validator

from .indices import band_params, constant_defaults
from .platforms import Platform
from .reference_surfaces import SurfacesFile

log = logging.getLogger(__name__)

THERMAL = {"T", "T1", "T2"}
RADAR = {"VV", "VH", "HH", "HV"}
# Plausible physical ranges per band family for bound finding.
REFLECTANCE_RANGE = (1e-3, 1.0)
THERMAL_RANGE = (250.0, 340.0)
RADAR_RANGE = (1e-3, 1.0)
# Band perturbation used to decide whether a surface value is within noise.
REFLECTANCE_NOISE = 0.002
THERMAL_NOISE = 0.5
RADAR_NOISE_REL = 0.1
UNSTABLE_FRACTION = 0.15
# For open-ended indices (ratios) the swing is judged relative to the value.
UNSTABLE_RELATIVE = 0.5


def _band_range(param: str, eps: float) -> tuple[float, float]:
    if param in THERMAL:
        return THERMAL_RANGE
    if param in RADAR:
        return (eps, RADAR_RANGE[1])
    return (eps, REFLECTANCE_RANGE[1])


def _evaluate(name: str, bands: dict[str, np.ndarray], constants: dict[str, float]) -> np.ndarray:
    params = {**bands, **constants}
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore")
        out = spyndex.computeIndex(name, params=params)
    arr = np.asarray(out)
    if np.iscomplexobj(arr):
        arr = np.where(np.abs(arr.imag) < 1e-12, arr.real, np.nan)
    return np.asarray(arr, dtype="float64")


def _box(params: list[str], eps: float, n_random: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    ranges = [_band_range(p, eps) for p in params]
    corners = np.array(list(itertools.product(*ranges)), dtype="float64").reshape(-1, len(params))
    lo = np.array([r[0] for r in ranges])
    hi = np.array([r[1] for r in ranges])
    # Log-uniform interior samples reach the small-reflectance regime where ratios blow up.
    u = rng.random((n_random, len(params)))
    interior = np.exp(np.log(lo) + u * (np.log(hi) - np.log(lo)))
    pts = np.vstack([corners, interior])
    return {p: pts[:, i] for i, p in enumerate(params)}


def formula_bounds(name: str, platform: Platform, *, n_random: int = 6000, seed: int = 7) -> dict | None:
    """Sampled [lo, hi] of an index over plausible band values; open sides flagged."""
    params = band_params(name)
    constants = constant_defaults(name, platform)
    if not params or any(v is None for v in constants.values()) or len(params) > 10:
        return None
    rng = np.random.default_rng(seed)
    coarse = _evaluate(name, _box(params, 1e-3, n_random, rng), constants)
    fine = _evaluate(name, _box(params, 1e-5, 0, rng), constants)
    coarse = coarse[np.isfinite(coarse)]
    fine = fine[np.isfinite(fine)]
    if coarse.size == 0:
        return None
    lo, hi = float(coarse.min()), float(coarse.max())
    span = max(hi - lo, 1e-9)
    lo_open = fine.size > 0 and float(fine.min()) < lo - 0.5 * span
    hi_open = fine.size > 0 and float(fine.max()) > hi + 0.5 * span
    return {"lo": _nice(lo), "hi": _nice(hi), "loOpen": bool(lo_open), "hiOpen": bool(hi_open)}


def _nice(v: float) -> float:
    """Snap values within 1 % of a round number (±1, 0, 2 …) onto it."""
    for r in (round(v), round(v * 2) / 2, round(v, 1)):
        if abs(v - r) <= 0.01 * max(1.0, abs(r)):
            return float(r)
    return float(f"{v:.4g}")


def _perturbations(params: list[str], values: dict[str, float]) -> list[dict[str, float]]:
    out = []
    for p in params:
        v = values[p]
        if p in THERMAL:
            d = THERMAL_NOISE
        elif p in RADAR:
            d = abs(v) * RADAR_NOISE_REL
        else:
            d = REFLECTANCE_NOISE
        for sign in (-1, 1):
            out.append({**values, p: v + sign * d})
    return out


def surface_values(name: str, platform: Platform, surfaces: SurfacesFile | None, bounds: dict | None) -> tuple[list[dict], list[str]]:
    """Index value per reference surface on this platform: ([{id, value}], [unstable ids])."""
    if surfaces is None:
        return [], []
    params = band_params(name)
    constants = constant_defaults(name, platform)
    if any(v is None for v in constants.values()):
        return [], []
    stable: list[dict] = []
    unstable: list[str] = []
    for s in surfaces.surfaces:
        sample = s.samples.get(platform.id)
        if sample is None or not all(p in sample.values for p in params):
            continue
        base = {p: sample.values[p] for p in params}
        cases = [base, *_perturbations(params, base)]
        vals = _evaluate(name, {p: np.array([c[p] for c in cases]) for p in params}, constants)
        v0 = float(vals[0])
        if not np.isfinite(v0):
            unstable.append(s.id)
            continue
        finite = vals[np.isfinite(vals)]
        if bounds and not bounds["loOpen"] and not bounds["hiOpen"]:
            limit = UNSTABLE_FRACTION * (bounds["hi"] - bounds["lo"])
        else:
            limit = UNSTABLE_RELATIVE * max(abs(v0), 1e-6)
        if finite.size < vals.size or float(finite.max() - finite.min()) > limit:
            unstable.append(s.id)
            continue
        stable.append({"id": s.id, "value": round(v0, 4)})
    stable.sort(key=lambda d: d["value"])
    return stable, unstable


# ---------------------------------------------------------------------------
# Hand-written guides
# ---------------------------------------------------------------------------


class GuideClass(BaseModel):
    """One labeled value band; ``lo``/``hi`` None = open-ended."""

    lo: float | None = None
    hi: float | None = None
    label: str
    # Reference surfaces the author expects in this band; tests hold the
    # guide to the measured spectra (unstable surfaces are skipped).
    expect: list[str] = Field(default_factory=list)


class IndexGuide(BaseModel):
    measures: str
    high: str
    low: str
    # Where real scenes usually fall: the suggested fixed range for plates that
    # should read comparably across the book.
    typical: tuple[float, float]
    classes: list[GuideClass] = Field(default_factory=list)
    # A value with physical meaning on both sides (e.g. 0 = water/land for NDWI):
    # diverging maps should split here.
    center: float | None = None
    center_label: str | None = None
    caveats: list[str] = Field(default_factory=list)
    source: str | None = None

    @model_validator(mode="after")
    def _ordered(self):
        lo, hi = self.typical
        if not lo < hi:
            raise ValueError(f"typical range must increase: {self.typical}")
        prev_hi: float | None = None
        for i, c in enumerate(self.classes):
            if c.lo is not None and c.hi is not None and not c.lo < c.hi:
                raise ValueError(f"class {c.label!r}: lo must be below hi")
            if i > 0 and c.lo != prev_hi:
                raise ValueError(f"class {c.label!r} must start where the previous one ends ({prev_hi})")
            if i == 0 and c.lo is not None:
                raise ValueError("the first class must be open below (lo: null)")
            prev_hi = c.hi
        if self.classes and self.classes[-1].hi is not None:
            raise ValueError("the last class must be open above (hi: null)")
        return self

    def class_for(self, v: float) -> GuideClass | None:
        for c in self.classes:
            if (c.lo is None or v >= c.lo) and (c.hi is None or v < c.hi):
                return c
        return None


def load_guides(path: Path) -> dict[str, IndexGuide]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    return {name: IndexGuide.model_validate(body) for name, body in raw.items()}


def guide_record(g: IndexGuide) -> dict:
    return {
        "measures": g.measures,
        "high": g.high,
        "low": g.low,
        "typical": list(g.typical),
        "classes": [c.model_dump(exclude={"expect"}) for c in g.classes],
        "center": g.center,
        "centerLabel": g.center_label,
        "caveats": list(g.caveats),
        "source": g.source,
    }
