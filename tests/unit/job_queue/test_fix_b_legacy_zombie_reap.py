"""Unit tests for Fix B legacy zombie reap (leader decision).

Covers ``JobRepository.reap_legacy_mirror_zombies`` — the
one-time reconciliation method that retires the 3 pre-Fix-B legacy
zombie ACTIVE message JobItems whose instances are dead/gone and
whose driving Tasks are absent or terminal.

The dispatch is by the leader decision (2026-09-02): reap the 3
legacy zombies IN THIS BRANCH so Fix B's claim of "retires the
zombie class" is real, not aspirational. The method is
**D2-exempt** — legacy one-time reconciliation with a fixed
cutover bound (``LEGACY_MIRROR_ZOMBIE_CUTOVER_ISO``), NOT a
load-bearing correctness sweep. It self-extinguishes once no
rows match.

Contract (every dimension is a required predicate AND a required
test):

  * ``job_type == 'message'`` — wrong-type rows NOT reaped.
  * ``admission_state == 'active'`` — non-active rows NOT reaped.
  * ``created_at < LEGACY_MIRROR_ZOMBIE_CUTOVER_ISO`` — post-cutover
    rows NOT reaped (forward rows covered by the inline writer).
  * Linked ``instance`` is ``None`` OR ``status`` in
    ``TERMINAL_INSTANCE_STATUSES`` — live-instance rows NOT reaped.
  * Linked ``task`` is ``None`` OR ``status`` in terminal-task set
    (``COMPLETED`` / ``FAILED`` / ``CANCELLED``) — live-task rows
    NOT reaped.

When ALL dimensions match, the row is reaped: ``admission_state``
``'active' → 'done'`` with ``terminal_reason='orphan_retired'``.
Goes through ``job_state_machine.validate_transition`` BEFORE
the SQL guard (the example, not the bypass class). Audit INFO
log per reaped row.

Same test isolation discipline as
``tests/unit/job_queue/test_fix_b_inline_mirror_transition.py``:
this file uses the in-memory SQLite StaticPool for its sequential tests;
the concurrent-race recipe is kept file-backed and documented in the
inline transition suite.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Register every model on ``SQLModel.metadata`` BEFORE ``create_all``.
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.constants import (
    ALIVE_INSTANCE_STATUSES,
    TERMINAL_INSTANCE_STATUSES,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import JobRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import (
    LEGACY_MIRROR_ZOMBIE_CUTOVER_ISO,
)
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_state_machine import (
    InvalidTransitionError,
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """Real in-memory SQLite engine (StaticPool)."""
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
    return JobRepository(engine)


@pytest.fixture
def task_repo(engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def instance_repo(engine):
    """Plain ``SQLModelInstanceRepository`` handle — the method only
    needs ``.get(instance_id) -> Instance | None`` so any repo with
    that surface works."""
    from daemon.repositories.instance.repository import (
        SQLModelInstanceRepository,
    )
    return SQLModelInstanceRepository(engine=engine)


def _iso(dt: datetime) -> str:
    """ISO-8601 with offset matching the model's ``created_at`` column."""
    return dt.isoformat()


def _seed_message_job(
    engine,
    *,
    job_id: str | None = None,
    instance_id: str | None = None,
    created_at: datetime,
    admission_state: str = AdmissionState.ACTIVE.value,
    project_id: str = "test-project",
) -> JobItem:
    """Seed a message JobItem with explicit ``created_at``."""
    jid = job_id or f"job-{uuid.uuid4().hex[:8]}"
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        item = JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="/tmp/agent",
            message="zombie-reap-test",
            source="api",
            job_type="message",
            admission_state=admission_state,
            instance_id=iid,
            project_id=project_id,
            created_at=_iso(created_at),
            job_metadata={},
            max_retries=0,
        )
        s.add(item)
        s.commit()
        s.refresh(item)
    return item


def _seed_task_job(
    engine,
    *,
    created_at: datetime,
    admission_state: str = AdmissionState.ACTIVE.value,
) -> JobItem:
    """Seed a TASK (mission) JobItem — the wrong-type dimension test."""
    jid = f"job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        item = JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="/tmp/agent",
            message="wrong-type-test",
            source="api",
            job_type="task",  # NOT 'message'
            admission_state=admission_state,
            instance_id=f"inst-{uuid.uuid4().hex[:8]}",
            project_id="test-project",
            created_at=_iso(created_at),
            job_metadata={},
            max_retries=0,
        )
        s.add(item)
        s.commit()
        s.refresh(item)
    return item


