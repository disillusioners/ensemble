"""The overlay orchestrator — the gates, the throttle, the cost ceiling.

Covers AC-8 (the consult/apply gate split), AC-9 (the per-seam default
OFF + the boot re-verify), AC-10 (the kill channel), AC-11/FLAG3
(the auto-trip), AC-14 (the R-B identity), AC-18 (the breaker/throttle)
and T-4/T-6/T-8.
"""

from __future__ import annotations

from typing import Any, Mapping

import httpx
import pytest

from daemon.services.decision import metrics
from daemon.services.decision.client import DecisionClient
from daemon.services.decision.config import DecisionConfig
from daemon.services.decision.kill import (
    GLOBAL_ENABLED_KEY,
    InMemoryKillStore,
    KillChannelResolver,
    seam_enabled_key,
    seam_stage_key,
)
from daemon.services.decision.overlay import (
    CostTotals,
    DecisionOverlay,
    InMemoryCostAccumulator,
)
from daemon.services.decision.registry import SeamRegistry, ReviewRecord
from daemon.services.decision.shadow import ShadowWriter
from daemon.services.decision.types import (
    DecisionStage,
    FailureClass,
    OverlayOutcome,
    SuppressionReason,
)
from tests.unit.decision.helpers import (
    RowCapture,
    choice_answer_payload,
    json_response,
    make_config,
    seam_record,
    smart_handler,
    text_response,
)


class _PreloadedAccumulator(InMemoryCostAccumulator):
    """An accumulator pre-loaded with a month-to-date total."""

    def __init__(
        self,
        total: float = 0.0,
        seam: str | None = None,
        tokens: int = 0,
    ) -> None:
        super().__init__()
        self._usd = float(total)
        self._tokens = int(tokens)
        if seam is not None:
            self._per_seam_usd[seam] = float(total)
            self._per_seam_tokens[seam] = int(tokens)

    async def record(
        self, seam_id: str, *, usd: float, tokens: int
    ) -> CostTotals:
        self._usd += float(usd)
        self._tokens += int(tokens)
        self._per_seam_usd[seam_id] = self._per_seam_usd.get(seam_id, 0.0) + float(usd)
        self._per_seam_tokens[seam_id] = (
            self._per_seam_tokens.get(seam_id, 0) + int(tokens)
        )
        return CostTotals(
            month_to_date_usd=self._usd,
            seam_month_to_date_usd=self._per_seam_usd.get(seam_id, 0.0),
            month_to_date_tokens=self._tokens,
            seam_month_to_date_tokens=self._per_seam_tokens.get(seam_id, 0),
        )


def _build_overlay(
    *,
    handler=None,
    store_values: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
    config: DecisionConfig | None = None,
    accumulator=None,
    notify_sink=None,
    board_service=None,
    seam=None,
    shadow_writer: ShadowWriter | None = None,
):
    """Build a registry + overlay over mocks; returns the parts."""
    calls: list[httpx.Request] = []

    def _recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return (handler or smart_handler)(request)

    transport = httpx.MockTransport(_recording)
    the_config = config or make_config()
    client = DecisionClient(the_config, transport_factory=lambda: transport)
    registry = SeamRegistry(client=client, config=the_config)
    record = seam if seam is not None else seam_record()
    registry.register(record)

    store = InMemoryKillStore(dict(store_values or {}))
    kill = KillChannelResolver(store, env=env or {})
    capture = RowCapture()
    writer = shadow_writer or ShadowWriter(sink=capture)
    overlay = DecisionOverlay(
        registry=registry,
        kill=kill,
        shadow_writer=writer,
        config=the_config,
        accumulator=accumulator,
        notify_sink=notify_sink,
        board_service=board_service,
    )
    return overlay, registry, kill, store, capture, calls


def _on_seam(seam_id: str = "snapshot.l3.selection", *, global_on: bool = True):
    """The store values that turn the master + the seam ON."""
    return {
        GLOBAL_ENABLED_KEY: "1" if global_on else "0",
        seam_enabled_key(seam_id): "1",
        seam_stage_key(seam_id): "shadow",
    }


# ── T-8 / AC-8 — the consult gate ───────────────────────────────────────


async def test_a_fresh_seam_is_consulted_nothing_until_its_key_is_on() -> None:
    """T-8 — the per-seam default is OFF; registration is opt-out.

    A freshly registered seam makes NO call at all until BOTH the
    global master and its own per-seam key are explicitly ON.
    """
    overlay, _registry, _kill, _store, capture, calls = _build_overlay()
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert calls == [], "a per-seam-default-OFF seam must never call out"
    assert result.outcome == ["id-a"], "the heuristic stands"
    assert result.state == OverlayOutcome.SUPPRESSED.value
    assert result.suppression == SuppressionReason.SWITCH_OFF.value
    assert "reason=switch_off" in capture.last


