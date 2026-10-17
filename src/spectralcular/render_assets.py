"""Render image + chart assets that the HTML template references.

These produce on-disk PNG / SVG files in ``assets/``. The HTML/CSS layer
(``book.py``) is responsible for layout, typography, and final paging.
"""

from __future__ import annotations

import io
import json
import logging
import math
import urllib.request
from functools import lru_cache
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
from matplotlib.colors import to_hex, to_rgb
from matplotlib.figure import Figure
from matplotlib.patches import Circle as MplCircle
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.ticker import FuncFormatter
from mpl_toolkits.axes_grid1.inset_locator import inset_axes
from PIL import Image, ImageDraw

from .colormaps import ResolvedColormap
from .normalize import NormResult

log = logging.getLogger(__name__)

NE_LAND_GEOJSON = Path(__file__).resolve().parent / "_data" / "ne_110m_land.geojson"

# Editorial palette — kept consistent with the HTML template.
INK = "#1a1a1a"
RULE = "#cfcdc7"
PAPER = "#f7f4ec"
ACCENT = "#9a3324"
INK_SOFT_HEX = "#4a4845"

# Charts are embedded in the print PDF, which must carry no transparency
# (Lulu: "transparent layers ... are flattened"). So: no alpha on any artist —
# translucent fills are pre-blended over what they sit on (_blend) — figure
# and inset patches are hidden rather than set to alpha 0, and rasters inside
# an SVG are written at print resolution.
SVG_RASTER_DPI = 300


def _blend(color: str, alpha: float, over: str = PAPER) -> str:
    """The opaque color ``color`` at ``alpha`` shows over ``over``."""
    c, o = np.asarray(to_rgb(color)), np.asarray(to_rgb(over))
    return to_hex(alpha * c + (1 - alpha) * o)

MAX_PNG_LONG_EDGE = 3600  # US Letter portrait + bleed at 300 DPI = 11.25" × 300 = 3375 px



def render_index_png(
    index_da: "xr.DataArray | np.ndarray",
    cmap: ResolvedColormap,
    norm: NormResult,
    out_png: Path,
) -> Path:
    """Apply the colormap to the normalized index and save as RGBA PNG.

    Pure data-to-pixels — no titles, ticks, frames, or text. Composition
    happens in CSS. Accepts a DataArray or a bare 2-D array (page-warped).
    """
    arr = np.asarray(getattr(index_da, "values", index_da), dtype="float32")
    h, w = arr.shape
    long_edge = max(h, w)
    if long_edge > MAX_PNG_LONG_EDGE:
        scale = MAX_PNG_LONG_EDGE / long_edge
        new_h, new_w = int(h * scale), int(w * scale)
        # Coarse downsample via PIL on a temporary float image.
        tmp = Image.fromarray(np.where(np.isfinite(arr), arr, np.nan), mode="F")
        tmp = tmp.resize((new_w, new_h), Image.Resampling.BILINEAR)
        arr = np.asarray(tmp)

    normed = (arr - norm.vmin) / (norm.vmax - norm.vmin)
    normed = np.clip(normed, 0.0, 1.0)
    rgba = cmap.matplotlib(normed, bytes=True)  # (H, W, 4) uint8

    # Restore transparency where the underlying value was NaN.
    nan_mask = ~np.isfinite(arr)
    rgba[nan_mask] = (0, 0, 0, 0)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(out_png, optimize=True)
    log.info("Wrote index PNG (%dx%d) → %s", rgba.shape[1], rgba.shape[0], out_png)
    return out_png


def _save_svg(fig: Figure, out_path: Path, *, tight: bool = True) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    if tight:
        fig.savefig(buf, format="svg", bbox_inches="tight", transparent=True, pad_inches=0.02,
                    dpi=SVG_RASTER_DPI)
    else:
        # Preserve the figure's own subplots_adjust so bars span its full width.
        fig.savefig(buf, format="svg", transparent=True, dpi=SVG_RASTER_DPI)
    out_path.write_bytes(buf.getvalue())
    plt.close(fig)
    return out_path


def histogram_window(values: np.ndarray, domain: tuple[float, float] | None = None) -> tuple[float, float]:
    """The x-range a histogram bins, robust to outliers:

    * p1–p99 widened by a quarter of that width on each side,
    * tightened to Tukey's outer fences, Q1 − 3·IQR … Q3 + 3·IQR, so a long
      thin tail holding more than 1 % of the pixels (MI's ratio blow-ups,
      SR's dark water) doesn't squash the bulk into a few bars,
    * but never hiding more than 5 % of the scene per side (it always reaches
      p5 and p95): a second class — water beside land, a burn scar, ice — is
      part of the scene, not an outlier,
    * never past the data or the index's ``domain``.

    Compact and bimodal distributions keep their whole span (their fences lie
    beyond p1/p99). Mirrors the picker's ``histogramWindow``
    (web/src/lib/raster/stats/percentiles.ts)."""
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return (domain or (0.0, 1.0))
    p1, p5, q1, p50, q3, p95, p99 = np.percentile(finite, [1, 5, 25, 50, 75, 95, 99])
    lo_d, hi_d = float(finite.min()), float(finite.max())
    w = p99 - p1
    lo = max(lo_d, p1 - 0.25 * w) if w > 0 else lo_d
    hi = min(hi_d, p99 + 0.25 * w) if w > 0 else hi_d
    iqr = q3 - q1
    if iqr > 0:
        lo, hi = max(lo, min(q1 - 3 * iqr, p5)), min(hi, max(q3 + 3 * iqr, p95))
    if domain is not None:
        lo, hi = max(lo, domain[0]), min(hi, domain[1])
    if not hi > lo:
        pad = max(abs(p50) * 0.05, 1e-3)
        return float(p50 - pad), float(p50 + pad)
    return float(lo), float(hi)


def nice_ticks(lo: float, hi: float, n: int = 6) -> tuple[np.ndarray, int]:
    """Tick positions on 1 / 2 / 2.5 / 5 × 10ⁿ steps inside ``[lo, hi]`` and the
    decimals that print every one of them distinctly (no 0.1, 0.2, 0.2, 0.3)."""
    from matplotlib.ticker import MaxNLocator
    ticks = MaxNLocator(nbins=n, steps=[1, 2, 2.5, 5, 10]).tick_values(lo, hi)
    ticks = ticks[(ticks >= lo - 1e-12) & (ticks <= hi + 1e-12)]
    if len(ticks) < 2:
        return np.array([lo, hi]), 3
    step = float(np.min(np.diff(ticks)))
    decimals = 0
    while decimals < 6 and abs(round(step, decimals) - step) > step * 1e-6:
        decimals += 1
    return ticks, decimals


