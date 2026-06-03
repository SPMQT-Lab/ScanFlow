"""Qt-aware worker threads for preview-driven follow-up scans.

These QThread subclasses contain the scan/motion/save orchestration
that previously lived inline in ``gui/panels/preview_panel.py``. Moving
them out of the GUI panel keeps the panel focused on UI, makes the
business logic testable in isolation, and lets future CLI/batch
entrypoints reuse the same code paths.
"""

from .paths import latest_dat_in_folder, unique_dat_path
from .preview_followup import FeatureScanWorker
from .preview_groups import FeatureGroupScanWorker

__all__ = [
    "FeatureScanWorker",
    "FeatureGroupScanWorker",
    "latest_dat_in_folder",
    "unique_dat_path",
]
