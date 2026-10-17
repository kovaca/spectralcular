import { describe, expect, it } from 'vitest';
import fixturesJson from '../src/lib/data/truecolor_fixtures.json';
import type { TrueColorFixturesFile, TrueColorSpec, Triple } from '../src/lib/data/types';
import { DEFAULT_TRUECOLOR_PRESET, truecolorPresets } from '../src/lib/data';
import {
	isResolved,
	resolveStretch,
	saturate,
	sigmoidal,
	specFromPreset,
	toneCurve,
	tonePixel,
	moveEnd,
	withMode,
	type ChannelPercentiles
} from '../src/lib/raster/truecolor';

const fixtures = fixturesJson as unknown as TrueColorFixturesFile;

const spec = (o: Partial<TrueColorSpec> = {}): TrueColorSpec => ({
	preset: null,
	stretch: { mode: 'linked', source: 'manual', pLo: 2, pHi: 98, lo: [0, 0, 0], hi: [1, 1, 1] },
	gamma: 1,
	contrast: 0,
	midpoint: 0.5,
	saturation: 1,
	...o
});

describe('tone pipeline parity with Python (truecolor_fixtures.json)', () => {
	it('has fixtures for the current spec version', () => {
		expect(fixtures.specVersion).toBe(1);
		expect(fixtures.cases.length).toBeGreaterThan(5);
	});
	fixtures.cases.forEach((c, i) => {
		it(`case ${i}: ${c.spec.stretch.mode}, γ ${c.spec.gamma}, β ${c.spec.contrast}, s ${c.spec.saturation}`, () => {
			c.reflectance.forEach((rho, j) => {
				const out = tonePixel(rho, c.spec)!;
				for (let k = 0; k < 3; k++) expect(out[k]).toBeCloseTo(c.rgb[j][k], 5);
			});
		});
	});
});

describe('tone steps', () => {
	it('identity spec is a clamp', () => {
		expect(tonePixel([-0.2, 0.25, 1.5], spec())).toEqual([0, 0.25, 1]);
	});
	it('NaN in any channel is transparent', () => {
		expect(tonePixel([0.1, NaN, 0.1], spec())).toBeNull();
	});
	it('sigmoid fixes the ends and is off at zero strength', () => {
		expect(sigmoidal(0, 10, 0.5)).toBeCloseTo(0, 12);
		expect(sigmoidal(1, 10, 0.5)).toBeCloseTo(1, 12);
		expect(sigmoidal(0.3, 0, 0.5)).toBe(0.3);
	});
	it('saturation 0 grays and 1 is identity', () => {
		const rgb: Triple = [0.8, 0.3, 0.1];
		expect(saturate(rgb, 1)).toBe(rgb);
		const g = saturate(rgb, 0);
		expect(g[0]).toBeCloseTo(g[1], 5);
		expect(g[1]).toBeCloseTo(g[2], 5);
	});
	it('tone curve equals the pipeline without saturation', () => {
		const s = spec({ stretch: { ...spec().stretch, lo: [0.02, 0.02, 0.02], hi: [0.4, 0.4, 0.4] }, gamma: 1.6, contrast: 5, midpoint: 0.4 });
		for (const x of [0, 0.1, 0.2, 0.35, 0.5]) expect(toneCurve(x, s)).toBeCloseTo(tonePixel([x, x, x], s)![0], 12);
	});
});

