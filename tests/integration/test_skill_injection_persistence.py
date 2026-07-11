"""Regression tests for skill-injection metadata persistence.

Bug
---
``SkillInjectionService.track_injection()`` wrote injected skill IDs
to an in-memory dict only. The production caller in
``daemon/services/instance_messaging.py`` never persisted them to
``instance_metadata["last_injected_skill_ids"]``. The Phase 4 metrics
service reads that metadata key at task completion; without the write
it saw an empty list, no-op'd, and no ``SkillUsageRecord`` was created.

A later ``record_feedback`` call could therefore not find a usage row
to stamp and the skill row's ``total_applied`` counter never moved.

Fix
---
``instance_messaging.py`` now calls
``instance_repo.set_metadata(instance_id, INJECTED_SKILLS_METADATA_KEY,
injected_skill_ids)`` right after ``track_injection(...)``.

These tests cover the full production path:
``inject_skills -> track_injection -> set_metadata -> task_completion
-> feedback``. Tests 1-4 verify the happy path; Test 5 reproduces the
pre-fix state (no persistence call) and asserts that the downstream
metrics path correctly no-ops, mirroring the pre-fix bug.

Test isolation
--------------
Each test builds its own in-memory SQLite engine plus a fresh set of
repositories and services. No pytest fixtures are used so tests can be
run in any order without cross-test state leakage.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine


# =============================================================================
# Engine + repository stack — self-contained per test
# =============================================================================


def _build_engine() -> Engine:
    """Build a fresh in-memory SQLite engine with all required tables.

    The instance persistence path needs both the six skill tables and
    the ``instances`` / ``instance_hierarchy`` tables. Importing
    :class:`SQLModelInstanceRepository` pulls in :mod:`task`,
    :mod:`event`, :mod:`message_queue`, and :mod:`job_queue` models
    via transitive imports — those get registered on
    ``SQLModel.metadata`` too, so
    :func:`SQLModel.metadata.create_all` provisions them all in one
    pass with correct FK ordering.

    Returns:
        A configured :class:`Engine` ready for repository use.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    # Import models so SQLModel.metadata is populated. The Instance
    # models must be imported for the ``instances`` table to exist.
    from daemon.repositories.instance.models import (
        Instance,
        InstanceHierarchy,
    )
    from daemon.repositories.skill.models import (
        Skill,
        SkillABTest,
        SkillEmbedding,
        SkillLineage,
        SkillTrigger,
        SkillUsageRecord,
    )

    _ = (Instance, InstanceHierarchy, Skill, SkillUsageRecord,
         SkillLineage, SkillTrigger, SkillEmbedding, SkillABTest)
    SQLModel.metadata.create_all(engine)
    return engine


# =============================================================================
# Helpers — minimal config / fake deps
# =============================================================================


class FakeConfig:
    """Minimal :class:`SkillEvolutionConfig` stub.

    Only the attributes actually read by the services under test are
    populated; values mirror the production defaults so the test runs
    behave like production code.
    """

    def __init__(self) -> None:
        self.max_inject_skills = 5
        self.ab_sample_size = 10
        self.ab_min_difference = 0.15
        self.max_extensions = 3
        self.capture_min_iterations = 5
        self.capture_min_duration_seconds = 60


