from __future__ import annotations

from .models import CaseResult, DiffCategory, DiffItem, EvalRun, MetricResult


def _evidence(case: CaseResult, requirement_id: str) -> tuple[list[str], str | None, float | None]:
    for execution in case.executions:
        for metric in execution.metrics:
            if metric.requirement_id == requirement_id:
                return metric.evidence, metric.evaluator, metric.confidence
    return [], None, None


def compare_runs(previous: EvalRun, current: EvalRun, *, material_delta: float = 0.1) -> list[DiffItem]:
    items: list[DiffItem] = []
    previous_cases = {case.case_id: case for case in previous.cases}
    current_cases = {case.case_id: case for case in current.cases}

    for case_id in sorted(previous_cases.keys() | current_cases.keys()):
        old_case = previous_cases.get(case_id)
        new_case = current_cases.get(case_id)
        old_scores = old_case.requirement_scores if old_case else {}
        new_scores = new_case.requirement_scores if new_case else {}

        for requirement_id in sorted(old_scores.keys() | new_scores.keys()):
            old_score = old_scores.get(requirement_id, 0.0)
            new_score = new_scores.get(requirement_id, 0.0)
            old_pass = old_score >= 0.5
            new_pass = new_score >= 0.5
            if not old_pass and new_pass:
                category = DiffCategory.NEW
            elif old_pass and not new_pass:
                category = DiffCategory.LOST
            elif old_pass and new_pass and new_score - old_score >= material_delta:
                category = DiffCategory.IMPROVED
            elif old_pass and new_pass and old_score - new_score >= material_delta:
                category = DiffCategory.REGRESSED
            else:
                category = DiffCategory.RETAINED

            old_evidence, old_evaluator, old_confidence = _evidence(old_case, requirement_id) if old_case else ([], None, None)
            new_evidence, new_evaluator, new_confidence = _evidence(new_case, requirement_id) if new_case else ([], None, None)
            confidences = [value for value in (old_confidence, new_confidence) if value is not None]
            items.append(DiffItem(
                category=category,
                case_id=case_id,
                requirement_id=requirement_id,
                previous_score=old_score,
                current_score=new_score,
                previous_evidence=old_evidence,
                current_evidence=new_evidence,
                evaluator=new_evaluator or old_evaluator,
                confidence=min(confidences) if confidences else None,
            ))

        if old_case and new_case:
            old_format = old_case.contract_pass_rate >= 1.0
            new_format = new_case.contract_pass_rate >= 1.0
            if old_format != new_format:
                _, old_contract_evidence, old_contract_details = _metric_snapshot(old_case, "contract")
                _, new_contract_evidence, new_contract_details = _metric_snapshot(new_case, "contract")
                items.append(DiffItem(
                    category=DiffCategory.FORMAT_CHANGE,
                    case_id=case_id,
                    previous_score=old_case.contract_pass_rate,
                    current_score=new_case.contract_pass_rate,
                    previous_evidence=old_contract_evidence,
                    current_evidence=new_contract_evidence,
                    evaluator="contract",
                    details={"previous": old_contract_details, "current": new_contract_details},
                ))
            for evaluator in ("forbidden_content", "unsupported_claims"):
                old_score, old_evidence, old_details = _metric_snapshot(old_case, evaluator)
                new_score, new_evidence, new_details = _metric_snapshot(new_case, evaluator)
                if new_score is not None and (old_score is None or new_score > old_score):
                    items.append(DiffItem(
                        category=DiffCategory.UNSUPPORTED,
                        case_id=case_id,
                        previous_score=old_score,
                        current_score=new_score,
                        previous_evidence=old_evidence,
                        current_evidence=new_evidence,
                        evaluator=evaluator,
                        details={"previous": old_details, "current": new_details},
                    ))
    return items


def _metric_snapshot(case: CaseResult, evaluator: str) -> tuple[float | None, list[str], list[dict]]:
    metrics = [
        metric
        for execution in case.executions
        for metric in execution.metrics
        if metric.evaluator == evaluator
    ]
    values = [metric.score for metric in metrics if metric.score is not None]
    evidence = list(dict.fromkeys(item for metric in metrics for item in metric.evidence))
    details = [metric.details for metric in metrics if metric.details]
    return (sum(values) / len(values) if values else None, evidence, details)
