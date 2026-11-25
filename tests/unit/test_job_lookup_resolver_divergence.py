"""Regression pins — JOB LOOKUP RESOLVER DIVERGENCE (commission 2026-11-22).

Lane: ``sonic/job-resolver-m2`` (module ``mod-pins-and-registry``).
Companion fix lane: ``sonic/job-resolver-m1`` (module 1 — the production fix).

WHAT THIS PACK PINS
-------------------
The job lookup surface (``job_get`` / ``job_continue`` / ``job_inject``) resolves
a work handle through ONE kind-agnostic resolver —
``daemon.services.work_resolver.WorkResolverService.resolve_work`` — which
consults BOTH backing tables:

* ``task`` (worker-pool side / report lane) keyed by ``Task.work_id``;
* ``job_queue_items`` (dispatch-queue side) keyed by the JobItem primary key,
  which IS the work id (``JobItem.job_id == Task.work_id`` — one UUID minted
  once and forwarded to both).

Three row shapes must resolve:

(a) JobItem-only  — the row exists in ``job_queue_items`` and has NO ``task``
    row (the dispatch-queue / message-driven side, and the incident row);
(b) Task-only     — the row exists in ``task`` and has NO JobItem row (the
    report lane: ``process_report`` / ``send_report``);
(c) dual-backed   — both rows share ``work_id == job_id`` (the dispatch flow).

…and the handle a human (or an agent) pastes into a chat must survive ordinary
DECORATION — trailing whitespace, a trailing newline, surrounding quotes,
surrounding backticks — instead of collapsing to "Job not found". The SAME
rule applies to the watch front-end (``watch_mission``), which resolves its
receipt through the very same resolver.

LIVE ANCHOR
-----------
The incident row is the JobItem-only row
``a785828f-9e28-45a0-9e4f-2caa21de281e`` on live:
``admission_state='done'``, ``terminal_reason='completed'``,
``job_type='task'`` (the sibling mirror shape uses ``job_type='message'``).
That exact shape — and that exact id — is seeded here, so a regression in the
JobItem-only branch is caught against the incident's own row shape rather than
against a synthetic one.

HERMETICITY
-----------
In-memory SQLite (``StaticPool``) + the REAL repositories + the REAL
``WorkResolverService`` + the REAL ``JobQueueService`` (with the real resolver
wired through ``set_work_resolver``) + the REAL ``create_job_tools`` /
``create_mission_watch_tools`` factories.
Zero PostgreSQL, zero network, zero daemon, zero live-install contact.

NODE MAP — 61 nodes
-------------------
``TestResolveWorkRowShapes``        **5**  — (a) JobItem-only (task-kind AND
                                            the ``job_type='message'`` mirror
                                            kind), (b) Task-only, (c) dual-backed,
                                            (e) unknown id → ``None``.
``TestResolveWorkIdNormalization``   **14** — (d) 7 decorations (trailing space
                                            / trailing newline / leading space /
                                            surrounding whitespace / double quotes
                                            / single quotes / backticks) ×
                                            {resolves to the SAME record as the
                                            bare id | ANTI-VACUITY: a decorated
                                            UNKNOWN id still → ``None``}.
``TestJobToolLookup``               **23** — (a) ``job_get`` fetches the
                                            JobItem-only row, (b) ``job_continue``
                                            continues the completed JobItem-only
                                            row (the lookup phase passes AND the
                                            continue reaches the enqueue),
                                            (c) ``job_get`` / ``job_continue`` /
                                            ``job_inject`` × 7 decorations — the
                                            FULL decoration set, not just
                                            whitespace.
``TestWatchMissionReceiptResolution`` **15** — ``watch_mission`` resolves a
                                            decorated receipt through the shared
                                            resolver (dispatched receipt: 7
                                            decorations) and arms the pre-dispatch
                                            receipt on the CANONICAL id (7
                                            decorations) + the unknown-target
                                            refusal (1).
``TestAntiVacuityControls``          **4**  — the "Job not found" path survives
                                            the fix (the sibling control).

MUTATION MEANINGFULNESS
-----------------------
Every pin in this file is discriminating. The legs below were MEASURED in
DISPOSABLE extracts (``git archive`` + a copy of this pack) — never in the
live worktree. The measured red-sets are recorded in
``.agents/tester/PACKS.md`` under
``JOB-LOOKUP-RESOLVER-DIVERGENCE — Unit Pack Registration``:

* BASE tree ``faff9c1de`` — 51 passed / 10 failed; the 10 reds are EXACTLY
  the canonical-id keying contract (3 ``job_continue`` wrapper legs + 7
  ``watch_mission`` pre-dispatch legs) that the companion fix lane lands.
  Every other node is green at base.
* COMPANION FIX (mod-1-rev's in-flight tree) — 61/61 GREEN.
* resolver normalization reverted ⇒ 30 NEW REDS
* ``job_continue``'s follow-up lookup re-keyed to the raw id ⇒ 3 NEW REDS
* ``watch_mission``'s pre-dispatch receipt re-keyed to the raw target ⇒
  7 NEW REDS
* JobItem-only branch broken ⇒ 39 NEW REDS

SEAM CONTRACT (what these pins assume of the fix)
--------------------------------------------------
1. ``resolve_work`` normalizes the caller-supplied handle BEFORE its two
   indexed lookups (whitespace + matched quote / bracket / backtick
   wrappers). A decorated id must resolve to the SAME record as the bare id.
2. The tool surface tolerates a decorated id END-TO-END — including
   ``job_continue``'s SECOND lookup (the soft-delete re-check), which must
   not answer "has been deleted" for a row that exists. The re-check is
   keyed on the CANONICAL id the resolver returned.
3. Every KEYED WRITE downstream of a resolver lookup (the watch row) is
   keyed on the CANONICAL id too — a watch row keyed on a decorated handle
   is never resolved by the notifier (``WHERE job_id == work_id``) and is
   therefore a stranded watch.
4. Normalization never FABRICATES a hit: a decorated id for work that does
   not exist still resolves to ``None`` / "Job not found".
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService
from daemon.services.mission_resolver import MissionResolver
from daemon.services.work_resolver import WorkResolverService
from daemon.tools.job_queue import create_job_tools, create_mission_watch_tools


# ── Live incident anchor ────────────────────────────────────────────────────

#: The JobItem-only row observed on live for the "Job not found" incident.
#: Seeded verbatim (same id, same admission/terminal/job_type shape) so the
#: pins reproduce the incident's row rather than a synthetic stand-in.
LIVE_INCIDENT_JOB_ID = "a785828f-9e28-45a0-9e4f-2caa21de281e"

#: The instance that OWNS the watched mission (the mission the receipt
#: belongs to) — seeded live so the mission watch arms instead of
#: short-circuiting on "already terminal".
MISSION_INSTANCE_ID = "inst-mission"

#: The instance that CALLS ``watch_mission`` — the watcher row's
#: ``instance_id`` (a real FK to ``instances.instance_id``).
WATCHER_INSTANCE_ID = "inst-watcher"


# ── Decoration matrix ───────────────────────────────────────────────────────
#
# Each entry renders a DECORATED work id around the bare id. The contract is
# that a decorated id resolves to the SAME work record as the bare id.
# The list is deliberately one decoration per node: a partial implementation
# shows up as ONE named red instead of a single opaque red.

_DECORATIONS: dict[str, Callable[[str], str]] = {
    "trailing_space": lambda wid: f"{wid} ",
    "trailing_newline": lambda wid: f"{wid}\n",
    "leading_space": lambda wid: f" {wid}",
    "surrounding_whitespace": lambda wid: f"  {wid}  ",
    "double_quotes": lambda wid: f'"{wid}"',
    "single_quotes": lambda wid: f"'{wid}'",
    "backticks": lambda wid: f"`{wid}`",
}


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (``StaticPool`` — cross-thread safe).

    Same shape as ``tests/unit/services/test_work_resolver.py::engine``:
    one connection is kept alive for the whole test so the
    ``asyncio.to_thread`` hop inside ``JobQueueService.get_work`` sees the
    same store, and ``PRAGMA foreign_keys=ON`` mirrors the production
    posture.
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
def task_repo(engine: Engine) -> TaskRepository:
    """Real ``TaskRepository`` bound to the in-memory engine."""
    return TaskRepository(engine)


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    """Real ``JobRepository`` bound to the in-memory engine."""
    return JobRepository(engine)


@pytest.fixture
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    """Real ``SQLModelInstanceRepository`` bound to the in-memory engine."""
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def resolver(
    task_repo: TaskRepository,
    job_repo: JobRepository,
    instance_repo: SQLModelInstanceRepository,
) -> WorkResolverService:
    """REAL ``WorkResolverService`` over the three real repositories."""
    return WorkResolverService(task_repo, job_repo, instance_repo)


@pytest.fixture
def job_service(
    engine: Engine,
    job_repo: JobRepository,
    resolver: WorkResolverService,
) -> JobQueueService:
    """REAL ``JobQueueService`` with the REAL resolver wired in.

    The production wiring is ``JobQueueService.set_work_resolver(...)``
    (``daemon/api.py:1688``) — the same seam is exercised here so the
    tool-level legs travel the REAL chain:
    ``tool → JobQueueService.get_work → WorkResolverService.resolve_work →
    repositories``. A stubbed service would let a resolver-layer regression
    hide behind a mock.
    """
    service = JobQueueService(
        repository=job_repo,
        lock_manager=JobLockManager(LockRepository(engine)),
        queue_repo=JobQueueRepository(engine),
        instance_manager=None,
    )
    service.set_work_resolver(resolver)
    return service


# ── Seed helpers ────────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    project_id: str = "test-project",
    agent_id: str = "developer",
    status: str = "running",
) -> str:
    """Insert an ``Instance`` row; returns the ``instance_id``."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                agent_name=agent_id,
                project_id=project_id,
                status=status,
                created_at=now_iso,
                updated_at=now_iso,
                paused_at=None,
                parent_id=None,
            )
        )
        s.commit()
    return instance_id


