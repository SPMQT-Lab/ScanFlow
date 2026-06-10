"""Back-compat shim — feature discovery moved to ``scanflow.analysis``.

Kept so existing imports keep working; new code should import from
:mod:`scanflow.analysis.feature_discovery`. Remove once nothing imports
this path (grep before deleting).
"""

from scanflow.analysis.feature_discovery import (  # noqa: F401
    FeatureCandidate,
    discover_features,
    _level_correct,
)

__all__ = ["FeatureCandidate", "discover_features"]
