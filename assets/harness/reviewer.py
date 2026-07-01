"""
G9 - Automated code reviewer (a small CodeRabbit-style gate).

Runs BEFORE any code is allowed into the pipeline and AFTER outputs are produced.
Static-analyses Python for the exact ways ML results get faked:

  ERROR (blocks the gate):
    - hardcoded metric literals            e.g.  accuracy = 0.87
    - silent except that swallows/fabricates  e.g.  except: pass  /  except: return np.zeros(...)
    - data loaders that return synthetic   e.g.  def load_*(): return np.random.randn(...)
  WARN (advisory):
    - TODO/FIXME/HACK/XXX in code
    - unseeded default_rng()

Usage:
    python reviewer.py <path> [<path> ...]        # code review, exits 1 on ERROR
    python reviewer.py --outputs results/         # post-output audit of result files
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import dataclass

METRIC_NAMES = {
    "acc", "accuracy", "score", "result", "results", "metric", "metrics",
    "kappa", "f1", "corr", "correlation", "sndr", "cr", "auc", "recall",
    "precision", "val_acc", "test_acc", "adaptive_acc", "baseline_acc",
}
SYNTHETIC_CALLS = {
    "np.random.rand", "np.random.randn", "np.random.random", "np.random.randint",
    "np.random.normal", "np.random.uniform", "numpy.random.randn",
    "np.zeros", "np.ones", "np.full", "np.empty", "numpy.zeros",
}
LOADER_HINTS = ("load", "read", "fetch", "get_data", "get_dataset")
TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")


@dataclass
class Finding:
    severity: str      # ERROR | WARN
    path: str
    line: int
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.path}:{self.line} ({self.code}) {self.message}"


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _returns_synthetic(fn: ast.FunctionDef) -> list[int]:
    hits = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Call):
            if _dotted(n.value.func) in SYNTHETIC_CALLS:
                hits.append(n.lineno)
    return hits


def _handler_reraises(h: ast.ExceptHandler) -> bool:
    return any(isinstance(n, ast.Raise) for n in ast.walk(h))


def review_source(path: str) -> list[Finding]:
    src = open(path, encoding="utf-8").read()
    findings: list[Finding] = []
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        return [Finding("ERROR", path, e.lineno or 0, "SYNTAX", f"cannot parse: {e.msg}")]

    for node in ast.walk(tree):
        # Hardcoded metric literals.
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id.lower() in METRIC_NAMES:
                    v = node.value
                    if isinstance(v, ast.Constant) and isinstance(v.value, (int, float)) \
                            and not isinstance(v.value, bool) and v.value not in (0, 1):
                        findings.append(Finding(
                            "ERROR", path, node.lineno, "HARDCODED_METRIC",
                            f"'{tgt.id}' assigned literal {v.value!r} - a metric must be "
                            f"computed from data, never hardcoded.",
                        ))

        # Silent / fabricating except handlers.
        if isinstance(node, ast.ExceptHandler) and not _handler_reraises(node):
            body = node.body
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                findings.append(Finding(
                    "ERROR", path, node.lineno, "SILENT_EXCEPT",
                    "except swallows the error with `pass` and does not re-raise.",
                ))
            for b in body:
                if isinstance(b, ast.Return) and isinstance(b.value, ast.Call) \
                        and _dotted(b.value.func) in SYNTHETIC_CALLS:
                    findings.append(Finding(
                        "ERROR", path, b.lineno, "FABRICATED_FALLBACK",
                        "except returns synthetic data instead of re-raising - "
                        "this is exactly how a fake result slips in.",
                    ))

        # Data loaders that return synthetic data.
        if isinstance(node, ast.FunctionDef) and any(h in node.name.lower() for h in LOADER_HINTS):
            for ln in _returns_synthetic(node):
                findings.append(Finding(
                    "ERROR", path, ln, "SYNTHETIC_LOADER",
                    f"loader '{node.name}' returns synthetic data - a loader must "
                    f"return REAL data or raise.",
                ))

        # Unseeded default_rng() -> advisory.
        if isinstance(node, ast.Call) and _dotted(node.func) in ("np.random.default_rng", "numpy.random.default_rng") \
                and not node.args and not node.keywords:
            findings.append(Finding(
                "WARN", path, node.lineno, "UNSEEDED_RNG",
                "default_rng() with no seed - results won't be reproducible.",
            ))

    for i, line in enumerate(src.splitlines(), start=1):
        if TODO_RE.search(line):
            findings.append(Finding("WARN", path, i, "TODO", line.strip()[:80]))
    return findings


def review_paths(paths: list[str], exclude: tuple[str, ...] = ()) -> list[Finding]:
    files: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                if any(x in root for x in (".venv", "__pycache__", ".git")):
                    continue
                files += [os.path.join(root, n) for n in names if n.endswith(".py")]
        elif p.endswith(".py"):
            files.append(p)
    findings: list[Finding] = []
    for f in files:
        if any(x in f for x in exclude):
            continue
        findings += review_source(f)
    return findings


def audit_outputs(results_dir: str) -> list[Finding]:
    """Post-output gate: every *.result.json must carry a grounded manifest and
    point to a real predictions file on disk."""
    findings: list[Finding] = []
    if not os.path.isdir(results_dir):
        return [Finding("ERROR", results_dir, 0, "NO_RESULTS_DIR", "results dir missing")]
    result_files = [f for f in os.listdir(results_dir) if f.endswith(".result.json")]
    if not result_files:
        findings.append(Finding("WARN", results_dir, 0, "NO_RESULTS", "no result files yet"))
    for rf in result_files:
        full = os.path.join(results_dir, rf)
        try:
            obj = json.load(open(full))
        except Exception as e:  # noqa: BLE001 - reviewer reports, never fabricates
            findings.append(Finding("ERROR", full, 0, "BAD_RESULT_JSON", repr(e)))
            continue
        man = obj.get("manifest", {})
        if len(str(man.get("data_sha256", ""))) != 64 or int(man.get("n_samples", 0)) <= 0:
            findings.append(Finding("ERROR", full, 0, "UNGROUNDED_RESULT",
                                    "result has no grounded data manifest"))
        pp = obj.get("predictions_path", "")
        if not (isinstance(pp, str) and os.path.exists(pp)):
            findings.append(Finding("ERROR", full, 0, "MISSING_PREDICTIONS",
                                    f"predictions file not found: {pp!r}"))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Anti-faking code + output reviewer (G9)")
    ap.add_argument("paths", nargs="*", default=["guards", "provenance"])
    ap.add_argument("--outputs", help="audit result files in this dir")
    ap.add_argument("--exclude", nargs="*", default=("tests", "fixtures", "reviewer.py"))
    args = ap.parse_args(argv)

    findings: list[Finding] = []
    if args.outputs:
        findings += audit_outputs(args.outputs)
    if args.paths and not args.outputs:
        findings += review_paths(args.paths, exclude=tuple(args.exclude))

    errors = [f for f in findings if f.severity == "ERROR"]
    for f in findings:
        print(f)
    print(f"\n{len(errors)} error(s), {len(findings) - len(errors)} warning(s).")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
