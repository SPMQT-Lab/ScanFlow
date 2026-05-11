"""Mock CreaTec COM dispatch — drives ScanFlow without real hardware.

Stands in for the ``pstmafm.stmafmrem`` COM object: stores parameters in a
plain dict, simulates scan/approach state machines on real wall-clock
time, and generates synthetic scan data (atomic lattice + drift + noise)
so panels like the Live View show realistic-looking output.

Used by:
    • offline development on Linux / Mac / Windows without STMAFM
    • automated tests of every panel without an instrument
    • the GUI's "Connect Mock STM" toolbar action
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Synthetic-image model
# ---------------------------------------------------------------------------

@dataclass
class MockSurface:
    """Parameters of the synthetic sample the mock pretends to scan."""
    lattice_period_nm: float = 0.4        # cubic lattice spacing
    lattice_amplitude_nm: float = 0.02
    drift_rate_nm_s: Tuple[float, float] = (0.0005, -0.0003)
    noise_amplitude_nm: float = 0.003
    atoms_random_seed: int = 42

    def topography(self, nx: int, ny: int,
                   size_nm: Tuple[float, float],
                   elapsed_s: float) -> np.ndarray:
        """Render an (ny, nx) topography array in nm."""
        rng = np.random.default_rng(self.atoms_random_seed)
        sx, sy = size_nm
        x = np.linspace(0, sx, nx) + self.drift_rate_nm_s[0] * elapsed_s
        y = np.linspace(0, sy, ny) + self.drift_rate_nm_s[1] * elapsed_s
        X, Y = np.meshgrid(x, y)
        k = 2 * math.pi / max(self.lattice_period_nm, 1e-6)
        lattice = self.lattice_amplitude_nm * np.cos(k * X) * np.cos(k * Y)
        # Sprinkle a few gaussian "adsorbates"
        adsorbates = np.zeros_like(lattice)
        for _ in range(6):
            cx, cy = rng.uniform(0, sx), rng.uniform(0, sy)
            sigma = rng.uniform(0.3, 1.2)
            amp = rng.uniform(0.02, 0.08)
            adsorbates += amp * np.exp(
                -((X - cx) ** 2 + (Y - cy) ** 2) / (2 * sigma ** 2)
            )
        noise = self.noise_amplitude_nm * rng.standard_normal(lattice.shape)
        return lattice + adsorbates + noise


# ---------------------------------------------------------------------------
# Mock dispatch
# ---------------------------------------------------------------------------

# Default-valued keys (mirrors the manufacturer's typical defaults)
_DEFAULTS: Dict[str, Any] = {
    "STMAFM.SCANSTATUS": 0,
    "SCAN.BIASVOLTAGE.VOLT": 0.1,
    "SCAN.SETPOINT.AMPERE": 1e-10,
    "SCAN.PREAMPGAIN.EXPONENT": 9,
    "SCAN.SPEED.NM/SEC": 50.0,
    "SCAN.IMAGESIZE.NM": (50.0, 50.0),
    "SCAN.IMAGESIZE.NM.X": 50.0,
    "SCAN.IMAGESIZE.NM.Y": 50.0,
    "SCAN.NUM.X": 256,
    "SCAN.NUM.Y": 256,
    "SCAN.ROTATION.DEG": 0.0,
    "SCAN.CHANNELS": ("TOPOGRAPHY", "CURRENT"),
    "Sec/Image:": 30.0,
    "HVAMPCOARSE.APPROACH.FINISHED": 1,
    "HVAMPCOARSE.APPROACH.BURSTCOUNT": 1,
    "HVAMPCOARSE.APPROACH.RETRYCOUNT": 1,
    "HVAMPCOARSE.APPROACH.PERIOD.SEC": 1.5,
    "HVAMPCOARSE.PULSEHEIGHT.VOLT": 60.0,
    "HVAMPCOARSE.PULSEDURATION.SEC": 0.003,
    "SLIDER.ZLIMIT.ON": "OFF",
    "SLIDER.ZLIMIT.RETRACT.NM": 10.0,
    "SLIDER.ZLIMIT.VOLT": -10.0,
    "LOCK-IN.FREQ.HZ": 652.7,
    "LOCK-IN.AMPLITUDE.MVPP": 20.0,
    "LOCK-IN.CHANNEL": "Current(ADC0)",
    "LOCK-IN.MODE": "Internal + SCAN OFF",
    "LOCK-IN.PHASE1.DEG": 0.0,
    "VERTMAN.PREAMPGAIN.EXPONENT": 9,
    "VERTMAN.SPECLENGTH.SEC": 10.0,
    "VERTMAN.REPEATCOUNT": 1,
    "VERTMAN.SPECAVRG.COUNT": 1,
    "VERTMAN.LATSPEED.NM/SEC": 1.0,
    "VERTMAN.SPEC.BACK": 1,
    "VERTMAN.CHANNELs": ("Current(filtered)", "Lock-in X", "Lock-in Y"),
    "AFM.PLL_EXCITATION.VOLT": 0.05,
    "AFM.SRS_GAIN": 100.0,
    "AFM.PLL_AMPLITUDE.NM": 0.2,
    "AFM.RESULTS.FCENTER.HZ": 24500.0,
    "T_ADC2[K]": 4.5,
    "T_ADC3[K]": 77.0,
    "T-STM:": 4.5,
    "Block_DSTime_Change": False,
}


class MockDispatch:
    """Plain-Python stand-in for ``pstmafm.stmafmrem``."""

    def __init__(self, surface: Optional[MockSurface] = None) -> None:
        self._params: Dict[str, Any] = dict(_DEFAULTS)
        self._surface = surface or MockSurface()
        self._scan_start: Optional[float] = None
        self._approach_start: Optional[float] = None
        self._approach_duration_s = 2.5
        self._freq_scan_start: Optional[float] = None
        self._freq_scan_duration_s = 0.0
        self._epoch = time.time()
        self._lock = threading.RLock()
        self.savedatfilename = "/tmp/mock_scan.dat"
        self.savevertfilename = "/tmp/mock_spec.VERT"
        # Test hook: when non-None, getadcvalf returns this voltage so safety
        # tests can simulate a tip crash without modifying the scan data.
        self.mock_current_override_V: Optional[float] = None

    # Test helpers -----------------------------------------------------

    def simulate_tip_crash(self, current_nA: float = 5.0) -> None:
        """Make the next current readings indicate the given crash level."""
        preamp_exp = int(self._params.get("SCAN.PREAMPGAIN.EXPONENT", 9))
        # current_A = V / 10^exp  → V = current_A * 10^exp
        self.mock_current_override_V = (current_nA * 1e-9) * (10 ** preamp_exp)

    def clear_tip_crash(self) -> None:
        self.mock_current_override_V = None

    def getadcvalf(self, board: int, channel: int) -> float:
        """ADC0 channel0 = current preamp output (volts).

        Tests override this via ``mock_current_override_V`` to simulate
        a tip crash.
        """
        if self.mock_current_override_V is not None:
            return float(self.mock_current_override_V)
        # Idle current ~ setpoint, expressed at preamp output
        setpoint_A = float(self._params.get("SCAN.SETPOINT.AMPERE", 1e-10))
        preamp_exp = int(self._params.get("SCAN.PREAMPGAIN.EXPONENT", 9))
        return setpoint_A * (10 ** preamp_exp) * (1.0 + 0.01 * (time.time() % 1))

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def setp(self, key: str, value: Any) -> None:
        with self._lock:
            self._handle_command(key, value)

    def getp(self, key: str, default: Any = "") -> Any:
        with self._lock:
            return self._read_key(key, default)

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def _handle_command(self, key: str, value: Any) -> None:
        # Commands (write-only side-effects)
        if key == "STMAFM.BTN.START":
            self._scan_start = time.time()
            self._params["STMAFM.SCANSTATUS"] = 2
            return
        if key == "STMAFM.BTN.STOP":
            self._scan_start = None
            self._params["STMAFM.SCANSTATUS"] = 0
            return
        if key == "STMAFM.BEEP":
            return
        if key == "HVAMPCOARSE.APPROACH.START":
            self._approach_start = time.time()
            self._params["HVAMPCOARSE.APPROACH.FINISHED"] = 0
            return
        if key == "AFM.BTN.FREQSCAN":
            self._freq_scan_start = time.time()
            self._freq_scan_duration_s = float(
                self._params.get("AFM.FREQSCAN.DURATION", 5.0)
            )
            return
        if key == "AFM.BTN.FREQSCAN.RESULTS.APPLY":
            return
        if key == "AFM.FREQSCAN":
            # (center_Hz, span_Hz, duration_s, points)
            try:
                self._params["AFM.RESULTS.FCENTER.HZ"] = float(value[0])
                self._freq_scan_duration_s = float(value[2])
            except (TypeError, IndexError):
                pass
            self._params[key] = value
            return
        if key == "STMAFM.FILE.SAVE.DAT":
            self.savedatfilename = str(value)
            self._params["STMAFM.LASTSAVEDFILE"] = str(value)
            return
        if key == "STMAFM.FILE.SAVE.VERT":
            self.savevertfilename = str(value)
            return
        if key == "STMAFM.FILE.LOAD.DAT":
            return
        if key == "TIP-FORM.CMD.START":
            return
        if key in ("VERTMAN.CMD.SINGLE_SPECTRUM", "VERTMAN.CMD.GRID"):
            return
        if key == "STMAFM.CMD.SETXYOFF.VOLT" or key == "STMAFM.CMD.SETXYOFF.IMAGECOORD":
            self._params[key] = value
            return
        if key in ("UPDATE.AUTOUPDATE.ON",):
            self._params[key] = value
            return
        if key == "SCAN.IMAGESIZE.PIXEL":
            try:
                self._params["SCAN.NUM.X"] = int(value[0])
                self._params["SCAN.NUM.Y"] = int(value[1])
            except (TypeError, IndexError):
                pass
            return
        if key == "SCAN.IMAGESIZE.NM":
            try:
                self._params["SCAN.IMAGESIZE.NM.X"] = float(value[0])
                self._params["SCAN.IMAGESIZE.NM.Y"] = float(value[1])
            except (TypeError, IndexError):
                pass
            self._params[key] = value
            return
        # Default: store
        self._params[key] = value

    # ------------------------------------------------------------------
    # Key reads
    # ------------------------------------------------------------------

    def _read_key(self, key: str, default: Any) -> Any:
        # Live status: scan
        if key == "STMAFM.SCANSTATUS":
            return 2 if self._is_scanning() else 0
        # Live status: approach
        if key == "HVAMPCOARSE.APPROACH.FINISHED":
            if self._approach_start is None:
                return self._params.get(key, 1)
            elapsed = time.time() - self._approach_start
            if elapsed >= self._approach_duration_s:
                self._approach_start = None
                self._params[key] = 1
                return 1
            return 0
        # Frequency-scan completion piggybacks on SCANSTATUS in CreaTec; we
        # use the same convention.
        # Live scan data
        if key == "DATA.SCAN":
            return self._gen_scan_data(default)
        if key == "DATA.TIMESPEC":
            return self._gen_timespec(default)
        if key == "DATA.VERTMAN":
            return self._gen_vertman(default)
        # Default lookup
        return self._params.get(key, default)

    # ------------------------------------------------------------------
    # Synthetic data
    # ------------------------------------------------------------------

    def _is_scanning(self) -> bool:
        if self._scan_start is None:
            return False
        duration = self._scan_duration_s()
        if time.time() - self._scan_start >= duration:
            self._scan_start = None
            return False
        return True

    def _scan_duration_s(self) -> float:
        nx = int(self._params.get("SCAN.NUM.X", 256))
        ny = int(self._params.get("SCAN.NUM.Y", 256))
        speed = float(self._params.get("SCAN.SPEED.NM/SEC", 50.0))
        sx = float(self._params.get("SCAN.IMAGESIZE.NM.X", 50.0))
        # Line time = sx / speed, total = ny * line_time * 2 (fwd + bwd traces)
        line_time = sx / max(speed, 1.0)
        return max(2.0, ny * line_time * 0.05)  # speed it up for testing

    def _gen_scan_data(self, default: Any) -> Tuple[float, ...]:
        """Return a flat tuple matching the current scan dimensions."""
        try:
            channel, unit = default  # default is (channel, unit) tuple from getp
        except (TypeError, ValueError):
            channel, unit = 1, 4
        nx = int(self._params.get("SCAN.NUM.X", 256))
        ny = int(self._params.get("SCAN.NUM.Y", 256))
        size_nm = (
            float(self._params.get("SCAN.IMAGESIZE.NM.X", 50.0)),
            float(self._params.get("SCAN.IMAGESIZE.NM.Y", 50.0)),
        )
        elapsed = time.time() - self._epoch
        topo = self._surface.topography(nx, ny, size_nm, elapsed)
        # If still scanning, blank the rows below the moving frontline
        if self._scan_start is not None:
            duration = self._scan_duration_s()
            progress = min(1.0, (time.time() - self._scan_start) / max(duration, 0.1))
            frontline = int(progress * ny)
            topo[frontline:, :] = 0.0
        # Channel 1=topo fwd (nm), 2=current fwd (A), 3=current bwd, 4=topo bwd
        if int(channel) in (1, 4):
            arr = topo
        elif int(channel) in (2, 3):
            # Mock current: derivative of topo plus a small DC offset
            arr = 1e-10 + 1e-11 * np.gradient(topo, axis=1)
        elif int(channel) == 7:
            arr = -2.0 + 0.5 * np.gradient(topo, axis=0)  # df Hz
        else:
            arr = topo
        return tuple(arr.flatten().tolist())

    def _gen_timespec(self, default: Any) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
        n = 500
        t = np.linspace(0, 10.0, n)
        rng = np.random.default_rng(7)
        signal = 1e-10 * np.sin(2 * math.pi * 2.0 * t) + 5e-12 * rng.standard_normal(n)
        return (tuple(t.tolist()), tuple(signal.tolist()))

    def _gen_vertman(self, default: Any) -> Tuple[float, ...]:
        n = 1024
        v = np.linspace(-0.7, 0.7, n)
        rng = np.random.default_rng(11)
        if isinstance(default, tuple) and len(default) >= 1:
            ch = int(default[0])
        else:
            ch = 1
        if ch == 0:        # time
            data = np.linspace(0, 10, n)
        elif ch == 1:      # bias
            data = v
        elif ch == 3:      # current
            data = 1e-10 * np.sinh(v / 0.05) + 1e-12 * rng.standard_normal(n)
        elif ch == 4:      # lock-in X (dI/dV)
            data = 1e-9 * np.exp(-(v ** 2) / 0.05) + 1e-11 * rng.standard_normal(n)
        elif ch == 13:     # lock-in Y
            data = 1e-11 * rng.standard_normal(n)
        else:
            data = np.zeros(n)
        return tuple(data.tolist())

    # ------------------------------------------------------------------
    # Stubs for COM passthrough used by some controllers
    # ------------------------------------------------------------------

    def btn_vertspec_mult(self, posxy: Any) -> None:
        pass

    def btn_vertspec_line(self, x1: int, y1: int, x2: int, y2: int) -> None:
        pass

    def btn_spectraongrid(self) -> None:
        pass

    def btn_timespec(self) -> None:
        pass

    def latmanip(self, x1: int, y1: int, x2: int, y2: int) -> None:
        pass

    def latmanipxymove(self, xs: float, ys: float, xe: float, ye: float) -> None:
        pass

    def latsave(self) -> None:
        pass

    def timespecsave(self) -> None:
        pass

    def setxyoffpixel(self, x: float, y: float) -> None:
        pass

    def scandatabitmap(self) -> Optional[bytes]:
        return None

    def scanwaitfinished(self) -> None:
        # Block-poll style
        while self._is_scanning():
            time.sleep(0.05)


# ---------------------------------------------------------------------------
# Mock user dispatch (the secondary pstmafm.stmafmuser COM object)
# ---------------------------------------------------------------------------

class MockUserDispatch:
    """Mirror of ``pstmafm.stmafmuser`` — exposes crosscorr / getxypos."""

    def crosscorr(self) -> Tuple[float, float]:
        return (0.0, 0.0)

    def getxypos(self) -> Tuple[float, float]:
        return (0.0, 0.0)
