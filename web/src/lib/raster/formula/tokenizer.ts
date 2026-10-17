export type Token =
	| { kind: 'num'; value: number; pos: number }
	| { kind: 'ident'; name: string; pos: number }
	| { kind: 'op'; op: '+' | '-' | '*' | '/' | '**'; pos: number }
	| { kind: 'lparen'; pos: number }
	| { kind: 'rparen'; pos: number }
	| { kind: 'eof'; pos: number };

export class FormulaSyntaxError extends Error {
	constructor(
		message: string,
		public readonly pos: number,
		public readonly source: string
	) {
		super(`${message} at position ${pos} in "${source}"`);
		this.name = 'FormulaSyntaxError';
	}
}

const NUM_RE = /^(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?/;
const IDENT_RE = /^[A-Za-z_][A-Za-z0-9_]*/;

export function tokenize(source: string): Token[] {
	const tokens: Token[] = [];
	let i = 0;
	while (i < source.length) {
		const ch = source[i];
		if (ch === ' ' || ch === '\t' || ch === '\n' || ch === '\r') {
			i++;
			continue;
		}
		const rest = source.slice(i);
		const num = NUM_RE.exec(rest);
		if (num) {
			tokens.push({ kind: 'num', value: Number(num[0]), pos: i });
			i += num[0].length;
			continue;
		}
		const ident = IDENT_RE.exec(rest);
		if (ident) {
			tokens.push({ kind: 'ident', name: ident[0], pos: i });
			i += ident[0].length;
			continue;
		}
		if (rest.startsWith('**')) {
			tokens.push({ kind: 'op', op: '**', pos: i });
			i += 2;
			continue;
		}
		if (ch === '+' || ch === '-' || ch === '*' || ch === '/') {
			tokens.push({ kind: 'op', op: ch, pos: i });
			i++;
			continue;
		}
		if (ch === '(') {
			tokens.push({ kind: 'lparen', pos: i });
			i++;
			continue;
		}
		if (ch === ')') {
			tokens.push({ kind: 'rparen', pos: i });
			i++;
			continue;
		}
		throw new FormulaSyntaxError(`Unexpected character "${ch}"`, i, source);
	}
	tokens.push({ kind: 'eof', pos: source.length });
	return tokens;
}
