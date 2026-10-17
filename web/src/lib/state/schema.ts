import { z } from 'zod';

/**
 * Persisted project schema (localStorage + project JSON export).
 * Bump SCHEMA_VERSION and add a step in migrations.ts when this changes.
 */
export const SCHEMA_VERSION = 2 as const;

export const LonLatSchema = z.tuple([z.number(), z.number()]);
export const BBoxSchema = z.tuple([z.number(), z.number(), z.number(), z.number()]);

export const FrameSchema = z.object({
	center: LonLatSchema,
	widthM: z.number().positive(),
	rotationDeg: z.number()
});

const PolygonSchema = z.object({
	type: z.literal('Polygon'),
	coordinates: z.array(z.array(z.tuple([z.number(), z.number()]).or(z.array(z.number()))))
});
const MultiPolygonSchema = z.object({
	type: z.literal('MultiPolygon'),
	coordinates: z.array(z.array(z.array(z.tuple([z.number(), z.number()]).or(z.array(z.number())))))
});

export const SceneSchema = z.object({
	collection: z.string(),
	itemId: z.string(),
	datetime: z.string(),
	cloudCover: z.number(),
	mgrs: z.string(),
	epsg: z.number().nullable(),
	platform: z.string().nullable().default(null),
	geometry: z.union([PolygonSchema, MultiPolygonSchema]),
	/** asset key → href, so the plate renders without a second STAC round-trip */
	assetHrefs: z.record(z.string(), z.string()),
	thumbnail: z.string().nullable().default(null)
});

export const NormalizationSchema = z.discriminatedUnion('method', [
	z.object({ method: z.literal('percentile'), lower: z.number(), upper: z.number() }),
	z.object({ method: z.literal('fixed'), vmin: z.number(), vmax: z.number() }),
	z.object({ method: z.literal('diverging') }),
	z.object({ method: z.literal('domain') })
]);

export const SummarySchema = z.object({
	n: z.number(),
	min: z.number(),
	max: z.number(),
	mean: z.number(),
	std: z.number(),
	p1: z.number(),
	p2: z.number(),
	p50: z.number(),
	p98: z.number(),
	p99: z.number(),
	p5: z.number().optional(),
	p25: z.number().optional(),
	p75: z.number().optional(),
	p95: z.number().optional()
});

export const FrameStatsSchema = z.object({
	/** cache key: item + index + frame + mask */
	computedFor: z.string(),
	resolutionM: z.number(),
	nPixels: z.number(),
	validFraction: z.number(),
	nodataFraction: z.number(),
	maskedFraction: z.number(),
	summary: SummarySchema,
	histogram: z.object({ edges: z.tuple([z.number(), z.number()]), counts: z.array(z.number()), under: z.number().optional(), over: z.number().optional() })
});

const TripleSchema = z.tuple([z.number(), z.number(), z.number()]);

/** Mirrors truecolor.py TrueColorStretch (camelCase). */
export const TrueColorStretchSchema = z.object({
	mode: z.enum(['linked', 'dark-object', 'per-channel']),
	source: z.enum(['auto', 'manual']),
	pLo: z.number().min(0).max(50),
	pHi: z.number().min(50).max(100),
	lo: TripleSchema.nullable(),
	hi: TripleSchema.nullable()
});

/** Mirrors truecolor.py TrueColorSpec (camelCase). */
export const TrueColorSpecSchema = z.object({
	preset: z.string().nullable(),
	stretch: TrueColorStretchSchema,
	gamma: z.number().min(0.2).max(5),
	contrast: z.number().min(0).max(30),
	midpoint: z.number().gt(0).lt(1),
	saturation: z.number().min(0).max(3)
});

const RgbChannelSchema = z.object({ percentiles: z.array(z.number()), mean: z.number(), counts: z.array(z.number()) });

/** Per-channel reflectance statistics of a true-color frame (see stats/compute.ts computeRgbStats). */
export const RgbFrameStatsSchema = z.object({
	/** cache key: item + rgb + frame + mask */
	computedFor: z.string(),
	resolutionM: z.number(),
	nPixels: z.number(),
	validFraction: z.number(),
	nodataFraction: z.number(),
	maskedFraction: z.number(),
	qs: z.array(z.number()),
	edges: z.tuple([z.number(), z.number()]),
	channels: z.tuple([RgbChannelSchema, RgbChannelSchema, RgbChannelSchema])
});

