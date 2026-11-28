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
import logging
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


class _RaisingSkillRepo:
    """A repo whose row read RAISES — the read-error fail-open leg."""

    def get(self, skill_id):
        raise RuntimeError("db down")


class _AttributeLessSkillRepo:
    """A repo whose row read returns a skill row WITHOUT ``is_active``.

    The FIX 8 leg: a MISSING ``is_active`` attribute is UNKNOWN (a model
    rename), not "servable".
    """

    def get(self, skill_id):
        return SimpleNamespace(id=skill_id, status="active")


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
        self, skill_repo, tmp_path, caplog
    ):
        """The typed refusal fires; NO job row is minted; the signal lands."""
        reporter = _RecordingReporter()
        observability.set_anomaly_reporter(reporter)
        dispatcher = _make_dispatcher(skill_repo)
        dead = _make_skill(
            skill_repo, name="f3-dead", is_active=False, status="inactive"
        )

        with caplog.at_level(logging.WARNING):
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

        # FIX 6 (tidier M3): the REST-path F3 refusal carries the HOUSE
        # ``refused:`` prefix — the SAME shape the burst cap logs
        # (``logger.warning("SkillJobDispatcher.enqueue_evolution: %s",
        # message)`` with a ``refused: burst cap …`` message), so a
        # ``refused:``-prefix count catches BOTH refusal paths.
        refusal_logs = [
            r.getMessage()
            for r in caplog.records
            if "refused:" in r.getMessage()
        ]
        assert refusal_logs, (
            "the F3 refusal must log the house 'refused:' prefix — got "
            f"{[r.getMessage() for r in caplog.records]!r}"
        )
        assert any(
            m.startswith(
                "SkillJobDispatcher.enqueue_evolution: refused: "
                "non-servable target"
            )
            for m in refusal_logs
        ), (
            "the refusal log line must carry the burst-cap house shape "
            f"— got {refusal_logs!r}"
        )

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

    @pytest.mark.asyncio
    async def test_read_error_fails_open_and_is_recorded(
        self, skill_repo, tmp_path, caplog
    ):
        """A RAISING row read ⇒ the admission PROCEEDS (fail-open) and
        the degraded posture is RECORDED (a WARNING naming the read
        failure + a ``servability_check_error`` sink record). A
        DEGRADED gate is NOT a refusal: no ``non_servable_target``
        refusal counter and no anomaly row."""
        reporter = _RecordingReporter()
        observability.set_anomaly_reporter(reporter)
        live = _make_skill(skill_repo, name="f3-live3", is_active=True)
        dispatcher = _make_dispatcher(_RaisingSkillRepo())

        with caplog.at_level(logging.WARNING):
            job_id = await dispatcher.enqueue_evolution(
                PROJECT, live.id, EVOLUTION_TYPE_FIX, "fix it"
            )

        # (a) FAIL-OPEN: the admission proceeds — a job id is returned
        # and exactly ONE keeper job was enqueued.
        assert job_id == "job-001"
        assert len(dispatcher._job_service.enqueued) == 1

        # (b) RECORDED: a WARNING names the read failure + the
        # fail-open posture, and the sink carries the
        # ``servability_check_error`` admission record.
        read_error_warnings = [
            r
            for r in caplog.records
            if "FIX servability read failed" in r.getMessage()
        ]
        assert read_error_warnings, (
            "the read-error fail-open must log a WARNING naming the "
            f"read failure — got {[r.getMessage() for r in caplog.records]!r}"
        )
        assert any("FAIL-OPEN" in r.getMessage() for r in read_error_warnings)
        # FIX 6 (tidier L16): the read-failed WARNING NAMES the skill —
        # the same ``[:8]`` shortening the no-repo degraded leg uses.
        assert all(
            live.id[:8] in r.getMessage() for r in read_error_warnings
        ), (
            "the read-failed WARNING must name the skill — got "
            f"{[r.getMessage() for r in read_error_warnings]!r}"
        )
        records = _sink_records(tmp_path)
        error_records = [
            r for r in records if r.get("outcome") == "servability_check_error"
        ]
        assert error_records, (
            "the read-error fail-open must leave a sink record — "
            f"got {records!r}"
        )
        assert any(r.get("stage") == "admission" for r in error_records)

        # (c) A degraded posture is NOT a refusal — the
        # admission-refusal counter stays untouched.
        assert (
            observability.counters().admission_refusals.get(
                "non_servable_target"
            )
            is None
        )

        # (d) NO anomaly row — the fail-open path announces no refusal.
        assert reporter.calls == []

    @pytest.mark.asyncio
    async def test_missing_is_active_attribute_degrades_fail_open_and_is_recorded(
        self, skill_repo, tmp_path, caplog
    ):
        """A MISSING ``is_active`` attribute is UNKNOWN — NOT servable.

        FIX 8 (tidier L1): ``getattr(skill, "is_active", True)`` defaulted
        to SERVABLE when the attribute was missing, so a model rename
        silently DISARMED the F3 gate with NO signal. UNKNOWN now takes the
        EXISTING degraded path — fail open AND RECORDED (a WARNING naming
        the skill + a ``servability_gate_degraded`` admission run record),
        the same shape the no-repo leg uses. ONLY an attribute that is
        PRESENT and falsy means "deactivated/refuse".
        """
        dispatcher = _make_dispatcher(_AttributeLessSkillRepo())
        target_id = str(uuid.uuid4())

        with caplog.at_level(logging.WARNING):
            job_id = await dispatcher.enqueue_evolution(
                PROJECT, target_id, EVOLUTION_TYPE_FIX, "fix it"
            )

        # (a) FAIL-OPEN: the admission proceeds (the execution-time guard
        # stays the backstop).
        assert job_id == "job-001"
        assert len(dispatcher._job_service.enqueued) == 1

        # (b) RECORDED: a WARNING names the skill + the UNKNOWN posture.
        degraded_warnings = [
            r.getMessage()
            for r in caplog.records
            if "DEGRADED" in r.getMessage()
        ]
        assert degraded_warnings, (
            "a missing 'is_active' attribute must log a WARNING — got "
            f"{[r.getMessage() for r in caplog.records]!r}"
        )
        assert any(target_id[:8] in m for m in degraded_warnings), (
            "the degraded WARNING must name the skill — got "
            f"{degraded_warnings!r}"
        )
        assert any("UNKNOWN" in m for m in degraded_warnings), (
            "the degraded WARNING must state the UNKNOWN posture — got "
            f"{degraded_warnings!r}"
        )

        # (c) The sink carries the degraded-posture admission record.
        records = _sink_records(tmp_path)
        degraded_records = [
            r
            for r in records
            if r.get("outcome") == "servability_gate_degraded"
        ]
        assert degraded_records, (
            "the missing-attribute degradation must leave a sink record — "
            f"got {records!r}"
        )
        assert any(
            r.get("degraded_reason") == "missing_is_active_attribute"
            for r in degraded_records
        ), (
            "the degraded record must name the missing attribute — got "
            f"{degraded_records!r}"
        )

        # (d) A degraded posture is NOT a refusal — the admission-refusal
        # counter stays untouched.
        assert (
            observability.counters().admission_refusals.get(
                "non_servable_target"
            )
            is None
        )


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


