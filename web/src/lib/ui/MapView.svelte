<script lang="ts">
	import { MapLibre, NavigationControl, ScaleControl } from 'svelte-maplibre-gl';
	import { DeckGLOverlay } from '@svelte-maplibre-gl/deckgl';
	import type { Map as MaplibreMap } from 'maplibre-gl';
	import type { Layer, PickingInfo } from '@deck.gl/core';
	import { ScatterplotLayer, TextLayer } from '@deck.gl/layers';
	import { getSession } from '$lib/state/session.svelte';
	import { getProjectStore } from '$lib/state/project.svelte';
	import { getColormap, locations, themeRgb, type LocationDef } from '$lib/data';
	import { plateTheme } from '$lib/export/manifest';
	import { BASEMAP_STYLE } from '$lib/geo/basemap';
	import type { Plate } from '$lib/state/schema';
	import { PlateLayer } from '$lib/raster/gpu/PlateLayer';
	import { planSlots } from '$lib/raster/formula/resolve';
	import { frameLayers } from './FrameEditor';
	import { PAGE } from '$lib/book/page';

	const session = getSession();
	const project = getProjectStore();

	let {
		onPickLocation,
		showPlates = false,
		focusIds = [],
		onOpenPlate
	}: {
		onPickLocation: (loc: LocationDef) => void;
		/** draw every saved plate as a numbered dot (while the Overview tab is open) */
		showPlates?: boolean;
		/** plates the Overview is focused on; drawn larger, the rest recede */
		focusIds?: string[];
		onOpenPlate?: (p: Plate) => void;
	} = $props();

	let gpuError = $state<string | null>(null);
	let map = $state<MaplibreMap | undefined>();
	/** Fingers are wider than cursors: widen the picking radius so frame handles can be grabbed. */
	const coarse = typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches;

	$effect(() => {
		if (map) session.map = map;
	});

	function onStyleLoad(m: MaplibreMap) {
		const first = m.getStyle().layers?.find((l: { type: string; id: string }) => l.type === 'symbol');
		session.labelLayerId = first?.id ?? null;
	}

	// Derived must stay pure: return the layer *and* any build error together.
	const raster = $derived.by((): { layer: Layer | null; error: string | null } => {
		const scene = session.draft.scene;
		if (scene && session.isTrueColor) {
			const tone = session.draft.truecolor;
			const platform = session.scenePlatform;
			if (!tone || !platform) return { layer: null, error: null };
			if (session.trueColorBlocker) return { layer: null, error: `True color: ${session.trueColorBlocker}` };
			const layer = new PlateLayer({
				id: 'plate-raster',
				kind: 'truecolor',
				sceneId: scene.itemId,
				hrefs: scene.assetHrefs,
				platform,
				tone,
				maskEnabled: session.draft.maskEnabled,
				beforeId: session.labelLayerId ?? undefined,
				onBuildError: (m: string) => queueMicrotask(() => (gpuError = m))
			});
			return { layer, error: null };
		}
		const def = session.indexDef;
		const cmap = session.colormapDef;
		const range = session.range;
		if (!scene || !def || !cmap || !range) return { layer: null, error: null };
		const plan = planSlots(def, { wantMask: session.draft.maskEnabled });
		if (!plan.gpuSupported) return { layer: null, error: `${def.name}: ${plan.reason} — no live preview, stats still work` };
		if (scene.collection !== plan.platform.collection) {
			return { layer: null, error: `${def.name} needs a ${plan.platform.label} scene; the pinned scene is ${scene.collection}. Search scenes again.` };
		}
		const layer = new PlateLayer({
			id: 'plate-raster',
			kind: 'index',
			sceneId: scene.itemId,
			hrefs: scene.assetHrefs,
			platform: plan.platform,
			index: def,
			colormapRow: cmap.row ?? 0,
			reversed: session.draft.colormap.reversed,
			vmin: range.vmin,
			vmax: range.vmax,
			maskEnabled: session.draft.maskEnabled,
			beforeId: session.labelLayerId ?? undefined,
			onBuildError: (m: string) => queueMicrotask(() => (gpuError = m))
		});
		return { layer, error: null };
	});
	const rasterLayer = $derived(raster.layer);
	const rasterError = $derived(raster.error ?? gpuError);

	const locationLayer = $derived(
		new ScatterplotLayer<LocationDef>({
			id: 'locations',
			data: locations,
			getPosition: (d) => [(d.bbox[0] + d.bbox[2]) / 2, (d.bbox[1] + d.bbox[3]) / 2],
			getRadius: (d) => (d.status === 'approved' ? 6 : 4),
			radiusUnits: 'pixels',
			radiusMinPixels: 3,
			getFillColor: (d) => [...themeRgb(d.themes[0]), d.status === 'approved' ? 230 : 150],
			opacity: showPlates ? 0.35 : 1,
			getLineColor: [26, 26, 26, 200],
			getLineWidth: 1,
			lineWidthUnits: 'pixels',
			stroked: true,
			pickable: true,
			autoHighlight: true,
			highlightColor: [154, 51, 36, 255],
			onClick: (info: PickingInfo<LocationDef>) => {
				if (info.object) onPickLocation(info.object);
			},
			updateTriggers: {}
		})
	);

	const ord = (p: Plate) => String(p.ordinal).padStart(3, '0');
	const focusSet = $derived(new Set(focusIds));
	/** dim a plate when the Overview is focused on others */
	const dimmed = (p: Plate) => focusSet.size > 0 && !focusSet.has(p.id);
	const plateLayers = $derived.by((): Layer[] => {
		if (!showPlates || project.plates.length === 0) return [];
		const data = project.plates;
		const focusKey = focusIds.join(',');
		return [
			new ScatterplotLayer<Plate>({
				id: 'plates',
				data,
				getPosition: (p) => p.frame.center,
				getRadius: (p) => (focusSet.has(p.id) ? 8 : 6),
				radiusUnits: 'pixels',
				getFillColor: (p) => [...themeRgb(plateTheme(p)), dimmed(p) ? 70 : 240],
				getLineColor: (p) => [154, 51, 36, dimmed(p) ? 80 : 255],
				getLineWidth: (p) => (focusSet.has(p.id) ? 2 : 1.25),
				lineWidthUnits: 'pixels',
				stroked: true,
				pickable: true,
				autoHighlight: true,
				highlightColor: [154, 51, 36, 255],
				onClick: (info: PickingInfo<Plate>) => {
					if (info.object) onOpenPlate?.(info.object);
				},
				updateTriggers: { getRadius: focusKey, getFillColor: focusKey, getLineColor: focusKey, getLineWidth: focusKey }
			}),
			new TextLayer<Plate>({
				id: 'plate-labels',
				data,
				getPosition: (p) => p.frame.center,
				getText: ord,
				getPixelOffset: [11, 0],
				getTextAnchor: 'start',
				getAlignmentBaseline: 'center',
				fontFamily: 'Menlo, ui-monospace, monospace',
				getSize: 10.5,
				getColor: (p) => [26, 26, 26, dimmed(p) ? 90 : 255],
				background: true,
				getBackgroundColor: (p) => [247, 244, 236, dimmed(p) ? 90 : 225],
				backgroundPadding: [3, 1],
				updateTriggers: { getColor: focusKey, getBackgroundColor: focusKey }
			})
		];
	});

	const plateLabel = $derived.by(() => {
		const p = session.editingPlateId ? project.plates.find((x) => x.id === session.editingPlateId) : null;
		const n = p ? String(p.ordinal).padStart(3, '0') : '—';
		return `${n}  ${session.draft.location.name}  ·  ${session.isTrueColor ? 'True color' : session.draft.index}`;
	});

	const layers = $derived.by((): Layer[] => {
		const out: Layer[] = [];
		if (rasterLayer) out.push(rasterLayer);
		out.push(locationLayer);
		out.push(...plateLayers);
		out.push(
			...frameLayers({
				frame: session.draft.frame,
				page: PAGE,
				accent: session.accent,
				plateLabel,
				interactive: true,
				spine: 'left',
				onChange: (f, phase) => {
					session.setFrame(f);
					if (phase === 'end') session.refreshStats();
				},
				onDragStateChange: (dragging) => {
					session.frameDragging = dragging;
					const m = session.map;
					if (!m) return;
					if (dragging) {
						m.dragPan.disable();
						m.dragRotate.disable();
					} else {
						m.dragPan.enable();
						m.dragRotate.enable();
					}
				}
			})
		);
		return out;
	});

	function getTooltip(info: PickingInfo) {
		if (info.layer?.id === 'locations' && info.object) {
			const l = info.object as LocationDef;
			return { text: `${l.name}\n${l.themes.join(', ')} · ${l.status}` };
		}
		if (info.layer?.id === 'plates' && info.object) {
			const p = info.object as Plate;
			const look = p.kind === 'truecolor' ? 'True color' : `${p.index} · ${getColormap(p.colormap.name)?.shortName ?? p.colormap.name}${p.colormap.reversed ? ' ⇄' : ''}`;
			return { text: `${ord(p)}  ${p.location.name}\n${look} · ${p.scene ? p.scene.datetime.slice(0, 10) : 'no scene'}` };
		}
		return null;
	}
