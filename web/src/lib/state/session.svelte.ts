import type { Map as MaplibreMap } from 'maplibre-gl';
import type { LocationDef } from '$lib/data/types';
import {
	DEFAULT_PLATFORM_ID,
	DEFAULT_TRUECOLOR_PRESET,
	getColormap,
	getIndex,
	getPlatform,
	getTrueColorPreset,
	gsdForIndex,
	gsdForTrueColor,
	platformForCollection,
	platformForIndex
} from '$lib/data';
import type { PlatformDef, TrueColorSpec, TrueColorStretch } from '$lib/data/types';
import { hexOf, resolveStretch, specFromPreset, tonePixel, withMode } from '$lib/raster/truecolor';
import { signHrefs } from '$lib/stac/sas';
import { maskRequestFor } from '$lib/raster/stats/mask';
import { PAGE, ceilingMeters, printStats } from '$lib/book/page';
import { frameKey, type FrameModel } from '$lib/geo/frame';
import type { LonLat } from '$lib/geo/tangent';
import { resolveNormalization, DEFAULT_NORMALIZATION } from '$lib/raster/stats/normalization';
import { StatsClient, StaleRequestError } from '$lib/raster/stats/client';
import { parseInterval, formatInterval, expandWindow, clampToToday, type DateRange } from '$lib/stac/query';
import type { StacItem } from '$lib/stac/types';
import { clientFor } from '$lib/stac/client';
import { framePolygon } from '$lib/geo/frame';
import { sceneFromItem } from '$lib/stac/scene';
import { newId, type FrameStats, type LocationRef, type NormalizationSpec, type Plate, type PlateKind, type RgbFrameStats, type Scene, slugify } from './schema';
import { indexFitForScene, trueColorBlockerFor, type Fit } from '$lib/data/fit';
import { rankScenes } from '$lib/stac/rank';
import { colormapForRamp, readingNotes, stretchOptions, type RampType, type StretchOption } from '$lib/data/reading';

/** The plate being edited. Same shape as Plate minus identity/order/timestamps. */
export interface Draft {
	location: LocationRef;
	frame: FrameModel;
	scene: Scene | null;
	dateRange: string | null;
	/** index + colormap, or a true-color composite */
	kind: PlateKind;
	/** the true-color tone; kept when switching back to index mode so the look returns */
	truecolor: TrueColorSpec | null;
	index: string;
	colormap: { name: string; reversed: boolean };
	normalization: NormalizationSpec;
	maskEnabled: boolean;
	caption: string;
	notes: string;
	stats: FrameStats | null;
	rgbStats: RgbFrameStats | null;
}

export type SearchStatus = 'idle' | 'loading' | 'done' | 'error';
export type OrbitState = 'ascending' | 'descending';

const DEFAULT_INDEX = 'NDVI';
const DEFAULT_COLORMAP = 'bids:viridis';

/**
 * Frame width at which the plate prints at exactly PAGE.dpi from the index's
 * native pixels (the full-resolution ceiling). New frames start here.
 */
export function fullResolutionWidthM(index: string): number {
	const def = getIndex(index);
	return ceilingMeters(def ? gsdForIndex(def) : 10).widthM;
}

export function defaultDraft(): Draft {
	return {
		location: { kind: 'adhoc', locationId: 'new_location', name: 'New location', landscapeCharacter: '' },
		frame: { center: [15.0, 37.75], widthM: fullResolutionWidthM(DEFAULT_INDEX), rotationDeg: 0 },
		scene: null,
		dateRange: null,
		kind: 'index',
		truecolor: null,
		index: DEFAULT_INDEX,
		colormap: { name: DEFAULT_COLORMAP, reversed: false },
		normalization: DEFAULT_NORMALIZATION,
		maskEnabled: true,
		caption: '',
		notes: '',
		stats: null,
		rgbStats: null
	};
}


/**
 * Ephemeral editor state: the draft plate, the map handle, scene search
 * results and in-flight statistics. Nothing here is persisted except through
 * ProjectStore when the user saves the plate.
 */
export class Session {
	draft = $state<Draft>(defaultDraft());
	/** id of the saved plate the draft was loaded from (null = unsaved draft) */
	editingPlateId = $state<string | null>(null);
	dirty = $state(false);
	map = $state<MaplibreMap | null>(null);
	/** first symbol layer id in the basemap style — rasters render beneath it */
	labelLayerId = $state<string | null>(null);
	frameDragging = $state(false);

