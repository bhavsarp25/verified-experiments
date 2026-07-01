"""G9 meta-tests: the reviewer must flag every planted fake, and must give the
real guard/provenance code a clean bill of health."""
import os

import reviewer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BAD = os.path.join(HERE, "fixtures_bad.py")


def _codes(findings):
    return {f.code for f in findings}


def test_reviewer_flags_every_planted_fake():
    findings = reviewer.review_source(BAD)
    codes = _codes(findings)
    for expected in ("HARDCODED_METRIC", "SILENT_EXCEPT", "FABRICATED_FALLBACK", "SYNTHETIC_LOADER"):
        assert expected in codes, f"reviewer missed {expected}: found {codes}"
    assert any(f.severity == "ERROR" for f in findings)


def test_reviewer_passes_the_real_code():
    """The actual guard + provenance code must be clean (no ERROR findings).
    If this fails, our own code contains a fake pattern - fix the code, not
    the test."""
    findings = reviewer.review_paths(
        [os.path.join(ROOT, "guards"), os.path.join(ROOT, "provenance")],
        exclude=("tests", "fixtures", "reviewer.py"),
    )
    errors = [f for f in findings if f.severity == "ERROR"]
    assert not errors, "real code tripped the reviewer:\n" + "\n".join(str(e) for e in errors)


def test_output_audit_rejects_ungrounded_result(tmp_path):
    (tmp_path / "bogus.result.json").write_text('{"value": 0.9, "manifest": {}, "predictions_path": "x"}')
    findings = reviewer.audit_outputs(str(tmp_path))
    codes = _codes(findings)
    assert "UNGROUNDED_RESULT" in codes
    assert "MISSING_PREDICTIONS" in codes
