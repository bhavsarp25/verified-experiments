"""agent/permissions.py

The tool permission hook. Phase 2 safety core.

A thin, framework-agnostic check the agentic loop (phase 3) wires into the
Claude Agent SDK's tool-permission callback. It is kept separate from the loop
so it can be unit-tested with no SDK and no model.

Policy:
  - Write tools (Edit, Write, ...) are allowed only for paths the fence in
    boundaries.classify() approves.
  - Bash is allowed only for a small set of read-only and make commands, and
    never with a shell operator that could redirect or chain into a write.
  - Read-only tools (Read, Grep, Glob) are allowed.
  - Anything else is denied. Deny by default.

The instruction the agent is given and the enforcement here say the same thing,
so a confused step or an injected instruction still cannot cross the fence.
"""
from __future__ import annotations

from dataclasses import dataclass

import boundaries


# Tools that write to disk and therefore must be path-checked.
WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

# Read-only tools the agent may always use.
READ_TOOLS = frozenset({"Read", "Grep", "Glob"})

# Bash is allowed only when the command starts with one of these. Every entry
# is read-only or a make target the harness defines.
ALLOWED_BASH_PREFIXES: tuple[str, ...] = (
    "make gate",
    "make audit",
    "make diagnose",
    "python diagnose.py",
    "python -m pytest",
    "pytest",
    "git status",
    "git diff",
    "ls",
    "cat ",
)

# Shell operators that could redirect, chain, or hide a write. Any of these in
# a Bash command is an automatic deny, even if the prefix looks allowed.
BLOCKED_BASH_TOKENS: tuple[str, ...] = (
    ">", "|", ";", "&", "`", "$(", "\n",
)


@dataclass(frozen=True)
class PermissionResult:
    allow: bool
    reason: str


def _extract_path(tool_input: dict | None) -> str | None:
    if not tool_input:
        return None
    for key in ("file_path", "notebook_path", "path"):
        value = tool_input.get(key)
        if value:
            return value
    return None


def _check_bash(command: str) -> PermissionResult:
    stripped = command.strip()
    if not stripped:
        return PermissionResult(False, "empty bash command")
    for token in BLOCKED_BASH_TOKENS:
        if token in command:
            return PermissionResult(False, f"bash command uses a blocked operator '{token}': {command}")
    if any(stripped.startswith(prefix) for prefix in ALLOWED_BASH_PREFIXES):
        return PermissionResult(True, f"allowed read-only or make command: {stripped}")
    return PermissionResult(False, f"bash command not on the allowlist: {stripped}")


def check_tool(
    tool_name: str,
    tool_input: dict | None,
    *,
    root: str,
    writable: tuple[str, ...] = boundaries.DEFAULT_WRITABLE,
) -> PermissionResult:
    """Decide whether the agent may run tool_name with tool_input. Deny by
    default. Write tools are routed through the path fence."""
    if tool_name in WRITE_TOOLS:
        path = _extract_path(tool_input)
        if path is None:
            return PermissionResult(False, f"{tool_name} call has no file path to check")
        decision = boundaries.classify(root, path, writable=writable)
        return PermissionResult(decision.allow, decision.reason)
    if tool_name == "Bash":
        command = (tool_input or {}).get("command", "")
        return _check_bash(command)
    if tool_name in READ_TOOLS:
        return PermissionResult(True, "read-only tool")
    return PermissionResult(False, f"tool not on the agent allowlist: {tool_name}")
