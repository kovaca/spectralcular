"""Export the config pools + book specs as static JSON for the web plate picker.

``spectralcular export-web`` writes deterministic JSON (sorted keys, rounded floats) plus a
256-wide RGBA colormap LUT sprite into ``web/src/lib/data/``. The web app treats
these files as its read-only source of truth so that index formulas, colormap
samples and book geometry stay defined *once*, in Python, alongside the build.

Files written:
  book.json           physical page geometry derived from config/book_specs.yaml
  indices.json        pool indices + spyndex formula/constants/domain/gsd, plus
                      the reading aids from index_reading.py: sampled formula
                      bounds, reference-surface values, hand-written guides
  colormaps.json      pool colormaps + provenance + LUT row index + preview stops
  colormaps_lut.png   256 × N RGBA8 sprite, one row per continuous colormap (same
                      256-stop sampling as :func:`colormaps.resolve_colormap`);
                      categorical entries get ``row: null`` and no sprite row
  locations.json      location pool verbatim (camelCase keys)
  index_fixtures.json seeded (bands → value) pairs from spyndex.computeIndex so
                      the TS evaluator can be checked against the Python truth
  platforms.json      imagery platforms (STAC source, param→asset, scale/offset,
                      mask semantics, wavelengths, rgb assets) from platforms.PLATFORMS
  truecolor.json      true-color tone presets from config/truecolor.yaml
  truecolor_fixtures.json
                      seeded (reflectance, spec) → display RGB from
                      :func:`truecolor.tone`, the parity check for the web's
                      TS/GLSL tone pipeline
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any

import numpy as np
import spyndex
from PIL import Image

from .book import S2_GSD_M
from .book_specs import load_book_specs
from .colormaps import resolve_colormap
from .index_reading import IndexGuide, formula_bounds, guide_record, surface_values
from .indices import INDEX_DOMAIN, S2_REFLECTANCE_SCALE, DEFAULT_SCL_MASK_CLASSES, band_params, constant_defaults
from .platforms import DEFAULT_PLATFORM_ID, PLATFORMS, Platform, get_platform
from .pool import ColormapPool, IndexPool, LocationPool
from .reference_surfaces import SurfacesFile
from .truecolor import SPEC_VERSION as TRUECOLOR_SPEC_VERSION
from .truecolor import TrueColorPresets, TrueColorSpec, TrueColorStretch, tone

log = logging.getLogger(__name__)

# v3: indices gain bounds / surfaces / unstableSurfaces / guide, file gains surfaces
SCHEMA_VERSION = 3
# WebGL2 only guarantees this many 2D-array texture layers; the LUT sprite is
# uploaded as one layer per row, so warn when the pool grows past it.
GUARANTEED_LUT_ROWS = 256
LUT_WIDTH = 256
PREVIEW_STOPS = 16
# Distinct band assets the web GPU compositor can bind (mirrors MAX_GPU_BANDS in
# web/src/lib/raster/formula/resolve.ts — keep the two in step).
MAX_GPU_BANDS = 8

# Backward-compatible alias: spyndex param → Sentinel-2 asset key.
PARAM_TO_ASSET: dict[str, str] = PLATFORMS[DEFAULT_PLATFORM_ID].param_to_asset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _round(obj: Any, ndigits: int = 6) -> Any:
    """Recursively round floats so the JSON output is byte-stable across runs.

    Small magnitudes keep ``ndigits`` significant figures instead of decimals,
    so scale factors like Landsat's 2.75e-5 survive (6 decimals made it 2.8e-5).
    """
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return None
        if obj != 0.0 and abs(obj) < 0.1:
            return float(f"{obj:.{ndigits}g}")
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: _round(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round(v, ndigits) for v in obj]
    return obj


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_round(payload), indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    log.info("wrote %s", path)


def _camel(s: str) -> str:
    head, *tail = s.split("_")
    return head + "".join(t.capitalize() for t in tail)


def _camel_keys(d: dict) -> dict:
    return {_camel(k): v for k, v in d.items()}


# ---------------------------------------------------------------------------
# book.json
# ---------------------------------------------------------------------------

_PAGE_SIZE_LABEL = {"us_letter": "Letter", "a4": "A4"}


def export_book(book_specs_yaml: Path) -> dict:
    spec = load_book_specs(book_specs_yaml)
    ceiling_w, ceiling_h = spec.aoi_ceiling()
    px_w, px_h = spec.native_px
    return {
        "schemaVersion": SCHEMA_VERSION,
        "pageSize": _PAGE_SIZE_LABEL.get(spec.trim.page_size, spec.trim.page_size),
        "orientation": spec.trim.orientation,
        "trim": {"widthIn": spec.trim.width_in, "heightIn": spec.trim.height_in},
        "bleedIn": spec.bleed_in,
        # The plate fills the bleed box; this is the frame's aspect.
        "plate": {"widthIn": spec.plate_width_in, "heightIn": spec.plate_height_in, "aspect": spec.plate_aspect},
        "dpi": spec.dpi,
        "nativePx": {"width": px_w, "height": px_h},
        "aoiCeilingKm": {"width": ceiling_w, "height": ceiling_h},
        "safetyMarginIn": spec.safety_margin_in,
        "gutter": {
            "fullBleedInnerMarginIn": spec.gutter.full_bleed_inner_margin_in,
            "recommendedInnerMarginIn": spec.gutter.recommended_inner_margin_in,
        },
        "reflectanceScale": S2_REFLECTANCE_SCALE,
        "sclMaskClasses": list(DEFAULT_SCL_MASK_CLASSES),
    }


# ---------------------------------------------------------------------------
# indices.json + fixtures
# ---------------------------------------------------------------------------

def _index_record(entry, surfaces: SurfacesFile | None = None, guides: dict[str, IndexGuide] | None = None) -> dict:
    spec = spyndex.indices[entry.name]
    platform = get_platform(entry.platform)
    params = band_params(entry.name)
    constants = constant_defaults(entry.name, platform)
    computable = all(v is not None for v in constants.values())
    bands = {p: entry.bands.get(p, platform.param_to_asset.get(p)) for p in params}
    distinct_assets = {a for a in bands.values() if a is not None}
    domain = INDEX_DOMAIN.get(entry.name)
    bounds = formula_bounds(entry.name, platform) if computable else None
    on_surfaces, unstable = surface_values(entry.name, platform, surfaces, bounds)
    guide = (guides or {}).get(entry.name)
    return {
        "name": entry.name,
        "longName": spec.long_name,
        "formula": spec.formula,
        "theme": entry.theme,
        "platform": entry.platform,
        "applicationDomain": spec.application_domain,
        "bands": bands,
        "params": params,
        "constants": constants,
        "domain": list(domain) if domain else None,
        "status": entry.status.value,
        "expressiveIn": list(entry.expressive_in),
        "gsdM": platform.gsd_for_assets(distinct_assets),
        "gpuSupported": computable and len(distinct_assets) <= MAX_GPU_BANDS,
        "computable": computable,
        "reference": spec.reference,
        "notes": entry.notes,
        "bounds": bounds,
        "surfaces": on_surfaces,
        "unstableSurfaces": unstable,
        "guide": guide_record(guide) if guide else None,
    }


def _surface_record(s) -> dict:
    return {
        "id": s.id,
        "label": s.label,
        "description": s.description,
        "samples": {
            pid: {"item": sm.item, "datetime": sm.datetime, "lonlat": list(sm.lonlat)}
            for pid, sm in s.samples.items()
        },
    }


def export_indices(
    idx_pool: IndexPool,
    surfaces: SurfacesFile | None = None,
    guides: dict[str, IndexGuide] | None = None,
) -> dict:
    guides = guides or {}
    unknown = sorted(set(guides) - {e.name for e in idx_pool.entries})
    if unknown:
        log.warning("index guides for indices not in the pool: %s", ", ".join(unknown))
    return {
        "schemaVersion": SCHEMA_VERSION,
        # Sentinel-2 tables, kept for consumers that predate platforms.json.
        "gsdByAsset": dict(S2_GSD_M),
        "paramToAsset": dict(PARAM_TO_ASSET),
        "surfaces": [_surface_record(s) for s in surfaces.surfaces] if surfaces else [],
        "entries": [_index_record(e, surfaces, guides) for e in idx_pool.entries],
    }


def _platform_record(p: Platform) -> dict:
    return {
        "id": p.id,
        "label": p.label,
        "stacApi": p.stac_api,
        "collection": p.collection,
        "signing": p.signing,
        "paramToAsset": dict(p.param_to_asset),
        "gsdByAsset": dict(p.gsd_by_asset),
        "valueScale": {k: list(v) for k, v in p.value_scale.items()},
        "nodata": dict(p.nodata),
        "mask": None if p.mask is None else {"asset": p.mask.asset, "kind": p.mask.kind, "values": list(p.mask.values)},
        "cloudProperty": p.cloud_property,
        "wavelengthNm": dict(p.wavelength_nm),
        "bandsNm": {k: list(v) for k, v in p.bands_nm.items()},
        "gridProperty": list(p.grid_property) if isinstance(p.grid_property, tuple) else p.grid_property,
        "nativeResM": p.native_res_m,
        "dtype": p.dtype,
        "valueCeiling": p.value_ceiling,
        "orbitProperty": p.orbit_property,
        "stacQuery": dict(p.stac_query),
        "rgbAssets": list(p.rgb_assets) if p.rgb_assets else None,
    }


def export_platforms(platforms: dict[str, Platform] = PLATFORMS) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "defaultPlatform": DEFAULT_PLATFORM_ID,
        "entries": [_platform_record(p) for p in platforms.values()],
    }


def export_index_fixtures(idx_pool: IndexPool, *, n: int = 5, seed: int = 1234) -> dict:
    """Seeded reflectance envs + spyndex results; the TS evaluator must reproduce them."""
    rng = random.Random(seed)
    fixtures: dict[str, list[dict]] = {}
    for entry in idx_pool.entries:
        params_ = band_params(entry.name)
        constants = constant_defaults(entry.name, get_platform(entry.platform))
        if any(v is None for v in constants.values()):
            continue
        cases = []
        for _ in range(n):
            env = {p: round(rng.uniform(0.005, 0.6), 4) for p in params_}
            params = {**env, **constants}
            try:
                raw = spyndex.computeIndex(entry.name, params=params)
            except Exception as e:  # noqa: BLE001 — record, don't abort the export
                log.warning("fixture for %s failed: %s", entry.name, e)
                continue
            # Python promotes negative-base fractional powers to complex; numpy
            # on real arrays (and GLSL) yields NaN there. Record NaN → null.
            val = None if isinstance(raw, complex) else float(raw)
            cases.append({"bands": env, "value": val if val is not None and np.isfinite(val) else None})
        fixtures[entry.name] = cases
    return {"schemaVersion": SCHEMA_VERSION, "seed": seed, "fixtures": fixtures}


# ---------------------------------------------------------------------------
# truecolor.json + fixtures
# ---------------------------------------------------------------------------

def _stretch_record(st: TrueColorStretch) -> dict:
    return {
        "mode": st.mode,
        "source": st.source,
        "pLo": st.p_lo,
        "pHi": st.p_hi,
        "lo": list(st.lo) if st.lo is not None else None,
        "hi": list(st.hi) if st.hi is not None else None,
    }


def _spec_record(spec: TrueColorSpec) -> dict:
    return {
        "preset": spec.preset,
        "stretch": _stretch_record(spec.stretch),
        "gamma": spec.gamma,
        "contrast": spec.contrast,
        "midpoint": spec.midpoint,
        "saturation": spec.saturation,
    }


def export_truecolor(presets: TrueColorPresets) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "specVersion": TRUECOLOR_SPEC_VERSION,
        "default": presets.default,
        "presets": [
            {"id": p.id, "label": p.label, "description": p.description, **_spec_record(p)}
            for p in presets.presets
        ],
    }


def export_truecolor_fixtures(*, n_specs: int = 12, n_pixels: int = 8, seed: int = 4321) -> dict:
    """Seeded (reflectance, spec) → display RGB pairs from the Python tone
    pipeline; the web's TS implementation must reproduce them."""
    rng = random.Random(seed)
    cases = []
    for i in range(n_specs):
        linked = i % 2 == 0
        if linked:
            lo = round(rng.uniform(-0.02, 0.08), 4)
            hi = round(lo + rng.uniform(0.15, 0.9), 4)
            lo3, hi3 = (lo,) * 3, (hi,) * 3
        else:
            lo3 = tuple(round(rng.uniform(-0.02, 0.1), 4) for _ in range(3))
            hi3 = tuple(round(l + rng.uniform(0.15, 0.9), 4) for l in lo3)
        spec = TrueColorSpec(
            stretch=TrueColorStretch(mode="linked" if linked else "per-channel", source="manual", lo=lo3, hi=hi3),
            gamma=round(rng.choice([1.0, rng.uniform(0.5, 3.0)]), 3),
            # every other spec exercises the sigmoid / OKLab paths
            contrast=round(rng.uniform(1.0, 15.0), 3) if i % 3 else 0.0,
            midpoint=round(rng.uniform(0.2, 0.8), 3),
            saturation=round(rng.uniform(0.0, 2.0), 3) if i % 4 else 1.0,
        )
        pixels = [[round(rng.uniform(-0.05, 1.0), 4) for _ in range(3)] for _ in range(n_pixels)]
        # pin the clamp edges: below every lo, above every hi
        pixels += [[-0.1, -0.1, -0.1], [1.2, 1.2, 1.2]]
        out = tone(np.array(pixels), spec)
        cases.append({
            "spec": _spec_record(spec),
            "reflectance": pixels,
            "rgb": out.tolist(),
        })
    return {"schemaVersion": SCHEMA_VERSION, "specVersion": TRUECOLOR_SPEC_VERSION, "seed": seed, "cases": cases}


