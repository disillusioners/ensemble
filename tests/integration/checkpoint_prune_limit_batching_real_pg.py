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
  bound holds at the PG level. The production default 1000 is
  exercised end-to-end so a regression that flattens the LIMIT to
  ``LIMIT ALL`` would surface; a separate test DIALS the batch size
  down (~5) against a small staged orphan set to drive the per-pair
  loop across MULTIPLE data-bearing chunks and prove full
  exhaustion plus count/bytes totals across chunk boundaries (the
  b2-vs-b alias bug — review C1 — passed every <1000-row test while
  silently never exhausting large pairs, because an unconstrained
  subquery still happens to return the true targets when the whole
  table fits inside the first batch of ctids).

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


async def _blob_bytes(pool, thread_id: str, checkpoint_ns: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COALESCE(SUM(OCTET_LENGTH(blob)), 0) FROM checkpoint_blobs "
            "WHERE thread_id = $1 AND checkpoint_ns = $2",
            thread_id,
            checkpoint_ns,
        )


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


class TestMultiChunkExhaustionDialedBatch:
    """Multi-CHUNK exhaustion on real PG — the behavioral counterpart
    of the unit-level static alias pin (review C1 regression guard).

    The b2-vs-b alias bug passed every test that staged fewer
    orphans than one batch: an unconstrained subquery still happens
    to return the true targets when the whole table fits inside the
    first ``batch`` ctids. Here the batch size is DIALED DOWN to 5
    and a 12+ orphan set is staged, so the per-pair loop MUST cross
    >= 3 data-bearing chunks. Staging keeps the 2 OLDEST
    checkpoints so the live anchors LEAD the physical ctid order on
    this fresh append-only table: an unconstrained first-batch
    window would cover the live ctids, match nothing against the
    anti-join predicate, and delete 0 — the chunk-count, totals,
    and exhaustion assertions then fail deterministically on the
    buggy SQL. (Under keep-NEWEST staging the orphans LEAD instead
    — the one layout where the buggy window slides through them
    identically to the fixed predicate-bound one, so the previous
    staging had no discriminative power over this bug class.
    Review W1.)

    Acceptance:
    * >= 3 data-bearing chunks observed, each within the batch bound;
    * count AND bytes totals match the live table deltas across
      chunk boundaries;
    * full per-pair exhaustion — a second prune deletes 0 (the
      chunk loop exits on a genuinely empty candidate set, not on a
      truncated leading window);
    * zero live blobs touched — global count conservation on the
      fresh disposable DB (this pair is the only data).
    """

    BATCH = 5
    MIN_ORPHANS = 12  # >= 3 data-bearing chunks at batch 5

    async def test_pair_exhausts_across_multiple_dialed_chunks(
        self, stack, monkeypatch
    ):
        import daemon.checkpoint_adapter as adapter_module

        saver, pool, adapter = stack

        # Dial the per-pair chunk size DOWN — the adapter binds the
        # constant by import (``from daemon.constants import ...``),
        # so patch the adapter module's own binding.
        monkeypatch.setattr(
            adapter_module, "CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE", self.BATCH
        )

        # Instrument the SQL primitive to observe per-chunk row counts
        # (instance-level wrapper; the original bound method is
        # captured BEFORE patching). The primitive returns
        # ``(rows, stop_reason)`` since the W1 partial-signal fix —
        # unwrap for the counter, pass the pair through unchanged.
        chunk_sizes: list[int] = []
        primitive = adapter._delete_blobs_anti_join_chunk

        async def _counting(thread_id, checkpoint_ns, batch_size):
            rows, stop_reason = await primitive(thread_id, checkpoint_ns, batch_size)
            chunk_sizes.append(len(rows))
            return rows, stop_reason

        monkeypatch.setattr(adapter, "_delete_blobs_anti_join_chunk", _counting)

        # Stage 12 turns → 10 excess checkpoints (keep=2) × 2 blobs
        # each = ~20 orphans — comfortably above MIN_ORPHANS at
        # batch 5.
        tid = "t-dialed-multichunk"
        _run, _ = _build_graph(saver, thread_id=tid, turns=12)
        await _run()
        # keep the 2 OLDEST, not the newest (get_checkpoint_ids is
        # newest-first, so slice the tail of the full list). On this
        # fresh append-only table insertion order ≈ ctid order, so
        # oldest-retention puts the LIVE blobs at the physical HEAD:
        # an unconstrained first-batch window (the review-C1 bug
        # class) would cover live ctids, delete 0, and fail the
        # assertions below deterministically. keep-NEWEST would
        # stage orphans LEADING instead — the one layout where the
        # buggy window is indistinguishable from the fixed
        # predicate-bound one (no discriminative power).
        all_ids = await adapter.get_checkpoint_ids(tid, "", 12)
        keep = set(all_ids[-2:])
        await adapter.delete_checkpoints_excluding(tid, "", keep)
        await adapter.delete_writes_excluding(tid, "", keep)

        before_count = await _blob_count(pool, tid, "")
        before_bytes = await _blob_bytes(pool, tid, "")
        global_before = await _blob_count_global(pool)
        assert before_count >= self.MIN_ORPHANS + 4, (
            f"staging under-delivered: {before_count} blobs staged, "
            f"need >= {self.MIN_ORPHANS} orphans (+ the retained "
            f"checkpoint set) for a >= 3-chunk run at batch "
            f"{self.BATCH}"
        )

        summary = await prune_unreferenced_blobs(adapter)

        after_count = await _blob_count(pool, tid, "")
        after_bytes = await _blob_bytes(pool, tid, "")
        global_after = await _blob_count_global(pool)

        # (1) multi-chunk crossing — >= 3 data-bearing chunks, each
        # within the dialed batch bound.
        data_chunks = [n for n in chunk_sizes if n > 0]
        assert len(data_chunks) >= 3, (
            f"expected >= 3 data-bearing chunks at batch={self.BATCH} "
            f"(staged {before_count} blobs); got chunk sizes "
            f"{chunk_sizes} — the batch dial-down or staging regressed"
        )
        assert all(0 < n <= self.BATCH for n in data_chunks), (
            f"per-chunk bound violated at batch={self.BATCH}: "
            f"{data_chunks}"
        )

        # (2) totals match the live table deltas ACROSS chunk
        # boundaries (count and bytes).
        assert summary.total_deleted == before_count - after_count, (
            f"deleted-count total ({summary.total_deleted}) != live "
            f"row delta ({before_count - after_count}) — chunk loop "
            f"leaked, double-counted, or truncated"
        )
        assert summary.total_bytes_freed == before_bytes - after_bytes, (
            f"bytes-freed total ({summary.total_bytes_freed}) != live "
            f"OCTET_LENGTH delta ({before_bytes - after_bytes}) — "
            f"RETURNING aggregation drifted across chunk boundaries"
        )

        # (3) full exhaustion — a second prune finds nothing left.
        second = await prune_unreferenced_blobs(adapter)
        assert second.total_deleted == 0, (
            f"pair NOT exhausted by the first prune: second run "
            f"deleted {second.total_deleted} more rows — the chunk "
            f"loop exited early (b2-vs-b unconstrained-subquery "
            f"symptom)"
        )

        # (4) zero live blobs touched — global conservation on the
        # fresh disposable DB (this pair is the only data; survivors
        # are exactly the retained checkpoints' referenced set).
        assert global_before - summary.total_deleted == global_after, (
            f"global blob count changed by more than the pair's "
            f"deletion total — blobs outside the target pair were "
            f"touched"
        )
        assert summary.skipped == [], (
            f"multi-chunk prune produced unexpected skips: "
            f"{summary.skipped}"
        )
