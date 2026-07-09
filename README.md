# verified-experiments

Guardrails that make machine-learning and data-science results **impossible to
fake, hardcode, or hallucinate.**

It ships as a Claude skill (`SKILL.md`) and as a plain drop-in Python harness
(`assets/harness/`). Every reported number has to earn its place: it must trace
to real data, be recomputable from stored predictions, survive leakage and
sanity controls, and be reproducible. A static reviewer gates the code itself,
before it enters the pipeline and after it produces output.

## The nine guards

| Guard | Stops |
|-------|-------|
| G1 Provenance | results with no data hash / git commit / seed |
| G2 Real-data | missing / constant / NaN / implausibly-clean data |
| G3 Leakage | a "label-blind" method secretly using labels |
| G4 Sanity + controls | impossible accuracies; shuffled-label leaks |
| G5 Audit | reported numbers not backed by stored predictions |
| G6 Reproducibility | non-deterministic (untrustworthy) runs |
| G7 Fail-loud | silent fallbacks; results without provenance |
| G8 Meta-tests | a guard that does not actually catch its fake |
| G9 Reviewer | fake patterns in the code itself (runs before + after) |
| D1 Diagnoser | a red gate reported with no root cause or the wrong STATUS |

Every guard ships with a test that feeds it a deliberate fake and asserts the
guard rejects it. See [`references/guards.md`](references/guards.md) for detail.

## When a gate goes red: diagnose it

```bash
make diagnose   # root-cause each finding, then print one completion STATUS
```

Instead of a raw stack trace, `make diagnose` runs the reviewer and the guard
meta-tests, then for each failure prints which guard tripped, the file and line,
what it means, the likely cause, and what to try. It ends with one STATUS you can
report back: **DONE**, **DONE_WITH_CONCERNS**, **BLOCKED**, or **NEEDS_CONTEXT**,
each with a reason, what was attempted, and a recommendation. This is the layer
that debugs the guards and talks you through what failed and why. The failure to
plain-language mapping is itself guarded by meta-tests, so the diagnoser can never
call a fake run DONE.

## Use it as a library

```bash
cp -r assets/harness/* your-project/     # guards/, provenance/, reviewer.py, Makefile, tests/
cd your-project
pip install numpy pytest
make gate      # reviewer clean + all guards pass on fakes and reals
make audit     # after producing outputs: every result grounded + recomputable
```

`make gate` exits non-zero on any fake pattern or failing guard. Nothing enters
the pipeline until it is green; no output is trusted until `make audit` is green.

## Use it as a Claude skill

Clone this repo into `~/.claude/skills/verified-experiments/`. It triggers when
you ask to verify an experiment, gate an ML pipeline, add provenance/audit to
metrics, or make sure results are not faked.

## License

MIT. See [LICENSE](LICENSE).
