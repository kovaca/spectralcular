<script lang="ts">
	import { indices, THEME_ORDER, THEME_LABEL, gsdForIndex, gsdForTrueColor, platformForIndex, type IndexDef } from '$lib/data';
	import { fits, indexFitForScene, type Fit } from '$lib/data/fit';
	import { planSlots } from '$lib/raster/formula/resolve';
	import { getSession } from '$lib/state/session.svelte';
	import BandRuler from './BandRuler.svelte';

	const session = getSession();
	let query = $state('');
	let mode = $state<'all' | 'fits' | 'approved'>('all');

	const scene = $derived(session.draft.scene);
	/** "fits scene" has nothing to say without a scene; fall back to all. */
	const effectiveMode = $derived(mode === 'fits' && !scene ? 'all' : mode);

	type Row = { def: IndexDef; fit: Fit };
	const groups = $derived.by(() => {
		const q = query.trim().toLowerCase();
		return THEME_ORDER.map((theme) => {
			const rows: Row[] = indices
				.filter((d) => d.theme === theme)
				.filter((d) => effectiveMode !== 'approved' || d.status === 'approved')
				.filter((d) => q === '' || `${d.name} ${d.longName}`.toLowerCase().includes(q))
				.map((def) => ({ def, fit: indexFitForScene(def, scene) }))
				.filter((r) => effectiveMode !== 'fits' || fits(r.fit));
			const fitting = rows.filter((r) => fits(r.fit)).length;
			// One quiet line per group: what a scene would need, or how many of these it serves.
			let meta = '';
			if (scene && fitting < rows.length) {
				const platforms = new Set(rows.filter((r) => !fits(r.fit)).map((r) => r.fit.platform.label));
				meta = fitting === 0 && platforms.size === 1 ? `needs ${[...platforms][0]}` : `${fitting} of ${rows.length} fit`;
			}
			return { theme, rows, meta };
		}).filter((g) => g.rows.length);
	});
	const anyOther = $derived(groups.some((g) => g.rows.some((r) => r.fit.kind === 'other-platform')));
	const anyMissing = $derived(groups.some((g) => g.rows.some((r) => r.fit.kind === 'missing-assets')));

	const tc = $derived(session.isTrueColor);
	const def = $derived(session.indexDef);
	const platform = $derived(tc ? session.platform : def ? platformForIndex(def) : null);
	const plan = $derived(def && !tc ? planSlots(def) : null);
	const currentFit = $derived(tc ? null : session.sceneFit);
	const usedAssets = $derived(tc ? [...(session.platform.rgbAssets ?? [])] : def ? [...new Set(Object.values(def.bands))] : []);
	const usedLabel = $derived.by(() => {
		if (!def || !platform) return '';
		const parts: string[] = [];
		for (const [asset, [code, name, nm]] of Object.entries(platform.bandsNm ?? {})) {
			if (usedAssets.includes(asset)) parts.push(`${code} ${name} ${nm >= 3000 ? `${(nm / 1000).toFixed(1)} µm` : `${nm} nm`}`);
		}
		return parts.length ? parts.join(' · ') : usedAssets.map((a) => a.toUpperCase()).join(' · ');
	});

	function chipTitle(r: Row): string {
		const base = `${r.def.longName} — ${r.def.formula}`;
		return r.fit.note ? `${base} · ${r.fit.note}` : base;
	}
	function pick(d: IndexDef) {
		session.setIndex(d.name);
		session.refreshStats();
	}
</script>

