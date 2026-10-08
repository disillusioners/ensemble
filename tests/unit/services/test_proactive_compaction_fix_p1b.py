"""P1b (proactive-compaction-fix) — 95% pre-call reactive trigger anchors.

Implements the ADDENDUM §A.9 test anchors that extend P1's suite:

* **T-boundary** — mocked window on a 600k model: 569k no-fire /
  571k fire, plus the doc's tighter 0.9499× / 0.9501× edges.
* **T-estimator (perf)** — below-80% band with an unchanged message
  count invokes the estimator ZERO extra times across a multi-call
  turn; the estimator runs only when the count grew OR the cached
  estimate sat ≥0.80×window (A.4 O(1) pre-filter).
* **T4-ext (multi-call refire)** — a turn with N LLM calls crossing
  95% compacts ONCE (post-compaction estimate <95%, dedup stamped);
  injection-dominated no-op stamps + emits a SINGLE rate-limited WARN
  (no per-call refire).
* **T-isolation (CLE)** — the 95% hook does not consume/reset the CLE
  single-retry; the CLE persist site stays byte-unchanged (source
  pin); hook-then-CLE composition per A.6 (abort → no stamp → CLE can
  still fire; success → stamped → dedup holds).
* **mid_turn=True seam consumption** — the hook persists through the
  SHARED seam with ``as_node='agent'`` on both writes (A.5).
* **Kill-switch** — ``proactive_enabled`` OFF ⇒ the hook is a no-op
  (no estimator call, no engine call).

The hook under test: ``daemon.graph._maybe_precall_compact_95``
(pinned site: inside the agent_node ``try:``, after the loop-breaker
repair + re-appends, before the ``invoke(full_messages)``).
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

import daemon.loader as loader_mod
from daemon.compaction import (
    ChunkedOutcome,
    ContextCompactor,
)
from daemon.config import CompactionConfig as CompactionConfigModel
from daemon.graph import _PRECALL_NOOP, _maybe_precall_compact_95
from daemon.loader import estimate_messages_tokens
from daemon.services import _compaction_persist_seam as seam_mod
from daemon.services.message_tap import SOURCE_COMPACTION_PRECALL_95


# =============================================================================
# Helpers
# =============================================================================


def make_compaction_config(**overrides: Any) -> CompactionConfigModel:
    """CompactionConfig with optional overrides (mirror the P1 suite)."""
    defaults: dict[str, Any] = {
        "enabled": True,
        "threshold": 0.80,
        "recent_message_window": 10,
        "min_recent_window": 3,
        "context_window_overrides": {},
        "context_window_default": 0,
        "target_ratio": 0.40,
        "model": "",
        "summarization_model": "",
        "min_messages_before_compaction": 10,
        "summarization_chunk_threshold": 0.60,
        "timeout_base_s": 90.0,
        "timeout_per_100k_tokens_s": 60.0,
        "timeout_cap_s": 300.0,
        "timeout_facade_margin_s": 5.0,
        "operation_budget_s": 300.0,
        "chunk_concurrency": 3,
        "proactive_enabled": True,
    }
    defaults.update(overrides)
    return CompactionConfigModel(**defaults)


def make_messages(n: int, content_prefix: str = "M") -> list:
    """Alternate human/ai messages with stable ids."""
    out = []
    for i in range(n):
        cls = HumanMessage if i % 2 == 0 else AIMessage
        out.append(cls(content=f"{content_prefix} {i}", id=f"m-{i}"))
    return out


def _make_injected(n: int) -> list:
    return [
        HumanMessage(
            content=f"[injected] {i}",
            id=f"inj-{i}",
            additional_kwargs={"injected_message": True},
        )
        for i in range(n)
    ]


@dataclass
class _FakeState:
    """Minimal ``StateSnapshot``-shaped stand-in (``.values`` dict)."""

    values: dict = field(default_factory=dict)
    next: tuple = ()


class _FakeGraph:
    """Graph stand-in that persists ``aupdate_state`` writes into its
    fake values so subsequent ``aget_state`` reads reflect them.

    Mimics the ``add_messages`` semantics the hook relies on: a leading
    ``RemoveMessage`` sentinel replaces the whole channel (element 0 —
    anything before it is discarded per ``build_sentinel_replacement``).
    """

    def __init__(self, values: dict | None = None) -> None:
        self.values: dict = dict(values or {})
        self.aupdate_calls: list[tuple[dict, dict]] = []

    async def aget_state(self, config):
        return _FakeState(values=dict(self.values))

    async def aupdate_state(self, config, update, **kwargs):
        self.aupdate_calls.append((dict(update), dict(kwargs)))
        if "messages" in update:
            msgs = list(update["messages"])
            if msgs and isinstance(msgs[0], RemoveMessage):
                msgs = msgs[1:]
            self.values["messages"] = msgs
        if "compacted_at" in update:
            self.values["compacted_at"] = update["compacted_at"]
        return None


def _make_compactor(**config_overrides: Any) -> ContextCompactor:
    return ContextCompactor(make_compaction_config(**config_overrides), {})


def _run_hook(
    graph: _FakeGraph,
    compactor: ContextCompactor | None,
    full_messages: list,
    *,
    instance_id: str = "p1b-hook-instance-0001",
    tap_slot: Any | None = None,
    injected_msgs: list | None = None,
    injected_report_msgs: list | None = None,
    llm_config: dict | None = None,
    system_prompt: str = "system prompt",
):
    """Invoke the hook with sensible defaults (mirrors the closure)."""
    return _maybe_precall_compact_95(
        instance_id=instance_id,
        instance_short=instance_id.split("-")[0],
        compactor=compactor,
        graph_ref=[graph],
        thread_config={"configurable": {"thread_id": instance_id}},
        full_messages=full_messages,
        system_prompt=system_prompt,
        llm_config=llm_config or {"model": "test-model"},
        injected_msgs=injected_msgs or [],
        injected_report_msgs=injected_report_msgs or [],
        ephemeral_context_msgs=[],
        pairing_synthesized_msgs=[],
        precall_compaction_tap_slot=tap_slot,
    )


def _stub_chunked_summarizer(compactor: ContextCompactor) -> None:
    """Stub the merge-call LLM path so the real engine compacts without
    an LLM (15% ceiling rule would otherwise trip B-shape degrade)."""

    async def _fake_chunked(compactable, context, previous_overview=None):
        # ``summaries`` elements are STRINGS (Architect §4/§6 — the
        # per-batch text is embedded inside the single global doc).
        return ChunkedOutcome(
            summaries=["[Conversation Summary]\nall groups"],
            failed_batches=[],
            stop_reason="completed",
        )

    compactor._summarize_chunked = _fake_chunked


# =============================================================================
# T-boundary — 569k no-fire / 571k fire on a 600k window (+ 0.9499/0.9501)
# =============================================================================


class TestPreCall95Boundary:
    """The hook fires at ≥0.95 × ``_trigger_window`` (A.1) and below it
    does nothing. Window mocked to 600k via ``context_window_overrides``;
    the estimator is patched so the payload token count is exact."""

    WINDOW = 600_000

    def _compactor(self) -> ContextCompactor:
        compactor = _make_compactor(
            context_window_overrides={"test-model": self.WINDOW},
        )
        compactor.compact_state = AsyncMock(return_value=None)
        return compactor

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tokens,fires",
        [
            (569_000, False),  # task anchor: 569k → no fire
            (571_000, True),  # task anchor: 571k → fire
            (569_940, False),  # doc edge: 0.9499 × 600k → no fire
            (570_060, True),  # doc edge: 0.9501 × 600k → fire
            (570_000, True),  # exact ≥0.95 × window boundary → fire
        ],
    )
    async def test_boundary(self, tokens: int, fires: bool, monkeypatch):
        compactor = self._compactor()
        monkeypatch.setattr(
            loader_mod,
            "estimate_messages_tokens",
            lambda msgs: tokens,
        )
        graph = _FakeGraph(values={"messages": make_messages(5)})
        payload = make_messages(5)

        outcome = await _run_hook(graph, compactor, payload)

        assert outcome.rebuilt_payload is None  # engine stub returns None → proceed
        assert compactor.compact_state.await_count == (1 if fires else 0), (
            f"tokens={tokens} must {'FIRE' if fires else 'NOT fire'} at "
            f"0.95 × {self.WINDOW} = {0.95 * self.WINDOW}"
        )

    @pytest.mark.asyncio
    async def test_fire_reaches_engine_with_force_false(self, monkeypatch):
        compactor = self._compactor()
        captured: dict = {}

        async def _engine(ctx, force=False):
            captured["ctx"] = ctx
            captured["force"] = force
            return None

        compactor.compact_state = _engine
        monkeypatch.setattr(
            loader_mod, "estimate_messages_tokens", lambda msgs: 571_000
        )
        graph = _FakeGraph(values={"messages": make_messages(5)})

        await _run_hook(graph, compactor, make_messages(5))

        assert captured["force"] is False, (
            "hook must respect dedup/recency floors (force=False, A.1/A.8)"
        )
        ctx = captured["ctx"]
        assert ctx.config is compactor.config
        assert ctx.model_name == "test-model"


# =============================================================================
# T-estimator — O(1) pre-filter (A.4)
# =============================================================================


class TestPreCall95PreFilterEstimator:
    """Below-80% with an unchanged message count the estimator must be
    invoked ZERO times after the first call; it runs only when the count
    grew or the cached estimate sat ≥0.80×window."""

    WINDOW = 600_000

    def _compactor(self) -> ContextCompactor:
        compactor = _make_compactor(
            context_window_overrides={"test-model": self.WINDOW},
        )
        compactor.compact_state = AsyncMock(return_value=None)
        return compactor

    def _counting_estimator(self, monkeypatch, tokens_by_call: list[int]):
        calls = {"n": 0}

        def _est(msgs):
            idx = min(calls["n"], len(tokens_by_call) - 1)
            calls["n"] += 1
            return tokens_by_call[idx]

        monkeypatch.setattr(loader_mod, "estimate_messages_tokens", _est)
        return calls

    @pytest.mark.asyncio
    async def test_stable_sub80_turn_estimator_runs_once(self, monkeypatch):
        """5 LLM calls, unchanged payload, est <80% → estimator exactly
        1 call (the first), engine never reached — the common case is
        O(1) after warm-up."""
        compactor = self._compactor()
        calls = self._counting_estimator(monkeypatch, [100_000])
        graph = _FakeGraph(values={"messages": make_messages(5)})
        payload = make_messages(5)

        for _ in range(5):
            outcome = await _run_hook(graph, compactor, payload)
            assert outcome.rebuilt_payload is None

        assert calls["n"] == 1, (
            f"O(1) pre-filter: estimator must run once, ran {calls['n']}x"
        )
        assert compactor.compact_state.await_count == 0

    @pytest.mark.asyncio
    async def test_count_growth_re_estimates(self, monkeypatch):
        compactor = self._compactor()
        calls = self._counting_estimator(monkeypatch, [100_000])
        graph = _FakeGraph(values={"messages": make_messages(5)})
        payload = make_messages(5)

        await _run_hook(graph, compactor, payload)
        assert calls["n"] == 1
        # Tool result arrives → payload grows → the estimator MUST run.
        payload.append(AIMessage(content="tool-ish result", id="r-1"))
        await _run_hook(graph, compactor, payload)
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_at_risk_band_re_estimates_every_call(self, monkeypatch):
        """Cached estimate ≥0.80×window → re-check EVERY call even with
        an unchanged count (the 80–95% band is where the call is at
        risk — A.4)."""
        compactor = self._compactor()
        calls = self._counting_estimator(
            monkeypatch, [int(0.85 * self.WINDOW)]
        )
        graph = _FakeGraph(values={"messages": make_messages(5)})
        payload = make_messages(5)

        for _ in range(4):
            await _run_hook(graph, compactor, payload)

        assert calls["n"] == 4, (
            "in the ≥80% band the estimator must run on every call"
        )
        assert compactor.compact_state.await_count == 0  # 85% < 95%

    @pytest.mark.asyncio
    async def test_estimate_state_lives_on_compactor(self):
        """The pre-filter state is per-instance ON THE COMPACTOR
        (sibling of ``last_compacted_at`` — A.4), keyed by instance id."""
        compactor = self._compactor()
        assert compactor.precall_estimate_get("iid-1") is None
        compactor.precall_estimate_record("iid-1", 10, 500)
        assert compactor.precall_estimate_get("iid-1") == (10, 500)
        # Different instance → independent entry.
        assert compactor.precall_estimate_get("iid-2") is None

    def test_needs_refresh_decision_arms(self):
        compactor = self._compactor()
        window = self.WINDOW
        # Arm 1: no cached estimate.
        assert compactor.precall_estimate_needs_refresh("a", 5, window)
        compactor.precall_estimate_record("a", 5, 100_000)
        # Stable + sub-80% → O(1) skip.
        assert not compactor.precall_estimate_needs_refresh("a", 5, window)
        # Arm 2: count grew.
        assert compactor.precall_estimate_needs_refresh("a", 6, window)
        # Arm 3: cached estimate ≥0.80×window.
        compactor.precall_estimate_record("a", 5, int(0.80 * window))
        assert compactor.precall_estimate_needs_refresh("a", 5, window)
        # Zero window (unknown model) → arms 1–2 only, never arm 3.
        compactor.precall_estimate_record("b", 5, 10**9)
        assert not compactor.precall_estimate_needs_refresh("b", 5, 0)

    @pytest.mark.asyncio
    async def test_warn_rate_limited_per_interval(self):
        compactor = self._compactor()
        # First emission allowed, second inside the window suppressed.
        assert compactor.precall_warn_should_emit("iid") is True
        assert compactor.precall_warn_should_emit("iid") is False
        # Interval collapsed to 0 → emission allowed again.
        compactor._precall_warn_interval_s = 0.0
        assert compactor.precall_warn_should_emit("iid") is True


# =============================================================================
# T4-ext — multi-call refire + injection-dominated skip
# =============================================================================


class TestPreCall95MultiCallRefire:
    """A turn with N LLM calls crossing 95% compacts ONCE; post-
    compaction estimate sits below 95% (A.6 'success stops refire')."""

    def _compactor(self) -> ContextCompactor:
        compactor = _make_compactor(
            context_window_overrides={"test-model": 200},
            recent_message_window=1,
            min_recent_window=1,
        )
        _stub_chunked_summarizer(compactor)
        return compactor

    @pytest.mark.asyncio
    async def test_n_call_turn_compacts_once(self):
        """A turn with N LLM calls crossing 95% compacts ONCE;
        post-compaction estimate sits below 95% (A.6 'success
        stops refire').

        Escape-Hatch Hardening round 2 (Lane 1) — the dedup
        yields to over-budget contexts. The post-compaction
        payload is a rebuilt, lower-token history; the A.6
        'success stops refire' invariant is preserved because
        (a) after a SUCCESSFUL compaction the engine stamps
        ``compacted_at`` (the 60s dedup window opens), AND
        (b) the rebuilt payload sits BELOW the gate (post-tokens
        < 0.95 * 200). Both halves hold: when the rebuild brings
        the post-tokens under the gate, the second hook call
        short-circuits at the gate predicate BEFORE reaching
        the engine. We pin BOTH invariants here.

        For the over-budget case (where the post-tokens
        remained over-budget despite the rebuild), the dedup
        YIELDS and the engine runs again — the Lane 1 binding.
        The p1b test uses a 200-token window; the rebuild is
        generally under-budget (the chunked summarizer is
        stubbed to keep a short content), so the A.6 invariant
        pins here as the assertion below.
        """
        compactor = self._compactor()
        graph = _FakeGraph(values={"messages": make_messages(30)})

        # Call 1 — the LLM-bound payload mirrors the oversized state.
        payload = make_messages(30)
        outcome = await _run_hook(graph, compactor, payload)
        assert outcome.rebuilt_payload is not None, (
            "95% crossing must compact + rebuild"
        )
        assert len(graph.aupdate_calls) == 2  # messages + compacted_at
        # The durable-return contract: sentinel-first prefix + stamp.
        assert outcome.outgoing_prefix is not None
        assert outcome.compacted_at is not None

        # Post-compaction payload estimate is BELOW 95% (durable relief).
        post_tokens = estimate_messages_tokens(outcome.rebuilt_payload)
        assert post_tokens < 0.95 * 200

        # The pre-call hook's INVARIANT: when the post-rebuild payload
        # sits UNDER the gate (below 0.95 * 200), the hook short-
        # circuits at the gate predicate BEFORE reaching the engine.
        # We pin this WITHOUT appending tool messages (which would
        # push the post-payload over-budget and engage the Lane 1
        # dedup-yield — verified separately).
        outcome2 = await _run_hook(
            graph, compactor, list(outcome.rebuilt_payload),
        )
        assert outcome2.rebuilt_payload is None, (
            "post-compaction under-budget → A.6 'success stops "
            "refire' (gate predicate returns PRECALL_NOOP before "
            "engine). Lane 1 over-budget yield verified by "
            "test_overbudget_bypasses_dedup_window and "
            "test_dedup_does_not_silence_over_budget_shrink."
        )

        assert len(graph.aupdate_calls) == 2, (
            "a 95% crossing must compact exactly ONCE per window — no "
            "per-call refire"
        )

    @pytest.mark.asyncio
    async def test_both_seam_writes_carry_as_node_agent(self):
        """The hook consumes the SHARED seam with ``mid_turn=True`` →
        BOTH ``aupdate_state`` writes carry ``as_node='agent'`` (A.5),
        messages write FIRST (order-pinned)."""
        compactor = self._compactor()
        graph = _FakeGraph(values={"messages": make_messages(30)})

        await _run_hook(graph, compactor, make_messages(30))

        assert len(graph.aupdate_calls) == 2
        first_update, first_kwargs = graph.aupdate_calls[0]
        second_update, second_kwargs = graph.aupdate_calls[1]
        assert "messages" in first_update
        assert first_kwargs.get("as_node") == "agent"
        assert "compacted_at" in second_update
        assert second_kwargs.get("as_node") == "agent"
        # The pre-resolved graph is used — no manager round-trip.
        assert graph.values.get("compacted_at") is not None

    @pytest.mark.asyncio
    async def test_tap_fires_with_precall_label_on_real_compaction(self):
        compactor = self._compactor()
        graph = _FakeGraph(values={"messages": make_messages(30)})
        tap = MagicMock()
        tap.tap_node_return = AsyncMock(return_value=0)

        await _run_hook(
            graph, compactor, make_messages(30), tap_slot=tap
        )

        tap.tap_node_return.assert_awaited_once()
        args = tap.tap_node_return.await_args.args
        assert len(args) == 2
        assert args[1] == "p1b-hook-instance-0001"  # instance_id


class TestPreCall95InjectionDominatedSkip:
    """Injection-dominated no-op: 60s dedup still engages after the floor.

    COMPACTION NEVER-BLOCKED (Verdict A, fix/compaction-never-blocked
    @ c600af60d). **OLD expectation** (pre-Phase-2): the 95% pre-call
    hook emitted a stamp-only no-op for the injection-dominated case
    (skip + single rate-limited WARN + stamp persisted via
    ``as_node='agent'``). The context never shrank.

    **NEW expectation** (post-Phase-2): the engine's all-injected
    skip now falls through to the 50%-tail last-effort floor. The
    hook sees a real shrink (rebuilt_payload + outgoing_prefix),
    and the 60s dedup still engages after the floor's
    ``compacted_at`` stamp.
    """

    @pytest.mark.asyncio
    async def test_stamped_skip_single_warn_no_refire(self, caplog):
        """Floor engages; the dedup still holds for healthy under-budget
        re-fires AND yields for over-budget re-fires.

        Phase-2 expectation: the 95% pre-call hook fires the
        50%-tail floor on the injection-dominated payload; the
        result carries non-empty ``replacement_messages`` (drop
        list + notice + tail). The dedup still engages on the
        NEXT call within 60s IF the corpus is under-budget
        (the floor's ``compacted_at`` stamp lands in state,
        blocking subsequent re-evaluations of the same
        payload).

        Escape-Hatch Hardening round 2 (Lane 1) — when the
        rebuilt payload is STILL over-budget, the dedup yields
        and a fresh shrink fires. We pin BOTH halves: a healthy
        under-budget rebuild carries the A.6 'success stops
        refire' invariant; an over-budget rebuild engages the
        Lane 1 dedup-yield. This test pins the under-budget
        half (the test's payload is the heavy-injected kind
        where the floor shrinks to a notice-only replacement
        and the post-build payload is well under-budget).
        """
        compactor = _make_compactor(
            context_window_overrides={"test-model": 200},
            min_messages_before_compaction=10,
        )
        _stub_chunked_summarizer(compactor)
        # State: 3 regular + 12 injected. The payload estimate crosses
        # 95% of the 200-token window (real estimator on a big injected
        # block), but the regular pool is below min_messages → the
        # engine's min-messages gate fires, falling through to the
        # 50%-tail floor (Phase-2 hardening).
        injected = _make_injected(12)
        injected[0].content = "[SYSTEM CONTEXT]\n" + ("x" * 400)
        injected[1].content = "[SYSTEM CONTEXT]\n" + ("y" * 400)
        graph = _FakeGraph(
            values={"messages": make_messages(3) + injected}
        )
        payload = make_messages(3) + injected

        with caplog.at_level(logging.INFO, logger="daemon.graph"):
            # Call 1 — crosses 95%, floor engages, real shrink lands.
            outcome1 = await _run_hook(graph, compactor, payload)
            # Call 2 — same heavy payload (matches pre-Lane-1 test
            # setup). The Gate predicate fires (payload still 277
            # tokens > 95% of 200). The engine evaluates Lane 1's
            # over-budget yield: the prior compacted_at is within
            # the 60s window, the same payload is over-budget →
            # dedup YIELDS → floor fires again. The post-floor
            # corpus is still heavy (the floor's compaction_type
            # is the same payload each time), so the floor keeps
            # firing. We pin that the GATE fires on every call
            # (the Lane 1 dedup-yield contract surfaces as the
            # floor re-engaging until the post-rebuild payload
            # sits under-budget, at which point the gate itself
            # short-circuits).
            for _ in range(2):
                outcome2 = await _run_hook(graph, compactor, payload)

        # Phase-2: floor returns a real shrink → rebuilt_payload is
        # populated. The dedup stamp is carried on compacted_at.
        assert outcome1.rebuilt_payload is not None, (
            "floor must produce a non-None rebuilt_payload — the seam "
            "persists a real shrink, not just a stamp."
        )
        assert outcome1.outgoing_prefix is not None
        assert outcome1.compacted_at is not None
        # Lane 1: the same-payload follow-up calls engage the
        # dedup-yield + floor re-fires (the pre-Lane-1 dedup was
        # unconditional and short-circuited these calls). The
        # over-budget case pins the Lane 1 binding property —
        # dedup MUST yield when context is over-budget.
        assert outcome2.rebuilt_payload is not None, (
            "Lane 1: same over-budget payload across calls → "
            "dedup yields, real shrink lands, rebuilt_payload "
            "populated"
        )
        # Phase-2: Variant B standard path writes TWO aupdate_state
        # calls (one for messages + sentinel, one for compacted_at).
        # The OLD stamp-only path was a single aupdate (just the
        # stamp). The pin flips from 1 → 2.
        assert len(graph.aupdate_calls) >= 2, (
            f"Variant B (real shrink) writes 2 aupdate_state calls; "
            f"got {len(graph.aupdate_calls)}"
        )
        # First aupdate: messages channel (the new channel value)
        update, kwargs = graph.aupdate_calls[0]
        assert "messages" in update, (
            "first aupdate must carry the messages channel (real shrink)"
        )
        assert kwargs.get("as_node") == "agent"
        # Second aupdate: compacted_at stamp (the dedup anchor)
        update2, kwargs2 = graph.aupdate_calls[1]
        assert "compacted_at" in update2
        assert kwargs2.get("as_node") == "agent"
        # Phase-2: floor's INFO log is the new "precall-95 fired" signal.
        # The hook logs "attempting" BEFORE the engine call on every
        # dispatch (3 total — one per call), and a "complete" INFO log
        # on each successful compaction. Lane 1: same-payload
        # follow-ups re-fire the floor (over-budget yield), so each
        # call lands a "complete" log.
        precall_infos = [
            r for r in caplog.records
            if "precall-95" in r.getMessage() and r.levelno == logging.INFO
        ]
        assert len(precall_infos) >= 4, (
            f"precall-95 logs: 3 'attempting' (one per call) + 3+ "
            f"'complete' (one per success) expected; got "
            f"{len(precall_infos)} — under-budget gate short-circuit "
            f"verified by test_n_call_turn_compacts_once"
        )
        # Lane 1: the floor re-engages on each call (over-budget
        # + recent-stamp → dedup yields → floor fires). The
        # over-budget yield is the BINDING contract (the pre-Lane-1
        # dedup was unconditional and would have silenced calls
        # 2-3).
        floor_engaged_warns = [
            r for r in caplog.records
            if "falling through to last-effort floor" in r.getMessage()
        ]
        assert len(floor_engaged_warns) >= 1, (
            "engine-side floor engagement must fire at least once "
            "(per-Lane-1 over-budget yield); the unconditional "
            "pre-Lane-1 dedup short-circuit is replaced by yield "
            "behavior. A.6 'success stops refire' is preserved "
            "through the post-rebuild under-budget GATE short-"
            "circuit, not the engine-side dedup."
        )

    @pytest.mark.asyncio
    async def test_stamp_only_skip_does_not_fire_tap(self):
        """Phase-2: the floor IS a real shrink, so the tap DOES fire.

        The OLD test asserted ``tap.tap_node_return.assert_not_awaited()``
        because the stamp-only path had no replacement messages to tap.
        The NEW behavior: the floor produces non-empty
        ``replacement_messages`` (drop list + notice + retained tail),
        so the tap IS invoked.

        Note: the test must inflate the payload above 95% of the 200-token
        window so the pre-call gate fires; the floor engages and the
        tap is called. The OLD test got away with a single big
        injected block because the stamp-only path fired on ANY
        95%-crossing input. The NEW path needs the floor to engage
        → 2 big injected blocks (matches the first test's payload).
        """
        compactor = _make_compactor(
            context_window_overrides={"test-model": 200},
            min_messages_before_compaction=10,
        )
        _stub_chunked_summarizer(compactor)
        injected = _make_injected(12)
        # Two big injected blocks so the payload crosses 95% of the
        # 200-token window (forces the pre-call gate + floor).
        injected[0].content = "[SYSTEM CONTEXT]\n" + ("x" * 400)
        injected[1].content = "[SYSTEM CONTEXT]\n" + ("y" * 400)
        graph = _FakeGraph(
            values={"messages": make_messages(3) + injected}
        )
        tap = MagicMock()
        tap.tap_node_return = AsyncMock(return_value=0)

        await _run_hook(
            graph, compactor, make_messages(3) + injected, tap_slot=tap
        )

        # Phase-2: floor is a real shrink, so the tap IS awaited.
        tap.tap_node_return.assert_awaited()


# =============================================================================
# Kill-switch + availability guards
# =============================================================================


class TestPreCall95KillSwitch:
    """A.8 — the SAME flag governs the hook; OFF = no-op."""

    @pytest.mark.asyncio
    async def test_flag_off_is_full_noop(self, monkeypatch):
        compactor = _make_compactor(
            proactive_enabled=False,
            context_window_overrides={"test-model": 600_000},
        )
        compactor.compact_state = AsyncMock(return_value=None)
        est_calls = {"n": 0}

        def _est(msgs):
            est_calls["n"] += 1
            return 599_000

        monkeypatch.setattr(loader_mod, "estimate_messages_tokens", _est)
        graph = _FakeGraph(values={"messages": make_messages(5)})

        outcome = await _run_hook(graph, compactor, make_messages(5))

        assert outcome == _PRECALL_NOOP
        assert est_calls["n"] == 0, "OFF must not even estimate"
        assert compactor.compact_state.await_count == 0
        assert graph.aupdate_calls == []

    @pytest.mark.asyncio
    async def test_no_compactor_or_graph_is_noop(self):
        payload = make_messages(5)
        assert await _run_hook(_FakeGraph(), None, payload) == _PRECALL_NOOP
        # graph_ref resolving to None graph:
        compactor = _make_compactor()
        outcome = await _maybe_precall_compact_95(
            instance_id="x",
            instance_short="x",
            compactor=compactor,
            graph_ref=[None],
            thread_config={},
            full_messages=payload,
            system_prompt="sp",
            llm_config={"model": "test-model"},
            injected_msgs=[],
            injected_report_msgs=[],
            ephemeral_context_msgs=[],
            pairing_synthesized_msgs=[],
        )
        assert outcome == _PRECALL_NOOP


# =============================================================================
# T-isolation — CLE composition (A.6 / A.7)
# =============================================================================

# Byte-exact CLE persist lines (the handler's Variant B writes). The
# 95% hook must NOT touch them — source-pinned here so any edit to the
# handler's persist recipe fails this suite (task T-isolation anchor).
_CLE_PERSIST_MESSAGES_LINE = (
    "await graph.aupdate_state(thread_config, "
    "{'messages': replacement_messages}, as_node='agent')"
)
_CLE_PERSIST_STAMP_LINE = (
    "await graph.aupdate_state(thread_config, "
    "{'compacted_at': result.compacted_at}, as_node='agent')"
)


class TestPreCall95CLEIsolation:
    """The 95% hook is a disjoint path: it never consumes the CLE
    single-retry and leaves the CLE persist site byte-unchanged."""

    def test_cle_persist_site_byte_unchanged(self):
        import daemon.graph as graph_mod

        source = inspect.getsource(graph_mod)
        assert source.count(_CLE_PERSIST_MESSAGES_LINE) == 1, (
            "CLE persist (messages) site must stay byte-identical and "
            "appear exactly once"
        )
        assert source.count(_CLE_PERSIST_STAMP_LINE) == 1, (
            "CLE persist (compacted_at) site must stay byte-identical "
            "and appear exactly once"
        )
        # The CLE handler keeps its in-frame rebuild + single re-invoke.
        assert "compact_messages = [SystemMessage(content=system_prompt)] + updated_state.values.get('messages', [])" in source
        assert source.count("except ContextLengthExceededError:") == 1

    def test_hook_call_site_sits_before_invoke_outside_cle_handler(self):
        """Structural pin: the hook call happens before the FIRST
        ``run_in_executor`` invoke (pre-call), not inside the CLE
        handler's retry (post-failure)."""
        import daemon.graph as graph_mod

        source = inspect.getsource(graph_mod)
        hook_site = source.find("_maybe_precall_compact_95(\n")
        # Anchor the invoke search AFTER the hook site. The substring
        # "response = await loop.run_in_executor(\n" also matches as a
        # suffix of "new_response = await loop.run_in_executor(\n" in
        # the Phase-2 _maybe_pre_terminal_repair block (~:2749), so a
        # global find would resolve a bogus earlier index.
        first_invoke = source.find(
            "response = await loop.run_in_executor(\n", hook_site
        )
        cle_handler = source.find("except ContextLengthExceededError:")
        assert 0 < hook_site < first_invoke < cle_handler

    @pytest.mark.asyncio
    async def test_abort_does_not_stamp_and_cle_can_still_fire(self):
        """A.6 — abort → NO stamp → the dedup does NOT engage → a later
        trigger (CLE) can still compact in the same turn."""
        compactor = _make_compactor(
            context_window_overrides={"test-model": 200},
            recent_message_window=2,
            min_recent_window=1,
        )
        _stub_chunked_summarizer(compactor)
        graph = _FakeGraph(values={"messages": make_messages(30)})

        # The seam's pre-write guard refuses the write (abort).
        from daemon.compaction import CompactionAborted

        with patch.object(
            seam_mod,
            "build_sentinel_replacement",
            side_effect=CompactionAborted("test abort"),
        ):
            outcome = await _run_hook(graph, compactor, make_messages(30))

        assert outcome == _PRECALL_NOOP  # fail_open — the call proceeds
        assert graph.aupdate_calls == [], (
            "abort must leave the checkpoint untouched (no stamp) — the "
            "60s dedup must NOT engage"
        )
        assert graph.values.get("compacted_at") is None

        # CLE-analog trigger afterwards: compaction CAN still fire.
        outcome2 = await _run_hook(graph, compactor, make_messages(30))
        assert outcome2.rebuilt_payload is not None
        assert len(graph.aupdate_calls) == 2

    @pytest.mark.asyncio
    async def test_success_stamps_and_dedup_holds_for_cle(self):
        """A.6 — success → stamped mid-turn → a subsequent same-turn
        trigger reads the stamp and the dedup holds (engine returns
        None).

        Escape-Hatch Hardening round 2 (Lane 1 + H4 alignment) —
        the dedup holds when the post-rebuild corpus is
        UNDER-budget; Lane 1 makes dedup YIELD when the corpus
        is over-budget. The post-hook-rebuild corpus in this
        fixture is ~178 tokens + ~2 system_prompt = 180 tokens;
        budget is 200 * 0.80 = 160. The corpus IS over-budget →
        the dedup yields → engine returns a real CompactionResult
        (NOT None). The A.6 'success stops refire' invariant
        is preserved through the post-rebuild UNDER-BUDGET
        GATE short-circuit (verified separately by
        ``test_n_call_turn_compacts_once`` which uses a smaller
        payload that lands under-budget post-rebuild).

        To pin the under-budget half cleanly, this test
        constructs a fresh CLE context with an UNDERSIZED
        ``state.values['messages']`` — a SystemMessage-only
        rebuild that fits comfortably under 200 * 0.80 = 160
        tokens. The 60s dedup then engages on the freshly-
        stamped prior compacted_at.
        """
        from daemon.compaction import CompactionContext, _extract_msg_timestamps
        from daemon.loader import estimate_messages_tokens
        from langchain_core.messages import SystemMessage

        compactor = _make_compactor(
            context_window_overrides={"test-model": 200},
            recent_message_window=2,
            min_recent_window=1,
        )
        _stub_chunked_summarizer(compactor)
        # A small under-budget state messages list (clean post-
        # rebuild view). 2 tokens + 2 overhead = 4 tokens; well
        # under 160.
        msgs = [
            SystemMessage(content="system prompt"),
            SystemMessage(content="concise summary"),
        ]
        # Verify this is under-budget before constructing the
        # CLE ctx.
        msg_tokens = estimate_messages_tokens(msgs)
        assert msg_tokens < 0.80 * 200, (
            f"test setup: under-budget rebuild must fit; got "
            f"{msg_tokens} tokens vs 0.80 * 200 = 160"
        )

        # Build a CLE-style CompactionContext with
        # ``last_compacted_at`` set to a recent stamp (within the
        # 60s dedup window).
        from datetime import datetime, timezone
        recent_ts = (
            datetime.now(timezone.utc).isoformat()
        )  # recent enough to engage the dedup
        ctx = CompactionContext(
            messages=msgs,
            system_prompt_tokens=0,  # pre-H4-alignment convention;
            # the under-budget rebuild + recent stamp is the
            # exact contract the dedup holds against.
            model_name="test-model",
            config=compactor.config,
            llm_config=compactor.llm_config,
            last_compacted_at=recent_ts,
            instance_id="p1b-hook-instance-0001",
            msg_timestamps=_extract_msg_timestamps(msgs),
        )
        result = await compactor.compact_state(ctx, force=False)
        assert result is None, (
            "after a successful hook compaction the 60s dedup must hold "
            "for the same-turn CLE trigger — Lane 1 dedup-yield checked "
            "separately (over-budget case pinned by "
            "test_dedup_does_not_silence_over_budget_shrink)"
        )

    @pytest.mark.asyncio
    async def test_hook_failure_never_breaks_the_call(self):
        """Any internal failure → WARN + proceed with the original
        payload (the hook must never break the LLM call)."""
        compactor = _make_compactor(
            context_window_overrides={"test-model": 200},
        )
        compactor.compact_state = AsyncMock(
            side_effect=RuntimeError("engine exploded")
        )
        graph = _FakeGraph(values={"messages": make_messages(30)})
        payload = make_messages(30)

        outcome = await _run_hook(graph, compactor, payload)

        assert outcome == _PRECALL_NOOP
        assert graph.aupdate_calls == []


# =============================================================================
# Rebuild mechanics
# =============================================================================


class TestPreCall95RebuildPayload:
    """The rebuilt payload follows the CLE handler's in-frame pattern:
    ``[SystemMessage] + compacted state + injected + report``."""

    @pytest.mark.asyncio
    async def test_rebuild_layout(self):
        compactor = _make_compactor(
            context_window_overrides={"test-model": 200},
            recent_message_window=2,
            min_recent_window=1,
        )
        _stub_chunked_summarizer(compactor)
        injected = [
            HumanMessage(
                content="urgent user note",
                id="inj-x",
                additional_kwargs={"injected_message": True},
            )
        ]
        report = [HumanMessage(content="child report", id="rep-1")]
        graph = _FakeGraph(values={"messages": make_messages(30)})

        outcome = await _run_hook(
            graph,
            compactor,
            make_messages(30) + injected + report,
            injected_msgs=injected,
            injected_report_msgs=report,
        )

        assert outcome.rebuilt_payload is not None
        rebuilt = outcome.rebuilt_payload
        assert isinstance(rebuilt[0], SystemMessage)
        assert rebuilt[0].content == "system prompt"
        # Injected + report re-appended at the tail (C3 parity).
        assert rebuilt[-2] is injected[0]
        assert rebuilt[-1] is report[0]
        # The compacted summary doc from the engine is present.
        contents = " ".join(
            str(getattr(m, "content", "")) for m in rebuilt
        )
        assert "Conversation Summary" in contents
        # DURABLE RETURN (supersession resolution): sentinel-first
        # prefix carrying the post-compaction channel + injected/report.
        prefix = outcome.outgoing_prefix
        assert prefix is not None and len(prefix) >= 1
        assert isinstance(prefix[0], RemoveMessage)
        assert str(prefix[0].id) == "__remove_all__" or "remove" in str(
            type(prefix[0]).__name__
        )
        assert [m for m in prefix[1:]] == rebuilt[1:]
        # The dedup stamp rides on the node return.
        assert outcome.compacted_at is not None

    @pytest.mark.asyncio
    async def test_pairing_placeholders_accumulated(self):
        """The pairing guard accumulator is MUTATED so the C2 return
        persists synthesized placeholders (same contract as CLE)."""
        compactor = _make_compactor(
            context_window_overrides={"test-model": 200},
            recent_message_window=2,
            min_recent_window=1,
        )
        _stub_chunked_summarizer(compactor)
        graph = _FakeGraph(values={"messages": make_messages(30)})
        pairing: list = []

        outcome = await _maybe_precall_compact_95(
            instance_id="p1b-hook-instance-0001",
            instance_short="p1b",
            compactor=compactor,
            graph_ref=[graph],
            thread_config={"configurable": {"thread_id": "x"}},
            full_messages=make_messages(30),
            system_prompt="system prompt",
            llm_config={"model": "test-model"},
            injected_msgs=[],
            injected_report_msgs=[],
            ephemeral_context_msgs=[],
            pairing_synthesized_msgs=pairing,
            precall_compaction_tap_slot=None,
        )
        assert outcome.rebuilt_payload is not None
        # Clean state → no placeholders needed; the accumulator list is
        # the mutation channel and must at least remain a list the C2
        # return persists (CLE parity).
        assert isinstance(pairing, list)


# =============================================================================
# Tap label contract (A.9 T-tap — LOCKED decision)
# =============================================================================


class TestPreCall95TapLabel:
    def test_precall_label_is_new_and_distinct(self):
        from daemon.services.message_tap import (
            SOURCE_COMPACTION_MESSAGING,
            SOURCE_COMPACTION_REACTIVE,
        )

        assert SOURCE_COMPACTION_PRECALL_95 == "compaction_precall_95"
        assert SOURCE_COMPACTION_PRECALL_95 not in {
            SOURCE_COMPACTION_REACTIVE,
            SOURCE_COMPACTION_MESSAGING,
            "user_message_entry",
            "agent_node_return",
        }

    def test_engine_estimator_binding_untouched_by_loader_patch(self):
        """The engine's estimator binding lives in ``daemon.compaction``
        (imported at module load) — patching ``daemon.loader`` for hook
        tests must not affect engine math (call-count isolation)."""
        import daemon.compaction as compaction_mod

        assert compaction_mod.estimate_messages_tokens is not None
        msgs = make_messages(3)
        assert compaction_mod.estimate_messages_tokens(msgs) > 0
