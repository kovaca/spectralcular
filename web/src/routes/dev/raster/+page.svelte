<script lang="ts">
	/**
	 * Phase-0 spike: stream one Sentinel-2 item's bands from Earth Search and
	 * render a spectral index + colormap through the custom shader module.
	 */
	import { browser } from '$app/environment';
	import { MapLibre, NavigationControl } from 'svelte-maplibre-gl';
	import { DeckGLOverlay } from '@svelte-maplibre-gl/deckgl';
	import type { Device, Texture } from '@luma.gl/core';
	import type { Layer } from '@deck.gl/core';
	import { clientFor } from '$lib/stac/client';
	import { signHrefs } from '$lib/stac/sas';
	import type { StacItem } from '$lib/stac/types';
	import { indices, colormaps, colormapLutUrl, getIndex, getColormap, platforms, getPlatform, DEFAULT_PLATFORM_ID } from '$lib/data';
	import { getColormapTexture } from '$lib/raster/gpu/colormapTexture';
	import { buildIndexLayer } from '$lib/raster/gpu/buildLayer';
	import { planSlots } from '$lib/raster/formula/resolve';

	// One known-good scene per platform: Etna (S2), Phoenix (Landsat 9), Mekong delta (S1 ascending).
	const SAMPLE_ITEMS: Record<string, string> = {
		'sentinel-2-l2a': 'S2A_33SVB_20230716_0_L2A',
		'landsat-c2-l2': 'LC09_L2SP_037037_20240710_02_T1',
		'sentinel-1-rtc': 'S1A_IW_GRDH_1SDV_20240729T110334_20240729T110403_054975_06B275_rtc'
	};
	// `?platform=landsat-c2-l2&item=<id>&index=<name>` preselects a scene so a platform can be tested on a fresh load.
	const params = browser ? new URLSearchParams(location.search) : new URLSearchParams();
	const initialPlatform = getPlatform(params.get('platform') ?? '') ? (params.get('platform') as string) : DEFAULT_PLATFORM_ID;
	let platformId = $state(initialPlatform);
	const platform = $derived(getPlatform(platformId) ?? platforms[0]);
	let itemId = $state(params.get('item') ?? SAMPLE_ITEMS[initialPlatform]);
	let item = $state<StacItem | null>(null);
	/** item asset hrefs, signed for the platform's storage */
	let hrefs = $state<Record<string, string>>({});
	let center = $state<[number, number]>([15.0, 37.75]);
	let zoom = $state(10);
	let error = $state<string | null>(null);
	let texture = $state<Texture | null>(null);
	let indexName = $state(params.get('index') ?? (indices.find((d) => d.platform === initialPlatform && planSlots(d).gpuSupported)?.name ?? 'NDVI'));
	let colormapName = $state('bids:viridis');
	let reversed = $state(false);
	let vmin = $state(-0.2);
	let vmax = $state(0.8);
	let maskEnabled = $state(true);
	let valueOffset = $state(0);
	let loaded = $state(false);

	const gpuIndices = $derived(indices.filter((d) => d.platform === platformId && planSlots(d).gpuSupported));

	function switchPlatform(id: string) {
		platformId = id;
		itemId = SAMPLE_ITEMS[id] ?? '';
		const first = indices.find((d) => d.platform === id && planSlots(d).gpuSupported);
		if (first) indexName = first.name;
		loadItem();
	}

	async function onDevice(device: Device) {
		try {
			texture = await getColormapTexture(device, colormapLutUrl);
		} catch (e) {
			error = String(e);
		}
	}

	async function loadItem() {
		error = null;
		loaded = false;
		item = null;
		try {
			const it = await clientFor(platform).getItem(itemId, platform.collection);
			const tifs = Object.fromEntries(Object.entries(it.assets).filter(([, a]) => /\.tiff?$/i.test(a.href)).map(([k, a]) => [k, a.href]));
			hrefs = await signHrefs(tifs, platform);
			item = it;
			if (it.bbox) center = [(it.bbox[0] + it.bbox[2]) / 2, (it.bbox[1] + it.bbox[3]) / 2];
		} catch (e) {
			error = String(e);
		}
	}
	loadItem();

	const layers: Layer[] = $derived.by(() => {
		const def = getIndex(indexName);
		const cmap = getColormap(colormapName);
		if (!item || !texture || !def || !cmap) return [];
		try {
			return [
				buildIndexLayer({
					sceneId: item.id,
					hrefs,
					platform,
					index: def,
					colormapTexture: texture,
					colormapRow: cmap.row ?? 0,
					reversed,
					vmin,
					vmax,
					maskEnabled,
					valueOffset,
					onLoad: () => (loaded = true)
				})
			];
		} catch (e) {
			error = String(e);
			return [];
		}
	});
</script>

<div class="wrap">
	<aside>
		<h2>Raster spike</h2>
		<label>Platform
			<select value={platformId} onchange={(e) => switchPlatform(e.currentTarget.value)}>
				{#each platforms as p}<option value={p.id}>{p.label}</option>{/each}
			</select>
		</label>
		<label>STAC item id <input bind:value={itemId} onchange={loadItem} /></label>
		<label>Index
			<select bind:value={indexName}>
				{#each gpuIndices as d}<option value={d.name}>{d.name} — {d.longName}</option>{/each}
			</select>
		</label>
		<label>Colormap
			<select bind:value={colormapName}>
				{#each colormaps as c}<option value={c.name}>{c.name}</option>{/each}
			</select>
		</label>
		<label><input type="checkbox" bind:checked={reversed} /> reversed</label>
		<label>vmin <input type="number" step="0.05" bind:value={vmin} /></label>
		<label>vmax <input type="number" step="0.05" bind:value={vmax} /></label>
		<label><input type="checkbox" bind:checked={maskEnabled} /> quality mask</label>
		<label>value offset <input type="number" step="0.01" bind:value={valueOffset} /></label>
		<p class="status">
			{#if error}<span class="err">{error}</span>
			{:else if !item}loading item…
			{:else if !texture}waiting for GPU…
			{:else}{item.id}{platform.cloudProperty ? ` · cloud ${item.properties['eo:cloud_cover']}%` : ''} · {loaded ? 'COGs loaded' : 'loading COGs…'}{/if}
		</p>
		{#if item}
			<pre>{JSON.stringify({ slots: planSlots(getIndex(indexName)!).slotAssets, mask: planSlots(getIndex(indexName)!).maskAsset })}</pre>
		{/if}
	</aside>
	<div class="map">
		<MapLibre
			autoloadGlobalCss={false}
			style="https://tiles.openfreemap.org/styles/positron"
			bind:center
			bind:zoom
			maxPitch={0}
			attributionControl={{ compact: true }}
		>
			<NavigationControl showCompass={true} />
			<DeckGLOverlay interleaved={true} {layers} onDeviceInitialized={onDevice} />
		</MapLibre>
	</div>
</div>

<style>
	.wrap { display: grid; grid-template-columns: 300px 1fr; height: 100vh; }
	aside { padding: 12px; background: var(--bg-panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; gap: 8px; overflow: auto; }
	label { display: flex; flex-direction: column; gap: 3px; color: var(--text-dim); }
	input, select { background: var(--bg-raised); border: 1px solid var(--border); border-radius: var(--radius); padding: 4px 6px; }
	.map { position: relative; }
	.map :global(.maplibregl-map) { position: absolute; inset: 0; }
	.err { color: var(--danger); }
	pre { font-family: var(--mono); font-size: 11px; color: var(--text-dim); }
</style>
