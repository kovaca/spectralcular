/**
 * Register MapLibre's web worker with Vite. MapLibre 6 ships the worker as a
 * separate module; letting Vite resolve it from *our* source (rather than from
 * inside a dependency) keeps both `vite dev` and the static build working.
 */
import { setWorkerUrl } from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';

if (typeof window !== 'undefined') {
	setWorkerUrl(workerUrl);
}
