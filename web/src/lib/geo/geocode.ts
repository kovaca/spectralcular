import type { LonLat } from './tangent';

/** A place-name search result. */
export interface GeocodeHit {
	id: string;
	name: string;
	/** region and country, for telling same-named places apart */
	detail: string;
	/** OSM feature kind, e.g. "volcano", "city", "nature reserve" */
	kind: string;
	center: LonLat;
}

/**
 * Photon (komoot's OpenStreetMap geocoder) is built for search-as-you-type,
 * needs no key and sends CORS headers, so a static site can call it directly.
 */
const PHOTON = 'https://photon.komoot.io/api/';

/**
 * Plates frame landscapes, not addresses: keep settlements, regions and
 * natural features ("other" holds volcanoes, lakes, reserves) and drop the
 * cafés, shops and hotels that share their names.
 */
const LAYERS = ['city', 'district', 'locality', 'county', 'state', 'country', 'other'];
const EXCLUDE_TAGS = ['amenity', 'shop', 'tourism', 'office', 'craft', 'leisure:playground'];

interface PhotonFeature {
	geometry: { coordinates: [number, number] };
	properties: {
		osm_type?: string;
		osm_id?: number;
		osm_value?: string;
		name?: string;
		city?: string;
		county?: string;
		state?: string;
		country?: string;
	};
}

export function hitFromPhoton(f: PhotonFeature): GeocodeHit | null {
	const p = f.properties;
	const [lon, lat] = f.geometry.coordinates;
	const name = p.name ?? p.city ?? p.county ?? p.state ?? p.country;
	if (!name || !Number.isFinite(lon) || !Number.isFinite(lat)) return null;
	const detail = [p.city, p.county, p.state, p.country].filter((s, i, a) => s && s !== name && a.indexOf(s) === i).slice(-2).join(', ');
	return {
		id: `${p.osm_type ?? '?'}${p.osm_id ?? `${lon},${lat}`}`,
		name,
		detail,
		kind: (p.osm_value ?? '').replaceAll('_', ' '),
		center: [lon, lat]
	};
}

/** Answers already fetched this session, so backspacing over a query is instant. */
const cache = new Map<string, GeocodeHit[]>();

/** Look up places by name. Throws on network failure; abort with `signal`. */
export async function searchPlaces(query: string, signal?: AbortSignal, limit = 6): Promise<GeocodeHit[]> {
	const key = `${limit}|${query.trim().toLowerCase()}`;
	const cached = cache.get(key);
	if (cached) return cached;
	const params = new URLSearchParams({ q: query, limit: String(limit), lang: 'en' });
	for (const l of LAYERS) params.append('layer', l);
	for (const t of EXCLUDE_TAGS) params.append('osm_tag', `!${t}`);
	const url = `${PHOTON}?${params}`;
	const res = await fetch(url, { signal });
	if (!res.ok) throw new Error(`Place search failed (${res.status})`);
	const body = (await res.json()) as { features?: PhotonFeature[] };
	const hits = (body.features ?? []).map(hitFromPhoton).filter((h): h is GeocodeHit => h !== null);
	// OSM often holds a node and a relation for the same place; keep the first.
	const seen = new Set<string>();
	const unique = hits.filter((h) => {
		const k = `${h.name}|${h.detail}`;
		if (seen.has(k)) return false;
		seen.add(k);
		return true;
	});
	cache.set(key, unique);
	return unique;
}
