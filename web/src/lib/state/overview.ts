import { getIndex, platformForCollection, platformForIndex } from '$lib/data';
import { plateTheme } from '$lib/export/manifest';
import type { Plate } from './schema';

/**
 * The book at a glance: where its plates are, when their scenes were taken,
 * and which indices and colormaps they use (and how often). Pure, so the
 * Overview tab and the tests read the same numbers.
 */

export interface OverviewPoint {
	locationId: string;
	name: string;
	/** mean of the plates' frame centers */
	lon: number;
	lat: number;
	theme: string;
	plateIds: string[];
}

export interface OverviewMark {
	plateId: string;
	ordinal: number;
	name: string;
	/** scene date, YYYY-MM-DD */
	date: string;
	/** ms since epoch, for placing on an axis */
	t: number;
	theme: string;
}

export interface OverviewPair {
	key: string;
	index: string;
	colormap: string;
	reversed: boolean;
	plateIds: string[];
}

export interface BookOverview {
	counts: { plates: number; places: number; indices: number; colormaps: number; trueColor: number; noScene: number };
	/** platform label → plate count */
	platforms: Map<string, number>;
	/** first and last scene date, YYYY-MM-DD */
	span: [string, string] | null;
	byIndex: Map<string, string[]>;
	byColormap: Map<string, string[]>;
	byLocation: Map<string, string[]>;
	trueColorIds: string[];
	pairs: OverviewPair[];
	points: OverviewPoint[];
	marks: OverviewMark[];
	/** plates per calendar month of their scene, Jan … Dec */
	months: number[];
}

function push<K>(m: Map<K, string[]>, k: K, id: string) {
	const a = m.get(k);
	if (a) a.push(id);
	else m.set(k, [id]);
}

function platformLabel(p: Plate): string | null {
	if (p.scene) return platformForCollection(p.scene.collection)?.label ?? p.scene.collection;
	if (p.kind === 'truecolor') return null;
	const def = getIndex(p.index);
	return def ? platformForIndex(def).label : null;
}

export function pairKey(index: string, colormap: string, reversed: boolean): string {
	return `${index}|${colormap}|${reversed ? 'r' : ''}`;
}

export function bookOverview(plates: Plate[]): BookOverview {
	const byIndex = new Map<string, string[]>();
	const byColormap = new Map<string, string[]>();
	const byLocation = new Map<string, string[]>();
	const platforms = new Map<string, number>();
	const pairs = new Map<string, OverviewPair>();
	const pts = new Map<string, OverviewPoint & { sx: number; sy: number }>();
	const marks: OverviewMark[] = [];
	const months = Array.from({ length: 12 }, () => 0);
	const trueColorIds: string[] = [];
	let noScene = 0;

	for (const p of plates) {
		const theme = plateTheme(p);
		const loc = p.location.locationId;
		push(byLocation, loc, p.id);
		const pt = pts.get(loc);
		if (pt) {
			pt.sx += p.frame.center[0];
			pt.sy += p.frame.center[1];
			pt.plateIds.push(p.id);
		} else {
			pts.set(loc, { locationId: loc, name: p.location.name, lon: 0, lat: 0, theme, plateIds: [p.id], sx: p.frame.center[0], sy: p.frame.center[1] });
		}

		if (p.kind === 'truecolor') trueColorIds.push(p.id);
		else {
			push(byIndex, p.index, p.id);
			push(byColormap, p.colormap.name, p.id);
			const key = pairKey(p.index, p.colormap.name, p.colormap.reversed);
			const pair = pairs.get(key);
			if (pair) pair.plateIds.push(p.id);
			else pairs.set(key, { key, index: p.index, colormap: p.colormap.name, reversed: p.colormap.reversed, plateIds: [p.id] });
		}

		const pl = platformLabel(p);
		if (pl) platforms.set(pl, (platforms.get(pl) ?? 0) + 1);

		const date = p.scene?.datetime.slice(0, 10);
		const t = date ? Date.parse(`${date}T00:00:00Z`) : NaN;
		if (date && Number.isFinite(t)) {
			marks.push({ plateId: p.id, ordinal: p.ordinal, name: p.location.name, date, t, theme });
			months[Number(date.slice(5, 7)) - 1]++;
		} else noScene++;
	}

	marks.sort((a, b) => a.t - b.t || a.ordinal - b.ordinal);
	const points: OverviewPoint[] = [...pts.values()].map(({ sx, sy, ...pt }) => ({
		...pt,
		lon: sx / pt.plateIds.length,
		lat: sy / pt.plateIds.length
	}));

	return {
		counts: {
			plates: plates.length,
			places: byLocation.size,
			indices: byIndex.size,
			colormaps: byColormap.size,
			trueColor: trueColorIds.length,
			noScene
		},
		platforms,
		span: marks.length ? [marks[0].date, marks.at(-1)!.date] : null,
		byIndex,
		byColormap,
		byLocation,
		trueColorIds,
		pairs: [...pairs.values()].sort((a, b) => b.plateIds.length - a.plateIds.length || a.index.localeCompare(b.index)),
		points,
		marks,
		months
	};
}

/** What the Overview is focused on; every view dims what doesn't match. */
export type OverviewFocus =
	| { kind: 'location'; key: string }
	| { kind: 'index'; key: string }
	| { kind: 'colormap'; key: string }
	| { kind: 'pair'; key: string }
	| { kind: 'truecolor'; key: 'truecolor' }
	| { kind: 'plate'; key: string };

/** The plate ids a focus selects (empty for an unused index or colormap). */
export function focusPlateIds(o: BookOverview, f: OverviewFocus | null): string[] {
	if (!f) return [];
	switch (f.kind) {
		case 'location':
			return o.byLocation.get(f.key) ?? [];
		case 'index':
			return o.byIndex.get(f.key) ?? [];
		case 'colormap':
			return o.byColormap.get(f.key) ?? [];
		case 'pair':
			return o.pairs.find((p) => p.key === f.key)?.plateIds ?? [];
		case 'truecolor':
			return o.trueColorIds;
		case 'plate':
			return [f.key];
	}
}

/**
 * Stack timeline marks into rows so none overlaps the one before it in its
 * row; `x` is 0…1 along the axis, `minGap` the same unit.
 */
export function stackMarks<T extends { x: number }>(marks: T[], minGap: number, maxRows = 4): (T & { row: number })[] {
	const ends: number[] = [];
	return marks.map((m) => {
		let row = ends.findIndex((e) => m.x - e >= minGap);
		if (row < 0) row = ends.length < maxRows ? ends.length : ends.indexOf(Math.min(...ends));
		ends[row] = m.x;
		return { ...m, row };
	});
}
