import { emptyProject, type Project } from './schema';
import { migrate, MigrationError } from './migrations';

// `gb:` = the project's original name (gradient book); kept so saved projects survive the rename.
export const STORAGE_KEY = 'gb:project:v1';
const CORRUPT_PREFIX = 'gb:project:corrupt:';

export interface LoadResult {
	project: Project;
	/** set when stored data could not be read and was quarantined */
	quarantinedKey?: string;
	error?: string;
}

/** Read the project from localStorage; never throws, never silently discards data. */
export function loadProject(storage: Storage | null = safeStorage()): LoadResult {
	if (!storage) return { project: emptyProject() };
	const raw = storage.getItem(STORAGE_KEY);
	if (!raw) return { project: emptyProject() };
	try {
		return { project: migrate(JSON.parse(raw)) };
	} catch (e) {
		const key = `${CORRUPT_PREFIX}${Date.now()}`;
		try {
			storage.setItem(key, raw);
		} catch {
			/* storage full — nothing more we can do */
		}
		return {
			project: emptyProject(),
			quarantinedKey: key,
			error: e instanceof MigrationError ? e.message : String(e)
		};
	}
}

export function saveProject(project: Project, storage: Storage | null = safeStorage()): void {
	if (!storage) return;
	storage.setItem(STORAGE_KEY, JSON.stringify(project));
}

export function safeStorage(): Storage | null {
	try {
		return typeof localStorage !== 'undefined' ? localStorage : null;
	} catch {
		return null;
	}
}

export function serializeProject(project: Project): string {
	return JSON.stringify(project, null, 2);
}

export function parseProjectFile(text: string): Project {
	return migrate(JSON.parse(text));
}

export function debounce<A extends unknown[]>(fn: (...a: A) => void, ms: number): ((...a: A) => void) & { flush(): void } {
	let t: ReturnType<typeof setTimeout> | null = null;
	let last: A | null = null;
	const wrapped = ((...a: A) => {
		last = a;
		if (t) clearTimeout(t);
		t = setTimeout(() => {
			t = null;
			if (last) fn(...last);
			last = null;
		}, ms);
	}) as ((...a: A) => void) & { flush(): void };
	wrapped.flush = () => {
		if (t) {
			clearTimeout(t);
			t = null;
		}
		if (last) fn(...last);
		last = null;
	};
	return wrapped;
}
