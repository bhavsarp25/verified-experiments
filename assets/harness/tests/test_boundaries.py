"""Phase 2 meta-tests: the fence must hold.

Same spirit as the guard meta-tests. We hand the boundary deliberate attempts
to cross it (edit a guard, traverse out with .., make a guard writable, tamper
a protected file) and assert it refuses. The agent's only path to green is
fixing real pipeline code, never editing the harness that judges it. If one of
these fails, fix the fence, not the test."""
import os
import subprocess

import boundaries
import permissions


# --- helpers ----------------------------------------------------------------

def _fake_harness(tmp_path):
    """A minimal harness laid out like the real one, inside a fresh git repo,
    so the tamper meta-gate has real git state to read."""
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


# --- classify: who may write what ------------------------------------------

def test_pipeline_is_writable(tmp_path):
    root = _fake_harness(tmp_path)
    assert boundaries.classify(root, "pipeline/train.py").allow


def test_guard_is_not_writable(tmp_path):
    root = _fake_harness(tmp_path)
    d = boundaries.classify(root, "guards/leakage.py")
    assert not d.allow
    assert "protected" in d.reason


def test_traversal_into_a_guard_is_blocked(tmp_path):
    """pipeline/../guards/x.py resolves into a guard and must be denied."""
    root = _fake_harness(tmp_path)
    assert not boundaries.classify(root, "pipeline/../guards/leakage.py").allow


def test_path_outside_root_is_blocked(tmp_path):
    root = _fake_harness(tmp_path)
    assert not boundaries.classify(root, "../../etc/passwd").allow
    assert not boundaries.classify(root, "/etc/passwd").allow


def test_unlisted_path_is_denied_by_default(tmp_path):
    root = _fake_harness(tmp_path)
    assert not boundaries.classify(root, "random_notes.txt").allow


def test_cannot_make_a_guard_writable():
    """A config that tries to open guards/ for writing is rejected outright."""
    try:
        boundaries.validate_writable(("pipeline", "guards"))
        assert False, "validate_writable should reject a protected prefix"
    except ValueError as e:
        assert "guards" in str(e)


def test_agent_cannot_edit_its_own_fence(tmp_path):
    root = _fake_harness(tmp_path)
    for target in ("agent/boundaries.py", "agent/permissions.py", "agent/contract.md"):
        assert not boundaries.classify(root, target).allow, target


# --- the tamper meta-gate ---------------------------------------------------

def test_clean_repo_reports_no_tamper(tmp_path):
    root = _fake_harness(tmp_path)
    report = boundaries.changed_protected(root)
    assert report.clean
    assert report.changed == []


def test_editing_a_guard_is_detected_as_tamper(tmp_path):
    root = _fake_harness(tmp_path)
    with open(os.path.join(root, "guards", "leakage.py"), "a") as f:
        f.write("# weakened\n")
    report = boundaries.changed_protected(root)
    assert not report.clean
    assert "guards/leakage.py" in report.changed
    assert "TAMPER" in report.render()


def test_editing_pipeline_is_not_tamper(tmp_path):
    root = _fake_harness(tmp_path)
    with open(os.path.join(root, "pipeline", "train.py"), "a") as f:
        f.write("y = 2\n")
    assert boundaries.changed_protected(root).clean


def test_new_file_under_a_protected_dir_is_tamper(tmp_path):
    root = _fake_harness(tmp_path)
    open(os.path.join(root, "guards", "sneak.py"), "w").close()
    assert not boundaries.changed_protected(root).clean


def test_revert_restores_a_tampered_guard(tmp_path):
    root = _fake_harness(tmp_path)
    guard = os.path.join(root, "guards", "leakage.py")
    original = open(guard).read()
    with open(guard, "a") as f:
        f.write("# weakened\n")
    reverted = boundaries.revert_protected(root)
    assert "guards/leakage.py" in reverted
    assert open(guard).read() == original
    assert boundaries.changed_protected(root).clean


def test_revert_deletes_a_protected_untracked_file(tmp_path):
    root = _fake_harness(tmp_path)
    sneak = os.path.join(root, "guards", "sneak.py")
    open(sneak, "w").close()
    boundaries.revert_protected(root)
    assert not os.path.exists(sneak)


# --- run budget: no runaway loops -------------------------------------------

def test_budget_allows_progress():
    b = boundaries.RunBudget(max_iters=12, max_no_progress=3)
    for n in (5, 4, 3, 2):
        b.record(n)
        assert not b.should_stop()[0]


def test_budget_stops_on_no_progress():
    b = boundaries.RunBudget(max_iters=12, max_no_progress=3)
    for n in (5, 5, 5, 5):
        b.record(n)
    stop, reason = b.should_stop()
    assert stop
    assert "no progress" in reason


def test_budget_stops_on_iteration_cap():
    b = boundaries.RunBudget(max_iters=3, max_no_progress=99)
    for n in (5, 4, 3):
        b.record(n)
    assert b.should_stop()[0]


# --- permission hook --------------------------------------------------------

def test_hook_allows_write_to_pipeline(tmp_path):
    root = _fake_harness(tmp_path)
    r = permissions.check_tool("Edit", {"file_path": "pipeline/train.py"}, root=root)
    assert r.allow


def test_hook_denies_write_to_guard(tmp_path):
    root = _fake_harness(tmp_path)
    r = permissions.check_tool("Write", {"file_path": "guards/leakage.py"}, root=root)
    assert not r.allow


def test_hook_allows_make_targets(tmp_path):
    root = _fake_harness(tmp_path)
    for cmd in ("make gate", "make diagnose", "python -m pytest -q", "git status"):
        assert permissions.check_tool("Bash", {"command": cmd}, root=root).allow, cmd


def test_hook_denies_dangerous_bash(tmp_path):
    root = _fake_harness(tmp_path)
    for cmd in ("rm -rf guards", "echo x > guards/leakage.py", "make gate && rm x", "cat a | tee b"):
        assert not permissions.check_tool("Bash", {"command": cmd}, root=root).allow, cmd


def test_hook_allows_read_tools(tmp_path):
    root = _fake_harness(tmp_path)
    assert permissions.check_tool("Read", {"file_path": "guards/leakage.py"}, root=root).allow


def test_hook_denies_unknown_tool(tmp_path):
    root = _fake_harness(tmp_path)
    assert not permissions.check_tool("WebFetch", {"url": "http://x"}, root=root).allow
