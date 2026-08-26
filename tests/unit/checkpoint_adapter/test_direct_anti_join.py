"""Real-SQL anti-join tests — Phase 1 C3 (PR4).

Mirrors the §9 mapping in phase1-plan.md:

  * referenced blobs survive
  * unreferenced blobs die
  * mixed thread boundaries hold
  * mixed namespace boundaries hold (THE critical predicate: ns-matching
    on BOTH sides of the anti-join — a same-named (channel, version)
    in a different namespace MUST NOT rescue an unreferenced blob)
  * missing-channel → NULL semantics (``->>`` yields NULL, NULL = ver
    is NULL, NOT EXISTS picks the row)
  * non-object channel_versions (schema drift) — fail-safe path
  * NULL blob bytes — counted + deletable
  * count arm == delete arm parity (dry-run never diverges from what
    would delete)
  * ``find_all_thread_ns_pairs`` returns all pairs (D21)

PG: REAL PostgreSQL (disposable database) — ``pytest.skip`` when PG
unreachable. SQLite: REAL ``AsyncSqliteSaver`` + the
``SqliteCheckpointerAdapter`` against an in-memory database.

Both use the REAL adapter classes (no mocks wearing adapter names).
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest

# These tests evict the root-conftest langgraph mocks and import the
# REAL langgraph + REAL adapters — same pattern as
# tests/integration/test_compaction_e2e.py.
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


# ── SQLite ─────────────────────────────────────────────────────────────────────


class TestSqliteAdapterBlobs:
    """SQLite: there is no checkpoint_blobs table — stubs no-op with WARNING.

    Real ``AsyncSqliteSaver`` + real ``SqliteCheckpointerAdapter``
    (because the suite is honest about what it tests). The
    ``prune_unreferenced_blobs`` short-circuit at the algorithm layer is
    covered by the service-layer tests; here we verify the ADAPTER-level
    no-op + WARNING when its blob arms are called directly (defense in
    depth — no production code calls them on SQLite, but tests must lock
    the contract).
    """

    async def _make_adapter(self):
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from daemon.checkpoint_adapter import SqliteCheckpointerAdapter

        db_path = f"/tmp/agents_ensemble_blobs_sqlite_{uuid.uuid4().hex[:8]}.db"
        conn = await aiosqlite.connect(db_path)
        saver = AsyncSqliteSaver(conn)
        # The SQL DDL mirrors langgraph-checkpoint-sqlite's schema for
        # the checkpoints table — the only one the SQLite adapter reads.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                parent_checkpoint_id TEXT,
                type TEXT,
                checkpoint BLOB NOT NULL,
                metadata BLOB NOT NULL DEFAULT '{}',
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
            )
            """
        )
        await conn.commit()
        return SqliteCheckpointerAdapter(saver), db_path

    async def _seed_checkpoints(self, adapter, rows):
        # rows: list of (thread_id, checkpoint_ns, checkpoint_id, channel_versions dict)
        async with adapter._saver.lock:
            for tid, ns, cid, cv in rows:
                payload = json.dumps(
                    {"v": 1, "id": cid, "ts": "2026-08-26T00:00:00Z",
                     "channel_values": {}, "channel_versions": cv,
                     "versions_seen": {}}
                ).encode()
                await adapter._saver.conn.execute(
                    "INSERT INTO checkpoints "
                    "(thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (tid, ns, cid, None, payload, b"{}"),
                )
            await adapter._saver.conn.commit()

    async def test_find_all_thread_ns_pairs_returns_every_pair(self, caplog):
        adapter, db_path = await self._make_adapter()
        try:
            await self._seed_checkpoints(
                adapter,
                [
                    ("t1", "", "c1", {"m": "1"}),
                    ("t1", "child:x", "c2", {"m": "1"}),
                    ("t2", "", "c3", {"m": "1"}),
                    # single-checkpoint thread — must appear (D21)
                    ("lonely", "", "c4", {"m": "1"}),
                ],
            )
            pairs = await adapter.find_all_thread_ns_pairs()
            assert ("t1", "") in [(p[0], p[1]) for p in pairs]
            assert ("t1", "child:x") in [(p[0], p[1]) for p in pairs]
            assert ("t2", "") in [(p[0], p[1]) for p in pairs]
            assert ("lonely", "") in [(p[0], p[1]) for p in pairs], (
                "single-checkpoint thread must be enumerated (D21)"
            )
            counts = {p[:2]: p[2] for p in pairs}
            assert counts[("t1", "")] == 1
            assert counts[("lonely", "")] == 1
        finally:
            import os
            try:
                os.unlink(db_path)
            except OSError:
                pass

    async def test_count_and_delete_blob_arms_noop_with_warning(self, caplog):
        import logging
        adapter, db_path = await self._make_adapter()
        try:
            caplog.set_level(logging.WARNING, logger="daemon.checkpoint_adapter")
            cnt = await adapter.count_refs_for_blob_thread("any", "")
            assert cnt == 0
            c, b = await adapter.count_blobs_anti_join("any", "")
            assert c == 0 and b == 0
            d, b2 = await adapter.delete_blobs_anti_join("any", "")
            assert d == 0 and b2 == 0
            # At least one WARNING naming the SQLite-no-op.
            assert any(
                "checkpoint_blobs" in rec.getMessage() and rec.levelno == logging.WARNING
                for rec in caplog.records
            )
        finally:
            import os
            try:
                os.unlink(db_path)
            except OSError:
                pass


