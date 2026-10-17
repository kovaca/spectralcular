"""Colormap pool entries: inline colors, provenance, YAML round trip."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from spectralcular.pool import (
    ColormapPool,
    ColormapPoolEntry,
    ColormapSource,
    ReviewStatus,
    dump_colormap_pool,
    load_colormap_pool,
)


def test_inline_colors_entry_validates_and_short_name():
    e = ColormapPoolEntry(name="custom:teal", type="sequential", semantic_register="water",
                          colors=["#003333", "#33cccc", "#ffffff"])
    assert e.short_name == "teal" and e.colors is not None


def test_inline_colors_rejects_one_stop_and_bad_hex():
    with pytest.raises(ValidationError):
        ColormapPoolEntry(name="custom:x", type="sequential", semantic_register="water", colors=["#000"])
    with pytest.raises(ValidationError):
        ColormapPoolEntry(name="custom:x", type="sequential", semantic_register="water",
                          colors=["#000", "notacolor"])


def test_unknown_name_without_colors_rejected():
    with pytest.raises(ValidationError):
        ColormapPoolEntry(name="nope:zzz", type="sequential", semantic_register="water")
    with pytest.raises(ValidationError):
        ColormapPoolEntry(name="custom:zzz", type="sequential", semantic_register="water")


def test_catalog_name_validates():
    e = ColormapPoolEntry(name="colorcurious:crameri.roma", type="diverging", semantic_register="neutral_analytical")
    assert e.short_name == "roma"


def test_duplicate_names_rejected():
    with pytest.raises(ValidationError):
        ColormapPool(entries=[
            ColormapPoolEntry(name="bids:viridis", type="sequential", semantic_register="neutral_analytical"),
            ColormapPoolEntry(name="bids:viridis", type="sequential", semantic_register="neutral_analytical"),
        ])


def test_dump_round_trips_without_anchors(tmp_path):
    shared = ["NDVI", "EVI"]
    pool = ColormapPool(entries=[
        ColormapPoolEntry(name="cmocean:algae", type="sequential", semantic_register="vegetation",
                          pairs_with=shared, status=ReviewStatus.approved,
                          source=ColormapSource(collection="cmocean", catalog_id="cmocean.algae", license="MIT"),
                          tags=["a", "b"]),
        ColormapPoolEntry(name="cmocean:algae_r", type="sequential", semantic_register="vegetation",
                          pairs_with=shared),
        ColormapPoolEntry(name="custom:teal", type="sequential", semantic_register="water",
                          colors=["#003333", "#ffffff"]),
    ])
    path = tmp_path / "colormaps.yaml"
    dump_colormap_pool(pool, path, header="# hello")
    text = path.read_text()
    assert text.startswith("# hello\n\n")
    assert "&id" not in text and "*id" not in text
    assert "source:" in text and "colors:" in text
    assert text.count("source:") == 1  # None fields are dropped
    loaded = load_colormap_pool(path)
    assert [e.name for e in loaded.entries] == ["cmocean:algae", "cmocean:algae_r", "custom:teal"]
    assert loaded.entries[0].source.catalog_id == "cmocean.algae"
    assert loaded.entries[2].colors == ["#003333", "#ffffff"]
