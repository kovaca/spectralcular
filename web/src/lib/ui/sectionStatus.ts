/**
 * One line of state per inspector section, for the stacked section heads:
 * a status glyph (done / to do / needs attention / optional) and a short
 * summary of what is set there. Pure: the page passes a plain snapshot of the
 * session, so the tests and the heads read the same rules.
 */

export type SectionStatus = 'done' | 'todo' | 'warn' | 'opt';
export type SectionId = 'scene' | 'index' | 'read' | 'colormap' | 'caption';

export interface SectionSummary {
	id: SectionId;
	roman: string;
	title: string;
	status: SectionStatus;
	text: string;
	/** longer explanation for the head's tooltip */
	hint?: string;
}

export interface SectionInput {
	scene: { date: string; cloudCover: number; satellite: string | null } | null;
	/** the platform reports cloud cover (radar does not) */
	hasCloud: boolean;
	trueColor: boolean;
	/** why true color can't render from this scene, if it can't */
	trueColorBlocker: string | null;
	index: { name: string; platformLabel: string; gsdM: number; gpuSupported: boolean } | null;
	/** the pinned scene can't feed the index: the fit note; null when it fits or nothing to check */
	fitNote: string | null;
	read: {
		statsStatus: 'idle' | 'loading' | 'done' | 'error';
		active: string | null;
		recommended: string | null;
		done: boolean;
	};
	tone: { preset: string; edited: boolean } | null;
	colormap: { shortName: string; reversed: boolean; printSafety: string; type: string; suggestedRamp: string | null } | null;
	caption: string;
}

/** `Landsat 8/9 C2 L2` → `Landsat 8/9`; `Sentinel-2 L2A` → `Sentinel-2`. */
export function shortPlatform(label: string): string {
	return label.replace(/(\s+(L\d\w*|C\d|RTC))+$/, '');
}

const ROMAN = ['I', 'II', 'III', 'IV', 'V'];
const CAPTION_CHARS = 32;

export function sectionSummaries(s: SectionInput): SectionSummary[] {
	return [scene(s), index(s), read(s), colormap(s), caption(s)].map((x, i) => ({ ...x, roman: ROMAN[i] }));
}

type Draft = Omit<SectionSummary, 'roman'>;

function scene(s: SectionInput): Draft {
	const base = { id: 'scene' as const, title: 'Scene' };
	if (!s.scene) return { ...base, status: 'todo', text: 'no scene', hint: 'Search and pin a scene' };
	const detail = s.hasCloud ? `${s.scene.cloudCover.toFixed(0)}% cloud` : (s.scene.satellite ?? '');
	const text = [s.scene.date, detail].filter(Boolean).join(' · ');
	if (s.trueColor && s.trueColorBlocker) return { ...base, status: 'warn', text: `${s.scene.date} · no true color`, hint: s.trueColorBlocker };
	if (!s.trueColor && s.fitNote && s.index) return { ...base, status: 'warn', text: `${s.scene.date} · doesn't fit ${s.index.name}`, hint: s.fitNote };
	return { ...base, status: 'done', text };
}

function index(s: SectionInput): Draft {
	const base = { id: 'index' as const, title: 'Index' };
	if (s.trueColor) return { ...base, status: 'done', text: 'True color' };
	if (!s.index) return { ...base, status: 'todo', text: 'no index' };
	const text = `${s.index.name} · ${shortPlatform(s.index.platformLabel)} · ${s.index.gsdM} m`;
	if (s.fitNote) return { ...base, status: 'warn', text, hint: s.fitNote };
	if (!s.index.gpuSupported) return { ...base, status: 'warn', text, hint: 'No live preview for this formula' };
	return { ...base, status: 'done', text };
}

function read(s: SectionInput): Draft {
	if (s.trueColor) {
		const t = s.tone;
		return { id: 'read', title: 'Tone', status: 'done', text: t ? `${t.preset}${t.edited ? ' · edited' : ''}` : 'default tone' };
	}
	const base = { id: 'read' as const, title: 'Read' };
	if (!s.index) return { ...base, status: 'todo', text: 'pick an index' };
	if (!s.scene) return { ...base, status: 'todo', text: 'needs a scene' };
	if (s.read.statsStatus === 'loading') return { ...base, status: 'todo', text: 'sampling…' };
	if (s.read.statsStatus === 'error') return { ...base, status: 'warn', text: 'stats failed' };
	const active = s.read.active ?? 'custom stretch';
	if (s.read.done) return { ...base, status: 'done', text: active };
	if (s.read.recommended && s.read.recommended !== s.read.active) {
		return { ...base, status: 'todo', text: `${active} · suggested: ${s.read.recommended}`, hint: 'Choose a stretch, or keep this one' };
	}
	return { ...base, status: 'todo', text: active, hint: 'Choose a stretch, or keep this one' };
}

function colormap(s: SectionInput): Draft {
	const base = { id: 'colormap' as const, title: 'Colormap' };
	if (s.trueColor) return { ...base, status: 'done', text: 'true color' };
	const c = s.colormap;
	if (!c) return { ...base, status: 'todo', text: 'no colormap' };
	const text = `${c.shortName}${c.reversed ? ' ⇄' : ''}`;
	if (c.printSafety === 'unsafe') return { ...base, status: 'warn', text, hint: 'Marked unsafe for print' };
	if (c.suggestedRamp && c.suggestedRamp !== c.type) {
		return { ...base, status: 'warn', text, hint: `III Read suggests a ${c.suggestedRamp} ramp; this one is ${c.type}` };
	}
	return { ...base, status: 'done', text };
}

function caption(s: SectionInput): Draft {
	const t = s.caption.trim().replace(/\s+/g, ' ');
	return {
		id: 'caption',
		title: 'Caption',
		status: 'opt',
		text: t ? (t.length > CAPTION_CHARS ? `${t.slice(0, CAPTION_CHARS - 1)}…` : t) : 'default'
	};
}
