"""On-disk analysis artifacts — the cross-process hand-off files.

Convention: an analysis of ``scan_001.dat`` is written atomically as
``scan_001.analysis.json`` next to the scan and its
``scan_001.scanflow.json`` sidecar. An ML detector running in a separate
environment only needs these helpers (or just the JSON convention) plus
``scanflow.contracts`` — never the control or instrument layers.
"""

from __future__ import annotations

import json
from pathlib import Path

from scanflow.contracts import AnalysisResult

from .acquisition_log import write_json_atomic


def analysis_result_path(dat_path: Path | str) -> Path:
    return Path(dat_path).with_suffix(".analysis.json")


def write_analysis_result(result: AnalysisResult,
                          dat_path: Path | str) -> Path:
    """Atomically write ``result`` next to the scan it analysed."""
    return write_json_atomic(analysis_result_path(dat_path),
                             result.to_payload())


def read_analysis_result(path: Path | str) -> AnalysisResult:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return AnalysisResult.from_payload(payload)
