"""True-color plates: the tone pipeline from surface reflectance to display RGB.

A true-color plate renders the visible red, green and blue bands instead of an
index through a colormap. The tone pipeline is defined *once*, here, and
mirrored line for line by the web picker (``web/src/lib/raster/truecolor.ts``
and its GLSL module) so a plate prints exactly as it was tuned. ``gb
export-web`` writes ``truecolor_fixtures.json`` from :func:`tone` and the web
tests check their implementation against it.

Pipeline (``SPEC_VERSION`` 1), per pixel, input ρ = surface reflectance:

1. **Linear stretch** — ``t = clamp((ρ − lo) / (hi − lo), 0, 1)`` per channel.
   *linked* stretches share one window (keeps the scene's color balance);
   *dark-object* gives each band its own black point but one shared span
   (dark-object subtraction: removes the additive haze offset, keeps the
   balance); *per-channel* windows are independent (neutralise casts too).
2. **Gamma** — ``t = t ** (1 / γ)``; γ > 1 lifts the midtones (rio-color
   convention).
3. **Sigmoidal contrast** — rio-color's curve with strength β (0 = off) and
   midpoint α, normalized so 0 → 0 and 1 → 1.
4. **Saturation** — the result is read as sRGB-encoded, converted to OKLab,
   chroma (a, b) scaled by s, converted back and clipped to the sRGB gamut.

Invalid pixels (nodata, masked, NaN in any channel) are transparent.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import numpy as np
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

log = logging.getLogger(__name__)

SPEC_VERSION = 1
# Sigmoidal strengths below this are treated as off (the curve degenerates to
# 0/0 at β = 0); the GLSL module uses the same threshold.
CONTRAST_EPS = 1e-3
# Saturation within this of 1 skips the OKLab round trip entirely.
SATURATION_EPS = 1e-4
# Dark-object stretches must share hi − lo to within this (reflectance).
SPAN_TOL = 1e-6
# Percentiles the picker offers for auto stretches.
AUTO_LOW_PERCENTILES = (0.5, 1.0, 2.0, 5.0)
AUTO_HIGH_PERCENTILES = (95.0, 98.0, 99.0, 99.5)

Triple = tuple[float, float, float]


# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------

def _triple(v) -> Triple | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return (float(v),) * 3
    t = tuple(float(x) for x in v)
    if len(t) != 3:
        raise ValueError(f"expected one value or three (r, g, b), got {len(t)}")
    return t  # type: ignore[return-value]


class TrueColorStretch(BaseModel):
    """Black/white points in reflectance. ``source: auto`` re-derives them from
    the frame's own percentiles; ``manual`` keeps the numbers as given."""

    mode: Literal["linked", "dark-object", "per-channel"] = "linked"
    source: Literal["auto", "manual"] = "manual"
    p_lo: float = Field(2.0, ge=0.0, le=50.0)
    p_hi: float = Field(98.0, ge=50.0, le=100.0)
    # None until resolved (auto presets, hand-written configs); plates exported
    # from the picker always carry the resolved numbers.
    lo: Triple | None = None
    hi: Triple | None = None

    @field_validator("lo", "hi", mode="before")
    @classmethod
    def _as_triple(cls, v):
        return _triple(v)

    @model_validator(mode="after")
    def _check(self):
        if (self.lo is None) != (self.hi is None):
            raise ValueError("stretch needs both lo and hi, or neither")
        if self.source == "manual" and self.lo is None:
            raise ValueError("a manual stretch needs lo and hi")
        if self.lo is not None and self.hi is not None:
            if any(h <= l for l, h in zip(self.lo, self.hi)):
                raise ValueError(f"stretch hi must exceed lo in every channel (lo={self.lo}, hi={self.hi})")
            if self.mode == "linked" and (len(set(self.lo)) > 1 or len(set(self.hi)) > 1):
                raise ValueError("a linked stretch uses the same lo/hi for r, g and b")
            spans = [h - l for l, h in zip(self.lo, self.hi)]
            if self.mode == "dark-object" and max(spans) - min(spans) > SPAN_TOL:
                raise ValueError(f"a dark-object stretch shares one span (hi − lo) across r, g and b, got {spans}")
        return self

    @property
    def resolved(self) -> bool:
        return self.lo is not None


class TrueColorSpec(BaseModel):
    """Everything that turns three reflectance bands into a printed plate."""

    preset: str | None = None
    stretch: TrueColorStretch = Field(default_factory=lambda: TrueColorStretch(source="auto"))
    gamma: float = Field(1.0, ge=0.2, le=5.0)
    contrast: float = Field(0.0, ge=0.0, le=30.0)
    midpoint: float = Field(0.5, gt=0.0, lt=1.0)
    saturation: float = Field(1.0, ge=0.0, le=3.0)


