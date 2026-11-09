"""The council's blocking-defect pins (D1..D4) — written against the SHIPPED shape.

Each pin is DISCRIMINATING: it must go RED if the safety defect returns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from daemon.services.decision.client import DecisionClient
from daemon.services.decision.kill import (
    GLOBAL_ENABLED_KEY,
    AppSettingKillStore,
    KillChannelResolver,
    seam_enabled_key,
    seam_stage_key,
)
from tests.unit.decision.helpers import choice_answer_payload, json_response, smart_handler


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


# ═══════════════════════════════════════════════════════════════════════
# D1 — the PRODUCTION construction path can be configured
# ═══════════════════════════════════════════════════════════════════════


def _sqlite_engine():
    """A real SQLite engine with the ``app_settings`` table."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel

    from daemon.models.app_settings import AppSetting

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    AppSetting.__table__.create(engine, checkfirst=True)
    return engine


class _HouseDecisionConfig:
    """A stand-in for the house ``Config.decision`` namespace."""

    base_url = ""
    api_key = ""
    model = ""
    timeout_s = 5.0
    cost_ceiling_usd = 50.0
    token_ceiling = 10_000_000
    cost_floor_per_attempt_usd = 0.0005
    pricing_usd_per_million_tokens = ""
    global_enabled = True
    seams = None

    def __init__(self, **overrides: Any) -> None:
        for key, value in overrides.items():
            setattr(self, key, value)


async def test_the_production_factory_stamps_the_configured_predicate_with_decision_signal(
    monkeypatch,
) -> None:
    """D1 PIN — the MANAGER-CONSTRUCTION PATH proves CONFIGURED.

    Calls the SAME factory the manager calls. NO ``configured=``
    override and NO ``make_config`` — the config is resolved from the
    env + the yaml inside the factory, exactly as production does it.

    Discriminating: under the old ``from_source`` path
    ``explicitly_configured`` was never stamped, so the factory's
    overlay would suppress ``not_configured`` and this pin would fail.
    """
    from daemon.services.decision.factory import build_decision_overlay

    monkeypatch.setenv("DECISION_API_KEY", "sk-production-signal")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-inherited")
    monkeypatch.delenv("DECISION_BASE_URL", raising=False)
    parts = build_decision_overlay(
        engine=_sqlite_engine(),
        house_decision_config=_HouseDecisionConfig(),
    )
    assert parts.config.is_configured() is True
    assert parts.overlay._is_configured() is True
    assert parts.config.api_key == "sk-production-signal"
    assert parts.config.base_url == "https://proxy.test/v1"  # value fallback


async def test_the_production_factory_with_only_openai_inherited_is_not_configured(
    monkeypatch,
) -> None:
    """D1 PIN — the same factory, only ``OPENAI_*`` inherited ⇒ suppressed.

    Discriminating: a factory that ignored the signal would report
    configured here and this pin would fail.
    """
    from daemon.services.decision.factory import build_decision_overlay

    monkeypatch.delenv("DECISION_API_KEY", raising=False)
    monkeypatch.delenv("DECISION_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-inherited")
    parts = build_decision_overlay(
        engine=_sqlite_engine(),
        house_decision_config=_HouseDecisionConfig(),
    )
    assert parts.config.is_configured() is False
    assert parts.config.base_url == "https://proxy.test/v1"  # value fallback still works
    assert parts.config.api_key == "sk-inherited"


def test_the_manager_calls_the_factory() -> None:
    """D1 PIN — nothing else constructs the overlay in production.

    Discriminating: a manager that reverted to constructing the overlay
    inline (e.g. ``DecisionOverlay(`` directly) would fail this pin.
    """
    source = (_repo_root() / "daemon" / "manager.py").read_text()
    assert "build_decision_overlay(" in source
    # The manager must NOT construct the overlay or the client directly.
    assert "DecisionOverlay(" not in source
    assert "DecisionClient(" not in source


# ═══════════════════════════════════════════════════════════════════════
# D1-ADJACENT — the shipped config.yaml must not arm the belt
# ═══════════════════════════════════════════════════════════════════════


def test_the_shipped_config_yaml_has_no_active_decision_block() -> None:
    """D1-ADJACENT PIN — read from the REPO, not a fixture.

    The shipped ``config.yaml`` must NOT satisfy the yaml half of the
    configured predicate: "configured" must require an operator ACTION
    in every case. Discriminating: re-adding an active ``decision:``
    block makes this pin fail.
    """
    import yaml

    text = (_repo_root() / "config.yaml").read_text()
    parsed = yaml.safe_load(text) or {}
    assert "decision" not in parsed, (
        "the shipped config.yaml carries an ACTIVE decision: block — it must be "
        "absent or fully commented out (the belt would be permanently armed)"
    )
    assert "decision:" in text, "the commented-out example should still be present"