def _tick_formatter(decimals: int) -> FuncFormatter:
    def fmt(v: float, _p: int) -> str:
        t = f"{v:.{decimals}f}"
        return t.lstrip("-") if float(t) == 0 else t   # no "-0.0"
    return FuncFormatter(fmt)


def histogram_ceiling(counts: np.ndarray, factor: float = 2.0) -> float | None:
    """A broken-scale ceiling for a spike of one or two bars, or None.

    When the tallest bar is more than ``factor`` × every other bar — or the
    two tallest are both more than ``factor`` × every remaining bar — those
    one or two bars are drawn at ``factor`` × the tallest remaining bar, with
    a break marker at the top, so the rest of the distribution keeps its
    height. Anything else is drawn at full height."""
    h = np.sort(counts[counts > 0])[::-1]
    for k in (1, 2):
        if h.size > k and h[k] > 0 and h[k - 1] > factor * h[k]:
            return factor * float(h[k])
    return None


def _share(n: int, total: int) -> str:
    pct = 100 * n / total
    return "<0.1%" if pct < 0.1 else f"{pct:.1f}%"


def render_histogram_svg(
    values: np.ndarray,
    norm: NormResult,
    cmap: ResolvedColormap,
    out_svg: Path,
    *,
    bins: int = 120,
    display_range: tuple[float, float] | None = None,

) -> Path:
    """Wide-format histogram. Visually mirrors the gradient band's full-page width.

    Bins span :func:`histogram_window` (robust, inside the index's natural
    ``display_range``), so the bulk of the scene fills the chart; pixels
    outside the window are counted, not drawn — the share below and above is
    printed at each edge. Bars inside the colormap range take its color, bars
    outside it a muted ink; dashed lines mark the colormap's ends, and an edge
    note says where the ramp continues when an end lies outside the window.
    """
    finite = values[np.isfinite(values)]
    lo, hi = histogram_window(finite, display_range)
    counts, edges = np.histogram(finite, bins=bins, range=(lo, hi))
    centers = 0.5 * (edges[:-1] + edges[1:])
    width = edges[1] - edges[0]
    below, above = int((finite < lo).sum()), int((finite > hi).sum())

    norm_centers = (centers - norm.vmin) / (norm.vmax - norm.vmin)
    in_range = (norm_centers >= 0.0) & (norm_centers <= 1.0)
    colors = np.empty((len(centers), 4))
    colors[in_range] = cmap.matplotlib(np.clip(norm_centers[in_range], 0, 1))
    # Outside the colormap range: muted ink so they read as "tail".
    colors[~in_range] = (0.29, 0.28, 0.26, 1.0)

    fig, ax = plt.subplots(figsize=(10.0, 2.0))
    fig.patch.set_visible(False)
    ceiling = histogram_ceiling(counts)
    top = ceiling if ceiling is not None else max(1, counts.max())
    ax.bar(centers, np.minimum(counts, top), width=width, color=colors, edgecolor="none", align="center")
    if ceiling is not None:
        # Broken scale: a "//" cut near the top of each capped bar, plus a short
        # ink tick above it marking where the scale stops.
        for i in np.flatnonzero(counts > ceiling):
            x = centers[i]
            r, g, b = colors[i][:3]
            if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.75:
                # A near-paper colormap end (GnBu, ice …): outline the bar so it
                # and its break read against the page.
                ax.bar([x], [top], width=width, fill=False, edgecolor=INK_SOFT_HEX, lw=0.6, zorder=2.5)
            for y in (0.86, 0.91):
                ax.plot([x - width * 0.9, x + width * 0.9], [top * (y - 0.03), top * (y + 0.03)],
                        color=PAPER, lw=1.4, solid_capstyle="butt", zorder=3)
            ax.plot([x - width * 0.7, x + width * 0.7], [top * 1.02] * 2, color=INK, lw=0.6, zorder=3)
    ax.set_xlim(lo, hi)
    ax.set_ylim(0, top * 1.22)  # headroom for the break tick and the edge notes

    for v in (norm.vmin, norm.vmax):
        if lo <= v <= hi:
            ax.axvline(v, color=INK, lw=0.5, ls=(0, (2, 2)))

    ticks, decimals = nice_ticks(lo, hi)
    ax.set_xticks(ticks)
    ax.xaxis.set_major_formatter(_tick_formatter(decimals))
    cut = _tick_formatter(max(decimals, 2))

    notes_l, notes_r = [], []
    if below:
        notes_l.append(f"◂ {_share(below, finite.size)} below {cut(lo, 0)}")
    if above:
        notes_r.append(f"{_share(above, finite.size)} above {cut(hi, 0)} ▸")
    if norm.vmin < lo:
        notes_l.append(f"◂ ramp from {cut(norm.vmin, 0)}")
    if norm.vmax > hi:
        notes_r.append(f"ramp to {cut(norm.vmax, 0)} ▸")
    # The 10 in figure prints 150 mm wide (×0.59): 11 pt here ≈ 6.5 pt on paper.
    note = dict(transform=ax.transAxes, va="top", fontsize=11, color=INK, family="DejaVu Sans Mono")
    if notes_l:
        ax.text(0.0, 1.0, "   ".join(notes_l), ha="left", **note)
    if notes_r:
        ax.text(1.0, 1.0, "   ".join(notes_r), ha="right", **note)

    # No y-axis: counts are in the stats column. Keeping the chart pure shape.
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    ax.spines["bottom"].set_linewidth(0.4)
    ax.tick_params(colors=INK, labelsize=11, length=2.5, width=0.4, pad=3)
    ax.set_xlabel("")
    ax.set_ylabel("")

    # Thin axis margins — just enough room for the leftmost/rightmost tick
    # labels to fit inside the figure bbox without clipping.
    fig.subplots_adjust(left=0.018, right=0.982, top=0.95, bottom=0.26)

    return _save_svg(fig, out_svg, tight=False)