def _seed_instance(
    engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    created_at: datetime | None = None,
) -> str:
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    created = created_at or datetime.now(timezone.utc)
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="developer",
                agent_dir="/tmp/agent",
                status=status,
                version=1,
                instance_metadata={},
                created_at=created,
            )
        )
        s.commit()
    return iid


def _seed_task(
    engine,
    *,
    work_id: str,
    instance_id: str | None = None,
    status: str = TaskStatus.RUNNING.value,
) -> Task:
    with Session(engine) as s:
        item = Task(
            work_id=work_id,
            instance_id=instance_id or f"inst-{uuid.uuid4().hex[:8]}",
            task_type="process_message",
            status=status,
        )
        s.add(item)
        s.commit()
        s.refresh(item)
    return item


def _read_job(engine, job_id: str) -> JobItem | None:
    with Session(engine) as s:
        return s.get(JobItem, job_id)


# A pre-cutover datetime — comfortably before the bound.
_PRE_CUTOVER = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)
# A post-cutover datetime — comfortably after the bound.
_POST_CUTOVER = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────


class TestReapLegacyMirrorZombiesHappyPath:
    """The 3 pre-cutover rows that match the predicate are reaped."""

    def test_reaps_legacy_message_zombie_with_absent_instance(
        self, engine, job_repo, task_repo, instance_repo
    ):
        """The legacy zombie shape: ACTIVE message JobItem whose
        linked instance is absent (no row). The reap reaps it;
        terminal_reason='orphan_retired'; idempotent on second run."""
        job = _seed_message_job(
            engine, created_at=_PRE_CUTOVER,
        )
        # NO instance row — the zombie signature.

        # First run: reaped.
        reaped = job_repo.reap_legacy_mirror_zombies(
            task_repository=task_repo,
            instance_repository=instance_repo,
        )
        assert len(reaped) == 1
        assert reaped[0].job_id == job.job_id
        assert reaped[0].admission_state == AdmissionState.DONE.value
        assert reaped[0].terminal_reason == "orphan_retired", (
            "terminal_reason='orphan_retired' is the audit-truthful "
            "stamp — these rows did NOT complete organically"
        )

        # Strong form: re-read via fresh session.
        post = _read_job(engine, job.job_id)
        assert post is not None
        assert post.admission_state == AdmissionState.DONE.value
        assert post.terminal_reason == "orphan_retired"

        # Second run: idempotent — same predicate, no rows match
        # (the row is no longer 'active').
        reaped_again = job_repo.reap_legacy_mirror_zombies(
            task_repository=task_repo,
            instance_repository=instance_repo,
        )
        assert reaped_again == [], (
            f"Idempotency: second run must be a silent no-op. "
            f"Got {len(reaped_again)} reaped: {reaped_again}"
        )

    def test_reaps_legacy_zombie_with_terminal_instance(
        self, engine, job_repo, task_repo, instance_repo
    ):
        """Instance exists but is in a TERMINAL state — still a
        legacy zombie. The reap matches this dimension."""
        instance_id = _seed_instance(
            engine,
            status=InstanceStatus.COMPLETED.value,
        )
        job = _seed_message_job(
            engine,
            instance_id=instance_id,
            created_at=_PRE_CUTOVER,
        )

        reaped = job_repo.reap_legacy_mirror_zombies(
            task_repository=task_repo,
            instance_repository=instance_repo,
        )
        assert len(reaped) == 1
        assert reaped[0].job_id == job.job_id
        assert reaped[0].terminal_reason == "orphan_retired"

    def test_reaps_legacy_zombie_with_terminal_task(
        self, engine, job_repo, task_repo, instance_repo
    ):
        """Linked Task is in a terminal status (COMPLETED) — the
        reap matches this dimension even though the instance is
        absent."""
        job = _seed_message_job(
            engine, created_at=_PRE_CUTOVER,
        )
        # No instance, but a COMPLETED Task for this work_id.
        _seed_task(
            engine, work_id=job.job_id,
            status=TaskStatus.COMPLETED.value,
        )

        reaped = job_repo.reap_legacy_mirror_zombies(
            task_repository=task_repo,
            instance_repository=instance_repo,
        )
        assert len(reaped) == 1
        assert reaped[0].terminal_reason == "orphan_retired"

    def test_reaps_multiple_legacy_zombies_in_one_call(
        self, engine, job_repo, task_repo, instance_repo
    ):
        """The 3 legacy zombies together: all match the predicate;
        all are reaped in one call. Same shape as the real prod
        set (08-01 × 1; 08-14 × 2)."""
        jobs = [
            _seed_message_job(
                engine, created_at=_PRE_CUTOVER - timedelta(days=i),
            )
            for i in range(3)
        ]

        reaped = job_repo.reap_legacy_mirror_zombies(
            task_repository=task_repo,
            instance_repository=instance_repo,
        )
        assert len(reaped) == 3
        reaped_ids = {r.job_id for r in reaped}
        assert reaped_ids == {j.job_id for j in jobs}
        for r in reaped:
            assert r.terminal_reason == "orphan_retired"


