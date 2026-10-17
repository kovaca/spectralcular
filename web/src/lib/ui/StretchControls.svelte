<script lang="ts">
	import { getSession } from '$lib/state/session.svelte';
	import type { NormalizationSpec } from '$lib/state/schema';

	const session = getSession();
	const norm = $derived(session.draft.normalization);
	const range = $derived(session.range);
	const stats = $derived(session.draft.stats);
	const domain = $derived(session.indexDef?.domain ?? null);

	function set(method: NormalizationSpec['method']) {
		switch (method) {
			case 'percentile':
				session.setNormalization({ method, lower: 1, upper: 99 });
				break;
			case 'fixed':
				session.setNormalization({ method, vmin: range?.vmin ?? domain?.[0] ?? -1, vmax: range?.vmax ?? domain?.[1] ?? 1 });
				break;
			case 'diverging':
			case 'domain':
				session.setNormalization({ method });
		}
	}
	function setPct(lower: number, upper: number) {
		session.setNormalization({ method: 'percentile', lower, upper });
	}
	function setFixed(k: 'vmin' | 'vmax', v: number) {
		if (norm.method !== 'fixed' || !Number.isFinite(v)) return;
		session.setNormalization({ ...norm, [k]: v });
	}
</script>

<!-- Fine-tuning for the III Read stretch: method, percentiles or fixed ends, mask. -->
<div class="stretch">
	{#if range}
		<div class="ends mono"><span>ramp {range.vmin.toFixed(3)}</span><span>{range.description}</span><span>{range.vmax.toFixed(3)}</span></div>
	{/if}

	<div class="row">
		<div class="seg">
			<button class:on={norm.method === 'percentile'} onclick={() => set('percentile')}>percentile</button>
			<button class:on={norm.method === 'fixed'} onclick={() => set('fixed')}>fixed</button>
			<button class:on={norm.method === 'diverging'} onclick={() => set('diverging')}>diverging</button>
			<button class:on={norm.method === 'domain'} onclick={() => set('domain')} disabled={!domain} title={domain ? `${domain[0]} … ${domain[1]}` : 'No canonical domain for this index'}>domain</button>
		</div>
		<label class="mask eyebrow"><input type="checkbox" checked={session.draft.maskEnabled} onchange={(e) => { session.setMaskEnabled(e.currentTarget.checked); session.refreshStats(); }} /> mask</label>
	</div>

	{#if norm.method === 'percentile'}
		<div class="pcts">
			{#each [[1, 99], [2, 98], [0, 100]] as [lo, hi] (lo)}
				<button class="btn sm" class:primary={norm.lower === lo && norm.upper === hi} onclick={() => setPct(lo, hi)}>{lo}–{hi}</button>
			{/each}
		</div>
	{:else if norm.method === 'fixed'}
		<div class="fixed">
			<label class="field"><span>vmin</span><input class="input" type="number" step="0.01" value={norm.vmin} onchange={(e) => setFixed('vmin', e.currentTarget.valueAsNumber)} /></label>
			<label class="field"><span>vmax</span><input class="input" type="number" step="0.01" value={norm.vmax} onchange={(e) => setFixed('vmax', e.currentTarget.valueAsNumber)} /></label>
		</div>
	{/if}

	{#if stats}
		<div class="readout mono" title="valid / nodata / masked">valid {(stats.validFraction * 100).toFixed(0)}% · masked {(stats.maskedFraction * 100).toFixed(0)}% · n={stats.nPixels}</div>
	{/if}
</div>

<style>
	.ends { display: flex; justify-content: space-between; gap: 8px; font-size: 10.5px; color: var(--ink-mute); }
	.row { display: flex; justify-content: space-between; align-items: center; gap: 8px 12px; margin-top: 6px; flex-wrap: wrap; }
	.mask { display: flex; gap: 4px; align-items: center; white-space: nowrap; }
	.pcts { display: flex; gap: 4px; margin-top: 8px; }
	.fixed { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
	.readout { margin-top: 8px; font-size: 10.5px; color: var(--ink-mute); }
</style>
