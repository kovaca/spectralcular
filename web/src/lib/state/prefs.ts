import { safeStorage } from './persistence';

/**
 * Tiny per-browser flags for onboarding state (intro seen, guide dismissed).
 * Separate from the project so importing or clearing a project never brings the
 * introduction back, and never throws when storage is unavailable.
 */
// `gb:` prefix kept from the original name so existing browsers keep their flags.
export const PREF_INTRO_SEEN = 'gb:pref:intro-seen';
export const PREF_GUIDE_DONE = 'gb:pref:guide-done';

export function getFlag(key: string, storage: Storage | null = safeStorage()): boolean {
	try {
		return storage?.getItem(key) === '1';
	} catch {
		return false;
	}
}

export function setFlag(key: string, value: boolean, storage: Storage | null = safeStorage()): void {
	try {
		if (value) storage?.setItem(key, '1');
		else storage?.removeItem(key);
	} catch {
		/* private mode or quota: the flag is a convenience, not state */
	}
}
