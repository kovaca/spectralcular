# Design conventions — Spectralcular (plate picker and printed spread)

The visual language shared by the web picker and the printed book. Change a token
here and in both `web/src/app.css` and `src/spectralcular/templates/book.css`.

## Context
- **What this covers:** the SvelteKit plate picker in `web/` and the two-page book
  spread rendered by `src/spectralcular/book.py` + `templates/book.css`. Front
  matter (`front_matter.html.j2`), the overview spread and the cover
  (`cover.html.j2`) use the same tokens. There are no section dividers (they
  would flip the verso/recto parity of every later spread).
- **The job it does:** in the picker, compose one plate with confidence (place, scene,
  frame, index, colormap, range) and keep an ordered list of plates. On the spread,
  let the plate breathe on the verso and state exactly how it was made on the recto,
  in a form that reads the same on every spread.
- **Audience:** the editor curating the book today; later, cartographers and
  remote-sensing people composing their own atlas from the same picker.

## Chosen direction: "Atlas paper"
Warm paper, a humanist serif, small mono labels, roman-numeral sections and one
red-brown accent — the book's print language, used for the picker too, so screen
and page read as one publication. The picker's rail keeps a **ledger of plates with
previews**; the colormap selector shows a **big band with named alternates**; a
**band ruler** lights the bands a formula uses. The specimen card sets imagery,
index and colormap as **three ruled columns** above the true-color thumbnail and
the scene locator.

## Color tokens
Shared by the web app and the print stylesheet. Names match `templates/book.css`
where they already exist.

| Token | Hex | Role / when to use |
|-------|-----|--------------------|
| `--paper` | `#f7f4ec` | page and app background |
| `--paper-2` | `#fbf9f4` | raised/selected surface: selected ledger row, empty matrix cell |
| `--paper-edge` | `#efeae0` | map stage ground, locator background, image placeholders |
| `--ink` | `#1a1a1a` | primary text, section labels, strong rules |
| `--ink-soft` | `#4a4845` | secondary text, running head, meta strings |
| `--ink-mute` | `#7a7771` | tertiary text, kv keys, axis labels, tick marks |
| `--rule` | `#cfcdc7` | hairline rules (0.4–0.5 pt print, 0.5–1 px screen), borders |
| `--rule-soft` | `#e2dfd7` | dotted row separators, very light dividers |
| `--accent` | `#9a3324` | roman numerals, plate number, formula box, lit bands, selected state, "unsaved" |
| `--ok` | `#4f7a3a` | effective dpi at or above target, approved status |
| `--warn` | `#b07a1a` | dpi below target, needs-revision status |
| `--bad` | `#b3261e` | errors, rejected status (distinct from accent by weight and context) |

Rules: color in the UI comes from the imagery and the colormap band. The accent is
used for at most one element per panel or section. No tints, gradients or shadows
other than the frame's drop shadow on the map stage (`0 12px 30px rgba(26,26,26,.25)`).

## Typography
| Role | Typeface | Weights | Notes |
|------|----------|---------|-------|
| Display / headings / body | `"Iowan Old Style", "Charter", "Hoefler Text", Georgia, serif` | 400, italic 400 | titles 15 pt print / 17–20 px screen; body 8.5–9 pt print / 13.5 px screen; `font-feature-settings: "kern" 1, "liga" 1, "onum" 1` |
| Labels, data, buttons | `"Berkeley Mono", "JetBrains Mono", ui-monospace, Menlo, monospace` | 400, 500, 700 | 6.5–7.5 pt print / 9–11 px screen; labels uppercase with 0.10–0.22 em tracking; values tabular (`"tnum" 1`) |

- **Type scale (screen):** 9 / 10 / 10.5 / 11 / 12 / 13.5 / 17 / 20 px. Mono never
  above 11 px; serif never below 12 px.
