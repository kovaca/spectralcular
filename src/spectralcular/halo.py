"""Feathered text-halo layer for the page-1 plate.

Renders a separate halo PNG (alpha-clipped darkened + slightly blurred copy
of the imagery) sized to the canonical page resolution. The HTML/CSS
template stacks this PNG between the colorized index image and the editorial
text overlays so the text reads cleanly without a heavy global vignette.

Why a separate PNG instead of compositing into the index image: it keeps
debugging trivial (open ``<asset_id>_halo.png`` to see exactly what's
being added) and matches the layered output pattern from
``sat-annotation-viz``.

WeasyPrint doesn't support ``backdrop-filter``; pre-baking the blur means
PDF and HTML render bit-identically.

For print the layers are flattened: :func:`compose_plate_page` composites the
plate and the halo over the page ground into one opaque sRGB JPEG at exactly
the page's pixel size, so the printed plate page carries no transparency and
no image above 300 ppi (Lulu's interior preset).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import matplotlib
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .print_image import PLATE_GROUND_RGB, cover_fit, save_print_jpeg

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Page geometry
# ---------------------------------------------------------------------------

PAGE_SIZE_MM: dict[str, tuple[float, float]] = {
    "A4": (210.0, 297.0),
    "Letter": (215.9, 279.4),
}

# Standard POD bleed (0.125 in) on all sides. The halo PNG must match the
# bleed-inclusive page dimensions used in book.css (.page-image / .halo).
DEFAULT_BLEED_MM: float = 3.175


def page_size_mm(page_size: str, orientation: str,
                 bleed_mm: float = 0.0) -> tuple[float, float]:
    """Return (width, height) in mm. ``bleed_mm`` is added to all four sides."""
    w, h = PAGE_SIZE_MM[page_size]
    if orientation == "landscape":
        w, h = h, w
    if bleed_mm:
        w += 2 * bleed_mm
        h += 2 * bleed_mm
    return w, h


def mm_to_px(mm: float, dpi: int) -> int:
    return int(round(mm * dpi / 25.4))


def pt_to_px(pt: float, dpi: int) -> int:
    return int(round(pt * dpi / 72.0))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

Corner = Literal["tl", "tr", "bl", "br"]
Family = Literal["serif", "mono"]


@dataclass
class HaloParams:
    """Tunables for the feathered halo. Defaults are calibrated for 300 dpi US Letter portrait + 0.125\" bleed."""

    dilate_px: int = 10#22 #18           # mask expansion before blur
    blur_px: int = 6#20 #24             # Gaussian radius on mask
    darken: float = 0.03 #0.18          # 0..1, how much imagery is dimmed under the halo
    imagery_blur_px: int = 5#10 #4      # Gaussian on imagery under halo
    # Drop shadow on the text glyphs themselves — baked into the halo PNG
    # because WeasyPrint doesn't support CSS ``text-shadow``.
    shadow_offset_px: tuple[int, int] = (0, -4) #(0, 4)
    shadow_blur_px: int = 4#4 #6
    shadow_opacity: float = 0.5


@dataclass
class TextOverlay:
    """One editorial overlay on the page-1 plate."""

    text: str
    corner: Corner
    font_pt: float
    family: Family = "mono"
    weight: Literal["normal", "bold"] = "normal"
    italic: bool = False
    # vertical, horizontal — matches CSS `padding: 16mm 36mm`. 16 mm = 3.18 mm
    # bleed + Lulu's 12.7 mm safety margin; 36 mm horizontal clears the 32 mm
    # gutter at 400–600 pp + bleed.
    pad_mm: tuple[float, float] = (16.0, 36.0)
    max_width_mm: float | None = None
    line_spacing: float = 1.18


# ---------------------------------------------------------------------------
# Font resolution (uses matplotlib's bundled DejaVu fonts so we don't need
# to ship our own font files).
# ---------------------------------------------------------------------------

def _font_path(family: Family, weight: str, italic: bool) -> Path:
    base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    if family == "mono":
        return base / "DejaVuSansMono.ttf"
    bold = weight == "bold"
    if bold and italic:
        return base / "DejaVuSerif-BoldItalic.ttf"
    if bold:
        return base / "DejaVuSerif-Bold.ttf"
    if italic:
        return base / "DejaVuSerif-Italic.ttf"
    return base / "DejaVuSerif.ttf"


def _load_font(o: TextOverlay, dpi: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_font_path(o.family, o.weight, o.italic)),
                              size=pt_to_px(o.font_pt, dpi))


# ---------------------------------------------------------------------------
# Overlay placement
# ---------------------------------------------------------------------------

