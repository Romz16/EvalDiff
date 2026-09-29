from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Sequence

from .adapters import adapter_from_config
from .diffing import compare_runs
from .loader import load_suite
from .models import RunStatus
from .plugins import PLUGIN_GROUPS, discover_plugins
from .policies import apply_policy
from .reporters import HTMLReporter, JSONReporter, TerminalReporter
from .runner import evaluate
from .store import SQLiteRunStore


EXIT_CODES = {RunStatus.PASS: 0, RunStatus.WARN: 10, RunStatus.REVIEW: 10, RunStatus.FAIL: 20, RunStatus.ERROR: 40}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evaldiff", description="Behavioral regression testing for AI systems")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="create a starter EvalDiff project")
    init_parser.add_argument("directory", nargs="?", default=".")

    run_parser = sub.add_parser("run", help="run an evaluation suite")
    run_parser.add_argument("suite")
    run_parser.add_argument("--store", default=".evaldiff/evaldiff.db")
    run_parser.add_argument("--baseline", help="baseline name or explicit run id")
    run_parser.add_argument("--no-baseline", action="store_true")
    run_parser.add_argument("--reports-dir", default=".evaldiff/reports")
    run_parser.add_argument("--ci", action="store_true", help="emit CI exit codes")

    compare_parser = sub.add_parser("compare", help="compare two stored runs")
    compare_parser.add_argument("previous")
    compare_parser.add_argument("current")
    compare_parser.add_argument("--store", default=".evaldiff/evaldiff.db")

    baseline_parser = sub.add_parser("baseline", help="manage approved baselines")
    baseline_sub = baseline_parser.add_subparsers(dest="baseline_command", required=True)
    baseline_set = baseline_sub.add_parser("set", help="point a baseline name to an immutable run")
    baseline_set.add_argument("run_id")
    baseline_set.add_argument("--name", default="last-approved")
    baseline_set.add_argument("--store", default=".evaldiff/evaldiff.db")
    baseline_set.add_argument(
        "--allow-non-pass",
        action="store_true",
        help="explicitly allow a WARN/FAIL/REVIEW run to become a baseline",
    )

    report_parser = sub.add_parser("report", help="render a stored run")
    report_parser.add_argument("run_id")
    report_parser.add_argument("--format", choices=("terminal", "json", "html"), default="html")
    report_parser.add_argument("--output")
    report_parser.add_argument("--store", default=".evaldiff/evaldiff.db")

    doctor_parser = sub.add_parser("doctor", help="validate the local EvalDiff environment")
    doctor_parser.add_argument("suite", nargs="?")
    doctor_parser.add_argument("--store", default=".evaldiff/evaldiff.db")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            return _init(Path(args.directory))
        if args.command == "run":
            return _run(args)
        if args.command == "compare":
            return _compare(args)
        if args.command == "baseline":
            return _baseline(args)
        if args.command == "report":
            return _report(args)
        if args.command == "doctor":
            return _doctor(args)
    except (ValueError, KeyError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"EvalDiff configuration error: {exc}", file=sys.stderr)
        return 30
    except Exception as exc:
        print(f"EvalDiff execution error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 40
    return 30


def _init(root: Path) -> int:
    evals = root / "evals"
    evals.mkdir(parents=True, exist_ok=True)
    suite_path = evals / "example.json"
    system_path = evals / "example_system.py"
    gitignore_path = root / ".gitignore"
    if suite_path.exists() or system_path.exists():
        raise ValueError("starter files already exist; refusing to overwrite them")
    suite_path.write_text(json.dumps(_starter_suite(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    system_path.write_text(
        "def summarize(payload):\n"
        "    text = payload.get('text', '')\n"
        "    return {'summary': f'The notice gives a 30-day deadline. {text}'.strip(), 'deadline_days': 30}\n",
        encoding="utf-8",
    )
    if not gitignore_path.exists():
        gitignore_path.write_text(".evaldiff/\n__pycache__/\n*.py[cod]\n.venv/\ndist/\nbuild/\n", encoding="utf-8")
    print(f"Created {suite_path} and {system_path}")
    print(f"Run: evaldiff run {suite_path}")
    return 0


def _run(args: argparse.Namespace) -> int:
    suite_path = Path(args.suite).resolve()
    sys.path.insert(0, str(suite_path.parent))
    suite = load_suite(suite_path)
    store = SQLiteRunStore(args.store)
    adapter = adapter_from_config(suite.system)
    baseline = None
    selector = None if args.no_baseline else (args.baseline or suite.baseline)
    if selector:
        if selector.startswith("run_"):
            baseline = store.load(selector)
        else:
            baseline = store.load_baseline(suite.project, suite.name, selector)
    run = evaluate(suite, adapter, baseline=baseline)
    store.save(run)
    reports_dir = Path(args.reports_dir)
    JSONReporter().render(run, reports_dir / f"{run.id}.json")
    HTMLReporter().render(run, reports_dir / f"{run.id}.html")
    TerminalReporter().render(run)
    if any(execution.error for case in run.cases for execution in case.executions):
        return 40
    return EXIT_CODES[run.status] if args.ci else 0


def _compare(args: argparse.Namespace) -> int:
    store = SQLiteRunStore(args.store)
    previous = store.load(args.previous)
    current = store.load(args.current)
    current.diffs = compare_runs(previous, current)
    current.baseline_run_id = previous.id
    current.metadata["coverage_delta"] = current.coverage - previous.coverage
    TerminalReporter().render(current)
    return 0


def _baseline(args: argparse.Namespace) -> int:
    store = SQLiteRunStore(args.store)
    store.set_baseline(args.run_id, args.name, allow_non_pass=args.allow_non_pass)
    print(f"Baseline {args.name!r} now points to {args.run_id}")
    return 0


def _report(args: argparse.Namespace) -> int:
    run = SQLiteRunStore(args.store).load(args.run_id)
    if args.format == "terminal":
        TerminalReporter().render(run)
        return 0
    output = Path(args.output or f".evaldiff/reports/{run.id}.{args.format}")
    reporter = JSONReporter() if args.format == "json" else HTMLReporter()
    reporter.render(run, output)
    print(output)
    return 0


def _doctor(args: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []
    checks.append(("Python", sys.version_info >= (3, 11), sys.version.split()[0]))
    checks.append(("SQLite", True, str(Path(args.store))))
    checks.append(("Git", shutil.which("git") is not None, shutil.which("git") or "not found"))
    for kind in PLUGIN_GROUPS:
        plugins = discover_plugins(kind)
        failed = [name for name, value in plugins.items() if isinstance(value, Exception)]
        checks.append((f"Plugins {kind}", not failed, f"{len(plugins)} found; failed={failed}"))
    if args.suite:
        suite_path = Path(args.suite).resolve()
        sys.path.insert(0, str(suite_path.parent))
        suite = load_suite(suite_path)
        adapter_from_config(suite.system)
        checks.append(("Suite", True, f"{suite.project}/{suite.name}, {len(suite.cases)} cases"))
    for name, ok, detail in checks:
        print(f"{'OK' if ok else 'FAIL':<4} {name}: {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 30


def _starter_suite() -> dict:
    return {
        "project": "example-ai-system",
        "suite": "smoke-regression",
        "repetitions": 1,
        "system": {"adapter": "callable", "target": "example_system:summarize", "name": "v1"},
        "policy": {
            "fail_on_critical_loss": True,
            "fail_on_unmet_critical": True,
            "min_critical_pass_rate": 1.0,
            "max_coverage_drop": 0.0,
            "max_format_failures": 0,
            "max_forbidden_content_matches": 0,
        },
        "cases": [{
            "id": "notice-001",
            "input": {"text": "A corrected filing is required."},
            "requirements": [
                {"id": "deadline", "description": "Mention the 30-day deadline", "weight": 3, "critical": True, "evaluator": "contains", "value": "30-day"},
                {"id": "corrective_action", "description": "Explain corrective filing", "weight": 2, "evaluator": "contains", "value": "corrected filing"},
            ],
            "contract": {"schema": {"type": "object", "required": ["summary", "deadline_days"], "properties": {"summary": {"type": "string"}, "deadline_days": {"type": "integer"}}}},
        }],
    }
