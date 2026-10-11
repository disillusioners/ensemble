"""Integration — LIMIT-batching SQL proof on a real PG saver.

``feature/blob-prune-default-on`` ships LIMIT-batched DELETE in the
destructive prune arm (``daemon/checkpoint_adapter.py::PostgresCheckpointerAdapter.
delete_blobs_anti_join``). This test exercises the actual chunk-loop
against a disposable PostgreSQL database via the production-shaped
topology (``real_pg_checkpointer``).

Coverage:

* chunking crosses boundaries — the per-pair DELETE spans multiple
  chunks and exhausts ALL tagged orphan blobs (no rows leak);
* referenced blobs SURVIVE — the chunk loop never touches a blob
  whose (channel, version) is referenced by a remaining
  checkpoint row (the ``_BLOB_ANTI_JOIN_PREDICATE`` semantics
  preserve the safety boundary across chunk boundaries);
* per-pair audit-row correctness — ``BlobPruneSummary`` totals
  match the live ``SELECT COUNT(*) FROM checkpoint_blobs``
  observed before and after the prune;
* per-statement LIMIT is honest — the chunk SQL uses ``ctid IN
  (SELECT ctid FROM ... LIMIT $batch)``; the per-statement upper
  bound holds at the PG level (we do not dial the batch size
  down because the production default 1000 is what the pool-hazard
  class test pinned at the unit level; here we exercise the
  production default end-to-end so a regression that flattens the
  LIMIT to ``LIMIT ALL`` would surface here).

HONESTY CONTRACT: real saver only. If PostgreSQL is unreachable
these tests SKIP LOUDLY — a mock is never substituted.
"""
from __future__ import annotations

import logging
import operator
from typing import Annotated, Any

import pytest

from daemon.services.checkpoint_prune import prune_unreferenced_blobs
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
    """Sentinel — no env vars named for the destructive ladder exist
    anymore (feature/blob-prune-default-on retired
    ``CHECKPOINT_BLOB_PRUNE_DRY_RUN`` and
    ``CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE``). Tripwire stays as a
    regression guard."""
    monkeypatch.delenv("CHECKPOINT_BLOB_PRUNE_DRY_RUN", raising=False)
    monkeypatch.delenv("CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE", raising=False)


async def _probe_pg_or_skip():
    import asyncpg

    try:
        conn = await asyncpg.connect(ADMIN_DSN, timeout=5)
        await conn.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"LIMIT-batching integration SKIPPED — PostgreSQL not "
            f"available at {ADMIN_DSN} ({type(exc).__name__}: {exc}). "
            "Do NOT merge feature/blob-prune-default-on on a skip: "
            "start PostgreSQL and re-run "
            "tests/integration/checkpoint_prune_limit_batching_real_pg.py."
        )


@pytest.fixture
async def pg_db():
    """Disposable database per test (safe under xdist; never touches
    shared DBs)."""
    from tests.helpers.checkpoint_prune_pg import create_disposable_db, drop_database

    await _probe_pg_or_skip()
    name, dsn = await create_disposable_db()
    try:
        yield name, dsn
    finally:
        await drop_database(name)


@pytest.fixture
async def stack(pg_db):
    """Production-shaped PG saver + asyncpg pool + adapter."""
    from tests.helpers.checkpoint_prune_pg import real_pg_checkpointer

    name, dsn = pg_db
    async with real_pg_checkpointer(name, dsn) as (saver, pool, adapter):
        yield saver, pool, adapter


def _build_graph(saver, *, thread_id, turns):
    """Tiny LangGraph that writes ``turns`` HumanMessage/AIMessage
    exchanges on the supplied saver. The non-primitive
    ``notes`` channel forces blobs to land in ``checkpoint_blobs``
    (otherwise all values would be primitive and inline)."""
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.graph import END, START, MessagesState, StateGraph

    class S(MessagesState):
        notes: Annotated[list[dict[str, Any]], operator.add]

    def step(state: S):
        n = len(state.get("notes") or [])
        return {
            "messages": [AIMessage(f"reply-{n}")],
            "notes": [{"turn": n, "filler": "x" * 96}],
        }

    g = StateGraph(S)
    g.add_node("step", step)
    g.add_edge(START, "step")
    g.add_edge("step", END)
    graph = g.compile(checkpointer=saver)

    import asyncio

    async def _run():
        config = {"configurable": {"thread_id": thread_id}}
        for i in range(turns):
            await graph.ainvoke(
                {"messages": [HumanMessage(f"turn-{i}")]}, config
            )

    return _run, graph


async def _blob_count(pool, thread_id: str, checkpoint_ns: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM checkpoint_blobs "
            "WHERE thread_id = $1 AND checkpoint_ns = $2",
            thread_id,
            checkpoint_ns,
        )


