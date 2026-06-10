"""Safe scan-frame XY motion management.

The low-level Createc offset commands remain on :mod:`scanflow.core.scan`.
This module adds the policy layer automation code needs: calibration,
readback, move limits, settling, and structured motion results.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from .safety import SafetyMonitor

if TYPE_CHECKING:
    from .stm_client import STMClient

log = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class XYPosition:
    x_nm: float
    y_nm: float
    source: str
    timestamp: str | None = None

    @property
    def xy(self) -> tuple[float, float]:
        return (self.x_nm, self.y_nm)


@dataclass(frozen=True)
class XYCalibration:
    volts_per_nm_x: float
    volts_per_nm_y: float
    source: str
    confidence: str
    timestamp: str | None = None


@dataclass(frozen=True)
class MotionConfig:
    max_single_move_nm: float = 100.0
    readback_tolerance_nm: float = 2.0
    default_settle_s: float = 3.0
    max_retries: int = 1


@dataclass(frozen=True)
class MotionResult:
    ok: bool
    requested_nm: tuple[float, float]
    before_nm: tuple[float, float] | None
    after_nm: tuple[float, float] | None
    attempts: int
    reason: str
    warnings: list[str] = field(default_factory=list)


class TipMotionManager:
    """Policy layer for automated scan-frame XY movement.

    Automation should use this class instead of calling
    ``ScanController.set_offset_nm`` or ``nudge_offset_pixels`` directly.
    """

    def __init__(
        self,
        stm: "STMClient",
        safety: SafetyMonitor | None = None,
        config: MotionConfig | None = None,
    ) -> None:
        self._stm = stm
        self._safety = safety
        self.config = config or MotionConfig()
        self._calibration: XYCalibration | None = None

    @property
    def calibration(self) -> XYCalibration | None:
        return self._calibration

    def read_position_nm(self) -> XYPosition | None:
        """Read the current scan-frame XY offset in nanometres."""
        try:
            xy = self._stm.tip_xy_position()
            if xy is not None:
                return XYPosition(float(xy[0]), float(xy[1]), "tip_xy_position", _utc_now())
        except Exception as exc:
            log.debug("tip_xy_position read failed: %s", exc)

        try:
            xy = self._stm.scan.get_offset_nm()
            if xy is not None:
                return XYPosition(float(xy[0]), float(xy[1]), "scan_offset", _utc_now())
        except Exception as exc:
            log.debug("scan.get_offset_nm read failed: %s", exc)
        return None

    def calibrate_xy(self, strategy: str = "current_then_delta") -> XYCalibration:
        """Calibrate scan-offset volts/nm conversion.

        ``strategy`` may be ``"current"``, ``"delta"``, or
        ``"current_then_delta"``.
        """
        if strategy not in {"current", "delta", "current_then_delta"}:
            raise ValueError(f"unknown XY calibration strategy: {strategy!r}")

        result: Optional[tuple[float, float]] = None
        source = strategy
        if strategy in {"current", "current_then_delta"}:
            result = self._stm.scan.calibrate_xy_from_current()
            source = "current"
        if result is None and strategy in {"delta", "current_then_delta"}:
            result = self._stm.scan.calibrate_xy_by_delta()
            source = "delta"
        if result is None:
            raise RuntimeError("XY calibration failed; readback did not provide a usable V/nm ratio")

        cal = XYCalibration(
            volts_per_nm_x=float(result[0]),
            volts_per_nm_y=float(result[1]),
            source=source,
            confidence="medium" if source == "current" else "high",
            timestamp=_utc_now(),
        )
        self._calibration = cal
        return cal

    def assert_safe_to_move(self) -> None:
        """Raise if the instrument is not in a safe state for XY motion."""
        try:
            if self._stm.scan.is_running:
                raise RuntimeError("refusing XY motion while scan is running")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"could not verify scan idle state before motion: {exc}") from exc

        if self._safety is not None:
            status = self._safety.check(self._stm)
            if not status.ok:
                raise RuntimeError(status.reason or "safety monitor blocked XY motion")

    def move_absolute_nm(
        self,
        x_nm: float,
        y_nm: float,
        *,
        reason: str,
        settle_s: float | None = None,
        verify: bool = True,
    ) -> MotionResult:
        """Move scan-frame offset to an absolute XY position in nm."""
        target = (float(x_nm), float(y_nm))
        settle = self.config.default_settle_s if settle_s is None else float(settle_s)
        warnings: list[str] = []
        before_pos = self.read_position_nm()
        before = before_pos.xy if before_pos is not None else None

        if before is None:
            return MotionResult(False, target, None, None, 0, reason, [
                "cannot perform absolute move without position readback",
            ])
        move_mag = _distance_nm(before, target)
        if move_mag > self.config.max_single_move_nm:
            return MotionResult(False, target, before, before, 0, reason, [
                *warnings,
                f"requested move {move_mag:.3f} nm exceeds limit "
                f"{self.config.max_single_move_nm:.3f} nm",
            ])

        try:
            self.assert_safe_to_move()
        except Exception as exc:
            return MotionResult(False, target, before, before, 0, reason, [*warnings, str(exc)])

        try:
            if self._calibration is None:
                self.calibrate_xy()
        except Exception as exc:
            return MotionResult(False, target, before, before, 0, reason, [*warnings, str(exc)])

        try:
            log.info(
                "motion command: %s -> target=(%.3f, %.3f) nm",
                reason, target[0], target[1],
            )
            self._stm.scan.set_offset_nm(target[0], target[1])
        except Exception as exc:
            return MotionResult(False, target, before, before, 1, reason, [*warnings, f"motion command failed: {exc}"])

        if settle > 0:
            time.sleep(settle)

        if not verify:
            after_pos = self.read_position_nm()
            last_after = after_pos.xy if after_pos is not None else None
            return MotionResult(True, target, before, last_after, 1, reason, warnings)

        readback_attempts = max(1, int(self.config.max_retries) + 1)
        last_after = None
        last_warnings = list(warnings)
        for attempt in range(1, readback_attempts + 1):
            after_pos = self.read_position_nm()
            last_after = after_pos.xy if after_pos is not None else None
            if last_after is None:
                last_warnings = [*warnings, "position readback unavailable after move"]
            else:
                err = _distance_nm(last_after, target)
                if err <= self.config.readback_tolerance_nm:
                    return MotionResult(True, target, before, last_after, attempt, reason, warnings)
                last_warnings = [
                    *warnings,
                    f"readback error {err:.3f} nm exceeds tolerance "
                    f"{self.config.readback_tolerance_nm:.3f} nm",
                ]
            if attempt < readback_attempts:
                time.sleep(min(0.05 * attempt, 0.2))

        return MotionResult(False, target, before, last_after, readback_attempts, reason, last_warnings)

    def move_relative_nm(
        self,
        dx_nm: float,
        dy_nm: float,
        *,
        reason: str,
        settle_s: float | None = None,
        verify: bool = True,
    ) -> MotionResult:
        """Move scan-frame offset by a relative XY delta in nm."""
        dx = float(dx_nm)
        dy = float(dy_nm)
        if _distance_nm((0.0, 0.0), (dx, dy)) > self.config.max_single_move_nm:
            return MotionResult(
                False,
                (dx, dy),
                None,
                None,
                0,
                reason,
                [f"requested relative move exceeds limit {self.config.max_single_move_nm:.3f} nm"],
            )
        before = self.read_position_nm()
        if before is None:
            return MotionResult(False, (dx, dy), None, None, 0, reason, [
                "cannot perform relative move without position readback",
            ])
        return self.move_absolute_nm(
            before.x_nm + dx,
            before.y_nm + dy,
            reason=reason,
            settle_s=settle_s,
            verify=verify,
        )

    def move_relative_pixels(
        self,
        dx_px: float,
        dy_px: float,
        *,
        frame_size_nm: tuple[float, float],
        frame_pixels: tuple[int, int],
        reason: str,
        settle_s: float | None = None,
        verify: bool = True,
    ) -> MotionResult:
        """Convert a pixel displacement in the current frame to nm and move."""
        sx, sy = float(frame_size_nm[0]), float(frame_size_nm[1])
        px, py = max(int(frame_pixels[0]), 1), max(int(frame_pixels[1]), 1)
        return self.move_relative_nm(
            float(dx_px) * sx / px,
            float(dy_px) * sy / py,
            reason=reason,
            settle_s=settle_s,
            verify=verify,
        )


def _distance_nm(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
