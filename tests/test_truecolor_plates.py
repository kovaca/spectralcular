"""True-color plates through the Python print path: import, candidates,
spread configs, lock, sequencing, rendering and the page-2 HTML."""

from __future__ import annotations

import csv

import numpy as np
import pytest
import yaml
from PIL import Image
from pydantic import ValidationError

from spectralcular.candidate_build import candidate_to_spread_config
from spectralcular.candidates import CandidateEntry, CandidateSet, candidate_slug, dump_candidates, load_candidates
from spectralcular.config import Colormap, Imagery, Index, Location, SpreadConfig
from spectralcular.import_selection import import_selection, load_manifest
from spectralcular.lock import lock_candidates
from spectralcular.pool import (
    ColormapPool,
    ColormapPoolEntry,
    IndexPool,
    IndexPoolEntry,
    LocationPool,
    LocationPoolEntry,
    ReviewStatus,
)
from spectralcular.sequencing import assign_ordinals, lint_sequence
from spectralcular.truecolor import TrueColorSpec, TrueColorStretch

TC_RECORD = {
    "preset": "natural",
    "stretch": {"mode": "per-channel", "source": "auto", "pLo": 2, "pHi": 98,
                "lo": [0.01, 0.02, 0.03], "hi": [0.25, 0.22, 0.2]},
    "gamma": 1.6, "contrast": 4, "midpoint": 0.45, "saturation": 1.1,
}


def _spec() -> TrueColorSpec:
    return TrueColorSpec(preset="natural", stretch=TrueColorStretch(mode="linked", source="manual", lo=0.0, hi=0.3),
                         gamma=1.8)


def _pools():
    loc = LocationPool(entries=[LocationPoolEntry(
        id="mt_etna", name="Mt. Etna", bbox=(14.8, 37.6, 15.2, 37.9), landscape_character="volcano",
        themes=["vegetation"], season_windows=["2023-07-01/2023-08-31"], status=ReviewStatus.approved)])
    idx = IndexPool(entries=[
        IndexPoolEntry(name="NDVI", theme="vegetation", bands={"N": "nir", "R": "red"}, status=ReviewStatus.approved),
    ])
    cm = ColormapPool(entries=[ColormapPoolEntry(name="bids:viridis", type="sequential",
                                                  semantic_register="neutral_analytical", status=ReviewStatus.approved)])
    return loc, idx, cm


def _plate(**over):
    p = {
        "ordinal": 2, "id": "mt_etna__truecolor", "kind": "truecolor", "locationId": "mt_etna",
        "location": {"kind": "pool", "name": "Mt. Etna", "landscapeCharacter": "", "bbox": [14.9, 37.6, 15.3, 37.9],
                     "themes": ["vegetation"]},
        "frame": {"center": [15.11, 37.75], "widthM": 15863.4, "heightM": 20395.7, "rotationDeg": 25,
                  "bbox": [14.9, 37.6, 15.3, 37.9], "cornersLonLat": []},
        "scene": {"collection": "sentinel-2-l2a", "itemId": "S2A_33SWB_20230713_0_L2A",
                  "datetime": "2023-07-13T09:50:21Z", "cloudCover": 0.0, "mgrs": "33SWB", "epsg": 32633},
        "dateRange": "2023-07-01/2023-08-31", "index": None, "colormap": None, "colormapReversed": False,
        "normalization": None, "theme": "vegetation", "platform": "sentinel-2-l2a",
        "truecolor": TC_RECORD, "maskEnabled": False, "stats": None, "caption": "Etna, as seen", "notes": "",
    }
    p.update(over)
    return p


def _index_plate():
    return {
        **_plate(), "id": "mt_etna__NDVI__bids_viridis", "kind": "index", "ordinal": 1,
        "index": "NDVI", "colormap": "bids:viridis", "truecolor": None, "maskEnabled": True,
        "normalization": {"method": "percentile", "vmin": -0.02, "vmax": 0.88, "description": "pct[1,99]"},
    }


def _manifest(*plates, version=2):
    return {"kind": "spectralcular.selection", "version": version, "plates": list(plates)}


