"""Surface ``MeasurementRecipe.validate()`` results before a run starts.

One shared dialog so every panel that launches an AutomationRunner gives
the same pre-flight experience: errors block with an explanation, warnings
ask for explicit confirmation, a clean recipe starts silently.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox


def confirm_recipe_preflight(parent, recipe, *, mode: str) -> bool:
    """Validate ``recipe`` and ask the user about any findings.

    Returns True when the run may proceed. ``mode`` is ``"live"`` or
    ``"mock"`` (some warnings only matter on the real rig).
    """
    issues = recipe.validate(mode=mode)
    errors = [i for i in issues if i.startswith("ERROR")]
    warnings = [i for i in issues if i.startswith("WARNING")]

    if errors:
        QMessageBox.critical(
            parent, "Recipe blocked",
            "This recipe has problems the runner would refuse:\n\n"
            + "\n".join(f"•  {i}" for i in errors + warnings),
        )
        return False

    if warnings:
        ret = QMessageBox.warning(
            parent, "Recipe warnings",
            "\n".join(f"•  {w}" for w in warnings)
            + "\n\nStart anyway?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return ret == QMessageBox.Yes

    return True
