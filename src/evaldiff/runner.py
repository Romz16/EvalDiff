from __future__ import annotations

import asyncio
import dataclasses
import math
import statistics
import time
import uuid
from collections import defaultdict
from typing import Iterable

from .adapters import SystemAdapter
from .diffing import compare_runs
from .evaluators import ContractEvaluator, RequirementEvaluator, SemanticJudge, UnsupportedClaimEvaluator, UnsupportedClaimsJudge
from .models import (
    Case,
    CaseResult,
    EvalRun,
    ExecutionResult,
    MetricResult,
    MetricStatus,
    Suite,
    SystemVersion,
    fingerprint,
)
from .policies import apply_policy


def _run_id() -> str:
    return f"run_{int(time.time() * 1000):x}_{uuid.uuid4().hex[:10]}"


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


async def evaluate_async(
    suite: Suite,
    system: SystemAdapter,
    *,
    baseline: EvalRun | None = None,
    system_version: SystemVersion | None = None,
    judge: SemanticJudge | None = None,
    unsupported_claims_judge: UnsupportedClaimsJudge | None = None,
) -> EvalRun:
    semaphore = asyncio.Semaphore(suite.concurrency)
    requirement_evaluator = RequirementEvaluator(judge=judge)
    contract_evaluator = ContractEvaluator()
    unsupported_evaluator = UnsupportedClaimEvaluator(judge=unsupported_claims_judge)

    async def execute(case: Case, repetition: int) -> ExecutionResult:
        total_started = time.perf_counter()
        output = None
        metrics: list[MetricResult] = []
        error = None
        queue_latency_ms = 0.0
        system_latency_ms = 0.0
        evaluation_latency_ms = 0.0
        try:
            queue_started = time.perf_counter()
            async with semaphore:
                queue_latency_ms = (time.perf_counter() - queue_started) * 1000
                system_started = time.perf_counter()
                try:
                    output = await system.invoke(case)
                finally:
                    system_latency_ms = (time.perf_counter() - system_started) * 1000
            evaluation_started = time.perf_counter()
            try:
                metrics.extend(await contract_evaluator.evaluate(case, output))
                metrics.extend(await requirement_evaluator.evaluate(case, output))
                metrics.extend(await unsupported_evaluator.evaluate(case, output))
            finally:
                evaluation_latency_ms = (time.perf_counter() - evaluation_started) * 1000
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            metrics.append(MetricResult(
                evaluator="execution",
                name="system_invoke",
                status=MetricStatus.ERROR,
                score=0.0,
                details={"error": error},
            ))
        latency_ms = (time.perf_counter() - total_started) * 1000
        return ExecutionResult(
            repetition=repetition,
            output=output,
            metrics=metrics,
            latency_ms=latency_ms,
            system_latency_ms=system_latency_ms,
            evaluation_latency_ms=evaluation_latency_ms,
            queue_latency_ms=queue_latency_ms,
            error=error,
        )

    tasks = [execute(case, repetition) for case in suite.cases for repetition in range(1, suite.repetitions + 1)]
    execution_results = await asyncio.gather(*tasks)
    grouped: dict[str, list[ExecutionResult]] = defaultdict(list)
    for execution, case in zip(execution_results, [case for case in suite.cases for _ in range(suite.repetitions)]):
        grouped[case.id].append(execution)

    case_results = [_aggregate_case(case, grouped[case.id]) for case in suite.cases]
    total_weight = sum(requirement.weight for case in suite.cases for requirement in case.requirements)
    weighted_score = sum(
        requirement.weight * result.requirement_scores.get(requirement.id, 0.0)
        for case, result in zip(suite.cases, case_results)
        for requirement in case.requirements
    )
    coverage = weighted_score / total_weight if total_weight else 1.0
    version = system_version or SystemVersion.from_config(suite.system)
    run = EvalRun(
        id=_run_id(),
        project=suite.project,
        suite_name=suite.name,
        system_version=version,
        cases=case_results,
        coverage=coverage,
        baseline_run_id=baseline.id if baseline else None,
        metadata={
            "suite_fingerprint": fingerprint(_suite_fingerprint_payload(suite)),
            "repetitions": suite.repetitions,
            "quality_summary": _quality_summary(suite, case_results),
        },
    )
    if baseline is not None:
        run.diffs = compare_runs(baseline, run)
        run.metadata["coverage_delta"] = run.coverage - baseline.coverage
    decision = apply_policy(run, suite, baseline)
    run.status = decision.status
    run.decision_reasons = decision.reasons
    return run


