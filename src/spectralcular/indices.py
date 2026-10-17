"""Spectral index computation via spyndex."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import rioxarray  # noqa: F401
import spyndex
import xarray as xr

from .config import Index
from .data_loader import open_band
from .platforms import PLATFORMS, Platform, S2_SCL_MASK_CLASSES

log = logging.getLogger(__name__)

# Backward-compatible aliases; the registry in platforms.py is authoritative.
DEFAULT_SCL_MASK_CLASSES = S2_SCL_MASK_CLASSES
S2_REFLECTANCE_SCALE = 1.0 / PLATFORMS["sentinel-2-l2a"].scale_for("red")[0]

# Canonical "natural" domain for a few common indices, used to fix the histogram
# x-axis to the index's full meaningful range (e.g. NDVI [-1, 1]) regardless of
# what the observed sample happens to span. spyndex itself doesn't expose this.
INDEX_DOMAIN: dict[str, tuple[float, float]] = {
    "NDVI":  (-1.0, 1.0),
    "NDWI":  (-1.0, 1.0),
    "NDMI":  (-1.0, 1.0),
    "NBR":   (-1.0, 1.0),
    "NBR2":  (-1.0, 1.0),
    "NDSI":  (-1.0, 1.0),
    "NDBI":  (-1.0, 1.0),
    "EVI":   (-1.0, 1.0),
    "SAVI":  (-1.5, 1.5),
    "MSAVI": (-1.0, 1.0),
    "GNDVI": (-1.0, 1.0),
    "GCI":   (-1.0, 10.0),
    "MCARI": (-1.0, 1.0),
    "NDRE":  (-1.0, 1.0),
    # Landsat thermal (spyndex divides T by 10000 inside these formulas)
    "NDVIT": (-1.0, 1.0),
    "SAVIT": (-1.5, 1.5),
    "NBRT1": (-1.0, 1.0),
    "NBRT2": (-1.0, 1.0),
    "NBRT3": (-1.0, 1.0),
    # (ARI and CSIT had [-1, 1] / [0, 5] here; measured reference surfaces span
    # 1–11 and 15–465, so both now take the observed range.)
    # Sentinel-1 dual-pol ratios
    "NDPolI": (-1.0, 1.0),
    "RFDI":   (-1.0, 1.0),
    "DpRVIVV": (0.0, 2.0),
    "DpRVIHH": (0.0, 2.0),
    "VDDPI":  (1.0, 2.0),
}


def index_domain(name: str) -> tuple[float, float] | None:
    """Look up the canonical [min, max] domain for ``name`` if known."""
    return INDEX_DOMAIN.get(name)



def _coerce_reflectance(da: xr.DataArray) -> xr.DataArray:
    """Convert raw Sentinel-2 L2A digital numbers to surface reflectance in [0, 1]."""
    arr = da.astype("float32") / S2_REFLECTANCE_SCALE
    arr.attrs.update(da.attrs)
    return arr


def apply_value_scale(da: xr.DataArray, asset: str, platform: Platform) -> xr.DataArray:
    """Digital numbers → physical values for ``asset`` on ``platform``.

    ``value = DN * scale + offset``; the platform's nodata DN (if any) becomes
    NaN first so it never leaks into the index.
    """
    arr = da.astype("float32")
    nodata = platform.nodata_for(asset)
    if nodata is not None:
        arr = arr.where(arr != nodata)
    scale, offset = platform.scale_for(asset)
    out = arr * scale + offset
    out.attrs.update(da.attrs)
    return out


def quality_mask(mask_da: xr.DataArray, platform: Platform) -> xr.DataArray:
    """Boolean keep-mask (True = valid) from the platform's quality raster."""
    spec = platform.mask
    if spec is None:
        raise ValueError(f"platform {platform.id} has no quality mask")
    if spec.kind == "scl-classes":
        bad = xr.apply_ufunc(np.isin, mask_da.astype("int16"), kwargs={"test_elements": list(spec.values)})
    else:  # qa-bits
        bits = mask_da.fillna(0).astype("uint32")
        bad = (bits & np.uint32(spec.bitmask)) != 0
    return ~bad


def band_params(index_name: str) -> list[str]:
    """Spectral band parameters of an index in spyndex's documented order.

    Everything in ``spec.bands`` that isn't a constant. (Radar polarisations
    ``VV``/``VH``/``HH``/``HV`` are not listed in ``spyndex.bands``, so that
    table can't be used as the filter.)
    """
    spec = spyndex.indices[index_name]
    return [p for p in spec.bands if p not in spyndex.constants]


def constant_defaults(index_name: str, platform: Platform | None = None) -> dict[str, float | None]:
    """spyndex constant defaults for ``index_name``, overlaid with the platform's
    band-centre wavelengths (``lambdaG``, ``lambdaR``, … have no spyndex default)."""
    spec = spyndex.indices[index_name]
    out: dict[str, float | None] = {}
    for p in spec.bands:
        if p in spyndex.constants:
            out[p] = spyndex.constants[p].default
            if platform is not None and p in platform.wavelength_nm:
                out[p] = platform.wavelength_nm[p]
    return out


