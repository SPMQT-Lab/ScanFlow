"""Scan controller — image acquisition parameters and execution.

Uses SI units throughout (nm for size, nm/s for speed, volts/amperes
for bias/setpoint). All keys go through the modern setp/getp API.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple

import numpy as np

if TYPE_CHECKING:
    from .stm_client import STMClient

log = logging.getLogger(__name__)


class ScanStatus(IntEnum):
    STOPPED = 0
    # CreaTec returns 1 during pre-scan setup / approach moves / brief
    # transitional states. It's not "actively scanning" in the sense that
    # matters to our polling loop, so it gets folded in with STOPPED for
    # is_running purposes.
    PAUSED = 1
    SCANNING = 2
    # CreaTec returns 3 as a brief wrap-up state between SCANNING(2) and
    # FINISHED(9) — the scan data is complete, the file write is in progress.
    # Treat as STOPPED so the wait loop exits cleanly.
    WRAPPING_UP = 3
    # CreaTec returns 9 immediately after a scan completes cleanly. Treated
    # the same as STOPPED — added explicitly so it doesn't spam the log
    # with "Unknown SCANSTATUS 9" warnings on every scan.
    FINISHED = 9

    @classmethod
    def _missing_(cls, value):
        """Any unrecognised status value falls back to STOPPED so the
        runner never crashes on a numeric value we haven't catalogued."""
        log.warning("Unknown SCANSTATUS %r — treating as STOPPED", value)
        return cls.STOPPED


class Channel:
    """Standard CreaTec channel names. Pass tuples of these to set_channels()."""
    TOPOGRAPHY = "TOPOGRAPHY"
    CURRENT = "CURRENT"
    DF = "DF"                    # Frequency shift (AFM)
    DAMPING = "DAMPING"
    AMPLITUDE = "AMPLITUDE"
    LOCKIN_X = "Lock-in X"
    LOCKIN_Y = "Lock-in Y"
    LOCKIN_R = "Lock-in R"


# DATA.SCAN channel codes (verified against CreaTec example 4_Data_Analysis.py)
class ScanDataChannel(IntEnum):
    TOPOGRAPHY_FWD = 1
    CURRENT_FWD = 2
    CURRENT_BWD = 3
    TOPOGRAPHY_BWD = 4
    DF_FWD = 7
    DAMPING_FWD = 8
    AMPLITUDE_FWD = 9


class ScanDataUnit(IntEnum):
    """Unit codes accepted by getp('DATA.SCAN', (ch, unit))."""
    NM = 4          # nanometres (topography)
    AMPERE = 3      # amperes (current)
    HZ = 5          # hertz (DF)
    VOLT = 1        # volts (raw)


@dataclass
class ScanParams:
    """Scan parameters in user-natural units (nm, nm/s, V, A)."""
    bias_V: float = 0.1                  # Volts
    setpoint_A: float = 1e-10            # Amperes
    size_nm: tuple[float, float] = (50.0, 50.0)
    speed_nm_s: float = 50.0
    pixels: tuple[int, int] = (256, 256)
    rotation_deg: float = 0.0
    channels: tuple[str, ...] = (Channel.TOPOGRAPHY, Channel.CURRENT)
    const_height: bool = False
    preamp_exponent: int = 9             # 10^9 by default
    memo: str = ""


def _safe_float(raw, default: float = 0.0) -> float:
    """Parse a getp() result that may be '', None, garbage, or NaN.

    STMAFM occasionally returns empty strings for valid keys while busy
    (saving, mid-stop); a bare ``float(...)`` here used to crash whatever
    automation step happened to read parameters at that moment. A wrong
    default is recoverable and visible in logs; a crashed overnight run
    is not.
    """
    try:
        if raw in (None, ""):
            return float(default)
        value = float(raw)
    except (TypeError, ValueError):
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "unparseable instrument value %r — using %s", raw, default)
        return float(default)
    if value != value or value in (float("inf"), float("-inf")):  # NaN/Inf
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "non-finite instrument value %r — using %s", raw, default)
        return float(default)
    return value


