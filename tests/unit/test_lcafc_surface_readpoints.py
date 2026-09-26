"""LCAFC surface read-points — incident 7d4a3bd9 merge gate (Job 5).

Cycle spec: ``.agents/shared/planning/leader-completion-attestation/requirements.md``
2026-09-26 supersession — "UNVERIFIED-COMPLETION SURFACE": when the
attestation gate ended a mission via ``terminal_after_bound``
(``completion_gate_escalated=True``), EVERY read surface must render
``completed (gate escalated — unverified)``
(``daemon.constants.COMPLETION_GATE_ESCALATED_DISPLAY``) instead of
plain ``completed``. Episode B (7d4a3bd9) rendered plain ``completed``
and the unverified shape was invisible.

This file asserts the escalated terminal is visible at EVERY read
point:

* §1  JOBS API read point — ``daemon/routers/jobs_crud.py`` (GET
  ``/api/jobs/{job_id}`` detail through the real resolver + the
  shared ``_job_to_response`` render seam).
* §2  MISSION read points — ``daemon/services/mission_resolver.py``
  (MissionRecord flag), ``daemon/tools/missions.py`` (get_mission
  snapshot + list_missions summary payloads via ``_render_liveness``),
  ``daemon/routers/missions.py`` + ``daemon/routers/schemas.py``
  (MissionResponse ``completion_gate_escalated`` field).
* §3  SSE read point — ``daemon/routers/jobs_streaming.py``
  ``_ResolvedWork`` completed payload (status swap + flag).
* §4  FE STATIC — ``frontend/src/app/models/job.model.ts`` union
  membership + terminal predicate + the 3 touched components, and a
  BYTE-PIN: the FE literal must equal the backend constant
  character-for-character (em-dash included).

Unit-level; mirrors the established pattern in
``tests/unit/routers/test_jobs_streaming_resolver.py`` (in-memory
SQLite StaticPool, real repositories, FastAPI TestClient with
dependency overrides). No production code changes — this file is a
merge-gate read-points verifier.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.constants import COMPLETION_GATE_ESCALATED_DISPLAY
from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import Task  # noqa: F401 — registers table metadata
from daemon.routers.jobs_crud import _job_to_response
from daemon.routers.jobs_crud import router as jobs_crud_router
from daemon.routers.jobs_streaming import _ResolvedWork
from daemon.routers.missions import _mission_record_to_response
from daemon.routers.schemas import MissionResponse
from daemon.services.mission_resolver import MissionRecord, MissionResolver
from daemon.services.work_resolver import WorkRecord, WorkResolverService
from daemon.services.job_queue_service import JobQueueService
from daemon.tools.missions import (
    _mission_snapshot_dict,
    _mission_summary_dict,
    _render_liveness,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
JOB_MODEL_TS = REPO_ROOT / "frontend" / "src" / "app" / "models" / "job.model.ts"

# ─── Fixtures (mirrors test_jobs_streaming_resolver.py) ──────────────────────


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine (StaticPool + FK on)."""
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
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def task_repo(engine: Engine) -> "TaskRepository":
    from daemon.repositories.task.repository import TaskRepository

    return TaskRepository(engine)


@pytest.fixture
def resolver(
    task_repo: "TaskRepository",
    job_repo: JobRepository,
    instance_repo: SQLModelInstanceRepository,
) -> WorkResolverService:
    svc = JobQueueService(
        repository=job_repo,
        lock_manager=MagicMock(),
        queue_repo=MagicMock(),
        instance_manager=MagicMock(),
    )
    svc.set_work_resolver(
        WorkResolverService(task_repo, job_repo, instance_repo)
    )
    return svc


@pytest.fixture
def mission_resolver(
    job_repo: JobRepository,
    instance_repo: SQLModelInstanceRepository,
) -> MissionResolver:
    return MissionResolver(instance_repo, job_repo)


@pytest.fixture
def client(engine: Engine, resolver: JobQueueService) -> TestClient:
    """TestClient wired with the jobs_crud router + real repos."""
    app = FastAPI()
    app.include_router(jobs_crud_router, prefix="/api")
    from daemon.routers.jobs_crud import (
        get_dead_letter_svc,
        get_job_queue_service,
    )

    app.dependency_overrides[get_job_queue_service] = lambda: resolver
    app.dependency_overrides[get_dead_letter_svc] = lambda: MagicMock()
    return TestClient(app)