def _tc_cand(**over) -> CandidateEntry:
    kw = dict(id="mt_etna__truecolor", location_id="mt_etna", kind="truecolor", platform="sentinel-2-l2a",
              theme="vegetation", status=ReviewStatus.approved, truecolor=_spec(), ordinal=3)
    kw.update(over)
    return CandidateEntry(**kw)


# --- spectralcular import ---------------------------------------------------------------

def test_v2_manifest_imports_truecolor_and_index_plates(tmp_path):
    path = tmp_path / "book.selection.json"
    import json
    path.write_text(json.dumps(_manifest(_index_plate(), _plate())))
    loc, idx, cm = _pools()
    cands, _, rep = import_selection(load_manifest(path), CandidateSet(), loc, idx, cm)
    assert rep.skipped == [] and rep.problems == []
    assert sorted(rep.candidates_added) == ["mt_etna__NDVI__bids_viridis", "mt_etna__truecolor"]
    e = cands.by_id()["mt_etna__truecolor"]
    assert e.kind == "truecolor" and e.index is None and e.colormap is None
    assert e.platform == "sentinel-2-l2a" and e.theme == "vegetation"
    assert e.mask_enabled is False and e.normalization is None
    st = e.truecolor.stretch
    assert st.mode == "per-channel" and st.lo == (0.01, 0.02, 0.03) and st.hi == (0.25, 0.22, 0.2)
    assert (st.p_lo, st.p_hi) == (2.0, 98.0)
    assert e.truecolor.gamma == 1.6 and e.truecolor.contrast == 4 and e.truecolor.saturation == 1.1
    assert e.frame.rotation_deg == 25 and e.scene_ids == ["S2A_33SWB_20230713_0_L2A"]


def test_v1_manifest_still_imports_index_plates():
    loc, idx, cm = _pools()
    p = {k: v for k, v in _index_plate().items() if k not in ("kind", "truecolor")}
    cands, _, rep = import_selection(_manifest(p, version=1), CandidateSet(), loc, idx, cm)
    assert rep.candidates_added == ["mt_etna__NDVI__bids_viridis"] and rep.problems == []


def test_truecolor_reimport_updates_the_tone_and_keeps_reviewer_notes():
    loc, idx, cm = _pools()
    first, _, _ = import_selection(_manifest(_plate()), CandidateSet(), loc, idx, cm)
    noted = CandidateSet(entries=[e.model_copy(update={"reviewer_notes": "lovely"}) for e in first.entries])
    tc = {**TC_RECORD, "gamma": 2.2, "preset": None}
    cands, _, rep = import_selection(_manifest(_plate(truecolor=tc, caption="new")), noted, loc, idx, cm)
    assert rep.candidates_updated == ["mt_etna__truecolor"]
    e = cands.by_id()["mt_etna__truecolor"]
    assert e.truecolor.gamma == 2.2 and e.truecolor.preset is None
    assert e.caption == "new" and e.reviewer_notes == "lovely"


def test_truecolor_on_radar_is_skipped_and_reported():
    loc, idx, cm = _pools()
    _, _, rep = import_selection(_manifest(_plate(platform="sentinel-1-rtc")), CandidateSet(), loc, idx, cm)
    assert rep.candidates_added == []
    assert len(rep.skipped) == 1 and "red/green/blue" in rep.skipped[0]


def test_truecolor_with_bad_stretch_is_skipped_not_fatal():
    loc, idx, cm = _pools()
    bad = {**TC_RECORD, "stretch": {**TC_RECORD["stretch"], "lo": [0.3, 0.3, 0.3], "hi": [0.1, 0.1, 0.1]}}
    _, _, rep = import_selection(_manifest(_plate(truecolor=bad)), CandidateSet(), loc, idx, cm)
    assert rep.candidates_added == [] and "exceed" in rep.skipped[0]


# --- candidates ---------------------------------------------------------------