class TestReapLegacyMirrorZombiesPredicateExclusion:
    """The predicate dimensions that prevent the reap from firing."""

    def test_wrong_job_type_not_reaped(
        self, engine, job_repo, task_repo, instance_repo
    ):
        """TASK (mission) JobItems are NOT reaped — the predicate's
        first dimension (``job_type='message'``) excludes them."""
        job = _seed_task_job(
            engine, created_at=_PRE_CUTOVER,
        )
        # NO instance + NO task — every other dimension would
        # match. The wrong job_type is the single dimension that
        # must protect this row.
        reaped = job_repo.reap_legacy_mirror_zombies(
            task_repository=task_repo,
            instance_repository=instance_repo,
        )
        assert reaped == [], (
            f"TASK JobItem must NOT be reaped (job_type != 'message'). "
            f"Got: {reaped}"
        )
        post = _read_job(engine, job.job_id)
        assert post is not None
        assert post.admission_state == AdmissionState.ACTIVE.value
        assert post.terminal_reason is None

    def test_non_active_admission_not_reaped(
        self, engine, job_repo, task_repo, instance_repo
    ):
        """``admission_state != 'active'`` (e.g. QUEUED) — the
        second predicate dimension — excludes the row even if every
        other dimension matches. The reap is targeted at the
        active-zombie class only."""
        job = _seed_message_job(
            engine,
            created_at=_PRE_CUTOVER,
            admission_state=AdmissionState.QUEUED.value,
        )
        reaped = job_repo.reap_legacy_mirror_zombies(
            task_repository=task_repo,
            instance_repository=instance_repo,
        )
        assert reaped == []
        post = _read_job(engine, job.job_id)
        assert post is not None
        assert post.admission_state == AdmissionState.QUEUED.value
        assert post.terminal_reason is None

    def test_post_cutover_not_reaped(
        self, engine, job_repo, task_repo, instance_repo
    ):
        """``created_at >= CUTOVER`` — the cutover bound — excludes
        forward rows. Forward rows are the inline writer's
        responsibility; the legacy reap MUST NOT touch them.
        """
        job = _seed_message_job(
            engine,
            created_at=_POST_CUTOVER,
        )
        reaped = job_repo.reap_legacy_mirror_zombies(
            task_repository=task_repo,
            instance_repository=instance_repo,
        )
        assert reaped == [], (
            f"Post-cutover row must NOT be reaped. Got: {reaped}"
        )
        post = _read_job(engine, job.job_id)
        assert post is not None
        assert post.admission_state == AdmissionState.ACTIVE.value
        assert post.terminal_reason is None

    def test_live_instance_not_reaped(
        self, engine, job_repo, task_repo, instance_repo
    ):
        """``instance.status`` in ALIVE_INSTANCE_STATUSES (i.e. NOT
        in TERMINAL_INSTANCE_STATUSES) — the instance dimension
        excludes the row. A pre-cutover message JobItem with a
        running instance is NOT a zombie — the parent may still
        be doing live work.
        """
        instance_id = _seed_instance(
            engine, status=InstanceStatus.RUNNING.value,
        )
        job = _seed_message_job(
            engine,
            instance_id=instance_id,
            created_at=_PRE_CUTOVER,
        )

        reaped = job_repo.reap_legacy_mirror_zombies(
            task_repository=task_repo,
            instance_repository=instance_repo,
        )
        assert reaped == [], (
            f"Live instance must protect the row from the reap. "
            f"Got: {reaped}"
        )
        post = _read_job(engine, job.job_id)
        assert post is not None
        assert post.admission_state == AdmissionState.ACTIVE.value

    @pytest.mark.parametrize(
        "alive_status",
        sorted(ALIVE_INSTANCE_STATUSES),
    )
    def test_live_instance_statuses_all_protect(
        self, engine, job_repo, task_repo, instance_repo, alive_status
    ):
        """Comprehensive live-instance coverage — every value in the
        canonical ``ALIVE_INSTANCE_STATUSES`` set protects the row
        from the reap.
        """
        instance_id = _seed_instance(
            engine, status=alive_status,
        )
        job = _seed_message_job(
            engine,
            instance_id=instance_id,
            created_at=_PRE_CUTOVER,
        )

        reaped = job_repo.reap_legacy_mirror_zombies(
            task_repository=task_repo,
            instance_repository=instance_repo,
        )
        assert reaped == [], (
            f"Status={alive_status!r} must protect the row from "
            f"the reap (it's an ALIVE status, not a TERMINAL one). "
            f"Got reaped: {reaped}"
        )

    def test_live_task_not_reaped(
        self, engine, job_repo, task_repo, instance_repo
    ):
        """Linked Task in PENDING / RUNNING — the task dimension
        excludes the row. A pre-cutover message JobItem with a
        still-pending Task is NOT a zombie; the inline writer
        will get it at T0 when the Task completes.
        """
        job = _seed_message_job(
            engine, created_at=_PRE_CUTOVER,
        )
        # No instance (zombie dim 4) but a RUNNING Task.
        _seed_task(
            engine, work_id=job.job_id,
            status=TaskStatus.RUNNING.value,
        )

        reaped = job_repo.reap_legacy_mirror_zombies(
            task_repository=task_repo,
            instance_repository=instance_repo,
        )
        assert reaped == [], (
            f"Live task must protect the row from the reap "
            f"(the inline writer owns T0 finalization). "
            f"Got reaped: {reaped}"
        )

    @pytest.mark.parametrize(
        "alive_task_status",
        [TaskStatus.PENDING.value, TaskStatus.RUNNING.value],
    )
    def test_alive_task_statuses_protect(
        self, engine, job_repo, task_repo, instance_repo, alive_task_status
    ):
        """Same as above, parametric over both alive task statuses.
        """
        job = _seed_message_job(
            engine, created_at=_PRE_CUTOVER,
        )
        _seed_task(
            engine, work_id=job.job_id,
            status=alive_task_status,
        )

        reaped = job_repo.reap_legacy_mirror_zombies(
            task_repository=task_repo,
            instance_repository=instance_repo,
        )
        assert reaped == [], (
            f"Task status={alive_task_status!r} must protect the "
            f"row. Got reaped: {reaped}"
        )

    def test_terminal_instance_statuses_all_match(
        self, engine, job_repo, task_repo, instance_repo
    ):
        """Coverage for the TERMINAL_INSTANCE_STATUSES set — every
        terminal status (except 'absent', which is its own case) is
        a valid reap trigger."""
        for status in sorted(TERMINAL_INSTANCE_STATUSES):
            instance_id = _seed_instance(engine, status=status)
            job = _seed_message_job(
                engine,
                instance_id=instance_id,
                created_at=_PRE_CUTOVER,
            )

            reaped = job_repo.reap_legacy_mirror_zombies(
                task_repository=task_repo,
                instance_repository=instance_repo,
            )
            assert len(reaped) == 1, (
                f"Status={status!r} (in TERMINAL_INSTANCE_STATUSES) "
                f"must trigger reap. Got {len(reaped)} reaped."
            )
            assert reaped[0].job_id == job.job_id
            assert reaped[0].terminal_reason == "orphan_retired"

            # Reset for the next parametrize iteration.
            with Session(engine) as s:
                s.delete(_read_job(engine, job.job_id))
                inst = s.get(Instance, instance_id)
                if inst is not None:
                    s.delete(inst)
                s.commit()

    def test_terminal_task_statuses_all_match(
        self, engine, job_repo, task_repo, instance_repo
    ):
        """Coverage for terminal-task statuses (COMPLETED/FAILED/
        CANCELLED) — every one is a valid reap trigger."""
        for status in (
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
        ):
            job = _seed_message_job(
                engine, created_at=_PRE_CUTOVER,
            )
            _seed_task(engine, work_id=job.job_id, status=status)

            reaped = job_repo.reap_legacy_mirror_zombies(
                task_repository=task_repo,
                instance_repository=instance_repo,
            )
            assert len(reaped) == 1, (
                f"Task status={status!r} (terminal) must trigger reap. "
                f"Got {len(reaped)} reaped."
            )
            assert reaped[0].terminal_reason == "orphan_retired"

            # Reset for the next parametrize iteration.
            with Session(engine) as s:
                s.delete(_read_job(engine, job.job_id))
                from sqlmodel import select as sqlmodel_select
                tasks = s.exec(
                    sqlmodel_select(Task).where(Task.work_id == job.job_id)
                ).all()
                for t in tasks:
                    s.delete(t)
                s.commit()