<div class="section">
	{#if tc && platform}
		<div class="current">
			<div class="head">
				<span class="name">True color</span>
				<span class="pill">{gsdForTrueColor(platform)} m</span>
			</div>
			<div class="long">No index: the visible red, green and blue bands, toned in III Read. Pick an index to go back.</div>
		</div>
		<div class="bands">
			<div class="eyebrow">Bands in use</div>
			<BandRuler {platform} used={usedAssets} />
			<div class="used mono">{usedLabel}</div>
		</div>
	{:else if def && platform}
		<div class="current">
			<div class="head">
				<span class="name">{def.name}</span>
				<span class="pill">{gsdForIndex(def)} m</span>
				{#if plan && !plan.gpuSupported}<span class="pill warn" title={plan.reason}>no GPU preview</span>{/if}
				{#if currentFit && !fits(currentFit)}<span class="pill warn" title="The pinned scene cannot feed this formula; see I Scene">{currentFit.note}</span>{/if}
			</div>
			<div class="long">{def.longName}</div>
			<div class="formula mono">{def.formula}</div>
		</div>
		<div class="bands">
			<div class="eyebrow">Bands in use</div>
			<BandRuler {platform} used={usedAssets} />
			<div class="used mono">{usedLabel}</div>
		</div>
	{/if}
	<div class="tools">
		<input class="input" type="search" placeholder="Filter indices" bind:value={query} aria-label="Filter indices" />
		<div class="seg" role="group" aria-label="Show">
			<button class:on={effectiveMode === 'all'} onclick={() => (mode = 'all')}>all</button>
			<button class:on={effectiveMode === 'fits'} disabled={!scene} title={scene ? 'Only indices this scene can feed' : 'Pin a scene first'} onclick={() => (mode = 'fits')}>fits scene</button>
			<button class:on={effectiveMode === 'approved'} onclick={() => (mode = 'approved')}>approved</button>
		</div>
	</div>
	<div class="groups scroll">
		{#each groups as g (g.theme)}
			<div class="group">
				<div class="gh eyebrow"><span>{THEME_LABEL[g.theme]}</span>{#if g.meta}<span class="gm">{g.meta}</span>{/if}</div>
				<div class="chips">
					{#each g.rows as r (r.def.name)}
						<button
							class="chip mono"
							class:on={!tc && r.def.name === session.draft.index}
							class:approved={r.def.status === 'approved'}
							class:other={r.fit.kind === 'other-platform'}
							class:missing={r.fit.kind === 'missing-assets'}
							title={chipTitle(r)}
							onclick={() => pick(r.def)}>{r.def.name}</button>
					{/each}
				</div>
			</div>
		{:else}
			<p class="empty">No index matches.</p>
		{/each}
	</div>
	{#if scene && (anyOther || anyMissing)}
		<div class="legend eyebrow">
			{#if anyOther}<span><i class="sw other"></i> other platform</span>{/if}
			{#if anyMissing}<span><i class="sw missing"></i> band not in this scene</span>{/if}
		</div>
	{/if}
</div>

<style>
	.current { display: flex; flex-direction: column; gap: 2px; }
	.head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
	.name { font-family: var(--serif); font-size: 22px; line-height: 1; }
	.long { font-style: italic; color: var(--ink-soft); font-size: 12.5px; }
	.formula { font-size: 11px; color: var(--accent); word-break: break-all; }
	.bands { margin-top: 8px; border-top: var(--hair) solid var(--rule); padding-top: 4px; }
	.used { font-size: 10px; color: var(--ink-soft); margin-top: 2px; line-height: 1.4; }
	.tools { display: flex; gap: 8px; align-items: center; margin-top: 10px; flex-wrap: wrap; }
	.tools .input { flex: 1 1 120px; min-width: 0; }
	.groups { margin-top: 8px; max-height: 220px; display: flex; flex-direction: column; gap: 8px; }
	.gh { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
	.gm { text-transform: none; letter-spacing: 0.04em; color: var(--warn); }
	.chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
	.chip { font-size: 10.5px; padding: 2px 7px; border: var(--hair) solid var(--rule); background: var(--paper-2); color: var(--ink-mute); }
	.chip.approved { color: var(--ink-soft); }
	/* Scene fit is a register, not an alarm: the border pattern changes, the ink recedes. */
	.chip.other { border-style: dashed; color: var(--ink-mute); background: transparent; }
	.chip.missing { border-style: dotted; border-width: 1px; color: var(--ink-mute); background: transparent; }
	.chip:hover { border-color: var(--ink-mute); color: var(--ink); }
	.chip.on { border-color: var(--accent); color: var(--accent); }
	.legend { display: flex; gap: 14px; margin-top: 8px; text-transform: none; letter-spacing: 0.04em; }
	.legend span { display: inline-flex; align-items: center; gap: 5px; }
	.sw { display: inline-block; width: 14px; height: 9px; border: var(--hair) solid var(--ink-mute); }
	.sw.other { border-style: dashed; }
	.sw.missing { border-style: dotted; border-width: 1px; }
</style>
