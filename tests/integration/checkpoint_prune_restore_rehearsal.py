"""Automated restore-rehearsal ROUNDTRIP — Phase 1 C3 (PR4, Rev 2 fold-in).

Proves the rollback procedure end-to-end on a REAL PostgreSQL saver:

    backup → destructive prune → restore → BYTE-equality

Byte-equality = the full (thread_id, checkpoint_ns, channel, version,
type, md5(blob)) row set after restore is IDENTICAL to the pre-prune
baseline — not merely equal counts. Backups restore via
``INSERT INTO checkpoint_blobs SELECT * FROM <backup> ON CONFLICT DO
NOTHING`` (the runbook's documented shape); survivors conflict-skip and
only pruned rows re-insert.
"""
from __future__ import annotations

import hashlib
import logging
import operator
from typing import Annotated, Any

import pytest

from tests.helpers.checkpoint_prune_pg import (
    ADMIN_DSN,
    evict_langgraph_mocks,
    restore_langgraph_mocks,
)


@pytest.fixture(autouse=True)
def _real_langgraph():
    saved = evict_langgraph_mocks()
    try:
        yield
    finally:
        restore_langgraph_mocks(saved)


@pytest.fixture(autouse=True)
def _default_ladder(monkeypatch):
    monkeypatch.delenv("CHECKPOINT_BLOB_PRUNE_DRY_RUN", raising=False)
    monkeypatch.delenv("CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE", raising=False)


@pytest.fixture
async def pg_db():
    import asyncpg
    from tests.helpers.checkpoint_prune_pg import create_disposable_db, drop_database

    # Async probe (this fixture is async — no asyncio.run here).
    try:
        conn = await asyncpg.connect(ADMIN_DSN, timeout=5)
        await conn.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"BINDING restore-rehearsal SKIPPED — PostgreSQL not available at "
            f"{ADMIN_DSN} ({type(exc).__name__}: {exc}). Do NOT merge PR4 on a "
            "skip: start PostgreSQL and re-run "
            "tests/integration/checkpoint_prune_restore_rehearsal.py."
        )
    name, dsn = await create_disposable_db()
    try:
        yield name, dsn
    finally:
        await drop_database(name)


async def _full_blob_rows(conn):
    rows = await conn.fetch(
        "SELECT thread_id, checkpoint_ns, channel, version, type, blob "
        "FROM checkpoint_blobs "
        "ORDER BY thread_id, checkpoint_ns, channel, version"
    )
    return [
        (
            r["thread_id"], r["checkpoint_ns"], r["channel"], r["version"],
            r["type"],
            hashlib.md5(r["blob"]).hexdigest() if r["blob"] is not None else None,
        )
        for r in rows
    ]


class TestRestoreRehearsalRoundtrip:
    async def test_backup_prune_restore_byte_equality(self, pg_db, monkeypatch):
        """backup → destructive prune → restore → byte-equality (plan §C3)."""
        from langchain_core.messages import AIMessage, HumanMessage
        from langgraph.graph import END, START, MessagesState, StateGraph

        from daemon.services.checkpoint_prune import prune_unreferenced_blobs
        from tests.helpers.checkpoint_prune_pg import real_pg_checkpointer

        class RehearsalState(MessagesState):
            notes: Annotated[list[dict[str, Any]], operator.add]

        def step(state: RehearsalState):
            n = len(state.get("notes") or [])
            return {
                "messages": [AIMessage(f"reply-{n}")],
                "notes": [{"turn": n, "filler": "y" * 96}],
            }

        name, dsn = pg_db
        async with real_pg_checkpointer(name, dsn) as (saver, pool, adapter):
            g = StateGraph(RehearsalState)
            g.add_node("step", step)
            g.add_edge(START, "step")
            g.add_edge("step", END)
            graph = g.compile(checkpointer=saver)

            T = "rehearsal-thread"
            config = {"configurable": {"thread_id": T}}
            for i in range(6):
                await graph.ainvoke(
                    {"messages": [HumanMessage(f"turn-{i}")]}, config
                )

            async with pool.acquire() as conn:
                # 2. Baseline + backup table (runbook shape).
                baseline_rows = await _full_blob_rows(conn)
                baseline_count = len(baseline_rows)
                assert baseline_count > 0, "real turns must produce blobs"
                await conn.execute(
                    "CREATE TABLE checkpoint_blobs_rehearsal_backup AS "
                    "SELECT * FROM checkpoint_blobs"
                )

            # Orphan blobs via the REAL retention arm, then destructive prune.
            keep = set(await adapter.get_checkpoint_ids(T, "", 2))
            await adapter.delete_checkpoints_excluding(T, "", keep)
            await adapter.delete_writes_excluding(T, "", keep)

            monkeypatch.setenv("CHECKPOINT_BLOB_PRUNE_DRY_RUN", "0")
            monkeypatch.setenv("CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE", "1")
            summary = await prune_unreferenced_blobs(adapter)
            assert summary.dry_run is False
            assert summary.total_deleted >= 1, "prune must delete orphans here"

            async with pool.acquire() as conn:
                post_prune_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM checkpoint_blobs"
                )
                assert post_prune_count < baseline_count

                # 4. Restore from backup (runbook shape).
                await conn.execute(
                    "INSERT INTO checkpoint_blobs "
                    "SELECT * FROM checkpoint_blobs_rehearsal_backup "
                    "ON CONFLICT DO NOTHING"
                )
                restored_rows = await _full_blob_rows(conn)
                restored_count = len(restored_rows)

            # 5. BYTE-equality: full row set identical to baseline.
            assert restored_count == baseline_count, (
                "restore must match baseline count"
            )
            assert restored_rows == baseline_rows, (
                "restore must match baseline BYTE-for-byte (md5 of every blob)"
            )

            # 6. Post-restore liveness: aget still reconstructs the thread.
            snap = await graph.aget_state(config)
            assert len(snap.values["messages"]) > 0
            assert snap.values["notes"]

            async with pool.acquire() as conn:
                await conn.execute(
                    "DROP TABLE checkpoint_blobs_rehearsal_backup"
                )
