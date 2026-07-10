"""agent/boundaries.py

The fence. Phase 2 safety core for agentic mode.

This decides which paths the agent may write, and detects any change to the
protected harness files (tamper). Nothing here runs an agent or an LLM. It is
pure policy plus a git-backed check, so it is deterministic and unit-testable
on its own. The agentic loop (phase 3) calls into this; it never reimplements
the boundary.

Two ideas:
  1. classify(): may the agent write this path? Deny by default. Only an
     explicit writable prefix is allowed, and never one that resolves into a
     protected file (path traversal is resolved before the check).
  2. changed_protected(): did any protected harness file change on disk? This is
     the meta-gate the loop runs every iteration. If it is not clean, the loop
     aborts as TAMPER and may revert.

Paths are resolved relative to the harness root (the directory that holds
guards/, provenance/, reviewer.py, ...). In an installed project that is the
project root; in this repo it is assets/harness/.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field


# The harness files that judge the agent. The agent may NEVER write these.
# A directory entry protects everything under it. The agent's own fence
# (agent/boundaries.py, agent/permissions.py, agent/contract.md) is protected
# too, so the agent cannot edit the rules it runs under.
PROTECTED: tuple[str, ...] = (
    "guards",
    "provenance",
    "tests",
    "reviewer.py",
    "diagnose.py",
    "conftest.py",
    "Makefile",
    "pytest.ini",
    "agent/boundaries.py",
    "agent/permissions.py",
    "agent/contract.md",
)

# Default writable prefixes. A project may extend this with its own experiment
# script paths, but an extension that resolves into PROTECTED is rejected by
# validate_writable().
DEFAULT_WRITABLE: tuple[str, ...] = (
    "pipeline",
    "results",
)


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str


def harness_root() -> str:
    """The harness root, inferred from this file's location (agent/ is one
    level under the root)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


# Build artifacts are never tamper. A compiled .pyc or a cache dir appearing
# under a protected directory does not change what the harness runs (Python
# recompiles from the .py source, which IS protected). Filtering these keeps
# the fence robust even when a project has no .gitignore.
_ARTIFACT_DIRS = ("__pycache__", ".pytest_cache")


def _is_build_artifact(rel: str) -> bool:
    if rel.endswith((".pyc", ".pyo")):
        return True
    return any(part in _ARTIFACT_DIRS for part in rel.split(os.sep))


def _under(rel: str, prefix: str) -> bool:
    return rel == prefix or rel.startswith(prefix + os.sep)


def is_protected(rel: str) -> bool:
    """True if a harness-relative path is a protected harness file."""
    return any(_under(rel, p) for p in PROTECTED)


def _rel_to_root(root: str, path: str) -> str | None:
    """Resolve path (absolute or root-relative) to a root-relative path with
    all symlinks and '..' collapsed. Returns None if it escapes root, so a
    traversal like pipeline/../guards/x.py cannot slip through."""
    root_real = os.path.realpath(root)
    target = path if os.path.isabs(path) else os.path.join(root_real, path)
    target_real = os.path.realpath(target)
    if target_real != root_real and not target_real.startswith(root_real + os.sep):
        return None
    return os.path.relpath(target_real, root_real)


def validate_writable(writable: tuple[str, ...]) -> None:
    """Reject a writable allowlist that overlaps a protected file. This stops a
    project config from accidentally (or deliberately) making a guard writable."""
    bad = [w for w in writable if is_protected(w.rstrip(os.sep))]
    if bad:
        raise ValueError(f"writable prefixes overlap protected files: {bad}")


def classify(root: str, path: str, writable: tuple[str, ...] = DEFAULT_WRITABLE) -> Decision:
    """Decide whether the agent may write path. Deny by default."""
    validate_writable(writable)
    rel = _rel_to_root(root, path)
    if rel is None:
        return Decision(False, f"path escapes the project root: {path}")
    if is_protected(rel):
        return Decision(False, f"protected harness file, the agent may not edit it: {rel}")
    if any(_under(rel, w) for w in writable):
        return Decision(True, f"writable: {rel}")
    return Decision(False, f"outside the writable allowlist: {rel}")


# --- the tamper meta-gate ---------------------------------------------------