def _build_services() -> SimpleNamespace:
    """Build the full real-services integration stack for one test.

    Returns a :class:`SimpleNamespace` exposing:

    * ``engine`` — the in-memory SQLite engine.
    * ``skill_repo`` / ``usage_repo`` / ``trigger_repo`` /
      ``embedding_repo`` / ``ab_test_repo`` — Phase 1 repos bound to
      ``engine``.
    * ``instance_repo`` — :class:`SQLModelInstanceRepository` (the
      **real** repo, not a fake — required to exercise
      ``set_metadata`` against an actual SQLite-backed JSON column).
    * ``search_service`` — a mock :class:`SkillSearchService` whose
      ``search`` method is an :class:`AsyncMock`. Tests configure the
      return value via :func:`_set_search_results`.
    * ``config`` — :class:`FakeConfig`.
    * ``injection_service`` — real :class:`SkillInjectionService`
      built against the real repos + mocked search service.
    * ``metrics_service`` — real :class:`SkillMetricsService` built
      against the real repos + real ``instance_repo``.
    """
    from daemon.repositories.instance.repository import (
        SQLModelInstanceRepository,
    )
    from daemon.repositories.skill.repository import (
        SkillABTestRepository,
        SkillEmbeddingRepository,
        SkillLineageRepository,
        SkillRepository,
        SkillTriggerRepository,
        SkillUsageRepository,
    )
    from daemon.services.skill_injection_service import SkillInjectionService
    from daemon.services.skill_metrics_service import (
        INJECTED_SKILLS_METADATA_KEY,
        SkillMetricsService,
    )

    engine = _build_engine()
    skill_repo = SkillRepository(engine)
    lineage_repo = SkillLineageRepository(engine)
    usage_repo = SkillUsageRepository(engine)
    trigger_repo = SkillTriggerRepository(engine)
    embedding_repo = SkillEmbeddingRepository(engine)
    ab_test_repo = SkillABTestRepository(engine)

    instance_repo = SQLModelInstanceRepository(engine)

    search_service = MagicMock()
    search_service.search = AsyncMock(
        return_value={"injected": [], "low_match": []}
    )

    config = FakeConfig()
    injection_service = SkillInjectionService(
        search_service=search_service,
        config=config,
        ab_test_repo=ab_test_repo,
        skill_repo=skill_repo,
    )
    metrics_service = SkillMetricsService(
        usage_repo=usage_repo,
        skill_repo=skill_repo,
        trigger_repo=trigger_repo,
        ab_test_repo=ab_test_repo,
        config=config,
        instance_repo=instance_repo,
        evolution_service=None,
        agent_id_resolver=None,
    )

    return SimpleNamespace(
        engine=engine,
        skill_repo=skill_repo,
        lineage_repo=lineage_repo,
        usage_repo=usage_repo,
        trigger_repo=trigger_repo,
        embedding_repo=embedding_repo,
        ab_test_repo=ab_test_repo,
        instance_repo=instance_repo,
        search_service=search_service,
        config=config,
        injection_service=injection_service,
        metrics_service=metrics_service,
        metadata_key=INJECTED_SKILLS_METADATA_KEY,
    )


def _create_instance(
    instance_repo: Any,
    instance_id: str,
    metadata: Optional[dict[str, Any]] = None,
    project_id: Optional[str] = "test-project",
    agent_id: str = "agent-x",
) -> None:
    """Create an instance row with optional metadata via the real repo."""
    instance_repo.create(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir="/agents/agent-x",
        metadata=metadata,
        project_id=project_id,
    )


def _create_skill(
    skill_repo: Any,
    name: str,
    project_id: str = "test-project",
) -> Any:
    """Create a skill row directly via :meth:`SkillRepository.create`."""
    return skill_repo.create(
        name=name,
        description=f"desc for {name}",
        content=f"content for {name}",
        project_id=project_id,
    )


def _set_search_results(
    services: SimpleNamespace,
    injected_skills: list[Any],
    low_match: Optional[list[dict[str, Any]]] = None,
) -> None:
    """Configure the mocked search service to return the given skills.

    Wraps each skill into the ``{"skill": ..., "score": ...}`` shape
    the injection service expects from
    :meth:`SkillSearchService.search`.
    """
    services.search_service.search = AsyncMock(
        return_value={
            "injected": [
                {"skill": skill, "score": 0.95} for skill in injected_skills
            ],
            "low_match": low_match or [],
        }
    )


# =============================================================================
# Test 1 — Injection -> track_injection -> set_metadata persists IDs
# =============================================================================


