"""Tests for CorrelationManager shadow-mode hook exception resilience.

Phase 1 (Shadow Mode) safety guarantee:

    "If CM throws an exception, the parent code path continues normally."

The hook helpers ``notify_corr_register`` and ``notify_corr_resolve`` are
fire-and-forget wrappers that MUST swallow any exception raised by the
CorrelationManager. The call sites in
``daemon/tools/instance.py::send_message``,
``daemon/services/child_reports.py``, and
``daemon/services/error_reporting.py`` rely on this guarantee — a CM
failure must NEVER break the main control flow.

These tests verify the guarantee by mocking the CM singleton:

  * Test A: CM raises on ``register_message_send``
            → ``notify_corr_register`` does not propagate.
  * Test B: CM raises on ``resolve_response``
            → ``notify_corr_resolve`` does not propagate.
  * Test C: CM is ``None`` (not wired up) → both helpers are silent no-ops.
  * Bonus:  Various ``Exception`` subclasses are swallowed; the warning
            log is emitted with the expected format.

Run with:

    pytest tests/test_cm_resilience.py -v
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.correlation_manager import (
    STATUS_ERROR,
    STATUS_RESPONDED,
    get_correlation_manager,
    notify_corr_register,
    notify_corr_resolve,
    set_correlation_manager,
)


# =============================================================================
# Helpers
# =============================================================================


def _make_cm_mock(
    *,
    register_raises: Exception | None = None,
    resolve_raises: Exception | None = None,
) -> MagicMock:
    """Build a MagicMock that stands in for the CorrelationManager singleton.

    Pass an Exception instance to ``register_raises`` / ``resolve_raises`` to
    make that method raise on call. Both default to a normal ``AsyncMock``
    that returns ``None`` / ``False``.
    """
    cm = MagicMock(name="CorrelationManagerMock")

    if register_raises is not None:
        cm.register_message_send = AsyncMock(side_effect=register_raises)
    else:
        cm.register_message_send = AsyncMock(return_value=None)

    if resolve_raises is not None:
        cm.resolve_response = AsyncMock(side_effect=resolve_raises)
    else:
        cm.resolve_response = AsyncMock(return_value=False)

    return cm


def _shadow_warning_records(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    """Return WARNING records that match the CM hook log format.

    Phase 3: the hook helpers are no longer "shadow" (observing only) —
    they are the authoritative resolution path. The log message keeps
    the ``"CM hook:"`` prefix as a stable tag for log scraping; the
    ``shadow`` / ``ignored`` tokens were removed when the hooks became
    authoritative.
    """
    return [
        r
        for r in records
        if r.levelno == logging.WARNING
        and "CM hook:" in r.getMessage()
    ]


# =============================================================================
# Fixture: reset CM singleton around every test
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_cm_singleton():
    """Ensure each test starts and ends with CM singleton cleared.

    The module-level ``_correlation_manager`` global in
    ``daemon.services.correlation_manager`` persists across tests; without
    this fixture, state leaks between tests.
    """
    set_correlation_manager(None)
    try:
        yield
    finally:
        set_correlation_manager(None)


# =============================================================================
# Test A — Register hook resilience
# =============================================================================


class TestRegisterHookResilience:
    """``notify_corr_register`` must swallow any CM exception."""

    @pytest.mark.asyncio
    async def test_register_does_not_propagate_when_cm_raises(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """CM.register_message_send raises → hook helper must not raise."""
        cm = _make_cm_mock(
            register_raises=RuntimeError("CM register boom"),
        )
        set_correlation_manager(cm)

        caplog.set_level(logging.WARNING, logger="daemon.services.correlation_manager")

        # The hook helper must swallow the exception — no raise here.
        await notify_corr_register(
            parent_id="parent-1",
            child_id="child-1",
            message_id=str(uuid.uuid4()),
        )

        # The CM method was actually invoked (the hook didn't short-circuit).
        cm.register_message_send.assert_awaited_once()
        # A shadow warning matching the expected format was logged.
        warnings = _shadow_warning_records(caplog.records)
        assert len(warnings) >= 1, (
            "Expected at least one shadow warning from notify_corr_register; "
            f"got records: {[r.getMessage() for r in caplog.records]}"
        )
        assert any("register_message_send" in r.getMessage() for r in warnings)

    @pytest.mark.asyncio
    async def test_register_happy_path_still_works(self) -> None:
        """Sanity: a healthy CM is called exactly once with the right args."""
        cm = _make_cm_mock()
        set_correlation_manager(cm)

        msg_id = str(uuid.uuid4())
        await notify_corr_register(
            parent_id="parent-1",
            child_id="child-1",
            message_id=msg_id,
        )

        cm.register_message_send.assert_awaited_once_with(
            "parent-1", "child-1", msg_id
        )


# =============================================================================
# Test B — Resolve hook resilience
# =============================================================================


class TestResolveHookResilience:
    """``notify_corr_resolve`` must swallow any CM exception."""

    @pytest.mark.asyncio
    async def test_resolve_does_not_propagate_when_cm_raises(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """CM.resolve_response raises → hook helper must not raise."""
        cm = _make_cm_mock(
            resolve_raises=RuntimeError("CM resolve boom"),
        )
        set_correlation_manager(cm)

        caplog.set_level(logging.WARNING, logger="daemon.services.correlation_manager")

        # The hook helper must swallow the exception — no raise here.
        result = await notify_corr_resolve(
            parent_id="parent-1",
            child_id="child-1",
            message_id=str(uuid.uuid4()),
            status=STATUS_RESPONDED,
        )

        # Helper returns None when it short-circuits the exception.
        assert result is None
        # The CM method was actually invoked.
        cm.resolve_response.assert_awaited_once()
        # A shadow warning matching the expected format was logged.
        warnings = _shadow_warning_records(caplog.records)
        assert len(warnings) >= 1, (
            "Expected at least one shadow warning from notify_corr_resolve; "
            f"got records: {[r.getMessage() for r in caplog.records]}"
        )
        assert any("resolve_response" in r.getMessage() for r in warnings)

    @pytest.mark.asyncio
    async def test_resolve_error_status_path_is_resilient(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Resilience also covers status=error (error_reporting.py path)."""
        cm = _make_cm_mock(
            resolve_raises=RuntimeError("CM error-path boom"),
        )
        set_correlation_manager(cm)

        caplog.set_level(logging.WARNING, logger="daemon.services.correlation_manager")

        await notify_corr_resolve(
            parent_id="parent-1",
            child_id="child-1",
            message_id=str(uuid.uuid4()),
            status=STATUS_ERROR,
        )

        cm.resolve_response.assert_awaited_once()
        warnings = _shadow_warning_records(caplog.records)
        assert len(warnings) >= 1
        # Status code must appear in the log for traceability.
        assert any("status=error" in r.getMessage() for r in warnings)

    @pytest.mark.asyncio
    async def test_resolve_happy_path_still_works(self) -> None:
        """Sanity: a healthy CM is called exactly once with the right args."""
        cm = _make_cm_mock()
        set_correlation_manager(cm)

        msg_id = str(uuid.uuid4())
        result = await notify_corr_resolve(
            parent_id="parent-1",
            child_id="child-1",
            message_id=msg_id,
            status=STATUS_RESPONDED,
        )

        cm.resolve_response.assert_awaited_once_with(
            "parent-1", "child-1", msg_id, status=STATUS_RESPONDED
        )
        # The wrapper itself returns None (CM's return value is discarded).
        assert result is None


