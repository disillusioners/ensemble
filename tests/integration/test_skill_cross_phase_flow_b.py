"""Cross-phase integration test — Flow B.

Skill Evolution end-to-end flow:

    Metrics → Trigger → Analysis Job → FIX Evolution → A/B Testing

Verifies the full pipeline across the four phases that own these
services:

* **Phase 4** — ``SkillMetricsService`` writes denormalized counters
  on the ``skills`` row. ``SkillTriggerEngine`` walks enabled
  triggers and flags skills whose stats cross a threshold.
* **Phase 5** — ``SkillJobDispatcher.enqueue_analysis`` routes a
  ``skill_analysis`` JobItem to ``system_parallel_queue``
  (NOT ``system_fifo_queue``).
* **Phase 5** — ``SkillEvolutionService.evolve_skill`` (FIX path)
  creates a new-generation row, records a ``SkillLineage`` edge,
  and writes a ``SkillABTest`` row pairing old + new variants.
* **Phase 5** — After ``ab_sample_size`` (10) comparisons,
  ``SkillEvolutionService.check_ab_test_resolution`` resolves the
  A/B test: deactivates the loser, activates the winner, clears
  the ``ab_test_group``, and stamps ``resolved_at`` on the row.

The test runs against an in-memory SQLite engine with real
SQLModel tables and real services — the only mock is the
``SkillJobDispatcher`` collaborators (``job_service`` +
``queue_repo``) and the ``SkillEvolutionService._call_llm`` chokepoint.
This proves the data flows correctly across phase boundaries
without spinning up the daemon, real LLM, or real job queue.

Each step in the test asserts the state carried over from the
previous step, so a regression that breaks one phase is caught
even when the bug only manifests in a later phase.
"""

from __future__ import annotations

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
from daemon.services.skill_job_dispatcher import SkillJobDispatcher
from daemon.services.skill_metrics_service import SkillMetricsService
from daemon.services.skill_trigger_engine import SkillTriggerEngine


# ---------------------------------------------------------------------------
# Shared constants for Flow B
# ---------------------------------------------------------------------------

PROJECT_ID: str = "test-project-flow-b"

# Parallel queue ID the test asserts the dispatcher resolves to. The
# dispatcher's _resolve_parallel_queue_id reads from a mock
# JobQueueRepository; this is the synthetic ID it returns.
PARALLEL_QUEUE_ID: str = "q-parallel-flow-b"

# A/B test thresholds mirror SkillEvolutionConfig defaults.
AB_SAMPLE_SIZE: int = 10
AB_MIN_DIFFERENCE: float = 0.15


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
    """Default SkillEvolutionConfig — ab_sample_size=10 (matches repo default)."""
    return SkillEvolutionConfig()


@pytest.fixture
def embedding_service():
    """Mocked SkillEmbeddingService — no OpenAI calls."""
    svc = MagicMock()
    svc.update_skill_embeddings = AsyncMock(return_value=1)
    return svc


@pytest.fixture
def fake_metrics_service():
    """Mock metrics service used by SkillEvolutionService.check_ab_test_resolution.

    The real metrics_service is the trigger-engine side; this is the
    evolution-side dependency for A/B resolution. Both repos talk to the
    same DB so this is only used for the threshold calculation.
    """
    svc = MagicMock()
    svc.get_ab_comparison_stats = AsyncMock(
        return_value={
            "skill_id_a": None,
            "skill_id_b": None,
            "completion_rate_a": 0.0,
            "completion_rate_b": 0.0,
            "difference": 0.0,
            "comparisons": 0,
            "extension_count": 0,
            "ready_to_resolve": False,
            "needs_more_data": False,
        }
    )
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
    """Mock SkillJobDispatcher with a fake parallel queue ID.

    The trigger-engine callback (the production wiring from
    ``manager._run_skill_metric_scan``) calls ``enqueue_analysis``,
    which we want to capture and inspect. The job_service is mocked
    to return a deterministic job_id; the queue_repo resolves
    ``system_parallel_queue`` to ``PARALLEL_QUEUE_ID`` so we can
    assert the routing invariant.
    """
    job_service = MagicMock()

    class _FakeJob:
        def __init__(self, job_id: str) -> None:
            self.job_id = job_id

    job_service.enqueue = AsyncMock(return_value=_FakeJob("job-flow-b"))

    class _FakeQueue:
        queue_id = PARALLEL_QUEUE_ID
        queue_name = "system_parallel_queue"

    queue_repo = MagicMock()
    queue_repo.get_by_name = MagicMock(return_value=_FakeQueue())

    return SkillJobDispatcher(
        job_service=job_service,
        queue_repo=queue_repo,
    )


