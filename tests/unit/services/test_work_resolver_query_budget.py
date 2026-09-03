"""Engine-bound query-budget pins for the ``list_work`` jobs-list path.

The S4 fix (mission-class WS3, 2026-09-02): the jobs-list path —
``GET /api/jobs`` → ``JobQueueService.list_work`` →
``WorkResolverService.list_work`` → the ``_query_jobs`` no-LIMIT
fetch — used to issue ONE JobItem SELECT PER ROW through
``_job_to_record`` → ``_mission_fields_for_instance`` →
``MissionResolver._batch_jobitem_lookup([instance_id])``. The fix
hoists the mission-fields resolution out of the row loop:
``list_work`` calls ``_batch_mission_fields`` once per page (a single
batched ``IN``-clause JobItem SELECT) and hands each row its
pre-computed tuple via the ``mission_fields=`` parameter.

What is pinned here (engine-bound — a REAL ``before_cursor_execute``
listener counts actual SELECTs during the call; mock counting is
banned for this contract, same pattern as
``tests/unit/routers/test_missions_api.py::TestEngineBoundQueryCount``
and ``tests/unit/services/test_mission_resolver.py::TestBatchQueryCount``):

* **O(1) per page** — the total SELECT count for a ``list_work``
  call is FLAT as the page size doubles (5 → 10 → 20 rows). Any
  growth with page size is the N+1 regression.
* **Per-leg budget** — exactly 2 JobItem SELECTs (``_query_jobs`` +
  the batched ``_batch_jobitem_lookup``) and 2 Instance SELECTs
  (``_batch_instances`` + ``_batch_child_instance_ids``) for the
  ``root_only=True`` default; the JobItem count drops to 1 when the
  child filter is skipped (``root_only=False``).
* **Correctness under batching** — the batched path returns the same
  mission values the per-row path would (W4 hazard included), so the
  optimisation is not read-model-drifting.

Harness: file-backed SQLite at ``tmp_path`` with ``NullPool`` + WAL +
``busy_timeout`` (the Testing & QC conventions recipe; the
QUARANTINE.md ``StaticPool + WriteGuardSession`` trap is not used).
Real repositories wired into the real ``WorkResolverService`` — the
SQL level is genuinely exercised. One engine is shared by both
repositories (the resolver's own recommendation: sharing the engine
avoids SQLite WAL lock contention), so the listener counts SELECTs
across both tables on that one engine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401  (transitive dep)

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.repository import TaskRepository
from daemon.services.work_resolver import WorkResolverService


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (the Testing & QC recipe)."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/query_budget.db",
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )
    with eng.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA busy_timeout=10000")
        conn.commit()
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def resolver(engine: Engine) -> WorkResolverService:
    """Real WorkResolverService over real repos (lazy MissionResolver).

    No ``mission_resolver=`` seed — the lazy accessor must
    self-construct, which is also the shape older standalone wiring
    uses. Always-on since WS3: no env discipline needed.
    """
    return WorkResolverService(
        task_repo=TaskRepository(engine),
        job_repo=JobRepository(engine),
        instance_repo=SQLModelInstanceRepository(engine),
    )


# ─── Seed helpers ───────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    status: str = "running",
) -> str:
    """Insert one Instance row (mirrors the mission-suite seeders)."""
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                project_id="query-budget-project",
                status=status,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
                last_activity_at=now,
                paused_at=None,
            )
        )
        s.commit()
    return instance_id


def _seed_job(
    engine: Engine,
    *,
    instance_id: str,
    admission_state: str = AdmissionState.ACTIVE.value,
    terminal_reason: str | None = None,
    job_type: str = "message",
) -> str:
    """Insert one JobItem row linked to ``instance_id``."""
    jid = f"job-{instance_id}"
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            JobItem(
                job_id=jid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="query-budget test job",
                source="api",
                project_id="query-budget-project",
                priority=5,
                queue_id="budget-queue",
                instance_id=instance_id,
                admission_state=admission_state,
                terminal_reason=terminal_reason,
                job_metadata={},
                created_at=now,
                updated_at=now,
                job_type=job_type,
            )
        )
        s.commit()
    return jid


def _seed_page(engine: Engine, *, n: int, base: int = 0) -> list[str]:
    """Seed ``n`` distinct instance+job rows; return the job ids.

    ``base`` offsets the id namespace so successive calls in one test
    (page-doubling pins) never collide on the Instance PK.
    """
    jids: list[str] = []
    for i in range(base, base + n):
        iid = _seed_instance(engine, instance_id=f"inst-{i}")
        jids.append(_seed_job(engine, instance_id=iid))
    return jids


# ─── Engine-bound SELECT spy (the M1-gate pattern) ─────────────────────────


class _SelectSpy:
    """Count real SELECTs per table via a ``before_cursor_execute`` hook."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self.counts: dict[str, int] = {
            "instances": 0,
            "job_queue_items": 0,
            "total": 0,
        }

    def __enter__(self) -> "_SelectSpy":
        def _before_cursor_execute(  # noqa: ANN001 — SQLAlchemy hook
            conn, cursor, statement, parameters, context, executemany
        ) -> None:  # noqa: ANN001
            s = statement.strip().upper()
            if s.startswith("SELECT"):
                self.counts["total"] += 1
                if "FROM INSTANCES" in s:
                    self.counts["instances"] += 1
                if "JOB_QUEUE_ITEMS" in s:
                    self.counts["job_queue_items"] += 1

        self._hook = _before_cursor_execute
        event.listen(self._engine, "before_cursor_execute", self._hook)
        return self

    def __exit__(self, *exc: Any) -> None:
        event.remove(self._engine, "before_cursor_execute", self._hook)