	search = $state<{
		status: SearchStatus;
		items: StacItem[];
		error: string | null;
		cloudMax: number;
		range: DateRange;
		yearRadius: number;
		/** radar pass direction; a frame must never mix the two */
		orbit: OrbitState;
	}>({
		status: 'idle',
		items: [],
		error: null,
		cloudMax: 20,
		range: { start: '2023-06-01', end: '2023-08-31' },
		yearRadius: 0,
		orbit: 'ascending'
	});
	selectedItem = $state<StacItem | null>(null);
	/** colormap list filter; III Read's suggestions set it to the ramp type they recommend */
	colormapType = $state<'all' | RampType>('all');
	/** the III Read step has been acted on for this draft (a stretch chosen or confirmed) */
	readDone = $state(false);

	statsStatus = $state<'idle' | 'loading' | 'done' | 'error'>('idle');
	statsError = $state<string | null>(null);
	private statsClient = new StatsClient();
	private searchAbort: AbortController | null = null;

	readonly indexDef = $derived(getIndex(this.draft.index) ?? null);
	readonly isTrueColor = $derived(this.draft.kind === 'truecolor');
	/** Platform of the pinned scene's collection (null without a scene). */
	readonly scenePlatform: PlatformDef | null = $derived(this.draft.scene ? (platformForCollection(this.draft.scene.collection) ?? null) : null);
	/**
	 * Imagery platform: the chosen index's, or for true color the pinned scene's
	 * (falling back to the index's before a scene is pinned). Drives scene search and rendering.
	 */
	readonly platform: PlatformDef = $derived(
		(this.isTrueColor ? this.scenePlatform : null) ??
			(this.indexDef ? platformForIndex(this.indexDef) : (getPlatform(DEFAULT_PLATFORM_ID) as PlatformDef))
	);
	/** Why true color is unavailable here (null: available). */
	readonly trueColorBlocker = $derived(trueColorBlockerFor(this.scenePlatform ?? this.platform, this.draft.scene));
	/** Whether the chosen index can compute from the pinned scene (platform and per-item assets). */
	readonly sceneFit: Fit | null = $derived(this.indexDef ? indexFitForScene(this.indexDef, this.draft.scene) : null);
	/** False when the pinned scene comes from a different collection than the index needs. */
	readonly sceneMatchesPlatform = $derived(!this.draft.scene || this.draft.scene.collection === this.platform.collection);
	readonly colormapDef = $derived(getColormap(this.draft.colormap.name) ?? null);
	readonly gsdM = $derived(this.isTrueColor ? gsdForTrueColor(this.platform) : this.indexDef ? gsdForIndex(this.indexDef) : 10);
	readonly print = $derived(printStats(this.draft.frame.widthM, this.gsdM));
	readonly range = $derived(resolveNormalization(this.draft.normalization, this.draft.stats?.summary ?? null, this.indexDef?.domain ?? null));
	readonly framePoly = $derived(framePolygon(this.draft.frame, PAGE.aspect));
	readonly statsKey = $derived(
		this.draft.scene ? `${this.draft.scene.itemId}|${this.draft.index}|${frameKey(this.draft.frame)}|${this.draft.maskEnabled}` : null
	);
	readonly rgbStatsKey = $derived(
		this.draft.scene ? `${this.draft.scene.itemId}|rgb|${frameKey(this.draft.frame)}|${this.draft.maskEnabled}` : null
	);
	/** How to read this index on this frame: stretch options and notes (III Read). Null in true-color mode. */
	readonly reading = $derived.by(() => {
		const def = this.indexDef;
		if (!def || this.isTrueColor) return null;
		const stats = this.draft.stats;
		const input = {
			def,
			summary: stats?.summary ?? null,
			histogram: stats?.histogram ?? null,
			normalization: this.draft.normalization,
			range: this.range,
			colormap: this.colormapDef,
			maskedFraction: stats?.maskedFraction ?? null
		};
		return { options: stretchOptions(input), notes: readingNotes(input) };
	});
	/**
	 * UI accent: the selected colormap's ink (≈ 65 % along the ramp), or for
	 * true color the frame's median pixel rendered through the tone.
	 */
	readonly accent = $derived.by(() => {
		if (this.isTrueColor) {
			const tc = this.draft.truecolor;
			const st = this.draft.rgbStats;
			const i = st?.qs.indexOf(50) ?? -1;
			if (!tc || !st || i < 0) return '#8c8a7e';
			const rgb = tonePixel([st.channels[0].percentiles[i], st.channels[1].percentiles[i], st.channels[2].percentiles[i]], tc);
			return rgb ? hexOf(rgb) : '#8c8a7e';
		}
		const stops = this.colormapDef?.previewStops;
		if (!stops?.length) return '#d9a441';
		const i = this.draft.colormap.reversed ? Math.floor(stops.length * 0.35) : Math.floor(stops.length * 0.65);
		return stops[Math.min(stops.length - 1, i)];
	});

