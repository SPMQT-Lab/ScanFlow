"""ScanFlow's main STM client — facade over the CreaTec COM interface.

Uses the modern setp/getp API (STMAFM 2020+) with SI units throughout.
Sub-controllers (scan, feedback, coarse, lockin, spec, afm, tipform,
temperature) group related operations to match the manufacturer's key
namespaces.

On non-Windows systems the client still imports cleanly; any live call
raises STMNotConnectedError so the GUI can run in offline mode.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class STMNotConnectedError(RuntimeError):
    pass


class STMClient:
    """Facade over the CreaTec STMAFM COM object.

    Exposes sub-controllers under attributes that mirror the manufacturer's
    key namespaces: scan, feedback, coarse, lockin, spec, afm, tipform,
    temperature. Each sub-controller calls back into setp/getp.
    """

    PROG_ID = "pstmafm.stmafmrem"

    def __init__(self) -> None:
        self._stm: Any = None
        # Sub-controllers are created lazily to avoid circular imports
        from .scan import ScanController
        from .feedback import FeedbackController
        from .coarse import CoarseController
        from .lockin import LockInController
        from .spectroscopy import SpectroscopyController
        from .afm import AFMController
        from .tipform import TipFormController
        from .temperature import TemperatureMonitor
        from .events import EventBridge

        self.scan = ScanController(self)
        self.feedback = FeedbackController(self)
        self.coarse = CoarseController(self)
        self.lockin = LockInController(self)
        self.spec = SpectroscopyController(self)
        self.afm = AFMController(self)
        self.tipform = TipFormController(self)
        self.temperature = TemperatureMonitor(self)
        self.events = EventBridge()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Attempt to attach to a running STMAFM session. Returns True on success."""
        try:
            import win32com.client
            self._stm = win32com.client.Dispatch(self.PROG_ID)
            # Probe: a successful setp/getp confirms the link
            status = self._stm.getp("STMAFM.SCANSTATUS", "")
            log.info("Connected to STMAFM (scanstatus=%s)", status)
            self._enable_dst_protection()
            # Best-effort event subscription
            self.events.attach()
            return True
        except Exception as e:
            log.warning("Could not connect to STMAFM: %s", e)
            self._stm = None
            return False

    def disconnect(self) -> None:
        self.events.detach()
        self._stm = None

    @property
    def connected(self) -> bool:
        if self._stm is None:
            return False
        try:
            self._stm.getp("STMAFM.SCANSTATUS", "")
            return True
        except Exception:
            return False

    def _require(self) -> None:
        if self._stm is None:
            raise STMNotConnectedError("STM not connected — call connect() first")

    # ------------------------------------------------------------------
    # Low-level wrappers around setp/getp
    # ------------------------------------------------------------------

    def setp(self, key: str, value: Any) -> None:
        """Set a CreaTec parameter by structured key (e.g. 'SCAN.BIASVOLTAGE.VOLT')."""
        self._require()
        self._stm.setp(key, value)

    def getp(self, key: str, default: Any = "") -> Any:
        """Read a CreaTec parameter by structured key."""
        self._require()
        return self._stm.getp(key, default)

    # ------------------------------------------------------------------
    # Direct COM passthrough for legacy operations not yet wrapped
    # ------------------------------------------------------------------

    @property
    def raw(self) -> Any:
        """Return the underlying COM object for advanced/legacy calls."""
        self._require()
        return self._stm

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def beep(self) -> None:
        """Test signal — STMAFM controller plays a beep."""
        self.setp("STMAFM.BEEP", "")

    def _enable_dst_protection(self) -> None:
        """Disable automatic daylight-saving change-over so overnight runs
        do not break filename sequencing (see scan_with_tracking.py warnings).
        """
        try:
            self.setp("Block_DSTime_Change", True)
            log.debug("DST auto-change suppressed for the session")
        except Exception:
            pass
