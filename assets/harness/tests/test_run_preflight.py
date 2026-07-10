"""The agent must refuse to run on a dirty baseline.

boundaries.revert_protected restores protected files from HEAD, so if the tree
already has uncommitted changes to a protected file, a TAMPER abort would
discard the user's own work. preflight() catches that before the loop starts.

run.py imports the optional Agent SDK, so skip if it is absent."""
import os
import subprocess

import pytest

pytest.importorskip("claude_agent_sdk")

import run


def _fake_harness(tmp_path):
    root = tmp_path / "proj"
    (root / "guards").mkdir(parents=True)
    (root / "pipeline").mkdir()
    (root / "agent").mkdir()
    (root / "guards" / "leakage.py").write_text("def check():\n    return True\n")
    (root / "pipeline" / "train.py").write_text("x = 1\n")
    (root / "agent" / "contract.md").write_text("# contract\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"],
        cwd=root, check=True,
    )
    return str(root)


def test_preflight_passes_on_a_clean_tree(tmp_path):
    assert run.preflight(_fake_harness(tmp_path)) is None


def test_preflight_refuses_when_a_protected_file_is_dirty(tmp_path):
    root = _fake_harness(tmp_path)
    with open(os.path.join(root, "guards", "leakage.py"), "a") as f:
        f.write("# uncommitted\n")
    reason = run.preflight(root)
    assert reason is not None
    assert "guards/leakage.py" in reason
    assert "commit or stash" in reason.lower()


def test_preflight_ignores_dirty_pipeline(tmp_path):
    """A dirty pipeline is fine; only protected files would be lost on revert."""
    root = _fake_harness(tmp_path)
    with open(os.path.join(root, "pipeline", "train.py"), "a") as f:
        f.write("y = 2\n")
    assert run.preflight(root) is None
