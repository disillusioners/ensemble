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
import importlib
import inspect
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


# ── Real-langgraph binding gate (K3) ────────────────────────────────────────


@pytest.fixture()
def _real_langgraph_saver_base():
    """Bind ``daemon.checkpoint_adapter``'s ``BaseCheckpointSaver`` to the
    REAL pinned class for one test, then restore the conftest mock view.

    Mirrors the binding-gate idiom of
    ``tests/integration/test_get_instance_messages_observed_count_zero.py``
    (``evict_langgraph_mocks`` → real imports → ``restore_langgraph_mocks``,
    autouse fixture, function lifetime) with one addition this file needs:
    a RELOAD PAIRING. ``evict_langgraph_mocks()`` clears ``sys.modules``
    but ``daemon.checkpoint_adapter`` is already imported (collection-time,
    under the conftest mocks) with its module-global ``BaseCheckpointSaver``
    bound to the conftest MOCK class — and the retry proxy subclasses
    whichever object that global holds when ``_wrap_saver_with_connection_retry``
    runs. Eviction alone therefore proves nothing here; reloading the
    module under eviction rebinds the global to the REAL pinned
    ``langgraph.checkpoint.base.BaseCheckpointSaver``. The ``finally``
    block restores the mocks and reloads again, so mock-bound unit tests
    sharing this session keep their original module view.
    """
    from tests.helpers.checkpoint_prune_pg import (
        evict_langgraph_mocks,
        restore_langgraph_mocks,
    )

    saved = evict_langgraph_mocks()
    import daemon.checkpoint_adapter as ca

    importlib.reload(ca)
    try:
        yield ca
    finally:
        restore_langgraph_mocks(saved)
        importlib.reload(ca)


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

    K3: every test here runs against the REAL pinned
    ``langgraph.checkpoint.base.BaseCheckpointSaver`` via the
    ``_real_langgraph_saver_base`` fixture (mock evict + module
    reload + restore). Round 1 certified these gates against the
    conftest MOCK — inverted semantics: the mock has no
    ``ensure_valid_checkpointer`` and no concrete base surface, so
    the suite stayed green while production was broken.
    """

    def test_proxy_is_BaseCheckpointSaver_instance(
        self, _real_langgraph_saver_base
    ):
        """isinstance(proxy, BaseCheckpointSaver) must hold against the
        REAL pinned class — the exact gate at
        ``langgraph.types.ensure_valid_checkpointer``.
        """
        ca = _real_langgraph_saver_base
        BaseCheckpointSaver = ca.BaseCheckpointSaver
        # K3 guard: this MUST be the real pinned class, not the
        # conftest mock (which lives in tests.conftest).
        assert BaseCheckpointSaver.__module__ == "langgraph.checkpoint.base"

        saver = _FakeSaver(_FakePool())
        proxy = ca._wrap_saver_with_connection_retry(saver)
        assert isinstance(proxy, BaseCheckpointSaver)
        # The proxy class itself subclasses BaseCheckpointSaver,
        # not via register() / monkey-patch. Direct inheritance
        # is the only safe way given langgraph 1.0.9's plain
        # ``type`` (not ABCMeta) checkpointer class.
        proxy_cls = type(proxy)
        assert issubclass(proxy_cls, BaseCheckpointSaver)
        assert BaseCheckpointSaver in proxy_cls.__mro__

    def test_ensure_valid_checkpointer_does_not_raise(
        self, _real_langgraph_saver_base
    ):
        """The exact gate ``StateGraph.compile`` runs — REAL LangGraph.

        Under the conftest mock namespace this test used to SILENTLY
        SKIP (``importorskip("langgraph.types")`` cannot resolve the
        real submodule against the mocked empty ``langgraph`` package)
        — the inverted-semantics defect: the suite "proved" the fix
        while the runtime gate would have rejected the proxy. The
        fixture evicts the mocks, so the real import resolves.
        """
        pytest.importorskip("langgraph.types")
        ca = _real_langgraph_saver_base
        BaseCheckpointSaver = ca.BaseCheckpointSaver
        from langgraph.types import ensure_valid_checkpointer

        saver = _FakeSaver(_FakePool())
        proxy = ca._wrap_saver_with_connection_retry(saver)
        # Must NOT raise. (Returns the proxy unchanged for valid
        # inputs; ``None``/``True``/``False`` are also valid.)
        result = ensure_valid_checkpointer(proxy)
        assert result is proxy
        # Defense in depth — if the proxy ever regresses to a
        # non-subclass object, the gate would still pass
        # ``ensure_valid_checkpointer`` but ``isinstance`` would
        # fail directly. Assert both.
        assert isinstance(proxy, BaseCheckpointSaver)

    def test_stategraph_compile_accepts_proxy_as_checkpointer(
        self, _real_langgraph_saver_base
    ):
        """End-to-end: a real StateGraph.compile(checkpointer=proxy)
        must succeed against the same proxy the manager wires in
        production. This is the runtime-reproduced failure path
        the reviewer identified — when this passes, the blocker
        is dead.

        Runs under REAL langgraph only: the fixture evicts the
        conftest ``StateGraph = MagicMock()`` placeholder, so no
        mock-detection skip is needed (round 1 needed one because
        the mock would silently "pass" this test without ever
        invoking ``ensure_valid_checkpointer``).
        """
        pytest.importorskip("langgraph.graph")
        ca = _real_langgraph_saver_base
        from langgraph.graph import StateGraph

        # K3 guard: the fixture guarantees the real StateGraph (a
        # plain class), never the conftest MagicMock.
        assert not hasattr(StateGraph, "_mock_name")

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

        proxy = ca._wrap_saver_with_connection_retry(_CompileSaver())
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
    async def test_alist_not_intercepted_async_generator_passthrough(self):
        """``alist`` MUST NOT be wrapped as a coroutine on the proxy.

        ``AsyncPostgresSaver.alist`` (langgraph-checkpoint-postgres 3.1.0)
        is an ASYNC GENERATOR — the source contains ``yield`` — and
        LangGraph consumes it via ``async for c in checkpointer.alist(...)``
        (pinned pregel/main.py:1417). A coroutine-shaped wrapper breaks
        both consumption styles:

          * ``async for proxy.alist(...)`` → ``TypeError: ... requires __aiter__``
          * ``await proxy.alist(...)``     → ``TypeError: object
            async_generator can't be used in 'await'``

        The contract: ``alist`` falls through ``__getattr__`` to the
        wrapped saver's real async generator. (a) The proxy must NOT
        declare an explicit ``alist`` attribute — so attribute access
        returns the same object the underlying saver exposes. (b)
        Iterating the proxy must drive the underlying saver's yields.
        """
        seen_items: list[Any] = []

        class _AlistGeneratorSaver:
            """Minimal saver whose ``alist`` is a real async generator.

            ``async def ... yield ...`` makes ``alist`` an async
            generator function (has ``__code__`` with ``CO_ASYNC_GENERATOR``),
            which is the exact surface shape
            ``langgraph-checkpoint-postgres 3.1.0`` ships.
        """

            conn = None  # topology detector short-circuits

            async def alist(self, *args, **kwargs):
                # The yield makes this an ``async def`` with
                # ``__aiter__`` / ``__anext__`` on the returned
                # iterator — the correct shape for ``async for``.
                for i in (1, 2, 3):
                    yield {"i": i, "args": args, "kwargs": kwargs}

        fake = _AlistGeneratorSaver()
        proxy = _wrap_saver_with_connection_retry(fake)

        # (a) The proxy now defines an EXPLICIT alist forwarder (the
        # round-2 MRO-rule fix: a bare __getattr__ fall-through is
        # INERT because the pinned base declares a CONCRETE alist
        # stub that resolves via MRO first). The forwarder must be a
        # plain ``def`` that returns the wrapped saver's async
        # generator — NOT a coroutine wrapper, and NOT the base's
        # concrete stub. The regression classes: (i) a proxy-defined
        # ``async def alist`` would make this a coroutine function
        # and break ``async for`` consumption; (ii) no forwarder at
        # all would let the base stub win via MRO and raise
        # NotImplementedError on first ``__anext__``.
        assert proxy.alist.__func__ is not fake.alist.__func__, (
            "proxy.alist should be the proxy's own explicit forwarder "
            "(the MRO rule makes a __getattr__ fall-through inert); if "
            "this is the wrapped saver's function the forwarder vanished"
        )
        assert not asyncio.iscoroutinefunction(proxy.alist), (
            "proxy.alist must remain an async-generator-preserving "
            "passthrough, not a coroutine — a coroutine wrapping an "
            "async generator fails both ``async for`` and ``await``"
        )
        # Belt-and-braces: the call returns an async generator object,
        # NOT a coroutine. This is the live-shape check — if a future
        # refactor swaps the proxy's intercepted method shape, this
        # still fires.
        gen_obj = proxy.alist("config-1")
        assert not asyncio.iscoroutine(gen_obj), (
            "proxy.alist(...) must return an async generator object, "
            "not a coroutine"
        )
        assert hasattr(gen_obj, "__aiter__") and hasattr(gen_obj, "__anext__"), (
            "proxy.alist(...) must return an object with async iterator "
            "protocol — that is the only shape LangGraph's pregel/main.py:1417 "
            "consumes via ``async for``"
        )

        # (b) Iteration works end-to-end through the proxy — the
        # upstream pipeline's exact consumption style. This is the
        # assertion the original ``await proxy.alist(...)`` style
        # failed (TypeError: object async_generator can't be used
        # in 'await'); ``async for`` is the surviving style and it
        # must produce the underlying saver's items verbatim.
        async for item in proxy.alist("config-1", limit=5):
            seen_items.append(item)

        assert seen_items == [
            {"i": 1, "args": ("config-1",), "kwargs": {"limit": 5}},
            {"i": 2, "args": ("config-1",), "kwargs": {"limit": 5}},
            {"i": 3, "args": ("config-1",), "kwargs": {"limit": 5}},
        ], "proxy must yield the underlying async generator's items"

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

# ── K-guard: real-pinned-class contract (round 2, incident 2026-10-10) ──────


class TestRealLanggraphSaverProxy:
    """Contract tests against the REAL pinned ``BaseCheckpointSaver``.

    THE ROOT-CAUSE CLASS this file's mock-bound tests could never see:
    the pinned base (``.venv/.../langgraph/checkpoint/base/__init__.py``,
    langgraph 1.0.9 / langgraph-checkpoint 3.1.x) declares ZERO abstract
    methods — every public member is CONCRETE, so MRO resolves it on the
    proxy and ``__getattr__`` never fires. Any member the proxy fails to
    forward explicitly silently becomes the BASE's stub:

    * K1 — ``get_next_version``: base stub raises NotImplementedError
      for ``str`` versions (every existing PG thread carries str
      versions, ``f"{next_v:032}.{next_h:016}"``); pregel calls it at
      8 sites → first message to any existing PG instance crashed.
    * K2 — ``alist``: base stub is a concrete async-generator that
      raises NotImplementedError on first ``__anext__`` → every
      ``aget_state_history`` broke.

    Every test here runs under ``_real_langgraph_saver_base`` — the
    conftest langgraph mocks are EVICTED and ``daemon.checkpoint_adapter``
    is reloaded so its proxy subclasses the true pinned class. The
    tests self-verify the binding (``__module__`` /
    ``inspect.getfile`` assertions), so a regression of the fixture
    back to mock-bound state fails loudly instead of passing vacuously.
    """

    @staticmethod
    def _assert_real_pinned(base_cls) -> None:
        """Fail loudly if the binding regressed to the conftest mock."""
        assert base_cls.__module__ == "langgraph.checkpoint.base", (
            f"expected the REAL pinned BaseCheckpointSaver, got "
            f"{base_cls.__module__}.{base_cls.__qualname__} — the "
            f"mock-eviction/reload fixture regressed"
        )
        module_file = inspect.getfile(base_cls)
        assert "site-packages" in module_file and module_file.endswith(
            "langgraph/checkpoint/base/__init__.py"
        ), f"not the pinned site-packages class: {module_file}"

    def test_k1_get_next_version_forwards_wrapped_override(
        self, _real_langgraph_saver_base
    ):
        """K1: a wrapped saver that OVERRIDES ``get_next_version`` must
        be reachable through the proxy — the base's concrete stub (which
        raises NotImplementedError for str versions) must NOT win via
        MRO. This is the deployment blocker: pregel calls
        ``checkpointer.get_next_version(current, None)`` with str
        versions minted by postgres/base.py:543-552.
        """
        ca = _real_langgraph_saver_base
        real_base = ca.BaseCheckpointSaver
        self._assert_real_pinned(real_base)

        STR_VERSION = (
            "00000000000000000000000000000001.0000000000000000"
        )
        EXPECTED_NEXT = (
            "00000000000000000000000000000002.0000000000000000"
        )

        class _StrVersionSaver(real_base):
            """PG-shaped saver: str versions (postgres/base.py:543-552)."""

            def get_next_version(self, current, channel):
                # Sanity: the base stub would raise NotImplementedError
                # for this exact input — prove the override is live.
                assert isinstance(current, str)
                next_v = int(current.split(".")[0], 16) + 1
                return f"{next_v:032}.{0:016}"

        # Precondition: the fake really overrides the base member.
        assert (
            _StrVersionSaver.get_next_version is not real_base.get_next_version
        )

        proxy = ca._wrap_saver_with_connection_retry(_StrVersionSaver())
        # NO NotImplementedError — the wrapped override answers.
        assert (
            proxy.get_next_version(STR_VERSION, None) == EXPECTED_NEXT
        )
        # And the proxy surface must not be a coroutine (sync ID mint).
        assert not asyncio.iscoroutinefunction(proxy.get_next_version)

    def test_k2_alist_round_trips_wrapped_async_generator(
        self, _real_langgraph_saver_base
    ):
        """K2: ``async for c in proxy.alist(...)`` must drive the
        WRAPPED saver's real async generator — the base's concrete
        alist stub (raises NotImplementedError on first ``__anext__``)
        must NOT win via MRO. Round 1's ``__getattr__`` de-interception
        (f19803dc3) was INERT for exactly this reason.
        """
        ca = _real_langgraph_saver_base
        real_base = ca.BaseCheckpointSaver
        self._assert_real_pinned(real_base)

        class _AlistSaver(real_base):
            """Real async-generator alist (aio.py 3.1.0 shape)."""

            async def alist(self, *args, **kwargs):
                for i in (1, 2, 3):
                    yield {"i": i, "args": args, "kwargs": kwargs}

        assert (
            _AlistSaver.alist is not real_base.alist
        ), "fake must override the base alist stub"
        # The base alist IS an async-generator function (concrete stub).
        assert inspect.isasyncgenfunction(real_base.alist)

        fake = _AlistSaver()
        proxy = ca._wrap_saver_with_connection_retry(fake)

        # The proxy's alist is NOT a coroutine function (explicit
        # forwarder returning the wrapped async generator).
        assert not asyncio.iscoroutinefunction(proxy.alist)
        gen_obj = proxy.alist("cfg", limit=5)
        assert not asyncio.iscoroutine(gen_obj)
        assert hasattr(gen_obj, "__aiter__") and hasattr(gen_obj, "__anext__")

        async def _walk():
            out = []
            async for c in proxy.alist("cfg", limit=5):
                out.append(c)
            return out

        items = asyncio.run(_walk())
        assert [i["i"] for i in items] == [1, 2, 3]
        assert items[0]["args"] == ("cfg",)
        assert items[0]["kwargs"] == {"limit": 5}

    def test_k_guard_concrete_surface_forwards_wrapped_overrides(
        self, _real_langgraph_saver_base
    ):
        """K-GUARD (systematic, load-bearing).

        Walks the REAL pinned ``BaseCheckpointSaver``'s concrete public
        methods and asserts: for EACH method, a wrapped saver that
        overrides it is reachable THROUGH the proxy (the proxy's
        attribute resolves to the wrapped implementation, not the base
        stub). A future langgraph bump that adds a new concrete stub to
        the base without a matching explicit forwarder fails this test
        loudly — the exact K1/K2 recurrence trap.

        Also covers the two shadowable surface members of the same
        family (``serde`` class-attr default, ``config_specs``
        property): the proxy must route both to the wrapped saver.
        """
        ca = _real_langgraph_saver_base
        real_base = ca.BaseCheckpointSaver
        self._assert_real_pinned(real_base)

        abstract = getattr(real_base, "__abstractmethods__", set())
        concrete_public = sorted(
            name
            for name, member in inspect.getmembers(
                real_base, predicate=inspect.isfunction
            )
            if not name.startswith("_") and name not in abstract
        )
        # Sanity: the walk must actually see the K1/K2 members —
        # otherwise the guard degenerates to a vacuous pass.
        assert "get_next_version" in concrete_public
        assert "alist" in concrete_public
        assert len(concrete_public) >= 20, (
            f"unexpectedly small base surface ({len(concrete_public)}) — "
            "the walk is broken, not the proxy"
        )

        problems: list[str] = []

        for name in concrete_public:
            base_member = getattr(real_base, name)
            hit = {"called": False}
            SENTINEL = object()

            if inspect.isasyncgenfunction(base_member):
                # Async-generator member: override with a real async
                # generator yielding exactly one sentinel item; iterate
                # through the proxy and require the sentinel.
                async def _gen(self, *args, _hit=hit, _s=SENTINEL, **kwargs):
                    _hit["called"] = True
                    yield _s

                override = _gen
            elif inspect.iscoroutinefunction(base_member):

                async def _coro(self, *args, _hit=hit, _s=SENTINEL, **kwargs):
                    _hit["called"] = True
                    return _s

                override = _coro
            else:

                def _sync(self, *args, _hit=hit, _s=SENTINEL, **kwargs):
                    _hit["called"] = True
                    return _s

                override = _sync

            fake = type(
                f"_KG Fake override:{name}",
                (real_base,),
                {
                    name: override,
                    # Never call the base __init__ (it would normalize
                    # serde etc.); these fakes carry no state.
                    "__init__": lambda self, *a, **kw: None,
                },
            )()
            proxy = ca._wrap_saver_with_connection_retry(fake)

            try:
                if inspect.isasyncgenfunction(base_member):
                    async def _drive():
                        got = []
                        async for item in getattr(proxy, name)():
                            got.append(item)
                        return got

                    got = asyncio.run(_drive())
                    routed = bool(got) and got[0] is SENTINEL
                elif inspect.iscoroutinefunction(base_member):
                    routed = asyncio.run(getattr(proxy, name)()) is SENTINEL
                else:
                    routed = getattr(proxy, name)() is SENTINEL
            except NotImplementedError as exc:
                problems.append(
                    f"{name}: base stub leaked through the proxy "
                    f"(NotImplementedError: {exc}) — missing explicit "
                    f"forwarder (the K1/K2 recurrence)"
                )
                continue
            except Exception as exc:  # noqa: BLE001 — report, don't crash
                problems.append(
                    f"{name}: unexpected {type(exc).__name__}: {exc}"
                )
                continue

            if not hit["called"] or not routed:
                problems.append(
                    f"{name}: proxy did NOT route to the wrapped "
                    f"override (called={hit['called']}, "
                    f"routed={routed}) — MRO resolved the base stub; "
                    f"add an explicit forwarder"
                )

        # Shadowable non-method surface members of the same family.
        class _SerdeFake(real_base):
            serde = ("fake-serde",)  # class attr shadows base default
            config_specs = ["fake-spec"]  # class attr shadows base property

            def __init__(self, *a, **kw):
                pass  # do NOT let the base __init__ normalize serde

        serde_fake = _SerdeFake()
        serde_proxy = ca._wrap_saver_with_connection_retry(serde_fake)
        if serde_proxy.serde is not serde_fake.serde:
            problems.append(
                "serde: proxy resolved the BASE class-attr default "
                "instead of the wrapped saver's serde — add the "
                "property forwarder"
            )
        if serde_proxy.config_specs != ["fake-spec"]:
            problems.append(
                "config_specs: proxy resolved the BASE property "
                "([]) instead of the wrapped saver's specs — add the "
                "property forwarder"
            )

        assert not problems, (
            "K-GUARD FAILURES — BaseCheckpointSaver members the proxy "
            "does not forward to a wrapped override:\n  "
            + "\n  ".join(problems)
            + "\nEvery concrete public member of the pinned base needs "
            "an explicit forwarder on _SaverRetryProxy (the MRO rule: "
            "__getattr__ never fires for base-concrete attributes). "
            "See the proxy class docstring (lockstep requirement)."
        )

    def test_k_guard_base_surface_is_fully_concrete_assumption(
        self, _real_langgraph_saver_base
    ):
        """Documents the MRO rule's premise: the pinned base has ZERO
        abstract methods. If a future pin flips members back to
        abstract, the K-guard walk above still holds (abstract members
        are excluded), but this premise statement must be revisited —
        abstract members on the proxy would make the proxy itself
        non-instantiable, an even louder failure.
        """
        ca = _real_langgraph_saver_base
        real_base = ca.BaseCheckpointSaver
        self._assert_real_pinned(real_base)
        assert getattr(real_base, "__abstractmethods__", set()) == set(), (
            "pinned BaseCheckpointSaver now has abstract methods — "
            "revisit the proxy's explicit-forwarder strategy and this "
            "test's premise"
        )
