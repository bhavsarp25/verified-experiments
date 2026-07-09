# Agentic verified-experiments: scope

Status: scope approved, not built. Decisions locked below.

Today the harness is guardrails an agent can read. This scopes the next step: a
self-checking agent that runs the guardrails, reads the diagnosis, fixes its own
fakes, and re-runs until the gate is green or it hits a wall and stops. The
harness stops being documentation and becomes the agent's environment and its
warden at the same time.

Working name: `verified-agent`, a mode of this same skill, not a separate repo.

## Locked decisions

1. **Checkpoint mode first.** The agent proposes each fix and waits for a human
   yes before it edits. Full autonomy (edit and re-run on its own until green or
   stop) is a later flag, unlocked only after the boundary tests are proven.
2. **Same repo.** This lives in `verified-experiments` as an `agent/` add-on plus
   an "Agentic mode" section in `SKILL.md`. The agent is worthless without the
   guardrails it wraps, so they ship together.

## The core idea: guardrails as boundary, not suggestion

The point is that the agent cannot cheat its way to green. Two layers.

1. **Structural, already true today.** The harness makes fakery impossible, not
   discouraged. A metric only exists as a `Result` backed by a grounded manifest
   and a real predictions file. Data loaders raise instead of returning
   synthetic. So even a misbehaving agent physically cannot emit a hardcoded
   number that passes `make audit`. This layer needs zero new work. It is the
   reason this is safe to automate.
2. **Enforced write-boundary, new.** The agent may only edit the pipeline code it
   is supposed to fix. It may never touch the harness that judges it. That
   boundary is enforced by the runtime, not by asking nicely.

## The boundary, concretely

The agent operates on two lists.

- **Writable:** `pipeline/`, experiment scripts, `results/` (produced outputs
  only).
- **Read-only, hard-blocked:** `guards/`, `provenance/`, `reviewer.py`,
  `diagnose.py`, `tests/`, `Makefile`, `conftest.py`. These are the fence. If the
  agent tries to edit one, the write is rejected before it happens.

Two enforcement mechanisms, defense in depth.

- A **permission hook** on the Edit and Write tools that checks the target path
  against the allowlist and denies anything in the protected set.
- A **meta-gate** run every iteration: `git diff --name-only` against the
  protected paths. If any protected file changed, the run aborts as `TAMPER` and
  reverts. This catches anything the hook missed, for example a `Bash` `sed`.

So the agent literally cannot weaken a guard, delete a test, or loosen a
threshold to make the gate pass. Its only path to green is fixing the real code.

## The agent loop (states)

```
DIAGNOSE ---> read `make diagnose` STATUS + findings
   |
   |- DONE --------------> STOP: report success, print the audited Result
   |
   |- DONE_WITH_CONCERNS -> optionally clear warnings, then STOP
   |
   |- BLOCKED --> pick ONE finding
   |                |
   |             PLAN: state the root cause + intended fix (one file)
   |                |
   |             CHECKPOINT: show the plan, wait for human yes  (checkpoint mode)
   |                |
   |             EDIT: apply the `fix:` action, writable paths only
   |                |
   |             RE-RUN: make gate + meta-gate
   |                |
   |             |- progress (fewer findings) -> loop back to DIAGNOSE
   |             |- no progress after N tries  -> ESCALATE
   |
   |- NEEDS_CONTEXT --> STOP: ask the human for the missing input
```

Hard stops built in: a max-iteration budget, a no-progress counter (findings
must strictly decrease or it escalates), and immediate halt on `NEEDS_CONTEXT` or
`TAMPER`. The agent never loops forever and never edits the fence. In checkpoint
mode there is also a human gate before every edit.

## What the agent is told (the contract)

A generated system prompt, derived from `SKILL.md` plus an explicit
`agent/contract.md`:

- Your job: make `make gate` green by fixing pipeline code only.
- Your tools: Read, scoped Edit and Write, Bash limited to `make` targets and
  reads.
- Forbidden: editing anything in the protected set; fabricating data; silencing
  errors; removing a guard.
- Fix one finding at a time. State the root cause before editing.
- If a guard is genuinely inapplicable, you may not remove it. You stop and
  recommend it to the human with a written reason. Humans remove guards, agents
  do not.
- Exit conditions: DONE, or STOP-and-ask on BLOCKED-no-progress or
  NEEDS_CONTEXT.

Instruction and enforcement say the same thing, so a prompt injection or a
confused step still cannot cross the fence.

## Tech stack

- Claude Agent SDK (Python), model `claude-opus-4-8` for the fixer, optionally
  `claude-haiku-4-5` for the cheap diagnose-parsing step.
- Tools: `Read`, `Edit` and `Write` wrapped with the path-allowlist permission
  hook, `Bash` allowlisted to `make gate|audit|diagnose` and read-only commands,
  and `diagnose` exposed as a first-class tool returning structured JSON instead
  of scraped text.
- The existing `diagnose.py` gets a `--json` flag so the agent consumes findings
  as data.

## What ships (additions only, nothing existing weakened)

```
agent/
  run.py            the loop + SDK wiring
  contract.md       the boundary the agent is given
  boundaries.py     the allowlist + protected-path meta-gate
  permissions.py    the Edit/Write path hook
diagnose.py         +--json output (small change)
tests/
  test_boundaries.py   agent cannot edit a guard; tamper aborts; no-progress escalates
SKILL.md            +"Agentic mode" section
docs/agentic-scope.md  this file
```

Every new capability ships a deliberate-fake meta-test, same as G8: a test that
hands the agent a tampering move and asserts the boundary blocks it, and a test
that asserts the agent cannot reach DONE by editing a guard.

## Build phases

1. `diagnose.py --json` plus the structured `diagnose` tool.
2. `boundaries.py` plus `permissions.py` plus the meta-gate, with their tests.
   This is the safety core and lands before any autonomy.
3. `agent/run.py`: the loop, budgets, escalation, checkpoint gate, DONE and STOP
   handling.
4. Dogfood: plant a known fake in a sample pipeline, watch the agent propose the
   fix, approve it at the checkpoint, and confirm it stops at green without
   touching the fence.

## Open items for later

- Unlocking full autonomy once phase 2 boundary tests are proven.
- Whether the checkpoint gate is per-edit or per-finding (start per-edit).
- A run transcript artifact (every plan, edit, and gate result) as an audit
  trail, in the same spirit as the provenance manifest.
