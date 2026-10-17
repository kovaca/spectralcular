import type { IndexDef, PlatformDef } from '$lib/data/types';
import { platformForIndex } from '$lib/data';

/** Band textures the GPU band stack can bind (plus one dedicated mask texture). */
export const MAX_GPU_BANDS = 8;

export interface SlotPlan {
	/** band param → slot index (0..MAX_GPU_BANDS-1) */
	paramSlot: Record<string, number>;
	/** asset key → slot index (unique assets only) */
	assetSlot: Record<string, number>;
	/** slot index → asset key, in slot order */
	slotAssets: string[];
	/** platform mask asset riding in the dedicated mask texture, when wanted and available */
	maskAsset: string | null;
	/** asset keys that must be provided as sources (bands + mask) */
	sources: string[];
	platform: PlatformDef;
	gpuSupported: boolean;
	reason?: string;
}

export interface PlanOptions {
	/** bind the platform's mask asset (cloud / quality mask) when it has one */
	wantMask?: boolean;
	platform?: PlatformDef;
}

/**
 * Assign an index's bands to band-stack slots. Distinct params that point at
 * the same asset share a slot. The mask never competes with bands for a slot:
 * it has its own texture, so every GPU-renderable index can be masked.
 */
export function planSlots(def: IndexDef, opts: PlanOptions = {}): SlotPlan {
	const wantMask = opts.wantMask ?? true;
	const platform = opts.platform ?? platformForIndex(def);
	const assets: string[] = [];
	for (const p of def.params) {
		const asset = def.bands[p];
		if (!asset) return unsupported(def, platform, `param ${p} has no ${platform.label} asset`);
		if (!assets.includes(asset)) assets.push(asset);
	}
	if (assets.length === 0) return unsupported(def, platform, 'index uses no bands');
	if (assets.length > MAX_GPU_BANDS) {
		return unsupported(def, platform, `${assets.length} bands exceed the ${MAX_GPU_BANDS}-texture GPU limit`);
	}
	if (!def.computable) return unsupported(def, platform, 'formula has constants without defaults');

	const assetSlot: Record<string, number> = {};
	assets.forEach((a, i) => (assetSlot[a] = i));
	const paramSlot: Record<string, number> = {};
	for (const p of def.params) paramSlot[p] = assetSlot[def.bands[p]];

	const maskAsset = wantMask && platform.mask ? platform.mask.asset : null;
	return {
		paramSlot,
		assetSlot,
		slotAssets: assets,
		maskAsset,
		sources: maskAsset ? [...assets, maskAsset] : [...assets],
		platform,
		gpuSupported: true
	};
}

function unsupported(def: IndexDef, platform: PlatformDef, reason: string): SlotPlan {
	return {
		paramSlot: {},
		assetSlot: {},
		slotAssets: [],
		maskAsset: null,
		sources: [],
		platform,
		gpuSupported: false,
		reason
	};
}
