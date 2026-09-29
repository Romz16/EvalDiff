from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from .adapters import import_object
from .models import Case, MetricResult, MetricStatus, Requirement, SystemOutput


class SemanticJudge(Protocol):
    async def score(self, *, requirement: Requirement, case: Case, output: SystemOutput) -> MetricResult: ...


class UnsupportedClaimsJudge(Protocol):
    async def evaluate_claims(self, *, case: Case, output: SystemOutput) -> MetricResult: ...


def _snippet(text: str, start: int, end: int, context: int = 70) -> str:
    left = max(0, start - context)
    right = min(len(text), end + context)
    return text[left:right].strip()


@dataclass(slots=True)
class RequirementEvaluator:
    judge: SemanticJudge | None = None

    async def evaluate(self, case: Case, output: SystemOutput) -> list[MetricResult]:
        results: list[MetricResult] = []
        for requirement in case.requirements:
            strategy = requirement.evaluator.lower().strip()
            if strategy == "semantic":
                results.append(await self._semantic(case, output, requirement))
            else:
                results.append(self._deterministic(output, requirement, strategy))
        return results

    async def _semantic(self, case: Case, output: SystemOutput, requirement: Requirement) -> MetricResult:
        if self.judge is None:
            return MetricResult(
                evaluator="requirement_coverage",
                name="requirement",
                requirement_id=requirement.id,
                status=MetricStatus.REVIEW,
                score=0.0,
                confidence=0.0,
                deterministic=False,
                details={"reason": "semantic evaluator requested but no judge was configured"},
            )
        result = self.judge.score(requirement=requirement, case=case, output=output)
        if inspect.isawaitable(result):
            result = await result
        result.requirement_id = requirement.id
        result.deterministic = False
        if result.score is not None and not 0 <= result.score <= 1:
            return MetricResult(
                evaluator="requirement_coverage",
                name="requirement",
                requirement_id=requirement.id,
                status=MetricStatus.ERROR,
                score=0.0,
                confidence=0.0,
                deterministic=False,
                details={"error": f"semantic judge returned score outside [0,1]: {result.score}"},
            )
        return result

    def _deterministic(self, output: SystemOutput, requirement: Requirement, strategy: str) -> MetricResult:
        text = output.text
        target = requirement.value
        score = 0.0
        evidence: list[str] = []
        details: dict[str, Any] = {}
        try:
            if strategy == "contains":
                if target is None:
                    raise ValueError("contains evaluator requires 'value'")
                match = re.search(re.escape(str(target)), text, re.IGNORECASE)
                if match:
                    score = 1.0
                    evidence = [_snippet(text, match.start(), match.end())]
            elif strategy in {"contains_all", "contains_any", "not_contains"}:
                targets = target if isinstance(target, list) else [target]
                if not targets or any(item is None for item in targets):
                    raise ValueError(f"{strategy} evaluator requires a value or non-empty value list")
                matches = [
                    (str(item), re.search(re.escape(str(item)), text, re.IGNORECASE))
                    for item in targets
                ]
                if strategy == "contains_all":
                    score = float(all(match is not None for _, match in matches))
                elif strategy == "contains_any":
                    score = float(any(match is not None for _, match in matches))
                else:
                    score = float(all(match is None for _, match in matches))
                evidence = [
                    _snippet(text, match.start(), match.end())
                    for _, match in matches
                    if match is not None
                ]
            elif strategy == "exact":
                score = float(text.strip() == str(target).strip())
                if score:
                    evidence = [text.strip()]
            elif strategy == "regex":
                if target is None:
                    raise ValueError("regex evaluator requires 'value'")
                match = re.search(str(target), text, re.IGNORECASE | re.MULTILINE)
                if match:
                    score = 1.0
                    evidence = [_snippet(text, match.start(), match.end())]
            elif strategy == "json_path":
                if not isinstance(target, dict) or "path" not in target:
                    raise ValueError("json_path evaluator requires value.path")
                value = _get_path(output.content, str(target["path"]))
                expected = target.get("equals", True)
                score = float(value == expected)
                evidence = [f"{target['path']}={value!r}"]
            else:
                raise ValueError(f"unknown requirement evaluator {strategy!r}")
        except Exception as exc:
            return MetricResult(
                evaluator="requirement_coverage",
                name="requirement",
                requirement_id=requirement.id,
                status=MetricStatus.ERROR,
                score=0.0,
                deterministic=True,
                details={"error": str(exc)},
            )

        return MetricResult(
            evaluator="requirement_coverage",
            name="requirement",
            requirement_id=requirement.id,
            status=MetricStatus.PASS if score >= requirement.threshold else MetricStatus.FAIL,
            score=score,
            evidence=evidence,
            confidence=1.0,
            deterministic=True,
            details=details,
        )


