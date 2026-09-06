"""Targeted end-to-end probe of JobProcessor._defer_idle_check.

Confirms the audit-brief scenario flows through the actual Gate A
probe path (JobProcessor._defer_idle_check), not just the repository
predicates in isolation. The probe path is the one called in
production by the JobProcessor._process_next_job() loop.

The probe path semantics (per daemon/services/job_processor.py:213):

  1. Consult JobRepository.has_active_non_deferred_work(project_id).
     Truthy ⇒ return 1 (gate blocks).
  2. Otherwise consult TaskRepository.has_active_non_deferred_work(
     project_id). Truthy ⇒ return 1.
  3. Otherwise return 0 (gate says "idle" — defer queue may admit).

A 0 return means the defer queue would wrongly admit the next job.
"""

import asyncio
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel
from unittest.mock import MagicMock

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_processor import JobProcessor
from daemon.repositories import SQLModelProjectRepository


def _insert_instance(engine, *, instance_id, project_id, status):
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(text(
            """
            INSERT INTO instances
                (instance_id, agent_id, agent_dir, status, project_id,
                 created_at, updated_at, version)
            VALUES
                (:instance_id, 'developer', 'agents/developer', :status,
                 :project_id, :created_at, :updated_at, 1)
            """), {"instance_id": instance_id, "status": status,
                   "project_id": project_id, "created_at": now, "updated_at": now})


def _insert_queue(engine, *, queue_id, project_id, queue_type):
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(text(
            """
            INSERT INTO job_queues
                (queue_id, project_id, queue_name, queue_name_lower,
                 queue_type, concurrency_limit, is_system, is_paused,
                 description, created_at, updated_at)
            VALUES
                (:queue_id, :project_id, :queue_name, :queue_name_lower,
                 :queue_type, 1, 0, 0, NULL, :created_at, :updated_at)
            """), {"queue_id": queue_id, "project_id": project_id,
                   "queue_name": queue_id, "queue_name_lower": queue_id.lower(),
                   "queue_type": queue_type, "created_at": now, "updated_at": now})


def _insert_job_item(engine, *, job_id, instance_id, project_id, queue_id,
                     admission_state, job_type="message"):
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(text(
            """
            INSERT INTO job_queue_items
                (job_id, agent_id, agent_dir, message, source,
                 project_id, queue_id, priority, admission_state,
                 created_at, instance_id, job_type, retry_count, metadata)
            VALUES
                (:job_id, 'developer', 'agents/developer', 'test', 'api',
                 :project_id, :queue_id, 0, :admission_state,
                 :created_at, :instance_id, :job_type, 0, '{}')
            """), {"job_id": job_id, "project_id": project_id,
                   "queue_id": queue_id, "admission_state": admission_state,
                   "created_at": now, "instance_id": instance_id,
                   "job_type": job_type})