def compute_index(
    asset_id: str,
    index_cfg: Index,
    band_paths: dict[str, Path],
    indices_dir: Path,
    scl_path: Path | None = None,
    *,
    out_stem: str | None = None,
    platform: Platform | None = None,
    mask_path: Path | None = None,
) -> tuple[Path, dict]:
    """Compute the configured index and persist a GeoTIFF + stats sidecar.

    ``band_paths`` maps STAC asset keys (e.g. "red", "nir") to local files.
    ``index_cfg.bands`` maps spyndex parameters (e.g. "N", "R") to those asset keys.

    ``platform`` selects DN scaling, nodata, wavelength constants and the
    quality-mask semantics; ``None`` keeps the historical Sentinel-2 behavior.
    ``mask_path`` (or the legacy ``scl_path``) points at the platform's quality
    raster; when given, masked pixels become NaN.

    ``out_stem`` overrides the output filename stem (``<stem>.tif`` /
    ``<stem>_stats.json``). Defaults to ``"{asset_id}_{index}"``, preserving
    the single-spread layout; the preview path passes ``out_stem=<index>`` to
    land one raster per ``(location, index)`` regardless of colormap.
    """
    stem = out_stem if out_stem is not None else f"{asset_id}_{index_cfg.name}"
    if index_cfg.name not in spyndex.indices:
        raise KeyError(f"Unknown index {index_cfg.name!r}")

    indices_dir.mkdir(parents=True, exist_ok=True)
    out_tif = indices_dir / f"{stem}.tif"
    stats_json = indices_dir / f"{stem}_stats.json"
    # Cache hit: reuse the computed raster + sidecar. This is what lets a
    # batch over many colormap variants of one (location, index) — or the
    # preview matrix — pay the spyndex compute once. Same policy as the
    # band cache in fetch_bands (keyed on the stem / asset_id).
    if out_tif.exists() and stats_json.exists():
        log.info("Using cached index raster %s", out_tif)
        return out_tif, json.loads(stats_json.read_text())

    spec = spyndex.indices[index_cfg.name]
    log.info("Computing %s = %s", spec.short_name, spec.formula)

    # Load required band arrays into the spyndex parameter space.
    params: dict[str, xr.DataArray] = {}
    template: xr.DataArray | None = None
    for spyndex_param, asset_key in index_cfg.bands.items():
        if asset_key not in band_paths:
            raise KeyError(
                f"Index {index_cfg.name} needs asset {asset_key!r} for parameter "
                f"{spyndex_param!r}; available: {sorted(band_paths)}"
            )
        raw = open_band(band_paths[asset_key])
        da = apply_value_scale(raw, asset_key, platform) if platform is not None else _coerce_reflectance(raw)
        params[spyndex_param] = da
        if template is None:
            template = da

    assert template is not None  # at least one band

    # Several indices (EVI, SAVI, …) also require numeric constants (g, C1, C2,
    # L, …) that aren't spectral bands, so the pool's band mapping doesn't cover
    # them. Supply spyndex's library defaults (and the platform's band-centre
    # wavelengths) for any required constant the caller didn't provide.
    for param, value in constant_defaults(index_cfg.name, platform).items():
        if param not in params:
            if value is None:
                raise ValueError(
                    f"Index {index_cfg.name} needs constant {param!r}, which has no default"
                    + ("" if platform is not None else " (pass platform= to supply wavelengths)")
                )
            params[param] = value

    result = spyndex.computeIndex(index=index_cfg.name, params=params)

    # spyndex returns a DataArray when given DataArrays; ensure spatial metadata.
    if not isinstance(result, xr.DataArray):
        result = xr.DataArray(result, coords=template.coords, dims=template.dims)
    result = result.rio.write_crs(template.rio.crs)
    result.attrs["index"] = index_cfg.name
    result.attrs["formula"] = spec.formula
    result.attrs["long_name"] = spec.long_name

    # Optional quality mask (Sentinel-2 SCL classes, Landsat QA_PIXEL bits).
    mask_file = mask_path if mask_path is not None else scl_path
    mask_platform = platform if platform is not None else PLATFORMS["sentinel-2-l2a"]
    if mask_file is not None and mask_file.exists() and mask_platform.mask is not None:
        mask_da = open_band(mask_file)
        mask_aligned = mask_da.rio.reproject_match(result)
        result = result.where(quality_mask(mask_aligned, mask_platform))

    result.rio.to_raster(out_tif, compress="deflate", dtype="float32")

    stats = _summarize(result)
    sidecar = {
        "asset_id": asset_id,
        "platform": platform.id if platform is not None else "sentinel-2-l2a",
        "index": index_cfg.name,
        "long_name": spec.long_name,
        "formula": spec.formula,
        "bands_used": dict(index_cfg.bands),
        "stats": stats,
        "raster": str(out_tif),
    }
    stats_json.write_text(json.dumps(sidecar, indent=2))
    log.info(
        "%s stats: min=%.3f mean=%.3f max=%.3f valid=%.1f%%",
        index_cfg.name,
        stats["min"],
        stats["mean"],
        stats["max"],
        100 * stats["valid_fraction"],
    )
    return out_tif, sidecar


def _summarize(da: xr.DataArray) -> dict:
    arr = np.asarray(da.values, dtype="float64")
    finite = np.isfinite(arr)
    valid = arr[finite]
    if valid.size == 0:
        return {
            "min": float("nan"),
            "max": float("nan"),
            "mean": float("nan"),
            "std": float("nan"),
            "p1": float("nan"),
            "p50": float("nan"),
            "p99": float("nan"),
            "valid_fraction": 0.0,
            "n_pixels": int(arr.size),
        }
    p1, p50, p99 = np.percentile(valid, [1, 50, 99])
    return {
        "min": float(valid.min()),
        "max": float(valid.max()),
        "mean": float(valid.mean()),
        "std": float(valid.std()),
        "p1": float(p1),
        "p50": float(p50),
        "p99": float(p99),
        "valid_fraction": float(valid.size / arr.size),
        "n_pixels": int(arr.size),
    }
