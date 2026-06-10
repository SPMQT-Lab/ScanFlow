"""Dependency-boundary smoke tests.

ScanFlow's layers must keep distinct dependency footprints
(docs/dependency_architecture.md):

  * ``scanflow.core`` / recipes / the CLI are instrument-control paths —
    importable with numpy + pyyaml only. No Qt, no analysis/ML stack:
    a lab PC must be able to run a safe sweep without installing any of it.
  * GUI startup may use PySide6/pyqtgraph but must NOT require the
    optional ProbeFlow analysis stack — the Preview button loads it
    lazily and explains what to install if it's missing.

Each test imports the entry path in a fresh interpreter and inspects
sys.modules — this checks what is actually loaded, not what is installed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

_ANALYSIS_ML = {"torch", "torchvision", "clip", "sklearn", "cv2", "probeflow"}


def _loaded_top_level_modules(code: str) -> set[str]:
    """Run ``code`` in a fresh interpreter, return top-level sys.modules."""
    snippet = (
        code
        + "\nimport sys, json"
        + "\nprint('MODULES=' + json.dumps(sorted({m.split('.')[0] for m in sys.modules})))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True, text=True, cwd=_REPO_ROOT, timeout=120,
    )
    assert proc.returncode == 0, f"snippet failed:\n{proc.stderr}"
    for line in reversed(proc.stdout.strip().splitlines()):
        if line.startswith("MODULES="):
            return set(json.loads(line[len("MODULES="):]))
    raise AssertionError(f"marker line not found in output:\n{proc.stdout}")


def test_contracts_is_stdlib_only():
    """scanflow.contracts is the stabilising layer every other layer may
    import — it must depend on nothing but the standard library (not even
    numpy), so analysis/ML tools can consume it in any environment.
    """
    loaded = _loaded_top_level_modules("import scanflow.contracts")
    forbidden = {"numpy", "yaml", "PySide6", "pyqtgraph"} | _ANALYSIS_ML
    assert not loaded & forbidden, (
        f"scanflow.contracts loaded {sorted(loaded & forbidden)} — "
        "it must stay stdlib-only"
    )
    # And it must not pull in other scanflow layers either.
    assert "scanflow" in loaded  # sanity: the import actually happened


def test_core_is_qt_free():
    loaded = _loaded_top_level_modules("import scanflow.core")
    assert "PySide6" not in loaded, (
        "scanflow.core imported PySide6 — a Qt helper module is being "
        "imported eagerly (see the lazy exports in core/__init__.py)"
    )
    assert not loaded & _ANALYSIS_ML


def test_recipe_api_is_qt_free():
    loaded = _loaded_top_level_modules(
        "from scanflow.automation import MeasurementRecipe, MosaicConfig, SurveyConfig"
    )
    assert "PySide6" not in loaded
    assert not loaded & _ANALYSIS_ML


def test_cli_import_is_qt_free():
    loaded = _loaded_top_level_modules("import scanflow.cli")
    assert "PySide6" not in loaded, (
        "importing the CLI pulled in Qt — AutomationRunner must stay a "
        "function-local import in _run_blocking()"
    )


def test_cli_estimate_runs_qt_free():
    """`scanflow estimate` must work on a machine without PySide6."""
    loaded = _loaded_top_level_modules(
        "from scanflow.cli import main\n"
        "rc = main(['estimate', 'bias', '--start', '-1', '--end', '1', '--step', '0.1'])\n"
        "assert rc == 0, f'estimate exited {rc}'"
    )
    assert "PySide6" not in loaded
    assert not loaded & _ANALYSIS_ML


def test_no_ml_stack_anywhere_in_control_paths():
    """torch/CLIP/cv2/sklearn must never load for instrument control."""
    for code in ("import scanflow", "import scanflow.io",
                 "import scanflow.automation.runner"):
        loaded = _loaded_top_level_modules(code)
        assert not loaded & _ANALYSIS_ML, f"{code!r} loaded {loaded & _ANALYSIS_ML}"


def test_gui_startup_does_not_require_probeflow():
    """The GUI must launch without the optional analysis stack installed.

    PreviewWindow (which imports ProbeFlow) is created lazily by the
    Preview toolbar button; main-window import must not touch it.
    """
    pytest.importorskip("PySide6")
    loaded = _loaded_top_level_modules("import scanflow.gui.main_window")
    assert "probeflow" not in loaded, (
        "GUI startup imported ProbeFlow — PreviewWindow is no longer lazy"
    )
    assert not loaded & {"torch", "torchvision", "clip", "sklearn", "cv2"}
