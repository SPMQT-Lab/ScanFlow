"""Sweep confirmation dialog with editable step preview and voltage warnings.

Replaces the plain QMessageBox.question("Start now?") with a modal that
shows the full step schedule as an editable table — every per-step
parameter (bias, setpoint, size X/Y, pixel count, scan speed, settle
time) can be tweaked before launching. Two voltage thresholds raise an
explicit confirmation popup on Start:

* ``HIGH_BIAS_V`` — any |bias| above this prompts a 'risky bias'
  confirmation.
* ``LOW_BIAS_V`` — any |bias| below this prompts a 'feedback may struggle'
  confirmation.

Both thresholds are constants here, easy to tune per lab convention.
"""

from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from scanflow.automation import MeasurementRecipe
from scanflow.automation.recipe import ScanStep, format_duration


HIGH_BIAS_V = 1.0     # |V| above this triggers a confirmation popup
LOW_BIAS_V = 0.1      # |V| below this triggers a confirmation popup (skipped in const-height mode)


def categorize_voltages(steps: List[ScanStep]) -> dict:
    """Return {'high': [...], 'low': [...]} for warning banners and dialogs.

    'Low' explicitly excludes const-height steps — at low bias the
    feedback isn't engaged, so the 'feedback may struggle' warning
    doesn't apply.
    """
    high = [s for s in steps if abs(getattr(s, "bias_V", 0.0)) > HIGH_BIAS_V]
    low = [
        s for s in steps
        if abs(getattr(s, "bias_V", 0.0)) < LOW_BIAS_V
        and not getattr(s, "const_height", False)
    ]
    return {"high": high, "low": low}


