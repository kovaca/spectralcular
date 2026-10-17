"""Front matter: half-title through "Reading a scene", ahead of plate 001.

The front matter calls the spreads *scenes*; the spreads themselves keep
their plate numbers.

Page 1 is a recto, and every plate spread is plate (verso) + specimen card
(recto), so the front matter must end on a recto — an odd page count — for
plate 001 to open on a verso. The sequence is built to land there:

    1  half-title            (recto)
    2  frontispiece          the book's colormaps, one strip per plate
    3  title page            (recto)
    4  copyright / credits   derived from the plates + config/book.yaml
    5  contents              (recto)
    6  introduction          (flows; the list of plates opens on a verso)
    8  list of scenes        verso + recto
   10  reading a scene       verso: the scene page · recto: the specimen card

and :func:`proof.build_proof` pads with one blank page if it ever doesn't.
Contents and list-of-plates page numbers for the plates and the overview are
computed from that rule, so the front matter is rendered twice: once to
count its pages, once with the numbers.
"""

from __future__ import annotations

# Must run before importing weasyprint on Apple Silicon macOS.
from . import _compat  # noqa: F401

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from PIL import Image

from .book_meta import BookMeta, paragraphs
from .overview import (
    TRUECOLOR_STOPS,
    OverviewPlate,
    _platform_phrase,
    _plural,
    _stops,
    book_overview,
    theme_title,
)
from .platforms import PLATFORMS
from .print_image import px_for, save_print_jpeg

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
NOTICE_MD = Path(__file__).resolve().parent / "_data" / "colorcurious" / "NOTICE.md"

# Frontispiece strip block and the "Reading a plate" miniatures (book.css .fm-*).
FRONTIS_MM = (150.0, 200.0)
MINIATURE_W_MM = 74.0

# palettize namespaces whose maps the colorcurious notice credits under another name.
_COLLECTION_ALIAS = {"bids": "matplotlib"}


@dataclass(frozen=True)
class PlateRow:
    number: str
    place: str
    look: str          # "NDVI · delta" / "True color"
    theme: str
    page: int


@dataclass(frozen=True)
class CreditBlock:
    heading: str
    lines: list[str]


def plates_start_page(front_pages: int) -> int:
    """First plate page: the first verso (even page) after the front matter."""
    nxt = front_pages + 1
    return nxt if nxt % 2 == 0 else nxt + 1


# ---------------------------------------------------------------------------
# Credits
# ---------------------------------------------------------------------------

def _notice_table(path: Path = NOTICE_MD) -> dict[str, tuple[str, str]]:
    """collection → (license, attribution) from the vendored catalog notice."""
    out: dict[str, tuple[str, str]] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[1].isdigit():
            out[cells[0]] = (cells[2], cells[3])
    return out


def colormap_collection(name: str) -> str:
    """``colorcurious:crameri.roma`` → crameri; ``cmocean:delta`` → cmocean."""
    head, sep, rest = name.partition(":")
    if not sep:
        return "custom"
    if head == "colorcurious":
        return rest.split(".", 1)[0]
    return _COLLECTION_ALIAS.get(head, head)


def _year_span(dates: list[str]) -> str:
    years = sorted({d[:4] for d in dates if d})
    if not years:
        return ""
    return years[0] if years[0] == years[-1] else f"{years[0]}–{years[-1]}"


def _stac_provider(api: str) -> str:
    if "element84" in api:
        return "Earth Search by Element 84 (Registry of Open Data on AWS)"
    if "planetarycomputer" in api:
        return "Microsoft Planetary Computer"
    return api


def derive_credits(plates: list[OverviewPlate], meta: BookMeta) -> list[CreditBlock]:
    """The colophon's credits, from what the plates actually use."""
    by_platform: dict[str, list[str]] = {}
    for p in plates:
        by_platform.setdefault(p.platform, []).append(p.date or "")

    imagery: list[str] = []
    providers: list[str] = []
    for pid, dates in by_platform.items():
        span = _year_span(dates)
        plat = PLATFORMS.get(pid)
        if pid.startswith("sentinel"):
            line = f"Contains modified Copernicus Sentinel data {span}".strip() + "."
        elif pid.startswith("landsat"):
            line = "Landsat Collection 2 Level-2 data courtesy of the U.S. Geological Survey" + (f", {span}" if span else "") + "."
        else:
            line = f"{plat.label if plat else pid} data" + (f", {span}" if span else "") + "."
        if line not in imagery:
            imagery.append(line)
        if plat is not None:
            prov = f"{plat.label} scenes via {_stac_provider(plat.stac_api)}."
            if prov not in providers:
                providers.append(prov)

    notice = _notice_table()
    used = sorted({colormap_collection(p.colormap) for p in plates if p.colormap})
    cmaps = []
    for c in used:
        lic, attr = notice.get(c, ("", ""))
        cmaps.append(f"{c}: {attr}" + (f" ({lic})" if lic else "") if attr else c)

    blocks = [
        CreditBlock("Imagery", imagery + providers),
        CreditBlock("Spectral indices", [
            ("Index definitions from Awesome Spectral Indices (Montero et al. 2023, "
             "Scientific Data 10, 197), computed with spyndex."),
        ]),
        CreditBlock("Colormaps", cmaps),
        CreditBlock("Maps", [
            "Locator basemaps © OpenStreetMap contributors, ODbL 1.0.",
            "Land outlines and globes from Natural Earth (public domain).",
        ]),
        CreditBlock("Making", [
            "Composed with spectralcular; color by palettize; typeset with WeasyPrint.",
            *meta.extra_credits,
        ]),
    ]
    return [b for b in blocks if b.lines]


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def render_frontispiece(plates: list[OverviewPlate], out_jpg: Path,
                        size_mm: tuple[float, float] = FRONTIS_MM) -> Path:
    """One horizontal strip per plate, in plate order, each its colormap as printed."""
    w, h = px_for(size_mm[0] / 25.4), px_for(size_mm[1] / 25.4)
    n = max(1, len(plates))
    img = np.zeros((h, w, 3), dtype=np.uint8)
    edges = np.linspace(0, h, n + 1).round().astype(int)
    xs = np.linspace(0.0, 1.0, w)
    for i, p in enumerate(plates):
        stops = (TRUECOLOR_STOPS if p.kind == "truecolor" or not p.colormap
                 else _stops(p.colormap, p.reversed, p.colormap_colors, n=64))
        rgb = np.array([[int(s[k:k + 2], 16) for k in (1, 3, 5)] for s in stops], dtype=float)
        pos = np.linspace(0.0, 1.0, len(rgb))
        row = np.stack([np.interp(xs, pos, rgb[:, c]) for c in range(3)], axis=-1)
        img[edges[i]:edges[i + 1]] = row.round().astype(np.uint8)
    return save_print_jpeg(Image.fromarray(img), out_jpg)


