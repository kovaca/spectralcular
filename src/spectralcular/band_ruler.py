"""The "bands in use" ruler: a wavelength axis with one tick per sensor band,
the bands an index consumes lit in the accent color.

Rendered as an inline SVG string so the book template can drop it into the
page and WeasyPrint draws it with the page's own colors. Optical bands sit on
a linear 400–2300 nm axis; thermal bands (Landsat TIRS) get a short broken-axis
segment on the right. Platforms without wavelengths (radar) fall back to a row
of channel labels.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from .platforms import Platform

INK = "#1a1a1a"
INK_MUTE = "#7a7771"
RULE = "#cfcdc7"
ACCENT = "#9a3324"
MONO = "Berkeley Mono, JetBrains Mono, Menlo, ui-monospace, monospace"

W, H = 600.0, 48.0            # viewBox; the page CSS scales it to the column width
X0, X1 = 14.0, 586.0          # axis extent
BASE_Y = 29.0
OPTICAL_NM = (400.0, 2300.0)
THERMAL_NM = (10000.0, 13000.0)
MIN_LABEL_GAP = 22.0          # px between labeled ticks before we drop a label


def _fmt_nm(nm: float) -> str:
    if nm >= 1000 and nm >= THERMAL_NM[0]:
        return f"{nm / 1000:.1f} µm"
    return f"{nm:g}"


def bands_used_label(platform: Platform, used_assets: set[str]) -> str:
    """Human string for the block header, e.g. ``B04 red 665 nm · B08 near-infrared 842 nm``."""
    parts = []
    for asset, (code, name, nm) in platform.bands_nm.items():
        if asset in used_assets:
            parts.append(f"{code} {name} {_fmt_nm(nm)}{'' if nm >= THERMAL_NM[0] else ' nm'}")
    if not parts and used_assets:
        parts = sorted(a.upper() for a in used_assets)
    return " · ".join(parts)


def render_band_ruler_svg(platform: Platform, used_assets: set[str]) -> str:
    """Return an ``<svg>`` element for the given platform with ``used_assets`` lit."""
    if not platform.bands_nm:
        return _render_channel_row(platform, used_assets)

    optical = {a: v for a, v in platform.bands_nm.items() if v[2] < 3000}
    thermal = {a: v for a, v in platform.bands_nm.items() if v[2] >= 3000}

    # Split the axis: optical gets everything unless thermal bands exist.
    if thermal:
        opt_x1 = X0 + (X1 - X0) * 0.78
        th_x0 = opt_x1 + 22.0
    else:
        opt_x1 = X1
        th_x0 = None

    def x_opt(nm: float) -> float:
        t = (nm - OPTICAL_NM[0]) / (OPTICAL_NM[1] - OPTICAL_NM[0])
        return X0 + t * (opt_x1 - X0)

    def x_th(nm: float) -> float:
        t = (nm - THERMAL_NM[0]) / (THERMAL_NM[1] - THERMAL_NM[0])
        return th_x0 + t * (X1 - th_x0)

    out: list[str] = []
    out.append(
        f'<svg class="band-ruler" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:g} {H:g}" '
        f'role="img" aria-label="Sensor bands used by this index">'
    )
    # Visible-light hint along the baseline, 400–700 nm.
    out.append(
        '<defs><linearGradient id="vis" x1="0" x2="1">'
        '<stop offset="0" stop-color="#5b2cff"/><stop offset=".3" stop-color="#2b8bff"/>'
        '<stop offset=".55" stop-color="#37d67a"/><stop offset=".75" stop-color="#f5d33a"/>'
        '<stop offset=".9" stop-color="#ff5a1f"/><stop offset="1" stop-color="#c0192c"/>'
        '</linearGradient></defs>'
    )
    out.append(f'<line x1="{X0}" y1="{BASE_Y}" x2="{opt_x1:.1f}" y2="{BASE_Y}" stroke="{INK_MUTE}" stroke-width="0.6"/>')
    out.append(
        f'<rect x="{x_opt(400):.1f}" y="{BASE_Y - 0.9:.1f}" width="{x_opt(700) - x_opt(400):.1f}" '
        f'height="1.8" fill="url(#vis)"/>'
    )
    if thermal:
        # Broken axis marker and thermal segment.
        bx = (opt_x1 + th_x0) / 2
        out.append(f'<path d="M{bx - 4:.1f} {BASE_Y + 4} l3 -8 l2 8 l3 -8" fill="none" stroke="{INK_MUTE}" stroke-width="0.6"/>')
        out.append(f'<line x1="{th_x0:.1f}" y1="{BASE_Y}" x2="{X1}" y2="{BASE_Y}" stroke="{INK_MUTE}" stroke-width="0.6"/>')

    # Ticks. Decide labels greedily: used bands first, then the rest if they fit.
    ticks: list[tuple[float, str, float, bool]] = []
    for asset, (code, _name, nm) in optical.items():
        ticks.append((x_opt(nm), code, nm, asset in used_assets))
    for asset, (code, _name, nm) in thermal.items():
        ticks.append((x_th(nm), code, nm, asset in used_assets))
    ticks.sort()

    labeled: list[float] = []
    for x, code, nm, used in sorted(ticks, key=lambda t: (not t[3], t[0])):
        if used or all(abs(x - lx) >= MIN_LABEL_GAP for lx in labeled):
            labeled.append(x)
    for x, code, nm, used in ticks:
        if used:
            out.append(f'<line x1="{x:.1f}" y1="{BASE_Y - 10}" x2="{x:.1f}" y2="{BASE_Y + 10}" stroke="{ACCENT}" stroke-width="2.4"/>')
        else:
            out.append(f'<line x1="{x:.1f}" y1="{BASE_Y - 6}" x2="{x:.1f}" y2="{BASE_Y + 6}" stroke="{INK_MUTE}" stroke-width="0.8"/>')
        if x in labeled:
            color = ACCENT if used else INK_MUTE
            weight = ' font-weight="700"' if used else ""
            out.append(
                f'<text x="{x:.1f}" y="{BASE_Y - 13.5}" text-anchor="middle" font-family="{MONO}" '
                f'font-size="7.2" fill="{color}"{weight}>{escape(code)}</text>'
            )
    # Wavelength scale below: axis ends plus every used band center.
    below: list[tuple[float, str, str]] = [
        (X0, "start", f"{OPTICAL_NM[0]:g} nm"),
        (opt_x1, "end", f"{OPTICAL_NM[1]:g} nm"),
    ]
    if thermal:
        below.append((th_x0, "start", _fmt_nm(THERMAL_NM[0])))
        below.append((X1, "end", _fmt_nm(THERMAL_NM[1])))
    for x, _code, nm, used in ticks:
        if used and all(abs(x - bx) >= 34 for bx, _a, _t in below):
            below.append((x, "middle", _fmt_nm(nm)))
    for x, anchor, text in below:
        out.append(
            f'<text x="{x:.1f}" y="{BASE_Y + 16}" text-anchor="{anchor}" font-family="{MONO}" '
            f'font-size="6.8" fill="{INK_MUTE}">{escape(text)}</text>'
        )
    out.append("</svg>")
    return "".join(out)


def _render_channel_row(platform: Platform, used_assets: set[str]) -> str:
    """Radar and other platforms without wavelengths: evenly spaced channel ticks."""
    assets = list(dict.fromkeys(platform.param_to_asset.values()))
    n = max(len(assets), 1)
    out = [
        f'<svg class="band-ruler" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:g} {H:g}" '
        f'role="img" aria-label="Sensor channels used by this index">',
        f'<line x1="{X0}" y1="{BASE_Y}" x2="{X1}" y2="{BASE_Y}" stroke="{INK_MUTE}" stroke-width="0.6"/>',
    ]
    for i, asset in enumerate(assets):
        x = X0 + (X1 - X0) * (i + 0.5) / n
        used = asset in used_assets
        color = ACCENT if used else INK_MUTE
        out.append(
            f'<line x1="{x:.1f}" y1="{BASE_Y - (10 if used else 6)}" x2="{x:.1f}" y2="{BASE_Y + (10 if used else 6)}" '
            f'stroke="{color}" stroke-width="{2.4 if used else 0.8}"/>'
        )
        weight = ' font-weight="700"' if used else ""
        out.append(
            f'<text x="{x:.1f}" y="{BASE_Y - 13.5}" text-anchor="middle" font-family="{MONO}" font-size="7.2" '
            f'fill="{color}"{weight}>{escape(asset.upper())}</text>'
        )
    out.append(
        f'<text x="{X0}" y="{BASE_Y + 17}" font-family="{MONO}" font-size="6.8" fill="{INK_MUTE}">'
        f'{escape(platform.label)} · polarization channels</text>'
    )
    out.append("</svg>")
    return "".join(out)
