"""LCA idle-orphan two-set live semantics under REAL PostgreSQL.

Job 4 LCA merge gate — dialect canary for ``d950d2c8`` (incident b08f40fe
amendment: ``fix(lca): idle-orphan descendants are not live``).

This module is a PG-MODE test file. It exercises the two new
repository queries and the two-set live facade introduced by d950d2c8
against REAL PostgreSQL (port 15441 disposable cluster, DB
``ensemble_lca_final``), proving the semantics are dialect-portable.

What we cover (per the task spec):

1. ``SQLModelMessageQueueRepository.get_unprocessed_for_instances`` — the
   message-lane half of the conditional-live predicate. New in d950d2c8.
   PG must return identical semantics to SQLite: PENDING / READY /
   PROCESSING / RETRYING rows are returned; COMPLETED / FAILED rows are
   excluded; empty ``instance_ids`` short-circuits to ``[]``.
2. ``JobRepository.get_active_by_instance`` — the job-lane half.
   Pre-existing query but a mandatory second check (an IDLE instance
   with a queued job has NO message_queue row yet). PG must filter on
   ``admission_state IN ('queued', 'active')`` correctly.
3. ``_dormant_descendants_with_work_en_route`` — the module-level helper
   wiring the two lanes. End-to-end with PG-backed repositories: each
   lane shape (msg-only, job-only, both, neither) is pinned.
4. ``InstanceManager.count_live_descendants`` — the production facade
   driven through a stub manager with PG-backed repos. IDLE-orphan
   descendants (no message, no job) MUST NOT count live (incident
   b08f40fe regression at the count level); IDLE + unprocessed message
   / active job MUST count live (conditional-live arm).

Run with::

    unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER \\
          POSTGRES_PASSWORD POSTGRES_URL PG_TEST_HOST PG_TEST_PORT \\
          PG_TEST_DB PG_TEST_USER PG_TEST_PASSWORD ENSEMBLE_TEST_PG_URL
    PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=15441 \\
    PG_TEST_DB=ensemble_lca_final PG_TEST_USER=$USER \\
    PG_TEST_PASSWORD=trust \\
    timeout 300 uv run python -m pytest \\
      tests/postgres/test_attestation_live_descendants_pg_lca.py -q \\
      --tb=short --override-ini="addopts=" -m postgres
"""

from __future__ import annotations

import os
from types import MethodType, SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel

# Import EVERY model module so SQLModel.metadata registers every table
# before ``create_all`` is called (the conftest fixture owns the schema,
# but importing here is needed for this module's local engine to see
# the tables).
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401

from daemon.manager import (
    InstanceManager,
    _dormant_descendants_with_work_en_route,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import (
    ACTIVE_ADMISSION_STATES,
    AdmissionState,
    JobItem,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.message_queue.models import MessageQueue, MessageStatus
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)


pytestmark = pytest.mark.postgres


# ── PG engine (module-scoped, mirrors tests/postgres/test_premature_*_pg.py) ──


def _pg_engine() -> Engine:
    """Build a PG engine honoring PG_TEST_* env vars (per conftest contract)."""
    pg_host = os.environ.get("PG_TEST_HOST", "localhost")
    pg_port = int(os.environ.get("PG_TEST_PORT", "5432"))
    pg_db = os.environ.get("PG_TEST_DB", "ensemble_test")
    pg_user = os.environ.get("PG_TEST_USER", "ensemble")
    pg_password = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
    url = f"postgresql+psycopg://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_db}"
    return create_engine(url, pool_pre_ping=True, future=True)


@pytest.fixture(scope="module")
def pg_engine() -> Engine:
    """Module-scoped PG engine. create_all on entry; dispose on teardown.

    Intentionally omits ``drop_all`` on teardown (same defensive
    rationale as ``test_premature_completion_regression.py``) — sibling
    PG tests in this run share the same database and would observe
    undefined tables after a drop.
    """
    engine = _pg_engine()
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _truncate_pg_tables(pg_engine):
    """Per-test TRUNCATE so tests are order-independent.

    The shared session-scoped ``pg_engine`` in tests/postgres/conftest.py
    owns the autouse ``_pg_truncate_tables`` fixture — but this module
    uses a LOCAL module-scoped engine so TRUNCATE is scoped to this
    module's tables only (defense-in-depth: never accidentally truncate
    a sibling module's rows).
    """
    tables = [t.name for t in reversed(SQLModel.metadata.sorted_tables)]
    if not tables:
        yield
        return
    from sqlalchemy import text

    with pg_engine.begin() as conn:
        joined = ", ".join(f'"{name}"' for name in tables)
        conn.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
    yield


# ── Seeding helpers (direct inserts — no daemon involved) ───────────────


def _seed_instance(engine: Engine, instance_id: str, parent_id: str | None,
                    status: str, agent_id: str = "test") -> None:
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id=agent_id,
                agent_dir="./agents/test",
                parent_id=parent_id,
                status=status,
            )
        )
        session.commit()


