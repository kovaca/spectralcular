"""Spread configuration: pydantic models + YAML loader."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .framing import FrameSpec
from .truecolor import TrueColorSpec


class Location(BaseModel):
    name: str
    bbox: tuple[float, float, float, float]  # lon_min, lat_min, lon_max, lat_max

    @field_validator("bbox")
    @classmethod
    def _bbox_ordered(cls, v: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        lon_min, lat_min, lon_max, lat_max = v
        if not (lon_min < lon_max and lat_min < lat_max):
            raise ValueError(f"bbox must be (lon_min < lon_max, lat_min < lat_max); got {v}")
        return v


class Imagery(BaseModel):
    # Platform id (see platforms.PLATFORMS): sentinel-2-l2a, landsat-c2-l2, sentinel-1-rtc.
    source: str = "sentinel-2-l2a"
    date_range: str  # ISO interval "YYYY-MM-DD/YYYY-MM-DD"
    # Scene cloud-cover ceiling (%); ignored for platforms without a cloud property.
    cloud_cover_max: float | None = 10.0
    # Radar only: restrict to one pass direction so ascending and descending
    # swaths never mosaic together. Defaults to ascending when unset.
    orbit_state: Literal["ascending", "descending"] | None = None
    # Pin specific STAC item ids (from the plate picker). When set, the date
    # range and cloud filter are skipped and exactly these scenes are loaded.
    item_ids: list[str] | None = None

    @field_validator("source")
    @classmethod
    def _known_platform(cls, v: str) -> str:
        from .platforms import PLATFORMS
        if v not in PLATFORMS:
            raise ValueError(f"imagery.source {v!r} not in {sorted(PLATFORMS)}")
        return v

    @property
    def platform(self):
        from .platforms import get_platform
        return get_platform(self.source)


class Index(BaseModel):
    name: str  # spyndex short name, e.g. "NDVI"
    bands: dict[str, str]  # spyndex param → STAC asset key on the imagery platform, e.g. {"N": "nir", "R": "red"}


class Normalization(BaseModel):
    method: Literal["percentile", "fixed", "diverging"] = "percentile"
    lower: float | None = 1.0
    upper: float | None = 99.0
    vmin: float | None = None
    vmax: float | None = None


class Colormap(BaseModel):
    # palettize preset id ("bids:viridis"), catalog row ("colorcurious:crameri.roma"),
    # or any name with inline ``colors`` — see :func:`colormaps.resolve_colormap`.
    name: str
    reversed: bool = False
    colors: list[str] | None = None  # copied from the pool entry at lock time
    normalization: Normalization = Field(default_factory=Normalization)


class Layout(BaseModel):
    template: Literal["full_image_plus_diagnostics"] = "full_image_plus_diagnostics"
    page_size: Literal["A4", "Letter"] = "Letter"
    orientation: Literal["portrait", "landscape"] = "portrait"
    dpi: int = 300
    annotations: Literal["none", "minimal", "technical"] = "minimal"
    caption: str = ""
    plate_number: str = "001"
    interpretive_note: str = ""
    outputs: list[Literal["pdf", "html"]] = Field(default_factory=lambda: ["pdf"])


class SpreadConfig(BaseModel):
    """One plate. Either an *index* plate (``index`` + ``colormap``) or a
    *true-color* plate (``truecolor``: the platform's red/green/blue bands
    through the tone pipeline in :mod:`truecolor`) — never both."""

    asset_id: str
    location: Location
    imagery: Imagery
    index: Index | None = None
    colormap: Colormap | None = None
    truecolor: TrueColorSpec | None = None
    # Drop the platform's quality-mask classes (clouds, shadow, …) to
    # transparent, as the picker's mask toggle does. Nodata is always
    # transparent.
    mask_enabled: bool = True
    layout: Layout = Field(default_factory=Layout)
    # Explicit page frame (center / width / rotation) from the plate picker.
    # When set, the renderer warps the index raster onto this exact page grid
    # instead of deriving a north-up frame from the data extent.
    frame: FrameSpec | None = None

    @model_validator(mode="after")
    def _one_kind(self):
        if self.truecolor is not None:
            if self.index is not None or self.colormap is not None:
                raise ValueError("a true-color spread has no index or colormap")
            if self.imagery.platform.rgb_assets is None:
                raise ValueError(f"imagery.source {self.imagery.source!r} has no visible red/green/blue bands")
        elif self.index is None or self.colormap is None:
            raise ValueError("a spread needs index + colormap, or truecolor")
        return self

    @property
    def kind(self) -> Literal["index", "truecolor"]:
        return "truecolor" if self.truecolor is not None else "index"


def load_config(path: str | Path) -> SpreadConfig:
    path = Path(path)
    with path.open("r") as f:
        raw = yaml.safe_load(f)
    return SpreadConfig.model_validate(raw)
