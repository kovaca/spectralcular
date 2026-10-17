import { describe, expect, it } from 'vitest';
import { getIndex, getColormap } from '$lib/data';
import type { IndexDef } from '$lib/data/types';
import { histogram, histogramWindow, summarize } from '$lib/raster/stats/percentiles';
import { resolveNormalization } from '$lib/raster/stats/normalization';
import {
	centerOf,
	colormapForRamp,
	composition,
	fractionBelow,
	rampFit,
	readingAxis,
	readingNotes,
	stretchOptions,
	typicalRange,
	type ReadingInput
} from '$lib/data/reading';
import type { NormalizationSpec } from '$lib/state/schema';

const NDVI = getIndex('NDVI') as IndexDef;

/** A synthetic frame: `n` values per [lo, hi) run, spread evenly. */
function frame(...runs: [number, number, number][]) {
	const vals: number[] = [];
	for (const [lo, hi, n] of runs) for (let i = 0; i < n; i++) vals.push(lo + ((hi - lo) * (i + 0.5)) / n);
	const summary = summarize(Float64Array.from(vals));
	const [a, b] = histogramWindow(summary, NDVI.domain);
	return { summary, histogram: histogram(vals, a, b, 64) };
}

function input(def: IndexDef, f: ReturnType<typeof frame> | null, norm: NormalizationSpec, cmap = 'bids:viridis'): ReadingInput {
	return {
		def,
		summary: f?.summary ?? null,
		histogram: f?.histogram ?? null,
		normalization: norm,
		range: resolveNormalization(norm, f?.summary ?? null, def.domain),
		colormap: getColormap(cmap) ?? null,
		maskedFraction: 0
	};
}

describe('exported reading metadata', () => {
	it('carries a guide, bounds and ordered reference surfaces for NDVI', () => {
		expect(NDVI.guide?.classes.length).toBeGreaterThan(2);
		expect(NDVI.bounds).toEqual({ lo: -1, hi: 1, loOpen: false, hiOpen: false });
		const v = NDVI.surfaces.map((s) => s.value);
		expect(v).toEqual([...v].sort((a, b) => a - b));
		expect(NDVI.surfaces.find((s) => s.id === 'rainforest')!.value).toBeGreaterThan(0.8);
	});
	it('gives unguided indices a typical range from the formula or the surfaces', () => {
		const ratio = getIndex('VVVHR') as IndexDef;
		expect(ratio.guide).toBeNull();
		expect(typicalRange(ratio)?.source).toBe('surfaces');
		expect(centerOf(ratio)).toBeNull();
		const gndvi = getIndex('GNDVI') as IndexDef;
		expect(typicalRange(gndvi)?.source).toBe('formula');
		expect(centerOf(gndvi)?.value).toBe(0);
	});
});

describe('histogramWindow', () => {
	it('bins the robust spread inside the domain, counting the tails', () => {
		const vals = [...Array.from({ length: 98 }, (_, i) => 0.3 + i * 0.005), -0.9, 0.99];
		const s = summarize(Float64Array.from(vals));
		const [lo, hi] = histogramWindow(s, [-1, 1]);
		expect(lo).toBeGreaterThan(-0.9);
		expect(hi).toBeLessThan(0.99);
		const h = histogram(vals, lo, hi, 64);
		expect(h.under! + h.over! + h.counts.reduce((a, b) => a + b, 0)).toBe(100);
	});
	it('keeps a long thin tail from squashing the bulk (outer fences)', () => {
		// 95 % between 0 and 1, 5 % strung out to 100: p99 sits far up the tail
		const bulk = Array.from({ length: 950 }, (_, i) => i / 950);
		const tail = Array.from({ length: 50 }, (_, i) => 2 + (i * 98) / 49);
		const s = summarize(Float64Array.from([...bulk, ...tail]));
		const [lo, hi] = histogramWindow(s, null);
		expect(lo).toBeLessThanOrEqual(0);
		expect(hi).toBeLessThan(4);
	});

	it('falls back to the p1–p99 rule for summaries saved without quartiles', () => {
		const s = summarize(Float64Array.from(Array.from({ length: 100 }, (_, i) => i)));
		const { p25: _a, p75: _b, ...old } = s;
		expect(histogramWindow(old, null)).toEqual([0, 99]);
	});
});

