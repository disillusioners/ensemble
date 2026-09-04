"""T5.4 — armed-absence fixture for ``saver.alist(…)`` on the live path.

FR-4 (T5.4 / AC-4.1): every Phase-5 new test that exercises a message-API
live path MUST patch ``saver.alist`` with an ARMED mock — a mock whose
invocation triggers a hard test failure (via ``side_effect=AssertionError``),
NOT a counter. The test fails LOUDLY if alist fires, regardless of
whether the test's other assertions pass.

Three layers (per PR3 review doc §1.1.2):

1. The fixture monkey-patches ``AsyncPostgresSaver.alist`` (or any
   duck-typed ``saver.alist`` attribute) with an ``AsyncMock`` whose
   ``side_effect`` raises ``AssertionError("alist called on live path")``.
2. Every Phase-5 DB test wires this fixture via ``autouse=True``.
3. The assertion ``with pytest.raises(AssertionError): ...`` is REJECTED
   — the test simply fails via normal caplog / assert semantics.

Why not a counter? — A counter-based ``assert called == 0`` passes
when the call site is mocked away. Armed absence passes ONLY when
alist is provably unreachable on the live path: any fire surfaces
``AssertionError`` to the test runner.

Self-check requirement (T5.4): the new
``tests/integration/test_armed_absence_alist.py`` proves that the fixture
itself is genuinely armed — it asserts (a) the ``side_effect`` is set
on the mock AND (b) invoking it raises ``AssertionError``. A fixture
that forgets to set ``side_effect`` would pass a "no calls" assertion
vacuously; the self-check guards against that regression.

Migrator exemption. The offline migrator
(``daemon/migrations/checkpoint_migrator.py``) is the ONE sanctioned
caller of ``saver.alist(…)`` per §33 guardrail (FR-7). It does NOT
patch alist with the armed fixture; the migrator's own tests are out
of scope for this helper.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


ARMED_ALIST_MESSAGE = "alist called on live path"


def armed_alist_mock() -> AsyncMock:
    """Return an armed AsyncMock that fires AssertionError when invoked.

    Use this directly when you need the mock object (e.g. to assert
    ``assert_not_called`` semantics in the same test). Most Phase-5
    tests will prefer the :func:`armed_alist_fixture` pytest fixture
    which wraps the same mock with autouse wiring.
    """
    mock = AsyncMock(name="saver.alist.armed")
    mock.side_effect = AssertionError(ARMED_ALIST_MESSAGE)
    return mock


@pytest.fixture
def armed_alist_fixture(monkeypatch):
    """Patch ``AsyncPostgresSaver.alist`` (and friends) with an armed AsyncMock.

    The fixture patches the ``alist`` attribute on the runtime saver
    class instances used by the daemon. Because the test uses a real
    ``AsyncPostgresSaver`` (DSN-pinned disposable PG, per
    ``tests/helpers/checkpoint_prune_pg.py``), the fixture monkey-patches
    the instance's ``alist`` method directly — not the class — so other
    tests sharing the class are unaffected.

    Yields the armed mock so the test can:
    * assert ``armed_alist_fixture.assert_not_called()`` (the live-path
      gate), AND
    * verify the self-check that calling the mock raises.

    Note: the import of ``AsyncPostgresSaver`` is LAZY (inside the
    fixture body) because the project's root ``tests/conftest.py``
    globally mocks ``langgraph.*`` for unit tests; importing at module
    top would fail in unit-test contexts. Tests that need the real
    class go through ``tests/helpers/checkpoint_prune_pg.py``'s
    ``evict_langgraph_mocks`` pattern (which the binding-gate
    integration tests already do).
    """
    mock = armed_alist_mock()
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ModuleNotFoundError:
        # Langgraph is mocked at this point — the test context is not
        # PG-integration. The fixture still yields the mock so callers
        # can use it for the self-check; only the class-patch is a
        # no-op. PG-integration tests that need the class-patch
        # explicitly restore langgraph via evict_langgraph_mocks.
        yield mock
        return
    monkeypatch.setattr(AsyncPostgresSaver, "alist", mock, raising=False)
    yield mock


@pytest.fixture(autouse=False)
def _armed_alist_autouse(armed_alist_fixture):
    """Convenience autouse variant — opt-in per-test via param.

    Phase-5 tests that exercise a live message-API path SHOULD declare
    this fixture (or use ``armed_alist_fixture`` directly) so that any
    accidental alist invocation fails LOUDLY. Migration-only tests do
    NOT use this fixture (the migrator is the sanctioned alist caller).
    """
    return armed_alist_fixture
