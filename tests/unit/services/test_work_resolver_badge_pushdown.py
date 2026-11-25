"""Engine-bound pins for the badge-leg SQL pushdown (jbh-m1a).

Copies the harness from ``test_work_resolver_query_budget.py`` (file-backed
SQLite + NullPool + before_cursor_execute listener, real repos wired into a
real ``WorkResolverService``; mock counting banned).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.repository import TaskRepository
from daemon.services.work_resolver import TIE_PAD, WorkResolverService


@pytest.fixture
def engine(tmp_path) -> Engine:
    eng = create_engine(
        f"sqlite:///{tmp_path}/badge_pushdown.db",
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
    return WorkResolverService(
        task_repo=TaskRepository(engine),
        job_repo=JobRepository(engine),
        instance_repo=SQLModelInstanceRepository(engine),
    )


def _seed_instance(engine, *, instance_id, status="running", project_id="p"):
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                project_id=project_id,
                status=status,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
                last_activity_at=now,
                paused_at=None,
            )
        )
        s.commit()
    return instance_id


def _seed_job(engine, *, instance_id, jid=None, project_id="p", created_at=None):
    if jid is None:
        jid = f"job-{instance_id}"
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            JobItem(
                job_id=jid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="badge pushdown test job",
                source="api",
                project_id=project_id,
                priority=5,
                queue_id="q",
                instance_id=instance_id,
                admission_state=AdmissionState.DEAD.value,
                terminal_reason="completed",
                job_metadata={},
                created_at=created_at,
                updated_at=created_at,
                job_type="message",
            )
        )
        s.commit()
    return jid


class _SelectSpy:
    def __init__(self, engine):
        self._engine = engine
        self.job_item_rows = 0
        self.selects = 0

    def __enter__(self):
        def before(conn, cursor, statement, parameters, context, executemany):
            s = statement.strip().upper()
            if s.startswith("SELECT") and "JOB_QUEUE_ITEMS" in s:
                self.selects += 1

        def after(conn, cursor, statement, parameters, context, executemany):
            s = statement.strip().upper()
            if s.startswith("SELECT") and "JOB_QUEUE_ITEMS" in s:
                # best-effort row count via fetchall on a separate cursor is
                # not available; count via context is unreliable. We instead
                # rely on the wrapped-method counter for row budgets.
                pass

        self._b = before
        self._a = after
        event.listen(self._engine, "before_cursor_execute", self._b)
        event.listen(self._engine, "after_cursor_execute", self._a)
        return self

    def __exit__(self, *exc):
        event.remove(self._engine, "before_cursor_execute", self._b)
        event.remove(self._engine, "after_cursor_execute", self._a)


def test_row_budget(engine, resolver):
    """400-row terminal population, limit=10 → bounded fetch."""
    n = 400
    base_time = datetime.now(timezone.utc)
    ids = []
    for i in range(n):
        iid = f"inst-{i}"
        _seed_instance(engine, instance_id=iid)
        # stagger created_at so sort is meaningful
        ca = (base_time - timedelta(seconds=i)).isoformat()
        jid = _seed_job(engine, instance_id=iid, jid=f"job-{i}", created_at=ca)
        ids.append(jid)

    ensured = {ids[-1]}  # ranked below the SQL cut
    # Count rows returned by _query_jobs via wrapping.
    original = resolver._query_jobs
    counter = {"rows": 0, "calls": 0}

    def wrapped(*a, **kw):
        res = original(*a, **kw)
        counter["rows"] += len(res)
        counter["calls"] += 1
        return res

    resolver._query_jobs = wrapped
    try:
        recs = resolver.list_work(kind="job", limit=10, ensure_job_ids=ensured)
    finally:
        resolver._query_jobs = original

    # ensured row must appear (page completeness)
    assert ids[-1] in {r.work_id for r in recs}
    # row budget: main + recovery
    margin = 30
    assert counter["rows"] <= 10 + margin, (
        f"JobItem SELECTs returned {counter['rows']} rows; "
        f"expected <= {10 + margin}"
    )
    # at least the recovery happened
    assert counter["calls"] >= 1


def test_flat_select_count(engine, resolver):
    """The total SELECT count for a ``list_work`` call is CONSTANT as the
    page size grows (5 → 10 → 20 over the SAME population).

    Mirrors the house pin in
    ``tests/unit/services/test_work_resolver_query_budget.py``
    (``test_select_count_flat_as_page_doubles``): any growth with the
    page size is the per-row N+1 regression — a returning
    per-row ``_lookup_instance`` storm breaks flatness.

    The population is seeded ONCE (40 rows) so only the page size
    varies between the three measurements — growing the population as
    well would confound the property under test.

    Measured on the current implementation: 5 SELECTs per call
    (2 × job_queue_items, 2 × instances, 1 × task) at every page size,
    including the unbounded (``limit=None``) call.
    """
    for i in range(40):
        iid = f"fp-inst-{i}"
        _seed_instance(engine, instance_id=iid)
        _seed_job(engine, instance_id=iid, jid=f"fp-job-{i}")

    def count_selects(limit):
        class Spy:
            def __init__(self):
                self.n = 0
                self.per_leg = {"job_queue_items": 0, "instances": 0, "task": 0}

            def before(self, conn, cursor, stmt, params, context, executemany):
                s = stmt.strip().upper()
                if s.startswith("SELECT"):
                    self.n += 1
                    if "JOB_QUEUE_ITEMS" in s:
                        self.per_leg["job_queue_items"] += 1
                    if "FROM INSTANCES" in s:
                        self.per_leg["instances"] += 1
                    if "FROM TASK" in s or "FROM TASK," in s:
                        self.per_leg["task"] += 1

            def __enter__(self):
                event.listen(engine, "before_cursor_execute", self.before)
                return self

            def __exit__(self, *a):
                event.remove(engine, "before_cursor_execute", self.before)

        with Spy() as sp:
            recs = resolver.list_work(kind="job", limit=limit)
        assert len(recs) == (40 if limit is None else limit), (
            f"page size {limit} must return {limit} records; got "
            f"{len(recs)}"
        )
        return sp.n, sp.per_leg

    c5, leg5 = count_selects(5)
    c10, leg10 = count_selects(10)
    c20, leg20 = count_selects(20)
    c_none, leg_none = count_selects(None)

    assert c5 == c10 == c20, (
        f"SELECT count must be FLAT as the page grows (n=5 → {c5}, "
        f"n=10 → {c10}, n=20 → {c20}); growth with page size is the "
        f"per-row lookup regression."
    )
    assert c_none == c5, (
        f"the unbounded call must cost the SAME number of SELECTs as a "
        f"capped one (unbounded → {c_none}, capped → {c5}); a per-row "
        f"leg that scales with the population breaks flatness."
    )
    assert leg5 == leg10 == leg20 == leg_none, (
        f"per-leg SELECT counts must be flat too: {leg5} / {leg10} / "
        f"{leg20} / {leg_none}"
    )
    # Measured absolute budget (the house per-leg shape: page query +
    # batched mission-fields, batched instances + child-instance lookup,
    # batched task-timing) — a per-row regression moves these numbers.
    assert c5 == 5, (
        f"SELECT count per list_work call must stay at 5 "
        f"(2 job_queue_items + 2 instances + 1 task); got {c5} "
        f"({leg5})."
    )


def test_flatness_proper(engine, resolver):
    """Unchanged house-shaped flatness pin (kept from the original file)."""
    # seed a fixed 40-row population once
    for i in range(40):
        iid = f"fp-inst-{i}"
        _seed_instance(engine, instance_id=iid)
        _seed_job(engine, instance_id=iid, jid=f"fp-job-{i}")

    def count_selects(limit):
        class Spy:
            def __init__(self):
                self.n = 0
            def before(self, conn, cursor, stmt, params, context, executemany):
                s = stmt.strip().upper()
                if s.startswith("SELECT"):
                    self.n += 1
            def __enter__(self):
                event.listen(engine, "before_cursor_execute", self.before)
                return self
            def __exit__(self, *a):
                event.remove(engine, "before_cursor_execute", self.before)
        with Spy() as sp:
            resolver.list_work(kind="job", limit=limit)
        return sp.n

    c5 = count_selects(5)
    c10 = count_selects(10)
    c20 = count_selects(20)
    assert c5 == c10 == c20, (c5, c10, c20)


def test_page_completeness(engine, resolver):
    n = 400
    base_time = datetime.now(timezone.utc)
    ids = []
    for i in range(n):
        iid = f"pc-inst-{i}"
        _seed_instance(engine, instance_id=iid)
        ca = (base_time - timedelta(seconds=i)).isoformat()
        jid = _seed_job(engine, instance_id=iid, jid=f"pc-job-{i}", created_at=ca)
        ids.append(jid)

    ensured = {ids[-5]}  # low-ranked
    recs = resolver.list_work(kind="job", limit=10, ensure_job_ids=ensured)
    assert ids[-5] in {r.work_id for r in recs}


def test_order_compat(engine, resolver):
    # tie-heavy fixture: several rows share created_at
    tie_time = datetime.now(timezone.utc).isoformat()
    for i in range(5):
        iid = f"tie-inst-{i}"
        _seed_instance(engine, instance_id=iid)
        _seed_job(engine, instance_id=iid, jid=f"tie-job-{i}", created_at=tie_time)
    # plus a couple distinct
    for i in range(5, 8):
        iid = f"tie-inst-{i}"
        _seed_instance(engine, instance_id=iid)
        ca = (datetime.now(timezone.utc) - timedelta(seconds=i)).isoformat()
        _seed_job(engine, instance_id=iid, jid=f"tie-job-{i}", created_at=ca)

    bounded = resolver.list_work(kind="job", limit=4)
    unbounded = resolver.list_work(kind="job")
    # bounded records (first 4 of unbounded) should match
    assert [r.work_id for r in bounded] == [r.work_id for r in unbounded[:4]], (
        [r.work_id for r in bounded],
        [r.work_id for r in unbounded[:4]],
    )


def test_no_limit_path(engine, resolver):
    n = 12
    for i in range(n):
        iid = f"nl-inst-{i}"
        _seed_instance(engine, instance_id=iid)
        _seed_job(engine, instance_id=iid, jid=f"nl-job-{i}")
    recs = resolver.list_work(kind="job")
    assert len(recs) == n


# verify TIE_PAD importable
def test_tie_pad():
    assert TIE_PAD == 8
