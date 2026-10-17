import type { Expr } from './ast';

/** Where each identifier lives in the fragment shader. */
export interface GlslBindings {
	/** band param → GLSL expression holding its physical value (e.g. `b0`) */
	bands: Readonly<Record<string, string>>;
	/** constant → GLSL expression (e.g. `M.k0.x`) */
	constants: Readonly<Record<string, string>>;
	/** prefix for the helper functions (`gb_div`, `gb_pow`) */
	helperPrefix?: string;
}

function lit(n: number): string {
	if (!Number.isFinite(n)) throw new Error(`Non-finite literal ${n}`);
	const s = n.toString();
	// GLSL needs an explicit float: `1` → `1.0`, `1e-6` stays valid, `.5` → `0.5`.
	if (/e/i.test(s)) return s.includes('.') ? s : s.replace(/e/i, '.0e');
	if (s.includes('.')) return s.startsWith('.') ? `0${s}` : s.startsWith('-.') ? `-0${s.slice(1)}` : s;
	return `${s}.0`;
}

/**
 * Emit a GLSL expression for `expr`. Division and exponentiation route
 * through helpers so the shader can flag numpy-style invalid results (GLSL
 * `pow(x, y)` is undefined for `x < 0`, while numpy handles integer
 * exponents of negative bases just fine and returns NaN otherwise).
 */
export function toGlsl(expr: Expr, b: GlslBindings): string {
	const p = b.helperPrefix ?? 'gb';
	const go = (e: Expr): string => {
		switch (e.type) {
			case 'num':
				return lit(e.value);
			case 'var': {
				const band = b.bands[e.name];
				if (band) return band;
				const k = b.constants[e.name];
				if (k) return k;
				throw new Error(`Unbound variable "${e.name}" in GLSL emission`);
			}
			case 'neg':
				return `(-${go(e.arg)})`;
			case 'bin':
				switch (e.op) {
					case '+':
						return `(${go(e.left)} + ${go(e.right)})`;
					case '-':
						return `(${go(e.left)} - ${go(e.right)})`;
					case '*':
						return `(${go(e.left)} * ${go(e.right)})`;
					case '/':
						return `${p}_div(${go(e.left)}, ${go(e.right)})`;
					case '**':
						return `${p}_pow(${go(e.left)}, ${go(e.right)})`;
				}
		}
	};
	return go(expr);
}

/** GLSL source for the `_div` / `_pow` helpers plus the invalid flag. */
export function glslHelpers(prefix = 'gb'): string {
	return /* glsl */ `
bool ${prefix}_bad = false;
float ${prefix}_div(float a, float b) {
  if (b == 0.0) { ${prefix}_bad = true; return 0.0; }
  return a / b;
}
float ${prefix}_pow(float a, float b) {
  if (b == floor(b)) {
    // Integer exponent: sign-aware, defined for negative bases like numpy.
    float p = pow(abs(a), abs(b));
    if (b < 0.0) { if (p == 0.0) { ${prefix}_bad = true; return 0.0; } p = 1.0 / p; }
    bool odd = mod(abs(b), 2.0) == 1.0;
    return (a < 0.0 && odd) ? -p : p;
  }
  if (a < 0.0) { ${prefix}_bad = true; return 0.0; }
  return pow(a, b);
}
`;
}
