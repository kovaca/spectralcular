"""CLI entry point.

Pool review (Stage 3):
  - ``spectralcular review {locations|indices|colormaps|all}``  build pool contact sheets

Candidate matrix lifecycle (Stage 4 → 6):
  - ``spectralcular candidates generate``   auto-propose triples from the approved pools
  - ``spectralcular candidates review``     build the matrix contact sheet
  - ``spectralcular candidates order``      assign ordinals / lint the visual rhythm
  - ``spectralcular candidates lock``       emit spreads.csv + per-spread configs
  - ``spectralcular build``                 render approved candidates at full resolution
  - ``spectralcular overview``              render the overview spread (back matter)
  - ``spectralcular proof``                 assemble the combined proof PDF

Colormap catalog:
  - ``spectralcular colormaps sync``        vendor the colorcurious catalog from a checkout
  - ``spectralcular colormaps import``      merge catalog entries into the colormap pool

Single spread:
  - ``spectralcular run <config.yaml>``     build one full-resolution spread
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path


def _setup_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def _project_root() -> Path:
    from .paths import ProjectNotFound, find_project_root

    try:
        return find_project_root()
    except ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2)


# ---------------------------------------------------------------------------
# Shared path resolution
# ---------------------------------------------------------------------------

def _paths(root: Path) -> dict[str, Path]:
    return {
        "locations_yaml": root / "config" / "pool" / "locations.yaml",
        "indices_yaml": root / "config" / "pool" / "indices.yaml",
        "colormaps_yaml": root / "config" / "pool" / "colormaps.yaml",
        "candidates_yaml": root / "config" / "candidates.yaml",
        "preview_cache": root / "data" / "preview_cache",
        "review_out": root / "output" / "review",
        "spreads_config_dir": root / "config" / "spreads",
        "spreads_csv": root / "spreads.csv",
        "spreads_pdf_dir": root / "output" / "spreads",
        "proof_pdf": root / "output" / "proof.pdf",
        "proof_pdfx": root / "output" / "proof_pdfx.pdf",
        "overview_pdf": root / "output" / "overview.pdf",
        "metadata_dir": root / "output" / "metadata",
        "book_yaml": root / "config" / "book.yaml",
        "front_matter_pdf": root / "output" / "front_matter.pdf",
        "front_matter_work": root / "output" / "front_matter",
        "print_dir": root / "output" / "print",
        "book_specs_yaml": root / "config" / "book_specs.yaml",
        "web_data_dir": root / "web" / "src" / "lib" / "data",
        "reference_surfaces_yaml": root / "config" / "reference_surfaces.yaml",
        "index_guides_yaml": root / "config" / "index_guides.yaml",
        "truecolor_yaml": root / "config" / "truecolor.yaml",
    }


def _load_pools(p: dict[str, Path]):
    from .pool import load_colormap_pool, load_index_pool, load_location_pool

    return (
        load_location_pool(p["locations_yaml"]),
        load_index_pool(p["indices_yaml"]),
        load_colormap_pool(p["colormaps_yaml"]),
    )


# ---------------------------------------------------------------------------
# Single spread
# ---------------------------------------------------------------------------

def _cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import run

    print(run(args.config))
    return 0


# ---------------------------------------------------------------------------
# Pool review (Stage 3)
# ---------------------------------------------------------------------------

def _cmd_review(args: argparse.Namespace) -> int:
    from .pool import load_colormap_pool, load_index_pool, load_location_pool
    from .review.contact_sheet import (
        render_colormaps_sheet,
        render_indices_sheet,
        render_locations_sheet,
    )

    root = _project_root()
    p = _paths(root)
    out_dir = p["review_out"]
    rgb_cache = root / "data" / "review_cache"
    cmap_cache = rgb_cache / "_colormaps"
    cmap_cache.mkdir(parents=True, exist_ok=True)

    fetch = not args.no_fetch
    paths: dict[str, Path] = {}
    if args.target in ("all", "locations"):
        paths["locations"] = render_locations_sheet(
            load_location_pool(p["locations_yaml"]),
            cache_dir=rgb_cache, out_dir=out_dir, fetch=fetch, only_id=args.only,
            refresh=args.refresh,
        )
    if args.target in ("all", "indices"):
        paths["indices"] = render_indices_sheet(
            load_index_pool(p["indices_yaml"]), out_dir=out_dir,
        )
    if args.target in ("all", "colormaps"):
        paths["colormaps"] = render_colormaps_sheet(
            load_colormap_pool(p["colormaps_yaml"]),
            cache_dir=cmap_cache, out_dir=out_dir,
        )
    for name, fp in paths.items():
        print(f"{name}: {fp}")
    return 0


# ---------------------------------------------------------------------------
# Candidate matrix lifecycle (Stage 4)
# ---------------------------------------------------------------------------

def _cmd_candidates_generate(args: argparse.Namespace) -> int:
    from .candidate_gen import generate_candidates
    from .candidates import dump_candidates, load_candidates

    p = _paths(_project_root())
    loc_pool, idx_pool, cmap_pool = _load_pools(p)
    existing = load_candidates(p["candidates_yaml"])
    merged, per_theme = generate_candidates(
        loc_pool, idx_pool, cmap_pool, existing, per_location_index_cap=args.cap,
    )
    counts = merged.status_counts()
    print(f"candidates: {len(merged.entries)} total "
          f"(proposed {counts['proposed']}, approved {counts['approved']}, "
          f"rejected {counts['rejected']}, needs_revision {counts['needs_revision']})")
    for theme, n in sorted(per_theme.items()):
        print(f"  {theme}: {n} newly proposed")
    if args.dry_run:
        print("(dry run — config/candidates.yaml not written)")
        return 0
    dump_candidates(merged, p["candidates_yaml"])
    print(f"wrote {p['candidates_yaml']}")
    return 0


def _cmd_candidates_review(args: argparse.Namespace) -> int:
    from .candidates import load_candidates
    from .review.contact_sheet import render_candidates_sheet

    p = _paths(_project_root())
    loc_pool, idx_pool, cmap_pool = _load_pools(p)
    out = render_candidates_sheet(
        load_candidates(p["candidates_yaml"]),
        loc_pool, idx_pool, cmap_pool,
        preview_cache_dir=p["preview_cache"],
        out_dir=p["review_out"],
        fetch=not args.no_fetch,
        only_id=args.only,
        refresh=args.refresh,
        norm_method=args.norm_method,
        cvd_kind=args.cvd,
    )
    print(f"candidates: {out}")
    return 0


def _cmd_candidates_order(args: argparse.Namespace) -> int:
    from .candidates import dump_candidates, load_candidates
    from .pool import load_location_pool
    from .sequencing import assign_ordinals, lint_sequence

    p = _paths(_project_root())
    cs = load_candidates(p["candidates_yaml"])
    loc_pool = load_location_pool(p["locations_yaml"])

    if args.auto:
        cs = assign_ordinals(cs)
        dump_candidates(cs, p["candidates_yaml"])
        n = len([e for e in cs.entries if e.ordinal is not None])
        print(f"assigned ordinals to {n} approved candidate(s) → {p['candidates_yaml']}")

    violations = lint_sequence(cs, loc_pool)
    if not violations:
        print("rhythm: no violations")
    else:
        print(f"rhythm: {len(violations)} violation(s)")
        for v in violations:
            print(f"  #{v.ordinal:>3}  {v.kind}: {v.detail}")
    return 0


def _cmd_candidates_lock(args: argparse.Namespace) -> int:
    from .candidates import load_candidates
    from .global_norm import load_norm_ranges
    from .lock import LockError, lock_candidates

    p = _paths(_project_root())
    loc_pool, idx_pool, cmap_pool = _load_pools(p)
    try:
        config_paths, csv_path = lock_candidates(
            load_candidates(p["candidates_yaml"]),
            loc_pool, idx_pool, cmap_pool,
            load_norm_ranges(p["preview_cache"]),
            config_spreads_dir=p["spreads_config_dir"],
            spreads_csv_path=p["spreads_csv"],
            force=args.force,
        )
    except LockError as e:
        print(f"lock refused: {e}", file=sys.stderr)
        return 1
    print(f"locked {len(config_paths)} spread config(s) → {p['spreads_config_dir']}")
    print(f"merge sheet → {csv_path}")
    return 0


# ---------------------------------------------------------------------------
# Production + proof (Stage 5–6)
# ---------------------------------------------------------------------------

def _cmd_build(args: argparse.Namespace) -> int:
    from .candidate_build import BuildIncomplete, build_candidates
    from .candidates import load_candidates
    from .global_norm import load_norm_ranges

    root = _project_root()
    p = _paths(root)
    loc_pool, idx_pool, cmap_pool = _load_pools(p)
    try:
        outputs = build_candidates(
            load_candidates(p["candidates_yaml"]),
            loc_pool, idx_pool,
            load_norm_ranges(p["preview_cache"]),
            root,
            theme=args.theme,
            only_id=args.only,
            cmap_pool=cmap_pool,
            skip_existing=args.skip_existing,
            keep_going=args.keep_going,
        )
    except BuildIncomplete as e:
        print(f"built {len(e.outputs)} spread(s); {len(e.failures)} failed:", file=sys.stderr)
        for cid, err in e.failures:
            print(f"  ✗ {cid}: {err}", file=sys.stderr)
        print("re-run with --skip-existing to retry only these", file=sys.stderr)
        return 1
    print(f"built {len(outputs)} spread(s) → {p['spreads_pdf_dir']}")
    return 0


def _render_overview(root: Path, p: dict[str, Path], *, html: bool = False) -> Path:
    from .overview import (
        load_overview_plates,
        render_overview_html,
        render_overview_pdf,
    )

    from .book_meta import load_book_meta

    _locs, idx_pool, cmap_pool = _load_pools(p)
    plates = load_overview_plates(p["spreads_csv"], root, p["metadata_dir"])
    title = load_book_meta(p["book_yaml"]).title
    out = render_overview_pdf(out_pdf=p["overview_pdf"], plates=plates, idx_pool=idx_pool, cmap_pool=cmap_pool,
                              book_title=title)
    if html:
        render_overview_html(out_html=out.with_suffix(".html"), plates=plates,
                             idx_pool=idx_pool, cmap_pool=cmap_pool, book_title=title)
    return out


def _cmd_overview(args: argparse.Namespace) -> int:
    root = _project_root()
    out = _render_overview(root, _paths(root), html=args.html)
    print(f"overview: {out}")
    return 0


def _render_front_matter(root: Path, p: dict[str, Path]) -> Path:
    from .book_meta import load_book_meta
    from .front_matter import render_front_matter
    from .overview import load_overview_plates
    from .proof import _read_rows

    plates = load_overview_plates(p["spreads_csv"], root, p["metadata_dir"])
    rows = _read_rows(p["spreads_csv"])
    first = p["spreads_pdf_dir"] / f"{rows[0]['id']}.pdf" if rows else None
    out, _pages = render_front_matter(out_pdf=p["front_matter_pdf"], meta=load_book_meta(p["book_yaml"]),
                                      plates=plates, work_dir=p["front_matter_work"], first_spread_pdf=first)
    return out


def _assemble_proof(root: Path, p: dict[str, Path], args: argparse.Namespace) -> Path:
    from .proof import build_proof

    overview = None if args.no_overview else _render_overview(root, p)
    front = None if args.no_front_matter else _render_front_matter(root, p)
    return build_proof(
        p["spreads_csv"], p["spreads_pdf_dir"], p["proof_pdf"],
        front_matter_pdf=front,
        overview_pdf=overview,
    )


def _cmd_proof(args: argparse.Namespace) -> int:
    from .proof import finish_pdfx

    root = _project_root()
    p = _paths(root)
    out = _assemble_proof(root, p, args)
    print(f"proof: {out}")
    if args.pdfx:
        pdfx = finish_pdfx(out, p["proof_pdfx"])
        print(f"pdfx:  {pdfx}")
    return 0


def _cmd_print(args: argparse.Namespace) -> int:
    """Proof → print-ready interior + cover for upload, with a preflight report."""
    import csv

    import fitz

    from .book_meta import load_book_meta
    from .book_specs import load_book_specs
    from .cover import render_cover
    from .overview import load_overview_plates
    from .preflight import plate_pages_of, preflight_cover, preflight_interior
    from .print_pdf import finish_for_print

    root = _project_root()
    p = _paths(root)
    specs = load_book_specs(p["book_specs_yaml"])
    meta = load_book_meta(p["book_yaml"])
    proof = p["proof_pdf"] if args.reuse_proof and p["proof_pdf"].exists() else _assemble_proof(root, p, args)

    slug = re.sub(r"[^a-z0-9]+", "-", meta.title.lower()).strip("-") or "book"
    out_dir = p["print_dir"]
    pdf_meta = {"title": meta.title, "author": meta.author, "subject": meta.subtitle}
    finish = dict(ppi=specs.print_pdf.image_ppi, quality=specs.print_pdf.jpeg_quality, metadata=pdf_meta)

    interior = out_dir / f"{slug}-interior.pdf"
    rep = finish_for_print(proof, interior, bleed_in=specs.bleed_in, **finish)
    with fitz.open(interior) as d:
        pages = d.page_count
    print(f"interior: {interior}  ({pages} pages, {rep.bytes_out / 1e6:,.0f} MB; {rep.summary()})")

    plates = load_overview_plates(p["spreads_csv"], root, p["metadata_dir"])
    with p["spreads_csv"].open(newline="") as f:
        images = {r["plate_number"]: root / "assets" / "images" / f"{r['id']}.png" for r in csv.DictReader(f)}
    raw_cover, geo = render_cover(out_pdf=out_dir / "cover-raw.pdf", specs=specs, meta=meta, plates=plates,
                                  plate_images=images, pages=pages, work_dir=out_dir / "cover", project_root=root)
    cover = out_dir / f"{slug}-cover.pdf"
    # TrimBox = the panels' outer edge: inside the bleed (paperback) or the board wrap (casewrap).
    crep = finish_for_print(raw_cover, cover, bleed_in=specs.cover_edge_in, **finish)
    raw_cover.unlink(missing_ok=True)
    print(f"cover:    {cover}  ({geo.width_in:.3f} × {geo.height_in:.3f} in, spine {geo.spine_in:.3f} in; "
          f"{crep.summary()})")

    reports = [preflight_interior(interior, specs, plate_pages=plate_pages_of(interior)),
               preflight_cover(cover, specs, pages)]
    md = out_dir / "preflight.md"
    md.write_text(f"# Preflight — {meta.title}\n\n" + "\n".join(r.markdown() for r in reports))
    for r in reports:
        print(f"preflight {r.kind}: {r.status.upper()}")
        for c in r.checks:
            if c.status != "pass":
                print(f"  {'!' if c.status == 'warn' else '✗'} {c.name}: {c.detail}")
    print(f"report:   {md}")
    return 1 if any(r.status == "fail" for r in reports) else 0


def _cmd_import(args: argparse.Namespace) -> int:
    from .candidates import dump_candidates, load_candidates
    from .import_selection import ImportError_, import_selection, load_manifest
    from .pool import ReviewStatus, dump_location_pool

    p = _paths(_project_root())
    try:
        manifest = load_manifest(args.manifest)
    except ImportError_ as e:
        print(f"error: {e}")
        return 2
    loc_pool, idx_pool, cmap_pool = _load_pools(p)
    cands, locs, report = import_selection(
        manifest, load_candidates(p["candidates_yaml"]), loc_pool, idx_pool, cmap_pool,
        new_status=ReviewStatus(args.status),
    )
    print(f"manifest: {manifest.get('project', {}).get('name', '?')} · {len(manifest['plates'])} plate(s)")
    for s in report.locations_added:
        print(f"  + location  {s}")
    for s in report.candidates_added:
        print(f"  + candidate {s}")
    for s in report.candidates_updated:
        print(f"  ~ candidate {s}")
    for s in report.skipped:
        print(f"  ! skipped   {s}")
    for s in report.problems:
        print(f"  ✗ problem   {s}")
    print(report.summary())
    if args.dry_run:
        print("(dry run — nothing written)")
        return 1 if report.problems else 0
    if report.locations_added:  # don't reformat the reviewer's pool file for nothing
        dump_location_pool(locs, p["locations_yaml"])
        print(f"wrote {p['locations_yaml']}")
    dump_candidates(cands, p["candidates_yaml"])
    print(f"wrote {p['candidates_yaml']}")
    return 1 if report.problems else 0


# ---------------------------------------------------------------------------
# Colormap catalog
# ---------------------------------------------------------------------------

COLORMAPS_POOL_HEADER = """\
# Colormap pool. Entries resolve through palettize (`ns:name`), the vendored
# colorcurious catalog (`colorcurious:<collection>.<name>`, see
# src/spectralcular/_data/colorcurious/), or inline `colors:`.
#
# `spectralcular colormaps import` merges the catalog in here without touching reviewer
# edits (status / notes / semantic_register / pairs_with / print_safety).
# Edit `status:` in place to drive the editorial loop
# (proposed → approved | rejected | needs_revision)."""


