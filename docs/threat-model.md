# Threat model

What the fence guarantees, what it does not, and why. Written for a reviewer who
wants to know where this breaks before they trust it.

The adversary we model is not a nation state. It is the ordinary one: a capable
model, under pressure to make the gate turn green, that will take the shortest
available path. Sometimes that path is fixing the code. Sometimes it is editing
the thing that judges the code. The whole design exists to make the second path
unavailable rather than merely discouraged.

## Trust boundary

```
  model  ->  tool call  ->  [ PreToolUse hook + can_use_tool ]  ->  filesystem
                                        |
                            permissions.check_tool
                                        |
                             boundaries.classify
                                        |
        after every edit:  boundaries.changed_protected  ->  revert + abort
```

Three independent layers, in order:

1. **Instruction.** `agent/contract.md` is the model's system prompt, verbatim.
   Necessary, never sufficient. Instruction is not a boundary.
2. **Enforcement at the tool layer.** Every tool call is checked before it runs.
   Deny by default. Path traversal is resolved (`pipeline/../guards/x.py`
   becomes `guards/x.py`) before the check.
3. **Detection after the fact.** The tamper meta-gate re-reads git state after
   every edit. If any protected source file changed, the run aborts and reverts.

Layer 2 can be bypassed only by code execution (see below). Layer 3 is the
backstop that still catches it.

## What is guaranteed

- **No tool call can write a protected file.** `guards/`, `provenance/`,
  `tests/`, `reviewer.py`, `diagnose.py`, `conftest.py`, `Makefile`,
  `pytest.ini`, and everything under `agent/` are denied at the tool layer, and
  a write to any of them aborts the run.
- **A metric cannot be fabricated even by a compliant agent.** A `Result`
  refuses to exist without a grounded provenance manifest and a real predictions
  file, and G5 recomputes every number from stored predictions by a separate
  code path. This is structural, not a check that can be skipped.
- **The agent cannot widen its own fence.** `validate_writable` rejects any
  writable prefix that resolves into a protected path, and the fence files are
  themselves protected.
- **The loop terminates.** `RunBudget` caps iterations and requires findings to
  strictly decrease, otherwise it escalates to a human.
- **A no-op is never progress.** The fixer raises if the model made no edit.

## What is NOT guaranteed

Be clear about this. **The fence is a boundary on tool calls. It is not a
sandbox.**

- **Arbitrary code execution is possible in principle.** The agent may write
  Python into `pipeline/`. Any process that later imports or runs that code
  executes it with the agent's privileges. We closed the obvious path: `pytest`
  is not on the bash allowlist, and `pytest.ini` (protected) scopes collection
  to `tests/`, so a `pipeline/conftest.py` or `pipeline/test_*.py` is never
  imported by `make gate`. But the real experiment, by definition, runs the
  pipeline code. If the model is untrusted, **run it in a container or VM.** The
  tamper meta-gate remains the backstop: any protected file altered by executed
  code is detected and reverted.
- **Bytecode is excluded from tamper detection, not from the write fence.**
  `.pyc` and `__pycache__` under a protected directory do not count as tamper,
  because build artifacts appear for innocent reasons and would otherwise make
  the gate useless without a `.gitignore`. A crafted `.pyc` is not a write
  channel: `classify` still denies writing any path under a protected directory,
  including `guards/__pycache__/`. See
  `tests/test_boundaries.py::test_artifact_filter_is_not_a_write_channel`.
- **The revert depends on git.** `revert_protected` restores from `HEAD`. It
  needs a committed, clean baseline. On a dirty tree it will revert your
  uncommitted changes to protected files too. Commit before you run the agent.
- **The bash allowlist is a prefix match plus a blocked-operator list.** It
  rejects redirects, pipes, chaining, subshells, and backticks. It is
  intentionally small. Widening it is the easiest way to reintroduce a hole.
- **G9 is a static AST reviewer.** It catches the patterns it knows
  (hardcoded metric literals, silent excepts, synthetic loaders, fabricated
  fallbacks). It is a tripwire for the common failure modes, not a proof of
  honesty. An adversary who knows the rules can write around them. The
  structural guards (G1, G5, G7) are what make that not matter for a *reported
  number*.

## The bug this document exists because of

The first live run silently disabled the entire tool-layer fence. Naming whole
tools in the SDK's `allowed_tools` auto-approves them **before** `can_use_tool`
is consulted. The prompt still said "do not edit guards" and nothing enforced
it. The SDK said so plainly in a warning we had to go read the log to see.

Two rules came out of it, and both are now tests:

- Never put a write tool in an allow-list.
  (`test_no_write_tool_is_ever_auto_approved`)
- Never let a fence-allowed command execute code the agent wrote.
  (`test_bare_pytest_is_not_allowed`, `test_pytest_collection_is_scoped_to_tests`)

If you change `permissions.py`, `boundaries.py`, or `sdk_fixer.py`, assume you
have reintroduced one of these until the tests say otherwise.

## Reporting

Found a way through? Open an issue. A working escape against a committed
baseline is the most useful contribution this repo can receive.
