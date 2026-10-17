"""Vendored colorcurio.us colormap catalog.

The catalog is a snapshot of two files from the colorcurious repo
(``static/colormaps.json`` + ``static/colormaps-sprite.png``), kept under
``src/spectralcular/_data/colorcurious/``. The JSON carries metadata and
perceptual metrics only; **the colors live in the sprite**: row ``i`` of the
256-wide PNG is record ``i`` of the (id-sorted) JSON array.

Pool entries reference a record as ``colorcurious:<collection>.<name>`` (an
optional ``_r`` suffix reverses it). :func:`sync_catalog` refreshes the
snapshot from a local checkout and regenerates ``NOTICE.md`` with the
per-collection attribution the upstream licenses require.
"""

from __future__ import annotations

import datetime as _dt
import functools
import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

CATALOG_DIR = Path(__file__).resolve().parent / "_data" / "colorcurious"
CATALOG_NAMESPACE = "colorcurious"
SPRITE_WIDTH = 256

# colorcurious collection → the palettize/cmap namespace the hand-built pool
# used for the same data, so dedupe can match `matplotlib.viridis` ↔ `bids:viridis`.
_COLLECTION_TO_NAMESPACE = {"matplotlib": "bids"}
_SPECIAL_PALETTIZE_NAMES = {"matplotlib.cividis": "cividis:cividis"}

# Fields kept when the JSON is slimmed for vendoring (see :func:`slim_record`).
_KEEP_PERCEPTUAL = {
    "lightness": ("monotonic",),
    "hue": ("quadrants", "path"),
    "chroma": ("max",),
    "diverging": ("pivot_t",),
}


class CatalogError(Exception):
    """Raised for a missing catalog file or an unknown record id."""


@dataclass(frozen=True)
class CatalogRecord:
    id: str
    name: str
    type: str  # colorcurious type incl. "multi-sequential"
    collection: str
    attribution: str
    license: str
    url: str
    tags: tuple[str, ...]
    notes: str
    stops_count: int
    row: int
    lightness_monotonic: bool | None = None
    hue_quadrants: tuple[str, ...] = ()
    hue_path: str | None = None
    endpoint_temp: tuple[str | None, str | None] = (None, None)
    chroma_max: float | None = None
    diverging_pivot_t: float | None = None
    cvd_preserves_order: dict[str, bool] = field(default_factory=dict)
    cvd_uniformity: dict[str, float] = field(default_factory=dict)

    @property
    def short_name(self) -> str:
        return self.id.split(".", 1)[-1]

    @property
    def pool_name(self) -> str:
        return f"{CATALOG_NAMESPACE}:{self.id}"


@dataclass(frozen=True)
class Catalog:
    records: dict[str, CatalogRecord]
    order: tuple[str, ...]
    sprite: np.ndarray  # (N, 256, 3) uint8
    meta: dict

    def get(self, catalog_id: str) -> CatalogRecord:
        try:
            return self.records[catalog_id]
        except KeyError:
            raise CatalogError(
                f"unknown colorcurious colormap {catalog_id!r} "
                f"(catalog has {len(self.records)} records; run `spectralcular colormaps sync`?)"
            ) from None

    def __len__(self) -> int:
        return len(self.records)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _record_from_json(raw: dict, row: int) -> CatalogRecord:
    src = raw.get("source") or {}
    perc = raw.get("perceptual") or {}
    light = perc.get("lightness") or {}
    hue = perc.get("hue") or {}
    chroma = perc.get("chroma") or {}
    div = perc.get("diverging") or None
    temp = perc.get("endpoint_temp") or {}
    cvd = raw.get("cvd") or {}
    return CatalogRecord(
        id=raw["id"],
        name=raw.get("name") or raw["id"],
        type=raw["type"],
        collection=src.get("collection") or raw["id"].split(".", 1)[0],
        attribution=src.get("attribution") or "",
        license=src.get("license") or "",
        url=src.get("url") or "",
        tags=tuple(raw.get("tags") or ()),
        notes=raw.get("notes") or "",
        stops_count=int(raw.get("stops_count") or SPRITE_WIDTH),
        row=row,
        lightness_monotonic=light.get("monotonic"),
        hue_quadrants=tuple(hue.get("quadrants") or ()),
        hue_path=hue.get("path"),
        endpoint_temp=(temp.get("start"), temp.get("end")),
        chroma_max=chroma.get("max"),
        diverging_pivot_t=(div or {}).get("pivot_t") if div else None,
        cvd_preserves_order={k: bool(v.get("preserves_order")) for k, v in cvd.items() if isinstance(v, dict)},
        cvd_uniformity={k: float(v["uniformity"]) for k, v in cvd.items()
                        if isinstance(v, dict) and v.get("uniformity") is not None},
    )


