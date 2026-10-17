<script lang="ts">
	import { colormaps } from '$lib/data';
	import { getSession } from '$lib/state/session.svelte';
	import GradientSwatch from './GradientSwatch.svelte';
	import TrueColorSwatch from './TrueColorSwatch.svelte';
	import { getTrueColorPreset } from '$lib/data';

	const session = getSession();
	const tc = $derived(session.isTrueColor);
	const cur = $derived(tc ? null : session.colormapDef);
	const presetLabel = $derived(getTrueColorPreset(session.draft.truecolor?.preset ?? '')?.label ?? 'custom tone');
	const rgbBands = $derived(
		(session.platform.rgbAssets ?? []).map((a) => session.platform.bandsNm[a]?.[0] ?? a).join(' · ')
	);
	let filter = $state<'all' | 'pairs' | 'approved'>('pairs');
	const type = $derived(session.colormapType);
	/** the ramp type III Read suggests for this frame */
	const suggested = $derived(session.reading?.options.find((o) => o.recommended) ?? null);
	const mismatch = $derived(suggested && cur && cur.type !== suggested.ramp ? suggested : null);
	let query = $state('');

	const visible = $derived.by(() => {
		const idx = session.draft.index;
		// Only maps with a LUT row can render on the GPU (categoricals have none).
		let list = colormaps.filter((c) => c.row !== null && c.type !== 'categorical' && c.type !== 'cyclic');
		if (type !== 'all') list = list.filter((c) => c.type === type);
		if (filter === 'approved') list = list.filter((c) => c.status === 'approved');
		if (filter === 'pairs') {
			const paired = list.filter((c) => c.pairsWith.includes(idx));
			list = paired.length ? paired : list;
		}
		const q = query.trim().toLowerCase();
		if (q) {
			list = list.filter(
				(c) =>
					c.shortName.toLowerCase().includes(q) ||
					c.collection.toLowerCase().includes(q) ||
					c.tags.some((t) => t.toLowerCase().includes(q))
			);
		}
		return list;
	});
</script>

<div class="section">
	{#if tc}
		<div class="current">
			<div class="row">
				<span class="name">True color</span>
				<span class="facts mono">{presetLabel}</span>
			</div>
			<TrueColorSwatch tone={session.draft.truecolor} height={16} />
			<p class="hint">Tone it in III Read. Pick any ramp below to go back to an index plate.</p>
		</div>
	{/if}
	{#if cur}
		<div class="current">
			<div class="row">
				<span class="name">{cur.shortName}{session.draft.colormap.reversed ? ' ⇄' : ''}</span>
				<span class="facts mono">
					<span class:ok={cur.printSafety === 'safe'} class:warn={cur.printSafety === 'watchlist'} class:bad={cur.printSafety === 'unsafe'}>print {cur.printSafety}</span>
				</span>
				<button class="btn ghost sm" onclick={() => session.toggleReversed()} title="Reverse the ramp (r)">reverse</button>
			</div>
			<GradientSwatch stops={cur.previewStops} reversed={session.draft.colormap.reversed} height={16} />
		</div>
	{/if}
	<div class="tools">
		<div class="seg">
			<button class:on={filter === 'pairs'} onclick={() => (filter = 'pairs')}>pairs with {session.draft.index}</button>
			<button class:on={filter === 'approved'} onclick={() => (filter = 'approved')}>approved</button>
			<button class:on={filter === 'all'} onclick={() => (filter = 'all')}>all</button>
		</div>
		<div class="seg">
			<button class:on={type === 'all'} onclick={() => (session.colormapType = 'all')}>any</button>
			<button class:on={type === 'sequential'} onclick={() => (session.colormapType = 'sequential')}>seq</button>
			<button class:on={type === 'diverging'} onclick={() => (session.colormapType = 'diverging')}>div</button>
		</div>
	</div>
	{#if mismatch}
		<p class="hint">
			III Read suggests a <b>{mismatch.ramp}</b> ramp here ({mismatch.title.toLowerCase()}); {cur?.shortName} is {cur?.type}.
			<button class="btn ghost sm" onclick={() => session.applyStretch(mismatch)}>Apply</button>
		</p>
	{/if}
	<input class="input q" type="search" placeholder="filter {visible.length} maps" bind:value={query} aria-label="Filter colormaps" />
	<button
		class="truecolor"
		class:on={tc}
		disabled={!!session.trueColorBlocker}
		onclick={() => session.selectTrueColor()}
		title={session.trueColorBlocker ?? 'Render the scene’s red, green and blue bands instead of an index'}
	>
		<TrueColorSwatch tone={session.draft.truecolor} height={12} />
		<span class="tc-label"><span class="mono">True color</span><span class="why">{session.trueColorBlocker ?? `red · green · blue — ${rgbBands}`}</span></span>
	</button>
	<ul class="grid scroll">
		{#each visible as c (c.name)}
			<li>
				<button class="cm" class:on={!tc && c.name === session.draft.colormap.name} onclick={() => session.setColormap(c.name)} title="{c.name} · {c.semanticRegister} · {c.notes}">
					<GradientSwatch stops={c.previewStops} reversed={session.draft.colormap.reversed} height={9} />
					<span class="label mono">{c.shortName}</span>
				</button>
			</li>
		{/each}
	</ul>
</div>

<style>
	.current { display: flex; flex-direction: column; gap: 6px; }
	.row { display: flex; align-items: baseline; gap: 10px; }
	.name { font-family: var(--serif); font-size: 17px; flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.facts { font-size: 10px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-mute); }
	.ok { color: var(--ok); } .warn { color: var(--warn); } .bad { color: var(--bad); }
	.tools { display: flex; justify-content: space-between; gap: 6px; margin: 8px 0 6px; flex-wrap: wrap; }
	.q { margin-bottom: 4px; }
	.hint { margin: 0 0 6px; font-size: 11.5px; line-height: 1.4; color: var(--ink-soft); }
	.hint .btn { margin-left: 6px; vertical-align: baseline; }
	/* The grid scrolls, so it clips anything drawn outside its padding box: the
	   3px padding holds the 2px selection outline on the outer swatches, and the
	   matching negative margin keeps the columns flush with the filter above. */
	.grid { list-style: none; margin: 0 -3px; padding: 3px; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 5px 6px; max-height: 220px; }
	.cm { width: 100%; display: flex; flex-direction: column; gap: 2px; padding: 0; background: transparent; border: 0; text-align: left; }
	.cm :global(.swatch) { outline: 1px solid transparent; outline-offset: 1px; }
	.cm:hover :global(.swatch) { outline-color: var(--ink-mute); }
	.cm.on :global(.swatch) { outline-color: var(--accent); }
	.label { font-size: 9.5px; line-height: 1.2; color: var(--ink-mute); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.cm.on .label { color: var(--ink); }
	.truecolor { width: 100%; display: flex; flex-direction: column; gap: 3px; margin: 0 0 8px; padding: 0; background: transparent; border: 0; text-align: left; }
	.truecolor :global(.tc) { outline: 1px solid transparent; outline-offset: 1px; }
	.truecolor:hover:not(:disabled) :global(.tc) { outline-color: var(--ink-mute); }
	.truecolor.on :global(.tc) { outline-color: var(--accent); }
	.truecolor:disabled { cursor: default; opacity: 0.45; }
	.tc-label { display: flex; justify-content: space-between; gap: 8px; font-size: 9.5px; color: var(--ink-mute); }
	.tc-label .mono { font-size: 10px; letter-spacing: 0.06em; }
	.truecolor.on .tc-label .mono { color: var(--ink); }
	.why { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: var(--mono); }
</style>
