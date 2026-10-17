import { MultiCOGLayer, type MultiCOGLayerProps } from '@developmentseed/deck.gl-geotiff';
import { resolveSecondaryTiles, selectSecondaryLevel } from '@developmentseed/deck.gl-raster';
import { assembleTiles } from '@developmentseed/geotiff';
import type { Device, Texture } from '@luma.gl/core';
import { BandStack, buildBandStackProps, type BandTile } from './bandStack';

/** How float32 rasters (Sentinel-1 gamma0) are packed into 16-bit textures. */
export interface FloatQuant {
	/** physical value mapped to 65535; larger values clamp */
	ceiling: number;
	/** raster nodata value, mapped to DN 0 (NaN always is) */
	nodata: number | null;
}

export type BandStackCOGLayerProps = MultiCOGLayerProps & {
	/** slot index → source name (asset key); at most MAX_BAND_SLOTS */
	slotAssets: string[];
	/** source name of the mask band, bound to the dedicated mask texture */
	maskAsset: string | null;
	/** required when the sources are float32 COGs; null for integer DNs */
	floatQuant: FloatQuant | null;
};

/** Shape of the per-tile data MultiCOGLayer hands to `_buildRenderResult`. */
interface TileData {
	bands: Map<string, BandTile>;
}

interface RenderResult {
	renderPipeline: { module: unknown; props: unknown }[];
}

/** Single-band raster as decoded by @developmentseed/geotiff. */
interface PixelInterleavedArray {
	layout: 'pixel-interleaved';
	data: ArrayBufferView & { length: number; byteLength: number };
	width: number;
	height: number;
}
interface BandSeparateArray {
	layout: 'band-separate';
	bands: { byteLength: number }[];
	width: number;
	height: number;
}
type RasterArray = PixelInterleavedArray | BandSeparateArray;

type Vec4 = [number, number, number, number];

/**
 * Quantize a float raster into uint16 DNs: 0 = nodata, valid values occupy
 * 1..65535 linearly up to `ceiling` (clamped). The shader recovers the value
 * as `sample × ceiling`, so `dnMax` on the tile is the ceiling.
 */
export function quantizeFloat32(data: ArrayLike<number>, q: FloatQuant): Uint16Array {
	const out = new Uint16Array(data.length);
	const scale = 65535 / q.ceiling;
	for (let i = 0; i < data.length; i++) {
		const v = data[i];
		if (Number.isNaN(v) || v === q.nodata) continue;
		out[i] = Math.max(1, Math.min(65535, Math.round(v * scale)));
	}
	return out;
}

/** Create the band texture, quantising float data when `quant` is given. */
function createBandTexture(device: Device, array: RasterArray, quant: FloatQuant | null): { texture: Texture; dnMax?: number } {
	if (array.layout !== 'pixel-interleaved') {
		throw new Error('Band-separate layout not yet supported in BandStackCOGLayer');
	}
	const { width, height } = array;
	let data = array.data;
	let format: 'r8unorm' | 'r16unorm';
	let dnMax: number | undefined;
	if (data instanceof Float32Array || data instanceof Float64Array) {
		if (!quant) throw new Error('float32 raster needs a platform valueCeiling to render on the GPU');
		data = quantizeFloat32(data, quant);
		format = 'r16unorm';
		dnMax = quant.ceiling;
	} else if (data instanceof Uint8Array || data instanceof Uint8ClampedArray) {
		format = 'r8unorm';
	} else if (data instanceof Uint16Array) {
		format = 'r16unorm';
	} else {
		throw new Error(`Unsupported typed array type: ${(data as object).constructor.name}`);
	}
	const texture = device.createTexture({
		data: data as Uint8Array | Uint16Array,
		format,
		width,
		height,
		sampler: { minFilter: 'linear', magFilter: 'linear' }
	} as unknown as Parameters<Device['createTexture']>[0]);
	return { texture, dnMax };
}

function byteLengthOf(arr: RasterArray): number {
	return arr.layout === 'pixel-interleaved' ? arr.data.byteLength : arr.bands.reduce((s, b) => s + b.byteLength, 0);
}

interface FetchableImage {
	fetchTile(x: number, y: number, opts: { boundless: boolean; pool: unknown; signal?: AbortSignal }): Promise<{ array: RasterArray }>;
	fetchTiles(xy: [number, number][], opts: { boundless: boolean; pool: unknown; signal?: AbortSignal }): Promise<unknown[]>;
}

/** z=0 is the coarsest overview, z=max is full resolution (mirrors upstream). */
function selectImage(geotiff: { overviews: unknown[] }, z: number): FetchableImage {
	const images = [geotiff, ...geotiff.overviews] as FetchableImage[];
	return images[images.length - 1 - z];
}

/**
 * MultiCOGLayer whose per-tile pipeline starts with our 8-slot BandStack
 * instead of deck.gl-raster's 4-slot CompositeBands, and whose tile fetch can
 * quantize float32 rasters (upstream only accepts uint8/uint16). Tiling and
 * per-band UV alignment are inherited unchanged. `composite` is ignored.
 */
