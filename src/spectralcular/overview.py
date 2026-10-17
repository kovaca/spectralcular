"""The overview spread: the whole book at a glance, in the back matter just
before the index.

The print twin of the plate picker's Overview tab
(``web/src/lib/state/overview.ts`` + ``ui/BookOverview.svelte``): where the
plates are, when their scenes were taken, how much of the index and colormap
catalogs the book uses, and every index × colormap combination with the
plates that carry it.

* Verso — I Places (world map), II Scene dates (+ season), III Catalog coverage.
* Recto — IV Combinations, grouped by theme then index; each chip is the
  colormap as printed (reversal included) over its plate number(s).

Every figure is an inline SVG laid out here in millimetres (1 user unit =
1 mm), so heights are known before WeasyPrint sees the page: the combinations
block steps down through density tiers until the whole book fits its two
columns.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
from jinja2 import Environment, FileSystemLoader, StrictUndefined

# Must run before importing weasyprint on Apple Silicon macOS.
from . import _compat  # noqa: F401
from .band_ruler import ACCENT, INK, INK_MUTE, MONO, RULE
from .colormaps import colormap_short_name, resolve_colormap
from .config import load_config
from .platforms import PLATFORMS
from .pool import ColormapPool, IndexPool
from .proof import _read_rows

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

INK_SOFT = "#4a4845"
RULE_SOFT = "#e2dfd7"
PAPER = "#f7f4ec"
PAPER_2 = "#fbf9f4"

THEME_TITLES = {
    "vegetation": "Vegetation",
    "water": "Water",
    "burn": "Burn",
    "soil_geology": "Soil & Geology",
    "urban": "Urban",
    "snow_ice": "Snow & Ice",
    "atmosphere": "Atmosphere",
    "radar": "Radar",
}
THEME_ORDER = ("vegetation", "water", "burn", "soil_geology", "urban", "snow_ice", "atmosphere", "radar")
# Theme ink for map dots and date marks — the same table as the picker's
# THEME_RGB in web/src/lib/data/index.ts; keep the two in step.
THEME_RGB: dict[str, tuple[int, int, int]] = {
    "vegetation": (95, 143, 69),
    "water": (61, 127, 184),
    "burn": (194, 91, 44),
    "soil_geology": (168, 131, 74),
    "urban": (138, 138, 138),
    "snow_ice": (127, 176, 201),
    "atmosphere": (155, 127, 184),
    "radar": (111, 111, 134),
}
_NEUTRAL_RGB = (187, 187, 187)
# True-color plates have no colormap; their chip is the neutral ramp the
# diagnostic page uses in the gradient-band slot.
TRUECOLOR_STOPS = ("#1a1a1a", "#7a7771", "#f7f4ec")

CONTENT_W = 150.0  # mm: 8.75in page − 2 × 36 mm padding (book.css --page-pad-x)
MAP_H = 73.0       # Equal Earth at CONTENT_W (aspect 2.055 : 1)
DATES_W, SEASON_W, DATES_H = 104.0, 40.0, 26.0
COMBOS_H = 190.0   # recto: 252 mm content − head, title, section head, colophon, gaps


def theme_hex(theme: str) -> str:
    r, g, b = THEME_RGB.get(theme, _NEUTRAL_RGB)
    return f"#{r:02x}{g:02x}{b:02x}"


def theme_title(theme: str) -> str:
    return THEME_TITLES.get(theme, theme.replace("_", " ").title())


def _pt(pt: float) -> float:
    """Points → millimetres (SVG user units here)."""
    return pt * 25.4 / 72


def _text_w(text: str, pt: float) -> float:
    """Width of ``text`` in the book's monospace face (0.6 em advance)."""
    return len(text) * 0.6 * _pt(pt)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


# ---------------------------------------------------------------------------
# Data: one record per locked plate
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OverviewPlate:
    ordinal: int
    plate_number: str
    kind: str                     # "index" | "truecolor"
    theme: str
    location_id: str
    location_name: str
    lon: float                    # frame center (bbox center without a frame)
    lat: float
    platform: str                 # platform id, e.g. "sentinel-2-l2a"
    index: str | None = None
    colormap: str | None = None   # pool name, never carrying ``_r``
    reversed: bool = False
    colormap_colors: tuple[str, ...] | None = None
    truecolor_preset: str | None = None
    date: str | None = None       # scene date, YYYY-MM-DD


_DATE_RE = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")


