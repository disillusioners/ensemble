"""Tests for Audit Findings M1 + M2 in
``SQLModelMessageQueueRepository``.

M1 — ``dequeue`` must use the atomic ``UPDATE-WHERE-subquery-AND-status
RETURNING *`` claim pattern (same shape as ``claim_pending_task`` in
the task repository). Under concurrent workers, at most one worker
observes a non-None result for a given ready message.

M2 — ``find_stuck_messages`` previously OR'd
``last_activity_at IS NULL`` with ``processing_started_at < threshold``
and then AND'd the whole expression with ``last_activity_at <
threshold``, which made the NULL branch unreachable (``NULL <
threshold`` is NULL, failing the outer AND). The fixed query correctly
OR's ``last_activity_at IS NULL`` with ``last_activity_at < threshold``
so BOTH stuck conditions surface.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.message_queue.models import MessageQueue, MessageStatus, MessageType
from daemon.repositories.message_queue.repository import SQLModelMessageQueueRepository


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def engine():
    """In-memory SQLite engine, shared across threads for concurrency tests."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def message_repo(engine):
    return SQLModelMessageQueueRepository(engine)


def _insert_ready_message(
    engine,
    *,
    instance_id: str = "inst-1",
    message_id: str | None = None,
    priority: int = 1,
    next_retry_at: datetime | None = None,
    enqueued_offset_seconds: float = 0.0,
    status: str = MessageStatus.READY.value,
) -> MessageQueue:
    """Insert a message in 'ready' status (or other desired status).

    ``enqueued_offset_seconds`` shifts ``enqueued_at`` into the past —
    useful for tests that need a stable ORDER BY ordering between two
    candidate messages.
    """
    mid = message_id or str(uuid.uuid4())
    enqueued_at = datetime.now(timezone.utc) - timedelta(seconds=enqueued_offset_seconds)
    with Session(engine) as session:
        m = MessageQueue(
            message_id=mid,
            instance_id=instance_id,
            content="fixture content",
            type=MessageType.AGENT.value,
            source="test",
            status=status,
            priority=priority,
            retry_count=0,
            max_retries=5,
            enqueued_at=enqueued_at,
            next_retry_at=next_retry_at,
        )
        session.add(m)
        session.commit()
        session.refresh(m)
        return m


def _insert_processing_message(
    engine,
    *,
    instance_id: str = "inst-1",
    message_id: str | None = None,
    processing_started_offset_seconds: float = 0.0,
    last_activity_offset_seconds: float | None = 0.0,
    status: str = MessageStatus.PROCESSING.value,
) -> MessageQueue:
    """Insert a message in 'processing' status.

    ``last_activity_offset_seconds=None`` means ``last_activity_at`` is
    left as NULL — the M2 NULL-branch fixture.
    """
    mid = message_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    processing_started_at = now - timedelta(seconds=processing_started_offset_seconds)
    if last_activity_offset_seconds is None:
        last_activity_at = None
    else:
        last_activity_at = now - timedelta(seconds=last_activity_offset_seconds)
    with Session(engine) as session:
        m = MessageQueue(
            message_id=mid,
            instance_id=instance_id,
            content="fixture content",
            type=MessageType.AGENT.value,
            source="test",
            status=status,
            priority=1,
            retry_count=0,
            max_retries=5,
            enqueued_at=now,
            processing_started_at=processing_started_at,
            last_activity_at=last_activity_at,
        )
        session.add(m)
        session.commit()
        session.refresh(m)
        return m


# ============================================================================
# M1 — dequeue() atomic claim
# ============================================================================


