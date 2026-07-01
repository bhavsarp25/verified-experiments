# The guards, in detail

Nine guards. Each makes one class of fake result either impossible to construct
or loud to detect. Every guard ships with a meta-test that feeds it a deliberate
fake and asserts rejection (see `assets/harness/tests/`).

Table of contents: G1 provenance, G2 real-data, G3 leakage, G4 sanity+controls,
G5 audit, G6 reproducibility, G7 fail-loud, G8 meta-tests, G9 reviewer.

Import paths assume the harness lives at the project root (`guards/`,
`provenance/`, `reviewer.py`).

## G1 - Provenance (`provenance/manifest.py`)
A number is a result only if it can say where it came from: data hash, git
commit, seed, library versions, machine, time.
```python
from provenance.manifest import build_manifest
m = build_manifest(X, seed=0)          # hash + n_samples computed FROM X
assert m.is_grounded                    # 64-char data hash, >0 samples, real commit
```
You cannot build a grounded manifest without real data in hand.

## G2 - Real-data assertion (`guards/data_integrity.py`)
Reject missing / constant / NaN / implausibly-clean data before it feeds a model.
```python
from guards.data_integrity import assert_real_data
assert_real_data(X, expected_ndim=2, min_rows=100, require_correlation=True)
```
`require_correlation` catches i.i.d.-generated data (real multivariate signals
share cross-feature structure). `assert_real_eeg` is a worked domain example.
Adapt the "this cannot be real" tells to your domain.

## G3 - Label-leakage tripwire (`guards/leakage.py`)
If a method claims to be unsupervised/label-blind, shuffle the labels and prove
its predictions do not change.
```python
from guards.leakage import label_shuffle_invariance
label_shuffle_invariance(adapt_fn, X, y, metric_fn)   # raises LeakageError if it peeks
```

## G4 - Sanity + controls (`guards/sanity.py`)
- `assert_valid_accuracy` - in [0,1], finite.
- `assert_not_above_oracle(acc, oracle)` - a label-blind method can NEVER beat a
  label-cheating oracle; if it does, something leaked.
- `beats_chance(acc, n_classes, n)` - claim success only above chance by >z SEs.
- `random_label_control(fit_fn, X, y, n_classes)` - train on shuffled labels; it
  MUST score chance. If it beats chance, the pipeline is leaking.

## G5 - Independent audit (`guards/audit.py`)
Never trust the training loop's self-reported number. Store raw predictions to
disk and recompute the metric by a SEPARATE code path.
```python
from guards.audit import assert_report_matches_predictions
assert_report_matches_predictions("results/preds.csv", reported_acc)  # CSV: y_true,y_pred
```

## G6 - Reproducibility (`guards/reproducibility.py`)
```python
from guards.reproducibility import seed_everything, assert_reproducible
seed_everything(0)
assert_reproducible(run_fn, seed=0)    # two seeded runs must match
```

## G7 - Fail loud, never fabricate (`guards/fabrication.py`)
- `fail_loud(cond, msg)` instead of `try/except: return fake`.
- `forbid_synthetic_fallback(loader, ...)` - re-raises on load failure; never
  substitutes synthetic data.
- `Result(name, value, manifest, predictions_path)` - construction FAILS unless
  backed by a grounded manifest and a real predictions file. A bare literal
  cannot become a `Result`, so a hardcoded metric cannot enter the pipeline.

## G8 - Meta-tests (`tests/`)
The discipline that ties it together: for every guard, a test feeds it a
deliberate fake and asserts the guard rejects it. Red (fake fails) before green.
Run: `python -m pytest -q`.

## G9 - Reviewer (`reviewer.py`)
A small static analyzer that runs BEFORE code enters the pipeline and AFTER
outputs land. It flags, as ERRORs: hardcoded metric literals
(`accuracy = 0.87`), silent excepts (`except: pass`), synthetic-returning
loaders, fabricated fallbacks (`except: return np.zeros(...)`); and WARNs on
unseeded RNGs and TODOs.
```bash
python reviewer.py guards provenance pipeline   # code gate, exits 1 on ERROR
python reviewer.py --outputs results            # every result grounded + recomputable
```

## The loop (run before AND after every change)
```
make gate     # G9 reviewer clean + all guard meta-tests pass
make audit    # G5/G7 post-output: every result grounded + recomputable
```
`make gate` exits non-zero on any fake pattern or failing guard. Nothing enters
the pipeline until it is green; no output is trusted until `make audit` is green.

## Wiring the guards into a real pipeline (the intended flow)
1. Load real data through `forbid_synthetic_fallback`; then `assert_real_data`.
2. `seed_everything(seed)`.
3. Train/evaluate. Write raw predictions to `results/<name>.preds.csv`.
4. Compute the metric, then `assert_report_matches_predictions` (G5) to confirm
   it matches the stored predictions.
5. Build a `Result` (G1+G7): it refuses to exist without provenance + predictions.
6. For any "unsupervised" claim, run `label_shuffle_invariance` (G3) and
   `random_label_control` (G4).
7. `assert_not_above_oracle` against a label-cheating upper bound (G4).
8. Save `results/<name>.result.json` (value + manifest + predictions_path).
9. `make gate` before commit; `make audit` after producing outputs.
