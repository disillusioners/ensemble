"""BINDING GATE — Phase 1 C3 real-saver integration test (PR4).

Tracking note 9 (approver, 2026-08-25): "Real-saver integration test is a
BINDING gate before PR4 merge/destructive enable." This file BLOCKS the
PR4 merge AND any ``CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1`` enable.

HONESTY CONTRACT: "real saver" means a REAL ``AsyncPostgresSaver``
exercising REAL blob storage — a real psycopg connection + ``setup()``
schema, a real ``asyncpg`` pool, the REAL
``PostgresCheckpointerAdapter`` (production shape, mirroring
``daemon/persistence.py::create_postgres_checkpointer``), driven by a
real LangGraph ``StateGraph`` (real ``add_messages`` channel + a
non-primitive accumulation channel, both persisted as blobs) and by real
``aput`` calls that build ``_DeltaSnapshot`` chains. If PostgreSQL is
unreachable these tests SKIP LOUDLY — a mock is never substituted and a
skip never counts as GREEN for the merge gate.

Coverage (phase1-plan.md §C3 + §9 mapping):

  * write → retention prune → blob prune → aget/resume reconstruction
    (messages + non-primitive channel, across a simulated process
    restart);
  * ``_DeltaSnapshot`` chains: snapshot blobs referenced by remaining
    checkpoints SURVIVE; orphaned snapshot blobs die; the delta-channel
    seed reconstruction still works post-prune;
  * fail-safe zero-refs: schema-drifted thread → ERROR log + ZERO rows
    deleted even with destructive armed;
  * concurrent aput during a destructive prune → the new checkpoint's
    blob is preserved (atomic blob+checkpoint commit makes the
    anti-join concurrency-safe);
  * dry-run default deletes NOTHING (blob rows byte-identical);
  * SQLite backend no-ops with a WARNING.
"""
from __future__ import annotations

import asyncio
import json
import logging
import operator
import uuid
from typing import Annotated, Any

import pytest

from tests.helpers.checkpoint_prune_pg import (
    ADMIN_DSN,
    evict_langgraph_mocks,
    restore_langgraph_mocks,
)


@pytest.fixture(autouse=True)
def _real_langgraph():
    """Evict the root-conftest langgraph mocks for this module (repo pattern)."""
    saved = evict_langgraph_mocks()
    try:
        yield
    finally:
        restore_langgraph_mocks(saved)


@pytest.fixture(autouse=True)
def _default_ladder(monkeypatch):
    """Start every test in the shipped default state (dry-run, destructive off)."""
    monkeypatch.delenv("CHECKPOINT_BLOB_PRUNE_DRY_RUN", raising=False)
    monkeypatch.delenv("CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE", raising=False)


def _arm_destructive(monkeypatch):
    monkeypatch.setenv("CHECKPOINT_BLOB_PRUNE_DRY_RUN", "0")
    monkeypatch.setenv("CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE", "1")


# ── fixtures ───────────────────────────────────────────────────────────────────


async def _probe_pg_or_skip():
    import asyncpg

    try:
        conn = await asyncpg.connect(ADMIN_DSN, timeout=5)
        await conn.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"BINDING GATE SKIPPED — PostgreSQL not available at {ADMIN_DSN} "
            f"({type(exc).__name__}: {exc}). Do NOT merge PR4 or enable "
            "CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE on a skip: start PostgreSQL "
            "and re-run tests/integration/checkpoint_prune_real_saver.py."
        )


@pytest.fixture
async def pg_db():
    """Disposable database per test (safe under xdist; never touches shared DBs)."""
    from tests.helpers.checkpoint_prune_pg import create_disposable_db, drop_database

    await _probe_pg_or_skip()
    name, dsn = await create_disposable_db()
    try:
        yield name, dsn
    finally:
        await drop_database(name)


@pytest.fixture
async def stack(pg_db):
    """The production-shaped checkpointer stack on the disposable DB."""
    from tests.helpers.checkpoint_prune_pg import real_pg_checkpointer

    name, dsn = pg_db
    async with real_pg_checkpointer(name, dsn) as (saver, pool, adapter):
        yield saver, pool, adapter


@pytest.fixture
async def pool(stack):
    _saver, pool, _adapter = stack
    return pool


@pytest.fixture
async def adapter(stack):
    _saver, _pool, adapter = stack
    return adapter


