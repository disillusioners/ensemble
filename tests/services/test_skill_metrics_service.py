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

    async def test_failed_task_with_explicit_feedback_increments_failures_and_fallback(
        self, metrics_service, skill_repo, project_id
    ):
        """Failure + worker applied=False feedback: fallback=True, streak grows.

        Phase 4 Option C: ``total_fallbacks`` is no longer driven by task
        failure alone. The worker must explicitly call ``skill_feedback``
        with ``applied=False`` for the fallback counter to bump.
        """
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
        # Option C: explicit worker feedback (applied=False) drives fallback.
        await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=inst_id,
            agent_id="agent-x",
            project_id=project_id,
            applied=False,
            note="skill did not help",
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
        """``last_injected_skill_ids`` and ``explicitly_replaced_ids`` are cleared.

        Both metadata keys are wiped from the instance after the
        completion hook returns — ``last_injected_skill_ids`` so
        the next task starts with a fresh injection set, and
        ``explicitly_replaced_ids`` so the finalize-on-replace
        blocklist doesn't permanently disable auto_load for
        the replaced skill(s). Other keys (``other_key``) are
        preserved untouched.
        """
        skill = _make_skill(skill_repo, project_id, "delta")
        inst_id = "inst-clear"
        fake_inst = FakeInstance(
            inst_id,
            metadata={
                "last_injected_skill_ids": [skill.id],
                "explicitly_replaced_ids": [skill.id],
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
        assert "explicitly_replaced_ids" not in (
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
        assert "explicitly_replaced_ids" in cleared_keys

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
# task_message plumbing (CAPTURED-skill fix)
# =============================================================================


class TestTaskMessagePlumbing:
    """``task_message`` flows from ``record_task_completion`` into
    ``skill_usage_records.task_message`` on both the INSERT and
    UPDATE branches.

    Closes the CAPTURED-plumbing gap: the user-asked snapshot is
    the canonical input to the skill-keeper LLM prompt. Without
    this plumbing the column stays ``NULL`` forever and the
    auto-extraction flow receives an empty string.
    """

    async def test_record_one_persists_task_message(
        self, metrics_service, skill_repo, project_id
    ):
        """``record_task_completion(..., task_message=...)`` writes
        the column on the INSERT path.

        No prior usage row exists, so ``_record_one`` takes the
        ``usage_repo.create`` branch. Asserts the persisted row
        carries the supplied snapshot verbatim — not truncated,
        not coerced to ``None``, not stripped.
        """
        from daemon.services.skill_metrics_service import (
            INJECTED_SKILLS_METADATA_KEY,
        )

        skill = _make_skill(skill_repo, project_id, "tm-insert")
        instance_id = "inst-tm-insert"
        metrics_service.instance_repo._instances[instance_id] = (
            FakeInstance(
                instance_id,
                metadata={INJECTED_SKILLS_METADATA_KEY: [skill.id]},
            )
        )

        inserted = await metrics_service.record_task_completion(
            instance_id=instance_id,
            agent_id="agent-x",
            project_id=project_id,
            task_succeeded=True,
            iterations=1,
            duration_seconds=1,
            task_message="my user task",
        )
        assert inserted == 1

        rec = metrics_service.usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=instance_id
        )
        assert rec is not None
        assert rec.task_message == "my user task"

    async def test_record_one_update_completion_preserves_or_updates_task_message(
        self, metrics_service, skill_repo, project_id
    ):
        """Feedback-first path → row exists → completion hook
        back-fills ``task_message`` via ``update_completion``.

        Production sequence: the agent calls ``skill_feedback``
        FIRST (no completion hook has fired yet), inserting a row
        on the ``record_feedback`` on-miss branch. The
        ``task_message`` column is ``NULL`` at that point because
        the feedback path doesn't know what the user asked.
        Then the completion hook fires later with the actual
        snapshot. The UPDATE branch in
        :meth:`SkillUsageRepository.update_completion` must
        forward ``task_message`` and populate the column.
        """
        from daemon.services.skill_metrics_service import (
            INJECTED_SKILLS_METADATA_KEY,
        )

        skill = _make_skill(skill_repo, project_id, "tm-update")
        instance_id = "inst-tm-update"

        # Step 1: feedback lands first, inserts an on-miss row
        # without task_message (the feedback path can't know
        # what the user asked — only the completion hook has
        # that context).
        await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=instance_id,
            agent_id="tester",
            project_id=project_id,
            applied=True,
            note="worked great",
        )

        # At this point, the latest usage row exists and
        # task_message should be NULL.
        pre = metrics_service.usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=instance_id
        )
        assert pre is not None
        assert pre.task_message is None, (
            "feedback-first path inserts without task_message; the "
            "completion hook back-fills it"
        )

        # Step 2: wire the injected-skill metadata so
        # ``record_task_completion`` proceeds; then call the
        # completion hook with the actual user snapshot.
        metrics_service.instance_repo._instances[instance_id] = (
            FakeInstance(
                instance_id,
                metadata={INJECTED_SKILLS_METADATA_KEY: [skill.id]},
            )
        )
        inserted = await metrics_service.record_task_completion(
            instance_id=instance_id,
            agent_id="tester",
            project_id=project_id,
            task_succeeded=True,
            iterations=4,
            duration_seconds=120,
            task_message="late message",
        )
        assert inserted == 1

        # Step 3: read back — task_message must now be set.
        post = metrics_service.usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=instance_id
        )
        assert post is not None
        assert post.task_message == "late message", (
            "Late-arriving completion hook must overwrite the "
            "NULL task_message left by the on-miss feedback "
            "insert with the real user snapshot"
        )
        # And the feedback signal must be preserved.
        assert post.feedback_applied is True
        assert post.feedback_note == "worked great"
        # And the completion columns must be stamped (existing
        # behavior — the regression guard for the original
        # idempotency fix).
        assert post.task_succeeded is True
        assert post.iterations == 4
        assert post.duration_seconds == 120


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

    async def test_missing_skill_returns_false(
        self, metrics_service, skill_repo, project_id
    ):
        """Skill row deleted between injection and feedback → False.

        Previously this asserted "no record exists → False". The
        record-on-miss path now inserts a usage record when the
        skill row still exists, so the only way to land back on
        ``False`` is when the skill itself was removed from the
        ``skills`` table (the on-miss insert can't lookup
        ``ab_test_group`` and has nothing to attach to). Covers
        the soft-failure contract: the tool returns ``False``,
        never raises.
        """
        # Create the skill, then delete it before feedback runs.
        # (Without a row in ``skills`` the on-miss insert path
        # falls back to the ``False`` return.)
        skill = _make_skill(skill_repo, project_id, "kappa")
        skill_repo.delete(skill.id)
        ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id="inst-NEVER",
            agent_id="a",
            project_id=project_id,
            applied=True,
            note="",
        )
        assert ok is False

    async def test_no_record_inserts_on_miss(
        self, metrics_service, skill_repo, project_id
    ):
        """On-miss insert: feedback recorded even when no usage row exists.

        Reproduces the prod bug where a worker (``process_message``
        task path, no job-queue completion hook) called
        ``skill_feedback`` on a skill that was injected via
        ``<meta load_skill=...>``. The completion hook never ran,
        so no usage record existed — the feedback was silently
        dropped ("No usage record found..."). The on-miss insert
        path now creates the record with feedback signals stamped
        directly so the signal is preserved.
        """
        skill = _make_skill(skill_repo, project_id, "lambda")
        ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id="inst-onmiss",
            agent_id="a",
            project_id=project_id,
            applied=False,
            note="skill loaded but task needed test-pack-execution",
        )
        assert ok is True

        rec = metrics_service.usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id="inst-onmiss"
        )
        assert rec is not None
        # Selected=True because the agent wouldn't be giving
        # feedback on a skill it never received.
        assert rec.selected is True
        assert rec.feedback_applied is False
        assert (
            rec.feedback_note
            == "skill loaded but task needed test-pack-execution"
        )
        assert rec.fallback is True  # applied=False falls back
        # On-miss insert bumped selection + fallback counters.
        refreshed = skill_repo.get(skill.id)
        assert refreshed.total_selections == 1
        assert refreshed.total_fallbacks == 1

    async def test_on_miss_insert_then_completion_updates(
        self, metrics_service, skill_repo, project_id
    ):
        """On-miss insert + late completion hook → UPDATE, no duplicate.

        The agent calls ``skill_feedback`` first (creates row on
        miss), then the completion hook fires (via
        ``record_task_completion``) for the same (skill,
        instance). The completion hook MUST NOT insert a second
        row — it calls ``update_completion`` on the existing
        row to stamp the task-outcome columns without losing
        the feedback signal or double-bumping
        ``total_selections``.
        """
        skill = _make_skill(skill_repo, project_id, "mu")
        instance_id = "inst-interleave"
        metrics_service.instance_repo._instances[instance_id] = (
            FakeInstance(
                instance_id,
                metadata={"last_injected_skill_ids": [skill.id]},
            )
        )

        ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=instance_id,
            agent_id="tester",
            project_id=project_id,
            applied=True,
            note="helpful for discovery",
        )
        assert ok is True

        inserted = await metrics_service.record_task_completion(
            instance_id=instance_id,
            agent_id="tester",
            project_id=project_id,
            task_succeeded=True,
            iterations=4,
            duration_seconds=120,
        )
        assert inserted == 1

        records, total = metrics_service.usage_repo.get_by_skill(skill.id)
        assert total == 1, (
            f"Expected EXACTLY ONE row after feedback-then-completion, "
            f"got {total}: {[r.id for r in records]}"
        )
        rec = records[0]
        assert rec.feedback_applied is True  # Preserved from feedback
        assert rec.feedback_note == "helpful for discovery"
        assert rec.task_succeeded is True  # Stamped by completion
        assert rec.iterations == 4
        assert rec.duration_seconds == 120
        # ``total_selections`` bumped ONCE (by the on-miss insert);
        # the completion path must not double-count.
        assert skill_repo.get(skill.id).total_selections == 1
        assert skill_repo.get(skill.id).total_applied == 1
        assert skill_repo.get(skill.id).total_completions == 1


