"""ScanFlow application entry point."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QLocale
from PySide6.QtGui import QIcon

from scanflow.io import Session
from scanflow.gui.main_window import MainWindow
from scanflow.gui.theme import STYLESHEET

_LOGO = Path(__file__).parents[2] / "Logo.png"


# Default location for the lab PC. Override with the SCANFLOW_LOG_DIR
# environment variable; if neither resolves the file logging just
# silently no-ops (e.g. on a fresh Linux dev box without the folder).
_DEFAULT_LOG_DIR = Path(r"C:\ScanflowMonitor\logs")


def _configure_file_logging() -> Path | None:
    """Send all stdlib logging to a daily rotating file.

    Returns the path actually used, or None if no writable location
    could be set up (no exception is raised — file logging is a
    'best-effort' diagnostic, never blocks the GUI launch).
    """
    log_dir_env = os.environ.get("SCANFLOW_LOG_DIR")
    candidates = []
    if log_dir_env:
        candidates.append(Path(log_dir_env))
    # The Windows-style default only makes sense on Windows. On Linux/Mac
    # Path("C:\\...") becomes a literal folder name, which we don't want.
    if sys.platform.startswith("win"):
        candidates.append(_DEFAULT_LOG_DIR)
    # Final fallback: user's home directory, always writable
    candidates.append(Path.home() / ".scanflow" / "logs")

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d")
            path = candidate / f"scanflow_{stamp}.log"
            # INFO by default — DEBUG was 95% z_monitor heartbeat noise.
            # Set SCANFLOW_LOG_LEVEL=DEBUG when you want the firehose.
            level_name = os.environ.get("SCANFLOW_LOG_LEVEL", "INFO").upper()
            level = getattr(logging, level_name, logging.INFO)

            handler = RotatingFileHandler(
                path, maxBytes=5 * 1024 * 1024, backupCount=5,
                encoding="utf-8",
            )
            handler.setLevel(level)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            root = logging.getLogger()
            root.setLevel(level)
            root.addHandler(handler)
            logging.getLogger(__name__).info(
                "ScanFlow file logging → %s", path,
            )
            return path
        except Exception:
            continue
    return None


def main() -> None:
    log_path = _configure_file_logging()
    session = Session.load()
    app = QApplication.instance() or QApplication(sys.argv)
    # Force period as decimal separator regardless of system locale so that
    # QDoubleSpinBox accepts "0.5" on any platform (German, French, etc. use comma).
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    app.setApplicationName("ScanFlow")
    app.setOrganizationName("SPMQT-Lab")
    app.setStyleSheet(STYLESHEET)
    if _LOGO.exists():
        app.setWindowIcon(QIcon(str(_LOGO)))
    window = MainWindow(session)
    if log_path is not None:
        # Surface the log location in the GUI log so the user knows
        # where to grab the file from.
        window.statusBar().showMessage(f"Log file: {log_path}", 8000)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
