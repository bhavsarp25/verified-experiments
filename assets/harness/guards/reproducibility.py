"""
G6 - Determinism / reproducibility.

A real result runs twice and matches. seed_everything pins every RNG we touch;
assert_reproducible runs a function twice with the same seed and fails if the
outputs differ beyond tolerance. Non-reproducible numbers are not trustworthy.
"""
from __future__ import annotations

import os
import random

import numpy as np

from . import ReproducibilityError


def seed_everything(seed: int) -> np.random.Generator:
    """Pin stdlib + numpy RNGs. (torch/braindecode get pinned here too once the
    T1 stack lands.) Returns a seeded Generator for local use."""
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    return np.random.default_rng(seed)


def assert_reproducible(run_fn, seed: int, tol: float = 0.0) -> None:
    """run_fn(seed) -> np.ndarray | float. Call twice; the outputs must match
    within `tol`. A run that ignores its seed (uses unseeded randomness) will be
    caught here."""
    a = np.asarray(run_fn(seed), dtype=float)
    b = np.asarray(run_fn(seed), dtype=float)
    if a.shape != b.shape:
        raise ReproducibilityError(f"Output shape changed between runs: {a.shape} vs {b.shape}")
    max_diff = float(np.max(np.abs(a - b))) if a.size else 0.0
    if max_diff > tol:
        raise ReproducibilityError(
            f"Not reproducible: two runs at seed {seed} differ by {max_diff:.3e} "
            f"(tol {tol}). Unseeded randomness somewhere."
        )
