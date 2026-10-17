import { describe, expect, it } from 'vitest';
import { getIndex } from '$lib/data';
import { fits, indexFitForScene } from '../src/lib/data/fit';

const s2 = { collection: 'sentinel-2-l2a', assetHrefs: { red: 'r.tif', nir: 'n.tif', scl: 's.tif' } };
const s1Iw = { collection: 'sentinel-1-rtc', assetHrefs: { vv: 'vv.tiff', vh: 'vh.tiff' } };
const s1Polar = { collection: 'sentinel-1-rtc', assetHrefs: { hh: 'hh.tiff', hv: 'hv.tiff' } };
const ls7 = { collection: 'landsat-c2-l2', assetHrefs: { red: 'r.TIF', nir08: 'n.TIF', lwir: 't.TIF' } };

describe('indexFitForScene', () => {
	it('is neutral without a scene', () => {
		const f = indexFitForScene(getIndex('NDVI')!, null);
		expect(f.kind).toBe('no-scene');
		expect(fits(f)).toBe(true);
	});
	it('fits when the scene carries every band', () => {
		expect(indexFitForScene(getIndex('NDVI')!, s2).kind).toBe('ok');
		expect(indexFitForScene(getIndex('NDPolI')!, s1Iw).kind).toBe('ok');
	});
	it('flags an index defined on another platform', () => {
		const f = indexFitForScene(getIndex('NDVIT')!, s2);
		expect(f.kind).toBe('other-platform');
		expect(f.note).toMatch(/Landsat/);
		expect(fits(f)).toBe(false);
	});
	it('flags bands the item does not ship', () => {
		const hh = indexFitForScene(getIndex('RFDI')!, s1Iw);
		expect(hh.kind).toBe('missing-assets');
		expect(hh.missing).toEqual(['hh', 'hv']);
		expect(hh.note).toBe('scene has no HH · HV');
		expect(indexFitForScene(getIndex('RFDI')!, s1Polar).kind).toBe('ok');
		const th = indexFitForScene(getIndex('NDVIT')!, ls7);
		expect(th.kind).toBe('missing-assets');
		expect(th.note).toBe('scene has no B10');
	});
});