@functools.lru_cache(maxsize=4)
def _load_catalog_cached(catalog_dir: str) -> Catalog:
    d = Path(catalog_dir)
    json_path, png_path, meta_path = d / "colormaps.json", d / "colormaps-sprite.png", d / "manifest.json"
    if not json_path.exists() or not png_path.exists():
        raise CatalogError(f"colorcurious catalog not found under {d} (expected colormaps.json + colormaps-sprite.png)")
    raw = json.loads(json_path.read_text())
    sprite = np.asarray(Image.open(png_path).convert("RGB"), dtype="uint8")
    if sprite.ndim != 3 or sprite.shape[1] != SPRITE_WIDTH:
        raise CatalogError(f"sprite has shape {sprite.shape}; expected (N, {SPRITE_WIDTH}, 3)")
    if sprite.shape[0] != len(raw):
        raise CatalogError(f"sprite has {sprite.shape[0]} rows but colormaps.json has {len(raw)} records")
    records = {r["id"]: _record_from_json(r, i) for i, r in enumerate(raw)}
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    return Catalog(records=records, order=tuple(r["id"] for r in raw), sprite=sprite, meta=meta)


def load_catalog(catalog_dir: Path | str = CATALOG_DIR) -> Catalog:
    """Load (and cache) the vendored catalog."""
    return _load_catalog_cached(str(Path(catalog_dir).resolve()))


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

def decode_stops(catalog: Catalog, record: CatalogRecord) -> np.ndarray:
    """Return the record's stops as ``(n, 3)`` uint8 sRGB.

    Mirrors colorcurious ``sampleRow``: stop ``k`` of ``n`` reads column
    ``min(255, floor(((k + 0.5) / n) * 256))``. For continuous maps
    (``n == 256``) this is the full row verbatim.
    """
    n = record.stops_count
    cols = np.minimum(SPRITE_WIDTH - 1, (((np.arange(n) + 0.5) / n) * SPRITE_WIDTH).astype(int))
    return catalog.sprite[record.row, cols, :].copy()


def stops_hex(catalog: Catalog, record: CatalogRecord) -> list[str]:
    return [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in decode_stops(catalog, record).tolist()]


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def is_catalog_name(name: str) -> bool:
    return name.startswith(CATALOG_NAMESPACE + ":")


def split_name(pool_name: str) -> tuple[str, bool]:
    """``"colorcurious:crameri.roma_r"`` → ``("crameri.roma", True)``."""
    if not is_catalog_name(pool_name):
        raise CatalogError(f"{pool_name!r} is not a {CATALOG_NAMESPACE}: name")
    cid = pool_name.split(":", 1)[1]
    if cid.endswith("_r"):
        return cid[:-2], True
    return cid, False


def palettize_name_for(catalog_id: str) -> str:
    """The palettize/cmap preset id the hand-built pool would have used for
    this catalog record (dedupe key): lower-cased ``<namespace>:<name>``."""
    if catalog_id in _SPECIAL_PALETTIZE_NAMES:
        return _SPECIAL_PALETTIZE_NAMES[catalog_id]
    collection, _, short = catalog_id.partition(".")
    ns = _COLLECTION_TO_NAMESPACE.get(collection, collection)
    return f"{ns}:{short.lower()}"


# ---------------------------------------------------------------------------
# Sync from a colorcurious checkout
# ---------------------------------------------------------------------------

def slim_record(raw: dict) -> dict:
    """Drop the bulky per-step metrics; keep what the pool/review sheet uses."""
    out = {k: raw.get(k) for k in ("id", "name", "type", "source", "tags", "notes", "stops_count")}
    perc = raw.get("perceptual") or {}
    slim_perc: dict = {}
    for section, keys in _KEEP_PERCEPTUAL.items():
        block = perc.get(section)
        if isinstance(block, dict):
            slim_perc[section] = {k: block.get(k) for k in keys if k in block}
        elif section == "diverging":
            slim_perc[section] = None
    if isinstance(perc.get("endpoint_temp"), dict):
        slim_perc["endpoint_temp"] = perc["endpoint_temp"]
    out["perceptual"] = slim_perc
    cvd = raw.get("cvd") or {}
    out["cvd"] = {k: {"preserves_order": v.get("preserves_order"), "uniformity": v.get("uniformity")}
                  for k, v in cvd.items() if isinstance(v, dict)}
    return out


