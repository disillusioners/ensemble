"""Tests for ``SkillMetricsService`` (Phase 4 of Skill Evolution).

Covers the full Phase 4 surface:

* :meth:`SkillMetricsService.record_task_completion` — reads
  injected-skill IDs from instance metadata, creates one
  :class:`SkillUsageRecord` per skill, bumps denormalized
  counters, resets ``consecutive_failures`` on success /
  increments on failure, and clears the metadata key.
* :meth:`SkillMetricsService.record_feedback` — stamps
  ``feedback_applied`` / ``feedback_note`` onto the latest
  usage record and increments ``total_applied`` when applied
  is True.
* :meth:`SkillMetricsService.get_skill_stats` — derived rate
  metrics from the denormalized counter columns.
* :meth:`SkillMetricsService.get_ab_comparison_stats` — reads
  persistent state from ``skill_ab_tests`` and computes
  completion rates from ``skill_usage_records``.

The metrics service relies on Phase 1 repositories; the tests
reuse the ``tests/repositories/conftest.py`` engine fixture
plus a lightweight fake instance repository so the suite stays
fast and isolated.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest


# =============================================================================
# Fake instance repository
# =============================================================================


class FakeInstance:
    """Minimal stand-in for the Instance row."""

    def __init__(
        self,
        instance_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self.id = instance_id
        self.instance_id = instance_id
        self.instance_metadata = dict(metadata or {})


class FakeInstanceRepo:
    """In-memory replacement for :class:`SQLModelInstanceRepository`."""

    def __init__(
        self,
        instances: Optional[dict[str, FakeInstance]] = None,
    ) -> None:
        self._instances: dict[str, FakeInstance] = dict(
            instances or {}
        )
        self.delete_metadata_calls: list[tuple[str, str]] = []
        self.set_metadata_calls: list[tuple[str, str, Any]] = []

    def get(self, instance_id: str) -> Optional[FakeInstance]:
        return self._instances.get(instance_id)

    def delete_metadata(self, instance_id: str, key: str) -> Any:
        self.delete_metadata_calls.append((instance_id, key))
        inst = self._instances.get(instance_id)
        if inst is not None and key in inst.instance_metadata:
            del inst.instance_metadata[key]
        return inst

    def set_metadata(
        self, instance_id: str, key: str, value: Any
    ) -> Any:
        self.set_metadata_calls.append((instance_id, key, value))
        inst = self._instances.get(instance_id)
        if inst is None:
            inst = FakeInstance(instance_id, {key: value})
            self._instances[instance_id] = inst
        else:
            inst.instance_metadata[key] = value
        return inst


# =============================================================================
# Helpers / fixtures
# =============================================================================


def _make_skill(skill_repo, project_id, name, **kwargs):
    """Create a skill with sensible defaults."""
    defaults = {
        "name": name,
        "description": f"desc for {name}",
        "content": f"content for {name}",
        "project_id": project_id,
    }
    defaults.update(kwargs)
    return skill_repo.create(**defaults)


class FakeConfig:
    """Minimal ``SkillEvolutionConfig`` stub."""

    def __init__(
        self,
        *,
        ab_sample_size: int = 10,
        ab_min_difference: float = 0.15,
        max_extensions: int = 3,
    ) -> None:
        self.ab_sample_size = ab_sample_size
        self.ab_min_difference = ab_min_difference
        self.max_extensions = max_extensions


@pytest.fixture
def metrics_service(engine, project_id):
    """A :class:`SkillMetricsService` wired against the test repos."""
    from daemon.repositories.skill.repository import (
        SkillABTestRepository,
        SkillRepository,
        SkillTriggerRepository,
        SkillUsageRepository,
    )
    from daemon.services.skill_metrics_service import (
        INJECTED_SKILLS_METADATA_KEY,
        SkillMetricsService,
    )

    skill_repo = SkillRepository(engine)
    usage_repo = SkillUsageRepository(engine)
    trigger_repo = SkillTriggerRepository(engine)
    ab_test_repo = SkillABTestRepository(engine)
    instance_repo = FakeInstanceRepo()
    config = FakeConfig()

    service = SkillMetricsService(
        usage_repo=usage_repo,
        skill_repo=skill_repo,
        trigger_repo=trigger_repo,
        ab_test_repo=ab_test_repo,
        config=config,
        instance_repo=instance_repo,
    )
    service.instance_repo = instance_repo  # type: ignore[assignment]
    service.skill_repo = skill_repo
    service.usage_repo = usage_repo
    service.trigger_repo = trigger_repo
    service.ab_test_repo = ab_test_repo
    service.config = config
    service.INJECTED_SKILLS_METADATA_KEY = INJECTED_SKILLS_METADATA_KEY  # type: ignore[attr-defined]
    return service


# =============================================================================
# record_task_completion
# =============================================================================


class TestRecordTaskCompletion:
    """Tests for :meth:`SkillMetricsService.record_task_completion`."""

    async def test_no_injected_skills_no_records(
        self, metrics_service
    ):
        """Empty metadata -> no-op."""
        inst_id = "inst-empty"
        metrics_service.instance_repo._instances[inst_id] = (
            FakeInstance(inst_id, metadata={})
        )

        inserted = await metrics_service.record_task_completion(
            instance_id=inst_id,
            agent_id="agent-x",
            project_id="proj-1",
            task_succeeded=True,
            iterations=2,
            duration_seconds=10,
        )

        assert inserted == 0

    async def test_missing_instance_no_records(self, metrics_service):
        """Instance not in repo -> no-op (returns 0)."""
        inserted = await metrics_service.record_task_completion(
            instance_id="inst-missing",
            agent_id="agent-x",
            project_id="proj-1",
            task_succeeded=True,
            iterations=1,
            duration_seconds=1,
        )
        assert inserted == 0

    async def test_no_instance_repo_no_records(
        self, metrics_service, skill_repo, project_id
    ):
        """``instance_repo=None`` -> gracefully no-op."""
        _make_skill(skill_repo, project_id, "alpha")
        metrics_service.instance_repo = None

        inserted = await metrics_service.record_task_completion(
            instance_id="whatever",
            agent_id="agent-x",
            project_id=project_id,
            task_succeeded=True,
            iterations=0,
            duration_seconds=0,
        )
        assert inserted == 0

    async def test_records_one_row_per_injected_skill(
        self, metrics_service, skill_repo, project_id
    ):
        """One SkillUsageRecord per injected skill ID."""
        s1 = _make_skill(skill_repo, project_id, "s1")
        s2 = _make_skill(skill_repo, project_id, "s2")
        inst_id = "inst-1"
        metrics_service.instance_repo._instances[inst_id] = (
            FakeInstance(
                inst_id,
                metadata={"last_injected_skill_ids": [s1.id, s2.id]},
            )
        )

        inserted = await metrics_service.record_task_completion(
            instance_id=inst_id,
            agent_id="agent-x",
            project_id=project_id,
            task_succeeded=True,
            iterations=3,
            duration_seconds=42,
        )

        assert inserted == 2
        items1, total1 = metrics_service.usage_repo.get_by_skill(s1.id)
        items2, total2 = metrics_service.usage_repo.get_by_skill(s2.id)
        assert total1 == 1
        assert total2 == 1
        rec = items1[0]
        assert rec.selected is True
        assert rec.task_succeeded is True
        assert rec.iterations == 3
        assert rec.duration_seconds == 42
        assert rec.applied is False

    async def test_successful_task_bumps_completions_resets_failures(
        self, metrics_service, skill_repo, project_id
    ):
        """Success bumps completions, resets consecutive_failures."""
        skill = _make_skill(skill_repo, project_id, "alpha")
        skill_repo.increment_counter(
            skill.id, "consecutive_failures", amount=4
        )
        inst_id = "inst-success"
        metrics_service.instance_repo._instances[inst_id] = (
            FakeInstance(
                inst_id,
                metadata={"last_injected_skill_ids": [skill.id]},
            )
        )

        await metrics_service.record_task_completion(
            instance_id=inst_id,
            agent_id="agent-x",
            project_id=project_id,
            task_succeeded=True,
            iterations=1,
            duration_seconds=1,
        )

        fetched = skill_repo.get(skill.id)
        assert fetched.total_selections == 1
        assert fetched.total_completions == 1
        assert fetched.consecutive_failures == 0
        assert fetched.last_used_at is not None

    async def test_failed_task_increments_failures_and_fallback(
        self, metrics_service, skill_repo, project_id
    ):
        """Failure with pre-existing streak: fallback=True, streak grows."""
        skill = _make_skill(skill_repo, project_id, "beta")
        skill_repo.increment_counter(
            skill.id, "consecutive_failures", amount=2
        )
        inst_id = "inst-fail"
        metrics_service.instance_repo._instances[inst_id] = (
            FakeInstance(
                inst_id,
                metadata={"last_injected_skill_ids": [skill.id]},
            )
        )

        await metrics_service.record_task_completion(
            instance_id=inst_id,
            agent_id="agent-x",
            project_id=project_id,
            task_succeeded=False,
            iterations=1,
            duration_seconds=1,
        )

        fetched = skill_repo.get(skill.id)
        assert fetched.total_selections == 1
        assert fetched.total_completions == 0
        assert fetched.total_fallbacks == 1
        assert fetched.consecutive_failures == 3

    async def test_failed_task_zero_pre_failures_no_fallback(
        self, metrics_service, skill_repo, project_id
    ):
        """Failure with 0 prior failures: fallback is False."""
        skill = _make_skill(skill_repo, project_id, "gamma")
        inst_id = "inst-first-fail"
        metrics_service.instance_repo._instances[inst_id] = (
            FakeInstance(
                inst_id,
                metadata={"last_injected_skill_ids": [skill.id]},
            )
        )

        await metrics_service.record_task_completion(
            instance_id=inst_id,
            agent_id="agent-x",
            project_id=project_id,
            task_succeeded=False,
            iterations=1,
            duration_seconds=1,
        )

        fetched = skill_repo.get(skill.id)
        assert fetched.total_fallbacks == 0
        assert fetched.consecutive_failures == 1

    async def test_missing_skill_skipped(
        self, metrics_service, project_id
    ):
        """A deleted skill referenced in metadata is silently skipped."""
        inst_id = "inst-missing-skill"
        metrics_service.instance_repo._instances[inst_id] = (
            FakeInstance(
                inst_id,
                metadata={"last_injected_skill_ids": ["no-such-skill"]},
            )
        )

        inserted = await metrics_service.record_task_completion(
            instance_id=inst_id,
            agent_id="agent-x",
            project_id=project_id,
            task_succeeded=True,
            iterations=0,
            duration_seconds=0,
        )
        assert inserted == 0

    async def test_clears_injected_skills_metadata(
        self, metrics_service, skill_repo, project_id
    ):
        """``last_injected_skill_ids`` is cleared after recording."""
        skill = _make_skill(skill_repo, project_id, "delta")
        inst_id = "inst-clear"
        fake_inst = FakeInstance(
            inst_id,
            metadata={
                "last_injected_skill_ids": [skill.id],
                "other_key": "preserved",
            },
        )
        metrics_service.instance_repo._instances[inst_id] = fake_inst

        await metrics_service.record_task_completion(
            instance_id=inst_id,
            agent_id="agent-x",
            project_id=project_id,
            task_succeeded=True,
            iterations=1,
            duration_seconds=1,
        )

        assert "last_injected_skill_ids" not in (
            fake_inst.instance_metadata
        )
        assert fake_inst.instance_metadata.get("other_key") == "preserved"
        cleared_keys = [
            key
            for (_id, key) in (
                metrics_service.instance_repo.delete_metadata_calls
            )
        ]
        assert "last_injected_skill_ids" in cleared_keys

    async def test_per_skill_isolation(
        self, metrics_service, skill_repo, project_id
    ):
        """A failure on one skill does not block the others."""
        good = _make_skill(skill_repo, project_id, "epsilon")
        inst_id = "inst-iso"
        metrics_service.instance_repo._instances[inst_id] = (
            FakeInstance(
                inst_id,
                metadata={
                    "last_injected_skill_ids": ["bad-skill", good.id]
                },
            )
        )

        inserted = await metrics_service.record_task_completion(
            instance_id=inst_id,
            agent_id="agent-x",
            project_id=project_id,
            task_succeeded=True,
            iterations=1,
            duration_seconds=1,
        )
        assert inserted == 1
        assert skill_repo.get(good.id).total_selections == 1


# =============================================================================
# record_feedback
# =============================================================================


class TestRecordFeedback:
    """Tests for :meth:`SkillMetricsService.record_feedback`."""

    async def test_records_feedback_on_latest(
        self, metrics_service, skill_repo, project_id
    ):
        """Feedback is stamped onto the most recent usage record."""
        skill = _make_skill(skill_repo, project_id, "zeta")
        inst_id = "inst-fb"
        usage_repo = metrics_service.usage_repo
        usage_repo.create(
            skill_id=skill.id, project_id=project_id,
            instance_id=inst_id, agent_id="a",
        )
        latest = usage_repo.create(
            skill_id=skill.id, project_id=project_id,
            instance_id=inst_id, agent_id="a",
        )

        ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            applied=True,
            note="worked great",
        )

        assert ok is True
        rec = usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=inst_id
        )
        assert rec is not None
        assert rec.id == latest.id
        assert rec.feedback_applied is True
        assert rec.feedback_note == "worked great"

    async def test_applied_true_bumps_total_applied(
        self, metrics_service, skill_repo, project_id
    ):
        """``applied=True`` increments the skill's ``total_applied``."""
        skill = _make_skill(skill_repo, project_id, "eta")
        inst_id = "inst-applied"
        metrics_service.usage_repo.create(
            skill_id=skill.id, project_id=project_id,
            instance_id=inst_id, agent_id="a",
        )

        ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            applied=True,
            note="",
        )
        assert ok is True
        assert skill_repo.get(skill.id).total_applied == 1

    async def test_applied_false_no_counter_bump(
        self, metrics_service, skill_repo, project_id
    ):
        """``applied=False`` records feedback but does NOT bump total_applied."""
        skill = _make_skill(skill_repo, project_id, "theta")
        inst_id = "inst-not-applied"
        metrics_service.usage_repo.create(
            skill_id=skill.id, project_id=project_id,
            instance_id=inst_id, agent_id="a",
        )

        ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            applied=False,
            note="not helpful",
        )
        assert ok is True
        assert skill_repo.get(skill.id).total_applied == 0
        rec = metrics_service.usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=inst_id
        )
        assert rec.feedback_applied is False
        assert rec.feedback_note == "not helpful"

    async def test_applied_none_no_counter_bump(
        self, metrics_service, skill_repo, project_id
    ):
        """``applied=None`` is low-confidence: no counter change."""
        skill = _make_skill(skill_repo, project_id, "iota")
        inst_id = "inst-unsure"
        metrics_service.usage_repo.create(
            skill_id=skill.id, project_id=project_id,
            instance_id=inst_id, agent_id="a",
        )

        ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            applied=None,
            note="not sure",
        )
        assert ok is True
        assert skill_repo.get(skill.id).total_applied == 0

    async def test_no_record_returns_false(
        self, metrics_service, skill_repo, project_id
    ):
        """No matching record -> ``False``, no error."""
        skill = _make_skill(skill_repo, project_id, "kappa")
        ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id="inst-NEVER",
            agent_id="a",
            project_id=project_id,
            applied=True,
            note="",
        )
        assert ok is False


