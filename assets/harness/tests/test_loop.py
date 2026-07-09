"""Phase 3 loop meta-tests: the orchestrator must obey the fence.

Same anti-faking spirit as the boundary meta-tests. We drive the loop with fake
diagnose and fixer callables and assert the state machine does the safe thing:
it stops green on DONE, stops on NEEDS_CONTEXT, obeys the human checkpoint,
refuses to edit a protected file, catches a tampering fixer and reverts, and
escalates when it makes no progress. If one of these fails, fix the loop, not
the test.

The loop takes injected dependencies on purpose so it is fully deterministic
here, with no live model. The real SDK fixer is a later, thin binding that plugs
in as fixer_fn without changing any of this behaviour."""
import os
import subprocess

import boundaries
import loop
from loop import DiagnoseResult, Finding, Plan, auto_approve, auto_deny, run_loop


# --- helpers ----------------------------------------------------------------

def _fake_harness(tmp_path):
    """A minimal harness laid out like the real one, inside a fresh git repo, so
    the tamper meta-gate has real git state to read. Mirrors the helper in
    test_boundaries.py."""
    root = tmp_path / "proj"
    (root / "guards").mkdir(parents=True)
    (root / "pipeline").mkdir()
    (root / "agent").mkdir()
    (root / "guards" / "leakage.py").write_text("def check():\n    return True\n")
    (root / "pipeline" / "train.py").write_text("x = 1\n")
    (root / "Makefile").write_text("gate:\n\techo ok\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"],
        cwd=root, check=True,
    )
    return str(root)


def _finding(path="pipeline/train.py", severity="ERROR", guard="G9", code="HARDCODED_METRIC"):
    return Finding(guard=guard, code=code, path=path, line=1, severity=severity,
                   what="a fake", cause="root cause", fix="fix it")


def _blocked(*findings):
    return DiagnoseResult(status="BLOCKED", reason="findings open", findings=list(findings))


def _done():
    return DiagnoseResult(status="DONE", reason="gate green")


class _ScriptedDiagnose:
    """Returns each queued DiagnoseResult in turn, then repeats the last one
    forever. Records how many times it was called."""

    def __init__(self, *results):
        if not results:
            raise ValueError("need at least one scripted diagnose result")
        self._results = list(results)
        self.calls = 0

    def __call__(self, root):
        idx = min(self.calls, len(self._results) - 1)
        self.calls += 1
        return self._results[idx]


class _CountingFixer:
    """A fixer that records its calls. `action` optionally performs a real side
    effect (write a file) so we can test tamper and no-progress paths."""

    def __init__(self, action=None):
        self.calls = 0
        self._action = action

    def __call__(self, finding, root):
        self.calls += 1
        if self._action is not None:
            self._action(finding, root)


# --- happy path -------------------------------------------------------------

def test_happy_path_reaches_done_in_two_iterations(tmp_path):
    root = _fake_harness(tmp_path)
    diagnose = _ScriptedDiagnose(_blocked(_finding()), _done())
    fixer = _CountingFixer()
    result = run_loop(root, diagnose_fn=diagnose, fixer_fn=fixer, checkpoint_fn=auto_approve)
    assert result.outcome == "DONE"
    assert result.iterations == 2
    assert fixer.calls == 1
    assert diagnose.calls == 2


# --- checkpoint deny --------------------------------------------------------

def test_checkpoint_deny_stops_and_never_edits(tmp_path):
    root = _fake_harness(tmp_path)
    diagnose = _ScriptedDiagnose(_blocked(_finding()))
    fixer = _CountingFixer()
    result = run_loop(root, diagnose_fn=diagnose, fixer_fn=fixer, checkpoint_fn=auto_deny)
    assert result.outcome == "STOPPED_BY_HUMAN"
    assert fixer.calls == 0


# --- the fence: a guard target is refused before any edit -------------------

def test_fence_refuses_a_guard_target(tmp_path):
    root = _fake_harness(tmp_path)
    diagnose = _ScriptedDiagnose(_blocked(_finding(path="guards/leakage.py")))

    def _would_edit_guard(finding, root):
        with open(os.path.join(root, "guards", "leakage.py"), "a") as f:
            f.write("# weakened\n")

    fixer = _CountingFixer(action=_would_edit_guard)
    result = run_loop(root, diagnose_fn=diagnose, fixer_fn=fixer, checkpoint_fn=auto_approve)
    assert result.outcome == "BLOCKED_BY_FENCE"
    assert fixer.calls == 0
    # The guard on disk is untouched.
    assert boundaries.changed_protected(root).clean


# --- tamper: a fixer that writes a protected file is caught and reverted -----