# ── The F7 metrics-service follow-up legs (soft-fail + name key) ──────────
#
# PLACEMENT NOTE (DIVERGED): these pins exercise
# ``SkillMetricsService.record_feedback`` — the F7 lane's surface. Their
# thematically correct home is ``tests/unit/test_skill_feedback_by_name.py``
# (the F7 pin file), which THIS LANE DOES NOT OWN. They live here because
# this is the only test file this lane owns; the wave-C lane should
# relocate them to the F7 pin file.


class _FakeMetricsConfig:
    ab_sample_size = 10
    ab_min_difference = 0.15
    max_extensions = 3


class _FakeInstanceRepo:
    def get(self, instance_id):
        return None


class _RaiseOnceSkillRepo:
    """Raises on the FIRST ``get`` — the F7 resolution soft-fail leg.

    The raise is scoped to the FIRST call so the pin DISCRIMINATES: the
    F7 RESOLUTION read is the one that raises, and the later
    ``_do_feedback`` reads behave normally — so the ``_do_feedback``
    ``except`` wrapper cannot mask a missing guard on the resolution
    reads (pre-fix, the raise propagated straight out of
    ``record_feedback`` and this pin ERRORED).
    """

    def __init__(self, inner):
        self._inner = inner
        self.get_calls = 0

    def get(self, skill_id):
        self.get_calls += 1
        if self.get_calls == 1:
            raise RuntimeError("db down")
        return self._inner.get(skill_id)

    def get_by_name(self, project_id, name, generation=0):
        return self._inner.get_by_name(project_id, name, generation)


