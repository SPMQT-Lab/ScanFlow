"""Public automation API.

Recipe and model imports stay Qt-free. ``AutomationRunner`` is imported lazily
so non-GUI tests and ProbeFlow-side metadata readers do not require PySide6.
"""

from .recipe import (
    MeasurementRecipe, ScanStep, SpectroscopyStep, ApproachStep, WaitStep,
    TipFormStep, SurveyStep, MosaicStep,
)
from .survey import SurveyConfig, FeatureRecord, SurveyManifest
from .mosaic import MosaicConfig, tile_centers_in_wide_pixels
from .feature_discovery import FeatureCandidate, discover_features

__all__ = [
    "MeasurementRecipe",
    "ScanStep", "SpectroscopyStep", "ApproachStep", "WaitStep", "TipFormStep",
    "SurveyStep", "MosaicStep",
    "AutomationRunner", "RunnerState",
    "SurveyConfig", "FeatureRecord", "SurveyManifest",
    "MosaicConfig", "tile_centers_in_wide_pixels",
    "FeatureCandidate", "discover_features",
]


def __getattr__(name: str):
    if name in {"AutomationRunner", "RunnerState"}:
        from .runner import AutomationRunner, RunnerState
        return {"AutomationRunner": AutomationRunner, "RunnerState": RunnerState}[name]
    raise AttributeError(name)
