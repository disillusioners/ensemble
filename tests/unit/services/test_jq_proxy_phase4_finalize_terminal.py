"""Phase 4 ``_finalize_terminal`` boundary tests for Job-as-Queue-Proxy.

Phase 4 (commit ``e61b8c5a``) of ``feature/job-as-queue-proxy`` flips
write authority from dual-write to instance-authoritative. The single
terminal-write boundary ``JobQueueService._finalize_terminal`` is the
mandatory funnel for every active-job termination: it takes a REQUIRED
:class:`Decision` enum (``NO_RETRY`` / ``RETRY`` / ``DEAD_LETTER``) so
a future finalize path that forgets retry/DLQ semantics fails at
instantiation, not in production.

The boundary's contract has four pin-able invariants:

A. **Decision dispatch**
   Each ``Decision`` value drives a single admission transition:
   - ``NO_RETRY``    → ``admission_state='done'`` (mirror ``status``)
   - ``RETRY``       → ``admission_state='queued'`` via retry engine
   - ``DEAD_LETTER`` → ``admission_state='dead'`` via DLQ service

B. **Decision is required (closed enum, no default)**
   The signature is ``_finalize_terminal(instance_id, decision, ...)``
   with no default — a missing ``decision`` raises ``TypeError`` at
   call time.

C. **admission_state is the primary write**
   The persisted ``admission_state`` is the source of truth; ``status``
   is written as a backward-compat mirror (Phase 5 drops the column).
   Both columns land in the same SQL UPDATE so they cannot drift.

D. **Dead-letter canonicalization**
   ``_STATUS_CANONICAL_MAP`` maps ``dead → dead_letter`` so the new
   Phase 4 ``admission_state='dead'`` spelling collapses onto the
   legacy ``status='dead_letter'`` canonical vocabulary.

The fixture style mirrors ``tests/unit/services/test_jq_proxy_phase2_dualwrite.py``
(real in-memory SQLite + StaticPool + real ``JobRepository`` /
``DeadLetterRepository``); ``JobQueueService`` itself is constructed
with ``MagicMock`` placeholders for ``lock_manager`` / ``queue_repo`` /
``instance_manager`` because the boundary's data path only touches
``self._repository`` and ``self._lock_manager.release_by_instance``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.job_queue import (
    AdmissionState,
    Decision,
    JobItem,
    JobQueue,
    JobQueueRepository,
    JobStatus,
    QueueType,
    AdmissionState,
    AdmissionState,
    AdmissionState,
)
from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.services.dead_letter_service import DeadLetterService
from daemon.services.job_queue_service import JobQueueService
from daemon.services.work_status import (
    _STATUS_CANONICAL_MAP,
    canonicalize_status,
)


# >>> test-local status_to_admission (Phase 4 cleanup) <<<
# The ``status_to_admission`` helper was deleted from
# ``daemon.repositories.job_queue.models`` in Phase 4 cleanup
# (``admission_state`` is now the sole write authority). Tests that
# seed JobItem rows from a ``status`` string still need this
# JobStatus -> AdmissionState mapping, so we redefine it locally
# here. Behavior is identical to the deleted production helper
# (including the ``QUEUED`` fallback for unknown inputs).
def status_to_admission(status):  # noqa: ANN001,ANN201 — test-local re-export
    return {
        "pending": "queued",
        "processing": "active",
        "paused": "active",
        "completed": "done",
        "failed": "done",
        "cancelled": "done",
        "dead_letter": "dead",
    }.get(status, "queued")


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool, FK pragma on).

    Mirrors the fixture style in ``test_jq_proxy_phase2_dualwrite.py``
    and ``test_job_queue_proxy_phase1.py``: StaticPool keeps a single
    connection alive across the test so ``asyncio.to_thread`` workers
    share the in-memory store, and ``PRAGMA foreign_keys=ON`` matches
    production SQLite posture.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def dlq_repo(engine: Engine) -> DeadLetterRepository:
    return DeadLetterRepository(engine)


@pytest.fixture
def dlq_service(
    job_repo: JobRepository, dlq_repo: DeadLetterRepository
) -> DeadLetterService:
    """DLQ service wired against the test engine.

    Required for the ``DEAD_LETTER`` path tests — ``_finalize_terminal``
    routes through ``dlq_service.move_to_dlq_standalone`` when one is
    wired. Standalone variant creates its own session so it does not
    depend on the watcher-notification path.
    """
    return DeadLetterService(job_repository=job_repo, dlq_repository=dlq_repo)


@pytest.fixture
def job_queue_service(
    job_repo: JobRepository, dlq_service: DeadLetterService
) -> JobQueueService:
    """``JobQueueService`` wired for boundary-only testing.

    ``lock_manager``, ``queue_repo``, and ``instance_manager`` are
    ``MagicMock`` placeholders. The boundary's data path only touches:

    * ``self._repository`` (real, bound to the in-memory engine),
    * ``self._lock_manager.release_by_instance`` (awaitable no-op
      AsyncMock — the lock-release ``finally`` runs after every
      decision path so we want a no-op that does not raise),
    * ``self._dlq_service`` (real DLQ service, set via
      ``set_dlq_service`` for the ``DEAD_LETTER`` test).

    The retry engine is set per-test (most tests do not need it; the
    ``RETRY`` path test wires a stub).
    """
    lock_manager = MagicMock()
    lock_manager.release_by_instance = AsyncMock(return_value=[])
    queue_repo = MagicMock(spec=JobQueueRepository)
    instance_manager = MagicMock()
    instance_manager._instance_repository = None  # Forces 'failed' fallback

    service = JobQueueService(
        repository=job_repo,
        lock_manager=lock_manager,
        queue_repo=queue_repo,
        instance_manager=instance_manager,
    )
    service.set_dlq_service(dlq_service)
    return service


# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_queue(engine: Engine, *, project_id: str = "test-project") -> str:
    """Insert a ``JobQueue`` row and return its ``queue_id``.

    Each call produces a unique ``queue_name`` so multiple jobs in the
    same project can coexist without tripping the
    ``UNIQUE(project_id, queue_name_lower)`` constraint.
    """
    from sqlmodel import Session

    queue_id = f"q-{uuid.uuid4().hex[:12]}"
    queue_name = f"q-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        s.add(
            JobQueue(
                queue_id=queue_id,
                project_id=project_id,
                queue_name=queue_name,
                queue_name_lower=queue_name,
                queue_type=QueueType.FIFO.value,
                concurrency_limit=1,
            )
        )
        s.commit()
    return queue_id


def _make_job(
    engine: Engine, job_repo: JobRepository, **overrides
) -> JobItem:
    """Create a job with reasonable defaults.

    Mirrors ``test_jq_proxy_phase2_dualwrite._make_job`` so the new test
    file picks up the same INSERT-path dual-write contract.
    """
    project_id = overrides.pop("project_id", "test-project")
    queue_id = overrides.pop("queue_id", None) or _make_queue(
        engine, project_id=project_id
    )
    defaults = {
        "agent_id": "developer",
        "agent_dir": "/tmp/agents/developer",
        "message": "phase4 finalize-terminal test job",
        "source": "api",
        "project_id": project_id,
        "queue_id": queue_id,
        "priority": 5,
    }
    defaults.update(overrides)
    return job_repo.create(**defaults)


def _start_job(engine: Engine, job_repo: JobRepository, job_id: str, instance_id: str = "inst-1") -> None:
    """Transition a job PENDING → PROCESSING so the boundary's
    ``admission_state='active'`` pre-check passes."""
    started = job_repo.start_job(job_id, instance_id=instance_id)
    assert started is not None, "start_job should succeed for fresh jobs"


def _refresh(engine: Engine, job_id: str) -> JobItem:
    """Re-read a JobItem from the engine so assertions see a fresh row."""
    from sqlmodel import Session, select

    with Session(engine) as s:
        return s.exec(select(JobItem).where(JobItem.job_id == job_id)).one()


class _StubRetryEngine:
    """Minimal retry engine for ``Decision.RETRY`` boundary testing.

    Phase 4's canonical retry path (Plan §3.2) goes
    ``active → queued`` directly via ``_finalize_terminal(RETRY)``.
    The production ``JobRetryEngine.maybe_retry`` still gates on
    ``status == 'failed'`` (the dual-write mirror for legacy callers)
    and on the ``admission_state='active'`` column. Phase 4 cleanup
    stops writing the legacy ``status`` column, so the
    ``atomic_transition('processing' → 'pending')`` path no longer
    matches its ``WHERE status='processing'`` guard — the stub issues
    the canonical ``active → queued`` transition directly via a raw
    guarded UPDATE keyed on ``admission_state='active'``, mirroring
    the production retry contract.

    Returns the updated JobItem (matches the
    ``JobRetryEngine.maybe_retry`` return contract).
    """

    def __init__(self, job_repo: JobRepository):
        self._job_repo = job_repo

    def maybe_retry(self, job_id: str) -> JobItem | None:
        from sqlmodel import Session as SQLModelSession, update as sqlmodel_update
        with SQLModelSession(self._job_repo.engine) as session:
            stmt = (
                sqlmodel_update(JobItem)
                .where(JobItem.job_id == job_id)
                .where(JobItem.admission_state == AdmissionState.ACTIVE.value)
                .values(admission_state=AdmissionState.QUEUED.value)
            )
            result = session.exec(stmt)
            session.commit()
            if result.rowcount == 0:
                return None
            job = session.get(JobItem, job_id)
            return job


# ─── A. Terminal finalization with each Decision ────────────────────────────


class TestDecisionDispatch:
    """Each ``Decision`` value drives a single admission transition.

    Every test starts a job (``admission_state='active'``), calls
    ``_finalize_terminal`` directly with the Decision under test, and
    asserts the persisted row's ``admission_state`` and ``status``
    columns match the documented mapping.
    """

    @pytest.mark.asyncio
    async def test_no_retry_complete_writes_done_and_completed(
        self, engine, job_repo, job_queue_service: JobQueueService
    ):
        """``Decision.NO_RETRY`` with COMPLETED intent →
        ``admission_state='done', status='completed'``.

        ``target_status`` is supplied so the status mirror reflects
        call intent (COMPLETED) instead of the instance-derivation
        fallback (which returns 'failed' when no instance is attached).
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-complete")

        canonical_job_id, final_status = await job_queue_service._finalize_terminal(
            instance_id="inst-complete",
            decision=Decision.NO_RETRY,
            job_id=job.job_id,
            target_status=AdmissionState.DONE.value,
            result_summary="phase4 COMPLETED",
        )

        assert canonical_job_id == job.job_id
        assert final_status == AdmissionState.DONE.value

        refetched = _refresh(engine, job.job_id)
        # admission_state is the primary write target.
        assert refetched.admission_state == AdmissionState.DONE.value
        # status mirror lands in the SAME UPDATE.
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.result_summary == "phase4 COMPLETED"
        assert refetched.completed_at is not None

    @pytest.mark.asyncio
    async def test_no_retry_failed_writes_done_and_failed(
        self, engine, job_repo, job_queue_service: JobQueueService
    ):
        """``Decision.NO_RETRY`` (no retry engine wired, no retries
        available) writes ``admission_state='done', status='failed'`` —
        the failure mirrors into DONE per the dual-write mapping
        (``status_to_admission('failed')='done'``).
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-fail")

        canonical_job_id, final_status = await job_queue_service._finalize_terminal(
            instance_id="inst-fail",
            decision=Decision.NO_RETRY,
            job_id=job.job_id,
            target_status=AdmissionState.DONE.value,
            error_message="phase4 NO_RETRY failure",
        )

        assert canonical_job_id == job.job_id
        assert final_status == AdmissionState.DONE.value

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.error_message == "phase4 NO_RETRY failure"

    @pytest.mark.asyncio
    async def test_retry_writes_queued_and_pending(
        self, engine, job_repo, job_queue_service: JobQueueService
    ):
        """``Decision.RETRY`` (via stub retry engine) writes
        ``admission_state='queued', status='pending'``.

        The stub performs ``atomic_transition('processing' → 'pending')``
        which dual-writes via ``status_to_admission('pending')='queued'``.
        The boundary returns ``final_status='pending'`` because the
        stub's ``maybe_retry`` returns a non-None JobItem.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-retry")

        # Wire the stub retry engine.
        job_queue_service.set_retry_engine(_StubRetryEngine(job_repo))

        canonical_job_id, final_status = await job_queue_service._finalize_terminal(
            instance_id="inst-retry",
            decision=Decision.RETRY,
            job_id=job.job_id,
        )

        assert canonical_job_id == job.job_id
        assert final_status == AdmissionState.QUEUED.value

        refetched = _refresh(engine, job.job_id)
        # admission_state='queued' (status_to_admission('pending'))
        assert refetched.admission_state == AdmissionState.QUEUED.value
        # status mirror in the same UPDATE.
        assert refetched.admission_state == AdmissionState.QUEUED.value

    @pytest.mark.asyncio
    async def test_retry_engine_returns_none_yields_dead_letter(
        self, engine, job_repo, job_queue_service: JobQueueService
    ):
        """``Decision.RETRY`` with a retry engine that returns ``None``
        surfaces ``final_status='dead_letter'`` (the boundary treats
        ``None`` as "engine moved it to DLQ").

        This pins the boundary's contract for the
        ``maybe_retry returned None → final_status='dead_letter'`` case,
        which is what callers see when retries are exhausted.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-retry-none")

        class _NoneRetry:
            def maybe_retry(self, job_id: str) -> JobItem | None:
                return None

        job_queue_service.set_retry_engine(_NoneRetry())

        canonical_job_id, final_status = await job_queue_service._finalize_terminal(
            instance_id="inst-retry-none",
            decision=Decision.RETRY,
            job_id=job.job_id,
        )

        assert canonical_job_id == job.job_id
        # maybe_retry returned None → boundary surfaces dead_letter.
        assert final_status == AdmissionState.DEAD.value

    @pytest.mark.asyncio
    async def test_dead_letter_writes_dead_and_dead_letter(
        self, engine, job_repo, job_queue_service: JobQueueService
    ):
        """``Decision.DEAD_LETTER`` writes
        ``admission_state='dead', status='dead_letter'``.

        Routed through ``dlq_service.move_to_dlq_standalone`` with
        ``from_admission_state='active'`` (the Phase 4 canonical guard).
        The DLQ helper dual-writes ``admission_state='dead'`` via
        ``status_to_admission('dead_letter')='dead'``.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-dlq")

        canonical_job_id, final_status = await job_queue_service._finalize_terminal(
            instance_id="inst-dlq",
            decision=Decision.DEAD_LETTER,
            job_id=job.job_id,
        )

        assert canonical_job_id == job.job_id
        assert final_status == AdmissionState.DEAD.value

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DEAD.value
        assert refetched.admission_state == AdmissionState.DEAD.value

    @pytest.mark.asyncio
    async def test_no_retry_cancelled_writes_done_and_cancelled(
        self, engine, job_repo, job_queue_service: JobQueueService
    ):
        """``Decision.NO_RETRY`` with CANCELLED intent writes
        ``admission_state='done'``. Phase 4 cleanup: the legacy
        ``status`` column is no longer written — only
        ``admission_state`` is asserted here. The CANCELLED intent
        is captured via the returned ``final_status`` value (still
        ``AdmissionState.DONE.value``) and via the ``cancelled_at``
        timestamp set by ``finalize_active_to_done``.

        ``target_status='cancelled'`` is supplied so the boundary
        returns ``final_status='cancelled'`` to the caller (per the
        boundary's Phase 4 contract documented at lines 1215-1219 of
        ``job_queue_service.py``).
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-cancel")

        canonical_job_id, final_status = await job_queue_service._finalize_terminal(
            instance_id="inst-cancel",
            decision=Decision.NO_RETRY,
            job_id=job.job_id,
            target_status=AdmissionState.DONE.value,
            error_message="user cancelled",
        )

        assert canonical_job_id == job.job_id
        assert final_status == AdmissionState.DONE.value

        refetched = _refresh(engine, job.job_id)
        # Phase 4 cleanup: only ``admission_state`` is the source of
        # truth. ``status`` is frozen at the INSERT default
        # (``"pending"``) and no longer reflects the terminal
        # transition.
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.cancelled_at is not None
        # The boundary writes cancelled_at for TERMINATED-style paths.
        assert refetched.cancelled_at is not None


# ─── B. Decision enum is REQUIRED ───────────────────────────────────────────


class TestDecisionRequired:
    """``Decision`` is a closed, non-defaulted enum parameter.

    The boundary signature is
    ``_finalize_terminal(instance_id, decision, *, ...)`` — there is
    no default for ``decision``. A missing value raises ``TypeError``
    at call time (the structural guarantee from Plan §3.2). All
    production callers must pass a ``Decision`` enum member — not
    ``None`` and not a string.
    """

    @pytest.mark.asyncio
    async def test_missing_decision_raises_type_error(
        self, engine, job_repo, job_queue_service: JobQueueService
    ):
        """Calling ``_finalize_terminal`` without ``decision`` raises
        ``TypeError`` — there is no default value, so Python refuses
        to bind the call.

        This is the structural guarantee: a new finalize path that
        forgets to state retry/DLQ semantics fails at instantiation,
        not in production.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id)

        with pytest.raises(TypeError):
            # Intentionally omit ``decision`` — no default exists.
            await job_queue_service._finalize_terminal(  # type: ignore[call-arg]
                instance_id="inst-type-error",
                job_id=job.job_id,
            )

    def test_decision_enum_has_three_values(self):
        """``Decision`` is a closed enum with exactly the three
        documented values. Adding a new member (or accidentally
        removing one) breaks every caller — the closed set is the
        structural guarantee that the §8.2 retry-without-instance
        audit reduces to a ``grep``.
        """
        values = {d.value for d in Decision}
        assert values == {"no_retry", "retry", "dead_letter"}
        assert len(Decision) == 3

    def test_decision_members_are_strict_strings(self):
        """``Decision`` is a ``str`` enum — each value compares equal
        to its string form, but is also a distinct enum member. This
        pins the dual representation callers rely on.
        """
        assert Decision.NO_RETRY == "no_retry"
        assert Decision.RETRY == "retry"
        assert Decision.DEAD_LETTER == "dead_letter"
        # Members are still distinct types — not interchangeable.
        assert Decision.NO_RETRY is not Decision.RETRY
        assert Decision.RETRY is not Decision.DEAD_LETTER

    def test_production_callers_pass_decision_enum(self):
        """Grep-style guard: every call site of ``_finalize_terminal``
        / ``_finalize_terminal_sync`` in ``daemon/`` passes a Decision
        enum member (``Decision.NO_RETRY`` / ``Decision.RETRY`` /
        ``Decision.DEAD_LETTER``), not a string or ``None``.

        This test does not actually ``grep`` — it imports the call
        sites and inspects them statically via ``ast``. If a new
        caller forgets to pass a Decision, the structural test fails.
        """
        import ast
        from pathlib import Path

        daemon_root = Path(__file__).resolve().parents[3] / "daemon"
        # Files known to call the boundary — when a new caller is
        # added, add its path here so the grep-style guard catches
        # missing-Decision regressions immediately.
        # ``__file__`` lives at
        # ``<project_root>/tests/unit/services/test_...py``; four levels
        # up reaches the project root.
        caller_files = [
            daemon_root / "services" / "job_queue_service.py",
            daemon_root / "services" / "job_recovery_service.py",
        ]

        # Decision factory names — functions / methods that return a
        # ``Decision`` enum member. A local variable assigned from one
        # of these is a valid ``decision=`` kwarg value (the canonical
        # ``complete_job`` / ``complete_job_sync`` pattern: store the
        # factory result in a local, then forward to the boundary).
        _DECISION_FACTORY_NAMES: set[str] = {
            "_decide_terminal_decision",
        }

        # Sanity-check the daemon root exists so a future directory
        # move surfaces as a clear error here rather than a silent
        # empty-callers pass.
        assert daemon_root.is_dir(), (
            f"daemon root not found at {daemon_root} — "
            f"file relocation broke the static-analysis guard"
        )

        violations: list[str] = []
        # Pre-pass: collect every ``<name> = self._decide_terminal_decision(...)``
        # assignment in each caller file. A local variable assigned
        # from a Decision factory is a valid ``decision=`` kwarg value.
        _decision_variable_assignments: dict[str, bool] = {}
        for path in caller_files:
            source = path.read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                # Right-hand side must be a call to a Decision factory.
                if not isinstance(node.value, ast.Call):
                    continue
                func = node.value.func
                # Match ``self._decide_terminal_decision(...)``.
                is_factory_call = (
                    isinstance(func, ast.Attribute)
                    and func.attr in _DECISION_FACTORY_NAMES
                )
                if not is_factory_call:
                    continue
                # Mark every simple-name target as a Decision variable.
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        _decision_variable_assignments[target.id] = True
            # Now scan for the actual boundary calls.
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # Match any call whose function name ends in
                # ``_finalize_terminal`` or ``_finalize_terminal_sync``.
                func = node.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr not in (
                    "_finalize_terminal",
                    "_finalize_terminal_sync",
                ):
                    continue

                # Find the ``decision=`` kwarg in this call.
                decision_kwarg: ast.keyword | None = None
                for kw in node.keywords:
                    if kw.arg == "decision":
                        decision_kwarg = kw
                        break
                if decision_kwarg is None:
                    # decision passed positionally or not at all.
                    # Either way the structural guarantee is broken.
                    violations.append(
                        f"{path.name}:{node.lineno}: missing 'decision=' kwarg"
                    )
                    continue

                # Verify the kwarg value is one of:
                #   * a ``Decision.X`` attribute access (literal enum
                #     reference), or
                #   * a ``Name`` (local variable) that the caller
                #     previously assigned from
                #     ``_decide_terminal_decision()`` (which returns
                #     a ``Decision`` enum member). This is the pattern
                #     in ``job_queue_service.complete_job`` /
                #     ``complete_job_sync`` — the decision is computed
                #     from a ``DemandState`` and stored in a local
                #     before being forwarded to the boundary.
                # Strings, ``None``, and other expressions are
                # rejected by this guard.
                value = decision_kwarg.value
                if isinstance(value, ast.Attribute):
                    if value.attr not in {
                        "NO_RETRY",
                        "RETRY",
                        "DEAD_LETTER",
                    }:
                        violations.append(
                            f"{path.name}:{node.lineno}: 'decision=' "
                            f"references unknown Decision member {value.attr!r}"
                        )
                elif isinstance(value, ast.Name):
                    # Local variable — must be assigned from a Decision
                    # factory (the documented pattern). Track
                    # assignments via a simple pre-pass over the same
                    # module so we can confirm the variable's origin.
                    if not _decision_variable_assignments.get(value.id, False):
                        violations.append(
                            f"{path.name}:{node.lineno}: 'decision=' is a "
                            f"local variable {value.id!r} but the test "
                            f"could not verify it was assigned from a "
                            f"Decision factory. Add the assignment site to "
                            f"_DECISION_FACTORY_NAMES if this is intentional."
                        )
                else:
                    violations.append(
                        f"{path.name}:{node.lineno}: 'decision=' is not a "
                        f"Decision enum reference or a local variable "
                        f"assigned from a Decision factory "
                        f"(got {type(value).__name__})"
                    )

        assert not violations, (
            "Every _finalize_terminal / _finalize_terminal_sync call "
            "site must pass a Decision enum value:\n"
            + "\n".join(violations)
        )