def date_from_item_id(item_id: str) -> str | None:
    """The acquisition date embedded in a STAC item id, if any.

    ``S2A_33SWB_20230713_0_L2A`` → ``2023-07-13``; Landsat path/row tokens are
    six digits and never match.
    """
    for m in _DATE_RE.finditer(item_id):
        y, mo, d = (int(g) for g in m.groups())
        if 1972 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def load_overview_plates(
    spreads_csv: Path,
    project_root: Path,
    metadata_dir: Path | None = None,
) -> list[OverviewPlate]:
    """Read the locked book: ``spreads.csv`` in order, each row's spread
    config, and — when ``spectralcular build`` has run — its metadata sidecar for the
    exact scene date (else the date in the pinned item id)."""
    plates: list[OverviewPlate] = []
    for row in _read_rows(spreads_csv):
        cfg_path = project_root / row["config_path"]
        if not cfg_path.exists():
            log.warning("overview: %s has no spread config at %s; skipped", row["id"], cfg_path)
            continue
        cfg = load_config(cfg_path)

        date = None
        meta = (metadata_dir / f"{row['id']}.json") if metadata_dir else None
        if meta is not None and meta.exists():
            date = (json.loads(meta.read_text()).get("scene_datetime") or "")[:10] or None
        if date is None:
            date = next((d for d in map(date_from_item_id, cfg.imagery.item_ids or []) if d), None)

        if cfg.frame is not None:
            lon, lat = cfg.frame.center
        else:
            b = cfg.location.bbox
            lon, lat = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2

        try:
            ordinal = int(row["ordinal"])
        except (KeyError, ValueError):
            ordinal = len(plates) + 1
        plates.append(OverviewPlate(
            ordinal=ordinal,
            plate_number=cfg.layout.plate_number or row.get("plate_number") or f"{ordinal:03d}",
            kind=cfg.kind,
            theme=row["theme"],
            location_id=row["location_id"],
            location_name=cfg.location.name,
            lon=float(lon),
            lat=float(lat),
            platform=cfg.imagery.source,
            index=cfg.index.name if cfg.index else None,
            colormap=cfg.colormap.name if cfg.colormap else None,
            reversed=bool(cfg.colormap.reversed) if cfg.colormap else False,
            colormap_colors=tuple(cfg.colormap.colors) if cfg.colormap and cfg.colormap.colors else None,
            truecolor_preset=(cfg.truecolor.preset or "custom") if cfg.truecolor else None,
            date=date,
        ))
    return plates


# ---------------------------------------------------------------------------
# Aggregation (mirrors bookOverview() in web/src/lib/state/overview.ts)
# ---------------------------------------------------------------------------

@dataclass
class OverviewPoint:
    location_id: str
    name: str
    lon: float                    # mean of the plates' frame centers
    lat: float
    theme: str
    plates: list[OverviewPlate]


@dataclass
class OverviewPair:
    theme: str
    index: str
    colormap: str
    reversed: bool
    plates: list[OverviewPlate]


@dataclass
class BookOverview:
    plates: list[OverviewPlate]
    points: list[OverviewPoint]
    pairs: list[OverviewPair]
    by_index: dict[str, list[OverviewPlate]]
    by_colormap: dict[str, list[OverviewPlate]]
    truecolor: list[OverviewPlate]
    platforms: Counter
    span: tuple[str, str] | None
    no_scene: int
    months: list[int] = field(default_factory=lambda: [0] * 12)

    @property
    def years(self) -> str | None:
        if not self.span:
            return None
        y0, y1 = self.span[0][:4], self.span[1][:4]
        return y0 if y0 == y1 else f"{y0}–{y1}"

    @property
    def repeated(self) -> int:
        return sum(1 for p in self.pairs if len(p.plates) > 1)


def book_overview(plates: list[OverviewPlate]) -> BookOverview:
    plates = sorted(plates, key=lambda p: p.ordinal)
    pts: dict[str, OverviewPoint] = {}
    pairs: dict[tuple, OverviewPair] = {}
    by_index: dict[str, list[OverviewPlate]] = {}
    by_colormap: dict[str, list[OverviewPlate]] = {}
    truecolor: list[OverviewPlate] = []
    platforms: Counter = Counter()
    months = [0] * 12
    dates: list[str] = []

    for p in plates:
        pt = pts.get(p.location_id)
        if pt is None:
            pts[p.location_id] = OverviewPoint(p.location_id, p.location_name, p.lon, p.lat, p.theme, [p])
        else:
            pt.plates.append(p)
        if p.kind == "truecolor":
            truecolor.append(p)
        else:
            by_index.setdefault(p.index, []).append(p)
            by_colormap.setdefault(p.colormap, []).append(p)
            key = (p.theme, p.index, p.colormap, p.reversed)
            if key in pairs:
                pairs[key].plates.append(p)
            else:
                pairs[key] = OverviewPair(p.theme, p.index, p.colormap, p.reversed, [p])
        plat = PLATFORMS.get(p.platform)
        # "Sentinel-2 L2A" → "Sentinel-2", "Landsat 8/9 C2 L2" → "Landsat"
        platforms[plat.label.split(" ")[0] if plat else p.platform] += 1
        if p.date:
            dates.append(p.date)
            months[int(p.date[5:7]) - 1] += 1

    for pt in pts.values():
        pt.lon = sum(p.lon for p in pt.plates) / len(pt.plates)
        pt.lat = sum(p.lat for p in pt.plates) / len(pt.plates)

    dates.sort()
    return BookOverview(
        plates=plates,
        points=list(pts.values()),
        pairs=list(pairs.values()),
        by_index=by_index,
        by_colormap=by_colormap,
        truecolor=truecolor,
        platforms=platforms,
        span=(dates[0], dates[-1]) if dates else None,
        no_scene=len(plates) - len(dates),
        months=months,
    )