- **Type scale (print):** running head 6.5 pt, kv keys 6.5 pt, kv values 7.5 pt,
  section labels 6.8 pt, lede-size body 8.5 pt, subtitle 10 pt, title 15 pt.
- **Where fonts come from:** the book build uses fonts installed on the build machine
  (Iowan Old Style ships with macOS; Charter is the Linux/CI fallback and is free to
  bundle). The web app should self-host **Charter** (Bitstream, free license) and
  **JetBrains Mono** in `web/static/fonts/` so the picker does not depend on the
  viewer's OS. Do not load Google Fonts in the picker.
- **Label convention:** mono, uppercase, tracked labels are the one place uppercase is
  allowed. Roman numerals (`I`, `II`, `III`, `IV`) in the accent color prefix section
  labels in both the inspector and the diagnostic page; they encode section order and
  are not decorative.

## Layout & spacing

### Plate picker (`web/`)
- **App grid:** `grid-template-columns: 380px 1fr 320px; grid-template-rows: 50px 1fr`
  at ≥ 1280 px. Areas: top bar / rail / stage / inspector.
- **Top bar:** serif project name 20 px; mono state string
  `PLATE 004 · MT. ETNA · NDVI · ECLIPSE · UNSAVED` with `UNSAVED` in accent; buttons
  right-aligned.
- **Rail (plates ledger):** two tabs `PLACES · 248` / `PLATES · 4` (mono, active tab
  underlined 1.5 px accent). Plates are grouped by theme with a mono theme header and
  count. Each row: 44 × 56 px preview thumbnail (rendered index plate), a mono
  coordinate line `L006 · I012 · C118` with the colormap id in accent, the place name
  in serif 13.5 px, and a mono line `NDVI ▬▬ eclipse` with a 34 × 7 px band swatch;
  plate ordinal right-aligned. Selected row: `--paper-2` background and a 2 px accent
  inset left edge. Last row of the current theme is an "add" row in italic serif with a
  dashed thumbnail. Keyboard hints sit at the rail's bottom in mono.
- **Rail (overview):** a third tab, `OVERVIEW`, shows the book at a glance
  (`web/src/lib/ui/BookOverview.svelte`, numbers from `state/overview.ts`). Top to
  bottom: a mono tally (`7 plates · 6 places · 5 indices · 4 colormaps · 2021–2024`,
  plus the platform mix when the book uses more than one); a 180 px locator map
  (native MapLibre layers, no deck.gl) with one theme-coloured dot per place, sized by
  plate count, and an accent ring at the current draft; a scene-date track (year axis,
  one theme-coloured mark per plate, crowded marks stack into up to four rows) over a
  12-cell month strip; the index × colormap combinations with `×n` (repeats in warn
  ochre); then **catalog coverage**: every index as a 10 px cell in theme rows
  (paper = unused, accent tints for 1 / 2 / 3+ plates, ink outline = the plate being
  edited) and every GPU ramp as a 5 × 16 px vertical gradient bar in collection rows
  (unused at 20 % opacity, used at full strength with an accent underline). An
  `all · used only` seg control collapses the catalogs. Clicking any dot, mark, cell,
  bar or combination **focuses** it: the other views and the stage map's plate dots
  dim what doesn't match, and a card at the foot of the tab lists the matching plates
  (click to open), with `Show on map` and, for an index or colormap,
  `Use for this plate`. Esc or ✕ clears the focus. While the tab is open the stage
  map draws every saved plate as a theme dot with an accent ring and a mono ordinal;
  the place-pool dots recede to 35 %.
- **Stage:** `--paper-edge` ground with a 50 px graticule at 5 % ink; the frame shows
  the rendered index preview with a 0.5 px white trim line 9 px inside the bleed edge
  and 8 px square handles (paper fill, 0.5 px ink border). Tools at top-left as a
  mono button strip; the active tool in accent. Print readout bottom-left as a
  2 × 2 mono grid under a 0.5 px ink rule: Ground, Native pixels, Effective dpi,
  Rotation.
