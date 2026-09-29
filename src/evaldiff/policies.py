from __future__ import annotations

from .models import DiffCategory, EvalRun, Policy, PolicyDecision, RunStatus, Suite


def apply_policy(run: EvalRun, suite: Suite, previous: EvalRun | None = None) -> PolicyDecision:
    policy = suite.policy
    failures: list[str] = []
    warnings: list[str] = []

    case_by_id = {case.id: case for case in suite.cases}
    result_by_id = {case.case_id: case for case in run.cases}

    if policy.fail_on_unmet_critical:
        for case in suite.cases:
            result = result_by_id[case.id]
            for requirement in (item for item in case.requirements if item.critical):
                passed = 0
                for execution in result.executions:
                    metric = next(
                        (item for item in execution.metrics if item.requirement_id == requirement.id),
                        None,
                    )
                    if metric is not None and metric.score is not None and metric.score >= requirement.threshold:
                        passed += 1
                pass_rate = passed / len(result.executions) if result.executions else 0.0
                if pass_rate < policy.min_critical_pass_rate:
                    failures.append(
                        f"critical requirement unmet: {case.id}/{requirement.id} "
                        f"pass rate {pass_rate:.1%} is below {policy.min_critical_pass_rate:.1%}"
                    )
    if policy.fail_on_critical_loss:
        for item in run.diffs:
            if item.category is not DiffCategory.LOST or not item.requirement_id:
                continue
            case = case_by_id.get(item.case_id)
            requirement = next((req for req in case.requirements if req.id == item.requirement_id), None) if case else None
            if requirement and requirement.critical:
                failures.append(f"critical requirement lost: {item.case_id}/{item.requirement_id}")

    if previous is not None:
        drop = previous.coverage - run.coverage
        if drop > policy.max_coverage_drop:
            failures.append(f"coverage dropped {drop:.1%}, above allowed {policy.max_coverage_drop:.1%}")

    format_failures = sum(case.contract_pass_rate < 1.0 for case in run.cases)
    if format_failures > policy.max_format_failures:
        failures.append(f"format failures {format_failures} exceed allowed {policy.max_format_failures}")

    forbidden_counts = [
        int(metric.score)
        for case in run.cases
        for execution in case.executions
        for metric in execution.metrics
        if metric.evaluator == "forbidden_content" and metric.score is not None
    ]
    forbidden_max = max(forbidden_counts, default=0)
    run.metadata["forbidden_content_matches_max"] = forbidden_max
    run.metadata["forbidden_content_matches_total"] = sum(forbidden_counts)
    if forbidden_max > policy.max_forbidden_content_matches:
        failures.append(
            f"forbidden content matches {forbidden_max} exceed allowed {policy.max_forbidden_content_matches}"
        )

    if policy.max_unsupported_claim_rate is not None:
        unsupported = [
            metric.score
            for case in run.cases
            for execution in case.executions
            for metric in execution.metrics
            if metric.evaluator == "unsupported_claims"
            and metric.name == "unsupported_claim_rate"
            and metric.score is not None
        ]
        rate = sum(unsupported) / len(unsupported) if unsupported else 0.0
        run.metadata["unsupported_claim_rate"] = rate
        if rate > policy.max_unsupported_claim_rate:
            failures.append(
                f"unsupported claim rate {rate:.1%} exceeds allowed {policy.max_unsupported_claim_rate:.1%}"
            )

    execution_error_count = sum(bool(execution.error) for case in run.cases for execution in case.executions)
    metric_error_count = sum(
        metric.status.value == "ERROR"
        for case in run.cases
        for execution in case.executions
        for metric in execution.metrics
    )
    error_count = execution_error_count + metric_error_count
    if error_count > policy.max_error_count:
        failures.append(f"execution/evaluator errors {error_count} exceed allowed {policy.max_error_count}")

    if policy.min_coverage is not None and run.coverage < policy.min_coverage:
        failures.append(f"coverage {run.coverage:.1%} is below minimum {policy.min_coverage:.1%}")

    if policy.min_case_pass_rate is not None:
        for case in run.cases:
            if case.pass_rate < policy.min_case_pass_rate:
                failures.append(
                    f"case pass rate {case.case_id}={case.pass_rate:.1%} is below "
                    f"{policy.min_case_pass_rate:.1%}"
                )

    if policy.fail_on_any_requirement_failure:
        for case in suite.cases:
            result = result_by_id[case.id]
            for requirement in case.requirements:
                score = result.requirement_scores.get(requirement.id, 0.0)
                if score < requirement.threshold:
                    failures.append(f"requirement unmet: {case.id}/{requirement.id} ({score:.1%})")

    if policy.max_p95_latency_ms is not None:
        slow = [case for case in run.cases if case.system_latency_p95_ms > policy.max_p95_latency_ms]
        if slow:
            failures.append(
                f"{len(slow)} case(s) exceed system p95 latency limit {policy.max_p95_latency_ms:g} ms"
            )

    review_count = sum(
        metric.status.value == "REVIEW"
        for case in run.cases
        for execution in case.executions
        for metric in execution.metrics
    )
    if review_count and policy.warn_on_review:
        warnings.append(f"{review_count} evaluator result(s) require review")

    if policy.warn_on_partial_case_pass and policy.min_case_pass_rate is None:
        partial_cases = [case.case_id for case in run.cases if case.pass_rate < 1.0]
        if partial_cases:
            warnings.append(
                "strict case pass rate is below 100% for: " + ", ".join(partial_cases)
            )

    if failures:
        return PolicyDecision(RunStatus.FAIL, failures + warnings)
    if warnings:
        return PolicyDecision(RunStatus.WARN, warnings)
    return PolicyDecision(RunStatus.PASS, [])
