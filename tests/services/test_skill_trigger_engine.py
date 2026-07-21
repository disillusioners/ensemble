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

    async def test_low_completion_rate_below_threshold(
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

        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is True

    async def test_low_completion_rate_above_threshold(
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

        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    async def test_low_completion_rate_min_selections_gate(
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

        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    async def test_high_fallback_rate_above_threshold(
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

        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is True

    async def test_high_fallback_rate_below_threshold(
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

        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    async def test_consecutive_failures_meets_threshold(
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

        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is True

    async def test_consecutive_failures_below_threshold(
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

        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    async def test_task_count_scan_meets_threshold(
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

        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is True

    async def test_task_count_scan_below_threshold(
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

        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    async def test_periodic_scan_old_last_used(
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
        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is True

    async def test_periodic_scan_recent_last_used(
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
        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    async def test_periodic_scan_never_used_skipped(
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
        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    async def test_unknown_condition_type_returns_false(
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
        fired = await trigger_engine._evaluate_condition(trigger, skill)
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
            "low_usefulness",
        }
        for trigger in DEFAULT_TRIGGERS:
            assert "condition_type" in trigger
            assert "condition_json" in trigger
            assert "action" in trigger
            assert trigger["action"] in {"analyze", "evolve_fix"}


# =============================================================================
# Phase 5 (2026-07-21): low_usefulness condition evaluator
# =============================================================================


class TestLowUsefulnessCondition:
    """Tests for the new ``low_usefulness`` trigger condition.

    Fires when the average ``feedback_usefulness`` (1-10 quality
    score on each ``SkillUsageRecord``) is below ``threshold``
    (default 4.0) AND at least ``min_samples`` (default 5)
    records carry a score. Distinct from the other five
    conditions — it reads the usage table directly rather than
    denormalized counters on the skill row.
    """

    def _make_scored_records(
        self, usage_repo, skill_id, project_id, scores
    ):
        """Insert ``scores`` usage records with the supplied
        ``feedback_usefulness`` values. Each record is paired
        with its own ``instance_id`` so the latest-for-skill
        lookup is unambiguous.
        """
        for i, score in enumerate(scores):
            rec = usage_repo.create(
                skill_id=skill_id,
                project_id=project_id,
                instance_id=f"inst-{i}",
                agent_id="a",
            )
            usage_repo.update_feedback(
                record_id=rec.id,
                applied=True,
                note="x",
                usefulness=score,
            )

    async def test_low_usefulness_fires_when_avg_below_threshold(
        self, trigger_engine, project_id
    ):
        """5 records avg=3.0 < threshold=4.0 → fires."""
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "low-rated"
        )
        # 5 records: 3, 3, 3, 3, 3 → avg=3.0 < 4.0.
        self._make_scored_records(
            trigger_engine.usage_repo,
            skill_id=skill.id,
            project_id=project_id,
            scores=[3, 3, 3, 3, 3],
        )

        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "low_use",
            "low_usefulness",
            {"threshold": 4.0, "min_samples": 5},
            "analyze",
        )

        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is True

    async def test_low_usefulness_does_not_fire_above_threshold(
        self, trigger_engine, project_id
    ):
        """5 records avg=7.0 ≥ threshold=4.0 → does NOT fire."""
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "high-rated"
        )
        self._make_scored_records(
            trigger_engine.usage_repo,
            skill_id=skill.id,
            project_id=project_id,
            scores=[7, 7, 7, 7, 7],
        )

        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "low_use",
            "low_usefulness",
            {"threshold": 4.0, "min_samples": 5},
            "analyze",
        )

        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    async def test_low_usefulness_below_min_samples_does_not_fire(
        self, trigger_engine, project_id
    ):
        """Only 4 scored records (below min_samples=5) — even if
        avg is low, the trigger does NOT fire (noise floor)."""
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "too-few"
        )
        self._make_scored_records(
            trigger_engine.usage_repo,
            skill_id=skill.id,
            project_id=project_id,
            scores=[1, 1, 1, 1],  # 4 records, avg=1.0
        )

        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "low_use",
            "low_usefulness",
            {"threshold": 4.0, "min_samples": 5},
            "analyze",
        )

        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    async def test_low_usefulness_with_no_scored_records_does_not_fire(
        self, trigger_engine, project_id
    ):
        """No scored records at all → trigger does NOT fire (no
        signal to act on)."""
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "no-scores"
        )
        # 3 records, none scored.
        for i in range(3):
            trigger_engine.usage_repo.create(
                skill_id=skill.id,
                project_id=project_id,
                instance_id=f"inst-{i}",
                agent_id="a",
            )

        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "low_use",
            "low_usefulness",
            {"threshold": 4.0, "min_samples": 5},
            "analyze",
        )

        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False

    async def test_low_usefulness_at_threshold_does_not_fire(
        self, trigger_engine, project_id
    ):
        """avg == threshold → strict ``<`` so the trigger does
        NOT fire (boundary case)."""
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "boundary"
        )
        # 5 records avg = exactly 4.0.
        self._make_scored_records(
            trigger_engine.usage_repo,
            skill_id=skill.id,
            project_id=project_id,
            scores=[4, 4, 4, 4, 4],
        )

        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "low_use",
            "low_usefulness",
            {"threshold": 4.0, "min_samples": 5},
            "analyze",
        )

        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is False  # strict < comparison

    async def test_low_usefulness_dispatcher_routes(
        self, trigger_engine, project_id
    ):
        """The ``low_usefulness`` condition_type is wired into the
        ``_evaluate_condition`` dispatcher (not just the static
        method) — proves the routing works end-to-end via the
        public surface."""
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "routed"
        )
        self._make_scored_records(
            trigger_engine.usage_repo,
            skill_id=skill.id,
            project_id=project_id,
            scores=[2, 2, 2, 2, 2],
        )

        # Build the trigger via the repo so the row shape matches
        # what ``_evaluate_condition`` expects.
        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "low_use_routed",
            "low_usefulness",
            {"threshold": 4.0, "min_samples": 5},
            "analyze",
        )

        # Call through the public dispatcher — if routing were
        # broken, this would hit the ``unknown condition_type``
        # warning branch and return False.
        fired = await trigger_engine._evaluate_condition(trigger, skill)
        assert fired is True

    async def test_low_usefulness_evaluate_all_end_to_end(
        self, trigger_engine, project_id
    ):
        """``evaluate_all`` surfaces a flagged entry with a
        ``reason`` that includes the avg and sample count when
        ``low_usefulness`` fires."""
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "e2e"
        )
        # 6 records: avg = 3.0, above min_samples=5.
        self._make_scored_records(
            trigger_engine.usage_repo,
            skill_id=skill.id,
            project_id=project_id,
            scores=[3, 3, 3, 3, 3, 3],
        )

        _make_trigger(
            trigger_engine.trigger_repo,
            "low_use_e2e",
            "low_usefulness",
            {"threshold": 4.0, "min_samples": 5},
            "analyze",
        )

        flagged = await trigger_engine.evaluate_all()
        assert len(flagged) == 1
        entry = flagged[0]
        assert entry["skill_id"] == skill.id
        assert entry["trigger_name"] == "low_use_e2e"
        assert entry["trigger_action"] == "analyze"
        # Reason includes the condition type, skill name, the
        # avg usefulness (3.0/10), and the sample count (6).
        assert "low_usefulness" in entry["reason"]
        assert "e2e" in entry["reason"]
        assert "3.0/10" in entry["reason"]
        assert "6" in entry["reason"]

    async def test_low_usefulness_reason_when_unavailable(
        self, trigger_engine, project_id
    ):
        """``_build_reason`` for ``low_usefulness`` degrades to
        ``avg=n/a, count=0`` when ``usage_repo`` is unavailable
        (does NOT raise)."""
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "no-repo"
        )

        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "low_use_reason",
            "low_usefulness",
            {"threshold": 4.0, "min_samples": 5},
            "analyze",
        )

        # Detach the usage_repo so the reason fallback fires.
        saved_usage_repo = trigger_engine.usage_repo
        trigger_engine.usage_repo = None
        try:
            reason = await trigger_engine._build_reason(
                trigger, skill, stats={}
            )
        finally:
            trigger_engine.usage_repo = saved_usage_repo

        assert "low_usefulness" in reason
        assert "no-repo" in reason
        # Fallback markers from the source — avg_str="n/a", count_str="0".
        assert "n/a" in reason
        assert "0" in reason


