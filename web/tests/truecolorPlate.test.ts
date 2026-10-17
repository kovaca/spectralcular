import { describe, expect, it } from 'vitest';
import { emptyProject, PlateSchema, SCHEMA_VERSION, plateSlug, type Plate, type RgbFrameStats, type Scene } from '../src/lib/state/schema';
import { migrate } from '../src/lib/state/migrations';
import { loadProject, saveProject, STORAGE_KEY } from '../src/lib/state/persistence';
import { buildManifest, MANIFEST_VERSION, plateToManifest, validateForExport } from '../src/lib/export/manifest';
import { candidatesYaml } from '../src/lib/export/yamlSnippets';
import { channelStats, rgbHistogramWindow } from '../src/lib/raster/stats/compute';
import { RGB_PERCENTILES } from '../src/lib/raster/stats/types';
import { getLocation, getPlatform } from '../src/lib/data';
import { trueColorBlockerFor } from '../src/lib/data/fit';

const NOW = '2026-10-01T00:00:00.000Z';
const S2_ASSETS = { red: 'https://x/red.tif', green: 'https://x/green.tif', blue: 'https://x/blue.tif', scl: 'https://x/scl.tif' };

const scene = (collection = 'sentinel-2-l2a', assetHrefs: Record<string, string> = S2_ASSETS): Scene => ({
	collection,
	itemId: 'S2A_TEST',
	datetime: '2023-07-13T10:00:00Z',
	cloudCover: 1,
	mgrs: '33SWB',
	epsg: 32633,
	platform: 'sentinel-2a',
	geometry: { type: 'Polygon', coordinates: [[[15, 37], [16, 37], [16, 38], [15, 38], [15, 37]]] },
	assetHrefs,
	thumbnail: null
});

const tone = {
	preset: 'haze_cut',
	stretch: { mode: 'per-channel' as const, source: 'auto' as const, pLo: 1, pHi: 99.5, lo: [0.02, 0.03, 0.05] as [number, number, number], hi: [0.2, 0.22, 0.25] as [number, number, number] },
	gamma: 1.4,
	contrast: 6,
	midpoint: 0.45,
	saturation: 1.15
};

