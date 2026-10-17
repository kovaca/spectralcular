<script lang="ts">
	import { getProjectStore } from '$lib/state/project.svelte';
	import { getSession } from '$lib/state/session.svelte';
	import { getColormap, THEME_LABEL, THEME_ORDER, getIndex, getTrueColorPreset, platformForCollection, platformForIndex } from '$lib/data';
	import { plateTheme } from '$lib/export/manifest';
	import GradientSwatch from './GradientSwatch.svelte';
	import TrueColorSwatch from './TrueColorSwatch.svelte';
	import type { Plate } from '$lib/state/schema';

	const project = getProjectStore();
	const session = getSession();
	let { onExport, onNew }: { onExport: () => void; onNew: () => void } = $props();

	let dragFrom = $state<number | null>(null);
	let dragOver = $state<number | null>(null);

	function onDragStart(i: number, e: DragEvent) {
		dragFrom = i;
		e.dataTransfer?.setData('text/plain', String(i));
		if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
	}
	function onDrop(i: number) {
		if (dragFrom !== null && dragFrom !== i) project.movePlate(dragFrom, i);
		dragFrom = null;
		dragOver = null;
	}
	function open(p: Plate) {
		session.loadPlate(p);
		project.activePlateId = p.id;
	}
	function remove(p: Plate) {
		project.removePlate(p.id);
		if (session.editingPlateId === p.id) session.editingPlateId = null;
	}
	const themeOf = (p: Plate) => (p.kind === 'truecolor' || getIndex(p.index) ? plateTheme(p) : 'other');
	/** Platform label when it isn't the default Sentinel-2. */
	function platformNote(p: Plate): string {
		const def = getIndex(p.index);
		const platform = p.kind === 'truecolor' ? (p.scene ? platformForCollection(p.scene.collection) : undefined) : def ? platformForIndex(def) : undefined;
		return platform && platform.id !== 'sentinel-2-l2a' ? ` · ${platform.label}` : '';
	}
	/** Book order preserved inside each theme group; groups follow THEME_ORDER. */
	const groups = $derived.by(() => {
		const order: string[] = [...THEME_ORDER, 'other'];
		const rows = project.plates.map((p, i) => ({ p, i }));
		return order
			.map((t) => ({ theme: t, rows: rows.filter(({ p }) => themeOf(p) === t) }))
			.filter((g) => g.rows.length);
	});
	const ord = (p: Plate) => String(p.ordinal).padStart(3, '0');
</script>

