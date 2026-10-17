import type { PlatformDef } from '$lib/data/types';

/** Planetary Computer's token endpoint: `GET /token/<collection>` → `{ token, "msft:expiry" }`. */
export const PLANETARY_COMPUTER_SAS_URL = 'https://planetarycomputer.microsoft.com/api/sas/v1/token';

/** Refresh this long before a token expires so in-flight tile requests never race the expiry. */
const REFRESH_MARGIN_MS = 60_000;

interface SasToken {
	token: string;
	/** epoch ms */
	expiry: number;
}

const cache = new Map<string, Promise<SasToken>>();

/** Does this platform's storage need a SAS token appended to asset hrefs? */
export function needsSigning(platform: PlatformDef): boolean {
	return platform.signing === 'planetary-computer';
}

/**
 * A valid SAS token for `collection`, fetched once and reused until shortly
 * before it expires. Tokens are per collection, not per asset.
 */
export async function getSasToken(collection: string, fetchFn: typeof fetch = fetch): Promise<string> {
	const cached = cache.get(collection);
	if (cached) {
		try {
			const t = await cached;
			if (t.expiry - Date.now() > REFRESH_MARGIN_MS) return t.token;
		} catch {
			// fall through and refetch
		}
		cache.delete(collection);
	}
	const p = (async (): Promise<SasToken> => {
		const res = await fetchFn(`${PLANETARY_COMPUTER_SAS_URL}/${encodeURIComponent(collection)}`);
		if (!res.ok) throw new Error(`Planetary Computer token for ${collection} failed: ${res.status}`);
		const json = (await res.json()) as { token: string; 'msft:expiry': string };
		const expiry = Date.parse(json['msft:expiry']);
		return { token: json.token, expiry: Number.isFinite(expiry) ? expiry : Date.now() + 30 * 60_000 };
	})();
	cache.set(collection, p);
	try {
		return (await p).token;
	} catch (e) {
		cache.delete(collection);
		throw e;
	}
}

/** Append a SAS token to a blob href that doesn't already carry a query string. */
export function appendToken(href: string, token: string): string {
	return href.includes('?') ? href : `${href}?${token}`;
}

/** Sign every href in the map for `platform` (identity for public platforms). */
export async function signHrefs(hrefs: Record<string, string>, platform: PlatformDef): Promise<Record<string, string>> {
	if (!needsSigning(platform)) return hrefs;
	const token = await getSasToken(platform.collection);
	return Object.fromEntries(Object.entries(hrefs).map(([k, v]) => [k, appendToken(v, token)]));
}

/** Test hook. */
export function clearSasCache(): void {
	cache.clear();
}
