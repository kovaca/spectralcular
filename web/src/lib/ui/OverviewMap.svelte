<script lang="ts">
	import { MapLibre, NavigationControl, GeoJSONSource, CircleLayer, SymbolLayer } from 'svelte-maplibre-gl';
	import { LngLatBounds, type Map as MaplibreMap, type MapLayerMouseEvent } from 'maplibre-gl';
	import { themeCss } from '$lib/data';
	import { BASEMAP_STYLE } from '$lib/geo/basemap';
	import type { LonLat } from '$lib/geo/tangent';
	import type { OverviewPoint } from '$lib/state/overview';

	/**
	 * A small locator for the whole book: one dot per place, sized by how many
	 * plates it holds, plus a ring where the current draft is. Native MapLibre
	 * layers keep it to one WebGL context and no deck.gl.
	 */
	let {
		points,
		focusIds,
		draft,
		onFocus
	}: {
		points: OverviewPoint[];
		focusIds: string[];
		draft: LonLat | null;
		onFocus: (locationId: string) => void;
	} = $props();

	let map = $state<MaplibreMap | undefined>();
	let hover = $state<string | null>(null);
	const coarse = typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches;

	const focusSet = $derived(new Set(focusIds));
	const data = $derived<GeoJSON.FeatureCollection>({
		type: 'FeatureCollection',
		features: points.map((p) => ({
			type: 'Feature',
			geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
			properties: {
				locationId: p.locationId,
				name: p.name,
				n: p.plateIds.length,
				label: p.plateIds.length > 1 ? String(p.plateIds.length) : '',
				color: themeCss(p.theme),
				focus: p.plateIds.some((id) => focusSet.has(id)),
				dim: focusSet.size > 0 && !p.plateIds.some((id) => focusSet.has(id))
			}
		}))
	});
	const draftData = $derived<GeoJSON.FeatureCollection>({
		type: 'FeatureCollection',
		features: draft ? [{ type: 'Feature', geometry: { type: 'Point', coordinates: draft }, properties: {} }] : []
	});

	/** Refit only when the set of places changes, not on every focus change. */
	const placesKey = $derived(points.map((p) => `${p.locationId}@${p.lon.toFixed(3)},${p.lat.toFixed(3)}`).join('|'));
	$effect(() => {
		const m = map;
		void placesKey;
		if (!m) return;
		if (points.length === 0) {
			m.jumpTo({ center: draft ?? [10, 25], zoom: 0.4 });
			return;
		}
		if (points.length === 1) {
			m.jumpTo({ center: [points[0].lon, points[0].lat], zoom: 4 });
			return;
		}
		const b = new LngLatBounds();
		for (const p of points) b.extend([p.lon, p.lat]);
		m.fitBounds(b, { padding: { top: 22, bottom: 22, left: 22, right: 40 }, maxZoom: 5, duration: 0 });
	});

	function onClick(e: MapLayerMouseEvent) {
		const id = e.features?.[0]?.properties?.locationId;
		if (typeof id === 'string') onFocus(id);
	}
	function onMove(e: MapLayerMouseEvent) {
		const f = e.features?.[0]?.properties;
		hover = f ? `${f.name} · ${f.n} plate${f.n === 1 ? '' : 's'}` : null;
		if (map) map.getCanvas().style.cursor = f ? 'pointer' : '';
	}
	function onLeave() {
		hover = null;
		if (map) map.getCanvas().style.cursor = '';
	}
</script>

<div class="mini" role="region" aria-label="Map of the book's places">
	<MapLibre
		autoloadGlobalCss={false}
		bind:map
		style={BASEMAP_STYLE}
		center={[10, 25]}
		zoom={0.4}
		minZoom={-2}
		maxPitch={0}
		dragRotate={false}
		touchPitch={false}
		pitchWithRotate={false}
		scrollZoom={false}
		cooperativeGestures={coarse}
		attributionControl={false}
	>
		<NavigationControl showCompass={false} position="top-right" />
		<GeoJSONSource id="overview-draft" data={draftData}>
			<CircleLayer id="overview-draft-ring" paint={{ 'circle-radius': 9, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#9a3324', 'circle-stroke-width': 1.25 }} />
		</GeoJSONSource>
		<GeoJSONSource id="overview-places" {data}>
			<CircleLayer
				id="overview-dots"
				paint={{
					'circle-color': ['get', 'color'],
					'circle-radius': ['interpolate', ['linear'], ['get', 'n'], 1, 4.5, 6, 9],
					'circle-opacity': ['case', ['get', 'dim'], 0.25, 1],
					'circle-stroke-color': ['case', ['get', 'focus'], '#9a3324', '#1a1a1a'],
					'circle-stroke-width': ['case', ['get', 'focus'], 2, 0.75],
					'circle-stroke-opacity': ['case', ['get', 'dim'], 0.3, 1]
				}}
				onclick={onClick}
				onmousemove={onMove}
				onmouseleave={onLeave}
			/>
			<SymbolLayer
				id="overview-counts"
				layout={{
					'text-field': ['get', 'label'],
					'text-font': ['Noto Sans Regular'],
					'text-size': 10,
					'text-offset': [0.95, 0],
					'text-anchor': 'left',
					'text-allow-overlap': true
				}}
				paint={{ 'text-color': '#1a1a1a', 'text-halo-color': '#f7f4ec', 'text-halo-width': 1.4, 'text-opacity': ['case', ['get', 'dim'], 0.35, 1] }}
			/>
		</GeoJSONSource>
	</MapLibre>
	{#if hover}<div class="hover mono">{hover}</div>{/if}
</div>
<!-- At this size MapLibre's attribution control would cover the dots; credit the basemap below instead. -->
<p class="credit mono">© <a href="https://openfreemap.org" target="_blank" rel="noreferrer">OpenFreeMap</a> © <a href="https://www.openmaptiles.org/" target="_blank" rel="noreferrer">OpenMapTiles</a> © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a></p>

<style>
	.mini { position: relative; height: 180px; border: var(--hair) solid var(--rule); background: var(--paper-edge); }
	.mini :global(.maplibregl-map) { position: absolute; inset: 0; font-family: var(--mono); }
	.mini :global(.maplibregl-ctrl-group) { background: var(--paper); border: var(--hair) solid var(--rule); border-radius: 0; box-shadow: none; }
	.mini :global(.maplibregl-ctrl-group button) { width: 24px; height: 24px; }
	.mini :global(.maplibregl-ctrl-group button + button) { border-top: var(--hair) solid var(--rule); }
	.mini :global(.maplibregl-ctrl-top-right) { top: 0; }
	.credit { margin: 2px 0 0; font-size: 8.5px; color: var(--ink-mute); text-align: right; }
	.credit a { color: inherit; text-decoration: none; }
	.credit a:hover { text-decoration: underline; }
	.hover { position: absolute; left: 6px; top: 6px; z-index: 2; padding: 2px 6px; background: color-mix(in oklab, var(--paper) 92%, transparent); border: var(--hair) solid var(--rule); font-size: 10px; color: var(--ink); pointer-events: none; }
	@media (pointer: coarse) { .mini :global(.maplibregl-ctrl-group button) { width: 32px; height: 32px; } }
</style>
