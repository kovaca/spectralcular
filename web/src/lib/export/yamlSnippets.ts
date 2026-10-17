import type { TrueColorSpec } from '$lib/data/types';
import type { ManifestPlate, SelectionManifest } from './manifest';

/** Quote a YAML scalar conservatively. */
function q(s: string): string {
	return JSON.stringify(s);
}

function num(n: number, digits = 6): string {
	return Number(n.toFixed(digits)).toString();
}

const triple = (t: readonly number[] | null) => (t ? `[${t.map((v) => num(v)).join(', ')}]` : 'null');

/** A candidate's `truecolor:` block, snake_case like truecolor.py's TrueColorSpec. */
export function truecolorYaml(t: TrueColorSpec, indent = '  '): string[] {
	const st = t.stretch;
	return [
		`${indent}truecolor:`,
		`${indent}  preset: ${t.preset === null ? 'null' : t.preset}`,
		`${indent}  stretch: {mode: ${st.mode}, source: ${st.source}, p_lo: ${num(st.pLo)}, p_hi: ${num(st.pHi)}, lo: ${triple(st.lo)}, hi: ${triple(st.hi)}}`,
		`${indent}  gamma: ${num(t.gamma)}`,
		`${indent}  contrast: ${num(t.contrast)}`,
		`${indent}  midpoint: ${num(t.midpoint)}`,
		`${indent}  saturation: ${num(t.saturation)}`
	];
}

/** `config/candidates.yaml` entries (the fields the Python schema understands). */
export function candidatesYaml(m: SelectionManifest): string {
	const lines: string[] = ['entries:'];
	for (const p of m.plates) {
		lines.push(`- id: ${p.id}`);
		lines.push(`  location_id: ${p.locationId}`);
		if (p.kind === 'truecolor') {
			lines.push(`  kind: truecolor`);
			lines.push(`  platform: ${p.platform}`);
			if (p.truecolor) lines.push(...truecolorYaml(p.truecolor));
		} else {
			lines.push(`  index: ${p.index}`);
			lines.push(`  colormap: ${q(p.colormap ?? '')}`);
		}
		lines.push(`  theme: ${p.theme}`);
		lines.push(`  status: approved`);
		lines.push(`  ordinal: ${p.ordinal}`);
		lines.push(`  pairing_signal: picker`);
		lines.push(`  affinity_signal: picker`);
		if (p.normalization && p.normalization.vmin !== null && p.normalization.vmax !== null) {
			lines.push(`  normalization: {method: fixed, vmin: ${num(p.normalization.vmin)}, vmax: ${num(p.normalization.vmax)}}`);
		}
		lines.push(`  frame: {center: [${num(p.frame.center[0])}, ${num(p.frame.center[1])}], width_m: ${num(p.frame.widthM, 1)}, height_m: ${num(p.frame.heightM, 1)}, rotation_deg: ${num(p.frame.rotationDeg, 2)}}`);
		if (p.scene) lines.push(`  scene_ids: [${p.scene.itemId}]`);
		if (p.dateRange) lines.push(`  date_range: ${p.dateRange}`);
		lines.push(`  caption: ${q(p.caption)}`);
		lines.push(`  notes: ${q(p.notes)}`);
	}
	return lines.join('\n') + '\n';
}

/** `config/pool/locations.yaml` entries for ad-hoc locations only. */
export function locationsYaml(m: SelectionManifest): string {
	const adhoc = new Map<string, ManifestPlate>();
	for (const p of m.plates) if (p.location.kind === 'adhoc' && !adhoc.has(p.locationId)) adhoc.set(p.locationId, p);
	if (adhoc.size === 0) return '# no ad-hoc locations — every plate uses a pool location\n';
	const lines: string[] = ['entries:'];
	for (const p of adhoc.values()) {
		const b = p.location.bbox.map((v) => num(v)).join(', ');
		lines.push(`- id: ${p.locationId}`);
		lines.push(`  name: ${q(p.location.name)}`);
		lines.push(`  bbox: [${b}]`);
		lines.push(`  landscape_character: ${q(p.location.landscapeCharacter)}`);
		lines.push(`  themes: [${p.location.themes.join(', ')}]`);
		lines.push(`  season_windows: [${q(p.dateRange ?? '2023-01-01/2023-12-31')}]`);
		lines.push(`  cloud_cover_max: 10.0`);
		lines.push(`  source: web_picker`);
		lines.push(`  status: approved`);
		lines.push(`  notes: ${q(`Added from the plate picker (${p.scene?.itemId ?? 'no scene'}).`)}`);
	}
	return lines.join('\n') + '\n';
}
