"""Original-symptom acceptance — chained proactive compaction on a resume
dispatch (proactive-compaction-fix).

THE ORIGINAL SYMPTOM (the 810-msg / ~493k-token waiting_children
orchestrator of 2026-09-04): a long-lived instance ABOVE the 80% trigger
threshold NEVER compacted. Three stacked root causes, all fixed on this
branch:

* L1 — the terminal-SHAPE gate read every quiescent between-turn
  checkpoint (``next == ()``) as terminal → the trigger skipped BEFORE
  any token math.
* L2 — the ``if not is_retry:`` blanket skip excluded cascade-RESUME
  dispatches (the waiting_children orchestrator lane — the flagship
  victims).
* L3 — the engine's numerator counted regular messages only, so
  injection-heavy orchestrators never crossed the threshold even at
  800+ messages.

This file chains ALL FOUR acceptance links in ONE test on the REAL
machinery (real StateGraph + real file-backed AsyncSqliteSaver + real
gate chain + real ContextCompactor engine + real shared persist seam).
Only the LLM seam (``daemon.graph.ThinkingChatOpenAI`` summarize/merge
client) is stubbed — no network:

(i)   PRE-STATE  — quiescent between-turn checkpoint (``next == ()``),
      instance status NON-terminal (``waiting_children``), and the REAL
      ``estimate_messages_tokens`` (the same estimator the engine uses)
      proves the history is ABOVE the trigger (window × threshold).
(ii)  DISPATCH  — the REAL ``_process_message_with_tracking`` is driven
      on the RESUME lane (``is_retry=True`` + ``message_source=
      "cascade_resume"`` — the exact lane the pre-P2 blanket skip
      starved), not a fresh dispatch and not a direct trigger call.
(iii) FIRE      — the real gate chain (flag → status → shape → engine)
      proceeds and the REAL ``compact_state`` runs exactly once
      (counting passthrough spy — the engine body is 100% real).
(iv)  PERSIST   — the compacted state is DURABLE: reloaded through a
      FRESH AsyncSqliteSaver connection on the same file, the
      compaction-global SystemMessage doc is present, the compacted
      span ids are gone, the preserved tail survives, ``compacted_at``
      is stamped, ``next`` is untouched (Variant A), and the captured
      ``aupdate_state`` recipe is exactly TWO ordered writes WITHOUT
      ``as_node``.

Negative control (non-vacuousness): the SAME scenario with the
proactive kill-switch OFF (``proactive_enabled=False`` — the field the
``ENSEMBLE_PROACTIVE_COMPACTION`` env maps onto at ``load_config``; the
gate reads the field, env resolution itself is pinned by the P1 file)
does NOT compact: engine 0 calls, zero checkpoint writes, history
untouched and still above the trigger.

Patterned after the real-graph harness in
``tests/unit/services/test_proactive_compaction_fix_p2.py``
(``_RealLangGraph`` module swap + ``_GraphWrapper`` write capture, the
O17 binding pattern from
``tests/unit/services/test_compact_executor_revive_brick_e2e.py``).

Architecture references:

* ``daemon/services/instance_messaging.py`` — ``_process_message_with_tracking``
  (unconditional trigger at the resume lane, ~:3827) + ``_maybe_compact_context``
  (gate chain: flag → status → shape → engine → shared seam).
* ``daemon/compaction.py`` — ``ContextCompactor.compact_state`` (dedup →
  injected partition → min-messages → honest numerator → threshold →
  grouping/selection → summarize/merge via ``ThinkingChatOpenAI``).
* ``daemon/services/_compaction_persist_seam.py`` — Variant A persist
  (two ``aupdate_state`` calls, no ``as_node``, sentinel element 0).
* ``daemon/loader.py`` — ``estimate_messages_tokens`` (the trigger math).
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.compaction import ContextCompactor, get_model_context_limit
from daemon.config import CompactionConfig
from daemon.loader import estimate_messages_tokens
from daemon.services.instance_messaging import InstanceMessagingService
from langchain_core.messages import AIMessage, HumanMessage


# =============================================================================
# Fixture constants — sized so the REAL token math crosses the trigger
# =============================================================================

_MODEL = "acceptance-model"  # NOT in the MODEL_CONTEXT_LIMITS registry
_WINDOW = 2000               # anchored via context_window_overrides
_THRESHOLD_RATIO = 0.80
_TRIGGER_TOKENS = int(_WINDOW * _THRESHOLD_RATIO)  # 1600
_CHUNKY = "x" * 4000         # ≈1000 cl100k tokens each → 10 of them ≈ 10k


def _make_compaction_config(**overrides: Any) -> CompactionConfig:
    """CompactionConfig anchored for the real threshold math: the
    override pins the trigger window at 2000 so a 10-message chunky
    history (~10k real tiktoken tokens) sits ~6× ABOVE the 1600-token
    trigger — the original incident's ratio, reproducible in a tmp file.
    """
    defaults: dict[str, Any] = {
        "enabled": True,
        "threshold": _THRESHOLD_RATIO,
        "recent_message_window": 4,
        "min_recent_window": 2,
        "context_window_overrides": {_MODEL: _WINDOW},
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
    return CompactionConfig(**defaults)


def _make_manager(*, instance_status: str = "waiting_children") -> MagicMock:
    """Manager mock exposing every surface the RESUME dispatch touches
    before/at the compaction trigger (mirror of the P2 ``_build_service``
    manager surface, with string-typed llm attrs — the trigger builds a
    real ``CompactionContext`` from them). ``waiting_children`` is the
    orchestrator status from the incident — NON-terminal, so the status
    gate (``COMPACT_REJECT_STATUSES``) must let it through.
    """
    mgr = MagicMock()
    mgr.config = MagicMock()
    mgr.config.compaction = _make_compaction_config()
    mgr.config.llm.model = _MODEL
    mgr.config.llm.base_url = "http://127.0.0.1:9"  # never reached (LLM stubbed)
    mgr.config.llm.api_key = "test-key"
    mgr.config.llm.model_vision = ""
    mgr.config.llm.temperature = 0.7
    mgr.config.llm.request_timeout = 30
    mgr.config.llm.buffer_response_header = True
    mgr.config.limits.graph_recursion_limit = 100
    # Instance row: agent_id=None keeps agent-meta resolution on its
    # no-op branch; instance_metadata={} keeps the prompt-token lookup
    # deterministic (cache miss → 0 system tokens).
    mgr._instance_repository.get = MagicMock(
        return_value=SimpleNamespace(
            agent_id=None,
            agent_tag=None,
            status=instance_status,
            instance_metadata={},
        )
    )
    mgr._instance_repository.set_metadata = MagicMock()
    # D2 seam drain: empty injection FIFO.
    mgr.get_injection = MagicMock(return_value=None)
    mgr.clear_injection = MagicMock(return_value=None)
    mgr.message_metadata_repo = None
    # ``_has_checkpoint`` reads the raw saver via the adapter property.
    # Truthy → the resume lane (is_retry=True) takes the checkpointed
    # resume path, as a real cascade-resume dispatch would.
    mgr._checkpointer.raw_saver.aget = AsyncMock(return_value=MagicMock())
    # Deterministic system-prompt tokens (real code path, cache miss → 0).
    mgr.prompt_cache.get = MagicMock(return_value=None)
    return mgr


def _install_real_compactor(mgr: MagicMock) -> dict:
    """Install the REAL ``ContextCompactor`` engine behind a counting
    passthrough spy. The engine body (dedup, injected partition,
    min-messages, honest numerator, threshold, grouping/selection,
    summarization) is 100% real — the spy only observes invocations.
    """
    compactor = ContextCompactor(
        mgr.config.compaction,
        {
            "base_url": "http://127.0.0.1:9",
            "api_key": "test-key",
            "model": _MODEL,
            "model_vision": "",
            "temperature": 0.7,
            "request_timeout": 30,
            "buffer_response_header": True,
        },
    )
    real_compact_state = compactor.compact_state
    calls = {"n": 0}

    async def _counting_compact_state(ctx, force: bool = False):
        calls["n"] += 1
        return await real_compact_state(ctx, force=force)

    compactor.compact_state = _counting_compact_state
    mgr._compactor = compactor
    return calls


class _FakeSummarizationResult:
    """Minimal LLM response stand-in: the engine reads ``.content``."""

    content = (
        "ACCEPTANCE SUMMARY: orchestrator coordinated child workers; "
        "key decisions and artifacts recorded; no blockers outstanding."
    )


def _fake_llm_factory() -> tuple:
    """``ThinkingChatOpenAI`` replacement: every construction returns the
    same instance whose ``invoke`` returns a canned summarization result.
    Unlimited responses — the chunked path may make several calls.
    """
    instance = MagicMock()
    instance.invoke = MagicMock(return_value=_FakeSummarizationResult())
    return (lambda **kwargs: instance), instance


# =============================================================================
# Real-graph harness (mirrors test_proactive_compaction_fix_p2.py)
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
    around a block of test code, then restore (identity-restore
    discipline, mirrors the P2 / executor-e2e harness)."""

    def __enter__(self):
        self._original_modules = {
            k: sys.modules[k] for k in _MOCKED_LANGGRAPH_KEYS if k in sys.modules
        }
        for key in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[key]
        return self

    def __exit__(self, exc_type, exc, tb):
        for key in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[key]
        for key, mod in self._original_modules.items():
            sys.modules[key] = mod
        return False