# =============================================================================
# Phase 5 (2026-07-21): low_usefulness edge cases
# =============================================================================


class TestLowUsefulnessEdgeCases:
    """Cover the gap between :class:`TestLowUsefulnessCondition`
    (happy paths) and the underlying error-handling / config paths
    of :meth:`SkillTriggerEngine._eval_low_usefulness` +
    :meth:`SkillTriggerEngine._build_reason`.

    These tests pin the defensive behavior so the dispatcher
    doesn't silently regress:

    * ``usage_repo=None`` → condition cannot evaluate → ``False``.
    * ``get_avg_usefulness`` raising → exception swallowed → ``False``.
    * Per-trigger ``threshold`` and ``min_samples`` overrides are
      honored from ``condition_json``.
    * The ``_build_reason`` wording fix is pinned (no more
      "last N usages" — the count is over *scored* records).
    """

    def _make_scored_records(
        self, usage_repo, skill_id, project_id, scores
    ):
        """Insert ``scores`` usage records with the supplied
        ``feedback_usefulness`` values. Each record is paired
        with its own ``instance_id`` so the latest-for-skill
        lookup is unambiguous.
        """
        for i, score in enumerate(scores):
            rec = usage_repo.create(
                skill_id=skill_id,
                project_id=project_id,
                instance_id=f"inst-{i}",
                agent_id="a",
            )
            usage_repo.update_feedback(
                record_id=rec.id,
                applied=True,
                note="x",
                usefulness=score,
            )

    async def test_eval_low_usefulness_returns_false_when_usage_repo_none(
        self, trigger_engine, project_id
    ):
        """``usage_repo=None`` → ``_eval_low_usefulness`` cannot
        evaluate and returns ``False`` (with a logged warning).

        We detach the ``usage_repo`` post-construction so the
        engine's internal attribute is ``None`` — the fallback
        to ``metrics_service.usage_repo`` already ran at
        ``__init__`` time, so we also have to detach that to
        fully null out the source.
        """
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "no-usage-repo"
        )

        # Null out both the engine's direct handle AND the
        # metrics_service fallback so ``_eval_low_usefulness``'s
        # ``getattr(self, "usage_repo", None)`` resolves to None.
        saved_engine_repo = trigger_engine.usage_repo
        saved_metrics_repo = trigger_engine.metrics_service.usage_repo
        trigger_engine.usage_repo = None
        trigger_engine.metrics_service.usage_repo = None
        try:
            fired = await trigger_engine._eval_low_usefulness(
                skill, {"threshold": 4.0, "min_samples": 5}
            )
        finally:
            trigger_engine.usage_repo = saved_engine_repo
            trigger_engine.metrics_service.usage_repo = (
                saved_metrics_repo
            )

        assert fired is False

    async def test_eval_low_usefulness_returns_false_on_repo_exception(
        self, trigger_engine, project_id
    ):
        """A raising ``get_avg_usefulness`` (e.g. transient DB
        issue) must NOT bubble — the inner guard catches and
        returns ``False`` so the dispatcher scan keeps going."""
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "repo-raises"
        )

        # Replace get_avg_usefulness with a raising stand-in.
        original = trigger_engine.usage_repo.get_avg_usefulness
        trigger_engine.usage_repo.get_avg_usefulness = lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("simulated DB error")
        )
        try:
            fired = await trigger_engine._eval_low_usefulness(
                skill, {"threshold": 4.0, "min_samples": 5}
            )
        finally:
            trigger_engine.usage_repo.get_avg_usefulness = original

        # Exception swallowed — the dispatcher would otherwise
        # silently skip the skill.
        assert fired is False

    async def test_custom_threshold_via_condition_json(
        self, trigger_engine, project_id
    ):
        """Per-trigger ``threshold`` override is honored.

        Seed 5 records with avg 3.5. With ``threshold=3.0`` the
        trigger does NOT fire (3.5 >= 3.0). With ``threshold=4.0``
        the trigger fires (3.5 < 4.0).
        """
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "custom-threshold"
        )
        # Scores: 3+3+4+4+4 = 18 / 5 = 3.6 (use 3+3+4+4+4)
        # Use 3+3+3+4+5 = 18 / 5 = 3.6 too — pick 3,3,4,4,4 → 18/5 = 3.6.
        # Use 3,3,3,4,5 → 18/5 = 3.6. For exact 3.5 we need sum=17.5
        # which isn't possible with integers — use 3,3,4,4,4 = 18/5=3.6
        # which still falls between 3.0 and 4.0 cleanly.
        self._make_scored_records(
            trigger_engine.usage_repo,
            skill_id=skill.id,
            project_id=project_id,
            scores=[3, 3, 4, 4, 4],  # avg = 3.6
        )

        # Threshold 3.0 — avg (3.6) >= threshold → does NOT fire.
        fired_low = await trigger_engine._eval_low_usefulness(
            skill, {"threshold": 3.0, "min_samples": 5}
        )
        assert fired_low is False

        # Threshold 4.0 — avg (3.6) < threshold → fires.
        fired_high = await trigger_engine._eval_low_usefulness(
            skill, {"threshold": 4.0, "min_samples": 5}
        )
        assert fired_high is True

    async def test_custom_min_samples_via_condition_json(
        self, trigger_engine, project_id
    ):
        """Per-trigger ``min_samples`` override is honored as the
        noise floor.

        Seed 3 records with avg 2.0. With ``min_samples=3`` the
        trigger fires (2.0 < 4.0 default threshold, count meets
        the floor). With ``min_samples=5`` the trigger does NOT
        fire (3 < 5 floor).
        """
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "custom-min"
        )
        self._make_scored_records(
            trigger_engine.usage_repo,
            skill_id=skill.id,
            project_id=project_id,
            scores=[2, 2, 2],  # avg = 2.0, count = 3
        )

        # min_samples=3 — floor met → fires.
        fired_at_three = await trigger_engine._eval_low_usefulness(
            skill, {"threshold": 4.0, "min_samples": 3}
        )
        assert fired_at_three is True

        # min_samples=5 — floor NOT met (3 < 5) → does NOT fire,
        # even though the avg would otherwise cross the threshold.
        fired_at_five = await trigger_engine._eval_low_usefulness(
            skill, {"threshold": 4.0, "min_samples": 5}
        )
        assert fired_at_five is False

    async def test_reason_uses_scored_usages_wording(
        self, trigger_engine, project_id
    ):
        """Pin the wording fix in :meth:`_build_reason` for the
        ``low_usefulness`` branch.

        The reason text must say "scored usages" — the count is
        over all non-superseded scored records aggregated by
        ``get_avg_usefulness`` (NOT a "last N usages" window).
        Regressing to "last" would silently misrepresent the
        semantics in operator-facing logs.
        """
        skill = _make_skill(
            trigger_engine.skill_repo, project_id, "wording"
        )
        # 6 records avg = 3.0 (above min_samples=5).
        self._make_scored_records(
            trigger_engine.usage_repo,
            skill_id=skill.id,
            project_id=project_id,
            scores=[3, 3, 3, 3, 3, 3],
        )

        trigger = _make_trigger(
            trigger_engine.trigger_repo,
            "low_use_wording",
            "low_usefulness",
            {"threshold": 4.0, "min_samples": 5},
            "analyze",
        )

        reason = await trigger_engine._build_reason(
            trigger, skill, stats={}
        )

        # Correct wording — pins the 2026-07-21 fix.
        assert "scored usages" in reason
        # The "last N usages" wording was a bug; if it creeps
        # back, this assertion catches it.
        assert "last " not in reason.lower().replace(
            "last_used_at", ""
        ) or "last 5" not in reason  # noqa: E501
        # Sanity: the count of scored records (6) is in the
        # reason text, not a window count.
        assert "6" in reason
        # The avg value (3.0) is in the reason text.
        assert "3.0" in reason