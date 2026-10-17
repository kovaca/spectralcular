<script lang="ts">
	import { colormaps, getColormap, getIndex, indices, THEME_LABEL, THEME_ORDER, type ColormapDef, type IndexDef } from '$lib/data';
	import { bookOverview, focusPlateIds, type OverviewFocus } from '$lib/state/overview';
	import { getProjectStore } from '$lib/state/project.svelte';
	import { getSession } from '$lib/state/session.svelte';
	import type { Plate } from '$lib/state/schema';
	import GradientSwatch from './GradientSwatch.svelte';
	import OverviewMap from './OverviewMap.svelte';
	import OverviewTimeline from './OverviewTimeline.svelte';

	/**
	 * The book at a glance: where the plates are, when their scenes were taken,
	 * and which of the catalog's indices and colormaps they use, and how often.
	 * Clicking anything focuses it; every view then dims what doesn't match and
	 * the card at the foot lists the plates, ready to open.
	 */
	let {
		focusIds = $bindable([]),
		onOpenPlate,
		onShowOnMap
	}: {
		/** plate ids under the current focus, for the stage map */
		focusIds?: string[];
		onOpenPlate: (p: Plate) => void;
		/** fit the stage map to these plates */
		onShowOnMap: (plates: Plate[]) => void;
	} = $props();

	const project = getProjectStore();
	const session = getSession();

	const o = $derived(bookOverview(project.plates));
	let focus = $state<OverviewFocus | null>(null);
	let usedOnly = $state(false);

	const ids = $derived(focusPlateIds(o, focus));
	$effect(() => {
		focusIds = ids;
	});
	const idSet = $derived(new Set(ids));
	const plateById = $derived(new Map(project.plates.map((p) => [p.id, p])));
	const focusPlates = $derived(ids.map((id) => plateById.get(id)).filter((p): p is Plate => !!p));

	function toggle(f: OverviewFocus) {
		focus = focus && focus.kind === f.kind && focus.key === f.key ? null : f;
	}
	const isFocus = (kind: OverviewFocus['kind'], key: string) => focus?.kind === kind && focus.key === key;
	/** something is focused and this item's plates are not among it */
	const dimFor = (plateIds: string[] | undefined) => ids.length > 0 && !(plateIds ?? []).some((id) => idSet.has(id));

	// ----- coverage --------------------------------------------------------

	const draftIndex = $derived(session.isTrueColor ? null : session.draft.index);
	const draftColormap = $derived(session.isTrueColor ? null : session.draft.colormap.name);

	const indexRows = $derived(
		THEME_ORDER.map((theme) => {
			const all = indices.filter((d) => d.theme === theme);
			const used = all.filter((d) => o.byIndex.has(d.name));
			return { theme, label: THEME_LABEL[theme], all, used, shown: usedOnly ? used : all };
		}).filter((r) => r.shown.length)
	);
	/** Same set the colormap picker offers: ramps with a LUT row. */
	const ramps = colormaps.filter((c) => c.row !== null && c.type !== 'categorical' && c.type !== 'cyclic');
	const collections = [...new Set(ramps.map((c) => c.collection))].sort();
	const colormapRows = $derived(
		collections
			.map((col) => {
				const all = ramps.filter((c) => c.collection === col);
				const used = all.filter((c) => o.byColormap.has(c.name));
				return { col, all, used, shown: usedOnly ? used : all };
			})
			.filter((r) => r.shown.length)
	);

	const uses = (m: Map<string, string[]>, k: string) => m.get(k)?.length ?? 0;
	const level = (n: number) => (n >= 3 ? 3 : n);
	function indexTitle(d: IndexDef): string {
		const n = uses(o.byIndex, d.name);
		return `${d.name} — ${d.longName} · ${n ? `${n} plate${n === 1 ? '' : 's'}` : 'not used'}`;
	}
	function colormapTitle(c: ColormapDef): string {
		const n = uses(o.byColormap, c.name);
		return `${c.shortName} · ${c.collection} · ${c.type} · ${n ? `${n} plate${n === 1 ? '' : 's'}` : 'not used'}`;
	}

	// ----- focus card ------------------------------------------------------

	const card = $derived.by(() => {
		const f = focus;
		if (!f) return null;
		switch (f.kind) {
			case 'index': {
				const d = getIndex(f.key);
				return { eyebrow: 'Index', title: f.key, sub: d?.longName ?? '', stops: null as string[] | null, reversed: false };
			}
			case 'colormap': {
				const c = getColormap(f.key);
				return { eyebrow: 'Colormap', title: c?.shortName ?? f.key, sub: c ? `${c.collection} · ${c.type} · print ${c.printSafety}` : '', stops: c?.previewStops ?? null, reversed: false };
			}
			case 'pair': {
				const p = o.pairs.find((x) => x.key === f.key);
				const c = p ? getColormap(p.colormap) : undefined;
				return { eyebrow: 'Combination', title: p ? `${p.index} · ${c?.shortName ?? p.colormap}${p.reversed ? ' ⇄' : ''}` : f.key, sub: '', stops: c?.previewStops ?? null, reversed: p?.reversed ?? false };
			}
			case 'location': {
				const pt = o.points.find((x) => x.locationId === f.key);
				return { eyebrow: 'Place', title: pt?.name ?? f.key, sub: '', stops: null, reversed: false };
			}
			case 'truecolor':
				return { eyebrow: 'Composite', title: 'True color', sub: '', stops: null, reversed: false };
			case 'plate': {
				const p = plateById.get(f.key);
				return { eyebrow: 'Plate', title: p ? `${ord(p)} · ${p.location.name}` : f.key, sub: '', stops: null, reversed: false };
			}
		}
	});
	/** the focused index or colormap could go on the plate being edited */
	const canUse = $derived.by(() => {
		if (focus?.kind === 'index') return session.isTrueColor || session.draft.index !== focus.key;
		if (focus?.kind === 'colormap') return session.isTrueColor || session.draft.colormap.name !== focus.key;
		return false;
	});
	function useForDraft() {
		if (focus?.kind === 'index') {
			session.setIndex(focus.key);
			session.refreshStats();
		} else if (focus?.kind === 'colormap') session.setColormap(focus.key);
	}

	const ord = (p: Plate) => String(p.ordinal).padStart(3, '0');
	function look(p: Plate): string {
		if (p.kind === 'truecolor') return 'True color';
		return `${p.index} · ${getColormap(p.colormap.name)?.shortName ?? p.colormap.name}${p.colormap.reversed ? ' ⇄' : ''}`;
	}
	const years = $derived(o.span ? (o.span[0].slice(0, 4) === o.span[1].slice(0, 4) ? o.span[0].slice(0, 4) : `${o.span[0].slice(0, 4)}–${o.span[1].slice(0, 4)}`) : null);
	const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && focus) {
			focus = null;
			e.stopPropagation();
		}
	}
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="overview" onkeydown={onKeydown}>
	<div class="head">
		<span class="book">{project.project.name}</span>
		<span class="tally mono">
			{plural(o.counts.plates, 'plate')} · {plural(o.counts.places, 'place')} · {plural(o.counts.indices, 'index', 'indices')} · {plural(o.counts.colormaps, 'colormap')}{o.counts.trueColor ? ` · ${o.counts.trueColor} true color` : ''}{years ? ` · ${years}` : ''}
		</span>
		{#if o.platforms.size > 1}
			<span class="tally mono">{[...o.platforms].map(([l, n]) => `${l} ${n}`).join(' · ')}</span>
		{/if}
	</div>

	<div class="body scroll">
		{#if o.counts.plates === 0}
			<p class="empty">No plates yet. Saved plates appear here on a map and a timeline, and fill in the index and colormap catalogs below, so you can see what the book covers and what it hasn't tried.</p>
		{/if}

		<section class="block">
			<div class="bh eyebrow"><span>Places</span><span class="bm">{plural(o.counts.places, 'place')}</span></div>
			<OverviewMap points={o.points} focusIds={ids} draft={session.draft.frame.center} onFocus={(id) => toggle({ kind: 'location', key: id })} />
		</section>

		{#if o.counts.plates}
			<section class="block">
				<div class="bh eyebrow"><span>Scene dates</span><span class="bm">{years ?? ''}</span></div>
				<OverviewTimeline marks={o.marks} months={o.months} noScene={o.counts.noScene} focusIds={ids} onFocus={(id) => toggle({ kind: 'plate', key: id })} />
			</section>
		{/if}

		{#if o.pairs.length || o.counts.trueColor}
			<section class="block">
				<div class="bh eyebrow"><span>Combinations</span><span class="bm">{plural(o.pairs.length + (o.counts.trueColor ? 1 : 0), 'look')}</span></div>
				<ul class="pairs">
					{#each o.pairs as p (p.key)}
						{@const c = getColormap(p.colormap)}
						<li>
							<button class="pair" class:on={isFocus('pair', p.key)} class:dim={dimFor(p.plateIds)} onclick={() => toggle({ kind: 'pair', key: p.key })}>
								<span class="pi mono">{p.index}</span>
								<span class="psw">{#if c}<GradientSwatch stops={c.previewStops} reversed={p.reversed} height={8} />{/if}</span>
								<span class="pc mono">{c?.shortName ?? p.colormap}{p.reversed ? ' ⇄' : ''}</span>
								<span class="pn mono" class:rep={p.plateIds.length > 1} title={p.plateIds.length > 1 ? 'Used on more than one plate' : undefined}>×{p.plateIds.length}</span>
							</button>
						</li>
					{/each}
					{#if o.counts.trueColor}
						<li>
							<button class="pair" class:on={isFocus('truecolor', 'truecolor')} class:dim={dimFor(o.trueColorIds)} onclick={() => toggle({ kind: 'truecolor', key: 'truecolor' })}>
								<span class="pi mono">True color</span><span class="psw"></span><span class="pc"></span>
								<span class="pn mono">×{o.counts.trueColor}</span>
							</button>
						</li>
					{/if}
				</ul>
			</section>
		{/if}

		<div class="seg-row">
			<span class="eyebrow">Catalog coverage</span>
			<div class="seg" role="group" aria-label="Show in catalogs">
				<button class:on={!usedOnly} onclick={() => (usedOnly = false)}>all</button>
				<button class:on={usedOnly} onclick={() => (usedOnly = true)}>used only</button>
			</div>
		</div>

		<section class="block">
			<div class="bh eyebrow"><span>Indices</span><span class="bm">{o.counts.indices} of {indices.length} used</span></div>
			{#each indexRows as r (r.theme)}
				<div class="crow">
					<span class="cl mono" title={r.label}><span class="clt">{r.label}</span><span class="cn">{r.used.length}/{r.all.length}</span></span>
					<span class="cells">
						{#each r.shown as d (d.name)}
							{@const n = uses(o.byIndex, d.name)}
							<button
								class="cell l{level(n)}"
								class:cur={d.name === draftIndex}
								class:on={isFocus('index', d.name)}
								class:dim={ids.length > 0 && !isFocus('index', d.name) && dimFor(o.byIndex.get(d.name))}
								tabindex={n || d.name === draftIndex ? 0 : -1}
								title={indexTitle(d)}
								aria-label={indexTitle(d)}
								onclick={() => toggle({ kind: 'index', key: d.name })}
							></button>
						{/each}
					</span>
				</div>
			{:else}
				<p class="none">No index used yet.</p>
			{/each}
			<div class="legend eyebrow" aria-hidden="true">
				<span><i class="cell l0"></i>unused</span><span><i class="cell l1"></i>1</span><span><i class="cell l2"></i>2</span><span><i class="cell l3"></i>3+</span><span><i class="cell l0 cur"></i>this plate</span>
			</div>
		</section>

		<section class="block">
			<div class="bh eyebrow"><span>Colormaps</span><span class="bm">{o.counts.colormaps} of {ramps.length} used</span></div>
			{#each colormapRows as r (r.col)}
				<div class="crow">
					<span class="cl mono" title={r.col}><span class="clt">{r.col}</span><span class="cn">{r.used.length}/{r.all.length}</span></span>
					<span class="cells bars">
						{#each r.shown as c (c.name)}
							{@const n = uses(o.byColormap, c.name)}
							<button
								class="bar"
								class:used={n > 0}
								class:cur={c.name === draftColormap}
								class:on={isFocus('colormap', c.name)}
								class:dim={ids.length > 0 && !isFocus('colormap', c.name) && dimFor(o.byColormap.get(c.name))}
								style:background="linear-gradient(to top, {c.previewStops.join(',')})"
								tabindex={n || c.name === draftColormap ? 0 : -1}
								title={colormapTitle(c)}
								aria-label={colormapTitle(c)}
								onclick={() => toggle({ kind: 'colormap', key: c.name })}
							></button>
						{/each}
					</span>
				</div>
			{:else}
				<p class="none">No colormap used yet.</p>
			{/each}
		</section>
	</div>

	{#if card}
		<div class="card" role="region" aria-label="Focused: {card.title}">
			<div class="ch">
				<span class="eyebrow">{card.eyebrow}</span>
				<span class="ct">{card.title}</span>
				<button class="btn ghost sm x" onclick={() => (focus = null)} aria-label="Clear focus" title="Clear (Esc)">✕</button>
			</div>
			{#if card.stops}<div class="csw"><GradientSwatch stops={card.stops} reversed={card.reversed} height={8} /></div>{/if}
			{#if card.sub}<p class="sub">{card.sub}</p>{/if}
			{#if focusPlates.length}
				<ol class="hits scroll">
					{#each focusPlates as p (p.id)}
						<li>
							<button class="hit" class:active={p.id === session.editingPlateId} onclick={() => onOpenPlate(p)} title="Open plate {ord(p)}">
								<span class="ho mono">{ord(p)}</span>
								<span class="hn">{p.location.name}</span>
								<span class="hm mono">{look(p)} · {p.scene ? p.scene.datetime.slice(0, 10) : 'no scene'}</span>
							</button>
						</li>
					{/each}
				</ol>
			{:else}
				<p class="sub">Not used on any plate yet.</p>
			{/if}
			<div class="cf">
				{#if focusPlates.length}
					<button class="btn ghost sm" onclick={() => onShowOnMap(focusPlates)} title="Fit the map to these plates">Show on map</button>
				{/if}
				{#if focus?.kind === 'index' || focus?.kind === 'colormap'}
					<button class="btn sm" disabled={!canUse} onclick={useForDraft}>
						{canUse ? `Use for ${session.editingPlateId ? 'this' : 'the new'} plate` : 'On this plate'}
					</button>
				{/if}
			</div>
		</div>
	{/if}
</div>

<style>
	.overview { display: flex; flex-direction: column; height: 100%; min-height: 0; }
	.head { display: flex; flex-direction: column; gap: 2px; padding: 12px 14px 8px; border-bottom: var(--hair) solid var(--rule); }
	.book { font-family: var(--serif); font-size: 17px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.tally { font-size: 10px; letter-spacing: 0.06em; color: var(--ink-soft); line-height: 1.5; }
	.body { flex: 1; min-height: 0; padding: 4px 14px 14px; }
	.block { margin-top: 12px; }
	.bh { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; border-bottom: var(--hair) solid var(--rule); padding-bottom: 3px; margin-bottom: 6px; }
	.bm { text-transform: none; letter-spacing: 0.04em; }
	.none { margin: 4px 0; font-size: 11.5px; font-style: italic; color: var(--ink-mute); }
	.dim { opacity: 0.25; }

	.pairs { list-style: none; margin: 0; padding: 0; }
	.pair { display: grid; grid-template-columns: minmax(0, 1fr) 34px minmax(0, 1fr) auto; gap: 8px; align-items: center; width: 100%; padding: 3px 4px; background: none; border: 0; border-bottom: 0.3px dotted var(--rule); text-align: left; }
	.pair:hover { background: var(--paper-2); }
	.pair.on { background: var(--paper-2); box-shadow: inset 2px 0 var(--accent); }
	.pi, .pc { font-size: 10.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.pc { color: var(--ink-soft); }
	.pn { font-size: 10px; color: var(--ink-mute); }
	.pn.rep { color: var(--warn); font-weight: 700; }

	.seg-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-top: 16px; }

	.crow { display: grid; grid-template-columns: 100px minmax(0, 1fr); gap: 8px; align-items: start; padding: 3px 0; border-bottom: 0.3px dotted var(--rule-soft); }
	.cl { display: flex; justify-content: space-between; gap: 4px; font-size: 9.5px; color: var(--ink-soft); white-space: nowrap; overflow: hidden; padding-top: 1px; }
	.clt { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
	.cn { flex: 0 0 auto; color: var(--ink-mute); }
	.cells { display: flex; flex-wrap: wrap; gap: 2px; }
	.cell { width: var(--cell, 10px); height: var(--cell, 10px); padding: 0; border: var(--hair) solid var(--rule); background: var(--paper-2); display: inline-block; }
	.cell.l1 { background: color-mix(in oklab, var(--accent) 45%, var(--paper)); border-color: color-mix(in oklab, var(--accent) 60%, var(--paper)); }
	.cell.l2 { background: color-mix(in oklab, var(--accent) 72%, var(--paper)); border-color: var(--accent); }
	.cell.l3 { background: var(--accent); border-color: var(--accent); }
	.cell.cur { outline: 1.25px solid var(--ink); outline-offset: 1px; }
	.cell.on { outline: 1.5px solid var(--accent); outline-offset: 1px; }
	.cell:hover { border-color: var(--ink); }
	.bars { gap: 1px 2px; }
	.bar { width: 5px; height: 16px; padding: 0; border: 0; opacity: 0.2; }
	.bar.used { opacity: 1; box-shadow: 0 2.5px 0 -0.5px var(--accent); }
	.bar.cur { opacity: 1; outline: 1.25px solid var(--ink); outline-offset: 1px; }
	.bar.on { opacity: 1; outline: 1.5px solid var(--accent); outline-offset: 1px; }
	.bar:hover { opacity: 1; }
	.bar.dim { opacity: 0.12; }
	.legend { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-top: 6px; text-transform: none; letter-spacing: 0.04em; }
	.legend span { display: inline-flex; align-items: center; gap: 4px; }
	.legend .cell { --cell: 8px; }

	.card { flex: 0 0 auto; max-height: 45%; display: flex; flex-direction: column; gap: 4px; padding: 10px 14px; border-top: var(--hair) solid var(--ink); background: var(--paper-2); }
	.ch { display: flex; align-items: baseline; gap: 8px; }
	.ct { flex: 1; min-width: 0; font-family: var(--serif); font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.x { align-self: center; }
	.csw { width: 120px; }
	.sub { margin: 0; font-size: 11.5px; font-style: italic; color: var(--ink-soft); }
	.hits { list-style: none; margin: 2px 0 0; padding: 0; min-height: 0; }
	.hit { display: grid; grid-template-columns: 30px minmax(0, 1fr); gap: 0 8px; width: 100%; padding: 4px 2px; background: none; border: 0; border-top: 0.3px dotted var(--rule); text-align: left; }
	.hit:hover { background: var(--paper); }
	.hit.active { box-shadow: inset 2px 0 var(--accent); }
	.ho { grid-row: span 2; font-size: 10px; color: var(--ink-mute); padding-top: 2px; }
	.hn { font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.hm { font-size: 9.5px; color: var(--ink-mute); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.cf { display: flex; justify-content: flex-end; gap: 6px; margin-top: 4px; }
	.cf:empty { display: none; }

	@media (pointer: coarse) {
		.cells { gap: 3px; }
		.cell { --cell: 14px; }
		.bar { width: 7px; height: 20px; }
		.pair { padding: 7px 4px; }
		.hit { padding: 7px 2px; }
	}
	@media (min-width: 1024px) and (max-width: 1279px) { .crow { grid-template-columns: 78px minmax(0, 1fr); } }
</style>