# ---------------------------------------------------------------------------
# colormaps.json + LUT sprite
# ---------------------------------------------------------------------------

def _hex(rgba: np.ndarray) -> str:
    r, g, b = (int(round(float(c) * 255)) for c in rgba[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def _collection_of(entry) -> str:
    if entry.source is not None and entry.source.collection:
        return entry.source.collection
    head, sep, _ = entry.name.partition(":")
    return head if sep else "custom"


def export_colormaps(cmap_pool: ColormapPool, lut_png: Path) -> dict:
    rows: list[np.ndarray] = []
    entries: list[dict] = []
    for entry in cmap_pool.entries:
        resolved = resolve_colormap(entry, n_samples=LUT_WIDTH)
        colors = np.asarray(resolved.matplotlib.colors, dtype="float64")  # (256, 4) in [0, 1]
        stop_idx = np.linspace(0, LUT_WIDTH - 1, PREVIEW_STOPS).round().astype(int)
        # The shader interpolates linearly along a row; categorical palettes
        # would smear, and the picker hides them anyway — no LUT row.
        row: int | None = None
        if entry.type != "categorical":
            row = len(rows)
            rows.append(np.clip(np.round(colors * 255), 0, 255).astype("uint8"))
        src = entry.source
        entries.append({
            "name": entry.name,
            "shortName": entry.short_name,
            "collection": _collection_of(entry),
            "row": row,
            "type": entry.type,
            "semanticRegister": entry.semantic_register,
            "printSafety": entry.print_safety,
            "pairsWith": list(entry.pairs_with),
            "status": entry.status.value,
            "previewStops": [_hex(colors[i]) for i in stop_idx],
            "tags": list(entry.tags),
            "source": None if src is None else {
                "collection": src.collection,
                "catalogId": src.catalog_id,
                "attribution": src.attribution,
                "license": src.license,
                "url": src.url,
            },
            "hasColors": bool(entry.colors),
            "notes": entry.notes,
        })
    if len(rows) > GUARANTEED_LUT_ROWS:
        log.warning("LUT sprite has %d rows; WebGL2 guarantees only %d texture-array layers "
                    "(most GPUs allow 2048). The web app checks the device limit at load.",
                    len(rows), GUARANTEED_LUT_ROWS)
    sprite = np.stack(rows, axis=0)  # (N, 256, 4)
    lut_png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(sprite, mode="RGBA").save(lut_png, optimize=True)
    log.info("wrote %s (%d rows)", lut_png, len(rows))
    return {
        "schemaVersion": SCHEMA_VERSION,
        "lutImage": lut_png.name,
        "lutWidth": LUT_WIDTH,
        "entries": entries,
    }


# ---------------------------------------------------------------------------
# locations.json
# ---------------------------------------------------------------------------

def export_locations(loc_pool: LocationPool) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "entries": [_camel_keys(e.model_dump(mode="json")) for e in loc_pool.entries],
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def export_web_data(
    out_dir: Path,
    *,
    book_specs_yaml: Path,
    loc_pool: LocationPool,
    idx_pool: IndexPool,
    cmap_pool: ColormapPool,
    surfaces: SurfacesFile | None = None,
    guides: dict[str, IndexGuide] | None = None,
    truecolor: TrueColorPresets | None = None,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def put(name: str, payload: dict) -> None:
        p = out_dir / name
        _write_json(p, payload)
        written.append(p)

    put("book.json", export_book(book_specs_yaml))
    put("platforms.json", export_platforms())
    put("indices.json", export_indices(idx_pool, surfaces, guides))
    put("index_fixtures.json", export_index_fixtures(idx_pool))
    lut_png = out_dir / "colormaps_lut.png"
    put("colormaps.json", export_colormaps(cmap_pool, lut_png))
    written.append(lut_png)
    put("locations.json", export_locations(loc_pool))
    if truecolor is not None:
        put("truecolor.json", export_truecolor(truecolor))
    put("truecolor_fixtures.json", export_truecolor_fixtures())
    return written
