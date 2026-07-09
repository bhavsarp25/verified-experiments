"""agent/loop.py

Phase 3: the autonomous fix loop, built as a testable orchestrator.

The loop runs diagnose, reads the STATUS, and on BLOCKED picks one finding,
forms a plan (root cause plus one target file), asks a human checkpoint before
editing, applies the fix, then enforces the tamper meta-gate and the run budget
every iteration. It never edits a protected file: the only path to green is
fixing pipeline code.

This module wires nothing to a live model. Every side effect the loop needs is
injected as a callable so the whole state machine is deterministic and unit
testable with fakes, in the anti-faking spirit of this repo:

  diagnose_fn(root)          -> DiagnoseResult   (status plus findings)
  fixer_fn(finding, root)    -> None             (applies one edit)
  checkpoint_fn(plan)        -> bool             (human approve or deny)

The real Claude Agent SDK adapter is a later, thin binding (Phase 3b). It plugs
in as fixer_fn without changing this loop.

The fence in boundaries.py is the authority. This loop calls into it, it never
reimplements the boundary.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field

import boundaries
from boundaries import DEFAULT_WRITABLE, RunBudget


# Findings this loop will act on. WARN is advisory and never drives an edit.
ACTIONABLE_SEVERITIES: frozenset[str] = frozenset({"ERROR", "TEST"})

# Statuses that mean the gate is green enough to stop with success.
DONE_STATUSES: frozenset[str] = frozenset({"DONE", "DONE_WITH_CONCERNS"})

# Every status the diagnose contract may emit. An unknown status is a bug, not
# something to silently pass through.
KNOWN_STATUSES: frozenset[str] = frozenset(
    {"DONE", "DONE_WITH_CONCERNS", "BLOCKED", "NEEDS_CONTEXT"}
)


# --- the diagnose JSON contract ---------------------------------------------

@dataclass
class Finding:
    """One diagnosis finding, matching the diagnose.py --json schema."""

    guard: str = ""
    code: str = ""
    path: str = ""
    line: int = 0
    severity: str = ""
    what: str = ""
    cause: str = ""
    fix: str = ""
    detail: str = ""

    @classmethod
    def from_dict(cls, obj: dict) -> "Finding":
        if not isinstance(obj, dict):
            raise ValueError(f"a finding must be a JSON object, got {type(obj).__name__}")
        return cls(
            guard=str(obj.get("guard", "")),
            code=str(obj.get("code", "")),
            path=str(obj.get("path", "")),
            line=int(obj.get("line", 0) or 0),
            severity=str(obj.get("severity", "")),
            what=str(obj.get("what", "")),
            cause=str(obj.get("cause", "")),
            fix=str(obj.get("fix", "")),
            detail=str(obj.get("detail", "")),
        )

    def is_actionable(self) -> bool:
        return self.severity in ACTIONABLE_SEVERITIES


@dataclass
class DiagnoseResult:
    """The whole diagnose.py --json object, parsed."""

    status: str
    reason: str = ""
    attempted: list[str] = field(default_factory=list)
    recommendation: str = ""
    exit_code: int = 0
    findings: list[Finding] = field(default_factory=list)

    def actionable(self) -> list[Finding]:
        return [f for f in self.findings if f.is_actionable()]

    @classmethod
    def from_dict(cls, obj: dict) -> "DiagnoseResult":
        if not isinstance(obj, dict):
            raise ValueError(f"diagnose output must be a JSON object, got {type(obj).__name__}")
        status = str(obj.get("status", ""))
        if status not in KNOWN_STATUSES:
            raise ValueError(
                f"diagnose returned an unknown status {status!r}; "
                f"expected one of {sorted(KNOWN_STATUSES)}"
            )
        raw_findings = obj.get("findings", [])
        if not isinstance(raw_findings, list):
            raise ValueError("diagnose 'findings' must be a JSON array")
        return cls(
            status=status,
            reason=str(obj.get("reason", "")),
            attempted=[str(a) for a in obj.get("attempted", [])],
            recommendation=str(obj.get("recommendation", "")),
            exit_code=int(obj.get("exit_code", 0) or 0),
            findings=[Finding.from_dict(f) for f in raw_findings],
        )


def parse_diagnose_json(text: str) -> DiagnoseResult:
    """Parse one diagnose.py --json object. Fail loud on non-JSON: never guess a
    status or invent findings when the input cannot be read."""
    stripped = text.strip()
    if not stripped:
        raise RuntimeError("diagnose produced no output to parse as JSON")
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"diagnose did not emit valid JSON: {e}\n---\n{stripped[:2000]}") from e
    return DiagnoseResult.from_dict(obj)


def _diagnose_python(root: str) -> str:
    """The interpreter to run diagnose.py with: the harness venv if it exists,
    otherwise the system python3."""
    venv_python = os.path.join(root, ".venv", "bin", "python")
    return venv_python if os.path.isfile(venv_python) else "python3"


def diagnose_via_subprocess(root: str) -> DiagnoseResult:
    """Run `python diagnose.py --json` at root and parse the one JSON object it
    prints. A nonzero exit code is normal for BLOCKED, so it is not itself an
    error; the JSON carries the real status. We fail loud only when the process
    cannot start or emits something that is not the JSON contract."""
    try:
        proc = subprocess.run(
            [_diagnose_python(root), "diagnose.py", "--json"],
            cwd=root, capture_output=True, text=True,
        )
    except OSError as e:
        raise RuntimeError(f"could not run diagnose.py in {root}: {e}") from e
    if not proc.stdout.strip():
        raise RuntimeError(
            f"diagnose.py printed no stdout in {root} "
            f"(exit {proc.returncode}); stderr:\n{proc.stderr.strip()}"
        )
    return parse_diagnose_json(proc.stdout)


# --- the plan the human sees at the checkpoint ------------------------------

@dataclass(frozen=True)
class Plan:
    """One proposed step: the root cause and the single file we intend to edit."""

    finding: Finding
    target: str

    def render(self) -> str:
        f = self.finding
        return (
            f"PLAN [{f.guard or '?'} {f.code or '?'}] severity={f.severity or '?'}\n"
            f"  root cause: {f.cause or f.what or '(none stated)'}\n"
            f"  target file: {self.target}\n"
            f"  intended fix: {f.fix or '(none stated)'}"
        )


# checkpoint_fn defaults for tests and headless runs.

def auto_approve(plan: Plan) -> bool:
    """A checkpoint that always approves. For tests and full-autonomy runs."""
    return True


def auto_deny(plan: Plan) -> bool:
    """A checkpoint that always denies. For tests."""
    return False


# --- the loop result --------------------------------------------------------

# Plain-language summaries keyed by outcome, in the STATUS vocabulary.
_OUTCOME_SUMMARY: dict[str, str] = {
    "DONE": "DONE: the gate is green and every guard passes.",
    "NEEDS_CONTEXT": "NEEDS_CONTEXT: a required input is missing, stopping to ask a human.",
    "STOPPED_BY_HUMAN": "STOPPED: the human denied the proposed fix at the checkpoint.",
    "BLOCKED_BY_FENCE": "BLOCKED: the proposed target is a protected harness file, refusing to edit it.",
    "TAMPER": "TAMPER: a protected harness file changed, reverted and aborted.",
    "ESCALATED": "ESCALATED: no progress within the run budget, handing the findings to a human.",
}


@dataclass
class LoopResult:
    outcome: str
    iterations: int
    last_status: str
    message: str
    history: list[str] = field(default_factory=list)

    def render(self) -> str:
        headline = _OUTCOME_SUMMARY.get(self.outcome, f"{self.outcome}: {self.message}")
        lines = [
            headline,
            f"  iterations: {self.iterations}",
            f"  last diagnose STATUS: {self.last_status or '(none)'}",
        ]
        if self.message:
            lines.append(f"  detail: {self.message}")
        if self.history:
            lines.append("  history:")
            lines += [f"    {h}" for h in self.history]
        return "\n".join(lines)


# --- the state machine ------------------------------------------------------

def run_loop(
    root: str,
    *,
    diagnose_fn,
    fixer_fn,
    checkpoint_fn,
    budget: RunBudget | None = None,
    writable: tuple[str, ...] = DEFAULT_WRITABLE,
) -> LoopResult:
    """Run the checkpoint-mode fix loop until it reaches a terminal outcome.

    Terminal outcomes: DONE, NEEDS_CONTEXT, STOPPED_BY_HUMAN, BLOCKED_BY_FENCE,
    TAMPER, ESCALATED. The budget caps iterations and no-progress so the loop can
    never run forever, and the fence is checked before every edit and the tamper
    meta-gate after every edit.
    """
    if budget is None:
        budget = RunBudget()
    # Validate the writable allowlist once, up front, so a bad config fails loud
    # before any edit rather than mid-run.
    boundaries.validate_writable(writable)

    history: list[str] = []
    iterations = 0
    last_status = ""
    # A hard ceiling independent of the budget's own counters, so a misbehaving
    # injected diagnose_fn can never spin us forever.
    hard_cap = budget.max_iters + 1

    while True:
        iterations += 1
        if iterations > hard_cap:
            raise RuntimeError(
                f"fix loop exceeded its hard iteration ceiling ({hard_cap}); "
                f"the run budget failed to stop it, which is a bug"
            )

        diagnosis = diagnose_fn(root)
        last_status = diagnosis.status

        if diagnosis.status in DONE_STATUSES:
            history.append(f"iter {iterations}: {diagnosis.status}, stopping green")
            return LoopResult("DONE", iterations, last_status,
                              diagnosis.reason or "gate is green", history)

        if diagnosis.status == "NEEDS_CONTEXT":
            history.append(f"iter {iterations}: NEEDS_CONTEXT, stopping to ask")
            return LoopResult("NEEDS_CONTEXT", iterations, last_status,
                              diagnosis.reason or "a required input is missing", history)

        if diagnosis.status != "BLOCKED":
            # from_dict already rejects unknown statuses; this guards the one
            # remaining known status we have not handled explicitly.
            raise RuntimeError(f"fix loop cannot handle diagnose status {diagnosis.status!r}")

        actionable = diagnosis.actionable()
        if not actionable:
            history.append(f"iter {iterations}: BLOCKED but no ERROR/TEST finding to fix")
            return LoopResult(
                "ESCALATED", iterations, last_status,
                "BLOCKED with no actionable finding; only a human can resolve this",
                history,
            )

        finding = actionable[0]
        plan = Plan(finding=finding, target=finding.path)

        if not checkpoint_fn(plan):
            history.append(f"iter {iterations}: checkpoint denied {plan.target}")
            return LoopResult("STOPPED_BY_HUMAN", iterations, last_status,
                              f"human denied the fix for {plan.target}", history)

        # Fence check BEFORE any edit. A denied target is never handed to the fixer.
        decision = boundaries.classify(root, plan.target, writable=writable)
        if not decision.allow:
            history.append(f"iter {iterations}: fence blocked {plan.target}: {decision.reason}")
            return LoopResult("BLOCKED_BY_FENCE", iterations, last_status,
                              decision.reason, history)

        fixer_fn(finding, root)

        # Tamper meta-gate AFTER the edit. If the fixer touched a protected file,
        # revert it and abort.
        tamper = boundaries.changed_protected(root)
        if not tamper.clean:
            reverted = boundaries.revert_protected(root)
            history.append(f"iter {iterations}: TAMPER, reverted {reverted}")
            return LoopResult("TAMPER", iterations, last_status,
                              f"protected files changed and were reverted: {reverted}", history)

        open_findings = len(diagnosis.actionable())
        budget.record(open_findings)
        history.append(
            f"iter {iterations}: fixed via {plan.target}, {open_findings} open finding(s)"
        )
        stop, why = budget.should_stop()
        if stop:
            history.append(f"iter {iterations}: budget stop, {why}")
            return LoopResult("ESCALATED", iterations, last_status, why, history)
