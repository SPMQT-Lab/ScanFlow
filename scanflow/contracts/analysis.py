"""Analysis-output contracts: what detectors/classifiers hand back.

Analysis and ML layers consume :class:`~scanflow.contracts.scan_record.
ScanRecord` (or the image it points to) and emit an
:class:`AnalysisResult` — a list of :class:`Feature` plus provenance.
They never command the instrument; anything actionable is expressed
separately as a :class:`~scanflow.contracts.actions.ProposedAction`.

Positions are physical (nm) wherever possible, and every Feature carries
an explicit coordinate ``frame`` — a Feature without a frame is
uninterpretable and the constructor refuses it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .coordinates import require_known_frame

ANALYSIS_RESULT_SCHEMA = "scanflow.analysis.v1"


@dataclass
class Feature:
    """One detected feature, located in an explicit coordinate frame."""

    feature_id: str
    x_nm: float
    y_nm: float
    frame: str                      # one of coordinates.KNOWN_FRAMES — mandatory

    #: (min_x, min_y, max_x, max_y) in the same frame, if available.
    bbox_nm: Optional[tuple[float, float, float, float]] = None
    label: Optional[str] = None     # e.g. "monomer", "cluster", "defect"
    confidence: Optional[float] = None

    source: str = ""                # algorithm/model that produced it
    source_version: str = ""

    def __post_init__(self) -> None:
        require_known_frame(self.frame, context=f"Feature {self.feature_id!r}")
        self.x_nm = float(self.x_nm)
        self.y_nm = float(self.y_nm)
        if self.confidence is not None:
            c = float(self.confidence)
            if not 0.0 <= c <= 1.0:
                raise ValueError(
                    f"Feature {self.feature_id!r}: confidence {c} not in [0, 1]"
                )
            self.confidence = c


@dataclass
class AnalysisResult:
    """What an analysis/ML pass found in one scan, with provenance."""

    analysis_id: str
    input_scan_id: str              # ScanRecord raw_path or session-scoped id
    algorithm: str
    algorithm_version: str
    created_at: str = ""
    schema: str = ANALYSIS_RESULT_SCHEMA

    features: list[Feature] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    quality_metrics: dict[str, float] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # JSON payloads — the cross-process hand-off format. An ML detector
    # running in its own environment writes this next to the sidecar;
    # ScanFlow-side planners/GUI read it back. Keep stable.
    # ------------------------------------------------------------------

    def to_payload(self) -> dict:
        return {
            "schema": self.schema,
            "analysis_id": self.analysis_id,
            "input_scan_id": self.input_scan_id,
            "algorithm": self.algorithm,
            "algorithm_version": self.algorithm_version,
            "created_at": self.created_at,
            "features": [
                {
                    "feature_id": f.feature_id,
                    "x_nm": f.x_nm,
                    "y_nm": f.y_nm,
                    "frame": f.frame,
                    "bbox_nm": list(f.bbox_nm) if f.bbox_nm is not None else None,
                    "label": f.label,
                    "confidence": f.confidence,
                    "source": f.source,
                    "source_version": f.source_version,
                }
                for f in self.features
            ],
            "warnings": list(self.warnings),
            "quality_metrics": dict(self.quality_metrics),
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "AnalysisResult":
        features = [
            Feature(
                feature_id=f.get("feature_id", ""),
                x_nm=f["x_nm"],
                y_nm=f["y_nm"],
                frame=f["frame"],
                bbox_nm=tuple(f["bbox_nm"]) if f.get("bbox_nm") else None,
                label=f.get("label"),
                confidence=f.get("confidence"),
                source=f.get("source", ""),
                source_version=f.get("source_version", ""),
            )
            for f in payload.get("features", [])
        ]
        return cls(
            analysis_id=payload.get("analysis_id", ""),
            input_scan_id=payload.get("input_scan_id", ""),
            algorithm=payload.get("algorithm", ""),
            algorithm_version=payload.get("algorithm_version", ""),
            created_at=payload.get("created_at", ""),
            schema=payload.get("schema", ANALYSIS_RESULT_SCHEMA),
            features=features,
            warnings=list(payload.get("warnings", [])),
            quality_metrics=dict(payload.get("quality_metrics", {})),
        )