@pytest.fixture
async def saver(stack):
    saver, _pool, _adapter = stack
    return saver


def build_graph(saver):
    """Real StateGraph: messages (add_messages) + notes (list accumulation).

    Both channels hold non-primitive values → the PG saver persists them
    as ``checkpoint_blobs`` rows (versions bump per turn).
    """
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph

    class PruneState(MessagesState):
        notes: Annotated[list[dict[str, Any]], operator.add]

    def step(state: PruneState):
        n = len(state.get("notes") or [])
        return {
            "messages": [AIMessage(f"reply-{n}")],
            "notes": [{"turn": n, "filler": "z" * 64}],
        }

    g = StateGraph(PruneState)
    g.add_node("step", step)
    g.add_edge(START, "step")
    g.add_edge("step", END)
    return g.compile(checkpointer=saver)


async def blob_fingerprint(adapter, thread_id: str, ns: str = ""):
    """(channel, version, md5(blob)) set for byte-level assertions."""
    import hashlib

    async with adapter._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT channel, version, type, blob FROM checkpoint_blobs "
            "WHERE thread_id=$1 AND checkpoint_ns=$2 ORDER BY channel, version",
            thread_id,
            ns,
        )
    return {
        (
            r["channel"],
            r["version"],
            r["type"],
            hashlib.md5(r["blob"]).hexdigest() if r["blob"] is not None else None,
        )
        for r in rows
    }


async def referenced_versions(adapter, thread_id: str, ns: str = ""):
    """(channel, version) pairs referenced by REMAINING checkpoint rows."""
    async with adapter._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT e.key AS ch, e.value AS ver "
            "FROM checkpoints c CROSS JOIN LATERAL jsonb_each_text("
            "  CASE WHEN jsonb_typeof(c.checkpoint->'channel_versions') = 'object'"
            "       THEN c.checkpoint->'channel_versions' ELSE '{}'::jsonb END) e "
            "WHERE c.thread_id=$1 AND c.checkpoint_ns=$2",
            thread_id,
            ns,
        )
    return {(r["ch"], r["ver"]) for r in rows}


