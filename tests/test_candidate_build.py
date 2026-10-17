"""candidate → SpreadConfig mapping carries reversed + inline colors."""

from __future__ import annotations

import yaml

from spectralcular.candidate_build import cache_key_for, candidate_to_spread_config, index_cache_key_for
from spectralcular.candidates import CandidateEntry, candidate_slug
from spectralcular.config import SpreadConfig
from spectralcular.pool import ColormapPoolEntry, IndexPoolEntry, LocationPoolEntry, ReviewStatus


def _loc():
    return LocationPoolEntry(id="amazon", name="Amazon", bbox=(-62.5, -4.5, -61.8, -3.9),
                             landscape_character="rainforest", themes=["vegetation"],
                             season_windows=["2023-07-01/2023-08-31"], status=ReviewStatus.approved)


def _idx():
    return IndexPoolEntry(name="NDVI", theme="vegetation", bands={"N": "nir", "R": "red"})


def test_reversed_and_colors_reach_the_spread_config(tmp_path):
    cmap = ColormapPoolEntry(name="custom:teal", type="sequential", semantic_register="water",
                             colors=["#003333", "#ffffff"])
    cand = CandidateEntry(id=candidate_slug("amazon", "NDVI", "custom:teal", reversed=True),
                          location_id="amazon", index="NDVI", colormap="custom:teal", reversed=True,
                          theme="vegetation", status=ReviewStatus.approved, ordinal=2)
    cfg = candidate_to_spread_config(cand, _loc(), _idx(), None, cmap_entry=cmap)
    assert cfg.asset_id == "amazon__NDVI__custom_teal_r"
    assert cfg.colormap.reversed is True and cfg.colormap.colors == ["#003333", "#ffffff"]
    assert cfg.layout.caption.endswith("NDVI")
    # standalone YAML round trip keeps both
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    back = SpreadConfig.model_validate(yaml.safe_load(p.read_text()))
    assert back.colormap.reversed and back.colormap.colors == ["#003333", "#ffffff"]


def test_defaults_are_forward_and_no_colors():
    cand = CandidateEntry(id="amazon__NDVI__bids_viridis", location_id="amazon", index="NDVI",
                          colormap="bids:viridis", theme="vegetation")
    cfg = candidate_to_spread_config(cand, _loc(), _idx(), None)
    assert cfg.colormap.reversed is False and cfg.colormap.colors is None


def test_mask_toggle_reaches_index_spread_config_and_splits_index_cache():
    masked = CandidateEntry(id="amazon__NDVI__bids_viridis", location_id="amazon", index="NDVI",
                            colormap="bids:viridis", theme="vegetation", scene_ids=["S2A_X"])
    unmasked = masked.model_copy(update={"mask_enabled": False})
    assert candidate_to_spread_config(masked, _loc(), _idx(), None).mask_enabled is True
    assert candidate_to_spread_config(unmasked, _loc(), _idx(), None).mask_enabled is False
    # same bands, different index rasters
    assert cache_key_for(masked) == cache_key_for(unmasked)
    assert index_cache_key_for(masked) == cache_key_for(masked)
    assert index_cache_key_for(unmasked) == cache_key_for(masked) + "__nomask"