async def test_injection_persists_skill_ids_to_metadata():
    """After ``track_injection`` + ``set_metadata``, instance metadata holds the IDs.

    Mirrors the production fix in ``instance_messaging.py``: the
    caller invokes ``track_injection(...)`` (in-memory tracking) and
    immediately follows up with
    ``instance_repo.set_metadata(instance_id, key, ids)`` so the
    metrics service at task completion can find the IDs.

    Pre-fix: ``set_metadata`` was never called, so the metrics
    service saw an empty metadata key and silently no-op'd.
    """
    services = _build_services()
    skill_a = _create_skill(services.skill_repo, "skill-a")
    skill_b = _create_skill(services.skill_repo, "skill-b")
    _set_search_results(services, [skill_a, skill_b])

    instance_id = "inst-persist-1"
    _create_instance(
        services.instance_repo,
        instance_id,
        metadata={"skill_injection": True},
    )

    # Step 1: run the injection — same call site instance_messaging.py uses.
    text, injected_skill_ids = await services.injection_service.inject_skills(
        user_message="use both skills",
        project_id="test-project",
        instance_id=instance_id,
        message_id="msg-1",
    )

    assert text is not None
    assert "[System Inject]" in text
    assert sorted(injected_skill_ids) == sorted([skill_a.id, skill_b.id])

    # Step 2: in-memory tracking (still in place post-fix).
    services.injection_service.track_injection(
        instance_id=instance_id,
        message_id="msg-1",
        skill_ids=injected_skill_ids,
    )
    # Sanity — the in-memory dict was populated.
    cached = services.injection_service.get_injected_skill_ids(
        instance_id, "msg-1"
    )
    assert cached == injected_skill_ids

    # Step 3: persistence — what instance_messaging.py NOW does after
    # track_injection. This is the production-line fix.
    services.instance_repo.set_metadata(
        instance_id,
        services.metadata_key,
        list(injected_skill_ids),
    )

    # Read back via the real repo — assert the IDs landed in metadata.
    inst = services.instance_repo.get(instance_id)
    assert inst is not None
    persisted = inst.instance_metadata.get(services.metadata_key)
    assert persisted is not None
    assert sorted(persisted) == sorted([skill_a.id, skill_b.id])


# =============================================================================
# Test 2 — Multiple injections dedupe via read-merge-write
# =============================================================================


async def test_persistence_merge_dedupes_multiple_injections():
    """Repeated injection+set_metadata pairs accumulate IDs without duplicates.

    An instance can receive multiple tasks within its lifetime, and
    each task may inject a different overlap of skills. The persist
    step must dedupe across tasks so the metrics service doesn't
    double-count skills that appeared in earlier injection batches.
    """
    services = _build_services()
    skill_a = _create_skill(services.skill_repo, "skill-a")
    skill_b = _create_skill(services.skill_repo, "skill-b")
    skill_c = _create_skill(services.skill_repo, "skill-c")

    instance_id = "inst-persist-merge"
    _create_instance(
        services.instance_repo,
        instance_id,
        metadata={"skill_injection": True},
    )

    # ---- First injection: skill-a + skill-b --------------------
    _set_search_results(services, [skill_a, skill_b])
    _, ids_1 = await services.injection_service.inject_skills(
        user_message="use a and b",
        project_id="test-project",
        instance_id=instance_id,
        message_id="msg-1",
    )
    services.injection_service.track_injection(instance_id, "msg-1", ids_1)

    # Persistence call #1 — the fix overwrites with the first batch.
    services.instance_repo.set_metadata(
        instance_id, services.metadata_key, list(ids_1)
    )

    inst = services.instance_repo.get(instance_id)
    assert inst.instance_metadata.get(services.metadata_key) == list(ids_1)

    # ---- Second injection: skill-b + skill-c (overlap on b) ----
    _set_search_results(services, [skill_b, skill_c])
    _, ids_2 = await services.injection_service.inject_skills(
        user_message="use b and c",
        project_id="test-project",
        instance_id=instance_id,
        message_id="msg-2",
    )
    services.injection_service.track_injection(instance_id, "msg-2", ids_2)

    # Persistence call #2 — read-merge-write: union with existing IDs,
    # preserving the first-seen order. This mirrors what production
    # needs to avoid double-counting when an instance reuses injected
    # skills across tasks.
    inst = services.instance_repo.get(instance_id)
    existing = list(
        inst.instance_metadata.get(services.metadata_key) or []
    )
    # ``dict.fromkeys`` preserves insertion order while removing dupes.
    merged = list(dict.fromkeys(existing + ids_2))
    services.instance_repo.set_metadata(
        instance_id, services.metadata_key, merged
    )

    # Final state: a, b, c — b appears once, order preserved.
    inst = services.instance_repo.get(instance_id)
    final = inst.instance_metadata.get(services.metadata_key)
    assert final == [skill_a.id, skill_b.id, skill_c.id]


