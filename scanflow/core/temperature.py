"""Temperature monitor — read all temperature sensors on the cryostat."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .stm_client import STMClient

log = logging.getLogger(__name__)


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
        self._working_keys: dict[str, str] = {}   # field → key that actually works
        self._diagnosed = False

    def _try_float(self, *keys: str) -> Optional[float]:
        """Try each key in order, return the first that yields a non-empty float."""
        for key in keys:
            try:
                v = self._c.getp(key, "")
                if v is not None and v != "":
                    return float(v)
            except Exception:
                pass
        return None

    def _try_float_tracked(self, field: str, *keys: str) -> Optional[float]:
        """Like _try_float but remembers which key worked and logs it once."""
        if field in self._working_keys:
            # fast path: use the key we already know works
            return self._try_float(self._working_keys[field])
        for key in keys:
            try:
                v = self._c.getp(key, "")
                if v is not None and v != "":
                    val = float(v)
                    if not self._diagnosed:
                        log.info("Temperature key resolved: %s → %r", field, key)
                    self._working_keys[field] = key
                    return val
            except Exception:
                pass
        return None

    def read(self) -> TemperatureReading:
        reading = TemperatureReading(
            # T-STM: has a trailing colon that some Createc versions reject; try both
            stm=self._try_float_tracked("stm", "T-STM:", "T-STM", "T_STM"),
            # OneK channels: no special characters, but may simply not be wired
            cryo_4K=self._try_float_tracked("cryo_4K", "OneK_4K_Cryo"),
            cryo_1K=self._try_float_tracked("cryo_1K", "OneK_1K_Cryo"),
            one_K=self._try_float_tracked("one_K", "OneK_STM"),
            # ADC keys: [K] brackets may not be accepted by COM getp; try stripped fallback
            adc2_K=self._try_float_tracked("adc2_K", "T_ADC2[K]", "T_ADC2"),
            adc3_K=self._try_float_tracked("adc3_K", "T_ADC3[K]", "T_ADC3"),
            aux6_K=self._try_float_tracked("aux6_K", "T_AUXADC6[K]", "T_AUXADC6"),
            aux7_K=self._try_float_tracked("aux7_K", "T_AUXADC7[K]", "T_AUXADC7"),
        )
        if not self._diagnosed:
            working = [f for f in self._working_keys]
            if working:
                log.info("Temperature keys resolved on first read: %s", working)
            else:
                log.warning(
                    "Temperature read: no keys returned a value. "
                    "Checked: T-STM:/T-STM, OneK_*, T_ADC2/3[K], T_AUXADC6/7[K]. "
                    "Verify key names in Createc parameter list."
                )
            self._diagnosed = True
        return reading