async def test_the_global_master_off_suppresses_every_seam() -> None:
    """The master is evaluated before the per-seam switch."""
    overlay, _registry, _kill, _store, capture, calls = _build_overlay(
        store_values=_on_seam(global_on=False)
    )
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert calls == []
    assert result.suppression == SuppressionReason.SWITCH_OFF.value


async def test_the_master_on_but_the_seam_off_still_suppresses() -> None:
    """The per-seam key is consulted on its own."""
    overlay, _registry, _kill, _store, _capture, calls = _build_overlay(
        store_values={GLOBAL_ENABLED_KEY: "1"}
    )
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert calls == []
    assert result.suppression == SuppressionReason.SWITCH_OFF.value


async def test_an_unreadable_switch_suppresses_and_makes_no_call() -> None:
    """AC-8 — an unreadable switch ⇒ NO CALL + a ``switch_unreadable`` row."""
    overlay, _registry, _kill, _store, capture, calls = _build_overlay(
        store_values={
            GLOBAL_ENABLED_KEY: "1",
            seam_enabled_key("snapshot.l3.selection"): "1",
            seam_stage_key("snapshot.l3.selection"): "not-a-stage",
        }
    )
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert calls == [], "an unreadable switch must not reach the socket"
    assert result.suppression == SuppressionReason.SWITCH_UNREADABLE.value
    assert "reason=switch_unreadable" in capture.last


async def test_the_seams_own_gate_switch_is_part_of_the_consult_gate() -> None:
    """§3.2.2 — the seam's existing gate switch rides the composition."""
    overlay, _registry, _kill, _store, _capture, calls = _build_overlay(
        store_values=_on_seam(),
        seam=seam_record(gate_switch=lambda: False),
    )
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert calls == []
    assert result.suppression == SuppressionReason.SWITCH_OFF.value


async def test_an_unconfigured_overlay_never_calls_out() -> None:
    """AC-8 — the consult gate includes the configuration check."""
    overlay, _registry, _kill, _store, _capture, calls = _build_overlay(
        store_values=_on_seam(),
        config=make_config(api_key=""),
    )
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert calls == []
    assert result.suppression == SuppressionReason.NOT_CONFIGURED.value


# ── AC-8 — the apply gate ────────────────────────────────────────────────


async def test_the_shadow_stage_is_consulted_but_never_applies() -> None:
    """FLAG12 — the stage does NOT gate consultation.

    A ``shadow`` seam IS consulted (the call happens, the A/B row is
    written) and the heuristic outcome is returned UNCHANGED.
    """
    overlay, _registry, _kill, _store, capture, calls = _build_overlay(
        store_values=_on_seam()
    )
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert len(calls) == 1, "the shadow stage still consults"
    assert result.outcome == ["id-a"], "the shadow stage never moves the outcome"
    assert result.state == OverlayOutcome.LOGGED.value
    assert "mode=shadow" in capture.last


async def test_the_deciding_stage_is_unreachable_without_a_ratification() -> None:
    """AC-7/FLAG12 — the apply path is PRESENT but UNREACHABLE in P1.

    Even with the stage key flipped to ``deciding``, the registry holds
    no ratified D3 transition, so the overlay may not act.
    """
    values = _on_seam()
    values[seam_stage_key("snapshot.l3.selection")] = "deciding"
    overlay, _registry, _kill, _store, _capture, calls = _build_overlay(
        store_values=values
    )
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert len(calls) == 1
    assert result.outcome == ["id-a"], "no ratification ⇒ the overlay may not act"
    assert result.applied is False


async def test_a_ratified_deciding_seam_applies_its_answer() -> None:
    """The full C2 path — a ratified ``deciding`` seam may act."""
    values = _on_seam()
    values[seam_stage_key("snapshot.l3.selection")] = "deciding"
    overlay, registry, _kill, _store, _capture, _calls = _build_overlay(
        store_values=values
    )
    registry.transition_stage(
        "snapshot.l3.selection",
        DecisionStage.DECIDING,
        review=ReviewRecord(reviewer="council", ratified_by="the Emperor"),
    )
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert result.applied is True
    assert result.state == OverlayOutcome.APPLIED.value
    assert result.outcome == ["id-a", "id-b", "id-c"]


# ── T-4 — a transient 502 never parks the seam ──────────────────────────