class SweepConfirmDialog(QDialog):
    """Editable confirmation dialog for a sweep / scan recipe."""

    COLUMNS = [
        "#", "Label", "Bias (V)", "Setpoint (pA)",
        "Size X (nm)", "Size Y (nm)", "Pixels", "Speed (nm/s)", "Settle (s)",
    ]

    def __init__(self, recipe: MeasurementRecipe, parent=None) -> None:
        super().__init__(parent)
        self._recipe = recipe
        self.setWindowTitle(f"Confirm: {recipe.name}")
        self.setMinimumSize(960, 620)
        self._build_ui()

    # ------------------------------------------------------------------

    def _scan_steps(self) -> List[ScanStep]:
        return [s for s in self._recipe.steps
                if getattr(s, "kind", "scan") == "scan"]

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # ── header summary ───────────────────────────────────────────
        scan_steps = self._scan_steps()
        n_steps = len(scan_steps)
        est_s = self._recipe.estimate_duration_s()
        head_text = (
            f"<b>{self._recipe.name}</b><br>"
            f"Scans: <b>{n_steps}</b>   ·   "
            f"Estimated total: <b>{format_duration(est_s)}</b>   ·   "
            f"Drift: {'on' if self._recipe.drift_correction else 'off'}"
            f"{' (fast)' if getattr(self._recipe, 'fast_alignment', False) else ''}   ·   "
            f"Safety threshold: {self._recipe.safety_max_current_A * 1e9:.2f} nA"
        )
        head = QLabel(head_text)
        head.setTextFormat(Qt.RichText)
        head.setStyleSheet("font-size: 11pt;")
        root.addWidget(head)

        # ── voltage warning banner ───────────────────────────────────
        cats = categorize_voltages(scan_steps)
        if cats["high"] or cats["low"]:
            lines = []
            if cats["high"]:
                vs = sorted({abs(s.bias_V) for s in cats["high"]}, reverse=True)
                lines.append(
                    f"⚠ <b>{len(cats['high'])}</b> step(s) with |V| &gt; "
                    f"{HIGH_BIAS_V:.1f} V (max {vs[0]:.2f} V) — risky on "
                    f"fragile samples / tips."
                )
            if cats["low"]:
                vs = sorted({abs(s.bias_V) for s in cats["low"]})
                lines.append(
                    f"⚠ <b>{len(cats['low'])}</b> step(s) with |V| &lt; "
                    f"{LOW_BIAS_V * 1000:.0f} mV (min {vs[0] * 1000:.1f} mV) "
                    f"— feedback may struggle, image contrast may be poor."
                )
            warn = QLabel("<br>".join(lines))
            warn.setTextFormat(Qt.RichText)
            warn.setWordWrap(True)
            warn.setStyleSheet(
                "background-color: #fff8e1; color: #6d4c00; "
                "padding: 8px; border: 1px solid #ffd54f; border-radius: 4px;"
            )
            root.addWidget(warn)

        # ── help text + editable step table ──────────────────────────
        hint = QLabel(
            "<i>Edit any cell to tweak that scan's parameters. "
            "Changes are applied on Start.</i>"
        )
        hint.setTextFormat(Qt.RichText)
        root.addWidget(hint)

        self._table = QTableWidget(len(scan_steps), len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.verticalHeader().setVisible(False)
        for r, step in enumerate(scan_steps):
            self._fill_row(r, step)
        self._table.resizeColumnsToContents()
        root.addWidget(self._table, 1)

        # ── buttons ─────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        start = QPushButton("Start sweep")
        start.setDefault(True)
        start.clicked.connect(self._on_start)
        btn_row.addWidget(start)
        root.addLayout(btn_row)

    def _fill_row(self, row: int, step: ScanStep) -> None:
        def cell(text: str, *, editable: bool = True, highlight: bool = False) -> QTableWidgetItem:
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignCenter)
            if not editable:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setBackground(QBrush(QColor("#f3f3f3")))
            if highlight:
                item.setBackground(QBrush(QColor("#fff3cd")))
            return item

        bias = step.bias_V
        risky = (abs(bias) > HIGH_BIAS_V or
                 (abs(bias) < LOW_BIAS_V and not getattr(step, "const_height", False)))

        self._table.setItem(row, 0, cell(str(row + 1), editable=False))
        self._table.setItem(row, 1, cell(step.label or ""))
        self._table.setItem(row, 2, cell(f"{bias:.4f}", highlight=risky))
        self._table.setItem(row, 3, cell(f"{step.setpoint_A * 1e12:.3f}"))
        self._table.setItem(row, 4, cell(f"{step.size_nm[0]:.2f}"))
        self._table.setItem(row, 5, cell(f"{step.size_nm[1]:.2f}"))
        self._table.setItem(row, 6, cell(str(int(step.pixels[0]))))
        self._table.setItem(row, 7, cell(f"{step.speed_nm_s:.2f}"))
        self._table.setItem(row, 8, cell(f"{step.settling_s:.1f}"))

    # ------------------------------------------------------------------
    # Start handling
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        """Read edited cells back into the recipe, then run voltage prompts."""
        scan_steps = self._scan_steps()
        try:
            for r, step in enumerate(scan_steps):
                step.label = self._table.item(r, 1).text()
                step.bias_V = float(self._table.item(r, 2).text())
                step.setpoint_A = float(self._table.item(r, 3).text()) * 1e-12
                step.size_nm = (
                    float(self._table.item(r, 4).text()),
                    float(self._table.item(r, 5).text()),
                )
                px = int(self._table.item(r, 6).text())
                step.pixels = (px, px)
                step.speed_nm_s = float(self._table.item(r, 7).text())
                step.settling_s = float(self._table.item(r, 8).text())
        except (ValueError, AttributeError) as e:
            QMessageBox.critical(
                self, "Bad value",
                f"Could not parse one of the cells:\n{e}\n\n"
                f"Numbers must be plain decimal (e.g. 0.05, not '0,05' or '50mV').",
            )
            return

        # Voltage confirmation prompts — explicit "are you sure?" for both extremes.
        cats = categorize_voltages(scan_steps)
        if cats["high"]:
            vs = sorted({abs(s.bias_V) for s in cats["high"]}, reverse=True)
            ans = QMessageBox.question(
                self, "High bias confirmation",
                f"<b>{len(cats['high'])}</b> step(s) will scan above ±{HIGH_BIAS_V:.1f} V "
                f"(maximum {vs[0]:.2f} V).<br><br>"
                f"This bias level can damage some sample types or tips. "
                f"Are you sure you want to proceed?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
        if cats["low"]:
            vs = sorted({abs(s.bias_V) for s in cats["low"]})
            ans = QMessageBox.question(
                self, "Low bias confirmation",
                f"<b>{len(cats['low'])}</b> step(s) will scan below ±{LOW_BIAS_V * 1000:.0f} mV "
                f"(minimum {vs[0] * 1000:.1f} mV).<br><br>"
                f"At very low bias the feedback loop may struggle to reach the "
                f"setpoint and image contrast can be poor. "
                f"Are you sure you want to proceed?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return

        self.accept()
