<script lang="ts">
	import type { TrueColorSpec } from '$lib/data/types';
	import { toneCurve, windowOf } from '$lib/raster/truecolor';

	/**
	 * Three stacked bars, one per band, each running dark → full channel through
	 * the tone curve over its stretch window; without a tone, plain ramps.
	 */
	let { tone = null, height = 14 }: { tone?: TrueColorSpec | null; height?: number } = $props();
	const N = 12;
	const bars = $derived(
		[0, 1, 2].map((c) => {
			const stops = Array.from({ length: N }, (_, i) => {
				const t = i / (N - 1);
				let v = t;
				if (tone) {
					const { lo, hi } = windowOf(tone.stretch);
					v = toneCurve(lo[c] + t * (hi[c] - lo[c]), tone, c);
				}
				const k = Math.round(v * 255);
				const rgb = [0, 0, 0];
				rgb[c] = k;
				return `rgb(${rgb.join(' ')})`;
			});
			return `linear-gradient(to right, ${stops.join(', ')})`;
		})
	);
</script>

<span class="tc" style:height="{height}px" aria-hidden="true">
	{#each bars as bg, i (i)}<span style:background={bg}></span>{/each}
</span>

<style>
	.tc { display: flex; flex-direction: column; width: 100%; box-shadow: inset 0 0 0 0.5px rgb(26 26 26 / 0.18); }
	.tc span { flex: 1; }
</style>