export class BandStackCOGLayer extends MultiCOGLayer {
	static layerName = 'BandStackCOGLayer';
	// Same casting trick as upstream: deck.gl's DefaultProps shape diverges from
	// the props type once extra keys are added.
	static defaultProps = {
		...(MultiCOGLayer as unknown as { defaultProps: Record<string, unknown> }).defaultProps,
		slotAssets: { type: 'array', value: [], compare: true },
		maskAsset: null,
		floatQuant: { type: 'object', value: null, compare: true }
	} as unknown as typeof MultiCOGLayer.defaultProps;
}

function buildRenderResult(this: BandStackCOGLayer, data: TileData): RenderResult | null {
	const { bands } = data;
	const { slotAssets, maskAsset, renderPipeline } = this.props as unknown as BandStackCOGLayerProps;
	if (slotAssets.some((a) => !bands.has(a)) || (maskAsset && !bands.has(maskAsset))) {
		if (import.meta.env.DEV) console.debug('[BandStackCOGLayer] tile missing bands', { have: [...bands.keys()], slotAssets, maskAsset });
		return null;
	}
	const props = buildBandStackProps(slotAssets, maskAsset, bands);
	return { renderPipeline: [{ module: BandStack, props }, ...((renderPipeline ?? []) as RenderResult['renderPipeline'])] };
}

interface PrimaryOpts {
	x: number;
	y: number;
	z: number;
	pool: unknown;
	signal?: AbortSignal;
	device: Device;
}

type FetchedBand = [string, BandTile & { width: number; height: number; byteLength: number }, null];

async function fetchPrimaryBand(
	this: BandStackCOGLayer,
	name: string,
	sourceState: { geotiff: { overviews: unknown[] } },
	opts: PrimaryOpts
): Promise<FetchedBand> {
	const { x, y, z, pool, signal, device } = opts;
	const { floatQuant } = this.props as unknown as BandStackCOGLayerProps;
	const image = selectImage(sourceState.geotiff, z);
	const tile = await image.fetchTile(x, y, { boundless: true, pool, signal });
	const arr = tile.array;
	const { texture, dnMax } = createBandTexture(device, arr, floatQuant);
	return [name, { texture, uvTransform: [0, 0, 1, 1], width: arr.width, height: arr.height, byteLength: byteLengthOf(arr), dnMax }, null];
}

interface SecondaryLevel {
	tileWidth: number;
	tileHeight: number;
	metersPerPixel: number;
}
interface SecondaryOpts {
	descriptor: { levels: SecondaryLevel[] };
	primaryLevel: unknown;
	primaryCol: number;
	primaryRow: number;
	primaryZ: number;
	pool: unknown;
	signal?: AbortSignal;
	device: Device;
	debug: boolean;
}

async function fetchSecondaryBand(
	this: BandStackCOGLayer,
	name: string,
	sourceState: { geotiff: { overviews: unknown[] } },
	opts: SecondaryOpts
): Promise<FetchedBand> {
	const { descriptor, primaryLevel, primaryCol, primaryRow, primaryZ, pool, signal, device } = opts;
	const { floatQuant } = this.props as unknown as BandStackCOGLayerProps;
	const state = (this as unknown as { state: { multiDescriptor: { primary: { levels: SecondaryLevel[] } } } }).state;
	const primaryMpp = state.multiDescriptor.primary.levels[primaryZ].metersPerPixel;
	const secondaryLevel = selectSecondaryLevel(descriptor.levels as never, primaryMpp) as unknown as SecondaryLevel;
	const secondaryZ = descriptor.levels.indexOf(secondaryLevel);
	const resolution = resolveSecondaryTiles(primaryLevel as never, primaryCol, primaryRow, secondaryLevel as never, secondaryZ) as unknown as {
		tileIndices: { x: number; y: number }[];
		uvTransform: Vec4;
		stitchedWidth: number;
		stitchedHeight: number;
		minCol: number;
		minRow: number;
	};
	const image = selectImage(sourceState.geotiff, secondaryZ);
	const xy = resolution.tileIndices.map((idx) => [idx.x, idx.y] as [number, number]);
	const tiles = await image.fetchTiles(xy, { boundless: true, pool, signal });
	const assembled = assembleTiles(tiles as never, {
		width: resolution.stitchedWidth,
		height: resolution.stitchedHeight,
		tileWidth: secondaryLevel.tileWidth,
		tileHeight: secondaryLevel.tileHeight,
		minCol: resolution.minCol,
		minRow: resolution.minRow
	} as never) as unknown as RasterArray;
	const { texture, dnMax } = createBandTexture(device, assembled, floatQuant);
	return [
		name,
		{ texture, uvTransform: resolution.uvTransform, width: assembled.width, height: assembled.height, byteLength: byteLengthOf(assembled), dnMax },
		null
	];
}

// These three are declared `private` in the upstream .d.ts, so class-member
// overrides are type errors; at runtime they are ordinary prototype methods
// dispatched through `this`, so replacing them on the prototype is exactly
// equivalent to a subclass override.
Object.assign(BandStackCOGLayer.prototype as unknown as Record<string, unknown>, {
	_buildRenderResult: buildRenderResult,
	_fetchPrimaryBand: fetchPrimaryBand,
	_fetchSecondaryBand: fetchSecondaryBand
});
