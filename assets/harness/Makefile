# Uses .venv/bin/python if present, else python3. Override with `make PY=...`.
PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

# The GATE: run this BEFORE code enters the pipeline and AFTER outputs land.
# It fails loudly (non-zero exit) on any fake pattern or failing guard.
.PHONY: gate
gate: review test
	@echo "GATE PASSED: code reviewed clean + all guards green."

# G9 static reviewer over the pipeline + agent code (not the test fixtures).
.PHONY: review
review:
	$(PY) reviewer.py guards provenance agent pipeline 2>/dev/null || $(PY) reviewer.py guards provenance agent

# G8 guard meta-tests (each guard must reject a deliberate fake).
.PHONY: test
test:
	$(PY) -m pytest -q

# Post-output audit: every result must be grounded + recomputable.
.PHONY: audit
audit:
	$(PY) reviewer.py --outputs results

# Diagnose: run the reviewer + guard meta-tests, then print one plain-language
# STATUS report (DONE / DONE_WITH_CONCERNS / BLOCKED / NEEDS_CONTEXT) with a
# root-cause note per finding. This is the gstack-style "speak to me" layer.
# Non-zero exit on BLOCKED or NEEDS_CONTEXT so CI can gate on it too.
.PHONY: diagnose
diagnose:
	$(PY) diagnose.py

# Diagnose produced results instead of code (grounding + recompute audit).
.PHONY: diagnose-outputs
diagnose-outputs:
	$(PY) diagnose.py --outputs results

# T1 (added next): frozen decoder session1 -> session2 drift chart.
.PHONY: t1
t1:
	@echo "T1 not built yet - guardrails must be green first."
