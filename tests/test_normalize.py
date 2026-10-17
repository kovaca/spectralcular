"""Normalization unit tests."""

from __future__ import annotations

import numpy as np

from spectralcular.config import Normalization
from spectralcular.normalize import resolve_norm


def test_percentile_clips_to_inner_range():
    arr = np.linspace(-1, 1, 1001).astype("float32")
    norm = resolve_norm(arr, Normalization(method="percentile", lower=2, upper=98))
    assert np.isclose(norm.vmin, np.percentile(arr, 2), atol=1e-6)
    assert np.isclose(norm.vmax, np.percentile(arr, 98), atol=1e-6)


def test_fixed_bounds_passthrough():
    arr = np.array([0.1, 0.2, 0.9], dtype="float32")
    norm = resolve_norm(arr, Normalization(method="fixed", vmin=-1, vmax=1))
    assert norm.vmin == -1.0
    assert norm.vmax == 1.0


def test_diverging_uses_max_abs():
    arr = np.array([-0.4, 0.1, 0.9, np.nan], dtype="float32")
    norm = resolve_norm(arr, Normalization(method="diverging"))
    assert np.isclose(norm.vmin, -0.9)
    assert np.isclose(norm.vmax, 0.9)


def test_normalize_clips_to_unit_range():
    arr = np.array([-2.0, 0.0, 0.5, 2.0], dtype="float32")
    norm = resolve_norm(arr, Normalization(method="fixed", vmin=0, vmax=1))
    out = norm.normalize(arr)
    assert out.min() == 0.0 and out.max() == 1.0