def _cmd_colormaps_sync(args: argparse.Namespace) -> int:
    from .catalog import CatalogError, sync_catalog

    try:
        rep = sync_catalog(args.from_, slim=not args.full)
    except CatalogError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(rep.summary())
    for f in rep.files:
        print(f"  wrote {f}")
    return 0


def _cmd_colormaps_import(args: argparse.Namespace) -> int:
    from .catalog import CatalogError, load_catalog
    from .colormap_import import merge_catalog_into_pool
    from .pool import dump_colormap_pool, load_colormap_pool

    p = _paths(_project_root())
    try:
        catalog = load_catalog()
    except CatalogError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    pool = load_colormap_pool(p["colormaps_yaml"])
    merged, report = merge_catalog_into_pool(pool, catalog, include_categorical=args.include_categorical)
    print(f"catalog: {len(catalog)} records ({catalog.meta.get('source_commit', 'unknown')[:12]}) · "
          f"pool: {len(pool.entries)} → {len(merged.entries)} entries")
    for s in report.added:
        print(f"  + {s}")
    for s in report.enriched:
        print(f"  ~ {s}")
    if report.skipped_categorical:
        print(f"  ! skipped {len(report.skipped_categorical)} categorical "
              f"(pass --include-categorical to add them)")
    for s in report.problems:
        print(f"  ✗ {s}")
    print(report.summary())
    if args.dry_run:
        print("(dry run — nothing written)")
        return 1 if report.problems else 0
    dump_colormap_pool(merged, p["colormaps_yaml"], header=COLORMAPS_POOL_HEADER)
    print(f"wrote {p['colormaps_yaml']}")
    return 1 if report.problems else 0


