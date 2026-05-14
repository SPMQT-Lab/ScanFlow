"""ScanFlow Survey panel — auto-discover features in a wide scan, zoom on each.

Workflow:
  1. Configure wide-scan parameters (size, pixels, speed).
  2. Configure per-feature zoom defaults (iterations, size multiplier, limits).
  3. Set the shared bias/setpoint and output folder.
  4. Start. ScanFlow takes the wide scan, segments bright features, then runs
     N iterative zoom scans per feature with re-centering between iterations.
  5. After the run, export to PPTX (optionally hand off to ProbeFlow).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QGridLayout, QLabel, QDoubleSpinBox,
    QSpinBox, QPushButton, QLineEdit, QFileDialog, QMessageBox, QProgressBar,
)
from PySide6.QtCore import Signal

from scanflow.core import STMClient
from scanflow.automation import (
    MeasurementRecipe, SurveyStep, SurveyConfig,
    AutomationRunner, RunnerState,
)


class SurveyPanel(QWidget):
    """Configure and run an auto-discover-and-zoom campaign."""

    log_message = Signal(str)
    error_message = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._runner: AutomationRunner | None = None
        self._last_manifest_path: Path | None = None
        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.addWidget(self._build_wide_group())
        root.addWidget(self._build_zoom_group())
        root.addWidget(self._build_discovery_group())
        root.addWidget(self._build_shared_group())
        root.addWidget(self._build_run_group())

        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        root.addWidget(self._progress)

        self._status = QLabel("Ready")
        root.addWidget(self._status)
        root.addStretch(1)

    def _build_wide_group(self) -> QGroupBox:
        box = QGroupBox("Wide scan (overview)")
        g = QGridLayout(box)

        g.addWidget(QLabel("Size X (nm)"), 0, 0)
        self._wide_x = QDoubleSpinBox()
        self._wide_x.setRange(1.0, 50000.0)
        self._wide_x.setDecimals(2)
        self._wide_x.setValue(120.0)
        g.addWidget(self._wide_x, 0, 1)

        g.addWidget(QLabel("Size Y (nm)"), 0, 2)
        self._wide_y = QDoubleSpinBox()
        self._wide_y.setRange(1.0, 50000.0)
        self._wide_y.setDecimals(2)
        self._wide_y.setValue(120.0)
        g.addWidget(self._wide_y, 0, 3)

        g.addWidget(QLabel("Pixels"), 1, 0)
        self._wide_pixels = QSpinBox()
        self._wide_pixels.setRange(64, 4096)
        self._wide_pixels.setValue(512)
        g.addWidget(self._wide_pixels, 1, 1)

        g.addWidget(QLabel("Speed (nm/s)"), 1, 2)
        self._wide_speed = QDoubleSpinBox()
        self._wide_speed.setRange(0.1, 5000.0)
        self._wide_speed.setDecimals(1)
        self._wide_speed.setValue(100.0)
        g.addWidget(self._wide_speed, 1, 3)
        return box

    def _build_zoom_group(self) -> QGroupBox:
        box = QGroupBox("Per-feature zoom")
        g = QGridLayout(box)

        g.addWidget(QLabel("Pixels"), 0, 0)
        self._zoom_pixels = QSpinBox()
        self._zoom_pixels.setRange(32, 2048)
        self._zoom_pixels.setValue(256)
        g.addWidget(self._zoom_pixels, 0, 1)

        g.addWidget(QLabel("Speed (nm/s)"), 0, 2)
        self._zoom_speed = QDoubleSpinBox()
        self._zoom_speed.setRange(0.1, 2000.0)
        self._zoom_speed.setDecimals(1)
        self._zoom_speed.setValue(20.0)
        g.addWidget(self._zoom_speed, 0, 3)

        g.addWidget(QLabel("Iterations"), 1, 0)
        self._zoom_iters = QSpinBox()
        self._zoom_iters.setRange(1, 10)
        self._zoom_iters.setValue(3)
        self._zoom_iters.setToolTip("Number of zoom scans per feature; "
                                    "extra iterations refine centering.")
        g.addWidget(self._zoom_iters, 1, 1)

        g.addWidget(QLabel("Size × feature"), 1, 2)
        self._size_mult = QDoubleSpinBox()
        self._size_mult.setRange(1.0, 10.0)
        self._size_mult.setDecimals(2)
        self._size_mult.setValue(2.0)
        self._size_mult.setToolTip("Zoom frame = (feature size) × this multiplier.")
        g.addWidget(self._size_mult, 1, 3)

        g.addWidget(QLabel("Min zoom (nm)"), 2, 0)
        self._zoom_min = QDoubleSpinBox()
        self._zoom_min.setRange(0.5, 100.0)
        self._zoom_min.setDecimals(2)
        self._zoom_min.setValue(3.0)
        g.addWidget(self._zoom_min, 2, 1)

        g.addWidget(QLabel("Max zoom (nm)"), 2, 2)
        self._zoom_max = QDoubleSpinBox()
        self._zoom_max.setRange(1.0, 500.0)
        self._zoom_max.setDecimals(2)
        self._zoom_max.setValue(30.0)
        g.addWidget(self._zoom_max, 2, 3)
        return box

    def _build_discovery_group(self) -> QGroupBox:
        box = QGroupBox("Feature discovery")
        g = QGridLayout(box)
        g.addWidget(QLabel("Min feature (nm)"), 0, 0)
        self._min_feat = QDoubleSpinBox()
        self._min_feat.setRange(0.1, 50.0)
        self._min_feat.setDecimals(2)
        self._min_feat.setValue(0.8)
        g.addWidget(self._min_feat, 0, 1)

        g.addWidget(QLabel("Max feature (nm)"), 0, 2)
        self._max_feat = QDoubleSpinBox()
        self._max_feat.setRange(1.0, 200.0)
        self._max_feat.setDecimals(2)
        self._max_feat.setValue(20.0)
        g.addWidget(self._max_feat, 0, 3)

        g.addWidget(QLabel("Cluster merge (nm)"), 1, 0)
        self._merge = QDoubleSpinBox()
        self._merge.setRange(0.0, 5.0)
        self._merge.setDecimals(2)
        self._merge.setValue(0.5)
        self._merge.setToolTip(
            "Morphological closing radius. Fuses near-adjacent bright spots "
            "into one cluster feature."
        )
        g.addWidget(self._merge, 1, 1)

        g.addWidget(QLabel("Max features"), 1, 2)
        self._max_count = QSpinBox()
        self._max_count.setRange(1, 200)
        self._max_count.setValue(30)
        g.addWidget(self._max_count, 1, 3)

        g.addWidget(QLabel("Edge margin (px)"), 2, 0)
        self._edge_margin = QSpinBox()
        self._edge_margin.setRange(0, 200)
        self._edge_margin.setValue(16)
        self._edge_margin.setToolTip(
            "Features whose bounding box lies within this many pixels of the "
            "frame edge are ignored — their zooms would clip."
        )
        g.addWidget(self._edge_margin, 2, 1)
        return box

    def _build_shared_group(self) -> QGroupBox:
        box = QGroupBox("Tunneling and output")
        g = QGridLayout(box)
        g.addWidget(QLabel("Bias (V)"), 0, 0)
        self._bias = QDoubleSpinBox()
        self._bias.setRange(-10.0, 10.0)
        self._bias.setDecimals(4)
        self._bias.setValue(0.1)
        g.addWidget(self._bias, 0, 1)

        g.addWidget(QLabel("Setpoint (pA)"), 0, 2)
        self._setpoint = QDoubleSpinBox()
        self._setpoint.setRange(0.001, 1e6)
        self._setpoint.setDecimals(3)
        self._setpoint.setValue(50.0)
        g.addWidget(self._setpoint, 0, 3)

        g.addWidget(QLabel("Campaign name"), 1, 0)
        self._name = QLineEdit("Survey")
        g.addWidget(self._name, 1, 1, 1, 3)

        g.addWidget(QLabel("Output folder"), 2, 0)
        self._output = QLineEdit()
        self._output.setPlaceholderText("(required for PPTX export)")
        g.addWidget(self._output, 2, 1, 1, 2)
        pick = QPushButton("Browse…")
        pick.clicked.connect(self._pick_folder)
        g.addWidget(pick, 2, 3)
        return box

    def _build_run_group(self) -> QGroupBox:
        box = QGroupBox("Run")
        g = QGridLayout(box)
        self._start_btn = QPushButton("Start survey")
        self._start_btn.clicked.connect(self._start_run)
        g.addWidget(self._start_btn, 0, 0)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_run)
        g.addWidget(self._stop_btn, 0, 1)

        self._export_btn = QPushButton("Open in ProbeFlow")
        self._export_btn.setEnabled(False)
        self._export_btn.setToolTip(
            "Hand off the survey to ProbeFlow for visual enhancement of each "
            "feature, then export to PPTX from there."
        )
        self._export_btn.clicked.connect(self._open_in_probeflow)
        g.addWidget(self._export_btn, 0, 2)
        return box

    # ------------------------------------------------------------------

    def load_from_stm(self) -> None:
        """Read current Createc scan parameters and pre-fill the wide-scan fields.

        Called by the main window after a successful Connect so the campaign
        starts from whatever you've already framed up in STMAFM rather than
        the panel's hard-coded defaults.
        """
        try:
            params = self._stm.scan.read()
        except Exception:
            return
        for w in (self._wide_x, self._wide_y, self._wide_pixels,
                  self._wide_speed, self._bias, self._setpoint):
            w.blockSignals(True)
        try:
            self._wide_x.setValue(float(params.size_nm[0]))
            self._wide_y.setValue(float(params.size_nm[1]))
            self._wide_pixels.setValue(int(params.pixels[0]))
            self._wide_speed.setValue(float(params.speed_nm_s))
            self._bias.setValue(float(params.bias_V))
            self._setpoint.setValue(float(params.setpoint_A) * 1e12)
        finally:
            for w in (self._wide_x, self._wide_y, self._wide_pixels,
                      self._wide_speed, self._bias, self._setpoint):
                w.blockSignals(False)
        self.log_message.emit(
            f"Survey loaded from Createc: "
            f"X={params.size_nm[0]:.1f} nm  Y={params.size_nm[1]:.1f} nm  "
            f"pixels={params.pixels[0]}  speed={params.speed_nm_s:.1f} nm/s"
        )

    def _build_config(self) -> SurveyConfig:
        return SurveyConfig(
            wide_size_nm=(self._wide_x.value(), self._wide_y.value()),
            wide_pixels=(self._wide_pixels.value(), self._wide_pixels.value()),
            wide_speed_nm_s=self._wide_speed.value(),
            zoom_pixels=(self._zoom_pixels.value(), self._zoom_pixels.value()),
            zoom_speed_nm_s=self._zoom_speed.value(),
            zoom_iterations=self._zoom_iters.value(),
            size_multiplier=self._size_mult.value(),
            min_zoom_nm=self._zoom_min.value(),
            max_zoom_nm=self._zoom_max.value(),
            min_feature_nm=self._min_feat.value(),
            max_feature_nm=self._max_feat.value(),
            merge_distance_nm=self._merge.value(),
            max_features=self._max_count.value(),
            edge_margin_px=self._edge_margin.value(),
            bias_V=self._bias.value(),
            setpoint_A=self._setpoint.value() * 1e-12,
            output_folder=self._output.text(),
            name=self._name.text() or "Survey",
        )

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Output folder")
        if path:
            self._output.setText(path)

    def _start_run(self) -> None:
        if not self._stm.connected and not self._stm.is_mock:
            QMessageBox.warning(self, "STM not connected",
                                "Connect to the STM (or Mock) first.")
            return
        cfg = self._build_config()

        recipe = MeasurementRecipe(name=cfg.name)
        recipe.add_step(SurveyStep(config=cfg, label=cfg.name))

        confirm = QMessageBox.question(
            self, "Start campaign",
            f"<b>{cfg.name}</b><br><br>"
            f"Wide field: {cfg.wide_size_nm[0]:.0f} × {cfg.wide_size_nm[1]:.0f} nm "
            f"at {cfg.wide_speed_nm_s:.0f} nm/s<br>"
            f"Zoom: {cfg.zoom_iterations} iterations per feature, "
            f"size × {cfg.size_multiplier:.1f}<br>"
            f"Up to {cfg.max_features} features<br>"
            f"Bias {cfg.bias_V:.3f} V, setpoint {cfg.setpoint_A*1e12:.1f} pA<br><br>"
            f"Start?"
        )
        if confirm != QMessageBox.Yes:
            return

        self._runner = AutomationRunner(self._stm, recipe)
        self._runner.progress.connect(self._on_progress)
        self._runner.state_changed.connect(self._on_state)
        self._runner.error.connect(lambda m: self.error_message.emit(m))
        self._runner.survey_discovered.connect(self._on_discovered)
        self._runner.survey_feature_started.connect(self._on_feature_started)
        self._runner.survey_feature_done.connect(self._on_feature_done)
        self._runner.survey_finished.connect(self._on_survey_finished)
        self._runner.z_stability.connect(self._on_z_stability)
        self._runner.scan_completed.connect(
            lambda p: self.log_message.emit(f"saved: {p}")
        )
        self._runner.start()

        self._progress.setMaximum(0)  # busy/indeterminate during survey
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

    def _stop_run(self) -> None:
        if self._runner:
            self._runner.stop()
            self._status.setText("Stopping…")

    def _open_in_probeflow(self) -> None:
        if self._last_manifest_path is None or not self._last_manifest_path.exists():
            QMessageBox.warning(self, "No survey", "Run a survey first.")
            return
        from scanflow.io import open_survey_in_probeflow
        if open_survey_in_probeflow(self._last_manifest_path):
            self.log_message.emit(
                f"Handing off to ProbeFlow: {self._last_manifest_path}"
            )
        else:
            QMessageBox.warning(
                self, "ProbeFlow not found",
                "Could not launch ProbeFlow automatically.<br><br>"
                "Open ProbeFlow yourself and load:<br>"
                f"<code>{self._last_manifest_path}</code>"
            )

    # ── runner callbacks ───────────────────────────────────────────────

    def _on_progress(self, current: int, total: int, label: str) -> None:
        if total > 0:
            self._progress.setMaximum(total)
            self._progress.setValue(current)
        self._status.setText(label)

    def _on_state(self, state) -> None:
        self._status.setText(state.name.capitalize())
        if state in (RunnerState.FINISHED, RunnerState.ERROR, RunnerState.IDLE):
            self._start_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._export_btn.setEnabled(self._last_manifest_path is not None)

    def _on_discovered(self, n: int) -> None:
        self.log_message.emit(f"Survey: discovered {n} feature(s) in wide scan")
        if n > 0:
            self._progress.setMaximum(n)
            self._progress.setValue(0)

    def _on_feature_started(self, idx: int, total: int,
                            size_nm: float, dx_nm: float, dy_nm: float) -> None:
        self._progress.setValue(idx - 1)
        self.log_message.emit(
            f"[{idx:02d}/{total}] feature ≈ {size_nm:.2f} nm  "
            f"tip moves ΔX={dx_nm:+.2f} nm, ΔY={dy_nm:+.2f} nm"
        )

    def _on_feature_done(self, record) -> None:
        self._progress.setValue(record.index)
        iters = "  ".join(
            f"iter{i+1}: ({dx:+.2f}, {dy:+.2f}) Å"
            for i, (dx, dy) in enumerate(record.drift_log_angstrom)
        )
        self.log_message.emit(
            f"[{record.index:02d}] done — zoom {record.zoom_size_nm[0]:.1f} nm — {iters}"
        )

    def _on_z_stability(self, metrics) -> None:
        from scanflow.automation.scan_metrics import format_z_stability
        self.log_message.emit(format_z_stability(metrics))

    def _on_survey_finished(self, manifest_path: str) -> None:
        self._last_manifest_path = Path(manifest_path)
        self._export_btn.setEnabled(True)
        self.log_message.emit(f"Survey finished — manifest: {manifest_path}")
        self._status.setText("Survey complete")
