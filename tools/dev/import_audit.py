#!/usr/bin/env python3
"""Heavy-import audit for ScanFlow — run before merging PRs.

Two checks:

1. Static: which files import heavy packages at module top level
   (col 0) versus lazily inside a function. Top-level heavy imports in
   core/automation/io are dependency-boundary violations — see
   docs/dependency_architecture.md for the layer rules.

2. Runtime: which heavy packages actually land in sys.modules when each
   entry path is imported in a fresh interpreter.

Usage (from the repository root):

    python tools/dev/import_audit.py
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

HEAVY = {
    "torch", "torchvision", "clip", "sklearn", "skimage", "cv2", "PIL",
    "matplotlib", "scipy", "probeflow", "PySide6", "pyqtgraph", "pptx",
}

# What each import path is allowed to load. Keep in sync with
# tests/test_import_boundaries.py (the enforced version of this table).
ENTRY_PATHS = {
    "scanflow": set(),
    "scanflow.core": set(),
    "scanflow.automation": set(),
    "scanflow.cli": set(),
    "scanflow.io": set(),
    "scanflow.gui.main_window": {"PySide6", "pyqtgraph"},
}


def static_audit(root: Path) -> int:
    print("== Static: heavy imports by file (TOP-LEVEL = eager) ==")
    violations = 0
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top, lazy = set(), set()
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            for n in names:
                if n in HEAVY:
                    (top if node.col_offset == 0 else lazy).add(n)
        if top or lazy:
            line = f"  {path}"
            if top:
                line += f"  TOP-LEVEL: {', '.join(sorted(top))}"
            if lazy:
                line += f"  [lazy: {', '.join(sorted(lazy))}]"
            print(line)
        # GUI may be eager; everything else must keep heavy imports lazy
        # (PySide6 in runner/workers/core Qt-helpers is tolerated because
        # those modules are themselves only imported from Qt contexts).
        if top - {"PySide6"} and "gui" not in path.parts:
            print(f"    ^ VIOLATION: non-GUI module eagerly imports {sorted(top - {'PySide6'})}")
            violations += 1
    return violations


def runtime_audit() -> int:
    print("\n== Runtime: heavy modules loaded per entry path ==")
    failures = 0
    for module, allowed in ENTRY_PATHS.items():
        code = (
            f"import importlib, sys, json; importlib.import_module({module!r}); "
            "print(json.dumps(sorted({m.split('.')[0] for m in sys.modules})))"
        )
        proc = subprocess.run([sys.executable, "-c", code],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"  {module:30s} -> IMPORT FAILED:\n{proc.stderr}")
            failures += 1
            continue
        loaded = set(json.loads(proc.stdout.strip().splitlines()[-1])) & HEAVY
        extra = loaded - allowed
        status = "OK" if not extra else f"VIOLATION (unexpected: {', '.join(sorted(extra))})"
        print(f"  {module:30s} -> {', '.join(sorted(loaded)) or '(none)':40s} {status}")
        if extra:
            failures += 1
    return failures


def main() -> int:
    root = Path(__file__).resolve().parents[2] / "scanflow"
    bad = static_audit(root) + runtime_audit()
    if bad:
        print(f"\n{bad} boundary violation(s) — see docs/dependency_architecture.md")
        return 1
    print("\nAll dependency boundaries hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