	// ----- draft mutations -------------------------------------------------

	private touch(): void {
		this.dirty = true;
	}

	setFrame(f: FrameModel): void {
		this.draft.frame = f;
		this.touch();
	}

	setLocationName(name: string): void {
		if (this.draft.location.kind === 'adhoc') {
			this.draft.location = { ...this.draft.location, name, locationId: slugify(name) };
		} else {
			// editing a pool location's name detaches it into an ad-hoc one
			this.draft.location = { kind: 'adhoc', name, locationId: slugify(name), landscapeCharacter: '' };
		}
		this.touch();
	}

	/**
	 * Begin a fresh plate at a suggested location, centered on its bbox at full
	 * print resolution. `look` overrides the carried-over index and colormap.
	 */
	startFromLocation(loc: LocationDef, look?: { index: string; colormap: string }): void {
		const index = look?.index ?? this.draft.index;
		const center: LonLat = [(loc.bbox[0] + loc.bbox[2]) / 2, (loc.bbox[1] + loc.bbox[3]) / 2];
		this.draft = {
			...defaultDraft(),
			location: { kind: 'pool', locationId: loc.id, name: loc.name },
			frame: { center, widthM: fullResolutionWidthM(index), rotationDeg: 0 },
			dateRange: loc.seasonWindows[0] ?? null,
			// An example look is an index plate; otherwise the mode and tone carry over.
			kind: look ? 'index' : this.draft.kind,
			truecolor: this.carriedTone(),
			index,
			colormap: look ? { name: look.colormap, reversed: false } : { ...this.draft.colormap },
			maskEnabled: this.draft.maskEnabled
		};
		this.editingPlateId = null;
		this.dirty = false;
		this.readDone = false;
		this.selectedItem = null;
		this.search = { ...this.search, status: 'idle', items: [], error: null, cloudMax: Math.max(this.search.cloudMax, loc.cloudCoverMax), range: parseInterval(loc.seasonWindows[0] ?? '2023-06-01/2023-08-31'), yearRadius: 0 };
		this.flyToFrame();
	}

	/** Begin a fresh plate wherever the map is looking. */
	startHere(): void {
		const map = this.map;
		this.startAtPoint('New location', map ? [map.getCenter().lng, map.getCenter().lat] : this.draft.frame.center);
	}

	/** Begin a fresh ad-hoc plate at a point (a searched place or typed coordinates). */
	startAtPoint(name: string, center: LonLat, opts: { fly?: boolean } = {}): void {
		this.draft = {
			...defaultDraft(),
			location: { kind: 'adhoc', locationId: slugify(name) || 'new_location', name, landscapeCharacter: '' },
			frame: { center, widthM: fullResolutionWidthM(this.draft.index), rotationDeg: 0 },
			dateRange: formatInterval(this.search.range),
			kind: this.draft.kind,
			truecolor: this.carriedTone(),
			index: this.draft.index,
			colormap: { ...this.draft.colormap },
			maskEnabled: this.draft.maskEnabled
		};
		this.editingPlateId = null;
		this.dirty = false;
		this.readDone = false;
		this.selectedItem = null;
		this.search = { ...this.search, status: 'idle', items: [], error: null };
		if (opts.fly) this.flyToFrame();
	}

