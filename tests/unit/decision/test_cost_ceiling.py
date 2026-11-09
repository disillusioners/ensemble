"""The cost ceiling (L8) and the operator notify (L9).

L8 — there is NO pricing table in the ensemble or in the proxy: the
proxy meters TOKENS. So the ceiling is computed WITHOUT inventing a
price:

* (a) the provider RESPONDS with a cost → the USD accumulator;
* (b) otherwise an OPERATOR-CONFIGURED per-model USD-per-million-token
  rate converts the metered tokens;
* (c) if neither is available → the consult is metered in TOKENS, a
  ``pricing_unconfigured`` suppressed row is emitted, and the configured
  TOKEN ceiling is the fail-safe.

L9 — the operator notify uses the BOARD SINK: ``BoardService.create_item``
is SYNC, opens its own Session and its own transaction, and is guarded
by a process-level RLock ⇒ safe to call from a background thread. The
overlay calls it DIRECTLY (sync), off the event loop.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from daemon.services.decision import metrics
from daemon.services.decision.kill import (
    GLOBAL_ENABLED_KEY,
    seam_enabled_key,
    seam_stage_key,
)
from tests.unit.decision.helpers import (
    choice_answer_payload,
    json_response,
    make_config,
    smart_handler,
)
from tests.unit.decision.test_overlay import _build_overlay, _on_seam


def _usage_handler(usage: dict[str, Any] | None):
    """A handler returning a usage block (or none)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(choice_answer_payload(usage=usage))

    return handler


# ── (a) the provider-reported cost ───────────────────────────────────────


async def test_a_reported_cost_is_metered_in_usd() -> None:
    """L8 (a) — the provider's reported cost is the USD spend."""
    overlay, _registry, _kill, _store, _capture, _calls = _build_overlay(
        handler=_usage_handler({"cost_usd": 0.25, "total_tokens": 1000}),
        store_values=_on_seam(),
        config=make_config(cost_ceiling_usd=50.0),
    )
    before = metrics.cost_total.get(seam_id="snapshot.l3.selection")
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    after = metrics.cost_total.get(seam_id="snapshot.l3.selection")
    assert after == pytest.approx(before + 0.25)


# ── (b) the operator rate table ──────────────────────────────────────────


async def test_the_operator_rate_converts_the_metered_tokens() -> None:
    """L8 (b) — an operator-configured per-model rate converts the tokens.

    1 000 000 tokens at 1.20 USD per million = 1.20 USD.
    """
    overlay, _registry, _kill, _store, _capture, _calls = _build_overlay(
        handler=_usage_handler({"total_tokens": 1_000_000}),
        store_values=_on_seam(),
        config=make_config(
            cost_ceiling_usd=50.0,
            pricing_usd_per_million_tokens={"decision": 1.20},
        ),
    )
    before = metrics.cost_total.get(seam_id="snapshot.l3.selection")
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    after = metrics.cost_total.get(seam_id="snapshot.l3.selection")
    assert after == pytest.approx(before + 1.20)


async def test_the_operator_rate_can_trip_the_ceiling() -> None:
    """L8 (b) — the converted spend trips the ceiling like any other."""
    notified: list[dict[str, Any]] = []
    overlay, _registry, _kill, store, _capture, _calls = _build_overlay(
        handler=_usage_handler({"total_tokens": 1_000_000}),
        store_values=_on_seam(),
        notify_sink=notified.append,
        config=make_config(
            cost_ceiling_usd=1.0,
            pricing_usd_per_million_tokens={"decision": 1.20},
        ),
    )
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert notified, "the trip must notify"
    assert notified[0]["reason"] == "global_ceiling"
    assert await store.get(seam_enabled_key("snapshot.l3.selection")) == "0"
    assert await store.get(seam_stage_key("snapshot.l3.selection")) == "shadow"
    assert await store.get(GLOBAL_ENABLED_KEY) == "0"


# ── (c) the token fallback + the visible degradation ─────────────────────


