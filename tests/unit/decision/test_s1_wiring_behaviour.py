"""Behavioural tests for Decision Module S1 wiring (R2-E).

Proves the three remaining behavioural obligations:
1. Overlay ACTIVE (configured + per-seam enabled + stage=shadow): a snapshot L3
   selection returns a result IDENTICAL to heuristic-only, and a shadow row IS written.
2. Overlay INACTIVE (switch off / unconfigured): decisions are byte-identical to
   today and NO call is made.
3. Kill switch flipped via the daemon-settings surface: attempts stop increasing
   WITHOUT a daemon restart; and the kill survives a restart and is re-verified at boot.
Plus the config/settings contract tests (yaml pass-through, config_path resolution,
MANAGED_KEYS registration + allowed_values validation, durable cost store).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from daemon.config import DecisionConfig, DecisionSeamsConfig, load_config
from daemon.models.app_settings import AppSetting
from daemon.services.decision.client import DecisionClient
from daemon.services.decision.config import (
    GLOBAL_ENABLED_KEY,
    DecisionConfig as CoreDecisionConfig,
    seam_switch_tier,
)
from daemon.services.decision.cost_store import (
    AppSettingCostAccumulator,
    SettingsServiceKillStore,
    build_s1_seam_record,
)
from daemon.services.decision.kill import (
    AppSettingKillStore,
    KillChannelResolver,
    seam_enabled_key,
    seam_stage_key,
)
from daemon.services.decision.overlay import DecisionOverlay
from daemon.services.decision.registry import SeamRegistry
from daemon.services.decision.shadow import ShadowWriter
from daemon.services.settings_service import (
    MANAGED_KEYS,
    SettingsService,
    _get_config_value,
    parse_and_validate,
)
from daemon.services.snapshot_search_service import SnapshotSearchService
from tests.unit.decision.helpers import (
    RowCapture,
    choice_answer_payload,
    json_response,
)


def _make_test_engine():
    """A thread-safe in-memory SQLite engine with app_settings table created."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AppSetting.__table__.create(engine, checkfirst=True)
    return engine


class _FakeSnapshot:
    """Lightweight test double with .id and preview fields."""

    def __init__(
        self,
        snapshot_id: str,
        title: str = "title",
        task_summary: str = "find task in this snapshot",
        domain_tags: list[str] | None = None,
        project_id: str = "p1",
    ) -> None:
        self.id = snapshot_id
        self.project_id = project_id
        self.title = title
        self.task_summary = task_summary
        self.domain_tags = domain_tags or ["test"]
        self.status = "active"
        self.digest = {"task_summary_text": task_summary}
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.runtime_version = "0.14.2"
        self.supersedes_snapshot_id = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "task_summary": self.task_summary,
            "domain_tags": self.domain_tags,
        }


class _FakeSnapshotRepo:
    def __init__(self, candidates: list[_FakeSnapshot]) -> None:
        self._candidates = candidates

    def list_active_by_project(self, project_id: str, limit: int = 50) -> list[_FakeSnapshot]:
        return [s for s in self._candidates if s.project_id == project_id and s.status == "active"][:limit]

    def filter_by_tags(self, project_id: str, tags: list[str], mode: str) -> list[_FakeSnapshot]:
        return self._candidates

    def get_embeddings(self, snapshot_id: str) -> list[Any]:
        return [MagicMock(embedding=[1.0, 0.0, 0.0])]