class TestDequeueAtomicClaim:
    """``dequeue`` uses the atomic UPDATE-RETURNING claim pattern.

    Selection criteria (must all hold):
    - ``status = 'ready'``
    - ``next_retry_at IS NULL OR next_retry_at <= now``
    - ``instance_id = :instance_id`` when provided

    Ordered by ``priority ASC, enqueued_at ASC``; LIMIT 1.

    Atomic claim: ``UPDATE ... WHERE message_id = (SELECT ...) AND
    status = 'ready' RETURNING *``. The outer ``AND status = 'ready'``
    is the EvalPlanQual guard — under PostgreSQL READ COMMITTED, if a
    concurrent worker has already claimed the candidate row, the outer
    guard re-evaluates against the post-lock row state and the UPDATE
    matches zero rows.
    """

    def test_dequeue_returns_none_when_no_messages(self, message_repo):
        assert message_repo.dequeue() is None

    def test_dequeue_returns_none_when_instance_has_no_messages(
        self, message_repo, engine
    ):
        _insert_ready_message(engine, instance_id="other-inst")
        assert message_repo.dequeue(instance_id="inst-1") is None

    def test_dequeue_claims_oldest_ready_message(self, message_repo, engine):
        """Of multiple ready messages, the lowest enqueued_at wins."""
        older = _insert_ready_message(
            engine, message_id="older", enqueued_offset_seconds=60.0
        )
        newer = _insert_ready_message(
            engine, message_id="newer", enqueued_offset_seconds=5.0
        )
        result = message_repo.dequeue()
        assert result is not None
        assert result.message_id == older.message_id
        assert result.status == MessageStatus.PROCESSING.value

    def test_dequeue_claims_highest_priority_first(self, message_repo, engine):
        """Lower priority value wins; ties broken by enqueued_at ASC."""
        # Enqueue high-priority (low number) message second
        high = _insert_ready_message(
            engine, message_id="high", priority=10, enqueued_offset_seconds=5.0
        )
        low = _insert_ready_message(
            engine, message_id="low", priority=1, enqueued_offset_seconds=60.0
        )
        result = message_repo.dequeue()
        assert result is not None
        # priority ASC: 1 wins over 10
        assert result.message_id == low.message_id

    def test_dequeue_sets_processing_timestamps(self, message_repo, engine):
        """On claim: status='processing', processing_started_at=now, last_activity_at=now."""
        before = datetime.now(timezone.utc)
        _insert_ready_message(engine)
        result = message_repo.dequeue()
        after = datetime.now(timezone.utc)

        assert result is not None
        assert result.status == MessageStatus.PROCESSING.value
        assert result.processing_started_at is not None
        assert result.last_activity_at is not None
        # Timestamps bracketed by before/after — both UTC-aware.
        assert before <= result.processing_started_at <= after
        assert before <= result.last_activity_at <= after

    def test_dequeue_filters_by_instance_id(self, message_repo, engine):
        """When instance_id is provided, only that instance's messages are eligible."""
        a = _insert_ready_message(engine, message_id="a", instance_id="inst-A")
        b = _insert_ready_message(engine, message_id="b", instance_id="inst-B")

        result = message_repo.dequeue(instance_id="inst-B")
        assert result is not None
        assert result.message_id == b.message_id
        assert result.instance_id == "inst-B"

        # inst-A's message is still ready (untouched)
        with Session(engine) as session:
            persisted_a = session.get(MessageQueue, a.message_id)
        assert persisted_a.status == MessageStatus.READY.value

    def test_dequeue_skips_messages_with_future_next_retry_at(
        self, message_repo, engine
    ):
        """A READY message with ``next_retry_at`` in the future is NOT eligible."""
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        _insert_ready_message(
            engine, message_id="future", next_retry_at=future
        )
        eligible = _insert_ready_message(
            engine, message_id="eligible", enqueued_offset_seconds=5.0
        )
        result = message_repo.dequeue()
        assert result is not None
        assert result.message_id == eligible.message_id

    def test_dequeue_claims_message_with_past_next_retry_at(
        self, message_repo, engine
    ):
        """A READY message with ``next_retry_at`` in the past IS eligible."""
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        msg = _insert_ready_message(
            engine, message_id="past-retry", next_retry_at=past
        )
        result = message_repo.dequeue()
        assert result is not None
        assert result.message_id == msg.message_id

    def test_dequeue_skips_non_ready_messages(self, message_repo, engine):
        """Processing / failed / completed messages must NOT be claimed."""
        _insert_ready_message(
            engine, message_id="proc", status=MessageStatus.PROCESSING.value
        )
        _insert_ready_message(
            engine, message_id="fail", status=MessageStatus.FAILED.value
        )
        _insert_ready_message(
            engine, message_id="done", status=MessageStatus.COMPLETED.value
        )
        eligible = _insert_ready_message(engine, message_id="ok")
        result = message_repo.dequeue()
        assert result is not None
        assert result.message_id == eligible.message_id

    def test_dequeue_claims_each_message_at_most_once(self, message_repo, engine):
        """Sequential dequeue calls drain distinct messages.

        After N dequeue calls against N ready messages, the (N+1)th
        call must return None.
        """
        ids = [
            _insert_ready_message(
                engine,
                message_id=f"m{i}",
                priority=i,  # distinct priority for deterministic order
            ).message_id
            for i in range(1, 6)
        ]
        claimed: list[str] = []
        for _ in range(5):
            r = message_repo.dequeue()
            assert r is not None
            claimed.append(r.message_id)
        # All distinct, in priority order
        assert sorted(claimed) == sorted(ids)
        assert claimed == sorted(ids, key=lambda mid: int(mid[1:]))
        # Sixth call returns None — nothing left
        assert message_repo.dequeue() is None

    def test_dequeue_concurrent_only_one_worker_wins(self, message_repo, engine):
        """The atomic claim must prevent double-claiming under concurrency.

        Two threads call dequeue() at the same instant against a single
        ready message. Exactly one of them must observe a non-None
        result; the other must observe None.
        """
        _insert_ready_message(engine, message_id="contested")

        results: list[MessageQueue | None] = [None, None]
        barrier = threading.Barrier(2)

        def worker(idx: int) -> None:
            barrier.wait()
            results[idx] = message_repo.dequeue()

        t1 = threading.Thread(target=worker, args=(0,))
        t2 = threading.Thread(target=worker, args=(1,))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        winners = [r for r in results if r is not None]
        losers = [r for r in results if r is None]
        assert len(winners) == 1, (
            f"Expected exactly one winner, got {len(winners)} winners "
            f"and {len(losers)} losers"
        )
        assert len(losers) == 1
        assert winners[0].message_id == "contested"
        assert winners[0].status == MessageStatus.PROCESSING.value

        # DB invariant: exactly one row in 'processing' for that message_id.
        with Session(engine) as session:
            row = session.get(MessageQueue, "contested")
        assert row.status == MessageStatus.PROCESSING.value

    def test_dequeue_concurrent_drains_n_messages_with_n_workers(
        self, message_repo, engine
    ):
        """N workers dequeueing against N messages must each claim a DISTINCT message.

        The critical invariant under concurrency is **no double-claim**:
        the atomic UPDATE-RETURNING must never let two workers each
        return the same message_id. Under SQLite + StaticPool,
        individual ``engine.begin()`` calls can race on the shared
        connection (the underlying sqlite3 module raises
        ``OperationalError: cannot commit transaction - SQL statements
        in progress``); we tolerate that some workers fail this way as
        long as the surviving winners are unique.
        """
        n = 4
        for i in range(n):
            _insert_ready_message(
                engine,
                message_id=f"m{i}",
                priority=i,  # distinct priority for deterministic order
            )

        results: list[MessageQueue | None] = [None] * n
        errors: list[BaseException | None] = [None] * n
        barrier = threading.Barrier(n)

        def worker(idx: int) -> None:
            try:
                barrier.wait()
                results[idx] = message_repo.dequeue()
            except BaseException as e:  # noqa: BLE001 — concurrent driver errors are noise here
                errors[idx] = e

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Critical invariant: every successful dequeue returned a UNIQUE message_id.
        winners = [r for r in results if r is not None]
        winner_ids = [r.message_id for r in winners]
        assert len(winner_ids) == len(set(winner_ids)), (
            f"Double-claim detected: {winner_ids}"
        )
        # At least one worker must have succeeded (i.e. the system is functional).
        assert len(winners) >= 1
        # Every winner is in 'processing'.
        assert all(r.status == MessageStatus.PROCESSING.value for r in winners)
        # All winners are from the inserted set.
        assert set(winner_ids).issubset({f"m{i}" for i in range(n)})

    def test_dequeue_with_instance_filter_under_concurrency(
        self, message_repo, engine
    ):
        """Concurrent dequeue with instance_id filter must respect the filter.

        Both workers target the SAME instance_id; only one of two
        messages for that instance is claimed per call.
        """
        a = _insert_ready_message(engine, message_id="a", instance_id="inst-A")
        b = _insert_ready_message(engine, message_id="b", instance_id="inst-A")
        # Decoy in another instance — must NEVER be claimed by these workers.
        _insert_ready_message(
            engine, message_id="decoy", instance_id="inst-B", priority=99
        )

        results: list[MessageQueue | None] = [None, None]
        barrier = threading.Barrier(2)

        def worker(idx: int) -> None:
            barrier.wait()
            results[idx] = message_repo.dequeue(instance_id="inst-A")

        t1 = threading.Thread(target=worker, args=(0,))
        t2 = threading.Thread(target=worker, args=(1,))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        winners = [r for r in results if r is not None]
        assert len(winners) == 2
        winner_ids = {r.message_id for r in winners}
        assert winner_ids == {a.message_id, b.message_id}
        # Decoy untouched
        with Session(engine) as session:
            decoy = session.get(MessageQueue, "decoy")
        assert decoy.status == MessageStatus.READY.value


