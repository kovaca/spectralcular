import { describe, expect, it } from 'vitest';
import { formatLatLon, parseLatLon } from '$lib/geo/coords';
import { hitFromPhoton } from '$lib/geo/geocode';

describe('parseLatLon', () => {
	const close = (text: string, lon: number, lat: number) => {
		const r = parseLatLon(text);
		expect(r, text).not.toBeNull();
		expect(r![0], text).toBeCloseTo(lon, 4);
		expect(r![1], text).toBeCloseTo(lat, 4);
	};

	it('reads decimal pairs latitude first', () => {
		close('37.75, 15.0', 15, 37.75);
		close('37.75 15', 15, 37.75);
		close('-33.92;18.42', 18.42, -33.92);
		close('  -3.1 / -60.0 ', -60, -3.1);
	});

	it('swaps when the first value can only be a longitude', () => {
		close('-122.42, 37.77', -122.42, 37.77);
	});

	it('follows hemisphere letters on either side', () => {
		close('37.75N 15E', 15, 37.75);
		close('N37.75, E15', 15, 37.75);
		close('15.0 E, 37.75 N', 15, 37.75);
		close('33.9S 18.4E', 18.4, -33.9);
		close('122.4 W 37.8 N', -122.4, 37.8);
	});

	it('reads degrees, minutes and seconds', () => {
		close(`37°45'N 14°59'40"E`, 14 + 59 / 60 + 40 / 3600, 37.75);
		close('37 45 0 N, 15 0 0 E', 15, 37.75);
		close(`19°08′S 22°58′E`, 22 + 58 / 60, -(19 + 8 / 60));
	});

	it('rejects text that is not a coordinate pair', () => {
		for (const t of ['', 'etna', '37.75', 'route 66', '95, 200', '37.75N 15N', '40 E 30 W', `37.5°30' 15`, '37°75\' 15']) {
			expect(parseLatLon(t), t).toBeNull();
		}
	});
});

describe('formatLatLon', () => {
	it('labels hemispheres', () => {
		expect(formatLatLon([15, 37.75])).toBe('37.7500° N, 15.0000° E');
		expect(formatLatLon([-60.02, -3.1], 2)).toBe('3.10° S, 60.02° W');
	});
});

describe('hitFromPhoton', () => {
	it('names the place and says where and what it is', () => {
		const h = hitFromPhoton({
			geometry: { coordinates: [22.98, -19.154] },
			properties: { osm_type: 'R', osm_id: 349348, osm_value: 'nature_reserve', name: 'Okavango Delta', state: 'North-West District', country: 'Botswana' }
		});
		expect(h).toEqual({ id: 'R349348', name: 'Okavango Delta', detail: 'North-West District, Botswana', kind: 'nature reserve', center: [22.98, -19.154] });
	});
	it('falls back to the enclosing area for unnamed features and skips repeats in the detail', () => {
		const h = hitFromPhoton({ geometry: { coordinates: [2.35, 48.85] }, properties: { city: 'Paris', state: 'Île-de-France', country: 'France' } });
		expect(h?.name).toBe('Paris');
		expect(h?.detail).toBe('Île-de-France, France');
	});
});
