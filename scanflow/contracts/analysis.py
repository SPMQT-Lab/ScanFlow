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
