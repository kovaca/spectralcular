"""Normalization strategies that map index values into [0, 1] for colormap application."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Normalization


@dataclass
class NormResult:
    vmin: float
    vmax: float
    method: str
    description: str

    def normalize(self, arr: np.ndarray) -> np.ndarray:
        """Map ``arr`` into [0, 1] with NaNs preserved."""
        out = (arr - self.vmin) / (self.vmax - self.vmin)
        return np.clip(out, 0.0, 1.0)


def resolve_norm(arr: np.ndarray, cfg: Normalization) -> NormResult:
    """Pick concrete vmin/vmax from the configured strategy."""
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        raise ValueError("No finite values in array — cannot normalize.")

    if cfg.method == "fixed":
        if cfg.vmin is None or cfg.vmax is None:
            raise ValueError("fixed normalization requires both vmin and vmax")
        return NormResult(
            vmin=float(cfg.vmin),
            vmax=float(cfg.vmax),
            method="fixed",
            description=f"fixed [{cfg.vmin:g}, {cfg.vmax:g}]",
        )

    if cfg.method == "percentile":
        lower = cfg.lower if cfg.lower is not None else 2.0
        upper = cfg.upper if cfg.upper is not None else 98.0
        vmin, vmax = np.percentile(finite, [lower, upper])
        return NormResult(
            vmin=float(vmin),
            vmax=float(vmax),
            method="percentile",
            description=f"{lower:g}–{upper:g} percentile",
        )

    if cfg.method == "diverging":
        bound = float(np.max(np.abs(finite)))
        return NormResult(
            vmin=-bound,
            vmax=bound,
            method="diverging",
            description=f"diverging ±{bound:.3g} around 0",
        )

    raise ValueError(f"Unknown normalization method: {cfg.method}")
