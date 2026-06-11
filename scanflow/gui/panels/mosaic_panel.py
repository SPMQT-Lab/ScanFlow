"""ScanFlow Mosaic panel — wide image + N×N zoom tiles + wide image.

Inputs (wide overview): size X/Y, pixels, speed.
Inputs (per-tile zoom): size X/Y (default wide/N), pixels, speed, grid N.
Bias modes:
  • Single bias  — all tile iterations use the same bias_V.
  • Bias sweep   — each iteration uses a different bias from a comma-
                   separated list; iteration count is derived from the list.
Shared: setpoint, settle. Output folder, campaign name.

Workflow when Start is pressed:
  1. Wide overview scan → wide_before.dat / wide_before.png
  2. Visit each of the N×N tiles (top row first, then middle, then bottom);
     per tile, run M iterations — each at the configured bias — save every
     iteration's .dat and a PNG preview.
  3. Wide overview again → wide_after.dat / wide_after.png
"""

from __future__ import annotations

import datetime
import time
from pathlib import Path
from typing import List

from PySide6.QtCore import Signal, QTimer
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
from scanflow.gui.preflight import confirm_recipe_preflight


class MosaicPanel(QWidget):
    """Configure and run an N×N mosaic campaign."""

    log_message = Signal(str)
    error_message = Signal(str)
    scan_completed = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._runner: AutomationRunner | None = None
        self._last_output: Path | None = None
        self._run_start_t: float | None = None
        self._total_steps_eta: int = 0
        self._recipe_eta: MeasurementRecipe | None = None
        self._build_ui()
        self._eta_timer = QTimer(self)
        self._eta_timer.setInterval(60_000)
        self._eta_timer.timeout.connect(self._refresh_eta_from_runner)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.addWidget(self._build_wide_group())
        root.addWidget(self._build_tile_group())   # sets self._tile_group
        root.addWidget(self._build_shared_group())

        # Live scan count + duration estimate.
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

        self._time_label = QLabel("")
        self._time_label.setStyleSheet("color: #555; font-style: italic;")
        root.addWidget(self._time_label)
        root.addStretch(1)

        # ── Connect signals ─────────────────────────────────────────────

        # Auto tile-size: recompute whenever wide size or grid N changes.
        self._auto_tile_check.toggled.connect(self._refresh_tile_size)
        self._auto_tile_check.toggled.connect(self._refresh_estimate)
        for w in (self._wide_x, self._wide_y):
            w.valueChanged.connect(self._refresh_tile_size)
        self._grid_n.valueChanged.connect(self._refresh_tile_size)
        self._grid_n.valueChanged.connect(self._refresh_grid_label)

        # Sweep toggle.
        self._sweep_check.toggled.connect(self._on_sweep_toggled)
        self._sweep_edit.textChanged.connect(self._refresh_sweep_info)
        self._sweep_edit.textChanged.connect(self._refresh_estimate)

        # All spinboxes that affect the time estimate.
        for w in (self._wide_x, self._wide_y,
                  self._wide_pixels_x, self._wide_pixels_y, self._wide_speed,
                  self._tile_x, self._tile_y,
                  self._tile_pixels_x, self._tile_pixels_y, self._tile_speed,
                  self._iters, self._settle, self._grid_n):
            w.valueChanged.connect(self._refresh_estimate)

        # Initial state.
        self._refresh_tile_size()
        self._refresh_grid_label()
        self._on_sweep_toggled(False)
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
        # Title is updated dynamically by _refresh_grid_label().
        self._tile_group = QGroupBox("Per-tile zoom (3×3 grid = 9 tiles)")
        g = QGridLayout(self._tile_group)

        # Row 0: auto-size checkbox.
        self._auto_tile_check = QCheckBox(
            "Auto-size tiles (= wide / N, no gaps or overlap)"
        )
        self._auto_tile_check.setChecked(True)
        g.addWidget(self._auto_tile_check, 0, 0, 1, 4)

        # Row 1: tile sizes.
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

        # Row 2: tile pixels.
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

        # Row 3: speed + grid N.
        g.addWidget(QLabel("Speed (nm/s)"), 3, 0)
        self._tile_speed = QDoubleSpinBox()
        self._tile_speed.setRange(0.1, 2000.0)
        self._tile_speed.setDecimals(1)
        self._tile_speed.setValue(20.0)
        g.addWidget(self._tile_speed, 3, 1)

        g.addWidget(QLabel("Grid N×N"), 3, 2)
        self._grid_n = QSpinBox()
        self._grid_n.setRange(1, 10)
        self._grid_n.setValue(3)
        self._grid_n.setToolTip(
            "N×N grid of zoom tiles.\n"
            "3 → 3×3 = 9 tiles, 4 → 4×4 = 16 tiles, etc.\n"
            "Auto-size adjusts tile size to wide/N automatically."
        )
        g.addWidget(self._grid_n, 3, 3)

        # Row 4: iterations (hidden when bias sweep is active).
        self._iters_label = QLabel("Iterations per tile")
        g.addWidget(self._iters_label, 4, 2)
        self._iters = QSpinBox()
        self._iters.setRange(1, 20)
        self._iters.setValue(3)
        self._iters.setToolTip(
            "Scans per tile (for repeat / averaging). "
            "Ignored when Bias sweep is active — iteration count "
            "comes from the number of sweep values."
        )
        g.addWidget(self._iters, 4, 3)

        return self._tile_group

    def _build_shared_group(self) -> QGroupBox:
        box = QGroupBox("Tunneling, settle, output")
        g = QGridLayout(box)

        # Row 0: bias + setpoint.
        self._bias_label = QLabel("Bias (V)")
        g.addWidget(self._bias_label, 0, 0)
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

        # Row 1: settle.
        g.addWidget(QLabel("Settle before scan (s)"), 1, 0)
        self._settle = QDoubleSpinBox()
        self._settle.setRange(0.0, 600.0)
        self._settle.setDecimals(1)
        self._settle.setValue(5.0)
        g.addWidget(self._settle, 1, 1)

        # Row 2: bias sweep toggle.
        self._sweep_check = QCheckBox("Bias sweep per tile iteration")
        self._sweep_check.setToolTip(
            "Each iteration of every tile is acquired at a different bias.\n"
            "Enter the bias values (V) as a comma-separated list.\n"
            "The number of values determines iterations per tile.\n"
            "'Bias (V)' above is used for the wide overview scans."
        )
        g.addWidget(self._sweep_check, 2, 0, 1, 4)

        # Row 3: sweep values (shown only when sweep is ON).
        self._sweep_values_label = QLabel("Sweep values (V):")
        g.addWidget(self._sweep_values_label, 3, 0)
        self._sweep_edit = QLineEdit()
        self._sweep_edit.setPlaceholderText("e.g. -0.5, -0.1, 0.1, 0.5")
        self._sweep_edit.setToolTip(
            "Comma-separated bias values in volts, one per tile iteration.\n"
            "Example: -0.5, -0.1, 0.1, 0.5  →  4 iterations per tile."
        )
        g.addWidget(self._sweep_edit, 3, 1, 1, 2)
        self._sweep_info = QLabel("")
        self._sweep_info.setStyleSheet("color: gray; font-style: italic;")
        g.addWidget(self._sweep_info, 3, 3)

        # Row 4: campaign name.
        g.addWidget(QLabel("Campaign name"), 4, 0)
        self._name = QLineEdit("Mosaic")
        g.addWidget(self._name, 4, 1, 1, 3)

        # Row 5: output folder.
        g.addWidget(QLabel("Output folder"), 5, 0)
        self._output = QLineEdit()
        self._output.setPlaceholderText("(required to save .dat and .png files)")
        g.addWidget(self._output, 5, 1, 1, 2)
        pick = QPushButton("Browse…")
        pick.clicked.connect(self._pick_folder)
        g.addWidget(pick, 5, 3)
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
    # Slot helpers
    # ------------------------------------------------------------------

    def _refresh_grid_label(self) -> None:
        n = self._grid_n.value()
        n2 = n * n
        self._tile_group.setTitle(
            f"Per-tile zoom ({n}×{n} grid = {n2} tile{'s' if n2 != 1 else ''})"
        )

    def _refresh_tile_size(self) -> None:
        auto = self._auto_tile_check.isChecked()
        if auto:
            n = max(self._grid_n.value(), 1)
            self._tile_x.setValue(self._wide_x.value() / n)
            self._tile_y.setValue(self._wide_y.value() / n)
        self._tile_x.setEnabled(not auto)
        self._tile_y.setEnabled(not auto)

    def _on_sweep_toggled(self, checked: bool) -> None:
        """Show/hide sweep widgets; update bias label; hide iterations."""
        for w in (self._sweep_values_label, self._sweep_edit, self._sweep_info):
            w.setVisible(checked)
        # Hide manual iteration count when the sweep drives it.
        self._iters_label.setVisible(not checked)
        self._iters.setVisible(not checked)
        # Clarify that the bias field controls only the wide scans.
        self._bias_label.setText(
            "Wide scan bias (V)" if checked else "Bias (V)"
        )
        self._refresh_sweep_info()
        self._refresh_estimate()

    def _refresh_sweep_info(self) -> None:
        if not self._sweep_check.isChecked():
            self._sweep_info.setText("")
            return
        try:
            vals = self._parse_sweep_values()
            n = len(vals)
            if n == 0:
                self._sweep_info.setText("(empty)")
                self._sweep_info.setStyleSheet("color: gray; font-style: italic;")
            else:
                self._sweep_info.setText(f"→ {n} iter{'s' if n != 1 else ''}/tile")
                self._sweep_info.setStyleSheet("color: green; font-weight: bold;")
        except ValueError:
            self._sweep_info.setText("⚠ invalid")
            self._sweep_info.setStyleSheet("color: red; font-weight: bold;")

    def _parse_sweep_values(self) -> List[float]:
        """Parse the sweep text field.  Raises ValueError on bad input."""
        text = self._sweep_edit.text().strip()
        if not text:
            return []
        return [float(v.strip()) for v in text.split(",") if v.strip()]

    def _refresh_estimate(self) -> None:
        try:
            cfg = self._build_config()
            step = MosaicStep(config=cfg)
            n_tiles = cfg.total_tiles()
            n_iters = cfg.effective_iterations()
            total = step.estimate_duration_s()
        except Exception:
            self._count_label.setText("Scans: —")
            self._estimate_label.setText("Estimated total time: —")
            return
        n_scans = 2 + n_tiles * n_iters
        self._count_label.setText(
            f"Scans: <b>{n_scans}</b>  "
            f"(2 wide + {n_tiles} tiles × {n_iters} iter)"
        )
        self._estimate_label.setText(
            f"Estimated total time: <b>{format_duration(total)}</b>"
        )

    def _build_config(self) -> MosaicConfig:
        bias_sweep: List[float] = []
        if self._sweep_check.isChecked():
            try:
                bias_sweep = self._parse_sweep_values()
            except ValueError:
                bias_sweep = []
        return MosaicConfig(
            wide_size_nm=(self._wide_x.value(), self._wide_y.value()),
            wide_pixels=(self._wide_pixels_x.value(), self._wide_pixels_y.value()),
            wide_speed_nm_s=self._wide_speed.value(),
            tile_size_nm=(self._tile_x.value(), self._tile_y.value()),
            tile_pixels=(self._tile_pixels_x.value(), self._tile_pixels_y.value()),
            tile_speed_nm_s=self._tile_speed.value(),
            iterations_per_tile=self._iters.value(),
            grid_n=self._grid_n.value(),
            bias_V=self._bias.value(),
            bias_sweep=bias_sweep,
            setpoint_A=self._setpoint.value() * 1e-12,
            settling_s=self._settle.value(),
            output_folder=self._output.text(),
            name=self._name.text() or "Mosaic",
        )

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Output folder")
        if path:
            self._output.setText(path)

    # ------------------------------------------------------------------
    # Run / stop
    # ------------------------------------------------------------------

    def _start_run(self) -> None:
        if not self._stm.connected and not self._stm.is_mock:
            QMessageBox.warning(self, "STM not connected",
                                "Connect to the STM (or Mock) first.")
            return

        cfg = self._build_config()
        n_tiles = cfg.total_tiles()
        n_iters = cfg.effective_iterations()
        step_for_estimate = MosaicStep(config=cfg)
        total_s = step_for_estimate.estimate_duration_s()

        # Bias description for the confirmation dialog.
        if cfg.bias_sweep:
            shown = cfg.bias_sweep[:6]
            bias_str = "Bias sweep: " + ", ".join(
                f"{b * 1000:.1f} mV" for b in shown
            ) + (" …" if len(cfg.bias_sweep) > 6 else "")
        else:
            bias_str = f"Bias {cfg.bias_V:.3f} V"

        confirm = QMessageBox.question(
            self, "Start mosaic",
            f"<b>{cfg.name}</b><br><br>"
            f"Wide: {cfg.wide_size_nm[0]:.0f} × {cfg.wide_size_nm[1]:.0f} nm "
            f"@ {cfg.wide_speed_nm_s:.0f} nm/s<br>"
            f"Grid: {cfg.grid_n}×{cfg.grid_n} = {n_tiles} tiles, "
            f"{n_iters} iterations each "
            f"= {n_tiles * n_iters} small scans<br>"
            f"Tile size: {cfg.resolved_tile_size_nm()[0]:.2f} × "
            f"{cfg.resolved_tile_size_nm()[1]:.2f} nm<br>"
            f"{bias_str}<br>"
            f"Setpoint {cfg.setpoint_A * 1e12:.1f} pA<br>"
            f"Estimated total time: <b>{format_duration(total_s)}</b><br><br>"
            f"Start?",
        )
        if confirm != QMessageBox.Yes:
            return

        recipe = MeasurementRecipe(name=cfg.name)
        recipe.add_step(MosaicStep(config=cfg, label=cfg.name))

        mode = "mock" if self._stm.is_mock else "live"
        if not confirm_recipe_preflight(self, recipe, mode=mode):
            return

        # Snapshot XY before the runner thread starts.
        try:
            xy0 = self._stm.scan.get_offset_nm()
        except Exception as e:
            self.log_message.emit(f"Mosaic START — error reading XY: {e}")
            xy0 = None
        if xy0 is not None:
            self.log_message.emit(
                f"Mosaic START — current XY = ({xy0[0]:+.3f}, {xy0[1]:+.3f}) nm"
            )
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
        self._runner.scan_completed.connect(self._on_scan_completed)
        self._runner.mosaic_tile_started.connect(self._on_tile_started)
        self._runner.mosaic_tile_done.connect(self._on_tile_done)
        self._runner.mosaic_finished.connect(self._on_mosaic_finished)
        self._runner.settling.connect(self._on_settling)
        self._runner.info_message.connect(self.log_message)
        self._runner.start()

        self._run_start_t = time.time()
        self._total_steps_eta = n_tiles + 2
        self._recipe_eta = recipe
        self._time_label.setText("")
        self._eta_timer.start()
        self._progress.setMaximum(n_tiles + 2)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._force_quit_btn.setEnabled(True)

    def _on_scan_completed(self, path: str) -> None:
        self.scan_completed.emit(path)

    def _stop_run(self) -> None:
        if not self._runner:
            return
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
        self._update_eta(current, total)

    def _on_state(self, state) -> None:
        self._status.setText(state.name.capitalize())
        if state in (RunnerState.FINISHED, RunnerState.ERROR, RunnerState.IDLE):
            self._eta_timer.stop()
            self._time_label.setText("")
            self._run_start_t = None
            self._start_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._force_quit_btn.setEnabled(False)
            self._stop_btn.setText("Stop")

    def _on_settling(self, remaining_s: int, label: str) -> None:
        self._status.setText(f"{label} — {remaining_s} s")

    def _refresh_eta_from_runner(self) -> None:
        if self._runner is None:
            return
        current = self._runner._current_step_index or 0
        self._update_eta(current, self._total_steps_eta)

    def _update_eta(self, completed: int, total: int) -> None:
        if self._run_start_t is None or total == 0:
            return
        elapsed = time.time() - self._run_start_t
        # progress(N) fires at step N *start*, so only N-1 steps have actually finished
        finished = max(completed - 1, 0)
        if finished > 0:
            pace_s = elapsed / finished
            remaining_s = (total - finished) * pace_s
        elif self._recipe_eta is not None:
            remaining_s = max(0.0, self._recipe_eta.estimate_duration_s() - elapsed)
        else:
            return
        remaining_s = max(0.0, remaining_s)
        if remaining_s < 60:
            rem_str = "< 1 min"
        elif remaining_s < 3600:
            rem_str = f"~{int(remaining_s / 60)}m"
        else:
            h = int(remaining_s / 3600)
            m = int((remaining_s % 3600) / 60)
            rem_str = f"~{h}h {m:02d}m"
        eta = datetime.datetime.now() + datetime.timedelta(seconds=remaining_s)
        self._time_label.setText(f"Remaining: {rem_str}  (ETA {eta.strftime('%H:%M')})")

    def _on_tile_started(self, idx: int, total: int) -> None:
        self.log_message.emit(f"Tile {idx:02d}/{total} starting")

    def _on_tile_done(self, idx: int) -> None:
        self.log_message.emit(f"Tile {idx:02d} done")

    def _on_mosaic_finished(self, output_dir: str) -> None:
        self._last_output = Path(output_dir)
        self.log_message.emit(f"Mosaic finished — output: {output_dir}")
        self._status.setText("Mosaic complete")
