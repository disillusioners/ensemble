"""Full-history tool-call pairing validator and healer.

Module-level helper module for the 2013-bricking bug class
(incident 03d7657f). The pre-existing helpers
:func:`daemon.graph._ensure_tool_result_pairing` (in-graph tail guard)
and :func:`daemon.services.instance_messaging._heal_poisoned_checkpoint_tail`
(enqueue-seam guard) BOTH short-circuit on the O(1) tail-only happy path
when the last message is not ``AIMessage(tool_calls=[...])``. The
forensic scenario in incident 03d7657f minted an
interrupted-mid-tool defect the tail-only helpers could not see:

  * Unanswered tool call — an ``AIMessage(tool_calls)`` whose
    ``ToolMessage`` never arrived (stream died after committing the
    AIMessage, before the tool result was written). The forensic
    id was ``call_8ed9e1771dca`` (1c/0r — one call site, zero
    results, mid-history).

LESSON LOAD-BEARING for W1 — IMMEDIACY, not count-pairing:
    Strict OpenAI-compatible gateways reject requests with the
    shape ``AIMessage(tool_calls) → [non-ToolMessage]`` before the
    matching ``ToolMessage`` arrives — even when the matching
    ``ToolMessage`` exists SOMEWHERE ELSE in the history. The
    count-pairing scan ("every tc_id has SOME ToolMessage
    somewhere") is INSUFFICIENT.

    Concrete failure shape (live repair evidence):
    ``[AI-issuer][AI-other][TM-synth][TM-other]`` — count-pairing
    is valid (every tc_id has a partner), but the gateway STILL
    rejects with 2013 because ``AI-other`` is between
    ``AI-issuer`` and its required immediate ``ToolMessage``
    answer. The order-valid form is
    ``[AI-issuer][TM-synth][AI-other][TM-other]``.

    Therefore this helper's primary check is an
    **adjacency / immediacy** rule: every ``AIMessage(tool_calls)``
    must be answered by the IMMEDIATELY-adjacent contiguous
    ``ToolMessage`` block — no intervening non-ToolMessage between
    call and answer. Healing actions:

      * (a) Unanswered tool_calls → synthesize placeholder
        ``ToolMessage`` at the END of the adjacent block (just
        before the first non-ToolMessage), so the synthesized
        placeholder sits IMMEDIATELY after the issuing
        ``AIMessage``. Id format: ``partner-synth-{tc_id}``;
        content "Tool execution interrupted…".
      * (b) Orphaned ``ToolMessage`` (no matching call anywhere
        in the resulting history) → remove. This catches
        TMs that were stranded by a synthesis (e.g. a TM
        originally intended for a now-synthesized-partnered
        ``AIMessage``).
      * (c) Duplicate ``tool_call_id`` across two DIFFERENT
        ``AIMessage``s — NOT a gateway violation as long as
        each AIMessage has its own adjacent block. The
        pre-existing count-based duplicate detector was
        over-aggressive; this helper does NOT remove duplicates
        unless they create an order violation.

Performance gating (W1 design rationale):
    The full scan is wired ONLY at the LLM dispatch boundary
    (``graph.py`` ``current_llm.invoke(full_messages)`` and the
    enqueue-seam tail-guard). The hot-path cost is bounded by:

      * Always run the cheap O(n) ``has_pairing_violations`` probe
        (O(n) time, O(n) auxiliary set of issued tool_call_ids
        precomputed once per probe call). On healthy histories the
        cost is the probe alone.
      * ONLY when the probe returns ``True`` (a real violation
        is present) does the full ``validate_and_heal_messages``
        run. The full scan mutates in place and returns a report
        the caller threads into the C2 return so the heal is
        persisted on the next checkpoint superstep.

DESIGN CONSTRAINTS:
  * Stateless helper: takes a messages list, returns a new
    report (synthesized list + removed_message_ids + summary
    counts) + mutates the input list in place.
  * Mirrors :func:`daemon.graph._ensure_tool_result_pairing`'s
    R1 deterministic id format (``pairing-synth-{tc_id}`` is the
    in-graph format; this helper uses ``partner-synth-{tc_id}``
    to mark a distinct production lane but ``_is_partner_synth``
    recognizes both — re-heal idempotency across the helper
    chain stays intact).
  * No import-cycle exposure: the helpers do not import graph.py
    or services — they take a plain message list. Callers that
    need graph-state lookups do them BEFORE calling these
    helpers.

For a deeper code map of the bricked incident and the verification
chain that proved the helpers do not catch this case, see the
``auto-loaded skills``-pinned knowledge notes on the
``gotcha-agents-ensemble-the-tool-pairing-guard-lattice-cannot`` topic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

logger = logging.getLogger(__name__)


# Mirrors ``daemon.graph._TOOL_PAIRING_PLACEHOLDER_TEXT`` so the
# full-history helper produces placeholders the in-graph guard would
# produce (idempotent re-heal across the helper chain). The text is
# honest about the cause — never fabricated output, never empty.
PARTNER_SYNTH_TEXT = (
    "[Tool execution interrupted (daemon restart/crash) — result "
    "unavailable. Re-issue the tool call if still needed.]"
)


@dataclass
class ToolPairingHealReport:
    """Operational record of one ``validate_and_heal_messages`` invocation.

    Carries the synthesized placeholders and the ids of removed
    orphans so callers can persist via the C2 return
    (synthesized ``ToolMessage``s + ``RemoveMessage`` sentinels for
    removals). The in-place mutation of ``messages`` is the
    LLM-bound-state fix; the report fields let callers persist the
    same heals to the LangGraph checkpoint.

    Attributes:
        synthesized: Placeholder ``ToolMessage``s inserted into the
            messages list (in left-to-right order). Empty when the
            history was already valid.
        removed_orphan_indices: Indices in the REBUILT list of
            ``ToolMessage``s removed because they had no matching
            ``AIMessage(tool_calls=...)`` call-site anywhere in the
            history. Empty on the happy path.
        removed_duplicate_indices: Indices in the REBUILT list of
            (call-site, ToolMessage) pairs removed because a prior
            ``AIMessage`` with the SAME ``tool_call_id`` already
            existed in the history. The dedup path is defensive
            (cheap, no-judgment); strict gateways tolerate the
            count-duplicate as long as each AIMessage has its own
            adjacent block, so the dedup runs AFTER the adjacency
            fix and only fires when the duplicate creates an
            actual adjacent-block violation (i.e. the later
            AIMessage's adjacent block contains the duplicate TM,
            which would re-trigger 2013 after the block fix).
        removed_message_ids: ``id`` attributes of every message that
            was removed by the heal, in removal order. Callers thread
            these into ``RemoveMessage(id=...)`` sentinels for the C2
            return so the LangGraph ``add_messages`` reducer drops them
            from the checkpoint in the same superstep. Empty when no
            removals were needed.
        scanned_count: Total messages inspected. Useful for logging
            and for hot-path cost estimation.
    """

    synthesized: list[ToolMessage] = field(default_factory=list)
    removed_orphan_indices: list[int] = field(default_factory=list)
    removed_duplicate_indices: list[int] = field(default_factory=list)
    removed_message_ids: list[str] = field(default_factory=list)
    scanned_count: int = 0


def _extract_tool_call_ids(msg: BaseMessage) -> list[str]:
    """Return the ``tool_call_id``s carried by ``msg`` as a list of str.

    For ``AIMessage`` with non-empty ``tool_calls``: one id per entry
    in ``msg.tool_calls`` (the dict shape ``{"id": str, ...}``).
    For ``ToolMessage``: ``[msg.tool_call_id]`` when present.
    For every other message type: empty list.

    Defensive against malformed entries (non-dict tool_call rows):
    a malformed entry contributes no id, the caller continues.
    """
    if isinstance(msg, AIMessage):
        tcs = getattr(msg, "tool_calls", None) or []
        ids: list[str] = []
        for tc in tcs:
            if isinstance(tc, dict):
                tc_id = tc.get("id")
                if tc_id:
                    ids.append(tc_id)
            else:
                tc_id = getattr(tc, "id", None)
                if tc_id:
                    ids.append(tc_id)
        return ids
    if isinstance(msg, ToolMessage):
        tc_id = getattr(msg, "tool_call_id", None)
        return [tc_id] if tc_id else []
    return []


def _make_synth_tool_message(
    tc_id: str,
    tc_name: str = "",
) -> ToolMessage:
    """Build a placeholder ``ToolMessage`` for an unanswered tc_id.

    Deterministic id format (``partner-synth-{tc_id}``) so a re-heal
    on a history that already has the placeholder uses the SAME id,
    and ``add_messages`` reducer dedups. ``_is_partner_synth``
    recognizes both this module's ``partner-synth-`` and the
    in-graph guard's ``pairing-synth-`` formats — re-heal across
    the helper chain stays idempotent.
    """
    return ToolMessage(
        content=PARTNER_SYNTH_TEXT,
        tool_call_id=tc_id,
        name=tc_name,
        id=f"partner-synth-{tc_id}",
    )


def _is_partner_synth(msg: BaseMessage) -> bool:
    """True iff ``msg`` is a placeholder ``ToolMessage`` minted by
    :func:`daemon.graph._ensure_tool_result_pairing` or
    :func:`_make_synth_tool_message`.

    Used by the validator to recognize placeholders and exclude them
    from orphan / duplicate detection — they are intentionally
    synthetic and may legitimately carry a ``tool_call_id`` whose
    parent AIMessage was removed by a prior repair surgery.
    """
    if not isinstance(msg, ToolMessage):
        return False
    msg_id = getattr(msg, "id", None) or ""
    return msg_id.startswith("partner-synth-") or msg_id.startswith("pairing-synth-")


def _build_next_non_tool_after(messages: list[BaseMessage]) -> list[int]:
    """Pre-compute for each index ``i`` the position of the first
    non-``ToolMessage`` at index > ``i`` (or ``len(messages)`` if
    no such position exists).

    Used by :func:`validate_and_heal_messages` to determine, for
    each ``AIMessage(tool_calls)`` at index ``i``, where the
    contiguous ``ToolMessage`` block immediately following it ends.
    Strict gateways require the matching ``ToolMessage``s to be in
    this block — anywhere later is the 2013 poison shape.
    """
    n = len(messages)
    result = [n] * n
    last_non_tool = n
    for i in range(n - 1, -1, -1):
        result[i] = last_non_tool
        if not isinstance(messages[i], ToolMessage):
            last_non_tool = i
    return result


def validate_and_heal_messages(
    messages: list[BaseMessage],
    *,
    instance_short: str = "",
) -> ToolPairingHealReport:
    """Validate ``messages`` end-to-end and heal any pairing violation.

    PRIMARY CHECK — adjacency / immediacy (the load-bearing rule):

        For every ``AIMessage(tool_calls=[...])`` at index ``i``, the
        IMMEDIATELY following contiguous ``ToolMessage`` block
        (extending from index ``i+1`` up to the first non-ToolMessage
        or end of list) must collectively carry ``ToolMessage``s whose
        ``tool_call_id``s cover every entry in the AIMessage's
        ``tool_calls``. If any ``tool_call_id`` is missing from the
        adjacent block, the gateway rejects with 2013.

    HEAL OPS:

      (a) Unanswered tool_calls → synthesize a placeholder
         ``ToolMessage`` for each missing ``tool_call_id`` at the
         END of the adjacent block (just before the first
         non-ToolMessage, or at the list tail if there is no
         non-ToolMessage after the AIMessage). This places the
         placeholder IMMEDIATELY after any existing adjacent
         ``ToolMessage``s, preserving the AI(tc) → TM block
         adjacency the gateway requires.

      (b) Orphaned ``ToolMessage`` (no matching call anywhere in
         the resulting history) → remove. This catches TMs that
         were stranded by a synthesis (a TM that was originally
         paired with an AIMessage, but the synthesized partner
         earlier in the history now satisfies the AIMessage and
         the original TM has no remaining AIMessage to pair with).

    NOTE on duplicate ``tool_call_id`` (W1(c) clarification):
    Strict gateways accept count-duplicates as long as each
    ``AIMessage`` has its own IMMEDIATELY-adjacent ``ToolMessage``
    block. The pre-existing count-based duplicate detector was
    over-aggressive. THIS VALIDATOR DOES NOT REMOVE DUPLICATES.
    The producer-side guard
    :func:`daemon.graph._ensure_full_history_pairing`'s caller
    (graph.py, W4) handles dup-id prevention at the COMMIT
    boundary via :func:`dedupe_incoming_tool_call_ids`, which is
    the canonical cheap defensive path per the W1(c) clarification.

    The function mutates ``messages`` IN PLACE — synthesized
    placeholders are inserted at the correct positions and orphans
    are removed. The returned report carries the operational
    summary.

    Caller responsibilities:
      * Persist the healed messages (e.g. via the in-graph node
        return + ``add_messages`` reducer, or a follow-up
        ``graph.aupdate_state``). This helper has no I/O.
      * Surface the report in WARNING logs when ``synthesized`` or
        ``removed_orphan_indices`` is non-empty — the caller has
        the ``instance_short`` label and access to the broader log
        namespace.

    Args:
        messages: LLM-bound ``full_messages`` list (or any other
            list carrying the conversation history). Caller passes
            ownership; this function mutates it in place.
        instance_short: Short instance id for log lines. Empty
            string accepted (used by unit tests).

    Returns:
        A :class:`ToolPairingHealReport` summarising the operations
        performed. On the happy path, ``synthesized`` and
        ``removed_orphan_indices`` are both empty.
    """
    if not messages:
        return ToolPairingHealReport(scanned_count=0)

    n = len(messages)
    report = ToolPairingHealReport(scanned_count=n)

    # Pre-compute next_non_tool_after for the adjacency scan.
    next_non_tool_after = _build_next_non_tool_after(messages)

    # Phase 1: walk left-to-right; for each AIMessage(tc), build the
    # adjacent block. Existing TMs in the adjacent block are kept in
    # their original positions; missing tc_ids are synthesized at the
    # END of the adjacent block (just before the first non-ToolMessage,
    # or at the list tail). This places the synthesized placeholder
    # IMMEDIATELY after any existing adjacent TMs, preserving the
    # AI(tc) → TM block adjacency the gateway requires.
    new_list: list[BaseMessage] = []
    i = 0
    while i < n:
        msg = messages[i]
        new_list.append(msg)

        if isinstance(msg, AIMessage):
            ai_tc_ids = _extract_tool_call_ids(msg)
            if ai_tc_ids:
                block_end = next_non_tool_after[i]
                # Copy any existing TMs in the adjacent block into the
                # new list in their original positions. Track their
                # tc_ids so we know which are missing.
                block_tc_ids: set[str] = set()
                for k in range(i + 1, block_end):
                    tm = messages[k]
                    if isinstance(tm, ToolMessage):
                        tc_id = getattr(tm, "tool_call_id", None)
                        if tc_id:
                            block_tc_ids.add(tc_id)
                        new_list.append(tm)

                # Synthesize placeholders for missing tc_ids at the
                # END of the adjacent block.
                tc_name_by_id: dict[str, str] = {}
                for tc in (msg.tool_calls or []):
                    if isinstance(tc, dict):
                        tc_name_by_id[tc.get("id", "")] = (
                            tc.get("name", "") or ""
                        )
                    else:
                        tc_name_by_id[getattr(tc, "id", "") or ""] = (
                            getattr(tc, "name", "") or ""
                        )
                missing = [
                    tc_id for tc_id in ai_tc_ids
                    if tc_id not in block_tc_ids
                ]
                if missing:
                    new_placeholders: list[ToolMessage] = []
                    for tc_id in missing:
                        ph = _make_synth_tool_message(
                            tc_id, tc_name_by_id.get(tc_id, "")
                        )
                        new_placeholders.append(ph)
                        new_list.append(ph)
                    report.synthesized.extend(new_placeholders)

                # Skip past the original adjacent block.
                i = block_end
                continue
        i += 1

    # Phase 2: orphan removal — TMs whose nearest preceding NON-TOOL
    # message did NOT issue their tc_id (the "block-ownership rule").
    # A TM belongs to the adjacent ToolMessage block of the AIMessage
    # that issued its tc_id; if some other AIMessage (or a Human/System
    # message) is the nearest preceding non-Tool, the TM is in the
    # wrong block and must be removed — strict gateways enforce
    # immediacy (TM immediately after its issuer's adjacent block),
    # and a misplaced TM is a 2013 brick-class violation.
    #
    # Earlier formulations tried:
    #   (a) global issued-set (rejected by the review: a later
    #       AIMessage in the list issuing the same tc_id kept the TM
    #       as "issued" even though the TM was in the wrong block)
    #   (b) prefix issued-set (round-1 FIX 3: TM-before-issuer shape
    #       is caught but the misplacement-after-other-AI shape
    #       ``[AI(X)][AI(Y)][TM(X)][TM(Y)]`` still survives — TM(X)
    #       has an earlier issuer so the prefix rule keeps it, but
    #       it's in AI(Y)'s block, so the gateway 2013-rejects)
    #   (c) nearest preceding AIMessage (the review's literal
    #       phrasing): still masks ``[AI(X)][Human][TM(X)]`` — the
    #       nearest preceding AI IS the issuer, so the stranded TM
    #       after the Human would survive and the probe would stay
    #       clean, recreating the same masking class. The non-Tool
    #       formulation subsumes both shapes and matches the
    #       anthropic corpus signature ("messages with role 'tool'
    #       must be a response to a preceeding message with
    #       'tool_calls'").
    #
    # Guard: if the list has NO ``AIMessage``s at all (every
    # message is a ``ToolMessage``), there are no pairing semantics
    # to enforce — every TM is "orphan" by definition. Skip
    # orphan removal in that case to preserve the at-least-one-
    # message contract callers (notably ``emergency_truncate``) rely
    # on when collapsing a long tool-result-only history.
    has_any_aimessage = any(
        isinstance(m, AIMessage) for m in new_list
    )

    final_list: list[BaseMessage] = []
    if has_any_aimessage:
        # ``last_non_tool_tc_ids`` carries the tc_ids of the nearest
        # preceding non-Tool message WHEN that message is an
        # ``AIMessage`` with ``tool_calls``. When the nearest
        # preceding non-Tool is a HumanMessage / SystemMessage /
        # AIMessage without tool_calls, the set is empty (a TM
        # following any of those is stranded → REMOVED).
        last_non_tool_tc_ids: set[str] = set()
        for m in new_list:
            if isinstance(m, ToolMessage):
                if _is_partner_synth(m):
                    # Partner-synth placeholders are intentionally
                    # placed by Phase 1 immediately after the
                    # AIMessage that issued their tc_id — the
                    # block-ownership rule naturally keeps them.
                    final_list.append(m)
                    continue
                tc_id = getattr(m, "tool_call_id", None)
                if tc_id and tc_id not in last_non_tool_tc_ids:
                    # Block-ownership miss: the nearest preceding
                    # non-Tool message did NOT issue this tc_id.
                    # The TM is stranded in the wrong adjacent
                    # block — remove it. Sentinel threading picks
                    # up the id below (id-less case documented at
                    # :454-460).
                    report.removed_orphan_indices.append(len(final_list))
                    continue
                final_list.append(m)
            elif isinstance(m, AIMessage):
                # New nearest preceding non-Tool message — refresh
                # the tracking set with this AIMessage's tc_ids.
                last_non_tool_tc_ids = {
                    tc_id for tc_id in _extract_tool_call_ids(m)
                    if tc_id
                }
                final_list.append(m)
            else:
                # HumanMessage / SystemMessage / RemoveMessage /
                # etc. — also a non-Tool message, so it bounds the
                # block-ownership region. An empty set means any
                # subsequent TM (until the next AIMessage) is
                # stranded.
                last_non_tool_tc_ids = set()
                final_list.append(m)
    else:
        # All-ToolMessages history — keep as-is, no pairing semantics.
        final_list = list(new_list)

    # Capture removed message ids BEFORE mutating the input list, so
    # the caller can thread them into ``RemoveMessage`` sentinels for
    # C2-return persistence. We compare the FINAL list against the
    # ORIGINAL list by identity and surface the ids of anything
    # dropped during Phase 2.
    final_ids_by_obj: set[int] = set()
    for m in final_list:
        final_ids_by_obj.add(id(m))

    # ROUND-2 DOCUMENTATION — id-less orphan TMs are removed
    # payload-only with NO ``RemoveMessage`` sentinel. LangGraph's
    # ``RemoveMessage`` API requires a message ``id`` to thread a
    # removal sentinel into the C2 return (the ``add_messages``
    # reducer matches sentinels by id; no id, no drop on the next
    # checkpoint). For id-less TMs we collect no sentinel, so the
    # removed message is gone from the LLM-bound payload
    # (``full_messages``) for THIS dispatch — the gateway sees the
    # healed history and accepts — but on the next dispatch, the
    # checkpoint still carries the id-less TM (the previous
    # sentinel never landed), so the W1 probe flags it again and
    # the W1 heal removes it again. PERPETUAL RE-HEAL.
    #
    # Why we don't mint synthetic ids: the in-process ``messages``
    # list is the W1/W2 caller's working copy, but the checkpoint
    # row was committed by an earlier node and carries a different
    # (or null) id. Mismatching ids would either be no-ops (the
    # checkpoint reducer can't match them) or, worse, accidentally
    # drop an UNRELATED committed message that happens to share the
    # synthetic id. The risk of cross-message id collision
    # outweighs the perpetual-re-heal cost, which is bounded (a
    # few µs per dispatch per orphan) and self-resolving on the
    # NEXT turn once the LLM produces an AIMessage whose commit
    # replaces the orphaned tool-result-only segment.
    #
    # Mitigations already in place: (a) LangChain's default TM
    # construction in this codebase always sets an id (the
    # in-process helpers at ``daemon/loader.py`` and the
    # ``add_messages`` reducer default), so id-less TMs are a
    # corner case (mostly partner-synth placeholders minted by
    # Phase 1, which carry the ``partner-synth-{tc_id}`` id); (b)
    # the perpetual re-heal is O(n) and bounded by the conversation
    # length, so the cost is sub-millisecond on realistic
    # histories. The right long-term fix is a LangGraph API
    # addition (``RemoveMessage(index=N)`` or remove-by-content)
    # which is out of scope for this branch — see the linked
    # issue in ``.agents/shared/knowledge/``.
    removed_ids: list[str] = []
    for m in messages:
        if id(m) in final_ids_by_obj:
            continue
        mid = getattr(m, "id", None)
        if mid:
            removed_ids.append(mid)
        # else: id-less orphan — payload-only removal (no
        # sentinel). See the rationale block above.

    # Mutate in place.
    messages.clear()
    messages.extend(final_list)

    report.removed_message_ids = removed_ids

    if report.synthesized or report.removed_orphan_indices:
        logger.warning(
            f"[ToolPairing:FULL] instance={instance_short or '?'} "
            f"scanned={report.scanned_count} "
            f"synthesized={len(report.synthesized)} "
            f"orphans_removed={len(report.removed_orphan_indices)} "
            f"— 2013 brick-class full-history heal (adjacency fix)"
        )

    return report


def has_pairing_violations(messages: Iterable[BaseMessage]) -> bool:
    """Cheap pre-flight check: does the history contain any pairing
    violation detectable from the current message set?

    Returns ``True`` iff ANY of:
      * any ``AIMessage(tool_calls=[...])`` carries a
        ``tool_call_id`` not satisfied by an IMMEDIATELY-adjacent
        ``ToolMessage`` (the load-bearing strict-gateway rule);
      * any non-partner-synth ``ToolMessage``'s nearest preceding
        NON-TOOL message did NOT issue its ``tool_call_id`` (the
        block-ownership rule). The nearest preceding non-Tool is
        the last message walking back that is not a
        ``ToolMessage`` — typically the most recent ``AIMessage``
        (an earlier ``HumanMessage``/``SystemMessage`` also
        qualifies as "non-Tool", in which case the tracking set
        is empty and ANY following ``ToolMessage`` is flagged as
        stranded). This subsumes the misplacement-after-other-AI
        shape ``[AI(X)][AI(Y)][TM(X)][TM(Y)]`` (TM(X)'s nearest
        preceding non-Tool is AI(Y), which did not issue X) AND
        the Human-interleave shape ``[AI(X)][Human][TM(X)]`` (the
        Human is the nearest preceding non-Tool and does not
        issue any tc_id).

    NOTE: count-duplicates are NOT reported here (per W1(c)
    clarification — strict gateways accept them as long as each
    AIMessage has its own adjacent block). The producer-side
    guard ``dedupe_incoming_tool_call_ids`` handles dup-id
    prevention at the COMMIT boundary.

    This is a non-mutating validator — it does not heal, only
    signals. Use it as a fast probe BEFORE running the full
    validator when the caller wants to skip the full scan on
    healthy histories.

    Args:
        messages: Read-only iterable over the conversation history.

    Returns:
        ``True`` if any violation is detected; ``False`` if the
        history is structurally valid for the gateway.
    """
    # We need indexing to compute adjacency, so materialize the
    # iterable into a list (caller typically already passes a list).
    msgs_list = list(messages) if not isinstance(messages, list) else messages
    n = len(msgs_list)
    if n == 0:
        return False

    next_non_tool_after = _build_next_non_tool_after(msgs_list)

    # Single left-to-right pass with two violation checks in the
    # same loop. The adjacency check (AIMessage(tc) → all tc_ids in
    # the IMMEDIATELY-adjacent ToolMessage block) is the load-bearing
    # strict-gateway rule; the block-ownership check (TM(tc) → its
    # nearest preceding NON-TOOL message issued the tc_id) is the
    # misplacement/stranded-TM detector. Both checks share the same
    # "nearest preceding non-Tool" tracking set that the healer's
    # Phase 2 maintains.
    #
    # ROUND-2 BLOCKER — the previous prefix issued-set caught only
    # TM-before-issuer (TM(X) with no earlier issuer). The misplace-
    # ment-after-other-AI shape ``[AI(X)][AI(Y)][TM(X)][TM(Y)]``
    # survived the prefix rule (TM(X) HAS an earlier issuer — AI(X)
    # at index 0) but is in AI(Y)'s adjacent block, so the gateway
    # 2013-rejects and the probe reported CLEAN (the W2 retry was
    # a no-op, the reraise bricked the instance). The nearest
    # preceding NON-TOOL formulation subsumes both shapes AND the
    # Human-interleave shape ``[AI(X)][Human][TM(X)]``: the Human
    # is a non-Tool, so the tracking set becomes empty, and TM(X)
    # is flagged as stranded.
    last_non_tool_tc_ids: set[str] = set()

    for i, msg in enumerate(msgs_list):
        if isinstance(msg, AIMessage):
            ai_tc_ids = _extract_tool_call_ids(msg)
            if ai_tc_ids:
                block_end = next_non_tool_after[i]
                block_tc_ids: set[str] = set()
                for k in range(i + 1, block_end):
                    tm = msgs_list[k]
                    if isinstance(tm, ToolMessage):
                        tc_id = getattr(tm, "tool_call_id", None)
                        if tc_id:
                            block_tc_ids.add(tc_id)
                # Adjacency check: every tc_id in the AIMessage must
                # appear in the IMMEDIATELY-adjacent ToolMessage block.
                for tc_id in ai_tc_ids:
                    if tc_id not in block_tc_ids:
                        return True
            # Update the nearest preceding non-Tool tracking set
            # with this AIMessage's tc_ids. Done AFTER the adjacency
            # check; the adjacency check is independent of the
            # tracking set (it reads the next block, not the prefix).
            last_non_tool_tc_ids = {
                tc_id for tc_id in ai_tc_ids if tc_id
            }
        elif isinstance(msg, ToolMessage):
            if _is_partner_synth(msg):
                continue
            tc_id = getattr(msg, "tool_call_id", None)
            if not tc_id:
                continue
            # Block-ownership check: TM's nearest preceding
            # non-Tool message must have issued its tc_id. If the
            # nearest preceding non-Tool is a HumanMessage /
            # SystemMessage / AIMessage-without-tool_calls, the
            # tracking set is empty and ANY TM is stranded.
            if tc_id not in last_non_tool_tc_ids:
                return True
        else:
            # HumanMessage / SystemMessage / RemoveMessage / etc. —
            # bounds the block-ownership region. An empty set
            # means any subsequent TM (until the next AIMessage)
            # is stranded.
            last_non_tool_tc_ids = set()
    return False


def collect_existing_tool_call_ids(messages: Iterable[BaseMessage]) -> set[str]:
    """Return the set of ``tool_call_id``s already represented in the
    committed history (real ``ToolMessage``s only — partner-synth
    placeholders are excluded because they are intentionally
    orphaned when their parent AIMessage was removed).

    Used by the producer-side guard (W4) to detect a duplicate
    ``tool_call_id`` at the moment a new AIMessage is about to be
    committed. The W4 caller compares the new AIMessage's tc_ids
    against this set; collisions trigger deterministic re-ids.
    """
    ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, ToolMessage) and not _is_partner_synth(msg):
            tc_id = getattr(msg, "tool_call_id", None)
            if tc_id:
                ids.add(tc_id)
    return ids


def dedupe_incoming_tool_call_ids(
    new_ai_message: AIMessage,
    committed_messages: Iterable[BaseMessage],
    *,
    instance_short: str = "",
) -> tuple[AIMessage, int]:
    """Producer-side guard for duplicate ``tool_call_id`` prevention.

    Defensive path per the W1(c) clarification. The 03d7657f forensic
    shape was actually an unanswered-mid-history defect
    (1c/0r — one AIMessage(tc), zero matching ToolMessages, mid-
    history); the "duplicate" was a 12-char-prefix grouping false
    positive. No proven re-mint path exists, but the count-based
    duplicate guard is kept here as a cheap defensive measure: if
    an LLM REGENERATION / RETRY / REPLAY ever re-mints an
    already-answered ``tool_call_id`` (rare but possible if the
    provider loses determinism), this guard re-ids the colliding
    entries at the commit boundary so the gateway never sees a
    count-duplicate.

    Strategy: at the agent_node commit boundary, scan the
    ``new_ai_message``'s ``tool_calls`` and re-id any entry whose
    ``tool_call_id`` already exists in ``committed_messages``. The
    re-id is deterministic (``{original}__dup{N}`` where ``N`` is
    the collision index, starting at 1) so repeated guards on the
    same message produce the SAME new id — idempotent across
    reruns.

    Re-id safety: the model's behavior is unchanged because the
    renamed id is unique within the conversation history, so the
    gateway accepts the history on the next dispatch. The model's
    tool-binding layer uses the renamed id for the next ToolNode
    invocation; the resulting ``ToolMessage`` carries the same
    renamed id.

    Args:
        new_ai_message: The AIMessage the LLM just produced. The
            helper mutates its ``tool_calls`` IN PLACE — the SAME
            object that rides the C2 return carries the renamed
            ids.
        committed_messages: The ``state['messages']`` snapshot read
            at agent_node entry (the local ``messages`` variable).
            Iterated to collect already-committed ``tool_call_id``s.
        instance_short: Short instance id for log lines. Empty
            string accepted.

    Returns:
        ``(new_ai_message, reid_count)`` — the same ``AIMessage``
        (mutated in place) and the number of ids renamed. The
        caller may ignore the returned ``AIMessage`` (it is the
        same object).
    """
    if not isinstance(new_ai_message, AIMessage):
        return new_ai_message, 0
    tool_calls = getattr(new_ai_message, "tool_calls", None)
    if not tool_calls:
        return new_ai_message, 0

    existing_ids = collect_existing_tool_call_ids(committed_messages)
    reid_count = 0
    collision_index = 0
    new_tool_calls: list[dict] = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            tc_id = tc.get("id")
        else:
            tc_id = getattr(tc, "id", None)
        if not tc_id:
            new_tool_calls.append(tc)
            continue
        if tc_id not in existing_ids:
            new_tool_calls.append(tc)
            continue
        collision_index += 1
        new_id = f"{tc_id}__dup{collision_index}"
        while new_id in existing_ids:
            collision_index += 1
            new_id = f"{tc_id}__dup{collision_index}"
        if isinstance(tc, dict):
            new_tc = {**tc, "id": new_id}
        else:
            try:
                new_tc = tc.model_copy(update={"id": new_id})  # type: ignore[attr-defined]
            except AttributeError:
                try:
                    from dataclasses import replace as _dc_replace

                    new_tc = _dc_replace(tc, id=new_id)
                except Exception:  # pragma: no cover - defensive
                    new_tc = tc
        new_tool_calls.append(new_tc)
        existing_ids.add(new_id)
        reid_count += 1

    if reid_count > 0:
        try:
            new_ai_message.tool_calls = new_tool_calls  # type: ignore[assignment]
        except Exception:  # pragma: no cover - defensive
            logger.warning(
                f"[ToolPairing:FULL] W4 producer guard could not "
                f"set tool_calls on AIMessage for {instance_short or '?'} "
                f"— relying on W1 full-history heal at next dispatch"
            )
            return new_ai_message, 0
        logger.warning(
            f"[ToolPairing:FULL] W4 producer guard re-id "
            f"{reid_count} duplicate tool_call_id(s) for "
            f"{instance_short or '?'} — defensive (W1(c) clarification)"
        )

    return new_ai_message, reid_count