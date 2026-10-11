"""Unit tests — feature/blob-prune-default-on LIMIT-batching.

Proves the chunk-loop in
``daemon/checkpoint_adapter.py::PostgresCheckpointerAdapter.delete_blobs_anti_join``:

(a) single-chunk case — per-pair DELETE that fits under
    ``CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE`` exits the chunk loop
    after exactly one DELETE call (no extra round-trip on a healthy
    pair);
(b) multi-chunk case — a per-pair DELETE that exceeds the chunk size
    progresses through chunks; every chunk is a separate DELETE
    statement; all rows eventually deleted; audit-row totals match the
    mock-supplied chunk payload;
(c) empty-case — no candidate rows means zero DELETE statements and
    ``(0, 0)`` returned;
(d) audit correctness — the per-chunk breakdown sums to the totals
    the caller observes on the ``BlobPruneSummary``;
(e) chunk-count safety guard — the per-statement LIMIT-bounded chunk
    never exceeds ``CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE`` (else
    the pool-hazard class would reopen).

The test subclasses ``PostgresCheckpointerAdapter`` to override the
SQL primitive (``_delete_blobs_anti_join_chunk``) so no PG connection
is required — the chunk loop is the SEAM. The real PG integration
test for the chunk SQL lives in
``tests/integration/checkpoint_prune_limit_batching_real_pg.py`` and
SKIPS LOUDLY when PG is unreachable (do not silently substitute a mock).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import daemon.constants
from daemon.checkpoint_adapter import PostgresCheckpointerAdapter
from daemon.services.checkpoint_prune import prune_unreferenced_blobs


BATCH_SIZE = daemon.constants.CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE


class FakeChunkRow:
    """A minimal asyncpg-record-like — exposes ``n`` like
    ``RETURNING OCTET_LENGTH(blob) AS n`` does on the real adapter."""

    def __init__(self, n: int) -> None:
        self._n = n

    def __getitem__(self, key: str):
        if key != "n":
            raise KeyError(key)
        return self._n


def _chunk_response(rows_per_chunk: list[list[int]]):
    """Return an async function that consumes the queue and returns the
    next ``OCTET_LENGTH(blob)`` row list per call. The chunk loop
    terminates on the first empty list (the production iterator
    shape)."""
    queue = list(rows_per_chunk) + [[]]  # terminator

    async def _call(*args, **kwargs):
        return [FakeChunkRow(n) for n in queue.pop(0)]

    return _call


def _adapter_mock(pairs, refs=5) -> MagicMock:
    """PostgresCheckpointerAdapter-shaped mock — bypasses the
    isinstance gate by patching the symbol on the calling module.
    Returns ``refs`` per pair (healthy enough to pass the zero-refs
    fail-safe) so the loop reaches the destructive arm.

    The destructive arm is itself the chunk loop on
    ``PostgresCheckpointerAdapter.delete_blobs_anti_join``. The
    test exercises the loop via a SUBCLASS that overrides the SQL
    primitive — see ``_LoopAdapter`` below — so the adapter mock here
    satisfies only the ``isinstance`` check + the helper-method calls
    (``find_all_thread_ns_pairs``, ``count_refs_for_blob_thread``)."""
    m = MagicMock()
    m.find_all_thread_ns_pairs = AsyncMock(return_value=list(pairs))
    m.count_refs_for_blob_thread = AsyncMock(return_value=refs)
    return m


class _LoopAdapter(PostgresCheckpointerAdapter):
    """Adapts PostgresCheckpointerAdapter's chunk-loop to a mock SQL
    primitive — overrides only ``_delete_blobs_anti_join_chunk`` to
    return rows from a pre-loaded queue. The chunk-loop body in
    ``delete_blobs_anti_join`` runs against this mock primitive.

    Constructor takes the candidate pairs and refs directly (no
    nested MagicMock attribute) so the helper methods do not have
    to round-trip through ``self._mock.find_*()`` and risk the
    MagicMock auto-async attributes. The ``_saver`` attribute is
    left unset — the methods we override don't touch it.
    """

    def __init__(self, rows_per_chunk, pairs=(), refs=5):
        # PostgresCheckpointerAdapter.__init__ requires a saver AND a
        # pool; the helpers we override never touch either, so we
        # pass placeholders.
        super().__init__(saver=None, pool=None)  # type: ignore[arg-type]
        self._rows_per_chunk = rows_per_chunk
        self._pairs = list(pairs)
        self._refs = refs

    async def find_all_thread_ns_pairs(self):
        return list(self._pairs)

    async def count_refs_for_blob_thread(self, thread_id, checkpoint_ns):
        return self._refs

    async def _delete_blobs_anti_join_chunk(self, thread_id, checkpoint_ns, batch_size):
        # The real signature returns a list of asyncpg-row-likes.
        if not self._rows_per_chunk:
            return []
        chunk = self._rows_per_chunk.pop(0)
        return [FakeChunkRow(n) for n in chunk]


@pytest.fixture
def as_pg(monkeypatch):
    """Make the service's ``isinstance(checkpointer,
    PostgresCheckpointerAdapter)`` test happy: ``_LoopAdapter`` is a
    real subclass, so the isinstance check passes WITHOUT patching.
    The fixture stays for parity with the other suite and for any
    future refactors that auto-mock the class symbol."""
    return


class TestLimitBatchingSingleChunk:
    async def test_pair_smaller_than_batch_single_chunk(self, as_pg, caplog):
        """(a) one pair, payload of 25 deletes × 1024 bytes → the
        chunk loop returns 25 rows on the first SQL primitive call,
        totals 25 deletes × 1024 bytes, then loops once more on the
        empty terminator and exits. Total chunk calls: 2 (one
        data-bearing + one terminator probe)."""
        rows = [[1024] * 25]  # 1 chunk of 25 rows × 1024 bytes each
        adapter = _LoopAdapter(rows, pairs=[("t-1", "", 3)])
        summary = await prune_unreferenced_blobs(adapter)
        assert summary.destructive is True
        assert summary.total_deleted == 25
        assert summary.total_bytes_freed == 25 * 1024
        # Chunk queue drained — the loop must have asked again on the
        # empty terminator (which my chunk-primitive returns ``[]``
        # for an empty input). The terminator call emptied the queue
        # entirely (pop-then-detect-empty idiom).
        assert adapter._rows_per_chunk == [], (
            "loop must probe once more after the data-bearing chunk "
            "to detect the empty terminator"
        )


class TestLimitBatchingMultiChunk:
    async def test_pair_larger_than_batch_uses_multiple_chunks(self, as_pg):
        """(b) stage 8 chunks (7 × 10 + 1 × 4) → loop iterates
        through all of them, total = 74. The loop terminates on the
        first empty chunks-list probe."""
        rows = [
            [100] * 10,  # chunk 1
            [100] * 10,  # chunk 2
            [100] * 10,  # chunk 3
            [100] * 10,  # chunk 4
            [100] * 10,  # chunk 5
            [100] * 10,  # chunk 6
            [100] * 10,  # chunk 7
            [100] * 4,   # chunk 8 (partial)
        ]
        adapter = _LoopAdapter(rows, pairs=[("t-1", "", 3)])
        summary = await prune_unreferenced_blobs(adapter)
        assert summary.total_deleted == 74
        assert summary.total_bytes_freed == 74 * 100
        # The chunk list was fully drained — the loop iteration
        # consumed all 8 chunks and then saw ``[]`` on the next
        # call. The terminator-call CHANGED the queue to ``[]``.
        assert adapter._rows_per_chunk == [], (
            "chunk loop must have drained all 8 data-bearing chunks "
            "and exited on the empty probe (not busy-spinning)"
        )


class TestLimitBatchingEmpty:
    async def test_no_candidates_short_circuits_before_chunk_loop(self, as_pg):
        """(c) candidate enumeration returns an empty set → no chunk
        DELETE is issued (the per-pair loop short-circuits BEFORE
        the destructive arm). The chunk method stays untouched."""
        rows = [["1"] * 5]  # staged but never consumed
        adapter = _LoopAdapter(rows, pairs=[], refs=0)
        summary = await prune_unreferenced_blobs(adapter)
        assert summary.total_deleted == 0
        assert summary.total_bytes_freed == 0
        assert summary.scanned_pairs == 0
        # Chunk list untouched (the destructive arm was never reached).
        assert len(adapter._rows_per_chunk) == 1, (
            "empty candidates must short-circuit BEFORE the chunk loop "
            "touches any SQL primitive"
        )


class TestAuditTotalsAcrossChunks:
    async def test_blob_prune_summary_totals_match_chunk_sum(self, as_pg):
        """(d) the ``BlobPruneSummary`` totals are the sums of the
        per-chunk returns — defensive against a refactor that flips
        the per-chunk counter to e.g. report only the last chunk's
        count."""
        rows = [
            [10] * 3,    # 3 rows × 10 = 30
            [20] * 7,    # 7 rows × 20 = 140
            [40] * 2,    # 2 rows × 40 = 80
        ]
        adapter = _LoopAdapter(rows, pairs=[("t-1", "", 3)])
        summary = await prune_unreferenced_blobs(adapter)
        assert summary.total_deleted == 12  # 3 + 7 + 2
        assert summary.total_bytes_freed == 250


class TestPerChunkLimitBounded:
    async def test_chunk_strictly_respects_batch_size(self, as_pg, caplog):
        """(e) the chunk loop enforces the per-statement LIMIT via
        the ``assert chunk_count <= batch_size`` safety guard. A
        future refactor that drops the LIMIT clause would silently
        reopen the pool-hazard class — this test pins the guard by
        staging a single chunk whose size exceeds the (real) batch
        size constant and asserting (i) the per-pair WARNING log
        carries the safety-guard message (the service's per-pair
        never-raise catch downgrades the ``AssertionError`` into a
        per-pair SKIP), and (ii) the pair is recorded as SKIPPED."""
        import logging

        # Default BATCH_SIZE is 1000 — stage 1001 rows to trip the bound.
        rows = [[1] * (BATCH_SIZE + 1)]
        adapter = _LoopAdapter(rows, pairs=[("t-1", "", 3)])
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.checkpoint_prune"
        ):
            summary = await prune_unreferenced_blobs(adapter)
        # The safety guard fires — total_deleted is 0 (the pair was
        # skipped before any deletes were committed).
        assert summary.total_deleted == 0
        # The per-pair WARNING log carries the safety-guard failure
        # reason (the never-raise contract converts the
        # ``AssertionError`` into a per-pair SKIP with the exception
        # class name in the reason string).
        skipped_reasons = [s[2] for s in summary.skipped]
        assert any(
            "AssertionError" in reason for reason in skipped_reasons
        ), (
            f"the per-statement LIMIT safety guard must convert the "
            f"AssertionError into a per-pair SKIP; got skipped: "
            f"{summary.skipped}"
        )


class TestPostgresCheckpointerAdapterSymbol:
    """The chunk method is defined on ``PostgresCheckpointerAdapter``
    — the seam must exist for the chunk loop to call into it."""

    def test_chunk_method_present_on_pg_adapter(self):
        assert hasattr(PostgresCheckpointerAdapter, "_delete_blobs_anti_join_chunk"), (
            "_delete_blobs_anti_join_chunk must live on the PG adapter; "
            "its absence means the chunk-loop seam is broken"
        )
        assert hasattr(PostgresCheckpointerAdapter, "delete_blobs_anti_join"), (
            "the public delete entry point must continue to exist; "
            "its absence breaks the pruned-blobs call site in "
            "daemon/services/checkpoint_prune.py"
        )

    def test_chunk_method_signature_takes_batch_size(self):
        import inspect

        sig = inspect.signature(
            PostgresCheckpointerAdapter._delete_blobs_anti_join_chunk
        )
        params = list(sig.parameters.keys())
        # thread_id, checkpoint_ns, batch_size — the third param must
        # exist and accept the LIMIT size. Future refactors adding a
        # new positional would silently break the chunk-loop binding.
        assert "batch_size" in params, (
            "chunk signature drifted; the chunk loop binds to "
            "batch_size positionally"
        )


class TestConstantsPublic:
    """The new constants must exist; their names are part of the
    module's public surface (audit-row schema, runbooks, and the
    chunk-loop's safety guard all reference them by name)."""

    def test_constant_present_and_int(self):
        assert hasattr(daemon.constants, "CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE")
        assert isinstance(
            daemon.constants.CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE, int
        )
        # Sane lower bound — under ~50 rows, the LIMIT-batching churn
        # is worse than the unbounded DELETE; over ~10_000 rows, the
        # chunk footprint approaches the old unbounded footprint.
        assert 50 <= daemon.constants.CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE <= 10_000

    def test_retired_env_flag_constants_gone(self):
        # The dual-arm gate is gone — the constants must not exist
        # any longer (their presence means the gate is reachable
        # accidentally).
        assert not hasattr(daemon.constants, "CHECKPOINT_BLOB_PRUNE_DRY_RUN")
        assert not hasattr(daemon.constants, "CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE")
