import type { ShaderModule } from '@luma.gl/shadertools';
import type { Texture } from '@luma.gl/core';

/** Band textures the stack binds; the mask has its own, ninth texture. */
export const MAX_BAND_SLOTS = 8;
export const MODULE_NAME = 'gbBandStack';

type Vec4 = [number, number, number, number];

/** What MultiCOGLayer knows about one fetched band tile (mirrors deck.gl-geotiff's BandTileData). */
export interface BandTile {
	texture: Texture;
	uvTransform: Vec4;
	/**
	 * Texture-normalised sample × dnMax = digital number. Set by the fetch
	 * override that quantises float32 rasters; otherwise inferred from the
	 * texture format (r8unorm → 255, r16unorm → 65535).
	 */
	dnMax?: number;
}

export interface BandStackProps {
	bands: Texture[];
	uvTransforms: Vec4[];
	dnMax: number[];
	mask: Texture | null;
	maskUvTransform: Vec4;
	maskDnMax: number;
}

const SLOTS = Array.from({ length: MAX_BAND_SLOTS }, (_, i) => i);
const IDENTITY: Vec4 = [0, 0, 1, 1];

/** DN range implied by a luma.gl texture format. */
export function dnMaxForFormat(format: string | undefined): number {
	if (!format) return 65535;
	if (format.startsWith('r8')) return 255;
	if (format.startsWith('r16')) return 65535;
	if (format.includes('float')) return 1;
	return 65535;
}

/**
 * Replaces deck.gl-raster's 4-slot `CompositeBands`: binds up to eight band
 * textures plus a dedicated mask texture, each with its own UV transform, and
 * exposes them to downstream modules as digital numbers:
 *
 *   float gbBandStack_dn(int slot, vec2 uv)   linear-filtered band DN
 *   float gbBandStack_maskDn(vec2 uv)          nearest-texel mask DN
 *
 * Nothing is written to `color` here; the spectral-index module does that.
 * Texture units: 8 + 1 here, +1 for the colormap LUT = 10 of WebGL2's 16.
 */
