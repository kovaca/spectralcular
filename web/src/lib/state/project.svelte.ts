import { browser } from '$app/environment';
import type { Plate, Project } from './schema';
import * as R from './reducers';
import { debounce, loadProject, saveProject, STORAGE_KEY } from './persistence';
import { newId } from './schema';
import { normalizeAdhocIds } from './adhocIds';

/**
 * The persisted project (plates + order). A single class instance holds a
 * `$state` project; every mutation goes through a pure reducer and is written
 * to localStorage after a short debounce. Other tabs are picked up through
 * the `storage` event.
 */
export class ProjectStore {
	project = $state<Project>(null as unknown as Project);
	loadError = $state<string | null>(null);
	quarantinedKey = $state<string | null>(null);
	/** the plate currently loaded in the editor, if any */
	activePlateId = $state<string | null>(null);

	readonly plates = $derived(this.project ? [...this.project.plates].sort((a, b) => a.ordinal - b.ordinal) : []);
	readonly activePlate = $derived(this.plates.find((p) => p.id === this.activePlateId) ?? null);

	private persist = debounce((p: Project) => saveProject(p), 400);
	private muteStorageEvent = false;

	constructor() {
		const res = loadProject(browser ? localStorage : null);
		this.project = res.project;
		this.loadError = res.error ?? null;
		this.quarantinedKey = res.quarantinedKey ?? null;
		if (browser) {
			window.addEventListener('storage', (e) => {
				if (e.key !== STORAGE_KEY || this.muteStorageEvent || !e.newValue) return;
				const other = loadProject(localStorage);
				if (!other.error) this.project = other.project;
			});
			window.addEventListener('beforeunload', () => this.persist.flush());
		}
	}

	private commit(next: Project): void {
		if (next === this.project) return;
		next = normalizeAdhocIds(next);
		this.project = next;
		this.persist(next);
	}

	addPlate(plate: Plate): void {
		this.commit(R.addPlate(this.project, plate));
	}
	updatePlate(id: string, patch: Partial<Plate>): void {
		this.commit(R.updatePlate(this.project, id, patch));
	}
	removePlate(id: string): void {
		if (this.activePlateId === id) this.activePlateId = null;
		this.commit(R.removePlate(this.project, id));
	}
	duplicatePlate(id: string): string {
		const copyId = newId();
		this.commit(R.duplicatePlate(this.project, id, copyId));
		return copyId;
	}
	movePlate(from: number, to: number): void {
		this.commit(R.movePlate(this.project, from, to));
	}
	rename(name: string): void {
		this.commit(R.renameProject(this.project, name));
	}
	/** Replace the whole project (import). */
	replace(project: Project): void {
		this.activePlateId = null;
		this.commit(project);
		this.persist.flush();
	}
	flush(): void {
		this.persist.flush();
	}
}

let store: ProjectStore | null = null;
export function getProjectStore(): ProjectStore {
	if (!store) store = new ProjectStore();
	return store;
}
