"""Cross-phase integration test — Flow C (CAPTURED flow).

Skill Evolution end-to-end flow:

    Successful Task (no skill applied)
        → Complexity Check
        → Capture Job Enqueued (system_parallel_queue)
        → Skill-Keeper Captures
        → New Skill Created (``lineage_origin='captured'``)

Verifies the full CAPTURED pipeline across the three phases that
own these services:

* **Phase 4** — :class:`SkillMetricsService.record_task_completion`
  writes denormalized counters on the ``skills`` row, then runs
  :meth:`SkillMetricsService._check_capture_eligibility` at the
  tail. The gate enforces five preconditions (evolution wired,
  task succeeded, agent ``skill_injection=True``, no skill applied,
  complexity threshold).
* **Phase 4/5** — :class:`SkillEvolutionService.check_and_capture`
  re-validates ``has_applied_for_instance`` to close the TOCTOU
  window and returns a ``task_details`` dict.
* **Phase 5** — :class:`SkillJobDispatcher.enqueue_capture` routes
  a ``skill_capture`` JobItem to ``system_parallel_queue`` (NOT
  ``system_fifo_queue``).
* **Phase 5** — :class:`SkillEvolutionService._evolve_captured`
  asks the LLM for ``{name, description, content}`` and creates a
  new ``Skill`` row with ``lineage_origin='captured'``,
  ``generation=0``, ``status='active'``, ``category='workflow'``
  — and NO parent (captured skills are standalone).

The test runs against an in-memory SQLite engine with real
SQLModel tables and real services — the only mocks are the
``SkillJobDispatcher`` collaborators (``job_service`` +
``queue_repo``) for routing assertions, and the
``SkillEvolutionService._call_llm`` chokepoint so the suite runs
without a real OpenAI key. The embedding service is stubbed
because Phase 2's embedding pipeline is exercised elsewhere.

Each step asserts the state carried over from the previous step,
so a regression that breaks one phase is caught even when the
bug only manifests in a later phase.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.config import SkillEvolutionConfig
from daemon.repositories.skill import (
    SkillABTestRepository,
    SkillEmbeddingRepository,
    SkillLineageRepository,
    SkillRepository,
    SkillTriggerRepository,
    SkillUsageRepository,
)
from daemon.services.skill_evolution_service import SkillEvolutionService
from daemon.services.skill_job_dispatcher import (
    JOB_TYPE_CAPTURE,
    PARALLEL_QUEUE_NAME,
    SkillJobDispatcher,
)
from daemon.services.skill_metrics_service import (
    INJECTED_SKILLS_METADATA_KEY,
    SkillMetricsService,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared constants for Flow C
# ---------------------------------------------------------------------------

PROJECT_ID: str = "test-project-flow-c"
PARALLEL_QUEUE_ID: str = "q-parallel-flow-c"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """In-memory SQLite engine with all six skill tables created."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repos(engine):
    """All six skill repositories bound to the test engine."""
    return SimpleNamespace(
        skill=SkillRepository(engine),
        lineage=SkillLineageRepository(engine),
        usage=SkillUsageRepository(engine),
        trigger=SkillTriggerRepository(engine),
        embedding=SkillEmbeddingRepository(engine),
        ab_test=SkillABTestRepository(engine),
    )


@pytest.fixture
def config():
    """Default SkillEvolutionConfig — thresholds at 5 iterations / 60 seconds."""
    return SkillEvolutionConfig()


@pytest.fixture
def embedding_service():
    """Mocked SkillEmbeddingService — no OpenAI calls."""
    svc = MagicMock()
    svc.update_skill_embeddings = AsyncMock(return_value=1)
    return svc


@pytest.fixture
def fake_metrics_service():
    """Mock metrics service used by SkillEvolutionService for stats queries."""
    svc = MagicMock()
    svc.get_skill_stats = AsyncMock(
        return_value={
            "total_selections": 0,
            "total_applied": 0,
            "total_completions": 0,
            "total_fallbacks": 0,
            "completion_rate": 0.0,
            "fallback_rate": 0.0,
            "applied_rate": 0.0,
            "consecutive_failures": 0,
        }
    )
    return svc