export const LocationRefSchema = z.discriminatedUnion('kind', [
	z.object({ kind: z.literal('pool'), locationId: z.string(), name: z.string() }),
	z.object({
		kind: z.literal('adhoc'),
		/** slug used as location_id on import */
		locationId: z.string(),
		name: z.string(),
		landscapeCharacter: z.string().default('')
	})
]);

export const PlateSchema = z.object({
	id: z.string(),
	ordinal: z.number().int().nonnegative(),
	location: LocationRefSchema,
	frame: FrameSchema,
	scene: SceneSchema.nullable(),
	/** ISO interval used for the STAC search that found the scene */
	dateRange: z.string().nullable().default(null),
	/** index + colormap, or a true-color composite (v2) */
	kind: z.enum(['index', 'truecolor']).default('index'),
	/** the tone of a true-color plate; null for index plates */
	truecolor: TrueColorSpecSchema.nullable().default(null),
	/**
	 * Index and colormap are kept on true-color plates too, so switching a
	 * plate back to index mode restores its previous look.
	 */
	index: z.string(),
	colormap: z.object({ name: z.string(), reversed: z.boolean().default(false) }),
	normalization: NormalizationSchema,
	maskEnabled: z.boolean().default(true),
	stats: FrameStatsSchema.nullable().default(null),
	rgbStats: RgbFrameStatsSchema.nullable().default(null),
	caption: z.string().default(''),
	notes: z.string().default(''),
	createdAt: z.string(),
	updatedAt: z.string()
});

export const ProjectSchema = z.object({
	schemaVersion: z.literal(SCHEMA_VERSION),
	id: z.string(),
	name: z.string(),
	plates: z.array(PlateSchema),
	createdAt: z.string(),
	updatedAt: z.string()
});

export type Frame = z.infer<typeof FrameSchema>;
export type Scene = z.infer<typeof SceneSchema>;
export type NormalizationSpec = z.infer<typeof NormalizationSchema>;
export type FrameStats = z.infer<typeof FrameStatsSchema>;
export type LocationRef = z.infer<typeof LocationRefSchema>;
export type Plate = z.infer<typeof PlateSchema>;
export type PlateKind = Plate['kind'];
export type RgbFrameStats = z.infer<typeof RgbFrameStatsSchema>;
export type Project = z.infer<typeof ProjectSchema>;

export function newId(): string {
	return typeof crypto !== 'undefined' && 'randomUUID' in crypto
		? crypto.randomUUID()
		: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function emptyProject(name = 'Untitled book'): Project {
	const now = new Date().toISOString();
	return { schemaVersion: SCHEMA_VERSION, id: newId(), name, plates: [], createdAt: now, updatedAt: now };
}

/**
 * Slug matching candidates.candidate_slug: `<location_id>__<index>__<colormap>[_r]`
 * with the colormap's `:` and `.` separators flattened to `_`; a reversed pick
 * appends `_r` unless the name already ends with it.
 */
export function candidateSlug(locationId: string, index: string, colormap: string, reversed = false): string {
	let cm = colormap.replaceAll(':', '_').replaceAll('.', '_');
	if (reversed && !cm.endsWith('_r')) cm += '_r';
	return `${locationId}__${index}__${cm}`;
}

/** Slug matching candidates.candidate_slug for a true-color plate: `<location_id>__truecolor`. */
export function trueColorSlug(locationId: string): string {
	return `${locationId}__truecolor`;
}

/** The candidate id a plate exports under. */
export function plateSlug(p: Pick<Plate, 'kind' | 'location' | 'index' | 'colormap'>): string {
	return p.kind === 'truecolor'
		? trueColorSlug(p.location.locationId)
		: candidateSlug(p.location.locationId, p.index, p.colormap.name, p.colormap.reversed);
}

/** A pool-style id from a free-text name: lowercase, ascii, underscores. */
export function slugify(name: string): string {
	return name
		.normalize('NFKD')
		.replace(/[̀-ͯ]/g, '')
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '_')
		.replace(/^_+|_+$/g, '')
		.slice(0, 64) || 'location';
}
