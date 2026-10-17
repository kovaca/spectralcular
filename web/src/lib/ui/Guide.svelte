<script lang="ts">
	import { getSession } from '$lib/state/session.svelte';
	import { getProjectStore } from '$lib/state/project.svelte';
	import { composition } from '$lib/data/reading';

	/**
	 * A quiet coach on the map stage for the first plate. It reads the session and
	 * says what comes next; every step that has an action offers it inline so a new
	 * user never has to hunt for the right panel. Dismissed once and remembered.
	 */
	let {
		onDismiss,
		onExample,
		onOpenInspector,
		onOpenPlaces,
		narrow = false
	}: { onDismiss: () => void; onExample: () => void; onOpenInspector: () => void; onOpenPlaces: () => void; narrow?: boolean } = $props();

	const session = getSession();
	const project = getProjectStore();

	type StepId = 'place' | 'scene' | 'read' | 'compose' | 'done';
	const STEPS: { id: StepId; label: string }[] = [
		{ id: 'place', label: 'Place' },
		{ id: 'scene', label: 'Scene' },
		{ id: 'read', label: 'Read' },
		{ id: 'compose', label: 'Compose & save' }
	];

	const untouched = $derived(
		session.draft.location.kind === 'adhoc' && session.draft.location.locationId === 'new_location' && !session.dirty && !session.draft.scene && session.search.status === 'idle'
	);
	/** The plate on the stage is saved (just now, or loaded from the ledger). */
	const saved = $derived(session.editingPlateId !== null && !session.dirty && project.plates.some((p) => p.id === session.editingPlateId));
	const ordinal = $derived(String(project.plates.find((p) => p.id === session.editingPlateId)?.ordinal ?? 0).padStart(3, '0'));
	const current = $derived.by((): StepId => {
		if (saved) return 'done';
		if (untouched) return 'place';
		if (!session.draft.scene) return 'scene';
		if (!session.readDone) return 'read';
		return 'compose';
	});
	const suggested = $derived(session.reading?.options.find((o) => o.recommended) ?? null);
	/** "62 % dense canopy, 21 % water · snow": the two largest guide classes in the frame */
	const mix = $derived.by(() => {
		const g = session.indexDef?.guide;
		const shares = g ? composition(g.classes, session.draft.stats?.histogram ?? null) : null;
		if (!shares) return '';
		return [...shares]
			.sort((a, b) => b.fraction - a.fraction)
			.filter((x) => x.fraction >= 0.05)
			.slice(0, 2)
			.map((x) => `${Math.round(x.fraction * 100)}% ${x.cls.label}`)
			.join(', ');
	});
	const index = $derived(STEPS.findIndex((s) => s.id === current));

	const message = $derived.by((): string => {
		switch (current) {
			case 'place':
				return 'Choose a place from the rail or click a dot on the map. The rectangle is one printed page; to frame anywhere else, search a place name or lat, lon, or use Frame here.';
			case 'scene':
				switch (session.search.status) {
					case 'loading':
						return `Searching ${session.platform.label} for ${session.draft.location.name}…`;
					case 'error':
						return 'The scene search failed. Check the connection and try again from I Scene.';
					case 'done':
						return session.search.items.length
							? `Pick a scene from the results in I Scene. Full cover and low cloud first; the frame renders ${session.isTrueColor ? 'in true color' : session.draft.index} as soon as one is pinned.`
							: 'No scenes matched this window. In I Scene widen the dates or allow more cloud, then search again.';
					default:
						return `Now find imagery: search ${session.platform.label} scenes for the season window that came with this place.`;
				}
			case 'read':
				if (session.isTrueColor) {
					if (session.statsStatus === 'loading') return 'True color is on the frame. Sampling its red, green and blue across the frame for III Tone…';
					if (session.statsStatus === 'error') return `True color is on the frame, but sampling failed (${session.statsError}). The presets in III Tone still work.`;
					return 'III Tone sets how reflectance prints: pick a preset, then adjust the stretch, gamma, contrast and saturation until the page reads well.';
				}
				if (session.statsStatus === 'loading') return `${session.draft.index} is on the frame. Sampling its values across the frame for III Read…`;
				if (session.statsStatus === 'error') return `${session.draft.index} is on the frame, but sampling failed (${session.statsError}). III Read still shows how to read ${session.draft.index}.`;
				return `III Read shows what ${session.draft.index} measures and where this frame falls${mix ? `: ${mix}` : ''}. Choose how values meet the ramp${suggested ? ` (suggested: ${suggested.title.toLowerCase()})` : ''}, then a colormap in IV to match.`;
			case 'compose':
				return session.isTrueColor
					? 'Drag the corners to resize, the stem to rotate; adjust the tone (III) and watch it re-render, or pick a ramp in IV to make it an index plate. When it reads well, save it.'
					: `Drag the corners to resize, the stem to rotate; change the index (II), stretch (III) or colormap (IV) and watch it re-render. When it reads well, save it.`;
			case 'done':
				return `Plate ${ordinal} is saved and listed under Plates. Pick the next place, or export the selection when the set is ready.`;
		}
	});

	function openRead() {
		if (narrow) onOpenInspector();
		requestAnimationFrame(() => document.getElementById('section-read')?.scrollIntoView({ block: 'start', behavior: 'smooth' }));
	}

	function search() {
		void session.runSearch();
		if (narrow) onOpenInspector();
	}