# ─── Seed helpers ────────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    agent_id: str = "developer",
    project_id: str | None = "test-project",
    status: str = "completed",
    completion_gate_escalated: bool = False,
) -> str:
    """Insert an Instance row (optionally gate-escalated)."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            agent_name=agent_id,
            project_id=project_id,
            status=status,
            created_at=now_iso,
            updated_at=now_iso,
            paused_at=None,
            completion_gate_escalated=completion_gate_escalated,
        )
        s.add(inst)
        s.commit()
    return iid


def _seed_completed_job(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = "completed",
) -> str:
    """Insert a terminal JobItem (``completed``|``failed``), stamped
    with the backing instance. The WorkRecord ``status`` for DONE rows
    is sourced from ``terminal_reason`` (Phase 7c; the ``status``
    column is frozen), so non-completed variants set it here."""
    jid = str(uuid.uuid4())
    with Session(engine) as s:
        job = JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="m",
            source="api",
            project_id="test-project",
            priority=5,
            status=status,
            terminal_reason=status,
            admission_state=AdmissionState.DONE.value,
            instance_id=instance_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            job_metadata={},
        )
        s.add(job)
        s.commit()
    return jid


def _escalated_work_record(
    *,
    status: str = "completed",
    escalated: bool = True,
) -> WorkRecord:
    """A minimal WorkRecord with the escalation flag set."""
    return WorkRecord(
        work_id=str(uuid.uuid4()),
        kind="job",
        status=status,
        instance_id=f"inst-{uuid.uuid4().hex[:8]}",
        project_id="test-project",
        agent_id="developer",
        result_summary=None,
        error=None,
        created_at=None,
        completion_gate_escalated=escalated,
    )


def _escalated_mission_record(
    *,
    liveness: str | None = "completed",
    escalated: bool = True,
) -> MissionRecord:
    """A minimal MissionRecord with the escalation flag set."""
    return MissionRecord(
        mission_id="inst-escalated",
        agent_id="developer",
        parent_mission_id=None,
        liveness=liveness,
        terminal_reason="completed" if liveness == "completed" else None,
        epoch=1,
        completion_gate_escalated=escalated,
    )


# ═════════════════════════════════════════════════════════════════════════════
# §1 — JOBS API read point (jobs_crud detail path + shared render seam)
# ═════════════════════════════════════════════════════════════════════════════


class TestJobsApiReadPoint:
    """§1: GET /api/jobs/{job_id} renders the escalated label."""

    def test_detail_escalated_completed_renders_label(
        self, engine: Engine, client: TestClient
    ) -> None:
        """Decisive §1 assertion: escalated terminal ⇒ the jobs API
        response payload carries the EXACT display string, not plain
        ``completed``."""
        iid = _seed_instance(
            engine, status="completed", completion_gate_escalated=True
        )
        jid = _seed_completed_job(engine, instance_id=iid)

        resp = client.get(f"/api/jobs/{jid}")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == COMPLETION_GATE_ESCALATED_DISPLAY, (
            f"jobs API detail must surface the escalated label for a "
            f"gate-escalated completed mission; got {body['status']!r}"
        )
        assert body["status"] != "completed"

    def test_detail_plain_completed_negative_control(
        self, engine: Engine, client: TestClient
    ) -> None:
        """Negative control: NOT escalated ⇒ plain ``completed``."""
        iid = _seed_instance(
            engine, status="completed", completion_gate_escalated=False
        )
        jid = _seed_completed_job(engine, instance_id=iid)

        resp = client.get(f"/api/jobs/{jid}")

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "completed"

    def test_detail_escalated_non_completed_status_untouched(
        self, engine: Engine, client: TestClient
    ) -> None:
        """The swap is gated on status == ``completed``: an escalated
        flag on a non-completed row must NOT rewrite the status."""
        iid = _seed_instance(
            engine, status="failed", completion_gate_escalated=True
        )
        jid = _seed_completed_job(engine, instance_id=iid, status="failed")

        resp = client.get(f"/api/jobs/{jid}")

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "failed"

    def test_shared_render_seam_rewrites_resolver_backed_row(self) -> None:
        """``_job_to_response`` is the shared render seam (detail +
        batched callers funnel through it): resolver-backed row with
        the flag ⇒ escalated label; legacy fallback (work_record=None)
        stays honest (plain JobItem mirror status)."""
        escalated_job = SimpleNamespace(
            job_id="j-1",
            status="completed",
            admission_state="done",
            priority=5,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            project_id="test-project",
            queue_id=None,
            instance_id="inst-1",
            source="api",
            job_metadata={},
            idempotency_key=None,
            message="m",
            deleted_at=None,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        record = _escalated_work_record()

        out = _job_to_response(escalated_job, work_record=record)
        assert out.status == COMPLETION_GATE_ESCALATED_DISPLAY

        # Legacy fallback: no WorkRecord ⇒ NO rewrite (honest default).
        out_legacy = _job_to_response(escalated_job, work_record=None)
        assert out_legacy.status == "completed"


# ═════════════════════════════════════════════════════════════════════════════
# §2 — MISSION read points (resolver → tool payloads → HTTP schema)
# ═════════════════════════════════════════════════════════════════════════════


class TestMissionReadPoints:
    """§2: the flag threads through resolver, tool output, and schema."""

    def test_mission_resolver_record_carries_flag(
        self, engine: Engine, mission_resolver: MissionResolver
    ) -> None:
        """Decisive §2a: resolver output (MissionRecord) carries
        ``completion_gate_escalated=True`` while the canonical
        ``liveness`` field stays untouched (filters keep matching the
        canonical vocabulary)."""
        iid = _seed_instance(
            engine, status="completed", completion_gate_escalated=True
        )

        record = mission_resolver.resolve(iid)

        assert record is not None
        assert record.completion_gate_escalated is True, (
            "MissionRecord must carry completion_gate_escalated=True for "
            "an escalated instance row"
        )
        assert record.liveness == "completed", (
            "canonical liveness must stay 'completed' on the resolver row"
        )

    def test_mission_resolver_negative_control(
        self, engine: Engine, mission_resolver: MissionResolver
    ) -> None:
        iid = _seed_instance(
            engine, status="completed", completion_gate_escalated=False
        )

        record = mission_resolver.resolve(iid)

        assert record is not None
        assert record.completion_gate_escalated is False
        assert record.liveness == "completed"

    def test_get_mission_tool_snapshot_renders_label(self) -> None:
        """Decisive §2b (get_mission): the tool snapshot payload renders
        the DISTINCT string via ``_render_liveness`` + the flag."""
        payload = _mission_snapshot_dict(_escalated_mission_record())

        assert payload["liveness"] == COMPLETION_GATE_ESCALATED_DISPLAY
        assert payload["completion_gate_escalated"] is True

    def test_list_missions_tool_summary_renders_label(self) -> None:
        """Decisive §2b (list_missions): the summary payload renders
        the DISTINCT string + the flag."""
        payload = _mission_summary_dict(_escalated_mission_record())

        assert payload["liveness"] == COMPLETION_GATE_ESCALATED_DISPLAY
        assert payload["completion_gate_escalated"] is True

    def test_render_liveness_passthrough_non_completed(self) -> None:
        """Renderer gate: escalated + non-completed liveness passes
        through unchanged."""
        assert (
            _render_liveness(
                _escalated_mission_record(liveness="processing")
            )
            == "processing"
        )
        # Non-escalated completed passes through unchanged too.
        assert (
            _render_liveness(_escalated_mission_record(escalated=False))
            == "completed"
        )

    def test_mission_response_schema_carries_flag_and_label(self) -> None:
        """Decisive §2c: MissionResponse schema declares the
        ``completion_gate_escalated`` field and the HTTP router
        (``_mission_record_to_response``) renders the label + flag."""
        # Schema-level: the additive field exists on the wire model.
        assert "completion_gate_escalated" in MissionResponse.model_fields

        resp = _mission_record_to_response(_escalated_mission_record())
        assert resp.liveness == COMPLETION_GATE_ESCALATED_DISPLAY
        assert resp.completion_gate_escalated is True

        # Negative control through the same router mapping.
        plain = _mission_record_to_response(
            _escalated_mission_record(escalated=False)
        )
        assert plain.liveness == "completed"
        assert plain.completion_gate_escalated is False


# ═════════════════════════════════════════════════════════════════════════════
# §3 — SSE read point (jobs_streaming _ResolvedWork payload builders)
# ═════════════════════════════════════════════════════════════════════════════


class TestSseReadPoint:
    """§3: the SSE completed payload carries the flag AND the label."""

    def test_sse_completed_payload_swaps_status(self) -> None:
        """Decisive §3: completed payload for an escalated terminal
        carries the DISTINCT status string + the machine-readable
        flag."""
        record = _escalated_work_record()
        resolved = _ResolvedWork.from_work_record(record)

        payload = resolved.to_completed_payload(work_id=record.work_id)

        assert payload["status"] == COMPLETION_GATE_ESCALATED_DISPLAY, (
            f"SSE completed payload must surface the escalated label; "
            f"got {payload['status']!r}"
        )
        assert payload["completion_gate_escalated"] is True

    def test_sse_connected_payload_carries_flag_canonical_status(
        self,
    ) -> None:
        """Mid-flight payloads (connected/status_update) keep the
        canonical status — the swap is completed-event-only — but the
        flag rides uniformly."""
        record = _escalated_work_record()
        resolved = _ResolvedWork.from_work_record(record)

        payload = resolved.to_payload(work_id=record.work_id)

        assert payload["status"] == "completed"
        assert payload["completion_gate_escalated"] is True

    def test_sse_completed_payload_negative_control(self) -> None:
        """Not escalated ⇒ plain ``completed`` on the completed event."""
        record = _escalated_work_record(escalated=False)
        resolved = _ResolvedWork.from_work_record(record)

        payload = resolved.to_completed_payload(work_id=record.work_id)

        assert payload["status"] == "completed"
        assert payload["completion_gate_escalated"] is False

    def test_sse_completed_non_completed_status_passthrough(self) -> None:
        """Escalated flag + non-completed status ⇒ verbatim pass-through
        (the swap is gated on ``completed``)."""
        record = _escalated_work_record(status="failed")
        resolved = _ResolvedWork.from_work_record(record)

        payload = resolved.to_completed_payload(work_id=record.work_id)

        assert payload["status"] == "failed"


# ═════════════════════════════════════════════════════════════════════════════
# §4 — FE STATIC (job.model.ts unions + terminal predicate + components)
# ═════════════════════════════════════════════════════════════════════════════


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestFeStatic:
    """§4: the FE union/predicate/components reference the literal, and
    the FE literal is BYTE-IDENTICAL to the backend constant."""

    def test_fe_literal_equals_backend_constant_byte_for_byte(self) -> None:
        """Decisive §4 byte-pin: the literal in job.model.ts must equal
        ``COMPLETION_GATE_ESCALATED_DISPLAY`` character-for-character
        (the em-dash makes drift likely — pin both ends)."""
        fe_text = _read(JOB_MODEL_TS)
        # The FE literal appears with TS single quotes; extract every
        # occurrence of the quoted string.
        fe_literals = set(re.findall(r"'(completed \(gate escalated[^']*)'", fe_text))
        assert fe_literals, "no escalated literal found in job.model.ts"
        assert fe_literals == {COMPLETION_GATE_ESCALATED_DISPLAY}, (
            f"FE literal(s) {fe_literals!r} != backend constant "
            f"{COMPLETION_GATE_ESCALATED_DISPLAY!r} (byte drift)"
        )

        # Backend end of the pin: the constant itself is the canonical
        # string (guards a constants.py drift breaking the pin silent).
        constants_text = _read(REPO_ROOT / "daemon" / "constants.py")
        assert COMPLETION_GATE_ESCALATED_DISPLAY in constants_text

    def test_fe_mission_liveness_union_includes_literal(self) -> None:
        """MissionLiveness union includes the escalated terminal."""
        fe_text = _read(JOB_MODEL_TS)
        union_lines = [
            line
            for line in fe_text.splitlines()
            if "export type MissionLiveness" in line
        ]
        assert len(union_lines) == 1
        assert COMPLETION_GATE_ESCALATED_DISPLAY in union_lines[0], (
            "MissionLiveness union must include the escalated literal"
        )

    def test_fe_is_terminal_status_returns_true_for_literal(self) -> None:
        """``isTerminalStatus`` treats the literal as terminal —
        extract the function body and statically evaluate the ``===``
        disjunction against the literal."""
        fe_text = _read(JOB_MODEL_TS)
        match = re.search(
            r"export function isTerminalStatus\(.*?\n\}", fe_text, re.DOTALL
        )
        assert match, "isTerminalStatus not found in job.model.ts"
        fn_body = match.group(0)
        assert COMPLETION_GATE_ESCALATED_DISPLAY in fn_body, (
            "isTerminalStatus must return true for the escalated literal"
        )
        # Simulate the predicate: collect the compared literals and
        # verify the escalated one is among the terminal values.
        compared = set(re.findall(r"===\s*'([^']+)'", fn_body))
        assert COMPLETION_GATE_ESCALATED_DISPLAY in compared

    def test_fe_job_interfaces_carry_flag(self) -> None:
        """The Job + Work interfaces carry the machine-readable flag
        (the narrowings key off it)."""
        fe_text = _read(JOB_MODEL_TS)
        assert fe_text.count("completion_gate_escalated?: boolean | null;") >= 2, (
            "Job and Work interfaces must both declare "
            "completion_gate_escalated"
        )

    @pytest.mark.parametrize(
        "component",
        [
            "frontend/src/app/components/job-card/job-card.component.ts",
            "frontend/src/app/components/job-detail-drawer/job-detail-drawer.component.ts",
            "frontend/src/app/components/job-queue-panel/job-queue-panel.component.ts",
        ],
    )
    def test_fe_touched_components_reference_literal(
        self, component: str
    ) -> None:
        """Each of the 3 touched components references the literal."""
        path = REPO_ROOT / component
        assert path.exists(), f"missing component: {component}"
        assert COMPLETION_GATE_ESCALATED_DISPLAY in _read(path), (
            f"{component} must reference the escalated literal"
        )
