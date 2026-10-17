"""book_specs.yaml: derived geometry is computed, stated values are checked."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from spectralcular.book_specs import BookSpecs, load_book_specs

REPO_ROOT = Path(__file__).resolve().parents[1]
SPECS = REPO_ROOT / "config" / "book_specs.yaml"


def _paperback(raw: dict) -> dict:
    raw["cover"]["type"] = "paperback_perfect_bind"
    return raw


def test_repo_specs_load_and_derive():
    s = load_book_specs(SPECS)
    assert s.native_px == (2625, 3375)
    w, h = s.aoi_ceiling()
    assert (round(w, 2), round(h, 2)) == (26.25, 33.75)
    assert s.plate_aspect == pytest.approx(8.75 / 11.25)


def test_casewrap_cover_matches_lulus_required_size():
    # Lulu's upload check for this book: 19.5 × 12.75 in, spine 0.75 in (214 pages)
    s = load_book_specs(SPECS)
    assert s.is_casewrap
    assert s.spine_width_for(214) == pytest.approx(0.75)
    w, h = s.cover_document_for(214)
    assert (w, h) == pytest.approx((19.5, 12.75))
    # the template's minimum-spine sheet is 19.0 × 12.75 (24–84 pages → 0.25 in)
    assert s.cover_document_for(32) == pytest.approx((19.0, 12.75))
    assert s.spine_width_for(800) == pytest.approx(2.125)


def test_paperback_cover_uses_the_formula():
    s = BookSpecs.model_validate(_paperback(yaml.safe_load(SPECS.read_text())))
    assert s.spine_width_for(500) == pytest.approx(500 / 444 + 0.06)
    w, h = s.cover_document_for(500)
    assert (round(w, 3), round(h, 2)) == (18.436, 11.25)


def test_stated_values_must_match_derived():
    raw = yaml.safe_load(SPECS.read_text())
    raw["dpi"] = 200  # invalidates the stated aoi_ceiling_km
    with pytest.raises(ValidationError, match="aoi_ceiling_km"):
        BookSpecs.model_validate(raw)
    raw = yaml.safe_load(SPECS.read_text())
    raw["cover"]["spine_width_in"] = 0.5  # the table gives 1.375 at the 500-page target
    with pytest.raises(ValidationError, match="spine_width_in"):
        BookSpecs.model_validate(raw)


def test_casewrap_needs_its_board_geometry():
    raw = yaml.safe_load(SPECS.read_text())
    raw["cover"].pop("wrap_in")
    with pytest.raises(ValidationError, match="wrap_in"):
        BookSpecs.model_validate(raw)


def test_a4_book_without_stated_derived_values():
    raw = _paperback(yaml.safe_load(SPECS.read_text()))
    raw["trim"] = {"page_size": "a4", "width_in": 8.27, "height_in": 11.69, "orientation": "portrait"}
    raw.pop("aoi_ceiling_km")
    raw["page_count"]["target"] = 200
    s = BookSpecs.model_validate(raw)
    assert s.native_px == (round(8.52 * 300), round(11.94 * 300))
    assert s.spine_width_in == pytest.approx(200 / 444 + 0.06, abs=1e-6)


def test_page_count_must_be_signature_multiple():
    raw = yaml.safe_load(SPECS.read_text())
    raw["page_count"]["target"] = 502
    with pytest.raises(ValidationError, match="multiple"):
        BookSpecs.model_validate(raw)
