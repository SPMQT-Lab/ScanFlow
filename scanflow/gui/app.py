"""ScanFlow application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from scanflow.io import Session
from scanflow.gui.main_window import MainWindow
from scanflow.gui.theme import STYLESHEET

_LOGO = Path(__file__).parents[2] / "Logo.png"


def main() -> None:
    session = Session.load()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("ScanFlow")
    app.setOrganizationName("SPMQT-Lab")
    app.setStyleSheet(STYLESHEET)
    if _LOGO.exists():
        app.setWindowIcon(QIcon(str(_LOGO)))
    window = MainWindow(session)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
