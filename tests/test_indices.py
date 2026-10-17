"""Sanity-check that the spyndex wrapper computes NDVI per the textbook formula.

Doesn't touch STAC — operates on hand-built arrays via spyndex directly.
"""

from __future__ import annotations

import numpy as np
import spyndex


def test_ndvi_matches_formula():
    rng = np.random.default_rng(42)
    nir = rng.uniform(0.05, 0.6, size=(8, 8)).astype("float32")
    red = rng.uniform(0.02, 0.3, size=(8, 8)).astype("float32")

    expected = (nir - red) / (nir + red)
    got = np.asarray(spyndex.computeIndex(index="NDVI", params={"N": nir, "R": red}))

    assert got.shape == expected.shape
    np.testing.assert_allclose(got, expected, rtol=1e-6, atol=1e-7)
