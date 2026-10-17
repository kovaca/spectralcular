import type { Expr } from './ast';

/**
 * Evaluate an AST with JS number semantics, which coincide with numpy's for
 * real-valued arrays: `x / 0 → ±Infinity`, `0 / 0 → NaN`, negative base with
 * a fractional exponent → NaN. Missing variables throw.
 */
export function evaluate(expr: Expr, env: Readonly<Record<string, number>>): number {
	switch (expr.type) {
		case 'num':
			return expr.value;
		case 'var': {
			const v = env[expr.name];
			if (v === undefined) throw new Error(`Unbound variable "${expr.name}"`);
			return v;
		}
		case 'neg':
			return -evaluate(expr.arg, env);
		case 'bin': {
			const a = evaluate(expr.left, env);
			const b = evaluate(expr.right, env);
			switch (expr.op) {
				case '+':
					return a + b;
				case '-':
					return a - b;
				case '*':
					return a * b;
				case '/':
					return a / b;
				case '**':
					return a ** b;
			}
		}
	}
}

/**
 * Build a fast evaluator for many pixels: variables are bound to typed-array
 * columns once, then `fn(i)` computes the value for row `i`.
 */
export function compileEvaluator(
	expr: Expr,
	columns: Readonly<Record<string, ArrayLike<number>>>,
	constants: Readonly<Record<string, number>>
): (i: number) => number {
	const build = (e: Expr): ((i: number) => number) => {
		switch (e.type) {
			case 'num': {
				const v = e.value;
				return () => v;
			}
			case 'var': {
				if (e.name in constants) {
					const v = constants[e.name];
					return () => v;
				}
				const col = columns[e.name];
				if (!col) throw new Error(`Unbound variable "${e.name}"`);
				return (i) => col[i];
			}
			case 'neg': {
				const f = build(e.arg);
				return (i) => -f(i);
			}
			case 'bin': {
				const l = build(e.left);
				const r = build(e.right);
				switch (e.op) {
					case '+':
						return (i) => l(i) + r(i);
					case '-':
						return (i) => l(i) - r(i);
					case '*':
						return (i) => l(i) * r(i);
					case '/':
						return (i) => l(i) / r(i);
					case '**':
						return (i) => l(i) ** r(i);
				}
			}
		}
	};
	return build(expr);
}
