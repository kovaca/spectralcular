"""One-piece cover: back · spine · front (``spectralcular print``).

Geometry comes from :mod:`book_specs` for the *actual* interior page count,
for either binding Lulu offers:

* hardcover casewrap — 8.625 × 11.25 in boards, the sheet wrapping 0.75 in
  round their edges, spine from Lulu's page-range table:
  width = 2 × (wrap + board) + spine, height = board + 2 × wrap
  (214 pages → 19.5 × 12.75 in, spine 0.75 in);
* paperback — width = 2 × (trim + bleed) + spine, height = trim + 2 × bleed,
  spine = pages / 444 + 0.06 in.

Lulu's rules the layout follows (Book Creation Guide pp. 13–24; help center):

* everything that matters sits 0.5 in inside the trim (paperback) or the
  board edge (casewrap) on every panel — and 0.5 in from the spine folds,
  since the spine can drift ±0.125 in;
* the background wraps continuously across the spine (no image or hard
  color edge that stops at a fold);
* spine text only at ``spine_text_min_pages`` or more, 0.125 in clear of
  both folds;
* with an ISBN, a 2.5 × 1.7 in white barcode box bottom-right on the back
  (Lulu supplies the barcode art; ``cover.barcode_image`` places it);
* no transparency, images at 300 ppi — :mod:`print_pdf` finishes the file.

The cover is read upright: spine text runs top to bottom (US convention).
"""

from __future__ import annotations

# Must run before importing weasyprint on Apple Silicon macOS.
from . import _compat  # noqa: F401

import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from PIL import Image

from .book_meta import BookMeta, paragraphs
from .book_specs import BookSpecs
from .overview import OverviewPlate, book_overview
from .print_image import PAPER_RGB, PLATE_GROUND_RGB, cover_fit, flatten, px_for, save_print_jpeg

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
SAFETY_IN = 0.5
HERO_W_IN = 6.0
MOSAIC_GAP_IN = 0.05


@dataclass(frozen=True)
class CoverGeometry:
    """Inches from the sheet's top-left. ``edge_in`` is the paper beyond each
    panel's outer edge (bleed on a paperback, the board wrap on a casewrap);
    a panel is the trim (paperback) or the board (casewrap)."""
    pages: int
    width_in: float
    height_in: float
    edge_in: float
    spine_in: float
    panel_w_in: float
    panel_h_in: float
    safety_in: float = SAFETY_IN
    casewrap: bool = False

    @property
    def back_x0(self) -> float:          # back panel's outer edge
        return self.edge_in

    @property
    def spine_x0(self) -> float:
        return self.edge_in + self.panel_w_in

    @property
    def front_x0(self) -> float:         # front panel's spine fold
        return self.spine_x0 + self.spine_in

    def back_box(self) -> tuple[float, float, float, float]:
        """(x, y, w, h) of the back panel's safe area."""
        s = self.safety_in
        return (self.back_x0 + s, self.edge_in + s, self.panel_w_in - 2 * s, self.panel_h_in - 2 * s)

    def front_box(self) -> tuple[float, float, float, float]:
        s = self.safety_in
        return (self.front_x0 + s, self.edge_in + s, self.panel_w_in - 2 * s, self.panel_h_in - 2 * s)


def cover_geometry(specs: BookSpecs, pages: int) -> CoverGeometry:
    w, h = specs.cover_document_for(pages)
    pw, ph = specs.cover_panel_in
    safety = specs.cover.safety_margin_in if specs.cover.safety_margin_in is not None else SAFETY_IN
    return CoverGeometry(pages=pages, width_in=w, height_in=h, edge_in=specs.cover_edge_in,
                         spine_in=specs.spine_width_for(pages), panel_w_in=pw, panel_h_in=ph,
                         safety_in=safety, casewrap=specs.is_casewrap)


def mosaic_grid(n: int, box_w: float, box_h: float, aspect: float, gap: float = MOSAIC_GAP_IN) -> tuple[int, int, float]:
    """(cols, rows, tile width) of the largest tiles of ``aspect`` (w/h) that
    fit ``n`` tiles in ``box_w × box_h`` inches."""
    best = (1, n, 0.0)
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        tw = min((box_w - gap * (cols - 1)) / cols, ((box_h - gap * (rows - 1)) / rows) * aspect)
        if tw > best[2]:
            best = (cols, rows, tw)
    return best


