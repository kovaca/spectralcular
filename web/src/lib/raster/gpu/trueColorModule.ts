import type { ShaderModule } from '@luma.gl/shadertools';
import type { PlatformDef } from '$lib/data/types';
import type { SlotPlan } from '../formula/resolve';
import { CONTRAST_EPS, LAB_TO_LMS, LIN_TO_LMS, LMS_TO_LAB, LMS_TO_LIN, SATURATION_EPS } from '../truecolor';
import { MODULE_NAME as BAND_STACK } from './bandStack';

type Vec4 = [number, number, number, number];

/** Runtime props: every value is a uniform, so tone edits never recompile. */
export interface TrueColorProps {
	/** stretch window in reflectance, r/g/b in xyz */
	lo: Vec4;
	hi: Vec4;
	gamma: number;
	contrast: number;
	midpoint: number;
	saturation: number;
	/** per-slot DN → reflectance: value = dn * scale + offset (r, g, b in xyz) */
	scale: Vec4;
	offset: Vec4;
	nodata: Vec4;
	nodataOn: Vec4;
	nodataTol: Vec4;
	maskKind: number;
	maskEnabled: boolean;
	maskBits: number;
	nodataBits: number;
}

export type TrueColorModuleProps = Record<string, number | boolean | readonly number[]>;

export interface CompiledTrueColorModule {
	module: ShaderModule<TrueColorModuleProps>;
	plan: SlotPlan;
}

const cache = new Map<string, CompiledTrueColorModule>();

/** Red, green, blue in slots 0..2; the platform mask in its own texture when wanted. */
export function planRgbSlots(platform: PlatformDef, wantMask = true): SlotPlan {
	const rgb = platform.rgbAssets;
	if (!rgb) {
		return { paramSlot: {}, assetSlot: {}, slotAssets: [], maskAsset: null, sources: [], platform, gpuSupported: false, reason: `${platform.label} has no visible bands` };
	}
	const maskAsset = wantMask && platform.mask ? platform.mask.asset : null;
	return {
		paramSlot: { R: 0, G: 1, B: 2 },
		assetSlot: { [rgb[0]]: 0, [rgb[1]]: 1, [rgb[2]]: 2 },
		slotAssets: [...rgb],
		maskAsset,
		sources: maskAsset ? [...rgb, maskAsset] : [...rgb],
		platform,
		gpuSupported: true
	};
}

/** Row-major 3×3 → GLSL `mat3` whose `v * M` equals the row-major `M · v`. */
const mat3 = (m: readonly (readonly number[])[]) => `mat3(${m.map((r) => r.map((x) => x.toFixed(10)).join(', ')).join(',\n    ')})`;
const f = (x: number) => x.toExponential(6);

/**
 * The true-color shader module: runs after the BandStack module and writes the
 * toned RGB into `color`. Mirrors ../truecolor.ts (and so truecolor.py) step
 * for step; nodata and mask handling match the spectral index module.
 */