@pytest.fixture
def dispatcher():
    """Real SkillJobDispatcher with a fake parallel queue ID.

    The CAPTURED eligibility gate calls ``enqueue_capture`` (an
    ``async`` method on the dispatcher) which routes to
    ``system_parallel_queue``. We mock the two collaborators so the
    routing invariant is assertable:

    * ``job_service.enqueue`` returns a deterministic ``job_id``.
    * ``queue_repo.get_by_name`` resolves
      ``system_parallel_queue`` to ``PARALLEL_QUEUE_ID``.

    This mirrors the wiring from ``Flow B``'s dispatcher fixture.
    """
    job_service = MagicMock()

    class _FakeJob:
        def __init__(self, job_id: str) -> None:
            self.job_id = job_id

    job_service.enqueue = AsyncMock(return_value=_FakeJob("job-flow-c"))

    class _FakeQueue:
        queue_id = PARALLEL_QUEUE_ID
        queue_name = PARALLEL_QUEUE_NAME

    queue_repo = MagicMock()
    queue_repo.get_by_name = MagicMock(return_value=_FakeQueue())

    return SkillJobDispatcher(
        job_service=job_service,
        queue_repo=queue_repo,
    )


@pytest.fixture
def agent_id_resolver():
    """Agent-id resolver returning metadata with ``skill_injection=True``.

    Capture is a feature of the skill-injection subsystem — agents
    without ``skill_injection=True`` are skipped by the gate
    (see Gate 3 of ``SkillMetricsService._check_capture_eligibility``).
    """
    meta = SimpleNamespace(skill_injection=True)
    return MagicMock(return_value=meta)


@pytest.fixture
def instance_repo():
    """Instance repo with controllable injected-skill metadata.

    By default returns an instance whose
    ``last_injected_skill_ids`` is empty — i.e. "no skill was
    injected on this instance". The capture gate's Gate 4 also
    checks ``has_applied_for_instance``; that's stubbed per-test
    on the usage repo (see helpers below).
    """
    inst = SimpleNamespace(instance_metadata={})
    repo = MagicMock()
    repo.get = MagicMock(return_value=inst)
    repo.delete_metadata = MagicMock(return_value=None)
    repo.set_metadata = MagicMock(return_value=None)
    return repo


@pytest.fixture
def evolution_service(repos, embedding_service, fake_metrics_service, config):
    """Real ``SkillEvolutionService`` wired against the real repos.

    LLM calls are stubbed per-test via
    ``patch.object(service, '_call_llm')`` so the suite runs
    offline (no OpenAI calls).
    """
    return SkillEvolutionService(
        skill_repo=repos.skill,
        lineage_repo=repos.lineage,
        usage_repo=repos.usage,
        embedding_service=embedding_service,
        metrics_service=fake_metrics_service,
        ab_test_repo=repos.ab_test,
        config=config,
        llm_config={
            "base_url": "http://test",
            "api_key": "test-key",
            "model": "gpt-4o",
        },
    )


@pytest.fixture
def metrics_service(
    repos,
    config,
    evolution_service,
    dispatcher,
    agent_id_resolver,
    instance_repo,
):
    """Real ``SkillMetricsService`` wired to test repos + mocks.

    The dispatcher is attached via ``set_job_dispatcher`` (the
    production setter that the metrics service exposes for late
    binding — the dispatcher may not be available at construction
    time during boot).
    """
    svc = SkillMetricsService(
        usage_repo=repos.usage,
        skill_repo=repos.skill,
        trigger_repo=repos.trigger,
        ab_test_repo=repos.ab_test,
        config=config,
        instance_repo=instance_repo,
        evolution_service=evolution_service,
        agent_id_resolver=agent_id_resolver,
    )
    svc.set_job_dispatcher(dispatcher)
    return svc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_skill(
    repos,
    name: str = "seed-skill-flow-c",
    project_id: str | None = PROJECT_ID,
) -> Any:
    """Create a real skill row and return it."""
    return repos.skill.create(
        name=name,
        description=f"desc for {name}",
        content=f"content for {name}",
        project_id=project_id,
        category="workflow",
    )


