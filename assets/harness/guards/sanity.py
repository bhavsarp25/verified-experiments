"""
G4 - Sanity bounds + positive/negative controls.

- A real accuracy lives in [0,1], beats chance only if genuinely learning, and
  can NEVER beat the label-cheating oracle (if it does, something leaked).
- Negative control: train/eval on SHUFFLED labels. A correct pipeline scores
  ~chance. If the shuffled-label run beats chance, the pipeline is leaking or
  buggy - a classic ML self-fooling detector.
"""
from __future__ import annotations

import numpy as np

from . import SanityError


def chance_level(n_classes: int) -> float:
    return 1.0 / float(n_classes)


def assert_not_above_oracle(acc: float, oracle: float, eps: float = 1e-6) -> None:
    """The unsupervised twin can approach but not exceed the label-cheating
    oracle. Beating it is impossible unless labels leaked or metrics are faked."""
    if acc > oracle + eps:
        raise SanityError(
            f"Accuracy {acc:.4f} exceeds the label-cheating oracle {oracle:.4f}. "
            f"That is impossible for a label-blind method - leak or fabrication."
        )


def assert_valid_accuracy(acc: float) -> None:
    if not np.isfinite(acc) or not (0.0 <= acc <= 1.0):
        raise SanityError(f"Accuracy {acc} is not a valid probability in [0,1].")


def beats_chance(acc: float, n_classes: int, n_samples: int, z: float = 3.0) -> bool:
    """True only if acc is above chance by more than `z` binomial std errors.
    Used to claim success, never to claim validity."""
    p0 = chance_level(n_classes)
    se = (p0 * (1 - p0) / max(n_samples, 1)) ** 0.5
    return acc > p0 + z * se


def random_label_control(train_eval_fn, X, y, n_classes: int, seed: int = 0, z: float = 4.0) -> float:
    """train_eval_fn(X, y_train) -> accuracy on a held-out real-label eval.
    We train on SHUFFLED labels. It must NOT beat chance. Returns the control
    accuracy; raises if it beats chance (=> leakage/bug)."""
    rng = np.random.default_rng(seed)
    y_shuf = np.asarray(y).copy()
    rng.shuffle(y_shuf)
    ctrl_acc = float(train_eval_fn(X, y_shuf))
    if beats_chance(ctrl_acc, n_classes, len(y), z=z):
        raise SanityError(
            f"Negative control (shuffled labels) scored {ctrl_acc:.4f}, above "
            f"chance {chance_level(n_classes):.4f}. The pipeline is leaking signal "
            f"it should not have."
        )
    return ctrl_acc