def test_tamper_is_caught_and_reverted(tmp_path):
    root = _fake_harness(tmp_path)
    diagnose = _ScriptedDiagnose(_blocked(_finding(path="pipeline/train.py")))

    def _sneak_into_a_guard(finding, root):
        # The plan targets pipeline/, but the fixer misbehaves and edits a guard.
        with open(os.path.join(root, "guards", "leakage.py"), "a") as f:
            f.write("# tampered\n")

    fixer = _CountingFixer(action=_sneak_into_a_guard)
    result = run_loop(root, diagnose_fn=diagnose, fixer_fn=fixer, checkpoint_fn=auto_approve)
    assert result.outcome == "TAMPER"
    assert fixer.calls == 1
    # revert_protected restored the guard, so the repo is clean again.
    assert boundaries.changed_protected(root).clean


# --- no progress: same findings forever escalates within the budget ---------

def test_no_progress_escalates(tmp_path):
    root = _fake_harness(tmp_path)
    two = _blocked(_finding(path="pipeline/train.py", code="A"),
                   _finding(path="pipeline/train.py", code="B"))
    diagnose = _ScriptedDiagnose(two)  # repeats forever

    fixer = _CountingFixer()  # changes nothing, so findings never decrease
    budget = boundaries.RunBudget(max_iters=12, max_no_progress=3)
    result = run_loop(root, diagnose_fn=diagnose, fixer_fn=fixer,
                      checkpoint_fn=auto_approve, budget=budget)
    assert result.outcome == "ESCALATED"
    assert "no progress" in result.message
    assert result.iterations <= budget.max_iters


# --- NEEDS_CONTEXT passes straight through to a stop ------------------------

def test_needs_context_stops_immediately(tmp_path):
    root = _fake_harness(tmp_path)
    diagnose = _ScriptedDiagnose(
        DiagnoseResult(status="NEEDS_CONTEXT", reason="missing dataset path")
    )
    fixer = _CountingFixer()
    result = run_loop(root, diagnose_fn=diagnose, fixer_fn=fixer, checkpoint_fn=auto_approve)
    assert result.outcome == "NEEDS_CONTEXT"
    assert result.iterations == 1
    assert fixer.calls == 0


# --- DONE_WITH_CONCERNS is also a green stop --------------------------------

def test_done_with_concerns_stops_green(tmp_path):
    root = _fake_harness(tmp_path)
    diagnose = _ScriptedDiagnose(
        DiagnoseResult(status="DONE_WITH_CONCERNS", reason="one warning left")
    )
    result = run_loop(root, diagnose_fn=diagnose, fixer_fn=_CountingFixer(),
                      checkpoint_fn=auto_approve)
    assert result.outcome == "DONE"


# --- JSON parsing seam (no diagnose.py invocation) --------------------------

def test_parse_diagnose_json_reads_the_contract():
    text = (
        '{"status":"BLOCKED","reason":"one fake","attempted":["make gate"],'
        '"recommendation":"fix pipeline/x.py","exit_code":1,'
        '"findings":[{"guard":"G9","code":"HARDCODED_METRIC","path":"pipeline/x.py",'
        '"line":42,"severity":"ERROR","what":"w","cause":"c","fix":"f","detail":"d"}]}'
    )
    parsed = loop.parse_diagnose_json(text)
    assert parsed.status == "BLOCKED"
    assert parsed.exit_code == 1
    assert len(parsed.findings) == 1
    assert parsed.findings[0].path == "pipeline/x.py"
    assert parsed.findings[0].is_actionable()
    assert parsed.actionable() == parsed.findings


def test_parse_diagnose_json_fails_loud_on_garbage():
    try:
        loop.parse_diagnose_json("not json at all")
        assert False, "parse_diagnose_json should raise on non-JSON"
    except RuntimeError as e:
        assert "valid JSON" in str(e)


def test_parse_diagnose_json_rejects_unknown_status():
    try:
        loop.parse_diagnose_json('{"status":"WOBBLE","findings":[]}')
        assert False, "an unknown status must be rejected"
    except ValueError as e:
        assert "unknown status" in str(e)


# --- render uses the STATUS vocabulary --------------------------------------

def test_result_render_is_plain_language(tmp_path):
    root = _fake_harness(tmp_path)
    diagnose = _ScriptedDiagnose(_blocked(_finding()), _done())
    result = run_loop(root, diagnose_fn=diagnose, fixer_fn=_CountingFixer(),
                      checkpoint_fn=auto_approve)
    text = result.render()
    assert "DONE" in text
    assert "iterations: 2" in text


def test_plan_render_shows_target_and_cause():
    plan = Plan(finding=_finding(), target="pipeline/train.py")
    text = plan.render()
    assert "pipeline/train.py" in text
    assert "root cause" in text
