"""Real-PG proof for the deferred-DELETE audit-split (capture → chunked-delete).

Gate 3 of the "upgrade-system overhaul" mission proves the deferred-DELETE split
introduced onto ``MessageQueueRepository`` against a DEDICATED SCRATCH PG database
(``tests/postgres/conftest.py`` provides the session-scoped ``pg_engine`` whose
``drop_all`` + ``create_all`` runs against the commissioned DB).

Implementation targets:
- ``daemon.repositories.message_queue.repository.MessageQueueRepository.capture_doomed_ids_and_audit``
  (defined at ``repository.py:1022``)
- ``daemon.repositories.message_queue.repository.MessageQueueRepository.delete_doomed_ids``
  (defined at ``repository.py:1134``)
- F-1 contract at ``repository.py:1182-1191``: "preserve predicate is now
  RE-APPLIED at DELETE time" — the same NOT-IN subquery the capture ran,
  re-evaluated per chunk against LIVE task state.

Properties proven:

(a) CAPTURE EXCLUDES RUNNING / PAUSED TASKS — seed MQ+task rows across states
    (terminal-doomed, running, paused, pending, orphan-without-task); invoke
    the capture phase; assert the doomed set contains ONLY the eligible
    terminal-or-pending ids, never running/paused. Also assert the JOURNAL
    audit log line is written with the canonical prefix + cap'd id repr.
(b) DELETE RE-APPLIES THE PRESERVE PREDICATE PER CHUNK — invoke
    ``delete_doomed_ids`` with a small ``chunk_size`` over a doomed set sized
    to force ≥3 chunks, with some tasks' states flipped to in-flight-eligible
    (running/paused) AFTER capture; assert the DELETE-time predicate
    re-derivation (not the capture-time snapshot) governs each chunk — the
    flipped ids SURVIVE, the still-terminal ids are deleted, chunk boundaries
    don't leak survivors.
(c) IN-FLIGHT FLIP SURVIVES CAPTURE→DELETE — the literal race: an id
    captured while terminal, flipped to in-flight BEFORE DELETE executes,
    must SURVIVE the delete (message row still present and queryable
    afterwards). This is the env-poison occ-#5 incident class.

Self-contained: does NOT import the stub-poisoning unit-test file noted in
the brief (``tests/unit/tools/test_audit_after_soak_split.py``).

Run with::

    .venv/bin/pytest tests/postgres/test_audit_split_scratch_pg.py \\
        --override-ini="addopts=" -m postgres -q --tb=short -p no:randomly

Scratch DB lifecycle (CREATE DATABASE / DROP DATABASE) is owned by the
commissioning gate, NOT by this test — the conftest session-scoped engine
just gets attached to the scratch DB via ``PG_TEST_DB``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlmodel import Session

from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)
from daemon.repositories.task.models import Task, TaskStatus


# Auto-apply the postgres marker so ``-m postgres`` selects these tests
# and the default ``addopts`` skips them otherwise.
pytestmark = pytest.mark.postgres


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_mq_row(
    session: Session, instance_id: str, status: str = MessageStatus.READY.value
) -> str:
    """Insert a MessageQueue row; return its generated message_id."""
    mid = str(uuid.uuid4())
    row = MessageQueue(
        message_id=mid,
        instance_id=instance_id,
        content="audit-split scratch payload",
        type=MessageType.AGENT.value,
        status=status,
        enqueued_at=datetime.utcnow(),
    )
    session.add(row)
    session.commit()
    return mid


def _seed_task_row(
    session: Session,
    instance_id: str,
    message_id: str,
    status: str,
) -> str:
    """Insert a Task row at the requested status; return its work_id."""
    row = Task(
        work_id=str(uuid.uuid4()),
        instance_id=instance_id,
        message_id=message_id,
        status=status,
    )
    session.add(row)
    session.commit()
    return row.work_id


def _flip_task_status(session: Session, message_id: str, new_status: str) -> None:
    """UPDATE the backing task's status by message_id (the in-flight flip)."""
    session.exec(
        text("UPDATE task SET status = :new_status WHERE message_id = :mid"),
        params={"new_status": new_status, "mid": message_id},
    )
    session.commit()


def _read_mq_ids(session: Session) -> set[str]:
    rows = session.exec(text("SELECT message_id FROM message_queue")).all()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# (a) CAPTURE EXCLUDES RUNNING / PAUSED TASKS
# ---------------------------------------------------------------------------


