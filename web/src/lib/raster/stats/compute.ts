import { parseFormula } from '../formula/parser';
import { compileEvaluator } from '../formula/evaluate';
import { frameCorners, frameHeightM, lonLatToPage, type FrameModel } from '$lib/geo/frame';
import { projectorForEpsg } from '$lib/geo/utm';
import { readWindow, sampleNearest, type BandGrid } from './reader';
import { histogram, histogramWindow, percentileSorted, summarize } from './percentiles';
import {
	RGB_HISTOGRAM_BINS,
	RGB_PERCENTILES,
	type MaskRequest,
	type RgbChannelStats,
	type RgbStatsRequest,
	type RgbStatsResult,
	type StatsRequest,
	type StatsResult
} from './types';

interface SampleRequest {
	hrefs: Record<string, string>;
	/** column name → asset key */
	bands: Record<string, string>;
	frame: FrameModel;
	aspect: number;
	epsg: number;
	valueScale: Record<string, [number, number]>;
	nodata: Record<string, number | null>;
	mask: MaskRequest | null;
	maskEnabled: boolean;
	resolutionM: number;
}

interface FrameSamples {
	n: number;
	resolutionM: number;
	/** column name → physical value per grid cell */
	columns: Record<string, Float32Array>;
	inside: Uint8Array;
	nodata: Uint8Array;
	masked: Uint8Array;
	nInside: number;
}

/**
 * Read every band (and the mask) inside the (rotated) frame at `resolutionM`
 * on the first band's grid, other bands by nearest neighbor, and flag each
 * cell as inside / nodata / masked with the same rules as the shaders.
 */
async function sampleFrame(req: SampleRequest, signal?: AbortSignal): Promise<FrameSamples> {
	const proj = projectorForEpsg(req.epsg);

	// Envelope of the rotated frame in the scene's UTM CRS.
	const corners = frameCorners(req.frame, req.aspect).map((c) => proj.forward(c));
	const xs = corners.map((c) => c[0]);
	const ys = corners.map((c) => c[1]);
	const bbox: [number, number, number, number] = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];

	const assetKeys = [...new Set(Object.values(req.bands))];
	const mask = req.mask && req.hrefs[req.mask.asset] ? req.mask : null;
	const keys = mask ? [...assetKeys, mask.asset] : assetKeys;
	const grids = new Map<string, BandGrid>();
	await Promise.all(
		keys.map(async (k) => {
			const href = req.hrefs[k];
			if (!href) throw new Error(`missing href for asset ${k}`);
			grids.set(k, await readWindow(href, bbox, req.resolutionM, signal));
		})
	);

	// Reference grid = first band; sample the others by nearest neighbor.
	const ref = grids.get(assetKeys[0])!;
	const n = ref.width * ref.height;
	const columns: Record<string, Float32Array> = {};
	for (const param of Object.keys(req.bands)) columns[param] = new Float32Array(n);
	const inside = new Uint8Array(n);
	const nodata = new Uint8Array(n);
	const masked = new Uint8Array(n);

	const halfW = req.frame.widthM / 2;
	const halfH = frameHeightM(req.frame, req.aspect) / 2;
	const maskGrid = mask ? grids.get(mask.asset)! : null;
	const scaleOf: Record<string, [number, number]> = {};
	const nodataOf: Record<string, number | null> = {};
	for (const asset of assetKeys) {
		scaleOf[asset] = req.valueScale[asset] ?? req.valueScale['*'] ?? [1, 0];
		nodataOf[asset] = asset in req.nodata ? req.nodata[asset] : (req.nodata['*'] ?? null);
	}
	let nInside = 0;

	for (let r = 0; r < ref.height; r++) {
		const y = ref.originY - (r + 0.5) * ref.resY;
		for (let c = 0; c < ref.width; c++) {
			const i = r * ref.width + c;
			const x = ref.originX + (c + 0.5) * ref.resX;
			const lonlat = proj.inverse([x, y]);
			const [px, py] = lonLatToPage(req.frame, lonlat);
			if (Math.abs(px) > halfW || Math.abs(py) > halfH) continue;
			inside[i] = 1;
			nInside++;
			for (const [param, asset] of Object.entries(req.bands)) {
				const g = grids.get(asset)!;
				const dn = g === ref ? g.data[i] : sampleNearest(g, x, y);
				const nd = nodataOf[asset];
				if (Number.isNaN(dn) || (nd !== null && dn === nd)) nodata[i] = 1;
				const [scale, offset] = scaleOf[asset];
				columns[param][i] = dn * scale + offset;
			}
			if (maskGrid && mask) {
				const cls = sampleNearest(maskGrid, x, y);
				if (Number.isNaN(cls)) masked[i] = 1;
				else {
					// Same encoding as the shader: class rasters become a one-hot bit.
					const bits = mask.kind === 'scl-classes' ? (cls < 31 ? 1 << cls : 0) : cls;
					if ((bits & mask.nodataBits) !== 0) masked[i] = 1;
					else if (req.maskEnabled && (bits & mask.bits) !== 0) masked[i] = 1;
				}
			}
		}
	}
	return { n, resolutionM: ref.resX, columns, inside, nodata, masked, nInside };
}