# ── PostgreSQL ────────────────────────────────────────────────────────────────


PG_FIXTURE_SKIP_REASON = (
    "PostgreSQL not available at the PG_TEST_* DSN — direct anti-join "
    "SQL tests skipped. The C3 binding gate requires a REAL PG; do not "
    "merge PR4 on a skip."
)


@pytest.fixture(scope="module")
def pg_db_dsn():
    """Disposable PG database for direct anti-join SQL fixtures."""
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


def _cv_with(versions: dict) -> str:
    """JSONB with the upstream-shaped channel_versions — pg accepts JSON literal."""
    return json.dumps({"v": 1, "id": str(uuid.uuid4()),
                       "ts": "2026-08-26T00:00:00Z",
                       "channel_values": {}, "channel_versions": versions,
                       "versions_seen": {}})


async def _insert_checkpoint(adapter, thread_id, ns, cid, channel_versions):
    async with adapter._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO checkpoints "
            "(thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata) "
            "VALUES ($1, $2, $3, $4, $5::jsonb, '{}'::jsonb)",
            thread_id, ns, cid, None, _cv_with(channel_versions),
        )


async def _insert_blob(adapter, thread_id, ns, channel, version, blob):
    async with adapter._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO checkpoint_blobs "
            "(thread_id, checkpoint_ns, channel, version, type, blob) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            thread_id, ns, channel, version, "msgpack", blob,
        )


async def _count_blobs(adapter, thread_id, ns):
    async with adapter._pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS n FROM checkpoint_blobs WHERE thread_id=$1 AND checkpoint_ns=$2",
            thread_id, ns,
        )
        return int(row["n"])


async def _blobs_rows(adapter, thread_id, ns):
    async with adapter._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT channel, version, type, OCTET_LENGTH(blob) AS n "
            "FROM checkpoint_blobs WHERE thread_id=$1 AND checkpoint_ns=$2 "
            "ORDER BY channel, version",
            thread_id, ns,
        )
        return [(r["channel"], r["version"], r["type"], int(r["n"])) for r in rows]