def _set_injected(repo, instance_id: str, skill_ids: list[str]) -> None:
    """Configure ``instance_repo.get(instance_id)`` to advertise these skill ids."""
    inst = SimpleNamespace(
        instance_id=instance_id,
        instance_metadata={INJECTED_SKILLS_METADATA_KEY: list(skill_ids)},
    )
    repo.get = MagicMock(return_value=inst)


# ---------------------------------------------------------------------------
# Step 1 — Successful task with no skill applied triggers the gate
# ---------------------------------------------------------------------------


class TestStep1TaskCompletionOpensCaptureGate:
    """Step 1: ``record_task_completion`` for a successful task with
    no skill applied reaches the capture gate and
    ``enqueue_capture`` is invoked.
    """

    async def test_complex_success_enqueues_capture(
        self,
        metrics_service,
        repos,
        instance_repo,
        dispatcher,
    ):
        """Complex success (iterations > 5 AND duration > 60s)
        with no skill applied → dispatcher.enqueue_capture fires.
        """
        skill_id = _seed_skill(repos, "complex-target").id
        _set_injected(instance_repo, "inst-flow-c-1", [skill_id])
        # No skill was actually applied on this instance.
        repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        result = await metrics_service.record_task_completion(
            instance_id="inst-flow-c-1",
            agent_id="developer",
            project_id=PROJECT_ID,
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
            task_message="auto-capture me",
        )

        # Usage record still inserted (gate runs AFTER metrics).
        assert result == 1
        # enqueue_capture was called exactly once.
        assert dispatcher._job_service.enqueue.await_count == 1

    async def test_trivial_success_skips_capture(
        self,
        metrics_service,
        repos,
        instance_repo,
        dispatcher,
    ):
        """Trivial success (low iterations AND short duration)
        → complexity gate refuses; no capture enqueued.
        """
        skill_id = _seed_skill(repos, "trivial-target").id
        _set_injected(instance_repo, "inst-flow-c-triv", [skill_id])
        repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        result = await metrics_service.record_task_completion(
            instance_id="inst-flow-c-triv",
            agent_id="developer",
            project_id=PROJECT_ID,
            task_succeeded=True,
            iterations=1,        # <= 5
            duration_seconds=5,  # <= 60
            task_message="trivial",
        )

        # Usage record inserted; gate refused so no enqueue.
        assert result == 1
        assert dispatcher._job_service.enqueue.await_count == 0

    async def test_failed_task_skips_capture(
        self,
        metrics_service,
        repos,
        instance_repo,
        dispatcher,
    ):
        """Failed task → gate2 refuses; no capture enqueued."""
        skill_id = _seed_skill(repos, "failed-target").id
        _set_injected(instance_repo, "inst-flow-c-fail", [skill_id])
        repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        result = await metrics_service.record_task_completion(
            instance_id="inst-flow-c-fail",
            agent_id="developer",
            project_id=PROJECT_ID,
            task_succeeded=False,
            iterations=10,
            duration_seconds=120,
            task_message="failure path",
        )

        assert result == 1
        assert dispatcher._job_service.enqueue.await_count == 0

    async def test_skill_already_applied_skips_capture(
        self,
        metrics_service,
        repos,
        instance_repo,
        dispatcher,
    ):
        """When ``has_applied_for_instance`` is True, the success
        is attributed to the existing skill — gate4 refuses;
        no capture enqueued.
        """
        skill_id = _seed_skill(repos, "applied-target").id
        _set_injected(instance_repo, "inst-flow-c-app", [skill_id])
        repos.usage.has_applied_for_instance = MagicMock(return_value=True)

        result = await metrics_service.record_task_completion(
            instance_id="inst-flow-c-app",
            agent_id="developer",
            project_id=PROJECT_ID,
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
            task_message="skill already did it",
        )

        assert result == 1
        assert dispatcher._job_service.enqueue.await_count == 0


# ---------------------------------------------------------------------------
# Step 2 — Capture job routes to system_parallel_queue
# ---------------------------------------------------------------------------