</script>

<div class="guide" role="status" aria-live="polite">
	<div class="track mono" aria-hidden="true">
		{#each STEPS as s, i (s.id)}
			<span class="step" class:on={i === index} class:past={i < index || current === 'done'}>{i + 1} {s.label}</span>
		{/each}
	</div>
	<p class="msg">{message}</p>
	<div class="acts">
		{#if current === 'place'}
			<button class="btn primary sm" onclick={onExample}>Open a worked example</button>
			{#if narrow}<button class="btn sm" onclick={onOpenPlaces}>Places</button>{/if}
		{:else if current === 'scene' && session.search.status !== 'loading'}
			{#if session.search.status === 'done' && session.search.items.length}
				<button class="btn primary sm" onclick={() => session.pinBestScene()}>Pin the best match</button>
				{#if narrow}<button class="btn sm" onclick={onOpenInspector}>See results</button>{/if}
			{:else}
				<button class="btn primary sm" onclick={search}>Search {session.platform.label}</button>
			{/if}
		{:else if current === 'read'}
			{#if suggested}<button class="btn primary sm" onclick={() => session.applyStretch(suggested)}>Use the suggested stretch</button>{/if}
			<button class="btn sm" onclick={openRead}>{narrow ? `Open III ${session.isTrueColor ? 'Tone' : 'Read'}` : `Show III ${session.isTrueColor ? 'Tone' : 'Read'}`}</button>
		{:else if current === 'compose'}
			{#if narrow}<button class="btn sm" onclick={onOpenInspector}>Open the inspector</button>{/if}
			<span class="eyebrow">Save plate above{narrow ? '' : ', or ⌘S'}</span>
		{/if}
		<span class="sp"></span>
		<button class="dismiss mono" onclick={onDismiss} title="Hide the guide">{current === 'done' ? 'close' : 'hide guide'}</button>
	</div>
</div>

<style>
	.guide { pointer-events: auto; width: min(360px, 100%); background: color-mix(in oklab, var(--paper) 96%, transparent); border: var(--hair) solid var(--ink); border-left: 2px solid var(--accent); padding: 8px 12px 8px; backdrop-filter: blur(6px); display: flex; flex-direction: column; gap: 6px; }
	.track { display: flex; gap: 10px; font-size: 9.5px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--ink-mute); }
	.step.on { color: var(--accent); }
	.step.past { color: var(--ink-soft); text-decoration: line-through; text-decoration-thickness: 0.5px; }
	.msg { margin: 0; font-size: 12.5px; line-height: 1.45; color: var(--ink); }
	.acts { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
	.sp { flex: 1; }
	.dismiss { background: none; border: 0; padding: 0; font-size: 9.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-mute); text-decoration: underline; text-underline-offset: 2px; }
	.dismiss:hover { color: var(--ink); }
</style>