def render_rgb_thumbnail_png(
    rgb_bands: dict[str, np.ndarray],
    out_png: Path,
    *,
    target_long_edge_px: int = 1024,
    p_lo: float = 2.0,
    p_hi: float = 98.0,
    gamma: float = 0.9,
) -> Path:
    """True-color RGB composite from Sentinel-2 B04/B03/B02 overview arrays.

    Each band is independently percentile-stretched (``p_lo``/``p_hi``) into
    [0, 1], gamma-corrected, then stacked into an RGB image. Final long-edge
    is at most ``target_long_edge_px`` (typical input is already small from
    ``fetch_rgb_overview``).
    """
    r = np.asarray(rgb_bands["red"], dtype="float32")
    g = np.asarray(rgb_bands["green"], dtype="float32")
    b = np.asarray(rgb_bands["blue"], dtype="float32")
    if not (r.shape == g.shape == b.shape):
        # Snap to the smallest shape to defend against off-by-one mismatches
        # between independently-fetched COG overviews.
        h = min(r.shape[0], g.shape[0], b.shape[0])
        w = min(r.shape[1], g.shape[1], b.shape[1])
        r, g, b = r[:h, :w], g[:h, :w], b[:h, :w]

    def _stretch(channel: np.ndarray) -> np.ndarray:
        finite = channel[np.isfinite(channel)]
        if finite.size == 0:
            return np.zeros_like(channel)
        lo, hi = np.percentile(finite, [p_lo, p_hi])
        if hi <= lo:
            return np.zeros_like(channel)
        out = (channel - lo) / (hi - lo)
        return np.clip(np.where(np.isfinite(out), out, 0.0), 0.0, 1.0)

    rgb = np.stack([_stretch(r), _stretch(g), _stretch(b)], axis=-1)
    rgb = np.power(rgb, gamma)
    rgb_u8 = (rgb * 255.0 + 0.5).astype("uint8")

    img = Image.fromarray(rgb_u8, mode="RGB")
    long_edge = max(img.width, img.height)
    if long_edge > target_long_edge_px:
        scale = target_long_edge_px / long_edge
        img = img.resize(
            (int(img.width * scale), int(img.height * scale)),
            Image.Resampling.LANCZOS,
        )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png, optimize=True)
    log.info("Wrote RGB thumbnail (%dx%d) → %s", img.width, img.height, out_png)
    return out_png


def render_gradient_band_png(
    cmap: ResolvedColormap,
    out_png: Path,
    *,
    width_px: int = 2400,
    height_px: int = 660,
    angle_deg: float = 22.5,
    noise: float = 0.018,
    seed: int = 7,
) -> Path:
    """Render the colormap as a noisy linear-gradient band — the page's visual hero.

    Reproduces the look of ``gradient_palettize3.py``: a linear gradient at
    ``angle_deg`` plus light monochromatic Gaussian noise (sigma=``noise``) added
    *before* the colormap is applied, so the noise gets recolored through the
    palette and reads as fine grain rather than bland grayscale dither.
    """
    rng = np.random.default_rng(seed)
    angle_rad = math.radians(angle_deg % 360)
    ux, uy = math.cos(angle_rad), math.sin(angle_rad)

    x = np.linspace(0.0, 1.0, num=width_px, dtype=np.float32)
    y = np.linspace(0.0, 1.0, num=height_px, dtype=np.float32)
    xs, ys = np.meshgrid(x, y)
    grad = xs * ux + ys * uy
    grad -= grad.min()
    grad /= grad.max()

    if noise > 0:
        noise_arr = rng.normal(0.0, noise, grad.shape).astype(np.float32)
        grad = np.clip(grad + noise_arr, 0.0, 1.0)

    rgba = cmap.matplotlib(grad, bytes=True)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    # Opaque RGB: an all-255 alpha channel still becomes a soft mask in the PDF.
    Image.fromarray(rgba[..., :3], mode="RGB").save(out_png, optimize=True)
    log.info("Wrote gradient band PNG (%dx%d, %.1f°) → %s", width_px, height_px, angle_deg, out_png)
    return out_png


@lru_cache(maxsize=1)
def _load_land_rings() -> list[np.ndarray]:
    """Return Natural Earth 110m land polygons as a flat list of (N, 2) lon/lat rings.

    Cached so repeated locator renders during a batch don't re-parse the geojson.
    """
    if not NE_LAND_GEOJSON.exists():
        raise FileNotFoundError(
            f"Natural Earth land geojson not found at {NE_LAND_GEOJSON}. "
            "Re-fetch via: curl -sSL https://raw.githubusercontent.com/"
            "nvkelso/natural-earth-vector/master/geojson/ne_110m_land.geojson "
            f"-o {NE_LAND_GEOJSON}"
        )
    raw = json.loads(NE_LAND_GEOJSON.read_text())
    rings: list[np.ndarray] = []
    for feat in raw["features"]:
        geom = feat["geometry"]
        if geom["type"] == "Polygon":
            polys = [geom["coordinates"]]
        elif geom["type"] == "MultiPolygon":
            polys = geom["coordinates"]
        else:
            continue
        # Each poly is [outer_ring, hole1, hole2, ...]; we render outer rings only
        # (holes inside continents are negligible at 110m and would need even-odd fill).
        for poly in polys:
            for ring in poly[:1]:
                rings.append(np.asarray(ring, dtype=np.float64))
    return rings


def _ortho_project(lons: np.ndarray, lats: np.ndarray, lon0: float, lat0: float):
    """Vectorised orthographic projection of (lon, lat) → (x, y, visible_mask).

    Output coords on the unit sphere; visible iff cos(angular distance) ≥ 0.
    """
    lons = np.radians(lons)
    lats = np.radians(lats)
    lon0r = math.radians(lon0)
    lat0r = math.radians(lat0)
    cos_c = (
        math.sin(lat0r) * np.sin(lats)
        + math.cos(lat0r) * np.cos(lats) * np.cos(lons - lon0r)
    )
    x = np.cos(lats) * np.sin(lons - lon0r)
    y = math.cos(lat0r) * np.sin(lats) - math.sin(lat0r) * np.cos(lats) * np.cos(lons - lon0r)
    return x, y, cos_c >= 0.0


def _split_by_visibility(x: np.ndarray, y: np.ndarray, visible: np.ndarray) -> list[np.ndarray]:
    """Split a ring into runs of visible points, keeping only segments long enough to draw."""
    out: list[np.ndarray] = []
    n = len(visible)
    if n < 3 or not visible.any():
        return out
    if visible.all():
        return [np.column_stack([x, y])]
    # Find runs of visible=True
    pad = np.concatenate(([False], visible, [False]))
    diffs = np.diff(pad.astype(np.int8))
    starts = np.where(diffs == 1)[0]
    ends = np.where(diffs == -1)[0]
    for s, e in zip(starts, ends):
        if e - s >= 3:
            out.append(np.column_stack([x[s:e], y[s:e]]))
    return out


