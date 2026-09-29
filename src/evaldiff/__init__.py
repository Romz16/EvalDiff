"""Public API for EvalDiff."""

from .adapters import HTTPAdapter, PythonCallableAdapter, SystemAdapter
from .models import (
    Case,
    DiffCategory,
    EvalRun,
    OutputContract,
    Policy,
    Requirement,
    RunStatus,
    Suite,
    SystemOutput,
    SystemVersion,
)
from .runner import evaluate, evaluate_async

__all__ = [
    "Case",
    "DiffCategory",
    "EvalRun",
    "HTTPAdapter",
    "OutputContract",
    "Policy",
    "PythonCallableAdapter",
    "Requirement",
    "RunStatus",
    "Suite",
    "SystemAdapter",
    "SystemOutput",
    "SystemVersion",
    "evaluate",
    "evaluate_async",
]

__version__ = "0.1.0"

