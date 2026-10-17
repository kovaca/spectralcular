"""Render a spread template to a print-ready PDF (and/or standalone HTML)."""

from __future__ import annotations

# Must run before importing weasyprint on Apple Silicon macOS.
from . import _compat  # noqa: F401

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from weasyprint import HTML

from .band_ruler import bands_used_label, render_band_ruler_svg
from .platforms import PLATFORMS

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


@dataclass
class SpreadAssets:
    """All on-disk image/SVG assets the spread template references."""

    image_png: Path
    halo_png: Path
    gradient_band_png: Path
    histogram_svg: Path
    locator_svg: Path                 # standalone globe (legacy/diagnostic only)
    rgb_thumbnail_png: Path | None    # None when STAC item lacks RGB assets
    scene_locator_svg: Path           # regional locator with globe inset
    oklch_chart_svg: Path
    # The printed plate page: plate + halo flattened into one opaque JPEG
    # (halo.compose_plate_page). None → the template stacks the two PNGs.
    plate_page: Path | None = None
    # True-color plates: the bands with no tone pipeline, beside the plate.
    # (Their gradient band / OKLCh / histogram slots hold the neutral ramp,
    # tone curves and per-channel histograms.)
    linear_thumbnail_png: Path | None = None


def _format_lat(lat: float) -> str:
    return f"{abs(lat):.3f}°{'S' if lat < 0 else 'N'}"


def _format_lon(lon: float) -> str:
    return f"{abs(lon):.3f}°{'W' if lon < 0 else 'E'}"


def _format_bbox_human(bbox: tuple[float, float, float, float]) -> str:
    lon_min, lat_min, lon_max, lat_max = bbox
    return (
        f"{_format_lat(lat_min)}, {_format_lon(lon_min)}  →  "
        f"{_format_lat(lat_max)}, {_format_lon(lon_max)}"
    )


def _bbox_size_km(bbox: tuple[float, float, float, float]) -> str:
    lon_min, lat_min, lon_max, lat_max = bbox
    lat_mid = math.radians((lat_min + lat_max) / 2)
    km_per_deg_lat = 111.32
    km_per_deg_lon = 111.32 * math.cos(lat_mid)
    w_km = (lon_max - lon_min) * km_per_deg_lon
    h_km = (lat_max - lat_min) * km_per_deg_lat
    return f"{w_km:.1f}×{h_km:.1f} km"


def _shorten_scene_id(scene_id: str) -> str:
    """Drop S2 processing suffixes that aren't meaningful in the page meta.

    "S2A_20MPA_20230724_0_L2A" → "S2A_20MPA_20230724"
    """
    parts = scene_id.split("_")
    if len(parts) >= 5 and parts[-1].startswith("L") and parts[-2].isdigit():
        return "_".join(parts[:-2])
    return scene_id


_SCENE_PREFIX_LABELS = {
    "S2A": "Sentinel-2A", "S2B": "Sentinel-2B", "S2C": "Sentinel-2C",
    "S1A": "Sentinel-1A", "S1B": "Sentinel-1B", "S1C": "Sentinel-1C", "S1D": "Sentinel-1D",
    "LC08": "Landsat 8", "LC09": "Landsat 9", "LO08": "Landsat 8", "LO09": "Landsat 9",
}


def _scene_tile(scene_id: str) -> str:
    """The grid/tile token of a scene id, for tight captions.

    ``S2A_33SVB_20230716`` → ``33SVB``; ``LC09_L2SP_037037_20240710_02_T1`` →
    ``037037``; anything else is returned unchanged.
    """
    parts = scene_id.split("_")
    if len(parts) >= 3 and parts[0].startswith("S2"):
        return parts[1]
    if len(parts) >= 4 and parts[0].startswith("L"):
        return parts[2]
    return scene_id


def _platform_label(platform: str | None, scene_id: str) -> str:
    """Human-readable platform string ("Sentinel-2A", "Landsat 9", "Sentinel-1C").

    ``platform`` is the STAC ``platform`` property (``sentinel-2a``,
    ``landsat-9``, ``sentinel-1c``); falls back to parsing the scene id.
    """
    if platform:
        p = platform.lower()
        if p.startswith("landsat"):
            return "Landsat " + p.split("-")[-1]
        if p.startswith("sentinel-"):
            body = p[len("sentinel-"):]
            return "Sentinel-" + body.upper()
        return platform.replace("-", " ").title()
    head = scene_id.split("_")[0]
    for prefix, label in _SCENE_PREFIX_LABELS.items():
        if head.startswith(prefix):
            return label
    return scene_id[:16]


