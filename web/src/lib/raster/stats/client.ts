import type { AnyStatsRequest, AnyStatsResult, RgbStatsRequest, RgbStatsResult, StatsRequest, StatsResponse, StatsResult } from './types';

/** Main-thread handle on the stats worker: one in-flight request, newest wins. */
export class StatsClient {
	private worker: Worker | null = null;
	private nextId = 1;
	private pending = new Map<number, { resolve: (r: AnyStatsResult) => void; reject: (e: Error) => void }>();

	private ensure(): Worker {
		if (!this.worker) {
			this.worker = new Worker(new URL('./stats.worker.ts', import.meta.url), { type: 'module' });
			this.worker.onmessage = (e: MessageEvent<StatsResponse>) => {
				const msg = e.data;
				const id = msg.ok ? msg.result.requestId : msg.requestId;
				const p = this.pending.get(id);
				if (!p) return;
				this.pending.delete(id);
				if (msg.ok) p.resolve(msg.result);
				else p.reject(new Error(msg.error));
			};
			this.worker.onerror = (e) => {
				for (const p of this.pending.values()) p.reject(new Error(e.message));
				this.pending.clear();
			};
		}
		return this.worker;
	}

	compute(req: Omit<StatsRequest, 'requestId'>): Promise<StatsResult> {
		return this.send({ ...req, kind: 'index' }) as Promise<StatsResult>;
	}

	/** Per-channel reflectance statistics for a true-color frame. */
	computeRgb(req: Omit<RgbStatsRequest, 'requestId'>): Promise<RgbStatsResult> {
		return this.send(req) as Promise<RgbStatsResult>;
	}

	private send(req: Omit<StatsRequest, 'requestId'> | Omit<RgbStatsRequest, 'requestId'>): Promise<AnyStatsResult> {
		const requestId = this.nextId++;
		// Older requests are superseded — reject them so callers drop stale UI.
		for (const [id, p] of this.pending) {
			p.reject(new StaleRequestError());
			this.pending.delete(id);
		}
		return new Promise((resolve, reject) => {
			this.pending.set(requestId, { resolve, reject });
			this.ensure().postMessage({ ...req, requestId } as AnyStatsRequest);
		});
	}

	dispose(): void {
		this.worker?.terminate();
		this.worker = null;
		this.pending.clear();
	}
}

export class StaleRequestError extends Error {
	constructor() {
		super('superseded by a newer stats request');
		this.name = 'StaleRequestError';
	}
}