</script>

<div class="map">
	<MapLibre
		autoloadGlobalCss={false}
		bind:map
		style={BASEMAP_STYLE}
		center={[15.0, 37.75]}
		zoom={9}
		maxPitch={0}
		attributionControl={{ compact: true }}
		onload={(e) => onStyleLoad(e.target)}
	>
		<NavigationControl showCompass={true} visualizePitch={false} position="bottom-right" />
		<ScaleControl unit="metric" position="bottom-left" />
		<DeckGLOverlay interleaved={true} {layers} {getTooltip} getCursor={({ isDragging, isHovering }) => (isDragging ? 'grabbing' : isHovering ? 'pointer' : 'grab')} />
	</MapLibre>
	{#if rasterError}
		<div class="gpu-error mono">{rasterError}</div>
	{/if}
</div>

<style>
	.map { position: relative; width: 100%; height: 100%; background: var(--paper-edge); }
	.map :global(.maplibregl-map) { position: absolute; inset: 0; font-family: var(--mono); }
	.map :global(.maplibregl-ctrl-top-right) { top: 62px; }
	.map :global(.maplibregl-ctrl-bottom-right) { bottom: 66px; }
	.map :global(.maplibregl-ctrl-bottom-left) { bottom: 66px; }
	.map :global(.maplibregl-ctrl-group) { background: var(--paper); border: var(--hair) solid var(--rule); border-radius: 0; box-shadow: none; }
	.map :global(.maplibregl-ctrl-group button + button) { border-top: var(--hair) solid var(--rule); }
	.map :global(.maplibregl-ctrl-scale) { background: color-mix(in oklab, var(--paper) 88%, transparent); color: var(--ink-soft); border-color: var(--ink-mute); font-family: var(--mono); font-size: 10px; }
	@media (max-width: 1023px) {
		.map :global(.maplibregl-ctrl-top-right) { top: 96px; }
		.map :global(.maplibregl-ctrl-bottom-right) { bottom: 58px; }
		.map :global(.maplibregl-ctrl-bottom-left) { bottom: 58px; }
		.map :global(.maplibregl-ctrl-attrib) { font-size: 9px; }
	}
	.gpu-error { position: absolute; left: 12px; bottom: 36px; background: var(--paper); color: var(--bad); border: var(--hair) solid var(--bad); padding: 6px 10px; font-family: var(--mono); font-size: 11px; max-width: 60%; }
</style>
