from scanflow.core import STMClient


class FlakyDispatch:
    def __init__(self):
        self.set_calls = 0
        self.get_calls = 0
        self.action_calls = 0

    def setp(self, key, value):
        if key == "STMAFM.BTN.START":
            self.action_calls += 1
            raise RuntimeError("RPC_E_CALL_REJECTED")
        self.set_calls += 1
        if self.set_calls == 1:
            raise RuntimeError("RPC_E_CALL_REJECTED")

    def getp(self, key, default=""):
        self.get_calls += 1
        if self.get_calls == 1:
            raise RuntimeError("RPC_E_CALL_REJECTED")
        return "ok"


def _client_with_dispatch(dispatch):
    c = STMClient()
    c._is_mock = True
    c._mock_stm = dispatch
    c._mock_user = None
    return c


def test_setp_retries_idempotent_parameter_write():
    dispatch = FlakyDispatch()
    client = _client_with_dispatch(dispatch)

    client.setp("SCAN.SPEED.NM/SEC", 10.0)

    assert dispatch.set_calls == 2


def test_getp_retries_transient_failure():
    dispatch = FlakyDispatch()
    client = _client_with_dispatch(dispatch)

    assert client.getp("ANY.KEY", "") == "ok"
    assert dispatch.get_calls == 2


def test_physical_action_setp_is_not_retried():
    dispatch = FlakyDispatch()
    client = _client_with_dispatch(dispatch)

    try:
        client.setp("STMAFM.BTN.START", "")
    except RuntimeError:
        pass

    assert dispatch.action_calls == 1

