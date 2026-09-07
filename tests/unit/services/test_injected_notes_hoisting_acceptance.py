"""Injected-notes hoisting — end-to-end acceptance chain (scenario-closure).

TEST GATE §1 — chained ACCEPTANCE test for the fix/injected-notes-hoisting
scenario. Mirrors the P2-style acceptance-chain pattern from
``tests/unit/services/test_proactive_compaction_symptom_acceptance.py`` —
REAL LangGraph + REAL file-backed SQLite checkpointer at ``tmp_path`` + the
REAL ``ContextCompactor`` selection/envelope/persist-seam machinery.

What this file proves (every leg is asserted on the reloaded checkpoint,
not on a mock):

  (1) ANSWERED bare injected notes are ABSENT from the post-compaction
      channel (their ids are gone; the compacted span replaced them).
  (2) ``CompactionResult.injected_absorbed`` counter reflects the count
      of absorbed answered notes (real field name, real envelope math).
  (3) The ``context_kind`` [SYSTEM CONTEXT] block is STILL present
      AND STILL hoisted above the compaction doc (fold card) — relative
      ordering pin.
  (4) The UNANSWERED bare injected note is STILL present VERBATIM
      (full content match, original id) AND hoisted above the fold card.
  (5) The fold card (``compaction-global-*`` SystemMessage) is intact.

Negative control (non-vacuousness): the same scenario with the
``ENSEMBLE_INJECTED_NOTES_ABSORB`` kill-switch OFF (the legacy behavior)
degenerates to ALL bare notes hoisted — the original incident shape — so
the acceptance chain here is not a tautology of the engine + a generic
mock.

The ONLY stubbed seam is ``ContextCompactor._call_summarization_llm`` —
the LLM network call that produces the summary body. Selection logic,
envelope accounting, the ``build_sentinel_replacement`` seam, and the
real ``persist_compaction_result`` (which calls ``graph.aupdate_state``
twice against the real ``AsyncSqliteSaver``) are all 100% real. The
compaction message tap and the proactive gate chain are deliberately
out of scope (covered by sibling tests); the seam itself is the
canonical write path both the executor and the proactive trigger
consume.

Architecture references:

  * ``daemon/compaction.py`` — ``ContextCompactor.compact_state`` (the
    engine that produces ``CompactionResult`` with
    ``injected_absorbed`` / ``injected_preserved``).
  * ``daemon/services/_compaction_persist_seam.py`` — the shared
    ``persist_compaction_result`` seam (Variant A: TWO ``aupdate_state``
    writes WITHOUT ``as_node``).
  * ``daemon/loader.py`` — ``estimate_messages_tokens`` (the trigger math).
  * Original incident: ``809e2a59`` prod instance — 12 operator notes
    pinned above the fold card FOREVER before the fix.
"""
from __future__ import annotations

import sys
from typing import Any

import pytest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


# =============================================================================
# Real-langgraph swap (mirror of test_proactive_compaction_symptom_acceptance.py)
# =============================================================================

_MOCKED_LANGGRAPH_KEYS = (
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.prebuilt",
    "langgraph.constants",
    "langgraph.checkpoint",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
)


class _RealLangGraph:
    """Swap the conftest's mocked langgraph modules for the real ones
    around a block of test code, then identity-restore (mirrors the
    P2 / executor-e2e harness discipline).

    The root ``tests/conftest.py`` installs lightweight ``MagicMock``
    langgraph stubs for unit tests; this acceptance chain needs the real
    ``AsyncSqliteSaver`` + ``StateGraph`` + ``aupdate_state`` semantics —
    same swap pattern as the proactive-compaction symptom acceptance.
    """

    def __enter__(self):
        self._original_modules = {
            k: sys.modules[k] for k in _MOCKED_LANGGRAPH_KEYS if k in sys.modules
        }
        for key in _MOCKED_LANGGRAPH_KEYS:
            if key in sys.modules:
                del sys.modules[key]
        for key in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[key]
        return self

    def __exit__(self, exc_type, exc, tb):
        for key in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[key]
        for key, mod in self._original_modules.items():
            sys.modules[key] = mod
        return False


