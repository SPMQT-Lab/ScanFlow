"""Automation runner: executes a MeasurementRecipe against a live STM.

Runs in a QThread so the GUI stays responsive. Emits Qt signals for progress,
drift results, new scan files, and errors.
"""

from __future__ import annotations

import logging
import time
from enum import Enum, auto
from pathlib import Path
from typing import Optional

import numpy as np

from PySide6.QtCore import QThread, Signal

from scanflow.core import STMClient, STMNotConnectedError, ScanParams
from scanflow.automation.recipe import MeasurementRecipe
from scanflow.drift import DriftDetector, DriftResult

log = logging.getLogger(__name__)


class RunnerState(Enum):
    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()
    FINISHED = auto()
    ERROR = auto()


class AutomationRunner(QThread):
    """Executes a recipe step-by-step with optional drift correction.

    Signals
    -------
    progress(current_step, total_steps, label)
    scan_completed(dat_file_path)
    drift_measured(DriftResult)
    drift_corrected(dx_pixels, dy_pixels)
    state_changed(RunnerState)
    error(message)
    """

    progress = Signal(int, int, str)
    scan_completed = Signal(str)
    drift_measured = Signal(object)
    drift_corrected = Signal(float, float)
    state_changed = Signal(object)
    error = Signal(str)

    def __init__(self, stm: STMClient, recipe: MeasurementRecipe, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._recipe = recipe
        self._state = RunnerState.IDLE
        self._stop_requested = False
        self._pause_requested = False
        self._detector: Optional[DriftDetector] = None
        self._reference_array: Optional[np.ndarray] = None
        self._reference_timestamp: Optional[float] = None

    # ------------------------------------------------------------------
    # Public controls
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self._stop_requested = True

    def pause(self) -> None:
        self._pause_requested = True

    def resume(self) -> None:
        self._pause_requested = False

    def _set_state(self, state: RunnerState) -> None:
        self._state = state
        self.state_changed.emit(state)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._stop_requested = False
        self._set_state(RunnerState.RUNNING)
        try:
            self._execute()
        except STMNotConnectedError as e:
            log.error("STM disconnected: %s", e)
            self.error.emit(f"STM disconnected: {e}")
            self._set_state(RunnerState.ERROR)
            return
        except Exception as e:
            log.exception("Unexpected runner error")
            self.error.emit(str(e))
            self._set_state(RunnerState.ERROR)
            return
        self._set_state(RunnerState.FINISHED)

    def _execute(self) -> None:
        recipe = self._recipe
        total = recipe.total_steps()
        step_idx = 0

        # Overnight safety: suppress DST automatic time change
        if recipe.suppress_dst_change:
            try:
                self._stm.setp("Block_DSTime_Change", True)
            except Exception:
                pass

        if recipe.drift_correction:
            self._detector = DriftDetector(continuous=True)

        if recipe.drift_correction and recipe.drift_template:
            self._reference_array = self._load_channel(
                Path(recipe.drift_template), recipe.drift_channel
            )
            self._reference_timestamp = time.time()

        for _rep in range(recipe.repetitions):
            for step in recipe.steps:
                if self._stop_requested:
                    return
                self._wait_if_paused()
                step_idx += 1
                label = step.label or f"step {step_idx}/{total}"
                self.progress.emit(step_idx, total, label)
                log.info("Starting %s", label)

                # Apply scan parameters
                params = ScanParams(
                    bias_V=step.bias_V,
                    setpoint_A=step.setpoint_A,
                    size_nm=step.size_nm,
                    speed_nm_s=step.speed_nm_s,
                    pixels=step.pixels,
                    rotation_deg=step.rotation_deg,
                    const_height=step.const_height,
                    channels=step.channels,
                    preamp_exponent=step.preamp_exponent,
                    memo=step.memo or label,
                )
                self._stm.scan.apply(params)

                # Alignment scan + drift correction before the data scan
                if recipe.drift_correction and self._reference_array is not None:
                    self._do_alignment_scan(recipe)

                # Data scan
                dat_path = self._scan_and_save(recipe)
                if dat_path:
                    log.info("Saved: %s", dat_path)
                    self.scan_completed.emit(str(dat_path))
                    if self._reference_array is None and recipe.drift_correction:
                        self._reference_array = self._load_channel(dat_path, recipe.drift_channel)
                        self._reference_timestamp = time.time()

                if recipe.inter_step_delay_s > 0:
                    time.sleep(recipe.inter_step_delay_s)

    def _scan_and_save(self, recipe: MeasurementRecipe) -> Optional[Path]:
        scan = self._stm.scan
        scan.start()
        if not scan.wait_until_done():
            return None
        save_target = ""
        if recipe.save_folder:
            from datetime import datetime
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_target = str(Path(recipe.save_folder) / f"scan_{stamp}.dat")
            scan.save_dat(save_target)
        else:
            scan.save_dat(str(self._stm.raw.savedatfilename))
        path = scan.last_saved_path()
        return path

    def _do_alignment_scan(self, recipe: MeasurementRecipe) -> None:
        align_path = self._scan_and_save(recipe)
        if not align_path or self._reference_array is None:
            return
        current_array = self._load_channel(align_path, recipe.drift_channel)
        if current_array is None:
            return
        cur_ts = time.time()
        result = self._detector.measure(
            reference=self._reference_array,
            current=current_array,
            ref_timestamp=self._reference_timestamp,
            cur_timestamp=cur_ts,
            extra_seconds=recipe.drift_reposition_delay_s,
        )
        self.drift_measured.emit(result)
        log.info(
            "Drift: dx=%.2f Å, dy=%.2f Å, magnitude=%.2f Å, confidence=%.2f",
            result.dx_angstrom, result.dy_angstrom,
            result.magnitude_angstrom, result.confidence,
        )
        self._stm.scan.nudge_offset_pixels(result.dx_pixels, result.dy_pixels)
        time.sleep(recipe.drift_reposition_delay_s)
        self.drift_corrected.emit(result.dx_pixels, result.dy_pixels)

    def _wait_if_paused(self) -> None:
        if self._pause_requested:
            self._set_state(RunnerState.PAUSED)
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.2)
            if not self._stop_requested:
                self._set_state(RunnerState.RUNNING)

    @staticmethod
    def _load_channel(path: Path, channel: int) -> Optional[np.ndarray]:
        try:
            from createc.Createc_pyFile import DAT_IMG
            img = DAT_IMG(str(path))
            return np.array(img.img_array_list[channel], dtype=float)
        except Exception as e:
            log.warning("Could not load channel %d from %s: %s", channel, path, e)
            return None
