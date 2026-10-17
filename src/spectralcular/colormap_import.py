"""Merge the vendored colorcurious catalog into ``config/pool/colormaps.yaml``.

``spectralcular colormaps import`` is the colormap analogue of ``spectralcular candidates generate``:
it *proposes* entries and never clobbers what a reviewer decided. Rules:

* A catalog record whose data the pool already has under a palettize name
  (``cmocean.algae`` ↔ ``cmocean:algae``, ``matplotlib.viridis`` ↔
  ``bids:viridis``) or under its ``colorcurious:`` name is **enriched** only:
  ``source`` is filled if empty and ``tags`` are unioned. ``status``, ``notes``,
  ``semantic_register``, ``pairs_with``, ``print_safety`` and ``type`` are left
  alone. ``_r`` twins inherit ``source`` from the base record.
* Every other record is **added** as ``status: proposed`` with a
  ``semantic_register`` / ``pairs_with`` guess from the documented heuristics
  below (the note records which rule fired so the reviewer can see it).
* Categorical palettes are skipped unless ``include_categorical=True``: the
  pipeline renders continuous indices and the picker hides them.
* Re-running is a no-op.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .catalog import Catalog, CatalogRecord, palettize_name_for
from .pool import (
    ColormapPool,
    ColormapPoolEntry,
    ColormapSource,
    ColormapType,
    ReviewStatus,
    SemanticRegister,
)

# ---------------------------------------------------------------------------
# Heuristics (pure, tabulated)
# ---------------------------------------------------------------------------

_EARTH_TAGS = {"terrain", "topographic", "topography", "land-and-sea", "geoscience", "elevation", "geology"}
_WATER_TAGS = {"bathymetry", "ocean", "oceanography", "water", "sea"}
_BLUEISH_TAGS = {"blue", "blue-green", "blues", "teal", "cyan"}
_BURN_TAGS = {"thermal", "heat", "fire", "warm", "hot"}
_NEUTRAL_TAGS = {"perceptually-uniform", "scientific", "cvd-friendly", "colorblind", "colorblind-safe",
                 "default-scientific", "gray", "gray", "grayscale", "single-hue", "anomaly", "uniform"}
_NEUTRAL_COLLECTIONS = {"tol", "colorbrewer", "moreland"}
_COOL_QUADRANTS = {"azure", "blue", "cyan"}
_GREEN_QUADRANTS = {"green", "chartreuse", "mint"}
_WARM_QUADRANTS = {"red", "orange", "yellow", "rose"}
_ICE_RE = re.compile(r"ice|arctic|freez|frost|glaci|snow", re.I)

PAIRS_BY_REGISTER: dict[str, dict[str, list[str]]] = {
    "water": {"sequential": ["NDWI", "NDMI"], "diverging": ["NDWI"], "cyclic": []},
    "vegetation": {"sequential": ["NDVI", "EVI"], "diverging": ["NDVI"], "cyclic": []},
    "burn": {"sequential": ["NBR"], "diverging": ["NBR"], "cyclic": []},
    "snow_ice": {"sequential": ["NDSI"], "diverging": ["NDSI"], "cyclic": []},
    "earth": {"sequential": ["BI"], "diverging": [], "cyclic": []},
    "neutral_analytical": {"sequential": [], "diverging": [], "cyclic": []},
    "experimental_aesthetic": {"sequential": [], "diverging": [], "cyclic": []},
}


def map_type(cc_type: str) -> ColormapType:
    """colorcurious ``multi-sequential`` folds into ``sequential`` (candidate
    pairing only distinguishes diverging from the rest); a tag preserves it."""
    if cc_type == "multi-sequential":
        return "sequential"
    if cc_type not in ("sequential", "diverging", "cyclic", "categorical"):
        raise ValueError(f"unknown colorcurious type {cc_type!r}")
    return cc_type  # type: ignore[return-value]


def _share(quadrants: tuple[str, ...], wanted: set[str]) -> float:
    if not quadrants:
        return 0.0
    return sum(1 for q in quadrants if q in wanted) / len(quadrants)


def infer_semantic_register(rec: CatalogRecord, mapped_type: ColormapType) -> tuple[SemanticRegister, str]:
    """Return ``(register, rule_name)``; first match wins."""
    tags = set(rec.tags)
    q = rec.hue_quadrants
    if mapped_type == "categorical":
        return "experimental_aesthetic", "categorical"
    if tags & _EARTH_TAGS or rec.type == "multi-sequential":
        return "earth", "earth-tags"
    if tags & _WATER_TAGS or (tags & _BLUEISH_TAGS and _share(q, _COOL_QUADRANTS) >= 0.5):
        return "water", "water-tags"
    if _ICE_RE.search(rec.short_name):
        return "snow_ice", "ice-name"
    if tags & _BURN_TAGS or (q and set(q) <= _WARM_QUADRANTS):
        return "burn", "warm-hues"
    if mapped_type == "sequential" and _share(q, _GREEN_QUADRANTS) >= 0.5:
        return "vegetation", "green-hues"
    if tags & _NEUTRAL_TAGS or rec.collection in _NEUTRAL_COLLECTIONS:
        return "neutral_analytical", "scientific-tags"
    return "experimental_aesthetic", "default"


def infer_pairs_with(register: SemanticRegister, mapped_type: ColormapType) -> list[str]:
    return list(PAIRS_BY_REGISTER.get(register, {}).get(mapped_type, []))


def source_for(rec: CatalogRecord) -> ColormapSource:
    return ColormapSource(collection=rec.collection, catalog_id=rec.id, attribution=rec.attribution,
                          license=rec.license, url=rec.url)


def propose_entry(rec: CatalogRecord) -> ColormapPoolEntry:
    mapped = map_type(rec.type)
    register, rule = infer_semantic_register(rec, mapped)
    tags = list(rec.tags)
    if rec.type == "multi-sequential" and "multi-sequential" not in tags:
        tags.append("multi-sequential")
    note = rec.notes.strip()
    note = (note + " " if note else "") + f"[auto-tagged from colorcurious; register via {rule}]"
    return ColormapPoolEntry(
        name=rec.pool_name,
        type=mapped,
        semantic_register=register,
        print_safety="unverified",
        pairs_with=infer_pairs_with(register, mapped),
        status=ReviewStatus.proposed,
        notes=note,
        source=source_for(rec),
        tags=tags,
    )


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

@dataclass
class ColormapImportReport:
    added: list[str] = field(default_factory=list)
    enriched: list[str] = field(default_factory=list)
    skipped_categorical: list[str] = field(default_factory=list)
    unchanged: int = 0
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"colormaps +{len(self.added)} · enriched {len(self.enriched)} · unchanged {self.unchanged} · "
                f"categorical skipped {len(self.skipped_categorical)} · problems {len(self.problems)}")


def _enrich(entry: ColormapPoolEntry, rec: CatalogRecord) -> tuple[ColormapPoolEntry, bool]:
    update: dict = {}
    if entry.source is None:
        update["source"] = source_for(rec)
    merged_tags = list(entry.tags) + [t for t in rec.tags if t not in entry.tags]
    if merged_tags != list(entry.tags):
        update["tags"] = merged_tags
    if not update:
        return entry, False
    return entry.model_copy(update=update), True


def merge_catalog_into_pool(
    pool: ColormapPool,
    catalog: Catalog,
    *,
    include_categorical: bool = False,
) -> tuple[ColormapPool, ColormapImportReport]:
    """Pure merge (no I/O); see module docstring for the rules."""
    report = ColormapImportReport()
    by_name: dict[str, ColormapPoolEntry] = {e.name: e for e in pool.entries}
    order = [e.name for e in pool.entries]
    touched: set[str] = set()

    for cid in catalog.order:
        rec = catalog.records[cid]
        pal = palettize_name_for(cid)
        cc = rec.pool_name
        twins = [n for n in (pal, cc, pal + "_r", cc + "_r") if n in by_name]
        if twins:
            for n in twins:
                new_entry, changed = _enrich(by_name[n], rec)
                by_name[n] = new_entry
                touched.add(n)
                if changed:
                    report.enriched.append(n)
            continue
        if rec.type == "categorical" and not include_categorical:
            report.skipped_categorical.append(cid)
            continue
        try:
            entry = propose_entry(rec)
        except Exception as e:  # noqa: BLE001 — collect, don't abort the import
            report.problems.append(f"{cid}: {e}")
            continue
        by_name[entry.name] = entry
        order.append(entry.name)
        report.added.append(entry.name)

    report.unchanged = len(pool.entries) - len(report.enriched)
    merged = ColormapPool(entries=[by_name[n] for n in order])
    return merged, report
