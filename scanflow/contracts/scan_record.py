"""ScanRecord — the dataclass behind the ``.scanflow.json`` sidecar.

There is exactly ONE schema for acquired-scan metadata
(``scanflow.acquisition.v1``); this dataclass is its in-memory form.
:func:`scanflow.io.sidecar.write_scan_sidecar` builds a ScanRecord and
serialises :meth:`ScanRecord.to_payload`; analysis/ML code reads sidecars
back with :meth:`ScanRecord.from_payload`. Do not invent a second,
parallel scan-metadata model — schema drift between "the sidecar" and
"the record" is exactly what this class exists to prevent.

Field notes:

* ``scan_parameters`` / ``motion`` / ``quality`` / ``safety`` are plain
  JSON-safe dicts. Contracts is stdlib-only and must not import
  ``ScanParams`` or ``MotionResult``; callers convert before constructing
  (the sidecar writer already does).
* ``scan_offset_nm`` is in the :data:`~scanflow.contracts.coordinates.
  CREATEC_SCAN_OFFSET_NM` frame (X = frame centre, Y = frame top edge);
  ``coordinate_system`` records that explicitly in the payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .coordinates import CREATEC_SCAN_OFFSET_NM, require_known_frame

SCAN_RECORD_SCHEMA = "scanflow.acquisition.v1"


@dataclass
class ScanRecord:
    """Stable record of one acquired scan (sidecar schema v1)."""

    session_id: str
    routine: str
    raw_path: str                                  # file name of the .dat
    created_at: str = ""
    schema: str = SCAN_RECORD_SCHEMA
    record_type: str = "scanflow_scan"
    scanflow_version: Optional[str] = None
    source_format: str = "createc_dat"
    sha256: Optional[str] = None

    step_index: Optional[int] = None
    step_kind: str = "scan"
    step_label: str = ""

    scan_parameters: dict[str, Any] = field(default_factory=dict)
    scan_offset_nm: Optional[tuple[float, float]] = None
    coordinate_system: str = CREATEC_SCAN_OFFSET_NM

    motion: Optional[dict[str, Any]] = None
    quality: dict[str, Any] = field(default_factory=dict)
    safety: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_known_frame(self.coordinate_system, context="ScanRecord")
        if self.scan_offset_nm is not None:
            x, y = self.scan_offset_nm
            self.scan_offset_nm = (float(x), float(y))

    # ------------------------------------------------------------------
    # Sidecar payload (the on-disk JSON shape — keep stable)
    # ------------------------------------------------------------------

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "record_type": self.record_type,
            "scanflow_version": self.scanflow_version,
            "created_at": self.created_at,
            "raw_file": {
                "path": self.raw_path,
                "source_format": self.source_format,
                "sha256": self.sha256,
            },
            "session": {
                "session_id": self.session_id,
                "routine": self.routine,
            },
            "step": {
                "index": self.step_index,
                "kind": self.step_kind,
                "label": self.step_label,
            },
            "scan_parameters": dict(self.scan_parameters),
            "position": {
                "scan_offset_nm": (list(self.scan_offset_nm)
                                   if self.scan_offset_nm is not None else None),
                "coordinate_system": self.coordinate_system,
            },
            "motion": self.motion,
            "quality": dict(self.quality),
            "safety": dict(self.safety),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ScanRecord":
        raw_file = payload.get("raw_file") or {}
        session = payload.get("session") or {}
        step = payload.get("step") or {}
        position = payload.get("position") or {}
        offset = position.get("scan_offset_nm")
        return cls(
            schema=payload.get("schema", SCAN_RECORD_SCHEMA),
            record_type=payload.get("record_type", "scanflow_scan"),
            scanflow_version=payload.get("scanflow_version"),
            created_at=payload.get("created_at", ""),
            raw_path=raw_file.get("path", ""),
            source_format=raw_file.get("source_format", "createc_dat"),
            sha256=raw_file.get("sha256"),
            session_id=session.get("session_id", ""),
            routine=session.get("routine", ""),
            step_index=step.get("index"),
            step_kind=step.get("kind", "scan"),
            step_label=step.get("label", ""),
            scan_parameters=payload.get("scan_parameters") or {},
            scan_offset_nm=tuple(offset) if offset else None,
            # Older sidecars (pre-contracts) carry no coordinate_system;
            # they were always written in the Createc offset frame.
            coordinate_system=position.get("coordinate_system",
                                           CREATEC_SCAN_OFFSET_NM),
            motion=payload.get("motion"),
            quality=payload.get("quality") or {},
            safety=payload.get("safety") or {},
        )
