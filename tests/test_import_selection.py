"""spectralcular import merge semantics."""

import json

import pytest

from spectralcular.candidates import CandidateEntry, CandidateSet
from spectralcular.import_selection import ImportError_, import_selection, load_manifest
from spectralcular.pool import (
    ColormapPool,
    ColormapPoolEntry,
    IndexPool,
    IndexPoolEntry,
    LocationPool,
    LocationPoolEntry,
    ReviewStatus,
)


def _pools():
    loc = LocationPool(entries=[LocationPoolEntry(
        id="mt_etna", name="Mt. Etna", bbox=(14.8, 37.6, 15.2, 37.9), landscape_character="volcano",
        themes=["vegetation"], season_windows=["2023-07-01/2023-08-31"], status=ReviewStatus.approved)])
    idx = IndexPool(entries=[
        IndexPoolEntry(name="NDVI", theme="vegetation", bands={"N": "nir", "R": "red"}, status=ReviewStatus.approved),
        IndexPoolEntry(name="NDWI", theme="water", bands={"G": "green", "N": "nir"}, status=ReviewStatus.approved),
    ])
    cm = ColormapPool(entries=[ColormapPoolEntry(name="bids:viridis", type="sequential",
                                                  semantic_register="neutral_analytical", status=ReviewStatus.approved)])
    return loc, idx, cm


def _plate(**over):
    p = {
        "ordinal": 1, "id": "mt_etna__NDVI__bids_viridis", "locationId": "mt_etna",
        "location": {"kind": "pool", "name": "Mt. Etna", "landscapeCharacter": "", "bbox": [14.9, 37.6, 15.3, 37.9], "themes": ["vegetation"]},
        "frame": {"center": [15.11, 37.75], "widthM": 15863.4, "heightM": 20395.7, "rotationDeg": 25, "bbox": [14.9, 37.6, 15.3, 37.9], "cornersLonLat": []},
        "scene": {"collection": "sentinel-2-l2a", "itemId": "S2A_33SWB_20230713_0_L2A", "datetime": "2023-07-13T09:50:21Z", "cloudCover": 0.0, "mgrs": "33SWB", "epsg": 32633},
        "dateRange": "2023-07-01/2023-08-31", "index": "NDVI", "theme": "vegetation", "colormap": "bids:viridis", "colormapReversed": False,
        "normalization": {"method": "percentile", "vmin": -0.02, "vmax": 0.88, "description": "pct[1,99]"},
        "maskEnabled": True, "platform": "sentinel-2-l2a", "stats": None, "caption": "Etna in July", "notes": "",
    }
    p.update(over)
    return p


def _manifest(*plates):
    return {"kind": "spectralcular.selection", "version": 1, "plates": list(plates)}


def test_new_candidate_is_created_with_picker_fields():
    loc, idx, cm = _pools()
    cands, locs, rep = import_selection(_manifest(_plate()), CandidateSet(), loc, idx, cm)
    assert rep.candidates_added == ["mt_etna__NDVI__bids_viridis"]
    assert rep.problems == []
    e = cands.by_id()["mt_etna__NDVI__bids_viridis"]
    assert e.status == ReviewStatus.approved
    assert e.pairing_signal == "picker" and e.affinity_signal == "picker"
    assert e.scene_ids == ["S2A_33SWB_20230713_0_L2A"]
    assert e.frame is not None and e.frame.rotation_deg == 25
    assert e.normalization.method == "fixed" and e.normalization.vmax == 0.88
    assert e.date_range == "2023-07-01/2023-08-31"
    assert e.ordinal == 1 and e.caption == "Etna in July"
    assert len(locs.entries) == 1  # pool location reused


def test_adhoc_location_is_added_to_pool():
    loc, idx, cm = _pools()
    p = _plate(locationId="my_valley", id="my_valley__NDVI__bids_viridis",
               location={"kind": "adhoc", "name": "My valley", "landscapeCharacter": "orchards", "bbox": [10, 45, 10.3, 45.2], "themes": ["vegetation"]})
    cands, locs, rep = import_selection(_manifest(p), CandidateSet(), loc, idx, cm)
    assert rep.locations_added == ["my_valley"]
    new = {e.id: e for e in locs.entries}["my_valley"]
    assert new.source == "web_picker" and new.status == ReviewStatus.approved
    assert new.season_windows == ["2023-07-01/2023-08-31"]
    assert rep.problems == []


