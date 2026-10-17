import type { Polygon, MultiPolygon } from 'geojson';

/** The subset of a STAC Item the picker relies on (Earth Search sentinel-2-l2a). */
export interface StacAsset {
	href: string;
	type?: string;
	title?: string;
	roles?: string[];
	'proj:shape'?: [number, number];
	'proj:transform'?: number[];
	'raster:bands'?: { nodata?: number; scale?: number; offset?: number; data_type?: string }[];
}

export interface StacItem {
	type: 'Feature';
	id: string;
	collection: string;
	bbox: [number, number, number, number];
	geometry: Polygon | MultiPolygon;
	properties: {
		datetime: string;
		platform?: string;
		constellation?: string;
		'eo:cloud_cover'?: number;
		'grid:code'?: string;
		's2:mgrs_tile'?: string;
		'proj:code'?: string;
		'proj:epsg'?: number;
		's2:processing_baseline'?: string;
		's2:nodata_pixel_percentage'?: number;
		[k: string]: unknown;
	};
	assets: Record<string, StacAsset>;
	links: { rel: string; href: string }[];
}

export interface StacSearchResponse {
	type: 'FeatureCollection';
	features: StacItem[];
	links?: { rel: string; href: string; method?: string; body?: unknown }[];
	numberMatched?: number;
	numberReturned?: number;
}

export interface SceneSearchParams {
	bbox?: [number, number, number, number];
	intersects?: Polygon;
	/** ISO interval "YYYY-MM-DD/YYYY-MM-DD" or single datetime */
	datetime?: string;
	cloudCoverMax?: number;
	limit?: number;
	collection?: string;
	/** extra STAC `query` clauses, e.g. `{ 'sat:orbit_state': { eq: 'ascending' } }` */
	query?: Record<string, unknown>;
}