def render_miniatures(spread_pdf: Path, out_dir: Path, width_mm: float = MINIATURE_W_MM) -> tuple[Path, Path] | None:
    """Plate page and specimen card of ``spread_pdf`` as print-resolution rasters."""
    if not spread_pdf.exists():
        return None
    out = []
    with fitz.open(spread_pdf) as doc:
        if doc.page_count < 2:
            return None
        for i, name in enumerate(("plate", "card")):
            page = doc[i]
            zoom = px_for(width_mm / 25.4) / page.rect.width
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            out.append(save_print_jpeg(img, out_dir / f"reading_{name}.jpg"))
    return out[0], out[1]


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

def _look(p: OverviewPlate) -> str:
    if p.kind == "truecolor" or not p.index:
        return "True color"
    cmap = re.sub(r"^.*[:.]", "", p.colormap or "")
    return f"{p.index} · {cmap}{' (rev.)' if p.reversed else ''}"


def _template_fill(text: str, values: dict[str, object]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), text)


def _build_html(*, meta: BookMeta, plates: list[OverviewPlate], front_pages: int,
                frontis_jpg: Path, miniatures: tuple[Path, Path] | None) -> str:
    ov = book_overview(plates)
    start = plates_start_page(front_pages)
    rows = [PlateRow(p.plate_number, p.location_name, _look(p), theme_title(p.theme), start + 2 * i)
            for i, p in enumerate(plates)]
    overview_page = start + 2 * len(plates)
    counts = {
        "n_plates": len(plates), "n_places": len(ov.points),
        "n_indices": len(ov.by_index), "n_colormaps": len(ov.by_colormap),
        "years": ov.years or "", "platforms": _platform_phrase(ov.platforms),
    }
    first = plates[0].plate_number if plates else "001"
    last = plates[-1].plate_number if plates else "001"
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), undefined=StrictUndefined, autoescape=True)
    return env.get_template("front_matter.html.j2").render(
        css=(TEMPLATE_DIR / "book.css").read_text(),
        meta=meta,
        intro=[_template_fill(p, counts) for p in paragraphs(meta.introduction)],
        tally=" · ".join([_plural(len(plates), "plate"), _plural(len(ov.points), "place"),
                          _plural(len(ov.by_index), "index", "indices"),
                          _plural(len(ov.by_colormap), "colormap")]),
        credits=derive_credits(plates, meta),
        frontis_uri=frontis_jpg.resolve().as_uri(),
        frontis_caption=f"The {_plural(len(plates), 'scene')} of this book as their colormaps, in book order.",
        miniatures=[m.resolve().as_uri() for m in miniatures] if miniatures else None,
        plate_rows=rows,
        plates_range=f"{first}–{last}",
        plates_page=start,
        overview_page=overview_page,
    )


def render_front_matter(
    *,
    out_pdf: Path,
    meta: BookMeta,
    plates: list[OverviewPlate],
    work_dir: Path,
    first_spread_pdf: Path | None = None,
) -> tuple[Path, int]:
    """Render the front matter to ``out_pdf``; returns (path, page count)."""
    from weasyprint import HTML

    work_dir.mkdir(parents=True, exist_ok=True)
    frontis = render_frontispiece(plates, work_dir / "frontispiece.jpg")
    minis = render_miniatures(first_spread_pdf, work_dir) if first_spread_pdf else None

    pages = 11  # the designed sequence; corrected below if the copy reflows
    for _ in range(3):
        html = _build_html(meta=meta, plates=plates, front_pages=pages, frontis_jpg=frontis, miniatures=minis)
        doc = HTML(string=html, base_url=str(TEMPLATE_DIR)).render()
        if len(doc.pages) == pages:
            break
        pages = len(doc.pages)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.write_pdf(target=str(out_pdf))
    log.info("Wrote front matter (%d pages) → %s", len(doc.pages), out_pdf)
    return out_pdf, len(doc.pages)

