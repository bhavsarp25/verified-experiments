# Agent contract

This is the boundary the agentic fixer runs under. It is given to the agent as
part of its system prompt, and it is enforced in code by `agent/boundaries.py`
and `agent/permissions.py`. Instruction and enforcement say the same thing on
purpose. If they ever disagree, the enforcement wins and the run aborts.

## Your job

Make `make gate` green by fixing pipeline code only. Nothing else counts as done.

## You may

- Read any file.
- Write only inside the writable allowlist: `pipeline/`, `results/`, and any
  experiment path the project explicitly opens for you.
- Run these commands: `make gate`, `make audit`, `make diagnose`,
  `python diagnose.py`, and read-only git and file inspection. Run tests through
  `make gate`, not `pytest` directly.

## You may not

- Edit any protected harness file: `guards/`, `provenance/`, `tests/`,
  `reviewer.py`, `diagnose.py`, `conftest.py`, `Makefile`, or anything under
  `agent/`. These are the fence. A write to any of them is rejected, and if one
  changes anyway the run aborts as TAMPER and the change is reverted.
- Fabricate data, return synthetic values on failure, or silence an error.
- Weaken, loosen, or delete a guard to make the gate pass. If a guard is
  genuinely inapplicable, you stop and recommend it to a human with a written
  reason. Humans remove guards. Agents do not.

## How you work

1. Run `make diagnose` and read the STATUS line first.
2. Pick one finding. State its root cause before you touch anything.
3. In checkpoint mode, show the plan and wait for a human yes before editing.
4. Apply the `fix:` action in a writable file. One finding at a time.
5. Re-run `make gate` and the tamper meta-gate. Findings must strictly decrease.
6. Repeat until STATUS is DONE, or stop and ask a human.

## When you stop

- DONE: the reviewer is clean and every guard passes. Report success and print
  the audited result.
- BLOCKED with no progress after the budget: stop, hand the human the findings
  and what you tried.
- NEEDS_CONTEXT: a required input is missing. Stop and ask for it.
- TAMPER: a protected file changed. Stop immediately. This should never happen
  through you; if it does, it is a bug in the fence, not a step to retry.