class TestStep2CaptureJobRoutedToParallelQueue:
    """Step 2: ``SkillJobDispatcher.enqueue_capture`` enqueues a
    ``skill_capture`` JobItem on ``system_parallel_queue`` (NOT
    ``system_fifo_queue``).

    This mirrors the routing invariant asserted for ``enqueue_analysis``
    in Flow B.
    """

    async def test_capture_job_routes_to_parallel_queue(
        self,
        metrics_service,
        repos,
        instance_repo,
        dispatcher,
    ):
        """The capture job's ``queue_id`` resolves to the parallel queue."""
        skill_id = _seed_skill(repos, "routing-target").id
        _set_injected(instance_repo, "inst-flow-c-rt", [skill_id])
        repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        await metrics_service.record_task_completion(
            instance_id="inst-flow-c-rt",
            agent_id="developer",
            project_id=PROJECT_ID,
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
            task_message="check routing",
        )

        kwargs = dispatcher._job_service.enqueue.await_args.kwargs

        # Routing invariant: parallel queue, not None (FIFO fallback).
        assert kwargs["queue_id"] == PARALLEL_QUEUE_ID
        assert kwargs["queue_id"] is not None
        # Job-type and agent identify the skill-keeper lane.
        assert kwargs["job_type"] == JOB_TYPE_CAPTURE
        assert kwargs["job_type"] == "skill_capture"
        assert kwargs["agent_id"] == "skill-keeper"
        assert kwargs["source"] == "skill_evolution"
        assert kwargs["project_id"] == PROJECT_ID

        # Metadata carries the full task context for the worker.
        meta = kwargs["metadata"]
        assert "task_details" in meta
        task_details = meta["task_details"]
        assert task_details["instance_id"] == "inst-flow-c-rt"
        assert task_details["agent_id"] == "developer"
        assert task_details["project_id"] == PROJECT_ID
        assert task_details["iterations"] == 10
        assert task_details["duration_seconds"] == 120
        assert task_details["task_succeeded"] is True
        assert task_details["task_message"] == "check routing"

    async def test_parallel_queue_lookup_uses_correct_name(
        self,
        metrics_service,
        repos,
        instance_repo,
        dispatcher,
    ):
        """``queue_repo.get_by_name`` was queried with ``system_parallel_queue``."""
        skill_id = _seed_skill(repos, "queue-name-target").id
        _set_injected(instance_repo, "inst-flow-c-qn", [skill_id])
        repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        await metrics_service.record_task_completion(
            instance_id="inst-flow-c-qn",
            agent_id="developer",
            project_id=PROJECT_ID,
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
            task_message="queue name check",
        )

        dispatcher._queue_repo.get_by_name.assert_called()
        call_args = dispatcher._queue_repo.get_by_name.call_args
        # (project_id, queue_name) positional args.
        assert call_args.args[1] == PARALLEL_QUEUE_NAME
        assert call_args.args[1] == "system_parallel_queue"
        assert call_args.args[0] == PROJECT_ID


# ---------------------------------------------------------------------------
# Step 3 — Skill-keeper performs the capture via _evolve_captured
# ---------------------------------------------------------------------------