def _seed_message(engine: Engine, instance_id: str, status: str) -> None:
    with Session(engine) as session:
        session.add(
            MessageQueue(
                instance_id=instance_id,
                content="PG-mode work en route (test)",
                source="test",
                status=status,
            )
        )
        session.commit()


def _seed_job(engine: Engine, instance_id: str,
              admission_state: str, agent_id: str = "test") -> None:
    with Session(engine) as session:
        session.add(
            JobItem(
                agent_id=agent_id,
                agent_dir="./agents/test",
                message="PG-mode queued work (test)",
                instance_id=instance_id,
                admission_state=admission_state,
            )
        )
        session.commit()


# ── Stub manager (mirrors tests/integration/_StubManager shape) ─────────


class _PGStubManager:
    """Bare class used to build a facade-testing stub via ``object.__new__``.

    Production wiring reads ``_instance_repository`` /
    ``LIVE_DESCENDANTS_BFS_CAP`` / ``_queue_repository`` /
    ``_job_queue_service`` via ``getattr(..., None)``. We attach the
    requested attributes manually via ``object.__new__`` (bypassing
    ``__init__``); unwired lanes simply contribute no work-en-route
    signal.
    """
    pass


def _build_pg_manager(
    pg_engine: Engine,
    *,
    with_message_lane: bool = False,
    with_job_lane: bool = False,
) -> _PGStubManager:
    """Build a stub InstanceManager-like object bound to the PG engine."""
    repo = SQLModelInstanceRepository(pg_engine)
    manager = object.__new__(_PGStubManager)
    manager._instance_repository = repo
    if with_message_lane:
        manager._queue_repository = SQLModelMessageQueueRepository(pg_engine)
    if with_job_lane:
        manager._job_queue_service = SimpleNamespace(
            _repository=JobRepository(pg_engine)
        )
    manager.LIVE_DESCENDANTS_BFS_CAP = InstanceManager.LIVE_DESCENDANTS_BFS_CAP
    manager.count_live_descendants = MethodType(
        InstanceManager.count_live_descendants, manager
    )
    return manager


# ── 1. get_unprocessed_for_instances (message lane — NEW) ──────────────