class TrueColorPreset(TrueColorSpec):
    id: str
    label: str
    description: str = ""


class TrueColorPresets(BaseModel):
    version: int = SPEC_VERSION
    default: str
    presets: list[TrueColorPreset]

    @model_validator(mode="after")
    def _default_exists(self):
        ids = [p.id for p in self.presets]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate true-color preset ids: {ids}")
        if self.default not in ids:
            raise ValueError(f"default preset {self.default!r} is not one of {ids}")
        return self

    def get(self, preset_id: str) -> TrueColorPreset:
        for p in self.presets:
            if p.id == preset_id:
                return p
        raise KeyError(f"unknown true-color preset {preset_id!r}; known: {[p.id for p in self.presets]}")


def load_presets(path: Path) -> TrueColorPresets:
    return TrueColorPresets.model_validate(yaml.safe_load(path.read_text()))


def spec_from_preset(preset: TrueColorPreset) -> TrueColorSpec:
    return TrueColorSpec.model_validate(preset.model_dump(exclude={"id", "label", "description"}) | {"preset": preset.id})


# ---------------------------------------------------------------------------
# Tone steps (vectorised; float64)
# ---------------------------------------------------------------------------

def linear_stretch(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def apply_gamma(t: np.ndarray, gamma: float) -> np.ndarray:
    return np.power(t, 1.0 / gamma)


def sigmoidal(t: np.ndarray, contrast: float, midpoint: float) -> np.ndarray:
    """rio-color's sigmoidal contrast, normalized to fix 0 and 1."""
    if contrast < CONTRAST_EPS:
        return t
    b, a = contrast, midpoint
    lo = 1.0 / (1.0 + np.exp(b * a))
    hi = 1.0 / (1.0 + np.exp(b * (a - 1.0)))
    return np.clip((1.0 / (1.0 + np.exp(b * (a - t))) - lo) / (hi - lo), 0.0, 1.0)


# OKLab (Björn Ottosson, 2020). Mirrored in the GLSL module.
_LIN_TO_LMS = np.array([
    [0.4122214708, 0.5363325363, 0.0514459929],
    [0.2119034982, 0.6806995451, 0.1073969566],
    [0.0883024619, 0.2817188376, 0.6299787005],
])
_LMS_TO_LAB = np.array([
    [0.2104542553, 0.7936177850, -0.0040720468],
    [1.9779984951, -2.4285922050, 0.4505937099],
    [0.0259040371, 0.7827717662, -0.8086757660],
])
_LAB_TO_LMS = np.array([
    [1.0, 0.3963377774, 0.2158037573],
    [1.0, -0.1055613458, -0.0638541728],
    [1.0, -0.0894841775, -1.2914855480],
])
_LMS_TO_LIN = np.array([
    [4.0767416621, -3.3077115913, 0.2309699292],
    [-1.2684380046, 2.6097574011, -0.3413193965],
    [-0.0041960863, -0.7034186147, 1.7076147010],
])


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def saturate(rgb: np.ndarray, s: float) -> np.ndarray:
    """Scale OKLab chroma of sRGB-encoded ``rgb`` (..., 3) by ``s``."""
    if abs(s - 1.0) < SATURATION_EPS:
        return rgb
    lms = srgb_to_linear(rgb) @ _LIN_TO_LMS.T
    lab = np.cbrt(lms) @ _LMS_TO_LAB.T
    lab[..., 1:] *= s
    lin = (lab @ _LAB_TO_LMS.T) ** 3 @ _LMS_TO_LIN.T
    return linear_to_srgb(lin)


def tone(rho: np.ndarray, spec: TrueColorSpec) -> np.ndarray:
    """Reflectance (..., 3) → display sRGB in [0, 1] (..., 3). NaN propagates
    per pixel: a pixel with any NaN channel comes back all-NaN."""
    st = spec.stretch
    if not st.resolved:
        raise ValueError("stretch is not resolved; call resolve_stretch first")
    rho = np.asarray(rho, dtype="float64")
    bad = np.isnan(rho).any(axis=-1)
    t = np.empty_like(rho)
    for c in range(3):
        t[..., c] = linear_stretch(np.nan_to_num(rho[..., c]), st.lo[c], st.hi[c])
    t = apply_gamma(t, spec.gamma)
    t = sigmoidal(t, spec.contrast, spec.midpoint)
    t = saturate(t, spec.saturation)
    t[bad] = np.nan
    return t


def tone_curve(x: np.ndarray, spec: TrueColorSpec, channel: int = 0) -> np.ndarray:
    """Reflectance → display value for one channel, ignoring saturation (which
    mixes channels). For the diagnostic page's curve chart."""
    st = spec.stretch
    t = linear_stretch(np.asarray(x, dtype="float64"), st.lo[channel], st.hi[channel])
    return sigmoidal(apply_gamma(t, spec.gamma), spec.contrast, spec.midpoint)


# ---------------------------------------------------------------------------
# Stretch resolution
# ---------------------------------------------------------------------------

def channel_percentiles(band: np.ndarray, qs) -> list[float]:
    """numpy-linear percentiles over finite pixels (the web stats use the same)."""
    v = np.asarray(band, dtype="float64")
    v = v[np.isfinite(v)]
    if v.size == 0:
        raise ValueError("no valid pixels to resolve a true-color stretch")
    return [float(x) for x in np.percentile(v, list(qs))]


def resolve_stretch(rgb: tuple[np.ndarray, np.ndarray, np.ndarray], stretch: TrueColorStretch) -> TrueColorStretch:
    """Fill ``lo``/``hi`` for an auto stretch from the bands' own percentiles.

    Linked auto takes the widest window across channels (min of the low
    percentiles, max of the high ones), so no channel clips harder than its
    own percentile and the balance is kept. Dark-object auto keeps each
    band's low percentile as its black point (the haze offset) and gives all
    three the widest span, so gains stay equal.

    A stretch that already carries numbers passes through untouched, auto or
    not: plates from the picker arrive resolved against the frame as tuned,
    and the print must use exactly that window, not re-derive it from the
    full-resolution pixels.
    """
    if stretch.resolved:
        return stretch
    per = [channel_percentiles(b, (stretch.p_lo, stretch.p_hi)) for b in rgb]
    lo = tuple(p[0] for p in per)
    hi = tuple(p[1] for p in per)
    if stretch.mode == "linked":
        lo, hi = (min(lo),) * 3, (max(hi),) * 3
    elif stretch.mode == "dark-object":
        span = max(1e-4, *(h - l for l, h in zip(lo, hi)))
        hi = tuple(l + span for l in lo)
    hi = tuple(max(h, l + 1e-4) for l, h in zip(lo, hi))
    return stretch.model_copy(update={"lo": lo, "hi": hi})


def resolve_spec(rgb: tuple[np.ndarray, np.ndarray, np.ndarray], spec: TrueColorSpec) -> TrueColorSpec:
    return spec.model_copy(update={"stretch": resolve_stretch(rgb, spec.stretch)})


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def to_rgba8(t: np.ndarray) -> np.ndarray:
    """Display sRGB (H, W, 3) with NaN → RGBA8 with transparent invalid pixels."""
    bad = np.isnan(t).any(axis=-1)
    rgb = np.round(np.nan_to_num(t) * 255.0).clip(0, 255).astype("uint8")
    alpha = np.where(bad, 0, 255).astype("uint8")
    return np.concatenate([rgb, alpha[..., None]], axis=-1)


def apply_truecolor(r: np.ndarray, g: np.ndarray, b: np.ndarray, spec: TrueColorSpec) -> tuple[np.ndarray, TrueColorSpec]:
    """Reflectance bands (NaN = invalid) → (RGBA8 image, the spec as resolved)."""
    spec = resolve_spec((r, g, b), spec)
    return to_rgba8(tone(np.stack([r, g, b], axis=-1), spec)), spec


# ---------------------------------------------------------------------------
# Labels + stats for the printed page
# ---------------------------------------------------------------------------

def preset_label(preset_id: str | None, presets: TrueColorPresets | None = None) -> str:
    """Display name for a spec's preset (``custom`` when it has none)."""
    if not preset_id:
        return "custom"
    if presets is not None:
        try:
            return presets.get(preset_id).label
        except KeyError:
            pass
    return preset_id.replace("_", " ").capitalize()


def channel_stats(r: np.ndarray, g: np.ndarray, b: np.ndarray) -> dict:
    """Reflectance summary per channel over pixels valid in all three bands."""
    stack = np.stack([np.asarray(c, dtype="float64") for c in (r, g, b)], axis=-1)
    valid = np.isfinite(stack).all(axis=-1)
    n = int(valid.sum())
    out: dict = {"n_pixels": int(valid.size), "valid_fraction": n / valid.size if valid.size else 0.0}
    for c, key in enumerate(("red", "green", "blue")):
        v = stack[..., c][valid]
        if v.size == 0:
            out[key] = None
            continue
        p1, p50, p99 = np.percentile(v, [1, 50, 99])
        out[key] = {"p1": float(p1), "p50": float(p50), "p99": float(p99), "mean": float(v.mean())}
    return out
