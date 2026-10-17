import { afterEach, describe, expect, it, vi } from 'vitest';
import { sceneFromItem } from '../src/lib/stac/scene';
import { cloudLabel, isGeoTiffHref, itemCloudCover, itemGrid, itemThumbnail } from '../src/lib/stac/items';
import { appendToken, clearSasCache, getSasToken, signHrefs } from '../src/lib/stac/sas';
import type { StacItem } from '../src/lib/stac/types';
import platformsJson from '../src/lib/data/platforms.json';
import type { PlatformsFile } from '../src/lib/data/types';

const platforms = (platformsJson as unknown as PlatformsFile).entries;
const byId = Object.fromEntries(platforms.map((p) => [p.id, p]));

type ItemOverrides = Omit<Partial<StacItem>, 'properties' | 'assets'> & { properties?: Record<string, unknown>; assets?: StacItem['assets'] };
function item(over: ItemOverrides): StacItem {
	return {
		type: 'Feature',
		id: 'X',
		collection: 'c',
		bbox: [0, 0, 1, 1],
		geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
		links: [],
		...over,
		properties: { datetime: '2024-07-10T18:00:00Z', ...(over.properties ?? {}) },
		assets: over.assets ?? {}
	} as StacItem;
}

describe('sceneFromItem', () => {
	it('keeps Sentinel-2 behavior', () => {
		const s = sceneFromItem(
			item({
				id: 'S2A_33SVB_20230716_0_L2A',
				collection: 'sentinel-2-l2a',
				properties: { 'eo:cloud_cover': 0.5, 'grid:code': 'MGRS-33SVB', 'proj:epsg': 32633, platform: 'sentinel-2a' },
				assets: { red: { href: 'https://s/B04.tif' }, scl: { href: 'https://s/SCL.tif' }, thumbnail: { href: 'https://s/t.jpg' } }
			})
		);
		expect(s.mgrs).toBe('33SVB');
		expect(s.cloudCover).toBe(0.5);
		expect(Object.keys(s.assetHrefs).sort()).toEqual(['red', 'scl', 'thumbnail']);
	});
	it('accepts Landsat uppercase .TIF assets, WRS grid and rendered_preview thumbnails', () => {
		const s = sceneFromItem(
			item({
				id: 'LC09_L2SP_037037_20240710_02_T1',
				collection: 'landsat-c2-l2',
				properties: { 'eo:cloud_cover': 0, 'landsat:wrs_path': '037', 'landsat:wrs_row': '037', 'proj:epsg': 32612, platform: 'landsat-9' },
				assets: {
					red: { href: 'https://landsateuwest.blob.core.windows.net/x/LC09_SR_B4.TIF?st=1&sig=abc' },
					lwir11: { href: 'https://landsateuwest.blob.core.windows.net/x/LC09_ST_B10.TIF' },
					qa_pixel: { href: 'https://landsateuwest.blob.core.windows.net/x/LC09_QA_PIXEL.TIF' },
					rendered_preview: { href: 'https://planetarycomputer.microsoft.com/api/data/v1/item/preview.png?a=b' }
				}
			})
		);
		expect(s.mgrs).toBe('037/037');
		expect(s.epsg).toBe(32612);
		expect(s.assetHrefs.red).toBe('https://landsateuwest.blob.core.windows.net/x/LC09_SR_B4.TIF'); // token stripped
		expect(s.assetHrefs.qa_pixel).toBeDefined();
		expect(s.thumbnail).toContain('preview.png');
	});
	it('handles Sentinel-1 RTC: no cloud cover, .rtc.tiff assets, orbit grid', () => {
		const it_ = item({
			id: 'S1A_IW_GRDH_1SDV_20240729T110334_20240729T110403_054975_06B275_rtc',
			collection: 'sentinel-1-rtc',
			properties: { 'sat:orbit_state': 'ascending', 'sat:relative_orbit': 18, 'proj:epsg': 32648, platform: 'sentinel-1a' },
			assets: { vv: { href: 'https://s1/measurement/iw-vv.rtc.tiff' }, vh: { href: 'https://s1/measurement/iw-vh.rtc.tiff' }, tilejson: { href: 'https://pc/tilejson.json' } }
		});
		const s = sceneFromItem(it_);
		expect(Number.isNaN(itemCloudCover(it_))).toBe(true);
		expect(cloudLabel(itemCloudCover(it_))).toBe('n/a');
		expect(s.cloudCover).toBe(0);
		expect(s.mgrs).toBe('orbit 18 asc');
		expect(Object.keys(s.assetHrefs).sort()).toEqual(['vh', 'vv']);
		expect(itemThumbnail(it_)).toBeNull();
	});
	it('isGeoTiffHref is case-insensitive and tolerates query strings', () => {
		expect(isGeoTiffHref('a/B04.tif')).toBe(true);
		expect(isGeoTiffHref('a/B4.TIF?sig=1')).toBe(true);
		expect(isGeoTiffHref('a/iw-vv.rtc.tiff')).toBe(true);
		expect(isGeoTiffHref('a/thumb.jpg')).toBe(false);
	});
	it('itemGrid prefers MGRS, then WRS, then id', () => {
		expect(itemGrid(item({ id: 'S2A_20MNA_x', properties: { 's2:mgrs_tile': '20MNA' } }))).toBe('20MNA');
		expect(itemGrid(item({ id: 'A_B_C', properties: {} }))).toBe('B');
	});
});

