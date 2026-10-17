/**
 * numpy-compatible (linear interpolation) percentiles over a *sorted* array.
 * `np.percentile(a, q)` with the default method.
 */
export function percentileSorted(sorted: ArrayLike<number>, q: number): number {
	const n = sorted.length;
	if (n === 0) return NaN;
	if (n === 1) return sorted[0];
	const pos = (q / 100) * (n - 1);
	const lo = Math.floor(pos);
	const hi = Math.min(lo + 1, n - 1);
	const frac = pos - lo;
	return sorted[lo] + (sorted[hi] - sorted[lo]) * frac;
}

export interface Summary {
	n: number;
	min: number;
	max: number;
	mean: number;
	std: number;
	p1: number;
	p2: number;
	/** p5 / quartiles / p95 — optional: projects saved before they existed lack them */
	p5?: number;
	p25?: number;
	p50: number;
	p75?: number;
	p95?: number;
	p98: number;
	p99: number;
}

export function summarize(values: Float32Array | Float64Array): Summary {
	const n = values.length;
	if (n === 0) return { n: 0, min: NaN, max: NaN, mean: NaN, std: NaN, p1: NaN, p2: NaN, p50: NaN, p98: NaN, p99: NaN };
	const sorted = Float64Array.from(values).sort();
	let sum = 0;
	for (let i = 0; i < n; i++) sum += sorted[i];
	const mean = sum / n;
	let sq = 0;
	for (let i = 0; i < n; i++) sq += (sorted[i] - mean) ** 2;
	return {
		n,
		min: sorted[0],
		max: sorted[n - 1],
		mean,
		std: Math.sqrt(sq / n),
		p1: percentileSorted(sorted, 1),
		p2: percentileSorted(sorted, 2),
		p5: percentileSorted(sorted, 5),
		p25: percentileSorted(sorted, 25),
		p50: percentileSorted(sorted, 50),
		p75: percentileSorted(sorted, 75),
		p95: percentileSorted(sorted, 95),
		p98: percentileSorted(sorted, 98),
		p99: percentileSorted(sorted, 99)
	};
}

export interface Histogram {
	edges: [number, number];
	counts: number[];
	/** values below / above the edges (absent in stats saved before they were counted) */
	under?: number;
	over?: number;
}

export function histogram(values: ArrayLike<number>, lo: number, hi: number, bins = 64): Histogram {
	const counts = new Array<number>(bins).fill(0);
	const span = hi - lo;
	let under = 0;
	let over = 0;
	if (!(span > 0)) return { edges: [lo, hi], counts, under, over };
	for (let i = 0; i < values.length; i++) {
		const v = values[i];
		if (v < lo) {
			under++;
			continue;
		}
		if (v > hi) {
			over++;
			continue;
		}
		let b = Math.floor(((v - lo) / span) * bins);
		if (b === bins) b = bins - 1;
		counts[b]++;
	}
	return { edges: [lo, hi], counts, under, over };
}

/**
 * Where to bin a frame's histogram: the robust spread (p1–p99 plus a quarter
 * of its width each side) inside the index's domain, so one saturated pixel
 * can't flatten the bars and a domain of [-1, 1] doesn't waste bins on values
 * the frame never reaches.
 */
export function histogramWindow(s: Summary, domain: [number, number] | null): [number, number] {
	const w = s.p99 - s.p1;
	let lo = w > 0 ? Math.max(s.min, s.p1 - 0.25 * w) : s.min;
	let hi = w > 0 ? Math.min(s.max, s.p99 + 0.25 * w) : s.max;
	// Tukey's outer fences: a long thin tail holding > 1 % of the pixels must
	// not squash the bulk — but never hide more than 5 % per side (a second
	// class is not an outlier). Same rule as render_assets.histogram_window.
	if (s.p25 !== undefined && s.p75 !== undefined) {
		const iqr = s.p75 - s.p25;
		if (iqr > 0) {
			lo = Math.max(lo, Math.min(s.p25 - 3 * iqr, s.p5 ?? -Infinity));
			hi = Math.min(hi, Math.max(s.p75 + 3 * iqr, s.p95 ?? Infinity));
		}
	}
	if (domain) {
		lo = Math.max(lo, domain[0]);
		hi = Math.min(hi, domain[1]);
	}
	if (!(hi > lo)) {
		const c = Number.isFinite(s.p50) ? s.p50 : 0;
		const pad = Math.max(Math.abs(c) * 0.05, 1e-3);
		return [c - pad, c + pad];
	}
	return [lo, hi];
}