class _GraphWrapper:
    """Wrap a real ``CompiledStateGraph`` so ``aupdate_state`` calls are
    CAPTURED (payload + kwargs) while delegating verbatim — the seam's
    writes land in the REAL checkpointer and the exact recipe
    (2 ordered writes, no ``as_node``) stays assertable."""

    def __init__(self, real_graph: Any) -> None:
        self._real = real_graph
        self.aupdate_state_calls: list[tuple] = []

    async def aget_state(self, config):
        return await self._real.aget_state(config)

    async def aupdate_state(self, config, values, **kwargs):
        self.aupdate_state_calls.append((config, values, kwargs))
        return await self._real.aupdate_state(config, values, **kwargs)

    def __getattr__(self, name: str) -> Any:  # delegate the rest
        return getattr(self._real, name)


# =============================================================================
# THE acceptance chain
# =============================================================================


class TestOriginalSymptomAcceptanceChain:
    """ONE test, FOUR links, REAL machinery — the original symptom
    proven CLOSED end-to-end on the cascade-resume lane."""

    @pytest.mark.asyncio
    async def test_resume_dispatch_above_threshold_compacts_and_persists_end_to_end(
        self, tmp_path, caplog
    ):
        with _RealLangGraph():
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import END, START, MessagesState, StateGraph

            # Mirror the production state shape: ``SessionState`` is a
            # ``MessagesState`` subclass with a ``compacted_at`` channel
            # — without the channel the seam's stamp write would be
            # silently dropped.
            class _WakeState(MessagesState):
                compacted_at: str | None

            def _build_graph(saver):
                g = StateGraph(_WakeState)
                g.add_node("agent", _agent)
                g.add_edge(START, "agent")
                g.add_edge("agent", END)
                return g.compile(checkpointer=saver)

            runs: list[str] = []
            counter = {"n": 0}

            async def _agent(state):
                runs.append("ran")
                counter["n"] += 1
                return {
                    "messages": [
                        AIMessage(content="agent-out", id=f"echo-{counter['n']}")
                    ]
                }

            iid = "acceptance-orchestrator"
            cfg = {"configurable": {"thread_id": iid}}

            db_path = tmp_path / "symptom_acceptance.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                compiled = _build_graph(saver)

                # ── Seed a REAL conversation: one real graph run (turn 1)
                # plus a chunky orchestrated history seeded through the
                # real checkpointer (the seeding shape used by the
                # executor revive-brick e2e harness). Result: a quiescent
                # between-turn checkpoint carrying ~10k REAL tiktoken
                # tokens — 6× above the 1600-token trigger.
                await compiled.ainvoke(
                    {"messages": [HumanMessage(content="turn-1", id="h-turn1")]},
                    cfg,
                )
                seeded: list = []
                for i in range(10):
                    cls = HumanMessage if i % 2 == 0 else AIMessage
                    seeded.append(cls(content=_CHUNKY, id=f"seed-{i}"))
                seeded.append(HumanMessage(content="recent tail a", id="seed-tail-a"))
                seeded.append(AIMessage(content="recent tail b", id="seed-tail-b"))
                await compiled.aupdate_state(
                    cfg, {"messages": seeded}, as_node="agent"
                )

                # ── LINK (i): PRE-STATE — quiescent, non-terminal status,
                # never compacted, and ABOVE the trigger by the REAL
                # estimator the engine itself uses.
                st_pre = await compiled.aget_state(cfg)
                assert st_pre.next == (), (
                    f"PRE-STATE broken: between-turn checkpoint must be "
                    f"quiescent (next == ()) — got {st_pre.next!r}"
                )
                assert st_pre.values.get("compacted_at") is None, (
                    "PRE-STATE broken: instance must never have compacted"
                )
                pre_messages = list(st_pre.values["messages"])
                pre_ids = {m.id for m in pre_messages}
                pre_tokens = estimate_messages_tokens(pre_messages)
                assert pre_tokens > _TRIGGER_TOKENS, (
                    f"PRE-STATE broken: history must be ABOVE the trigger "
                    f"({pre_tokens} !> {_TRIGGER_TOKENS})"
                )
                # The window anchor the trigger math will resolve.
                assert (
                    get_model_context_limit(_MODEL, _make_compaction_config())
                    == _WINDOW
                )

                # ── The REAL engine behind a counting spy + the REAL
                # service against the wrapped REAL graph.
                mgr = _make_manager(instance_status="waiting_children")
                assert mgr._instance_repository.get.return_value.status not in (
                    "terminated",
                    "error",
                    "failed",
                ), "PRE-STATE broken: status must be NON-terminal"
                compact_calls = _install_real_compactor(mgr)
                svc = InstanceMessagingService(
                    manager=mgr,
                    cancellation_service=SimpleNamespace(manager=mgr),
                )
                wrapped = _GraphWrapper(compiled)
                mgr.get_instance = AsyncMock(return_value=wrapped)

                # ── LINK (ii): DISPATCH on the RESUME lane — the exact
                # call shape ``manager._resume_processing_background``
                # makes for a cascade-resume wake of a waiting_children
                # orchestrator (is_retry=True, cascade_resume). Pre-P2
                # this lane never reached the trigger at all (L2).
                factory, _llm_instance = _fake_llm_factory()
                with patch("daemon.graph.ThinkingChatOpenAI", factory):
                    dispatch_error: BaseException | None = None
                    try:
                        await svc._process_message_with_tracking(
                            instance_id=iid,
                            message="child report wake",
                            message_id="mid-resume-1",
                            is_retry=True,  # the cascade-resume lane
                            retry_count=0,
                            message_source="cascade_resume",
                            silent=False,
                        )
                    except Exception as exc:  # noqa: BLE001
                        # The post-trigger dispatch tail (streaming,
                        # usage emission, mirrors) is beyond the four
                        # links; the durable assertions below are
                        # self-certifying — compaction state can only
                        # exist if dispatch → trigger → engine → seam
                        # ALL ran.
                        dispatch_error = exc
                if dispatch_error is not None:
                    print(
                        "[acceptance] dispatch tail error (out of chain "
                        f"scope): {dispatch_error!r}"
                    )

                # ── LINK (iii): FIRE — the REAL engine ran exactly once
                # on this dispatch (gate chain: flag ON → status
                # waiting_children → shape quiescent → engine).
                assert compact_calls["n"] == 1, (
                    f" FIRE broken: the real compaction engine must run "
                    f"exactly once on the resume dispatch; got "
                    f"{compact_calls['n']}"
                )

                # ── LINK (iv): PERSIST — durable through a FRESH
                # connection + saver instance on the SAME file.
                conn2 = await aiosqlite.connect(str(db_path))
                fresh_saver = AsyncSqliteSaver(conn2)
                await fresh_saver.setup()
                try:
                    fresh_compiled = _build_graph(fresh_saver)
                    st_after = await fresh_compiled.aget_state(cfg)

                    # Variant A left the resume handle untouched.
                    assert st_after.next == (), (
                        f"PERSIST broken: Variant-A persist must leave "
                        f"next untouched; got {st_after.next!r}"
                    )
                    # The compaction-global doc is present and stamped.
                    after_messages = list(st_after.values["messages"])
                    assert after_messages, "PERSIST broken: empty channel"
                    doc = after_messages[0]
                    assert getattr(doc, "type", "") == "system", (
                        f"PERSIST broken: element 0 must be the "
                        f"compaction-global SystemMessage; got "
                        f"{type(doc).__name__}"
                    )
                    assert str(getattr(doc, "id", "")).startswith(
                        "compaction-global-"
                    ), (
                        f"PERSIST broken: doc id must be compaction-global-*; "
                        f"got {getattr(doc, 'id', None)!r}"
                    )
                    assert str(doc.content).strip(), (
                        "PERSIST broken: compaction doc must carry a "
                        "non-empty summary body"
                    )
                    # The compacted span is GONE; the preserved tail and
                    # doc survive (compacted_ids semantics, site-verified).
                    after_ids = {m.id for m in after_messages}
                    for dropped in [f"seed-{i}" for i in range(8)]:
                        assert dropped not in after_ids, (
                            f"PERSIST broken: compacted-span id {dropped!r} "
                            f"survived compaction"
                        )
                    for kept in ("seed-tail-a", "seed-tail-b"):
                        assert kept in after_ids, (
                            f"PERSIST broken: preserved-tail id {kept!r} "
                            f"was dropped"
                        )
                    assert after_ids <= (pre_ids | {doc.id}), (
                        "PERSIST broken: post-state contains foreign ids"
                    )
                    # The stamp is durable.
                    assert st_after.values.get("compacted_at"), (
                        "PERSIST broken: compacted_at stamp missing from "
                        "the reloaded checkpoint"
                    )
                    # THE SYMPTOM IS CLOSED: history went from ~6× above
                    # the trigger to BELOW it — the gate will not re-fire
                    # on the next dispatch.
                    after_tokens = estimate_messages_tokens(after_messages)
                    assert after_tokens < _TRIGGER_TOKENS, (
                        f"SYMPTOM NOT CLOSED: post-compaction usage "
                        f"{after_tokens} still >= trigger {_TRIGGER_TOKENS}"
                    )

                    # The captured persist recipe: exactly TWO ordered
                    # aupdate_state writes, NO as_node (Variant A),
                    # messages-first, REMOVE_ALL sentinel at element 0.
                    assert len(wrapped.aupdate_state_calls) == 2, (
                        f"PERSIST broken: expected exactly 2 aupdate_state "
                        f"writes (messages, compacted_at); got "
                        f"{len(wrapped.aupdate_state_calls)}"
                    )
                    (cfg1, vals1, kw1), (cfg2, vals2, kw2) = (
                        wrapped.aupdate_state_calls
                    )
                    assert kw1 == {} and kw2 == {}, (
                        f"PERSIST broken: Variant A must NEVER pass "
                        f"as_node/kwargs; got {kw1!r}, {kw2!r}"
                    )
                    assert cfg1 == cfg2 == {
                        "configurable": {"thread_id": iid}
                    }, f"PERSIST broken: writes targeted {cfg1!r}, {cfg2!r}"
                    assert "messages" in vals1 and "compacted_at" not in vals1
                    assert "compacted_at" in vals2 and "messages" not in vals2
                    from langgraph.graph.message import REMOVE_ALL_MESSAGES

                    first_write_msg = vals1["messages"][0]
                    assert getattr(first_write_msg, "id", None) == (
                        REMOVE_ALL_MESSAGES
                    ), (
                        "PERSIST broken: sentinel must be element 0 of the "
                        "messages write"
                    )
                finally:
                    await conn2.close()
            finally:
                await conn.close()

    @pytest.mark.asyncio
    async def test_proactive_flag_off_same_scenario_does_not_compact(
        self, tmp_path
    ):
        """Negative control (non-vacuousness): the SAME seeded instance
        on the SAME resume lane with the kill-switch OFF does NOT
        compact. ``proactive_enabled`` is the config field the
        ``ENSEMBLE_PROACTIVE_COMPACTION`` env maps onto at load_config;
        the gate reads the field (env resolution is pinned by the P1
        file). Zero engine calls, zero checkpoint writes, history
        untouched and STILL above the trigger — the pre-fix symptom
        state, preserved under the flag.
        """
        with _RealLangGraph():
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import END, START, MessagesState, StateGraph

            class _WakeState(MessagesState):
                compacted_at: str | None

            counter = {"n": 0}

            async def _agent(state):
                counter["n"] += 1
                return {
                    "messages": [
                        AIMessage(content="agent-out", id=f"echo-{counter['n']}")
                    ]
                }

            iid = "acceptance-flag-off"
            cfg = {"configurable": {"thread_id": iid}}

            db_path = tmp_path / "symptom_flag_off.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                g = StateGraph(_WakeState)
                g.add_node("agent", _agent)
                g.add_edge(START, "agent")
                g.add_edge("agent", END)
                compiled = g.compile(checkpointer=saver)

                await compiled.ainvoke(
                    {"messages": [HumanMessage(content="turn-1", id="h-turn1")]},
                    cfg,
                )
                seeded: list = []
                for i in range(10):
                    cls = HumanMessage if i % 2 == 0 else AIMessage
                    seeded.append(cls(content=_CHUNKY, id=f"seed-{i}"))
                seeded.append(HumanMessage(content="recent tail a", id="seed-tail-a"))
                seeded.append(AIMessage(content="recent tail b", id="seed-tail-b"))
                await compiled.aupdate_state(
                    cfg, {"messages": seeded}, as_node="agent"
                )
                st_pre = await compiled.aget_state(cfg)
                assert st_pre.next == ()
                pre_ids = {m.id for m in st_pre.values["messages"]}
                assert (
                    estimate_messages_tokens(st_pre.values["messages"])
                    > _TRIGGER_TOKENS
                )

                mgr = _make_manager(instance_status="waiting_children")
                mgr.config.compaction.proactive_enabled = False  # kill-switch
                compact_calls = _install_real_compactor(mgr)
                svc = InstanceMessagingService(
                    manager=mgr,
                    cancellation_service=SimpleNamespace(manager=mgr),
                )
                wrapped = _GraphWrapper(compiled)
                mgr.get_instance = AsyncMock(return_value=wrapped)

                factory, _ = _fake_llm_factory()
                with patch("daemon.graph.ThinkingChatOpenAI", factory):
                    try:
                        await svc._process_message_with_tracking(
                            instance_id=iid,
                            message="child report wake",
                            message_id="mid-resume-1",
                            is_retry=True,
                            retry_count=0,
                            message_source="cascade_resume",
                            silent=False,
                        )
                    except Exception:
                        pass

                assert compact_calls["n"] == 0, (
                    "KILL-SWITCH BROKEN: flag OFF must never reach the engine"
                )
                assert wrapped.aupdate_state_calls == [], (
                    "KILL-SWITCH BROKEN: flag OFF must not write the checkpoint"
                )
                st_after = await compiled.aget_state(cfg)
                assert st_after.values.get("compacted_at") is None, (
                    "KILL-SWITCH BROKEN: compacted_at stamped under flag OFF"
                )
                after_ids = {m.id for m in st_after.values["messages"]}
                assert after_ids == pre_ids, (
                    "KILL-SWITCH BROKEN: message channel mutated under "
                    "flag OFF"
                )
                assert (
                    estimate_messages_tokens(st_after.values["messages"])
                    > _TRIGGER_TOKENS
                ), "history should STILL be above the trigger under flag OFF"
            finally:
                await conn.close()
