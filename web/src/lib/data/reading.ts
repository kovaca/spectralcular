/**
 * Reading an index before coloring it: which values matter, where this frame
 * sits among them, and which stretch and kind of ramp suit it. Pure functions
 * over the exported index metadata (guide, formula bounds, reference-surface
 * values) and the frame's statistics; the III Read panel and the guide coach
 * render what these return.
 */
import { colormaps, getSurface } from '$lib/data';
import type { ColormapDef, GuideClass, IndexDef, SurfaceValue } from './types';
import type { Histogram, Summary } from '$lib/raster/stats/percentiles';
import type { ResolvedRange } from '$lib/raster/stats/normalization';
import type { NormalizationSpec } from '$lib/state/schema';

export type RampType = 'sequential' | 'diverging';

export interface TypicalRange {
	lo: number;
	hi: number;
	/** guide: written by hand · formula: the index's full closed range · surfaces: span of the reference surfaces */
	source: 'guide' | 'formula' | 'surfaces';
}

/** The range a plate of this index is best read against, before looking at any frame. */
export function typicalRange(def: IndexDef): TypicalRange | null {
	if (def.guide) return { lo: def.guide.typical[0], hi: def.guide.typical[1], source: 'guide' };
	const b = def.bounds;
	const closed = b && !b.loOpen && !b.hiOpen && b.hi > b.lo ? b : null;
	const sv = def.surfaces.map((s) => s.value);
	// With many surfaces, one outlier (snow, turbid water) shouldn't set the scale.
	const core = sv.length >= 6 ? sv.slice(1, -1) : sv;
	const sLo = Math.min(...core);
	const sHi = Math.max(...core);
	if (closed && (!core.length || sHi - sLo >= 0.2 * (closed.hi - closed.lo))) return { lo: closed.lo, hi: closed.hi, source: 'formula' };
	if (core.length >= 2 && sHi > sLo) {
		const pad = 0.08 * (sHi - sLo);
		return { lo: sLo - pad, hi: sHi + pad, source: 'surfaces' };
	}
	return closed ? { lo: closed.lo, hi: closed.hi, source: 'formula' } : null;
}

export interface Center {
	value: number;
	label: string;
	source: 'guide' | 'formula';
}

/** A value with meaning on both sides, where a diverging ramp should split. */
export function centerOf(def: IndexDef): Center | null {
	if (def.guide) return def.guide.center === null ? null : { value: def.guide.center, label: def.guide.centerLabel ?? '', source: 'guide' };
	// A normalized difference (closed and symmetric about 0): 0 means both bands reflect equally.
	const b = def.bounds;
	if (b && !b.loOpen && !b.hiOpen && b.lo < 0 && Math.abs(b.lo + b.hi) <= 1e-6 * Math.max(1, b.hi)) {
		return { value: 0, label: 'bands equal', source: 'formula' };
	}
	return null;
}

/** Ruler extent: the typical range, widened to hold this frame's 2–98 % and the ramp. */
export function readingAxis(def: IndexDef, summary: Summary | null, range: ResolvedRange | null): [number, number] {
	const t = typicalRange(def);
	let lo = t?.lo ?? Infinity;
	let hi = t?.hi ?? -Infinity;
	if (summary && summary.n > 0) {
		lo = Math.min(lo, summary.p2);
		hi = Math.max(hi, summary.p98);
	}
	if (!(hi > lo)) {
		const sv = def.surfaces.map((s) => s.value);
		if (sv.length >= 2) [lo, hi] = [Math.min(...sv), Math.max(...sv)];
		else if (Number.isFinite(lo)) [lo, hi] = [lo - 0.5, lo + 0.5];
		else [lo, hi] = [-1, 1];
	}
	if (range) {
		// Show the ramp ends, unless a typed range reaches far past anything real.
		const span = hi - lo;
		if (range.vmin < lo && range.vmin >= lo - span) lo = range.vmin;
		if (range.vmax > hi && range.vmax <= hi + span) hi = range.vmax;
	}
	const pad = 0.03 * (hi - lo);
	return [lo - pad, hi + pad];
}

export interface ClassShare {
	cls: GuideClass;
	index: number;
	fraction: number;
}

function classIndex(classes: GuideClass[], v: number): number {
	for (let i = 0; i < classes.length; i++) {
		const c = classes[i];
		if ((c.lo === null || v >= c.lo) && (c.hi === null || v < c.hi)) return i;
	}
	return -1;
}

