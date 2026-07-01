"""
G1 - Provenance manifest.

A result is not a result unless it can say exactly where it came from:
which data (by hash), which code (git commit), which seed, which library
versions, on what machine, when. No manifest, no result.

This is the audit-trail discipline from the BSS-2 work ("host decode is the
single source of truth") applied to every number this project produces.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Any

import numpy as np


def sha256_array(arr: np.ndarray) -> str:
    """Content hash of a numpy array. Two identical arrays hash identically;
    any change to the bytes changes the hash. This is what ties a result to
    the exact data it was computed from."""
    a = np.ascontiguousarray(arr)
    h = hashlib.sha256()
    h.update(str(a.dtype).encode())
    h.update(str(a.shape).encode())
    h.update(a.tobytes())
    return h.hexdigest()


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            dirty = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            return out.stdout.strip() + ("-dirty" if dirty else "")
    except Exception:
        return "NO_GIT"  # sentinel, not fabricated data - git simply unavailable
    return "NO_GIT"


@dataclass(frozen=True)
class Manifest:
    """Immutable provenance record attached to every result."""
    data_sha256: str
    n_samples: int
    git_commit: str
    seed: int
    python_version: str
    numpy_version: str
    platform: str
    created_utc: str
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @property
    def is_grounded(self) -> bool:
        """A manifest is grounded only if it references real, non-empty data
        and real code. This is the tripwire against manifests fabricated for
        results that never touched data."""
        return (
            isinstance(self.data_sha256, str)
            and len(self.data_sha256) == 64
            and self.n_samples > 0
            and self.git_commit != ""
        )


def build_manifest(data: np.ndarray, seed: int, extra: dict[str, Any] | None = None) -> Manifest:
    """Build a manifest FROM the actual data array. Because the hash and sample
    count are computed from `data`, you cannot build a valid grounded manifest
    without real data in hand."""
    data = np.asarray(data)
    return Manifest(
        data_sha256=sha256_array(data),
        n_samples=int(data.shape[0]),
        git_commit=_git_commit(),
        seed=int(seed),
        python_version=sys.version.split()[0],
        numpy_version=np.__version__,
        platform=platform.platform(),
        created_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        extra=dict(extra or {}),
    )