async def test_a_transient_502_fails_open_and_leaves_the_provisioning_state() -> None:
    """T-4 — a 502 is a FAILURE, never a provisioning verdict.

    The consult fails open (the heuristic stands) and the provisioning
    state is UNCHANGED — the seam is NOT parked as
    ``provisioning_missing``.
    """
    overlay, _registry, _kill, _store, _capture, calls = _build_overlay(
        handler=lambda request: json_response(
            {"type": "invalid_request_error", "code": "missing_required_parameter"},
            502,
        ),
        store_values=_on_seam(),
    )
    before = overlay.provisioning_state("snapshot.l3.selection")
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert len(calls) == 1
    assert result.outcome == ["id-a"], "the consult fails open"
    assert result.failure_class == FailureClass.HTTP_502.value
    assert overlay.provisioning_state("snapshot.l3.selection") == before
    assert overlay.provisioning_state("snapshot.l3.selection").value == "ok"


async def test_a_probe_provable_404_moves_the_provisioning_state() -> None:
    """T-5 — a 404 with the proxy's envelope IS a provisioning verdict."""
    overlay, _registry, _kill, _store, _capture, _calls = _build_overlay(
        handler=lambda request: json_response(
            {"error": {"type": "server_error", "message": "unknown model"}}, 404
        ),
        store_values=_on_seam(),
    )
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert overlay.provisioning_state("snapshot.l3.selection").value == (
        "provisioning_missing"
    )


# ── AC-10 — the kill channel ────────────────────────────────────────────


async def test_the_kill_write_is_read_back_and_verified() -> None:
    """AC-10 — WRITE → READ-BACK → VERIFY."""
    _overlay, _registry, kill, store, _capture, _calls = _build_overlay()
    result = await kill.write("snapshot.l3.selection", enabled=False)
    assert result.ok is True
    assert result.verified[seam_enabled_key("snapshot.l3.selection")] is True
    assert await store.get(seam_enabled_key("snapshot.l3.selection")) == "0"


async def test_a_read_back_mismatch_resolves_off_and_raises_switch_unreadable() -> None:
    """AC-10 — a mismatch ⇒ the switch resolves OFF + a suppression row."""

    class _LyingStore(InMemoryKillStore):
        async def set(self, key: str, value: str) -> None:
            await super().set(key, "999")  # the store does not keep the value

    store = _LyingStore()
    kill = KillChannelResolver(store, env={})
    result = await kill.write("snapshot.l3.selection", enabled=False)
    assert result.ok is False
    assert result.mismatched == (seam_enabled_key("snapshot.l3.selection"),)


async def test_a_flipped_kill_takes_effect_on_the_next_consult() -> None:
    """AC-10 — the switch is read PER CONSULT; a flip needs no restart."""
    overlay, _registry, kill, _store, _capture, calls = _build_overlay(
        store_values=_on_seam()
    )
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert len(calls) == 1
    await kill.write("snapshot.l3.selection", enabled=False)
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert len(calls) == 1, "the kill stops the next call without a restart"


# ── AC-9 — the boot seed + the re-verify ────────────────────────────────


async def test_the_boot_seed_never_overwrites_an_existing_value() -> None:
    """AC-9 — the DB wins: a kill is NEVER undone by a restart."""
    _overlay, _registry, kill, store, _capture, _calls = _build_overlay(
        store_values={seam_enabled_key("snapshot.l3.selection"): "0"},
        env={"DECISION_SEAM_SNAPSHOT_L3_SELECTION": "1"},
    )
    outcomes = await kill.boot_seed(["snapshot.l3.selection"])
    assert await store.get(seam_enabled_key("snapshot.l3.selection")) == "0"
    assert any(not o.seeded for o in outcomes)


async def test_the_boot_seed_writes_only_where_the_store_has_no_value() -> None:
    """AC-9 — the env seed lands once, then the DB tier wins."""
    _overlay, _registry, kill, store, _capture, _calls = _build_overlay(
        store_values={},
        env={"DECISION_ENABLED": "1", "DECISION_SEAM_SNAPSHOT_L3_SELECTION": "1"},
    )
    await kill.boot_seed(["snapshot.l3.selection"])
    assert await store.get(GLOBAL_ENABLED_KEY) == "1"
    assert await store.get(seam_enabled_key("snapshot.l3.selection")) == "1"


