import proj4 from 'proj4';
import type { LonLat } from './tangent';

/** PROJ string for a WGS84 UTM zone from its EPSG code (326xx north, 327xx south). */
export function utmProjFromEpsg(epsg: number): string {
	if (epsg >= 32601 && epsg <= 32660) return `+proj=utm +zone=${epsg - 32600} +datum=WGS84 +units=m +no_defs`;
	if (epsg >= 32701 && epsg <= 32760) return `+proj=utm +zone=${epsg - 32700} +south +datum=WGS84 +units=m +no_defs`;
	throw new Error(`EPSG:${epsg} is not a WGS84 UTM zone`);
}

export interface Projector {
	forward(p: LonLat): [number, number];
	inverse(xy: [number, number]): LonLat;
}

export function projectorForEpsg(epsg: number): Projector {
	const def = utmProjFromEpsg(epsg);
	const conv = proj4('EPSG:4326', def);
	return {
		forward: (p) => conv.forward([p[0], p[1]]) as [number, number],
		inverse: (xy) => conv.inverse([xy[0], xy[1]]) as LonLat
	};
}
