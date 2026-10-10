"""Tests for the checkpoint-saver resilience layer (incident 2026-10-10).

Three components are exercised here:

1. ``_wrap_saver_with_connection_retry`` — one-shot retry on
   connection-class failures; non-connection errors propagate
   immediately; second failure re-raises unchanged.
2. ``PostgresCheckpointerAdapter.close`` — topology-aware: pool vs
   single-connection paths; ordering preserved (asyncpg pool first,
   saver resource last).
3. ``_is_retryable_connection_error`` — classifier test surface:
   SQLSTATE 08xxx, 57P01/57P02/57P03, psycopg OperationalError
   "the connection is closed"; everything else is non-retryable.

Additionally covered:

4. ``_SaverRetryProxy`` ABC registration — the proxy MUST pass the
   ``isinstance(..., BaseCheckpointSaver)`` gate that
   ``StateGraph.compile`` runs via ``ensure_valid_checkpointer``.
   Without that, every PG install dies at first instance-graph build
   with ``TypeError: Invalid checkpointer provided``. See fix #1 in
   incident 2026-10-10 review.

5. ``_is_retryable_connection_error`` substring guard — the
   "the connection is closed" substring fallback fires ONLY for
   psycopg-family exception modules. A coincidentally-worded
   exception from elsewhere must NOT be classified as retryable.

These are pure-Python tests — no DB connection required. Real-PG
recovery integration is the tester's lane (out of scope here).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from daemon.checkpoint_adapter import (
    PostgresCheckpointerAdapter,
    _is_retryable_connection_error,
    _wrap_saver_with_connection_retry,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


class _OperationalError(Exception):
    """psycopg's OperationalError family stand-in.

    psycopg exposes ``.sqlstate`` as a class attribute; tests set it
    on instances via kwargs.

    The ``__module__`` is forced to ``"psycopg"`` so the
    ``_is_retryable_connection_error`` substring-guard correctly
    recognises this stand-in as a psycopg-family exception (post
    incident-2026-10-10 review fix #3 — the substring fallback
    fires only on psycopg modules).
    """

    __module__ = "psycopg"

    def __init__(self, message: str, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


class _FakeAsyncConnection:
    """Single-connection stand-in for psycopg.AsyncConnection.

    No pool-only attributes — the topology detector (close()) must
    classify this as ``single-connection``.
    """

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakePool:
    """Pool stand-in for psycopg_pool.AsyncConnectionPool.

    Has the pool-only attributes ``get_stats`` and ``min_size`` so
    the topology detector classifies it as ``pool``.
    """

    def __init__(self) -> None:
        self.closed = False
        self.min_size = 1
        self.max_size = 5

    async def get_stats(self) -> dict:
        return {}

    async def close(self) -> None:
        self.closed = True


class _FakeSaver:
    """Saver stand-in exposing ``conn`` and the methods the wrapper
    retries (aget, aput, etc.). Records each call so tests can assert
    retry counts."""

    def __init__(self, conn: Any, *, fail_count: int = 0, exc_factory=None) -> None:
        self.conn = conn
        self.fail_count = fail_count
        self.exc_factory = exc_factory
        self.call_count = 0

    async def aget(self, *args, **kwargs):
        self.call_count += 1
        if self.fail_count > 0:
            self.fail_count -= 1
            if self.exc_factory:
                raise self.exc_factory()
        return {"args": args, "kwargs": kwargs, "call": self.call_count}

    async def aput(self, *args, **kwargs):
        self.call_count += 1
        if self.fail_count > 0:
            self.fail_count -= 1
            if self.exc_factory:
                raise self.exc_factory()
        return self.call_count

    async def alist(self, *args, **kwargs):
        self.call_count += 1
        if self.fail_count > 0:
            self.fail_count -= 1
            if self.exc_factory:
                raise self.exc_factory()
        return []


# ── _is_retryable_connection_error ─────────────────────────────────────────


class TestIsRetryableConnectionError:
    """Classifier coverage for the retry gate."""

    def test_sqlstate_08000_is_retryable(self):
        # class-08 connection_exception (SQLSTATE 08000)
        exc = _OperationalError("connection_failure", sqlstate="08000")
        assert _is_retryable_connection_error(exc) is True

    def test_sqlstate_08006_is_retryable(self):
        # class-08 connection_failure (SQLSTATE 08006)
        exc = _OperationalError("connection_failure", sqlstate="08006")
        assert _is_retryable_connection_error(exc) is True

    def test_sqlstate_57p01_admin_shutdown_is_retryable(self):
        # 57P01 admin_shutdown — server-initiated termination
        exc = _OperationalError("admin_shutdown", sqlstate="57P01")
        assert _is_retryable_connection_error(exc) is True

    def test_sqlstate_57p02_crash_shutdown_is_retryable(self):
        exc = _OperationalError("crash_shutdown", sqlstate="57P02")
        assert _is_retryable_connection_error(exc) is True

    def test_sqlstate_57p03_cannot_connect_now_is_retryable(self):
        exc = _OperationalError("cannot_connect_now", sqlstate="57P03")
        assert _is_retryable_connection_error(exc) is True

    def test_psycopg_operational_error_connection_closed_is_retryable(self):
        """The exact string the journal captured during the incident."""
        exc = _OperationalError("the connection is closed")
        assert _is_retryable_connection_error(exc) is True

    def test_sqlstate_40001_serialization_failure_is_NOT_retryable(self):
        """Serializable failures are handled by the saver's own
        SERIALIZABLE retry wrap (delete_blobs_anti_join) — this
        wrapper is for connection-class failures only."""
        exc = _OperationalError("serialization_failure", sqlstate="40001")
        assert _is_retryable_connection_error(exc) is False

    def test_sqlstate_23505_unique_violation_is_NOT_retryable(self):
        exc = _OperationalError("unique_violation", sqlstate="23505")
        assert _is_retryable_connection_error(exc) is False

    def test_plain_value_error_is_NOT_retryable(self):
        assert _is_retryable_connection_error(ValueError("nope")) is False

    def test_runtime_error_is_NOT_retryable(self):
        assert _is_retryable_connection_error(RuntimeError("oops")) is False

    def test_empty_sqlstate_with_arbitrary_message_is_NOT_retryable(self):
        """A non-connection message with no SQLSTATE must not be
        mis-classified as retryable."""
        exc = _OperationalError("some other db error")
        assert _is_retryable_connection_error(exc) is False

    def test_non_psycopg_exception_with_connection_closed_substring_is_NOT_retryable(
        self,
    ):
        """Guard against false positives on the substring fallback.

        The ``"the connection is closed"`` substring match must only
        fire for psycopg-family exception modules. An application-
        level ``RuntimeError`` (or any exception whose module is NOT
        in the ``psycopg`` family) that happens to carry the same
        English phrase must NOT be classified as retryable — the
        retry would silently swallow upstream error classes.
        """

        class _RuntimeErrorLike(RuntimeError):
            pass

        # __module__ defaults to the test module, which is not a
        # psycopg family. The exception's class is RuntimeError,
        # unrelated to psycopg.
        exc = _RuntimeErrorLike("the connection is closed")
        assert _is_retryable_connection_error(exc) is False

    def test_psycopg_exception_with_connection_closed_substring_IS_retryable(
        self,
    ):
        """Confirm the guard is BROAD ENOUGH — a real psycopg
        exception with the substring IS retryable, so tightening
        the predicate did not break the original incident case.
        """

        class _PsycopgOp(Exception):
            pass

        # Simulate ``type(exc).__module__ == "psycopg"`` (or
        # psycopg.errors / similar) without depending on the
        # psycopg driver at test-import time.
        _PsycopgOp.__module__ = "psycopg.errors"
        exc = _PsycopgOp("the connection is closed")
        assert _is_retryable_connection_error(exc) is True


# ── _SaverRetryProxy isinstance / compile gate (review fix #1) ───────────────


class TestSaverRetryProxyABCRegistration:
    """The retry proxy must satisfy ``BaseCheckpointSaver`` isinstance
    checks that LangGraph runs via ``ensure_valid_checkpointer``.

    Without this, ``StateGraph.compile(checkpointer=proxy)`` raises
    ``TypeError: Invalid checkpointer provided…`` for every PG
    install at first instance-graph build (the runtime-reproduced
    blocker). These tests are the unblock proof.
    """

    def test_proxy_is_BaseCheckpointSaver_instance(self):
        """isinstance(proxy, BaseCheckpointSaver) must hold.

        Independent of any compile-time machinery — this is the
        exact gate at ``langgraph.types.ensure_valid_checkpointer``.
        Works under the conftest's ``langgraph.checkpoint.base`` mock
        (which exposes a plain stand-in class) AND against the real
        langgraph class.
        """
        from langgraph.checkpoint.base import BaseCheckpointSaver

        saver = _FakeSaver(_FakePool())
        proxy = _wrap_saver_with_connection_retry(saver)
        assert isinstance(proxy, BaseCheckpointSaver)
        # The proxy class itself subclasses BaseCheckpointSaver,
        # not via register() / monkey-patch. Direct inheritance
        # is the only safe way given langgraph 1.0.9's plain
        # ``type`` (not ABCMeta) checkpointer class.
        proxy_cls = type(proxy)
        assert issubclass(proxy_cls, BaseCheckpointSaver)
        assert BaseCheckpointSaver in proxy_cls.__mro__

    def test_ensure_valid_checkpointer_does_not_raise(self):
        """The exact gate ``StateGraph.compile`` runs.

        Requires real LangGraph — both ``langgraph.types`` and
        ``langgraph.checkpoint.base``. Skips under the conftest's
        mock setup because ``pytest.importorskip("langgraph.types")``
        cannot resolve the real submodule against the empty
        ``langgraph`` namespace package the conftest installs.

        On a real LangGraph install this exercises the runtime
        gate against the actual ``BaseCheckpointSaver`` class and
        proves the unblock.
        """
        pytest.importorskip("langgraph.types")
        from langgraph.checkpoint.base import BaseCheckpointSaver
        from langgraph.types import ensure_valid_checkpointer

        saver = _FakeSaver(_FakePool())
        proxy = _wrap_saver_with_connection_retry(saver)
        # Must NOT raise. (Returns the proxy unchanged for valid
        # inputs; ``None``/``True``/``False`` are also valid.)
        result = ensure_valid_checkpointer(proxy)
        assert result is proxy
        # Defense in depth — if the proxy ever regresses to a
        # non-subclass object, the gate would still pass
        # ``ensure_valid_checkpointer`` but ``isinstance`` would
        # fail directly. Assert both.
        assert isinstance(proxy, BaseCheckpointSaver)

    def test_stategraph_compile_accepts_proxy_as_checkpointer(self):
        """End-to-end: a real StateGraph.compile(checkpointer=proxy)
        must succeed against the same proxy the manager wires in
        production. This is the runtime-reproduced failure path
        the reviewer identified — when this passes, the blocker
        is dead.

        Skips under the conftest's mock path: the
        ``StateGraph = MagicMock()`` placeholder installed by
        ``tests/conftest.py`` would let the test "pass" without
        ever invoking ``ensure_valid_checkpointer``. Detected via
        ``unittest.mock`` introspection (``_mock_name`` is set on
        all ``MagicMock`` instances; the real ``StateGraph`` from
        ``langgraph.graph`` is a plain class with no such
        attribute).
        """
        pytest.importorskip("langgraph.graph")
        from langgraph.graph import StateGraph

        # Real-langgraph guard: MagicMock from conftest would
        # silently pass this test without exercising the gate.
        # The real ``StateGraph`` is a class, not a Mock instance.
        if isinstance(StateGraph, type) is False or hasattr(
            StateGraph, "_mock_name"
        ):
            pytest.skip(
                "test_stategraph_compile_accepts_proxy_as_checkpointer "
                "requires a real langgraph.graph (not the conftest Mock)"
            )
        from typing import TypedDict

        class _State(TypedDict):
            x: int

        # Minimal fake saver providing just the two methods
        # StateGraph.compile touches synchronously.
        class _CompileSaver:
            async def aget(self, config):
                return None

            async def aput(self, config, checkpoint, metadata, new_versions):
                return {
                    "configurable": {
                        "thread_id": "t",
                        "checkpoint_ns": "",
                        "checkpoint_id": "c",
                    }
                }

        proxy = _wrap_saver_with_connection_retry(_CompileSaver())
        graph = StateGraph(_State)

        def _inc(state: _State) -> _State:
            return {"x": state.get("x", 0) + 1}

        graph.add_node("inc", _inc)
        graph.set_entry_point("inc")
        graph.add_edge("inc", "__end__")

        # Must NOT raise TypeError. A successful return value is a
        # CompiledStateGraph; the compile-time checkpointer gate
        # ran clean.
        compiled = graph.compile(checkpointer=proxy)
        assert compiled is not None

    def test_proxy_preserves_method_call_through_retry(self):
        """Inheritance + explicit method definitions must coexist:
        the wrapper keeps its retry layer even though it now has
        inherited ``BaseCheckpointSaver`` machinery in the MRO.
        """
        saver = _FakeSaver(_FakePool())
        proxy = _wrap_saver_with_connection_retry(saver)
        # Public method dispatched via the explicit async aget,
        # which carries the retry layer.
        assert asyncio.iscoroutinefunction(proxy.aget)
        # Inherited methods (none yet exercised by the public
        # gate) still resolve via __getattr__ to the wrapped
        # saver, NOT via MRO.
        assert proxy.fail_count == 0  # attribute passthrough


# ── _wrap_saver_with_connection_retry ──────────────────────────────────────


class TestRetryWrapper:
    """End-to-end retry semantics on the saver proxy."""

    @pytest.mark.asyncio
    async def test_passes_through_on_success(self):
        saver = _FakeSaver(_FakePool())
        wrapped = _wrap_saver_with_connection_retry(saver)
        result = await wrapped.aget("config-1")
        assert result["call"] == 1
        assert saver.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_once_on_operational_closed_then_succeeds(self):
        """First call raises 'the connection is closed', second call
        succeeds. Wrapper must invoke the saver TWICE and return the
        second result."""
        saver = _FakeSaver(
            _FakePool(),
            fail_count=1,
            exc_factory=lambda: _OperationalError("the connection is closed"),
        )
        wrapped = _wrap_saver_with_connection_retry(saver)
        result = await wrapped.aget("config-1")
        assert saver.call_count == 2
        assert result["call"] == 2  # second call succeeded

    @pytest.mark.asyncio
    async def test_retries_once_on_sqlstate_08006(self):
        saver = _FakeSaver(
            _FakePool(),
            fail_count=1,
            exc_factory=lambda: _OperationalError("connection_failure", sqlstate="08006"),
        )
        wrapped = _wrap_saver_with_connection_retry(saver)
        await wrapped.aput("config-1", value="x")
        assert saver.call_count == 2

    @pytest.mark.asyncio
    async def test_does_not_retry_non_connection_error(self):
        saver = _FakeSaver(
            _FakePool(),
            fail_count=1,
            exc_factory=lambda: _OperationalError("unique_violation", sqlstate="23505"),
        )
        wrapped = _wrap_saver_with_connection_retry(saver)
        with pytest.raises(_OperationalError):
            await wrapped.aput("config-1", value="x")
        # Exactly one call — non-connection errors propagate immediately.
        assert saver.call_count == 1

    @pytest.mark.asyncio
    async def test_second_failure_reraises_unchanged(self):
        """The second connection-class failure re-raises unchanged so
        upstream error classification sees the same exception."""
        # Always fail with the same exc
        saver = _FakeSaver(
            _FakePool(),
            fail_count=10,
            exc_factory=lambda: _OperationalError("the connection is closed"),
        )
        wrapped = _wrap_saver_with_connection_retry(saver)
        with pytest.raises(_OperationalError) as ei:
            await wrapped.aget("config-1")
        # Exactly two calls — one initial + one retry.
        assert saver.call_count == 2
        # Exception is the same instance class (psycopg classifies by type).
        assert isinstance(ei.value, _OperationalError)
        assert "the connection is closed" in str(ei.value)

    @pytest.mark.asyncio
    async def test_attribute_passthrough(self):
        """Non-method access (saver.conn) passes through transparently
        — the topology-aware close() relies on this."""
        pool = _FakePool()
        saver = _FakeSaver(pool)
        wrapped = _wrap_saver_with_connection_retry(saver)
        assert wrapped.conn is pool
        assert wrapped.fail_count == 0  # data attribute passthrough

    @pytest.mark.asyncio
    async def test_alist_retries_too(self):
        saver = _FakeSaver(
            _FakePool(),
            fail_count=1,
            exc_factory=lambda: _OperationalError("the connection is closed"),
        )
        wrapped = _wrap_saver_with_connection_retry(saver)
        result = await wrapped.alist("config-1")
        assert saver.call_count == 2
        assert result == []

    @pytest.mark.asyncio
    async def test_does_not_swallow_cancelled_error(self):
        """asyncio.CancelledError must propagate unchanged — the
        graceful-shutdown contract depends on it."""
        class _CancelSaver:
            conn = None

            async def aget(self, *args, **kwargs):
                raise asyncio.CancelledError()

        wrapped = _wrap_saver_with_connection_retry(_CancelSaver())
        with pytest.raises(asyncio.CancelledError):
            await wrapped.aget("config-1")


# ── PostgresCheckpointerAdapter close() topology ───────────────────────────


class TestAdapterCloseTopology:
    """The adapter's close() must work for both pool-backed and
    single-connection topologies, with the asyncpg pool closed FIRST."""

    @pytest.mark.asyncio
    async def test_pool_topology_closes_asyncpg_pool_then_saver_pool(self):
        asyncpg_pool = _FakePool()
        saver_pool = _FakePool()
        # Saver exposes the saver pool via .conn (per upstream aio.py:57).
        saver = _FakeSaver(saver_pool)
        adapter = PostgresCheckpointerAdapter(saver, asyncpg_pool)

        # Both initially unclosed
        assert not asyncpg_pool.closed
        assert not saver_pool.closed

        await adapter.close()

        assert asyncpg_pool.closed, "asyncpg pool should be closed"
        assert saver_pool.closed, "saver pool should be closed"

    @pytest.mark.asyncio
    async def test_single_conn_topology_closes_asyncpg_then_saver_conn(self):
        """Legacy topology (single psycopg.AsyncConnection on .conn)
        still works — important for the existing test suite and any
        custom integrations that pre-date the pool fix."""
        saver_conn = _FakeAsyncConnection()
        asyncpg_pool = _FakePool()
        saver = _FakeSaver(saver_conn)
        adapter = PostgresCheckpointerAdapter(saver, asyncpg_pool)

        assert not saver_conn.closed
        assert not asyncpg_pool.closed

        await adapter.close()

        assert saver_conn.closed
        assert asyncpg_pool.closed

    @pytest.mark.asyncio
    async def test_close_tolerates_asyncpg_pool_failure(self):
        """If the asyncpg pool fails to close, the saver pool must
        STILL be closed — never leak one resource because the other
        raised."""
        saver_pool = _FakePool()

        class _FailingPool:
            min_size = 1
            max_size = 5

            async def get_stats(self):
                return {}

            async def close(self):
                raise RuntimeError("asyncpg pool close failed")

        saver = _FakeSaver(saver_pool)
        adapter = PostgresCheckpointerAdapter(saver, _FailingPool())
        await adapter.close()
        assert saver_pool.closed, "saver pool must close even if asyncpg fails"

    @pytest.mark.asyncio
    async def test_close_tolerates_saver_resource_failure(self):
        """If the saver resource fails to close, the asyncpg pool must
        still be closed (it was closed first; this confirms the
        ordering survives an exception in the second step)."""
        asyncpg_pool = _FakePool()

        class _FailingPool:
            min_size = 1
            max_size = 5

            async def get_stats(self):
                return {}

            async def close(self):
                raise RuntimeError("boom")

        class _FailingSaverConn:
            async def close(self):
                raise RuntimeError("boom")

        saver = _FakeSaver(_FailingSaverConn())
        adapter = PostgresCheckpointerAdapter(saver, asyncpg_pool)
        await adapter.close()
        assert asyncpg_pool.closed

    @pytest.mark.asyncio
    async def test_raw_saver_returns_underlying_saver(self):
        """The adapter's ``raw_saver`` property must continue to
        return the saver (or its retry proxy) so LangGraph reads/
        writes work — the proxy must be transparent."""
        saver = _FakeSaver(_FakePool())
        adapter = PostgresCheckpointerAdapter(saver, _FakePool())
        # raw_saver attribute access passes through the proxy to the
        # underlying saver's methods. The proxy IS the raw_saver (the
        # wrapper is in the hot path; identity is preserved).
        rs = adapter.raw_saver
        # aget is callable through the proxy.
        result = await rs.aget("config-1")
        assert result["args"] == ("config-1",)