from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class RunStatus(str, enum.Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    REVIEW = "REVIEW"
    ERROR = "ERROR"


class MetricStatus(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW = "REVIEW"
    ERROR = "ERROR"


class DiffCategory(str, enum.Enum):
    NEW = "NEW"
    RETAINED = "RETAINED"
    LOST = "LOST"
    IMPROVED = "IMPROVED"
    REGRESSED = "REGRESSED"
    UNSUPPORTED = "UNSUPPORTED"
    FORMAT_CHANGE = "FORMAT_CHANGE"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(slots=True)
class Requirement:
    id: str
    description: str
    weight: float = 1.0
    critical: bool = False
    evaluator: str = "contains"
    value: Any = None
    threshold: float = 0.5
    dimension: str = "fact"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("requirement id must not be empty")
        if self.weight <= 0:
            raise ValueError(f"requirement {self.id!r} must have a positive weight")
        if not 0 <= self.threshold <= 1:
            raise ValueError(f"requirement {self.id!r} threshold must be between 0 and 1")
        if not self.dimension.strip():
            raise ValueError(f"requirement {self.id!r} dimension must not be empty")


@dataclass(slots=True)
class OutputContract:
    json_schema: dict[str, Any] | None = None
    required_fields: list[str] = field(default_factory=list)
    forbidden_fields: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    max_length: int | None = None
    validator: str | None = None


@dataclass(slots=True)
class Case:
    id: str
    input: Any
    requirements: list[Requirement] = field(default_factory=list)
    ideal_answer: str | None = None
    references: list[Any] = field(default_factory=list)
    forbidden_content: list[str] = field(default_factory=list)
    contract: OutputContract | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("case id must not be empty")
        ids = [requirement.id for requirement in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError(f"case {self.id!r} has duplicate requirement ids")


@dataclass(slots=True)
class Policy:
    fail_on_critical_loss: bool = True
    fail_on_unmet_critical: bool = True
    min_critical_pass_rate: float = 1.0
    max_coverage_drop: float = 0.0
    max_format_failures: int = 0
    max_forbidden_content_matches: int = 0
    max_unsupported_claim_rate: float | None = None
    max_error_count: int = 0
    max_p95_latency_ms: float | None = None
    min_coverage: float | None = None
    min_case_pass_rate: float | None = None
    fail_on_any_requirement_failure: bool = False
    warn_on_review: bool = True
    warn_on_partial_case_pass: bool = True

    def __post_init__(self) -> None:
        for name in ("min_critical_pass_rate", "max_coverage_drop"):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        for name in ("max_unsupported_claim_rate", "min_coverage", "min_case_pass_rate"):
            value = getattr(self, name)
            if value is not None and not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        for name in ("max_format_failures", "max_forbidden_content_matches", "max_error_count"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")

    @classmethod
    def no_critical_regressions(cls) -> "Policy":
        return cls(fail_on_critical_loss=True)


@dataclass(slots=True)
class Suite:
    name: str
    cases: list[Case]
    project: str = "evaldiff"
    repetitions: int = 1
    concurrency: int = 4
    system: dict[str, Any] = field(default_factory=dict)
    baseline: str | None = None
    policy: Policy = field(default_factory=Policy)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.repetitions < 1:
            raise ValueError("repetitions must be at least 1")
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("suite has duplicate case ids")


@dataclass(slots=True)
class SystemOutput:
    content: Any
    trace: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    cost: float | None = None

    @property
    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return json.dumps(self.content, ensure_ascii=False, sort_keys=True)


@dataclass(slots=True)
class SystemVersion:
    name: str = "current"
    model: str | None = None
    provider: str | None = None
    revision: str | None = None
    git_commit: str | None = None
    prompt_fingerprint: str | None = None
    retriever_fingerprint: str | None = None
    tool_fingerprint: str | None = None
    config_fingerprint: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "SystemVersion":
        version = dict(config.get("version", {}))
        version.setdefault("name", config.get("name", "current"))
        version.setdefault("provider", config.get("adapter"))
        version["config_fingerprint"] = fingerprint(config)
        return cls(**{key: value for key, value in version.items() if key in {f.name for f in dataclasses.fields(cls)}})


@dataclass(slots=True)
class MetricResult:
    evaluator: str
    name: str
    status: MetricStatus
    score: float | None = None
    requirement_id: str | None = None
    evidence: list[str] = field(default_factory=list)
    confidence: float | None = None
    deterministic: bool = True
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionResult:
    repetition: int
    output: SystemOutput | None
    metrics: list[MetricResult]
    latency_ms: float
    system_latency_ms: float = 0.0
    evaluation_latency_ms: float = 0.0
    queue_latency_ms: float = 0.0
    error: str | None = None


@dataclass(slots=True)
class CaseResult:
    case_id: str
    executions: list[ExecutionResult]
    requirement_scores: dict[str, float]
    coverage: float
    contract_pass_rate: float
    pass_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    system_latency_p50_ms: float = 0.0
    system_latency_p95_ms: float = 0.0
    evaluation_latency_p50_ms: float = 0.0
    evaluation_latency_p95_ms: float = 0.0
    cost_mean: float | None = None


@dataclass(slots=True)
class DiffItem:
    category: DiffCategory
    case_id: str
    requirement_id: str | None = None
    previous_score: float | None = None
    current_score: float | None = None
    previous_evidence: list[str] = field(default_factory=list)
    current_evidence: list[str] = field(default_factory=list)
    evaluator: str | None = None
    confidence: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PolicyDecision:
    status: RunStatus
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EvalRun:
    id: str
    project: str
    suite_name: str
    system_version: SystemVersion
    cases: list[CaseResult]
    coverage: float
    status: RunStatus = RunStatus.REVIEW
    decision_reasons: list[str] = field(default_factory=list)
    diffs: list[DiffItem] = field(default_factory=list)
    baseline_run_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        delta = ""
        if "coverage_delta" in self.metadata:
            delta = f" ({self.metadata['coverage_delta']:+.1%})"
        return f"EvalDiff {self.status.value}: coverage {self.coverage:.1%}{delta}; {len(self.diffs)} diff item(s)"

    def to_dict(self) -> dict[str, Any]:
        return _to_primitive(self)


def _to_primitive(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {field.name: _to_primitive(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_primitive(item) for item in value]
    return value