describe('stretch resolution', () => {
	const stats: ChannelPercentiles = {
		qs: [0.5, 1, 2, 5, 50, 95, 98, 99, 99.5],
		channels: [
			{ percentiles: [0.01, 0.02, 0.03, 0.04, 0.08, 0.15, 0.18, 0.2, 0.22] },
			{ percentiles: [0.02, 0.03, 0.04, 0.05, 0.09, 0.2, 0.25, 0.28, 0.3] },
			{ percentiles: [0.05, 0.06, 0.07, 0.08, 0.1, 0.16, 0.17, 0.18, 0.19] }
		]
	};
	const auto = { mode: 'per-channel' as const, source: 'auto' as const, pLo: 2, pHi: 98, lo: null, hi: null };
	it('per-channel takes each band’s own percentiles', () => {
		const st = resolveStretch(auto, stats);
		expect(st.lo).toEqual([0.03, 0.04, 0.07]);
		expect(st.hi).toEqual([0.18, 0.25, 0.17]);
	});
	it('linked takes the widest window', () => {
		const st = resolveStretch({ ...auto, mode: 'linked' }, stats);
		expect(st.lo).toEqual([0.03, 0.03, 0.03]);
		expect(st.hi).toEqual([0.25, 0.25, 0.25]);
	});
	it('manual and stat-less stretches pass through', () => {
		const manual = { ...auto, source: 'manual' as const, lo: [0, 0, 0] as Triple, hi: [0.3, 0.3, 0.3] as Triple };
		expect(resolveStretch(manual, stats)).toBe(manual);
		expect(resolveStretch(auto, null)).toBe(auto);
	});
	it('switching to linked unifies the window', () => {
		const per = resolveStretch(auto, stats);
		const linked = withMode(per, 'linked');
		expect(linked.lo).toEqual([0.03, 0.03, 0.03]);
		expect(linked.hi).toEqual([0.25, 0.25, 0.25]);
	});
	it('dark-object keeps each band’s black point and shares the widest span', () => {
		const st = resolveStretch({ ...auto, mode: 'dark-object' }, stats);
		expect(st.lo).toEqual([0.03, 0.04, 0.07]);
		// spans: R 0.15, G 0.21, B 0.10 → all get 0.21
		st.hi!.forEach((h, c) => expect(h - st.lo![c]).toBeCloseTo(0.21, 12));
		expect(isResolved({ ...spec(), stretch: st })).toBe(true);
		expect(isResolved({ ...spec(), stretch: { ...st, hi: [0.3, 0.3, 0.3] } })).toBe(false);
	});
	it('switching to dark-object keeps black points and adopts the widest span', () => {
		const per = resolveStretch(auto, stats);
		const d = withMode(per, 'dark-object');
		expect(d.lo).toEqual(per.lo);
		d.hi!.forEach((h, c) => expect(h - d.lo![c]).toBeCloseTo(0.21, 12));
	});
	it('moving an end keeps each mode’s invariant', () => {
		const lo: Triple = [0.02, 0.03, 0.05];
		const hi: Triple = [0.22, 0.23, 0.25];
		// dark-object: a black point drags its white point (span kept)
		let w = moveEnd(lo, hi, 'dark-object', 'lo', 2, 0.07, 0.005);
		expect(w.lo).toEqual([0.02, 0.03, 0.07]);
		expect(w.hi[2]).toBeCloseTo(0.27, 12);
		// …and a white point sets the shared span for every band
		w = moveEnd(lo, hi, 'dark-object', 'hi', 0, 0.32, 0.005);
		w.hi.forEach((h, c) => expect(h - w.lo[c]).toBeCloseTo(0.3, 12));
		// linked moves all bands; per-channel only the one
		expect(moveEnd([0, 0, 0], [0.3, 0.3, 0.3], 'linked', 'hi', null, 0.4, 0.005).hi).toEqual([0.4, 0.4, 0.4]);
		expect(moveEnd(lo, hi, 'per-channel', 'hi', 1, 0.4, 0.005).hi).toEqual([0.22, 0.4, 0.25]);
	});
	it('every shipped preset resolves against frame stats', () => {
		expect(DEFAULT_TRUECOLOR_PRESET.id).toBe('natural');
		for (const p of truecolorPresets) {
			const s = specFromPreset(p, stats);
			expect(s.preset).toBe(p.id);
			expect(isResolved(s)).toBe(true);
		}
	});
});
