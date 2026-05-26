"""ScanFlow preview tab backed by ProbeFlow analysis kernels."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QFrame,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QCheckBox,
)

from probeflow.analysis.preview import (
    PreviewAnalysisParams,
    PreviewFeatureRow,
    apply_preview_background,
    detect_preview_features,
)
from probeflow.core.scan_loader import load_scan

from scanflow.core import STMClient, SafetyConfig, SafetyMonitor, TipMotionManager, ScanParams

log = logging.getLogger(__name__)


@dataclass
class _PreviewState:
    source_path: Path
    scan: Any
    plane_index: int
    raw_plane: np.ndarray
    corrected_plane: np.ndarray | None = None
    background_image: np.ndarray | None = None
    feature_rows: tuple[PreviewFeatureRow, ...] = ()
    warnings: tuple[str, ...] = ()


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


class _FeatureDetectWorker(QThread):
    result_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, plane: np.ndarray, scan_range_m: tuple[float, float] | None, params: PreviewAnalysisParams) -> None:
        super().__init__()
        self._plane = np.asarray(plane, dtype=np.float64)
        self._scan_range_m = scan_range_m
        self._params = params

    def run(self) -> None:
        try:
            rows, warnings = detect_preview_features(
                self._plane,
                scan_range_m=self._scan_range_m,
                params=self._params,
            )
        except Exception as exc:  # pragma: no cover - exercised via panel tests
            log.exception("Preview feature detection failed")
            self.failed.emit(str(exc))
            return
        self.result_ready.emit((rows, warnings))


class _FeatureScanWorker(QThread):
    scan_saved = Signal(str)
    failed = Signal(str)
    progress = Signal(int, int, str)

    def __init__(self, stm: STMClient, source_path: Path, targets: list[PreviewFeatureRow]) -> None:
        super().__init__()
        self._stm = stm
        self._source_path = Path(source_path)
        self._targets = list(targets)

    def run(self) -> None:
        try:
            if not self._stm.bind_thread():
                raise RuntimeError("could not bind STM to preview scan worker thread")
            motion = TipMotionManager(
                self._stm,
                safety=SafetyMonitor(
                    SafetyConfig(
                        max_current_A=1e-9,
                        enable_current_check=True,
                        retract_on_violation_nm=10.0,
                    )
                ),
            )
            current = self._stm.scan.read()
            base_folder = self._source_path.parent
            base_stem = self._source_path.stem
            total = len(self._targets)
            for i, row in enumerate(self._targets, start=1):
                self.progress.emit(i, total, f"Target {row.index + 1}/{total}")
                motion.assert_safe_to_move()
                move = motion.move_relative_nm(
                    row.dx_nm,
                    row.dy_nm,
                    reason=f"preview follow-up target {row.index + 1}",
                    settle_s=3.0,
                )
                if not move.ok:
                    raise RuntimeError(
                        f"target {row.index + 1} move failed: {move.reason} "
                        + ("; ".join(move.warnings) if move.warnings else "")
                    )

                params = ScanParams(
                    bias_V=current.bias_V,
                    setpoint_A=current.setpoint_A,
                    size_nm=current.size_nm,
                    speed_nm_s=current.speed_nm_s,
                    pixels=current.pixels,
                    rotation_deg=current.rotation_deg,
                    const_height=current.const_height,
                    channels=current.channels,
                    preamp_exponent=current.preamp_exponent,
                    memo=f"preview target {row.index + 1}",
                )
                self._stm.scan.apply(params)
                target = _unique_dat_path(base_folder, f"{base_stem}_feature_{row.index + 1:02d}")
                timeout_s = _estimate_scan_timeout(params)
                saved = self._stm.scan.scan_and_save(str(target), timeout_s=timeout_s)
                if saved is None:
                    raise RuntimeError(f"scan timed out for target {row.index + 1}")
                self.scan_saved.emit(str(saved))
        except Exception as exc:  # pragma: no cover - exercised via panel tests
            log.exception("Preview follow-up scan failed")
            self.failed.emit(str(exc))
        finally:
            try:
                self._stm.unbind_thread()
            except Exception:
                pass


class _PreviewImageView(QWidget):
    """Single-surface image viewer with feature overlays."""

    feature_clicked = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._title = QLabel("<b>Preview image</b>")
        self._plot = pg.PlotWidget()
        self._plot.setBackground("w")
        self._plot.setAspectLocked(True)
        self._plot.invertY(True)
        self._plot.showGrid(x=False, y=False)
        self._plot.hideAxis("bottom")
        self._plot.hideAxis("left")

        self._image = pg.ImageItem(axisOrder="row-major")
        self._plot.addItem(self._image)

        self._all_overlay = pg.ScatterPlotItem(
            size=8,
            pen=pg.mkPen("#1565c0", width=1.2),
            brush=pg.mkBrush(21, 101, 192, 90),
        )
        self._selected_overlay = pg.ScatterPlotItem(
            size=12,
            pen=pg.mkPen("#2e7d32", width=1.4),
            brush=pg.mkBrush(46, 125, 50, 160),
        )
        self._plot.addItem(self._all_overlay)
        self._plot.addItem(self._selected_overlay)
        self._plot.scene().sigMouseClicked.connect(self._on_scene_clicked)

        self._caption = QLabel("")
        self._caption.setWordWrap(True)
        self._feature_rows: list[PreviewFeatureRow] = []

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
        selected_rows: list[PreviewFeatureRow] | None = None,
    ) -> None:
        if state is None:
            self._image.clear()
            self._all_overlay.setData([])
            self._selected_overlay.setData([])
            self._feature_rows = []
            self._caption.setText("No preview loaded.")
            return

        image, label = self._select_image(state, display_mode)
        arr = np.asarray(image, dtype=np.float64)
        if arr.ndim != 2:
            self._image.clear()
            self._caption.setText("Preview image is not 2-D.")
        else:
            self._image.setImage(arr, autoLevels=True)
            self._caption.setText(label)

        self._title.setText(f"<b>{state.source_path.name}</b>")
        self._feature_rows = list(state.feature_rows)
        self._all_overlay.setData(
            [{"pos": (row.x_px, row.y_px), "data": row.index} for row in state.feature_rows]
        )
        self._selected_overlay.setData(
            [{"pos": (row.x_px, row.y_px), "data": row.index} for row in (selected_rows or [])]
        )

    def _on_scene_clicked(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._feature_rows:
            return
        pos = self._plot.plotItem.vb.mapSceneToView(event.scenePos())
        x = float(pos.x())
        y = float(pos.y())
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
        self._feature_worker: _FeatureDetectWorker | None = None
        self._scan_worker: _FeatureScanWorker | None = None
        self._building_table = False
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        self._status = QLabel("Ready")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._viewer = _PreviewImageView()
        self._viewer.feature_clicked.connect(self._toggle_feature_selection)

        controls_panel = QWidget()
        controls_panel.setObjectName("previewControls")
        controls_panel.setStyleSheet("""
            QWidget#previewControls {
                background: #f7f9fc;
            }
            QWidget#previewControls QLabel {
                color: #111;
            }
            QWidget#previewControls QLineEdit,
            QWidget#previewControls QComboBox,
            QWidget#previewControls QDoubleSpinBox,
            QWidget#previewControls QSpinBox {
                background-color: #ffffff;
                color: #111;
                border: 1px solid #9a9a9a;
                border-radius: 3px;
                padding: 2px 6px;
                min-height: 24px;
            }
            QWidget#previewControls QCheckBox {
                color: #111;
            }
            QWidget#previewControls QPushButton {
                min-height: 28px;
            }
        """)
        controls = QVBoxLayout(controls_panel)
        controls.setContentsMargins(8, 8, 8, 8)
        controls.setSpacing(10)

        self._background_btn = QPushButton("Apply background")
        self._background_btn.clicked.connect(self._apply_background)
        self._refresh_btn = QPushButton("Refresh latest")
        self._refresh_btn.clicked.connect(self.refresh_latest)

        self._features_btn = QPushButton("Detect features")
        self._features_btn.clicked.connect(self._detect_features)
        self._scan_selected_btn = QPushButton("Scan selected")
        self._scan_selected_btn.clicked.connect(self._scan_selected_features)
        for button in (
            self._refresh_btn,
            self._background_btn,
            self._features_btn,
            self._scan_selected_btn,
        ):
            controls.addWidget(button)

        controls.addWidget(self._section_label("Source"))
        source_form = QFormLayout()
        source_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        source_form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        source_form.setHorizontalSpacing(10)
        source_form.setVerticalSpacing(8)
        source_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        controls.addLayout(source_form)

        self._folder_edit = QLineEdit()
        self._folder_edit.setPlaceholderText("Folder containing completed .dat scans")
        self._style_control(self._folder_edit)
        source_form.addRow("Scan folder", self._folder_edit)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._pick_folder)
        self._style_button(browse_btn)
        source_form.addRow("", browse_btn)

        controls.addWidget(self._section_label("View"))
        view_form = QFormLayout()
        view_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        view_form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        view_form.setHorizontalSpacing(10)
        view_form.setVerticalSpacing(8)
        view_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        controls.addLayout(view_form)

        self._plane_spin = QSpinBox()
        self._plane_spin.setRange(0, 0)
        self._plane_spin.valueChanged.connect(self._plane_changed)
        self._style_control(self._plane_spin)
        view_form.addRow("Plane", self._plane_spin)

        self._display_mode = QComboBox()
        self._display_mode.addItem("Raw", "raw")
        self._display_mode.addItem("Background-corrected", "background_corrected")
        self._display_mode.addItem("Background image", "background_image")
        self._display_mode.currentIndexChanged.connect(self._display_mode_changed)
        self._style_control(self._display_mode)
        view_form.addRow("View mode", self._display_mode)

        controls.addWidget(self._section_label("Processing"))
        proc_form = QFormLayout()
        proc_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        proc_form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        proc_form.setHorizontalSpacing(10)
        proc_form.setVerticalSpacing(8)
        proc_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        controls.addLayout(proc_form)

        self._background_mode = QComboBox()
        self._background_mode.addItems(["linear", "poly2", "poly3", "low_pass"])
        self._style_control(self._background_mode)
        proc_form.addRow("Background", self._background_mode)

        self._background_strength = QDoubleSpinBox()
        self._background_strength.setRange(0.5, 50.0)
        self._background_strength.setDecimals(1)
        self._background_strength.setValue(5.0)
        self._style_control(self._background_strength)
        proc_form.addRow("Blur", self._background_strength)

        self._feature_mode = QComboBox()
        self._feature_mode.addItems([
            "segmentation_first",
            "segmentation_only",
            "points_only",
        ])
        self._style_control(self._feature_mode)
        proc_form.addRow("Feature mode", self._feature_mode)

        self._threshold_mode = QComboBox()
        self._threshold_mode.addItems(["otsu", "manual", "adaptive"])
        self._style_control(self._threshold_mode)
        proc_form.addRow("Threshold", self._threshold_mode)

        self._manual_threshold = QDoubleSpinBox()
        self._manual_threshold.setRange(0.0, 255.0)
        self._manual_threshold.setDecimals(0)
        self._manual_threshold.setValue(128.0)
        self._style_control(self._manual_threshold)
        proc_form.addRow("Manual (0-255)", self._manual_threshold)

        self._invert_features = QCheckBox("Invert (dark features)")
        controls.addWidget(self._invert_features)

        self._min_area_nm2 = QDoubleSpinBox()
        self._min_area_nm2.setRange(0.0, 1e6)
        self._min_area_nm2.setDecimals(2)
        self._min_area_nm2.setValue(0.5)
        self._style_control(self._min_area_nm2)
        proc_form.addRow("min area (nm^2)", self._min_area_nm2)

        self._max_area_nm2 = QDoubleSpinBox()
        self._max_area_nm2.setRange(0.0, 1e9)
        self._max_area_nm2.setDecimals(2)
        self._max_area_nm2.setValue(0.0)
        self._style_control(self._max_area_nm2)
        proc_form.addRow("max area (nm^2)", self._max_area_nm2)

        self._sigma_clip = QDoubleSpinBox()
        self._sigma_clip.setRange(0.0, 10.0)
        self._sigma_clip.setDecimals(1)
        self._sigma_clip.setValue(2.0)
        self._style_control(self._sigma_clip)
        proc_form.addRow("sigma-clip", self._sigma_clip)

        controls.addStretch(1)

        controls_widget = QScrollArea()
        controls_widget.setWidgetResizable(True)
        controls_widget.setFrameShape(QFrame.Shape.NoFrame)
        controls_widget.setWidget(controls_panel)
        controls_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        controls_widget.setMinimumWidth(470)
        controls_widget.setMaximumWidth(620)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.addWidget(self._viewer)
        body.addWidget(controls_widget)
        body.setStretchFactor(0, 4)
        body.setStretchFactor(1, 1)
        body.setSizes([1000, 360])
        root.addWidget(body, 3)

        table_box = QWidget()
        table_layout = QVBoxLayout(table_box)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(QLabel("<b>Detected features</b>"))
        self._feature_table = QTableWidget(0, 9)
        self._feature_table.setHorizontalHeaderLabels([
            "Use",
            "#",
            "Source",
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
        self._feature_table.setMinimumHeight(240)
        self._feature_table.itemChanged.connect(self._on_feature_table_item_changed)
        table_layout.addWidget(self._feature_table)

        table_buttons = QHBoxLayout()
        table_buttons.addStretch(1)
        select_all_btn = QPushButton("Select all")
        select_all_btn.clicked.connect(lambda: self._set_all_selected(True))
        table_buttons.addWidget(select_all_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: self._set_all_selected(False))
        table_buttons.addWidget(clear_btn)
        table_layout.addLayout(table_buttons)
        table_box.setMinimumHeight(300)
        root.addWidget(table_box, 2)

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
        self._current_source = latest
        self._load_scan(latest)

    def handle_scan_completed(self, dat_path: str) -> None:
        """Refresh the preview after any external scan completes."""
        path = Path(dat_path)
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
        self._folder_edit.setText(str(source.parent))

        plane_count = len(getattr(scan, "planes", []))
        self._plane_spin.blockSignals(True)
        try:
            self._plane_spin.setMaximum(max(0, plane_count - 1))
            self._plane_spin.setValue(min(int(self._plane_spin.value()), max(0, plane_count - 1)))
        finally:
            self._plane_spin.blockSignals(False)

        self._set_plane_state(self._plane_spin.value(), clear_analysis=True)
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
            threshold=str(self._threshold_mode.currentText()),
            manual_threshold=float(self._manual_threshold.value()),
            invert=bool(self._invert_features.isChecked()),
            min_area_nm2=float(self._min_area_nm2.value()),
            max_area_nm2=None if self._max_area_nm2.value() <= 0 else float(self._max_area_nm2.value()),
            size_sigma_clip=None if self._sigma_clip.value() <= 0 else float(self._sigma_clip.value()),
        )

    def _background_plane(self) -> np.ndarray:
        if self._current_state is None:
            raise RuntimeError("load a scan before applying background subtraction")
        return self._current_state.raw_plane

    def _apply_background(self) -> None:
        if self._current_state is None:
            QMessageBox.information(self, "No scan", "Load a scan before applying background subtraction.")
            return
        if self._background_worker is not None and self._background_worker.isRunning():
            QMessageBox.information(self, "Busy", "Background subtraction is already running.")
            return

        params = self._analysis_params()
        self._show_status(f"Applying background subtraction to {self._current_source.name}...")
        self._background_worker = _BackgroundWorker(self._current_state.raw_plane, params)
        self._background_worker.result_ready.connect(self._on_background_ready)
        self._background_worker.failed.connect(self._on_background_failed)
        self._background_worker.start()

    def _on_background_ready(self, result: object) -> None:
        corrected, background_image = result
        if self._current_state is None:
            return
        self._current_state.corrected_plane = np.asarray(corrected, dtype=np.float64)
        self._current_state.background_image = np.asarray(background_image, dtype=np.float64)
        self._current_state.feature_rows = ()
        self._current_state.warnings = ()
        self._clear_feature_table()
        self._set_display_mode("background_corrected")
        self._render_current_state()
        source_name = self._current_state.source_path.name
        self._show_status(f"{source_name}: background corrected")
        self.log_message.emit(f"Background subtraction applied to {source_name}")

    def _on_background_failed(self, message: str) -> None:
        self._show_status(f"Background subtraction failed: {message}")
        self.error_message.emit(message)

    def _detect_features(self) -> None:
        if self._current_state is None:
            QMessageBox.information(self, "No scan", "Load a scan before detecting features.")
            return
        if self._feature_worker is not None and self._feature_worker.isRunning():
            QMessageBox.information(self, "Busy", "Feature detection is already running.")
            return

        params = self._analysis_params()
        plane = self._current_state.corrected_plane
        if plane is None:
            plane = self._current_state.raw_plane
        self._show_status(f"Detecting features in {self._current_source.name}...")
        self._feature_worker = _FeatureDetectWorker(plane, self._current_scan.scan_range_m, params)
        self._feature_worker.result_ready.connect(self._on_features_ready)
        self._feature_worker.failed.connect(self._on_features_failed)
        self._feature_worker.start()

    def _on_features_ready(self, result: object) -> None:
        rows, warnings = result
        if self._current_state is None:
            return
        self._current_state.feature_rows = tuple(rows)
        self._current_state.warnings = tuple(warnings)
        self._populate_feature_table(self._current_state.feature_rows)
        self._render_current_state()
        if warnings:
            self._show_status(
                f"{self._current_source.name}: {len(rows)} features; " + " | ".join(warnings)
            )
        else:
            self._show_status(f"{self._current_source.name}: {len(rows)} features")
        self.log_message.emit(
            f"Feature detection completed for {self._current_source.name} "
            f"({len(rows)} feature(s))"
        )

    def _on_features_failed(self, message: str) -> None:
        self._show_status(f"Feature detection failed: {message}")
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
        self._current_state.feature_rows = ()
        self._current_state.warnings = ()
        if clear_analysis:
            self._current_state.corrected_plane = None
            self._current_state.background_image = None
            self._clear_feature_table()
        if plane_index < len(plane_names):
            self._viewer._title.setText(f"<b>{Path(self._current_scan.source_path).name} | {plane_names[plane_index]}</b>")
        if plane_index < len(plane_units):
            pass
        self._render_current_state()

    def _render_current_state(self) -> None:
        if self._current_state is None:
            self._viewer.set_state(None, display_mode="raw")
            return
        self._viewer.set_state(
            self._current_state,
            display_mode=self._current_display_mode(),
            selected_rows=self._selected_feature_rows(),
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

    def _populate_feature_table(self, rows: tuple[PreviewFeatureRow, ...]) -> None:
        self._building_table = True
        try:
            self._feature_table.setRowCount(len(rows))
            for row_idx, row in enumerate(rows):
                select_item = QTableWidgetItem()
                select_item.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                )
                select_item.setCheckState(Qt.CheckState.Unchecked)
                select_item.setData(Qt.ItemDataRole.UserRole, row.index)
                self._feature_table.setItem(row_idx, 0, select_item)

                values = [
                    str(row.index + 1),
                    row.source,
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
        selected: list[PreviewFeatureRow] = []
        rows = self._current_state.feature_rows
        for row_idx in range(self._feature_table.rowCount()):
            item = self._feature_table.item(row_idx, 0)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, int) and 0 <= data < len(rows):
                selected.append(rows[data])
        return selected

    def _set_all_selected(self, checked: bool) -> None:
        self._building_table = True
        try:
            state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            for row_idx in range(self._feature_table.rowCount()):
                item = self._feature_table.item(row_idx, 0)
                if item is not None:
                    item.setCheckState(state)
        finally:
            self._building_table = False
        self._render_current_state()

    def _toggle_feature_selection(self, feature_index: int) -> None:
        for row_idx in range(self._feature_table.rowCount()):
            item = self._feature_table.item(row_idx, 0)
            if item is None:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, int) and data == feature_index:
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
        self._render_current_state()

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

        self._scan_selected_btn.setEnabled(False)
        self._scan_worker = _FeatureScanWorker(self._stm, self._current_state.source_path, selected)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.scan_saved.connect(self._on_followup_scan_saved)
        self._scan_worker.failed.connect(self._on_followup_scan_failed)
        self._scan_worker.finished.connect(lambda: self._scan_selected_btn.setEnabled(True))
        self._scan_worker.start()
        self._show_status(f"Scanning {len(selected)} selected feature(s)...")

    def _on_scan_progress(self, idx: int, total: int, label: str) -> None:
        self._show_status(f"{label} ({idx}/{total})")

    def _on_followup_scan_saved(self, path: str) -> None:
        self.scan_completed.emit(path)
        self.handle_scan_completed(path)
        self.log_message.emit(f"Follow-up scan saved: {path}")

    def _on_followup_scan_failed(self, message: str) -> None:
        self.error_message.emit(message)
        self._show_status(f"Follow-up scan failed: {message}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose scan folder")
        if path:
            self._folder_edit.setText(path)
            self.refresh_latest()

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #111; font-weight: 700; margin-top: 4px;")
        return label

    def _style_control(self, widget: QWidget) -> None:
        widget.setStyleSheet("""
            QWidget {
                background-color: #ffffff;
                color: #111111;
                border: 1px solid #9a9a9a;
                border-radius: 3px;
                padding: 2px 6px;
                min-height: 24px;
            }
        """)

    def _style_button(self, widget: QPushButton) -> None:
        widget.setStyleSheet("""
            QPushButton {
                min-height: 28px;
                padding: 4px 8px;
            }
        """)

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


def _latest_dat_in_folder(folder: Path) -> Path | None:
    if not folder.exists():
        return None
    candidates = list(folder.glob("*.dat"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _unique_dat_path(folder: Path, stem: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / f"{stem}.dat"
    if not candidate.exists():
        return candidate
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = folder / f"{stem}_{stamp}.dat"
    if not candidate.exists():
        return candidate
    idx = 2
    while True:
        candidate = folder / f"{stem}_{stamp}_{idx}.dat"
        if not candidate.exists():
            return candidate
        idx += 1


def _estimate_scan_timeout(params: ScanParams) -> float:
    line_time = 2.0 * float(params.size_nm[0]) / max(float(params.speed_nm_s), 0.01)
    return max(120.0, line_time * int(params.pixels[1]) + 90.0)
