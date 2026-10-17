<script lang="ts">
	import { getTrueColorPreset, truecolorPresets, type Triple } from '$lib/data';
	import { AUTO_HIGH_PERCENTILES, AUTO_LOW_PERCENTILES, hexOf, moveEnd, toneCurve, toneDiffersFromPreset, tonePixel, windowOf } from '$lib/raster/truecolor';
	import { getSession } from '$lib/state/session.svelte';

	/**
	 * III Read for a true-color plate: the tone pipeline's controls. Reflectance
	 * → linear stretch → gamma → sigmoidal contrast → OKLab saturation, the same
	 * steps (and numbers) the print pipeline runs (src/spectralcular/truecolor.py).
	 */
	const session = getSession();
	const tc = $derived(session.draft.truecolor);
	const stats = $derived(session.draft.rgbStats);
	const st = $derived(tc?.stretch ?? null);
	const win = $derived(st ? windowOf(st) : null);
	const linked = $derived(st?.mode === 'linked');
	const preset = $derived(tc?.preset ? getTrueColorPreset(tc.preset) : undefined);

	const CH = ['R', 'G', 'B'] as const;
	const CH_INK = ['#b4413a', '#4d7f3f', '#3c64a0'];

	const modified = $derived(toneDiffersFromPreset(preset, tc));

	// ----- histogram axis + markers ----------------------------------------

	const axis = $derived.by((): [number, number] => {
		const lo = Math.min(stats?.edges[0] ?? 0, ...(win?.lo ?? [0]));
		const hi = Math.max(stats?.edges[1] ?? 0.4, ...(win?.hi ?? [0.3]).map((v) => v * 1.04));
		return [lo, hi > lo ? hi : lo + 0.1];
	});
	const pos = (v: number) => ((v - axis[0]) / (axis[1] - axis[0])) * 100;
	const clampPct = (p: number) => Math.max(0, Math.min(100, p));

	/** Three histogram outlines (SVG paths in a 0..100 × 0..100 box), shared y scale. */
	const histPaths = $derived.by(() => {
		if (!stats) return [];
		const [e0, e1] = stats.edges;
		const n = stats.channels[0].counts.length;
		const w = (e1 - e0) / n;
		const max = Math.max(1, ...stats.channels.flatMap((c) => c.counts));
		return stats.channels.map((c) => {
			let d = `M ${pos(e0)} 100`;
			c.counts.forEach((k, i) => {
				const y = 100 - (k / max) * 100;
				d += ` L ${pos(e0 + i * w)} ${y} L ${pos(e0 + (i + 1) * w)} ${y}`;
			});
			return `${d} L ${pos(e1)} 100 Z`;
		});
	});

	/** Gray reflectance through the tone: how a neutral surface at each value prints. */
	const greyRamp = $derived.by(() => {
		if (!tc) return '';
		const stops = Array.from({ length: 24 }, (_, i) => {
			const x = axis[0] + (i / 23) * (axis[1] - axis[0]);
			const rgb = tonePixel([x, x, x], tc);
			return `${rgb ? hexOf(rgb) : 'transparent'} ${((i / 23) * 100).toFixed(1)}%`;
		});
		return `linear-gradient(to right, ${stops.join(', ')})`;
	});

	type Marker = { key: string; ch: number | null; end: 'lo' | 'hi'; value: number };
	const markers = $derived.by((): Marker[] => {
		if (!win) return [];
		if (linked) return [{ key: 'lo', ch: null, end: 'lo', value: win.lo[0] }, { key: 'hi', ch: null, end: 'hi', value: win.hi[0] }];
		return [0, 1, 2].flatMap((c) => [
			{ key: `lo${c}`, ch: c, end: 'lo' as const, value: win.lo[c] },
			{ key: `hi${c}`, ch: c, end: 'hi' as const, value: win.hi[c] }
		]);
	});

	let histEl = $state<HTMLDivElement | null>(null);
	let drag = $state<Marker | null>(null);
	const MIN_GAP = 0.005;

	function valueAt(clientX: number): number {
		const r = histEl!.getBoundingClientRect();
		const f = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
		return Math.round((axis[0] + f * (axis[1] - axis[0])) * 10000) / 10000;
	}
	function setEnd(m: Marker, v: number) {
		if (!win || !st) return;
		session.setStretch(moveEnd(win.lo as Triple, win.hi as Triple, st.mode, m.end, m.ch, v, MIN_GAP));
	}
	function onDown(e: PointerEvent) {
		if (!histEl || !markers.length) return;
		const v = valueAt(e.clientX);
		const r = histEl.getBoundingClientRect();
		const px = (x: number) => (pos(x) / 100) * r.width;
		const hit = markers
			.map((m) => ({ m, d: Math.abs(px(m.value) - px(v)) }))
			.sort((a, b) => a.d - b.d)[0];
		if (!hit || hit.d > 10) return;
		drag = hit.m;
		histEl.setPointerCapture(e.pointerId);
		e.preventDefault();
	}
	function onMove(e: PointerEvent) {
		if (drag) setEnd(drag, valueAt(e.clientX));
	}
	function onUp(e: PointerEvent) {
		if (!drag) return;
		drag = null;
		histEl?.releasePointerCapture(e.pointerId);
	}

	// ----- tone curve ------------------------------------------------------

	const curves = $derived.by(() => {
		if (!tc) return [];
		const chans = linked ? [0] : [0, 1, 2];
		return chans.map((c) => {
			const pts = Array.from({ length: 49 }, (_, i) => {
				const x = axis[0] + (i / 48) * (axis[1] - axis[0]);
				return `${pos(x).toFixed(2)},${(100 - toneCurve(x, tc, c) * 100).toFixed(2)}`;
			});
			return { c, ink: linked ? 'var(--ink)' : CH_INK[c], d: `M ${pts.join(' L ')}` };
		});
	});

	// ----- numeric edits -----------------------------------------------------

	function editEnd(end: 'lo' | 'hi', ch: number | null, v: number) {
		if (!Number.isFinite(v) || !win) return;
		setEnd({ key: '', ch, end, value: v }, v);
	}
	const fmt = (v: number) => v.toFixed(3);
	const pctLabel = (p: number) => `p${p}`;