# ─── The budget pins ────────────────────────────────────────────────────────


class TestListWorkJobsQueryBudget:
    """The jobs-list path issues O(1) SELECTs per page — not O(rows)."""

    def test_select_count_flat_as_page_doubles(
        self, engine: Engine, resolver: WorkResolverService
    ):
        """5-row page and 10-row page cost the SAME number of SELECTs.

        The S4 regression this pins: pre-fix, each row ran
        ``_mission_fields_for_instance`` → one JobItem SELECT per
        row, so total SELECTs grew with the page (5 rows ⇒ +5).
        Post-fix the mission-fields leg is one batched SELECT per
        page.
        """
        totals: list[int] = []
        base = 0
        for n in (5, 10):
            _seed_page(engine, n=n, base=base)
            base += n
            with _SelectSpy(engine) as spy:
                records = resolver.list_work(kind="job")
            assert len(records) >= n, (
                f"expected at least {n} rows on the page; got "
                f"{len(records)}"
            )
            totals.append(spy.counts["total"])

        assert totals[0] == totals[1], (
            f"SELECT count must be FLAT as the page doubles "
            f"(n=5 → {totals[0]}, n=10 → {totals[1]}); growth with "
            f"page size is the S4 N+1 regression "
            f"(per-row JobItem SELECT in _job_to_record)."
        )

    def test_page_of_twenty_costs_same_as_page_of_five(
        self, engine: Engine, resolver: WorkResolverService
    ):
        """The bound holds at 20 rows — O(1), not O(rows)."""
        _seed_page(engine, n=5)
        with _SelectSpy(engine) as small:
            resolver.list_work(kind="job")

        _seed_page(engine, n=20, base=100)
        with _SelectSpy(engine) as big:
            resolver.list_work(kind="job")

        assert big.counts["total"] == small.counts["total"], (
            f"20-row page ({big.counts}) must cost the same as a "
            f"5-row page ({small.counts}); a per-row leg grew with "
            f"the page."
        )

    def test_per_leg_budget_root_only_default(
        self, engine: Engine, resolver: WorkResolverService
    ):
        """Default ``root_only=True`` shape: 2 JobItem SELECTs
        (``_query_jobs`` + batched mission-fields lookup) and 2
        Instance SELECTs (``_batch_instances`` +
        ``_batch_child_instance_ids``) — regardless of row count.
        """
        _seed_page(engine, n=6)
        with _SelectSpy(engine) as spy:
            records = resolver.list_work(kind="job")

        assert len(records) == 6
        assert spy.counts["job_queue_items"] == 2, (
            f"JobItem leg must be exactly 2 SELECTs (page query + ONE "
            f"batched mission-fields IN-clause); got "
            f"{spy.counts['job_queue_items']}. More than 2 is the S4 "
            f"per-row regression."
        )
        assert spy.counts["instances"] == 2, (
            f"Instance leg must be exactly 2 SELECTs "
            f"(_batch_instances + _batch_child_instance_ids); got "
            f"{spy.counts['instances']}."
        )
        assert spy.counts["total"] == 4

    def test_per_leg_budget_root_only_false(
        self, engine: Engine, resolver: WorkResolverService
    ):
        """``root_only=False`` skips the child filter: 1 JobItem SELECT
        for the page + 1 for the batched mission fields, 1 Instance
        SELECT.
        """
        _seed_page(engine, n=6)
        with _SelectSpy(engine) as spy:
            records = resolver.list_work(kind="job", root_only=False)

        assert len(records) == 6
        assert spy.counts["job_queue_items"] == 2, (
            f"JobItem leg must stay 2 (page query + batched mission "
            f"fields); got {spy.counts['job_queue_items']}."
        )
        assert spy.counts["instances"] == 1
        assert spy.counts["total"] == 3

    def test_batched_mission_fields_match_per_row_resolution(
        self, engine: Engine, resolver: WorkResolverService
    ):
        """Correctness under batching: the batched pre-computed tuples
        equal the per-row resolution — W4 hazard included.

        Seeds one DONE+dead_letter JobItem (the W4 shape: DEAD
        admission flips ``mission_terminal_reason`` to
        ``dead_letter`` regardless of the since-revived instance)
        among healthy rows and asserts the batched list path
        surfaces the identical mission triple the single-row path
        resolves for the same row.
        """
        # Healthy rows (mirror + mission both live).
        _seed_page(engine, n=3)

        # W4 row: DEAD JobItem + a live (revived) instance.
        iid_w4 = _seed_instance(
            engine, instance_id="inst-w4", status="running"
        )
        jid_w4 = _seed_job(
            engine,
            instance_id=iid_w4,
            admission_state=AdmissionState.DEAD.value,
            terminal_reason="dead_letter",
        )

        records = resolver.list_work(kind="job")
        by_id = {r.work_id: r for r in records}

        # Batched-path values for the W4 row.
        batched = by_id[jid_w4]
        assert batched.mission_id == iid_w4
        assert batched.mission_epoch == 1
        assert batched.mission_terminal_reason == "dead_letter", (
            "W4 hazard must survive the batched mission-fields path: "
            "a DEAD linked JobItem surfaces 'dead_letter' regardless "
            "of the live instance."
        )
        assert batched.status == "dead_letter"

        # Cross-check against the single-row resolution (per-row path).
        single = resolver.resolve_work(jid_w4)
        assert single is not None
        assert (
            single.mission_id,
            single.mission_epoch,
            single.mission_terminal_reason,
        ) == (
            batched.mission_id,
            batched.mission_epoch,
            batched.mission_terminal_reason,
        ), (
            "batched mission fields must equal the per-row resolution "
            "for the same row (the S4 batching must not drift the "
            "read model)."
        )

        # Every healthy row carries populated mission identity too.
        for i in range(3):
            row = by_id[f"job-inst-{i}"]
            assert row.mission_id == f"inst-{i}"
            assert row.mission_epoch == 1

    def test_empty_page_issues_no_batched_mission_select(
        self, engine: Engine, resolver: WorkResolverService
    ):
        """Empty page short-circuits: the mission-fields batch never
        runs (the ``if jobs:`` guard), so the JobItem leg is exactly
        the page query.
        """
        with _SelectSpy(engine) as spy:
            records = resolver.list_work(kind="job")

        assert records == []
        assert spy.counts["job_queue_items"] == 1, (
            "empty page must issue exactly the page query and skip "
            f"the mission-fields batch; got {spy.counts}"
        )
        assert spy.counts["instances"] == 0
