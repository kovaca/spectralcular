# Spectralcular Scenery

The book this repository was built for: 100 scenes of 96 places, each a
Sentinel-2 spectral index rendered through a colormap, with a specimen card
facing every scene. `selection.json` is the plate-picker export it is built
from, and `book.yaml` is its copy (title, introduction, cover text), which
becomes the project's `config/book.yaml`.

```bash
examples/spectralcular-scenery/build.sh ~/books/spectralcular-scenery
```

The script creates a project folder (`spectralcular init`), imports the
selection, locks the spread order, builds every spread and writes the
upload-ready files:

| File | What |
|---|---|
| `output/print/spectralcular-scenery-interior.pdf` | 214 pages, US Letter + bleed, sRGB, 300 ppi |
| `output/print/spectralcular-scenery-cover.pdf` | one-piece cover; spine sized for the page count |
| `output/print/preflight.md` | checks against Lulu's file requirements |
| `output/proof.pdf` | the same interior before print finishing |

Budget about 25 GB of disk and an hour and a half on a home connection:
the scenes' bands are fetched from Earth Search at 10 m and cached under
`data/`. Re-running resumes where it stopped, and only spreads without a
PDF are built again.

Each plate pins its Sentinel-2 scene, frame (center, width, rotation) and
normalization, so a rebuild renders the same pages. To change the book, edit
`config/candidates.yaml` in the project folder (or the book copy in
`config/book.yaml`), or compose a new selection in the web picker and import
its export with `spectralcular import`.
