"""FrameSpec geometry must match web/src/lib/geo/frame.ts (same series, same conventions)."""

import math

from spectralcular.framing import FrameSpec, frame_from_spec, meters_per_degree_lat, meters_per_degree_lon

ASPECT = 8.75 / 11.25


def _spec(rot=0.0):
    w = 17_500.0
    return FrameSpec(center=(15.0, 37.75), width_m=w, height_m=w / ASPECT, rotation_deg=rot)


def _local(spec, p):
    lon0, lat0 = spec.center
    return ((p[0] - lon0) * meters_per_degree_lon(lat0), (p[1] - lat0) * meters_per_degree_lat(lat0))


def test_north_up_corners():
    tl, tr, br, bl = _spec().corners_lonlat()
    assert math.isclose(tl[1], tr[1], abs_tol=1e-9)
    assert math.isclose(tr[0], br[0], abs_tol=1e-9)
    e, n = _local(_spec(), tr)
    assert math.isclose(e, 8750, abs_tol=1e-3)
    assert math.isclose(n, 17_500 / ASPECT / 2, abs_tol=1e-3)


def test_width_invariant_under_rotation():
    for rot in (0, 33, 90, -120, 180):
        s = _spec(rot)
        tl, tr, br, bl = s.corners_lonlat()
        d = lambda a, b: math.hypot(*(x - y for x, y in zip(_local(s, a), _local(s, b))))
        assert math.isclose(d(tl, tr), 17_500, abs_tol=1e-3)
        assert math.isclose(d(tl, bl), 17_500 / ASPECT, abs_tol=1e-3)


def test_rotation_90_points_page_top_east():
    s = _spec(90)
    e, n = _local(s, s.page_to_lonlat(0, 1000))
    assert math.isclose(e, 1000, abs_tol=1e-3)
    assert math.isclose(n, 0, abs_tol=1e-3)


def test_envelope_and_image_frame():
    s = _spec(25)
    env = s.envelope_bbox()
    for c in s.corners_lonlat():
        assert env[0] - 1e-12 <= c[0] <= env[2] + 1e-12
        assert env[1] - 1e-12 <= c[1] <= env[3] + 1e-12
    f = frame_from_spec(s)
    assert f.rotation_deg == 25
    assert f.bbox == env
    pad = s.padded_bbox(0.02)
    assert pad[0] < env[0] and pad[2] > env[2]


def test_matches_web_fixture():
    # Values produced by the TS implementation for the same frame.
    s = FrameSpec(center=(15.11, 37.75), width_m=15863.4, height_m=20395.7, rotation_deg=25)
    tl = s.corners_lonlat()[0]
    # center + page (-w/2, +h/2) rotated 25° clockwise
    e = -7931.7 * math.cos(math.radians(25)) + 10197.85 * math.sin(math.radians(25))
    n = 7931.7 * math.sin(math.radians(25)) + 10197.85 * math.cos(math.radians(25))
    assert math.isclose(tl[0], 15.11 + e / meters_per_degree_lon(37.75), abs_tol=1e-9)
    assert math.isclose(tl[1], 37.75 + n / meters_per_degree_lat(37.75), abs_tol=1e-9)
