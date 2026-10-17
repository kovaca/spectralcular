import { describe, expect, it } from 'vitest';
import {
	frameCorners,
	frameEnvelope,
	frameFromBbox,
	frameContains,
	lonLatToPage,
	pageToLonLat,
	normalizeRotation,
	frameHeightM
} from '../src/lib/geo/frame';
import { bboxSizeMeters, lonLatToLocal, localToLonLat } from '../src/lib/geo/tangent';
import { coverageFraction } from '../src/lib/geo/coverage';
import { projectorForEpsg, utmProjFromEpsg } from '../src/lib/geo/utm';

const ASPECT = 8.75 / 11.25;

describe('tangent plane', () => {
	it('round-trips lon/lat ↔ local meters', () => {
		const o: [number, number] = [15.0, 37.75];
		const p = localToLonLat(o, 12345, -6789);
		const [e, n] = lonLatToLocal(o, p);
		expect(e).toBeCloseTo(12345, 6);
		expect(n).toBeCloseTo(-6789, 6);
	});
	it('one degree of latitude is ~111 km', () => {
		const s = bboxSizeMeters([0, 0, 1, 1]);
		expect(s.heightM).toBeGreaterThan(110_000);
		expect(s.heightM).toBeLessThan(112_000);
	});
});

describe('frame', () => {
	const f = { center: [15.0, 37.75] as [number, number], widthM: 17_500, rotationDeg: 0 };
	it('north-up corners bound a rectangle with the page aspect', () => {
		const [tl, tr, br] = frameCorners(f, ASPECT);
		expect(tl[1]).toBeCloseTo(tr[1], 9);
		expect(tr[0]).toBeCloseTo(br[0], 9);
		const [e, n] = lonLatToLocal(f.center, tr);
		expect(e).toBeCloseTo(8750, 3);
		expect(n).toBeCloseTo(frameHeightM(f, ASPECT) / 2, 3);
	});
	it('width is invariant under rotation', () => {
		for (const rot of [0, 33, 90, -120, 180]) {
			const g = { ...f, rotationDeg: rot };
			const [tl, tr, br, bl] = frameCorners(g, ASPECT);
			const d = (a: [number, number], b: [number, number]) => {
				const [e1, n1] = lonLatToLocal(g.center, a);
				const [e2, n2] = lonLatToLocal(g.center, b);
				return Math.hypot(e1 - e2, n1 - n2);
			};
			expect(d(tl, tr)).toBeCloseTo(17_500, 3);
			expect(d(bl, br)).toBeCloseTo(17_500, 3);
			expect(d(tl, bl)).toBeCloseTo(17_500 / ASPECT, 3);
		}
	});
	it('rotation 90 points the page top east', () => {
		const g = { ...f, rotationDeg: 90 };
		const top = pageToLonLat(g, 0, 1000);
		const [e, n] = lonLatToLocal(g.center, top);
		expect(e).toBeCloseTo(1000, 3);
		expect(n).toBeCloseTo(0, 3);
	});
	it('page coords round-trip', () => {
		const g = { ...f, rotationDeg: 27 };
		const p = pageToLonLat(g, 1234, -567);
		const [x, y] = lonLatToPage(g, p);
		expect(x).toBeCloseTo(1234, 4);
		expect(y).toBeCloseTo(-567, 4);
	});
	it('contains its own center and not a far point', () => {
		expect(frameContains(f, ASPECT, f.center)).toBe(true);
		expect(frameContains(f, ASPECT, [16, 37.75])).toBe(false);
	});
	it('fits inside a bbox (contract) with page aspect', () => {
		const bbox: [number, number, number, number] = [14.8, 37.6, 15.2, 37.9];
		const g = frameFromBbox(bbox, ASPECT);
		const env = frameEnvelope(g, ASPECT);
		expect(env[0]).toBeGreaterThanOrEqual(bbox[0] - 1e-9);
		expect(env[2]).toBeLessThanOrEqual(bbox[2] + 1e-9);
		expect(env[1]).toBeGreaterThanOrEqual(bbox[1] - 1e-9);
		expect(env[3]).toBeLessThanOrEqual(bbox[3] + 1e-9);
		const size = bboxSizeMeters(bbox);
		// the tall bbox is the limiting side here → width = height * aspect
		expect(g.widthM).toBeCloseTo(Math.min(size.widthM, size.heightM * ASPECT), 6);
	});
	it('normalizes rotation', () => {
		expect(normalizeRotation(370)).toBe(10);
		expect(normalizeRotation(-190)).toBe(170);
		expect(normalizeRotation(180)).toBe(180);
	});
});

describe('coverage', () => {
	it('is 1 when the scene contains the frame, ~0.5 when it covers half', () => {
		const frame = { type: 'Polygon' as const, coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]] };
		const big = { type: 'Polygon' as const, coordinates: [[[-1, -1], [2, -1], [2, 2], [-1, 2], [-1, -1]]] };
		const half = { type: 'Polygon' as const, coordinates: [[[0, 0], [0.5, 0], [0.5, 1], [0, 1], [0, 0]]] };
		expect(coverageFraction(frame, big)).toBeCloseTo(1, 6);
		expect(coverageFraction(frame, half)).toBeCloseTo(0.5, 3);
	});
});

describe('utm', () => {
	it('builds zone definitions', () => {
		expect(utmProjFromEpsg(32633)).toContain('+zone=33');
		expect(utmProjFromEpsg(32733)).toContain('+south');
		expect(() => utmProjFromEpsg(4326)).toThrow();
	});
	it('projects Etna into zone 33N near the known easting', () => {
		const p = projectorForEpsg(32633);
		const [x, y] = p.forward([15.0, 37.75]);
		expect(x).toBeCloseTo(500_000, -3);
		expect(y).toBeGreaterThan(4_170_000);
		const back = p.inverse([x, y]);
		expect(back[0]).toBeCloseTo(15.0, 6);
	});
});
