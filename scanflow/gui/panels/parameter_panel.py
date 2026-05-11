"""Parameter browser — inspect and set any STMAFM key by name.

Useful when:
    • a recipe needs a parameter the dedicated panels don't expose
    • debugging — read live values without leaving ScanFlow
    • exploring the manufacturer's key tree

The panel groups the most-used keys by namespace and lets the user enter
arbitrary keys at the bottom.
"""

from __future__ import annotations

from typing import List, Tuple

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QLineEdit, QPushButton, QGroupBox, QGridLayout, QLabel, QMessageBox,
    QHeaderView,
)
from PySide6.QtCore import Signal, Qt

from scanflow.core import STMClient


# Curated list of well-known keys grouped by namespace. The runtime values
# come from the live instrument via getp.
_KEY_GROUPS: List[Tuple[str, List[str]]] = [
    ("SCAN", [
        "SCAN.BIASVOLTAGE.VOLT",
        "SCAN.SETPOINT.AMPERE",
        "SCAN.PREAMPGAIN.EXPONENT",
        "SCAN.SPEED.NM/SEC",
        "SCAN.IMAGESIZE.NM.X",
        "SCAN.IMAGESIZE.NM.Y",
        "SCAN.NUM.X",
        "SCAN.NUM.Y",
        "SCAN.ROTATION.DEG",
        "SCAN.CHANNELS",
        "STMAFM.SCANSTATUS",
        "Sec/Image:",
    ]),
    ("HVAMPCOARSE (Approach)", [
        "HVAMPCOARSE.APPROACH.START",
        "HVAMPCOARSE.APPROACH.FINISHED",
        "HVAMPCOARSE.APPROACH.BURSTCOUNT",
        "HVAMPCOARSE.APPROACH.RETRYCOUNT",
        "HVAMPCOARSE.APPROACH.PERIOD.SEC",
        "HVAMPCOARSE.PULSEHEIGHT.VOLT",
        "HVAMPCOARSE.PULSEDURATION.SEC",
        "HVAMPCOARSE.BURSTCOUNT.XY",
        "HVAMPCOARSE.BURSTCOUNT.Z",
    ]),
    ("SLIDER", [
        "SLIDER.ZLIMIT.ON",
        "SLIDER.ZLIMIT.VOLT",
        "SLIDER.ZLIMIT.RETRACT.NM",
    ]),
    ("LOCK-IN", [
        "LOCK-IN.FREQ.HZ",
        "LOCK-IN.AMPLITUDE.MVPP",
        "LOCK-IN.CHANNEL",
        "LOCK-IN.MODE",
        "LOCK-IN.PHASE1.DEG",
    ]),
    ("VERTMAN (Spectroscopy)", [
        "VERTMAN.PREAMPGAIN.EXPONENT",
        "VERTMAN.SPECLENGTH.SEC",
        "VERTMAN.REPEATCOUNT",
        "VERTMAN.SPECAVRG.COUNT",
        "VERTMAN.LATSPEED.NM/SEC",
        "VERTMAN.SPEC.BACK",
        "VERTMAN.CHANNELs",
    ]),
    ("AFM / PLL", [
        "AFM.CHK.DF_CONTROL",
        "AFM.CHK.AMPLITUDE_CONTROL",
        "AFM.PLL_EXCITATION.VOLT",
        "AFM.PLL_AMPLITUDE.NM",
        "AFM.SRS_GAIN",
        "AFM.RESULTS.FCENTER.HZ",
        "AFM.TUNE.DF.BW.HZ",
        "AFM.TUNE.AMPLITUDE.BW.HZ",
    ]),
    ("LATMAN (Lateral)", [
        "LATMAN.BIASVOLTAGE.VOLT",
        "LATMAN.SETPOINT.AMPERE",
        "LATMAN.LATSPEED.NM/SEC",
        "LATMAN.CHK.FB",
    ]),
    ("Temperature", [
        "T_ADC2[K]",
        "T_ADC3[K]",
        "T_AUXADC6[K]",
        "T_AUXADC7[K]",
        "T-STM:",
    ]),
]


