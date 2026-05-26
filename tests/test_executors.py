from pathlib import Path
from types import SimpleNamespace

from scanflow.automation.executors import ExecutorContext, ScanExecutor


class EventLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, **payload) -> None:
        self.events.append((event_type, payload))


class DelayedStartScan:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.save_calls: list[str] = []
        self._poll_count = 0
        self.started = False
        self.save_before_started = False

    def start(self) -> None:
        self.start_calls += 1
        self._poll_count = 0

    @property
    def is_running(self) -> bool:
        self._poll_count += 1
        if self._poll_count <= 2:
            return False
        if self._poll_count <= 4:
            self.started = True
            return True
        return False

    def stop(self) -> None:
        self.stop_calls += 1

    def save_dat(self, path: str) -> None:
        if not self.started:
            self.save_before_started = True
        self.save_calls.append(path)

    def last_saved_path(self) -> Path | None:
        if not self.save_calls:
            return None
        return Path(self.save_calls[-1])


def test_scan_executor_waits_for_running_before_saving(tmp_path):
    scan = DelayedStartScan()
    stm = SimpleNamespace(
        scan=scan,
        raw=SimpleNamespace(savedatfilename=str(tmp_path / "default.dat")),
    )
    context = ExecutorContext(
        stm=stm,
        motion=SimpleNamespace(),
        log=EventLog(),
        should_stop=lambda: False,
    )
    executor = ScanExecutor(context)

    result = executor.scan_once(poll_interval_s=0.01)

    assert result == tmp_path / "default.dat"
    assert scan.start_calls == 1
    assert scan.save_before_started is False
    assert scan.stop_calls == 0
    assert scan.started is True
    assert context.log.events[0][0] == "scan_started"
    assert context.log.events[-1][0] == "scan_completed"