def _draw_graticule(ax, lon0: float, lat0: float, *, color: str, lw: float = 0.35) -> None:
    """Subtle 30° graticule clipped to the visible hemisphere."""
    n_samples = 256

    # Meridians: every 30° from -180..180 step 30
    for L in range(-180, 181, 30):
        lats = np.linspace(-85.0, 85.0, n_samples)
        lons = np.full_like(lats, L)
        x, y, vis = _ortho_project(lons, lats, lon0, lat0)
        for seg in _split_by_visibility(x, y, vis):
            ax.plot(seg[:, 0], seg[:, 1], color=color, lw=lw, zorder=2)

    # Parallels: every 30° from -60..60 (skip poles)
    for P in range(-60, 61, 30):
        lons = np.linspace(-180.0, 180.0, n_samples)
        lats = np.full_like(lons, P)
        x, y, vis = _ortho_project(lons, lats, lon0, lat0)
        for seg in _split_by_visibility(x, y, vis):
            ax.plot(seg[:, 0], seg[:, 1], color=color, lw=lw, zorder=2)


def _draw_globe_to_ax(
    ax,
    lon0: float,
    lat0: float,
    *,
    land_color: str = "#cfcdc7",
    ocean_color: str = "#ecebe4",
    aoi_color: str = "#9a3324",
    graticule_color: str = "#b9b6ae",
    aoi_radius: float = 0.062,
    aoi_lw: float = 1.0,
    graticule_lw: float = 0.35,
    limb_lw: float = 0.5,
) -> None:
    """Render an orthographic globe into ``ax``, centered on (lon0, lat0).

    Shared by the standalone globe and the inset on the regional locator.
    """
    ax.add_patch(MplCircle((0, 0), 1.0, facecolor=ocean_color, edgecolor="none", zorder=0))
    for ring in _load_land_rings():
        x, y, vis = _ortho_project(ring[:, 0], ring[:, 1], lon0, lat0)
        for seg in _split_by_visibility(x, y, vis):
            ax.add_patch(
                MplPolygon(
                    seg, facecolor=land_color, edgecolor="none", closed=True, zorder=1,
                )
            )
    _draw_graticule(ax, lon0, lat0, color=graticule_color, lw=graticule_lw)
    cx, cy, _ = _ortho_project(np.array([lon0]), np.array([lat0]), lon0, lat0)
    ax.add_patch(
        MplCircle((float(cx[0]), float(cy[0])), aoi_radius,
                  facecolor=aoi_color, edgecolor="white", lw=aoi_lw, zorder=4)
    )
    ax.add_patch(
        MplCircle((0, 0), 1.0, facecolor="none", edgecolor=RULE, lw=limb_lw, zorder=5)
    )
    ax.set_xlim(-1.06, 1.06)
    ax.set_ylim(-1.06, 1.06)
    ax.set_aspect("equal")
    ax.axis("off")


def render_locator_globe_svg(
    bbox: tuple[float, float, float, float],
    out_svg: Path,
) -> Path:
    """Standalone orthographic globe centered on the AOI."""
    lon_min, lat_min, lon_max, lat_max = bbox
    lon0 = (lon_min + lon_max) / 2.0
    lat0 = (lat_min + lat_max) / 2.0
    fig, ax = plt.subplots(figsize=(2.6, 2.6))
    fig.patch.set_visible(False)
    _draw_globe_to_ax(ax, lon0, lat0)
    return _save_svg(fig, out_svg)


# ---------------------------------------------------------------------------
# OSM tile basemap (used by the scene locator)
# ---------------------------------------------------------------------------

_OSM_TILE_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def _osm_user_agent() -> str:
    """OSM's tile usage policy asks for an identifying User-Agent with contact
    details. Set ``SPECTRALCULAR_CONTACT`` (an email or URL) so requests from your runs are
    attributable to you rather than to the project."""
    import os
    from importlib.metadata import PackageNotFoundError, version

    try:
        ver = version("spectralcular")
    except PackageNotFoundError:
        ver = "dev"
    contact = (os.environ.get("SPECTRALCULAR_CONTACT") or os.environ.get("GB_CONTACT") or "").strip() or "+https://github.com/kovaca/spectralcular"
    return f"spectralcular/{ver} ({contact})"


_TILE_PX = 256


def _deg2num(lat_deg: float, lon_deg: float, zoom: int) -> tuple[float, float]:
    """Slippy-map: (lat, lon, zoom) → fractional (xtile, ytile)."""
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    x = (lon_deg + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def _num2deg(xtile: float, ytile: float, zoom: int) -> tuple[float, float]:
    """Slippy-map: (xtile, ytile, zoom) → (lon, lat) of NW corner."""
    n = 2.0 ** zoom
    lon = xtile / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ytile / n))))
    return lon, lat


def _pick_zoom(lon_min: float, lon_max: float, target_px: int = 900) -> int:
    """Largest zoom such that the lon span fits in ~``target_px`` horizontally."""
    span = max(lon_max - lon_min, 1e-6)
    # tile width in degrees at zoom z is 360 / 2^z.
    for z in range(14, 0, -1):
        tiles_x = span / (360.0 / 2 ** z)
        if tiles_x * _TILE_PX <= target_px:
            return z
    return 1


