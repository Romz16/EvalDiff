from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Case, OutputContract, Policy, Requirement, Suite


def load_data(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    suffix = source.suffix.lower()
    if suffix == ".json":
        value = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("YAML support requires PyYAML; install evaldiff with its dependencies") from exc
        value = yaml.safe_load(text)
    else:
        raise ValueError(f"unsupported suite format {suffix!r}; use .json, .yaml or .yml")
    if not isinstance(value, dict):
        raise ValueError("suite document must contain an object at the top level")
    return value


def load_suite(path: str | Path) -> Suite:
    data = load_data(path)
    ideal_defaults = data.get("ideal", {})
    cases = [_case_from_dict(item, ideal_defaults) for item in data.get("cases", [])]
    if not cases:
        raise ValueError("suite must contain at least one case")
    baseline = data.get("baseline")
    if isinstance(baseline, dict):
        baseline = baseline.get("selector")
    return Suite(
        name=str(data.get("suite") or data.get("name") or Path(path).stem),
        project=str(data.get("project", "evaldiff")),
        cases=cases,
        repetitions=int(data.get("repetitions", 1)),
        concurrency=int(data.get("concurrency", 4)),
        system=dict(data.get("system", {})),
        baseline=baseline,
        policy=Policy(**_select_fields(data.get("policy", {}), Policy)),
        metadata=dict(data.get("metadata", {})),
    )


def _case_from_dict(data: dict[str, Any], defaults: dict[str, Any]) -> Case:
    if not isinstance(data, dict):
        raise ValueError("every case must be an object")
    ideal = {**defaults, **dict(data.get("ideal", {}))}
    requirement_data = data.get("requirements", ideal.get("requirements", []))
    requirements = [Requirement(**_select_fields(item, Requirement)) for item in requirement_data]
    contract_data = data.get("contract", data.get("output_contract", ideal.get("contract")))
    contract = None
    if contract_data:
        normalized = dict(contract_data)
        if "schema" in normalized and "json_schema" not in normalized:
            normalized["json_schema"] = normalized.pop("schema")
        contract = OutputContract(**_select_fields(normalized, OutputContract))
    return Case(
        id=str(data["id"]),
        input=data.get("input"),
        requirements=requirements,
        ideal_answer=data.get("ideal_answer", ideal.get("answer")),
        references=list(data.get("references", ideal.get("references", []))),
        forbidden_content=list(data.get("forbidden_content", ideal.get("forbidden_content", []))),
        contract=contract,
        tags=list(data.get("tags", [])),
        metadata=dict(data.get("metadata", {})),
    )


def _select_fields(data: dict[str, Any], model: type) -> dict[str, Any]:
    fields = set(model.__dataclass_fields__)
    return {key: value for key, value in dict(data).items() if key in fields}
