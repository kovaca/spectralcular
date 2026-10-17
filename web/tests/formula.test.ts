import { describe, expect, it } from 'vitest';
import { parseFormula, evaluate, toGlsl, freeVariables, tokenize, planSlots, compileEvaluator } from '../src/lib/raster/formula';
import indicesJson from '../src/lib/data/indices.json';
import fixturesJson from '../src/lib/data/index_fixtures.json';
import type { IndexDef, IndexFixturesFile, IndicesFile } from '../src/lib/data/types';

const indices = (indicesJson as unknown as IndicesFile).entries as IndexDef[];
const fixtures = (fixturesJson as unknown as IndexFixturesFile).fixtures;

describe('tokenizer', () => {
	it('tokenizes operators, numbers and identifiers', () => {
		const kinds = tokenize('2.5*(N-R)/(N+6.0*R-7.5*B+1.0)**2').map((t) => t.kind);
		expect(kinds).toContain('op');
		expect(kinds.filter((k) => k === 'num')).toHaveLength(5);
	});
	it('rejects unknown characters', () => {
		expect(() => tokenize('N ^ R')).toThrow(/Unexpected character/);
	});
});

describe('parser semantics (Python precedence)', () => {
	it('unary minus binds looser than **', () => {
		expect(evaluate(parseFormula('-2**2'), {})).toBe(-4);
	});
	it('** is right associative', () => {
		expect(evaluate(parseFormula('2**3**2'), {})).toBe(512);
	});
	it('allows unary minus in the exponent', () => {
		expect(evaluate(parseFormula('2**-1'), {})).toBe(0.5);
	});
	it('left-associates - and /', () => {
		expect(evaluate(parseFormula('8-4-2'), {})).toBe(2);
		expect(evaluate(parseFormula('8/4/2'), {})).toBe(1);
	});
	it('reports syntax errors with position', () => {
		expect(() => parseFormula('(N-R')).toThrow(/Expected "\)"/);
	});
});

describe('all pool indices', () => {
	it('parse', () => {
		for (const d of indices) expect(() => parseFormula(d.formula), d.name).not.toThrow();
	});
	it('reference only known bands and constants', () => {
		for (const d of indices) {
			const vars = freeVariables(parseFormula(d.formula));
			for (const v of vars) {
				const known = d.params.includes(v) || v in d.constants;
				expect(known, `${d.name}: ${v}`).toBe(true);
			}
		}
	});
	it('match the spyndex fixtures to 1e-6', () => {
		let checked = 0;
		for (const d of indices) {
			const cases = fixtures[d.name];
			if (!cases) continue;
			const ast = parseFormula(d.formula);
			const consts = Object.fromEntries(
				Object.entries(d.constants).filter(([, v]) => v !== null) as [string, number][]
			);
			for (const c of cases) {
				const got = evaluate(ast, { ...consts, ...c.bands });
				if (c.value === null) {
					expect(Number.isFinite(got), `${d.name} expected non-finite`).toBe(false);
				} else {
					expect(Math.abs(got - c.value), `${d.name} ${JSON.stringify(c.bands)}`).toBeLessThan(1e-6);
				}
				checked++;
			}
		}
		expect(checked).toBeGreaterThan(400);
	});
	it('emit GLSL for every GPU-supported index', () => {
		let supported = 0;
		for (const d of indices) {
			const plan = planSlots(d);
			if (!plan.gpuSupported) continue;
			supported++;
			const consts = Object.fromEntries(Object.keys(d.constants).map((k, i) => [k, `M.k0.${'xyzw'[i % 4]}`]));
			const bands = Object.fromEntries(Object.entries(plan.paramSlot).map(([p, s]) => [p, `b${s}`]));
			const glsl = toGlsl(parseFormula(d.formula), { bands, constants: consts });
			expect(glsl).toMatch(/\bb[0-7]\b/);
			// every literal must be a float literal
			for (const m of glsl.matchAll(/(?<![\w.])(\d+)(?![\w.])/g)) {
				throw new Error(`${d.name}: bare integer literal ${m[1]} in ${glsl}`);
			}
		}
		// Every computable index with ≤ 8 distinct assets renders on the GPU.
		const expected = indices.filter((d) => d.computable && new Set(Object.values(d.bands)).size <= 8).length;
		expect(supported).toBe(expected);
	});
});

