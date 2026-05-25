import pytest

from scanflow.core import MotionConfig, STMClient, TipMotionManager


@pytest.fixture
def stm():
    s = STMClient()
    assert s.connect_mock()
    yield s
    s.disconnect()


def test_calibrate_from_current_offset(stm):
    stm.raw.setxyoffvolt(1.0, 2.0)
    motion = TipMotionManager(stm, config=MotionConfig(default_settle_s=0.0))

    cal = motion.calibrate_xy("current")

    assert cal.volts_per_nm_x == pytest.approx(0.1)
    assert cal.volts_per_nm_y == pytest.approx(0.1)
    assert cal.source == "current"


def test_calibrate_by_delta_from_origin(stm):
    motion = TipMotionManager(stm, config=MotionConfig(default_settle_s=0.0))

    cal = motion.calibrate_xy("delta")

    assert cal.volts_per_nm_x == pytest.approx(0.1)
    assert cal.volts_per_nm_y == pytest.approx(0.1)
    assert stm.scan.get_offset_nm() == pytest.approx((0.0, 0.0))


def test_move_absolute_nm_uses_calibration_and_verifies_readback(stm):
    motion = TipMotionManager(stm, config=MotionConfig(default_settle_s=0.0))

    result = motion.move_absolute_nm(10.0, -5.0, reason="unit test")

    assert result.ok is True
    assert result.after_nm == pytest.approx((10.0, -5.0))
    assert result.attempts == 1
    assert stm.scan.get_offset_nm() == pytest.approx((10.0, -5.0))


def test_move_relative_nm_updates_from_readback(stm):
    stm.raw.setxyoffvolt(1.0, 1.0)
    motion = TipMotionManager(stm, config=MotionConfig(default_settle_s=0.0))

    result = motion.move_relative_nm(5.0, -2.0, reason="relative")

    assert result.ok is True
    assert result.before_nm == pytest.approx((10.0, 10.0))
    assert result.after_nm == pytest.approx((15.0, 8.0))


def test_pixel_nudge_converts_using_scan_frame(stm):
    stm.scan.size_nm = (20.0, 10.0)
    stm.scan.pixels = (200, 100)
    motion = TipMotionManager(stm, config=MotionConfig(default_settle_s=0.0))

    result = motion.move_relative_pixels(
        10.0,
        -5.0,
        frame_size_nm=(20.0, 10.0),
        frame_pixels=(200, 100),
        reason="pixel nudge",
    )

    assert result.ok is True
    assert result.after_nm == pytest.approx((1.0, -0.5))


def test_move_rejects_over_limit(stm):
    motion = TipMotionManager(
        stm,
        config=MotionConfig(default_settle_s=0.0, max_single_move_nm=1.0),
    )

    result = motion.move_absolute_nm(10.0, 0.0, reason="too far")

    assert result.ok is False
    assert result.attempts == 0
    assert "exceeds limit" in " ".join(result.warnings)


def test_readback_failure_returns_failed_motion_result(stm):
    stm.raw.setxyoffvolt(1.0, 1.0)
    motion = TipMotionManager(stm, config=MotionConfig(default_settle_s=0.0))
    motion.calibrate_xy("current")

    stm.raw.setxyoffvolt = lambda _x, _y: None

    result = motion.move_absolute_nm(20.0, 20.0, reason="bad actuator")

    assert result.ok is False
    assert "readback error" in " ".join(result.warnings)


def test_scan_running_blocks_motion(stm):
    motion = TipMotionManager(stm, config=MotionConfig(default_settle_s=0.0))
    stm.scan.start()

    try:
        result = motion.move_absolute_nm(1.0, 1.0, reason="while running")
    finally:
        stm.scan.stop()

    assert result.ok is False
    assert "while scan is running" in " ".join(result.warnings)