/**
 * Compute index statistics for the pixels inside a (rotated) frame, sampling
 * every band at `resolutionM` on the first band's grid. Runs inside the
 * stats worker but has no worker-specific code so it is unit-testable.
 */
export async function computeFrameStats(req: StatsRequest, signal?: AbortSignal): Promise<StatsResult> {
	const t0 = performance.now();
	const s = await sampleFrame(req, signal);
	const ast = parseFormula(req.formula);
	const fn = compileEvaluator(ast, s.columns, req.constants);
	const values = new Float32Array(s.nInside);
	let nValid = 0;
	let nNodata = 0;
	let nMasked = 0;
	for (let i = 0; i < s.n; i++) {
		if (!s.inside[i]) continue;
		if (s.nodata[i]) {
			nNodata++;
			continue;
		}
		if (s.masked[i]) {
			nMasked++;
			continue;
		}
		const v = fn(i);
		if (Number.isFinite(v)) values[nValid++] = v;
	}
	const valid = values.subarray(0, nValid);
	const summary = summarize(valid);
	const [lo, hi] = histogramWindow(summary, req.domain);

	return {
		requestId: req.requestId,
		resolutionM: s.resolutionM,
		nPixels: s.nInside,
		validFraction: s.nInside ? nValid / s.nInside : 0,
		nodataFraction: s.nInside ? nNodata / s.nInside : 0,
		maskedFraction: s.nInside ? nMasked / s.nInside : 0,
		summary,
		histogram: histogram(valid, lo, hi, 64),
		elapsedMs: performance.now() - t0
	};
}

/**
 * Where to bin the three reflectance histograms: from 0 (or the darkest low
 * percentile, if negative) to a quarter past the brightest p99.5, so the
 * stretch markers have room on both sides.
 */
export function rgbHistogramWindow(lowest: number, highest: number): [number, number] {
	const lo = Math.max(-0.1, Math.min(0, lowest));
	const hi = Math.min(1.6, Math.max(0.05, highest * 1.25));
	return [lo, hi > lo ? hi : lo + 0.05];
}

/** Summarize one channel's valid reflectances (sorted in place). */
export function channelStats(sorted: Float64Array, qs: readonly number[], lo: number, hi: number, bins: number): RgbChannelStats {
	let sum = 0;
	for (let i = 0; i < sorted.length; i++) sum += sorted[i];
	return {
		percentiles: qs.map((q) => percentileSorted(sorted, q)),
		mean: sorted.length ? sum / sorted.length : NaN,
		counts: histogram(sorted, lo, hi, bins).counts
	};
}

/** Per-channel reflectance percentiles and histograms for a true-color frame. */
export async function computeRgbStats(req: RgbStatsRequest, signal?: AbortSignal): Promise<RgbStatsResult> {
	const t0 = performance.now();
	const [ra, ga, ba] = req.rgbAssets;
	const s = await sampleFrame({ ...req, bands: { R: ra, G: ga, B: ba } }, signal);
	const cols = [s.columns.R, s.columns.G, s.columns.B];
	const vals = cols.map(() => new Float64Array(s.nInside));
	let nValid = 0;
	let nNodata = 0;
	let nMasked = 0;
	for (let i = 0; i < s.n; i++) {
		if (!s.inside[i]) continue;
		if (s.nodata[i]) {
			nNodata++;
			continue;
		}
		if (s.masked[i]) {
			nMasked++;
			continue;
		}
		if (!(Number.isFinite(cols[0][i]) && Number.isFinite(cols[1][i]) && Number.isFinite(cols[2][i]))) continue;
		for (let c = 0; c < 3; c++) vals[c][nValid] = cols[c][i];
		nValid++;
	}
	const sorted = vals.map((v) => v.subarray(0, nValid).sort());
	const qs = [...RGB_PERCENTILES];
	const lowest = Math.min(...sorted.map((v) => percentileSorted(v, 0.5)));
	const highest = Math.max(...sorted.map((v) => percentileSorted(v, 99.5)));
	const [lo, hi] = nValid ? rgbHistogramWindow(lowest, highest) : [0, 0.5];
	const channels = sorted.map((v) => channelStats(v, qs, lo, hi, RGB_HISTOGRAM_BINS)) as RgbStatsResult['channels'];
	return {
		requestId: req.requestId,
		resolutionM: s.resolutionM,
		nPixels: s.nInside,
		validFraction: s.nInside ? nValid / s.nInside : 0,
		nodataFraction: s.nInside ? nNodata / s.nInside : 0,
		maskedFraction: s.nInside ? nMasked / s.nInside : 0,
		qs,
		edges: [lo, hi],
		channels,
		elapsedMs: performance.now() - t0
	};
}
