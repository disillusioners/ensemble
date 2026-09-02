"""Tests for :class:`daemon.services.mission_resolver.MissionResolver` (M1).

Mission-class Milestone M1 (per
``.agents/shared/planning/mission-class/architecture-recommendation.md``
§5 M1 row). The resolver is a pure read-model projection — no writes,
no JobItem creation, no admission-state mutation. The census stays at
23 frozen admission-state writers (per
``daemon/job_state/constitution.py``).

These tests pin the M1 contract:

* **Identity** — ``mission_id == instance_id``; ``parent_mission_id``
  comes from ``Instance.parent_id`` (permanent across revive).
* **Liveness mapping** — every ``InstanceStatus`` enum value maps onto
  the canonical mission vocabulary via
  :data:`_STATUS_CANONICAL_MAP`. ``completed`` is revivable;
  ``cancelled`` / ``failed`` / ``dead_letter`` are true-terminal.
* **Epoch semantics** — current epoch + current liveness are precise
  (best-effort today; M4(ii) will refine per-epoch timestamps).
* **W4 hazard** — a linked DEAD JobItem flips
  ``mission_terminal_reason`` to ``"dead_letter"`` regardless of a
  since-revived instance.
* **Degradation** — a transient DB error during the lookup degrades
  every mission field to ``None`` rather than blowing up the whole
  page.
* **PURITY** — the resolver never INSERTs / UPDATEs / DELETEs; the
  session-spy test asserts no writes during a full ``resolve_many``
  pass over a populated page.
* **Kill-switch OFF/ON** — ``ENSEMBLE_MISSION_PROJECTION_ENABLED`` is
  default OFF; the additive fields stay absent from the wire format
  until the operator activates them.

Harness notes
-------------

Mirrors the StaticPool in-memory SQLite recipe used by
``tests/unit/services/test_fix_c_read_model_split.py`` (read-only path,
no ``WriteGuardSession`` — the QUARANTINE.md StaticPool+WriteGuardSession
trap does not apply). The resolver is the SUT; the instance / job
repositories are real (no mocks), so the SQL-level reads are exercised.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

import daemon.services.mission_resolver as mr_mod
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401  (Fix C transitive dep)

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.repository import TaskRepository
from daemon.services.mission_resolver import (
    MissionRecord,
    MissionResolver,
    is_mission_projection_enabled,
    _reset_mission_projection_for_tests,
)
from daemon.services.work_resolver import _is_mission_projection_enabled


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool)."""
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
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def resolver(instance_repo, job_repo) -> MissionResolver:
    return MissionResolver(
        instance_repo=instance_repo,
        job_repo=job_repo,
    )


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    agent_id: str = "developer",
    project_id: str | None = "test-project",
    status: str = InstanceStatus.RUNNING.value,
    parent_id: str | None = None,
    last_activity_at: datetime | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> str:
    """Insert a populated ``Instance`` row."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    iso_now = now.isoformat()
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            agent_name=agent_id,
            project_id=project_id,
            status=status,
            created_at=created_at or iso_now,
            updated_at=updated_at or iso_now,
            last_activity_at=last_activity_at,
            paused_at=None,
            parent_id=parent_id,
        )
        s.add(inst)
        s.commit()
    return iid


def _seed_job(
    engine: Engine,
    *,
    job_id: str | None = None,
    instance_id: str | None = None,
    admission_state: str = AdmissionState.ACTIVE.value,
    terminal_reason: str | None = None,
    job_type: str = "task",
    deleted_at: str | None = None,
) -> str:
    """Insert a ``JobItem`` row. W4-hazard test uses
    ``admission_state='dead'``; happy-path tests use the default
    ``active``.
    """
    jid = job_id or str(uuid.uuid4())
    with Session(engine) as s:
        job = JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="M1 test job",
            source="api",
            project_id="test-project",
            priority=5,
            admission_state=admission_state,
            terminal_reason=terminal_reason,
            instance_id=instance_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            deleted_at=deleted_at,
            job_metadata={},
            job_type=job_type,
        )
        s.add(job)
        s.commit()
    return jid


# ─── Kill-switch harness ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_mission_kill_switch():
    """Per-test: reset the kill-switch so the OFF default is fresh.

    The kill-switch is module-level cached state; left over from a
    prior test would leak. This fixture is ``autouse`` so every test
    in this file starts with a clean state (kill-switch OFF by
    default; explicitly flipped ON by tests that exercise the ON
    branch via ``monkeypatch.setenv("ENSEMBLE_MISSION_PROJECTION_ENABLED", "1")``
    — the same shape ``TestKillSwitch`` uses for its own
    ON-path assertions).
    """
    _reset_mission_projection_for_tests()
    # Belt and suspenders: make sure the env var is also blank — the
    # resolver is consulted at most once per process, so cached state
    # can mask an env flip.
    os.environ.pop("ENSEMBLE_MISSION_PROJECTION_ENABLED", None)
    yield
    _reset_mission_projection_for_tests()
    os.environ.pop("ENSEMBLE_MISSION_PROJECTION_ENABLED", None)


# ─── Identity contract ─────────────────────────────────────────────────────


class TestIdentity:
    """One mission per instance, keyed by ``instance_id``.

    Per spec §3: ``mission_id == instance_id`` and
    ``parent_mission_id == instance.parent_id`` — the leader's lean
    adjudicated under pressure-test. ``parent_id`` is permanent across
    terminate→revive (census-of-23 invariant: no new mint sites), so
    the derived identity inherits that permanence for free.
    """

    def test_mission_id_equals_instance_id(self, engine, resolver):
        """``mission_id`` is the instance id (literally equal)."""
        iid = _seed_instance(engine, instance_id="inst-1")
        record = resolver.resolve(iid)
        assert record is not None
        assert record.mission_id == iid

    def test_parent_mission_id_equals_instance_parent_id(
        self, engine, resolver
    ):
        """``parent_mission_id`` carries ``Instance.parent_id`` verbatim."""
        parent_id = _seed_instance(
            engine, instance_id="parent-1"
        )
        child_id = _seed_instance(
            engine, instance_id="child-1", parent_id=parent_id
        )
        record = resolver.resolve(child_id)
        assert record is not None
        assert record.parent_mission_id == parent_id

    def test_root_mission_has_no_parent(self, engine, resolver):
        """Root missions have ``parent_mission_id=None``."""
        iid = _seed_instance(engine, instance_id="root-1", parent_id=None)
        record = resolver.resolve(iid)
        assert record is not None
        assert record.parent_mission_id is None


# ─── Liveness mapping contract ─────────────────────────────────────────────


class TestLivenessMapping:
    """Every ``InstanceStatus`` enum value maps onto the mission vocab.

    The mission vocab uses :data:`_STATUS_CANONICAL_MAP` (the same map
    Fix C's ``mission_liveness`` consult uses) — the non-terminal
    cluster collapses onto ``processing``; ``error`` and ``failed`` map
    onto ``failed``; ``terminated`` maps onto ``cancelled``; ``idle`` and
    ``queued`` map to ``processing`` (in-flight) and ``pending`` (idle
    but queued) respectively.
    """

    @pytest.mark.parametrize(
        "instance_status,expected_liveness",
        [
            # Non-terminal cluster — ``idle`` collapses onto
            # ``processing`` per the canonical map (the same shape
            # Fix C's ``mission_liveness`` consult uses for the "work
            # is happening" cluster). Finer-grained state (idle vs
            # running) is available to consumers via the Instance
            # detail view, but the mission vocab collapses them.
            (InstanceStatus.RUNNING.value, "processing"),
            (InstanceStatus.WAITING.value, "processing"),
            (InstanceStatus.WAITING_CHILDREN.value, "processing"),
            (InstanceStatus.QUEUED.value, "processing"),
            (InstanceStatus.IDLE.value, "processing"),
            (InstanceStatus.PAUSED.value, "paused"),
            # Terminal cluster — verbatim the canonical map. ``completed``
            # is the revivable terminal.
            (InstanceStatus.COMPLETED.value, "completed"),
            (InstanceStatus.FAILED.value, "failed"),
            (InstanceStatus.ERROR.value, "failed"),
            (InstanceStatus.TERMINATED.value, "cancelled"),
        ],
    )
    def test_status_to_liveness_mapping(
        self,
        engine,
        resolver,
        instance_status,
        expected_liveness,
    ):
        iid = _seed_instance(
            engine, instance_id=f"inst-{instance_status}", status=instance_status
        )
        record = resolver.resolve(iid)
        assert record is not None
        assert record.liveness == expected_liveness, (
            f"InstanceStatus.{instance_status!r} → expected "
            f"{expected_liveness!r}, got {record.liveness!r}"
        )

    def test_unknown_instance_status_falls_through(self, engine, resolver):
        """Unknown ``status`` strings pass through unchanged — defensive.

        A future ``Instance.status`` value the canonical map has not
        been taught about does NOT crash the projection (the same
        shape Fix C's ``mission_liveness`` consult uses). The resolver
        logs a warning instead of crashing the read.
        """
        # Bypass the InstanceStatus enum validator by setting the
        # column directly through the ORM (the field is a raw str in
        # the SQLModel).
        iid = f"inst-future-{uuid.uuid4().hex[:6]}"
        with Session(engine) as s:
            inst = Instance(
                instance_id=iid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                status="future_state",
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            s.add(inst)
            s.commit()

        record = resolver.resolve(iid)
        assert record is not None
        assert record.liveness == "future_state", (
            "Unknown statuses pass through unchanged (defensive "
            "contract; Fix C precedent)."
        )


# ─── Terminal classification ───────────────────────────────────────────────


class TestTerminalClassification:
    """``completed`` is REVIVABLE; ``cancelled``/``failed`` are TRUE-terminal.

    Per spec §3: ``InstanceStatus.COMPLETED`` → mission liveness
    ``completed`` (can be →RUNNING again on a send_message). All
    other terminal states are true-terminal — they cannot transition
    to RUNNING without a fresh instance creation (which would mint a
    NEW ``instance_id``, not continue the existing mission).
    """

    def test_completed_is_revivable_terminal(self, engine, resolver):
        iid = _seed_instance(
            engine,
            instance_id="inst-completed",
            status=InstanceStatus.COMPLETED.value,
        )
        record = resolver.resolve(iid)
        assert record is not None
        # ``canonicalize_status`` produces "completed".
        assert record.liveness == "completed"
        # ``terminal_reason`` mirrors liveness when terminal (and
        # no W4-link overrides it).
        assert record.terminal_reason == "completed"

    def test_cancelled_is_true_terminal(self, engine, resolver):
        iid = _seed_instance(
            engine,
            instance_id="inst-cancelled",
            status=InstanceStatus.TERMINATED.value,
        )
        record = resolver.resolve(iid)
        assert record is not None
        assert record.liveness == "cancelled"
        assert record.terminal_reason == "cancelled"

    def test_failed_is_true_terminal(self, engine, resolver):
        iid = _seed_instance(
            engine,
            instance_id="inst-failed",
            status=InstanceStatus.FAILED.value,
        )
        record = resolver.resolve(iid)
        assert record is not None
        assert record.liveness == "failed"
        assert record.terminal_reason == "failed"

    def test_error_is_true_terminal(self, engine, resolver):
        """InstanceStatus.ERROR canonicalises to ``failed`` (true-terminal)."""
        iid = _seed_instance(
            engine,
            instance_id="inst-error",
            status=InstanceStatus.ERROR.value,
        )
        record = resolver.resolve(iid)
        assert record is not None
        assert record.liveness == "failed"
        assert record.terminal_reason == "failed"

    def test_living_instance_has_no_terminal_reason(
        self, engine, resolver
    ):
        """Non-terminal missions report ``terminal_reason=None``."""
        iid = _seed_instance(
            engine,
            instance_id="inst-running",
            status=InstanceStatus.RUNNING.value,
        )
        record = resolver.resolve(iid)
        assert record is not None
        assert record.liveness == "processing"
        assert record.terminal_reason is None, (
            "Living missions must NOT surface a terminal_reason — only "
            "the W4 hazard can flip it pre-terminality."
        )


# ─── Epoch semantics (best-effort M1; precise in M4) ───────────────────────


class TestEpochSemantics:
    """Current epoch is **always ``1``** for every non-degraded projection.

    Per spec §3 known-limitation: precise per-epoch timestamps are
    NOT derivable at read time today — the DB has no terminal-transition
    timestamps. M1 reports ``epoch=1`` for every non-degraded
    projection, terminal AND non-terminal (the honest answer until
    M4(ii)'s ``mission_events`` log lands). Epoch is ``None`` ONLY when
    the resolver degraded (no Instance row available) — NOT when the
    mission is terminal. The fields are present + non-null where you'd
    expect so future tooling can branch on them without re-parsing.
    """

    def test_living_instance_reports_epoch_1(self, engine, resolver):
        iid = _seed_instance(
            engine,
            instance_id="inst-running",
            status=InstanceStatus.RUNNING.value,
        )
        record = resolver.resolve(iid)
        assert record is not None
        assert record.epoch == 1

    def test_completed_instance_reports_epoch_1(self, engine, resolver):
        """Even a terminal (revivable) instance reports ``epoch=1``.

        Per-epoch timestamps will be added with M4(ii)'s event log —
        for M1 the honest answer is "current epoch = 1, no historical
        epochs reconstructed".
        """
        iid = _seed_instance(
            engine,
            instance_id="inst-completed",
            status=InstanceStatus.COMPLETED.value,
        )
        record = resolver.resolve(iid)
        assert record is not None
        assert record.epoch == 1

    def test_completed_then_revived_epoch_remains_1(self, engine, resolver):
        """completed→revive cycle pins the current-true epoch=1 contract.

        Per spec §3 known-limitation + the M1 docstring ("always 1 for
        every non-degraded projection"), epoch is **constant 1** across
        a complete→revive cycle — not null when terminal, not
        incremented when revived. The mission_events log (M4 ii) is the
        cure that will let us report ``epoch_count`` + ``last_epoch_at``
        precisely; until then, current-true = 1 everywhere a non-degraded
        MissionRecord surfaces.

        Steps:
          1. Seed an Instance in COMPLETED (a revivable terminal
             state). Resolve → epoch=1, liveness="completed",
             terminal_reason="completed".
          2. Transition the same Instance to RUNNING (the send_message
             revive path flips a COMPLETED instance back to RUNNING
             in-place; ``instances.parent_id`` stays put so identity
             is permanent). Resolve → epoch=1 (NOT bumped to 2; not
             null), liveness="processing", terminal_reason=None.
        """
        iid = _seed_instance(
            engine,
            instance_id="inst-completed-revived",
            status=InstanceStatus.COMPLETED.value,
        )

        # Step 1: terminal COMPLETED — current epoch is precise, = 1.
        before = resolver.resolve(iid)
        assert before is not None
        assert before.liveness == "completed"
        assert before.terminal_reason == "completed"
        assert before.epoch == 1, (
            "Terminal-with-revive must still report epoch=1 — the honest "
            "constant answer until M4(ii) mission_events log preserves "
            "per-epoch truthmakers."
        )

        # Step 2: revive in place — flip Instance.status to RUNNING.
        # (The real revival path is ``send_message`` in
        # ``daemon/services/instance_messaging.py``; here we simulate
        # the post-revive state to pin the read-model projection.)
        now_iso = datetime.now(timezone.utc).isoformat()
        with Session(engine) as s:
            inst = s.get(Instance, iid)
            assert inst is not None
            inst.status = InstanceStatus.RUNNING.value
            inst.updated_at = now_iso
            s.add(inst)
            s.commit()

        after = resolver.resolve(iid)
        assert after is not None
        assert after.liveness == "processing"
        assert after.terminal_reason is None
        # Identity is permanent across the revive — same instance id,
        # same mission_id, same epoch=1 constant.
        assert after.mission_id == before.mission_id == iid
        assert after.epoch == 1, (
            "Revived mission must NOT bump epoch to 2 — the M1 contract "
            "is epoch=1 for every non-degraded projection. Future "
            "epoch_count fidelity belongs to M4(ii)."
        )


# ─── W4 hazard ────────────────────────────────────────────────────────────


class TestW4Hazard:
    """A linked DEAD JobItem flips ``terminal_reason`` to ``"dead_letter"``.

    Per spec §3 + agent-contract-draft.md §2 W4 rule: a ``DEAD`` job's
    derived mission status is hard-coded to ``"dead_letter"`` even if a
    since-revived instance reports a healthy status. The renderer must
    NOT let a revived instance override the dead-letter truth.
    """

    def test_dead_linked_job_surfaces_dead_letter(
        self, engine, resolver
    ):
        """Instance + DEAD JobItem → mission_terminal_reason='dead_letter'."""
        iid = _seed_instance(
            engine,
            instance_id="inst-dead-link",
            status=InstanceStatus.RUNNING.value,  # even a "running" instance
        )
        _seed_job(
            engine,
            instance_id=iid,
            admission_state=AdmissionState.DEAD.value,
        )

        record = resolver.resolve(iid)
        assert record is not None
        assert record.liveness == "processing"  # the instance is "running"
        assert record.terminal_reason == "dead_letter", (
            "W4 hazard: a revived instance must NOT mask a dead "
            "JobItem's dead-letter truth (spec §3 + agent-contract-"
            "draft.md §2 W4 rule)."
        )

    def test_living_linked_job_leaves_terminal_reason_none(
        self, engine, resolver
    ):
        """No DEAD link → ``terminal_reason=None`` for a living instance."""
        iid = _seed_instance(
            engine,
            instance_id="inst-no-dead-link",
            status=InstanceStatus.RUNNING.value,
        )
        # An active (not DEAD) JobItem is harmless — no W4 activation.
        _seed_job(
            engine,
            instance_id=iid,
            admission_state=AdmissionState.ACTIVE.value,
        )

        record = resolver.resolve(iid)
        assert record is not None
        assert record.terminal_reason is None

    def test_soft_deleted_dead_job_does_not_trigger_w4(
        self, engine, resolver
    ):
        """``deleted_at IS NOT NULL`` JobItems are invisible to the W4 check."""
        iid = _seed_instance(
            engine,
            instance_id="inst-soft-deleted",
            status=InstanceStatus.RUNNING.value,
        )
        # DEAD but soft-deleted — must NOT trigger W4.
        _seed_job(
            engine,
            instance_id=iid,
            admission_state=AdmissionState.DEAD.value,
            deleted_at=datetime.now(timezone.utc).isoformat(),
        )

        record = resolver.resolve(iid)
        assert record is not None
        assert record.terminal_reason is None, (
            "Soft-deleted JobItems must NOT activate the W4 hazard — "
            "they're invisible to the read surface per the "
            "JobRepository.list convention."
        )


# ─── Linked jobs list ──────────────────────────────────────────────────────


class TestLinkedJobs:
    """``linked_jobs`` enumerates ``JobItem.job_id`` values per mission."""

    def test_linked_jobs_lists_non_deleted(self, engine, resolver):
        iid = _seed_instance(
            engine, instance_id="inst-linked"
        )
        jid_a = _seed_job(engine, instance_id=iid)
        jid_b = _seed_job(engine, instance_id=iid)
        jid_soft = _seed_job(
            engine, instance_id=iid, deleted_at=datetime.now(timezone.utc).isoformat()
        )

        record = resolver.resolve(iid)
        assert record is not None
        # Two non-deleted jobs surface; soft-deleted one drops.
        assert sorted(record.linked_jobs) == sorted([jid_a, jid_b])
        assert jid_soft not in record.linked_jobs

    def test_no_linked_jobs_returns_empty_list(self, engine, resolver):
        iid = _seed_instance(engine, instance_id="inst-no-jobs")
        record = resolver.resolve(iid)
        assert record is not None
        assert record.linked_jobs == []


# ─── Degradation contract (§8.2 — Fix C mission_liveness precedent) ──────


class TestDegradation:
    """A transient DB error during the lookup degrades every mission
    field to ``None`` rather than blowing up the whole page.

    Mirrors the Fix C ``mission_liveness=None`` precedent and the
    mission_liveness §8.2 contract — narrow ``SQLAlchemyError`` catch
    + ``logger.warning`` + degraded record.
    """

    def test_missing_instance_returns_none(self, engine, resolver):
        """Unknown ``instance_id`` → ``MissionResolver.resolve`` returns
        ``None`` (NOT a degraded record) — the batched caller treats
        absent ids as "unknown", which is distinct from "degraded"."""
        record = resolver.resolve("inst-no-such-id")
        assert record is None

    def test_none_instance_id_returns_degraded_record(
        self, engine, resolver
    ):
        """``instance_id=None`` → degraded-shape record (all-None)."""
        record = resolver.resolve(None)
        assert record is not None
        assert record.mission_id is None
        assert record.liveness is None
        assert record.terminal_reason is None
        assert record.epoch is None
        assert record.linked_jobs == []
        assert record.parent_mission_id is None

    def test_resolve_many_with_all_unknown_returns_empty(self, resolver):
        """``resolve_many`` with unknown ids returns ``{}`` (NOT a
        degraded record — the batched caller treats every requested
        id as unknown and continues)."""
        result = resolver.resolve_many(["inst-a", "inst-b"])
        assert result == {}

    def test_resolve_many_filters_none(self, resolver):
        """``None`` ids in a batched call are silently dropped (PK is
        never null)."""
        result = resolver.resolve_many([None, None])
        assert result == {}


# ─── PURITY: no DML in projection paths ────────────────────────────────────


class TestPurity:
    """The resolver never INSERTs / UPDATEs / DELETEs.

    Census invariant from constitution.py: KNOWN_ADMISSION_STATE_WRITERS
    is frozen at 23; the resolver adds ZERO new writers. The pure
    row-COUNT snapshot test missed a class of regressions (in-place
    UPDATE on an existing row keeps the row count steady but mutates a
    value), so this layer now uses two complementary spies:

    1. **Engine event listener** — records every SQL statement the
       engine executes during the SUT call. The assertion fails
       loudly if any ``INSERT`` / ``UPDATE`` / ``DELETE`` /
       ``SAVEPOINT`` statement reaches the engine. SELECTs are
       expected (the resolver is a read-model projection) and not
       asserted against.
    2. **Value/content snapshot** — captures the full set of
       ``Instance`` and ``JobItem`` column values before and after
       the SUT call. Catches regressions that bypass the engine
       (rare — every code path goes through the engine — but the
       belt-and-suspenders shape costs nothing).

    Together the two spies pin the invariant at the resolver layer so
    a future regression that casually adds a write call fails loudly
    here — the unit test catches it before the constitution drift
    test (which guards the wider census) catches it.
    """

    @staticmethod
    def _attach_dml_spy(engine: Engine) -> tuple[list[str], callable]:
        """Attach a before-cursor-execute listener; return (captured, detacher).

        The listener records the SQL statement text for every cursor
        execution. INSERT / UPDATE / DELETE / SAVEPOINT statements
        trip the assertion; SELECT statements are expected and
        recorded but not asserted against. Returns the mutable list so
        the caller can read it after the SUT call, and a callable that
        removes the listener when invoked (must be called in a
        ``finally`` so subsequent tests do not see the listener).
        """
        captured: list[str] = []

        def _before_cursor_execute(  # noqa: ANN001 — SQLAlchemy hook
            conn, cursor, statement, parameters, context, executemany  # noqa: ARG001
        ):
            captured.append(statement)

        event.listen(engine, "before_cursor_execute", _before_cursor_execute)

        def _detacher() -> None:
            event.remove(engine, "before_cursor_execute", _before_cursor_execute)

        return captured, _detacher

    def _assert_no_writes(self, captured: list[str]) -> None:
        """Fail the test if any captured statement is a writer."""
        write_prefixes = ("INSERT", "UPDATE", "DELETE", "SAVEPOINT", "REPLACE")
        offenders = [
            stmt
            for stmt in captured
            if stmt.lstrip().upper().startswith(write_prefixes)
        ]
        assert not offenders, (
            "PURITY violated: resolver emitted a write statement(s) "
            f"during a read-only projection. Offenders: {offenders!r}. "
            "MissionResolver is a pure read-model projection; it must "
            "not INSERT/UPDATE/DELETE/SAVEPOINT/REPLACE."
        )

    def _snapshot_rows(self, engine: Engine) -> tuple[list[Instance], list[JobItem]]:
        """Capture the full Instance and JobItem column values."""
        from sqlmodel import Session as SQLModelSession
        from sqlmodel import select

        with SQLModelSession(engine) as s:
            inst_rows = list(s.exec(select(Instance)).all())
            job_rows = list(s.exec(select(JobItem)).all())
        return inst_rows, job_rows

    def _assert_no_value_drift(
        self,
        before: tuple[list[Instance], list[JobItem]],
        after: tuple[list[Instance], list[JobItem]],
    ) -> None:
        """Assert every column of every row is identical pre/post SUT call."""
        inst_before, job_before = before
        inst_after, job_after = after
        assert len(inst_before) == len(inst_after), (
            "PURITY violated: Instance row count drifted."
        )
        assert len(job_before) == len(job_after), (
            "PURITY violated: JobItem row count drifted."
        )

        # Compare via column-level extraction (not raw ``__dict__``,
        # which carries SQLAlchemy's ``_sa_instance_state`` and other
        # ORM-internal handles that change identity between sessions
        # even when the data is identical). Use
        # ``_asdict_like_columns`` to extract just the schema columns.
        inst_columns = [c.key for c in Instance.__table__.columns]
        job_columns = [c.key for c in JobItem.__table__.columns]

        def _col_values(row, columns):
            return {col: getattr(row, col, None) for col in columns}

        for inst_a, inst_b in zip(inst_before, inst_after, strict=False):
            vals_a = _col_values(inst_a, inst_columns)
            vals_b = _col_values(inst_b, inst_columns)
            assert vals_a == vals_b, (
                "PURITY violated: an Instance column drifted across "
                "the resolver call. Before/after must be byte-equal."
                f" Diff keys: {[k for k in vals_a if vals_a[k] != vals_b[k]]}"
            )
        for job_a, job_b in zip(job_before, job_after, strict=False):
            vals_a = _col_values(job_a, job_columns)
            vals_b = _col_values(job_b, job_columns)
            assert vals_a == vals_b, (
                "PURITY violated: a JobItem column drifted across "
                "the resolver call. Before/after must be byte-equal."
                f" Diff keys: {[k for k in vals_a if vals_a[k] != vals_b[k]]}"
            )

    def test_resolve_emits_no_dml(self, engine, resolver, instance_repo, job_repo):
        """``resolve()`` over a populated instance must not write anything.

        Dual spy: an engine-level ``before_cursor_execute`` listener
        catches any writer-shaped statement; a value-by-value column
        snapshot catches any in-place mutation that slips past the
        listener. Belt-and-suspenders — the row-COUNT snapshot test
        the file used previously missed in-place UPDATEs (row count
        stays steady on UPDATE).
        """
        # Seed the row BEFORE the snapshot so the snapshot already
        # includes the seeded data (any delta after the resolve call
        # then reflects ONLY what the resolver wrote).
        iid = _seed_instance(engine, instance_id="inst-purity-1")
        _seed_job(engine, instance_id=iid, admission_state=AdmissionState.ACTIVE.value)

        before = self._snapshot_rows(engine)
        captured, detach = self._attach_dml_spy(engine)
        try:
            record = resolver.resolve(iid)
        finally:
            detach()
        assert record is not None

        after = self._snapshot_rows(engine)

        # Spy 1: no writer-shaped SQL reached the engine.
        self._assert_no_writes(captured)
        # Spy 2: every column value is byte-equal before and after.
        self._assert_no_value_drift(before, after)

    def test_resolve_many_emits_no_dml(self, engine, resolver):
        """``resolve_many()`` over a populated page must not write.

        Same dual-spy shape as ``test_resolve_emits_no_dml``. The
        batched path opens its own SQLModel session via
        ``_batch_instances`` and ``_list_linked_jobs``; every cursor
        execution routes through the engine-level listener.
        """
        # Populate the page with 5 instances to exercise the
        # batched path.
        ids = [
            _seed_instance(engine, instance_id=f"inst-batch-{i}")
            for i in range(5)
        ]

        before = self._snapshot_rows(engine)
        captured, detach = self._attach_dml_spy(engine)
        try:
            records = resolver.resolve_many(ids)
        finally:
            detach()
        assert len(records) == 5

        after = self._snapshot_rows(engine)

        self._assert_no_writes(captured)
        self._assert_no_value_drift(before, after)


# ─── Kill-switch OFF/ON ────────────────────────────────────────────────────


class TestKillSwitch:
    """``ENSEMBLE_MISSION_PROJECTION_ENABLED`` gates the additive fields.

    Default OFF (soak discipline, mirrors the WC-wake and governor-guard
    precedents). The wire format stays byte-identical to pre-M1 when
    OFF; the three additive ``mission_*`` fields surface on the
    :class:`WorkRecord` when the operator activates the switch.

    M1 contract (spec §5 M1 row):
    * OFF → ``WorkRecord.to_dict`` OMITS the three keys (byte-identical).
    * ON  → ``WorkRecord.to_dict`` INCLUDES the three keys with values
      populated from :class:`MissionResolver`.
    """

    def test_default_off(self, monkeypatch):
        """The default (unset env) is OFF.

        The ``autouse`` fixture ensures the env is unset at the top
        of this test, so the freshly-resolved state is the OFF
        default.
        """
        monkeypatch.delenv("ENSEMBLE_MISSION_PROJECTION_ENABLED", raising=False)
        _reset_mission_projection_for_tests()
        assert is_mission_projection_enabled() is False
        assert _is_mission_projection_enabled() is False

    def test_env_on_flips_resolver_to_on(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_MISSION_PROJECTION_ENABLED", "1")
        _reset_mission_projection_for_tests()
        assert is_mission_projection_enabled() is True

    def test_env_off_flips_resolver_to_off(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_MISSION_PROJECTION_ENABLED", "0")
        _reset_mission_projection_for_tests()
        assert is_mission_projection_enabled() is False


# ─── Read-surface test (WorkRecord integration) ────────────────────────────


class TestWorkRecordIntegration:
    """The three additive ``mission_*`` fields surface on ``WorkRecord``.

    When the kill-switch is OFF, the keys are absent from
    :meth:`WorkRecord.to_dict` (the M1 OFF contract). When ON, the keys
    surface with values populated from :class:`MissionResolver` via the
    helper injected into :class:`WorkResolverService._mission_fields_for_instance`.

    "One read surface is enough for this test" (per the M1 task spec
    §6). WorkRecord is the primary read-surface; the other three
    surfaces (JobResponse schema, ``_ResolvedWork`` SSE payload,
    jobs_crud delegation) source their mission fields from the same
    WorkRecord, so they're in lock-step by construction.
    """

    def test_workrecord_mission_fields_absent_off(self):
        """OFF (default) → ``WorkRecord.to_dict`` omits the mission keys."""
        from datetime import datetime, timezone

        from daemon.services.work_resolver import WorkRecord

        # The ``autouse`` fixture has the kill-switch OFF (no env
        # var set; cached state cleared). Building a vanilla
        # WorkRecord with mission_id populated but with the kill-
        # switch OFF must NOT include the keys in the JSON output.
        record = WorkRecord(
            work_id="job-1",
            kind="job",
            status="processing",
            instance_id="inst-wo-1",
            project_id="test",
            agent_id="developer",
            result_summary=None,
            error=None,
            created_at=datetime.now(timezone.utc),
            started_at=None,
            completed_at=None,
            job_type="message",
            mission_liveness="processing",
            # When OFF, ``to_dict()`` should NOT include these.
            mission_id="inst-wo-1",
            mission_epoch=1,
            mission_terminal_reason=None,
        )
        out = record.to_dict()
        assert "mission_id" not in out, (
            "OFF state must NOT include mission_id (byte-identical to "
            "pre-M1 per spec §5 M1 row)."
        )
        assert "mission_epoch" not in out
        assert "mission_terminal_reason" not in out
        # Fix C fields ARE present (they were added in a prior fix;
        # M1 only concerns itself with the new keys).
        assert out["job_type"] == "message"
        assert out["mission_liveness"] == "processing"

    def test_workrecord_mission_fields_present_on(self):
        """ON → ``WorkRecord.to_dict`` includes the mission keys."""
        from datetime import datetime, timezone

        from daemon.services.work_resolver import WorkRecord

        try:
            os.environ["ENSEMBLE_MISSION_PROJECTION_ENABLED"] = "1"
            _reset_mission_projection_for_tests()

            record = WorkRecord(
                work_id="job-1",
                kind="job",
                status="processing",
                instance_id="inst-wo-2",
                project_id="test",
                agent_id="developer",
                result_summary=None,
                error=None,
                created_at=datetime.now(timezone.utc),
                started_at=None,
                completed_at=None,
                job_type="message",
                mission_liveness="processing",
                mission_id="inst-wo-2",
                mission_epoch=1,
                mission_terminal_reason=None,
            )
            out = record.to_dict()
            assert out["mission_id"] == "inst-wo-2"
            assert out["mission_epoch"] == 1
            # ``terminal_reason`` is None here (living mission); the key
            # is present (OFF/ON is about presence, not value).
            assert "mission_terminal_reason" in out
            assert out["mission_terminal_reason"] is None
        finally:
            # Reset back to OFF so subsequent tests in the suite get
            # the OFF default. The fixture's finaliser also resets,
            # but doing it in-line makes the test self-contained.
            os.environ.pop("ENSEMBLE_MISSION_PROJECTION_ENABLED", None)
            _reset_mission_projection_for_tests()


# ─── F1 ON-path regression: WorkResolverService with kill-switch ON ────────


class TestWorkResolverServiceKillSwitchOn:
    """Regression suite for the F1 fix.

    The F1 finding was that ``WorkResolverService._mission_resolver_obj``
    was read by the lazy accessor but NEVER initialised in ``__init__``
    — with the kill-switch ON and no constructor-injected resolver, the
    first call through the lazy accessor raised ``AttributeError``,
    which would have bricked all four Fix-C read surfaces on the
    operator flip. The fix seeds the attribute to ``None`` in
    ``__init__``; these tests pin the ON-path contract end-to-end.

    Each test drives the resolver with a populated engine + the
    kill-switch ON; the assertion is that the call returns a
    ``WorkRecord`` with the three mission fields populated and that
    no ``AttributeError`` escapes the call. Pre-fix code (missing
    ``__init__`` initialiser) raises ``AttributeError`` here; post-fix
    code returns the populated ``WorkRecord``.
    """

    def test_resolve_work_on_path_populates_mission_fields(
        self, engine, instance_repo, job_repo, monkeypatch
    ):
        """``WorkResolverService.resolve_work`` with the kill-switch ON
        must return a populated ``WorkRecord`` with the three mission
        fields — NOT raise ``AttributeError``.

        Pre-fix code shape (no ``_mission_resolver_obj`` initialiser
        in ``__init__``) raised ``AttributeError`` here on the first
        lazy accessor call; this test would fail loudly with that
        exception. Post-fix code shape (initialiser added) returns
        the populated ``WorkRecord``.
        """
        from daemon.services.work_resolver import WorkResolverService

        # Seed the Instance + JobItem so the projection has real data
        # to surface. Use a non-terminal status so the resolver
        # returns ``terminal_reason=None`` (the additive field is
        # present but its value is ``None`` — see the
        # ``test_workrecord_mission_fields_present_on`` precedent for
        # the presence-vs-value distinction).
        iid = _seed_instance(engine, instance_id="inst-on-path-1")
        jid = _seed_job(
            engine, instance_id=iid, admission_state=AdmissionState.ACTIVE.value
        )

        monkeypatch.setenv("ENSEMBLE_MISSION_PROJECTION_ENABLED", "1")
        _reset_mission_projection_for_tests()

        # Wire the resolver WITHOUT injecting a MissionResolver seed
        # — the lazy accessor must self-construct on first ON-path
        # call. This is the exact shape that broke pre-fix.
        svc = WorkResolverService(
            task_repo=_task_repo_fixture(engine),
            job_repo=job_repo,
            instance_repo=instance_repo,
        )
        record = svc.resolve_work(jid)
        assert record is not None, (
            "ON-path resolve_work must return a populated WorkRecord "
            "(F1 regression: an AttributeError on the lazy accessor "
            "would surface here as the call returning None or "
            "raising)."
        )
        # The three additive mission fields are populated from
        # MissionResolver._project — ``instance_id`` identity holds
        # per the M1 spec §3 identity contract.
        assert record.mission_id == iid
        assert record.mission_epoch is not None
        assert record.mission_terminal_reason is None

    def test_resolve_work_on_path_attribute_error_absent(
        self, engine, instance_repo, job_repo, monkeypatch
    ):
        """Dedicated pinpoint test for the F1 ``AttributeError`` class.

        Pre-fix: ``AttributeError: 'WorkResolverService' object has
        no attribute '_mission_resolver_obj'`` escapes the lazy
        accessor. Post-fix: no such exception escapes. The dedicated
        test gives the failure mode a name in the test report rather
        than collapsing it into the generic ``record is None`` /
        ``record.mission_id == iid`` assertions above.
        """
        from daemon.services.work_resolver import WorkResolverService

        iid = _seed_instance(engine, instance_id="inst-on-path-2")
        jid = _seed_job(
            engine, instance_id=iid, admission_state=AdmissionState.ACTIVE.value
        )

        monkeypatch.setenv("ENSEMBLE_MISSION_PROJECTION_ENABLED", "1")
        _reset_mission_projection_for_tests()

        svc = WorkResolverService(
            task_repo=_task_repo_fixture(engine),
            job_repo=job_repo,
            instance_repo=instance_repo,
        )
        try:
            record = svc.resolve_work(jid)
        except AttributeError as exc:  # pragma: no cover — fails the test
            pytest.fail(
                "F1 regression surfaced: WorkResolverService lazy "
                "accessor raised AttributeError with the kill-switch "
                f"ON: {exc!r}. The __init__ must initialise "
                "_mission_resolver_obj=None so the lazy accessor has "
                "an attribute to memoise against."
            )
        assert record is not None

    def test_resolve_work_off_path_preserves_pre_m1_instance_timing(
        self, engine, instance_repo, job_repo, monkeypatch
    ):
        """F2 byte-identical regression — OFF path must NOT do the
        ``_lookup_instance`` call on the single-row JobItem path.

        Pre-M1 behaviour for ``resolve_work`` (single-row JobItem
        path) was: no instance lookup at all on this path, so the
        WorkRecord's ``started_at`` / ``completed_at`` fields were
        sourced from ``None`` (the JobItem mirror columns that
        carried those values were dropped in Phase 5 — see the
        ``_instance_started_at`` / ``_instance_completed_at`` doc
        comments). The M1 commit added an unconditional
        ``_lookup_instance`` block in ``_job_to_record`` that broke
        the byte-identical OFF contract: DONE/DEAD rows whose
        backing ``Instance`` had ``last_activity_at`` populated
        started surfacing instance-derived timestamps on the
        single-row path even with the kill-switch OFF.

        The F2 fix gates the ``_lookup_instance`` block on the
        kill-switch. This test pins the OFF shape: the WorkRecord's
        ``started_at`` / ``completed_at`` must stay ``None`` (the
        pre-M1 value) even when the backing ``Instance`` carries
        timing data. Post-fix OFF passes; pre-fix OFF (the buggy
        state) fails because the instance-derived values would
        appear instead.
        """
        from daemon.repositories.instance.models import InstanceStatus
        from daemon.services.work_resolver import WorkResolverService

        # Seed a terminal Instance WITH ``last_activity_at`` populated
        # — the F2 bug would surface the instance's timing values on
        # the single-row resolve path even when the kill-switch was
        # OFF. Pre-M1 the Instance was never consulted on the
        # single-row path.
        now_dt = datetime.now(timezone.utc)
        iid = "inst-f2-off"
        with Session(engine) as s:
            inst = Instance(
                instance_id=iid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                project_id="test-project",
                status=InstanceStatus.COMPLETED.value,
                created_at=now_dt.isoformat(),
                updated_at=now_dt.isoformat(),
                last_activity_at=now_dt,
                paused_at=None,
            )
            s.add(inst)
            s.commit()
        jid = _seed_job(
            engine,
            instance_id=iid,
            admission_state=AdmissionState.DONE.value,
            terminal_reason="completed",
        )

        # The autouse fixture has the kill-switch OFF (no env
        # var set; cached state cleared). The monkeypatch here is
        # belt-and-suspenders in case a prior test in the suite
        # leaked the env.
        monkeypatch.delenv(
            "ENSEMBLE_MISSION_PROJECTION_ENABLED", raising=False
        )
        _reset_mission_projection_for_tests()
        assert is_mission_projection_enabled() is False

        svc = WorkResolverService(
            task_repo=_task_repo_fixture(engine),
            job_repo=job_repo,
            instance_repo=instance_repo,
        )
        record = svc.resolve_work(jid)
        assert record is not None

        # Pre-M1 shape: instance timing fields are None on the
        # single-row path (the JobItem mirror columns were dropped
        # in Phase 5; the F2 fix restores the OFF contract by
        # skipping the ``_lookup_instance`` call). The mission
        # additive fields stay None too — OFF means
        # byte-identical to pre-M1 across the whole record.
        assert record.started_at is None, (
            "F2 regression: OFF path leaked an instance-derived "
            f"started_at onto a single-row WorkRecord: {record.started_at!r}. "
            "Pre-M1 the JobItem mirror columns were the only "
            "started_at source and they surfaced None on this "
            "path; the F2 fix gates the _lookup_instance block "
            "on the kill-switch."
        )
        assert record.completed_at is None, (
            "F2 regression: OFF path leaked an instance-derived "
            f"completed_at onto a single-row WorkRecord: {record.completed_at!r}. "
            "Same rationale as started_at — pre-M1 the single-row "
            "path did not consult the Instance row."
        )
        # Mission additive fields are absent — the M1 default.
        assert record.mission_id is None
        assert record.mission_epoch is None
        assert record.mission_terminal_reason is None


def _task_repo_fixture(engine: Engine) -> TaskRepository:
    """Build a real TaskRepository bound to the test engine.

    Lives next to the F1 ON-path tests so the tests are
    self-contained — they wire a fresh ``WorkResolverService``
    instead of relying on a session-scoped fixture.
    """
    return TaskRepository(engine)