# =============================================================================
# get_skill_stats
# =============================================================================


class TestGetSkillStats:
    """Tests for :meth:`SkillMetricsService.get_skill_stats`."""

    async def test_returns_zero_for_missing_skill(self, metrics_service):
        stats = await metrics_service.get_skill_stats("no-such")
        assert stats == {
            "total": 0,
            "selected": 0,
            "applied": 0,
            "completions": 0,
            "fallbacks": 0,
            "avg_iterations": 0.0,
            "avg_duration": 0.0,
            "completion_rate": 0.0,
            "applied_rate": 0.0,
            "fallback_rate": 0.0,
            "consecutive_failures": 0,
        }

    async def test_returns_zero_rates_for_zero_selections(
        self, metrics_service, skill_repo, project_id
    ):
        """No selections -> all rates are 0.0 (no div-by-zero)."""
        skill = _make_skill(skill_repo, project_id, "lambda")
        stats = await metrics_service.get_skill_stats(skill.id)
        assert stats["total"] == 0
        assert stats["completion_rate"] == 0.0
        assert stats["fallback_rate"] == 0.0
        assert stats["applied_rate"] == 0.0

    async def test_completion_rate_computed(
        self, metrics_service, skill_repo, usage_repo, project_id
    ):
        """Completion rate is completions / total from usage records."""
        skill = _make_skill(skill_repo, project_id, "mu")
        for _ in range(6):
            usage_repo.create(
                skill_id=skill.id, project_id=project_id,
                instance_id="i", agent_id="a", selected=True,
            )
        for _ in range(4):
            usage_repo.create(
                skill_id=skill.id, project_id=project_id,
                instance_id="i", agent_id="a", selected=True,
                task_succeeded=True,
            )
        stats = await metrics_service.get_skill_stats(skill.id)
        assert stats["completion_rate"] == pytest.approx(0.4)
        assert stats["total"] == 10
        assert stats["completions"] == 4

    async def test_fallback_and_applied_rates(
        self, metrics_service, skill_repo, usage_repo, project_id
    ):
        """fallback_rate and applied_rate derive from usage records."""
        skill = _make_skill(skill_repo, project_id, "nu")
        # 10 records total: 5 with fallback=True, 2 with applied=True
        for _ in range(3):
            usage_repo.create(
                skill_id=skill.id, project_id=project_id,
                instance_id="i", agent_id="a", selected=True,
            )
        for _ in range(5):
            usage_repo.create(
                skill_id=skill.id, project_id=project_id,
                instance_id="i", agent_id="a", selected=True,
                fallback=True,
            )
        for _ in range(2):
            usage_repo.create(
                skill_id=skill.id, project_id=project_id,
                instance_id="i", agent_id="a", selected=True,
                applied=True,
            )
        stats = await metrics_service.get_skill_stats(skill.id)
        assert stats["total"] == 10
        assert stats["fallback_rate"] == pytest.approx(0.5)
        assert stats["applied_rate"] == pytest.approx(0.2)


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
        group = "g1"
        skill_old = _make_skill(
            skill_repo, project_id, "old", ab_test_group=group
        )
        skill_new = _make_skill(
            skill_repo, project_id, "new", ab_test_group=group
        )
        for _ in range(4):
            usage_repo.create(
                skill_id=skill_old.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=True,
                ab_test_group=group,
            )
        usage_repo.create(
            skill_id=skill_old.id, project_id=project_id,
            instance_id="i2", agent_id="a",
            task_succeeded=False,
            ab_test_group=group,
        )
        for _ in range(2):
            usage_repo.create(
                skill_id=skill_new.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=True,
                ab_test_group=group,
            )
        usage_repo.create(
            skill_id=skill_new.id, project_id=project_id,
            instance_id="i2", agent_id="a",
            task_succeeded=False,
            ab_test_group=group,
        )

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
        """ready_to_resolve iff comparisons>=sample AND diff>=min.

        With the composite score: skill_new beats skill_old on
        completion_rate (1.0 vs 0.0) but they tie on
        fallback_rate / applied_rate (all 0 — no feedback in
        this test) so the efficiency + speed components are
        neutral 0.5 for both. Net difference is 0.35 (the
        completion-rate weight of 0.35).
        """
        group = "g-resolve"
        s_old = _make_skill(
            skill_repo, project_id, "old", ab_test_group=group
        )
        s_new = _make_skill(
            skill_repo, project_id, "new", ab_test_group=group
        )
        for _ in range(2):
            usage_repo.create(
                skill_id=s_old.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=False,
                ab_test_group=group,
            )
        for _ in range(2):
            usage_repo.create(
                skill_id=s_new.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=True,
                ab_test_group=group,
            )

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
        # Composite-based difference: completion-rate weight
        # (0.35) is the only driver since fallback/applied tie
        # and efficiency/speed are neutral (no global baseline
        # data — all records have iterations=0, duration=0).
        assert result["difference"] == pytest.approx(0.35)

    async def test_needs_more_data_below_threshold(
        self, metrics_service, skill_repo, usage_repo,
        ab_test_repo, project_id
    ):
        """needs_more_data iff comparisons>=sample but diff<min.

        Both variants have identical record shapes, so their
        composite scores are equal and the difference is 0.
        """
        group = "g-stuck"
        s_old = _make_skill(
            skill_repo, project_id, "old", ab_test_group=group
        )
        s_new = _make_skill(
            skill_repo, project_id, "new", ab_test_group=group
        )
        for success in (True, False):
            usage_repo.create(
                skill_id=s_old.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=success,
                ab_test_group=group,
            )
            usage_repo.create(
                skill_id=s_new.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=success,
                ab_test_group=group,
            )

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
        group = "g-too-few"
        s_old = _make_skill(
            skill_repo, project_id, "old", ab_test_group=group
        )
        s_new = _make_skill(
            skill_repo, project_id, "new", ab_test_group=group
        )
        for _ in range(3):
            usage_repo.create(
                skill_id=s_old.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=False,
                ab_test_group=group,
            )
        for _ in range(3):
            usage_repo.create(
                skill_id=s_new.id, project_id=project_id,
                instance_id="i", agent_id="a",
                task_succeeded=True,
                ab_test_group=group,
            )

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


