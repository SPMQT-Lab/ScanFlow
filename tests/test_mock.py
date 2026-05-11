"""Tests for the MockDispatch — verifies offline simulation works end-to-end."""

import time
import numpy as np
import pytest

from scanflow.core import STMClient
from scanflow.core.scan import ScanDataChannel, ScanDataUnit


@pytest.fixture
def stm():
    s = STMClient()
    assert s.connect_mock()
    yield s
    s.disconnect()


def test_mock_connect_sets_flag(stm):
    assert stm.is_mock is True
    assert stm.connected  # mock satisfies the same probe


def test_setp_getp_roundtrip(stm):
    stm.setp("SCAN.BIASVOLTAGE.VOLT", 0.25)
    assert float(stm.getp("SCAN.BIASVOLTAGE.VOLT", 0)) == pytest.approx(0.25)


def test_scan_status_lifecycle(stm):
    assert int(stm.getp("STMAFM.SCANSTATUS", 0)) == 0
    stm.setp("STMAFM.BTN.START", "")
    assert int(stm.getp("STMAFM.SCANSTATUS", 0)) == 2
    # Stop early
    stm.setp("STMAFM.BTN.STOP", "")
    assert int(stm.getp("STMAFM.SCANSTATUS", 0)) == 0


def test_approach_finishes(stm):
    stm.setp("HVAMPCOARSE.APPROACH.START", "")
    assert int(stm.getp("HVAMPCOARSE.APPROACH.FINISHED", 0)) == 0
    # Mock approach takes ~2.5 s; wait a little longer
    deadline = time.time() + 5
    while time.time() < deadline:
        if int(stm.getp("HVAMPCOARSE.APPROACH.FINISHED", 0)) == 1:
            break
        time.sleep(0.2)
    assert int(stm.getp("HVAMPCOARSE.APPROACH.FINISHED", 0)) == 1


def test_live_scan_data_shape(stm):
    stm.scan.pixels = (64, 64)
    arr = stm.scan.live_data(channel=int(ScanDataChannel.TOPOGRAPHY_FWD),
                             unit=int(ScanDataUnit.NM))
    assert arr is not None
    assert arr.shape == (64, 64)
    assert np.isfinite(arr).all()


def test_live_scan_partial_during_scan(stm):
    """While scanning, rows below the front line should be zero."""
    stm.scan.pixels = (32, 32)
    stm.setp("SCAN.SPEED.NM/SEC", 50.0)
    stm.setp("STMAFM.BTN.START", "")
    time.sleep(0.1)   # just barely into the scan
    arr = stm.scan.live_data()
    assert arr is not None
    # Bottom half should be zero
    assert (arr[-4:, :] == 0).any()
    stm.setp("STMAFM.BTN.STOP", "")


def test_temperature_readouts(stm):
    reading = stm.temperature.read()
    assert reading is not None
    # The default mock has 4.5 K STM, 77 K cryostat
    assert reading.stm == pytest.approx(4.5)
    assert reading.adc3_K == pytest.approx(77.0)


def test_user_dispatch_available(stm):
    assert stm.user is not None
    assert stm.crosscorr() == (0.0, 0.0)
    assert stm.tip_xy_position() == (0.0, 0.0)
