"""spectralcular colormaps import: heuristics + merge-safety."""

from __future__ import annotations

from spectralcular.catalog import CatalogRecord, load_catalog
from spectralcular.colormap_import import (
    infer_pairs_with,
    infer_semantic_register,
    map_type,
    merge_catalog_into_pool,
    propose_entry,
)
from spectralcular.pool import ColormapPool, ColormapPoolEntry, ColormapSource, ReviewStatus


def _rec(**over) -> CatalogRecord:
    base = dict(id="x.y", name="y", type="sequential", collection="x", attribution="A", license="MIT",
                url="", tags=(), notes="", stops_count=256, row=0)
    base.update(over)
    return CatalogRecord(**base)


def test_map_type_folds_multi_sequential():
    assert map_type("multi-sequential") == "sequential"
    assert map_type("diverging") == "diverging"


def test_register_heuristics_table():
    assert infer_semantic_register(_rec(tags=("terrain",)), "sequential")[0] == "earth"
    assert infer_semantic_register(_rec(type="multi-sequential"), "sequential")[0] == "earth"
    assert infer_semantic_register(_rec(tags=("bathymetry",)), "sequential")[0] == "water"
    assert infer_semantic_register(
        _rec(tags=("blue",), hue_quadrants=("azure", "blue", "cyan", "green")), "sequential")[0] == "water"
    assert infer_semantic_register(_rec(id="cmasher.arctic"), "sequential")[0] == "snow_ice"
    assert infer_semantic_register(_rec(hue_quadrants=("red", "orange", "yellow")), "sequential")[0] == "burn"
    assert infer_semantic_register(_rec(hue_quadrants=("green", "chartreuse", "mint", "cyan")), "sequential")[0] == "vegetation"
    assert infer_semantic_register(_rec(tags=("perceptually-uniform",)), "diverging")[0] == "neutral_analytical"
    assert infer_semantic_register(_rec(collection="tol"), "sequential")[0] == "neutral_analytical"
    assert infer_semantic_register(_rec(tags=("rainbow",)), "sequential")[0] == "experimental_aesthetic"
    assert infer_semantic_register(_rec(type="categorical"), "categorical")[0] == "experimental_aesthetic"


def test_pairs_follow_register_and_type():
    assert infer_pairs_with("water", "sequential") == ["NDWI", "NDMI"]
    assert infer_pairs_with("water", "diverging") == ["NDWI"]
    assert infer_pairs_with("vegetation", "sequential") == ["NDVI", "EVI"]
    assert infer_pairs_with("neutral_analytical", "sequential") == []


def test_propose_entry_is_proposed_with_provenance():
    rec = load_catalog().get("crameri.roma")
    e = propose_entry(rec)
    assert e.name == "colorcurious:crameri.roma" and e.status == ReviewStatus.proposed
    assert e.type == "diverging" and e.print_safety == "unverified"
    assert e.source is not None and e.source.license == "CC-BY-4.0" and e.source.catalog_id == "crameri.roma"
    assert "auto-tagged" in e.notes


def _pool():
    return ColormapPool(entries=[
        ColormapPoolEntry(name="cmocean:algae", type="sequential", semantic_register="vegetation",
                          pairs_with=["NDVI", "EVI"], status=ReviewStatus.approved, notes="hand note"),
        ColormapPoolEntry(name="cmocean:algae_r", type="sequential", semantic_register="vegetation",
                          status=ReviewStatus.rejected),
        ColormapPoolEntry(name="bids:viridis", type="sequential", semantic_register="neutral_analytical",
                          status=ReviewStatus.approved, source=ColormapSource(collection="hand", license="X")),
    ])


def test_merge_enriches_without_touching_reviewer_fields():
    cat = load_catalog()
    merged, rep = merge_catalog_into_pool(_pool(), cat)
    by = {e.name: e for e in merged.entries}
    algae = by["cmocean:algae"]
    assert algae.status == ReviewStatus.approved and algae.notes == "hand note"
    assert algae.semantic_register == "vegetation" and algae.pairs_with == ["NDVI", "EVI"]
    assert algae.source is not None and algae.source.collection == "cmocean" and algae.source.catalog_id == "cmocean.algae"
    assert algae.tags  # unioned from the catalog
    # the _r twin inherits provenance from the base record
    assert by["cmocean:algae_r"].source is not None and by["cmocean:algae_r"].source.catalog_id == "cmocean.algae"
    assert by["cmocean:algae_r"].status == ReviewStatus.rejected
    # an existing source is never overwritten
    assert by["bids:viridis"].source.collection == "hand"
    assert "cmocean:algae" in rep.enriched and "cmocean:algae_r" in rep.enriched
    # no duplicate added for deduped records
    assert "colorcurious:cmocean.algae" not in by and "colorcurious:matplotlib.viridis" not in by
    assert all(by[n].status == ReviewStatus.proposed for n in rep.added)
    assert rep.skipped_categorical and not rep.problems


def test_merge_is_idempotent_and_categorical_is_opt_in():
    cat = load_catalog()
    once, rep1 = merge_catalog_into_pool(_pool(), cat)
    twice, rep2 = merge_catalog_into_pool(once, cat)
    assert rep2.added == [] and rep2.enriched == []
    assert [e.name for e in twice.entries] == [e.name for e in once.entries]
    with_cat, rep3 = merge_catalog_into_pool(once, cat, include_categorical=True)
    assert len(rep3.added) == len(rep1.skipped_categorical) > 0
    assert all(e.type == "categorical" for e in with_cat.entries if e.name in rep3.added)
