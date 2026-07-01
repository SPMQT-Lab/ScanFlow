"""Neutral ScanFlow acquisition sidecars for downstream tools.

The sidecar's in-memory form is :class:`scanflow.contracts.ScanRecord` —
this module converts control-layer objects (ScanParams, MotionResult) to
JSON-safe dicts, builds a ScanRecord, and serialises its payload. Schema
changes belong in the contracts module, not here.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scanflow.contracts import SCAN_RECORD_SCHEMA, ScanRecord

from .acquisition_log import write_json_atomic
from .json_util import json_safe as _json_safe

SCAN_SIDECAR_SCHEMA = SCAN_RECORD_SCHEMA  # "scanflow.acquisition.v1"
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
        self.created_at = _utc_now()
        self.updated_at = self.created_at
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
        self.updated_at = _utc_now()
        if self.path is None:
            return None
        payload = {
            "schema": SESSION_SCHEMA,
            "record_type": "scanflow_session",
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
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
    quality: dict[str, Any] | None = None,
    safety: dict[str, Any] | None = None,
    include_hash: bool = False,
) -> Path:
    dat = Path(dat_path)
    sidecar = scanflow_sidecar_path(dat)
    record = ScanRecord(
        session_id=session_id,
        routine=routine,
        raw_path=dat.name,
        created_at=_utc_now(),
        scanflow_version=_scanflow_version(),
        sha256=_sha256(dat) if include_hash and dat.exists() else None,
        step_index=step_index,
        step_kind=step_kind,
        step_label=step_label,
        scan_parameters=_scan_params_payload(scan_parameters),
        scan_offset_nm=tuple(position_nm) if position_nm is not None else None,
        motion=_json_safe(motion) if motion is not None else None,
        quality=quality or {},
        safety=safety or {},
    )
    return write_json_atomic(sidecar, record.to_payload())


def _scan_params_payload(params: Any | None) -> dict[str, Any]:
    if params is None:
        return {}
    data = _json_safe(params)
    if isinstance(data, dict):
        return data
    return {}


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

