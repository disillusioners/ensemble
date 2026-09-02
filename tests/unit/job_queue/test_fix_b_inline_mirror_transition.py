"""Unit tests for Fix B — inline idempotent mirror transition.

The new transition lives in
``daemon/repositories/job_queue/repository.py::finalize_mirror_job_at_completion``.
Its contract:

  * For ``job_type == 'message'`` JobItems only — TASK (mission)
    rows are left to the bus-gated finalize path (scope discipline).
  * Idempotent: rowcount == 0 (already terminal, missing job, or
    non-message) is a silent ``None`` return. The core safety property.
  * Race-safe: concurrent writers cannot both observe the SQL
    ``admission_state IN ('queued','active')`` predicate as true.
  * Goes through ``job_state_machine.validate_transition`` BEFORE the
    SQL guard (the 8 legacy writers bypass it; this new writer
    demonstrates the right shape — the example, not the bypass class).
  * Stamps ``terminal_reason='completed'`` (organic-style — closes
    the old cosmetic gap of empty ``terminal_reason`` on sweep-finalized
    rows).

Each test below pins one bullet of the contract. The whole file is
the acceptance suite for Fix B's repository-level writer.

F11 / shared-worktree hazard: file-backed SQLite via ``concurrent_engine``
(see ``tests/job_queue/conftest.py``) — NOT StaticPool / :memory:, whose
single shared connection trips the documented cross-thread lost-write
hazard. The race-safety test (``test_double_fire_is_one_transition``)
exercises the file-backed engine to produce a clean concurrent
``rowcount == 1`` for the winning thread and ``rowcount == 0`` for the
loser.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

# Register every model on ``SQLModel.metadata`` BEFORE ``create_all`` —
# mirrors the harness in ``tests/test_observer_failed_at_stamp.py``.
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.job_queue import JobRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.services.job_state_machine import (
    InvalidTransitionError,
    job_state_machine,
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures — minimal engine, in-memory SQLite (StaticPool) for the
# single-thread tests; the concurrent test uses file-backed SQLite via
# the ``concurrent_engine`` fixture from ``tests/job_queue/conftest.py``.
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """Real in-memory SQLite engine (StaticPool) — single-thread tests.

    The single-thread tests below do not exercise cross-thread race
    detection; StaticPool's single shared connection is sufficient and
    faster. The concurrent test (``test_double_fire_is_one_transition``)
    uses ``concurrent_engine`` instead so the SQL ``IN`` predicate
    sees real cross-connection visibility.
    """
    from sqlalchemy.pool import StaticPool

    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def job_repo(engine) -> JobRepository:
    """A ``JobRepository`` wired against the in-memory engine."""
    return JobRepository(engine)


def _seed_instance(engine, *, status: str = "running") -> str:
    """Seed a minimal instance row (the mirror's ``instance_id``)."""
    from daemon.repositories.instance.models import Instance

    iid = f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="developer",
                agent_dir="/tmp/agent",
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        s.commit()
    return iid


def _seed_message_job(
    engine,
    *,
    instance_id: str,
    admission_state: str = AdmissionState.ACTIVE.value,
) -> JobItem:
    """Seed a message-mirror JobItem with the given admission_state.

    Mirrors the shape ``enqueue_message_job`` writes at dispatch time
    (job_type='message', admission_state='active', instance_id set).
    """
    jid = f"job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        item = JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="/tmp/agent",
            message="unit-test message",
            source="api",
            job_type="message",
            admission_state=admission_state,
            instance_id=instance_id,
            project_id="test-project",
            job_metadata={},
        )
        s.add(item)
        s.commit()
        s.refresh(item)
    return item


def _seed_task_job(
    engine,
    *,
    instance_id: str,
    admission_state: str = AdmissionState.ACTIVE.value,
) -> JobItem:
    """Seed a TASK (mission) JobItem — for the scope-discipline tests."""
    jid = f"job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        item = JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="/tmp/agent",
            message="unit-test task",
            source="api",
            job_type="task",
            admission_state=admission_state,
            instance_id=instance_id,
            project_id="test-project",
            job_metadata={},
        )
        s.add(item)
        s.commit()
        s.refresh(item)
    return item


def _read(engine, job_id: str) -> JobItem | None:
    with Session(engine) as s:
        return s.get(JobItem, job_id)


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────


