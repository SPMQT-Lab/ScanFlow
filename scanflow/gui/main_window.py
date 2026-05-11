"""ScanFlow main window."""

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
from scanflow.gui.panels.control_panel import ControlPanel
from scanflow.gui.panels.coarse_panel import CoarsePanel
from scanflow.gui.panels.automation_panel import AutomationPanel
from scanflow.gui.panels.spectroscopy_panel import SpectroscopyPanel
from scanflow.gui.panels.drift_panel import DriftPanel
from scanflow.gui.panels.log_panel import LogPanel
from scanflow.gui.panels.live_view_panel import LiveViewPanel
from scanflow.gui.panels.afm_tuning_panel import AFMTuningPanel
from scanflow.gui.panels.advanced_spec_panel import AdvancedSpecPanel
from scanflow.gui.panels.timespec_panel import TimeSpecPanel
from scanflow.gui.panels.lateral_panel import LateralPanel
from scanflow.gui.widgets.temperature_widget import TemperatureWidget
from scanflow.gui import theme as _theme

_LOGO = Path(__file__).parents[2] / "Logo.png"


class MainWindow(QMainWindow):
    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
        self._stm = STMClient()
        self._dark_mode = False

        self.setWindowTitle("ScanFlow")
        self.resize(1200, 800)
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

        self._coarse = CoarsePanel(self._stm)
        self._control = ControlPanel(self._stm)
        self._live = LiveViewPanel(self._stm)
        self._spec = SpectroscopyPanel(self._stm)
        self._adv_spec = AdvancedSpecPanel(self._stm)
        self._timespec = TimeSpecPanel(self._stm)
        self._lateral = LateralPanel(self._stm)
        self._afm_tuning = AFMTuningPanel(self._stm)
        self._automation = AutomationPanel(self._stm, session)
        self._drift = DriftPanel()
        self._log = LogPanel()

        self._tabs.addTab(self._coarse, "Coarse / Approach")
        self._tabs.addTab(self._control, "Scan Control")
        self._tabs.addTab(self._live, "Live View")
        self._tabs.addTab(self._spec, "Spectroscopy")
        self._tabs.addTab(self._adv_spec, "Multi/Line/Grid Spec")
        self._tabs.addTab(self._timespec, "Time Spec")
        self._tabs.addTab(self._lateral, "Lateral Manip")
        self._tabs.addTab(self._afm_tuning, "AFM Tuning")
        self._tabs.addTab(self._automation, "Automation")
        self._tabs.addTab(self._drift, "Drift Monitor")
        self._tabs.addTab(self._log, "Log")

        # Wire signals
        self._coarse.log_message.connect(self._log.append)
        self._spec.log_message.connect(self._log.append)
        self._live.log_message.connect(self._log.append)
        self._afm_tuning.log_message.connect(self._log.append)
        self._adv_spec.log_message.connect(self._log.append)
        self._timespec.log_message.connect(self._log.append)
        self._lateral.log_message.connect(self._log.append)
        self._automation.runner_drift_measured.connect(self._drift.update_drift)
        self._automation.runner_scan_completed.connect(self._log.append)
        self._automation.runner_error.connect(self._log.append_error)
        self._automation.runner_live_frame.connect(self._live.show_frame)

        # -- Status bar --
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._temp_widget = TemperatureWidget(self._stm)
        self._status_bar.addPermanentWidget(self._temp_widget)
        self._stm_label = QLabel("STM: disconnected")
        self._status_bar.addPermanentWidget(self._stm_label)

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._refresh_status)
        self._poll_timer.start(3000)

    # ------------------------------------------------------------------
    # STM connection
    # ------------------------------------------------------------------

    def _connect_stm(self) -> None:
        ok = self._stm.connect()
        if ok:
            self._stm_label.setText("STM: connected")
            self._log.append("STM connected")
            self._control.refresh()
            QMessageBox.information(
                self, "STM Connected",
                "Successfully connected to the CreaTec STM.\n"
                "Instrument parameters have been refreshed.",
            )
        else:
            self._stm_label.setText("STM: disconnected")
            self._log.append_error("STM connection failed")
            QMessageBox.critical(
                self, "Connection Failed",
                "Could not connect to the CreaTec STM.\n\n"
                "Possible causes:\n"
                "  • The STMAFM software is not running\n"
                "  • This machine is not the STM control PC\n"
                "  • The COM interface is unavailable (Linux/offline)\n\n"
                "ScanFlow will continue in offline mode.",
            )

    def _disconnect_stm(self) -> None:
        self._stm.disconnect()
        self._stm_label.setText("STM: disconnected")
        self._log.append("STM disconnected")

    def _refresh_status(self) -> None:
        connected = self._stm.connected
        self._stm_label.setText(f"STM: {'connected' if connected else 'disconnected'}")

    # ------------------------------------------------------------------
    # Theme toggle
    # ------------------------------------------------------------------

    def _toggle_theme(self) -> None:
        self._dark_mode = not self._dark_mode
        app = QApplication.instance()
        if self._dark_mode:
            app.setStyleSheet(_theme.DARK_STYLESHEET)
            self._theme_action.setText("Day Mode")
        else:
            app.setStyleSheet(_theme.LIGHT_STYLESHEET)
            self._theme_action.setText("Night Mode")

    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._session.save()
        super().closeEvent(event)
