"""Background temperature poller — reads all cryostat sensors on a QTimer.

Modelled after ZMonitor: a QObject with a QTimer that polls every
``poll_interval_s``, keeps a rolling history buffer, emits per-sensor
summaries at ``summary_interval_s``, and fires ``refill_detected`` when
any sensor drops more than ``refill_threshold_K`` in a single poll
(almost always an LN2 refill event).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import fields as dc_fields
from typing import Optional, Tuple

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from scanflow.core import activity

from .temperature import TemperatureMonitor, TemperatureReading

log = logging.getLogger(__name__)

# Human-readable labels for each TemperatureReading field
SENSOR_LABELS: dict[str, str] = {
    "stm":     "STM stage",
    "cryo_4K": "4K shield",
    "cryo_1K": "1K stage",
    "one_K":   "1K head",
    "adc2_K":  "ADC2",
    "adc3_K":  "ADC3",
    "aux6_K":  "STM (AUX6)",   # T_AUXADC6[K] = STM temperature (per py-createc)
    "aux7_K":  "LHe (AUX7)",   # T_AUXADC7[K] = LHe bath temperature
}

SENSOR_COLORS: dict[str, str] = {
    "stm":     "#0077b6",
    "cryo_4K": "#f77f00",
    "cryo_1K": "#2dc653",
    "one_K":   "#e63946",
    "adc2_K":  "#9b2226",
    "adc3_K":  "#6a4c93",
    "aux6_K":  "#457b9d",
    "aux7_K":  "#a8dadc",
}

SENSOR_FIELDS: list[str] = [f.name for f in dc_fields(TemperatureReading)]


def format_summary(stats: dict) -> str:
    """One-line log string from a summary dict."""
    parts = []
    for window in ("1h", "3h"):
        wdata = stats.get(window, {})
        if not wdata:
            continue
        sensor_parts = []
        for field, sd in wdata.items():
            label = SENSOR_LABELS.get(field, field)
            sensor_parts.append(
                f"{label}: {sd['latest_K']:.2f} K (Δ{sd['delta_K']:.3f} K)"
            )
        if sensor_parts:
            parts.append(f"{window}: " + "  |  ".join(sensor_parts))
    if not parts:
        return "T summary: no data"
    return "T " + "    ".join(parts)


class TemperaturePoller(QObject):
    """Polls cryostat temperature channels and reports events.

    Signals
    -------
    sample_added(t, reading)
        Emitted on every successful poll.
    summary(stats_dict)
        Emitted every ``summary_interval_s``. Each entry is keyed by
        window name ("1h", "3h") then by sensor field name, with sub-keys
        ``min_K``, ``max_K``, ``delta_K``, ``latest_K``, ``n``.
    refill_detected(sensor_field, delta_K)
        Emitted when a sensor drops more than ``refill_threshold_K`` in
        one poll interval — almost certainly a LN2 refill.
    """

    sample_added    = Signal(float, object)   # (t_epoch, TemperatureReading)
    summary         = Signal(dict)
    refill_detected = Signal(str, float)      # (sensor_field, delta_K)

    def __init__(
        self,
        stm=None,
        *,
        poll_interval_s: float = 30.0,
        summary_interval_s: float = 3600.0,
        refill_threshold_K: float = 2.0,
        max_history_s: float = 12 * 3600.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._stm = stm
        self._poll_s = float(poll_interval_s)
        self._summary_s = float(summary_interval_s)
        self._refill_K = float(refill_threshold_K)
        max_samples = int(max_history_s / max(poll_interval_s, 1.0)) + 100
        self._buffer: deque[Tuple[float, TemperatureReading]] = deque(maxlen=max_samples)
        self._last_reading: Optional[TemperatureReading] = None
        self._last_summary_t: Optional[float] = None
        # Refill event log: list of (t, sensor_field, delta_K)
        # Bounded: refills are rare physical events, but a stuck sensor
        # oscillating across the threshold must not grow this forever.
        self._refill_events: deque[Tuple[float, str, float]] = deque(maxlen=500)

        self._timer = QTimer(self)
        self._timer.setInterval(int(self._poll_s * 1000))
        self._automation_skips = 0
        self._timer.timeout.connect(self._timer_poll)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._timer.start()

    def poll_now(self) -> None:
        """Trigger an immediate poll outside the normal timer cadence.

        Call this right after connecting the STM so the Temperature tab
        populates without waiting for the first 30-second tick.
        """
        self._poll()

    def stop(self) -> None:
        self._timer.stop()

    def clear(self) -> None:
        self._buffer.clear()
        self._last_reading = None
        self._last_summary_t = None
        self._refill_events.clear()

    def set_stm(self, stm) -> None:
        self._stm = stm

    def is_running(self) -> bool:
        return self._timer.isActive()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _timer_poll(self) -> None:
        # Thin to every 4th tick (~2 min at the default 30 s cadence)
        # while automation runs — temperature.read() is several COM calls
        # serialised onto the STMAFM GUI thread (H7). poll_now() bypasses
        # the thinning for explicit user-triggered reads.
        if activity.is_active():
            self._automation_skips += 1
            if self._automation_skips % 4:
                return
        else:
            self._automation_skips = 0
        self._poll()

    def _poll(self) -> None:
        if self._stm is None:
            return
        # Use is_mock to skip the connected check in mock mode (avoids a
        # COM round-trip that can flip False during a scan on some rigs).
        is_mock = getattr(self._stm, "is_mock", False)
        if not is_mock:
            connected = getattr(self._stm, "connected", True)
            if not connected:
                return
        try:
            reading = self._stm.temperature.read()
        except Exception as e:
            log.warning("temperature poll failed: %s", e)
            return

        t = time.time()

        # Log active sensor values at INFO on every poll so the file log
        # shows that temperature reading is working.
        active_vals = {
            f: getattr(reading, f)
            for f in SENSOR_FIELDS
            if getattr(reading, f) is not None
        }
        if active_vals:
            parts = "  ".join(
                f"{SENSOR_LABELS.get(f, f)}={v:.3f} K"
                for f, v in active_vals.items()
            )
            log.info("Temperature: %s", parts)
        else:
            # Warn once if all sensors return None (likely wrong COM key names).
            if not self._buffer:
                log.warning(
                    "Temperature poll succeeded but all sensor keys returned empty "
                    "— check the COM key names in TemperatureMonitor.read()"
                )

        self._check_refill(t, reading)
        self._buffer.append((t, reading))
        self._last_reading = reading
        self.sample_added.emit(t, reading)

        if self._last_summary_t is None:
            self._last_summary_t = t
        elif t - self._last_summary_t >= self._summary_s:
            stats = self._compute_summary()
            self.summary.emit(stats)
            self._last_summary_t = t

    def _check_refill(self, t: float, new: TemperatureReading) -> None:
        if self._last_reading is None:
            return
        for field in SENSOR_FIELDS:
            prev = getattr(self._last_reading, field)
            curr = getattr(new, field)
            if prev is None or curr is None:
                continue
            delta = curr - prev
            if delta < -abs(self._refill_K):
                log.info("Refill detected on %s: ΔT = %.3f K", field, delta)
                self._refill_events.append((t, field, delta))
                self.refill_detected.emit(field, delta)

    def _compute_summary(self) -> dict:
        result: dict[str, dict] = {}
        if not self._buffer:
            return result
        t_now = self._buffer[-1][0]
        for window_name, window_s in [("1h", 3600.0), ("3h", 10800.0)]:
            cutoff = t_now - window_s
            window = [(t, r) for t, r in self._buffer if t >= cutoff]
            if len(window) < 2:
                continue
            result[window_name] = {}
            for field in SENSOR_FIELDS:
                vals = [
                    getattr(r, field)
                    for _, r in window
                    if getattr(r, field) is not None
                ]
                if len(vals) < 2:
                    continue
                result[window_name][field] = {
                    "min_K":    float(min(vals)),
                    "max_K":    float(max(vals)),
                    "delta_K":  float(max(vals) - min(vals)),
                    "latest_K": float(vals[-1]),
                    "n":        len(vals),
                }
        return result

    # ------------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------------

    def get_samples(self, field: str) -> Tuple[np.ndarray, np.ndarray]:
        """Return (timestamps_epoch, temperatures_K) for one sensor."""
        ts, ks = [], []
        for t, r in self._buffer:
            v = getattr(r, field, None)
            if v is not None:
                ts.append(t)
                ks.append(v)
        if not ts:
            return np.empty(0), np.empty(0)
        return np.asarray(ts, dtype=float), np.asarray(ks, dtype=float)

    def get_latest(self) -> Optional[TemperatureReading]:
        return self._last_reading

    def active_sensors(self) -> list[str]:
        """Sensor fields that have at least one non-None reading."""
        seen: set[str] = set()
        for _, r in self._buffer:
            for field in SENSOR_FIELDS:
                if getattr(r, field) is not None:
                    seen.add(field)
        return [f for f in SENSOR_FIELDS if f in seen]

    def refill_events(self) -> list[Tuple[float, str, float]]:
        """Return list of (t_epoch, sensor_field, delta_K) for all refill events."""
        return list(self._refill_events)
