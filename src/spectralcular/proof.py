"""Assemble the book interior (Stage 6): front matter → plates → back matter.

Merges the per-spread PDFs (built by ``spectralcular build``) in book order — read from
the locked ``spreads.csv`` — into a single ``output/proof.pdf`` using PyMuPDF
(``fitz``). Page 1 is a recto, and every spread is plate (verso, even page) +
specimen card (recto, odd page), so the assembly keeps that parity explicitly:

* the front matter (:mod:`front_matter`) is followed by one blank page if it
  ends on a verso, so plate 001 opens on a verso;
* every spread must be exactly two pages;
* the overview spread (:mod:`overview`) is padded to open on a verso;
* the book ends on a verso (an even page count).

There are no section-divider pages between themes: a single divider page
would flip every following spread onto the wrong side.

``finish_pdfx`` is an optional, *experimental* Ghostscript pass, kept for
vendors that ask for PDF/X. Lulu does not (see :mod:`print_pdf`, which
``spectralcular print`` uses instead).
"""

from __future__ import annotations

import csv
import logging
import shutil
import subprocess
from pathlib import Path

import fitz  # PyMuPDF

log = logging.getLogger(__name__)

# Editorial paper tone, matching render_assets.PAPER (#f7f4ec).
_PAPER = (0xF7 / 255, 0xF4 / 255, 0xEC / 255)

# US Letter portrait + 0.125" bleed, in points (1 pt = 1/72").
_DEFAULT_W = 8.75 * 72
_DEFAULT_H = 11.25 * 72


def _read_rows(spreads_csv: Path) -> list[dict]:
    with spreads_csv.open(newline="") as f:
        rows = list(csv.DictReader(f))

    def _ord(r: dict) -> int:
        try:
            return int(r["ordinal"])
        except (KeyError, ValueError):
            return 10**9

    return sorted(rows, key=_ord)


def _blank(doc: "fitz.Document", width: float, height: float) -> None:
    page = doc.new_page(width=width, height=height)
    page.draw_rect(page.rect, color=None, fill=_PAPER)


def _pad_to_verso(doc: "fitz.Document", width: float, height: float) -> bool:
    """Add a blank paper page if the next page would be a recto. Page 1 is a
    recto, so the next page is a verso exactly when the count so far is odd."""
    if doc.page_count % 2 == 0:
        _blank(doc, width, height)
        return True
    return False


def build_proof(
    spreads_csv: Path,
    spread_pdf_dir: Path,
    out_pdf: Path,
    *,
    front_matter_pdf: Path | None = None,
    overview_pdf: Path | None = None,
) -> Path:
    """Merge front matter, the per-spread PDFs in ordinal order, and the
    back matter (``overview_pdf``, a two-page spread opening on a verso)."""
    rows = _read_rows(spreads_csv)
    if not rows:
        raise ValueError(f"No rows in {spreads_csv}")

    # Page size from the first available spread; else the Letter+bleed default.
    width, height = _DEFAULT_W, _DEFAULT_H
    for r in rows:
        p = spread_pdf_dir / f"{r['id']}.pdf"
        if p.exists():
            with fitz.open(p) as probe:
                if probe.page_count:
                    rect = probe[0].rect
                    width, height = rect.width, rect.height
            break

    doc = fitz.open()
    if front_matter_pdf is not None:
        with fitz.open(front_matter_pdf) as src:
            doc.insert_pdf(src)
    if _pad_to_verso(doc, width, height) and front_matter_pdf is not None:
        log.info("front matter ends on a verso; one blank page added before plate 001")

    inserted = 0
    missing: list[str] = []
    for r in rows:
        pdf_path = spread_pdf_dir / f"{r['id']}.pdf"
        if not pdf_path.exists():
            missing.append(r["id"])
            continue
        with fitz.open(pdf_path) as src:
            if src.page_count != 2:
                raise ValueError(f"{pdf_path.name} has {src.page_count} pages; a spread is exactly 2 "
                                 "(plate verso + specimen recto)")
            doc.insert_pdf(src)
        inserted += 1

    if missing:
        log.warning("%d spread PDF(s) missing (run `spectralcular build` first): %s",
                    len(missing), missing[:8])
    if inserted == 0:
        raise RuntimeError("No spread PDFs found to assemble; run `spectralcular build` first.")

    if overview_pdf is not None:
        _pad_to_verso(doc, width, height)
        with fitz.open(overview_pdf) as src:
            doc.insert_pdf(src)

    if doc.page_count % 2:  # end on a verso: the last leaf is whole
        _blank(doc, width, height)

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_pdf, deflate=True, garbage=4)
    pages = doc.page_count
    doc.close()
    log.info("Wrote proof: %d spread(s), %d pages → %s", inserted, pages, out_pdf)
    return out_pdf


# ---------------------------------------------------------------------------
# Optional Ghostscript PDF/X finishing (experimental)
# ---------------------------------------------------------------------------

def finish_pdfx(in_pdf: Path, out_pdf: Path, *, gs_bin: str | None = None) -> Path:
    """Re-distill ``in_pdf`` at prepress quality, RGB-preserving, via Ghostscript.

    Experimental. Produces a normalized, sRGB-preserving PDF suitable as a
    starting point for PDF/X preflight — NOT a validated PDF/X-1a. Validate
    against the vendor's spec before submission.
    """
    gs = gs_bin or shutil.which("gs")
    if gs is None:
        raise RuntimeError("Ghostscript ('gs') not found on PATH; cannot run --pdfx.")

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        gs,
        "-dBATCH", "-dNOPAUSE", "-dSAFER",
        "-sDEVICE=pdfwrite",
        "-dPDFSETTINGS=/prepress",
        "-dCompatibilityLevel=1.4",
        "-sColorConversionStrategy=RGB",
        "-dProcessColorModel=/DeviceRGB",
        "-dEmbedAllFonts=true",
        "-dSubsetFonts=true",
        f"-sOutputFile={out_pdf}",
        str(in_pdf),
    ]
    log.info("Running Ghostscript finishing pass: %s", " ".join(cmd))
    log.warning("This produces an sRGB-preserving prepress PDF, NOT a validated "
                "PDF/X-1a:2001. Confirm against your print vendor's preflight before printing.")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Ghostscript failed (exit {result.returncode}):\n{result.stderr}")
    return out_pdf
