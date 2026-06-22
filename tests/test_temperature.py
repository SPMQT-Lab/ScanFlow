"""Tests for TemperatureMonitor — esp. the Createc refresh poke."""

from scanflow.core.temperature import TemperatureMonitor


class _SpyClient:
    """Minimal STM client double recording setp/getp calls in order."""

    is_mock = True

    def __init__(self, values=None):
        self.values = values or {}
        self.calls: list[tuple] = []

    def setp(self, key, value):
        self.calls.append(("setp", key, value))

    def getp(self, key, default=""):
        self.calls.append(("getp", key))
        return self.values.get(key, "")


def test_read_pokes_memo_before_reading():
    """Regression (lab 2026-06-22 'no Temperature'): STMAFM 4.3+ leaves the
    T_*[K] params stale until a parameter write nudges a refresh, so read()
    must MEMO_STMAFM-poke before any getp."""
    c = _SpyClient()
    TemperatureMonitor(c).read()
    assert c.calls, "read() made no COM calls"
    assert c.calls[0] == ("setp", "MEMO_STMAFM", ""), (
        f"first call must be the refresh poke, got {c.calls[0]}"
    )
    # And the poke precedes every getp.
    first_getp = next(i for i, c_ in enumerate(c.calls) if c_[0] == "getp")
    assert first_getp > 0


def test_read_returns_values_after_poke():
    c = _SpyClient(values={"T_AUXADC6[K]": "4.83", "T_AUXADC7[K]": "4.21"})
    reading = TemperatureMonitor(c).read()
    assert reading.aux6_K == 4.83
    assert reading.aux7_K == 4.21


def test_poke_failure_is_non_fatal():
    class _Boom(_SpyClient):
        def setp(self, key, value):
            raise RuntimeError("share down")
    c = _Boom(values={"T_AUXADC6[K]": "4.83"})
    reading = TemperatureMonitor(c).read()  # must not raise
    assert reading.aux6_K == 4.83
