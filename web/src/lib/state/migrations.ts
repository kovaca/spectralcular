import { normalizeAdhocIds } from './adhocIds';
import { ProjectSchema, SCHEMA_VERSION, type Project } from './schema';

type Step = (raw: Record<string, unknown>) => Record<string, unknown>;

/** version n → n+1 transforms. Add one entry per schema bump. */
const STEPS: Record<number, Step> = {
	// v2: plates gain a kind (index | truecolor), a true-color tone and RGB stats.
	1: (raw) => ({
		...raw,
		schemaVersion: 2,
		plates: Array.isArray(raw.plates)
			? raw.plates.map((p) => ({ kind: 'index', truecolor: null, rgbStats: null, ...(p as Record<string, unknown>) }))
			: raw.plates
	})
};

export class MigrationError extends Error {}

/** Bring any stored project up to the current schema and validate it. */
export function migrate(raw: unknown): Project {
	if (!raw || typeof raw !== 'object') throw new MigrationError('project is not an object');
	let doc = raw as Record<string, unknown>;
	let v = typeof doc.schemaVersion === 'number' ? doc.schemaVersion : 0;
	if (v === 0) throw new MigrationError('project has no schemaVersion');
	while (v < SCHEMA_VERSION) {
		const step = STEPS[v];
		if (!step) throw new MigrationError(`no migration from schema v${v}`);
		doc = step(doc);
		v = doc.schemaVersion as number;
	}
	if (v > SCHEMA_VERSION) throw new MigrationError(`project schema v${v} is newer than this app (v${SCHEMA_VERSION})`);
	const parsed = ProjectSchema.safeParse(doc);
	if (!parsed.success) throw new MigrationError(`invalid project: ${parsed.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`).join('; ')}`);
	// Older saves can hold several places under one ad-hoc id (all "new_location").
	return normalizeAdhocIds(parsed.data);
}
