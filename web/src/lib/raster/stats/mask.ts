import type { PlatformDef } from '$lib/data/types';
import type { MaskRequest } from './types';

/** Bit 0 marks no-data in both mask encodings (SCL class 0, QA_PIXEL fill bit). */
export const NODATA_BITS = 1 << 0;

/** Bits set for a list of class ids / bit positions. */
export function bitmask(values: readonly number[]): number {
	return values.reduce((m, c) => m | (1 << c), 0);
}

/** The stats worker's view of a platform's quality mask (null when the platform has none). */
export function maskRequestFor(platform: PlatformDef): MaskRequest | null {
	if (!platform.mask) return null;
	return { asset: platform.mask.asset, kind: platform.mask.kind, bits: bitmask(platform.mask.values), nodataBits: NODATA_BITS };
}