	/** The tone a new plate inherits: same settings, auto windows re-derived on the new frame. */
	private carriedTone(): TrueColorSpec | null {
		const tc = this.draft.truecolor ? ($state.snapshot(this.draft.truecolor) as TrueColorSpec) : null;
		if (!tc) return null;
		return tc.stretch.source === 'auto' ? { ...tc, stretch: { ...tc.stretch, lo: null, hi: null } } : tc;
	}

	/** Load a saved plate into the editor. */
	loadPlate(plate: Plate): void {
		const p = $state.snapshot(plate) as Plate;
		this.draft = {
			location: p.location,
			frame: p.frame,
			scene: p.scene,
			dateRange: p.dateRange,
			kind: p.kind,
			truecolor: p.truecolor,
			index: p.index,
			colormap: p.colormap,
			normalization: p.normalization,
			maskEnabled: p.maskEnabled,
			caption: p.caption,
			notes: p.notes,
			stats: p.stats,
			rgbStats: p.rgbStats
		};
		this.editingPlateId = p.id;
		this.dirty = false;
		this.readDone = true;
		this.selectedItem = null;
		if (p.dateRange) this.search = { ...this.search, range: parseInterval(p.dateRange), status: 'idle', items: [], error: null };
		this.flyToFrame();
	}

	/** Snapshot the draft as a Plate (new id unless `existing` is given). */
	toPlate(existing?: Plate): Plate {
		const now = new Date().toISOString();
		const d = $state.snapshot(this.draft) as Draft;
		return {
			id: existing?.id ?? newId(),
			ordinal: existing?.ordinal ?? 0,
			location: d.location,
			frame: d.frame,
			scene: d.scene,
			dateRange: d.dateRange,
			kind: d.kind,
			truecolor: d.truecolor,
			index: d.index,
			colormap: d.colormap,
			normalization: d.normalization,
			maskEnabled: d.maskEnabled,
			stats: d.stats,
			rgbStats: d.rgbStats,
			caption: d.caption,
			notes: d.notes,
			createdAt: existing?.createdAt ?? now,
			updatedAt: now
		};
	}

	setIndex(name: string): void {
		if (this.isTrueColor) this.draft.kind = 'index';
		if (name === this.draft.index) {
			this.touch();
			return;
		}
		const before = this.platform.collection;
		this.draft.index = name;
		this.draft.stats = null;
		this.readDone = false;
		// Results from another collection would only pin a scene the new index cannot read.
		if (this.platform.collection !== before) this.search = { ...this.search, status: 'idle', items: [], error: null };
		this.touch();
	}
	setColormap(name: string): void {
		const fromTrueColor = this.isTrueColor;
		this.draft.kind = 'index';
		this.draft.colormap = { ...this.draft.colormap, name };
		this.readDone = true;
		this.touch();
		if (fromTrueColor) void this.refreshStats();
	}
	toggleReversed(): void {
		if (this.isTrueColor) return;
		this.draft.colormap = { ...this.draft.colormap, reversed: !this.draft.colormap.reversed };
		this.touch();
	}
	setNormalization(n: NormalizationSpec): void {
		this.draft.normalization = n;
		this.readDone = true;
		this.touch();
	}

	/**
	 * Apply a III Read stretch: its normalization, the ramp type it calls for
	 * (switching to a paired map of that type if the current one isn't), and
	 * the colormap list filtered to match.
	 */
	applyStretch(opt: StretchOption): void {
		// III Read suggestions are index stretches; they never apply to a true-color plate.
		if (this.isTrueColor) return;
		this.setNormalization(opt.normalization);
		this.colormapType = opt.ramp;
		const next = colormapForRamp(opt.ramp, this.draft.index, this.colormapDef);
		if (next && next.name !== this.draft.colormap.name) this.setColormap(next.name);
	}

	/** Keep the current stretch and move on from III Read. */
	confirmReading(): void {
		this.readDone = true;
	}
	setMaskEnabled(v: boolean): void {
		this.draft.maskEnabled = v;
		this.draft.stats = null;
		this.draft.rgbStats = null;
		this.touch();
	}

	// ----- true color --------------------------------------------------------

	/**
	 * Switch the draft to a true-color plate. Starts from the default preset the
	 * first time; afterwards the previous tone returns. False when the
	 * scene/platform has no visible bands.
	 */
	selectTrueColor(): boolean {
		if (this.trueColorBlocker) return false;
		this.draft.kind = 'truecolor';
		if (!this.draft.truecolor) this.draft.truecolor = specFromPreset(DEFAULT_TRUECOLOR_PRESET, this.draft.rgbStats);
		this.touch();
		void this.refreshStats();
		return true;
	}

