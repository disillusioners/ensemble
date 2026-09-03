"""Reproduce the post-settle defer-gate window — read-only investigation.

**Branch:** fix/defer-gate-post-settle-window @ f77fb892
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

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
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
    job_type: str = "message",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Insert a JobItem row directly via SQL."""
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
        The job predicate's ``j.admission_state = 'active'`` filter
        (``daemon/repositories/job_queue/repository.py:741``) excludes
        the settled mirror, so the LEFT JOIN against a non-terminal
        instance is invisible to the predicate. The gate sees "idle"
        even though the parent mission is still working.
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
            "is still working. The bug is the SQL filter "
            "``j.admission_state = 'active'`` at "
            "daemon/repositories/job_queue/repository.py:741 — it "
            "excludes settled mirrors of non-terminal instances, so "
            "the LEFT JOIN against ``instances.status`` is invisible."
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
            "admit. The terminal-instance filter at "
            "daemon/repositories/job_queue/repository.py:745-749 "
            "should still gate this correctly."
        )

    def test_baseline_active_mirror_of_live_instance_blocks(
        self, fb_engine, job_repo
    ):
        """Baseline (control): an ACTIVE mirror of a LIVE instance
        MUST block — this is the existing-correct case.

        The Phase 2 defer-gate predicate
        (``daemon/repositories/job_queue/repository.py:645-768``) was
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

        Background gate filter is
        ``daemon/repositories/job_queue/repository.py:770-893`` —
        ``has_active_non_background_work`` is system-wide
        (project_id argument is ``del``'d). The Phase 2 background
        predicate uses ``admission_state IN ('queued', 'active')`` —
        broader than the defer predicate's single ``active`` value,
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
            "for the system-wide background gate too. The SQL filter "
            "``j.admission_state IN ('queued', 'active')`` at "
            "daemon/repositories/job_queue/repository.py:863 excludes "
            "settled mirrors."
        )