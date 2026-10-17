import { CompositeLayer, type CompositeLayerProps, type Layer, type UpdateParameters } from '@deck.gl/core';
import type { Texture } from '@luma.gl/core';
import type { IndexDef, PlatformDef, TrueColorSpec } from '$lib/data/types';
import { colormapLutUrl } from '$lib/data';
import { appendToken, getSasToken, needsSigning } from '$lib/stac/sas';
import { getColormapTexture } from './colormapTexture';
import { buildIndexLayer, buildTrueColorLayer } from './buildLayer';

export interface PlateLayerProps extends CompositeLayerProps {
	kind: 'index' | 'truecolor';
	sceneId: string;
	/** asset key → unsigned COG href */
	hrefs: Record<string, string>;
	/** the scene's platform (the index's, for index plates) */
	platform: PlatformDef;
	maskEnabled: boolean;
	beforeId?: string;
	onBuildError?: (message: string) => void;
	// index plates
	index?: IndexDef;
	colormapRow?: number;
	reversed?: boolean;
	vmin?: number;
	vmax?: number;
	valueOffset?: number;
	// true-color plates
	tone?: TrueColorSpec;
}

// A type alias (not an interface) so it satisfies deck.gl's indexed SharedLayerState.
type State = {
	texture: Texture | null;
	error: string | null;
	/** SAS token for `tokenFor`, when the platform's storage needs one */
	token: string | null;
	tokenFor: string | null;
	/** the LUT texture load has been started (index plates only) */
	textureRequested: boolean;
};

/**
 * The plate preview: an index through a colormap, or a true-color composite.
 * Creates the colormap LUT texture from the layer's own GPU device (index
 * plates only — true color never waits on it) and signs Planetary Computer
 * hrefs before the COG layer opens them, so no device or token plumbing is
 * needed from the overlay and it survives component remounts.
 */
export class PlateLayer extends CompositeLayer<PlateLayerProps> {
	static layerName = 'PlateLayer';
	static defaultProps = { kind: 'index', reversed: false, maskEnabled: true, valueOffset: 0 };

	declare state: State;

	initializeState(): void {
		this.state = { texture: null, error: null, token: null, tokenFor: null, textureRequested: false };
		this.ensureTexture();
		this.ensureToken();
	}

	updateState(params: UpdateParameters<this>): void {
		super.updateState(params);
		if (params.changeFlags.propsChanged) {
			this.ensureTexture();
			this.ensureToken();
		}
	}

	shouldUpdateState({ changeFlags }: UpdateParameters<this>): boolean {
		return changeFlags.propsOrDataChanged || changeFlags.stateChanged;
	}

	private ensureTexture(): void {
		if (this.props.kind !== 'index' || this.state.textureRequested) return;
		// Plain mutation: a flag, not something to re-render for.
		this.state.textureRequested = true;
		getColormapTexture(this.context.device, colormapLutUrl)
			.then((texture) => this.setState({ texture }))
			.catch((e: unknown) => this.setState({ error: e instanceof Error ? e.message : String(e) }));
	}

	private ensureToken(): void {
		const platform = this.props.platform;
		if (!needsSigning(platform) || this.state.tokenFor === platform.collection) return;
		getSasToken(platform.collection)
			.then((token) => this.setState({ token, tokenFor: platform.collection }))
			.catch((e: unknown) => this.setState({ error: e instanceof Error ? e.message : String(e) }));
	}

	renderLayers(): Layer | null {
		const { texture, error, token, tokenFor } = this.state;
		const p = this.props;
		if (error) {
			p.onBuildError?.(error);
			return null;
		}
		const platform = p.platform;
		let hrefs = p.hrefs;
		if (needsSigning(platform)) {
			if (!token || tokenFor !== platform.collection) return null; // token still loading
			hrefs = Object.fromEntries(Object.entries(p.hrefs).map(([k, v]) => [k, appendToken(v, token)]));
		}
		try {
			if (p.kind === 'truecolor') {
				if (!p.tone) return null;
				return buildTrueColorLayer({
					id: `${p.id}-truecolor`,
					sceneId: p.sceneId,
					hrefs,
					platform,
					tone: p.tone,
					maskEnabled: p.maskEnabled,
					beforeId: p.beforeId
				});
			}
			if (!texture || !p.index) return null;
			return buildIndexLayer({
				id: `${p.id}-cog`,
				sceneId: p.sceneId,
				hrefs,
				index: p.index,
				platform,
				colormapTexture: texture,
				colormapRow: p.colormapRow ?? 0,
				reversed: p.reversed,
				vmin: p.vmin ?? 0,
				vmax: p.vmax ?? 1,
				maskEnabled: p.maskEnabled,
				valueOffset: p.valueOffset,
				beforeId: p.beforeId
			});
		} catch (e) {
			p.onBuildError?.(e instanceof Error ? e.message : String(e));
			return null;
		}
	}
}
