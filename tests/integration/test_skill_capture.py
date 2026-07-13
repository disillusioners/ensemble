"""Integration tests for the CAPTURED-flow eligibility gate.

Phase 5 of the Skill Evolution System. The CAPTURED flow
auto-extracts a reusable skill from a successful task that did
NOT use any existing skill. The eligibility gate lives in
:meth:`SkillMetricsService._check_capture_eligibility` and runs
at the tail of :meth:`SkillMetricsService.record_task_completion`.

Test surface
------------

The three core branches of the gate:

1. **Capture fires** — successful task with high complexity
   (``iterations > capture_min_iterations`` or
   ``duration_seconds > capture_min_duration_seconds``) AND
   no skill applied AND the agent has
   ``skill_injection=True``.
2. **No capture on simple task** — same setup but the task is
   trivial, so the gate refuses the eligibility check.
3. **No capture when skill applied** — same setup but a usage
   record exists with ``feedback_applied=True``, so the success
   is attributed to an existing skill rather than a new pattern.

All three tests route through the real
:class:`SkillMetricsService` against a real (in-memory) SQLite
DB, with the embedding pipeline mocked (no LLM / OpenAI calls)
and the dispatcher replaced with a MagicMock so the test can
assert whether ``enqueue_capture`` was called.
"""

from __future__ import annotations

