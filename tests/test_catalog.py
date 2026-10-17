"""Vendored colorcurious catalog: decode, naming, sync."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from spectralcular.catalog import (
    CatalogError,
    decode_stops,
    load_catalog,
    palettize_name_for,
    slim_record,
    split_name,
    stops_hex,
    sync_catalog,
)

# A colorcurious checkout for the sync test (skipped when absent).
COLORCURIOUS = Path(os.environ.get("COLORCURIOUS_DIR", "~/projects/colorcurious")).expanduser()


def test_catalog_loads_and_rows_match_records():
    cat = load_catalog()
    assert len(cat) == cat.sprite.shape[0] == len(cat.order)
    assert cat.sprite.shape[1:] == (256, 3)
    assert cat.meta.get("record_count") == len(cat)


def test_viridis_decodes_to_known_endpoints():
    cat = load_catalog()
    rec = cat.get("matplotlib.viridis")
    h = stops_hex(cat, rec)
    assert rec.stops_count == 256 and len(h) == 256
    assert (h[0], h[-1]) == ("#440154", "#fde725")
    assert rec.lightness_monotonic is True
    assert rec.cvd_preserves_order == {"protan": True, "deutan": True, "tritan": True}


def test_categorical_decodes_to_n_discrete_stops():
    cat = load_catalog()
    rec = cat.get("carto.Antique")
    stops = decode_stops(cat, rec)
    assert rec.type == "categorical" and stops.shape == (12, 3)
    assert stops_hex(cat, rec)[0] == "#855c75"


def test_diverging_metrics_present():
    rec = load_catalog().get("crameri.roma")
    assert rec.type == "diverging" and rec.diverging_pivot_t == pytest.approx(0.49, abs=0.01)
    assert rec.license == "CC-BY-4.0" and rec.collection == "crameri"


def test_unknown_id_raises():
    with pytest.raises(CatalogError):
        load_catalog().get("nope.zzz")


def test_naming_helpers():
    assert split_name("colorcurious:crameri.roma_r") == ("crameri.roma", True)
    assert split_name("colorcurious:crameri.roma") == ("crameri.roma", False)
    with pytest.raises(CatalogError):
        split_name("bids:viridis")
    assert palettize_name_for("crameri.bamO") == "crameri:bamo"
    assert palettize_name_for("matplotlib.viridis") == "bids:viridis"
    assert palettize_name_for("matplotlib.cividis") == "cividis:cividis"
    assert palettize_name_for("cmocean.algae") == "cmocean:algae"


def test_slim_record_keeps_review_fields_only():
    raw = {
        "id": "x.y", "name": "Y", "type": "sequential", "source": {"collection": "x"}, "tags": ["a"],
        "notes": "", "stops_count": 256,
        "perceptual": {"lightness": {"start": 0.1, "monotonic": True}, "hue": {"quadrants": ["red"], "path": "p"},
                       "chroma": {"max": 0.2, "mean": 0.1}, "diverging": None,
                       "delta_e": {"per_step": [1, 2, 3]}, "endpoint_temp": {"start": "warm", "end": None}},
        "cvd": {"protan": {"delta_e_total": 1, "uniformity": 0.5, "preserves_order": True}},
    }
    slim = slim_record(raw)
    assert "delta_e" not in slim["perceptual"]
    assert slim["perceptual"]["lightness"] == {"monotonic": True}
    assert slim["perceptual"]["chroma"] == {"max": 0.2}
    assert slim["cvd"] == {"protan": {"preserves_order": True, "uniformity": 0.5}}


@pytest.mark.skipif(not (COLORCURIOUS / "static" / "colormaps.json").exists(),
                    reason="colorcurious checkout not available")
def test_sync_writes_all_files(tmp_path):
    rep = sync_catalog(COLORCURIOUS, tmp_path)
    names = {p.name for p in rep.files}
    assert names == {"colormaps.json", "colormaps-sprite.png", "manifest.json", "NOTICE.md"}
    assert rep.record_count == 386
    cat = load_catalog(tmp_path)
    assert len(cat) == 386
    notice = (tmp_path / "NOTICE.md").read_text()
    assert "CC-BY-4.0" in notice and "| crameri |" in notice