def test_candidate_validation_both_ways():
    assert candidate_slug("mt_etna", None, None, kind="truecolor") == "mt_etna__truecolor"
    with pytest.raises(ValidationError, match="truecolor spec"):
        _tc_cand(truecolor=None)
    with pytest.raises(ValidationError, match="platform"):
        _tc_cand(platform=None)
    with pytest.raises(ValidationError, match="no index or colormap"):
        _tc_cand(index="NDVI")
    with pytest.raises(ValidationError, match="red/green/blue"):
        _tc_cand(platform="sentinel-1-rtc")
    with pytest.raises(ValidationError, match="needs index and colormap"):
        CandidateEntry(id="x", location_id="mt_etna", theme="vegetation", index="NDVI")
    with pytest.raises(ValidationError, match="cannot carry"):
        CandidateEntry(id="x", location_id="mt_etna", theme="vegetation", index="NDVI",
                       colormap="bids:viridis", truecolor=_spec())


def test_candidates_yaml_round_trip(tmp_path):
    index_cand = CandidateEntry(id="mt_etna__NDVI__bids_viridis", location_id="mt_etna", index="NDVI",
                                colormap="bids:viridis", theme="vegetation")
    path = tmp_path / "candidates.yaml"
    dump_candidates(CandidateSet(entries=[_tc_cand(mask_enabled=False), index_cand]), path)
    raw = yaml.safe_load(path.read_text())["entries"]
    # Index plates keep their old shape; the true-color plate sorts after them.
    assert "kind" not in raw[0] and "mask_enabled" not in raw[0]
    assert raw[1]["kind"] == "truecolor" and raw[1]["mask_enabled"] is False
    back = load_candidates(path).by_id()["mt_etna__truecolor"]
    assert back.truecolor == _spec() and back.mask_enabled is False


def test_cross_pool_validation_skips_index_checks_for_truecolor():
    from spectralcular.candidates import validate_against_pools

    loc, idx, cm = _pools()
    assert validate_against_pools(CandidateSet(entries=[_tc_cand()]), loc, idx, cm) == []
    assert validate_against_pools(CandidateSet(entries=[_tc_cand(location_id="nowhere")]), loc, idx, cm)


# --- spread config ------------------------------------------------------------

def _spread(**over):
    kw = dict(asset_id="a", location=Location(name="X", bbox=(0, 0, 1, 1)),
              imagery=Imagery(source="sentinel-2-l2a", date_range="2023-01-01/2023-02-01"))
    kw.update(over)
    return SpreadConfig(**kw)


def test_spread_config_requires_exactly_one_kind():
    assert _spread(truecolor=_spec()).kind == "truecolor"
    ndvi = dict(index=Index(name="NDVI", bands={"N": "nir", "R": "red"}), colormap=Colormap(name="bids:viridis"))
    assert _spread(**ndvi).kind == "index"
    with pytest.raises(ValidationError, match="index \\+ colormap, or truecolor"):
        _spread()
    with pytest.raises(ValidationError, match="no index or colormap"):
        _spread(truecolor=_spec(), **ndvi)
    with pytest.raises(ValidationError, match="red/green/blue"):
        _spread(truecolor=_spec(), imagery=Imagery(source="sentinel-1-rtc", date_range="2023-01-01/2023-02-01"))


def test_candidate_to_spread_config_for_truecolor(tmp_path):
    loc, _, _ = _pools()
    cand = _tc_cand(frame=None, scene_ids=["S2A_X"], mask_enabled=False)
    cfg = candidate_to_spread_config(cand, loc.entries[0])
    assert cfg.kind == "truecolor" and cfg.index is None and cfg.colormap is None
    assert cfg.imagery.source == "sentinel-2-l2a" and cfg.imagery.item_ids == ["S2A_X"]
    assert cfg.imagery.cloud_cover_max == 10.0
    assert cfg.mask_enabled is False
    assert cfg.layout.caption == "Mt. Etna — True color" and cfg.layout.plate_number == "003"
    # The standalone YAML `spectralcular candidates lock` writes round-trips.
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False))
    assert SpreadConfig.model_validate(yaml.safe_load(p.read_text())) == cfg


# --- lock + sequencing ----------------------------------------------------------