def _fetch_osm_basemap(
    extent_lonlat: tuple[float, float, float, float],
    cache_dir: Path,
    *,
    target_px: int = 900,
    timeout_s: float = 15.0,
) -> tuple[Image.Image, tuple[float, float, float, float]] | None:
    """Stitch a regional OSM basemap covering ``extent_lonlat``.

    Returns ``(image, (lon_min, lon_max, lat_min, lat_max))`` where the
    returned extent is the basemap canvas's geographic bounds (slightly
    larger than the requested extent because tiles snap to a grid).
    Returns ``None`` if any tile fetch fails — caller falls back to the
    Natural Earth land outline.
    """
    lon_min, lat_min, lon_max, lat_max = extent_lonlat
    z = _pick_zoom(lon_min, lon_max, target_px=target_px)
    x0_f, y0_f = _deg2num(lat_max, lon_min, z)  # NW
    x1_f, y1_f = _deg2num(lat_min, lon_max, z)  # SE
    # Clamp to the tile grid: a locator widened past ±180° / the Mercator
    # limit would otherwise request tiles that don't exist (HTTP 400) and drop
    # the whole basemap. The map simply ends at the antimeridian.
    last = 2 ** z - 1
    x0, y0 = max(0, int(math.floor(x0_f))), max(0, int(math.floor(y0_f)))
    x1, y1 = min(last, int(math.floor(x1_f))), min(last, int(math.floor(y1_f)))

    cache_dir.mkdir(parents=True, exist_ok=True)
    cols, rows = (x1 - x0 + 1), (y1 - y0 + 1)
    canvas = Image.new("RGB", (cols * _TILE_PX, rows * _TILE_PX), (240, 240, 240))

    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            tile_path = cache_dir / f"{z}_{tx}_{ty}.png"
            if not tile_path.exists():
                url = _OSM_TILE_TEMPLATE.format(z=z, x=tx, y=ty)
                req = urllib.request.Request(url, headers={"User-Agent": _osm_user_agent()})
                try:
                    with urllib.request.urlopen(req, timeout=timeout_s) as r:
                        tile_path.write_bytes(r.read())
                except Exception as exc:
                    log.warning("OSM tile fetch failed (z=%d x=%d y=%d): %s", z, tx, ty, exc)
                    return None
            try:
                tile = Image.open(tile_path).convert("RGB")
            except Exception as exc:
                log.warning("Failed to open cached OSM tile %s: %s", tile_path, exc)
                return None
            canvas.paste(tile, ((tx - x0) * _TILE_PX, (ty - y0) * _TILE_PX))

    nw_lon, nw_lat = _num2deg(x0, y0, z)
    se_lon, se_lat = _num2deg(x1 + 1, y1 + 1, z)
    return canvas, (nw_lon, se_lon, se_lat, nw_lat)


def _fill_polygon_on_raster(
    img: Image.Image,
    ring_lonlat: np.ndarray,
    extent: tuple[float, float, float, float],
    color: str,
    alpha: float,
) -> Image.Image:
    """Composite a translucent polygon fill into an RGB raster shown by
    ``imshow(extent=(lon_min, lon_max, lat_min, lat_max), origin="upper")``."""
    lon_min, lon_max, lat_min, lat_max = extent
    w, h = img.size
    pts = [((lon - lon_min) / (lon_max - lon_min) * w, (lat_max - lat) / (lat_max - lat_min) * h)
           for lon, lat in ring_lonlat]
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rgb = tuple(int(round(255 * c)) for c in to_rgb(color))
    ImageDraw.Draw(layer).polygon(pts, fill=(*rgb, int(round(255 * alpha))))
    out = img.convert("RGBA")
    out.alpha_composite(layer)
    return out.convert("RGB")


def _polygons_from_geojson(geom: dict) -> list[np.ndarray]:
    """Return a flat list of (N, 2) lon/lat outer-ring arrays for a GeoJSON geom."""
    if not geom:
        return []
    t = geom.get("type")
    if t == "Polygon":
        polys = [geom["coordinates"]]
    elif t == "MultiPolygon":
        polys = geom["coordinates"]
    else:
        return []
    out = []
    for poly in polys:
        if poly:
            out.append(np.asarray(poly[0], dtype=np.float64))
    return out


def render_scene_locator_svg(
    bbox: tuple[float, float, float, float],
    scene_geometries: list[dict] | dict | None,
    out_svg: Path,
    *,
    osm_cache_dir: Path | None = None,
    land_color: str = "#cfcdc7",
    ocean_color: str = "#ecebe4",
    accent: str = "#9a3324",
    aoi_polygon: list[tuple[float, float]] | None = None,
) -> Path:
    """Regional view showing every contributing scene footprint and the AOI.

    ``aoi_polygon`` (lon/lat ring) draws a rotated page frame in place of the
    axis-aligned ``bbox`` rectangle; ``bbox`` still centers the globe inset.

    ``scene_geometries`` may be a list of GeoJSON geoms (multi-scene mosaic)
    or a single geom (legacy single-scene callers); either is accepted.
    Layers, low to high z-order: OSM basemap (fallback: Natural Earth land
    outline) → scene-footprint polygons → AOI bbox. The standalone globe
    is composed in the upper-right as an inset for global context.
    """
    lon_min, lat_min, lon_max, lat_max = bbox

    # Normalize scene_geometries into a flat list of lon/lat outer rings.
    if scene_geometries is None:
        geom_iter: list[dict] = []
    elif isinstance(scene_geometries, dict):
        geom_iter = [scene_geometries]
    else:
        geom_iter = list(scene_geometries)

    rings: list[np.ndarray] = []
    for g in geom_iter:
        rings.extend(_polygons_from_geojson(g))
    if rings:
        all_pts = np.concatenate(rings, axis=0)
        s_lon_min, s_lat_min = all_pts.min(axis=0)
        s_lon_max, s_lat_max = all_pts.max(axis=0)
    else:
        s_lon_min, s_lat_min, s_lon_max, s_lat_max = bbox

    span = max(s_lon_max - s_lon_min, s_lat_max - s_lat_min)
    buf = max(0.5 * span, 0.25)  # never pinch tighter than ¼ degree
    map_lon_min = s_lon_min - buf
    map_lon_max = s_lon_max + buf
    map_lat_min = s_lat_min - buf
    map_lat_max = s_lat_max + buf
    lat_mid = (map_lat_min + map_lat_max) / 2

    # The diagnostic page shows the locator in a landscape box beside the
    # true-color preview (see templates/book.css .feature). Widen the
    # longitude window so the drawn map fills roughly a 1.8 : 1 frame after
    # the latitude-dependent aspect correction below.
    target_aspect = 1.8
    lat_span = map_lat_max - map_lat_min
    lon_span_needed = target_aspect * lat_span / max(math.cos(math.radians(lat_mid)), 0.1)
    lon_mid = (map_lon_min + map_lon_max) / 2
    if lon_span_needed > (map_lon_max - map_lon_min):
        map_lon_min = lon_mid - lon_span_needed / 2
        map_lon_max = lon_mid + lon_span_needed / 2

    fig, ax = plt.subplots(figsize=(3.6, 2.0))
    fig.patch.set_visible(False)

    basemap = None
    if osm_cache_dir is not None:
        # Zoom choice lands between target/2 and target px across; the map
        # prints ~2.75 in wide, so 1800 keeps it at or above 300 ppi.
        basemap = _fetch_osm_basemap(
            (map_lon_min, map_lat_min, map_lon_max, map_lat_max),
            osm_cache_dir,
            target_px=1800,
        )

    # The AOI's translucent fill: baked into the basemap raster (or blended
    # with the land color on the vector fallback) so the PDF needs no alpha.
    aoi_ring = (np.asarray(aoi_polygon, dtype=float) if aoi_polygon else
                np.array([(lon_min, lat_min), (lon_max, lat_min), (lon_max, lat_max), (lon_min, lat_max)]))
    aoi_alpha = 0.30

    if basemap is not None:
        img, (b_lon_min, b_lon_max, b_lat_min, b_lat_max) = basemap
        img = _fill_polygon_on_raster(img, aoi_ring, (b_lon_min, b_lon_max, b_lat_min, b_lat_max),
                                      accent, aoi_alpha)
        # Display in lon/lat with the basemap's actual canvas extent. At low
        # latitudes the Web Mercator → equirectangular distortion is small;
        # for editorial regional locators that's acceptable.
        ax.imshow(
            np.asarray(img),
            extent=(b_lon_min, b_lon_max, b_lat_min, b_lat_max),
            origin="upper",
            zorder=0,
            interpolation="bilinear",
        )
    else:
        ax.set_facecolor(ocean_color)
        for ring in _load_land_rings():
            ax.add_patch(
                MplPolygon(ring, facecolor=land_color, edgecolor="none",
                           closed=True, zorder=1)
            )

    # Scene footprint outline (slightly thicker than AOI bbox for legibility)
    for poly in rings:
        ax.add_patch(
            MplPolygon(poly, facecolor="none", edgecolor=accent, lw=1.2, zorder=3)
        )

    # AOI — the page frame polygon when rotated, else the bbox rectangle —
    # filled translucent + thin solid edge
    ax.add_patch(
        MplPolygon(aoi_ring, closed=True, edgecolor=accent, lw=1.0, zorder=4,
                   facecolor="none" if basemap is not None else _blend(accent, aoi_alpha, over=land_color))
    )

    ax.set_xlim(map_lon_min, map_lon_max)
    ax.set_ylim(map_lat_min, map_lat_max)
    ax.set_aspect(1.0 / max(math.cos(math.radians(lat_mid)), 0.1))
    for spine in ax.spines.values():
        spine.set_edgecolor(RULE)
        spine.set_linewidth(0.4)
    ax.set_xticks([])
    ax.set_yticks([])

    # Globe inset — upper right, ~30% of the regional axes.
    g_lon0 = (lon_min + lon_max) / 2.0
    g_lat0 = (lat_min + lat_max) / 2.0
    iax = inset_axes(
        ax, width="32%", height="32%", loc="upper right",
        borderpad=0.4,
    )
    iax.patch.set_visible(False)
    _draw_globe_to_ax(
        iax, g_lon0, g_lat0,
        aoi_radius=0.085, aoi_lw=0.8, graticule_lw=0.25, limb_lw=0.4,
    )

    return _save_svg(fig, out_svg)


