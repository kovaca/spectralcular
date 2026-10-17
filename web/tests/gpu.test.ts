import { describe, expect, it } from 'vitest';
import { quantizeFloat32 } from '../src/lib/raster/gpu/BandStackCOGLayer';
import { dnMaxForFormat, buildBandStackProps, MAX_BAND_SLOTS } from '../src/lib/raster/gpu/bandStack';
import { packSlots } from '../src/lib/raster/gpu/spectralIndexModule';

type Vec4 = [number, number, number, number];

describe('float32 quantisation (Sentinel-1 gamma0)', () => {
	it('maps nodata and NaN to 0, values linearly to 1..65535, clamps at the ceiling', () => {
		const q = quantizeFloat32(new Float32Array([-32768, NaN, 0, 0.000001, 1.0, 2.0, 5.0]), { ceiling: 2.0, nodata: -32768 });
		expect(Array.from(q)).toEqual([0, 0, 1, 1, 32768, 65535, 65535]);
	});
	it('round-trips typical backscatter to within half a step', () => {
		const ceiling = 2.0;
		const vals = new Float32Array([0.0015, 0.02, 0.13, 0.42, 1.3]);
		const q = quantizeFloat32(vals, { ceiling, nodata: null });
		for (let i = 0; i < vals.length; i++) {
			expect(Math.abs((q[i] / 65535) * ceiling - vals[i])).toBeLessThanOrEqual((0.5 * ceiling) / 65535 + 1e-9);
		}
	});
});

describe('band stack helpers', () => {
	it('infers DN range from the texture format', () => {
		expect(dnMaxForFormat('r8unorm')).toBe(255);
		expect(dnMaxForFormat('r16unorm')).toBe(65535);
		expect(dnMaxForFormat('r32float')).toBe(1);
		expect(dnMaxForFormat(undefined)).toBe(65535);
	});
	it('binds slots in order, honours a tile-provided dnMax and rejects too many bands', () => {
		const tex = (format: string) => ({ format }) as unknown as import('@luma.gl/core').Texture;
		const tiles = new Map([
			['vv', { texture: tex('r16unorm'), uvTransform: [0, 0, 1, 1] as Vec4, dnMax: 2 }],
			['vh', { texture: tex('r16unorm'), uvTransform: [0.5, 0, 0.5, 1] as Vec4, dnMax: 2 }],
			['scl', { texture: tex('r8unorm'), uvTransform: [0, 0, 1, 1] as Vec4 }]
		]);
		const p = buildBandStackProps(['vv', 'vh'], 'scl', tiles);
		expect(p.dnMax).toEqual([2, 2]);
		expect(p.uvTransforms[1]).toEqual([0.5, 0, 0.5, 1]);
		expect(p.maskDnMax).toBe(255);
		expect(() => buildBandStackProps(Array.from({ length: MAX_BAND_SLOTS + 1 }, (_, i) => `b${i}`), null, tiles)).toThrow(/at most/);
		expect(() => buildBandStackProps(['missing'], null, tiles)).toThrow(/not found/);
	});
	it('packs up to eight slot values into two vec4s', () => {
		expect(packSlots([1, 2, 3, 4, 5], 9)).toEqual([[1, 2, 3, 4], [5, 9, 9, 9]]);
	});
});
