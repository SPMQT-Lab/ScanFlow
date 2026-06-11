"""ScanFlow main window — focused tabbed GUI.

Sweep tab orchestrates bias / current ramps with tip-crash safety. Log
tab shows running events. Live monitoring of the scan itself happens
in the manufacturer's STMAFM window.
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
from scanflow.gui.panels.mosaic_panel import MosaicPanel
# PreviewWindow is NOT imported here: its module pulls in the ProbeFlow
# analysis stack, which is an optional install on the lab PC. It is
# imported lazily in _ensure_preview_window() so the GUI (Sweep, Survey,
# Mosaic, Log, …) launches without it and the Preview button explains
# what to install instead of the whole app failing at startup.
from scanflow.gui.panels.z_stability_panel import ZStabilityPanel
from scanflow.gui.panels.positioning_diag_panel import PositioningDiagPanel
from scanflow.gui.panels.temperature_panel import TemperaturePanel
from scanflow.gui.panels.atom_tracker_panel import AtomTrackerPanel
from scanflow.gui.panels.tipform_panel import TipFormPanel
from scanflow.gui.panels.log_panel import LogPanel
from scanflow.core.z_monitor import ZMonitor
from scanflow.core.temp_poller import TemperaturePoller
from scanflow.gui import theme as _theme

_LOGO = Path(__file__).parents[2] / "Logo.png"

# Status-dot colors — bright enough to read on the dark-blue status bar
# in both themes.
_STATUS_GREEN = "#4ADE80"
_STATUS_AMBER = "#F5A800"
_STATUS_RED = "#F87171"


class MainWindow(QMainWindow):
    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
        self._stm = STMClient()
        self._dark_mode = session.theme == "dark"

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

        self._connect_action = QAction("Connect STM", self)
        self._connect_action.setShortcut("F5")
        self._connect_action.setToolTip(
            "Connect to the Createc STMAFM software running on this PC (F5)"
        )
        self._connect_action.triggered.connect(self._connect_stm)
        toolbar.addAction(self._connect_action)

        self._mock_action = QAction("Connect Mock", self)
        self._mock_action.setToolTip(
            "Offline simulation — try recipes and panels without hardware"
        )
        self._mock_action.triggered.connect(self._connect_mock)
        toolbar.addAction(self._mock_action)

        self._disconnect_action = QAction("Disconnect", self)
        self._disconnect_action.setToolTip("Release the STMAFM connection")
        self._disconnect_action.triggered.connect(self._disconnect_stm)
        self._disconnect_action.setEnabled(False)
        toolbar.addAction(self._disconnect_action)

        toolbar.addSeparator()

        self._theme_action = QAction(
            "Day Mode" if self._dark_mode else "Night Mode", self
        )
        self._theme_action.setShortcut("Ctrl+T")
        self._theme_action.setToolTip(
            "Toggle dark theme (Ctrl+T) — remembered for next session"
        )
        self._theme_action.triggered.connect(self._toggle_theme)
        toolbar.addAction(self._theme_action)

        self._preview_action = QAction("Preview", self)
        self._preview_action.setShortcut("Ctrl+P")
        self._preview_action.setToolTip(
            "Open the scan preview / analysis window (Ctrl+P)"
        )
        self._preview_action.triggered.connect(self._show_preview_window)
        toolbar.addAction(self._preview_action)

        # -- Tabs --
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)

        self._sweep = SweepPanel(self._stm)
        self._mosaic = MosaicPanel(self._stm)
        self._preview_window = None  # created on demand — _ensure_preview_window()
        self._last_scan_path: str | None = None
        self._positioning_diag = PositioningDiagPanel(self._stm)
        # Z-stability monitor runs continuously; it auto-skips polls while
        # disconnected, so it's safe to construct + start before Connect.
        self._z_monitor = ZMonitor(self._stm, interval_s=1.0)
        self._z_stability = ZStabilityPanel(self._z_monitor)
        self._z_monitor.start()
        self._temp_poller = TemperaturePoller(self._stm, poll_interval_s=30.0)
        self._temperature = TemperaturePanel(self._temp_poller)
        self._temp_poller.start()
        self._atom_tracker = AtomTrackerPanel(self._stm)
        # Tip Form panel: supervisor-sanctioned tab; adds ZERO pollers
        # (COM only on explicit button clicks) per ROADMAP §2.5/§6.
        self._tipform = TipFormPanel(self._stm)
        self._log = LogPanel()

        self._tabs.addTab(self._sweep, "Sweep")
        self._tabs.addTab(self._mosaic, "Mosaic")
        self._tabs.addTab(self._positioning_diag, "Positioning Test")
        self._tabs.addTab(self._z_stability, "Z Stability")
        self._tabs.addTab(self._temperature, "Temperature")
        self._tabs.addTab(self._atom_tracker, "Atom Tracker")
        self._tabs.addTab(self._tipform, "Tip Form")
        self._tabs.addTab(self._log, "Log")

        self._sweep.log_message.connect(self._log.append)
        self._sweep.error_message.connect(self._log.append_error)
        self._mosaic.log_message.connect(self._log.append)
        self._mosaic.error_message.connect(self._log.append_error)
        self._positioning_diag.log_message.connect(self._log.append)
        self._z_stability.log_message.connect(self._log.append)
        self._temperature.log_message.connect(self._log.append)
        self._atom_tracker.log_message.connect(self._log.append)
        self._atom_tracker.error_message.connect(self._log.append_error)
        self._tipform.log_message.connect(self._log.append)
        self._tipform.error_message.connect(self._log.append_error)

        self._sweep.scan_completed.connect(self._forward_scan_to_preview)
        self._mosaic.scan_completed.connect(self._forward_scan_to_preview)
        self._sweep.scan_completed.connect(
            lambda _: self._atom_tracker.handle_scan_completed()
        )

        self._log_tab_index = self._tabs.indexOf(self._log)
        self._log.popout_clicked.connect(self._toggle_log_window)
        self._log.floating_closed.connect(self._dock_log_tab)

        # -- Status bar --
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._stm_label = QLabel()
        self._status_bar.addPermanentWidget(self._stm_label)
        self._set_stm_status("disconnected", _STATUS_RED)

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._refresh_status)
        self._poll_timer.start(3000)
        # Cache for the scanning-status detail so the 3 s poll doesn't do
        # ~10 COM reads while an automation worker is hammering the same
        # STMAFM server (COM calls are serialised across apartments).
        self._status_detail = ""
        self._status_detail_t = 0.0

    # ------------------------------------------------------------------

    def _set_stm_status(self, text: str, color: str) -> None:
        self._stm_label.setText(f"● STM: {text}")
        self._stm_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _connect_stm(self) -> None:
        ok = self._stm.connect()
        if ok:
            self._set_stm_status("connected", _STATUS_GREEN)
            self._disconnect_action.setEnabled(True)
            self._log.append("Connected to STMAFM")
            self._status_bar.showMessage("Connected to STMAFM", 5000)
            self._sweep.load_from_stm()
            self._temp_poller.poll_now()
        else:
            self._set_stm_status("disconnected", _STATUS_RED)
            self._log.append_error("STM connection failed")
            QMessageBox.critical(
                self, "Connection Failed",
                "Could not connect to the CreaTec STM.\n\n"
                "Make sure the STMAFM software is running on this PC. "
                "ScanFlow stays in offline mode otherwise.",
            )

    def _connect_mock(self) -> None:
        if self._stm.connect_mock():
            self._set_stm_status("mock", _STATUS_AMBER)
            self._disconnect_action.setEnabled(True)
            self._log.append("Mock STM connected (offline simulation)")
            self._status_bar.showMessage("Mock STM connected", 5000)
            self._temp_poller.poll_now()

    def _disconnect_stm(self) -> None:
        self._stm.disconnect()
        self._set_stm_status("disconnected", _STATUS_RED)
        self._disconnect_action.setEnabled(False)
        self._log.append("STM disconnected")

    def _refresh_status(self) -> None:
        if self._stm.is_mock:
            self._set_stm_status("mock", _STATUS_AMBER)
            return
        connected = self._stm.connected
        if not connected:
            self._set_stm_status("disconnected", _STATUS_RED)
            self._disconnect_action.setEnabled(False)
            return
        self._disconnect_action.setEnabled(True)
        try:
            running = self._stm.scan.is_running
            if running:
                # Refresh the detail (offset + frame size, ~10 getp calls)
                # at most every 15 s; in between reuse the cached string so
                # the GUI poll stays lightweight during automation.
                import time as _time
                now = _time.monotonic()
                if not self._status_detail or now - self._status_detail_t > 15.0:
                    offset = self._stm.scan.get_offset_nm()
                    params = self._stm.scan.read()
                    pos_str = (
                        f"X={offset[0]:+.2f} Y={offset[1]:+.2f} nm"
                        if offset else "pos:n/a"
                    )
                    size_str = (
                        f"{params.size_nm[0]:.1f}×{params.size_nm[1]:.1f} nm"
                        if params.size_nm else ""
                    )
                    self._status_detail = f"{pos_str}  |  {size_str}"
                    self._status_detail_t = now
                self._set_stm_status(
                    f"scanning  |  {self._status_detail}", _STATUS_GREEN
                )
            else:
                self._status_detail = ""
                self._set_stm_status("connected", _STATUS_GREEN)
        except Exception:
            self._set_stm_status("connected", _STATUS_GREEN)

    def _toggle_theme(self) -> None:
        self._dark_mode = not self._dark_mode
        app = QApplication.instance()
        if self._dark_mode:
            app.setStyleSheet(_theme.DARK_STYLESHEET)
            self._theme_action.setText("Day Mode")
        else:
            app.setStyleSheet(_theme.LIGHT_STYLESHEET)
            self._theme_action.setText("Night Mode")
        # Persist immediately so the choice survives even if the app is
        # killed instead of closed.
        self._session.theme = "dark" if self._dark_mode else "light"
        try:
            self._session.save()
        except OSError:
            pass

    def _forward_scan_to_preview(self, path: str) -> None:
        """Route completed scans to the preview window if it exists.

        The newest path is remembered either way, so a preview window
        opened later starts on the latest scan instead of empty.
        """
        self._last_scan_path = path
        if self._preview_window is not None:
            self._preview_window.handle_scan_completed(path)

    def _ensure_preview_window(self):
        """Create the PreviewWindow on first use; None if deps are missing."""
        if self._preview_window is not None:
            return self._preview_window
        try:
            from scanflow.gui.panels.preview_panel import PreviewWindow
        except ImportError as exc:
            self._log.append_error(f"Preview unavailable: {exc}")
            QMessageBox.warning(
                self, "Preview unavailable",
                "The Preview tools need ScanFlow's analysis dependencies "
                "and the ProbeFlow package.\n\n"
                "Install with:\n"
                "    pip install -e \".[analysis]\"\n"
                "and make sure 'probeflow' is importable on this PC.\n\n"
                f"Import error: {exc}",
            )
            return None
        window = PreviewWindow(self._stm, self)
        window.log_message.connect(self._log.append)
        window.error_message.connect(self._log.append_error)
        window.scan_completed.connect(self._log.append)
        self._preview_window = window
        if self._last_scan_path:
            window.handle_scan_completed(self._last_scan_path)
        return window

    def _show_preview_window(self) -> None:
        window = self._ensure_preview_window()
        if window is not None:
            window.show_window()

    # ------------------------------------------------------------------
    # Log pop-out
    # ------------------------------------------------------------------

    def _log_is_floating(self) -> bool:
        return self._tabs.indexOf(self._log) == -1

    def _toggle_log_window(self) -> None:
        if self._log_is_floating():
            self._dock_log_tab()
        else:
            self._float_log_tab()

    def _float_log_tab(self) -> None:
        self._log_tab_index = self._tabs.indexOf(self._log)
        self._tabs.removeTab(self._log_tab_index)
        self._log.set_floating(True)
        self._log.setParent(None)
        self._log.setWindowTitle("ScanFlow — Log")
        if _LOGO.exists():
            self._log.setWindowIcon(QIcon(str(_LOGO)))
        self._log.resize(720, 480)
        self._log.show()
        self._log.raise_()
        self._log.activateWindow()

    def _dock_log_tab(self) -> None:
        self._log.set_floating(False)
        self._tabs.insertTab(self._log_tab_index, self._log, "Log")

    def closeEvent(self, event) -> None:
        # A floating log window must not outlive (or keep alive) the app.
        if self._log_is_floating():
            self._log.set_floating(False)
            self._log.close()
        # Stop any running automation first so Createc receives scan.stop()
        # before the COM apartment is torn down. Without this, Createc keeps
        # scanning indefinitely after ScanFlow exits.
        self._sweep.stop_for_close()
        self._temp_poller.stop()
        self._z_monitor.stop()
        self._session.save()
        super().closeEvent(event)