function tcPlate(extra: Partial<Plate> = {}): Plate {
	return PlateSchema.parse({
		id: 'tc1',
		ordinal: 1,
		location: { kind: 'pool', locationId: 'mt_etna', name: 'Mount Etna' },
		frame: { center: [15, 37.75], widthM: 17500, rotationDeg: 0 },
		scene: scene(),
		kind: 'truecolor',
		truecolor: tone,
		index: 'NDVI',
		colormap: { name: 'bids:viridis', reversed: true },
		normalization: { method: 'percentile', lower: 1, upper: 99 },
		createdAt: NOW,
		updatedAt: NOW,
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

describe('schema v2', () => {
	it('migrates v1 projects: every plate becomes an index plate', () => {
		const v1 = {
			schemaVersion: 1,
			id: 'p',
			name: 'Old book',
			createdAt: NOW,
			updatedAt: NOW,
			plates: [
				{
					id: 'a',
					ordinal: 1,
					location: { kind: 'pool', locationId: 'mt_etna', name: 'Mount Etna' },
					frame: { center: [15, 37.75], widthM: 17500, rotationDeg: 0 },
					scene: null,
					index: 'NDVI',
					colormap: { name: 'bids:viridis', reversed: false },
					normalization: { method: 'percentile', lower: 1, upper: 99 },
					createdAt: NOW,
					updatedAt: NOW
				}
			]
		};
		const p = migrate(v1);
		expect(SCHEMA_VERSION).toBe(2);
		expect(p.schemaVersion).toBe(2);
		expect(p.plates[0].kind).toBe('index');
		expect(p.plates[0].truecolor).toBeNull();
		expect(p.plates[0].rgbStats).toBeNull();
	});

	it('a v1 project in storage loads (not quarantined)', () => {
		const s = new MemStorage();
		s.setItem(STORAGE_KEY, JSON.stringify({ schemaVersion: 1, id: 'p', name: 'Old', createdAt: NOW, updatedAt: NOW, plates: [] }));
		const res = loadProject(s);
		expect(res.error).toBeUndefined();
		expect(res.project.schemaVersion).toBe(2);
	});

	it('round-trips a true-color plate through storage', () => {
		const s = new MemStorage();
		const p = { ...emptyProject('TC'), plates: [tcPlate()] };
		saveProject(p, s);
		const back = loadProject(s).project.plates[0];
		expect(back.kind).toBe('truecolor');
		expect(back.truecolor).toEqual(tone);
		// the index look is remembered for switching back
		expect(back.colormap).toEqual({ name: 'bids:viridis', reversed: true });
	});

	it('rejects an out-of-range tone', () => {
		expect(() => tcPlate({ truecolor: { ...tone, gamma: 0 } })).toThrow();
	});
});

describe('manifest v2', () => {
	it('exports a true-color plate per the interface contract', () => {
		const m = plateToManifest(tcPlate());
		expect(m.kind).toBe('truecolor');
		expect(m.id).toBe('mt_etna__truecolor');
		expect(m.index).toBeNull();
		expect(m.colormap).toBeNull();
		expect(m.colormapReversed).toBe(false);
		expect(m.normalization).toBeNull();
		expect(m.platform).toBe('sentinel-2-l2a');
		// the location's primary theme, not the remembered index's
		expect(m.theme).toBe(getLocation('mt_etna')!.themes[0]);
		expect(m.truecolor).toEqual(tone);
		expect(m.truecolor!.stretch.lo).toHaveLength(3);
	});

	it('sits in its location’s theme, not the remembered index’s', () => {
		const powell = tcPlate({ location: { kind: 'pool', locationId: 'lake_powell_glen_canyon', name: 'Lake Powell' } });
		expect(plateToManifest(powell).theme).toBe('water');
		expect(plateToManifest({ ...powell, kind: 'index' }).theme).toBe('vegetation');
	});

	it('takes the platform from the scene collection', () => {
		const ls = scene('landsat-c2-l2', { red: 'r', green: 'g', blue: 'b', qa_pixel: 'q' });
		expect(plateToManifest(tcPlate({ scene: ls })).platform).toBe('landsat-c2-l2');
	});

	it('index plates keep their v1 shape plus kind and a null tone', () => {
		const m = plateToManifest(tcPlate({ kind: 'index', truecolor: null }));
		expect(m.kind).toBe('index');
		expect(m.id).toBe('mt_etna__NDVI__bids_viridis_r');
		expect(m.index).toBe('NDVI');
		expect(m.colormap).toBe('bids:viridis');
		expect(m.truecolor).toBeNull();
		expect(m.normalization?.method).toBe('percentile');
	});

	it('bumps the manifest version', () => {
		const man = buildManifest({ ...emptyProject(), plates: [tcPlate()] });
		expect(MANIFEST_VERSION).toBe(2);
		expect(man.version).toBe(2);
	});

	it('validation: unresolved stretch, radar scene, duplicates', () => {
		const unresolved = tcPlate({ truecolor: { ...tone, stretch: { ...tone.stretch, lo: null, hi: null } } });
		expect(validateForExport({ ...emptyProject(), plates: [unresolved] }).join()).toMatch(/not resolved/);
		const radar = tcPlate({ scene: scene('sentinel-1-rtc', { vv: 'v', vh: 'h' }) });
		expect(validateForExport({ ...emptyProject(), plates: [radar] }).join()).toMatch(/no visible/);
		const dup = validateForExport({ ...emptyProject(), plates: [tcPlate(), tcPlate({ id: 'tc2', ordinal: 2 })] });
		expect(dup.join()).toMatch(/duplicates #1 \(mt_etna__truecolor\)/);
		expect(validateForExport({ ...emptyProject(), plates: [tcPlate()] })).toEqual([]);
	});

	it('writes a snake_case truecolor block in candidates.yaml', () => {
		const y = candidatesYaml(buildManifest({ ...emptyProject(), plates: [tcPlate()] }));
		expect(y).toContain('- id: mt_etna__truecolor');
		expect(y).toContain('  kind: truecolor');
		expect(y).toContain('  platform: sentinel-2-l2a');
		expect(y).toContain('  stretch: {mode: per-channel, source: auto, p_lo: 1, p_hi: 99.5, lo: [0.02, 0.03, 0.05], hi: [0.2, 0.22, 0.25]}');
		expect(y).toContain('  gamma: 1.4');
		expect(y).not.toContain('  index:');
		expect(y).not.toContain('normalization');
	});

	it('slug helper matches both kinds', () => {
		expect(plateSlug(tcPlate())).toBe('mt_etna__truecolor');
		expect(plateSlug(tcPlate({ kind: 'index' }))).toBe('mt_etna__NDVI__bids_viridis_r');
	});
});

describe('true-color availability', () => {
	it('blocks radar and scenes missing a visible band', () => {
		expect(trueColorBlockerFor(getPlatform('sentinel-1-rtc')!, null)).toMatch(/no visible/);
		expect(trueColorBlockerFor(getPlatform('sentinel-2-l2a')!, scene('sentinel-2-l2a', { red: 'r', green: 'g' }))).toMatch(/no blue/);
		expect(trueColorBlockerFor(getPlatform('landsat-c2-l2')!, null)).toBeNull();
	});
});

describe('RGB frame statistics', () => {
	it('bins from 0 to a quarter past the brightest p99.5', () => {
		expect(rgbHistogramWindow(0.01, 0.4)).toEqual([0, 0.5]);
		expect(rgbHistogramWindow(-0.03, 0.4)[0]).toBe(-0.03);
		expect(rgbHistogramWindow(-0.5, 5)).toEqual([-0.1, 1.6]);
	});
	it('channel percentiles are numpy-linear', () => {
		const v = Float64Array.from({ length: 101 }, (_, i) => i / 100);
		const s = channelStats(v, RGB_PERCENTILES, 0, 1, 10);
		expect(s.percentiles[RGB_PERCENTILES.indexOf(50)]).toBeCloseTo(0.5, 12);
		expect(s.percentiles[RGB_PERCENTILES.indexOf(0.5)]).toBeCloseTo(0.005, 12);
		expect(s.mean).toBeCloseTo(0.5, 12);
		expect(s.counts.reduce((a, b) => a + b, 0)).toBe(101);
	});
});

describe('session true-color mode', async () => {
	const { Session } = await import('../src/lib/state/session.svelte');
	const rgbStats: RgbFrameStats = {
		computedFor: 'k',
		resolutionM: 80,
		nPixels: 100,
		validFraction: 1,
		nodataFraction: 0,
		maskedFraction: 0,
		qs: [...RGB_PERCENTILES],
		edges: [0, 0.5],
		channels: [0, 1, 2].map((c) => ({ percentiles: RGB_PERCENTILES.map((q) => (q / 100) * 0.3 + c * 0.01), mean: 0.15, counts: [] as number[] })) as unknown as RgbFrameStats['channels']
	};

	it('selects true color with the default preset and keeps the index look', () => {
		const s = new Session();
		expect(s.selectTrueColor()).toBe(true);
		expect(s.draft.kind).toBe('truecolor');
		expect(s.draft.truecolor?.preset).toBe('natural');
		expect(s.draft.index).toBe('NDVI');
		s.toggleReversed();
		expect(s.draft.colormap.reversed).toBe(false);
		s.setColormap('bids:viridis');
		expect(s.draft.kind).toBe('index');
		// returning to true color brings the same tone back
		s.selectTrueColor();
		expect(s.draft.truecolor?.preset).toBe('natural');
		s.dispose();
	});

	it('auto stretches resolve from the frame stats; typed numbers make it manual', () => {
		const s = new Session();
		s.selectTrueColor();
		s.draft.rgbStats = rgbStats;
		s.applyTrueColorPreset('balanced');
		const st = s.draft.truecolor!.stretch;
		expect(st.source).toBe('auto');
		expect(st.lo![1]).toBeCloseTo(0.02 * 0.3 + 0.01, 12);
		expect(st.hi![2]).toBeCloseTo(0.98 * 0.3 + 0.02, 12);
		s.setStretch({ mode: 'linked' });
		const linked = s.draft.truecolor!.stretch;
		expect(new Set(linked.lo).size).toBe(1);
		expect(linked.hi![0]).toBeCloseTo(0.98 * 0.3 + 0.02, 12);
		s.setStretch({ lo: [0, 0, 0], hi: [0.3, 0.3, 0.3] });
		expect(s.draft.truecolor!.stretch.source).toBe('manual');
		s.setTone({ gamma: 2.2 });
		expect(s.draft.truecolor!.gamma).toBe(2.2);
		s.resetToPreset();
		expect(s.draft.truecolor!.gamma).toBe(1.1);
		s.dispose();
	});

	it('III Read index suggestions never apply to a true-color plate', () => {
		const s = new Session();
		s.selectTrueColor();
		const before = s.draft.normalization;
		s.applyStretch({ id: 'x', title: 'x', detail: '', ramp: 'diverging', normalization: { method: 'diverging' }, recommended: true, active: false, use: null } as never);
		expect(s.draft.normalization).toEqual(before);
		expect(s.draft.kind).toBe('truecolor');
		s.dispose();
	});

	it('round-trips through toPlate / loadPlate', () => {
		const s = new Session();
		s.selectTrueColor();
		s.draft.rgbStats = rgbStats;
		const p = s.toPlate();
		expect(p.kind).toBe('truecolor');
		expect(p.rgbStats?.qs).toEqual([...RGB_PERCENTILES]);
		const t = new Session();
		t.loadPlate(p);
		expect(t.draft.kind).toBe('truecolor');
		expect(t.draft.truecolor).toEqual(p.truecolor);
		s.dispose();
		t.dispose();
	});
});