	/** Apply a preset's whole tone, resolved against this frame when its stats are in. */
	applyTrueColorPreset(id: string): void {
		const preset = getTrueColorPreset(id);
		if (!preset) return;
		this.draft.truecolor = specFromPreset(preset, this.draft.rgbStats);
		this.readDone = true;
		this.touch();
	}

	/** Back to the current preset's settings (the default preset when there is none). */
	resetToPreset(): void {
		this.applyTrueColorPreset(this.draft.truecolor?.preset ?? DEFAULT_TRUECOLOR_PRESET.id);
	}

	/** Change gamma / contrast / midpoint / saturation. */
	setTone(patch: Partial<Pick<TrueColorSpec, 'gamma' | 'contrast' | 'midpoint' | 'saturation'>>): void {
		const tc = this.draft.truecolor;
		if (!tc) return;
		this.draft.truecolor = { ...tc, ...patch };
		this.readDone = true;
		this.touch();
	}

	/**
	 * Change the stretch. Typed or dragged lo/hi values make it manual; choosing
	 * percentiles (or source: 'auto') re-derives the window from the frame.
	 */
	setStretch(patch: Partial<TrueColorStretch>): void {
		const tc = this.draft.truecolor;
		if (!tc) return;
		let st: TrueColorStretch = { ...tc.stretch, ...patch };
		if (patch.mode && patch.mode !== tc.stretch.mode) st = withMode({ ...tc.stretch, ...patch, mode: tc.stretch.mode }, patch.mode);
		if (patch.lo || patch.hi) st = { ...st, source: 'manual' };
		else if (st.source === 'auto') st = resolveStretch({ ...st, lo: null, hi: null }, this.draft.rgbStats);
		this.draft.truecolor = { ...tc, stretch: st };
		this.readDone = true;
		this.touch();
	}

	useScene(item: StacItem): void {
		this.selectedItem = item;
		this.draft.scene = sceneFromItem(item);
		this.draft.dateRange = formatInterval(this.search.range);
		this.draft.stats = null;
		this.draft.rgbStats = null;
		this.touch();
	}
	clearScene(): void {
		this.selectedItem = null;
		this.draft.scene = null;
		this.draft.stats = null;
		this.draft.rgbStats = null;
		this.touch();
	}

	// ----- STAC search -----------------------------------------------------

	async runSearch(): Promise<void> {
		this.searchAbort?.abort();
		const ctrl = new AbortController();
		this.searchAbort = ctrl;
		this.search = { ...this.search, status: 'loading', error: null };
		const r = clampToToday(this.search.yearRadius > 0 ? expandWindow(this.search.range, this.search.yearRadius) : this.search.range);
		const platform = this.platform;
		// Platform-level clauses (e.g. Landsat 8/9 only) plus one radar pass direction,
		// so frames never straddle ascending and descending swaths.
		const query: Record<string, unknown> = { ...platform.stacQuery };
		if (platform.orbitProperty) query[platform.orbitProperty] = { eq: this.search.orbit };
		try {
			const items = await clientFor(platform).search(
				{
					intersects: this.framePoly,
					datetime: formatInterval(r),
					collection: platform.collection,
					cloudCoverMax: platform.cloudProperty ? this.search.cloudMax : undefined,
					query: Object.keys(query).length ? query : undefined,
					limit: 60
				},
				ctrl.signal
			);
			if (ctrl.signal.aborted) return;
			this.search = { ...this.search, status: 'done', items };
		} catch (e) {
			if (ctrl.signal.aborted) return;
			this.search = { ...this.search, status: 'error', error: e instanceof Error ? e.message : String(e), items: [] };
		}
	}

	/**
	 * Pin the best result of the last search: full frame coverage first, then the
	 * least cloud. Returns false when nothing usable came back.
	 */
	pinBestScene(): boolean {
		const ranked = rankScenes(this.search.items, this.platform.collection, this.framePoly);
		if (!ranked.length) return false;
		this.useScene(ranked[0].item);
		void this.refreshStats();
		return true;
	}