# =============================================================================
# get_skill_stats
# =============================================================================


class TestGetSkillStats:
    """Tests for :meth:`SkillMetricsService.get_skill_stats`."""

    async def test_returns_zero_for_missing_skill(self, metrics_service):
        stats = await metrics_service.get_skill_stats("no-such")
        assert stats == {
            "total_selections": 0,
            "total_applied": 0,
            "total_completions": 0,
            "total_fallbacks": 0,
            "completion_rate": 0.0,
            "fallback_rate": 0.0,
            "applied_rate": 0.0,
            "consecutive_failures": 0,
        }

    async def test_returns_zero_rates_for_zero_selections(
        self, metrics_service, skill_repo, project_id
    ):
        """No selections -> all rates are 0.0 (no div-by-zero)."""
        skill = _make_skill(skill_repo, project_id, "lambda")
        stats = await metrics_service.get_skill_stats(skill.id)
        assert stats["total_selections"] == 0
        assert stats["completion_rate"] == 0.0
        assert stats["fallback_rate"] == 0.0
        assert stats["applied_rate"] == 0.0

    async def test_completion_rate_computed(
        self, metrics_service, skill_repo, project_id
    ):
        """Completion rate is completions / selections."""
        skill = _make_skill(skill_repo, project_id, "mu")
        skill_repo.increment_counter(
            skill.id, "total_selections", amount=10
        )
        skill_repo.increment_counter(
            skill.id, "total_completions", amount=4
        )

        stats = await metrics_service.get_skill_stats(skill.id)
        assert stats["completion_rate"] == pytest.approx(0.4)
        assert stats["total_selections"] == 10
        assert stats["total_completions"] == 4

    async def test_fallback_and_applied_rates(
        self, metrics_service, skill_repo, project_id
    ):
        """fallback_rate and applied_rate use selections as denominator."""
        skill = _make_skill(skill_repo, project_id, "nu")
        skill_repo.increment_counter(
            skill.id, "total_selections", amount=10
        )
        skill_repo.increment_counter(
            skill.id, "total_fallbacks", amount=5
        )
        skill_repo.increment_counter(
            skill.id, "total_applied", amount=2
        )

        stats = await metrics_service.get_skill_stats(skill.id)
        assert stats["fallback_rate"] == pytest.approx(0.5)
        assert stats["applied_rate"] == pytest.approx(0.2)

    async def test_consecutive_failures_included(
        self, metrics_service, skill_repo, project_id
    ):
        skill = _make_skill(skill_repo, project_id, "xi")
        skill_repo.increment_counter(
            skill.id, "consecutive_failures", amount=7
        )
        stats = await metrics_service.get_skill_stats(skill.id)
        assert stats["consecutive_failures"] == 7