class TestFixBMirrorTransitionHappyPath:
    """The T0 transition: ``active → done`` with ``terminal_reason='completed'``."""

    def test_active_message_job_finalizes_to_done_with_completed_reason(
        self, engine, job_repo
    ):
        """An ACTIVE message JobItem reached by ``finalize_mirror_job_at_completion``
        transitions to ``done`` with ``terminal_reason='completed'`` —
        the organic-style stamp the spec requires.
        """
        iid = _seed_instance(engine)
        job = _seed_message_job(engine, instance_id=iid)

        finalized = job_repo.finalize_mirror_job_at_completion(job.job_id)

        assert finalized is not None, (
            "T0 transition must return the updated JobItem"
        )
        assert finalized.admission_state == AdmissionState.DONE.value
        assert finalized.terminal_reason == "completed", (
            "Inline transition must stamp terminal_reason='completed' "
            "(organic-style — closes the old cosmetic gap of empty "
            "terminal_reason on sweep-finalized rows)"
        )

        # Strong form: assert via re-read (the same row must be in
        # the DB, not just in the returned JobItem object's memory).
        row = _read(engine, job.job_id)
        assert row is not None
        assert row.admission_state == AdmissionState.DONE.value
        assert row.terminal_reason == "completed"

    def test_queued_message_job_finalizes_to_done(
        self, engine, job_repo
    ):
        """A QUEUED message JobItem reached by ``finalize_mirror_job_at_completion``
        also transitions to ``done`` — covers the path where the Task
        completed BEFORE the dispatch worker promoted the JobItem to
        ACTIVE (the ``(queued, done)`` row of ``VALID_TRANSITIONS``).

        This is the legitimate "Task done before dispatch promote"
        shape — the SQL guard ``IN ('queued','active')`` covers both.
        """
        iid = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=iid,
            admission_state=AdmissionState.QUEUED.value,
        )

        finalized = job_repo.finalize_mirror_job_at_completion(job.job_id)
        assert finalized is not None
        assert finalized.admission_state == AdmissionState.DONE.value
        assert finalized.terminal_reason == "completed"


class TestFixBMirrorTransitionIdempotency:
    """The CORE SAFETY PROPERTY — rowcount == 0 is silent no-op."""

    def test_double_fire_returns_none_on_second_call(
        self, engine, job_repo
    ):
        """Two consecutive calls on the same message JobItem: the first
        transitions ``active → done``; the second returns ``None``
        without raising (already terminal — silent no-op).

        Idempotency is the core safety property: race-loss with a
        concurrent observer finalize, the instance-terminal cascade,
        or ``force_finalize_orphan`` MUST NOT raise.
        """
        iid = _seed_instance(engine)
        job = _seed_message_job(engine, instance_id=iid)

        # First call: wins the SQL guard.
        first = job_repo.finalize_mirror_job_at_completion(job.job_id)
        assert first is not None
        assert first.admission_state == AdmissionState.DONE.value

        # Second call: row is already terminal — silent no-op.
        second = job_repo.finalize_mirror_job_at_completion(job.job_id)
        assert second is None, (
            "Idempotency contract: a second call on an already-terminal "
            "row must return None (silent no-op), not raise"
        )

        # Re-read: state unchanged.
        row = _read(engine, job.job_id)
        assert row is not None
        assert row.admission_state == AdmissionState.DONE.value
        assert row.terminal_reason == "completed"

    def test_existing_done_row_is_silent_noop(
        self, engine, job_repo
    ):
        """Pre-existing ``done`` row (e.g. f2 already finalized it
        before the inline transition was wired) — silent ``None``."""
        iid = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=iid,
            admission_state=AdmissionState.DONE.value,
        )
        # Stale terminal_reason simulates a pre-existing done row.
        with Session(engine) as s:
            row = s.get(JobItem, job.job_id)
            row.terminal_reason = "completed"
            s.commit()

        result = job_repo.finalize_mirror_job_at_completion(job.job_id)
        assert result is None

        # terminal_reason is unchanged (no overwrite).
        after = _read(engine, job.job_id)
        assert after.terminal_reason == "completed"

    def test_existing_dead_row_is_silent_noop(
        self, engine, job_repo
    ):
        """Pre-existing ``dead`` row (e.g. an older dead-lettered
        message mirror) — silent ``None`` and ``terminal_reason``
        untouched. The ``validate_transition`` pre-check is a
        fail-fast for programming errors but DEAD is a terminal
        state where ``(dead, done)`` would be illegal."""
        iid = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=iid,
            admission_state=AdmissionState.DEAD.value,
        )
        with Session(engine) as s:
            row = s.get(JobItem, job.job_id)
            row.terminal_reason = "aborted"
            s.commit()

        result = job_repo.finalize_mirror_job_at_completion(job.job_id)
        assert result is None

        after = _read(engine, job.job_id)
        assert after.admission_state == AdmissionState.DEAD.value
        assert after.terminal_reason == "aborted"

    def test_missing_job_id_returns_none(
        self, engine, job_repo
    ):
        """A non-existent ``job_id`` returns ``None`` silently."""
        result = job_repo.finalize_mirror_job_at_completion(
            "job-does-not-exist"
        )
        assert result is None

    def test_none_job_id_returns_none(
        self, engine, job_repo
    ):
        """``None`` ``job_id`` returns ``None`` silently — the caller
        passes ``completed_task.work_id`` which can legitimately be
        ``None`` in a test double that bypasses the linkage contract.
        The method must not raise on ``None``."""
        assert job_repo.finalize_mirror_job_at_completion(None) is None


