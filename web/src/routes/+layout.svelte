<script lang="ts">
	import 'maplibre-gl/dist/maplibre-gl.css';
	// Registers MapLibre's worker URL so Vite serves it correctly in dev and build.
	import '$lib/ui/maplibreWorker';
	import '../app.css';
	let { children } = $props();
</script>

{@render children()}
