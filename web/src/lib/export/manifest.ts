import { PAGE } from '$lib/book/page';
import { frameCorners, frameEnvelope, frameHeightM } from '$lib/geo/frame';
import { getIndex, getLocation, book, platformForCollection, type TrueColorSpec } from '$lib/data';
import { resolveNormalization } from '$lib/raster/stats/normalization';
import { isResolved } from '$lib/raster/truecolor';
import { trueColorBlockerFor } from '$lib/data/fit';
import { plateSlug, type Plate, type Project } from '$lib/state/schema';

export const MANIFEST_KIND = 'spectralcular.selection';
/** v2: plates carry `kind` and, for true-color plates, the `truecolor` tone. */
export const MANIFEST_VERSION = 2;

/** One plate as the Python importer (`spectralcular import`) consumes it. */
export interface ManifestPlate {
	ordinal: number;
	id: string;
	locationId: string;
	location: {
		kind: 'pool' | 'adhoc';
		name: string;
		landscapeCharacter: string;
		bbox: [number, number, number, number];
		themes: string[];
	};
	frame: {
		center: [number, number];
		widthM: number;
		heightM: number;
		rotationDeg: number;
		bbox: [number, number, number, number];
		cornersLonLat: [number, number][];
	};
	scene: {
		collection: string;
		itemId: string;
		datetime: string;
		cloudCover: number;
		mgrs: string;
		epsg: number | null;
	} | null;
	dateRange: string | null;
	kind: 'index' | 'truecolor';
	/** null for true-color plates */
	index: string | null;
	theme: string;
	/** imagery platform id: the index's, or the scene collection's for true color */
	platform: string;
	/** null for true-color plates */
	colormap: string | null;
	colormapReversed: boolean;
	/** null for true-color plates */
	normalization: { method: string; vmin: number | null; vmax: number | null; description: string | null } | null;
	/** the resolved tone (lo/hi always numbers) for true-color plates; null otherwise */
	truecolor: TrueColorSpec | null;
	maskEnabled: boolean;
	stats: Plate['stats'];
	caption: string;
	notes: string;
}

export interface SelectionManifest {
	kind: typeof MANIFEST_KIND;
	version: typeof MANIFEST_VERSION;
	exportedAt: string;
	app: { name: string; dataSchemaVersion: number };
	project: { id: string; name: string };
	book: { pageSize: string; orientation: string; dpi: number; bleedIn: number; plateAspect: number };
	plates: ManifestPlate[];
}

/**
 * The book section a plate sits in. Index plates follow their index's theme; a
 * true-color plate follows its location's primary theme (falling back to the
 * index it remembers, for ad-hoc places).
 */
export function plateTheme(p: Pick<Plate, 'kind' | 'index' | 'location'>): string {
	const def = getIndex(p.index);
	if (p.kind === 'truecolor') {
		const pool = p.location.kind === 'pool' ? getLocation(p.location.locationId) : undefined;
		return pool?.themes[0] ?? def?.theme ?? 'vegetation';
	}
	return def?.theme ?? 'vegetation';
}

export function plateToManifest(p: Plate): ManifestPlate {
	const def = getIndex(p.index);
	const tc = p.kind === 'truecolor';
	const theme = plateTheme(p);
	const envelope = frameEnvelope(p.frame, PAGE.aspect);
	const pool = p.location.kind === 'pool' ? getLocation(p.location.locationId) : undefined;
	const resolved = tc ? null : resolveNormalization(p.normalization, p.stats?.summary ?? null, def?.domain ?? null);
	const platform = tc
		? ((p.scene && platformForCollection(p.scene.collection)?.id) ?? def?.platform ?? 'sentinel-2-l2a')
		: (def?.platform ?? 'sentinel-2-l2a');
	return {
		ordinal: p.ordinal,
		id: plateSlug(p),
		locationId: p.location.locationId,
		location: {
			kind: p.location.kind,
			name: p.location.name,
			landscapeCharacter: p.location.kind === 'adhoc' ? p.location.landscapeCharacter : pool?.landscapeCharacter ?? '',
			bbox: envelope,
			themes: pool?.themes ?? [theme]
		},
		frame: {
			center: p.frame.center,
			widthM: p.frame.widthM,
			heightM: frameHeightM(p.frame, PAGE.aspect),
			rotationDeg: p.frame.rotationDeg,
			bbox: envelope,
			cornersLonLat: frameCorners(p.frame, PAGE.aspect)
		},
		scene: p.scene
			? {
					collection: p.scene.collection,
					itemId: p.scene.itemId,
					datetime: p.scene.datetime,
					cloudCover: p.scene.cloudCover,
					mgrs: p.scene.mgrs,
					epsg: p.scene.epsg
				}
			: null,
		dateRange: p.dateRange,
		kind: p.kind,
		index: tc ? null : p.index,
		theme,
		platform,
		colormap: tc ? null : p.colormap.name,
		colormapReversed: tc ? false : p.colormap.reversed,
		normalization: tc
			? null
			: {
					method: p.normalization.method,
					vmin: resolved?.vmin ?? null,
					vmax: resolved?.vmax ?? null,
					description: resolved?.description ?? null
				},
		truecolor: tc && p.truecolor ? p.truecolor : null,
		maskEnabled: p.maskEnabled,
		stats: tc ? null : p.stats,
		caption: p.caption,
		notes: p.notes
	};
}

export function buildManifest(project: Project): SelectionManifest {
	const plates = [...project.plates].sort((a, b) => a.ordinal - b.ordinal).map(plateToManifest);
	return {
		kind: MANIFEST_KIND,
		version: MANIFEST_VERSION,
		exportedAt: new Date().toISOString(),
		app: { name: 'spectralcular-picker', dataSchemaVersion: book.schemaVersion },
		project: { id: project.id, name: project.name },
		book: {
			pageSize: book.pageSize,
			orientation: book.orientation,
			dpi: book.dpi,
			bleedIn: book.bleedIn,
			plateAspect: PAGE.aspect
		},
		plates
	};
}

/** Problems that would make `spectralcular import` reject or the build skip a plate. */
export function validateForExport(project: Project): string[] {
	const problems: string[] = [];
	const seen = new Map<string, number>();
	for (const p of project.plates) {
		const slug = plateSlug(p);
		if (!p.scene) problems.push(`#${p.ordinal} ${p.location.name}: no scene selected`);
		if (p.kind === 'truecolor') {
			const platform = p.scene ? platformForCollection(p.scene.collection) : undefined;
			if (p.scene && !platform) problems.push(`#${p.ordinal}: no platform for ${p.scene.collection}`);
			const blocker = platform && p.scene ? trueColorBlockerFor(platform, p.scene) : null;
			if (blocker) problems.push(`#${p.ordinal} ${p.location.name}: true color unavailable — ${blocker}`);
			if (!p.truecolor) problems.push(`#${p.ordinal} ${p.location.name}: true-color plate has no tone`);
			else if (!isResolved(p.truecolor)) problems.push(`#${p.ordinal} ${p.location.name}: the auto stretch is not resolved yet — open the plate so its frame is sampled, then save`);
		} else if (!getIndex(p.index)) problems.push(`#${p.ordinal}: unknown index ${p.index}`);
		const prev = seen.get(slug);
		if (prev) {
			const what = p.kind === 'truecolor' ? 'location, true color' : 'location, index, colormap and direction';
			problems.push(`#${p.ordinal} duplicates #${prev} (${slug}) — same ${what}`);
		} else seen.set(slug, p.ordinal);
	}
	return problems;
}
