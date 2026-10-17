import { describe, expect, it } from 'vitest';
import { sectionSummaries, shortPlatform, type SectionInput } from '../src/lib/ui/sectionStatus';

const base: SectionInput = {
	scene: { date: '2024-07-10', cloudCover: 2.6, satellite: 'sentinel-2a' },
	hasCloud: true,
	trueColor: false,
	trueColorBlocker: null,
	index: { name: 'NDVI', platformLabel: 'Sentinel-2 L2A', gsdM: 10, gpuSupported: true },
	fitNote: null,
	read: { statsStatus: 'done', active: 'Fit this frame', recommended: 'Fit this frame', done: true },
	tone: null,
	colormap: { shortName: 'eclipse', reversed: false, printSafety: 'safe', type: 'sequential', suggestedRamp: 'sequential' },
	caption: ''
};
const by = (s: SectionInput) => Object.fromEntries(sectionSummaries(s).map((x) => [x.id, x]));

describe('sectionSummaries', () => {
	it('summarizes a complete plate', () => {
		const s = by(base);
		expect(s.scene).toMatchObject({ status: 'done', text: '2024-07-10 · 3% cloud', roman: 'I' });
		expect(s.index).toMatchObject({ status: 'done', text: 'NDVI · Sentinel-2 · 10 m' });
		expect(s.read).toMatchObject({ status: 'done', text: 'Fit this frame', title: 'Read' });
		expect(s.colormap).toMatchObject({ status: 'done', text: 'eclipse' });
		expect(s.caption).toMatchObject({ status: 'opt', text: 'default', roman: 'V' });
	});

	it('marks what is still to do without a scene', () => {
		const s = by({ ...base, scene: null });
		expect(s.scene.status).toBe('todo');
		expect(s.read).toMatchObject({ status: 'todo', text: 'needs a scene' });
	});

	it('flags a scene that cannot feed the index in both I and II', () => {
		const s = by({ ...base, fitNote: 'needs Landsat 8/9 C2 L2' });
		expect(s.scene).toMatchObject({ status: 'warn', text: "2024-07-10 · doesn't fit NDVI", hint: 'needs Landsat 8/9 C2 L2' });
		expect(s.index.status).toBe('warn');
	});

	it('names the suggestion while a stretch is not yet kept', () => {
		const s = by({ ...base, read: { statsStatus: 'done', active: 'Fit this frame', recommended: 'Same scale as the book', done: false } });
		expect(s.read).toMatchObject({ status: 'todo', text: 'Fit this frame · suggested: Same scale as the book' });
	});

	it('switches III to Tone for true color', () => {
		const s = by({ ...base, trueColor: true, tone: { preset: 'Natural', edited: true } });
		expect(s.read).toMatchObject({ title: 'Tone', status: 'done', text: 'Natural · edited' });
		expect(s.index.text).toBe('True color');
	});

	it('warns on a ramp type that disagrees with the reading, and on unsafe print', () => {
		expect(by({ ...base, colormap: { ...base.colormap!, type: 'diverging' } }).colormap.status).toBe('warn');
		expect(by({ ...base, colormap: { ...base.colormap!, printSafety: 'unsafe' } }).colormap.status).toBe('warn');
	});

	it('shows reversed ramps and truncates long captions', () => {
		expect(by({ ...base, colormap: { ...base.colormap!, reversed: true } }).colormap.text).toBe('eclipse ⇄');
		const t = by({ ...base, caption: 'Mount Etna in midsummer, the lava fields of 2021 still bare' }).caption.text;
		expect(t.length).toBe(32);
		expect(t.endsWith('…')).toBe(true);
	});

	it('uses the satellite instead of cloud cover for radar', () => {
		expect(by({ ...base, hasCloud: false, scene: { date: '2024-01-02', cloudCover: NaN, satellite: 'sentinel-1a' } }).scene.text).toBe('2024-01-02 · sentinel-1a');
	});
});

describe('shortPlatform', () => {
	it('drops processing-level suffixes', () => {
		expect(shortPlatform('Landsat 8/9 C2 L2')).toBe('Landsat 8/9');
		expect(shortPlatform('Sentinel-2 L2A')).toBe('Sentinel-2');
		expect(shortPlatform('Sentinel-1 RTC')).toBe('Sentinel-1');
	});
});