# ============================================================================
# M2 — find_stuck_messages() OR-clause fix
# ============================================================================


class TestFindStuckMessages:
    """``find_stuck_messages`` must surface BOTH stuck conditions:

    1. ``status='processing'`` AND ``last_activity_at IS NULL`` —
       the worker never reported any activity since claiming.
    2. ``status='processing'`` AND ``last_activity_at < threshold`` —
       the worker's last heartbeat is older than
       ``MESSAGE_TIMEOUT_SECONDS`` (1 hour).

    Both branches are evaluated against the SAME timestamp column
    (``last_activity_at``). The previous implementation's
    ``OR(last_activity_at IS NULL, processing_started_at < threshold)
    AND last_activity_at < threshold`` made branch (1) unreachable
    because ``NULL < threshold`` is NULL, failing the AND.
    """

    def test_find_stuck_messages_returns_empty_when_nothing_processing(
        self, message_repo, engine
    ):
        assert message_repo.find_stuck_messages() == []

    def test_find_stuck_messages_skips_recent_processing_messages(
        self, message_repo, engine
    ):
        """A processing message whose last_activity_at is fresh is NOT stuck."""
        recent = _insert_processing_message(
            engine,
            message_id="recent",
            processing_started_offset_seconds=60.0,
            last_activity_offset_seconds=5.0,
        )
        result = message_repo.find_stuck_messages()
        assert recent.message_id not in {m.message_id for m in result}

    def test_find_stuck_messages_surfaces_old_last_activity(self, message_repo, engine):
        """Branch 2: processing AND last_activity_at < threshold."""
        # last_activity 2 hours ago — well past 1-hour threshold.
        stuck = _insert_processing_message(
            engine,
            message_id="stuck-old",
            processing_started_offset_seconds=7200.0,
            last_activity_offset_seconds=7200.0,
        )
        result = message_repo.find_stuck_messages()
        assert stuck.message_id in {m.message_id for m in result}

    def test_find_stuck_messages_surfaces_null_last_activity(self, message_repo, engine):
        """Branch 1 (the previously unreachable one): processing AND last_activity_at IS NULL.

        This is the regression test for M2. Before the fix, this row
        was never returned because the AND with
        ``last_activity_at < threshold`` filtered it out (``NULL <
        threshold`` is NULL).
        """
        stuck_null = _insert_processing_message(
            engine,
            message_id="stuck-null",
            processing_started_offset_seconds=7200.0,
            last_activity_offset_seconds=None,  # NULL — never reported activity
        )
        result = message_repo.find_stuck_messages()
        assert stuck_null.message_id in {m.message_id for m in result}, (
            "find_stuck_messages failed to surface a processing message "
            "with last_activity_at IS NULL — the previously unreachable "
            "OR branch"
        )

    def test_find_stuck_messages_surfaces_both_branches(self, message_repo, engine):
        """Both NULL and threshold branches surface in the same call."""
        null_branch = _insert_processing_message(
            engine,
            message_id="null-branch",
            processing_started_offset_seconds=7200.0,
            last_activity_offset_seconds=None,
        )
        old_branch = _insert_processing_message(
            engine,
            message_id="old-branch",
            processing_started_offset_seconds=7200.0,
            last_activity_offset_seconds=7200.0,
        )
        # And a NOT-stuck control row.
        _insert_processing_message(
            engine,
            message_id="healthy",
            processing_started_offset_seconds=60.0,
            last_activity_offset_seconds=5.0,
        )
        result = message_repo.find_stuck_messages()
        ids = {m.message_id for m in result}
        assert null_branch.message_id in ids
        assert old_branch.message_id in ids
        assert "healthy" not in ids

    def test_find_stuck_messages_skips_non_processing_statuses(self, message_repo, engine):
        """Only status='processing' rows are considered — ready/failed/completed are not 'stuck'."""
        # A 'ready' message with NULL last_activity_at must NOT surface.
        # (Not stuck — never claimed in the first place.)
        now = datetime.now(timezone.utc)
        with Session(engine) as session:
            m = MessageQueue(
                message_id="ready-null",
                instance_id="inst-1",
                content="x",
                type=MessageType.AGENT.value,
                source="test",
                status=MessageStatus.READY.value,
                priority=1,
                retry_count=0,
                max_retries=5,
                enqueued_at=now,
                processing_started_at=None,
                last_activity_at=None,
            )
            session.add(m)
            session.commit()

        result = message_repo.find_stuck_messages()
        assert "ready-null" not in {m.message_id for m in result}

    def test_find_stuck_messages_returns_message_objects(self, message_repo, engine):
        """Returned objects are MessageQueue instances with populated fields."""
        stuck = _insert_processing_message(
            engine,
            message_id="stuck-shape",
            processing_started_offset_seconds=7200.0,
            last_activity_offset_seconds=7200.0,
        )
        result = message_repo.find_stuck_messages()
        match = next((m for m in result if m.message_id == stuck.message_id), None)
        assert match is not None
        assert isinstance(match, MessageQueue)
        assert match.status == MessageStatus.PROCESSING.value
        assert match.last_activity_at is not None
        assert match.processing_started_at is not None


