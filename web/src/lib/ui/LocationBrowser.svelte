<script lang="ts">
	import { untrack } from 'svelte';
	import { locations, THEME_ORDER, THEME_LABEL, themeCss, type LocationDef } from '$lib/data';
	import { bboxSizeMeters, type LonLat } from '$lib/geo/tangent';
	import { formatLatLon, parseLatLon } from '$lib/geo/coords';
	import { searchPlaces, type GeocodeHit } from '$lib/geo/geocode';

	let {
		current,
		onPick,
		onPoint
	}: {
		current: string | null;
		onPick: (loc: LocationDef) => void;
		/** a searched place or typed coordinates: start an ad-hoc plate there */
		onPoint: (name: string, center: LonLat) => void;
	} = $props();

	let theme = $state<string>('all');
	let status = $state<string>('all');
	let query = $state('');

	const q = $derived(query.trim());
	/** typed coordinates, e.g. `37.75, 15` or `37°45'N 15°E` */
	const point = $derived(parseLatLon(q));

	const filtered = $derived(
		locations.filter(
			(l) =>
				(theme === 'all' || l.themes.includes(theme as LocationDef['themes'][number])) &&
				(status === 'all' || l.status === status) &&
				(q === '' || `${l.name} ${l.landscapeCharacter} ${l.id}`.toLowerCase().includes(q.toLowerCase()))
		)
	);

	/** Place names beyond the book pool, looked up once typing pauses. */
	const GEOCODE_MIN_CHARS = 3;
	let geo = $state<{ status: 'idle' | 'loading' | 'done' | 'error'; hits: GeocodeHit[] }>({ status: 'idle', hits: [] });
	const worldwide = $derived(!point && q.length >= GEOCODE_MIN_CHARS);
	$effect(() => {
		const text = q;
		if (!worldwide) {
			geo = { status: 'idle', hits: [] };
			return;
		}
		// keep the previous hits on screen while the next lookup runs
		geo = { status: 'loading', hits: untrack(() => geo.hits) };
		const ctrl = new AbortController();
		const t = setTimeout(async () => {
			try {
				geo = { status: 'done', hits: await searchPlaces(text, ctrl.signal) };
			} catch {
				if (!ctrl.signal.aborted) geo = { status: 'error', hits: [] };
			}
		}, 300);
		return () => {
			clearTimeout(t);
			ctrl.abort();
		};
	});

	function pickPoint() {
		if (point) onPoint(formatLatLon(point, 3), point);
	}
	function pickHit(h: GeocodeHit) {
		onPoint(h.name, h.center);
	}
	/** Enter takes the most specific match: coordinates, then the book pool, then anywhere. */
	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && query) {
			query = '';
			e.preventDefault();
		}
		if (e.key !== 'Enter') return;
		e.preventDefault();
		if (point) pickPoint();
		else if (q && filtered.length) onPick(filtered[0]);
		else if (geo.hits.length) pickHit(geo.hits[0]);
	}

	const idx = $derived(filtered.findIndex((l) => l.id === current));

	export function step(delta: number) {
		if (filtered.length === 0) return;
		const next = idx < 0 ? (delta > 0 ? 0 : filtered.length - 1) : (idx + delta + filtered.length) % filtered.length;
		onPick(filtered[next]);
		requestAnimationFrame(() => document.getElementById(`loc-${filtered[next].id}`)?.scrollIntoView({ block: 'nearest' }));
	}

	function km(l: LocationDef) {
		const s = bboxSizeMeters(l.bbox);
		return `${(s.widthM / 1000).toFixed(0)} × ${(s.heightM / 1000).toFixed(0)} km`;
	}
</script>

