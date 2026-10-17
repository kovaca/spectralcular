/**
 * Scene fit: will this index compute from the pinned scene?
 *
 * Three questions, answered from data the plate already carries, so no network
 * round-trip is needed: is there a scene at all; does it come from the
 * platform the index is defined on; and does the item actually ship every
 * asset the formula reads (Sentinel-1 RTC frames are VV/VH at mid-latitudes
 * but HH/HV over the poles; Landsat 4–7 items name the thermal band `lwir`).
 * The UI turns the answer into a quiet visual register on the index chips.
 */
import { platformForIndex } from './index';
import type { IndexDef, PlatformDef } from './types';

/** The subset of a Scene the fit check needs (keeps this module free of the state layer). */
export interface SceneLike {
	collection: string;
	assetHrefs: Record<string, string>;
}

export type FitKind = 'no-scene' | 'ok' | 'other-platform' | 'missing-assets';

export interface Fit {
	kind: FitKind;
	/** platform the index is defined on */
	platform: PlatformDef;
	/** asset keys the formula needs but the scene lacks (only for `missing-assets`) */
	missing: string[];
	/** short human note for tooltips and pills; empty when the index fits */
	note: string;
}

/** Band code (`B10`, `VV`) for an asset on a platform, for notes. */
export function assetCode(platform: PlatformDef, asset: string): string {
	const b = platform.bandsNm?.[asset];
	return b ? b[0] : asset.toUpperCase();
}

export function indexFitForScene(def: IndexDef, scene: SceneLike | null): Fit {
	const platform = platformForIndex(def);
	if (!scene) return { kind: 'no-scene', platform, missing: [], note: '' };
	if (scene.collection !== platform.collection) {
		return { kind: 'other-platform', platform, missing: [], note: `needs a ${platform.label} scene` };
	}
	const assets = [...new Set(Object.values(def.bands))];
	const missing = assets.filter((a) => !scene.assetHrefs[a]);
	if (missing.length) {
		const codes = missing.map((a) => assetCode(platform, a)).join(' · ');
		return { kind: 'missing-assets', platform, missing, note: `scene has no ${codes}` };
	}
	return { kind: 'ok', platform, missing: [], note: '' };
}

export const fits = (f: Fit): boolean => f.kind === 'ok' || f.kind === 'no-scene';

/** Why a true-color plate can't be drawn from this platform/scene (null: it can). */
export function trueColorBlockerFor(platform: PlatformDef | null, scene: SceneLike | null): string | null {
	if (!platform) return scene ? `No platform is registered for ${scene.collection}` : null;
	if (!platform.rgbAssets) return `${platform.label} has no visible red, green and blue bands`;
	if (scene) {
		const missing = platform.rgbAssets.filter((a) => !scene.assetHrefs[a]);
		if (missing.length) return `The pinned scene has no ${missing.join('/')} asset`;
	}
	return null;
}