# ============================================================================
# Cross-cutting: dequeue returns MessageQueue, not raw row
# ============================================================================


class TestDequeueReturnShape:
    """``dequeue`` returns a fully-populated ``MessageQueue`` model
    (via the existing ``_row_to_message`` helper), matching the
    contract of ``complete`` / ``fail`` / ``retry``.
    """

    def test_dequeue_returns_message_queue_instance(self, message_repo, engine):
        msg = _insert_ready_message(
            engine,
            message_id="shape",
            priority=7,
        )
        result = message_repo.dequeue()
        assert isinstance(result, MessageQueue)
        assert result.message_id == msg.message_id
        assert result.priority == 7

    def test_dequeue_preserves_metadata(self, message_repo, engine):
        """JSON columns round-trip through ``_row_to_message``."""
        mid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        with Session(engine) as session:
            m = MessageQueue(
                message_id=mid,
                instance_id="inst-1",
                content="payload",
                type=MessageType.HUMAN.value,
                source="api",
                status=MessageStatus.READY.value,
                priority=1,
                retry_count=0,
                max_retries=5,
                message_metadata={"trace_id": "abc-123", "nested": {"k": 1}},
                images=["data:image/png;base64,AAA"],
                enqueued_at=now,
            )
            session.add(m)
            session.commit()

        result = message_repo.dequeue()
        assert result is not None
        assert result.message_metadata == {"trace_id": "abc-123", "nested": {"k": 1}}
        assert result.images == ["data:image/png;base64,AAA"]
