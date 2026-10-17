"""Image framing — the actual displayed extent on page 1.

The user supplies an AOI bbox in the config. The page-1 plate has its own
aspect ratio (e.g. A4 portrait ≈ 0.707), and there's no guarantee the
user's bbox matches it. To keep "what the diagnostic reports" in lockstep
with "what's actually shown on page 1", we compute an :class:`ImageFrame`
that:

1. starts from the user's bbox,
2. is the largest centered rectangle *inside* the bbox whose on-the-ground
   (cos-corrected) aspect matches the page aspect — see
   :func:`fit_aspect_contract` (default), and
3. carries an optional ``rotation_deg`` so future non-North-up framings
   slot in without changing the API.

The ImageFrame then drives:
  - the STAC clip (single scene fully covering the frame),
  - the page-1 plate image (renders at the frame, fills the page exactly),
  - the diagnostic locator's AOI rectangle,
  - the diagnostic bbox / center / extent_km report.

:func:`fit_aspect_expand` is also exported for the future multi-scene
mosaic path; the default single-scene pipeline avoids it because expand
introduces nodata wherever the chosen scene doesn't cover the expanded
frame.

For now the renderer treats ``rotation_deg`` as a no-op (always 0). The
field exists so downstream code that consumes :class:`ImageFrame` is
ready for rotated framings when we add them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


_PAGE_SIZE_MM: dict[str, tuple[float, float]] = {
    "A4": (210.0, 297.0),
    "Letter": (215.9, 279.4),
}


def page_aspect(page_size: str, orientation: str) -> float:
    """width / height aspect of the printed page."""
    w, h = _PAGE_SIZE_MM[page_size]
    if orientation == "landscape":
        w, h = h, w
    return w / h


@dataclass(frozen=True)
class ImageFrame:
    """A North-up rectangular frame in geographic (lon, lat) coordinates.

    ``rotation_deg`` is reserved for future non-North-up framings; current
    consumers assume 0 and may ignore the field.
    """
    bbox: tuple[float, float, float, float]   # lon_min, lat_min, lon_max, lat_max
    rotation_deg: float = 0.0

    @property
    def lon_min(self) -> float: return self.bbox[0]
    @property
    def lat_min(self) -> float: return self.bbox[1]
    @property
    def lon_max(self) -> float: return self.bbox[2]
    @property
    def lat_max(self) -> float: return self.bbox[3]

    @property
    def center(self) -> tuple[float, float]:
        return ((self.lon_min + self.lon_max) / 2,
                (self.lat_min + self.lat_max) / 2)

    @property
    def corners_lonlat(self) -> list[tuple[float, float]]:
        """NW, NE, SE, SW — useful for plotting the frame as a polygon."""
        return [
            (self.lon_min, self.lat_max),
            (self.lon_max, self.lat_max),
            (self.lon_max, self.lat_min),
            (self.lon_min, self.lat_min),
        ]

    def width_km(self) -> float:
        lat_mid_rad = math.radians((self.lat_min + self.lat_max) / 2)
        return (self.lon_max - self.lon_min) * 111.32 * math.cos(lat_mid_rad)

    def height_km(self) -> float:
        return (self.lat_max - self.lat_min) * 111.32


def _ground_units(bbox: tuple[float, float, float, float]) -> tuple[float, float, float]:
    """Return ``(width_ground_units, height_ground_units, cos_lat)`` for ``bbox``.

    Longitude degrees foreshorten by ``cos(lat)``; we work in cos-corrected
    ground units so the on-page aspect ratio reflects what's actually drawn.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    lat_mid_deg = (lat_min + lat_max) / 2
    cos_lat = max(math.cos(math.radians(lat_mid_deg)), 1e-3)
    return (lon_max - lon_min) * cos_lat, (lat_max - lat_min), cos_lat


def fit_aspect_contract(
    user_bbox: tuple[float, float, float, float],
    target_aspect: float,
) -> ImageFrame:
    """Largest centered rectangle *inside* ``user_bbox`` matching ``target_aspect``.

    Center-crops along the long axis. The frame is fully contained in
    ``user_bbox`` so the displayed area is always covered by available
    imagery — no nodata strips on page 1.

    Trade-off: data on the long axis is dropped. To keep all user data
    we'd need :func:`fit_aspect_expand` plus multi-scene mosaicking to
    fill any coverage gaps; that's a separate, larger change.
    """
    width_g, height_g, cos_lat = _ground_units(user_bbox)
    if width_g <= 0 or height_g <= 0:
        return ImageFrame(bbox=user_bbox)

    lon_min, lat_min, lon_max, lat_max = user_bbox
    cx = (lon_min + lon_max) / 2
    cy = (lat_min + lat_max) / 2
    current = width_g / height_g

    if abs(current - target_aspect) < 1e-6:
        return ImageFrame(bbox=user_bbox)

    if current > target_aspect:
        # Too wide — shrink longitude.
        target_w_g = target_aspect * height_g
        half_lon = (target_w_g / cos_lat) / 2
        lon_min, lon_max = cx - half_lon, cx + half_lon
    else:
        # Too tall — shrink latitude.
        target_h_g = width_g / target_aspect
        half_lat = target_h_g / 2
        lat_min, lat_max = cy - half_lat, cy + half_lat

    return ImageFrame(bbox=(lon_min, lat_min, lon_max, lat_max))