def _wrap_to_width(text: str, font: ImageFont.FreeTypeFont,
                   max_px: int) -> list[str]:
    """Greedy word-wrap so each line fits within ``max_px``."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        # length() is faster than getbbox and adequate for wrap decisions.
        if font.getlength(candidate) <= max_px or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_overlay_to_mask(
    canvas: Image.Image,
    overlay: TextOverlay,
    page_w_px: int,
    page_h_px: int,
    dpi: int,
) -> None:
    """Paint a solid white footprint of one overlay's glyphs into the L-mode mask."""
    font = _load_font(overlay, dpi)
    pad_v_px = mm_to_px(overlay.pad_mm[0], dpi)
    pad_h_px = mm_to_px(overlay.pad_mm[1], dpi)
    line_h_px = int(round(font.size * overlay.line_spacing))
    # CSS half-leading above the first line and below the last line of a
    # line-box: (line-height − 1) × font-size / 2.
    half_leading_px = max(0, int(round((overlay.line_spacing - 1) * font.size / 2)))
    # Anchor on the baseline. The baseline is the only typographic line
    # CSS positions deterministically: baseline = em-box-top + ascent. For
    # fonts where ascent + descent ≠ font.size (e.g. DejaVu Serif Italic)
    # the descender extends past em-box-bottom, so anchoring on cap-top
    # or descender pulls the halo away from the CSS glyphs by that excess.
    ascent_px, _descent_px = font.getmetrics()

    # Wrap if a max width is set.
    if overlay.max_width_mm is not None:
        max_px = mm_to_px(overlay.max_width_mm, dpi)
        lines = _wrap_to_width(overlay.text, font, max_px)
    else:
        lines = [overlay.text]

    draw = ImageDraw.Draw(canvas)

    # All anchors are baseline-relative: PIL "ls" / "rs". Compute the
    # baseline of the FIRST line for top-anchored corners, or the LAST
    # line for bottom-anchored corners, then walk by line_h_px.
    if overlay.corner in ("tl", "tr"):
        # CSS first line: line-box-top = pad_v, em-box-top = pad_v + half_leading,
        # baseline = em-box-top + ascent.
        baseline_first = pad_v_px + half_leading_px + ascent_px
        if overlay.corner == "tl":
            x = pad_h_px
            anchor = "ls"
        else:
            x = page_w_px - pad_h_px
            anchor = "rs"
        for i, line in enumerate(lines):
            draw.text((x, baseline_first + i * line_h_px),
                      line, font=font, fill=255, anchor=anchor)
    else:  # "bl" or "br"
        # CSS last line: line-box-bottom = page_h - pad_v,
        # em-box-bottom = (page_h - pad_v) - half_leading,
        # baseline = em-box-bottom - (font.size - ascent_px).
        baseline_last = (page_h_px - pad_v_px - half_leading_px
                         - (font.size - ascent_px))
        if overlay.corner == "bl":
            x = pad_h_px
            anchor = "ls"
        else:
            x = page_w_px - pad_h_px
            anchor = "rs"
        for i, line in enumerate(lines):
            y = baseline_last - (len(lines) - 1 - i) * line_h_px
            draw.text((x, y), line, font=font, fill=255, anchor=anchor)


# ---------------------------------------------------------------------------
# Mask processing
# ---------------------------------------------------------------------------