<div class="browser">
	<div class="filters">
		<input class="input" type="search" placeholder="Search places or enter lat, lon" bind:value={query} onkeydown={onKeydown} aria-label="Search places, or enter a latitude and longitude" />
		<div class="row">
			<select class="input" bind:value={theme} aria-label="Theme">
				<option value="all">All themes</option>
				{#each THEME_ORDER as t}<option value={t}>{THEME_LABEL[t]}</option>{/each}
			</select>
			<select class="input" bind:value={status} aria-label="Status">
				<option value="all">Any status</option>
				<option value="approved">Approved</option>
				<option value="proposed">Proposed</option>
			</select>
		</div>
		<div class="nav">
			<span class="eyebrow">{point ? 'coordinates' : `${filtered.length} ${q ? 'in the book pool' : 'places'}`}</span>
			<span class="keys"><button class="btn ghost sm" onclick={() => step(-1)} title="Previous ( [ )">‹ prev</button><button class="btn ghost sm" onclick={() => step(1)} title="Next ( ] )">next ›</button></span>
		</div>
	</div>
	<ul class="list scroll" role="listbox" aria-label="Places">
		{#if point}
			<li>
				<button class="loc go" role="option" aria-selected="false" onclick={pickPoint}>
					<span class="n mono">↵</span>
					<span class="body">
						<span class="name">Frame {formatLatLon(point, 4)}</span>
						<span class="meta">new plate centered on these coordinates</span>
					</span>
				</button>
			</li>
		{/if}
		{#if q && filtered.length && (point || worldwide)}
			<li class="group" role="presentation"><span class="eyebrow">Book pool</span></li>
		{/if}
		{#each filtered as l, i (l.id)}
			<li>
				<button id="loc-{l.id}" class="loc" class:current={l.id === current} role="option" aria-selected={l.id === current} onclick={() => onPick(l)}>
					<span class="n mono">{String(i + 1).padStart(3, '0')}</span>
					<span class="body">
						<span class="name"><span class="dot" class:approved={l.status === 'approved'} style:background={themeCss(l.themes[0])}></span>{l.name}</span>
						<span class="meta">{l.landscapeCharacter}</span>
						<span class="meta mono">{km(l)} · {l.seasonWindows[0]?.slice(0, 4)} · {l.themes.map((t) => THEME_LABEL[t]).join(', ').toLowerCase()}</span>
					</span>
				</button>
			</li>
		{:else}
			{#if !point && !worldwide}
				<li class="empty">No book places match. Type a place name or a lat, lon to frame anywhere, or use "Frame here".</li>
			{/if}
		{/each}
		{#if worldwide}
			<li class="group" role="presentation">
				<span class="eyebrow">Anywhere</span>
				<span class="src mono">
					{#if geo.status === 'loading'}searching…{:else if geo.status === 'error'}search unavailable{:else if geo.status === 'done' && !geo.hits.length}no matches{:else}OpenStreetMap · Photon{/if}
				</span>
			</li>
			{#each geo.hits as h (h.id)}
				<li>
					<button class="loc world" role="option" aria-selected="false" onclick={() => pickHit(h)} title="Frame {h.name} at full print resolution">
						<span class="n mono">·</span>
						<span class="body">
							<span class="name">{h.name}</span>
							<span class="meta">{[h.kind, h.detail].filter(Boolean).join(' · ')}</span>
							<span class="meta mono">{formatLatLon(h.center, 3)}</span>
						</span>
					</button>
				</li>
			{/each}
		{/if}
	</ul>
</div>

<style>
	.browser { display: flex; flex-direction: column; height: 100%; min-height: 0; }
	.filters { display: flex; flex-direction: column; gap: 6px; padding: 10px 14px; border-bottom: var(--hair) solid var(--rule); }
	.row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 6px; }
	.nav { display: flex; justify-content: space-between; align-items: center; margin-top: 2px; }
	.keys { display: flex; gap: 2px; }
	.list { list-style: none; margin: 0; padding: 0; flex: 1; min-height: 0; }
	.loc { display: flex; gap: 10px; width: 100%; text-align: left; padding: 8px 14px; background: none; border: 0; border-top: 0.3px dotted var(--rule); }
	.loc:hover { background: var(--paper-2); }
	.loc.current { background: var(--paper-2); box-shadow: inset 2px 0 var(--accent); }
	.n { flex: 0 0 26px; font-size: 9.5px; letter-spacing: 0.1em; color: var(--ink-mute); padding-top: 3px; }
	.body { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
	.name { font-family: var(--serif); font-size: 13.5px; display: flex; align-items: center; gap: 6px; }
	.dot { flex: 0 0 7px; width: 7px; height: 7px; border-radius: 50%; opacity: 0.45; background: #bbb; }
	.dot.approved { opacity: 1; }
	.meta { color: var(--ink-mute); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.meta.mono { font-size: 10px; }
	.group { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; padding: 10px 14px 4px; border-top: var(--hair) solid var(--rule); }
	.group:first-child { border-top: 0; }
	.src { font-size: 9.5px; letter-spacing: 0.06em; color: var(--ink-mute); }
	.loc.go .n, .loc.world .n { color: var(--accent); }
	.loc.go { background: var(--paper-2); }
</style>