class ParameterPanel(QWidget):
    log_message = Signal(str)

    def __init__(self, stm: STMClient, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm
        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # Tree of known keys
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Key", "Value"])
        self._tree.header().setSectionResizeMode(QHeaderView.Stretch)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        for group_name, keys in _KEY_GROUPS:
            group_item = QTreeWidgetItem([group_name, ""])
            group_item.setExpanded(True)
            for k in keys:
                child = QTreeWidgetItem([k, "—"])
                group_item.addChild(child)
            self._tree.addTopLevelItem(group_item)
            group_item.setExpanded(True)
        root.addWidget(self._tree, 1)

        btn_row = QHBoxLayout()
        refresh = QPushButton("Refresh all")
        refresh.clicked.connect(self.refresh_all)
        btn_row.addWidget(refresh)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        # Arbitrary key inspector
        adhoc = QGroupBox("Inspect / set arbitrary key")
        g = QGridLayout(adhoc)

        g.addWidget(QLabel("Key"), 0, 0)
        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("e.g. SCAN.BIASVOLTAGE.VOLT")
        g.addWidget(self._key_edit, 0, 1, 1, 3)

        g.addWidget(QLabel("Value"), 1, 0)
        self._value_edit = QLineEdit()
        self._value_edit.setPlaceholderText("Read shows here · type to set")
        g.addWidget(self._value_edit, 1, 1, 1, 3)

        read_btn = QPushButton("Read (getp)")
        read_btn.clicked.connect(self._read_adhoc)
        g.addWidget(read_btn, 2, 1)

        write_btn = QPushButton("Write (setp)")
        write_btn.clicked.connect(self._write_adhoc)
        g.addWidget(write_btn, 2, 2)

        root.addWidget(adhoc)

    # ------------------------------------------------------------------

    def refresh_all(self) -> None:
        if not self._stm.connected and not self._stm.is_mock:
            self.log_message.emit("Parameter browser: STM not connected")
            return
        for i in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(i)
            for j in range(group.childCount()):
                child = group.child(j)
                key = child.text(0)
                try:
                    value = self._stm.getp(key, "")
                except Exception as e:
                    value = f"<error: {e}>"
                child.setText(1, _format_value(value))

    def _on_double_click(self, item: QTreeWidgetItem, col: int) -> None:
        # Pre-fill the inspector with the clicked key for quick editing
        if item.parent() is None:
            return  # group header
        self._key_edit.setText(item.text(0))
        self._value_edit.setText(item.text(1) if item.text(1) != "—" else "")

    def _read_adhoc(self) -> None:
        key = self._key_edit.text().strip()
        if not key:
            return
        try:
            value = self._stm.getp(key, "")
            self._value_edit.setText(_format_value(value))
            self.log_message.emit(f"getp {key} → {value!r}")
        except Exception as e:
            QMessageBox.critical(self, "Read error", str(e))

    def _write_adhoc(self) -> None:
        key = self._key_edit.text().strip()
        raw = self._value_edit.text().strip()
        if not key:
            return
        value = _parse_value(raw)
        try:
            self._stm.setp(key, value)
            self.log_message.emit(f"setp {key} ← {value!r}")
        except Exception as e:
            QMessageBox.critical(self, "Write error", str(e))


def _format_value(v) -> str:
    if isinstance(v, (tuple, list)):
        return ", ".join(_format_value(x) for x in v)
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _parse_value(raw: str):
    """Best-effort: int → float → bool → tuple → string."""
    if raw.lower() in ("on", "true"):
        return "ON"
    if raw.lower() in ("off", "false"):
        return "OFF"
    if "," in raw:
        parts = [_parse_scalar(p.strip()) for p in raw.split(",")]
        return tuple(parts)
    return _parse_scalar(raw)


def _parse_scalar(s: str):
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s