export const BandStack: ShaderModule<Record<string, unknown>> = {
	name: MODULE_NAME,
	inject: {
		'fs:#decl': /* glsl */ `
${SLOTS.map((i) => `uniform sampler2D ${MODULE_NAME}_band${i};`).join('\n')}
uniform sampler2D ${MODULE_NAME}_mask;

vec2 ${MODULE_NAME}_applyUv(vec2 uv, vec4 xf) { return uv * xf.zw + xf.xy; }

vec4 ${MODULE_NAME}_xf(int slot) {
${SLOTS.map((i) => `  if (slot == ${i}) return ${MODULE_NAME}.uvTransform${i};`).join('\n')}
  return vec4(0.0, 0.0, 1.0, 1.0);
}
float ${MODULE_NAME}_dnMax(int slot) {
  vec4 a = ${MODULE_NAME}.dnMax0;
  vec4 b = ${MODULE_NAME}.dnMax1;
  if (slot == 0) return a.x; if (slot == 1) return a.y; if (slot == 2) return a.z; if (slot == 3) return a.w;
  if (slot == 4) return b.x; if (slot == 5) return b.y; if (slot == 6) return b.z; return b.w;
}
float ${MODULE_NAME}_sample(int slot, vec2 uv) {
  vec2 t = ${MODULE_NAME}_applyUv(uv, ${MODULE_NAME}_xf(slot));
${SLOTS.map((i) => `  if (slot == ${i}) return texture(${MODULE_NAME}_band${i}, t).r;`).join('\n')}
  return 0.0;
}
/** Digital number of band \`slot\` at \`uv\` (linear filtering). */
float ${MODULE_NAME}_dn(int slot, vec2 uv) {
  return ${MODULE_NAME}_sample(slot, uv) * ${MODULE_NAME}_dnMax(slot);
}
// Categorical / bit-field masks must not be interpolated across texels:
// read the texel directly so 20 m class blocks stay crisp.
float ${MODULE_NAME}_maskDn(vec2 uv) {
  vec2 t = ${MODULE_NAME}_applyUv(uv, ${MODULE_NAME}.maskUvTransform);
  ivec2 sz = textureSize(${MODULE_NAME}_mask, 0);
  ivec2 ij = clamp(ivec2(floor(t * vec2(sz))), ivec2(0), sz - ivec2(1));
  return texelFetch(${MODULE_NAME}_mask, ij, 0).r * ${MODULE_NAME}.maskDnMax;
}
`
	},
	fs: /* glsl */ `
uniform ${MODULE_NAME}Uniforms {
${SLOTS.map((i) => `  vec4 uvTransform${i};`).join('\n')}
  vec4 maskUvTransform;
  vec4 dnMax0;
  vec4 dnMax1;
  float maskDnMax;
  float maskPresent;
} ${MODULE_NAME};
`,
	uniformTypes: {
		...Object.fromEntries(SLOTS.map((i) => [`uvTransform${i}`, 'vec4<f32>'])),
		maskUvTransform: 'vec4<f32>',
		dnMax0: 'vec4<f32>',
		dnMax1: 'vec4<f32>',
		maskDnMax: 'f32',
		maskPresent: 'f32'
	} as Record<string, 'vec4<f32>' | 'f32'>,
	getUniforms: (props?: Record<string, unknown>) => {
		const p = (props ?? {}) as Partial<BandStackProps>;
		const bands = p.bands ?? [];
		const first = bands[0];
		const out: Record<string, unknown> = {};
		for (const i of SLOTS) {
			// WebGL needs every declared sampler bound; reuse the first texture.
			out[`${MODULE_NAME}_band${i}`] = bands[i] ?? first;
			out[`uvTransform${i}`] = p.uvTransforms?.[i] ?? IDENTITY;
		}
		out[`${MODULE_NAME}_mask`] = p.mask ?? first;
		out.maskUvTransform = p.maskUvTransform ?? IDENTITY;
		const d = p.dnMax ?? [];
		out.dnMax0 = [d[0] ?? 65535, d[1] ?? 65535, d[2] ?? 65535, d[3] ?? 65535];
		out.dnMax1 = [d[4] ?? 65535, d[5] ?? 65535, d[6] ?? 65535, d[7] ?? 65535];
		out.maskDnMax = p.maskDnMax ?? 255;
		out.maskPresent = p.mask ? 1 : 0;
		return out;
	}
};

/**
 * Bind fetched band tiles to stack slots in `slotAssets` order, plus the mask.
 * Throws when a required asset is missing (callers check first and return
 * null for the transient "sources switching" state).
 */
export function buildBandStackProps(
	slotAssets: readonly string[],
	maskAsset: string | null,
	tiles: ReadonlyMap<string, BandTile>
): BandStackProps {
	if (slotAssets.length === 0) throw new Error('At least one band is required');
	if (slotAssets.length > MAX_BAND_SLOTS) {
		throw new Error(`BandStack binds at most ${MAX_BAND_SLOTS} band textures (got ${slotAssets.length})`);
	}
	const bands: Texture[] = [];
	const uvTransforms: Vec4[] = [];
	const dnMax: number[] = [];
	for (const asset of slotAssets) {
		const t = tiles.get(asset);
		if (!t) throw new Error(`Band "${asset}" not found in fetched tiles`);
		bands.push(t.texture);
		uvTransforms.push(t.uvTransform);
		dnMax.push(t.dnMax ?? dnMaxForFormat(t.texture.format));
	}
	let mask: Texture | null = null;
	let maskUvTransform: Vec4 = IDENTITY;
	let maskDnMax = 255;
	if (maskAsset) {
		const t = tiles.get(maskAsset);
		if (!t) throw new Error(`Mask "${maskAsset}" not found in fetched tiles`);
		mask = t.texture;
		maskUvTransform = t.uvTransform;
		maskDnMax = t.dnMax ?? dnMaxForFormat(t.texture.format);
	}
	return { bands, uvTransforms, dnMax, mask, maskUvTransform, maskDnMax };
}