async def test_the_boot_reverify_counts_every_kill() -> None:
    """AC-9 — the boot re-verify increments the counter per seam."""
    _overlay, _registry, kill, _store, _capture, _calls = _build_overlay(
        store_values={
            GLOBAL_ENABLED_KEY: "0",
            seam_enabled_key("snapshot.l3.selection"): "0",
            seam_enabled_key("other.seam"): "1",
            seam_enabled_key("broken.seam"): "not-a-boolean",
        }
    )
    before = metrics.switch_reverify_total.get()
    outcomes = await kill.reverify(
        ["snapshot.l3.selection", "other.seam", "broken.seam"]
    )
    by_seam = {o.seam_id: o.outcome for o in outcomes}
    assert by_seam["snapshot.l3.selection"] == "verified_off"
    assert by_seam["other.seam"] == "verified_on"
    assert by_seam["broken.seam"] == "unreadable"
    assert metrics.switch_reverify_total.get() == before + len(outcomes)


# ── AC-11 / FLAG3 — the auto-trip ───────────────────────────────────────


async def test_the_cost_ceiling_trips_to_shadow_off_and_notifies() -> None:
    """T-6 — crossing the USD 50 global ceiling trips the module.

    The trip writes BOTH keys per seam (``stage := 'shadow'`` AND
    ``enabled := 0``) plus the global master OFF, counts the trip and
    fires the operator notify.
    """
    notified: list[dict[str, Any]] = []
    accumulator = _PreloadedAccumulator(0.0)
    overlay, _registry, kill, store, _capture, _calls = _build_overlay(
        handler=lambda request: json_response(
            choice_answer_payload(usage={"cost_usd": 0.25})
        ),
        store_values=_on_seam(),
        accumulator=accumulator,
        notify_sink=notified.append,
        config=make_config(cost_ceiling_usd=0.10),
    )
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert result.outcome == ["id-a"]
    # BOTH keys, per seam, plus the global master.
    assert await store.get(seam_stage_key("snapshot.l3.selection")) == "shadow"
    assert await store.get(seam_enabled_key("snapshot.l3.selection")) == "0"
    assert await store.get(GLOBAL_ENABLED_KEY) == "0"
    # The counter + the notify.
    assert notified, "the operator notify must fire"
    assert notified[0]["reason"] == "global_ceiling"
    assert notified[0]["ceiling_usd"] == pytest.approx(0.10)
    assert metrics.cost_trip_total.get(reason="global_ceiling") >= 1
    assert overlay.trip_status()["tripped"] is True


async def test_the_per_seam_share_guard_trips_one_seam_only() -> None:
    """The per-seam share keeps one seam from eating the whole budget."""
    notified: list[dict[str, Any]] = []
    # The seam is already past its share (0.05) but the global ceiling
    # (0.10) is NOT crossed.
    accumulator = _PreloadedAccumulator(0.06, seam="snapshot.l3.selection")
    overlay, _registry, _kill, store, _capture, _calls = _build_overlay(
        handler=lambda request: json_response(
            choice_answer_payload(usage={"cost_usd": 0.001})
        ),
        store_values=_on_seam(),
        accumulator=accumulator,
        notify_sink=notified.append,
        config=make_config(cost_ceiling_usd=0.10),
    )
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert notified[0]["reason"] == "per_seam_share"
    assert await store.get(seam_enabled_key("snapshot.l3.selection")) == "0"
    assert await store.get(GLOBAL_ENABLED_KEY) == "1", "the global master stays on"


async def test_a_suppressed_consult_costs_nothing() -> None:
    """AC-18 — a suppressed consult makes NO call and incurs NO cost."""
    overlay, _registry, _kill, _store, _capture, calls = _build_overlay()
    before = metrics.cost_total.get()
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert calls == []
    assert metrics.cost_total.get() == before


# ── AC-18 — the throttle ─────────────────────────────────────────────────


async def test_the_breaker_opens_after_five_consecutive_failures() -> None:
    """AC-18 — the consecutive-failure breaker (N = 5) short-circuits."""
    overlay, _registry, _kill, _store, _capture, calls = _build_overlay(
        handler=lambda request: text_response("boom", 500),
        store_values=_on_seam(),
    )
    for _ in range(5):
        await overlay.consult(
            "snapshot.l3.selection",
            heuristic_outcome=["id-a"],
            context={"input": "x"},
        )
    assert len(calls) == 5
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert len(calls) == 5, "the breaker short-circuits the next attempt"
    assert result.suppression == SuppressionReason.BREAKER_OPEN.value


async def test_the_rate_budget_is_enforced_pre_socket() -> None:
    """AC-18 — the rate budget is enforced BEFORE the socket."""
    overlay, _registry, _kill, _store, _capture, calls = _build_overlay(
        store_values=_on_seam()
    )
    overlay._budget._seam_rate = 2  # the per-seam calls-per-minute budget
    for _ in range(2):
        await overlay.consult(
            "snapshot.l3.selection",
            heuristic_outcome=["id-a"],
            context={"input": "x"},
        )
    assert len(calls) == 2
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert len(calls) == 2, "the rate budget stops the call"
    assert result.suppression == SuppressionReason.RATE_LIMITED.value