# =============================================================================
# Helpers — mirror tests/unit/services/test_injected_notes_hoisting.py
# =============================================================================


_PADDING = "lorem ipsum dolor sit amet consectetur adipiscing elit " * 3


def _bare_note(content: str, msg_id: str) -> HumanMessage:
    """Operator note: bare ``injected_message=True``, NO ``context_kind``.

    Stable construction-time id per the project message-id invariant
    (foundation pattern in
    ``daemon/services/context_messages.py::_make_context_message``) —
    every persisted HumanMessage in this test carries an explicit ``id=``.
    """
    return HumanMessage(
        content=content,
        id=msg_id,
        additional_kwargs={"injected_message": True},
    )


def _ctx_note(content: str, msg_id: str) -> HumanMessage:
    """Real ``[SYSTEM CONTEXT]`` block: flag + ``context_kind``."""
    return HumanMessage(
        content=content,
        id=msg_id,
        additional_kwargs={
            "injected_message": True,
            "context_kind": "task_context",
        },
    )


def _padded_regulars(prefix: str, ids: list[str]) -> list[HumanMessage]:
    """Regular history — sized to exceed the trigger window at runtime.

    Stable construction-time id on every message.
    """
    return [
        HumanMessage(content=f"{prefix}-{i} {_PADDING}", id=ids[i])
        for i in range(len(ids))
    ]


# =============================================================================
# THE acceptance chain
# =============================================================================


# Anchored config: a tiny context window + low threshold so the scenario
# fires fast under the 5-minute budget. The values mirror the proactive
# symptom acceptance idiom (engine body 100% real; only the LLM seam
# stubbed). The trigger window pins to a model name NOT in the
# ``MODEL_CONTEXT_LIMITS`` registry so the override is the only
# source-of-truth for the trigger math.
_MODEL = "incident-acceptance-model"
_WINDOW = 400
_THRESHOLD_RATIO = 0.5
_TRIGGER_TOKENS = int(_WINDOW * _THRESHOLD_RATIO)


def _make_compaction_config(**overrides: Any):
    """Real ``CompactionConfig`` anchored for the trigger math."""
    from daemon.config import CompactionConfig

    defaults: dict[str, Any] = {
        "enabled": True,
        "threshold": _THRESHOLD_RATIO,
        "recent_message_window": 2,
        "min_recent_window": 1,
        "context_window_overrides": {_MODEL: _WINDOW},
        "context_window_default": 0,
        "target_ratio": 0.40,
        "model": "",
        "summarization_model": "",
        "min_messages_before_compaction": 3,
        "summarization_chunk_threshold": 1.0,
        "timeout_base_s": 60.0,
        "timeout_per_100k_tokens_s": 60.0,
        "timeout_cap_s": 300.0,
        "timeout_facade_margin_s": 5.0,
        "operation_budget_s": 120.0,
        "chunk_concurrency": 2,
        "proactive_enabled": True,
    }
    defaults.update(overrides)
    return CompactionConfig(**defaults)


def _make_real_compactor(llm_seen: list[str]) -> Any:
    """Real ``ContextCompactor`` with the LLM seam stubbed.

    Everything else (selection, envelope, grouping, threshold, the
    ``build_sentinel_replacement`` seam) is 100% real.
    """
    from daemon.compaction import ContextCompactor

    config = _make_compaction_config()
    compactor = ContextCompactor(
        config=config,
        llm_config={
            "model": _MODEL,
            "base_url": "http://127.0.0.1:9",  # never reached (LLM stubbed)
            "api_key": "sk-test",
            "temperature": 0.0,
        },
    )

    async def _fake_call(prompt: str, ctx: Any) -> str:
        llm_seen.append(prompt)
        return (
            "ACCEPTANCE SUMMARY: compacted span captured the absorb "
            "scenario; context_kind + UNANSWERED bare notes hoisted; "
            "ANSWERED bare notes absorbed into the doc; no blockers."
        )

    compactor._call_summarization_llm = _fake_call  # type: ignore[method-assign]
    return compactor, config


