"""The overview spread: loading the locked book, the aggregates (mirroring
web/src/lib/state/overview.ts), the combinations layout, and the two-page
render that lands before the index in the proof."""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path

import fitz
import pytest
import yaml

from spectralcular.config import (
    Colormap,
    Imagery,
    Index,
    Layout,
    Location,
    SpreadConfig,
)
from spectralcular.framing import FrameSpec
from spectralcular.overview import (
    COMBO_TIERS,
    COMBOS_H,
    CONTENT_W,
    OverviewPlate,
    book_overview,
    combination_groups,
    date_from_item_id,
    equal_earth,
    layout_combinations,
    load_overview_plates,
    pick_tier,
    render_overview_pdf,
)
from spectralcular.pool import load_colormap_pool, load_index_pool
from spectralcular.proof import _pad_to_verso

ROOT = Path(__file__).resolve().parents[1]
GRAY = ("#000000", "#ffffff")


def _plate(ordinal: int, *, theme="vegetation", loc="etna", index="NDVI", colormap="custom:gray",
           reversed=False, date="2023-07-13", kind="index", lon=15.0, lat=37.7, platform="sentinel-2-l2a"):
    tc = kind == "truecolor"
    return OverviewPlate(
        ordinal=ordinal, plate_number=f"{ordinal:03d}", kind=kind, theme=theme,
        location_id=loc, location_name=loc.title(), lon=lon, lat=lat, platform=platform,
        index=None if tc else index, colormap=None if tc else colormap, reversed=reversed,
        colormap_colors=None if tc else GRAY, truecolor_preset="natural" if tc else None, date=date,
    )


def test_date_from_item_id():
    assert date_from_item_id("S2A_33SWB_20230713_0_L2A") == "2023-07-13"
    assert date_from_item_id("LC09_L2SP_037037_20240710_02_T1") == "2024-07-10"
    assert date_from_item_id("S1A_IW_GRDH_1SDV_20240115T051234_20240115T051259_052106_064C1B_rtc") == "2024-01-15"
    assert date_from_item_id("no-date-here_123456") is None


def test_book_overview_aggregates():
    plates = [
        _plate(1, loc="etna", lon=15.0, lat=37.0),
        _plate(2, loc="etna", lon=15.2, lat=37.4, index="EVI", date="2023-08-01"),
        _plate(3, loc="powell", theme="water", index="NDWI", lon=-111, lat=37, date=None, platform="landsat-c2-l2"),
        _plate(4, loc="rhodes", theme="burn", kind="truecolor", date="2021-08-20"),
        _plate(5, loc="okavango", theme="water", index="NDWI", lon=22, lat=-19, date="2022-01-05"),
    ]
    ov = book_overview(plates)
    assert len(ov.points) == 4
    etna = next(p for p in ov.points if p.location_id == "etna")
    assert (etna.lon, etna.lat) == pytest.approx((15.1, 37.2))
    assert set(ov.by_index) == {"NDVI", "EVI", "NDWI"}
    assert [p.ordinal for p in ov.by_index["NDWI"]] == [3, 5]
    assert [p.ordinal for p in ov.truecolor] == [4]
    # NDWI × gray appears twice in the water theme: one pair, repeated
    ndwi = [p for p in ov.pairs if p.index == "NDWI"]
    assert len(ndwi) == 1 and len(ndwi[0].plates) == 2 and ov.repeated == 1
    assert ov.span == ("2021-08-20", "2023-08-01") and ov.years == "2021–2023"
    assert ov.no_scene == 1
    assert ov.months[6] == 1 and ov.months[7] == 2 and ov.months[0] == 1
    assert ov.platforms == {"Sentinel-2": 4, "Landsat": 1}


def test_combination_groups_order_themes_then_indices():
    plates = [
        _plate(1, theme="water", index="NDWI"),
        _plate(2, theme="vegetation", index="ndre"),
        _plate(3, theme="vegetation", index="EVI"),
        _plate(4, theme="vegetation", index="EVI", reversed=True),
        _plate(5, theme="burn", kind="truecolor"),
        _plate(6, theme="vegetation", index="EVI"),
    ]
    groups = combination_groups(book_overview(plates))
    assert [g.theme for g in groups] == ["vegetation", "water", "truecolor"]
    veg = groups[0]
    assert [label for label, _ in veg.entries] == ["EVI", "ndre"]
    evi = veg.entries[0][1]
    # EVI × gray on 003 and 006 is one repeated chip; the reversed map is its own
    assert [(c.numbers, c.repeated) for c in evi] == [("003 006", True), ("004", False)]
    assert evi[1].name.endswith("_r") and evi[1].stops[0] == "#ffffff"
    assert groups[-1].entries[0][1][0].name == "natural"


def test_equal_earth_extent():
    x, _ = equal_earth(180.0, 0.0)
    _, y = equal_earth(0.0, 90.0)
    assert float(x) == pytest.approx(2.70663, abs=1e-4)
    assert float(y) == pytest.approx(1.31737, abs=1e-4)


def _synthetic_book(n: int, n_indices: int, seed: int = 3) -> list[OverviewPlate]:
    rng = random.Random(seed)
    themes = ["vegetation", "water", "burn", "soil_geology", "urban", "snow_ice", "atmosphere"]
    idx = [(f"IDX{i:03d}", themes[i % len(themes)]) for i in range(n_indices)]
    out = []
    for k in range(1, n + 1):
        name, theme = rng.choice(idx)
        out.append(_plate(k, theme=theme, index=name, colormap=f"custom:c{rng.randrange(150)}",
                          loc=f"loc{rng.randrange(n // 2 + 1)}", lon=rng.uniform(-170, 170),
                          lat=rng.uniform(-60, 75), date=f"{rng.randint(2019, 2025)}-{rng.randint(1, 12):02d}-10"))
    return out