class TestFixBMirrorTransitionScopeDiscipline:
    """TASK (mission) JobItems are NOT inline-transitioned here."""

    def test_task_job_is_left_untouched(
        self, engine, job_repo
    ):
        """``job_type='task'`` (mission) JobItems keep their bus-gated
        finalize — the inline transition is structurally wrong for
        missions because it bypasses the wait-for-children contract.

        Scope discipline is the spec's hard rule (Part 1, §4).
        """
        iid = _seed_instance(engine)
        job = _seed_task_job(engine, instance_id=iid)

        result = job_repo.finalize_mirror_job_at_completion(job.job_id)

        # Scope discipline: the method returns None for non-message
        # jobs and does NOT touch the DB.
        assert result is None, (
            "Scope discipline: finalize_mirror_job_at_completion must "
            "leave TASK (mission) JobItems to the bus-gated finalize path"
        )

        row = _read(engine, job.job_id)
        assert row is not None
        assert row.admission_state == AdmissionState.ACTIVE.value, (
            "TASK job's admission_state must remain ACTIVE — the inline "
            "transition is structurally wrong for missions (would bypass "
            "wait-for-children)"
        )
        assert row.terminal_reason is None, (
            "TASK job must NOT receive the inline terminal_reason stamp"
        )


class TestFixBMirrorTransitionValidatesTransition:
    """The transition MUST go through ``validate_transition`` — the
    8 legacy writers bypass it; this new writer does NOT."""

    def test_validate_transition_called_for_active(
        self, engine, job_repo, monkeypatch
    ):
        """``job_state_machine.validate_transition`` MUST be called
        with ``('active', 'done')`` for an ACTIVE message JobItem —
        proof the new writer goes through the legal-transition
        machinery (the example, not the bypass class).

        The repo method does a *lazy* import inside its body, so the
        singleton ``daemon.services.job_state_machine.job_state_machine``
        is what runs. We patch that singleton's ``validate_transition``
        method and assert it was invoked with the canonical
        ``(active, done, job_id)`` shape.
        """
        iid = _seed_instance(engine)
        job = _seed_message_job(engine, instance_id=iid)

        from daemon.services import job_state_machine as sm_module

        calls: list[tuple[str | None, str, str]] = []
        original_validate = sm_module.job_state_machine.validate_transition

        def spy_validate(from_state, to_state, job_id=""):
            calls.append((from_state, to_state, job_id))
            return original_validate(from_state, to_state, job_id=job_id)

        monkeypatch.setattr(
            sm_module.job_state_machine,
            "validate_transition",
            spy_validate,
        )

        job_repo.finalize_mirror_job_at_completion(job.job_id)

        assert any(
            from_state == AdmissionState.ACTIVE.value
            and to_state == AdmissionState.DONE.value
            and job_id_arg == job.job_id
            for from_state, to_state, job_id_arg in calls
        ), (
            f"validate_transition(ACTIVE, DONE, job_id) must run before "
            f"the SQL guard. Saw calls: {calls}"
        )

    def test_validate_transition_called_for_queued(
        self, engine, job_repo, monkeypatch
    ):
        """``(queued, done)`` is also a legal transition (Fix B's
        normal path when the Task completes before dispatch promote).
        Verify the pre-check fires for QUEUED message jobs too."""
        iid = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=iid,
            admission_state=AdmissionState.QUEUED.value,
        )

        from daemon.services import job_state_machine as sm_module

        calls: list[tuple[str | None, str, str]] = []
        original_validate = sm_module.job_state_machine.validate_transition

        def spy_validate(from_state, to_state, job_id=""):
            calls.append((from_state, to_state, job_id))
            return original_validate(from_state, to_state, job_id=job_id)

        monkeypatch.setattr(
            sm_module.job_state_machine,
            "validate_transition",
            spy_validate,
        )

        job_repo.finalize_mirror_job_at_completion(job.job_id)

        assert any(
            from_state == AdmissionState.QUEUED.value
            and to_state == AdmissionState.DONE.value
            and job_id_arg == job.job_id
            for from_state, to_state, job_id_arg in calls
        ), (
            f"validate_transition(QUEUED, DONE, job_id) must run for "
            f"QUEUED message jobs. Saw calls: {calls}"
        )

    def test_illegal_transition_raises_and_blocks_write(
        self, engine, job_repo, monkeypatch
    ):
        """If ``validate_transition`` raises (programming error — a
        future ``admission_state`` value not in ``VALID_TRANSITIONS``),
        the writer MUST propagate the raise so the SQL UPDATE never
        fires. This proves the pre-check is wired in front of the SQL
        guard.

        Production never sees this path — the writer only accepts
        ``active`` / ``queued`` and both are legal transitions — but
        the fail-fast property is part of the contract.
        """
        iid = _seed_instance(engine)
        job = _seed_message_job(engine, instance_id=iid)

        from daemon.services import job_state_machine as sm_module

        def rejecting_validate(from_state, to_state, job_id=""):
            raise InvalidTransitionError(
                job_id=job_id,
                from_state=from_state,
                to_state=to_state,
            )

        monkeypatch.setattr(
            sm_module.job_state_machine,
            "validate_transition",
            staticmethod(rejecting_validate),
        )

        with pytest.raises(InvalidTransitionError):
            job_repo.finalize_mirror_job_at_completion(job.job_id)

        # Strong form: the SQL UPDATE never fired — the row stays
        # ACTIVE.
        row = _read(engine, job.job_id)
        assert row is not None
        assert row.admission_state == AdmissionState.ACTIVE.value, (
            "validate_transition failure MUST block the SQL UPDATE — "
            "the row must remain ACTIVE"
        )