def test_lock_writes_truecolor_rows(tmp_path):
    loc, idx, cm = _pools()
    index_cand = CandidateEntry(id="mt_etna__NDVI__bids_viridis", location_id="mt_etna", index="NDVI",
                                colormap="bids:viridis", theme="vegetation", status=ReviewStatus.approved,
                                ordinal=1)
    tc = _tc_cand(ordinal=2, truecolor=TrueColorSpec(
        preset="balanced", stretch=TrueColorStretch(mode="per-channel", source="auto",
                                                    lo=(0.01, 0.02, 0.03), hi=(0.2, 0.25, 0.3))))
    spreads_dir = tmp_path / "config" / "spreads"
    paths, csv_path = lock_candidates(CandidateSet(entries=[index_cand, tc]), loc, idx, cm, {},
                                      config_spreads_dir=spreads_dir, spreads_csv_path=tmp_path / "spreads.csv")
    rows = list(csv.DictReader(csv_path.open()))
    assert [r["kind"] for r in rows] == ["index", "truecolor"]
    assert rows[1]["index"] == "" and rows[1]["colormap"] == ""
    assert rows[1]["normalization"] == "balanced rgb[0.01,0.2|0.02,0.25|0.03,0.3]"
    assert rows[0]["normalization"] == "pct[1,99]"
    cfg = SpreadConfig.model_validate(yaml.safe_load((spreads_dir / "mt_etna__truecolor.yaml").read_text()))
    assert cfg.kind == "truecolor" and cfg.truecolor.stretch.hi == (0.2, 0.25, 0.3)


def test_sequencing_treats_truecolor_as_one_look():
    entries = [
        _tc_cand(id="a__truecolor", location_id="a"),
        _tc_cand(id="b__truecolor", location_id="b"),
        CandidateEntry(id="c__NDVI__bids_viridis", location_id="c", index="NDVI", colormap="bids:viridis",
                       theme="vegetation", status=ReviewStatus.approved),
    ]
    ordered = sorted(assign_ordinals(CandidateSet(entries=entries)).approved(), key=lambda e: e.ordinal)
    # The greedy pass splits the two true-color plates with the index plate.
    assert [e.kind for e in ordered] == ["truecolor", "index", "truecolor"]
    run = CandidateSet(entries=[e.model_copy(update={"ordinal": i}) for i, e in enumerate(entries[:2], start=1)])
    locs = LocationPool(entries=[LocationPoolEntry(
        id=i, name=i, bbox=(0, 0, 1, 1), landscape_character="x", themes=["vegetation"],
        season_windows=["2023-07-01/2023-08-31"], status=ReviewStatus.approved) for i in ("a", "b")])
    v = lint_sequence(run, locs)
    assert [x.kind for x in v] == ["consecutive_colormap"] and "truecolor" in v[0].detail


# --- rendering ------------------------------------------------------------------

def _bands(h=40, w=30, seed=0):
    rng = np.random.default_rng(seed)
    r, g, b = (rng.uniform(0.0, 0.3, (h, w)).astype("float32") for _ in range(3))
    r[0, 0] = np.nan
    return r, g, b


def test_render_truecolor_png_matches_the_tone_pipeline(tmp_path):
    from spectralcular.render_assets import render_truecolor_png
    from spectralcular.truecolor import to_rgba8, tone

    r, g, b = _bands()
    out = render_truecolor_png(r, g, b, _spec(), tmp_path / "plate.png")
    img = np.asarray(Image.open(out))
    assert img.shape == (40, 30, 4) and img[0, 0, 3] == 0
    np.testing.assert_array_equal(img, to_rgba8(tone(np.stack([r, g, b], -1), _spec())))


def test_render_truecolor_png_downsamples_long_edges(tmp_path):
    from spectralcular.render_assets import render_truecolor_png

    r, g, b = _bands(h=200, w=100)
    out = render_truecolor_png(r, g, b, _spec(), tmp_path / "plate.png", long_edge_max=50)
    assert Image.open(out).size == (25, 50)


