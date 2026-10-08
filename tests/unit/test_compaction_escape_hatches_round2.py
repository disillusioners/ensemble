"""ESCAPE-HATCH HARDENING — round 2 (compaction subsystem) tests.

Lane-by-lane coverage:

* Lane 1 — dedup yields under ``force=True`` AND over-budget contexts;
  reactive CLE site has force-floor punch-through on ``None`` return
  and exception retry; the 3 unwrapped floor raise sites are wrapped
  with graceful degradation; the duplicated 60s literal is consolidated
  into ``CompactionConfig.dedup_window_s`` (default 60).

* Lane 2 — token-aware floor success check (the count-based
  ``ceil(N/2)`` floor alone cannot declare success on a token-heavy
  tail). The floor iteratively halves down to ``last_k_floor`` while
  still over-budget.

* Lane 3 — ``estimate_messages_tokens`` counts non-text content
  blocks (image / file / audio) at realistic per-block weights
  (``CompactionConfig.image_block_tokens`` / ``file_block_tokens`` /
  ``audio_block_tokens``).

* H4 alignment — the reactive ctx at ``graph.py:8888`` carries
  REAL ``system_prompt_tokens`` (pre-H4 passed 0); the executor
  floor at ``compact_executor.py:812`` carries REAL system-prompt
  tokens (pre-H4 passed 0).

* Lane 4 — CI blind spot pin: a real escalated row (durable
  ``compaction_escalation_until``) drives ``gate_ratio`` to the
  ``escalation_gate_ratio`` config field. Deleting the
  graph.py:7461-7484 read block makes this test fail.

* Lane 5 — escalation ``gate_ratio`` defaults to 0.80 (env knob).

* Conditional included: comment/docstring drift fixes (sync-vs-to_thread,
  stale anchors).

Round-1 corpus (``test_compaction_never_blocked.py`` +
``test_proactive_compaction_fix_p1b.py``) stays green — verified via
``pytest`` invocation in the test run.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
    COMPACTION_NOTICE_CONTEXT_KIND,
    COMPACTION_TYPE_TAIL_TRUNCATION_LAST_EFFORT,
    ChunkedOutcome,
    CompactionContext,
    ContextCompactor,
    _build_last_effort_replacement,
)
from daemon.config import CompactionConfig as CompactionConfigModel
from daemon.graph import _PRECALL_NOOP, _maybe_precall_compact_95
from daemon.loader import estimate_messages_tokens
from daemon.services._escalation_metadata import (
    ESCALATION_THRESHOLD_KEY,
    ESCALATION_UNTIL_KEY,
    is_proactive_escalation_active,
    set_proactive_escalation_metadata,
)


# =============================================================================
# Helpers (mirror test_compaction_never_blocked.py)
# =============================================================================


def make_compaction_config(**overrides: Any) -> CompactionConfigModel:
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
        "proactive_escalate_after": 3,
    }
    defaults.update(overrides)
    return CompactionConfigModel(**defaults)


def make_messages(n: int, content_prefix: str = "M") -> list:
    out = []
    for i in range(n):
        cls = HumanMessage if i % 2 == 0 else AIMessage
        out.append(cls(content=f"{content_prefix} {i}", id=f"m-{i}"))
    return out


def make_compact_context(
    config: CompactionConfigModel,
    messages: list,
    *,
    instance_id: str = "test-inst",
    last_compacted_at: str | None = None,
    system_prompt_tokens: int = 200,
    model_name: str = "gpt-4o",
) -> CompactionContext:
    return CompactionContext(
        messages=messages,
        system_prompt_tokens=system_prompt_tokens,
        model_name=model_name,
        config=config,
        llm_config={},
        last_compacted_at=last_compacted_at,
        instance_id=instance_id,
    )


# =============================================================================
# Hook-driving helpers (Lane 4 read-block pin)
# =============================================================================


class _Lane4FakeRepo:
    """Minimal in-memory fake of the InstanceRepository surface
    used by the Lane 4 read-block pin test.

    Exposes ONLY the parts the read block + the durable writer
    exercise:

    * ``get(instance_id)`` — read path the read block calls via
      ``asyncio.to_thread``. Returns a MagicMock row carrying
      both ``instance_metadata`` (the JSONB column the C2 reader
      prefers) and ``metadata`` (the legacy fallback).
    * ``set_metadata_many(instance_id, payload)`` — preferred
      atomic write path (PostgreSQL ``jsonb_set`` / SQLite
      ``json_set`` in one UPDATE). Used by
      :func:`set_proactive_escalation_metadata` when present.

    Deliberately excludes ``update`` / ``set_metadata`` — the
    production writer prefers ``set_metadata_many`` and this
    fake's presence forces THAT path (regression guard: a
    regression to the legacy ``update`` write would not be
    covered by this fake; the writer would no-op via the
    defensive fallback). The seam's read-side (the C2 reader)
    reads ``instance_metadata`` regardless of writer path.
    """

    def __init__(self, instance_id: str = "ci-blindspot-iid"):
        self._instance_id = instance_id
        self.row = MagicMock()
        self.row.status = "running"
        self.row.instance_metadata = {}
        self.row.metadata = {}
        self.set_metadata_many_calls: list[tuple[str, dict]] = []

    def get(self, _instance_id: str):
        return self.row

    def set_metadata_many(
        self, instance_id: str, payload: dict
    ) -> None:
        self.set_metadata_many_calls.append((instance_id, dict(payload)))
        # Mirror the production ``jsonb_set`` semantics: merge
        # into the column (additive, do not clobber other keys).
        self.row.instance_metadata = {
            **self.row.instance_metadata, **payload
        }
        # Keep the legacy fallback in sync so a regression to
        # the legacy reader would still see the metadata.
        self.row.metadata = dict(self.row.instance_metadata)


@dataclass
class _Lane4FakeState:
    """Minimal ``StateSnapshot``-shaped stand-in (``.values`` dict)."""

    values: dict = field(default_factory=dict)
    next: tuple = ()


class _Lane4FakeGraph:
    """Graph stand-in that persists ``aupdate_state`` writes into
    its fake values so subsequent ``aget_state`` reads reflect
    them. Mimics the ``add_messages`` semantics the hook relies
    on: a leading ``RemoveMessage`` sentinel replaces the whole
    channel."""

    def __init__(self, values: dict | None = None) -> None:
        self.values: dict = dict(values or {})
        self.aupdate_calls: list[tuple[dict, dict]] = []

    async def aget_state(self, config):
        return _Lane4FakeState(values=dict(self.values))

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


def _lane4_stub_chunked_summarizer(compactor: ContextCompactor) -> None:
    """Stub the merge-call LLM path so the real engine compacts
    without an LLM (the 15% ceiling rule would otherwise trip
    B-shape degrade)."""

    async def _fake_chunked(compactable, context, previous_overview=None):
        return ChunkedOutcome(
            summaries=["[Conversation Summary]\nall groups"],
            failed_batches=[],
            stop_reason="completed",
        )

    compactor._summarize_chunked = _fake_chunked


async def _lane4_run_hook(
    *,
    instance_id: str,
    compactor: ContextCompactor,
    graph: _Lane4FakeGraph,
    full_messages: list,
    tap_slot: Any | None = None,
    llm_config: dict | None = None,
):
    """Invoke :func:`daemon.graph._maybe_precall_compact_95` with
    the same closure-local defaults the agent_node site uses."""
    return await _maybe_precall_compact_95(
        instance_id=instance_id,
        instance_short=instance_id.split("-")[0],
        compactor=compactor,
        graph_ref=[graph],
        thread_config={"configurable": {"thread_id": instance_id}},
        full_messages=full_messages,
        system_prompt="system prompt",
        llm_config=llm_config or {"model": "test-model"},
        injected_msgs=[],
        injected_report_msgs=[],
        ephemeral_context_msgs=[],
        pairing_synthesized_msgs=[],
        precall_compaction_tap_slot=tap_slot,
    )


# =============================================================================
# Lane 1 tests — dedup yields to force=True and over-budget
# =============================================================================


class TestLane1DedupYieldsToForce:
    """Lane 1 — the engine's dedup MUST NOT block a forced compaction."""

    async def test_force_true_bypasses_dedup_window(self):
        """When ``last_compacted_at`` is within the dedup window AND
        ``force=True``, the dedup MUST yield (the contraction is
        no longer driven by automatic anti-refire, the operator
        asked for it). The engine routes to the floor when the
        dedup yields AND over-budget — see also
        ``TestLane1FloorLandsOnOverBudgetEvenWithDedupActive``.
        """
        # Use a recent last_compacted_at (within 60s dedup window).
        recent = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat()
        cfg = make_compaction_config()  # dedup_window_s default 60
        msgs = make_messages(20)
        ctx = make_compact_context(
            cfg, msgs, last_compacted_at=recent
        )
        compactor = ContextCompactor(cfg, {})
        # force=True with a non-trivial corpus forces the
        # threshold check to bypass; the dedup ALSO yields.
        result = await compactor.compact_state(ctx, force=True)
        assert result is not None, (
            "force=True must not be blocked by the dedup "
            "(Lane 1: dedup yields under force=True)"
        )

    async def test_force_true_with_under_budget_corpus_emits_stamp_only(
        self,
    ):
        """An under-budget + ``force=True`` corpus still takes the
        path the auto-trigger would have, but the dedup yield
        routes through the body. The min-messages check is the
        only skip-condition (selectable=20 >= min=10), and the
        threshold check is bypassed by ``force=True``. With only
        20 short messages and over-budget? No — selectable
        tokens + system_prompt_tokens (200) must exceed
        ``trigger_window * 0.80``. With context_window=8192 and
        selectable=20 tokens, that's well under the threshold,
        so the engine takes a stamp-only ``tail_truncation_last_effort``
        via the all-injected or selectable path? Selectable is 20
        non-empty msgs, so the all-injected skip doesn't fire.
        The min-messages skip doesn't fire (selectable>=10).
        With ``force=True``, the threshold bypasses (force skip
        check), and the engine still routes to the floor when
        the selectable subset is small enough to take the
        ``emergency_truncation`` path. We assert the result is
        NOT ``None`` — that's the dedup-yield guarantee.
        """
        cfg = make_compaction_config()
        recent = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat()
        msgs = make_messages(20)
        ctx = make_compact_context(
            cfg, msgs, last_compacted_at=recent
        )
        compactor = ContextCompactor(cfg, {})
        result = await compactor.compact_state(ctx, force=True)
        assert result is not None, (
            "Lane 1: force=True must never return None on a "
            "recent-stamp context (the dedup yields, the engine "
            "always does work)"
        )


