"""FeatureDetector interface + the in-house threshold detector.

This is the hand-off point for analysis and ML (long_term_architecture
§13.1): a detector consumes an image (plus its physical scale) and emits
a :class:`scanflow.contracts.AnalysisResult` whose Features are in
physical units with an explicit coordinate frame and provenance
(algorithm + version). The rest of ScanFlow neither knows nor cares
which detector produced a result.

An ML detector lives in its own package (never imported by control
code), implements this same interface, and is compared against
``ThresholdFeatureDetector`` on the synthetic tracking scenes
(tests/synthetic_surfaces.py) before any rig use. Worked example:
tests/test_analysis_handoff.py; guide: docs/analysis_ml_handoff.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

import numpy as np

from scanflow.contracts import (
    IMAGE_CENTER_RELATIVE_NM,
    AnalysisResult,
    Feature,
)

from .feature_discovery import discover_features


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@runtime_checkable
class FeatureDetector(Protocol):
    """The one interface every feature detector implements."""

    name: str
    version: str

    def detect(self, image: np.ndarray, nm_per_px: float,
               *, input_scan_id: str = "") -> AnalysisResult:
        """Find features in ``image``; positions in physical units.

        ``image`` is a 2-D topography array (row 0 = first scanline),
        e.g. from ``ScanController.live_data()`` or a ``.dat`` loader.
        Loading files is the caller's job — detectors stay file-format
        agnostic.
        """
        ...


class ThresholdFeatureDetector:
    """Contract wrapper around the classical bright-feature segmentation.

    Emits Features in the IMAGE_CENTER_RELATIVE_NM frame (dx/dy from the
    image centre, +y downward — directly usable by the preview follow-up
    path and convertible via scan_geometry). ``confidence`` is left None:
    the threshold detector has no calibrated confidence and inventing one
    would mislead ranking code downstream.
    """

    name = "bright_feature_threshold"
    version = "1.0"

    def __init__(self, **discover_kwargs) -> None:
        #: passed through to discover_features (min/max feature size,
        #: merge distance, edge margin, max_features, ...)
        self.discover_kwargs = dict(discover_kwargs)

    def detect(self, image: np.ndarray, nm_per_px: float,
               *, input_scan_id: str = "") -> AnalysisResult:
        candidates = discover_features(image, nm_per_px, **self.discover_kwargs)
        ny, nx = image.shape
        cx0, cy0 = nx / 2.0, ny / 2.0

        features = []
        for i, cand in enumerate(candidates, start=1):
            min_row, min_col, max_row, max_col = cand.bbox_px
            features.append(Feature(
                feature_id=f"f{i:03d}",
                x_nm=(cand.cx_px - cx0) * nm_per_px,
                y_nm=(cand.cy_px - cy0) * nm_per_px,
                frame=IMAGE_CENTER_RELATIVE_NM,
                bbox_nm=(
                    (min_col - cx0) * nm_per_px,
                    (min_row - cy0) * nm_per_px,
                    (max_col - cx0) * nm_per_px,
                    (max_row - cy0) * nm_per_px,
                ),
                label=None,
                confidence=None,
                source=self.name,
                source_version=self.version,
            ))

        warnings = []
        if not features:
            warnings.append(
                "no features found — known conservative failure modes: "
                "low contrast, inverted polarity, partial frames, bright "
                "terraces (see tests/test_tracking_dataset.py)"
            )
        return AnalysisResult(
            analysis_id=str(uuid.uuid4()),
            input_scan_id=input_scan_id,
            algorithm=self.name,
            algorithm_version=self.version,
            created_at=_utc_now(),
            features=features,
            warnings=warnings,
            quality_metrics={"n_features": float(len(features))},
        )
