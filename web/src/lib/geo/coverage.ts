import type { Polygon, MultiPolygon } from 'geojson';
import { intersect } from '@turf/intersect';
import { area } from '@turf/area';
import { featureCollection, feature } from '@turf/helpers';

/** Fraction (0–1) of `frame` covered by `scene` geometry (geodesic areas). */
export function coverageFraction(frame: Polygon, scene: Polygon | MultiPolygon): number {
	const frameArea = area(frame);
	if (frameArea <= 0) return 0;
	const inter = intersect(featureCollection([feature(frame), feature(scene)]));
	if (!inter) return 0;
	return Math.min(1, area(inter) / frameArea);
}