def _dilate(mask: Image.Image, radius_px: int) -> Image.Image:
    """Dilate a binary-ish L-mode mask by ``radius_px``.

    PIL ``MaxFilter`` has a hard size cap (~21) and quadratic cost. For larger
    radii we downsample → MaxFilter → upsample, which is visually
    indistinguishable after the subsequent Gaussian blur and far cheaper.
    """
    if radius_px <= 0:
        return mask
    # Cheap path for small radii.
    if radius_px <= 7:
        size = 2 * radius_px + 1
        return mask.filter(ImageFilter.MaxFilter(size))
    # Scaled path: shrink by factor f, dilate proportionally, then upscale.
    f = max(1, radius_px // 4)
    small = mask.resize((mask.width // f, mask.height // f), Image.Resampling.BILINEAR)
    eff = max(1, radius_px // f)
    if eff % 2 == 0:
        eff += 1
    small = small.filter(ImageFilter.MaxFilter(eff))
    return small.resize(mask.size, Image.Resampling.BILINEAR)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render_halo_png(
    *,
    index_image_path: Path,
    overlays: list[TextOverlay],
    page_size: str,
    orientation: str,
    dpi: int,
    params: HaloParams,
    out_halo_path: Path,
    bleed_mm: float = DEFAULT_BLEED_MM,
    optimize: bool = True,
) -> Path:
    """Build a separate halo PNG that stacks above the index plate.

    The output is a full-bleed RGBA image at the canonical page-pixel size,
    including ``bleed_mm`` added to every side so the PNG matches the
    bleed-inclusive ``.page-image`` element in book.css. Pixels are fully
    transparent except in a feathered region behind each overlay, where a
    slightly darkened + slightly blurred copy of the imagery shows through.
    """
    page_w_mm, page_h_mm = page_size_mm(page_size, orientation, bleed_mm=bleed_mm)
    page_w_px = mm_to_px(page_w_mm, dpi)
    page_h_px = mm_to_px(page_h_mm, dpi)

    # Bring the imagery to canonical page resolution. Cover-crop matches the
    # CSS ``object-fit: cover; object-position: center`` on .plate-image so
    # the halo aligns with what's actually visible on page 1.
    plate = cover_fit(Image.open(index_image_path).convert("RGBA"), page_w_px, page_h_px)

    # 1. Render overlay glyphs into an L-mode mask.
    text_mask = Image.new("L", (page_w_px, page_h_px), 0)
    for o in overlays:
        if not o.text:
            continue
        _draw_overlay_to_mask(text_mask, o, page_w_px, page_h_px, dpi)

    # 2. Dilate, then Gaussian-blur into a soft alpha.
    halo_mask = _dilate(text_mask, params.dilate_px)
    halo_mask = halo_mask.filter(ImageFilter.GaussianBlur(params.blur_px))

    # 3. Build the "modified imagery" — slightly darker + slightly blurred.
    modified = ImageEnhance.Brightness(plate.convert("RGB")).enhance(1.0 - params.darken)
    if params.imagery_blur_px > 0:
        modified = modified.filter(ImageFilter.GaussianBlur(params.imagery_blur_px))

    # 4. Halo layer = modified imagery clipped by the soft mask.
    halo_layer = modified.convert("RGBA")
    halo_layer.putalpha(halo_mask)

    # 5. Drop-shadow layer for the glyphs (WeasyPrint can't render
    #    ``text-shadow``, so we bake it into this PNG). Solid black, blurred,
    #    offset down a few px, then alpha-scaled down by ``shadow_opacity``.
    dx, dy = params.shadow_offset_px
    shadow_mask = Image.new("L", (page_w_px, page_h_px), 0)
    shadow_mask.paste(text_mask, (dx, dy))
    if params.shadow_blur_px > 0:
        shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(params.shadow_blur_px))
    if params.shadow_opacity < 1.0:
        shadow_mask = shadow_mask.point(lambda v, k=params.shadow_opacity: int(v * k))
    shadow_layer = Image.new("RGBA", (page_w_px, page_h_px), (0, 0, 0, 0))
    shadow_layer.putalpha(shadow_mask)

    # 6. Composite shadow on top of halo so glyph shadows sit above the
    #    feathered imagery patch.
    halo_layer.alpha_composite(shadow_layer)

    out_halo_path.parent.mkdir(parents=True, exist_ok=True)
    halo_layer.save(out_halo_path, optimize=optimize)
    log.info("Wrote halo PNG (%dx%d) → %s", page_w_px, page_h_px, out_halo_path)
    return out_halo_path


def compose_plate_page(
    *,
    index_image_path: Path,
    halo_path: Path,
    out_path: Path,
    page_size: str,
    orientation: str,
    dpi: int,
    bleed_mm: float = DEFAULT_BLEED_MM,
    ground: tuple[int, int, int] = PLATE_GROUND_RGB,
) -> Path:
    """The printed plate page as one opaque JPEG: page ground, plate
    (cover-cropped exactly as the halo was), halo on top. Same pixel size as
    the halo, i.e. the bleed-inclusive page at ``dpi``."""
    page_w_mm, page_h_mm = page_size_mm(page_size, orientation, bleed_mm=bleed_mm)
    w, h = mm_to_px(page_w_mm, dpi), mm_to_px(page_h_mm, dpi)
    page = Image.new("RGBA", (w, h), (*ground, 255))
    page.alpha_composite(cover_fit(Image.open(index_image_path).convert("RGBA"), w, h))
    halo = Image.open(halo_path).convert("RGBA")
    if halo.size != (w, h):
        halo = halo.resize((w, h), Image.Resampling.LANCZOS)
    page.alpha_composite(halo)
    save_print_jpeg(page.convert("RGB"), out_path, ppi=dpi)
    log.info("Wrote flattened plate page (%dx%d) → %s", w, h, out_path)
    return out_path


def build_page1_overlays(
    *,
    index_short: str,
    colormap_short_name: str,
    location_name: str,
    caption: str,
    plate_number: str,
) -> list[TextOverlay]:
    """Mirror the four CSS .overlay--* divs in spread.html.j2.

    Style values track the CSS rules in ``book.css``:
      - top-left / top-right / bottom-right: 7.5pt mono
      - bottom-right: bold tracking is purely a typographic detail (PIL
        renders the whole word; halo doesn't care about letter-spacing)
      - bottom-left caption: 9pt italic serif, max-width 110mm
    """
    overlays: list[TextOverlay] = [
        TextOverlay(
            text=f"{index_short} · {colormap_short_name}",
            corner="tl",
            font_pt=7.5,
            family="mono",
        ),
        TextOverlay(
            text=location_name.upper(),
            corner="tr",
            font_pt=7.5,
            family="mono",
            weight="bold",
        ),
        TextOverlay(
            text=f"PLATE {plate_number}",
            corner="br",
            font_pt=7.5,
            family="mono",
        ),
    ]
    if caption:
        overlays.append(
            TextOverlay(
                text=caption,
                corner="bl",
                font_pt=9.0,
                family="serif",
                italic=True,
                max_width_mm=110.0,
            )
        )
    return overlays
