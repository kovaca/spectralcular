"""Smoke-test the YAML config loader against the MVP config."""

from __future__ import annotations

from pathlib import Path

import pytest

from spectralcular.config import load_config


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_amazon_config_loads():
    cfg = load_config(REPO_ROOT / "examples" / "spreads" / "spread_amazon_ndvi_viridis.yaml")
    assert cfg.asset_id == "amazon_basin_ndvi_viridis"
    assert cfg.index.name == "NDVI"
    assert cfg.colormap.name == "bids:viridis"
    assert cfg.colormap.normalization.method == "percentile"


def test_invalid_bbox_rejected():
    with pytest.raises(Exception):
        load_config_inline(bbox=[1.0, 1.0, 0.0, 0.0])  # inverted


def load_config_inline(bbox):
    """Helper: build a tiny in-memory yaml with the given bbox to exercise validation."""
    from spectralcular.config import SpreadConfig

    raw = {
        "asset_id": "x",
        "location": {"name": "X", "bbox": list(bbox)},
        "imagery": {"source": "sentinel-2-l2a", "date_range": "2023-07-01/2023-08-31", "cloud_cover_max": 10},
        "index": {"name": "NDVI", "bands": {"N": "nir", "R": "red"}},
        "colormap": {"name": "bids:viridis"},
    }
    return SpreadConfig.model_validate(raw)
