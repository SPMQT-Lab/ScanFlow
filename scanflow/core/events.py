"""COM event sink for STMAFM.

The CreaTec ``pstmafm.stmafmevent`` CoClass fires the ``event1`` outgoing
event whenever the DSP has internal state to push back (scan finished,
approach finished, data recorder tick, etc.). Subscribing to it lets us
react faster than polling ``STMAFM.SCANSTATUS`` once per second — useful
for short scans and tight automation loops.

Important caveats:

* The hardware fires events on the COM apartment thread, not Qt's GUI
  thread. We funnel callbacks through ``QMetaObject.invokeMethod`` /
  ``Qt.QueuedConnection`` so handlers run on the receiver's thread.

* If the event sink can't be registered (no Windows, no STMAFM running,
  or CoClass not exposed), we silently fall back to polling — the rest
  of ScanFlow keeps working.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)


class _EventSink:
    """Plain-Python receiver bound to ``pstmafm.stmafmevent`` via win32com.

    win32com's ``DispatchWithEvents`` reflectively wires outgoing events
    (named ``event1`` in the type lib) onto methods prefixed with ``On``,
    so the handler method is ``OnEvent1``.
    """

    on_event: Optional[Callable[[], None]] = None  # set by EventBridge

    def OnEvent1(self) -> None:
        try:
            if self.on_event is not None:
                self.on_event()
        except Exception:
            log.exception("Event handler raised")


class EventBridge:
    """Best-effort COM-event subscription with a thread-safe ``flag``.

    Use it as a wake-up signal in polling loops::

        bridge = EventBridge()
        if bridge.attach():
            while not bridge.consume_flag(timeout=1.0):
                ...   # do other work, woken when an event fires

    If ``attach()`` returns False the bridge is inert and ``consume_flag``
    just blocks on the timeout — equivalent to plain polling.
    """

    PROG_ID = "pstmafm.stmafmevent"

    def __init__(self) -> None:
        self._dispatch = None
        self._sink: Optional[_EventSink] = None
        self._event = threading.Event()
        self._user_cb: Optional[Callable[[], None]] = None

    def attach(self) -> bool:
        try:
            import win32com.client
            self._dispatch = win32com.client.DispatchWithEvents(self.PROG_ID, _EventSink)
            self._sink = self._dispatch
            self._dispatch.on_event = self._on_event
            log.info("COM event sink attached: %s", self.PROG_ID)
            return True
        except Exception as e:
            log.info("Event sink unavailable (%s) — falling back to polling", e)
            self._dispatch = None
            self._sink = None
            return False

    def detach(self) -> None:
        if self._dispatch is not None:
            try:
                self._dispatch.close()
            except Exception:
                pass
        self._dispatch = None
        self._sink = None

    @property
    def attached(self) -> bool:
        return self._dispatch is not None

    def set_callback(self, cb: Optional[Callable[[], None]]) -> None:
        """Register a user callback invoked on every event (thread: COM apt)."""
        self._user_cb = cb

    def _on_event(self) -> None:
        self._event.set()
        if self._user_cb is not None:
            try:
                self._user_cb()
            except Exception:
                log.exception("User event callback raised")

    def consume_flag(self, timeout: float = 1.0) -> bool:
        """Wait up to ``timeout`` for an event, then clear and return True.

        Returns False on timeout. When the bridge is not attached, this is
        equivalent to ``time.sleep(timeout)`` returning False.
        """
        if not self.attached:
            self._event.wait(timeout=timeout)
            return False
        triggered = self._event.wait(timeout=timeout)
        if triggered:
            self._event.clear()
        return triggered
