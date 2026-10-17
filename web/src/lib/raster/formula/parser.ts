import type { Expr } from './ast';
import { FormulaSyntaxError, tokenize, type Token } from './tokenizer';

/**
 * Pratt parser with Python precedence:
 *   `+ -` (10) < `* /` (20) < unary minus (30) < `**` (40, right-assoc).
 * So `-x**2` parses as `-(x**2)` and `2**-1` as `2**(-1)`, matching the
 * Python/numpy evaluation spyndex performs.
 */
const BIN_PREC: Record<string, number> = { '+': 10, '-': 10, '*': 20, '/': 20, '**': 40 };
const UNARY_PREC = 30;

class Parser {
	private i = 0;
	constructor(
		private readonly tokens: Token[],
		private readonly source: string
	) {}

	private peek(): Token {
		return this.tokens[this.i];
	}
	private next(): Token {
		return this.tokens[this.i++];
	}
	private fail(msg: string, tok: Token): never {
		throw new FormulaSyntaxError(msg, tok.pos, this.source);
	}

	parse(): Expr {
		const expr = this.parseExpr(0);
		const t = this.peek();
		if (t.kind !== 'eof') this.fail(`Unexpected token`, t);
		return expr;
	}

	private parseExpr(minPrec: number): Expr {
		let left = this.parsePrefix();
		for (;;) {
			const t = this.peek();
			if (t.kind !== 'op') break;
			const prec = BIN_PREC[t.op];
			if (prec < minPrec) break;
			this.next();
			// `**` is right-associative: parse the rhs at the same precedence.
			const rhsMin = t.op === '**' ? prec : prec + 1;
			const right = this.parseExpr(rhsMin);
			left = { type: 'bin', op: t.op, left, right };
		}
		return left;
	}

	private parsePrefix(): Expr {
		const t = this.next();
		switch (t.kind) {
			case 'num':
				return { type: 'num', value: t.value };
			case 'ident':
				return { type: 'var', name: t.name };
			case 'lparen': {
				const inner = this.parseExpr(0);
				const close = this.next();
				if (close.kind !== 'rparen') this.fail('Expected ")"', close);
				return inner;
			}
			case 'op':
				if (t.op === '-') return { type: 'neg', arg: this.parseExpr(UNARY_PREC) };
				if (t.op === '+') return this.parseExpr(UNARY_PREC);
				return this.fail(`Unexpected operator "${t.op}"`, t);
			default:
				return this.fail('Unexpected end of formula', t);
		}
	}
}

const cache = new Map<string, Expr>();

/** Parse a spyndex formula string into an AST (memoised per source string). */
export function parseFormula(source: string): Expr {
	let ast = cache.get(source);
	if (!ast) {
		ast = new Parser(tokenize(source), source).parse();
		cache.set(source, ast);
	}
	return ast;
}
