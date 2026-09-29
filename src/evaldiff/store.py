from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .models import (
    CaseResult,
    DiffCategory,
    DiffItem,
    EvalRun,
    ExecutionResult,
    MetricResult,
    MetricStatus,
    RunStatus,
    SystemOutput,
    SystemVersion,
)


class SQLiteRunStore:
    def __init__(self, path: str | Path = ".evaldiff/evaldiff.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    suite_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    coverage REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runs_project_suite_created
                    ON runs(project, suite_name, created_at DESC);
                CREATE TABLE IF NOT EXISTS baselines (
                    project TEXT NOT NULL,
                    suite_name TEXT NOT NULL,
                    name TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    PRIMARY KEY(project, suite_name, name)
                );
            """)

    def save(self, run: EvalRun) -> None:
        payload = json.dumps(run.to_dict(), ensure_ascii=False, sort_keys=True)
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT INTO runs(id, project, suite_name, status, coverage, created_at, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (run.id, run.project, run.suite_name, run.status.value, run.coverage, run.created_at, payload),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"run {run.id!r} already exists; run artifacts are immutable") from exc

    def load(self, run_id: str) -> EvalRun:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT payload_json FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"run not found: {run_id}")
        return run_from_dict(json.loads(row["payload_json"]))

    def set_baseline(self, run_id: str, name: str = "last-approved", *, allow_non_pass: bool = False) -> None:
        run = self.load(run_id)
        if run.status is not RunStatus.PASS and not allow_non_pass:
            raise ValueError(
                f"run {run.id!r} has status {run.status.value}; only PASS runs can become baselines "
                "unless allow_non_pass=True is explicitly set"
            )
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO baselines(project, suite_name, name, run_id) VALUES (?, ?, ?, ?)
                   ON CONFLICT(project, suite_name, name) DO UPDATE SET run_id = excluded.run_id""",
                (run.project, run.suite_name, name, run.id),
            )

    def load_baseline(self, project: str, suite_name: str, name: str = "last-approved") -> EvalRun:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT run_id FROM baselines WHERE project = ? AND suite_name = ? AND name = ?",
                (project, suite_name, name),
            ).fetchone()
        if row is None:
            raise KeyError(f"baseline not found: {project}/{suite_name}/{name}")
        return self.load(row["run_id"])

    def list_runs(self, project: str | None = None, suite_name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        query = "SELECT id, project, suite_name, status, coverage, created_at FROM runs"
        clauses: list[str] = []
        params: list[Any] = []
        if project:
            clauses.append("project = ?")
            params.append(project)
        if suite_name:
            clauses.append("suite_name = ?")
            params.append(suite_name)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]


def run_from_dict(data: dict[str, Any]) -> EvalRun:
    cases = []
    for case in data.get("cases", []):
        executions = []
        for execution in case.get("executions", []):
            output_data = execution.get("output")
            output = SystemOutput(**output_data) if output_data is not None else None
            metrics = [MetricResult(
                evaluator=metric["evaluator"],
                name=metric["name"],
                status=MetricStatus(metric["status"]),
                score=metric.get("score"),
                requirement_id=metric.get("requirement_id"),
                evidence=list(metric.get("evidence", [])),
                confidence=metric.get("confidence"),
                deterministic=bool(metric.get("deterministic", True)),
                details=dict(metric.get("details", {})),
            ) for metric in execution.get("metrics", [])]
            executions.append(ExecutionResult(
                repetition=int(execution["repetition"]),
                output=output,
                metrics=metrics,
                latency_ms=float(execution["latency_ms"]),
                system_latency_ms=float(execution.get("system_latency_ms", execution["latency_ms"])),
                evaluation_latency_ms=float(execution.get("evaluation_latency_ms", 0.0)),
                queue_latency_ms=float(execution.get("queue_latency_ms", 0.0)),
                error=execution.get("error"),
            ))
        cases.append(CaseResult(
            case_id=case["case_id"],
            executions=executions,
            requirement_scores={key: float(value) for key, value in case.get("requirement_scores", {}).items()},
            coverage=float(case["coverage"]),
            contract_pass_rate=float(case["contract_pass_rate"]),
            pass_rate=float(case["pass_rate"]),
            latency_p50_ms=float(case["latency_p50_ms"]),
            latency_p95_ms=float(case["latency_p95_ms"]),
            system_latency_p50_ms=float(case.get("system_latency_p50_ms", case["latency_p50_ms"])),
            system_latency_p95_ms=float(case.get("system_latency_p95_ms", case["latency_p95_ms"])),
            evaluation_latency_p50_ms=float(case.get("evaluation_latency_p50_ms", 0.0)),
            evaluation_latency_p95_ms=float(case.get("evaluation_latency_p95_ms", 0.0)),
            cost_mean=case.get("cost_mean"),
        ))
    diffs = [DiffItem(
        category=DiffCategory(item["category"]),
        case_id=item["case_id"],
        requirement_id=item.get("requirement_id"),
        previous_score=item.get("previous_score"),
        current_score=item.get("current_score"),
        previous_evidence=list(item.get("previous_evidence", [])),
        current_evidence=list(item.get("current_evidence", [])),
        evaluator=item.get("evaluator"),
        confidence=item.get("confidence"),
        details=dict(item.get("details", {})),
    ) for item in data.get("diffs", [])]
    return EvalRun(
        id=data["id"],
        project=data["project"],
        suite_name=data["suite_name"],
        system_version=SystemVersion(**data["system_version"]),
        cases=cases,
        coverage=float(data["coverage"]),
        status=RunStatus(data["status"]),
        decision_reasons=list(data.get("decision_reasons", [])),
        diffs=diffs,
        baseline_run_id=data.get("baseline_run_id"),
        created_at=data["created_at"],
        metadata=dict(data.get("metadata", {})),
    )
