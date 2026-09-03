#!/usr/bin/env python3
"""Defer-Gate Runtime Matrix — behavioral evidence at RUNTIME on the
exact production code path.

Spec: ``.agents/tester/MOCK_TESTS.md`` →
"Mock Test: defer_gate_runtime_matrix (W-round, fix/defer-gate-post-settle-window)".

Branch: ``fix/defer-gate-post-settle-window`` @ ``b46c9f8b`` (defer-gate
post-Fix-B widening; SQL-string unit tests are GREEN at this commit; this
pack proves the gate behaves correctly at RUNTIME on the production
seam — repository predicates, service-level ``_defer_idle_check`` /
``_background_idle_check`` / ``_select_next_eligible_job``, and
``claim_pending_task`` t2 guard).

Five scenarios (S1–S5) pin the gate matrix on REAL repositories with
file-backed SQLite (WAL, NullPool, NEVER StaticPool+WriteGuardSession
tripwire):

  * **S1 defer BLOCKED**: settled mirror (message, done) + non-terminal
    instance → defer candidate on a defer queue NOT admitted.
    Exercised at three layers:
      - LAYER A (job predicate):
        ``JobRepository.has_active_non_deferred_work(project_id) == True``
      - LAYER B (service-level Gate A):
        ``JobProcessor._defer_idle_check(project_id) == 1``
      - LAYER C (service-level Gate B):
        ``JobQueueService._select_next_eligible_job([defer_cand], project_id) is None``

  * **S2 defer ADMITTED**: same shape but instance TERMINAL → defer
    candidate admitted (the gate reads IDLE).
    Exercised at the same three layers + the "candidate returns" assertion.

  * **S3 PAUSED blocked (by-design)**: settled mirror whose instance is
    PAUSED → gate reads BUSY (pinned semantics ``7ecf09e2``: pause =
    suspended-but-occupying, NOT idle; matches the
    ``TaskRepository.has_active_non_deferred_work`` predicate's
    ``status='paused'`` counting posture).
    Exercised at LAYER A + LAYER C.

  * **S4 folding layering proof**: defer candidate PENDING; parent
    message Task COMPLETED; instance waiting_children; mirror done.
      - LAYER C leg: Gate B returns ``None`` (defer branch gated).
      - LAYER D leg: ``TaskRepository.claim_pending_task`` t2 guard
        correctly finds no active non-deferred task and PROCEEDS
        (returns the deferred Task). The two legs document the
        layered model: gate = mission liveness (the fix layer),
        claim = task liveness (orthogonal; correctly identifies the
        deferred Task as claimable in the absence of non-deferred
        peers). Per the spec: "claim proceeding is CORRECT, gate is
        the fix layer".

  * **S5 self-deadlock exclusion**: defer candidate whose OWN instance
    is waiting_children, the candidate's OWN JobItem sits on the defer
    queue → gate admits (queue-type exclusion holds; no self-block).
    Exercised at LAYER A + LAYER C.

Output contract: per-scenario PASS/FAIL line + key evidence rows
(gate return value, JobItem/Task row states, candidate selection).
Final line: ``RESULT: PASS|FAIL|TIMEOUT``; exit 0/1/124.

Self-contained. Internal 150s timeout via ``signal.alarm``; designed to
be wrapped with ``timeout 300`` by the .sh wrapper (dual-layer guard
per the test-pack skill).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

# Ensure repo root on PYTHONPATH so daemon/ resolves when run from anywhere
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

# ─── Real production imports ──────────────────────────────────────────
from daemon.constants import TERMINAL_INSTANCE_STATUSES  # noqa: E402
from daemon.repositories.instance.models import (  # noqa: E402
    Instance,
    InstanceStatus,
)
from daemon.repositories.job_queue import (  # noqa: E402
    JobQueueRepository,
    JobRepository,
    LockRepository,
)
from daemon.repositories.job_queue.models import (  # noqa: E402
    AdmissionState,
    JobItem,
    JobQueue,
    QueueType,
)
from daemon.services.job_lock_manager import (  # noqa: E402
    JobLockManager,
)
from daemon.repositories.task.models import (  # noqa: E402
    Task,
    TaskStatus,
    TaskType,
)
from daemon.repositories.task.repository import TaskRepository  # noqa: E402


# ════════════════════════════════════════════════════════════════════════════
# Result collection + timeout (per test-pack skill convention)
# ════════════════════════════════════════════════════════════════════════════

_RESULTS: list[tuple[str, str, str]] = []  # (scenario, status, evidence)
_OVERALL_PASS = True
_TIMED_OUT = False


def _record(scenario: str, passed: bool, evidence: str) -> None:
    global _OVERALL_PASS
    status = "PASS" if passed else "FAIL"
    if not passed:
        _OVERALL_PASS = False
    _RESULTS.append((scenario, status, evidence))
    print(f"--- {scenario}: {status} ---")
    print(evidence)
    print()


def _alarm_handler(signum, frame):  # noqa: ARG001
    global _TIMED_OUT
    _TIMED_OUT = True
    raise TimeoutError("internal 150s alarm tripped")


# ════════════════════════════════════════════════════════════════════════════
# Per-scenario setup — fresh file-backed SQLite engine per scenario so
# the system-wide background predicate cannot leak rows between scenarios.
# Each scenario builds its own repos and services.
# ════════════════════════════════════════════════════════════════════════════


def _fresh_engine_for(scenario_name: str):
    """Return (engine, db_path, tmp_dir) for one scenario's isolated DB.

    Background sister predicate is system-wide (``project_id`` is `del`'d)
    so cross-scenario rows would leak. Per-scenario isolation keeps each
    S1–S5 self-contained — matches the spec's "Each scenario is
    independent" framing.
    """
    tmp_dir = tempfile.mkdtemp(prefix=f"defer_gate_{scenario_name}_")
    db_path = os.path.join(tmp_dir, "probe.db")
    engine = _create_file_backed_engine(db_path)
    _create_schema(engine)
    return engine, db_path, tmp_dir


def _create_file_backed_engine(db_path: str):
    """File-backed SQLite engine. Default QueuePool (not StaticPool).

    The blueprint tripwire: StaticPool + WriteGuardSession +
    dependency_bus repo interleaved inside one open transaction
    corrupts writes. Production PG unaffected; for SQLite, file-backed
    is the safe choice per Repo & Dev Environment Conventions.
    """
    return create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )


def _create_schema(engine) -> None:
    """Register every model the scenarios touch.

    Mirrors the F11 file-backed recipe in
    ``tests/job_queue/test_defer_gate_post_settle_window.py``. We pull
    in every model the production code paths could touch (real schema,
    no shortcut) so any future additions don't silently leave an empty
    DB behind.
    """
    from daemon.repositories.job_queue import models as _jq_models  # noqa: F401
    from daemon.repositories.task import models as _task_models  # noqa: F401
    from daemon.repositories.instance import models as _inst_models  # noqa: F401
    SQLModel.metadata.create_all(engine)


# ════════════════════════════════════════════════════════════════════════════
# Seed helpers (raw SQL — minimal surface, real schema, real predicates
# observe the rows through SQLAlchemy joins exactly as production does)
# ════════════════════════════════════════════════════════════════════════════


def _insert_instance(
    engine,
    *,
    instance_id: str,
    project_id: str = "proj-defer-gate",
    status: str = InstanceStatus.WAITING_CHILDREN.value,
    agent_id: str = "developer",
) -> None:
    """Insert an Instance row directly via SQL.

    The defer/background predicates' ``LEFT JOIN instances i`` requires
    a matching ``instances`` row for the project/status filter to
    evaluate. The mirror JobItem's ``instance_id`` points here.
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
    engine,
    *,
    queue_id: str,
    project_id: str,
    queue_type: str = "parallel",
    queue_name: str | None = None,
    concurrency_limit: int = 1,
) -> None:
    """Insert a JobQueue row directly via SQL.

    Defer and background queues enforce ``concurrency_limit = 1`` via
    a Python validator (``JobQueue.enforce_defer_concurrency_limit``),
    but the raw-SQL path bypasses validators — so we always pass
    ``concurrency_limit=1`` for those queue types to keep the
    CheckConstraint green.
    """
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
    engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str,
    queue_id: str,
    admission_state: str,
    job_type: str = "task",
    deleted_at: str | None = None,
) -> None:
    """Insert a JobItem row directly via SQL.

    Default ``job_type='task'`` per W7 fixture realism: the realistic
    shape of defer/background candidates in production is ``task``-type.
    Callers must stamp ``job_type='message'`` explicitly when the
    scenario is specifically a Fix-B settled mirror — the busy-set's
    mirror clause (``j.job_type = 'message' AND j.admission_state =
    'done'``) only matches message mirrors.
    """
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source,
                     project_id, queue_id, priority, admission_state,
                     created_at, instance_id, job_type, retry_count,
                     deleted_at, metadata)
                VALUES
                    (:job_id, :agent_id, :agent_dir, :message, :source,
                     :project_id, :queue_id, :priority, :admission_state,
                     :created_at, :instance_id, :job_type, :retry_count,
                     :deleted_at, :metadata)
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
                "deleted_at": deleted_at,
                "metadata": json.dumps({}),
            },
        )