	/**
	 * A worked example for first-time users: frame a pool location, search its
	 * season window and pin the best scene, so a rendered plate appears without
	 * touching the inspector. Index and colormap are set explicitly so the example
	 * reads the same every time.
	 */
	async startExample(loc: LocationDef, index: string, colormap: string): Promise<'pinned' | 'no-scene' | 'error'> {
		this.startFromLocation(loc, { index, colormap });
		await this.runSearch();
		if (this.search.status === 'error') return 'error';
		return this.pinBestScene() ? 'pinned' : 'no-scene';
	}

	// ----- statistics ------------------------------------------------------

	/** Recompute stats for the current scene/index/frame (or RGB, for true color) if they are stale. */
	async refreshStats(): Promise<void> {
		if (this.isTrueColor) return this.refreshRgbStats();
		const key = this.statsKey;
		const scene = this.draft.scene;
		const def = this.indexDef;
		if (!key || !scene || !def || !def.computable) return;
		if (this.draft.stats?.computedFor === key) return;
		if (scene.epsg === null) {
			this.statsStatus = 'error';
			this.statsError = 'Scene has no EPSG code';
			return;
		}
		const platform = platformForIndex(def);
		if (scene.collection !== platform.collection) {
			this.statsStatus = 'error';
			this.statsError = `${def.name} needs a ${platform.label} scene; the pinned scene is ${scene.collection}`;
			return;
		}
		const raw: Record<string, string> = {};
		for (const asset of Object.values(def.bands)) {
			if (!scene.assetHrefs[asset]) {
				this.statsStatus = 'error';
				this.statsError = `Scene has no "${asset}" asset`;
				return;
			}
			raw[asset] = scene.assetHrefs[asset];
		}
		const maskAsset = platform.mask?.asset;
		if (maskAsset && scene.assetHrefs[maskAsset]) raw[maskAsset] = scene.assetHrefs[maskAsset];
		const hrefs = await signHrefs(raw, platform);
		const constants = Object.fromEntries(Object.entries(def.constants).filter(([, v]) => v !== null) as [string, number][]);
		this.statsStatus = 'loading';
		this.statsError = null;
		try {
			const r = await this.statsClient.compute({
				hrefs,
				bands: def.bands,
				formula: def.formula,
				constants,
				frame: { ...this.draft.frame, center: [...this.draft.frame.center] as [number, number] },
				aspect: PAGE.aspect,
				epsg: scene.epsg,
				valueScale: platform.valueScale,
				nodata: platform.nodata,
				mask: maskRequestFor(platform),
				maskEnabled: this.draft.maskEnabled,
				resolutionM: 80,
				domain: def.domain
			});
			// Only accept if the draft still matches what we computed.
			if (this.statsKey !== key) return;
			this.draft.stats = {
				computedFor: key,
				resolutionM: r.resolutionM,
				nPixels: r.nPixels,
				validFraction: r.validFraction,
				nodataFraction: r.nodataFraction,
				maskedFraction: r.maskedFraction,
				summary: r.summary,
				histogram: r.histogram
			};
			this.statsStatus = 'done';
		} catch (e) {
			if (e instanceof StaleRequestError) return;
			this.statsStatus = 'error';
			this.statsError = e instanceof Error ? e.message : String(e);
		}
	}