</script>

<div class="section read tone">
	<p class="measures">
		Reflectance in, print color out: a <b>stretch</b> sets black and white points, then <b>gamma</b>, <b>contrast</b> and
		<b>saturation</b> shape the tones. The printed plate uses exactly these numbers.
	</p>

	<div class="presets" role="radiogroup" aria-label="Preset">
		{#each truecolorPresets as p (p.id)}
			<button class="opt" class:on={tc?.preset === p.id} role="radio" aria-checked={tc?.preset === p.id} onclick={() => session.applyTrueColorPreset(p.id)} title={p.description}>
				<span class="t">{p.label}{#if tc?.preset === p.id && modified}<span class="badge mono">edited</span>{/if}</span>
				<span class="d">{p.description}</span>
			</button>
		{/each}
	</div>

	{#if tc && st && win}
		<div class="ruler">
			<div
				class="hist"
				class:dragging={!!drag}
				bind:this={histEl}
				onpointerdown={onDown}
				onpointermove={onMove}
				onpointerup={onUp}
				onpointercancel={onUp}
				role="slider"
				aria-label="Stretch window (drag the markers)"
				aria-valuemin={axis[0]}
				aria-valuemax={axis[1]}
				aria-valuenow={win.lo[0]}
				tabindex="-1"
			>
				{#if histPaths.length}
					<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
						{#each histPaths as d, c (c)}<path {d} fill={CH_INK[c]} fill-opacity="0.22" stroke={CH_INK[c]} stroke-width="0.6" vector-effect="non-scaling-stroke" />{/each}
					</svg>
				{:else}
					<span class="hint">
						{#if session.statsStatus === 'loading'}Reading the frame's red, green and blue at 80 m…{:else if session.statsStatus === 'error'}<span class="bad">{session.statsError}</span>{:else if session.draft.scene}The band histograms appear once the scene is sampled.{:else}Pin a scene to see this frame's reflectance.{/if}
					</span>
				{/if}
				<span class="shade" style:left="0" style:width="{clampPct(pos(Math.min(...win.lo)))}%"></span>
				<span class="shade" style:left="{clampPct(pos(Math.max(...win.hi)))}%" style:right="0"></span>
				{#each markers as m (m.key)}
					<span class="mk" class:hi={m.end === 'hi'} class:active={drag?.key === m.key} style:left="{clampPct(pos(m.value))}%" style:--mk={m.ch === null ? 'var(--ink)' : CH_INK[m.ch]} title="{m.ch === null ? '' : `${CH[m.ch]} `}{m.end} {fmt(m.value)}"></span>
				{/each}
			</div>
			<div class="ramp" style:background={greyRamp} title="a neutral surface at each reflectance, as it prints"></div>
			<div class="axis mono">
				<span>{fmt(axis[0])}</span>
				<span class="lbl">reflectance{stats ? ` · R ${fmt(stats.channels[0].percentiles[stats.qs.indexOf(50)])} G ${fmt(stats.channels[1].percentiles[stats.qs.indexOf(50)])} B ${fmt(stats.channels[2].percentiles[stats.qs.indexOf(50)])} median` : ''}</span>
				<span>{fmt(axis[1])}</span>
			</div>
		</div>

		<div class="eyebrow head">Stretch <span class="sub">black and white points, in reflectance</span></div>
		<div class="row">
			<div class="seg" role="group" aria-label="Channels">
				<button class:on={linked} onclick={() => session.setStretch({ mode: 'linked' })} title="One window for all three bands: keeps the scene's color balance">linked</button>
				<button class:on={st.mode === 'dark-object'} onclick={() => session.setStretch({ mode: 'dark-object' })} title="A black point per band, one shared gain: removes the haze offset and keeps the color balance">haze offset</button>
				<button class:on={st.mode === 'per-channel'} onclick={() => session.setStretch({ mode: 'per-channel' })} title="An independent window per band: also neutralises color casts, but colors become relative to this scene">per band</button>
			</div>
			<div class="seg" role="group" aria-label="Window source">
				<button class:on={st.source === 'auto'} disabled={!stats} onclick={() => session.setStretch({ source: 'auto' })} title={stats ? 'Follow this frame’s percentiles' : 'Needs the frame’s statistics'}>auto</button>
				<button class:on={st.source === 'manual'} onclick={() => session.setStretch({ lo: [...win.lo], hi: [...win.hi] })} title="Keep the numbers as set">fixed</button>
			</div>
		</div>
		{#if st.source === 'auto'}
			<div class="pcts">
				<span class="eyebrow">low</span>
				{#each AUTO_LOW_PERCENTILES as p (p)}
					<button class="btn sm" class:primary={st.pLo === p} onclick={() => session.setStretch({ source: 'auto', pLo: p })}>{pctLabel(p)}</button>
				{/each}
				<span class="eyebrow">high</span>
				{#each AUTO_HIGH_PERCENTILES as p (p)}
					<button class="btn sm" class:primary={st.pHi === p} onclick={() => session.setStretch({ source: 'auto', pHi: p })}>{pctLabel(p)}</button>
				{/each}
			</div>
			{#if !st.lo}<p class="note">Showing a provisional 0–0.30 window until the frame's statistics arrive.</p>{/if}
		{/if}
		<div class="ends" class:per={!linked}>
			{#if linked}
				<label class="field"><span>black</span><input class="input" type="number" step="0.005" value={fmt(win.lo[0])} onchange={(e) => editEnd('lo', null, e.currentTarget.valueAsNumber)} /></label>
				<label class="field"><span>white</span><input class="input" type="number" step="0.005" value={fmt(win.hi[0])} onchange={(e) => editEnd('hi', null, e.currentTarget.valueAsNumber)} /></label>
			{:else}
				<span></span><span class="eyebrow">black</span><span class="eyebrow">white</span>
				{#each [0, 1, 2] as c (c)}
					<span class="ch mono" style:color={CH_INK[c]}>{CH[c]}</span>
					<input class="input" type="number" step="0.005" aria-label="{CH[c]} black" value={fmt(win.lo[c])} onchange={(e) => editEnd('lo', c, e.currentTarget.valueAsNumber)} />
					<input class="input" type="number" step="0.005" aria-label="{CH[c]} white" value={fmt(win.hi[c])} onchange={(e) => editEnd('hi', c, e.currentTarget.valueAsNumber)} />
				{/each}
			{/if}
		</div>

		<div class="eyebrow head">Tone <span class="sub">applied after the stretch, in this order</span></div>
		<div class="sliders">
			<label class="sl">
				<span class="k mono" title="t^(1/γ): above 1 lifts the midtones, below 1 deepens them">gamma</span>
				<input type="range" min="0.3" max="3" step="0.01" value={tc.gamma} oninput={(e) => session.setTone({ gamma: e.currentTarget.valueAsNumber })} />
				<input class="input num" type="number" min="0.2" max="5" step="0.05" value={tc.gamma} onchange={(e) => e.currentTarget.valueAsNumber >= 0.2 && session.setTone({ gamma: e.currentTarget.valueAsNumber })} />
			</label>
			<label class="sl">
				<span class="k mono" title="Sigmoidal (S-curve) strength; 0 is off">contrast</span>
				<input type="range" min="0" max="20" step="0.1" value={tc.contrast} oninput={(e) => session.setTone({ contrast: e.currentTarget.valueAsNumber })} />
				<input class="input num" type="number" min="0" max="30" step="0.5" value={tc.contrast} onchange={(e) => e.currentTarget.valueAsNumber >= 0 && session.setTone({ contrast: e.currentTarget.valueAsNumber })} />
			</label>
			<label class="sl" class:off={tc.contrast < 0.001}>
				<span class="k mono" title="Where the S-curve pivots: lower favors the shadows">midpoint</span>
				<input type="range" min="0.05" max="0.95" step="0.01" value={tc.midpoint} disabled={tc.contrast < 0.001} oninput={(e) => session.setTone({ midpoint: e.currentTarget.valueAsNumber })} />
				<input class="input num" type="number" min="0.05" max="0.95" step="0.01" value={tc.midpoint} disabled={tc.contrast < 0.001} onchange={(e) => { const v = e.currentTarget.valueAsNumber; if (v > 0 && v < 1) session.setTone({ midpoint: v }); }} />
			</label>
			<label class="sl">
				<span class="k mono" title="OKLab chroma scale: 1 leaves color alone, 0 is grayscale">saturation</span>
				<input type="range" min="0" max="2" step="0.01" value={tc.saturation} oninput={(e) => session.setTone({ saturation: e.currentTarget.valueAsNumber })} />
				<input class="input num" type="number" min="0" max="3" step="0.05" value={tc.saturation} onchange={(e) => e.currentTarget.valueAsNumber >= 0 && session.setTone({ saturation: e.currentTarget.valueAsNumber })} />
			</label>
		</div>

		<div class="curve">
			<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="Tone curve: reflectance → print value">
				<line x1="0" y1="100" x2="100" y2="0" class="diag" vector-effect="non-scaling-stroke" />
				{#each curves as k (k.c)}<path d={k.d} fill="none" stroke={k.ink} stroke-width="1.2" vector-effect="non-scaling-stroke" />{/each}
			</svg>
			<div class="cap mono"><span>tone curve</span><span>reflectance → print{linked ? '' : ' · per band'}</span></div>
		</div>

		<div class="row foot">
			<label class="mask eyebrow"><input type="checkbox" checked={session.draft.maskEnabled} onchange={(e) => { session.setMaskEnabled(e.currentTarget.checked); session.refreshStats(); }} /> mask clouds</label>
			<button class="btn ghost sm" onclick={() => session.resetToPreset()} disabled={!modified} title="Back to {preset?.label ?? 'the default preset'}">reset to preset</button>
		</div>
		{#if stats}
			<div class="readout mono" title="valid / masked">valid {(stats.validFraction * 100).toFixed(0)}% · masked {(stats.maskedFraction * 100).toFixed(0)}% · n={stats.nPixels} · {Math.round(stats.resolutionM)} m</div>
		{/if}

		{#if !session.readDone && session.draft.scene}
			<div class="done"><button class="btn ghost sm" onclick={() => session.confirmReading()}>Keep this tone</button></div>
		{/if}
	{/if}
</div>

<style>
	.measures { margin: 0 0 8px; font-size: 12.5px; line-height: 1.4; color: var(--ink-soft); }
	.presets { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; }
	.opt { display: flex; flex-direction: column; gap: 2px; text-align: left; padding: 5px 7px 6px; background: transparent; border: var(--hair) solid var(--rule); }
	.opt:hover { background: var(--paper-2); border-color: var(--ink-mute); }
	.opt.on { border-color: var(--accent); box-shadow: inset 2px 0 var(--accent); background: var(--paper-2); }
	.opt .t { display: flex; align-items: baseline; gap: 6px; font-size: 13px; }
	.opt .d { font-size: 10.5px; line-height: 1.3; color: var(--ink-soft); display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
	.badge { font-size: 9px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--accent); }

	.ruler { margin-top: 10px; user-select: none; }
	.hist { position: relative; height: 58px; background: var(--paper-2); border-left: var(--hair) solid var(--rule); border-right: var(--hair) solid var(--rule); overflow: hidden; touch-action: none; cursor: default; }
	.hist svg { position: absolute; inset: 0; width: 100%; height: 100%; mix-blend-mode: multiply; }
	.hist.dragging { cursor: ew-resize; }
	.hint { position: absolute; inset: 0; display: grid; place-items: center; padding: 0 10px; text-align: center; color: var(--ink-mute); font-size: 11.5px; font-style: italic; }
	.bad { color: var(--bad); font-style: normal; }
	.shade { position: absolute; top: 0; bottom: 0; background: color-mix(in oklab, var(--ink) 7%, transparent); pointer-events: none; }
	.mk { position: absolute; top: 0; bottom: 0; width: 0; border-left: 1.5px solid var(--mk); cursor: ew-resize; }
	.mk::after { content: ''; position: absolute; top: 0; left: -4.5px; border: 4px solid transparent; border-top: 5px solid var(--mk); }
	.mk.hi::after { top: auto; bottom: 0; border-top: 4px solid transparent; border-bottom: 5px solid var(--mk); }
	.mk::before { content: ''; position: absolute; top: 0; bottom: 0; left: -7px; width: 14px; }
	.mk.active { border-left-width: 2px; }
	.ramp { height: 8px; box-shadow: inset 0 0 0 0.5px rgb(26 26 26 / 0.18); }
	.axis { display: flex; justify-content: space-between; gap: 8px; font-size: 9.5px; color: var(--ink-mute); margin-top: 2px; }
	.axis .lbl { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

	.head { display: flex; gap: 8px; align-items: baseline; margin-top: 12px; }
	.head .sub { text-transform: none; letter-spacing: 0; font-family: var(--serif); font-style: italic; font-size: 11.5px; }
	.row { display: flex; justify-content: space-between; align-items: center; gap: 8px 12px; margin-top: 6px; flex-wrap: wrap; }
	.pcts { display: flex; gap: 4px; margin-top: 6px; align-items: center; flex-wrap: wrap; }
	.pcts .eyebrow { margin: 0 2px 0 4px; }
	.pcts .eyebrow:first-child { margin-left: 0; }
	.note { margin: 6px 0 0; font-size: 11px; font-style: italic; color: var(--ink-mute); }
	.ends { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 8px; margin-top: 8px; }
	.ends.per { grid-template-columns: 14px 1fr 1fr; align-items: center; gap: 4px 8px; }
	.ch { font-size: 10.5px; font-weight: 700; }

	.sliders { display: flex; flex-direction: column; gap: 4px; margin-top: 6px; }
	.sl { display: grid; grid-template-columns: 64px 1fr 58px; align-items: center; gap: 8px; }
	.sl .k { font-size: 10px; letter-spacing: 0.06em; color: var(--ink-soft); }
	.sl input[type='range'] { width: 100%; margin: 0; }
	.sl .num { padding: 2px 5px; }
	.sl.off .k { color: var(--ink-mute); }

	.curve { margin-top: 10px; display: grid; grid-template-columns: 96px 1fr; gap: 10px; align-items: end; }
	.curve svg { width: 96px; height: 64px; background: var(--paper-2); border: var(--hair) solid var(--rule); }
	.diag { stroke: var(--rule); stroke-width: 1; stroke-dasharray: 2 2; }
	.cap { display: flex; flex-direction: column; gap: 2px; font-size: 9.5px; color: var(--ink-mute); }
	.foot { margin-top: 10px; }
	.mask { display: flex; gap: 4px; align-items: center; white-space: nowrap; }
	.readout { margin-top: 6px; font-size: 10.5px; color: var(--ink-mute); }
	.done { margin-top: 10px; display: flex; justify-content: flex-end; }
</style>
