import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),
	kit: {
		// Pure client-side app (WebGL + localStorage). GitHub Pages serves
		// /<repo>/404.html for unknown paths, so that is the SPA fallback and
		// deep links still boot the app; index.html stays prerendered.
		adapter: adapter({ fallback: '404.html' }),
		// GitHub Pages serves project sites under /<repo>; the Pages workflow
		// sets BASE_PATH from actions/configure-pages ("/spectralcular", or "" on
		// a custom domain). Local dev and root deployments leave it empty.
		paths: { base: process.env.BASE_PATH ?? '' },
		alias: { $data: 'src/lib/data' }
	}
};

export default config;