class _FakeEmbeddingService:
    async def embed_text(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        return 1.0


def _staleness_stub(snap: Any) -> dict[str, Any]:
    return {"freshness": "fresh", "snapshot_age_days": 1.0}


def _make_mock_transport(
    calls: list[httpx.Request],
    picked: str = "snap-1",
    values: tuple[str, ...] = ("snap-1", "snap-2", "snap-3"),
) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return json_response(choice_answer_payload(picked=picked, values=values))

    return httpx.MockTransport(_handler)


# =============================================================================
# 0. Config & Settings contract proofs
# =============================================================================


def test_config_yaml_and_config_path_attribute_chains() -> None:
    """Verify that load_config loads config.yaml into Config.decision and that
    all three config_path attribute chains resolve without AttributeError."""
    cfg = load_config("config.yaml")
    assert isinstance(cfg.decision, DecisionConfig)
    assert isinstance(cfg.decision.seams, DecisionSeamsConfig)

    # Prove config_path chains walk cleanly with bare getattr (B-4 protection)
    val_global = _get_config_value(cfg, "decision.global_enabled")
    val_seam_enabled = _get_config_value(cfg, "decision.seams.snapshot_l3_selection_enabled")
    val_seam_stage = _get_config_value(cfg, "decision.seams.snapshot_l3_selection_stage")

    assert val_global is False
    assert val_seam_enabled is False
    assert val_seam_stage == "shadow"

    # Prove seam_switch_tier correctly renders the store keys mapping
    tier = seam_switch_tier(cfg.decision)
    assert tier[GLOBAL_ENABLED_KEY] == "0"
    assert tier[seam_enabled_key("snapshot.l3.selection")] == "0"
    assert tier[seam_stage_key("snapshot.l3.selection")] == "shadow"


def test_managed_keys_registration_and_allowed_values_validation() -> None:
    """Verify MANAGED_KEYS registration, constraints, and allowed_values validation."""
    assert "decision_global_enabled" in MANAGED_KEYS
    assert "decision_seam_snapshot_l3_selection_enabled" in MANAGED_KEYS
    assert "decision_seam_snapshot_l3_selection_stage" in MANAGED_KEYS

    spec_global = MANAGED_KEYS["decision_global_enabled"]
    assert spec_global.value_type == "int"
    assert spec_global.ge == 0
    assert spec_global.le == 1
    assert spec_global.requires_restart is False

    spec_stage = MANAGED_KEYS["decision_seam_snapshot_l3_selection_stage"]
    assert spec_stage.value_type == "str"
    assert spec_stage.allowed_values == {"shadow", "deciding"}
    assert spec_stage.requires_restart is False

    # Valid values
    ok, val, _, _ = parse_and_validate(spec_stage, "shadow")
    assert ok and val == "shadow"
    ok, val, _, _ = parse_and_validate(spec_stage, "deciding")
    assert ok and val == "deciding"

    # Typo rejected at write time (422)
    ok, val, failure_class, detail = parse_and_validate(spec_stage, "shadw")
    assert not ok
    assert failure_class == "validation"
    assert "fails constraint" in (detail or "")


# =============================================================================
# 1. Behavioural Obligation 1: Overlay ACTIVE
# =============================================================================


@pytest.mark.asyncio
async def test_s1_overlay_active_produces_identical_selection_and_writes_shadow_row() -> None:
    """Obligation 1: Overlay ACTIVE (configured + enabled + stage=shadow):
    a snapshot L3 selection returns a result IDENTICAL to heuristic-only,
    and a shadow row IS written."""
    calls: list[httpx.Request] = []
    transport = _make_mock_transport(calls, picked="snap-2")

    engine = _make_test_engine()

    # Core config with overlay ACTIVE and stage=shadow
    core_cfg = CoreDecisionConfig(
        base_url="https://proxy.test/v1",
        api_key="sk-test",
        model="decision",
        global_enabled=True,
    )

    client = DecisionClient(
        core_cfg, transport_factory=lambda: transport, configured=True
    )
    registry = SeamRegistry(client=client, config=core_cfg)
    registry.register(build_s1_seam_record())

    # Switch store with master ON and seam ON (shadow)
    kill_store = AppSettingKillStore(engine)
    await kill_store.set(GLOBAL_ENABLED_KEY, "1")
    await kill_store.set(seam_enabled_key("snapshot.l3.selection"), "1")
    await kill_store.set(seam_stage_key("snapshot.l3.selection"), "shadow")

    kill_resolver = KillChannelResolver(kill_store)
    capture = RowCapture()
    shadow_writer = ShadowWriter(sink=capture)
    accumulator = AppSettingCostAccumulator(engine)

    overlay = DecisionOverlay(
        registry=registry,
        kill=kill_resolver,
        shadow_writer=shadow_writer,
        config=core_cfg,
        accumulator=accumulator,
        configured=True,
    )

    # Candidates: snap-1, snap-2, snap-3
    candidates = [
        _FakeSnapshot("snap-1", "First"),
        _FakeSnapshot("snap-2", "Second"),
        _FakeSnapshot("snap-3", "Third"),
    ]
    repo = _FakeSnapshotRepo(candidates)
    emb_service = _FakeEmbeddingService()

    # Mock chat client that heuristic uses: picks snap-1 then snap-3
    class _FakeChatCompletions:
        def create(self, *args, **kwargs):
            m = MagicMock()
            m.choices = [
                MagicMock(
                    message=MagicMock(content='{"selected": ["snap-1", "snap-3"]}')
                )
            ]
            return m

    fake_llm_client = MagicMock()
    fake_llm_client.chat.completions = _FakeChatCompletions()

    # 1. Search WITHOUT overlay (heuristic baseline)
    service_baseline = SnapshotSearchService(
        snapshot_repo=repo,
        embedding_service=emb_service,  # type: ignore[arg-type]
        llm_config={"model": "test-chat"},
        staleness_fn=_staleness_stub,
        decision_overlay=None,
    )
    baseline_result = await service_baseline.search(
        query="find task", project_id="p1", llm_client=fake_llm_client
    )

    # 2. Search WITH ACTIVE overlay
    service_active = SnapshotSearchService(
        snapshot_repo=repo,
        embedding_service=emb_service,  # type: ignore[arg-type]
        llm_config={"model": "test-chat"},
        staleness_fn=_staleness_stub,
        decision_overlay=overlay,
    )
    active_result = await service_active.search(
        query="find task", project_id="p1", llm_client=fake_llm_client
    )

    # ASSERTION 1: Result is strictly IDENTICAL to heuristic-only
    assert active_result["error"] is None
    assert [r["snapshot_id"] for r in active_result["results"]] == [
        r["snapshot_id"] for r in baseline_result["results"]
    ] == ["snap-1", "snap-3"]

    # (c) — the consult is DETACHED now: drain the in-flight task
    # before asserting on its effects.
    await overlay.wait_for_detached()

    # ASSERTION 2: Proxy was called (active consult)
    assert len(calls) == 1

    # ASSERTION 3: Shadow row IS written
    assert len(capture.rows) == 1
    last_row = capture.last
    assert "snapshot.l3.selection" in last_row
    assert "shadow" in last_row
    assert "snap-1" in last_row


# =============================================================================
# 2. Behavioural Obligation 2: Overlay INACTIVE
# =============================================================================


@pytest.mark.asyncio
async def test_s1_overlay_inactive_produces_byte_identical_results_and_no_call_made() -> None:
    """Obligation 2: Overlay INACTIVE (switch off / unconfigured):
    decisions are byte-identical to today and NO call is made."""
    calls: list[httpx.Request] = []
    transport = _make_mock_transport(calls)

    engine = _make_test_engine()

    # Switch store with master OFF (default state)
    kill_store = AppSettingKillStore(engine)
    await kill_store.set(GLOBAL_ENABLED_KEY, "0")
    await kill_store.set(seam_enabled_key("snapshot.l3.selection"), "0")

    core_cfg = CoreDecisionConfig(
        base_url="https://proxy.test/v1",
        api_key="sk-test",
        model="decision",
        global_enabled=False,
    )
    client = DecisionClient(
        core_cfg, transport_factory=lambda: transport, configured=True
    )
    registry = SeamRegistry(client=client, config=core_cfg)
    registry.register(build_s1_seam_record())

    kill_resolver = KillChannelResolver(kill_store)
    capture = RowCapture()
    shadow_writer = ShadowWriter(sink=capture)
    accumulator = AppSettingCostAccumulator(engine)

    overlay = DecisionOverlay(
        registry=registry,
        kill=kill_resolver,
        shadow_writer=shadow_writer,
        config=core_cfg,
        accumulator=accumulator,
        configured=True,
    )

    candidates = [
        _FakeSnapshot("snap-1", "First"),
        _FakeSnapshot("snap-2", "Second"),
    ]
    repo = _FakeSnapshotRepo(candidates)
    emb_service = _FakeEmbeddingService()

    class _FakeChatCompletions:
        def create(self, *args, **kwargs):
            m = MagicMock()
            m.choices = [
                MagicMock(message=MagicMock(content='{"selected": ["snap-1"]}'))
            ]
            return m

    fake_llm_client = MagicMock()
    fake_llm_client.chat.completions = _FakeChatCompletions()

    # Search with INACTIVE overlay
    service = SnapshotSearchService(
        snapshot_repo=repo,
        embedding_service=emb_service,  # type: ignore[arg-type]
        llm_config={"model": "test-chat"},
        staleness_fn=_staleness_stub,
        decision_overlay=overlay,
    )
    res = await service.search(
        query="find task", project_id="p1", llm_client=fake_llm_client
    )

    # (c) — the dispatch itself happens even for an INACTIVE overlay
    # (the gate read inside the task suppresses it): drain before
    # asserting nothing was called.
    await overlay.wait_for_detached()

    # ASSERTION 1: Decision is byte-identical
    assert [r["snapshot_id"] for r in res["results"]] == ["snap-1"]

    # ASSERTION 2: NO socket call was made
    assert len(calls) == 0

    # Test Unconfigured as well
    unconfigured_cfg = CoreDecisionConfig(base_url="", api_key="")
    overlay_unconfigured = DecisionOverlay(
        registry=registry,
        kill=kill_resolver,
        shadow_writer=shadow_writer,
        config=unconfigured_cfg,
        accumulator=accumulator,
    )
    service_unconf = SnapshotSearchService(
        snapshot_repo=repo,
        embedding_service=emb_service,  # type: ignore[arg-type]
        llm_config={"model": "test-chat"},
        staleness_fn=_staleness_stub,
        decision_overlay=overlay_unconfigured,
    )
    res2 = await service_unconf.search(
        query="find task", project_id="p1", llm_client=fake_llm_client
    )
    assert [r["snapshot_id"] for r in res2["results"]] == ["snap-1"]
    assert len(calls) == 0
    await overlay_unconfigured.wait_for_detached()


# =============================================================================
# 3. Behavioural Obligation 3: Kill switch flip & reboot survival
# =============================================================================


@pytest.mark.asyncio
async def test_kill_switch_flip_takes_effect_without_restart_and_survives_restart() -> None:
    """Obligation 3: Kill switch flipped via the daemon-settings surface:
    attempts stop increasing WITHOUT a daemon restart; and the kill survives
    a restart and is re-verified at boot."""
    calls: list[httpx.Request] = []
    transport = _make_mock_transport(calls, picked="snap-1", values=("snap-1", "snap-2"))

    engine = _make_test_engine()

    settings_service = SettingsService(engine=engine)
    fallback_store = AppSettingKillStore(engine)
    kill_store = SettingsServiceKillStore(
        lambda: settings_service, fallback_store=fallback_store
    )

    core_cfg = CoreDecisionConfig(
        base_url="https://proxy.test/v1",
        api_key="sk-test",
        model="decision",
        global_enabled=True,
    )
    client = DecisionClient(
        core_cfg, transport_factory=lambda: transport, configured=True
    )
    registry = SeamRegistry(client=client, config=core_cfg)
    registry.register(build_s1_seam_record())

    # Build kill resolver bound to settings_service.set_override
    kill_resolver = KillChannelResolver(
        kill_store,
        override_writer=lambda k, v, u: settings_service.set_override(k, v, u),
    )

    # Bind settings_service invalidation hook to resolver.invalidate()
    settings_service.register_override_changed_callback(
        lambda key: kill_resolver.invalidate(None)
    )

    # Start with switches ON in settings service
    settings_service.set_override(GLOBAL_ENABLED_KEY, 1)
    settings_service.set_override(seam_enabled_key("snapshot.l3.selection"), 1)
    settings_service.set_override(seam_stage_key("snapshot.l3.selection"), "shadow")

    overlay = DecisionOverlay(
        registry=registry,
        kill=kill_resolver,
        shadow_writer=ShadowWriter(sink=lambda r: None),
        config=core_cfg,
        accumulator=AppSettingCostAccumulator(engine),
        configured=True,
    )

    # Turn 1: Overlay is ON, consult succeeds and calls proxy
    res1 = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=("snap-1", "snap-2"),
        context={"query": "q", "candidate_ids": ("snap-1", "snap-2")},
    )
    assert res1.state == "logged"
    assert len(calls) == 1

    # OPERATOR ACTION: Kill switch flipped via settings surface (WITHOUT daemon restart)
    settings_service.set_override(GLOBAL_ENABLED_KEY, 0)

    # Turn 2: Attempt on NEXT consult WITHOUT restart
    res2 = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=("snap-1", "snap-2"),
        context={"query": "q", "candidate_ids": ("snap-1", "snap-2")},
    )
    assert res2.state == "suppressed"
    assert res2.suppression == "switch_off"
    # Attempts stopped increasing!
    assert len(calls) == 1

    # SIMULATE DAEMON RESTART:
    # 1. Resolver in-memory cache is wiped
    # 2. Fresh resolver created over the same persistent DB engine
    fresh_kill_store = SettingsServiceKillStore(
        lambda: settings_service, fallback_store=AppSettingKillStore(engine)
    )
    fresh_resolver = KillChannelResolver(
        fresh_kill_store,
        override_writer=lambda k, v, u: settings_service.set_override(k, v, u),
    )

    # 3. Boot re-verify pass runs at boot (simulating manager.initialize())
    outcomes = await fresh_resolver.reverify(["snapshot.l3.selection"])
    outcomes_dict = {o.seam_id: o.outcome for o in outcomes}
    assert outcomes_dict["global"] == "verified_off"

    # Turn 3 after reboot: kill survived restart and remains OFF
    fresh_overlay = DecisionOverlay(
        registry=registry,
        kill=fresh_resolver,
        shadow_writer=ShadowWriter(sink=lambda r: None),
        config=core_cfg,
        accumulator=AppSettingCostAccumulator(engine),
    )
    res3 = await fresh_overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=("snap-1", "snap-2"),
        context={"query": "q", "candidate_ids": ("snap-1", "snap-2")},
    )
    assert res3.state == "suppressed"
    assert res3.suppression == "switch_off"
    assert len(calls) == 1


