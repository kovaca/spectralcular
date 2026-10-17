import { PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers';
import type { Layer, PickingInfo } from '@deck.gl/core';
import type { PageSpec } from '$lib/book/page';
import { frameCorners, frameInset, frameHeightM, pageToLonLat, lonLatToPage, normalizeRotation, type FrameModel } from '$lib/geo/frame';
import { localToLonLat, lonLatToLocal, type LonLat } from '$lib/geo/tangent';

/**
 * The page frame as deck.gl layers: a dimmed world outside the sheet, the
 * bleed edge, trim line, safety margin and gutter guides, printer's crop
 * marks at the trim corners, corner handles for resizing and a rotate handle.
 *
 * Interaction model (all via deck's layer drag callbacks):
 *  - drag the sheet          → move
 *  - drag a corner handle    → resize about the center (aspect locked)
 *  - drag the rotate handle  → rotate; hold Shift to snap to 15°
 */

export type DragPhase = 'start' | 'move' | 'end';

export interface FrameEditorOptions {
	frame: FrameModel;
	page: PageSpec;
	/** CSS hex like #d9a441 — the colormap's ink */
	accent: string;
	plateLabel: string;
	interactive: boolean;
	/** which side is the spine: gutter guide is drawn there */
	spine: 'left' | 'right';
	onChange: (frame: FrameModel, phase: DragPhase) => void;
	onDragStateChange?: (dragging: boolean) => void;
}

const IN_TO_M = (page: PageSpec, frame: FrameModel) => frame.widthM / page.plateWidthIn;

function hexToRgb(hex: string): [number, number, number] {
	const h = hex.replace('#', '');
	const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
	return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const PAPER: [number, number, number] = [239, 233, 223];
const INK: [number, number, number] = [16, 18, 20];

type Drag =
	| { kind: 'move'; startPointer: LonLat; startCenter: LonLat }
	| { kind: 'resize' }
	| { kind: 'rotate' };

let drag: Drag | null = null;

export function frameLayers(o: FrameEditorOptions): Layer[] {
	const { frame, page } = o;
	const aspect = page.aspect;
	const accent = hexToRgb(o.accent);
	const corners = frameCorners(frame, aspect);
	const heightM = frameHeightM(frame, aspect);
	const m = IN_TO_M(page, frame); // meters per page inch
	const bleed = page.bleedIn * m;
	const safety = (page.bleedIn + page.safetyMarginIn) * m;
	const gutter = (page.bleedIn + page.gutterIn) * m;

	const trim = frameInset(frame, aspect, { left: bleed, right: bleed, top: bleed, bottom: bleed });
	const safe = frameInset(frame, aspect, { left: safety, right: safety, top: safety, bottom: safety });
	const gutterLine = frameInset(frame, aspect, {
		left: o.spine === 'left' ? gutter : 0,
		right: o.spine === 'right' ? gutter : 0,
		top: 0,
		bottom: 0
	});
	const gutterPath = o.spine === 'left' ? [gutterLine[0], gutterLine[3]] : [gutterLine[1], gutterLine[2]];

	// Crop marks: short strokes outside the bleed, aligned with the trim edges.
	const markLen = 0.28 * m;
	const markGap = page.bleedIn * m + 0.06 * m;
	const hw = frame.widthM / 2;
	const hh = heightM / 2;
	const tx = hw - bleed; // trim half-width
	const ty = hh - bleed;
	const marks: LonLat[][] = [];
	for (const sx of [-1, 1]) {
		for (const sy of [-1, 1]) {
			// horizontal stroke beyond the vertical edge
			marks.push([pageToLonLat(frame, sx * (hw + 0.06 * m), sy * ty), pageToLonLat(frame, sx * (hw + 0.06 * m + markLen), sy * ty)]);
			// vertical stroke beyond the horizontal edge
			marks.push([pageToLonLat(frame, sx * tx, sy * (hh + 0.06 * m)), pageToLonLat(frame, sx * tx, sy * (hh + 0.06 * m + markLen))]);
		}
	}
	void markGap;

	const rotateHandle = pageToLonLat(frame, 0, hh + 0.9 * m);
	const rotateStem: LonLat[] = [pageToLonLat(frame, 0, hh), rotateHandle];

	const world: LonLat[] = [
		[-179.999, -85],
		[179.999, -85],
		[179.999, 85],
		[-179.999, 85]
	];

	const commit = (f: FrameModel, phase: DragPhase) => o.onChange(f, phase);

	const beginDrag = (d: Drag) => {
		drag = d;
		o.onDragStateChange?.(true);
	};
	const endDrag = (info: PickingInfo) => {
		if (!drag) return;
		drag = null;
		o.onDragStateChange?.(false);
		commit(frame, 'end');
		void info;
	};

	const layers: Layer[] = [
		new PolygonLayer<{ polygon: LonLat[][] }>({
			id: 'frame-dim',
			data: [{ polygon: [world, [...corners].reverse()] }],
			getPolygon: (d) => d.polygon,
			getFillColor: [...INK, 70],
			stroked: false,
			filled: true,
			pickable: false
		}),
		new PolygonLayer<{ polygon: LonLat[] }>({
			id: 'frame-sheet',
			data: [{ polygon: corners }],
			getPolygon: (d) => d.polygon,
			getFillColor: [0, 0, 0, 1],
			getLineColor: [...PAPER, 235],
			getLineWidth: 1.5,
			lineWidthUnits: 'pixels',
			stroked: true,
			filled: true,
			pickable: o.interactive,
			onDragStart: (info) => {
				if (!info.coordinate) return;
				beginDrag({ kind: 'move', startPointer: info.coordinate as LonLat, startCenter: frame.center });
				commit(frame, 'start');
			},
			onDrag: (info) => {
				if (!drag || drag.kind !== 'move' || !info.coordinate) return;
				const [e0, n0] = lonLatToLocal(drag.startCenter, drag.startPointer);
				const [e1, n1] = lonLatToLocal(drag.startCenter, info.coordinate as LonLat);
				commit({ ...frame, center: localToLonLat(drag.startCenter, e1 - e0, n1 - n0) }, 'move');
			},
			onDragEnd: endDrag
		}),
		new PathLayer<{ path: LonLat[]; kind: string }>({
			id: 'frame-guides',
			data: [
				{ path: [...trim, trim[0]], kind: 'trim' },
				{ path: [...safe, safe[0]], kind: 'safe' },
				{ path: gutterPath, kind: 'gutter' }
			],
			getPath: (d) => d.path,
			getColor: (d) => (d.kind === 'trim' ? [...PAPER, 140] : d.kind === 'gutter' ? [...accent, 200] : [...PAPER, 60]),
			getWidth: (d) => (d.kind === 'gutter' ? 2 : 1),
			widthUnits: 'pixels',
			pickable: false
		}),
		new PathLayer<{ path: LonLat[] }>({
			id: 'frame-cropmarks',
			data: [...marks, rotateStem].map((path) => ({ path })),
			getPath: (d) => d.path,
			getColor: [...PAPER, 230],
			getWidth: 1.25,
			widthUnits: 'pixels',
			capRounded: false,
			pickable: false
		}),
		new TextLayer<{ position: LonLat; text: string }>({
			id: 'frame-label',
			data: [{ position: pageToLonLat(frame, -tx, ty + 0.16 * m), text: o.plateLabel }],
			getPosition: (d) => d.position,
			getText: (d) => d.text,
			getSize: 11,
			getColor: [...PAPER, 240],
			getAngle: -frame.rotationDeg,
			getTextAnchor: 'start',
			getAlignmentBaseline: 'bottom',
			billboard: false,
			fontFamily: 'IBM Plex Mono, ui-monospace, monospace',
			fontWeight: 500,
			characterSet: 'auto',
			pickable: false
		})
	];

	if (o.interactive) {
		layers.push(
			new ScatterplotLayer<{ position: LonLat; i: number }>({
				id: 'frame-corners',
				data: corners.map((position, i) => ({ position, i })),
				getPosition: (d) => d.position,
				getRadius: 6,
				radiusUnits: 'pixels',
				getFillColor: [...INK, 255],
				getLineColor: [...PAPER, 255],
				getLineWidth: 1.5,
				lineWidthUnits: 'pixels',
				stroked: true,
				pickable: true,
				onDragStart: () => {
					beginDrag({ kind: 'resize' });
					commit(frame, 'start');
				},
				onDrag: (info) => {
					if (!drag || drag.kind !== 'resize' || !info.coordinate) return;
					const [x, y] = lonLatToPage(frame, info.coordinate as LonLat);
					const w = Math.max(500, 2 * Math.max(Math.abs(x), Math.abs(y) * aspect));
					commit({ ...frame, widthM: w }, 'move');
				},
				onDragEnd: endDrag
			}),
			new ScatterplotLayer<{ position: LonLat }>({
				id: 'frame-rotate',
				data: [{ position: rotateHandle }],
				getPosition: (d) => d.position,
				getRadius: 7,
				radiusUnits: 'pixels',
				getFillColor: [...accent, 255],
				getLineColor: [...PAPER, 255],
				getLineWidth: 1.5,
				lineWidthUnits: 'pixels',
				stroked: true,
				pickable: true,
				onDragStart: () => {
					beginDrag({ kind: 'rotate' });
					commit(frame, 'start');
				},
				onDrag: (info) => {
					if (!drag || drag.kind !== 'rotate' || !info.coordinate) return;
					const [e, n] = lonLatToLocal(frame.center, info.coordinate as LonLat);
					let bearing = (Math.atan2(e, n) * 180) / Math.PI;
					const ev = (info as PickingInfo & { srcEvent?: { shiftKey?: boolean } }).srcEvent;
					if (ev?.shiftKey) bearing = Math.round(bearing / 15) * 15;
					commit({ ...frame, rotationDeg: normalizeRotation(bearing) }, 'move');
				},
				onDragEnd: endDrag
			})
		);
	}
	return layers;
}
