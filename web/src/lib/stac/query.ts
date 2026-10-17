/** Date-range helpers mirroring review/preview.py's season-window handling. */

export interface DateRange {
	start: string; // YYYY-MM-DD
	end: string;
}

export function parseInterval(s: string): DateRange {
	const [start, end] = s.split('/');
	if (!start || !end) throw new Error(`Bad interval "${s}"`);
	return { start: start.slice(0, 10), end: end.slice(0, 10) };
}

export function formatInterval(r: DateRange): string {
	return `${r.start}/${r.end}`;
}

/** Shift a season window by whole years (e.g. -2 … +2), keeping month/day. */
export function shiftYears(r: DateRange, years: number): DateRange {
	const shift = (d: string) => {
		const y = Number(d.slice(0, 4)) + years;
		return `${String(y).padStart(4, '0')}${d.slice(4)}`;
	};
	return { start: shift(r.start), end: shift(r.end) };
}

/** Expand a window to ±radius years, as one contiguous range (for a single STAC query). */
export function expandWindow(r: DateRange, radiusYears: number): DateRange {
	return { start: shiftYears(r, -radiusYears).start, end: shiftYears(r, radiusYears).end };
}

export function todayIso(): string {
	return new Date().toISOString().slice(0, 10);
}

export function clampToToday(r: DateRange): DateRange {
	const t = todayIso();
	return { start: r.start, end: r.end > t ? t : r.end };
}
