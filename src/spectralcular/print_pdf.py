"""Finish an assembled PDF for print-on-demand upload.

Lulu names no PDF/X or PDF/A standard; its own Distiller presets
(``Lulu-Interior-Print-PDF.joboptions`` / ``…-Cover-…``) are the spec we
follow, and the Book Creation Guide's file checklist:

* color left as **sRGB** (``ColorConversionStrategy /LeaveColorUnchanged``,
  RGB profile ``sRGB IEC61966-2.1``) — no CMYK conversion here;
* **no transparency** (``AllowTransparency false``, "transparent layers …
  are flattened");
* images at **300 ppi** (downsampled above 450, minimum 300, maximum 600),
  **JPEG at maximum quality without chroma subsampling**;
* fonts embedded; no printer marks; no security.

:func:`finish_for_print` enforces those on a WeasyPrint/PyMuPDF document:
every raster is re-encoded at exactly ``ppi`` for its largest placement,
alpha composited over the paper, and tagged with an embedded sRGB profile;
WeasyPrint's isolated transparency groups (it wraps every SVG in one, even
when nothing inside is translucent) are dropped once nothing transparent is
left to group; TrimBox/BleedBox are set; an sRGB output intent and document
metadata are written. :mod:`preflight` then checks the result.
"""

from __future__ import annotations

import io
import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from .print_image import JPEG_QUALITY, PAPER_RGB, PRINT_PPI, flatten, jpeg_bytes, resample, srgb_icc

log = logging.getLogger(__name__)

_ALPHA_RE = re.compile(r"/(ca|CA)\s*(0?\.\d+|0)(?![\d.])")
_BLEND_RE = re.compile(r"/BM\s*/(?!Normal\b|Compatible\b)\w+")


@dataclass
class FinishReport:
    images_reencoded: int = 0
    images_flattened: int = 0
    images_skipped: list[str] = field(default_factory=list)
    groups_removed: int = 0
    residual_transparency: list[str] = field(default_factory=list)
    bytes_out: int = 0

    def summary(self) -> str:
        s = (f"{self.images_reencoded} image(s) re-encoded at print ppi "
             f"({self.images_flattened} flattened), {self.groups_removed} transparency group(s) removed")
        if self.images_skipped:
            s += f", {len(self.images_skipped)} inline image(s) left as-is"
        if self.residual_transparency:
            s += f"; TRANSPARENCY REMAINS in {len(self.residual_transparency)} object(s)"
        return s


def _placements(doc: fitz.Document) -> tuple[dict[int, tuple[float, float]], list[str]]:
    """Largest placed size (inches) of every image xref, and inline images seen."""
    sizes: dict[int, tuple[float, float]] = defaultdict(lambda: (0.0, 0.0))
    inline: list[str] = []
    for page in doc:
        for info in page.get_image_info(xrefs=True):
            a, b, c, d, _e, _f = info["transform"]
            w_in, h_in = math.hypot(a, b) / 72, math.hypot(c, d) / 72
            if w_in <= 0 or h_in <= 0:
                continue
            x = info.get("xref") or 0
            if not x:
                inline.append(f"page {page.number + 1}: {info['width']}×{info['height']} inline image")
                continue
            pw, ph = sizes[x]
            sizes[x] = (max(pw, w_in), max(ph, h_in))
    return dict(sizes), inline


def _pil_image(doc: fitz.Document, xref: int) -> Image.Image:
    """The image at ``xref`` as PIL, its soft mask (if any) as alpha."""
    pix = fitz.Pixmap(doc, xref)
    if pix.n - pix.alpha >= 4:  # CMYK etc. → RGB
        pix = fitz.Pixmap(fitz.csRGB, pix)
    smask = doc.xref_get_key(xref, "SMask")
    if smask[0] == "xref":
        mask = fitz.Pixmap(doc, int(smask[1].split()[0]))
        if pix.alpha:
            pix = fitz.Pixmap(pix, 0)
        pix = fitz.Pixmap(pix, mask)
    mode = {1: "L", 2: "LA", 3: "RGB", 4: "RGBA"}[pix.n]
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def colorspace_of(doc: fitz.Document, xref: int) -> str:
    """An image's /ColorSpace as text, indirect references resolved."""
    kind, val = doc.xref_get_key(xref, "ColorSpace")
    if kind == "xref":
        return doc.xref_object(int(val.split()[0]), compressed=True)
    return val


def _is_print_jpeg(doc: fitz.Document, xref: int, width: int, height: int, tol_px: int = 2) -> bool:
    key = doc.xref_get_key
    return (key(xref, "Filter")[1] == "/DCTDecode"
            and key(xref, "SMask")[0] == "null"
            and (colorspace_of(doc, xref) in ("/DeviceRGB", "/DeviceGray") or "ICCBased" in colorspace_of(doc, xref))
            and abs(int(key(xref, "Width")[1]) - width) <= tol_px
            and abs(int(key(xref, "Height")[1]) - height) <= tol_px)


def _has_alpha(img: Image.Image) -> bool:
    if img.mode not in ("RGBA", "LA"):
        return False
    lo, _hi = img.getchannel("A").getextrema()
    return lo < 255


