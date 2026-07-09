"""D1 meta-tests: the diagnosis + STATUS layer must call a fake a fake.

Same spirit as the G8 guard meta-tests. We feed the diagnoser known-bad
inputs (a fabricated reviewer ERROR, a failing guard test, a missing results
dir) and assert it reports the right STATUS and never says DONE when something
is wrong. Clean input must report DONE. If one of these fails, fix diagnose.py,
not the test."""
import diagnose
import reviewer


def _finding(code, severity="ERROR", path="guards/x.py", line=3, message="planted"):
    return reviewer.Finding(severity, path, line, code, message)


def _report(findings=None, failed_tests=None, *, tests_ran=True, pytest_rc=0):
    return diagnose.build_report(
        findings or [],
        failed_tests or [],
        tests_ran=tests_ran,
        pytest_rc=pytest_rc,
        attempted=["test"],
    )


def test_clean_inputs_report_done():
    r = _report()
    assert r.status == diagnose.DONE
    assert r.exit_code() == 0
    assert "FINDINGS: none." in r.render()


def test_fabricated_metric_blocks_never_done():
    """Deliberate fake: a HARDCODED_METRIC error must BLOCK, not pass."""
    r = _report([_finding("HARDCODED_METRIC")])
    assert r.status == diagnose.BLOCKED
    assert r.exit_code() == 1
    out = r.render()
    assert "G9/HARDCODED_METRIC" in out
    assert "fix:" in out  # every finding carries an action line


def test_failing_guard_test_blocks():
    r = _report(failed_tests=["tests/test_guards.py::test_leakage_shuffle"], pytest_rc=1)
    assert r.status == diagnose.BLOCKED
    assert "[G3]" in r.render()  # leakage keyword maps to G3


def test_warning_only_is_concerns():
    r = _report([_finding("TODO", severity="WARN")])
    assert r.status == diagnose.DONE_WITH_CONCERNS
    assert r.exit_code() == 0


def test_missing_results_dir_needs_context_not_blocked():
    """A missing results dir is missing input, not a fake to fix."""
    r = _report([_finding("NO_RESULTS_DIR", path="results", line=0)])
    assert r.status == diagnose.NEEDS_CONTEXT
    assert r.exit_code() == 2


def test_unparsed_pytest_failure_still_blocks():
    """Tests exited non-zero but nothing parsed (e.g. import error): still block."""
    r = _report(failed_tests=[], tests_ran=True, pytest_rc=1)
    assert r.status == diagnose.BLOCKED
    assert "[G8] pytest" in r.render()
    assert "no individual FAILED line was parsed" in r.render()


def test_parse_pytest_failures_reads_summary_lines():
    out = (
        "FAILED tests/test_guards.py::test_a - AssertionError\n"
        "FAILED tests/test_reviewer.py::test_b - ValueError: x\n"
        "1 passed, 2 failed\n"
    )
    fails = diagnose.parse_pytest_failures(out)
    assert fails == [
        "tests/test_guards.py::test_a",
        "tests/test_reviewer.py::test_b",
    ]


def test_failed_test_maps_to_expected_guard():
    assert diagnose.diagnose_failed_test("x::test_reproducibility_seed")[0] == "G6"
    assert diagnose.diagnose_failed_test("x::test_manifest_grounded")[0] == "G1"
    assert diagnose.diagnose_failed_test("x::test_unknown_thing")[0] == "G?"


def test_every_reviewer_code_has_a_diagnosis():
    """No reviewer code should fall through to the unknown-code note in
    normal operation. If the reviewer grows a new code, add its diagnosis."""
    known = set(diagnose.REVIEWER_DIAGNOSES)
    for code in ("HARDCODED_METRIC", "SILENT_EXCEPT", "FABRICATED_FALLBACK",
                 "SYNTHETIC_LOADER", "SYNTAX", "TODO",
                 "UNGROUNDED_RESULT", "MISSING_PREDICTIONS", "NO_RESULTS_DIR"):
        assert code in known, f"no diagnosis for reviewer code {code}"