describe('composition and ramp fit', () => {
	it('shares a mixed frame across the guide classes', () => {
		const f = frame([-0.4, -0.05, 30], [0.55, 0.78, 70]);
		const shares = composition(NDVI.guide!.classes, f.histogram)!;
		const byLabel = Object.fromEntries(shares.map((s) => [s.cls.label, s.fraction]));
		expect(byLabel['water · snow']).toBeCloseTo(0.3, 1);
		expect(byLabel['green crops · grassland · woodland']).toBeCloseTo(0.7, 1);
		expect(shares.reduce((a, s) => a + s.fraction, 0)).toBeCloseTo(1, 6);
		expect(fractionBelow(f.histogram, 0)).toBeCloseTo(0.3, 1);
	});
	it('measures how much of the ramp a frame fills and how much clips', () => {
		const f = frame([0.6, 0.8, 100]);
		const fit = rampFit({ vmin: -1, vmax: 1, description: '' }, f.summary, f.histogram);
		expect(fit.use).toBeCloseTo(0.1, 1);
		expect(fit.clipped).toBeCloseTo(0, 6);
		const tight = rampFit({ vmin: 0.65, vmax: 0.75, description: '' }, f.summary, f.histogram);
		expect(tight.clipped).toBeCloseTo(0.5, 1);
	});
	it('widens the axis to show the frame', () => {
		const f = frame([0.9, 0.99, 100]);
		const [lo, hi] = readingAxis(NDVI, f.summary, null);
		expect(lo).toBeLessThan(NDVI.guide!.typical[0]);
		expect(hi).toBeGreaterThan(0.98);
	});
});

describe('stretchOptions', () => {
	it('recommends splitting at 0 when the frame holds both water and land', () => {
		const f = frame([-0.4, -0.05, 30], [0.55, 0.78, 70]);
		const opts = stretchOptions(input(NDVI, f, { method: 'percentile', lower: 1, upper: 99 }));
		expect(opts.map((o) => o.id)).toEqual(['frame', 'book', 'split']);
		const rec = opts.find((o) => o.recommended)!;
		expect(rec.id).toBe('split');
		expect(rec.normalization).toEqual({ method: 'diverging' });
		expect(rec.ramp).toBe('diverging');
	});
	it('recommends the book-wide range when the frame fills it well', () => {
		const f = frame([0.0, 0.8, 100]);
		const opts = stretchOptions(input(NDVI, f, { method: 'percentile', lower: 1, upper: 99 }));
		expect(opts.find((o) => o.recommended)!.id).toBe('book');
	});
	it('recommends fitting the frame when it occupies a sliver of the range', () => {
		const f = frame([0.8, 0.9, 100]);
		const opts = stretchOptions(input(NDVI, f, { method: 'fixed', vmin: -0.2, vmax: 0.9 }));
		expect(opts.find((o) => o.recommended)!.id).toBe('frame');
		expect(opts.find((o) => o.id === 'book')!.active).toBe(true);
	});
	it('splits a non-zero center with a symmetric fixed range', () => {
		const ndsi = getIndex('NDSI') as IndexDef;
		const f = frame([0.1, 0.8, 100]);
		const split = stretchOptions(input(ndsi, f, { method: 'percentile', lower: 1, upper: 99 })).find((o) => o.id === 'split')!;
		expect(split.normalization.method).toBe('fixed');
		if (split.normalization.method === 'fixed') expect((split.normalization.vmin + split.normalization.vmax) / 2).toBeCloseTo(0.4, 6);
	});
});

describe('readingNotes', () => {
	it('warns when a diverging ramp is centered off the meaningful value', () => {
		const f = frame([0.3, 0.8, 100]);
		const div = getColormap('cmocean:balance') ? 'cmocean:balance' : colormapForRamp('diverging', 'NDVI', null)!.name;
		const notes = readingNotes(input(NDVI, f, { method: 'percentile', lower: 2, upper: 98 }, div));
		expect(notes.some((n) => n.level === 'warn' && n.text.includes('midpoint'))).toBe(true);
	});
	it('warns when a split ramp has nothing on one side', () => {
		const f = frame([0.3, 0.8, 100]);
		const div = colormapForRamp('diverging', 'NDVI', null)!.name;
		const notes = readingNotes(input(NDVI, f, { method: 'diverging' }, div));
		expect(notes.some((n) => n.text.includes('one half of the diverging ramp goes unused'))).toBe(true);
	});
	it('warns when several classes collapse onto one end color', () => {
		const f = frame([-0.4, -0.05, 20], [0.05, 0.18, 20], [0.6, 0.78, 60]);
		const notes = readingNotes(input(NDVI, f, { method: 'fixed', vmin: 0.5, vmax: 0.8 }));
		expect(notes.some((n) => n.text.includes("ramp's first color"))).toBe(true);
	});
});

describe('colormapForRamp', () => {
	it('keeps a map that already has the wanted type and finds one otherwise', () => {
		const viridis = getColormap('bids:viridis')!;
		expect(colormapForRamp('sequential', 'NDVI', viridis)).toBe(viridis);
		expect(colormapForRamp('diverging', 'NDVI', viridis)?.type).toBe('diverging');
	});
});