def _seed_jobitem(
    engine: Engine,
    *,
    job_id: str,
    instance_id: str | None = None,
    admission_state: str = AdmissionState.DONE.value,
    terminal_reason: str | None = "completed",
    job_type: str = "task",
    project_id: str = "test-project",
    agent_id: str = "developer",
    created_at: str | None = None,
) -> str:
    """Insert a ``JobItem`` row; returns the ``job_id``.

    Defaults reproduce the LIVE incident row shape exactly:
    ``admission_state='done'``, ``terminal_reason='completed'``,
    ``job_type='task'``. Pass ``job_type='message'`` for the sibling mirror
    shape (which the resolver maps onto the ``settled`` receipt status).
    """
    created = created_at or datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            JobItem(
                job_id=job_id,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                message="test message",
                source="api",
                project_id=project_id,
                priority=5,
                admission_state=admission_state,
                instance_id=instance_id,
                created_at=created,
                deleted_at=None,
                job_metadata={},
                terminal_reason=terminal_reason,
                job_type=job_type,
            )
        )
        s.commit()
    return job_id


def _seed_task(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
    task_type: str = "process_report",
    status: str = TaskStatus.COMPLETED.value,
    result: str | None = None,
    error: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    created_at: datetime | None = None,
) -> str:
    """Insert a ``Task`` row; returns the ``work_id``."""
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        s.add(
            Task(
                work_id=work_id,
                task_type=task_type,
                instance_id=instance_id,
                message_id=None,
                status=status,
                result=result,
                error=error,
                created_at=created_at or now,
                started_at=started_at,
                completed_at=completed_at,
                is_deferred=False,
            )
        )
        s.commit()
    return work_id