# ---------------------------------------------------------------------------
# OKLCh trajectory charts
# ---------------------------------------------------------------------------

# sRGB → OKLab conversion matrices (Björn Ottosson, 2020).
_M1 = np.array([
    [0.4122214708, 0.5363325363, 0.0514459929],
    [0.2119034982, 0.6806995451, 0.1073969566],
    [0.0883024619, 0.2817188376, 0.6299787005],
])
_M2 = np.array([
    [0.2104542553,  0.7936177850, -0.0040720468],
    [1.9779984951, -2.4285922050,  0.4505937099],
    [0.0259040371,  0.7827717662, -0.8086757660],
])


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def _srgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    """``rgb`` is (N, 3) in [0, 1]; returns (N, 3) OKLab."""
    lin = _srgb_to_linear(rgb)
    lms = lin @ _M1.T
    lms_p = np.cbrt(np.clip(lms, 0.0, None))
    return lms_p @ _M2.T


def _oklab_to_oklch(lab: np.ndarray) -> np.ndarray:
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    C = np.sqrt(a * a + b * b)
    h = np.degrees(np.arctan2(b, a)) % 360.0
    return np.stack([L, C, h], axis=-1)


def colormap_oklch_facts(cmap: ResolvedColormap, *, n_samples: int = 256) -> dict[str, str]:
    """Short printable facts about a colormap's OKLCh trajectory for the recto.

    ``lightness``: L* at the two ends (0–100) and whether it is monotonic;
    ``chroma``: peak C* and where along the ramp it occurs; ``hue``: h° at the
    two ends. Formatted strings, ready for the kv list.
    """
    ts = np.linspace(0.0, 1.0, n_samples)
    rgb = cmap.matplotlib(ts)[:, :3]
    L, C, h = np.moveaxis(_oklab_to_oklch(_srgb_to_oklab(rgb)), 1, 0)
    dL = np.diff(L)
    ends = (L[0] * 100, L[-1] * 100)
    if np.all(dL >= -1e-4) or np.all(dL <= 1e-4):
        shape = "monotonic"
    elif L.min() * 100 < min(ends) - 0.5:
        shape = f"dips to {L.min() * 100:.0f}"
    elif L.max() * 100 > max(ends) + 0.5:
        shape = f"peaks at {L.max() * 100:.0f}"
    else:
        shape = "wavers"
    peak = int(np.argmax(C))
    return {
        # Kept to ~18 characters so the row never wraps on the recto.
        "lightness": f"{ends[0]:.0f} → {ends[1]:.0f}, {shape}",
        "chroma": f"{C[peak]:.2f} at {ts[peak]:.2f}",
        "hue": f"{h[0]:.0f}° → {h[-1]:.0f}°",
        "reversed": "yes" if getattr(cmap, "reversed", False) else "no",
    }