def fit_aspect_expand(
    user_bbox: tuple[float, float, float, float],
    target_aspect: float,
) -> ImageFrame:
    """Smallest centered rectangle *containing* ``user_bbox`` matching ``target_aspect``.

    Reserved for the multi-scene mosaic path where downstream code fills
    any coverage gaps with imagery from neighbouring scenes. The current
    single-scene pipeline uses :func:`fit_aspect_contract` so we never
    produce nodata strips on page 1.
    """
    width_g, height_g, cos_lat = _ground_units(user_bbox)
    if width_g <= 0 or height_g <= 0:
        return ImageFrame(bbox=user_bbox)

    lon_min, lat_min, lon_max, lat_max = user_bbox
    current = width_g / height_g

    if abs(current - target_aspect) < 1e-6:
        return ImageFrame(bbox=user_bbox)

    if current < target_aspect:
        target_w_g = target_aspect * height_g
        delta_lon = (target_w_g - width_g) / cos_lat
        lon_min -= delta_lon / 2
        lon_max += delta_lon / 2
    else:
        target_h_g = width_g / target_aspect
        delta_lat = target_h_g - height_g
        lat_min -= delta_lat / 2
        lat_max += delta_lat / 2

    return ImageFrame(bbox=(lon_min, lat_min, lon_max, lat_max))


def derive_frame(
    user_bbox: tuple[float, float, float, float],
    *,
    page_size: str,
    orientation: str,
    rotation_deg: float = 0.0,
) -> ImageFrame:
    """Compute the on-page :class:`ImageFrame` for a user bbox + page layout.

    Default strategy: aspect-contract. The frame is the largest rectangle
    inside ``user_bbox`` that matches the page aspect, centered on the
    AOI. ``rotation_deg`` is stored but currently a no-op for the
    renderer; reserved for future non-North-up framings.
    """
    target = page_aspect(page_size, orientation)
    frame = fit_aspect_contract(user_bbox, target)
    if rotation_deg:
        frame = ImageFrame(bbox=frame.bbox, rotation_deg=rotation_deg)
    return frame


# ---------------------------------------------------------------------------
# Explicit frames (from the web plate picker)
# ---------------------------------------------------------------------------
#
# The picker positions a page-shaped rectangle directly: center, width across
# the page's horizontal axis, and the compass bearing of the page's "up".
# These helpers mirror ``web/src/lib/geo/{tangent,frame}.ts`` exactly so the
# corners the user saw on screen are the corners the renderer warps to.

from pydantic import BaseModel, Field  # noqa: E402


def meters_per_degree_lat(lat_deg: float) -> float:
    p = math.radians(lat_deg)
    return 111132.954 - 559.822 * math.cos(2 * p) + 1.175 * math.cos(4 * p)


def meters_per_degree_lon(lat_deg: float) -> float:
    p = math.radians(lat_deg)
    return 111412.84 * math.cos(p) - 93.5 * math.cos(3 * p) + 0.118 * math.cos(5 * p)


class FrameSpec(BaseModel):
    """A page frame placed explicitly on the ground.

    ``rotation_deg`` is the compass bearing of the page top (0 = north-up,
    90 = the top of the page faces east). ``height_m`` is carried for
    readability/validation but the plate aspect is fixed by the book spec.
    """

    center: tuple[float, float]  # lon, lat
    width_m: float = Field(gt=0)
    height_m: float = Field(gt=0)
    rotation_deg: float = 0.0

    def page_to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        """Page-local meters (x right, y up) → lon/lat."""
        t = math.radians(self.rotation_deg)
        east = x * math.cos(t) + y * math.sin(t)
        north = -x * math.sin(t) + y * math.cos(t)
        lon0, lat0 = self.center
        return (lon0 + east / meters_per_degree_lon(lat0), lat0 + north / meters_per_degree_lat(lat0))

    def corners_lonlat(self) -> list[tuple[float, float]]:
        """Top-left, top-right, bottom-right, bottom-left in page order."""
        hw, hh = self.width_m / 2, self.height_m / 2
        return [
            self.page_to_lonlat(-hw, hh),
            self.page_to_lonlat(hw, hh),
            self.page_to_lonlat(hw, -hh),
            self.page_to_lonlat(-hw, -hh),
        ]

    def envelope_bbox(self) -> tuple[float, float, float, float]:
        c = self.corners_lonlat()
        lons = [p[0] for p in c]
        lats = [p[1] for p in c]
        return (min(lons), min(lats), max(lons), max(lats))

    def padded_bbox(self, fraction: float = 0.02) -> tuple[float, float, float, float]:
        """Envelope grown by ``fraction`` on every side (fetch margin for warping)."""
        lo, la, hi, ha = self.envelope_bbox()
        dx, dy = (hi - lo) * fraction, (ha - la) * fraction
        return (lo - dx, la - dy, hi + dx, ha + dy)

    @property
    def aspect(self) -> float:
        return self.width_m / self.height_m


def frame_from_spec(spec: FrameSpec) -> ImageFrame:
    """The :class:`ImageFrame` view of an explicit frame (envelope + rotation)."""
    return ImageFrame(bbox=spec.envelope_bbox(), rotation_deg=spec.rotation_deg)