class TestReapLegacyMirrorZombiesValidateTransitionPath:
    """The reap goes through ``validate_transition`` BEFORE the SQL
    guard — same assertion style as the inline writer's
    ``test_illegal_transition_raises_and_blocks_write``."""

    def test_validate_transition_called_for_active(
        self, engine, job_repo, task_repo, instance_repo, monkeypatch
    ):
        """``job_state_machine.validate_transition`` MUST be called
        with ``('active', 'done')`` for a reaped row — proof the
        new writer goes through the legal-transition machinery.
        """
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

        _seed_message_job(engine, created_at=_PRE_CUTOVER)
        job_repo.reap_legacy_mirror_zombies(
            task_repository=task_repo,
            instance_repository=instance_repo,
        )

        assert any(
            from_state == AdmissionState.ACTIVE.value
            and to_state == AdmissionState.DONE.value
            for from_state, to_state, _job_id in calls
        ), (
            f"validate_transition(ACTIVE, DONE) must run before "
            f"the SQL guard. Saw calls: {calls}"
        )

    def test_illegal_transition_raises_and_blocks_write(
        self, engine, job_repo, task_repo, instance_repo, monkeypatch
    ):
        """If ``validate_transition`` raises (a programming error
        that shouldn't fire in production — ``(active, done)`` is a
        legal transition), the writer MUST propagate the raise
        AND block the SQL UPDATE. The row stays ACTIVE.

        Same assertion shape as the inline writer's
        ``test_illegal_transition_raises_and_blocks_write``.
        """
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

        job = _seed_message_job(engine, created_at=_PRE_CUTOVER)

        with pytest.raises(InvalidTransitionError):
            job_repo.reap_legacy_mirror_zombies(
                task_repository=task_repo,
                instance_repository=instance_repo,
            )

        # Strong form: the SQL UPDATE never fired — row stays ACTIVE.
        post = _read_job(engine, job.job_id)
        assert post is not None
        assert post.admission_state == AdmissionState.ACTIVE.value, (
            "validate_transition failure MUST block the SQL UPDATE — "
            "the row must remain ACTIVE"
        )
        assert post.terminal_reason is None


