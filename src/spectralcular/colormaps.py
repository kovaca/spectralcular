"""Resolve a colormap name (or pool/config entry) into matplotlib.

Three kinds of source are supported through one entry point,
:func:`resolve_colormap`:

* **palettize presets** — ``"bids:viridis"``, ``"cmocean:algae_r"``; resolved
  by palettize (which in turn reaches the ``cmap`` catalog for ``ns:name``).
* **colorcurious catalog rows** — ``"colorcurious:crameri.roma"`` (+ optional
  ``_r``); colors are decoded verbatim from the vendored sprite so they match
  colorcurio.us exactly (8-bit sRGB, which is all print needs).
* **inline colors** — any name plus ``colors=[hex, ...]``; interpolated in
  sRGB by palettize. This is the "paste your own colormap" escape hatch.

``reversed=True`` flips whichever of the above was resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

import numpy as np
from matplotlib.colors import ListedColormap, to_rgba
from palettize import Colormap as PalColormap
from palettize import create_colormap

from . import catalog as _catalog

ColormapSourceKind = Literal["palettize", "colorcurious", "inline"]
CUSTOM_NAMESPACE = "custom"  # reserved for inline-colour pool entries


def colormap_short_name(name: str, *, reversed: bool = False) -> str:
    """``"bids:viridis" → "viridis"``; ``"colorcurious:crameri.roma" → "roma"``.

    Appends ``_r`` when ``reversed`` and the name does not already carry it.
    """
    short = name.split(":")[-1]
    if _catalog.is_catalog_name(name):
        short = short.split(".", 1)[-1]
    if reversed and not short.endswith("_r"):
        short += "_r"
    return short


@dataclass
class ResolvedColormap:
    name: str
    matplotlib: ListedColormap
    n_samples: int
    reversed: bool = False
    source: ColormapSourceKind = "palettize"
    _palettize: PalColormap | None = field(default=None, repr=False)
    _stops_hex: list[str] | None = field(default=None, repr=False)

    @property
    def palettize(self) -> PalColormap:
        """The palettize object (built lazily for catalog/inline sources)."""
        if self._palettize is None:
            stops = self._stops_hex or []
            if self.reversed:
                stops = stops[::-1]
            self._palettize = create_colormap(colors=stops, interpolation_space="srgb", name=self.name)
        return self._palettize

    @property
    def short_name(self) -> str:
        return colormap_short_name(self.name, reversed=self.reversed)


def _sample(pal: PalColormap, n_samples: int) -> np.ndarray:
    ts = np.linspace(0.0, 1.0, n_samples)
    return np.array([to_rgba(pal.get_color(float(t))) for t in ts])


def _finish(name: str, rgba: np.ndarray, *, reversed: bool) -> ListedColormap:
    if reversed:
        rgba = rgba[::-1].copy()
    display = colormap_short_name(name, reversed=reversed) if reversed else name
    mpl_cmap = ListedColormap(rgba, name=display)
    mpl_cmap.set_bad(color=(0, 0, 0, 0))  # NaN → transparent
    return mpl_cmap


def resolve_colormap(
    spec: str | Any,
    *,
    reversed: bool = False,
    colors: Sequence[str] | None = None,
    n_samples: int = 256,
) -> ResolvedColormap:
    """Resolve ``spec`` (a name, or any object with ``.name`` and optional
    ``.colors`` / ``.reversed``) into a :class:`ResolvedColormap`.

    A ``.reversed`` attribute on ``spec`` and the ``reversed`` argument combine
    with XOR, as does a ``_r`` suffix on a catalog name.
    """
    if isinstance(spec, str):
        name = spec
    else:
        name = spec.name
        if colors is None:
            colors = getattr(spec, "colors", None)
        reversed = bool(reversed) != bool(getattr(spec, "reversed", False))

    if colors:
        stops = [str(c) for c in colors]
        if len(stops) < 2:
            raise ValueError(f"colormap {name!r}: inline colors need at least 2 stops, got {len(stops)}")
        pal = create_colormap(colors=stops, interpolation_space="srgb", name=name)
        rgba = _sample(pal, n_samples)
        return ResolvedColormap(name=name, matplotlib=_finish(name, rgba, reversed=reversed),
                                n_samples=n_samples, reversed=reversed, source="inline",
                                _palettize=None if reversed else pal, _stops_hex=stops)

    if _catalog.is_catalog_name(name):
        cid, suffix_rev = _catalog.split_name(name)
        reversed = bool(reversed) != suffix_rev
        cat = _catalog.load_catalog()
        rec = cat.get(cid)
        stops_u8 = _catalog.decode_stops(cat, rec)
        stops = [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in stops_u8.tolist()]
        if rec.stops_count == n_samples:
            rgb = stops_u8.astype("float64") / 255.0
            rgba = np.concatenate([rgb, np.ones((len(rgb), 1))], axis=1)
        else:
            rgba = _sample(create_colormap(colors=stops, interpolation_space="srgb", name=name), n_samples)
        return ResolvedColormap(name=name, matplotlib=_finish(name, rgba, reversed=reversed),
                                n_samples=n_samples, reversed=reversed, source="colorcurious",
                                _stops_hex=stops)

    try:
        pal = create_colormap(preset=name, name=name)
    except Exception as e:  # noqa: BLE001 — surface palettize's message verbatim
        raise ValueError(f"palettize cannot resolve preset {name!r}: {e}") from e
    rgba = _sample(pal, n_samples)
    return ResolvedColormap(name=name, matplotlib=_finish(name, rgba, reversed=reversed),
                            n_samples=n_samples, reversed=reversed, source="palettize",
                            _palettize=None if reversed else pal,
                            _stops_hex=None if not reversed else [pal.get_color(float(t)) for t in np.linspace(0, 1, 32)])


def check_resolvable(name: str, *, colors: Sequence[str] | None = None) -> None:
    """Raise ``ValueError`` unless ``name`` (with optional inline colors) resolves.

    Cheap: catalog names are only looked up (no color decoding); palettize
    presets are constructed but sampled at 8 points.
    """
    if colors:
        if len(colors) < 2:
            raise ValueError(f"colormap {name!r}: inline colors need at least 2 stops")
        from matplotlib.colors import is_color_like
        bad = [c for c in colors if not is_color_like(c)]
        if bad:
            raise ValueError(f"colormap {name!r}: unparseable colors {bad[:3]}")
        return
    if name.startswith(CUSTOM_NAMESPACE + ":"):
        raise ValueError(f"colormap {name!r} is in the reserved {CUSTOM_NAMESPACE}: namespace but has no inline colors")
    if _catalog.is_catalog_name(name):
        cid, _ = _catalog.split_name(name)
        try:
            _catalog.load_catalog().get(cid)
        except _catalog.CatalogError as e:
            raise ValueError(str(e)) from e
        return
    try:
        create_colormap(preset=name, name=name)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"palettize cannot resolve preset {name!r}: {e}") from e
