import type { Summary } from './percentiles';

/** Mirrors spectralcular.normalize's methods, plus 'domain' (index's canonical range). */
export type Normalization =
	| { method: 'percentile'; lower: number; upper: number }
	| { method: 'fixed'; vmin: number; vmax: number }
	| { method: 'diverging' }
	| { method: 'domain' };

export const DEFAULT_NORMALIZATION: Normalization = { method: 'percentile', lower: 1, upper: 99 };

export interface ResolvedRange {
	vmin: number;
	vmax: number;
	description: string;
}

function pct(summary: Summary, q: number): number {
	if (q === 1) return summary.p1;
	if (q === 2) return summary.p2;
	if (q === 50) return summary.p50;
	if (q === 98) return summary.p98;
	if (q === 99) return summary.p99;
	if (q <= 0) return summary.min;
	if (q >= 100) return summary.max;
	// Only the stored percentiles are available without the raw pixels.
	throw new Error(`percentile ${q} not available; use 1/2/50/98/99`);
}

/**
 * Turn a normalization request into vmin/vmax. Returns null when the method
 * needs stats that are not available yet.
 */
export function resolveNormalization(
	norm: Normalization,
	stats: Summary | null,
	domain: [number, number] | null
): ResolvedRange | null {
	switch (norm.method) {
		case 'fixed':
			return { vmin: norm.vmin, vmax: norm.vmax, description: `fixed[${fmt(norm.vmin)},${fmt(norm.vmax)}]` };
		case 'domain':
			if (!domain) return null;
			return { vmin: domain[0], vmax: domain[1], description: `domain[${fmt(domain[0])},${fmt(domain[1])}]` };
		case 'percentile': {
			if (!stats || stats.n === 0) return null;
			const vmin = pct(stats, norm.lower);
			const vmax = pct(stats, norm.upper);
			if (!(vmax > vmin)) return null;
			return { vmin, vmax, description: `pct[${norm.lower},${norm.upper}]` };
		}
		case 'diverging': {
			if (!stats || stats.n === 0) return null;
			const m = Math.max(Math.abs(stats.p1), Math.abs(stats.p99));
			if (!(m > 0)) return null;
			return { vmin: -m, vmax: m, description: 'diverging' };
		}
	}
}

function fmt(v: number): string {
	return Number.isInteger(v) ? String(v) : v.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
}