def _build_compaction_context(
    *,
    messages: list,
    config: Any,
    instance_id: str,
) -> Any:
    """Real ``CompactionContext`` with ``instance_id`` stamped (production
    shape)."""
    from daemon.compaction import CompactionContext

    return CompactionContext(
        messages=messages,
        system_prompt_tokens=0,
        model_name=_MODEL,
        config=config,
        llm_config={
            "model": _MODEL,
            "base_url": "http://127.0.0.1:9",
            "api_key": "sk-test",
            "temperature": 0.0,
        },
        last_compacted_at=None,
        instance_id=instance_id,
    )


class TestInjectedNotesHoistingAcceptanceChain:
    """ONE test chains the FULL incident scenario end-to-end on REAL
    machinery (real LangGraph + real checkpointer + real engine +
    real persist seam). The test asserts all five legs of the chain
    on the reloaded checkpoint state, not on a mock."""

    @pytest.mark.asyncio
    async def test_full_incident_scenario_chain_end_to_end(self, tmp_path):
        """The full incident scenario:

          (i)   ONE ``context_kind`` [SYSTEM CONTEXT] block — PERMANENT,
                preserved verbatim and hoisted above the fold card;
          (ii)  ONE ANSWERED bare injected note — followed later by an
                AIMessage — absorbed into the compacted span;
          (iii) ONE UNANSWERED bare injected note — trailing, no later
                AIMessage — preserved verbatim and hoisted above the
                fold card;
          (iv)  Regular history large enough to exceed the compaction
                trigger (real estimator, real window).

        Engine + persist-seam + checkpoint are real. Only the LLM seam
        is stubbed. The fresh-connection reload is the durability step
        the original incident missed (the legacy code wrote the absorb
        decision but never verified the checkpoint on reload — so the
        hoisted notes stayed pinned even after a "successful" compaction).
        """
        with _RealLangGraph():
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import END, START, MessagesState, StateGraph

            from daemon.compaction import build_sentinel_replacement
            from daemon.loader import estimate_messages_tokens
            from daemon.services._compaction_persist_seam import (
                persist_compaction_result,
            )

            # The production state shape: ``MessagesState`` plus the
            # ``compacted_at`` scalar channel the seam stamps.
            class _IncidentState(MessagesState):
                compacted_at: str | None

            def _build_graph(saver: Any) -> Any:
                async def _agent(state: Any) -> dict:
                    return {"messages": []}

                g = StateGraph(_IncidentState)
                g.add_node("agent", _agent)
                g.add_edge(START, "agent")
                g.add_edge("agent", END)
                return g.compile(checkpointer=saver)

            iid = "incident-acceptance-iid"
            cfg = {"configurable": {"thread_id": iid}}

            # ── File-backed SQLite at tmp_path: the project recipe
            # (never StaticPool + WriteGuardSession; the file is the
            # connection boundary, mirroring the ratchet note for
            # shared-worktree test execution). PRAGMA WAL +
            # busy_timeout applied post-connect; aiosqlite carries no
            # SQLAlchemy poolclass, so the per-saver fresh connection
            # is the equivalent of "NullPool".
            db_path = tmp_path / "incident_acceptance.db"
            conn = await aiosqlite.connect(str(db_path))
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=10000")
            await conn.commit()
            saver = AsyncSqliteSaver(conn)
            await saver.setup()

            try:
                compiled = _build_graph(saver)

                # ── Seed the channel with the FULL scenario.
                # The proactive-compaction symptom acceptance has the
                # same pattern: an initial ``ainvoke`` to establish the
                # checkpoint followed by ``aupdate_state(as_node=...)``
                # to overwrite the channel with the scenario. The
                # first ``ainvoke`` is required: langgraph's
                # ``aperform_superstep`` rejects the seam's
                # ``aupdate_state`` (Variant A — no as_node) until the
                # graph has completed at least one superstep, so the
                # checkpoint carries no pending tasks.
                await compiled.ainvoke(
                    {
                        "messages": [
                            HumanMessage(content="seed-turn-1", id="h-seed1"),
                        ]
                    },
                    cfg,
                )
                ctx_note = _ctx_note(
                    "[SYSTEM CONTEXT: incident]\nCTX-BODY-MUST-SURVIVE",
                    "ctx-incident",
                )
                answered_note = _bare_note(
                    "ANSWERED-OPS-NOTE-MUST-BE-ABSENT-AFTER",
                    "note-answered",
                )
                unanswered_note = _bare_note(
                    "UNANSWERED-OPS-NOTE-MUST-STAY-VERBATIM",
                    "note-unanswered",
                )
                # 14 padded regular messages spread across the channel
                # so the regular-history span is the one the engine
                # groups + summarizes; both the absorbed note and the
                # unanswered note sit inside the selectable / preserved
                # span boundary respectively.
                regulars = _padded_regulars(
                    "r", [f"r-{i}" for i in range(14)]
                )
                seed: list = (
                    [ctx_note]
                    + regulars[:3]
                    + [
                        answered_note,
                        AIMessage(
                            content="acknowledged, logging",
                            id="ai-ack-1",
                        ),
                    ]
                    + regulars[3:7]
                    + [
                        AIMessage(
                            content="intermediate reply",
                            id="ai-intermediate",
                        ),
                    ]
                    + regulars[7:]
                    + [unanswered_note]
                )
                await compiled.aupdate_state(
                    cfg, {"messages": seed}, as_node="agent"
                )

                # ── PRE-STATE — quiescent between-turn checkpoint,
                # every seed id present, history ABOVE the trigger.
                st_pre = await compiled.aget_state(cfg)
                assert st_pre.next == (), (
                    f"PRE-STATE broken: between-turn checkpoint must be "
                    f"quiescent (next == ()); got {st_pre.next!r}"
                )
                pre_messages = list(st_pre.values["messages"])
                pre_ids = {m.id for m in pre_messages if m.id}
                for required in (
                    "ctx-incident",
                    "note-answered",
                    "note-unanswered",
                ):
                    assert required in pre_ids, (
                        f"PRE-STATE broken: seed id {required!r} missing"
                    )
                pre_tokens = estimate_messages_tokens(pre_messages)
                assert pre_tokens > _TRIGGER_TOKENS, (
                    f"PRE-STATE broken: history must be ABOVE the "
                    f"trigger ({pre_tokens} !> {_TRIGGER_TOKENS})"
                )

                # ── Engine + seam: real ``compact_state`` → real
                # ``persist_compaction_result``. Only the LLM seam is
                # stubbed.
                llm_seen: list[str] = []
                compactor, config = _make_real_compactor(llm_seen)
                ctx_obj = _build_compaction_context(
                    messages=pre_messages,
                    config=config,
                    instance_id=iid,
                )
                result = await compactor.compact_state(ctx_obj)

                assert result is not None, (
                    "ENGINE BROKEN: compaction skipped on the full "
                    "scenario (regular history is non-empty; should "
                    "compact)."
                )
                assert result.compaction_type == "summarization", (
                    f"ENGINE BROKEN: expected summarization exit on "
                    f"the full scenario; got {result.compaction_type}"
                )

                # ── LEG (2) — envelope math from the LIVE engine
                # result (the real field names; pinned for the FE /
                # executor / leader readers):
                #   * 1 answered bare note absorbed (note-answered)
                #   * 2 hoisted (ctx-incident + note-unanswered)
                assert result.injected_absorbed == 1, (
                    f"LEG (2) BROKEN: injected_absorbed must reflect "
                    f"the single ANSWERED note; got "
                    f"{result.injected_absorbed}"
                )
                assert result.injected_preserved == 2, (
                    f"LEG (2) BROKEN: injected_preserved must reflect "
                    f"ctx_kind + UNANSWERED (2); got "
                    f"{result.injected_preserved}"
                )
                # The absorbed note's content reached the summarizer
                # (the inverse of the original permanent-preserve pin).
                assert any(
                    "ANSWERED-OPS-NOTE-MUST-BE-ABSENT-AFTER" in p
                    for p in llm_seen
                ), (
                    "ENGINE BROKEN: ANSWERED bare note never reached "
                    "the summarizer — did not join the selectable pool"
                )
                # The ctx_kind body NEVER reaches the summarizer.
                for prompt in llm_seen:
                    assert "CTX-BODY-MUST-SURVIVE" not in prompt, (
                        "ENGINE BROKEN: context_kind body leaked into "
                        "the summarization prompt"
                    )

                # ── PERSIST — real seam, real ``aupdate_state``.
                written = await persist_compaction_result(
                    manager=None,
                    instance_id=iid,
                    result=result,
                    mid_turn=False,
                    abort_policy="raise",
                    graph=compiled,
                )
                assert written is True, (
                    "PERSIST BROKEN: seam returned False under raise "
                    "policy — abort or no-write detected"
                )

                # ── FRESH-connection reload — the durability check
                # the original incident missed. New ``aiosqlite``
                # connection + new ``AsyncSqliteSaver`` on the SAME
                # file proves the writes are durable, not in a
                # connection-local cache.
                conn2 = await aiosqlite.connect(str(db_path))
                await conn2.execute("PRAGMA journal_mode=WAL")
                await conn2.execute("PRAGMA busy_timeout=10000")
                await conn2.commit()
                fresh_saver = AsyncSqliteSaver(conn2)
                await fresh_saver.setup()
                try:
                    fresh_compiled = _build_graph(fresh_saver)
                    st_after = await fresh_compiled.aget_state(cfg)

                    assert st_after.next == (), (
                        "PERSIST BROKEN: Variant A must leave next "
                        f"untouched; got {st_after.next!r}"
                    )
                    assert st_after.values.get("compacted_at"), (
                        "PERSIST BROKEN: compacted_at stamp missing "
                        "from the reloaded checkpoint"
                    )

                    after_messages = list(st_after.values["messages"])
                    assert after_messages, (
                        "PERSIST BROKEN: empty channel after compaction"
                    )
                    after_ids = [
                        getattr(m, "id", None) for m in after_messages
                    ]
                    after_ids_set = {i for i in after_ids if i}

                    # ── LEG (5): fold card (compaction-global-*
                    # SystemMessage) is intact.
                    fold_card = next(
                        (
                            m for m in after_messages
                            if isinstance(m, SystemMessage)
                            and (getattr(m, "id", "") or "").startswith(
                                "compaction-global-"
                            )
                        ),
                        None,
                    )
                    assert fold_card is not None, (
                        "LEG (5) BROKEN: fold card (compaction-global-*) "
                        "must be present in the reloaded channel"
                    )
                    fold_idx = after_messages.index(fold_card)
                    assert str(getattr(fold_card, "content", "")).strip(), (
                        "LEG (5) BROKEN: fold card content must be "
                        "non-empty"
                    )

                    # ── LEG (3): context_kind block STILL present
                    # AND hoisted above the fold card.
                    ctx_kept = next(
                        (
                            m for m in after_messages
                            if getattr(m, "id", None) == "ctx-incident"
                        ),
                        None,
                    )
                    assert ctx_kept is not None, (
                        "LEG (3) BROKEN: context_kind block must "
                        "SURVIVE compaction"
                    )
                    assert ctx_kept.content == (
                        "[SYSTEM CONTEXT: incident]\nCTX-BODY-MUST-SURVIVE"
                    ), (
                        "LEG (3) BROKEN: context_kind block content "
                        "MUST be verbatim"
                    )
                    assert (
                        (ctx_kept.additional_kwargs or {}).get(
                            "context_kind"
                        )
                        == "task_context"
                    ), (
                        "LEG (3) BROKEN: context_kind kwarg must "
                        "survive (it is the permanent-preserve signal)"
                    )
                    ctx_idx = after_messages.index(ctx_kept)
                    assert ctx_idx < fold_idx, (
                        f"LEG (3) BROKEN: context_kind block MUST be "
                        f"hoisted ABOVE the fold card "
                        f"(ctx at index {ctx_idx}, fold at index {fold_idx})"
                    )

                    # ── LEG (4): UNANSWERED note STILL present
                    # VERBATIM AND hoisted above the fold card.
                    unanswered_kept = next(
                        (
                            m for m in after_messages
                            if getattr(m, "id", None)
                            == "note-unanswered"
                        ),
                        None,
                    )
                    assert unanswered_kept is not None, (
                        "LEG (4) BROKEN: UNANSWERED note MUST "
                        "survive compaction — the safety invariant"
                    )
                    assert unanswered_kept.content == (
                        "UNANSWERED-OPS-NOTE-MUST-STAY-VERBATIM"
                    ), (
                        f"LEG (4) BROKEN: UNANSWERED note content "
                        f"MUST be verbatim; got "
                        f"{unanswered_kept.content!r}"
                    )
                    unanswered_idx = after_messages.index(unanswered_kept)
                    assert unanswered_idx < fold_idx, (
                        f"LEG (4) BROKEN: UNANSWERED note must be "
                        f"hoisted ABOVE the fold card "
                        f"(unanswered at index {unanswered_idx}, "
                        f"fold at index {fold_idx})"
                    )

                    # ── LEG (1): ANSWERED bare notes ABSENT from
                    # the post-state channel.
                    assert "note-answered" not in after_ids_set, (
                        "LEG (1) BROKEN: ANSWERED bare note id must "
                        f"be ABSENT after compaction; post-state ids: "
                        f"{after_ids}"
                    )
                    # Belt-and-braces: the original note content
                    # should not appear in the reloaded channel
                    # either (defense-in-depth — the hoist head must
                    # not echo the absorbed note's body).
                    assert not any(
                        getattr(m, "content", "") == (
                            "ANSWERED-OPS-NOTE-MUST-BE-ABSENT-AFTER"
                        )
                        for m in after_messages
                    ), (
                        "LEG (1) BROKEN: ANSWERED bare note content "
                        "appeared in the reloaded channel "
                        "(should be absorbed into the doc only)"
                    )

                    # ── Foreign-id guard: post-state ⊆
                    # (pre-state ∪ {fold_card_id, synthetic-*}).
                    # The fold card id is NEW (added by the seam);
                    # every other id must come from the pre-state.
                    pre_and_new = pre_ids | {fold_card.id}
                    assert after_ids_set <= pre_and_new, (
                        "PERSIST BROKEN: post-state contains foreign "
                        f"ids not in pre-state and not the fold card: "
                        f"{after_ids_set - pre_and_new}"
                    )

                    # ── build_sentinel_replacement shape pin: the
                    # seam's own composition is
                    # [RemoveMessage(sentinel), *head, *doc, *tail].
                    # Sanity-pin that the seam itself returns the
                    # expected ordering when fed the live result.
                    replacement = build_sentinel_replacement(
                        result,
                        pre_messages,
                        compacted_ids=result.compacted_ids,
                    )
                    keepables = [
                        m for m in replacement
                        if not isinstance(m, __import__(
                            "langchain_core.messages",
                            fromlist=["RemoveMessage"],
                        ).RemoveMessage)
                    ]
                    # Head = ctx_kind + UNANSWERED, in order.
                    assert keepables[0].id == "ctx-incident", (
                        "SEAM BROKEN: head element 0 must be the "
                        "context_kind block; got "
                        f"{keepables[0].id!r}"
                    )
                    assert keepables[1].id == "note-unanswered", (
                        "SEAM BROKEN: head element 1 must be the "
                        "UNANSWERED bare note; got "
                        f"{keepables[1].id!r}"
                    )
                    # Doc immediately after the head.
                    assert isinstance(keepables[2], SystemMessage), (
                        "SEAM BROKEN: element 2 must be the compaction "
                        f"SystemMessage doc; got {type(keepables[2]).__name__}"
                    )
                    assert (
                        keepables[2].id or ""
                    ).startswith("compaction-global-"), (
                        "SEAM BROKEN: doc id must be "
                        f"compaction-global-*; got {keepables[2].id!r}"
                    )
                    # The answered note is NOT in the seam's head
                    # (and not anywhere — it was absorbed into the
                    # compacted span).
                    seam_ids = [m.id for m in keepables]
                    assert "note-answered" not in seam_ids, (
                        "SEAM BROKEN: answered note leaked into the "
                        "seam's replacement"
                    )
                finally:
                    await conn2.close()
            finally:
                await conn.close()

    @pytest.mark.asyncio
    async def test_flag_off_same_scenario_hoists_everything_legacy_shape(
        self, tmp_path
    ):
        """Negative control (non-vacuousness): the SAME scenario with
        ``ENSEMBLE_INJECTED_NOTES_ABSORB=0`` (the kill-switch OFF)
        degenerates to the legacy two-bucket hoist shape: ALL bare
        notes are preserved verbatim and hoisted above the fold
        card — the original incident shape. ``context_kind`` is
        unchanged in BOTH states (the pin holds across both
        scenarios). This proves the acceptance chain above is not
        a tautology of the engine + a generic mock: flip the flag,
        leg (1) reverses (note-answered REAPPEARS, hoisted verbatim)
        and leg (2) flips to ``injected_preserved=3,
        injected_absorbed=0``.
        """
        import os

        # Set BEFORE any engine import reads the resolver — mirrors
        # the existing kill-switch test idiom.
        os.environ["ENSEMBLE_INJECTED_NOTES_ABSORB"] = "0"
        try:
            with _RealLangGraph():
                import aiosqlite
                from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
                from langgraph.graph import (
                    END, START, MessagesState, StateGraph,
                )

                from daemon.services._compaction_persist_seam import (
                    persist_compaction_result,
                )

                class _IncidentState(MessagesState):
                    compacted_at: str | None

                def _build_graph(saver: Any) -> Any:
                    async def _agent(state: Any) -> dict:
                        return {"messages": []}

                    g = StateGraph(_IncidentState)
                    g.add_node("agent", _agent)
                    g.add_edge(START, "agent")
                    g.add_edge("agent", END)
                    return g.compile(checkpointer=saver)

                iid = "incident-acceptance-flagoff-iid"
                cfg = {"configurable": {"thread_id": iid}}

                db_path = tmp_path / "incident_acceptance_flagoff.db"
                conn = await aiosqlite.connect(str(db_path))
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA busy_timeout=10000")
                await conn.commit()
                saver = AsyncSqliteSaver(conn)
                await saver.setup()
                try:
                    compiled = _build_graph(saver)

                    # Initial superstep to establish the checkpoint
                    # (see test_full_incident_scenario_chain_end_to_end
                    # for the rationale).
                    await compiled.ainvoke(
                        {
                            "messages": [
                                HumanMessage(
                                    content="seed-turn-1", id="h-seed1"
                                ),
                            ]
                        },
                        cfg,
                    )

                    ctx_note = _ctx_note(
                        "[SYSTEM CONTEXT: incident]\nCTX-BODY-MUST-SURVIVE",
                        "ctx-incident",
                    )
                    answered_note = _bare_note(
                        "ANSWERED-OPS-NOTE-FLAGOFF-MUST-HOIST",
                        "note-answered",
                    )
                    unanswered_note = _bare_note(
                        "UNANSWERED-OPS-NOTE-FLAGOFF-MUST-HOIST",
                        "note-unanswered",
                    )
                    regulars = _padded_regulars(
                        "r", [f"r-{i}" for i in range(14)]
                    )
                    seed: list = (
                        [ctx_note]
                        + regulars[:3]
                        + [
                            answered_note,
                            AIMessage(
                                content="acknowledged, logging",
                                id="ai-ack-1",
                            ),
                        ]
                        + regulars[3:7]
                        + [
                            AIMessage(
                                content="intermediate reply",
                                id="ai-intermediate",
                            ),
                        ]
                        + regulars[7:]
                        + [unanswered_note]
                    )
                    await compiled.aupdate_state(
                        cfg, {"messages": seed}, as_node="agent"
                    )

                    st_pre = await compiled.aget_state(cfg)
                    pre_messages = list(st_pre.values["messages"])

                    llm_seen: list[str] = []
                    compactor, config = _make_real_compactor(llm_seen)
                    ctx_obj = _build_compaction_context(
                        messages=pre_messages,
                        config=config,
                        instance_id=iid,
                    )
                    result = await compactor.compact_state(ctx_obj)
                    assert result is not None
                    assert result.compaction_type == "summarization"

                    # Envelope FLIPS to the legacy accounting under OFF.
                    assert result.injected_absorbed == 0, (
                        f"FLAG-OFF BROKEN: injected_absorbed must be "
                        f"0 under OFF; got {result.injected_absorbed}"
                    )
                    assert result.injected_preserved == 3, (
                        f"FLAG-OFF BROKEN: injected_preserved must "
                        f"be 3 (ctx_kind + 2 bare) under OFF; got "
                        f"{result.injected_preserved}"
                    )
                    # The answered bare note NEVER reaches the
                    # summarizer under OFF (the legacy hoist shape).
                    assert not any(
                        "ANSWERED-OPS-NOTE-FLAGOFF-MUST-HOIST" in p
                        for p in llm_seen
                    ), (
                        "FLAG-OFF BROKEN: answered bare note leaked "
                        "into the summarization prompt under OFF — "
                        "should be hoisted verbatim"
                    )

                    written = await persist_compaction_result(
                        manager=None,
                        instance_id=iid,
                        result=result,
                        mid_turn=False,
                        abort_policy="raise",
                        graph=compiled,
                    )
                    assert written is True

                    conn2 = await aiosqlite.connect(str(db_path))
                    await conn2.execute("PRAGMA journal_mode=WAL")
                    await conn2.execute("PRAGMA busy_timeout=10000")
                    await conn2.commit()
                    fresh_saver = AsyncSqliteSaver(conn2)
                    await fresh_saver.setup()
                    try:
                        fresh_compiled = _build_graph(fresh_saver)
                        st_after = await fresh_compiled.aget_state(cfg)
                        after_messages = list(st_after.values["messages"])
                        after_ids_set = {
                            getattr(m, "id", None)
                            for m in after_messages
                        }
                        after_ids_set.discard(None)

                        # Both bare notes are HOISTED (re-appear).
                        assert "note-answered" in after_ids_set, (
                            "FLAG-OFF BROKEN: answered bare note MUST "
                            "re-appear hoisted under OFF"
                        )
                        assert "note-unanswered" in after_ids_set, (
                            "FLAG-OFF BROKEN: unanswered bare note "
                            "MUST re-appear hoisted under OFF"
                        )
                        # ctx_kind STILL hoisted above the fold card
                        # (the pin holds across both states).
                        ctx_kept = next(
                            (
                                m for m in after_messages
                                if getattr(m, "id", None)
                                == "ctx-incident"
                            ),
                            None,
                        )
                        assert ctx_kept is not None
                        fold_card = next(
                            (
                                m for m in after_messages
                                if isinstance(m, SystemMessage)
                                and (
                                    getattr(m, "id", "") or ""
                                ).startswith("compaction-global-")
                            ),
                            None,
                        )
                        assert fold_card is not None
                        assert (
                            after_messages.index(ctx_kept)
                            < after_messages.index(fold_card)
                        )
                    finally:
                        await conn2.close()
                finally:
                    await conn.close()
        finally:
            os.environ.pop("ENSEMBLE_INJECTED_NOTES_ABSORB", None)