	/** Per-channel reflectance stats for a true-color frame; re-resolves an auto stretch. */
	private async refreshRgbStats(): Promise<void> {
		const key = this.rgbStatsKey;
		const scene = this.draft.scene;
		const platform = this.scenePlatform;
		if (!key || !scene || !platform?.rgbAssets) return;
		if (this.draft.rgbStats?.computedFor === key) {
			this.resolveAutoStretch();
			return;
		}
		if (scene.epsg === null) {
			this.statsStatus = 'error';
			this.statsError = 'Scene has no EPSG code';
			return;
		}
		const blocker = trueColorBlockerFor(platform, scene);
		if (blocker) {
			this.statsStatus = 'error';
			this.statsError = blocker;
			return;
		}
		const rgbAssets = platform.rgbAssets;
		const raw: Record<string, string> = Object.fromEntries(rgbAssets.map((a) => [a, scene.assetHrefs[a]]));
		const maskAsset = platform.mask?.asset;
		if (maskAsset && scene.assetHrefs[maskAsset]) raw[maskAsset] = scene.assetHrefs[maskAsset];
		const hrefs = await signHrefs(raw, platform);
		this.statsStatus = 'loading';
		this.statsError = null;
		try {
			const r = await this.statsClient.computeRgb({
				kind: 'rgb',
				rgbAssets: [...rgbAssets],
				hrefs,
				frame: { ...this.draft.frame, center: [...this.draft.frame.center] as [number, number] },
				aspect: PAGE.aspect,
				epsg: scene.epsg,
				valueScale: platform.valueScale,
				nodata: platform.nodata,
				mask: maskRequestFor(platform),
				maskEnabled: this.draft.maskEnabled,
				resolutionM: 80
			});
			if (this.rgbStatsKey !== key) return;
			if (r.validFraction === 0 || r.channels.some((c) => c.percentiles.some((v) => !Number.isFinite(v)))) {
				this.statsStatus = 'error';
				this.statsError = 'No valid pixels in the frame (all nodata or masked)';
				return;
			}
			this.draft.rgbStats = {
				computedFor: key,
				resolutionM: r.resolutionM,
				nPixels: r.nPixels,
				validFraction: r.validFraction,
				nodataFraction: r.nodataFraction,
				maskedFraction: r.maskedFraction,
				qs: r.qs,
				edges: r.edges,
				channels: r.channels
			};
			this.resolveAutoStretch();
			this.statsStatus = 'done';
		} catch (e) {
			if (e instanceof StaleRequestError) return;
			this.statsStatus = 'error';
			this.statsError = e instanceof Error ? e.message : String(e);
		}
	}

	/** An auto stretch follows the frame: re-derive its window from the current RGB stats. */
	private resolveAutoStretch(): void {
		const tc = this.draft.truecolor;
		if (!tc || tc.stretch.source !== 'auto' || !this.draft.rgbStats) return;
		const st = resolveStretch({ ...tc.stretch, lo: null, hi: null }, this.draft.rgbStats);
		const same = st.lo && tc.stretch.lo && st.hi && tc.stretch.hi && st.lo.every((v, i) => v === tc.stretch.lo![i]) && st.hi.every((v, i) => v === tc.stretch.hi![i]);
		if (!same) this.draft.truecolor = { ...tc, stretch: st };
	}

	// ----- map helpers -----------------------------------------------------

	flyToFrame(opts: { alignToPage?: boolean } = {}): void {
		const map = this.map;
		if (!map) return;
		const f = this.draft.frame;
		const heightM = f.widthM / PAGE.aspect;
		const canvas = map.getCanvas();
		// meters per pixel at zoom z (equator) = 40075016.686 / (512 · 2^z); scale by cos(lat)
		const cos = Math.cos((f.center[1] * Math.PI) / 180);
		const padPx = 80;
		const fitW = (canvas.clientWidth - 2 * padPx) / 1.15;
		const fitH = (canvas.clientHeight - 2 * padPx) / 1.15;
		// when not aligned the rotated frame's screen footprint grows; pad by |sin|+|cos|
		const t = (Math.abs(f.rotationDeg) * Math.PI) / 180;
		const grow = opts.alignToPage ? 1 : Math.abs(Math.cos(t)) + Math.abs(Math.sin(t));
		const mpp = Math.max((f.widthM * grow) / fitW, (heightM * grow) / fitH);
		const zoom = Math.log2((40075016.686 * cos) / (512 * mpp));
		map.easeTo({ center: f.center, zoom, bearing: opts.alignToPage ? f.rotationDeg : 0, pitch: 0, duration: 700 });
	}

	alignViewToPage(): void {
		this.flyToFrame({ alignToPage: true });
	}
	northUp(): void {
		this.map?.easeTo({ bearing: 0, pitch: 0, duration: 500 });
	}

	dispose(): void {
		this.statsClient.dispose();
		this.searchAbort?.abort();
	}
}

let session: Session | null = null;
export function getSession(): Session {
	// In dev, keep the singleton on globalThis so HMR module swaps don't wipe the draft.
	const g = globalThis as unknown as { __gbSession?: Session };
	if (import.meta.env.DEV && g.__gbSession) session = g.__gbSession;
	if (!session) {
		session = new Session();
		if (import.meta.env.DEV) g.__gbSession = session;
	}
	return session;
}
