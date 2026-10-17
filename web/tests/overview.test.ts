import { describe, expect, it } from 'vitest';
import { PlateSchema, type Plate, type Scene } from '../src/lib/state/schema';
import { bookOverview, focusPlateIds, pairKey, stackMarks } from '../src/lib/state/overview';

const now = '2026-09-01T00:00:00.000Z';

function scene(datetime: string): Scene {
	return {
		collection: 'sentinel-2-l2a',
		itemId: `S2_${datetime}`,
		datetime,
		cloudCover: 1,
		mgrs: '33SVB',
		epsg: 32633,
		platform: 'sentinel-2a',
		geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
		assetHrefs: {},
		thumbnail: null
	};
}

function plate(id: string, ordinal: number, extra: Partial<Plate> = {}): Plate {
	return PlateSchema.parse({
		id,
		ordinal,
		location: { kind: 'pool', locationId: 'mt_etna', name: 'Mount Etna' },
		frame: { center: [15, 37.75], widthM: 17500, rotationDeg: 0 },
		scene: null,
		index: 'NDVI',
		colormap: { name: 'cmasher:eclipse', reversed: false },
		normalization: { method: 'percentile', lower: 2, upper: 98 },
		createdAt: now,
		updatedAt: now,
		...extra
	});
}

const plates = [
	plate('a', 1, { scene: scene('2024-07-10T09:50:00Z') }),
	plate('b', 2, { scene: scene('2021-07-02T09:50:00Z'), frame: { center: [15.2, 37.95], widthM: 17500, rotationDeg: 0 } }),
	plate('c', 3, {
		location: { kind: 'adhoc', locationId: 'phoenix', name: 'Phoenix', landscapeCharacter: '' },
		frame: { center: [-112, 33.4], widthM: 30000, rotationDeg: 0 },
		index: 'NDWI',
		colormap: { name: 'cmasher:eclipse', reversed: true },
		scene: scene('2023-01-15T18:00:00Z')
	}),
	plate('d', 4, { kind: 'truecolor', location: { kind: 'adhoc', locationId: 'phoenix', name: 'Phoenix', landscapeCharacter: '' } })
];

describe('bookOverview', () => {
	const o = bookOverview(plates);

	it('counts places, indices and colormaps across plates', () => {
		expect(o.counts).toEqual({ plates: 4, places: 2, indices: 2, colormaps: 1, trueColor: 1, noScene: 1 });
		expect(o.byIndex.get('NDVI')).toEqual(['a', 'b']);
		expect(o.byColormap.get('cmasher:eclipse')).toEqual(['a', 'b', 'c']);
		expect(o.trueColorIds).toEqual(['d']);
	});

	it('keeps reversed and plain uses of a ramp as separate pairs, most used first', () => {
		expect(o.pairs.map((p) => [p.key, p.plateIds.length])).toEqual([
			[pairKey('NDVI', 'cmasher:eclipse', false), 2],
			[pairKey('NDWI', 'cmasher:eclipse', true), 1]
		]);
	});

	it('places one point per location at the mean frame center', () => {
		const etna = o.points.find((p) => p.locationId === 'mt_etna')!;
		expect(etna.plateIds).toEqual(['a', 'b']);
		expect(etna.lon).toBeCloseTo(15.1);
		expect(etna.lat).toBeCloseTo(37.85);
	});

	it('orders timeline marks by date and bins months', () => {
		expect(o.marks.map((m) => m.plateId)).toEqual(['b', 'c', 'a']);
		expect(o.span).toEqual(['2021-07-02', '2024-07-10']);
		expect(o.months[0]).toBe(1);
		expect(o.months[6]).toBe(2);
		expect(o.months.reduce((a, b) => a + b, 0)).toBe(3);
	});

	it('resolves a focus to plate ids', () => {
		expect(focusPlateIds(o, { kind: 'location', key: 'phoenix' })).toEqual(['c', 'd']);
		expect(focusPlateIds(o, { kind: 'colormap', key: 'cmasher:eclipse' })).toEqual(['a', 'b', 'c']);
		expect(focusPlateIds(o, { kind: 'index', key: 'EVI' })).toEqual([]);
		expect(focusPlateIds(o, null)).toEqual([]);
	});

	it('is empty for an empty book', () => {
		const e = bookOverview([]);
		expect(e.span).toBeNull();
		expect(e.points).toEqual([]);
		expect(e.counts.plates).toBe(0);
	});
});

describe('stackMarks', () => {
	it('moves a crowded mark to the next free row', () => {
		const rows = stackMarks([{ x: 0 }, { x: 0.01 }, { x: 0.5 }, { x: 0.505 }], 0.03).map((m) => m.row);
		expect(rows).toEqual([0, 1, 0, 1]);
	});
	it('reuses the emptiest row once all rows are taken', () => {
		const rows = stackMarks([{ x: 0 }, { x: 0.001 }, { x: 0.002 }], 0.03, 2).map((m) => m.row);
		expect(rows).toEqual([0, 1, 0]);
	});
});
