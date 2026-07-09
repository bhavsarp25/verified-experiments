"""agent/run.py

The human entry point for the checkpoint-mode fix loop.

This wires the real diagnose (via subprocess) and a stdin-driven human
checkpoint into loop.run_loop. The fixer is deliberately NOT wired to a live
model here: the Claude Agent SDK binding is Phase 3b. Until then the fixer
placeholder raises with a clear message rather than pretending to fix anything,
so a run that reaches the edit step fails loud instead of silently doing
nothing.

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


def sdk_fixer_not_wired(finding, root: str) -> None:
    """The real fixer is a live Claude Agent SDK adapter, wired in Phase 3b. It
    is not available yet, so we refuse loudly instead of skipping the edit."""
    raise NotImplementedError(
        "the live SDK fixer is not wired yet; Phase 3b. "
        f"Would have fixed {finding.guard or '?'} {finding.code or '?'} in {finding.path}. "
        "Run with an injected fixer_fn to exercise the loop before then."
    )


def stdin_checkpoint(plan: Plan) -> bool:
    """Show the plan and read a y/n from stdin. Anything other than an explicit
    yes is treated as a deny, so a stray Enter never approves an edit."""
    print(plan.render())
    answer = input("Approve this fix? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = args[0] if args else boundaries.harness_root()

    result = run_loop(
        root,
        diagnose_fn=diagnose_via_subprocess,
        fixer_fn=sdk_fixer_not_wired,
        checkpoint_fn=stdin_checkpoint,
    )
    print(result.render())
    return 0 if result.outcome == "DONE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
