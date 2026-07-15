"""Unit tests for Phase 4 trigger enhancements.

Phase 4 of the Skill Evolution System adds three behavioural changes
to the metrics + trigger path that this file pins down:

1. ``consecutive_failures`` default trigger now routes through
   Tier 2 analysis (``action='analyze'``) instead of direct
   Tier 3 evolution (``action='evolve_fix'``). The LLM is given a
   chance to declare the streak spurious before any rewrite.
2. :meth:`SkillEvolutionService._build_analysis_prompt` exposes
   the new metrics (``applied_rate``, ``avg_iterations``,
   ``avg_duration``) to the Tier 2 model alongside the legacy
   ``completion_rate`` / ``fallback_rate`` fields.
3. The **fallback signal** (Option C) is driven by the worker's
   ``applied`` judgment in :meth:`SkillMetricsService.record_feedback`,
   not by the bare ``task_succeeded`` outcome. A failed task is
   NOT blamed on the skill until the worker explicitly records
   ``applied=False``.

Tests are self-contained: in-memory SQLite, real repositories
(no mocks), inline fixtures, no shared conftest. Async service
methods are driven via ``asyncio.run`` so the test functions
themselves stay synchronous.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterator, Optional

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel


# =============================================================================
# SQLite engine + metadata wiring
# =============================================================================


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Enable FK enforcement on every new SQLite connection."""

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def engine() -> Iterator[Engine]:
    """In-memory SQLite engine with all skill_* tables created."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _enable_sqlite_foreign_keys(eng)
    # Import models so SQLModel.metadata.create_all picks them up.
    from daemon.repositories.skill.models import (
        Skill,
        SkillABTest,
        SkillEmbedding,
        SkillLineage,
        SkillTrigger,
        SkillUsageRecord,
    )

    _ = (Skill, SkillLineage, SkillUsageRecord, SkillTrigger,
         SkillEmbedding, SkillABTest)
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def project_id() -> str:
    """Stable project ID for all tests in this file."""
    return "test-project"


@pytest.fixture
def skill_repo(engine: Engine):
    """A :class:`SkillRepository` bound to the in-memory engine."""
    from daemon.repositories.skill.repository import SkillRepository

    return SkillRepository(engine)


@pytest.fixture
def usage_repo(engine: Engine):
    """A :class:`SkillUsageRepository` bound to the in-memory engine."""
    from daemon.repositories.skill.repository import SkillUsageRepository

    return SkillUsageRepository(engine)


@pytest.fixture
def trigger_repo(engine: Engine):
    """A :class:`SkillTriggerRepository` bound to the in-memory engine."""
    from daemon.repositories.skill.repository import SkillTriggerRepository

    return SkillTriggerRepository(engine)


# =============================================================================
# Fake collaborators (instance repo + config)
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
    """In-memory replacement for ``SQLModelInstanceRepository``."""

    def __init__(self) -> None:
        self._instances: dict[str, FakeInstance] = {}
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
def metrics_service(engine: Engine, project_id: str):
    """A :class:`SkillMetricsService` wired against real test repos."""
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

    s_repo = SkillRepository(engine)
    u_repo = SkillUsageRepository(engine)
    t_repo = SkillTriggerRepository(engine)
    ab_repo = SkillABTestRepository(engine)
    instance_repo = FakeInstanceRepo()
    config = FakeConfig()

    service = SkillMetricsService(
        usage_repo=u_repo,
        skill_repo=s_repo,
        trigger_repo=t_repo,
        ab_test_repo=ab_repo,
        config=config,
        instance_repo=instance_repo,
    )
    service.instance_repo = instance_repo  # type: ignore[assignment]
    service.skill_repo = s_repo
    service.usage_repo = u_repo
    service.trigger_repo = t_repo
    service.ab_test_repo = ab_repo
    service.config = config
    service.INJECTED_SKILLS_METADATA_KEY = INJECTED_SKILLS_METADATA_KEY  # type: ignore[attr-defined]
    return service


# =============================================================================
# Helpers
# =============================================================================


def _make_skill(skill_repo, project_id: str, name: str, **kwargs):
    """Create a skill with sensible defaults and return the row."""
    defaults = {
        "name": name,
        "description": f"desc for {name}",
        "content": f"content for {name}",
        "project_id": project_id,
    }
    defaults.update(kwargs)
    return skill_repo.create(**defaults)


def _seed_instance(
    metrics_service, instance_id: str, skill_ids: list[str]
) -> None:
    """Seed the FakeInstanceRepo so a completion hook can read skills."""
    metrics_service.instance_repo._instances[instance_id] = FakeInstance(
        instance_id,
        metadata={"last_injected_skill_ids": list(skill_ids)},
    )


# =============================================================================
# Test 1 — consecutive_failures trigger action
# =============================================================================


def test_consecutive_failures_action_is_analyze() -> None:
    """``consecutive_failures`` trigger routes through Tier 2 analysis.

    Before the Phase 4 fix, ``consecutive_failures`` jumped straight
    to Tier 3 (``action='evolve_fix'``), bypassing the cheap LLM
    sanity check. The Tier 2 pass may declare the streak spurious
    (e.g., upstream infra issue), in which case no evolution is
    needed and a rewrite would be wasted work + could regress a
    working skill.
    """
    from daemon.services.skill_trigger_seed import DEFAULT_TRIGGERS

    cf = next(
        t for t in DEFAULT_TRIGGERS
        if t["condition_type"] == "consecutive_failures"
    )
    assert cf["action"] == "analyze", (
        f"consecutive_failures must route through Tier 2 analysis, "
        f"got action={cf['action']!r}"
    )


# =============================================================================
# Test 2 — Tier 2 analysis prompt includes the new metrics
# =============================================================================


def test_analysis_prompt_includes_new_metrics() -> None:
    """_build_analysis_prompt must surface the new metric fields.

    The cheap Tier 2 model needs ``applied_rate``, ``avg_iterations``
    and ``avg_duration`` to make an informed should_evolve decision.
    The legacy ``completion_rate`` and ``fallback_rate`` must still
    be present for backward compatibility with the model's prior
    training context.
    """
    from types import SimpleNamespace

    from daemon.services.skill_evolution_service import SkillEvolutionService

    skill = SimpleNamespace(
        name="sample-skill",
        description="a sample skill",
        content="skill content here",
    )
    stats = {
        "total": 10,
        "selected": 10,
        "applied": 5,
        "completions": 4,
        "fallbacks": 3,
        "avg_iterations": 3.0,
        "avg_duration": 120.0,
        "completion_rate": 0.4,
        "applied_rate": 0.5,
        "fallback_rate": 0.3,
        "consecutive_failures": 0,
    }
    prompt = SkillEvolutionService._build_analysis_prompt(
        skill, stats, [], "test reason"
    )

    # New metrics (Phase 4 additions)
    assert "applied_rate" in prompt, (
        "applied_rate missing from Tier 2 prompt; cheap model needs "
        "it to distinguish 'rarely applied' from 'broken'"
    )
    assert "avg_iterations" in prompt, (
        "avg_iterations missing from Tier 2 prompt"
    )
    assert "avg_duration" in prompt, (
        "avg_duration missing from Tier 2 prompt"
    )

    # Legacy metrics still present (don't regress old behaviour)
    assert "completion_rate" in prompt
    assert "fallback_rate" in prompt


# =============================================================================
# Tests 3-7 — Fallback behaviour (Option C)
# =============================================================================


def test_fallback_applied_false_sets_fallback_true(
    metrics_service, skill_repo, usage_repo, project_id
) -> None:
    """``record_feedback(applied=False)`` stamps ``fallback=True`` and
    bumps ``total_fallbacks``.

    Under Option C, the worker's ``applied=False`` judgment is the
    authoritative fallback signal — the skill was injected, the
    worker tried to use it, and decided it didn't help. Both the
    usage row and the denormalized skill counter must reflect that.
    """
    skill = _make_skill(skill_repo, project_id, "fb-false")
    inst_id = "i-1"
    _seed_instance(metrics_service, inst_id, [skill.id])

    asyncio.run(
        metrics_service.record_task_completion(
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            task_succeeded=True,
            iterations=1,
            duration_seconds=1,
        )
    )

    ok = asyncio.run(
        metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            applied=False,
            note="not helpful",
        )
    )
    assert ok is True, "record_feedback should succeed when a usage record exists"

    rec = usage_repo.get_latest_for_skill_instance(skill.id, inst_id)
    assert rec is not None, "usage record should have been created by record_task_completion"
    assert rec.fallback is True, (
        "applied=False must stamp fallback=True on the usage record"
    )

    s = skill_repo.get(skill.id)
    assert s.total_fallbacks == 1, (
        f"total_fallbacks should increment to 1 on first applied=False, "
        f"got {s.total_fallbacks}"
    )


def test_fallback_applied_true_sets_fallback_false(
    metrics_service, skill_repo, usage_repo, project_id
) -> None:
    """``record_feedback(applied=True)`` stamps ``fallback=False`` on the
    usage record (the default before feedback is recorded).
    """
    skill = _make_skill(skill_repo, project_id, "fb-true")
    inst_id = "i-2"
    _seed_instance(metrics_service, inst_id, [skill.id])

    asyncio.run(
        metrics_service.record_task_completion(
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            task_succeeded=True,
            iterations=1,
            duration_seconds=1,
        )
    )

    ok = asyncio.run(
        metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            applied=True,
            note="",
        )
    )
    assert ok is True

    rec = usage_repo.get_latest_for_skill_instance(skill.id, inst_id)
    assert rec is not None
    assert rec.fallback is False, (
        "applied=True must stamp fallback=False on the usage record"
    )


def test_fallback_reversal_decrements_counter(
    metrics_service, skill_repo, project_id
) -> None:
    """Reversing a fallback (False then True) decrements ``total_fallbacks``.

    The counter is a count of *currently-fallback* records, not a
    cumulative tally of feedback events. So a second feedback call
    that overrides an earlier ``applied=False`` must decrement
    rather than leave the counter stale at +1.
    """
    skill = _make_skill(skill_repo, project_id, "fb-reverse")
    inst_id = "i-3"
    _seed_instance(metrics_service, inst_id, [skill.id])

    asyncio.run(
        metrics_service.record_task_completion(
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            task_succeeded=True,
            iterations=1,
            duration_seconds=1,
        )
    )

    # First: mark as not applied → fallback=True, counter +1
    asyncio.run(
        metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            applied=False,
            note="",
        )
    )
    assert skill_repo.get(skill.id).total_fallbacks == 1

    # Second: mark as applied → fallback=False, counter -1
    asyncio.run(
        metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            applied=True,
            note="",
        )
    )
    assert skill_repo.get(skill.id).total_fallbacks == 0, (
        "Reversing fallback (False→True feedback sequence) must "
        "decrement total_fallbacks back to 0"
    )


def test_fallback_applied_none_no_change(
    metrics_service, skill_repo, project_id
) -> None:
    """``record_feedback(applied=None)`` is a passive signal — neither
    the ``fallback`` flag nor the ``total_fallbacks`` counter change.
    """
    skill = _make_skill(skill_repo, project_id, "fb-none")
    inst_id = "i-4"
    _seed_instance(metrics_service, inst_id, [skill.id])

    asyncio.run(
        metrics_service.record_task_completion(
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            task_succeeded=True,
            iterations=1,
            duration_seconds=1,
        )
    )

    initial_counter = skill_repo.get(skill.id).total_fallbacks
    ok = asyncio.run(
        metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            applied=None,
            note="uncertain",
        )
    )
    assert ok is True, (
        "applied=None should still succeed (record found and updated)"
    )

    after_counter = skill_repo.get(skill.id).total_fallbacks
    assert initial_counter == after_counter, (
        "applied=None must not bump total_fallbacks "
        f"(before={initial_counter}, after={after_counter})"
    )


def test_fallback_hard_task_not_blamed(
    metrics_service, skill_repo, usage_repo, project_id
) -> None:
    """Option C: a hard task failure is NOT blamed on the skill.

    The skill may have been perfectly relevant; the task was just
    hard. Until the worker explicitly says ``applied=False``, the
    ``fallback`` flag must remain False. Otherwise the
    ``high_fallback_rate`` trigger would fire on benign failures
    and rewrite skills that are not actually at fault.
    """
    skill = _make_skill(skill_repo, project_id, "fb-hard-task")
    inst_id = "i-5"
    _seed_instance(metrics_service, inst_id, [skill.id])

    asyncio.run(
        metrics_service.record_task_completion(
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            task_succeeded=False,
            iterations=1,
            duration_seconds=1,
        )
    )

    rec = usage_repo.get_latest_for_skill_instance(skill.id, inst_id)
    assert rec is not None
    assert rec.fallback is False, (
        "Option C: a hard-task failure should not be blamed on the "
        "skill until the worker explicitly records applied=False"
    )


def test_fallback_multiple_failures_not_blamed(
    metrics_service, skill_repo, usage_repo, project_id
) -> None:
    """Option C: Even on 2nd+ consecutive failure, fallback stays False until worker feedback."""
    # Skill arrives with consecutive_failures=2 — i.e., the system has
    # already recorded two prior failures. A *third* failing task must
    # STILL not be auto-blamed on the skill; the worker has to say so
    # explicitly via record_feedback(applied=False).
    skill = _make_skill(
        skill_repo, project_id, "fb-multi-fail", consecutive_failures=2
    )
    inst_id = "i-multi-fail"
    _seed_instance(metrics_service, inst_id, [skill.id])

    initial_fallbacks = skill_repo.get(skill.id).total_fallbacks

    asyncio.run(
        metrics_service.record_task_completion(
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            task_succeeded=False,
            iterations=1,
            duration_seconds=1,
        )
    )

    rec = usage_repo.get_latest_for_skill_instance(skill.id, inst_id)
    assert rec is not None
    assert rec.fallback is False, (
        "Option C: a 2nd+ consecutive failure must not be blamed on the "
        "skill until the worker explicitly records applied=False"
    )

    after_fallbacks = skill_repo.get(skill.id).total_fallbacks
    assert after_fallbacks == initial_fallbacks, (
        "record_task_completion must not bump total_fallbacks when "
        "task_succeeded=False (Option C defers blame to worker feedback); "
        f"before={initial_fallbacks}, after={after_fallbacks}"
    )


# =============================================================================
# Test 8 — Counter reads correctly (trigger path)
# =============================================================================


def test_high_fallback_rate_trigger_reads_counter(
    metrics_service, skill_repo, project_id
) -> None:
    """After ``applied=False`` feedback, ``total_fallbacks`` is 1 so
    the ``high_fallback_rate`` trigger engine has real signal to
    consume on the next scan.

    This is the end-to-end read path: counter → trigger engine
    threshold check. Without the counter bump, the trigger would
    see stale data and either over- or under-fire.
    """
    skill = _make_skill(skill_repo, project_id, "trigger-counter")
    inst_id = "i-6"
    _seed_instance(metrics_service, inst_id, [skill.id])

    asyncio.run(
        metrics_service.record_task_completion(
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            task_succeeded=True,
            iterations=1,
            duration_seconds=1,
        )
    )
    asyncio.run(
        metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=inst_id,
            agent_id="a",
            project_id=project_id,
            applied=False,
            note="",
        )
    )

    s = skill_repo.get(skill.id)
    assert s.total_fallbacks == 1, (
        f"expected total_fallbacks == 1 after one applied=False "
        f"feedback, got {s.total_fallbacks}"
    )