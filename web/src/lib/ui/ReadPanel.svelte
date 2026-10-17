<script lang="ts">
	import { getSurface } from '$lib/data';
	import {
		centerOf,
		colorAt,
		composition,
		fmtValue,
		readingAxis,
		surfaceLabel,
		surfacesInFrame,
		type StretchOption
	} from '$lib/data/reading';
	import { getSession } from '$lib/state/session.svelte';
	import StretchControls from './StretchControls.svelte';
	import TonePanel from './TonePanel.svelte';

	/**
	 * III Read: what the index measures, where this frame's values fall among
	 * familiar surfaces, and which stretch (and kind of ramp) suits it. Sits
	 * between choosing the index and choosing the colormap.
	 */
	const session = getSession();
	const def = $derived(session.indexDef);
	const guide = $derived(def?.guide ?? null);
	const stats = $derived(session.draft.stats);
	const summary = $derived(stats?.summary ?? null);
	const hist = $derived(stats?.histogram ?? null);
	const range = $derived(session.range);
	const reading = $derived(session.reading);
	const center = $derived(def ? centerOf(def) : null);
	const axis = $derived(def ? readingAxis(def, summary, range) : ([-1, 1] as [number, number]));
	const stops = $derived(session.colormapDef?.previewStops ?? []);
	const reversed = $derived(session.draft.colormap.reversed);

	const pos = (v: number) => ((v - axis[0]) / (axis[1] - axis[0])) * 100;
	const clampPct = (p: number) => Math.max(0, Math.min(100, p));
	/** color a value prints as under the current stretch and ramp */
	function ink(v: number): string {
		if (!range || !(range.vmax > range.vmin)) return 'var(--ink-mute)';
		return colorAt(stops, reversed, (v - range.vmin) / (range.vmax - range.vmin));
	}

	const bars = $derived.by(() => {
		if (!hist) return [];
		const [lo, hi] = hist.edges;
		const w = (hi - lo) / hist.counts.length;
		const max = Math.max(1, ...hist.counts);
		return hist.counts.map((c, i) => {
			const a = lo + i * w;
			return { left: pos(a), width: (w / (axis[1] - axis[0])) * 100, height: (c / max) * 100, color: ink(a + w / 2) };
		});
	});

	const classes = $derived(
		(guide?.classes ?? []).map((c, i) => {
			const a = Math.max(c.lo ?? axis[0], axis[0]);
			const b = Math.min(c.hi ?? axis[1], axis[1]);
			return { ...c, i, left: clampPct(pos(a)), width: Math.max(0, clampPct(pos(b)) - clampPct(pos(a))), a, b };
		})
	);
	const shares = $derived(guide ? composition(guide.classes, hist) : null);

	// Surface ticks. Ticks closer than a few pixels share one label (the index
	// can't separate those surfaces, which is worth seeing); labels stack in up
	// to three rows so neighbors don't collide.
	let rulerW = $state(300);
	const ROWS = 3;
	const CHAR_PX = 5.4;
	const MAX_SLIDE_PX = 48;
	const ticks = $derived.by(() => {
		if (!def) return [];
		return def.surfaces.map((s) => {
			const p = pos(s.value);
			return { ...s, x: clampPct(p), off: p < 0 ? 'lo' : p > 100 ? 'hi' : null, desc: getSurface(s.id)?.description ?? '' };
		});
	});
	const tickLabels = $derived.by(() => {
		const clusters: (typeof ticks)[] = [];
		for (const t of ticks) {
			const last = clusters.at(-1);
			if (last && (((t.x - last.at(-1)!.x) / 100) * rulerW < 9 || (t.off && t.off === last[0].off))) last.push(t);
			else clusters.push([t]);
		}
		const ends = Array.from({ length: ROWS }, () => -Infinity);
		return clusters.map((c) => {
			const names = c.map((t) => surfaceLabel(t.id));
			const off = c[0].off;
			let text = names.length > 3 ? `${names.slice(0, 2).join(' · ')} +${names.length - 2}` : names.join(' · ');
			text = `${off === 'lo' ? '◂ ' : ''}${text}${off === 'hi' ? ' ▸' : ''}`;
			const x = (((c[0].x + c.at(-1)!.x) / 2) / 100) * rulerW;
			const wPx = text.length * CHAR_PX + 6;
			// Center on the ticks; otherwise slide right into the first row with room.
			const ideal = Math.max(0, Math.min(rulerW - wPx, x - wPx / 2));
			let row = -1;
			let left = ideal;
			for (let r = 0; r < ROWS; r++) {
				const l = Math.max(ideal, ends[r] + 4);
				if (l - ideal <= MAX_SLIDE_PX && l + wPx <= rulerW) {
					row = r;
					left = l;
					break;
				}
			}
			if (row >= 0) ends[row] = left + wPx;
			const title = c.map((t) => `${surfaceLabel(t.id)} ${fmtValue(t.value)}`).join(' · ');
			return { key: c.map((t) => t.id).join('+'), text, left, row, title, dim: c.every((t) => summary && !inFrame.some((s) => s.id === t.id)) };
		});
	});
	const inFrame = $derived(def ? surfacesInFrame(def, summary) : []);

	function choose(o: StretchOption) {
		session.applyStretch(o);
	}
	const hasGuide = $derived(!!guide);