class TestReapLegacyMirrorZombiesFailureContainment:
    """Transient failures are logged and contained per cycle."""

    def test_candidate_scan_failure_is_swallowed(
        self, engine, job_repo, task_repo, instance_repo, monkeypatch, caplog
    ):
        """A candidate-query failure returns ``[]``; it never escapes
        into the periodic recovery service."""
        import daemon.repositories.job_queue.repository as repository_module

        class FailingSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def exec(self, statement):
                raise RuntimeError("candidate scan boom")

        monkeypatch.setattr(
            repository_module,
            "SQLModelSession",
            lambda unused_engine: FailingSession(),
        )

        with caplog.at_level(
            "ERROR", logger="daemon.repositories.job_queue.repository"
        ):
            reaped = job_repo.reap_legacy_mirror_zombies(
                task_repository=task_repo,
                instance_repository=instance_repo,
            )

        assert reaped == []
        assert "candidate scan" in caplog.text
        assert any(record.exc_info for record in caplog.records)

    def test_instance_lookup_failure_skips_only_bad_row(
        self, engine, job_repo, task_repo, instance_repo, monkeypatch, caplog
    ):
        """One instance lookup failure is logged; the next row is still
        reaped and the failed row remains ACTIVE for the next cycle."""
        older_job = _seed_message_job(
            engine,
            created_at=datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc),
        )
        failing_instance_id = older_job.instance_id
        newer_job = _seed_message_job(
            engine,
            created_at=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
        )
        _seed_task(
            engine, work_id=newer_job.job_id,
            status=TaskStatus.COMPLETED.value,
        )

        original_get = instance_repo.get

        def get_or_raise(instance_id):
            if instance_id == failing_instance_id:
                raise RuntimeError("instance lookup boom")
            return original_get(instance_id)

        monkeypatch.setattr(instance_repo, "get", get_or_raise)

        with caplog.at_level(
            "WARNING", logger="daemon.repositories.job_queue.repository"
        ):
            reaped = job_repo.reap_legacy_mirror_zombies(
                task_repository=task_repo,
                instance_repository=instance_repo,
            )

        assert [item.job_id for item in reaped] == [newer_job.job_id]
        assert "instance lookup failed" in caplog.text
        assert "task_id=None" in caplog.text
        assert _read_job(engine, older_job.job_id).admission_state == "active"
        assert _read_job(engine, newer_job.job_id).admission_state == "done"

    def test_task_lookup_failure_skips_only_bad_row(
        self, engine, job_repo, task_repo, instance_repo, monkeypatch, caplog
    ):
        """One Task lookup failure is logged; a later terminal Task is
        still reaped and the failed row remains ACTIVE."""
        failing_job = _seed_message_job(
            engine,
            created_at=datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc),
        )
        terminal_job = _seed_message_job(
            engine,
            created_at=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
        )
        _seed_task(
            engine, work_id=terminal_job.job_id,
            status=TaskStatus.FAILED.value,
        )
        original_get_by_work_id = task_repo.get_by_work_id

        def get_by_work_id_or_raise(work_id):
            if work_id == failing_job.job_id:
                raise RuntimeError("task lookup boom")
            return original_get_by_work_id(work_id)

        monkeypatch.setattr(
            task_repo, "get_by_work_id", get_by_work_id_or_raise
        )

        with caplog.at_level(
            "WARNING", logger="daemon.repositories.job_queue.repository"
        ):
            reaped = job_repo.reap_legacy_mirror_zombies(
                task_repository=task_repo,
                instance_repository=instance_repo,
            )

        assert [item.job_id for item in reaped] == [terminal_job.job_id]
        assert "task lookup failed" in caplog.text
        assert "task_id=None" in caplog.text
        assert any(
            record.getMessage().find(f"{failing_job.job_id[:8]}") >= 0
            for record in caplog.records
        )
        assert _read_job(engine, failing_job.job_id).admission_state == "active"
        assert _read_job(engine, terminal_job.job_id).admission_state == "done"
    """Forward rows (post-cutover) never match — the reap self-
    extinguishes on the cycle after the 3 rows are gone."""

    def test_no_candidates_returns_empty(
        self, engine, job_repo, task_repo, instance_repo
    ):
        """Empty active-job set: ``[]`` returned (no-op). The
        self-extinguishing shape — most cycles land here after
        the 3 rows are reaped."""
        # No seeded rows at all.
        reaped = job_repo.reap_legacy_mirror_zombies(
            task_repository=task_repo,
            instance_repository=instance_repo,
        )
        assert reaped == []

    def test_only_forward_rows_returns_empty(
        self, engine, job_repo, task_repo, instance_repo
    ):
        """A pre-cutover harvest is COMPLETE; only post-cutover
        ACTIVE message rows exist; reap returns ``[]``. The
        natural no-op shape after cleanup."""
        # Forward row (post-cutover) — should NOT match.
        _seed_message_job(
            engine, created_at=_POST_CUTOVER,
        )
        # Another forward row.
        _seed_message_job(
            engine, created_at=_POST_CUTOVER + timedelta(hours=1),
        )
        reaped = job_repo.reap_legacy_mirror_zombies(
            task_repository=task_repo,
            instance_repository=instance_repo,
        )
        assert reaped == []


