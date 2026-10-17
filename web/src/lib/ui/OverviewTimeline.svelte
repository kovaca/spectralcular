<script lang="ts">
	import { themeCss } from '$lib/data';
	import { stackMarks, type OverviewMark } from '$lib/state/overview';

	/**
	 * When the book's scenes were taken: one mark per plate on a year axis
	 * (crowded marks stack into rows), and a month strip below for the season.
	 */
	let {
		marks,
		months,
		noScene,
		focusIds,
		onFocus
	}: {
		marks: OverviewMark[];
		months: number[];
		noScene: number;
		focusIds: string[];
		onFocus: (plateId: string) => void;
	} = $props();

	const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
	const MARK_PX = 9;
	const ROW_PX = 11;
	const MAX_ROWS = 4;

	let width = $state(300);
	const focusSet = $derived(new Set(focusIds));

	const axis = $derived.by(() => {
		if (!marks.length) return null;
		const y0 = Number(marks[0].date.slice(0, 4));
		const y1 = Number(marks.at(-1)!.date.slice(0, 4)) + 1;
		const t0 = Date.UTC(y0, 0, 1);
		const t1 = Date.UTC(y1, 0, 1);
		const years = Array.from({ length: y1 - y0 + 1 }, (_, i) => y0 + i);
		// label every year when there's room, otherwise every 2nd/5th
		const every = years.length <= 7 ? 1 : years.length <= 14 ? 2 : 5;
		return {
			t0,
			t1,
			ticks: years.map((y) => ({ y, x: (Date.UTC(y, 0, 1) - t0) / (t1 - t0), label: y < y1 && (y - y0) % every === 0 }))
		};
	});
	const placed = $derived.by(() => {
		if (!axis) return [];
		const gap = width > 0 ? (MARK_PX + 1) / width : 0.03;
		return stackMarks(
			marks.map((m) => ({ ...m, x: (m.t - axis.t0) / (axis.t1 - axis.t0) })),
			gap,
			MAX_ROWS
		);
	});
	const rows = $derived(Math.max(1, ...placed.map((m) => m.row + 1)));
	const monthMax = $derived(Math.max(1, ...months));
	/** months that hold a focused plate */
	const focusMonths = $derived(new Set(marks.filter((m) => focusSet.has(m.plateId)).map((m) => Number(m.date.slice(5, 7)) - 1)));
</script>

{#if axis}
	<div class="track" bind:clientWidth={width} style:height="{rows * ROW_PX + 6}px">
		{#each axis.ticks as t (t.y)}
			<span class="tick" class:edge={t.x <= 0 || t.x >= 1} style:left="{t.x * 100}%"></span>
		{/each}
		{#each placed as m (m.plateId)}
			<button
				class="mark"
				class:focus={focusSet.has(m.plateId)}
				class:dim={focusSet.size > 0 && !focusSet.has(m.plateId)}
				style:left="{m.x * 100}%"
				style:bottom="{m.row * ROW_PX + 3}px"
				style:background={themeCss(m.theme)}
				title="{String(m.ordinal).padStart(3, '0')} · {m.name} · {m.date}"
				aria-label="Plate {m.ordinal}, {m.name}, {m.date}"
				onclick={() => onFocus(m.plateId)}
			></button>
		{/each}
	</div>
	<div class="years mono" aria-hidden="true">
		{#each axis.ticks as t (t.y)}
			{#if t.label}<span style:left="{t.x * 100}%">{t.y}</span>{/if}
		{/each}
	</div>
	<div class="months" role="img" aria-label="Scenes by month: {months.map((n, i) => (n ? `${MONTHS[i]} ${n}` : '')).filter(Boolean).join(', ')}">
		{#each months as n, i (i)}
			<span class="m" class:focus={focusMonths.has(i)} title="{MONTHS[i]} · {n} plate{n === 1 ? '' : 's'}">
				<span class="well"><span class="bar" style:height="{(n / monthMax) * 100}%"></span></span>
				<span class="ml mono">{MONTHS[i][0]}</span>
			</span>
		{/each}
	</div>
{:else}
	<p class="none">No scenes pinned yet.</p>
{/if}
{#if noScene && marks.length}<p class="none">{noScene} plate{noScene === 1 ? '' : 's'} without a scene</p>{/if}

<style>
	.track { position: relative; border-bottom: var(--hair) solid var(--ink-mute); margin: 0 5px; }
	.tick { position: absolute; bottom: -3px; height: 5px; border-left: var(--hair) solid var(--ink-mute); }
	.tick:not(.edge) { top: 0; height: auto; border-left-style: dotted; border-color: var(--rule); bottom: -3px; }
	.mark { position: absolute; width: 9px; height: 9px; transform: translateX(-50%); padding: 0; border: var(--hair) solid var(--ink); border-radius: 50%; }
	.mark:hover { outline: 1px solid var(--ink-mute); outline-offset: 1px; }
	.mark.focus { outline: 1.5px solid var(--accent); outline-offset: 1px; z-index: 1; }
	.mark.dim { opacity: 0.25; }
	.years { position: relative; height: 14px; margin: 2px 5px 0; font-size: 9px; color: var(--ink-mute); }
	.years span { position: absolute; transform: translateX(-50%); white-space: nowrap; }
	.years span:first-child { transform: none; }
	.months { display: grid; grid-template-columns: repeat(12, 1fr); gap: 2px; height: 34px; margin-top: 6px; }
	.m { display: grid; grid-template-rows: 1fr 11px; min-width: 0; }
	.well { display: flex; align-items: flex-end; border-bottom: var(--hair) solid var(--rule); min-height: 0; }
	.bar { display: block; width: 100%; background: var(--ink-soft); }
	.m.focus .bar { background: var(--accent); }
	.ml { text-align: center; font-size: 8.5px; color: var(--ink-mute); line-height: 11px; }
	.none { margin: 4px 0 0; font-size: 11.5px; font-style: italic; color: var(--ink-mute); }
	@media (pointer: coarse) {
		.mark { width: 12px; height: 12px; }
	}
</style>
