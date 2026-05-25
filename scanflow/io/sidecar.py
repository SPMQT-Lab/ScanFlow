"""Neutral ScanFlow acquisition sidecars for downstream tools."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .acquisition_log import write_json_atomic

SCAN_SIDECAR_SCHEMA = "scanflow.acquisition.v1"
SESSION_SCHEMA = "scanflow.session.v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_session_id() -> str:
    return str(uuid.uuid4())


def scanflow_sidecar_path(dat_path: Path | str) -> Path:
    return Path(dat_path).with_suffix(".scanflow.json")


def session_manifest_path(folder: Path | str) -> Path:
    return Path(folder) / "scanflow_session.json"


class SessionManifestWriter:
    """Maintains ``scanflow_session.json`` during an active routine."""

    def __init__(
        self,
        folder: Path | str | None,
        *,
        session_id: str | None,
        recipe_name: str,
        routine: str,
    ) -> None:
        self.folder = Path(folder).resolve() if folder else None
        self.session_id = session_id or new_session_id()
        self.recipe_name = recipe_name
        self.routine = routine
        self.scans: list[dict[str, Any]] = []
        self.path = session_manifest_path(self.folder) if self.folder else None

    def add_scan(
        self,
        *,
        dat_path: Path | str,
        sidecar_path: Path | str,
        role: str,
        step_index: int | None = None,
        label: str = "",
    ) -> None:
        self.scans.append({
            "dat_path": _rel_or_abs(dat_path, self.folder),
            "sidecar_path": _rel_or_abs(sidecar_path, self.folder),
            "role": role,
            "step_index": step_index,
            "label": label,
        })
        self.write()

    def write(self) -> Path | None:
        if self.path is None:
            return None
        payload = {
            "schema": SESSION_SCHEMA,
            "record_type": "scanflow_session",
            "session_id": self.session_id,
            "created_at": _utc_now(),
            "instrument": {
                "vendor": "Createc",
                "client": "STMAFM COM",
            },
            "recipe": {
                "name": self.recipe_name,
                "routine": self.routine,
            },
            "scans": self.scans,
        }
        return write_json_atomic(self.path, payload)


def write_scan_sidecar(
    dat_path: Path | str,
    *,
    session_id: str,
    routine: str,
    step_index: int | None,
    step_kind: str,
    step_label: str,
    scan_parameters: Any | None = None,
    position_nm: tuple[float, float] | None = None,
    motion: Any | None = None,
    drift: Any | None = None,
    quality: dict[str, Any] | None = None,
    safety: dict[str, Any] | None = None,
    include_hash: bool = False,
) -> Path:
    dat = Path(dat_path)
    sidecar = scanflow_sidecar_path(dat)
    payload = {
        "schema": SCAN_SIDECAR_SCHEMA,
        "record_type": "scanflow_scan",
        "scanflow_version": _scanflow_version(),
        "created_at": _utc_now(),
        "raw_file": {
            "path": dat.name,
            "source_format": "createc_dat",
            "sha256": _sha256(dat) if include_hash and dat.exists() else None,
        },
        "session": {
            "session_id": session_id,
            "routine": routine,
        },
        "step": {
            "index": step_index,
            "kind": step_kind,
            "label": step_label,
        },
        "scan_parameters": _scan_params_payload(scan_parameters),
        "position": {
            "scan_offset_nm": list(position_nm) if position_nm is not None else None,
        },
        "motion": _json_safe(motion) if motion is not None else None,
        "drift": _drift_payload(drift),
        "quality": quality or {},
        "safety": safety or {},
    }
    return write_json_atomic(sidecar, payload)


def _scan_params_payload(params: Any | None) -> dict[str, Any]:
    if params is None:
        return {}
    data = _json_safe(params)
    if isinstance(data, dict):
        return data
    return {}


def _drift_payload(drift: Any | None) -> dict[str, Any]:
    if drift is None:
        return {"enabled": False}
    data = _json_safe(drift)
    if isinstance(data, dict):
        data.setdefault("enabled", True)
        return data
    return {"enabled": True, "value": data}


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


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _scanflow_version() -> str | None:
    try:
        from scanflow import __version__
        return str(__version__)
    except Exception:
        return None


def _rel_or_abs(path: Path | str, base: Path | None) -> str:
    p = Path(path)
    if base is None:
        return str(p)
    try:
        return str(p.resolve().relative_to(base.resolve()))
    except Exception:
        return str(p)