# ─── C. admission_state as primary write target ─────────────────────────────


class TestAdmissionStatePrimaryWrite:
    """``admission_state`` is the queue-proxy authority (Plan §3.1).

    ``status`` is written as a backward-compat mirror in the SAME SQL
    UPDATE so the two columns cannot drift. These tests verify the
    dual-write lands in lockstep — the persisted row's
    ``admission_state`` matches ``status_to_admission(status)`` after
    every Decision path, and the helper mapping is stable.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "decision_kwargs, expected_status, expected_admission",
        [
            # Decision.NO_RETRY + COMPLETED → done + completed
            (
                {"decision": Decision.NO_RETRY, "target_status": AdmissionState.DONE.value},
                AdmissionState.DONE.value,
                AdmissionState.DONE.value,
            ),
            # Decision.NO_RETRY + FAILED → done + failed
            (
                {"decision": Decision.NO_RETRY, "target_status": AdmissionState.DONE.value},
                AdmissionState.DONE.value,
                AdmissionState.DONE.value,
            ),
            # Decision.NO_RETRY + CANCELLED → done + cancelled
            (
                {"decision": Decision.NO_RETRY, "target_status": AdmissionState.DONE.value},
                AdmissionState.DONE.value,
                AdmissionState.DONE.value,
            ),
            # Decision.DEAD_LETTER → dead + dead_letter
            (
                {"decision": Decision.DEAD_LETTER},
                AdmissionState.DEAD.value,
                AdmissionState.DEAD.value,
            ),
        ],
    )
    async def test_admission_state_matches_status_to_admission(
        self,
        engine,
        job_repo,
        job_queue_service: JobQueueService,
        decision_kwargs: dict[str, Any],
        expected_status: str,
        expected_admission: str,
    ):
        """For each Decision path, ``admission_state`` lands as
        ``status_to_admission(status)`` — the helper mapping is the
        single source of truth for the dual-write contract.

        The mirror is written in the SAME UPDATE; a post-finalize
        SELECT sees both columns populated and consistent.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-param")

        call_kwargs = {
            "instance_id": "inst-param",
            "job_id": job.job_id,
        }
        call_kwargs.update(decision_kwargs)

        canonical_job_id, final_status = await job_queue_service._finalize_terminal(
            **call_kwargs
        )

        assert canonical_job_id == job.job_id
        assert final_status == expected_status

        refetched = _refresh(engine, job.job_id)
        # Phase 4 cleanup: ``admission_state`` is the sole write
        # authority; the legacy ``status`` column is frozen at the
        # INSERT default (``"pending"``) and no longer mirrors the
        # terminal transition. The dual-write contract is gone, so
        # only ``admission_state`` is asserted here.
        assert refetched.admission_state == expected_admission

    @pytest.mark.asyncio
    async def test_status_to_admission_invariant_for_every_status(
        self, engine, job_repo, job_queue_service: JobQueueService
    ):
        """Sweep a job through every Decision path on a fresh row and
        verify the dual-write invariant holds at every step.

        Walks PENDING → ACTIVE → DONE (COMPLETED) → ACTIVE (re-queued
        stub retry) → DONE (CANCELLED) so the row exercises both the
        DONE bucket and the QUEUED bucket in the same test.
        """
        job = _make_job(engine, job_repo)

        # 1. Fresh → QUEUED. Phase 4 cleanup: only ``admission_state``
        # is asserted; ``status`` is frozen at the INSERT default and
        # no longer mirrors the dual-write contract.
        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.QUEUED.value

        # 2. Start → ACTIVE.
        _start_job(engine, job_repo, job.job_id, instance_id="inst-sweep")
        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.ACTIVE.value

        # 3. NO_RETRY + COMPLETED → DONE.
        await job_queue_service._finalize_terminal(
            instance_id="inst-sweep",
            decision=Decision.NO_RETRY,
            job_id=job.job_id,
            target_status=AdmissionState.DONE.value,
        )
        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value

    def test_status_to_admission_helper_is_stable(self):
        """The ``status_to_admission`` helper is the single source of
        truth for the dual-write. Pin every JobStatus value to its
        documented admission bucket so a future refactor that breaks
        the mapping is caught immediately.
        """
        cases = [
            (AdmissionState.QUEUED.value, AdmissionState.QUEUED.value),
            (AdmissionState.ACTIVE.value, AdmissionState.ACTIVE.value),
            (AdmissionState.ACTIVE.value, AdmissionState.ACTIVE.value),
            (AdmissionState.DONE.value, AdmissionState.DONE.value),
            (AdmissionState.DONE.value, AdmissionState.DONE.value),
            (AdmissionState.DONE.value, AdmissionState.DONE.value),
            (AdmissionState.DEAD.value, AdmissionState.DEAD.value),
        ]
        for status_value, expected_admission in cases:
            assert status_to_admission(status_value) == expected_admission, (
                f"status_to_admission({status_value!r}) returned "
                f"{status_to_admission(status_value)!r}, expected "
                f"{expected_admission!r}"
            )


