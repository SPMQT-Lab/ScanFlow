"""Automation runner: executes a MeasurementRecipe against a live STM.

Runs in a QThread so the GUI stays responsive. Emits Qt signals for progress,
new scan files, and errors.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum, auto
from pathlib import Path
from typing import Optional

import numpy as np

from PySide6.QtCore import QThread, Signal

from scanflow.core import (
    STMClient, STMNotConnectedError, ScanParams, IVTable,
    SafetyMonitor, SafetyConfig, SafetyViolation, TipMotionManager,
    MotionConfig, MotionResult, TipFormParams,
)
from scanflow.automation.recipe import (
    MeasurementRecipe, ScanStep, SpectroscopyStep, ApproachStep, WaitStep,
    TipFormStep, SurveyStep, MosaicStep, MIN_CONST_CURRENT_BIAS_V,
)
from scanflow.automation.survey import SurveyConfig, FeatureRecord, SurveyManifest
from scanflow.automation.mosaic import MosaicConfig, tile_targets_nm
from scanflow.automation.feature_discovery import discover_features, FeatureCandidate
from scanflow.automation.scan_metrics import compute_z_stability, format_z_stability
from scanflow.io.acquisition_log import AcquisitionLog, default_acquisition_log_path
from scanflow.io.sidecar import (
    SessionManifestWriter, new_session_id, scanflow_sidecar_path,
    write_scan_sidecar,
)

log = logging.getLogger(__name__)


class RunnerState(Enum):
    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()
    FINISHED = auto()
    ERROR = auto()


class AutomationRunner(QThread):
    """Executes a recipe step-by-step.

    Signals
    -------
    progress(current_step, total_steps, label)
    scan_completed(dat_file_path)
    state_changed(RunnerState)
    error(message)
    """

    progress = Signal(int, int, str)
    scan_completed = Signal(str)
    state_changed = Signal(object)
    error = Signal(str)
    # Live-frame delivery is OPT-IN and conflated. Every DATA.SCAN pull is a
    # full-frame COM transfer executed on STMAFM's GUI thread (the documented
    # cause of controller-window lag during automation), so the runner only
    # pulls frames when a consumer called enable_live_frames(). Delivery is
    # latest-frame-wins: the payload stays in a slot read via
    # take_live_frame(); this signal is just a wake-up and at most ONE
    # notification is queued no matter how far the GUI falls behind.
    live_frame_ready = Signal()
    settling = Signal(int, str)         # remaining seconds, label
    safety_violation = Signal(str, float)  # message, |I| in amperes
    safety_reading = Signal(float)      # latest |I| reading in amperes
    # Survey campaign signals
    survey_discovered = Signal(int)                       # number of features found
    survey_feature_started = Signal(int, int, float, float, float)
    # (idx, total, char_dim_nm, dx_tip_nm, dy_tip_nm)
    survey_feature_done = Signal(object)                  # FeatureRecord
    survey_finished = Signal(str)                         # manifest path
    z_stability = Signal(object)                          # dict from compute_z_stability
    # Mosaic campaign signals
    mosaic_tile_started = Signal(int, int)                # (tile_idx, total)
    mosaic_tile_done = Signal(int)                        # tile_idx
    mosaic_finished = Signal(str)                         # output folder path
    # Free-form line for the GUI Log tab — used to surface things the
    # runner already log.info()s to the file log, but that the user also
    # wants to see in the GUI without pulling the file.
    info_message = Signal(str)

    def __init__(self, stm: STMClient, recipe: MeasurementRecipe, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._recipe = recipe
        self._state = RunnerState.IDLE
        self._stop_requested = False
        self._stop_count = 0
        self._emergency_stop_requested = False
        self._pause_requested = False
        self._safety = SafetyMonitor(SafetyConfig(
            max_current_A=recipe.safety_max_current_A,
            enable_current_check=recipe.safety_enable,
            retract_on_violation_nm=recipe.safety_retract_nm,
        ))
        self._motion: TipMotionManager | None = None
        self._session_id = new_session_id()
        self._session_writers: dict[Path, SessionManifestWriter] = {}
        self._acq_log = AcquisitionLog(default_acquisition_log_path(recipe.save_folder))
        self._active_scan_params: Optional[ScanParams] = None
        self._current_step_index: Optional[int] = None
        self._current_step_kind: str = ""
        self._current_label: str = ""
        self._last_motion_result: Optional[MotionResult] = None
        self._last_z_stability: dict | None = None
        self._max_observed_current_A: Optional[float] = None
        self._run_generation = 0
        self._active_run_generation = 0
        self._tip_form_approval_pending = False
        self._tip_form_approval_run_generation: int | None = None
        # Live-frame conflation state (see live_frame_ready). None interval
        # means disabled — no DATA.SCAN pulls during the scan-wait loop.
        self._live_frame_interval_s: float | None = None
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._frame_pending = False
        self._last_frame_pull = 0.0

    # ------------------------------------------------------------------
    # Public controls
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Request a graceful stop. A second call escalates to emergency stop.

        Both flags are checked by the worker thread on every poll. The worker
        thread does the actual scan.stop()/z_limit calls — COM proxies are
        apartment-bound and cannot be touched from the GUI thread.
        """
        self._stop_count += 1
        self._stop_requested = True
        if self._stop_count >= 2:
            self._emergency_stop_requested = True
            log.warning("AutomationRunner.stop() #%d — emergency stop flagged",
                        self._stop_count)
        else:
            log.info("AutomationRunner.stop() #%d — graceful halt flagged",
                     self._stop_count)

    def force_stop(self) -> None:
        """Try to halt the runner gracefully; terminate only as a last resort.

        QThread.terminate() skips the finally block, which means
        unbind_thread() never runs and the COM apartment is left in an
        inconsistent state — that's what crashes STMAFM after a force
        quit. So we give the graceful path up to 8 seconds to drain
        first; only if it really hangs do we fall back to terminate().
        """
        log.warning("AutomationRunner.force_stop() requested")
        self._stop_requested = True
        self._emergency_stop_requested = True
        if not self.isRunning():
            return
        # Graceful path: flags are set, the scan loop will see them and
        # exit cleanly via the finally block (which uninits COM safely).
        if self.wait(8000):
            log.info("force_stop: worker exited gracefully — COM clean")
            return
        # Worker truly hung. Terminate is the only option, but warn:
        # STMAFM may crash because COM tear-down is skipped.
        log.error(
            "force_stop: worker did not exit after 8 s — terminating. "
            "STMAFM may crash; if so, restart STMAFM before reconnecting."
        )
        self.terminate()
        self.wait(2000)

    def pause(self) -> None:
        self._pause_requested = True

    def resume(self) -> None:
        self._pause_requested = False

    # ------------------------------------------------------------------
    # Live frames (opt-in, conflated)
    # ------------------------------------------------------------------

    def enable_live_frames(self, interval_s: float = 2.0) -> None:
        """Ask the runner to pull live scan frames during scans.

        Off by default: each pull is a full-frame DATA.SCAN transfer
        marshalled on STMAFM's GUI thread, so only enable this when a
        panel actually displays the frames. ``interval_s`` is a floor —
        frames are never pulled more often than this, independent of the
        (faster, cheap) safety poll. Connect to ``live_frame_ready`` and
        fetch with :meth:`take_live_frame`.
        """
        self._live_frame_interval_s = max(0.1, float(interval_s))

    def disable_live_frames(self) -> None:
        self._live_frame_interval_s = None

    def take_live_frame(self):
        """Return the newest pulled frame (or None) and clear the slot.

        Call from the slot connected to ``live_frame_ready``. Frames that
        arrive while a notification is still queued replace the slot
        contents — the consumer always sees the latest data and the Qt
        event queue never accumulates frame payloads.
        """
        with self._frame_lock:
            frame, self._latest_frame = self._latest_frame, None
            self._frame_pending = False
        return frame

    def _publish_live_frame(self, frame) -> None:
        """Worker-thread side of the conflation: store, notify at most once."""
        with self._frame_lock:
            self._latest_frame = frame
            if self._frame_pending:
                return  # notification already queued; it will see this frame
            self._frame_pending = True
        self.live_frame_ready.emit()

    def approve_next_tip_form(self) -> None:
        """Arm one supervised tip-forming pulse for the current or next run."""
        target_generation = (
            self._active_run_generation
            if self._active_run_generation > 0
            else self._run_generation + 1
        )
        self._tip_form_approval_pending = True
        self._tip_form_approval_run_generation = target_generation

    def _set_state(self, state: RunnerState) -> None:
        self._state = state
        self.state_changed.emit(state)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        # Reset ALL control flags: a runner that was stopped (or
        # emergency-stopped) on a previous run must not start its next
        # run with stale escalation state — one stale flag here means the
        # first Stop click escalates straight to emergency retract, or
        # the run aborts itself on the first poll.
        self._stop_requested = False
        self._emergency_stop_requested = False
        self._pause_requested = False
        self._stop_count = 0
        self._last_frame_pull = 0.0
        self._run_generation += 1
        self._active_run_generation = self._run_generation
        # COM proxies are apartment-bound — re-dispatch them on this worker
        # thread before any setp/getp, otherwise the first call raises
        # RPC_E_WRONG_THREAD ("interface marshalled for a different thread").
        self._stm.bind_thread()
        self._motion = TipMotionManager(
            self._stm,
            safety=self._safety,
            config=MotionConfig(
                max_single_move_nm=500.0,
                readback_tolerance_nm=2.0,
                default_settle_s=0.0,
                max_retries=1,
            ),
        )
        self._set_state(RunnerState.RUNNING)
        try:
            self._execute()
        except SafetyViolation as e:
            log.error("SAFETY: %s", e)
            self._acq_log.emit("safety_abort", reason=str(e), current_A=e.current_A)
            self._safety.emergency_stop(self._stm)
            current = e.current_A if e.current_A is not None else 0.0
            self.safety_violation.emit(str(e), current)
            self.error.emit(f"SAFETY ABORT: {e}")
            self._set_state(RunnerState.ERROR)
            return
        except STMNotConnectedError as e:
            log.error("STM disconnected: %s", e)
            self._acq_log.emit("routine_error", reason=str(e), kind="stm_disconnected")
            self.error.emit(f"STM disconnected: {e}")
            self._set_state(RunnerState.ERROR)
            return
        except Exception as e:
            log.exception("Unexpected runner error")
            self._acq_log.emit("routine_error", reason=str(e), kind="unexpected")
            self.error.emit(str(e))
            self._set_state(RunnerState.ERROR)
            return
        finally:
            self._active_run_generation = 0
            self._stm.unbind_thread()
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

        for _rep in range(recipe.repetitions):
            for step in recipe.steps:
                if self._stop_requested:
                    return
                self._wait_if_paused()
                step_idx += 1
                label = step.label or f"step {step_idx}/{total}"
                self._current_step_index = step_idx
                self._current_step_kind = getattr(step, "kind", "scan")
                self._current_label = label
                self._last_motion_result = None
                self._last_z_stability = None
                self.progress.emit(step_idx, total, label)
                log.info("Starting %s", label)

                kind = self._current_step_kind
                try:
                    if kind == "scan":
                        self._do_scan_step(step, recipe, label)
                    elif kind == "spectroscopy":
                        self._do_spec_step(step, label)
                    elif kind == "approach":
                        self._do_approach_step(step, label)
                    elif kind == "wait":
                        self._sleep_with_progress(
                            step.seconds, label or "Wait")
                    elif kind == "tip_form":
                        self._do_tip_form_step(step, label)
                    elif kind == "survey":
                        self._do_survey_step(step, label)
                    elif kind == "mosaic":
                        self._do_mosaic_step(step, label)
                    else:
                        log.warning("Unknown step kind: %s — skipping", kind)
                except (SafetyViolation, STMNotConnectedError):
                    # Always fatal regardless of stop_on_error — safety
                    # aborts and lost connections end the run.
                    raise
                except Exception as e:
                    if recipe.stop_on_error:
                        raise
                    log.exception("Step failed (stop_on_error=False): %s", label)
                    self._acq_log.emit(
                        "routine_error",
                        step_kind=kind,
                        label=label,
                        reason=str(e),
                        continued=True,
                    )
                    self.error.emit(
                        f"{label} failed: {e} — continuing (stop_on_error is off)"
                    )

                if recipe.inter_step_delay_s > 0:
                    self._sleep_with_progress(
                        recipe.inter_step_delay_s, "Inter-step pause"
                    )

    def _motion_manager(self) -> TipMotionManager:
        if self._motion is None:
            self._motion = TipMotionManager(self._stm, safety=self._safety)
        return self._motion

    def _capture_tip_form_snapshot(self, phase: str, label: str) -> bool:
        """Require a live-data snapshot for tip-form quality checks."""
        try:
            img = self._stm.scan.live_data()
        except Exception:
            img = None

        if img is None:
            msg = (
                f"TipFormStep '{label}' requires a {phase} quality snapshot; "
                "live_data() was unavailable"
            )
            self._acq_log.emit(
                "routine_error",
                step_kind="tip_form",
                label=label,
                reason=msg,
                phase=phase,
            )
            log.warning(msg)
            self.error.emit(msg)
            self._stop_requested = True
            return False

        try:
            self._last_z_stability = compute_z_stability(img)
            self._acq_log.emit(
                "z_stability",
                metrics=self._last_z_stability,
                phase=f"{phase}_tip_form",
            )
        except Exception as exc:
            log.warning(
                "TipFormStep '%s' %s snapshot quality metric failed: %s",
                label, phase, exc,
            )
        return True

    def _apply_scan_params(self, params: ScanParams) -> None:
        self._active_scan_params = params
        self._stm.scan.apply(params)

    def _routine_name(self) -> str:
        kinds = {getattr(step, "kind", "scan") for step in self._recipe.steps}
        if kinds == {"survey"}:
            return "survey"
        if kinds == {"mosaic"}:
            return "mosaic"
        name = self._recipe.name.lower()
        if "bias" in name:
            return "bias_ramp"
        if "current" in name:
            return "current_ramp"
        if "overnight" in name:
            return "overnight"
        return "recipe"

    def _record_motion_result(self, result: MotionResult) -> None:
        self._last_motion_result = result
        self._acq_log.emit(
            "motion_attempt",
            requested_nm=result.requested_nm,
            before_nm=result.before_nm,
            reason=result.reason,
            attempts=result.attempts,
        )
        self._acq_log.emit("motion_result", result=result)
        msg = (
            f"motion: {'ok' if result.ok else 'FAILED'} "
            f"{result.reason} target=({result.requested_nm[0]:+.3f}, "
            f"{result.requested_nm[1]:+.3f}) nm"
        )
        if result.after_nm is not None:
            msg += f" actual=({result.after_nm[0]:+.3f}, {result.after_nm[1]:+.3f}) nm"
        if result.warnings:
            msg += " [" + "; ".join(result.warnings) + "]"
        log.info(msg)
        self.info_message.emit(msg)

    def _record_scan_artifacts(
        self,
        dat_path: Path,
        *,
        role: str,
        scan_params: Optional[ScanParams] = None,
        motion: Optional[MotionResult] = None,
        quality: Optional[dict] = None,
    ) -> None:
        params = scan_params or self._active_scan_params
        position = None
        try:
            pos = self._motion_manager().read_position_nm()
            position = pos.xy if pos is not None else None
        except Exception:
            position = None

        safety = {
            "enabled": self._recipe.safety_enable,
            "max_current_A": self._recipe.safety_max_current_A,
            "max_observed_current_A": self._max_observed_current_A,
            "aborted": False,
        }
        sidecar = write_scan_sidecar(
            dat_path,
            session_id=self._session_id,
            routine=self._routine_name(),
            step_index=self._current_step_index,
            step_kind=self._current_step_kind or "scan",
            step_label=self._current_label,
            scan_parameters=params,
            position_nm=position,
            motion=motion or self._last_motion_result,
            quality=quality or ({"z_stability": self._last_z_stability}
                                if self._last_z_stability is not None else {}),
            safety=safety,
        )
        folder = Path(dat_path).resolve().parent
        self._ensure_acq_log_for_folder(folder)
        writer = self._session_writers.get(folder)
        if writer is None:
            writer = SessionManifestWriter(
                folder,
                session_id=self._session_id,
                recipe_name=self._recipe.name,
                routine=self._routine_name(),
            )
            self._session_writers[folder] = writer
        writer.add_scan(
            dat_path=dat_path,
            sidecar_path=sidecar,
            role=role,
            step_index=self._current_step_index,
            label=self._current_label,
        )
        self._acq_log.emit(
            "scan_completed",
            dat_path=dat_path,
            sidecar_path=sidecar,
            role=role,
            step_index=self._current_step_index,
            label=self._current_label,
        )

    def _ensure_acq_log_for_folder(self, folder: Path | str | None) -> None:
        if self._acq_log.path is not None or folder is None:
            return
        self._acq_log = AcquisitionLog(default_acquisition_log_path(folder))

    def _do_scan_step(self, step: ScanStep,
                      recipe: MeasurementRecipe, label: str) -> None:
        # Hard guard against 0 V in constant-current mode — the feedback loop
        # would drive the tip into the surface. Skip the step entirely.
        if not step.const_height and abs(step.bias_V) < MIN_CONST_CURRENT_BIAS_V:
            msg = (f"Skipping {label}: |bias|={abs(step.bias_V)*1000:.2f} mV "
                   f"< {MIN_CONST_CURRENT_BIAS_V*1000:.2f} mV — constant-current "
                   "scan at 0 V would crash the tip.")
            log.warning(msg)
            self.error.emit(msg)
            return
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
        self._apply_scan_params(params)

        if step.settling_s > 0:
            self._sleep_with_progress(step.settling_s, f"Settling: {label}")
            if self._stop_requested:
                return

        dat_path = self._scan_and_save(recipe)
        if dat_path:
            log.info("Saved: %s", dat_path)
            self.scan_completed.emit(str(dat_path))
            self._emit_z_stability()
            self._record_scan_artifacts(dat_path, role="data", scan_params=params)

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

    def _do_tip_form_step(self, step: TipFormStep, label: str) -> None:
        """Execute one supervised tip-forming pulse when explicitly armed."""
        if (
            not self._tip_form_approval_pending
            or self._tip_form_approval_run_generation != self._active_run_generation
        ):
            if (
                self._tip_form_approval_pending
                and self._tip_form_approval_run_generation is not None
                and self._tip_form_approval_run_generation != self._active_run_generation
            ):
                msg = (
                    f"TipFormStep '{label}' approval was armed for run "
                    f"{self._tip_form_approval_run_generation}, but this is run "
                    f"{self._active_run_generation}; call approve_next_tip_form() "
                    "again before starting this recipe."
                )
                self._tip_form_approval_pending = False
                self._tip_form_approval_run_generation = None
            else:
                msg = (
                    f"TipFormStep '{label}' requires explicit operator confirmation; "
                    "call approve_next_tip_form() before starting this recipe."
                )
            self._acq_log.emit(
                "routine_error",
                step_kind="tip_form",
                label=label,
                reason=msg,
                x_px=step.x_px,
                y_px=step.y_px,
                voltage_V=step.voltage_V,
                pulse_length_s=step.pulse_length_s,
            )
            log.warning(msg)
            self.error.emit(msg)
            self._stop_requested = True
            return

        self._tip_form_approval_pending = False
        self._tip_form_approval_run_generation = None
        self._acq_log.emit(
            "tip_form_started",
            label=label,
            x_px=step.x_px,
            y_px=step.y_px,
            voltage_V=step.voltage_V,
            z_approach_nm=step.z_approach_nm,
            pulse_length_s=step.pulse_length_s,
            z_offset_nm=step.z_offset_nm,
            lateral_speed_nm_s=step.lateral_speed_nm_s,
        )
        try:
            self._motion_manager().assert_safe_to_move()
            if self._motion_manager().calibration is None:
                self._motion_manager().calibrate_xy("current_then_delta")
        except Exception as exc:
            msg = f"TipFormStep '{label}' aborted before pulse: {exc}"
            self._acq_log.emit("routine_error", step_kind="tip_form", label=label, reason=msg)
            log.warning(msg)
            self.error.emit(msg)
            self._stop_requested = True
            return

        if not self._capture_tip_form_snapshot("pre", label):
            return

        params = TipFormParams(
            voltage_V=step.voltage_V,
            z_approach_nm=step.z_approach_nm,
            pulse_length_s=step.pulse_length_s,
            z_offset_nm=step.z_offset_nm,
            lateral_speed_nm_s=step.lateral_speed_nm_s,
        )
        try:
            self._stm.tipform.form_at(step.x_px, step.y_px, params)
        except Exception as exc:
            msg = f"TipFormStep '{label}' failed: {exc}"
            self._acq_log.emit("routine_error", step_kind="tip_form", label=label, reason=msg)
            log.warning(msg)
            self.error.emit(msg)
            self._stop_requested = True
            return

        if not self._capture_tip_form_snapshot("post", label):
            return

        self._acq_log.emit(
            "tip_form_completed",
            label=label,
            x_px=step.x_px,
            y_px=step.y_px,
            voltage_V=step.voltage_V,
            pulse_length_s=step.pulse_length_s,
        )

    def _do_survey_step(self, step: SurveyStep, label: str) -> None:
        """Wide scan → feature discovery → per-feature zoom campaign.

        Saves each scan plus a quick PNG preview, writes ``survey.json`` after
        every feature so a partial / interrupted run still leaves usable data.
        """
        from datetime import datetime
        cfg = step.config

        output: Optional[Path] = None
        if cfg.output_folder:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output = Path(cfg.output_folder) / f"survey_{stamp}"
            output.mkdir(parents=True, exist_ok=True)

        manifest = SurveyManifest(
            name=cfg.name,
            timestamp=datetime.now().isoformat(timespec="seconds"),
            wide_size_nm=cfg.wide_size_nm,
            wide_pixels=cfg.wide_pixels,
        )

        # --- 1. Wide scan -------------------------------------------------
        self.progress.emit(0, 1, f"{cfg.name}: wide scan")
        wide_params = ScanParams(
            bias_V=cfg.bias_V,
            setpoint_A=cfg.setpoint_A,
            size_nm=cfg.wide_size_nm,
            pixels=cfg.wide_pixels,
            speed_nm_s=cfg.wide_speed_nm_s,
            memo=f"{cfg.name} overview",
        )
        self._apply_scan_params(wide_params)
        if cfg.settling_s > 0:
            self._sleep_with_progress(cfg.settling_s, f"{cfg.name}: wide settle")
            if self._stop_requested:
                return
        wide_path = self._scan_and_save_to(output, "wide.dat")
        if wide_path:
            manifest.wide_scan_path = str(wide_path)
            self.scan_completed.emit(str(wide_path))

        wide_image = self._stm.scan.live_data()
        if wide_image is None:
            self.error.emit("Survey: no wide-scan image available — aborting")
            return

        if output is not None:
            preview = _save_image_preview(wide_image, output / "wide.png")
            if preview:
                manifest.wide_preview_path = str(preview)

        # --- 2. Discover features ----------------------------------------
        nm_per_px = cfg.wide_size_nm[0] / max(cfg.wide_pixels[0], 1)
        candidates = discover_features(
            wide_image, nm_per_px,
            min_feature_nm=cfg.min_feature_nm,
            max_feature_nm=cfg.max_feature_nm,
            size_multiplier=cfg.size_multiplier,
            min_zoom_nm=cfg.min_zoom_nm,
            max_zoom_nm=cfg.max_zoom_nm,
            merge_distance_nm=cfg.merge_distance_nm,
            edge_margin_px=cfg.edge_margin_px,
            max_features=cfg.max_features,
        )
        self.survey_discovered.emit(len(candidates))
        log.info("Survey: discovered %d feature(s)", len(candidates))

        # Re-render the wide preview with numbered bounding boxes overlaid
        if output is not None and candidates:
            _save_overview_preview(wide_image, candidates, output / "wide_annotated.png")

        # --- 3. Per-feature zoom loop ------------------------------------
        # Track the cumulative wide-pixel target so we can move by the
        # *delta* between consecutive features. We always restore the wide
        # frame params before moving, so the nm-per-pixel scale is the wide
        # image's scale.
        self._last_wide_px = (cfg.wide_pixels[0] / 2.0, cfg.wide_pixels[1] / 2.0)

        for idx, cand in enumerate(candidates, start=1):
            if self._stop_requested:
                break
            self._wait_if_paused()
            record = self._do_feature_zoom(
                idx, len(candidates), cand, cfg, output, nm_per_px, wide_params
            )
            if record is not None:
                manifest.features.append(record)
                self.survey_feature_done.emit(record)
            if output is not None:
                manifest.save(output / "survey.json")

        # --- 4. Finalise -------------------------------------------------
        if output is not None:
            manifest_path = output / "survey.json"
            manifest.save(manifest_path)
            self.survey_finished.emit(str(manifest_path))
            log.info("Survey manifest written: %s", manifest_path)

    def _do_feature_zoom(
        self,
        idx: int,
        total: int,
        cand: FeatureCandidate,
        cfg: SurveyConfig,
        output: Optional[Path],
        wide_nm_per_px: float,
        wide_params: "ScanParams",
    ) -> Optional[FeatureRecord]:
        """Center the scan window on ``cand``, run ``zoom_iterations`` scans,
        and report residual centering between iterations.

        Positioning: moves the XY offset by the wide-frame pixel delta from the
        previous feature's wide-pixel position. The wide frame params are
        re-applied first so the motion layer uses the wide nm-per-pixel
        scale, not the previous feature's zoom scale.

        FIXME(B2-frame-resize): UNRESOLVED COORDINATE-CONVENTION CONFLICT.
        Do not modify this method's positioning, and do not copy its scheme
        into new workflows, until the question below is settled on the rig.

        This method centres a feature by (1) applying the WIDE frame params,
        (2) moving the offset by the feature's pixel delta from the wide
        centre, then (3) applying the smaller ZOOM frame params — with NO
        ``-frame_height/2`` Y correction anywhere. That is only correct if
        STMAFM keeps the frame CENTRE fixed when SCAN.IMAGESIZE changes.

        Every other workflow assumes the opposite: scan_geometry.py documents
        SCAN.OFFSET.Y.NM as the frame TOP EDGE, and the mosaic path
        (mosaic.tile_targets_nm) and both preview workers
        (workers/preview_followup.py, workers/preview_groups.py) apply the
        ``-frame_height/2`` correction via scan_geometry.feature_target_xy_nm.
        Both conventions cannot be right. If the top-edge model is right,
        this survey path places every zoom (wide_h - zoom_h)/2 too HIGH —
        ~57 nm for the default 120 nm wide / 5 nm zoom, i.e. the feature is
        not in the zoom frame at all.

        To resolve (one manual experiment on the CreaTec):
          1. Park the scan frame over a visible feature, note
             SCAN.OFFSET.Y.NM and where the feature sits in the image.
          2. Halve SCAN.IMAGESIZE.NM.Y and rescan without moving the offset.
          3. If the feature stays centred → centre is preserved: this path
             is correct; FIX mosaic.tile_targets_nm and the preview workers
             and scan_geometry's documentation instead.
             If the frame's top scanline stays put → top edge is preserved:
             FIX THIS PATH by computing absolute targets through
             scan_geometry.feature_target_xy_nm (as preview_followup does).
          4. Either way: encode the verified behaviour in
             MockDispatch.setxyoffvolt (core/mock_dispatch.py) so mock tests
             can catch this class of bug, then delete this FIXME.

        Tracked as finding B2 in REVIEW.md (2026-06-10). The mock cannot
        detect the bug today — its offset model has no frame-size dependence.
        """
        wide_cx = cfg.wide_pixels[0] / 2.0
        wide_cy = cfg.wide_pixels[1] / 2.0
        dx_nm_log = (cand.cx_px - wide_cx) * wide_nm_per_px
        dy_nm_log = (cand.cy_px - wide_cy) * wide_nm_per_px

        self.survey_feature_started.emit(
            idx, total, cand.char_dim_nm, dx_nm_log, dy_nm_log,
        )

        # 1. Restore wide-frame params (preserves XY offset, sets nm-per-pixel
        # scale so the next move is interpreted in wide-image pixels).
        try:
            self._apply_scan_params(wide_params)
        except Exception as e:
            log.warning("could not re-apply wide params before move: %s", e)

        # 2. Shift the offset by (target - previous) in wide-pixel units.
        prev_x, prev_y = getattr(self, "_last_wide_px",
                                 (wide_cx, wide_cy))
        dx_px = float(cand.cx_px - prev_x)
        dy_px = float(cand.cy_px - prev_y)
        motion = self._motion_manager().move_relative_pixels(
            dx_px,
            dy_px,
            frame_size_nm=cfg.wide_size_nm,
            frame_pixels=cfg.wide_pixels,
            reason=f"survey feature {idx:02d} centre",
            settle_s=cfg.settling_s,
        )
        self._record_motion_result(motion)
        if motion.ok:
            self._last_wide_px = (float(cand.cx_px), float(cand.cy_px))
            log.info("Feature %d move: Δpx=(%+0.2f, %+0.2f) → Δnm=(%+0.2f, %+0.2f)",
                     idx, dx_px, dy_px,
                     dx_px * wide_nm_per_px, dy_px * wide_nm_per_px)
        else:
            msg = f"Survey feature {idx:02d} positioning failed: {'; '.join(motion.warnings)}"
            log.warning(msg)
            self.error.emit(msg)
            self._stop_requested = True
            return None

        # 3. Apply zoom params (the XY offset stays where we just placed it).
        zoom_params = ScanParams(
            bias_V=cfg.bias_V,
            setpoint_A=cfg.setpoint_A,
            size_nm=cand.zoom_nm,
            pixels=cfg.zoom_pixels,
            speed_nm_s=cfg.zoom_speed_nm_s,
            memo=f"{cfg.name} f{idx:02d}",
        )
        self._apply_scan_params(zoom_params)

        record = FeatureRecord(
            index=idx,
            centroid_pixels=(cand.cx_px, cand.cy_px),
            centroid_nm_offset=(dx_nm_log, dy_nm_log),
            char_dim_nm=cand.char_dim_nm,
            zoom_size_nm=cand.zoom_nm,
            bias_V=cfg.bias_V,
            setpoint_A=cfg.setpoint_A,
        )

        zoom_nm_per_px = cand.zoom_nm[0] / max(cfg.zoom_pixels[0], 1)
        zoom_cx = cfg.zoom_pixels[0] / 2.0
        zoom_cy = cfg.zoom_pixels[1] / 2.0

        for it in range(cfg.zoom_iterations):
            if self._stop_requested:
                break
            dat_name = f"feature_{idx:02d}_iter{it+1}.dat"
            png_name = f"feature_{idx:02d}_iter{it+1}.png"

            # Pre-scan settle — lets the piezo / feedback / tip stabilise so
            # the first scanline doesn't carry residual drift from the
            # apply()/move commands above.
            if cfg.settling_s > 0:
                self._sleep_with_progress(
                    cfg.settling_s,
                    f"f{idx:02d} iter{it+1} settle",
                )
                if self._stop_requested:
                    break

            path = self._scan_and_save_to(output, dat_name)
            if path is not None:
                record.scan_paths.append(str(path))
                self.scan_completed.emit(str(path))

            img = self._stm.scan.live_data()
            if img is None:
                record.drift_log_angstrom.append((0.0, 0.0))
                continue

            # Z-stability for this iteration's data scan
            stability = compute_z_stability(img)
            self._last_z_stability = stability
            self._acq_log.emit("z_stability", metrics=stability)
            self.z_stability.emit(stability)
            record.z_stability_per_iter.append(stability)

            if output is not None:
                preview = _save_image_preview(img, output / png_name)
                if preview:
                    record.preview_paths.append(str(preview))

            # Re-detect dominant feature inside the zoom and measure its offset
            # from the frame centre. Loose filters because the feature should
            # fill a large fraction of the frame.
            inner = discover_features(
                img, zoom_nm_per_px,
                min_feature_nm=cfg.min_feature_nm * 0.5,
                max_feature_nm=cfg.max_feature_nm * 2.0,
                size_multiplier=1.0,
                min_zoom_nm=0.1,
                max_zoom_nm=cand.zoom_nm[0],
                merge_distance_nm=cfg.merge_distance_nm,
                edge_margin_px=2,
                max_features=1,
            )
            if not inner:
                record.drift_log_angstrom.append((0.0, 0.0))
                continue

            ck = inner[0]
            dx_px = ck.cx_px - zoom_cx
            dy_px = ck.cy_px - zoom_cy
            dx_a = dx_px * zoom_nm_per_px * 10.0  # nm → Å
            dy_a = dy_px * zoom_nm_per_px * 10.0
            record.drift_log_angstrom.append((float(dx_a), float(dy_a)))
            record.final_residual_angstrom = (float(dx_a), float(dy_a))

            # Re-centre for the next iteration (skip on the last one)
            if it < cfg.zoom_iterations - 1 and (abs(dx_px) + abs(dy_px) > 0.5):
                motion = self._motion_manager().move_relative_pixels(
                    -float(dx_px),
                    -float(dy_px),
                    frame_size_nm=cand.zoom_nm,
                    frame_pixels=cfg.zoom_pixels,
                    reason=f"survey feature {idx:02d} iter {it + 1} recenter",
                    settle_s=cfg.settling_s,
                )
                self._record_motion_result(motion)
                if not motion.ok:
                    msg = (
                        f"Survey feature {idx:02d} recenter failed: "
                        f"{'; '.join(motion.warnings)}"
                    )
                    log.warning(msg)
                    self.error.emit(msg)
                    self._stop_requested = True
                    break

        return record

    def _log_offset(self, label: str) -> Optional[tuple[float, float]]:
        """Read SCAN.OFFSET.{X,Y}.NM, log it at INFO, return the (x,y) tuple.

        Returns None if the read fails. Used to trace where the scan
        window actually is at each phase of a routine — vital for
        debugging cases where 'set X, scan there' produces results in
        a different XY than expected.
        """
        try:
            pos = self._motion_manager().read_position_nm()
            xy = pos.xy if pos is not None else None
        except Exception as e:
            log.warning("offset read failed at %s: %s", label, e)
            return None
        if xy is None:
            log.info("%s: offset (read returned None)", label)
            return None
        log.info("%s: offset X=%.3f nm  Y=%.3f nm", label, xy[0], xy[1])
        return xy

    def _verify_move(self, label: str, before: Optional[tuple[float, float]],
                     expected_dx_nm: float, expected_dy_nm: float,
                     tol_nm: float = 0.5) -> None:
        """Log a WARNING if the actual change in offset disagrees with the
        commanded shift by more than ``tol_nm``."""
        if before is None:
            return
        after = self._log_offset(f"{label} (after)")
        if after is None:
            return
        actual_dx = after[0] - before[0]
        actual_dy = after[1] - before[1]
        if (abs(actual_dx - expected_dx_nm) > tol_nm
                or abs(actual_dy - expected_dy_nm) > tol_nm):
            log.warning(
                "%s: shift verification FAILED — expected (%+.3f, %+.3f) nm, "
                "got (%+.3f, %+.3f) nm",
                label, expected_dx_nm, expected_dy_nm, actual_dx, actual_dy,
            )


    def _emit_z_stability(self) -> None:
        """Pull the just-completed scan's topography and emit a stability metric."""
        try:
            img = self._stm.scan.live_data()
        except Exception:
            return
        if img is None:
            return
        try:
            metrics = compute_z_stability(img)
            self._last_z_stability = metrics
            self._acq_log.emit("z_stability", metrics=metrics)
            self.z_stability.emit(metrics)
        except Exception:
            log.debug("compute_z_stability failed", exc_info=True)

    def _do_mosaic_step(self, step: MosaicStep, label: str) -> None:
        """Wide overview → N×N zoom tiles (M iterations each) → wide overview.

        Each tile target is an absolute XY computed from the wide frame's
        offset readback (see mosaic.tile_targets_nm) and reached through
        TipMotionManager. Iterations per tile are repeats (optionally a
        bias sweep), not re-centring passes.
        """
        from datetime import datetime
        cfg = step.config
        tile_size_nm = cfg.resolved_tile_size_nm()
        n_tiles = cfg.total_tiles()
        n_iter = cfg.effective_iterations()

        output: Optional[Path] = None
        if cfg.output_folder:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output = Path(cfg.output_folder) / f"mosaic_{stamp}"
            output.mkdir(parents=True, exist_ok=True)

        # First thing on the worker thread: record where we're starting
        # from, before any apply() / scan / positioning call runs.
        self._log_offset(f"{cfg.name}: at Start click (worker-thread snapshot)")

        # ── 1. Wide overview (before) ─────────────────────────────────
        wide_params = ScanParams(
            bias_V=cfg.bias_V,
            setpoint_A=cfg.setpoint_A,
            size_nm=cfg.wide_size_nm,
            pixels=cfg.wide_pixels,
            speed_nm_s=cfg.wide_speed_nm_s,
            memo=f"{cfg.name} wide before",
        )
        self._apply_scan_params(wide_params)
        self._log_offset(f"{cfg.name}: wide_before apply")
        if cfg.settling_s > 0:
            self._sleep_with_progress(cfg.settling_s, f"{cfg.name}: wide settle")
            if self._stop_requested:
                return
        self.progress.emit(0, n_tiles + 2, f"{cfg.name}: wide before")
        wide_before_path = self._scan_and_save_to(output, "wide_before.dat")
        wide_before_img = self._stm.scan.live_data()
        if wide_before_path:
            self.scan_completed.emit(str(wide_before_path))
        if output is not None and wide_before_img is not None:
            _save_image_preview(wide_before_img, output / "wide_before.png")
        self._log_offset(f"{cfg.name}: wide_before scan complete")

        # Anchor: read the wide-frame's XY centre via SCAN.OFFSET.{X,Y}.NM.
        # Every tile target is wide_centre + tile-offset; the wide_after
        # scan at the end restores this exact XY.
        wide_pos = self._motion_manager().read_position_nm()
        wide_centre = wide_pos.xy if wide_pos is not None else None
        if wide_centre is None:
            self.error.emit(
                "Mosaic: couldn't read SCAN.OFFSET.{X,Y}.NM — aborting "
                "to avoid moving the tip blind."
            )
            return
        log.info("Mosaic wide centre anchored: X=%.3f nm, Y=%.3f nm",
                 wide_centre[0], wide_centre[1])
        self.info_message.emit(
            f"wide centre anchored: X={wide_centre[0]:+.3f} nm  "
            f"Y={wide_centre[1]:+.3f} nm"
        )

        # Derive the piezo V/nm calibration. The motion layer needs this
        # to convert nm → volts for the underlying Createc offset call
        # (empirically ~0.1 V/nm on this rig). Policy "current_then_delta":
        # try the cheap from-current ratio first; when the starting offset
        # is too small to trust (Createc rounds .VOLT readback to two
        # decimals), fall back to the delta probe — a known +0.1 V move
        # (≈1 nm) plus restore, which calibrates to well under 1%. If
        # neither works, abort rather than position the tip blind.
        self.info_message.emit(
            "Calibration: deriving V/nm (from current offset, falling back "
            "to a ±0.1 V delta probe — tip returns to start)"
        )
        try:
            cal = self._motion_manager().calibrate_xy("current_then_delta")
        except Exception as e:
            msg = (
                "Mosaic ABORTED — piezo calibration could not be derived "
                "(both from-current and delta-probe methods failed). "
                "Check the piezo is responding and SCAN.OFFSET keys are live. "
                f"{e}"
            )
            log.error(msg)
            self.info_message.emit("⚠ " + msg)
            self.error.emit(msg)
            return
        self.info_message.emit(
            f"XY calibration: {cal.volts_per_nm_x:.5f} V/nm (X), "
            f"{cal.volts_per_nm_y:.5f} V/nm (Y)"
        )

        # ── 2. Tile loop ─────────────────────────────────────────────
        # Absolute positioning: for each tile, compute the target XY in
        # nm and move through TipMotionManager. No accumulation, no scale
        # ambiguity. The pixel→nm conversion and the keep-inside-wide-frame
        # clamp live in mosaic.tile_targets_nm / scan_geometry — the clamp
        # honours both Createc conventions (X = frame centre, Y = top edge).
        for tile_idx, target_x_nm, target_y_nm, was_clamped in tile_targets_nm(
            cfg, wide_centre[0], wide_centre[1],
        ):
            if self._stop_requested:
                break
            self._wait_if_paused()
            self.mosaic_tile_started.emit(tile_idx, n_tiles)
            self.progress.emit(tile_idx, n_tiles + 2,
                               f"{cfg.name}: tile {tile_idx}/{n_tiles}")

            target_nm = (target_x_nm, target_y_nm)
            if was_clamped:
                msg = (
                    f"tile {tile_idx:02d}: requested frame extends outside the "
                    f"wide field — clamped to X={target_nm[0]:+.3f} nm, "
                    f"Y={target_nm[1]:+.3f} nm. Check tile_size_nm/grid_n; "
                    "tiles will overlap or leave gaps."
                )
                log.warning(msg)
                self.info_message.emit("⚠ " + msg)

            try:
                self.info_message.emit(
                    f"tile {tile_idx:02d}/{n_tiles}: target "
                    f"X={target_nm[0]:+.3f} nm  Y={target_nm[1]:+.3f} nm  "
                    f"(Δ from wide home: {target_nm[0] - wide_centre[0]:+.2f}, "
                    f"{target_nm[1] - wide_centre[1]:+.2f})"
                )
                motion = self._motion_manager().move_absolute_nm(
                    target_nm[0],
                    target_nm[1],
                    reason=f"mosaic tile {tile_idx:02d} target",
                    settle_s=cfg.settling_s,
                )
                self._record_motion_result(motion)
                if not motion.ok:
                    msg = (
                        f"tile {tile_idx:02d}: positioning failed — "
                        f"{'; '.join(motion.warnings)}"
                    )
                    log.warning(msg)
                    self.info_message.emit("⚠ " + msg)
                    self.error.emit(msg)
                    return
                log.info(
                    "tile %02d: move_absolute_nm(%.3f, %.3f) "
                    "[Δ from wide home: %+.3f, %+.3f nm]",
                    tile_idx, target_nm[0], target_nm[1],
                    target_nm[0] - wide_centre[0], target_nm[1] - wide_centre[1],
                )
                actual = self._log_offset(f"tile {tile_idx:02d} after positioning")
                if actual is not None:
                    err_x = actual[0] - target_nm[0]
                    err_y = actual[1] - target_nm[1]
                    self.info_message.emit(
                        f"tile {tile_idx:02d}/{n_tiles}: actual "
                        f"X={actual[0]:+.3f} nm  Y={actual[1]:+.3f} nm  "
                        f"(err: {err_x:+.3f}, {err_y:+.3f} nm)"
                    )
                    if abs(err_x) > 0.5 or abs(err_y) > 0.5:
                        msg = (f"tile {tile_idx:02d}: positioning mismatch — wanted "
                               f"({target_nm[0]:.3f}, {target_nm[1]:.3f}), "
                               f"Createc reports ({actual[0]:.3f}, {actual[1]:.3f}), "
                               f"err ({err_x:+.3f}, {err_y:+.3f}) nm")
                        log.warning(msg)
                        self.info_message.emit("⚠ " + msg)
            except Exception as e:
                log.warning("tile %02d positioning failed: %s", tile_idx, e)
                self.info_message.emit(f"⚠ tile {tile_idx:02d} positioning failed: {e}")

            # 2c–d. Per-iteration loop.  Each iteration applies its own bias
            # so a bias sweep is supported (each iter at a different voltage).
            # apply() preserves the XY offset set by set_offset_nm() above.
            bias_seq = cfg.effective_bias_sequence()
            zoom_nm_per_px_x = tile_size_nm[0] / max(cfg.tile_pixels[0], 1)
            zoom_nm_per_px_y = tile_size_nm[1] / max(cfg.tile_pixels[1], 1)

            for it, iter_bias_V in enumerate(bias_seq):
                if self._stop_requested:
                    break

                # Safety guard: never scan constant-current at ~0 V.
                if abs(iter_bias_V) < MIN_CONST_CURRENT_BIAS_V:
                    msg = (f"tile {tile_idx:02d} iter{it + 1}: "
                           f"|bias|={abs(iter_bias_V) * 1000:.2f} mV "
                           f"< {MIN_CONST_CURRENT_BIAS_V * 1000:.2f} mV — "
                           "skipping (constant-current scan at 0 V would crash the tip)")
                    log.warning(msg)
                    self.info_message.emit("⚠ " + msg)
                    continue

                # Apply scan params for this iteration (bias may differ).
                iter_params = ScanParams(
                    bias_V=iter_bias_V,
                    setpoint_A=cfg.setpoint_A,
                    size_nm=tile_size_nm,
                    pixels=cfg.tile_pixels,
                    speed_nm_s=cfg.tile_speed_nm_s,
                    memo=(f"{cfg.name} tile {tile_idx:02d} iter{it + 1} "
                          f"{iter_bias_V * 1000:.1f} mV"),
                )
                self._stm.scan.apply(iter_params)
                self._log_offset(f"tile {tile_idx:02d} iter{it + 1} after apply")

                if cfg.settling_s > 0:
                    self._sleep_with_progress(
                        cfg.settling_s,
                        f"tile {tile_idx:02d} iter{it + 1} "
                        f"{iter_bias_V * 1000:.1f} mV settle",
                    )
                    if self._stop_requested:
                        break

                # Filenames: include the bias tag when a sweep is active so
                # each image is self-documenting (e.g. tile_02_iter3_+100mV.dat).
                bias_tag = (f"_{iter_bias_V * 1000:+.0f}mV"
                            if cfg.bias_sweep else "")
                dat_name = f"tile_{tile_idx:02d}_iter{it + 1}{bias_tag}.dat"
                png_name = f"tile_{tile_idx:02d}_iter{it + 1}{bias_tag}.png"

                self._log_offset(f"tile {tile_idx:02d} iter{it + 1} before scan")
                path = self._scan_and_save_to(output, dat_name)
                if path is not None:
                    self.scan_completed.emit(str(path))
                self._log_offset(f"tile {tile_idx:02d} iter{it + 1} after scan")

                img = self._stm.scan.live_data()
                if img is None:
                    continue
                if output is not None:
                    _save_image_preview(img, output / png_name)

                # Z stability per scan
                try:
                    stability = compute_z_stability(img)
                    self._last_z_stability = stability
                    self._acq_log.emit("z_stability", metrics=stability)
                    self.z_stability.emit(stability)
                except Exception:
                    pass



            self.mosaic_tile_done.emit(tile_idx)

        # ── 3. Wide overview (after) ──────────────────────────────────
        # Re-apply wide params; the XY offset is wherever the last tile
        # left it — that's fine, we just snap back to the wide frame.
        wide_after_params = ScanParams(
            bias_V=cfg.bias_V,
            setpoint_A=cfg.setpoint_A,
            size_nm=cfg.wide_size_nm,
            pixels=cfg.wide_pixels,
            speed_nm_s=cfg.wide_speed_nm_s,
            memo=f"{cfg.name} wide after",
        )
        # Return to the wide centre via absolute motion, so
        # wide_after is anchored at the same XY as wide_before.
        self._log_offset(f"{cfg.name}: before return-to-centre")
        try:
            log.info("wide-after: move_absolute_nm(%.3f, %.3f)",
                     wide_centre[0], wide_centre[1])
            motion = self._motion_manager().move_absolute_nm(
                wide_centre[0],
                wide_centre[1],
                reason=f"{cfg.name}: return to wide centre",
                settle_s=cfg.settling_s,
            )
            self._record_motion_result(motion)
            if not motion.ok:
                msg = f"return-to-centre failed: {'; '.join(motion.warnings)}"
                log.warning(msg)
                self.error.emit(msg)
                self._stop_requested = True
        except Exception as e:
            log.warning("return-to-centre failed: %s", e)
        self._log_offset(f"{cfg.name}: after return-to-centre")

        self._apply_scan_params(wide_after_params)
        if cfg.settling_s > 0:
            self._sleep_with_progress(cfg.settling_s, f"{cfg.name}: wide-after settle")
        if not self._stop_requested:
            self.progress.emit(n_tiles + 1, n_tiles + 2, f"{cfg.name}: wide after")
            self._log_offset(f"{cfg.name}: wide_after before scan")
            wide_after_path = self._scan_and_save_to(output, "wide_after.dat")
            wide_after_img = self._stm.scan.live_data()
            if wide_after_path:
                self.scan_completed.emit(str(wide_after_path))
            if output is not None and wide_after_img is not None:
                _save_image_preview(wide_after_img, output / "wide_after.png")
            self._log_offset(f"{cfg.name}: wide_after scan complete")



        if output is not None:
            self.mosaic_finished.emit(str(output))
            log.info("Mosaic finished — output: %s", output)

    def _scan_and_save_to(self, output: Optional[Path], filename: str) -> Optional[Path]:
        """Run one scan, save under ``output/filename`` if given, return path."""
        scan = self._stm.scan
        role = Path(filename).stem
        if output is not None:
            self._ensure_acq_log_for_folder(output)
        else:
            try:
                self._ensure_acq_log_for_folder(Path(self._stm.raw.savedatfilename).parent)
            except Exception:
                pass
        self._acq_log.emit(
            "scan_started",
            role=role,
            step_index=self._current_step_index,
            label=self._current_label,
            scan_parameters=self._active_scan_params,
        )
        scan.start()
        if not self._wait_for_scan_with_live_emit():
            return None
        if output is None:
            try:
                scan.save_dat(str(self._stm.raw.savedatfilename))
            except Exception:
                pass
            path = scan.last_saved_path()
            if path is not None:
                self._record_scan_artifacts(Path(path), role=role)
            return path
        target = output / filename
        try:
            scan.save_dat(str(target))
        except Exception as e:
            log.warning("save_dat failed for %s: %s", target, e)
            return None
        quality = None
        try:
            img = scan.live_data()
            if img is not None:
                self._last_z_stability = compute_z_stability(img)
                quality = {"z_stability": self._last_z_stability}
        except Exception:
            quality = None
        self._record_scan_artifacts(target, role=role, quality=quality)
        return target

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
        # Poll instead of coarse.wait_for_approach(): the runner must stay
        # responsive to Stop during an approach that can take many minutes.
        # No current-threshold check here — the whole point of an approach
        # is for the current to rise to the setpoint, so the tip-crash
        # threshold would false-trigger.
        deadline = time.time() + float(step.timeout_s)
        finished = False
        while time.time() < deadline:
            if self._stop_requested:
                self._stm.coarse.stop_approach()
                log.info("Approach stopped by user request: %s", label)
                return
            if self._stm.coarse.approach_finished:
                finished = True
                break
            time.sleep(1.0)
        if not finished:
            self._stm.coarse.stop_approach()
            self.error.emit(f"Approach timed out: {label}")
        log.info("Approach step finished: %s", label)

    def _sleep_with_progress(self, seconds: float, label: str,
                             check_safety: bool = True) -> None:
        """Sleep emitting per-second updates so the GUI can show a countdown.

        Polls the safety monitor once per second — a tip contact during a
        long wait or settle must abort the run just like one during a scan.
        Raises SafetyViolation accordingly.
        """
        end = time.time() + seconds
        while True:
            remaining = end - time.time()
            if remaining <= 0 or self._stop_requested:
                break
            if check_safety:
                self._check_safety()
            self.settling.emit(max(0, int(remaining)), label)
            time.sleep(min(1.0, remaining))

    def _scan_and_save(self, recipe: MeasurementRecipe) -> Optional[Path]:
        scan = self._stm.scan
        if recipe.save_folder:
            self._ensure_acq_log_for_folder(Path(recipe.save_folder))
        else:
            try:
                self._ensure_acq_log_for_folder(Path(self._stm.raw.savedatfilename).parent)
            except Exception:
                pass
        self._acq_log.emit(
            "scan_started",
            role="data",
            step_index=self._current_step_index,
            label=self._current_label,
            scan_parameters=self._active_scan_params,
        )
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
        """Wait for the active scan to finish, polling safety as it runs.

        Uses the event bridge as a wake-up signal when available; otherwise
        falls back to fixed-interval polling. The safety check runs every
        iteration (cheap: one ADC read + one getp) and raises
        ``SafetyViolation`` if the threshold is hit.

        Live frames are decoupled from the safety poll: a full-frame
        DATA.SCAN pull is an expensive COM transfer executed on STMAFM's
        GUI thread, so frames are pulled only when a consumer opted in via
        :meth:`enable_live_frames`, and never more often than its interval.
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
            if self._emergency_stop_requested:
                log.warning("Emergency stop requested — retracting tip")
                self._safety.emergency_stop(self._stm)
                # Wait for STMAFM to actually halt before we let the worker
                # exit and tear down COM. Releasing the proxy while STMAFM
                # is still mid-stop is the canonical 'STMAFM crashed after
                # I clicked Stop' pattern.
                self._wait_for_scan_idle(timeout_s=5.0)
                return False
            if self._stop_requested:
                scan.stop()
                self._wait_for_scan_idle(timeout_s=5.0)
                return False
            self._wait_if_paused()
            # Safety check — raises SafetyViolation if threshold exceeded
            self._check_safety()
            # Pull one live frame for the viewer — opt-in and rate-limited
            frame_interval = self._live_frame_interval_s
            if frame_interval is not None:
                now = time.monotonic()
                if now - self._last_frame_pull >= frame_interval:
                    self._last_frame_pull = now
                    try:
                        frame = scan.live_data()
                        if frame is not None:
                            self._publish_live_frame(frame)
                    except Exception:
                        log.debug("live_data() raised", exc_info=True)
            # Either wake on an event or sleep the polling interval
            bridge.consume_flag(timeout=poll_interval_s)
        return True

    def _wait_for_scan_idle(self, timeout_s: float = 5.0,
                             poll_s: float = 0.1) -> bool:
        """Block until STMAFM reports the scan as no longer running.

        Used right after we send STMAFM.BTN.STOP — without this delay, we
        return immediately, the worker thread exits, and the finally
        block tears down our COM proxy while STMAFM is still in its
        stop-handling code. That's the standard 'STMAFM crashed after I
        clicked Stop' recipe.

        Returns True if the scan went idle within ``timeout_s``, False if
        the timeout fired (we still let the caller proceed in that case
        — better than hanging the worker forever).
        """
        import time as _time
        scan = self._stm.scan
        deadline = _time.monotonic() + float(timeout_s)
        while _time.monotonic() < deadline:
            try:
                if not scan.is_running:
                    log.info("scan idle confirmed after stop")
                    return True
            except Exception as e:
                log.debug("is_running check raised: %s", e)
                return False
            _time.sleep(poll_s)
        log.warning("scan still running %.1fs after stop request — proceeding anyway",
                    timeout_s)
        return False

    def _check_safety(self) -> None:
        """Raise SafetyViolation if the current threshold is exceeded.

        Also fails closed on persistent current-readback failure: the
        monitor reports read_failed and eventually flips ok=False (see
        SafetyConfig.max_read_failures) — without this, a broken readback
        would silently run the whole recipe unprotected.
        """
        status = self._safety.check(self._stm)
        if status.read_failed:
            n = self._safety.consecutive_read_failures
            if n == self._safety.config.warn_read_failures:
                msg = (f"safety: current readback failed {n}× in a row — "
                       "tip-crash protection is degraded")
                log.warning(msg)
                self._acq_log.emit("safety_read_failure", consecutive=n)
                self.info_message.emit("⚠ " + msg)
        if status.measured_current_A is not None:
            current = abs(status.measured_current_A)
            if self._max_observed_current_A is None:
                self._max_observed_current_A = current
            else:
                self._max_observed_current_A = max(self._max_observed_current_A, current)
            self._acq_log.emit("safety_reading", current_A=current, ok=status.ok)
            self.safety_reading.emit(current)
        if not status.ok:
            self._acq_log.emit(
                "safety_abort",
                reason=status.reason,
                current_A=status.measured_current_A,
            )
            raise SafetyViolation(status.reason,
                                  current_A=status.measured_current_A)

    def _wait_if_paused(self) -> None:
        if self._pause_requested:
            self._set_state(RunnerState.PAUSED)
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.2)
            if not self._stop_requested:
                self._set_state(RunnerState.RUNNING)


def _save_image_preview(arr: np.ndarray, path: Path) -> Optional[Path]:
    """Render a 2-D scan array as a greyscale PNG using matplotlib."""
    try:
        import matplotlib
        matplotlib.use("Agg")  # headless backend
        import matplotlib.pyplot as plt
        from scanflow.automation.feature_discovery import _level_correct

        levelled = _level_correct(arr.astype(float))
        fig, ax = plt.subplots(figsize=(4, 4), dpi=150)
        ax.imshow(levelled, cmap="afmhot", origin="lower")
        ax.set_axis_off()
        fig.tight_layout(pad=0)
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, bbox_inches="tight", pad_inches=0)
        plt.close(fig)
        return path
    except Exception as e:
        log.warning("Could not write preview %s: %s", path, e)
        return None


def _save_overview_preview(
    arr: np.ndarray,
    candidates: list,
    path: Path,
) -> Optional[Path]:
    """Render the wide scan with numbered boxes around each discovered feature."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
        from scanflow.automation.feature_discovery import _level_correct

        levelled = _level_correct(arr.astype(float))
        fig, ax = plt.subplots(figsize=(6, 6), dpi=150)
        ax.imshow(levelled, cmap="afmhot", origin="lower")
        for i, c in enumerate(candidates, start=1):
            min_row, min_col, max_row, max_col = c.bbox_px
            rect = Rectangle((min_col, min_row), max_col - min_col, max_row - min_row,
                             fill=False, edgecolor="cyan", linewidth=1.5)
            ax.add_patch(rect)
            ax.text(max_col + 2, min_row, str(i),
                    color="cyan", fontsize=10, fontweight="bold",
                    bbox=dict(facecolor="black", alpha=0.5, pad=1, edgecolor="none"))
        ax.set_axis_off()
        fig.tight_layout(pad=0)
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, bbox_inches="tight", pad_inches=0.05)
        plt.close(fig)
        return path
    except Exception as e:
        log.warning("Could not write overview preview %s: %s", path, e)
        return None
