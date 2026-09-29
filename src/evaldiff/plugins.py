from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any


PLUGIN_GROUPS = {
    "adapters": "evaldiff.adapters",
    "evaluators": "evaldiff.evaluators",
    "reporters": "evaldiff.reporters",
    "stores": "evaldiff.stores",
}


def discover_plugins(kind: str) -> dict[str, Any]:
    try:
        group = PLUGIN_GROUPS[kind]
    except KeyError as exc:
        raise ValueError(f"unknown plugin kind: {kind}") from exc
    discovered: dict[str, Any] = {}
    for entry_point in entry_points(group=group):
        try:
            discovered[entry_point.name] = entry_point.load()
        except Exception as exc:
            discovered[entry_point.name] = exc
    return discovered
