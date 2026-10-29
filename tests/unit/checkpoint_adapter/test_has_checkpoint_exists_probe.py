"""EXISTS-probe tests for the checkpoint adapter + manager (Track-1, item 2).

Background
----------
``InstanceManager._has_checkpoint`` used to answer "does this instance
have a checkpoint?" with a FULL saver read::

    state = await self.checkpointer.raw_saver.aget(config)
    result = state is not None

That read (a) takes the saver's single ``asyncio.Lock`` — serializing
every other checkpoint read behind it — and (b) deserializes the
ENTIRE checkpoint (checkpoint + channel values + blobs) before the
caller can look at one boolean out of it.

Track-1 quick-win (checkpoint read-path plan, item 2) replaces it with
an EXISTS probe on the checkpoint adapter's direct-SQL surface:

    SELECT 1 FROM checkpoints WHERE thread_id = $1 AND checkpoint_ns = '' LIMIT 1

On PostgreSQL that query runs on the adapter's SEPARATE asyncpg pool
(``self._pool``), so the probe never touches the saver's lock and never
materialises a payload.

These tests are PURE UNIT (no PostgreSQL, no SQLite file, no network):
the direct-SQL surface is a fake that records the exact SQL text and
bind params and returns a canned row. Three layers are pinned:

  1. the PostgreSQL adapter helper — exact SQL, exact bind params, and
     the row-found / no-row boolean mapping;
  2. the SQLite adapter helper — the same semantics on the SQLite
     backend (the SQLite saver has no second pool, so the probe runs
     on the saver connection under the saver lock);
  3. ``InstanceManager._has_checkpoint`` — it calls the adapter
     probe, NEVER the saver, and keeps its fail-soft contract
     (exception → False).

Namespace semantics (pinned by the SQL text): the saver lookup being
replaced resolved ``config["configurable"].get("checkpoint_ns", "")``
— the ROOT namespace — so the probe pins ``checkpoint_ns = ''`` as a
literal. ``thread_id`` is the only bind parameter.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.checkpoint_adapter import (
    PostgresCheckpointerAdapter,
    SqliteCheckpointerAdapter,
)
from daemon.manager import InstanceManager
from daemon.services.instance_messaging import InstanceMessagingService

# The EXACT SQL text the PostgreSQL probe must issue. Pinned verbatim
# (including the single spaces) — the unit test asserts equality, not
# a substring.
EXPECTED_PG_SQL = (
    "SELECT 1 FROM checkpoints WHERE thread_id = $1 AND checkpoint_ns = '' LIMIT 1"
)

# The SQLite mirror of the same question (``?`` placeholders).
EXPECTED_SQLITE_SQL = (
    "SELECT 1 FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? LIMIT 1"
)


# ── fake direct-SQL surface (asyncpg-shaped) ────────────────────────────────


class _FakeConn:
    """Minimal asyncpg-connection stand-in.

    Records every ``fetchrow`` call as ``(sql, args)`` and returns a
    canned row (or ``None`` for the no-row case).
    """

    def __init__(self, row=None):
        self._row = row
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *args):
        self.fetchrow_calls.append((sql, args))
        return self._row


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    """asyncpg-Pool stand-in: ``acquire()`` yields the fake conn."""

    def __init__(self, conn):
        self._conn = conn
        self.acquire_count = 0

    def acquire(self):
        self.acquire_count += 1
        return _FakeAcquire(self._conn)


class _FakeSaver:
    """Saver stand-in whose read methods FAIL LOUDLY.

    The whole point of the probe is that the saver is never touched —
    so any call into it is a test failure, not a silent pass.
    """

    def __init__(self):
        self.aget = AsyncMock(
            side_effect=AssertionError("saver.aget must not be called by the probe")
        )

    @property
    def conn(self):  # pragma: no cover - never reached
        raise AssertionError("saver.conn must not be touched by the probe")


class _FakeSqliteCursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row

    async def fetchall(self):  # pragma: no cover - not used by the probe
        return [] if self._row is None else [self._row]


class _FakeSqliteConn:
    def __init__(self, row=None):
        self._row = row
        self.execute_calls: list[tuple[str, tuple]] = []

    async def execute(self, sql, params=()):
        self.execute_calls.append((sql, params))
        return _FakeSqliteCursor(self._row)


class _FakeSqliteSaver:
    def __init__(self, row=None):
        self.lock = asyncio.Lock()
        self.conn = _FakeSqliteConn(row)
        self.aget = AsyncMock(
            side_effect=AssertionError("saver.aget must not be called by the probe")
        )


# ── 1. PostgreSQL adapter probe ────────────────────────────────────────────


class TestPostgresHasCheckpointProbe:
    """The PG probe runs on the asyncpg pool — off the saver lock."""

    async def test_row_found_returns_true_and_pins_exact_sql(self):
        conn = _FakeConn(row={"1": 1})
        pool = _FakePool(conn)
        adapter = PostgresCheckpointerAdapter(_FakeSaver(), pool)

        result = await adapter.has_checkpoint("inst-1")

        assert result is True
        assert conn.fetchrow_calls == [(EXPECTED_PG_SQL, ("inst-1",))]
        # The SQL text pins the ROOT namespace (LangGraph's default
        # checkpoint_ns for a config carrying only thread_id).
        assert "checkpoint_ns = ''" in conn.fetchrow_calls[0][0]

    async def test_no_row_returns_false(self):
        conn = _FakeConn(row=None)
        adapter = PostgresCheckpointerAdapter(_FakeSaver(), _FakePool(conn))

        result = await adapter.has_checkpoint("inst-missing")

        assert result is False
        assert conn.fetchrow_calls == [(EXPECTED_PG_SQL, ("inst-missing",))]

    async def test_probe_never_touches_the_saver(self):
        """The saver read path (lock + deserialization) is bypassed."""
        saver = _FakeSaver()
        conn = _FakeConn(row={"1": 1})
        adapter = PostgresCheckpointerAdapter(saver, _FakePool(conn))

        await adapter.has_checkpoint("inst-1")

        saver.aget.assert_not_called()

    async def test_probe_runs_on_the_adapter_pool(self):
        """The SQL goes to the SEPARATE asyncpg pool, not the saver."""
        conn = _FakeConn(row=None)
        pool = _FakePool(conn)
        adapter = PostgresCheckpointerAdapter(_FakeSaver(), pool)

        await adapter.has_checkpoint("inst-1")

        assert pool.acquire_count == 1


# ── 2. SQLite adapter probe ────────────────────────────────────────────────


class TestSqliteHasCheckpointProbe:
    """The SQLite mirror keeps the same boolean semantics."""

    async def test_row_found_returns_true_and_pins_exact_sql(self):
        saver = _FakeSqliteSaver(row=(1,))
        adapter = SqliteCheckpointerAdapter(saver)

        result = await adapter.has_checkpoint("inst-1")

        assert result is True
        assert saver.conn.execute_calls == [(EXPECTED_SQLITE_SQL, ("inst-1", ""))]

    async def test_no_row_returns_false(self):
        saver = _FakeSqliteSaver(row=None)
        adapter = SqliteCheckpointerAdapter(saver)

        result = await adapter.has_checkpoint("inst-missing")

        assert result is False
        assert saver.conn.execute_calls == [(EXPECTED_SQLITE_SQL, ("inst-missing", ""))]

    async def test_probe_never_touches_the_saver_read(self):
        saver = _FakeSqliteSaver(row=(1,))
        adapter = SqliteCheckpointerAdapter(saver)

        await adapter.has_checkpoint("inst-1")

        saver.aget.assert_not_called()


# ── 3. InstanceManager._has_checkpoint ─────────────────────────────────────


class _StubAdapter:
    """Adapter stand-in exposing ONLY the new probe surface.

    ``raw_saver.aget`` is an AsyncMock that fails loudly if the manager
    ever reaches for the saver again.
    """

    def __init__(self, exists: bool = True, raises: Exception | None = None):
        self.exists = exists
        self.raises = raises
        self.calls: list[str] = []
        self.raw_saver = SimpleNamespace(
            aget=AsyncMock(
                side_effect=AssertionError(
                    "manager._has_checkpoint must not use raw_saver.aget"
                )
            )
        )

    async def has_checkpoint(self, thread_id: str) -> bool:
        self.calls.append(thread_id)
        if self.raises is not None:
            raise self.raises
        return self.exists


def _manager_with_adapter(adapter) -> InstanceManager:
    """Bare manager facade with only ``_checkpointer`` populated.

    ``InstanceManager.__new__`` skips ``__init__`` entirely — the
    established pattern (``tests/unit/test_manager_enqueue_message_work_id_required.py``).
    """
    manager = InstanceManager.__new__(InstanceManager)
    manager._checkpointer = adapter
    return manager


class TestManagerHasCheckpointProbe:
    """The manager delegates to the adapter probe and never reads the saver."""

    async def test_returns_true_when_probe_finds_a_row(self):
        adapter = _StubAdapter(exists=True)
        manager = _manager_with_adapter(adapter)

        result = await manager._has_checkpoint("inst-1")

        assert result is True
        assert adapter.calls == ["inst-1"]
        adapter.raw_saver.aget.assert_not_called()

    async def test_returns_false_when_probe_finds_no_row(self):
        adapter = _StubAdapter(exists=False)
        manager = _manager_with_adapter(adapter)

        result = await manager._has_checkpoint("inst-1")

        assert result is False
        assert adapter.calls == ["inst-1"]
        adapter.raw_saver.aget.assert_not_called()

    async def test_probe_failure_is_fail_soft_false(self):
        """Unchanged contract: any exception yields False (never raises)."""
        adapter = _StubAdapter(raises=RuntimeError("pool exhausted"))
        manager = _manager_with_adapter(adapter)

        result = await manager._has_checkpoint("inst-1")

        assert result is False
        assert adapter.calls == ["inst-1"]

    async def test_missing_checkpointer_is_fail_soft_false(self):
        """An uninitialized manager (no adapter) still returns False."""
        manager = InstanceManager.__new__(InstanceManager)
        manager._checkpointer = None

        result = await manager._has_checkpoint("inst-1")

        assert result is False


# ── 5. retry-path probe: InstanceMessagingService._has_checkpoint ──────────


class _StubAdapterWithBothSurfaces:
    """Adapter stand-in exposing BOTH the probe and the raw saver read.

    The retry-path conversion is only meaningful if the service uses the
    adapter's EXISTS probe — the raw saver (which carries the saver's
    single lock and the full deserialization) must never be touched.
    """

    def __init__(self, exists: bool = True, raises: Exception | None = None):
        self.exists = exists
        self.raises = raises
        self.probe_calls: list[str] = []
        self.has_checkpoint = AsyncMock(side_effect=self._probe)
        self.raw_saver = SimpleNamespace(
            aget=AsyncMock(
                side_effect=AssertionError(
                    "raw saver aget must not be called by the retry-path probe"
                )
            )
        )

    async def _probe(self, thread_id: str) -> bool:
        self.probe_calls.append(thread_id)
        if self.raises is not None:
            raise self.raises
        return self.exists


def _service_with_adapter(adapter) -> InstanceMessagingService:
    """Real ``InstanceMessagingService`` over a mocked manager facade.

    R-c: the service reaches the adapter through the manager's PUBLIC
    ``checkpointer`` property (the private reach-through is gone). The
    stub therefore exposes the PUBLIC name — a bare MagicMock would
    auto-fabricate an unrelated attribute for it and the probe would
    never be called (the failure would look like a silent False). The
    private attribute is set too, mirroring the real facade (whose
    ``checkpointer`` property returns ``_checkpointer``).
    """
    manager = MagicMock()
    manager._checkpointer = adapter
    manager.checkpointer = adapter
    return InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(),
    )


class TestRetryPathHasCheckpointProbe:
    """The retry path probes the adapter — it never reads the raw saver.

    ``InstanceMessagingService._has_checkpoint`` is consulted on the
    RETRY path (``instance_messaging.py:5087``) to decide whether the
    interrupted turn resumes from its checkpoint or starts fresh. It
    used to answer that with a FULL saver read; it now answers it with
    the adapter's EXISTS probe.
    """

    async def test_retry_path_calls_probe_and_never_the_raw_saver(self):
        adapter = _StubAdapterWithBothSurfaces(exists=True)
        svc = _service_with_adapter(adapter)

        result = await svc._has_checkpoint("inst-1")

        assert result is True
        assert adapter.probe_calls == ["inst-1"]
        adapter.raw_saver.aget.assert_not_called()

    async def test_retry_path_probe_no_row_returns_false(self):
        adapter = _StubAdapterWithBothSurfaces(exists=False)
        svc = _service_with_adapter(adapter)

        result = await svc._has_checkpoint("inst-1")

        assert result is False
        assert adapter.probe_calls == ["inst-1"]
        adapter.raw_saver.aget.assert_not_called()

    async def test_retry_path_probe_failure_is_fail_soft_false(self):
        """Unchanged contract: a probe failure yields False, never raises."""
        adapter = _StubAdapterWithBothSurfaces(raises=RuntimeError("pool exhausted"))
        svc = _service_with_adapter(adapter)

        result = await svc._has_checkpoint("inst-1")

        assert result is False
        adapter.raw_saver.aget.assert_not_called()


# ── 4. dead-code proof: the manager's message-count helper is gone ─────────


class TestMessageCountHelperRemoved:
    """No message-count helper survives on EITHER class.

    The helper performed a FULL saver read to count messages — the
    same deserialization cost the EXISTS probe removes. It had ZERO
    callers repo-wide (grep evidence in the item report), so it was
    deleted in this item; the instance no longer exists on the manager
    facade (nor on the messaging service).

    RENAME-PROOF (R-d): the assertion scans the LIVE attribute surface
    for ANY attribute whose name mentions BOTH "message" and "count".
    The previous pin checked ONE spelling via ``hasattr`` + a
    module-source grep — a helper renamed to, say, ``_count_messages``
    would have kept that pin green while the dead code lived on, and
    the source grep constrained exactly one string. The pattern below
    survives a rename: it fails for any message-count helper, whatever
    it is called.
    """

    @pytest.mark.parametrize(
        "cls",
        [InstanceManager, InstanceMessagingService],
        ids=lambda cls: cls.__name__,
    )
    def test_no_message_count_helper_on_the_class(self, cls):
        offenders = sorted(
            name
            for name in dir(cls)
            if "message" in name.lower() and "count" in name.lower()
        )
        assert not offenders, (
            f"{cls.__name__} must carry NO message-count helper — found "
            f"{offenders}. The helper was deleted as dead code; a rename "
            "must not smuggle it back (this assertion is rename-proof)."
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
