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

from scanflow.core import (
    STMClient, STMNotConnectedError, ScanParams, IVTable,
    SafetyMonitor, SafetyConfig, SafetyViolation,
)
from scanflow.automation.recipe import (
    MeasurementRecipe, ScanStep, SpectroscopyStep, ApproachStep, WaitStep,
)
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
    live_frame = Signal(object)         # numpy 2-D array, emitted ~2 Hz during scan
    settling = Signal(int, str)         # remaining seconds, label
    safety_violation = Signal(str, float)  # message, |I| in amperes
    safety_reading = Signal(float)      # latest |I| reading in amperes

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
        self._safety = SafetyMonitor(SafetyConfig(
            max_current_A=recipe.safety_max_current_A,
            enable_current_check=recipe.safety_enable,
            retract_on_violation_nm=recipe.safety_retract_nm,
        ))

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
        except SafetyViolation as e:
            log.error("SAFETY: %s", e)
            self._safety.emergency_stop(self._stm)
            current = e.current_A if e.current_A is not None else 0.0
            self.safety_violation.emit(str(e), current)
            self.error.emit(f"SAFETY ABORT: {e}")
            self._set_state(RunnerState.ERROR)
            return
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

                kind = getattr(step, "kind", "scan")
                if kind == "scan":
                    self._do_scan_step(step, recipe, label)
                elif kind == "spectroscopy":
                    self._do_spec_step(step, label)
                elif kind == "approach":
                    self._do_approach_step(step, label)
                elif kind == "wait":
                    self._sleep_with_progress(
                        step.seconds, label or "Wait")
                else:
                    log.warning("Unknown step kind: %s — skipping", kind)

                if recipe.inter_step_delay_s > 0:
                    self._sleep_with_progress(
                        recipe.inter_step_delay_s, "Inter-step pause"
                    )

    def _do_scan_step(self, step: ScanStep,
                      recipe: MeasurementRecipe, label: str) -> None:
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

        if step.settling_s > 0:
            self._sleep_with_progress(step.settling_s, f"Settling: {label}")
            if self._stop_requested:
                return

        if recipe.drift_correction and self._reference_array is not None:
            self._do_alignment_scan(recipe)

        dat_path = self._scan_and_save(recipe)
        if dat_path:
            log.info("Saved: %s", dat_path)
            self.scan_completed.emit(str(dat_path))
            if self._reference_array is None and recipe.drift_correction:
                self._reference_array = self._load_channel(
                    dat_path, recipe.drift_channel)
                self._reference_timestamp = time.time()

    def _do_spec_step(self, step: SpectroscopyStep, label: str) -> None:
        table = IVTable(
            bias_start_V=step.bias_start_V,
            bias_end_V=step.bias_end_V,
            points=step.points,
            backward_sweep=step.backward_sweep,
        )
        self._stm.spec.configure(
            table=table,
            channels=step.channels,
            duration_s=step.duration_s,
            repeat_count=step.repeat_count,
            average_count=step.average_count,
            lat_speed_nm_s=step.lat_speed_nm_s,
            preamp_exponent=step.preamp_exponent,
        )
        if step.settling_s > 0:
            self._sleep_with_progress(step.settling_s, f"Settling: {label}")
            if self._stop_requested:
                return
        if len(step.positions) == 1:
            x, y = step.positions[0]
            self._stm.spec.single_at_pixel(int(x), int(y))
        else:
            self._stm.spec.multi_at_pixels(list(step.positions))
        log.info("Spec step finished: %s", label)

    def _do_approach_step(self, step: ApproachStep, label: str) -> None:
        from scanflow.core import ApproachConfig
        cfg = ApproachConfig(
            bias_V=step.bias_V,
            target_current_A=step.setpoint_A,
            burst_count=step.burst_count,
            retry_count=step.retry_count,
            period_s=step.period_s,
        )
        self._stm.coarse.configure_approach(cfg)
        self._stm.coarse.start_approach()
        if not self._stm.coarse.wait_for_approach(timeout_s=step.timeout_s):
            self.error.emit(f"Approach timed out: {label}")
        log.info("Approach step finished: %s", label)

    def _sleep_with_progress(self, seconds: float, label: str) -> None:
        """Sleep emitting per-second updates so the GUI can show a countdown."""
        end = time.time() + seconds
        while True:
            remaining = end - time.time()
            if remaining <= 0 or self._stop_requested:
                break
            self.settling.emit(max(0, int(remaining)), label)
            time.sleep(min(1.0, remaining))

    def _scan_and_save(self, recipe: MeasurementRecipe) -> Optional[Path]:
        scan = self._stm.scan
        scan.start()
        if not self._wait_for_scan_with_live_emit():
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

    def _wait_for_scan_with_live_emit(self, poll_interval_s: Optional[float] = None) -> bool:
        """Wait for the active scan to finish, emitting live frames as it runs.

        Uses the event bridge as a wake-up signal when available; otherwise
        falls back to fixed-interval polling. Emits ``live_frame`` at most
        once per ``poll_interval_s``. Checks the safety monitor every
        iteration and raises ``SafetyViolation`` if the threshold is hit.
        """
        scan = self._stm.scan
        bridge = self._stm.events
        recipe = self._recipe
        if poll_interval_s is None:
            poll_interval_s = recipe.safety_poll_interval_s
        # Give the DSP a moment to flip into SCANNING
        for _ in range(3):
            if scan.is_running:
                break
            bridge.consume_flag(timeout=poll_interval_s)
        while scan.is_running:
            if self._stop_requested:
                scan.stop()
                return False
            self._wait_if_paused()
            # Safety check — raises SafetyViolation if threshold exceeded
            self._check_safety()
            # Pull one live frame for the viewer
            try:
                frame = scan.live_data()
                if frame is not None:
                    self.live_frame.emit(frame)
            except Exception:
                log.debug("live_data() raised", exc_info=True)
            # Either wake on an event or sleep the polling interval
            bridge.consume_flag(timeout=poll_interval_s)
        return True

    def _check_safety(self) -> None:
        """Raise SafetyViolation if the current threshold is exceeded."""
        status = self._safety.check(self._stm)
        if status.measured_current_A is not None:
            self.safety_reading.emit(abs(status.measured_current_A))
        if not status.ok:
            raise SafetyViolation(status.reason,
                                  current_A=status.measured_current_A)

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