# =============================================================================
# Test 3 — record_task_completion finds the persisted IDs
# =============================================================================


async def test_record_task_completion_finds_persisted_ids():
    """After persistence, ``record_task_completion`` reads the IDs and writes usage rows.

    This is the Phase 4 happy path: the metrics service reads
    ``last_injected_skill_ids`` from instance metadata (which the
    persistence fix populates), writes one :class:`SkillUsageRecord`
    per skill, bumps denormalized counters on each skill row, and
    clears the metadata key.
    """
    services = _build_services()
    skill_a = _create_skill(services.skill_repo, "skill-a")
    skill_b = _create_skill(services.skill_repo, "skill-b")

    instance_id = "inst-metrics"
    _create_instance(
        services.instance_repo,
        instance_id,
        metadata={"skill_injection": True},
    )
    # Simulate the production fix having run.
    services.instance_repo.set_metadata(
        instance_id,
        services.metadata_key,
        [skill_a.id, skill_b.id],
    )

    inserted = await services.metrics_service.record_task_completion(
        instance_id=instance_id,
        agent_id="agent-x",
        project_id="test-project",
        task_succeeded=True,
        iterations=3,
        duration_seconds=42,
    )

    # Both skills produced a usage record.
    assert inserted == 2

    records_a, total_a = services.usage_repo.get_by_skill(skill_a.id)
    records_b, total_b = services.usage_repo.get_by_skill(skill_b.id)
    assert total_a == 1
    assert total_b == 1
    assert records_a[0].instance_id == instance_id
    assert records_b[0].instance_id == instance_id
    assert records_a[0].task_succeeded is True
    assert records_a[0].selected is True
    # ``applied`` defaults to False until feedback arrives.
    assert records_a[0].applied is False

    # Denormalized counters bumped on the skill rows.
    refreshed_a = services.skill_repo.get(skill_a.id)
    refreshed_b = services.skill_repo.get(skill_b.id)
    assert refreshed_a.total_selections == 1
    assert refreshed_a.total_completions == 1
    assert refreshed_b.total_selections == 1
    assert refreshed_b.total_completions == 1
    assert refreshed_a.last_used_at is not None
    assert refreshed_b.last_used_at is not None

    # Metadata cleared after recording — the next task starts clean.
    inst = services.instance_repo.get(instance_id)
    assert services.metadata_key not in (inst.instance_metadata or {})


# =============================================================================
# Test 4 — record_feedback stamps the record after persistence
# =============================================================================


async def test_feedback_finds_usage_record_after_persistence():
    """After persistence + task completion, ``record_feedback`` stamps the latest record.

    Verifies the Phase 4 ``skill_feedback`` tool backend can find a
    usage record to stamp when the persistence fix is in place.
    Without the fix (Test 5) no record exists; with it, this test
    confirms the record is located by ``(skill_id, instance_id)`` and
    the ``total_applied`` counter is bumped.
    """
    services = _build_services()
    skill = _create_skill(services.skill_repo, "feedback-skill")

    instance_id = "inst-feedback"
    _create_instance(
        services.instance_repo,
        instance_id,
        metadata={"skill_injection": True},
    )
    # Persistence — simulating the fix.
    services.instance_repo.set_metadata(
        instance_id, services.metadata_key, [skill.id]
    )

    # Step 1: record_task_completion writes the usage record.
    inserted = await services.metrics_service.record_task_completion(
        instance_id=instance_id,
        agent_id="agent-x",
        project_id="test-project",
        task_succeeded=True,
        iterations=1,
        duration_seconds=1,
    )
    assert inserted == 1

    # Pre-feedback state: counters at 0, record exists with feedback_applied=None.
    assert services.skill_repo.get(skill.id).total_applied == 0
    latest = services.usage_repo.get_latest_for_skill_instance(
        skill_id=skill.id, instance_id=instance_id
    )
    assert latest is not None
    assert latest.feedback_applied is None

    # Step 2: record_feedback stamps the record and bumps total_applied.
    ok = await services.metrics_service.record_feedback(
        skill_id=skill.id,
        instance_id=instance_id,
        agent_id="agent-x",
        project_id="test-project",
        applied=True,
        note="helpful",
    )
    assert ok is True

    latest = services.usage_repo.get_latest_for_skill_instance(
        skill_id=skill.id, instance_id=instance_id
    )
    assert latest is not None
    assert latest.feedback_applied is True
    assert latest.feedback_note == "helpful"
    assert services.skill_repo.get(skill.id).total_applied == 1