def _insert_task(
    engine,
    *,
    work_id: str,
    instance_id: str,
    status: str,
    is_deferred: bool = False,
    is_background: bool = False,
    completed_at: datetime | None = None,
) -> int:
    """Insert a Task row directly via SQL. Returns the integer id.

    The defer candidate Task for S4 uses ``is_deferred=True`` to model
    the work_id-bound Task that backs the defer-queue JobItem.
    ``completed_at`` is stamped only for terminal-status rows; the
    column is set in the same INSERT so we never have to issue a second
    UPDATE (a single transaction stays atomic).
    """
    completed_iso = (
        completed_at.isoformat()
        if completed_at is not None
        else None
    )
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO task
                    (work_id, task_type, instance_id, message_id, status,
                     is_deferred, is_background, retry_count,
                     worker_id, started_at, completed_at,
                     cancel_requested, cancel_requested_at,
                     retry_scheduled, next_retry_at,
                     created_at, last_heartbeat_at,
                     suspension_reason, resume_target_turn_id)
                VALUES
                    (:work_id, :task_type, :instance_id, NULL, :status,
                     :is_deferred, :is_background, 0,
                     NULL, NULL, :completed_at,
                     0, NULL,
                     0, NULL,
                     :created_at, NULL,
                     NULL, NULL)
                """
            ),
            {
                "work_id": work_id,
                "task_type": TaskType.PROCESS_MESSAGE.value,
                "instance_id": instance_id,
                "status": status,
                "is_deferred": 1 if is_deferred else 0,
                "is_background": 1 if is_background else 0,
                "completed_at": completed_iso,
                "created_at": now,
            },
        )
        return int(row.lastrowid)


# ════════════════════════════════════════════════════════════════════════════
# Layer factories — REAL services where the production seam is reachable
# ════════════════════════════════════════════════════════════════════════════


def _build_layer_a(job_repo: JobRepository, task_repo: TaskRepository):
    """LAYER A: raw repository predicates.

    The two shared predicates (``has_active_non_deferred_work`` and
    ``has_active_non_background_work``) live on
    ``JobRepository`` / ``TaskRepository``. They return bool. Mirrors
    what the maintenance ``_is_idle`` probe and the layered
    gate composition observe.
    """
    return job_repo, task_repo


def _build_layer_b(
    job_repo: JobRepository,
    task_repo: TaskRepository,
    project_repo: Any,
    queue_repo: JobQueueRepository,
    jq_service: Any,
):
    """LAYER B: service-level ``_defer_idle_check`` / ``_background_idle_check``.

    Construct a real ``JobProcessor`` whose dependencies point at the
    real repositories on the file-backed engine. The processor is
    built via ``__new__`` to skip the daemon-wiring ``__init__`` —
    we set the attributes the gate methods actually read. No polling
    loop is started; only the two gate methods are invoked.
    """
    from daemon.services.job_processor import JobProcessor  # noqa: PLC0415

    proc = JobProcessor.__new__(JobProcessor)
    # The two gate methods read: queue_service._repository (the
    # JobRepository) and instance_manager._task_repo. Wiring them
    # at the canonical seams (the same pattern the production
    # ``InstanceManager.initialize`` uses) lets the gates run with
    # no test-only Mock detection.
    proc._queue_service = jq_service
    proc._instance_manager = MagicMock()
    proc._instance_manager._task_repo = task_repo
    proc._project_repo = project_repo
    proc._queue_repo = queue_repo
    # The processor's __init__ also touches these — harmless for the
    # two gate methods, but be explicit so no incidental attribute
    # access raises.
    proc._dispatch_bus = None
    proc._event_dispatch_enabled = False
    proc._job_feedback_observer = None
    return proc


def _build_layer_c(
    job_repo: JobRepository,
    queue_repo: JobQueueRepository,
    task_repo: TaskRepository | None = None,
):
    """LAYER C: service-level ``_select_next_eligible_job``.

    Construct a real ``JobQueueService``. The selector consults
    ``_repository`` (JobRepository) for the job predicate and
    ``_instance_manager._task_repo`` (TaskRepository) for the task
    predicate. We wire a minimal ``_instance_manager`` MagicMock that
    exposes ``_task_repo`` exactly the way the production manager
    does.
    """
    from daemon.services.job_queue_service import JobQueueService  # noqa: PLC0415
    from daemon.services.job_lock_manager import JobLockManager  # noqa: PLC0415

    lock_repo = LockRepository(engine=job_repo.engine)
    lock_manager = JobLockManager(lock_repo=lock_repo)
    svc = JobQueueService(
        job_repo, lock_manager, queue_repo, instance_manager=None,
    )
    if task_repo is not None:
        # Phase 1 wiring: ``set_instance_manager`` keeps the
        # instance_manager reference; the selector reaches the
        # task_repo via ``instance_manager._task_repo`` exactly the way
        # ``tests/job_queue/test_select_next_eligible_job.py`` does
        # it.
        mgr = MagicMock()
        mgr._task_repo = task_repo
        svc.set_instance_manager(mgr)
    return svc


# ════════════════════════════════════════════════════════════════════════════
# Per-scenario helpers
# ════════════════════════════════════════════════════════════════════════════


def _fetch_pending_jobs_for_project(job_repo: JobRepository, project_id: str):
    """Return the SQLModel-level JobItem list for the project.

    Used to feed ``_select_next_eligible_job`` with a real pending
    list (the selector accepts JobItem instances and only reads
    ``queue_id`` / ``project_id``).
    """
    return job_repo.list_pending_by_project(project_id)


def _build_defer_candidate(
    *,
    project_id: str,
    queue_id: str,
    job_id: str = "job-defer-cand",
    instance_id: str | None = None,
) -> JobItem:
    """Build a queued JobItem on the defer queue — the candidate.

    Default ``instance_id`` is None: the candidate's instance is
    scoped via the deferred Task, not the JobItem. This matches the
    pattern where a defer-queue JobItem has ``instance_id`` set
    after the claim path resolves the work_id to an instance.
    """
    return JobItem(
        job_id=job_id,
        agent_id="developer",
        agent_dir="agents/developer",
        message="defer candidate",
        source="api",
        project_id=project_id,
        queue_id=queue_id,
        priority=0,
        admission_state=AdmissionState.QUEUED.value,
        job_type="task",
        instance_id=instance_id,
    )


# ════════════════════════════════════════════════════════════════════════════
# Scenario S1 — defer BLOCKED (settled mirror + non-terminal instance)
# ════════════════════════════════════════════════════════════════════════════


async def scenario_s1_defer_blocked() -> None:
    """S1 defer BLOCKED.

    Spec: "S1 defer BLOCKED: project P has a settled mirror
    (JobItem job_type='message', admission_state='done', instance_id
    set, on a NON-defer queue, non-deleted) whose instance is
    NON-TERMINAL (waiting_children) → defer candidate on the defer
    queue: ``_defer_idle_check``-equivalent
    (``has_active_non_deferred_work``) returns BUSY AND
    ``_select_next_eligible_job`` defer branch returns None."

    Exercises:
      - LAYER A: ``JobRepository.has_active_non_deferred_work`` == True
      - LAYER B: ``JobProcessor._defer_idle_check`` == 1
      - LAYER C: ``JobQueueService._select_next_eligible_job`` defers None
    """
    scenario = "S1 defer BLOCKED (settled mirror + non-terminal + defer candidate)"
    engine, db_path, tmp_dir = _fresh_engine_for("s1")
    try:
        project_id = "proj-s1"
        # ── Seed: parallel queue with settled message mirror + defer queue
        queue_repo = JobQueueRepository(engine)
        # The defer queue (the candidate's lane).
        defer_queue = queue_repo.create(
            project_id=project_id,
            queue_name="queue-defer",
            queue_type=QueueType.DEFER.value,
            concurrency_limit=1,
        )
        # The non-defer queue that holds the settled mirror.
        parallel_queue = queue_repo.create(
            project_id=project_id,
            queue_name="queue-parallel",
            queue_type=QueueType.PARALLEL.value,
            concurrency_limit=1,
        )

        # Instance in waiting_children (non-terminal).
        _insert_instance(
            engine,
            instance_id="inst-s1",
            project_id=project_id,
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        # Settled mirror: message + done + linked instance NON-TERMINAL.
        _insert_job_item(
            engine,
            job_id="job-mirror-s1",
            instance_id="inst-s1",
            project_id=project_id,
            queue_id=parallel_queue.queue_id,
            admission_state=AdmissionState.DONE.value,  # settled at T0
            job_type="message",                          # mirror
        )

        # ── Build the production seam — real repos + real services
        job_repo = JobRepository(engine)
        task_repo = TaskRepository(engine)
        proj_repo = MagicMock()
        layer_c = _build_layer_c(job_repo, queue_repo, task_repo=task_repo)
        layer_b = _build_layer_b(
            job_repo, task_repo, proj_repo, queue_repo, layer_c,
        )

        # ── LAYER A: raw repository predicate
        layer_a_predicate = job_repo.has_active_non_deferred_work(project_id)
        # Background predicate: also busy (same mirror clause).
        layer_a_background = job_repo.has_active_non_background_work(project_id)

        # ── LAYER B: service-level _defer_idle_check
        layer_b_gate = await layer_b._defer_idle_check(project_id)
        # Background sister for completeness.
        layer_b_background_gate = await layer_b._background_idle_check()

        # ── LAYER C: _select_next_eligible_job with the defer candidate
        defer_candidate = _build_defer_candidate(
            project_id=project_id,
            queue_id=defer_queue.queue_id,
            job_id="job-defer-cand-s1",
            instance_id="inst-s1",
        )
        layer_c_selected = await layer_c._select_next_eligible_job(
            [defer_candidate], project_id,
        )

        ev: list[str] = []
        passed = True

        if layer_a_predicate is True:
            ev.append(
                f"OK LAYER A (job predicate): "
                f"has_active_non_deferred_work({project_id!r}) == True"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LAYER A: predicate returned {layer_a_predicate!r}, "
                f"expected True (settled mirror + non-terminal instance "
                f"must trip the post-Fix-B widening clause)"
            )

        if layer_a_background is True:
            ev.append(
                f"OK LAYER A (background sister): "
                f"has_active_non_background_work() == True"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LAYER A (background): returned {layer_a_background!r}, "
                f"expected True"
            )

        if layer_b_gate == 1:
            ev.append(
                f"OK LAYER B (Gate A service): "
                f"_defer_idle_check({project_id!r}) == 1 (BUSY)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LAYER B: _defer_idle_check returned {layer_b_gate!r}, "
                f"expected 1"
            )

        if layer_b_background_gate == 1:
            ev.append(
                "OK LAYER B (background sister): "
                "_background_idle_check() == 1 (BUSY)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LAYER B (background): returned "
                f"{layer_b_background_gate!r}, expected 1"
            )

        if layer_c_selected is None:
            ev.append(
                "OK LAYER C (Gate B service): "
                "_select_next_eligible_job([defer_candidate]) is None "
                "(defer branch blocked by Gate B)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LAYER C: defer branch returned "
                f"{getattr(layer_c_selected, 'job_id', layer_c_selected)!r}, "
                f"expected None"
            )

        # Cross-check terminal_statuses: the constant sourced from
        # daemon.constants must match the canonical set the SQL body
        # uses — single-source guard.
        ev.append(
            f"INFO: TERMINAL_INSTANCE_STATUSES = "
            f"{sorted(TERMINAL_INSTANCE_STATUSES)}"
        )
        ev.append(
            f"INFO: defer candidate (job_id={defer_candidate.job_id}) "
            f"on queue_id={defer_queue.queue_id} (queue_type=defer)"
        )

        _record(scenario, passed, "\n".join(ev))
    except Exception as e:
        _record(
            scenario, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}",
        )
    finally:
        engine.dispose()
        _cleanup(tmp_dir, db_path)


# ════════════════════════════════════════════════════════════════════════════
# Scenario S2 — defer ADMITTED (settled mirror + terminal instance)
# ════════════════════════════════════════════════════════════════════════════


async def scenario_s2_defer_admitted() -> None:
    """S2 defer ADMITTED.

    Spec: "S2 defer ADMITTED: same shape but instance is TERMINAL
    (completed) → gate reads IDLE, candidate admitted via real claim
    path."

    Exercises:
      - LAYER A: predicate == False (terminal instance → idle)
      - LAYER B: _defer_idle_check == 0
      - LAYER C: _select_next_eligible_job returns the defer candidate
    """
    scenario = "S2 defer ADMITTED (settled mirror + TERMINAL instance)"
    engine, db_path, tmp_dir = _fresh_engine_for("s2")
    try:
        project_id = "proj-s2"
        queue_repo = JobQueueRepository(engine)
        defer_queue = queue_repo.create(
            project_id=project_id,
            queue_name="queue-defer",
            queue_type=QueueType.DEFER.value,
            concurrency_limit=1,
        )
        parallel_queue = queue_repo.create(
            project_id=project_id,
            queue_name="queue-parallel",
            queue_type=QueueType.PARALLEL.value,
            concurrency_limit=1,
        )

        # TERMINAL instance — the gate's success case.
        _insert_instance(
            engine,
            instance_id="inst-s2",
            project_id=project_id,
            status=InstanceStatus.COMPLETED.value,
        )
        _insert_job_item(
            engine,
            job_id="job-mirror-s2",
            instance_id="inst-s2",
            project_id=project_id,
            queue_id=parallel_queue.queue_id,
            admission_state=AdmissionState.DONE.value,
            job_type="message",
        )

        job_repo = JobRepository(engine)
        task_repo = TaskRepository(engine)
        proj_repo = MagicMock()
        layer_c = _build_layer_c(job_repo, queue_repo, task_repo=task_repo)
        layer_b = _build_layer_b(
            job_repo, task_repo, proj_repo, queue_repo, layer_c,
        )

        # LAYER A
        layer_a_predicate = job_repo.has_active_non_deferred_work(project_id)
        layer_a_background = job_repo.has_active_non_background_work(project_id)

        # LAYER B
        layer_b_gate = await layer_b._defer_idle_check(project_id)
        layer_b_background_gate = await layer_b._background_idle_check()

        # LAYER C
        defer_candidate = _build_defer_candidate(
            project_id=project_id,
            queue_id=defer_queue.queue_id,
            job_id="job-defer-cand-s2",
            instance_id="inst-s2",
        )
        layer_c_selected = await layer_c._select_next_eligible_job(
            [defer_candidate], project_id,
        )

        ev: list[str] = []
        passed = True

        if layer_a_predicate is False:
            ev.append(
                f"OK LAYER A (job predicate): "
                f"has_active_non_deferred_work({project_id!r}) == False (IDLE)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LAYER A: predicate returned {layer_a_predicate!r}, "
                f"expected False — settled mirror of TERMINAL instance "
                f"must be idle"
            )

        if layer_a_background is False:
            ev.append(
                "OK LAYER A (background sister): "
                "has_active_non_background_work() == False (IDLE)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LAYER A (background): returned "
                f"{layer_a_background!r}, expected False"
            )

        if layer_b_gate == 0:
            ev.append(
                f"OK LAYER B (Gate A service): "
                f"_defer_idle_check({project_id!r}) == 0 (IDLE)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LAYER B: _defer_idle_check returned {layer_b_gate!r}, "
                f"expected 0"
            )

        if layer_b_background_gate == 0:
            ev.append(
                "OK LAYER B (background sister): "
                "_background_idle_check() == 0 (IDLE)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LAYER B (background): returned "
                f"{layer_b_background_gate!r}, expected 0"
            )

        if (
            layer_c_selected is not None
            and getattr(layer_c_selected, "job_id", None)
            == defer_candidate.job_id
        ):
            ev.append(
                f"OK LAYER C (Gate B service): "
                f"_select_next_eligible_job returned the defer candidate "
                f"(job_id={layer_c_selected.job_id})"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LAYER C: returned "
                f"{getattr(layer_c_selected, 'job_id', layer_c_selected)!r}, "
                f"expected {defer_candidate.job_id!r}"
            )

        ev.append(
            f"INFO: instance.status=COMPLETED, mirror "
            f"(job_type=message, admission_state=done) is "
            f"genuinely-done work — the gate must admit"
        )

        _record(scenario, passed, "\n".join(ev))
    except Exception as e:
        _record(
            scenario, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}",
        )
    finally:
        engine.dispose()
        _cleanup(tmp_dir, db_path)


# ════════════════════════════════════════════════════════════════════════════
# Scenario S3 — PAUSED blocked (by-design)
# ════════════════════════════════════════════════════════════════════════════


async def scenario_s3_paused_blocked() -> None:
    """S3 PAUSED blocked (by-design).

    Spec: "S3 PAUSED blocked (by-design): settled mirror whose
    instance is PAUSED → gate reads BUSY (pinned semantics
    ``7ecf09e2``)."

    Pinned semantics: ``7ecf09e2`` — pause is suspended-but-occupying,
    NOT idle. A paused ``Instance`` is NOT in the terminal set
    (``completed`` / ``error`` / ``terminated`` / ``failed``), so its
    JobItem IS counted as active non-deferred work and the defer queue
    stays held. The TaskRepository sister predicate counts
    ``status='paused'`` as blocking for the same reason.

    The ``JOB_TERMINAL_STATUSES`` shared constant lives in
    ``daemon/repositories/job_queue/_idle_predicate_sql.py`` and is
    sourced from ``daemon.constants.TERMINAL_INSTANCE_STATUSES`` —
    the SQL body uses ``i.status NOT IN :terminal_statuses``, so
    ``paused`` is intentionally NOT terminal.

    Exercises:
      - LAYER A: predicate == True (pause counts as busy)
      - LAYER C: _select_next_eligible_job defers None
    """
    scenario = "S3 PAUSED blocked (by-design, 7ecf09e2)"
    engine, db_path, tmp_dir = _fresh_engine_for("s3")
    try:
        project_id = "proj-s3"
        queue_repo = JobQueueRepository(engine)
        defer_queue = queue_repo.create(
            project_id=project_id,
            queue_name="queue-defer",
            queue_type=QueueType.DEFER.value,
            concurrency_limit=1,
        )
        parallel_queue = queue_repo.create(
            project_id=project_id,
            queue_name="queue-parallel",
            queue_type=QueueType.PARALLEL.value,
            concurrency_limit=1,
        )

        # PAUSED instance — paused is NOT terminal.
        _insert_instance(
            engine,
            instance_id="inst-s3",
            project_id=project_id,
            status=InstanceStatus.PAUSED.value,
        )
        _insert_job_item(
            engine,
            job_id="job-mirror-s3",
            instance_id="inst-s3",
            project_id=project_id,
            queue_id=parallel_queue.queue_id,
            admission_state=AdmissionState.DONE.value,
            job_type="message",
        )

        job_repo = JobRepository(engine)
        task_repo = TaskRepository(engine)
        layer_c = _build_layer_c(job_repo, queue_repo, task_repo=task_repo)

        # LAYER A — pause must count as busy (the SQL ``NOT IN
        # :terminal_statuses`` clause excludes paused).
        layer_a_predicate = job_repo.has_active_non_deferred_work(project_id)
        layer_a_background = job_repo.has_active_non_background_work(project_id)

        # LAYER C — defer must NOT be admitted.
        defer_candidate = _build_defer_candidate(
            project_id=project_id,
            queue_id=defer_queue.queue_id,
            job_id="job-defer-cand-s3",
            instance_id="inst-s3",
        )
        layer_c_selected = await layer_c._select_next_eligible_job(
            [defer_candidate], project_id,
        )

        ev: list[str] = []
        passed = True

        # Cross-check: paused is NOT in the terminal set the SQL uses.
        if "paused" not in TERMINAL_INSTANCE_STATUSES:
            ev.append(
                "OK pre-check: 'paused' NOT in TERMINAL_INSTANCE_STATUSES "
                "(the canonical set the SQL body uses)"
            )
        else:
            passed = False
            ev.append(
                "FAIL pre-check: 'paused' is in TERMINAL_INSTANCE_STATUSES — "
                "the pause-fix invariant is broken upstream"
            )

        if layer_a_predicate is True:
            ev.append(
                f"OK LAYER A: has_active_non_deferred_work({project_id!r}) "
                f"== True (paused instance counts as BUSY)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LAYER A: predicate returned {layer_a_predicate!r}, "
                f"expected True (pause = suspended-but-occupying)"
            )

        if layer_a_background is True:
            ev.append(
                "OK LAYER A (background): has_active_non_background_work() "
                "== True (paused instance counts as BUSY)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LAYER A (background): returned "
                f"{layer_a_background!r}, expected True"
            )

        if layer_c_selected is None:
            ev.append(
                "OK LAYER C: _select_next_eligible_job returned None "
                "(defer branch blocked — pause is busy)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LAYER C: returned "
                f"{getattr(layer_c_selected, 'job_id', layer_c_selected)!r}, "
                f"expected None (the 2026-07-17 pause-fix invariant)"
            )

        ev.append(
            "INFO: pause-fix invariant (W2, 7ecf09e2) — a paused instance "
            "is suspended-but-occupying, NOT idle. The defer queue must "
            "wait for the instance to leave the PAUSED state."
        )

        _record(scenario, passed, "\n".join(ev))
    except Exception as e:
        _record(
            scenario, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}",
        )
    finally:
        engine.dispose()
        _cleanup(tmp_dir, db_path)


# ════════════════════════════════════════════════════════════════════════════
# Scenario S4 — folding layering proof
# ════════════════════════════════════════════════════════════════════════════


async def scenario_s4_folding_layering_proof() -> None:
    """S4 folding layering proof.

    Spec: "S4 folding layering proof: defer candidate pending; parent
    message Task COMPLETED; instance waiting_children; mirror done →
    Gate B ``_select_next_eligible_job`` defer branch returns None
    (gate blocks on mission liveness) WHILE ``claim_pending_task`` t2
    guard correctly finds NO active task and proceeds — documenting
    the two-leg layering (gate = mission liveness, claim = task
    liveness; claim proceeding is CORRECT, gate is the fix layer)."

    Setup:
      * Instance waiting_children.
      * Settled mirror (message, done) on a parallel queue.
      * Parent message Task COMPLETED on the same instance.
      * Defer candidate JobItem (queued, on defer queue).
      * Defer candidate Task (PENDING, is_deferred=True) — same
        instance. NO JobItem linked (we seed it via the report-task
        path: a Task without a JobItem makes the queue-awareness
        guard in claim_pending_task pass; the t2 guard is the only
        defer-relevant gate).

    Leg 1 (LAYER C): _select_next_eligible_job returns None.
    Leg 2 (LAYER D): claim_pending_task t2 guard correctly finds no
    active non-deferred task and PROCEEDS — the claim path correctly
    identifies the deferred Task as claimable in the absence of
    non-deferred peers. This is the layering proof: gate blocks on
    mission liveness; claim guard correctly identifies task
    liveness.
    """
    scenario = "S4 folding layering proof (gate vs claim — two legs)"
    engine, db_path, tmp_dir = _fresh_engine_for("s4")
    try:
        project_id = "proj-s4"
        queue_repo = JobQueueRepository(engine)
        defer_queue = queue_repo.create(
            project_id=project_id,
            queue_name="queue-defer",
            queue_type=QueueType.DEFER.value,
            concurrency_limit=1,
        )
        parallel_queue = queue_repo.create(
            project_id=project_id,
            queue_name="queue-parallel",
            queue_type=QueueType.PARALLEL.value,
            concurrency_limit=1,
        )

        # Instance waiting_children (non-terminal, non-paused).
        _insert_instance(
            engine,
            instance_id="inst-s4",
            project_id=project_id,
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        # Settled mirror (message, done).
        _insert_job_item(
            engine,
            job_id="job-mirror-s4",
            instance_id="inst-s4",
            project_id=project_id,
            queue_id=parallel_queue.queue_id,
            admission_state=AdmissionState.DONE.value,
            job_type="message",
        )

        # Parent message Task COMPLETED.
        _insert_task(
            engine,
            work_id="job-mirror-s4",
            instance_id="inst-s4",
            status=TaskStatus.COMPLETED.value,
            is_deferred=False,
            completed_at=datetime.now(timezone.utc),
        )

        # Defer candidate JobItem (on defer queue, queued).
        defer_candidate = _build_defer_candidate(
            project_id=project_id,
            queue_id=defer_queue.queue_id,
            job_id="job-defer-cand-s4",
            instance_id="inst-s4",
        )
        # The defer candidate JobItem is needed for leg 1 (the
        # selector walks the pending list). For leg 2 the claim path
        # examines the Task's work_id — so we deliberately do NOT
        # seed a separate JobItem for the deferred Task (this is the
        # "report-task / direct-queue" path the queue-awareness guard
        # passes through cleanly).
        with engine.begin() as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                text(
                    """
                    INSERT INTO job_queue_items
                        (job_id, agent_id, agent_dir, message, source,
                         project_id, queue_id, priority, admission_state,
                         created_at, instance_id, job_type, retry_count,
                         deleted_at, metadata)
                    VALUES
                        (:job_id, :agent_id, :agent_dir, :message, :source,
                         :project_id, :queue_id, :priority, :admission_state,
                         :created_at, :instance_id, :job_type, 0,
                         NULL, :metadata)
                    """
                ),
                {
                    "job_id": defer_candidate.job_id,
                    "agent_id": "developer",
                    "agent_dir": "agents/developer",
                    "message": "defer candidate s4",
                    "source": "api",
                    "project_id": project_id,
                    "queue_id": defer_queue.queue_id,
                    "priority": 0,
                    "admission_state": AdmissionState.QUEUED.value,
                    "created_at": now,
                    "instance_id": "inst-s4",
                    "job_type": "task",
                    "metadata": json.dumps({}),
                },
            )

        # The deferred Task — no JobItem bound (work_id is unique;
        # the parent mirror is keyed on job-mirror-s4). The
        # report-task / direct-queue path.
        deferred_task_id = _insert_task(
            engine,
            work_id="task-defer-s4",
            instance_id="inst-s4",
            status=TaskStatus.PENDING.value,
            is_deferred=True,
        )

        job_repo = JobRepository(engine)
        task_repo = TaskRepository(engine)
        # LAYER C — selector.
        layer_c = _build_layer_c(job_repo, queue_repo, task_repo=task_repo)

        # ── LEG 1: Gate B returns None (defer branch blocked by
        #    mission liveness — mirror + non-terminal instance).
        pending = _fetch_pending_jobs_for_project(job_repo, project_id)
        layer_c_selected = await layer_c._select_next_eligible_job(
            pending, project_id,
        )

        # ── LEG 2: claim_pending_task t2 guard finds no active
        #    non-deferred task → proceeds.
        claimed = task_repo.claim_pending_task(worker_id="worker-s4")

        ev: list[str] = []
        passed = True

        # Leg 1 assertion
        if layer_c_selected is None:
            ev.append(
                "OK LEG 1 (LAYER C — Gate B): "
                "_select_next_eligible_job([defer_cand], project) is None "
                "(gate blocks on mission liveness; the fix layer)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LEG 1: Gate B returned "
                f"{getattr(layer_c_selected, 'job_id', layer_c_selected)!r}, "
                f"expected None (the defer queue must not admit while "
                f"the parent mission is live)"
            )

        # Leg 2 assertion — claim correctly identifies the deferred
        # Task as claimable in the absence of non-deferred peers.
        # The t2 subquery in claim_pending_task looks for non-deferred
        # tasks in (pending, running, paused) — the parent mirror
        # Task is COMPLETED, so the subquery returns nothing → EXISTS
        # is false → NOT EXISTS = TRUE → guard passes → claim proceeds.
        # Use ``bool(...)`` because SQLite returns INTEGER 0/1 (not
        # Python True/False) — strict identity (``is True``) would
        # fail on the int-1 value.
        if claimed is not None and bool(claimed.is_deferred):
            ev.append(
                f"OK LEG 2 (LAYER D — claim t2 guard): "
                f"claim_pending_task returned the deferred Task "
                f"(id={claimed.id}, work_id={claimed.work_id}, "
                f"status={claimed.status}, is_deferred={bool(claimed.is_deferred)}) "
                f"— t2 guard correctly finds NO active non-deferred task "
                f"and proceeds (correct layering: gate = mission liveness, "
                f"claim = task liveness)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LEG 2: claim_pending_task returned "
                f"{claimed!r}, expected a deferred Task. "
                f"The t2 guard may have over-blocked — should find no "
                f"non-deferred peer Task (parent is COMPLETED)."
            )

        # Sanity: parent's Task COMPLETED in DB.
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT status FROM task WHERE work_id = :wid"
                ),
                {"wid": "job-mirror-s4"},
            ).fetchone()
        if row and row[0] == TaskStatus.COMPLETED.value:
            ev.append(
                "OK LEG 2 sanity: parent message Task in DB is COMPLETED "
                "(the t2 subquery correctly returns nothing)"
            )
        else:
            ev.append(
                f"WARN LEG 2 sanity: parent task status = {row[0] if row else None!r}"
            )

        ev.append(
            "INFO: this two-leg result documents the layered model — "
            "Gate B blocks defer admission on mission liveness (the fix), "
            "and the claim t2 guard independently identifies the deferred "
            "Task as claimable (correct task-liveness enforcement; the gate "
            "is the layer that prevents admission, claim is the layer "
            "that prevents a queued defer job from being skipped if the "
            "gate ever mis-sides)."
        )

        _record(scenario, passed, "\n".join(ev))
    except Exception as e:
        _record(
            scenario, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}",
        )
    finally:
        engine.dispose()
        _cleanup(tmp_dir, db_path)


# ════════════════════════════════════════════════════════════════════════════
# Scenario S5 — self-deadlock exclusion
# ════════════════════════════════════════════════════════════════════════════


async def scenario_s5_self_deadlock_exclusion() -> None:
    """S5 self-deadlock exclusion.

    Spec: "S5 self-deadlock exclusion: defer candidate whose OWN
    instance is waiting_children, the candidate's own row sits on the
    defer queue → gate admits (queue-type exclusion holds; no
    self-block)."

    The shared SQL body in
    ``daemon/repositories/job_queue/_idle_predicate_sql.py`` excludes
    the defer queue from the busy-set (``:excluded_queue_types``
    bind), so a defer candidate cannot witness against itself: its
    own JobItem is filtered out by the SQL ``q.queue_type NOT IN
    :excluded_queue_types`` clause. Same mechanism for background
    queues. Without this, every project with a single defer JobItem
    would deadlock on its own gate.

    Exercises:
      - LAYER A: predicate == False (defer queue is excluded from
        busy-set) even with a defer JobItem present on the same
        project.
      - LAYER C: _select_next_eligible_job returns the defer
        candidate.
    """
    scenario = "S5 self-deadlock exclusion (defer queue excluded from busy-set)"
    engine, db_path, tmp_dir = _fresh_engine_for("s5")
    try:
        project_id = "proj-s5"
        queue_repo = JobQueueRepository(engine)
        defer_queue = queue_repo.create(
            project_id=project_id,
            queue_name="queue-defer",
            queue_type=QueueType.DEFER.value,
            concurrency_limit=1,
        )

        # Instance waiting_children — non-terminal, but the instance
        # is irrelevant: the busy-set excludes the defer queue, so
        # any non-terminal instance tied to a defer JobItem is
        # filtered out by the SQL clause before the busy-set sees it.
        _insert_instance(
            engine,
            instance_id="inst-s5",
            project_id=project_id,
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        # The defer candidate's OWN JobItem sits on the defer queue,
        # linked to the same non-terminal instance. If the busy-set
        # did not exclude defer queues, this row would self-witness
        # and the gate would never release.
        defer_candidate = _build_defer_candidate(
            project_id=project_id,
            queue_id=defer_queue.queue_id,
            job_id="job-self-cand-s5",
            instance_id="inst-s5",
        )
        with engine.begin() as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                text(
                    """
                    INSERT INTO job_queue_items
                        (job_id, agent_id, agent_dir, message, source,
                         project_id, queue_id, priority, admission_state,
                         created_at, instance_id, job_type, retry_count,
                         deleted_at, metadata)
                    VALUES
                        (:job_id, :agent_id, :agent_dir, :message, :source,
                         :project_id, :queue_id, :priority, :admission_state,
                         :created_at, :instance_id, :job_type, 0,
                         NULL, :metadata)
                    """
                ),
                {
                    "job_id": defer_candidate.job_id,
                    "agent_id": "developer",
                    "agent_dir": "agents/developer",
                    "message": "self-defer candidate",
                    "source": "api",
                    "project_id": project_id,
                    "queue_id": defer_queue.queue_id,
                    "priority": 0,
                    "admission_state": AdmissionState.QUEUED.value,
                    "created_at": now,
                    "instance_id": "inst-s5",
                    "job_type": "task",
                    "metadata": json.dumps({}),
                },
            )

        job_repo = JobRepository(engine)
        task_repo = TaskRepository(engine)
        layer_c = _build_layer_c(job_repo, queue_repo, task_repo=task_repo)

        # LAYER A — predicate must NOT see the candidate's own row
        # (defer queue excluded).
        layer_a_predicate = job_repo.has_active_non_deferred_work(project_id)
        layer_a_background = job_repo.has_active_non_background_work(project_id)

        # LAYER C — selector must admit the defer candidate.
        pending = _fetch_pending_jobs_for_project(job_repo, project_id)
        layer_c_selected = await layer_c._select_next_eligible_job(
            pending, project_id,
        )

        ev: list[str] = []
        passed = True

        if layer_a_predicate is False:
            ev.append(
                f"OK LAYER A: has_active_non_deferred_work({project_id!r}) "
                f"== False (defer queue excluded from busy-set — "
                f"no self-block)"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LAYER A: predicate returned {layer_a_predicate!r}, "
                f"expected False — the candidate's own row must NOT "
                f"witness against itself (queue-type exclusion invariant)"
            )

        # Background sister is INFORMATIONAL on S5, NOT a pass/fail
        # assertion. Defer work IS non-background work by design (the
        # 2026-07-23 defer-leak fix: defer lanes hold the background
        # gate), so a single defer JobItem correctly makes the
        # background busy-set True. The spec for S5 only requires the
        # DEFER gate to admit (queue-type exclusion holds); the
        # background sister's True here is the documented defer-leak
        # invariant doing its job — it just isn't what S5 pins.
        if layer_a_background is True:
            ev.append(
                "INFO LAYER A (background sister): "
                "has_active_non_background_work() == True (by-design — "
                "defer work IS non-background work; the 2026-07-23 "
                "defer-leak fix; background sister gate correctly "
                "stays held by the defer JobItem)"
            )
        elif layer_a_background is False:
            ev.append(
                "INFO LAYER A (background sister): "
                "has_active_non_background_work() == False (note: this "
                "would be unexpected if there are no other rows; the "
                "defer-leak invariant says defer work is non-background)"
            )
        else:
            ev.append(
                f"INFO LAYER A (background sister): returned "
                f"{layer_a_background!r} (informational only on S5)"
            )

        if (
            layer_c_selected is not None
            and getattr(layer_c_selected, "job_id", None)
            == defer_candidate.job_id
        ):
            ev.append(
                f"OK LAYER C: _select_next_eligible_job returned the "
                f"defer candidate (job_id={layer_c_selected.job_id})"
            )
        else:
            passed = False
            ev.append(
                f"FAIL LAYER C: returned "
                f"{getattr(layer_c_selected, 'job_id', layer_c_selected)!r}, "
                f"expected {defer_candidate.job_id!r}"
            )

        ev.append(
            f"INFO: defer queue_id={defer_queue.queue_id} is excluded "
            f"by the SQL clause ``q.queue_type NOT IN :excluded_queue_types`` "
            f"(deferred bound in "
            f"``_idle_predicate_sql.DEFER_EXCLUDED_QUEUE_TYPES``); "
            f"the same exclusion holds for background queues."
        )

        _record(scenario, passed, "\n".join(ev))
    except Exception as e:
        _record(
            scenario, False,
            f"EXCEPTION: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}",
        )
    finally:
        engine.dispose()
        _cleanup(tmp_dir, db_path)


# ════════════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════════════


def main() -> int:
    print("=== Test Pack: defer_gate_runtime_matrix ===")
    print(
        "(Defer-Gate Runtime Matrix — behavioral evidence at RUNTIME on "
        "production code path)"
    )
    print("Branch: fix/defer-gate-post-settle-window @ b46c9f8b")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print()

    start = time.monotonic()

    # ── Layer-2 internal timeout (signal-based, 150s per spec)
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(150)

    # ── File-backed SQLite under /tmp (per F11 file-backed recipe;
    # cleaned on exit via tmp_path-style cleanup below)
    tmp_dir = tempfile.mkdtemp(prefix="defer_gate_runtime_matrix_")
    db_path = os.path.join(tmp_dir, "probe.db")
    engine = _create_file_backed_engine(db_path)
    _create_schema(engine)

    print(f"DB: {db_path}")
    print(f"Tables: {sorted(SQLModel.metadata.tables.keys())[:10]}...")
    print()

    try:
        asyncio.run(scenario_s1_defer_blocked())
        asyncio.run(scenario_s2_defer_admitted())
        asyncio.run(scenario_s3_paused_blocked())
        asyncio.run(scenario_s4_folding_layering_proof())
        asyncio.run(scenario_s5_self_deadlock_exclusion())
    except TimeoutError as te:
        elapsed = time.monotonic() - start
        print(f"\nTIMEOUT: internal 150s alarm tripped: {te}")
        print(f"\nRESULT: TIMEOUT (elapsed={elapsed:.1f}s)")
        engine.dispose()
        _cleanup(tmp_dir, db_path)
        return 124
    except Exception as e:
        print(f"\nUNEXPECTED EXCEPTION in scenario runner: {type(e).__name__}: {e}")
        traceback.print_exc()

    elapsed = time.monotonic() - start
    print("=" * 70)
    print(f"Total scenarios: {len(_RESULTS)}")
    print(f"  PASS: {sum(1 for _, s, _ in _RESULTS if s == 'PASS')}")
    print(f"  FAIL: {sum(1 for _, s, _ in _RESULTS if s == 'FAIL')}")
    print(f"Elapsed: {elapsed:.1f}s")
    print()

    if _TIMED_OUT:
        print("RESULT: TIMEOUT")
        return 124
    if _OVERALL_PASS:
        print("RESULT: PASS")
        return 0
    print("RESULT: FAIL")
    return 1


def _cleanup(tmp_dir: str, db_path: str) -> None:
    """Best-effort cleanup of /tmp files; do not raise."""
    import shutil
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
        if os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
