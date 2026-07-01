"""
G3 - Label-leakage tripwire.

The adaptive model ("the twin") must adapt to the test session WITHOUT its
labels. We verify that by shuffling the test labels and re-running adaptation:
if the model's predictions change, it was secretly using the labels -> leak.

This is the automated version of the T3 test in the eng plan, and the same
instinct that caught host-side cheating on BSS-2.
"""
from __future__ import annotations

import numpy as np

from . import LeakageError


def label_shuffle_invariance(adapt_fn, X, y, metric_fn, tol: float = 0.02, seed: int = 0) -> dict:
    """adapt_fn(X, y_like) -> predictions. A label-blind adaptation MUST ignore
    y_like, so shuffling it must not change predictions (metric vs TRUE y stays
    the same). If it changes by more than `tol`, labels leaked."""
    rng = np.random.default_rng(seed)
    y = np.asarray(y)

    pred_real = np.asarray(adapt_fn(X, y.copy()))
    y_shuf = y.copy()
    rng.shuffle(y_shuf)
    pred_shuf = np.asarray(adapt_fn(X, y_shuf))

    m_real = float(metric_fn(y, pred_real))
    m_shuf = float(metric_fn(y, pred_shuf))
    delta = abs(m_real - m_shuf)

    if delta > tol:
        raise LeakageError(
            f"Adaptation changed with shuffled labels (metric {m_real:.4f} vs "
            f"{m_shuf:.4f}, delta {delta:.4f} > tol {tol}). The model is using "
            f"test labels it should never see."
        )
    return {"metric_real": m_real, "metric_shuffled": m_shuf, "delta": delta}
