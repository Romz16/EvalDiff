from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from .models import Case, SystemOutput


@runtime_checkable
class SystemAdapter(Protocol):
    async def invoke(self, case: Case) -> SystemOutput: ...


def import_object(path: str) -> Any:
    module_name, separator, object_name = path.partition(":")
    if not separator:
        module_name, separator, object_name = path.rpartition(".")
    if not module_name or not object_name:
        raise ValueError(f"expected a dotted path or module:object, got {path!r}")
    module = importlib.import_module(module_name)
    return getattr(module, object_name)


def normalize_output(value: Any) -> SystemOutput:
    if isinstance(value, SystemOutput):
        return value
    if isinstance(value, dict) and "content" in value:
        allowed = {"content", "trace", "usage", "metadata", "cost"}
        return SystemOutput(**{key: item for key, item in value.items() if key in allowed})
    return SystemOutput(content=value)


@dataclass(slots=True)
class PythonCallableAdapter:
    callable: Callable[[Any], Any] | str
    pass_case: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.callable, str):
            self.callable = import_object(self.callable)
        if not callable(self.callable):
            raise TypeError("callable adapter target is not callable")

    async def invoke(self, case: Case) -> SystemOutput:
        argument = case if self.pass_case else case.input
        if inspect.iscoroutinefunction(self.callable):
            result = self.callable(argument)
        else:
            result = await asyncio.to_thread(self.callable, argument)
        if inspect.isawaitable(result):
            result = await result
        return normalize_output(result)


@dataclass(slots=True)
class HTTPAdapter:
    endpoint: str
    timeout_seconds: float = 30.0
    headers: dict[str, str] = field(default_factory=dict)
    input_key: str = "input"

    async def invoke(self, case: Case) -> SystemOutput:
        return await asyncio.to_thread(self._invoke_sync, case)

    def _invoke_sync(self, case: Case) -> SystemOutput:
        payload = json.dumps({self.input_key: case.input, "case_id": case.id}).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json", **self.headers}
        request = urllib.request.Request(self.endpoint, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode(response.headers.get_content_charset() or "utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP adapter received {exc.code}: {body[:500]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"HTTP adapter request failed: {exc.reason}") from exc

        content_type = response.headers.get_content_type()
        if content_type == "application/json":
            return normalize_output(json.loads(raw))
        return SystemOutput(content=raw)


def adapter_from_config(config: dict[str, Any]) -> SystemAdapter:
    kind = config.get("adapter", "callable")
    if kind in {"callable", "python"}:
        target = config.get("target") or config.get("callable")
        if not target:
            raise ValueError("callable adapter requires 'target'")
        return PythonCallableAdapter(target, pass_case=bool(config.get("pass_case", False)))
    if kind == "http":
        endpoint = config.get("endpoint")
        if not endpoint:
            raise ValueError("http adapter requires 'endpoint'")
        return HTTPAdapter(
            endpoint=endpoint,
            timeout_seconds=float(config.get("timeout_seconds", 30)),
            headers=dict(config.get("headers", {})),
            input_key=config.get("input_key", "input"),
        )
    raise ValueError(f"unknown adapter type: {kind!r}")
