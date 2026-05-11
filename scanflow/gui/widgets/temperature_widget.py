"""Status-bar temperature widget — live cryo temperature readout."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout
from PySide6.QtCore import QTimer

from scanflow.core import STMClient


class TemperatureWidget(QWidget):
    """Compact widget for the status bar; polls every 5 seconds."""

    def __init__(self, stm: STMClient, poll_ms: int = 5000, parent=None) -> None:
        super().__init__(parent)
        self._stm = stm

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        self._labels: dict[str, QLabel] = {}
        for key, title in [
            ("stm", "STM"),
            ("cryo_4K", "4K"),
            ("cryo_1K", "1K"),
        ]:
            lbl = QLabel(f"{title}: —")
            self._labels[key] = lbl
            layout.addWidget(lbl)

        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    def refresh(self) -> None:
        if not self._stm.connected:
            for lbl in self._labels.values():
                lbl.setText(lbl.text().split(":")[0] + ": —")
            return
        try:
            t = self._stm.temperature.read()
        except Exception:
            return
        if t.stm is not None:
            self._labels["stm"].setText(f"STM: {t.stm:.2f} K")
        if t.cryo_4K is not None:
            self._labels["cryo_4K"].setText(f"4K: {t.cryo_4K:.2f} K")
        if t.cryo_1K is not None:
            self._labels["cryo_1K"].setText(f"1K: {t.cryo_1K:.2f} K")
