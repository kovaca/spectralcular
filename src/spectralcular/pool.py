"""Stage-3 candidate pools: locations, indices, colormaps.

Each pool entry carries a `status` field that drives the editorial review
loop in docs/process.md Stage 3. Entries flow `proposed` → `approved` /
`rejected` / `needs_revision` by editing YAML in place.

Cross-validators check that every index name resolves through `spyndex`
and every colormap name resolves (through `palettize`, the vendored
colorcurious catalog, or inline ``colors``) so a typo in a YAML seed
surfaces at load time, not at render time.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


Theme = Literal[
    "vegetation",
    "water",
    "burn",
    "soil_geology",
    "urban",
    "snow_ice",
    "atmosphere",
    "radar",
]


class ReviewStatus(str, Enum):
    proposed = "proposed"
    approved = "approved"
    rejected = "rejected"
    needs_revision = "needs_revision"


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

class LocationPoolEntry(BaseModel):
    id: str
    name: str
    bbox: tuple[float, float, float, float]  # lon_min, lat_min, lon_max, lat_max
    landscape_character: str
    themes: list[Theme]
    season_windows: list[str]
    cloud_cover_max: float = 10.0
    source: str = ""
    status: ReviewStatus = ReviewStatus.proposed
    notes: str = ""
    reviewer_notes: str = ""

    @field_validator("bbox")
    @classmethod
    def _bbox_ordered(cls, v):
        lon_min, lat_min, lon_max, lat_max = v
        if not (lon_min < lon_max and lat_min < lat_max):
            raise ValueError(f"bbox must be (lon_min < lon_max, lat_min < lat_max); got {v}")
        return v

    @field_validator("season_windows")
    @classmethod
    def _at_least_one_window(cls, v):
        if not v:
            raise ValueError("season_windows must contain at least one ISO interval")
        for w in v:
            if "/" not in w:
                raise ValueError(f"season window {w!r} must be 'YYYY-MM-DD/YYYY-MM-DD'")
        return v


class LocationPool(BaseModel):
    entries: list[LocationPoolEntry]

    @field_validator("entries")
    @classmethod
    def _unique_ids(cls, v):
        seen: set[str] = set()
        for e in v:
            if e.id in seen:
                raise ValueError(f"duplicate location id {e.id!r}")
            seen.add(e.id)
        return v


# ---------------------------------------------------------------------------
# Indices
# ---------------------------------------------------------------------------

class IndexPoolEntry(BaseModel):
    name: str  # spyndex short name
    theme: Theme
    # Imagery platform the index is computed from (see platforms.PLATFORMS).
    platform: str = "sentinel-2-l2a"
    bands: dict[str, str]  # spyndex param → STAC asset key on ``platform``
    expressive_in: list[str] = Field(default_factory=list)
    status: ReviewStatus = ReviewStatus.proposed
    notes: str = ""

    @field_validator("name")
    @classmethod
    def _index_known_to_spyndex(cls, v):
        import spyndex
        if v not in spyndex.indices:
            raise ValueError(f"index {v!r} not in spyndex.indices")
        return v

    @field_validator("platform")
    @classmethod
    def _platform_known(cls, v):
        from .platforms import PLATFORMS
        if v not in PLATFORMS:
            raise ValueError(f"platform {v!r} not in {sorted(PLATFORMS)}")
        return v

    @model_validator(mode="after")
    def _bands_resolve_on_platform(self):
        from .platforms import get_platform
        plat = get_platform(self.platform)
        bad_params = sorted(p for p in self.bands if p not in plat.param_to_asset)
        if bad_params:
            raise ValueError(f"index {self.name}: params {bad_params} not available on {self.platform}")
        bad_assets = sorted(a for a in self.bands.values() if a not in plat.gsd_by_asset)
        if bad_assets:
            raise ValueError(f"index {self.name}: assets {bad_assets} unknown on {self.platform}")
        return self


class IndexPool(BaseModel):
    entries: list[IndexPoolEntry]

    @field_validator("entries")
    @classmethod
    def _unique_names(cls, v):
        # Index names are the identity used by candidates, spreads.csv, the web
        # manifest and ``spectralcular import``; one spyndex index lives on exactly one
        # platform in this pool.
        seen: set[str] = set()
        for e in v:
            if e.name in seen:
                raise ValueError(f"duplicate index name {e.name!r}")
            seen.add(e.name)
        return v

    def by_name(self) -> dict[str, IndexPoolEntry]:
        return {e.name: e for e in self.entries}


# ---------------------------------------------------------------------------
# Colormaps
# ---------------------------------------------------------------------------

ColormapType = Literal["sequential", "diverging", "cyclic", "categorical"]
SemanticRegister = Literal[
    "vegetation",
    "water",
    "burn",
    "neutral_analytical",
    "experimental_aesthetic",
    "snow_ice",
    "earth",
]
PrintSafety = Literal["safe", "watchlist", "unsafe", "unverified"]


class ColormapSource(BaseModel):
    """Upstream provenance of a colormap (carried through to the colophon)."""
    collection: str                 # "crameri", "cmocean", ...
    catalog_id: str | None = None   # colorcurious record id, e.g. "crameri.roma"
    attribution: str = ""
    license: str = ""
    url: str = ""


class ColormapPoolEntry(BaseModel):
    # palettize preset id ("bids:viridis"), a vendored catalog row
    # ("colorcurious:crameri.roma"), or any name when ``colors`` is given.
    name: str
    type: ColormapType
    semantic_register: SemanticRegister
    print_safety: PrintSafety = "unverified"
    pairs_with: list[str] = Field(default_factory=list)
    status: ReviewStatus = ReviewStatus.proposed
    notes: str = ""
    source: ColormapSource | None = None
    tags: list[str] = Field(default_factory=list)
    # Inline stops (>= 2 CSS colors, interpolated in sRGB) — lets anyone add a
    # colormap by pasting hex values, no palettize catalog knowledge needed.
    colors: list[str] | None = None

    @field_validator("colors")
    @classmethod
    def _colors_parse(cls, v):
        if v is None:
            return v
        if len(v) < 2:
            raise ValueError("colors needs at least 2 stops")
        from matplotlib.colors import is_color_like
        bad = [c for c in v if not is_color_like(c)]
        if bad:
            raise ValueError(f"unparseable colors: {bad[:3]}")
        return v

    @model_validator(mode="after")
    def _colormap_resolves(self):
        from .colormaps import check_resolvable
        check_resolvable(self.name, colors=self.colors)
        return self

    @property
    def short_name(self) -> str:
        from .colormaps import colormap_short_name
        return colormap_short_name(self.name)


class ColormapPool(BaseModel):
    entries: list[ColormapPoolEntry]

    @field_validator("entries")
    @classmethod
    def _unique_names(cls, v):
        seen: set[str] = set()
        for e in v:
            if e.name in seen:
                raise ValueError(f"duplicate colormap name {e.name!r}")
            seen.add(e.name)
        return v


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_yaml(path: str | Path) -> dict:
    with Path(path).open("r") as f:
        return yaml.safe_load(f)


def dump_location_pool(pool: LocationPool, path: str | Path) -> None:
    """Write the location pool back as YAML (stable key order, no ``None``)."""
    payload = {"entries": [e.model_dump(exclude_none=True, mode="json") for e in pool.entries]}
    Path(path).write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100))


class _NoAliasDumper(yaml.SafeDumper):
    """yaml.safe_dump emits ``&id001``/``*id001`` anchors for shared lists;
    the pool files are hand-edited, so never do that."""

    def ignore_aliases(self, data):  # noqa: D401
        return True


def dump_colormap_pool(pool: ColormapPool, path: str | Path, *, header: str = "") -> None:
    """Write the colormap pool back as YAML (stable key order, no ``None``,
    no anchors). ``header`` is emitted verbatim as leading comment lines."""
    payload = {"entries": [e.model_dump(exclude_none=True, mode="json") for e in pool.entries]}
    body = yaml.dump(payload, Dumper=_NoAliasDumper, sort_keys=False, allow_unicode=True,
                     width=100, default_flow_style=False)
    Path(path).write_text((header.rstrip("\n") + "\n\n" if header else "") + body)


def load_location_pool(path: str | Path) -> LocationPool:
    return LocationPool.model_validate(_load_yaml(path))


def load_index_pool(path: str | Path) -> IndexPool:
    return IndexPool.model_validate(_load_yaml(path))


def load_colormap_pool(path: str | Path) -> ColormapPool:
    return ColormapPool.model_validate(_load_yaml(path))