def test_diagnostic_charts_render(tmp_path):
    from spectralcular.render_assets import (
        render_truecolor_curves_svg,
        render_truecolor_histogram_svg,
        render_truecolor_linear_png,
        render_truecolor_ramp_png,
    )

    r, g, b = _bands()
    per = TrueColorSpec(stretch=TrueColorStretch(mode="per-channel", lo=(0, 0.01, 0.02), hi=(0.2, 0.25, 0.3)),
                        contrast=5)
    for spec in (_spec(), per):
        assert render_truecolor_curves_svg(spec, tmp_path / "c.svg").read_text().startswith("<?xml")
        assert render_truecolor_histogram_svg(r, g, b, spec, tmp_path / "h.svg").stat().st_size > 0
        ramp = Image.open(render_truecolor_ramp_png(spec, tmp_path / "ramp.png", width_px=64, height_px=16))
        assert ramp.size == (64, 16) and ramp.mode == "RGB"
    lin = Image.open(render_truecolor_linear_png(r, g, b, tmp_path / "lin.png"))
    assert lin.mode == "RGBA"


def _assets(tmp_path):
    from spectralcular.book import SpreadAssets

    png = tmp_path / "x.png"
    Image.new("RGBA", (4, 4)).save(png)
    svg = tmp_path / "x.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    return SpreadAssets(image_png=png, halo_png=png, gradient_band_png=png, histogram_svg=svg, locator_svg=svg,
                        rgb_thumbnail_png=png, scene_locator_svg=svg, oklch_chart_svg=svg,
                        linear_thumbnail_png=png)


_COMMON = dict(
    asset_id="mt_etna__truecolor", location_name="Mt. Etna", bbox=(14.9, 37.6, 15.3, 37.9),
    source="sentinel-2-l2a", platform="sentinel-2a", scene_id="S2A_33SWB_20230713_0_L2A",
    acquired_iso="2023-07-13T09:50:21Z", cloud_cover=0.0, crs="EPSG:32633",
    platform_id="sentinel-2-l2a", caption="Etna, as seen", plate_number="002", interpretive_note="",
)


def test_truecolor_spread_html(tmp_path):
    from spectralcular.book import render_spread_html
    from spectralcular.truecolor import channel_stats

    r, g, b = _bands()
    spec = TrueColorSpec(preset="haze_cut", stretch=TrueColorStretch(mode="per-channel", lo=(0.01, 0.02, 0.03),
                                                                     hi=(0.2, 0.25, 0.3)),
                         gamma=1.4, contrast=6, midpoint=0.45, saturation=1.15)
    out = render_spread_html(
        out_html=tmp_path / "s.html", kind="truecolor", bands={"R": "red", "G": "green", "B": "blue"},
        truecolor_spec=spec, truecolor_stats=channel_stats(r, g, b), truecolor_preset_label="Haze-cut",
        assets=_assets(tmp_path), **_COMMON,
    )
    html = out.read_text()
    assert "<title>True color — Mt. Etna</title>" in html
    assert html.count("True color · Haze-cut") == 2       # page-1 overlay + running head
    assert "Linear &nbsp;·&nbsp; unstretched" in html and "Stretch" in html and "Tone</span>" in html
    assert "0.030 → 0.300" in html                        # blue window
    assert "6 @ 0.45" in html and "1.15" in html and "1.40" in html
    assert "R G B = B04 · B03 · B02" in html             # channel order, not wavelength order
    assert "B02 blue 490 nm · B03 green 560 nm · B04 red 665 nm" in html  # ruler label lights all three
    assert "Colormap" not in html and "Index</span>" not in html


def test_index_spread_html_is_unchanged_by_the_truecolor_branch(tmp_path):
    from spectralcular.book import render_spread_html

    stats = {"min": -0.1, "max": 0.9, "mean": 0.4, "std": 0.2, "p1": 0.0, "p50": 0.4, "p99": 0.85,
             "valid_fraction": 0.98, "n_pixels": 12345}
    out = render_spread_html(
        out_html=tmp_path / "s.html", index_short="NDVI", index_long_name="Normalized Difference Vegetation Index",
        index_formula="(N - R)/(N + R)", index_domain=(-1.0, 1.0), application_domain="vegetation",
        bands={"N": "nir", "R": "red"}, colormap_name="bids:viridis", colormap_short_name="viridis",
        normalization_desc="fixed", vmin=0.0, vmax=0.9, stats=stats, assets=_assets(tmp_path), **_COMMON,
    )
    html = out.read_text()
    assert "<title>Normalized Difference Vegetation Index — Mt. Etna</title>" in html
    assert html.count("NDVI · viridis") == 2
    assert "unstretched" not in html and "Colormap" in html
