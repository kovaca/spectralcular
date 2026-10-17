"""spectralcular export-web colormap export: LUT rows, provenance, categorical handling."""

from __future__ import annotations

import json

import numpy as np
from PIL import Image

from spectralcular.export_web import LUT_WIDTH, SCHEMA_VERSION, export_colormaps
from spectralcular.pool import ColormapPool, ColormapPoolEntry, ColormapSource, ReviewStatus


def test_export_colormaps_rows_and_metadata(tmp_path):
    pool = ColormapPool(entries=[
        ColormapPoolEntry(name="bids:viridis", type="sequential", semantic_register="neutral_analytical",
                          status=ReviewStatus.approved),
        ColormapPoolEntry(name="colorcurious:carto.Antique", type="categorical",
                          semantic_register="experimental_aesthetic"),
        ColormapPoolEntry(name="colorcurious:crameri.roma", type="diverging", semantic_register="neutral_analytical",
                          source=ColormapSource(collection="crameri", catalog_id="crameri.roma",
                                                attribution="Crameri", license="CC-BY-4.0", url="https://x"),
                          tags=["scientific"]),
        ColormapPoolEntry(name="custom:teal", type="sequential", semantic_register="water",
                          colors=["#003333", "#ffffff"]),
    ])
    lut = tmp_path / "lut.png"
    out = export_colormaps(pool, lut)
    assert out["schemaVersion"] == SCHEMA_VERSION == 3
    by = {e["name"]: e for e in out["entries"]}
    assert by["bids:viridis"]["row"] == 0
    assert by["colorcurious:carto.Antique"]["row"] is None            # categorical: no LUT row
    assert by["colorcurious:crameri.roma"]["row"] == 1                # rows stay contiguous
    assert by["custom:teal"]["row"] == 2 and by["custom:teal"]["hasColors"] is True
    assert by["colorcurious:crameri.roma"]["collection"] == "crameri"
    assert by["colorcurious:crameri.roma"]["source"]["license"] == "CC-BY-4.0"
    assert by["colorcurious:crameri.roma"]["tags"] == ["scientific"]
    assert by["bids:viridis"]["source"] is None and by["bids:viridis"]["collection"] == "bids"
    assert by["custom:teal"]["collection"] == "custom"
    sprite = np.asarray(Image.open(lut))
    assert sprite.shape == (3, LUT_WIDTH, 4)
    assert by["colorcurious:crameri.roma"]["previewStops"][0].startswith("#")
    json.dumps(out)  # serialisable


def test_export_indices_platform_and_wavelength_constants():
    from spectralcular.export_web import MAX_GPU_BANDS, export_indices, export_platforms
    from spectralcular.pool import IndexPool, IndexPoolEntry

    pool = IndexPool(entries=[
        IndexPoolEntry(name="FAI", theme="water", bands={"N": "nir", "R": "red", "S1": "swir16"}),
        IndexPoolEntry(name="NDVI", theme="vegetation", bands={"N": "nir", "R": "red"}),
        IndexPoolEntry(name="ANDWI", theme="water",
                       bands={"B": "blue", "G": "green", "R": "red", "N": "nir", "S1": "swir16", "S2": "swir22"}),
        IndexPoolEntry(name="NDVIT", theme="burn", platform="landsat-c2-l2",
                       bands={"N": "nir08", "R": "red", "T": "lwir11"}),
        IndexPoolEntry(name="NDPolI", theme="radar", platform="sentinel-1-rtc", bands={"VV": "vv", "VH": "vh"}),
    ])
    out = export_indices(pool)
    by = {e["name"]: e for e in out["entries"]}
    # Wavelength constants come from the platform, so FAI is now computable.
    assert by["FAI"]["computable"] is True and by["FAI"]["gpuSupported"] is True
    assert by["FAI"]["constants"] == {"lambdaN": 842.0, "lambdaR": 665.0, "lambdaS1": 1610.0}
    assert by["NDVI"]["platform"] == "sentinel-2-l2a" and by["NDVI"]["gsdM"] == 10
    assert by["ANDWI"]["gpuSupported"] is (6 <= MAX_GPU_BANDS)
    assert by["NDVIT"]["platform"] == "landsat-c2-l2" and by["NDVIT"]["gsdM"] == 30
    assert by["NDVIT"]["bands"] == {"N": "nir08", "R": "red", "T": "lwir11"}
    assert by["NDPolI"]["platform"] == "sentinel-1-rtc" and by["NDPolI"]["gsdM"] == 10
    json.dumps(out)

    plats = export_platforms()
    ids = [p["id"] for p in plats["entries"]]
    assert ids == ["sentinel-2-l2a", "landsat-c2-l2", "sentinel-1-rtc"]
    ls = next(p for p in plats["entries"] if p["id"] == "landsat-c2-l2")
    assert ls["mask"] == {"asset": "qa_pixel", "kind": "qa-bits", "values": [0, 1, 2, 3, 4, 5]}
    assert ls["valueScale"]["lwir11"] == [0.00341802, 149.0]
    assert ls["gridProperty"] == ["landsat:wrs_path", "landsat:wrs_row"]
    # Planetary Computer's landsat-c2-l2 spans Landsat 4–9; the picker must ask for OLI/TIRS only.
    assert ls["stacQuery"] == {"platform": {"in": ["landsat-8", "landsat-9"]}}
    assert next(p for p in plats["entries"] if p["id"] == "sentinel-2-l2a")["stacQuery"] == {}
    json.dumps(plats)
