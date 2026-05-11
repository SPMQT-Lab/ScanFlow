"""Automation panel: recipe builder and run control for unattended sessions."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QDoubleSpinBox, QSpinBox, QPushButton,
    QCheckBox, QProgressBar, QComboBox, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QGridLayout, QLineEdit,
)
from PySide6.QtCore import Qt, Signal

from scanflow.core import STMClient
from scanflow.io import Session
from scanflow.automation import MeasurementRecipe, ScanStep, AutomationRunner


class AutomationPanel(QWidget):
    runner_scan_completed = Signal(str)
    runner_drift_measured = Signal(object)
    runner_error = Signal(str)
    runner_live_frame = Signal(object)
    runner_settling = Signal(int, str)

    def __init__(self, stm: STMClient, session: Session, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._session = session
        self._runner: AutomationRunner | None = None
        self._recipe: MeasurementRecipe | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_recipe_group())
        layout.addWidget(self._build_preview_group(), 1)
        layout.addWidget(self._build_run_group())
        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        layout.addWidget(self._progress)
        self._status = QLabel("Ready")
        layout.addWidget(self._status)

    def _build_recipe_group(self) -> QGroupBox:
        box = QGroupBox("Recipe")
        g = QGridLayout(box)

        g.addWidget(QLabel("Type"), 0, 0)
        self._type = QComboBox()
        self._type.addItems([
            "Overnight (repeated single scan)",
            "Bias ramp",
            "Current ramp",
        ])
        g.addWidget(self._type, 0, 1, 1, 3)

        g.addWidget(QLabel("Bias (V)"), 1, 0)
        self._bias = QDoubleSpinBox()
        self._bias.setRange(-10.0, 10.0)
        self._bias.setDecimals(4)
        self._bias.setValue(0.1)
        g.addWidget(self._bias, 1, 1)

        g.addWidget(QLabel("Setpoint (pA)"), 1, 2)
        self._current = QDoubleSpinBox()
        self._current.setRange(0.001, 1e6)
        self._current.setDecimals(3)
        self._current.setValue(100.0)
        g.addWidget(self._current, 1, 3)

        g.addWidget(QLabel("Size X (nm)"), 2, 0)
        self._size_x = QDoubleSpinBox()
        self._size_x.setRange(0.1, 50000.0)
        self._size_x.setValue(50.0)
        g.addWidget(self._size_x, 2, 1)

        g.addWidget(QLabel("Size Y (nm)"), 2, 2)
        self._size_y = QDoubleSpinBox()
        self._size_y.setRange(0.1, 50000.0)
        self._size_y.setValue(50.0)
        g.addWidget(self._size_y, 2, 3)

        g.addWidget(QLabel("Speed (nm/s)"), 3, 0)
        self._speed = QDoubleSpinBox()
        self._speed.setRange(0.01, 1000.0)
        self._speed.setValue(50.0)
        g.addWidget(self._speed, 3, 1)

        g.addWidget(QLabel("Pixels"), 3, 2)
        self._pixels = QSpinBox()
        self._pixels.setRange(8, 8192)
        self._pixels.setValue(256)
        g.addWidget(self._pixels, 3, 3)

        g.addWidget(QLabel("Repetitions"), 4, 0)
        self._reps = QSpinBox()
        self._reps.setRange(1, 10000)
        self._reps.setValue(50)
        g.addWidget(self._reps, 4, 1)

        # Ramp extras
        g.addWidget(QLabel("Ramp end value"), 4, 2)
        self._end_value = QDoubleSpinBox()
        self._end_value.setRange(-10000.0, 10000.0)
        self._end_value.setDecimals(4)
        self._end_value.setValue(-0.1)
        g.addWidget(self._end_value, 4, 3)

        g.addWidget(QLabel("Ramp steps"), 5, 0)
        self._ramp_steps = QSpinBox()
        self._ramp_steps.setRange(2, 500)
        self._ramp_steps.setValue(11)
        g.addWidget(self._ramp_steps, 5, 1)

        self._drift_chk = QCheckBox("Drift correction")
        self._drift_chk.setChecked(True)
        g.addWidget(self._drift_chk, 5, 2, 1, 2)

        self._dst_chk = QCheckBox("Suppress DST change (overnight safe)")
        self._dst_chk.setChecked(True)
        g.addWidget(self._dst_chk, 6, 0, 1, 4)

        g.addWidget(QLabel("Save folder"), 7, 0)
        self._save_folder = QLineEdit()
        self._save_folder.setPlaceholderText("(leave empty for STMAFM default)")
        g.addWidget(self._save_folder, 7, 1, 1, 2)
        pick = QPushButton("Browse…")
        pick.clicked.connect(self._pick_folder)
        g.addWidget(pick, 7, 3)

        build = QPushButton("Build Recipe")
        build.clicked.connect(self._build_recipe)
        g.addWidget(build, 8, 0, 1, 2)

        save = QPushButton("Save Recipe…")
        save.clicked.connect(self._save_recipe)
        g.addWidget(save, 8, 2)

        load = QPushButton("Load Recipe…")
        load.clicked.connect(self._load_recipe)
        g.addWidget(load, 8, 3)

        return box

    def _build_preview_group(self) -> QGroupBox:
        box = QGroupBox("Recipe preview")
        v = QVBoxLayout(box)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Label", "Bias (V)", "Setpoint (pA)", "Size (nm)", "Pixels"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self._table)
        return box

    def _build_run_group(self) -> QGroupBox:
        box = QGroupBox("Run")
        h = QHBoxLayout(box)
        self._start_btn = QPushButton("Start")
        self._start_btn.clicked.connect(self._start)
        h.addWidget(self._start_btn)
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.clicked.connect(self._pause)
        self._pause_btn.setEnabled(False)
        h.addWidget(self._pause_btn)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._stop)
        self._stop_btn.setEnabled(False)
        h.addWidget(self._stop_btn)
        return box

    # ------------------------------------------------------------------

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose save folder")
        if path:
            self._save_folder.setText(path)

    def _build_recipe(self) -> None:
        drift = self._drift_chk.isChecked()
        size = (self._size_x.value(), self._size_y.value())
        pixels = (self._pixels.value(), self._pixels.value())
        speed = self._speed.value()

        idx = self._type.currentIndex()
        if idx == 0:
            self._recipe = MeasurementRecipe.overnight(
                bias_V=self._bias.value(),
                setpoint_A=self._current.value() * 1e-12,
                repetitions=self._reps.value(),
                size_nm=size, pixels=pixels, speed_nm_s=speed,
                drift_correction=drift,
            )
        elif idx == 1:
            self._recipe = MeasurementRecipe.bias_ramp(
                start_V=self._bias.value(),
                end_V=self._end_value.value(),
                steps=self._ramp_steps.value(),
                setpoint_A=self._current.value() * 1e-12,
                size_nm=size, pixels=pixels, speed_nm_s=speed,
                drift_correction=drift,
            )
        else:
            self._recipe = MeasurementRecipe.current_ramp(
                start_pA=self._current.value(),
                end_pA=self._end_value.value(),
                steps=self._ramp_steps.value(),
                bias_V=self._bias.value(),
                size_nm=size, pixels=pixels, speed_nm_s=speed,
                drift_correction=drift,
            )

        self._recipe.suppress_dst_change = self._dst_chk.isChecked()
        self._recipe.save_folder = self._save_folder.text()
        self._populate_table()
        self._status.setText(f"Recipe built: {self._recipe.name}")

    def _populate_table(self) -> None:
        if not self._recipe:
            return
        self._table.setRowCount(0)
        for step in self._recipe.steps:
            row = self._table.rowCount()
            self._table.insertRow(row)
            for col, val in enumerate([
                step.label or "—",
                f"{step.bias_V:.4f}",
                f"{step.setpoint_A * 1e12:.2f}",
                f"{step.size_nm[0]:.1f}×{step.size_nm[1]:.1f}",
                f"{step.pixels[0]}×{step.pixels[1]}",
            ]):
                self._table.setItem(row, col, QTableWidgetItem(val))

    def _save_recipe(self) -> None:
        if not self._recipe:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Recipe", "", "YAML (*.yaml)")
        if path:
            from pathlib import Path
            self._recipe.save(Path(path))

    def _load_recipe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load Recipe", "", "YAML (*.yaml)")
        if path:
            from pathlib import Path
            self._recipe = MeasurementRecipe.load(Path(path))
            self._populate_table()
            self._status.setText(f"Loaded: {self._recipe.name}")

    def _start(self) -> None:
        if not self._recipe:
            self._build_recipe()
        if not self._recipe or not self._recipe.steps:
            return
        self._runner = AutomationRunner(self._stm, self._recipe)
        self._runner.progress.connect(self._on_progress)
        self._runner.scan_completed.connect(self.runner_scan_completed)
        self._runner.drift_measured.connect(self.runner_drift_measured)
        self._runner.error.connect(self.runner_error)
        self._runner.state_changed.connect(self._on_state)
        self._runner.live_frame.connect(self.runner_live_frame)
        self._runner.settling.connect(self.runner_settling)
        self._runner.settling.connect(self._on_settling)
        self._runner.start()

        self._start_btn.setEnabled(False)
        self._pause_btn.setEnabled(True)
        self._stop_btn.setEnabled(True)
        self._progress.setMaximum(self._recipe.total_steps())

    def _pause(self) -> None:
        if self._runner:
            self._runner.pause()

    def _stop(self) -> None:
        if self._runner:
            self._runner.stop()

    def _on_progress(self, current: int, total: int, label: str) -> None:
        self._progress.setValue(current)
        self._status.setText(f"{current}/{total}: {label}")

    def _on_settling(self, remaining_s: int, label: str) -> None:
        self._status.setText(f"{label} ({remaining_s}s)")

    def _on_state(self, state) -> None:
        from scanflow.automation import RunnerState
        self._status.setText(state.name.capitalize())
        if state in (RunnerState.FINISHED, RunnerState.ERROR, RunnerState.IDLE):
            self._start_btn.setEnabled(True)
            self._pause_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