def _replace_with_jpeg(doc: fitz.Document, xref: int, img: Image.Image, icc_xref: int, quality: int) -> None:
    data = jpeg_bytes(img, quality=quality)
    # Stream first: update_stream rewrites the dictionary's /Filter, so the
    # dictionary (declaring the JPEG as DCTDecode) has to be written after it.
    doc.update_stream(xref, data, compress=False)
    doc.update_object(
        xref,
        f"<</Type/XObject/Subtype/Image/Width {img.width}/Height {img.height}"
        f"/ColorSpace[/ICCBased {icc_xref} 0 R]/BitsPerComponent 8/Filter/DCTDecode"
        f"/Length {len(data)}>>",
    )


def _transparency_users(doc: fitz.Document) -> list[str]:
    found = []
    for x in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(x, compressed=True)
        except Exception:  # noqa: BLE001 — free/broken xref slots
            continue
        if "/SMask" in obj and "/SMask/None" not in obj and "/SMask /None" not in obj:
            found.append(f"xref {x}: soft mask")
        elif _ALPHA_RE.search(obj) and any(float(v) < 1 for _k, v in _ALPHA_RE.findall(obj)):
            found.append(f"xref {x}: constant alpha < 1")
        elif _BLEND_RE.search(obj):
            found.append(f"xref {x}: blend mode")
    return found


def _strip_groups(doc: fitz.Document) -> int:
    n = 0
    for x in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(x, compressed=True)
        except Exception:  # noqa: BLE001
            continue
        if "/Group" in obj and "/Transparency" in obj:
            doc.xref_set_key(x, "Group", "null")
            n += 1
    return n


def _add_output_intent(doc: fitz.Document) -> int:
    icc = doc.get_new_xref()
    doc.update_object(icc, "<</N 3/Alternate/DeviceRGB>>")
    doc.update_stream(icc, srgb_icc())
    oi = doc.get_new_xref()
    doc.update_object(
        oi,
        f"<</Type/OutputIntent/S/GTS_PDFX/OutputConditionIdentifier(sRGB IEC61966-2.1)"
        f"/RegistryName(http://www.color.org)/Info(sRGB IEC61966-2.1)/DestOutputProfile {icc} 0 R>>",
    )
    doc.xref_set_key(doc.pdf_catalog(), "OutputIntents", f"[{oi} 0 R]")
    return icc


def finish_doc(
    doc: fitz.Document,
    *,
    ppi: int = PRINT_PPI,
    quality: int = JPEG_QUALITY,
    bleed_in: float = 0.125,
    background: tuple[int, int, int] = PAPER_RGB,
    metadata: dict[str, str] | None = None,
) -> FinishReport:
    """Apply the print rules to ``doc`` in place (see module docstring)."""
    rep = FinishReport()
    icc_xref = _add_output_intent(doc)

    sizes, rep.images_skipped = _placements(doc)
    for xref, (w_in, h_in) in sizes.items():
        tw, th = max(1, round(w_in * ppi)), max(1, round(h_in * ppi))
        if _is_print_jpeg(doc, xref, tw, th):
            # Already an opaque JPEG at print size (e.g. the flattened plate
            # page): re-encoding would only add a JPEG generation. Tag it
            # sRGB unless it already carries a profile.
            if "ICCBased" not in colorspace_of(doc, xref):
                doc.xref_set_key(xref, "ColorSpace", f"[/ICCBased {icc_xref} 0 R]")
            continue
        img = _pil_image(doc, xref)
        if _has_alpha(img):
            rep.images_flattened += 1
        img = flatten(img, background)
        img = resample(img, tw, th)
        _replace_with_jpeg(doc, xref, img, icc_xref, quality)
        rep.images_reencoded += 1

    rep.residual_transparency = _transparency_users(doc)
    if not rep.residual_transparency:
        rep.groups_removed = _strip_groups(doc)
    else:
        log.warning("Transparency left in %d object(s); keeping transparency groups: %s",
                    len(rep.residual_transparency), rep.residual_transparency[:5])

    inset = bleed_in * 72
    for page in doc:
        r = page.mediabox
        page.set_bleedbox(r)
        page.set_trimbox(fitz.Rect(r.x0 + inset, r.y0 + inset, r.x1 - inset, r.y1 - inset))

    meta = dict(doc.metadata or {})
    meta.update({"creator": "spectralcular", "producer": f"PyMuPDF {fitz.VersionBind}"})
    meta.update(metadata or {})
    doc.set_metadata({k: v for k, v in meta.items() if k in (
        "title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate")})
    return rep


def finish_for_print(in_pdf: Path, out_pdf: Path, **kw) -> FinishReport:
    """Read ``in_pdf``, :func:`finish_doc` it, write ``out_pdf``."""
    doc = fitz.open(in_pdf)
    rep = finish_doc(doc, **kw)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True)
    doc.close()
    out_pdf.write_bytes(buf.getvalue())
    rep.bytes_out = out_pdf.stat().st_size
    log.info("Finished %s → %s: %s", in_pdf.name, out_pdf, rep.summary())
    return rep
