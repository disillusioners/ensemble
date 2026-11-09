"""The kill channel's resolved-state cache (L5) + the fail-closed path.

"Read per consult" must NOT be a synchronous DB read per consult: the
daemon's settings ``get_overrides()`` opens a NEW Session and queries
EVERY call with NO cache, and it is SYNC — a DB round-trip per consult
would sit on the hot path and break the house event-loop rule.

So the RESOLVED switch state is cached in-process and invalidated by
the settings WRITE path. These tests pin:

* the second read of the same seam makes NO store read;
* the invalidation hook makes a flipped kill effective on the NEXT
  consult (no restart);
* a store read FAILURE resolves the switch OFF (fail-closed) and emits
  a ``switch_unreadable`` suppressed row with NO call.
"""

from __future__ import annotations

from typing import Any, Mapping

import httpx

from daemon.services.decision.kill import (
    GLOBAL_ENABLED_KEY,
    InMemoryKillStore,
    KillChannelResolver,
    seam_enabled_key,
    seam_stage_key,
)
from daemon.services.decision.types import DecisionStage
from tests.unit.decision.helpers import RowCapture, make_config, smart_handler


class _CountingStore(InMemoryKillStore):
    """A store that counts its reads and can be made to FAIL."""

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        super().__init__(values)
        self.reads = 0
        self.fail = False

    async def get(self, key: str) -> str | None:
        self.reads += 1
        if self.fail:
            raise RuntimeError("the settings table is unreachable")
        return await super().get(key)


def _on() -> dict[str, str]:
    return {
        GLOBAL_ENABLED_KEY: "1",
        seam_enabled_key("snapshot.l3.selection"): "1",
        seam_stage_key("snapshot.l3.selection"): "shadow",
    }


async def test_the_resolved_state_is_served_from_the_cache() -> None:
    """L5 — the second read of the same seam makes NO store read."""
    store = _CountingStore(_on())
    kill = KillChannelResolver(store, env={})
    first = await kill.read_state("snapshot.l3.selection")
    reads_after_first = store.reads
    second = await kill.read_state("snapshot.l3.selection")
    assert store.reads == reads_after_first, "the resolved state must be cached"
    assert second == first


async def test_the_invalidation_hook_drops_the_cached_state() -> None:
    """L5 — the write-path invalidation is the public hook."""
    store = _CountingStore(_on())
    kill = KillChannelResolver(store, env={})
    await kill.read_state("snapshot.l3.selection")
    reads = store.reads
    kill.invalidate("snapshot.l3.selection")
    await kill.read_state("snapshot.l3.selection")
    assert store.reads > reads, "the invalidated state must be re-resolved"


async def test_a_flipped_kill_takes_effect_on_the_next_consult() -> None:
    """L5 — the cache never delays a kill: the write invalidates it."""
    from tests.unit.decision.test_overlay import _build_overlay, _on_seam

    overlay, _registry, kill, store, _capture, calls = _build_overlay(
        store_values=_on_seam()
    )
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert len(calls) == 1
    # The operator writes the kill through the live channel; the write
    # path invalidates the cache (the wiring round binds the settings
    # service's set_override/clear_override to ``kill.invalidate``).
    await kill.write("snapshot.l3.selection", enabled=False)
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert len(calls) == 1, "the kill stops the next call without a restart"


async def test_a_store_read_failure_resolves_off_and_is_unreadable() -> None:
    """L5 — fail-closed: an unresolvable state ⇒ OFF + no call."""
    store = _CountingStore(_on())
    kill = KillChannelResolver(store, env={})
    store.fail = True
    state = await kill.read_state("snapshot.l3.selection")
    assert state.readable is False
    assert state.global_on is False
    assert state.seam_on is False
    assert state.consult_allowed is False


async def test_an_unreadable_switch_emits_a_suppressed_row_and_no_call() -> None:
    """FLAG12 — an unreadable switch ⇒ NO CALL AT ALL + the row."""
    from daemon.services.decision.client import DecisionClient
    from daemon.services.decision.overlay import DecisionOverlay
    from daemon.services.decision.registry import SeamRegistry
    from daemon.services.decision.shadow import ShadowWriter
    from tests.unit.decision.helpers import seam_record

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return smart_handler(request)

    config = make_config()
    client = DecisionClient(
        config, transport_factory=lambda: httpx.MockTransport(handler)
    )
    registry = SeamRegistry(client=client, config=config)
    registry.register(seam_record())

    store = _CountingStore(_on())
    store.fail = True
    kill = KillChannelResolver(store, env={})
    capture = RowCapture()
    overlay = DecisionOverlay(
        registry=registry,
        kill=kill,
        shadow_writer=ShadowWriter(sink=capture),
        config=config,
    )
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert calls == [], "an unreadable switch must not reach the socket"
    assert result.suppression == "switch_unreadable"
    assert "reason=switch_unreadable" in capture.last


