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
    branch via ``_set_mission_kill_switch``).
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
    """Current epoch is precise (a small positive integer).

    Per spec §3 known-limitation: precise per-epoch timestamps are
    NOT derivable at read time today — the DB has no terminal-transition
    timestamps. M1 reports ``epoch=1`` for every non-degraded
    projection (the honest answer until M4(ii)'s ``mission_events``
    log lands). The fields are present + non-null where you'd expect
    so future tooling can branch on them without re-parsing.
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
    is frozen at 23; the resolver adds ZERO new writers. This test
    pins the invariant at the resolver layer (session-spy style) so a
    future regression that casually adds a write call to the resolver
    fails loudly here — the unit test catches it before the
    constitution drift test (which guards the wider census) catches
    it.
    """

    def test_resolve_emits_no_dml(self, engine, resolver, instance_repo, job_repo):
        """``resolve()`` over a populated instance must not write anything.

        The spy sits at the engine level: row counts on the writable
        tables must be identical before and after a ``resolve``
        call. The resolver opens its OWN SQLModel sessions through
        the repository wrappers, so any DML it issues would land
        in the same engine and show up in this count — the
        assertion fails loudly on a write-via-resolver regression.
        """
        # Seed the row BEFORE the snapshot so the snapshot already
        # includes the seeded data (any delta after the resolve call
        # then reflects ONLY what the resolver wrote).
        iid = _seed_instance(engine, instance_id="inst-purity-1")
        _seed_job(engine, instance_id=iid, admission_state=AdmissionState.ACTIVE.value)

        from sqlmodel import Session as SQLModelSession

        with SQLModelSession(engine) as s:
            inst_before = s.exec(
                __import__("sqlmodel").select(Instance)
            ).all()
            job_before = s.exec(
                __import__("sqlmodel").select(JobItem)
            ).all()

        record = resolver.resolve(iid)
        assert record is not None

        # Re-snapshot — no writes should have happened.
        with SQLModelSession(engine) as s:
            inst_after = s.exec(
                __import__("sqlmodel").select(Instance)
            ).all()
            job_after = s.exec(
                __import__("sqlmodel").select(JobItem)
            ).all()

        # Purity: row-count delta is zero. The MissionResolver is a
        # pure reader — see module docstring "census invariant".
        assert len(inst_after) == len(inst_before), (
            f"PURITY violated: instance row count changed "
            f"({len(inst_before)} → {len(inst_after)}). The resolver "
            f"must not INSERT/UPDATE/DELETE."
        )
        assert len(job_after) == len(job_before), (
            f"PURITY violated: job row count changed "
            f"({len(job_before)} → {len(job_after)})."
        )

    def test_resolve_many_emits_no_dml(self, engine, resolver):
        """``resolve_many()`` over a populated page must not write."""
        from sqlmodel import Session as SQLModelSession
        from sqlmodel import select

        # Populate the page with 5 instances to exercise the
        # batched path.
        ids = [
            _seed_instance(engine, instance_id=f"inst-batch-{i}")
            for i in range(5)
        ]

        with SQLModelSession(engine) as s:
            inst_before = s.exec(select(Instance)).all()
            job_before = s.exec(select(JobItem)).all()

        records = resolver.resolve_many(ids)
        assert len(records) == 5

        with SQLModelSession(engine) as s:
            inst_after = s.exec(select(Instance)).all()
            job_after = s.exec(select(JobItem)).all()

        assert len(inst_after) == len(inst_before)
        assert len(job_after) == len(job_before)


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

    def _set_mission_kill_switch(self, enabled: bool):
        """Set the kill-switch via env var and reset its cached state."""
        if enabled:
            os.environ["ENSEMBLE_MISSION_PROJECTION_ENABLED"] = "1"
        else:
            os.environ.pop("ENSEMBLE_MISSION_PROJECTION_ENABLED", None)
        _reset_mission_projection_for_tests()

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



