"""Tests for the socket instrument."""
import socket
import time
from unittest.mock import patch, MagicMock

import pytest

from because.buffer import OpType, get_context
from because.instruments.socket import instrument, uninstall


@pytest.fixture(autouse=True)
def reset_socket():
    """Ensure each test starts with a clean (unpatched) socket."""
    uninstall()
    yield
    uninstall()


def _tcp_ops():
    return [
        op for op in get_context().snapshot()
        if op.op_type == OpType.HTTP_REQUEST
        and op.metadata.get("kind") == "tcp_connect"
    ]


# ── instrument() ──────────────────────────────────────────────────────────────

def test_instrument_is_idempotent():
    from because.instruments import socket as sock_mod
    instrument()
    patched_once = socket.socket.connect
    instrument()
    # Second call must not wrap again
    assert socket.socket.connect is patched_once
    assert sock_mod._installed is True


def test_instrument_records_successful_connect():
    instrument()
    before = len(_tcp_ops())

    # Use a real loopback connection to localhost echo/discard — or mock it
    with patch("socket.socket.connect", wraps=socket.socket.connect) as _:
        pass  # instrument is already applied

    # Simulate a successful connect via a mock socket
    sock = MagicMock(spec=socket.socket)
    real_connect = socket.socket.connect

    with patch.object(socket.socket, "connect", real_connect):
        # Patch the underlying original to succeed silently
        from because.instruments import socket as sock_mod
        original = sock_mod._original_connect
        with patch.object(sock_mod, "_original_connect", lambda self, addr: None):
            s = socket.socket()
            s.connect(("127.0.0.1", 9999))

    ops = _tcp_ops()
    assert len(ops) == before + 1
    op = ops[-1]
    assert op.success is True
    assert op.metadata["host"] == "127.0.0.1"
    assert op.metadata["port"] == 9999
    assert op.metadata["kind"] == "tcp_connect"


def test_instrument_records_failed_connect():
    instrument()
    before = len(_tcp_ops())

    from because.instruments import socket as sock_mod
    def _refuse(self, addr):
        import errno as errno_mod
        raise ConnectionRefusedError(errno_mod.ECONNREFUSED, "Connection refused")

    with patch.object(sock_mod, "_original_connect", _refuse):
        s = socket.socket()
        with pytest.raises(ConnectionRefusedError):
            s.connect(("127.0.0.1", 9999))

    ops = _tcp_ops()
    assert len(ops) == before + 1
    op = ops[-1]
    assert op.success is False
    assert op.metadata["error"] == "ConnectionRefusedError"
    assert op.metadata["host"] == "127.0.0.1"


def test_instrument_records_connect_ex_success():
    instrument()
    before = len(_tcp_ops())

    from because.instruments import socket as sock_mod
    with patch.object(sock_mod, "_original_connect_ex", lambda self, addr: 0):
        s = socket.socket()
        result = s.connect_ex(("127.0.0.1", 9999))

    assert result == 0
    ops = _tcp_ops()
    assert len(ops) == before + 1
    op = ops[-1]
    assert op.success is True
    assert op.metadata["errno"] == 0


def test_instrument_records_connect_ex_failure():
    instrument()
    before = len(_tcp_ops())
    import errno as errno_mod

    from because.instruments import socket as sock_mod
    with patch.object(sock_mod, "_original_connect_ex", lambda self, addr: errno_mod.ECONNREFUSED):
        s = socket.socket()
        result = s.connect_ex(("192.168.1.1", 9999))

    assert result != 0
    ops = _tcp_ops()
    assert len(ops) == before + 1
    op = ops[-1]
    assert op.success is False


def test_instrument_records_duration():
    instrument()

    from because.instruments import socket as sock_mod
    with patch.object(sock_mod, "_original_connect", lambda self, addr: time.sleep(0.01)):
        s = socket.socket()
        s.connect(("127.0.0.1", 9999))

    op = _tcp_ops()[-1]
    assert op.duration_ms >= 10


# ── uninstall() ───────────────────────────────────────────────────────────────

def test_uninstall_restores_original():
    from because.instruments import socket as sock_mod
    original_connect = sock_mod._original_connect
    instrument()
    assert socket.socket.connect is not original_connect
    uninstall()
    assert socket.socket.connect is original_connect


def test_uninstall_idempotent():
    uninstall()
    uninstall()  # no error
