"""
G5 - Independent recompute (audit).

Never trust the training loop's self-reported number. Every reported metric must
be re-derivable, by a SEPARATE code path, from the raw predictions stored on
disk. If the recompute disagrees with the report, the report is fabricated or
buggy. (BSS-2 parallel: the host decode was the single source of truth; here the
stored predictions are.)

Predictions file format: CSV with two integer columns `y_true,y_pred`.
"""
from __future__ import annotations

import numpy as np

from . import AuditError


def recompute_accuracy(predictions_csv: str) -> float:
    """Independently recompute accuracy from stored predictions. Deliberately
    simple and dependency-free so it shares no code with the training loop."""
    data = np.loadtxt(predictions_csv, delimiter=",", skiprows=1, dtype=int)
    if data.ndim != 2 or data.shape[1] != 2:
        raise AuditError(f"{predictions_csv}: expected 2 columns y_true,y_pred")
    y_true, y_pred = data[:, 0], data[:, 1]
    if len(y_true) == 0:
        raise AuditError(f"{predictions_csv}: no predictions to audit")
    return float(np.mean(y_true == y_pred))


def assert_report_matches_predictions(predictions_csv: str, reported_acc: float, tol: float = 1e-6) -> float:
    """Raise if the reported accuracy does not match the number recomputed from
    the stored predictions. Returns the audited accuracy."""
    audited = recompute_accuracy(predictions_csv)
    if abs(audited - reported_acc) > tol:
        raise AuditError(
            f"Reported accuracy {reported_acc:.6f} != independently recomputed "
            f"{audited:.6f} from {predictions_csv}. The reported number is not "
            f"backed by the stored predictions."
        )
    return audited
