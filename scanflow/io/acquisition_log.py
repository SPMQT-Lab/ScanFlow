"""JSON-lines acquisition logging for ScanFlow routines."""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AcquisitionLog:
    """Append-only JSONL log for routine events.

    The text GUI log stays concise; this file keeps the structured data needed
    to audit motion, safety, drift, and scan-save behavior after the run.
    """

    def __init__(self, path: Path | str | None) -> None:
        self.path = Path(path).resolve() if path else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event_type: str, **payload: Any) -> None:
        if self.path is None:
            return
        event = {
            "timestamp": _utc_now(),
            "event_type": str(event_type),
            **{str(k): _json_safe(v) for k, v in payload.items()},
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True, default=str) + "\n")


def default_acquisition_log_path(save_folder: str | Path | None) -> Path | None:
    if not save_folder:
        return None
    folder = Path(save_folder)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return folder / f"scanflow_acquisition_{stamp}.jsonl"


def write_json_atomic(path: Path | str, payload: dict[str, Any]) -> Path:
    """Write JSON via sibling temp file then replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=target.name + ".tmp",
        suffix=".json",
    )
    try:
        with open(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(_json_safe(payload), fh, indent=2, sort_keys=False, default=str)
        Path(tmp_name).replace(target)
    except Exception:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value

