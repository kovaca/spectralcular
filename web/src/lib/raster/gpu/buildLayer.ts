import type { Texture } from '@luma.gl/core';
import type { MultiCOGLayerProps } from '@developmentseed/deck.gl-geotiff';
import type { IndexDef, PlatformDef, TrueColorSpec } from '$lib/data/types';
import { nodataFor, platformForIndex, valueScaleFor } from '$lib/data';
import { BandStackCOGLayer, type BandStackCOGLayerProps, type FloatQuant } from './BandStackCOGLayer';
import { MASK_KIND, compileIndexModule, packConstants, packSlots } from './spectralIndexModule';
import { NODATA_BITS, bitmask } from '$lib/raster/stats/mask';
import { compileTrueColorModule } from './trueColorModule';
import { windowOf } from '../truecolor';

export interface IndexLayerOptions {
	id?: string;
	/** unique per scene, used in the layer id */
	sceneId: string;
	/** asset key → COG href (from the STAC item or a saved plate) */
	hrefs: Record<string, string>;
	index: IndexDef;
	/** defaults to the index's platform */
	platform?: PlatformDef;
	/** MapLibre layer id to render beneath (labels on top), interleaved mode only */
	beforeId?: string;
	colormapTexture: Texture;
	colormapRow: number;
	reversed?: boolean;
	vmin: number;
	vmax: number;
	/** apply the platform's quality mask (clouds, shadow, snow…); nodata is always masked */
	maskEnabled?: boolean;
	/** extra additive offset applied to every band's physical value (diagnostics; 0 = Python parity) */
	valueOffset?: number;
	constants?: Record<string, number>;
	opacity?: number;
	onLoad?: () => void;
}

/** Float32 platforms are packed into 16-bit textures up to `valueCeiling`; integer platforms pass through. */
export function floatQuantFor(platform: PlatformDef, asset: string): FloatQuant | null {
	if (platform.dtype !== 'float32') return null;
	return { ceiling: platform.valueCeiling ?? 1, nodata: nodataFor(platform, asset) };
}

/** Mask uniforms for a platform; `maskKind` 0 means the shader never reads the mask texture. */
export function maskUniforms(platform: PlatformDef, bound: boolean): { maskKind: number; maskBits: number; nodataBits: number } {
	if (!bound || !platform.mask) return { maskKind: MASK_KIND.none, maskBits: 0, nodataBits: 0 };
	return { maskKind: MASK_KIND[platform.mask.kind], maskBits: bitmask(platform.mask.values), nodataBits: NODATA_BITS };
}

type Sources = Record<string, { url: string }>;
const sourcesMemo = new Map<string, { key: string; sources: Sources }>();

/**
 * MultiCOGLayer compares `sources` by reference and, on any change, drops its
 * tile cache and reopens every COG. Hand back the previous object for the same
 * layer id while the URLs are unchanged, so colormap/stretch edits only redraw.
 */
function stableSources(layerId: string, keys: readonly string[], hrefs: Record<string, string>): Sources {
	const key = keys.map((k) => `${k}=${hrefs[k]}`).join('\n');
	const hit = sourcesMemo.get(layerId);
	if (hit?.key === key) return hit.sources;
	const sources = Object.fromEntries(keys.map((k) => [k, { url: hrefs[k] }]));
	sourcesMemo.set(layerId, { key, sources });
	return sources;
}

type Vec4 = [number, number, number, number];

/**
 * Per-slot DN → physical value and nodata uniforms, packed for up to 8 slots
 * (slots 0-3 in `[0]`, 4-7 in `[1]`). Shared by the index and true-color modules.
 */
export function slotUniforms(platform: PlatformDef, slotAssets: readonly string[], valueOffset = 0) {
	const scales = slotAssets.map((a) => valueScaleFor(platform, a));
	const nodata = slotAssets.map((a) => nodataFor(platform, a));
	const floatQuant = floatQuantFor(platform, slotAssets[0]);
	return {
		floatQuant,
		scale: packSlots(scales.map((s) => s[0]), 1),
		offset: packSlots(scales.map((s) => s[1] + valueOffset)),
		// Packed floats: DN 0 is nodata by construction, tolerance = half a quantisation step.
		nodata: packSlots(floatQuant ? nodata.map(() => 0) : nodata.map((n) => n ?? 0)),
		nodataOn: packSlots(floatQuant ? nodata.map(() => 1) : nodata.map((n) => (n === null ? 0 : 1))),
		nodataTol: packSlots(nodata.map(() => (floatQuant ? (0.5 * floatQuant.ceiling) / 65535 : 0.5)), 0.5)
	} satisfies { floatQuant: FloatQuant | null } & Record<string, unknown>;
}

function requireSources(sceneId: string, sources: readonly string[], hrefs: Record<string, string>): void {
	for (const k of sources) {
		if (!hrefs[k]) throw new Error(`Scene ${sceneId} has no "${k}" asset`);
	}
}

const onTileError = (e: unknown) => console.error('[PlateLayer] tile error', e);