def test_the_load_config_passthrough_is_retained() -> None:
    """D1-ADJACENT — a HAND-ADDED block must be honoured, not ignored."""
    source = (_repo_root() / "daemon" / "config.py").read_text()
    assert 'config_dict["decision"] = clean_dec' in source


# ═══════════════════════════════════════════════════════════════════════
# D2 — an unreadable kill tier resolves OFF (production stores)
# ═══════════════════════════════════════════════════════════════════════


class _BrokenEngine:
    """An engine whose every session raises — a REAL production store input."""

    def __init__(self) -> None:
        self.connect_called = 0

    def connect(self) -> Any:
        self.connect_called += 1
        raise RuntimeError("the settings database is unreachable")

    def __call__(self) -> Any:
        return self.connect()


async def _assert_unreadable_resolves_off(store: Any, seam_id: str) -> None:
    """The shared assertion: OFF + unreadable, never ON."""
    kill = KillChannelResolver(store, env={"DECISION_ENABLED": "1"})
    state = await kill.read_state(seam_id)
    assert state.readable is False, "an unreadable store must resolve UNREADABLE"
    assert state.global_on is False, "unreadable must resolve OFF, never ON"
    assert state.seam_on is False
    assert state.consult_allowed is False


async def test_d2_the_app_setting_kill_store_raises_on_a_broken_engine() -> None:
    """D2 PIN (a) — the SHIPPED AppSettingKillStore, a FAILING ENGINE.

    No double: the real store over an engine whose session raises.
    Discriminating: a store that swallows the error to ``None`` makes
    the resolver read the env tier → ``DECISION_ENABLED=1`` → ON.
    """
    store = AppSettingKillStore(_BrokenEngine())
    with pytest.raises(Exception):
        await store.get(GLOBAL_ENABLED_KEY)
    await _assert_unreadable_resolves_off(store, "snapshot.l3.selection")


async def test_d2_the_settings_service_kill_store_raises_when_both_tiers_fail() -> None:
    """D2 PIN (a) — the SHIPPED SettingsServiceKillStore, both tiers failing.

    Discriminating: swallowing either tier's error to ``None`` lets the
    resolver fall through to the env tier and resolve ON.
    """
    from daemon.services.decision.cost_store import SettingsServiceKillStore

    class _BrokenService:
        def get_overrides(self) -> dict[str, Any]:
            raise RuntimeError("the settings table is unreachable")

    store = SettingsServiceKillStore(
        lambda: _BrokenService(),
        fallback_store=AppSettingKillStore(_BrokenEngine()),
    )
    with pytest.raises(Exception):
        await store.get(GLOBAL_ENABLED_KEY)
    await _assert_unreadable_resolves_off(store, "snapshot.l3.selection")


async def test_d2_an_unreadable_kill_emits_a_switch_unreadable_row_not_switch_off() -> None:
    """D2 PIN — the suppressed row says ``switch_unreadable``, not ``switch_off``.

    Discriminating: the old code resolved the unreadable tier to the
    env/default tier and emitted the indistinguishable ``switch_off``.
    """
    from daemon.services.decision.overlay import DecisionOverlay
    from daemon.services.decision.registry import SeamRegistry
    from daemon.services.decision.shadow import ShadowWriter
    from tests.unit.decision.helpers import RowCapture, make_config, seam_record

    capture = RowCapture()
    config = make_config()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return smart_handler(request)

    client = DecisionClient(
        config, transport_factory=lambda: httpx.MockTransport(handler), configured=True
    )
    registry = SeamRegistry(client=client, config=config)
    registry.register(seam_record())

    kill = KillChannelResolver(
        AppSettingKillStore(_BrokenEngine()),
        env={"DECISION_ENABLED": "1"},
    )
    overlay = DecisionOverlay(
        registry=registry,
        kill=kill,
        shadow_writer=ShadowWriter(sink=capture),
        config=config,
        configured=True,
    )
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert calls == [], "an unreadable kill must never reach the socket"
    assert result.suppression == "switch_unreadable"
    assert "reason=switch_unreadable" in capture.last


