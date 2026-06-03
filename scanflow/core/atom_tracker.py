"""5-point Z-gradient atom tracker for lateral drift correction.

The tracker probes Z feedback at five positions around a reference point
(centre + 4 cardinal directions) in sequence:

    origin → Z0 → Z+x → Z-x → Z+y → Z-y → origin

From the four off-centre readings it computes the gradient of the Z
signal, which points toward the adsorbate. Applying a proportional gain
gives the XY correction to apply to the scan offset.

All movement uses ``stm.scan.set_offset_nm`` (which calls Createc's
SETXYOFF.VOLT command and physically moves the tip) followed by a brief
settle delay before reading ``getdacvalfb()``.

Thread safety: the worker runs in a QThread; all log signals are emitted
from the worker thread and connected to the GUI log via Qt queued
connections.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)

# 5-point probe sequence: (dx_factor, dy_factor) relative to reference
# Chosen to minimise tip travel and let Z re-stabilise near centre.
_PROBE_SEQ: list[Tuple[float, float]] = [
    (0.0,  0.0),   # Z0  — centre baseline
    (+1.0, 0.0),   # Z+x
    (-1.0, 0.0),   # Z-x  (passes back through centre vicinity)
    (0.0, +1.0),   # Z+y
    (0.0, -1.0),   # Z-y
]


@dataclass
class TrackResult:
    """Result of one 5-point drift measurement."""
    ref_x_nm: float
    ref_y_nm: float
    z_readings: list[float]           # [Z0, Z+x, Z-x, Z+y, Z-y] in DAC counts
    gradient_x: float                 # Z+x - Z-x  (DAC counts)
    gradient_y: float                 # Z+y - Z-y
    feature_strength: float           # Z0 - mean(off-centre)  (quality indicator)
    dx_correction_nm: float           # correction applied (or 0 if skipped)
    dy_correction_nm: float
    applied: bool                     # True if correction was applied
    skip_reason: str                  # non-empty when applied=False


def _find_reference_pixel(
    z: np.ndarray,
    prominence_frac: float = 0.05,
) -> Optional[Tuple[int, int]]:
    """Find the most prominent local maximum in a 2-D Z array.

    Uses a simple neighbourhood-max check (no scipy required).
    Returns (row, col) or None if no feature meets the prominence threshold.
    """
    z_range = float(np.ptp(z))
    if z_range == 0:
        return None
    min_prominence = z_range * prominence_frac

    rows, cols = z.shape
    best_row, best_col = -1, -1
    best_prom = 0.0

    # Vectorised: build a (rows-2) x (cols-2) mask of local maxima
    centre = z[1:-1, 1:-1]
    for dr in range(-1, 2):
        for dc in range(-1, 2):
            if dr == 0 and dc == 0:
                continue
            neighbour = z[1 + dr: rows - 1 + dr, 1 + dc: cols - 1 + dc]
            centre = np.minimum(centre, neighbour)  # running minimum of neighbours

    # centre now holds min(neighbourhood) for each interior pixel
    prominence_map = z[1:-1, 1:-1] - centre
    if prominence_map.max() < min_prominence:
        return None

    flat_idx = int(np.argmax(prominence_map))
    best_row = flat_idx // (cols - 2) + 1
    best_col = flat_idx % (cols - 2) + 1
    return (best_row, best_col)


def find_reference_nm(
    z: np.ndarray,
    scan_x_nm: float,
    scan_y_nm: float,
    home_x_nm: float,
    home_y_nm: float,
    prominence_frac: float = 0.05,
) -> Optional[Tuple[float, float]]:
    """Convert the best-peak pixel in ``z`` to absolute nm coordinates.

    ``home_x_nm`` / ``home_y_nm`` is the scan origin (top-left in the
    Createc convention: SCAN.OFFSET.X.NM = centre, SCAN.OFFSET.Y.NM =
    top edge). The image centre in Y = home_y_nm + scan_y_nm/2.

    Returns (x_nm, y_nm) in the same coordinate system as SCAN.OFFSET,
    or None if no suitable feature was found.
    """
    peak = _find_reference_pixel(z, prominence_frac=prominence_frac)
    if peak is None:
        return None
    row, col = peak
    rows, cols = z.shape
    # col → x: left edge = home_x_nm - scan_x_nm/2
    x_nm = (home_x_nm - scan_x_nm / 2.0) + (col / max(cols - 1, 1)) * scan_x_nm
    # row → y: top edge = home_y_nm (Createc convention)
    y_nm = home_y_nm + (row / max(rows - 1, 1)) * scan_y_nm
    return (x_nm, y_nm)


class _FivePointWorker(QThread):
    """Executes the 5-point Z-gradient measurement in a background thread.

    Emits log_message for every step so the operator can debug from the
    log panel even before the result is validated on the actual rig.
    """

    result_ready = Signal(object)    # TrackResult
    log_message  = Signal(str)
    error_message = Signal(str)

    def __init__(
        self,
        stm,
        ref_x_nm: float,
        ref_y_nm: float,
        *,
        delta_nm: float = 0.5,
        settle_s: float = 0.15,
        gain: float = 0.5,
        min_feature_strength: float = 50.0,
        max_correction_nm: float = 2.0,
        apply_correction: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._stm = stm
        self._ref_x = float(ref_x_nm)
        self._ref_y = float(ref_y_nm)
        self._delta = float(delta_nm)
        self._settle = float(settle_s)
        self._gain = float(gain)
        self._min_strength = float(min_feature_strength)
        self._max_corr = float(max_correction_nm)
        self._apply = bool(apply_correction)

    # ------------------------------------------------------------------

    def run(self) -> None:
        stm = self._stm
        try:
            stm.bind_thread()
        except Exception as e:
            self.error_message.emit(f"AtomTracker: bind_thread failed: {e}")
            return

        self.log_message.emit(
            f"AtomTracker: 5-point probe at ({self._ref_x:.3f}, {self._ref_y:.3f}) nm  "
            f"δ={self._delta:.2f} nm  settle={self._settle:.2f} s"
        )

        z_readings: list[float] = []
        probe_labels = ["Z0(centre)", "Z+x", "Z-x", "Z+y", "Z-y"]

        for i, (dx_f, dy_f) in enumerate(_PROBE_SEQ):
            probe_x = self._ref_x + dx_f * self._delta
            probe_y = self._ref_y + dy_f * self._delta
            try:
                stm.scan.set_offset_nm(probe_x, probe_y, settle_s=self._settle)
                time.sleep(self._settle)
                z_raw = float(stm.raw.getdacvalfb())
                z_readings.append(z_raw)
                self.log_message.emit(
                    f"  {probe_labels[i]:12s}  pos=({probe_x:.3f}, {probe_y:.3f}) nm  "
                    f"Z={z_raw:.1f} DAC"
                )
            except Exception as e:
                self.error_message.emit(f"AtomTracker: probe {probe_labels[i]} failed: {e}")
                stm.unbind_thread()
                return

        # Move tip back to reference
        try:
            stm.scan.set_offset_nm(self._ref_x, self._ref_y, settle_s=self._settle)
        except Exception as e:
            self.error_message.emit(f"AtomTracker: return-to-ref failed: {e}")
            stm.unbind_thread()
            return

        z0, zpx, zmx, zpy, zmy = z_readings
        gradient_x = zpx - zmx
        gradient_y = zpy - zmy
        feature_strength = z0 - 0.25 * (zpx + zmx + zpy + zmy)

        self.log_message.emit(
            f"AtomTracker: gradient_x={gradient_x:+.1f}  gradient_y={gradient_y:+.1f}  "
            f"feature_strength={feature_strength:.1f} DAC  "
            f"(threshold={self._min_strength:.1f})"
        )

        # Quality gate
        if feature_strength < self._min_strength:
            result = TrackResult(
                ref_x_nm=self._ref_x, ref_y_nm=self._ref_y,
                z_readings=z_readings,
                gradient_x=gradient_x, gradient_y=gradient_y,
                feature_strength=feature_strength,
                dx_correction_nm=0.0, dy_correction_nm=0.0,
                applied=False,
                skip_reason=f"feature_strength {feature_strength:.1f} < {self._min_strength:.1f}",
            )
            self.log_message.emit(
                f"AtomTracker: no feature at reference — skipping correction "
                f"({result.skip_reason})"
            )
            self.result_ready.emit(result)
            stm.unbind_thread()
            return

        # Correction: move toward higher Z (gradient direction)
        z_range = max(abs(gradient_x), abs(gradient_y), 1.0)
        dx = self._gain * self._delta * gradient_x / z_range
        dy = self._gain * self._delta * gradient_y / z_range

        # Clamp
        dx = max(-self._max_corr, min(self._max_corr, dx))
        dy = max(-self._max_corr, min(self._max_corr, dy))

        if self._apply:
            new_x = self._ref_x + dx
            new_y = self._ref_y + dy
            try:
                stm.scan.set_offset_nm(new_x, new_y, settle_s=self._settle)
                self.log_message.emit(
                    f"AtomTracker: correction applied  "
                    f"Δx={dx:+.3f} nm  Δy={dy:+.3f} nm  "
                    f"→ new ref ({new_x:.3f}, {new_y:.3f}) nm"
                )
            except Exception as e:
                self.error_message.emit(f"AtomTracker: apply correction failed: {e}")
                stm.unbind_thread()
                return
        else:
            self.log_message.emit(
                f"AtomTracker: correction NOT applied (dry-run)  "
                f"Δx={dx:+.3f} nm  Δy={dy:+.3f} nm"
            )

        result = TrackResult(
            ref_x_nm=self._ref_x, ref_y_nm=self._ref_y,
            z_readings=z_readings,
            gradient_x=gradient_x, gradient_y=gradient_y,
            feature_strength=feature_strength,
            dx_correction_nm=dx if self._apply else 0.0,
            dy_correction_nm=dy if self._apply else 0.0,
            applied=self._apply,
            skip_reason="",
        )
        self.result_ready.emit(result)
        stm.unbind_thread()
