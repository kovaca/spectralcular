<script lang="ts">
	import { getSession } from '$lib/state/session.svelte';
	import { normalizeRotation } from '$lib/geo/frame';
	import { PAGE } from '$lib/book/page';

	const session = getSession();
	const f = $derived(session.draft.frame);

	function setWidthKm(km: number) {
		if (!Number.isFinite(km) || km <= 0) return;
		session.setFrame({ ...f, widthM: km * 1000 });
		session.refreshStats();
	}
	function setRotation(deg: number) {
		if (!Number.isFinite(deg)) return;
		session.setFrame({ ...f, rotationDeg: normalizeRotation(deg) });
		session.refreshStats();
	}
	function fitCeiling() {
		setWidthKm(session.print.ceilingWidthM / 1000);
	}
</script>

<div class="toolbar">
	<div class="name">
		<input class="title" value={session.draft.location.name} onchange={(e) => session.setLocationName(e.currentTarget.value)} aria-label="Location name" />
		<span class="eyebrow">{session.draft.location.kind === 'pool' ? `pool · ${session.draft.location.locationId}` : `new · ${session.draft.location.locationId}`}</span>
	</div>
	<label class="ctl field"><span>Width km</span><input class="input" type="number" min="0.5" step="0.5" value={(f.widthM / 1000).toFixed(2)} onchange={(e) => setWidthKm(e.currentTarget.valueAsNumber)} /></label>
	<label class="ctl field"><span>Rotation °</span><input class="input" type="number" step="1" value={f.rotationDeg.toFixed(1)} onchange={(e) => setRotation(e.currentTarget.valueAsNumber)} /></label>
	<div class="btns">
		<button class="btn sm" onclick={fitCeiling} title="Set the width so the plate prints at {PAGE.dpi} dpi from native pixels (the full-resolution ceiling)">Fit {PAGE.dpi} dpi</button>
		<button class="btn sm" onclick={() => setRotation(0)} title="Reset rotation">0°</button>
		<button class="btn sm" onclick={() => session.alignViewToPage()} title="Rotate the map so the page reads upright, as printed (a)">Align to page</button>
		<button class="btn sm" onclick={() => session.northUp()} title="Map north up (n)">North up</button>
		<button class="btn sm" onclick={() => session.flyToFrame()} title="Zoom to the frame (f)">Zoom to frame</button>
	</div>
</div>

<style>
	.toolbar { display: grid; grid-template-columns: minmax(140px, 1fr) 84px 84px auto; align-items: end; gap: 14px; padding: 8px 14px; background: color-mix(in oklab, var(--paper) 94%, transparent); border-bottom: var(--hair) solid var(--rule); backdrop-filter: blur(6px); }
	.name { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
	.title { width: 100%; background: transparent; border: 0; border-bottom: var(--hair) solid transparent; font-family: var(--serif); font-size: 20px; color: var(--ink); padding: 0; min-width: 0; }
	.title:hover, .title:focus { border-bottom-color: var(--rule); outline: none; }
	.ctl { min-width: 0; }
	.btns { display: flex; gap: 4px; flex-wrap: wrap; justify-content: flex-end; max-width: 460px; }
	.btns .btn { border-color: var(--rule); }
	.btns .btn:hover { border-color: var(--ink); }
	@media (max-width: 1023px) {
		.toolbar { grid-template-columns: 1fr 76px 76px; gap: 8px 10px; padding: 6px 12px 8px; }
		.title { font-size: 17px; }
		.btns { grid-column: 1 / -1; justify-content: flex-start; flex-wrap: nowrap; overflow-x: auto; max-width: none; scrollbar-width: none; margin: 0 -12px; padding: 0 12px; }
		.btns::-webkit-scrollbar { display: none; }
		.btns .btn { flex: 0 0 auto; }
	}
</style>
