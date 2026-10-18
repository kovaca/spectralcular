<script lang="ts">
	import { getProjectStore } from '$lib/state/project.svelte';
	import { buildManifest, validateForExport } from '$lib/export/manifest';
	import { candidatesYaml, locationsYaml } from '$lib/export/yamlSnippets';
	import { copyText, downloadText, safeFilename } from '$lib/export/download';
	import { parseProjectFile, serializeProject } from '$lib/state/persistence';

	const project = getProjectStore();
	let { open = $bindable(false) }: { open?: boolean } = $props();

	let tab = $state<'manifest' | 'candidates' | 'locations' | 'project'>('manifest');
	let copied = $state(false);
	let importError = $state<string | null>(null);
	let dialog = $state<HTMLDialogElement | null>(null);

	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) dialog.showModal();
		if (!open && dialog.open) dialog.close();
	});

	const manifest = $derived(open ? buildManifest(project.project) : null);
	const problems = $derived(open ? validateForExport(project.project) : []);
	const text = $derived.by(() => {
		if (!manifest) return '';
		switch (tab) {
			case 'manifest':
				return JSON.stringify(manifest, null, 2);
			case 'candidates':
				return candidatesYaml(manifest);
			case 'locations':
				return locationsYaml(manifest);
			case 'project':
				return serializeProject(project.project);
		}
	});
	const filename = $derived.by(() => {
		const base = safeFilename(project.project.name);
		return tab === 'manifest' ? `${base}.selection.json` : tab === 'candidates' ? `candidates.snippet.yaml` : tab === 'locations' ? 'locations.snippet.yaml' : `${base}.project.json`;
	});

	async function copy() {
		copied = await copyText(text);
		setTimeout(() => (copied = false), 1500);
	}
	function download() {
		downloadText(filename, text, tab === 'manifest' || tab === 'project' ? 'application/json' : 'text/yaml');
	}
	async function importProject(e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		importError = null;
		try {
			const p = parseProjectFile(await file.text());
			project.replace(p);
			open = false;
		} catch (err) {
			importError = err instanceof Error ? err.message : String(err);
		}
		input.value = '';
	}
</script>

<dialog bind:this={dialog} onclose={() => (open = false)} class="export">
	<header>
		<div>
			<h2>Export for the book build</h2>
			<p class="lede">The selection manifest is what <code>spectralcular import</code> reads. YAML snippets are there if you'd rather paste into <code>config/</code> by hand.</p>
		</div>
		<button class="btn ghost" onclick={() => (open = false)} aria-label="Close">✕</button>
	</header>

	{#if problems.length}
		<ul class="problems">
			{#each problems as p (p)}<li>{p}</li>{/each}
		</ul>
	{/if}

	<div class="tabs">
		<button class:on={tab === 'manifest'} onclick={() => (tab = 'manifest')}>selection manifest</button>
		<button class:on={tab === 'candidates'} onclick={() => (tab = 'candidates')}>candidates.yaml</button>
		<button class:on={tab === 'locations'} onclick={() => (tab = 'locations')}>locations.yaml</button>
		<button class:on={tab === 'project'} onclick={() => (tab = 'project')}>project backup</button>
	</div>
	<pre class="mono scroll">{text}</pre>
	<footer>
		<span class="mono filename">{filename}</span>
		<span class="spacer"></span>
		<label class="btn">Import project… <input type="file" accept="application/json,.json" onchange={importProject} hidden /></label>
		<button class="btn" onclick={copy}>{copied ? 'Copied' : 'Copy'}</button>
		<button class="btn primary" onclick={download}>Download</button>
	</footer>
	{#if importError}<p class="problems">Import failed: {importError}</p>{/if}
	{#if tab === 'manifest'}
		<p class="how mono">cd spectralcular && uv run spectralcular import ~/Downloads/{filename} && uv run spectralcular candidates lock --force && uv run spectralcular build && uv run spectralcular print</p>
	{/if}
</dialog>

<style>
	.export { width: min(900px, 92vw); max-height: 90vh; background: var(--paper); color: var(--ink); border: var(--hair) solid var(--ink); border-radius: 0; padding: 0; flex-direction: column; }
	.export[open] { display: flex; }
	.export::backdrop { background: rgb(26 26 26 / 0.45); }
	header { display: flex; justify-content: space-between; align-items: flex-start; padding: 16px 18px 10px; }
	h2 { margin: 0 0 4px; font-family: var(--serif); font-weight: 400; font-size: 24px; }
	.lede { margin: 0; color: var(--ink-soft); font-size: 12.5px; font-style: italic; }
	code { font-family: var(--mono); color: var(--ink); font-style: normal; }
	.problems { margin: 0 18px 8px; padding: 8px 12px 8px 26px; border: var(--hair) solid var(--bad); font-size: 12px; color: var(--bad); }
	.tabs { display: flex; gap: 2px; padding: 0 18px; border-bottom: var(--hair) solid var(--rule); }
	.tabs button { background: none; border: 0; border-bottom: 1.5px solid transparent; padding: 8px 10px; color: var(--ink-mute); font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; }
	.tabs button.on { color: var(--ink); border-bottom-color: var(--accent); }
	pre { margin: 0; padding: 12px 18px; font-size: 11px; line-height: 1.45; flex: 1; min-height: 240px; max-height: 50vh; background: var(--paper-2); color: var(--ink); }
	footer { display: flex; gap: 8px; align-items: center; padding: 10px 18px; border-top: var(--hair) solid var(--rule); }
	.spacer { flex: 1; }
	.filename { font-size: 11px; color: var(--ink-mute); }
	.how { margin: 0; padding: 8px 18px 14px; font-size: 11px; color: var(--ink-mute); }
	@media (max-width: 640px) {
		.export { max-height: 90dvh; }
		header { padding: 14px 14px 8px; }
		h2 { font-size: 20px; }
		.tabs { padding: 0 14px; overflow-x: auto; scrollbar-width: none; }
		.tabs::-webkit-scrollbar { display: none; }
		.tabs button { flex: 0 0 auto; white-space: nowrap; }
		pre { padding: 10px 14px; min-height: 160px; }
		footer { flex-wrap: wrap; padding: 10px 14px; }
		.filename { flex: 1 0 100%; overflow-wrap: anywhere; }
		.spacer { display: none; }
		.how { padding: 8px 14px 12px; overflow-wrap: anywhere; }
	}
</style>
