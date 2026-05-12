"""ScanFlow main window — focused, two-tab GUI.

Sweep tab orchestrates bias / current ramps with drift correction and
tip-crash safety. Log tab shows running events. Live monitoring of the
scan itself happens in the manufacturer's STMAFM window.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QLabel,
    QToolBar, QMessageBox, QApplication,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QPixmap

from scanflow.core import STMClient
from scanflow.io import Session
from scanflow.gui.panels.sweep_panel import SweepPanel
from scanflow.gui.panels.log_panel import LogPanel
from scanflow.gui import theme as _theme

_LOGO = Path(__file__).parents[2] / "Logo.png"


class MainWindow(QMainWindow):
    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
        self._stm = STMClient()
        self._dark_mode = False

        self.setWindowTitle("ScanFlow")
        self.resize(900, 720)
        if _LOGO.exists():
            self.setWindowIcon(QIcon(str(_LOGO)))

        # -- Toolbar --
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(toolbar)

        if _LOGO.exists():
            logo_label = QLabel()
            pix = QPixmap(str(_LOGO)).scaledToHeight(
                36, Qt.TransformationMode.SmoothTransformation
            )
            logo_label.setPixmap(pix)
            logo_label.setContentsMargins(4, 0, 12, 0)
            toolbar.addWidget(logo_label)

        connect_action = QAction("Connect STM", self)
        connect_action.triggered.connect(self._connect_stm)
        toolbar.addAction(connect_action)

        mock_action = QAction("Connect Mock", self)
        mock_action.triggered.connect(self._connect_mock)
        toolbar.addAction(mock_action)

        disconnect_action = QAction("Disconnect", self)
        disconnect_action.triggered.connect(self._disconnect_stm)
        toolbar.addAction(disconnect_action)

        toolbar.addSeparator()

        self._theme_action = QAction("Night Mode", self)
        self._theme_action.triggered.connect(self._toggle_theme)
        toolbar.addAction(self._theme_action)

        # -- Tabs --
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)

        self._sweep = SweepPanel(self._stm)
        self._log = LogPanel()

        self._tabs.addTab(self._sweep, "Sweep")
        self._tabs.addTab(self._log, "Log")

        self._sweep.log_message.connect(self._log.append)
        self._sweep.error_message.connect(self._log.append_error)

        # -- Status bar --
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._stm_label = QLabel("STM: disconnected")
        self._status_bar.addPermanentWidget(self._stm_label)

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._refresh_status)
        self._poll_timer.start(3000)

    # ------------------------------------------------------------------

    def _connect_stm(self) -> None:
        ok = self._stm.connect()
        if ok:
            self._stm_label.setText("STM: connected")
            self._log.append("Connected to STMAFM")
            QMessageBox.information(
                self, "STM Connected",
                "Successfully connected to the CreaTec STMAFM software."
            )
        else:
            self._stm_label.setText("STM: disconnected")
            self._log.append_error("STM connection failed")
            QMessageBox.critical(
                self, "Connection Failed",
                "Could not connect to the CreaTec STM.\n\n"
                "Make sure the STMAFM software is running on this PC. "
                "ScanFlow stays in offline mode otherwise.",
            )

    def _connect_mock(self) -> None:
        if self._stm.connect_mock():
            self._stm_label.setText("STM: mock")
            self._log.append("Mock STM connected (offline simulation)")

    def _disconnect_stm(self) -> None:
        self._stm.disconnect()
        self._stm_label.setText("STM: disconnected")
        self._log.append("STM disconnected")

    def _refresh_status(self) -> None:
        if self._stm.is_mock:
            self._stm_label.setText("STM: mock")
            return
        connected = self._stm.connected
        self._stm_label.setText(f"STM: {'connected' if connected else 'disconnected'}")

    def _toggle_theme(self) -> None:
        self._dark_mode = not self._dark_mode
        app = QApplication.instance()
        if self._dark_mode:
            app.setStyleSheet(_theme.DARK_STYLESHEET)
            self._theme_action.setText("Day Mode")
        else:
            app.setStyleSheet(_theme.LIGHT_STYLESHEET)
            self._theme_action.setText("Night Mode")

    def closeEvent(self, event) -> None:
        self._session.save()
        super().closeEvent(event)
