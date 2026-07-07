"""Tests for TemperatureMonitor — esp. the Createc refresh poke."""

from scanflow.core.temperature import TemperatureMonitor


class _RawCom:
    """Stand-in for the underlying pstmafm COM object (py-createc style)."""

    def __init__(self, values=None):
        self.values = values or {}
        self.calls: list[tuple] = []

    def setparam(self, key, value):
        self.calls.append(("setparam", key, value))

    def getparam(self, key):
        self.calls.append(("getparam", key))
        return self.values.get(key, "")


class _Client:
    """STM client double exposing both .raw (getparam/setparam) and getp/setp."""

    is_mock = True

    def __init__(self, raw=None, getp_values=None):
        self._raw = raw
        self.getp_values = getp_values or {}
        self.calls: list[tuple] = []

    @property
    def raw(self):
        if self._raw is None:
            raise RuntimeError("not connected")
        return self._raw

    def setp(self, key, value):
        self.calls.append(("setp", key, value))

    def getp(self, key, default=""):
        self.calls.append(("getp", key))
        return self.getp_values.get(key, default)


def test_read_pokes_memo_via_setparam_first():
    """Regression (lab 2026-06-22 'no Temperature'): the refresh poke must use
    the real setparam COM method, since ScanFlow's setp does not trigger the
    STMAFM 4.3+ readout refresh — and it must land BEFORE any T_* read."""
    raw = _RawCom(values={"T_AUXADC6[K]": "4.83"})
    c = _Client(raw=raw)
    TemperatureMonitor(c).read()
    poke_idx = next(
        (i for i, call in enumerate(raw.calls) if call[0] == "setparam"), None
    )
    assert poke_idx is not None, "setparam refresh poke never happened"
    assert raw.calls[poke_idx][1] == "MEMO_STMAFM"
    first_temp_read = next(
        i for i, call in enumerate(raw.calls)
        if call[0] == "getparam" and call[1].startswith("T_")
    )
    assert poke_idx < first_temp_read, "poke must precede the T_* reads"


def test_poke_preserves_scan_memo():
    """Regression: the poke used to write '' — but MEMO_STMAFM is the same
    key scan.apply() uses to label saved .dat files, so a temperature poll
    landing between apply and save blanked the scan's memo. The refresh is
    triggered by the WRITE, not the value: the poke must write back the
    memo it just read."""
    raw = _RawCom(values={"MEMO_STMAFM": "tile_02_iter3_+100mV",
                          "T_AUXADC6[K]": "4.83"})
    TemperatureMonitor(_Client(raw=raw)).read()
    pokes = [c for c in raw.calls if c[0] == "setparam" and c[1] == "MEMO_STMAFM"]
    assert pokes == [("setparam", "MEMO_STMAFM", "tile_02_iter3_+100mV")]


def test_read_values_via_getparam():
    raw = _RawCom(values={"T_AUXADC6[K]": "4.83", "T_AUXADC7[K]": "4.21"})
    reading = TemperatureMonitor(_Client(raw=raw)).read()
    assert reading.aux6_K == 4.83
    assert reading.aux7_K == 4.21


def test_falls_back_to_getp_when_no_raw():
    """Mock / no raw COM: must still read via getp + poke via setp."""
    c = _Client(raw=None, getp_values={"T_AUXADC6[K]": "4.5"})
    reading = TemperatureMonitor(c).read()
    assert reading.aux6_K == 4.5
    assert ("setp", "MEMO_STMAFM", "") in c.calls


def test_poke_failure_is_non_fatal():
    raw = _RawCom(values={"T_AUXADC6[K]": "4.83"})
    def _boom(*a, **k):
        raise RuntimeError("share down")
    raw.setparam = _boom
    reading = TemperatureMonitor(_Client(raw=raw)).read()  # must not raise
    assert reading.aux6_K == 4.83