def render_colormap_oklch_charts_svg(
    cmap: ResolvedColormap,
    out_svg: Path,
    *,
    n_samples: int = 256,
) -> Path:
    """Three small line charts: L(t), C(t), h(t) tracing the colormap through OKLCh.

    Each subplot has a thin colormap ribbon underlaid along its x-axis so the
    trajectory reads against the actual colors.
    """
    ts = np.linspace(0.0, 1.0, n_samples)
    rgba = cmap.matplotlib(ts)            # (N, 4) RGBA in [0, 1]
    rgb = rgba[:, :3]
    lab = _srgb_to_oklab(rgb)
    lch = _oklab_to_oklch(lab)
    L, C, h = lch[:, 0], lch[:, 1], lch[:, 2]

    # Unwrap hue so the trajectory line doesn't jump 360°→0°.
    h_unwrapped = np.unwrap(np.radians(h))
    h_plot = np.degrees(h_unwrapped)

    # Wide and shallow: the diagnostic page gives the curves a full-width row
    # 23 mm tall under the gradient band (see templates/book.css). 6.5 in wide
    # keeps the 6.5 pt tick labels near 6 pt once scaled to 150 mm.
    fig, axes = plt.subplots(1, 3, figsize=(6.5, 1.0))
    fig.patch.set_visible(False)

    series = [
        ("Lightness  L*", L, (0.0, 1.0)),
        ("Chroma  C*",    C, (0.0, max(0.32, float(C.max()) * 1.05))),
        ("Hue  h°",       h_plot, None),
    ]

    for ax, (title, y, ylim) in zip(axes, series):
        # Color ribbon underlay — a thin band along the bottom of each panel.
        # (85 % over the paper, pre-blended so the raster stays opaque.)
        paper = np.asarray(to_rgb(PAPER))
        ribbon = (0.85 * rgb + 0.15 * paper).reshape(1, -1, 3)
        ax.imshow(
            ribbon,
            aspect="auto",
            extent=(0.0, 1.0, 0.0, 1.0),
            transform=ax.get_xaxis_transform(),
            zorder=0,
        )
        ax.plot(ts, y, color=INK, lw=0.9, zorder=2)
        ax.set_title(title, fontsize=7.5, color=INK, pad=4, loc="left")
        ax.set_xlim(0.0, 1.0)
        if ylim is not None:
            ax.set_ylim(*ylim)
        ax.tick_params(colors=INK, labelsize=6.5, length=2.0, width=0.4, pad=2)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(RULE)
            ax.spines[s].set_linewidth(0.4)

    fig.subplots_adjust(left=0.055, right=0.995, top=0.80, bottom=0.24, wspace=0.28)
    return _save_svg(fig, out_svg, tight=False)


# ---------------------------------------------------------------------------
# Colorblindness (CVD) simulation
# ---------------------------------------------------------------------------

# Classic Viénot–Brettel–Mollon (1999) dichromat simulation matrices, applied
# in *linear* RGB. Approximate (a single matrix per type), so the result is an
# editorial check — "does this colormap collapse for a deuteranope?" — not a
# clinically accurate render. Label any UI toggle accordingly.
_CVD_LINEAR_MATRICES: dict[str, np.ndarray] = {
    "protanopia": np.array([
        [0.0, 2.02344, -2.52581],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ]),
    "deuteranopia": np.array([
        [1.0, 0.0, 0.0],
        [0.494207, 0.0, 1.24827],
        [0.0, 0.0, 1.0],
    ]),
    "tritanopia": np.array([
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [-0.395913, 0.801109, 0.0],
    ]),
}


def simulate_cvd_png(in_png: Path, out_png: Path, *, kind: str = "deuteranopia") -> Path:
    """Write a colorblind-simulated copy of an RGBA PNG, preserving alpha.

    The transform runs in linear RGB (reusing :func:`_srgb_to_linear` /
    :func:`_linear_to_srgb`). ``kind`` ∈ {protanopia, deuteranopia, tritanopia}.
    """
    if kind not in _CVD_LINEAR_MATRICES:
        raise ValueError(f"unknown CVD kind {kind!r}; expected one of {list(_CVD_LINEAR_MATRICES)}")
    img = Image.open(in_png).convert("RGBA")
    arr = np.asarray(img, dtype="float32") / 255.0
    rgb, alpha = arr[..., :3], arr[..., 3:4]

    lin = _srgb_to_linear(rgb)
    sim = lin @ _CVD_LINEAR_MATRICES[kind].T
    out_rgb = _linear_to_srgb(np.clip(sim, 0.0, 1.0))
    rgba = np.concatenate([np.clip(out_rgb, 0.0, 1.0), alpha], axis=-1)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((rgba * 255 + 0.5).astype("uint8"), mode="RGBA").save(out_png, optimize=True)
    return out_png


# ---------------------------------------------------------------------------
# True-color plates
# ---------------------------------------------------------------------------
# The plate itself goes through the shared tone pipeline in
# :mod:`truecolor`; the diagnostic charts below fill the slots the index
# plate uses for its gradient band, OKLCh curves and histogram.

# Muted channel inks, legible in print against the paper tone.
CHANNEL_INK = {"r": "#b3362b", "g": "#4f7d3a", "b": "#2f5f9e"}


def _downsample_float(arr: np.ndarray, long_edge_max: int) -> np.ndarray:
    """Bilinear float downsample to ``long_edge_max`` — the index plate's policy."""
    h, w = arr.shape
    long_edge = max(h, w)
    if long_edge <= long_edge_max:
        return arr
    scale = long_edge_max / long_edge
    tmp = Image.fromarray(np.where(np.isfinite(arr), arr, np.nan).astype("float32"), mode="F")
    return np.asarray(tmp.resize((int(w * scale), int(h * scale)), Image.Resampling.BILINEAR))


def render_truecolor_png(
    r: np.ndarray,
    g: np.ndarray,
    b: np.ndarray,
    spec,
    out_png: Path,
    *,
    long_edge_max: int = MAX_PNG_LONG_EDGE,
) -> Path:
    """Reflectance bands (NaN = invalid) through the tone pipeline → RGBA PNG.

    ``spec`` is a :class:`truecolor.TrueColorSpec` with a *resolved* stretch.
    Downsampling happens on reflectance, before the (non-linear) tone curve, as
    the index plate does on index values before its colormap.
    """
    from .truecolor import to_rgba8, tone

    chans = [_downsample_float(np.asarray(c, dtype="float32"), long_edge_max) for c in (r, g, b)]
    rgba = to_rgba8(tone(np.stack(chans, axis=-1), spec))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(out_png, optimize=True)
    log.info("Wrote true-color PNG (%dx%d) → %s", rgba.shape[1], rgba.shape[0], out_png)
    return out_png


def render_truecolor_linear_png(
    r: np.ndarray,
    g: np.ndarray,
    b: np.ndarray,
    out_png: Path,
    *,
    target_long_edge_px: int = 1024,
) -> Path:
    """The same bands with no tone pipeline at all: reflectance 0–1 shown as
    display 0–1. The diagnostic page sets it beside the rendered plate so the
    reader sees what the stretch did."""
    from .truecolor import TrueColorSpec, TrueColorStretch

    identity = TrueColorSpec(stretch=TrueColorStretch(source="manual", lo=0.0, hi=1.0))
    return render_truecolor_png(r, g, b, identity, out_png, long_edge_max=target_long_edge_px)