class TestFixBMirrorTransitionConcurrentRaceSafety:
    """Concurrent double-fire: exactly one transition wins.

    Uses the file-backed engine from ``tests/job_queue/conftest.py``
    so each thread sees its own connection — StaticPool's single
    shared connection serializes cursor access and the cross-thread
    ``rowcount == 1`` race never fires. Same recipe as the existing
    concurrent writers in ``tests/job_queue/``.
    """

    def test_double_fire_is_one_transition(
        self, tmp_path
    ):
        """Two threads fire the inline transition simultaneously;
        exactly ONE wins (``rowcount == 1``) and the other sees
        ``rowcount == 0`` (silent no-op).

        Uses ``concurrent_engine`` from ``tests/job_queue/conftest.py``
        (file-backed SQLite, default QueuePool) — StaticPool hides the
        race because its single shared connection serializes cursor
        access. The harness here replicates the file-backed recipe
        directly so this test file stays runnable without the
        conftest.
        """
        from sqlalchemy.pool import QueuePool

        eng = create_engine(
            f"sqlite:///{tmp_path}/fix_b_mirror_concurrent.db",
            connect_args={"check_same_thread": False},
            poolclass=QueuePool,
        )
        SQLModel.metadata.create_all(eng)

        try:
            # Single seed — one message JobItem + one instance.
            iid = _seed_instance(eng)
            job = _seed_message_job(engine=eng, instance_id=iid)
            jid = job.job_id

            results: list[JobItem | None] = [None, None]
            errors: list[BaseException] = []

            def fire(idx: int) -> None:
                try:
                    repo = JobRepository(eng)
                    results[idx] = repo.finalize_mirror_job_at_completion(jid)
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            # Two concurrent fires — each thread has its own
            # connection (QueuePool default), so the SQL ``IN``
            # predicate sees real cross-connection visibility.
            t1 = threading.Thread(target=fire, args=(0,))
            t2 = threading.Thread(target=fire, args=(1,))
            t1.start()
            t2.start()
            t1.join(timeout=10.0)
            t2.join(timeout=10.0)

            assert not errors, f"Concurrent fires raised: {errors}"
            # Exactly one winner — the other is the silent no-op.
            winners = [
                r for r in results
                if r is not None
                and r.admission_state == AdmissionState.DONE.value
            ]
            noops = [r for r in results if r is None]
            assert len(winners) == 1, (
                f"Exactly one thread must win the SQL guard and "
                f"transition to done; saw {len(winners)} winners. "
                f"results={results}"
            )
            assert len(noops) == 1, (
                f"Exactly one thread must lose (silent None); "
                f"saw {len(noops)} no-ops. results={results}"
            )

            # Final row state — exactly one done stamp.
            row = _read(eng, jid)
            assert row is not None
            assert row.admission_state == AdmissionState.DONE.value
            assert row.terminal_reason == "completed"
        finally:
            eng.dispose()