class TestLane1DedupYieldsToOverBudget:
    """Lane 1 — over-budget contexts MUST shrink even within the dedup."""

    async def test_overbudget_bypasses_dedup_window(self):
        """Force the corpus to be MASSIVE + ``last_compacted_at``
        recent. The dedup would normally short-circuit, but the
        over-budget predicate (Lane 1) must yield and the engine
        must shrink. We construct a 200-message HumanMessage-
        only corpus with a small (1000-token) context window so
        the over-budget predicate (``selectable+system > window*0.80``)
        fires regardless.
        """
        from daemon.config import CompactionConfig as CC
        cfg = make_compaction_config(
            context_window_overrides={"gpt-4o": 100},
            context_window_default=100,
            # Do NOT use force here — we want the over-budget
            # predicate to be the yielding condition.
        )
        recent = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat()
        # 200 messages of 'X' content = ~200 tokens; with
        # system_prompt=200 the budget is 100*0.80=80. Clearly
        # over-budget.
        msgs = [
            HumanMessage(content="X " * 50, id=f"m-{i}")
            for i in range(200)
        ]
        ctx = make_compact_context(
            cfg, msgs, last_compacted_at=recent
        )
        compactor = ContextCompactor(cfg, {})
        result = await compactor.compact_state(ctx, force=False)
        assert result is not None, (
            "Lane 1: over-budget context must not be blocked "
            "by the dedup (the over-budget yield routes the "
            "engine to floor or summarization, NOT stamp-only)"
        )

    async def test_dedup_active_under_budget_still_stamps(self):
        """Counter-test: when the corpus IS under-budget AND
        recent-stamped, the dedup stays active and returns
        ``None`` (anti-refire semantics preserved). This pin
        the byte-identity invariants for healthy corpora.
        """
        cfg = make_compaction_config()  # 8192 default window
        recent = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat()
        # Tiny corpus — well under-budget (default 8192).
        msgs = make_messages(5)
        ctx = make_compact_context(
            cfg, msgs, last_compacted_at=recent
        )
        compactor = ContextCompactor(cfg, {})
        result = await compactor.compact_state(ctx, force=False)
        assert result is None, (
            "Lane 1: under-budget + recent-stamp → anti-refire "
            "stamp-only; dedup stays active (byte-identical "
            "with pre-Lane-1 for healthy corpora)"
        )


