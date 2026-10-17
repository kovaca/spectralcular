import type { TrueColorPreset, TrueColorSpec, TrueColorStretch, Triple } from '$lib/data/types';

/**
 * The true-color tone pipeline, mirroring src/spectralcular/truecolor.py line
 * for line (checked against truecolor_fixtures.json). The GLSL module in
 * gpu/trueColorModule.ts mirrors this file in turn.
 *
 *   reflectance → linear stretch (lo..hi) → gamma → sigmoidal contrast → OKLab saturation
 */

export const SPEC_VERSION = 1;
/** Sigmoidal strengths below this are off (the curve degenerates at β = 0). */
export const CONTRAST_EPS = 1e-3;
/** Saturation within this of 1 skips the OKLab round trip. */
export const SATURATION_EPS = 1e-4;
export const AUTO_LOW_PERCENTILES = [0.5, 1, 2, 5] as const;
export const AUTO_HIGH_PERCENTILES = [95, 98, 99, 99.5] as const;
/** Window used to draw an auto stretch before the frame's statistics arrive (display only). */
export const PROVISIONAL_WINDOW: [number, number] = [0, 0.3];

export function linearStretch(x: number, lo: number, hi: number): number {
	return Math.min(1, Math.max(0, (x - lo) / (hi - lo)));
}

export function applyGamma(t: number, gamma: number): number {
	return Math.pow(t, 1 / gamma);
}

/** rio-color's sigmoidal contrast, normalized to fix 0 and 1. */
export function sigmoidal(t: number, contrast: number, midpoint: number): number {
	if (contrast < CONTRAST_EPS) return t;
	const b = contrast;
	const a = midpoint;
	const lo = 1 / (1 + Math.exp(b * a));
	const hi = 1 / (1 + Math.exp(b * (a - 1)));
	return Math.min(1, Math.max(0, (1 / (1 + Math.exp(b * (a - t))) - lo) / (hi - lo)));
}

// OKLab (Björn Ottosson, 2020), row-major: out = M · v.
export const LIN_TO_LMS = [
	[0.4122214708, 0.5363325363, 0.0514459929],
	[0.2119034982, 0.6806995451, 0.1073969566],
	[0.0883024619, 0.2817188376, 0.6299787005]
] as const;
export const LMS_TO_LAB = [
	[0.2104542553, 0.793617785, -0.0040720468],
	[1.9779984951, -2.428592205, 0.4505937099],
	[0.0259040371, 0.7827717662, -0.808675766]
] as const;
export const LAB_TO_LMS = [
	[1.0, 0.3963377774, 0.2158037573],
	[1.0, -0.1055613458, -0.0638541728],
	[1.0, -0.0894841775, -1.291485548]
] as const;
export const LMS_TO_LIN = [
	[4.0767416621, -3.3077115913, 0.2309699292],
	[-1.2684380046, 2.6097574011, -0.3413193965],
	[-0.0041960863, -0.7034186147, 1.707614701]
] as const;

type Mat3 = readonly (readonly [number, number, number])[];
function mul(m: Mat3, v: Triple): Triple {
	return [
		m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
		m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
		m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2]
	];
}

