<script lang="ts">
	import { getSession } from '$lib/state/session.svelte';
	import { fits } from '$lib/data/fit';
	import { FULL_COVERAGE, rankScenes } from '$lib/stac/rank';
	import { cloudLabel, itemCloudCover, itemDate, itemGrid, itemThumbnail } from '$lib/stac/items';
	import type { StacItem } from '$lib/stac/types';

	const session = getSession();

	const results = $derived(rankScenes(session.search.items, session.platform.collection, session.framePoly));

	const scene = $derived(session.draft.scene);
	const fit = $derived(session.sceneFit);
	const radar = $derived(session.platform.orbitProperty !== null);

	function isCurrent(item: StacItem) {
		return scene?.itemId === item.id;
	}
	/** Radar items: the channels this frame carries, e.g. "VV VH". */
	function polarizations(item: StacItem): string {
		const p = item.properties['sar:polarizations'];
		return Array.isArray(p) ? p.join(' ') : '';
	}
	function sceneMeta(item: StacItem): string {
		const parts: string[] = [];
		if (session.platform.cloudProperty) parts.push(`cloud ${cloudLabel(itemCloudCover(item))}`);
		parts.push(itemGrid(item));
		const pol = polarizations(item);
		if (pol) parts.push(pol);
		if (item.properties.platform) parts.push(String(item.properties.platform));
		return parts.join(' · ');
	}
</script>

