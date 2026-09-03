"""Reproduce the post-settle defer-gate window — read-only investigation.

**Branch:** fix/defer-gate-post-settle-window @ 853abb1b (Phase-1 RED
baseline; the Phase-2 fix — the widened busy-set in
``_idle_predicate_sql.py`` — lands on top and flips the RED tests GREEN)
**Audit:** defer-gate post-Fix-B premature-admission window (suspected,
construction-visible, NOT yet reproduced prior to this commit).

## The suspected window

Post-Fix-B (commit e53d0519, 2026-09-02) the inline idempotent mirror
transition flips a message-mirror JobItem to ``admission_state='done'``
the instant ``ProcessMessageProcessor.on_success`` fires — at T0, when
the message-processing Task completes. The parent instance may still
be in a non-terminal state (``waiting_children``, ``running``,
``paused``) at that instant because child reports / graph turns are
still in flight.

The defer-queue idle gate (``JobRepository.has_active_non_deferred_work``
+ ``TaskRepository.has_active_non_deferred_work``) is consulted at
``JobProcessor._defer_idle_check`` (Gate A) and
``JobQueueService._select_next_eligible_job`` (Gate B) — both project-
scoped. If BOTH legs return False at the moment a defer job is
considered, the defer queue wrongly admits work while the mission is
still live.

## What this test pins

The scenario from the audit brief:

  (a) a non-defer mirror JobItem SETTLED (``admission_state='done'``)
      whose linked instance is NON-TERMINAL (``waiting_children``),
  (b) ZERO running/paused/pending job-less non-deferred tasks,
  (c) a defer job PENDING (probe target).

We invoke the ACTUAL gate via the real repository methods (no
mocking of gate internals) and assert what the gate returns. If the
gate says "blocked" the test passes and the window is COVERED; if the
gate says "idle" the test fails (intentional RED reproduction pending
the architect's solution-design round).

The pre-existing defer-gate tests use the session-scoped
``engine`` fixture with StaticPool in-memory SQLite. The audit brief
directs this test to file-backed SQLite via ``tmp_path`` to dodge the
known StaticPool write-corruption trap when the gate's predicate SQL
runs under multi-connection fan-out (F11 fixture convention).

## Phase-1 reproduction scope (read-only)

- ZERO new admission-state writers added. Census stays 23.
- No production code modified.
- The reproduction constructs the scenario, runs the actual SQL, and
  asserts the gate return value. If the test is RED, the bug is REAL
  and the report goes to the architect round. If the test is GREEN,
  the gate already covers the case and we ship a pinning test +
  doc update.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

from daemon.constants import TERMINAL_INSTANCE_STATUSES
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import _idle_predicate_sql
from daemon.repositories.job_queue.models import AdmissionState, JobItem, JobQueue
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository


# ─────────────────────────────────────────────────────────────────────────────
# File-backed SQLite engine fixture (per audit brief: NEVER StaticPool
# in-memory shared sessions; tmp_path file-backed engine for each test)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fb_engine(tmp_path):
    """File-backed SQLite engine (default QueuePool, not StaticPool).

    The audit brief mandates file-backed SQLite for this test class —
    the StaticPool in-memory fixture used by the sibling defer-gate
    tests is known to corrupt writes when multi-connection fan-out
    exercises the same engine (Repo & Dev Environment Conventions,
    "Multi-edit/write-tool verification discipline"). Default
    QueuePool hands each connection its own SQLite handle.
    """
    db_path = tmp_path / "defer_gate_post_settle.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def job_repo(fb_engine):
    """JobRepository backed by the file-backed engine."""
    return JobRepository(fb_engine)


@pytest.fixture
def task_repo(fb_engine):
    """TaskRepository backed by the file-backed engine."""
    return TaskRepository(fb_engine)


# ─────────────────────────────────────────────────────────────────────────────
# Raw-SQL seed helpers (mirror the defer-test recipe)
# ─────────────────────────────────────────────────────────────────────────────


def _insert_instance(
    engine: Engine,
    *,
    instance_id: str,
    project_id: str = "proj-post-settle",
    status: str = InstanceStatus.WAITING_CHILDREN.value,
    agent_id: str = "developer",
) -> None:
    """Insert an Instance row directly via SQL.

    The job predicate's ``LEFT JOIN instances i`` needs a matching
    ``instances`` row for the project/status filter to evaluate. The
    mirror JobItem's ``instance_id`` points here.
    """
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status, :project_id,
                     :created_at, :updated_at, 1)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": agent_id,
                "agent_dir": f"agents/{agent_id}",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
            },
        )


def _insert_queue(
    engine: Engine,
    *,
    queue_id: str,
    project_id: str,
    queue_type: str = "parallel",
    queue_name: str | None = None,
    concurrency_limit: int = 1,
) -> None:
    """Insert a JobQueue row directly via SQL."""
    name = queue_name or queue_id
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_queues
                    (queue_id, project_id, queue_name, queue_name_lower,
                     queue_type, concurrency_limit, is_system, is_paused,
                     description, created_at, updated_at)
                VALUES
                    (:queue_id, :project_id, :queue_name, :queue_name_lower,
                     :queue_type, :concurrency_limit, 0, 0,
                     NULL, :created_at, :updated_at)
                """
            ),
            {
                "queue_id": queue_id,
                "project_id": project_id,
                "queue_name": name,
                "queue_name_lower": name.lower(),
                "queue_type": queue_type,
                "concurrency_limit": concurrency_limit,
                "created_at": now,
                "updated_at": now,
            },
        )


