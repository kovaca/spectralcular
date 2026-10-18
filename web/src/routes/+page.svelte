<script lang="ts">
	import { onMount } from 'svelte';
	import { MediaQuery } from 'svelte/reactivity';
	import { LngLatBounds } from 'maplibre-gl';
	import MapView from '$lib/ui/MapView.svelte';
	import LocationBrowser from '$lib/ui/LocationBrowser.svelte';
	import PlateList from '$lib/ui/PlateList.svelte';
	import ScenePicker from '$lib/ui/ScenePicker.svelte';
	import IndexPicker from '$lib/ui/IndexPicker.svelte';
	import ColormapPicker from '$lib/ui/ColormapPicker.svelte';
	import ReadPanel from '$lib/ui/ReadPanel.svelte';
	import PrintReadout from '$lib/ui/PrintReadout.svelte';
	import FrameToolbar from '$lib/ui/FrameToolbar.svelte';
	import ExportDialog from '$lib/ui/ExportDialog.svelte';
	import Intro from '$lib/ui/Intro.svelte';
	import Guide from '$lib/ui/Guide.svelte';
	import BookOverview from '$lib/ui/BookOverview.svelte';
	import InspectorSection from '$lib/ui/InspectorSection.svelte';
	import GradientSwatch from '$lib/ui/GradientSwatch.svelte';
	import TrueColorSwatch from '$lib/ui/TrueColorSwatch.svelte';
	import { SectionStack } from '$lib/ui/sectionStack.svelte';
	import { sectionSummaries, type SectionId } from '$lib/ui/sectionStatus';
	import { fits } from '$lib/data/fit';
	import { planSlots } from '$lib/raster/formula/resolve';
	import { toneDiffersFromPreset } from '$lib/raster/truecolor';
	import type { Plate } from '$lib/state/schema';
	import { getFlag, setFlag, PREF_GUIDE_DONE, PREF_INTRO_SEEN } from '$lib/state/prefs';
	import { getSession } from '$lib/state/session.svelte';
	import { getProjectStore } from '$lib/state/project.svelte';
	import { locations, getLocation, getTrueColorPreset, gsdForIndex, platformForIndex, type LocationDef } from '$lib/data';
	import type { LonLat } from '$lib/geo/tangent';

	const session = getSession();
	const project = getProjectStore();

	/** Below this the rail and inspector become sheets over the stage, driven by the bottom bar. */
	const narrow = new MediaQuery('(max-width: 1023px)');

	type RailTab = 'places' | 'plates' | 'overview';
	let rail = $state<RailTab>('places');
	/** The bottom bar's Plates button reopens whichever book view was used last. */
	let lastBookTab = $state<'plates' | 'overview'>('plates');
	/** plates the Overview is focused on, echoed on the stage map */
	let overviewFocus = $state<string[]>([]);
	/** Which sheet is open on narrow screens; ignored on wide ones. */
	let sheet = $state<'none' | 'rail' | 'inspector'>('none');
	let exportOpen = $state(false);
	let introOpen = $state(false);
	/** The stage guide is shown until dismissed once; the intro reopens it. */
	let guideDone = $state(true);
	let introEverOpened = false;
	let browser = $state<LocationBrowser | null>(null);
	let toast = $state<string | null>(null);

	const currentLocationId = $derived(session.draft.location.kind === 'pool' ? session.draft.location.locationId : null);
	const editing = $derived(session.editingPlateId ? project.plates.find((p) => p.id === session.editingPlateId) ?? null : null);
	const plateLabel = $derived(editing ? `Plate ${String(editing.ordinal).padStart(3, '0')}` : 'New plate');
	const railOpen = $derived(!narrow.current || sheet === 'rail');
	const inspectorOpen = $derived(!narrow.current || sheet === 'inspector');

	function setRail(tab: RailTab) {
		rail = tab;
		if (tab !== 'places') lastBookTab = tab;
	}
	function showRail(tab: RailTab) {
		setRail(tab);
		sheet = 'rail';
	}
	function openPlate(p: Plate) {
		session.loadPlate(p);
		project.activePlateId = p.id;
	}
	/** Fit the stage map to some plates; on narrow screens close the sheet so they're seen. */
	function showPlatesOnMap(plates: Plate[]) {
		const m = session.map;
		if (!m || !plates.length) return;
		if (plates.length === 1) m.flyTo({ center: plates[0].frame.center, zoom: 9 });
		else {
			const b = new LngLatBounds();
			for (const p of plates) b.extend(p.frame.center);
			m.fitBounds(b, { padding: 80, maxZoom: 9 });
		}
		if (narrow.current) sheet = 'none';
	}
	function toggleSheet(which: 'rail' | 'inspector') {
		sheet = sheet === which ? 'none' : which;
	}

	function pickLocation(loc: LocationDef) {
		session.startFromLocation(loc);
		rail = 'places';
		if (narrow.current) sheet = 'none';
	}
	function pickPoint(name: string, center: LonLat) {
		session.startAtPoint(name, center, { fly: true });
		rail = 'places';
		if (narrow.current) sheet = 'none';
	}

	/** The worked example: Mt. Etna through NDVI and eclipse, the book's proof plate. */
	const EXAMPLE = { locationId: 'mt_etna', index: 'NDVI', colormap: 'cmasher:eclipse' };

	async function runExample() {
		const loc = getLocation(EXAMPLE.locationId) ?? locations[0];
		if (!loc) return;
		rail = 'places';
		if (narrow.current) sheet = 'none';
		say(`Framing ${loc.name} and searching ${session.platform.label}…`);
		const r = await session.startExample(loc, EXAMPLE.index, EXAMPLE.colormap);
		if (r === 'pinned') say(`${loc.name}: clearest scene pinned. Rendering ${EXAMPLE.index}…`);
		else if (r === 'no-scene') say('No scene matched the example window. Widen the dates in I Scene.');
		else say('Scene search failed. Check the connection and try again from I Scene.');
	}

	function dismissGuide() {
		guideDone = true;
		setFlag(PREF_GUIDE_DONE, true);
	}
	function showGuide() {
		guideDone = false;
		setFlag(PREF_GUIDE_DONE, false);
	}
	function browsePlaces() {
		if (narrow.current) showRail('places');
		else rail = 'places';
	}

	function say(msg: string) {
		toast = msg;
		setTimeout(() => (toast = null), 1800);
	}

	function savePlate(asNew = false) {
		if (!session.draft.scene) {
			say('Pick a scene before saving the plate');
			if (narrow.current) sheet = 'inspector';
			return;
		}
		if (editing && !asNew) {
			project.updatePlate(editing.id, session.toPlate(editing));
			say(`Plate ${String(editing.ordinal).padStart(3, '0')} updated`);
		} else {
			const plate = session.toPlate();
			project.addPlate(plate);
			session.editingPlateId = plate.id;
			project.activePlateId = plate.id;
			say(`Saved as plate ${String(project.plates.length).padStart(3, '0')}`);
		}
		// The store may have re-derived an ad-hoc location id (see state/adhocIds.ts).
		const saved = project.plates.find((p) => p.id === session.editingPlateId);
		if (saved) session.draft.location = { ...saved.location };
		session.dirty = false;
		// Keep the Overview up if that's where the user is; otherwise show the ledger.
		if (rail !== 'overview') setRail('plates');
	}

	/** Detach the editor from the current plate so the next save creates a new one. */
	function newPlate() {
		session.editingPlateId = null;
		project.activePlateId = null;
		session.dirty = true;
		say('Next save creates a new plate');
	}

	// ----- inspector: stacked section heads ---------------------------------

	const stack = new SectionStack();
	let inspectorEl = $state<HTMLElement | null>(null);
	let sheetHeadH = $state(0);
	$effect(() => {
		if (inspectorEl) return stack.attach(inspectorEl);
	});

	const sections = $derived.by(() => {
		const scene = session.draft.scene;
		const def = session.indexDef;
		const tc = session.isTrueColor;
		const fit = session.sceneFit;
		const cm = session.colormapDef;
		const opts = session.reading?.options ?? [];
		const recommended = opts.find((o) => o.recommended) ?? null;
		const tone = session.draft.truecolor;
		const preset = getTrueColorPreset(tone?.preset ?? '');
		return sectionSummaries({
			scene: scene ? { date: scene.datetime.slice(0, 10), cloudCover: scene.cloudCover, satellite: scene.platform } : null,
			hasCloud: !!(session.scenePlatform ?? session.platform).cloudProperty,
			trueColor: tc,
			trueColorBlocker: session.trueColorBlocker,
			index: def ? { name: def.name, platformLabel: platformForIndex(def).label, gsdM: gsdForIndex(def), gpuSupported: planSlots(def).gpuSupported } : null,
			fitNote: scene && fit && !fits(fit) ? fit.note : null,
			read: { statsStatus: session.statsStatus, active: opts.find((o) => o.active)?.title ?? null, recommended: recommended?.title ?? null, done: session.readDone },
			tone: tc ? { preset: preset?.label ?? 'custom tone', edited: toneDiffersFromPreset(preset, tone) } : null,
			colormap: cm ? { shortName: cm.shortName, reversed: session.draft.colormap.reversed, printSafety: cm.printSafety, type: cm.type, suggestedRamp: recommended?.ramp ?? null } : null,
			caption: session.draft.caption
		});
	});
	const bodyId = (id: SectionId) => `section-${id}`;

	function onKey(e: KeyboardEvent) {
		const t = e.target as HTMLElement | null;
		if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
		if (e.key === '[') browser?.step(-1);
		else if (e.key === ']') browser?.step(1);
		else if (e.key === 'a') session.alignViewToPage();
		else if (e.key === 'n') session.northUp();
		else if (e.key === 'f') session.flyToFrame();
		else if (e.key === 'r') session.toggleReversed();
		else if (e.key === '?') introOpen = true;
		else if (e.key === 'Escape' && narrow.current) sheet = 'none';
		else if (e.key === 's' && (e.metaKey || e.ctrlKey)) {
			e.preventDefault();
			savePlate();
		} else if (e.key === 'e' && (e.metaKey || e.ctrlKey)) {
			e.preventDefault();
			exportOpen = true;
		} else return;
	}

	onMount(() => {
		window.addEventListener('keydown', onKey);
		// First visit with an empty book: introduce the picker and coach the first plate.
		// A browser that already holds a book skips both; the intro (?) can bring the guide back.
		const hasBook = project.plates.length > 0;
		guideDone = hasBook || getFlag(PREF_GUIDE_DONE);
		if (!hasBook && !getFlag(PREF_INTRO_SEEN)) introOpen = true;
		return () => window.removeEventListener('keydown', onKey);
	});
	$effect(() => {
		if (introOpen) introEverOpened = true;
		else if (introEverOpened) setFlag(PREF_INTRO_SEEN, true);
	});