from types import SimpleNamespace
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
from daemon.services.skill_metrics_service import (
    INJECTED_SKILLS_METADATA_KEY,
    SkillMetricsService,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """In-memory SQLite engine with all skill tables created."""
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
    """SkillEvolutionConfig with thresholds that match the test scenarios."""
    cfg = SkillEvolutionConfig()
    # Keep thresholds at their defaults (5 iterations, 60 seconds)
    # so the test scenarios are explicit about exceeding or not.
    return cfg


@pytest.fixture
def embedding_service():
    """Mocked SkillEmbeddingService — no OpenAI calls."""
    svc = MagicMock()
    svc.update_skill_embeddings = AsyncMock(return_value=0)
    svc.embed_user_message = AsyncMock(return_value=[0.1] * 4)
    return svc


@pytest.fixture
def evolution_service(repos, embedding_service, config):
    """Real SkillEvolutionService backed by the test repos."""
    return SkillEvolutionService(
        skill_repo=repos.skill,
        lineage_repo=repos.lineage,
        usage_repo=repos.usage,
        embedding_service=embedding_service,
        metrics_service=MagicMock(),  # Not used in the gate path
        ab_test_repo=repos.ab_test,
        config=config,
        llm_config={"base_url": "http://test", "api_key": "test"},
    )


@pytest.fixture
def dispatcher():
    """Mocked SkillJobDispatcher — captures enqueue_capture calls.

    ``enqueue_capture`` is awaited in the metrics service so it must
    be an ``AsyncMock``.
    """
    d = MagicMock()
    d.enqueue_capture = AsyncMock(return_value="job-captured")
    d.dispatch_fix = AsyncMock(return_value="job-fix")
    d.enqueue_analysis = AsyncMock(return_value="job-analysis")
    d.enqueue_evolution = AsyncMock(return_value="job-evolution")
    d.enqueue_metric_scan = AsyncMock(return_value="job-scan")
    return d


@pytest.fixture
def agent_id_resolver():
    """agent_id_resolver that returns metadata with skill_injection=True."""
    meta = SimpleNamespace(skill_injection=True)
    return MagicMock(return_value=meta)


@pytest.fixture
def instance_repo():
    """Instance repo with controllable injected-skill metadata.

    By default returns an instance with ``last_injected_skill_ids``
    empty (so capture gating sees "no skill injected"). Individual
    tests can override ``get.return_value`` to inject a skill list.
    """
    inst = SimpleNamespace(instance_metadata={})
    repo = MagicMock()
    repo.get = MagicMock(return_value=inst)
    repo.delete_metadata = MagicMock(return_value=None)
    repo.set_metadata = MagicMock(return_value=None)
    return repo


@pytest.fixture
def metrics_service(repos, config, evolution_service, dispatcher, agent_id_resolver, instance_repo):
    """Real SkillMetricsService wired to test repos + mocks."""
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


def _seed_skill(repos, name: str = "captured-test", project_id: str | None = None) -> str:
    """Create a real skill row and return its id."""
    skill = repos.skill.create(
        name=name,
        description=f"test {name}",
        content="body",
        project_id=project_id,
        category="workflow",
    )
    return skill.id


def _set_injected(repo, instance_id: str, skill_ids: list[str]) -> None:
    """Configure ``instance_repo.get(instance_id)`` to return the skill list."""
    inst = SimpleNamespace(
        instance_id=instance_id,
        instance_metadata={INJECTED_SKILLS_METADATA_KEY: list(skill_ids)},
    )
    repo.get = MagicMock(return_value=inst)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSkillCapture:
    """End-to-end CAPTURED-flow eligibility tests via the metrics service."""

    @pytest.mark.asyncio
    async def test_capture_on_complex_success(
        self,
        metrics_service,
        repos,
        instance_repo,
        dispatcher,
        evolution_service,
    ):
        """Complex success with no skill applied triggers capture."""
        # Seed a skill so the usage-record insert has a target.
        skill_id = _seed_skill(repos)
        _set_injected(instance_repo, "inst-cx-1", [skill_id])

        # ``has_applied_for_instance`` must return False — no skill applied.
        repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        result = await metrics_service.record_task_completion(
            instance_id="inst-cx-1",
            agent_id="developer",
            project_id="p-cx",
            task_succeeded=True,
            iterations=10,        # > capture_min_iterations (5)
            duration_seconds=120,  # > capture_min_duration_seconds (60)
            task_message="teach me about X",
        )

        # A usage record was created.
        assert result == 1
        # And the dispatcher received an enqueue_capture call.
        assert dispatcher.enqueue_capture.await_count == 1
        # enqueue_capture is called with ``(project_id, task_details)``
        # positionally in ``SkillMetricsService._check_capture_eligibility``.
        args, kwargs = dispatcher.enqueue_capture.await_args
        project_id_arg, task_details = args
        assert project_id_arg == "p-cx"
        assert task_details["instance_id"] == "inst-cx-1"
        assert task_details["agent_id"] == "developer"
        assert task_details["iterations"] == 10
        assert task_details["duration_seconds"] == 120
        assert task_details["task_succeeded"] is True

    @pytest.mark.asyncio
    async def test_no_capture_on_simple_task(
        self,
        metrics_service,
        repos,
        instance_repo,
        dispatcher,
    ):
        """Trivial success (low iterations AND short duration) skips capture."""
        skill_id = _seed_skill(repos)
        _set_injected(instance_repo, "inst-cx-2", [skill_id])
        repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        result = await metrics_service.record_task_completion(
            instance_id="inst-cx-2",
            agent_id="developer",
            project_id="p-cx",
            task_succeeded=True,
            iterations=1,   # <= capture_min_iterations (5)
            duration_seconds=5,  # <= capture_min_duration_seconds (60)
            task_message="trivial",
        )

        # Usage record was still inserted (the gate doesn't suppress metrics).
        assert result == 1
        # But the dispatcher was NOT called — the gate refused.
        assert dispatcher.enqueue_capture.await_count == 0

    @pytest.mark.asyncio
    async def test_no_capture_when_skill_applied(
        self,
        metrics_service,
        repos,
        instance_repo,
        dispatcher,
    ):
        """When a usage record has feedback_applied=True, capture is suppressed."""
        skill_id = _seed_skill(repos)
        _set_injected(instance_repo, "inst-cx-3", [skill_id])
        # Skill was applied — success is attributed to it.
        repos.usage.has_applied_for_instance = MagicMock(return_value=True)

        result = await metrics_service.record_task_completion(
            instance_id="inst-cx-3",
            agent_id="developer",
            project_id="p-cx",
            task_succeeded=True,
            iterations=12,
            duration_seconds=200,
            task_message="applied a skill",
        )

        assert result == 1
        # No capture enqueue — the existing skill owns the success.
        assert dispatcher.enqueue_capture.await_count == 0

    @pytest.mark.asyncio
    async def test_no_capture_on_failed_task(
        self,
        metrics_service,
        repos,
        instance_repo,
        dispatcher,
    ):
        """Failed tasks are never eligible for capture, even when complex."""
        skill_id = _seed_skill(repos)
        _set_injected(instance_repo, "inst-cx-4", [skill_id])
        repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        result = await metrics_service.record_task_completion(
            instance_id="inst-cx-4",
            agent_id="developer",
            project_id="p-cx",
            task_succeeded=False,
            iterations=10,
            duration_seconds=120,
            task_message="failed task",
        )

        assert result == 1
        assert dispatcher.enqueue_capture.await_count == 0

    @pytest.mark.asyncio
    async def test_no_capture_when_agent_lacks_skill_injection(
        self,
        metrics_service,
        repos,
        instance_repo,
        dispatcher,
        agent_id_resolver,
    ):
        """Agents without ``skill_injection=True`` never trigger capture."""
        # Override the resolver to disable skill injection for this agent.
        meta = SimpleNamespace(skill_injection=False)
        agent_id_resolver.return_value = meta

        skill_id = _seed_skill(repos)
        _set_injected(instance_repo, "inst-cx-5", [skill_id])
        repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        result = await metrics_service.record_task_completion(
            instance_id="inst-cx-5",
            agent_id="developer",
            project_id="p-cx",
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
            task_message="complex but no injection",
        )

        assert result == 1
        assert dispatcher.enqueue_capture.await_count == 0

    @pytest.mark.asyncio
    async def test_no_capture_when_no_skills_injected(
        self,
        metrics_service,
        repos,
        instance_repo,
        dispatcher,
    ):
        """No ``last_injected_skill_ids`` metadata → no metrics, no capture."""
        # instance_repo already returns empty metadata by default.
        result = await metrics_service.record_task_completion(
            instance_id="inst-cx-6",
            agent_id="developer",
            project_id="p-cx",
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
            task_message="no skills",
        )

        # No skills to record → 0 records.
        assert result == 0
        assert dispatcher.enqueue_capture.await_count == 0

    @pytest.mark.asyncio
    async def test_metrics_recorded_even_when_capture_fires(
        self,
        metrics_service,
        repos,
        instance_repo,
        dispatcher,
    ):
        """Capture gating is downstream of metrics — both fire on success."""
        skill_id = _seed_skill(repos)
        _set_injected(instance_repo, "inst-cx-7", [skill_id])
        repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        # Read the skill row before so we can compare counters after.
        before = repos.skill.get(skill_id)
        assert before.total_selections == 0

        result = await metrics_service.record_task_completion(
            instance_id="inst-cx-7",
            agent_id="developer",
            project_id="p-cx",
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
            task_message="combined",
        )

        assert result == 1
        # Counter bumped.
        after = repos.skill.get(skill_id)
        assert after.total_selections == 1
        assert after.total_completions == 1
        # Capture also fired.
        assert dispatcher.enqueue_capture.await_count == 1

    @pytest.mark.asyncio
    async def test_failed_task_bumps_consecutive_failures_without_capture(
        self,
        metrics_service,
        repos,
        instance_repo,
        dispatcher,
    ):
        """Failed task → consecutive_failures +1, no capture."""
        skill_id = _seed_skill(repos)
        _set_injected(instance_repo, "inst-cx-8", [skill_id])
        repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        await metrics_service.record_task_completion(
            instance_id="inst-cx-8",
            agent_id="developer",
            project_id="p-cx",
            task_succeeded=False,
            iterations=10,
            duration_seconds=120,
            task_message="failed",
        )

        skill = repos.skill.get(skill_id)
        assert skill.total_selections == 1
        assert skill.total_completions == 0
        assert skill.consecutive_failures == 1
        assert dispatcher.enqueue_capture.await_count == 0


class TestCaptureServiceLevel:
    """Direct exercise of :meth:`SkillEvolutionService.check_and_capture`.

    These tests bypass the metrics-service layer and exercise the
    evolution service's gate directly so the per-rule semantics are
    explicit. They use the same in-memory DB as
    :class:`TestSkillCapture`.
    """

    @pytest.mark.asyncio
    async def test_check_and_capture_returns_task_details_when_eligible(
        self, evolution_service, repos,
    ):
        # No usage records for this instance → has_applied returns False.
        repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        details = await evolution_service.check_and_capture(
            instance_id="inst-1",
            agent_id="developer",
            project_id="p-1",
            task_message="auto-capture me",
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
        )

        assert details is not None
        assert details["instance_id"] == "inst-1"
        assert details["iterations"] == 10
        assert details["duration_seconds"] == 120

    @pytest.mark.asyncio
    async def test_check_and_capture_returns_none_when_failed(
        self, evolution_service, repos,
    ):
        details = await evolution_service.check_and_capture(
            instance_id="inst-1",
            agent_id="developer",
            project_id="p-1",
            task_message="failed",
            task_succeeded=False,
            iterations=10,
            duration_seconds=120,
        )
        assert details is None

    @pytest.mark.asyncio
    async def test_check_and_capture_returns_none_when_trivial(
        self, evolution_service, repos,
    ):
        details = await evolution_service.check_and_capture(
            instance_id="inst-1",
            agent_id="developer",
            project_id="p-1",
            task_message="trivial",
            task_succeeded=True,
            iterations=1,
            duration_seconds=5,
        )
        assert details is None

    @pytest.mark.asyncio
    async def test_check_and_capture_returns_none_when_skill_already_applied(
        self, evolution_service, repos,
    ):
        repos.usage.has_applied_for_instance = MagicMock(return_value=True)
        details = await evolution_service.check_and_capture(
            instance_id="inst-1",
            agent_id="developer",
            project_id="p-1",
            task_message="applied",
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
        )
        assert details is None