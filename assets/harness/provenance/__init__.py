"""Provenance layer: every result must carry proof of where it came from."""
from .manifest import Manifest, sha256_array, build_manifest

__all__ = ["Manifest", "sha256_array", "build_manifest"]
