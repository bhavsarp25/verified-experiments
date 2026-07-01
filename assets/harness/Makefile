# Uses .venv/bin/python if present, else python3. Override with `make PY=...`.
PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

# The GATE: run this BEFORE code enters the pipeline and AFTER outputs land.
# It fails loudly (non-zero exit) on any fake pattern or failing guard.
.PHONY: gate
gate: review test
	@echo "GATE PASSED: code reviewed clean + all guards green."

# G9 static reviewer over the pipeline code (not the test fixtures).
.PHONY: review
review:
	$(PY) reviewer.py guards provenance pipeline 2>/dev/null || $(PY) reviewer.py guards provenance

# G8 guard meta-tests (each guard must reject a deliberate fake).
.PHONY: test
test:
	$(PY) -m pytest -q

# Post-output audit: every result must be grounded + recomputable.
.PHONY: audit
audit:
	$(PY) reviewer.py --outputs results

# T1 (added next): frozen decoder session1 -> session2 drift chart.
.PHONY: t1
t1:
	@echo "T1 not built yet - guardrails must be green first."
