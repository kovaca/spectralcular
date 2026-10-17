import { describe, expect, it } from 'vitest';
import { PAGE, printStats, ceilingMeters, dpiStatus } from '../src/lib/book/page';
import book from '../src/lib/data/book.json';

describe('page spec', () => {
	it('uses the bleed box aspect from book.json', () => {
		expect(PAGE.aspect).toBeCloseTo(8.75 / 11.25, 9);
		expect(PAGE.dpi).toBe(300);
	});
	it('reproduces the AOI ceiling in book_specs.yaml at 10 m', () => {
		const c = ceilingMeters(10);
		expect(c.widthM / 1000).toBeCloseTo(book.aoiCeilingKm.width, 6);
		expect(c.heightM / 1000).toBeCloseTo(book.aoiCeilingKm.height, 6);
	});
	it('computes effective dpi for a 17.5 km frame at 10 m', () => {
		const s = printStats(17_500, 10);
		expect(s.nativePxW).toBeCloseTo(1750, 6);
		expect(s.effectiveDpi).toBeCloseTo(200, 6);
		expect(s.status).toBe('upsampled');
		expect(s.withinCeiling).toBe(true);
		expect(s.targetPxW).toBe(2625);
	});
	it('flags frames past the ceiling and soft ones', () => {
		expect(printStats(30_000, 10).withinCeiling).toBe(false);
		expect(printStats(30_000, 10).status).toBe('full');
		expect(printStats(10_000, 20).status).toBe('soft');
		expect(dpiStatus(300, 300)).toBe('full');
	});
});

describe('new frames', () => {
	it('start at exactly the print dpi for the index', async () => {
		const { defaultDraft, fullResolutionWidthM } = await import('../src/lib/state/session.svelte');
		expect(fullResolutionWidthM('NDVI')).toBeCloseTo(26_250, 6);
		expect(printStats(defaultDraft().frame.widthM, 10).effectiveDpi).toBeCloseTo(PAGE.dpi, 6);
	});
});