def test_a_capture_phase_excludes_running_and_paused_tasks(
    pg_repository_factory, pg_session_factory, caplog
):
    """Property (a): the capture SQL is

        SELECT message_id FROM message_queue
        WHERE message_id NOT IN (
            SELECT message_id FROM task
            WHERE status IN ('running', 'paused') [OR arm-3 clause]
        )

    So MQ rows whose backing task is 'running' or 'paused' MUST NOT appear
    in the doomed set. MQ rows whose task is terminal-or-pending (or no
    task at all) MUST be captured.
    """
    mq_repo = pg_repository_factory(SQLModelMessageQueueRepository)

    expected_captured: list[str] = []
    expected_preserved: list[str] = []

    with pg_session_factory() as session:
        # Orphan — no backing task: still captured (NOT IN subquery is TRUE).
        expected_captured.append(
            _seed_mq_row(session, "i-orphan", status=MessageStatus.COMPLETED.value)
        )
        # Terminal task states (completed/cancelled/failed): captured.
        for instance_suffix, task_status in (
            ("done", TaskStatus.COMPLETED.value),
            ("cancel", TaskStatus.CANCELLED.value),
            ("failed", TaskStatus.FAILED.value),
        ):
            mid = _seed_mq_row(
                session, f"i-{instance_suffix}", status=MessageStatus.READY.value
            )
            _seed_task_row(session, f"i-{instance_suffix}", mid, task_status)
            expected_captured.append(mid)
        # PENDING task: arm-1 predicate excludes ONLY ('running', 'paused');
        # pending is NOT in that exclusion set, so the MQ row IS captured.
        mid_pending = _seed_mq_row(
            session, "i-pending", status=MessageStatus.READY.value
        )
        _seed_task_row(session, "i-pending", mid_pending, TaskStatus.PENDING.value)
        expected_captured.append(mid_pending)
        # RUNNING + PAUSED tasks: preserved by arm-1 predicate.
        mid_run = _seed_mq_row(session, "i-run", status=MessageStatus.PROCESSING.value)
        _seed_task_row(session, "i-run", mid_run, TaskStatus.RUNNING.value)
        expected_preserved.append(mid_run)
        mid_pause = _seed_mq_row(session, "i-pause", status=MessageStatus.READY.value)
        _seed_task_row(session, "i-pause", mid_pause, TaskStatus.PAUSED.value)
        expected_preserved.append(mid_pause)

    with caplog.at_level(logging.WARNING):
        doomed = mq_repo.capture_doomed_ids_and_audit(preserve_in_flight=True)

    captured = set(doomed)

    # Capture set equality (order-independent, no leakage).
    assert captured == set(expected_captured), (
        f"capture_doomed_ids_and_audit(preserve_in_flight=True) returned "
        f"{sorted(captured)} but expected {sorted(expected_captured)}"
    )
    # Explicit individual assertions for the decisive exclusion cases.
    assert mid_run not in captured, (
        "MQ row whose backing task is RUNNING must not be captured (arm-1)"
    )
    assert mid_pause not in captured, (
        "MQ row whose backing task is PAUSED must not be captured (arm-1)"
    )

    # Audit-log assertion: the JOURNAL warning line shape is the F-1 contract.
    journal_records = [
        rec for rec in caplog.records if "JOURNAL" in rec.getMessage()
    ]
    assert any(
        "preserve_in_flight=True" in rec.getMessage() for rec in journal_records
    ), (
        "Missing preserve_in_flight=True audit log line. Saw journal records: "
        f"{[rec.getMessage() for rec in journal_records]}"
    )
    # Also: the cap'd id repr should be present (the audit row payload).
    assert any(
        "doomed_message_ids=" in rec.getMessage() for rec in journal_records
    ), (
        "Missing doomed_message_ids= payload in audit line. "
        f"Saw: {[rec.getMessage() for rec in journal_records]}"
    )


# ---------------------------------------------------------------------------
# (b) DELETE RE-APPLIES THE PRESERVE PREDICATE PER CHUNK
# ---------------------------------------------------------------------------


