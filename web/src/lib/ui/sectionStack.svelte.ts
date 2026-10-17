/**
 * Tracks which inspector section is current while its heads stack (see
 * InspectorSection.svelte) and scrolls a section into place on request.
 *
 * Current = the last section whose head sits at the top of the panel, either
 * stuck to the stack or in its natural place right under it. A head is stuck
 * at the top when it touches the head before it while its body's predecessor
 * ends above it; heads stuck in the bottom stack also touch, but sit above
 * their natural place, which tells the two apart without reading any CSS.
 */
export class SectionStack {
	current = $state(0);
	#el: HTMLElement | null = null;
	#raf = 0;

	attach(el: HTMLElement): () => void {
		this.#el = el;
		const onScroll = () => {
			if (this.#raf) return;
			this.#raf = requestAnimationFrame(() => {
				this.#raf = 0;
				this.measure();
			});
		};
		el.addEventListener('scroll', onScroll, { passive: true });
		const ro = new ResizeObserver(onScroll);
		ro.observe(el);
		this.measure();
		return () => {
			el.removeEventListener('scroll', onScroll);
			ro.disconnect();
			cancelAnimationFrame(this.#raf);
			this.#raf = 0;
			if (this.#el === el) this.#el = null;
		};
	}

	measure(): void {
		const el = this.#el;
		if (!el) return;
		const heads = [...el.querySelectorAll<HTMLElement>(':scope > .sec-head')];
		const bodies = [...el.querySelectorAll<HTMLElement>(':scope > .sec-body')];
		if (!heads.length) return;
		// At the very bottom the last sections can't reach the top: the last one is current.
		if (el.scrollTop > 0 && el.scrollTop + el.clientHeight >= el.scrollHeight - 2) {
			this.current = heads.length - 1;
			return;
		}
		let cur = 0;
		for (let i = 1; i < heads.length; i++) {
			const h = heads[i].getBoundingClientRect();
			const prevHead = heads[i - 1].getBoundingClientRect();
			const prevBody = bodies[i - 1]?.getBoundingClientRect();
			if (!prevBody) break;
			const atTop = h.top <= prevHead.bottom + 0.5 && prevBody.bottom <= h.top + 0.5;
			if (atTop) cur = i;
			else break;
		}
		this.current = cur;
	}

	jump(i: number): void {
		const el = this.#el;
		const body = el?.querySelectorAll<HTMLElement>(':scope > .sec-body')[i];
		if (!body) return;
		const reduce = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
		body.scrollIntoView({ block: 'start', behavior: reduce ? 'auto' : 'smooth' });
		this.current = i;
	}
}