class TestReapLegacyMirrorZombiesArgumentValidation:
    """Argument validation — both repositories are required."""

    def test_task_repository_none_raises(
        self, engine, job_repo
    ):
        """``task_repository=None`` raises ``ValueError`` — the
        task-status dimension is mandatory for the predicate."""
        with pytest.raises(ValueError, match="task_repository"):
            job_repo.reap_legacy_mirror_zombies(
                task_repository=None,
                instance_repository="anything",
            )

    def test_instance_repository_none_raises(
        self, engine, job_repo, task_repo
    ):
        """``instance_repository=None`` raises ``ValueError`` — the
        instance-status dimension is mandatory for the predicate."""
        with pytest.raises(ValueError, match="instance_repository"):
            job_repo.reap_legacy_mirror_zombies(
                task_repository=task_repo,
                instance_repository=None,
            )


class TestReapLegacyMirrorZombiesCutoverConstant:
    """The cutover constant is pinned and exposed."""

    def test_cutover_constant_value(self):
        """The cutover constant is the documented ISO timestamp."""
        assert LEGACY_MIRROR_ZOMBIE_CUTOVER_ISO == "2026-09-02T00:00:00+00:00", (
            f"Cutover constant has drifted from the documented "
            f"value. Got: {LEGACY_MIRROR_ZOMBIE_CUTOVER_ISO!r}. "
            f"Document the choice in docs/job-task-system.md §8.1."
        )