class TestPGGetUnprocessedForInstances:
    """Dialect canary for the new message-lane query (d950d2c8)."""

    def test_returns_pending_ready_processing_retrying(self, pg_engine):
        """All four not-yet-processed statuses are returned on PG."""
        for status in (
            MessageStatus.PENDING.value,
            MessageStatus.READY.value,
            MessageStatus.PROCESSING.value,
            MessageStatus.RETRYING.value,
        ):
            _seed_message(pg_engine, "pg-msg-1", status)
        repo = SQLModelMessageQueueRepository(pg_engine)
        pairs = repo.get_unprocessed_for_instances(["pg-msg-1"])
        returned = {row[1] for row in pairs}
        # 4 rows seeded → 4 distinct message_ids returned (factory assigns UUIDs).
        assert len(returned) == 4
        assert len(pairs) == 4
        for _iid, mid in pairs:
            assert mid  # non-empty UUID string

    def test_excludes_completed_and_failed(self, pg_engine):
        """Terminal message statuses must NOT be returned."""
        _seed_message(pg_engine, "pg-msg-1", MessageStatus.COMPLETED.value)
        _seed_message(pg_engine, "pg-msg-1", MessageStatus.FAILED.value)
        # Add one unprocessed so we can prove the filter excludes ONLY terminals.
        _seed_message(pg_engine, "pg-msg-1", MessageStatus.READY.value)
        repo = SQLModelMessageQueueRepository(pg_engine)
        pairs = repo.get_unprocessed_for_instances(["pg-msg-1"])
        assert len(pairs) == 1, (
            f"expected 1 (only READY), got {len(pairs)} — terminal filter failed on PG"
        )

    def test_empty_instance_ids_short_circuits(self, pg_engine):
        """``get_unprocessed_for_instances([])`` must return ``[]`` on PG."""
        # No seed: any row returned would mean the IN-clause path didn't short-circuit.
        repo = SQLModelMessageQueueRepository(pg_engine)
        assert repo.get_unprocessed_for_instances([]) == []

    def test_filters_to_requested_instance_ids(self, pg_engine):
        """Only rows targeting the requested instances are returned."""
        _seed_message(pg_engine, "pg-msg-A", MessageStatus.PENDING.value)
        _seed_message(pg_engine, "pg-msg-A", MessageStatus.READY.value)
        _seed_message(pg_engine, "pg-msg-B", MessageStatus.PENDING.value)
        _seed_message(pg_engine, "pg-msg-C", MessageStatus.PROCESSING.value)
        repo = SQLModelMessageQueueRepository(pg_engine)
        only_A = repo.get_unprocessed_for_instances(["pg-msg-A"])
        assert len(only_A) == 2
        assert {iid for iid, _mid in only_A} == {"pg-msg-A"}
        A_and_C = repo.get_unprocessed_for_instances(["pg-msg-A", "pg-msg-C"])
        assert len(A_and_C) == 3
        assert {iid for iid, _mid in A_and_C} == {"pg-msg-A", "pg-msg-C"}

    def test_pg_dialect_no_errors(self, pg_engine):
        """The query must execute cleanly on PG (no dialect-specific SQL errors).

        If PG rejects the IN-list-of-strings or the .in_() expansion, this
        surfaces the error. The previous SQLite-only verification did NOT
        exercise PG — this is the dialect canary's whole point.
        """
        repo = SQLModelMessageQueueRepository(pg_engine)
        # Many instance IDs to stress the IN-clause expansion on PG.
        ids = [f"pg-stress-{i}" for i in range(50)]
        for iid in ids[:5]:
            _seed_message(pg_engine, iid, MessageStatus.PENDING.value)
        # 50 IDs requested, only 5 seeded → 5 returned.
        pairs = repo.get_unprocessed_for_instances(ids)
        assert len(pairs) == 5


# ── 2. get_active_by_instance (job lane — pre-existing) ────────────────


class TestPGGetActiveByInstance:
    """The job-lane half: PG must filter on admission_state correctly."""

    def test_returns_queued_and_active(self, pg_engine):
        """Both in-flight admission states (ACTIVE_ADMISSION_STATES) are returned."""
        _seed_job(pg_engine, "pg-job-1", AdmissionState.QUEUED.value)
        _seed_job(pg_engine, "pg-job-1", AdmissionState.ACTIVE.value)
        repo = JobRepository(pg_engine)
        # ACTIVE_ADMISSION_STATES is the canonical predicate this query uses.
        assert AdmissionState.QUEUED.value in ACTIVE_ADMISSION_STATES
        assert AdmissionState.ACTIVE.value in ACTIVE_ADMISSION_STATES
        job = repo.get_active_by_instance("pg-job-1")
        # Freshest-by-created_at; either is acceptable (both seeded back-to-back).
        assert job is not None
        assert job.instance_id == "pg-job-1"
        assert job.admission_state in ACTIVE_ADMISSION_STATES

    def test_excludes_done_and_dead(self, pg_engine):
        """DONE and DEAD rows are settled — must NOT be returned."""
        _seed_job(pg_engine, "pg-job-1", AdmissionState.DONE.value)
        _seed_job(pg_engine, "pg-job-1", AdmissionState.DEAD.value)
        repo = JobRepository(pg_engine)
        assert repo.get_active_by_instance("pg-job-1") is None

    def test_returns_none_for_unknown_instance(self, pg_engine):
        """Unknown instance returns None — not an exception (PG NULL-safe)."""
        repo = JobRepository(pg_engine)
        assert repo.get_active_by_instance("does-not-exist") is None

    def test_pg_dialect_no_errors(self, pg_engine):
        """The query must execute cleanly on PG (admission_state IN clause)."""
        repo = JobRepository(pg_engine)
        # Force the IN-clause to materialize against a real table on PG.
        job = repo.get_active_by_instance("any-id")
        assert job is None  # no rows seeded → None, but the SQL ran without error


# ── 3. _dormant_descendants_with_work_en_route (helper) ───────────────


