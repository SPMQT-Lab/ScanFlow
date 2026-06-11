"""Classical image analysis — observes scans, proposes actions.

Phase 4 of docs/long_term_architecture.md begins here: analysis code
lives in its own namespace, speaks to the rest of ScanFlow through
``scanflow.contracts``, and NEVER commands the instrument.

Dependency rule (enforced by tests/test_import_boundaries.py): this
package may import ``scanflow.contracts``, numpy, and (lazily)
scikit-image — but not Qt, not ``scanflow.core`` (no instrument access),
and not ``scanflow.automation`` (no control-layer coupling). Anything an
analyzer wants done on the instrument must leave this package as a
:class:`scanflow.contracts.ProposedAction`.

Contents:

* :mod:`~scanflow.analysis.feature_discovery` — bright-feature
  segmentation (moved from ``scanflow.automation``; a re-export shim
  keeps the old import path working).
* :mod:`~scanflow.analysis.detectors` — the FeatureDetector interface
  and the in-house threshold detector, emitting contract
  ``AnalysisResult`` objects. ML detectors implement the same interface
  in their own package.
* :mod:`~scanflow.analysis.planners` — turn an ``AnalysisResult`` into
  ``ProposedAction`` suggestions for the control core to validate.

The end-to-end hand-off is demonstrated in
tests/test_analysis_handoff.py and documented in
docs/analysis_ml_handoff.md.
"""

from .detectors import FeatureDetector, ThresholdFeatureDetector
from .planners import ZoomFeaturesPlanner

__all__ = [
    "FeatureDetector",
    "ThresholdFeatureDetector",
    "ZoomFeaturesPlanner",
]
