"""Shared fixtures for the metrics unit legs (tidier H2 / LOW #23).

THE GATE FIXTURE LIVES HERE — ONCE
-----------------------------------

``daemon/services/metrics_gate.py`` keeps PROCESS-GLOBAL state
(``_managed_enabled``, ``_managed_values``, ``_managed_provider``,
``_refreshing``, ``_refresh_requested_at``) and that state is cleared
ONLY by ``metrics_gate.reset_for_tests()``.  Before this conftest the
reset lived in FIVE hand-copied per-module fixtures — four
byte-identical copies plus a superset in the registry leg — so a flag
added to ``reset_for_tests()`` (e.g. ``_refreshing``) was silently not
cleared by the copies that predated it.

One autouse fixture, one reset path: the env var is unset, the gate is
reset before AND after every test in this directory.  The fixture is
autouse and ORDER-INDEPENDENT: it touches only the metrics gate's own
state, so it cannot couple two tests (and it makes them MORE isolated,
not less).

THE SOCKET DOUBLES LIVE HERE — ONCE
------------------------------------

The ``FakeSocket`` / ``FakeSocketFactory`` doubles are shared by every
listener leg (a module that copies them is a module that will drift).
``fileno()`` raises on purpose: the stdlib selector cannot register
the fake, so the serve loop exits immediately instead of spinning on a
file descriptor that would never be readable.
"""

from __future__ import annotations

import pytest

from daemon.services import metrics_gate


class FakeSocket:
    """A socket stand-in — ``bind``/``listen`` are recorded, not called."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self.bound_to: tuple[str, int] | None = None
        self.listened = False
        self.closed = False
        self.bind_error: Exception | None = None

    def setsockopt(self, *_args: object) -> None:
        return

    def bind(self, address: tuple[str, int]) -> None:
        if self.bind_error is not None:
            raise self.bind_error
        self.bound_to = address

    def getsockname(self) -> tuple[str, int]:
        return (self.host, self.port)

    def listen(self, _backlog: int) -> None:
        self.listened = True

    def close(self) -> None:
        self.closed = True

    def fileno(self) -> int:
        raise OSError("fake socket has no file descriptor")


class FakeSocketFactory:
    """Records the (host, port) the listener asked for."""

    def __init__(self, *, bind_error: Exception | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self.sockets: list[FakeSocket] = []
        self._bind_error = bind_error

    def __call__(self, host: str, port: int) -> FakeSocket:
        self.calls.append((host, port))
        sock = FakeSocket(host=host, port=port)
        if self._bind_error is not None:
            sock.bind_error = self._bind_error
        self.sockets.append(sock)
        return sock


@pytest.fixture
def fake_socket_factory() -> type[FakeSocketFactory]:
    """The socket-factory double (shared — never copy it per module)."""
    return FakeSocketFactory


@pytest.fixture(autouse=True)
def _clean_metrics_gate(monkeypatch: pytest.MonkeyPatch):
    """Every metrics test starts (and ends) from a pristine gate.

    The env var is UNSET and ``metrics_gate.reset_for_tests()`` clears
    the gate's process-global state before AND after the test.  The
    fixture is order-independent: it touches only the metrics gate's
    own state.
    """
    monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
    metrics_gate.reset_for_tests()
    yield
    metrics_gate.reset_for_tests()
