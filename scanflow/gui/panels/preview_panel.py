"""ScanFlow preview tab backed by ProbeFlow analysis kernels."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
import datetime as _dt
from pathlib import Path
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QFrame,
    QFormLayout,
    QLabel,
    QLineEdit,
    QGraphicsEllipseItem,
    QGraphicsRectItem,
    QGraphicsSimpleTextItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSlider,
    QSplitter,
    QScrollArea,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QMainWindow,
    QToolButton,
    QSizePolicy,
    QWidget,
    QCheckBox,
)

from probeflow.analysis.preview import (
    PreviewAnalysisParams,
    PreviewFeatureRow,
    apply_preview_background,
    detect_preview_features,
)
from probeflow.analysis.helpers import cv2_module, to_uint8_for_cv
from probeflow.analysis.features import Particle, classify_particles
from probeflow.core.scan_loader import load_scan
from probeflow.processing.geometry import set_zero_plane

from scanflow.core import STMClient, SafetyConfig, SafetyMonitor, TipMotionManager, ScanParams
from scanflow.gui import theme as _theme
from scanflow.core.scan import (
    estimate_scan_duration_s as _estimate_scan_duration,
    estimate_scan_timeout_s as _estimate_scan_timeout,
    format_duration as _format_duration,
)
from scanflow.automation.workers import (
    FeatureGroupScanWorker as _FeatureGroupScanWorker,
    FeatureScanWorker as _FeatureScanWorker,
    latest_dat_in_folder as _latest_dat_in_folder,
    unique_dat_path as _unique_dat_path,
)
from scanflow.automation.group_survey import FeatureGroup, group_features

log = logging.getLogger(__name__)


def _preview_stylesheet() -> str:
    return f"""
    QWidget#previewWindow {{
        background-color: {_theme.DARK_BG};
        color: {_theme.DARK_TEXT};
    }}
    QWidget#previewControls, QWidget#previewTableBox {{
        background-color: {_theme.DARK_BG};
    }}
    QLabel#previewStatus {{
        background-color: {_theme.DARK_BLUE_DARK};
        color: {_theme.DARK_WHITE};
        border: 1px solid {_theme.DARK_BORDER};
        border-left: 4px solid {_theme.AMBER};
        border-radius: 4px;
        padding: 7px 10px;
        font-weight: 600;
        min-height: 28px;
    }}
    QLabel#previewScanInfo {{
        background-color: {_theme.DARK_SURFACE};
        color: {_theme.DARK_TEXT};
        border: 1px solid {_theme.DARK_BORDER};
        border-radius: 4px;
        padding: 4px 8px;
        min-height: 22px;
        font-family: monospace;
    }}
    QLabel#previewTime {{
        color: {_theme.DARK_TEXT_MUTED};
        font-style: italic;
        padding: 2px 4px;
    }}
    QLabel#previewSection {{
        color: {_theme.DARK_BLUE};
        font-weight: 700;
        margin-top: 6px;
        margin-bottom: 2px;
    }}
    QWidget#previewControls QLabel {{
        color: {_theme.DARK_TEXT};
        background: transparent;
    }}
    QWidget#previewControls QLineEdit,
    QWidget#previewControls QComboBox,
    QWidget#previewControls QDoubleSpinBox,
    QWidget#previewControls QSpinBox {{
        background-color: {_theme.DARK_SURFACE};
        color: {_theme.DARK_TEXT};
        border: 1px solid {_theme.DARK_BORDER};
        border-radius: 4px;
        padding: 4px 8px;
        min-height: 24px;
    }}
    QWidget#previewControls QLineEdit:focus,
    QWidget#previewControls QComboBox:focus,
    QWidget#previewControls QDoubleSpinBox:focus,
    QWidget#previewControls QSpinBox:focus {{
        border: 1px solid {_theme.DARK_BLUE};
    }}
    QWidget#previewControls QSpinBox,
    QWidget#previewControls QDoubleSpinBox {{
        padding-right: 4px;
    }}
    QWidget#previewControls QSpinBox::up-button,
    QWidget#previewControls QSpinBox::down-button,
    QWidget#previewControls QDoubleSpinBox::up-button,
    QWidget#previewControls QDoubleSpinBox::down-button {{
        width: 0px;
        border: none;
        background: transparent;
    }}
    QWidget#previewControls QCheckBox {{
        color: {_theme.DARK_TEXT};
        background: transparent;
    }}
    QWidget#previewControls QListWidget,
    QWidget#previewTableBox QTableWidget {{
        background-color: {_theme.DARK_SURFACE};
        color: {_theme.DARK_TEXT};
        border: 1px solid {_theme.DARK_BORDER};
        border-radius: 4px;
        alternate-background-color: {_theme.DARK_BLUE_LIGHT};
        selection-background-color: {_theme.DARK_BLUE};
        selection-color: {_theme.DARK_WHITE};
    }}
    QWidget#previewTableBox QHeaderView::section,
    QWidget#previewControls QHeaderView::section {{
        background-color: {_theme.DARK_BLUE_LIGHT};
        color: {_theme.DARK_WHITE};
        padding: 4px 6px;
        border: none;
        border-right: 1px solid {_theme.DARK_BORDER};
        border-bottom: 1px solid {_theme.DARK_BORDER};
        font-weight: 700;
    }}
    QWidget#previewControls QPushButton,
    QWidget#previewTableBox QPushButton {{
        background-color: {_theme.DARK_BLUE};
        color: {_theme.DARK_WHITE};
        border: 1px solid rgba(255,255,255,0.12);
        border-radius: 4px;
        min-height: 28px;
        padding: 4px 10px;
        font-weight: 600;
    }}
    QWidget#previewControls QPushButton:hover,
    QWidget#previewTableBox QPushButton:hover {{
        background-color: #3B82F6;
    }}
    QWidget#previewControls QPushButton:disabled,
    QWidget#previewTableBox QPushButton:disabled,
    QWidget#previewControls QToolButton:disabled {{
        background-color: #22344D;
        color: {_theme.DARK_TEXT_MUTED};
        border: 1px solid #2B3D58;
    }}
    QWidget#previewControls QPushButton[role="secondary"] {{
        background-color: {_theme.DARK_BLUE};
        color: {_theme.DARK_WHITE};
        border: 1px solid rgba(255,255,255,0.10);
    }}
    QWidget#previewControls QPushButton[role="secondary"]:hover {{
        background-color: #3B82F6;
    }}
    QWidget#previewControls QPushButton[role="primary"] {{
        background-color: {_theme.DARK_BLUE};
        color: {_theme.DARK_WHITE};
        border: 1px solid rgba(255,255,255,0.10);
    }}
    QWidget#previewControls QPushButton[role="primary"]:hover {{
        background-color: #3B82F6;
    }}
    QWidget#previewControls QPushButton[role="primary"]:checked {{
        background-color: {_theme.DARK_BLUE_LIGHT};
        border: 1px solid {_theme.DARK_BORDER};
    }}
    QWidget#previewControls QPushButton[role="accent"] {{
        background-color: {_theme.AMBER};
        color: #1A1A1A;
        font-weight: 700;
    }}
    QWidget#previewControls QPushButton[role="accent"]:hover {{
        background-color: #E09A00;
    }}
    QWidget#previewControls QPushButton[role="danger"] {{
        background-color: #7f1d1d;
        color: {_theme.DARK_WHITE};
        border: 1px solid #b91c1c;
        font-weight: 700;
    }}
    QWidget#previewControls QPushButton[role="danger"]:hover {{
        background-color: #991b1b;
    }}
    QWidget#previewControls QPushButton[role="toggle"] {{
        background-color: {_theme.DARK_BLUE};
        color: {_theme.DARK_WHITE};
        border: 1px solid rgba(255,255,255,0.10);
    }}
    QWidget#previewControls QPushButton[role="toggle"]:hover {{
        background-color: #3B82F6;
    }}
    QWidget#previewControls QPushButton[role="toggle"]:checked {{
        background-color: {_theme.DARK_BLUE_LIGHT};
        border: 1px solid {_theme.DARK_BORDER};
    }}
    QWidget#previewControls QToolButton {{
        background-color: {_theme.DARK_BLUE};
        color: {_theme.DARK_WHITE};
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 4px;
        padding: 4px 10px;
        min-height: 28px;
        font-weight: 600;
    }}
    QWidget#previewControls QToolButton:hover {{
        background-color: #3B82F6;
    }}
    QWidget#previewControls QToolButton:checked {{
        background-color: {_theme.DARK_BLUE_LIGHT};
        border: 1px solid {_theme.DARK_BORDER};
    }}
    QWidget#previewControls QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollArea#previewControlsScroll {{
        background: transparent;
        border: none;
    }}
    QWidget#previewControls QScrollArea > QWidget {{
        background: transparent;
    }}
    QFrame#previewClassCard {{
        background-color: {_theme.DARK_SURFACE};
        border: 1px solid {_theme.DARK_BORDER};
        border-radius: 6px;
    }}
    QFrame#previewClassHeader {{
        background-color: #2E5279;
        border: 1px solid rgba(255,255,255,0.12);
        border-radius: 5px;
    }}
    QFrame#previewClassHeader[active="true"] {{
        background-color: #3A638F;
        border: 1px solid {_theme.AMBER};
    }}
    QFrame#previewClassHeader:hover {{
        background-color: #3D6B99;
    }}
    QFrame#previewClassHeader QLabel {{
        color: {_theme.DARK_WHITE};
        background: transparent;
        font-weight: 700;
    }}
    QFrame#previewClassHeader QToolButton {{
        background-color: #35507A;
        color: {_theme.DARK_WHITE};
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 4px;
        padding: 2px 6px;
        min-width: 22px;
        min-height: 22px;
    }}
    QFrame#previewClassHeader QToolButton:hover {{
        background-color: #406089;
    }}
    QFrame#previewClassHeader QPushButton[role="danger"] {{
        min-height: 22px;
        padding: 2px 8px;
        min-width: 54px;
    }}
    QWidget#previewClassBody {{
        background-color: {_theme.DARK_SURFACE};
    }}
    QLabel#previewClassStatus {{
        color: {_theme.DARK_TEXT_MUTED};
        background-color: #172840;
        border: 1px solid #2B4565;
        border-radius: 4px;
        padding: 3px 6px;
    }}
    QWidget#previewScanHub {{
        background-color: #172840;
        border: 1px solid #2B4565;
        border-radius: 6px;
    }}
    QPushButton[role="primary"],
    QToolButton[role="primary"] {{
        background-color: {_theme.DARK_BLUE};
        color: #061122;
        border: 1px solid #93C5FD;
        border-radius: 4px;
        min-height: 28px;
        padding: 4px 10px;
        font-weight: 700;
    }}
    QPushButton[role="primary"]:hover,
    QToolButton[role="primary"]:hover {{
        background-color: #93C5FD;
        border-color: #BFDBFE;
    }}
    QPushButton[role="toggle"] {{
        background-color: #315982;
        color: {_theme.DARK_WHITE};
        border: 1px solid #5A7DA6;
        border-radius: 4px;
        min-height: 28px;
        padding: 4px 10px;
        font-weight: 700;
    }}
    QPushButton[role="toggle"]:checked {{
        background-color: {_theme.DARK_BLUE};
        color: #061122;
        border-color: #93C5FD;
    }}
    QPushButton[role="accent"] {{
        background-color: {_theme.AMBER};
        color: #1A1A1A;
        border: 1px solid #FFD166;
        border-radius: 4px;
        min-height: 28px;
        padding: 4px 10px;
        font-weight: 800;
    }}
    QPushButton[role="accent"]:hover {{
        background-color: #E09A00;
    }}
    QPushButton[role="danger"] {{
        background-color: #7f1d1d;
        color: {_theme.DARK_WHITE};
        border: 1px solid #ef4444;
        border-radius: 4px;
        min-height: 28px;
        padding: 4px 10px;
        font-weight: 700;
    }}
    QPushButton[role="danger"]:hover {{
        background-color: #991b1b;
    }}
    QPushButton[role="primary"]:disabled,
    QToolButton[role="primary"]:disabled,
    QPushButton[role="toggle"]:disabled,
    QPushButton[role="accent"]:disabled,
    QPushButton[role="danger"]:disabled {{
        background-color: #22344D;
        color: {_theme.DARK_TEXT_MUTED};
        border: 1px solid #2B4565;
    }}
    QToolButton#previewTrayHeader {{
        background-color: #315982;
        color: {_theme.DARK_WHITE};
        border: 1px solid #5A7DA6;
        border-radius: 6px;
        padding: 7px 10px;
        font-weight: 700;
        text-align: left;
        min-height: 30px;
    }}
    QToolButton#previewTrayHeader:hover {{
        background-color: #3D6B99;
    }}
    QToolButton#previewTrayHeader:checked {{
        background-color: #3A638F;
        border-left: 4px solid {_theme.AMBER};
    }}
    QWidget#previewSectionContent {{
        background-color: #13233A;
        border: 1px solid #2E486A;
        border-radius: 6px;
        padding: 4px;
    }}
    QWidget#previewControls QScrollBar:vertical,
    QWidget#previewTableBox QScrollBar:vertical {{
        background: {_theme.DARK_BLUE_LIGHT};
        width: 10px;
        border-radius: 5px;
        margin: 0;
    }}
    QWidget#previewControls QScrollBar::handle:vertical,
    QWidget#previewTableBox QScrollBar::handle:vertical {{
        background: {_theme.DARK_BORDER};
        border-radius: 5px;
        min-height: 20px;
    }}
    QWidget#previewControls QScrollBar::handle:vertical:hover,
    QWidget#previewTableBox QScrollBar::handle:vertical:hover {{
        background: {_theme.DARK_BLUE};
    }}
    QWidget#previewControls QScrollBar:horizontal,
    QWidget#previewTableBox QScrollBar:horizontal {{
        background: {_theme.DARK_BLUE_LIGHT};
        height: 10px;
        border-radius: 5px;
    }}
    QWidget#previewControls QScrollBar::handle:horizontal,
    QWidget#previewTableBox QScrollBar::handle:horizontal {{
        background: {_theme.DARK_BORDER};
        border-radius: 5px;
        min-width: 20px;
    }}
    QWidget#previewControls QScrollBar::handle:horizontal:hover,
    QWidget#previewTableBox QScrollBar::handle:horizontal:hover {{
        background: {_theme.DARK_BLUE};
    }}
    """


@dataclass
class _PreviewState:
    source_path: Path
    scan: Any
    plane_index: int
    raw_plane: np.ndarray
    corrected_plane: np.ndarray | None = None
    background_image: np.ndarray | None = None
    preview_rows: tuple[PreviewFeatureRow, ...] = ()
    preview_particles: tuple[Particle, ...] = ()
    feature_rows: tuple[PreviewFeatureRow, ...] = ()
    particles: tuple[Particle, ...] = ()
    sample_labels: dict[int, str] = field(default_factory=dict)
    classifications: dict[int, str] = field(default_factory=dict)
    class_colors: dict[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass
class _ClassRecord:
    key: str
    label: str
    color: str
    name_is_placeholder: bool = False
    sample_indices: set[int] = field(default_factory=set)
    encoder: str = "raw"
    threshold: str = "gmm"
    crop_size: int = 48
    status: str = "No samples labelled yet."


@dataclass
class _ClassRowWidgets:
    root: QWidget
    header: "_ClickableFrame"
    body_scroll: QWidget
    arrow_btn: QToolButton
    name_label: QLabel
    swatch: QWidget
    count_label: QLabel
    delete_btn: QPushButton
    name_edit: QLineEdit
    encoder: QComboBox
    threshold: QComboBox
    crop_size: QSpinBox
    status: QLabel


class _WheelGuardComboBox(QComboBox):
    def wheelEvent(self, event) -> None:  # pragma: no cover - simple Qt guard
        event.ignore()


class _StageSection(QWidget):
    def __init__(self, title: str, *, expanded: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("previewSectionBox")
        self._toggle = QToolButton(self)
        self._toggle.setObjectName("previewTrayHeader")
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setText(title)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self._toggle.setProperty("section", True)
        self._toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._toggle.toggled.connect(self._on_toggled)

        self._content = QWidget(self)
        self._content.setObjectName("previewSectionContent")
        self._content.setVisible(expanded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._toggle)
        layout.addWidget(self._content)

    def content(self) -> QWidget:
        return self._content

    def setExpanded(self, expanded: bool) -> None:
        self._toggle.blockSignals(True)
        try:
            self._toggle.setChecked(expanded)
        finally:
            self._toggle.blockSignals(False)
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self._content.setVisible(expanded)

    def isExpanded(self) -> bool:
        return self._toggle.isChecked()

    def _on_toggled(self, checked: bool) -> None:
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        self._content.setVisible(checked)


class _ClickableFrame(QFrame):
    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # pragma: no cover - trivial Qt hook
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _ScanLoadWorker(QThread):
    result_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, source: Path) -> None:
        super().__init__()
        self._source = Path(source)

    def run(self) -> None:
        try:
            scan = load_scan(self._source)
        except Exception as exc:  # pragma: no cover - exercised via panel tests
            log.exception("Preview load failed for %s", self._source)
            self.failed.emit(str(exc))
            return
        self.result_ready.emit(scan)


class _BackgroundWorker(QThread):
    result_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, raw_plane: np.ndarray, params: PreviewAnalysisParams) -> None:
        super().__init__()
        self._raw_plane = np.asarray(raw_plane, dtype=np.float64)
        self._params = params

    def run(self) -> None:
        try:
            corrected, background_image = apply_preview_background(self._raw_plane, self._params)
        except Exception as exc:  # pragma: no cover - exercised via panel tests
            log.exception("Preview background subtraction failed")
            self.failed.emit(str(exc))
            return
        self.result_ready.emit((corrected, background_image))


class _SegmentationWorker(QThread):
    result_ready = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        plane: np.ndarray,
        scan_range_m: tuple[float, float] | None,
        *,
        threshold_value: int,
        min_area_slider: int,
        max_area_slider: int,
        invert: bool,
        feature_mode: str,
    ) -> None:
        super().__init__()
        self._plane = np.asarray(plane, dtype=np.float64)
        self._scan_range_m = scan_range_m
        self._threshold_value = int(threshold_value)
        self._min_area_slider = int(min_area_slider)
        self._max_area_slider = int(max_area_slider)
        self._invert = bool(invert)
        self._feature_mode = str(feature_mode)

    def run(self) -> None:
        try:
            particles, rows, warnings = _generate_unimr_segmentation_preview(
                self._plane,
                self._scan_range_m,
                threshold_value=self._threshold_value,
                min_area_slider=self._min_area_slider,
                max_area_slider=self._max_area_slider,
                invert=self._invert,
                feature_mode=self._feature_mode,
            )
        except Exception as exc:  # pragma: no cover - exercised via panel tests
            log.exception("Preview feature detection failed")
            self.failed.emit(str(exc))
            return
        self.result_ready.emit((particles, rows, warnings))


class _ClassificationWorker(QThread):
    result_ready = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        plane: np.ndarray,
        particles: list[Particle],
        samples: list[tuple[str, Particle]],
        *,
        encoder: str,
        threshold_method: str,
    ) -> None:
        super().__init__()
        self._plane = np.asarray(plane, dtype=np.float64)
        self._particles = list(particles)
        self._samples = list(samples)
        self._encoder = encoder
        self._threshold_method = threshold_method

    def run(self) -> None:
        try:
            classifs, used_encoder = _classify_particles_auto(
                self._plane,
                self._particles,
                self._samples,
                encoder=self._encoder,
                threshold_method=self._threshold_method,
            )
        except Exception as exc:  # pragma: no cover - exercised via panel tests
            log.exception("Preview particle classification failed")
            self.failed.emit(str(exc))
            return
        self.result_ready.emit((classifs, used_encoder))




class _GroupScanDialog(QDialog):
    """Parameter dialog shown before launching a grouped follow-up scan.

    Collects grouping knobs (max_per_group, max_group_nm, feature_padding_nm)
    and scan acquisition settings (pixels, speed, iterations, settling).
    """

    def __init__(self, parent=None, *, defaults: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scan as Groups — parameters")
        self.setModal(True)
        d = defaults or {}

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)

        # ── Grouping ──────────────────────────────────────────────────────
        self._max_per_group = QSpinBox()
        self._max_per_group.setRange(2, 8)
        self._max_per_group.setValue(int(d.get("max_per_group", 4)))
        self._max_per_group.setToolTip("Maximum number of features captured in one scan frame.")
        form.addRow("Max features / group", self._max_per_group)

        self._max_group_nm = QDoubleSpinBox()
        self._max_group_nm.setRange(5.0, 200.0)
        self._max_group_nm.setSingleStep(5.0)
        self._max_group_nm.setDecimals(1)
        self._max_group_nm.setSuffix(" nm")
        self._max_group_nm.setValue(float(d.get("max_group_nm", 30.0)))
        self._max_group_nm.setToolTip("Maximum side length of any group's scan frame.")
        form.addRow("Max frame size", self._max_group_nm)

        self._padding_nm = QDoubleSpinBox()
        self._padding_nm.setRange(0.5, 20.0)
        self._padding_nm.setSingleStep(0.5)
        self._padding_nm.setDecimals(1)
        self._padding_nm.setSuffix(" nm")
        self._padding_nm.setValue(float(d.get("feature_padding_nm", 3.0)))
        self._padding_nm.setToolTip("Margin added on each side of the feature bounding boxes.")
        form.addRow("Feature padding", self._padding_nm)

        # ── Scan acquisition ──────────────────────────────────────────────
        self._pixels = QComboBox()
        for label, val in [("128 × 128", 128), ("256 × 256", 256), ("512 × 512", 512)]:
            self._pixels.addItem(label, val)
        default_px = int(d.get("group_pixels", 256))
        for i in range(self._pixels.count()):
            if self._pixels.itemData(i) == default_px:
                self._pixels.setCurrentIndex(i)
                break
        form.addRow("Resolution", self._pixels)

        self._speed = QDoubleSpinBox()
        self._speed.setRange(1.0, 1000.0)
        self._speed.setSingleStep(5.0)
        self._speed.setDecimals(1)
        self._speed.setSuffix(" nm/s")
        self._speed.setValue(float(d.get("group_speed_nm_s", 20.0)))
        form.addRow("Scan speed", self._speed)

        self._time_preset = QComboBox()
        for _label, _minutes in [("5 min", 5), ("10 min", 10), ("15 min", 15), ("Custom", None)]:
            self._time_preset.addItem(_label, _minutes)
        self._time_preset.setCurrentIndex(3)  # default: Custom
        self._time_preset.setToolTip(
            "Shortcut: sets Scan speed so one full pass takes the chosen time.\n"
            "Uses Max frame size × Resolution to compute the speed."
        )
        form.addRow("Target scan time", self._time_preset)

        self._time_preset.currentIndexChanged.connect(self._on_time_preset_changed)
        self._max_group_nm.valueChanged.connect(self._on_time_preset_changed)
        self._pixels.currentIndexChanged.connect(self._on_time_preset_changed)
        self._speed.valueChanged.connect(self._on_speed_manual_edit)

        self._bias = QDoubleSpinBox()
        self._bias.setRange(-10.0, 10.0)
        self._bias.setDecimals(4)
        self._bias.setSingleStep(0.1)
        self._bias.setSuffix(" V")
        self._bias.setValue(float(d.get("bias_V", 0.1)))
        self._bias.setToolTip("Tip-sample bias for the group scan (must not be 0 V).")
        form.addRow("Bias", self._bias)

        self._setpoint = QDoubleSpinBox()
        self._setpoint.setRange(0.001, 1000.0)
        self._setpoint.setDecimals(3)
        self._setpoint.setSingleStep(0.1)
        self._setpoint.setSuffix(" nA")
        self._setpoint.setValue(float(d.get("setpoint_nA", 0.1)))
        self._setpoint.setToolTip("Tunnelling setpoint current.")
        form.addRow("Setpoint", self._setpoint)

        self._iterations = QSpinBox()
        self._iterations.setRange(1, 10)
        self._iterations.setValue(int(d.get("group_iterations", 3)))
        self._iterations.setToolTip("Number of repeat acquisitions per group (drift corrected).")
        form.addRow("Iterations / group", self._iterations)

        self._bias_seq = QLineEdit()
        self._bias_seq.setPlaceholderText("e.g. -1.0, -0.5, 0.5, 1.0  (overrides Bias + Iterations)")
        self._bias_seq.setText(str(d.get("bias_sequence_str", "")))
        self._bias_seq.setToolTip(
            "Comma-separated bias values (V).  Each group is scanned once per value.\n"
            "Leave empty to use Bias + Iterations instead."
        )
        form.addRow("Bias sequence", self._bias_seq)

        self._bias_seq.textChanged.connect(self._on_seq_changed)
        self._on_seq_changed(self._bias_seq.text())

        self._settling = QDoubleSpinBox()
        self._settling.setRange(0.0, 60.0)
        self._settling.setSingleStep(0.5)
        self._settling.setDecimals(1)
        self._settling.setSuffix(" s")
        self._settling.setValue(float(d.get("settling_s", 3.0)))
        self._settling.setToolTip("Settling pause after positioning and before each acquisition.")
        form.addRow("Settling time", self._settling)

        # ── Output folder (optional) ──────────────────────────────────────
        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        self._folder_edit = QLineEdit()
        self._folder_edit.setPlaceholderText("(optional — defaults to source scan folder)")
        self._folder_edit.setText(str(d.get("output_folder", "")))
        folder_browse = QPushButton("Browse…")
        folder_browse.setFixedWidth(80)
        folder_browse.clicked.connect(self._pick_folder)
        folder_layout.addWidget(self._folder_edit, 1)
        folder_layout.addWidget(folder_browse)
        form.addRow("Output folder", folder_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.adjustSize()

    # ------------------------------------------------------------------
    def _on_time_preset_changed(self) -> None:
        minutes = self._time_preset.currentData()
        if minutes is None:
            return
        px = int(self._pixels.currentData() or 256)
        frame_nm = float(self._max_group_nm.value())
        target_s = minutes * 60.0
        speed = max(1.0, min(1000.0, (2.0 * frame_nm * px) / target_s))
        self._speed.blockSignals(True)
        try:
            self._speed.setValue(speed)
        finally:
            self._speed.blockSignals(False)

    def _on_speed_manual_edit(self) -> None:
        for i in range(self._time_preset.count()):
            if self._time_preset.itemData(i) is None:
                self._time_preset.blockSignals(True)
                try:
                    self._time_preset.setCurrentIndex(i)
                finally:
                    self._time_preset.blockSignals(False)
                break

    def _on_seq_changed(self, text: str) -> None:
        active = bool(text.strip())
        self._bias.setEnabled(not active)
        self._iterations.setEnabled(not active)

    def _on_accept(self) -> None:
        seq_text = self._bias_seq.text().strip()
        if seq_text:
            try:
                biases = [float(x.strip()) for x in seq_text.split(",") if x.strip()]
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid bias sequence",
                    "Bias sequence must be comma-separated numbers, e.g. -1.0, -0.5, 0.5, 1.0"
                )
                return
            if any(abs(b) < 1e-9 for b in biases):
                QMessageBox.warning(
                    self, "Invalid bias",
                    "Bias must not be 0 V — scanning at 0 V in constant-current mode crashes the tip."
                )
                return
        else:
            if abs(self._bias.value()) < 1e-9:
                QMessageBox.warning(
                    self, "Invalid bias",
                    "Bias must not be 0 V — scanning at 0 V in constant-current mode crashes the tip."
                )
                return
        self.accept()

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Output folder for group scans")
        if path:
            self._folder_edit.setText(path)

    def values(self) -> dict:
        px = int(self._pixels.currentData() or 256)
        seq_text = self._bias_seq.text().strip()
        bias_sequence: list[float] = []
        if seq_text:
            bias_sequence = [float(x.strip()) for x in seq_text.split(",") if x.strip()]
        preset_minutes = self._time_preset.currentData()  # int or None (Custom)
        return {
            "max_per_group": int(self._max_per_group.value()),
            "max_group_nm": float(self._max_group_nm.value()),
            "feature_padding_nm": float(self._padding_nm.value()),
            "group_pixels": px,
            "group_speed_nm_s": float(self._speed.value()),
            "target_scan_time_s": float(preset_minutes * 60) if preset_minutes is not None else None,
            "group_iterations": int(self._iterations.value()),
            "settling_s": float(self._settling.value()),
            "output_folder": self._folder_edit.text().strip(),
            "bias_V": float(self._bias.value()),
            "setpoint_nA": float(self._setpoint.value()),
            "bias_sequence": bias_sequence,
            "bias_sequence_str": seq_text,
        }


class _FeatureScanDialog(QDialog):
    """Parameter dialog for the Queue-selected follow-up scan flow.

    Collects bias, setpoint, repetition count, and an optional multi-bias
    sequence.  When a bias sequence is provided it takes precedence: each
    feature is scanned once per bias value in the list and the single-bias /
    repetitions fields are ignored.
    """

    def __init__(self, parent=None, *, defaults: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Queue selected — scan parameters")
        self.setModal(True)
        d = defaults or {}

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)

        # ── Scan geometry ────────────────────────────────────────────────
        self._size = QDoubleSpinBox()
        self._size.setRange(1.0, 500.0)
        self._size.setSingleStep(1.0)
        self._size.setDecimals(1)
        self._size.setSuffix(" nm")
        self._size.setValue(float(d.get("size_nm", 10.0)))
        self._size.setToolTip("Square scan frame side length for each follow-up feature scan.")
        form.addRow("Scan size", self._size)

        self._pixels = QComboBox()
        for _label, _val in [("128 × 128", 128), ("256 × 256", 256), ("512 × 512", 512)]:
            self._pixels.addItem(_label, _val)
        _default_px = int(d.get("pixels", 256))
        for _i in range(self._pixels.count()):
            if self._pixels.itemData(_i) == _default_px:
                self._pixels.setCurrentIndex(_i)
                break
        self._pixels.setToolTip("Pixel resolution of each feature scan (no longer inherited from the wide scan).")
        form.addRow("Resolution", self._pixels)

        # Target wall-clock time per feature image. The worker computes the
        # scan speed from size + resolution to hit this, so small features
        # are always imaged in a fixed ~5 min instead of a few fast seconds.
        self._time_preset = QComboBox()
        for _label, _minutes in [("5 min", 5.0), ("10 min", 10.0),
                                 ("15 min", 15.0), ("Inherit rig speed", None)]:
            self._time_preset.addItem(_label, _minutes)
        _default_min = d.get("target_minutes", 5.0)
        _idx = next((i for i in range(self._time_preset.count())
                     if self._time_preset.itemData(i) == _default_min), 0)
        self._time_preset.setCurrentIndex(_idx)
        self._time_preset.setToolTip(
            "Per-image scan time. Sets the scan speed so each feature takes\n"
            "this long — keeps drift per image bounded. Default 5 min."
        )
        form.addRow("Target scan time", self._time_preset)

        # ── Electrical parameters ─────────────────────────────────────────
        self._bias = QDoubleSpinBox()
        self._bias.setRange(-10.0, 10.0)
        self._bias.setDecimals(4)
        self._bias.setSingleStep(0.1)
        self._bias.setSuffix(" V")
        self._bias.setValue(float(d.get("bias_V", 0.1)))
        self._bias.setToolTip("Tip-sample bias for the follow-up scan (must not be 0 V).")
        form.addRow("Bias", self._bias)

        self._setpoint = QDoubleSpinBox()
        self._setpoint.setRange(0.001, 1000.0)
        self._setpoint.setDecimals(3)
        self._setpoint.setSingleStep(0.1)
        self._setpoint.setSuffix(" nA")
        self._setpoint.setValue(float(d.get("setpoint_nA", 0.1)))
        self._setpoint.setToolTip("Tunnelling setpoint current.")
        form.addRow("Setpoint", self._setpoint)

        # ── Repetitions ───────────────────────────────────────────────────
        self._repetitions = QSpinBox()
        self._repetitions.setRange(1, 20)
        self._repetitions.setValue(int(d.get("repetitions", 1)))
        self._repetitions.setToolTip(
            "How many times each feature is scanned at the chosen bias/setpoint."
        )
        form.addRow("Repetitions", self._repetitions)

        # ── Bias sequence (optional) ──────────────────────────────────────
        self._bias_seq = QLineEdit()
        self._bias_seq.setPlaceholderText("e.g. -1.0, -0.5, 0.5, 1.0  (overrides Bias + Repetitions)")
        self._bias_seq.setText(str(d.get("bias_sequence_str", "")))
        self._bias_seq.setToolTip(
            "Comma-separated bias values (V).  Each feature is scanned once per value.\n"
            "Leave empty to use Bias + Repetitions instead."
        )
        form.addRow("Bias sequence", self._bias_seq)

        self._bias_seq.textChanged.connect(self._on_seq_changed)
        self._on_seq_changed(self._bias_seq.text())

        # ── Auto-center ───────────────────────────────────────────────────
        self._center_check = QCheckBox("Auto-center (quick scan before each feature)")
        self._center_check.setChecked(bool(d.get("enable_centering", False)))
        self._center_check.setToolTip(
            "Run a fast low-resolution finder scan, detect the feature position, "
            "and correct the tip offset before the full-resolution scan."
        )
        form.addRow("", self._center_check)

        self._quick_px_spin = QSpinBox()
        self._quick_px_spin.setRange(32, 128)
        self._quick_px_spin.setSingleStep(16)
        self._quick_px_spin.setValue(int(d.get("quick_pixels", 64)))
        self._quick_px_spin.setToolTip("Resolution of the quick finder scan (same area, same speed).")
        self._quick_px_spin.setEnabled(self._center_check.isChecked())
        form.addRow("Quick-scan pixels", self._quick_px_spin)

        self._center_check.toggled.connect(self._quick_px_spin.setEnabled)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.adjustSize()

    def _on_seq_changed(self, text: str) -> None:
        active = bool(text.strip())
        self._bias.setEnabled(not active)
        self._repetitions.setEnabled(not active)

    def _on_accept(self) -> None:
        seq_text = self._bias_seq.text().strip()
        if seq_text:
            try:
                biases = [float(x.strip()) for x in seq_text.split(",") if x.strip()]
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid bias sequence",
                    "Bias sequence must be comma-separated numbers, e.g. -1.0, -0.5, 0.5, 1.0"
                )
                return
            if any(abs(b) < 1e-9 for b in biases):
                QMessageBox.warning(
                    self, "Invalid bias",
                    "Bias must not be 0 V — scanning at 0 V in constant-current mode crashes the tip."
                )
                return
        else:
            if abs(self._bias.value()) < 1e-9:
                QMessageBox.warning(
                    self, "Invalid bias",
                    "Bias must not be 0 V — scanning at 0 V in constant-current mode crashes the tip."
                )
                return
        self.accept()

    def values(self) -> dict:
        seq_text = self._bias_seq.text().strip()
        bias_sequence: list[float] = []
        if seq_text:
            bias_sequence = [float(x.strip()) for x in seq_text.split(",") if x.strip()]
        return {
            "size_nm": float(self._size.value()),
            "pixels": int(self._pixels.currentData() or 256),
            "target_minutes": self._time_preset.currentData(),
            "bias_V": float(self._bias.value()),
            "setpoint_nA": float(self._setpoint.value()),
            "repetitions": int(self._repetitions.value()),
            "bias_sequence": bias_sequence,
            "bias_sequence_str": seq_text,
            "enable_centering": bool(self._center_check.isChecked()),
            "quick_pixels": int(self._quick_px_spin.value()),
        }


class _PreviewImageView(QWidget):
    """Single-surface image viewer with feature overlays."""

    feature_clicked = Signal(int)
    canvas_clicked = Signal(float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("previewImageView")
        self.setStyleSheet(f"background-color: {_theme.DARK_BG}; color: {_theme.DARK_TEXT};")
        self._title = QLabel("<b>Preview image</b>")
        self._title.setStyleSheet(
            f"color: {_theme.DARK_BLUE}; font-weight: 700; padding: 2px 4px;"
        )
        self._current_state: _PreviewState | None = None
        self._plot = pg.PlotWidget()
        self._plot.setBackground(_theme.DARK_BG)
        self._plot.setStyleSheet(
            f"background-color: {_theme.DARK_BG}; border: none; color: {_theme.DARK_TEXT};"
        )
        self._plot.setAspectLocked(True)
        self._plot.invertY(True)
        self._plot.showGrid(x=False, y=False)
        self._plot.hideAxis("bottom")
        self._plot.hideAxis("left")

        self._image = pg.ImageItem(axisOrder="row-major")
        self._plot.addItem(self._image)
        self._plot.scene().sigMouseClicked.connect(self._on_scene_clicked)
        self._image_shape: tuple[int, int] | None = None

        self._caption = QLabel("")
        self._caption.setWordWrap(True)
        self._caption.setStyleSheet(
            f"color: {_theme.DARK_TEXT_MUTED}; background-color: {_theme.DARK_SURFACE};"
            f" border-top: 1px solid {_theme.DARK_BORDER}; padding: 4px 6px;"
        )
        self._feature_rows: list[PreviewFeatureRow] = []
        self._box_items: list[QGraphicsRectItem] = []
        self._zero_items: list[QGraphicsEllipseItem] = []
        self._zero_labels: list[QGraphicsSimpleTextItem] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._title)
        layout.addWidget(self._plot, 1)
        layout.addWidget(self._caption)

    def set_state(
        self,
        state: _PreviewState | None,
        *,
        display_mode: str,
        overlay_rows: list[PreviewFeatureRow] | None = None,
        selected_rows: list[PreviewFeatureRow] | None = None,
        zero_points: list[tuple[int, int]] | None = None,
        reset_view: bool = False,
    ) -> None:
        if state is None:
            self._current_state = None
            self._image.clear()
            self._clear_boxes()
            self._clear_zero_points()
            self._feature_rows = []
            self._image_shape = None
            self._caption.setText("No preview loaded.")
            return

        self._current_state = state
        image, label = self._select_image(state, display_mode)
        arr = np.asarray(image, dtype=np.float64)
        if arr.ndim != 2:
            self._image.clear()
            self._image_shape = None
            self._caption.setText("Preview image is not 2-D.")
        else:
            self._image.setImage(arr, autoLevels=True)
            if reset_view or self._image_shape != arr.shape:
                self._plot.setRange(xRange=(0, arr.shape[1]), yRange=(0, arr.shape[0]), padding=0.02)
            self._image_shape = arr.shape
            self._caption.setText(label)

        self._title.setText(f"<b>{state.source_path.name}</b>")
        self._feature_rows = list(overlay_rows or state.feature_rows)
        self._draw_boxes(self._feature_rows, selected_rows or [])
        self._draw_zero_points(zero_points or [])

    def _clear_boxes(self) -> None:
        for item in self._box_items:
            try:
                self._plot.removeItem(item)
            except Exception:
                pass
        self._box_items.clear()

    def _clear_zero_points(self) -> None:
        for item in self._zero_items:
            try:
                self._plot.removeItem(item)
            except Exception:
                pass
        for item in self._zero_labels:
            try:
                self._plot.removeItem(item)
            except Exception:
                pass
        self._zero_items.clear()
        self._zero_labels.clear()

    def _draw_boxes(
        self,
        rows: list[PreviewFeatureRow],
        selected_rows: list[PreviewFeatureRow],
    ) -> None:
        self._clear_boxes()
        selected_ids = {row.index for row in selected_rows}
        for row in rows:
            bbox = row.bbox_px
            if bbox is None:
                x0 = float(row.x_px - 0.5)
                y0 = float(row.y_px - 0.5)
                w = h = 1.0
            else:
                x0 = float(bbox[0])
                y0 = float(bbox[1])
                w = max(1.0, float(bbox[2] - bbox[0]))
                h = max(1.0, float(bbox[3] - bbox[1]))
            rect = QGraphicsRectItem(x0, y0, w, h)
            class_color = self._class_color(row.label)
            if row.index in selected_ids:
                pen_color = "#f5c400"
                fill_color = pg.mkColor(class_color)
                fill_color.setAlpha(60)
                rect.setBrush(pg.mkBrush(fill_color))
            else:
                pen_color = class_color
                fill_color = pg.mkColor(class_color)
                fill_color.setAlpha(0)
                rect.setBrush(pg.mkBrush(fill_color))
            rect.setPen(pg.mkPen(pen_color, width=1.6))
            if row.index in selected_ids:
                rect.setPen(pg.mkPen(pen_color, width=3.2))
            rect.setZValue(10)
            self._plot.addItem(rect)
            self._box_items.append(rect)

    def _class_color(self, label: str) -> str:
        key = str(label).strip().lower()
        if not key:
            return "#2ecc71"
        if self._current_state is not None:
            color = self._current_state.class_colors.get(key)
            if color:
                return color
        return "#2ecc71"

    def _register_class_color(self, label: str) -> str:
        key = str(label).strip().lower()
        if not key or self._current_state is None:
            return "#2ecc71"
        color = self._current_state.class_colors.get(key)
        if color:
            return color
        palette = [
            "#3498db",  # blue
            "#e74c3c",  # red
            "#9b59b6",  # violet
            "#e67e22",  # orange
            "#1abc9c",  # teal
            "#f1c40f",  # yellow
            "#ff66cc",  # pink
            "#00bcd4",  # cyan
            "#8e44ad",  # purple
            "#2ecc71",  # green
        ]
        used = {str(value).lower() for value in self._current_state.class_colors.values()}
        for candidate in palette:
            if candidate.lower() not in used:
                self._current_state.class_colors[key] = candidate
                return candidate
        color = palette[len(self._current_state.class_colors) % len(palette)]
        self._current_state.class_colors[key] = color
        return color

    def _draw_zero_points(self, points: list[tuple[int, int]]) -> None:
        self._clear_zero_points()
        for idx, (x_px, y_px) in enumerate(points[:3], start=1):
            ell = QGraphicsEllipseItem(float(x_px) - 3.0, float(y_px) - 3.0, 6.0, 6.0)
            ell.setPen(pg.mkPen("#ffcc00", width=1.8))
            ell.setBrush(pg.mkBrush(0, 0, 0, 0))
            ell.setZValue(11)
            self._plot.addItem(ell)
            self._zero_items.append(ell)

            text = QGraphicsSimpleTextItem(str(idx))
            text.setBrush(pg.mkBrush("#ffcc00"))
            text.setPos(float(x_px) + 4.0, float(y_px) - 10.0)
            text.setZValue(12)
            self._plot.addItem(text)
            self._zero_labels.append(text)

    def _on_scene_clicked(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = self._plot.plotItem.vb.mapSceneToView(event.scenePos())
        x = float(pos.x())
        y = float(pos.y())
        self.canvas_clicked.emit(x, y)
        if not self._feature_rows:
            return
        nearest_idx: int | None = None
        nearest_dist2 = 49.0
        for row in self._feature_rows:
            dx = row.x_px - x
            dy = row.y_px - y
            dist2 = dx * dx + dy * dy
            if dist2 <= nearest_dist2:
                nearest_dist2 = dist2
                nearest_idx = row.index
        if nearest_idx is not None:
            self.feature_clicked.emit(nearest_idx)

    def _select_image(self, state: _PreviewState, display_mode: str) -> tuple[np.ndarray, str]:
        mode = (display_mode or "raw").strip().lower()
        if mode == "background_corrected" and state.corrected_plane is not None:
            return state.corrected_plane, "Background-corrected plane"
        if mode == "background_image" and state.background_image is not None:
            return state.background_image, "Background image"
        return state.raw_plane, "Raw plane"


class PreviewPanel(QWidget):
    """Preview tab that follows the latest scan and lets the operator drive analysis."""

    log_message = Signal(str)
    error_message = Signal(str)
    scan_completed = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._current_source: Path | None = None
        self._current_scan: Any | None = None
        self._current_state: _PreviewState | None = None
        self._load_worker: _ScanLoadWorker | None = None
        self._background_worker: _BackgroundWorker | None = None
        self._segmentation_worker: _SegmentationWorker | None = None
        self._classification_worker: _ClassificationWorker | None = None
        self._scan_worker: _FeatureScanWorker | None = None
        self._group_scan_worker: _FeatureGroupScanWorker | None = None
        self._group_scan_defaults: dict = {}
        self._feature_scan_defaults: dict = {}
        self._building_table = False
        self._class_records: dict[str, _ClassRecord] = {}
        self._class_widgets: dict[str, _ClassRowWidgets] = {}
        self._active_class_key = ""
        self._class_add_mode = False
        self._class_expanded_keys: set[str] = set()
        self._stage = "raw"
        self._zero_plane_points: list[tuple[int, int]] = []
        self._zero_plane_mode = False
        self._preview_selected_indices: set[int] = set()
        self._flatten_collapsed = False
        self._recent_sources: list[Path] = []
        self._recent_limit = 12
        self._analysis_timer = QTimer(self)
        self._analysis_timer.setSingleShot(True)
        self._analysis_timer.setInterval(40)
        self._analysis_timer.timeout.connect(self._refresh_live_segmentation)
        self._queue_armed = False
        # Position captured at segmentation-apply time; used as group-scan anchor
        self._preview_home_nm: tuple[float, float] | None = None
        self._initial_section: _StageSection | None = None
        self._source_section: _StageSection | None = None
        self._classification_section: _StageSection | None = None
        self._scan_section: _StageSection | None = None
        self._controls_scroll: QScrollArea | None = None
        self._class_list_content: QWidget | None = None
        self._class_list_layout: QVBoxLayout | None = None
        self._class_empty_label: QLabel | None = None
        self._class_action_btn: QPushButton | None = None
        # Periodic STM scan status poller (2 s)
        self._scan_info_timer = QTimer(self)
        self._scan_info_timer.setInterval(2000)
        self._scan_info_timer.timeout.connect(self._refresh_scan_info)
        self._scan_info_timer.start()
        self._scan_was_running: bool = False  # tracks transition to log scan-start once
        self._feat_scan_start_t: float | None = None
        self._feat_scan_total: int = 0
        self._feat_scans_saved: int = 0
        self._eta_timer = QTimer(self)
        self._eta_timer.setInterval(60_000)
        self._eta_timer.timeout.connect(self._refresh_eta)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        self.setObjectName("previewWindow")
        self.setStyleSheet(_preview_stylesheet())

        self._status = QLabel("Ready")
        self._status.setObjectName("previewStatus")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._scan_info_label = QLabel("")
        self._scan_info_label.setObjectName("previewScanInfo")
        self._scan_info_label.setWordWrap(True)
        self._scan_info_label.setVisible(False)
        root.addWidget(self._scan_info_label)

        self._time_label = QLabel("")
        self._time_label.setObjectName("previewTime")
        self._time_label.setVisible(False)
        root.addWidget(self._time_label)

        self._viewer = _PreviewImageView()
        self._viewer.feature_clicked.connect(self._toggle_feature_selection)
        self._viewer.canvas_clicked.connect(self._on_canvas_clicked)

        controls_panel = QWidget()
        controls_panel.setObjectName("previewControls")
        controls = QVBoxLayout(controls_panel)
        controls.setContentsMargins(6, 6, 6, 6)
        controls.setSpacing(6)

        self._refresh_btn = QPushButton("Refresh latest")
        self._refresh_btn.clicked.connect(self.refresh_latest)
        self._refresh_btn.setToolTip("Reload the newest .dat file in the selected folder.")
        self._refresh_btn.setProperty("role", "primary")
        self._refresh_btn.setMinimumHeight(28)
        self._refresh_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._reset_btn = QPushButton("Reset analysis")
        self._reset_btn.clicked.connect(self._reset_analysis)
        self._reset_btn.setToolTip("Clear flattening, segmentation, and class state back to the raw image.")
        self._reset_btn.setProperty("role", "primary")

        self._background_btn = QPushButton("Auto flatten")
        self._background_btn.clicked.connect(self._apply_background)
        self._background_btn.setToolTip("Flatten the current plane using ProbeFlow background correction.")
        self._background_btn.setProperty("role", "primary")

        self._zero_plane_btn = QPushButton("3-point flatten")
        self._zero_plane_btn.setCheckable(True)
        self._zero_plane_btn.toggled.connect(self._toggle_zero_plane_mode)
        self._zero_plane_btn.setToolTip("Click 3 points on the image to subtract the plane they define.")
        self._zero_plane_btn.setProperty("role", "primary")

        self._features_btn = QPushButton("Apply settings")
        self._features_btn.clicked.connect(self._apply_segmentation_settings)
        self._features_btn.setToolTip("Freeze the live segmentation into the feature table.")
        self._features_btn.setProperty("role", "accent")
        self._features_btn.setMinimumHeight(32)

        self._label_btn = QPushButton("Label selected")
        self._label_btn.clicked.connect(self._label_selected_samples)
        self._label_btn.setToolTip("Assign the current class name to selected particles.")
        self._label_btn.setProperty("role", "primary")

        self._classify_btn = QPushButton("Classify")
        self._classify_btn.clicked.connect(self._classify_particles)
        self._classify_btn.setToolTip("Run ProbeFlow-backed classification on the labeled samples.")
        self._classify_btn.setProperty("role", "accent")
        self._classify_btn.setMinimumHeight(32)

        self._scan_selected_btn = QPushButton("Queue selected")
        self._scan_selected_btn.clicked.connect(self._scan_selected_features)
        self._scan_selected_btn.setToolTip("Queue the checked molecules for follow-up scans.")
        self._scan_selected_btn.setProperty("role", "primary")
        self._scan_groups_btn = QPushButton("Scan as Groups")
        self._scan_groups_btn.clicked.connect(self._scan_selected_as_groups)
        self._scan_groups_btn.setToolTip(
            "Group checked features spatially and scan each group in one frame."
        )
        self._scan_groups_btn.setProperty("role", "primary")
        self._stop_scan_btn = QPushButton("Stop scan")
        self._stop_scan_btn.clicked.connect(self._stop_active_scan)
        self._stop_scan_btn.setToolTip("Request graceful stop after the current feature/group finishes.")
        self._stop_scan_btn.setProperty("role", "danger")
        self._queue_class_btn = QPushButton("Queue class")
        self._queue_class_btn.clicked.connect(self._queue_class_from_active)
        self._queue_class_btn.setToolTip("Queue a follow-up scan for the active class.")
        self._queue_class_btn.setProperty("role", "primary")
        self._queue_class_btn.setMinimumHeight(28)
        self._background_btn.setMinimumHeight(28)
        self._zero_plane_btn.setMinimumHeight(28)
        self._reset_btn.setMinimumHeight(28)
        self._scan_selected_btn.setMinimumHeight(28)
        self._scan_groups_btn.setMinimumHeight(28)
        self._stop_scan_btn.setMinimumHeight(28)
        self._background_btn.setEnabled(False)
        self._features_btn.setEnabled(False)
        self._label_btn.setEnabled(False)
        self._classify_btn.setEnabled(False)
        self._scan_selected_btn.setEnabled(False)
        self._scan_groups_btn.setEnabled(False)
        self._stop_scan_btn.setEnabled(False)
        self._queue_class_btn.setEnabled(False)
        self._reset_btn.setEnabled(False)
        self._source_section = _StageSection("Source", expanded=True)
        source_layout = QVBoxLayout(self._source_section.content())
        source_layout.setContentsMargins(6, 6, 6, 6)
        source_layout.setSpacing(5)

        source_form = QFormLayout()
        source_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        source_form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        source_form.setHorizontalSpacing(10)
        source_form.setVerticalSpacing(4)
        source_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        source_layout.addLayout(source_form)

        self._source_path_edit = QLineEdit()
        self._source_path_edit.setReadOnly(True)
        self._source_path_edit.setPlaceholderText("Latest loaded .dat will appear here")
        self._style_control(self._source_path_edit)
        source_form.addRow("Loaded file", self._source_path_edit)

        self._recent_list = QListWidget()
        self._recent_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._recent_list.setMinimumHeight(42)
        self._recent_list.setMaximumHeight(62)
        self._recent_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._recent_list.itemDoubleClicked.connect(self._open_recent_item)
        self._recent_label = QLabel("Recent scans")
        source_form.addRow(self._recent_label, self._recent_list)
        self._recent_empty_label = QLabel("No recent scans yet.")
        self._recent_empty_label.setStyleSheet(
            f"color: {_theme.DARK_TEXT_MUTED}; font-style: italic; padding: 0 2px 4px 2px;"
        )
        source_layout.addWidget(self._recent_empty_label)

        self._folder_edit = QLineEdit()
        self._folder_edit.setPlaceholderText("Folder containing completed .dat scans")
        self._style_control(self._folder_edit)
        source_form.addRow("Scan folder", self._folder_edit)

        source_buttons = QHBoxLayout()
        source_buttons.setContentsMargins(0, 0, 0, 0)
        source_buttons.setSpacing(6)

        browse_btn = QPushButton("Browse folder")
        browse_btn.clicked.connect(self._pick_folder)
        browse_btn.setProperty("role", "primary")
        browse_btn.setMinimumHeight(28)
        browse_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        source_buttons.addWidget(browse_btn)

        open_btn = QPushButton("Open scan")
        open_btn.clicked.connect(self._pick_scan_file)
        open_btn.setProperty("role", "primary")
        open_btn.setMinimumHeight(28)
        open_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        source_buttons.addWidget(open_btn)

        source_buttons.addWidget(self._refresh_btn)
        source_layout.addLayout(source_buttons)
        controls.addWidget(self._source_section)

        self._initial_section = _StageSection("Initial Settings", expanded=True)
        initial_layout = QVBoxLayout(self._initial_section.content())
        initial_layout.setContentsMargins(6, 6, 6, 6)
        initial_layout.setSpacing(5)

        view_title = self._section_label("View")
        initial_layout.addWidget(view_title)
        view_form = QFormLayout()
        view_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        view_form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        view_form.setHorizontalSpacing(10)
        view_form.setVerticalSpacing(4)
        view_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        initial_layout.addLayout(view_form)

        self._plane_spin = QSpinBox()
        self._plane_spin.setRange(0, 0)
        self._plane_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._plane_spin.valueChanged.connect(self._plane_changed)
        self._style_control(self._plane_spin)
        view_form.addRow("Plane", self._plane_spin)

        self._display_mode = _WheelGuardComboBox()
        self._display_mode.addItem("Raw", "raw")
        self._display_mode.addItem("Background-corrected", "background_corrected")
        self._display_mode.addItem("Background image", "background_image")
        self._display_mode.currentIndexChanged.connect(self._display_mode_changed)
        self._style_control(self._display_mode)
        view_form.addRow("View mode", self._display_mode)

        self._flatten_section = QWidget()
        flatten_container = QVBoxLayout(self._flatten_section)
        flatten_container.setContentsMargins(0, 0, 0, 0)
        flatten_container.setSpacing(4)
        flatten_container.addWidget(self._section_label("Flatten"))
        flat_form = QFormLayout()
        flat_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        flat_form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        flat_form.setHorizontalSpacing(10)
        flat_form.setVerticalSpacing(4)
        flat_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        flatten_container.addLayout(flat_form)

        self._background_mode = _WheelGuardComboBox()
        self._background_mode.addItems(["linear", "poly2", "poly3", "low_pass"])
        self._style_control(self._background_mode)
        flat_form.addRow("Background", self._background_mode)

        self._background_strength = QDoubleSpinBox()
        self._background_strength.setRange(0.5, 50.0)
        self._background_strength.setDecimals(1)
        self._background_strength.setValue(5.0)
        self._background_strength.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._style_control(self._background_strength)
        flat_form.addRow("Blur", self._background_strength)

        self._zero_status = QLabel("Zero-plane mode: off")
        self._zero_status.setWordWrap(True)
        flat_form.addRow("Status", self._zero_status)

        flatten_buttons = QHBoxLayout()
        flatten_buttons.setContentsMargins(0, 0, 0, 0)
        flatten_buttons.setSpacing(6)
        flatten_buttons.addWidget(self._background_btn)
        flatten_buttons.addWidget(self._zero_plane_btn)
        flatten_buttons.addWidget(self._reset_btn)
        flatten_buttons.addStretch(1)
        flatten_container.addLayout(flatten_buttons)
        initial_layout.addWidget(self._flatten_section)

        self._segmentation_section = QWidget()
        seg_container = QVBoxLayout(self._segmentation_section)
        seg_container.setContentsMargins(0, 0, 0, 0)
        seg_container.setSpacing(4)
        seg_container.addWidget(self._section_label("Segmentation"))
        seg_form = QFormLayout()
        seg_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        seg_form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        seg_form.setHorizontalSpacing(10)
        seg_form.setVerticalSpacing(4)
        seg_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        seg_container.addLayout(seg_form)

        self._feature_mode = _WheelGuardComboBox()
        self._feature_mode.addItems([
            "segmentation_first",
            "segmentation_only",
            "points_only",
        ])
        self._style_control(self._feature_mode)
        seg_form.addRow("Feature mode", self._feature_mode)

        self._threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self._threshold_slider.setRange(0, 255)
        self._threshold_slider.setValue(97)
        self._threshold_value = QLabel("97")
        self._threshold_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._connect_slider_value(self._threshold_slider, self._threshold_value, self._threshold_slider.value())
        seg_form.addRow("Threshold", self._slider_row(self._threshold_slider, self._threshold_value))

        self._invert_features = QCheckBox("Invert (dark features)")
        seg_form.addRow("", self._invert_features)

        self._min_area_slider = QSlider(Qt.Orientation.Horizontal)
        self._min_area_slider.setRange(0, 1000)
        self._min_area_slider.setValue(2)
        self._min_area_value = QLabel("0.002")
        self._min_area_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._connect_slider_value(self._min_area_slider, self._min_area_value, self._min_area_slider.value(), percent=True)
        seg_form.addRow("Min area (%)", self._slider_row(self._min_area_slider, self._min_area_value))

        self._max_area_slider = QSlider(Qt.Orientation.Horizontal)
        self._max_area_slider.setRange(0, 1000)
        self._max_area_slider.setValue(1000)
        self._max_area_value = QLabel("1.000")
        self._max_area_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._connect_slider_value(self._max_area_slider, self._max_area_value, self._max_area_slider.value(), percent=True)
        seg_form.addRow("Max area (%)", self._slider_row(self._max_area_slider, self._max_area_value))

        self._sigma_clip = QDoubleSpinBox()
        self._sigma_clip.setRange(0.0, 10.0)
        self._sigma_clip.setDecimals(1)
        self._sigma_clip.setValue(2.0)
        self._sigma_clip.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._style_control(self._sigma_clip)
        seg_form.addRow("sigma-clip", self._sigma_clip)

        seg_container.addWidget(self._features_btn)
        initial_layout.addWidget(self._segmentation_section)

        controls.addWidget(self._initial_section)

        self._classification_section = _StageSection("Classification", expanded=False)
        class_container = QVBoxLayout(self._classification_section.content())
        class_container.setContentsMargins(6, 6, 6, 6)
        class_container.setSpacing(4)

        class_top = QHBoxLayout()
        class_top.setContentsMargins(0, 0, 0, 0)
        class_top.setSpacing(6)
        class_top.addWidget(self._label_btn)
        self._add_class_btn = QPushButton("Add class")
        self._add_class_btn.clicked.connect(self._add_class)
        self._add_class_btn.setProperty("role", "primary")
        self._add_class_btn.setMinimumHeight(28)
        self._add_class_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        class_top.addWidget(self._add_class_btn)
        class_container.addLayout(class_top)

        self._target_class_label = QLabel("Target class: none")
        self._target_class_label.setObjectName("previewClassStatus")
        self._target_class_label.setWordWrap(True)
        class_container.addWidget(self._target_class_label)

        self._sample_status = QLabel("No Feature-classes labelled yet.")
        self._sample_status.setObjectName("previewClassStatus")
        self._sample_status.setWordWrap(True)
        class_container.addWidget(self._sample_status)

        self._class_empty_label = QLabel("No Feature-classes labelled")
        self._class_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._class_empty_label.setStyleSheet(
            f"color: {_theme.DARK_TEXT_MUTED}; font-style: italic; padding: 6px 8px;"
        )
        class_container.addWidget(self._class_empty_label)

        self._class_list_content = QWidget()
        self._class_list_content.setObjectName("previewClassListContent")
        self._class_list_layout = QVBoxLayout(self._class_list_content)
        self._class_list_layout.setContentsMargins(0, 0, 0, 0)
        self._class_list_layout.setSpacing(2)
        class_container.addWidget(self._class_list_content)

        class_container.addWidget(self._classify_btn)
        controls.addWidget(self._classification_section)

        self._scan_section = _StageSection("Scan", expanded=False)
        scan_container = QVBoxLayout(self._scan_section.content())
        scan_container.setContentsMargins(6, 6, 6, 6)
        scan_container.setSpacing(5)
        scan_hub = QWidget()
        scan_hub.setObjectName("previewScanHub")
        scan_hub_layout = QVBoxLayout(scan_hub)
        scan_hub_layout.setContentsMargins(6, 6, 6, 6)
        scan_hub_layout.setSpacing(5)
        scan_row_a = QHBoxLayout()
        scan_row_a.setContentsMargins(0, 0, 0, 0)
        scan_row_a.setSpacing(6)
        scan_row_a.addWidget(self._scan_selected_btn)
        scan_row_a.addWidget(self._queue_class_btn)
        scan_hub_layout.addLayout(scan_row_a)
        scan_row_b = QHBoxLayout()
        scan_row_b.setContentsMargins(0, 0, 0, 0)
        scan_row_b.setSpacing(6)
        scan_row_b.addWidget(self._scan_groups_btn)
        scan_row_b.addWidget(self._stop_scan_btn)
        scan_hub_layout.addLayout(scan_row_b)
        scan_container.addWidget(scan_hub)
        controls.addWidget(self._scan_section)

        self._feature_mode.currentIndexChanged.connect(lambda *_: self._schedule_live_segmentation())
        self._invert_features.toggled.connect(lambda *_: self._schedule_live_segmentation())
        self._threshold_slider.valueChanged.connect(lambda value: self._on_segmentation_slider_changed(value))
        self._min_area_slider.valueChanged.connect(lambda value: self._on_segmentation_slider_changed(value))
        self._max_area_slider.valueChanged.connect(lambda value: self._on_segmentation_slider_changed(value))
        self._sigma_clip.valueChanged.connect(lambda *_: self._schedule_live_segmentation())
        self._classification_section.setExpanded(False)
        self._scan_section.setExpanded(False)

        controls.addStretch(1)

        controls_widget = QScrollArea()
        controls_widget.setWidgetResizable(True)
        controls_widget.setFrameShape(QFrame.Shape.NoFrame)
        controls_widget.setWidget(controls_panel)
        controls_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        controls_widget.setObjectName("previewControlsScroll")
        controls_widget.setMinimumWidth(410)
        controls_widget.setMaximumWidth(540)
        controls_widget.setStyleSheet("background: transparent; border: none;")
        controls_widget.viewport().setStyleSheet("background: transparent;")
        self._controls_scroll = controls_widget

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.addWidget(self._viewer)
        body.addWidget(controls_widget)
        body.setStretchFactor(0, 5)
        body.setStretchFactor(1, 2)
        body.setSizes([1180, 340])
        table_box = QWidget()
        table_box.setObjectName("previewTableBox")
        table_layout = QVBoxLayout(table_box)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_title = QLabel("Detected features")
        table_title.setObjectName("previewSection")
        table_layout.addWidget(table_title)
        self._feature_table = QTableWidget(0, 10)
        self._feature_table.setObjectName("previewFeatureTable")
        self._feature_table.setHorizontalHeaderLabels([
            "Use",
            "#",
            "Source",
            "Class",
            "x (nm)",
            "y (nm)",
            "dx (nm)",
            "dy (nm)",
            "Area (nm^2)",
            "Score",
        ])
        self._feature_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._feature_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._feature_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._feature_table.verticalHeader().setVisible(False)
        self._feature_table.setMinimumHeight(110)
        self._feature_table.itemChanged.connect(self._on_feature_table_item_changed)
        table_layout.addWidget(self._feature_table)

        table_buttons = QHBoxLayout()
        table_buttons.addStretch(1)
        select_all_btn = QPushButton("Select all")
        select_all_btn.setProperty("role", "primary")
        select_all_btn.clicked.connect(lambda: self._set_all_selected(True))
        table_buttons.addWidget(select_all_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.setProperty("role", "primary")
        clear_btn.clicked.connect(lambda: self._set_all_selected(False))
        table_buttons.addWidget(clear_btn)
        table_layout.addLayout(table_buttons)
        table_box.setMinimumHeight(90)

        main_splitter = QSplitter(Qt.Orientation.Vertical)
        main_splitter.setChildrenCollapsible(True)
        main_splitter.addWidget(body)
        main_splitter.addWidget(table_box)
        main_splitter.setStretchFactor(0, 4)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([760, 240])
        root.addWidget(main_splitter, 1)

        self._apply_preview_button_styles()
        self._refresh_recent_list()
        self._set_stage("raw")

    # ------------------------------------------------------------------
    # Public API for main window / runner handoff
    # ------------------------------------------------------------------

    def refresh_latest(self) -> None:
        folder = self._folder()
        if folder is None:
            self._show_status("No scan folder selected.")
            return
        latest = _latest_dat_in_folder(folder)
        if latest is None:
            self._show_status(f"No .dat files found in {folder}")
            return
        self.open_scan(latest)

    def handle_scan_completed(self, dat_path: str) -> None:
        """Refresh the preview after any external scan completes."""
        self.open_scan(Path(dat_path))

    def open_scan(self, source: str | Path) -> None:
        path = Path(source)
        self._current_source = path
        self._folder_edit.setText(str(path.parent))
        self._load_scan(path)

    # ------------------------------------------------------------------
    # Scan loading and analysis
    # ------------------------------------------------------------------

    def _load_scan(self, source: Path) -> None:
        if self._load_worker is not None and self._load_worker.isRunning():
            self._load_worker.requestInterruption()
            self._load_worker.wait(500)
        self._show_status(f"Loading {source.name}...")
        self._load_worker = _ScanLoadWorker(source)
        self._load_worker.result_ready.connect(lambda scan: self._on_scan_loaded(source, scan))
        self._load_worker.failed.connect(self._on_load_failed)
        self._load_worker.start()

    def _on_scan_loaded(self, source: Path, scan: Any) -> None:
        self._current_scan = scan
        self._current_source = source
        self._remember_recent_source(source)
        self._folder_edit.setText(str(source.parent))
        self._source_path_edit.setText(str(source))
        self._zero_plane_points = []
        self._zero_plane_mode = False
        self._preview_selected_indices.clear()
        self._zero_plane_btn.blockSignals(True)
        try:
            self._zero_plane_btn.setChecked(False)
        finally:
            self._zero_plane_btn.blockSignals(False)
        self._zero_status.setText("Zero-plane mode: off")

        plane_count = len(getattr(scan, "planes", []))
        self._plane_spin.blockSignals(True)
        try:
            self._plane_spin.setMaximum(max(0, plane_count - 1))
            self._plane_spin.setValue(min(int(self._plane_spin.value()), max(0, plane_count - 1)))
        finally:
            self._plane_spin.blockSignals(False)

        self._set_plane_state(self._plane_spin.value(), clear_analysis=True)
        self._set_display_mode("raw")
        self._set_stage("raw")
        self._background_btn.setEnabled(True)
        self._sample_status.setText("No Feature-classes labelled yet.")
        self._show_status(f"{source.name}: raw plane loaded")
        self.log_message.emit(f"Preview raw scan loaded: {source.name}")

    def _on_load_failed(self, message: str) -> None:
        self._show_status(f"Preview load failed: {message}")
        self.error_message.emit(message)

    def _analysis_params(self) -> PreviewAnalysisParams:
        return PreviewAnalysisParams(
            plane_index=int(self._plane_spin.value()),
            background_mode=str(self._background_mode.currentText()),
            background_strength=float(self._background_strength.value()),
            feature_mode=str(self._feature_mode.currentText()),
            threshold="manual",
            manual_threshold=float(self._threshold_slider.value()),
            invert=bool(self._invert_features.isChecked()),
            min_area_nm2=0.0,
            max_area_nm2=None,
            size_sigma_clip=None if self._sigma_clip.value() <= 0 else float(self._sigma_clip.value()),
        )

    def _current_analysis_plane(self) -> np.ndarray:
        if self._current_state is None:
            raise RuntimeError("load a scan before analysing")
        return self._current_state.corrected_plane if self._current_state.corrected_plane is not None else self._current_state.raw_plane

    def _show_raw_plane(self) -> None:
        if self._current_state is None:
            QMessageBox.information(self, "No scan", "Load a scan before switching to the raw plane.")
            return
        self._flatten_collapsed = False
        self._flatten_section.setVisible(True)
        self._set_display_mode("raw")
        self._render_current_state()
        self._show_status(f"{self._current_state.source_path.name}: raw plane")

    def _reset_analysis(self) -> None:
        if self._current_scan is None:
            QMessageBox.information(self, "No scan", "Load a scan before resetting analysis.")
            return
        self._preview_selected_indices.clear()
        self._zero_plane_points = []
        self._zero_plane_mode = False
        self._zero_plane_btn.blockSignals(True)
        try:
            self._zero_plane_btn.setChecked(False)
        finally:
            self._zero_plane_btn.blockSignals(False)
        self._flatten_collapsed = True
        self._set_plane_state(self._plane_spin.value(), clear_analysis=True)
        self._set_display_mode("raw")
        self._set_stage("raw")
        self._refresh_class_selector()
        self._source_path_edit.setText(str(self._current_scan.source_path))
        self._sample_status.setText("No Feature-classes labelled yet.")
        self._show_status(f"{Path(self._current_scan.source_path).name}: analysis reset to raw")
        self.log_message.emit(f"Analysis reset for {Path(self._current_scan.source_path).name}")

    def _apply_background(self) -> None:
        if self._current_state is None:
            QMessageBox.information(self, "No scan", "Load a scan before flattening.")
            return
        if self._background_worker is not None and self._background_worker.isRunning():
            QMessageBox.information(self, "Busy", "Flattening is already running.")
            return
        self._flatten_collapsed = False
        self._flatten_section.setVisible(True)

        params = self._analysis_params()
        self._show_status(f"Flattening {self._current_source.name}...")
        self._background_worker = _BackgroundWorker(self._current_state.raw_plane, params)
        self._background_worker.result_ready.connect(self._on_background_ready)
        self._background_worker.failed.connect(self._on_background_failed)
        self._background_worker.start()

    def _toggle_zero_plane_mode(self, checked: bool) -> None:
        self._zero_plane_mode = bool(checked)
        if checked:
            self._flatten_collapsed = False
            self._flatten_section.setVisible(True)
            self._zero_plane_points = []
            self._zero_status.setText("Zero-plane mode: click 3 points on the image.")
            self._show_status("Zero-plane mode: click 3 points on the image.")
        else:
            self._zero_status.setText("Zero-plane mode: off")
            self._show_status("Zero-plane mode off.")
        self._render_current_state()

    def _on_canvas_clicked(self, x_px: float, y_px: float) -> None:
        if not self._zero_plane_mode or self._current_state is None:
            return
        plane = self._current_state.corrected_plane if self._current_state.corrected_plane is not None else self._current_state.raw_plane
        if plane is None:
            return
        ny, nx = plane.shape
        px = max(0, min(int(round(x_px)), nx - 1))
        py = max(0, min(int(round(y_px)), ny - 1))
        self._zero_plane_points.append((px, py))
        self._viewer.set_state(
            self._current_state,
            display_mode=self._current_display_mode(),
            overlay_rows=list(self._current_state.feature_rows or self._current_state.preview_rows),
            selected_rows=self._highlighted_feature_rows(list(self._current_state.feature_rows or self._current_state.preview_rows)),
            zero_points=self._zero_plane_points,
        )
        if len(self._zero_plane_points) < 3:
            self._zero_status.setText(
                f"Zero-plane point {len(self._zero_plane_points)}/3 set at ({px}, {py}); click {3 - len(self._zero_plane_points)} more."
            )
            self._show_status(self._zero_status.text())
            return
        self._apply_zero_plane_points()

    def _apply_zero_plane_points(self) -> None:
        if self._current_state is None:
            return
        plane = self._current_state.corrected_plane if self._current_state.corrected_plane is not None else self._current_state.raw_plane
        if plane is None:
            return
        try:
            flattened = set_zero_plane(np.asarray(plane, dtype=np.float64), self._zero_plane_points[:3], patch=1)
        except Exception as exc:
            self._show_status(f"Zero-plane flatten failed: {exc}")
            self.error_message.emit(str(exc))
            return
        self._current_state.corrected_plane = np.asarray(flattened, dtype=np.float64)
        self._current_state.background_image = np.asarray(plane - flattened, dtype=np.float64)
        self._current_state.preview_rows = ()
        self._current_state.preview_particles = ()
        self._current_state.feature_rows = ()
        self._current_state.particles = ()
        self._current_state.sample_labels.clear()
        self._current_state.classifications.clear()
        self._current_state.class_colors.clear()
        self._class_records.clear()
        self._class_widgets.clear()
        self._active_class_key = ""
        self._class_add_mode = False
        self._class_expanded_keys.clear()
        self._clear_feature_table()
        self._preview_selected_indices.clear()
        self._refresh_class_tree()
        self._set_stage("segmentation")
        self._set_display_mode("background_corrected")
        self._seed_segmentation_controls_from_plane(self._current_state.corrected_plane)
        self._render_current_state()
        self._zero_plane_btn.blockSignals(True)
        try:
            self._zero_plane_btn.setChecked(False)
        finally:
            self._zero_plane_btn.blockSignals(False)
        self._zero_plane_mode = False
        self._zero_status.setText("Zero-plane flatten applied from 3 points.")
        self._show_status("Zero-plane flatten applied from 3 points.")
        self.log_message.emit(f"Zero-plane flatten applied to {self._current_source.name}")
        self._refresh_live_segmentation()

    def _on_background_ready(self, result: object) -> None:
        corrected, background_image = result
        if self._current_state is None:
            return
        self._current_state.corrected_plane = np.asarray(corrected, dtype=np.float64)
        self._current_state.background_image = np.asarray(background_image, dtype=np.float64)
        self._current_state.preview_rows = ()
        self._current_state.preview_particles = ()
        self._current_state.feature_rows = ()
        self._current_state.particles = ()
        self._current_state.sample_labels.clear()
        self._current_state.classifications.clear()
        self._current_state.class_colors.clear()
        self._class_records.clear()
        self._class_widgets.clear()
        self._active_class_key = ""
        self._class_add_mode = False
        self._class_expanded_keys.clear()
        self._clear_feature_table()
        self._sample_status.setText("No Feature-classes labelled yet.")
        self._zero_plane_points = []
        self._preview_selected_indices.clear()
        self._refresh_class_tree()
        self._set_stage("segmentation")
        self._set_display_mode("background_corrected")
        self._seed_segmentation_controls_from_plane(self._current_state.corrected_plane)
        self._render_current_state()
        source_name = self._current_state.source_path.name
        self._show_status(f"{source_name}: flattened")
        self.log_message.emit(f"Flatten applied to {source_name}")
        self._refresh_live_segmentation()

    def _on_background_failed(self, message: str) -> None:
        self._show_status(f"Flattening failed: {message}")
        self.error_message.emit(message)

    def _area_px_to_slider(self, area_px: float, shape: tuple[int, int]) -> int:
        total_area = float(max(int(shape[0]), 1) * max(int(shape[1]), 1))
        if total_area <= 0:
            return 0
        return int(round((float(area_px) / total_area) * 100000.0))

    def _seed_segmentation_controls_from_plane(self, plane: np.ndarray | None) -> None:
        if plane is None:
            return
        arr = np.asarray(plane, dtype=np.float64)
        if arr.ndim != 2 or arr.size == 0:
            return
        try:
            cv2 = cv2_module("preview segmentation")
            u8 = to_uint8_for_cv(arr, clip_low=1.0, clip_high=99.0)
            otsu_value, otsu_thresh = cv2.threshold(
                u8,
                0,
                255,
                cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
            )
            contours, _ = cv2.findContours(
                np.asarray(otsu_thresh, dtype=np.uint8),
                cv2.RETR_TREE,
                cv2.CHAIN_APPROX_SIMPLE,
            )
        except Exception:
            return
        areas = [float(cv2.contourArea(contour)) for contour in contours if contour is not None]
        total_area = float(arr.shape[0] * arr.shape[1])
        min_area_px = 10.0
        max_area_px = max(total_area * 0.01, 1.0)
        if areas:
            avg_area = float(np.mean(areas))
            std_area = float(np.std(areas))
            size_factor = 2.0
            min_area_px = max(avg_area - size_factor * std_area, 10.0)
            max_area_px = avg_area + size_factor * std_area
        threshold_value = int(round(float(otsu_value)))
        min_slider = min(self._area_px_to_slider(min_area_px, arr.shape), 1000)
        max_slider = min(self._area_px_to_slider(max_area_px, arr.shape), 1000)
        self._threshold_slider.blockSignals(True)
        self._min_area_slider.blockSignals(True)
        self._max_area_slider.blockSignals(True)
        try:
            self._threshold_slider.setValue(max(0, min(255, threshold_value)))
            self._min_area_slider.setValue(max(0, min(1000, min_slider)))
            self._max_area_slider.setValue(max(0, min(1000, max_slider)))
        finally:
            self._threshold_slider.blockSignals(False)
            self._min_area_slider.blockSignals(False)
            self._max_area_slider.blockSignals(False)
        self._update_slider_label(self._threshold_value, self._threshold_slider.value())
        self._update_slider_label(self._min_area_value, self._min_area_slider.value(), percent=True)
        self._update_slider_label(self._max_area_value, self._max_area_slider.value(), percent=True)

    def _schedule_live_segmentation(self) -> None:
        if self._current_state is None or self._current_state.corrected_plane is None:
            return
        self._analysis_timer.start()

    def _refresh_live_segmentation(self) -> None:
        if self._current_state is None:
            return
        plane = self._current_state.corrected_plane
        if plane is None:
            self._show_status("Flatten the scan before segmenting features.")
            return
        if self._segmentation_worker is not None and self._segmentation_worker.isRunning():
            return

        self._show_status(f"Updating segmentation preview for {self._current_source.name}...")
        self._segmentation_worker = _SegmentationWorker(
            plane,
            self._current_scan.scan_range_m,
            threshold_value=int(self._threshold_slider.value()),
            min_area_slider=int(self._min_area_slider.value()),
            max_area_slider=int(self._max_area_slider.value()),
            invert=bool(self._invert_features.isChecked()),
            feature_mode=str(self._feature_mode.currentText()),
        )
        self._segmentation_worker.result_ready.connect(self._on_segmentation_ready)
        self._segmentation_worker.failed.connect(self._on_segmentation_failed)
        self._segmentation_worker.start()

    def _detect_features(self) -> None:
        """Compatibility hook for tests and older preview flows.

        The new UX keeps detection user-driven. This helper performs one
        explicit segmentation pass and immediately applies the preview rows
        into the table so older test paths still work.
        """
        if self._current_state is None:
            QMessageBox.information(self, "No scan", "Load and flatten a scan before detecting features.")
            return
        plane = self._current_state.corrected_plane
        if plane is None:
            self._refresh_live_segmentation()
            QMessageBox.information(self, "No preview", "Flatten the scan before detecting features.")
            return
        try:
            particles, rows, warnings = _generate_unimr_segmentation_preview(
                np.asarray(plane, dtype=np.float64),
                self._current_scan.scan_range_m,
                threshold_value=int(self._threshold_slider.value()),
                min_area_slider=int(self._min_area_slider.value()),
                max_area_slider=int(self._max_area_slider.value()),
                invert=bool(self._invert_features.isChecked()),
                feature_mode=str(self._feature_mode.currentText()),
            )
            self._on_segmentation_ready((particles, rows, warnings))
        except Exception as exc:  # pragma: no cover - exercised via panel tests
            log.exception("Preview feature detection failed")
            self._on_segmentation_failed(str(exc))

    def _on_segmentation_ready(self, result: object) -> None:
        particles, rows, warnings = result
        if self._current_state is None:
            return
        self._current_state.preview_particles = tuple(particles)
        self._current_state.preview_rows = tuple(rows)
        self._current_state.warnings = tuple(warnings)
        self._preview_selected_indices = self._selected_feature_indices_from_table()
        self._set_stage("segmentation")
        self._set_display_mode("background_corrected")
        self._populate_feature_table(
            self._current_state.preview_rows,
            selected_indices=self._preview_selected_indices,
        )
        self._render_current_state()
        self._features_btn.setVisible(True)
        self._features_btn.setEnabled(True)
        self._show_status(
            f"{self._current_source.name}: live segmentation found {len(rows)} feature(s)"
        )
        self.log_message.emit(
            f"Segmentation preview updated for {self._current_source.name} "
            f"({len(rows)} candidate(s))"
        )

    def _on_segmentation_failed(self, message: str) -> None:
        self._show_status(f"Segmentation preview failed: {message}")
        self.error_message.emit(message)

    def _apply_segmentation_settings(self) -> None:
        if self._current_state is None:
            QMessageBox.information(self, "No scan", "Load and flatten a scan before applying segmentation settings.")
            return
        if not self._current_state.preview_rows:
            self._refresh_live_segmentation()
            QMessageBox.information(self, "No preview", "Wait for the live segmentation preview to finish.")
            return
        selected_indices = self._selected_feature_indices_from_table()
        self._current_state.feature_rows = self._current_state.preview_rows
        self._current_state.particles = self._current_state.preview_particles
        self._current_state.sample_labels.clear()
        self._current_state.classifications.clear()
        self._current_state.class_colors.clear()
        self._class_records.clear()
        self._class_widgets.clear()
        self._active_class_key = ""
        self._class_add_mode = False
        self._class_expanded_keys.clear()
        self._preview_selected_indices = set(selected_indices)
        self._populate_feature_table(
            self._current_state.feature_rows,
            selected_indices=selected_indices,
        )
        self._refresh_class_selector()
        self._render_current_state()
        self._set_stage("classification")
        self._render_current_state()
        self._sample_status.setText("No Feature-classes labelled yet.")

        # Capture tip position as the anchor reference for all follow-up group scans.
        # Reading here (not at scan-start time) ensures groups stay within the wide image
        # even if there is a delay between segmentation and launching the scan worker.
        self._preview_home_nm = None
        try:
            if self._stm.connected:
                self._preview_home_nm = self._stm.scan.get_offset_nm()
        except Exception:
            pass
        home_str = (
            f"  |  home ({self._preview_home_nm[0]:+.2f}, {self._preview_home_nm[1]:+.2f}) nm"
            if self._preview_home_nm else ""
        )

        self._show_status(
            f"{self._current_source.name}: applied {len(self._current_state.feature_rows)} feature(s){home_str}"
        )
        self.log_message.emit(
            f"Segmentation settings applied for {self._current_source.name} "
            f"({len(self._current_state.feature_rows)} feature(s)){home_str}"
        )

    def _label_selected_samples(self) -> None:
        if self._current_state is None or not self._current_state.feature_rows:
            QMessageBox.information(self, "No features", "Apply segmentation settings before labeling samples.")
            return
        selected = self._selected_feature_rows()
        if not selected:
            QMessageBox.information(self, "No selection", "Select one or more particles first.")
            return
        record = self._active_class_record()
        if record is None:
            record = self._ensure_class_record(self._next_default_class_name(), placeholder_name=True)
            self._class_expanded_keys.add(record.key)
            self._set_active_class_key(record.key)
        for row in selected:
            for other in self._class_records.values():
                if other.key != record.key:
                    other.sample_indices.discard(row.index)
            record.sample_indices.add(row.index)
            self._current_state.sample_labels[row.index] = record.label
            self._current_state.classifications[row.index] = record.label
        record.status = f"{len(record.sample_indices)} sample(s) labeled."
        self._sample_status.setText(
            f"{len(self._class_records)} class(es), {len(self._current_state.sample_labels)} labeled sample(s)."
        )
        self._populate_feature_table(
            self._current_state.feature_rows,
            selected_indices=self._selected_feature_indices_from_table(),
        )
        self._sync_class_records_from_state()
        self._active_class_key = record.key
        self._class_add_mode = True
        self._feature_table.clearSelection()
        self._render_current_state()
        self._show_status(f"Assigned class {record.label!r} to {len(selected)} selected feature(s)")
        self.log_message.emit(f"Sample label applied: {record.label!r}")

    def _classify_particles(self) -> None:
        if self._current_state is None or not self._current_state.feature_rows:
            QMessageBox.information(self, "No features", "Apply segmentation settings before classification.")
            return
        samples = self._sample_particles()
        if not samples:
            QMessageBox.information(self, "No samples", "Label at least one sample before running classification.")
            return
        if self._classification_worker is not None and self._classification_worker.isRunning():
            QMessageBox.information(self, "Busy", "Classification is already running.")
            return

        active = self._active_class_record()
        encoder = str(active.encoder if active is not None else "raw")
        threshold_method = str(active.threshold if active is not None else "gmm")
        plane = self._current_state.corrected_plane if self._current_state.corrected_plane is not None else self._current_state.raw_plane
        self._show_status(f"Classifying {self._current_source.name}...")
        self._classification_worker = _ClassificationWorker(
            plane,
            list(self._current_state.particles),
            samples,
            encoder=encoder,
            threshold_method=threshold_method,
        )
        self._classification_worker.result_ready.connect(self._on_classification_ready)
        self._classification_worker.failed.connect(self._on_classification_failed)
        self._classification_worker.start()

    def _on_classification_ready(self, result: object) -> None:
        classifs, used_encoder = result
        if self._current_state is None:
            return
        self._current_state.classifications = {c.particle_index: c.class_name for c in classifs}
        for class_name in self._current_state.classifications.values():
            if str(class_name).strip().lower() and str(class_name).strip().lower() != "other":
                self._register_class_color(str(class_name))
        self._refresh_class_selector()
        self._set_stage("scan")
        self._populate_feature_table(
            self._current_state.feature_rows,
            selected_indices=self._selected_feature_indices_from_table(),
        )
        self._render_current_state()
        self._sample_status.setText(
            f"{len(self._class_records)} class(es), {len(self._current_state.classifications)} classified sample(s)."
        )
        self._show_status(
            f"{self._current_source.name}: classification complete using {used_encoder}"
        )
        self.log_message.emit(
            f"Classification completed for {self._current_source.name} using {used_encoder}"
        )

    def _on_classification_failed(self, message: str) -> None:
        self._show_status(f"Classification failed: {message}")
        self.error_message.emit(message)

    def _set_plane_state(self, plane_index: int, *, clear_analysis: bool) -> None:
        if self._current_scan is None:
            return
        planes = getattr(self._current_scan, "planes", [])
        plane_names = getattr(self._current_scan, "plane_names", [])
        plane_units = getattr(self._current_scan, "plane_units", [])
        if not planes:
            raise RuntimeError("scan has no planes")
        plane_index = max(0, min(int(plane_index), len(planes) - 1))
        raw = np.asarray(planes[plane_index], dtype=np.float64)
        if raw.ndim != 2:
            raise ValueError("preview only supports 2-D scan planes")
        self._current_state = _PreviewState(
            source_path=Path(self._current_scan.source_path),
            scan=self._current_scan,
            plane_index=plane_index,
            raw_plane=raw,
        )
        self._current_state.preview_rows = ()
        self._current_state.preview_particles = ()
        self._current_state.feature_rows = ()
        self._current_state.particles = ()
        self._current_state.sample_labels = {}
        self._current_state.classifications = {}
        self._current_state.class_colors = {}
        self._current_state.warnings = ()
        self._class_records.clear()
        self._class_widgets.clear()
        self._active_class_key = ""
        self._class_add_mode = False
        self._class_expanded_keys.clear()
        self._zero_plane_points = []
        if clear_analysis:
            self._current_state.corrected_plane = None
            self._current_state.background_image = None
            self._clear_feature_table()
            self._queue_class_btn.setEnabled(False)
            self._refresh_class_tree()
        self._set_display_mode("raw")
        if plane_index < len(plane_names):
            self._viewer._title.setText(f"<b>{Path(self._current_scan.source_path).name} | {plane_names[plane_index]}</b>")
        if plane_index < len(plane_units):
            pass
        self._render_current_state()

    def _render_current_state(self) -> None:
        if self._current_state is None:
            self._viewer.set_state(None, display_mode="raw")
            return
        reset_view = self._viewer._current_state is not self._current_state
        overlay_rows = [
            replace(row, label=self._row_class_label(row.index))
            for row in list(self._current_state.feature_rows or self._current_state.preview_rows)
        ]
        self._viewer.set_state(
            self._current_state,
            display_mode=self._current_display_mode(),
            overlay_rows=overlay_rows,
            selected_rows=self._highlighted_feature_rows(overlay_rows),
            zero_points=self._zero_plane_points,
            reset_view=reset_view,
        )

    def _set_display_mode(self, mode: str) -> None:
        for idx in range(self._display_mode.count()):
            if str(self._display_mode.itemData(idx)) == mode:
                self._display_mode.blockSignals(True)
                try:
                    self._display_mode.setCurrentIndex(idx)
                finally:
                    self._display_mode.blockSignals(False)
                break

    def _display_mode_changed(self, *_args) -> None:
        self._render_current_state()

    def _current_display_mode(self) -> str:
        value = self._display_mode.currentData()
        return str(value) if value is not None else "raw"

    # ------------------------------------------------------------------
    # Feature selection / table
    # ------------------------------------------------------------------

    def _populate_feature_table(
        self,
        rows: tuple[PreviewFeatureRow, ...],
        *,
        selected_indices: set[int] | None = None,
    ) -> None:
        self._building_table = True
        try:
            selected_indices = set(selected_indices or set())
            self._feature_table.setRowCount(len(rows))
            for row_idx, row in enumerate(rows):
                select_item = QTableWidgetItem()
                select_item.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                )
                select_item.setCheckState(
                    Qt.CheckState.Checked if row.index in selected_indices else Qt.CheckState.Unchecked
                )
                select_item.setData(Qt.ItemDataRole.UserRole, row.index)
                self._feature_table.setItem(row_idx, 0, select_item)

                values = [
                    str(row.index + 1),
                    row.source,
                    self._row_class_label(row.index),
                    f"{row.x_nm:.3f}",
                    f"{row.y_nm:.3f}",
                    f"{row.dx_nm:.3f}",
                    f"{row.dy_nm:.3f}",
                    "" if row.area_nm2 is None else f"{row.area_nm2:.3f}",
                    f"{row.score:.3f}",
                ]
                for col, value in enumerate(values, start=1):
                    item = QTableWidgetItem(value)
                    item.setFlags(
                        Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                    )
                    item.setData(Qt.ItemDataRole.UserRole, row.index)
                    self._feature_table.setItem(row_idx, col, item)
        finally:
            self._building_table = False

    def _clear_feature_table(self) -> None:
        self._building_table = True
        try:
            self._feature_table.setRowCount(0)
        finally:
            self._building_table = False

    def _on_feature_table_item_changed(self, _item: QTableWidgetItem) -> None:
        if self._building_table:
            return
        self._render_current_state()

    def _selected_feature_rows(self) -> list[PreviewFeatureRow]:
        if self._current_state is None:
            return []
        rows = self._current_state.feature_rows or self._current_state.preview_rows
        if not rows:
            return []
        selected: list[PreviewFeatureRow] = []
        if self._feature_table.rowCount() == 0:
            source = self._preview_selected_indices if self._current_state.feature_rows == () else set()
            for row in rows:
                if row.index in source:
                    selected.append(row)
            return selected
        for row_idx in range(self._feature_table.rowCount()):
            item = self._feature_table.item(row_idx, 0)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, int) and 0 <= data < len(rows):
                selected.append(rows[data])
        return selected

    def _selected_feature_indices_from_table(self) -> set[int]:
        if self._current_state is None:
            return set()
        rows = self._current_state.feature_rows or self._current_state.preview_rows
        if not rows:
            return set()
        if self._feature_table.rowCount() == 0:
            if self._current_state.feature_rows == () and self._current_state.preview_rows:
                return set(self._preview_selected_indices)
            return set()
        selected: set[int] = set()
        for row_idx in range(self._feature_table.rowCount()):
            item = self._feature_table.item(row_idx, 0)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, int):
                selected.add(data)
        return selected

    def _highlighted_feature_rows(self, rows: list[PreviewFeatureRow]) -> list[PreviewFeatureRow]:
        if self._current_state is None:
            return []
        highlighted: dict[int, PreviewFeatureRow] = {}
        for row in self._selected_feature_rows():
            highlighted[row.index] = row
        return list(highlighted.values())

    def _row_class_label(self, feature_index: int) -> str:
        if self._current_state is None:
            return ""
        if feature_index in self._current_state.sample_labels:
            return self._current_state.sample_labels[feature_index]
        if feature_index in self._current_state.classifications:
            return self._current_state.classifications[feature_index]
        return ""

    def _register_class_color(self, label: str) -> str:
        if self._current_state is None:
            return _theme.DARK_BLUE
        key = str(label).strip().lower()
        if not key:
            return _theme.DARK_BLUE
        existing = self._current_state.class_colors.get(key)
        if existing:
            return existing
        palette = [
            _theme.DARK_BLUE,
            "#f97316",
            "#a855f7",
            "#22c55e",
            "#ef4444",
            "#14b8a6",
            "#eab308",
            "#06b6d4",
            "#fb7185",
            "#8b5cf6",
        ]
        used = {str(value).lower() for value in self._current_state.class_colors.values()}
        for candidate in palette:
            if candidate.lower() not in used:
                self._current_state.class_colors[key] = candidate
                return candidate
        color = palette[len(self._current_state.class_colors) % len(palette)]
        self._current_state.class_colors[key] = color
        return color

    def _sync_class_records_from_state(self) -> None:
        previous = {
            key: _ClassRecord(
                key=rec.key,
                label=rec.label,
                color=rec.color,
                name_is_placeholder=rec.name_is_placeholder,
                sample_indices=set(),
                encoder=rec.encoder,
                threshold=rec.threshold,
                crop_size=rec.crop_size,
                status=rec.status,
            )
            for key, rec in self._class_records.items()
        }
        self._class_records = {}
        if self._current_state is None:
            self._refresh_class_tree()
            return
        merged: dict[str, _ClassRecord] = {}
        for idx, label in self._current_state.sample_labels.items():
            key = str(label).strip().lower()
            if not key or key == "other":
                continue
            rec = merged.get(key)
            if rec is None:
                base = previous.get(key)
                rec = base if base is not None else _ClassRecord(
                    key=key,
                    label=str(label),
                    color=self._register_class_color(str(label)),
                    name_is_placeholder=False,
                )
                merged[key] = rec
            rec.sample_indices.add(int(idx))
            rec.status = f"{len(rec.sample_indices)} sample(s) labeled."
        for idx, label in self._current_state.classifications.items():
            key = str(label).strip().lower()
            if not key or key == "other":
                continue
            rec = merged.get(key)
            if rec is None:
                base = previous.get(key)
                rec = base if base is not None else _ClassRecord(
                    key=key,
                    label=str(label),
                    color=self._register_class_color(str(label)),
                    name_is_placeholder=False,
                )
                merged[key] = rec
            rec.sample_indices.add(int(idx))
            if not rec.status or rec.status.startswith("No samples"):
                rec.status = f"{len(rec.sample_indices)} sample(s) labeled."
        for key, rec in previous.items():
            if key not in merged:
                merged[key] = rec
        self._class_records = merged
        self._refresh_class_tree()

    def _active_class_record(self) -> _ClassRecord | None:
        return self._class_records.get(self._active_class_key) if self._active_class_key else None

    def _available_class_labels(self) -> list[str]:
        return [rec.label for rec in self._class_records.values() if rec.label.strip()]

    def _refresh_class_selector(self, preserve: str | None = None) -> None:
        self._sync_class_records_from_state()
        if self._current_state is None:
            self._queue_class_btn.setEnabled(False)
            return
        has_classes = bool(self._class_records)
        has_features = bool(self._current_state.feature_rows)
        enabled = self._stage in {"classification", "scan"} and has_classes and has_features
        self._queue_class_btn.setEnabled(enabled and bool(self._active_class_key))
        if preserve:
            self._set_active_class_key(preserve, preserve_mode=True)

    def _current_class_key(self) -> str:
        return self._active_class_key

    def _toggle_active_class_key(self, class_key: str) -> None:
        key = str(class_key).strip().lower()
        if not key:
            self._set_active_class_key("")
            return
        if key == self._active_class_key:
            self._set_active_class_key("")
            return
        self._set_active_class_key(key)
        self._class_add_mode = True
        self._show_status(f"Adding samples to class {self._active_class_record().label!r}.")

    def _set_active_class_key(self, class_key: str, *, preserve_mode: bool = False) -> None:
        key = str(class_key).strip().lower()
        if not key:
            previous_samples = set()
            previous = self._active_class_record()
            if previous is not None:
                previous_samples = set(previous.sample_indices)
            self._active_class_key = ""
            self._class_add_mode = False
            self._target_class_label.setText("Target class: none")
            self._queue_class_btn.setEnabled(False)
            if previous_samples and self._current_state is not None:
                selected = self._selected_feature_indices_from_table()
                selected.difference_update(previous_samples)
                if self._current_state.feature_rows:
                    self._populate_feature_table(
                        self._current_state.feature_rows,
                        selected_indices=selected,
                    )
                elif self._current_state.preview_rows:
                    self._preview_selected_indices = selected
            self._refresh_class_tree()
            self._update_class_summary_text()
            self._render_current_state()
            return
        if key not in self._class_records:
            return
        self._active_class_key = key
        record = self._class_records[key]
        self._target_class_label.setText(f"Target class: {record.label}")
        self._queue_class_btn.setEnabled(
            self._stage in {"classification", "scan"}
            and self._current_state is not None
            and bool(self._current_state.feature_rows)
        )
        self._sample_status.setText(
            f"{len(self._class_records)} class(es), {sum(len(rec.sample_indices) for rec in self._class_records.values())} labeled sample(s)."
        )
        rows = ()
        if self._current_state is not None:
            rows = self._current_state.feature_rows or self._current_state.preview_rows
        self._populate_feature_table(
            rows,
            selected_indices=set(record.sample_indices),
        )
        self._refresh_class_tree()
        self._ensure_class_card_visible(key)
        self._render_current_state()

    def _select_class_rows_from_combo(self) -> None:
        if self._active_class_key:
            self._set_active_class_key(self._active_class_key)

    def _queue_class_from_active(self) -> None:
        if self._current_state is None or not self._active_class_key:
            self._show_status("Select a class row before queueing a class.")
            return
        self._set_active_class_key(self._active_class_key)
        self._scan_selected_features()

    def _add_class(self) -> None:
        label = self._next_default_class_name()
        self._ensure_class_record(label, placeholder_name=True)
        self._class_expanded_keys.add(label.strip().lower())
        self._set_active_class_key(label)
        self._class_add_mode = True
        self._target_class_label.setText(f"Target class: {label}")
        self._queue_class_btn.setEnabled(
            self._stage in {"classification", "scan"}
            and self._current_state is not None
            and bool(self._current_state.feature_rows)
        )
        self._sample_status.setText(f"{len(self._class_records)} feature-class row(s) ready.")
        self._show_status(f"Added {label}.")
        self._ensure_class_card_visible(label)

    def _next_default_class_name(self) -> str:
        existing = {rec.label.strip().lower() for rec in self._class_records.values()}
        idx = 1
        while True:
            label = f"Name {idx}"
            if label.lower() not in existing:
                return label
            idx += 1

    def _ensure_class_record(self, label: str, *, placeholder_name: bool = False) -> _ClassRecord:
        key = str(label).strip().lower()
        if not key:
            label = self._next_default_class_name()
            key = label.lower()
        record = self._class_records.get(key)
        if record is not None:
            if placeholder_name:
                record.name_is_placeholder = True
            return record
        record = _ClassRecord(
            key=key,
            label=str(label).strip() or key,
            color=self._register_class_color(label),
            name_is_placeholder=bool(placeholder_name),
            encoder="raw",
            threshold="gmm",
            crop_size=48,
        )
        self._class_records[key] = record
        if self._current_state is not None:
            self._current_state.class_colors[key] = record.color
        self._refresh_class_tree()
        return record

    def _refresh_class_tree(self) -> None:
        if self._class_list_layout is None:
            return
        self._building_table = True
        try:
            self._class_widgets.clear()
            self._clear_layout(self._class_list_layout)
            if self._current_state is None or not self._class_records:
                if self._class_empty_label is not None:
                    self._class_empty_label.setVisible(True)
                if self._class_list_content is not None:
                    self._class_list_content.setVisible(False)
                self._target_class_label.setText("Target class: none")
                self._sample_status.setText("No Feature-classes labelled yet.")
                return
            if self._class_empty_label is not None:
                self._class_empty_label.setVisible(False)
            if self._class_list_content is not None:
                self._class_list_content.setVisible(True)
            records = sorted(self._class_records.values(), key=lambda item: item.label.lower())
            for record in records:
                record.status = (
                    f"{len(record.sample_indices)} sample(s) labeled."
                    if record.sample_indices
                    else "No samples labeled yet."
                )
                widgets = self._build_class_card(record)
                self._class_widgets[record.key] = widgets
                self._class_list_layout.addWidget(widgets.root)
        finally:
            self._building_table = False
        self._update_class_summary_text()
        self._update_all_class_card_styles()

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
                continue
            child_layout = item.layout()
            if child_layout is not None:
                self._clear_layout(child_layout)  # type: ignore[arg-type]

    def _build_class_card(self, record: _ClassRecord) -> _ClassRowWidgets:
        root = QFrame()
        root.setObjectName("previewClassCard")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(3)

        header = _ClickableFrame()
        header.setObjectName("previewClassHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(6, 4, 6, 4)
        header_layout.setSpacing(6)

        arrow_btn = QToolButton()
        arrow_btn.setCheckable(True)
        arrow_btn.setChecked(record.key in self._class_expanded_keys)
        arrow_btn.setArrowType(Qt.ArrowType.DownArrow if arrow_btn.isChecked() else Qt.ArrowType.RightArrow)
        arrow_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        arrow_btn.setProperty("role", "toggle")

        name_label = QLabel(record.label)
        name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        swatch = QWidget()
        swatch.setFixedSize(38, 14)
        swatch.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        count_label = QLabel(str(len(record.sample_indices)))
        count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        count_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        delete_btn = QPushButton("Delete")
        delete_btn.setProperty("role", "danger")
        delete_btn.setMinimumHeight(24)

        header_layout.addWidget(arrow_btn)
        header_layout.addWidget(name_label, 1)
        header_layout.addWidget(swatch)
        header_layout.addWidget(count_label)
        header_layout.addStretch(1)
        header_layout.addWidget(delete_btn)

        body = QWidget()
        body.setObjectName("previewClassBody")
        body_layout = QFormLayout(body)
        body_layout.setContentsMargins(2, 2, 2, 2)
        body_layout.setHorizontalSpacing(8)
        body_layout.setVerticalSpacing(3)

        name_edit = QLineEdit(record.label)
        name_edit.setPlaceholderText(record.label)
        if record.name_is_placeholder:
            name_edit.setText("")
        self._style_control(name_edit)

        encoder = _WheelGuardComboBox()
        encoder.addItems(["raw", "pca_kmeans", "auto"])
        encoder.setCurrentText(record.encoder)
        self._style_control(encoder)

        threshold = _WheelGuardComboBox()
        threshold.addItems(["gmm", "otsu", "distribution"])
        threshold.setCurrentText(record.threshold)
        self._style_control(threshold)

        crop_size = QSpinBox()
        crop_size.setRange(16, 256)
        crop_size.setValue(record.crop_size)
        crop_size.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._style_control(crop_size)

        status = QLabel(record.status)
        status.setWordWrap(True)

        body_layout.addRow("Class name", name_edit)
        body_layout.addRow("Encoding", encoder)
        body_layout.addRow("Threshold", threshold)
        body_layout.addRow("Crop size", crop_size)
        body_layout.addRow("Status", status)

        root_layout.addWidget(header)
        root_layout.addWidget(body)

        widgets = _ClassRowWidgets(
            root=root,
            header=header,
            body_scroll=body,
            arrow_btn=arrow_btn,
            name_label=name_label,
            swatch=swatch,
            count_label=count_label,
            delete_btn=delete_btn,
            name_edit=name_edit,
            encoder=encoder,
            threshold=threshold,
            crop_size=crop_size,
            status=status,
        )

        header.clicked.connect(lambda key=record.key: self._toggle_active_class_key(key))
        arrow_btn.toggled.connect(lambda checked, key=record.key: self._set_class_card_expanded(key, checked))
        delete_btn.clicked.connect(lambda _=False, key=record.key: self._delete_class(key))
        name_edit.editingFinished.connect(lambda key=record.key: self._rename_class_from_widget(key))
        encoder.currentIndexChanged.connect(lambda *_args, key=record.key: self._update_class_settings_from_widget(key))
        threshold.currentIndexChanged.connect(lambda *_args, key=record.key: self._update_class_settings_from_widget(key))
        crop_size.valueChanged.connect(lambda *_args, key=record.key: self._update_class_settings_from_widget(key))

        self._update_class_card_visual(record.key, widgets)
        return widgets

    def _update_class_summary_text(self) -> None:
        if self._current_state is None:
            return
        total = sum(len(rec.sample_indices) for rec in self._class_records.values())
        if self._class_records:
            self._sample_status.setText(f"{len(self._class_records)} class(es), {total} labeled sample(s).")
        else:
            self._sample_status.setText("No Feature-classes labelled yet.")

    def _update_class_card_visual(self, key: str, widget: _ClassRowWidgets | None = None) -> None:
        record = self._class_records.get(key)
        widgets = widget or self._class_widgets.get(key)
        if record is None or widgets is None:
            return
        active = key == self._active_class_key
        expanded = key in self._class_expanded_keys
        widgets.root.setProperty("active", active)
        widgets.header.setProperty("active", active)
        widgets.name_label.setText(record.label)
        widgets.count_label.setText(str(len(record.sample_indices)))
        widgets.status.setText(record.status)
        widgets.swatch.setStyleSheet(
            f"background-color: {record.color}; border: 1px solid {_theme.DARK_BORDER}; border-radius: 4px;"
        )
        header_sheet = (
            f"QFrame#previewClassHeader {{background-color: #3A638F; border: 1px solid {_theme.AMBER}; border-radius: 5px;}}"
            if active
            else "QFrame#previewClassHeader {background-color: #2E5279; border: 1px solid rgba(255,255,255,0.12); border-radius: 5px;}"
        )
        widgets.header.setStyleSheet(
            header_sheet
            + (
                f"QFrame#previewClassHeader QLabel {{color: {_theme.DARK_WHITE}; background: transparent; font-weight: 700;}}"
                "QFrame#previewClassHeader QToolButton {"
                f"background-color: #35507A; color: {_theme.DARK_WHITE}; border: 1px solid rgba(255,255,255,0.10); border-radius: 4px; padding: 2px 6px; min-width: 22px; min-height: 22px;}}"
                "QFrame#previewClassHeader QToolButton:hover {background-color: #406089;}"
                "QFrame#previewClassHeader QPushButton[role=\"danger\"] {min-height: 22px; padding: 2px 8px; min-width: 54px;}"
            )
        )
        widgets.body_scroll.setStyleSheet(f"QWidget#previewClassBody {{background-color: {_theme.DARK_SURFACE};}}")
        widgets.arrow_btn.blockSignals(True)
        try:
            widgets.body_scroll.setVisible(expanded)
            widgets.arrow_btn.setChecked(expanded)
            widgets.arrow_btn.setArrowType(
                Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
            )
        finally:
            widgets.arrow_btn.blockSignals(False)

    def _update_all_class_card_styles(self) -> None:
        for key in list(self._class_widgets.keys()):
            self._update_class_card_visual(key)

    def _set_class_card_expanded(self, key: str, expanded: bool) -> None:
        widgets = self._class_widgets.get(key)
        if widgets is None:
            return
        if expanded:
            self._class_expanded_keys.add(key)
        else:
            self._class_expanded_keys.discard(key)
        widgets.body_scroll.setVisible(bool(expanded))
        self._update_class_card_visual(key, widgets)
        if expanded and self._controls_scroll is not None:
            self._controls_scroll.ensureWidgetVisible(widgets.root, 0, 8)

    def _ensure_class_card_visible(self, key: str) -> None:
        widgets = self._class_widgets.get(key)
        if widgets is None or self._controls_scroll is None:
            return
        self._controls_scroll.ensureWidgetVisible(widgets.root, 0, 8)

    def _rename_class_from_widget(self, key: str) -> None:
        record = self._class_records.get(key)
        widget = self._class_widgets.get(key)
        if record is None or widget is None:
            return
        new_label = widget.name_edit.text().strip()
        if not new_label:
            if record.name_is_placeholder:
                widget.name_edit.setText("")
            else:
                widget.name_edit.setText(record.label)
            return
        new_key = new_label.lower()
        if new_key != key and new_key in self._class_records:
            widget.name_edit.setText(record.label)
            return
        if new_key != key:
            self._class_records.pop(key, None)
            self._class_records[new_key] = record
            self._class_widgets[new_key] = widget
            self._class_widgets.pop(key, None)
            if key in self._class_expanded_keys:
                self._class_expanded_keys.discard(key)
                self._class_expanded_keys.add(new_key)
            for mapping in (self._current_state.sample_labels if self._current_state else {}, self._current_state.classifications if self._current_state else {}):
                for idx, label in list(mapping.items()):
                    if str(label).strip().lower() == key:
                        mapping[idx] = new_label
            if self._current_state is not None and key in self._current_state.class_colors:
                self._current_state.class_colors[new_key] = self._current_state.class_colors.pop(key)
            if self._active_class_key == key:
                self._active_class_key = new_key
        record.key = new_key
        record.label = new_label
        record.name_is_placeholder = False
        self._update_class_settings_from_widget(new_key)
        self._refresh_class_tree()
        if self._current_state is not None and self._current_state.feature_rows:
            self._populate_feature_table(
                self._current_state.feature_rows,
                selected_indices=self._selected_feature_indices_from_table(),
            )
        self._render_current_state()

    def _update_class_settings_from_widget(self, key: str) -> None:
        record = self._class_records.get(key)
        widget = self._class_widgets.get(key)
        if record is None or widget is None:
            return
        edited_label = widget.name_edit.text().strip()
        if edited_label:
            record.label = edited_label
            record.name_is_placeholder = False
        elif not record.name_is_placeholder:
            widget.name_edit.setText(record.label)
        else:
            widget.name_edit.setText("")
        record.encoder = str(widget.encoder.currentText())
        record.threshold = str(widget.threshold.currentText())
        record.crop_size = int(widget.crop_size.value())
        record.status = f"{len(record.sample_indices)} sample(s) labeled."
        widget.status.setText(record.status)
        widget.count_label.setText(str(len(record.sample_indices)))
        if self._current_state is not None:
            self._current_state.class_colors[key] = record.color
        self._update_class_card_visual(key, widget)
        self._refresh_class_tree()

    def _delete_class(self, key: str) -> None:
        record = self._class_records.pop(key, None)
        self._class_widgets.pop(key, None)
        self._class_expanded_keys.discard(key)
        if record is None:
            return
        if self._current_state is not None:
            for idx, label in list(self._current_state.sample_labels.items()):
                if str(label).strip().lower() == key:
                    self._current_state.sample_labels.pop(idx, None)
            for idx, label in list(self._current_state.classifications.items()):
                if str(label).strip().lower() == key:
                    self._current_state.classifications.pop(idx, None)
            self._current_state.class_colors.pop(key, None)
        if self._active_class_key == key:
            self._active_class_key = ""
            self._class_add_mode = False
            self._queue_class_btn.setEnabled(False)
            self._target_class_label.setText("Target class: none")
        if self._class_records:
            self._sample_status.setText(f"{len(self._class_records)} feature-class row(s) ready.")
        else:
            self._sample_status.setText("No Feature-classes labelled yet.")
        self._refresh_class_tree()
        if self._current_state is not None and self._current_state.feature_rows:
            self._populate_feature_table(
                self._current_state.feature_rows,
                selected_indices=self._selected_feature_indices_from_table(),
            )
        self._render_current_state()

    def _selected_feature_indices_from_active_class(self) -> set[int]:
        record = self._active_class_record()
        return set(record.sample_indices) if record is not None else set()

    def _assign_feature_to_active_class(self, feature_index: int) -> None:
        if self._current_state is None or not self._active_class_key:
            return
        record = self._class_records.get(self._active_class_key)
        if record is None:
            return
        label = record.label
        for key, other in self._class_records.items():
            if feature_index in other.sample_indices and key != self._active_class_key:
                other.sample_indices.discard(feature_index)
        record.sample_indices.add(feature_index)
        record.status = f"{len(record.sample_indices)} sample(s) labeled."
        self._current_state.sample_labels[feature_index] = label
        self._current_state.classifications[feature_index] = label
        self._current_state.class_colors[self._active_class_key] = record.color
        self._preview_selected_indices = set(record.sample_indices)
        self._sync_feature_table_for_selection(feature_index, checked=True)
        self._sync_feature_table_for_class_labels()
        self._refresh_class_tree()
        self._render_current_state()

    def _sync_feature_table_for_selection(self, feature_index: int, *, checked: bool) -> None:
        if self._feature_table.rowCount() == 0:
            if self._current_state is not None and self._current_state.feature_rows == () and self._current_state.preview_rows:
                if checked:
                    self._preview_selected_indices.add(feature_index)
                else:
                    self._preview_selected_indices.discard(feature_index)
            return
        self._building_table = True
        try:
            for row_idx in range(self._feature_table.rowCount()):
                item = self._feature_table.item(row_idx, 0)
                if item is None:
                    continue
                data = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(data, int) and data == feature_index:
                    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                    self._feature_table.selectRow(row_idx)
                    self._feature_table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                    break
        finally:
            self._building_table = False

    def _sync_feature_table_for_class_labels(self) -> None:
        if self._current_state is None or self._feature_table.rowCount() == 0:
            return
        self._building_table = True
        try:
            for row_idx in range(self._feature_table.rowCount()):
                item = self._feature_table.item(row_idx, 0)
                if item is None:
                    continue
                data = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(data, int):
                    label = self._row_class_label(data)
                    self._feature_table.item(row_idx, 3).setText(label)
        finally:
            self._building_table = False

    def _set_all_selected(self, checked: bool) -> None:
        if self._current_state is None:
            return
        rows = self._current_state.feature_rows or self._current_state.preview_rows
        if not rows:
            return
        self._building_table = True
        try:
            state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            for row_idx in range(self._feature_table.rowCount()):
                item = self._feature_table.item(row_idx, 0)
                if item is not None:
                    item.setCheckState(state)
        finally:
            self._building_table = False
        if self._current_state.feature_rows == () and self._current_state.preview_rows:
            if checked:
                self._preview_selected_indices = {row.index for row in rows}
            else:
                self._preview_selected_indices.clear()
        self._render_current_state()

    def _toggle_feature_selection(self, feature_index: int) -> None:
        if self._zero_plane_mode:
            return
        if self._class_add_mode and self._active_class_key:
            self._assign_feature_to_active_class(feature_index)
            return
        matched = False
        for row_idx in range(self._feature_table.rowCount()):
            item = self._feature_table.item(row_idx, 0)
            if item is None:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, int) and data == feature_index:
                matched = True
                self._building_table = True
                try:
                    item.setCheckState(
                        Qt.CheckState.Unchecked
                        if item.checkState() == Qt.CheckState.Checked
                        else Qt.CheckState.Checked
                    )
                finally:
                    self._building_table = False
                self._feature_table.selectRow(row_idx)
                self._feature_table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                break
        if not matched and self._current_state is not None and self._current_state.feature_rows == () and self._current_state.preview_rows:
            if feature_index in self._preview_selected_indices:
                self._preview_selected_indices.discard(feature_index)
            else:
                self._preview_selected_indices.add(feature_index)
        self._render_current_state()

    def _sample_particles(self) -> list[tuple[str, Particle]]:
        if self._current_state is None:
            return []
        samples: list[tuple[str, Particle]] = []
        if not self._current_state.sample_labels:
            return samples
        by_index = {particle.index: particle for particle in self._current_state.particles}
        for idx, class_name in self._current_state.sample_labels.items():
            particle = by_index.get(idx)
            if particle is not None:
                samples.append((class_name, particle))
        return samples

    # ------------------------------------------------------------------
    # Follow-up scan
    # ------------------------------------------------------------------

    def _scan_selected_features(self) -> None:
        if self._current_state is None:
            QMessageBox.information(self, "No preview", "Load a scan before scanning features.")
            return
        selected = self._selected_feature_rows()
        if not selected:
            QMessageBox.information(self, "No selection", "Select at least one feature to scan.")
            return
        if self._scan_worker is not None and self._scan_worker.isRunning():
            QMessageBox.information(self, "Busy", "A follow-up scan is already running.")
            return

        # Pre-populate dialog with current STM parameters (fall back to last-used defaults)
        feat_defaults = dict(self._feature_scan_defaults)
        try:
            current = self._stm.scan.read()
            feat_defaults.setdefault("bias_V", current.bias_V)
            feat_defaults.setdefault("setpoint_nA", current.setpoint_A * 1e9)
            # Default size to 1/5 of the current scan width if not previously set
            if "size_nm" not in feat_defaults and current.size_nm:
                feat_defaults["size_nm"] = round(current.size_nm[0] / 5.0, 1)
        except Exception:
            pass

        dlg = _FeatureScanDialog(self, defaults=feat_defaults)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        scan_params = dlg.values()
        self._feature_scan_defaults = dict(scan_params)

        self._scan_selected_btn.setEnabled(False)
        self._stop_scan_btn.setEnabled(True)
        feat_scan_range_m = getattr(self._current_scan, "scan_range_m", None)
        self._scan_worker = _FeatureScanWorker(
            self._stm,
            self._current_state.source_path,
            selected,
            bias_V=scan_params["bias_V"],
            setpoint_A=scan_params["setpoint_nA"] * 1e-9,
            n_reps=scan_params["repetitions"],
            bias_sequence=scan_params["bias_sequence"] or None,
            size_nm=scan_params["size_nm"],
            home_nm=self._preview_home_nm,
            scan_range_nm=(
                (feat_scan_range_m[0] * 1e9, feat_scan_range_m[1] * 1e9)
                if feat_scan_range_m is not None else None
            ),
            enable_centering=scan_params.get("enable_centering", False),
            quick_pixels=scan_params.get("quick_pixels", 64),
            pixels=scan_params.get("pixels", 256),
            target_duration_s=(
                scan_params["target_minutes"] * 60.0
                if scan_params.get("target_minutes") is not None else None
            ),
        )
        n_reps = len(scan_params["bias_sequence"]) if scan_params["bias_sequence"] else scan_params["repetitions"]
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.scan_saved.connect(self._on_followup_scan_saved)
        self._scan_worker.failed.connect(self._on_followup_scan_failed)
        self._scan_worker.finished.connect(lambda: self._scan_selected_btn.setEnabled(True))
        self._scan_worker.finished.connect(lambda: self._stop_scan_btn.setEnabled(False))
        self._scan_worker.finished.connect(self._on_feat_scan_finished)
        self._scan_worker.start()

        self._feat_scan_start_t = time.time()
        self._feat_scan_total = len(selected) * n_reps
        self._feat_scans_saved = 0
        self._time_label.setVisible(True)
        self._time_label.setText("")
        self._eta_timer.start()

        self._show_status(
            f"Scanning {len(selected)} feature(s)"
            + (f" × {n_reps} bias steps" if n_reps > 1 else "") + "…"
        )

    def _on_scan_progress(self, idx: int, total: int, label: str) -> None:
        self._show_status(f"{label} ({idx}/{total})")

    def _on_followup_scan_saved(self, path: str) -> None:
        self._feat_scans_saved += 1
        self._refresh_eta()
        self.scan_completed.emit(path)
        self.handle_scan_completed(path)
        self.log_message.emit(f"Follow-up scan saved: {path}")

    def _on_followup_scan_failed(self, message: str) -> None:
        self.error_message.emit(message)
        self._show_status(f"Follow-up scan failed: {message}")

    def _on_feat_scan_finished(self) -> None:
        self._eta_timer.stop()
        self._time_label.setVisible(False)
        self._feat_scan_start_t = None

    def _refresh_eta(self) -> None:
        if self._feat_scan_start_t is None or self._feat_scan_total == 0:
            return
        elapsed = time.time() - self._feat_scan_start_t
        saved = self._feat_scans_saved
        total = self._feat_scan_total
        if saved > 0:
            pace_s = elapsed / saved
            remaining_s = max(0.0, (total - saved) * pace_s)
        else:
            remaining_s = 0.0
            self._time_label.setText(f"0/{total} scans done — estimating…")
            return
        if remaining_s < 60:
            rem_str = "< 1 min"
        elif remaining_s < 3600:
            rem_str = f"~{int(remaining_s / 60)}m"
        else:
            h = int(remaining_s / 3600)
            m = int((remaining_s % 3600) / 60)
            rem_str = f"~{h}h {m:02d}m"
        eta = _dt.datetime.now() + _dt.timedelta(seconds=remaining_s)
        self._time_label.setText(
            f"{saved}/{total} scans done — Remaining: {rem_str}  (ETA {eta.strftime('%H:%M')})"
        )

    # ------------------------------------------------------------------
    # Group scan
    # ------------------------------------------------------------------

    def _scan_selected_as_groups(self) -> None:
        """Cluster the checked features spatially and scan each group."""
        if self._current_state is None:
            QMessageBox.information(self, "No preview", "Load a scan before scanning groups.")
            return
        selected = self._selected_feature_rows()
        if not selected:
            QMessageBox.information(self, "No selection",
                                    "Select at least one feature before scanning groups.")
            return
        if (self._group_scan_worker is not None
                and self._group_scan_worker.isRunning()):
            QMessageBox.information(self, "Busy", "A group scan is already running.")
            return
        if self._scan_worker is not None and self._scan_worker.isRunning():
            QMessageBox.information(self, "Busy",
                                    "A follow-up scan is already running. "
                                    "Wait for it to finish before launching a group scan.")
            return

        # Show parameter dialog (pre-populated with last-used values, then current STM)
        group_defaults = dict(self._group_scan_defaults)
        # Bias sequences are one-off; don't carry them over so iterations is always editable
        group_defaults.pop("bias_sequence_str", None)
        group_defaults.pop("bias_sequence", None)
        try:
            current = self._stm.scan.read()
            group_defaults.setdefault("bias_V", current.bias_V)
            group_defaults.setdefault("setpoint_nA", current.setpoint_A * 1e9)
        except Exception:
            pass
        dlg = _GroupScanDialog(self, defaults=group_defaults)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        params = dlg.values()
        self._group_scan_defaults = dict(params)  # remember for next time

        # Compute groups
        scan_range_m = getattr(self._current_scan, "scan_range_m", None)
        scan_shape = self._current_state.raw_plane.shape  # (ny, nx)
        groups = group_features(
            selected,
            scan_range_m,
            scan_shape,
            max_per_group=params["max_per_group"],
            max_group_nm=params["max_group_nm"],
            feature_padding_nm=params["feature_padding_nm"],
        )
        if not groups:
            QMessageBox.information(self, "No groups",
                                    "Feature grouping produced no groups — "
                                    "check that at least one feature is selected.")
            return

        n_feat = len(selected)
        n_groups = len(groups)
        member_summary = ", ".join(
            str(len(g.members)) for g in groups
        )
        msg = (
            f"{n_feat} feature(s) grouped into {n_groups} group(s) "
            f"[{member_summary} feature(s) each].\n\n"
            f"Proceed to scan all {n_groups} group(s)?"
        )
        if QMessageBox.question(
            self, "Confirm group scan", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        px = params["group_pixels"]
        self._scan_groups_btn.setEnabled(False)
        self._scan_selected_btn.setEnabled(False)
        self._stop_scan_btn.setEnabled(True)
        bias_seq = params.get("bias_sequence") or []
        self._group_scan_worker = _FeatureGroupScanWorker(
            self._stm,
            self._current_state.source_path,
            groups,
            group_pixels=(px, px),
            group_speed_nm_s=params["group_speed_nm_s"],
            group_iterations=params["group_iterations"],
            settling_s=params["settling_s"],
            output_folder=params["output_folder"],
            bias_V=params.get("bias_V"),
            setpoint_A=params["setpoint_nA"] * 1e-9 if params.get("setpoint_nA") else None,
            bias_sequence=bias_seq or None,
            home_nm=self._preview_home_nm,
            scan_range_nm=(
                (scan_range_m[0] * 1e9, scan_range_m[1] * 1e9)
                if scan_range_m is not None else None
            ),
            target_scan_time_s=params.get("target_scan_time_s"),
        )
        self._group_scan_worker.group_started.connect(self._on_group_started)
        self._group_scan_worker.group_scan_saved.connect(self._on_group_scan_saved)
        self._group_scan_worker.failed.connect(self._on_group_scan_failed)
        self._group_scan_worker.finished_all.connect(self._on_group_scan_finished_all)
        self._group_scan_worker.finished.connect(self._on_group_scan_thread_done)
        self._group_scan_worker.start()
        n_steps = len(bias_seq) if bias_seq else params["group_iterations"]
        self.log_message.emit(
            f"Group scan started: {n_feat} feature(s) → {n_groups} group(s)"
            + (f" × {n_steps} bias steps" if bias_seq else f" × {n_steps} iter(s)")
        )
        self._show_status(
            f"Scanning {n_groups} group(s) ({n_feat} feature(s) total)"
            + (f" × {n_steps} bias steps" if bias_seq else "") + "…"
        )

    def _on_group_started(self, group_idx: int, total: int, label: str) -> None:
        self._show_status(f"Group scan {group_idx}/{total}: {label}")
        self.log_message.emit(f"Group scan {group_idx}/{total}: {label}")

    def _on_group_scan_saved(self, group_idx: int, path: str) -> None:
        self.scan_completed.emit(path)
        self.log_message.emit(f"Group {group_idx} scan saved: {path}")

    def _on_group_scan_failed(self, message: str) -> None:
        self.error_message.emit(message)
        self._show_status(f"Group scan failed: {message}")

    def _on_group_scan_finished_all(self, folder: str) -> None:
        self.log_message.emit(f"Group scan complete. Output: {folder}")
        self._show_status(f"Group scan complete → {folder}")

    def _on_group_scan_thread_done(self) -> None:
        self._stop_scan_btn.setEnabled(False)
        self._scan_groups_btn.setEnabled(
            self._stage in {"classification", "scan"}
            and self._current_state is not None
            and bool(self._current_state.feature_rows)
        )
        self._scan_selected_btn.setEnabled(
            self._stage in {"classification", "scan"}
            and self._current_state is not None
            and bool(self._current_state.feature_rows)
        )

    def _stop_active_scan(self) -> None:
        if self._scan_worker is not None and self._scan_worker.isRunning():
            self._scan_worker.stop()
            self._show_status("Stop requested — will halt after current target.")
        if self._group_scan_worker is not None and self._group_scan_worker.isRunning():
            self._group_scan_worker.stop()
            self._show_status("Stop requested — will halt after current group.")
        self._stop_scan_btn.setEnabled(False)

    def stop_for_close(self) -> None:
        """Stop background workers when the application closes.

        The scan workers move the tip and own a COM apartment: request a
        stop, give them 8 s to finish the current scan cleanly, then
        hard-terminate only as a last resort — terminate skips the COM
        unbind and STMAFM may need a restart afterwards. The compute
        workers (load / background / segmentation / classification) touch
        no hardware; give each a short grace period so Qt does not tear
        down a still-running thread at shutdown.
        """
        for name in ("_scan_worker", "_group_scan_worker"):
            worker = getattr(self, name, None)
            if worker is None or not worker.isRunning():
                continue
            log.info("Window closing — stopping %s", type(worker).__name__)
            worker.stop()
            if not worker.wait(8000):
                log.warning(
                    "%s did not exit after 8 s on close — terminating. "
                    "STMAFM may need a restart.", type(worker).__name__,
                )
                worker.terminate()
                worker.wait(1000)
        for name in ("_load_worker", "_background_worker",
                     "_segmentation_worker", "_classification_worker"):
            worker = getattr(self, name, None)
            if worker is not None and worker.isRunning():
                worker.wait(2000)

    # ------------------------------------------------------------------
    # Scan status poller
    # ------------------------------------------------------------------

    def _refresh_scan_info(self) -> None:
        """Called every 2 s — shows position + size whenever a scan is running."""
        try:
            if not self._stm.connected:
                self._scan_info_label.setVisible(False)
                self._scan_was_running = False
                return
            running = self._stm.scan.is_running
            if not running:
                self._scan_info_label.setVisible(False)
                self._scan_was_running = False
                return
            offset = self._stm.scan.get_offset_nm()
            params = self._stm.scan.read()
            pos_str = (
                f"X={offset[0]:+.2f}  Y={offset[1]:+.2f} nm"
                if offset else "pos: n/a"
            )
            size_str = (
                f"{params.size_nm[0]:.1f} × {params.size_nm[1]:.1f} nm"
                if params.size_nm else "size: n/a"
            )
            est_s = _estimate_scan_duration(params)
            est_str = _format_duration(est_s)
            self._scan_info_label.setText(
                f"Scanning  |  {pos_str}  |  {size_str}  |  ~{est_str}"
            )
            self._scan_info_label.setVisible(True)
            # Log once on the scan-start transition so position appears in the log panel
            if not self._scan_was_running:
                self.log_message.emit(
                    f"Scan started  |  {pos_str}  |  {size_str}  |  ~{est_str}"
                )
                log.info(
                    "Scan detected: offset %s  size %s  est %s",
                    pos_str, size_str, est_str,
                )
            self._scan_was_running = True
        except Exception:
            self._scan_info_label.setVisible(False)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose scan folder")
        if path:
            self._folder_edit.setText(path)
            self.refresh_latest()

    def _pick_scan_file(self) -> None:
        folder = self._folder()
        start_dir = str(folder) if folder is not None else str(Path.cwd())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open .dat scan",
            start_dir,
            "Createc scans (*.dat);;All files (*)",
        )
        if path:
            self.open_scan(path)

    def _open_recent_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(path, str) and path:
            self.open_scan(path)

    def _remember_recent_source(self, source: Path) -> None:
        resolved = source.resolve()
        self._recent_sources = [p for p in self._recent_sources if p.resolve() != resolved]
        self._recent_sources.insert(0, resolved)
        del self._recent_sources[self._recent_limit :]
        self._refresh_recent_list()

    def _refresh_recent_list(self) -> None:
        if not hasattr(self, "_recent_list"):
            return
        self._recent_list.blockSignals(True)
        try:
            self._recent_list.clear()
            for path in self._recent_sources:
                label = path.name
                if path.parent.name:
                    label = f"{path.name}  [{path.parent.name}]"
                item = QListWidgetItem(label)
                item.setToolTip(str(path))
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                self._recent_list.addItem(item)
            if hasattr(self, "_recent_empty_label"):
                self._recent_empty_label.setVisible(self._recent_list.count() == 0)
            if hasattr(self, "_recent_label"):
                self._recent_label.setVisible(self._recent_list.count() > 0)
            self._recent_list.setVisible(self._recent_list.count() > 0)
        finally:
            self._recent_list.blockSignals(False)

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("previewSection")
        return label

    def _style_control(self, widget: QWidget) -> None:
        widget.setStyleSheet("""
            QWidget {
                background-color: %s;
                color: %s;
                border: 1px solid %s;
                border-radius: 4px;
                padding: 4px 8px;
                min-height: 24px;
            }
            QWidget:focus {
                border: 1px solid %s;
            }
        """ % (
            _theme.DARK_SURFACE,
            _theme.DARK_TEXT,
            _theme.DARK_BORDER,
            _theme.DARK_BLUE,
        ))

    def _style_button(self, widget: QPushButton) -> None:
        widget.setProperty("role", "primary")
        self._apply_button_style(widget, "primary")

    def _apply_preview_button_styles(self) -> None:
        for widget in self.findChildren(QPushButton):
            role = str(widget.property("role") or "").strip().lower()
            if role:
                self._apply_button_style(widget, role)
        for widget in self.findChildren(QToolButton):
            role = str(widget.property("role") or "").strip().lower()
            if role:
                self._apply_button_style(widget, role)

    def _apply_button_style(self, widget: QWidget, role: str) -> None:
        selector = "QToolButton" if isinstance(widget, QToolButton) else "QPushButton"
        role = (role or "primary").strip().lower()
        if role == "accent":
            widget.setStyleSheet(f"""
                {selector} {{
                    background-color: {_theme.AMBER};
                    color: #1A1A1A;
                    border: 1px solid #FFD166;
                    border-radius: 4px;
                    min-height: 28px;
                    padding: 4px 10px;
                    font-weight: 800;
                }}
                {selector}:hover {{ background-color: #E09A00; }}
                {selector}:disabled {{
                    background-color: #22344D;
                    color: {_theme.DARK_TEXT_MUTED};
                    border: 1px solid #2B4565;
                }}
            """)
            return
        if role == "danger":
            widget.setStyleSheet(f"""
                {selector} {{
                    background-color: #7f1d1d;
                    color: {_theme.DARK_WHITE};
                    border: 1px solid #ef4444;
                    border-radius: 4px;
                    min-height: 28px;
                    padding: 4px 10px;
                    font-weight: 700;
                }}
                {selector}:hover {{ background-color: #991b1b; }}
                {selector}:disabled {{
                    background-color: #22344D;
                    color: {_theme.DARK_TEXT_MUTED};
                    border: 1px solid #2B4565;
                }}
            """)
            return
        if role == "toggle":
            widget.setStyleSheet(f"""
                {selector} {{
                    background-color: #315982;
                    color: {_theme.DARK_WHITE};
                    border: 1px solid #5A7DA6;
                    border-radius: 4px;
                    min-height: 28px;
                    padding: 4px 10px;
                    font-weight: 700;
                }}
                {selector}:hover {{ background-color: #3D6B99; }}
                {selector}:checked {{
                    background-color: {_theme.DARK_BLUE};
                    color: #061122;
                    border: 1px solid #93C5FD;
                }}
                {selector}:disabled {{
                    background-color: #22344D;
                    color: {_theme.DARK_TEXT_MUTED};
                    border: 1px solid #2B4565;
                }}
            """)
            return
        widget.setStyleSheet(f"""
            {selector} {{
                background-color: {_theme.DARK_BLUE};
                color: #061122;
                border: 1px solid #93C5FD;
                border-radius: 4px;
                min-height: 28px;
                padding: 4px 10px;
                font-weight: 700;
            }}
            {selector}:hover {{
                background-color: #93C5FD;
                border-color: #BFDBFE;
            }}
            {selector}:disabled {{
                background-color: #22344D;
                color: {_theme.DARK_TEXT_MUTED};
                border: 1px solid #2B4565;
            }}
        """)

    def _slider_row(self, slider: QSlider, value_label: QLabel) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(slider, 1)
        value_label.setMinimumWidth(56)
        layout.addWidget(value_label)
        return row

    def _connect_slider_value(self, slider: QSlider, value_label: QLabel, value: int, *, percent: bool = False) -> None:
        self._update_slider_label(value_label, value, percent=percent)
        slider.valueChanged.connect(lambda v, lbl=value_label, pct=percent: self._update_slider_label(lbl, v, percent=pct))

    def _update_slider_label(self, label: QLabel, value: int, *, percent: bool = False) -> None:
        if percent:
            label.setText(f"{value / 1000.0:.3f}")
        else:
            label.setText(str(int(value)))

    def _slider_percent_to_area_nm2(self, slider_value: int) -> float:
        if self._current_state is None:
            return float(slider_value)
        scan_range = getattr(self._current_scan, "scan_range_m", None)
        if scan_range is None:
            return float(slider_value)
        area_nm2 = float(scan_range[0] * 1e9 * scan_range[1] * 1e9)
        return area_nm2 * (float(slider_value) / 100000.0)

    def _set_stage(self, stage: str) -> None:
        self._stage = stage
        self._initial_section.setExpanded(stage in {"raw", "segmentation"})
        self._classification_section.setExpanded(stage in {"classification", "scan"})
        self._scan_section.setExpanded(stage == "scan")
        self._features_btn.setVisible(stage == "segmentation")
        self._features_btn.setEnabled(stage == "segmentation" and self._current_state is not None and self._current_state.preview_rows is not None)
        self._label_btn.setVisible(stage in {"classification", "scan"})
        self._classify_btn.setVisible(stage == "classification")
        self._scan_selected_btn.setVisible(stage in {"classification", "scan"})
        self._scan_groups_btn.setVisible(stage in {"classification", "scan"})
        self._stop_scan_btn.setVisible(stage in {"classification", "scan"})
        self._queue_class_btn.setVisible(stage in {"classification", "scan"})
        self._label_btn.setEnabled(stage in {"classification", "scan"} and self._current_state is not None and bool(self._current_state.feature_rows))
        self._classify_btn.setEnabled(stage == "classification" and self._current_state is not None and bool(self._current_state.feature_rows))
        can_scan = stage in {"classification", "scan"} and self._current_state is not None and bool(self._current_state.feature_rows)
        self._scan_selected_btn.setEnabled(can_scan)
        self._scan_groups_btn.setEnabled(can_scan)
        followup_running = bool(self._scan_worker is not None and self._scan_worker.isRunning())
        group_running = bool(self._group_scan_worker is not None and self._group_scan_worker.isRunning())
        self._stop_scan_btn.setEnabled(stage in {"classification", "scan"} and (followup_running or group_running))
        class_enabled = self._current_state is not None and bool(self._current_state.feature_rows) and bool(self._class_records)
        self._queue_class_btn.setEnabled(stage in {"classification", "scan"} and class_enabled and bool(self._active_class_key))
        self._background_btn.setVisible(stage in {"raw", "segmentation"})
        self._zero_plane_btn.setVisible(stage in {"raw", "segmentation"})
        self._reset_btn.setVisible(stage in {"raw", "segmentation", "classification", "scan"})
        self._reset_btn.setEnabled(self._current_scan is not None)

    def _on_segmentation_slider_changed(self, value: int) -> None:
        self._update_slider_label(self._threshold_value, self._threshold_slider.value())
        self._update_slider_label(self._min_area_value, self._min_area_slider.value(), percent=True)
        self._update_slider_label(self._max_area_value, self._max_area_slider.value(), percent=True)
        if self._stage == "classification":
            self._set_stage("segmentation")
            if self._current_state is not None:
                self._current_state.feature_rows = ()
                self._current_state.particles = ()
                self._current_state.sample_labels.clear()
                self._current_state.classifications.clear()
                self._current_state.class_colors.clear()
                self._class_records.clear()
                self._class_widgets.clear()
                self._active_class_key = ""
                self._class_add_mode = False
                self._class_expanded_keys.clear()
                self._clear_feature_table()
                self._preview_selected_indices.clear()
                self._sample_status.setText("No Feature-classes labelled yet.")
        elif self._stage == "segmentation" and self._current_state is not None and not self._current_state.feature_rows:
            self._preview_selected_indices = self._selected_feature_indices_from_table()
        self._schedule_live_segmentation()

    def _folder(self) -> Path | None:
        text = self._folder_edit.text().strip()
        if text:
            folder = Path(text)
            return folder if folder.exists() else None
        if self._current_source is not None:
            return self._current_source.parent
        return None

    def _plane_changed(self, *_args) -> None:
        if self._current_scan is None:
            return
        self._set_plane_state(self._plane_spin.value(), clear_analysis=True)
        self._show_status(
            f"{Path(self._current_scan.source_path).name}: plane {self._plane_spin.value()} loaded"
        )

    def _show_status(self, message: str) -> None:
        self._status.setText(message)
        self.log_message.emit(message)


class PreviewWindow(QMainWindow):
    """Floating preview window that hosts the reusable PreviewPanel."""

    log_message = Signal(str)
    error_message = Signal(str)
    scan_completed = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowTitle("ScanFlow Preview")
        self.resize(1380, 960)

        self._panel = PreviewPanel(stm, parent=self)
        self.setCentralWidget(self._panel)

        self._panel.log_message.connect(self.log_message.emit)
        self._panel.error_message.connect(self.error_message.emit)
        self._panel.scan_completed.connect(self.scan_completed.emit)

    def show_window(self) -> None:
        if self._panel._current_source is None:
            self._panel.refresh_latest()
        self.show()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def refresh_latest(self) -> None:
        self._panel.refresh_latest()
        self.show_window()

    def open_scan(self, source: str | Path) -> None:
        self._panel.open_scan(source)
        self.show_window()

    def handle_scan_completed(self, dat_path: str) -> None:
        self._panel.handle_scan_completed(dat_path)
        self.show_window()

    @property
    def panel(self) -> PreviewPanel:
        return self._panel

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()


def _pixel_size_x_m_from_scan_range(scan_range_m: tuple[float, float] | None, shape: tuple[int, int]) -> float:
    if scan_range_m is None:
        return 1.0
    return float(scan_range_m[0]) / max(int(shape[1]), 1)


def _pixel_size_y_m_from_scan_range(scan_range_m: tuple[float, float] | None, shape: tuple[int, int]) -> float:
    if scan_range_m is None:
        return 1.0
    return float(scan_range_m[1]) / max(int(shape[0]), 1)


def _pixel_size_m_from_scan_range(scan_range_m: tuple[float, float] | None, shape: tuple[int, int]) -> float:
    return float(
        (_pixel_size_x_m_from_scan_range(scan_range_m, shape) * _pixel_size_y_m_from_scan_range(scan_range_m, shape)) ** 0.5
    )


def _particles_to_preview_rows(
    particles: list[Particle],
    scan_range_m: tuple[float, float] | None,
    shape: tuple[int, int],
) -> list[PreviewFeatureRow]:
    if not particles:
        return []
    pixel_size_x_m = _pixel_size_x_m_from_scan_range(scan_range_m, shape)
    pixel_size_y_m = _pixel_size_y_m_from_scan_range(scan_range_m, shape)
    ny, nx = int(shape[0]), int(shape[1])
    rows: list[PreviewFeatureRow] = []
    for particle in particles:
        x_m = float(particle.centroid_x_m)
        y_m = float(particle.centroid_y_m)
        rows.append(
            PreviewFeatureRow(
                index=int(particle.index),
                source="segmentation",
                x_px=x_m / pixel_size_x_m if pixel_size_x_m > 0 else 0.0,
                y_px=y_m / pixel_size_y_m if pixel_size_y_m > 0 else 0.0,
                x_nm=x_m * 1e9,
                y_nm=y_m * 1e9,
                dx_nm=(x_m - (nx / 2.0) * pixel_size_x_m) * 1e9,
                dy_nm=(y_m - (ny / 2.0) * pixel_size_y_m) * 1e9,
                x_m=x_m,
                y_m=y_m,
                dx_m=x_m - (nx / 2.0) * pixel_size_x_m,
                dy_m=y_m - (ny / 2.0) * pixel_size_y_m,
                score=float(particle.mean_height),
                bbox_px=tuple(int(v) for v in particle.bbox_px),
                label=f"region-{particle.index + 1}",
                area_nm2=float(particle.area_nm2),
            )
        )
    return rows


def _classify_particles_auto(
    arr: np.ndarray,
    particles: list[Particle],
    samples: list[tuple[str, Particle]],
    *,
    encoder: str,
    threshold_method: str,
) -> tuple[list[object], str]:
    encoder_key = (encoder or "raw").strip().lower()
    if encoder_key != "auto":
        return classify_particles(
            arr,
            particles,
            samples,
            encoder=encoder_key,
            threshold_method=threshold_method,
            crop_size_px=48,
        ), encoder_key

    candidates: list[tuple[float, str, list[object]]] = []
    for candidate_encoder in ("raw", "pca_kmeans"):
        try:
            classifs = classify_particles(
                arr,
                particles,
                samples,
                encoder=candidate_encoder,
                threshold_method=threshold_method,
                crop_size_px=48,
            )
        except Exception as exc:
            log.debug("Preview classification with %s failed: %s", candidate_encoder, exc)
            continue
        candidates.append((_classification_score(classifs), candidate_encoder, classifs))

    if not candidates:
        raise RuntimeError("classification failed for all encoders")
    score, used_encoder, classifs = max(candidates, key=lambda item: item[0])
    log.debug("Preview classification selected encoder %s (score %.3f)", used_encoder, score)
    return classifs, used_encoder


def _classification_score(classifs: list[object]) -> float:
    score = 0.0
    for item in classifs:
        class_name = str(getattr(item, "class_name", ""))
        similarity = float(getattr(item, "similarity", 0.0))
        if class_name and class_name != "other":
            score += 1.0 + similarity
        else:
            score -= 0.25
    return score


def _generate_unimr_segmentation_preview(
    plane: np.ndarray,
    scan_range_m: tuple[float, float] | None,
    *,
    threshold_value: int,
    min_area_slider: int,
    max_area_slider: int,
    invert: bool,
    feature_mode: str = "segmentation_first",
) -> tuple[list[Particle], list[PreviewFeatureRow], list[str]]:
    arr = np.asarray(plane, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError("segmentation preview requires a 2-D plane")

    mode = str(feature_mode or "segmentation_first").strip().lower()
    if mode == "points_only":
        params = PreviewAnalysisParams(feature_mode="points_only")
        rows, warnings = detect_preview_features(arr, scan_range_m=scan_range_m, params=params)
        return [], rows, warnings

    cv2 = cv2_module("preview segmentation")
    u8 = to_uint8_for_cv(arr, clip_low=1.0, clip_high=99.0)
    flag = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
    _, thresh = cv2.threshold(np.asarray(u8, dtype=np.uint8), int(threshold_value), 255, flag)
    contours, _ = cv2.findContours(np.asarray(thresh, dtype=np.uint8), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    total_area = float(arr.shape[0] * arr.shape[1])
    min_area_px = max(0.0, total_area * (float(min_area_slider) / 100000.0))
    max_area_px = total_area if int(max_area_slider) <= 0 else total_area * (float(max_area_slider) / 100000.0)

    pixel_size_x_m = _pixel_size_x_m_from_scan_range(scan_range_m, arr.shape)
    pixel_size_y_m = _pixel_size_y_m_from_scan_range(scan_range_m, arr.shape)
    particles: list[Particle] = []
    rows: list[PreviewFeatureRow] = []

    for contour in contours:
        area_px = float(cv2.contourArea(contour))
        if area_px < min_area_px or area_px > max_area_px:
            continue

        mask = np.zeros(arr.shape, dtype=np.uint8)
        cv2.drawContours(mask, [contour], -1, color=1, thickness=-1)
        ys, xs = np.where(mask > 0)
        if xs.size == 0 or ys.size == 0:
            continue

        x0 = int(xs.min())
        x1 = int(xs.max())
        y0 = int(ys.min())
        y1 = int(ys.max())
        area_pix = int(mask.sum())
        area_m2 = float(area_pix * pixel_size_x_m * pixel_size_y_m)
        area_nm2 = area_m2 * 1e18
        x_m = float(xs.mean()) * pixel_size_x_m
        y_m = float(ys.mean()) * pixel_size_y_m

        finite = arr[ys, xs]
        finite = finite[np.isfinite(finite)]
        if finite.size == 0:
            mean_h = max_h = min_h = float("nan")
        else:
            mean_h = float(finite.mean())
            max_h = float(finite.max())
            min_h = float(finite.min())

        particle = Particle(
            index=len(particles),
            centroid_x_m=x_m,
            centroid_y_m=y_m,
            area_m2=area_m2,
            area_nm2=area_nm2,
            bbox_m=(x0 * pixel_size_x_m, y0 * pixel_size_y_m, (x1 + 1) * pixel_size_x_m, (y1 + 1) * pixel_size_y_m),
            bbox_px=(x0, y0, x1 + 1, y1 + 1),
            mean_height=mean_h,
            max_height=max_h,
            min_height=min_h,
            n_pixels=area_pix,
            contour_xy_m=[(float(pt[0][0]) * pixel_size_x_m, float(pt[0][1]) * pixel_size_y_m) for pt in contour],
        )
        particles.append(particle)
        rows.append(
            PreviewFeatureRow(
                index=particle.index,
                source="segmentation",
                x_px=x_m / pixel_size_x_m if pixel_size_x_m > 0 else 0.0,
                y_px=y_m / pixel_size_y_m if pixel_size_y_m > 0 else 0.0,
                x_nm=x_m * 1e9,
                y_nm=y_m * 1e9,
                dx_nm=(x_m - (arr.shape[1] / 2.0) * pixel_size_x_m) * 1e9,
                dy_nm=(y_m - (arr.shape[0] / 2.0) * pixel_size_y_m) * 1e9,
                x_m=x_m,
                y_m=y_m,
                dx_m=x_m - (arr.shape[1] / 2.0) * pixel_size_x_m,
                dy_m=y_m - (arr.shape[0] / 2.0) * pixel_size_y_m,
                score=mean_h,
                bbox_px=particle.bbox_px,
                label=f"region-{particle.index + 1}",
                area_nm2=area_nm2,
            )
        )

    warnings: list[str] = []
    if rows:
        return particles, rows, warnings

    warnings.append("segmentation returned no features")
    if mode == "segmentation_only":
        return particles, rows, warnings

    params = PreviewAnalysisParams(feature_mode="points_only")
    fallback_rows, fallback_warnings = detect_preview_features(arr, scan_range_m=scan_range_m, params=params)
    warnings.extend(fallback_warnings)
    return particles, fallback_rows, warnings
