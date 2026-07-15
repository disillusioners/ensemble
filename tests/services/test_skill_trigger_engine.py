"""Tests for ``SkillTriggerEngine`` and ``skill_trigger_seed`` (Phase 4).

Covers:

* :meth:`SkillTriggerEngine.evaluate_all` — walks every enabled
  trigger and returns the flagged skills.
* The five built-in condition evaluators:
  ``low_completion_rate``, ``high_fallback_rate``,
  ``consecutive_failures``, ``task_count_scan``,
  ``periodic_scan``.
* ``seed_default_triggers`` — idempotent insertion of the
  ``DEFAULT_TRIGGERS`` catalogue.

Tests run against an in-memory SQLite engine (via the
``tests/services/conftest.py`` engine fixture). The metrics
service is wired in directly so the engine exercises the
real DB-backed ``get_skill_stats`` path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest


# =============================================================================
# Helpers
# =============================================================================


def _make_skill(skill_repo, project_id, name, **kwargs):
    defaults = {
        "name": name,
        "description": f"desc for {name}",
        "content": f"content for {name}",
        "project_id": project_id,
    }
    defaults.update(kwargs)
    return skill_repo.create(**defaults)


def _make_trigger(trigger_repo, name, condition_type, condition_json, action):
    return trigger_repo.create(
        name=name,
        condition_type=condition_type,
        condition_json=condition_json,
        action=action,
        project_id=None,
    )


@pytest.fixture
def trigger_engine(engine, project_id):
    """A :class:`SkillTriggerEngine` wired against the test repos."""
    from daemon.repositories.skill.repository import (
        SkillABTestRepository,
        SkillRepository,
        SkillTriggerRepository,
        SkillUsageRepository,
    )
    from daemon.services.skill_metrics_service import SkillMetricsService
    from daemon.services.skill_trigger_engine import SkillTriggerEngine

    skill_repo = SkillRepository(engine)
    usage_repo = SkillUsageRepository(engine)
    trigger_repo = SkillTriggerRepository(engine)
    ab_test_repo = SkillABTestRepository(engine)

    config = type(
        "Cfg",
        (),
        {
            "ab_sample_size": 10,
            "ab_min_difference": 0.15,
            "max_extensions": 3,
        },
    )()

    metrics_service = SkillMetricsService(
        usage_repo=usage_repo,
        skill_repo=skill_repo,
        trigger_repo=trigger_repo,
        ab_test_repo=ab_test_repo,
        config=config,
        instance_repo=None,
    )
    engine_inst = SkillTriggerEngine(
        trigger_repo=trigger_repo,
        metrics_service=metrics_service,
    )
    # Bundle for ergonomics.
    engine_inst.skill_repo = skill_repo
    engine_inst.usage_repo = usage_repo
    engine_inst.trigger_repo = trigger_repo
    engine_inst.ab_test_repo = ab_test_repo
    engine_inst.metrics_service = metrics_service
    return engine_inst


# =============================================================================
# Built-in condition evaluators (sync, no DB)
# =============================================================================


class TestBuiltInConditions:
    """Tests for the per-condition_type evaluators."""

    def test_low_completion_rate_below_threshold(
        self, trigger_engine, project_id
    ):
        skill = _make_skill(trigger_engine.skill_repo, project_id, "low")
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_selections", amount=10
        )
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_completions", amount=2  # 0.2 < 0.3
        )
        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "low_cr",
            "low_completion_rate",
            {"threshold": 0.3, "min_selections": 5},
            "analyze",
        )

        fired = trigger_engine._evaluate_condition(trigger, skill)
        assert fired is True

    def test_low_completion_rate_above_threshold(
        self, trigger_engine, project_id
    ):
        skill = _make_skill(trigger_engine.skill_repo, project_id, "ok")
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_selections", amount=10
        )
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_completions", amount=5  # 0.5 >= 0.3
        )
        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "low_cr",
            "low_completion_rate",
            {"threshold": 0.3, "min_selections": 5},
            "analyze",
        )

        fired = trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    def test_low_completion_rate_min_selections_gate(
        self, trigger_engine, project_id
    ):
        """Below min_selections: condition does NOT fire."""
        skill = _make_skill(trigger_engine.skill_repo, project_id, "new")
        # 1 selection, 0 completions → rate=0.0 (below 0.3) but
        # 1 < min_selections=5 so the gate blocks it.
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_selections", amount=1
        )
        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "low_cr",
            "low_completion_rate",
            {"threshold": 0.3, "min_selections": 5},
            "analyze",
        )

        fired = trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    def test_high_fallback_rate_above_threshold(
        self, trigger_engine, project_id
    ):
        skill = _make_skill(trigger_engine.skill_repo, project_id, "fb")
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_selections", amount=10
        )
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_fallbacks", amount=7  # 0.7 > 0.5
        )
        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "high_fb",
            "high_fallback_rate",
            {"threshold": 0.5, "min_selections": 5},
            "analyze",
        )

        fired = trigger_engine._evaluate_condition(trigger, skill)
        assert fired is True

    def test_high_fallback_rate_below_threshold(
        self, trigger_engine, project_id
    ):
        skill = _make_skill(trigger_engine.skill_repo, project_id, "fb2")
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_selections", amount=10
        )
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_fallbacks", amount=2  # 0.2 < 0.5
        )
        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "high_fb",
            "high_fallback_rate",
            {"threshold": 0.5, "min_selections": 5},
            "analyze",
        )

        fired = trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    def test_consecutive_failures_meets_threshold(
        self, trigger_engine, project_id
    ):
        skill = _make_skill(trigger_engine.skill_repo, project_id, "streak")
        trigger_engine.skill_repo.increment_counter(
            skill.id, "consecutive_failures", amount=3
        )
        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "cf",
            "consecutive_failures",
            {"threshold": 3},
            "evolve_fix",
        )

        fired = trigger_engine._evaluate_condition(trigger, skill)
        assert fired is True

    def test_consecutive_failures_below_threshold(
        self, trigger_engine, project_id
    ):
        skill = _make_skill(trigger_engine.skill_repo, project_id, "streak2")
        trigger_engine.skill_repo.increment_counter(
            skill.id, "consecutive_failures", amount=2
        )
        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "cf",
            "consecutive_failures",
            {"threshold": 3},
            "evolve_fix",
        )

        fired = trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    def test_task_count_scan_meets_threshold(
        self, trigger_engine, project_id
    ):
        skill = _make_skill(trigger_engine.skill_repo, project_id, "hot")
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_selections", amount=20
        )
        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "tcs",
            "task_count_scan",
            {"threshold": 20},
            "analyze",
        )

        fired = trigger_engine._evaluate_condition(trigger, skill)
        assert fired is True

    def test_task_count_scan_below_threshold(
        self, trigger_engine, project_id
    ):
        skill = _make_skill(trigger_engine.skill_repo, project_id, "cold")
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_selections", amount=5
        )
        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "tcs",
            "task_count_scan",
            {"threshold": 20},
            "analyze",
        )

        fired = trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    def test_periodic_scan_old_last_used(
        self, trigger_engine, project_id
    ):
        """``last_used_at`` older than interval fires the trigger."""
        skill = _make_skill(trigger_engine.skill_repo, project_id, "stale")
        # last_used_at set to 10 days ago.
        old_ts = (
            datetime.now(timezone.utc) - timedelta(days=10)
        ).isoformat()
        trigger_engine.skill_repo.update(
            skill.id, last_used_at=old_ts
        )

        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "ps",
            "periodic_scan",
            {"interval_days": 7},
            "analyze",
        )
        fired = trigger_engine._evaluate_condition(trigger, skill)
        assert fired is True

    def test_periodic_scan_recent_last_used(
        self, trigger_engine, project_id
    ):
        """Recent activity: condition does not fire."""
        skill = _make_skill(trigger_engine.skill_repo, project_id, "fresh")
        recent_ts = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat()
        trigger_engine.skill_repo.update(
            skill.id, last_used_at=recent_ts
        )

        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "ps",
            "periodic_scan",
            {"interval_days": 7},
            "analyze",
        )
        fired = trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    def test_periodic_scan_never_used_skipped(
        self, trigger_engine, project_id
    ):
        """``last_used_at IS NULL`` -> periodic_scan does not fire."""
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "unused"
        )
        # Default state: last_used_at is None.
        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "ps",
            "periodic_scan",
            {"interval_days": 7},
            "analyze",
        )
        fired = trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    def test_unknown_condition_type_returns_false(
        self, trigger_engine, project_id
    ):
        """An unknown condition_type returns False (no error)."""
        skill = _make_skill(trigger_engine.skill_repo, project_id, "x")
        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "weird",
            "unknown_condition",
            {"threshold": 1},
            "analyze",
        )
        fired = trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False


# =============================================================================
# evaluate_all — end-to-end
# =============================================================================


class TestEvaluateAll:
    """Tests for :meth:`SkillTriggerEngine.evaluate_all`."""

    async def test_no_triggers_no_flags(self, trigger_engine):
        flagged = await trigger_engine.evaluate_all()
        assert flagged == []

    async def test_no_matching_skills_no_flags(self, trigger_engine):
        """Trigger exists but no skill meets the threshold."""
        _make_trigger(
            trigger_engine.trigger_repo,
            "low_cr",
            "low_completion_rate",
            {"threshold": 0.3, "min_selections": 5},
            "analyze",
        )
        # No skills at all.
        flagged = await trigger_engine.evaluate_all()
        assert flagged == []

    async def test_flagged_skill_appears_in_results(
        self, trigger_engine, project_id
    ):
        skill = _make_skill(trigger_engine.skill_repo, project_id, "bad")
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_selections", amount=10
        )
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_completions", amount=1  # 0.1 < 0.3
        )
        _make_trigger(
            trigger_engine.trigger_repo,
            "low_cr",
            "low_completion_rate",
            {"threshold": 0.3, "min_selections": 5},
            "analyze",
        )

        flagged = await trigger_engine.evaluate_all()
        assert len(flagged) == 1
        entry = flagged[0]
        assert entry["skill_id"] == skill.id
        assert entry["skill_name"] == "bad"
        assert entry["trigger_name"] == "low_cr"
        assert entry["trigger_action"] == "analyze"
        assert "low_completion_rate" in entry["reason"]
        # Stats dict is present.
        assert "stats" in entry
        # New stats shape (delegated to get_stats_filtered): uses
        # usage-record aggregation. This test setup only bumps
        # counters (no usage records), so total reflects 0.
        assert entry["stats"]["total"] == 0

    async def test_stable_sorting(
        self, trigger_engine, project_id
    ):
        """Results are sorted by (trigger_name, skill_name)."""
        # Three skills; one trigger that flags all (completion_rate=0).
        for name in ("charlie", "alpha", "bravo"):
            skill = _make_skill(
                trigger_engine.skill_repo, project_id, name
            )
            trigger_engine.skill_repo.increment_counter(
                skill.id, "total_selections", amount=10
            )
        _make_trigger(
            trigger_engine.trigger_repo,
            "low_cr",
            "low_completion_rate",
            {"threshold": 0.3, "min_selections": 5},
            "analyze",
        )

        flagged = await trigger_engine.evaluate_all()
        names = [f["skill_name"] for f in flagged]
        # Sorted alphabetically.
        assert names == ["alpha", "bravo", "charlie"]

    async def test_multiple_triggers_same_skill(
        self, trigger_engine, project_id
    ):
        """A skill can be flagged by multiple triggers."""
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "multi"
        )
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_selections", amount=10
        )
        # No completions → low_completion_rate fires.
        # 10 fallbacks → high_fallback_rate fires (10/10=1.0).
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_fallbacks", amount=10
        )

        _make_trigger(
            trigger_engine.trigger_repo,
            "low_cr",
            "low_completion_rate",
            {"threshold": 0.3, "min_selections": 5},
            "analyze",
        )
        _make_trigger(
            trigger_engine.trigger_repo,
            "high_fb",
            "high_fallback_rate",
            {"threshold": 0.5, "min_selections": 5},
            "analyze",
        )

        flagged = await trigger_engine.evaluate_all()
        trigger_names = {f["trigger_name"] for f in flagged}
        assert trigger_names == {"high_fb", "low_cr"}

    async def test_inactive_skill_excluded(
        self, trigger_engine, project_id
    ):
        """``is_active=False`` skills are excluded by ``skill_repo.list``."""
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "inactive"
        )
        trigger_engine.skill_repo.increment_counter(
            skill.id, "total_selections", amount=10
        )
        # Set up a low_completion_rate trigger.
        _make_trigger(
            trigger_engine.trigger_repo,
            "low_cr",
            "low_completion_rate",
            {"threshold": 0.3, "min_selections": 5},
            "analyze",
        )
        # Deactivate the skill.
        trigger_engine.skill_repo.deactivate(skill.id)

        flagged = await trigger_engine.evaluate_all()
        assert flagged == []

    async def test_disabled_trigger_skipped(self, trigger_engine):
        """``is_enabled=False`` triggers are skipped."""
        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "low_cr",
            "low_completion_rate",
            {"threshold": 0.3, "min_selections": 5},
            "analyze",
        )
        # Disable it.
        trigger_engine.trigger_repo.update(
            trigger.id, is_enabled=False
        )

        flagged = await trigger_engine.evaluate_all()
        assert flagged == []

    async def test_reason_formatting_per_condition_type(
        self, trigger_engine, project_id
    ):
        """``reason`` field differs per condition_type."""
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "reason-test"
        )
        trigger_engine.skill_repo.increment_counter(
            skill.id, "consecutive_failures", amount=5
        )
        _make_trigger(
            trigger_engine.trigger_repo,
            "cf",
            "consecutive_failures",
            {"threshold": 3},
            "evolve_fix",
        )
        flagged = await trigger_engine.evaluate_all()
        assert len(flagged) == 1
        assert "consecutive_failures" in flagged[0]["reason"]
        assert "reason-test" in flagged[0]["reason"]


# =============================================================================
# seed_default_triggers
# =============================================================================


class TestSeedDefaultTriggers:
    """Tests for ``seed_default_triggers``."""

    async def test_first_seed_inserts_all_defaults(
        self, engine
    ):
        from daemon.repositories.skill.repository import (
            SkillTriggerRepository,
        )
        from daemon.services.skill_trigger_seed import (
            DEFAULT_TRIGGERS,
            seed_default_triggers,
        )

        trigger_repo = SkillTriggerRepository(engine)
        inserted = await seed_default_triggers(
            trigger_repo, project_id=None
        )
        assert inserted == len(DEFAULT_TRIGGERS)

        # Verify all default names are present.
        all_triggers = trigger_repo.list(
            project_id=None, enabled_only=False
        )
        names = {t.name for t in all_triggers}
        expected_names = {t["name"] for t in DEFAULT_TRIGGERS}
        assert expected_names.issubset(names)

    async def test_second_seed_is_noop(
        self, engine
    ):
        """Re-running the seeder doesn't duplicate rows."""
        from daemon.repositories.skill.repository import (
            SkillTriggerRepository,
        )
        from daemon.services.skill_trigger_seed import (
            DEFAULT_TRIGGERS,
            seed_default_triggers,
        )

        trigger_repo = SkillTriggerRepository(engine)
        await seed_default_triggers(trigger_repo, project_id=None)
        # Second call: 0 inserts.
        inserted2 = await seed_default_triggers(
            trigger_repo, project_id=None
        )
        assert inserted2 == 0

        # Still exactly the same number of rows.
        all_triggers = trigger_repo.list(
            project_id=None, enabled_only=False
        )
        assert len(all_triggers) == len(DEFAULT_TRIGGERS)

    async def test_disabled_default_not_re_created(
        self, engine
    ):
        """A disabled default is preserved (not duplicated, not skipped)."""
        from daemon.repositories.skill.repository import (
            SkillTriggerRepository,
        )
        from daemon.services.skill_trigger_seed import seed_default_triggers

        trigger_repo = SkillTriggerRepository(engine)
        # Seed once.
        await seed_default_triggers(trigger_repo, project_id=None)
        # Disable one of the defaults.
        all_triggers = trigger_repo.list(
            project_id=None, enabled_only=False
        )
        cf = next(t for t in all_triggers if t.name == "consecutive_failures")
        trigger_repo.update(cf.id, is_enabled=False)

        # Seed again — no new inserts, the disabled row is preserved.
        inserted = await seed_default_triggers(
            trigger_repo, project_id=None
        )
        assert inserted == 0

        # Find the disabled row again — still there, still disabled.
        again = trigger_repo.list(
            project_id=None, enabled_only=False
        )
        cf2 = next(
            t for t in again if t.name == "consecutive_failures"
        )
        assert cf2.is_enabled is False

    async def test_default_trigger_structure(
        self, engine
    ):
        """Every default trigger has the expected shape."""
        from daemon.services.skill_trigger_seed import DEFAULT_TRIGGERS

        names = {t["name"] for t in DEFAULT_TRIGGERS}
        assert names == {
            "low_completion_rate",
            "high_fallback_rate",
            "consecutive_failures",
            "periodic_scan",
            "task_count_scan",
        }
        for trigger in DEFAULT_TRIGGERS:
            assert "condition_type" in trigger
            assert "condition_json" in trigger
            assert "action" in trigger
            assert trigger["action"] in {"analyze", "evolve_fix"}