class TestPGDormantWorkEnRouteHelper:
    """Two-lane wiring tested end-to-end on PG."""

    def test_message_lane_only(self, pg_engine):
        """With only the message repo wired, message rows are detected."""
        _seed_instance(pg_engine, "pg-A", None, InstanceStatus.IDLE.value)
        _seed_instance(pg_engine, "pg-A-child", "pg-A", InstanceStatus.IDLE.value)
        _seed_message(pg_engine, "pg-A-child", MessageStatus.PENDING.value)
        msg_repo = SQLModelMessageQueueRepository(pg_engine)
        with_work = _dormant_descendants_with_work_en_route(
            msg_repo, None, ["pg-A-child", "pg-A-child-no-job"]
        )
        assert with_work == {"pg-A-child"}

    def test_job_lane_only(self, pg_engine):
        """With only the job repo wired, active jobs are detected."""
        _seed_instance(pg_engine, "pg-B", None, InstanceStatus.IDLE.value)
        _seed_instance(pg_engine, "pg-B-child", "pg-B", InstanceStatus.QUEUED.value)
        _seed_job(pg_engine, "pg-B-child", AdmissionState.QUEUED.value)
        job_repo = JobRepository(pg_engine)
        with_work = _dormant_descendants_with_work_en_route(
            None, job_repo, ["pg-B-child"]
        )
        assert with_work == {"pg-B-child"}

    def test_both_lanes_wired(self, pg_engine):
        """Both lanes contribute; the union is the work-bearing set."""
        _seed_instance(pg_engine, "pg-C", None, InstanceStatus.IDLE.value)
        _seed_instance(pg_engine, "pg-C-msg", "pg-C", InstanceStatus.IDLE.value)
        _seed_instance(pg_engine, "pg-C-job", "pg-C", InstanceStatus.IDLE.value)
        _seed_instance(pg_engine, "pg-C-orphan", "pg-C", InstanceStatus.IDLE.value)
        _seed_message(pg_engine, "pg-C-msg", MessageStatus.READY.value)
        _seed_job(pg_engine, "pg-C-job", AdmissionState.ACTIVE.value)
        msg_repo = SQLModelMessageQueueRepository(pg_engine)
        job_repo = JobRepository(pg_engine)
        with_work = _dormant_descendants_with_work_en_route(
            msg_repo, job_repo, ["pg-C-msg", "pg-C-job", "pg-C-orphan"]
        )
        assert with_work == {"pg-C-msg", "pg-C-job"}

    def test_no_lanes_wired_returns_empty(self, pg_engine):
        """Both lanes None → empty set (helper contract)."""
        with_work = _dormant_descendants_with_work_en_route(
            None, None, ["pg-D-1", "pg-D-2"]
        )
        assert with_work == set()


# ── 4. count_live_descendants (facade) under PG ────────────────────────


