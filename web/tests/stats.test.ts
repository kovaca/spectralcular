import { describe, expect, it } from 'vitest';
import { percentileSorted, summarize, histogram } from '../src/lib/raster/stats/percentiles';
import { resolveNormalization } from '../src/lib/raster/stats/normalization';

describe('percentiles (numpy linear)', () => {
	const a = Float64Array.from([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
	it('matches np.percentile', () => {
		expect(percentileSorted(a, 50)).toBeCloseTo(5.5, 9);
		expect(percentileSorted(a, 1)).toBeCloseTo(1.09, 9);
		expect(percentileSorted(a, 99)).toBeCloseTo(9.91, 9);
		expect(percentileSorted(a, 0)).toBe(1);
		expect(percentileSorted(a, 100)).toBe(10);
	});
	it('summarizes', () => {
		const s = summarize(Float32Array.from([3, 1, 2]));
		expect(s.min).toBe(1);
		expect(s.max).toBe(3);
		expect(s.mean).toBeCloseTo(2, 9);
		expect(s.p50).toBe(2);
	});
	it('histograms into bins with inclusive upper edge', () => {
		const h = histogram([0, 0.5, 1], 0, 1, 4);
		expect(h.counts).toEqual([1, 0, 1, 1]);
	});
});

describe('normalization', () => {
	const s = { n: 100, min: -0.5, max: 0.9, mean: 0.2, std: 0.3, p1: -0.3, p2: -0.25, p50: 0.2, p98: 0.8, p99: 0.85 };
	it('percentile 1–99 uses the stored percentiles', () => {
		const r = resolveNormalization({ method: 'percentile', lower: 1, upper: 99 }, s, null)!;
		expect(r.vmin).toBe(-0.3);
		expect(r.vmax).toBe(0.85);
		expect(r.description).toBe('pct[1,99]');
	});
	it('domain needs a domain; fixed needs nothing', () => {
		expect(resolveNormalization({ method: 'domain' }, s, null)).toBeNull();
		expect(resolveNormalization({ method: 'domain' }, null, [-1, 1])!.vmax).toBe(1);
		expect(resolveNormalization({ method: 'fixed', vmin: 0, vmax: 1 }, null, null)!.description).toBe('fixed[0,1]');
	});
	it('diverging is symmetric about zero', () => {
		const r = resolveNormalization({ method: 'diverging' }, s, null)!;
		expect(r.vmin).toBe(-0.85);
		expect(r.vmax).toBe(0.85);
	});
});
