"""SCOPE (c) ITEM 1 — the retention lane's pins.

The retention lane (fix/post-outage-recovery, commit series ITEM 1)
adds two bounded legs:

* **Op B2 — the terminal-thread FULL reap** (``_reap_terminal_threads``):
  a TIGHTER-age sibling of Op B. A TERMINAL instance past the age
  bound loses its whole CHECKPOINT FOOTPRINT (checkpoints + writes +
  blobs) while its instance row and its conversation survive.
* **the BACKSTOP job** (``run_backstop_prune``, registered UN_GATED as
  ``checkpoint_backstop_prune``): a bounded per-thread prune + blob
  reclaim on a wall-clock cadence that does NOT consult the idle gate.

THE PINS, AND WHY EACH EXISTS:

* the **''-invariant** pin — ``checkpoint_ns`` is ``''`` on EVERY row
  BY CONSTRUCTION (LangGraph's root namespace). A fixture with a
  non-'' namespace row demonstrates the LEAK that motivates the
  invariant: the retention predicates are namespace-scoped, so a row
  written under any other namespace is INVISIBLE to the root-namespace
  prune. The pin guards the invariant by showing what would escape.
* the **uuid6-ordering** pin — the keep-latest-N correctness depends
  on ``checkpoint_id`` being a uuid6 (the timestamp is in the high
  bits, so the lexicographic DESC the adapter uses IS chronological).
  The pin asserts both the ordering predicate and the id shape.
* the **readiness-sentinel exclusion** pin — the
  ``__ensemble_readiness_probe__`` thread must stay invisible to the
  thread-level sweeps.
* the **validated-join** pin — the new reap predicate must PROVABLY
  MATCH rows on the REAL data shapes (``''`` ns, channel-mint
  versions, uuid6 ids, TEXT timestamps on SQLite) — a silently-dead
  rule is the failure this guards.
* the **starvation** pin — on a CONTINUOUSLY BUSY nest the backstop
  must still prune within its bound (the measured third bloat-class
  recurrence: 13GB / 94k rows after 47h of missed cycles).

Harness: a REAL ``AsyncSqliteSaver`` + the REAL
``SqliteCheckpointerAdapter`` over a real-shaped checkpoint store, and
the REAL ``SQLModelInstanceRepository`` over an in-memory SQLite. The
PG-backed blob pins use the repo's disposable-database harness (they
skip loudly when PostgreSQL is unreachable — a mock is never
substituted for the real saver).

FAIL-HONEST: the pins that assert the NEW behaviour are RED at the
pre-lane base — verified in a throwaway /tmp worktree, removed +
pruned afterwards.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from daemon.checkpoint_adapter import SqliteCheckpointerAdapter
from daemon.config import PersistenceConfig
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.instance_ui_prefs.repository import (
    InstanceUiPrefsRepository,
)
from daemon.services import maintenance as mt

#: The readiness-probe sentinel thread id (the daemon's readiness
#: probe). It must never surface in a thread-level sweep.
SENTINEL_THREAD_ID = "__ensemble_readiness_probe__"


# ─────────────────────────────────────────────────────────────────────────────
# Real-shape helpers
# ─────────────────────────────────────────────────────────────────────────────


def _uuid6(ts_ms: int, seq: int = 0) -> str:
    """A uuid6-shaped id: unix_ts_ms in the high 48 bits.

    uuid6 layout (RFC 9562): bits 127..80 = unix_ts_ms (48 bits),
    79..76 = version (6), 75..64 = clock_seq (14 bits), 63..62 =
    variant (10), 61..0 = node (62 bits). The TIMESTAMP IS IN THE HIGH
    BITS — which is exactly why the adapter's lexicographic
    ``ORDER BY checkpoint_id DESC`` is chronological.

    Built by hand (not ``uuid.uuid6()``) so the pin runs on every
    supported Python and so the layout is explicit at the call site.
    """
    clock_seq = seq & 0x3FFF
    variant = 0b10
    node = 0x123456789ABCDEF & ((1 << 62) - 1)
    value = (
        ((ts_ms & ((1 << 48) - 1)) << 80)
        | (6 << 76)
        | (clock_seq << 64)
        | (variant << 62)
        | node
    )
    return str(uuid.UUID(int=value))


def _channel_mint_version(counter: int) -> str:
    """A channel-mint version string — the REAL shape (measured on
    prod 2026-11-24: ``00000000000000000000000000000015.0.04321847323694494``).

    It is NOT a uuid and NOT an ns-path — which is why the blob→parent
    join cannot be written against it.
    """
    return f"{counter:032d}.0.{counter * 0.04321847323694494:.18f}"


def _iso_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@pytest.fixture(autouse=True)
def _real_langgraph():
    """Evict the root-conftest langgraph mocks so the REAL
    ``AsyncSqliteSaver`` loads (the house pattern — see
    ``tests/unit/checkpoint_adapter/test_direct_anti_join.py``)."""
    from tests.helpers.checkpoint_prune_pg import (
        evict_langgraph_mocks,
        restore_langgraph_mocks,
    )

    saved = evict_langgraph_mocks()
    try:
        yield
    finally:
        restore_langgraph_mocks(saved)


@pytest.fixture
async def sqlite_store(tmp_path):
    """A REAL ``AsyncSqliteSaver`` + the REAL SQLite adapter over a
    real-shaped checkpoint store (the saver owns the schema)."""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = str(tmp_path / "retention_pins.db")
    conn = await aiosqlite.connect(db_path)
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    adapter = SqliteCheckpointerAdapter(saver)
    try:
        yield adapter
    finally:
        await conn.close()


@pytest.fixture
def instance_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


async def _seed_checkpoint_rows(
    adapter,
    *,
    thread_id: str,
    count: int,
    ns: str = "",
    start_ms: int = 1_700_000_000_000,
    channel: str = "messages",
) -> list[str]:
    """Seed ``count`` REAL-SHAPE checkpoint rows for one (thread, ns).

    Real shape (measured on prod 2026-11-24): ``checkpoint_ns`` is
    ``''`` (never NULL), ``checkpoint_id`` is a uuid6, the payload
    carries ``channel_versions`` whose values are channel-mint version
    strings, and ``ts`` is an ISO-8601 string.
    """
    ids: list[str] = []
    async with adapter._saver.lock:
        for i in range(count):
            cid = _uuid6(start_ms + i * 1000, seq=i)
            ids.append(cid)
            version = _channel_mint_version(i + 1)
            payload = json.dumps(
                {
                    "v": 1,
                    "id": cid,
                    "ts": _iso_ts(
                        datetime.fromtimestamp(
                            (start_ms + i * 1000) / 1000, tz=timezone.utc
                        )
                    ),
                    "channel_values": {},
                    "channel_versions": {channel: version},
                    "versions_seen": {},
                }
            ).encode()
            await adapter._saver.conn.execute(
                "INSERT INTO checkpoints "
                "(thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, "
                "metadata) VALUES (?, ?, ?, ?, ?, ?)",
                (thread_id, ns, cid, None, payload, b"{}"),
            )
        await adapter._saver.conn.commit()
    return ids


def _insert_instance(
    engine,
    instance_id: str,
    *,
    status: str,
    updated_at: datetime,
    parent_id: str | None = None,
    agent_id: str = "leader",
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, last_activity_at, version,
                     parent_id)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status,
                     :project_id, :created_at, :updated_at,
                     :last_activity_at, :version, :parent_id)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": agent_id,
                "agent_dir": f"agents/{agent_id}",
                "status": status,
                "project_id": "test-project",
                "created_at": updated_at.isoformat(),
                "updated_at": updated_at.isoformat(),
                "last_activity_at": updated_at.isoformat(),
                "version": 1,
                "parent_id": parent_id,
            },
        )


def _make_job(adapter, instance_engine, *, ui_prefs_repo=None):
    config = PersistenceConfig()
    config.checkpoint_max_per_thread = 3
    return mt.CheckpointCleanupJob(
        config=config,
        checkpointer=adapter,
        instance_repo=SQLModelInstanceRepository(instance_engine),
        ui_prefs_repo=ui_prefs_repo,
    )


# ─────────────────────────────────────────────────────────────────────────────
# The ''-invariant (the leak the invariant prevents)
# ─────────────────────────────────────────────────────────────────────────────


class TestEmptyNsInvariant:
    @pytest.mark.asyncio
    async def test_a_non_empty_ns_row_escapes_the_root_ns_prune(
        self, sqlite_store,
    ):
        """THE LEAK, DEMONSTRATED.

        The retention predicates are NAMESPACE-SCOPED: Op D prunes one
        ``(thread_id, checkpoint_ns)`` pair at a time and Op E's blob
        anti-join matches on the namespace too. A row written under
        any namespace other than ``''`` is therefore INVISIBLE to the
        root-namespace prune — it survives untouched.

        That is WHY ``checkpoint_ns`` is ``''`` on every row BY
        CONSTRUCTION (LangGraph's root namespace) and why the writer
        must never write or backfill it: a non-'' row is a row the
        retention lane can never reclaim.
        """
        # The root-namespace pair: five checkpoints (over the cap).
        await _seed_checkpoint_rows(
            sqlite_store, thread_id="thread-root", count=5, ns="",
        )
        # A row under a DIFFERENT namespace — the leak.
        await _seed_checkpoint_rows(
            sqlite_store, thread_id="thread-root", count=1, ns="child:x",
        )

        excess = await sqlite_store.find_excess_checkpoint_groups(3)
        pairs = {(p[0], p[1]) for p in excess}
        assert ("thread-root", "") in pairs
        # The non-'' namespace is NOT over the cap — and, more to the
        # point, it is a DIFFERENT pair: the root-ns prune cannot
        # touch it.
        assert ("thread-root", "child:x") not in pairs

        # Run the Op D prune for the ROOT pair only.
        keep = set(await sqlite_store.get_checkpoint_ids("thread-root", "", 3))
        assert len(keep) == 3
        deleted = await sqlite_store.delete_checkpoints_excluding(
            "thread-root", "", keep,
        )
        assert deleted == 2

        # The leak: the non-'' row SURVIVED the root-ns prune.
        remaining = await sqlite_store.find_all_thread_ns_pairs()
        counts = {(p[0], p[1]): p[2] for p in remaining}
        assert counts[("thread-root", "")] == 3
        assert counts[("thread-root", "child:x")] == 1, (
            "the non-'' namespace row must survive — that IS the leak the "
            "invariant prevents"
        )

    @pytest.mark.asyncio
    async def test_the_real_shape_rows_carry_the_empty_ns(
        self, sqlite_store,
    ):
        """The fixture rows carry ``''`` (never NULL) — the shape the
        whole retention lane is written against."""
        await _seed_checkpoint_rows(
            sqlite_store, thread_id="t", count=2, ns="",
        )
        async with sqlite_store._saver.lock:
            rows = await sqlite_store._saver.conn.execute(
                "SELECT checkpoint_ns, typeof(checkpoint_ns) FROM checkpoints"
            )
            fetched = await rows.fetchall()
        assert fetched, "fixture must have rows"
        for ns, ns_type in fetched:
            assert ns == ""
            assert ns_type == "text"

    @pytest.mark.asyncio
    async def test_the_channel_mint_version_is_not_a_join_key(self):
        """The blob ``version`` is a channel-mint string — NOT a uuid and
        NOT an ns-path — so the blob→parent join cannot be written
        against it (the reason the retention lane keys on the
        thread→instance join ALONE)."""
        version = _channel_mint_version(15)
        assert version.startswith("00000000000000000000000000000015.")
        assert "00000000-0000" not in version
        assert version != ""


# ─────────────────────────────────────────────────────────────────────────────
# The uuid6-ordering dependency
# ─────────────────────────────────────────────────────────────────────────────


class TestUuid6OrderingDependency:
    @pytest.mark.asyncio
    async def test_keep_latest_n_keeps_the_newest_by_uuid6_ordering(
        self, sqlite_store,
    ):
        """The keep-latest-N correctness depends on ``checkpoint_id``
        being a uuid6 — the timestamp is in the HIGH bits, so the
        adapter's ``ORDER BY checkpoint_id DESC`` is chronological.

        The pin asserts the ORDERING PREDICATE (the three survivors
        are the three newest by the DECODED timestamp) and the ID
        SHAPE (the uuid version nibble is 6).
        """
        base_ms = 1_700_000_000_000
        ids = await _seed_checkpoint_rows(
            sqlite_store, thread_id="t", count=5, start_ms=base_ms,
        )
        for cid in ids:
            assert uuid.UUID(cid).version == 6, (
                f"{cid} is not a uuid6 — the ordering dependency is broken"
            )

        keep = await sqlite_store.get_checkpoint_ids("t", "", 3)
        assert len(keep) == 3
        # The DECODED timestamps: the survivors must be the three
        # newest (the lexicographic DESC == chronological claim).
        decoded = sorted(uuid.UUID(c).time for c in keep)
        all_decoded = sorted(uuid.UUID(c).time for c in ids)
        assert decoded == all_decoded[-3:]

    @pytest.mark.asyncio
    async def test_the_uuid6_layout_puts_the_timestamp_in_the_high_bits(
        self, sqlite_store,
    ):
        """The layout itself: a later mint sorts LATER lexicographically
        — the property the adapter's DESC ordering relies on."""
        early = _uuid6(1_700_000_000_000)
        late = _uuid6(1_700_000_000_000 + 60_000)
        assert early < late
        assert uuid.UUID(early).time < uuid.UUID(late).time


# ─────────────────────────────────────────────────────────────────────────────
# The readiness-sentinel exclusion
# ─────────────────────────────────────────────────────────────────────────────


class TestReadinessSentinelExclusion:
    @pytest.mark.asyncio
    async def test_the_sentinel_is_invisible_to_the_thread_sweeps(
        self, sqlite_store, instance_engine,
    ):
        """The readiness probe writes ``checkpoint_writes`` ONLY — it
        never creates a ``checkpoints`` row — so it cannot appear in
        the pair enumeration the thread-level sweeps consume (Op D, Op
        E, the backstop).

        The PG orphan scan carries an EXPLICIT exclusion as well
        (defense-in-depth) — pinned by the source assertion below.
        """
        # The sentinel thread carries NO checkpoints row (write-only).
        async with sqlite_store._saver.lock:
            await sqlite_store._saver.conn.execute(
                "INSERT INTO writes (thread_id, checkpoint_ns, "
                "checkpoint_id, task_id, idx, channel, type, value) "
                "VALUES (?, '', ?, 'task', 0, 'messages', NULL, X'00')",
                (SENTINEL_THREAD_ID, _uuid6(1_700_000_000_000)),
            )
            await sqlite_store._saver.conn.commit()

        pairs = await sqlite_store.find_all_thread_ns_pairs()
        assert all(p[0] != SENTINEL_THREAD_ID for p in pairs)
        excess = await sqlite_store.find_excess_checkpoint_groups(3)
        assert all(p[0] != SENTINEL_THREAD_ID for p in excess)

        # Op B2 keys on the INSTANCE repo — the sentinel has no instance
        # row, so it can never be a reap candidate.
        job = _make_job(sqlite_store, instance_engine)
        summary = await job._reap_terminal_threads()
        assert summary.candidates == 0
        assert summary.reaped_threads == 0

    def test_the_pg_orphan_scan_carries_the_sentinel_exclusion(self):
        """The PG adapter's ``list_thread_ids`` excludes the sentinel
        explicitly (defense-in-depth: the probe design could change)."""
        import inspect

        from daemon.checkpoint_adapter import PostgresCheckpointerAdapter

        source = inspect.getsource(PostgresCheckpointerAdapter.list_thread_ids)
        assert "CHECKPOINT_SENTINEL_THREAD_ID" in source


# ─────────────────────────────────────────────────────────────────────────────
# The validated-join pin — the new reap predicate MATCHES real rows
# ─────────────────────────────────────────────────────────────────────────────


class TestTerminalReapPredicate:
    @pytest.mark.asyncio
    async def test_the_predicate_matches_a_real_shape_terminal_thread(
        self, sqlite_store, instance_engine,
    ):
        """THE VALIDATED-JOIN PIN: the new predicate PROVABLY MATCHES
        rows on the real data shapes (``''`` ns, uuid6 ids, TEXT
        timestamps on SQLite).

        A terminal instance past the bound loses its whole checkpoint
        footprint; the instance ROW survives (the conversation lives in
        ``message_queue``).
        """
        instance_id = "aged-terminal"
        old = datetime.now(timezone.utc) - timedelta(hours=30)
        _insert_instance(
            instance_engine, instance_id,
            status=InstanceStatus.COMPLETED.value, updated_at=old,
        )
        await _seed_checkpoint_rows(
            sqlite_store, thread_id=instance_id, count=5, ns="",
        )

        job = _make_job(sqlite_store, instance_engine)
        summary = await job._reap_terminal_threads()

        assert summary.skipped_reason is None, summary
        assert summary.candidates == 1, summary
        assert summary.reaped_threads == 1, summary
        assert summary.errors == 0, summary

        # The thread's checkpoint footprint is GONE …
        pairs = await sqlite_store.find_all_thread_ns_pairs()
        assert all(p[0] != instance_id for p in pairs)

        # … and the instance ROW survives.
        with Session(instance_engine) as session:
            row = session.get(Instance, instance_id)
        assert row is not None
        assert row.status == InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_a_non_terminal_instance_is_never_reaped(
        self, sqlite_store, instance_engine,
    ):
        instance_id = "live-running"
        old = datetime.now(timezone.utc) - timedelta(hours=30)
        _insert_instance(
            instance_engine, instance_id,
            status=InstanceStatus.RUNNING.value, updated_at=old,
        )
        await _seed_checkpoint_rows(
            sqlite_store, thread_id=instance_id, count=5, ns="",
        )
        job = _make_job(sqlite_store, instance_engine)
        summary = await job._reap_terminal_threads()
        assert summary.reaped_threads == 0, summary
        pairs = await sqlite_store.find_all_thread_ns_pairs()
        assert any(p[0] == instance_id for p in pairs)

    @pytest.mark.asyncio
    async def test_a_recently_finished_instance_is_not_reaped(
        self, sqlite_store, instance_engine,
    ):
        """Inside the bound ⇒ untouched (the age bound is the policy
        knob the leader adjudicates)."""
        instance_id = "fresh-terminal"
        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        _insert_instance(
            instance_engine, instance_id,
            status=InstanceStatus.COMPLETED.value, updated_at=recent,
        )
        await _seed_checkpoint_rows(
            sqlite_store, thread_id=instance_id, count=5, ns="",
        )
        job = _make_job(sqlite_store, instance_engine)
        summary = await job._reap_terminal_threads()
        assert summary.candidates == 0, summary
        assert summary.reaped_threads == 0, summary

    @pytest.mark.asyncio
    async def test_a_pinned_instance_is_never_reaped(
        self, sqlite_store, instance_engine,
    ):
        """The pinned subtree is protected — a pinned instance's thread
        survives the reap."""
        instance_id = "pinned-terminal"
        old = datetime.now(timezone.utc) - timedelta(hours=30)
        _insert_instance(
            instance_engine, instance_id,
            status=InstanceStatus.COMPLETED.value, updated_at=old,
        )
        await _seed_checkpoint_rows(
            sqlite_store, thread_id=instance_id, count=5, ns="",
        )
        prefs_repo = InstanceUiPrefsRepository(instance_engine)
        prefs_repo.upsert(instance_id, pinned=True)

        job = _make_job(
            sqlite_store, instance_engine, ui_prefs_repo=prefs_repo,
        )
        summary = await job._reap_terminal_threads()
        assert summary.reaped_threads == 0, summary
        pairs = await sqlite_store.find_all_thread_ns_pairs()
        assert any(p[0] == instance_id for p in pairs)

    @pytest.mark.asyncio
    async def test_the_kill_switch_disables_the_leg(
        self, sqlite_store, instance_engine, monkeypatch,
    ):
        instance_id = "aged-terminal-killed"
        old = datetime.now(timezone.utc) - timedelta(hours=30)
        _insert_instance(
            instance_engine, instance_id,
            status=InstanceStatus.COMPLETED.value, updated_at=old,
        )
        await _seed_checkpoint_rows(
            sqlite_store, thread_id=instance_id, count=5, ns="",
        )
        monkeypatch.setenv(mt.TERMINAL_REAP_KILL_SWITCH_ENV, "0")
        job = _make_job(sqlite_store, instance_engine)
        summary = await job._reap_terminal_threads()
        assert summary.skipped_reason == "kill_switch", summary
        assert summary.reaped_threads == 0, summary
        pairs = await sqlite_store.find_all_thread_ns_pairs()
        assert any(p[0] == instance_id for p in pairs)

    @pytest.mark.asyncio
    async def test_the_batch_bound_limits_one_cycle(
        self, sqlite_store, instance_engine, monkeypatch,
    ):
        monkeypatch.setenv(mt.TERMINAL_REAP_BATCH_ENV, "2")
        old = datetime.now(timezone.utc) - timedelta(hours=30)
        for i in range(5):
            iid = f"aged-{i}"
            _insert_instance(
                instance_engine, iid,
                status=InstanceStatus.COMPLETED.value, updated_at=old,
            )
            await _seed_checkpoint_rows(
                sqlite_store, thread_id=iid, count=2, ns="",
            )
        job = _make_job(sqlite_store, instance_engine)
        summary = await job._reap_terminal_threads()
        assert summary.candidates == 5, summary
        assert summary.reaped_threads == 2, summary


# ─────────────────────────────────────────────────────────────────────────────
# The starvation pin — the backstop prunes on a busy nest
# ─────────────────────────────────────────────────────────────────────────────


class TestStarvationBackstop:
    @pytest.mark.asyncio
    async def test_the_backstop_prunes_on_a_busy_nest(
        self, sqlite_store, instance_engine,
    ):
        """THE STARVATION PIN: on a CONTINUOUSLY BUSY nest the backstop
        still prunes within its bound.

        The contrast is the point: the IDLE-GATED daily cycle does
        NOT run on a busy nest (that is the measured starvation), while
        the UN_GATED backstop does.
        """
        await _seed_checkpoint_rows(
            sqlite_store, thread_id="busy-thread", count=9, ns="",
        )
        job = _make_job(sqlite_store, instance_engine)

        service = mt.MaintenanceService(check_interval_minutes=1)
        service.set_job_queue_service(_busy_job_queue_service())
        service.set_task_repository(_busy_task_repository())
        service.register(
            "checkpoint_backstop_prune", 1.0, job.run_backstop_prune,
        )
        await service._run_pending_jobs()

        pairs = await sqlite_store.find_all_thread_ns_pairs()
        counts = {(p[0], p[1]): p[2] for p in pairs}
        assert counts.get(("busy-thread", ""), 0) == 3, (
            "the backstop must have pruned the busy nest's thread"
        )

    @pytest.mark.asyncio
    async def test_an_idle_gated_job_does_not_run_on_a_busy_nest(
        self, sqlite_store, instance_engine,
    ):
        """THE CONTRAST: the same busy nest starves an IDLE-GATED job —
        the condition the backstop exists to survive."""
        await _seed_checkpoint_rows(
            sqlite_store, thread_id="busy-thread-2", count=9, ns="",
        )
        job = _make_job(sqlite_store, instance_engine)

        service = mt.MaintenanceService(check_interval_minutes=1)
        service.set_job_queue_service(_busy_job_queue_service())
        service.set_task_repository(_busy_task_repository())
        service.register(
            "checkpoint_cleanup", 1.0, job.run_backstop_prune,
        )
        await service._run_pending_jobs()

        pairs = await sqlite_store.find_all_thread_ns_pairs()
        counts = {(p[0], p[1]): p[2] for p in pairs}
        assert counts.get(("busy-thread-2", ""), 0) == 9, (
            "the idle-gated job must have been skipped on a busy nest"
        )

    def test_the_backstop_job_is_registered_un_gated(self):
        """The policy table is the SINGLE source of the idle-gate
        classification — the backstop must be UN_GATED there (an
        unknown job name defaults to IDLE_GATED)."""
        assert (
            mt.JOB_IDLE_POLICIES["checkpoint_backstop_prune"]
            == mt.IdlePolicy.UN_GATED
        )

    @pytest.mark.asyncio
    async def test_the_backstop_respects_its_pair_budget(
        self, sqlite_store, instance_engine, monkeypatch,
    ):
        monkeypatch.setenv(mt.BACKSTOP_PAIR_BUDGET_ENV, "1")
        for i in range(3):
            await _seed_checkpoint_rows(
                sqlite_store, thread_id=f"budgeted-{i}", count=9, ns="",
            )
        job = _make_job(sqlite_store, instance_engine)
        summary = await job.run_backstop_prune()
        assert summary.excess_pairs == 3, summary
        assert summary.pruned_pairs == 1, summary

        pairs = await sqlite_store.find_all_thread_ns_pairs()
        counts = {(p[0], p[1]): p[2] for p in pairs}
        assert counts[("budgeted-0", "")] == 3
        assert counts[("budgeted-1", "")] == 9
        assert counts[("budgeted-2", "")] == 9

    @pytest.mark.asyncio
    async def test_the_backstop_never_deletes_an_instance_row(
        self, sqlite_store, instance_engine,
    ):
        """The backstop is a PRUNE, not a reap: it never deletes an
        instance row (no Op A/B/C semantics)."""
        instance_id = "survivor"
        old = datetime.now(timezone.utc) - timedelta(hours=30)
        _insert_instance(
            instance_engine, instance_id,
            status=InstanceStatus.COMPLETED.value, updated_at=old,
        )
        await _seed_checkpoint_rows(
            sqlite_store, thread_id=instance_id, count=9, ns="",
        )
        job = _make_job(sqlite_store, instance_engine)
        await job.run_backstop_prune()
        with Session(instance_engine) as session:
            row = session.get(Instance, instance_id)
        assert row is not None

    @pytest.mark.asyncio
    async def test_the_backstop_kill_switch_is_a_no_op(
        self, sqlite_store, instance_engine, monkeypatch,
    ):
        monkeypatch.setenv(mt.BACKSTOP_KILL_SWITCH_ENV, "0")
        await _seed_checkpoint_rows(
            sqlite_store, thread_id="killed-thread", count=9, ns="",
        )
        job = _make_job(sqlite_store, instance_engine)
        summary = await job.run_backstop_prune()
        assert summary.skipped_reason == "kill_switch", summary
        pairs = await sqlite_store.find_all_thread_ns_pairs()
        counts = {(p[0], p[1]): p[2] for p in pairs}
        assert counts[("killed-thread", "")] == 9


def _busy_job_queue_service() -> Any:
    """A job-queue service whose idle probe reports BUSY."""
    repo = MagicMock()
    repo.has_active_non_deferred_work = MagicMock(return_value=True)
    repo.list_all_pending = MagicMock(return_value=[{"id": "x"}])
    repo.find_processing_jobs = MagicMock(return_value=[{"id": "y"}])
    service = MagicMock()
    service._repository = repo
    return service


def _busy_task_repository() -> Any:
    repo = MagicMock()
    repo.has_active_non_deferred_work = MagicMock(return_value=True)
    return repo


# ─────────────────────────────────────────────────────────────────────────────
# The policy-options block (ITEM 1c)
# ─────────────────────────────────────────────────────────────────────────────


class TestPolicyOptionsBlock:
    def test_the_module_docstring_carries_the_policy_options(self):
        """The three policy knobs the commission did NOT decide are
        named in the module docstring, with the statement that the
        implementation changes nothing for the first two."""
        doc = mt.__doc__ or ""
        assert "POLICY OPTIONS" in doc
        assert "Keep-N depth" in doc
        assert "Per-thread vs global caps" in doc
        assert "Terminal-thread full-reap aggressiveness" in doc
        assert "THIS MODULE CHANGES NOTHING HERE" in doc

    def test_the_docstring_carries_the_measured_escape_diagnosis(self):
        """The escape diagnosis (why the churn class escapes Op B and
        Op C) is recorded IN CODE, not only in the report."""
        doc = mt.__doc__ or ""
        assert "RETENTION ESCAPE" in doc
        assert "escape is a CADENCE + AGE-ORDERING escape" in doc


# ─────────────────────────────────────────────────────────────────────────────
# The ''-invariant on the BLOB side (real PostgreSQL)
# ─────────────────────────────────────────────────────────────────────────────


PG_FIXTURE_SKIP_REASON = (
    "PostgreSQL not available at the PG_TEST_* DSN — the blob-side "
    "namespace-scope pin is skipped. A mock is never substituted for "
    "the real saver."
)


@pytest.fixture(scope="module")
def pg_db_dsn():
    """A disposable PG database for the blob-side fixture."""
    from tests.helpers.checkpoint_prune_pg import (
        create_disposable_db,
        drop_database,
        require_postgres,
    )

    require_postgres()
    loop = asyncio.new_event_loop()
    name, dsn = loop.run_until_complete(create_disposable_db())
    try:
        yield name, dsn
    finally:
        loop.run_until_complete(drop_database(name))


@pytest.fixture
async def pg_adapter(pg_db_dsn):
    from tests.helpers.checkpoint_prune_pg import real_pg_checkpointer

    name, dsn = pg_db_dsn
    async with real_pg_checkpointer(name, dsn) as (_saver, _pool, adapter):
        yield adapter


def _pg_checkpoint_payload(channel_versions: dict) -> str:
    return json.dumps(
        {
            "v": 1,
            "id": str(uuid.uuid4()),
            "ts": _iso_ts(datetime.now(timezone.utc)),
            "channel_values": {},
            "channel_versions": channel_versions,
            "versions_seen": {},
        }
    )


class TestBlobAntiJoinIsNamespaceScoped:
    """Op E's anti-join is NAMESPACE-SCOPED on BOTH sides.

    A blob under a non-'' namespace is invisible to the root-namespace
    reclaim — the blob-side twin of the row-side leak. The pin proves
    it against a REAL PostgreSQL with the REAL production predicate
    (``_BLOB_ANTI_JOIN_PREDICATE``) — not a copy of it.
    """

    @pytest.mark.asyncio
    async def test_a_non_empty_ns_blob_is_invisible_to_the_root_ns_reclaim(
        self, pg_adapter,
    ):
        thread_id = "ns-scope-thread"
        root_version = _channel_mint_version(1)
        sub_version = _channel_mint_version(2)

        async with pg_adapter._pool.acquire() as conn:
            # The root-namespace checkpoint (references the root blob).
            await conn.execute(
                "INSERT INTO checkpoints (thread_id, checkpoint_ns, "
                "checkpoint_id, type, checkpoint, metadata) "
                "VALUES ($1, '', $2, $3, $4::jsonb, '{}'::jsonb)",
                thread_id, _uuid6(1_700_000_000_000), None,
                _pg_checkpoint_payload({"messages": root_version}),
            )
            # A checkpoint under a DIFFERENT namespace.
            await conn.execute(
                "INSERT INTO checkpoints (thread_id, checkpoint_ns, "
                "checkpoint_id, type, checkpoint, metadata) "
                "VALUES ($1, 'child:x', $2, $3, $4::jsonb, '{}'::jsonb)",
                thread_id, _uuid6(1_700_000_001_000), None,
                _pg_checkpoint_payload({"messages": sub_version}),
            )
            # Two blobs: one in the root namespace (referenced), one in
            # the other namespace (referenced ONLY by that namespace's
            # checkpoint).
            await conn.execute(
                "INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, "
                "channel, version, type, blob) "
                "VALUES ($1, '', 'messages', $2, 'msgpack', $3)",
                thread_id, root_version, b"\x01root",
            )
            await conn.execute(
                "INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, "
                "channel, version, type, blob) "
                "VALUES ($1, 'child:x', 'messages', $2, 'msgpack', $3)",
                thread_id, sub_version, b"\x01sub",
            )

        # The root-namespace reclaim does NOT touch the other
        # namespace's blob — the same-ns predicate on both sides.
        deleted, _bytes = await pg_adapter.delete_blobs_anti_join(
            thread_id, "",
        )
        assert deleted == 0, (
            "no root-namespace blob is unreferenced — the reclaim must "
            "delete nothing"
        )

        async with pg_adapter._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT checkpoint_ns FROM checkpoint_blobs "
                "WHERE thread_id = $1 ORDER BY checkpoint_ns",
                thread_id,
            )
        assert [r["checkpoint_ns"] for r in rows] == ["", "child:x"], (
            "BOTH blobs survive the root-namespace reclaim — the "
            "other-namespace blob is INVISIBLE to it (the leak the "
            "invariant prevents)"
        )

    @pytest.mark.asyncio
    async def test_the_root_ns_reclaim_frees_the_orphaned_root_blob(
        self, pg_adapter,
    ):
        """The POSITIVE twin: a root-namespace blob whose reference is
        gone IS reclaimed — the ''-namespace rows are the ones the
        retention lane can actually reclaim."""
        thread_id = "root-orphan-thread"
        kept_version = _channel_mint_version(3)
        orphan_version = _channel_mint_version(4)

        async with pg_adapter._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO checkpoints (thread_id, checkpoint_ns, "
                "checkpoint_id, type, checkpoint, metadata) "
                "VALUES ($1, '', $2, $3, $4::jsonb, '{}'::jsonb)",
                thread_id, _uuid6(1_700_000_002_000), None,
                _pg_checkpoint_payload({"messages": kept_version}),
            )
            await conn.execute(
                "INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, "
                "channel, version, type, blob) "
                "VALUES ($1, '', 'messages', $2, 'msgpack', $3)",
                thread_id, kept_version, b"\x01kept",
            )
            await conn.execute(
                "INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, "
                "channel, version, type, blob) "
                "VALUES ($1, '', 'messages', $2, 'msgpack', $3)",
                thread_id, orphan_version, b"\x01orphan",
            )

        deleted, _bytes = await pg_adapter.delete_blobs_anti_join(
            thread_id, "",
        )
        assert deleted == 1

        async with pg_adapter._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT version FROM checkpoint_blobs WHERE thread_id = $1",
                thread_id,
            )
        assert [r["version"] for r in rows] == [kept_version]