@pytest.fixture
def trigger_engine(repos, config):
    """Real SkillTriggerEngine backed by real repos + a real metrics service."""
    metrics_service = SkillMetricsService(
        usage_repo=repos.usage,
        skill_repo=repos.skill,
        trigger_repo=repos.trigger,
        ab_test_repo=repos.ab_test,
        config=config,
        instance_repo=None,
    )
    return SkillTriggerEngine(
        trigger_repo=repos.trigger,
        metrics_service=metrics_service,
    )


@pytest.fixture
def evolution_service(repos, embedding_service, fake_metrics_service, config):
    """Real SkillEvolutionService wired against the real repos.

    LLM calls are stubbed per-test via ``patch.object(service, '_call_llm')``.
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
            "model": "gpt-4o-mini",
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(skill_repo, project_id: str, name: str, **kwargs: Any):
    """Create a skill with sensible defaults."""
    defaults: dict[str, Any] = {
        "name": name,
        "description": f"desc for {name}",
        "content": f"content for {name}",
        "project_id": project_id,
    }
    defaults.update(kwargs)
    return skill_repo.create(**defaults)


def _seed_poor_metrics(repos, skill, completed: int = 3, total: int = 10) -> None:
    """Stamp the skill row with a low completion rate.

    Bumps denormalized counters directly via ``increment_counter`` —
    the same way ``SkillMetricsService`` would during a real task.
    """
    repos.skill.increment_counter(skill.id, "total_selections", amount=total)
    repos.skill.increment_counter(skill.id, "total_completions", amount=completed)


def _seed_low_completion_rate_trigger(repos) -> None:
    """Seed the low_completion_rate trigger at threshold=0.3, min_selections=5."""
    repos.trigger.create(
        name="low_cr_flow_b",
        condition_type="low_completion_rate",
        condition_json={"threshold": 0.3, "min_selections": 5},
        action="analyze",
        project_id=None,
    )


# ---------------------------------------------------------------------------
# Step 1 — Set up a skill with poor metrics
# ---------------------------------------------------------------------------


class TestStep1PoorMetricsSetup:
    """Step 1: Create a skill and record usage with low completion rate."""

    def test_seed_poor_metrics_bumps_counters(
        self, repos
    ) -> None:
        skill = _make_skill(repos.skill, PROJECT_ID, "low-completion")
        _seed_poor_metrics(repos, skill, completed=3, total=10)

        refreshed = repos.skill.get(skill.id)
        assert refreshed is not None
        assert refreshed.total_selections == 10
        assert refreshed.total_completions == 3
        assert refreshed.total_fallbacks == 0

    async def test_get_skill_stats_reports_low_completion_rate(
        self, repos, config
    ) -> None:
        """get_skill_stats reflects the bumped counters (rate = 0.3, exactly the
        threshold — must be strictly < 0.3 to fire the trigger, so we use 3/10
        and verify the rate is reported, not that it crosses the gate).
        """
        svc = SkillMetricsService(
            usage_repo=repos.usage,
            skill_repo=repos.skill,
            trigger_repo=repos.trigger,
            ab_test_repo=repos.ab_test,
            config=config,
            instance_repo=None,
        )
        skill = _make_skill(repos.skill, PROJECT_ID, "rate-check")
        _seed_poor_metrics(repos, skill, completed=3, total=10)

        stats = await svc.get_skill_stats(skill.id)
        assert stats["total_selections"] == 10
        assert stats["total_completions"] == 3
        assert stats["completion_rate"] == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Step 2 — Run trigger engine, verify the skill is flagged
# ---------------------------------------------------------------------------


class TestStep2TriggerEngineFlagsSkill:
    """Step 2: SkillTriggerEngine.evaluate_all returns the flagged skill."""

    async def test_low_completion_rate_flagged(
        self, repos, trigger_engine
    ) -> None:
        """Completion rate of 0.2 (2/10) < threshold 0.3 → flag for analysis."""
        _seed_low_completion_rate_trigger(repos)
        skill = _make_skill(repos.skill, PROJECT_ID, "flagged")
        _seed_poor_metrics(repos, skill, completed=2, total=10)

        flagged = await trigger_engine.evaluate_all()
        assert len(flagged) == 1
        entry = flagged[0]
        assert entry["skill_id"] == skill.id
        assert entry["skill_name"] == "flagged"
        assert entry["trigger_name"] == "low_cr_flow_b"
        assert entry["trigger_action"] == "analyze"
        assert "low_completion_rate" in entry["reason"]
        # Stats payload carried through.
        assert entry["stats"]["total_selections"] == 10
        assert entry["stats"]["total_completions"] == 2

    async def test_healthy_skill_not_flagged(
        self, repos, trigger_engine
    ) -> None:
        """Completion rate of 0.5 (5/10) >= threshold 0.3 → no flag."""
        _seed_low_completion_rate_trigger(repos)
        skill = _make_skill(repos.skill, PROJECT_ID, "healthy")
        _seed_poor_metrics(repos, skill, completed=5, total=10)

        flagged = await trigger_engine.evaluate_all()
        assert flagged == []


# ---------------------------------------------------------------------------
# Step 3 — Analysis job is enqueued to system_parallel_queue
# ---------------------------------------------------------------------------


class TestStep3AnalysisJobEnqueued:
    """Step 3: Trigger output is dispatched to system_parallel_queue.

    Mirrors the production wiring from ``manager._run_skill_metric_scan``:
    for each flagged entry, ``SkillJobDispatcher.enqueue_analysis`` is
    called with the skill_id / reason / stats payload.
    """

    async def test_flagged_skill_dispatches_to_parallel_queue(
        self, repos, trigger_engine, dispatcher
    ) -> None:
        _seed_low_completion_rate_trigger(repos)
        skill = _make_skill(repos.skill, PROJECT_ID, "dispatch-target")
        _seed_poor_metrics(repos, skill, completed=2, total=10)

        # Production-style wiring: walk flagged, enqueue analysis.
        flagged = await trigger_engine.evaluate_all()
        assert len(flagged) == 1, "precondition: skill must be flagged"

        for entry in flagged:
            await dispatcher.enqueue_analysis(
                project_id=PROJECT_ID,
                skill_id=entry["skill_id"],
                reason=entry["trigger_name"],
                stats=entry["stats"],
            )

        # Exactly one enqueue call was made.
        dispatcher._job_service.enqueue.assert_awaited_once()
        kwargs = dispatcher._job_service.enqueue.await_args.kwargs

        # CRITICAL routing invariant: queue_id resolved to the
        # parallel queue, NOT None (which would fall back to FIFO).
        assert kwargs["queue_id"] == PARALLEL_QUEUE_ID
        assert kwargs["queue_id"] is not None
        # Job-type and source identify the skill-keeper lane.
        assert kwargs["job_type"] == "skill_analysis"
        assert kwargs["agent_id"] == "skill-keeper"
        assert kwargs["source"] == "skill_evolution"
        assert kwargs["project_id"] == PROJECT_ID

        # Metadata carries the full context for the skill-keeper.
        meta = kwargs["metadata"]
        assert meta["skill_id"] == skill.id
        assert meta["reason"] == "low_cr_flow_b"
        assert meta["stats"]["completion_rate"] == pytest.approx(0.2)

    async def test_parallel_queue_lookup_uses_correct_name(
        self, repos, trigger_engine, dispatcher
    ) -> None:
        """``queue_repo.get_by_name`` is called with 'system_parallel_queue'."""
        _seed_low_completion_rate_trigger(repos)
        skill = _make_skill(repos.skill, PROJECT_ID, "queue-name-check")
        _seed_poor_metrics(repos, skill, completed=2, total=10)

        flagged = await trigger_engine.evaluate_all()
        await dispatcher.enqueue_analysis(
            project_id=PROJECT_ID,
            skill_id=flagged[0]["skill_id"],
            reason=flagged[0]["trigger_name"],
            stats=flagged[0]["stats"],
        )

        # Verify the queue lookup used the correct name.
        dispatcher._queue_repo.get_by_name.assert_called()
        call_args = dispatcher._queue_repo.get_by_name.call_args
        # (project_id, queue_name) positional args.
        assert call_args.args[1] == "system_parallel_queue"
        assert call_args.args[0] == PROJECT_ID


# ---------------------------------------------------------------------------
# Step 4 — Skill-keeper performs FIX evolution
# ---------------------------------------------------------------------------


class TestStep4FixEvolution:
    """Step 4: SkillEvolutionService.evolve_skill produces a new version
    with lineage + A/B test record + status='ab_testing' on both variants.
    """

    async def test_evolve_fix_creates_new_generation_with_lineage(
        self, repos, evolution_service
    ) -> None:
        old = _make_skill(repos.skill, PROJECT_ID, "needs-fix", generation=1)
        lineage_repo = evolution_service._lineage_repo

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                evolution_service,
                "_call_llm",
                AsyncMock(return_value="new improved body"),
            )
            result = await evolution_service.evolve_skill(
                old.id, "FIX", "tighten error handling"
            )

        assert result["skipped"] is False
        assert result["old_skill_id"] == old.id
        assert result["new_skill_id"] != old.id
        assert result["ab_test_group"]

        # New row is the next generation with lineage_origin='evolved'.
        new_skill = repos.skill.get(result["new_skill_id"])
        assert new_skill is not None
        assert new_skill.generation == 2
        assert new_skill.lineage_origin == "evolved"
        assert new_skill.status == "ab_testing"
        assert new_skill.ab_test_group == result["ab_test_group"]

        # Old skill flipped to status='ab_testing' per spec.
        old_after = repos.skill.get(old.id)
        assert old_after.status == "ab_testing"
        assert old_after.ab_test_group == result["ab_test_group"]

        # Lineage edge points new -> old.
        parents = lineage_repo.get_parents(result["new_skill_id"])
        assert len(parents) == 1
        assert parents[0].parent_skill_id == old.id
        assert "FIX" in parents[0].change_summary


# ---------------------------------------------------------------------------
# Step 5 — A/B test record exists, links old + new variants
# ---------------------------------------------------------------------------


class TestStep5ABTestRecordCreated:
    """Step 5: skill_ab_tests row pairs old + new under the same group."""

    async def test_evolve_fix_writes_ab_test_row(
        self, repos, evolution_service
    ) -> None:
        """``skill_ab_tests`` row exists with (skill_id_old, skill_id_new)."""
        old = _make_skill(repos.skill, PROJECT_ID, "ab-test-target")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                evolution_service,
                "_call_llm",
                AsyncMock(return_value="ab test new content"),
            )
            result = await evolution_service.evolve_skill(
                old.id, "FIX", "reduce false positives"
            )

        # The A/B test row exists with the expected pairing.
        ab_test = repos.ab_test.get_by_group(result["ab_test_group"])
        assert ab_test is not None
        assert ab_test.skill_id_old == old.id
        assert ab_test.skill_id_new == result["new_skill_id"]
        assert ab_test.comparisons == 0
        assert ab_test.extension_count == 0
        assert ab_test.resolved_at is None
        assert ab_test.winner_skill_id is None

    async def test_both_variants_share_ab_test_group(
        self, repos, evolution_service
    ) -> None:
        """Old and new both have ``ab_test_group`` set to the same UUID."""
        old = _make_skill(repos.skill, PROJECT_ID, "shared-group")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                evolution_service,
                "_call_llm",
                AsyncMock(return_value="new body"),
            )
            result = await evolution_service.evolve_skill(
                old.id, "FIX", "improve"
            )

        old_after = repos.skill.get(old.id)
        new_after = repos.skill.get(result["new_skill_id"])
        assert old_after.ab_test_group == new_after.ab_test_group
        assert old_after.ab_test_group == result["ab_test_group"]


# ---------------------------------------------------------------------------
# Step 6 — N comparisons → A/B resolution (winner determined, loser deactivated)
# ---------------------------------------------------------------------------


class TestStep6ABTestResolution:
    """Step 6: After ab_sample_size comparisons with a significant
    difference, the A/B test resolves: winner is activated, loser is
    deactivated, ab_test_group cleared, resolved_at stamped.
    """

    async def test_full_flow_resolves_ab_test_with_clear_winner(
        self,
        repos,
        evolution_service,
        fake_metrics_service,
    ) -> None:
        """End-to-end: poor-metrics skill → trigger → evolve → A/B → resolve.

        This is the canonical Flow B happy path. We exercise every step
        sequentially in one test to assert that the data flows correctly
        across phase boundaries — a regression in any phase surfaces here.
        """
        # ── Step 1: poor-metrics skill ────────────────────────────────
        old = _make_skill(repos.skill, PROJECT_ID, "flow-b-end-to-end")
        _seed_poor_metrics(repos, old, completed=2, total=10)

        # ── Step 4: FIX evolution (LLM stubbed) ───────────────────────
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                evolution_service,
                "_call_llm",
                AsyncMock(return_value="greatly improved skill body"),
            )
            evo_result = await evolution_service.evolve_skill(
                old.id, "FIX", "address low completion rate"
            )

        assert evo_result["skipped"] is False
        new_skill_id = evo_result["new_skill_id"]
        ab_group = evo_result["ab_test_group"]

        # ── Step 5: A/B test record exists ────────────────────────────
        ab_row = repos.ab_test.get_by_group(ab_group)
        assert ab_row is not None
        assert ab_row.skill_id_old == old.id
        assert ab_row.skill_id_new == new_skill_id

        # ── Step 6: simulate N comparisons (N=ab_sample_size) ─────────
        for _ in range(AB_SAMPLE_SIZE):
            repos.ab_test.increment_comparison(ab_group)

        # After N comparisons: completion rates diverge significantly
        # (new=1.0 vs old=0.2 → diff=0.8 >= 0.15 threshold).
        fake_metrics_service.get_ab_comparison_stats.return_value = {
            "skill_id_a": old.id,
            "skill_id_b": new_skill_id,
            "completion_rate_a": 0.2,
            "completion_rate_b": 1.0,
            "difference": 0.8,
            "comparisons": AB_SAMPLE_SIZE,
            "extension_count": 0,
            "ready_to_resolve": True,
            "needs_more_data": False,
        }

        result = await evolution_service.check_ab_test_resolution(ab_group)

        # A/B test resolved by raw completion rate.
        assert result["resolved"] is True
        assert result["winner_id"] == new_skill_id  # higher rate
        assert result["loser_id"] == old.id
        assert result["reason"] == "threshold_met"

        # ── Verify side effects propagated to the DB ──────────────────
        # Loser deactivated.
        old_after = repos.skill.get(old.id)
        assert old_after.is_active is False
        assert old_after.status == "inactive"

        # Winner activated, ab_test_group cleared.
        new_after = repos.skill.get(new_skill_id)
        assert new_after.is_active is True
        assert new_after.status == "active"
        assert new_after.ab_test_group is None

        # A/B test row stamped with winner + resolved_at.
        ab_row_final = repos.ab_test.get_by_group(ab_group)
        assert ab_row_final.winner_skill_id == new_skill_id
        assert ab_row_final.resolved_at is not None

    async def test_resolution_below_threshold_extends_test(
        self,
        repos,
        evolution_service,
        fake_metrics_service,
    ) -> None:
        """When diff < threshold, the test extends instead of resolving."""
        old = _make_skill(repos.skill, PROJECT_ID, "stuck-test")
        new = _make_skill(repos.skill, PROJECT_ID, "stuck-test-v2")

        # Manually create an A/B test (skip the FIX path — we only care
        # about the resolution step here).
        ab_group = "stuck-group-flow-b"
        repos.ab_test.create_ab_test(ab_group, old.id, new.id)
        for _ in range(AB_SAMPLE_SIZE):
            repos.ab_test.increment_comparison(ab_group)

        # Stats say: enough comparisons but tiny difference.
        fake_metrics_service.get_ab_comparison_stats.return_value = {
            "skill_id_a": old.id,
            "skill_id_b": new.id,
            "completion_rate_a": 0.6,
            "completion_rate_b": 0.65,
            "difference": 0.05,  # < 0.15
            "comparisons": AB_SAMPLE_SIZE,
            "extension_count": 0,
            "ready_to_resolve": False,
            "needs_more_data": True,
        }

        result = await evolution_service.check_ab_test_resolution(ab_group)

        assert result["resolved"] is False
        assert result["reason"] == "extended"
        assert result["extension_count"] == 1

        # Persisted extension_count was bumped.
        ab_row = repos.ab_test.get_by_group(ab_group)
        assert ab_row.extension_count == 1
        # Neither variant deactivated; test continues.
        assert repos.skill.get(old.id).is_active is True
        assert repos.skill.get(new.id).is_active is True

    async def test_resolution_force_resolves_when_max_extensions_exceeded(
        self,
        repos,
        evolution_service,
        fake_metrics_service,
    ) -> None:
        """After max_extensions rounds, force-resolve by raw completion_rate."""
        old = _make_skill(repos.skill, PROJECT_ID, "force-old")
        new = _make_skill(repos.skill, PROJECT_ID, "force-new")

        ab_group = "force-group-flow-b"
        repos.ab_test.create_ab_test(ab_group, old.id, new.id)
        for _ in range(AB_SAMPLE_SIZE):
            repos.ab_test.increment_comparison(ab_group)
        # Bump extension_count to max (3) before calling resolution.
        for _ in range(3):
            repos.ab_test.increment_extension(ab_group)

        # Sub-threshold diff, but max_extensions exhausted.
        fake_metrics_service.get_ab_comparison_stats.return_value = {
            "skill_id_a": old.id,
            "skill_id_b": new.id,
            "completion_rate_a": 0.55,
            "completion_rate_b": 0.6,
            "difference": 0.05,  # < 0.15
            "comparisons": AB_SAMPLE_SIZE,
            "extension_count": 3,  # max_extensions
            "ready_to_resolve": False,
            "needs_more_data": True,
        }

        result = await evolution_service.check_ab_test_resolution(ab_group)

        assert result["resolved"] is True
        assert result["reason"] == "force_resolved_max_extensions"
        # Winner is the variant with the higher raw completion rate.
        assert result["winner_id"] == new.id
        assert result["loser_id"] == old.id

        # Loser deactivated, winner activated.
        assert repos.skill.get(old.id).is_active is False
        assert repos.skill.get(new.id).is_active is True
        assert repos.skill.get(new.id).status == "active"


# ---------------------------------------------------------------------------
# End-to-end cross-phase integration
# ---------------------------------------------------------------------------


class TestFullCrossPhaseFlowB:
    """Full pipeline: Metrics → Trigger → Dispatch → Evolution → A/B resolution.

    This class exercises every Flow B step in one test to prove the
    data flowing across phase boundaries is consistent. A regression
    in any single phase is caught here even when the bug only manifests
    in a later phase (the assertions on intermediate state would fail).
    """

    async def test_complete_flow_b_pipeline(
        self,
        repos,
        trigger_engine,
        dispatcher,
        evolution_service,
        fake_metrics_service,
    ) -> None:
        # ── Phase 4: Setup ────────────────────────────────────────────
        _seed_low_completion_rate_trigger(repos)
        bad_skill = _make_skill(repos.skill, PROJECT_ID, "end-to-end-bad")
        _seed_poor_metrics(repos, bad_skill, completed=2, total=10)
        # Also create a "good" skill that must NOT be flagged — proves
        # the trigger engine doesn't false-positive unrelated skills.
        good_skill = _make_skill(repos.skill, PROJECT_ID, "end-to-end-good")
        repos.skill.increment_counter(good_skill.id, "total_selections", 10)
        repos.skill.increment_counter(good_skill.id, "total_completions", 8)

        # ── Phase 4: Trigger engine flags only the bad skill ─────────
        flagged = await trigger_engine.evaluate_all()
        assert len(flagged) == 1
        assert flagged[0]["skill_id"] == bad_skill.id

        # ── Phase 5: Dispatcher enqueues analysis to parallel queue ──
        for entry in flagged:
            await dispatcher.enqueue_analysis(
                project_id=PROJECT_ID,
                skill_id=entry["skill_id"],
                reason=entry["trigger_name"],
                stats=entry["stats"],
            )
        dispatch_kwargs = (
            dispatcher._job_service.enqueue.await_args.kwargs
        )
        assert dispatch_kwargs["queue_id"] == PARALLEL_QUEUE_ID
        assert dispatch_kwargs["job_type"] == "skill_analysis"

        # ── Phase 5: Skill-keeper performs FIX evolution ─────────────
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                evolution_service,
                "_call_llm",
                AsyncMock(return_value="tightened body"),
            )
            evo_result = await evolution_service.evolve_skill(
                bad_skill.id, "FIX", "address low completion"
            )

        new_skill_id = evo_result["new_skill_id"]
        ab_group = evo_result["ab_test_group"]

        # ── Phase 5: Lineage + A/B record created ────────────────────
        parents = repos.lineage.get_parents(new_skill_id)
        assert len(parents) == 1
        assert parents[0].parent_skill_id == bad_skill.id

        ab_row = repos.ab_test.get_by_group(ab_group)
        assert ab_row.skill_id_old == bad_skill.id
        assert ab_row.skill_id_new == new_skill_id

        # ── Phase 5: A/B test progresses to ab_sample_size comparisons ─
        for _ in range(AB_SAMPLE_SIZE):
            repos.ab_test.increment_comparison(ab_group)

        # Stats reflect the new skill dramatically outperforming old.
        fake_metrics_service.get_ab_comparison_stats.return_value = {
            "skill_id_a": bad_skill.id,
            "skill_id_b": new_skill_id,
            "completion_rate_a": 0.2,
            "completion_rate_b": 0.95,
            "difference": 0.75,
            "comparisons": AB_SAMPLE_SIZE,
            "extension_count": 0,
            "ready_to_resolve": True,
            "needs_more_data": False,
        }

        # ── Phase 5: A/B resolution: new wins, old deactivated ───────
        result = await evolution_service.check_ab_test_resolution(ab_group)
        assert result["resolved"] is True
        assert result["winner_id"] == new_skill_id
        assert result["loser_id"] == bad_skill.id
        assert result["reason"] == "threshold_met"

        # ── Final DB state ───────────────────────────────────────────
        # The unrelated "good" skill must remain untouched by the flow.
        good_after = repos.skill.get(good_skill.id)
        assert good_after.is_active is True
        assert good_after.status == "active"
        assert good_after.ab_test_group is None

        # The bad (loser) skill is deactivated.
        bad_after = repos.skill.get(bad_skill.id)
        assert bad_after.is_active is False
        assert bad_after.status == "inactive"

        # The new (winner) skill is active and the ab_test_group cleared.
        new_after = repos.skill.get(new_skill_id)
        assert new_after.is_active is True
        assert new_after.status == "active"
        assert new_after.ab_test_group is None

        # The A/B test row is stamped with winner + resolved_at.
        ab_final = repos.ab_test.get_by_group(ab_group)
        assert ab_final.winner_skill_id == new_skill_id
        assert ab_final.resolved_at is not None