"""Scan controller — image acquisition parameters and execution.

Uses SI units throughout (nm for size, nm/s for speed, volts/amperes
for bias/setpoint). All keys go through the modern setp/getp API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple

import numpy as np

if TYPE_CHECKING:
    from .stm_client import STMClient


class ScanStatus(IntEnum):
    STOPPED = 0
    # CreaTec returns 1 during pre-scan setup / approach moves / brief
    # transitional states. It's not "actively scanning" in the sense that
    # matters to our polling loop, so it gets folded in with STOPPED for
    # is_running purposes.
    PAUSED = 1
    SCANNING = 2

    @classmethod
    def _missing_(cls, value):
        """Any unrecognised status value falls back to STOPPED so the
        runner never crashes on a numeric value we haven't catalogued."""
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "Unknown SCANSTATUS %r — treating as STOPPED", value,
        )
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
        x = float(self._c.getp("SCAN.IMAGESIZE.NM.X", ""))
        y = float(self._c.getp("SCAN.IMAGESIZE.NM.Y", ""))
        return (x, y)

    @size_nm.setter
    def size_nm(self, value: tuple[float, float]) -> None:
        self._c.setp("SCAN.IMAGESIZE.NM.X", float(value[0]))
        self._c.setp("SCAN.IMAGESIZE.NM.Y", float(value[1]))

    @property
    def speed_nm_s(self) -> float:
        return float(self._c.getp("SCAN.SPEED.NM/SEC", ""))

    @speed_nm_s.setter
    def speed_nm_s(self, value: float) -> None:
        self._c.setp("SCAN.SPEED.NM/SEC", float(value))

    @property
    def pixels(self) -> tuple[int, int]:
        x = int(self._c.getp("SCAN.NUM.X", "") or 256)
        y = int(self._c.getp("SCAN.NUM.Y", "") or 256)
        return (x, y)

    @pixels.setter
    def pixels(self, value: tuple[int, int]) -> None:
        self._c.setp("SCAN.NUM.X", int(value[0]))
        self._c.setp("SCAN.NUM.Y", int(value[1]))

    @property
    def rotation_deg(self) -> float:
        return float(self._c.getp("SCAN.ROTATION.DEG", "") or 0.0)

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
        """Shift the scan offset by a sub-pixel amount (used for drift correction)."""
        self._c.raw.setxyoffpixel(dx, dy)

    def set_offset_nm(self, x_nm: float, y_nm: float) -> None:
        """Set the scan-frame XY offset in nanometres (absolute positioning).

        Routes through ``setxyoffvolt`` which truly takes piezo volts:
        empirically on this rig, ``setxyoffvolt(1, 0)`` produces an
        offset of ~9.6 nm (see calibrate_xy_from_current). We multiply
        the requested nm by the cached V/nm ratio.

        If no calibration is available yet, we try to derive one from
        the current SCAN.OFFSET.{X,Y}.NM/VOLT readings. If even that
        fails (offset near zero), we WARN loudly and pass through 1:1 —
        the user will see the resulting WARN line in the log and know to
        recalibrate.
        """
        import logging as _logging
        log = _logging.getLogger(__name__)

        if self._volts_per_nm_x is None or self._volts_per_nm_y is None:
            self.calibrate_xy_from_current()

        if self._volts_per_nm_x is None or self._volts_per_nm_y is None:
            log.warning(
                "set_offset_nm(%.3f, %.3f) without calibration — "
                "passing nm to setxyoffvolt 1:1, position will likely be wrong. "
                "Move the scan to a non-zero offset first so calibration can derive.",
                x_nm, y_nm,
            )
            self._c.raw.setxyoffvolt(float(x_nm), float(y_nm))
            return

        x_volts = float(x_nm) * self._volts_per_nm_x
        y_volts = float(y_nm) * self._volts_per_nm_y
        log.debug(
            "set_offset_nm(%.3f, %.3f) → setxyoffvolt(%.5f, %.5f) "
            "[cal: %.5f, %.5f V/nm]",
            x_nm, y_nm, x_volts, y_volts,
            self._volts_per_nm_x, self._volts_per_nm_y,
        )
        self._c.raw.setxyoffvolt(x_volts, y_volts)

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
        to derive a stable ratio.

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
        # If only one axis is available, use it for both (piezo gain is
        # usually symmetric to within a percent)
        if x_cal is None and y_cal is not None:
            x_cal = y_cal
        if y_cal is None and x_cal is not None:
            y_cal = x_cal
        if x_cal is None and y_cal is None:
            log.warning(
                "calibrate_xy_from_current: |X.NM|=%.3f, |Y.NM|=%.3f too close to "
                "zero to derive calibration. Move scan to a non-zero offset first.",
                abs(x_nm), abs(y_nm),
            )
            return None

        self._volts_per_nm_x = float(x_cal)
        self._volts_per_nm_y = float(y_cal)
        log.info(
            "XY calibration: %.5f V/nm (X), %.5f V/nm (Y)   "
            "[from offset X=%.3f nm / %.4f V, Y=%.3f nm / %.4f V]",
            self._volts_per_nm_x, self._volts_per_nm_y,
            x_nm, x_v, y_nm, y_v,
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
            bias_V=float(self._c.getp("SCAN.BIASVOLTAGE.VOLT", "")),
            setpoint_A=float(self._c.getp("SCAN.SETPOINT.AMPERE", "") or 0.0),
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
        self._c.setp("STMAFM.FILE.SAVE.DAT", str(filepath))

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