async def test_the_boot_reverify_is_the_public_entry_point() -> None:
    """L5/AC-9 — the boot re-verify drops the cache and re-reads."""
    store = _CountingStore(_on())
    kill = KillChannelResolver(store, env={})
    await kill.read_state("snapshot.l3.selection")
    reads = store.reads
    outcomes = await kill.reverify(["snapshot.l3.selection"])
    assert [o.outcome for o in outcomes if o.seam_id == "snapshot.l3.selection"] == [
        "verified_on"
    ]
    assert store.reads > reads, "the re-verify re-reads the store"


async def test_the_store_keys_use_the_underscore_flattened_seam_id() -> None:
    """L7 — the KEY carries the flattened seam id; the record keeps the dotted one.

    The settings service's ``config_path`` walk DOT-SPLITS, so a dotted
    seam id in a key would walk into a non-existent attribute — and the
    crash is LATENT (it fires the first time an operator writes a DB
    override).
    """
    from daemon.services.decision.kill import (
        flatten_seam_id,
        seam_enabled_key,
        seam_env_name,
        seam_stage_key,
    )

    assert flatten_seam_id("snapshot.l3.selection") == "snapshot_l3_selection"
    assert (
        seam_enabled_key("snapshot.l3.selection")
        == "decision_seam_snapshot_l3_selection_enabled"
    )
    assert (
        seam_stage_key("snapshot.l3.selection")
        == "decision_seam_snapshot_l3_selection_stage"
    )
    assert seam_env_name("snapshot.l3.selection") == "DECISION_SEAM_SNAPSHOT_L3_SELECTION"


# ── the sanctioned write path (SettingsService.set_override) ──────────


async def test_the_switch_write_goes_through_the_override_writer() -> None:
    """The write path is the operator-visible ``set_override`` seam.

    ``set_override`` is registry-gated and validates the value against
    the managed key's declared type, so the value handed to it must be
    the REGISTRY-typed one (an int for the ``_enabled`` keys, a string
    for the ``_stage`` key) — not the store's raw string form.
    """
    calls: list[tuple[str, Any, str | None]] = []

    def writer(key: str, value: Any, updated_by: str | None) -> None:
        calls.append((key, value, updated_by))

    store = _CountingStore(_on())
    kill = KillChannelResolver(store, env={}, override_writer=writer)
    await kill.write("snapshot.l3.selection", enabled=False)
    await kill.write("snapshot.l3.selection", stage=DecisionStage.SHADOW)
    keys = {key for key, _v, _u in calls}
    assert keys == {
        "decision_seam_snapshot_l3_selection_enabled",
        "decision_seam_snapshot_l3_selection_stage",
    }
    typed = {key: value for key, value, _u in calls}
    assert typed["decision_seam_snapshot_l3_selection_enabled"] == 0
    assert typed["decision_seam_snapshot_l3_selection_stage"] == "shadow"


async def test_the_daemon_side_write_emits_its_own_audit_line(caplog) -> None:
    """``set_override`` does NOT audit — the ROUTER does.

    A daemon-side write (the boot seed, the auto-trip) would therefore
    write the row silently, so this module emits a line in the router's
    own format — one grep covers both surfaces.
    """
    import logging

    def writer(key: str, value: Any, updated_by: str | None) -> None:
        return None

    store = _CountingStore(_on())
    kill = KillChannelResolver(store, env={}, override_writer=writer)
    with caplog.at_level(logging.INFO, logger="daemon.services.decision.kill"):
        await kill.write("snapshot.l3.selection", enabled=False)
    assert any(
        "[Settings] db override set key=decision_seam_snapshot_l3_selection_enabled"
        in record.getMessage()
        for record in caplog.records
    )


async def test_the_yaml_tier_may_come_from_the_house_config() -> None:
    """The yaml tier is the house config's ``yaml_switch_tier()``."""
    from daemon.services.decision.config import DecisionConfig

    class _Seams:
        snapshot_l3_selection_enabled = True
        snapshot_l3_selection_stage = "deciding"

    class _House:
        global_enabled = True
        seams = _Seams()

    store = _CountingStore({})
    kill = KillChannelResolver(store, env={}, yaml_snapshot=_House())
    state = await kill.read_state("snapshot.l3.selection")
    assert state.global_on is True
    assert state.seam_on is True
    assert state.stage is DecisionStage.SHADOW or state.stage.value == "deciding"


async def test_the_config_renders_the_yaml_switch_tier() -> None:
    """The config's yaml tier is keyed by the STORE keys."""
    from daemon.services.decision.config import DecisionConfig

    class _Seams:
        snapshot_l3_selection_enabled = True
        snapshot_l3_selection_stage = "deciding"

    class _House:
        global_enabled = True
        seams = _Seams()

    config = DecisionConfig.from_source(_House())
    tier = config.yaml_switch_tier()
    assert tier["decision_global_enabled"] == "1"
    assert tier["decision_seam_snapshot_l3_selection_enabled"] == "1"
    assert tier["decision_seam_snapshot_l3_selection_stage"] == "deciding"
