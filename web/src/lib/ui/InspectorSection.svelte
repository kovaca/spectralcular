<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { SectionSummary } from './sectionStatus';

	/**
	 * One inspector section as two siblings: a sticky head and its body. Being
	 * direct children of the scrolling inspector, the heads stick across the
	 * whole panel: passed sections stack at the top, later ones at the bottom,
	 * so every section's state is always in view and one click away.
	 */
	let {
		s,
		i,
		total,
		current = false,
		bodyId,
		onJump,
		extra,
		children
	}: {
		s: SectionSummary;
		i: number;
		total: number;
		current?: boolean;
		bodyId: string;
		onJump: (i: number) => void;
		/** small graphic before the summary text (the colormap swatch) */
		extra?: Snippet;
		children: Snippet;
	} = $props();

	const STATUS_WORD = { done: 'set', todo: 'to do', warn: 'needs attention', opt: 'optional' } as const;
</script>

<button
	type="button"
	class="sec-head"
	class:current
	style:--i={i}
	style:--rest={total - 1 - i}
	aria-controls={bodyId}
	aria-current={current ? 'step' : undefined}
	title={s.hint ? `${s.title}: ${s.hint}` : `Go to ${s.roman} ${s.title}`}
	onclick={() => onJump(i)}
>
	<span class="glyph {s.status}" aria-hidden="true"></span>
	<span class="roman">{s.roman}</span><span class="title">{s.title}</span>
	<span class="sr-only">({STATUS_WORD[s.status]})</span>
	<span class="sum {s.status}">
		{#if extra}<span class="x">{@render extra()}</span>{/if}
		<span class="txt">{s.text}</span>
	</span>
</button>
<div class="sec-body" id={bodyId} style:--i={i} style:--rest={total - 1 - i}>
	{@render children()}
</div>

<style>
	.sec-head {
		position: sticky;
		top: calc(var(--stack-top, 0px) + var(--i) * var(--sec-h));
		bottom: calc(var(--rest) * var(--sec-h));
		z-index: 2;
		display: flex;
		align-items: center;
		gap: 0;
		width: 100%;
		height: var(--sec-h);
		padding: 0 16px 0 14px;
		background: var(--paper);
		border: 0;
		border-bottom: var(--hair) solid var(--rule);
		box-shadow: 0 calc(-1 * var(--hair)) 0 var(--rule);
		text-align: left;
		font-family: var(--mono);
		font-size: 10px;
		letter-spacing: 0.22em;
		text-transform: uppercase;
		color: var(--ink);
	}
	.sec-head:hover { background: var(--paper-2); }
	.sec-head.current { box-shadow: inset 2px 0 var(--accent), 0 calc(-1 * var(--hair)) 0 var(--rule); }
	.sec-head:focus-visible { outline-offset: -2px; }
	.roman { flex: 0 0 auto; }
	.title { flex: 0 0 auto; margin-right: 12px; }

	/* status: filled = set, hollow = to do, triangle = needs attention, dashed = optional */
	.glyph { flex: 0 0 7px; width: 7px; height: 7px; margin-right: 9px; border-radius: 50%; }
	.glyph.done { background: var(--ink); }
	.glyph.todo { border: 1px solid var(--ink-mute); }
	.glyph.opt { border: 1px dashed var(--ink-mute); }
	.glyph.warn { border-radius: 0; background: var(--warn); clip-path: polygon(50% 0, 100% 100%, 0 100%); width: 8px; flex-basis: 8px; }

	.sum { margin-left: auto; display: flex; align-items: center; gap: 6px; min-width: 0; text-transform: none; letter-spacing: 0.03em; font-size: 10.5px; color: var(--ink-soft); }
	.sum.todo, .sum.opt { color: var(--ink-mute); }
	.sum.warn { color: var(--warn); }
	.txt { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.x { flex: 0 0 36px; width: 36px; display: block; }

	/* the body lands just under its own head (and the heads stacked above it) */
	.sec-body {
		scroll-margin-top: calc(var(--stack-top, 0px) + (var(--i) + 1) * var(--sec-h));
		scroll-margin-bottom: calc(var(--rest) * var(--sec-h));
	}
	.sec-body > :global(.section) { border-bottom: 0; }
	/* the last body fills the panel, so every section can scroll up under the stack */
	.sec-body:last-child { min-height: calc(100% - var(--stack-top, 0px) - var(--i) * var(--sec-h) - var(--sec-h)); padding-bottom: 8px; }
</style>
