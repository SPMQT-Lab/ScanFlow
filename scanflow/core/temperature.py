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
        # field → (method, key) that actually returns a value on this rig.
        self._working: dict[str, tuple[str, str]] = {}
        self._diagnosed = False

    def _raw(self):
        """The underlying COM object, or None (mock / not connected)."""
        try:
            return self._c.raw
        except Exception:
            return None

    def _poke_refresh(self) -> None:
        """Force Createc to refresh its temperature readout before we read it.

        On STMAFM 4.3+ the ``T_*[K]`` parameters stay stale (usually empty)
        until a parameter write nudges the server to re-poll the ADCs.
        py-createc does a dummy ``setparam('MEMO_STMAFM', '')`` before every
        temperature read; ScanFlow's own ``setp`` is a different COM method
        that does NOT trigger the refresh, which is why the tab stayed at
        "no data" even after the first poke attempt. Try the real
        ``setparam`` first, then fall back to ``setp``. Best-effort.

        The poke VALUE matters: MEMO_STMAFM is the same key scan.apply()
        uses to label saved .dat files, so a poll landing between apply and
        save used to blank the scan's memo. The refresh is triggered by the
        WRITE, not the value — read the current memo and write it back
        unchanged.
        """
        memo = ""
        for reader in ("getparam", "getp"):
            try:
                v = self._read_via(reader, "MEMO_STMAFM")
                if v not in (None, ""):
                    memo = str(v)
                    break
            except Exception:
                pass
        raw = self._raw()
        if raw is not None:
            try:
                raw.setparam("MEMO_STMAFM", memo)
                return
            except Exception:
                pass
        try:
            self._c.setp("MEMO_STMAFM", memo)
        except Exception:
            pass

    def _read_via(self, method: str, key: str):
        """Read one parameter using a specific COM method ('getparam'/'getp')."""
        if method == "getparam":
            raw = self._raw()
            if raw is None:
                return None
            return raw.getparam(key)        # py-createc's method (1 arg)
        return self._c.getp(key, "")        # ScanFlow's method (key, default)

    def _try_float_tracked(self, field: str, *keys: str) -> Optional[float]:
        """Resolve a sensor by probing both COM read methods and all candidate
        keys, remembering the (method, key) combo that first returns a value.

        ScanFlow reads scan params fine via ``getp``, but on some rigs the
        ``T_*[K]`` temperature params are only exposed through ``getparam``
        (py-createc's method). Probing both makes the readout work without
        having to know which the rig wants."""
        if field in self._working:
            method, key = self._working[field]
            try:
                v = self._read_via(method, key)
                if v is not None and v != "":
                    return float(v)
            except Exception:
                pass
            return None
        for method in ("getparam", "getp"):
            for key in keys:
                try:
                    v = self._read_via(method, key)
                    if v is not None and v != "":
                        val = float(v)
                        if not self._diagnosed:
                            log.info("Temperature resolved: %s → %s(%r)",
                                     field, method, key)
                        self._working[field] = (method, key)
                        return val
                except Exception:
                    pass
        return None

    def read(self) -> TemperatureReading:
        # Nudge Createc to refresh the readout, otherwise the T_*[K] keys
        # come back empty and every sensor reads as "no data".
        self._poke_refresh()
        # This rig only wires the two AUXADC channels (STM = AUXADC6,
        # LHe = AUXADC7). Once getparam is used, the other speculative keys
        # (T-STM:, OneK_*, T_ADC2/3) resolve to unwired-channel garbage, so
        # they are not probed — only the two real sensors are read.
        reading = TemperatureReading(
            aux6_K=self._try_float_tracked("aux6_K", "T_AUXADC6[K]", "T_AUXADC6"),
            aux7_K=self._try_float_tracked("aux7_K", "T_AUXADC7[K]", "T_AUXADC7"),
        )
        if not self._diagnosed:
            if self._working:
                log.info("Temperature resolved on first read: %s",
                         {f: m for f, (m, _k) in self._working.items()})
            else:
                raw = self._raw()
                has_getparam = raw is not None and hasattr(raw, "getparam")
                log.warning(
                    "Temperature read: no keys returned a value via getparam "
                    "or getp. raw COM present=%s, getparam attr=%s. Checked: "
                    "T-STM:/T-STM, OneK_*, T_ADC2/3[K], T_AUXADC6/7[K]. "
                    "Verify the key names in the Createc parameter list.",
                    raw is not None, has_getparam,
                )
            self._diagnosed = True
        return reading
