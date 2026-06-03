"""Worker for preview-driven follow-up scans on detected features.

For each :class:`PreviewFeatureRow` target, the worker moves the tip to
the absolute position derived from the wide-scan home anchor plus the
ProbeFlow-reported feature offset, then runs one or more scans with the
requested bias / setpoint / size.

Coordinate convention (Createc):
- ``SCAN.OFFSET.X.NM`` = CENTRE of scan frame in X
- ``SCAN.OFFSET.Y.NM`` = TOP EDGE (first scanline) in Y
- ProbeFlow's ``dx_nm`` / ``dy_nm`` are offsets from the *image centre*
  (positive dy = below centre = larger Createc Y)

Therefore the absolute Y target for a feature is::

    feat_top_y = home_y + scan_range_y / 2  +  dy_nm  -  size_y / 2
                 └──── home is also a top edge ─┘   └─ shift to top edge of new frame
"""

from __future__ import annotations

import logging
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
from scanflow.core.scan_geometry import feature_target_xy_nm

from .paths import unique_dat_path

log = logging.getLogger(__name__)


class FeatureScanWorker(QThread):
    """Scan a list of preview-detected features, one at a time.

    Signals
    -------
    scan_saved(dat_path)
        Emitted after each successful save.
    failed(message)
        Emitted when a single target fails (worker continues with the rest).
    progress(current, total, label)
        Emitted before each target / repetition for status bar updates.
    """

    scan_saved = Signal(str)
    failed = Signal(str)
    progress = Signal(int, int, str)

    def __init__(
        self,
        stm: STMClient,
        source_path: Path,
        targets: list,                # list[PreviewFeatureRow], avoid hard import
        *,
        bias_V: float | None = None,
        setpoint_A: float | None = None,
        n_reps: int = 1,
        bias_sequence: list[float] | None = None,
        size_nm: float | None = None,  # None → inherit current STM size
        home_nm: tuple[float, float] | None = None,
        scan_range_nm: tuple[float, float] | None = None,
    ) -> None:
        super().__init__()
        self._stm = stm
        self._source_path = Path(source_path)
        self._targets = list(targets)
        self._stop_requested = False
        self._bias_V = float(bias_V) if bias_V is not None else None
        self._setpoint_A = float(setpoint_A) if setpoint_A is not None else None
        self._n_reps = max(1, int(n_reps))
        self._bias_sequence = list(bias_sequence) if bias_sequence else []
        self._size_nm = float(size_nm) if size_nm is not None else None
        self._home_nm = home_nm
        self._scan_range_nm = scan_range_nm

    def stop(self) -> None:
        self._stop_requested = True

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
            bias_V = self._bias_V if self._bias_V is not None else current.bias_V
            setpoint_A = self._setpoint_A if self._setpoint_A is not None else current.setpoint_A
            biases = self._bias_sequence if self._bias_sequence else [bias_V] * self._n_reps
            base_folder = self._source_path.parent
            base_stem = self._source_path.stem
            total = len(self._targets)
            for i, row in enumerate(self._targets, start=1):
                if self._stop_requested:
                    break
                self.progress.emit(i, total, f"Target {row.index + 1}/{total}")
                motion.assert_safe_to_move()
                scan_size_for_move = (
                    (self._size_nm, self._size_nm)
                    if self._size_nm is not None
                    else current.size_nm
                )
                if self._home_nm is not None and self._scan_range_nm is not None:
                    home_x, home_y = self._home_nm
                    abs_target_x, abs_target_y = feature_target_xy_nm(
                        home_x_nm=home_x,
                        home_y_nm=home_y,
                        scan_range_y_nm=self._scan_range_nm[1],
                        feature_dx_nm=row.dx_nm,
                        feature_dy_nm=row.dy_nm,
                        frame_height_nm=scan_size_for_move[1],
                    )
                    log.info(
                        "Feature scan target %d/%d: absolute move → (%.3f, %.3f) nm  "
                        "[home (%.3f, %.3f)  Δ=(%.3f, %.3f)]",
                        row.index + 1, total, abs_target_x, abs_target_y,
                        home_x, home_y, row.dx_nm, row.dy_nm,
                    )
                    move = motion.move_absolute_nm(
                        abs_target_x,
                        abs_target_y,
                        reason=f"preview follow-up target {row.index + 1}",
                        settle_s=3.0,
                    )
                else:
                    log.info(
                        "Feature scan target %d/%d: relative move Δ=(%.3f, %.3f) nm",
                        row.index + 1, total, row.dx_nm, row.dy_nm,
                    )
                    move = motion.move_relative_nm(
                        row.dx_nm,
                        row.dy_nm,
                        reason=f"preview follow-up target {row.index + 1}",
                        settle_s=3.0,
                    )
                _move_msg = (
                    f"Target {row.index + 1}/{total} move: {'ok' if move.ok else 'FAILED'} "
                )
                if move.before_nm is not None:
                    _move_msg += f"  before=({move.before_nm[0]:+.3f}, {move.before_nm[1]:+.3f}) nm"
                if move.after_nm is not None:
                    _move_msg += f"  after=({move.after_nm[0]:+.3f}, {move.after_nm[1]:+.3f}) nm"
                    if self._home_nm is not None and self._scan_range_nm is not None:
                        want_x, want_y = abs_target_x, abs_target_y
                    else:
                        bx = move.before_nm[0] if move.before_nm else 0.0
                        by = move.before_nm[1] if move.before_nm else 0.0
                        want_x, want_y = bx + row.dx_nm, by + row.dy_nm
                    err_x = move.after_nm[0] - want_x
                    err_y = move.after_nm[1] - want_y
                    _move_msg += f"  err=({err_x:+.3f}, {err_y:+.3f}) nm"
                    if abs(err_x) > 0.5 or abs(err_y) > 0.5:
                        log.warning(
                            "Target %d/%d positioning mismatch — err (%+.3f, %+.3f) nm",
                            row.index + 1, total, err_x, err_y,
                        )
                if move.warnings:
                    _move_msg += "  [" + "; ".join(move.warnings) + "]"
                log.info(_move_msg) if move.ok else log.warning(_move_msg)
                if not move.ok:
                    msg = (
                        f"target {row.index + 1} move failed: {move.reason} "
                        + ("; ".join(move.warnings) if move.warnings else "")
                    )
                    log.warning("%s — skipping target", msg)
                    self.failed.emit(msg)
                    continue

                for rep_idx, b in enumerate(biases):
                    if self._stop_requested:
                        break
                    rep_label = (
                        f"b{rep_idx + 1}/{len(biases)}" if self._bias_sequence
                        else f"rep{rep_idx + 1}/{len(biases)}"
                    ) if len(biases) > 1 else ""
                    suffix = f"_b{rep_idx + 1}" if self._bias_sequence else (
                        f"_rep{rep_idx + 1}" if len(biases) > 1 else ""
                    )
                    label = f"Target {row.index + 1}/{total}"
                    if rep_label:
                        label += f" {rep_label}"
                    self.progress.emit(i, total, label)
                    scan_size = (
                        (self._size_nm, self._size_nm)
                        if self._size_nm is not None
                        else current.size_nm
                    )
                    params = ScanParams(
                        bias_V=b,
                        setpoint_A=setpoint_A,
                        size_nm=scan_size,
                        speed_nm_s=current.speed_nm_s,
                        pixels=current.pixels,
                        rotation_deg=current.rotation_deg,
                        const_height=current.const_height,
                        channels=current.channels,
                        preamp_exponent=current.preamp_exponent,
                        memo=f"preview target {row.index + 1}{suffix}",
                    )
                    self._stm.scan.apply(params)
                    target = unique_dat_path(
                        base_folder,
                        f"{base_stem}_feature_{row.index + 1:02d}{suffix}",
                    )
                    timeout_s = _estimate_scan_timeout(params)
                    pos = self._stm.scan.get_offset_nm()
                    log.info(
                        "Feature %d/%d%s: scanning @ (%s) nm  bias=%.4f V  ~%s",
                        row.index + 1, total,
                        f" {rep_label}" if rep_label else "",
                        f"{pos[0]:+.3f}, {pos[1]:+.3f}" if pos else "n/a",
                        b,
                        _format_duration(_estimate_scan_duration(params)),
                    )
                    try:
                        saved = self._stm.scan.scan_and_save(str(target), timeout_s=timeout_s)
                    except Exception as exc:
                        log.warning(
                            "scan_and_save raised for target %d%s: %s",
                            row.index + 1,
                            f" {rep_label}" if rep_label else "",
                            exc,
                        )
                        continue
                    if saved is None:
                        log.warning(
                            "scan timed out for target %d%s, skipping",
                            row.index + 1,
                            f" {rep_label}" if rep_label else "",
                        )
                        continue
                    self.scan_saved.emit(str(saved))
        except Exception as exc:  # pragma: no cover - exercised via panel tests
            log.exception("Preview follow-up scan failed")
            self.failed.emit(str(exc))
        finally:
            try:
                self._stm.unbind_thread()
            except Exception:
                pass
