"""
G7 - Fail loud, never fabricate.  +  the structural anti-hardcoding core.

Two ideas:
1. `fail_loud` / `forbid_synthetic_fallback`: never swallow an error and return
   made-up data. If real data can't load, ERROR. (BSS-2 rule: no host fallback
   under any circumstance.)
2. `Result`: a number is not a result unless it carries a grounded provenance
   manifest AND a path to the raw predictions it was computed from. You cannot
   construct a Result from a bare literal, so a hardcoded accuracy simply
   cannot enter the pipeline as a "result".
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from provenance.manifest import Manifest
from . import FabricationError


def fail_loud(condition: bool, message: str) -> None:
    """Raise if condition is false. Use instead of `try/except: return fake`."""
    if not condition:
        raise FabricationError(message)


def forbid_synthetic_fallback(loader, *args, **kwargs):
    """Wrap a real-data loader. If it raises, we RE-RAISE. We never substitute
    synthetic data on failure. Returns whatever the loader returns on success."""
    try:
        return loader(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 - deliberate: re-raise, never fabricate
        raise FabricationError(
            f"Real-data loader failed and synthetic fallback is forbidden: {e!r}"
        ) from e


@dataclass(frozen=True)
class Result:
    """A grounded result. Construction fails unless it is backed by real data
    provenance and a real predictions file on disk."""
    name: str
    value: float
    manifest: Manifest
    predictions_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, Manifest) or not self.manifest.is_grounded:
            raise FabricationError(
                f"Result '{self.name}' has no grounded manifest - refusing to "
                f"treat a number with no data provenance as a result."
            )
        if not (isinstance(self.predictions_path, str) and os.path.exists(self.predictions_path)):
            raise FabricationError(
                f"Result '{self.name}' points to no real predictions file "
                f"({self.predictions_path!r}) - a result must be recomputable "
                f"from stored predictions."
            )
        if not (0.0 <= float(self.value) <= 1.0):
            raise FabricationError(
                f"Result '{self.name}' value {self.value} out of [0,1]."
            )
