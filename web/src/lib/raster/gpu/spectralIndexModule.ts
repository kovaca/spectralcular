import type { ShaderModule } from '@luma.gl/shadertools';
import type { Texture } from '@luma.gl/core';
import type { IndexDef, MaskKind } from '$lib/data/types';
import { parseFormula } from '../formula/parser';
import { toGlsl, glslHelpers } from '../formula/glsl';
import { freeVariables } from '../formula/ast';
import { planSlots, type SlotPlan, type PlanOptions } from '../formula/resolve';
import { MODULE_NAME as BAND_STACK } from './bandStack';

type Vec4 = [number, number, number, number];

/** GPU encoding of a platform's mask semantics. */
export const MASK_KIND: Record<MaskKind | 'none', number> = { none: 0, 'scl-classes': 1, 'qa-bits': 2 };

/** Runtime props of the module — every value is a uniform, so changing them never recompiles. */
export interface SpectralIndexProps {
	colormapTexture: Texture;
	colormapIndex: number;
	reversed: boolean;
	vmin: number;
	vmax: number;
	/** per-slot DN → physical value: value = dn * scale + offset (slots 0-3 in *0, 4-7 in *1) */
	scale0: Vec4;
	scale1: Vec4;
	offset0: Vec4;
	offset1: Vec4;
	/** per-slot nodata DN; `nodataOn` = 1 enables the test for that slot */
	nodata0: Vec4;
	nodata1: Vec4;
	nodataOn0: Vec4;
	nodataOn1: Vec4;
	/** per-slot |dn - nodata| tolerance: 0.5 for integer DNs, half a quantisation step for packed floats */
	nodataTol0: Vec4;
	nodataTol1: Vec4;
	/** MASK_KIND value; 0 disables mask reads entirely */
	maskKind: number;
	/** user toggle for the quality mask (nodata bits always apply) */
	maskEnabled: boolean;
	/** bits (class ids or QA bit positions) masked when maskEnabled */
	maskBits: number;
	/** bits that always mask (fill / no-data class) */
	nodataBits: number;
	/** up to 8 formula constants, packed */
	k0: Vec4;
	k1: Vec4;
}

/** Loose prop map luma.gl's ShaderModule typing accepts; runtime values come from SpectralIndexProps. */
export type IndexModuleProps = Record<string, number | boolean | readonly number[] | Texture>;

export interface CompiledIndexModule {
	module: ShaderModule<IndexModuleProps>;
	plan: SlotPlan;
	/** constant name → default value, in k-slot order */
	constants: { name: string; value: number }[];
	glslExpression: string;
}

const MAX_CONSTANTS = 8;
const COMPS = ['x', 'y', 'z', 'w'];
const cache = new Map<string, CompiledIndexModule>();

const vec4Field = (base: string, slot: number) => `${base}${Math.floor(slot / 4)}.${COMPS[slot % 4]}`;

/**
 * Build the shader module for one spectral index. It runs after the BandStack
 * module (which binds the band and mask textures) and writes the colormapped,
 * normalized index into `color`. Invalid pixels (nodata, masked class, NaN/∞,
 * division by zero, negative base with fractional exponent) become fully
 * transparent.
 *
 * Each index gets its own module *name* because the mesh layer only recompiles
 * when a module name in the pipeline changes.
 */