<div class="section">
	{#if scene}
		<div class="current">
			{#if scene.thumbnail}<img src={scene.thumbnail} alt="" />{:else}<span class="thumb-empty"></span>{/if}
			<ul class="kv">
				<li><span class="k">item</span><span class="v">{scene.itemId}</span></li>
				<li><span class="k">date</span><span class="v">{scene.datetime.slice(0, 10)}</span></li>
				{#if scene.platform}<li><span class="k">sat</span><span class="v">{scene.platform}</span></li>{/if}
				{#if session.platform.cloudProperty}<li><span class="k">cloud</span><span class="v">{scene.cloudCover.toFixed(1)}%</span></li>{/if}
				<li><span class="k">grid</span><span class="v">{scene.mgrs}{scene.epsg ? ` · EPSG:${scene.epsg}` : ''}</span></li>
			</ul>
			<button class="btn ghost sm clear" onclick={() => session.clearScene()}>Clear</button>
		</div>
		{#if session.isTrueColor}
			{#if session.trueColorBlocker}
				<p class="note warn">True color: {session.trueColorBlocker}. Pick another scene, or an index in II.</p>
			{/if}
		{:else if fit && !fits(fit)}
			<p class="note warn">
				{#if fit.kind === 'other-platform'}
					This scene is {scene.collection}; <b>{session.draft.index}</b> reads {fit.platform.label}. Search below for one, or pick an index that fits this scene.
				{:else}
					<b>{session.draft.index}</b> needs bands this item does not carry ({fit.note.replace(/^scene has no /, '')}). Pick another scene, or an index that fits.
				{/if}
			</p>
		{/if}
	{/if}

	{#if !scene && session.search.status === 'idle'}
		<p class="hint">No scene pinned. The frame shows only the basemap until one is chosen: set a date window, search, then pick a result{session.platform.cloudProperty ? ' with full cover and little cloud' : ''}.</p>
	{/if}

	<div class="controls">
		<label class="field"><span>From</span><input class="input" type="date" bind:value={session.search.range.start} /></label>
		<label class="field"><span>To</span><input class="input" type="date" bind:value={session.search.range.end} /></label>
	</div>
	<div class="controls years">
		<label class="field"><span>± years</span>
			<select class="input" bind:value={session.search.yearRadius}>
				<option value={0}>0</option><option value={1}>1</option><option value={2}>2</option><option value={3}>3</option>
			</select>
		</label>
		{#if session.platform.cloudProperty}
			<label class="field"><span>Cloud below <b class="mono">{session.search.cloudMax}%</b></span><input class="range" type="range" min="1" max="60" step="1" bind:value={session.search.cloudMax} /></label>
		{:else if radar}
			<div class="field">
				<span>Pass direction · no cloud filter</span>
				<div class="seg" role="group" aria-label="Orbit direction">
					<button class:on={session.search.orbit === 'ascending'} onclick={() => (session.search.orbit = 'ascending')}>ascending</button>
					<button class:on={session.search.orbit === 'descending'} onclick={() => (session.search.orbit = 'descending')}>descending</button>
				</div>
			</div>
		{/if}
	</div>
	<button class="btn primary search" onclick={() => session.runSearch()} disabled={session.search.status === 'loading'}>
		{session.search.status === 'loading' ? 'Searching…' : `Search ${session.platform.label} scenes`}
	</button>

	{#if session.search.status === 'error'}
		<p class="note bad">Search failed: {session.search.error}</p>
	{:else if session.search.status === 'done' && results.length === 0}
		<p class="empty">No {session.platform.label} scenes match. Widen the dates{session.platform.cloudProperty ? ', allow more cloud,' : radar ? ', try the other pass direction,' : ''} or move the frame.</p>
	{:else if results.length}
		<ul class="results">
			{#each results as r (r.item.id)}
				<li>
					<button class="scene" class:current={isCurrent(r.item)} onclick={() => { session.useScene(r.item); session.refreshStats(); }}>
						{#if itemThumbnail(r.item)}<img src={itemThumbnail(r.item)} alt="" loading="lazy" />{:else}<span class="thumb-empty"></span>{/if}
						<span class="s-body">
							<span class="s-row"><span class="mono date">{itemDate(r.item)}</span><span class="pill" class:bad={r.coverage < FULL_COVERAGE} class:ok={r.coverage >= FULL_COVERAGE}>{(r.coverage * 100).toFixed(0)}% cover</span></span>
							<span class="s-row meta mono">{sceneMeta(r.item)}</span>
						</span>
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.current { display: grid; grid-template-columns: 56px 1fr auto; gap: 10px; align-items: start; margin-bottom: 10px; }
	.current img, .current .thumb-empty { width: 56px; height: 56px; object-fit: cover; border: var(--hair) solid var(--rule); background: var(--paper-edge); display: block; }
	.current .kv { min-width: 0; }
	.current .kv .k { flex-basis: 40px; }
	.clear { align-self: start; }
	.hint { margin: 0 0 10px; color: var(--ink-mute); font-size: 12px; line-height: 1.45; font-style: italic; }
	.note { margin: 0 0 10px; font-size: 12px; line-height: 1.45; padding: 6px 8px; border: var(--hair) solid currentColor; }
	.note b { font-family: var(--mono); font-weight: 500; font-size: 11px; }
	.note.warn { color: var(--warn); }
	.note.bad { color: var(--bad); margin-top: 10px; }
	.controls { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 8px; margin-bottom: 8px; min-width: 0; }
	.controls .input { min-width: 0; }
	.controls.years { grid-template-columns: 64px minmax(0, 1fr); align-items: end; }
	.controls .seg { align-self: start; margin-top: 2px; }
	.range { width: 100%; margin: 6px 0 4px; }
	.search { width: 100%; justify-content: center; }
	.results { list-style: none; margin: 10px 0 0; padding: 0; display: flex; flex-direction: column; max-height: 300px; overflow: auto; border-top: var(--hair) solid var(--rule); }
	.scene { display: flex; gap: 8px; width: 100%; text-align: left; padding: 6px 4px; background: transparent; border: 0; border-bottom: 0.3px dotted var(--rule); border-left: 2px solid transparent; }
	.scene:hover { background: var(--paper-2); }
	.scene.current { border-left-color: var(--accent); background: var(--paper-2); }
	.scene img, .scene .thumb-empty { width: 40px; height: 40px; object-fit: cover; background: var(--paper-edge); flex: 0 0 40px; border: var(--hair) solid var(--rule); }
	.s-body { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1; }
	.s-row { display: flex; justify-content: space-between; align-items: center; gap: 6px; }
	.date { font-size: 11.5px; }
	.meta { font-size: 10.5px; color: var(--ink-mute); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