def _format_count_human(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


# Sentinel-2 L2A asset key → native GSD (m). Used to label the imagery
# kv-list with the coarsest resolution among the bands an index actually
# pulls (e.g. NDVI = red+nir → 10 m; NDMI = nir+swir16 → 20 m).
S2_GSD_M: dict[str, int] = PLATFORMS["sentinel-2-l2a"].gsd_by_asset


def _gsd_for_bands(bands: dict[str, str], platform_id: str = "sentinel-2-l2a") -> str:
    """Coarsest native GSD across the index's asset keys on ``platform_id``."""
    table = PLATFORMS.get(platform_id, PLATFORMS["sentinel-2-l2a"]).gsd_by_asset
    gsds = [table[v] for v in bands.values() if v in table]
    if not gsds:
        return "—"
    return f"{max(gsds)} m"


def _num(v: float, *, signed: bool = True) -> str:
    """A stat for the kv list: about four significant digits, never wider
    than ``+0.000`` / ``+123.4`` / ``+1.2e+05`` (the value column holds ~20
    mono characters and every row has to stay on one line)."""
    sign = "+" if signed else ""
    a = abs(v)
    if a == 0 or 1e-3 <= a < 10:
        return f"{v:{sign}.3f}"
    if a < 100:
        return f"{v:{sign}.2f}"
    if a < 10_000:
        return f"{v:{sign}.1f}"
    return f"{v:{sign}.1e}"


def _pct(fraction: float) -> str:
    """Valid share: ``97.9`` / ``100`` (no ``100.0`` that tips a row onto two lines)."""
    p = 100 * fraction
    return "100" if p >= 99.95 else f"{p:.1f}"


def _norm_label(desc: str | None) -> str:
    """The normalization *method*; the range row carries the numbers.
    ``fixed[-0.81,0.81]`` → ``fixed``; ``pct[2,98]`` → ``p2–p98``."""
    if not desc:
        return "—"
    head, _, rest = desc.partition("[")
    if head.startswith("pct") and rest:
        lo, _, hi = rest.rstrip("]").partition(",")
        return f"p{lo.strip()}–p{hi.strip()}"
    return head.strip() or desc


def _format_stats_for_template(stats: dict[str, Any]) -> dict[str, str]:
    """Pre-format stats so the template stays presentation-only."""
    return {
        "min":       _num(stats["min"]),
        "max":       _num(stats["max"]),
        "mean":      _num(stats["mean"]),
        "std":       _num(stats["std"], signed=False),
        "p1":        _num(stats["p1"]),
        "p50":       _num(stats["p50"]),
        "p99":       _num(stats["p99"]),
        "valid_pct": _pct(stats["valid_fraction"]),
    }


def _truecolor_context(spec, stats: dict | None, preset_label: str) -> dict[str, Any]:
    """Pre-formatted Stretch / Tone column values for a true-color plate."""
    st = spec.stretch
    window = (f"auto · p{st.p_lo:g}–p{st.p_hi:g}" if st.source == "auto" else "manual")
    channels = []
    for c, key in enumerate(("red", "green", "blue")):
        med = (stats or {}).get(key) or {}
        channels.append({
            "name": key,
            "window": f"{st.lo[c]:.3f} → {st.hi[c]:.3f}",
            "median": f"{med['p50']:.2f}" if "p50" in med else "—",
        })
    contrast = (f"{spec.contrast:g} @ {spec.midpoint:.2f}" if spec.contrast >= 1e-3 else "off")
    valid = (stats or {}).get("valid_fraction")
    return {
        "preset": preset_label,
        "mode": {"linked": "linked", "dark-object": "haze offset (shared gain)"}.get(st.mode, "per channel"),
        "window_source": window,
        "channels": channels,
        "medians": " / ".join(ch["median"] for ch in channels),
        "gamma": f"{spec.gamma:.2f}",
        "contrast": contrast,
        "saturation": f"{spec.saturation:.2f}",
        "valid_pct": _pct(valid) if valid is not None else "—",
        "n_pixels_human": _format_count_human(int((stats or {}).get("n_pixels", 0))),
    }


def _file_uri(path: Path) -> str:
    return Path(path).resolve().as_uri()


def _font_uris() -> dict[str, str]:
    """file:// URIs for matplotlib's bundled DejaVu fonts.

    The page-1 halo is rendered in PIL with these exact files; embedding the
    same TTFs via ``@font-face`` makes WeasyPrint render the overlay text with
    identical metrics, so the feathered halo lines up with the glyphs.
    """
    base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    return {
        "font_mono_uri":           (base / "DejaVuSansMono.ttf").as_uri(),
        "font_mono_bold_uri":      (base / "DejaVuSansMono-Bold.ttf").as_uri(),
        "font_serif_uri":          (base / "DejaVuSerif.ttf").as_uri(),
        "font_serif_italic_uri":   (base / "DejaVuSerif-Italic.ttf").as_uri(),
        "font_serif_bold_uri":     (base / "DejaVuSerif-Bold.ttf").as_uri(),
        "font_serif_bolditalic_uri": (base / "DejaVuSerif-BoldItalic.ttf").as_uri(),
    }


def _build_html(
    *,
    asset_id: str,
    location_name: str,
    bbox: tuple[float, float, float, float],
    source: str,
    platform: str | None,
    scene_id: str,
    acquired_iso: str,
    cloud_cover: float,
    crs: str,
    scene_count: int = 1,
    scene_ids: list[str] | None = None,
    date_range_min: str | None = None,
    date_range_max: str | None = None,
    cloud_max: float | None = None,
    kind: str = "index",
    index_short: str | None = None,
    index_long_name: str | None = None,
    index_formula: str | None = None,
    index_domain: tuple[float, float] | None = None,
    application_domain: str | None = None,
    bands: dict[str, str],
    platform_id: str = "sentinel-2-l2a",
    colormap_name: str | None = None,
    colormap_short_name: str | None = None,
    normalization_desc: str | None = None,
    vmin: float | None = None,
    vmax: float | None = None,
    stats: dict[str, Any] | None = None,
    caption: str,
    plate_number: str,
    interpretive_note: str,
    assets: SpreadAssets,
    frame_rotation_deg: float = 0.0,
    frame_extent_km: str | None = None,
    colormap_facts: dict[str, str] | None = None,
    truecolor_spec: Any = None,
    truecolor_stats: dict[str, Any] | None = None,
    truecolor_preset_label: str = "custom",
    book_title: str = "Spectralcular Scenery",
) -> str:
    """Render the spread HTML. ``kind="index"`` needs the ``index_*`` /
    ``colormap_*`` / ``vmin``/``vmax``/``stats`` arguments; ``kind="truecolor"``
    needs ``truecolor_spec`` (resolved) and takes ``truecolor_stats``."""
    if kind not in ("index", "truecolor"):
        raise ValueError(f"unknown plate kind {kind!r}")
    if kind == "truecolor" and truecolor_spec is None:
        raise ValueError("a true-color spread needs truecolor_spec")
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=StrictUndefined,
        autoescape=True,
    )
    template = env.get_template("spread.html.j2")
    css_text = (TEMPLATE_DIR / "book.css").read_text()

    bands_str = ", ".join(f"{k}={v}" for k, v in bands.items())
    acquired_short = acquired_iso.split("T")[0] if "T" in acquired_iso else acquired_iso
    lon_min, lat_min, lon_max, lat_max = bbox
    lon_center = (lon_min + lon_max) / 2
    lat_center = (lat_min + lat_max) / 2

    if kind == "index" and index_domain is not None:
        d0, d1 = index_domain
        index_domain_str = f"[{d0:g}, {d1:g}]"
    else:
        index_domain_str = "—"

    # Multi-scene labels for the Imagery section.
    primary_scene_short = _shorten_scene_id(scene_id)
    scene_ids_short = [_shorten_scene_id(sid) for sid in (scene_ids or [scene_id])]

    def _short_date(iso: str | None) -> str:
        if not iso:
            return ""
        return iso.split("T")[0]

    dmin = _short_date(date_range_min) or acquired_short
    dmax = _short_date(date_range_max) or acquired_short
    date_label = dmin if dmin == dmax else f"{dmin} → {dmax}"

    if cloud_max is not None and cloud_max > cloud_cover + 0.005:
        cloud_label = f"{cloud_cover:.2f}–{cloud_max:.2f}"
    else:
        cloud_label = f"{cloud_cover:.2f}"

    # "Bands in use" ruler: every sensor band, the index's inputs lit.
    plat = PLATFORMS.get(platform_id)
    used_assets = set(bands.values())
    if plat is not None:
        band_ruler_svg = render_band_ruler_svg(plat, used_assets)
        bands_used = bands_used_label(plat, used_assets)
        ruler_range = "thermal segment" if any(v[2] >= 3000 for v in plat.bands_nm.values()) else ""
        sensor_label = f"{plat.label} · 400–2300 nm" + (f" · {ruler_range}" if ruler_range else "")
    else:
        band_ruler_svg, bands_used, sensor_label = "", bands_str, ""

    facts = {"lightness": "—", "chroma": "—", "hue": "—", "reversed": "no"}
    facts.update(colormap_facts or {})

    if kind == "index":
        look: dict[str, Any] = dict(
            index_short=index_short,
            index_long_name=index_long_name,
            index_formula=index_formula,
            index_domain_str=index_domain_str,
            application_domain=application_domain,
            colormap_name=colormap_name,
            colormap_short_name=colormap_short_name,
            colormap_source=(colormap_name.split(":", 1)[0] if ":" in colormap_name else "palettize"),
            normalization_desc=_norm_label(normalization_desc),
            vmin=_num(vmin),
            vmax=_num(vmax),
            n_pixels_human=_format_count_human(int(stats.get("n_pixels", 0))),
            stats=_format_stats_for_template(stats),
            truecolor=None,
            linear_thumbnail_png=None,
        )
    else:
        tc = _truecolor_context(truecolor_spec, truecolor_stats, truecolor_preset_label)
        # "R G B = B04 · B03 · B02": band codes in channel order.
        codes = plat.bands_nm if plat is not None else {}
        tc["channel_bands"] = " · ".join(codes[a][0] if a in codes else a for a in bands.values())
        look = dict(
            truecolor=tc,
            linear_thumbnail_png=(_file_uri(assets.linear_thumbnail_png)
                                  if assets.linear_thumbnail_png else None),
        )

    return template.render(
        kind=kind,
        book_title=book_title,
        **look,
        band_ruler_svg=band_ruler_svg,
        bands_used_label=bands_used,
        sensor_label=sensor_label,
        scene_tiles_label=" · ".join(dict.fromkeys(_scene_tile(s) for s in (scene_ids or [scene_id]))),
        colormap_facts=facts,
        css=css_text,
        **_font_uris(),
        asset_id=asset_id,
        location_name=location_name,
        bbox_human=_format_bbox_human(bbox),
        bbox_west=_format_lon(lon_min),
        bbox_east=_format_lon(lon_max),
        bbox_north=_format_lat(lat_max),
        bbox_south=_format_lat(lat_min),
        bbox_size_km=frame_extent_km or _bbox_size_km(bbox),
        frame_rotation_label=(f"{frame_rotation_deg:.0f}°" if abs(frame_rotation_deg) >= 0.5 else ""),
        title_long=len(index_long_name or "") > 48,
        lat_center_str=_format_lat(lat_center),
        lon_center_str=_format_lon(lon_center),
        source=source,
        platform_label=_platform_label(platform, scene_id),
        scene_id=scene_id,
        scene_id_short=primary_scene_short,
        scene_count=scene_count,
        scene_ids_short=scene_ids_short,
        date_label=date_label,
        cloud_label=cloud_label,
        acquired_short=acquired_short,
        cloud_pct=f"{cloud_cover:.2f}",
        crs=crs,
        bands_str=bands_str,
        gsd_label=_gsd_for_bands(bands, platform_id),
        caption=caption,
        plate_number=plate_number,
        interpretive_note=interpretive_note.strip(),
        # Asset URIs — absolute file:// so the same HTML works in WeasyPrint
        # *and* when opened directly in a browser from any directory.
        image_png=_file_uri(assets.image_png),
        halo_png=_file_uri(assets.halo_png),
        plate_page=_file_uri(assets.plate_page) if assets.plate_page else None,
        gradient_band_png=_file_uri(assets.gradient_band_png),
        histogram_svg=_file_uri(assets.histogram_svg),
        locator_svg=_file_uri(assets.locator_svg),
        rgb_thumbnail_png=(_file_uri(assets.rgb_thumbnail_png)
                           if assets.rgb_thumbnail_png else None),
        scene_locator_svg=_file_uri(assets.scene_locator_svg),
        oklch_chart_svg=_file_uri(assets.oklch_chart_svg),
    )


def render_spread_pdf(*, out_pdf: Path, **ctx: Any) -> Path:
    """Render the spread to a print-ready PDF via WeasyPrint."""
    html = _build_html(**ctx)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(target=str(out_pdf))
    log.info("Wrote spread PDF → %s", out_pdf)
    return out_pdf


def render_spread_html(*, out_html: Path, **ctx: Any) -> Path:
    """Render the spread to a self-contained HTML file (assets via file:// URIs)."""
    html = _build_html(**ctx)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html)
    log.info("Wrote spread HTML → %s", out_html)
    return out_html
