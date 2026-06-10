"""Launch ProbeFlow with a ScanFlow survey manifest pre-loaded.

ScanFlow's Survey panel uses this to hand off polished-image editing and
final PPTX export to ProbeFlow, which is a sibling tool maintained in the
same lab. The two processes are independent — we just spawn ProbeFlow
with a CLI flag that tells it which manifest to open.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import List

log = logging.getLogger(__name__)


def open_survey_in_probeflow(manifest_path: Path) -> bool:
    """Launch ProbeFlow with ``--open-survey <manifest_path>``.

    Tries the ``probeflow`` executable on PATH first, falls back to
    ``python -m probeflow``. Returns True if launch succeeded, False if
    neither route worked (caller can then show a manual-open dialog).
    """
    manifest_path = Path(manifest_path).resolve()
    if not manifest_path.exists():
        log.warning("Manifest does not exist: %s", manifest_path)
        return False

    candidates: List[List[str]] = [
        ["probeflow", "--open-survey", str(manifest_path)],
        [sys.executable, "-m", "probeflow", "--open-survey", str(manifest_path)],
    ]

    for cmd in candidates:
        try:
            subprocess.Popen(cmd, start_new_session=True)
            log.info("Launched ProbeFlow: %s", " ".join(cmd))
            return True
        except (FileNotFoundError, OSError) as e:
            log.debug("Could not launch %s: %s", cmd[0], e)
            continue

    log.warning("ProbeFlow could not be launched — not on PATH and not importable as a module")
    return False
