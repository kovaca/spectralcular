<script lang="ts">
	import { locations, indices, colormaps, platforms } from '$lib/data';

	let {
		open = $bindable(false),
		onExample,
		onBrowse,
		onShowGuide
	}: { open?: boolean; onExample: () => void; onBrowse: () => void; onShowGuide: () => void } = $props();

	let dialog = $state<HTMLDialogElement | null>(null);

	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) dialog.showModal();
		if (!open && dialog.open) dialog.close();
	});

	const nPlaces = locations.length;
	const nIndices = indices.length;
	const nMaps = colormaps.filter((c) => c.row !== null).length;
	const platformLabels = platforms.map((p) => p.label).join(', ');

	const steps = [
		{ n: '1', head: 'Place and frame', body: 'Pick a place in the rail, click a dot on the map, or search any place name or lat, lon. The rectangle is one printed page, starting at 300 dpi: drag it to move, pull a corner to resize, pull the stem above it to rotate. The readout under the map says what it prints at.' },
		{ n: '2', head: 'Scene', body: 'In I Scene set a date window and search. Results are ranked by frame coverage, then cloud. Picking one renders it into the frame straight from the cloud-optimized GeoTIFFs.' },
		{ n: '3', head: 'Index, reading, colormap', body: 'II Index chooses the formula and, with it, the satellite. III Read says what it measures, marks where water, forest, sand, snow and the rest fall on its scale, and suggests how the frame’s values should meet the ramp. IV Colormap then picks the ramp to match; r reverses it. Or pick True color at the top of IV for the scene as the eye sees it, toned in III with presets, stretch, gamma, contrast and saturation.' },
		{ n: '4', head: 'Save and export', body: 'Save plate adds the composition to the book. The ledger under Plates keeps the order; drag rows to change it. Export writes the selection manifest the print pipeline builds from.' }
	];

	const keys: [string, string][] = [
		['[ ]', 'previous / next place'],
		['a', 'align the map to the page'],
		['n', 'north up'],
		['f', 'zoom to the frame'],
		['r', 'reverse the colormap'],
		['⌘S', 'save the plate'],
		['⌘E', 'export'],
		['?', 'this page']
	];

	function close() {
		open = false;
	}
	function example() {
		close();
		onExample();
	}
	function browse() {
		close();
		onBrowse();
	}
	function guide() {
		close();
		onShowGuide();
	}
</script>

