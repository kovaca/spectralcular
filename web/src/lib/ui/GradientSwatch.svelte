<script lang="ts">
	let { stops, reversed = false, height = 14 }: { stops: string[]; reversed?: boolean; height?: number } = $props();
	const css = $derived(`linear-gradient(to ${reversed ? 'left' : 'right'}, ${stops.join(', ')})`);
</script>

<span class="swatch" style:background={css} style:height="{height}px" aria-hidden="true"></span>

<style>
	.swatch { display: block; width: 100%; box-shadow: inset 0 0 0 0.5px rgb(26 26 26 / 0.18); }
</style>