def _seed_dual_backed(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
    result_payload: dict[str, Any],
) -> str:
    """Seed the dispatch-flow shape: a JobItem AND its linked Task.

    The two rows share ``work_id == job_id`` (the one-UUID-minted-once
    linkage contract). The Task carries the execution timing and the
    ``result`` JSON; the JobItem owns ``created_at``.
    """
    _seed_jobitem(engine, job_id=work_id, instance_id=instance_id)
    _seed_task(
        engine,
        work_id=work_id,
        instance_id=instance_id,
        task_type="process_message",
        status=TaskStatus.COMPLETED.value,
        result=json.dumps(result_payload),
        started_at=datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 1, 1, 10, 5, 0, tzinfo=timezone.utc),
    )
    return work_id


# ── Tool helpers ────────────────────────────────────────────────────────────


def _tool_by_name(tools: list[Any], name: str) -> Any:
    """Return the tool named ``name`` from the ``create_job_tools`` list."""
    for t in tools:
        if getattr(t, "name", None) == name or getattr(t, "__name__", None) == name:
            return t
    raise AssertionError(
        f"tool {name!r} not found; available: "
        f"{[getattr(t, 'name', None) or getattr(t, '__name__', None) for t in tools]}"
    )


class _FakeInstanceMeta:
    """The subset of ``Instance`` the job tools read off the manager."""

    def __init__(self, instance_id: str, status: str = "running") -> None:
        self.instance_id = instance_id
        self.status = status


