---
name: verified-experiments
description: Guardrails that make machine-learning and data-science results impossible to fake, hardcode, or hallucinate. Provides a tested, drop-in Python harness (provenance manifests, real-data assertions, label-leakage tripwires, sanity plus shuffled-label controls, independent metric re-computation/audit, reproducibility checks, and fail-loud/no-fabrication rules) plus a static code reviewer that runs before code enters the pipeline and after outputs are produced. Use when building, reviewing, running, or auditing any experiment, benchmark, model evaluation, training run, or data pipeline where the results must be trustworthy - especially to prevent hardcoded metric literals, silent except/fallbacks, synthetic-data substitution, train/test label leakage, or unreproducible numbers. Trigger when the user says results must be real / not faked / not hallucinated / not hardcoded, asks to verify or gate an experiment or ML pipeline, wants provenance or an audit trail on metrics, or wants a CodeRabbit-style check that catches faked results. Also includes a diagnose loop that root-causes any red gate and prints a plain-language completion STATUS (DONE, DONE_WITH_CONCERNS, BLOCKED, or NEEDS_CONTEXT) with a fix for each finding, so an agent can debug the guards and talk you through what failed and why.
---

# Verified Experiments

Make every reported number earn its place. This skill installs and applies a
small, tested Python harness that makes faked, hardcoded, or hallucinated
results either structurally impossible or loudly detected, and a reviewer that
gates the code before and after each step.

## When applying this skill

1. **Install the harness.** Copy `assets/harness/` into the target project root
   so the project has `guards/`, `provenance/`, `reviewer.py`, `conftest.py`,
   `Makefile`, and `tests/`. Install deps: `pip install numpy pytest`. Confirm
   it is live: `make gate` (reviewer clean + all guard meta-tests pass).

2. **Wire the guards into the real pipeline.** Do not just have the harness
   present; route results through it. The intended flow:
   - Load real data through `forbid_synthetic_fallback`, then `assert_real_data`.
   - `seed_everything(seed)` before anything random.
   - Train/evaluate, then **write raw predictions to disk** (`y_true,y_pred` CSV).
   - `assert_report_matches_predictions(csv, reported)` so the number is
     recomputed by a separate code path (never trust the training loop's
     self-report).
   - Build a `Result(name, value, manifest, predictions_path)` - it refuses to
     exist without a grounded provenance manifest and a real predictions file,
     so a hardcoded literal cannot become a result.
   - For any "unsupervised / label-blind" claim, run `label_shuffle_invariance`
     and `random_label_control`. Add `assert_not_above_oracle` against a
     label-cheating upper bound.
   - Save `results/<name>.result.json` = value + manifest + predictions_path.

3. **Run the loop before AND after every change** (this is the core habit):
   ```
   make gate      # G9 reviewer: code is clean of fake patterns + all guards green
   make audit     # post-output: every result grounded + independently recomputable
   make diagnose  # if gate or audit is red: root-cause each failure + a STATUS report
   ```
   `make gate` exits non-zero on any fake pattern or failing guard. Nothing
   enters the pipeline until gate is green; no output is trusted until audit is
   green. The reviewer flags hardcoded metric literals, silent excepts,
   synthetic-returning loaders, and fabricated fallbacks. When something is red,
   `make diagnose` turns the raw output into a plain-language root cause and one
   STATUS you can report back (see the diagnose loop below).

4. **Keep the discipline (G8).** Every guard already has a meta-test that feeds
   it a deliberate fake and asserts rejection. When adding a new guard or a
   domain-specific check, add its fake-catching test first (red), then make it
   pass (green). A guard is not "done" until it has caught a fake.

## The nine guards (summary)

| Guard | File | Stops |
|-------|------|-------|
| G1 Provenance | `provenance/manifest.py` | results with no data hash / commit / seed |
| G2 Real-data | `guards/data_integrity.py` | missing / constant / NaN / too-clean data |
| G3 Leakage | `guards/leakage.py` | a "label-blind" method secretly using labels |
| G4 Sanity + controls | `guards/sanity.py` | impossible accuracies; shuffled-label leaks |
| G5 Audit | `guards/audit.py` | reported numbers not backed by stored predictions |
| G6 Reproducibility | `guards/reproducibility.py` | non-deterministic (untrustworthy) runs |
| G7 Fail-loud | `guards/fabrication.py` | silent fallbacks; results without provenance |
| G8 Meta-tests | `tests/` | a guard that does not actually catch its fake |
| G9 Reviewer | `reviewer.py` | fake patterns in the CODE itself (before + after) |
| D1 Diagnoser | `diagnose.py` | a red gate reported with no root cause or wrong STATUS |

For full per-guard detail, wiring examples, and the reviewer's checks, read
[references/guards.md](references/guards.md).