async def state_values(graph, thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    snap = await graph.aget_state(config)
    return snap.values


def messages_snapshot(values):
    return [
        (m.type, getattr(m, "content", None)) for m in values.get("messages", [])
    ]


# ── the gate tests ────────────────────────────────────────────────────────────


class TestRealSaverWritePruneResume:
    async def test_real_saver_write_retention_prune_blob_prune_resume(
        self, saver, adapter, monkeypatch, caplog
    ):
        """THE core §9 test: real writes → real retention prune (Op D) →
        blob prune (dry-run then destructive) → aget + resume intact."""
        from daemon.constants import CHECKPOINT_MAX_PER_THREAD
        from daemon.services.checkpoint_prune import prune_unreferenced_blobs

        graph = build_graph(saver)
        T = "real-saver-thread-1"
        config = {"configurable": {"thread_id": T}}

        # 1) REAL writes — enough turns that retention can bite.
        for i in range(8):
            from langchain_core.messages import HumanMessage

            await graph.ainvoke(
                {"messages": [HumanMessage(f"turn-{i}")]}, config
            )

        values_before = await state_values(graph, T)
        assert len(values_before["messages"]) >= 16, "history must accumulate"
        fp_before = await blob_fingerprint(adapter, T)
        refs_before = await referenced_versions(adapter, T)
        assert refs_before, "healthy thread must expose channel_versions refs"
        assert fp_before, "real graph writes must produce checkpoint_blobs rows"

        # 2) REAL retention prune (Operation D machinery, keep newest 3) —
        #    this is what orphans blobs.
        keep_ids = set(await adapter.get_checkpoint_ids(T, "", 3))
        assert len(keep_ids) == 3
        await adapter.delete_checkpoints_excluding(T, "", keep_ids)
        await adapter.delete_writes_excluding(T, "", keep_ids)

        refs_after_retention = await referenced_versions(adapter, T)
        fp_before_pairs = {f[:2] for f in fp_before}
        expected_survivors = fp_before_pairs & refs_after_retention
        orphans = fp_before_pairs - refs_after_retention
        assert orphans, "retention prune must orphan some blobs for this test"
        assert expected_survivors, "healthy thread must keep referenced blobs"

        # 3) DRY-RUN (default env): reports would-delete > 0, deletes NOTHING.
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            summary = await prune_unreferenced_blobs(adapter)
        assert summary.dry_run is True
        assert summary.total_deleted == 0
        assert summary.scanned_pairs >= 1
        dry_run_lines = [
            r.getMessage() for r in caplog.records
            if "op=blob_prune" in r.getMessage() and "dry_run=1" in r.getMessage()
            and T[:8] in r.getMessage()
        ]
        assert dry_run_lines, "dry-run must emit a per-pair report line"
        assert fp_before == await blob_fingerprint(adapter, T), (
            "dry-run must not change a single byte of checkpoint_blobs"
        )

        # 4) DESTRUCTIVE (armed): orphans die; every referenced blob survives.
        _arm_destructive(monkeypatch)
        summary2 = await prune_unreferenced_blobs(adapter)
        assert summary2.dry_run is False
        assert summary2.total_deleted == len(orphans)
        fp_after = await blob_fingerprint(adapter, T)
        surviving = {fp[:2] for fp in fp_after}
        assert surviving == expected_survivors, (
            "post-prune blob set must equal (referenced ∩ had-blob) exactly "
            "— refs of primitive channels (inlined, no blob) never counted"
        )

        # 5) aget reconstruction — byte-identical channel state.
        values_after = await state_values(graph, T)
        assert messages_snapshot(values_after) == messages_snapshot(values_before)
        assert values_after["notes"] == values_before["notes"]

        # 6) RESUME — one more real turn through the graph works and grows.
        from langchain_core.messages import HumanMessage

        await graph.ainvoke({"messages": [HumanMessage("after-prune")]}, config)
        values_resumed = await state_values(graph, T)
        assert len(values_resumed["messages"]) == len(values_before["messages"]) + 2
        assert values_resumed["messages"][-1].content == "reply-8"

    async def test_real_saver_kill_safe_restart_reconstruction(
        self, saver, adapter, pg_db, monkeypatch
    ):
        """Process 'killed' → new saver instance on the same DB →
        post-prune state reconstructs and the next turn works."""
        from daemon.services.checkpoint_prune import prune_unreferenced_blobs
        from tests.helpers.checkpoint_prune_pg import real_pg_checkpointer

        graph = build_graph(saver)
        T = "restart-thread"
        config = {"configurable": {"thread_id": T}}
        from langchain_core.messages import HumanMessage

        for i in range(5):
            await graph.ainvoke({"messages": [HumanMessage(f"t{i}")]}, config)

        values_before = await state_values(graph, T)

        # Retention + destructive blob prune (armed).
        keep = set(await adapter.get_checkpoint_ids(T, "", 2))
        await adapter.delete_checkpoints_excluding(T, "", keep)
        await adapter.delete_writes_excluding(T, "", keep)
        _arm_destructive(monkeypatch)
        await prune_unreferenced_blobs(adapter)

        # Simulate process kill: close the ENTIRE stack (psycopg conn +
        # asyncpg pool), then boot a fresh one on the same database.
        # (Leaving the fixture to close the now-dead stack is fine —
        # close() is exception-swallowing by contract.)
        name, dsn = pg_db
        saver.conn = None  # prevent double-close noise on fixture teardown
        await adapter._pool.close()
        async with real_pg_checkpointer(name, dsn) as (saver2, _p2, adapter2):
            graph2 = build_graph(saver2)
            values_restarted = await state_values(graph2, T)
            assert messages_snapshot(values_restarted) == messages_snapshot(values_before)
            assert values_restarted["notes"] == values_before["notes"]

            # The next turn through the restarted process works.
            await graph2.ainvoke(
                {"messages": [HumanMessage("post-restart")]},
                {"configurable": {"thread_id": T}},
            )
            values_final = await state_values(graph2, T)
            assert len(values_final["messages"]) == len(values_before["messages"]) + 2


class TestRealSaverDeltaSnapshotChain:
    """_DeltaSnapshot chains built with REAL aput calls (the exact blob
    path pregel uses for delta channels at snapshot steps: the
    _DeltaSnapshot value is popped out of channel_values into a blob and
    a ``True`` sentinel is inlined)."""

    async def _aput_checkpoint(self, saver, T, ns, parent_id, channel_values,
                               channel_versions, new_versions, cid):
        # checkpoint_id must be lexicographically monotonic (the delta
        # scanner pages with ``checkpoint_id < cursor`` — the same
        # ordering invariant the adapter documents for Operation D).
        ckpt = {
            "v": 1,
            "id": cid,
            "ts": "2026-08-26T00:00:00Z",
            "channel_values": channel_values,
            "channel_versions": channel_versions,
            "versions_seen": {},
        }
        cfg = {
            "configurable": {
                "thread_id": T,
                "checkpoint_ns": ns,
                **({"checkpoint_id": parent_id} if parent_id else {}),
            }
        }
        return await saver.aput(cfg, ckpt, {}, new_versions)

    async def test_delta_chain_snapshot_blob_survives_and_orphan_snapshot_dies(
        self, saver, adapter, monkeypatch
    ):
        from langgraph.checkpoint.serde.types import _DeltaSnapshot
        from daemon.services.checkpoint_prune import prune_unreferenced_blobs

        T, NS = "delta-thread", "delta:chain"
        v1 = "00000000000000000000000000000001.0000000000000001"
        v2 = "00000000000000000000000000000002.0000000000000002"
        # Monotonic ids: s1 < s2 < s3 < s4 lexicographically.
        cid = lambda n: f"{n:032x}"  # noqa: E731

        # s1: SNAPSHOT at v1 (blob written via real aput).
        snap1 = {"n": 1, "data": "a" * 128}
        await self._aput_checkpoint(
            saver, T, NS, None,
            channel_values={"delta_state": _DeltaSnapshot(snap1)},
            channel_versions={"delta_state": v1},
            new_versions={"delta_state": v1},
            cid=cid(1),
        )
        # s2: no update — channel_versions still references v1.
        await self._aput_checkpoint(
            saver, T, NS, cid(1),
            channel_values={},
            channel_versions={"delta_state": v1},
            new_versions={},
            cid=cid(2),
        )
        # s3: SNAPSHOT at v2.
        snap2 = {"n": 2, "data": "b" * 128}
        await self._aput_checkpoint(
            saver, T, NS, cid(2),
            channel_values={"delta_state": _DeltaSnapshot(snap2)},
            channel_versions={"delta_state": v2},
            new_versions={"delta_state": v2},
            cid=cid(3),
        )
        # s4: no update — still references v2.
        await self._aput_checkpoint(
            saver, T, NS, cid(3),
            channel_values={},
            channel_versions={"delta_state": v2},
            new_versions={},
            cid=cid(4),
        )

        # Both snapshot blobs exist; the delta reader seeds from v1 at s2
        # (the walk starts at the target's PARENT: s1, a snapshot row).
        fp = await blob_fingerprint(adapter, T, NS)
        assert ("delta_state", v1) in {f[:2] for f in fp}
        assert ("delta_state", v2) in {f[:2] for f in fp}
        hist = await saver.aget_delta_channel_history(
            config={"configurable": {"thread_id": T, "checkpoint_ns": NS,
                                     "checkpoint_id": cid(2)}},
            channels=["delta_state"],
        )
        seed_before = hist["delta_state"].get("seed")
        from langgraph.checkpoint.serde.types import _DeltaSnapshot as _DS
        assert isinstance(seed_before, _DS) and seed_before.value == snap1, (
            "pre-prune delta reconstruction must seed from the v1 snapshot"
        )

        # Retention-delete s1 + s2 (keep s3, s4) via the REAL Op-D arm.
        # v1 is now referenced by nobody (only s1/s2 carried it); v2 is
        # referenced by BOTH s3 and s4.
        await adapter.delete_checkpoints_excluding(T, NS, {cid(3), cid(4)})
        await adapter.delete_writes_excluding(T, NS, {cid(3), cid(4)})

        _arm_destructive(monkeypatch)
        summary = await prune_unreferenced_blobs(adapter)
        assert summary.dry_run is False and summary.total_deleted >= 1
        fp_after = await blob_fingerprint(adapter, T, NS)
        assert {f[:2] for f in fp_after} == {("delta_state", v2)}, (
            "orphaned snapshot v1 must die; live snapshot v2 must survive"
        )

        # aget_tuple reconstruction at the head is value-identical.
        head = await saver.aget_tuple(
            {"configurable": {"thread_id": T, "checkpoint_ns": NS}}
        )
        assert head is not None
        restored = head.checkpoint["channel_values"]["delta_state"]
        assert isinstance(restored, _DS) and restored.value == snap2

        # The delta-channel reader still seeds from the SURVIVING v2 blob:
        # walking from s4, the parent s3 is a snapshot row whose seed is
        # the blob our prune just proved to preserve.
        hist2 = await saver.aget_delta_channel_history(
            config={"configurable": {"thread_id": T, "checkpoint_ns": NS}},
            channels=["delta_state"],
        )
        seed_after = hist2["delta_state"].get("seed")
        assert isinstance(seed_after, _DS) and seed_after.value == snap2, (
            "post-prune delta reconstruction must seed from the surviving "
            "v2 snapshot blob"
        )


class TestRealSaverFailSafe:
    async def test_real_saver_zero_refs_skip_logs_error_and_deletes_nothing(
        self, saver, adapter, monkeypatch, caplog
    ):
        """Schema drift: strip channel_versions from remaining checkpoints
        of a thread that has an ORPHAN blob. Fail-safe must DETECT (ERROR
        log) and PREVENT (zero rows deleted) — even with destructive armed."""
        from daemon.services.checkpoint_prune import prune_unreferenced_blobs

        graph = build_graph(saver)
        T = "drift-thread"
        config = {"configurable": {"thread_id": T}}
        from langchain_core.messages import HumanMessage

        await graph.ainvoke({"messages": [HumanMessage("hi")]}, config)

        # Orphan a blob: retention-delete everything but the head.
        keep = set(await adapter.get_checkpoint_ids(T, "", 1))
        await adapter.delete_checkpoints_excluding(T, "", keep)
        await adapter.delete_writes_excluding(T, "", keep)

        fp_before = await blob_fingerprint(adapter, T)
        assert fp_before

        # Simulate extraction-breaking drift: remove the channel_versions
        # key from every remaining checkpoint row.
        async with adapter._pool.acquire() as conn:
            await conn.execute(
                "UPDATE checkpoints "
                "SET checkpoint = checkpoint - 'channel_versions' "
                "WHERE thread_id = $1",
                T,
            )

        refs = await adapter.count_refs_for_blob_thread(T, "")
        assert refs == 0, "drifted thread must extract zero refs"

        _arm_destructive(monkeypatch)
        with caplog.at_level(logging.ERROR, logger="daemon.services.checkpoint_prune"):
            summary = await prune_unreferenced_blobs(adapter)

        # DETECTION: the ERROR line exists and names the fail-safe.
        assert any(
            r.levelno == logging.ERROR
            and "ZERO_REFS_FAIL_SAFE" in r.getMessage()
            and T[:8] in r.getMessage()
            for r in caplog.records
        ), "fail-safe detection must log an ERROR"
        # PREVENTION: zero rows deleted — every blob still present.
        assert summary.total_deleted == 0
        assert await blob_fingerprint(adapter, T) == fp_before
        assert any(
            s == (T, "", "ZERO_REFS_FAIL_SAFE") for s in summary.skipped
        )


class TestRealSaverConcurrentAput:
    async def test_real_saver_concurrent_aput_new_blob_preserved(
        self, saver, adapter, monkeypatch
    ):
        """While a destructive prune runs, a concurrent real aput writes a
        new checkpoint + blob. The new blob MUST survive (aput commits the
        blob and its referencing checkpoint atomically, so the anti-join
        either sees both or neither — never a referenced blob alone)."""
        from langgraph.checkpoint.serde.types import _DeltaSnapshot
        from daemon.services.checkpoint_prune import prune_unreferenced_blobs

        T, NS = "concurrent-thread", ""
        # Seed a few checkpoints with blobs (real graph turns), then orphan
        # a couple of old blobs so the destructive prune has real work.
        graph = build_graph(saver)
        config = {"configurable": {"thread_id": T}}
        from langchain_core.messages import HumanMessage

        for i in range(6):
            await graph.ainvoke({"messages": [HumanMessage(f"c{i}")]}, config)
        keep = set(await adapter.get_checkpoint_ids(T, "", 2))
        await adapter.delete_checkpoints_excluding(T, "", keep)
        await adapter.delete_writes_excluding(T, "", keep)

        referenced = await referenced_versions(adapter, T)
        fp_before = await blob_fingerprint(adapter, T)
        # Only refs that HAVE blobs count for survival assertions
        # (primitive-channel values are inlined — no blob rows exist).
        expected_blob_survivors = {f[:2] for f in fp_before} & referenced

        _arm_destructive(monkeypatch)
        # Fire the prune and, concurrently, a REAL aput of a fresh snapshot
        # checkpoint (new blob + new referencing row, one atomic commit).
        vnew = "00000000000000000000000000000009.0000000000000009"
        head_id = (await adapter.get_checkpoint_ids(T, "", 1))[0]
        new_cid = "ffffffffffffffffffffffffffffffff"  # sorts after all existing ids

        async def concurrent_aput():
            ckpt = {
                "v": 1,
                "id": new_cid,
                "ts": "2026-08-26T00:00:00Z",
                "channel_values": {"fresh_state": _DeltaSnapshot({"live": True})},
                "channel_versions": {"fresh_state": vnew},
                "versions_seen": {},
            }
            cfg = {"configurable": {"thread_id": T, "checkpoint_ns": NS,
                                    "checkpoint_id": head_id}}
            return await saver.aput(cfg, ckpt, {}, {"fresh_state": vnew})

        prune_task = asyncio.create_task(prune_unreferenced_blobs(adapter))
        aput_task = asyncio.create_task(concurrent_aput())
        summary, _ = await asyncio.gather(prune_task, aput_task)

        # The concurrent aput's blob is present and referenced.
        fp = await blob_fingerprint(adapter, T)
        assert ("fresh_state", vnew) in {f[:2] for f in fp}, (
            "concurrently-written blob must survive the prune"
        )
        refs_now = await referenced_versions(adapter, T)
        assert ("fresh_state", vnew) in refs_now
        # And nothing referenced (with a blob) pre-prune was lost.
        survived = {f[:2] for f in fp}
        assert expected_blob_survivors <= survived, (
            "every pre-prune referenced blob must still be present"
        )
        # aget at the new head reconstructs the fresh value.
        head = await saver.aget_tuple({"configurable": {"thread_id": T}})
        fresh = head.checkpoint["channel_values"].get("fresh_state")
        from langgraph.checkpoint.serde.types import _DeltaSnapshot as _DS
        assert isinstance(fresh, _DS) and fresh.value == {"live": True}


class TestRealSaverDryRunReport:
    async def test_real_saver_dry_run_report_line_shape(self, saver, adapter, caplog):
        """The dry-run report line carries counts + bytes and deletes
        nothing (also the source of the PR4 report's sample output)."""
        from daemon.services.checkpoint_prune import prune_unreferenced_blobs

        graph = build_graph(saver)
        T = "report-thread"
        config = {"configurable": {"thread_id": T}}
        from langchain_core.messages import HumanMessage

        for i in range(4):
            await graph.ainvoke({"messages": [HumanMessage(f"r{i}")]}, config)
        keep = set(await adapter.get_checkpoint_ids(T, "", 1))
        await adapter.delete_checkpoints_excluding(T, "", keep)
        await adapter.delete_writes_excluding(T, "", keep)

        fp_before = await blob_fingerprint(adapter, T)
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            summary = await prune_unreferenced_blobs(adapter)
        assert summary.dry_run is True and summary.total_deleted == 0
        assert await blob_fingerprint(adapter, T) == fp_before
        line = next(
            r.getMessage() for r in caplog.records
            if "op=blob_prune" in r.getMessage() and T[:8] in r.getMessage()
        )
        assert "dry_run=1" in line and "bytes=" in line and "refs_seen=" in line


class TestRealSaverSqliteNoOp:
    async def test_real_saver_sqlite_backend_noops_with_warning(self, tmp_path, caplog):
        """SQLite has no checkpoint_blobs table — the prune no-ops with a
        WARNING (plan default) instead of touching the saver."""
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from daemon.checkpoint_adapter import SqliteCheckpointerAdapter
        from daemon.services.checkpoint_prune import prune_unreferenced_blobs

        conn = await aiosqlite.connect(str(tmp_path / "cp.db"))
        try:
            saver_sqlite = AsyncSqliteSaver(conn)
            await saver_sqlite.setup()
            adapter_sqlite = SqliteCheckpointerAdapter(saver_sqlite)
            with caplog.at_level(logging.WARNING,
                                 logger="daemon.services.checkpoint_prune"):
                summary = await prune_unreferenced_blobs(adapter_sqlite)
            assert summary.backend == "sqlite"
            assert summary.total_deleted == 0
            assert any(
                "checkpoint_blobs" in r.getMessage()
                and r.levelno == logging.WARNING
                for r in caplog.records
            ), "SQLite no-op must emit a WARNING"
        finally:
            await conn.close()