<dialog bind:this={dialog} onclose={() => (open = false)} class="intro" aria-labelledby="intro-title">
	<header>
		<div class="masthead">
			<span class="eyebrow">Spectralcular · plate picker</span>
			<h2 id="intro-title">Compose printed pages of satellite indices, one frame at a time.</h2>
			<p class="lede">
				Each plate in the book is one place, seen through one spectral index, colored by one colormap, and printed full bleed on a Letter page.
				(A plate can also be true color: the scene's own red, green and blue, toned for print.) This picker is where those choices meet: you frame the page on a live map, see the real scene rendered through the index and ramp,
				and keep an ordered list of plates that the print pipeline turns into spreads.
			</p>
		</div>
		<button class="btn ghost close" onclick={close} aria-label="Close">✕</button>
	</header>

	<ol class="steps">
		{#each steps as s (s.n)}
			<li>
				<span class="n mono">{s.n}</span>
				<div>
					<div class="head">{s.head}</div>
					<p>{s.body}</p>
				</div>
			</li>
		{/each}
	</ol>

	<div class="facts">
		<div class="col">
			<div class="eyebrow">What is here</div>
			<ul class="kv">
				<li><span class="k">places</span><span class="v">{nPlaces} in the location pool, grouped by theme</span></li>
				<li><span class="k">indices</span><span class="v">{nIndices} spectral indices with their formulas</span></li>
				<li><span class="k">colormaps</span><span class="v">{nMaps} ramps that render live on the map</span></li>
				<li><span class="k">imagery</span><span class="v">{platformLabels}</span></li>
			</ul>
		</div>
		<div class="col">
			<div class="eyebrow">Good to know</div>
			<ul class="plain">
				<li>Imagery streams from public catalogs (Earth Search, Planetary Computer). Nothing is uploaded; there is no account.</li>
				<li>Your book lives in this browser. Export a project backup before clearing site data or moving machines.</li>
				<li>The first render of a scene can take a moment, longer for Landsat and radar; later views of the same scene are quick.</li>
			</ul>
		</div>
		<div class="col">
			<div class="eyebrow">Keys</div>
			<ul class="kv keys">
				{#each keys as [k, v] (k)}
					<li><span class="k key">{k}</span><span class="v">{v}</span></li>
				{/each}
			</ul>
		</div>
	</div>

	<footer>
		<button class="link" onclick={guide}>Show the step-by-step guide on the map</button>
		<span class="spacer"></span>
		<button class="btn" onclick={browse}>Browse places</button>
		<button class="btn primary" onclick={example} title="Frame Mt. Etna, search its summer window and pin the clearest scene">Open a worked example</button>
	</footer>
</dialog>

<style>
	.intro { width: min(860px, 94vw); max-height: 92vh; background: var(--paper); color: var(--ink); border: var(--hair) solid var(--ink); border-radius: 0; padding: 0; flex-direction: column; overflow: auto; }
	.intro[open] { display: flex; }
	.intro::backdrop { background: rgb(26 26 26 / 0.45); }
	header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; padding: 20px 22px 14px; border-bottom: var(--hair) solid var(--rule); }
	.masthead { display: flex; flex-direction: column; gap: 6px; max-width: 640px; }
	h2 { margin: 0; font-family: var(--serif); font-weight: 400; font-size: 26px; line-height: 1.2; letter-spacing: -0.005em; }
	.lede { margin: 4px 0 0; color: var(--ink-soft); font-size: 13.5px; line-height: 1.5; }
	.close { flex: 0 0 auto; }

	.steps { list-style: none; margin: 0; padding: 14px 22px 6px; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0 18px; }
	.steps li { display: flex; gap: 10px; align-items: flex-start; padding-top: 6px; border-top: var(--hair) solid var(--ink); }
	.steps .n { color: var(--accent); font-weight: 700; font-size: 11px; letter-spacing: 0.14em; padding-top: 3px; }
	.steps .head { font-family: var(--serif); font-size: 15px; margin-bottom: 3px; }
	.steps p { margin: 0; font-size: 12.5px; line-height: 1.45; color: var(--ink-soft); }

	.facts { display: grid; grid-template-columns: 1.1fr 1.2fr 0.9fr; gap: 0 18px; padding: 14px 22px 16px; }
	.col .eyebrow { border-bottom: var(--hair) solid var(--rule); padding-bottom: 3px; margin-bottom: 6px; }
	.plain { margin: 0; padding: 0 0 0 14px; font-size: 12.5px; line-height: 1.45; color: var(--ink-soft); }
	.plain li + li { margin-top: 4px; }
	.kv .k { flex-basis: 74px; }
	.keys .key { text-transform: none; letter-spacing: 0.04em; color: var(--ink); font-size: 10.5px; }

	footer { display: flex; gap: 8px; align-items: center; padding: 12px 22px 16px; border-top: var(--hair) solid var(--rule); flex-wrap: wrap; }
	.spacer { flex: 1; }
	.link { background: none; border: 0; padding: 0; font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.06em; color: var(--ink-soft); text-decoration: underline; text-underline-offset: 3px; }
	.link:hover { color: var(--accent); }

	@media (max-width: 1023px) {
		header { padding: 16px 16px 12px; }
		h2 { font-size: 21px; }
		.steps { grid-template-columns: 1fr 1fr; gap: 10px 14px; padding: 12px 16px 4px; }
		.facts { grid-template-columns: 1fr; gap: 12px; padding: 12px 16px 14px; }
		footer { padding: 12px 16px 14px; }
	}
	@media (max-width: 600px) {
		.steps { grid-template-columns: 1fr; }
		footer .link { order: 3; flex-basis: 100%; }
	}
</style>
