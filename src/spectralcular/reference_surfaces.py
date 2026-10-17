"""Reference surfaces: measured spectra of familiar landscapes, per platform.

Each surface (open water, rainforest, desert sand, fresh snow, …) is sampled
from real imagery at a documented site: the median of a small pixel window in a
scene the product's own quality layer agrees with (Sentinel-2 SCL class,
Landsat QA_PIXEL bits), with a spectral sanity check and a homogeneity check.
``spectralcular reference-surfaces`` does the sampling and writes
``config/reference_surfaces.yaml``; ``spectralcular export-web`` then evaluates every
index on those spectra (with spyndex) so the picker can show where familiar
surfaces fall on each index's scale.

Values are stored in physical units, exactly as the pipeline computes indices:
reflectance for optical bands, kelvin for Landsat thermal, linear gamma0 for
Sentinel-1.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import yaml
from pydantic import BaseModel, Field

from .platforms import PLATFORMS, Platform

log = logging.getLogger(__name__)

WINDOW_M = 90.0
# Relative spread (std / median) of the brightest band above which a window is
# a mixture rather than one surface.
MAX_CV = 0.25

# Sentinel-2 L2A scene classification
SCL_DARK, SCL_VEG, SCL_BARE, SCL_WATER, SCL_CLOUD_MED, SCL_CLOUD_HIGH, SCL_SNOW = 2, 4, 5, 6, 8, 9, 11
# Landsat C2 QA_PIXEL bit positions
QA_CLOUD, QA_SNOW, QA_CLEAR, QA_WATER = 3, 5, 6, 7

Check = Callable[[dict[str, float]], bool]


def _nd(a: float, b: float) -> float:
    return (a - b) / (a + b) if (a + b) else 0.0


@dataclass(frozen=True)
class Site:
    """Where and when to look for one surface on one platform."""

    points: tuple[tuple[float, float], ...]  # candidate (lon, lat), tried in order
    datetime: str  # STAC interval
    max_cloud: float | None = 20.0
    min_cloud: float | None = None
    scl: tuple[int, ...] = ()  # Sentinel-2: every window pixel in one of these
    qa: str | None = None  # Landsat: "clear" | "water" | "snow" | "cloud"
    check: Check | None = None  # spectral sanity check on the median spectrum


@dataclass(frozen=True)
class SurfaceSpec:
    id: str
    label: str  # short tick label
    description: str
    sites: dict[str, Site] = field(default_factory=dict)  # platform id → site


def _water(s: dict[str, float]) -> bool:
    return s["N"] < 0.06 and s["S1"] < 0.05


def _canopy(s: dict[str, float]) -> bool:
    return _nd(s["N"], s["R"]) > 0.75


def _crops(s: dict[str, float]) -> bool:
    return _nd(s["N"], s["R"]) > 0.65


def _dry_grass(s: dict[str, float]) -> bool:
    return 0.1 < _nd(s["N"], s["R"]) < 0.35


def _bare(s: dict[str, float]) -> bool:
    return _nd(s["N"], s["R"]) < 0.25 and s["S1"] > s["N"]


def _urban(s: dict[str, float]) -> bool:
    return _nd(s["N"], s["R"]) < 0.25


def _snow(s: dict[str, float]) -> bool:
    return s["G"] > 0.5 and _nd(s["G"], s["S1"]) > 0.6


def _burn(s: dict[str, float]) -> bool:
    return s["N"] < 0.2 and _nd(s["N"], s["S2"]) < 0.0


def _cloud(s: dict[str, float]) -> bool:
    return s["B"] > 0.4 and s["N"] > 0.4


S2, LS, S1 = "sentinel-2-l2a", "landsat-c2-l2", "sentinel-1-rtc"

SURFACES: tuple[SurfaceSpec, ...] = (
    SurfaceSpec(
        "open_water", "water", "Clear open water: the Ionian Sea off Sicily (Lake Geneva for radar).",
        {
            S2: Site(((15.40, 37.60), (15.50, 37.40)), "2023-06-01/2023-09-30", 5, scl=(SCL_WATER,), check=_water),
            LS: Site(((15.40, 37.60), (15.30, 37.30)), "2023-06-01/2023-09-30", 10, qa="water", check=_water),
            S1: Site(((6.55, 46.45), (6.45, 46.42)), "2023-06-01/2023-09-30", None),
        },
    ),
    SurfaceSpec(
        "turbid_water", "turbid", "Sediment-laden water: the Río de la Plata estuary.",
        {
            S2: Site(((-57.70, -34.80), (-57.90, -34.70)), "2022-12-01/2023-03-31", 10, scl=(SCL_WATER,)),
            LS: Site(((-57.70, -34.80), (-57.90, -34.70)), "2022-12-01/2023-03-31", 10, qa="water"),
        },
    ),
    SurfaceSpec(
        "rainforest", "forest", "Closed tropical rainforest canopy north of Manaus, dry season.",
        {
            S2: Site(((-60.15, -2.55), (-60.30, -2.45), (-60.05, -2.70)), "2023-07-01/2023-10-31", 30, scl=(SCL_VEG,), check=_canopy),
            LS: Site(((-60.15, -2.55), (-60.30, -2.45), (-60.05, -2.70)), "2023-07-01/2023-10-31", 30, qa="clear", check=_canopy),
            S1: Site(((-60.15, -2.55),), "2023-07-01/2023-10-31", None),
        },
    ),
    SurfaceSpec(
        "cropland", "crops", "Irrigated or rain-fed crops at peak green: Iowa corn and soy, late July.",
        {
            S2: Site(((-93.55, 42.12), (-93.70, 42.05), (-93.45, 41.95)), "2023-07-15/2023-08-15", 10, scl=(SCL_VEG,), check=_crops),
            LS: Site(((-93.55, 42.12), (-93.70, 42.05), (-93.45, 41.95)), "2023-07-10/2023-08-20", 10, qa="clear", check=_crops),
            S1: Site(((-93.55, 42.12),), "2023-07-15/2023-08-15", None),
        },
    ),
    SurfaceSpec(
        "dry_grass", "dry grass", "Senescent annual grassland: the Carrizo Plain, California, in late summer.",
        {
            S2: Site(((-119.80, 35.15), (-119.70, 35.10), (-119.90, 35.25)), "2023-08-01/2023-09-30", 5, scl=(SCL_VEG, SCL_BARE), check=_dry_grass),
            LS: Site(((-119.80, 35.15), (-119.70, 35.10), (-119.90, 35.25)), "2023-08-01/2023-09-30", 5, qa="clear", check=_dry_grass),
        },
    ),
    SurfaceSpec(
        "bare_soil", "soil", "Bare cropland between crops: Punjab after the wheat harvest, before rice is transplanted.",
        {
            S2: Site(((75.80, 30.85), (75.95, 30.75), (75.70, 30.95), (75.60, 30.80)), "2023-05-10/2023-06-10", 10, scl=(SCL_BARE,), check=_bare),
            LS: Site(((75.80, 30.85), (75.95, 30.75), (75.70, 30.95), (75.60, 30.80)), "2023-05-05/2023-06-15", 10, qa="clear", check=_bare),
            S1: Site(((75.80, 30.85),), "2023-05-10/2023-06-10", None),
        },
    ),
    SurfaceSpec(
        "desert_sand", "sand", "Dune sand: the Rub' al Khali, Arabian Peninsula.",
        {
            S2: Site(((50.00, 20.50), (49.50, 20.00)), "2023-01-01/2023-12-31", 2, scl=(SCL_BARE,), check=_bare),
            LS: Site(((50.00, 20.50), (49.50, 20.00)), "2023-01-01/2023-12-31", 2, qa="clear", check=_bare),
            S1: Site(((50.00, 20.50), (49.50, 20.00)), "2023-01-01/2023-12-31", None),
        },
    ),
    SurfaceSpec(
        "urban", "city", "Dense city fabric with little vegetation: central Cairo.",
        {
            S2: Site(((31.245, 30.055), (31.255, 30.065), (31.235, 30.045)), "2023-01-01/2023-12-31", 5, scl=(SCL_BARE,), check=_urban),
            LS: Site(((31.245, 30.055), (31.255, 30.065), (31.235, 30.045)), "2023-01-01/2023-12-31", 5, qa="clear", check=_urban),
            S1: Site(((31.245, 30.055),), "2023-01-01/2023-12-31", None),
        },
    ),
    SurfaceSpec(
        "snow", "snow", "Fresh snow on the Aletsch glacier accumulation area, spring.",
        {
            S2: Site(((8.05, 46.50), (8.00, 46.53), (8.02, 46.48)), "2023-03-01/2023-05-15", 20, scl=(SCL_SNOW,), check=_snow),
            LS: Site(((8.05, 46.50), (8.00, 46.53), (8.02, 46.48)), "2023-03-01/2023-05-31", 20, qa="snow", check=_snow),
        },
    ),
    SurfaceSpec(
        "burn_scar", "burn", "Fresh burn scar weeks after the July 2023 Rhodes wildfire.",
        {
            S2: Site(((28.00, 36.12), (28.05, 36.10), (27.95, 36.15), (28.08, 36.14)), "2023-08-01/2023-09-30", 5, scl=(SCL_DARK, SCL_VEG, SCL_BARE), check=_burn),
            LS: Site(((28.00, 36.12), (28.05, 36.10), (27.95, 36.15), (28.08, 36.14)), "2023-08-01/2023-10-15", 10, qa="clear", check=_burn),
        },
    ),
    SurfaceSpec(
        "cloud", "cloud", "Thick convective cloud over the Amazon, wet season.",
        {
            S2: Site(((-60.15, -2.55), (-60.60, -3.10), (-59.50, -2.20)), "2023-01-01/2023-04-30", None, min_cloud=85, scl=(SCL_CLOUD_HIGH,), check=_cloud),
            LS: Site(((-60.15, -2.55), (-60.60, -3.10), (-59.50, -2.20)), "2023-01-01/2023-04-30", None, min_cloud=85, qa="cloud", check=_cloud),
        },
    ),
)


# ---------------------------------------------------------------------------
# File model
# ---------------------------------------------------------------------------


class SurfaceSample(BaseModel):
    item: str
    datetime: str
    lonlat: tuple[float, float]
    window_m: float
    # spyndex band param → physical value (median over the window)
    values: dict[str, float]
    # spyndex band param → relative spread (std / |median|)
    spread: dict[str, float] = Field(default_factory=dict)
    orbit: str | None = None


class Surface(BaseModel):
    id: str
    label: str
    description: str
    samples: dict[str, SurfaceSample] = Field(default_factory=dict)


class SurfacesFile(BaseModel):
    generated: str
    window_m: float
    surfaces: list[Surface]


HEADER = """\
# Reference surfaces: measured spectra of familiar landscapes, one sample per
# platform. Generated by `spectralcular reference-surfaces` from real scenes (the item,
# date and pixel are recorded with each sample); `spectralcular export-web` evaluates every
# index on these values so the plate picker can mark where water, forest, sand,
# snow and so on fall on each index's scale. Regenerate rather than hand-edit.
"""


def load_surfaces(path: Path) -> SurfacesFile | None:
    if not path.exists():
        return None
    return SurfacesFile.model_validate(yaml.safe_load(path.read_text()))


def dump_surfaces(sf: SurfacesFile, path: Path) -> None:
    body = yaml.safe_dump(sf.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=120)
    path.write_text(HEADER + body)


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def _client(platform: Platform):
    from pystac_client import Client

    if platform.signing == "planetary-computer":
        import planetary_computer

        return Client.open(platform.stac_api, modifier=planetary_computer.sign_inplace)
    return Client.open(platform.stac_api)


def _read_window(href: str, lon: float, lat: float, half_m: float) -> np.ndarray:
    import rasterio
    from rasterio.warp import transform
    from rasterio.windows import from_bounds

    with rasterio.open(href) as ds:
        (x,), (y,) = transform("EPSG:4326", ds.crs, [lon], [lat])
        win = from_bounds(x - half_m, y - half_m, x + half_m, y + half_m, ds.transform).round_offsets().round_lengths()
        arr = ds.read(1, window=win, boundless=True, fill_value=0).astype("float64")
    return arr


def _mask_ok(platform: Platform, site: Site, arr: np.ndarray) -> bool:
    vals = arr.astype("int64").ravel()
    if platform.mask is None:
        return True
    if platform.mask.kind == "scl-classes":
        return bool(site.scl) and bool(np.isin(vals, site.scl).all())
    bit = {"clear": QA_CLEAR, "water": QA_WATER, "snow": QA_SNOW, "cloud": QA_CLOUD}[site.qa or "clear"]
    has = (vals >> bit) & 1 == 1
    if site.qa == "clear":
        has &= (vals >> QA_WATER) & 1 == 0
    return bool(has.all())


def _sample_item(platform: Platform, site: Site, item, lon: float, lat: float) -> SurfaceSample | None:
    half = WINDOW_M / 2
    if platform.mask is not None:
        mask_asset = item.assets.get(platform.mask.asset)
        if mask_asset is None or not _mask_ok(platform, site, _read_window(mask_asset.href, lon, lat, half)):
            return None
    values: dict[str, float] = {}
    spread: dict[str, float] = {}
    for param, asset in platform.param_to_asset.items():
        a = item.assets.get(asset)
        if a is None:
            continue
        raw = _read_window(a.href, lon, lat, half)
        nod = platform.nodata_for(asset)
        good = raw[np.isfinite(raw) & (raw != nod if nod is not None else True)]
        if good.size == 0:
            return None
        scale, offset = platform.scale_for(asset)
        phys = good * scale + offset
        if asset.startswith("lwir"):
            # Surface temperature is undefined under cloud (the product fills ~149 K).
            phys = phys[phys > 180.0]
            if phys.size == 0:
                continue
        med = float(np.median(phys))
        values[param] = round(med, 5)
        spread[param] = round(float(np.std(phys) / abs(med)) if med else 0.0, 3)
    if not values:
        return None
    if site.check is not None and not site.check(values):
        log.info("  %s: spectrum fails check %s", item.id, {k: values[k] for k in sorted(values)})
        return None
    # Radar speckle makes any window "inhomogeneous"; the median is the estimate.
    optical = [p for p in ("N", "R", "G") if p in values]
    key = max(optical, key=lambda p: values[p]) if optical else None
    if key is not None and spread.get(key, 0.0) > MAX_CV:
        log.info("  %s: window not homogeneous (%s cv %.2f)", item.id, key, spread[key])
        return None
    return SurfaceSample(
        item=item.id,
        datetime=str(item.properties.get("datetime", ""))[:19],
        lonlat=(lon, lat),
        window_m=WINDOW_M,
        values=values,
        spread=spread,
        orbit=item.properties.get("sat:orbit_state"),
    )


def sample_site(platform: Platform, site: Site, *, max_items: int = 12) -> SurfaceSample | None:
    client = _client(platform)
    for lon, lat in site.points:
        query: dict = dict(platform.stac_query)
        if platform.cloud_property and site.max_cloud is not None:
            query[platform.cloud_property] = {"lt": site.max_cloud}
        if platform.cloud_property and site.min_cloud is not None:
            query[platform.cloud_property] = {"gt": site.min_cloud}
        search = client.search(
            collections=[platform.collection],
            intersects={"type": "Point", "coordinates": [lon, lat]},
            datetime=site.datetime,
            query=query or None,
            max_items=40,
        )
        items = list(search.items())
        if platform.cloud_property and site.min_cloud is None:
            items.sort(key=lambda it: it.properties.get(platform.cloud_property, 100))
        for item in items[:max_items]:
            try:
                s = _sample_item(platform, site, item, lon, lat)
            except Exception as e:  # noqa: BLE001 — a bad asset shouldn't end the search
                log.info("  %s: read failed (%s)", item.id, e)
                continue
            if s is not None:
                return s
    return None


def sample_surfaces(existing: SurfacesFile | None = None, *, only: set[str] | None = None) -> SurfacesFile:
    """Sample every (surface, platform) pair; keep existing samples for pairs not re-run."""
    prior = {s.id: s for s in existing.surfaces} if existing else {}
    out: list[Surface] = []
    for spec in SURFACES:
        samples = dict(prior[spec.id].samples) if spec.id in prior else {}
        for pid, site in spec.sites.items():
            if only and spec.id not in only and pid not in only:
                continue
            log.warning("sampling %s on %s…", spec.id, pid)
            s = sample_site(PLATFORMS[pid], site)
            if s is None:
                log.warning("  no acceptable sample for %s on %s", spec.id, pid)
                continue
            samples[pid] = s
        out.append(Surface(id=spec.id, label=spec.label, description=spec.description, samples=samples))
    return SurfacesFile(generated=dt.date.today().isoformat(), window_m=WINDOW_M, surfaces=out)
