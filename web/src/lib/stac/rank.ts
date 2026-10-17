import type { Polygon } from 'geojson';
import { coverageFraction } from '$lib/geo/coverage';
import { itemCloudCover } from './items';
import type { StacItem } from './types';

export interface RankedScene {
	item: StacItem;
	/** fraction of the page frame the scene footprint covers, 0–1 */
	coverage: number;
}

/** Full coverage counts as ≥ 98 %: tile edges rarely line up exactly with the frame. */
export const FULL_COVERAGE = 0.98;

/**
 * Order search results the way the editor reads them: scenes that cover the
 * whole frame first, then the least cloud. Items from other collections are
 * dropped (a stale result set can outlive an index change). Radar items have no
 * cloud property; they keep their search order among themselves.
 */
export function rankScenes(items: StacItem[], collection: string, frame: Polygon): RankedScene[] {
	const cloud = (item: StacItem) => {
		const c = itemCloudCover(item);
		return Number.isFinite(c) ? c : 0;
	};
	return items
		.filter((item) => item.collection === collection)
		.map((item) => ({ item, coverage: coverageFraction(frame, item.geometry) }))
		.sort((a, b) => (b.coverage >= FULL_COVERAGE ? 1 : 0) - (a.coverage >= FULL_COVERAGE ? 1 : 0) || cloud(a.item) - cloud(b.item));
}
