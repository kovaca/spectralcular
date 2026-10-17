import { book } from '$lib/data';
import type { BookSpec } from '$lib/data/types';

/** Physical plate geometry. The plate fills the bleed box (trim + 2 × bleed). */
export interface PageSpec {
	plateWidthIn: number;
	plateHeightIn: number;
	/** width / height */
	aspect: number;
	dpi: number;
	trimWidthIn: number;
	trimHeightIn: number;
	bleedIn: number;
	safetyMarginIn: number;
	gutterIn: number;
}

export function pageSpecFromBook(b: BookSpec = book): PageSpec {
	return {
		plateWidthIn: b.plate.widthIn,
		plateHeightIn: b.plate.heightIn,
		aspect: b.plate.widthIn / b.plate.heightIn,
		dpi: b.dpi,
		trimWidthIn: b.trim.widthIn,
		trimHeightIn: b.trim.heightIn,
		bleedIn: b.bleedIn,
		safetyMarginIn: b.safetyMarginIn,
		gutterIn: b.gutter.fullBleedInnerMarginIn
	};
}

export const PAGE = pageSpecFromBook();

export type DpiStatus = 'full' | 'upsampled' | 'soft';

export interface PrintStats {
	widthM: number;
	heightM: number;
	gsdM: number;
	/** native pixels the frame spans at the index's coarsest band */
	nativePxW: number;
	nativePxH: number;
	/** pixels the printer wants at PAGE.dpi */
	targetPxW: number;
	targetPxH: number;
	/** what the imagery can deliver across the plate width */
	effectiveDpi: number;
	/** effectiveDpi / target dpi */
	resolutionRatio: number;
	status: DpiStatus;
	/** max ground width before the plate needs upsampling */
	ceilingWidthM: number;
	ceilingHeightM: number;
	withinCeiling: boolean;
}

/** Everything the stats panel shows about print resolution for a frame. */
export function printStats(widthM: number, gsdM: number, page: PageSpec = PAGE): PrintStats {
	const heightM = widthM / page.aspect;
	const nativePxW = widthM / gsdM;
	const nativePxH = heightM / gsdM;
	const targetPxW = Math.round(page.plateWidthIn * page.dpi);
	const targetPxH = Math.round(page.plateHeightIn * page.dpi);
	const effectiveDpi = nativePxW / page.plateWidthIn;
	const ratio = effectiveDpi / page.dpi;
	const ceilingWidthM = ceilingMeters(gsdM, page).widthM;
	const ceilingHeightM = ceilingMeters(gsdM, page).heightM;
	return {
		widthM,
		heightM,
		gsdM,
		nativePxW,
		nativePxH,
		targetPxW,
		targetPxH,
		effectiveDpi,
		resolutionRatio: ratio,
		status: dpiStatus(effectiveDpi, page.dpi),
		ceilingWidthM,
		ceilingHeightM,
		withinCeiling: widthM <= ceilingWidthM + 1e-6
	};
}

/** Largest frame that still prints at full native resolution: plate_in × dpi × gsd. */
export function ceilingMeters(gsdM: number, page: PageSpec = PAGE): { widthM: number; heightM: number } {
	return {
		widthM: page.plateWidthIn * page.dpi * gsdM,
		heightM: page.plateHeightIn * page.dpi * gsdM
	};
}

export function dpiStatus(effectiveDpi: number, targetDpi: number = PAGE.dpi): DpiStatus {
	if (effectiveDpi >= targetDpi - 1e-6) return 'full';
	if (effectiveDpi >= targetDpi / 2) return 'upsampled';
	return 'soft';
}