# =============================================================================
# Phase 5 (2026-07-21): record_feedback forwards usefulness + improvement_note
# =============================================================================


class TestRecordFeedbackPhase5:
    """The Phase 5 ``skill_feedback`` upgrade persists two new fields
    on the usage row:

    * ``feedback_usefulness`` (1-10 quality score)
    * ``feedback_improvement`` (actionable suggestion text)

    These tests pin the contract: ``record_feedback`` must forward
    the new fields to ``update_feedback`` for every existing-record
    branch (applied=True / False / None) AND for the on-miss insert
    path. Backward compat: callers that omit the new params get
    the existing behavior unchanged.
    """

    async def test_persists_usefulness_and_improvement(
        self, metrics_service, skill_repo, project_id
    ):
        """``record_feedback(usefulness=8, improvement_note=...)``
        persists both new fields onto the latest usage record."""
        skill = _make_skill(skill_repo, project_id, "zeta-phase5")
        inst_id = "inst-phase5-1"
        metrics_service.usage_repo.create(
            skill_id=skill.id,
            project_id=project_id,
            instance_id=inst_id,
            agent_id="a",
        )

        ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            applied=True,
            note="worked",
            usefulness=8,
            improvement_note="Should mention PACKS.md location",
        )

        assert ok is True
        rec = metrics_service.usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=inst_id
        )
        assert rec is not None
        assert rec.feedback_usefulness == 8
        assert (
            rec.feedback_improvement
            == "Should mention PACKS.md location"
        )

    async def test_backward_compat_without_new_params(
        self, metrics_service, skill_repo, project_id
    ):
        """Calling ``record_feedback`` without the new params leaves
        the new columns at their default ``None`` value — no
        regression for existing callers."""
        skill = _make_skill(skill_repo, project_id, "compat-skill")
        inst_id = "inst-compat"
        metrics_service.usage_repo.create(
            skill_id=skill.id,
            project_id=project_id,
            instance_id=inst_id,
            agent_id="a",
        )

        ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            applied=True,
            note="helpful",
        )

        assert ok is True
        rec = metrics_service.usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=inst_id
        )
        assert rec is not None
        # New columns are nullable defaults — the service didn't
        # touch them, so they're None.
        assert rec.feedback_usefulness is None
        assert rec.feedback_improvement is None

    async def test_applied_false_forwards_new_params(
        self, metrics_service, skill_repo, project_id
    ):
        """The ``applied=False`` branch forwards the new fields too
        — a negative-rated skill can still carry improvement
        suggestions for the next revision."""
        skill = _make_skill(skill_repo, project_id, "neg-skill")
        inst_id = "inst-neg"
        metrics_service.usage_repo.create(
            skill_id=skill.id,
            project_id=project_id,
            instance_id=inst_id,
            agent_id="a",
        )

        ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            applied=False,
            note="not relevant",
            usefulness=2,
            improvement_note="Add troubleshooting section",
        )

        assert ok is True
        rec = metrics_service.usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=inst_id
        )
        assert rec.feedback_usefulness == 2
        assert rec.feedback_improvement == "Add troubleshooting section"

    async def test_applied_none_forwards_new_params(
        self, metrics_service, skill_repo, project_id
    ):
        """The ``applied=None`` (unsure) branch forwards the new
        fields too."""
        skill = _make_skill(skill_repo, project_id, "unsure-skill")
        inst_id = "inst-unsure"
        metrics_service.usage_repo.create(
            skill_id=skill.id,
            project_id=project_id,
            instance_id=inst_id,
            agent_id="a",
        )

        ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            applied=None,
            note="ambiguous",
            usefulness=5,
            improvement_note="Maybe clarify scope",
        )

        assert ok is True
        rec = metrics_service.usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=inst_id
        )
        assert rec.feedback_usefulness == 5
        assert rec.feedback_improvement == "Maybe clarify scope"

    async def test_on_miss_insert_forwards_new_params(
        self, metrics_service, skill_repo, project_id
    ):
        """On-miss insert path (no existing record) also persists
        the new fields. Mirrors the existing-record branch.

        Production fix: the on-miss insert path runs through
        ``update_feedback`` to stamp the feedback signals, so
        ``usefulness`` / ``improvement_note`` are forwarded there
        too.
        """
        skill = _make_skill(skill_repo, project_id, "onmiss-skill")

        ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id="inst-onmiss-phase5",
            agent_id="a",
            project_id=project_id,
            applied=True,
            note="first-time feedback",
            usefulness=7,
            improvement_note="Add timeout checklist example",
        )

        assert ok is True
        rec = metrics_service.usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id="inst-onmiss-phase5"
        )
        assert rec is not None
        assert rec.feedback_usefulness == 7
        assert (
            rec.feedback_improvement == "Add timeout checklist example"
        )
        # Sanity: the on-miss path also bumps the selection counter.
        assert skill_repo.get(skill.id).total_selections == 1