@dataclass
class TamperReport:
    clean: bool
    changed: list[str] = field(default_factory=list)

    def render(self) -> str:
        if self.clean:
            return "OK: no protected harness file changed."
        lines = ["TAMPER: protected harness files changed (the agent may not edit these):"]
        lines += [f"  {c}" for c in self.changed]
        return "\n".join(lines)


def _git(root: str, args: list[str]) -> str:
    """Run a git command at root and return stdout. Fails loud: a boundary that
    cannot read git state must raise, never silently report clean."""
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {root}: {proc.stderr.strip()}")
    return proc.stdout


def _git_toplevel(root: str) -> str:
    return _git(root, ["rev-parse", "--show-toplevel"]).strip()


def _changed_files(toplevel: str) -> list[str]:
    """Every path git sees as changed vs HEAD (staged, unstaged, untracked),
    relative to the git toplevel."""
    out = _git(toplevel, ["status", "--porcelain", "--untracked-files=all"])
    files: list[str] = []
    for line in out.splitlines():
        entry = line[3:] if len(line) > 3 else ""
        if " -> " in entry:  # rename: take the destination
            entry = entry.split(" -> ", 1)[1]
        entry = entry.strip().strip('"')
        if entry:
            files.append(entry)
    return files


def changed_protected(root: str) -> TamperReport:
    """The meta-gate. Report any protected harness file that changed on disk.
    Works whether the harness is the git root or a subdirectory of it."""
    toplevel = _git_toplevel(root)
    root_real = os.path.realpath(root)
    changed: list[str] = []
    for gitpath in _changed_files(toplevel):
        abs_p = os.path.realpath(os.path.join(toplevel, gitpath))
        if abs_p != root_real and not abs_p.startswith(root_real + os.sep):
            continue  # outside the harness, not our concern
        rel = os.path.relpath(abs_p, root_real)
        if _is_build_artifact(rel):
            continue  # compiled bytecode / caches are not tamper
        if is_protected(rel):
            changed.append(rel)
    return TamperReport(clean=not changed, changed=sorted(changed))


def revert_protected(root: str) -> list[str]:
    """Undo any change to protected files: restore tracked ones from HEAD and
    delete protected untracked ones. Returns the reverted paths. Fails loud on
    a git error other than 'not in HEAD'."""
    report = changed_protected(root)
    if report.clean:
        return []
    toplevel = _git_toplevel(root)
    root_real = os.path.realpath(root)
    reverted: list[str] = []
    for rel in report.changed:
        gitpath = os.path.relpath(os.path.join(root_real, rel), toplevel)
        restore = subprocess.run(
            ["git", "checkout", "HEAD", "--", gitpath],
            cwd=toplevel, capture_output=True, text=True,
        )
        if restore.returncode != 0:
            # Not in HEAD means the agent created it under a protected dir. Remove it.
            abs_f = os.path.join(root_real, rel)
            if os.path.isfile(abs_f):
                os.remove(abs_f)
        reverted.append(rel)
    return sorted(reverted)


# --- run budget (stops runaway loops) ---------------------------------------

class RunBudget:
    """A safety stop for the loop. The agent must make progress or it halts.
    Progress means the number of open findings strictly decreases. Also caps
    total iterations so the loop can never run forever."""

    def __init__(self, max_iters: int = 12, max_no_progress: int = 3) -> None:
        if max_iters < 1 or max_no_progress < 1:
            raise ValueError("budgets must be at least 1")
        self.max_iters = max_iters
        self.max_no_progress = max_no_progress
        self.iters = 0
        self.no_progress = 0
        self._last: int | None = None

    def record(self, open_findings: int) -> None:
        self.iters += 1
        if self._last is not None and open_findings >= self._last:
            self.no_progress += 1
        else:
            self.no_progress = 0
        self._last = open_findings

    def should_stop(self) -> tuple[bool, str]:
        if self.iters >= self.max_iters:
            return True, f"iteration budget exhausted ({self.iters}/{self.max_iters})"
        if self.no_progress >= self.max_no_progress:
            return True, f"no progress for {self.no_progress} iterations, escalating to a human"
        return False, ""