async def test_without_a_rate_the_tokens_are_metered_and_the_degradation_is_visible() -> None:
    """L8 (c) — no rate ⇒ the TOKEN meter + a ``pricing_unconfigured`` row.

    The control is never ABSENT: it degrades VISIBLY.
    """
    capture_rows: list[str] = []
    overlay, _registry, _kill, _store, _capture, _calls = _build_overlay(
        handler=_usage_handler({"total_tokens": 4321}),
        store_values=_on_seam(),
        config=make_config(cost_ceiling_usd=50.0),
    )
    before_tokens = metrics.cost_tokens_total.get(seam_id="snapshot.l3.selection")
    before_suppressed = metrics.suppressed_total.get(
        seam_id="snapshot.l3.selection", reason="pricing_unconfigured"
    )
    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert result.outcome == ["id-a"]
    assert metrics.cost_tokens_total.get(
        seam_id="snapshot.l3.selection"
    ) == pytest.approx(before_tokens + 4321)
    assert metrics.suppressed_total.get(
        seam_id="snapshot.l3.selection", reason="pricing_unconfigured"
    ) == before_suppressed + 1


async def test_the_token_ceiling_is_the_fail_safe() -> None:
    """L8 (c) — the TOKEN ceiling trips the module when no USD rate exists."""
    notified: list[dict[str, Any]] = []
    overlay, _registry, _kill, store, _capture, _calls = _build_overlay(
        handler=_usage_handler({"total_tokens": 500}),
        store_values=_on_seam(),
        notify_sink=notified.append,
        config=make_config(cost_ceiling_usd=50.0, token_ceiling=100),
    )
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert notified, "the token ceiling must trip and notify"
    assert await store.get(seam_enabled_key("snapshot.l3.selection")) == "0"
    assert await store.get(GLOBAL_ENABLED_KEY) == "0"


async def test_the_attempt_counter_is_incremented_before_the_outcome() -> None:
    """§3.5.3 — every socket-reaching attempt is counted, whatever the outcome."""
    overlay, _registry, _kill, _store, _capture, _calls = _build_overlay(
        handler=_usage_handler({"total_tokens": 10}),
        store_values=_on_seam(),
    )
    before = metrics.attempts_total.get(
        seam_id="snapshot.l3.selection", mode="shadow"
    )
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    after = metrics.attempts_total.get(
        seam_id="snapshot.l3.selection", mode="shadow"
    )
    assert after == before + 1


# ── L9 — the board notify ────────────────────────────────────────────────


class _FakeBoardService:
    """A stand-in for ``BoardService`` — records ``create_item`` calls."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def create_item(self, **kwargs: Any) -> dict[str, Any]:
        self.items.append(kwargs)
        return {"id": "board-1"}


async def test_the_board_sink_carries_the_notify() -> None:
    """L9 — the operator notify writes a BOARD ITEM (the always surface)."""
    board = _FakeBoardService()
    overlay, _registry, _kill, store, _capture, _calls = _build_overlay(
        handler=_usage_handler({"cost_usd": 0.25}),
        store_values=_on_seam(),
        board_service=board,
        config=make_config(cost_ceiling_usd=0.10),
    )
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert board.items, "the board item must be written"
    item = board.items[0]
    assert "cost ceiling" in item["title"]
    assert item["created_by_instance"] == "ensemble-daemon"
    assert "decision-overlay" in item["tags"]
    assert overlay.trip_status()["board_item"] is True


async def test_without_a_board_service_the_notify_degrades_visibly() -> None:
    """L9 — no board service ⇒ counter + WARNING + the trip status."""
    overlay, _registry, _kill, store, _capture, _calls = _build_overlay(
        handler=_usage_handler({"cost_usd": 0.25}),
        store_values=_on_seam(),
        config=make_config(cost_ceiling_usd=0.10),
    )
    before = metrics.cost_trip_total.get(reason="global_ceiling")
    await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
    )
    assert metrics.cost_trip_total.get(reason="global_ceiling") == before + 1
    status = overlay.trip_status()
    assert status["tripped"] is True
    assert status["board_item"] is False
    assert status["reason"] == "global_ceiling"
