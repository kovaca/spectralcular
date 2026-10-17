import type { PlatformDef } from '$lib/data/types';
import type { SceneSearchParams, StacItem, StacSearchResponse } from './types';

export const EARTH_SEARCH_URL = 'https://earth-search.aws.element84.com/v1';
export const DEFAULT_COLLECTION = 'sentinel-2-l2a';

/**
 * Minimal STAC API client — plain fetch. Works against Earth Search v1 and
 * Planetary Computer's STAC API (both CORS-open; PC asset hrefs need signing
 * separately, see `sas.ts`).
 */
export class StacClient {
	constructor(
		private readonly baseUrl: string = EARTH_SEARCH_URL,
		private readonly defaultCollection: string = DEFAULT_COLLECTION
	) {}

	async search(params: SceneSearchParams, signal?: AbortSignal): Promise<StacItem[]> {
		const body: Record<string, unknown> = {
			collections: [params.collection ?? this.defaultCollection],
			limit: params.limit ?? 50,
			// Optical collections sort clearest-first; radar (no cloud property) newest-first.
			sortby:
				params.cloudCoverMax !== undefined
					? [{ field: 'properties.eo:cloud_cover', direction: 'asc' }]
					: [{ field: 'properties.datetime', direction: 'desc' }]
		};
		if (params.intersects) body.intersects = params.intersects;
		else if (params.bbox) body.bbox = params.bbox;
		if (params.datetime) body.datetime = toRfc3339Interval(params.datetime);
		const query: Record<string, unknown> = {};
		if (params.cloudCoverMax !== undefined) query['eo:cloud_cover'] = { lt: params.cloudCoverMax };
		if (params.query) Object.assign(query, params.query);
		if (Object.keys(query).length) body.query = query;
		const res = await fetch(`${this.baseUrl}/search`, {
			method: 'POST',
			headers: { 'content-type': 'application/json', accept: 'application/geo+json' },
			body: JSON.stringify(body),
			signal
		});
		if (!res.ok) throw new Error(`STAC search failed: ${res.status} ${res.statusText}`);
		const json = (await res.json()) as StacSearchResponse;
		return json.features;
	}

	async getItem(id: string, collection = this.defaultCollection, signal?: AbortSignal): Promise<StacItem> {
		const res = await fetch(`${this.baseUrl}/collections/${collection}/items/${encodeURIComponent(id)}`, {
			headers: { accept: 'application/geo+json' },
			signal
		});
		if (!res.ok) throw new Error(`STAC item ${id} not found: ${res.status}`);
		return (await res.json()) as StacItem;
	}
}

/** "2023-07-01/2023-08-31" → "2023-07-01T00:00:00Z/2023-08-31T23:59:59Z" (Earth Search wants full timestamps). */
export function toRfc3339Interval(interval: string): string {
	const [a, b] = interval.split('/');
	const start = a && !a.includes('T') ? `${a}T00:00:00Z` : a;
	if (b === undefined) return start;
	const end = b && !b.includes('T') ? `${b}T23:59:59Z` : b;
	return `${start}/${end}`;
}

const clients = new Map<string, StacClient>();

/** One client per platform (keyed by STAC API + collection). */
export function clientFor(platform: PlatformDef): StacClient {
	const key = `${platform.stacApi}|${platform.collection}`;
	let c = clients.get(key);
	if (!c) {
		c = new StacClient(platform.stacApi, platform.collection);
		clients.set(key, c);
	}
	return c;
}

/** Sentinel-2 on Earth Search: the historical default. */
export const stac = new StacClient();