class TestPGCountLiveDescendantsFacade:
    """The production facade driven through a stub manager on PG."""

    def test_idle_orphan_without_message_or_job_is_not_live(self, pg_engine):
        """THE b08f40fe regression at the count level — on PG.

        IDLE-orphan descendants with NO message row and NO unsettled
        job are NOT live. This is the core two-set semantics: the old
        unconditional-live code wrongly counted these live, holding the
        leader's gate open via ``live_descendants=4`` for never-
        dispatched orphans. Must hold on PG as on SQLite.
        """
        _seed_instance(pg_engine, "pg-root", None, InstanceStatus.RUNNING.value)
        _seed_instance(pg_engine, "pg-tester", "pg-root", InstanceStatus.COMPLETED.value)
        for i in range(4):
            _seed_instance(
                pg_engine, f"pg-orphan-{i}", "pg-tester", InstanceStatus.IDLE.value
            )
        manager = _build_pg_manager(
            pg_engine, with_message_lane=True, with_job_lane=True
        )
        assert manager.count_live_descendants("pg-root") == 0, (
            "PG dialect: IDLE orphans MUST NOT count live — b08f40fe regression"
        )

    def test_idle_with_unprocessed_message_counts_live(self, pg_engine):
        """IDLE + unprocessed message → live (message lane, on PG)."""
        _seed_instance(pg_engine, "pg-root", None, InstanceStatus.IDLE.value)
        _seed_instance(pg_engine, "pg-child", "pg-root", InstanceStatus.IDLE.value)
        _seed_message(pg_engine, "pg-child", MessageStatus.PENDING.value)
        manager = _build_pg_manager(pg_engine, with_message_lane=True)
        assert manager.count_live_descendants("pg-root") == 1

    def test_idle_with_active_job_counts_live(self, pg_engine):
        """IDLE + active job (NO message row) → live (job lane, on PG).

        The job lane is a MANDATORY second check — an IDLE instance
        with only a queued job has no message_queue row yet.
        """
        _seed_instance(pg_engine, "pg-root", None, InstanceStatus.IDLE.value)
        _seed_instance(pg_engine, "pg-child", "pg-root", InstanceStatus.IDLE.value)
        _seed_job(pg_engine, "pg-child", AdmissionState.QUEUED.value)
        manager = _build_pg_manager(pg_engine, with_job_lane=True)
        assert manager.count_live_descendants("pg-root") == 1

    def test_queued_with_unprocessed_message_counts_live(self, pg_engine):
        """QUEUED (dormant) + unprocessed message → live (on PG)."""
        _seed_instance(pg_engine, "pg-root", None, InstanceStatus.IDLE.value)
        _seed_instance(pg_engine, "pg-child", "pg-root", InstanceStatus.QUEUED.value)
        _seed_message(pg_engine, "pg-child", MessageStatus.READY.value)
        manager = _build_pg_manager(pg_engine, with_message_lane=True)
        assert manager.count_live_descendants("pg-root") == 1

    def test_terminal_message_does_not_count_live(self, pg_engine):
        """COMPLETED message is processed — NOT work en route (on PG)."""
        _seed_instance(pg_engine, "pg-root", None, InstanceStatus.IDLE.value)
        _seed_instance(pg_engine, "pg-child", "pg-root", InstanceStatus.IDLE.value)
        _seed_message(pg_engine, "pg-child", MessageStatus.COMPLETED.value)
        manager = _build_pg_manager(pg_engine, with_message_lane=True)
        assert manager.count_live_descendants("pg-root") == 0

    def test_done_job_does_not_count_live(self, pg_engine):
        """DONE job is settled — NOT work en route (on PG)."""
        _seed_instance(pg_engine, "pg-root", None, InstanceStatus.IDLE.value)
        _seed_instance(pg_engine, "pg-child", "pg-root", InstanceStatus.IDLE.value)
        _seed_job(pg_engine, "pg-child", AdmissionState.DONE.value)
        manager = _build_pg_manager(pg_engine, with_job_lane=True)
        assert manager.count_live_descendants("pg-root") == 0

    def test_unconditional_live_set_counts_without_work_checks(self, pg_engine):
        """RUNNING / WAITING / WAITING_CHILDREN / PAUSED are UNCONDITIONAL.

        With NO message/job lanes wired, they still count live — exactly
        the unconditional-live semantics. Proves the PG path of the
        unconditional branch is identical to SQLite.
        """
        _seed_instance(pg_engine, "pg-root", None, InstanceStatus.IDLE.value)
        for status in (
            InstanceStatus.RUNNING.value,
            InstanceStatus.WAITING.value,
            InstanceStatus.WAITING_CHILDREN.value,
            InstanceStatus.PAUSED.value,
        ):
            _seed_instance(pg_engine, f"pg-c-{status}", "pg-root", status)
        manager = _build_pg_manager(pg_engine)  # no lanes wired
        assert manager.count_live_descendants("pg-root") == 4

    def test_mixed_dormant_tree_counts_only_work_bearing_on_pg(self, pg_engine):
        """Mixed tree: messaged IDLE + IDLE orphan + RUNNING → count is 2.

        One IDLE child WITH an unprocessed message + one IDLE orphan +
        one RUNNING child → count is exactly 2 (the messaged dormant
        one and the running one); the orphan contributes nothing.
        """
        _seed_instance(pg_engine, "pg-root", None, InstanceStatus.IDLE.value)
        _seed_instance(pg_engine, "pg-mix-idle-msg", "pg-root", InstanceStatus.IDLE.value)
        _seed_instance(pg_engine, "pg-mix-idle-orphan", "pg-root", InstanceStatus.IDLE.value)
        _seed_instance(pg_engine, "pg-mix-running", "pg-root", InstanceStatus.RUNNING.value)
        _seed_message(pg_engine, "pg-mix-idle-msg", MessageStatus.READY.value)
        manager = _build_pg_manager(
            pg_engine, with_message_lane=True, with_job_lane=True
        )
        assert manager.count_live_descendants("pg-root") == 2