function histTotal(h: Histogram): number {
	return h.counts.reduce((a, b) => a + b, 0) + (h.under ?? 0) + (h.over ?? 0);
}

/** Share of the frame's valid pixels in each guide class (histogram bin centers). */
export function composition(classes: GuideClass[], h: Histogram | null): ClassShare[] | null {
	if (!classes.length || !h) return null;
	const total = histTotal(h);
	if (!total) return null;
	const shares = classes.map((cls, index) => ({ cls, index, fraction: 0 }));
	const [lo, hi] = h.edges;
	const w = (hi - lo) / h.counts.length;
	h.counts.forEach((c, i) => {
		const k = classIndex(classes, lo + (i + 0.5) * w);
		if (k >= 0) shares[k].fraction += c / total;
	});
	if (h.under) shares[Math.max(0, classIndex(classes, lo - 1e-9))].fraction += h.under / total;
	if (h.over) shares[Math.max(0, classIndex(classes, hi + 1e-9))].fraction += h.over / total;
	return shares;
}

/** Fraction of valid pixels below `v` (histogram estimate). */
export function fractionBelow(h: Histogram, v: number): number {
	const total = histTotal(h);
	if (!total) return 0;
	const [lo, hi] = h.edges;
	const w = (hi - lo) / h.counts.length;
	// Values beyond the edges are counted with the nearer side.
	let n = v > lo ? (h.under ?? 0) : 0;
	if (v > hi) n += h.over ?? 0;
	h.counts.forEach((c, i) => {
		const a = lo + i * w;
		if (a + w <= v) n += c;
		else if (a < v) n += (c * (v - a)) / w;
	});
	return Math.min(1, n / total);
}

export interface RampFit {
	/** share of the ramp's length that this frame's 2–98 % values cover */
	use: number;
	/** share of valid pixels outside [vmin, vmax], printed as the end colors */
	clipped: number;
}

export function rampFit(range: ResolvedRange, summary: Summary, h: Histogram | null): RampFit {
	const span = range.vmax - range.vmin;
	const use = span > 0 ? Math.max(0, Math.min(range.vmax, summary.p98) - Math.max(range.vmin, summary.p2)) / span : 0;
	const clipped = h ? fractionBelow(h, range.vmin) + (1 - fractionBelow(h, range.vmax)) : 0;
	return { use: Math.min(1, use), clipped: Math.max(0, Math.min(1, clipped)) };
}

/** Surfaces whose value falls inside this frame's 2–98 % values. */
export function surfacesInFrame(def: IndexDef, summary: Summary | null): SurfaceValue[] {
	if (!summary || summary.n === 0) return [];
	return def.surfaces.filter((s) => s.value >= summary.p2 && s.value <= summary.p98);
}

export function surfaceLabel(id: string): string {
	return getSurface(id)?.label ?? id.replaceAll('_', ' ');
}

// ----- stretch options -----------------------------------------------------

export interface StretchOption {
	id: 'frame' | 'book' | 'split';
	title: string;
	detail: string;
	normalization: NormalizationSpec;
	ramp: RampType;
	recommended: boolean;
	active: boolean;
	/** how much of the ramp this frame's values would fill (null before stats) */
	use: number | null;
}

export interface ReadingInput {
	def: IndexDef;
	summary: Summary | null;
	histogram: Histogram | null;
	normalization: NormalizationSpec;
	range: ResolvedRange | null;
	colormap: ColormapDef | null;
	maskedFraction: number | null;
}

export function fmtValue(v: number): string {
	const a = Math.abs(v);
	if (a >= 1000) return v.toExponential(1);
	if (a >= 100) return v.toFixed(0);
	if (a >= 10) return v.toFixed(1);
	return v.toFixed(2);
}

const pct = (f: number) => `${Math.round(f * 100)}%`;

function sameNorm(a: NormalizationSpec, b: NormalizationSpec): boolean {
	if (a.method !== b.method) return false;
	if (a.method === 'percentile' && b.method === 'percentile') return a.lower === b.lower && a.upper === b.upper;
	if (a.method === 'fixed' && b.method === 'fixed') return Math.abs(a.vmin - b.vmin) < 1e-9 && Math.abs(a.vmax - b.vmax) < 1e-9;
	return true;
}

function useFor(vmin: number, vmax: number, s: Summary | null): number | null {
	if (!s || s.n === 0 || !(vmax > vmin)) return null;
	return Math.max(0, Math.min(vmax, s.p98) - Math.max(vmin, s.p2)) / (vmax - vmin);
}