def _insert_job_item(
    engine: Engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str,
    queue_id: str,
    admission_state: str,
    job_type: str = "task",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Insert a JobItem row directly via SQL.

    Default ``job_type='task'`` per W7 fixture realism: the realistic
    shape of defer/background candidates in production is ``task``-type
    (Phase-1 noted the real defer job was task-type). Callers must stamp
    ``job_type='message'`` explicitly when the scenario is specifically
    a Fix-B settled mirror — the busy-set's mirror clause
    (``j.job_type = 'message' AND j.admission_state = 'done'``) only
    matches message mirrors; without the explicit stamp the scenario
    accidentally drops below the predicate.
    """
    now = datetime.now(timezone.utc).isoformat()
    metadata_json = json.dumps(metadata or {})
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source,
                     project_id, queue_id, priority, admission_state,
                     created_at, instance_id, job_type, retry_count,
                     metadata)
                VALUES
                    (:job_id, :agent_id, :agent_dir, :message, :source,
                     :project_id, :queue_id, :priority, :admission_state,
                     :created_at, :instance_id, :job_type, :retry_count,
                     :metadata)
                """
            ),
            {
                "job_id": job_id,
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "message": "test mirror",
                "source": "api",
                "project_id": project_id,
                "queue_id": queue_id,
                "priority": 0,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
                "job_type": job_type,
                "retry_count": 0,
                "metadata": metadata_json,
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# The reproduction
# ─────────────────────────────────────────────────────────────────────────────


class TestDeferGatePostSettleWindow:
    """Reproduce the post-settle defer-gate window (Fix-B regression).

    Audit-brief scenario:

      * Instance in ``waiting_children`` (non-terminal).
      * Non-defer mirror JobItem (``job_type='message'``, queue_type=
        'parallel') settled at T0 (``admission_state='done'``) by Fix B.
      * No task rows exist (Task completed and was reconciled — the
        JobItem is the only durable residue of the message-processing
        unit of work).

    Expected (intended): the defer-gate blocks (returns truthy /
    ``non_defer_active=True``) so the defer queue cannot admit while
    the mission is live.

    If this test FAILS, the window is REAL — the gate returns 0 /
    ``non_defer_active=False`` and the defer queue wrongly admits while
    the parent mission is still working. That is the bug.
    """

    def test_leg1_job_predicate_with_settled_mirror_and_live_instance(
        self, fb_engine, job_repo
    ):
        """Leg 1 (job predicate) must block the defer gate.

        Scenario:
          * Instance ``inst-live`` in ``waiting_children``.
          * Mirror JobItem (``job_type='message'``) on a parallel queue,
            ``admission_state='done'`` — settled at T0 per Fix B.

        Intended (per spec): Leg 1 returns True (the parent mission is
        live, even though the mirror has settled).

        Current behaviour (the bug, if it fires): Leg 1 returns False.
        The job predicate's ``j.admission_state = 'active'`` filter in
        the pre-Phase-2 predicate body (now superseded by
        ``daemon/repositories/job_queue/_idle_predicate_sql.py``) used
        to exclude settled mirrors, so the LEFT JOIN against a
        non-terminal instance was invisible to the predicate. The
        post-settle widening (2026-09-03, Phase-2 fix shipped at
        ``81e8d247``) closes this window: the shared busy-body adds the
        ``job_type='message' AND admission_state='done'`` clause
        explicitly.
        """
        _insert_instance(
            fb_engine,
            instance_id="inst-live",
            project_id="proj-post-settle",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _insert_queue(
            fb_engine,
            queue_id="queue-parallel",
            project_id="proj-post-settle",
            queue_type="parallel",
        )
        _insert_job_item(
            fb_engine,
            job_id="job-mirror-settled",
            instance_id="inst-live",
            project_id="proj-post-settle",
            queue_id="queue-parallel",
            admission_state=AdmissionState.DONE.value,  # settled at T0
            job_type="message",                          # mirror
        )

        # INTENDED: True. The parent mission is live (waiting_children);
        # the defer queue must wait.
        assert (
            job_repo.has_active_non_deferred_work("proj-post-settle") is True
        ), (
            "Leg 1 (job predicate) returned False on a settled mirror + "
            "non-terminal instance — the post-settle defer-gate window "
            "is REAL. The defer queue will admit while the parent mission "
            "is still working. Pre-fix, the bug was the SQL filter "
            "``j.admission_state = 'active'`` in the pre-Phase-2 "
            "predicate body (now superseded by the shared constant at "
            "``daemon/repositories/job_queue/_idle_predicate_sql.py``) "
            "— it excluded settled mirrors of non-terminal instances, "
            "so the LEFT JOIN against ``instances.status`` was invisible. "
            "The Phase-2 fix (shipped @ 81e8d247) adds the explicit "
            "``job_type='message' AND admission_state='done'`` clause."
        )

    def test_leg2_task_predicate_with_no_tasks_present(
        self, fb_engine, task_repo
    ):
        """Leg 2 (task predicate) must report "idle" in this scenario.

        With no ``task`` rows at all (the message-processing Task was
        completed and reconciled), the task predicate correctly returns
        False. The bug is NOT in this leg — it's in Leg 1. This test
        pins the "no tasks" baseline so the two-leg composition in
        ``JobProcessor._defer_idle_check`` is unambiguous.
        """
        # No task rows at all.
        assert (
            task_repo.has_active_non_deferred_work("proj-post-settle")
            is False
        ), (
            "Leg 2 (task predicate) returned True with NO task rows — "
            "unexpected baseline drift. The post-settle scenario is "
            "explicitly task-less (the original message Task completed "
            "and was reconciled)."
        )

    def test_full_gate_with_settled_mirror_and_no_tasks_blocks(
        self, fb_engine, job_repo, task_repo
    ):
        """The full defer gate (Leg 1 OR Leg 2) must block.

        Belt-and-suspenders: even if Leg 1 returns False (the bug),
        the OR composition should still block if Leg 2 returns True.
        This test pins the FULL gate semantics — the ``_defer_idle_check``
        posture is "block if EITHER leg is True" (failure-CLOSED).
        Currently, BOTH legs return False in this scenario, so the
        gate wrongly reports "idle".
        """
        _insert_instance(
            fb_engine,
            instance_id="inst-live-2",
            project_id="proj-gate-full",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _insert_queue(
            fb_engine,
            queue_id="queue-parallel-2",
            project_id="proj-gate-full",
            queue_type="parallel",
        )
        _insert_job_item(
            fb_engine,
            job_id="job-mirror-settled-2",
            instance_id="inst-live-2",
            project_id="proj-gate-full",
            queue_id="queue-parallel-2",
            admission_state=AdmissionState.DONE.value,
            job_type="message",
        )

        leg1 = job_repo.has_active_non_deferred_work("proj-gate-full")
        leg2 = task_repo.has_active_non_deferred_work("proj-gate-full")
        gate_blocked = leg1 or leg2  # OR semantics (per Gate A / Gate B)

        # INTENDED: gate_blocked is True. The defer queue must wait.
        assert gate_blocked is True, (
            f"Defer-gate FULL composition returned False (leg1={leg1}, "
            f"leg2={leg2}) on settled mirror + non-terminal instance + "
            f"no tasks — the post-settle window is REAL. Both legs "
            f"report 'idle' while the parent mission is live."
        )

    def test_baseline_settled_mirror_of_terminal_instance_is_idle(
        self, fb_engine, job_repo
    ):
        """Baseline (control): a settled mirror of a TERMINAL instance
        MUST report 'idle' — this is the existing-correct case.

        Pairs with the bug-scenario test above. When the parent
        instance has reached a terminal state (completed / error /
        terminated / failed), a settled mirror (``admission_state=
        'done'``) is genuinely-done work and the defer queue MAY
        admit. This test pins the correct baseline so any future
        fix does not over-block the genuine-idle case.
        """
        _insert_instance(
            fb_engine,
            instance_id="inst-terminal",
            project_id="proj-baseline",
            status=InstanceStatus.COMPLETED.value,
        )
        _insert_queue(
            fb_engine,
            queue_id="queue-parallel-base",
            project_id="proj-baseline",
            queue_type="parallel",
        )
        _insert_job_item(
            fb_engine,
            job_id="job-mirror-settled-base",
            instance_id="inst-terminal",
            project_id="proj-baseline",
            queue_id="queue-parallel-base",
            admission_state=AdmissionState.DONE.value,
            job_type="message",
        )

        # Correct (pre-existing) behaviour: terminal instance → idle.
        assert (
            job_repo.has_active_non_deferred_work("proj-baseline") is False
        ), (
            "Baseline regression: a settled mirror of a TERMINAL "
            "instance MUST be reported 'idle' so the defer queue may "
            "admit. The terminal-instance filter in the shared "
            "busy-body (``daemon/repositories/job_queue/_idle_predicate_sql.py``) "
            "— ``i.status NOT IN :terminal_statuses`` — must still "
            "gate this correctly. Single-sourced from "
            "``daemon.constants.TERMINAL_INSTANCE_STATUSES`` (W1)."
        )

    def test_baseline_active_mirror_of_live_instance_blocks(
        self, fb_engine, job_repo
    ):
        """Baseline (control): an ACTIVE mirror of a LIVE instance
        MUST block — this is the existing-correct case.

        The Phase 2 defer-gate predicate
        (``daemon/repositories/job_queue/_idle_predicate_sql.py``) was
        designed to block this exact shape (active + non-terminal +
        non-defer queue). This test pins that baseline so any fix to
        the post-settle window does not silently regress the
        already-covered case.
        """
        _insert_instance(
            fb_engine,
            instance_id="inst-live-active",
            project_id="proj-baseline-active",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _insert_queue(
            fb_engine,
            queue_id="queue-parallel-active",
            project_id="proj-baseline-active",
            queue_type="parallel",
        )
        _insert_job_item(
            fb_engine,
            job_id="job-mirror-active",
            instance_id="inst-live-active",
            project_id="proj-baseline-active",
            queue_id="queue-parallel-active",
            admission_state=AdmissionState.ACTIVE.value,  # pre-Fix-B
            job_type="message",
        )

        # Correct (pre-existing) behaviour: active + non-terminal → blocked.
        assert (
            job_repo.has_active_non_deferred_work("proj-baseline-active")
            is True
        ), (
            "Baseline regression: an ACTIVE mirror of a LIVE instance "
            "MUST block the defer gate. The Phase 2 predicate covers "
            "this — confirm we have not regressed it."
        )

    def test_background_gate_also_affected_when_settled_mirror_is_global(
        self, fb_engine, job_repo
    ):
        """The background gate (system-wide) has the same window.

        Background gate body lives in the shared SQL constants module
        (``daemon/repositories/job_queue/_idle_predicate_sql.py``,
        ``JOB_BACKGROUND_BUSY_BODY``) —
        ``has_active_non_background_work`` is system-wide
        (project_id argument is ``del``'d). The Phase 2 background
        predicate uses ``admission_state IN ('queued', 'active')`` —
        broader than the defer predicate's single ``active`` value
        (the documented defer-vs-background legacy-clause asymmetry),
        but STILL excludes ``done``. A settled mirror of a non-terminal
        instance is invisible to the background gate too. This test
        pins the parallel failure mode so the architect round has
        complete evidence on both legs.
        """
        _insert_instance(
            fb_engine,
            instance_id="inst-live-bg",
            project_id="proj-bg-window",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _insert_queue(
            fb_engine,
            queue_id="queue-parallel-bg",
            project_id="proj-bg-window",
            queue_type="parallel",
        )
        _insert_job_item(
            fb_engine,
            job_id="job-mirror-settled-bg",
            instance_id="inst-live-bg",
            project_id="proj-bg-window",
            queue_id="queue-parallel-bg",
            admission_state=AdmissionState.DONE.value,
            job_type="message",
        )

        # INTENDED: True. The background queue must wait.
        assert (
            job_repo.has_active_non_background_work(None) is True
        ), (
            "Background gate returned False on a settled mirror + "
            "non-terminal instance — the post-settle window is REAL "
            "for the system-wide background gate too. The legacy "
            "clause ``j.admission_state IN ('queued', 'active')`` in "
            "the shared busy-body "
            "(``daemon/repositories/job_queue/_idle_predicate_sql.py``) "
            "excludes settled mirrors; the post-Fix-B mirror clause "
            "is what catches them (shipped @ 81e8d247)."
        )

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 (the fix): shared-SQL-body drift guard, folding proof-test,
# self-deadlock guard pin, and PG/SQLite parity.
# ─────────────────────────────────────────────────────────────────────────────


def _insert_task(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
    status: str,
    is_deferred: bool,
) -> None:
    """Insert a task row directly via SQL (mirrors the deadlock-fix recipe).

    Python bools for ``is_deferred`` so the bind works on both SQLite
    (INTEGER 0/1) and PostgreSQL (BOOLEAN false/true).
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO task (task_type, instance_id, message_id, status,
                                  retry_count, created_at, cancel_requested,
                                  retry_scheduled, work_id, is_deferred,
                                  is_background)
                VALUES (:task_type, :instance_id, :message_id, :status,
                        :retry_count, :created_at, :cancel_requested,
                        :retry_scheduled, :work_id, :is_deferred,
                        :is_background)
                """
            ),
            {
                "task_type": TaskType.PROCESS_MESSAGE.value,
                "instance_id": instance_id,
                "message_id": str(uuid.uuid4()),
                "status": status,
                "retry_count": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": work_id,
                "is_deferred": is_deferred,
                "is_background": False,
            },
        )


def _make_gate_b_service(engine: Engine):
    """Build a JobQueueService with the real repository stack (Gate B)."""
    from daemon.services.job_queue_service import JobQueueService

    job_repo = JobRepository(engine)
    queue_repo = JobQueueRepository(engine)
    task_repo = TaskRepository(engine)
    from unittest.mock import MagicMock

    from daemon.services.job_lock_manager import JobLockManager

    lock_manager = JobLockManager(lock_repo=MagicMock())
    svc = JobQueueService(job_repo, lock_manager, queue_repo)
    im = MagicMock()
    im._task_repo = task_repo
    svc.set_instance_manager(im)
    return svc


class TestPostSettlePhase2Fix:
    """Phase-2 fix tests: shared-body drift guard, folding proof, self-deadlock."""

    def test_sql_body_shared_constant(self):
        """Drift paranoia: both repository predicates consume the shared
        SQL-body constants, and the constants carry the exact busy-set
        semantics the fix specifies (per the spec §4 test table).
        """
        defer_src = inspect.getsource(
            JobRepository.has_active_non_deferred_work
        )
        bg_src = inspect.getsource(JobRepository.has_active_non_background_work)

        # The wiring: each predicate executes its shared statement + binds.
        assert "defer_busy_statement()" in defer_src, (
            "has_active_non_deferred_work no longer consumes the shared "
            "defer busy-body — two-SQL-site drift risk"
        )
        assert "defer_busy_binds(" in defer_src
        assert "background_busy_statement()" in bg_src, (
            "has_active_non_background_work no longer consumes the shared "
            "background busy-body — two-SQL-site drift risk"
        )
        assert "background_busy_binds()" in bg_src

        # The single-source constants themselves. The terminal-status
        # tuple is DERIVED from the canonical
        # ``daemon.constants.TERMINAL_INSTANCE_STATUSES`` frozenset
        # (W1 — single-sourcing closes the two-set desync trap); the
        # cross-assert below pins that the predicate's bind matches the
        # canonical set exactly. Sorted for stable equality (the
        # ``expanding`` bindparam is order-insensitive — the canonical
        # source is a frozenset, so any equality check is order-free).
        assert _idle_predicate_sql.JOB_TERMINAL_STATUSES == tuple(
            sorted(TERMINAL_INSTANCE_STATUSES)
        ), (
            "JOB_TERMINAL_STATUSES desynced from the canonical "
            "TERMINAL_INSTANCE_STATUSES — re-derive from "
            "daemon.constants; the cross-assert guards the two-set "
            "desync trap (W1)"
        )
        # Belt-and-suspenders: cross-check the explicit four members too
        # — guards against a future canonical constant that silently
        # drops or adds a member without breaking the equality check
        # above (which would pass for symmetric additions/removals).
        assert set(_idle_predicate_sql.JOB_TERMINAL_STATUSES) == {
            "completed",
            "error",
            "terminated",
            "failed",
        }
        assert _idle_predicate_sql.DEFER_EXCLUDED_QUEUE_TYPES == ("defer",)
        # Background excludes ONLY 'background' — NOT 'defer': defer work
        # IS non-background work (2026-07-23 defer-leak fix, pinned by
        # test_defer_idle_gate_phase2.py::
        # test_defer_queue_job_counts_as_non_background).
        assert _idle_predicate_sql.BACKGROUND_EXCLUDED_QUEUE_TYPES == (
            "background",
        )

        # W1 drift-guard cross-assert: the SQL bind for the terminal
        # statuses is the canonical constant, NOT a hand-copied literal.
        # Captures the regression class where someone re-introduces a
        # local literal copy of the status set inside the bind helpers.
        from daemon.repositories.job_queue import _idle_predicate_sql as ips

        defer_binds = ips.defer_busy_binds("proj-drift")
        bg_binds = ips.background_busy_binds()
        assert set(defer_binds["terminal_statuses"]) == TERMINAL_INSTANCE_STATUSES
        assert set(bg_binds["terminal_statuses"]) == TERMINAL_INSTANCE_STATUSES

        def _norm(sql: str) -> str:
            return " ".join(sql.split())

        defer_sql = _norm(_idle_predicate_sql.JOB_DEFER_BUSY_BODY)
        bg_sql = _norm(_idle_predicate_sql.JOB_BACKGROUND_BUSY_BODY)

        # Both bodies carry the post-Fix-B settled-mirror clause (twice
        # referenced terminal filter: legacy clause + mirror clause).
        for label, sql in (("defer", defer_sql), ("background", bg_sql)):
            assert "j.job_type = 'message'" in sql, label
            assert "j.admission_state = 'done'" in sql, label
            assert "j.instance_id IS NOT NULL" in sql, label
            assert (
                sql.count("i.status NOT IN :terminal_statuses") == 2
            ), label

        # §4.1 asymmetry: defer body is scope-switchable via the
        # :project_id bind; the background body has NO project clause.
        assert ":project_id IS NULL OR j.project_id = :project_id" in defer_sql
        assert ":project_id" not in bg_sql

        # Background keeps the Fix-2B deadlock carve-out (queued JobItem
        # with a still-pending Task is unclaimable → must not hold the
        # gate); the defer body needs no task join (legacy clause is
        # active-only).
        assert "LEFT JOIN task t ON t.work_id = j.job_id" in bg_sql
        assert "t.status IS NULL" in bg_sql
        assert "t.status != 'pending'" in bg_sql
        assert "LEFT JOIN task" not in defer_sql

        # I3 clarifying line lives in the shared module's docstring.
        module_doc = " ".join(
            (inspect.getdoc(_idle_predicate_sql) or "").split()
        )
        assert (
            "a settled mirror of a non-terminal instance counts as live "
            "for the defer/background gate, terminal for everything else"
        ) in module_doc

    def test_post_settle_admission_gate_catches_what_claim_cannot(
        self, fb_engine, job_repo, task_repo
    ):
        """The mandatory folding proof-test (spec §4 table row).

        Scenario: defer candidate pending; parent message Task
        COMPLETED; parent instance ``waiting_children``; mirror ``done``.

        Asserts the two-leg architecture end to end:

          * Gate B (``_select_next_eligible_job``) returns None — the
            ADMISSION gate blocks because the mission is live (the
            settled mirror of the waiting parent counts as busy). This
            is the fix.
          * The claim-guard's t2 finds no active task — the folding
            defer-gate EXISTS inside ``claim_pending_task`` sees the
            COMPLETED parent message Task as no work. That is CORRECT:
            the message Task IS completed, there is no task race, and
            the claim guard is a task-granular atomic race-guard — per
            the 2026-07-23 plan §4 it is NOT the definition of idle.
            The gate is the mission-respecting layer; it catches what
            the claim cannot.
          * The atomic claim itself stays shut in this window via the
            2026-07-26 queue-awareness guard (linked JobItem still
            ``queued`` ⇒ slot not granted) — layered defense that does
            not consult mission liveness at all.
        """
        project = "proj-folding"

        # The live parent mission: instance waiting_children with a
        # settled message mirror (Fix-B post-settle shape).
        _insert_instance(
            fb_engine,
            instance_id="inst-parent-f",
            project_id=project,
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _insert_queue(
            fb_engine,
            queue_id="queue-par-f",
            project_id=project,
            queue_type="parallel",
        )
        _insert_job_item(
            fb_engine,
            job_id="job-mirror-f",
            instance_id="inst-parent-f",
            project_id=project,
            queue_id="queue-par-f",
            admission_state=AdmissionState.DONE.value,
            job_type="message",
        )

        # The defer candidate: pending job on the defer queue, its own
        # live instance, and a PENDING deferred task linked by work_id.
        _insert_instance(
            fb_engine,
            instance_id="inst-defer-f",
            project_id=project,
            status=InstanceStatus.RUNNING.value,
        )
        _insert_queue(
            fb_engine,
            queue_id="queue-defer-f",
            project_id=project,
            queue_type="defer",
        )
        _insert_job_item(
            fb_engine,
            job_id="job-defer-f",
            instance_id="inst-defer-f",
            project_id=project,
            queue_id="queue-defer-f",
            admission_state=AdmissionState.QUEUED.value,
        )
        # W7 fixture-realism pin: the defer candidate is task-typed
        # (the realistic shape per Phase-1's note that the real defer
        # job was task-type). Stamp ``message`` only when the scenario
        # is specifically a Fix-B settled mirror; candidates must be
        # task-type or the busy-set's mirror clause silently misfires.
        with fb_engine.begin() as conn:
            cand_kind = conn.execute(
                text("SELECT job_type FROM job_queue_items WHERE job_id = :jid"),
                {"jid": "job-defer-f"},
            ).scalar_one()
            mirror_kind = conn.execute(
                text("SELECT job_type FROM job_queue_items WHERE job_id = :jid"),
                {"jid": "job-mirror-f"},
            ).scalar_one()
        assert cand_kind == "task", (
            "defer candidate job_type drifted from 'task' — fixture "
            "realism broken (W7); the realistic defer shape is "
            "task-type, not message-type"
        )
        assert mirror_kind == "message", (
            "settled mirror job_type drifted from 'message' — the "
            "mirror clause ``j.job_type = 'message'`` would silently "
            "drop below the predicate"
        )
        # The parent message Task is COMPLETED (reconciled) — the
        # durable task-side residue of the message work is gone.
        _insert_task(
            fb_engine,
            work_id="job-mirror-f",
            instance_id="inst-parent-f",
            status=TaskStatus.COMPLETED.value,
            is_deferred=False,
        )
        # The deferred candidate task, still pending.
        _insert_task(
            fb_engine,
            work_id="job-defer-f",
            instance_id="inst-defer-f",
            status=TaskStatus.PENDING.value,
            is_deferred=True,
        )

        # Leg 2 sees NO active non-deferred task — the t2 semantics.
        assert task_repo.has_active_non_deferred_work(project) is False

        # Gate B blocks admission — the mission-respecting layer.
        svc = _make_gate_b_service(fb_engine)
        pending = job_repo.list_all_pending()
        assert [j.job_id for j in pending] == ["job-defer-f"]
        result = asyncio.run(
            svc._select_next_eligible_job(pending, project)
        )
        assert result is None, (
            "Gate B admitted the defer candidate while the parent "
            "mission is live (settled mirror + waiting_children) — the "
            "post-settle window is not closed at the admission gate."
        )

        # The claim-guard's t2 (the defer-gate folding EXISTS inside
        # ``claim_pending_task``) correctly finds NO active task in this
        # scenario: the only non-deferred task (the parent message
        # Task) is COMPLETED, which is outside t2's
        # ``status IN (pending, running, paused)`` set. This is the
        # exact subquery shape folded into the atomic claim
        # (``daemon/repositories/task/repository.py``,
        # ``claim_pending_task`` defer queue idle gate clause, the
        # ``AND NOT (task.is_deferred = :is_deferred_true AND EXISTS
        # (...))`` fold at lines 1357-1371) — asserted directly because
        # the claim's OUTER guards would mask t2's verdict (see below).
        t2_sql = text(
            """
            SELECT EXISTS (
                SELECT 1 FROM task t2
                JOIN instances i2 ON t2.instance_id = i2.instance_id
                WHERE t2.status IN (:s_pending, :s_running, :s_paused)
                  AND t2.is_deferred = :not_deferred
                  AND i2.project_id = (
                      SELECT i_cand.project_id FROM instances i_cand
                      WHERE i_cand.instance_id = :cand_instance
                  )
            )
            """
        )
        with fb_engine.begin() as conn:
            t2_found_work = conn.execute(
                t2_sql,
                {
                    "s_pending": TaskStatus.PENDING.value,
                    "s_running": TaskStatus.RUNNING.value,
                    "s_paused": TaskStatus.PAUSED.value,
                    "not_deferred": False,
                    "cand_instance": "inst-defer-f",
                },
            ).scalar_one()
        assert not t2_found_work, (
            "the claim-guard's t2 found active non-deferred task work — "
            "the claim clearing would NOT be correct behavior in this "
            "scenario; Leg 2 semantics drifted"
        )

        # The atomic claim ALSO stays shut in this window — but for a
        # DIFFERENT, also-correct reason: the 2026-07-26 queue-awareness
        # guard refuses to claim a Task whose linked JobItem is still
        # ``queued`` (slot not granted yet — Gate B just blocked that
        # grant). Layered defense: t2 cleared (no task race), the slot
        # guard holds the lane shut pre-admission. Neither guard
        # consults mission liveness — that is the GATE's job, and it
        # blocked above. Claim clearing (t2) is CORRECT behavior, not a
        # hole; per the 2026-07-23 plan §4 the claim-guard is a
        # task-granular atomic race-guard, not the definition of idle.
        claimed = task_repo.claim_pending_task("worker-folding-proof")
        assert claimed is None, (
            "claim_pending_task claimed a task whose linked JobItem is "
            "still queued — the queue-awareness guard (2026-07-26) "
            "regressed"
        )

    def test_folding_logic_getsource_substring_pin(self):
        """Folding-proof ``inspect.getsource`` pin.

        Pins that the production source of
        ``daemon/repositories/task/repository.py`` (the ``claim_pending_task``
        defer queue idle gate clause) still contains the expected
        folding-logic substring. Catches accidental rewrites of the
        claim's task-granular atomic race-guard (the inline ``AND NOT
        (... AND EXISTS (...))`` block at lines 1357-1371) — the
        folding proof above relies on this exact SQL shape.

        Cheap green (cheap-green reviewer item 2026-09-03):
        ``inspect.getsource`` is the cheapest possible pin — a
        substring match against the live source. A future refactor
        that accidentally rewrites the EXISTS subquery shape would
        fail this test even if the SQL still happens to evaluate
        identically (the test catches the edit, not just the runtime
        behavior).
        """
        from daemon.repositories.task import repository as task_repo_mod

        src = inspect.getsource(task_repo_mod)
        # The exact folding-logic substring that the proof-test above
        # mirrors with t2_sql. Each line is pinned individually — a
        # rewrite that flips the join order, drops a bind name, or
        # inverts the WHERE would fail at least one of these.
        expected_substrings = (
            # Outer fold: the defer candidate is held back when there
            # is active non-deferred work in the same project.
            "task.is_deferred = :is_deferred_true",
            "AND EXISTS (",
            "SELECT 1 FROM task t2",
            "JOIN instances i2",
            "ON t2.instance_id = i2.instance_id",
            "t2.status IN (:status_pending, :status_running, :status_paused)",
            "t2.is_deferred = :is_deferred_false",
            # The subproject scoping: the candidate's instance_id
            # resolves to its project_id, and t2 must share it.
            "i2.project_id = (",
            "SELECT i_cand.project_id",
            "FROM instances i_cand",
            "WHERE i_cand.instance_id = task.instance_id",
        )
        missing = [s for s in expected_substrings if s not in src]
        assert not missing, (
            "the production ``claim_pending_task`` defer queue idle "
            "gate clause at "
            "``daemon/repositories/task/repository.py`` no longer "
            "contains the expected folding-logic substring(s) — the "
            "claim-guard refactored; the folding proof-test above "
            "(t2_sql mirrors this exact shape) would silently drift. "
            f"Missing: {missing}"
        )

    def test_defer_candidate_own_live_instance_does_not_self_deadlock(
        self, fb_engine, job_repo, task_repo
    ):
        """A's guard pin: the defer candidate's OWN live instance must
        not witness against itself.

        The candidate's row sits on the defer queue, and the busy-set's
        queue-type exclusion (W2) keeps the defer queue's own rows out
        of the busy-set — so a defer candidate whose own instance is
        ``waiting_children`` is ADMITTED, structurally. (Mission
        liveness of the candidate's own instance is the defer queue's
        own work — blocking on it would self-deadlock the lane.)
        """
        project = "proj-self-deadlock"
        _insert_instance(
            fb_engine,
            instance_id="inst-self",
            project_id=project,
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _insert_queue(
            fb_engine,
            queue_id="queue-defer-self",
            project_id=project,
            queue_type="defer",
        )
        _insert_job_item(
            fb_engine,
            job_id="job-defer-self",
            instance_id="inst-self",
            project_id=project,
            queue_id="queue-defer-self",
            admission_state=AdmissionState.QUEUED.value,
        )
        # W7 fixture-realism pin: the defer candidate is task-typed
        # (the realistic shape per Phase-1's note that the real defer
        # job was task-type). The candidate's own row sits on the defer
        # queue and is excluded by ``queue_type NOT IN :excluded_queue_types``;
        # the test pins that the candidate shape matches what a real
        # defer queue would carry — task-typed, not message-typed.
        with fb_engine.begin() as conn:
            cand_kind = conn.execute(
                text("SELECT job_type FROM job_queue_items WHERE job_id = :jid"),
                {"jid": "job-defer-self"},
            ).scalar_one()
        assert cand_kind == "task", (
            "defer candidate job_type drifted from 'task' — fixture "
            "realism broken (W7); the realistic defer shape is "
            "task-type, not message-type"
        )
        # No other work anywhere — the ONLY row is the candidate itself.
        assert job_repo.has_active_non_deferred_work(project) is False, (
            "the defer candidate's own defer-queue row counted as busy "
            "non-defer work — the queue-type exclusion broke; the lane "
            "would self-deadlock"
        )

        svc = _make_gate_b_service(fb_engine)
        pending = job_repo.list_all_pending()
        result = asyncio.run(
            svc._select_next_eligible_job(pending, project)
        )
        assert result is not None and result.job_id == "job-defer-self", (
            "Gate B refused a defer candidate whose own instance is "
            "waiting_children — self-deadlock, the queue-type exclusion "
            "does not hold"
        )

    def test_dangling_instance_id_done_mirror_drops_to_idle(
        self, fb_engine, job_repo
    ):
        """W2(a) dangling-row pin: a done mirror whose linked
        ``instances`` row is DELETED (a dangling ``instance_id``) MUST
        report idle — ``NULL`` ``i.status`` evaluates to ``NULL`` under
        ``NOT IN`` and the row is dropped.

        Pinned against the docstring at
        ``daemon/repositories/job_queue/_idle_predicate_sql.py`` ~lines
        92-95: "NULL ``i.status`` (dangling ``instance_id``) evaluates
        to NULL under ``NOT IN`` and the row is dropped — identical to
        the previous ``!=`` chain's three-valued behavior."

        Scenario:

          * JobItem with ``job_type='message'``,
            ``admission_state='done'`` (Fix-B settled mirror).
          * ``instance_id`` is set, but the matching ``instances`` row
            is NEVER inserted (or has been deleted) — the LEFT JOIN
            yields ``i.status = NULL``.
          * No other work anywhere.

        Expected: the busy-set returns ``False`` — the dangling mirror
        is dropped, not counted as busy. This matches the pre-Fix-B
        ``!=`` chain's three-valued behavior (the original gate had the
        same SQL NULL semantics; the shared-body fix preserves them).

        This is NOT a regression test for the post-settle window
        (that's covered by ``test_leg1_job_predicate_with_settled_
        mirror_and_live_instance``); it pins the dangling-instance
        edge case so a future change to the predicate body does not
        silently re-interpret NULL ``i.status`` as a busy signal.
        """
        project = "proj-dangling"
        # Insert ONLY a JobItem whose ``instance_id`` has no matching
        # ``instances`` row. The mirror clause requires
        # ``instance_id IS NOT NULL`` (which is satisfied — the column
        # has a value), but the LEFT JOIN yields NULL ``i.status``.
        _insert_queue(
            fb_engine,
            queue_id="queue-par-dangling",
            project_id=project,
            queue_type="parallel",
        )
        _insert_job_item(
            fb_engine,
            job_id="job-mirror-dangling",
            instance_id="inst-dangling-missing",
            project_id=project,
            queue_id="queue-par-dangling",
            admission_state=AdmissionState.DONE.value,
            job_type="message",  # mirror, per W7 fixture realism
        )

        # Both legs must report idle: the dangling done mirror is
        # dropped by the ``NULL NOT IN :terminal_statuses`` semantics,
        # so the busy-set is empty.
        assert (
            job_repo.has_active_non_deferred_work(project) is False
        ), (
            "dangling instance_id + done mirror counted as busy — the "
            "NULL i.status three-valued drop is broken; the docstring "
            "at _idle_predicate_sql.py ~lines 92-95 promises the row is "
            "dropped, not counted as busy"
        )
        assert (
            job_repo.has_active_non_background_work(None) is False
        ), (
            "background gate (system-wide) wrongly counted a dangling "
            "done mirror as busy — same NULL ``i.status`` drop, same "
            "docstring pin"
        )


# ─────────────────────────────────────────────────────────────────────────────
# PG/SQLite parity (opt-in: pytest -m postgres, per repo convention).
# ─────────────────────────────────────────────────────────────────────────────

# Connection defaults mirror tests/postgres/conftest.py (docker-compose
# test stack: ensemble/ensemble_dev). Overridable via env for CI.
_PG_HOST = os.environ.get("PG_TEST_HOST", "localhost")
_PG_PORT = os.environ.get("PG_TEST_PORT", "5432")
_PG_DB = os.environ.get("PG_TEST_DB", "ensemble_test")
_PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
_PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
_PG_URL = (
    f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
    f"@{_PG_HOST}:{_PG_PORT}/{_PG_DB}"
)

# The four tables the busy-set SQL touches. The parity test creates any
# that are missing on the shared test database and (only) drops the ones
# it created itself — it must never drop a pre-existing table.
_PARITY_TABLES = (
    Instance.__table__,
    JobQueue.__table__,
    JobItem.__table__,
    Task.__table__,
)


@pytest.mark.postgres
class TestIdlePredicatePgSqliteParity:
    """Same busy-set returns on PostgreSQL and SQLite (dual-driver).

    The predicates run on PostgreSQL in production and SQLite in the
    unit suite; the expanding-bind busy-set bodies must return the same
    boolean on both engines for every pinned scenario (boolean-bind
    pattern: exact ``is True`` / ``is False`` checks, no truthiness).
    Skips cleanly when PostgreSQL is unreachable — the assertion is the
    parity, not PG's availability.
    """

    # ── dialect-safe seeding (str created_at is the model's native
    #    type; the JSONB `metadata` column is omitted — nullable, and
    #    a raw str bind into jsonb would fail on PG) ──

    def _insert_instance(self, engine, *, instance_id, project_id, status):
        now = datetime.now(timezone.utc).isoformat()
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO instances
                        (instance_id, agent_id, agent_dir, status,
                         project_id, created_at, updated_at, version)
                    VALUES (:instance_id, 'developer', 'agents/developer',
                            :status, :project_id, :created_at, :updated_at, 1)
                    """
                ),
                {
                    "instance_id": instance_id,
                    "status": status,
                    "project_id": project_id,
                    "created_at": now,
                    "updated_at": now,
                },
            )

    def _insert_queue(self, engine, *, queue_id, project_id, queue_type):
        now = datetime.now(timezone.utc).isoformat()
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO job_queues
                        (queue_id, project_id, queue_name, queue_name_lower,
                         queue_type, concurrency_limit, is_system, is_paused,
                         description, created_at, updated_at)
                    VALUES (:queue_id, :project_id, :queue_id, :queue_id,
                            :queue_type, 1, 0, 0, NULL, :created_at,
                            :updated_at)
                    """
                ),
                {
                    "queue_id": queue_id,
                    "project_id": project_id,
                    "queue_type": queue_type,
                    "created_at": now,
                    "updated_at": now,
                },
            )

    def _insert_job(self, engine, *, job_id, instance_id, project_id,
                    queue_id, admission_state, job_type="message"):
        now = datetime.now(timezone.utc).isoformat()
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO job_queue_items
                        (job_id, agent_id, agent_dir, message, source,
                         project_id, queue_id, priority, admission_state,
                         created_at, instance_id, job_type, retry_count)
                    VALUES (:job_id, 'developer', 'agents/developer', 'p',
                            'api', :project_id, :queue_id, 5,
                            :admission_state, :created_at, :instance_id,
                            :job_type, 0)
                    """
                ),
                {
                    "job_id": job_id,
                    "project_id": project_id,
                    "queue_id": queue_id,
                    "admission_state": admission_state,
                    "created_at": now,
                    "instance_id": instance_id,
                    "job_type": job_type,
                },
            )

    def _clear_work_tables(self, engine):
        """Delete ALL rows from the three work tables (scenario isolation).

        System-wide checks must not see the previous scenario's rows;
        deleting (not dropping) keeps pre-existing tables intact.
        """
        with engine.begin() as conn:
            for table in ("job_queue_items", "job_queues", "instances"):
                conn.execute(text(f"DELETE FROM {table}"))

    def test_idle_predicate_pg_sqlite_parity(self, tmp_path):
        from sqlalchemy import inspect as sa_inspect

        # Probe PG; skip CLEANLY with an explicit reason when the PG
        # test infrastructure is unavailable (repo convention — never
        # error). Two distinct unavailability modes: server unreachable,
        # or test database not prepared (bare `ensemble_test` without a
        # `public` schema — the docker-compose test stack creates it;
        # creating a schema on the target server is not a test's job).
        try:
            pg_engine = create_engine(_PG_URL, pool_pre_ping=True)
            with pg_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                has_public = conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.schemata "
                        "WHERE schema_name = 'public'"
                    )
                ).scalar_one()
        except Exception as e:
            # Cheap-green reviewer item 2026-09-03: probe-failure path
            # leaks the engine if we don't dispose before skipping. The
            # bare ``pytest.skip`` would leave the engine with an open
            # connection pool hanging — leaks the FDs across the test
            # session and warns under SQLAlchemy 2.x with
            # ``_ConnectionClosedError`` on next connect. Dispose first.
            try:
                pg_engine.dispose()
            except Exception:
                pass
            pytest.skip(f"PostgreSQL not available at {_PG_URL}: {e}")
        if not has_public:
            # Same leak risk on the no-public-schema branch.
            try:
                pg_engine.dispose()
            except Exception:
                pass
            pytest.skip(
                f"PostgreSQL test database at {_PG_URL} is not prepared: "
                "no 'public' schema (start the docker-compose.test.yml "
                "stack to provision it; parity asserted on SQLite only "
                "in this environment)"
            )

        sq_engine = create_engine(
            f"sqlite:///{tmp_path / 'parity.db'}",
            connect_args={"check_same_thread": False},
        )
        try:
            # Create only the missing tables on PG; remember which ones
            # so teardown never drops a pre-existing table.
            inspector = sa_inspect(pg_engine)
            missing = [
                t for t in _PARITY_TABLES
                if not inspector.has_table(t.name)
            ]
            SQLModel.metadata.create_all(pg_engine, tables=missing)
            SQLModel.metadata.create_all(sq_engine, tables=list(_PARITY_TABLES))

            job_repo_sq = JobRepository(sq_engine)
            job_repo_pg = JobRepository(pg_engine)

            def seed(engine, scenario, status, admission_state, job_type):
                self._insert_instance(
                    engine,
                    instance_id=f"inst-{scenario}",
                    project_id=f"proj-{scenario}",
                    status=status,
                )
                self._insert_queue(
                    engine,
                    queue_id=f"queue-par-{scenario}",
                    project_id=f"proj-{scenario}",
                    queue_type="parallel",
                )
                self._insert_job(
                    engine,
                    job_id=f"job-{scenario}",
                    instance_id=f"inst-{scenario}",
                    project_id=f"proj-{scenario}",
                    queue_id=f"queue-par-{scenario}",
                    admission_state=admission_state,
                    job_type=job_type,
                )

            # (scenario, instance status, admission_state, job_type,
            #  expected busy)
            scenarios = [
                # The fix: settled mirror of a live mission blocks.
                ("fix", InstanceStatus.WAITING_CHILDREN.value,
                 AdmissionState.DONE.value, "message", True),
                # Baseline #2: settled mirror of a TERMINAL instance is
                # idle — NO over-blocking.
                ("terminal", InstanceStatus.COMPLETED.value,
                 AdmissionState.DONE.value, "message", False),
                # Baseline #3: active mirror of a live instance blocks.
                ("active", InstanceStatus.WAITING_CHILDREN.value,
                 AdmissionState.ACTIVE.value, "message", True),
                # Message-only restriction: done task-type job stays idle.
                ("tasktype", InstanceStatus.RUNNING.value,
                 AdmissionState.DONE.value, "task", False),
                # W2 pin: PAUSED is non-terminal → busy.
                ("paused", InstanceStatus.PAUSED.value,
                 AdmissionState.DONE.value, "message", True),
            ]

            for scenario, status, admission, job_type, expected in scenarios:
                for engine in (sq_engine, pg_engine):
                    seed(engine, scenario, status, admission, job_type)

                project = f"proj-{scenario}"
                results: dict[str, dict[str, bool]] = {}
                for label, repo in (("sqlite", job_repo_sq), ("pg", job_repo_pg)):
                    results[label] = {
                        "defer_project": repo.has_active_non_deferred_work(project),
                        "defer_system": repo.has_active_non_deferred_work(None),
                        "background": repo.has_active_non_background_work(None),
                    }

                sq = results["sqlite"]
                pg = results["pg"]
                for key in ("defer_project", "defer_system", "background"):
                    assert sq[key] is expected, (
                        f"scenario={scenario} key={key}: sqlite returned "
                        f"{sq[key]!r}, expected {expected}"
                    )
                    assert pg[key] is expected, (
                        f"scenario={scenario} key={key}: pg returned "
                        f"{pg[key]!r}, expected {expected}"
                    )
                    # Parity is the point of the test — spell it out.
                    assert type(sq[key]) is type(pg[key]) is bool

                self._clear_work_tables(sq_engine)
                self._clear_work_tables(pg_engine)
        finally:
            sq_engine.dispose()
            try:
                self._clear_work_tables(pg_engine)
            except Exception:
                pass
            pg_engine.dispose()
