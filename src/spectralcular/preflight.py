"""Check a finished interior or cover PDF against the vendor's file rules.

Each check is ``pass`` / ``warn`` / ``fail`` with a one-line detail; the
rules come from ``config/book_specs.yaml`` (Lulu's Book Creation Guide, its
PDF presets and help-centre requirements — see the comments there). This is
not a substitute for the vendor's own upload check or a printed proof; it
catches what this pipeline can get wrong.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

from .book_specs import BookSpecs
from .print_pdf import _transparency_users, colorspace_of

_TOL_PT = 0.75   # page-size tolerance
_PPI_TOL = 0.02  # ±2 % around the target counts as "at" it


@dataclass
class Check:
    name: str
    status: str   # pass | warn | fail
    detail: str


@dataclass
class Report:
    path: Path
    kind: str
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool | str, detail: str) -> None:
        status = ok if isinstance(ok, str) else ("pass" if ok else "fail")
        self.checks.append(Check(name, status, detail))

    @property
    def status(self) -> str:
        s = {c.status for c in self.checks}
        return "fail" if "fail" in s else "warn" if "warn" in s else "pass"

    def markdown(self) -> str:
        mark = {"pass": "✓", "warn": "!", "fail": "✗"}
        lines = [f"## {self.kind.title()}: `{self.path.name}` — {self.status.upper()}", ""]
        lines += [f"- {mark[c.status]} **{c.name}** — {c.detail}" for c in self.checks]
        return "\n".join(lines) + "\n"


def _images(doc: fitz.Document) -> list[tuple[int, int, float, float, str, bool, str]]:
    """(page, xref, ppi_x, ppi_y, filter, has_smask, colorspace) per placement."""
    out = []
    for page in doc:
        for info in page.get_image_info(xrefs=True):
            a, b, c, d, _e, _f = info["transform"]
            w_in, h_in = math.hypot(a, b) / 72, math.hypot(c, d) / 72
            if w_in <= 0 or h_in <= 0:
                continue
            x = info.get("xref") or 0
            filt = doc.xref_get_key(x, "Filter")[1] if x else "inline"
            smask = doc.xref_get_key(x, "SMask")[0] != "null" if x else False
            cs = colorspace_of(doc, x) if x else "?"
            out.append((page.number + 1, x, info["width"] / w_in, info["height"] / h_in, filt, smask, cs))
    return out


def _is_blank(page: fitz.Page) -> bool:
    return not page.get_text().strip() and not page.get_images() and len(page.get_drawings()) <= 1


def _common_checks(rep: Report, doc: fitz.Document, path: Path, specs: BookSpecs, *, safety_in: float) -> None:
    pp = specs.print_pdf
    rep.add("encryption", not doc.is_encrypted and not doc.needs_pass, "no password or security")

    imgs = _images(doc)
    if imgs:
        lo = min(min(i[2], i[3]) for i in imgs)
        hi = max(max(i[2], i[3]) for i in imgs)
        low = [i for i in imgs if min(i[2], i[3]) < pp.min_image_ppi * (1 - _PPI_TOL)]
        high = [i for i in imgs if max(i[2], i[3]) > pp.max_image_ppi]
        off = [i for i in imgs if abs(i[2] - pp.image_ppi) > pp.image_ppi * _PPI_TOL
               or abs(i[3] - pp.image_ppi) > pp.image_ppi * _PPI_TOL]
        detail = f"{len(imgs)} placements, {lo:.0f}–{hi:.0f} ppi"
        if low or high:
            rep.add("image resolution", False, detail + f"; {len(low)} below {pp.min_image_ppi}, "
                    f"{len(high)} above {pp.max_image_ppi} (first: page {(low or high)[0][0]})")
        else:
            rep.add("image resolution", "pass" if not off else "warn",
                    detail + (f"; {len(off)} not at {pp.image_ppi}" if off else f"; all at {pp.image_ppi}"))
        not_jpeg = [i for i in imgs if i[4] != "/DCTDecode"]
        rep.add("image encoding", "pass" if not not_jpeg else "warn",
                "all JPEG" if not not_jpeg else f"{len(not_jpeg)} not JPEG (first: page {not_jpeg[0][0]}, {not_jpeg[0][4]})")
        untagged = [i for i in imgs if "ICCBased" not in i[6]]
        rep.add("image color space", "pass" if not untagged else "warn",
                "all sRGB (ICC)" if not untagged else f"{len(untagged)} untagged ({untagged[0][6]}); read as sRGB")
    else:
        rep.add("image resolution", "pass", "no raster images")

    transp = _transparency_users(doc)
    groups = 0
    for x in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(x, compressed=True)
        except Exception:  # noqa: BLE001 — free xref slots
            continue
        groups += "/Group" in obj and "/Transparency" in obj
    rep.add("transparency", not transp and not groups,
            "none" if not transp and not groups else f"{len(transp)} transparent object(s), {groups} transparency group(s)")

    unembedded = sorted({f[3] for page in doc for f in page.get_fonts() if f[1] == "n/a"})
    rep.add("fonts", not unembedded, "all embedded" if not unembedded else f"not embedded: {', '.join(unembedded)}")

    cat = doc.xref_object(doc.pdf_catalog())
    rep.add("output intent", "pass" if "/OutputIntents" in cat else "warn",
            "sRGB IEC61966-2.1" if "/OutputIntents" in cat else "none (RGB is read as sRGB)")

    # Text size and the safety margin.
    small5 = small_min = 0
    outside = 0
    first_outside = None
    edge = (specs.cover_edge_in if rep.kind == "cover" else specs.bleed_in) * 72 + safety_in * 72
    for page in doc:
        r = page.rect
        safe = fitz.Rect(r.x0 + edge, r.y0 + edge, r.x1 - edge, r.y1 - edge)
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if not span["text"].strip():
                        continue
                    small5 += span["size"] < 4.95
                    small_min += span["size"] < specs.page_rules.min_text_pt - 0.05
                    if not safe.contains(fitz.Rect(span["bbox"])):
                        outside += 1
                        first_outside = first_outside or (page.number + 1, span["text"][:30])
    rep.add("text size", "warn" if small5 or small_min else "pass",
            f"{small5} span(s) under 5 pt (upload warning), {small_min} under "
            f"{specs.page_rules.min_text_pt:g} pt (Global Distribution minimum)")
    rep.add("safety margin", "pass" if not outside else "warn",
            f"all text ≥ {safety_in:g} in inside the trim" if not outside else
            f"{outside} text span(s) inside the {safety_in:g} in margin (first: page {first_outside[0]}, "
            f"\"{first_outside[1]}\")")

    mb = path.stat().st_size / 1e6
    rep.add("file size", "fail" if mb > pp.file_size_max_mb else "warn" if mb > pp.file_size_warn_mb else "pass",
            f"{mb:,.0f} MB (Lulu: keep under {pp.file_size_warn_mb:g} MB; {pp.file_size_max_mb:g} MB ceiling)")


def preflight_interior(path: Path, specs: BookSpecs, *, plate_pages: list[int] | None = None) -> Report:
    rep = Report(path, "interior")
    doc = fitz.open(path)
    rules = specs.page_rules
    n = doc.page_count
    rep.add("page count", rules.min_pages <= n <= rules.max_pages and n % 2 == 0,
            f"{n} pages (perfect bound: {rules.min_pages}–{rules.max_pages}, even)")
    rep.add("multiple of 4", "pass" if n % 4 == 0 else "warn",
            "yes" if n % 4 == 0 else f"{n} is not; the printer may add {4 - n % 4} blank page(s) at the back")

    want_w, want_h = specs.plate_width_in * 72, specs.plate_height_in * 72
    bad = [p.number + 1 for p in doc if abs(p.rect.width - want_w) > _TOL_PT or abs(p.rect.height - want_h) > _TOL_PT]
    rep.add("page size", not bad, f"all {specs.plate_width_in:g} × {specs.plate_height_in:g} in (trim + bleed)"
            if not bad else f"{len(bad)} page(s) off-size (first: {bad[0]})")
    inset = specs.bleed_in * 72
    trims = [p.number + 1 for p in doc if abs(p.trimbox.x0 - p.mediabox.x0 - inset) > _TOL_PT]
    rep.add("trim box", "pass" if not trims else "warn",
            f"TrimBox {specs.bleed_in:g} in inside the page" if not trims else f"{len(trims)} page(s) without it")

    run = longest = 0
    for p in doc:
        run = run + 1 if _is_blank(p) else 0
        longest = max(longest, run)
    rep.add("blank pages", "pass" if longest <= rules.max_consecutive_blank else "warn",
            f"longest run {longest} (Global Distribution: ≤ {rules.max_consecutive_blank})")

    if plate_pages:
        wrong = [p for p in plate_pages if p % 2]
        rep.add("plates on versos", not wrong, f"all {len(plate_pages)} plate pages even (left)"
                if not wrong else f"{len(wrong)} plate page(s) on a recto (first: {wrong[0]})")

    band = specs.gutter_for(n)
    if band is not None:
        rep.add("gutter", "pass", f"{n} pages → inner margin ≥ {band.recommended_inner_margin_in:g} in "
                f"({band.full_bleed_inner_margin_in:g} in on full-bleed pages); book.css uses 36 mm (1.42 in)"
                if band.full_bleed_inner_margin_in <= 36 / 25.4 - specs.bleed_in else
                f"{n} pages needs {band.full_bleed_inner_margin_in:g} in; book.css's 36 mm is short")

    _common_checks(rep, doc, path, specs, safety_in=specs.safety_margin_in)
    doc.close()
    return rep


def preflight_cover(path: Path, specs: BookSpecs, interior_pages: int) -> Report:
    rep = Report(path, "cover")
    doc = fitz.open(path)
    rep.add("single page", doc.page_count == 1, f"{doc.page_count} page(s)")
    w, h = specs.cover_document_for(interior_pages)
    r = doc[0].rect
    rep.add("cover size", abs(r.width - w * 72) <= _TOL_PT and abs(r.height - h * 72) <= _TOL_PT,
            f"{r.width / 72:.3f} × {r.height / 72:.3f} in; {interior_pages} pages, {specs.cover.type} → "
            f"{w:.3f} × {h:.3f} in (spine {specs.spine_width_for(interior_pages):.3f} in)")
    safety = specs.cover.safety_margin_in if specs.cover.safety_margin_in is not None else specs.safety_margin_in
    _common_checks(rep, doc, path, specs, safety_in=safety)
    doc.close()
    return rep


def plate_pages_of(doc_path: Path) -> list[int]:
    """Pages whose image fills the whole page (the plates)."""
    out = []
    with fitz.open(doc_path) as doc:
        for page in doc:
            area = page.rect.width * page.rect.height
            for info in page.get_image_info():
                bb = fitz.Rect(info["bbox"]) & page.rect
                if bb.width * bb.height >= 0.98 * area:
                    out.append(page.number + 1)
                    break
    return out
