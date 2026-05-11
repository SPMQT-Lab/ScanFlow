"""Lateral atom manipulation panel — controlled tip drags.

Safety first: this tab can move single atoms on the surface. Drag bias
is typically 10 mV with 50 nA setpoint and 0.5 nm/s speed. A confirmation
dialog appears before each drag to reduce the chance of an accidental click.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QGroupBox, QLabel,
    QDoubleSpinBox, QSpinBox, QPushButton, QCheckBox, QMessageBox,
)
from PySide6.QtCore import Signal, Qt

from scanflow.core import STMClient, LateralParams


class LateralPanel(QWidget):
    """Configure and execute a lateral manipulation pull."""

    log_message = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        warn = QLabel(
            "<b>⚠ Caution.</b> Lateral manipulation drags individual atoms across the "
            "surface. Verify bias, setpoint, and endpoints before each pull — "
            "wrong values can crash the tip or move the wrong adsorbate."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #C75100; padding: 6px;")
        root.addWidget(warn)

        # Parameters
        params = QGroupBox("Manipulation parameters")
        pg_ = QGridLayout(params)

        pg_.addWidget(QLabel("Bias (mV)"), 0, 0)
        self._bias_mV = QDoubleSpinBox()
        self._bias_mV.setRange(-1000.0, 1000.0)
        self._bias_mV.setDecimals(2)
        self._bias_mV.setSingleStep(1.0)
        self._bias_mV.setValue(10.0)
        pg_.addWidget(self._bias_mV, 0, 1)

        pg_.addWidget(QLabel("Setpoint (nA)"), 0, 2)
        self._setpoint_nA = QDoubleSpinBox()
        self._setpoint_nA.setRange(0.001, 1000.0)
        self._setpoint_nA.setDecimals(3)
        self._setpoint_nA.setSingleStep(0.5)
        self._setpoint_nA.setValue(50.0)
        pg_.addWidget(self._setpoint_nA, 0, 3)

        pg_.addWidget(QLabel("Speed (nm/s)"), 1, 0)
        self._speed_nm_s = QDoubleSpinBox()
        self._speed_nm_s.setRange(0.01, 100.0)
        self._speed_nm_s.setDecimals(3)
        self._speed_nm_s.setSingleStep(0.1)
        self._speed_nm_s.setValue(0.5)
        pg_.addWidget(self._speed_nm_s, 1, 1)

        self._fb_chk = QCheckBox("Z-feedback on during drag")
        self._fb_chk.setChecked(True)
        pg_.addWidget(self._fb_chk, 1, 2, 1, 2)

        apply_btn = QPushButton("Apply parameters")
        apply_btn.clicked.connect(self._apply_params)
        pg_.addWidget(apply_btn, 2, 0, 1, 4)

        root.addWidget(params)

        # Endpoints
        ep = QGroupBox("Drag endpoints (image-pixel coordinates)")
        eg = QGridLayout(ep)
        eg.addWidget(QLabel("Start"), 0, 0)
        eg.addWidget(QLabel("X"), 0, 1)
        self._x1 = QSpinBox(); self._x1.setRange(0, 8192); self._x1.setValue(100)
        eg.addWidget(self._x1, 0, 2)
        eg.addWidget(QLabel("Y"), 0, 3)
        self._y1 = QSpinBox(); self._y1.setRange(0, 8192); self._y1.setValue(128)
        eg.addWidget(self._y1, 0, 4)

        eg.addWidget(QLabel("End"), 1, 0)
        eg.addWidget(QLabel("X"), 1, 1)
        self._x2 = QSpinBox(); self._x2.setRange(0, 8192); self._x2.setValue(156)
        eg.addWidget(self._x2, 1, 2)
        eg.addWidget(QLabel("Y"), 1, 3)
        self._y2 = QSpinBox(); self._y2.setRange(0, 8192); self._y2.setValue(128)
        eg.addWidget(self._y2, 1, 4)

        self._confirm_chk = QCheckBox("Ask for confirmation before each drag")
        self._confirm_chk.setChecked(True)
        eg.addWidget(self._confirm_chk, 2, 0, 1, 5)

        drag_btn = QPushButton("Drag tip")
        drag_btn.setProperty("accent", "true")
        drag_btn.clicked.connect(self._drag)
        eg.addWidget(drag_btn, 3, 0, 1, 3)

        save_btn = QPushButton("Save trace…")
        save_btn.clicked.connect(self._save_trace)
        eg.addWidget(save_btn, 3, 3, 1, 2)

        root.addWidget(ep)
        root.addStretch(1)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _require_connection(self) -> bool:
        if not self._stm.connected:
            QMessageBox.warning(self, "STM Not Connected",
                                "Connect to the STM before manipulating atoms.")
            return False
        return True

    def _current_params(self) -> LateralParams:
        return LateralParams(
            bias_V=self._bias_mV.value() * 1e-3,
            setpoint_A=self._setpoint_nA.value() * 1e-9,
            speed_nm_s=self._speed_nm_s.value(),
            feedback_on=self._fb_chk.isChecked(),
        )

    def _apply_params(self) -> None:
        if not self._require_connection():
            return
        try:
            p = self._current_params()
            self._stm.lateral.apply(p)
            self.log_message.emit(
                f"Lateral params: V={p.bias_V*1000:.1f} mV, I={p.setpoint_A*1e9:.2f} nA, "
                f"v={p.speed_nm_s:.2f} nm/s, FB={'on' if p.feedback_on else 'off'}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Apply error", str(e))

    def _drag(self) -> None:
        if not self._require_connection():
            return
        x1, y1 = self._x1.value(), self._y1.value()
        x2, y2 = self._x2.value(), self._y2.value()
        if self._confirm_chk.isChecked():
            p = self._current_params()
            msg = (f"Confirm tip drag:\n\n"
                   f"  ({x1}, {y1}) → ({x2}, {y2})\n"
                   f"  Bias  : {p.bias_V*1000:.1f} mV\n"
                   f"  Sp    : {p.setpoint_A*1e9:.2f} nA\n"
                   f"  Speed : {p.speed_nm_s:.2f} nm/s\n"
                   f"  Feedback: {'on' if p.feedback_on else 'off'}\n\n"
                   f"Proceed?")
            if QMessageBox.question(self, "Confirm drag", msg) != QMessageBox.Yes:
                return
        try:
            # Apply current params first so the drag uses what's on screen
            self._stm.lateral.apply(self._current_params())
            self._stm.lateral.drag(x1, y1, x2, y2)
            self.log_message.emit(f"Tip drag: ({x1},{y1}) → ({x2},{y2})")
        except Exception as e:
            QMessageBox.critical(self, "Drag error", str(e))

    def _save_trace(self) -> None:
        if not self._require_connection():
            return
        try:
            self._stm.lateral.save_trace()
            self.log_message.emit("Lateral trace saved")
        except Exception as e:
            QMessageBox.critical(self, "Save error", str(e))