@dataclass(slots=True)
class ContractEvaluator:
    async def evaluate(self, case: Case, output: SystemOutput) -> list[MetricResult]:
        contract = case.contract
        if contract is None:
            return []
        errors: list[str] = []
        content = output.content

        if contract.json_schema is not None:
            errors.extend(_validate_schema(content, contract.json_schema, "$"))

        if contract.required_fields or contract.forbidden_fields:
            if not isinstance(content, dict):
                errors.append("output must be an object for field constraints")
            else:
                for name in contract.required_fields:
                    if name not in content:
                        errors.append(f"required field missing: {name}")
                for name in contract.forbidden_fields:
                    if name in content:
                        errors.append(f"forbidden field present: {name}")

        for pattern in contract.patterns:
            if re.search(pattern, output.text, re.MULTILINE) is None:
                errors.append(f"pattern did not match: {pattern}")

        if contract.max_length is not None and len(output.text) > contract.max_length:
            errors.append(f"output length {len(output.text)} exceeds {contract.max_length}")

        if contract.validator:
            try:
                validator = import_object(contract.validator)
                if hasattr(validator, "model_validate"):
                    validator.model_validate(content)
                else:
                    result = validator(content)
                    if result is False:
                        errors.append(f"custom validator {contract.validator} returned false")
            except Exception as exc:
                errors.append(f"custom validator {contract.validator} failed: {exc}")

        return [MetricResult(
            evaluator="contract",
            name="output_contract",
            status=MetricStatus.FAIL if errors else MetricStatus.PASS,
            score=0.0 if errors else 1.0,
            evidence=errors,
            deterministic=True,
            details={"errors": errors},
        )]


@dataclass(slots=True)
class UnsupportedClaimEvaluator:
    judge: UnsupportedClaimsJudge | None = None

    async def evaluate(self, case: Case, output: SystemOutput) -> list[MetricResult]:
        if self.judge is not None:
            result = self.judge.evaluate_claims(case=case, output=output)
            if inspect.isawaitable(result):
                result = await result
            result.evaluator = "unsupported_claims"
            result.name = "unsupported_claim_rate"
            result.deterministic = False
            if result.score is not None and not 0 <= result.score <= 1:
                return [MetricResult(
                    evaluator="unsupported_claims",
                    name="unsupported_claim_rate",
                    status=MetricStatus.ERROR,
                    score=None,
                    confidence=0.0,
                    deterministic=False,
                    details={"error": f"unsupported-claims judge returned rate outside [0,1]: {result.score}"},
                )]
            return [result]

        matches: list[str] = []
        for pattern in case.forbidden_content:
            if re.search(pattern, output.text, re.IGNORECASE | re.MULTILINE):
                matches.append(pattern)
        if case.forbidden_content:
            return [MetricResult(
                evaluator="forbidden_content",
                name="forbidden_content_matches",
                status=MetricStatus.FAIL if matches else MetricStatus.PASS,
                score=float(len(matches)),
                evidence=matches,
                confidence=1.0,
                deterministic=True,
                details={
                    "matched_patterns": matches,
                    "match_count": len(matches),
                    "configured_pattern_count": len(case.forbidden_content),
                },
            )]
        if case.references:
            return [MetricResult(
                evaluator="unsupported_claims",
                name="unsupported_claim_rate",
                status=MetricStatus.REVIEW,
                score=None,
                confidence=0.0,
                deterministic=False,
                details={"reason": "references were provided but no unsupported-claims judge was configured"},
            )]
        return []


def _get_path(value: Any, path: str) -> Any:
    current = value
    for part in path.removeprefix("$.").split("."):
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise KeyError(path)
    return current


def _validate_schema(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    schema_type = schema.get("type")
    type_checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if schema_type in type_checks and not type_checks[schema_type](value):
        return [f"{path}: expected {schema_type}, got {type(value).__name__}"]

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not in enum")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: required property missing: {key}")
        properties = schema.get("properties", {})
        for key, child_schema in properties.items():
            if key in value:
                errors.extend(_validate_schema(value[key], child_schema, f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            for key in value.keys() - properties.keys():
                errors.append(f"{path}: additional property not allowed: {key}")

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors.extend(_validate_schema(item, schema["items"], f"{path}[{index}]"))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            errors.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            errors.append(f"{path}: longer than maxLength")
        if "pattern" in schema and re.search(str(schema["pattern"]), value) is None:
            errors.append(f"{path}: does not match pattern")

    return errors
