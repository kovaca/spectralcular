# How a book gets made

A book is a list of scenes. Each scene is one *location × index × colormap*
triple on one satellite image, rendered as a two-page spread: the plate on the
verso, printed to the edge of the paper, and its specimen card on the recto.
Everything between choosing scenes and uploading files to the printer is a
command over YAML in a project folder.

```
pools        config/pool/*.yaml
candidates   config/candidates.yaml
lock         config/spreads/<id>.yaml + spreads.csv
build        output/spreads/<id>.pdf + output/metadata/<id>.json
proof        output/proof.pdf
print        output/print/
```

There are two ways in. The **web picker** composes scenes by hand and exports a
selection that `spectralcular import` turns into approved candidates — the usual
path (see the README). **Curating at scale** generates candidates from the pools
and reviews them as contact sheets (stages 3–4). Both meet at `candidates lock`.
The stage numbers below are the ones the code and CLI help refer to.

## The print specification

Fixed before any rendering, because every render depends on it. The values live
in `config/book_specs.yaml`; derived geometry (document size, AOI ceiling, spine
width) is computed and cross-checked in `src/spectralcular/book_specs.py` — read
those rather than restating numbers.

- **Vendor: Lulu**, print on demand. US Letter portrait (8.5 × 11 in),
  perfect-bound paperback, Premium Color on 80# coated white.
- **Bleed 0.125 in**; plates render to the bleed box (8.75 × 11.25 in).
  Important content stays 0.5 in inside the trim.
- **Plates stay on one page.** A perfect-bound book does not lie flat, so a plate
  across the gutter would lose 10–15 mm; the specimen card faces the plate instead.
- **300 dpi.** One Sentinel-2 10 m pixel per printed dot gives a frame of at most
  26.25 × 33.75 km; wider frames resample. The picker shows the effective dpi as
  you zoom.
- **sRGB, unconverted.** Lulu converts to press CMYK itself. Its `.joboptions`
  presets are the file spec (it names no PDF/X): sRGB, no transparency, images at
  300 ppi as maximum-quality JPEG, fonts embedded. Keep Lulu's template package
  outside the repo; it is not redistributable.
- **Page parity.** Page 1 is a recto. The front matter is 11 pages, so plate 001
  lands on a verso; every spread is exactly two pages, and the overview spread at
  the back opens on a verso. There are no one-page theme dividers — each would
  flip every following spread onto the wrong side. `proof.py` enforces this.
- **Spine** = pages / 444 + 0.06 in, from the real page count at print time.

Before producing a book, order Lulu's paper sample pack and print one spread.
The screen lies about color on coated paper.

## Stages

### 1 · Specification

Edit `config/book_specs.yaml` only to change vendor, trim, paper or binding. A
change invalidates every render.

### 2 · The book

`config/book.yaml` holds the copy: title, author, introduction, cover text.
Credits that follow from the scenes (imagery years, data sources, every colormap
collection and its license) are derived. Scenes are ordered by index theme —
vegetation → water → burn → soil/geology → urban → snow/ice → atmosphere — with
no two adjacent spreads sharing a colormap.

### 3 · Pools

Three YAML pools under `config/pool/`, each entry with a `status:`
(`proposed` / `approved` / `rejected` / `needs_revision`) — the only field the
editorial pass changes. Rejected entries stay as an audit trail.

- **Locations** — name, bbox, themes, season windows, cloud-cover limit.
  Seeded by `tools/generate_locations_pool.py`.
- **Indices** — generated from `spyndex` by `uv run python -m spectralcular.pool_gen`
  and tagged with the platform that can compute them (`platforms.py`):
  Sentinel-2 for optical indices, Landsat 8/9 for thermal, Sentinel-1 RTC for
  radar.
- **Colormaps** — palettize presets plus the vendored colorcurio.us catalog
  (`spectralcular colormaps sync` / `import`), or inline `colors:` stops.

```bash
spectralcular review all                                # output/review/{locations,indices,colormaps}.html
spectralcular review locations --no-fetch               # re-render from cache after editing status:
spectralcular review locations --refresh --only <id>    # re-fetch after changing a bbox or season
```

The contact sheets are a lens on the YAML, never the source of truth. Each
location card shows the clearest full-coverage scene in its season windows and
flags aspect, AOI ceiling, coverage and cloud feasibility.

### 4 · Candidates

```bash
spectralcular candidates generate      # triples from the approved pools
spectralcular candidates review        # output/review/candidates.html
spectralcular candidates order --auto  # book order; --check lints the rhythm
spectralcular candidates lock          # → config/spreads/<id>.yaml + spreads.csv
```

`generate` pairs index and colormap by type (a sequential index gets a
sequential map; a diverging one a diverging map centered on its natural pivot)
and index and location by `expressive_in`, falling back to shared themes. It
merges into `config/candidates.yaml` and never overwrites a `status:` or a note.
The review cards show each plate under the per-index *global* normalization, a
histogram, range coverage, a color-vision-deficiency simulation and a
print-gamut hint. `lock` refuses while anything is still `proposed` or
`needs_revision`; `--force` locks the approved entries regardless.

### 5 · Build

```bash
spectralcular build --skip-existing --keep-going   # output/spreads/<id>.pdf + output/metadata/<id>.json
spectralcular run examples/spreads/spread_amazon_ndvi_viridis.yaml   # one spread from a config
```

Bands are fetched once per location and the index raster once per
`(location, index)`, so colormap variants are cheap. Each spread's JSON sidecar
pins the scene IDs, frame, formula, normalization range, colormap stops and
library versions.

### 6 · Proof and print

```bash
spectralcular proof     # output/proof.pdf: front matter, spreads, overview spread
spectralcular print     # output/print/<title>-interior.pdf, -cover.pdf, preflight.md
```

`print` flattens transparency, resamples every image to 300 ppi JPEG, embeds
fonts, sets trim and bleed boxes, renders the one-piece cover with the spine
sized for the page count, and checks Lulu's rules in `preflight.md`: 32–800
pages, an even count, at most two consecutive blank pages, text at least 7 pt
and inside the safety margin, interior under 500 MB. Upload both files, check
Lulu's own preview, and order one printed proof before anything else.

## Things that bite

- **Normalization changes the message.** A per-image stretch maximizes contrast
  but breaks comparison across the book. Generated candidates share one range per
  index across all its locations; picker scenes pin the range that was on screen.
  The range is printed on every specimen card.
- **Print gamut is unverified.** There is no ICC soft-proof yet, so every
  colormap is `print_safety: unverified`; saturated blues and deep purples are the
  usual losers on paper.
- **Clouds.** Some places have no clean scene in their season; widen the window
  or raise the location's `cloud_cover_max`.
- **Reproducibility.** Scenes and libraries move. Sidecars pin what was used and
  `uv.lock` pins the libraries; keep the band cache of a finished book.
- **Disk.** A 100-scene book needs about 25 GB of cached bands, index rasters and
  plate images, keyed by location rather than by spread.
- **Attribution.** Copernicus Sentinel data and Landsat need credit lines, and
  several colormap collections are CC-BY; the colophon prints all of them.