export function compileTrueColorModule(platform: PlatformDef, opts: { wantMask?: boolean } = {}): CompiledTrueColorModule {
	const wantMask = opts.wantMask ?? true;
	const key = `${platform.id}|${wantMask}`;
	const hit = cache.get(key);
	if (hit) return hit;
	const plan = planRgbSlots(platform, wantMask);
	if (!plan.gpuSupported) throw new Error(`True color is not available on ${platform.label}: ${plan.reason}`);

	const safe = `${platform.id}_${wantMask ? 'm1' : 'm0'}`.replace(/[^A-Za-z0-9_]/g, '_');
	const NAME = `gbTrueColor_${safe}`;
	const P = `gbTc_${safe}`;
	const C = ['x', 'y', 'z'];

	const fs = /* glsl */ `
uniform ${NAME}Uniforms {
  float gamma;
  float contrast;
  float midpoint;
  float saturation;
  float maskEnabled;
  int maskKind;
  int maskBits;
  int nodataBits;
  vec4 lo;
  vec4 hi;
  vec4 scale;
  vec4 offset;
  vec4 nodata;
  vec4 nodataOn;
  vec4 nodataTol;
} ${NAME};
`;

	const decl = /* glsl */ `
const mat3 ${P}_LIN_TO_LMS = ${mat3(LIN_TO_LMS)};
const mat3 ${P}_LMS_TO_LAB = ${mat3(LMS_TO_LAB)};
const mat3 ${P}_LAB_TO_LMS = ${mat3(LAB_TO_LMS)};
const mat3 ${P}_LMS_TO_LIN = ${mat3(LMS_TO_LIN)};

vec3 ${P}_srgbToLinear(vec3 c) {
  return mix(pow((c + 0.055) / 1.055, vec3(2.4)), c / 12.92, vec3(lessThanEqual(c, vec3(0.04045))));
}
vec3 ${P}_linearToSrgb(vec3 c) {
  c = clamp(c, 0.0, 1.0);
  return mix(1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, c * 12.92, vec3(lessThanEqual(c, vec3(0.0031308))));
}
vec3 ${P}_sigmoidal(vec3 t, float b, float a) {
  if (b < ${f(CONTRAST_EPS)}) return t;
  float lo = 1.0 / (1.0 + exp(b * a));
  float hi = 1.0 / (1.0 + exp(b * (a - 1.0)));
  return clamp((1.0 / (1.0 + exp(b * (a - t))) - lo) / (hi - lo), 0.0, 1.0);
}
vec3 ${P}_saturate(vec3 rgb, float s) {
  if (abs(s - 1.0) < ${f(SATURATION_EPS)}) return rgb;
  vec3 lms = ${P}_srgbToLinear(rgb) * ${P}_LIN_TO_LMS;
  vec3 lab = (sign(lms) * pow(abs(lms), vec3(1.0 / 3.0))) * ${P}_LMS_TO_LAB;
  lab.yz *= s;
  vec3 l = lab * ${P}_LAB_TO_LMS;
  return ${P}_linearToSrgb((l * l * l) * ${P}_LMS_TO_LIN);
}
`;

	const perSlot = [0, 1, 2].map((i) => /* glsl */ `
  float dn${i} = ${BAND_STACK}_dn(${i}, uv);
  if (${NAME}.nodataOn.${C[i]} > 0.5 && abs(dn${i} - ${NAME}.nodata.${C[i]}) < ${NAME}.nodataTol.${C[i]}) bad = true;
  float b${i} = dn${i} * ${NAME}.scale.${C[i]} + ${NAME}.offset.${C[i]};`).join('');

	const filterColor = /* glsl */ `
{
  vec2 uv = geometry.uv;
  bool bad = false;${perSlot}
  if (${NAME}.maskKind > 0) {
    int cls = int(floor(${BAND_STACK}_maskDn(uv) + 0.5));
    int bits = ${NAME}.maskKind == 1 ? (cls < 31 ? (1 << cls) : 0) : cls;
    if ((bits & ${NAME}.nodataBits) != 0) bad = true;
    if (${NAME}.maskEnabled > 0.5 && (bits & ${NAME}.maskBits) != 0) bad = true;
  }
  vec3 rho = vec3(b0, b1, b2);
  if (any(isnan(rho))) bad = true;
  vec3 t = clamp((rho - ${NAME}.lo.xyz) / (${NAME}.hi.xyz - ${NAME}.lo.xyz), 0.0, 1.0);
  t = pow(t, vec3(1.0 / ${NAME}.gamma));
  t = ${P}_sigmoidal(t, ${NAME}.contrast, ${NAME}.midpoint);
  t = ${P}_saturate(t, ${NAME}.saturation);
  color = bad ? vec4(0.0) : vec4(t, 1.0);
}
`;

	const ZERO: Vec4 = [0, 0, 0, 0];
	const ONE: Vec4 = [1, 1, 1, 1];
	const HALF: Vec4 = [0.5, 0.5, 0.5, 0.5];
	const module = {
		name: NAME,
		fs,
		inject: {
			'fs:#decl': decl,
			'fs:DECKGL_FILTER_COLOR': filterColor
		},
		uniformTypes: {
			gamma: 'f32',
			contrast: 'f32',
			midpoint: 'f32',
			saturation: 'f32',
			maskEnabled: 'f32',
			maskKind: 'i32',
			maskBits: 'i32',
			nodataBits: 'i32',
			lo: 'vec4<f32>',
			hi: 'vec4<f32>',
			scale: 'vec4<f32>',
			offset: 'vec4<f32>',
			nodata: 'vec4<f32>',
			nodataOn: 'vec4<f32>',
			nodataTol: 'vec4<f32>'
		},
		getUniforms: (props?: Partial<TrueColorProps>) => {
			const p = (props ?? {}) as Partial<TrueColorProps>;
			return {
				gamma: p.gamma ?? 1,
				contrast: p.contrast ?? 0,
				midpoint: p.midpoint ?? 0.5,
				saturation: p.saturation ?? 1,
				maskEnabled: p.maskEnabled === false ? 0 : 1,
				maskKind: p.maskKind ?? 0,
				maskBits: p.maskBits ?? 0,
				nodataBits: p.nodataBits ?? 0,
				lo: p.lo ?? ZERO,
				hi: p.hi ?? ONE,
				scale: p.scale ?? ONE,
				offset: p.offset ?? ZERO,
				nodata: p.nodata ?? ZERO,
				nodataOn: p.nodataOn ?? ZERO,
				nodataTol: p.nodataTol ?? HALF
			};
		}
	} as unknown as ShaderModule<TrueColorModuleProps>;

	const compiled = { module, plan };
	cache.set(key, compiled);
	return compiled;
}