class TestBackstopRegistrationSeam:
    """The registration seam (the knobs the manager reads at boot).

    The manager's registration block calls ``backstop_registration_params()``
    rather than reading the env knobs inline — so the knob SHAPE (the
    arity of the flag/int reads, the interval floor) is pinned by a
    test that CALLS the seam. (An inline read with the wrong arity is a
    TypeError at manager-initialize time — hours away from the edit
    that caused it.)
    """

    def test_the_default_registration_is_enabled_on_the_hour(
        self, monkeypatch,
    ):
        monkeypatch.delenv(mt.BACKSTOP_KILL_SWITCH_ENV, raising=False)
        monkeypatch.delenv(mt.BACKSTOP_INTERVAL_ENV, raising=False)
        enabled, interval = mt.backstop_registration_params()
        assert enabled is True
        assert interval == mt.DEFAULT_BACKSTOP_INTERVAL_MINUTES

    def test_the_kill_switch_disables_the_registration(self, monkeypatch):
        monkeypatch.setenv(mt.BACKSTOP_KILL_SWITCH_ENV, "0")
        enabled, _interval = mt.backstop_registration_params()
        assert enabled is False

    def test_the_interval_is_floored_at_one_minute(self, monkeypatch):
        monkeypatch.setenv(mt.BACKSTOP_INTERVAL_ENV, "0")
        _enabled, interval = mt.backstop_registration_params()
        assert interval == 1, (
            "a 0 interval means 'never due' under the service's disable "
            "convention — the kill switch is the way to express 'off'"
        )

    def test_the_manager_registers_the_backstop_through_the_seam(self):
        """The manager's registration block uses the seam (a source
        pin — the wiring itself lives in a deep initialize() path)."""
        import inspect

        from daemon import manager as manager_mod

        source = inspect.getsource(manager_mod)
        assert "backstop_registration_params()" in source
        assert '"checkpoint_backstop_prune"' in source
        assert "checkpoint_cleanup.run_backstop_prune" in source
