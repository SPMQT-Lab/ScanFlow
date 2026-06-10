"""Drift estimation — the dedicated home for ALL drift strategies.

ROADMAP §4 rule: every drift-estimation/correction strategy lives here,
behind one small interface, so strategies are swappable, comparable, and
never intermingled with runner logic, panels, or core controllers again
(the old hybrid alignment-scan code grew inside the runner and had to be
removed wholesale).

Authority model (docs/long_term_architecture.md): this package only
OBSERVES — estimators consume images and return a
:class:`~scanflow.drift.estimators.DriftEstimate`. Applying a correction
is control-layer work (TipMotionManager / the proposals chain) and is
NOT implemented here; wiring estimation into mosaic/survey execution is
gated on the B2 frame-resize experiment and the Phase 3 executor
extraction.

Hard boundary (enforced by tests/test_import_boundaries.py): this
package must not import Qt, ``scanflow.core`` (no instrument access),
or anything heavier than numpy + the feature-discovery module.

Every estimator must hold the baselines pinned in
tests/test_drift_estimators.py against the synthetic tracking scenes —
new strategies are compared there BEFORE any rig use.
"""

from .estimators import (
    ALL_ESTIMATORS,
    DriftEstimate,
    DriftEstimator,
    FeatureMatchDriftEstimator,
    PhaseCorrelationDriftEstimator,
)

__all__ = [
    "ALL_ESTIMATORS",
    "DriftEstimate",
    "DriftEstimator",
    "FeatureMatchDriftEstimator",
    "PhaseCorrelationDriftEstimator",
]
