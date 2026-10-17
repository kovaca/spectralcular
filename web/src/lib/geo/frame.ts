import type { Polygon } from 'geojson';
import { bboxSizeMeters, localToLonLat, lonLatToLocal, type BBox, type LonLat } from './tangent';

/**
 * The page frame on the ground: a rectangle with the plate's aspect ratio,
 * centered at `center`, `widthM` meters across the page's horizontal axis,
 * rotated so that the page's "up" points at compass bearing `rotationDeg`
 * (0 = north-up, 90 = page top faces east).
 */
export interface FrameModel {
	center: LonLat;
	widthM: number;
	rotationDeg: number;
}

const DEG = Math.PI / 180;

export function frameHeightM(f: FrameModel, aspect: number): number {
	return f.widthM / aspect;
}

/** Page-local (x right, y up, meters) → east/north meters. */
export function pageToLocal(f: FrameModel, x: number, y: number): [number, number] {
	const t = f.rotationDeg * DEG;
	return [x * Math.cos(t) + y * Math.sin(t), -x * Math.sin(t) + y * Math.cos(t)];
}

/** east/north meters → page-local (x right, y up). */
export function localToPage(f: FrameModel, east: number, north: number): [number, number] {
	const t = f.rotationDeg * DEG;
	return [east * Math.cos(t) - north * Math.sin(t), east * Math.sin(t) + north * Math.cos(t)];
}

export function pageToLonLat(f: FrameModel, x: number, y: number): LonLat {
	const [e, n] = pageToLocal(f, x, y);
	return localToLonLat(f.center, e, n);
}

export function lonLatToPage(f: FrameModel, p: LonLat): [number, number] {
	const [e, n] = lonLatToLocal(f.center, p);
	return localToPage(f, e, n);
}

/** Corners in page order: top-left, top-right, bottom-right, bottom-left. */
export function frameCorners(f: FrameModel, aspect: number): [LonLat, LonLat, LonLat, LonLat] {
	const hw = f.widthM / 2;
	const hh = frameHeightM(f, aspect) / 2;
	return [pageToLonLat(f, -hw, hh), pageToLonLat(f, hw, hh), pageToLonLat(f, hw, -hh), pageToLonLat(f, -hw, -hh)];
}

/** Axis-aligned lon/lat envelope of the (possibly rotated) frame. */
export function frameEnvelope(f: FrameModel, aspect: number): BBox {
	const c = frameCorners(f, aspect);
	const lons = c.map((p) => p[0]);
	const lats = c.map((p) => p[1]);
	return [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];
}

export function framePolygon(f: FrameModel, aspect: number): Polygon {
	const c = frameCorners(f, aspect);
	return { type: 'Polygon', coordinates: [[...c, c[0]]] };
}

export function frameContains(f: FrameModel, aspect: number, p: LonLat): boolean {
	const [x, y] = lonLatToPage(f, p);
	return Math.abs(x) <= f.widthM / 2 && Math.abs(y) <= frameHeightM(f, aspect) / 2;
}

/** A rectangle inset from the frame edges (used for trim / safety / gutter guides). */
export function frameInset(f: FrameModel, aspect: number, insetM: { left: number; right: number; top: number; bottom: number }): LonLat[] {
	const hw = f.widthM / 2;
	const hh = frameHeightM(f, aspect) / 2;
	return [
		pageToLonLat(f, -hw + insetM.left, hh - insetM.top),
		pageToLonLat(f, hw - insetM.right, hh - insetM.top),
		pageToLonLat(f, hw - insetM.right, -hh + insetM.bottom),
		pageToLonLat(f, -hw + insetM.left, -hh + insetM.bottom)
	];
}

/**
 * Largest north-up frame with the page aspect that fits *inside* a bbox,
 * centered on it (port of framing.fit_aspect_contract).
 */
export function frameFromBbox(b: BBox, aspect: number): FrameModel {
	const { widthM, heightM } = bboxSizeMeters(b);
	const center: LonLat = [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
	const fitW = Math.min(widthM, heightM * aspect);
	return { center, widthM: fitW, rotationDeg: 0 };
}

/** Normalize rotation to (-180, 180]. */
export function normalizeRotation(deg: number): number {
	let d = ((deg + 180) % 360 + 360) % 360 - 180;
	if (d === -180) d = 180;
	return d;
}

/** A stable hash of the frame for cache keys. */
export function frameKey(f: FrameModel): string {
	return `${f.center[0].toFixed(6)},${f.center[1].toFixed(6)},${f.widthM.toFixed(1)},${f.rotationDeg.toFixed(2)}`;
}