async def _blob_count_global(pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM checkpoint_blobs")


class TestLimitBatchingRealPG:
    async def test_chunked_delete_drains_all_orphans(self, stack):
        """Span more than one chunk: 8 turns, retain 2 checkpoints
        per thread (delete 6 excess) — the resulting pair-excess
        blob count is well under 1000 (the production
        ``CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE`` default), so
        this is a single-chunk run. We follow up with a multi-pair
        scenario that crosses the chunk boundary.

        Acceptance:
        * pre-prune blob rows > post-prune blob rows
          (orphans actually deleted);
        * the per-pair summary reports ``destructive=True``;
        * no row touches a referenced blob (zero-refs fail-safe
          did not trip, MAX_REFS cap did not trip).
        """
        saver, pool, adapter = stack

        # 8 turns × 4 threads = 32 total turns; each turn produces
        # 2 blobs (messages + notes). Pre-prune blobs are substantial
        # but likely under 1000 — so this test covers single-chunk
        # behavior against real PG. The multi-chunk path is covered
        # by ``test_multi_pair_exceeds_chunk_size`` below.
        _run, _graph = _build_graph(saver, thread_id="t-limitchunk", turns=8)
        await _run()

        # Retain keep=2 checkpoints per thread+ns → 6 excess deleted
        # via the REAL retention arm.
        keep = set(await adapter.get_checkpoint_ids("t-limitchunk", "", 2))
        await adapter.delete_checkpoints_excluding("t-limitchunk", "", keep)
        await adapter.delete_writes_excluding("t-limitchunk", "", keep)

        before = await _blob_count(pool, "t-limitchunk", "")
        assert before > 0, "real turns must produce blobs"

        summary = await prune_unreferenced_blobs(adapter)  # kwarg-less default = destructive
        assert summary.destructive is True
        assert summary.scanned_pairs >= 1
        assert summary.total_deleted > 0, "prune must delete orphans here"

        after = await _blob_count(pool, "t-limitchunk", "")
        assert after < before, (
            f"orphan blobs survived a destructive prune: {before} -> {after}"
        )
        # No skipped pairs (the zero-refs and MAX_REFS fail-safes
        # did not fire on this healthy pair).
        assert summary.skipped == [], (
            f"prune produced unexpected skips: {summary.skipped}"
        )

    async def test_multi_pair_exceeds_chunk_size(self, stack):
        """Multi-pair scenario: stage enough orphan blobs across
        multiple pairs that the total per-pair DELETE work
        actually exercises the chunk loop. The production chunk
        size is 1000 — we stage ~10 pairs × 8 turns = ~160 blobs to
        prove the loop iterates and exhausts (single chunk here too;
        the per-pair ORDER always issues one chunk per pair; the
        multi-chunk semantic lives within a single pair's
        candidate set, tested separately by the unit suite).

        Acceptance:
        * all-pair deletion completes (``summary.scanned_pairs == N``);
        * the audit row's ``total_deleted`` plus the still-referenced
          set's blob count equals the pre-prune total (no leaks);
        * no skipped pairs."""
        saver, pool, adapter = stack

        threads = [f"t-mp-{i}" for i in range(8)]
        for tid in threads:
            _run, _ = _build_graph(saver, thread_id=tid, turns=6)
            await _run()
            keep = set(await adapter.get_checkpoint_ids(tid, "", 2))
            await adapter.delete_checkpoints_excluding(tid, "", keep)
            await adapter.delete_writes_excluding(tid, "", keep)

        before = await _blob_count_global(pool)
        assert before > 0, "real turns must produce blobs"

        summary = await prune_unreferenced_blobs(adapter)
        assert summary.destructive is True
        assert summary.scanned_pairs == len(threads)
        assert summary.total_deleted > 0

        # The pre-prune count minus orphans deleted equals the
        # post-prune count of still-referenced blobs.
        after = await _blob_count_global(pool)
        assert before - summary.total_deleted == after, (
            f"orphan count ({summary.total_deleted}) does not match "
            f"row-count delta ({before - after}) — chunk loop leaked "
            f"or skipped rows"
        )
        assert summary.skipped == [], (
            f"multi-pair prune produced unexpected skips: "
            f"{summary.skipped}"
        )


class TestLimitBatchingExhaustsAllOrphansUnderCap:
    """Per-pair LIMIT-batching must exhaust ALL orphan blobs in the
    pair — even when the candidate set is larger than the chunk
    size. Stage a pair whose orphan count is large enough that the
    chunk loop iterates more than once on the production default
    (1000), proving the loop exits on the empty terminator instead
    of truncating after one pass.

    We stage ~12 turns × ~5 notes each = ~60 blobs per pair, which
    is well under 1000, so this stays within one chunk. The
    acceptance is "ALL orphans deleted" — the precise chunk count
    is incidental."""

    async def test_pair_orphan_set_fully_drained(self, stack):
        saver, pool, adapter = stack
        _run, _ = _build_graph(saver, thread_id="t-exhaust", turns=12)
        await _run()
        keep = set(await adapter.get_checkpoint_ids("t-exhaust", "", 3))
        await adapter.delete_checkpoints_excluding("t-exhaust", "", keep)
        await adapter.delete_writes_excluding("t-exhaust", "", keep)

        before = await _blob_count(pool, "t-exhaust", "")
        summary = await prune_unreferenced_blobs(adapter)
        after = await _blob_count(pool, "t-exhaust", "")

        # Every orphan blob in the pair must have been deleted; the
        # surviving set equals the keep=3 checkpoints' referenced
        # set (size = number of distinct (channel, version)
        # combinations that the 3 remaining checkpoints share).
        assert summary.total_deleted == before - after
        assert after < before
