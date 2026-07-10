"""agent/run.py

The human entry point for the checkpoint-mode fix loop.

This wires the real diagnose (via subprocess), a stdin-driven human checkpoint,
and the live model-driven fixer (agent/sdk_fixer.py) into loop.run_loop.

The fixer is bounded by the same fence as everything else: every tool call it
makes is checked by permissions.check_tool before it happens, and run_loop still
runs the tamper meta-gate after each edit. If the SDK is not installed, the run
fails loud rather than silently skipping the edit.

Importing this module has no side effects; the loop only runs from main().
"""
from __future__ import annotations

import sys

import boundaries
from loop import (
    Plan,
    diagnose_via_subprocess,
    run_loop,
)
from sdk_fixer import sdk_fixer


def stdin_checkpoint(plan: Plan) -> bool:
    """Show the plan and read a y/n from stdin. Anything other than an explicit
    yes is treated as a deny, so a stray Enter never approves an edit."""
    print(plan.render())
    answer = input("Approve this fix? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def preflight(root: str) -> str | None:
    """Refuse to start if protected files already have uncommitted changes.

    The tamper meta-gate reverts protected files from HEAD. If the baseline is
    dirty, a TAMPER abort would discard the user's own uncommitted work along
    with anything the agent touched. Return a refusal reason, or None if clean."""
    report = boundaries.changed_protected(root)
    if report.clean:
        return None
    return (
        "refusing to run: protected files have uncommitted changes:\n"
        + "\n".join(f"  {p}" for p in report.changed)
        + "\nCommit or stash them first. The tamper gate reverts from HEAD and "
        "would discard these."
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = args[0] if args else boundaries.harness_root()

    refusal = preflight(root)
    if refusal is not None:
        print(refusal)
        return 2

    result = run_loop(
        root,
        diagnose_fn=diagnose_via_subprocess,
        fixer_fn=sdk_fixer,
        checkpoint_fn=stdin_checkpoint,
    )
    print(result.render())
    return 0 if result.outcome == "DONE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
