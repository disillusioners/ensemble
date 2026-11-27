"""F3 pins — admission guards on NON-SERVABLE targets (skill-evolution R1).

Registered: NOT in a numbered pack — this is a NEW test file (the F3
lane's preferred shape; adding nodes to a PACKS.md-registered file would
churn its pinned node count).

THE GAP THIS CLOSES

Every FIX front door forwarded the raw ``skill_id`` WITHOUT loading the
skill row (verified: 0 ``is_active`` reads in each chokepoint):

* ``SkillEvolutionService._evolve_fix`` — the inline ``skill_evolve``
  path (no dispatcher admission at all);
* ``skill_fix`` (tool) → ``SkillJobDispatcher.dispatch_fix``;
* ``POST /api/skills/{skill_id}/fix`` → ``SkillJobDispatcher.dispatch_fix``;
* ``SkillJobDispatcher.enqueue_evolution`` — the ONE admission chokepoint
  the tool / REST / trigger-engine producers share.

A deactivated skill (``is_active=False`` — retired by an A/B resolution
or an operator deactivation) was therefore admissible and the keeper
would evolve a variant outside the serving surface.

THE CHOKEPOINT SET (deliberate)

* **Admission** — ``enqueue_evolution`` (via
  ``_check_fix_target_servable``): the row read is introduced at the ONE
  place tool + REST + trigger engine share; it RAISES the typed
  ``SkillNonServableTargetRefused`` ("non_servable_target").
* **Execution** — ``_evolve_fix``: the inline path never touches the
  dispatcher, and an admitted job re-reads the row at execution (the row
  may flip between admission and execution). Returns the house
  skip-envelope (never raises).
* **Rendering only** — the ``skill_fix`` tool and ``post_fix`` catch the
  typed refusal and render it per the house contract (tool envelope /
  HTTP 409 ``{"error", "reason"}``). They do NOT re-read the row.

NAMED MUTATION LEGS (each turns this file RED):

* **M-F3a** — remove the ``_check_fix_target_servable`` call from
  ``enqueue_evolution`` → the dispatcher admission test goes RED (the
  deactivated skill is admitted).
* **M-F3b** — remove the ``_evolve_fix`` servability guard → the
  execution test goes RED (a new skill row is created from a
  deactivated source).
* **M-F3c** — drop the structural ``except
  SkillNonServableTargetRefused`` from the ``skill_fix`` tool → the tool
  test goes RED (the generic fault path renders instead).
* **M-F3d** — drop the 409 branch from ``post_fix`` → the REST test goes
  RED (fall-through to 500).
* **M-F3e** — remove the ``record_admission_refusal`` /
  ``emit_lane_anomaly`` calls → the observability assertions go RED.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

from daemon.repositories.skill.models import (  # noqa: F401 — table registration
    Skill,
    SkillABTest,
    SkillEmbedding,
    SkillLineage,
    SkillTrigger,
    SkillUsageRecord,
)
from daemon.repositories.skill.repository import SkillRepository
from daemon.services import skill_capture_observability as observability
from daemon.services.skill_job_dispatcher import (
    EVOLUTION_TYPE_FIX,
    SkillJobDispatcher,
    SkillNonServableTargetRefused,
)
from tests.helpers.skill_sink_redirect import (
    lane_test_end,
    lane_test_start,
    restore_safe_sink_redirect,
    snapshot_real_sink_dir,
)

PROJECT = "proj-f3-nonservable"
INSTANCE_ID = "inst-f3-1"


# ── Harness ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _observability_sandbox(tmp_path, request):
    """Scratch sink + fresh counters per test (house lane discipline)."""
    lane_test_start(request.node.nodeid)
    before = snapshot_real_sink_dir()
    observability.configure_sink(tmp_path)
    observability.reset_counters()
    observability.set_anomaly_reporter(None)
    try:
        yield tmp_path
    finally:
        restore_safe_sink_redirect()
        observability.reset_counters()
        observability.set_anomaly_reporter(None)
        lane_test_end(request.node.nodeid, before)


@pytest.fixture
def engine(tmp_path):
    """A file-backed SQLite engine with the skill tables created."""
    eng = create_engine(
        f"sqlite:///{tmp_path / 'f3-nonservable.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def skill_repo(engine):
    return SkillRepository(engine)


def _make_skill(skill_repo, *, name="f3-skill", is_active=True, status="active"):
    return skill_repo.create(
        name=name,
        description="d",
        content="c",
        project_id=PROJECT,
        is_active=is_active,
        status=status,
    )


class _FakeJob:
    def __init__(self, job_id: str):
        self.job_id = job_id


class _FakeJobService:
    """A minimal job service: records enqueues, mints deterministic ids."""

    def __init__(self) -> None:
        self.enqueued: list[dict] = []

    @property
    def repository(self):
        # No durable ledger accessors — the burst cap is DISABLED in
        # these pins (cap=0), so nothing reaches it.
        return None

    async def enqueue(self, **kwargs):
        self.enqueued.append(dict(kwargs))
        return _FakeJob(f"job-{len(self.enqueued):03d}")


class _StubQueueRepo:
    def get_by_name(self, project_id, queue_name):
        return None


class _RecordingReporter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"accepted": True}


def _make_dispatcher(skill_repo, reporter=None, *, wire_repo=True):
    dispatcher = SkillJobDispatcher(
        job_service=_FakeJobService(),
        queue_repo=_StubQueueRepo(),
        evolution_lane_enabled=True,
        fix_cooldown_seconds=0,
        fix_burst_cap=0,  # the durable cap is out of scope here
        fix_burst_window_seconds=86400,
        anomaly_reporter=reporter,
        skill_repo=skill_repo if wire_repo else None,
    )
    return dispatcher


def _sink_records(tmp_path):
    sink_dir = tmp_path / observability.SINK_DIR_NAME
    records = []
    for path in sorted(sink_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


# ── Chokepoint 1 — the dispatcher admission gate (enqueue_evolution) ──────


class TestDispatcherAdmissionGate:
    @pytest.mark.asyncio
    async def test_fix_against_a_deactivated_skill_is_refused(
        self, skill_repo, tmp_path
    ):
        """The typed refusal fires; NO job row is minted; the signal lands."""
        reporter = _RecordingReporter()
        observability.set_anomaly_reporter(reporter)
        dispatcher = _make_dispatcher(skill_repo)
        dead = _make_skill(
            skill_repo, name="f3-dead", is_active=False, status="inactive"
        )

        with pytest.raises(SkillNonServableTargetRefused) as excinfo:
            await dispatcher.enqueue_evolution(
                PROJECT, dead.id, EVOLUTION_TYPE_FIX, "fix it"
            )

        # The stable token — the SAME one the REST route returns.
        assert excinfo.value.reason == "non_servable_target"
        assert "refused: non-servable target" in str(excinfo.value)
        assert "NO skill-keeper job was created" in str(excinfo.value)

        # NO job was admitted (a refused admission writes no row).
        assert dispatcher._job_service.enqueued == []

        # Observability: admission-refusal counter + sink record + anomaly.
        assert (
            observability.counters().admission_refusals.get(
                "non_servable_target"
            )
            == 1
        )
        records = _sink_records(tmp_path)
        refusal_records = [
            r for r in records if r.get("refusal_reason") == "non_servable_target"
        ]
        assert refusal_records, (
            f"a refused admission must leave a sink record — got {records!r}"
        )
        assert any(r.get("stage") == "admission" for r in refusal_records)
        assert len(reporter.calls) == 1, "the refusal must announce ONE anomaly"
        assert "non-servable target" in reporter.calls[0]["description"]

    @pytest.mark.asyncio
    async def test_fix_against_an_active_skill_is_admitted(
        self, skill_repo
    ):
        """The gate does NOT over-fire: an active target is admitted."""
        observability.reset_counters()
        dispatcher = _make_dispatcher(skill_repo)
        live = _make_skill(skill_repo, name="f3-live", is_active=True)

        job_id = await dispatcher.enqueue_evolution(
            PROJECT, live.id, EVOLUTION_TYPE_FIX, "fix it"
        )

        assert job_id == "job-001"
        assert len(dispatcher._job_service.enqueued) == 1
        assert (
            observability.counters().admission_refusals.get(
                "non_servable_target"
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_missing_skill_row_is_not_this_gates_refusal(
        self, skill_repo
    ):
        """An UNKNOWN id passes through — downstream owns the not-found case."""
        dispatcher = _make_dispatcher(skill_repo)

        job_id = await dispatcher.enqueue_evolution(
            PROJECT, str(uuid.uuid4()), EVOLUTION_TYPE_FIX, "fix it"
        )

        assert job_id == "job-001"

    @pytest.mark.asyncio
    async def test_unwired_repo_degrades_fail_open_and_is_recorded(
        self, skill_repo, tmp_path
    ):
        """No skill repo wired ⇒ admission proceeds + a DECLARED degraded
        posture record (fail-open; the execution guard is the backstop)."""
        dispatcher = _make_dispatcher(skill_repo, wire_repo=False)
        live = _make_skill(skill_repo, name="f3-live2", is_active=True)

        job_id = await dispatcher.enqueue_evolution(
            PROJECT, live.id, EVOLUTION_TYPE_FIX, "fix it"
        )

        assert job_id == "job-001"
        records = _sink_records(tmp_path)
        assert any(
            r.get("outcome") == "servability_gate_degraded" for r in records
        ), f"the degraded posture must be recorded — got {records!r}"


# ── Chokepoint 2 — the service execution guard (_evolve_fix) ──────────────


def _make_evolution_service(skill_repo):
    from daemon.services.skill_evolution_service import SkillEvolutionService

    return SkillEvolutionService(
        skill_repo=skill_repo,
        lineage_repo=MagicMock(),
        usage_repo=MagicMock(),
        embedding_service=MagicMock(),
        metrics_service=MagicMock(),
        ab_test_repo=MagicMock(),
        config=SimpleNamespace(
            ab_stall_timeout_days=7.0,
            ab_sample_size=10,
            ab_min_difference=0.15,
            max_extensions=3,
        ),
        llm_config={},
    )


class TestServiceExecutionGuard:
    @pytest.mark.asyncio
    async def test_inline_fix_on_a_deactivated_skill_is_skipped(
        self, skill_repo, tmp_path
    ):
        """The inline evolve path refuses — skip envelope, NO mutation."""
        svc = _make_evolution_service(skill_repo)
        dead = _make_skill(
            skill_repo, name="f3-dead-inline", is_active=False,
            status="inactive",
        )
        before = len(list(_all_skills(skill_repo)))

        result = await svc.evolve_skill(dead.id, "FIX", "tighten errors")

        assert result["skipped"] is True
        assert "non_servable_target" in result["reason"]
        assert "is_active=False" in result["reason"]
        assert result["skill_id"] == dead.id
        # NO new skill row was created.
        assert len(list(_all_skills(skill_repo))) == before
        # Execution-refusal counter under its OWN token.
        assert (
            observability.counters().execution_refusals.get(
                "non_servable_target"
            )
            == 1
        )
        records = _sink_records(tmp_path)
        assert any(
            r.get("stage") == "execution"
            and r.get("refusal_reason") == "non_servable_target"
            for r in records
        ), f"a refused execution must leave a sink record — got {records!r}"

    @pytest.mark.asyncio
    async def test_inline_fix_on_an_active_skill_is_not_refused(
        self, skill_repo
    ):
        """Over-fire guard: an ACTIVE target still reaches the evolution
        pipeline (the body producer is stubbed so the pin proves ONLY
        that the servability guard let the call through)."""
        svc = _make_evolution_service(skill_repo)
        live = _make_skill(skill_repo, name="f3-live-inline", is_active=True)
        svc._produce_evolved_body = AsyncMock(
            return_value=(live.content, live.description, {})
        )

        result = await svc.evolve_skill(live.id, "FIX", "tighten errors")

        # The guard did NOT skip; whatever the (stubbed) pipeline did,
        # it was not the non-servable refusal.
        assert not (
            result.get("skipped") and "non_servable_target" in str(
                result.get("reason", "")
            )
        )
        assert (
            observability.counters().execution_refusals.get(
                "non_servable_target"
            )
            is None
        )


def _all_skills(skill_repo):
    with Session(skill_repo.engine) as session:
        return list(session.exec(select(Skill)))


# ── Chokepoint 3 — the skill_fix tool renders the typed refusal ───────────


class TestToolRendering:
    @pytest.mark.asyncio
    async def test_skill_fix_renders_the_non_servable_refusal(self):
        from daemon.tools.skill_tools import create_skill_tools

        manager = MagicMock()
        manager._skill_search_service = MagicMock()
        manager._skill_store_service = MagicMock()
        manager._skill_metrics_service = MagicMock()
        manager._project_skill_source = None
        manager._project_skill_file_repository = None
        manager._project_skill_usage_repository = None
        dispatcher = MagicMock()
        dispatcher.dispatch_fix = MagicMock(
            side_effect=SkillNonServableTargetRefused(
                "refused: non-servable target — skill deadbeef is NOT "
                "servable (is_active=False, status='inactive'). The "
                "evolution lane does not admit FIX jobs against a "
                "deactivated skill. NO skill-keeper job was created."
            )
        )
        manager._skill_job_dispatcher = dispatcher
        tools = {t.name: t for t in create_skill_tools(manager, INSTANCE_ID)}

        result = await tools["skill_fix"].ainvoke(
            {
                "skill_id": str(uuid.uuid4()),
                "issue_description": "stale regex",
            }
        )

        assert "NOT dispatched" in result
        assert SkillNonServableTargetRefused.reason in result
        assert "NO skill-keeper job was created" in result
        assert "NOTHING will be picked up" in result
        # The designed refusal must NOT be reported as a dropped request.
        assert "was DROPPED" not in result


# ── Chokepoint 4 — the REST route renders the typed refusal ───────────────


class TestRestRendering:
    def test_post_fix_returns_409_with_the_reason_token(self):
        from daemon.routers import skills as skills_module

        app = FastAPI()
        api = APIRouter(prefix="/api")
        api.include_router(skills_module.router)
        app.include_router(api)

        dispatcher = MagicMock()
        dispatcher.dispatch_fix = AsyncMock(
            side_effect=SkillNonServableTargetRefused(
                "refused: non-servable target — skill deadbeef is NOT "
                "servable (is_active=False, status='inactive')."
            )
        )
        skills_module.set_skill_job_dispatcher(dispatcher)
        try:
            client = TestClient(app)
            response = client.post(
                "/api/skills/skill-abc/fix",
                json={"issue_description": "broken"},
            )
        finally:
            skills_module.set_skill_job_dispatcher(None)

        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["reason"] == "non_servable_target"
        assert "non-servable target" in detail["error"]