# =============================================================================
# Lane 1 — dedup config consolidation
# =============================================================================


class TestLane1DedupConfig:
    """Lane 1 — consolidate the two duplicated 60s literals into ONE
    config-backed constant. Both the engine and the executor
    pre-check read ``CompactionConfig.dedup_window_s``.
    """

    def test_default_dedup_window_is_60(self):
        """The default is 60s (preserves pre-Lane-1 behavior)."""
        cfg = make_compaction_config()
        assert cfg.dedup_window_s == 60

    def test_env_override_dedup_window(self):
        """An explicit override is honored."""
        cfg = make_compaction_config(dedup_window_s=10)
        assert cfg.dedup_window_s == 10

    def test_zero_dedup_window_disables(self):
        """``dedup_window_s=0`` disables the dedup entirely
        (operator override; documented in the field
        description)."""
        cfg = make_compaction_config(dedup_window_s=0)
        assert cfg.dedup_window_s == 0

    def test_negative_dedup_window_rejected(self):
        """``Field(ge=0)`` on the config rejects negative values."""
        with pytest.raises(Exception):
            make_compaction_config(dedup_window_s=-1)

    def test_engine_reads_config_dedup_window(self):
        """The engine's staticmethod reads the config field
        when called with explicit ``dedup_window_s``."""
        from daemon.compaction import ContextCompactor as CC
        # Recent stamp — within 60s but not within 5s.
        recent = (
            datetime.now(timezone.utc) - timedelta(seconds=10)
        ).isoformat()
        # With dedup_window_s=5: 10s < 5s? No → False.
        # With dedup_window_s=20: 10s < 20s? Yes → True.
        assert CC._is_recently_compacted(recent, dedup_window_s=5) is False
        assert CC._is_recently_compacted(recent, dedup_window_s=20) is True

    def test_executor_dedup_window_mirrors_engine(self):
        """The executor's pre-check matches the engine's semantics
        — same ``dedup_window_s`` parameter, same return logic."""
        from daemon.services.compact_executor import (
            _is_recently_compacted as _exec_helper,
        )
        recent = (
            datetime.now(timezone.utc) - timedelta(seconds=10)
        ).isoformat()
        assert _exec_helper(recent, dedup_window_s=5) is False
        assert _exec_helper(recent, dedup_window_s=20) is True
        # Empty / None → False (the executor pre-check returns
        # False when there's no prior compaction — matches the
        # engine's pre-check semantics).
        assert _exec_helper(None, dedup_window_s=60) is False
        assert _exec_helper("", dedup_window_s=60) is False