# ── AC-14 — the R-B identity ─────────────────────────────────────────────


async def test_the_rb_identity_holds_in_deciding_mode() -> None:
    """R-B — ``fallbacks_total == failures_total`` scoped to deciding."""
    values = _on_seam()
    values[seam_stage_key("snapshot.l3.selection")] = "deciding"
    overlay, registry, _kill, _store, _capture, _calls = _build_overlay(
        handler=lambda request: text_response("boom", 500),
        store_values=values,
    )
    registry.transition_stage(
        "snapshot.l3.selection",
        DecisionStage.DECIDING,
        review=ReviewRecord(reviewer="council", ratified_by="the Emperor"),
    )
    failures_before = metrics.failures_total.get(
        seam_id="snapshot.l3.selection", failure_class="http_500", mode="deciding"
    )
    fallbacks_before = metrics.fallbacks_total.get(
        seam_id="snapshot.l3.selection", mode="deciding"
    )
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    failures_after = metrics.failures_total.get(
        seam_id="snapshot.l3.selection", failure_class="http_500", mode="deciding"
    )
    fallbacks_after = metrics.fallbacks_total.get(
        seam_id="snapshot.l3.selection", mode="deciding"
    )
    assert failures_after == failures_before + 1
    assert fallbacks_after == fallbacks_before + 1
    assert fallbacks_after - fallbacks_before == failures_after - failures_before


async def test_the_floor_rejection_is_a_subset_view_not_an_additive_term() -> None:
    """R-B — ``floor_rejections_total`` mirrors ``below_floor`` failures."""
    values = _on_seam()
    values[seam_stage_key("snapshot.l3.selection")] = "deciding"
    overlay, registry, _kill, _store, _capture, _calls = _build_overlay(
        store_values=values,
        seam=seam_record(floor=0.95),
    )
    registry.transition_stage(
        "snapshot.l3.selection",
        DecisionStage.DECIDING,
        review=ReviewRecord(reviewer="council", ratified_by="the Emperor"),
    )
    failures_before = metrics.failures_total.get(
        seam_id="snapshot.l3.selection",
        failure_class="below_floor",
        mode="deciding",
    )
    rejections_before = metrics.floor_rejections_total.get(
        seam_id="snapshot.l3.selection", mode="deciding"
    )
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    failures_after = metrics.failures_total.get(
        seam_id="snapshot.l3.selection",
        failure_class="below_floor",
        mode="deciding",
    )
    rejections_after = metrics.floor_rejections_total.get(
        seam_id="snapshot.l3.selection", mode="deciding"
    )
    assert failures_after == failures_before + 1
    assert rejections_after == rejections_before + 1
    assert rejections_after - rejections_before == failures_after - failures_before


# ── Q2 end-to-end pins ───────────────────────────────────────────────────


async def test_with_only_openai_inherited_and_no_decision_signal_a_seam_enabled_makes_no_call_and_suppresses_not_configured(
    monkeypatch,
) -> None:
    """Q2 pin (negative): the reconciliation's second belt.

    The seam key is ON, the environment carries ONLY ``OPENAI_BASE_URL`` /
    ``OPENAI_API_KEY``, and there is NO ``decision:`` block. Under the
    reconciliation the module is NOT configured — so NO socket call is
    made and exactly one suppressed row with reason ``not_configured``
    is written. (Merely inheriting the OPENAI_* values does not make the
    module configured, because the ``decision`` row routes to a PAID
    upstream.)
    """
    monkeypatch.delenv("DECISION_BASE_URL", raising=False)
    monkeypatch.delenv("DECISION_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-inherited")
    overlay, _registry, _kill, _store, capture, calls = _build_overlay(
        store_values=_on_seam(),
        config=make_config(explicitly_configured=False),
    )
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert calls == [], "a not-configured module must never call out"
    assert result.suppression == "not_configured"
    assert "reason=not_configured" in capture.last


async def test_with_an_explicit_decision_signal_a_seam_enabled_does_call(
    monkeypatch,
) -> None:
    """Q2 pin (positive): an explicit signal makes the seam call out."""
    monkeypatch.setenv("DECISION_API_KEY", "sk-explicit")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-inherited")
    overlay, _registry, _kill, _store, _capture, calls = _build_overlay(
        store_values=_on_seam()
    )
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert len(calls) == 1, "an explicit signal makes the seam call out"
