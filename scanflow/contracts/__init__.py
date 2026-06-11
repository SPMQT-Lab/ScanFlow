"""Shared data contracts between control, analysis, ML, and GUI layers.

This is the stabilising package of the layered architecture
(docs/long_term_architecture.md): every layer may import it, and it
imports nothing but the standard library. Dependency rule, enforced by
tests/test_import_boundaries.py:

    scanflow.contracts -> stdlib ONLY
    (no numpy, no Qt, no scanflow.core / automation / gui / io)

The contract chain:

    ScanRecord  ->  AnalysisResult  ->  ProposedAction
                ->  ValidationResult  ->  ValidatedAction

Analysis/ML construct ScanRecord readers, AnalysisResults, and
ProposedActions. Only the control layer (scanflow.automation.proposals)
turns proposals into ValidationResults / ValidatedActions, and only
validated actions execute.
"""

from .coordinates import (
    CREATEC_SCAN_OFFSET_NM,
    IMAGE_CENTER_RELATIVE_NM,
    IMAGE_PIXELS,
    KNOWN_FRAMES,
    require_known_frame,
)
from .scan_record import SCAN_RECORD_SCHEMA, ScanRecord
from .analysis import ANALYSIS_RESULT_SCHEMA, AnalysisResult, Feature
from .actions import (
    ACTION_KINDS,
    ProposedAction,
    ValidatedAction,
    ValidationResult,
)

__all__ = [
    "CREATEC_SCAN_OFFSET_NM",
    "IMAGE_CENTER_RELATIVE_NM",
    "IMAGE_PIXELS",
    "KNOWN_FRAMES",
    "require_known_frame",
    "SCAN_RECORD_SCHEMA",
    "ScanRecord",
    "ANALYSIS_RESULT_SCHEMA",
    "AnalysisResult",
    "Feature",
    "ACTION_KINDS",
    "ProposedAction",
    "ValidationResult",
    "ValidatedAction",
]
