"""Worker that scans clustered feature groups one frame at a time.

For each :class:`FeatureGroup`:
1. Computes the absolute target offset (centre X, top edge Y) from the
   wide-scan home anchor plus the group's centre offset.
2. Clamps the target so the group frame stays inside the wide-scan
   boundary.
3. Moves the tip via :func:`TipMotionManager.move_absolute_nm`.
4. Applies a group-sized :class:`ScanParams`. If a ``target_scan_time_s``
   was requested, recomputes ``speed_nm_s`` per-group so that every group
   completes in approximately the same wall-clock time regardless of size.
5. Runs ``group_iterations`` passes (or a bias sequence) with a settle
   between each save.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from scanflow.core import (
    STMClient,
    ScanParams,
    SafetyConfig,
    SafetyMonitor,
    TipMotionManager,
)
from scanflow.core.scan import (
    estimate_scan_duration_s as _estimate_scan_duration,
    estimate_scan_timeout_s as _estimate_scan_timeout,
    format_duration as _format_duration,
)

from .paths import unique_dat_path

log = logging.getLogger(__name__)


class FeatureGroupScanWorker(QThread):
    """Scan a list of FeatureGroups one-by-one.

    Signals
    -------
    group_started(group_idx, total, label)
    group_scan_saved(group_idx, dat_path)
    failed(message)
        Emitted on any per-group error; worker continues with remaining groups.
    finished_all(output_folder)
        Emitted after the last group completes successfully.
    """

    group_started = Signal(int, int, str)
    group_scan_saved = Signal(int, str)
    failed = Signal(str)
    finished_all = Signal(str)

    def __init__(
        self,
        stm: STMClient,
        source_path: Path,
        groups: list,                    # list[FeatureGroup]; avoid hard import
        *,
        group_pixels: tuple[int, int] = (256, 256),
        group_speed_nm_s: float = 20.0,
        group_iterations: int = 3,
        settling_s: float = 3.0,
        output_folder: str = "",
        bias_V: float | None = None,
        setpoint_A: float | None = None,
        bias_sequence: list[float] | None = None,
        home_nm: tuple[float, float] | None = None,
        scan_range_nm: tuple[float, float] | None = None,
        target_scan_time_s: float | None = None,
    ) -> None:
        super().__init__()
        self._stm = stm
        self._source_path = Path(source_path)
        self._groups = list(groups)
        self._group_pixels = tuple(int(v) for v in group_pixels)
        self._group_speed_nm_s = float(group_speed_nm_s)
        self._target_scan_time_s = (
            float(target_scan_time_s) if target_scan_time_s is not None else None
        )
        self._group_iterations = int(group_iterations)
        self._settling_s = float(settling_s)
        self._output_folder = str(output_folder)
        self._bias_V = float(bias_V) if bias_V is not None else None
        self._setpoint_A = float(setpoint_A) if setpoint_A is not None else None
        self._bias_sequence = list(bias_sequence) if bias_sequence else []
        self._home_nm = home_nm
        self._scan_range_nm = scan_range_nm
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    # ------------------------------------------------------------------

    def run(self) -> None:  # pragma: no cover — exercised on the rig
        try:
            if not self._stm.bind_thread():
                raise RuntimeError("could not bind STM to group-scan worker thread")

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
            bias_V = self._bias_V if self._bias_V is not None else current.bias_V
            setpoint_A = self._setpoint_A if self._setpoint_A is not None else current.setpoint_A

            home_x, home_y = self._resolve_home(motion)

            output = self._resolve_output_folder()
            base_stem = self._source_path.stem
            total = len(self._groups)

            for g_idx, group in enumerate(self._groups, start=1):
                if self._stop_requested:
                    break

                target_x, target_y = self._compute_clamped_target(
                    group, home_x, home_y, g_idx, total,
                )

                label = (
                    f"Group {g_idx}/{total} "
                    f"({len(group.members)} feature{'s' if len(group.members) != 1 else ''},"
                    f" frame {group.frame_nm[0]:.1f}×{group.frame_nm[1]:.1f} nm,"
                    f" pos ({target_x:+.2f}, {target_y:+.2f}) nm)"
                )
                self.group_started.emit(g_idx, total, label)
                log.info(
                    "Group %d/%d: target (%.3f, %.3f) nm  Δ=(%.2f, %.2f) nm  frame %.1f×%.1f nm",
                    g_idx, total, target_x, target_y,
                    group.center_dx_nm, group.center_dy_nm,
                    group.frame_nm[0], group.frame_nm[1],
                )

                motion.assert_safe_to_move()
                if not self._move_to_group(motion, g_idx, total, target_x, target_y):
                    continue  # logged + emitted inside helper

                group_speed = self._effective_speed_nm_s(group, g_idx, total)
                self._scan_group(
                    g_idx, total, group, output, base_stem,
                    bias_V, setpoint_A, group_speed, target_x, target_y,
                )

            self.finished_all.emit(str(output))

        except Exception as exc:  # pragma: no cover — exercised on the rig
            log.exception("Group scan failed")
            self.failed.emit(str(exc))
        finally:
            try:
                self._stm.unbind_thread()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_home(self, motion: TipMotionManager) -> tuple[float, float]:
        if self._home_nm is not None:
            home_x, home_y = self._home_nm
            log.info(
                "Group scan worker: using pre-captured home X=%.3f nm  Y=%.3f nm",
                home_x, home_y,
            )
            return home_x, home_y
        home_pos = motion.read_position_nm()
        if home_pos is None:
            raise RuntimeError(
                "cannot read current scan offset — is the STM connected?"
            )
        log.info(
            "Group scan worker: home offset read at start X=%.3f nm  Y=%.3f nm",
            home_pos.x_nm, home_pos.y_nm,
        )
        return home_pos.x_nm, home_pos.y_nm

    def _resolve_output_folder(self) -> Path:
        if self._output_folder:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output = Path(self._output_folder) / f"group_scan_{stamp}"
            output.mkdir(parents=True, exist_ok=True)
            return output
        return self._source_path.parent

    def _compute_clamped_target(
        self,
        group,
        home_x: float,
        home_y: float,
        g_idx: int,
        total: int,
    ) -> tuple[float, float]:
        """Createc convention:
        - SCAN.OFFSET.X.NM = centre of scan frame (X)
        - SCAN.OFFSET.Y.NM = TOP EDGE (first scanline) of scan frame (Y)
        ProbeFlow's center_d{x,y}_nm are offsets from the wide-scan image centre.
        Wide-scan image centre Y = home_y + scan_range_y/2.
        """
        scan_range_y = self._scan_range_nm[1] if self._scan_range_nm is not None else 0.0
        scan_range_x = self._scan_range_nm[0] if self._scan_range_nm is not None else 0.0
        target_x = home_x + group.center_dx_nm
        target_y = (
            home_y
            + scan_range_y / 2.0
            + group.center_dy_nm
            - group.frame_nm[1] / 2.0
        )

        if self._scan_range_nm is None:
            return target_x, target_y

        half_fx = group.frame_nm[0] / 2.0
        frame_y = group.frame_nm[1]
        if group.frame_nm[0] <= scan_range_x:
            clamped_x = max(
                home_x - scan_range_x / 2.0 + half_fx,
                min(home_x + scan_range_x / 2.0 - half_fx, target_x),
            )
        else:
            clamped_x = target_x
        if frame_y <= scan_range_y:
            clamped_y = max(home_y, min(home_y + scan_range_y - frame_y, target_y))
        else:
            clamped_y = target_y

        if abs(clamped_x - target_x) > 0.01 or abs(clamped_y - target_y) > 0.01:
            log.warning(
                "Group %d/%d: target clamped (%.3f, %.3f) → (%.3f, %.3f) nm "
                "to keep %.1f×%.1f nm frame within %.1f×%.1f nm scan bounds",
                g_idx, total,
                target_x, target_y, clamped_x, clamped_y,
                group.frame_nm[0], group.frame_nm[1],
                scan_range_x, scan_range_y,
            )
        return clamped_x, clamped_y

    def _move_to_group(
        self,
        motion: TipMotionManager,
        g_idx: int,
        total: int,
        target_x: float,
        target_y: float,
    ) -> bool:
        log.info(
            "Group %d/%d: moving to X=%.3f nm  Y=%.3f nm",
            g_idx, total, target_x, target_y,
        )
        move = motion.move_absolute_nm(
            target_x, target_y,
            reason=f"group {g_idx:02d} centre",
            settle_s=self._settling_s,
        )
        msg = (
            f"Group {g_idx}/{total} move: {'ok' if move.ok else 'FAILED'} "
            f"target=({target_x:+.3f}, {target_y:+.3f}) nm"
        )
        if move.before_nm is not None:
            msg += f"  before=({move.before_nm[0]:+.3f}, {move.before_nm[1]:+.3f}) nm"
        if move.after_nm is not None:
            msg += f"  after=({move.after_nm[0]:+.3f}, {move.after_nm[1]:+.3f}) nm"
            err_x = move.after_nm[0] - target_x
            err_y = move.after_nm[1] - target_y
            msg += f"  err=({err_x:+.3f}, {err_y:+.3f}) nm"
            if abs(err_x) > 0.5 or abs(err_y) > 0.5:
                log.warning(
                    "Group %d/%d positioning mismatch — wanted (%.3f, %.3f) nm, "
                    "got (%.3f, %.3f) nm, err (%+.3f, %+.3f) nm",
                    g_idx, total, target_x, target_y,
                    move.after_nm[0], move.after_nm[1], err_x, err_y,
                )
        if move.warnings:
            msg += "  [" + "; ".join(move.warnings) + "]"
        (log.info if move.ok else log.warning)(msg)
        if not move.ok:
            reason = "; ".join(move.warnings) if move.warnings else (move.reason or "unknown")
            self.failed.emit(f"Group {g_idx} positioning failed ({reason}), skipping.")
        return move.ok

    def _effective_speed_nm_s(self, group, g_idx: int, total: int) -> float:
        if self._target_scan_time_s is None or self._target_scan_time_s <= 0:
            return self._group_speed_nm_s
        speed = max(1.0, min(
            1000.0,
            (2.0 * group.frame_nm[0] * self._group_pixels[1]) / self._target_scan_time_s,
        ))
        log.info(
            "Group %d/%d: speed adjusted to %.1f nm/s "
            "for %.1f nm frame to match ~%s target",
            g_idx, total, speed, group.frame_nm[0],
            _format_duration(self._target_scan_time_s),
        )
        return speed

    def _scan_group(
        self,
        g_idx: int,
        total: int,
        group,
        output: Path,
        base_stem: str,
        bias_V: float,
        setpoint_A: float,
        group_speed: float,
        target_x: float,
        target_y: float,
    ) -> None:
        if self._bias_sequence:
            for b_idx, b in enumerate(self._bias_sequence):
                if self._stop_requested:
                    break
                self._run_one(
                    g_idx, total, group, output, base_stem,
                    b, setpoint_A, group_speed, target_x, target_y,
                    suffix=f"b{b_idx + 1}",
                    memo=f"group {g_idx:02d} b{b_idx + 1}",
                    settle_before=True,
                )
            return
        # Standard: fixed bias, multi-iteration. Apply params once, scan N times.
        params = ScanParams(
            bias_V=bias_V,
            setpoint_A=setpoint_A,
            size_nm=group.frame_nm,
            pixels=self._group_pixels,
            speed_nm_s=group_speed,
            memo=f"group {g_idx:02d}",
        )
        self._stm.scan.apply(params)
        for it in range(self._group_iterations):
            if self._stop_requested:
                break
            if self._settling_s > 0:
                time.sleep(self._settling_s)
            target_path = unique_dat_path(
                output, f"{base_stem}_group{g_idx:02d}_iter{it + 1}",
            )
            timeout_s = _estimate_scan_timeout(params)
            log.info(
                "Group %d/%d iter %d/%d: scanning %.1f×%.1f nm @ (%.3f, %.3f) nm  "
                "bias=%.4f V  ~%s",
                g_idx, total, it + 1, self._group_iterations,
                group.frame_nm[0], group.frame_nm[1],
                target_x, target_y, bias_V,
                _format_duration(_estimate_scan_duration(params)),
            )
            try:
                saved = self._stm.scan.scan_and_save(str(target_path), timeout_s=timeout_s)
            except Exception as exc:
                log.warning(
                    "Group %d iter %d scan error: %s — skipping iteration",
                    g_idx, it + 1, exc,
                )
                continue
            if saved is None:
                log.warning("Group %d iter %d timed out — skipping iteration", g_idx, it + 1)
                continue
            self.group_scan_saved.emit(g_idx, str(saved))

    def _run_one(
        self,
        g_idx: int,
        total: int,
        group,
        output: Path,
        base_stem: str,
        bias_V: float,
        setpoint_A: float,
        group_speed: float,
        target_x: float,
        target_y: float,
        *,
        suffix: str,
        memo: str,
        settle_before: bool,
    ) -> None:
        params = ScanParams(
            bias_V=bias_V,
            setpoint_A=setpoint_A,
            size_nm=group.frame_nm,
            pixels=self._group_pixels,
            speed_nm_s=group_speed,
            memo=memo,
        )
        self._stm.scan.apply(params)
        if settle_before and self._settling_s > 0:
            time.sleep(self._settling_s)
        target_path = unique_dat_path(
            output, f"{base_stem}_group{g_idx:02d}_{suffix}",
        )
        timeout_s = _estimate_scan_timeout(params)
        log.info(
            "Group %d/%d %s: scanning %.1f×%.1f nm @ (%.3f, %.3f) nm  bias=%.4f V  ~%s",
            g_idx, total, suffix,
            group.frame_nm[0], group.frame_nm[1],
            target_x, target_y, bias_V,
            _format_duration(_estimate_scan_duration(params)),
        )
        try:
            saved = self._stm.scan.scan_and_save(str(target_path), timeout_s=timeout_s)
        except Exception as exc:
            log.warning("Group %d %s scan error: %s — skipping", g_idx, suffix, exc)
            return
        if saved is None:
            log.warning("Group %d %s timed out — skipping", g_idx, suffix)
            return
        self.group_scan_saved.emit(g_idx, str(saved))