@dataclass
class SyncReport:
    dest: Path
    record_count: int
    source_commit: str | None
    sprite_sha256: str
    files: list[Path]
    licenses: dict[str, int]

    def summary(self) -> str:
        lic = ", ".join(f"{k} ×{v}" for k, v in sorted(self.licenses.items(), key=lambda kv: -kv[1]))
        return (f"synced {self.record_count} colormaps → {self.dest} "
                f"(source {self.source_commit or 'unknown'}; sprite {self.sprite_sha256[:12]})\n  licenses: {lic}")


def _git_head(repo: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True, timeout=10)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001 — a missing git is not an error for sync
        return None


def write_notice(raw_records: list[dict], path: Path, *, source_commit: str | None) -> None:
    """Aggregate upstream attribution per collection into a NOTICE file."""
    by_coll: dict[str, dict] = {}
    for r in raw_records:
        src = r.get("source") or {}
        c = src.get("collection") or r["id"].split(".", 1)[0]
        slot = by_coll.setdefault(c, {"n": 0, "attribution": set(), "license": set(), "url": set()})
        slot["n"] += 1
        for k in ("attribution", "license", "url"):
            if src.get(k):
                slot[k].add(src[k])
    lines = [
        "# Third-party colormap notices (colorcurious catalog)",
        "",
        "The colormaps under this directory are a snapshot of the catalog published at",
        "https://colorcurio.us/colormaps"
        + (f" (source commit `{source_commit}`)" if source_commit else "") + ".",
        "Color data is 8-bit sRGB; each record carries its upstream attribution and",
        "license in `colormaps.json` (`source.attribution`, `source.license`, `source.url`).",
        "Collections licensed CC-BY-4.0 require attribution wherever their colormaps are",
        "reproduced, including a printed colophon.",
        "",
        "| collection | maps | license | attribution | url |",
        "|---|---:|---|---|---|",
    ]
    for c in sorted(by_coll):
        s = by_coll[c]
        lines.append(
            f"| {c} | {s['n']} | {'; '.join(sorted(s['license']))} | "
            f"{'; '.join(sorted(s['attribution']))} | {' '.join(sorted(s['url']))} |"
        )
    path.write_text("\n".join(lines) + "\n")


def sync_catalog(src_root: Path | str, dest: Path | str = CATALOG_DIR, *, slim: bool = True) -> SyncReport:
    """Copy the catalog from a colorcurious checkout (or its ``static/`` dir)."""
    src = Path(src_root).expanduser().resolve()
    static = src / "static" if (src / "static").is_dir() else src
    json_src, png_src = static / "colormaps.json", static / "colormaps-sprite.png"
    if not json_src.exists() or not png_src.exists():
        raise CatalogError(f"no colormaps.json + colormaps-sprite.png under {static}")

    raw = json.loads(json_src.read_text())
    ids = [r["id"] for r in raw]
    if ids != sorted(ids, key=str.lower) and ids != sorted(ids):
        # colorcurious sorts with localeCompare; either ordering is fine as long
        # as the sprite was written in the same order — which it is by construction.
        pass
    png_bytes = png_src.read_bytes()
    sprite = np.asarray(Image.open(png_src).convert("RGB"))
    if sprite.shape[0] != len(raw):
        raise CatalogError(f"sprite rows ({sprite.shape[0]}) != records ({len(raw)})")

    d = Path(dest)
    d.mkdir(parents=True, exist_ok=True)
    out_json = d / "colormaps.json"
    out_png = d / "colormaps-sprite.png"
    out_meta = d / "manifest.json"
    out_notice = d / "NOTICE.md"

    records = [slim_record(r) for r in raw] if slim else raw
    out_json.write_text(json.dumps(records, separators=(",", ":"), ensure_ascii=False) + "\n")
    out_png.write_bytes(png_bytes)
    commit = _git_head(src)
    licenses: dict[str, int] = {}
    for r in raw:
        lic = (r.get("source") or {}).get("license") or "unknown"
        licenses[lic] = licenses.get(lic, 0) + 1
    meta = {
        "source": "https://colorcurio.us/colormaps",
        "source_repo": "https://github.com/kovaca/colorcurious",
        "source_commit": commit,
        "synced_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "record_count": len(raw),
        "slim": slim,
        "sprite_sha256": hashlib.sha256(png_bytes).hexdigest(),
        "licenses": licenses,
    }
    out_meta.write_text(json.dumps(meta, indent=1) + "\n")
    write_notice(raw, out_notice, source_commit=commit)
    _load_catalog_cached.cache_clear()
    return SyncReport(dest=d, record_count=len(raw), source_commit=commit,
                      sprite_sha256=meta["sprite_sha256"], files=[out_json, out_png, out_meta, out_notice],
                      licenses=licenses)
