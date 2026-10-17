/** Types for the JSON emitted by `spectralcular export-web` (see src/spectralcular/export_web.py). */

export type Theme =
	| 'vegetation'
	| 'water'
	| 'burn'
	| 'soil_geology'
	| 'urban'
	| 'snow_ice'
	| 'atmosphere'
	| 'radar';

export type ReviewStatus = 'proposed' | 'approved' | 'rejected' | 'needs_revision';

export interface BookSpec {
	schemaVersion: number;
	pageSize: 'Letter';
	orientation: 'portrait' | 'landscape';
	trim: { widthIn: number; heightIn: number };
	bleedIn: number;
	/** The plate fills the bleed box; this is the frame aspect (w/h). */
	plate: { widthIn: number; heightIn: number; aspect: number };
	dpi: number;
	nativePx: { width: number; height: number };
	aoiCeilingKm: { width: number; height: number };
	safetyMarginIn: number;
	gutter: { fullBleedInnerMarginIn: number; recommendedInnerMarginIn: number };
	reflectanceScale: number;
	sclMaskClasses: number[];
}

export interface IndexDef {
	name: string;
	longName: string;
	formula: string;
	theme: Theme;
	/** Imagery platform id (see PlatformDef / platforms.json). */
	platform: string;
	applicationDomain: string;
	/** spyndex band param → STAC asset key on `platform` */
	bands: Record<string, string>;
	params: string[];
	constants: Record<string, number | null>;
	domain: [number, number] | null;
	status: ReviewStatus;
	expressiveIn: string[];
	gsdM: number | null;
	gpuSupported: boolean;
	computable: boolean;
	reference: string;
	notes: string;
	/** Range the formula can produce from plausible band values; an open side is unbounded. */
	bounds: IndexBounds | null;
	/** Index value on each measured reference surface for this platform, ascending. */
	surfaces: SurfaceValue[];
	/** Surfaces whose value is within band noise here (e.g. clear water on Landsat). */
	unstableSurfaces: string[];
	/** Hand-written reading guide (config/index_guides.yaml), for headline indices. */
	guide: IndexGuide | null;
}

export interface IndexBounds {
	lo: number;
	hi: number;
	loOpen: boolean;
	hiOpen: boolean;
}

export interface SurfaceValue {
	id: string;
	value: number;
}

/** A labeled value band; null ends are open. */
export interface GuideClass {
	lo: number | null;
	hi: number | null;
	label: string;
}

export interface IndexGuide {
	measures: string;
	high: string;
	low: string;
	/** where real scenes usually fall; the suggested book-wide fixed range */
	typical: [number, number];
	classes: GuideClass[];
	/** a value with meaning on both sides; diverging maps split here */
	center: number | null;
	centerLabel: string | null;
	caveats: string[];
	source: string | null;
}

/** A familiar landscape measured on each platform (config/reference_surfaces.yaml). */
export interface ReferenceSurface {
	id: string;
	/** short tick label */
	label: string;
	description: string;
	/** platform id → the scene the spectrum was read from */
	samples: Record<string, { item: string; datetime: string; lonlat: [number, number] }>;
}

export interface IndicesFile {
	schemaVersion: number;
	/** Sentinel-2 tables kept for back-compat; prefer PlatformDef. */
	gsdByAsset: Record<string, number>;
	paramToAsset: Record<string, string>;
	surfaces: ReferenceSurface[];
	entries: IndexDef[];
}

export type MaskKind = 'scl-classes' | 'qa-bits';

export interface MaskSpec {
	/** STAC asset key holding the mask raster. */
	asset: string;
	/** `scl-classes`: mask pixels whose class is in `values`; `qa-bits`: mask when any bit position in `values` is set. */
	kind: MaskKind;
	values: number[];
}