export function stretchOptions(r: ReadingInput): StretchOption[] {
	const { def, summary: s, histogram: h } = r;
	const name = def.name;
	const out: StretchOption[] = [];

	const frameNorm: NormalizationSpec = { method: 'percentile', lower: 2, upper: 98 };
	out.push({
		id: 'frame',
		title: 'Fit this frame',
		detail: s
			? `The ramp spans this frame's middle 96 % (${fmtValue(s.p2)} … ${fmtValue(s.p98)}): the most contrast, but one color means different values on different plates.`
			: `The ramp spans this frame's middle 96 % once pixels are sampled: the most contrast, but one color means different values on different plates.`,
		normalization: frameNorm,
		ramp: 'sequential',
		recommended: false,
		active: false,
		use: s ? 1 : null
	});

	const t = typicalRange(def);
	if (t) {
		const use = useFor(t.lo, t.hi, s);
		const clip = h ? fractionBelow(h, t.lo) + (1 - fractionBelow(h, t.hi)) : 0;
		const where =
			t.source === 'guide' ? `where ${name} usually falls` : t.source === 'formula' ? `the formula's full range` : `the span of the reference surfaces`;
		const fills = use === null ? '' : `; this frame fills ${pct(use)} of the ramp${clip >= 0.05 ? ` and ${pct(clip)} of it clips to the end colors` : ''}`;
		out.push({
			id: 'book',
			title: 'Book-wide range',
			detail: `Fixed at ${fmtValue(t.lo)} … ${fmtValue(t.hi)}, ${where}. Every ${name} plate reads alike${fills}.`,
			normalization: { method: 'fixed', vmin: t.lo, vmax: t.hi },
			ramp: 'sequential',
			recommended: false,
			active: false,
			use
		});
	}

	const c = centerOf(def);
	let below: number | null = null;
	if (c) {
		below = h ? fractionBelow(h, c.value) : null;
		let norm: NormalizationSpec;
		let use: number | null = null;
		if (c.value === 0) {
			norm = { method: 'diverging' };
			if (s && s.n) {
				const m = Math.max(Math.abs(s.p1), Math.abs(s.p99));
				use = useFor(-m, m, s);
			}
		} else {
			const m = s && s.n ? Math.max(Math.abs(s.p2 - c.value), Math.abs(s.p98 - c.value)) : t ? Math.max(Math.abs(t.lo - c.value), Math.abs(t.hi - c.value)) : 1;
			norm = { method: 'fixed', vmin: +(c.value - m).toFixed(4), vmax: +(c.value + m).toFixed(4) };
			use = useFor(c.value - m, c.value + m, s);
		}
		const sides = below === null ? '' : ` This frame has ${pct(below)} below and ${pct(1 - below)} above.`;
		out.push({
			id: 'split',
			title: `Split at ${fmtValue(c.value)}`,
			detail: `A diverging ramp centered on ${fmtValue(c.value)}${c.label ? ` (${c.label})` : ''}: color says which side each pixel is on.${sides}`,
			normalization: norm,
			ramp: 'diverging',
			recommended: false,
			active: false,
			use
		});
	}

	// Recommend: a real two-sided frame → split; a frame that fills the book-wide
	// range well → book; otherwise fit the frame.
	const split = out.find((o) => o.id === 'split');
	const book = out.find((o) => o.id === 'book');
	let pick: StretchOption | undefined;
	if (split && below !== null && Math.min(below, 1 - below) >= 0.15) pick = split;
	else if (book && (book.use === null || book.use >= 0.4)) pick = book;
	else pick = out[0];
	pick.recommended = true;
	for (const o of out) o.active = sameNorm(o.normalization, r.normalization);
	return out;
}

// ----- notes ---------------------------------------------------------------

export interface ReadingNote {
	level: 'info' | 'warn';
	text: string;
}