## Diagnosing failures: the investigate loop

When `make gate` or `make audit` goes red, do not guess a fix. Run
`make diagnose` and work the loop below. This is the layer that finds the
issue, explains it, and gives you one line to report back.

`make diagnose` runs the G9 reviewer and the G8 guard meta-tests, then prints:
a root-cause note per finding (which guard, the file and line, what it means,
the most likely cause, and what to try), followed by one completion STATUS for
the whole run.

**The five steps (do them in order, never skip to the fix):**

1. **Reproduce.** Run `make diagnose`. Read the STATUS line first, then each
   finding. If nothing reproduces, there is no bug to fix.
2. **Read the exact assertion.** For a failing guard test, open the named test
   and read what it asserts and the exception it raised. For a reviewer finding,
   open the reported `file:line`. The `cause:` line is the likely root, not a
   guess to act on blindly.
3. **Isolate.** Confirm the one guard or one line responsible. One failure at a
   time. Do not change three things and re-run.
4. **Root cause, then fix.** Only once you can name why it failed, apply the
   `fix:` line. Fix the code, never weaken or delete a guard to make it pass.
   If a guard is genuinely inapplicable, remove it with a written reason (this
   is allowed; silently loosening a threshold is not).
5. **Re-run and report.** `make gate` and `make diagnose` again. Report the new
   STATUS to the user in plain language.

**The STATUS you report** is one of:

- **DONE**: reviewer clean and all guard meta-tests pass. Safe to proceed.
- **DONE_WITH_CONCERNS**: no blockers, but advisory warnings (a TODO, an
  unseeded RNG) to clear. List them.
- **BLOCKED**: a reviewer error or a failing guard blocks the pipeline. State
  the blocker and what you tried.
- **NEEDS_CONTEXT**: a required input is missing (for example, no results
  directory yet). State exactly what is needed.

Each STATUS ships with REASON, ATTEMPTED, and a RECOMMENDATION, so the report
speaks to the user: what was checked, what passed, what failed and why, and the
next action. The mapping from failures to plain-language causes is itself guarded
by D1 meta-tests (`tests/test_diagnose.py`): feed the diagnoser a known fake and
it must not report DONE.

## Agentic mode (checkpoint loop built)

There is an optional path where an agent runs the guards, reads the diagnosis,
and fixes its own fakes. The guards stay the enforced boundary: the agent may
write only pipeline code, and can never edit a guard, a test, the reviewer, or
the diagnoser to make the gate pass. The only way to green is fixing real code.

The full loop lives in `agent/`:
- `agent/boundaries.py` (the fence): decides which paths are writable (deny by
  default, traversal-safe) and runs the tamper meta-gate. If any protected
  source file changes, the run aborts and reverts. Build artifacts (`.pyc`,
  `__pycache__`) never count as tamper.
- `agent/permissions.py`: the Edit/Write/Bash hook that enforces the same fence
  at the tool layer.
- `agent/contract.md`: the boundary the agent is told, word for word.
- `agent/loop.py`: the checkpoint fix loop. It reads `diagnose.py --json`, picks
  one finding, forms a plan, asks a human at the checkpoint, applies the fix in
  a writable file only, re-runs, and enforces the tamper gate and a run budget
  every iteration. Terminal outcomes: DONE, NEEDS_CONTEXT, STOPPED_BY_HUMAN,
  BLOCKED_BY_FENCE, TAMPER, ESCALATED.
- `agent/sdk_fixer.py`: the live model-driven fixer. The model is bounded by the
  same fence, enforced at the tool layer by a PreToolUse hook plus `can_use_tool`
  (never by an allow-list, which would silently shadow the callback). It fails
  loud if it makes no edit, so a no-op is never mistaken for a fix.
- `agent/run.py`: the human CLI entry, wiring diagnose + checkpoint + fixer.

Needs `pip install claude-agent-sdk`. Run it with `make agent`. See [docs/agentic-scope.md](docs/agentic-scope.md) for
the design and remaining work. You can still use the manual investigate loop
above at any time.

## Adapting to a domain

The harness is domain-agnostic except `assert_real_eeg`, which is a worked
example of `assert_real_data`. For a new domain, write the domain's own "this
cannot be real data" assertion (constant, out-of-range, wrong shape, implausibly
clean) on top of `assert_real_data`, and add a meta-test that a synthetic sample
is rejected.

## Non-negotiables

- A reported metric must be a `Result` backed by a grounded manifest and stored
  predictions. Never a bare literal.
- Data loaders raise on failure. Never substitute synthetic data.
- No `except: pass` and no `except: return <synthetic>`. Fail loud.
- Every reported number must be independently recomputable from stored
  predictions (G5).
- Fixed seeds; two runs must match (G6).
- `make gate` green before merge; `make audit` green after producing outputs.