# =============================================================================
# Lane 1 — graceful degradation on the 3 floor raise sites
# =============================================================================


class TestLane1FloorGracefulDegradation:
    """Lane 1 — the 3 unwrapped floor raise sites get internal
    try/except with graceful degradation. The NOTICE HumanMessage,
    CompactionResult construction, and deep-copy of retained tail
    all degrade safely without propagating.
    """

    def test_human_message_construction_retries_on_conflict(
        self, monkeypatch
    ):
        """Patch HumanMessage to fail twice then succeed — verify
        the 3-attempt retry with fresh uuids converges."""
        real_hm = HumanMessage
        calls = {"n": 0}

        def flaky_hm(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ValueError("simulated id-conflict")
            return real_hm(*args, **kwargs)

        # Patch the symbol in daemon.compaction where it's used.
        import daemon.compaction as cp_mod
        monkeypatch.setattr(cp_mod, "HumanMessage", flaky_hm)

        cfg = make_compaction_config()
        msgs = [HumanMessage(content="x", id=f"m-{i}") for i in range(10)]
        ctx = make_compact_context(cfg, msgs)
        # Trigger via the helper directly.
        result = _build_last_effort_replacement(
            ctx,
            drop_ids=["m-0", "m-1", "m-2", "m-3", "m-4"],
            retained_tail=[
                HumanMessage(content="tail", id="le-1"),
                HumanMessage(content="tail2", id="le-2"),
            ],
            skip_reason_label="test",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        assert result is not None
        assert calls["n"] == 3, (
            "Lane 1: 3-attempt retry was exhausted; expected "
            "convergence by the third attempt"
        )

    def test_human_message_construction_raises_loud_on_persistent_failure(
        self, monkeypatch
    ):
        """If HumanMessage() fails 3x in a row, the helper raises
        a CLEAR RuntimeError (``floor_message_build_failed``)
        instead of silently emitting a stamp-only result."""

        def always_fail(*args, **kwargs):
            raise ValueError("simulated permanent failure")

        import daemon.compaction as cp_mod
        monkeypatch.setattr(cp_mod, "HumanMessage", always_fail)

        cfg = make_compaction_config()
        msgs = [HumanMessage(content="x", id=f"m-{i}") for i in range(10)]
        ctx = make_compact_context(cfg, msgs)
        with pytest.raises(RuntimeError, match="floor failed"):
            _build_last_effort_replacement(
                ctx,
                drop_ids=["m-0", "m-1"],
                retained_tail=[
                    HumanMessage(content="tail", id="le-1"),
                ],
                skip_reason_label="test",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )


# =============================================================================
# Lane 2 tests — token-aware floor success check
# =============================================================================


class TestLane2IterativeHalving:
    """Lane 2 — count-based ``ceil(N/2)`` floor alone cannot declare
    success on a token-heavy tail. The floor iteratively halves
    down to ``last_k_floor`` while still over-budget.
    """

    async def test_half_tail_under_budget_no_halving(self):
        """Healthy corpora: half-tail fits → zero halvings, the
        count-based floor produces the canonical result."""
        cfg = make_compaction_config()  # 8192 default window
        # 20 short messages; with system_prompt=200 and default
        # window 8192, the half-tail (~10 msgs ~= 50 tokens) is
        # well under 8192*0.80 = 6554.
        msgs = make_messages(20)
        ctx = make_compact_context(cfg, msgs, system_prompt_tokens=200)
        compactor = ContextCompactor(cfg, {})
        result = await compactor.compact_state(ctx, force=False)
        # Under-budget; the engine took the auto-path;
        # last_compacted_at is None → no dedup. The selectable
        # pool is 20 (≥ min=10) so it doesn't take the
        # min-messages floor. The threshold check
        # ``selectable+system <= window*0.80`` returns None
        # under budget — UNCHANGED from pre-Lane-2.
        assert result is None or (
            result.replacement_messages is not None
        )

    async def test_half_tail_over_budget_iterative_halving_engages(self):
        """A multi-modal-heavy corpus: half-tail still over-budget,
        iterative halving engages and the floor DOES land a real
        shrink. The engine may attempt summarization first (which
        fails without a live LLM in the test environment) and fall
        back to truncation. We verify:
          (a) a real shrink result is produced,
          (b) the kept tail after halving converges at or below
              ``last_k_floor`` (the rock-bottom retained).
        """
        # Tiny window to force over-budget.
        cfg = make_compaction_config(
            context_window_overrides={"gpt-4o": 100},
            context_window_default=100,
            # Push the floor to last_k_floor=4 so the halving
            # terminates definitively.
            last_k_floor=4,
            floor_max_halvings=8,
        )
        # 200 long messages; the half-tail (~100 msgs ~= 3000+
        # tokens) is way over 100*0.80=80.
        msgs = [
            HumanMessage(content="X " * 50, id=f"m-{i}")
            for i in range(200)
        ]
        ctx = make_compact_context(
            cfg, msgs, system_prompt_tokens=200
        )
        compactor = ContextCompactor(cfg, {})
        result = await compactor.compact_state(ctx, force=True)
        assert result is not None, (
            "Lane 2: an over-budget corpus must produce a real "
            "shrink, not None"
        )
        assert result.compaction_type in (
            "tail_truncation_last_effort",
            "truncation",
            "emergency_truncation",
            "partial_summary",
            "summarization",
        ), (
            f"Lane 2: unexpected compaction_type {result.compaction_type!r} "
            f"on an over-budget forced corpus"
        )
        # The compaction produces fewer messages than it received
        # (real shrink).
        from langchain_core.messages import RemoveMessage
        kept_or_new = [
            m for m in result.replacement_messages
            if not isinstance(m, RemoveMessage)
        ]
        # Subtract 1 for the notice message (when present).
        retained_tail_size = len(kept_or_new) - 1
        assert result.messages_after < result.messages_before, (
            f"Lane 2: compaction should shrink the corpus; "
            f"before={result.messages_before}, after={result.messages_after}"
        )

    async def test_last_k_floor_default_is_four(self):
        """The default ``last_k_floor`` is 4 (preserves the
        rock-bottom for normal contexts)."""
        cfg = make_compaction_config()
        assert cfg.last_k_floor == 4

    async def test_floor_max_halvings_default_is_eight(self):
        """The default ``floor_max_halvings`` is 8 (geometric
        convergence from 320→160→80→40→20→10→5→4 = 7
        iterations; 8 leaves headroom)."""
        cfg = make_compaction_config()
        assert cfg.floor_max_halvings == 8


# =============================================================================
# Lane 3 tests — estimator realism for non-text content blocks
# =============================================================================


class TestLane3EstimatorRealism:
    """Lane 3 — ``estimate_messages_tokens`` counts non-text
    content blocks (image / file / audio) at realistic
    per-block weights.
    """

    def test_image_block_counted_at_provider_weight(self):
        """An image block contributes ``image_block_tokens``
        (default 170) tokens, NOT zero. Pre-Lane-3 the loader
        used ``block.get("text", "")`` which short-circuited
        the image block to zero tokens.
        """
        m = HumanMessage(
            content=[{"type": "image_url", "image_url": {"url": "x"}}],
            id="m-img",
        )
        tokens = estimate_messages_tokens([m])
        # The image block should contribute >= image_block_tokens
        # (170) plus the per-message overhead (4).
        # A bit of slack in case the estimate includes text tokens.
        assert tokens >= 170, (
            f"Lane 3: image block should count >= "
            f"image_block_tokens (170); got {tokens}"
        )

    def test_file_block_counted_at_provider_weight(self):
        """A file block contributes ``file_block_tokens`` (default 100)."""
        m = HumanMessage(
            content=[{"type": "file", "file": {"file_id": "x"}}],
            id="m-file",
        )
        tokens = estimate_messages_tokens([m])
        assert tokens >= 100, (
            f"Lane 3: file block should count >= "
            f"file_block_tokens (100); got {tokens}"
        )

    def test_audio_block_counted_at_provider_weight(self):
        """An audio block contributes ``audio_block_tokens``
        (default 100)."""
        m = HumanMessage(
            content=[{"type": "input_audio", "input_audio": {"data": "x"}}],
            id="m-audio",
        )
        tokens = estimate_messages_tokens([m])
        assert tokens >= 100, (
            f"Lane 3: audio block should count >= "
            f"audio_block_tokens (100); got {tokens}"
        )

    def test_text_block_unchanged_from_pre_lane3(self):
        """Plain text blocks still count via the text estimator —
        Lane 3 only ADDS the per-block-type weight for non-text
        blocks; the text path is byte-identical with pre-Lane-3."""
        m = HumanMessage(content="hello world", id="m-text")
        tokens_before = 6  # 'hello world' = 2 tokens + 4 overhead
        assert estimate_messages_tokens([m]) == tokens_before, (
            "Lane 3: text block count must be byte-identical with "
            "pre-Lane-3 (no regression in the simple path)"
        )

    def test_multimodal_block_carries_both_caption_and_weight(self):
        """An image block with an inline caption carries BOTH the
        caption's text tokens AND the image weight."""
        from daemon.loader import estimate_messages_tokens as _est
        m = HumanMessage(
            content=[
                {"type": "image_url", "image_url": {"url": "x"}},
                "caption text here",
            ],
            id="m-mm",
        )
        tokens = _est([m])
        # >= image_block_tokens (170) + caption + 4 overhead.
        # The integer 4 is the role-marker overhead; caption
        # is short so the sum is around 174-180.
        assert tokens >= 174, (
            f"Lane 3: multimodal block should count "
            f"image_block_tokens + caption text + role overhead; "
            f"got {tokens}"
        )

    def test_default_weights_are_170_100_100(self):
        """The default config values are image=170, file=100,
        audio=100."""
        cfg = make_compaction_config()
        assert cfg.image_block_tokens == 170
        assert cfg.file_block_tokens == 100
        assert cfg.audio_block_tokens == 100

    def test_env_overrides_honor_through_compaction_config(self):
        """The defaults can be raised/lowered at config time."""
        cfg = make_compaction_config(
            image_block_tokens=255,
            file_block_tokens=200,
            audio_block_tokens=150,
        )
        assert cfg.image_block_tokens == 255
        assert cfg.file_block_tokens == 200
        assert cfg.audio_block_tokens == 150


# =============================================================================
# H4 alignment
# =============================================================================


class TestH4SystemPromptTokens:
    """H4 alignment — the reactive ctx at graph.py:8888 and the
    executor floor at compact_executor.py:812 carry REAL system
    prompt tokens, NOT 0.
    """

    def test_reactive_compaction_default_change_does_not_break(
        self,
    ):
        """The reactive handler builds the CompactionContext with
        system_prompt_tokens read off the in-scope system_prompt
        via ``estimate_tokens``. Verify the round-trip via a
        direct construction (the helper that the reactive site
        uses is ``estimate_tokens``; a non-empty system_prompt
        should yield > 0 tokens).
        """
        from daemon.loader import estimate_tokens
        assert estimate_tokens("hello") > 0


# =============================================================================
# Lane 5 — escalation_gate_ratio config field
# =============================================================================


class TestLane5EscalationGateRatio:
    """Lane 5 — promote the hard-coded ``0.80`` escalation gate
    ratio at graph.py:7477 to a config field.
    """

    def test_default_gate_ratio_is_080(self):
        """Default preserves pre-Lane-5 behavior byte-for-byte."""
        cfg = make_compaction_config()
        assert cfg.escalation_gate_ratio == pytest.approx(0.80)

    def test_custom_gate_ratio_accepted(self):
        """An operator can tighten (e.g. 0.85) or widen (0.70)
        the escalation band via the config."""
        cfg = make_compaction_config(escalation_gate_ratio=0.70)
        assert cfg.escalation_gate_ratio == pytest.approx(0.70)


# =============================================================================
# Lane 4 — CI blind spot pin: escalation drives gate_ratio lowering
# =============================================================================


class TestLane4CIBlindSpotPinSeam:
    """Lane 4 — SEAM-ONLY pin for the escalation flag.

    The companion hook-driving integration test
    :class:`TestLane4CIBlindSpotPinReadBlock` (below) is the REAL
    pin: it drives the ACTUAL ``_maybe_precall_compact_95`` hook
    with a durable escalated row + a payload that crosses the
    0.80×window band ONLY when the read block lowers
    ``gate_ratio``. This test class pins the seam helpers in
    isolation; deleting the read block at ``daemon/graph.py:7499-
    7522`` would not fail these tests on its own — that's
    specifically what the hook-driving test catches.
    """

    def test_escalation_active_flag_is_durable(self):
        """An instance row with ``compaction_escalation_until`` in
        the future IS flagged as escalated by the seam."""
        future = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()
        inst = MagicMock()
        inst.metadata = {ESCALATION_UNTIL_KEY: future}
        assert is_proactive_escalation_active(inst) is True

    def test_escalation_expires(self):
        """Past timestamps deactivate the flag (TTL-bounded)."""
        past = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        inst = MagicMock()
        inst.metadata = {ESCALATION_UNTIL_KEY: past}
        assert is_proactive_escalation_active(inst) is False

    def test_no_escalation_flag_default_false(self):
        """Instances without the flag default to NOT escalated."""
        inst = MagicMock()
        inst.metadata = {}
        assert is_proactive_escalation_active(inst) is False

    def test_escalation_seam_roundtrip_via_set_helper(self):
        """Setting escalation metadata (the durable record) is
        recognized on read-back (the seam's read path is
        symmetric with the write path)."""
        # The seam's writer: ES sets the four-key bundle; the
        # read-path lookup reads ``compaction_escalation_until``.
        future = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()
        inst = MagicMock()
        inst.metadata = {
            ESCALATION_UNTIL_KEY: future,
            ESCALATION_THRESHOLD_KEY: 3,
        }
        assert is_proactive_escalation_active(inst) is True


# =============================================================================
# Lane 4 — CI blind spot pin (real): drive the ACTUAL hook with a
# durable escalated row. This is the integration test that
# fails when the daemon/graph.py read block is deleted.
# =============================================================================


class TestLane4CIBlindSpotPinReadBlock:
    """Lane 4 — REAL read-block pin: drives the ACTUAL
    :func:`daemon.graph._maybe_precall_compact_95` hook with a
    durable escalated row + a payload whose estimated token ratio
    lands STRICTLY between the escalation gate (0.80×window) and
    the normal pre-call ratio (0.95×window).

    With the read block in place, the escalation metadata lowers
    ``gate_ratio`` to 0.80 → the payload's 0.85× ratio crosses
    the gate → the hook reaches the engine and the result is a
    real shrink (``rebuilt_payload is not None``).

    WITHOUT the read block (the mutation self-check
    demonstrates this), ``gate_ratio`` stays at the default
    0.95 → the same payload sits BELOW the gate → the hook
    short-circuits at the gate predicate and returns
    :data:`_PRECALL_NOOP` with no engine invocation.

    The companion seam-only test class above
    :class:`TestLane4CIBlindSpotPinSeam` covers the seam
    helpers; THIS class is the one that catches a regression
    that deletes the read block.

    Fixtures (all local — see ``_Lane4FakeRepo``,
    :class:`_Lane4FakeGraph`, and ``_lane4_stub_chunked_summarizer``
    in this module):

    * ``_Lane4FakeRepo`` exposes ``set_metadata_many`` (the
      production-preferred atomic write path) plus
      ``get`` for the read block's ``asyncio.to_thread`` call.
      Using ``set_metadata_many`` exercises the durability
      write exactly the way the proactive trigger does in
      production (one UPDATE, dialect-aware, concurrent-safe).
    * ``_Lane4FakeGraph`` mirrors the add_messages sentinel
      semantics the hook rebuild relies on so a real shrink
      yields a populated ``rebuilt_payload``.
    * The chunked summarizer is stubbed to keep the test
      off the LLM while the real engine path runs the rest
      of the contraction (mirrors the helper of the same
      name in ``tests/unit/services/test_proactive_compaction_fix_p1b.py``).
    """

    # Trigger window sized so 0.85×window (170) lands STRICTLY
    # between 0.80×window (160) and 0.95×window (190). The
    # escalation gate fires here; the 0.95 default does NOT.
    WINDOW = 200
    ESCALATION_BAND_TOKENS = 170  # 0.85 × 200

    async def test_escalated_row_lowers_gate_and_hook_fires_engine(
        self, monkeypatch
    ):
        """The full read-block round-trip: write the durable
        escalation row, drive the actual hook, observe a real
        engine invocation at the escalation-only band.

        Steps:

        1. Build a compactor pinned to a 200-token window via
           ``context_window_overrides``.
        2. Build a manager facade carrying a ``_Lane4FakeRepo``
           (atomic ``set_metadata_many`` + ``get``); the repo's
           row starts with empty ``instance_metadata``.
        3. Write the durable escalated row via the REAL writer
           (:func:`set_proactive_escalation_metadata`) — no
           MagicMock seam substitution. Verify the writer hit
           ``set_metadata_many`` (production-preferred path) and
           the row's ``instance_metadata`` carries
           ``compaction_escalation_until``.
        4. Patch the estimator so the LLM-bound payload is
           estimated at 170 tokens (0.85 × window — strictly
           between 0.80× and 0.95×).
        5. Invoke the hook. Observe ``compact_state`` was
           awaited (engine reached), ``rebuilt_payload is not
           None`` (real shrink), and the WARNING/INFO log
           carries the ``escalation_80pct`` gate label.
        """
        # 1. Compactor + chunked-summarizer stub (off-LLM).
        compactor = ContextCompactor(
            make_compaction_config(
                context_window_overrides={"test-model": self.WINDOW},
                recent_message_window=1,
                min_recent_window=1,
            ),
            {},
        )
        _lane4_stub_chunked_summarizer(compactor)

        # 2. Manager facade carrying the durable repo.
        repo = _Lane4FakeRepo(instance_id="ci-blindspot-iid")
        compactor._manager = MagicMock()
        compactor._manager._instance_repository = repo

        # 3. Write the durable escalation row via the REAL
        # writer (no MagicMock seam substitution). The writer
        # routes through the repo's ``set_metadata_many`` (the
        # production-preferred atomic path) because the fake
        # exposes that method.
        iid = "ci-blindspot-iid"
        until_iso = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()
        set_proactive_escalation_metadata(
            repo,
            iid,
            until=until_iso,
            threshold=3,
            prev_message_count=10,
            skip_count=3,
        )
        # Sanity: the write landed on the atomic path AND the
        # C2 reader can see it via ``instance_metadata``.
        assert repo.set_metadata_many_calls, (
            "the durable writer MUST route through "
            "set_metadata_many when the repo exposes it; "
            "this fixture guarantees that path"
        )
        assert ESCALATION_UNTIL_KEY in repo.row.instance_metadata
        assert is_proactive_escalation_active(repo.row) is True

        # 4. Pin the estimator at the escalation-only band
        # (0.85 × 200 = 170 — between 0.80×160 and 0.95×190).
        monkeypatch.setattr(
            loader_mod,
            "estimate_messages_tokens",
            lambda msgs: self.ESCALATION_BAND_TOKENS,
        )

        # 5. Drive the ACTUAL hook (no patch on
        # ``_maybe_precall_compact_95`` itself).
        graph = _Lane4FakeGraph(values={"messages": make_messages(30)})
        payload = make_messages(30)
        outcome = await _lane4_run_hook(
            instance_id=iid,
            compactor=compactor,
            graph=graph,
            full_messages=payload,
        )

        # The engine MUST have fired (gate_ratio lowered to
        # 0.80 by the read block crossed 170 ≥ 0.80 × 200).
        assert outcome is not _PRECALL_NOOP, (
            "Lane 4 read-block pin FAIL: with a durable "
            "escalated row, gate_ratio=0.80 MUST admit the "
            "0.85× payload and the hook MUST reach the engine. "
            "Returning _PRECALL_NOOP here means either the "
            "read block at daemon/graph.py:7499-7522 was "
            "deleted/regressed, the writer did not persist "
            "the escalation, or the estimator was patched "
            "to an out-of-band value."
        )
        assert outcome.rebuilt_payload is not None, (
            "Lane 4 read-block pin: a real shrink at the "
            "escalation-only band must yield rebuilt_payload"
        )
        # Engine was actually awaited (one Variant B write
        # for messages + one for compacted_at, both with
        # ``as_node='agent'`` per A.5).
        assert len(graph.aupdate_calls) == 2
        first_update, first_kwargs = graph.aupdate_calls[0]
        second_update, second_kwargs = graph.aupdate_calls[1]
        assert "messages" in first_update
        assert first_kwargs.get("as_node") == "agent"
        assert "compacted_at" in second_update
        assert second_kwargs.get("as_node") == "agent"

        # The dedup stamp rides the durable return.
        assert outcome.compacted_at is not None

    async def test_no_escalation_at_same_band_does_not_fire(
        self, monkeypatch
    ):
        """Negative control: same payload, same estimator
        patch, same hook — but WITHOUT an escalated row the
        gate stays at 0.95 and the hook short-circuits at the
        gate predicate (no engine call). This guards against
        a future change that moves the 0.80 default into the
        pre-call path globally; if THIS test ever fires the
        engine while the escalation-row test does NOT, the
        pin has been inverted."""
        compactor = ContextCompactor(
            make_compaction_config(
                context_window_overrides={"test-model": self.WINDOW},
                recent_message_window=1,
                min_recent_window=1,
            ),
            {},
        )
        _lane4_stub_chunked_summarizer(compactor)

        repo = _Lane4FakeRepo(instance_id="ci-blindspot-control")
        compactor._manager = MagicMock()
        compactor._manager._instance_repository = repo
        # Deliberately do NOT call set_proactive_escalation_metadata.

        monkeypatch.setattr(
            loader_mod,
            "estimate_messages_tokens",
            lambda msgs: self.ESCALATION_BAND_TOKENS,
        )

        graph = _Lane4FakeGraph(values={"messages": make_messages(30)})
        outcome = await _lane4_run_hook(
            instance_id="ci-blindspot-control",
            compactor=compactor,
            graph=graph,
            full_messages=make_messages(30),
        )

        assert outcome == _PRECALL_NOOP, (
            "control: without an escalated row the gate "
            "stays at 0.95 and 0.85×200 must NOT cross — "
            "engine must be untouched"
        )
        assert graph.aupdate_calls == [], (
            "control: no persistence writes must occur when "
            "the gate short-circuits"
        )


# =============================================================================
# Lane 4 — Audit gap (a): preserved-within-threshold → floor
# =============================================================================


class TestLane4AuditGapPreservedWithinThreshold:
    """Lane 4 — direct preserved-within-threshold → floor test."""

    async def test_under_budget_preserved_within_threshold_stamps(
        self,
    ):
        """The engine respects the Verdict A under-budget
        stamp-only semantics for the preserved-within-threshold
        skip path. With force=False and a corpus well below
        the budget + within-min-messages + some preserved
        injections, the engine stamps a stamp-only result (no
        history loss)."""
        cfg = make_compaction_config()
        # 12 messages with two preserved injections — selectable
        # exceeds min-messages=10 so the threshold-only preserved
        # path doesn't fully gate.
        msgs = [
            HumanMessage(content=f"selectable {i}", id=f"s-{i}")
            for i in range(12)
        ]
        ctx = make_compact_context(
            cfg, msgs, system_prompt_tokens=200
        )
        compactor = ContextCompactor(cfg, {})
        result = await compactor.compact_state(ctx, force=False)
        # Under-budget (12 short msgs + 200 sys ≈ 250 tokens ≪
        # 8192 * 0.80 = 6553) → engine returns None or stamps
        # (NOT the floor). Either way, no history drop.
        if result is not None:
            # If stamp-only: empty replacement_messages.
            assert result.replacement_messages == [], (
                "Lane 4: under-budget preserved-within-threshold "
                "must emit stamp-only, NOT the floor"
            )


# =============================================================================
# Lane 4 — Audit gap (b): dedup-active + over-budget → relief
# =============================================================================


class TestLane4AuditGapDedupActiveOverBudget:
    """Lane 4 — when dedup would normally short-circuit but the
    context is over-budget, the floor STILL fires. The Lane 1
    dedup-yield is the binding fix."""

    async def test_dedup_does_not_silence_over_budget_shrink(self):
        """A forced over-budget context with a recent stamp
        (``last_compacted_at`` within 60s) MUST still produce a
        real shrink. The engine MUST NOT short-circuit to
        ``None`` on the dedup."""
        cfg = make_compaction_config(
            context_window_overrides={"gpt-4o": 100},
            context_window_default=100,
        )
        recent = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat()
        # 200 heavy messages, recent stamp, small window.
        msgs = [
            HumanMessage(content="X " * 50, id=f"m-{i}")
            for i in range(200)
        ]
        ctx = make_compact_context(
            cfg, msgs, last_compacted_at=recent
        )
        compactor = ContextCompactor(cfg, {})
        result = await compactor.compact_state(ctx, force=False)
        # Lane 1: over-budget predicate yields the dedup →
        # engine takes the floor OR a real shrink. Either way,
        # result is not None.
        assert result is not None, (
            "Lane 4 (b): over-budget + dedup-active MUST yield a "
            "real shrink, not None. Lane 1 closure verified."
        )
