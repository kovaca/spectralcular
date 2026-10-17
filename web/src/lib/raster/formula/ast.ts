/**
 * AST for spyndex index formulas.
 *
 * The grammar used by awesome-spectral-indices is deliberately tiny: numeric
 * literals, identifiers (band params + constants), unary minus, and the binary
 * operators `+ - * / **` with parentheses. Verified against all 102 pool
 * indices — there are no function calls.
 */
export type BinaryOp = '+' | '-' | '*' | '/' | '**';

export type Expr =
	| { type: 'num'; value: number }
	| { type: 'var'; name: string }
	| { type: 'neg'; arg: Expr }
	| { type: 'bin'; op: BinaryOp; left: Expr; right: Expr };

/** Every identifier referenced by the expression, in first-seen order. */
export function freeVariables(expr: Expr, out: string[] = []): string[] {
	switch (expr.type) {
		case 'num':
			return out;
		case 'var':
			if (!out.includes(expr.name)) out.push(expr.name);
			return out;
		case 'neg':
			return freeVariables(expr.arg, out);
		case 'bin':
			freeVariables(expr.left, out);
			return freeVariables(expr.right, out);
	}
}
