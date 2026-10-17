# Spectralcular plate picker

Interactive companion to the `spectralcular` pipeline: pick *where* a plate is framed,
*which* Sentinel-2 scene it uses, and *how* it is colored, then export a
manifest the Python build consumes.

## Run

```bash
uv run spectralcular export-web    # from the repo root — regenerates src/lib/data/
pnpm install
pnpm dev                # http://localhost:5173
pnpm test               # vitest: formula compiler, geometry, print maths, state
pnpm check              # svelte-check
pnpm build              # static site in build/ (adapter-static, SPA fallback in 404.html)
BASE_PATH=/spectralcular pnpm build && BASE_PATH=/spectralcular pnpm preview
                        # as deployed to GitHub Pages: http://localhost:4173/spectralcular/
```

`/dev/raster/` is a minimal diagnostic page for the GPU pipeline.

## Deploy

`.github/workflows/pages.yml` builds on every push to `main` that touches `web/`
and publishes to `https://<owner>.github.io/<repo>/`. Once per repository, set
Settings → Pages → Source to "GitHub Actions". The base path comes from
`actions/configure-pages`, so forks and custom domains work unchanged. The app
is entirely client-side: the STAC APIs, the Planetary Computer token endpoint,
Photon and OpenFreeMap all allow any origin and need no keys. Projects live in
localStorage, which is per origin — a project made on localhost does not appear
on the deployed site; move it with Export → project backup.

## How it works

| Layer | What it does |
| --- | --- |
| `src/lib/data/` | JSON written by `spectralcular export-web`: indices (spyndex formulas + constants), colormap LUT sprite (256 stops, same sampling as `colormaps.py`), location pool, book specs. The app never defines an index or colormap itself. |
| `src/lib/raster/formula/` | Parses spyndex formulas (`+ - * / **`), evaluates them on the CPU (stats) and emits GLSL (preview). Fixture-tested against `spyndex.computeIndex`. |
| `src/lib/raster/gpu/` | `PlateLayer` (built by `buildLayer.ts`) wraps deck.gl-geotiff's `MultiCOGLayer` through `BandStackCOGLayer` (8 band samplers + a mask texture); a generated luma.gl shader module computes the index, masks nodata + SCL (nearest-neighbour texel read), normalizes and looks up the colormap LUT. Invalid pixels are transparent. |
| `src/lib/raster/stats/` | Web Worker reading the band COGs at ~80 m with geotiff.js inside the rotated frame → percentiles, histogram, valid/masked fractions (mirrors the Python review path). |
| `src/lib/geo/` | Frame model `{center, widthM, rotationDeg}` on a local tangent plane — identical formulas to `framing.FrameSpec` in Python. |
| `src/lib/book/` | Plate geometry from `book.json`: native pixels, effective dpi, AOI ceiling. |
| `src/lib/data/reading.ts` | III Read: typical range (guide → closed formula bounds → reference-surface span), meaningful center, frame composition by guide class, ramp use / clipping, the three stretch options with a recommendation, and notes. Pure; `tests/reading.test.ts`. |
| `src/lib/state/` | zod schema + migrations, pure reducers, `ProjectStore` (persisted, debounced, multi-tab) and `Session` (editor draft). Corrupt storage is quarantined, never discarded. |
| `src/lib/export/` | `spectralcular.selection` v2 manifest (the Python importer also reads v1 and pre-rename `gradient-book.selection` files), `candidates.yaml` / `locations.yaml` snippets, project backup. |

First run: an introduction dialog (also under the `?` button / `?` key) and a
step-by-step guide on the map stage; `Open a worked example` frames Mt. Etna and
pins the clearest Sentinel-2 scene. Both are remembered per browser
(`gb:pref:*` keys in localStorage, separate from the project).

New frames start at the index's full-resolution width (300 dpi from native
pixels; 26.25 km across at 10 m). The place search filters the book pool and,
from three characters, also geocodes place names worldwide through
[Photon](https://photon.komoot.io) (OpenStreetMap; settlements, regions and
natural features only). Typed coordinates (`37.75, 15`, `37°45'N 15°E`) frame
that point; Enter picks the first match.

The rail's **Overview** tab shows the whole book: a locator map of its places, a
timeline of scene dates with a month strip, the index × colormap combinations
(repeats flagged), and every index and colormap in the catalog as a compact grid
showing which are used and how often. Click anything to focus it; the matching
plates are listed (open, show on map) and an unused index or colormap can be tried
on the current plate. The inspector's section heads stay stacked at the top and
bottom of the panel with a status and summary each; click one to jump there.

Keyboard: `[` `]` step through places · `a` align view to page · `n` north up ·
`f` zoom to frame · `r` reverse colormap · `?` introduction · ⌘S save plate · ⌘E export.

## Conventions

* Digital numbers become physical values with the per-asset `[scale, offset]`
  in `platforms.json` (Sentinel-2: DN / 10000 with no offset, matching
  `indices.py`; the Earth Search `raster:bands` offset is deliberately not
  applied. Landsat: `DN × 0.0000275 − 0.2` for reflectance, `DN × 0.00341802
  + 149` kelvin for thermal. Sentinel-1 RTC: linear gamma0 as stored).
* The index picked determines the imagery platform; the scene search follows
  it. Planetary Computer assets are signed with a per-collection SAS token
  (`src/lib/stac/sas.ts`) at render time; saved plates keep unsigned hrefs.
* The GPU band stack (`src/lib/raster/gpu/bandStack.ts`) binds up to eight
  band textures plus a dedicated quality-mask texture (Sentinel-2 SCL classes
  or Landsat QA_PIXEL bits), so every index with ≤ 8 distinct assets previews
  live. float32 rasters (Sentinel-1) are quantized to 16 bits up to the
  platform's `valueCeiling`.
* Frame rotation is the compass bearing of the page top; the map's
  "Align view to page" sets the bearing to that value.
* The manifest pins the *resolved* normalization as `fixed vmin/vmax` so the
  build reproduces what was on screen.
