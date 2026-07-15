"""Phase 3 composite scoring unit tests.

Comprehensive tests for the multi-metric composite scoring system
introduced in Milestone 2 Phase 3 of the skill-evolution pipeline.

Coverage:
    1. ``SkillMetricsService._composite_score`` — verifies the weighted
       5-metric blend (completion / applied / efficiency / fallback /
       speed) produces the correct score for a representative set of
       stats / baseline inputs. Includes edge cases:
       * ``total == 0`` early-return (returns ``0.0``, not neutral).
       * Missing baselines → efficiency/speed default to neutral 0.5.
       * Per-component cap at ``1.0`` when the variant beats the
         baseline by a large margin.
       * Final clamp at ``1.0`` to defend against misconfigured weights.
    2. ``SkillEvolutionService.check_ab_test_resolution`` — the
       threshold-met path's tie-breaking rule (challenger B wins ties).
       Verified end-to-end via the async public method with all repos
       mocked; ``_pick_winner`` is a nested closure so we drive it via
       the public method instead of importing it directly.
    3. ``SkillUsageRepository.get_stats_filtered`` — verifies the
       ``ab_test_group`` filter (only the named group's records are
       counted) and the always-on superseded exclusion. Uses real
       in-memory SQLite via SQLModel + StaticPool.
    4. ``SkillEvolutionConfig`` defaults — confirms
       ``ab_sample_size == 20`` and the 5 composite weights sum to
       exactly ``1.0`` (the production configuration sanity check).

The file is fully self-contained: fixtures (``engine``, ``usage_repo``,
``skill_repo``, ``metrics_service``) and helpers (``FakeConfig``,
``_make_skill``, ``_insert_record``, ``_build_evolution_service``)
are defined here because ``tests/unit/conftest.py`` only carries MCP
fixtures.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ── Local in-memory engine fixture (tests/unit/conftest.py only has MCP fixtures) ──


@pytest.fixture
def engine():
    """Real in-memory SQLite engine wired to SQLModel metadata.

    Uses ``StaticPool`` so the same in-memory database is shared
    across threads (the metrics service and the test thread both
    open sessions against the same connection). ``PRAGMA
    foreign_keys=ON`` is required because ``skill_usage_records``
    has a FK to ``skills.id`` with ``ON DELETE CASCADE``.
    """
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import event
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        c = dbapi_conn.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    # Importing the model modules registers the tables on
    # SQLModel.metadata — must happen BEFORE create_all.
    from daemon.repositories.skill.models import (
        Skill, SkillABTest, SkillEmbedding, SkillLineage,
        SkillTrigger, SkillUsageRecord,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def usage_repo(engine):
    """Real ``SkillUsageRepository`` bound to the test engine."""
    from daemon.repositories.skill.repository import SkillUsageRepository
    return SkillUsageRepository(engine)


@pytest.fixture
def skill_repo(engine):
    """Real ``SkillRepository`` bound to the test engine."""
    from daemon.repositories.skill.repository import SkillRepository
    return SkillRepository(engine)


class FakeConfig:
    """Minimal :class:`SkillEvolutionConfig` stub.

    Carries exactly the fields read by ``_composite_score`` (the 5
    weight attributes) plus the ``ab_sample_size`` /
    ``ab_min_difference`` / ``max_extensions`` attributes read by
    ``check_ab_test_resolution``. All defaults match the production
    config so the math lines up with the docstring examples.
    """

    def __init__(
        self,
        *,
        ab_sample_size: int = 20,
        ab_min_difference: float = 0.15,
        max_extensions: int = 3,
        ab_weight_completion: float = 0.35,
        ab_weight_applied: float = 0.20,
        ab_weight_efficiency: float = 0.20,
        ab_weight_fallback: float = 0.15,
        ab_weight_speed: float = 0.10,
    ) -> None:
        self.ab_sample_size = ab_sample_size
        self.ab_min_difference = ab_min_difference
        self.max_extensions = max_extensions
        self.ab_weight_completion = ab_weight_completion
        self.ab_weight_applied = ab_weight_applied
        self.ab_weight_efficiency = ab_weight_efficiency
        self.ab_weight_fallback = ab_weight_fallback
        self.ab_weight_speed = ab_weight_speed


@pytest.fixture
def metrics_service(engine, usage_repo, skill_repo):
    """Build a real :class:`SkillMetricsService` with default config.

    ``SkillMetricsService.__init__`` requires concrete repos for
    ``usage_repo`` / ``skill_repo`` / ``trigger_repo`` /
    ``ab_test_repo``. We use the in-memory SQLite-backed
    implementations so any downstream aggregation we exercise will
    hit real SQL — important because
    ``SkillUsageRepository.get_stats_filtered`` is the canonical
    producer of the ``stats`` dict consumed by ``_composite_score``.
    """
    from daemon.services.skill_metrics_service import SkillMetricsService
    from daemon.repositories.skill.repository import (
        SkillABTestRepository, SkillTriggerRepository,
    )
    cfg = FakeConfig()
    svc = SkillMetricsService(
        usage_repo=usage_repo,
        skill_repo=skill_repo,
        trigger_repo=SkillTriggerRepository(engine),
        ab_test_repo=SkillABTestRepository(engine),
        config=cfg,
    )
    return svc


def _make_skill(skill_repo, name, project_id="p1"):
    """Insert a real :class:`Skill` row.

    Returns the ``Skill`` instance (with ``.id`` populated).
    """
    return skill_repo.create(
        name=name,
        description=f"desc for {name}",
        content=f"content for {name}",
        project_id=project_id,
    )


# =====================================================================
# TestCompositeScore — pure-function math against the helper directly
# =====================================================================


class TestCompositeScore:
    """Pure-function tests for ``SkillMetricsService._composite_score``.

    The helper is sync (no DB I/O of its own) so we drive it with
    hand-crafted ``stats`` / ``global_baselines`` dicts and check
    the closed-form expected values.
    """

    def test_basic_computation(self, metrics_service):
        """Standard input → hand-computed weighted sum.

        With:
            completion_rate = 0.8 → 0.8 × 0.35 = 0.280
            applied_rate    = 0.5 → 0.5 × 0.20 = 0.100
            efficiency      = min(1, 5/3) = 1.0 → 1.0 × 0.20 = 0.200
            low_fallback    = 1 - 0.1   = 0.9 → 0.9 × 0.15 = 0.135
            speed           = min(1, 100/60) = 1.0 → 1.0 × 0.10 = 0.100
        Total = 0.815. Tolerance is ``0.01`` (the implementation uses
        ``min(1.0, ...)`` on each component so floats are exact in
        this case, but a small fudge avoids brittle FP comparisons).
        """
        stats = {
            "completion_rate": 0.8,
            "applied_rate": 0.5,
            "fallback_rate": 0.1,
            "avg_iterations": 3.0,
            "avg_duration": 60.0,
            "total": 100,
        }
        baselines = {"avg_iterations": 5.0, "avg_duration": 100.0}
        score = metrics_service._composite_score(stats, baselines)
        assert abs(score - 0.815) < 0.01

    def test_weights_sum_to_one(self):
        """The 5 default weights must sum to exactly 1.0.

        Guards against an accidental ``Field(default=...)`` change
        in :class:`SkillEvolutionConfig` that would skew the
        composite away from ``[0, 1]`` without the final clamp
        catching it (the clamp only catches > 1.0, not < 1.0).
        """
        from daemon.config import SkillEvolutionConfig
        cfg = SkillEvolutionConfig()
        total = (
            cfg.ab_weight_completion
            + cfg.ab_weight_applied
            + cfg.ab_weight_efficiency
            + cfg.ab_weight_fallback
            + cfg.ab_weight_speed
        )
        assert abs(total - 1.0) < 0.0001

    def test_lower_iterations_higher_efficiency(self, metrics_service):
        """Lower iterations → higher efficiency component → higher score.

        Verifies the ``baseline / actual`` shape of the efficiency
        component: a skill that takes 4 iterations beats a skill
        that takes 10 iterations (when the baseline is 5).
        """
        baselines = {"avg_iterations": 5.0, "avg_duration": 100.0}
        stats_low = {
            "completion_rate": 0.5, "applied_rate": 0.5,
            "fallback_rate": 0.0, "avg_iterations": 4.0,
            "avg_duration": 50.0, "total": 10,
        }
        stats_high = {
            "completion_rate": 0.5, "applied_rate": 0.5,
            "fallback_rate": 0.0, "avg_iterations": 10.0,
            "avg_duration": 50.0, "total": 10,
        }
        score_low = metrics_service._composite_score(stats_low, baselines)
        score_high = metrics_service._composite_score(stats_high, baselines)
        assert score_low > score_high

    def test_lower_duration_higher_speed(self, metrics_service):
        """Lower duration → higher speed component → higher score.

        Symmetric to the efficiency test but on the duration axis.
        """
        baselines = {"avg_iterations": 5.0, "avg_duration": 100.0}
        stats_fast = {
            "completion_rate": 0.5, "applied_rate": 0.5,
            "fallback_rate": 0.0, "avg_iterations": 5.0,
            "avg_duration": 50.0, "total": 10,
        }
        stats_slow = {
            "completion_rate": 0.5, "applied_rate": 0.5,
            "fallback_rate": 0.0, "avg_iterations": 5.0,
            "avg_duration": 200.0, "total": 10,
        }
        score_fast = metrics_service._composite_score(stats_fast, baselines)
        score_slow = metrics_service._composite_score(stats_slow, baselines)
        assert score_fast > score_slow

    def test_zero_history_neutral(self, metrics_service):
        """Two distinct zero-handling paths.

        1. ``total == 0`` → returns ``0.0`` (NOT the neutral
           ``0.5`` blend). The docstring is explicit: we don't
           reward untested variants.
        2. ``total > 0`` but baselines are ``0`` → efficiency &
           speed fall back to neutral ``0.5``; the other three
           components are taken from the supplied stats. This is
           the "fresh database with global averages not yet
           computed" case the early-return is designed NOT to
           handle.
        """
        # (1) total == 0 → explicit 0.0 early return.
        zero_stats = {
            "completion_rate": 0.9, "applied_rate": 0.9,
            "fallback_rate": 0.0, "avg_iterations": 1.0,
            "avg_duration": 1.0, "total": 0,
        }
        assert metrics_service._composite_score(
            zero_stats,
            {"avg_iterations": 5.0, "avg_duration": 100.0},
        ) == 0.0

        # (2) data exists, baselines are zero → neutral 0.5 for
        # efficiency/speed. Hand-computed expected:
        #   0.5 × 0.35 + 0.5 × 0.20 + 0.5 × 0.20 + 1.0 × 0.15 + 0.5 × 0.10
        # = 0.175 + 0.10 + 0.10 + 0.15 + 0.05 = 0.575.
        data_stats = {
            "completion_rate": 0.5, "applied_rate": 0.5,
            "fallback_rate": 0.0, "avg_iterations": 3.0,
            "avg_duration": 60.0, "total": 5,
        }
        zero_baselines = {"avg_iterations": 0.0, "avg_duration": 0.0}
        score = metrics_service._composite_score(data_stats, zero_baselines)
        assert abs(score - 0.575) < 0.001

    def test_total_zero_returns_zero(self, metrics_service):
        """``total == 0`` early-return overrides everything.

        Even if all the rate components in ``stats`` are 1.0 the
        score must be exactly ``0.0`` — the implementation
        short-circuits before reading any field.
        """
        stats = {
            "completion_rate": 0.9, "applied_rate": 0.9,
            "fallback_rate": 0.0, "avg_iterations": 1.0,
            "avg_duration": 1.0, "total": 0,
        }
        baselines = {"avg_iterations": 5.0, "avg_duration": 100.0}
        assert metrics_service._composite_score(stats, baselines) == 0.0

    def test_capped_at_one(self, metrics_service):
        """All components saturate at ``1.0`` → final clamp holds.

        With perfect stats (every component hits 1.0) and very
        generous baselines (10× iterations, 1000× duration), the
        raw weighted sum equals the sum of the weights = 1.0. The
        final ``max(0, min(1, score))`` clamp must keep it at
        exactly ``1.0`` — within FP noise.
        """
        stats = {
            "completion_rate": 1.0, "applied_rate": 1.0,
            "fallback_rate": 0.0, "avg_iterations": 1.0,
            "avg_duration": 1.0, "total": 5,
        }
        baselines = {"avg_iterations": 10.0, "avg_duration": 1000.0}
        score = metrics_service._composite_score(stats, baselines)
        assert abs(score - 1.0) < 1e-9
        assert score <= 1.0


# =====================================================================
# TestTieBreaking — end-to-end via the public async method
# =====================================================================


def _build_evolution_service(stats, *, skill_id_a="skill-a", skill_id_b="skill-b"):
    """Build a :class:`SkillEvolutionService` primed for the threshold-met path.

    The repos are all mocked because ``check_ab_test_resolution``
    reads them via ``asyncio.to_thread`` and we only need to
    control the two return values that gate the decision tree:

    * ``_ab_test_repo.get_by_group`` → fake row with
      ``comparisons=20`` (>= ``ab_sample_size``) so the
      ``needs_more_data`` gate is bypassed.
    * ``_metrics_service.get_ab_comparison_stats`` → the supplied
      ``stats`` dict, with the caller's controlled
      ``composite_score_a`` / ``composite_score_b`` and
      ``difference``.

    The side-effect repos (``_skill_repo.deactivate``,
    ``_ab_test_repo.resolve``, ``_skill_repo.update``) are
    plain ``MagicMock``s — they don't get awaited individually
    because the service wraps each in ``asyncio.to_thread``;
    ``MagicMock`` returns a non-awaitable from ``to_thread`` so
    the gather completes cleanly.
    """
    from daemon.services.skill_evolution_service import SkillEvolutionService

    svc = SkillEvolutionService(
        skill_repo=MagicMock(),
        lineage_repo=MagicMock(),
        usage_repo=MagicMock(),
        embedding_service=MagicMock(),
        metrics_service=MagicMock(),
        ab_test_repo=MagicMock(),
        config=FakeConfig(),
        llm_config={},
    )
    svc._metrics_service.get_ab_comparison_stats = AsyncMock(return_value=stats)

    ab_row = MagicMock()
    ab_row.comparisons = 20  # >= ab_sample_size (20)
    ab_row.extension_count = 0
    svc._ab_test_repo.get_by_group = MagicMock(return_value=ab_row)

    # Mock the side-effect sync calls so they don't crash.
    svc._skill_repo.deactivate = MagicMock()
    svc._ab_test_repo.resolve = MagicMock()
    svc._skill_repo.update = MagicMock()
    return svc, skill_id_a, skill_id_b


class TestTieBreaking:
    """Verify the threshold-met path's challenger-wins-ties rule.

    ``_pick_winner`` is a nested closure inside
    ``check_ab_test_resolution`` (not directly importable), so we
    drive it via the public async method. Three scenarios:

    1. Equal composite scores → challenger (B) wins (tie-breaker).
    2. B strictly better → challenger (B) wins.
    3. A strictly better → incumbent (A) wins.
    """

    @pytest.mark.asyncio
    async def test_challenger_wins_tie(self):
        """Equal composite scores → B wins (tie goes to challenger).

        With ``difference = 0.15`` (== ``ab_min_difference``) and
        ``comparisons = 20``, we land on the ``difference >=
        min_diff`` branch which calls ``_pick_winner``. With
        identical composite scores the ``score_b >= score_a``
        predicate is True so B (new) wins and A (old) loses.
        """
        stats = {
            "skill_id_a": "A", "skill_id_b": "B",
            "composite_score_a": 0.5, "composite_score_b": 0.5,
            "difference": 0.15, "comparisons": 20,
            "extension_count": 0, "ready_to_resolve": True,
        }
        svc, _, _ = _build_evolution_service(stats)
        result = await svc.check_ab_test_resolution("test-group")
        assert result["winner_id"] == "B"
        assert result["loser_id"] == "A"

    @pytest.mark.asyncio
    async def test_challenger_wins_when_better(self):
        """B's composite > A's → B wins.

        Straightforward score_b > score_a → returns (B, A).
        """
        stats = {
            "skill_id_a": "A", "skill_id_b": "B",
            "composite_score_a": 0.3, "composite_score_b": 0.8,
            "difference": 0.5, "comparisons": 20,
            "extension_count": 0, "ready_to_resolve": True,
        }
        svc, _, _ = _build_evolution_service(stats)
        result = await svc.check_ab_test_resolution("test-group")
        assert result["winner_id"] == "B"
        assert result["loser_id"] == "A"

    @pytest.mark.asyncio
    async def test_incumbent_wins_when_better(self):
        """A's composite > B's → A wins.

        Inverse of the previous case: score_a > score_b → the
        ``score_b >= score_a`` predicate is False so the function
        returns (A, B) instead.
        """
        stats = {
            "skill_id_a": "A", "skill_id_b": "B",
            "composite_score_a": 0.8, "composite_score_b": 0.3,
            "difference": 0.5, "comparisons": 20,
            "extension_count": 0, "ready_to_resolve": True,
        }
        svc, _, _ = _build_evolution_service(stats)
        result = await svc.check_ab_test_resolution("test-group")
        assert result["winner_id"] == "A"
        assert result["loser_id"] == "B"


# =====================================================================
# TestAbTestGroupFiltering — real SQLite, real repo aggregation
# =====================================================================


@pytest.fixture
def project_id():
    """Project ID shared across rows in this test class."""
    return "test-project"


def _insert_record(usage_repo, skill_id, project_id, **kwargs):
    """Insert a :class:`SkillUsageRecord` with sensible defaults.

    Defaults pick ``instance_id="inst-1"`` / ``agent_id="agent-x"``
    so tests can stay focused on the fields they care about
    (``ab_test_group``, ``superseded``). Anything in ``kwargs``
    overrides the defaults — including ``superseded=True`` and
    ``ab_test_group="..."``.
    """
    defaults = {
        "skill_id": skill_id,
        "project_id": project_id,
        "instance_id": "inst-1",
        "agent_id": "agent-x",
    }
    defaults.update(kwargs)
    return usage_repo.create(**defaults)


class TestAbTestGroupFiltering:
    """``SkillUsageRepository.get_stats_filtered`` group + superseded semantics.

    Real in-memory SQLite (no mocks) so the SQL aggregation is
    actually exercised. The FK ``ON DELETE CASCADE`` on
    ``skill_usage_records.skill_id`` is why skills must be
    inserted first.
    """

    def test_get_stats_filtered_by_group(self, skill_repo, usage_repo, project_id):
        """A/B group filter scopes to that group's records only.

        Inserts 3 records tagged with ``"group-1"`` and 2 tagged
        with ``"group-2"`` (all for the same skill). With the
        ``ab_test_group="group-1"`` filter, only the 3
        ``group-1`` records are counted.
        """
        skill = _make_skill(skill_repo, "A")
        _insert_record(usage_repo, skill.id, project_id, ab_test_group="group-1")
        _insert_record(usage_repo, skill.id, project_id, ab_test_group="group-1")
        _insert_record(usage_repo, skill.id, project_id, ab_test_group="group-1")
        _insert_record(usage_repo, skill.id, project_id, ab_test_group="group-2")
        _insert_record(usage_repo, skill.id, project_id, ab_test_group="group-2")

        stats = usage_repo.get_stats_filtered(skill.id, ab_test_group="group-1")
        assert stats["total"] == 3

    def test_get_stats_filtered_excludes_superseded(
        self, skill_repo, usage_repo, project_id,
    ):
        """Superseded rows are always excluded, no group filter.

        Inserts 2 superseded + 3 non-superseded records. Without
        a group filter, only the 3 non-superseded records are
        counted — the implementation's ``superseded == False``
        guard runs unconditionally.
        """
        skill = _make_skill(skill_repo, "B")
        _insert_record(
            usage_repo, skill.id, project_id, superseded=True,
        )
        _insert_record(
            usage_repo, skill.id, project_id, superseded=True,
        )
        _insert_record(
            usage_repo, skill.id, project_id, superseded=False,
        )
        _insert_record(
            usage_repo, skill.id, project_id, superseded=False,
        )
        _insert_record(
            usage_repo, skill.id, project_id, superseded=False,
        )

        stats = usage_repo.get_stats_filtered(skill.id)
        assert stats["total"] == 3

    def test_get_stats_filtered_no_group_all_records(
        self, skill_repo, usage_repo, project_id,
    ):
        """No group filter → all non-superseded records counted.

        Mixes records with three different ``ab_test_group``
        values (``"g1"``, ``"g2"``, ``None``). With no filter the
        repo ignores the group column entirely and counts every
        non-superseded row. Total == count of records inserted
        with ``superseded=False``.
        """
        skill = _make_skill(skill_repo, "C")
        # 2 records with group "g1"
        _insert_record(
            usage_repo, skill.id, project_id, ab_test_group="g1",
        )
        _insert_record(
            usage_repo, skill.id, project_id, ab_test_group="g1",
        )
        # 2 records with group "g2"
        _insert_record(
            usage_repo, skill.id, project_id, ab_test_group="g2",
        )
        _insert_record(
            usage_repo, skill.id, project_id, ab_test_group="g2",
        )
        # 2 records with no group (None)
        _insert_record(
            usage_repo, skill.id, project_id, ab_test_group=None,
        )
        _insert_record(
            usage_repo, skill.id, project_id, ab_test_group=None,
        )

        stats = usage_repo.get_stats_filtered(skill.id)
        # No superseded rows → total == number inserted.
        assert stats["total"] == 6


# =====================================================================
# TestSampleSize — config defaults
# =====================================================================


class TestSampleSize:
    """Smoke tests for :class:`SkillEvolutionConfig` defaults.

    The A/B test ``sample_size`` gate is the hard floor on how
    many comparisons are required before any resolution path
    fires — bumping it from 10 to 20 was a deliberate "silent
    upgrade" (D15), so the default value is load-bearing.
    """

    def test_sample_size_default_20(self):
        """``SkillEvolutionConfig().ab_sample_size == 20`` (D15)."""
        from daemon.config import SkillEvolutionConfig
        cfg = SkillEvolutionConfig()
        assert cfg.ab_sample_size == 20
