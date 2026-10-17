import type { Scene } from '$lib/state/schema';
import type { StacItem } from './types';
import { isGeoTiffHref, itemCloudCover, itemEpsg, itemGrid, itemThumbnail } from './items';

/** Strip a SAS token / query string so saved plates never carry an expiring URL. */
function unsigned(href: string): string {
	const i = href.indexOf('?');
	return i === -1 ? href : href.slice(0, i);
}

/**
 * Reduce a STAC item to what a plate needs to render and export later.
 * Hrefs are stored unsigned; Planetary Computer assets are signed at use.
 */
export function sceneFromItem(item: StacItem): Scene {
	const assetHrefs: Record<string, string> = {};
	for (const [k, a] of Object.entries(item.assets)) {
		if (isGeoTiffHref(a.href)) assetHrefs[k] = unsigned(a.href);
		else if (k === 'thumbnail') assetHrefs[k] = a.href;
	}
	const cloud = itemCloudCover(item);
	return {
		collection: item.collection,
		itemId: item.id,
		datetime: item.properties.datetime,
		cloudCover: Number.isFinite(cloud) ? cloud : 0,
		mgrs: itemGrid(item),
		epsg: itemEpsg(item),
		platform: item.properties.platform ?? item.properties.constellation ?? null,
		geometry: item.geometry,
		assetHrefs,
		thumbnail: itemThumbnail(item)
	};
}