# =============================================================================
# Test C — CM disabled (None)
# =============================================================================


class TestDisabledCM:
    """When the CM singleton is ``None``, both helpers must be silent no-ops."""

    @pytest.mark.asyncio
    async def test_register_helper_is_silent_when_cm_is_none(self) -> None:
        """No raise; the singleton must remain ``None``."""
        set_correlation_manager(None)
        assert get_correlation_manager() is None

        await notify_corr_register(
            parent_id="orphan-parent",
            child_id="orphan-child",
            message_id="orphan-msg",
        )
        # Singleton still None (we didn't accidentally replace it).
        assert get_correlation_manager() is None

    @pytest.mark.asyncio
    async def test_resolve_helper_is_silent_when_cm_is_none(self) -> None:
        """No raise; returns ``None`` since nothing was awaited."""
        set_correlation_manager(None)
        assert get_correlation_manager() is None

        result = await notify_corr_resolve(
            parent_id="orphan-parent",
            child_id="orphan-child",
            message_id="orphan-msg",
            status=STATUS_RESPONDED,
        )
        assert result is None
        assert get_correlation_manager() is None

    @pytest.mark.asyncio
    async def test_register_does_not_log_when_cm_is_none(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No-warning: the hook should silently short-circuit on CM=None."""
        set_correlation_manager(None)
        caplog.set_level(logging.WARNING, logger="daemon.services.correlation_manager")

        await notify_corr_register(
            parent_id="p", child_id="c", message_id="m"
        )

        # The hook wrapper must not log anything when CM is None — that's
        # the normal, healthy state for production deployments without CM.
        assert _shadow_warning_records(caplog.records) == []

    @pytest.mark.asyncio
    async def test_resolve_does_not_log_when_cm_is_none(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No-warning: the hook should silently short-circuit on CM=None."""
        set_correlation_manager(None)
        caplog.set_level(logging.WARNING, logger="daemon.services.correlation_manager")

        await notify_corr_resolve(
            parent_id="p", child_id="c", message_id="m", status=STATUS_RESPONDED
        )

        assert _shadow_warning_records(caplog.records) == []


# =============================================================================
# Bonus — Broad exception-type coverage
# =============================================================================


# All exceptions subclass ``Exception`` (not ``BaseException``) so they
# are caught by the wrapper's broad ``except Exception`` block.
_EXCEPTION_TYPES: list[type[Exception]] = [
    RuntimeError,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    OSError,
    asyncio.TimeoutError,  # type: ignore[misc]
]


class TestRegisterExceptionTypeCoverage:
    """``notify_corr_register`` must catch any ``Exception`` subclass."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc_cls", _EXCEPTION_TYPES)
    async def test_register_swallows_exception_subclass(
        self,
        caplog: pytest.LogCaptureFixture,
        exc_cls: type[Exception],
    ) -> None:
        """Every ``Exception`` subclass must be caught and logged as a warning."""
        cm = _make_cm_mock(register_raises=exc_cls("test"))
        set_correlation_manager(cm)

        caplog.set_level(logging.WARNING, logger="daemon.services.correlation_manager")

        # Must not raise regardless of the exception class.
        await notify_corr_register(
            parent_id="p", child_id="c", message_id="m"
        )

        cm.register_message_send.assert_awaited_once()
        assert len(_shadow_warning_records(caplog.records)) >= 1


class TestResolveExceptionTypeCoverage:
    """``notify_corr_resolve`` must catch any ``Exception`` subclass."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc_cls", _EXCEPTION_TYPES)
    async def test_resolve_swallows_exception_subclass(
        self,
        caplog: pytest.LogCaptureFixture,
        exc_cls: type[Exception],
    ) -> None:
        """Every ``Exception`` subclass must be caught and logged as a warning."""
        cm = _make_cm_mock(resolve_raises=exc_cls("test"))
        set_correlation_manager(cm)

        caplog.set_level(logging.WARNING, logger="daemon.services.correlation_manager")

        # Must not raise regardless of the exception class.
        await notify_corr_resolve(
            parent_id="p", child_id="c", message_id="m", status=STATUS_RESPONDED
        )

        cm.resolve_response.assert_awaited_once()
        assert len(_shadow_warning_records(caplog.records)) >= 1