export function readingNotes(r: ReadingInput): ReadingNote[] {
	const { def, summary: s, histogram: h, range, colormap } = r;
	const notes: ReadingNote[] = [];
	const c = centerOf(def);
	const t = typicalRange(def);

	if (range && s && s.n) {
		const fit = rampFit(range, s, h);
		if (fit.clipped > 0.08) notes.push({ level: 'warn', text: `${pct(fit.clipped)} of the frame falls outside the ramp and prints as its end colors.` });
		if (fit.use < 0.25) notes.push({ level: 'warn', text: `This frame fills only ${pct(fit.use)} of the ramp; most of its colors go unused.` });

		if (colormap?.type === 'diverging') {
			const mid = (range.vmin + range.vmax) / 2;
			const span = range.vmax - range.vmin;
			if (c && Math.abs(mid - c.value) > 0.1 * span) {
				notes.push({ level: 'warn', text: `The diverging ramp's midpoint lands at ${fmtValue(mid)}, not at ${fmtValue(c.value)}${c.label ? ` (${c.label})` : ''}. Split at ${fmtValue(c.value)}, or use a sequential map.` });
			} else if (c && h) {
				const below = fractionBelow(h, c.value);
				if (Math.min(below, 1 - below) < 0.05) {
					const side = below < 0.5 ? 'above' : 'below';
					notes.push({ level: 'warn', text: `Almost all of this frame is ${side} ${fmtValue(c.value)}, so one half of the diverging ramp goes unused. A sequential map on the book-wide range shows more.` });
				}
			} else if (!c) {
				notes.push({ level: 'info', text: `${def.name} has no natural midpoint, so a diverging ramp's center (${fmtValue(mid)}) means nothing in particular. A sequential map reads more honestly.` });
			}
		} else if (colormap && c && h) {
			const below = fractionBelow(h, c.value);
			if (Math.min(below, 1 - below) >= 0.15) {
				notes.push({ level: 'info', text: `This frame sits on both sides of ${fmtValue(c.value)}${c.label ? ` (${c.label})` : ''}; a diverging map would show that split.` });
			}
		}

		if (def.guide && h) {
			const shares = composition(def.guide.classes, h) ?? [];
			const lowSide = shares.filter((x) => x.fraction >= 0.03 && x.cls.hi !== null && x.cls.hi <= range.vmin);
			const highSide = shares.filter((x) => x.fraction >= 0.03 && x.cls.lo !== null && x.cls.lo >= range.vmax);
			for (const [side, list] of [['first', lowSide], ['last', highSide]] as const) {
				if (list.length >= 2) {
					notes.push({ level: 'warn', text: `${list.map((x) => `‘${x.cls.label}’`).join(' and ')} all print as the ramp's ${side} color. Widen the range to tell them apart.` });
				}
			}
		}

		if (t && s.p98 - s.p2 < 0.08 * (t.hi - t.lo)) {
			notes.push({ level: 'warn', text: `Values barely vary here (${fmtValue(s.p2)} … ${fmtValue(s.p98)}). Fitting the frame will exaggerate small differences.` });
		}
	}

	if (r.maskedFraction !== null && r.maskedFraction > 0.3) {
		notes.push({ level: 'info', text: `${pct(r.maskedFraction)} of the frame is masked by the scene's quality layer (cloud, shadow, nodata); the reading covers the rest.` });
	}
	if (def.unstableSurfaces.length) {
		notes.push({ level: 'info', text: `No stable value for ${def.unstableSurfaces.map(surfaceLabel).join(', ')}: too dark in these bands, so ${def.name} there is noise.` });
	}
	return notes;
}

// ----- colormap choice -----------------------------------------------------

/** Keep the current map if it already has the wanted ramp type; else the best-paired one of that type. */
export function colormapForRamp(type: RampType, index: string, current: ColormapDef | null): ColormapDef | null {
	if (current?.type === type) return current;
	const pool = colormaps.filter((c) => c.row !== null && c.type === type);
	return (
		pool.find((c) => c.pairsWith.includes(index) && c.status === 'approved') ??
		pool.find((c) => c.pairsWith.includes(index)) ??
		pool.find((c) => c.status === 'approved') ??
		pool[0] ??
		null
	);
}

/** Color a value prints as under the current ramp (preview stops, clamped). */
export function colorAt(stops: string[], reversed: boolean, t: number): string {
	if (!stops.length) return 'transparent';
	const u = Math.max(0, Math.min(1, reversed ? 1 - t : t)) * (stops.length - 1);
	const i = Math.floor(u);
	const j = Math.min(stops.length - 1, i + 1);
	const f = u - i;
	const a = hex(stops[i]);
	const b = hex(stops[j]);
	const mix = a.map((x, k) => Math.round(x + (b[k] - x) * f));
	return `rgb(${mix[0]} ${mix[1]} ${mix[2]})`;
}

function hex(h: string): [number, number, number] {
	const n = parseInt(h.slice(1, 7), 16);
	return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