def render_mosaic(images: list[Path], out_jpg: Path, *, cols: int, rows: int, tile_w_in: float,
                  aspect: float, gap_in: float = MOSAIC_GAP_IN) -> tuple[Path, float, float]:
    """Every plate as a tile, in plate order, on paper. Returns (path, w_in, h_in)."""
    tw, th = px_for(tile_w_in), px_for(tile_w_in / aspect)
    gap = px_for(gap_in)
    w, h = cols * tw + (cols - 1) * gap, rows * th + (rows - 1) * gap
    sheet = Image.new("RGB", (w, h), PAPER_RGB)
    for i, path in enumerate(images):
        r, c = divmod(i, cols)
        try:
            tile = flatten(Image.open(path), PLATE_GROUND_RGB)
        except OSError:
            log.warning("cover mosaic: cannot read %s; tile left blank", path)
            continue
        tile.thumbnail((tw * 4, th * 4))  # cheap pre-shrink of the 2.6k masters
        sheet.paste(cover_fit(tile, tw, th), (c * (tw + gap), r * (th + gap)))
    save_print_jpeg(sheet, out_jpg)
    return out_jpg, w / 300, h / 300


def render_hero(image: Path, out_jpg: Path, *, width_in: float, aspect: float) -> Path:
    img = flatten(Image.open(image), PLATE_GROUND_RGB)
    return save_print_jpeg(cover_fit(img, px_for(width_in), px_for(width_in / aspect)), out_jpg)


def render_cover(
    *,
    out_pdf: Path,
    specs: BookSpecs,
    meta: BookMeta,
    plates: list[OverviewPlate],
    plate_images: dict[str, Path],
    pages: int,
    work_dir: Path,
    project_root: Path,
) -> tuple[Path, CoverGeometry]:
    """Render the cover spread. ``plate_images`` maps plate number → the
    plate's master image (``assets/images/<id>.png``)."""
    from weasyprint import HTML

    geo = cover_geometry(specs, pages)
    work_dir.mkdir(parents=True, exist_ok=True)
    aspect = specs.plate_aspect

    hero_plate = next((p for p in plates if meta.cover.front_plate in (p.plate_number, p.location_id)), None) \
        if meta.cover.front_plate else None
    if meta.cover.front_plate and hero_plate is None:
        hero_plate = next((p for p in plates
                           if plate_images.get(p.plate_number, Path()).stem == meta.cover.front_plate), None)
    if meta.cover.front_plate and hero_plate is None:
        log.warning("cover: front_plate %r matches no plate number, location or candidate id; using plate %s",
                    meta.cover.front_plate, plates[0].plate_number)
    hero_plate = hero_plate or plates[0]
    hero = render_hero(plate_images[hero_plate.plate_number], work_dir / "cover_front.jpg",
                       width_in=HERO_W_IN, aspect=aspect)

    bx, by, bw, bh = geo.back_box()
    barcode = specs.cover.barcode_area
    reserve = barcode.height_in + 0.3 if meta.isbn else 0.9
    blurb_h = 1.25
    cols, rows, tile_w = mosaic_grid(len(plates), bw - 0.5, bh - blurb_h - reserve - 0.3, aspect)
    mosaic, mw, mh = render_mosaic([plate_images[p.plate_number] for p in plates], work_dir / "cover_mosaic.jpg",
                                   cols=cols, rows=rows, tile_w_in=tile_w, aspect=aspect)

    ov = book_overview(plates)
    counts = {"n_plates": len(plates), "n_places": len(ov.points),
              "n_indices": len(ov.by_index), "n_colormaps": len(ov.by_colormap)}
    blurb = [re.sub(r"\{(\w+)\}", lambda m: str(counts.get(m.group(1), m.group(0))), para)
             for para in paragraphs(meta.cover.back_text)]

    barcode_img = None
    if meta.isbn and meta.cover.barcode_image:
        p = (project_root / meta.cover.barcode_image)
        barcode_img = p.resolve().as_uri() if p.exists() else None
        if barcode_img is None:
            log.warning("cover: barcode image %s not found; the box is left empty", p)

    show_spine_text = pages >= specs.cover.spine_text_min_pages
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), undefined=StrictUndefined, autoescape=True)
    html = env.get_template("cover.html.j2").render(
        css=(TEMPLATE_DIR / "book.css").read_text(),
        g=geo,
        meta=meta,
        blurb=blurb,
        back=dict(x=bx, y=by, w=bw, h=bh),
        front=dict(zip("xywh", geo.front_box())),
        hero=dict(uri=hero.resolve().as_uri(), w=HERO_W_IN, h=HERO_W_IN / aspect),
        mosaic=dict(uri=mosaic.resolve().as_uri(), w=mw, h=mh, y=by + blurb_h + 0.15),
        spine_text=show_spine_text,
        spine_clear=specs.cover.spine_text_clearance_in,
        barcode=dict(show=bool(meta.isbn), w=barcode.width_in, h=barcode.height_in, img=barcode_img),
    )
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(target=str(out_pdf))
    log.info("Wrote cover %.3f × %.3f in (spine %.3f in, %d pages) → %s",
             geo.width_in, geo.height_in, geo.spine_in, pages, out_pdf)
    return out_pdf, geo