/** One imagery platform, mirroring src/spectralcular/platforms.py. */
export interface PlatformDef {
	id: string;
	label: string;
	stacApi: string;
	collection: string;
	signing: 'none' | 'planetary-computer';
	/** spyndex band param → STAC asset key */
	paramToAsset: Record<string, string>;
	/** asset key → native GSD (m) */
	gsdByAsset: Record<string, number>;
	/** asset key → [scale, offset]; value = DN * scale + offset. `*` is the default. */
	valueScale: Record<string, [number, number]>;
	/** asset key → nodata DN (null: rely on NaN). `*` is the default. */
	nodata: Record<string, number | null>;
	mask: MaskSpec | null;
	cloudProperty: string | null;
	wavelengthNm: Record<string, number>;
	/** Every spectral band the sensor carries: asset key → [band code, plain name, center nm]. Empty for radar. */
	bandsNm: Record<string, [string, string, number]>;
	gridProperty: string | [string, string] | null;
	nativeResM: number;
	dtype: 'uint16' | 'float32';
	/** float32 platforms: value mapped to the top of the quantized 16-bit texture range */
	valueCeiling: number | null;
	/** item property selecting one radar pass direction, e.g. `sat:orbit_state` */
	orbitProperty: string | null;
	/** extra STAC `query` clauses applied to every scene search (e.g. Landsat 8/9 only) */
	stacQuery: Record<string, Record<string, unknown>>;
	/** visible red, green, blue asset keys for true-color plates; null for radar */
	rgbAssets: [string, string, string] | null;
}

export interface PlatformsFile {
	schemaVersion: number;
	defaultPlatform: string;
	entries: PlatformDef[];
}

export type ColormapType = 'sequential' | 'diverging' | 'cyclic' | 'categorical';

/** Upstream provenance (from the vendored colorcurious catalog, or hand-entered). */
export interface ColormapSource {
	collection: string;
	catalogId: string | null;
	attribution: string;
	license: string;
	url: string;
}

export interface ColormapDef {
	/** Pool name: `ns:name`, `colorcurious:<collection>.<name>`, or `custom:<name>`. */
	name: string;
	shortName: string;
	/** Upstream family shown in the picker (`crameri`, `cmocean`, `custom`, ...). */
	collection: string;
	/** Row in colormaps_lut.png; `null` for categorical palettes (no LUT row). */
	row: number | null;
	type: ColormapType;
	semanticRegister: string;
	printSafety: 'safe' | 'watchlist' | 'unsafe' | 'unverified';
	pairsWith: string[];
	status: ReviewStatus;
	previewStops: string[];
	tags: string[];
	source: ColormapSource | null;
	/** True when the pool entry carries inline color stops. */
	hasColors: boolean;
	notes: string;
}

export interface ColormapsFile {
	schemaVersion: number;
	lutImage: string;
	lutWidth: number;
	entries: ColormapDef[];
}

export interface LocationDef {
	id: string;
	name: string;
	bbox: [number, number, number, number];
	landscapeCharacter: string;
	themes: Theme[];
	seasonWindows: string[];
	cloudCoverMax: number;
	source: string;
	status: ReviewStatus;
	notes: string;
	reviewerNotes: string;
}

export interface LocationsFile {
	schemaVersion: number;
	entries: LocationDef[];
}

export interface IndexFixturesFile {
	schemaVersion: number;
	seed: number;
	fixtures: Record<string, { bands: Record<string, number>; value: number | null }[]>;
}

export type Triple = [number, number, number];

/** Black/white points of a true-color plate, in reflectance (src/spectralcular/truecolor.py). */
export interface TrueColorStretch {
	/**
	 * linked: one window for r, g, b; dark-object: a black point per band with
	 * one shared span (haze offset removed, gains equal); per-channel: a window per band
	 */
	mode: 'linked' | 'dark-object' | 'per-channel';
	/** auto: lo/hi re-derived from the frame's pLo/pHi percentiles; manual: kept as given */
	source: 'auto' | 'manual';
	pLo: number;
	pHi: number;
	/** null until resolved (auto presets before the frame's stats arrive) */
	lo: Triple | null;
	hi: Triple | null;
}

/** Everything that turns three reflectance bands into a true-color plate. */
export interface TrueColorSpec {
	/** id of the preset this started from (informational) */
	preset: string | null;
	stretch: TrueColorStretch;
	/** t ** (1/gamma) */
	gamma: number;
	/** sigmoidal strength; 0 = off */
	contrast: number;
	midpoint: number;
	/** OKLab chroma scale; 1 = unchanged */
	saturation: number;
}

export interface TrueColorPreset extends TrueColorSpec {
	id: string;
	label: string;
	description: string;
}

export interface TrueColorFile {
	schemaVersion: number;
	specVersion: number;
	default: string;
	presets: TrueColorPreset[];
}

export interface TrueColorFixturesFile {
	schemaVersion: number;
	specVersion: number;
	seed: number;
	cases: { spec: TrueColorSpec; reflectance: Triple[]; rgb: Triple[] }[];
}
