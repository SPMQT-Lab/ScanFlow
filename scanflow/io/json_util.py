"""Shared JSON helpers for sidecar and acquisition log writers."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


def json_safe(value: Any) -> Any:
    """Recursively convert a value into JSON-serialisable primitives.

    Handles dataclasses (via asdict), Path (str), numpy scalars (.item()),
    and dict/list/tuple containers.
    """
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value