def evaluate(
    suite: Suite,
    system: SystemAdapter,
    *,
    baseline: EvalRun | None = None,
    system_version: SystemVersion | None = None,
    judge: SemanticJudge | None = None,
    unsupported_claims_judge: UnsupportedClaimsJudge | None = None,
) -> EvalRun:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(evaluate_async(
            suite,
            system,
            baseline=baseline,
            system_version=system_version,
            judge=judge,
            unsupported_claims_judge=unsupported_claims_judge,
        ))
    raise RuntimeError("evaluate() cannot run inside an event loop; use await evaluate_async()")


def _aggregate_case(case: Case, executions: list[ExecutionResult]) -> CaseResult:
    scores: dict[str, list[float]] = defaultdict(list)
    contract_scores: list[float] = []
    costs: list[float] = []
    successful = 0
    for execution in executions:
        if execution.output is not None and execution.output.cost is not None:
            costs.append(execution.output.cost)
        if execution.error is None:
            successful += 1
        for metric in execution.metrics:
            if metric.requirement_id and metric.score is not None:
                scores[metric.requirement_id].append(metric.score)
            if metric.evaluator == "contract" and metric.score is not None:
                contract_scores.append(metric.score)

    requirement_scores = {
        requirement.id: statistics.fmean(scores.get(requirement.id, [0.0]))
        for requirement in case.requirements
    }
    weight_total = sum(requirement.weight for requirement in case.requirements)
    coverage = (
        sum(requirement.weight * requirement_scores[requirement.id] for requirement in case.requirements) / weight_total
        if weight_total else 1.0
    )
    contract_pass_rate = statistics.fmean(contract_scores) if contract_scores else 1.0
    passed_repetitions = 0
    for execution in executions:
        requirement_pass = all(
            next((metric.score for metric in execution.metrics if metric.requirement_id == requirement.id), 0.0) >= requirement.threshold
            for requirement in case.requirements
        )
        contract_pass = all(metric.status is MetricStatus.PASS for metric in execution.metrics if metric.evaluator == "contract")
        if execution.error is None and requirement_pass and contract_pass:
            passed_repetitions += 1

    latencies = [execution.latency_ms for execution in executions]
    system_latencies = [execution.system_latency_ms for execution in executions]
    evaluation_latencies = [execution.evaluation_latency_ms for execution in executions]
    return CaseResult(
        case_id=case.id,
        executions=executions,
        requirement_scores=requirement_scores,
        coverage=coverage,
        contract_pass_rate=contract_pass_rate,
        pass_rate=passed_repetitions / len(executions) if executions else 0.0,
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
        system_latency_p50_ms=_percentile(system_latencies, 0.50),
        system_latency_p95_ms=_percentile(system_latencies, 0.95),
        evaluation_latency_p50_ms=_percentile(evaluation_latencies, 0.50),
        evaluation_latency_p95_ms=_percentile(evaluation_latencies, 0.95),
        cost_mean=statistics.fmean(costs) if costs else None,
    )


def _suite_fingerprint_payload(suite: Suite) -> dict:
    """Return the complete, stable evaluation definition used by a run."""
    return {
        "project": suite.project,
        "name": suite.name,
        "cases": [dataclasses.asdict(case) for case in suite.cases],
        "repetitions": suite.repetitions,
        "concurrency": suite.concurrency,
        "policy": dataclasses.asdict(suite.policy),
        "metadata": suite.metadata,
    }


def _quality_summary(suite: Suite, case_results: list[CaseResult]) -> dict[str, int]:
    result_by_id = {result.case_id: result for result in case_results}
    counts = {
        "types_correct": 0,
        "types_total": 0,
        "facts_correct": 0,
        "facts_total": 0,
        "hallucinations": 0,
    }
    hallucination_evidence: set[str] = set()
    for case in suite.cases:
        result = result_by_id[case.id]
        for requirement in case.requirements:
            dimension = requirement.dimension.strip().lower()
            score = result.requirement_scores.get(requirement.id, 0.0)
            if dimension == "type":
                counts["types_total"] += 1
                counts["types_correct"] += int(score >= requirement.threshold)
            elif dimension == "fact":
                counts["facts_total"] += 1
                counts["facts_correct"] += int(score >= requirement.threshold)
        for execution in result.executions:
            for metric in execution.metrics:
                if metric.evaluator == "forbidden_content":
                    hallucination_evidence.update(metric.evidence)
                elif metric.evaluator == "unsupported_claims":
                    claims = metric.details.get("unsupported_claims", metric.evidence)
                    hallucination_evidence.update(str(item) for item in claims)
    counts["hallucinations"] = len(hallucination_evidence)
    return counts
