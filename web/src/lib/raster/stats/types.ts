import type { FrameModel } from '$lib/geo/frame';
import type { MaskKind } from '$lib/data/types';
import type { Summary, Histogram } from './percentiles';

/** Quality-mask semantics, mirroring the GPU module (see buildLayer.maskUniforms). */
export interface MaskRequest {
	/** asset key of the mask raster (must be present in `hrefs`) */
	asset: string;
	kind: MaskKind;
	/** bits (one-hot class ids for `scl-classes`, QA bit positions for `qa-bits`) masked when enabled */
	bits: number;
	/** bits that always mask (no-data class / fill bit) */
	nodataBits: number;
}

export interface StatsRequest {
	kind?: 'index';
	requestId: number;
	/** asset key → COG href for every band the index needs (+ the mask asset when masking) */
	hrefs: Record<string, string>;
	/** spyndex param → asset key */
	bands: Record<string, string>;
	formula: string;
	constants: Record<string, number>;
	frame: FrameModel;
	aspect: number;
	epsg: number;
	/** asset key → [scale, offset]: value = DN * scale + offset */
	valueScale: Record<string, [number, number]>;
	/** asset key → nodata DN (null: only NaN counts as nodata) */
	nodata: Record<string, number | null>;
	mask: MaskRequest | null;
	maskEnabled: boolean;
	/** target sampling resolution in meters (Python review uses 80) */
	resolutionM: number;
	/** the index's canonical range; the histogram stays inside it (see histogramWindow) */
	domain: [number, number] | null;
}

export interface StatsResult {
	requestId: number;
	resolutionM: number;
	nPixels: number;
	validFraction: number;
	nodataFraction: number;
	maskedFraction: number;
	summary: Summary;
	histogram: Histogram;
	/** wall time in ms, for the diagnostics readout */
	elapsedMs: number;
}

/** Percentile levels the RGB stats report; covers every auto-stretch choice. */
export const RGB_PERCENTILES = [0.5, 1, 2, 5, 50, 95, 98, 99, 99.5] as const;
export const RGB_HISTOGRAM_BINS = 128;

/** Per-channel reflectance statistics for a true-color frame. */
export interface RgbStatsRequest extends Omit<StatsRequest, 'kind' | 'bands' | 'formula' | 'constants' | 'domain'> {
	kind: 'rgb';
	/** red, green, blue asset keys (each present in `hrefs`) */
	rgbAssets: [string, string, string];
}

export interface RgbChannelStats {
	/** aligned with `qs` */
	percentiles: number[];
	mean: number;
	counts: number[];
}

export interface RgbStatsResult {
	requestId: number;
	resolutionM: number;
	nPixels: number;
	validFraction: number;
	nodataFraction: number;
	maskedFraction: number;
	qs: number[];
	/** shared histogram window for the three channels, in reflectance */
	edges: [number, number];
	channels: [RgbChannelStats, RgbChannelStats, RgbChannelStats];
	elapsedMs: number;
}

export type AnyStatsRequest = StatsRequest | RgbStatsRequest;
export type AnyStatsResult = StatsResult | RgbStatsResult;

export type StatsResponse = { ok: true; result: AnyStatsResult } | { ok: false; requestId: number; error: string };
