from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from spectralcular.platforms import PLATFORMS
from spectralcular.truecolor import (
    TrueColorSpec,
    TrueColorStretch,
    apply_truecolor,
    load_presets,
    resolve_stretch,
    saturate,
    sigmoidal,
    spec_from_preset,
    to_rgba8,
    tone,
    tone_curve,
)

ROOT = Path(__file__).resolve().parents[1]


def _spec(lo=0.0, hi=1.0, **kw) -> TrueColorSpec:
    return TrueColorSpec(stretch=TrueColorStretch(source="manual", lo=lo, hi=hi,
                                                  mode=kw.pop("mode", "linked")), **kw)


def test_identity_spec_is_a_plain_clamp():
    rho = np.array([[0.0, 0.25, 0.5], [0.75, 1.0, 1.5], [-0.2, 0.1, 0.9]])
    np.testing.assert_allclose(tone(rho, _spec()), np.clip(rho, 0, 1), atol=1e-12)


def test_linear_stretch_maps_window_to_unit_range():
    out = tone(np.array([[0.05, 0.175, 0.30]]), _spec(0.05, 0.30))
    np.testing.assert_allclose(out, [[0.0, 0.5, 1.0]], atol=1e-12)


def test_per_channel_windows_apply_to_their_own_band():
    spec = _spec((0.0, 0.1, 0.2), (1.0, 0.6, 0.4), mode="per-channel")
    out = tone(np.array([[0.5, 0.35, 0.3]]), spec)
    np.testing.assert_allclose(out, [[0.5, 0.5, 0.5]], atol=1e-12)


def test_gamma_lifts_midtones_and_fixes_ends():
    out = tone(np.array([[0.0, 0.25, 1.0]]), _spec(gamma=2.0))
    np.testing.assert_allclose(out, [[0.0, 0.5, 1.0]], atol=1e-12)


def test_sigmoidal_fixes_ends_is_monotonic_and_steepens_the_midpoint():
    t = np.linspace(0, 1, 101)
    s = sigmoidal(t, 10.0, 0.5)
    assert s[0] == pytest.approx(0.0, abs=1e-12) and s[-1] == pytest.approx(1.0, abs=1e-12)
    assert np.all(np.diff(s) > 0)
    assert s[50] == pytest.approx(0.5, abs=1e-9)
    assert s[40] < 0.4 and s[60] > 0.6
    np.testing.assert_array_equal(sigmoidal(t, 0.0, 0.3), t)


def test_saturation_zero_is_grey_and_one_is_identity():
    rgb = np.array([[0.8, 0.3, 0.1], [0.2, 0.5, 0.7]])
    np.testing.assert_array_equal(saturate(rgb, 1.0), rgb)
    gray = saturate(rgb, 0.0)
    np.testing.assert_allclose(gray[:, 0], gray[:, 1], atol=1e-6)
    np.testing.assert_allclose(gray[:, 1], gray[:, 2], atol=1e-6)
    # Neutral pixels stay neutral whatever the saturation.
    np.testing.assert_allclose(saturate(np.array([[0.4, 0.4, 0.4]]), 1.8), [[0.4, 0.4, 0.4]], atol=1e-6)


def test_saturation_boost_increases_spread_and_stays_in_gamut():
    rgb = np.array([[0.6, 0.45, 0.3]])
    out = saturate(rgb, 2.5)
    assert np.ptp(out) > np.ptp(rgb)
    assert out.min() >= 0.0 and out.max() <= 1.0


def test_nan_in_any_channel_makes_the_pixel_transparent():
    rgba = to_rgba8(tone(np.array([[[0.1, np.nan, 0.2], [0.1, 0.1, 0.1]]]), _spec()))
    assert rgba.shape == (1, 2, 4)
    assert rgba[0, 0, 3] == 0 and rgba[0, 1, 3] == 255


def test_linked_auto_takes_the_widest_window():
    rng = np.random.default_rng(0)
    r, g, b = rng.uniform(0.0, 0.2, 1000), rng.uniform(0.05, 0.4, 1000), rng.uniform(0.1, 0.3, 1000)
    st = resolve_stretch((r, g, b), TrueColorStretch(mode="linked", source="auto", p_lo=2, p_hi=98))
    assert st.lo == (pytest.approx(np.percentile(r, 2)),) * 3
    assert st.hi == (pytest.approx(np.percentile(g, 98)),) * 3
    per = resolve_stretch((r, g, b), TrueColorStretch(mode="per-channel", source="auto", p_lo=2, p_hi=98))
    assert per.lo[2] == pytest.approx(np.percentile(b, 2))
    assert per.hi[0] == pytest.approx(np.percentile(r, 98))


