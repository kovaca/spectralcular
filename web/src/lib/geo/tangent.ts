/**
 * Local tangent-plane helpers. A book plate is ≤ ~35 km across, so a flat
 * east/north meter frame anchored at the frame center is accurate to well
 * under a pixel; the Python side (`framing.frame_from_spec`) uses the same
 * series so both agree on where the page corners land.
 */
export type LonLat = [number, number];

const DEG = Math.PI / 180;

export function metersPerDegreeLat(latDeg: number): number {
	const p = latDeg * DEG;
	return 111132.954 - 559.822 * Math.cos(2 * p) + 1.175 * Math.cos(4 * p);
}

export function metersPerDegreeLon(latDeg: number): number {
	const p = latDeg * DEG;
	return 111412.84 * Math.cos(p) - 93.5 * Math.cos(3 * p) + 0.118 * Math.cos(5 * p);
}

/** East/north meters (relative to `origin`) → lon/lat. */
export function localToLonLat(origin: LonLat, east: number, north: number): LonLat {
	const [lon0, lat0] = origin;
	return [lon0 + east / metersPerDegreeLon(lat0), lat0 + north / metersPerDegreeLat(lat0)];
}

/** lon/lat → east/north meters relative to `origin`. */
export function lonLatToLocal(origin: LonLat, p: LonLat): [number, number] {
	const [lon0, lat0] = origin;
	return [(p[0] - lon0) * metersPerDegreeLon(lat0), (p[1] - lat0) * metersPerDegreeLat(lat0)];
}

export type BBox = [number, number, number, number]; // lon_min, lat_min, lon_max, lat_max

/** Ground size of a lon/lat bbox in meters at its mid-latitude. */
export function bboxSizeMeters(b: BBox): { widthM: number; heightM: number } {
	const latMid = (b[1] + b[3]) / 2;
	return {
		widthM: (b[2] - b[0]) * metersPerDegreeLon(latMid),
		heightM: (b[3] - b[1]) * metersPerDegreeLat(latMid)
	};
}
