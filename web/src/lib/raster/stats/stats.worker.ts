/// <reference lib="webworker" />
import { computeFrameStats, computeRgbStats } from './compute';
import type { AnyStatsRequest, StatsResponse } from './types';

let current: AbortController | null = null;

self.onmessage = async (e: MessageEvent<AnyStatsRequest>) => {
	const req = e.data;
	current?.abort();
	const ctrl = new AbortController();
	current = ctrl;
	try {
		const result = req.kind === 'rgb' ? await computeRgbStats(req, ctrl.signal) : await computeFrameStats(req, ctrl.signal);
		if (!ctrl.signal.aborted) postMessage({ ok: true, result } satisfies StatsResponse);
	} catch (err) {
		if (ctrl.signal.aborted) return;
		postMessage({ ok: false, requestId: req.requestId, error: err instanceof Error ? err.message : String(err) } satisfies StatsResponse);
	}
};
