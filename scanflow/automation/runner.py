"""Automation runner: executes a MeasurementRecipe against a live STM.

Runs in a QThread so the GUI stays responsive. Emits Qt signals for progress,
drift results, new scan files, and errors.
"""

from __future__ import annotations

import logging
import time
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from PySide6.QtCore import QThread, Signal

from scanflow.core import (
    STMClient, STMNotConnectedError, ScanParams, IVTable,
    SafetyMonitor, SafetyConfig, SafetyViolation,
)
from scanflow.automation.recipe import (
    MeasurementRecipe, ScanStep, SpectroscopyStep, ApproachStep, WaitStep,
    SurveyStep, MosaicStep, MIN_CONST_CURRENT_BIAS_V,
)
from scanflow.automation.survey import SurveyConfig, FeatureRecord, SurveyManifest
from scanflow.automation.mosaic import MosaicConfig, tile_centers_in_wide_pixels
from scanflow.automation.feature_discovery import discover_features, FeatureCandidate
from scanflow.automation.scan_metrics import compute_z_stability, format_z_stability
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
        """Hard-terminate the runner thread. Last resort if soft stop hangs."""
        log.warning("AutomationRunner.force_stop() — terminating runner thread")
        self._stop_requested = True
        self._emergency_stop_requested = True
        if self.isRunning():
            self.terminate()
            self.wait(2000)

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
        # COM proxies are apartment-bound — re-dispatch them on this worker
        # thread before any setp/getp, otherwise the first call raises
        # RPC_E_WRONG_THREAD ("interface marshalled for a different thread").
        self._stm.bind_thread()
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
        finally:
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

        if recipe.drift_correction:
            self._detector = DriftDetector(
                continuous=True,
                method=getattr(recipe, "drift_method", "hybrid"),
            )

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
                elif kind == "survey":
                    self._do_survey_step(step, label)
                elif kind == "mosaic":
                    self._do_mosaic_step(step, label)
                else:
                    log.warning("Unknown step kind: %s — skipping", kind)

                if recipe.inter_step_delay_s > 0:
                    self._sleep_with_progress(
                        recipe.inter_step_delay_s, "Inter-step pause"
                    )

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
        self._stm.scan.apply(params)

        if step.settling_s > 0:
            self._sleep_with_progress(step.settling_s, f"Settling: {label}")
            if self._stop_requested:
                return

        if recipe.drift_correction and self._reference_array is not None:
            self._do_alignment_scan(recipe, data_params=params)
            if self._stop_requested:
                return

        dat_path = self._scan_and_save(recipe)
        if dat_path:
            log.info("Saved: %s", dat_path)
            self.scan_completed.emit(str(dat_path))
            # Z-stability snapshot: emit before reference handling so the GUI
            # sees one line per data scan regardless of drift state.
            self._emit_z_stability()
            if self._reference_array is None and recipe.drift_correction:
                # live_data() is always available immediately after a scan —
                # no createc file library dependency. Fall back to disk only
                # if the DSP buffer is empty for some reason.
                self._reference_array = self._stm.scan.live_data()
                if self._reference_array is None:
                    self._reference_array = self._load_channel(
                        dat_path, recipe.drift_channel)
                self._reference_timestamp = time.time()
                if self._reference_array is not None:
                    log.info("Drift reference captured — correction active from next scan")

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
        self._stm.scan.apply(wide_params)
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
        # Track the cumulative wide-pixel target so we can nudge by the
        # *delta* between consecutive features. We always restore the wide
        # frame params before nudging, so the nm-per-pixel scale used by
        # nudge_offset_pixels is the wide image's scale.
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

        Positioning: nudges the XY offset by the wide-frame pixel delta from the
        previous feature's wide-pixel position. The wide frame params are
        re-applied first so ``nudge_offset_pixels`` uses the wide nm-per-pixel
        scale — not the previous feature's zoom scale, which would land the
        next scan in the wrong place entirely.
        """
        wide_cx = cfg.wide_pixels[0] / 2.0
        wide_cy = cfg.wide_pixels[1] / 2.0
        dx_nm_log = (cand.cx_px - wide_cx) * wide_nm_per_px
        dy_nm_log = (cand.cy_px - wide_cy) * wide_nm_per_px

        self.survey_feature_started.emit(
            idx, total, cand.char_dim_nm, dx_nm_log, dy_nm_log,
        )

        # 1. Restore wide-frame params (preserves XY offset, sets nm-per-pixel
        # scale so the next nudge is interpreted in wide-image pixels).
        try:
            self._stm.scan.apply(wide_params)
        except Exception as e:
            log.warning("could not re-apply wide params before nudge: %s", e)

        # 2. Shift the offset by (target - previous) in wide-pixel units.
        prev_x, prev_y = getattr(self, "_last_wide_px",
                                 (wide_cx, wide_cy))
        dx_px = float(cand.cx_px - prev_x)
        dy_px = float(cand.cy_px - prev_y)
        try:
            self._stm.scan.nudge_offset_pixels(dx_px, dy_px)
            self._last_wide_px = (float(cand.cx_px), float(cand.cy_px))
            log.info("Feature %d nudge: Δpx=(%+0.2f, %+0.2f) → Δnm=(%+0.2f, %+0.2f)",
                     idx, dx_px, dy_px,
                     dx_px * wide_nm_per_px, dy_px * wide_nm_per_px)
        except Exception as e:
            log.warning("nudge_offset_pixels failed: %s", e)

        # 3. Apply zoom params (the XY offset stays where we just placed it).
        zoom_params = ScanParams(
            bias_V=cfg.bias_V,
            setpoint_A=cfg.setpoint_A,
            size_nm=cand.zoom_nm,
            pixels=cfg.zoom_pixels,
            speed_nm_s=cfg.zoom_speed_nm_s,
            memo=f"{cfg.name} f{idx:02d}",
        )
        self._stm.scan.apply(zoom_params)

        record = FeatureRecord(
            index=idx,
            centroid_pixels=(cand.cx_px, cand.cy_px),
            centroid_nm_offset=(dx_nm, dy_nm),
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
            # apply()/nudge() commands above.
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
                try:
                    self._stm.scan.nudge_offset_pixels(-float(dx_px), -float(dy_px))
                except Exception as e:
                    log.warning("nudge_offset_pixels failed: %s", e)

        return record

    def _log_offset(self, label: str) -> Optional[Tuple[float, float]]:
        """Read SCAN.OFFSET.{X,Y}.NM, log it at INFO, return the (x,y) tuple.

        Returns None if the read fails. Used to trace where the scan
        window actually is at each phase of a routine — vital for
        debugging cases where 'set X, scan there' produces results in
        a different XY than expected.
        """
        try:
            xy = self._stm.scan.get_offset_nm()
        except Exception as e:
            log.warning("offset read failed at %s: %s", label, e)
            return None
        if xy is None:
            log.info("%s: offset (read returned None)", label)
            return None
        log.info("%s: offset X=%.3f nm  Y=%.3f nm", label, xy[0], xy[1])
        return xy

    def _verify_nudge(self, label: str, before: Optional[Tuple[float, float]],
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
            self.z_stability.emit(metrics)
        except Exception:
            log.debug("compute_z_stability failed", exc_info=True)

    def _do_mosaic_step(self, step: MosaicStep, label: str) -> None:
        """Wide overview → 3×3 zoom tiles (N iterations each) → wide overview.

        Each tile is centered by nudging the XY offset in *wide-frame* pixels
        — same trick as the Survey path. Drift correction between tile
        iterations uses cross-correlation against iteration 1.
        """
        from datetime import datetime
        cfg = step.config
        tile_size_nm = cfg.resolved_tile_size_nm()
        n_tiles = cfg.total_tiles()
        n_iter = max(1, cfg.iterations_per_tile)

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
        self._stm.scan.apply(wide_params)
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
        wide_centre = self._stm.scan.get_offset_nm()
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

        # Derive the piezo V/nm calibration from the current offset.
        # set_offset_nm() needs this to convert nm → volts for the
        # underlying setxyoffvolt COM call. Empirically ~0.1 V/nm on this
        # rig. If both X and Y are near zero we can't derive — abort
        # rather than position the tip with a bad calibration.
        cal = self._stm.scan.calibrate_xy_from_current()
        if cal is None:
            msg = (
                "Mosaic ABORTED — piezo calibration could not be derived "
                "(wide_centre is at or near the piezo origin). "
                "Move STMAFM to a non-zero offset (≥ 0.05 nm) and re-start."
            )
            log.error(msg)
            self.info_message.emit("⚠ " + msg)
            self.error.emit(msg)
            return
        self.info_message.emit(
            f"XY calibration: {cal[0]:.5f} V/nm (X), {cal[1]:.5f} V/nm (Y)"
        )

        # Tile pixel-offsets → nm offsets in the wide frame
        wide_nm_per_px_x = cfg.wide_size_nm[0] / max(cfg.wide_pixels[0], 1)
        wide_nm_per_px_y = cfg.wide_size_nm[1] / max(cfg.wide_pixels[1], 1)
        max_excursion_x = cfg.wide_size_nm[0] / 2.0
        max_excursion_y = cfg.wide_size_nm[1] / 2.0

        # ── 2. Tile loop ─────────────────────────────────────────────
        # Absolute positioning: for each tile, compute the target XY in
        # nm and call set_offset_nm() (which on this rig invokes
        # setxyoffvolt(x_nm, y_nm) — verified working from the lab
        # 2026-05-20 probe logs). No accumulation, no scale ambiguity.
        for tile_idx, target_x_px, target_y_px in tile_centers_in_wide_pixels(cfg):
            if self._stop_requested:
                break
            self._wait_if_paused()
            self.mosaic_tile_started.emit(tile_idx, n_tiles)
            self.progress.emit(tile_idx, n_tiles + 2,
                               f"{cfg.name}: tile {tile_idx}/{n_tiles}")

            # Pixel-from-wide-centre → nm-from-wide-centre → absolute nm.
            dx_nm = (target_x_px - cfg.wide_pixels[0] / 2.0) * wide_nm_per_px_x
            dy_nm = (target_y_px - cfg.wide_pixels[1] / 2.0) * wide_nm_per_px_y
            # Safety clamp: never request a tile centre more than half a
            # wide-field away — anything outside is a math typo.
            dx_nm = max(-max_excursion_x, min(max_excursion_x, dx_nm))
            dy_nm = max(-max_excursion_y, min(max_excursion_y, dy_nm))
            target_nm = (wide_centre[0] + dx_nm, wide_centre[1] + dy_nm)

            try:
                self.info_message.emit(
                    f"tile {tile_idx:02d}/{n_tiles}: target "
                    f"X={target_nm[0]:+.3f} nm  Y={target_nm[1]:+.3f} nm  "
                    f"(Δ centre: {dx_nm:+.2f}, {dy_nm:+.2f})"
                )
                self._stm.scan.set_offset_nm(target_nm[0], target_nm[1])
                log.info(
                    "tile %02d: set_offset_nm(%.3f, %.3f) "
                    "[Δ from centre: %+.3f, %+.3f nm]",
                    tile_idx, target_nm[0], target_nm[1], dx_nm, dy_nm,
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
                log.warning("tile %02d set_offset_nm failed: %s", tile_idx, e)
                self.info_message.emit(f"⚠ tile {tile_idx:02d} positioning failed: {e}")

            # 2c. Apply tile params (offset preserved).
            tile_params = ScanParams(
                bias_V=cfg.bias_V,
                setpoint_A=cfg.setpoint_A,
                size_nm=tile_size_nm,
                pixels=cfg.tile_pixels,
                speed_nm_s=cfg.tile_speed_nm_s,
                memo=f"{cfg.name} tile {tile_idx:02d}",
            )
            self._stm.scan.apply(tile_params)
            self._log_offset(f"tile {tile_idx:02d} after apply(tile_params)")

            # 2d. Iterations with drift correction against iter 1.
            tile_ref: Optional[np.ndarray] = None
            zoom_nm_per_px_x = tile_size_nm[0] / max(cfg.tile_pixels[0], 1)
            zoom_nm_per_px_y = tile_size_nm[1] / max(cfg.tile_pixels[1], 1)
            for it in range(n_iter):
                if self._stop_requested:
                    break
                if cfg.settling_s > 0:
                    self._sleep_with_progress(
                        cfg.settling_s,
                        f"tile {tile_idx:02d} iter{it + 1} settle",
                    )
                    if self._stop_requested:
                        break

                self._log_offset(
                    f"tile {tile_idx:02d} iter{it + 1} before scan"
                )
                dat_name = f"tile_{tile_idx:02d}_iter{it + 1}.dat"
                png_name = f"tile_{tile_idx:02d}_iter{it + 1}.png"
                path = self._scan_and_save_to(output, dat_name)
                if path is not None:
                    self.scan_completed.emit(str(path))
                self._log_offset(
                    f"tile {tile_idx:02d} iter{it + 1} after scan"
                )

                img = self._stm.scan.live_data()
                if img is None:
                    continue
                if output is not None:
                    _save_image_preview(img, output / png_name)

                # Z stability per scan
                try:
                    self.z_stability.emit(compute_z_stability(img))
                except Exception:
                    pass

                # Drift correction: first iteration becomes the reference;
                # subsequent iterations cross-correlate against it and nudge.
                if it == 0:
                    tile_ref = img
                elif tile_ref is not None:
                    try:
                        detector = DriftDetector(
                            continuous=False,
                            method=getattr(self._recipe, "drift_method", "hybrid"),
                        )
                        result = detector.measure(reference=tile_ref, current=img)
                        if (abs(result.dx_pixels) + abs(result.dy_pixels)) > 0.5:
                            # Pixel shift the cross-correlation found
                            # → expected nm shift in current (tile) frame
                            exp_dx_nm = result.dx_pixels * zoom_nm_per_px_x
                            exp_dy_nm = result.dy_pixels * zoom_nm_per_px_y
                            before = self._log_offset(
                                f"tile {tile_idx:02d} iter{it + 1} drift nudge "
                                f"({result.dx_pixels:+.2f}, {result.dy_pixels:+.2f}) px "
                                f"= expected ({exp_dx_nm:+.3f}, {exp_dy_nm:+.3f}) nm"
                            )
                            self._stm.scan.nudge_offset_pixels(
                                result.dx_pixels, result.dy_pixels,
                            )
                            self._verify_nudge(
                                f"tile {tile_idx:02d} iter{it + 1} nudge",
                                before, exp_dx_nm, exp_dy_nm,
                            )
                            self.drift_measured.emit(result)
                    except Exception as e:
                        log.debug("tile drift step failed: %s", e)

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
        # Return to the wide centre via absolute set_offset_nm(), so
        # wide_after is anchored at the same XY as wide_before.
        self._log_offset(f"{cfg.name}: before return-to-centre")
        try:
            log.info("wide-after: set_offset_nm(%.3f, %.3f)",
                     wide_centre[0], wide_centre[1])
            self._stm.scan.set_offset_nm(wide_centre[0], wide_centre[1])
        except Exception as e:
            log.warning("return-to-centre failed: %s", e)
        self._log_offset(f"{cfg.name}: after return-to-centre")

        self._stm.scan.apply(wide_after_params)
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
        scan.start()
        if not self._wait_for_scan_with_live_emit():
            return None
        if output is None:
            try:
                scan.save_dat(str(self._stm.raw.savedatfilename))
            except Exception:
                pass
            return scan.last_saved_path()
        target = output / filename
        try:
            scan.save_dat(str(target))
        except Exception as e:
            log.warning("save_dat failed for %s: %s", target, e)
            return None
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
            if self._emergency_stop_requested:
                log.warning("Emergency stop requested — retracting tip")
                self._safety.emergency_stop(self._stm)
                return False
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

    def _do_alignment_scan(
        self,
        recipe: MeasurementRecipe,
        data_params: Optional["ScanParams"] = None,
    ) -> None:
        """Run a tracking scan and nudge the offset to keep features centred.

        If ``recipe.fast_alignment`` is True and ``data_params`` is given,
        the alignment scan runs at half the data-scan pixel count (same
        physical area). The reference is downsampled to match for the
        correlation, and the data-scan pixels are restored before the
        caller's data scan starts. Halves the alignment scan's wall-clock
        time at the cost of slightly coarser cross-correlation precision.

        The alignment frame is captured via ``live_data()`` and *not* saved
        to disk.
        """
        from dataclasses import replace as _dc_replace
        scan = self._stm.scan

        fast = bool(getattr(recipe, "fast_alignment", False)) and data_params is not None
        if fast:
            align_params = _dc_replace(
                data_params,
                pixels=(max(64, int(data_params.pixels[0]) // 2),
                        max(64, int(data_params.pixels[1]) // 2)),
                memo=f"{data_params.memo} (fast align)" if data_params.memo else "fast align",
            )
            scan.apply(align_params)

        scan.start()
        if not self._wait_for_scan_with_live_emit():
            if fast and data_params is not None:
                scan.apply(data_params)
            return
        current_array = scan.live_data()
        if current_array is None or self._reference_array is None:
            if fast and data_params is not None:
                scan.apply(data_params)
            return

        # Cross-correlation needs matching shapes; downsample the full-res
        # reference onto the alignment frame's grid if they differ.
        ref_for_corr = self._reference_array
        if fast and ref_for_corr.shape != current_array.shape:
            try:
                from skimage.transform import resize
                ref_for_corr = resize(
                    self._reference_array,
                    current_array.shape,
                    mode="reflect",
                    anti_aliasing=True,
                    preserve_range=True,
                )
            except Exception as e:
                log.warning("Reference resize failed (%s); skipping alignment", e)
                if data_params is not None:
                    scan.apply(data_params)
                return

        cur_ts = time.time()
        result = self._detector.measure(
            reference=ref_for_corr,
            current=current_array,
            ref_timestamp=self._reference_timestamp,
            cur_timestamp=cur_ts,
            extra_seconds=recipe.drift_reposition_delay_s,
        )
        self.drift_measured.emit(result)
        log.info(
            "Drift%s: dx=%.2f Å, dy=%.2f Å, magnitude=%.2f Å, confidence=%.2f",
            " (fast)" if fast else "",
            result.dx_angstrom, result.dy_angstrom,
            result.magnitude_angstrom, result.confidence,
        )

        # Nudge *while* the alignment frame is still active so the pixel
        # delta is interpreted in alignment-frame pixels (correct physical
        # shift either way: dx_align_px × align_nm_per_px = dx_data_px ×
        # data_nm_per_px, since size_nm is unchanged between modes).
        self._stm.scan.nudge_offset_pixels(result.dx_pixels, result.dy_pixels)

        # Restore data-scan pixels before the caller's data scan starts.
        if fast and data_params is not None:
            scan.apply(data_params)

        self._sleep_with_progress(recipe.drift_reposition_delay_s,
                                  "Drift reposition")
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


def _save_image_preview(arr: np.ndarray, path: Path) -> Optional[Path]:
    """Render a 2-D scan array as a greyscale PNG using matplotlib."""
    try:
        import matplotlib
        matplotlib.use("Agg")  # headless backend
        import matplotlib.pyplot as plt
        from scanflow.drift.detector import _level_correct

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
        from scanflow.drift.detector import _level_correct

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
