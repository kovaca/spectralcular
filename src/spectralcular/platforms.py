"""Imagery platforms: the single source of truth for everything that used to be
hard-coded to Sentinel-2 L2A on Earth Search.

A :class:`Platform` records where a collection lives (STAC API, collection id,
signing), how spyndex band parameters map onto its STAC asset keys, how raw
digital numbers become physical values, which asset (if any) carries a
pixel-quality mask and how to read it, and the band-center wavelengths that a
handful of spyndex indices take as constants.

The Python pipeline, the pool generator, ``spectralcular export-web`` and (through
``platforms.json``) the web plate picker all read from :data:`PLATFORMS`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

PlatformId = Literal["sentinel-2-l2a", "landsat-c2-l2", "sentinel-1-rtc"]

EARTH_SEARCH_URL = "https://earth-search.aws.element84.com/v1"
PLANETARY_COMPUTER_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
PLANETARY_COMPUTER_SAS_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token"

# Sentinel-2 SCL classes masked out: no_data, saturated, cloud shadow, mid+high
# cloud, cirrus, snow.
# https://sentinels.copernicus.eu/web/sentinel/technical-guides/sentinel-2-msi/level-2a/algorithm-overview
S2_SCL_MASK_CLASSES: tuple[int, ...] = (0, 1, 3, 8, 9, 10, 11)

# Landsat Collection 2 QA_PIXEL bit positions: fill, dilated cloud, cirrus,
# cloud, cloud shadow, snow.
# https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands
LANDSAT_QA_MASK_BITS: tuple[int, ...] = (0, 1, 2, 3, 4, 5)

DEFAULT_KEY = "*"


class MaskSpec(BaseModel):
    """Which asset holds the pixel-quality mask and how to interpret it.

    ``scl-classes``: the asset is a class raster; mask pixels whose class is in
    ``values``. ``qa-bits``: the asset is a bit-field; mask pixels where any bit
    position in ``values`` is set.
    """

    asset: str
    kind: Literal["scl-classes", "qa-bits"]
    values: list[int]

    @property
    def bitmask(self) -> int:
        """Bits set for each entry of ``values`` (class ids or bit positions)."""
        m = 0
        for v in self.values:
            m |= 1 << v
        return m


class Platform(BaseModel):
    id: str
    label: str
    stac_api: str
    collection: str
    signing: Literal["none", "planetary-computer"] = "none"
    # spyndex band parameter → STAC asset key
    param_to_asset: dict[str, str]
    # asset key → native ground sample distance (m)
    gsd_by_asset: dict[str, int]
    # asset key → (scale, offset): value = DN * scale + offset. "*" is the default.
    value_scale: dict[str, tuple[float, float]] = Field(default_factory=lambda: {DEFAULT_KEY: (1.0, 0.0)})
    # asset key → nodata DN (None: rely on the file's nodata / NaN). "*" is the default.
    nodata: dict[str, float | None] = Field(default_factory=lambda: {DEFAULT_KEY: None})
    mask: MaskSpec | None = None
    # STAC item property carrying scene cloud cover (None for radar).
    cloud_property: str | None = None
    # spyndex wavelength constants (lambdaG, lambdaR, …) → band center in nm.
    wavelength_nm: dict[str, float] = Field(default_factory=dict)
    # Every spectral band the sensor carries, in wavelength order, whether or
    # not the pipeline reads it: asset key → (band code, plain name, center nm).
    # Drawn as the "bands in use" ruler on the diagnostic page. Empty for radar.
    bands_nm: dict[str, tuple[str, str, float]] = Field(default_factory=dict)
    # Item property (or pair of properties) identifying the acquisition tile.
    grid_property: str | tuple[str, str] | None = None
    # Item property selecting one orbit direction (radar): mosaics must never
    # mix ascending and descending passes.
    orbit_property: str | None = None
    # Extra STAC ``query`` clauses applied to every scene search on this
    # platform, e.g. restricting a multi-mission collection to the sensors whose
    # assets match ``param_to_asset``.
    stac_query: dict[str, dict] = Field(default_factory=dict)
    # Visible red, green, blue asset keys for true-color plates; None when the
    # sensor has no visible bands (radar).
    rgb_assets: tuple[str, str, str] | None = None
    # Pixel grid the pipeline loads this platform at (m).
    native_res_m: float
    # Data-type hint for the web renderer: uint16 DNs or float32 physical values.
    dtype: Literal["uint16", "float32"] = "uint16"
    # float32 platforms only: physical value mapped to the top of the 16-bit
    # texture range when the web renderer quantises tiles (linear gamma0 rarely
    # exceeds ~1; 2.0 keeps urban double-bounce while leaving 3e-5 steps).
    value_ceiling: float | None = None

    @model_validator(mode="after")
    def _assets_have_gsd(self):
        missing = sorted(a for a in self.param_to_asset.values() if a not in self.gsd_by_asset)
        if missing:
            raise ValueError(f"platform {self.id}: assets without gsd_by_asset entry: {missing}")
        if self.rgb_assets is not None and (bad := [a for a in self.rgb_assets if a not in self.gsd_by_asset]):
            raise ValueError(f"platform {self.id}: rgb_assets without gsd_by_asset entry: {bad}")
        if self.mask is not None and self.mask.asset not in self.gsd_by_asset:
            raise ValueError(f"platform {self.id}: mask asset {self.mask.asset!r} has no gsd_by_asset entry")
        return self

    # -- helpers -----------------------------------------------------------

    @property
    def params(self) -> set[str]:
        return set(self.param_to_asset)

    def scale_for(self, asset: str) -> tuple[float, float]:
        return self.value_scale.get(asset, self.value_scale.get(DEFAULT_KEY, (1.0, 0.0)))

    def nodata_for(self, asset: str) -> float | None:
        if asset in self.nodata:
            return self.nodata[asset]
        return self.nodata.get(DEFAULT_KEY)

    def gsd_for_assets(self, assets) -> int | None:
        gsds = [self.gsd_by_asset[a] for a in assets if a in self.gsd_by_asset]
        return max(gsds) if gsds else None

    def supports(self, band_params) -> bool:
        """True when every spyndex band parameter maps to one of our assets."""
        return set(band_params).issubset(self.params)


SENTINEL_2_L2A = Platform(
    id="sentinel-2-l2a",
    label="Sentinel-2 L2A",
    stac_api=EARTH_SEARCH_URL,
    collection="sentinel-2-l2a",
    signing="none",
    param_to_asset={
        "A": "coastal",
        "B": "blue", "G": "green", "R": "red",
        "RE1": "rededge1", "RE2": "rededge2", "RE3": "rededge3",
        "N": "nir", "N2": "nir08",
        "S1": "swir16", "S2": "swir22",
    },
    gsd_by_asset={
        "red": 10, "green": 10, "blue": 10, "nir": 10,
        "rededge1": 20, "rededge2": 20, "rededge3": 20,
        "nir08": 20, "swir16": 20, "swir22": 20, "scl": 20,
        "coastal": 60, "nir09": 60, "cirrus": 60,
    },
    # L2A surface reflectance: DN / 10000 (no offset; matches the pipeline's
    # historical convention — see indices.py).
    value_scale={DEFAULT_KEY: (1.0 / 10000.0, 0.0), "scl": (1.0, 0.0)},
    nodata={DEFAULT_KEY: 0.0},
    mask=MaskSpec(asset="scl", kind="scl-classes", values=list(S2_SCL_MASK_CLASSES)),
    cloud_property="eo:cloud_cover",
    wavelength_nm={
        "lambdaG": 560.0, "lambdaR": 665.0, "lambdaN": 842.0,
        "lambdaN2": 865.0, "lambdaS1": 1610.0, "lambdaS2": 2190.0,
    },
    # MSI band centers (S2A), including the 60 m bands the pipeline never reads.
    bands_nm={
        "coastal": ("B01", "coastal", 443.0),
        "blue": ("B02", "blue", 490.0),
        "green": ("B03", "green", 560.0),
        "red": ("B04", "red", 665.0),
        "rededge1": ("B05", "red edge", 705.0),
        "rededge2": ("B06", "red edge", 740.0),
        "rededge3": ("B07", "red edge", 783.0),
        "nir": ("B08", "near-infrared", 842.0),
        "nir08": ("B8A", "narrow NIR", 865.0),
        "nir09": ("B09", "water vapour", 945.0),
        "cirrus": ("B10", "cirrus", 1375.0),
        "swir16": ("B11", "SWIR", 1610.0),
        "swir22": ("B12", "SWIR", 2190.0),
    },
    grid_property="grid:code",
    rgb_assets=("red", "green", "blue"),
    native_res_m=10,
    dtype="uint16",
)

LANDSAT_C2_L2 = Platform(
    id="landsat-c2-l2",
    label="Landsat 8/9 C2 L2",
    stac_api=PLANETARY_COMPUTER_URL,
    collection="landsat-c2-l2",
    signing="planetary-computer",
    # Thermal: spyndex's TM/ETM+ "T" and OLI/TIRS "T1" both map to TIRS band 10.
    param_to_asset={
        "A": "coastal", "B": "blue", "G": "green", "R": "red",
        "N": "nir08", "S1": "swir16", "S2": "swir22",
        "T": "lwir11", "T1": "lwir11", "T2": "lwir12",
    },
    # Everything is delivered on the 30 m product grid; TIRS is 100 m native.
    gsd_by_asset={
        "coastal": 30, "blue": 30, "green": 30, "red": 30,
        "nir08": 30, "swir16": 30, "swir22": 30,
        "lwir11": 30, "lwir12": 30, "qa_pixel": 30,
    },
    value_scale={
        DEFAULT_KEY: (0.0000275, -0.2),        # surface reflectance
        "lwir11": (0.00341802, 149.0),         # surface temperature, kelvin
        "lwir12": (0.00341802, 149.0),
        "qa_pixel": (1.0, 0.0),
    },
    nodata={DEFAULT_KEY: 0.0},
    mask=MaskSpec(asset="qa_pixel", kind="qa-bits", values=list(LANDSAT_QA_MASK_BITS)),
    cloud_property="eo:cloud_cover",
    wavelength_nm={
        "lambdaG": 561.0, "lambdaR": 655.0, "lambdaN": 865.0,
        "lambdaS1": 1609.0, "lambdaS2": 2201.0,
    },
    # OLI + TIRS band centers (Landsat 8); TIRS sits on the ruler's thermal segment.
    bands_nm={
        "coastal": ("B1", "coastal", 443.0),
        "blue": ("B2", "blue", 482.0),
        "green": ("B3", "green", 561.0),
        "red": ("B4", "red", 655.0),
        "nir08": ("B5", "near-infrared", 865.0),
        "swir16": ("B6", "SWIR", 1609.0),
        "swir22": ("B7", "SWIR", 2201.0),
        "lwir11": ("B10", "thermal", 10895.0),
        "lwir12": ("B11", "thermal", 12005.0),
    },
    grid_property=("landsat:wrs_path", "landsat:wrs_row"),
    # landsat-c2-l2 also holds Landsat 4–7 (TM/ETM+), whose items name the
    # thermal band ``lwir`` and lack ``coastal``; keep to OLI/TIRS.
    stac_query={"platform": {"in": ["landsat-8", "landsat-9"]}},
    rgb_assets=("red", "green", "blue"),
    native_res_m=30,
    dtype="uint16",
)

SENTINEL_1_RTC = Platform(
    id="sentinel-1-rtc",
    label="Sentinel-1 RTC",
    stac_api=PLANETARY_COMPUTER_URL,
    collection="sentinel-1-rtc",
    signing="planetary-computer",
    param_to_asset={"VV": "vv", "VH": "vh", "HH": "hh", "HV": "hv"},
    gsd_by_asset={"vv": 10, "vh": 10, "hh": 10, "hv": 10},
    # Linear gamma0 backscatter, float32, already physical.
    value_scale={DEFAULT_KEY: (1.0, 0.0)},
    nodata={DEFAULT_KEY: -32768.0},
    mask=None,
    cloud_property=None,
    wavelength_nm={},
    grid_property=None,
    orbit_property="sat:orbit_state",
    native_res_m=10,
    dtype="float32",
    value_ceiling=2.0,
)

PLATFORMS: dict[str, Platform] = {p.id: p for p in (SENTINEL_2_L2A, LANDSAT_C2_L2, SENTINEL_1_RTC)}
DEFAULT_PLATFORM_ID = SENTINEL_2_L2A.id



def get_platform(platform_id: str) -> Platform:
    try:
        return PLATFORMS[platform_id]
    except KeyError:
        raise KeyError(f"unknown platform {platform_id!r}; known: {sorted(PLATFORMS)}") from None
