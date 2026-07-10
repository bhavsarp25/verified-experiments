"""
Systematic diagnosis + completion-status reporting for the guard harness.

This is the gstack-style layer on top of the guards. It does NOT replace or
weaken any guard. It runs the existing G9 reviewer and the G8 guard meta-tests,
then turns their raw output into two things:

  1. Root-cause diagnosis (per issue): which guard, which check, the offending
     file/line, the most likely cause, and what to try. No guessing at a fix
     before the failure is understood.
  2. One completion STATUS for the whole run:
         DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
     with REASON, ATTEMPTED, and RECOMMENDATION, written to speak to the user
     in plain language.

It is honest by construction, in the same spirit as the guards it reports on.
It never swallows an error and never invents a number. If a step cannot run,
it says so and reports NEEDS_CONTEXT or BLOCKED rather than pretending success.

Usage:
    python diagnose.py                     # review code + run guard tests, report
    python diagnose.py --no-tests          # review code only, report
    python diagnose.py --outputs results   # audit produced results, report
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

import reviewer


# Completion-status values (the gstack STATUS protocol).
DONE = "DONE"
DONE_WITH_CONCERNS = "DONE_WITH_CONCERNS"
BLOCKED = "BLOCKED"
NEEDS_CONTEXT = "NEEDS_CONTEXT"

_EXIT_FOR_STATUS = {DONE: 0, DONE_WITH_CONCERNS: 0, BLOCKED: 1, NEEDS_CONTEXT: 2}


@dataclass(frozen=True)
class Diagnosis:
    """Plain-language root-cause note for one class of failure."""
    plain: str          # what this means, in plain words
    likely_cause: str   # the most likely root cause
    action: str         # what to try


# --- Reviewer finding codes (G9 code review + output audit) -> diagnosis ----
REVIEWER_DIAGNOSES: dict[str, Diagnosis] = {
    "HARDCODED_METRIC": Diagnosis(
        "A metric variable was set to a literal number instead of being computed from data.",
        "Someone typed the result in by hand, or stubbed it while wiring the pipeline.",
        "Compute the metric from stored predictions and wrap it in a Result. Never assign a metric a bare number.",
    ),
    "SILENT_EXCEPT": Diagnosis(
        "An except block swallows the error with pass and keeps going.",
        "A failing step is being hidden so the run looks like it worked.",
        "Remove the pass. Re-raise, or call fail_loud with a clear message so the failure is visible.",
    ),
    "FABRICATED_FALLBACK": Diagnosis(
        "An except block returns synthetic data (zeros or random) instead of failing.",
        "A loader or step falls back to fake data when the real path breaks.",
        "Delete the fallback. Let it raise, or route the loader through forbid_synthetic_fallback.",
    ),
    "SYNTHETIC_LOADER": Diagnosis(
        "A function named like a data loader returns generated data.",
        "A real loader was replaced with a placeholder that makes up data.",
        "Load real data or raise. A loader must never return np.random or np.zeros.",
    ),
    "UNSEEDED_RNG": Diagnosis(
        "default_rng() was called with no seed.",
        "Randomness is unpinned, so two runs will not match and G6 will fail later.",
        "Pass a fixed seed, for example seed_everything(seed) or default_rng(seed).",
    ),
    "TODO": Diagnosis(
        "A TODO, FIXME, HACK, or XXX marker is still in the code.",
        "Unfinished work was left in place.",
        "Finish or remove the marked line before this goes public.",
    ),
    "SYNTAX": Diagnosis(
        "A file could not be parsed as Python.",
        "A syntax error, most likely from an incomplete edit.",
        "Open the file at the reported line, fix the syntax, then re-run.",
    ),
    "UNGROUNDED_RESULT": Diagnosis(
        "A saved result has no grounded data manifest (missing 64-char data hash or sample count).",
        "The number was written out with no provenance, so it cannot be tied to real data.",
        "Rebuild the result via build_manifest and Result so it carries a real data hash and sample count.",
    ),
    "MISSING_PREDICTIONS": Diagnosis(
        "A saved result points to a predictions file that is not on disk.",
        "Predictions were not written, or the stored path is wrong.",
        "Write raw y_true,y_pred to disk and point the result at that file so it can be recomputed (G5).",
    ),
    "BAD_RESULT_JSON": Diagnosis(
        "A .result.json file could not be read as JSON.",
        "The file is truncated or malformed.",
        "Regenerate the result file. Do not hand-edit result JSON.",
    ),
    "NO_RESULTS": Diagnosis(
        "The results directory has no .result.json files yet.",
        "No outputs have been produced.",
        "Run the pipeline to produce results, then audit again.",
    ),
    "NO_RESULTS_DIR": Diagnosis(
        "The results directory does not exist.",
        "No outputs have been produced, or the wrong path was given.",
        "Point --outputs at the real results directory, or produce outputs first.",
    ),
}

UNKNOWN_CODE_DIAG = Diagnosis(
    "The reviewer reported a finding this diagnoser does not recognise.",
    "A new reviewer rule was added without a matching diagnosis entry.",
    "Add a Diagnosis for this code to REVIEWER_DIAGNOSES so the report stays useful.",
)

# Which guard each reviewer code belongs to, for the report label.
CODE_TO_GUARD: dict[str, str] = {
    "HARDCODED_METRIC": "G9", "SILENT_EXCEPT": "G9", "FABRICATED_FALLBACK": "G9",
    "SYNTHETIC_LOADER": "G9", "UNSEEDED_RNG": "G9", "TODO": "G9", "SYNTAX": "G9",
    "UNGROUNDED_RESULT": "G1", "MISSING_PREDICTIONS": "G5", "BAD_RESULT_JSON": "G5",
    "NO_RESULTS": "G5", "NO_RESULTS_DIR": "G5",
}


# --- Guard meta-test failures (G8) -> guard id + diagnosis ------------------
# Keyword table maps a failing test node id to the guard it exercises. This is
# a best-effort mapping; anything unmatched falls back to a generic diagnosis
# that tells the reader to read the assertion, never a fabricated cause.
GUARD_KEYWORDS: list[tuple[tuple[str, ...], str, Diagnosis]] = [
    (("leak", "shuffle", "peek"), "G3", Diagnosis(
        "The label-leakage tripwire test failed (G3).",
        "label_shuffle_invariance did not catch a method that peeks at labels, or it flagged a clean one.",
        "Check guards/leakage.py and its tol: predictions must not change when labels are shuffled.",
    )),
    (("audit", "recompute", "predictions", "inflated"), "G5", Diagnosis(
        "The independent-audit test failed (G5).",
        "recompute_accuracy disagrees with the stored predictions, or an inflated report was not caught.",
        "Check guards/audit.py and the predictions CSV format (y_true,y_pred).",
    )),
    (("reproduc", "nondet", "determ"), "G6", Diagnosis(
        "The reproducibility test failed (G6).",
        "A run ignores its seed, so two seeded runs differ.",
        "Check guards/reproducibility.py and seed_everything. Pin every RNG the run touches.",
    )),
    (("manifest", "provenance", "grounded", "hash"), "G1", Diagnosis(
        "A provenance/manifest test failed (G1).",
        "build_manifest or is_grounded accepted a result with no real data hash/commit, or the hash changed.",
        "Check provenance/manifest.py: grounded needs a 64-char data hash, n_samples > 0, and a real commit.",
    )),
    (("eeg", "real_data", "integrity", "constant", "channel", "clean", "nan"), "G2", Diagnosis(
        "A real-data test failed (G2).",
        "assert_real_data or assert_real_eeg let synthetic or malformed data through, or rejected real data.",
        "Check guards/data_integrity.py: constant, NaN, too-clean tells and the shape checks.",
    )),
    (("oracle", "chance", "accuracy", "control", "sanity"), "G4", Diagnosis(
        "A sanity/control test failed (G4).",
        "An impossible accuracy passed, or the shuffled-label control did not catch leakage.",
        "Check guards/sanity.py: the oracle bound, valid-accuracy range, chance math, random_label_control.",
    )),
    (("fail_loud", "fabric", "result", "synthetic_fallback"), "G7", Diagnosis(
        "A fail-loud/never-fabricate test failed (G7).",
        "fail_loud or forbid_synthetic_fallback did not raise, or a Result was built with no provenance.",
        "Check guards/fabrication.py: loaders must re-raise, and Result needs a grounded manifest plus real predictions.",
    )),
    (("reviewer", "planted", "flags"), "G9", Diagnosis(
        "A reviewer meta-test failed (G9).",
        "The static reviewer stopped flagging a planted fake, or it flagged clean real code.",
        "Check reviewer.py detection rules against tests/fixtures_bad.py. Fix the reviewer, not the test.",
    )),
    (("diagnose", "status", "report"), "D1", Diagnosis(
        "A diagnoser meta-test failed (D1).",
        "The status logic or a failure-to-diagnosis mapping in diagnose.py changed.",
        "Check diagnose.py build_report and the mapping tables against tests/test_diagnose.py.",
    )),
]

GENERIC_TEST_DIAG = Diagnosis(
    "A guard meta-test failed and did not map to a known guard.",
    "Either a newly added test, or a test outside the standard set.",
    "Open the failing test, read its assertion and exception, and trace it to the guard it exercises.",
)

PYTEST_UNPARSED_DIAG = Diagnosis(
    "The guard meta-tests exited non-zero but no individual FAILED line was parsed.",
    "Likely a collection or import error before any test ran, not a single assertion.",
    "Run `python -m pytest -q` directly and read the traceback at the top of the output.",
)


def diagnose_failed_test(node_id: str) -> tuple[str, Diagnosis]:
    """Map a failing pytest node id to (guard_id, Diagnosis)."""
    low = node_id.lower()
    for keys, guard_id, diag in GUARD_KEYWORDS:
        if any(k in low for k in keys):
            return guard_id, diag
    return "G?", GENERIC_TEST_DIAG


def parse_pytest_failures(pytest_output: str) -> list[str]:
    """Pull failing test node ids out of pytest's short summary lines.
    A line looks like: 'FAILED tests/test_guards.py::test_x - AssertionError'."""
    fails: list[str] = []
    for line in pytest_output.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAILED "):
            node = stripped[len("FAILED "):].split(" - ", 1)[0].strip()
            if node:
                fails.append(node)
    return fails


def render_finding(f: reviewer.Finding) -> str:
    diag = REVIEWER_DIAGNOSES.get(f.code, UNKNOWN_CODE_DIAG)
    guard = CODE_TO_GUARD.get(f.code, "G?")
    loc = f"{f.path}:{f.line}" if f.line else f.path
    return (
        f"  [{guard}/{f.code}] {loc}\n"
        f"    what:   {diag.plain}\n"
        f"    cause:  {diag.likely_cause}\n"
        f"    fix:    {diag.action}\n"
        f"    detail: {f.message}"
    )


def render_failed_test(node_id: str) -> str:
    guard, diag = diagnose_failed_test(node_id)
    return (
        f"  [{guard}] {node_id}\n"
        f"    what:   {diag.plain}\n"
        f"    cause:  {diag.likely_cause}\n"
        f"    fix:    {diag.action}"
    )


def structured_finding(f: reviewer.Finding) -> dict:
    """Build the JSON finding dict for one reviewer Finding (ERROR or WARN)."""
    diag = REVIEWER_DIAGNOSES.get(f.code, UNKNOWN_CODE_DIAG)
    guard = CODE_TO_GUARD.get(f.code, "G?")
    return {
        "guard": guard,
        "code": f.code,
        "path": f.path,
        "line": f.line,
        "severity": f.severity,
        "what": diag.plain,
        "cause": diag.likely_cause,
        "fix": diag.action,
        "detail": f.message,
    }


def structured_failed_test(node_id: str) -> dict:
    """Build the JSON finding dict for one failing guard meta-test node."""
    guard, diag = diagnose_failed_test(node_id)
    return {
        "guard": guard,
        "code": "GUARD_TEST",
        "path": node_id,
        "line": 0,
        "severity": "TEST",
        "what": diag.plain,
        "cause": diag.likely_cause,
        "fix": diag.action,
        "detail": "",
    }


def structured_pytest_unparsed() -> dict:
    """Build the JSON finding dict for the unparsed-pytest case."""
    return {
        "guard": "G8",
        "code": "PYTEST_UNPARSED",
        "path": "pytest",
        "line": 0,
        "severity": "TEST",
        "what": PYTEST_UNPARSED_DIAG.plain,
        "cause": PYTEST_UNPARSED_DIAG.likely_cause,
        "fix": PYTEST_UNPARSED_DIAG.action,
        "detail": "",
    }


@dataclass
class StatusReport:
    status: str
    reason: str
    attempted: list[str]
    recommendation: str
    diagnoses: list[str] = field(default_factory=list)
    structured: list[dict] = field(default_factory=list)

    def exit_code(self) -> int:
        return _EXIT_FOR_STATUS[self.status]

    def to_json_dict(self) -> dict:
        """Render the report as the structured JSON contract (schema stable
        across phases). One findings entry per reviewer Finding and per failing
        guard meta-test, built from the same structured list the text uses."""
        return {
            "status": self.status,
            "reason": self.reason,
            "attempted": list(self.attempted),
            "recommendation": self.recommendation,
            "exit_code": self.exit_code(),
            "findings": [dict(f) for f in self.structured],
        }

    def render(self) -> str:
        bar = "=" * 64
        lines = [bar, f"STATUS: {self.status}", f"REASON: {self.reason}", "ATTEMPTED:"]
        for step in self.attempted:
            lines.append(f"  - {step}")
        if self.diagnoses:
            lines.append("FINDINGS (root-cause diagnosis):")
            lines.extend(self.diagnoses)
        else:
            lines.append("FINDINGS: none.")
        lines.append(f"RECOMMENDATION: {self.recommendation}")
        lines.append(bar)
        return "\n".join(lines)


def build_report(
    findings: list[reviewer.Finding],
    failed_tests: list[str],
    *,
    tests_ran: bool,
    pytest_rc: int,
    attempted: list[str],
    needs_context: str | None = None,
) -> StatusReport:
    """Turn raw reviewer findings + guard-test results into one STATUS report.

    Pure and deterministic: given the same inputs it produces the same report.
    This is the piece the D1 meta-tests exercise with known failures."""
    errors = [f for f in findings if f.severity == "ERROR"]
    warns = [f for f in findings if f.severity == "WARN"]

    # A missing results dir is missing context, not a fake to fix.
    if needs_context is None:
        for f in errors:
            if f.code == "NO_RESULTS_DIR":
                needs_context = REVIEWER_DIAGNOSES["NO_RESULTS_DIR"].plain
                break

    diagnoses: list[str] = []
    diagnoses += [render_finding(f) for f in errors if f.code != "NO_RESULTS_DIR"]
    diagnoses += [render_failed_test(t) for t in failed_tests]
    diagnoses += [render_finding(f) for f in warns]

    # Structured findings, built in parallel and in the same order as the text
    # diagnoses so JSON and text come from one set of inputs.
    structured: list[dict] = []
    structured += [structured_finding(f) for f in errors if f.code != "NO_RESULTS_DIR"]
    structured += [structured_failed_test(t) for t in failed_tests]
    structured += [structured_finding(f) for f in warns]

    tests_failed_unparsed = tests_ran and pytest_rc != 0 and not failed_tests

    if needs_context is not None:
        status = NEEDS_CONTEXT
        reason = needs_context
        recommendation = "Provide the missing input above, then re-run `make diagnose`."
    elif errors or failed_tests or tests_failed_unparsed:
        status = BLOCKED
        if tests_failed_unparsed:
            diagnoses.append(render_failed_test("pytest::unparsed"))
            diagnoses[-1] = (
                "  [G8] pytest\n"
                f"    what:   {PYTEST_UNPARSED_DIAG.plain}\n"
                f"    cause:  {PYTEST_UNPARSED_DIAG.likely_cause}\n"
                f"    fix:    {PYTEST_UNPARSED_DIAG.action}"
            )
            structured.append(structured_pytest_unparsed())
        reason = (
            f"{len(errors)} reviewer error(s) and "
            f"{len(failed_tests)} failing guard test(s) block the pipeline."
        )
        recommendation = (
            "Fix each file/line above, following its 'fix:' line. Do not weaken "
            "or delete a guard to make it pass. Then re-run `make gate` and `make diagnose`."
        )
    elif warns:
        status = DONE_WITH_CONCERNS
        reason = f"No blocking issues, but {len(warns)} advisory warning(s) need a look."
        recommendation = (
            "Clear the warnings above (usually: seed an RNG, or remove a TODO), then re-run."
        )
    else:
        status = DONE
        reason = "G9 reviewer is clean and all G8 guard meta-tests pass."
        recommendation = "Safe to proceed. Re-run `make diagnose` after the next change."

    return StatusReport(status, reason, attempted, recommendation, diagnoses, structured)


def _run_pytest(harness_dir: str) -> tuple[int, str]:
    """Run the G8 guard meta-tests in a subprocess and return (rc, output).
    Subprocess so a diagnoser run never nests inside the pytest session it
    reports on. Uses check=False so we inspect the code instead of raising."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests"],
        cwd=harness_dir, capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Systematic diagnosis + STATUS report for the guard harness (gstack-style)."
    )
    # Default scope: the guard/provenance harness code plus the project's
    # pipeline code (the code an agent is allowed to fix). A missing pipeline/
    # dir is skipped by the reviewer, so this is safe in a bare harness. Meta
    # tooling that has to name the fake patterns (reviewer.py, diagnose.py,
    # tests, fixtures) is excluded by the same convention the gate uses, so it
    # does not false-flag itself.
    ap.add_argument("--paths", nargs="*", default=["guards", "provenance", "pipeline"],
                    help="code paths for the G9 reviewer")
    ap.add_argument("--outputs", help="audit produced result files in this dir instead of reviewing code")
    ap.add_argument("--exclude", nargs="*", default=("tests", "fixtures", "reviewer.py"))
    ap.add_argument("--no-tests", action="store_true", help="skip the G8 guard meta-tests")
    ap.add_argument("--json", action="store_true",
                    help="emit one structured JSON object instead of the text report")
    args = ap.parse_args(argv)

    harness_dir = os.path.dirname(os.path.abspath(__file__))

    if args.outputs:
        findings = reviewer.audit_outputs(args.outputs)
        attempted = [f"Audited produced results in '{args.outputs}' (G1/G5/G7)."]
        report = build_report(findings, [], tests_ran=False, pytest_rc=0, attempted=attempted)
        if args.json:
            print(json.dumps(report.to_json_dict(), indent=2))
        else:
            print(report.render())
        return report.exit_code()

    attempted = [f"Ran the G9 static reviewer over: {', '.join(args.paths)}."]
    findings = reviewer.review_paths(args.paths, exclude=tuple(args.exclude))

    failed_tests: list[str] = []
    pytest_rc = 0
    tests_ran = not args.no_tests
    if tests_ran:
        attempted.append("Ran the G8 guard meta-tests (python -m pytest -q).")
        pytest_rc, pytest_out = _run_pytest(harness_dir)
        failed_tests = parse_pytest_failures(pytest_out)
    else:
        attempted.append("Skipped the G8 guard meta-tests (--no-tests).")

    report = build_report(
        findings, failed_tests, tests_ran=tests_ran, pytest_rc=pytest_rc, attempted=attempted
    )
    if args.json:
        print(json.dumps(report.to_json_dict(), indent=2))
    else:
        print(report.render())
    return report.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