def test_manual_stretch_is_not_recomputed():
    st = TrueColorStretch(source="manual", lo=0.0, hi=0.3)
    assert resolve_stretch((np.ones(4), np.ones(4), np.ones(4)), st) is st


def test_auto_stretch_ignores_nan_pixels():
    r = np.array([np.nan, 0.1, 0.2, 0.3])
    st = resolve_stretch((r, r, r), TrueColorStretch(source="auto", p_lo=0.5, p_hi=99.5))
    assert np.isfinite(st.lo[0]) and np.isfinite(st.hi[0])


def test_stretch_validation():
    with pytest.raises(ValidationError, match="exceed"):
        TrueColorStretch(source="manual", lo=0.3, hi=0.1)
    with pytest.raises(ValidationError, match="linked"):
        TrueColorStretch(mode="linked", source="manual", lo=(0, 0.1, 0), hi=0.5)
    with pytest.raises(ValidationError, match="manual"):
        TrueColorStretch(source="manual")
    assert TrueColorStretch(source="manual", lo=0, hi=0.3).lo == (0.0, 0.0, 0.0)


def test_apply_truecolor_resolves_and_renders():
    rng = np.random.default_rng(1)
    bands = [rng.uniform(0.0, 0.3, (8, 6)) for _ in range(3)]
    bands[0][0, 0] = np.nan
    img, spec = apply_truecolor(*bands, TrueColorSpec(stretch=TrueColorStretch(source="auto")))
    assert img.shape == (8, 6, 4) and img.dtype == np.uint8
    assert spec.stretch.resolved
    assert img[0, 0, 3] == 0 and (img[..., 3] == 255).sum() == 47


def test_tone_curve_matches_tone_without_saturation():
    spec = _spec(0.02, 0.4, gamma=1.6, contrast=5, midpoint=0.4)
    x = np.linspace(0, 0.5, 11)
    rho = np.stack([x, x, x], axis=-1)
    np.testing.assert_allclose(tone_curve(x, spec), tone(rho, spec)[:, 0], atol=1e-12)


def test_shipped_presets_load_and_resolve():
    presets = load_presets(ROOT / "config" / "truecolor.yaml")
    assert presets.get(presets.default)
    rng = np.random.default_rng(2)
    bands = tuple(rng.uniform(0.0, 0.5, 500) for _ in range(3))
    for p in presets.presets:
        spec = spec_from_preset(p)
        assert spec.preset == p.id
        img, resolved = apply_truecolor(*bands, spec)
        assert resolved.stretch.resolved and img.shape == (500, 4)


def test_optical_platforms_name_rgb_bands_and_radar_does_not():
    assert PLATFORMS["sentinel-2-l2a"].rgb_assets == ("red", "green", "blue")
    assert PLATFORMS["landsat-c2-l2"].rgb_assets == ("red", "green", "blue")
    assert PLATFORMS["sentinel-1-rtc"].rgb_assets is None


def test_dark_object_keeps_per_band_black_points_and_one_gain():
    rng = np.random.default_rng(3)
    # blue carries a haze offset but less dynamic range than red
    r, g, b = rng.uniform(0.01, 0.35, 2000), rng.uniform(0.03, 0.3, 2000), rng.uniform(0.08, 0.22, 2000)
    st = resolve_stretch((r, g, b), TrueColorStretch(mode="dark-object", source="auto", p_lo=1, p_hi=99.5))
    assert st.lo[2] == pytest.approx(np.percentile(b, 1))
    spans = [h - l for l, h in zip(st.lo, st.hi)]
    assert max(spans) - min(spans) < 1e-9
    assert spans[0] == pytest.approx(np.percentile(r, 99.5) - np.percentile(r, 1))
    # per-channel would blow blue up relative to red; dark-object does not
    per = resolve_stretch((r, g, b), TrueColorStretch(mode="per-channel", source="auto", p_lo=1, p_hi=99.5))
    assert (per.hi[2] - per.lo[2]) < spans[2]


def test_dark_object_requires_a_shared_span():
    with pytest.raises(ValidationError, match="span"):
        TrueColorStretch(mode="dark-object", source="manual", lo=(0.0, 0.02, 0.05), hi=(0.3, 0.3, 0.3))
    TrueColorStretch(mode="dark-object", source="manual", lo=(0.0, 0.02, 0.05), hi=(0.3, 0.32, 0.35))


def test_resolved_auto_stretch_is_kept_as_tuned():
    # The picker resolves auto windows on its frame stats; print must not redo it.
    st = TrueColorStretch(mode="dark-object", source="auto", p_lo=1, p_hi=99.5,
                          lo=(0.02, 0.03, 0.06), hi=(0.32, 0.33, 0.36))
    rng = np.random.default_rng(4)
    assert resolve_stretch(tuple(rng.uniform(0, 0.5, 100) for _ in range(3)), st) is st