def _make_manager(
    instance_id: str,
    *,
    instance_status: str = "running",
    live_graph_task: bool = True,
) -> MagicMock:
    """Build the manager double the job tools consume.

    NOTE the explicit ``_task_repo = None``: the tools probe
    ``getattr(manager, "_task_repo", None)`` and a MagicMock attribute is
    truthy — the busy pre-check would then fire against a MagicMock
    return value and every leg would fail on the wrong gate.
    """
    manager = MagicMock()
    manager._instance_repository.get = MagicMock(
        return_value=_FakeInstanceMeta(instance_id, instance_status)
    )
    manager._task_repo = None
    manager.mission_record_service = None
    manager.get_agent_tool_revive_count = MagicMock(return_value=0)
    manager.note_agent_tool_revive = MagicMock()
    manager.has_live_graph_task = MagicMock(return_value=live_graph_task)
    manager.set_injection = MagicMock(
        return_value={
            "content": "injected message",
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
    )
    manager.get_injection_count = MagicMock(return_value=1)
    manager.enqueue_message_job = AsyncMock(
        return_value=SimpleNamespace(
            message_id="msg-new", job_id="new-job-1", status="queued"
        )
    )
    manager.enqueue_message = AsyncMock(
        return_value=SimpleNamespace(message_id="msg-inject")
    )
    return manager


# ── (a)(b)(c)(e) — resolver row-shape pins ──────────────────────────────────


class TestResolveWorkRowShapes:
    """``resolve_work`` must resolve every backing-table shape."""

    def test_jobitem_only_row_resolves(self, engine, resolver):
        """(a) JobItem-only row — the LIVE incident row shape.

        No ``task`` row exists for this id: the resolver must still find
        the JobItem and surface it as a ``kind="job"`` record whose
        status comes from ``terminal_reason`` (the durable terminal
        audit), not from the lossy legacy status map.
        """
        _seed_instance(engine, instance_id="inst-live")
        _seed_jobitem(engine, job_id=LIVE_INCIDENT_JOB_ID, instance_id="inst-live")

        record = resolver.resolve_work(LIVE_INCIDENT_JOB_ID)

        assert record is not None, "JobItem-only row must resolve"
        assert record.kind == "job"
        assert record.work_id == LIVE_INCIDENT_JOB_ID
        assert record.status == "completed"
        assert record.instance_id == "inst-live"
        assert record.job_type == "task"

    def test_jobitem_only_mirror_row_resolves_to_settled(self, engine, resolver):
        """(a) JobItem-only row with the sibling ``job_type='message'`` shape.

        A mirror receipt keeps ``admission_state='done'`` /
        ``terminal_reason='completed'`` but surfaces the ``settled``
        receipt status (the M3 per-kind dispatch).
        """
        _seed_instance(engine, instance_id="inst-mirror")
        _seed_jobitem(
            engine,
            job_id=LIVE_INCIDENT_JOB_ID,
            instance_id="inst-mirror",
            job_type="message",
        )

        record = resolver.resolve_work(LIVE_INCIDENT_JOB_ID)

        assert record is not None
        assert record.kind == "job"
        assert record.status == "settled"
        assert record.job_type == "message"

    def test_task_only_row_resolves(self, engine, resolver):
        """(b) Task-only row — the report lane (no JobItem row)."""
        _seed_instance(engine, instance_id="inst-report")
        work_id = str(uuid.uuid4())
        _seed_task(
            engine,
            work_id=work_id,
            instance_id="inst-report",
            task_type="process_report",
            status=TaskStatus.COMPLETED.value,
        )

        record = resolver.resolve_work(work_id)

        assert record is not None, "Task-only row must resolve"
        assert record.kind == "report"
        assert record.work_id == work_id
        assert record.status == "completed"
        assert record.instance_id == "inst-report"

    def test_dual_backed_row_resolves_to_the_jobitem_record(self, engine, resolver):
        """(c) Dual-backed row — both tables share ``work_id == job_id``.

        The record must be the JobItem-backed one (``kind="job"``) with
        the JobItem's ``created_at`` AND the Task's execution timing AND
        the Task's ``result`` JSON (``result_summary``).
        """
        _seed_instance(engine, instance_id="inst-dual")
        work_id = str(uuid.uuid4())
        _seed_dual_backed(
            engine,
            work_id=work_id,
            instance_id="inst-dual",
            result_payload={"content": "the agent's last answer"},
        )

        record = resolver.resolve_work(work_id)

        assert record is not None
        assert record.kind == "job"
        assert record.work_id == work_id
        # Timing comes from the Task row (D1).
        assert record.started_at is not None
        assert record.completed_at is not None
        # Result content comes from the Task row's JSON (v0.13.9 fix).
        assert record.result_summary == "the agent's last answer"

    def test_unknown_id_returns_none(self, resolver):
        """(e) A non-existent id returns ``None`` cleanly (no exception)."""
        assert resolver.resolve_work(str(uuid.uuid4())) is None


# ── (d) — id-normalization pins ─────────────────────────────────────────────


class TestResolveWorkIdNormalization:
    """A decorated work id must resolve to the SAME record as the bare id."""

    @pytest.mark.parametrize("decoration", sorted(_DECORATIONS))
    def test_decorated_id_resolves_to_the_same_record(self, engine, resolver, decoration):
        """(d) whitespace / quotes / backticks around the id are tolerated."""
        _seed_instance(engine, instance_id="inst-norm")
        _seed_jobitem(engine, job_id=LIVE_INCIDENT_JOB_ID, instance_id="inst-norm")

        bare = resolver.resolve_work(LIVE_INCIDENT_JOB_ID)
        assert bare is not None, "the bare id must resolve (control leg)"

        decorated = resolver.resolve_work(_DECORATIONS[decoration](LIVE_INCIDENT_JOB_ID))

        assert decorated is not None, (
            f"{decoration}: a decorated work id must resolve to the same work "
            f"record as the bare id"
        )
        assert decorated.work_id == bare.work_id
        assert decorated.kind == bare.kind
        assert decorated.status == bare.status
        assert decorated.instance_id == bare.instance_id

    @pytest.mark.parametrize("decoration", sorted(_DECORATIONS))
    def test_decorated_id_of_an_unknown_work_still_resolves_to_none(self, resolver, decoration):
        """ANTI-VACUITY: normalization must never FABRICATE a hit.

        A decorated id for work that does not exist still returns ``None``
        — so the normalization pins cannot be satisfied by a blanket
        "always return a record" implementation.
        """
        unknown = str(uuid.uuid4())
        assert resolver.resolve_work(_DECORATIONS[decoration](unknown)) is None


# ── Tool-level pins ─────────────────────────────────────────────────────────


@pytest.fixture
def tools(job_service, engine):
    """REAL tools built by the REAL factory over the REAL service.

    The tools travel the REAL chain — ``tool → JobQueueService.get_work →
    WorkResolverService.resolve_work → repositories`` — so a resolver-layer
    regression cannot hide behind a stubbed service.
    """
    instance_id = "inst-tools"
    _seed_instance(engine, instance_id=instance_id)
    manager = _make_manager(instance_id)
    built = create_job_tools(
        job_service=job_service,
        queue_mgmt_service=MagicMock(),
        dead_letter_service=MagicMock(),
        current_instance_id="",
        agent_id="",
        watcher_repo=None,
        manager=manager,
    )
    return {
        "job_get": _tool_by_name(built, "job_get"),
        "job_continue": _tool_by_name(built, "job_continue"),
        "job_inject": _tool_by_name(built, "job_inject"),
        "manager": manager,
        "instance_id": instance_id,
    }


def _assert_no_error(result: dict[str, Any]) -> None:
    """Assert the tool answer carries no error.

    NOTE: a successful ``job_get`` answer is the WorkRecord dict, which
    DOES carry an ``"error"`` KEY (the work unit's error message — ``None``
    on a healthy row). The failure shape is the error dict the tool
    returns on the miss path (``{"error": "Job not found"}``). The
    discriminator is therefore the VALUE, not the key's presence.
    """
    assert result.get("error") is None, result


class TestJobToolLookup:
    """The three lookup tools must survive the same row shapes + decorations."""

    async def test_job_get_resolves_the_jobitem_only_row(self, tools, engine):
        """(a) ``job_get`` fetches a JobItem-only row (the incident shape)."""
        _seed_jobitem(
            engine, job_id=LIVE_INCIDENT_JOB_ID, instance_id=tools["instance_id"]
        )

        result = await tools["job_get"].ainvoke({"job_id": LIVE_INCIDENT_JOB_ID})

        _assert_no_error(result)
        assert result["work_id"] == LIVE_INCIDENT_JOB_ID
        assert result["kind"] == "job"
        assert result["status"] == "completed"

    async def test_job_continue_continues_a_completed_jobitem_only_row(
        self, tools, engine, job_service
    ):
        """(b) ``job_continue`` continues a completed JobItem-only row.

        The lookup phase must pass cleanly (the row IS found) and the
        continue must reach the enqueue — a "Job not found" or "has been
        deleted" answer means the lookup phase failed.
        """
        _seed_jobitem(
            engine, job_id=LIVE_INCIDENT_JOB_ID, instance_id=tools["instance_id"]
        )

        result = await tools["job_continue"].ainvoke(
            {"old_job_id": LIVE_INCIDENT_JOB_ID, "message": "keep going"}
        )

        _assert_no_error(result)
        assert result["status"] == "queued"
        assert result["new_job_id"] == "new-job-1"
        assert result["instance_id"] == tools["instance_id"]
        tools["manager"].enqueue_message_job.assert_awaited_once()

    @pytest.mark.parametrize("decoration", sorted(_DECORATIONS))
    async def test_job_get_accepts_a_decorated_id(self, tools, engine, decoration):
        """(c) ``job_get`` resolves a whitespace-decorated id."""
        _seed_jobitem(
            engine, job_id=LIVE_INCIDENT_JOB_ID, instance_id=tools["instance_id"]
        )

        result = await tools["job_get"].ainvoke(
            {"job_id": _DECORATIONS[decoration](LIVE_INCIDENT_JOB_ID)}
        )

        _assert_no_error(result)
        assert result["work_id"] == LIVE_INCIDENT_JOB_ID

    @pytest.mark.parametrize("decoration", sorted(_DECORATIONS))
    async def test_job_continue_accepts_a_decorated_id(self, tools, engine, decoration):
        """(c) ``job_continue`` resolves a whitespace-decorated id.

        The lookup phase must pass AND the second (``get_job``) lookup
        must find the row — a "not found" / "has been deleted" answer
        means the decorated handle never reached the row.
        """
        _seed_jobitem(
            engine, job_id=LIVE_INCIDENT_JOB_ID, instance_id=tools["instance_id"]
        )

        result = await tools["job_continue"].ainvoke(
            {
                "old_job_id": _DECORATIONS[decoration](LIVE_INCIDENT_JOB_ID),
                "message": "keep going",
            }
        )

        _assert_no_error(result)
        assert result["status"] == "queued"
        assert result["new_job_id"] == "new-job-1"
        tools["manager"].enqueue_message_job.assert_awaited_once()

    @pytest.mark.parametrize("decoration", sorted(_DECORATIONS))
    async def test_job_inject_accepts_a_decorated_id(self, tools, engine, decoration):
        """(c) ``job_inject`` resolves a whitespace-decorated id."""
        _seed_jobitem(
            engine, job_id=LIVE_INCIDENT_JOB_ID, instance_id=tools["instance_id"]
        )

        result = await tools["job_inject"].ainvoke(
            {
                "job_id": _DECORATIONS[decoration](LIVE_INCIDENT_JOB_ID),
                "message": "additional guidance",
            }
        )

        _assert_no_error(result)
        assert result["instance_id"] == tools["instance_id"]
        assert result["status"] == "injected"


# ── watch_mission receipt resolution (the shared-resolver front-end) ────────


class TestWatchMissionReceiptResolution:
    """``watch_mission`` must resolve a DECORATED receipt via the shared resolver.

    The watch tools are a RESOLVER FRONT-END: ``watch_mission(target=...)``
    accepts a mission_id (= instance_id) OR a job reference (the receipt
    ``job_create`` / ``job_continue`` returned) and resolves the work side
    through the SAME ``JobQueueService.get_work`` →
    ``WorkResolverService.resolve_work`` chain the direct lookup tools use.
    A receipt copied out of markdown / JSON tool output therefore has to
    survive the SAME normalization rule — otherwise the watch silently
    refuses to arm ("Could not resolve ... as a mission_id or job
    reference") and the caller is left believing the mission is unwatched.
    """

    @pytest.fixture
    def watch_tools(self, job_service, engine, task_repo, instance_repo, job_repo):
        """REAL ``watch_mission`` tool over the REAL repositories."""
        mission_resolver = MissionResolver(instance_repo, job_repo)
        watcher_repo = JobWatcherRepository(engine)
        built = create_mission_watch_tools(
            job_service=job_service,
            mission_resolver=mission_resolver,
            task_repo=task_repo,
            watcher_repo=watcher_repo,
            current_instance_id=WATCHER_INSTANCE_ID,
        )
        return {
            "watch_mission": _tool_by_name(built, "watch_mission"),
            "watcher_repo": watcher_repo,
        }

    @pytest.mark.parametrize("decoration", sorted(_DECORATIONS))
    async def test_watch_mission_resolves_a_decorated_receipt(self, watch_tools, engine, decoration):
        """A decorated receipt resolves AND arms its watch row.

        The mission side is live (a ``running`` instance) and owns one
        LIVE receipt (a ``pending`` Task row); the watch row is keyed on
        the receipt's ``work_id``, so the assertion reads the armed row
        back out of the watcher repository.
        """
        _seed_instance(engine, instance_id=WATCHER_INSTANCE_ID)
        _seed_instance(engine, instance_id=MISSION_INSTANCE_ID, status="running")
        work_id = str(uuid.uuid4())
        # The mission's live receipt: the Task row (the receipt the watch
        # row keys on) + the JobItem mirror (the handle the caller copies
        # out of the tool output).
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=MISSION_INSTANCE_ID,
            task_type="process_message",
            status=TaskStatus.PENDING.value,
        )
        _seed_jobitem(
            engine,
            job_id=work_id,
            instance_id=MISSION_INSTANCE_ID,
            admission_state=AdmissionState.QUEUED.value,
            terminal_reason=None,
            job_type="task",
        )

        result = await watch_tools["watch_mission"].ainvoke(
            {"target": _DECORATIONS[decoration](work_id)}
        )

        assert "Error" not in result, result
        assert result.startswith("Mission watch registered"), result
        armed = watch_tools["watcher_repo"].get_watchers_for_job(work_id)
        assert [w.instance_id for w in armed] == [WATCHER_INSTANCE_ID], armed

    async def test_watch_mission_still_refuses_an_unknown_target(self, watch_tools, engine):
        """ANTI-VACUITY: an unresolvable target is still refused.

        The decoration tolerance must not turn the watch tool into a
        "watch anything" primitive — an unknown handle still gets the
        explicit refusal.
        """
        _seed_instance(engine, instance_id=WATCHER_INSTANCE_ID)

        result = await watch_tools["watch_mission"].ainvoke(
            {"target": str(uuid.uuid4())}
        )

        assert result.startswith("Error: Could not resolve"), result

    @pytest.mark.parametrize("decoration", sorted(_DECORATIONS))
    async def test_watch_mission_arms_a_pre_dispatch_receipt_on_the_canonical_id(
        self, watch_tools, engine, decoration
    ):
        """A decorated PRE-DISPATCH receipt arms on the CANONICAL id.

        ``job_create`` returns the receipt BEFORE the mission is
        dispatched (the JobItem row carries no ``instance_id`` yet), and
        the tool's own docstring calls that form out: "the receipt form
        works even before the mission is dispatched". The watch row is
        keyed by ``job_id`` — the engine's notify path queries
        ``WHERE job_id == work_id`` — so a watch armed on the DECORATED
        handle would never be resolved by the notifier: a stranded
        watch. The row must be keyed on the canonical id the resolver
        returned.
        """
        _seed_instance(engine, instance_id=WATCHER_INSTANCE_ID)
        work_id = str(uuid.uuid4())
        # A pre-dispatch receipt: the JobItem row exists (job_create
        # enqueued it) but no instance is minted yet.
        _seed_jobitem(
            engine,
            job_id=work_id,
            instance_id=None,
            admission_state=AdmissionState.QUEUED.value,
            terminal_reason=None,
            job_type="task",
        )

        result = await watch_tools["watch_mission"].ainvoke(
            {"target": _DECORATIONS[decoration](work_id)}
        )

        assert "Error" not in result, result
        assert "receipt" in result, result
        armed = watch_tools["watcher_repo"].get_watchers_for_job(work_id)
        assert [w.instance_id for w in armed] == [WATCHER_INSTANCE_ID], armed


# ── Anti-vacuity controls (the sibling control of the mutation matrix) ──────


class TestAntiVacuityControls:
    """The "not found" path must survive the fix.

    These are the CONTROL legs: they are green at base AND must stay green
    after the normalization fix — a fix that turns every lookup into a hit
    (or that swallows the miss branch) reds them.
    """

    def test_resolver_returns_none_for_an_unknown_id(self, resolver):
        assert resolver.resolve_work(str(uuid.uuid4())) is None

    async def test_job_get_still_reports_not_found(self, tools):
        result = await tools["job_get"].ainvoke({"job_id": str(uuid.uuid4())})
        assert result == {"error": "Job not found"}

    async def test_job_continue_still_reports_not_found(self, tools):
        missing = str(uuid.uuid4())
        result = await tools["job_continue"].ainvoke(
            {"old_job_id": missing, "message": "hello"}
        )
        assert "error" in result
        assert "not found" in result["error"]

    async def test_job_inject_still_reports_not_found(self, tools):
        missing = str(uuid.uuid4())
        result = await tools["job_inject"].ainvoke(
            {"job_id": missing, "message": "hello"}
        )
        assert "error" in result
        assert "not found" in result["error"]
