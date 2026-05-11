"""Advanced spectroscopy: multi-point, line, and grid I/V acquisition.

Reuses the I/V table built in the Spectroscopy panel — those settings
are global on the instrument (VERTMAN keys), so configuring them on the
Spectroscopy tab and then running multi-point / line / grid from here
gives a consistent measurement.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import yaml

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QSpinBox, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QFileDialog, QMessageBox, QTabWidget,
)
from PySide6.QtCore import Signal, Qt

from scanflow.core import STMClient


class AdvancedSpecPanel(QWidget):
    """Multi-point, line, and grid spectroscopy in one tab."""

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

        hint = QLabel(
            "Configure I/V table and lock-in on the <b>Spectroscopy</b> tab first — "
            "all modes below use those settings.<br>Pixel coordinates refer to "
            "the most recent scan frame (0,0 = top-left)."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        tabs = QTabWidget()
        tabs.addTab(self._build_multi_tab(), "Multi-point")
        tabs.addTab(self._build_line_tab(), "Line")
        tabs.addTab(self._build_grid_tab(), "Grid")
        root.addWidget(tabs, 1)

    # ── Multi-point ─────────────────────────────────────────────────────

    def _build_multi_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self._multi_table = QTableWidget(0, 2)
        self._multi_table.setHorizontalHeaderLabels(["Pixel X", "Pixel Y"])
        self._multi_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self._multi_table, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add row")
        add_btn.clicked.connect(self._multi_add_row)
        rm_btn = QPushButton("− Remove selected")
        rm_btn.clicked.connect(self._multi_remove_selected)
        clr_btn = QPushButton("Clear")
        clr_btn.clicked.connect(lambda: self._multi_table.setRowCount(0))
        btn_row.addWidget(add_btn)
        btn_row.addWidget(rm_btn)
        btn_row.addWidget(clr_btn)
        btn_row.addStretch(1)
        load_btn = QPushButton("Load YAML…")
        load_btn.clicked.connect(self._multi_load_yaml)
        save_btn = QPushButton("Save YAML…")
        save_btn.clicked.connect(self._multi_save_yaml)
        btn_row.addWidget(load_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        run_btn = QPushButton("Run multi-point spectroscopy")
        run_btn.setProperty("accent", "true")
        run_btn.clicked.connect(self._run_multi)
        layout.addWidget(run_btn)

        # Seed with a couple of example rows
        self._multi_set_positions([(128, 128)])
        return w

    def _multi_add_row(self) -> None:
        row = self._multi_table.rowCount()
        self._multi_table.insertRow(row)
        self._multi_table.setItem(row, 0, QTableWidgetItem("128"))
        self._multi_table.setItem(row, 1, QTableWidgetItem("128"))

    def _multi_remove_selected(self) -> None:
        rows = sorted({i.row() for i in self._multi_table.selectedIndexes()},
                      reverse=True)
        for r in rows:
            self._multi_table.removeRow(r)

    def _multi_positions(self) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        for r in range(self._multi_table.rowCount()):
            try:
                x = int(self._multi_table.item(r, 0).text())
                y = int(self._multi_table.item(r, 1).text())
                out.append((x, y))
            except (AttributeError, ValueError):
                continue
        return out

    def _multi_set_positions(self, positions: List[Tuple[int, int]]) -> None:
        self._multi_table.setRowCount(len(positions))
        for r, (x, y) in enumerate(positions):
            self._multi_table.setItem(r, 0, QTableWidgetItem(str(x)))
            self._multi_table.setItem(r, 1, QTableWidgetItem(str(y)))

    def _multi_load_yaml(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load marker list", "", "YAML (*.yaml *.yml)")
        if not path:
            return
        try:
            data = yaml.safe_load(Path(path).read_text())
            positions = [(int(p[0]), int(p[1])) for p in data.get("positions", [])]
            self._multi_set_positions(positions)
            self.log_message.emit(f"Loaded {len(positions)} marker positions from {path}")
        except Exception as e:
            QMessageBox.critical(self, "Load error", str(e))

    def _multi_save_yaml(self) -> None:
        positions = self._multi_positions()
        if not positions:
            QMessageBox.information(self, "Nothing to save", "Marker table is empty.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save marker list", "markers.yaml", "YAML (*.yaml)")
        if not path:
            return
        try:
            Path(path).write_text(yaml.safe_dump({"positions": positions}, sort_keys=False))
            self.log_message.emit(f"Saved markers to {path}")
        except Exception as e:
            QMessageBox.critical(self, "Save error", str(e))

    def _run_multi(self) -> None:
        if not self._require_connection():
            return
        positions = self._multi_positions()
        if not positions:
            QMessageBox.information(self, "Empty list", "Add at least one position.")
            return
        try:
            self._stm.spec.multi_at_pixels(positions)
            self.log_message.emit(f"Started multi-point spec at {len(positions)} positions")
        except Exception as e:
            QMessageBox.critical(self, "Spec error", str(e))

    # ── Line ────────────────────────────────────────────────────────────

    def _build_line_tab(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)

        g.addWidget(QLabel("Start pixel"), 0, 0)
        g.addWidget(QLabel("X"), 0, 1)
        self._line_x1 = QSpinBox()
        self._line_x1.setRange(0, 8192)
        self._line_x1.setValue(64)
        g.addWidget(self._line_x1, 0, 2)
        g.addWidget(QLabel("Y"), 0, 3)
        self._line_y1 = QSpinBox()
        self._line_y1.setRange(0, 8192)
        self._line_y1.setValue(128)
        g.addWidget(self._line_y1, 0, 4)

        g.addWidget(QLabel("End pixel"), 1, 0)
        g.addWidget(QLabel("X"), 1, 1)
        self._line_x2 = QSpinBox()
        self._line_x2.setRange(0, 8192)
        self._line_x2.setValue(192)
        g.addWidget(self._line_x2, 1, 2)
        g.addWidget(QLabel("Y"), 1, 3)
        self._line_y2 = QSpinBox()
        self._line_y2.setRange(0, 8192)
        self._line_y2.setValue(128)
        g.addWidget(self._line_y2, 1, 4)

        run_btn = QPushButton("Run line spectroscopy")
        run_btn.setProperty("accent", "true")
        run_btn.clicked.connect(self._run_line)
        g.addWidget(run_btn, 2, 0, 1, 5)
        g.setRowStretch(3, 1)
        return w

    def _run_line(self) -> None:
        if not self._require_connection():
            return
        try:
            self._stm.spec.line_between(
                self._line_x1.value(), self._line_y1.value(),
                self._line_x2.value(), self._line_y2.value(),
            )
            self.log_message.emit(
                f"Line spec: ({self._line_x1.value()},{self._line_y1.value()}) → "
                f"({self._line_x2.value()},{self._line_y2.value()})"
            )
        except Exception as e:
            QMessageBox.critical(self, "Line spec error", str(e))

    # ── Grid ────────────────────────────────────────────────────────────

    def _build_grid_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # Mode A — generate-points grid (drives multi-point spec)
        gen = QGroupBox("A — Generated grid (uses multi-point spectroscopy)")
        gg = QGridLayout(gen)
        gg.addWidget(QLabel("Origin X"), 0, 0)
        self._gx0 = QSpinBox(); self._gx0.setRange(0, 8192); self._gx0.setValue(64)
        gg.addWidget(self._gx0, 0, 1)
        gg.addWidget(QLabel("Origin Y"), 0, 2)
        self._gy0 = QSpinBox(); self._gy0.setRange(0, 8192); self._gy0.setValue(64)
        gg.addWidget(self._gy0, 0, 3)
        gg.addWidget(QLabel("Cols"), 1, 0)
        self._gnx = QSpinBox(); self._gnx.setRange(1, 100); self._gnx.setValue(5)
        gg.addWidget(self._gnx, 1, 1)
        gg.addWidget(QLabel("Rows"), 1, 2)
        self._gny = QSpinBox(); self._gny.setRange(1, 100); self._gny.setValue(5)
        gg.addWidget(self._gny, 1, 3)
        gg.addWidget(QLabel("Spacing X (px)"), 2, 0)
        self._gdx = QSpinBox(); self._gdx.setRange(1, 1024); self._gdx.setValue(32)
        gg.addWidget(self._gdx, 2, 1)
        gg.addWidget(QLabel("Spacing Y (px)"), 2, 2)
        self._gdy = QSpinBox(); self._gdy.setRange(1, 1024); self._gdy.setValue(32)
        gg.addWidget(self._gdy, 2, 3)
        gen_btn = QPushButton("Run grid (multi-point)")
        gen_btn.setProperty("accent", "true")
        gen_btn.clicked.connect(self._run_grid_generated)
        gg.addWidget(gen_btn, 3, 0, 1, 4)
        layout.addWidget(gen)

        # Mode B — manufacturer's built-in grid (VERTMAN grid params from native GUI)
        native = QGroupBox("B — Native STMAFM grid (params set in native GUI)")
        ng = QVBoxLayout(native)
        ng.addWidget(QLabel(
            "Configure the grid in the native STMAFM software "
            "(VERTMAN grid dialog), then click below to trigger acquisition."
        ))
        nat_btn = QPushButton("Run native grid")
        nat_btn.clicked.connect(self._run_grid_native)
        ng.addWidget(nat_btn)
        layout.addWidget(native)

        layout.addStretch(1)
        return w

    def _run_grid_generated(self) -> None:
        if not self._require_connection():
            return
        positions = self._stm.spec.build_grid_positions(
            origin_xy=(self._gx0.value(), self._gy0.value()),
            size_xy=(self._gnx.value(), self._gny.value()),
            spacing_xy=(self._gdx.value(), self._gdy.value()),
        )
        try:
            self._stm.spec.multi_at_pixels(positions)
            self.log_message.emit(
                f"Grid spec: {len(positions)} points "
                f"({self._gnx.value()}×{self._gny.value()})"
            )
        except Exception as e:
            QMessageBox.critical(self, "Grid spec error", str(e))

    def _run_grid_native(self) -> None:
        if not self._require_connection():
            return
        try:
            self._stm.spec.grid()
            self.log_message.emit("Triggered native grid spectroscopy")
        except Exception as e:
            QMessageBox.critical(self, "Grid spec error", str(e))

    # ── Common ──────────────────────────────────────────────────────────

    def _require_connection(self) -> bool:
        if not self._stm.connected:
            QMessageBox.warning(self, "STM Not Connected",
                                "Connect to the STM before running spectroscopy.")
            return False
        return True