def test_existing_candidate_is_promoted_and_keeps_reviewer_notes():
    loc, idx, cm = _pools()
    existing = CandidateSet(entries=[CandidateEntry(
        id="mt_etna__NDVI__bids_viridis", location_id="mt_etna", index="NDVI", colormap="bids:viridis",
        theme="vegetation", status=ReviewStatus.needs_revision, reviewer_notes="too flat", ordinal=7)])
    cands, _, rep = import_selection(_manifest(_plate()), existing, loc, idx, cm)
    assert rep.candidates_updated == ["mt_etna__NDVI__bids_viridis"]
    e = cands.by_id()["mt_etna__NDVI__bids_viridis"]
    assert e.status == ReviewStatus.approved           # curated in the picker → approved
    assert e.reviewer_notes == "too flat"              # reviewer's notes survive
    assert e.pairing_signal == "picker"
    assert e.ordinal == 1                              # picker-owned, overwritten
    assert e.frame is not None


def test_import_never_demotes_an_approved_candidate():
    loc, idx, cm = _pools()
    existing = CandidateSet(entries=[CandidateEntry(
        id="mt_etna__NDVI__bids_viridis", location_id="mt_etna", index="NDVI", colormap="bids:viridis",
        theme="vegetation", status=ReviewStatus.approved)])
    cands, _, _ = import_selection(_manifest(_plate()), existing, loc, idx, cm, new_status=ReviewStatus.proposed)
    assert cands.by_id()["mt_etna__NDVI__bids_viridis"].status == ReviewStatus.approved


def test_plates_without_scene_are_skipped_and_theme_mismatch_reported():
    loc, idx, cm = _pools()
    no_scene = _plate(scene=None)
    wrong_theme = _plate(id="mt_etna__NDWI__bids_viridis", index="NDWI", theme="vegetation", ordinal=2)
    cands, _, rep = import_selection(_manifest(no_scene, wrong_theme), CandidateSet(), loc, idx, cm)
    assert len(rep.skipped) == 1 and "no scene" in rep.skipped[0]
    # theme comes from the index pool (water), so no mismatch problem
    assert rep.problems == []
    assert cands.by_id()["mt_etna__NDWI__bids_viridis"].theme == "water"


def test_reversed_pick_is_kept_as_flag_when_no_r_twin_exists():
    loc, idx, cm = _pools()
    cands, _, rep = import_selection(_manifest(_plate(colormapReversed=True)), CandidateSet(), loc, idx, cm)
    assert rep.candidates_added == ["mt_etna__NDVI__bids_viridis_r"]
    e = cands.by_id()["mt_etna__NDVI__bids_viridis_r"]
    assert e.colormap == "bids:viridis" and e.reversed is True
    assert rep.problems == []


def test_reversed_pick_prefers_reviewed_r_twin():
    loc, idx, cm = _pools()
    cm = ColormapPool(entries=[*cm.entries, ColormapPoolEntry(
        name="bids:viridis_r", type="sequential", semantic_register="neutral_analytical", status=ReviewStatus.approved)])
    cands, _, rep = import_selection(_manifest(_plate(colormapReversed=True)), CandidateSet(), loc, idx, cm)
    e = cands.by_id()["mt_etna__NDVI__bids_viridis_r"]
    assert e.colormap == "bids:viridis_r" and e.reversed is False
    assert rep.problems == []


def test_load_manifest_validates_kind_and_version(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text(json.dumps({"kind": "other", "version": 1, "plates": []}))
    with pytest.raises(ImportError_):
        load_manifest(bad)
    bad.write_text(json.dumps({"kind": "spectralcular.selection", "version": 9, "plates": []}))
    with pytest.raises(ImportError_):
        load_manifest(bad)
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(_manifest()))
    assert load_manifest(ok)["plates"] == []


def test_mask_toggle_reaches_index_candidates():
    loc, idx, cm = _pools()
    cands, _, _ = import_selection(_manifest(_plate(maskEnabled=False)), CandidateSet(), loc, idx, cm)
    assert cands.by_id()["mt_etna__NDVI__bids_viridis"].mask_enabled is False
    # the picker owns the toggle: re-importing with it on flips it back
    cands, _, rep = import_selection(_manifest(_plate(maskEnabled=True)), cands, loc, idx, cm)
    assert rep.candidates_updated == ["mt_etna__NDVI__bids_viridis"]
    assert cands.by_id()["mt_etna__NDVI__bids_viridis"].mask_enabled is True