class TestStep3SkillKeeperPerformsCapture:
    """Step 3: ``SkillEvolutionService._evolve_captured(task_details)``
    asks the LLM for ``{name, description, content}`` and creates
    a new ``Skill`` row with ``lineage_origin='captured'``.
    """

    async def test_evolve_captured_creates_skill_with_correct_lineage(
        self,
        evolution_service,
        repos,
    ):
        """A captured skill row is created with lineage_origin='captured'."""
        llm_payload = json.dumps({
            "name": "auto-captured-flow-c",
            "description": "Auto-extracted from a complex successful task",
            "content": "## Captured body\nDo the thing systematically.",
        })

        task_details = {
            "instance_id": "inst-flow-c-cap",
            "agent_id": "developer",
            "project_id": PROJECT_ID,
            "task_message": "auto-capture me",
            "iterations": 10,
            "duration_seconds": 120,
            "task_succeeded": True,
        }

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                evolution_service,
                "_call_llm",
                AsyncMock(return_value=llm_payload),
            )
            result = await evolution_service._evolve_captured(task_details)

        # The result reports the new id and the call was not skipped.
        assert result["skipped"] is False
        assert "new_skill_id" in result
        new_skill_id = result["new_skill_id"]
        assert new_skill_id

        # CRITICAL: the persisted row carries lineage_origin='captured'.
        new_skill = repos.skill.get(new_skill_id)
        assert new_skill is not None
        assert new_skill.lineage_origin == "captured"
        assert new_skill.generation == 0
        assert new_skill.status == "active"
        assert new_skill.category == "workflow"
        assert new_skill.name == "auto-captured-flow-c"
        assert new_skill.description == (
            "Auto-extracted from a complex successful task"
        )
        assert "Captured body" in new_skill.content

    async def test_evolve_captured_has_no_parent_lineage(
        self,
        evolution_service,
        repos,
    ):
        """Captured skills are standalone: no ``SkillLineage`` edge."""
        llm_payload = json.dumps({
            "name": "standalone-capture",
            "description": "no parent",
            "content": "body",
        })
        task_details = {
            "task_message": "standalone",
            "iterations": 8,
            "duration_seconds": 90,
            "agent_id": "developer",
            "project_id": PROJECT_ID,
        }

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                evolution_service,
                "_call_llm",
                AsyncMock(return_value=llm_payload),
            )
            result = await evolution_service._evolve_captured(task_details)

        # No parent edges: lineage table stays empty for this skill.
        parents = repos.lineage.get_parents(result["new_skill_id"])
        assert parents == []

    async def test_evolve_captured_with_existing_skill_source(
        self,
        evolution_service,
        repos,
    ):
        """When ``task_details['skill']`` is set, the existing
        skill is used as a prompt source — but it is NOT a parent.
        The new skill still has ``lineage_origin='captured'`` and
        no lineage edges.
        """
        source_skill = _seed_skill(repos, "source-skill-flow-c")
        llm_payload = json.dumps({
            "name": "captured-from-source",
            "description": "uses source as prompt seed",
            "content": "body",
        })
        task_details = {
            "skill": source_skill,
            "task_message": "capture direction",
            "iterations": 10,
            "duration_seconds": 60,
            "agent_id": "developer",
            "project_id": PROJECT_ID,
        }

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                evolution_service,
                "_call_llm",
                AsyncMock(return_value=llm_payload),
            )
            result = await evolution_service._evolve_captured(task_details)

        new_skill = repos.skill.get(result["new_skill_id"])
        assert new_skill.lineage_origin == "captured"
        # The source skill is NOT a parent.
        parents = repos.lineage.get_parents(result["new_skill_id"])
        assert parents == []
        assert result["new_skill_id"] != source_skill.id

    async def test_evolve_captured_empty_details_raises(self, evolution_service):
        """Empty ``task_details`` raises ``ValueError``."""
        with pytest.raises(ValueError):
            await evolution_service._evolve_captured({})
        with pytest.raises(ValueError):
            await evolution_service._evolve_captured(None)

    async def test_evolve_captured_embedding_failure_is_swallowed(
        self,
        evolution_service,
        repos,
        embedding_service,
    ):
        """Embedding refresh may fail (the service degrades gracefully);
        the captured skill row is still created.
        """
        embedding_service.update_skill_embeddings.side_effect = (
            RuntimeError("embedding service down")
        )

        llm_payload = json.dumps({
            "name": "embed-fail-capture",
            "description": "embed failed",
            "content": "body",
        })
        task_details = {
            "task_message": "embed test",
            "iterations": 8,
            "duration_seconds": 90,
            "agent_id": "developer",
            "project_id": PROJECT_ID,
        }

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                evolution_service,
                "_call_llm",
                AsyncMock(return_value=llm_payload),
            )
            result = await evolution_service._evolve_captured(task_details)

        # The skill row exists despite the embedding failure.
        new_skill = repos.skill.get(result["new_skill_id"])
        assert new_skill is not None
        assert new_skill.lineage_origin == "captured"
        # Embedding service was attempted (graceful degradation).
        assert embedding_service.update_skill_embeddings.await_count >= 1


