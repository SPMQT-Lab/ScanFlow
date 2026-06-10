"""Tip-form arming dialog — the GUI half of the runner's approval gate.

The runner refuses tip-form steps unless ``approve_next_tip_form()`` was
called for that specific run (REVIEW H5). This dialog is the only GUI
path that triggers that call: it shows the pulse parameters and the
motion pre-flight warnings (the Createc travel-speed quirk), and arms
only after the operator types ``ARM`` — a deliberate speed bump, because
once ``TIP-FORM.CMD.START`` is issued the controller cannot be
interrupted.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout,
)

from scanflow.automation.recipe import TipFormStep
from scanflow.core import TipFormMotionAssessment

ARM_WORD = "ARM"


def summarize_tip_form(step: TipFormStep) -> str:
    """One-paragraph human summary of what the pulse will do."""
    return (
        f"Pulse {step.voltage_V:+.2f} V for {step.pulse_length_s * 1000:.0f} ms "
        f"at pixel ({step.x_px}, {step.y_px}) of the current frame.\n"
        f"Z approach {step.z_approach_nm:.2f} nm, Z offset "
        f"{step.z_offset_nm:+.2f} nm, travel speed "
        f"{step.lateral_speed_nm_s:.1f} nm/s."
    )


class TipFormArmDialog(QDialog):
    """Modal arming confirmation for ONE supervised tip-form pulse."""

    def __init__(
        self,
        step: TipFormStep,
        assessment: TipFormMotionAssessment | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Arm tip forming")
        layout = QVBoxLayout(self)

        summary = QLabel(summarize_tip_form(step))
        summary.setWordWrap(True)
        layout.addWidget(summary)

        if assessment is not None and assessment.warnings:
            for warning in assessment.warnings:
                label = QLabel("⚠ " + warning)
                label.setWordWrap(True)
                label.setStyleSheet("color: #C75100; font-weight: bold;")
                layout.addWidget(label)

        note = QLabel(
            "Arming authorises exactly ONE pulse for the run started next. "
            "Once the command is sent, STMAFM cannot be interrupted until "
            "the tip reaches the target and the pulse completes.\n\n"
            f"Type {ARM_WORD} to enable the arm button:"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self._confirm_edit = QLineEdit()
        self._confirm_edit.setPlaceholderText(ARM_WORD)
        self._confirm_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._confirm_edit)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self._arm_button = self._buttons.button(QDialogButtonBox.Ok)
        self._arm_button.setText("Arm one pulse")
        self._arm_button.setEnabled(False)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    def _on_text_changed(self, text: str) -> None:
        self._arm_button.setEnabled(text.strip().upper() == ARM_WORD)

    @property
    def arm_enabled(self) -> bool:
        return self._arm_button.isEnabled()
