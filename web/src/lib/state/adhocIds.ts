import { locations } from '$lib/data';
import { slugify, type Plate, type Project } from './schema';

/**
 * Ad-hoc location ids. The picker names an ad-hoc place from free text
 * ("New location", a geocoder hit, a typed name), but `spectralcular import` keys
 * `config/locations.yaml` by id and silently reuses an entry that already
 * exists. So two different places must never export the same id, and an
 * ad-hoc place must not borrow a pool id.
 *
 * Plates are grouped into places by name slug + proximity (centers within
 * the larger frame width), earliest-created first, so ids are derived and
 * stable: re-running this on its own output changes nothing.
 */

/** The id the default "New location" draft starts with. */
export const PLACEHOLDER_ID = 'new_location';

interface Place {
	root: string;
	center: [number, number];
	widthM: number;
	id: string;
}

function distanceM([lon1, lat1]: [number, number], [lon2, lat2]: [number, number]): number {
	const r = Math.PI / 180;
	const a =
		Math.sin(((lat2 - lat1) * r) / 2) ** 2 +
		Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(((lon2 - lon1) * r) / 2) ** 2;
	return 2 * 6_371_008.8 * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** `44_58n_123_23w` — a ~1 km tag that keeps an id readable and unique. */
export function coordTag([lon, lat]: [number, number]): string {
	const part = (v: number, pos: string, neg: string) => `${Math.abs(v).toFixed(2).replace('.', '_')}${v < 0 ? neg : pos}`;
	return `${part(lat, 'n', 's')}_${part(lon, 'e', 'w')}`;
}

/** Rewrite ad-hoc `locationId`s so each distinct place has its own id. */
export function assignAdhocIds(plates: Plate[], reserved: ReadonlySet<string>): Plate[] {
	const order = plates
		.map((p, i) => ({ p, i }))
		.filter(({ p }) => p.location.kind === 'adhoc')
		.sort((a, b) => a.p.createdAt.localeCompare(b.p.createdAt) || a.p.id.localeCompare(b.p.id));
	const places: Place[] = [];
	const taken = new Set(reserved);
	const ids = new Map<number, string>();

	for (const { p, i } of order) {
		const root = slugify(p.location.name);
		const center = p.frame.center as [number, number];
		const same = places.find(
			(pl) => pl.root === root && distanceM(pl.center, center) <= Math.max(pl.widthM, p.frame.widthM)
		);
		if (same) {
			ids.set(i, same.id);
			continue;
		}
		let id = root === PLACEHOLDER_ID || taken.has(root) ? `${root}_${coordTag(center)}` : root;
		for (let n = 2; taken.has(id); n++) id = `${root}_${coordTag(center)}_${n}`;
		taken.add(id);
		places.push({ root, center, widthM: p.frame.widthM, id });
		ids.set(i, id);
	}

	let changed = false;
	const out = plates.map((p, i) => {
		const id = ids.get(i);
		if (id === undefined || id === p.location.locationId) return p;
		changed = true;
		return { ...p, location: { ...p.location, locationId: id } };
	});
	return changed ? out : plates;
}

let poolIds: Set<string> | null = null;

/** {@link assignAdhocIds} against the location pool; returns `p` when nothing changes. */
export function normalizeAdhocIds(p: Project): Project {
	poolIds ??= new Set(locations.map((l) => l.id));
	const plates = assignAdhocIds(p.plates, poolIds);
	return plates === p.plates ? p : { ...p, plates };
}