# ---------------------------------------------------------------------------
# Step 4 — Phase 4 metrics + Phase 5 capture must agree on the state
# ---------------------------------------------------------------------------


class TestStep4MetricsAndCaptureAgree:
    """Step 4: After a successful capture, the denormalized counters
    reflect the captured instance AND a new skill with
    ``lineage_origin='captured'`` exists. The two phases share the
    same DB and must agree.
    """

    async def test_full_flow_c_pipeline(
        self,
        metrics_service,
        evolution_service,
        repos,
        instance_repo,
        dispatcher,
        embedding_service,
    ):
        """End-to-end Flow C happy path.

        Successful task → complexity gate → capture job enqueued
        on system_parallel_queue → skill-keeper captures →
        new skill with ``lineage_origin='captured'`` exists.
        """
        # Seed an existing skill so the metrics path has a usage row.
        existing_skill_id = _seed_skill(repos, "pre-existing").id
        _set_injected(instance_repo, "inst-flow-c-e2e", [existing_skill_id])
        # No skill was actually applied on this instance.
        repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        # ── Phase 4: Task completion triggers the capture gate ────
        result = await metrics_service.record_task_completion(
            instance_id="inst-flow-c-e2e",
            agent_id="developer",
            project_id=PROJECT_ID,
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
            task_message="complex success — capture me",
        )
        assert result == 1

        # ── Phase 4/5: Capture job enqueued to system_parallel_queue ─
        assert dispatcher._job_service.enqueue.await_count == 1
        dispatch_kwargs = dispatcher._job_service.enqueue.await_args.kwargs
        assert dispatch_kwargs["queue_id"] == PARALLEL_QUEUE_ID
        assert dispatch_kwargs["job_type"] == "skill_capture"

        task_details = dispatch_kwargs["metadata"]["task_details"]
        assert task_details["task_succeeded"] is True
        assert task_details["iterations"] == 10
        assert task_details["duration_seconds"] == 120

        # ── Phase 5: Skill-keeper captures via _evolve_captured ───
        llm_payload = json.dumps({
            "name": "captured-e2e-flow-c",
            "description": "End-to-end captured skill",
            "content": (
                "## Captured body\n"
                "Auto-extracted from a complex successful task "
                "that didn't apply an existing skill."
            ),
        })

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                evolution_service,
                "_call_llm",
                AsyncMock(return_value=llm_payload),
            )
            capture_result = await evolution_service._evolve_captured(
                task_details
            )

        # ── Phase 5: New skill row created with correct lineage ───
        assert capture_result["skipped"] is False
        new_skill_id = capture_result["new_skill_id"]
        assert new_skill_id != existing_skill_id

        new_skill = repos.skill.get(new_skill_id)
        assert new_skill is not None
        assert new_skill.lineage_origin == "captured"
        assert new_skill.generation == 0
        assert new_skill.status == "active"
        assert new_skill.category == "workflow"
        assert new_skill.project_id == PROJECT_ID
        assert new_skill.name == "captured-e2e-flow-c"

        # The new skill has no parent lineage edges (standalone).
        parents = repos.lineage.get_parents(new_skill_id)
        assert parents == []

        # ── Final DB state ─────────────────────────────────────────
        # The pre-existing skill is unchanged by the capture flow.
        existing_after = repos.skill.get(existing_skill_id)
        assert existing_after is not None
        assert existing_after.id == existing_skill_id
        # Its lineage_origin is whatever the seed set it to (default
        # 'imported'), NOT 'captured' — only the new skill is captured.
        assert existing_after.lineage_origin != "captured"

        # The captured skill is the only one tagged 'captured' across
        # the whole project (the seeded skill uses the default
        # 'imported' origin).
        all_skills, _total = repos.skill.list(project_id=PROJECT_ID)
        captured = [s for s in all_skills if s.lineage_origin == "captured"]
        assert len(captured) == 1
        assert captured[0].id == new_skill_id