def _cmd_init(args: argparse.Namespace) -> int:
    from .paths import init_project

    try:
        written = init_project(args.dir, book_yaml=args.book, force=args.force)
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"project ready: {args.dir.expanduser().resolve()} ({len(written)} config files)")
    print(f"next: export SPECTRALCULAR_PROJECT_DIR={args.dir}  (or pass --project-dir)")
    return 0


def _cmd_export_web(args: argparse.Namespace) -> int:
    from .export_web import export_web_data
    from .index_reading import load_guides
    from .reference_surfaces import load_surfaces
    from .truecolor import load_presets

    p = _paths(_project_root())
    loc_pool, idx_pool, cmap_pool = _load_pools(p)
    out_dir = args.out if args.out is not None else p["web_data_dir"]
    surfaces = load_surfaces(p["reference_surfaces_yaml"])
    if surfaces is None:
        print(f"note: {p['reference_surfaces_yaml']} not found; indices export without reference surfaces "
              "(run `spectralcular reference-surfaces`)")
    written = export_web_data(
        out_dir,
        book_specs_yaml=p["book_specs_yaml"],
        loc_pool=loc_pool, idx_pool=idx_pool, cmap_pool=cmap_pool,
        surfaces=surfaces, guides=load_guides(p["index_guides_yaml"]),
        truecolor=load_presets(p["truecolor_yaml"]),
    )
    for w in written:
        print(f"wrote {w}")
    return 0