def _neutral_axis_max(spec) -> float:
    """Reflectance span the charts draw: a little past the widest white point."""
    return float(max(spec.stretch.hi)) * 1.08


def render_truecolor_ramp_png(
    spec,
    out_png: Path,
    *,
    width_px: int = 2400,
    height_px: int = 660,
    angle_deg: float = 22.5,
    noise: float = 0.018,
    seed: int = 7,
) -> Path:
    """The gradient-band slot for true color: a *neutral* surface (equal
    reflectance in r, g, b) swept from 0 past the white point, through the
    plate's tone pipeline. Shows the tonal response and any cast a
    per-channel stretch puts on grays. Same grain as the colormap band."""
    from .truecolor import tone

    rng = np.random.default_rng(seed)
    angle_rad = math.radians(angle_deg % 360)
    xs, ys = np.meshgrid(np.linspace(0.0, 1.0, width_px), np.linspace(0.0, 1.0, height_px))
    grad = xs * math.cos(angle_rad) + ys * math.sin(angle_rad)
    grad = (grad - grad.min()) / (grad.max() - grad.min())
    if noise > 0:
        grad = np.clip(grad + rng.normal(0.0, noise, grad.shape), 0.0, 1.0)
    rho = grad * _neutral_axis_max(spec)
    rgb = tone(np.stack([rho, rho, rho], axis=-1), spec)
    rgb8 = np.round(rgb * 255.0).clip(0, 255).astype("uint8")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb8, mode="RGB").save(out_png, optimize=True)
    log.info("Wrote true-color neutral ramp (%dx%d) → %s", width_px, height_px, out_png)
    return out_png


def render_truecolor_curves_svg(spec, out_svg: Path, *, n_samples: int = 256) -> Path:
    """Tone curves, reflectance → display, one per channel (stretch, gamma and
    S-curve; saturation mixes channels and is listed in the Tone column).
    Fills the OKLCh-chart slot at the same 6.5 : 1 figure size."""
    from .truecolor import tone, tone_curve

    x_max = _neutral_axis_max(spec)
    x = np.linspace(0.0, x_max, n_samples)
    fig, ax = plt.subplots(figsize=(6.5, 1.0))
    fig.patch.set_visible(False)
    # Neutral-axis ribbon: what a gray surface of each reflectance becomes.
    ribbon = tone(np.stack([x, x, x], axis=-1), spec).reshape(1, -1, 3)
    ax.imshow(ribbon, aspect="auto", extent=(0.0, x_max, 0.0, 0.07),
              transform=ax.get_xaxis_transform(), zorder=0)
    st = spec.stretch
    for c, key in enumerate("rgb"):
        ink = CHANNEL_INK[key]
        ax.plot(x, tone_curve(x, spec, c), color=ink, lw=0.9, zorder=3)
        if st.mode != "linked":
            for v in (st.lo[c], st.hi[c]):
                ax.axvline(v, color=ink, lw=0.4, ls=(0, (2, 2)), zorder=2)
    if st.mode == "linked":
        for v in (st.lo[0], st.hi[0]):
            ax.axvline(v, color=INK, lw=0.4, ls=(0, (2, 2)), zorder=2)
    ax.set_title("Tone curve  reflectance → display", fontsize=7.5, color=INK, pad=4, loc="left")
    ax.set_xlim(0.0, x_max)
    ax.set_ylim(0.0, 1.02)
    ax.set_yticks([0.0, 0.5, 1.0])
    ax.tick_params(colors=INK, labelsize=6.5, length=2.0, width=0.4, pad=2)
    ticks, decimals = nice_ticks(0.0, x_max)
    ax.set_xticks(ticks)
    ax.xaxis.set_major_formatter(_tick_formatter(decimals))
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(RULE)
        ax.spines[s].set_linewidth(0.4)
    fig.subplots_adjust(left=0.055, right=0.995, top=0.80, bottom=0.24)
    return _save_svg(fig, out_svg, tight=False)


def render_truecolor_histogram_svg(
    r: np.ndarray,
    g: np.ndarray,
    b: np.ndarray,
    spec,
    out_svg: Path,
    *,
    bins: int = 140,
) -> Path:
    """Per-channel reflectance histograms (outlines, each normalized to its own
    peak) with every channel's stretch window marked. Same 10 × 2 in figure
    as the index histogram so it fills the same slot."""
    st = spec.stretch
    chans = [np.asarray(c, dtype="float64") for c in (r, g, b)]
    finite = [c[np.isfinite(c)] for c in chans]
    tops = [np.percentile(f, 99.5) for f in finite if f.size]
    x_max = max([_neutral_axis_max(spec), *tops]) if tops else _neutral_axis_max(spec)
    x_min = min(0.0, min(st.lo))
    edges = np.linspace(x_min, x_max, bins + 1)

    fig, ax = plt.subplots(figsize=(10.0, 2.0))
    fig.patch.set_visible(False)
    if st.mode == "linked":
        ax.axvspan(st.lo[0], st.hi[0], color=_blend(RULE, 0.35), lw=0, zorder=0)
    for c, key in enumerate("rgb"):
        ink = CHANNEL_INK[key]
        if finite[c].size:
            counts, _ = np.histogram(finite[c], bins=edges)
            peak = counts.max() or 1
            ax.stairs(counts / peak, edges, color=ink, lw=0.8, zorder=2)
        if st.mode != "linked":
            for v in (st.lo[c], st.hi[c]):
                ax.axvline(v, color=ink, lw=0.5, ls=(0, (2, 2)), zorder=3)
    if st.mode == "linked":
        for v in (st.lo[0], st.hi[0]):
            ax.axvline(v, color=INK, lw=0.5, ls=(0, (2, 2)), zorder=3)

    ax.set_xlim(x_min, x_max)
    ax.set_ylim(0.0, 1.05)
    ax.set_yticks([])
    for s in ("left", "top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    ax.spines["bottom"].set_linewidth(0.4)
    ax.tick_params(colors=INK, labelsize=8, length=2.5, width=0.4, pad=3)
    ticks, decimals = nice_ticks(x_min, x_max)
    ax.set_xticks(ticks)
    ax.xaxis.set_major_formatter(_tick_formatter(decimals))
    fig.subplots_adjust(left=0.018, right=0.982, top=0.95, bottom=0.22)
    return _save_svg(fig, out_svg, tight=False)
