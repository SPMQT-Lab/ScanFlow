"""Lock-in amplifier controller.

The internal lock-in modulates the bias voltage at a chosen frequency and
demodulates the resulting current signal — gives dI/dV (Lock-in X) and dI²/dV²
(Lock-in Y after a 90° phase shift) for spectroscopy and dI/dV imaging.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .stm_client import STMClient


class LockInMode(Enum):
    INTERNAL_OFF = "Internal Off"
    INTERNAL = "Internal"
    INTERNAL_SCAN_OFF = "Internal + SCAN OFF"
    INTERNAL_SPEC_ONLY = "Internal + SPEC ONLY"


class LockInController:
    def __init__(self, client: "STMClient") -> None:
        self._c = client

    # ------------------------------------------------------------------
    # Frequency / amplitude / phase
    # ------------------------------------------------------------------

    @property
    def freq_Hz(self) -> float:
        return float(self._c.getp("LOCK-IN.FREQ.HZ", ""))

    @freq_Hz.setter
    def freq_Hz(self, value: float) -> None:
        self._c.setp("LOCK-IN.FREQ.HZ", float(value))

    @property
    def amplitude_mVpp(self) -> float:
        return float(self._c.getp("LOCK-IN.AMPLITUDE.MVPP", ""))

    @amplitude_mVpp.setter
    def amplitude_mVpp(self, value: float) -> None:
        self._c.setp("LOCK-IN.AMPLITUDE.MVPP", float(value))

    @property
    def phase_deg(self) -> float:
        return float(self._c.getp("LOCK-IN.PHASE1.DEG", ""))

    @phase_deg.setter
    def phase_deg(self, value: float) -> None:
        self._c.setp("LOCK-IN.PHASE1.DEG", float(value))

    @property
    def channel(self) -> str:
        return str(self._c.getp("LOCK-IN.CHANNEL", ""))

    @channel.setter
    def channel(self, value: str) -> None:
        """e.g. 'Current(ADC0)' for normal STM dI/dV."""
        self._c.setp("LOCK-IN.CHANNEL", str(value))

    # ------------------------------------------------------------------
    # Mode
    # ------------------------------------------------------------------

    def set_mode(self, mode: LockInMode) -> None:
        self._c.setp("LOCK-IN.MODE", mode.value)

    def autophase(self, dIdV_offset_deg: float = 90.0) -> float:
        """Run autophase, then add an offset (default +90° to push dI/dV to X).

        Returns the final phase value in degrees.
        """
        self._c.setp("LOCK-IN.BTN.AUTOPHASE", "ON")
        new_phase = self.phase_deg + dIdV_offset_deg
        self.phase_deg = new_phase
        return new_phase

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def configure(
        self,
        freq_Hz: float = 652.7,
        amplitude_mVpp: float = 20.0,
        channel: str = "Current(ADC0)",
        mode: LockInMode = LockInMode.INTERNAL_SCAN_OFF,
        autophase: bool = True,
    ) -> None:
        """Apply a complete lock-in configuration in one call."""
        self.freq_Hz = freq_Hz
        self.amplitude_mVpp = amplitude_mVpp
        self.channel = channel
        self.set_mode(mode)
        if autophase:
            self.autophase()
