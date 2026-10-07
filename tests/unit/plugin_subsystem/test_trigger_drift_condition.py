"""Tests for the ``drift_event_observed`` trigger condition (slice ⑥).

The ③ probe doc's Path-A wire-up, verified end-to-end against the
real engine + real DB (in-memory SQLite): candidate resolution (the
unresolved drift events ARE the candidates), the condition body
(min_divergence_id floor, max_age_days freshness, plugin filter),
the reason text (verbatim payload fields), the flagged-entry shape
(EMPTY skill_id + the payload under ``drift_event``), inertness
without a wired store, and the seed-catalogue entry.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from daemon.plugin_subsystem import build_drift_event_payload
from daemon.plugin_subsystem.drift_event_publisher import (
    DriftEventRepository,
)
from daemon.services.skill_trigger_engine import SkillTriggerEngine
from daemon.services.skill_trigger_seed import DEFAULT_TRIGGERS


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures — mirror tests/services/test_skill_trigger_engine.py wiring
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def drift_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


class _StubMetricsService:
    """Enough of SkillMetricsService for the non-drift code paths
    (the skill re-fetch reads ``.skill_repo``). Drift candidates
    never reach the skill re-fetch (early branch)."""

    def __init__(self, engine) -> None:
        from daemon.repositories.skill.repository import SkillRepository

        self.skill_repo = SkillRepository(engine)

    async def get_skill_stats(self, skill_id: str) -> dict:
        # The stub's skills carry their own counters; the flagged
        # entry's stats payload is irrelevant to these tests.
        return {}


def _make_engine(drift_engine, *, with_drift: bool = True) -> SkillTriggerEngine:
    from daemon.repositories.skill.repository import (
        SkillTriggerRepository,
        SkillUsageRepository,
    )

    kwargs: dict = {}
    if with_drift:
        kwargs["drift_event_repo"] = DriftEventRepository(drift_engine)
    return SkillTriggerEngine(
        trigger_repo=SkillTriggerRepository(drift_engine),
        metrics_service=_StubMetricsService(drift_engine),
        usage_repo=SkillUsageRepository(drift_engine),
        **kwargs,
    )


@pytest.fixture
def trigger_engine(drift_engine):
    return _make_engine(drift_engine, with_drift=True)


@pytest.fixture
def bare_engine(drift_engine):
    """An engine with NO drift store wired (the inert default)."""
    return _make_engine(drift_engine, with_drift=False)


def _seed_event(
    repo: DriftEventRepository,
    *,
    divergence_id: int = 5,
    plugin: str = "opendesign",
    age_days: int = 0,
) -> None:
    payload = build_drift_event_payload(
        plugin,
        "snapshot_with_drift_alarm",
        {
            "id": divergence_id,
            "files": ["prompts/x.ts"],
            "delta": "+1/~0/-0",
            "rationale": "test divergence",
            "pinning_test": "tests/unit/plugin_subsystem/test_x.py::test_y",
        },
        "open-design-v0.24.1",
        now=datetime.now(timezone.utc) - timedelta(days=age_days),
    )
    repo.create_from_payload(payload)


def _drift_trigger(engine: SkillTriggerEngine, **condition: Any) -> None:
    engine.trigger_repo.create(
        name="drift_event_observed",
        condition_type="drift_event_observed",
        condition_json=condition
        or {"plugin": "opendesign", "min_divergence_id": 1, "max_age_days": 14},
        action="analyze",
        project_id=None,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Candidate resolution + condition body
# ══════════════════════════════════════════════════════════════════════════════


class TestDriftEventObservedCondition:
    @pytest.mark.asyncio
    async def test_fires_on_fresh_unresolved_event(self, trigger_engine, drift_engine):
        repo = DriftEventRepository(drift_engine)
        _seed_event(repo, divergence_id=5)
        _drift_trigger(trigger_engine)
        flagged = await trigger_engine.evaluate_all()
        assert len(flagged) == 1
        entry = flagged[0]
        # EMPTY skill_id: the skill-centric Tier-2 consumer skips it
        assert entry["skill_id"] == ""
        assert entry["trigger_action"] == "analyze"
        # the verbatim payload rides the flag
        assert entry["drift_event"]["divergence_id"] == 5
        assert entry["drift_event"]["plugin"] == "opendesign"
        assert entry["drift_event"]["class"] == "snapshot_with_drift_alarm"
        # the reason uses the verbatim payload fields (probe doc)
        assert "divergence_id=5" in entry["reason"]
        assert "plugin=opendesign" in entry["reason"]

    @pytest.mark.asyncio
    async def test_silent_when_no_events(self, trigger_engine):
        _drift_trigger(trigger_engine)
        assert await trigger_engine.evaluate_all() == []

    @pytest.mark.asyncio
    async def test_resolved_event_does_not_fire(self, trigger_engine, drift_engine):
        repo = DriftEventRepository(drift_engine)
        _seed_event(repo, divergence_id=5)
        assert repo.resolve("opendesign", 5) is True
        _drift_trigger(trigger_engine)
        assert await trigger_engine.evaluate_all() == []

    @pytest.mark.asyncio
    async def test_stale_event_beyond_max_age_does_not_fire(self, trigger_engine, drift_engine):
        repo = DriftEventRepository(drift_engine)
        _seed_event(repo, divergence_id=5, age_days=20)
        _drift_trigger(trigger_engine, plugin="opendesign", min_divergence_id=1, max_age_days=14)
        assert await trigger_engine.evaluate_all() == []

    @pytest.mark.asyncio
    async def test_event_within_max_age_fires(self, trigger_engine, drift_engine):
        repo = DriftEventRepository(drift_engine)
        _seed_event(repo, divergence_id=5, age_days=13)
        _drift_trigger(trigger_engine, plugin="opendesign", min_divergence_id=1, max_age_days=14)
        assert len(await trigger_engine.evaluate_all()) == 1

    @pytest.mark.asyncio
    async def test_min_divergence_id_floor(self, trigger_engine, drift_engine):
        repo = DriftEventRepository(drift_engine)
        _seed_event(repo, divergence_id=2)
        _drift_trigger(trigger_engine, plugin="opendesign", min_divergence_id=3, max_age_days=14)
        assert await trigger_engine.evaluate_all() == []

    @pytest.mark.asyncio
    async def test_plugin_filter(self, trigger_engine, drift_engine):
        repo = DriftEventRepository(drift_engine)
        _seed_event(repo, divergence_id=5, plugin="other-plugin")
        _drift_trigger(trigger_engine, plugin="opendesign", min_divergence_id=1, max_age_days=14)
        assert await trigger_engine.evaluate_all() == []

    @pytest.mark.asyncio
    async def test_inert_without_drift_store(self, bare_engine):
        _drift_trigger(bare_engine)
        # No drift repo wired → no candidates → never fires, never raises
        assert await bare_engine.evaluate_all() == []

    @pytest.mark.asyncio
    async def test_skill_triggers_unaffected_by_drift_store_param(self, trigger_engine, drift_engine):
        # The additive ctor param must not disturb the six pre-existing
        # conditions: a plain consecutive_failures trigger still works.
        from daemon.repositories.skill.repository import SkillRepository

        skill_repo = SkillRepository(drift_engine)
        skill_repo.create(
            name="sk",
            description="d",
            content="c",
            project_id=None,
            consecutive_failures=5,
        )
        trigger_engine.trigger_repo.create(
            name="cf",
            condition_type="consecutive_failures",
            condition_json={"threshold": 3},
            action="analyze",
            project_id=None,
        )
        flagged = await trigger_engine.evaluate_all()
        assert len(flagged) == 1
        assert flagged[0]["skill_name"] == "sk"


# ══════════════════════════════════════════════════════════════════════════════
# Seed catalogue
# ══════════════════════════════════════════════════════════════════════════════


class TestSeedCatalogueEntry:
    def test_drift_event_observed_in_default_triggers(self):
        entries = [t for t in DEFAULT_TRIGGERS if t["condition_type"] == "drift_event_observed"]
        assert len(entries) == 1
        entry = entries[0]
        # The ③ probe doc's Path-A example shape
        assert entry["condition_json"]["plugin"] == "opendesign"
        assert entry["condition_json"]["min_divergence_id"] == 1
        assert "max_age_days" in entry["condition_json"]
        assert entry["action"] == "analyze"
        # per-plugin for v1: ONE row naming the plugin (the future
        # cross-plugin "*" sweep is deferred and documented, not seeded)
        assert entry["condition_json"]["plugin"] != "*"

    def test_seed_idempotent_insert(self, drift_engine):
        import asyncio

        from daemon.repositories.skill.repository import SkillTriggerRepository
        from daemon.services.skill_trigger_seed import seed_default_triggers

        repo = SkillTriggerRepository(drift_engine)
        first = asyncio.run(seed_default_triggers(repo, None))
        second = asyncio.run(seed_default_triggers(repo, None))
        rows = repo.list(project_id=None, enabled_only=False)
        names = [r.name for r in rows]
        assert first > 0 and second == 0  # second pass inserts nothing
        assert names.count("drift_event_observed") == 1
