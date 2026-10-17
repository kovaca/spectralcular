import { describe, expect, it } from 'vitest';
import type { Texture } from '@luma.gl/core';
import { indices } from '../src/lib/data';
import { planSlots } from '../src/lib/raster/formula/resolve';
import { buildIndexLayer, type IndexLayerOptions } from '../src/lib/raster/gpu/buildLayer';

const index = indices.find((d) => planSlots(d, { wantMask: true }).gpuSupported)!;
const plan = planSlots(index, { wantMask: true });
const hrefs = Object.fromEntries(plan.sources.map((k) => [k, `https://example.test/${k}.tif`]));
const base: IndexLayerOptions = {
	id: 'plate-test',
	sceneId: 'scene-a',
	hrefs,
	index,
	colormapTexture: {} as Texture,
	colormapRow: 0,
	vmin: -1,
	vmax: 1
};

type Built = { props: { sources: unknown; updateTriggers: { renderTile: unknown[] } } };
const build = (o: Partial<IndexLayerOptions>) => buildIndexLayer({ ...base, ...o }) as unknown as Built;

describe('buildIndexLayer prop stability', () => {
	it('keeps `sources` by reference when only uniforms change, so COGs are not reopened', () => {
		const a = build({});
		const b = build({ colormapRow: 7, reversed: true, vmin: 0, vmax: 0.8, hrefs: { ...hrefs } });
		expect(b.props.sources).toBe(a.props.sources);
		expect(b.props.updateTriggers.renderTile).not.toEqual(a.props.updateTriggers.renderTile);
	});

	it('replaces `sources` when a URL changes', () => {
		const a = build({});
		const k = plan.sources[0];
		const b = build({ hrefs: { ...hrefs, [k]: `${hrefs[k]}?sig=new` } });
		expect(b.props.sources).not.toBe(a.props.sources);
	});
});

describe('buildTrueColorLayer prop stability', async () => {
	const { buildTrueColorLayer } = await import('../src/lib/raster/gpu/buildLayer');
	const { compileTrueColorModule, planRgbSlots } = await import('../src/lib/raster/gpu/trueColorModule');
	const { getPlatform } = await import('../src/lib/data');
	const s2 = getPlatform('sentinel-2-l2a')!;
	const tcHrefs = { red: 'https://example.test/red.tif', green: 'https://example.test/green.tif', blue: 'https://example.test/blue.tif', scl: 'https://example.test/scl.tif' };
	const tone = {
		preset: 'natural',
		stretch: { mode: 'linked' as const, source: 'manual' as const, pLo: 2, pHi: 98, lo: [0, 0, 0] as [number, number, number], hi: [0.3, 0.3, 0.3] as [number, number, number] },
		gamma: 1.8,
		contrast: 0,
		midpoint: 0.5,
		saturation: 1
	};
	type TcBuilt = { props: { sources: Record<string, unknown>; slotAssets: string[]; maskAsset: string | null; updateTriggers: { renderTile: unknown[] } } };
	const buildTc = (o: Partial<Parameters<typeof buildTrueColorLayer>[0]> = {}) =>
		buildTrueColorLayer({ id: 'tc-test', sceneId: 's', hrefs: tcHrefs, platform: s2, tone, ...o }) as unknown as TcBuilt;

	it('binds red, green, blue to slots 0..2 and the mask separately', () => {
		const l = buildTc();
		expect(l.props.slotAssets).toEqual(['red', 'green', 'blue']);
		expect(l.props.maskAsset).toBe('scl');
		expect(Object.keys(l.props.sources).sort()).toEqual(['blue', 'green', 'red', 'scl']);
	});

	it('keeps `sources` when only the tone changes, and re-renders tiles', () => {
		const a = buildTc();
		const b = buildTc({ tone: { ...tone, gamma: 1.2, saturation: 1.3, stretch: { ...tone.stretch, hi: [0.4, 0.4, 0.4] } }, hrefs: { ...tcHrefs } });
		expect(b.props.sources).toBe(a.props.sources);
		expect(b.props.updateTriggers.renderTile).not.toEqual(a.props.updateTriggers.renderTile);
	});

	it('draws an unresolved auto stretch with the provisional window', () => {
		const l = buildTc({ tone: { ...tone, stretch: { ...tone.stretch, source: 'auto', lo: null, hi: null } } });
		expect(l.props.updateTriggers.renderTile.slice(0, 6)).toEqual([0, 0, 0, 0.3, 0.3, 0.3]);
	});

	it('one shader module per platform and mask choice; none for radar', () => {
		expect(compileTrueColorModule(s2).module.name).toBe('gbTrueColor_sentinel_2_l2a_m1');
		expect(compileTrueColorModule(s2, { wantMask: false }).module.name).toBe('gbTrueColor_sentinel_2_l2a_m0');
		expect(compileTrueColorModule(s2)).toBe(compileTrueColorModule(s2));
		expect(planRgbSlots(getPlatform('sentinel-1-rtc')!).gpuSupported).toBe(false);
		expect(() => compileTrueColorModule(getPlatform('sentinel-1-rtc')!)).toThrow(/no visible/);
	});
});