export function srgbToLinear(c: number): number {
	return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

export function linearToSrgb(c: number): number {
	c = Math.min(1, Math.max(0, c));
	return c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
}

/** Scale the OKLab chroma of an sRGB-encoded color by `s`. */
export function saturate(rgb: Triple, s: number): Triple {
	if (Math.abs(s - 1) < SATURATION_EPS) return rgb;
	const lms = mul(LIN_TO_LMS, [srgbToLinear(rgb[0]), srgbToLinear(rgb[1]), srgbToLinear(rgb[2])]);
	const lab = mul(LMS_TO_LAB, [Math.cbrt(lms[0]), Math.cbrt(lms[1]), Math.cbrt(lms[2])]);
	lab[1] *= s;
	lab[2] *= s;
	const l = mul(LAB_TO_LMS, lab);
	const lin = mul(LMS_TO_LIN, [l[0] ** 3, l[1] ** 3, l[2] ** 3]);
	return [linearToSrgb(lin[0]), linearToSrgb(lin[1]), linearToSrgb(lin[2])];
}

/** A stretch with numbers in it: the resolved window, or the provisional one. */
export function windowOf(st: TrueColorStretch): { lo: Triple; hi: Triple } {
	if (st.lo && st.hi) return { lo: st.lo, hi: st.hi };
	const [lo, hi] = PROVISIONAL_WINDOW;
	return { lo: [lo, lo, lo], hi: [hi, hi, hi] };
}

/** Reflectance → display sRGB in [0, 1]; null when any channel is NaN (transparent). */
export function tonePixel(rho: Triple, spec: TrueColorSpec): Triple | null {
	if (rho.some((v) => Number.isNaN(v))) return null;
	const { lo, hi } = windowOf(spec.stretch);
	let t: Triple = [0, 1, 2].map((c) => linearStretch(rho[c], lo[c], hi[c])) as Triple;
	t = t.map((v) => applyGamma(v, spec.gamma)) as Triple;
	t = t.map((v) => sigmoidal(v, spec.contrast, spec.midpoint)) as Triple;
	return saturate(t, spec.saturation);
}

/** Reflectance → display value for one channel, without saturation (which mixes channels). */
export function toneCurve(x: number, spec: TrueColorSpec, channel = 0): number {
	const { lo, hi } = windowOf(spec.stretch);
	return sigmoidal(applyGamma(linearStretch(x, lo[channel], hi[channel]), spec.gamma), spec.contrast, spec.midpoint);
}

/** What resolveStretch needs from the frame's RGB statistics. */
export interface ChannelPercentiles {
	/** percentile levels, aligned with each channel's `percentiles` */
	qs: readonly number[];
	channels: readonly { percentiles: readonly number[] }[];
}

function percentileOf(stats: ChannelPercentiles, channel: number, q: number): number | null {
	const i = stats.qs.findIndex((x) => Math.abs(x - q) < 1e-9);
	if (i < 0) return null;
	const v = stats.channels[channel]?.percentiles[i];
	return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/**
 * Fill lo/hi for an auto stretch from the frame's percentiles. Linked takes
 * the widest window across channels (min of the lows, max of the highs), so
 * the balance is kept. Manual stretches, and auto ones without usable stats,
 * pass through unchanged.
 */
export function resolveStretch(st: TrueColorStretch, stats: ChannelPercentiles | null): TrueColorStretch {
	if (st.source === 'manual' && st.lo && st.hi) return st;
	if (!stats) return st;
	const lo: number[] = [];
	const hi: number[] = [];
	for (let c = 0; c < 3; c++) {
		const l = percentileOf(stats, c, st.pLo);
		const h = percentileOf(stats, c, st.pHi);
		if (l === null || h === null) return st;
		lo.push(l);
		hi.push(h);
	}
	let loT = lo as Triple;
	let hiT = hi as Triple;
	if (st.mode === 'linked') {
		const l = Math.min(...lo);
		const h = Math.max(...hi);
		loT = [l, l, l];
		hiT = [h, h, h];
	} else if (st.mode === 'dark-object') {
		const span = Math.max(1e-4, ...lo.map((l, c) => hi[c] - l));
		hiT = loT.map((l) => l + span) as Triple;
	}
	hiT = hiT.map((h, c) => Math.max(h, loT[c] + 1e-4)) as Triple;
	return { ...st, lo: loT, hi: hiT };
}

/** Dark-object stretches share hi − lo across bands to within this (mirrors SPAN_TOL). */
export const SPAN_TOL = 1e-6;

/** Switch stretch mode, keeping the current window as a starting point. */
export function withMode(st: TrueColorStretch, mode: TrueColorStretch['mode']): TrueColorStretch {
	if (mode === st.mode) return st;
	if (mode === 'per-channel' || !st.lo || !st.hi) return { ...st, mode };
	if (mode === 'dark-object') {
		const span = Math.max(...st.lo.map((l, c) => st.hi![c] - l));
		return { ...st, mode, lo: [...st.lo], hi: st.lo.map((l) => l + span) as Triple };
	}
	const l = Math.min(...st.lo);
	const h = Math.max(...st.hi);
	return { ...st, mode, lo: [l, l, l], hi: [h, h, h] };
}

/**
 * Move one end of the window (`ch` null = all bands) and keep the mode's
 * invariant: linked moves every band; dark-object moves a band's black point
 * with its white point (span kept), and a white point sets the shared span.
 */
export function moveEnd(lo: Triple, hi: Triple, mode: TrueColorStretch['mode'], end: 'lo' | 'hi', ch: number | null, v: number, minGap: number): { lo: Triple; hi: Triple } {
	const L = [...lo] as Triple;
	const H = [...hi] as Triple;
	const chans = ch === null || mode === 'linked' ? [0, 1, 2] : [ch];
	if (mode === 'dark-object') {
		const span = Math.max(...lo.map((l, c) => hi[c] - l));
		if (end === 'lo') {
			for (const c of chans) L[c] = v;
			return { lo: L, hi: L.map((l) => l + span) as Triple };
		}
		const s = Math.max(minGap, v - lo[chans[0]]);
		return { lo: L, hi: L.map((l) => l + s) as Triple };
	}
	for (const c of chans) {
		if (end === 'lo') L[c] = Math.min(v, H[c] - minGap);
		else H[c] = Math.max(v, L[c] + minGap);
	}
	return { lo: L, hi: H };
}

/** A fresh spec from a preset, resolved against the frame when stats are at hand. */
export function specFromPreset(preset: TrueColorPreset, stats: ChannelPercentiles | null): TrueColorSpec {
	const spec: TrueColorSpec = {
		preset: preset.id,
		stretch: { ...preset.stretch, lo: preset.stretch.lo ? [...preset.stretch.lo] : null, hi: preset.stretch.hi ? [...preset.stretch.hi] : null },
		gamma: preset.gamma,
		contrast: preset.contrast,
		midpoint: preset.midpoint,
		saturation: preset.saturation
	};
	return { ...spec, stretch: resolveStretch(spec.stretch, stats) };
}

/** True when the spec could be exported as-is (every number resolved and the window valid). */
export function isResolved(spec: TrueColorSpec): boolean {
	const { lo, hi, mode } = spec.stretch;
	if (!lo || !hi) return false;
	if (lo.some((l, c) => !(hi[c] > l))) return false;
	if (mode === 'linked' && (new Set(lo).size > 1 || new Set(hi).size > 1)) return false;
	if (mode === 'dark-object') {
		const spans = lo.map((l, c) => hi[c] - l);
		if (Math.max(...spans) - Math.min(...spans) > SPAN_TOL) return false;
	}
	return true;
}

/** sRGB [0, 1] triple → `#rrggbb`. */
export function hexOf(rgb: Triple): string {
	return '#' + rgb.map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, '0')).join('');
}

/** The preset's settings differ from the current tone (ignoring an auto window's resolved numbers). */
export function toneDiffersFromPreset(p: TrueColorPreset | undefined, t: TrueColorSpec | null): boolean {
	if (!p || !t) return false;
	const a = p.stretch;
	const b = t.stretch;
	const nums = (x: number, y: number) => Math.abs(x - y) > 1e-9;
	if (a.mode !== b.mode || a.source !== b.source) return true;
	if (a.source === 'auto' && (nums(a.pLo, b.pLo) || nums(a.pHi, b.pHi))) return true;
	if (a.source === 'manual' && (a.lo?.some((v, i) => nums(v, b.lo?.[i] ?? NaN)) || a.hi?.some((v, i) => nums(v, b.hi?.[i] ?? NaN)))) return true;
	return nums(p.gamma, t.gamma) || nums(p.contrast, t.contrast) || nums(p.midpoint, t.midpoint) || nums(p.saturation, t.saturation);
}