describe('SAS signing', () => {
	afterEach(() => clearSasCache());
	const landsat = byId['landsat-c2-l2'];
	const s2 = byId['sentinel-2-l2a'];

	it('appends the collection token once and caches it until near expiry', async () => {
		const fetchFn = vi.fn(async () => ({
			ok: true,
			json: async () => ({ token: 'st=2026&sig=abc', 'msft:expiry': new Date(Date.now() + 3600_000).toISOString() })
		})) as unknown as typeof fetch;
		const t1 = await getSasToken('landsat-c2-l2', fetchFn);
		const t2 = await getSasToken('landsat-c2-l2', fetchFn);
		expect(t1).toBe('st=2026&sig=abc');
		expect(t2).toBe(t1);
		expect(fetchFn).toHaveBeenCalledTimes(1);
		expect(appendToken('https://b/x.TIF', t1)).toBe('https://b/x.TIF?st=2026&sig=abc');
		expect(appendToken('https://b/x.TIF?already=1', t1)).toBe('https://b/x.TIF?already=1');
	});
	it('refetches when the cached token is about to expire', async () => {
		const fetchFn = vi.fn(async () => ({
			ok: true,
			json: async () => ({ token: 't', 'msft:expiry': new Date(Date.now() + 10_000).toISOString() })
		})) as unknown as typeof fetch;
		await getSasToken('landsat-c2-l2', fetchFn);
		await getSasToken('landsat-c2-l2', fetchFn);
		expect(fetchFn).toHaveBeenCalledTimes(2);
	});
	it('signHrefs is the identity for public platforms', async () => {
		const hrefs = { red: 'https://sentinel-cogs/B04.tif' };
		expect(await signHrefs(hrefs, s2)).toBe(hrefs);
		expect(landsat.signing).toBe('planetary-computer');
	});
});

import { rankScenes, FULL_COVERAGE } from '$lib/stac/rank';
import type { Polygon } from 'geojson';

describe('rankScenes', () => {
	const frame: Polygon = { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]] };
	const big: Polygon = { type: 'Polygon', coordinates: [[[-1, -1], [2, -1], [2, 2], [-1, 2], [-1, -1]]] };
	const half: Polygon = { type: 'Polygon', coordinates: [[[0, 0], [0.5, 0], [0.5, 1], [0, 1], [0, 0]]] };
	const item = (id: string, collection: string, geometry: Polygon, cloud?: number) =>
		({ id, collection, geometry, properties: { datetime: '2023-07-01T00:00:00Z', ...(cloud === undefined ? {} : { 'eo:cloud_cover': cloud }) }, assets: {} }) as never;

	it('puts full-coverage scenes first, then the least cloud, and drops other collections', () => {
		const ranked = rankScenes(
			[item('partial-clear', 'c', half, 1), item('full-cloudy', 'c', big, 30), item('full-clear', 'c', big, 5), item('other', 'x', big, 0)],
			'c',
			frame
		);
		expect(ranked.map((r) => r.item.id)).toEqual(['full-clear', 'full-cloudy', 'partial-clear']);
		expect(ranked[0].coverage).toBeGreaterThanOrEqual(FULL_COVERAGE);
		expect(ranked[2].coverage).toBeLessThan(FULL_COVERAGE);
	});

	it('keeps a stable order for radar items without cloud cover', () => {
		const ranked = rankScenes([item('a', 'c', big), item('b', 'c', big)], 'c', frame);
		expect(ranked.map((r) => r.item.id)).toEqual(['a', 'b']);
	});
});