def test_small_books_get_the_named_tier():
    groups = combination_groups(book_overview(_synthetic_book(20, 12)))
    assert pick_tier(groups) is COMBO_TIERS[0]


@pytest.mark.parametrize("n,n_indices", [(231, 110), (330, 140)])
def test_full_books_fit_the_columns(n, n_indices):
    groups = combination_groups(book_overview(_synthetic_book(n, n_indices)))
    tier = pick_tier(groups)
    placed, fits = layout_combinations(groups, tier)
    assert fits
    col_w = (CONTENT_W - 6.0) / 2
    for col, y, kind, payload in placed:
        assert col < 2 and y >= 0
        assert y + (tier.line_h if kind == "line" else 0) <= COMBOS_H + 1e-6
        if kind == "line":
            assert all(x >= 0 for x, _k, _p in payload)
            assert all(x <= col_w for x, _k, _p in payload)


def _write_book(tmp_path: Path) -> tuple[Path, Path]:
    cfg_dir = tmp_path / "config" / "spreads"
    cfg_dir.mkdir(parents=True)
    meta_dir = tmp_path / "output" / "metadata"
    meta_dir.mkdir(parents=True)
    rows = []
    for i, (sid, item, frame) in enumerate([
        ("etna__NDVI", "S2A_33SWB_20230713_0_L2A", FrameSpec(center=(15.11, 37.75), width_m=15000, height_m=19286, rotation_deg=25)),
        ("powell__NDWI", "S2B_12SVG_20220610_0_L2A", None),
    ], start=1):
        cfg = SpreadConfig(
            asset_id=sid,
            location=Location(name=sid.split("__")[0].title(), bbox=(-111.5, 36.8, -110.5, 37.6)),
            imagery=Imagery(date_range="2022-01-01/2023-12-31", item_ids=[item]),
            index=Index(name=sid.split("__")[1], bands={"N": "nir", "R": "red"}),
            colormap=Colormap(name="bids:viridis", reversed=i == 2),
            layout=Layout(plate_number=f"{i:03d}"),
            frame=frame,
        )
        path = cfg_dir / f"{sid}.yaml"
        path.write_text(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False))
        rows.append({"ordinal": i, "id": sid, "kind": "index", "theme": "vegetation" if i == 1 else "water",
                     "location_id": sid.split("__")[0], "index": cfg.index.name, "colormap": "bids:viridis",
                     "normalization": "", "plate_number": f"{i:03d}",
                     "config_path": f"config/spreads/{sid}.yaml", "status": "approved", "review_status": "draft"})
    # spectralcular build ran for etna: its sidecar carries the exact scene time
    (meta_dir / "etna__NDVI.json").write_text(json.dumps({"scene_datetime": "2023-07-16T09:50:31Z"}))
    csv_path = tmp_path / "spreads.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return csv_path, meta_dir


def test_load_overview_plates(tmp_path):
    csv_path, meta_dir = _write_book(tmp_path)
    plates = load_overview_plates(csv_path, tmp_path, meta_dir)
    etna, powell = plates
    assert etna.date == "2023-07-16"                  # sidecar beats the item id
    assert (etna.lon, etna.lat) == (15.11, 37.75)     # frame center
    assert powell.date == "2022-06-10"                # from the pinned item id
    assert (powell.lon, powell.lat) == pytest.approx((-111.0, 37.2))   # bbox center
    assert powell.reversed and powell.colormap == "bids:viridis"
    assert [p.theme for p in plates] == ["vegetation", "water"]


def test_render_is_one_spread_even_for_a_full_book(tmp_path):
    idx_pool = load_index_pool(ROOT / "config" / "pool" / "indices.yaml")
    cmap_pool = load_colormap_pool(ROOT / "config" / "pool" / "colormaps.yaml")
    rng = random.Random(11)
    real = [e for e in cmap_pool.entries if e.type != "categorical"]
    plates = []
    for k in range(1, 301):
        e = rng.choice(idx_pool.entries)
        cm = rng.choice(real)
        plates.append(OverviewPlate(
            ordinal=k, plate_number=f"{k:03d}", kind="index", theme=e.theme, location_id=f"loc{k % 140}",
            location_name=f"Place {k % 140}", lon=rng.uniform(-170, 170), lat=rng.uniform(-60, 75),
            platform=e.platform, index=e.name, colormap=cm.name, reversed=rng.random() < 0.3,
            colormap_colors=tuple(cm.colors) if cm.colors else None,
            date=f"{rng.randint(2019, 2025)}-{rng.randint(1, 12):02d}-15"))
    out = render_overview_pdf(out_pdf=tmp_path / "overview.pdf", plates=plates,
                              idx_pool=idx_pool, cmap_pool=cmap_pool)
    with fitz.open(out) as doc:
        assert doc.page_count == 2
        assert "The book at a glance" in doc[0].get_text()
        assert "Index × colormap" in doc[1].get_text()


@pytest.mark.parametrize("pages,expect", [(1, 1), (2, 3), (3, 3)])
def test_overview_opens_on_a_verso(pages, expect):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    _pad_to_verso(doc, 100, 100)
    assert doc.page_count == expect
    # the next page (number count + 1) is even, i.e. a verso
    assert (doc.page_count + 1) % 2 == 0
