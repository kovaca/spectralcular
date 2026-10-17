"""resolve_colormap: palettize presets, catalog rows, inline colors, reversal."""

from __future__ import annotations

import numpy as np
import pytest
from matplotlib.colors import to_hex

from spectralcular.colormaps import check_resolvable, colormap_short_name, resolve_colormap


def _ends(cm):
    return to_hex(cm.matplotlib(0.0)).lower(), to_hex(cm.matplotlib(1.0)).lower()


def test_viridis_endpoints_round_trip():
    cm = resolve_colormap("bids:viridis", n_samples=256)
    assert cm.source == "palettize"
    pal_first = cm.palettize.get_color(0.0).lower()
    pal_last = cm.palettize.get_color(1.0).lower()
    assert _ends(cm) == (pal_first, pal_last)


def test_catalog_row_matches_site_exactly():
    cm = resolve_colormap("colorcurious:matplotlib.viridis")
    assert cm.source == "colorcurious"
    assert _ends(cm) == ("#440154", "#fde725")
    assert cm.short_name == "viridis"


def test_reversed_flag_and_r_suffix_agree():
    flag = resolve_colormap("colorcurious:matplotlib.viridis", reversed=True)
    suffix = resolve_colormap("colorcurious:matplotlib.viridis_r")
    assert _ends(flag) == ("#fde725", "#440154") == _ends(suffix)
    assert flag.reversed and suffix.reversed
    assert flag.short_name == "viridis_r"
    # flag on an already-suffixed name flips back (XOR)
    assert _ends(resolve_colormap("colorcurious:matplotlib.viridis_r", reversed=True)) == ("#440154", "#fde725")


def test_reversed_palettize_preset():
    fwd, rev = resolve_colormap("bids:viridis"), resolve_colormap("bids:viridis", reversed=True)
    assert _ends(rev) == tuple(reversed(_ends(fwd)))
    assert rev.short_name == "viridis_r"


def test_inline_colors_interpolate_in_srgb():
    cm = resolve_colormap("custom:bw", colors=["#000000", "#ffffff"], n_samples=3)
    assert cm.source == "inline"
    mid = cm.matplotlib(0.5)
    assert np.allclose(mid[:3], 0.5, atol=0.01)


def test_entry_like_object_supplies_colors_and_reversed():
    class Entry:
        name = "custom:bw"
        colors = ["#000000", "#ffffff"]
        reversed = True

    cm = resolve_colormap(Entry())
    assert _ends(cm) == ("#ffffff", "#000000")


def test_nan_renders_transparent():
    for name in ("bids:viridis", "colorcurious:crameri.roma"):
        cm = resolve_colormap(name, n_samples=64)
        rgba = cm.matplotlib(np.array([np.nan]))
        assert rgba.shape == (1, 4) and rgba[0, 3] == 0.0


def test_unknown_names_raise():
    with pytest.raises(ValueError):
        resolve_colormap("nope:zzz")
    with pytest.raises(ValueError):
        check_resolvable("colorcurious:nope.zzz")
    with pytest.raises(ValueError):
        check_resolvable("custom:x")  # reserved namespace without colors
    with pytest.raises(ValueError):
        check_resolvable("custom:x", colors=["#000"])  # one stop


def test_check_resolvable_accepts_all_three_kinds():
    check_resolvable("bids:viridis")
    check_resolvable("colorcurious:crameri.roma_r")
    check_resolvable("custom:x", colors=["#000", "#fff"])


def test_short_name():
    assert colormap_short_name("bids:viridis") == "viridis"
    assert colormap_short_name("colorcurious:crameri.roma") == "roma"
    assert colormap_short_name("cmocean:algae_r", reversed=True) == "algae_r"
    assert colormap_short_name("bids:viridis", reversed=True) == "viridis_r"
