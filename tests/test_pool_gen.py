"""Indices pool regeneration: admission rules and merge safety."""

from __future__ import annotations

from spectralcular.pool import IndexPool, IndexPoolEntry, ReviewStatus, load_index_pool
from spectralcular.pool_gen import (
    APPROVED_SEED,
    counts_by_platform,
    dump_index_pool,
    eligible_entries,
    eligible_for_platform,
    regenerate,
)
from spectralcular.platforms import PLATFORMS


def test_admission_rules():
    by_name = eligible_entries()
    plat = {n: p for n, (p, _t, _b) in by_name.items()}
    # Sentinel-2 takes the optical catalog incl. the coastal-band indices.
    for n in ("NDVI", "EVI", "NDWI", "NHFD", "NPCI", "SIPI", "mND705", "mSR705", "CRSWIR", "FAI"):
        assert plat[n] == "sentinel-2-l2a", n
    # Landsat only admits thermal indices; NDVI is not duplicated there.
    landsat = {n for n, _t, _b in eligible_for_platform(PLATFORMS["landsat-c2-l2"])}
    assert "NDVI" not in landsat and {"NDVIT", "EBBI", "NBLIOLI", "DBI"} <= landsat
    assert len(landsat) == 20
    # Sentinel-1: dual-pol radar indices only.
    radar = {n for n, _t, _b in eligible_for_platform(PLATFORMS["sentinel-1-rtc"])}
    assert "QpRVI" not in radar and {"NDPolI", "RFDI", "VHVVR"} <= radar
    assert len(radar) == 12
    # Kernel indices never enter.
    assert not any(n.startswith("k") and n[1:2].isupper() for n in by_name)


def test_regenerate_from_scratch_seeds_approved():
    pool, report = regenerate(None)
    assert len(pool.entries) == 265
    assert counts_by_platform(pool) == {"sentinel-2-l2a": 233, "landsat-c2-l2": 20, "sentinel-1-rtc": 12}
    approved = {e.name for e in pool.entries if e.status == ReviewStatus.approved}
    assert approved == APPROVED_SEED
    assert not report.kept and not report.removed and len(report.added) == 265


def test_regenerate_is_merge_safe(tmp_path):
    existing = IndexPool(entries=[
        IndexPoolEntry(name="NDVI", theme="vegetation", bands={"N": "nir", "R": "red"},
                       status=ReviewStatus.approved, notes="hand-written", expressive_in=["amazon"]),
        # Stale band map + theme: must be refreshed but keep its editorial state.
        IndexPoolEntry(name="NBR", theme="vegetation", bands={"N": "nir", "S2": "swir22"},
                       status=ReviewStatus.rejected, notes="too similar to NBR2"),
    ])
    pool, report = regenerate(existing)
    by = pool.by_name()
    assert by["NDVI"].status == ReviewStatus.approved
    assert by["NDVI"].notes == "hand-written" and by["NDVI"].expressive_in == ["amazon"]
    assert by["NBR"].status == ReviewStatus.rejected and by["NBR"].notes == "too similar to NBR2"
    assert by["NBR"].theme == "burn"
    assert set(report.kept) == {"NDVI", "NBR"} and report.changed == ["NBR"]
    # New entries arrive as proposed, never approved, when merging.
    assert by["EVI"].status == ReviewStatus.proposed
    assert "EVI" in report.added and not report.removed

    out = tmp_path / "indices.yaml"
    dump_index_pool(pool, out)
    reloaded = load_index_pool(out)
    assert [e.name for e in reloaded.entries] == [e.name for e in pool.entries]
    assert reloaded.by_name()["NDVIT"].platform == "landsat-c2-l2"
    assert "&id" not in out.read_text()


def test_regenerate_reports_removed_entries():
    # An entry whose index is not eligible any more (kernel indices are excluded).
    stale = IndexPoolEntry.model_construct(
        name="kNDVI", theme="vegetation", platform="sentinel-2-l2a", bands={},
        status=ReviewStatus.proposed, notes="", expressive_in=[],
    )
    existing = IndexPool.model_construct(entries=[stale])
    _pool, report = regenerate(existing)
    assert report.removed == ["kNDVI"]
