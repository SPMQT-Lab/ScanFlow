"""Qt-free routine executor building blocks.

These classes intentionally avoid ``QThread`` and Qt signals. They are the
place for routine algorithms as the current ``AutomationRunner`` is slimmed
down into a thread/signal adapter.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from scanflow.core import STMClient, ScanParams, TipMotionManager, MotionResult
from scanflow.io.acquisition_log import AcquisitionLog


StopPredicate = Callable[[], bool]
ProgressCallback = Callable[[str], None]


@dataclass
class ExecutorContext:
    stm: STMClient
    motion: TipMotionManager
    log: AcquisitionLog | None = None
    should_stop: StopPredicate | None = None
    progress: ProgressCallback | None = None

    def stop_requested(self) -> bool:
        return bool(self.should_stop and self.should_stop())

    def emit(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message)

    def event(self, event_type: str, **payload) -> None:
        if self.log is not None:
            self.log.emit(event_type, **payload)


class ScanExecutor:
    """Small, Qt-free wrapper for apply/start/wait/save scan flow."""

    def __init__(self, context: ExecutorContext) -> None:
        self.context = context
        self.active_params: ScanParams | None = None

    def apply(self, params: ScanParams) -> None:
        self.active_params = params
        self.context.stm.scan.apply(params)

    def scan_once(
        self,
        *,
        save_path: Path | str | None = None,
        role: str = "data",
        poll_interval_s: float = 0.25,
    ) -> Path | None:
        scan = self.context.stm.scan
        self.context.event("scan_started", role=role, scan_parameters=self.active_params)
        try:
            scan.start()
        except Exception as exc:
            self.context.event("scan_error", role=role, reason=str(exc))
            return None

        if not self._wait_for_scan_start(scan, poll_interval_s=poll_interval_s):
            try:
                scan.stop()
            except Exception:
                pass
            self.context.event("scan_error", role=role, reason="scan did not start")
            return None

        while scan.is_running:
            if self.context.stop_requested():
                scan.stop()
                return None
            time.sleep(max(0.01, float(poll_interval_s)))
        saved_path: Path | None
        if save_path is None:
            target = str(self.context.stm.raw.savedatfilename)
        else:
            target = str(save_path)
        try:
            scan.save_dat(target)
        except Exception as exc:
            self.context.event("scan_error", role=role, reason=f"save failed: {exc}")
            return None
        saved_path = scan.last_saved_path()
        if saved_path is None:
            saved_path = Path(target)
        self.context.event("scan_completed", role=role, dat_path=str(saved_path))
        return saved_path

    def _wait_for_scan_start(
        self,
        scan,
        *,
        poll_interval_s: float,
    ) -> bool:
        """Wait for the scan to flip into the running state before saving."""
        timeout_s = max(1.0, 3.0 * max(0.01, float(poll_interval_s)))
        deadline = time.monotonic() + timeout_s
        while not scan.is_running:
            if self.context.stop_requested():
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(max(0.01, float(poll_interval_s)))
        return True


class MosaicExecutor:
    """Placeholder for the Qt-free mosaic algorithm extraction."""

    def __init__(self, context: ExecutorContext, scan: ScanExecutor) -> None:
        self.context = context
        self.scan = scan
