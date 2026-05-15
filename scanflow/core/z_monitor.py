"""Background poller for the tip Z position.

Calls ``stm.raw.getdacvalfb()`` on a QTimer — that's the Createc COM method
"get DAC value, feedback", which on a Createc rig is the live Z piezo DAC
reading. Keeps a rolling buffer of (t, z_raw) samples and reports ΔZ
statistics for arbitrary time windows.

Storage is in raw DAC units; the panel multiplies by a user-configurable
``dac_to_angstrom`` scale at display time so the calibration can be tuned
without throwing away history.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Optional, Tuple

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)


class ZMonitor(QObject):
    """Polls Z piezo feedback DAC and reports ΔZ over rolling windows.

    Signals
    -------
    sample_added(t_seconds, z_angstrom)
        Emitted once per poll for live plotting.
    summary(stats_dict)
        Emitted every ``summary_interval_s`` seconds (default 5 min) with
        ``{"5min": {...}, "1h": {...}, "3h": {...}}``. Each sub-dict carries
        ``ptp_A``, ``std_A``, ``drift_A_per_h``, ``n``, ``span_s``.
    """

    sample_added = Signal(float, float)
    summary = Signal(dict)

    DEFAULT_DAC_TO_ANGSTROM = 1.0  # placeholder until we know the rig's calibration

    def __init__(
        self,
        stm=None,
        *,
        interval_s: float = 1.0,
        max_history_s: float = 4 * 3600.0,
        summary_interval_s: float = 5 * 60.0,
        dac_to_angstrom: float = DEFAULT_DAC_TO_ANGSTROM,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._stm = stm
        self._interval_s = float(interval_s)
        self._summary_interval_s = float(summary_interval_s)
        self._scale = float(dac_to_angstrom)
        max_samples = int(max_history_s / max(interval_s, 0.1)) + 100
        self._buffer: deque[Tuple[float, float]] = deque(maxlen=max_samples)
        self._timer = QTimer(self)
        self._timer.setInterval(int(self._interval_s * 1000))
        self._timer.timeout.connect(self._poll)
        self._last_summary_t: Optional[float] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def clear(self) -> None:
        self._buffer.clear()
        self._last_summary_t = None

    def is_running(self) -> bool:
        return self._timer.isActive()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @property
    def scale(self) -> float:
        return self._scale

    def set_scale(self, dac_to_angstrom: float) -> None:
        """Update the DAC→Å conversion. Existing history is reinterpreted
        immediately (we store raw counts, convert on read)."""
        self._scale = float(dac_to_angstrom)

    def set_stm(self, stm) -> None:
        """Swap the STM client (used when the GUI re-connects)."""
        self._stm = stm

    # ------------------------------------------------------------------
    # Sample acquisition
    # ------------------------------------------------------------------

    def add_sample(self, t: float, raw: float) -> None:
        """Inject a sample manually (used by tests and mock-mode demos)."""
        self._buffer.append((float(t), float(raw)))
        self.sample_added.emit(float(t), float(raw) * self._scale)
        if self._last_summary_t is None:
            self._last_summary_t = float(t)
        elif t - self._last_summary_t >= self._summary_interval_s:
            self.summary.emit(self._all_window_stats())
            self._last_summary_t = float(t)

    def _poll(self) -> None:
        if self._stm is None:
            return
        try:
            raw = self._stm.raw.getdacvalfb()
        except Exception as e:
            log.debug("getdacvalfb() failed: %s", e)
            return
        self.add_sample(time.time(), float(raw))

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def window_stats(self, seconds: float) -> dict:
        """ΔZ statistics over the most recent ``seconds`` of samples."""
        if not self._buffer:
            return self._empty_stats(0)
        t_now = self._buffer[-1][0]
        t_cutoff = t_now - float(seconds)
        # deque is fast to iterate; filter into a window
        window = [(t, raw) for t, raw in self._buffer if t >= t_cutoff]
        if len(window) < 2:
            return self._empty_stats(len(window))
        ts = np.asarray([t for t, _ in window], dtype=float)
        zs = np.asarray([raw * self._scale for _, raw in window], dtype=float)
        # Least-squares slope, Å per second → Å per hour
        slope_per_s = float(np.polyfit(ts - ts[0], zs, 1)[0])
        return {
            "ptp_A": float(np.ptp(zs)),
            "std_A": float(np.std(zs)),
            "drift_A_per_h": slope_per_s * 3600.0,
            "n": int(len(window)),
            "span_s": float(ts[-1] - ts[0]),
        }

    def get_samples(self) -> Tuple[np.ndarray, np.ndarray]:
        """Return ``(ts, zs_angstrom)`` numpy arrays for plotting/export."""
        if not self._buffer:
            return (np.empty(0), np.empty(0))
        n = len(self._buffer)
        ts = np.empty(n, dtype=float)
        zs = np.empty(n, dtype=float)
        for i, (t, raw) in enumerate(self._buffer):
            ts[i] = t
            zs[i] = raw * self._scale
        return ts, zs

    def _all_window_stats(self) -> dict:
        return {
            "5min": self.window_stats(5 * 60),
            "1h": self.window_stats(60 * 60),
            "3h": self.window_stats(3 * 60 * 60),
        }

    @staticmethod
    def _empty_stats(n: int) -> dict:
        return {
            "ptp_A": 0.0,
            "std_A": 0.0,
            "drift_A_per_h": 0.0,
            "n": int(n),
            "span_s": 0.0,
        }


def format_summary(stats: dict) -> str:
    """One-line summary suitable for the Log panel."""
    s5 = stats.get("5min", {})
    s1 = stats.get("1h", {})
    s3 = stats.get("3h", {})
    return (
        f"Z drift: 5min Δ={s5.get('ptp_A', 0.0):.2f} Å  |  "
        f"1h Δ={s1.get('ptp_A', 0.0):.2f} Å, {s1.get('drift_A_per_h', 0.0):+.2f} Å/h  |  "
        f"3h Δ={s3.get('ptp_A', 0.0):.2f} Å, {s3.get('drift_A_per_h', 0.0):+.2f} Å/h"
    )
