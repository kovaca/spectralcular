import bookJson from './book.json';
import indicesJson from './indices.json';
import colormapsJson from './colormaps.json';
import locationsJson from './locations.json';
import platformsJson from './platforms.json';
import truecolorJson from './truecolor.json';
import lutUrl from './colormaps_lut.png';
import type {
	BookSpec,
	ColormapDef,
	ColormapsFile,
	IndexDef,
	IndicesFile,
	LocationDef,
	LocationsFile,
	PlatformDef,
	PlatformsFile,
	ReferenceSurface,
	TrueColorFile,
	TrueColorPreset
} from './types';
export type * from './types';

export const book = bookJson as BookSpec;
export const indicesFile = indicesJson as unknown as IndicesFile;
export const colormapsFile = colormapsJson as unknown as ColormapsFile;
export const locationsFile = locationsJson as unknown as LocationsFile;
export const platformsFile = platformsJson as unknown as PlatformsFile;
export const colormapLutUrl: string = lutUrl;
export const truecolorFile = truecolorJson as unknown as TrueColorFile;
export const truecolorPresets: TrueColorPreset[] = truecolorFile.presets;

export const indices: IndexDef[] = indicesFile.entries;
export const colormaps: ColormapDef[] = colormapsFile.entries;
export const locations: LocationDef[] = locationsFile.entries;
export const platforms: PlatformDef[] = platformsFile.entries;
export const referenceSurfaces: ReferenceSurface[] = indicesFile.surfaces ?? [];
export const DEFAULT_PLATFORM_ID: string = platformsFile.defaultPlatform;

const indexByName = new Map(indices.map((d) => [d.name, d]));
const colormapByName = new Map(colormaps.map((c) => [c.name, c]));
const locationById = new Map(locations.map((l) => [l.id, l]));
const platformById = new Map(platforms.map((p) => [p.id, p]));

export const getIndex = (name: string): IndexDef | undefined => indexByName.get(name);
export const getColormap = (name: string): ColormapDef | undefined => colormapByName.get(name);
export const getLocation = (id: string): LocationDef | undefined => locationById.get(id);
export const getPlatform = (id: string): PlatformDef | undefined => platformById.get(id);
const surfaceById = new Map(referenceSurfaces.map((s) => [s.id, s]));
export const getSurface = (id: string): ReferenceSurface | undefined => surfaceById.get(id);
const presetById = new Map(truecolorPresets.map((p) => [p.id, p]));
export const getTrueColorPreset = (id: string): TrueColorPreset | undefined => presetById.get(id);
export const DEFAULT_TRUECOLOR_PRESET: TrueColorPreset = presetById.get(truecolorFile.default) ?? truecolorPresets[0];

/** Platform whose STAC collection a scene came from. */
export function platformForCollection(collection: string): PlatformDef | undefined {
	return platforms.find((p) => p.collection === collection);
}

/** Coarsest native GSD (m) of a platform's red/green/blue bands. */
export function gsdForTrueColor(platform: PlatformDef): number {
	const g = (platform.rgbAssets ?? []).map((a) => platform.gsdByAsset[a]).filter((x): x is number => typeof x === 'number');
	return g.length ? Math.max(...g) : platform.nativeResM;
}

/** Platform an index is computed from; falls back to the default (Sentinel-2). */
export function platformForIndex(def: IndexDef): PlatformDef {
	const p = platformById.get(def.platform) ?? platformById.get(DEFAULT_PLATFORM_ID);
	if (!p) throw new Error(`platforms.json has no entry for ${def.platform}`);
	return p;
}

/** [scale, offset] turning a raw DN of `asset` into a physical value on `platform`. */
export function valueScaleFor(platform: PlatformDef, asset: string): [number, number] {
	return platform.valueScale[asset] ?? platform.valueScale['*'] ?? [1, 0];
}

/** Nodata DN for `asset` on `platform` (null: none declared). */
export function nodataFor(platform: PlatformDef, asset: string): number | null {
	if (asset in platform.nodata) return platform.nodata[asset];
	return platform.nodata['*'] ?? null;
}

export const THEME_ORDER = [
	'vegetation',
	'water',
	'burn',
	'soil_geology',
	'urban',
	'snow_ice',
	'atmosphere',
	'radar'
] as const;

export const THEME_LABEL: Record<(typeof THEME_ORDER)[number], string> = {
	vegetation: 'Vegetation',
	water: 'Water',
	burn: 'Burn',
	soil_geology: 'Soil & geology',
	urban: 'Urban',
	snow_ice: 'Snow & ice',
	atmosphere: 'Atmosphere',
	radar: 'Radar'
};

/** Theme ink for location dots, plate markers and overview marks (one table for map and rail). */
export const THEME_RGB: Record<(typeof THEME_ORDER)[number], [number, number, number]> = {
	vegetation: [95, 143, 69],
	water: [61, 127, 184],
	burn: [194, 91, 44],
	soil_geology: [168, 131, 74],
	urban: [138, 138, 138],
	snow_ice: [127, 176, 201],
	atmosphere: [155, 127, 184],
	radar: [111, 111, 134]
};
const NEUTRAL_RGB: [number, number, number] = [187, 187, 187];
export const themeRgb = (theme: string): [number, number, number] => THEME_RGB[theme as keyof typeof THEME_RGB] ?? NEUTRAL_RGB;
export const themeCss = (theme: string): string => `rgb(${themeRgb(theme).join(' ')})`;

/** Coarsest native ground sample distance (m) among an index's bands. */
export function gsdForIndex(def: IndexDef): number {
	const table = platformForIndex(def).gsdByAsset;
	const g = Object.values(def.bands)
		.map((a) => table[a])
		.filter((x): x is number => typeof x === 'number');
	return g.length ? Math.max(...g) : 10;
}
