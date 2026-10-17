import { describe, expect, it } from 'vitest';
import { assignAdhocIds, coordTag, normalizeAdhocIds } from '../src/lib/state/adhocIds';
import { migrate } from '../src/lib/state/migrations';
import { emptyProject, PlateSchema, type Plate } from '../src/lib/state/schema';

function adhoc(id: string, name: string, center: [number, number], createdAt: string, widthM = 26_250): Plate {
	return PlateSchema.parse({
		id,
		ordinal: 0,
		location: { kind: 'adhoc', locationId: 'new_location', name, landscapeCharacter: '' },
		frame: { center, widthM, rotationDeg: 0 },
		scene: null,
		index: 'NDVI',
		colormap: { name: 'bids:viridis', reversed: false },
		normalization: { method: 'percentile', lower: 1, upper: 99 },
		createdAt,
		updatedAt: createdAt
	});
}

const ids = (plates: Plate[]) => plates.map((p) => p.location.locationId);

describe('ad-hoc location ids', () => {
	// The shape of give-it-a-title.selection.json: four places, all "New location".
	const plates = [
		adhoc('sicily', 'New location', [14.95, 37.76], '2026-10-01T00:00:00Z'),
		adhoc('oregon-ndvi', 'New location', [-123.23, 44.58], '2026-10-02T00:00:00Z'),
		adhoc('oregon-tc', 'New location', [-123.23, 44.58], '2026-10-03T00:00:00Z'),
		adhoc('alaska', 'New location', [-159.13, 66.54], '2026-10-04T00:00:00Z'),
		adhoc('venezuela', 'New location', [-60.82, 8.5], '2026-10-05T00:00:00Z')
	];

	it('gives each distinct place its own id and shares one per place', () => {
		const out = ids(assignAdhocIds(plates, new Set()));
		expect(out).toEqual([
			'new_location_37_76n_14_95e',
			'new_location_44_58n_123_23w',
			'new_location_44_58n_123_23w',
			'new_location_66_54n_159_13w',
			'new_location_8_50n_60_82w'
		]);
	});

	it('is idempotent and returns the same array when nothing changes', () => {
		const once = assignAdhocIds(plates, new Set());
		expect(assignAdhocIds(once, new Set())).toBe(once);
	});

	it('does not depend on plate order', () => {
		const shuffled = [plates[3], plates[1], plates[4], plates[0], plates[2]];
		const byId = (ps: Plate[]) => Object.fromEntries(ps.map((p) => [p.id, p.location.locationId]));
		expect(byId(assignAdhocIds(shuffled, new Set()))).toEqual(byId(assignAdhocIds(plates, new Set())));
	});

	it('treats a nudged frame as the same place', () => {
		const a = adhoc('a', 'Mt. Fuji', [138.72, 35.34], '2026-10-01T00:00:00Z');
		const b = adhoc('b', 'Mt Fuji', [138.8, 35.4], '2026-10-02T00:00:00Z');
		expect(ids(assignAdhocIds([a, b], new Set()))).toEqual(['mt_fuji', 'mt_fuji']);
	});

	it('keeps a named place bare but tags a second, distant place of the same name', () => {
		const il = adhoc('il', 'Springfield', [-89.65, 39.8], '2026-10-01T00:00:00Z');
		const ma = adhoc('ma', 'Springfield', [-72.59, 42.1], '2026-10-02T00:00:00Z');
		expect(ids(assignAdhocIds([il, ma], new Set()))).toEqual(['springfield', 'springfield_42_10n_72_59w']);
	});

	it('never borrows a pool id', () => {
		const p = adhoc('x', 'Mt. Etna', [15.2, 37.75], '2026-10-01T00:00:00Z');
		expect(ids(normalizeAdhocIds({ ...emptyProject('t'), plates: [p] }).plates)).toEqual([
			`mt_etna_${coordTag([15.2, 37.75])}`
		]);
	});

	it('repairs stored projects on load', () => {
		const project = { ...emptyProject('t'), plates };
		expect(new Set(ids(migrate(JSON.parse(JSON.stringify(project))).plates)).size).toBe(4);
	});
});