/**
 * A COG layer that streams the index's band COGs straight from the STAC item
 * and renders the colormapped index on the GPU.
 */
export function buildIndexLayer(o: IndexLayerOptions): BandStackCOGLayer {
	const platform = o.platform ?? platformForIndex(o.index);
	const compiled = compileIndexModule(o.index, { wantMask: o.maskEnabled !== false, platform });
	const { plan } = compiled;
	requireSources(o.sceneId, plan.sources, o.hrefs);
	const id = o.id ?? `index-${o.sceneId}-${o.index.name}`;
	const sources = stableSources(id, plan.sources, o.hrefs);
	const { k0, k1 } = packConstants(compiled, o.constants);
	const u = slotUniforms(platform, plan.slotAssets, o.valueOffset ?? 0);
	const floatQuant = u.floatQuant;
	const [scale0, scale1] = u.scale;
	const [offset0, offset1] = u.offset;
	const [nodata0, nodata1] = u.nodata;
	const [nodataOn0, nodataOn1] = u.nodataOn;
	const [nodataTol0, nodataTol1] = u.nodataTol;

	const props: BandStackCOGLayerProps & { beforeId?: string } = {
		id,
		beforeId: o.beforeId,
		sources,
		slotAssets: plan.slotAssets,
		maskAsset: plan.maskAsset,
		floatQuant,
		opacity: o.opacity ?? 1,
		renderPipeline: [
			{
				module: compiled.module,
				props: {
					colormapTexture: o.colormapTexture,
					colormapIndex: o.colormapRow,
					reversed: o.reversed ?? false,
					vmin: o.vmin,
					vmax: o.vmax,
					scale0,
					scale1,
					offset0,
					offset1,
					nodata0,
					nodata1,
					nodataOn0,
					nodataOn1,
					nodataTol0,
					nodataTol1,
					maskEnabled: o.maskEnabled !== false,
					...maskUniforms(platform, plan.maskAsset !== null),
					k0,
					k1
				}
			}
		],
		// Cached tiles keep their sublayers unless this changes; list every uniform
		// not already pinned by the module or `sources` identity.
		updateTriggers: {
			renderTile: [o.colormapRow, o.reversed ?? false, o.vmin, o.vmax, o.maskEnabled !== false, o.valueOffset ?? 0, ...k0, ...k1]
		},
		onGeoTIFFLoad: o.onLoad,
		// Surface per-tile fetch/decode failures, which deck's TileLayer otherwise swallows.
		onTileError
	} as BandStackCOGLayerProps & { beforeId?: string; onTileError: (e: unknown) => void };
	return new BandStackCOGLayer(props as unknown as MultiCOGLayerProps);
}

export interface TrueColorLayerOptions {
	id?: string;
	sceneId: string;
	/** asset key → COG href */
	hrefs: Record<string, string>;
	platform: PlatformDef;
	beforeId?: string;
	/** the tone; an unresolved auto stretch draws with the provisional window */
	tone: TrueColorSpec;
	maskEnabled?: boolean;
	opacity?: number;
	onLoad?: () => void;
}

const vec3to4 = (v: readonly number[]): Vec4 => [v[0], v[1], v[2], 0];

/** A COG layer that renders the scene's red/green/blue bands through the true-color tone pipeline. */
export function buildTrueColorLayer(o: TrueColorLayerOptions): BandStackCOGLayer {
	const { platform } = o;
	const compiled = compileTrueColorModule(platform, { wantMask: o.maskEnabled !== false });
	const { plan } = compiled;
	requireSources(o.sceneId, plan.sources, o.hrefs);
	const id = o.id ?? `truecolor-${o.sceneId}`;
	const sources = stableSources(id, plan.sources, o.hrefs);
	const u = slotUniforms(platform, plan.slotAssets);
	const { lo, hi } = windowOf(o.tone.stretch);
	const t = o.tone;

	const props = {
		id,
		beforeId: o.beforeId,
		sources,
		slotAssets: plan.slotAssets,
		maskAsset: plan.maskAsset,
		floatQuant: u.floatQuant,
		opacity: o.opacity ?? 1,
		renderPipeline: [
			{
				module: compiled.module,
				props: {
					lo: vec3to4(lo),
					hi: vec3to4(hi),
					gamma: t.gamma,
					contrast: t.contrast,
					midpoint: t.midpoint,
					saturation: t.saturation,
					scale: u.scale[0],
					offset: u.offset[0],
					nodata: u.nodata[0],
					nodataOn: u.nodataOn[0],
					nodataTol: u.nodataTol[0],
					maskEnabled: o.maskEnabled !== false,
					...maskUniforms(platform, plan.maskAsset !== null)
				}
			}
		],
		updateTriggers: {
			renderTile: [...lo, ...hi, t.gamma, t.contrast, t.midpoint, t.saturation, o.maskEnabled !== false]
		},
		onGeoTIFFLoad: o.onLoad,
		onTileError
	} as BandStackCOGLayerProps & { beforeId?: string; onTileError: (e: unknown) => void };
	return new BandStackCOGLayer(props as unknown as MultiCOGLayerProps);
}
