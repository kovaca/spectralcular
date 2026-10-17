import type { LonLat } from './tangent';

/**
 * One coordinate as typed: decimal (`37.75`, `-15.2`) or degrees/minutes/seconds
 * (`37°45'30"`, `37 45 30`), with an optional hemisphere letter on either side.
 */
const NUM = String.raw`(\d+(?:\.\d+)?)`;
const PART = String.raw`([NSEW])?\s*(-?\d+(?:\.\d+)?)(?:(?:\s*[°º:]\s*|\s+)${NUM})?(?:(?:\s*['′:]\s*|\s+)${NUM})?\s*[°º'′"″]?\s*([NSEW])?`;
const PAIR = new RegExp(String.raw`^\s*${PART}\s*[,;/\s]\s*${PART}\s*$`, 'i');

interface Part {
	value: number;
	hemi: 'N' | 'S' | 'E' | 'W' | null;
}

function part(m: RegExpMatchArray, o: number): Part | null {
	const [pre, deg, min, sec, post] = [m[o], m[o + 1], m[o + 2], m[o + 3], m[o + 4]];
	if (pre && post) return null;
	const d = Number(deg);
	const mm = min ? Number(min) : 0;
	const ss = sec ? Number(sec) : 0;
	if (mm >= 60 || ss >= 60 || ((min || sec) && !Number.isInteger(d))) return null;
	let value = Math.abs(d) + mm / 60 + ss / 3600;
	if (deg.startsWith('-')) value = -value;
	const hemi = ((pre ?? post)?.toUpperCase() ?? null) as Part['hemi'];
	if (hemi === 'S' || hemi === 'W') {
		if (value < 0) return null;
		value = -value;
	}
	return { value, hemi };
}

const isLat = (p: Part) => p.hemi === 'N' || p.hemi === 'S';
const isLon = (p: Part) => p.hemi === 'E' || p.hemi === 'W';

/**
 * Parse a typed coordinate pair into [lon, lat], or null if the text isn't one.
 * Unlabeled pairs read latitude first (the map-app convention) unless the first
 * value can only be a longitude; hemisphere letters settle the order explicitly.
 */
export function parseLatLon(text: string): LonLat | null {
	const m = text.trim().match(PAIR);
	if (!m) return null;
	const a = part(m, 1);
	const b = part(m, 6);
	if (!a || !b) return null;
	let lat: number;
	let lon: number;
	if (isLat(a) || isLon(b)) {
		if (isLon(a) || isLat(b)) return null;
		[lat, lon] = [a.value, b.value];
	} else if (isLon(a) || isLat(b)) {
		[lon, lat] = [a.value, b.value];
	} else if (Math.abs(a.value) > 90 && Math.abs(b.value) <= 90) {
		[lon, lat] = [a.value, b.value];
	} else {
		[lat, lon] = [a.value, b.value];
	}
	if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
	return [lon, lat];
}

/** `37.7510° N, 14.9934° E` */
export function formatLatLon([lon, lat]: LonLat, digits = 4): string {
	const ns = lat < 0 ? 'S' : 'N';
	const ew = lon < 0 ? 'W' : 'E';
	return `${Math.abs(lat).toFixed(digits)}° ${ns}, ${Math.abs(lon).toFixed(digits)}° ${ew}`;
}
