<script lang="ts">
	import { getSession } from '$lib/state/session.svelte';
	import { PAGE } from '$lib/book/page';

	const session = getSession();
	/** Narrow screens: ground, dpi and rotation only; the rest hides behind a tap. */
	let { compact = false }: { compact?: boolean } = $props();
	let expanded = $state(false);
	const p = $derived(session.print);
	const km = (m: number) => (m / 1000).toFixed(m < 10_000 ? 2 : 1);
	const statusLabel = $derived(p.status === 'full' ? 'prints at full resolution' : p.status === 'upsampled' ? 'upsampled to 300 dpi' : 'soft: frame is too wide for this band');
</script>

<div class="readout mono" class:compact class:expanded role="status" aria-live="polite">
	<div class="cell"><span class="k">Ground</span><span class="v">{km(p.widthM)} × {km(p.heightM)} km</span></div>
	<div class="cell more"><span class="k">Native px @ {p.gsdM} m</span><span class="v">{Math.round(p.nativePxW)} × {Math.round(p.nativePxH)}</span></div>
	<div class="cell more"><span class="k">Plate needs</span><span class="v">{p.targetPxW} × {p.targetPxH} @ {PAGE.dpi}</span></div>
	<div class="cell dpi" class:ok={p.status === 'full'} class:warn={p.status === 'upsampled'} class:bad={p.status === 'soft'}>
		<span class="k">Effective</span><span class="v big">{Math.round(p.effectiveDpi)} dpi</span><span class="sub">{statusLabel}</span>
	</div>
	<div class="cell more"><span class="k">Ceiling @ {p.gsdM} m</span><span class="v" class:bad={!p.withinCeiling}>{km(p.ceilingWidthM)} × {km(p.ceilingHeightM)} km {p.withinCeiling ? '✓' : '✕'}</span></div>
	<div class="cell"><span class="k">Rotation</span><span class="v">{session.draft.frame.rotationDeg.toFixed(1)}°</span></div>
	{#if compact}<button class="toggle" onclick={() => (expanded = !expanded)} aria-expanded={expanded}>{expanded ? 'less' : 'more'}</button>{/if}
	<div class="bar" style:width="{Math.min(100, p.resolutionRatio * 100)}%" class:ok={p.status === 'full'} class:warn={p.status === 'upsampled'} class:bad={p.status === 'soft'}></div>
</div>

<style>
	.readout { position: relative; display: flex; gap: 22px; padding: 8px 14px 10px; background: color-mix(in oklab, var(--paper) 94%, transparent); border-top: var(--hair) solid var(--ink); backdrop-filter: blur(6px); overflow-x: auto; }
	.cell { display: flex; flex-direction: column; gap: 2px; white-space: nowrap; }
	.k { font-size: 9.5px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--ink-mute); }
	.v { font-size: 11.5px; color: var(--ink); }
	.big { font-size: 15px; font-weight: 500; }
	.sub { font-size: 10px; color: var(--ink-mute); }
	.dpi.ok .big { color: var(--ok); } .dpi.warn .big { color: var(--warn); } .dpi.bad .big { color: var(--bad); }
	.bad { color: var(--bad); }
	.bar { position: absolute; left: 0; top: 0; height: 2px; background: var(--ink-mute); transition: width 200ms ease; }
	.bar.ok { background: var(--ok); } .bar.warn { background: var(--warn); } .bar.bad { background: var(--bad); }
	.compact { gap: 14px; padding: 6px 12px 8px; flex-wrap: wrap; }
	.toggle { margin-left: auto; align-self: end; background: none; border: 0; padding: 0 2px; font-family: var(--mono); font-size: 9.5px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--ink-mute); text-decoration: underline; text-underline-offset: 2px; }
	.compact .more { display: none; }
	.compact.expanded .more { display: flex; }
	.compact .sub { display: none; }
	.compact .big { font-size: 13px; }
</style>