# =============================================================================
# Test 5 — The bug: feedback fails when persistence was skipped
# =============================================================================


async def test_feedback_fails_without_persistence():
    """Pre-fix state: no ``set_metadata`` call → no usage record → feedback is a no-op.

    This is the regression scenario the production fix prevents. The
    instance is created (and ``skill_injection`` could be enabled),
    but the ``last_injected_skill_ids`` key is NEVER written because
    ``instance_messaging.py`` only called ``track_injection`` (in-memory).

    Expected downstream behavior (matches pre-fix):

    * ``record_task_completion`` returns ``0`` — no IDs in metadata,
      so no usage records are inserted and no counters are bumped.
    * ``record_feedback`` takes the "no usage record" branch and
      returns ``False`` (the helper returns ``None`` from its inner
      ``_do_feedback`` because ``get_latest_for_skill_instance``
      returns ``None``; the outer coroutine then returns ``False``).
    * No usage record is created as a side-effect of feedback.
    * The skill's ``total_applied`` counter stays at ``0``.
    """
    services = _build_services()
    skill = _create_skill(services.skill_repo, "orphan-skill")

    instance_id = "inst-bug"
    # NOTE: skill_injection is intentionally NOT in the metadata either,
    # to faithfully reproduce the pre-fix state where the injection
    # service ran but its output never reached instance metadata.
    _create_instance(services.instance_repo, instance_id)

    # Verify the metadata key is absent (pre-fix condition).
    inst = services.instance_repo.get(instance_id)
    assert services.metadata_key not in (inst.instance_metadata or {})

    # ---- Step 1: task completion sees no injected IDs ---------------
    inserted = await services.metrics_service.record_task_completion(
        instance_id=instance_id,
        agent_id="agent-x",
        project_id="test-project",
        task_succeeded=True,
        iterations=1,
        duration_seconds=1,
    )
    assert inserted == 0

    # No usage records exist for this skill.
    records, total = services.usage_repo.get_by_skill(skill.id)
    assert total == 0

    # Counters were not bumped by the no-op completion.
    refreshed = services.skill_repo.get(skill.id)
    assert refreshed.total_selections == 0
    assert refreshed.total_completions == 0
    assert refreshed.total_applied == 0

    # ---- Step 2: feedback takes the "no record" branch ---------------
    ok = await services.metrics_service.record_feedback(
        skill_id=skill.id,
        instance_id=instance_id,
        agent_id="agent-x",
        project_id="test-project",
        applied=True,
        note="lost feedback",
    )
    # record_feedback returns False when no usage record exists.
    assert ok is False

    # Usage record was NOT created as a side-effect of feedback.
    records_after, total_after = services.usage_repo.get_by_skill(skill.id)
    assert total_after == 0
    latest = services.usage_repo.get_latest_for_skill_instance(
        skill_id=skill.id, instance_id=instance_id
    )
    assert latest is None

    # total_applied stays at 0 — feedback had nothing to bump.
    refreshed = services.skill_repo.get(skill.id)
    assert refreshed.total_applied == 0