# =============================================================================
# 4. Durable Cost Store test
# =============================================================================


@pytest.mark.asyncio
async def test_cost_store_durability_across_restarts() -> None:
    """Verify AppSettingCostAccumulator persists spend across restarts and respects periods."""
    engine = _make_test_engine()

    acc1 = AppSettingCostAccumulator(engine, period_getter=lambda: "2026-11")

    # Record spend in session 1
    totals1 = await acc1.record("snapshot.l3.selection", usd=1.25, tokens=4000)
    assert totals1.month_to_date_usd == 1.25
    assert totals1.month_to_date_tokens == 4000
    assert totals1.seam_month_to_date_usd == 1.25
    assert totals1.seam_month_to_date_tokens == 4000

    # Simulate restart: new accumulator instance over the same DB
    acc2 = AppSettingCostAccumulator(engine, period_getter=lambda: "2026-11")
    totals2 = await acc2.record("snapshot.l3.selection", usd=0.75, tokens=1000)
    assert totals2.month_to_date_usd == 2.0
    assert totals2.month_to_date_tokens == 5000
    assert totals2.seam_month_to_date_usd == 2.0
    assert totals2.seam_month_to_date_tokens == 5000

    # New calendar month: spend starts at 0 without any manual reset
    acc_next_month = AppSettingCostAccumulator(engine, period_getter=lambda: "2026-12")
    totals3 = await acc_next_month.record("snapshot.l3.selection", usd=0.50, tokens=500)
    assert totals3.month_to_date_usd == 0.50
    assert totals3.month_to_date_tokens == 500
