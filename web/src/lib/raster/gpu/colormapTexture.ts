import type { Device, Texture } from '@luma.gl/core';
import { createColormapTexture } from '@developmentseed/deck.gl-raster/gpu-modules';

let imageDataPromise: Promise<ImageData> | null = null;
const textures = new WeakMap<Device, Texture>();

/** Decode the exported LUT sprite (256 × N RGBA) once per page. */
export function loadColormapImageData(url: string): Promise<ImageData> {
	if (!imageDataPromise) {
		imageDataPromise = (async () => {
			const res = await fetch(url);
			if (!res.ok) throw new Error(`Failed to load colormap LUT ${url}: ${res.status}`);
			const bitmap = await createImageBitmap(await res.blob(), { premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
			const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
			const ctx = canvas.getContext('2d', { willReadFrequently: true });
			if (!ctx) throw new Error('2D canvas unavailable');
			ctx.drawImage(bitmap, 0, 0);
			return ctx.getImageData(0, 0, bitmap.width, bitmap.height);
		})();
	}
	return imageDataPromise;
}

/** One 2D-array texture (one layer per colormap row) per GPU device. */
export async function getColormapTexture(device: Device, url: string): Promise<Texture> {
	const hit = textures.get(device);
	if (hit) return hit;
	const imageData = await loadColormapImageData(url);
	// WebGL2 guarantees only 256 array layers; the sprite has one per colormap.
	const maxLayers = device.limits?.maxTextureArrayLayers ?? Infinity;
	if (imageData.height > maxLayers) {
		throw new Error(
			`Colormap LUT has ${imageData.height} rows but this GPU allows ${maxLayers} texture-array layers. ` +
				`Re-run \`spectralcular export-web\` with a smaller pool or split the LUT.`
		);
	}
	const tex = createColormapTexture(device, imageData);
	textures.set(device, tex);
	return tex;
}
