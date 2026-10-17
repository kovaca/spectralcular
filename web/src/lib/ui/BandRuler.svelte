<script lang="ts">
	/**
	 * "Bands in use": a wavelength axis with one tick per sensor band, the bands
	 * the current index consumes lit in the accent color. Mirrors
	 * src/spectralcular/band_ruler.py so the picker and the printed recto agree.
	 *
	 * Drawn in HTML rather than SVG so the type stays at a readable pixel size
	 * whatever the panel width; tick positions are percentages, label collisions
	 * are resolved against the measured width. Optical bands sit on a linear
	 * 400–2300 nm axis; thermal bands (Landsat TIRS) get a broken-axis segment
	 * on the right; radar platforms fall back to a row of channel labels.
	 */
	import type { PlatformDef } from '$lib/data';

	let { platform, used }: { platform: PlatformDef; used: string[] } = $props();

	let width = $state(300);
	const OPT: [number, number] = [400, 2300];
	const TH: [number, number] = [10000, 13000];
	const MIN_LABEL_GAP_PX = 22;

	type Tick = { pct: number; code: string; nm: number; used: boolean };

	const fmtNm = (nm: number) => (nm >= TH[0] ? `${(nm / 1000).toFixed(1)} µm` : `${nm}`);

	const model = $derived.by(() => {
		const entries = Object.entries(platform.bandsNm ?? {});
		const usedSet = new Set(used);
		if (entries.length === 0) {
			const assets = [...new Set(Object.values(platform.paramToAsset))];
			return { kind: 'channels' as const, assets, usedSet };
		}
		const optical = entries.filter(([, v]) => v[2] < 3000);
		const thermal = entries.filter(([, v]) => v[2] >= 3000);
		const optEnd = thermal.length ? 76 : 100; // percent of the axis given to optical
		const thStart = thermal.length ? optEnd + 5 : null;
		const pOpt = (nm: number) => ((nm - OPT[0]) / (OPT[1] - OPT[0])) * optEnd;
		const pTh = (nm: number) => (thStart ?? 0) + ((nm - TH[0]) / (TH[1] - TH[0])) * (100 - (thStart ?? 0));
		const ticks: Tick[] = [
			...optical.map(([a, [code, , nm]]) => ({ pct: pOpt(nm), code, nm, used: usedSet.has(a) })),
			...thermal.map(([a, [code, , nm]]) => ({ pct: pTh(nm), code, nm, used: usedSet.has(a) }))
		].sort((a, b) => a.pct - b.pct);
		// Used bands always keep their label; the rest only if they do not collide.
		const px = (pct: number) => (pct / 100) * width;
		const labeled = new Set<number>();
		for (const t of [...ticks].sort((a, b) => Number(b.used) - Number(a.used) || a.pct - b.pct)) {
			if (t.used || [...labeled].every((lp) => Math.abs(px(t.pct) - px(lp)) >= MIN_LABEL_GAP_PX)) labeled.add(t.pct);
		}
		const below: { pct: number; align: 'start' | 'middle' | 'end'; text: string }[] = [
			{ pct: 0, align: 'start', text: `${OPT[0]} nm` },
			{ pct: optEnd, align: 'end', text: `${OPT[1]} nm` }
		];
		// The thermal segment is too short for two edge labels at panel widths; one centered range reads fine.
		if (thStart !== null) below.push({ pct: (thStart + 100) / 2, align: 'middle', text: `${TH[0] / 1000}–${TH[1] / 1000} µm` });
		// Edge labels hang inward from their anchor, so compare against their visual center.
		const center = (b: { pct: number; align: string }) => px(b.pct) + (b.align === 'start' ? 18 : b.align === 'end' ? -18 : 0);
		for (const t of ticks) {
			if (t.nm >= TH[0]) continue; // thermal ticks are covered by the range label
			if (t.used && below.every((b) => Math.abs(px(t.pct) - center(b)) >= 36)) below.push({ pct: t.pct, align: 'middle', text: fmtNm(t.nm) });
		}
		return { kind: 'bands' as const, ticks, labeled, below, optEnd, thStart, visEnd: pOpt(700) };
	});
</script>

<div class="ruler" bind:clientWidth={width} role="img" aria-label="Sensor bands used by this index">
	{#if model.kind === 'bands'}
		<span class="axis" style:left="0" style:width="{model.optEnd}%"></span>
		<span class="vis" style:left="0" style:width="{model.visEnd}%"></span>
		{#if model.thStart !== null}
			<span class="break" style:left="{(model.optEnd + model.thStart) / 2}%">≀</span>
			<span class="axis" style:left="{model.thStart}%" style:width="{100 - model.thStart}%"></span>
		{/if}
		{#each model.ticks as t (t.code)}
			<span class="tick" class:used={t.used} style:left="{t.pct}%"></span>
			{#if model.labeled.has(t.pct)}
				<span class="code" class:used={t.used} style:left="{t.pct}%">{t.code}</span>
			{/if}
		{/each}
		{#each model.below as b (b.pct + b.text)}
			<span class="nm {b.align}" style:left="{b.pct}%">{b.text}</span>
		{/each}
	{:else}
		<span class="axis" style:left="0" style:width="100%"></span>
		{#each model.assets as a, i (a)}
			{@const pct = ((i + 0.5) / Math.max(model.assets.length, 1)) * 100}
			<span class="tick" class:used={model.usedSet.has(a)} style:left="{pct}%"></span>
			<span class="code" class:used={model.usedSet.has(a)} style:left="{pct}%">{a.toUpperCase()}</span>
		{/each}
		<span class="nm start" style:left="0">{platform.label} · polarization channels</span>
	{/if}
</div>

<style>
	.ruler { position: relative; height: 44px; margin: 0 4px; font-family: var(--mono); }
	.axis { position: absolute; top: 24px; height: 1px; background: var(--ink-mute); }
	.vis { position: absolute; top: 23px; height: 2px; background: linear-gradient(90deg, #5b2cff, #2b8bff 30%, #37d67a 55%, #f5d33a 75%, #ff5a1f 90%, #c0192c); }
	.break { position: absolute; top: 15px; transform: translateX(-50%); color: var(--ink-mute); font-size: 12px; line-height: 1; }
	.tick { position: absolute; top: 18px; width: 1px; height: 13px; background: var(--ink-mute); transform: translateX(-50%); }
	.tick.used { top: 14px; width: 3px; height: 21px; background: var(--accent); }
	.code { position: absolute; top: 0; transform: translateX(-50%); font-size: 9px; letter-spacing: 0.04em; color: var(--ink-mute); white-space: nowrap; }
	.code.used { color: var(--accent); font-weight: 700; }
	.nm { position: absolute; top: 33px; font-size: 9px; color: var(--ink-mute); white-space: nowrap; }
	.nm.middle { transform: translateX(-50%); }
	.nm.end { transform: translateX(-100%); }
</style>
