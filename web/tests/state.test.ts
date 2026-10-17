import { describe, expect, it } from 'vitest';
import { emptyProject, PlateSchema, candidateSlug, slugify, type Plate } from '../src/lib/state/schema';
import * as R from '../src/lib/state/reducers';
import { migrate } from '../src/lib/state/migrations';
import { loadProject, saveProject, STORAGE_KEY } from '../src/lib/state/persistence';

function plate(id: string, extra: Partial<Plate> = {}): Plate {
	const now = '2026-09-01T00:00:00.000Z';
	return PlateSchema.parse({
		id,
		ordinal: 0,
		location: { kind: 'pool', locationId: 'mt_etna', name: 'Mount Etna' },
		frame: { center: [15, 37.75], widthM: 17500, rotationDeg: 0 },
		scene: null,
		index: 'NDVI',
		colormap: { name: 'bids:viridis', reversed: false },
		normalization: { method: 'percentile', lower: 1, upper: 99 },
		createdAt: now,
		updatedAt: now,
		...extra
	});
}

class MemStorage implements Storage {
	private m = new Map<string, string>();
	get length() {
		return this.m.size;
	}
	clear() {
		this.m.clear();
	}
	getItem(k: string) {
		return this.m.get(k) ?? null;
	}
	key(i: number) {
		return [...this.m.keys()][i] ?? null;
	}
	removeItem(k: string) {
		this.m.delete(k);
	}
	setItem(k: string, v: string) {
		this.m.set(k, v);
	}
}

describe('reducers', () => {
	it('adds and renumbers', () => {
		let p = emptyProject();
		p = R.addPlate(p, plate('a'));
		p = R.addPlate(p, plate('b'));
		expect(p.plates.map((x) => x.ordinal)).toEqual([1, 2]);
	});
	it('moves and renumbers', () => {
		let p = emptyProject();
		for (const id of ['a', 'b', 'c']) p = R.addPlate(p, plate(id));
		p = R.movePlate(p, 2, 0);
		expect(p.plates.map((x) => x.id)).toEqual(['c', 'a', 'b']);
		expect(p.plates.map((x) => x.ordinal)).toEqual([1, 2, 3]);
	});
	it('removes, duplicates, updates', () => {
		let p = emptyProject();
		for (const id of ['a', 'b']) p = R.addPlate(p, plate(id));
		p = R.duplicatePlate(p, 'a', 'a2');
		expect(p.plates.map((x) => x.id)).toEqual(['a', 'a2', 'b']);
		p = R.removePlate(p, 'a');
		expect(p.plates.map((x) => x.ordinal)).toEqual([1, 2]);
		p = R.updatePlate(p, 'b', { caption: 'hi', id: 'nope' as string });
		expect(p.plates.find((x) => x.id === 'b')!.caption).toBe('hi');
	});
});

describe('persistence + migrations', () => {
	it('round-trips through storage', () => {
		const s = new MemStorage();
		let p = emptyProject('Test');
		p = R.addPlate(p, plate('a'));
		saveProject(p, s);
		const res = loadProject(s);
		expect(res.error).toBeUndefined();
		expect(res.project.name).toBe('Test');
		expect(res.project.plates).toHaveLength(1);
	});
	it('quarantines corrupt data instead of losing it', () => {
		const s = new MemStorage();
		s.setItem(STORAGE_KEY, '{"schemaVersion":1,"plates":"nope"}');
		const res = loadProject(s);
		expect(res.error).toMatch(/invalid project/);
		expect(res.quarantinedKey).toMatch(/^gb:project:corrupt:/);
		expect(s.getItem(res.quarantinedKey!)).toContain('nope');
		expect(res.project.plates).toEqual([]);
	});
	it('rejects newer schemas and missing versions', () => {
		expect(() => migrate({ schemaVersion: 99 })).toThrow(/newer/);
		expect(() => migrate({})).toThrow(/schemaVersion/);
	});
});

describe('slugs', () => {
	it('matches candidate_slug', () => {
		expect(candidateSlug('mt_etna', 'NDVI', 'bids:viridis')).toBe('mt_etna__NDVI__bids_viridis');
	});
	it('slugifies names like the pool ids', () => {
		expect(slugify('Amazon Basin (Rio Negro)')).toBe('amazon_basin_rio_negro');
		expect(slugify('Étna, 2023')).toBe('etna_2023');
	});
});
