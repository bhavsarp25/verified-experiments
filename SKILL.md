---
name: verified-experiments
description: Guardrails that make machine-learning and data-science results impossible to fake, hardcode, or hallucinate. Provides a tested, drop-in Python harness (provenance manifests, real-data assertions, label-leakage tripwires, sanity plus shuffled-label controls, independent metric re-computation/audit, reproducibility checks, and fail-loud/no-fabrication rules) plus a static code reviewer that runs before code enters the pipeline and after outputs are produced. Use when building, reviewing, running, or auditing any experiment, benchmark, model evaluation, training run, or data pipeline where the results must be trustworthy - especially to prevent hardcoded metric literals, silent except/fallbacks, synthetic-data substitution, train/test label leakage, or unreproducible numbers. Trigger when the user says results must be real / not faked / not hallucinated / not hardcoded, asks to verify or gate an experiment or ML pipeline, wants provenance or an audit trail on metrics, or wants a CodeRabbit-style check that catches faked results.
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
   make gate     # G9 reviewer: code is clean of fake patterns + all guards green
   make audit     # post-output: every result grounded + independently recomputable
   ```
   `make gate` exits non-zero on any fake pattern or failing guard. Nothing
   enters the pipeline until gate is green; no output is trusted until audit is
   green. The reviewer flags hardcoded metric literals, silent excepts,
   synthetic-returning loaders, and fabricated fallbacks.

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

For full per-guard detail, wiring examples, and the reviewer's checks, read
[references/guards.md](references/guards.md).

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
