"""Physical book specification (``config/book_specs.yaml``) with derived geometry.

The YAML holds the *inputs* a print-on-demand vendor asks you to pick (trim,
bleed, DPI, paper, binding, page count, spine formula). Everything that follows
from them — plate size in pixels, the AOI ceiling at the source GSD, spine
width, cover document size — is **computed here**, so changing the trim or the
page count can never leave a stale literal behind. If the YAML also states a
derived value (kept for readability) it is checked against the computation.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Tolerances for the optional stated-vs-derived checks.
_KM_TOL = 0.01
_IN_TOL = 0.005


class Vendor(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = "lulu"
    spec_sheet_url: str = ""
    pod_package_id: str | None = None  # vendor SKU for the interior/cover choice, if any
    notes: str = ""


class Trim(BaseModel):
    page_size: str = "us_letter"
    width_in: float
    height_in: float
    orientation: str = "portrait"


class Gutter(BaseModel):
    model_config = ConfigDict(extra="allow")
    page_count_band: str = ""
    add_to_inner_margin_in: float = 0.0
    recommended_inner_margin_in: float
    full_bleed_inner_margin_in: float


class GutterBand(BaseModel):
    """One row of the vendor's gutter table; ``max_pages=None`` is the open top band."""
    max_pages: int | None
    add_to_inner_margin_in: float
    recommended_inner_margin_in: float
    full_bleed_inner_margin_in: float


class PageRules(BaseModel):
    min_pages: int = 32
    max_pages: int = 800
    max_consecutive_blank: int = 2
    min_text_pt: float = 7.0


class PrintPdf(BaseModel):
    model_config = ConfigDict(extra="allow")
    color: str = "sRGB"
    image_ppi: int = 300
    min_image_ppi: int = 300
    max_image_ppi: int = 600
    jpeg_quality: int = 90
    flatten_transparency: bool = True
    file_size_warn_mb: float = 500
    file_size_max_mb: float = 1000


class BarcodeArea(BaseModel):
    width_in: float = 2.5
    height_in: float = 1.7
    inset_from_trim_in: float = 0.5
    required: bool = False


class PageCount(BaseModel):
    signature_multiple: int = 4
    target: int


class SpineFormula(BaseModel):
    """``spine_in = page_count * per_page_in + offset_in`` (vendor supplied)."""
    per_page_in: float
    offset_in: float


class SpineStep(BaseModel):
    """One row of a page-range spine table: up to ``max_pages`` → ``spine_in``."""
    max_pages: int
    spine_in: float


PAPERBACK = "paperback_perfect_bind"
CASEWRAP = "hardcover_casewrap"


class Cover(BaseModel):
    """The one-piece cover document.

    * ``paperback_perfect_bind``: trim-size panels + ``bleed_in`` on the outer
      edges; spine from ``spine_formula``.
    * ``hardcover_casewrap``: the printed sheet is glued over boards
      (``board_w_in`` × ``board_h_in`` per panel) and folds ``wrap_in`` round
      their edges; spine from the page-range ``spine_table``.
    """
    model_config = ConfigDict(extra="allow")
    type: str = PAPERBACK
    spine_formula: SpineFormula | None = None
    spine_table: list[SpineStep] = Field(default_factory=list)
    bleed_in: float | None = None
    wrap_in: float | None = None
    board_w_in: float | None = None
    board_h_in: float | None = None
    safety_margin_in: float | None = None   # from the trim (paperback) / the board edge (casewrap)
    spine_text_min_pages: int = 100
    spine_text_clearance_in: float = 0.125
    barcode_area: BarcodeArea = Field(default_factory=BarcodeArea)
    # optional stated values (checked against the derived ones)
    spine_width_in: float | None = None
    total_document_width_in: float | None = None
    total_document_height_in: float | None = None


class AoiCeiling(BaseModel):
    width: float
    height: float