- **Inspector:** sections `I SCENE`, `II INDEX`, `III READ`, `IV COLORMAP`, `V CAPTION`,
  each under a **stacked sticky head** (`InspectorSection.svelte`): all five heads stay
  in view, the passed ones stacked at the top of the panel and the later ones at the
  bottom, each `--sec-h` (28 px; 34 px on touch) tall. A head reads, left to right, a
  status glyph (● set · ○ to do · ▲ needs attention in warn ochre · dashed ○ optional),
  the roman numeral in accent, the mono label, and a right-aligned one-line summary of
  what is set (`2024-07-10 · 3% cloud`, `NDVI · Sentinel-2 · 10 m`,
  `Fit this frame · suggested: Book-wide range`, a 36 px swatch + `eclipse ⇄`, the
  caption's first words). Rules live in `ui/sectionStatus.ts`. Clicking a head scrolls
  its section to just under the stack; the current one carries a 2 px accent inset
  edge. The last section is at least a panel tall so every section can reach the top.
  Sections then hold a dotted kv list. Index section
  holds the band ruler above the index chips. Read section: the guide's one-line
  "measures" in serif, `HIGH` / `LOW` mono labels, then the value ruler (numbered class
  strip in paper tints, the frame histogram with each bar in the color it prints as, a
  7 px ramp strip with the clamped ends at 35 %, dashed split line, reference-surface
  ticks with mono 8.5 px labels in up to three rows), the class shares list (swatch =
  how the band prints), stretch options as ruled cards (the active one with an inset
  2 px accent rule, `SUGGESTED` in accent), notes (▲ in warn ochre), and `Fine-tune`
  (method seg, percentile presets, fixed ends, mask) behind a disclosure. Colormap
  section: name in serif 16 px, meta `L 0 → 95 · CVD SAFE`, a 16 px band, then a
  4-column grid of alternates (9 px band + mono name), the current one outlined 1 px
  accent.
- **Spacing scale:** 2 / 4 / 6 / 8 / 10 / 12 / 14 / 16 px. Section padding 12 × 16 px.
- **Radius / elevation:** 0 everywhere except the frame handles. Rules instead of
  shadows.

### Book spread (`templates/book.css`)
Verso is unchanged: full-bleed plate with four mono overlays (top-left index ·
colormap, top-right place in bold, bottom-left italic serif caption, bottom-right
`PLATE 004`), halo behind text as today.

Recto (diagnostic page), 36 mm horizontal padding, 252 mm content height, fixed
vertical budget so every recto in the book aligns:

The page reads as two halves. The **top half** identifies the plate: title, formula,
place, the two pictures that locate it, and the bands that made it. The **bottom
half** is the specimen record in the form of "Daylight press room": three fact
columns, the gradient band with its curves, and the histogram.

| Block | Height | Content |
|-------|--------|---------|
| Running head | 5 mm | `NDVI · ECLIPSE` left, `PLATE 004` center, place right in bold |
| Title block | 24 mm | centerd serif title 15 pt, accent-bordered mono formula box, italic subtitle (place) |
| Two-column feature | 46 mm | left: true-color preview of the framed area; right: scene locator (tile footprints dashed in ink, page frame in accent, lat/lon ticks in mono). Equal widths, 4 mm gutter, 0.4 pt rule around each, mono caption under each (`True color · Sentinel-2A · 16 July 2023`, `Scene footprints 33SVB · 33SWB and page frame`) |
| Bands in use | 19 mm | ruled block: label left, `B04 red 665 nm · B08 near-infrared 842 nm` right; wavelength ruler 400–2300 nm (inline SVG from `band_ruler.py`, band table in `platforms.py`), ticks for every sensor band, used bands as accent ticks with bold labels; visible range shown as a thin spectrum on the baseline; other labels dropped where they would collide; Landsat thermal bands go on a broken-axis segment at the right, radar platforms get a channel row |
| Three fact columns | 46 mm | `I IMAGERY` (platform, dates, scenes, cloud, crs, ground, gsd), `II INDEX` (area, domain, norm, range, extrema, mean, median, pixels), `III COLORMAP` (name, source, reversed, L*, C* peak, hue, maps). Equal widths, 4 mm gutters, each column ruled top and under its label; kv rows only, no figures |
| Gradient band | 12 mm | trim-to-trim (8.5 in), not into the bleed, exactly as today; fixed flex basis so the flex column can never squeeze it |
| OKLCh curves | 23 mm | the existing three-panel L*, C*, h° chart, re-rendered at a 6.5 : 1 figure so its type stays near 6 pt at 150 mm wide |
| Histogram | 30 mm | bars filled with the colormap gradient over the natural domain (unchanged 10 : 2 figure), axis labels −1.0 … +1.0 |
| Colophon | 8 mm | `PLATE 004 · etna_ndvi_eclipse` left with the number in accent, the book title (`config/book.yaml`) right |

Blocks total 211 mm; six 5.6 mm gaps and one 3 mm gap bring it to about 247 mm, and
the colophon's `margin-top: auto` absorbs the rest, so every block lands at the same
height on every plate. The narrative lede is removed from the spread; if editorial
text is wanted it belongs in the theme introduction pages. The index name and formula
appear once, in the title block, so column II carries only the numbers.

Picker implemented 2026-09-07 in `web/src/app.css` (tokens, shared `.btn`, `.section-title`,
`.kv`, `.seg`), `routes/+page.svelte` (top bar, rail tabs, 380/1fr/320 grid) and every
component under `web/src/lib/ui/`. `BandRuler.svelte` is an HTML port of the book's ruler
(positions in percent, labels in real pixels so they stay legible at 300 px) and reads
`PlatformDef.bandsNm`, exported by `spectralcular export-web` from `Platform.bands_nm`. Known gaps:
the ledger thumbnail is the STAC scene thumbnail (whole tile, true color), not a render of
the framed index; a GPU snapshot on save would replace it. Fonts are the system stack;
Charter and JetBrains Mono are not yet self-hosted.

Book implemented 2026-09-07 in `templates/spread.html.j2`, `templates/book.css`,
`band_ruler.py`, `book.py` (scene tile captions, colormap facts) and
`render_assets.py` (landscape locator, shallower OKLCh figure). Proof:
`output/spreads/etna_ndvi_eclipse.pdf`.

### Overview spread (back matter, `overview.py` + `templates/overview.html.j2`)
The print twin of the picker's Overview tab, placed after the last plate and just
before the index (which doesn't exist yet). It reuses the recto frame (36 mm padding,
252 mm content height, running head, title block with accent box, roman-numeral
section heads, colophon). Every figure is an inline SVG laid out in millimetres in
Python, so block heights are known before WeasyPrint lays out the page. Theme ink is
the picker's `THEME_RGB` table, copied into `overview.py`. `spectralcular proof` adds a blank
page when needed so the spread opens on a verso.

| Page | Block | Content |
|------|-------|---------|
| Verso | Title block | `The book at a glance`, tally in the accent box (plates · places · indices · colormaps · true color), italic subtitle with years and platforms |
| | I Places | Equal Earth world map at 150 × 73 mm, Natural Earth 110 m land; one dot per place in theme ink, sized by plate count; plate numbers (`004`, `004·017`, `004 +3`) placed greedily right/left/above/below and dropped where they would collide; theme legend under the map |
| | II Scene dates · Season | one square per plate in its month, stacked by theme, years ruled (104 mm); plates per calendar month as stacked bars (40 mm) |
| | III Catalog coverage | one cell per pool index by theme, shaded by plate count (unused, 1, 2, 3+); every ramp the picker offers as a thin vertical bar by collection, unused ones faded, used ones underlined in accent |
| Recto | Title block | `Index × colormap`, combinations and repeats in the accent box, one-line key as subtitle |
| | IV Combinations | two columns, 190 mm tall: theme heads, then index names (bold mono) flowing like text, each followed by one chip per index × colormap pair: the gradient as printed (reversal included) over its plate numbers; repeated pairs carry every number, in bold accent. True-color plates close the list as a neutral-ramp group |

The combinations block steps through three density tiers until the whole book fits:
**named** (12.5 mm chips with the colormap name under the number), **numbered**
(8.5 mm chips, number only), **compact** (6 mm). A synthetic 231-plate book over
~110 indices lands in *numbered*; 330 plates still fit.

## Components / patterns
- **Buttons:** mono 11 px, 0.5 px border. Default: ink border on transparent. Primary
  (`Update plate`): ink fill, paper text. Quiet (`Frame here`): rule-colored border,
  soft ink text. No radius.
- **Chips (index picker):** mono 10.5 px, 0.5 px rule border; selected chip has accent
  border and text.
- **kv list:** flex rows, dotted `--rule` separators, key in mono 9.5 px uppercase
  mute, value in mono 11 px ink. Same component on screen and in print.
- **Band swatches:** 3-stop CSS gradient from the colormap's first, middle and last
  stop for lists; full LUT strip for the selected band and the printed gradient band.
- **Band ruler:** shared SVG/HTML component in both surfaces; positions are linear in
  wavelength from 400 to 2300 nm. Platform-aware: Landsat and Sentinel-1 need their own
  tick sets (thermal 10.6–12.5 µm cannot share the axis; show a second short axis).
- **Status pills:** mono 10.5 px, 1 px border in the status color, no fill.

## Motion
Minimal. Only user-triggered transitions: rail row selection and tab change
(120 ms ease-out background), frame handle hover (border color). The map and GPU
preview animate on their own terms. Respect `prefers-reduced-motion`.

## Dark mode
Not in scope. The current dark picker is retired in favor of this light, paper-based
UI so that what the editor sees on screen is the paper they will print on. If a dark
stage is ever wanted for judging imagery, make it a per-stage toggle (dark map ground
only), not a whole-app theme.

## Responsive behavior
- **≥ 1280 px:** three-column grid as above.
- **1024–1279 px:** rail narrows to 300 px and ledger rows drop the coordinate line;
  inspector 300 px.
- **< 1024 px (tablet and phone):** one stage. The rail and the inspector become
  sheets that share the stage's grid cell (`grid-area: stage`, z-index above the map),
  `min(100%, 400px)` wide, the rail anchored left and the inspector right, so a tablet
  in portrait keeps map visible beside the sheet and a phone gets a full-width sheet.
  A 46 px bottom bar (`MAP · PLACES · PLATES · PLATE`, mono, active in accent with a
  1.5 px accent inset top edge) drives which sheet is open; picking a place closes the
  sheet so the map is seen; Escape closes. The top bar shrinks to 48 px with the book
  name and a single primary Save/Update; `Frame here`, `Save as new` and `Export` move
  to a sticky head at the top of the inspector sheet. Frame toolbar keeps the name and
  the two number fields on one row and scrolls its button strip horizontally. Print
  readout shows Ground, Effective dpi and Rotation, with a mono `more` link for the
  rest. The map never unmounts between layouts (it is the one expensive object).
  The rail sheet has three tabs (`PLACES · PLATES · OVERVIEW`); the bar's `PLATES`
  button reopens whichever of Plates or Overview was used last, and the stage map keeps
  the plate dots while Overview is the rail tab, even with the sheet closed. Inside the
  inspector sheet the section heads stack below the sheet's own sticky head
  (`--stack-top` = its measured height). At 1024–1279 px the rail tabs drop the places
  count to fit three tabs in 300 px.
- **Touch (`pointer: coarse`):** type scale unchanged, hit areas grow (section heads 34 px,
  overview cells 14 px, buttons ≥ 34 px,
  segmented buttons ≥ 32 px, inputs 36 px at 16 px so iOS does not zoom on focus);
  deck.gl picking radius 14 px so frame handles can be grabbed; ledger row actions stay
  visible on the active row because there is no hover.
- Print layout is fixed (US Letter + bleed) and does not reflow.

## Scene fit (which indices a scene can feed)
The platform is implied by the index, and the scene is pinned per plate, so the picker
answers one question continuously: *can this index compute from this scene?*
`web/src/lib/data/fit.ts` decides from data the plate already carries (no round-trip):
same collection as the index's platform, and every asset the formula reads present on
the item. The second check matters because Planetary Computer's `sentinel-1-rtc` is
VV/VH at mid-latitudes and HH/HV over the poles, and `landsat-c2-l2` also holds
Landsat 4–7 items whose thermal band is `lwir`, not `lwir11`. The register is quiet:
- **Index chips:** a chip whose index needs another platform's scene gets a dashed
  border; one whose bands the pinned item does not carry gets a dotted border; both
  recede to `--ink-mute` on transparent. The tooltip states why. No color, no icon.
- **Theme group heads:** when a scene is pinned, a group that nothing in it can use
  says `needs Sentinel-1 RTC` in the warn color; a mixed group says `3 of 19 fit`.
- **Filter:** the index segmented control is `all · fits scene · approved`; `fits scene`
  is disabled until a scene is pinned.
- **Current index:** if the pinned scene cannot feed it, a warn pill carries the reason
  under the index name, and section I explains in a sentence with the way out (search
  that platform's scenes, or pick an index that fits). The search button names the
  platform it will search (`Search Landsat 8/9 C2 L2 scenes`).
- **Selection side:** the platform registry carries `stac_query` (Landsat 8/9 only) that
  both the Python loader and the picker apply; changing to an index on another platform
  clears stale results; radar searches expose the pass direction (ascending/descending)
  as a segmented control and list each frame's polarizations.

## First run (introduction and guide)
New users meet the picker twice, both in the paper register and both remembered per
browser (`web/src/lib/state/prefs.ts`, flags separate from the project so importing or
clearing a book never brings them back):
- **Introduction** (`Intro.svelte`): a modal on the first visit with an empty book, and
  behind the `?` button in the top bar and the `?` key afterwards. Serif headline, one
  paragraph on what a plate is, four numbered steps (place and frame, scene, index and
  colormap, save and export) under ink rules, then three columns: what is here (counts
  from the data files), good to know (public catalogs, browser-local book, first render
  is slow), keys. Footer: `Browse places`, `Open a worked example` (primary), and a link
  to show the stage guide again. Numbers are arabic here; the inspector's roman numerals
  are referred to by name (`I Scene`) so the two systems do not collide.
- **Worked example** (`Session.startExample`): frames Mt. Etna, the book's proof plate,
  searches its season window and pins the best result (`stac/rank.ts`: full coverage,
  then least cloud), with NDVI through `eclipse`, so a rendered page appears without
  touching the inspector. The same ranking orders the results list and backs the
  guide's `Pin the best match` button.
- **Stage guide** (`Guide.svelte`): a card under the frame toolbar, ink hairline with a
  2 px accent left edge, a mono step track `1 PLACE · 2 SCENE · 3 COMPOSE & SAVE` (current
  in accent, past struck through), one sentence derived from session state, and the
  action for that state inline (open the example, search the platform, pin the best
  match, open the inspector on narrow screens). It reads state rather than counting
  clicks, so it stays correct whichever panel the user works in. After the first save it
  says so once and offers `close`; `hide guide` at any point dismisses it for good.
- **Empty states:** `I Scene` explains that the frame shows only the basemap until a scene
  is pinned; the plates ledger and the places list already had theirs.