</script>

{#if session.isTrueColor}
	<TonePanel />
{:else if def}
	<div class="section read">
		{#if guide}
			<p class="measures">{guide.measures}</p>
			<dl class="hl">
				<dt class="eyebrow">high</dt><dd>{guide.high}</dd>
				<dt class="eyebrow">low</dt><dd>{guide.low}</dd>
			</dl>
		{:else}
			<p class="measures muted">
				No written guide for {def.name}. The ticks show where measured surfaces fall on it{def.bounds ? `; the formula spans ${def.bounds.loOpen ? '−∞' : fmtValue(def.bounds.lo)} … ${def.bounds.hiOpen ? '∞' : fmtValue(def.bounds.hi)}` : ''}.
			</p>
		{/if}

		<div class="ruler" bind:clientWidth={rulerW} aria-label="{def.name} value ruler">
			{#if classes.length}
				<div class="classes">
					{#each classes as c (c.i)}
						<span class="cls" class:alt={c.i % 2 === 1} style:left="{c.left}%" style:width="{c.width}%" title="{c.label}: {c.lo === null ? '<' : fmtValue(c.lo)}{c.lo !== null && c.hi !== null ? ' … ' : ''}{c.hi === null ? '+' : fmtValue(c.hi)}">
							{#if c.width > 5}<span class="num mono">{c.i + 1}</span>{/if}
						</span>
					{/each}
				</div>
			{/if}
			<div class="hist">
				{#each bars as b, i (i)}
					<span class="bar" style:left="{b.left}%" style:width="{b.width}%" style:height="{b.height}%" style:background={b.color}></span>
				{/each}
				{#if !hist}
					<span class="hint">
						{#if session.statsStatus === 'loading'}Reading the frame's pixels at 80 m…{:else if session.statsStatus === 'error'}<span class="bad">{session.statsError}</span>{:else if session.draft.scene}The frame's histogram appears once the scene is sampled.{:else}Pin a scene to see where this frame falls.{/if}
					</span>
				{/if}
				{#if center && center.value > axis[0] && center.value < axis[1]}
					{@const cx = pos(center.value)}
					<span class="center" style:left="{cx}%" title="{fmtValue(center.value)} · {center.label}"></span>
					<span class="clabel mono" class:flip={cx > 60} style:left="{cx}%">{fmtValue(center.value)}{center.label ? ` ${center.label}` : ''}</span>
				{/if}
			</div>
			{#if range && range.vmax > range.vmin}
				<div class="ramp">
					<span class="clamp lo" style:width="{clampPct(pos(range.vmin))}%" style:background={ink(range.vmin)}></span>
					<span class="grad" style:left="{clampPct(pos(range.vmin))}%" style:width="{clampPct(pos(range.vmax)) - clampPct(pos(range.vmin))}%" style:background="linear-gradient(to {reversed ? 'left' : 'right'}, {stops.join(',')})"></span>
					<span class="clamp hi" style:left="{clampPct(pos(range.vmax))}%" style:background={ink(range.vmax)}></span>
				</div>
			{/if}
			<div class="ticks" style:height="{ROWS * 12 + 4}px">
				{#each ticks as t (t.id)}
					<span class="tick" class:dim={summary && !inFrame.some((s) => s.id === t.id)} style:left="{t.x}%" title="{t.desc} {def.name} = {fmtValue(t.value)}"></span>
				{/each}
				{#each tickLabels as l (l.key)}
					{#if l.row >= 0}
						<span class="tlabel mono" class:dim={l.dim} style:left="{l.left}px" style:top="{l.row * 12 + 4}px" title={l.title}>{l.text}</span>
					{/if}
				{/each}
			</div>
			<div class="axis mono">
				<span>{fmtValue(axis[0])}</span>
				<span class="lbl">{def.name}{summary ? ` · this frame ${fmtValue(summary.p2)} … ${fmtValue(summary.p98)}` : ''}</span>
				<span>{fmtValue(axis[1])}</span>
			</div>
		</div>

		{#if classes.length}
			<ol class="shares">
				{#each classes as c (c.i)}
					{@const f = shares?.[c.i]?.fraction ?? null}
					<li class:empty={f !== null && f < 0.005} title="{c.label}{f !== null ? ` · ${Math.round(f * 100)}% of this frame` : ''}">
						<span class="num mono">{c.i + 1}</span>
						<span class="sw" style:background="linear-gradient(to right, {ink(c.a)}, {ink((c.a + c.b) / 2)}, {ink(c.b)})" title="how this band prints with the current ramp"></span>
						<span class="lbl">{c.label}</span>
						<span class="range mono">{c.lo === null ? '<' : fmtValue(c.lo)}{c.lo !== null && c.hi !== null ? '…' : ''}{c.hi === null ? '+' : fmtValue(c.hi)}</span>
						{#if f !== null}
							<span class="pct mono">{Math.round(f * 100)}%</span>
							<span class="meter" style:width="{Math.round(f * 100)}%"></span>
						{/if}
					</li>
				{/each}
			</ol>
		{:else if summary && inFrame.length}
			<p class="inframe">This frame's range covers values like <b>{inFrame.map((s) => surfaceLabel(s.id)).join(', ')}</b>.</p>
		{/if}

		<div class="opts" role="radiogroup" aria-label="Stretch">
			<div class="eyebrow head">Stretch <span class="sub">how values meet the ramp</span></div>
			{#each reading?.options ?? [] as o (o.id)}
				<button class="opt" class:on={o.active} role="radio" aria-checked={o.active} onclick={() => choose(o)}>
					<span class="t">
						{o.title}
						{#if o.recommended}<span class="badge mono">suggested</span>{/if}
						<span class="kind mono">{o.ramp}</span>
					</span>
					<span class="d">{o.detail}</span>
					{#if o.use !== null}
						<span class="use" title="share of the ramp this frame's values fill"><span style:width="{Math.round(o.use * 100)}%"></span></span>
					{/if}
				</button>
			{/each}
		</div>

		{#if reading?.notes.length}
			<ul class="notes">
				{#each reading.notes as n, i (i)}
					<li class:warn={n.level === 'warn'}>{n.text}</li>
				{/each}
			</ul>
		{/if}

		<details class="fine">
			<summary class="eyebrow">Fine-tune</summary>
			<StretchControls />
		</details>

		{#if guide && (guide.caveats.length || guide.source)}
			<details class="caveats">
				<summary class="eyebrow">Reading {def.name} well{guide.caveats.length ? ` · ${guide.caveats.length}` : ''}</summary>
				<ul>
					{#each guide.caveats as c, i (i)}<li>{c}</li>{/each}
				</ul>
				{#if guide.source}<p class="src">{guide.source}</p>{/if}
			</details>
		{/if}

		{#if !session.readDone && session.draft.scene}
			<div class="done"><button class="btn ghost sm" onclick={() => session.confirmReading()}>Keep this stretch</button></div>
		{/if}
		{#if !hasGuide && def.surfaces.length === 0}
			<p class="measures muted">No reference surfaces were measured on this platform for {def.name}'s bands.</p>
		{/if}
	</div>
{:else}
	<div class="section read">
		<p class="measures muted">Pick an index in II to see what it measures and how this frame's values should meet the ramp.</p>
	</div>
{/if}

<style>
	.measures { margin: 0 0 6px; font-size: 13px; line-height: 1.4; }
	.muted { color: var(--ink-soft); font-style: italic; font-size: 12px; }
	.hl { display: grid; grid-template-columns: auto 1fr; gap: 1px 8px; margin: 0 0 10px; font-size: 12px; color: var(--ink-soft); }
	.hl dt { padding-top: 2px; }
	.hl dd { margin: 0; }

	.ruler { position: relative; user-select: none; }
	.classes { position: relative; height: 13px; }
	.cls { position: absolute; top: 0; bottom: 0; background: var(--paper-edge); border-right: var(--hair) solid var(--paper); display: grid; place-items: center; }
	.cls.alt { background: var(--rule-soft); }
	.cls .num { font-size: 8.5px; color: var(--ink-mute); }
	.hist { position: relative; height: 46px; background: var(--paper-2); border-left: var(--hair) solid var(--rule); border-right: var(--hair) solid var(--rule); overflow: hidden; }
	.bar { position: absolute; bottom: 0; min-width: 1px; }
	.hint { position: absolute; inset: 0; display: grid; place-items: center; padding: 0 10px; text-align: center; color: var(--ink-mute); font-size: 11.5px; font-style: italic; }
	.bad { color: var(--bad); font-style: normal; }
	.center { position: absolute; top: 0; bottom: 0; border-left: 1px dashed var(--ink-soft); }
	.clabel { position: absolute; top: 2px; padding: 0 3px 0 4px; font-size: 8.5px; color: var(--ink-soft); white-space: nowrap; background: color-mix(in oklab, var(--paper-2) 80%, transparent); }
	.clabel.flip { transform: translateX(-100%); padding: 0 4px 0 0; }
	.ramp { position: relative; height: 7px; }
	.ramp > span { position: absolute; top: 0; bottom: 0; }
	.clamp { opacity: 0.35; }
	.clamp.lo { left: 0; }
	.clamp.hi { right: 0; }
	.ticks { position: relative; border-top: var(--hair) solid var(--rule); }
	.tick { position: absolute; top: -4px; height: 7px; border-left: 1px solid var(--ink); }
	.tlabel { position: absolute; font-size: 8.5px; line-height: 11px; color: var(--ink-soft); white-space: nowrap; }
	.dim { opacity: 0.4; }
	.axis { display: flex; justify-content: space-between; gap: 8px; font-size: 9.5px; color: var(--ink-mute); }
	.axis .lbl { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

	.shares { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 1px; }
	.shares li { position: relative; isolation: isolate; display: grid; grid-template-columns: 12px 30px 1fr auto 34px; align-items: center; gap: 6px; padding: 2px 0; font-size: 11.5px; }
	.shares li.empty { color: var(--ink-mute); }
	.shares .num { font-size: 9px; color: var(--ink-mute); }
	.shares .sw { height: 9px; box-shadow: inset 0 0 0 0.5px rgb(26 26 26 / 0.18); }
	.shares .lbl { min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.shares .range { font-size: 9.5px; color: var(--ink-mute); }
	.shares .pct { font-size: 10.5px; text-align: right; }
	.shares .meter { position: absolute; left: 0; top: 0; bottom: 0; z-index: -1; background: color-mix(in oklab, var(--accent) 9%, transparent); }
	.inframe { margin: 8px 0 0; font-size: 12px; color: var(--ink-soft); }

	.opts { display: flex; flex-direction: column; gap: 4px; margin-top: 12px; }
	.head { display: flex; gap: 8px; align-items: baseline; }
	.head .sub { text-transform: none; letter-spacing: 0; font-family: var(--serif); font-style: italic; font-size: 11.5px; }
	.opt { display: flex; flex-direction: column; gap: 2px; text-align: left; padding: 6px 8px 7px; background: transparent; border: var(--hair) solid var(--rule); position: relative; }
	.opt:hover { background: var(--paper-2); border-color: var(--ink-mute); }
	.opt.on { border-color: var(--accent); box-shadow: inset 2px 0 var(--accent); background: var(--paper-2); }
	.opt .t { display: flex; align-items: baseline; gap: 6px; font-size: 13px; }
	.badge { font-size: 9px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--accent); }
	.kind { margin-left: auto; font-size: 9px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-mute); }
	.opt .d { font-size: 11.5px; line-height: 1.35; color: var(--ink-soft); }
	.use { display: block; height: 2px; background: var(--rule-soft); margin-top: 3px; }
	.use span { display: block; height: 100%; background: var(--ink-soft); }

	.notes { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
	.notes li { font-size: 11.5px; line-height: 1.35; color: var(--ink-soft); padding-left: 12px; position: relative; }
	.notes li::before { content: '·'; position: absolute; left: 3px; color: var(--ink-mute); }
	.notes li.warn { color: var(--ink); }
	.notes li.warn::before { content: '▲'; font-size: 7px; top: 3px; left: 0; color: var(--warn); }

	details { margin-top: 10px; }
	summary { cursor: pointer; list-style: none; }
	summary::-webkit-details-marker { display: none; }
	summary::before { content: '▸ '; }
	details[open] > summary::before { content: '▾ '; }
	.fine :global(.stretch) { margin-top: 6px; }
	.caveats ul { margin: 6px 0 0; padding-left: 16px; font-size: 11.5px; color: var(--ink-soft); display: flex; flex-direction: column; gap: 3px; }
	.caveats .src { margin: 6px 0 0; font-size: 10.5px; color: var(--ink-mute); font-style: italic; }
	.done { margin-top: 10px; display: flex; justify-content: flex-end; }
</style>