class BookSpecs(BaseModel):
    model_config = ConfigDict(extra="allow")

    vendor: Vendor = Field(default_factory=Vendor)
    trim: Trim
    bleed_in: float
    safety_margin_in: float
    gutter: Gutter
    dpi: int = 300
    source_gsd_m: float = 10.0  # Sentinel-2 visible/NIR
    page_count: PageCount
    cover: Cover
    gutter_table: list[GutterBand] = Field(default_factory=list)
    page_rules: PageRules = Field(default_factory=PageRules)
    print_pdf: PrintPdf = Field(default_factory=PrintPdf)
    aoi_ceiling_km: AoiCeiling | None = None  # optional stated value
    attribution: dict = Field(default_factory=dict)

    # --- derived -----------------------------------------------------------

    @property
    def plate_width_in(self) -> float:
        return self.trim.width_in + 2 * self.bleed_in

    @property
    def plate_height_in(self) -> float:
        return self.trim.height_in + 2 * self.bleed_in

    @property
    def plate_aspect(self) -> float:
        return self.plate_width_in / self.plate_height_in

    @property
    def native_px(self) -> tuple[int, int]:
        return round(self.plate_width_in * self.dpi), round(self.plate_height_in * self.dpi)

    def aoi_ceiling(self) -> tuple[float, float]:
        """Largest AOI (km) that fills the plate at 1 source pixel per print pixel."""
        w, h = self.native_px
        return w * self.source_gsd_m / 1000.0, h * self.source_gsd_m / 1000.0

    @property
    def spine_width_in(self) -> float:
        """Spine at the planning ``page_count.target``."""
        return self.spine_width_for(self.page_count.target)

    @property
    def cover_document_in(self) -> tuple[float, float]:
        return self.cover_document_for(self.page_count.target)

    @property
    def is_casewrap(self) -> bool:
        return self.cover.type == CASEWRAP

    def spine_width_for(self, pages: int) -> float:
        if self.is_casewrap:
            steps = sorted(self.cover.spine_table, key=lambda s: s.max_pages)
            return next((s.spine_in for s in steps if pages <= s.max_pages), steps[-1].spine_in)
        f = self.cover.spine_formula
        return pages * f.per_page_in + f.offset_in

    @property
    def cover_bleed_in(self) -> float:
        return self.cover.bleed_in if self.cover.bleed_in is not None else self.bleed_in

    @property
    def cover_edge_in(self) -> float:
        """Paper beyond each panel's outer edge: the wrap (casewrap) or the bleed."""
        return self.cover.wrap_in if self.is_casewrap else self.cover_bleed_in

    @property
    def cover_panel_in(self) -> tuple[float, float]:
        """(width, height) of one cover panel: the board (casewrap) or the trim."""
        if self.is_casewrap:
            return self.cover.board_w_in, self.cover.board_h_in
        return self.trim.width_in, self.trim.height_in

    def cover_document_for(self, pages: int) -> tuple[float, float]:
        """(width, height) in inches of the one-piece cover for ``pages`` interior pages."""
        edge = self.cover_edge_in
        pw, ph = self.cover_panel_in
        return 2 * (edge + pw) + self.spine_width_for(pages), ph + 2 * edge

    def gutter_for(self, pages: int) -> GutterBand | None:
        """The gutter band for an interior of ``pages`` pages (None without a table)."""
        for band in self.gutter_table:
            if band.max_pages is None or pages <= band.max_pages:
                return band
        return None

    # --- consistency -------------------------------------------------------

    @model_validator(mode="after")
    def _cover_type_is_complete(self):
        c = self.cover
        if c.type == CASEWRAP:
            missing = [k for k in ("wrap_in", "board_w_in", "board_h_in") if getattr(c, k) is None]
            if not c.spine_table:
                missing.append("spine_table")
        elif c.type == PAPERBACK:
            missing = [] if c.spine_formula is not None else ["spine_formula"]
        else:
            raise ValueError(f"cover.type must be {PAPERBACK!r} or {CASEWRAP!r}, not {c.type!r}")
        if missing:
            raise ValueError(f"cover.type {c.type} needs: {', '.join(missing)}")
        return self

    @model_validator(mode="after")
    def _stated_values_match_derived(self):
        problems: list[str] = []
        if self.page_count.target % self.page_count.signature_multiple:
            problems.append(f"page_count.target {self.page_count.target} is not a multiple of "
                            f"{self.page_count.signature_multiple}")
        if self.aoi_ceiling_km is not None:
            w, h = self.aoi_ceiling()
            if abs(w - self.aoi_ceiling_km.width) > _KM_TOL or abs(h - self.aoi_ceiling_km.height) > _KM_TOL:
                problems.append(f"aoi_ceiling_km states {self.aoi_ceiling_km.width}×{self.aoi_ceiling_km.height} "
                                f"but trim/bleed/dpi/gsd give {w:.3f}×{h:.3f}")
        if self.cover.spine_width_in is not None and abs(self.cover.spine_width_in - self.spine_width_in) > _IN_TOL:
            problems.append(f"cover.spine_width_in states {self.cover.spine_width_in} but the formula gives "
                            f"{self.spine_width_in:.3f}")
        cw, ch = self.cover_document_in
        if self.cover.total_document_width_in is not None and abs(self.cover.total_document_width_in - cw) > _IN_TOL:
            problems.append(f"cover.total_document_width_in states {self.cover.total_document_width_in} "
                            f"but derives to {cw:.3f}")
        if self.cover.total_document_height_in is not None and abs(self.cover.total_document_height_in - ch) > _IN_TOL:
            problems.append(f"cover.total_document_height_in states {self.cover.total_document_height_in} "
                            f"but derives to {ch:.3f}")
        if problems:
            raise ValueError("book_specs.yaml is internally inconsistent:\n  - " + "\n  - ".join(problems))
        return self


def load_book_specs(path: str | Path) -> BookSpecs:
    return BookSpecs.model_validate(yaml.safe_load(Path(path).read_text()))
