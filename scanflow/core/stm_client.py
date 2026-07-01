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
import threading
import time
from typing import Any, Optional

log = logging.getLogger(__name__)


class STMNotConnectedError(RuntimeError):
    pass


def _is_transient_com_error(exc: Exception) -> bool:
    """Best-effort classifier for common transient COM failures."""
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "rpc_e_call_rejected",
        "rpc_e_servercall_retrylater",
        "call rejected",
        "retry later",
        "temporar",
        "timeout",
        "busy",
        "server unavailable",
        "com_error",
    )
    return any(marker in text for marker in markers)


class STMClient:
    """Facade over the CreaTec STMAFM COM object.

    Exposes sub-controllers under attributes that mirror the manufacturer's
    key namespaces: scan, feedback, coarse, lockin, spec, afm, tipform,
    temperature. Each sub-controller calls back into setp/getp.

    Win32 COM proxies are apartment-bound: a proxy obtained on thread A
    raises ``RPC_E_WRONG_THREAD`` ("interface marshalled for a different
    thread") if called from thread B. We therefore keep one proxy *per
    thread* in ``threading.local()`` and expose them through the ``_stm``
    / ``_user`` properties. Worker threads (e.g. ``AutomationRunner``)
    must call ``bind_thread()`` once before issuing setp/getp.
    """

    PROG_ID = "pstmafm.stmafmrem"

    USER_PROG_ID = "pstmafm.stmafmuser"  # crosscorr, getxypos, mtip_*

    _ACTION_KEYS = {
        "STMAFM.BTN.START",
        "STMAFM.BTN.STOP",
        "STMAFM.BEEP",
        "STMAFM.CMD.SETXYOFF.VOLT",
        "STMAFM.CMD.SETXYOFF.IMAGECOORD",
        "HVAMPCOARSE.APPROACH.START",
        "HVAMPCOARSE.APPROACH.STOP",
        "HVAMPCOARSE.CMD.XYZSLIDER",
        "AFM.BTN.FREQSCAN",
        "AFM.BTN.FREQSCAN.RESULTS.APPLY",
        "TIP-FORM.CMD.START",
        "VERTMAN.CMD.SINGLE_SPECTRUM",
        "VERTMAN.CMD.GRID",
        "STMAFM.FILE.SAVE.DAT",
        "STMAFM.FILE.SAVE.VERT",
        "STMAFM.FILE.LOAD.DAT",
    }

    def __init__(self) -> None:
        # Real COM proxies live in thread-local storage (apartment-bound).
        self._local = threading.local()
        # Mock proxies are process-wide and internally thread-safe.
        self._mock_stm: Any = None
        self._mock_user: Any = None
        self._is_mock = False
        # Sub-controllers are created lazily to avoid circular imports
        from .scan import ScanController
        from .feedback import FeedbackController
        from .coarse import CoarseController
        from .lockin import LockInController
        from .spectroscopy import SpectroscopyController
        from .afm import AFMController
        from .tipform import TipFormController
        from .temperature import TemperatureMonitor
        from .lateral import LateralController
        from .events import EventBridge

        self.scan = ScanController(self)
        self.feedback = FeedbackController(self)
        self.coarse = CoarseController(self)
        self.lockin = LockInController(self)
        self.spec = SpectroscopyController(self)
        self.afm = AFMController(self)
        self.tipform = TipFormController(self)
        self.temperature = TemperatureMonitor(self)
        self.lateral = LateralController(self)
        self.events = EventBridge()

    # ------------------------------------------------------------------
    # Thread-local proxy access
    # ------------------------------------------------------------------

    @property
    def _stm(self) -> Any:
        if self._is_mock:
            return self._mock_stm
        return getattr(self._local, "stm", None)

    @_stm.setter
    def _stm(self, value: Any) -> None:
        if self._is_mock:
            self._mock_stm = value
        else:
            self._local.stm = value

    @property
    def _user(self) -> Any:
        if self._is_mock:
            return self._mock_user
        return getattr(self._local, "user", None)

    @_user.setter
    def _user(self, value: Any) -> None:
        if self._is_mock:
            self._mock_user = value
        else:
            self._local.user = value

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Attempt to attach to a running STMAFM session. Returns True on success."""
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            self._is_mock = False
            self._local.stm = win32com.client.Dispatch(self.PROG_ID)
            # Probe: a successful setp/getp confirms the link
            status = self._local.stm.getp("STMAFM.SCANSTATUS", "")
            log.info("Connected to STMAFM (scanstatus=%s)", status)
            self._enable_dst_protection()
            # Best-effort secondary dispatch for user-extension methods
            try:
                self._local.user = win32com.client.Dispatch(self.USER_PROG_ID)
            except Exception as e:
                log.info("User dispatch unavailable: %s", e)
                self._local.user = None
            # Best-effort event subscription (sink lives on this thread's apartment)
            self.events.attach()
            return True
        except Exception as e:
            log.warning("Could not connect to STMAFM: %s", e)
            self._local.stm = None
            self._local.user = None
            return False

    def connect_mock(self) -> bool:
        """Attach to an in-process MockDispatch — works on any OS."""
        from .mock_dispatch import MockDispatch, MockUserDispatch
        self._is_mock = True
        self._mock_stm = MockDispatch()
        self._mock_user = MockUserDispatch()
        log.info("Connected to MockSTM (offline simulation)")
        return True

    def bind_thread(self) -> bool:
        """Dispatch this thread's own COM proxy.

        Must be called from any worker thread (e.g. ``AutomationRunner``)
        before issuing setp/getp. Win32 COM proxies are apartment-bound, so
        the proxy created during ``connect()`` on the GUI thread cannot be
        called from elsewhere — doing so raises ``RPC_E_WRONG_THREAD``.

        Mock mode is a no-op (mock dispatch is thread-safe).
        """
        if self._is_mock:
            return True
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            self._local.stm = win32com.client.Dispatch(self.PROG_ID)
            try:
                self._local.user = win32com.client.Dispatch(self.USER_PROG_ID)
            except Exception:
                self._local.user = None
            log.info("STM bound on thread %s",
                     threading.current_thread().name)
            return True
        except Exception as e:
            log.warning("Could not bind STM on thread %s: %s",
                        threading.current_thread().name, e)
            self._local.stm = None
            self._local.user = None
            return False

    def unbind_thread(self) -> None:
        """Release this thread's COM proxies and uninitialize COM.

        Counterpart to :meth:`bind_thread`. Safe to call unconditionally —
        no-op in mock mode and tolerant of repeat calls.

        The 250 ms pre-uninit sleep lets any in-flight Createc COM events
        targeting our proxy drain before we tear the apartment down. Without
        it, STMAFM occasionally crashes when its post-scan-stop bookkeeping
        tries to invoke our just-released proxy.
        """
        if self._is_mock:
            return
        self._local.stm = None
        self._local.user = None
        try:
            import pythoncom
            import time
            time.sleep(0.25)
            pythoncom.CoUninitialize()
        except Exception:
            pass

    @property
    def is_mock(self) -> bool:
        return getattr(self, "_is_mock", False)

    def disconnect(self) -> None:
        # events.detach() makes a COM call on the sink — only safe on the
        # thread that attached it (typically the GUI thread).
        self.events.detach()
        if self._is_mock:
            self._mock_stm = None
            self._mock_user = None
        else:
            self._local.stm = None
            self._local.user = None
        self._is_mock = False

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
            current = threading.current_thread().name
            raise STMNotConnectedError(
                f"STM not connected on thread '{current}' — "
                "call STMClient.connect() (GUI thread) or "
                "STMClient.bind_thread() (worker thread) before setp/getp"
            )

    # ------------------------------------------------------------------
    # Low-level wrappers around setp/getp
    # ------------------------------------------------------------------

    def setp(self, key: str, value: Any) -> None:
        """Set a CreaTec parameter by structured key.

        Idempotent parameter writes get one transient-COM retry. Physical
        action commands (start/stop/move/pulse/save) are single-shot by
        default so a retry cannot duplicate an instrument action.
        """
        self._require()
        attempts = 1 if self._is_physical_action_key(key) else 2
        self._call_with_retry(lambda: self._stm.setp(key, value), attempts=attempts)

    def setp_idempotent(self, key: str, value: Any) -> None:
        """Set a parameter with retry even when the key is command-like.

        Use only when the caller has established the operation is idempotent
        on the current Createc API path.
        """
        self._require()
        self._call_with_retry(lambda: self._stm.setp(key, value), attempts=2)

    def getp(self, key: str, default: Any = "") -> Any:
        """Read a CreaTec parameter by structured key."""
        self._require()
        return self._call_with_retry(lambda: self._stm.getp(key, default), attempts=2)

    def getp_retry(self, key: str, default: Any = "") -> Any:
        """Explicit retrying read helper for call sites that want the policy visible."""
        return self.getp(key, default)

    @classmethod
    def _is_physical_action_key(cls, key: str) -> bool:
        if key in cls._ACTION_KEYS:
            return True
        return key.startswith(("TIP-FORM.CMD.", "HVAMPCOARSE.CMD.", "VERTMAN.CMD."))

    @staticmethod
    def _call_with_retry(fn, *, attempts: int, backoff_s: float = 0.05):
        attempts = max(int(attempts), 1)
        last = None
        for i in range(attempts):
            try:
                return fn()
            except Exception as exc:
                last = exc
                if i >= attempts - 1 or not _is_transient_com_error(exc):
                    raise
                log.debug("Transient COM call failure; retrying (%d/%d): %s",
                          i + 1, attempts, exc)
                time.sleep(backoff_s * (i + 1))
        raise last  # pragma: no cover - loop always returns or raises

    # ------------------------------------------------------------------
    # Direct COM passthrough for legacy operations not yet wrapped
    # ------------------------------------------------------------------

    @property
    def raw(self) -> Any:
        """Return the underlying COM object for advanced/legacy calls."""
        self._require()
        return self._stm

    @property
    def user(self) -> Any:
        """Return the ``pstmafm.stmafmuser`` COM object (crosscorr, getxypos, …).

        Returns None if the secondary dispatch couldn't be acquired —
        callers must guard accordingly.
        """
        return self._user

    def crosscorr(self) -> Optional[Any]:
        """Trigger the manufacturer's own cross-correlation drift detection.

        Returns whatever the COM call returns (typically a tuple of shifts)
        or None if the user dispatch is unavailable.
        """
        if self._user is None:
            return None
        try:
            return self._user.crosscorr()
        except Exception as e:
            log.warning("crosscorr() failed: %s", e)
            return None

    def tip_xy_position(self) -> Optional[tuple[float, float]]:
        """Read the current tip XY position in nanometres.

        Tries the primary-COM ``SCAN.OFFSET.{X,Y}.NM`` keys first — those
        match STMAFM's displayed offset and work on every rig we've
        tested. Falls back to the secondary ``pstmafm.stmafmuser`` COM's
        ``getxypos()`` when the primary path returns nothing (some older
        Createc versions only exposed it that way).
        """
        # Primary path — getp keys
        try:
            x = self.getp("SCAN.OFFSET.X.NM", "")
            y = self.getp("SCAN.OFFSET.Y.NM", "")
            if x not in (None, "") and y not in (None, ""):
                return (float(x), float(y))
        except Exception:
            pass
        # Fallback — secondary COM dispatch
        if self._user is None:
            return None
        try:
            result = self._user.getxypos()
            if result is None:
                return None
            return (float(result[0]), float(result[1]))
        except Exception:
            return None

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