# =============================================================================
# Bonus — Concurrency under failure
# =============================================================================


class TestConcurrentFailuresAreIsolated:
    """A CM exception must not leak out of the awaitable even under concurrency."""

    @pytest.mark.asyncio
    async def test_concurrent_register_failures_all_swallowed(self) -> None:
        """50 concurrent notify_corr_register calls — none raise."""
        import asyncio

        cm = _make_cm_mock(
            register_raises=RuntimeError("every call fails")
        )
        set_correlation_manager(cm)

        coros = [
            notify_corr_register(
                parent_id=f"parent-{i}",
                child_id=f"child-{i}",
                message_id=str(uuid.uuid4()),
            )
            for i in range(50)
        ]
        # gather with return_exceptions=True so we can inspect each result;
        # every result must be None (the hook returned cleanly).
        results = await asyncio.gather(*coros, return_exceptions=True)
        assert results == [None] * 50, (
            f"Expected 50 None results, got exceptions: "
            f"{[r for r in results if isinstance(r, Exception)]}"
        )
        # Every call hit the CM.
        assert cm.register_message_send.await_count == 50

    @pytest.mark.asyncio
    async def test_concurrent_resolve_failures_all_swallowed(self) -> None:
        """50 concurrent notify_corr_resolve calls — none raise."""
        import asyncio

        cm = _make_cm_mock(
            resolve_raises=RuntimeError("every call fails")
        )
        set_correlation_manager(cm)

        coros = [
            notify_corr_resolve(
                parent_id=f"parent-{i}",
                child_id=f"child-{i}",
                message_id=str(uuid.uuid4()),
                status=STATUS_RESPONDED,
            )
            for i in range(50)
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)
        assert results == [None] * 50
        assert cm.resolve_response.await_count == 50
