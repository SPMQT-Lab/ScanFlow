"""Runtime safety monitor — guards against tip crashes during automation.

The single most reliable indicator of a tip crash on an STM is the
tunneling current itself: in normal operation it sits at the setpoint
(typically 10–100 pA), and any approach to physical contact spikes it
several orders of magnitude up. By watching the live current and
aborting before the scan continues into damaged territory, we protect
the tip without relying on any user reaction time.

Strategy:

    every poll:
        I = measure_current()      # in amperes
        if |I| > threshold:        # default 1 nA
            stop the scan
            activate Z-limit (retract)
            raise SafetyViolation

Default threshold 1 nA is the user-recommended ground-truth crash
indicator on this rig. Adjust per recipe via ``max_current_A``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .stm_client import STMClient

log = logging.getLogger(__name__)


@dataclass
class SafetyConfig:
    """Configurable safety thresholds."""
    max_current_A: float = 1e-9         # 1 nA — tip-crash indicator
    enable_current_check: bool = True
    retract_on_violation_nm: float = 10.0  # Z-limit retract distance
    # Consecutive failed current readings tolerated before the monitor
    # fails CLOSED. A run whose readback is broken cannot be protected,
    # so it must not be allowed to continue blindly.
    warn_read_failures: int = 5
    max_read_failures: int = 20
    # Tunneling-loss detection. If |I| stays below ``min_tunnel_current_A``
    # for ``tunnel_lost_grace_s`` seconds, the tip is no longer in tunneling
    # range (it lost the surface) and the scan is producing nothing — report
    # not-ok so the runner can halt. None disables the check. Unlike a crash
    # this does NOT retract: the tip is already away from the surface.
    min_tunnel_current_A: Optional[float] = None
    tunnel_lost_grace_s: float = 8.0


@dataclass
class SafetyStatus:
    ok: bool
    reason: str = ""
    measured_current_A: Optional[float] = None
    # True when this check could not obtain a current reading at all.
    read_failed: bool = False
    # "ok" | "crash" | "tunnel_lost" — lets the runner react differently
    # (crash retracts the tip; tunnel_lost just stops the scan).
    kind: str = "ok"


class SafetyViolation(RuntimeError):
    """Raised by the automation runner when a safety threshold is hit."""

    def __init__(self, message: str, current_A: Optional[float] = None) -> None:
        super().__init__(message)
        self.current_A = current_A


class SafetyMonitor:
    """Reads live current and reports threshold violations."""

    # The live-scan fallback read pulls a FULL current frame over COM —
    # expensive and executed on STMAFM's GUI thread. When the ADC path is
    # broken and every poll hits the fallback, cache its result briefly so
    # a 0.5 s safety poll doesn't become a 2 Hz full-frame transfer. The
    # frame only fills in as the scan progresses, so a reading this stale
    # still reflects the same data the fallback would re-derive.
    FALLBACK_CACHE_S = 1.5

    def __init__(self, config: Optional[SafetyConfig] = None) -> None:
        self.config = config or SafetyConfig()
        self._consecutive_read_failures = 0
        self._fallback_cached_at = 0.0
        self._fallback_cached_value: Optional[float] = None
        self._low_current_since: Optional[float] = None

    @property
    def consecutive_read_failures(self) -> int:
        return self._consecutive_read_failures

    def reset_tunnel_timer(self) -> None:
        """Clear the sustained-low-current timer (call when a scan starts)."""
        self._low_current_since = None

    # ------------------------------------------------------------------
    # Current measurement
    # ------------------------------------------------------------------

    def measure_current_A(self, stm: "STMClient") -> Optional[float]:
        """Read the present tunneling current in amperes.

        Strategy:
          1. Instantaneous ADC reading (cheap, ~µs) — getadcvalf(0, 0).
             Returns the preamp output voltage; divide by 10**preamp_exp.
          2. Fallback: peek at the latest live scan-data current channel
             and return the peak |I| in the partial frame.
        """
        # Method 1: instantaneous voltage from ADC0 / preamp.
        # A NaN/Inf ADC reading must NOT be returned: NaN passes every
        # `abs(I) > threshold` comparison as False, which would silently
        # disable tip-crash protection while looking like a good reading.
        try:
            v = float(stm.raw.getadcvalf(0, 0))
            preamp_exp = int(stm.getp("SCAN.PREAMPGAIN.EXPONENT", 9) or 9)
            current = v / (10.0 ** preamp_exp)
            if np.isfinite(current):
                return current
            log.debug("ADC current reading non-finite (%r) — falling back", v)
        except Exception:
            pass
        # Method 2: max from live scan data — full-frame COM transfer, so
        # serve repeat calls from a short-lived cache (see FALLBACK_CACHE_S).
        import time as _time
        now = _time.monotonic()
        if now - self._fallback_cached_at < self.FALLBACK_CACHE_S:
            return self._fallback_cached_value
        value: Optional[float] = None
        try:
            arr = stm.scan.live_data(channel=2, unit=3)  # CURRENT_FWD, AMPERE
            if arr is not None:
                arr = np.asarray(arr, dtype=float)
                finite = arr[np.isfinite(arr)]
                # NaN rows happen on real rigs (partial frames, DSP hiccups);
                # ignore them rather than letting one NaN poison the max.
                if finite.size:
                    value = float(np.max(np.abs(finite)))
        except Exception:
            value = None
        if value is not None and not np.isfinite(value):
            value = None
        self._fallback_cached_at = now
        self._fallback_cached_value = value
        return value

    # ------------------------------------------------------------------
    # Check
    # ------------------------------------------------------------------

    def check(self, stm: "STMClient") -> SafetyStatus:
        cfg = self.config
        if not cfg.enable_current_check:
            return SafetyStatus(ok=True)
        I = self.measure_current_A(stm)
        # Belt and braces: a non-finite reading is a FAILED reading. It
        # must count toward the fail-closed escalation, never reset it.
        if I is not None and not np.isfinite(I):
            I = None
        if I is None:
            # Fail CLOSED on persistent readback failure: a monitor that
            # cannot read the current cannot protect the tip, so it must
            # not keep reporting "ok" forever.
            self._consecutive_read_failures += 1
            n = self._consecutive_read_failures
            if n >= cfg.max_read_failures:
                return SafetyStatus(
                    ok=False,
                    reason=(
                        f"current readback unavailable for {n} consecutive "
                        "checks — tip-crash protection cannot be guaranteed"
                    ),
                    read_failed=True,
                )
            return SafetyStatus(
                ok=True,
                reason=f"current readback failed ({n} consecutive)",
                read_failed=True,
            )
        self._consecutive_read_failures = 0
        if abs(I) > cfg.max_current_A:
            return SafetyStatus(
                ok=False,
                kind="crash",
                reason=(
                    f"Tip-crash threshold exceeded: "
                    f"|I| = {I*1e9:.3f} nA > {cfg.max_current_A*1e9:.3f} nA"
                ),
                measured_current_A=I,
            )
        # Tunneling-loss: sustained near-zero current means the tip lost the
        # surface and the scan is acquiring nothing.
        floor = cfg.min_tunnel_current_A
        if floor is not None and floor > 0:
            import time as _time
            now = _time.monotonic()
            if abs(I) < floor:
                if self._low_current_since is None:
                    self._low_current_since = now
                elif now - self._low_current_since >= cfg.tunnel_lost_grace_s:
                    return SafetyStatus(
                        ok=False,
                        kind="tunnel_lost",
                        reason=(
                            f"Tunneling lost: |I| = {abs(I)*1e12:.1f} pA below "
                            f"{floor*1e12:.1f} pA for ≥{cfg.tunnel_lost_grace_s:.0f}s "
                            "— tip no longer in tunneling range"
                        ),
                        measured_current_A=I,
                    )
            else:
                self._low_current_since = None
        return SafetyStatus(ok=True, measured_current_A=I)

    # ------------------------------------------------------------------
    # Reaction
    # ------------------------------------------------------------------

    def emergency_stop(self, stm: "STMClient") -> None:
        """Stop scan and retract tip via Z-limit. Safe to call repeatedly."""
        log.warning("Safety emergency_stop fired — halting scan and retracting tip")
        try:
            stm.scan.stop()
        except Exception:
            log.exception("scan.stop() failed during emergency_stop")
        try:
            stm.coarse.z_limit_on(self.config.retract_on_violation_nm)
        except Exception:
            log.exception("z_limit_on() failed during emergency_stop")
        # Best-effort beep
        try:
            stm.beep()
        except Exception:
            pass