class ScanController:
    def __init__(self, client: "STMClient") -> None:
        self._c = client
        # Piezo XY calibration in V/nm. Derived from the current
        # SCAN.OFFSET.{X,Y}.NM vs ...VOLT readings via calibrate_xy_from_current().
        # None means uncalibrated; set_offset_nm() will attempt to derive it
        # automatically on first use.
        self._volts_per_nm_x: Optional[float] = None
        self._volts_per_nm_y: Optional[float] = None

    # ------------------------------------------------------------------
    # Parameter getters/setters
    # ------------------------------------------------------------------

    @property
    def status(self) -> ScanStatus:
        return ScanStatus(int(self._c.getp("STMAFM.SCANSTATUS", "") or 0))

    @property
    def is_running(self) -> bool:
        return self.status == ScanStatus.SCANNING

    @property
    def size_nm(self) -> tuple[float, float]:
        x = _safe_float(self._c.getp("SCAN.IMAGESIZE.NM.X", ""), 0.0)
        y = _safe_float(self._c.getp("SCAN.IMAGESIZE.NM.Y", ""), 0.0)
        return (x, y)

    @size_nm.setter
    def size_nm(self, value: tuple[float, float]) -> None:
        self._c.setp("SCAN.IMAGESIZE.NM.X", float(value[0]))
        self._c.setp("SCAN.IMAGESIZE.NM.Y", float(value[1]))

    @property
    def speed_nm_s(self) -> float:
        return _safe_float(self._c.getp("SCAN.SPEED.NM/SEC", ""), 0.0)

    @speed_nm_s.setter
    def speed_nm_s(self, value: float) -> None:
        self._c.setp("SCAN.SPEED.NM/SEC", float(value))

    @property
    def pixels(self) -> tuple[int, int]:
        # Createc stores pixel count as "Num.X" / "Num.Y" in the dat file and
        # exposes those raw keys via COM for reading. "SCAN.NUM.X" is accepted
        # for writing (apply()) but returns "" on getp in some Createc versions,
        # causing silent fallback to 256. Try the raw key first.
        def _read_px(raw_key: str, structured_key: str) -> int:
            v = self._c.getp(raw_key, "")
            if not v:
                v = self._c.getp(structured_key, "")
            try:
                return int(float(v)) if v else 256
            except (ValueError, TypeError):
                return 256
        return (_read_px("Num.X", "SCAN.NUM.X"), _read_px("Num.Y", "SCAN.NUM.Y"))

    @pixels.setter
    def pixels(self, value: tuple[int, int]) -> None:
        self._c.setp("SCAN.NUM.X", int(value[0]))
        self._c.setp("SCAN.NUM.Y", int(value[1]))

    @property
    def rotation_deg(self) -> float:
        return _safe_float(self._c.getp("SCAN.ROTATION.DEG", ""), 0.0)

    @rotation_deg.setter
    def rotation_deg(self, value: float) -> None:
        self._c.setp("SCAN.ROTATION.DEG", float(value))

    @property
    def channels(self) -> tuple[str, ...]:
        raw = self._c.getp("SCAN.CHANNELS", "")
        return tuple(raw) if raw else ()

    @channels.setter
    def channels(self, value: tuple[str, ...]) -> None:
        self._c.setp("SCAN.CHANNELS", tuple(value))

    @property
    def duration_s(self) -> float:
        """Estimated time to complete a full scan in seconds."""
        try:
            return float(self._c.getp("Sec/Image:", "") or 0)
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    # Offset / positioning
    # ------------------------------------------------------------------

    def set_offset_volts(self, x_volts: float = 0.0, y_volts: float = 0.0) -> None:
        """Set scan-frame offset in raw volts (centre of scan range = (0, 0))."""
        self._c.setp("STMAFM.CMD.SETXYOFF.VOLT", (float(x_volts), float(y_volts)))

    def set_offset_image_coord(self, x_pixel: int, y_pixel: int) -> None:
        """Move scan offset to a pixel position within the current image."""
        self._c.setp("STMAFM.CMD.SETXYOFF.IMAGECOORD", (int(x_pixel), int(y_pixel)))

    def nudge_offset_pixels(self, dx: float, dy: float) -> None:
        """Shift the scan offset by a sub-pixel amount (used by survey
        per-feature re-centring and any other small in-frame nudge)."""
        self._c.raw.setxyoffpixel(dx, dy)

    def _get_offset_volt(self) -> Optional[Tuple[float, float]]:
        """Read the current piezo XY voltage from SCAN.OFFSET.{X,Y}.VOLT."""
        try:
            x = self._c.getp("SCAN.OFFSET.X.VOLT", None)
            y = self._c.getp("SCAN.OFFSET.Y.VOLT", None)
            if x in (None, "") or y in (None, ""):
                return None
            return (float(x), float(y))
        except Exception:
            return None

    def set_offset_nm(
        self, x_nm: float, y_nm: float,
        *,
        tolerance_nm: float = 0.3,
        max_iterations: int = 5,
        settle_s: float = 0.25,
    ) -> None:
        """Set the scan-frame XY offset in nanometres (closed-loop).

        The initial voltage estimate uses the CURRENT absolute piezo
        voltage as the reference point, then adds the required nm delta
        scaled by the local calibration:

            target_V = current_V + (target_nm - current_nm) × V/nm

        This eliminates the systematic ~8-9 nm open-loop error that
        previously arose because V/nm (a local slope) was multiplied by
        the absolute nm target (which assumed the piezo origin is at 0 V,
        which it is not). With a correct first-shot estimate the
        iterations only need to correct small nonlinearity residuals and
        converge in 1-2 steps.

        Fallback if no calibration: pass nm through to setxyoffvolt 1:1.
        """
        import logging as _logging
        import time as _time
        log = _logging.getLogger(__name__)

        if self._volts_per_nm_x is None or self._volts_per_nm_y is None:
            self.calibrate_xy_from_current()

        if self._volts_per_nm_x is None or self._volts_per_nm_y is None:
            log.warning(
                "set_offset_nm(%.3f, %.3f) without calibration — "
                "passing nm to setxyoffvolt 1:1, position will likely be wrong.",
                x_nm, y_nm,
            )
            self._c.raw.setxyoffvolt(float(x_nm), float(y_nm))
            return

        target = (float(x_nm), float(y_nm))
        vx_per_nm = float(self._volts_per_nm_x)
        vy_per_nm = float(self._volts_per_nm_y)

        # Read current absolute piezo voltage to seed the first shot correctly.
        # Without this, the code would compute target_nm × V_per_nm which assumes
        # the piezo passes through (0 nm, 0 V) — it doesn't, causing a systematic
        # ~8-9 nm initial overshoot that 3 iterations cannot fully recover from.
        cur_nm = self.get_offset_nm()
        cur_v  = self._get_offset_volt()
        if cur_nm is not None and cur_v is not None:
            x_volts = cur_v[0] + (target[0] - cur_nm[0]) * vx_per_nm
            y_volts = cur_v[1] + (target[1] - cur_nm[1]) * vy_per_nm
            log.debug(
                "set_offset_nm: delta init from (%.3f, %.3f) nm / (%.4f, %.4f) V"
                " → first-shot (%.4f, %.4f) V",
                cur_nm[0], cur_nm[1], cur_v[0], cur_v[1], x_volts, y_volts,
            )
        else:
            # Fallback to absolute estimate (old behaviour) if readback fails.
            x_volts = target[0] * vx_per_nm
            y_volts = target[1] * vy_per_nm
            log.debug("set_offset_nm: using absolute fallback init (%.4f, %.4f) V",
                      x_volts, y_volts)

        err_x = err_y = 0.0
        for attempt in range(max(1, max_iterations)):
            self._c.raw.setxyoffvolt(x_volts, y_volts)
            _time.sleep(settle_s)
            actual = self.get_offset_nm()
            if actual is None:
                log.debug(
                    "set_offset_nm: readback failed at iter %d; "
                    "leaving at open-loop volts (%.5f, %.5f)",
                    attempt + 1, x_volts, y_volts,
                )
                return
            err_x = target[0] - actual[0]
            err_y = target[1] - actual[1]
            if abs(err_x) <= tolerance_nm and abs(err_y) <= tolerance_nm:
                log.info(
                    "set_offset_nm: converged in %d iter(s) "
                    "[target (%.3f, %.3f) → actual (%.3f, %.3f) nm, "
                    "residual (%+.3f, %+.3f) nm]",
                    attempt + 1, target[0], target[1],
                    actual[0], actual[1], err_x, err_y,
                )
                return
            log.debug(
                "set_offset_nm iter %d: actual (%.3f, %.3f), "
                "residual (%+.3f, %+.3f) nm → adding (%+.5f, %+.5f) V",
                attempt + 1, actual[0], actual[1], err_x, err_y,
                err_x * vx_per_nm, err_y * vy_per_nm,
            )
            x_volts += err_x * vx_per_nm
            y_volts += err_y * vy_per_nm
        log.warning(
            "set_offset_nm: max %d iterations reached — "
            "residual (%+.3f, %+.3f) nm, left at best-effort position",
            max_iterations, err_x, err_y,
        )

    def get_offset_nm(self) -> Optional[Tuple[float, float]]:
        """Read the current scan-frame XY offset in nanometres.

        Reads ``SCAN.OFFSET.X.NM`` / ``SCAN.OFFSET.Y.NM`` via getp.
        Returns None if either value can't be read.
        """
        try:
            x = self._c.getp("SCAN.OFFSET.X.NM", None)
            y = self._c.getp("SCAN.OFFSET.Y.NM", None)
            if x in (None, "") or y in (None, ""):
                return None
            return (float(x), float(y))
        except Exception:
            return None

    def calibrate_xy_from_current(self) -> Optional[Tuple[float, float]]:
        """Derive the piezo XY V/nm ratio from the current offset readings.

        Reads SCAN.OFFSET.{X,Y}.NM and SCAN.OFFSET.{X,Y}.VOLT and computes
        V/nm per axis. Caches the result on the controller for future
        set_offset_nm() calls. Returns ``(vx_per_nm, vy_per_nm)`` or
        ``None`` if the current offset is too close to zero on both axes
        to derive a stable ratio — at that point callers should invoke
        :meth:`calibrate_xy_by_delta` instead.

        Safe to call repeatedly — re-runs whenever the user explicitly
        wants to refresh the calibration after manual position changes.
        """
        import logging as _logging
        log = _logging.getLogger(__name__)
        try:
            x_nm_raw = self._c.getp("SCAN.OFFSET.X.NM", None)
            x_v_raw  = self._c.getp("SCAN.OFFSET.X.VOLT", None)
            y_nm_raw = self._c.getp("SCAN.OFFSET.Y.NM", None)
            y_v_raw  = self._c.getp("SCAN.OFFSET.Y.VOLT", None)
        except Exception as e:
            log.warning("calibrate_xy_from_current: getp failed: %s", e)
            return None

        def _to_float(v) -> float:
            try:
                if v in (None, ""):
                    return 0.0
                return float(v)
            except Exception:
                return 0.0

        x_nm, x_v = _to_float(x_nm_raw), _to_float(x_v_raw)
        y_nm, y_v = _to_float(y_nm_raw), _to_float(y_v_raw)

        # Need at least one axis with non-trivial offset to derive ratio
        x_cal = (x_v / x_nm) if abs(x_nm) > 0.05 else None
        y_cal = (y_v / y_nm) if abs(y_nm) > 0.05 else None
        if x_cal is None and y_cal is not None:
            x_cal = y_cal
        if y_cal is None and x_cal is not None:
            y_cal = x_cal
        if x_cal is None and y_cal is None:
            log.info(
                "calibrate_xy_from_current: |X.NM|=%.3f, |Y.NM|=%.3f near "
                "zero — falling back to delta calibration",
                abs(x_nm), abs(y_nm),
            )
            return None

        self._volts_per_nm_x = float(x_cal)
        self._volts_per_nm_y = float(y_cal)
        log.info(
            "XY calibration (from current): %.5f V/nm (X), %.5f V/nm (Y)   "
            "[from offset X=%.3f nm / %.4f V, Y=%.3f nm / %.4f V]",
            self._volts_per_nm_x, self._volts_per_nm_y,
            x_nm, x_v, y_nm, y_v,
        )
        return (self._volts_per_nm_x, self._volts_per_nm_y)

    def calibrate_xy_by_delta(
        self, probe_volts: float = 0.1, settle_s: float = 0.3,
    ) -> Optional[Tuple[float, float]]:
        """Derive V/nm by making a tiny known move and reading the delta.

        Works regardless of starting position — including XY = (0, 0).
        Uses the *change* in SCAN.OFFSET.X.NM after a ``probe_volts``
        move on setxyoffvolt, so the absolute origin is irrelevant.

        Safety:
          * ``probe_volts = 0.1`` corresponds to roughly 1 nm of tip
            displacement on a typical Createc piezo (0.1 V/nm), well
            within any sane scan range.
          * After deriving the ratio, this method *restores the original
            offset* so the tip is back where it started.
          * Pure read-write-read-write sequence — no scan, no Z move.

        Returns ``(vx_per_nm, vy_per_nm)`` on success, ``None`` if the
        piezo didn't respond (delta too small to be trusted).
        """
        import logging as _logging
        import time as _time
        log = _logging.getLogger(__name__)

        def _read_nm() -> Optional[Tuple[float, float]]:
            try:
                x = self._c.getp("SCAN.OFFSET.X.NM", None)
                y = self._c.getp("SCAN.OFFSET.Y.NM", None)
                if x in (None, "") or y in (None, ""):
                    return None
                return (float(x), float(y))
            except Exception:
                return None

        def _read_volt() -> Optional[Tuple[float, float]]:
            try:
                x = self._c.getp("SCAN.OFFSET.X.VOLT", None)
                y = self._c.getp("SCAN.OFFSET.Y.VOLT", None)
                if x in (None, "") or y in (None, ""):
                    return None
                return (float(x), float(y))
            except Exception:
                return None

        start_nm = _read_nm()
        start_v = _read_volt()
        if start_nm is None or start_v is None:
            log.warning("calibrate_xy_by_delta: could not read start position")
            return None

        # Probe move: shift by +probe_volts on each axis
        target_v = (start_v[0] + probe_volts, start_v[1] + probe_volts)
        log.info(
            "calibrate_xy_by_delta: probing with setxyoffvolt(%+.4f, %+.4f) V "
            "(from start V=%.4f, %.4f)",
            target_v[0], target_v[1], start_v[0], start_v[1],
        )
        try:
            self._c.raw.setxyoffvolt(target_v[0], target_v[1])
        except Exception as e:
            log.warning("calibrate_xy_by_delta: probe setxyoffvolt failed: %s", e)
            return None
        _time.sleep(settle_s)

        after_nm = _read_nm()
        if after_nm is None:
            log.warning("calibrate_xy_by_delta: could not read after-probe position")
            # Try to restore anyway
            try:
                self._c.raw.setxyoffvolt(start_v[0], start_v[1])
            except Exception:
                pass
            return None

        delta_nm = (after_nm[0] - start_nm[0], after_nm[1] - start_nm[1])

        # Restore original position immediately
        try:
            self._c.raw.setxyoffvolt(start_v[0], start_v[1])
            _time.sleep(settle_s)
        except Exception as e:
            log.warning("calibrate_xy_by_delta: restore failed: %s", e)

        # Sanity: piezo should have responded by at least 0.1 nm
        if abs(delta_nm[0]) < 0.1 or abs(delta_nm[1]) < 0.1:
            log.warning(
                "calibrate_xy_by_delta: piezo barely responded — "
                "delta (%.3f, %.3f) nm too small to trust",
                delta_nm[0], delta_nm[1],
            )
            return None

        # V/nm = probe_volts / observed_nm_delta
        self._volts_per_nm_x = float(probe_volts / delta_nm[0])
        self._volts_per_nm_y = float(probe_volts / delta_nm[1])
        log.info(
            "XY calibration (by delta): %.5f V/nm (X), %.5f V/nm (Y)   "
            "[%.4f V move → (%.3f, %.3f) nm]",
            self._volts_per_nm_x, self._volts_per_nm_y,
            probe_volts, delta_nm[0], delta_nm[1],
        )
        return (self._volts_per_nm_x, self._volts_per_nm_y)

    # ------------------------------------------------------------------
    # Recipe application
    # ------------------------------------------------------------------

    def apply(self, params: ScanParams) -> None:
        """Apply a complete ScanParams object to the instrument.

        Pixels and physical size use only the per-axis ``.X``/``.Y`` keys.
        The tuple-form ``SCAN.IMAGESIZE.PIXEL`` / ``SCAN.IMAGESIZE.NM`` keys
        collapse the X side to 2 on the real STMAFM software, so we don't
        send them. No readback or extra getp calls here — keep this hot
        path minimal so the COM bus isn't hammered during long campaigns.
        """
        c = self._c
        c.setp("SCAN.PREAMPGAIN.EXPONENT", int(params.preamp_exponent))
        c.setp("SCAN.BIASVOLTAGE.VOLT", float(params.bias_V))
        c.setp("SCAN.SETPOINT.AMPERE", float(params.setpoint_A))

        # Pixels — per-axis only (tuple form corrupts X)
        c.setp("SCAN.NUM.X", int(params.pixels[0]))
        c.setp("SCAN.NUM.Y", int(params.pixels[1]))
        # Physical size — per-axis only
        c.setp("SCAN.IMAGESIZE.NM.X", float(params.size_nm[0]))
        c.setp("SCAN.IMAGESIZE.NM.Y", float(params.size_nm[1]))

        c.setp("SCAN.SPEED.NM/SEC", float(params.speed_nm_s))
        c.setp("SCAN.ROTATION.DEG", float(params.rotation_deg))
        c.setp("SCAN.CHANNELS", tuple(params.channels))
        c.setp("CHMode", int(params.const_height))
        if params.memo:
            c.setp("MEMO_STMAFM", str(params.memo))

    def read(self) -> ScanParams:
        """Read the current scan parameters from the instrument."""
        return ScanParams(
            bias_V=_safe_float(self._c.getp("SCAN.BIASVOLTAGE.VOLT", ""), 0.0),
            setpoint_A=_safe_float(self._c.getp("SCAN.SETPOINT.AMPERE", ""), 0.0),
            size_nm=self.size_nm,
            speed_nm_s=self.speed_nm_s,
            pixels=self.pixels,
            rotation_deg=self.rotation_deg,
            channels=self.channels,
            preamp_exponent=int(self._c.getp("SCAN.PREAMPGAIN.EXPONENT", "") or 9),
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start a scan (non-blocking)."""
        self._c.setp("STMAFM.BTN.START", "")

    def stop(self) -> None:
        """Stop a running scan."""
        self._c.setp("STMAFM.BTN.STOP", "")

    def wait_until_done(self, poll_interval_s: float = 1.0,
                        timeout_s: Optional[float] = None) -> bool:
        """Block until the scan finishes. Returns True on success, False on timeout."""
        start = time.time()
        # Allow up to 2 polling cycles for the status to flip to SCANNING
        for _ in range(3):
            if self.is_running:
                break
            time.sleep(poll_interval_s)
        while self.is_running:
            if timeout_s is not None and (time.time() - start) > timeout_s:
                return False
            time.sleep(poll_interval_s)
        return True

    def save_dat(self, filepath: str) -> None:
        """Save the most recent scan as a .dat file at the given path."""
        try:
            self._c.setp("STMAFM.FILE.SAVE.DAT", str(filepath))
        except Exception as exc:
            # Createc generates a .dat.jpeg thumbnail after saving the .dat.
            # On Windows, file indexers (Explorer, antivirus) occasionally hold
            # an exclusive lock on that thumbnail file, causing a COM error even
            # though the .dat itself was written successfully.  Treat this as a
            # non-fatal warning so the sweep can continue.
            msg = str(exc)
            if ".jpeg" in msg.lower() and (
                "cannot create" in msg.lower() or "being used by another" in msg.lower()
            ):
                log.warning(
                    "save_dat: .dat.jpeg thumbnail locked by another process — "
                    ".dat was saved, thumbnail skipped. (%s)", exc
                )
            else:
                raise

    def last_saved_path(self) -> Optional[Path]:
        path = self._c.getp("STMAFM.LASTSAVEDFILE", "") or self._c.raw.savedatfilename
        return Path(str(path)) if path else None

    def scan_and_save(self, filepath: Optional[str] = None,
                      timeout_s: Optional[float] = None) -> Optional[Path]:
        """Start a scan, wait, save, return the saved path."""
        self.start()
        if not self.wait_until_done(timeout_s=timeout_s):
            return None
        target = filepath or str(self._c.raw.savedatfilename)
        if target:
            self.save_dat(target)
        return Path(target) if target else None

    # ------------------------------------------------------------------
    # Live data access (no disk round-trip — works during a scan)
    # ------------------------------------------------------------------

    def live_data(
        self,
        channel: int = ScanDataChannel.TOPOGRAPHY_FWD,
        unit: int = ScanDataUnit.NM,
    ) -> Optional[np.ndarray]:
        """Pull the most recent scan data for a channel directly from the DSP.

        Returns a 2-D numpy array (rows × cols) in physical units, or None
        if no data is available yet. Works whether the scan is finished or
        still in progress — partial rows show as zeros at the bottom.

        Channel codes: see ``ScanDataChannel`` (1=Topo fwd, 2=I fwd, …).
        Unit codes:    see ``ScanDataUnit`` (4=nm, 3=A, 5=Hz, 1=V).
        """
        try:
            raw = self._c.getp("DATA.SCAN", (int(channel), int(unit)))
        except Exception:
            return None
        if raw is None or len(raw) == 0:
            return None
        arr = np.asarray(raw, dtype=float)
        nx, ny = self.pixels
        if arr.size == nx * ny:
            return arr.reshape(ny, nx)
        return arr  # unexpected length — return flat for caller to inspect

    def live_bitmap(self) -> Optional[bytes]:
        """Pre-rendered RGB bitmap of the current scan from the DSP."""
        try:
            return self._c.raw.scandatabitmap()
        except Exception:
            return None


# ------------------------------------------------------------------
# Scan timing helpers (used by GUI panels and automation executors)
# ------------------------------------------------------------------

def estimate_scan_duration_s(params: "ScanParams") -> float:
    """Best-estimate scan duration in seconds (forward + reverse, no settle padding)."""
    line_time = 2.0 * float(params.size_nm[0]) / max(float(params.speed_nm_s), 0.01)
    return line_time * int(params.pixels[1])


def estimate_scan_timeout_s(params: "ScanParams") -> float:
    """Scan duration estimate plus generous safety margin, floored at 2 min."""
    return max(120.0, estimate_scan_duration_s(params) + 90.0)


def speed_for_target_duration_s(
    size_nm: float,
    pixels_y: int,
    target_s: float,
    *,
    min_speed_nm_s: float = 0.5,
    max_speed_nm_s: float = 1000.0,
) -> float:
    """Scan speed (nm/s) that makes one full image take ``target_s`` seconds.

    Inverts :func:`estimate_scan_duration_s` (duration = 2·size·pixels_y /
    speed). Used to keep small feature scans at a fixed wall-clock time so
    per-image drift stays bounded, instead of inheriting the wide scan's
    fast speed and finishing in seconds. Result is clamped to a sane range.
    """
    target_s = max(1.0, float(target_s))
    speed = 2.0 * float(size_nm) * int(pixels_y) / target_s
    return max(min_speed_nm_s, min(max_speed_nm_s, speed))


def format_duration(seconds: float) -> str:
    """Human-readable mm:ss formatting (or 'Ns' under one minute)."""
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s:02d}s"
