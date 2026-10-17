"""Print readiness: image rules, the finishing pass, page parity, front
matter, cover geometry and the preflight (Lulu's file rules)."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import fitz
import matplotlib
import numpy as np
import pytest
from PIL import Image

from spectralcular.book_meta import BookMeta
from spectralcular.book_specs import load_book_specs
from spectralcular.cover import cover_geometry, mosaic_grid
from spectralcular.front_matter import colormap_collection, derive_credits, plates_start_page
from spectralcular.overview import OverviewPlate
from spectralcular.preflight import plate_pages_of, preflight_interior
from spectralcular.print_image import PAPER_RGB, cover_fit, flatten, jpeg_bytes
from spectralcular.print_pdf import finish_doc, finish_for_print
from spectralcular.proof import build_proof
from spectralcular.render_assets import _blend, _fill_polygon_on_raster

ROOT = Path(__file__).resolve().parents[1]
SPECS = load_book_specs(ROOT / "config" / "book_specs.yaml")
PAGE_W, PAGE_H = 8.75 * 72, 11.25 * 72
DEJAVU = str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf")


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# print_image
# ---------------------------------------------------------------------------

def test_flatten_composites_alpha_over_paper():
    img = Image.new("RGBA", (4, 4), (255, 0, 0, 0))
    assert flatten(img).getpixel((0, 0)) == PAPER_RGB
    half = Image.new("RGBA", (1, 1), (0, 0, 0, 128))
    r, _g, _b = flatten(half, (255, 255, 255)).getpixel((0, 0))
    assert 125 <= r <= 129


def test_cover_fit_crops_to_aspect_then_scales():
    img = Image.new("RGB", (200, 100))
    assert cover_fit(img, 50, 50).size == (50, 50)


def test_print_jpeg_is_444_with_srgb_profile():
    data = jpeg_bytes(Image.new("RGB", (16, 16), (10, 200, 30)))
    im = Image.open(io.BytesIO(data))
    assert im.format == "JPEG" and im.info.get("icc_profile")
    assert im.layer[0][1:3] == im.layer[1][1:3]  # no chroma subsampling


# ---------------------------------------------------------------------------
# print_pdf
# ---------------------------------------------------------------------------

def _doc_with_alpha_image(ppi: int = 600) -> fitz.Document:
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    rgba = Image.new("RGBA", (ppi, ppi), (0, 0, 255, 255))
    rgba.paste((0, 0, 0, 0), (0, 0, ppi // 2, ppi))  # left half transparent
    page.insert_image(fitz.Rect(72, 72, 144, 144), stream=_png(rgba))  # 1 × 1 in
    doc.xref_set_key(page.xref, "Group", "<</S/Transparency/CS/DeviceRGB>>")
    return doc


def test_finish_doc_resamples_flattens_tags_and_boxes():
    doc = _doc_with_alpha_image(600)
    rep = finish_doc(doc, ppi=300)
    assert rep.images_reencoded == 1 and rep.images_flattened == 1
    assert rep.groups_removed >= 1 and not rep.residual_transparency
    page = doc[0]
    (info,) = page.get_image_info(xrefs=True)
    x = info["xref"]
    assert (info["width"], info["height"]) == (300, 300)
    assert doc.xref_get_key(x, "Filter")[1] == "/DCTDecode"
    assert doc.xref_get_key(x, "SMask")[0] == "null"
    assert "ICCBased" in doc.xref_get_key(x, "ColorSpace")[1]
    assert "/OutputIntents" in doc.xref_object(doc.pdf_catalog())
    assert page.trimbox == fitz.Rect(9, 9, PAGE_W - 9, PAGE_H - 9)
    # transparent half now shows the paper, the opaque half stays blue
    pix = page.get_pixmap(dpi=72, clip=fitz.Rect(72, 72, 144, 144))
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    assert np.abs(arr[36, 10, :3].astype(int) - PAPER_RGB).max() <= 6
    assert arr[36, 60, 2] > 200 and arr[36, 60, 0] < 40


def test_finish_keeps_print_ready_jpeg_untouched(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    data = jpeg_bytes(Image.new("RGB", (2625, 3375), (90, 140, 60)))
    page.insert_image(page.rect, stream=data)
    src = tmp_path / "in.pdf"
    doc.save(src)
    rep = finish_for_print(src, tmp_path / "out.pdf")
    assert rep.images_reencoded == 0
    with fitz.open(tmp_path / "out.pdf") as out:
        (info,) = out[0].get_image_info(xrefs=True)
        assert len(out.xref_stream_raw(info["xref"])) == len(data)


# ---------------------------------------------------------------------------
# proof parity
# ---------------------------------------------------------------------------

def _pdf(path: Path, pages: int, label: str = "") -> Path:
    doc = fitz.open()
    for i in range(pages):
        p = doc.new_page(width=PAGE_W, height=PAGE_H)
        if label:
            p.insert_text((72, 72), f"{label}{i}")
    doc.save(path)
    return path


def _spreads(tmp_path: Path, n: int) -> tuple[Path, Path]:
    d = tmp_path / "spreads"
    d.mkdir()
    rows = []
    for i in range(1, n + 1):
        _pdf(d / f"p{i}.pdf", 2, f"plate{i}-")
        rows.append({"ordinal": i, "id": f"p{i}", "theme": "water" if i % 2 else "burn"})
    csv_path = tmp_path / "spreads.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ordinal", "id", "theme"])
        w.writeheader()
        w.writerows(rows)
    return csv_path, d


@pytest.mark.parametrize("front_pages", [0, 4, 5, 11])
def test_plates_always_open_on_versos_and_book_ends_even(tmp_path, front_pages):
    csv_path, d = _spreads(tmp_path, 3)
    front = _pdf(tmp_path / "fm.pdf", front_pages) if front_pages else None
    overview = _pdf(tmp_path / "ov.pdf", 2, "overview-")
    out = build_proof(csv_path, d, tmp_path / "proof.pdf", front_matter_pdf=front, overview_pdf=overview)
    with fitz.open(out) as doc:
        text = [p.get_text() for p in doc]
        assert doc.page_count % 2 == 0
    plate_pages = [i + 1 for i, t in enumerate(text) if "-0" in t and "plate" in t]
    assert len(plate_pages) == 3 and all(p % 2 == 0 for p in plate_pages)
    assert plate_pages[0] == plates_start_page(front_pages)
    overview_page = next(i + 1 for i, t in enumerate(text) if "overview-0" in t)
    assert overview_page % 2 == 0
    # no theme-divider pages between the spreads
    assert plate_pages == [plate_pages[0] + 2 * k for k in range(3)]


def test_a_spread_must_be_two_pages(tmp_path):
    csv_path, d = _spreads(tmp_path, 2)
    _pdf(d / "p2.pdf", 3)
    with pytest.raises(ValueError, match="exactly 2"):
        build_proof(csv_path, d, tmp_path / "proof.pdf")


# ---------------------------------------------------------------------------
# book specs, cover, front matter
# ---------------------------------------------------------------------------

def test_gutter_band_follows_actual_page_count():
    assert SPECS.gutter_for(214).full_bleed_inner_margin_in == 1.125
    assert SPECS.gutter_for(500).full_bleed_inner_margin_in == 1.25
    assert SPECS.gutter_for(900).full_bleed_inner_margin_in == 1.375
    # the design's 36 mm side padding clears every band up to 600 pages
    assert SPECS.gutter_for(600).full_bleed_inner_margin_in + SPECS.bleed_in <= 36 / 25.4


def test_casewrap_cover_geometry():
    g = cover_geometry(SPECS, 214)
    assert (g.width_in, g.height_in, g.spine_in) == pytest.approx((19.5, 12.75, 0.75))
    bx, by, bw, bh = g.back_box()
    fx, fy, fw, _fh = g.front_box()
    assert (bx, by) == pytest.approx((1.25, 1.25))           # 0.75 wrap + 0.5 safety
    assert (bw, bh) == pytest.approx((7.625, 10.25))          # 8.625 × 11.25 board less safety
    assert bx + bw == pytest.approx(g.spine_x0 - 0.5)         # 0.5 in from the spine fold
    assert fx == pytest.approx(g.front_x0 + 0.5)
    assert fx + fw == pytest.approx(g.width_in - 1.25)


def test_paperback_cover_geometry_matches_lulu_formula():
    import yaml
    from spectralcular.book_specs import BookSpecs
    raw = yaml.safe_load((ROOT / "config" / "book_specs.yaml").read_text())
    raw["cover"]["type"] = "paperback_perfect_bind"
    g = cover_geometry(BookSpecs.model_validate(raw), 256)
    assert g.spine_in == pytest.approx(256 / 444 + 0.06)
    assert g.width_in == pytest.approx(17.25 + 256 / 444 + 0.06)
    assert g.height_in == pytest.approx(11.25)
    bx, by, bw, _bh = g.back_box()
    fx, *_ = g.front_box()
    assert bx == pytest.approx(0.625) and by == pytest.approx(0.625)
    assert bx + bw == pytest.approx(g.spine_x0 - 0.5)
    assert fx == pytest.approx(g.front_x0 + 0.5)


def test_mosaic_grid_fits_every_plate():
    cols, rows, tw = mosaic_grid(100, 7.0, 7.0, 8.75 / 11.25)
    assert cols * rows >= 100
    assert cols * tw + (cols - 1) * 0.05 <= 7.0 + 1e-9
    assert rows * tw / (8.75 / 11.25) + (rows - 1) * 0.05 <= 7.0 + 1e-9


def test_plates_start_on_first_verso_after_front_matter():
    assert plates_start_page(11) == 12
    assert plates_start_page(10) == 12
    assert plates_start_page(0) == 2


def _op(i: int, colormap: str, platform="sentinel-2-l2a", date="2023-07-01") -> OverviewPlate:
    return OverviewPlate(ordinal=i, plate_number=f"{i:03d}", kind="index", theme="water", location_id=f"l{i}",
                         location_name=f"L{i}", lon=0, lat=0, platform=platform, index="NDVI",
                         colormap=colormap, date=date)


def test_colormap_collections():
    assert colormap_collection("colorcurious:crameri.roma") == "crameri"
    assert colormap_collection("cmocean:delta") == "cmocean"
    assert colormap_collection("bids:viridis") == "matplotlib"
    assert colormap_collection("custom") == "custom"


def test_credits_follow_the_plates():
    plates = [_op(1, "cmocean:delta", date="2017-08-08"), _op(2, "colorcurious:carto.Emrld", date="2026-03-25")]
    blocks = {b.heading: b.lines for b in derive_credits(plates, BookMeta(extra_credits=["Thanks."]))}
    assert blocks["Imagery"][0] == "Contains modified Copernicus Sentinel data 2017–2026."
    assert any("Earth Search" in line for line in blocks["Imagery"])
    assert [line.split(":")[0] for line in blocks["Colormaps"]] == ["carto", "cmocean"]
    assert "CC-BY-4.0" in blocks["Colormaps"][0]           # CARTOColors needs attribution
    assert "OpenStreetMap" in " ".join(blocks["Maps"])
    assert blocks["Making"][-1] == "Thanks."


# ---------------------------------------------------------------------------
# chart transparency
# ---------------------------------------------------------------------------

def test_blend_is_alpha_over_paper():
    assert _blend("#000000", 0.0) == "#f7f4ec"
    assert _blend("#9a3324", 1.0) == "#9a3324"


def test_aoi_fill_is_baked_into_the_basemap():
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    ring = np.array([(0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75)])
    out = _fill_polygon_on_raster(img, ring, (0.0, 1.0, 0.0, 1.0), "#000000", 0.30)
    assert out.mode == "RGB"
    assert out.getpixel((50, 50))[0] == pytest.approx(255 * 0.7, abs=2)
    assert out.getpixel((5, 5)) == (255, 255, 255)


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------

def test_preflight_passes_a_finished_book_and_flags_problems(tmp_path):
    doc = fitz.open()
    for i in range(32):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        if i % 2 == 1:  # plates on even page numbers
            page.insert_image(page.rect, stream=jpeg_bytes(Image.new("RGB", (2625, 3375), (20, 90, 160))))
        page.insert_text((72, 300), "Some body text", fontsize=9, fontname="dejavu", fontfile=DEJAVU)
    src = tmp_path / "book.pdf"
    doc.save(src)
    out = tmp_path / "book-print.pdf"
    finish_for_print(src, out)
    rep = preflight_interior(out, SPECS, plate_pages=plate_pages_of(out))
    status = {c.name: c.status for c in rep.checks}
    assert rep.status == "pass", rep.markdown()
    assert status["plates on versos"] == "pass"

    bad = fitz.open()
    for _ in range(33):
        bad.new_page(width=PAGE_W, height=PAGE_H)
    bad.save(tmp_path / "odd.pdf")
    rep = preflight_interior(tmp_path / "odd.pdf", SPECS)
    assert {c.name: c.status for c in rep.checks}["page count"] == "fail"