</script>

<svelte:head>
	<title>{project.project?.name ?? 'Spectralcular'} · plate picker</title>
	<meta name="description" content="Compose printed pages of satellite spectral indices: frame a place on a live map, pick a scene, an index and a colormap (or a true-color tone), and export the selection the Spectralcular pipeline prints from." />
</svelte:head>

{#snippet colormapSwatch()}
	{#if session.isTrueColor}
		<TrueColorSwatch tone={session.draft.truecolor} height={8} />
	{:else if session.colormapDef}
		<GradientSwatch stops={session.colormapDef.previewStops} reversed={session.draft.colormap.reversed} height={8} />
	{/if}
{/snippet}

{#snippet actions(compact: boolean)}
	<button class="btn quiet" onclick={() => session.startHere()} title="Start a new plate at the map center">Frame here</button>
	{#if editing}<button class="btn" onclick={() => savePlate(true)} title="Save the current settings as an additional plate">Save as new</button>{/if}
	{#if !compact}<button class="btn primary" onclick={() => savePlate()} title="⌘S">{editing ? 'Update plate' : 'Save plate'}</button>{/if}
	<button class="btn" onclick={() => (exportOpen = true)} title="⌘E">Export</button>
{/snippet}

<div class="app" class:narrow={narrow.current} style:--frame-accent={session.accent}>
	<header class="top">
		<input class="project-name" value={project.project.name} onchange={(e) => project.rename(e.currentTarget.value)} aria-label="Book name" />
		<div class="state mono">
			{#if project.loadError}<span class="bad" title={project.loadError}>saved project could not be read · quarantined as {project.quarantinedKey}</span>{/if}
			<span>{plateLabel}</span>
			<span class="sep">·</span><span>{session.draft.location.name}</span>
			{#if session.isTrueColor}
				<span class="sep">·</span><span>True color</span>
				<span class="sep">·</span><span>{getTrueColorPreset(session.draft.truecolor?.preset ?? '')?.label ?? 'custom tone'}</span>
			{:else}
				<span class="sep">·</span><span>{session.draft.index}</span>
				<span class="sep">·</span><span>{session.colormapDef?.shortName ?? session.draft.colormap.name}{session.draft.colormap.reversed ? ' ⇄' : ''}</span>
			{/if}
			{#if session.dirty}<span class="sep">·</span><span class="unsaved">unsaved</span>{/if}
		</div>
		<div class="actions">
			{#if narrow.current}
				{#if session.dirty}<span class="unsaved mono" title="Unsaved changes">●</span>{/if}
				<button class="btn primary" onclick={() => savePlate()} title="⌘S">{editing ? 'Update' : 'Save'}</button>
			{:else}
				{@render actions(false)}
			{/if}
			<button class="btn ghost help" onclick={() => (introOpen = true)} title="About this picker and how to use it (?)" aria-label="About this picker">?</button>
		</div>
	</header>

	<aside class="rail" hidden={!railOpen}>
		<div class="tabs mono">
			<button class:on={rail === 'places'} onclick={() => setRail('places')} aria-pressed={rail === 'places'}>Places<span class="count">&nbsp;· {locations.length}</span></button>
			<button class:on={rail === 'plates'} onclick={() => setRail('plates')} aria-pressed={rail === 'plates'}>Plates · {project.plates.length}</button>
			<button class:on={rail === 'overview'} onclick={() => setRail('overview')} aria-pressed={rail === 'overview'} title="The whole book: places, scene dates, and which indices and colormaps it uses">Overview</button>
			{#if narrow.current}<button class="close" onclick={() => (sheet = 'none')} aria-label="Close">✕</button>{/if}
		</div>
		<div class="rail-body">
			{#if rail === 'places'}
				<LocationBrowser bind:this={browser} current={currentLocationId} onPick={pickLocation} onPoint={pickPoint} />
			{:else if rail === 'plates'}
				<PlateList onExport={() => (exportOpen = true)} onNew={newPlate} />
			{:else}
				<BookOverview bind:focusIds={overviewFocus} onOpenPlate={openPlate} onShowOnMap={showPlatesOnMap} />
			{/if}
		</div>
		<div class="keys eyebrow">[ ] places · a align · n north · f frame · r reverse · ⌘S save · ⌘E export</div>
	</aside>

	<main class="stage">
		<div class="over top-left">
			<FrameToolbar />
			{#if !guideDone}
				<div class="guide-slot">
					<Guide narrow={narrow.current} onDismiss={dismissGuide} onExample={runExample} onOpenInspector={() => (sheet = 'inspector')} onOpenPlaces={() => showRail('places')} />
				</div>
			{/if}
		</div>
		<MapView onPickLocation={pickLocation} showPlates={rail === 'overview'} focusIds={rail === 'overview' ? overviewFocus : []} onOpenPlate={openPlate} />
		<div class="over bottom"><PrintReadout compact={narrow.current} /></div>
	</main>

	<aside class="inspector scroll" hidden={!inspectorOpen} bind:this={inspectorEl} style:--stack-top="{narrow.current ? sheetHeadH : 0}px" aria-label="Plate settings">
		{#if narrow.current}
			<div class="sheet-head" bind:clientHeight={sheetHeadH}>
				<span class="eyebrow">{plateLabel} · {session.draft.location.name}</span>
				{@render actions(true)}
				<button class="btn ghost sm close" onclick={() => (sheet = 'none')} aria-label="Close">✕</button>
			</div>
		{/if}
		{#each sections as s, i (s.id)}
			<InspectorSection {s} {i} total={sections.length} current={stack.current === i} bodyId={bodyId(s.id)} onJump={(k) => stack.jump(k)} extra={s.id === 'colormap' ? colormapSwatch : undefined}>
				{#if s.id === 'scene'}
					<ScenePicker />
				{:else if s.id === 'index'}
					<IndexPicker />
				{:else if s.id === 'read'}
					<ReadPanel />
				{:else if s.id === 'colormap'}
					<ColormapPicker />
				{:else}
					<div class="section">
						<textarea class="input" rows="2" aria-label="Caption" placeholder="{session.draft.location.name} — {session.isTrueColor ? 'True color' : session.draft.index}" bind:value={session.draft.caption} oninput={() => (session.dirty = true)}></textarea>
						<textarea class="input notes" rows="2" aria-label="Notes" placeholder="Notes for the editorial pass" bind:value={session.draft.notes} oninput={() => (session.dirty = true)}></textarea>
					</div>
				{/if}
			</InspectorSection>
		{/each}
	</aside>

	{#if narrow.current}
		<nav class="bar mono" aria-label="Panels">
			<button class:on={sheet === 'none'} onclick={() => (sheet = 'none')}>Map</button>
			<button class:on={sheet === 'rail' && rail === 'places'} onclick={() => showRail('places')}>Places</button>
			<button class:on={sheet === 'rail' && rail !== 'places'} onclick={() => showRail(lastBookTab)} title="Plates and the book overview">Plates <span class="n">{project.plates.length}</span></button>
			<button class:on={sheet === 'inspector'} onclick={() => toggleSheet('inspector')}>Plate{#if session.dirty}<span class="dot"></span>{/if}</button>
		</nav>
	{/if}

	{#if toast}<div class="toast">{toast}</div>{/if}
	<ExportDialog bind:open={exportOpen} />
	<Intro bind:open={introOpen} onExample={runExample} onBrowse={browsePlaces} onShowGuide={showGuide} />
</div>

<style>
	.app { display: grid; grid-template-columns: 380px 1fr 320px; grid-template-rows: 50px 1fr; grid-template-areas: 'top top top' 'rail stage inspector'; height: 100vh; height: 100dvh; background: var(--paper); }
	.top { grid-area: top; display: flex; align-items: center; gap: 18px; padding: 0 18px; border-bottom: var(--hair) solid var(--rule); min-width: 0; }
	.project-name { background: transparent; border: 0; border-bottom: var(--hair) solid transparent; font-family: var(--serif); font-size: 20px; color: var(--ink); padding: 2px 0; min-width: 0; width: 200px; }
	.project-name:hover, .project-name:focus { border-bottom-color: var(--rule); outline: none; }
	.state { flex: 1; min-width: 0; display: flex; gap: 6px; font-size: 10.5px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--ink-soft); white-space: nowrap; overflow: hidden; }
	.state .sep { color: var(--ink-mute); }
	.unsaved { color: var(--accent); font-weight: 700; }
	.state .bad { color: var(--bad); }
	.actions { display: flex; gap: 6px; align-items: center; }
	.help { padding: 5px 9px; margin-left: 6px; }
	.rail[hidden], .inspector[hidden] { display: none; }
	.rail { grid-area: rail; display: flex; flex-direction: column; min-height: 0; border-right: var(--hair) solid var(--rule); background: var(--paper); }
	.tabs { display: flex; border-bottom: var(--hair) solid var(--rule); }
	.tabs button { flex: 1; background: none; border: 0; padding: 10px 14px; font-size: 10.5px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--ink-mute); }
	.tabs button.on { color: var(--ink); box-shadow: inset 0 -1.5px var(--accent); }
	.tabs .close { flex: 0 0 44px; }
	@media (max-width: 1279px) { .tabs button { padding: 10px 6px; letter-spacing: 0.08em; } .tabs .count { display: none; } }
	.rail-body { flex: 1; min-height: 0; }
	.keys { padding: 8px 14px; border-top: var(--hair) solid var(--rule); font-size: 9.5px; letter-spacing: 0.06em; text-transform: none; }
	.stage { grid-area: stage; position: relative; min-width: 0; min-height: 0; }
	.over { position: absolute; left: 0; right: 0; z-index: 2; }
	.over.top-left { top: 0; }
	.over.bottom { bottom: 0; }
	/* The guide sits in flow under the toolbar (whose height varies with wrapping) and lets map clicks through around it. */
	.guide-slot { padding: 10px 12px 0; pointer-events: none; }
	.inspector { grid-area: inspector; border-left: var(--hair) solid var(--rule); min-height: 0; background: var(--paper); }
	.sheet-head { position: sticky; top: 0; z-index: 3; display: flex; align-items: center; gap: 6px; padding: 8px 12px; background: var(--paper); border-bottom: var(--hair) solid var(--rule); }
	.sheet-head .eyebrow { flex: 1 1 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
	.sheet-head .btn { white-space: nowrap; padding: 4px 8px; font-size: 10.5px; }
	.notes { margin-top: 6px; }
	.toast { position: fixed; bottom: 70px; left: 50%; transform: translateX(-50%); background: var(--ink); color: var(--paper); padding: 8px 14px; font-family: var(--mono); font-size: 11px; letter-spacing: 0.06em; z-index: 10; max-width: 90vw; text-align: center; }

	@media (max-width: 1279px) { .app { grid-template-columns: 300px 1fr 300px; } }

	/* Narrow: one stage, with the rail and inspector as sheets sharing its grid cell. */
	.app.narrow { grid-template-columns: minmax(0, 1fr); grid-template-rows: 48px 1fr 46px; grid-template-areas: 'top' 'stage' 'bar'; }
	.app.narrow .top { gap: 10px; padding: 0 12px; }
	.app.narrow .project-name { flex: 1; width: auto; font-size: 17px; }
	.app.narrow .state { display: none; }
	.app.narrow .rail, .app.narrow .inspector { grid-area: stage; z-index: 3; width: min(100%, 400px); border: 0; }
	.app.narrow .rail { justify-self: start; border-right: var(--hair) solid var(--rule); }
	.app.narrow .inspector { justify-self: end; border-left: var(--hair) solid var(--rule); }
	.app.narrow .keys { display: none; }
	.app.narrow .guide-slot { padding: 8px 10px 0; }
	.app.narrow .help { margin-left: 0; }
	.app.narrow .toast { bottom: 60px; }
	.bar { grid-area: bar; display: flex; border-top: var(--hair) solid var(--ink); background: var(--paper); padding-bottom: env(safe-area-inset-bottom); }
	.bar button { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 5px; background: none; border: 0; font-size: 10.5px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--ink-mute); }
	.bar button + button { border-left: var(--hair) solid var(--rule); }
	.bar button.on { color: var(--accent); box-shadow: inset 0 1.5px var(--accent); }
	.bar .n { color: var(--ink-mute); font-size: 9.5px; }
	.bar .dot { width: 5px; height: 5px; border-radius: 50%; background: var(--accent); }
</style>