def _cmd_reference_surfaces(args: argparse.Namespace) -> int:
    from .reference_surfaces import dump_surfaces, load_surfaces, sample_surfaces

    p = _paths(_project_root())
    path = p["reference_surfaces_yaml"]
    existing = load_surfaces(path)
    sf = sample_surfaces(existing, only=set(args.only) if args.only else None)
    dump_surfaces(sf, path)
    missing = [f"{s.id}:{pid}" for s in sf.surfaces for pid in ("sentinel-2-l2a",) if pid not in s.samples]
    print(f"wrote {path}")
    if missing:
        print("no sample for: " + ", ".join(missing))
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="spectralcular", description="Spectralcular pipeline: satellite spectral-index scenes → a print-ready book.")
    p.add_argument("-v", "--verbose", action="count", default=0,
                   help="Increase verbosity (-v INFO, -vv DEBUG)")
    p.add_argument("--project-dir", type=Path, default=None, metavar="DIR",
                   help="Project folder holding config/, data/, output/ "
                        "(default: $SPECTRALCULAR_PROJECT_DIR, else the nearest ancestor with config/book_specs.yaml).")
    sub = p.add_subparsers(dest="cmd", required=True)

    # run
    p_run = sub.add_parser("run", help="Build one spread from a YAML config.")
    p_run.add_argument("config", type=Path, help="Path to a spread YAML config")
    p_run.set_defaults(func=_cmd_run)

    # review (pools)
    p_review = sub.add_parser("review", help="Build Stage-3 pool review contact sheets.")
    p_review.add_argument("target", choices=["locations", "indices", "colormaps", "all"])
    p_review.add_argument("--no-fetch", action="store_true",
                          help="Skip STAC; rebuild HTML from cached previews only.")
    p_review.add_argument("--only", type=str, default=None,
                          help="Only refresh this location id (others use cache).")
    p_review.add_argument("--refresh", action="store_true",
                          help="Force re-fetch of every cached preview.")
    p_review.set_defaults(func=_cmd_review)

    # candidates (Stage 4)
    p_cand = sub.add_parser("candidates", help="Stage-4 candidate matrix lifecycle.")
    cand_sub = p_cand.add_subparsers(dest="cand_cmd", required=True)

    p_gen = cand_sub.add_parser("generate", help="Auto-propose triples from approved pools.")
    p_gen.add_argument("--cap", type=int, default=6,
                       help="Max colormap variants per (location, index). Default 6.")
    p_gen.add_argument("--dry-run", action="store_true",
                       help="Report counts without writing candidates.yaml.")
    p_gen.set_defaults(func=_cmd_candidates_generate)

    p_crev = cand_sub.add_parser("review", help="Build the matrix contact sheet.")
    p_crev.add_argument("--no-fetch", action="store_true",
                        help="Skip STAC; recolorize from cached index rasters only.")
    p_crev.add_argument("--only", type=str, default=None,
                        help="Only (re)fetch this location id's imagery.")
    p_crev.add_argument("--refresh", action="store_true",
                        help="Re-fetch imagery and recolorize all thumbnails.")
    p_crev.add_argument("--norm-method", choices=["percentile_pooled", "domain"],
                        default="percentile_pooled",
                        help="Per-index global normalization strategy.")
    p_crev.add_argument("--cvd", choices=["deuteranopia", "protanopia", "tritanopia"],
                        default="deuteranopia", help="Colorblind simulation type.")
    p_crev.set_defaults(func=_cmd_candidates_review)

    p_ord = cand_sub.add_parser("order", help="Assign ordinals / lint visual rhythm.")
    p_ord.add_argument("--auto", action="store_true",
                       help="Assign ordinals by taxonomy + break colormap runs.")
    p_ord.add_argument("--check", action="store_true",
                       help="Lint the sequence only (no mutation). Default if no --auto.")
    p_ord.set_defaults(func=_cmd_candidates_order)

    p_lock = cand_sub.add_parser("lock", help="Emit spreads.csv + per-spread configs.")
    p_lock.add_argument("--force", action="store_true",
                        help="Lock even with unresolved (proposed/needs_revision) entries.")
    p_lock.set_defaults(func=_cmd_candidates_lock)

    # build (Stage 5)
    p_build = sub.add_parser("build", help="Render approved candidates at full resolution.")
    p_build.add_argument("--theme", type=str, default=None, help="Restrict to one theme.")
    p_build.add_argument("--only", type=str, default=None, help="Restrict to one candidate id.")
    p_build.add_argument("--skip-existing", action="store_true",
                         help="Leave spreads that already have a PDF (resume an interrupted build).")
    p_build.add_argument("--keep-going", action="store_true",
                         help="Build the rest when a spread fails; list the failures at the end.")
    p_build.set_defaults(func=_cmd_build)

    # import (plate-picker selection manifest → config)
    p_imp = sub.add_parser("import", help="Import a plate-picker selection manifest into config/.")
    p_imp.add_argument("manifest", type=Path, help="Path to <book>.selection.json from the web picker")
    p_imp.add_argument("--status", choices=["approved", "proposed"], default="approved",
                       help="Status for newly created candidates (default approved).")
    p_imp.add_argument("--dry-run", action="store_true", help="Report without writing.")
    p_imp.set_defaults(func=_cmd_import)

    # colormaps (catalog sync + pool import)
    p_cm = sub.add_parser("colormaps", help="Colormap catalog: sync from colorcurious, import into the pool.")
    cm_sub = p_cm.add_subparsers(dest="cm_cmd", required=True)
    p_cms = cm_sub.add_parser("sync", help="Vendor colormaps.json + sprite from a colorcurious checkout.")
    p_cms.add_argument("--from", dest="from_", type=Path, required=True,
                       help="Path to a colorcurious checkout (or its static/ dir).")
    p_cms.add_argument("--full", action="store_true",
                       help="Keep the full metrics JSON instead of the slimmed one.")
    p_cms.set_defaults(func=_cmd_colormaps_sync)
    p_cmi = cm_sub.add_parser("import", help="Merge catalog records into config/pool/colormaps.yaml.")
    p_cmi.add_argument("--include-categorical", action="store_true",
                       help="Also add categorical palettes (hidden in the picker, unused in pairing).")
    p_cmi.add_argument("--dry-run", action="store_true", help="Report without writing.")
    p_cmi.set_defaults(func=_cmd_colormaps_import)

    # init (a fresh project folder for a book)
    p_init = sub.add_parser("init", help="Create a project folder (config copied from this checkout) for a book.")
    p_init.add_argument("dir", type=Path, help="Folder to create; data and outputs for the book live here.")
    p_init.add_argument("--book", type=Path, default=None,
                        help="A book.yaml (title, introduction, cover copy) to use instead of config/book.yaml.")
    p_init.add_argument("--force", action="store_true", help="Refresh the config of an existing project.")
    p_init.set_defaults(func=_cmd_init)

    # export-web (static data for the web plate picker)
    p_web = sub.add_parser("export-web", help="Export pools + book specs as JSON for web/.")
    p_web.add_argument("--out", type=Path, default=None,
                       help="Output directory (default: web/src/lib/data).")
    p_web.set_defaults(func=_cmd_export_web)

    # reference-surfaces (measured spectra for index interpretation)
    p_ref = sub.add_parser("reference-surfaces", help="Sample reference-surface spectra from real scenes (network).")
    p_ref.add_argument("--only", nargs="*", metavar="ID",
                       help="Re-sample only these surface or platform ids; keep the rest.")
    p_ref.set_defaults(func=_cmd_reference_surfaces)

    # overview spread (back matter)
    p_ov = sub.add_parser("overview", help="Render the overview spread from the locked book.")
    p_ov.add_argument("--html", action="store_true",
                      help="Also write output/overview.html for a quick look in a browser.")
    p_ov.set_defaults(func=_cmd_overview)

    # proof (Stage 6)
    p_proof = sub.add_parser("proof", help="Assemble the combined proof PDF.")
    p_proof.add_argument("--no-front-matter", action="store_true",
                         help="Leave out the front matter (config/book.yaml).")
    p_proof.add_argument("--no-overview", action="store_true",
                         help="Leave the overview spread out of the back matter.")
    p_proof.add_argument("--pdfx", action="store_true",
                         help="Run the experimental Ghostscript finishing pass (Lulu does not need it).")
    p_proof.set_defaults(func=_cmd_proof)

    # print (Stage 7): upload-ready interior + cover
    p_print = sub.add_parser("print", help="Print-ready interior + cover PDFs and a preflight report "
                                           "(output/print/).")
    p_print.add_argument("--reuse-proof", action="store_true",
                         help="Finish the existing output/proof.pdf instead of re-assembling it.")
    p_print.add_argument("--no-front-matter", action="store_true", help=argparse.SUPPRESS)
    p_print.add_argument("--no-overview", action="store_true", help=argparse.SUPPRESS)
    p_print.set_defaults(func=_cmd_print)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    if args.project_dir is not None:
        from .paths import set_project_root_override
        set_project_root_override(args.project_dir)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
