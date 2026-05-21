"""ScanFlow Mosaic panel — wide image + 3×3 zoom tiles + wide image.

Inputs (wide overview): size X/Y, pixels, speed.
Inputs (per-tile zoom): size X/Y (default wide/3), pixels (same as wide
unless overridden), speed, iterations.
Shared: bias, setpoint, settle. Output folder, campaign name.

Workflow when Start is pressed:
  1. Wide overview scan → wide_before.dat / wide_before.png
  2. Visit each of the 9 tiles row-major; per tile, run N iterations
     with drift correction between them; save every iteration's .dat
     and a PNG preview.
  3. Wide overview again → wide_after.dat / wide_after.png

The 9 tile images, stitched 3×3, reconstruct the wide image at higher
effective resolution.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from scanflow.core import STMClient
from scanflow.automation import (
    AutomationRunner, MeasurementRecipe, MosaicConfig, MosaicStep,
    RunnerState,
)
from scanflow.automation.recipe import format_duration


class MosaicPanel(QWidget):
    """Configure and run a 3×3 mosaic campaign."""

    log_message = Signal(str)
    error_message = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._runner: AutomationRunner | None = None
        self._last_output: Path | None = None
        self._build_ui()
        # Update tile-size auto-fill whenever wide size changes
        self._auto_tile_check.toggled.connect(self._refresh_tile_size)
        for w in (self._wide_x, self._wide_y):
            w.valueChanged.connect(self._refresh_tile_size)
        self._refresh_tile_size()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.addWidget(self._build_wide_group())
        root.addWidget(self._build_tile_group())
        root.addWidget(self._build_shared_group())

        # Live count + duration estimate, refreshed on any spinbox change.
        estimate_row = QHBoxLayout()
        self._count_label = QLabel("Scans: 0")
        self._count_label.setStyleSheet("font-weight: bold;")
        estimate_row.addWidget(self._count_label)
        estimate_row.addStretch(1)
        self._estimate_label = QLabel("Estimated total time: —")
        self._estimate_label.setStyleSheet("font-weight: bold;")
        estimate_row.addWidget(self._estimate_label)
        root.addLayout(estimate_row)

        root.addWidget(self._build_run_group())

        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        root.addWidget(self._progress)

        self._status = QLabel("Ready")
        root.addWidget(self._status)
        root.addStretch(1)

        # Wire every parameter that affects the time estimate.
        for w in (self._wide_x, self._wide_y,
                  self._wide_pixels_x, self._wide_pixels_y, self._wide_speed,
                  self._tile_x, self._tile_y,
                  self._tile_pixels_x, self._tile_pixels_y, self._tile_speed,
                  self._iters, self._settle):
            w.valueChanged.connect(self._refresh_estimate)
        self._auto_tile_check.toggled.connect(self._refresh_estimate)
        self._refresh_estimate()

    def _build_wide_group(self) -> QGroupBox:
        box = QGroupBox("Wide overview (before + after)")
        g = QGridLayout(box)

        g.addWidget(QLabel("Size X (nm)"), 0, 0)
        self._wide_x = QDoubleSpinBox()
        self._wide_x.setRange(1.0, 50000.0)
        self._wide_x.setDecimals(2)
        self._wide_x.setValue(90.0)
        g.addWidget(self._wide_x, 0, 1)

        g.addWidget(QLabel("Size Y (nm)"), 0, 2)
        self._wide_y = QDoubleSpinBox()
        self._wide_y.setRange(1.0, 50000.0)
        self._wide_y.setDecimals(2)
        self._wide_y.setValue(90.0)
        g.addWidget(self._wide_y, 0, 3)

        g.addWidget(QLabel("Pixels X"), 1, 0)
        self._wide_pixels_x = QSpinBox()
        self._wide_pixels_x.setRange(64, 4096)
        self._wide_pixels_x.setValue(256)
        g.addWidget(self._wide_pixels_x, 1, 1)

        g.addWidget(QLabel("Pixels Y"), 1, 2)
        self._wide_pixels_y = QSpinBox()
        self._wide_pixels_y.setRange(64, 4096)
        self._wide_pixels_y.setValue(256)
        g.addWidget(self._wide_pixels_y, 1, 3)

        g.addWidget(QLabel("Speed (nm/s)"), 2, 0)
        self._wide_speed = QDoubleSpinBox()
        self._wide_speed.setRange(0.1, 5000.0)
        self._wide_speed.setDecimals(1)
        self._wide_speed.setValue(100.0)
        g.addWidget(self._wide_speed, 2, 1)
        return box

    def _build_tile_group(self) -> QGroupBox:
        box = QGroupBox("Per-tile zoom (3×3 grid = 9 tiles)")
        g = QGridLayout(box)

        self._auto_tile_check = QCheckBox(
            "Auto-size tiles (= wide / 3, no gaps or overlap)"
        )
        self._auto_tile_check.setChecked(True)
        g.addWidget(self._auto_tile_check, 0, 0, 1, 4)

        g.addWidget(QLabel("Tile size X (nm)"), 1, 0)
        self._tile_x = QDoubleSpinBox()
        self._tile_x.setRange(0.1, 5000.0)
        self._tile_x.setDecimals(2)
        self._tile_x.setValue(30.0)
        g.addWidget(self._tile_x, 1, 1)

        g.addWidget(QLabel("Tile size Y (nm)"), 1, 2)
        self._tile_y = QDoubleSpinBox()
        self._tile_y.setRange(0.1, 5000.0)
        self._tile_y.setDecimals(2)
        self._tile_y.setValue(30.0)
        g.addWidget(self._tile_y, 1, 3)

        g.addWidget(QLabel("Tile pixels X"), 2, 0)
        self._tile_pixels_x = QSpinBox()
        self._tile_pixels_x.setRange(32, 4096)
        self._tile_pixels_x.setValue(256)
        g.addWidget(self._tile_pixels_x, 2, 1)

        g.addWidget(QLabel("Tile pixels Y"), 2, 2)
        self._tile_pixels_y = QSpinBox()
        self._tile_pixels_y.setRange(32, 4096)
        self._tile_pixels_y.setValue(256)
        g.addWidget(self._tile_pixels_y, 2, 3)

        g.addWidget(QLabel("Speed (nm/s)"), 3, 0)
        self._tile_speed = QDoubleSpinBox()
        self._tile_speed.setRange(0.1, 2000.0)
        self._tile_speed.setDecimals(1)
        self._tile_speed.setValue(20.0)
        g.addWidget(self._tile_speed, 3, 1)

        g.addWidget(QLabel("Iterations per tile"), 3, 2)
        self._iters = QSpinBox()
        self._iters.setRange(1, 10)
        self._iters.setValue(3)
        self._iters.setToolTip(
            "Number of scans per tile. Iterations 2..N are drift-corrected "
            "against iteration 1, so the final iteration is well-centered."
        )
        g.addWidget(self._iters, 3, 3)
        return box

    def _build_shared_group(self) -> QGroupBox:
        box = QGroupBox("Tunneling, settle, output")
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

        g.addWidget(QLabel("Settle before scan (s)"), 1, 0)
        self._settle = QDoubleSpinBox()
        self._settle.setRange(0.0, 600.0)
        self._settle.setDecimals(1)
        self._settle.setValue(5.0)
        g.addWidget(self._settle, 1, 1)

        g.addWidget(QLabel("Campaign name"), 2, 0)
        self._name = QLineEdit("Mosaic")
        g.addWidget(self._name, 2, 1, 1, 3)

        g.addWidget(QLabel("Output folder"), 3, 0)
        self._output = QLineEdit()
        self._output.setPlaceholderText("(required to save .dat and .png files)")
        g.addWidget(self._output, 3, 1, 1, 2)
        pick = QPushButton("Browse…")
        pick.clicked.connect(self._pick_folder)
        g.addWidget(pick, 3, 3)
        return box

    def _build_run_group(self) -> QGroupBox:
        box = QGroupBox("Run")
        g = QGridLayout(box)
        self._start_btn = QPushButton("Start mosaic")
        self._start_btn.clicked.connect(self._start_run)
        g.addWidget(self._start_btn, 0, 0)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_run)
        g.addWidget(self._stop_btn, 0, 1)

        self._force_quit_btn = QPushButton("Force Quit")
        self._force_quit_btn.setEnabled(False)
        self._force_quit_btn.setToolTip(
            "Hard-terminate the runner thread. Use only if Stop hangs — "
            "the STM may be left mid-scan."
        )
        self._force_quit_btn.clicked.connect(self._force_quit_run)
        g.addWidget(self._force_quit_btn, 0, 2)
        return box

    # ------------------------------------------------------------------

    def _refresh_tile_size(self) -> None:
        auto = self._auto_tile_check.isChecked()
        if auto:
            self._tile_x.setValue(self._wide_x.value() / 3.0)
            self._tile_y.setValue(self._wide_y.value() / 3.0)
        self._tile_x.setEnabled(not auto)
        self._tile_y.setEnabled(not auto)

    def _refresh_estimate(self) -> None:
        try:
            cfg = self._build_config()
            step = MosaicStep(config=cfg)
            n_tiles = cfg.total_tiles()
            n_iter = max(1, cfg.iterations_per_tile)
            total = step.estimate_duration_s()
        except Exception:
            self._count_label.setText("Scans: —")
            self._estimate_label.setText("Estimated total time: —")
            return
        n_scans = 2 + n_tiles * n_iter   # 2 wide + tiles × iters
        self._count_label.setText(
            f"Scans: <b>{n_scans}</b>  "
            f"(2 wide + {n_tiles} tiles × {n_iter} iter)"
        )
        self._estimate_label.setText(
            f"Estimated total time: <b>{format_duration(total)}</b>"
        )

    def _build_config(self) -> MosaicConfig:
        return MosaicConfig(
            wide_size_nm=(self._wide_x.value(), self._wide_y.value()),
            wide_pixels=(self._wide_pixels_x.value(), self._wide_pixels_y.value()),
            wide_speed_nm_s=self._wide_speed.value(),
            tile_size_nm=(self._tile_x.value(), self._tile_y.value()),
            tile_pixels=(self._tile_pixels_x.value(), self._tile_pixels_y.value()),
            tile_speed_nm_s=self._tile_speed.value(),
            iterations_per_tile=self._iters.value(),
            bias_V=self._bias.value(),
            setpoint_A=self._setpoint.value() * 1e-12,
            settling_s=self._settle.value(),
            output_folder=self._output.text(),
            name=self._name.text() or "Mosaic",
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
        recipe.add_step(MosaicStep(config=cfg, label=cfg.name))

        n_tiles = cfg.total_tiles()
        step_for_estimate = MosaicStep(config=cfg)
        total_s = step_for_estimate.estimate_duration_s()
        confirm = QMessageBox.question(
            self, "Start mosaic",
            f"<b>{cfg.name}</b><br><br>"
            f"Wide: {cfg.wide_size_nm[0]:.0f} × {cfg.wide_size_nm[1]:.0f} nm "
            f"@ {cfg.wide_speed_nm_s:.0f} nm/s<br>"
            f"Tiles: {n_tiles} × {cfg.iterations_per_tile} iterations "
            f"= {n_tiles * cfg.iterations_per_tile} small scans<br>"
            f"Tile size: {cfg.resolved_tile_size_nm()[0]:.2f} × "
            f"{cfg.resolved_tile_size_nm()[1]:.2f} nm<br>"
            f"Bias {cfg.bias_V:.3f} V, setpoint {cfg.setpoint_A * 1e12:.1f} pA<br>"
            f"Estimated total time: <b>{format_duration(total_s)}</b><br><br>"
            f"Start?",
        )
        if confirm != QMessageBox.Yes:
            return

        # Snapshot the XY position the instant the user commits, so the
        # Log tab shows where we're starting from before any apply() runs.
        try:
            xy0 = self._stm.scan.get_offset_nm()
        except Exception as e:
            self.log_message.emit(f"Mosaic START — error reading XY: {e}")
            xy0 = None
        if xy0 is not None:
            self.log_message.emit(
                f"Mosaic START — current XY = ({xy0[0]:+.3f}, {xy0[1]:+.3f}) nm"
            )
            # Fail-fast on the (0, 0) case — set_offset_nm() needs a V/nm
            # calibration derived from a non-zero offset, and there's no
            # point making the user wait through wide_before just to abort.
            if abs(xy0[0]) < 0.05 and abs(xy0[1]) < 0.05:
                QMessageBox.warning(
                    self, "Scan at piezo origin",
                    "<b>Cannot start mosaic at XY = (0, 0).</b><br><br>"
                    "ScanFlow derives the piezo V/nm calibration from the "
                    "current offset, and needs a non-zero starting point "
                    "(at least ±0.05 nm on either axis).<br><br>"
                    "In STMAFM, nudge the scan frame slightly off the origin "
                    "and try again — anywhere within a few nm is fine."
                )
                self.log_message.emit(
                    "Mosaic START aborted — XY = (0, 0); piezo calibration "
                    "cannot be derived. Move STMAFM to a non-zero offset first."
                )
                return
        else:
            self.log_message.emit(
                "Mosaic START — could not read SCAN.OFFSET.{X,Y}.NM "
                "(rig may be unable to position; aborting may be safer)"
            )
            QMessageBox.warning(
                self, "Cannot read XY offset",
                "ScanFlow could not read SCAN.OFFSET.{X,Y}.NM. Without a "
                "current-position reading, the mosaic positioning logic "
                "cannot derive its calibration. Check that STMAFM is "
                "connected and showing live offset values."
            )
            return

        self._runner = AutomationRunner(self._stm, recipe)
        self._runner.progress.connect(self._on_progress)
        self._runner.state_changed.connect(self._on_state)
        self._runner.error.connect(lambda m: self.error_message.emit(m))
        self._runner.scan_completed.connect(
            lambda p: self.log_message.emit(f"saved: {p}")
        )
        self._runner.mosaic_tile_started.connect(self._on_tile_started)
        self._runner.mosaic_tile_done.connect(self._on_tile_done)
        self._runner.mosaic_finished.connect(self._on_mosaic_finished)
        self._runner.settling.connect(self._on_settling)
        # Free-form runner info → straight into the Log tab so per-tile XY,
        # calibration, wide-centre, and positioning errors are visible
        # without pulling the file log.
        self._runner.info_message.connect(self.log_message)
        self._runner.start()

        self._progress.setMaximum(n_tiles + 2)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._force_quit_btn.setEnabled(True)

    def _stop_run(self) -> None:
        if not self._runner:
            return
        # First click: graceful. Second click: emergency stop + tip retract.
        stop_count_before = self._runner._stop_count
        self._runner.stop()
        if stop_count_before == 0:
            self.log_message.emit(
                "Stop requested — will finish current tile iteration then halt"
            )
            self._stop_btn.setText("Emergency Stop")
            self._status.setText("Stopping…")
        else:
            self.log_message.emit(
                "EMERGENCY STOP requested — aborting scan, retracting tip"
            )
            self._status.setText("Emergency stop — retracting tip…")

    def _force_quit_run(self) -> None:
        if not self._runner:
            return
        ans = QMessageBox.question(
            self, "Force-terminate runner?",
            "Hard-terminate the runner thread? The STM may be left "
            "mid-scan — only use if Stop is not responding.",
        )
        if ans == QMessageBox.Yes:
            self.log_message.emit(
                "FORCE QUIT — hard-terminating runner thread"
            )
            self._status.setText("Force-terminating…")
            self._runner.force_stop()

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
            self._force_quit_btn.setEnabled(False)
            self._stop_btn.setText("Stop")

    def _on_settling(self, remaining_s: int, label: str) -> None:
        self._status.setText(f"{label} — {remaining_s} s")

    def _on_tile_started(self, idx: int, total: int) -> None:
        self.log_message.emit(f"Tile {idx:02d}/{total} starting")

    def _on_tile_done(self, idx: int) -> None:
        self.log_message.emit(f"Tile {idx:02d} done")

    def _on_mosaic_finished(self, output_dir: str) -> None:
        self._last_output = Path(output_dir)
        self.log_message.emit(f"Mosaic finished — output: {output_dir}")
        self._status.setText("Mosaic complete")