async def test_d2_a_write_that_does_not_read_back_resolves_off() -> None:
    """D2 PIN (b) — council obligation 8: assert the RESOLUTION, not the surface.

    Discriminating: a resolver that reports a mismatch but still reads
    the (unchanged) store value as ON would fail this pin.
    """

    class _NonPersistingStore:
        """``set`` silently keeps the OLD value; ``get`` returns it."""

        def __init__(self) -> None:
            self._values = {
                GLOBAL_ENABLED_KEY: "1",
                seam_enabled_key("snapshot.l3.selection"): "1",
                seam_stage_key("snapshot.l3.selection"): "shadow",
            }

        async def get(self, key: str) -> str | None:
            return self._values.get(key)

        async def set(self, key: str, value: str) -> None:
            return None  # the write does not land

    kill = KillChannelResolver(_NonPersistingStore(), env={})
    write = await kill.write("snapshot.l3.selection", enabled=False)
    assert write.ok is False
    # THE RESOLUTION, not merely the surface: after a failed write the
    # switch must read OFF (fail-closed), never the stale ON.
    state = await kill.read_state("snapshot.l3.selection")
    assert state.seam_on is False, (
        "a write that does not read back must leave the switch resolving OFF"
    )
    assert state.consult_allowed is False


# ═══════════════════════════════════════════════════════════════════════
# D3 — a timeout consult bills > 0
# ═══════════════════════════════════════════════════════════════════════


async def test_d3_a_timeout_consult_bills_a_positive_floor() -> None:
    """D3 PIN — timeout bills > 0 (the floor, operator-configured).

    Discriminating: without the floor a timeout accrues exactly 0.00,
    so timeout-only traffic bypasses BOTH the USD ceiling and the token
    fail-safe. This pin goes RED if the floor is reverted.
    """
    import asyncio

    from daemon.services.decision.overlay import (
        CostTotals,
        DecisionOverlay,
        InMemoryCostAccumulator,
    )
    from daemon.services.decision.registry import SeamRegistry
    from daemon.services.decision.shadow import ShadowWriter
    from tests.unit.decision.helpers import make_config, seam_record

    class _RecordingAccumulator(InMemoryCostAccumulator):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[float, int]] = []

        async def record(self, seam_id: str, *, usd: float, tokens: int) -> CostTotals:
            self.calls.append((usd, tokens))
            return await super().record(seam_id, usd=usd, tokens=tokens)

    accumulator = _RecordingAccumulator()
    config = make_config(cost_floor_per_attempt_usd=0.0005)

    async def _slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.5)
        return json_response(choice_answer_payload())

    client = DecisionClient(
        config,
        transport_factory=lambda: httpx.MockTransport(_slow),
        configured=True,
    )
    registry = SeamRegistry(client=client, config=config)
    registry.register(seam_record())
    kill = KillChannelResolver(
        _OnStore(),
        env={},
    )
    overlay = DecisionOverlay(
        registry=registry,
        kill=kill,
        shadow_writer=ShadowWriter(sink=lambda row: None),
        config=config,
        accumulator=accumulator,
        configured=True,
    )
    # A short lane budget forces the timeout.
    record = registry.get("snapshot.l3.selection")
    assert record is not None
    overlay._registry._records["snapshot.l3.selection"] = type(record)(
        **{**record.__dict__, "timeout_s": 0.05}
    )
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert result.failure_class == "timeout"
    assert accumulator.calls, "the attempt must be metered"
    usd, tokens = accumulator.calls[-1]
    assert usd > 0, f"a timeout must bill > 0 (the D3 floor); got {usd}"


class _OnStore:
    """A store with the master + seam ON (the consult gate passes)."""

    def __init__(self) -> None:
        self._values = {
            GLOBAL_ENABLED_KEY: "1",
            seam_enabled_key("snapshot.l3.selection"): "1",
            seam_stage_key("snapshot.l3.selection"): "shadow",
        }

    async def get(self, key: str) -> str | None:
        return self._values.get(key)

    async def set(self, key: str, value: str) -> None:
        self._values[key] = value


# ═══════════════════════════════════════════════════════════════════════
# FOLD-NOW ADVISORY 3 — cost-ledger atomicity
# ═══════════════════════════════════════════════════════════════════════


async def test_the_cost_ledger_does_not_lose_concurrent_deltas() -> None:
    """ADVISORY 3 PIN — concurrent consults must not undercount spend.

    The increment is a read-modify-write; without serialization two
    concurrent consults both read the OLD total and one delta is LOST —
    the month-to-date spend undercounts, and the ceiling binds on that
    undercounted quantity. Discriminating: removing the lock makes the
    total strictly less than N × the delta.
    """
    import asyncio

    from daemon.services.decision.cost_store import AppSettingCostAccumulator

    engine = _sqlite_engine()
    accumulator = AppSettingCostAccumulator(engine, period_getter=lambda: "2099-01")
    n = 20
    await asyncio.gather(
        *[accumulator.record("snapshot.l3.selection", usd=0.01, tokens=10) for _ in range(n)]
    )
    totals = await accumulator.current_totals("snapshot.l3.selection")
    assert totals.month_to_date_usd == pytest.approx(n * 0.01), (
        f"the ledger lost deltas under concurrency: {totals.month_to_date_usd}"
    )
    assert totals.month_to_date_tokens == n * 10
