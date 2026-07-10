"""Phase 3b meta-tests: the live fixer runs inside the same fence.

No network and no model here. We test the parts that decide what the model is
allowed to do: the permission callback adapter, the prompt built from the
contract, and the refusal to report a fix that never happened. If the adapter
ever starts allowing a write to a guard, this fails."""
import asyncio
import os
import subprocess

import pytest

# The live fixer is optional: it needs the Claude Agent SDK. A fresh clone that
# installs only requirements.txt (numpy, pytest) skips these cleanly instead of
# erroring. CI installs the SDK so these run for real.
pytest.importorskip("claude_agent_sdk")

import loop
import sdk_fixer


def _fake_harness(tmp_path):
    """A minimal harness in a fresh git repo, mirroring tests/test_boundaries.py."""
    root = tmp_path / "proj"
    (root / "guards").mkdir(parents=True)
    (root / "pipeline").mkdir()
    (root / "agent").mkdir()
    (root / "guards" / "leakage.py").write_text("def check():\n    return True\n")
    (root / "pipeline" / "train.py").write_text("accuracy = 0.99\n")
    (root / "agent" / "contract.md").write_text("# Agent contract\n\nFix pipeline code only.\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"],
        cwd=root, check=True,
    )
    return str(root)


def _finding(path="pipeline/train.py"):
    return loop.Finding(
        guard="G9", code="HARDCODED_METRIC", path=path, line=1, severity="ERROR",
        what="a metric was assigned a literal", cause="typed in by hand",
        fix="compute it from stored predictions", detail="'accuracy' assigned 0.99",
    )


# --- the permission adapter is the fence ------------------------------------

def _decide(root, tool, tool_input):
    cb = sdk_fixer.make_can_use_tool(root)
    return asyncio.run(cb(tool, tool_input, None))


def test_fixer_may_edit_pipeline(tmp_path):
    root = _fake_harness(tmp_path)
    result = _decide(root, "Edit", {"file_path": "pipeline/train.py"})
    assert result.behavior == "allow"


def test_fixer_may_not_edit_a_guard(tmp_path):
    root = _fake_harness(tmp_path)
    result = _decide(root, "Write", {"file_path": "guards/leakage.py"})
    assert result.behavior == "deny"
    assert "protected" in result.message


def test_fixer_may_not_edit_the_tests_that_judge_it(tmp_path):
    root = _fake_harness(tmp_path)
    assert _decide(root, "Edit", {"file_path": "tests/test_guards.py"}).behavior == "deny"


def test_fixer_may_not_escape_the_root(tmp_path):
    root = _fake_harness(tmp_path)
    assert _decide(root, "Edit", {"file_path": "../../etc/passwd"}).behavior == "deny"


def test_fixer_may_not_shell_around_the_fence(tmp_path):
    root = _fake_harness(tmp_path)
    assert _decide(root, "Bash", {"command": "echo x > guards/leakage.py"}).behavior == "deny"
    assert _decide(root, "Bash", {"command": "rm -rf guards"}).behavior == "deny"


def test_fixer_may_run_the_gate(tmp_path):
    root = _fake_harness(tmp_path)
    assert _decide(root, "Bash", {"command": "make gate"}).behavior == "allow"


# --- the fence must not be shadowed by an allow-list ------------------------

def test_no_write_tool_is_ever_auto_approved(tmp_path):
    """Regression: naming a whole tool in allowed_tools auto-approves it BEFORE
    can_use_tool runs, silently disabling the fence (CanUseToolShadowedWarning).
    A live run once did exactly this. No write tool may appear in allowed_tools."""
    root = _fake_harness(tmp_path)
    options = sdk_fixer.build_options(root, model="claude-opus-4-8", max_turns=4)
    allowed = list(getattr(options, "allowed_tools", []) or [])
    for tool in sdk_fixer.WRITE_TOOL_NAMES:
        assert tool not in allowed, f"{tool} is auto-approved and bypasses the fence"


def test_options_install_a_pre_tool_use_hook(tmp_path):
    root = _fake_harness(tmp_path)
    options = sdk_fixer.build_options(root, model="claude-opus-4-8", max_turns=4)
    assert options.hooks and "PreToolUse" in options.hooks
    assert options.setting_sources is None      # ambient allow-rules cannot shadow us
    assert options.permission_mode == "default"  # nothing is bypassed


def _hook(root, tool, tool_input):
    cb = sdk_fixer.make_pre_tool_use_hook(root)
    return asyncio.run(cb({"tool_name": tool, "tool_input": tool_input}, None, None))


def test_hook_denies_guard_write(tmp_path):
    root = _fake_harness(tmp_path)
    out = _hook(root, "Write", {"file_path": "guards/leakage.py"})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "protected" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_hook_allows_pipeline_write(tmp_path):
    root = _fake_harness(tmp_path)
    assert _hook(root, "Edit", {"file_path": "pipeline/train.py"}) == {}


def test_hook_denies_shell_redirect_into_a_guard(tmp_path):
    root = _fake_harness(tmp_path)
    out = _hook(root, "Bash", {"command": "echo x > guards/leakage.py"})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# --- prompt is built from the contract the fence protects -------------------

def test_system_prompt_embeds_the_contract(tmp_path):
    root = _fake_harness(tmp_path)
    prompt = sdk_fixer.build_system_prompt(root)
    assert "Fix pipeline code only." in prompt          # the contract, verbatim
    assert "never edit the harness" in prompt.lower()


def test_missing_contract_refuses_to_run(tmp_path):
    root = _fake_harness(tmp_path)
    os.remove(os.path.join(root, "agent", "contract.md"))
    with pytest.raises(RuntimeError, match="contract.md is missing"):
        sdk_fixer.build_system_prompt(root)


def test_fix_prompt_names_one_finding_and_one_file():
    prompt = sdk_fixer.build_fix_prompt(_finding())
    assert "HARDCODED_METRIC" in prompt
    assert "pipeline/train.py:1" in prompt
    assert "compute it from stored predictions" in prompt
    assert "Edit only pipeline/train.py" in prompt
    assert "Do not touch any other file." in prompt


# --- never report a fix that did not happen ---------------------------------

def test_no_edit_raises_instead_of_claiming_success(tmp_path, monkeypatch):
    """If the model makes zero edits, the fixer must raise, not return quietly.
    A silent no-op would let the loop mistake nothing for progress."""
    root = _fake_harness(tmp_path)
    monkeypatch.setattr(sdk_fixer, "_run_fixer", lambda *a, **k: _zero())

    async def _noop():
        return 0

    def _zero():
        return _noop()

    with pytest.raises(RuntimeError, match="made no edit"):
        sdk_fixer.sdk_fixer(_finding(), root)
