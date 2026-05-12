"""Single entry point: ``python -m scanflow ...``

No arguments       → launch the GUI.
Sub-command given  → run the CLI (bias, current, run, estimate).
"""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) <= 1:
        from scanflow.gui.app import main as gui_main
        gui_main()
        return 0
    from scanflow.cli import main as cli_main
    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
