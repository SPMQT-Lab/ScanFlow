"""Core instrument-control API.

Dependency rule: this package must stay importable with only the standard
library + numpy — no Qt, no analysis/ML packages. Recipes, the CLI
``estimate`` path, and ProbeFlow-side metadata readers all import
``scanflow.core`` (directly or via submodules, which executes this file),
so anything eager here lands in every entry path.

The two Qt-based helpers (``temp_poller``, ``atom_tracker``) are therefore
re-exported lazily via module ``__getattr__`` (PEP 562):
``from scanflow.core import TemperaturePoller`` still works, but PySide6
is only imported when those names are actually used (i.e. by the GUI).
Guarded by tests/test_import_boundaries.py.
"""

from .stm_client import STMClient, STMNotConnectedError
from .scan import ScanController, ScanParams, ScanStatus, Channel
from .feedback import FeedbackController
from .coarse import CoarseController, ApproachConfig, RampParams
from .lockin import LockInController, LockInMode
from .spectroscopy import SpectroscopyController, IVTable
from .afm import AFMController
from .tipform import (
    TipFormController, TipFormParams,
    TipFormMotionAssessment, assess_tip_form_motion,
)
from .temperature import TemperatureMonitor
from .scan_geometry import (
    clamp_frame_to_wide_bounds,
    feature_target_xy_nm,
    frame_top_edge_y_nm,
    image_center_y_nm,
)
from .lateral import LateralController, LateralParams
from .safety import SafetyMonitor, SafetyConfig, SafetyStatus, SafetyViolation
from .motion import (
    TipMotionManager, MotionConfig, MotionResult, XYCalibration, XYPosition,
)

# Qt-dependent names served lazily — see module docstring.
_LAZY_QT_EXPORTS = {
    "TemperaturePoller": "temp_poller",
    "SENSOR_LABELS": "temp_poller",
    "SENSOR_COLORS": "temp_poller",
    "SENSOR_FIELDS": "temp_poller",
    "_FivePointWorker": "atom_tracker",
    "TrackResult": "atom_tracker",
    "find_reference_nm": "atom_tracker",
}


def __getattr__(name: str):
    module_name = _LAZY_QT_EXPORTS.get(name)
    if module_name is not None:
        import importlib
        module = importlib.import_module(f".{module_name}", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "STMClient", "STMNotConnectedError",
    "ScanController", "ScanParams", "ScanStatus", "Channel",
    "FeedbackController",
    "CoarseController", "ApproachConfig", "RampParams",
    "LockInController", "LockInMode",
    "SpectroscopyController", "IVTable",
    "AFMController",
    "TipFormController", "TipFormParams",
    "TipFormMotionAssessment", "assess_tip_form_motion",
    "TemperatureMonitor",
    "clamp_frame_to_wide_bounds", "feature_target_xy_nm",
    "frame_top_edge_y_nm", "image_center_y_nm",
    "TemperaturePoller", "SENSOR_LABELS", "SENSOR_COLORS", "SENSOR_FIELDS",
    "TrackResult", "find_reference_nm",
    "LateralController", "LateralParams",
    "SafetyMonitor", "SafetyConfig", "SafetyStatus", "SafetyViolation",
    "TipMotionManager", "MotionConfig", "MotionResult", "XYCalibration", "XYPosition",
]
