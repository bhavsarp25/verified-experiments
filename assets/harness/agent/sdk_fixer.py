"""agent/sdk_fixer.py

Phase 3b: the live model-driven fixer.

This is the one place a language model touches the loop. It is handed exactly
one finding and asked to fix it. Everything it may do is bounded by the same
fence the rest of the harness uses:

  - `can_use_tool` delegates to `permissions.check_tool`, so every Edit, Write,
    and Bash the model attempts is checked against the writable allowlist and
    the protected-path list before it happens. A write to a guard is denied at
    the tool layer, not merely discouraged in the prompt.
  - The system prompt is `agent/contract.md` verbatim, so what the model is told
    and what the code enforces are the same words.
  - After the fixer returns, `run_loop` still runs the tamper meta-gate. The
    fixer is never trusted on its own.

It fails loud. If the SDK is missing, the CLI is absent, or the model makes no
edit, this raises. It never reports a fix it did not make.
"""
from __future__ import annotations

import asyncio
import os

import permissions


DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_MAX_TURNS = 12

# Tools the fixer may never request at all. Everything else falls through to the
# fence (the PreToolUse hook + can_use_tool), which decides per call.
#
# There is deliberately NO allowed_tools list. Naming a whole tool there
# auto-approves it BEFORE the callback runs, which silently shadows the fence
# (the SDK warns: CanUseToolShadowedWarning). An empty allow list means every
# call reaches our checks. Do not add allowed_tools back.
DISALLOWED_TOOLS = ["WebFetch", "WebSearch", "Task"]

# Tools that write, for the shadowing regression test to assert on.
WRITE_TOOL_NAMES = ("Edit", "Write", "MultiEdit", "NotebookEdit", "Bash")


def _require_sdk():
    """Import the Claude Agent SDK or raise with a clear, actionable message."""
    try:
        import claude_agent_sdk
    except ImportError as e:
        raise RuntimeError(
            "the live SDK fixer needs the Claude Agent SDK: pip install claude-agent-sdk. "
            "Until it is installed, run the loop with an injected fixer_fn."
        ) from e
    return claude_agent_sdk


def read_contract(root: str) -> str:
    """The boundary the agent is told, read from the file the fence protects."""
    path = os.path.join(root, "agent", "contract.md")
    if not os.path.isfile(path):
        raise RuntimeError(f"agent/contract.md is missing at {path}; refusing to run unbounded")
    with open(path, encoding="utf-8") as f:
        return f.read()


def build_system_prompt(root: str) -> str:
    return (
        "You are the fixer inside a guardrail harness. You repair one finding at a "
        "time in pipeline code. You never edit the harness that judges you: any "
        "write to guards/, provenance/, tests/, reviewer.py, diagnose.py, the "
        "Makefile, conftest.py, or agent/ will be denied at the tool layer and the "
        "run will abort. Make the smallest honest change that removes the finding. "
        "Never fabricate data, never silence an error, never weaken a guard.\n\n"
        "Your contract follows.\n\n" + read_contract(root)
    )


def build_fix_prompt(finding) -> str:
    """One finding, stated plainly, with the diagnosis already done."""
    location = f"{finding.path}:{finding.line}" if finding.line else finding.path
    lines = [
        f"Fix exactly one finding: [{finding.guard or '?'} {finding.code or '?'}] at {location}",
        "",
        f"What it means: {finding.what or '(not stated)'}",
        f"Likely root cause: {finding.cause or '(not stated)'}",
        f"Recommended fix: {finding.fix or '(not stated)'}",
    ]
    if finding.detail:
        lines.append(f"Reviewer detail: {finding.detail}")
    lines += [
        "",
        f"Edit only {finding.path}. Do not touch any other file.",
        "When the finding is resolved, stop. Do not fix anything else.",
    ]
    return "\n".join(lines)


def make_can_use_tool(root: str, writable=None):
    """Adapt our synchronous fence to the SDK's async permission callback. This
    is what makes the model's boundary the SAME boundary as the loop's."""
    sdk = _require_sdk()
    kwargs = {} if writable is None else {"writable": writable}

    async def can_use_tool(tool_name: str, tool_input: dict, context) -> object:
        decision = permissions.check_tool(tool_name, tool_input, root=root, **kwargs)
        if decision.allow:
            return sdk.PermissionResultAllow()
        return sdk.PermissionResultDeny(message=decision.reason)

    return can_use_tool


def make_pre_tool_use_hook(root: str, writable=None):
    """A PreToolUse hook enforcing the same fence. This is the SDK's own
    recommended way to gate EVERY tool call, and it is our primary enforcement.
    can_use_tool is kept as a second layer."""
    kwargs = {} if writable is None else {"writable": writable}

    async def pre_tool_use(input_data, tool_use_id, context) -> dict:
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input") or {}
        decision = permissions.check_tool(tool_name, tool_input, root=root, **kwargs)
        if decision.allow:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": decision.reason,
            }
        }

    return pre_tool_use


def build_options(root: str, *, model: str, max_turns: int, writable=None):
    """Assemble the SDK options with the fence installed. Kept separate so the
    shadowing regression test can assert no write tool is ever auto-approved."""
    sdk = _require_sdk()
    return sdk.ClaudeAgentOptions(
        system_prompt=build_system_prompt(root),
        can_use_tool=make_can_use_tool(root, writable),
        hooks={
            "PreToolUse": [
                sdk.HookMatcher(matcher=None, hooks=[make_pre_tool_use_hook(root, writable)])
            ]
        },
        disallowed_tools=list(DISALLOWED_TOOLS),
        permission_mode="default",   # our fence decides, nothing is bypassed
        cwd=root,
        model=model,
        max_turns=max_turns,
        setting_sources=None,        # ignore ambient settings that could allow-list tools
    )


async def _prompt_stream(text: str):
    """The SDK requires streaming input whenever a can_use_tool callback is set,
    so the one instruction is delivered as a single-message async iterable."""
    yield {
        "type": "user",
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
        "session_id": "fixer",
    }


async def _run_fixer(finding, root: str, model: str, max_turns: int, writable) -> int:
    sdk = _require_sdk()
    options = build_options(root, model=model, max_turns=max_turns, writable=writable)
    edits = 0
    async for message in sdk.query(prompt=_prompt_stream(build_fix_prompt(finding)), options=options):
        blocks = getattr(message, "content", None)
        if not blocks:
            continue
        for block in blocks:
            name = getattr(block, "name", None)
            if name in ("Edit", "Write", "MultiEdit"):
                edits += 1
    return edits


def sdk_fixer(
    finding,
    root: str,
    *,
    model: str = DEFAULT_MODEL,
    max_turns: int = DEFAULT_MAX_TURNS,
    writable=None,
) -> None:
    """Fix one finding with a live model, bounded by the fence. Raises if the
    model made no edit, so the loop never mistakes a no-op for progress."""
    edits = asyncio.run(_run_fixer(finding, root, model, max_turns, writable))
    if edits == 0:
        raise RuntimeError(
            f"the fixer made no edit for {finding.code or '?'} in {finding.path}. "
            "It may have been denied by the fence, or it refused. Nothing was changed."
        )