class TestPostgresDirectAntiJoin:
    """Real-SQL anti-join tests against the actual tables.

    These are the unit-level (§C3 file-table row) tests that the plan
    names; the binding gate (real-saver end-to-end) lives in
    tests/integration/checkpoint_prune_real_saver.py.
    """

    async def test_referenced_blobs_survive_unreferenced_die(self, pg_adapter):
        T = "thr-1"
        await _insert_checkpoint(pg_adapter, T, "", "c1", {"messages": "v1"})
        await _insert_blob(pg_adapter, T, "", "messages", "v1", b"\x80\x01K\x01.")  # msgpack
        await _insert_blob(pg_adapter, T, "", "messages", "v2", b"\x80\x01K\x02.")  # ORPHAN

        # Dry-run arm — predicts exactly the orphan.
        cnt, bytes_freed = await pg_adapter.count_blobs_anti_join(T, "")
        assert cnt == 1
        assert bytes_freed == len(b"\x80\x01K\x02.")

        # Destructive arm — orphan dies; referenced survives.
        deleted, freed = await pg_adapter.delete_blobs_anti_join(T, "")
        assert deleted == 1
        assert freed == bytes_freed
        rows = await _blobs_rows(pg_adapter, T, "")
        assert rows == [("messages", "v1", "msgpack", len(b"\x80\x01K\x01."))]

    async def test_mixed_thread_boundary_each_ns_pruned_independently(
        self, pg_adapter
    ):
        # Two threads, same (channel, version) values — pruning T1 must
        # not touch T2's blob even though the (channel, version) is the
        # same in both (thread_id + checkpoint_ns is the primary key).
        T1, T2 = "thr-a", "thr-b"
        await _insert_checkpoint(pg_adapter, T1, "", "c1", {"m": "v1"})
        await _insert_blob(pg_adapter, T1, "", "m", "v1", b"a")
        await _insert_checkpoint(pg_adapter, T2, "", "c1", {"m": "v1"})
        await _insert_blob(pg_adapter, T2, "", "m", "v1", b"b")  # thread-distinct OK

        deleted, _ = await pg_adapter.delete_blobs_anti_join(T1, "")
        assert deleted == 0  # v1 IS referenced by T1's c1
        # T2's blob intact.
        rows = await _blobs_rows(pg_adapter, T2, "")
        assert rows == [("m", "v1", "msgpack", 1)]

    async def test_mixed_namespace_boundary_critical_predicate(self, pg_adapter):
        """THE critical predicate: ns-matching on BOTH sides.

        Same (channel, version) strings in two namespaces. The
        NOT EXISTS subquery must correlate ``c.checkpoint_ns = b.checkpoint_ns``
        (not a literal), so a same-named (channel, version) in a DIFFERENT
        namespace can never mask an unreferenced blob.
        """
        T = "thr-ns"
        # Namespace "nsA": checkpoint c1 references (ch, v99).
        await _insert_checkpoint(pg_adapter, T, "nsA", "c1", {"ch": "v99"})
        await _insert_blob(pg_adapter, T, "nsA", "ch", "v99", b"alive-A")
        # Namespace "nsB": an ORPHAN blob with the same (ch, v99) values.
        # (No checkpoint in nsB references it.)
        await _insert_blob(pg_adapter, T, "nsB", "ch", "v99", b"orphan-B")

        deleted, freed = await pg_adapter.delete_blobs_anti_join(T, "nsB")
        assert deleted == 1, (
            "nsB's orphan must die — nsA's (ch, v99) MUST NOT rescue it"
        )
        assert freed == len(b"orphan-B")

        rows_A = await _blobs_rows(pg_adapter, T, "nsA")
        rows_B = await _blobs_rows(pg_adapter, T, "nsB")
        assert rows_A == [("ch", "v99", "msgpack", len(b"alive-A"))]
        assert rows_B == []  # orphan gone

    async def test_missing_channel_in_remaining_checkpoint_yields_orphan(self, pg_adapter):
        """``->>`` on a missing key returns NULL → NOT EXISTS picks the row."""
        T = "thr-miss"
        # Checkpoint c1 references only "other"; the "messages" blob
        # below is unreferenced (its key is absent from channel_versions).
        await _insert_checkpoint(pg_adapter, T, "", "c1", {"other": "v1"})
        await _insert_blob(pg_adapter, T, "", "messages", "v1", b"orphan-missing")

        deleted, _ = await pg_adapter.delete_blobs_anti_join(T, "")
        assert deleted == 1

    async def test_channel_versions_non_object_yields_zero_refs(self, pg_adapter):
        """Schema drift: channel_versions as a string (not an object) —
        the fail-safe pre-check must extract 0 refs (CASE normalizes to
        ``{}``) so the prune refuses to delete anything for this thread.
        """
        T = "thr-drift"
        # Inject a checkpoint whose channel_versions is a JSON string.
        async with pg_adapter._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO checkpoints "
                "(thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata) "
                "VALUES ($1, '', $2, NULL, $3::jsonb, '{}'::jsonb)",
                T, "c1",
                json.dumps(
                    {"v": 1, "id": "c1", "ts": "2026-08-26T00:00:00Z",
                     "channel_values": {},
                     "channel_versions": "garbage-not-an-object",
                     "versions_seen": {}}
                ),
            )
            await conn.execute(
                "INSERT INTO checkpoint_blobs "
                "(thread_id, checkpoint_ns, channel, version, type, blob) "
                "VALUES ($1, '', 'm', 'v1', 'msgpack', 'orphan-drift')",
                T,
            )

        refs = await pg_adapter.count_refs_for_blob_thread(T, "")
        assert refs == 0, "non-object channel_versions must yield 0 refs"
        # The destructive arm would still execute, but the count_refs pre-
        # check at the algorithm layer SKIPS this thread (fail-safe).
        # The SQL itself, when called directly, deletes the row because
        # the predicate is "not referenced" — and indeed nothing refers.
        # The guard is at the algorithm layer; here we document the
        # count_refs signal.
        refs2 = await pg_adapter.count_refs_for_blob_thread(T, "")
        assert refs2 == 0

    async def test_count_arm_matches_destructive_arm_for_referenced_survive(
        self, pg_adapter
    ):
        """Dry-run count must equal delete count: the two arms share
        _BLOB_ANTI_JOIN_PREDICATE verbatim, so they can never diverge."""
        T = "thr-parity"
        await _insert_checkpoint(pg_adapter, T, "", "c1", {"k": "v1"})
        for v in ("v1", "v2", "v3"):
            await _insert_blob(pg_adapter, T, "", "k", v, v.encode() * 4)

        cnt, bytes_c = await pg_adapter.count_blobs_anti_join(T, "")
        deleted, bytes_d = await pg_adapter.delete_blobs_anti_join(T, "")
        assert cnt == deleted == 2
        assert bytes_c == bytes_d

    async def test_null_blob_bytes_handled(self, pg_adapter):
        T = "thr-null"
        await _insert_checkpoint(pg_adapter, T, "", "c1", {"k": "v1"})
        await _insert_blob(pg_adapter, T, "", "k", "v1", None)  # nullable BYTEA
        cnt, bytes_freed = await pg_adapter.count_blobs_anti_join(T, "")
        assert cnt == 0 and bytes_freed == 0  # referenced — survives
        deleted, freed = await pg_adapter.delete_blobs_anti_join(T, "")
        assert deleted == 0 and freed == 0

    async def test_find_all_thread_ns_pairs_returns_all_no_having(self, pg_adapter):
        """D21: returns ALL pairs including single-checkpoint threads;
        ``HAVING COUNT(*) > 1`` is NOT applied here (whereas it IS applied
        in ``find_excess_checkpoint_groups``).
        """
        await _insert_checkpoint(pg_adapter, "single", "", "c1", {"a": "1"})
        await _insert_checkpoint(pg_adapter, "single", "", "c2", {"a": "1"})
        await _insert_checkpoint(pg_adapter, "multi", "", "c1", {"a": "1"})
        await _insert_checkpoint(pg_adapter, "multi", "child", "c1", {"a": "1"})
        await _insert_checkpoint(pg_adapter, "multi", "child", "c2", {"a": "1"})
        await _insert_checkpoint(pg_adapter, "multi", "child", "c3", {"a": "1"})

        pairs = await pg_adapter.find_all_thread_ns_pairs()
        m = {p[:2]: p[2] for p in pairs}
        assert ("single", "") in m and m[("single", "")] == 2
        assert ("multi", "") in m and m[("multi", "")] == 1   # single checkpoint survives
        assert ("multi", "child") in m and m[("multi", "child")] == 3

        # And the HAVING-flavoured method correctly drops single-checkpoint
        # threads (so the two are NOT interchangeable — D21 must be used).
        # With max=2: "multi"/"" (exactly 1 checkpoint) is EXCLUDED even
        # though the blob prune MUST visit it.
        excess = await pg_adapter.find_excess_checkpoint_groups(2)
        excess_keys = {(p[0], p[1]) for p in excess}
        assert ("multi", "") not in excess_keys, (
            "exactly-1-checkpoint pair is invisible to find_excess_… — "
            "the reason D21 mandates find_all_thread_ns_pairs"
        )
        assert ("single", "") not in excess_keys
        assert ("multi", "child") in excess_keys  # 3 > 2 → visible

    async def test_count_refs_zero_means_zero(self, pg_adapter):
        T = "thr-refs"
        await _insert_checkpoint(pg_adapter, T, "", "c1", {"m": "v1", "n": "v2"})
        refs = await pg_adapter.count_refs_for_blob_thread(T, "")
        assert refs == 2  # two distinct (channel, version) pairs

        # Different threads with no refs (no remaining checkpoints)
        refs2 = await pg_adapter.count_refs_for_blob_thread("never-seen", "")
        assert refs2 == 0
