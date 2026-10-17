import type { StacItem } from './types';

export function itemEpsg(item: StacItem): number | null {
	const p = item.properties;
	if (typeof p['proj:epsg'] === 'number') return p['proj:epsg'];
	const code = p['proj:code'];
	if (typeof code === 'string') {
		const m = /EPSG:(\d+)/i.exec(code);
		if (m) return Number(m[1]);
	}
	return null;
}

/**
 * Acquisition-grid label: Sentinel-2 MGRS tile, Landsat WRS path/row, or the
 * second chunk of the item id as a last resort.
 */
export function itemGrid(item: StacItem): string {
	const p = item.properties;
	if (typeof p['grid:code'] === 'string') return p['grid:code'].replace(/^MGRS-/, '');
	if (typeof p['s2:mgrs_tile'] === 'string') return p['s2:mgrs_tile'];
	const path = p['landsat:wrs_path'];
	const row = p['landsat:wrs_row'];
	if (path !== undefined && row !== undefined) return `${path}/${row}`;
	const orbit = p['sat:relative_orbit'];
	const state = p['sat:orbit_state'];
	if (typeof orbit === 'number' && typeof state === 'string') return `orbit ${orbit} ${state.slice(0, 3)}`;
	return item.id.split('_').slice(1, 2)[0] ?? '';
}

/** @deprecated use itemGrid */
export const itemMgrs = itemGrid;

/** Scene cloud cover in percent; NaN when the collection has no cloud property (radar). */
export function itemCloudCover(item: StacItem): number {
	return typeof item.properties['eo:cloud_cover'] === 'number' ? item.properties['eo:cloud_cover'] : NaN;
}

/** "12.3%" or "n/a" for collections without cloud cover. */
export function cloudLabel(pct: number, digits = 1): string {
	return Number.isFinite(pct) ? `${pct.toFixed(digits)}%` : 'n/a';
}

export function itemDate(item: StacItem): string {
	return item.properties.datetime.slice(0, 10);
}

/** Earth Search ships a `thumbnail` asset; Planetary Computer a `rendered_preview` (unsigned API URL). */
export function itemThumbnail(item: StacItem): string | null {
	return item.assets.thumbnail?.href ?? item.assets.rendered_preview?.href ?? null;
}

/** Asset key → href for the given asset keys; throws if any is missing. */
export function assetHrefs(item: StacItem, keys: string[]): Record<string, string> {
	const out: Record<string, string> = {};
	for (const k of keys) {
		const a = item.assets[k];
		if (!a) throw new Error(`Item ${item.id} has no asset "${k}"`);
		out[k] = a.href;
	}
	return out;
}

/** True for GeoTIFF assets regardless of extension case (`.tif`, `.TIF`, `.rtc.tiff`). */
export function isGeoTiffHref(href: string): boolean {
	return /\.tiff?(\?.*)?$/i.test(href);
}