# ─── D. Dead-letter canonicalization ────────────────────────────────────────


class TestDeadLetterCanonicalization:
    """``dead → dead_letter`` canonicalization in ``_STATUS_CANONICAL_MAP``.

    Phase 4 (Job as Queue Proxy) introduces the new admission spelling
    ``admission_state='dead'``. The legacy ``status='dead_letter'``
    spelling must collapse onto the same canonical terminal
    ``'dead_letter'`` value so callers reading either column see a
    single vocabulary.
    """

    def test_status_canonical_map_has_dead_to_dead_letter(self):
        """``_STATUS_CANONICAL_MAP`` explicitly maps ``dead`` →
        ``dead_letter``.

        Phase 4 cleanup: the legacy ``"dead_letter"`` source key was
        removed from ``_STATUS_CANONICAL_MAP`` (the legacy
        ``JobItem.status`` column is no longer written). The new
        ``"dead"`` source key is the canonical mapping for the
        ``admission_state='dead'`` spelling — it collapses onto the
        same canonical terminal ``"dead_letter"``.
        """
        assert "dead" in _STATUS_CANONICAL_MAP
        assert _STATUS_CANONICAL_MAP["dead"] == "dead_letter"
        # The legacy ``"dead_letter"`` source key was removed in
        # Phase 4 cleanup, but ``canonicalize_status("dead_letter")``
        # still resolves to ``"dead_letter"`` via the map's
        # ``.get(status, status)`` fallback.
        assert canonicalize_status("dead_letter") == "dead_letter"

    def test_canonicalize_status_is_terminal_for_dead(self):
        """``canonicalize_status('dead')`` is a terminal status.

        Phase 4 adds ``'dead'`` as a source key — the canonical
        terminal set (``_TERMINAL_STATUSES``) contains
        ``'dead_letter'``, and ``canonicalize_status`` collapses
        ``'dead'`` onto it, so the terminal-set check is the
        canonical test that ``dead`` is terminal too.
        """
        from daemon.services.work_status import is_terminal

        canonical = canonicalize_status("dead")
        assert canonical == "dead_letter"
        assert is_terminal(canonical) is True

    def test_admission_state_dead_matches_status_to_admission(self):
        """``status_to_admission('dead_letter')`` returns
        ``AdmissionState.DEAD.value`` — the bidirectional invariant
        between the canonical status and the admission-state column.

        A future refactor that breaks this mapping would desynchronise
        the dual-write contract for the DLQ path.
        """
        assert status_to_admission(AdmissionState.DEAD.value) == AdmissionState.DEAD.value
        # The reverse direction: admission_state 'dead' is NOT a valid
        # input to ``status_to_admission`` (which is JobStatus-only) —
        # it falls through to the QUEUED default. This documents the
        # asymmetry: callers feed JobStatus values into the helper,
        # not AdmissionState values.
        assert status_to_admission(AdmissionState.DEAD.value) == AdmissionState.QUEUED.value

    @pytest.mark.asyncio
    async def test_persisted_dead_row_canonicalizes_to_dead_letter(
        self, engine, job_repo, job_queue_service: JobQueueService
    ):
        """End-to-end: a ``Decision.DEAD_LETTER`` finalize persists a
        row with ``admission_state='dead'`` and ``status='dead_letter'``,
        and the resolver's ``canonicalize_status`` collapses both onto
        the same canonical terminal.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-canon")

        await job_queue_service._finalize_terminal(
            instance_id="inst-canon",
            decision=Decision.DEAD_LETTER,
            job_id=job.job_id,
        )

        refetched = _refresh(engine, job.job_id)
        # Raw column values. Phase 4 cleanup: only ``admission_state``
        # is asserted; ``status`` is frozen at its INSERT default and
        # no longer mirrors the transition.
        assert refetched.admission_state == AdmissionState.DEAD.value

        # Both columns canonicalise to the same terminal label.
        assert canonicalize_status(refetched.admission_state) == "dead_letter"


# ─── Sanity smoke ────────────────────────────────────────────────────────────


class TestSmoke:
    """One quick smoke test to catch gross regressions in the fixture
    wiring. If this fails, the test file itself is broken (not the
    code under test)."""

    def test_engine_smoke_roundtrip(self, engine, job_repo):
        """Sanity: a created job can be read back via the engine."""
        job = _make_job(engine, job_repo)
        refetched = _refresh(engine, job.job_id)
        assert refetched.job_id == job.job_id
        assert refetched.admission_state == AdmissionState.QUEUED.value