# =============================================================================
# get_ab_comparison_stats
# =============================================================================


class TestGetABComparisonStats:
    """Tests for :meth:`SkillMetricsService.get_ab_comparison_stats`."""

    async def test_returns_zeros_for_missing_group(self, metrics_service):
        result = await metrics_service.get_ab_comparison_stats(
            "no-such-group"
        )
        assert result["skill_id_a"] is None
        assert result["skill_id_b"] is None
        assert result["comparisons"] == 0
        assert result["ready_to_resolve"] is False
        assert result["needs_more_data"] is False

    async def test_completion_rates_from_usage_records(
        self, metrics_service, skill_repo, ab_test_repo, project_id
    ):
        """Completion rates come from ``skill_usage_records``."""
        usage_repo = metrics_service.usage_repo
        skill_old = _make_skill(skill_repo, project_id, "old")
        skill_new = _make_skill(skill_repo, project_id, "new")
        for _ in range(4):
            usage_repo.create(
                skill_id=skill_old.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=True,
            )
        usage_repo.create(
            skill_id=skill_old.id, project_id=project_id,
            instance_id="i2", agent_id="a",
            task_succeeded=False,
        )
        for _ in range(2):
            usage_repo.create(
                skill_id=skill_new.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=True,
            )
        usage_repo.create(
            skill_id=skill_new.id, project_id=project_id,
            instance_id="i2", agent_id="a",
            task_succeeded=False,
        )

        group = "g1"
        ab_test_repo.create_ab_test(
            ab_test_group=group,
            skill_id_old=skill_old.id,
            skill_id_new=skill_new.id,
        )
        ab_test_repo.increment_comparison(group)
        ab_test_repo.increment_comparison(group)

        result = await metrics_service.get_ab_comparison_stats(group)
        assert result["skill_id_a"] == skill_old.id
        assert result["skill_id_b"] == skill_new.id
        assert result["completion_rate_a"] == pytest.approx(4 / 5)
        assert result["completion_rate_b"] == pytest.approx(2 / 3)
        assert result["comparisons"] == 2

    async def test_ready_to_resolve_when_significant(
        self, metrics_service, skill_repo, usage_repo,
        ab_test_repo, project_id
    ):
        """ready_to_resolve iff comparisons>=sample AND diff>=min."""
        s_old = _make_skill(skill_repo, project_id, "old")
        s_new = _make_skill(skill_repo, project_id, "new")
        for _ in range(2):
            usage_repo.create(
                skill_id=s_old.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=False,
            )
        for _ in range(2):
            usage_repo.create(
                skill_id=s_new.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=True,
            )

        group = "g-resolve"
        ab_test_repo.create_ab_test(
            ab_test_group=group,
            skill_id_old=s_old.id,
            skill_id_new=s_new.id,
        )
        for _ in range(metrics_service.config.ab_sample_size):
            ab_test_repo.increment_comparison(group)

        result = await metrics_service.get_ab_comparison_stats(group)
        assert result["ready_to_resolve"] is True
        assert result["needs_more_data"] is False
        assert result["difference"] == pytest.approx(1.0)

    async def test_needs_more_data_below_threshold(
        self, metrics_service, skill_repo, usage_repo,
        ab_test_repo, project_id
    ):
        """needs_more_data iff comparisons>=sample but diff<min."""
        s_old = _make_skill(skill_repo, project_id, "old")
        s_new = _make_skill(skill_repo, project_id, "new")
        for success in (True, False):
            usage_repo.create(
                skill_id=s_old.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=success,
            )
            usage_repo.create(
                skill_id=s_new.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=success,
            )

        group = "g-stuck"
        ab_test_repo.create_ab_test(
            ab_test_group=group,
            skill_id_old=s_old.id,
            skill_id_new=s_new.id,
        )
        for _ in range(metrics_service.config.ab_sample_size):
            ab_test_repo.increment_comparison(group)

        result = await metrics_service.get_ab_comparison_stats(group)
        assert result["ready_to_resolve"] is False
        assert result["needs_more_data"] is True
        assert result["difference"] == pytest.approx(0.0)

    async def test_below_sample_size_not_ready(
        self, metrics_service, skill_repo, usage_repo,
        ab_test_repo, project_id
    ):
        """comparisons < sample_size -> not ready, not needs_more."""
        s_old = _make_skill(skill_repo, project_id, "old")
        s_new = _make_skill(skill_repo, project_id, "new")
        for _ in range(3):
            usage_repo.create(
                skill_id=s_old.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=False,
            )
        for _ in range(3):
            usage_repo.create(
                skill_id=s_new.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=True,
            )

        group = "g-too-few"
        ab_test_repo.create_ab_test(
            ab_test_group=group,
            skill_id_old=s_old.id,
            skill_id_new=s_new.id,
        )
        ab_test_repo.increment_comparison(group)
        ab_test_repo.increment_comparison(group)

        result = await metrics_service.get_ab_comparison_stats(group)
        assert result["ready_to_resolve"] is False
        assert result["needs_more_data"] is False
        assert result["comparisons"] == 2

    async def test_extension_count_read(
        self, metrics_service, skill_repo, ab_test_repo, project_id
    ):
        """``extension_count`` is read from the test row, not hardcoded."""
        s_old = _make_skill(skill_repo, project_id, "old")
        s_new = _make_skill(skill_repo, project_id, "new")
        group = "g-ext"
        ab_test_repo.create_ab_test(
            ab_test_group=group,
            skill_id_old=s_old.id,
            skill_id_new=s_new.id,
        )
        ab_test_repo.increment_extension(group)
        ab_test_repo.increment_extension(group)

        result = await metrics_service.get_ab_comparison_stats(group)
        assert result["extension_count"] == 2
