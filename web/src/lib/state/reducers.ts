import type { Plate, Project } from './schema';

/** Pure project transforms — the store calls these; tests exercise them directly. */

function touch(p: Project): Project {
	return { ...p, updatedAt: new Date().toISOString() };
}

/** Re-number ordinals 1..n following array order. */
export function renumber(plates: Plate[]): Plate[] {
	return plates.map((pl, i) => (pl.ordinal === i + 1 ? pl : { ...pl, ordinal: i + 1 }));
}

export function addPlate(p: Project, plate: Plate): Project {
	return touch({ ...p, plates: renumber([...p.plates, plate]) });
}

export function updatePlate(p: Project, id: string, patch: Partial<Plate>): Project {
	const now = new Date().toISOString();
	return touch({
		...p,
		plates: p.plates.map((pl) => (pl.id === id ? { ...pl, ...patch, id: pl.id, updatedAt: now } : pl))
	});
}

export function removePlate(p: Project, id: string): Project {
	return touch({ ...p, plates: renumber(p.plates.filter((pl) => pl.id !== id)) });
}

export function duplicatePlate(p: Project, id: string, newId: string): Project {
	const i = p.plates.findIndex((pl) => pl.id === id);
	if (i < 0) return p;
	const now = new Date().toISOString();
	const copy: Plate = { ...p.plates[i], id: newId, createdAt: now, updatedAt: now };
	const plates = [...p.plates.slice(0, i + 1), copy, ...p.plates.slice(i + 1)];
	return touch({ ...p, plates: renumber(plates) });
}

/** Move the plate at `from` to position `to` (indices into the ordered array). */
export function movePlate(p: Project, from: number, to: number): Project {
	if (from === to || from < 0 || to < 0 || from >= p.plates.length || to >= p.plates.length) return p;
	const plates = [...p.plates];
	const [moved] = plates.splice(from, 1);
	plates.splice(to, 0, moved);
	return touch({ ...p, plates: renumber(plates) });
}

export function renameProject(p: Project, name: string): Project {
	return touch({ ...p, name });
}