def test_b_delete_reapplies_preserve_predicate_per_chunk(
    pg_repository_factory, pg_session_factory
):
    """Property (b): the delete-time predicate re-derivation governs each chunk.

    Seed 5 MQ+task rows all in a terminal-task state; capture picks all 5;
    BETWEEN capture and delete, flip some captured ids to in-flight-eligible
    (running/paused). delete_doomed_ids with chunk_size=2 produces 3 chunks.
    The DELETE-time predicate must keep the flipped ids alive (per chunk),
    NOT the capture-time snapshot. No leakage across chunk boundaries.
    """
    mq_repo = pg_repository_factory(SQLModelMessageQueueRepository)

    mq_ids: list[str] = []
    with pg_session_factory() as session:
        for i in range(5):
            mid = _seed_mq_row(session, f"i-b{i}", status=MessageStatus.READY.value)
            _seed_task_row(session, f"i-b{i}", mid, TaskStatus.COMPLETED.value)
            mq_ids.append(mid)

    # Capture picks all 5 (all are backed by terminal tasks at capture time).
    doomed = mq_repo.capture_doomed_ids_and_audit(preserve_in_flight=True)
    assert set(doomed) == set(mq_ids), (
        f"capture should see all 5 doomed MQ rows, got {sorted(doomed)}"
    )
    assert len(doomed) == 5

    # BETWEEN capture & delete: flip mq_ids[2] → running, mq_ids[4] → paused.
    with pg_session_factory() as session:
        _flip_task_status(session, mq_ids[2], TaskStatus.RUNNING.value)
        _flip_task_status(session, mq_ids[4], TaskStatus.PAUSED.value)

    # Chunk_size=2 → chunks: [d0,d1], [d2,d3], [d4]. The flipped d2 and d4
    # MUST survive their respective chunks; d0/d1/d3 are deleted.
    deleted_count = mq_repo.delete_doomed_ids(
        doomed_ids=doomed, preserve_in_flight=True, chunk_size=2
    )

    assert deleted_count == 3, (
        f"expected 3 deletes (d0, d1, d3); flipped d2 + d4 must survive, "
        f"got deleted_count={deleted_count}"
    )

    # Post-delete state: only the flipped-survivors remain in message_queue.
    with pg_session_factory() as session:
        remaining = _read_mq_ids(session)

    assert remaining == {mq_ids[2], mq_ids[4]}, (
        f"only the flipped ids should remain after chunked delete; got {remaining}"
    )
    # Cross-boundary double-check: d2 sits in chunk 2, d4 sits in chunk 3 — both
    # preserve-eligible, both survive — chunk boundaries don't leak survivors.
    assert mq_ids[2] in remaining, "d2 (in chunk 2) leaked — boundary violated"
    assert mq_ids[4] in remaining, "d4 (in chunk 3, size=1) leaked — boundary violated"


# ---------------------------------------------------------------------------
# (c) RACE: capture-while-terminal → flip to in-flight before delete
# ---------------------------------------------------------------------------


def test_c_inflight_flip_after_capture_survives_delete(
    pg_repository_factory, pg_session_factory
):
    """Property (c): the env-poison occ-#5 race.

    Capture snapshot says doomed → flip to in-flight before delete → message
    row SURVIVES because the DELETE re-applies the predicate per the F-1
    contract. The surviving MQ row remains queryable by message_id.
    """
    mq_repo = pg_repository_factory(SQLModelMessageQueueRepository)

    with pg_session_factory() as session:
        mid_raced = _seed_mq_row(
            session, "i-race", status=MessageStatus.READY.value
        )
        _seed_task_row(session, "i-race", mid_raced, TaskStatus.COMPLETED.value)
        mid_other = _seed_mq_row(
            session, "i-other", status=MessageStatus.READY.value
        )
        _seed_task_row(session, "i-other", mid_other, TaskStatus.COMPLETED.value)

    # Capture picks both (terminal-backing tasks at capture time).
    doomed = mq_repo.capture_doomed_ids_and_audit(preserve_in_flight=True)
    assert set(doomed) == {mid_raced, mid_other}

    # Race: flip the backing task of mid_raced to RUNNING BEFORE delete.
    with pg_session_factory() as session:
        _flip_task_status(session, mid_raced, TaskStatus.RUNNING.value)

    deleted_count = mq_repo.delete_doomed_ids(
        doomed_ids=doomed, preserve_in_flight=True, chunk_size=1
    )
    assert deleted_count == 1, (
        f"only mid_other should be deleted; mid_raced must survive because "
        f"the DELETE-time predicate sees its task as 'running'. "
        f"Got deleted_count={deleted_count}"
    )

    # Post-delete: only the raced-and-flipped row remains, and remains
    # individually queryable.
    with pg_session_factory() as session:
        rows = session.exec(
            text("SELECT message_id FROM message_queue ORDER BY message_id")
        ).all()
        remaining_ids = [r[0] for r in rows]
        assert remaining_ids == [mid_raced], (
            f"only the flipped-survivor should remain; got {remaining_ids}"
        )
        # Direct fetch by primary key — the surviving row is queryable.
        direct = session.get(MessageQueue, mid_raced)
        assert direct is not None and direct.message_id == mid_raced, (
            "surviving MQ row is not individually queryable by message_id"
        )
        # And the deleted one is genuinely gone (no ghost row).
        ghost = session.get(MessageQueue, mid_other)
        assert ghost is None, (
            f"mid_other (the non-flipped doomed row) was NOT deleted — "
            f"delete_doomed_ids returned {deleted_count} but the row persists"
        )