@lru_cache(maxsize=1024)
def _stops(name: str, reversed: bool, colors: tuple[str, ...] | None, n: int = 8) -> tuple[str, ...]:
    """``n`` evenly spaced hex stops of a colormap, as printed."""
    cm = resolve_colormap(name, reversed=reversed, colors=list(colors) if colors else None, n_samples=64)
    rgba = np.asarray(cm.matplotlib.colors)
    idx = np.linspace(0, len(rgba) - 1, n).round().astype(int)
    return tuple("#" + "".join(f"{round(float(c) * 255):02x}" for c in rgba[i][:3]) for i in idx)


def _gradient_def(gid: str, stops: tuple[str, ...], *, vertical: bool = False) -> str:
    axis = 'x1="0" y1="1" x2="0" y2="0"' if vertical else 'x1="0" y1="0" x2="1" y2="0"'
    last = max(len(stops) - 1, 1)
    body = "".join(f'<stop offset="{i / last:.3f}" stop-color="{c}"/>' for i, c in enumerate(stops))
    return f'<linearGradient id="{gid}" {axis}>{body}</linearGradient>'


def _mix(a: str, b: str, t: float) -> str:
    """``t`` of color ``a`` over ``b`` (sRGB)."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * t + y * (1 - t)):02x}" for x, y in zip(ca, cb))


def _svg_open(w: float, h: float, cls: str, label: str) -> str:
    return (f'<svg class="{cls}" xmlns="http://www.w3.org/2000/svg" width="{w:g}mm" height="{h:.2f}mm" '
            f'viewBox="0 0 {w:g} {h:.2f}" role="img" aria-label="{escape(label)}">')


def _text(x: float, y: float, s: str, pt: float, fill: str = INK, anchor: str = "start",
          weight: str | None = None, spacing: float | None = None) -> str:
    extra = (f' font-weight="{weight}"' if weight else "") + (f' letter-spacing="{spacing:g}"' if spacing else "")
    return (f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" font-family="{MONO}" '
            f'font-size="{_pt(pt):.3f}" fill="{fill}"{extra}>{escape(s)}</text>')


# ---------------------------------------------------------------------------
# I Places — Equal Earth world map
# ---------------------------------------------------------------------------

_EE = (1.340264, -0.081106, 0.000893, 0.003796)
_EE_X_MAX, _EE_Y_MAX = 2.70663, 1.31737


def equal_earth(lon, lat):
    """Equal Earth projection (Šavrič, Patterson & Jenny 2018), degrees → unit plane."""
    a1, a2, a3, a4 = _EE
    lam, phi = np.radians(lon), np.radians(lat)
    th = np.arcsin(np.sqrt(3) / 2 * np.sin(phi))
    t2 = th * th
    t6 = t2 ** 3
    x = 2 * np.sqrt(3) * lam * np.cos(th) / (3 * (a1 + 3 * a2 * t2 + t6 * (7 * a3 + 9 * a4 * t2)))
    y = th * (a1 + a2 * t2 + t6 * (a3 + a4 * t2))
    return x, y


def _place_label(plates: list[OverviewPlate]) -> str:
    nums = [p.plate_number for p in plates]
    if len(nums) <= 2:
        return "·".join(nums)
    return f"{nums[0]} +{len(nums) - 1}"


def render_world_map_svg(ov: BookOverview, w: float = CONTENT_W, h: float = MAP_H) -> str:
    """Land, graticule, one dot per place (theme ink, sized by plate count),
    plate numbers wherever they fit without collisions."""
    from .render_assets import _load_land_rings

    k = min((w - 0.6) / (2 * _EE_X_MAX), (h - 0.6) / (2 * _EE_Y_MAX))
    cx, cy = w / 2, h / 2

    def proj(lon, lat):
        x, y = equal_earth(np.asarray(lon, float), np.asarray(lat, float))
        return cx + x * k, cy - y * k

    def path(xs, ys, close=False) -> str:
        d = "M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in zip(xs, ys))
        return d + ("Z" if close else "")

    out = [_svg_open(w, h, "ov-map", f"Map of the book's {len(ov.points)} places")]
    lat_s = np.linspace(-90, 90, 91)
    sx1, sy1 = proj(np.full_like(lat_s, 180.0), lat_s)
    sx0, sy0 = proj(np.full_like(lat_s, -180.0), lat_s[::-1])
    sphere = path(np.concatenate([sx1, sx0]), np.concatenate([sy1, sy0]), close=True)
    out.append(f'<path d="{sphere}" fill="{PAPER_2}" stroke="none"/>')
    grat = []
    for lon in range(-150, 181, 30):
        grat.append(path(*proj(np.full_like(lat_s, float(lon)), lat_s)))
    lon_s = np.linspace(-180, 180, 121)
    for lat in (-60, -30, 0, 30, 60):
        grat.append(path(*proj(lon_s, np.full_like(lon_s, float(lat)))))
    out.append(f'<path d="{" ".join(grat)}" fill="none" stroke="{RULE_SOFT}" stroke-width="0.12"/>')
    land = [path(*proj(r[:, 0], r[:, 1]), close=True) for r in _load_land_rings()]
    out.append(f'<path d="{" ".join(land)}" fill="#e4dfd3" stroke="none"/>')
    out.append(f'<path d="{sphere}" fill="none" stroke="{RULE}" stroke-width="0.2"/>')

    pts = sorted(ov.points, key=lambda pt: (-len(pt.plates), pt.plates[0].ordinal))
    placed: list[tuple[float, float, float, float]] = []   # occupied boxes
    dots = []
    for pt in pts:
        x, y = (float(v) for v in proj(pt.lon, pt.lat))
        r = min(0.7 + 0.28 * (len(pt.plates) - 1), 1.8)
        dots.append((pt, x, y, r))
        placed.append((x - r, y - r, x + r, y + r))
    for pt, x, y, r in dots:
        out.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" fill="{theme_hex(pt.theme)}" '
                   f'stroke="{PAPER}" stroke-width="0.2"/>')

    def free(box) -> bool:
        x0, y0, x1, y1 = box
        if x0 < 0.3 or y0 < 0.3 or x1 > w - 0.3 or y1 > h - 0.3:
            return False
        return all(x1 <= a or x0 >= c or y1 <= b or y0 >= d for a, b, c, d in placed)

    pt_size = 4.4
    fh = _pt(pt_size)
    for pt, x, y, r in dots:
        label = _place_label(pt.plates)
        tw = _text_w(label, pt_size)
        # right, left, above, below — box (x0, y0, x1, y1) and text anchor point
        options = [
            ((x + r + 0.4, y - fh * 0.5, x + r + 0.4 + tw, y + fh * 0.5), (x + r + 0.4, y + fh * 0.35, "start")),
            ((x - r - 0.4 - tw, y - fh * 0.5, x - r - 0.4, y + fh * 0.5), (x - r - 0.4, y + fh * 0.35, "end")),
            ((x - tw / 2, y - r - 0.3 - fh, x + tw / 2, y - r - 0.3), (x, y - r - 0.3 - fh * 0.18, "middle")),
            ((x - tw / 2, y + r + 0.3, x + tw / 2, y + r + 0.3 + fh), (x, y + r + 0.3 + fh * 0.82, "middle")),
        ]
        for box, (tx, ty, anchor) in options:
            if free(box):
                placed.append(box)
                out.append(_text(tx, ty, label, pt_size, INK_SOFT, anchor))
                break
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------------
# II Scene dates — one square per plate in its month, stacked by theme
# ---------------------------------------------------------------------------

def _theme_key(p: OverviewPlate) -> tuple:
    return (THEME_ORDER.index(p.theme) if p.theme in THEME_ORDER else 99, p.ordinal)


def render_dates_svg(ov: BookOverview, w: float = DATES_W, h: float = DATES_H) -> str:
    out = [_svg_open(w, h, "ov-dates", "Scene dates, one square per plate")]
    dated = [p for p in ov.plates if p.date]
    if not dated:
        out.append(_text(w / 2, h / 2, "no scene dates", 5.5, INK_MUTE, "middle"))
        out.append("</svg>")
        return "".join(out)

    y0, y1 = int(ov.span[0][:4]), int(ov.span[1][:4])
    n_months = 12 * (y1 - y0 + 1)
    col = w / n_months
    base = h - 3.6
    cols: dict[int, list[OverviewPlate]] = {}
    for p in dated:
        cols.setdefault((int(p.date[:4]) - y0) * 12 + int(p.date[5:7]) - 1, []).append(p)
    tallest = max(len(v) for v in cols.values())
    s = min(col * 0.8, 2.2, (base - 0.5) / (tallest * 1.18))
    gap = s * 0.18

    years = list(range(y0, y1 + 1))
    every = 1 if len(years) <= 8 else 2 if len(years) <= 16 else 5
    for i, yr in enumerate(years):
        x = i * 12 * col
        out.append(f'<line x1="{x:.2f}" y1="0" x2="{x:.2f}" y2="{base + 1.2:.2f}" stroke="{RULE_SOFT}" stroke-width="0.15"/>')
        if (yr - y0) % every == 0:
            out.append(_text(x + 6 * col, h - 0.5, str(yr), 5.2, INK_MUTE, "middle"))
    out.append(f'<line x1="{w:.2f}" y1="0" x2="{w:.2f}" y2="{base + 1.2:.2f}" stroke="{RULE_SOFT}" stroke-width="0.15"/>')
    out.append(f'<line x1="0" y1="{base + 0.25:.2f}" x2="{w:.2f}" y2="{base + 0.25:.2f}" stroke="{INK_MUTE}" stroke-width="0.15"/>')
    for m, plates in sorted(cols.items()):
        x = m * col + (col - s) / 2
        for j, p in enumerate(sorted(plates, key=_theme_key)):
            y = base - (j + 1) * s - j * gap
            out.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{s:.2f}" height="{s:.2f}" fill="{theme_hex(p.theme)}"/>')
    out.append("</svg>")
    return "".join(out)


_MONTH_INITIALS = "JFMAMJJASOND"


def render_season_svg(ov: BookOverview, w: float = SEASON_W, h: float = DATES_H) -> str:
    """Plates per calendar month, stacked by theme."""
    out = [_svg_open(w, h, "ov-season", "Scenes by calendar month")]
    base = h - 3.6
    top = 2.6
    col = w / 12
    bar = col * 0.62
    by_month: list[list[OverviewPlate]] = [[] for _ in range(12)]
    for p in ov.plates:
        if p.date:
            by_month[int(p.date[5:7]) - 1].append(p)
    peak = max([len(m) for m in by_month] + [1])
    unit = (base - top) / peak
    for i, plates in enumerate(by_month):
        x = i * col + (col - bar) / 2
        y = base
        for theme in THEME_ORDER + tuple(sorted({p.theme for p in plates} - set(THEME_ORDER))):
            n = sum(1 for p in plates if p.theme == theme)
            if n:
                y -= n * unit
                out.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar:.2f}" height="{n * unit:.2f}" fill="{theme_hex(theme)}"/>')
        if plates:
            out.append(_text(i * col + col / 2, y - 0.5, str(len(plates)), 4.2, INK_MUTE, "middle"))
        out.append(_text(i * col + col / 2, h - 0.5, _MONTH_INITIALS[i], 5.2, INK_MUTE, "middle"))
    out.append(f'<line x1="0" y1="{base + 0.25:.2f}" x2="{w:.2f}" y2="{base + 0.25:.2f}" stroke="{INK_MUTE}" stroke-width="0.15"/>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------------
# III Catalog coverage — every index and ramp the picker offers, used ones lit
# ---------------------------------------------------------------------------

def coverage_ramps(cmap_pool: ColormapPool) -> list:
    """The ramps the picker offers (no categorical or cyclic maps)."""
    return [e for e in cmap_pool.entries if e.type not in ("categorical", "cyclic")]


def _collection_of(entry) -> str:
    if entry.source is not None and entry.source.collection:
        return entry.source.collection
    head, sep, _ = entry.name.partition(":")
    return head if sep else "custom"


def render_index_coverage_svg(ov: BookOverview, idx_pool: IndexPool, w: float = CONTENT_W) -> tuple[str, float]:
    """One cell per pool index, rows by theme, shaded by how many plates use it.
    Returns ``(svg, height_mm)``."""
    label_w, cell, gap, row_gap = 26.0, 1.9, 0.5, 1.1
    per_line = int((w - label_w + gap) // (cell + gap))
    fills = {0: PAPER_2, 1: _mix(ACCENT, PAPER, 0.45), 2: _mix(ACCENT, PAPER, 0.72), 3: ACCENT}
    rows = []
    for theme in THEME_ORDER:
        names = [e.name for e in idx_pool.entries if e.theme == theme]
        if names:
            rows.append((theme, names))
    body: list[str] = []
    y = 0.0
    for i, (theme, names) in enumerate(rows):
        used = sum(1 for n in names if n in ov.by_index)
        body.append(_text(0, y + cell * 0.9, theme_title(theme), 5.2, INK_SOFT))
        body.append(_text(label_w - 1.6, y + cell * 0.9, f"{used}/{len(names)}", 5.2, INK_MUTE, "end"))
        for j, name in enumerate(names):
            x = label_w + (j % per_line) * (cell + gap)
            cy = y + (j // per_line) * (cell + gap)
            n = len(ov.by_index.get(name, ()))
            stroke = RULE if n == 0 else fills[min(n, 3)]
            body.append(f'<rect x="{x:.2f}" y="{cy:.2f}" width="{cell}" height="{cell}" fill="{fills[min(n, 3)]}" '
                        f'stroke="{stroke}" stroke-width="0.12"/>')
        lines = math.ceil(len(names) / per_line)
        y += lines * (cell + gap) - gap
        if i < len(rows) - 1:
            body.append(f'<line x1="0" y1="{y + row_gap / 2:.2f}" x2="{w:g}" y2="{y + row_gap / 2:.2f}" '
                        f'stroke="{RULE_SOFT}" stroke-width="0.12" stroke-dasharray="0.3 0.5"/>')
        y += row_gap
    # Key
    y += 0.6
    x = label_w
    for n, label in ((0, "unused"), (1, "1 plate"), (2, "2"), (3, "3+")):
        body.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="1.5" height="1.5" fill="{fills[n]}" '
                    f'stroke="{RULE if n == 0 else fills[n]}" stroke-width="0.12"/>')
        body.append(_text(x + 2.2, y + 1.35, label, 4.8, INK_MUTE))
        x += 2.2 + _text_w(label, 4.8) + 3.5
    h = y + 2.0
    return _svg_open(w, h, "ov-coverage", "Index catalog coverage") + "".join(body) + "</svg>", h


_BAR_STEPS = 11  # 0.4 mm steps on a 4.4 mm catalog bar


def render_colormap_coverage_svg(ov: BookOverview, cmap_pool: ColormapPool, w: float = CONTENT_W) -> tuple[str, float]:
    """Every ramp as a thin vertical bar, grouped by collection; unused ones
    faded, used ones underlined. Returns ``(svg, height_mm)``."""
    bw, bgap, bar_h, group_gap = 0.7, 0.22, 4.4, 2.4
    label_pt = 4.6
    line_h = bar_h + 0.9 + _pt(label_pt) + 1.6
    ramps = coverage_ramps(cmap_pool)
    groups: dict[str, list] = {}
    for e in ramps:
        groups.setdefault(_collection_of(e), []).append(e)

    defs: list[str] = []
    body: list[str] = []
    x = y = 0.0
    for col in sorted(groups):
        entries = groups[col]
        used = sum(1 for e in entries if e.name in ov.by_colormap)
        label = f"{col} {used}/{len(entries)}"
        gw = max(len(entries) * (bw + bgap) - bgap, _text_w(label, label_pt))
        if x > 0 and x + gw > w:
            x, y = 0.0, y + line_h
        for j, e in enumerate(entries):
            on = e.name in ov.by_colormap
            stops = _stops(e.name, False, tuple(e.colors) if e.colors else None, _BAR_STEPS)
            if not on:  # faded to 20 % over the paper, pre-blended: print allows no transparency
                stops = tuple(_mix(c, PAPER, 0.2) for c in stops)
            bx = x + j * (bw + bgap)
            # Solid steps, low end at the bottom. WeasyPrint rasterises a
            # gradient this narrow into a 3 × 14 px inline image (72 ppi).
            step = bar_h / len(stops)
            for k, c in enumerate(reversed(stops)):
                body.append(f'<rect x="{bx:.2f}" y="{y + k * step:.3f}" width="{bw}" height="{step + 0.02:.3f}" fill="{c}"/>')
            if on:
                body.append(f'<rect x="{bx:.2f}" y="{y + bar_h + 0.25:.2f}" width="{bw}" height="0.35" fill="{ACCENT}"/>')
        body.append(_text(x, y + bar_h + 0.9 + _pt(label_pt) * 0.8, label, label_pt, INK_MUTE))
        x += gw + group_gap
    h = y + line_h - 1.2
    return (_svg_open(w, h, "ov-coverage", "Colormap catalog coverage")
            + "<defs>" + "".join(defs) + "</defs>" + "".join(body) + "</svg>"), h


# ---------------------------------------------------------------------------
# IV Combinations — theme → index → colormap chips with plate numbers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ComboTier:
    """One density step for the combinations block."""

    name: str
    chip_w: float          # gradient width, mm
    grad_h: float
    num_pt: float
    name_pt: float | None  # colormap name under the number; None hides it
    label_pt: float        # index name

    @property
    def line_h(self) -> float:
        h = self.grad_h + 0.5 + _pt(self.num_pt)
        if self.name_pt:
            h += 0.2 + _pt(self.name_pt)
        return h + 1.5


COMBO_TIERS = (
    ComboTier("named", chip_w=12.5, grad_h=2.0, num_pt=4.8, name_pt=4.4, label_pt=6.2),
    ComboTier("numbered", chip_w=8.5, grad_h=2.0, num_pt=4.8, name_pt=None, label_pt=6.0),
    ComboTier("compact", chip_w=6.0, grad_h=1.6, num_pt=4.3, name_pt=None, label_pt=5.4),
)
COMBO_COLS, COMBO_COL_GAP = 2, 6.0
_HEAD_H, _HEAD_GAP, _CHIP_GAP, _ENTRY_GAP, _WRAP_INDENT = 4.6, 2.2, 1.0, 3.2, 3.0


@dataclass
class _Chip:
    numbers: str
    name: str | None
    stops: tuple[str, ...]
    repeated: bool


@dataclass
class _Group:
    theme: str
    title: str
    meta: str
    entries: list[tuple[str, list[_Chip]]]   # (index label, chips)


def combination_groups(ov: BookOverview) -> list[_Group]:
    """Themes in book order; indices alphabetical within a theme; one chip per
    index × colormap pair (repeats carry every plate number). True-color plates
    close the list as their own group."""
    groups: list[_Group] = []
    themes = THEME_ORDER + tuple(sorted({p.theme for p in ov.pairs} - set(THEME_ORDER)))
    for theme in themes:
        pairs = [p for p in ov.pairs if p.theme == theme]
        if not pairs:
            continue
        entries = []
        for index in sorted({p.index for p in pairs}, key=str.lower):
            chips = []
            for pair in sorted((p for p in pairs if p.index == index), key=lambda p: p.plates[0].ordinal):
                first = pair.plates[0]
                chips.append(_Chip(
                    numbers=" ".join(p.plate_number for p in pair.plates),
                    name=colormap_short_name(pair.colormap, reversed=pair.reversed),
                    stops=_stops(pair.colormap, pair.reversed, first.colormap_colors),
                    repeated=len(pair.plates) > 1,
                ))
            entries.append((index, chips))
        n = sum(len(p.plates) for p in pairs)
        groups.append(_Group(theme, theme_title(theme), f"{_plural(n, 'plate')} · {_plural(len(entries), 'index', 'indices')}", entries))
    if ov.truecolor:
        chips = [_Chip(p.plate_number, p.truecolor_preset, TRUECOLOR_STOPS, False) for p in ov.truecolor]
        groups.append(_Group("truecolor", "True color", _plural(len(chips), "plate"), [("RGB", chips)]))
    return groups


def _chip_w(chip: _Chip, tier: ComboTier) -> float:
    return max(tier.chip_w, _text_w(chip.numbers, tier.num_pt))


def _flow_lines(group: _Group, tier: ComboTier, col_w: float) -> list[list[tuple[float, str, object]]]:
    """Lay one group's entries out as text flows: each item is
    ``(x, "label" | "chip", payload)``; an entry wider than the column wraps
    its chips onto indented continuation lines."""
    lines: list[list] = [[]]
    x = 0.0
    for label, chips in group.entries:
        lw = _text_w(label, tier.label_pt) + 1.2
        ew = lw + sum(_chip_w(c, tier) + _CHIP_GAP for c in chips) - _CHIP_GAP
        lead = _ENTRY_GAP if lines[-1] else 0.0
        if lines[-1] and x + lead + min(ew, lw + _chip_w(chips[0], tier)) > col_w:
            lines.append([])
            x, lead = 0.0, 0.0
        x += lead
        lines[-1].append((x, "label", label))
        x += lw
        for chip in chips:
            cw = _chip_w(chip, tier)
            if x + cw > col_w:
                lines.append([])
                x = _WRAP_INDENT
            lines[-1].append((x, "chip", chip))
            x += cw + _CHIP_GAP
    return [ln for ln in lines if ln]


def layout_combinations(groups: list[_Group], tier: ComboTier, h: float = COMBOS_H, w: float = CONTENT_W) -> tuple[list[tuple[int, float, str, object]], bool]:
    """Fill ``COMBO_COLS`` columns top to bottom. Returns placed blocks
    ``(column, y, "head" | "line", payload)`` and whether everything fit."""
    col_w = (w - COMBO_COL_GAP * (COMBO_COLS - 1)) / COMBO_COLS
    placed: list[tuple[int, float, str, object]] = []
    col, y = 0, 0.0
    for g in groups:
        lines = _flow_lines(g, tier, col_w)
        lead = _HEAD_GAP if y > 0 else 0.0
        if y + lead + _HEAD_H + tier.line_h > h:     # keep a head with its first line
            col, y, lead = col + 1, 0.0, 0.0
        y += lead
        placed.append((col, y, "head", g))
        y += _HEAD_H
        for ln in lines:
            if y + tier.line_h > h:
                col, y = col + 1, 0.0
            placed.append((col, y, "line", ln))
            y += tier.line_h
    return placed, col < COMBO_COLS


def pick_tier(groups: list[_Group], h: float = COMBOS_H) -> ComboTier:
    """The roomiest tier that fits the whole book."""
    for tier in COMBO_TIERS:
        if layout_combinations(groups, tier, h)[1]:
            return tier
    log.warning("overview: combinations overflow even the compact tier; the last ones are cut")
    return COMBO_TIERS[-1]


def render_combinations_svg(groups: list[_Group], tier: ComboTier, w: float = CONTENT_W, h: float = COMBOS_H) -> str:
    placed, _fits = layout_combinations(groups, tier, h, w)
    col_w = (w - COMBO_COL_GAP * (COMBO_COLS - 1)) / COMBO_COLS
    grads: dict[tuple[str, ...], str] = {}
    body: list[str] = []
    num_mm = _pt(tier.num_pt)
    for col, y, kind, payload in placed:
        if col >= COMBO_COLS:
            continue
        x0 = col * (col_w + COMBO_COL_GAP)
        if kind == "head":
            g: _Group = payload
            body.append(f'<rect x="{x0:.2f}" y="{y + 0.95:.2f}" width="1.7" height="1.7" '
                        f'fill="{theme_hex(g.theme) if g.theme != "truecolor" else INK_MUTE}"/>')
            body.append(_text(x0 + 2.7, y + 2.5, g.title.upper(), 5.6, INK, spacing=0.35))
            body.append(_text(x0 + col_w, y + 2.5, g.meta, 4.8, INK_MUTE, "end"))
            body.append(f'<line x1="{x0:.2f}" y1="{y + 3.5:.2f}" x2="{x0 + col_w:.2f}" y2="{y + 3.5:.2f}" '
                        f'stroke="{RULE}" stroke-width="0.12"/>')
            continue
        for x, item_kind, item in payload:
            if item_kind == "label":
                body.append(_text(x0 + x, y + tier.grad_h / 2 + _pt(tier.label_pt) * 0.36, item, tier.label_pt, INK, weight="700"))
                continue
            chip: _Chip = item
            gid = grads.setdefault(chip.stops, f"cg{len(grads)}")
            cw = _chip_w(chip, tier)
            body.append(f'<rect x="{x0 + x:.2f}" y="{y:.2f}" width="{cw:.2f}" height="{tier.grad_h}" '
                        f'fill="url(#{gid})" stroke="{_mix(INK, PAPER, 0.18)}" stroke-width="0.1"/>')
            ny = y + tier.grad_h + 0.5 + num_mm * 0.78
            body.append(_text(x0 + x, ny, chip.numbers, tier.num_pt, ACCENT if chip.repeated else INK_SOFT,
                              weight="700" if chip.repeated else None))
            if tier.name_pt and chip.name:
                max_chars = max(int(cw / (0.6 * _pt(tier.name_pt))), 3)
                name = chip.name if len(chip.name) <= max_chars else chip.name[:max_chars - 1] + "…"
                body.append(_text(x0 + x, ny + 0.2 + _pt(tier.name_pt), name, tier.name_pt, INK_MUTE))
    defs = "".join(_gradient_def(gid, stops) for stops, gid in grads.items())
    return (_svg_open(w, h, "ov-combos", "Every index and colormap combination in the book")
            + f"<defs>{defs}</defs>" + "".join(body) + "</svg>")


# ---------------------------------------------------------------------------
# The spread
# ---------------------------------------------------------------------------

def _platform_phrase(platforms: Counter) -> str:
    """``Sentinel-2`` alone; ``Sentinel-2 (196) and Landsat (35)``; three or
    more get the serial comma."""
    items = platforms.most_common()
    if len(items) == 1:
        return items[0][0]
    parts = [f"{label} ({n})" for label, n in items]
    if len(parts) == 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def _build_html(plates: list[OverviewPlate], idx_pool: IndexPool, cmap_pool: ColormapPool,
                book_title: str = "Spectralcular Scenery") -> str:
    ov = book_overview(plates)
    groups = combination_groups(ov)
    tier = pick_tier(groups)
    index_cov, _ = render_index_coverage_svg(ov, idx_pool)
    cmap_cov, _ = render_colormap_coverage_svg(ov, cmap_pool)
    ramps = coverage_ramps(cmap_pool)
    n_idx, n_cm = len(ov.by_index), len(ov.by_colormap)
    places = len(ov.points)

    tally = [_plural(len(ov.plates), "plate"), _plural(places, "place"),
             _plural(n_idx, "index", "indices"), _plural(n_cm, "colormap")]
    if ov.truecolor:
        tally.append(f"{len(ov.truecolor)} true color")
    subtitle = (f"{_plural(len(ov.plates), 'plate')} from {_plural(places, 'place')}"
                + (f", {ov.years}" if ov.years else "") + f", by {_platform_phrase(ov.platforms)}")
    themes = [t for t in THEME_ORDER if any(p.theme == t for p in ov.plates)]
    dates_meta = " · ".join(filter(None, [ov.years, f"{ov.no_scene} undated" if ov.no_scene else ""]))
    n_combos = len(ov.pairs) + (1 if ov.truecolor else 0)

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), undefined=StrictUndefined, autoescape=True)
    return env.get_template("overview.html.j2").render(
        css=(TEMPLATE_DIR / "book.css").read_text(),
        book_title=book_title,
        tally=" · ".join(tally),
        subtitle=subtitle,
        places_meta=f"{_plural(places, 'place')} · numbers are plates · larger dots hold more",
        legend=[(theme_title(t), theme_hex(t)) for t in themes],
        dates_meta=dates_meta,
        coverage_meta=f"{n_idx} of {len(idx_pool.entries)} indices · {n_cm} of {len(ramps)} colormaps",
        combos_tally=f"{_plural(len(ov.pairs), 'combination')} · {ov.repeated} repeated",
        combos_meta=f"{_plural(n_combos, 'look')} · red numbers share a combination",
        plates_label=_plural(len(ov.plates), "plate"),
        world_map_svg=render_world_map_svg(ov),
        dates_svg=render_dates_svg(ov),
        season_svg=render_season_svg(ov),
        index_coverage_svg=index_cov,
        colormap_coverage_svg=cmap_cov,
        combinations_svg=render_combinations_svg(groups, tier),
    )


def render_overview_pdf(*, out_pdf: Path, plates: list[OverviewPlate], idx_pool: IndexPool,
                        cmap_pool: ColormapPool, book_title: str = "Spectralcular Scenery") -> Path:
    """Render the two-page overview spread (verso, recto) to ``out_pdf``."""
    from weasyprint import HTML

    if not plates:
        raise ValueError("no plates to overview; run `spectralcular candidates lock` first")
    html = _build_html(plates, idx_pool, cmap_pool, book_title)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(target=str(out_pdf))
    log.info("Wrote overview spread (%d plates) → %s", len(plates), out_pdf)
    return out_pdf


def render_overview_html(*, out_html: Path, plates: list[OverviewPlate], idx_pool: IndexPool,
                         cmap_pool: ColormapPool, book_title: str = "Spectralcular Scenery") -> Path:
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(_build_html(plates, idx_pool, cmap_pool, book_title))
    log.info("Wrote overview HTML → %s", out_html)
    return out_html
