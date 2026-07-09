"""D1 meta-tests for the structured JSON output of the diagnoser.

Same spirit as tests/test_diagnose.py: feed build_report known inputs and
assert the JSON contract holds. The JSON must carry the same status and exit
code as the text report, one findings entry per reviewer Finding and per
failing guard meta-test, and it must round-trip through json unchanged. If one
of these fails, fix diagnose.py, not the test."""
import json

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


def test_clean_run_json_is_done_with_no_findings():
    r = _report()
    data = r.to_json_dict()
    assert data["status"] == diagnose.DONE
    assert data["exit_code"] == 0
    assert data["findings"] == []
    # Valid JSON: serialises and parses back to the same object.
    assert json.loads(json.dumps(data)) == data


def test_fabricated_reviewer_error_blocks_in_json():
    """A planted HARDCODED_METRIC ERROR must BLOCK, never pass, in JSON."""
    r = _report([_finding("HARDCODED_METRIC", path="pipeline/x.py", line=3)])
    data = r.to_json_dict()
    assert data["status"] == diagnose.BLOCKED
    assert data["exit_code"] == 1
    assert len(data["findings"]) == 1
    finding = data["findings"][0]
    assert finding["guard"] == "G9"
    assert finding["code"] == "HARDCODED_METRIC"
    assert finding["severity"] == "ERROR"
    assert finding["path"] == "pipeline/x.py"
    assert finding["line"] == 3
    assert finding["fix"]  # non-empty action line
    assert finding["detail"] == "planted"


def test_failing_guard_test_node_in_json():
    r = _report(failed_tests=["tests/test_guards.py::test_leakage_shuffle"], pytest_rc=1)
    data = r.to_json_dict()
    assert data["status"] == diagnose.BLOCKED
    assert len(data["findings"]) == 1
    finding = data["findings"][0]
    assert finding["code"] == "GUARD_TEST"
    assert finding["guard"] == "G3"  # leakage keyword maps to G3
    assert finding["severity"] == "TEST"
    assert finding["path"] == "tests/test_guards.py::test_leakage_shuffle"
    assert finding["line"] == 0
    assert finding["detail"] == ""
    assert finding["fix"]


def test_json_round_trips_unchanged():
    r = _report([_finding("HARDCODED_METRIC")], failed_tests=["x::test_reproducibility_seed"],
                pytest_rc=1)
    data = r.to_json_dict()
    assert json.loads(json.dumps(data)) == data


def test_needs_context_json_is_exit_two():
    r = _report([_finding("NO_RESULTS_DIR", path="results", line=0)])
    data = r.to_json_dict()
    assert data["status"] == diagnose.NEEDS_CONTEXT
    assert data["exit_code"] == 2


def test_unparsed_pytest_finding_in_json():
    r = _report(failed_tests=[], tests_ran=True, pytest_rc=1)
    data = r.to_json_dict()
    assert data["status"] == diagnose.BLOCKED
    assert len(data["findings"]) == 1
    finding = data["findings"][0]
    assert finding["guard"] == "G8"
    assert finding["code"] == "PYTEST_UNPARSED"
    assert finding["severity"] == "TEST"


def test_json_exit_code_matches_text_report():
    for findings, failed, rc in (
        ([], [], 0),
        ([_finding("HARDCODED_METRIC")], [], 0),
        ([], ["x::test_leakage_shuffle"], 1),
        ([_finding("NO_RESULTS_DIR", path="results", line=0)], [], 0),
    ):
        r = _report(findings, failed, pytest_rc=rc)
        assert r.to_json_dict()["exit_code"] == r.exit_code()