@pytest.fixture
def metrics_service(engine, skill_repo):
    from daemon.repositories.skill.repository import (
        SkillABTestRepository,
        SkillTriggerRepository,
        SkillUsageRepository,
    )
    from daemon.services.skill_metrics_service import SkillMetricsService

    return SkillMetricsService(
        usage_repo=SkillUsageRepository(engine),
        skill_repo=skill_repo,
        trigger_repo=SkillTriggerRepository(engine),
        ab_test_repo=SkillABTestRepository(engine),
        config=_FakeMetricsConfig(),
        instance_repo=_FakeInstanceRepo(),
    )


class TestMetricsServiceResolution:
    @pytest.mark.asyncio
    async def test_raising_resolution_read_soft_fails_without_propagating(
        self, metrics_service, skill_repo, caplog
    ):
        """FIX 2 (tidier M4): F7's resolution reads are INSIDE the soft-fail
        guard — a raising repo read must NOT propagate out of
        ``record_feedback``; it logs a WARNING and the pre-existing
        not-found soft-fail path owns the outcome."""
        repo = _RaiseOnceSkillRepo(skill_repo)
        metrics_service.skill_repo = repo

        with caplog.at_level(logging.WARNING):
            ok = await metrics_service.record_feedback(
                skill_id="name-that-raises",
                instance_id="inst-f7-raise",
                agent_id="a",
                project_id=PROJECT,
                applied=True,
                note="",
            )

        # (a) NO PROPAGATION: the call RETURNED. Pre-fix the raise escaped
        # ``record_feedback`` and this line never ran.
        assert ok is False
        # (b) The resolution read is the one that raised.
        assert repo.get_calls >= 1

        # (c) A WARNING names the resolution failure + the fall-through.
        resolution_warnings = [
            r.getMessage()
            for r in caplog.records
            if "name resolution failed" in r.getMessage()
        ]
        assert resolution_warnings, (
            "a raising resolution read must log a WARNING — got "
            f"{[r.getMessage() for r in caplog.records]!r}"
        )
        assert any(
            "falling through with the raw reference" in m
            for m in resolution_warnings
        ), (
            "the WARNING must state the fall-through posture — got "
            f"{resolution_warnings!r}"
        )

    @pytest.mark.asyncio
    async def test_global_scope_name_lookup_resolves_the_row(
        self, metrics_service, skill_repo
    ):
        """FIX 7 (tidier deferred #5): the NAME-LOOKUP KEY now MATCHES the
        USAGE-RECORD KEY — a global-scope (``None``) caller resolves the
        row (and records the RESOLVED uuid) instead of silently missing."""
        skill = skill_repo.create(
            name="global-scope-skill",
            description="d",
            content="c",
            project_id="",  # ← the usage-record key for the global scope
            generation=0,
        )
        inst = "inst-f7-global"

        ok = await metrics_service.record_feedback(
            skill_id="global-scope-skill",  # ← the NAME, not the uuid
            instance_id=inst,
            agent_id="a",
            project_id=None,  # ← global scope
            applied=True,
            note="by name",
        )

        assert ok is True, (
            "the global-scope name lookup must resolve the row — it "
            "silently missed before FIX 7"
        )
        rec = metrics_service.usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=inst
        )
        assert rec is not None
        assert rec.skill_id == skill.id, (
            "the resolved UUID must be recorded — never the name"
        )
        # The usage row's project key is the SAME expression the name
        # lookup used (the global scope's "" form).
        assert rec.project_id == ""
        assert skill_repo.get(skill.id).total_applied == 1

    @pytest.mark.asyncio
    async def test_null_scoped_global_row_still_resolves_by_name(
        self, metrics_service, skill_repo
    ):
        """FIX 7 compatibility: a skill row that stores NULL in
        ``Skill.project_id`` (the OTHER representation of "global") still
        resolves by name — the pre-fix behaviour is preserved."""
        skill = skill_repo.create(
            name="null-scoped-skill",
            description="d",
            content="c",
            project_id=None,
            generation=0,
        )
        inst = "inst-f7-null-scope"

        ok = await metrics_service.record_feedback(
            skill_id="null-scoped-skill",
            instance_id=inst,
            agent_id="a",
            project_id=None,
            applied=True,
            note="by name",
        )

        assert ok is True
        rec = metrics_service.usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=inst
        )
        assert rec is not None
        assert rec.skill_id == skill.id
