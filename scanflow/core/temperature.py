"""Temperature monitor — read all temperature sensors on the cryostat."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .stm_client import STMClient


@dataclass
class TemperatureReading:
    """Snapshot of all available temperature channels (Kelvin). None if not wired."""
    stm: Optional[float] = None       # STM scanner / sample stage
    cryo_4K: Optional[float] = None   # 4 K shield
    cryo_1K: Optional[float] = None   # 1 K stage (if equipped)
    one_K: Optional[float] = None     # 1K head / pot
    adc2_K: Optional[float] = None    # raw ADC2 readout
    adc3_K: Optional[float] = None    # raw ADC3 readout
    aux6_K: Optional[float] = None    # AUX ADC 6
    aux7_K: Optional[float] = None    # AUX ADC 7


class TemperatureMonitor:
    """Cryogenic temperature readouts.

    Different rigs wire different sensors to ADC2/ADC3/AUXADC6/AUXADC7.
    This monitor reads everything available and packages it; consumers
    decide which fields are meaningful for their setup.
    """

    def __init__(self, client: "STMClient") -> None:
        self._c = client

    def _try_float(self, key: str) -> Optional[float]:
        try:
            v = self._c.getp(key, "")
            if v is None or v == "":
                return None
            return float(v)
        except Exception:
            return None

    def read(self) -> TemperatureReading:
        return TemperatureReading(
            stm=self._try_float("T-STM:"),
            cryo_4K=self._try_float("OneK_4K_Cryo"),
            cryo_1K=self._try_float("OneK_1K_Cryo"),
            one_K=self._try_float("OneK_STM"),
            adc2_K=self._try_float("T_ADC2[K]"),
            adc3_K=self._try_float("T_ADC3[K]"),
            aux6_K=self._try_float("T_AUXADC6[K]"),
            aux7_K=self._try_float("T_AUXADC7[K]"),
        )
