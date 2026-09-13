"""Symptom-repair engine — durable, facade-wrapped, budget-enforced repair.

Hallucination-recovery ladder PHASE 1 (ADR-0001/0002/0006). This module
evolves the shipped transient ``LoopRepairer`` (``daemon/graph.py``) into
``SymptomRepairEngine``: ONE repair engine with a per-class PRESET TABLE
(loop class only in phase 1), producing a **return-carried
sentinel-first** surgery so the repair is CHECKPOINT-COMMITTED (durable)
instead of the shipped in-memory filter (transient — restart/revive
replayed the original degenerate history).

Design contract (per ``.agents/shared/planning/hallucination-recovery-ladder/``):

* **Durable carrier (P-6/P-7/P-10).** The surgery output is
  ``[RemoveMessage(REMOVE_ALL_MESSAGES), *hoisted-injected, *repair-doc,
  *retained-tail-with-ORIGINAL-ids]`` — the ``build_sentinel_replacement``
  shape (``daemon/compaction.py``). The sentinel MUST be element 0
  (anything before it is discarded by the reducer). The caller (the
  ``agent_node`` return assembly in ``daemon/graph.py``) carries this
  prefix on the NODE RETURN so the task commit lands it atomically.
  There is deliberately NO ``aupdate_state`` / ``aget_state`` call
  anywhere in this module — mid-superstep ``aupdate_state`` persists are
  SUPERSEDED by the in-flight task commit (canary
  ``TestMidSuperstepPersistCanary``), and node-stamped configs read EMPTY
  snapshots (the ``checkpoint_ns`` trap). Return-carried is the ONLY
  durable recipe.

* **Durable budget (B-4).** The engine consults the per-task durable
  budget carried in the context BEFORE every attempt; at cap
  (:data:`SYMPTOM_REPAIR_BUDGET`, mirroring the shipped ``max_repairs=3``
  semantics) it refuses without surgery and the caller escalates
  (loud terminal). The caller is the single increment site — on
  SUCCESSFUL repair only (abort consumes nothing).

* **Facade-wrapped summarizer (C-1/C-2, ADR-0006).** The summarizer
  client is built via ``wrap_langchain_failover`` (compaction's pattern):
  empty/degenerate output raises inside the retry scope → bounded retry →
  failover → on ultimate failure the repair ABORTS fail-open
  (:class:`SymptomRepairAborted`): no surgery, budget NOT consumed, the
  turn falls through to the existing retry/failover/terminal rungs. The
  shipped static truncation fallback is deliberately NOT reproduced here —
  a degenerate LLM summary must never silently enter history. (The shipped
  ``LoopRepairer`` keeps its fallback for the kill-switch-OFF path, where
  behavior is pinned byte-identical.)

* **Persist-refusal = abort (C-3, T-12).** The pre-write guard (same
  discipline as ``build_sentinel_replacement`` /
  ``persist_compaction_result`` returning ``False``) refuses a surgery
  that would silently lose a snapshot message; refusal aborts the repair
  fail-open.

* **Tool-unit folding (P-8).** Removed AIMessages take their paired
  ToolMessages with them (``tool_call_id`` matching) — a defensive sweep
  guarantees the retained tail never orphans a ToolMessage whose issuing
  AIMessage was removed as loop evidence.

Module import hygiene: this module is imported LAZILY from
``daemon.graph`` (the ``daemon.services`` package ``__init__`` transitively
pulls graph-adjacent modules, so a module-level import from graph would
cycle). In return, every ``daemon.graph`` / ``daemon.compaction`` symbol
used here is imported lazily inside methods — mirroring the established
``_call_summarization_llm`` lazy-import pattern in ``daemon/compaction.py``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, ClassVar

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    SystemMessage,
    ToolMessage,
)

logger = logging.getLogger(__name__)

#: Durable per-task repair budget. Mirrors the shipped ``max_repairs=3``
#: semantics (``LoopBreakerConfig.max_repairs``), which REMAINS the
#: per-TURN RAM expression; this is the per-TASK durable expression
#: (checkpoint-persisted ``repair_budget_used`` GraphState field).
SYMPTOM_REPAIR_BUDGET = 3

#: Per-call summarizer timeout default (matches
#: ``LoopBreakerConfig.summarization_timeout_seconds`` default = 120s).
#: Used as the dataclass default for ``SymptomRepairContext`` and as the
#: engine's last-resort fallback when neither the context nor the
#: instance overrides it.
DEFAULT_SUMMARIZATION_TIMEOUT_S = 120

#: Maximum characters of the verbatim ``tool_args`` JSON snippet included
#: in the repair doc and the summarization prompt. Mirrors the shipped
#: compaction excerpt cap (C-4 safety: bound the verbatim payload the
#: LLM is asked to acknowledge).
MAX_VERBATIM_ARGS_CHARS = 500

#: Repair-doc id namespace (A-4): ``repair-{instance_id}-{seq}``.
#: Deliberately DISTINCT from the compaction doc namespace
#: ``compaction-global-{instance}-{seq}`` so repair docs and compaction
#: docs never collide on the id-keyed reducer, and the per-class seq
#: parsers (this module's and ``_next_compaction_seq``) stay isolated.
REPAIR_DOC_ID_PREFIX = "repair-"


class SymptomRepairAborted(Exception):
    """Fail-open abort of a symptom repair.

    Raised by the summarizer leg (empty/degenerate output after the
    facade's bounded retry + failover) and by the pre-write guard
    (persist-refusal). The caller treats ANY abort as "repair skipped":
    no surgery, durable budget NOT consumed, the turn falls through to
    the next rung (shipped retry/failover/terminal backstops).
    """

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class SymptomRepairContext:
    """Inputs for one :meth:`SymptomRepairEngine.repair` invocation.

    Mirrors the shipped ``RepairContext`` fields the engine actually
    consumes; the shipped context's ``graph`` / ``thread_config`` fields
    are deliberately ABSENT — the return-carried carrier never touches
    the checkpoint mid-flight (P-10), so there is nothing to pass.

    Attributes:
        detection: Detector result for the symptom class (loop class:
            ``daemon.graph.LoopDetectionResult`` — duck-typed here so the
            engine stays detector-agnostic for later enrollments).
        messages: Full in-memory conversation history at detection time
            (oldest-first). This is the snapshot the surgery is built
            against and the pre-write guard defends.
        llm_config: Session LLM config for the summarizer (raw dict —
            ``wrap_langchain_failover`` consumes ``base_url_backup``
            from it and cleans the rest for client construction).
        system_prompt: Session system prompt (carried for payload-rebuild
            parity with the caller; not consumed by the engine itself).
        injected_msg: Closure-local injected ``HumanMessage``s pending
            this turn; re-appended by the CALLER after repair (C3
            pattern). The engine does not fold them into the surgery —
            they are not checkpoint state yet.
        summarization_timeout_seconds: Site-level ``asyncio.wait_for``
            backstop for the summarizer call (120s per
            ``LoopBreakerConfig.summarization_timeout_seconds`` — config
            unchanged, C-4). The facade's own ``wall_clock_cap_s``
            (inherited default 45s) trips first in practice.
        instance_id: Owning instance id — scopes the repair-doc id
            namespace ``repair-{instance_id}-{seq}`` (A-4).
        budget_used: Current durable per-task repair budget
            (``repair_budget_used`` GraphState value).
        budget_cap: Budget ceiling (:data:`SYMPTOM_REPAIR_BUDGET`).
    """

    detection: Any
    messages: list[BaseMessage]
    llm_config: dict[str, Any]
    system_prompt: str
    injected_msg: list[BaseMessage] | None = None
    summarization_timeout_seconds: int = DEFAULT_SUMMARIZATION_TIMEOUT_S
    instance_id: str = ""
    budget_used: int = 0
    budget_cap: int = SYMPTOM_REPAIR_BUDGET


@dataclass
class SymptomRepairOutcome:
    """Outcome of one :meth:`SymptomRepairEngine.repair` invocation.

    Core fields mirror the shipped ``RepairResult`` surface (the loop
    class's public contract is preserved); the durable-carrier fields are
    additive. On ANY abort ``success`` is ``False``, ``aborted`` is
    ``True``, ``surgery_prefix`` is ``None`` and ``budget_consumed`` is
    ``False`` — the caller falls through with the ORIGINAL messages.
    """

    success: bool
    #: LLM-bound post-repair list for THIS turn (without the system
    #: prompt): ``[*hoisted-injected, *repair-doc, *retained-tail]``.
    repaired_messages: list[BaseMessage]
    summary: str
    repair_message_id: str
    error: str | None = None
    #: Return-carried SENTINEL-FIRST prefix for the node return:
    #: ``[RemoveMessage(REMOVE_ALL_MESSAGES), *hoisted, *doc, *tail]``.
    #: ``None`` on abort (no surgery happened).
    surgery_prefix: list[BaseMessage] | None = None
    #: True only when a repair fully succeeded (surgery built + guard
    #: passed). The caller increments the durable budget exactly then.
    budget_consumed: bool = False
    #: Fail-open abort marker (summarizer failure / persist refusal).
    aborted: bool = False
    #: Machine-readable abort reason (``summarizer-failed`` /
    #: ``persist-refused`` / ``budget-exhausted``).
    abort_reason: str | None = None


class SymptomRepairEngine:
    """ONE repair engine, per-class surgical presets (ADR-0001).

    Phase-1 enrollment is the LOOP class only (ADR-0002). New classes
    (phase 2+) enroll as a new preset row + detector — never new
    machinery.
    """

    #: Per-class preset table — a dict ON the class (ADR-0001). Each row
    #: declares four preset axes:
    #:
    #: - ``evidence_window_selector`` — method name resolved on ``self``.
    #:   THE ONLY AXIS THE LOOP PATH CONSULTS AT RUNTIME.
    #: - ``summary_prompt`` — key into ``SUMMARY_PROMPTS``. Declared for
    #:   phase-2 enrollment; NOT consulted by the loop path. ``_summarize``
    #:   uses ``REPAIR_SUMMARIZATION_PROMPT`` (lazy-imported from
    #:   ``daemon.graph``) directly with no per-class branching.
    #: - ``retention`` — declared for phase-2 enrollment; NOT consulted.
    #:   The loop path's retention is the engine's hardcoded
    #:   hoisted-injected + evidence-unit + verbatim-non-evidence policy.
    #: - ``post_repair_routing`` — declared for phase-2 enrollment; NOT
    #:   consulted. The loop path always routes ``continue``.
    #:
    #: Per-class variation is confined to DATA, not control flow. Wiring
    #: the unconsulted axes into runtime paths is a phase-2 design call.
    PRESETS: ClassVar[dict[str, dict[str, Any]]] = {
        "loop": {
            "description": (
                "Repeated identical tool-call units detected by "
                "LoopDetector (threshold 3, consecutive-signature walk)"
            ),
            "evidence_window_selector": "_select_loop_evidence_window",
            "summary_prompt": "loop",
            "retention": (
                "hoisted-injected context + evidence unit (oldest "
                "occurrence) + all non-evidence history verbatim"
            ),
            "post_repair_routing": "continue",
        },
    }

    #: Per-class summarizer prompt fragments (the preset's
    #: ``summary_prompt`` key). NOTE: the loop preset does NOT resolve
    #: ``"loop"`` to anything here. ``_loop_prompt`` is a placeholder key,
    #: not a symbol — nothing in this module reads it. The loop path's
    #: summarizer uses the shipped ``REPAIR_SUMMARIZATION_PROMPT`` template
    #: (lazy-imported from ``daemon.graph``) directly, so there is no
    #: per-class prompt drift for the converted class.
    SUMMARY_PROMPTS: ClassVar[dict[str, str]] = {
        "loop": "_loop_prompt",
    }

    def __init__(self, timeout_seconds: int = DEFAULT_SUMMARIZATION_TIMEOUT_S) -> None:
        self._timeout_seconds = timeout_seconds or DEFAULT_SUMMARIZATION_TIMEOUT_S

    # ── preset resolution ────────────────────────────────────────────

    def preset_for(self, symptom_class: str) -> dict[str, Any]:
        """Return the preset row for ``symptom_class`` (KeyError → loud)."""
        try:
            return self.PRESETS[symptom_class]
        except KeyError:
            raise ValueError(
                f"Unknown symptom class {symptom_class!r} — enrolled "
                f"classes: {sorted(self.PRESETS)}"
            ) from None

    # ── public surface (loop class: same shape as LoopRepairer.repair) ──

    async def repair(
        self,
        context: SymptomRepairContext,
        *,
        symptom_class: str = "loop",
    ) -> SymptomRepairOutcome:
        """Execute the durable repair flow for a detected symptom.

        Kill-switch contract: the CALLER is the gate — never invoke
        ``repair()`` directly without consulting
        ``get_symptom_repair_ladder_enabled()`` /
        ``get_repair_loop_durable_enabled()`` (phase-2 enrollment safety:
        every new class must inherit the same gating discipline).

        Steps (each failure mode fails OPEN — the caller continues the
        turn on the ORIGINAL messages):

            1. **Budget gate (B-4)** — at durable cap: refuse WITHOUT
               surgery (``abort_reason="budget-exhausted"``); the caller
               escalates to the class's loud terminal backstop.
            2. **Evidence window (A-2)** — the loop preset's removal
               builder reuses the shipped ``LoopRepairer._build_removal_list``
               output (loop units minus the evidence unit) — detection
               semantics are untouched.
            3. **Summarize (C-1/C-2)** — facade-wrapped client; empty or
               degenerate output after bounded retry + failover raises
               :class:`SymptomRepairAborted` (``summarizer-failed``).
               NO static fallback — never inject a degenerate summary.
            4. **Repair doc (A-4)** — ``repair-{instance_id}-{seq}``
               construction-time id, ``context_kind``-stamped so the
               three-bucket compaction partition treats it as
               permanently non-selectable/hoisted, content pinned to
               VERBATIM tool name/args excerpts.
            5. **Surgery (A-3/A-5/A-6)** — sentinel-first replacement
               with hoisted-injected head, doc, ORIGINAL-id retained
               tail, and a tool-unit folding sweep (no orphaned
               ToolMessage).
            6. **Pre-write guard (C-3)** — every snapshot id except the
               declared removals must survive in the replacement;
               refusal = :class:`SymptomRepairAborted`
               (``persist-refused``).

        On success ``budget_consumed=True`` — the CALLER increments the
        durable budget on the node return (single increment site,
        atomic with the surgery's commit).
        """
        preset = self.preset_for(symptom_class)

        # Step 1 — durable budget gate (B-4). Refusal is NOT an abort in
        # the fail-open sense: nothing was attempted, and the caller
        # escalates (Workstream D). Distinguished via abort_reason.
        if context.budget_used >= context.budget_cap:
            logger.warning(
                "[SymptomRepair] class=%s instance=%s: durable budget "
                "exhausted (%d/%d) — refusing repair",
                symptom_class,
                (context.instance_id or "")[:8],
                context.budget_used,
                context.budget_cap,
            )
            return SymptomRepairOutcome(
                success=False,
                repaired_messages=list(context.messages),
                summary="",
                repair_message_id="",
                error="repair budget exhausted (durable)",
                aborted=True,
                abort_reason="budget-exhausted",
            )

        try:
            # Step 2 — evidence window via the preset selector. The loop
            # selector re-routes the shipped removal-builder output (A-2).
            selector = getattr(self, preset["evidence_window_selector"])
            removal_ids, loop_units = selector(context.detection, context.messages)

            # Step 3 — facade-wrapped summarizer (C-1/C-2).
            summary = await self._summarize(context, symptom_class)
            # Step 4 — repair doc (A-4).
            doc = self._build_repair_doc(context, summary, symptom_class)

            # Step 5 — sentinel-first surgery (A-3/A-5/A-6).
            prefix, llm_bound = self._build_surgery(
                context, removal_ids, doc
            )

            # Step 6 — pre-write guard (C-3). Runs on the ASSEMBLED
            # replacement (doc included) — mirrors
            # build_sentinel_replacement's CompactionAborted discipline.
            self._prewrite_guard(context.messages, prefix, removal_ids)
        except SymptomRepairAborted as abort_exc:
            logger.warning(
                "[SymptomRepair] class=%s instance=%s: repair ABORTED "
                "fail-open: %s",
                symptom_class,
                (context.instance_id or "")[:8],
                abort_exc,
            )
            return SymptomRepairOutcome(
                success=False,
                repaired_messages=list(context.messages),
                summary="",
                repair_message_id="",
                error=str(abort_exc),
                aborted=True,
                abort_reason=getattr(abort_exc, "reason", None) or "summarizer-failed",
            )
        except Exception as exc:  # noqa: BLE001 — repair must never wedge the turn
            logger.warning(
                "[SymptomRepair] class=%s instance=%s: repair failed: "
                "%s: %s",
                symptom_class,
                (context.instance_id or "")[:8],
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return SymptomRepairOutcome(
                success=False,
                repaired_messages=list(context.messages),
                summary="",
                repair_message_id="",
                error=f"{type(exc).__name__}: {exc}",
                aborted=True,
                abort_reason="summarizer-failed",
            )

        logger.info(
            "[SymptomRepair] class=%s instance=%s: surgery built — "
            "removing %d ids, retained tail %d messages, doc %s",
            symptom_class,
            (context.instance_id or "")[:8],
            len(removal_ids),
            len(llm_bound) - 1,  # minus the doc
            doc.id,
        )
        return SymptomRepairOutcome(
            success=True,
            repaired_messages=llm_bound,
            summary=summary,
            repair_message_id=doc.id or "",
            surgery_prefix=prefix,
            budget_consumed=True,
        )

    # ── preset: loop class ───────────────────────────────────────────

    @staticmethod
    def _select_loop_evidence_window(
        detection: Any,
        messages: list[BaseMessage],
    ) -> tuple[set[str], list[BaseMessage]]:
        """Loop preset evidence-window selector (A-2 re-route).

        Re-routes the shipped ``LoopRepairer._build_removal_list`` output
        (detection semantics untouched — same ids the transient repair
        removed) and ADDS the P-8 unit-folding sweep: any retained
        ``ToolMessage`` whose issuing AIMessage is in the removal set is
        folded out too, so the retained tail never orphans a tool result.

        Returns:
            ``(removal_ids, folded_units)`` — the ids to remove and the
            removed message objects (loop units, for telemetry).
        """
        from ..graph import LoopRepairer  # lazy: graph ↔ services cycle guard

        removals = LoopRepairer._build_removal_list(detection)
        removal_ids = {r.id for r in removals if r.id}

        # P-8 sweep: fold ToolMessages whose parent AIMessage is removed.
        # The detector already folds its units' ToolMessages; this catches
        # id-less / divergent fixtures defensively.
        removed_ai_tool_call_ids: set[str] = set()
        retained_ai_tool_call_ids: set[str] = set()
        for msg in messages:
            tool_calls = getattr(msg, "tool_calls", None) or []
            tc_ids = {
                tc.get("id", "") for tc in tool_calls if tc.get("id", "")
            }
            msg_id = getattr(msg, "id", None)
            if not isinstance(msg, AIMessage) or not tc_ids:
                continue
            if msg_id in removal_ids:
                removed_ai_tool_call_ids.update(tc_ids)
            else:
                retained_ai_tool_call_ids.update(tc_ids)
        orphan_candidates = removed_ai_tool_call_ids - retained_ai_tool_call_ids
        if orphan_candidates:
            for msg in messages:
                if not isinstance(msg, ToolMessage):
                    continue
                if (
                    getattr(msg, "tool_call_id", "") in orphan_candidates
                    and getattr(msg, "id", None)
                    and getattr(msg, "id") not in removal_ids
                ):
                    removal_ids.add(msg.id)

        folded_units = [
            m
            for m in (detection.loop_messages or [])
            if getattr(m, "id", None) in removal_ids
        ]
        return removal_ids, folded_units

    # ── summarizer (C-1/C-2/C-4) ─────────────────────────────────────

    async def _summarize(
        self,
        context: SymptomRepairContext,
        symptom_class: str,
    ) -> str:
        """Facade-wrapped summarizer call (async).

        Builds the client via ``wrap_langchain_failover`` (compaction's
        pattern — ADR-0006), wraps the synchronous ``invoke`` in
        ``asyncio.to_thread`` + ``asyncio.wait_for`` with the SITE-level
        timeout preserved from config (120s — C-4; the facade's
        ``wall_clock_cap_s`` is INHERITED from
        ``wrap_langchain_failover``'s default, so the facade cap is the
        first to trip and the 120s site cap stays as backstop).

        Empty/degenerate output (the S1 raise class surfaces THROUGH the
        facade as an exception after bounded retry + failover; a
        think-tag-only summary is caught by the local degeneracy check)
        raises :class:`SymptomRepairAborted` — the repair ABORTS
        fail-open. The shipped static truncation fallback is deliberately
        NOT reproduced: a degenerate LLM summary must never silently
        enter history (ADR-0006 decision — removal chosen over
        last-resort-with-telemetry because the abort path already falls
        through to the shipped backstops, making a fallback summary
        strictly worse than no surgery).
        """
        from ..compaction import _extract_text_from_content  # lazy
        from ..graph import (  # lazy: graph ↔ services cycle guard
            REPAIR_SUMMARIZATION_PROMPT,
            ThinkingChatOpenAI,
            clean_llm_config,
        )
        from ..utils import parse_think_tags  # lazy
        from .llm_failover import wrap_langchain_failover  # lazy
        from ..graph import LoopRepairer  # lazy: graph ↔ services cycle guard

        detection = context.detection
        excerpt = LoopRepairer._build_excerpt(context.messages, max_messages=10)
        prompt = REPAIR_SUMMARIZATION_PROMPT.format(
            tool_name=getattr(detection, "tool_name", "unknown"),
            tool_args=json.dumps(
                getattr(detection, "tool_args", {}), indent=2
            )[:MAX_VERBATIM_ARGS_CHARS],
            count=getattr(detection, "repetition_count", 0),
            conversation_excerpt=excerpt,
        )

        timeout = (
            context.summarization_timeout_seconds
            or self._timeout_seconds
            or DEFAULT_SUMMARIZATION_TIMEOUT_S
        )
        try:
            # clean_llm_config strips model_vision (same module as the
            # compaction summarizer call sites). The facade reads
            # base_url_backup from the RAW dict — pass context.llm_config
            # uncleaned, mirroring compaction.py:3383.
            config = clean_llm_config(dict(context.llm_config or {}))
            llm = ThinkingChatOpenAI(**config)
            llm_wrapper = wrap_langchain_failover(
                llm, dict(context.llm_config or {})
            )  # C-4: wall_clock_cap_s inherited (facade default)
            response = await self._invoke_summarizer(
                llm_wrapper, prompt, timeout
            )
        except SymptomRepairAborted:
            raise
        except Exception as exc:  # noqa: BLE001 — facade exhaustion lands here
            abort = SymptomRepairAborted(
                f"summarizer-failed: {type(exc).__name__}: {exc}"
            )
            abort.reason = "summarizer-failed"
            raise abort from exc

        text = _extract_text_from_content(getattr(response, "content", "") or "")
        cleaned, _thinking = parse_think_tags(text or "")
        if not cleaned.strip():
            abort = SymptomRepairAborted(
                "summarizer-failed: degenerate (empty) summary after "
                "facade retry + failover"
            )
            abort.reason = "summarizer-failed"
            raise abort
        return cleaned.strip()

    @staticmethod
    async def _invoke_summarizer(
        llm_wrapper: Any,
        prompt: str,
        timeout_seconds: int,
    ) -> BaseMessage:
        """Run the synchronous facade ``invoke`` off-loop with a hard cap."""
        from langchain_core.messages import HumanMessage  # lazy

        def _call():
            return llm_wrapper.invoke(
                [
                    SystemMessage(
                        content=(
                            "You are a helpful assistant that analyzes "
                            "conversation patterns."
                        )
                    ),
                    HumanMessage(content=prompt),
                ]
            )

        return await asyncio.wait_for(
            asyncio.to_thread(_call), timeout=timeout_seconds
        )

    @staticmethod
    def _build_excerpt(
        messages: list[BaseMessage], max_messages: int = 10
    ) -> str:
        """Removed: engine now delegates to ``LoopRepairer._build_excerpt``
        (daemon/graph.py) — byte-identical output, lazy-imported via the
        existing graph ↔ services cycle guard. Kept here as a deprecation
        shim for any external callers; the one internal call site at
        ``_summarize`` now imports ``LoopRepairer`` directly."""
        from .graph import LoopRepairer  # lazy: graph ↔ services cycle guard
        return LoopRepairer._build_excerpt(messages, max_messages=max_messages)

    # ── repair doc (A-4 / T-11) ──────────────────────────────────────

    @staticmethod
    def _next_doc_seq(messages: list[BaseMessage], instance_id: str) -> int:
        """Next repair-doc seq for ``instance_id``.

        Parses prior ``repair-{iid}-*`` SystemMessage ids from the
        snapshot and returns ``max_parsed + 1`` (mirrors
        ``_next_compaction_seq``; instance-scoped so coexisting instances
        never collide on the seq axis).
        """
        needle_prefix = f"{REPAIR_DOC_ID_PREFIX}{instance_id}-"
        max_seq = 0
        for msg in messages:
            if not isinstance(msg, SystemMessage):
                continue
            mid = getattr(msg, "id", None) or ""
            if not mid.startswith(needle_prefix):
                continue
            try:
                seq = int(mid[len(needle_prefix):])
            except (ValueError, TypeError):
                continue
            if seq > max_seq:
                max_seq = seq
        return max_seq + 1

    def _build_repair_doc(
        self,
        context: SymptomRepairContext,
        summary: str,
        symptom_class: str,
    ) -> SystemMessage:
        """Build the durable repair doc (A-4).

        * Construction-time STABLE id ``repair-{instance_id}-{seq}`` —
          the message-id invariant (the read-side ``serialize_message``
          fallback cannot heal checkpointed messages).
        * ``additional_kwargs`` stamped ``injected_message=True`` +
          ``context_kind="symptom_repair"`` so the compaction
          three-bucket partition treats the doc as permanently
          non-selectable/hoisted context (T-11) — every later compaction
          re-emits it verbatim above the compacted span.
        * Content pinned to VERBATIM excerpts (tool name / args JSON /
          repetition count) with the LLM summary clearly LABELED as
          generated — the doc must not become a new hallucination vector
          (risk R4, DQ2-e).
        """
        from .context_messages import CONTEXT_KIND_SYMPTOM_REPAIR  # lazy

        detection = context.detection
        seq = self._next_doc_seq(context.messages, context.instance_id)
        doc_id = f"{REPAIR_DOC_ID_PREFIX}{context.instance_id}-{seq}"
        content = (
            f"[SYMPTOM REPAIR — {symptom_class}]\n\n"
            f"The conversation history was repaired: repeated identical "
            f"tool-call units were removed from context. The oldest "
            f"occurrence (evidence unit) is retained below.\n\n"
            f"Removed evidence (verbatim excerpts):\n"
            f"- tool: {getattr(detection, 'tool_name', 'unknown')}\n"
            f"- args (verbatim JSON): "
            f"{json.dumps(getattr(detection, 'tool_args', {}), sort_keys=True)[:MAX_VERBATIM_ARGS_CHARS]}\n"
            f"- consecutive repetitions removed: "
            f"{max(int(getattr(detection, 'repetition_count', 1)) - 1, 0)}\n\n"
            f"Attempted-work summary (LLM-generated — verify against the "
            f"retained evidence before relying on it):\n"
            f"{summary}\n\n"
            f"Continue the task with a DIFFERENT approach; do not repeat "
            f"the removed calls."
        )
        return SystemMessage(
            content=content,
            id=doc_id,
            additional_kwargs={
                "injected_message": True,
                "context_kind": CONTEXT_KIND_SYMPTOM_REPAIR,
            },
        )

    # ── surgery (A-3 / A-5 / A-6 / C-3) ──────────────────────────────

    def _build_surgery(
        self,
        context: SymptomRepairContext,
        removal_ids: set[str],
        doc: SystemMessage,
    ) -> tuple[list[BaseMessage], list[BaseMessage]]:
        """Build the return-carried sentinel-first surgery.

        Desired final order (mirrors ``build_sentinel_replacement``):

            ``[sentinel, *hoisted-injected, *repair-doc, *retained-tail]``

        * sentinel: ``RemoveMessage(REMOVE_ALL_MESSAGES)`` at ELEMENT 0
          (P-6 — anything before it is discarded by the reducer).
        * hoisted-injected: ``context_kind`` blocks + UNANSWERED
          bare-flag notes re-emitted at the head (P-5 — mirrors the
          compaction seam's hoist decision via the SAME predicates).
        * retained tail: every non-hoisted, non-removed message in
          ORIGINAL order with ORIGINAL ids — full message objects, so
          the reducer upserts them IN PLACE (P-7 / A-5).
        * doc: appended after the hoisted head, before the tail (the
          directive precedes the conversation the LLM reads).

        Returns:
            ``(surgery_prefix, llm_bound)`` — the prefix is the
            node-return carrier (sentinel first); ``llm_bound`` is the
            same channel WITHOUT the sentinel (the caller prepends the
            system prompt for the LLM call).
        """
        from ..compaction import (  # lazy
            _injected_note_absorbed_ids,
            _is_hoisted_injected,
            make_remove_all_sentinel,
        )

        answered_note_ids = _injected_note_absorbed_ids(context.messages)
        hoisted: list[BaseMessage] = []
        retained: list[BaseMessage] = []
        for msg in context.messages:
            if _is_hoisted_injected(msg, answered_note_ids):
                hoisted.append(msg)
            elif getattr(msg, "id", None) in removal_ids:
                continue  # loop evidence — removed by the sentinel
            else:
                retained.append(msg)

        sentinel = make_remove_all_sentinel()
        prefix: list[BaseMessage] = [sentinel, *hoisted, doc, *retained]
        llm_bound: list[BaseMessage] = [*hoisted, doc, *retained]
        return prefix, llm_bound

    @staticmethod
    def _prewrite_guard(
        snapshot: list[BaseMessage],
        replacement: list[BaseMessage],
        removal_ids: set[str],
    ) -> None:
        """Pre-write guard (C-3 / T-12) — persist-refusal = abort.

        The ONE safety check (mirrors ``build_sentinel_replacement``):
        every id-bearing snapshot message that is NOT in the declared
        removal set MUST appear in the replacement. A violation would be
        a silent checkpoint loss — refuse by raising
        :class:`SymptomRepairAborted` (``persist-refused``); the caller
        treats it exactly like a summarizer abort (fail-open, budget not
        consumed).

        ``None``-id snapshot messages are NOT guard-tracked (they cannot
        be a sentinel-loss regression target — same stance as the
        compaction seam); the re-emitted replacement re-adds them as
        fresh objects under the REMOVE_ALL recipe.
        """
        replacement_ids = {
            getattr(m, "id", None) for m in replacement
        } - {None}
        snapshot_ids = {
            getattr(m, "id", None) for m in snapshot
        } - {None}
        lost = snapshot_ids - replacement_ids - set(removal_ids)
        if lost:
            abort = SymptomRepairAborted(
                f"persist-refused: pre-write guard would silently lose "
                f"snapshot ids {sorted(str(i) for i in lost)} — the "
                f"replacement does not carry them and they are not in "
                f"the declared removal set"
            )
            abort.reason = "persist-refused"
            raise abort


__all__ = [
    "SYMPTOM_REPAIR_BUDGET",
    "REPAIR_DOC_ID_PREFIX",
    "SymptomRepairAborted",
    "SymptomRepairContext",
    "SymptomRepairEngine",
    "SymptomRepairOutcome",
]
