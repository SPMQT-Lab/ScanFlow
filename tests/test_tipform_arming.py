"""Tip-form arming: motion pre-flight, GUI gate, CLI gate (REVIEW H5).

The Createc quirk under test: the tip travels to the tip-form target at
TIP-FORM.LATSPEED, not the scan speed — either a violent jump (lateral
far above scan speed) or an uninterruptible crawl (far below). The
assessment must surface this BEFORE arming, and nothing may execute a
pulse without the explicit per-run approval.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from scanflow.core import STMClient, assess_tip_form_motion
from scanflow.automation import MeasurementRecipe
from scanflow.automation.recipe import ScanStep, TipFormStep


@pytest.fixture
def stm():
    s = STMClient()
    assert s.connect_mock()
    yield s
    s.disconnect()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ── motion pre-flight (pure logic) ────────────────────────────────────────

def test_matched_speed_passes_clean():
    a = assess_tip_form_motion(50.0, scan_speed_nm_s=50.0,
                               frame_size_nm=(50.0, 50.0))
    assert a.ok
    assert a.speed_ratio == pytest.approx(1.0)


def test_fast_jump_warning():
    a = assess_tip_form_motion(1000.0, scan_speed_nm_s=20.0,
                               frame_size_nm=(50.0, 50.0))
    assert not a.ok
    assert any("fast jump" in w for w in a.warnings)


def test_uninterruptible_crawl_warning():
    # 0.5 nm/s across a 120 nm frame: diagonal ≈ 170 nm → ~340 s
    a = assess_tip_form_motion(0.5, scan_speed_nm_s=50.0,
                               frame_size_nm=(120.0, 120.0))
    assert not a.ok
    assert any("cannot be interrupted" in w for w in a.warnings)
    assert a.worst_travel_s > 60.0


def test_recipe_validate_flags_speed_mismatch():
    recipe = MeasurementRecipe(steps=[
        ScanStep(bias_V=0.1, setpoint_A=50e-12, speed_nm_s=20.0),
        TipFormStep(lateral_speed_nm_s=1000.0),
    ])
    issues = recipe.validate()
    assert any("fast jump" in i for i in issues)

    recipe2 = MeasurementRecipe(steps=[
        ScanStep(bias_V=0.1, setpoint_A=50e-12, speed_nm_s=50.0),
        TipFormStep(lateral_speed_nm_s=1.0),
    ])
    assert any("cannot be interrupted" in i for i in recipe2.validate())


# ── arming dialog ─────────────────────────────────────────────────────────

def test_arm_dialog_requires_typed_confirmation(qapp):
    from scanflow.gui.widgets.tipform_arm import TipFormArmDialog
    dialog = TipFormArmDialog(TipFormStep())
    assert dialog.arm_enabled is False
    dialog._confirm_edit.setText("yes")          # wrong word
    assert dialog.arm_enabled is False
    dialog._confirm_edit.setText("arm")          # case-insensitive
    assert dialog.arm_enabled is True
    dialog._confirm_edit.setText("")
    assert dialog.arm_enabled is False


# ── panel: end-to-end armed pulse on the mock ─────────────────────────────

def test_panel_armed_run_executes_exactly_one_pulse(qapp, stm, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from scanflow.gui.panels import tipform_panel as tp

    panel = tp.TipFormPanel(stm)
    monkeypatch.setattr(tp.TipFormArmDialog, "exec",
                        lambda self: QDialog.Accepted)

    pulses: list[tuple] = []
    stm.tipform.form_at = lambda x, y, params: pulses.append((x, y, params))
    stm.scan.live_data = lambda *a, **k: np.zeros((8, 8))

    panel._arm_and_run()
    assert panel._runner is not None
    assert panel._runner.wait(15000)
    assert len(pulses) == 1
    x, y, params = pulses[0]
    assert (x, y) == (panel._x_px.value(), panel._y_px.value())

    # A second run on the same panel must require a fresh arming —
    # the dialog auto-accepts here, so a second pulse IS armed and runs.
    panel._arm_and_run()
    assert panel._runner.wait(15000)
    assert len(pulses) == 2


def test_panel_cancelled_dialog_does_not_arm(qapp, stm, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from scanflow.gui.panels import tipform_panel as tp

    panel = tp.TipFormPanel(stm)
    monkeypatch.setattr(tp.TipFormArmDialog, "exec",
                        lambda self: QDialog.Rejected)
    pulses: list[tuple] = []
    stm.tipform.form_at = lambda *a, **k: pulses.append(a)

    panel._arm_and_run()
    assert panel._runner is None
    assert pulses == []


# ── CLI: --arm-tip-form is the only path ──────────────────────────────────

def _write_tipform_recipe(tmp_path):
    recipe = MeasurementRecipe(name="cli tip form", steps=[
        TipFormStep(x_px=16, y_px=16, voltage_V=0.5, pulse_length_s=0.1,
                    lateral_speed_nm_s=50.0),
    ])
    path = tmp_path / "tipform.yaml"
    recipe.save(path)
    return path


def test_cli_run_without_arm_flag_halts_at_tip_form(tmp_path, qapp):
    from scanflow.cli import main
    rc = main(["run", str(_write_tipform_recipe(tmp_path)), "--mock", "--yes"])
    assert rc == 2  # runner reported the refusal as an error


def test_cli_run_with_arm_flag_executes(tmp_path, qapp, monkeypatch):
    import scanflow.cli as cli
    from scanflow.core import STMClient as RealSTMClient

    pulses: list[tuple] = []

    def patched_connect(mock):
        stm = RealSTMClient()
        stm.connect_mock()
        stm.tipform.form_at = lambda *a, **k: pulses.append(a)
        stm.scan.live_data = lambda *a, **k: np.zeros((8, 8))
        return stm

    monkeypatch.setattr(cli, "_connect", patched_connect)
    rc = cli.main(["run", str(_write_tipform_recipe(tmp_path)),
                   "--mock", "--yes", "--arm-tip-form"])
    assert rc == 0
    assert len(pulses) == 1