<div class="plates">
	<div class="head">
		<span class="book">{project.project.name}</span>
		<span class="eyebrow">{project.plates.length} plates · {project.plates.length * 2} pages</span>
		<span class="sp"></span>
		{#if session.dirty}<span class="eyebrow unsaved">unsaved</span>{/if}
	</div>
	<div class="ledger scroll">
		{#each groups as g (g.theme)}
			<div class="theme"><span>{THEME_LABEL[g.theme as keyof typeof THEME_LABEL] ?? g.theme}</span><span>{g.rows.length}</span></div>
			<ol class="list">
				{#each g.rows as { p, i } (p.id)}
					{@const cm = getColormap(p.colormap.name)}
					{@const tc = p.kind === 'truecolor'}
					<li
						class="plate"
						class:active={p.id === session.editingPlateId}
						class:over={dragOver === i}
						draggable="true"
						ondragstart={(e) => onDragStart(i, e)}
						ondragover={(e) => { e.preventDefault(); dragOver = i; }}
						ondragleave={() => (dragOver = null)}
						ondrop={() => onDrop(i)}
						ondragend={() => { dragFrom = null; dragOver = null; }}
					>
						<button class="main" onclick={() => open(p)}>
							{#if p.scene?.thumbnail}
								<img class="thumb" src={p.scene.thumbnail} alt="" loading="lazy" />
							{:else}
								<span class="thumb empty" style:background={cm ? `linear-gradient(160deg, ${cm.previewStops.join(',')})` : undefined}></span>
							{/if}
							<span class="body">
								<span class="co mono">{p.location.locationId}{p.scene ? ` · ${p.scene.datetime.slice(0, 10)}` : ' · no scene'}{p.frame.rotationDeg ? ` · ${p.frame.rotationDeg.toFixed(0)}°` : ''}{platformNote(p)}</span>
								<span class="name">{p.location.name}</span>
								{#if tc}
									<span class="ic mono">True color<span class="sw"><TrueColorSwatch tone={p.truecolor} height={7} /></span><span class="cmn">{getTrueColorPreset(p.truecolor?.preset ?? '')?.label ?? 'custom tone'}</span></span>
								{:else}
								<span class="ic mono">{p.index}{#if cm}<span class="sw"><GradientSwatch stops={cm.previewStops} reversed={p.colormap.reversed} height={7} /></span>{/if}<span class="cmn" class:rev={p.colormap.reversed}>{cm?.shortName ?? p.colormap.name}{p.colormap.reversed ? ' ⇄' : ''}</span></span>
								{/if}
							</span>
							<span class="ord mono">{ord(p)}</span>
						</button>
						<span class="actions">
							<button class="btn ghost sm" title="Move up" disabled={i === 0} onclick={() => project.movePlate(i, i - 1)}>↑</button>
							<button class="btn ghost sm" title="Move down" disabled={i === project.plates.length - 1} onclick={() => project.movePlate(i, i + 1)}>↓</button>
							<button class="btn ghost sm" title="Duplicate" onclick={() => project.duplicatePlate(p.id)}>⧉</button>
							<button class="btn ghost sm danger" title="Remove" onclick={() => remove(p)}>✕</button>
						</span>
					</li>
				{/each}
			</ol>
		{:else}
			<p class="empty">No plates yet. Frame a page on the map, pick a scene, then an index and colormap (or true color), and save it. Drag plates here to set the book order.</p>
		{/each}
		<button class="add" onclick={onNew} title="Start a new plate from the current frame and settings">
			<span class="thumb dashed">+</span>
			<span class="add-label">New plate from {session.draft.location.name}</span>
		</button>
	</div>
	<div class="foot">
		<button class="btn primary sm" onclick={onExport} disabled={project.plates.length === 0}>Export for build</button>
	</div>
</div>

<style>
	.plates { display: flex; flex-direction: column; height: 100%; min-height: 0; }
	.head { display: flex; align-items: baseline; gap: 10px; padding: 12px 14px 6px; }
	.book { font-family: var(--serif); font-size: 17px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.sp { flex: 1; }
	.unsaved { color: var(--accent); }
	.ledger { flex: 1; min-height: 0; }
	.theme { display: flex; justify-content: space-between; padding: 10px 14px 4px; font-family: var(--mono); font-size: 9.5px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-mute); }
	.list { list-style: none; margin: 0; padding: 0; }
	.plate { display: flex; align-items: stretch; border-top: 0.3px dotted var(--rule); }
	.plate.over { border-top: 1px solid var(--accent); }
	.plate.active { background: var(--paper-2); box-shadow: inset 2px 0 var(--accent); }
	.plate:hover .actions { opacity: 1; }
	.main { display: grid; grid-template-columns: 44px 1fr auto; gap: 0 12px; align-items: center; flex: 1; min-width: 0; text-align: left; padding: 8px 8px 8px 14px; background: none; border: 0; cursor: grab; }
	.thumb { width: 44px; height: 56px; object-fit: cover; border: var(--hair) solid var(--rule); display: block; background: var(--paper-edge); }
	.thumb.dashed { border-style: dashed; display: grid; place-items: center; font-family: var(--mono); color: var(--rule); font-size: 14px; }
	.body { display: flex; flex-direction: column; min-width: 0; }
	.co { font-size: 9px; letter-spacing: 0.1em; color: var(--ink-mute); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.name { font-family: var(--serif); font-size: 13.5px; line-height: 1.2; margin: 2px 0 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.ic { display: flex; align-items: center; gap: 6px; font-size: 10px; color: var(--ink-soft); min-width: 0; }
	.sw { display: block; width: 34px; flex: 0 0 34px; }
	.cmn { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.cmn.rev { color: var(--accent); }
	.ord { font-size: 10px; color: var(--ink-mute); align-self: start; }
	.actions { display: flex; flex-direction: column; justify-content: center; opacity: 0; padding-right: 4px; }
	.actions .btn { padding: 0 5px; line-height: 1.3; border-color: transparent; }
	.danger:hover { color: var(--bad); }
	.add { display: grid; grid-template-columns: 44px 1fr; gap: 12px; align-items: center; width: 100%; text-align: left; padding: 8px 14px; background: none; border: 0; border-top: 0.3px dotted var(--rule); color: var(--ink-mute); font-family: var(--serif); font-style: italic; font-size: 13px; }
	.add:hover { color: var(--ink); background: var(--paper-2); }
	.foot { padding: 10px 14px; border-top: var(--hair) solid var(--rule); display: flex; justify-content: flex-end; }
	/* No hover on touch: the active row keeps its actions visible. */
	@media (hover: none) { .plate.active .actions { opacity: 1; } }
	@media (min-width: 1024px) and (max-width: 1279px) { .co { display: none; } }
</style>