@pytest.fixture
def fb_engine(tmp_path):
    db_path = tmp_path / "probe.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def test_defer_idle_check_probe_path_with_settled_mirror(tmp_path):
    """JobProcessor._defer_idle_check returns 0 on the audit scenario.

    Direct probe of the Gate A probe path. Constructs the audit-brief
    scenario, instantiates a JobProcessor with the real repository
    stack, and asserts the probe return value.
    """
    db_path = tmp_path / "probe.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)

    try:
        # Construct the scenario.
        _insert_instance(
            eng,
            instance_id="inst-probe",
            project_id="proj-probe",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _insert_queue(
            eng,
            queue_id="queue-probe-par",
            project_id="proj-probe",
            queue_type="parallel",
        )
        _insert_job_item(
            eng,
            job_id="job-probe-mirror",
            instance_id="inst-probe",
            project_id="proj-probe",
            queue_id="queue-probe-par",
            admission_state=AdmissionState.DONE.value,
            job_type="message",
        )

        # Build the JobProcessor with the real repository stack.
        job_repo = JobRepository(eng)
        queue_repo = JobQueueRepository(eng)
        task_repo = TaskRepository(eng)
        lock_manager = JobLockManager(lock_repo=MagicMock())

        # Instance manager mock with the real task_repo wired in.
        im = MagicMock()
        im._task_repo = task_repo

        # Project repo mock — _defer_idle_check doesn't consult it.
        proj_repo = MagicMock(spec=SQLModelProjectRepository)

        proc = JobProcessor(
            queue_service=MagicMock(_repository=job_repo),
            instance_manager=im,
            project_repo=proj_repo,
            queue_repo=queue_repo,
        )

        # Run the actual probe path. ``asyncio.run`` (not the legacy
        # ``get_event_loop().run_until_complete``, which is deprecated
        # without a running loop on Python 3.12+ and removed in 3.14).
        result = asyncio.run(proc._defer_idle_check("proj-probe"))

        # INTENDED: 1 (gate blocks — defer queue must wait).
        # ACTUAL: 0 (gate wrongly says "idle" — the bug).
        assert result == 1, (
            f"JobProcessor._defer_idle_check returned {result} on the "
            f"audit scenario (settled mirror + non-terminal instance). "
            f"Intended return is 1 (blocked). The post-settle defer-gate "
            f"window is REAL at the Gate A probe path."
        )
    finally:
        eng.dispose()


def _make_gate_b_service(eng):
    """Build a JobQueueService with the real repository stack for Gate B.

    Returns (service, queue_repo, job_repo, task_repo, project_id).
    """
    from daemon.services.job_queue_service import JobQueueService

    job_repo = JobRepository(eng)
    queue_repo = JobQueueRepository(eng)
    task_repo = TaskRepository(eng)
    lock_manager = JobLockManager(lock_repo=MagicMock())
    svc = JobQueueService(job_repo, lock_manager, queue_repo)
    im = MagicMock()
    im._task_repo = task_repo
    svc.set_instance_manager(im)
    return svc


def test_gate_b_select_next_eligible_with_settled_mirror(tmp_path):
    """JobQueueService._select_next_eligible_job admits the defer
    candidate on the self-witness scenario — the WS1 carve-out
    excludes the candidate's own settled mirror from the busy-set.

    Direct probe of Gate B (called from the JobProcessor's
    ``_process_next_job`` loop and from the JobQueueService admission
    path). The scenario:

      * Instance ``inst-gate-b`` in ``waiting_children`` (non-terminal,
        REVIVED from COMPLETED by the F7 revive — the defer self-
        witness incident shape).
      * A settled message mirror on a parallel queue, ``admission_state
        ='done'``, ``instance_id='inst-gate-b'`` (the parent mission's
        residual mirror).
      * A queued defer job on the defer queue, ``instance_id=
        'inst-gate-b'`` (same instance as the mirror — the candidate).

    INTENDED (WS1 fix, 2026-09-06): the defer candidate IS admitted —
    its own settled mirror is carved-out from the busy-set, so the
    gate returns False (IDLE) for the candidate. Without WS1 the gate
    wrongly returned the defer candidate as IDLE while the parent
    mission was still live AND the held candidate was its own only
    work — a self-deadlock that no reaper could break.

    This test was originally a Phase-1 RED reproduction (the defer
    queue wrongly admitted; Gate B returned the defer job). WS1 is the
    fix — the defer queue CORRECTLY admits when the candidate's only
    mirror is its own. The test is flipped from RED to GREEN.
    """
    db_path = tmp_path / "probe_gate_b.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)

    try:
        # Construct the audit-brief scenario + a defer job pending.
        _insert_instance(
            eng,
            instance_id="inst-gate-b",
            project_id="proj-gate-b",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _insert_queue(
            eng,
            queue_id="queue-par-gate-b",
            project_id="proj-gate-b",
            queue_type="parallel",
        )
        _insert_queue(
            eng,
            queue_id="queue-defer-gate-b",
            project_id="proj-gate-b",
            queue_type="defer",
        )
        _insert_job_item(
            eng,
            job_id="job-mirror-gate-b",
            instance_id="inst-gate-b",
            project_id="proj-gate-b",
            queue_id="queue-par-gate-b",
            admission_state=AdmissionState.DONE.value,
            job_type="message",
        )

        # Pending defer job (the defer queue's candidate) — DB-resident
        # (Phase-1 review nit): a MagicMock(spec=JobItem) candidate does
        # not exercise the real pending-list shape Gate B consumes; seed
        # the row and hand Gate B the repository's actual pending list.
        _insert_job_item(
            eng,
            job_id="job-defer-pending",
            instance_id="inst-gate-b",
            project_id="proj-gate-b",
            queue_id="queue-defer-gate-b",
            admission_state=AdmissionState.QUEUED.value,
        )

        svc = _make_gate_b_service(eng)
        pending = JobRepository(eng).list_all_pending()
        assert [j.job_id for j in pending] == ["job-defer-pending"], (
            "defer candidate not DB-resident as queued pending work"
        )

        # Run Gate B (modern loop handling — see the Gate A probe note).
        result = asyncio.run(
            svc._select_next_eligible_job(pending, "proj-gate-b")
        )

        # INTENDED (WS1 fix): the defer candidate IS admitted —
        # its own settled mirror does NOT witness against itself.
        assert result is not None and result.job_id == "job-defer-pending", (
            f"JobQueueService._select_next_eligible_job returned {result!r} "
            f"on the self-witness scenario — the WS1 carve-out should "
            f"exclude the candidate's own settled mirror from the "
            f"busy-set, so the defer candidate IS eligible. Got: "
            f"job_id={getattr(result, 'job_id', None)!r}."
        )

        # Sanity: the no-carve-out predicate (requesting the gate with
        # ``requester_instance_id=None``) still sees the mirror as busy
        # — the carve-out is per-candidate, not global.
        gate_no_carveout = asyncio.run(
            asyncio.coroutine(lambda: asyncio.sleep(0))()  # noqa: E731
        ) if False else None
        non_defer_active_no_carveout = asyncio.run(
            _gate_no_carveout(svc, pending, "proj-gate-b")
        )
        assert non_defer_active_no_carveout is True, (
            "no-carve-out gate sees the mirror as busy — the carve-out "
            "must be per-candidate, not global; a global carve-out would "
            "silently re-introduce the AMBER paused-instance witness bug "
            "(the gate would not block other witnesses)"
        )
    finally:
        eng.dispose()


async def _gate_no_carveout(svc, pending, project_id):
    """Helper: call the gate with ``requester_instance_id=None`` for
    the sanity check above. Uses a fresh JobRepository (no WS1 carve-
    out in the repository method when called with no requester)."""
    from daemon.repositories.job_queue.repository import JobRepository

    # The service's repository was built against ``eng`` — call it
    # directly with no requester so the no-carve-out body is selected.
    repo = svc._repository
    # The svc was built against the test engine; its repo engine
    # matches. Just exercise the predicate path with no requester.
    return bool(
        JobRepository(repo.engine).has_active_non_deferred_work(project_id)
    )


def test_gate_b_select_next_eligible_with_other_instance_witness(tmp_path):
    """WS1 cross-instance witness matrix row: other-instance mirrors
    STILL block the defer candidate.

    The carve-out is per-candidate — the candidate's OWN settled
    mirrors are excluded from the busy-set, but other-instance mirrors
    continue to witness. This is the structural contract:
    ``j.instance_id != :requester_instance_id`` excludes mirrors WHERE
    ``j.instance_id = :requester_instance_id`` (self-witness carve-out)
    and INCLUDES mirrors WHERE ``j.instance_id != :requester_instance_id``
    (other-instance witnesses).

    Scenario:

      * Instance ``inst-other`` in ``waiting_children`` with a settled
        message mirror (the busy-set witness).
      * Instance ``inst-cand`` (the defer candidate's instance) in
        ``waiting_children`` with the deferred candidate pending.
      * NO settled mirror on ``inst-cand`` (the candidate has nothing
        to carve out — the gate evaluates honestly for it).

    INTENDED: Gate B returns ``None`` — the other-instance mirror IS
    a busy witness and blocks the defer candidate.
    """
    db_path = tmp_path / "probe_gate_b_other.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)

    try:
        # The OTHER instance: a non-terminal mission with a settled
        # mirror (the busy witness).
        _insert_instance(
            eng,
            instance_id="inst-other",
            project_id="proj-other-witness",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _insert_queue(
            eng,
            queue_id="queue-par-other",
            project_id="proj-other-witness",
            queue_type="parallel",
        )
        _insert_job_item(
            eng,
            job_id="job-mirror-other",
            instance_id="inst-other",
            project_id="proj-other-witness",
            queue_id="queue-par-other",
            admission_state=AdmissionState.DONE.value,
            job_type="message",
        )

        # The CANDIDATE instance: non-terminal, no settled mirror (the
        # candidate has nothing to carve out).
        _insert_instance(
            eng,
            instance_id="inst-cand",
            project_id="proj-other-witness",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _insert_queue(
            eng,
            queue_id="queue-defer-cand",
            project_id="proj-other-witness",
            queue_type="defer",
        )
        _insert_job_item(
            eng,
            job_id="job-defer-cand",
            instance_id="inst-cand",
            project_id="proj-other-witness",
            queue_id="queue-defer-cand",
            admission_state=AdmissionState.QUEUED.value,
        )

        svc = _make_gate_b_service(eng)
        pending = JobRepository(eng).list_all_pending()
        assert [j.job_id for j in pending] == ["job-defer-cand"], (
            "defer candidate not DB-resident as queued pending work"
        )

        # Run Gate B — the WS1 per-candidate carve-out only excludes
        # the candidate's OWN mirror; the OTHER-instance mirror IS a
        # busy witness and blocks the defer candidate.
        result = asyncio.run(
            svc._select_next_eligible_job(pending, "proj-other-witness")
        )

        assert result is None, (
            f"JobQueueService._select_next_eligible_job returned "
            f"{result!r} on the cross-instance witness scenario — the "
            f"OTHER-instance mirror is a busy witness and must block "
            f"the defer candidate. The WS1 carve-out must NOT exclude "
            f"non-self mirrors."
        )
    finally:
        eng.dispose()