export function compileIndexModule(def: IndexDef, opts: PlanOptions = {}): CompiledIndexModule {
	const wantMask = opts.wantMask ?? true;
	const key = `${def.platform}|${def.name}|${wantMask}`;
	const hit = cache.get(key);
	if (hit) return hit;

	const plan = planSlots(def, { ...opts, wantMask });
	if (!plan.gpuSupported) throw new Error(`Index ${def.name} is not GPU-renderable: ${plan.reason}`);

	const ast = parseFormula(def.formula);
	const vars = freeVariables(ast);
	const constants: { name: string; value: number }[] = [];
	const constantGlsl: Record<string, string> = {};
	const bandGlsl: Record<string, string> = {};
	for (const v of vars) {
		const slot = plan.paramSlot[v];
		if (slot !== undefined) {
			bandGlsl[v] = `b${slot}`;
			continue;
		}
		const value = def.constants[v];
		if (value === null || value === undefined) {
			throw new Error(`Index ${def.name}: "${v}" is neither a band nor a constant with a default`);
		}
		if (constants.length >= MAX_CONSTANTS) throw new Error(`Index ${def.name} needs > ${MAX_CONSTANTS} constants`);
		const i = constants.length;
		constantGlsl[v] = `M.${vec4Field('k', i)}`;
		constants.push({ name: v, value });
	}

	const safe = `${def.platform}_${def.name}`.replace(/[^A-Za-z0-9_]/g, '_');
	const NAME = `gbIndex_${safe}`;
	const P = `gb_${safe}`;
	const expr = toGlsl(ast, { bands: bandGlsl, constants: constantGlsl, helperPrefix: P });
	const nSlots = plan.slotAssets.length;
	const slotArgs = Array.from({ length: nSlots }, (_, i) => `float b${i}`).join(', ');

	const fs = /* glsl */ `
uniform ${NAME}Uniforms {
  float vmin;
  float vmax;
  float reversed;
  float maskEnabled;
  int maskKind;
  int maskBits;
  int nodataBits;
  int colormapIndex;
  vec4 scale0;
  vec4 scale1;
  vec4 offset0;
  vec4 offset1;
  vec4 nodata0;
  vec4 nodata1;
  vec4 nodataOn0;
  vec4 nodataOn1;
  vec4 nodataTol0;
  vec4 nodataTol1;
  vec4 k0;
  vec4 k1;
} ${NAME};
`;

	const decl = /* glsl */ `
precision highp sampler2DArray;
uniform sampler2DArray gbColormapTexture;
${glslHelpers(P)}
float ${P}_index(${slotArgs}, vec4 k0, vec4 k1) {
  return ${expr.replaceAll('M.', `${NAME}.`)};
}
`;

	// One sample per used slot: nodata test on the DN, then scale to a value.
	const perSlot = Array.from({ length: nSlots }, (_, i) => /* glsl */ `
  float dn${i} = ${BAND_STACK}_dn(${i}, uv);
  if (${NAME}.${vec4Field('nodataOn', i)} > 0.5 && abs(dn${i} - ${NAME}.${vec4Field('nodata', i)}) < ${NAME}.${vec4Field('nodataTol', i)}) bad = true;
  float b${i} = dn${i} * ${NAME}.${vec4Field('scale', i)} + ${NAME}.${vec4Field('offset', i)};`).join('');

	const bArgs = Array.from({ length: nSlots }, (_, i) => `b${i}`).join(', ');

	const filterColor = /* glsl */ `
{
  vec2 uv = geometry.uv;
  bool bad = false;${perSlot}
  if (${NAME}.maskKind > 0) {
    int cls = int(floor(${BAND_STACK}_maskDn(uv) + 0.5));
    // Class rasters (SCL) become a one-hot bit; bit-field rasters (QA_PIXEL) are used as-is.
    int bits = ${NAME}.maskKind == 1 ? (cls < 31 ? (1 << cls) : 0) : cls;
    if ((bits & ${NAME}.nodataBits) != 0) bad = true;
    if (${NAME}.maskEnabled > 0.5 && (bits & ${NAME}.maskBits) != 0) bad = true;
  }
  ${P}_bad = false;
  float v = ${P}_index(${bArgs}, ${NAME}.k0, ${NAME}.k1);
  if (${P}_bad || isnan(v) || isinf(v)) bad = true;
  float t = clamp((v - ${NAME}.vmin) / (${NAME}.vmax - ${NAME}.vmin), 0.0, 1.0);
  t = mix(t, 1.0 - t, ${NAME}.reversed);
  vec4 rgba = texture(gbColormapTexture, vec3(t, 0.5, float(${NAME}.colormapIndex)));
  color = bad ? vec4(0.0) : vec4(rgba.rgb, 1.0);
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
			vmin: 'f32',
			vmax: 'f32',
			reversed: 'f32',
			maskEnabled: 'f32',
			maskKind: 'i32',
			maskBits: 'i32',
			nodataBits: 'i32',
			colormapIndex: 'i32',
			scale0: 'vec4<f32>',
			scale1: 'vec4<f32>',
			offset0: 'vec4<f32>',
			offset1: 'vec4<f32>',
			nodata0: 'vec4<f32>',
			nodata1: 'vec4<f32>',
			nodataOn0: 'vec4<f32>',
			nodataOn1: 'vec4<f32>',
			nodataTol0: 'vec4<f32>',
			nodataTol1: 'vec4<f32>',
			k0: 'vec4<f32>',
			k1: 'vec4<f32>'
		},
		getUniforms: (props?: Partial<SpectralIndexProps>) => {
			const p = (props ?? {}) as Partial<SpectralIndexProps>;
			return {
				gbColormapTexture: p.colormapTexture,
				vmin: p.vmin ?? -1,
				vmax: p.vmax ?? 1,
				reversed: p.reversed ? 1 : 0,
				maskEnabled: p.maskEnabled === false ? 0 : 1,
				maskKind: p.maskKind ?? 0,
				maskBits: p.maskBits ?? 0,
				nodataBits: p.nodataBits ?? 0,
				colormapIndex: p.colormapIndex ?? 0,
				scale0: p.scale0 ?? ONE,
				scale1: p.scale1 ?? ONE,
				offset0: p.offset0 ?? ZERO,
				offset1: p.offset1 ?? ZERO,
				nodata0: p.nodata0 ?? ZERO,
				nodata1: p.nodata1 ?? ZERO,
				nodataOn0: p.nodataOn0 ?? ZERO,
				nodataOn1: p.nodataOn1 ?? ZERO,
				nodataTol0: p.nodataTol0 ?? HALF,
				nodataTol1: p.nodataTol1 ?? HALF,
				k0: p.k0 ?? ZERO,
				k1: p.k1 ?? ZERO
			};
		}
	} as unknown as ShaderModule<IndexModuleProps>;

	const compiled = { module, plan, constants, glslExpression: expr };
	cache.set(key, compiled);
	return compiled;
}

/** Pack the constant defaults (optionally overridden) into the k0/k1 uniforms. */
export function packConstants(
	compiled: CompiledIndexModule,
	overrides: Readonly<Record<string, number>> = {}
): { k0: Vec4; k1: Vec4 } {
	const k = [0, 0, 0, 0, 0, 0, 0, 0];
	compiled.constants.forEach((c, i) => (k[i] = overrides[c.name] ?? c.value));
	return { k0: [k[0], k[1], k[2], k[3]], k1: [k[4], k[5], k[6], k[7]] };
}

/** Split an 8-long list into the two vec4 uniforms the module expects. */
export function packSlots(values: readonly number[], fill = 0): [Vec4, Vec4] {
	const v = Array.from({ length: 8 }, (_, i) => values[i] ?? fill);
	return [
		[v[0], v[1], v[2], v[3]],
		[v[4], v[5], v[6], v[7]]
	];
}
