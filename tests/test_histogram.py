"""The specimen card's histogram: a window robust to outliers, tick labels that
never repeat, and the tails counted at the edges."""

from __future__ import annotations

import numpy as np
import pytest

from spectralcular.render_assets import histogram_window, nice_ticks


def _labels(lo: float, hi: float) -> list[str]:
    ticks, decimals = nice_ticks(lo, hi)
    return [f"{v:.{decimals}f}" for v in ticks]


@pytest.mark.parametrize("lo,hi", [(0.08, 0.33), (-0.21, 0.95), (0.001, 0.0042), (12.3, 980), (-1, 1), (0.41, 0.57)])
def test_tick_labels_are_distinct_and_evenly_stepped(lo, hi):
    labels = _labels(lo, hi)
    assert len(labels) >= 2
    assert len(set(labels)) == len(labels)              # no "0.2, 0.2"
    values = np.array([float(x) for x in labels])
    steps = np.diff(values)
    assert np.allclose(steps, steps[0])                  # no "0.2, 0.4, 0.5, 0.8"
    mantissa = steps[0] / 10 ** np.floor(np.log10(steps[0]))
    assert any(np.isclose(mantissa, m) for m in (1, 2, 2.5, 5))


def test_extreme_pixels_do_not_set_the_window():
    rng = np.random.default_rng(0)
    bulk = rng.normal(0.3, 0.1, 100_000)
    vals = np.concatenate([bulk, [3000.0, -9000.0]])   # an index blowing up near a zero denominator
    lo, hi = histogram_window(vals)
    assert -0.2 < lo < 0.0 and 0.6 < hi < 0.8


def test_a_heavy_tail_is_fenced_off():
    # 95 % compact, 5 % strung out to 100 — p99 alone would sit far up the tail
    vals = np.concatenate([np.linspace(0, 1, 9500), np.linspace(2, 100, 500)])
    lo, hi = histogram_window(vals)
    assert lo <= 0 and hi < 4


def test_bimodal_scene_keeps_both_modes():
    rng = np.random.default_rng(1)
    vals = np.concatenate([rng.normal(-0.6, 0.05, 40_000), rng.normal(0.3, 0.1, 60_000)])
    lo, hi = histogram_window(vals)
    assert lo < -0.65 and hi > 0.45


def test_window_stays_inside_the_domain_and_survives_flat_data():
    lo, hi = histogram_window(np.random.default_rng(2).normal(0.9, 0.2, 10_000), (-1.0, 1.0))
    assert hi == 1.0
    lo, hi = histogram_window(np.full(100, 0.5))
    assert lo < 0.5 < hi


def test_one_or_two_bar_spikes_are_capped_at_twice_the_rest():
    from spectralcular.render_assets import histogram_ceiling
    base = np.array([10, 40, 60, 50, 30, 20, 5])
    one = base.copy()
    one[2] = 400                                        # one bar > 2 × every other
    assert histogram_ceiling(one) == 2 * 50
    two = base.copy()
    two[1], two[2] = 300, 400                           # two bars, both > 2 × the rest
    assert histogram_ceiling(two) == 2 * 50
    three = base.copy()
    three[1:4] = 400                                    # three tall bars: a real mode, not a spike
    assert histogram_ceiling(three) is None


def test_peaks_within_twice_the_next_bar_are_drawn_full_height():
    from spectralcular.render_assets import histogram_ceiling
    rng = np.random.default_rng(3)
    single = np.histogram(rng.normal(0.4, 0.1, 100_000), bins=120, range=(0, 0.8))[0]
    assert histogram_ceiling(single) is None
    counts = np.histogram(rng.uniform(-1, 0.3, 40_000), bins=120, range=(-1, 0.3))[0]
    counts[60:70] += 30_000                             # a wide mode (deep water over ten bars)
    assert histogram_ceiling(counts) is None


def test_a_second_class_is_never_hidden():
    # a fifth of the scene is water far below the land mode: it stays on the chart
    rng = np.random.default_rng(5)
    vals = np.concatenate([rng.normal(0.6, 0.05, 80_000), rng.normal(-0.4, 0.05, 20_000)])
    lo, hi = histogram_window(vals)
    assert (vals < lo).mean() <= 0.05 and (vals > hi).mean() <= 0.05