describe('slot planning', () => {
	const byName = new Map(indices.map((d) => [d.name, d]));
	const withAssets = (n: number) => indices.find((d) => d.computable && new Set(Object.values(d.bands)).size === n);
	it('assigns slots in param order and binds the platform mask separately', () => {
		const p = planSlots(byName.get('NDVI')!);
		expect(p.slotAssets).toEqual(['nir', 'red']);
		expect(p.paramSlot).toEqual({ N: 0, R: 1 });
		expect(p.maskAsset).toBe('scl');
		expect(p.sources).toEqual(['nir', 'red', 'scl']);
		expect(planSlots(byName.get('NDVI')!, { wantMask: false }).maskAsset).toBeNull();
	});
	it('shares a slot between params that point at the same asset', () => {
		const ndvit = byName.get('NDVIT');
		if (!ndvit) return;
		const p = planSlots(ndvit);
		expect(p.slotAssets).toEqual(['nir08', 'red', 'lwir11']);
		expect(p.maskAsset).toBe('qa_pixel');
	});
	it('keeps the mask for 4-, 5-, 6-band indices', () => {
		for (const n of [4, 5, 6]) {
			const d = withAssets(n);
			expect(d, `no ${n}-asset index in pool`).toBeDefined();
			const p = planSlots(d!);
			expect(p.gpuSupported, d!.name).toBe(true);
			expect(p.slotAssets).toHaveLength(n);
			expect(p.maskAsset).toBe('scl');
		}
	});
	it('rejects more than 8 assets', () => {
		const nine = { ...byName.get('NDVI')!, params: 'ABCDEFGHI'.split(''), bands: Object.fromEntries('ABCDEFGHI'.split('').map((c) => [c, `asset-${c}`])) };
		const p = planSlots(nine);
		expect(p.gpuSupported).toBe(false);
		expect(p.reason).toMatch(/9 bands exceed/);
	});
	it('radar indices have no mask', () => {
		const ndpoli = byName.get('NDPolI');
		if (!ndpoli) return;
		const p = planSlots(ndpoli);
		expect(p.gpuSupported).toBe(true);
		expect(p.maskAsset).toBeNull();
		expect(p.sources).toEqual(['vv', 'vh']);
	});
	it('ANDWI emits one band variable per slot', () => {
		const d = byName.get('ANDWI')!;
		const p = planSlots(d);
		const bands = Object.fromEntries(Object.entries(p.paramSlot).map(([k, s]) => [k, `b${s}`]));
		const glsl = toGlsl(parseFormula(d.formula), { bands, constants: {} });
		expect(new Set(glsl.match(/\bb[0-7]\b/g))).toEqual(new Set(['b0', 'b1', 'b2', 'b3', 'b4', 'b5']));
		expect(glsl).not.toContain('refl.');
	});
});

describe('GLSL emission', () => {
	it('NDVI', () => {
		const glsl = toGlsl(parseFormula('(N - R)/(N + R)'), { bands: { N: 'b0', R: 'b1' }, constants: {} });
		expect(glsl).toBe('gb_div((b0 - b1), (b0 + b1))');
	});
	it('floats literals and pow', () => {
		const glsl = toGlsl(parseFormula('2.5*(N-R)**2 + 1'), { bands: { N: 'b0', R: 'b1' }, constants: {} });
		expect(glsl).toBe('((2.5 * gb_pow((b0 - b1), 2.0)) + 1.0)');
	});
	it('constants resolve to uniforms', () => {
		const glsl = toGlsl(parseFormula('g*(N-R)/(N+C1*R-C2*B+L)'), {
			bands: { N: 'b0', R: 'b1', B: 'b2' },
			constants: { g: 'M.k0.x', C1: 'M.k0.y', C2: 'M.k0.z', L: 'M.k0.w' }
		});
		expect(glsl).toContain('M.k0.x');
		expect(glsl).toContain('M.k0.w');
	});
});

describe('compiled evaluator', () => {
	it('matches evaluate() over columns', () => {
		const ast = parseFormula('g*(N-R)/(N+C1*R-C2*B+L)');
		const cols = { N: new Float32Array([0.5, 0.3]), R: new Float32Array([0.1, 0.2]), B: new Float32Array([0.05, 0.1]) };
		const consts = { g: 2.5, C1: 6, C2: 7.5, L: 1 };
		const fn = compileEvaluator(ast, cols, consts);
		for (let i = 0; i < 2; i++) {
			const ref = evaluate(ast, { ...consts, N: cols.N[i], R: cols.R[i], B: cols.B[i] });
			expect(fn(i)).toBeCloseTo(ref, 6);
		}
	});
});
