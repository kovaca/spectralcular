import { fromUrl, type GeoTIFF, type GeoTIFFImage } from 'geotiff';

/** A band window read at (roughly) a target resolution, with its grid geometry. */
export interface BandGrid {
	data: Float64Array | Uint16Array | Uint8Array | Int16Array | Float32Array;
	width: number;
	height: number;
	/** CRS coordinates of the top-left corner of pixel (0,0) */
	originX: number;
	originY: number;
	/** pixel size in CRS units (positive) */
	resX: number;
	resY: number;
}

const tiffCache = new Map<string, Promise<GeoTIFF>>();

export function openTiff(href: string): Promise<GeoTIFF> {
	let p = tiffCache.get(href);
	if (!p) {
		p = fromUrl(href, { allowFullFile: false });
		tiffCache.set(href, p);
	}
	return p;
}

/** Pick the overview whose pixel size is the largest that is still ≤ target (else the coarsest). */
export async function pickLevel(tiff: GeoTIFF, targetResM: number): Promise<{ image: GeoTIFFImage; base: GeoTIFFImage; scale: number }> {
	const base = await tiff.getImage(0);
	const count = await tiff.getImageCount();
	const baseRes = Math.abs(base.getResolution()[0]);
	let best = base;
	let bestScale = 1;
	for (let i = 1; i < count; i++) {
		const img = await tiff.getImage(i);
		const scale = base.getWidth() / img.getWidth();
		if (baseRes * scale <= targetResM + 1e-6 && scale > bestScale) {
			best = img;
			bestScale = scale;
		}
	}
	return { image: best, base, scale: bestScale };
}

/**
 * Read the pixels of `href` covering the CRS bbox `[minX, minY, maxX, maxY]`
 * at the overview closest to `targetResM`.
 */
export async function readWindow(href: string, bbox: [number, number, number, number], targetResM: number, signal?: AbortSignal): Promise<BandGrid> {
	const tiff = await openTiff(href);
	const { image, base, scale } = await pickLevel(tiff, targetResM);
	const [ox, oy] = base.getOrigin();
	const [rx, ry] = base.getResolution();
	const resX = Math.abs(rx) * scale;
	const resY = Math.abs(ry) * scale;
	const w = image.getWidth();
	const h = image.getHeight();
	// y axis points down in pixel space; origin is the top-left corner.
	const x0 = clamp(Math.floor((bbox[0] - ox) / resX), 0, w);
	const x1 = clamp(Math.ceil((bbox[2] - ox) / resX), 0, w);
	const y0 = clamp(Math.floor((oy - bbox[3]) / resY), 0, h);
	const y1 = clamp(Math.ceil((oy - bbox[1]) / resY), 0, h);
	if (x1 <= x0 || y1 <= y0) {
		return { data: new Float32Array(0), width: 0, height: 0, originX: ox, originY: oy, resX, resY };
	}
	const rasters = await image.readRasters({ window: [x0, y0, x1, y1], samples: [0], signal });
	const data = (Array.isArray(rasters) ? rasters[0] : rasters) as BandGrid['data'];
	return {
		data,
		width: x1 - x0,
		height: y1 - y0,
		originX: ox + x0 * resX,
		originY: oy - y0 * resY,
		resX,
		resY
	};
}

function clamp(v: number, lo: number, hi: number): number {
	return Math.max(lo, Math.min(hi, v));
}

/** Nearest-neighbour sample of `g` at CRS coordinate (x, y); NaN when outside. */
export function sampleNearest(g: BandGrid, x: number, y: number): number {
	const c = Math.floor((x - g.originX) / g.resX);
	const r = Math.floor((g.originY - y) / g.resY);
	if (c < 0 || r < 0 || c >= g.width || r >= g.height) return NaN;
	return g.data[r * g.width + c];
}
