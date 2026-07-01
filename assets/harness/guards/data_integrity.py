"""
G2 - Real-data assertion.

Fails loud if the loaded data is missing, the wrong shape, or looks synthetic
(constant, all-zero, NaN, or implausibly clean). This is the direct analogue of
catching "the chip emitted 0 spikes but numbers were still reported" on BSS-2:
if the data isn't really there, nothing downstream is allowed to proceed.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import DataIntegrityError


@dataclass(frozen=True)
class EEGSpec:
    """Expected shape of a real EEG dataset. Defaults describe BCI Competition
    IV-2a (per the dataset docs: 22 EEG channels, 250 Hz)."""
    n_channels: int = 22
    sfreq: float = 250.0
    min_trials: int = 100
    n_classes: int = 4


def assert_real_data(
    X,
    *,
    expected_ndim: int | None = None,
    min_rows: int = 1,
    expect_cols: int | None = None,
    require_correlation: bool = False,
) -> None:
    """Domain-agnostic real-data assertion. Raises DataIntegrityError on data
    that is missing, malformed, or looks synthetic (constant, NaN/inf, or, when
    require_correlation is set, implausibly decorrelated across features).

    EEG (assert_real_eeg below) is one worked example. Adapt the checks to your
    own domain's "this cannot be real data" tells."""
    X = np.asarray(X)
    if X.ndim == 0:
        raise DataIntegrityError("scalar is not a dataset")
    if expected_ndim is not None and X.ndim != expected_ndim:
        raise DataIntegrityError(f"expected ndim {expected_ndim}, got {X.ndim} ({X.shape})")
    if X.shape[0] < min_rows:
        raise DataIntegrityError(f"only {X.shape[0]} rows (< {min_rows}); real data?")
    if not np.all(np.isfinite(X)):
        raise DataIntegrityError("data contains NaN/inf - not clean real data")
    flat = X.reshape(X.shape[0], -1)
    if expect_cols is not None and X.ndim == 2 and X.shape[1] != expect_cols:
        raise DataIntegrityError(f"{X.shape[1]} cols != expected {expect_cols}")
    if flat.shape[1] >= 1 and np.any(flat.var(axis=0) == 0):
        raise DataIntegrityError("a feature has zero variance (constant) - synthetic?")
    if require_correlation and flat.shape[1] >= 2:
        corr = np.corrcoef(flat.T)
        n = corr.shape[0]
        off = corr[~np.eye(n, dtype=bool)]
        if float(np.mean(np.abs(off))) < 0.01:
            raise DataIntegrityError("features implausibly uncorrelated - looks generated.")


def assert_real_eeg(X: np.ndarray, y: np.ndarray, spec: EEGSpec = EEGSpec()) -> None:
    """X: (n_trials, n_channels, n_times).  y: (n_trials,) integer labels.
    A worked, domain-specific example built on the same idea as assert_real_data.

    Raises DataIntegrityError on anything that smells synthetic or malformed."""
    X = np.asarray(X)
    y = np.asarray(y)

    if X.ndim != 3:
        raise DataIntegrityError(f"X must be (trials, channels, times); got {X.shape}")
    n_trials, n_ch, n_times = X.shape

    if n_trials < spec.min_trials:
        raise DataIntegrityError(f"Only {n_trials} trials (< {spec.min_trials}); real recording?")
    if n_ch != spec.n_channels:
        raise DataIntegrityError(f"{n_ch} channels != expected {spec.n_channels}")
    if y.shape[0] != n_trials:
        raise DataIntegrityError(f"y has {y.shape[0]} labels for {n_trials} trials")

    if not np.all(np.isfinite(X)):
        raise DataIntegrityError("X contains NaN/inf - not clean real data")

    # Synthetic-tell 1: constant / all-zero channels.
    per_channel_var = X.var(axis=(0, 2))
    if np.any(per_channel_var == 0):
        raise DataIntegrityError("At least one channel has zero variance (constant) - synthetic?")

    # Synthetic-tell 2: real EEG has cross-channel correlation structure. Data
    # drawn i.i.d. from a generator is implausibly decorrelated. We flag data
    # that is TOO clean (near-perfectly uncorrelated across channels).
    flat = X.transpose(1, 0, 2).reshape(n_ch, -1)
    corr = np.corrcoef(flat)
    off = corr[~np.eye(n_ch, dtype=bool)]
    mean_abs_off = float(np.mean(np.abs(off)))
    if mean_abs_off < 0.01:
        raise DataIntegrityError(
            f"Mean |cross-channel corr| = {mean_abs_off:.4f} is implausibly low - "
            f"real EEG channels share structure; this looks generated."
        )

    # Label sanity: right number of distinct classes, not a single constant.
    classes = np.unique(y)
    if classes.size < 2:
        raise DataIntegrityError("Labels collapse to a single class - not a real task.")
    if classes.size > spec.n_classes:
        raise DataIntegrityError(f"{classes.size} classes > expected {spec.n_classes}")
