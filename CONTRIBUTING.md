# Contributing

Thanks for looking. The project is small and opinionated; the fastest way to
help is to render a spread, notice what is wrong on paper, and open an issue.

## Setup

```bash
uv sync                      # Python 3.11+, all pipeline deps
uv run pytest -q             # offline unit tests (no network, no data)
cd web && pnpm install && pnpm test && pnpm check
```

System libraries: WeasyPrint needs Pango. Lint with `uv run ruff check src tests tools` (CI runs it).
A full book build belongs in its own project folder (`spectralcular init <dir>`), not the checkout.

## Ground rules

- **Pools are the source of truth.** `config/pool/*.yaml` (and a project's
  `config/candidates.yaml`) hold reviewer decisions in `status:` / `notes:`.
  Generators and importers must merge, never overwrite (see
  `candidate_gen.merge_candidates`, `colormap_import.merge_catalog_into_pool`).
- **Indices go through `spyndex`, colormaps through `resolve_colormap`.** No
  hand-rolled formulas, no matplotlib-only palettes.
- **`web/src/lib/data/` is generated but committed.** Run `uv run spectralcular export-web`
  after touching anything in `config/`; CI fails if it is stale.
  `uv run spectralcular reference-surfaces` re-measures
  `config/reference_surfaces.yaml` from real scenes (network) when a platform
  or the surface list changes.
- **Print specs are upstream constraints.** Read `docs/process.md` before
  proposing layout or format changes.
- Keep the module boundaries (config → data_loader → indices → normalize →
  colormaps → render_assets → book → proof → print_pdf).
- **American English** in code, docs and printed copy (color, gray, center);
  quoted titles and upstream data keep their own spelling.

## Adding a colormap

Paste stops into `config/pool/colormaps.yaml`:

```yaml
- name: custom:teal_dusk
  type: sequential
  semantic_register: water
  colors: ["#04202c", "#2c6f7a", "#a7d8d1", "#f6f1e6"]
  status: proposed
```

Or refresh the colorcurio.us catalog: `spectralcular colormaps sync --from <checkout>` then
`spectralcular colormaps import`. New entries land as `proposed`; flip `status:` after
looking at `spectralcular review colormaps`.

## Licensing

Code is MIT. Colormap data keeps its upstream license (see
`THIRD_PARTY_NOTICES.md`); when you add one, record `source:` so the colophon
can credit it.
