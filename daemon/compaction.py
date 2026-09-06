"""Context compaction engine for managing token limits.

This module provides a complete context window management system that automatically
compacts conversation history when it approaches the model's context limit. The system
uses LLM-based summarization to preserve important context while reducing token usage.

Key components:
- MODEL_CONTEXT_LIMITS: Registry of model context window sizes
- get_model_context_limit(): Lookup function with fuzzy matching
- estimate_tokens(): Token counting via tiktoken (imported from loader)
- CompactionContext: Container for all inputs needed for context compaction
- CompactionResult: Result of a compaction operation
- MessageGroup: Represents an atomic message group that cannot be split
- ContextCompactor: Main compaction engine with summarization and truncation strategies

Compaction Strategies:
1. Summarization: LLM-based summarization of old message groups
2. Chunked Summarization: For large histories, summarizes in batches then merges
3. Truncation: Fallback when summarization fails
4. Emergency Truncation: When even preserved groups exceed threshold
"""


import asyncio
import copy
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

from .config import CompactionConfig
from .loader import estimate_messages_tokens

logger = logging.getLogger(__name__)


def _extract_text_from_content(content: str | list) -> str:
    """Extract text from message content, handling multimodal lists.

    Args:
        content: Message content, either a string or a multimodal list
                 (e.g., [{'type': 'text', 'text': '...'}, {'type': 'image_url', ...}]).

    Returns:
        Extracted text string. For multimodal content, joins all text blocks.
        Skips image_url blocks entirely.
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                # Skip image_url and other non-text blocks
        return "".join(text_parts)

    return str(content) if content is not None else ""


def _is_injected_message(msg: BaseMessage) -> bool:
    """Phase 1 / C3: detect a user-injected message by ``additional_kwargs``.

    Mirrors the ``language_check_reminder`` skip pattern at graph.py:493.
    An injected message was deliberately placed into the conversation by
    the user via the injection slot (Phase 1 / C2) and MUST survive any
    compaction pass — both proactive (this module) and reactive
    (graph.py:641-684). Summarizing it would erase user intent.

    Args:
        msg: Candidate ``BaseMessage`` (typically ``HumanMessage``).

    Returns:
        ``True`` when the message is flagged as injected, ``False`` otherwise.
    """
    additional_kwargs = getattr(msg, "additional_kwargs", None)
    if not additional_kwargs:
        return False
    return bool(additional_kwargs.get("injected_message"))


def _has_context_kind(msg: BaseMessage) -> bool:
    """True when the injected message is a REAL ``[SYSTEM CONTEXT]`` block.

    Context messages are stamped by ``_make_context_message``
    (``daemon/services/context_messages.py``) with BOTH
    ``injected_message=True`` AND a ``context_kind`` enum value. They are
    permanently non-selectable: preserved verbatim and hoisted above the
    compaction doc at every pass (unchanged behavior).

    Bare-flag injected messages (operator notes via the FIFO injection
    drain — ``daemon/services/instance_messaging.py``) carry
    ``injected_message=True`` with NO ``context_kind``; only those are
    eligible for the answered-note lifecycle.
    """
    additional_kwargs = getattr(msg, "additional_kwargs", None)
    if not additional_kwargs:
        return False
    return bool(additional_kwargs.get("context_kind"))


def _injected_note_absorbed_ids(messages: list[BaseMessage]) -> frozenset[str]:
    """Ids of BARE-flag injected notes that are ANSWERED (selectable).

    The conservative "protect until answered" contract (injected-notes
    hoisting fix): a bare injected note is ANSWERED when an ``AIMessage``
    exists at a LATER index in the channel order. Unanswered notes —
    the newest message, or notes followed only by ToolMessages — stay
    permanently preserved. ``context_kind`` messages never qualify
    (they are permanent regardless of position), and id-less bare
    notes are conservatively treated as UNANSWERED (never absorbed).

    Args:
        messages: The FULL pre-compaction channel (conversation order).

    Returns:
        Frozenset of message ids that may be absorbed into the
        compacted span. Empty when there are no answered bare notes.
    """
    absorbed: set[str] = set()
    for idx, msg in enumerate(messages):
        if not _is_injected_message(msg) or _has_context_kind(msg):
            continue
        msg_id = getattr(msg, "id", None)
        if not msg_id:
            continue  # id-less → conservative: preserved, never absorbed
        if any(isinstance(m, AIMessage) for m in messages[idx + 1:]):
            absorbed.add(msg_id)
    return absorbed


def _is_hoisted_injected(
    msg: BaseMessage, absorbed_note_ids: frozenset[str]
) -> bool:
    """The hoist/preserve predicate for injected messages.

    Hoisted (preserved verbatim above the compaction doc) when:

    * the message carries ``context_kind`` (real system context —
      permanent), OR
    * it is a bare-flag note that is NOT answered (no later AIMessage
      in the pre-compaction channel — or an unresolvable id, treated
      conservatively as unanswered).

    Answered bare notes are NOT hoisted: they join the selectable pool
    and are absorbed into the compacted span like regular history.
    """
    if not _is_injected_message(msg):
        return False
    if _has_context_kind(msg):
        return True
    msg_id = getattr(msg, "id", None)
    if not msg_id:
        return True  # id-less bare note → conservative preserve
    return msg_id not in absorbed_note_ids


def _partition_injected_for_compaction(
    messages: list[BaseMessage],
) -> tuple[list[BaseMessage], list[BaseMessage], list[BaseMessage]]:
    """Three-bucket partition of the pre-compaction channel.

    Replaces the former unconditional two-way injected split: bare-flag
    operator notes now join the selectable pool once ANSWERED (an
    ``AIMessage`` exists at a later index — see
    :func:`_injected_note_absorbed_ids`), instead of being hoisted
    forever.

    Returns:
        Tuple ``(selectable, preserved_injected, absorbed_notes)`` where:

        * ``selectable`` — regular history PLUS answered bare notes, in
          original channel order (order matters: boundary grouping and
          tail preservation are order-sensitive).
        * ``preserved_injected`` — ``context_kind`` messages plus
          UNANSWERED bare notes (hoisted verbatim above the doc).
        * ``absorbed_notes`` — the answered bare-note subset of
          ``selectable`` (same objects), for envelope accounting.
    """
    absorbed_note_ids = _injected_note_absorbed_ids(messages)
    selectable: list[BaseMessage] = []
    preserved_injected: list[BaseMessage] = []
    absorbed_notes: list[BaseMessage] = []
    for msg in messages:
        if _is_hoisted_injected(msg, absorbed_note_ids):
            preserved_injected.append(msg)
        else:
            selectable.append(msg)
            if _is_injected_message(msg):
                absorbed_notes.append(msg)
    return selectable, preserved_injected, absorbed_notes


# Architecture §5 / §6 — the per-compaction output is a single
# `compaction-global-{iid}-{seq}` SystemMessage; the truncation marker is
# now the boundary line INSIDE the doc, not a separate message. The
# module-scope helper is kept (under a new name) for back-compat with
# older tests, but routes through the new doc builder.
GLOBAL_DOC_BOUNDARY_LINE = (
    "── END OF COMPACTED CONTEXT — "
    "everything below is the verbatim recent transcript ──"
)
GLOBAL_DOC_ARCHIVED_LINE = (
    "── ARCHIVED: {n} oldest sections condensed for budget; "
    "global overview above is authoritative ──"
)
GLOBAL_DOC_PLACEHOLDER_GLOBAL = (
    "(overview unavailable — merge pass failed; "
    "the sections below are authoritative)"
)
GLOBAL_DOC_ID_PREFIX = "compaction-global-"

# Threshold for the per-call GLOBAL overview cap. 600 tok is the
# architect-recommended cap from §4 (GLOBAL OVERVIEW), expressed as a
# per-message token budget so that the doc builder can stop emitting
# sections when the cap is hit.
GLOBAL_OVERVIEW_TOKEN_CAP = 600
# Whole-doc ceiling rule (architect §6.4): GLOBAL + Σsections ≤ 15% of
# context window. Breach → condense OLDEST sections first, never GLOBAL.
# Hard cap → degrade to GLOBAL + ARCHIVED line (B-shape).
COMPACTION_DOC_CEILING_FRACTION = 0.15
# Bounded best-effort GLOBAL call on the truncation path (§6.3).
TRUNCATION_GLOBAL_TIMEOUT_S = 20.0
TRUNCATION_GLOBAL_INPUT_CAP_CHARS = 40_000
TRUNCATION_GLOBAL_MIN_TOKENS_BEFORE = 2_000


class CompactionAborted(Exception):
    """Pre-write guard failure: replacement does not match snapshot.

    Raised by :func:`build_sentinel_replacement` when the injected +
    preserved-tail ids / counts in the replacement do NOT match the
    pre-compaction snapshot exactly. The caller is expected to let this
    propagate: compaction fails open, the checkpoint is untouched, and
    the next attempt retries from a clean state. This is the W1
    mitigation per architect §5.1.
    """


def _next_compaction_seq(messages: list[BaseMessage], instance_id: str) -> int:
    """Return next compaction seq for the given instance.

    Parses prior ``compaction-global-{iid}-*`` ids from the pre-compaction
    snapshot and returns ``max_parsed + 1`` (or ``1`` if no prior
    compaction doc for this instance is present). The parse is
    instance-scoped so two instances cannot collide on a shared seq.

    Args:
        messages: Pre-compaction snapshot (read-only — NOT mutated).
        instance_id: The owning instance id; included in the id prefix
            so multiple instances coexist on the same seq axis.

    Returns:
        The next seq integer. Always ``>= 1``.
    """
    needle_prefix = f"{GLOBAL_DOC_ID_PREFIX}{instance_id}-"
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


def _extract_previous_overview(messages: list[BaseMessage], instance_id: str) -> str | None:
    """Extract the prior doc's GLOBAL OVERVIEW text for pass-2 seeding.

    W1 fix (2026-09-01) — pass-2 convergence. When the pre-compaction
    snapshot contains a prior ``compaction-global-{iid}-{seq}``
    SystemMessage, its GLOBAL OVERVIEW body is the seed the new merge
    pass uses so the GLOBAL frame CONVERGES across passes (architect
    §4 — "the global frame converges across passes instead of being
    re-derived"). The engine hands the extracted text to the doc
    builder, which prepends it as a ``Previous overview: …`` line.

    Returns the GLOBAL OVERVIEW text (between ``── GLOBAL OVERVIEW ──``
    and ``── SECTION DETAIL ──`` / ``── END OF COMPACTED CONTEXT ──``)
    from the highest-seq prior doc for this instance. Returns ``None``
    when no prior doc exists OR the prior doc's body cannot be parsed
    (the doc builder then omits the ``Previous overview:`` line —
    silent fallback, not an error).

    Args:
        messages: Pre-compaction snapshot (read-only — NOT mutated).
        instance_id: The owning instance id; matches the doc id prefix.

    Returns:
        The prior GLOBAL OVERVIEW text, or ``None`` if no prior doc /
        unparsable body.
    """
    needle_prefix = f"{GLOBAL_DOC_ID_PREFIX}{instance_id}-"
    best_seq = -1
    best_msg: SystemMessage | None = None
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
        if seq > best_seq:
            best_seq = seq
            best_msg = msg
    if best_msg is None:
        return None
    body = getattr(best_msg, "content", "") or ""
    if not isinstance(body, str):
        body = str(body)
    # Slice between GLOBAL OVERVIEW marker and the next section /
    # boundary marker.
    overview_marker = "── GLOBAL OVERVIEW ──"
    section_marker = "── SECTION DETAIL ──"
    boundary_marker = "── END OF COMPACTED CONTEXT"
    start = body.find(overview_marker)
    if start == -1:
        return None
    start = start + len(overview_marker)
    # Skip any leading ``Previous overview: …`` from the prior doc
    # itself — we want the prior GLOBAL body, not the prior seed.
    body_from = body[start:].lstrip("\n")
    # Find the next major marker.
    section_at = body_from.find(section_marker)
    boundary_at = body_from.find(boundary_marker)
    candidates = [i for i in (section_at, boundary_at) if i != -1]
    end = min(candidates) if candidates else len(body_from)
    overview = body_from[:end].rstrip()
    # Drop a leading ``Previous overview: …\n\n`` line if the prior
    # doc itself had a seed (recursion guard — we pass the seed of
    # the seed, not the prior doc's seed).
    if overview.startswith("Previous overview:"):
        # Find the first blank-line separator after the seed.
        sep = overview.find("\n\n")
        if sep != -1:
            overview = overview[sep + 2 :].lstrip("\n")
    return overview or None


def make_remove_all_sentinel() -> "RemoveMessage":
    """The ``REMOVE_ALL_MESSAGES`` sentinel element (P1b helper).

    Same resolution as :func:`build_sentinel_replacement` (real
    ``langgraph.graph.message`` constant, falling back to the
    source-verified literal ``"__remove_all__"`` under the test-mocked
    runtime). Exported so the P1b 95% hook can build a SENTINEL-FIRST
    node-return prefix (``[sentinel, *post_compaction_channel]``) —
    the node's own task commit then LANDS the compaction (a
    mid-superstep ``aupdate_state`` alone is superseded when the
    in-flight task returns; see the T2-ext canary).
    """
    try:
        from langgraph.graph.message import REMOVE_ALL_MESSAGES
    except (ImportError, ModuleNotFoundError):
        REMOVE_ALL_MESSAGES = "__remove_all__"
    return RemoveMessage(id=REMOVE_ALL_MESSAGES)


def build_sentinel_replacement(
    result: "CompactionResult",
    current_messages: list[BaseMessage],
    compacted_ids: set[str] | None = None,
) -> list[BaseMessage]:
    """W1 fix: land the intended order verbatim via the REMOVE_ALL sentinel.

    Architect §5 (persist-seam) — shared helper, exported, called at all
    three sites (on-demand, proactive, reactive).

    1. **PRE-WRITE GUARD (mandatory, 🔴 mitigation)**: the
       replacement must include EVERY snapshot id EXCEPT those that
       were intentionally compacted (the engine passes
       ``compacted_ids`` — the ids of the messages that were
       summarized or trimmed). On a missing preserved-tail id: raise
       :class:`CompactionAborted` — compaction fails open, checkpoint
       untouched. (W1 fix per architect §3.)
    2. **Desired final order** (tail keeps ORIGINAL ids, full message
       objects):
       ``[permanent/unanswered injected…][compaction doc][tail…]``
       — the hoisted head is ``context_kind`` messages plus UNANSWERED
       bare-flag notes. ANSWERED bare notes are NOT hoisted: they stay
       in the doc/tail flow (absorbed into the compacted span like
       regular history).
    3. **Return**
       ``[RemoveMessage(id=REMOVE_ALL_MESSAGES), *injected, *doc, *tail]``
       — sentinel MUST be element 0 (anything before it is discarded).
       No per-id RemoveMessages are sent (eliminates the
       ValueError-on-absent-id class entirely). The doc and tail are
       emitted as full message objects (existing-id tail messages
       upsert in place; the new-id doc appends at the sentinel boundary).

    Reducer facts this builds on (source-verified langgraph 1.0.9):

    * existing-id input → upsert IN PLACE
    * new-id input → append at channel end
    * sentinel → everything after the first sentinel becomes the
      ENTIRE new channel value, verbatim order

    Atomicity: one checkpoint write for messages (same as today);
    crash-after-write-1 leaves messages compacted with stale
    ``compacted_at``; later dedup re-compact is CONVERGENT under
    sentinel + same ids — the new GLOBAL frame incorporates the
    prior doc's GLOBAL via the W1 ``previous_overview`` seed,
    so the chain converges across passes instead of being
    re-derived from scratch (architect §4).

    Args:
        result: The :class:`CompactionResult` produced by the engine.
        current_messages: The pre-compaction message list (the channel
            value read from checkpoint BEFORE the write).
        compacted_ids: Optional set of message ids that were
            INTENTIONALLY removed (the compactable span). When
            ``None``, the guard checks every snapshot id (the
            strictest mode). When provided, the guard excludes these
            ids from the "must survive" check. This is the production
            path — the engine knows exactly which ids it dropped.

    Returns:
        The replacement list, with the ``REMOVE_ALL_MESSAGES`` sentinel
        at index 0 and the full new channel value after.

    Raises:
        CompactionAborted: When the pre-write guard fails (a
            preserved-tail id is missing from the replacement).
            Compaction fails open, the checkpoint is left untouched,
            and the caller is expected to surface a non-fatal warning.
    """
    # langgraph 1.0.9 — REMOVE_ALL_MESSAGES sentinel. The
    # conftest for unit tests mocks the ``langgraph`` namespace
    # as a non-package; we tolerate ImportError here by falling
    # back to the source-verified literal value
    # ``"__remove_all__"`` so the helper works under both the
    # real-langgraph runtime AND the test-mocked runtime.
    #
    # W4 fix (2026-09-01) — ImportError fallback drift guard.
    # When the fallback fires, the helper now emits a one-time
    # WARNING (NOT silent) so a future upstream rename of
    # ``REMOVE_ALL_MESSAGES`` (which would make the import
    # succeed but the sentinel constant be stale) does NOT
    # silently use the wrong literal. Tests that need to pin
    # the real sentinel use ``_load_real_add_messages``'s
    # swap window to import from the real package.
    REMOVE_ALL_FALLBACK_EMITTED = False
    try:
        from langgraph.graph.message import REMOVE_ALL_MESSAGES
    except (ImportError, ModuleNotFoundError):
        import logging as _logging
        if not REMOVE_ALL_FALLBACK_EMITTED:
            _logging.getLogger(__name__).warning(
                "build_sentinel_replacement: langgraph.graph.message "
                "is not importable in this runtime; using the "
                "source-verified literal '__remove_all__' as the "
                "REMOVE_ALL_MESSAGES sentinel. This is expected "
                "under the test conftest's mocked langgraph; in "
                "production, a missing import here indicates a "
                "langgraph install/rename drift."
            )
            REMOVE_ALL_FALLBACK_EMITTED = True
        REMOVE_ALL_MESSAGES = "__remove_all__"

    # Step 1: split result.replacement_messages into three buckets:
    #   - removals: RemoveMessage items (the new code emits zero of
    #     these, but be defensive — if any leak through, fold them
    #     into the sentinel)
    #   - new_keepables: every non-RemoveMessage message (doc + tail)
    #   - injected: the hoisted subset of new_keepables — messages
    #     carrying the injected_message flag that must keep their head
    #     position (C3). Injected-notes hoisting fix: ``context_kind``
    #     messages and UNANSWERED bare-flag notes hoist; ANSWERED bare
    #     notes stay in doc_and_tail (absorbed into the compacted span).
    #     Answeredness is derived from ``current_messages`` — the same
    #     pre-compaction channel the engine partitioned — so the seam's
    #     hoist decision matches the engine's selection decision.
    #
    # Note: the new design emits a SINGLE SystemMessage (the doc) plus
    # the preserved tail (HumanMessage/AIMessage/etc., unchanged) and
    # the hoisted injected messages. Removals are NOT emitted as
    # RemoveMessage items — the sentinel replaces them.
    answered_note_ids = _injected_note_absorbed_ids(current_messages)
    keepables: list[BaseMessage] = [
        m for m in result.replacement_messages
        if not isinstance(m, RemoveMessage)
    ]
    injected: list[BaseMessage] = [
        m for m in keepables if _is_hoisted_injected(m, answered_note_ids)
    ]
    doc_and_tail: list[BaseMessage] = [
        m for m in keepables if not _is_hoisted_injected(m, answered_note_ids)
    ]

    # Step 2: PRE-WRITE GUARD — the sentinel recipe is
    # write-the-entire-new-channel-value. The load-bearing safety
    # property is: every snapshot id that COULD be a silent-loss
    # target — i.e. appears in the snapshot but NOT in the
    # replacement AND is NOT in the explicitly-compacted set — is
    # forbidden. The doc itself is a NEW id (that is the whole point
    # of the sentinel recipe) and is allowed to be absent from the
    # snapshot.
    #
    # Note: ``None``-id messages are NOT counted in the guard (they
    # have no id for the reducer to look up, so they cannot be a
    # sentinel-loss regression target).
    replacement_ids = [
        getattr(m, "id", None)
        for m in injected + doc_and_tail
    ]
    # Ride-along (2026-09-01) — membership check via SET, not
    # list. ``sid in <list>`` is O(n) per id; ``sid in <set>`` is
    # O(1). The seam is in the hot path of every compaction
    # write, and the ``compacted_ids`` parameter is already a
    # set; using a list on the other operand is asymmetric and
    # needlessly slow.
    replacement_ids_set: set[str] = {
        rid for rid in replacement_ids if rid
    }
    snapshot_ids = [getattr(m, "id", None) for m in current_messages]
    snapshot_ids_set: set[str] = {
        i for i in snapshot_ids if i
    }
    if compacted_ids is not None:
        assert compacted_ids <= snapshot_ids_set, (
            "compacted_ids must be a subset of the snapshot message ids"
        )
    # The ONE safety check: every removed id must be in the
    # engine's declared ``compacted_ids`` set. When the engine
    # passes ``None`` (legacy / test fixture), the helper falls
    # back to the strict mode — every removed id must be in
    # compacted_ids.
    snapshot_ids_lost = [
        sid for sid in (snapshot_ids_set - replacement_ids_set)
        if not compacted_ids or sid not in compacted_ids
    ]
    if snapshot_ids_lost:
        raise CompactionAborted(
            f"pre-write guard: {len(snapshot_ids_lost)} preserved-tail "
            f"ids would be silently lost (not in the replacement, "
            f"not in the explicitly-compacted set). Refusing to "
            f"write under sentinel — silent-loss class. "
            f"Lost ids (first 5): {snapshot_ids_lost[:5]}"
        )

    return [
        RemoveMessage(id=REMOVE_ALL_MESSAGES),
        *injected,
        *doc_and_tail,
    ]


# Back-compat alias for older code paths / tests that referenced the
# old module-scope helper. The boundary line is now a string constant
# (``GLOBAL_DOC_BOUNDARY_LINE``); the helper form simply appends a
# one-shot SystemMessage carrying that line — this is used by a small
# number of legacy tests that assert the marker count is 1. The
# canonical place for the boundary is INSIDE the global doc
# (``build_global_doc``); the alias is only a stub for legacy
# assertions.
def _append_truncation_marker(replacement: list) -> None:
    """Legacy alias — no-op in the post-§5 design.

    The marker is now the boundary line inside the global doc
    (``build_global_doc``); there is no separate ``truncation-marker-``
    SystemMessage. The old tests that assert the marker count is 1
    have been rewritten to assert the doc count is 1 (the new
    contract). This alias exists only so legacy ``from daemon.compaction
    import _append_truncation_marker`` imports keep resolving; it is a
    no-op.
    """
    return None


def _extract_instance_id(context) -> str:
    """Pull the instance id from a compaction context.

    Returns ``context.instance_id`` when set, else falls back to an
    empty string. The doc id derivation tolerates an empty id (the
    engine then uses a global scan for the seq, which is unsafe under
    cross-instance sharing — production callers MUST pass the
    instance id; this fallback exists for legacy tests that did not
    pass one).
    """
    return getattr(context, "instance_id", "") or ""


def _count_preserved(
    context,
    compactable,
) -> int:
    """Count preserved-tail messages for the doc envelope header.

    Reads ``context._preserved_count_for_doc`` when set (the engine
    stamps it before calling the doc builder). Falls back to 0 when
    the context is missing the marker (test code, fresh code paths).
    """
    return int(getattr(context, "_preserved_count_for_doc", 0) or 0)


def _compute_section_counts(result_kwargs: dict) -> tuple[int, int]:
    """Derive ``(sections_kept, sections_total)`` from a result dict.

    Counts the SECTION headers in the global doc body (k) and the
    total batch count from the envelope header's ``{k}/{n} sections``
    clause (n). For a FULL-success doc the body has a single section
    and the envelope carries ``1/1 sections`` (or the count of
    batches that were merged into the GLOBAL on the success path —
    typically 1, since the merge collapses them). For a PARTIAL doc
    the body has one section per surviving batch and the envelope
    reports ``k/n sections`` with the failed batches in the
    dropped-spans clause.

    The function is intentionally tolerant: if the doc is missing
    (e.g. emergency_truncation path), it returns ``(0, 0)``. The FE
    reads the field DEFENSIVELY and falls back to the prior card
    copy when the values are unusable.
    """
    replacement = result_kwargs.get("replacement_messages", [])
    if not replacement:
        return 0, 0
    # The doc is the FIRST keepable message in the replacement.
    doc = None
    for m in replacement:
        if (
            not isinstance(m, RemoveMessage)
            and getattr(m, "id", "").startswith(GLOBAL_DOC_ID_PREFIX)
        ):
            doc = m
            break
    if doc is None or not getattr(doc, "content", ""):
        return 0, 0
    body = doc.content
    # Count section headers in the body (k).
    k = body.count("### SECTION ")
    # Parse the envelope's ``{k}/{n} sections`` token. Falls back
    # to (k, k) when the token is absent (e.g. the doc has no
    # coverage clause and n = k by construction).
    import re
    m = re.search(r"global overview \+ (\d+)/(\d+) sections", body)
    if m:
        try:
            n = int(m.group(2))
        except (ValueError, TypeError):
            n = k
    else:
        # truncation / full-success with no coverage clause.
        n = k
    return k, max(k, n)


# Architecture §4 — per-compaction output is ONE SystemMessage.
# These helpers build the doc body top-down: envelope → GLOBAL → SECTIONS
# → boundary. They are pure functions over the inputs the engine already
# has (compactable groups, per-batch summaries, failed batches, span
# index map); the conversation-time map and the seq are computed by the
# caller and passed in.
def _format_dropped_span_clauses(
    dropped_spans: list[tuple[int, int]] | None,
) -> str:
    """Format the dropped-without-summary clause for the envelope header.

    Architect §4: ``dropped without summary: {NONE | messages #{a}–#{b},
    #{c}–#{d} — content not recoverable}``. The clause is OMITTED when
    no spans are dropped (W4 fix — explicit dropped, never falsified).

    Args:
        dropped_spans: 1-based ``(start_idx, end_idx)`` pairs for spans
            inside the compactable window that were NOT summarized (any
            of: batch failed, batch timed out, batch budget-excluded).
            Empty list / ``None`` → ``"NONE"``.

    Returns:
        The clause text (just the inner list, NOT the leading
        ``dropped without summary: `` prefix — the caller builds the
        prefix for omission/presence).
    """
    if not dropped_spans:
        return "NONE"
    parts = []
    for s, e in dropped_spans:
        if s == e:
            parts.append(f"#{s}")
        else:
            parts.append(f"#{s}–#{e}")
    return "messages " + ", ".join(parts) + " — content not recoverable"


def _format_coverage_clause(k: int, n: int) -> str | None:
    """Format the k/n coverage clause for the envelope header.

    Architect §4: ``summarized messages #{start}–#{end} → global overview
    + {k}/{n} sections``. The clause is OMITTED when ``k == n`` (no
    partiality — every batch succeeded).
    """
    if k == n:
        return None
    return f"{k}/{n} sections"


def _conversation_time_for(
    msg_id: str | None,
    msg_timestamps: dict | None,
) -> str | None:
    """Return first-appearance timestamp for ``msg_id``, or ``None``.

    Architect §6.5 / §4: the per-section conversation-time clause uses
    the first-appearance checkpoint ``ts`` map (the same logic as
    ``persistence.py:322-356``). Missing map rows → clause OMITTED
    (never generation-time fallback).
    """
    if not msg_id or not msg_timestamps:
        return None
    return msg_timestamps.get(msg_id)


def _extract_msg_timestamps(
    messages: list[BaseMessage],
) -> dict[str, str]:
    """Build the first-appearance ``{msg_id: iso_ts}`` map from messages.

    F1 (2026-09-01) — wire conversation-time provenance into the
    production doc builder. Walks ``messages`` in order; for each
    message carrying a timestamp, stamps the FIRST appearance as the
    value (later appearances on the same id are ignored, so the
    section-boundary lookup always returns the original conversation
    time even if the message re-appears after a re-compaction cycle).

    Timestamp precedence (first non-empty wins):

    1. ``message.created_at`` — LangChain ``BaseMessage`` attribute
       (timezone-aware ``datetime``).
    2. ``message.additional_kwargs["ts"]`` — string ISO timestamp
       (the engine / message_queue stamping convention).
    3. ``message.additional_kwargs["created_at"]`` — ISO string.

    Missing timestamp on a message → that id is OMITTED from the map;
    the doc builder's section-header clause will then be OMITTED
    (architect §4 — never generation-time fallback). The function
    returns an empty dict when no message carries a timestamp; this
    is the "no provenance available" case and the doc still emits
    without the time clause (mirrors the existing
    ``msg_timestamps=None`` semantics in :func:`build_compaction_doc`).
    """
    out: dict[str, str] = {}
    for msg in messages:
        mid = getattr(msg, "id", None)
        if not mid or mid in out:
            # Either no id (unrecoverable) or already seen (first
            # appearance wins — the SPEC of the map).
            continue
        ts = _msg_timestamp_iso(msg)
        if ts:
            out[mid] = ts
    return out


def _msg_timestamp_iso(msg: BaseMessage) -> str | None:
    """Pull a single ISO timestamp string from a message, or ``None``.

    Helper for :func:`_extract_msg_timestamps`. Tries, in order:
    ``created_at`` attribute, ``additional_kwargs["ts"]``,
    ``additional_kwargs["created_at"]``. Returns the ISO string, or
    ``None`` if none of the slots carries a value. Datetime objects
    are normalized to ISO via ``.isoformat()``; strings are passed
    through unchanged (already ISO by convention).
    """
    created = getattr(msg, "created_at", None)
    if created is not None:
        if hasattr(created, "isoformat"):
            return created.isoformat()
        # Strings: pass through.
        return str(created)
    extra = getattr(msg, "additional_kwargs", None) or {}
    ts = extra.get("ts") or extra.get("created_at")
    if ts is None:
        return None
    if hasattr(ts, "isoformat"):
        return ts.isoformat()
    return str(ts)


def _truncate_to_token_cap(
    text: str,
    cap: int,
) -> str:
    """Truncate ``text`` to fit within ``cap`` tokens (approx).

    W5 fix (2026-09-01) — the GLOBAL OVERVIEW cap is enforced
    here at doc assembly, not just in the merge prompt. The
    truncation is approximate (token estimation via
    ``estimate_messages_tokens`` wrapped in a single
    ``SystemMessage``); we iteratively shrink the text until it
    fits, preserving the head of the content (the model is
    instructed to put the most important entities/decisions
    first; we honor that order).

    The caller appends a ``[truncated]`` marker after this
    helper's output, so we DO NOT add one here.

    Args:
        text: Source text to truncate.
        cap: Maximum tokens (int). Caller passes
            ``GLOBAL_OVERVIEW_TOKEN_CAP``.

    Returns:
        Truncated text that fits within the cap. May be the
        original text (if it already fits).
    """
    if not text:
        return text
    full_tokens = estimate_messages_tokens(
        [SystemMessage(content=text)]
    )
    if full_tokens <= cap:
        return text
    # Iterative shrink: chop 20% off the tail each pass, then
    # re-estimate. Cheap bounded loop.
    candidate = text
    for _ in range(20):  # bounded retries — converge fast
        if estimate_messages_tokens(
            [SystemMessage(content=candidate)]
        ) <= cap:
            return candidate
        # Chop 20% of the tail. Use a character heuristic
        # (faster than tiktoken per-iteration) and let the
        # final token estimate verify.
        new_len = max(1, int(len(candidate) * 0.8))
        candidate = candidate[:new_len]
    # Last-ditch: return what we have (still over cap; caller
    # will mark truncated).
    return candidate


def _apply_ceiling_rule(
    sections: list[dict],
    global_overview: str | None,
    context_window: int,
) -> tuple[list[dict], str | None, int]:
    """Apply architect §6.4 ceiling rule.

    ``GLOBAL + Σsections ≤ 15% of context window``. Breach → condense
    OLDEST sections first, never GLOBAL. Hard cap (still over) → B-shape
    degrade: GLOBAL + ARCHIVED line, sections dropped to metadata only
    (start/end indices, no body).

    Returns:
        ``(condensed_sections, archived_line_or_none, archived_count)``
        where ``archived_line_or_none`` is the literal ARCHIVED-line
        text to embed in the doc, ``None`` if ceiling was respected.
    """
    cap_tokens = int(context_window * COMPACTION_DOC_CEILING_FRACTION)
    # Approximate via estimate_messages_tokens (the loader's tiktoken
    # path; cheap and bounded). Cap is in TOKENS, not chars.
    current_tokens = estimate_messages_tokens(
        [SystemMessage(content=global_overview or GLOBAL_DOC_PLACEHOLDER_GLOBAL)]
    ) + sum(
        estimate_messages_tokens([SystemMessage(content=s["body"])])
        for s in sections
    )
    if current_tokens <= cap_tokens:
        return sections, None, 0

    # Condense OLDEST sections first. Heuristic: drop body text but
    # keep start/end indices for archival reference. Repeat until
    # within cap, then mark archived_count.
    archived = 0
    condensed = list(sections)
    for i in range(len(condensed)):
        if current_tokens <= cap_tokens:
            break
        old_body = condensed[i]["body"]
        # Replace body with a short reference marker. Token savings
        # depend on body length; ``current_tokens`` is recomputed only
        # at the end of the loop body to keep the iteration cheap.
        condensed[i] = {
            **condensed[i],
            "body": "(condensed for budget — see GLOBAL OVERVIEW)",
        }
        archived += 1
        current_tokens = estimate_messages_tokens(
            [SystemMessage(content=global_overview or GLOBAL_DOC_PLACEHOLDER_GLOBAL)]
        ) + sum(
            estimate_messages_tokens([SystemMessage(content=s["body"])])
            for s in condensed
        )

    if current_tokens > cap_tokens:
        # Hard cap → B-shape: GLOBAL + ARCHIVED line only, drop
        # ALL sections (no section bodies in the doc; only the
        # coverage metadata in the envelope).
        return [], GLOBAL_DOC_ARCHIVED_LINE.format(n=len(sections)), len(sections)
    return condensed, None, archived


def _format_section_header(
    i: int,
    n: int,
    start_idx: int,
    end_idx: int,
    msg_timestamps: dict | None,
    start_id: str | None = None,
    end_id: str | None = None,
) -> str:
    """Format a single SECTION header line per architect §4.

    ``### SECTION {i}/{n} — messages #{s}–#{e} | conversation time
    {t0_iso} → {t1_iso}``. The conversation-time clause is OMITTED
    when the first-appearance map has no rows for either boundary
    (architect §4, O13 — never generation-time fallback).
    """
    s_label = f"#{start_idx}" if start_idx == end_idx else f"#{start_idx}–#{end_idx}"
    t0 = _conversation_time_for(start_id, msg_timestamps)
    t1 = _conversation_time_for(end_id, msg_timestamps)
    header = f"### SECTION {i}/{n} — messages {s_label}"
    if t0 and t1:
        header += f" | conversation time {t0} → {t1}"
    return header


def build_compaction_doc(
    *,
    instance_id: str,
    seq: int,
    mode: str,  # "summary" | "partial_summary" | "truncation"
    compacted_at: str,
    global_overview: str | None,  # None → placeholder (merge failed)
    sections: list[dict],  # each {start_idx, end_idx, body, start_id, end_id}
    total_sections: int,  # k/n: total = surviving + failed; k = len(sections)
    summarized_start: int,  # 1-based start idx of the compactable span
    summarized_end: int,  # 1-based end idx of the compactable span
    preserved_count: int,
    dropped_spans: list[tuple[int, int]],
    msg_timestamps: dict | None = None,
    context_window: int | None = None,
    previous_overview: str | None = None,  # pass-2 seed (architect §4)
) -> SystemMessage:
    """Build the single ``compaction-global-{iid}-{seq}`` doc.

    Architect §4 — message format spec. The body reads top-down:

    1. **Envelope header** (mode, compacted_at, coverage, dropped
       spans, preserved count, self_id).
    2. **GLOBAL OVERVIEW** (~600 tok cap; on merge-pass failure
       replaced by the placeholder line; absent on a truncation path
       that did not produce a best-effort GLOBAL — handled by the
       caller passing ``global_overview=""``).
    3. **SECTION DETAIL** (one block per surviving batch, batch
       order, with provenance header and arc-local body).
    4. **Boundary line** (always present).

    Conditional clauses are OMITTED, never falsified:

    * coverage ``{k}/{n} sections`` only when ``k < n``
    * ``dropped without summary: …`` only when non-empty
    * ``Previous overview: …`` only when ``previous_overview`` is
      non-empty (pass-2 seed)
    * ``ARCHIVED: …`` only when the ceiling rule fired

    Per-section times are CONVERSATION times from the first-appearance
    map; missing rows → clause OMITTED (never generation-time
    fallback).

    Args:
        instance_id: Owning instance id (used for the
            ``compaction-global-{iid}-{seq}`` id).
        seq: Sequence number (from :func:`_next_compaction_seq`).
        mode: Compaction mode — ``"summary"`` (full), ``"partial_summary"``,
            or ``"truncation"``.
        compacted_at: Generation timestamp (ISO); the ONLY generation
            timestamp in the doc (envelope header).
        global_overview: The merged GLOBAL text (may be ``None`` on
            merge-pass failure → placeholder; may be empty string on
            truncation with no best-effort GLOBAL).
        sections: Per-batch sections in batch order. Each dict has
            ``start_idx``, ``end_idx`` (1-based, inclusive), ``body``,
            ``start_id`` and ``end_id`` (message ids at the span
            boundaries; used for the time clause).
        summarized_start: 1-based start of the overall compactable
            span (for the envelope header).
        summarized_end: 1-based end of the overall compactable span.
        preserved_count: Number of preserved-tail messages.
        dropped_spans: 1-based ``(start, end)`` tuples for spans that
            were dropped without summary.
        msg_timestamps: First-appearance map (msg_id → ISO ts); may be
            ``None`` when the caller cannot compute it (clause
            omission per architect §4).
        context_window: Total context window size (for the ceiling
            rule). When ``None``, ceiling rule is skipped.
        previous_overview: When non-empty, prepended to the new
            GLOBAL as a seed for cross-pass convergence
            (architect §4 — "the global frame converges across
            passes instead of being re-derived").

    Returns:
        A single ``SystemMessage`` with id
        ``compaction-global-{iid}-{seq}`` and a body composed as
        described above. NO timestamp leak anywhere except the
        envelope header.
    """
    n_sections = len(sections)
    coverage_clause = _format_coverage_clause(n_sections, total_sections)
    dropped_clause = _format_dropped_span_clauses(dropped_spans)

    span_label = (
        f"#{summarized_start}–#{summarized_end}"
        if summarized_start != summarized_end
        else f"#{summarized_start}"
    )

    # Envelope header — always one line, mode + generation ts + coverage
    # (optional) + dropped (optional) + preserved count + self_id.
    header_parts: list[str] = [
        f"[CONTEXT COMPACTION — mode={mode}",
        f"compacted_at={compacted_at}",
    ]
    if coverage_clause:
        header_parts[0] += f" | summarized messages {span_label} → global overview + {coverage_clause}"
    else:
        header_parts[0] += f" | summarized messages {span_label} → global overview + {n_sections}/{total_sections} sections"
    if dropped_spans:
        header_parts.append(f"dropped without summary: {dropped_clause}")
    header_parts.append(f"preserved verbatim: {preserved_count} most recent messages (below this notice)")
    header_parts.append(f"self_id=compaction-global-{instance_id}-{seq}]")
    envelope = " | ".join(header_parts) + "\n"

    # GLOBAL OVERVIEW — applies the ceiling rule when context_window is
    # supplied; for the truncation path the caller passes
    # ``global_overview=""`` and the placeholder is omitted (architect
    # §6.3 — "absent-on-failure" of the bounded best-effort).
    if context_window is not None and n_sections > 0:
        # Pass 1 of the ceiling rule: condense OLDEST sections if over
        # cap, never the GLOBAL.
        sections, archived_line, archived_count = _apply_ceiling_rule(
            sections, global_overview, context_window
        )
    else:
        archived_line = None
        archived_count = 0

    body = envelope + "\n── GLOBAL OVERVIEW ──\n"

    if previous_overview:
        body += f"Previous overview: {previous_overview}\n\n"
    if global_overview:
        # W5 fix (2026-09-01) — enforce
        # ``GLOBAL_OVERVIEW_TOKEN_CAP`` at doc assembly. Today
        # the cap is prompt-soft only (the merge prompt says
        # "keep under ~600 tokens"); an over-long GLOBAL text
        # from a misbehaving model OR a multi-section partial
        # path would blow the cap silently. Truncate to the
        # cap when the body exceeds it, appending a
        # ``[truncated — see SECTION DETAIL for arc-local
        # detail]`` marker so the user knows the GLOBAL is
        # not the full text.
        global_tokens = estimate_messages_tokens(
            [SystemMessage(content=global_overview)]
        )
        if global_tokens > GLOBAL_OVERVIEW_TOKEN_CAP:
            truncated = _truncate_to_token_cap(
                global_overview, GLOBAL_OVERVIEW_TOKEN_CAP
            )
            body += (
                f"{truncated}\n"
                f"[truncated — full GLOBAL overview text exceeded the "
                f"GLOBAL_OVERVIEW_TOKEN_CAP={GLOBAL_OVERVIEW_TOKEN_CAP}; "
                f"SECTION DETAIL below remains complete]\n"
            )
        else:
            body += f"{global_overview}\n"
    elif mode == "truncation":
        # Architect §6.3 — bounded best-effort GLOBAL call failed or
        # was skipped; the envelope already lists the dropped spans,
        # so the doc has no body text here. The boundary line
        # immediately follows.
        pass
    else:
        # merge pass failed (W2-adjacent); placeholder.
        body += f"{GLOBAL_DOC_PLACEHOLDER_GLOBAL}\n"

    if n_sections > 0:
        body += "\n── SECTION DETAIL ──\n"
        for i, sec in enumerate(sections, start=1):
            header = _format_section_header(
                i=i,
                n=n_sections,
                start_idx=sec["start_idx"],
                end_idx=sec["end_idx"],
                msg_timestamps=msg_timestamps,
                start_id=sec.get("start_id"),
                end_id=sec.get("end_id"),
            )
            body += f"{header}\n{sec['body']}\n\n"
    if archived_count:
        body += f"── ARCHIVED: {archived_count} oldest sections condensed for budget; global overview above is authoritative ──\n"

    body += f"\n{GLOBAL_DOC_BOUNDARY_LINE}"

    return SystemMessage(
        id=f"{GLOBAL_DOC_ID_PREFIX}{instance_id}-{seq}",
        content=body,
    )


# Phase 1 / WS-3.1: shared adaptive-timeout formula for the three LLM
# call origins (single-batch :900, merge :939, condense :971 —
# pre-feature the inline expression was duplicated three times; the
# helper consolidates it to a single source of truth). The plan REJECTS
# using ``context.messages`` as the input (architect §3 Correction 1)
# — that would over-estimate every call after the first chunk and
# massively over-estimate merge/condense. Input MUST be the prompt
# actually being sent at the call site.
def _summarization_timeout_s(prompt: str, config: CompactionConfig) -> float:
    """Adaptive per-call LLM timeout for summarization calls.

    Formula:
        ``min(timeout_cap_s, timeout_base_s + (tokens/100_000) * timeout_per_100k_tokens_s)``

    Args:
        prompt: The exact prompt string the caller is about to send. Sized
            via ``estimate_messages_tokens`` (loader.py:465, tiktoken
            cl100k_base) wrapped in a single ``HumanMessage`` so the
            per-message overhead matches the actual payload. NOT
            ``context.messages`` — that over-estimates and breaks
            merge/condense timeouts (architect §3 Correction 1).
        config: Active ``CompactionConfig`` carrying the adaptive knobs.

    Returns:
        Per-call timeout in seconds. Capped at ``config.timeout_cap_s``.
    """
    tokens = estimate_messages_tokens([HumanMessage(content=prompt)])
    return min(
        config.timeout_cap_s,
        config.timeout_base_s + (tokens / 100_000) * config.timeout_per_100k_tokens_s,
    )


# (The former ``_partition_injected_messages`` two-way split was
# replaced by :func:`_partition_injected_for_compaction` — the
# injected-notes hoisting contract change: bare-flag operator notes
# become selectable once answered, so the engine needs the three-bucket
# partition above.)


# Context window sizes for known models (in tokens)
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    # OpenAI models
    "gpt-4": 8192,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4.5": 128000,
    "gpt-3.5-turbo": 16385,
    "o1": 200000,
    "o1-mini": 128000,
    "o3-mini": 200000,
    
    # Anthropic models (via OpenAI-compatible API)
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-3.5-sonnet": 200000,
    "claude-3.5-haiku": 200000,
    "claude-4": 200000,
    
    # Open-source models
    "llama-3": 8192,
    "llama-3.1": 128000,
    "mistral": 32000,
    "mixtral": 32000,
    "deepseek": 128000,
    "qwen": 32768,
}

DEFAULT_CONTEXT_LIMIT = 700000


def get_model_context_limit(model_name: str, config: object | None = None) -> int:
    """Get the context window limit for a given model name.

    Resolution order (first match wins):
    1. ``config.context_window_overrides`` — substring match against the model
       name; longest key wins. Lets operators cap distinct models (e.g. a
       smaller vision model) without touching the registry.
    2. ``MODEL_CONTEXT_LIMITS`` registry — fuzzy substring match (case-insensitive).
    3. ``config.context_window_default`` — used when neither overrides nor the
       registry match. Set to 0 to fall through to the hard-coded fallback.
    4. ``DEFAULT_CONTEXT_LIMIT`` — last-resort fallback.

    Args:
        model_name: Model identifier string (e.g., "gpt-4o", "claude-3.5-sonnet").
            Whitespace is stripped and matching is case-insensitive.
        config: Optional config object exposing ``context_window_overrides``
            (dict[str, int]) and ``context_window_default`` (int). Both are
            optional; missing attributes are treated as empty/zero.

    Returns:
        Context window size in tokens.
    """
    # Normalize once so override and registry matching see the same string.
    normalized = model_name.strip().lower()

    # Per-model overrides take priority (longest key first for specificity)
    if config is not None:
        overrides = getattr(config, "context_window_overrides", None) or {}
        if overrides and normalized:
            for key in sorted(overrides.keys(), key=len, reverse=True):
                if not key:
                    continue
                if key.lower() in normalized:
                    return int(overrides[key])

    # Direct match first
    if normalized in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[normalized]

    # Fuzzy match: check if any registry key is contained in the model name
    # Check longer keys first to get more specific matches
    for key in sorted(MODEL_CONTEXT_LIMITS.keys(), key=len, reverse=True):
        if key in normalized:
            return MODEL_CONTEXT_LIMITS[key]

    # Operator-supplied fallback when registry has no entry
    if config is not None:
        default = getattr(config, "context_window_default", 0)
        if default and default > 0:
            return int(default)

    return DEFAULT_CONTEXT_LIMIT


def resolve_compaction_model(config: CompactionConfig) -> str:
    """Effective compaction-model override from a :class:`CompactionConfig`.

    Precedence: ``config.model`` (canonical, env ``COMPACTION_MODEL`` /
    yaml ``compaction.model`` — env>yaml resolved in ``load_config``)
    → ``config.summarization_model`` (legacy alias, honored for
    backwards compatibility) → ``""`` (no override: session-model
    accessor + ``context_window_overrides``, the pre-existing behavior).

    Pure function of the config object — the parallel summarization pool
    calls this per batch (``_call_summarization_llm``), so every
    concurrent batch call resolves the SAME override with no shared
    mutable state. The empty-string result is falsy by design: callers
    branch on truthiness ("override active") exactly as the legacy
    ``summarization_model`` check did.
    """
    return config.model or config.summarization_model


# =============================================================================
# Phase 2: Compaction Engine
# =============================================================================

@dataclass
class CompactionContext:
    """Container for all inputs needed for context compaction.
    
    Attributes:
        messages: List of conversation messages to potentially compact.
        system_prompt_tokens: Token count of the system prompt (excluded from compaction).
        model_name: Model identifier for context window lookup.
        config: Compaction configuration settings.
        llm_config: LLM configuration for summarization calls.
        last_compacted_at: ISO timestamp of last compaction (if any).
    """
    messages: list[BaseMessage]
    system_prompt_tokens: int
    model_name: str
    config: CompactionConfig
    llm_config: dict
    last_compacted_at: str | None = None
    # Architect §4 — instance id is required to derive the doc id
    # ``compaction-global-{iid}-{seq}``. Optional default ``""`` for
    # legacy tests; production callers (compact_executor,
    # instance_messaging, graph.py) always pass it.
    instance_id: str = ""
    # Architect §6.3 — the bounded best-effort GLOBAL on the
    # truncation path needs ``tokens_before`` to gate the call
    # (skipped when ``< TRUNCATION_GLOBAL_MIN_TOKENS_BEFORE``).
    # Optional default ``0`` for legacy tests; production callers
    # stamp it before invoking ``_truncate_fallback``.
    tokens_before_total: int = 0
    # Architect §6.3 — pre-stamped generation timestamp so the
    # bounded call does not race with the outer ``compact_state``
    # clock. Optional default ``""``; production callers stamp it.
    compacted_at_iso: str = ""
    # Architect §4 / F1 (2026-09-01) — first-appearance
    # ``{msg_id: iso_ts}`` map for the SECTION DETAIL conversation-time
    # clause. Built by :func:`_extract_msg_timestamps` from
    # ``messages``; missing rows → clause OMITTED (never
    # generation-time fallback). Optional default ``None`` for legacy
    # tests / the dead watchover helper; the 3 production
    # ``compact_state`` callers stamp it before invoking the doc
    # builder.
    msg_timestamps: dict | None = None


@dataclass
class CompactionResult:
    """Result of a compaction operation.

    Attributes:
        replacement_messages: List containing RemoveMessage for deleted items
            and the new summary/retained messages.
        tokens_before: Total tokens before compaction.
        tokens_after: Total tokens after compaction (including system prompt).
        tokens_saved: Net tokens saved by compaction.
        messages_before: Number of messages before compaction.
        messages_after: Number of messages after compaction.
        compaction_type: Strategy used ("summarization", "chunked_summarization",
            "truncation", "partial_summary", "emergency_truncation").
        summarization_error: Error message if summarization failed.
        compacted_at: ISO timestamp when compaction occurred.
        forced: True when ``ContextCompactor.compact_state`` was invoked with
            ``force=True`` (WS-2 / architect §2 — only the threshold bypass is
            exposed; dedup + min-messages still apply). Additive default —
            existing construction sites continue to work unchanged, and the
            auto paths (proactive / reactive) emit ``forced=False`` by
            construction (S-7 anti-drift).
        failure_kind: When summarization fails mid-run, this is set to
            ``"timeout"`` (TimeoutError / asyncio.TimeoutError caught per
            WS-3.4 narrowing) or ``"error"`` (other exception). ``None`` on
            the success path. The executor maps ``failure_kind="timeout"``
            to the ``timed_out → fallback_applied`` SSE phases (WS-4 §7
            amendment).
    """
    replacement_messages: list[BaseMessage]
    tokens_before: int
    tokens_after: int
    tokens_saved: int
    messages_before: int
    messages_after: int
    compaction_type: str  # "summarization" | "chunked_summarization" | "truncation" | "partial_summary" | "emergency_truncation"
    summarization_error: str | None = None
    compacted_at: str | None = None
    forced: bool = False  # Phase 1 / WS-2: set by compact_state when force=True
    failure_kind: str | None = None  # Phase 1 / WS-3: "timeout" | "error" | None
    # Architect §6.2 — GLOBAL merge-pass outcome (independent of the
    # per-batch and per-chunk stop_reasons). ``"ok"`` when the merge
    # produced a GLOBAL OVERVIEW, ``"failed"`` on timeout/error after
    # the bounded retry (the doc is still emitted, but the GLOBAL
    # slot is filled with the placeholder line). ``None`` when the
    # merge call was not needed (single-batch / no surviving
    # summaries). Additive default — existing construction sites
    # that omit the field still satisfy the type.
    total_summary_status: str | None = None  # "ok" | "failed" | None
    # Architect §4 — the GLOBAL OVERVIEW text. Stored on the result
    # so the persist-seam helper can re-derive span boundaries and
    # pass the previous overview as a seed for pass-2 (cross-pass
    # convergence). ``None`` when merge failed / was not run. The
    # actual section bodies (per-batch text) live on the
    # replacement_messages doc.
    global_overview: str | None = None
    # Coordination note (2026-09-01, FE) — ``sections_kept`` and
    # ``sections_total`` are flat additive fields the FE reads
    # DEFENSIVELY via ``commandSectionCounts()`` to render the
    # /compact card copy. k = succeeded sections, n = total
    # batches. ``None`` when compaction did not run (e.g. the
    # ``emergency_truncation`` path is NOT a single-doc output and
    # does not carry the doc, so both are ``None``). The /compact
    # wire map threads these through to the command-progress detail
    # payload under the same names.
    sections_kept: int | None = None
    sections_total: int | None = None
    # B1 fix (2026-09-01) — engine-populated ``compacted_ids``.
    # The set of snapshot message ids that were INTENTIONALLY
    # removed by the engine on this compaction (the compactable
    # span, including emergency-truncation per-message targets).
    # The persist-seam sites consume this with a strict-None
    # fallback to the site-derived set; the site fallback ALSO
    # folds in any ``RemoveMessage`` target ids carried inside
    # ``replacement_messages`` (defense-in-depth — see B2 fix).
    # Without this, the pre-write guard is TAUTOLOGICAL because
    # the site-derived ``pre_ids − kept_ids`` partitions the
    # snapshot by construction (every kept id is in the
    # replacement, every dropped id is also in ``kept_ids`` only
    # if a ``RemoveMessage`` carries it; otherwise every dropped
    # id is silently lost under the sentinel).
    compacted_ids: frozenset[str] | None = None
    # Injected-notes hoisting fix — additive envelope counts the FE /
    # executor read DEFENSIVELY (mirrors the ``sections_kept`` /
    # ``sections_total`` pattern). ``injected_preserved`` = the
    # permanently-preserved injections (``context_kind`` blocks plus
    # UNANSWERED bare-flag notes) hoisted verbatim above the doc.
    # ``injected_absorbed`` = the ANSWERED bare notes that joined the
    # selectable pool and were consumed by the compacted span this
    # pass (summarized / truncated). An answered note left verbatim in
    # the preserved tail is in NEITHER count. ``None`` on legacy
    # construction sites that pre-date the fields.
    injected_preserved: int | None = None
    injected_absorbed: int | None = None


@dataclass
class ChunkedOutcome:
    """Phase 1 / WS-3.4 typed return for ``ContextCompactor._summarize_chunked``.

    Attributes:
        summaries: Successful per-batch summaries in order. May be
            empty (all batches failed) — caller branches on this
            (WS-3.4 binding: ``|S| = 0`` → existing truncate fallback;
            ``|S| ≥ 1`` → partial-summary assembly). Architect §4 / §6
            updated the element type from ``SystemMessage`` to
            ``str`` — the per-batch text is now embedded INSIDE the
            single global doc, not emitted as a separate message.
            The doc builder (and the merge call on the FULL path)
            receive this list directly.
        failed_batches: 0-based batch indices that did NOT produce a summary
            (either timed out per the WS-3.4 narrowing, or were skipped due
            to budget exhaustion). Length equals ``len(batches) - len(summaries)``
            after the loop terminates; partials are tracked for observability
            but the engine already encodes the stop semantics in
            ``stop_reason``.
        stop_reason: ``"completed"`` if all batches succeeded;
            ``"timeout"`` if a per-chunk TimeoutError tripped;
            ``"budget"`` if the whole-operation budget exhausted before the
            remaining batches could be issued;
            ``"error"`` if a non-timeout exception escaped per-chunk (outer
            handler at ``compact_state`` :744-772 still catches it via the
            broader ``except Exception`` for the fallback mapping).
    """
    summaries: list[str]
    failed_batches: list
    stop_reason: str  # "completed" | "timeout" | "error" | "budget"
    # Architect §6.2 — set on the FULL-success path when the merge
    # pass timed out / errored after the bounded retry. The caller
    # (``compact_state``) maps this to
    # ``total_summary_status="failed"`` on the result. Default
    # ``False`` preserves WS-3.4 backward-compat for callers that
    # construct a ``ChunkedOutcome`` without the field.
    merge_failed: bool = False
    # The original batch indices of the surviving bodies (in
    # the same order as ``summaries``). The partial path uses
    # this to land the right slice of ``compactable`` for each
    # body — critical when the survivor set is non-contiguous
    # (e.g. batches 0, 2, 4 succeed but 1, 3, 5 fail). The
    # default ``None`` means the caller is the contiguous
    # ``range(len(batches))`` case (no failed_batches).
    completed_idxs: list[int] | None = None
    # W3 fix (2026-09-01) — wall-clock seconds remaining in the
    # whole-operation budget AFTER the batch pool joined. The
    # outer merge call in ``compact_state`` consumes this so the
    # merge is anchored to the REMAINING budget (per §6.2 — not
    # the placeholder ``operation_budget_s - 0.0``). ``None`` on
    # the single-batch path (no pool was used) — caller falls
    # back to ``operation_budget_s``.
    budget_remaining_after_pool: float | None = None


@dataclass
class MessageGroup:
    """Represents an atomic message group that cannot be split during compaction.
    
    A MessageGroup is either a single message or an AI message followed by
    its related ToolMessages. Groups ensure tool call sequences remain atomic.
    
    Attributes:
        start_idx: Starting index in the original messages list.
        end_idx: Ending index (inclusive) in the original messages list.
        messages: The actual message objects in this group.
        group_type: Either "single" or "tool_sequence".
    """
    start_idx: int
    end_idx: int
    messages: list[BaseMessage]
    group_type: str  # "single" | "tool_sequence"


def identify_boundary_groups(messages: list[BaseMessage]) -> list[MessageGroup]:
    """Group messages into atomic units that cannot be split during compaction.
    
    Messages are grouped as follows:
    - Orphan tool messages become single-message groups
    - AI messages with tool_calls form groups with their corresponding ToolMessages
    - All other messages become single-message groups
    
    Args:
        messages: List of conversation messages to group.
        
    Returns:
        List of MessageGroup objects in chronological order.
    """
    groups: list[MessageGroup] = []
    i = 0
    
    while i < len(messages):
        msg = messages[i]
        msg_type = getattr(msg, "type", "unknown")
        
        if msg_type == "tool":
            # Orphan tool message - single group
            groups.append(MessageGroup(
                start_idx=i,
                end_idx=i,
                messages=[msg],
                group_type="single"
            ))
            i += 1
            continue
        
        if msg_type == "ai" and hasattr(msg, "tool_calls") and msg.tool_calls:
            # AI message with tool calls - collect matching tool responses
            tool_call_ids = set()
            for tc in msg.tool_calls:
                if isinstance(tc, dict):
                    tc_id = tc.get("id", "")
                else:
                    tc_id = getattr(tc, "id", "")
                if tc_id:
                    tool_call_ids.add(tc_id)
            
            # W2: If no valid tool_call_ids, treat as single message
            if not tool_call_ids:
                groups.append(MessageGroup(
                    start_idx=i,
                    end_idx=i,
                    messages=[msg],
                    group_type="single"
                ))
                i += 1
                continue
            
            # Collect following ToolMessages whose tool_call_id matches
            group_messages = [msg]
            group_end = i
            for j in range(i + 1, len(messages)):
                next_msg = messages[j]
                # W1: Use explicit None check to handle empty string tool_call_id
                if hasattr(next_msg, "tool_call_id") and getattr(next_msg, 'tool_call_id', None) is not None:
                    if next_msg.tool_call_id in tool_call_ids:
                        group_messages.append(next_msg)
                        group_end = j
                    else:
                        # Tool message not related to this AI - stop
                        break
                else:
                    # Non-tool message - stop
                    break
            
            groups.append(MessageGroup(
                start_idx=i,
                end_idx=group_end,
                messages=group_messages,
                group_type="tool_sequence"
            ))
            i = group_end + 1
            continue
        
        # Default: single message group
        groups.append(MessageGroup(
            start_idx=i,
            end_idx=i,
            messages=[msg],
            group_type="single"
        ))
        i += 1
    
    return groups


def select_compactable_groups(
    groups: list[MessageGroup],
    recent_window: int,
    min_window: int,
    context_window: int,
    system_prompt_tokens: int,
    estimate_fn: callable,
    config_threshold: float = 0.80,
    injected_tokens: int = 0,
) -> tuple[list[MessageGroup], list[MessageGroup], int]:
    """Select which groups to compact vs preserve using progressive window reduction.

    This function iteratively reduces the preserved window size until the total
    token count falls below the threshold, ensuring recent messages are kept intact.

    Args:
        groups: All message groups from identify_boundary_groups.
        recent_window: Desired number of recent groups to preserve.
        min_window: Hard minimum number of groups to preserve.
        context_window: Model's context window size in tokens.
        system_prompt_tokens: Token count of system prompt (excluded from compaction).
        estimate_fn: Function to estimate tokens for a message list.
        config_threshold: Fraction of context window that triggers compaction.
        injected_tokens: Tokens occupied by messages that MUST survive
            compaction (Phase 1 / L3 of proactive-compaction-fix —
            honest budget math). Injected messages are re-attached
            verbatim by every engine exit path, so the budget that
            decides whether compacting all regular groups brings the
            conversation under the threshold must include them —
            otherwise the engine would re-fire every dispatch on an
            injection-heavy instance. Default ``0`` preserves the
            pre-Phase-1 math for callers that don't pass it (notably
            the partial-summary tests that pre-date the fix).

    Returns:
        Tuple of (compactable_groups, preserved_groups, actual_window_size).
    """
    window = recent_window

    while window >= min_window:
        if len(groups) <= window:
            return [], groups, window

        preserved = groups[-window:]
        compactable = groups[:-window]
        preserved_tokens = (
            estimate_fn([msg for g in preserved for msg in g.messages])
            + injected_tokens
        )
        total = preserved_tokens + system_prompt_tokens
        threshold = context_window * config_threshold

        if total <= threshold:
            return compactable, preserved, window

        window -= 1

    # Fallback: use minimum window
    preserved = groups[-min_window:]
    compactable = groups[:-min_window] if len(groups) > min_window else []
    return compactable, preserved, min_window


def emergency_truncate(
    messages: list[BaseMessage],
    max_tokens: int,
    estimate_fn: callable,
    max_tool_response_chars: int = 2000,
    max_human_message_chars: int = 4000
) -> list[BaseMessage]:
    """Emergency truncation with 4-pass approach to fit within token limit.
    
    Pass 0: Convert all multimodal content to clean strings
    Pass 1: Truncate tool responses to max_tool_response_chars
    Pass 2: Truncate human messages to max_human_message_chars
    Pass 3: Progressive halving of content > 500 chars until under limit
    
    Args:
        messages: Messages to truncate.
        max_tokens: Target maximum tokens.
        estimate_fn: Function to estimate tokens.
        max_tool_response_chars: Max characters for tool responses.
        max_human_message_chars: Max characters for human messages.
        
    Returns:
        Truncated list of messages (deep copied).
    """
    # Pass 0: Deep copy and convert all multimodal content to clean strings
    truncated = copy.deepcopy(messages)
    for msg in truncated:
        if isinstance(msg.content, list):
            msg.content = _extract_text_from_content(msg.content)
    
    if estimate_fn(truncated) <= max_tokens:
        return truncated
    
    # Pass 1: Truncate tool responses
    for msg in truncated:
        if getattr(msg, "type", "") == "tool":
            content = _extract_text_from_content(msg.content)
            if len(content) > max_tool_response_chars:
                msg.content = content[:max_tool_response_chars] + "\n[...truncated]"
            else:
                msg.content = content  # Ensure string
    
    if estimate_fn(truncated) <= max_tokens:
        return truncated
    
    # Pass 2: Truncate human messages
    for msg in truncated:
        if getattr(msg, "type", "") == "human":
            content = _extract_text_from_content(msg.content)
            if len(content) > max_human_message_chars:
                msg.content = content[:max_human_message_chars] + "\n[...truncated]"
            else:
                msg.content = content  # Ensure string
    
    if estimate_fn(truncated) <= max_tokens:
        return truncated
    
    # Pass 3: Progressive halving of large content
    for msg in truncated:
        content = _extract_text_from_content(msg.content)
        if len(content) > 500:
            while len(content) > 500 and estimate_fn(truncated) > max_tokens:
                half_len = len(content) // 2
                # Find a good break point (end of sentence or line)
                break_point = content.rfind('. ', 0, half_len)
                if break_point == -1:
                    break_point = content.rfind('\n', 0, half_len)
                if break_point == -1:
                    break_point = half_len
                content = content[:break_point + 1] + "\n[...truncated]"
                msg.content = content
            
            if estimate_fn(truncated) <= max_tokens:
                return truncated
    
    # C1: After Pass 3, if still over limit, drop oldest messages as last resort
    while len(truncated) > 1 and estimate_fn(truncated) > max_tokens:
        truncated.pop(0)
    
    return truncated


def _truncate_batch_to_fit(
    batch_groups: list[MessageGroup],
    max_tokens: int,
    tokenizer_fn: callable,
    max_tool_response_chars: int = 2000
) -> list[MessageGroup]:
    """Truncate a batch of groups to fit within token limit.
    
    First converts all multimodal content to strings, then truncates tool responses,
    then drops oldest groups if still over limit.
    
    Args:
        batch_groups: Groups to truncate.
        max_tokens: Target maximum tokens.
        tokenizer_fn: Function to estimate tokens.
        max_tool_response_chars: Max characters for tool responses.
        
    Returns:
        Truncated list of groups (deep copied).
    """
    # Deep copy groups and convert all multimodal content to strings
    truncated_groups = []
    for group in batch_groups:
        group_copy = MessageGroup(
            start_idx=group.start_idx,
            end_idx=group.end_idx,
            messages=copy.deepcopy(group.messages),
            group_type=group.group_type
        )
        
        # Convert all multimodal content to clean strings first
        for msg in group_copy.messages:
            if isinstance(msg.content, list):
                msg.content = _extract_text_from_content(msg.content)
        
        # Truncate tool responses if over limit
        for msg in group_copy.messages:
            if getattr(msg, "type", "") == "tool":
                if len(msg.content) > max_tool_response_chars:
                    msg.content = msg.content[:max_tool_response_chars] + "\n[...truncated]"
        
        truncated_groups.append(group_copy)
    
    # If still over limit, drop oldest groups (keep at least 1)
    while len(truncated_groups) > 1 and tokenizer_fn(
        [msg for g in truncated_groups for msg in g.messages]
    ) > max_tokens:
        truncated_groups.pop(0)
    
    # W3: If single remaining group still exceeds max_tokens, truncate its messages
    # At this point, all content is already converted to strings
    if len(truncated_groups) == 1 and tokenizer_fn(
        [msg for g in truncated_groups for msg in g.messages]
    ) > max_tokens:
        for msg in truncated_groups[0].messages:
            content = getattr(msg, "content", "") or ""
            if len(content) > max_tool_response_chars:
                msg.content = content[:max_tool_response_chars] + "\n[...truncated]"

    return truncated_groups


class ContextCompactor:
    """Main compaction engine that handles context window management.
    
    This class orchestrates the compaction process, including:
    - Eligibility checking (dedup, minimum messages)
    - Token calculation and threshold detection
    - Message grouping and selection
    - LLM-based summarization with chunking
    - Fallback truncation strategies
    
    Usage:
        compactor = ContextCompactor(config, llm_config)
        result = await compactor.compact_state(context)
        if result:
            # Apply result.replacement_messages to LangGraph state
    """
    
    def __init__(self, config: CompactionConfig, llm_config: dict):
        """Initialize the compactor with configuration.
        
        Args:
            config: CompactionConfig with threshold, window, and model settings.
            llm_config: LLM configuration dict for summarization calls.
        """
        self.config = config
        self.llm_config = llm_config
        self.llm_config_with_headers = {
            **llm_config,
            "default_headers": {
                "x-proxy-app": "ensemble",
                "x-proxy-interleaved-thinking": "True",
                # X-LLMProxy-Buffer-Response: sent by default; omitted
                # entirely (never "false") when buffer_response_header is
                # disabled in the config dict. Default-on for dicts
                # lacking the key (older configs).
                **(
                    {"X-LLMProxy-Buffer-Response": "true"}
                    if llm_config.get("buffer_response_header", True)
                    else {}
                ),
            },
        }
        # ── P1b: 95% pre-call hook state (proactive-compaction-fix A.4) ──
        # Per-instance O(1) pre-filter state: ``instance_id →
        # (msg_count, total_tokens)`` of the last LLM-bound payload
        # estimate. Sibling of the checkpoint's ``compacted_at`` (which
        # lives in state, not here — this dict is the only per-instance
        # estimate cache). One entry per instance EVER SEEN (overwritten
        # per instance, never grown per-call); the dictionary grows
        # unbounded for the daemon lifetime — accepted as trivial while
        # values remain a two-int tuple (no eviction policy); revisit if
        # per-instance fields grow.
        self._precall_estimates: dict[str, tuple[int, int]] = {}
        # Rate-limit state for the hook's near-ceiling / skip WARN — at
        # most one WARN per instance per ``_precall_warn_interval_s``
        # seconds (mirrors the engine's 60s dedup window so the WARN
        # cadence matches the refire cadence).
        self._precall_warn_state: dict[str, float] = {}

    #: P1b — minimum seconds between ``[Compaction][precall-95]`` WARN
    #: emissions per instance (aligned with the engine's 60s dedup).
    _precall_warn_interval_s: float = 60.0

    def precall_estimate_get(
        self, instance_id: str
    ) -> tuple[int, int] | None:
        """Return the cached ``(msg_count, total_tokens)`` estimate, if any."""
        return self._precall_estimates.get(instance_id)

    def precall_estimate_needs_refresh(
        self,
        instance_id: str,
        msg_count: int,
        trigger_window: int,
    ) -> bool:
        """O(1) pre-filter for the 95% pre-call hook (ADDENDUM A.4).

        The estimator costs ~150–200 ms at 800 msgs / ~500k tokens; a
        multi-call tool-loop turn must not pay that on EVERY call. This
        filter says the estimator must run only when:

        1. there is NO cached estimate for the instance (first call), OR
        2. the payload message count grew since the cached estimate
           (tool results / injections append messages), OR
        3. the cached estimate already sat at ≥0.80× the trigger window
           (the at-risk band — re-check every call there).

        Common case (stable conversation, sub-80% occupancy) → ``False``
        → the hook returns without touching the estimator: O(1).

        Args:
            instance_id: Target instance.
            msg_count: Current LLM-bound payload message count.
            trigger_window: Gated trigger window (may be 0 — then arm 3
                never fires, arms 1–2 still work).

        Returns:
            ``True`` when the full estimator must run.
        """
        prev = self._precall_estimates.get(instance_id)
        if prev is None:
            return True
        prev_count, prev_tokens = prev
        if prev_count != msg_count:
            return True
        if trigger_window > 0 and prev_tokens >= 0.80 * trigger_window:
            return True
        return False

    def precall_estimate_record(
        self,
        instance_id: str,
        msg_count: int,
        total_tokens: int,
    ) -> None:
        """Cache the ``(msg_count, total_tokens)`` estimate for an instance."""
        self._precall_estimates[instance_id] = (msg_count, total_tokens)

    def precall_warn_should_emit(self, instance_id: str) -> bool:
        """Rate-limit gate for the hook's WARN emissions (one per interval).

        ``True`` when at least ``_precall_warn_interval_s`` seconds have
        elapsed since the last WARN for this instance (or none was ever
        emitted); records the emission. The 60s default mirrors the
        engine's dedup window so a stamped anti-refire skip (which
        silences the ENGINE for 60s) cannot be accompanied by a WARN
        storm in the same window.
        """
        now = time.monotonic()
        last = self._precall_warn_state.get(instance_id)
        if (
            last is not None
            and (now - last) < self._precall_warn_interval_s
        ):
            return False
        self._precall_warn_state[instance_id] = now
        return True

    def _effective_model_name(self, context: CompactionContext) -> str:
        """Model name for context-WINDOW math.

        When a compaction-model override is active
        (:func:`resolve_compaction_model`), token/window math follows the
        OVERRIDE model's context window — thresholds, chunking, and
        summary sizing all scale to the model that will actually serve
        the summarization calls. ``context_window_overrides`` /
        ``context_window_default`` apply to that name exactly as they
        did for the session model (see :func:`get_model_context_limit`).
        Unset override → the session model (``context.model_name``),
        byte-identical with the pre-setting behavior.
        """
        return resolve_compaction_model(context.config) or context.model_name

    def _trigger_window_for_model(self, model_name: str, config: "CompactionConfig") -> int:
        """Context window for TRIGGER math, from a bare model name.

        P1b extraction (proactive-compaction-fix ADDENDUM A.4): the 95%
        pre-call hook (``daemon/graph.py::_maybe_precall_compact_95``)
        needs the SAME W1 gating — ``min(session_window, override_window)``
        — for its ``0.95 × window`` math WITHOUT building a full
        :class:`CompactionContext`. :meth:`_trigger_window` delegates here
        so the two trigger sites cannot drift apart. The one-shot
        override-overflow WARN stays keyed on ``self`` exactly as before
        (fires at whichever site reaches it first; both share ``self``).

        Args:
            model_name: Session model name (window lookup key).
            config: The :class:`CompactionConfig` driving the override
                resolution + window registry. Callers pass the SAME config
                they would have carried on the context (production callers
                pass ``compactor.config``; ``_trigger_window`` passes
                ``context.config``).

        Returns:
            The gated trigger window (see :meth:`_trigger_window`).
        """
        override_name = resolve_compaction_model(config)
        if not override_name:
            return get_model_context_limit(
                model_name, config
            )
        override_window = get_model_context_limit(
            override_name, config
        )
        session_window = get_model_context_limit(
            model_name, config
        )
        if override_window > session_window:
            if not getattr(self, "_w_overflow_warned", False):
                self._w_overflow_warned = True
                logger.warning(
                    "Compaction override '%s' window (%d) exceeds session "
                    "model '%s' window (%d). Auto-path threshold gated at "
                    "the session window; internal chunking/merge/condense "
                    "sizing still follow the override. Use /compact to "
                    "force-recover once the session model has overflowed.",
                    override_name,
                    override_window,
                    model_name,
                    session_window,
                )
            return session_window
        return override_window

    def _trigger_window(self, context: CompactionContext) -> int:
        """Context window for the AUTO-path threshold gate (:826-841).

        W1 (review fix): when a compaction-model override is active,
        gate at ``min(session_window, override_window)`` so a LARGER
        override window cannot push proactive compaction past session
        capacity (defeating CLE auto-recovery — force=False reactive
        compaction returns None on context-length error). Internal
        sizing (chunk batching, merge, condense — :1094, :1414)
        continues to follow the OVERRIDE window via
        ``_effective_model_name``; this helper is the TRIGGER side only.

        One-shot WARN per compactor instance when the override window
        exceeds the session window; the message states the gating
        consequence so operators can pre-empt the surprise. Fires at
        the gate site (not ``load_config``) so the operator sees BOTH
        windows in the same log line, and so it is testable without
        loading the daemon config.

        P1b: the body delegates to :meth:`_trigger_window_for_model`
        (single implementation shared with the 95% pre-call hook).
        """
        return self._trigger_window_for_model(
            context.model_name, context.config
        )

    async def compact_state(
        self,
        context: CompactionContext,
        force: bool = False,
    ) -> CompactionResult | None:
        """Compact conversation history if it exceeds context window threshold.

        Args:
            context: CompactionContext with messages and configuration.
            force: Phase 1 / WS-2 (architect §2 narrowed). When True, the
                THRESHOLD check (:765) is bypassed — that is the ONLY
                bypass. Min-messages (:751) and the 60s dedup (:724-726)
                stay in-engine and STILL APPLY under force. Never bypasses
                boundary groups (D2), D3 sentinel persistence, pairing
                guard, or terminal guard. Default ``False`` → automatic
                paths (proactive `instance_messaging.py:1179`, reactive
                `graph.py:3513`) byte-identical when callers do not pass
                the flag (S-7 anti-drift). ``forced`` is stamped on the
                result so callers can distinguish forced compactions.

        Returns:
            CompactionResult if compaction occurred, None if not needed.
            ``compaction_type`` ∈ ``{"summarization", "truncation",
            "partial_summary", "emergency_truncation"}``.
            ``failure_kind`` ∈ ``{None, "timeout", "error"}`` on the
            engine result (WS-3.4 binding).
        """
        # 1. Deduplication: skip if recently compacted.
        # Anti-refire stamp is written by the call site (see
        # ``daemon/services/_compaction_persist_seam.py``); the
        # engine itself returns stamped CompactionResults with
        # empty replacement_messages for the anti-refire skip paths
        # (all-injected / min_messages / threshold) so the per-
        # dispatch refire loop closes even when the engine cannot
        # do useful work. The dedup here is a hard 60s window —
        # honored by BOTH the proactive trigger and ``/compact``.
        if context.last_compacted_at and self._is_recently_compacted(context.last_compacted_at):
            logger.debug("Skipping compaction: recently compacted")
            return None

        # C3 / Phase 1 + injected-notes hoisting fix: Partition the
        # channel into the selectable pool and the preserved injected
        # set. ``context_kind`` messages (real [SYSTEM CONTEXT] blocks)
        # and UNANSWERED bare-flag operator notes MUST survive
        # compaction verbatim — deliberate user intent, not
        # summarizable history. ANSWERED bare notes (an AIMessage
        # exists at a later index) join the selectable pool and are
        # absorbed into the compacted span like regular history; they
        # are NOT hoisted. We filter once up-front and re-attach the
        # preserved set to the result below.
        selectable_messages, hoisted_injected, absorbed_notes = (
            _partition_injected_for_compaction(context.messages)
        )

        # Pre-compute the PRESERVED injected tokens — included in the
        # GATE NUMERATOR and the SELECTION BUDGET (honest trigger;
        # matches what the LLM sees) but NOT selectable for compaction
        # (they must survive; re-attach paths unchanged). This is L3 of
        # the proactive-compaction-fix root-cause stack: the prior gate
        # excluded injections from the numerator, so a long-
        # orchestrating instance accumulating injected child reports
        # NEVER crossed the threshold even at 800+ messages.
        # Answered notes moved OUT of this figure and INTO the
        # selectable pool — they now count as available relief instead
        # of permanent surviving occupancy.
        injected_tokens = (
            estimate_messages_tokens(hoisted_injected)
            if hoisted_injected else 0
        )

        # Helper: build the anti-refire stamp-only result so every
        # skip path closes the dedup window. Callers stamp the
        # ``compacted_at`` via the shared seam; the engine signals
        # the stamp via a CompactionResult with empty
        # replacement_messages. The proactive call site uses this to
        # engage the dedup at ``compaction.py:1771-1774`` on the next
        # dispatch.
        anti_refire_skip = (
            lambda *, skip_reason: CompactionResult(
                replacement_messages=[],
                tokens_before=int(
                    estimate_messages_tokens(selectable_messages)
                    + injected_tokens
                    + context.system_prompt_tokens
                ),
                tokens_after=int(
                    estimate_messages_tokens(selectable_messages)
                    + injected_tokens
                    + context.system_prompt_tokens
                ),
                tokens_saved=0,
                messages_before=len(context.messages),
                messages_after=len(context.messages),
                compaction_type=skip_reason,
                compacted_at=datetime.now(timezone.utc).isoformat(),
                injected_preserved=len(hoisted_injected),
                injected_absorbed=0,
            )
        )

        # If every message is a PERMANENT or UNANSWERED injection, there
        # is nothing to compact (the preserved injections will be left
        # in place by the unchanged conversation state). Answered bare
        # notes and regular history are selectable, so their presence
        # alone does NOT fire this skip. ANTI-REFIRE stamp engages the
        # dedup so the gate does not re-fire every dispatch — the
        # warning is rate-limited at the call site.
        if not selectable_messages:
            # Cycle 2 (review suggestion 4) — the
            # injection-dominated skip log is now WARN, matching
            # the 95% pre-call hook's skip-without-relief WARN
            # (``daemon/graph.py``). Operators triaging
            # "compaction never fires" alerts should see the
            # skip in the WARN stream (was INFO, invisible in
            # 6d of prod data prior to the L3 fix). The 95% site
            # still has its own rate-limited WARN with a
            # different message — this is a separate signal that
            # fires for all call sites (proactive + 95% +
            # /compact) so the WARN-level signal is uniform.
            logger.warning(
                "[Compaction] skipping: every message carries the "
                "injected_message flag and none are answered "
                "(context_kind or unanswered bare notes; n=%d, "
                "injected_tokens=%d); anti-refire stamp engaged",
                len(context.messages),
                injected_tokens,
            )
            return anti_refire_skip(
                skip_reason="skipped_injections_dominate"
            )

        # 2. Eligibility: minimum messages check (against the SELECTABLE
        # subset — regular history plus answered notes — so a
        # preserved-injection-heavy conversation doesn't get spuriously
        # compacted away). ANTI-REFIRE stamp engages the dedup so the
        # gate does not re-fire every dispatch.
        if len(selectable_messages) < context.config.min_messages_before_compaction:
            logger.warning(
                "[Compaction] skipping: %d selectable messages "
                "(minimum: %d, preserved_injected=%d); anti-refire stamp engaged",
                len(selectable_messages),
                context.config.min_messages_before_compaction,
                len(hoisted_injected),
            )
            return anti_refire_skip(
                skip_reason="skipped_below_min_messages"
            )

        # 3. Token calculation — NUMERATOR includes the preserved
        # injected tokens (L3 fix; matches what the LLM sees — the FE
        # badge at ``_compute_context_usage`` already counts all
        # messages). Answered notes are inside the selectable estimate,
        # so the unified numerator is unchanged in total.
        history_tokens = (
            estimate_messages_tokens(selectable_messages)
            + injected_tokens
        )
        total_tokens = history_tokens + context.system_prompt_tokens

        # 4. Context window and threshold check.
        # Phase 1 / WS-2: ``force=True`` bypasses THIS check ONLY (architect
        # §2 narrowed from the broader dedup+min-messages+threshold form).
        # Min-messages (:751 above) and the 60s dedup (:724-726 above)
        # stay in-engine and STILL APPLY under force. Auto paths do not
        # pass ``force`` so their threshold check is unchanged when
        # ``force=False`` (S-7 byte-identity anti-drift).
        # W1 (review fix): gate the AUTO-path threshold at the SMALLER
        # of session vs override window — see :meth:`_trigger_window`.
        # The threshold check below uses that gated value; the engine's
        # INTERNAL sizing (:1094 chunking, :1414 merge/condense) keeps
        # following the OVERRIDE window via ``_effective_model_name``.
        context_window = self._trigger_window(context)
        threshold_tokens = int(context_window * context.config.threshold)
        if not force and total_tokens <= threshold_tokens:
            logger.debug(
                f"Skipping compaction: {total_tokens} tokens "
                f"<= threshold {threshold_tokens}"
            )
            return None

        # Threshold crossed — log at INFO so the operator sees the
        # gate fire in prod (was DEBUG; invisible in 6d of prod data).
        # Rate-limited WARN fires at ≥90% of threshold from the call
        # site (instance_messaging._maybe_compact_context).
        logger.info(
            f"Compaction triggered: {total_tokens} tokens "
            f"(threshold: {threshold_tokens}, "
            f"force={force}, selectable={len(selectable_messages)}, "
            f"injected_preserved={len(hoisted_injected)}, "
            f"injected_absorbed={len(absorbed_notes)}, "
            f"injected_tokens={injected_tokens})"
        )

        # 5. Boundary groups (selectable messages only — regular history
        # plus answered bare notes)
        groups = identify_boundary_groups(selectable_messages)

        # 6. Select compactable vs preserved. Pass injected tokens so
        # the budget math (`preserved_tokens <= threshold`) reflects
        # the real surviving occupancy — L3 honesty at the budget
        # side too. Without this, an injection-heavy conversation
        # could compact all regular groups and STILL exceed the
        # threshold (because the injections survive un-reduced),
        # producing a per-dispatch refire loop. The selection math
        # now bails out cleanly when the regular-pool cannot reach
        # target.
        compactable, preserved, actual_window = select_compactable_groups(
            groups,
            context.config.recent_message_window,
            context.config.min_recent_window,
            context_window,
            context.system_prompt_tokens,
            estimate_messages_tokens,
            config_threshold=context.config.threshold,
            injected_tokens=injected_tokens,
        )

        timestamp = datetime.now(timezone.utc).isoformat()

        # Architect §6.3 — stamp the context with the values the
        # bounded best-effort GLOBAL on the truncation path needs.
        # Idempotent: production callers may pre-stamp; the engine
        # overwrites with its own anchors.
        context.tokens_before_total = total_tokens
        context.compacted_at_iso = timestamp

        if not compactable:
            # Emergency path: even preserved groups exceed threshold
            preserved_msgs = [msg for g in preserved for msg in g.messages]
            preserved_tokens = (
                estimate_messages_tokens(preserved_msgs)
                + injected_tokens  # L3 honesty: surviving injected
                + context.system_prompt_tokens
            )

            # ANTI-REFIRE stamp engages the dedup so the emergency
            # bail does not re-fire every dispatch. The engine
            # stamps CompactionResult with empty replacement_messages
            # for the proactive site to persist via the shared seam.
            if preserved_tokens <= context_window * context.config.threshold:
                logger.info(
                    "[Compaction] skipping: preserved (%d) + "
                    "injected_tokens=%d + system=%d still within "
                    "threshold; anti-refire stamp engaged",
                    estimate_messages_tokens(preserved_msgs),
                    injected_tokens,
                    context.system_prompt_tokens,
                )
                return CompactionResult(
                    replacement_messages=[],
                    tokens_before=total_tokens,
                    tokens_after=preserved_tokens,
                    tokens_saved=0,
                    messages_before=len(context.messages),
                    messages_after=len(context.messages),
                    compaction_type="skipped_preserved_within_threshold",
                    compacted_at=datetime.now(timezone.utc).isoformat(),
                    injected_preserved=len(hoisted_injected),
                    injected_absorbed=0,
                )

            logger.warning(
                f"Emergency truncation: {preserved_tokens} tokens exceed threshold "
                f"with only {len(preserved)} preserved groups"
            )

            truncated_msgs = emergency_truncate(
                preserved_msgs,
                max_tokens=int(context_window * context.config.target_ratio),
                estimate_fn=estimate_messages_tokens,
            )

            # W6: Assign new IDs to truncated messages to avoid conflict with RemoveMessage
            for truncated_msg in truncated_msgs:
                if hasattr(truncated_msg, 'id') and truncated_msg.id:
                    truncated_msg.id = f"truncated-{uuid.uuid4()}"

            replacement = []
            for group in groups:
                for msg in group.messages:
                    if msg.id:
                        replacement.append(RemoveMessage(id=msg.id))
            replacement.extend(truncated_msgs)
            # C3: re-attach preserved injected messages verbatim at the
            # end so they survive emergency truncation. They were never
            # in the selectable pool so no RemoveMessage applies.
            # Answered notes ARE in the groups — they are absorbed by
            # the truncation (re-id'd truncated-*), which is the
            # contract for answered notes.
            replacement.extend(hoisted_injected)

            non_removal = [m for m in replacement if not isinstance(m, RemoveMessage)]
            tokens_after = estimate_messages_tokens(non_removal) + context.system_prompt_tokens

            # B1 fix (2026-09-01) — engine-populated compacted_ids.
            # Emergency truncation RemoveMessages cover EVERY group
            # message (every original message in the pre-compaction
            # snapshot that was grouped). The truncated messages
            # have been re-id'd ("truncated-{uuid}") so they are
            # NOT in pre_ids; the snapshot ids that will be lost
            # under the sentinel are exactly the RemoveMessage
            # targets + the doc's own new id (which is allowed).
            emergency_compacted_ids = frozenset({
                getattr(msg, "id", None)
                for group in groups
                for msg in group.messages
                if getattr(msg, "id", None)
            })
            return CompactionResult(
                replacement_messages=replacement,
                tokens_before=total_tokens,
                tokens_after=tokens_after,
                tokens_saved=total_tokens - tokens_after,
                messages_before=len(context.messages),
                messages_after=len(non_removal),
                compaction_type="emergency_truncation",
                compacted_at=timestamp,
                compacted_ids=emergency_compacted_ids,
                # Answered notes are all inside the groups here (the
                # emergency path truncates the ENTIRE selectable pool),
                # so every one of them is absorbed.
                injected_preserved=len(hoisted_injected),
                injected_absorbed=len(absorbed_notes),
            )

        # 7. Summarization path.
        # Phase 1 / WS-3.4 (C1 hybrid — binding): branch on
        # ``outcome.summaries`` empty vs non-empty; identical semantics
        # for proactive and reactive callers (no per-caller branching —
        # WS-3.4 binding). Architect §4 / §6 — the engine emits a
        # SINGLE SystemMessage doc (the global compaction notice);
        # there is no per-batch SystemMessage and no separate
        # truncation marker. The doc is built by
        # :func:`build_compaction_doc`; the persist seam is the
        # :func:`build_sentinel_replacement` helper.
        #
        # §4/§6.2 — the engine hands the doc builder the
        # ``compactable`` groups via a context attribute so the
        # builder can derive ``total_sections`` (k/n) and the
        # ``dropped_spans`` list from the SAME batch-slicing
        # heuristic the chunker used. Tests that stub
        # ``_summarize_chunked`` still set the attribute so the
        # doc builder is honest about n.
        context._compactable_groups_for_doc = compactable
        # W1 fix (2026-09-01) — extract the prior doc's GLOBAL
        # OVERVIEW once per compaction and thread it into the
        # merge prompt + doc builder. ``None`` on the first
        # compaction of an instance.
        previous_overview = _extract_previous_overview(
            context.messages, _extract_instance_id(context)
        )
        # W2 fix (2026-09-01) — stamp the preserved-tail count
        # for the doc envelope header at every doc-builder call
        # site (today only ``_truncate_fallback`` stamps it; the
        # summarization and partial-summary paths printed
        # ``preserved verbatim: 0 most recent messages``).
        context._preserved_count_for_doc = sum(
            len(g.messages) for g in preserved
        )
        # F1 fix (2026-09-01) — derive the first-appearance
        # ``{msg_id: iso_ts}`` map once per ``compact_state`` call
        # and stamp it on the context so the doc builders see the
        # same value (architect §4 conversation-time clause).
        # The 4 ``CompactionContext`` construction sites (the 3
        # active callers — compact_executor, instance_messaging,
        # graph — and the watchover helper) all pre-populate
        # ``context.msg_timestamps``; this defensive fallback
        # recomputes from ``context.messages`` when the field is
        # ``None`` (legacy / in-test construction paths that did
        # not pre-stamp). Result is a single map per compaction
        # cycle regardless of which doc-builder branch fires.
        if not getattr(context, "msg_timestamps", None):
            context.msg_timestamps = _extract_msg_timestamps(context.messages)
        msg_timestamps = context.msg_timestamps
        failure_kind: str | None = None
        summarization_error: str | None = None
        global_overview: str | None = None
        total_summary_status: str | None = None
        try:
            outcome = await self._summarize_chunked(
                compactable, context,
                previous_overview=previous_overview,
            )
            summaries = outcome.summaries

            if not summaries:
                # |S| = 0 — all batches failed (single-batch timeout,
                # multi-batch first-batch timeout, or budget exhausted
                # before any batch succeeded). Existing
                # ``_truncate_fallback`` fires unchanged; the doc is
                # built with no GLOBAL OVERVIEW and a single dropped-
                # spans clause (architect §6.3 — bounded best-effort
                # GLOBAL on the |S|=0 path; hard fail-open when the
                # bounded call itself fails).
                replacement, compaction_type, total_summary_status = (
                    await self._truncate_fallback(
                        compactable, preserved, context,
                        instance_id=_extract_instance_id(context),
                        msg_timestamps=msg_timestamps,
                    )
                )
                # C3: re-attach the preserved injected messages verbatim
                # at the end so they survive truncation.
                replacement.extend(hoisted_injected)
                if outcome.stop_reason in ("timeout", "budget"):
                    failure_kind = "timeout"
                else:
                    failure_kind = "error"
            elif outcome.stop_reason == "completed":
                # All batches succeeded → single merged (or single-
                # batch) text. The doc builder emits ONE section
                # spanning the entire compactable span.
                if outcome.merge_failed:
                    # Architect §6.2 — merge pass failed; the
                    # per-batch strings become SECTIONS inside the
                    # doc (same path the partial-summary assembly
                    # takes). The GLOBAL OVERVIEW slot is filled
                    # by the bounded merge retry below (fail-open
                    # ladder: empty + status="failed" if the retry
                    # also fails).
                    # ``summaries`` in this branch is the per-batch
                    # survivor list with original batch-index order
                    # (the chunked function preserves it on the
                    # merge-failed path), so we don't need the
                    # ``batch_indices`` mapping here.
                    batch_sections = self._per_batch_section_meta(
                        compactable, summaries, context,
                    )
                    import time as _time
                    # W3 fix (2026-09-01) — anchor the merge
                    # budget to the REMAINING budget, NOT the
                    # placeholder ``operation_budget_s - 0.0``.
                    # The pool already spent some time; the merge
                    # must respect what is left.
                    budget_remaining = (
                        outcome.budget_remaining_after_pool
                        if outcome.budget_remaining_after_pool is not None
                        else float(context.config.operation_budget_s)
                    )
                    # W1 — thread the prior doc's GLOBAL OVERVIEW
                    # into the merge prompt so the new GLOBAL
                    # converges across passes.
                    merged, ok = await self._merge_summaries(
                        summaries, context,
                        budget_seconds=budget_remaining,
                        previous_overview=previous_overview,
                    )
                    if ok:
                        total_summary_status = "ok"
                        global_overview = merged
                    else:
                        total_summary_status = "failed"
                        global_overview = None
                    doc = self._build_global_doc_for_partial(
                        batch_sections, context,
                        instance_id=_extract_instance_id(context),
                        compacted_at=timestamp,
                        global_overview=global_overview,
                        effective_model_name=self._effective_model_name(context),
                        previous_overview=previous_overview,
                        msg_timestamps=msg_timestamps,
                    )
                else:
                    global_text = summaries[0]
                    total_summary_status = "ok"
                    global_overview = global_text
                    doc = self._build_global_doc_for_full_success(
                        compactable, global_text, context,
                        instance_id=_extract_instance_id(context),
                        compacted_at=timestamp,
                        effective_model_name=self._effective_model_name(context),
                        previous_overview=previous_overview,
                        msg_timestamps=msg_timestamps,
                    )
                # C3: re-attach the preserved injected messages at the
                # end of the replacement list. The doc is the FIRST
                # element so it lands in its proper position once the
                # sentinel recipe (build_sentinel_replacement)
                # re-orders.
                replacement = [doc]
                # Flatten preserved tail into the replacement list
                # (multimodal content → text; original ids preserved).
                for group in preserved:
                    for msg in group.messages:
                        if isinstance(msg.content, list):
                            msg.content = _extract_text_from_content(msg.content)
                        replacement.append(msg)
                # C3: re-attach the preserved injected messages at the end.
                replacement.extend(hoisted_injected)
                compaction_type = "summarization"
                failure_kind = None
            else:
                # Partial-summary path: |S| >= 1, stop_reason ∈
                # {"timeout", "budget"}. The surviving per-batch
                # strings become SECTIONS inside the doc; failed
                # batches become the dropped-spans clause. The
                # bounded merge pass produces a GLOBAL OVERVIEW when
                # it succeeds; on failure the placeholder line is
                # emitted instead (architect §6.2 fail-open ladder).
                # Pass the original batch indices so the section
                # metadata can land the right slice of
                # ``compactable`` for each surviving body (the
                # non-contiguous survivor case where batches 1, 3, 5
                # failed but 0, 2, 4 succeeded).
                batch_sections = self._per_batch_section_meta(
                    compactable, summaries, context,
                    batch_indices=outcome.completed_idxs,
                )
                # Try the merge pass for the GLOBAL OVERVIEW
                # (bounded by the independent cap; never deepens
                # partiality on failure).
                import time as _time
                # W3 fix (2026-09-01) — anchor the merge budget
                # to the REMAINING budget, NOT the placeholder
                # ``operation_budget_s - 0.0``.
                budget_remaining = (
                    outcome.budget_remaining_after_pool
                    if outcome.budget_remaining_after_pool is not None
                    else float(context.config.operation_budget_s)
                )
                # Bounded merge; on failure the doc still emits.
                # W1 — thread the prior doc's GLOBAL OVERVIEW into
                # the merge prompt for cross-pass convergence.
                merged, ok = await self._merge_summaries(
                    summaries, context,
                    budget_seconds=budget_remaining,
                    previous_overview=previous_overview,
                )
                if ok:
                    total_summary_status = "ok"
                    global_overview = merged
                else:
                    total_summary_status = "failed"
                    global_overview = None
                doc = self._build_global_doc_for_partial(
                    batch_sections, context,
                    instance_id=_extract_instance_id(context),
                    compacted_at=timestamp,
                    global_overview=global_overview,
                    effective_model_name=self._effective_model_name(context),
                    previous_overview=previous_overview,
                    msg_timestamps=msg_timestamps,
                )
                # C3: re-attach the preserved injected messages at the end.
                replacement = [doc]
                for group in preserved:
                    for msg in group.messages:
                        if isinstance(msg.content, list):
                            msg.content = _extract_text_from_content(msg.content)
                        replacement.append(msg)
                replacement.extend(hoisted_injected)
                compaction_type = "partial_summary"
                failure_kind = "timeout"

        except (TimeoutError, asyncio.TimeoutError) as e:
            # W-4.1 — merge/condense path can surface a
            # ``TimeoutError`` / ``asyncio.TimeoutError`` that the
            # inner per-chunk narrowing (O14) DOES NOT catch (those
            # exceptions live outside ``_summarize_chunked``). Without
            # this branch, the outer ``except Exception`` catches them
            # and the engine emits ``failure_kind="error"`` — masking
            # a real timeout as a generic error and misclassifying
            # the wire outcome (FE would see "failed" instead of
            # "timed_out → fallback_applied"). The truncate fallback
            # still applies (preserves the auto-path contract); only
            # the classification differs.
            logger.warning(
                "Summarization timed out (merge/condense path), falling "
                "back to truncation: %s",
                e,
            )
            replacement, compaction_type, total_summary_status = (
                await self._truncate_fallback(
                    compactable, preserved, context,
                    instance_id=_extract_instance_id(context),
                    msg_timestamps=msg_timestamps,
                )
            )
            # C3: same re-attach on the truncation fallback path.
            replacement.extend(hoisted_injected)
            failure_kind = "timeout"
            summarization_error = f"{type(e).__name__}: {e}"
        except Exception as e:
            # Non-timeout exceptions from ``_summarize_chunked`` (O14
            # narrowed per-chunk except) or from merge/condense surface
            # here. ``_truncate_fallback`` applies.
            logger.warning(f"Summarization failed, falling back to truncation: {e}")
            replacement, compaction_type, total_summary_status = (
                await self._truncate_fallback(
                    compactable, preserved, context,
                    instance_id=_extract_instance_id(context),
                    msg_timestamps=msg_timestamps,
                )
            )
            # C3: same re-attach on the truncation fallback path.
            replacement.extend(hoisted_injected)
            failure_kind = "error"
            summarization_error = str(e)

        # 8. Build result — covers summarization, partial_summary, and
        # truncation fallback. ``compacted_at`` is stamped on every
        # branch above (D12 — a partial is a completed compaction, not
        # a failure).
        non_removal = [m for m in replacement if not isinstance(m, RemoveMessage)]
        tokens_after = estimate_messages_tokens(non_removal) + context.system_prompt_tokens

        # B1 fix (2026-09-01) — engine-populated compacted_ids.
        # The compactable span (every group message id that the
        # engine intends to drop / replace with the doc) is the
        # authoritative "removed" set for the persist-seam sites.
        # Preserved injected messages and the preserved tail are KEPT,
        # NOT removed, so they are NOT in this set. Answered notes that
        # joined the selectable pool ARE absorbed via this set when
        # their group was summarized. The doc itself is
        # a NEW id (allowed to be absent from the snapshot).
        compacted_span_ids = frozenset({
            getattr(msg, "id", None)
            for group in compactable
            for msg in group.messages
            if getattr(msg, "id", None)
        })

        # Injected-notes hoisting observability — the completion line and
        # the card envelope distinguish the preserved injections
        # (``context_kind`` blocks + UNANSWERED bare notes — hoisted
        # verbatim) from the absorbed ones (ANSWERED bare notes that
        # joined the selectable pool and were consumed by the compacted
        # span this pass). A preserved-tail-resident answered note is in
        # NEITHER count (it stayed verbatim inline; it was not hoisted
        # and not summarized).
        absorbed_in_span = sum(
            1
            for note in absorbed_notes
            if getattr(note, "id", None) in compacted_span_ids
        )
        logger.info(
            f"Compaction complete: {total_tokens} -> {tokens_after} tokens "
            f"(saved {total_tokens - tokens_after}), type={compaction_type}, "
            f"forced={force}, failure_kind={failure_kind}, "
            f"injected_preserved={len(hoisted_injected)}, "
            f"injected_absorbed={absorbed_in_span}, "
            f"total_summary_status={total_summary_status}"
        )

        result_kwargs: dict = dict(
            replacement_messages=replacement,
            tokens_before=total_tokens,
            tokens_after=tokens_after,
            tokens_saved=total_tokens - tokens_after,
            messages_before=len(context.messages),
            messages_after=len(non_removal),
            compaction_type=compaction_type,
            compacted_at=timestamp,
            forced=force,
            failure_kind=failure_kind,
            total_summary_status=total_summary_status,
            global_overview=global_overview,
            compacted_ids=compacted_span_ids,
            injected_preserved=len(hoisted_injected),
            injected_absorbed=absorbed_in_span,
        )
        if summarization_error:
            result_kwargs["summarization_error"] = summarization_error

        # Coordination note (2026-09-01, FE) — stamp
        # ``sections_kept`` / ``sections_total`` on the result so
        # the executor's wire map can thread them into the
        # command-progress detail payload. These are read by the FE
        # DEFENSIVELY — absent or different name → silent fallback
        # to the prior card copy (no fabrication).
        if compaction_type in ("summarization", "partial_summary"):
            section_counts = _compute_section_counts(result_kwargs)
            result_kwargs["sections_kept"] = section_counts[0]
            result_kwargs["sections_total"] = section_counts[1]

        return CompactionResult(**result_kwargs)
    
    async def _summarize_chunked(
        self,
        compactable_groups: list[MessageGroup],
        context: CompactionContext,
        previous_overview: str | None = None,
    ) -> "ChunkedOutcome":
        """Summarize compactable groups, chunking if necessary.

        Phase 1 / WS-3.4 (C1 hybrid): returns a ``ChunkedOutcome`` instead
        of raising on per-chunk failure. The outer handler at
        ``compact_state`` :744-772 branches on ``summaries`` empty vs
        non-empty — identical semantics for proactive (WS-3.5 instance_messaging.py:1179)
        and reactive (WS-3.5 graph.py:3513) callers by construction.

        Per-batch try/except is narrowed to
        ``(TimeoutError, asyncio.TimeoutError)`` (O14) INSIDE each pool
        task; other exceptions are parked by the gather
        (``return_exceptions=True``) and re-raised once the pool joins —
        they propagate to the outer ``except Exception`` (compact_state
        :744-772), which maps them to the existing truncate fallback and
        emits ``failure_kind="error"`` on the engine result.

        Whole-operation budget ``context.config.operation_budget_s``:
        shared wall-clock deadline (``asyncio.wait_for`` around the
        batch-pool gather). The deadline lives entirely inside
        ``_summarize_chunked`` — never between the two
        ``aupdate_state`` persistence calls in callers (D-B5/D-B6 —
        torn-write guard, that lives upstream). Expiry cancels in-flight
        and un-started batch tasks, records ``stop_reason="budget"``,
        and the engine returns with whatever summaries had completed;
        the outer handler decides the path.

        Parallelism (bounded pool): batches are INDEPENDENT — the
        per-batch prompt is a static template over that batch's groups
        only, and ``_merge_summaries`` consumes results strictly after
        the pool. ``chunk_concurrency`` (default 3) bounds in-flight
        calls via ``asyncio.Semaphore``; results are reassembled by
        task-list index (``asyncio.gather`` preserves input order —
        NEVER ``as_completed``), which is the chronological invariant
        ``_build_partial_replacement_messages`` relies on. The existing
        per-prompt adaptive timeout (``_summarization_timeout_s``)
        applies per task and composes with the pool as the per-batch
        failure boundary.

        Args:
            compactable_groups: Groups to summarize.
            context: Compaction context with configuration.

        Returns:
            ``ChunkedOutcome(summaries, failed_batches, stop_reason)``.
            ``stop_reason`` ∈ ``{"completed","timeout","error","budget"}``.
        """
        compactable_messages = [msg for g in compactable_groups for msg in g.messages]
        compactable_tokens = estimate_messages_tokens(compactable_messages)
        context_window = get_model_context_limit(
            self._effective_model_name(context), context.config
        )
        threshold_tokens = context_window * context.config.summarization_chunk_threshold

        # Whole-operation budget wall-clock anchor — measured against the
        # pool gather ONLY (``asyncio.wait_for(pool, ...)`` at ~:1218).
        # The budget does NOT wrap merge/condense — those run AFTER the
        # deadline has fired, serially, as part of the same
        # ``_summarize_chunked`` call frame but with no wall-clock cap
        # of their own. Parallel merge is intentionally deferred to a
        # future soak (Phase-1 design: chunking parallel; post-pool
        # serial). Module-level ``time.monotonic`` is monotonic across
        # the event loop and unaffected by wall-clock skew.
        import time as _time
        budget_started_at = _time.monotonic()
        budget_seconds = float(context.config.operation_budget_s)

        def _budget_remaining() -> float:
            return budget_seconds - (_time.monotonic() - budget_started_at)

        # Single batch if small enough
        if compactable_tokens <= threshold_tokens:
            try:
                summary = await self._summarize_single_batch(
                    compactable_groups, context
                )
                return ChunkedOutcome(
                    summaries=[summary],
                    failed_batches=[],
                    stop_reason="completed",
                    # No pool — outer merge falls back to the
                    # full operation_budget_s (the budget_remaining
                    # sentinel is None; caller substitutes the
                    # full budget).
                    budget_remaining_after_pool=None,
                )
            except (TimeoutError, asyncio.TimeoutError):
                # O14-narrowed per-chunk timeout. Outer handler maps
                # empty-summaries → truncate fallback (compaction_type
                # "truncation" + marker).
                logger.warning(
                    "Single-batch summarization timed out within "
                    "context.config.operation_budget_s=%ss",
                    context.config.operation_budget_s,
                )
                return ChunkedOutcome(
                    summaries=[],
                    failed_batches=[0],
                    stop_reason="timeout",
                    budget_remaining_after_pool=None,
                )

        # Chunk into batches of 20 groups
        batch_size = 20
        batches: list[list[MessageGroup]] = []
        for i in range(0, len(compactable_groups), batch_size):
            batch_groups = compactable_groups[i:i + batch_size]
            batch_msgs = [msg for g in batch_groups for msg in g.messages]
            batch_tokens = estimate_messages_tokens(batch_msgs)

            # Truncate batch if still too large
            if batch_tokens > threshold_tokens:
                batch_groups = _truncate_batch_to_fit(
                    batch_groups,
                    int(threshold_tokens),
                    estimate_messages_tokens,
                )
            batches.append(batch_groups)

        # Summarize batches in a bounded parallel pool. Batches are
        # INDEPENDENT: the per-batch prompt is a static template over
        # that batch's groups only (``_summarize_single_batch``) and
        # ``_merge_summaries`` consumes the results strictly AFTER the
        # pool completes — nothing inside the pool reads a prior
        # batch's output. Results are reassembled BY TASK-LIST INDEX:
        # ``asyncio.gather`` preserves input order, which IS the
        # chronological invariant ``_build_partial_replacement_messages``
        # relies on. NEVER ``as_completed`` here.
        #
        # FailoverController race note (parallel-429 review): every
        # batch call constructs its own ``ThinkingChatOpenAI`` +
        # ``wrap_langchain_failover`` wrapper inside
        # ``_call_summarization_llm`` — a fresh ``FailoverController``
        # and a fresh openai client per call — so concurrent
        # 429-driven ``swap_to_backup`` / ``reset_to_primary``
        # mutations never share mutable state across batches. No
        # cross-batch race by construction; no lock needed.
        chunk_concurrency = max(1, int(context.config.chunk_concurrency))
        semaphore = asyncio.Semaphore(chunk_concurrency)

        # Slot i of each structure is batch i. ``summaries_by_idx`` holds
        # completed summaries (``None`` = not completed); ``started``
        # flags batches whose task ACQUIRED a pool slot (i.e. actually
        # began its LLM call). Observability contract for
        # ``failed_batches`` (a member = that batch did not complete):
        #   - "skipped": the task never acquired the semaphore before
        #     the shared deadline cancelled the pool (never started);
        #   - "failed": the task started and then hit its own per-batch
        #     adaptive timeout, or was cancelled in-flight by the
        #     deadline.
        summaries_by_idx: list = [None] * len(batches)
        started = [False] * len(batches)
        timed_out_batches: set = set()

        async def _run_batch(batch_idx: int, batch: list[MessageGroup]) -> None:
            # Wait for a pool slot OUTSIDE the try: a cancellation while
            # waiting means this batch never started, and a ``finally``
            # release here would free a slot we never held.
            await semaphore.acquire()
            started[batch_idx] = True
            try:
                summaries_by_idx[batch_idx] = await self._summarize_single_batch(
                    batch, context
                )
            except (TimeoutError, asyncio.TimeoutError):
                # O14-narrowed per-batch timeout: this batch failed on
                # its OWN adaptive cap, not the shared deadline (a
                # deadline hit surfaces as gather cancellation, never as
                # an exception here). Record it and let siblings finish.
                timed_out_batches.add(batch_idx)
                logger.warning(
                    "Batch %d/%d summarization timed out on its own "
                    "adaptive cap; continuing remaining batches.",
                    batch_idx + 1, len(batches),
                )
            finally:
                semaphore.release()

        pool = asyncio.gather(
            *(_run_batch(i, b) for i, b in enumerate(batches)),
            return_exceptions=True,
        )
        try:
            results = await asyncio.wait_for(pool, timeout=_budget_remaining())
        except (TimeoutError, asyncio.TimeoutError):
            # Shared budget deadline (D-B5/D-B6 preserved): the deadline
            # lives entirely inside ``_summarize_chunked`` — ``wait_for``
            # cancels the gather (cancelling every un-started and
            # in-flight batch task, and awaiting that cancellation)
            # BEFORE this handler runs, so no caller-side
            # ``aupdate_state`` is ever interleaved with a live pool.
            # Completed summaries are kept below. CancelledError is
            # never swallowed here — it is consumed by ``wait_for``
            # itself; no ``except BaseException`` exists in this file.
            logger.warning(
                "Operation budget deadline hit with %d/%d batch summaries "
                "complete (%d in-flight cancelled, %d never started); "
                "keeping completed summaries.",
                sum(1 for s in summaries_by_idx if s is not None),
                len(batches),
                sum(
                    1 for i in range(len(batches))
                    if summaries_by_idx[i] is None and started[i]
                ),
                sum(
                    1 for i in range(len(batches))
                    if summaries_by_idx[i] is None and not started[i]
                ),
            )
            stop_reason = "budget"
        else:
            # Deadline did NOT fire. ``return_exceptions=True`` parks any
            # non-timeout batch exception in the results — re-raise the
            # first one so the outer ``except Exception`` (compact_state)
            # maps it to the truncate fallback with
            # ``failure_kind="error"`` (O14: only timeouts are handled
            # per-batch; everything else propagates).
            for res in results:
                if isinstance(res, BaseException):
                    raise res
            stop_reason = "timeout" if timed_out_batches else "completed"

        # Completion set in batch-index order (non-contiguous survival:
        # every COMPLETED batch's summary is kept; each incomplete
        # batch's messages are dropped individually downstream —
        # ``_build_partial_replacement_messages`` RemoveMessages ALL
        # compactable groups, then re-adds the surviving summaries, the
        # marker, and the preserved tail).
        partial_summaries = [s for s in summaries_by_idx if s is not None]
        completed_idxs_set = {
            i for i, s in enumerate(summaries_by_idx) if s is not None
        }
        completed_idxs = sorted(completed_idxs_set)  # original batch index per survivor
        failed_batches = sorted(set(range(len(batches))) - completed_idxs_set)

        # If we ran out of time before any batch succeeded, surface
        # "timeout" — even if some partials are present. Outer handler
        # ignores this string on the |S|>=1 path; only ``summaries``
        # drives the partial-vs-truncate branching.
        # W3 fix (2026-09-01) — compute the wall-clock REMAINING
        # budget once and thread it into every ChunkedOutcome
        # return path so the outer merge call uses the SAME value
        # the inner merge call uses (per architect §6.2 — the
        # merge is bounded by the remaining operation_budget_s).
        budget_remaining = max(
            0.0,
            budget_seconds - (_time.monotonic() - budget_started_at)
        )
        if partial_summaries and stop_reason == "completed":
            # All batches succeeded → merge if multiple. The merge
            # pass uses the same model selection as chunk summarization
            # (architect §6.7) and gets an independent budget derived
            # from the remaining operation_budget_s.
            if len(partial_summaries) == 1:
                return ChunkedOutcome(
                    summaries=partial_summaries,
                    failed_batches=[],
                    stop_reason="completed",
                    budget_remaining_after_pool=budget_remaining,
                )
            merged, ok = await self._merge_summaries(
                partial_summaries, context,
                budget_seconds=budget_remaining,
                previous_overview=previous_overview,
            )
            if not ok:
                # Merge pass failed; per §6.2 fail-open, we still
                # return the surviving per-batch strings (the doc
                # builder emits the placeholder GLOBAL, sections
                # intact, ``total_summary_status="failed"``).
                logger.warning(
                    "merge pass failed on full-success path; "
                    "emitting per-batch sections + placeholder GLOBAL"
                )
                return ChunkedOutcome(
                    summaries=partial_summaries,
                    failed_batches=[],
                    stop_reason="completed",
                    merge_failed=True,
                    budget_remaining_after_pool=budget_remaining,
                )
            # When the merge succeeded, replace the per-batch list
            # with a SINGLE-element list: the GLOBAL. The downstream
            # doc builder on the FULL path emits a single section
            # spanning the whole compactable span and uses the
            # merged text as the GLOBAL OVERVIEW (no extra per-
            # batch sections on a full success).
            return ChunkedOutcome(
                summaries=[merged],
                failed_batches=[],
                stop_reason="completed",
                budget_remaining_after_pool=budget_remaining,
            )

        # Partial-summary path (|S| >= 1, with stop_reason ∈
        # {"timeout", "budget"}) OR all-batches-failed path (|S| = 0).
        # Do NOT call _merge_summaries here — that's the explicit
        # rule for the partial path (architect §3 Correction 2 + C1):
        # only successful batches are summarized, and the preserved
        # tail + injected messages take over from B's raw span.
        # Capture the original batch indices of the survivors so
        # the partial-path assembly can land the right slice of
        # ``compactable`` for each body (the non-contiguous case).
        return ChunkedOutcome(
            summaries=partial_summaries,
            failed_batches=failed_batches,
            stop_reason=stop_reason,
            completed_idxs=completed_idxs,
            budget_remaining_after_pool=budget_remaining,
        )
    
    async def _summarize_single_batch(
        self,
        batch_groups: list[MessageGroup],
        context: CompactionContext
    ) -> str:
        """Summarize a single batch of message groups.

        Architect §4 / W3 — the returned content is a plain string with
        NO ``Timestamp:`` line, NO ``[Conversation Summary]`` wrapper.
        The single timestamp lives in the envelope header
        (``compacted_at``); the per-section text is arc-local detail
        only (the W5 fix that re-scopes section prompts to facts and
        tool outcomes rather than the previous global re-derivation).

        Args:
            batch_groups: Groups to summarize.
            context: Compaction context.

        Returns:
            Plain-text section body (str), ready to embed in the
            single ``compaction-global-`` doc.
        """
        # Format messages into readable conversation text
        conversation_parts: list[str] = []
        for group in batch_groups:
            for msg in group.messages:
                msg_type = getattr(msg, "type", "unknown")
                content = _extract_text_from_content(msg.content)
                if msg_type == "human":
                    conversation_parts.append(f"User: {content}")
                elif msg_type == "ai":
                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        tool_names = []
                        for tc in msg.tool_calls:
                            if isinstance(tc, dict):
                                name = tc.get("name", "?")
                            else:
                                name = getattr(tc, "name", "?")
                            tool_names.append(name)
                        content += f" [Called tools: {', '.join(tool_names)}]"
                    conversation_parts.append(f"Assistant: {content}")
                elif msg_type == "tool":
                    tool_name = getattr(msg, "name", "unknown")
                    conversation_parts.append(f"Tool ({tool_name}): {content}")
                else:
                    conversation_parts.append(f"{msg_type}: {content}")

        conversation_text = "\n".join(conversation_parts)

        # Re-scoped to arc-local detail (architect §4 — W5 fix: global
        # context is in GLOBAL OVERVIEW, not duplicated in every
        # section; cross-references allowed via the model).
        prompt = (
            "Summarize the following conversation segment in a self-contained way. "
            "Focus on arc-local detail only:\n"
            "- Concrete decisions made in this segment\n"
            "- Specific tool calls and their outcomes (with names + key return values)\n"
            "- Verbatim quotes that anchor the segment's intent\n"
            "- Outcomes that resolve prior open items\n\n"
            "Do NOT re-derive global context (entities, goals, prior history) — "
            "that lives in the GLOBAL OVERVIEW. Cross-reference the GLOBAL when "
            "useful ('see GLOBAL: project X → feature Y') but do not repeat it.\n\n"
            "Be concise. Aim for ~200-400 tokens.\n\n"
            f"Conversation:\n{conversation_text}"
        )

        return await self._call_summarization_llm(prompt, context)

    async def _merge_summaries(
        self,
        partial_summaries: list[str],
        context: CompactionContext,
        budget_seconds: float | None = None,
        previous_overview: str | None = None,
    ) -> tuple[str, bool]:
        """Merge multiple per-batch summaries into one GLOBAL OVERVIEW.

        Architect §6.2 — generalized to partial sets (the old
        full-success-only path is preserved but the function is now
        callable on any non-empty partial set). Returns a
        ``(content, ok)`` tuple: the merged content (or empty string
        on failure) and a boolean ``ok`` flag for the fail-open
        ladder.

        W1 fix (2026-09-01) — pass-2 convergence. ``previous_overview``
        is the prior doc's GLOBAL OVERVIEW text (from
        :func:`_extract_previous_overview`); when provided, it is
        threaded into the merge prompt as the "Previous overview:"
        seed so the GLOBAL frame CONVERGES across passes instead of
        being re-derived from scratch (architect §4).

        Independent budget (architect §6.2):
        ``min(inner_cap, 25% of remaining compaction deadline)``,
        excluded from the batch-pool deadline, **one retry max**.

        Failure ladder (fail-open, never deepens partiality):

        * merge OK → ``(content, True)``
        * merge fail/timeout → ``("", False)`` — caller emits the
          placeholder GLOBAL line in the doc, sections intact,
          ``total_summary_status="failed"``,
          ``compaction_type`` unchanged.

        Args:
            partial_summaries: Per-batch section bodies (plain
                strings, no timestamps). May be the FULL set
                (``stop_reason="completed"``) or a partial survivor
                set (the §6.2 generalization). When length is 1, the
                single body is returned verbatim (no LLM call).
            context: Compaction context.
            budget_seconds: Outer budget anchor (the
                ``operation_budget_s`` remaining at the time the
                pool finished). When provided, the merge is
                bounded by ``min(inner_cap, 0.25 * budget_seconds)``;
                when ``None``, the merge falls back to the
                ``timeout_base_s`` adaptive cap.
            previous_overview: When non-empty, the prior doc's
                GLOBAL OVERVIEW text. Threaded into the merge
                prompt so the new GLOBAL CONVERGES with the prior
                pass. ``None`` for the first compaction of an
                instance.

        Returns:
            ``(merged_text, ok)`` — ``merged_text`` is the GLOBAL
            OVERVIEW text (or empty string on failure); ``ok`` is
            ``False`` only when the merge call timed out / errored
            after the single retry.
        """
        if not partial_summaries:
            return "", False
        if len(partial_summaries) == 1:
            # W1 — single-section pass still threads the seed so
            # the merge "prompt" (here a no-op) does not lose the
            # pass-2 convergence contract. When the prior doc
            # exists, the new doc carries its GLOBAL body verbatim
            # (no merge LLM call needed; the seed IS the GLOBAL).
            if previous_overview:
                # Caller wraps this in the doc; we just signal ok
                # so the doc builder picks up the seed from the
                # context (already set by compact_state). Return
                # empty so the caller does NOT set
                # ``global_overview = merged`` and double-print.
                return "", True
            return partial_summaries[0], True

        # Direct merge for 2-3 summaries
        if len(partial_summaries) <= 3:
            return await self._merge_one_round(
                partial_summaries, context, budget_seconds,
                previous_overview=previous_overview,
            )

        # Hierarchical pairwise merge for 4+ summaries (single LLM
        # call per round; we collapse rounds until ≤3 are left, then
        # do the final direct merge).
        current = list(partial_summaries)
        last_error: str | None = None
        while len(current) > 3:
            next_round: list[str] = []
            for i in range(0, len(current), 2):
                pair = current[i:i + 2]
                if len(pair) == 2:
                    merged, ok = await self._merge_one_round(
                        pair, context, budget_seconds,
                        previous_overview=previous_overview,
                    )
                    if not ok:
                        # Fail-open: return empty so the caller emits
                        # the placeholder GLOBAL.
                        return "", False
                    next_round.append(merged)
                else:
                    next_round.append(pair[0])
            current = next_round
            if last_error is None and len(current) > 3:
                last_error = "hierarchical-round-failure"  # diagnostic

        final, ok = await self._merge_one_round(
            current, context, budget_seconds,
            previous_overview=previous_overview,
        )
        if not ok:
            return "", False
        return final, True

    async def _merge_one_round(
        self,
        partial_summaries: list[str],
        context: CompactionContext,
        budget_seconds: float | None,
        previous_overview: str | None = None,
    ) -> tuple[str, bool]:
        """Single LLM call that merges a list of section bodies.

        Helper for :meth:`_merge_summaries`. Implements the
        architect §6.2 fail-open ladder: bounded by the independent
        cap, one retry on transient failure, returns ``("", False)``
        on second failure.

        W1 fix (2026-09-01) — when ``previous_overview`` is
        provided, the merge prompt is extended with a
        ``Previous overview: …`` block so the model converges the
        new GLOBAL with the prior pass instead of re-deriving from
        scratch.

        Returns:
            ``(content, ok)`` — ``content`` is the merged text (no
            ``[Conversation Summary]`` wrapper, no ``Timestamp:``,
            no ``compaction-merge-`` id — those are obsolete in the
            §4 design). ``ok`` is ``False`` on timeout / error after
            retry.
        """
        combined = "\n\n---\n\n".join(
            f"Part {i+1}:\n{s}" for i, s in enumerate(partial_summaries)
        )
        # W1 — prior GLOBAL is threaded into the merge prompt so
        # the model converges across passes. The doc builder also
        # prints the same seed verbatim below the GLOBAL header.
        previous_overview_block = ""
        if previous_overview:
            previous_overview_block = (
                f"Previous overview (treat as authoritative frame; "
                f"merge in any updates; do not lose entities/goals/"
                f"decisions carried here):\n{previous_overview}\n\n"
            )
        merge_prompt = (
            "Combine these conversation segment summaries into a single, "
            "coherent GLOBAL OVERVIEW of the conversation arc. Preserve:\n"
            "- Top-level entities, goals, and decisions\n"
            "- Cross-cutting facts that span multiple segments\n"
            "- Open threads that the conversation has not yet resolved\n\n"
            "Remove redundancy, deduplicate, and keep the merged text under "
            "about 600 tokens. Do NOT include a timestamp or a header line — "
            "this is the body of the GLOBAL OVERVIEW section.\n\n"
            f"{previous_overview_block}"
            f"Sections:\n{combined}"
        )

        # Architect §6.2 — independent budget, excluded from the
        # batch-pool deadline, one retry max.
        from .config import CompactionConfig  # local import for type narrowing
        cfg: CompactionConfig = context.config
        if budget_seconds is not None:
            adaptive_cap = _summarization_timeout_s(merge_prompt, cfg)
            cap = min(adaptive_cap, max(1.0, 0.25 * float(budget_seconds)))
        else:
            cap = _summarization_timeout_s(merge_prompt, cfg)

        async def _do_merge() -> str:
            return await self._call_summarization_llm(merge_prompt, context)

        for attempt in (1, 2):  # at most one retry
            try:
                return await asyncio.wait_for(_do_merge(), timeout=cap), True
            except (TimeoutError, asyncio.TimeoutError) as e:
                logger.warning(
                    "merge pass attempt %d/2 timed out: %s", attempt, e
                )
                if attempt == 2:
                    return "", False
            except Exception as e:
                # Non-timeout error: also fail-open after one retry.
                logger.warning(
                    "merge pass attempt %d/2 failed: %s", attempt, e
                )
                if attempt == 2:
                    return "", False
        return "", False  # unreachable; the loop always returns.


    async def _call_summarization_llm(
        self,
        prompt: str,
        context: CompactionContext
    ) -> str:
        """Call LLM for summarization.

        Args:
            prompt: Summarization prompt.
            context: Compaction context with model info.

        Returns:
            LLM response content as string.
        """
        from .graph import ThinkingChatOpenAI, clean_llm_config
        from .services.llm_failover import wrap_langchain_failover

        # Compaction-model override (env COMPACTION_MODEL > yaml
        # compaction.model, resolved in load_config; legacy
        # summarization_model alias honored when unset): when active,
        # EVERY summarization call — including each concurrent batch call
        # in the parallel pool — resolves the SAME override through this
        # pure function on the shared config object, so all N client
        # constructions are consistent.
        override_model = resolve_compaction_model(context.config)
        if override_model:
            llm_config = {
                **self.llm_config_with_headers,
                "model": override_model,
            }
        else:
            llm_config = self.llm_config_with_headers

        # Phase 1 / WS-3.1+3.2: adaptive per-call timeout + facade margin.
        # ``inner_cap`` sizes both the ``asyncio.wait_for`` backstop AND the
        # facade's ``wall_clock_cap_s`` (``inner_cap + margin``). The
        # site-level backstop trips FIRST — that is the contract (architect
        # §9.8, "site TimeoutError still the first tripped"). The facade
        # cap is sized to wrap cleanly after the inner cancel so tenacity
        # retries stay inside the outer cap (llm_failover.py:559-568).
        inner_cap = _summarization_timeout_s(prompt, context.config)
        facade_cap = inner_cap + context.config.timeout_facade_margin_s

        # NEVER-SILENT FALLBACK (Commit B): if the override client cannot
        # be CONSTRUCTED (bad model string rejected by the client, config
        # shape error, facade wrap failure), WARN-log with the traceback
        # and rebuild from the session-model config — never swallowed.
        # (Invoke-time failures for an API-unknown model surface through
        # the EXISTING per-batch/outer handlers, which warn and fall back
        # to truncation — also never silent.)
        try:
            # ``base_url_backup`` is consumed by the HA facade from the raw
            # config dict; clean it only at the constructor.
            llm = ThinkingChatOpenAI(**clean_llm_config(dict(llm_config)))
            # v2 HA: route through the shared facade. See
            # ``daemon.services.llm_failover``. The facade cap is
            # ``inner_cap + timeout_facade_margin_s`` (default +5s) per the
            # architect §9.8 PINNED margin.
            llm_wrapper = wrap_langchain_failover(llm, llm_config, wall_clock_cap_s=facade_cap)
        except Exception:
            if not override_model:
                raise
            logger.warning(
                "Compaction model %r failed to construct; falling back to "
                "the session model for this summarization call.",
                override_model,
                exc_info=True,
            )
            llm_config = self.llm_config_with_headers
            llm = ThinkingChatOpenAI(**clean_llm_config(dict(llm_config)))
            llm_wrapper = wrap_langchain_failover(llm, llm_config, wall_clock_cap_s=facade_cap)

        # Belt-and-braces: ``inner_cap`` ``asyncio.wait_for`` is the
        # site-level cap (the FIRST to trip on timeout). The REAL
        # primary line of defense is the facade's
        # ``wall_clock_cap_s`` (tenacity ``stop_after_delay``
        # inside the retry loop) — see
        # ``daemon.services.llm_failover`` docstring "Wall-clock
        # cap". Belt-and-braces decision: keep the site-level
        # cap so a future site bypass of the facade still gets
        # cancellation; the facade cap is sized to wrap cleanly
        # after the inner cancel + a small margin so tenacity
        # retries don't overrun the outer ceiling. The
        # ``asyncio.TimeoutError`` propagates to the per-chunk
        # except in ``_summarize_chunked`` (narrowed to
        # ``(TimeoutError, asyncio.TimeoutError)`` per WS-3.4
        # O14), preserving the partial-summary path (C1).
        response = await asyncio.wait_for(
            asyncio.to_thread(
                llm_wrapper.invoke,
                [
                    SystemMessage(
                        content="You are a helpful assistant that summarizes conversations "
                        "concisely while preserving all important details."
                    ),
                    HumanMessage(content=prompt),
                ],
            ),
            timeout=inner_cap,
        )

        content = response.content
        return _extract_text_from_content(content)
    
    @staticmethod
    def _build_global_doc_for_full_success(
        compactable: list[MessageGroup],
        global_text: str,
        context: CompactionContext,
        *,
        instance_id: str,
        compacted_at: str,
        effective_model_name: str,
        previous_overview: str | None = None,
        msg_timestamps: dict | None = None,
    ) -> SystemMessage:
        """Build the global doc for the FULL-success path.

        Architect §6 — the FULL path emits a single section spanning
        the entire compactable span; the merged text becomes the
        GLOBAL OVERVIEW body. No per-batch sections (the per-batch
        texts are already merged into ``global_text``).

        W5 fix (2026-09-01) — the SECTION 1 body is a SHORT
        reference ("see GLOBAL OVERVIEW above") instead of
        duplicating ``global_text`` verbatim; the GLOBAL body is
        the only place the merged text appears. Eliminates the
        prior full-success-path text duplication.

        W1 fix (2026-09-01) — accepts ``previous_overview`` and
        threads it into the doc via :func:`build_compaction_doc`'s
        existing seed slot.

        F1 fix (2026-09-01) — accepts ``msg_timestamps`` and
        threads it into the doc's SECTION DETAIL via
        :func:`build_compaction_doc`'s existing
        ``msg_timestamps`` slot, so the conversation-time clause
        appears in the rendered doc. ``None`` is the legacy /
        legacy-test path; production callers (compact_state)
        always pass the first-appearance map derived from
        ``context.messages``.
        """
        # Span boundaries in 1-based terms (relative to the
        # compactable_groups list, which is a slice of the
        # selectable pool; the doc spec uses 1-based over the
        # original conversation — we emit indices relative to the
        # compactable subset since the engine does not know the
        # absolute conversation position. Persist-seam callers may
        # pass an absolute map if needed.)
        start_idx = 1
        end_idx = sum(len(g.messages) for g in compactable)
        start_id = compactable[0].messages[0].id if compactable else None
        end_id = compactable[-1].messages[-1].id if compactable else None
        seq = _next_compaction_seq(context.messages, instance_id)
        context_window = get_model_context_limit(
            effective_model_name, context.config
        )
        # W5 — single-section full success: the section body is
        # a short reference back to the GLOBAL OVERVIEW; the
        # GLOBAL body is the canonical merged text. Eliminates
        # the prior duplicated-text regression.
        section_body = (
            "(see GLOBAL OVERVIEW above — the merged text is the "
            "authoritative summary for this compactable span)"
        )
        return build_compaction_doc(
            instance_id=instance_id,
            seq=seq,
            mode="summary",
            compacted_at=compacted_at,
            global_overview=global_text,
            sections=[
                {
                    "start_idx": start_idx,
                    "end_idx": end_idx,
                    "body": section_body,
                    "start_id": start_id,
                    "end_id": end_id,
                }
            ],
            total_sections=1,
            summarized_start=start_idx,
            summarized_end=end_idx,
            preserved_count=_count_preserved(context, compactable),
            dropped_spans=[],
            context_window=context_window,
            previous_overview=previous_overview,
            msg_timestamps=msg_timestamps,
        )

    @staticmethod
    def _per_batch_section_meta(
        compactable: list[MessageGroup],
        summaries: list[str],
        context: CompactionContext,
        batch_indices: list[int] | None = None,
    ) -> list[dict]:
        """Compute the per-batch section metadata for the PARTIAL path.

        Architect §6.2 — the partial path produces N surviving sections
        (one per successful batch) in batch order, plus K dropped
        spans. The caller (``compact_state``) splits ``compactable``
        into the same batches as ``_summarize_chunked`` and aligns
        ``summaries[i]`` to batch i. Batches not in the surviving set
        produce empty summaries and contribute their span to the
        dropped-spans list.

        When ``batch_indices`` is provided, ``summaries[i]`` is mapped
        to the batch at original-index ``batch_indices[i]`` (the
        non-contiguous survivor case where batches 0, 2, 4 succeed
        but 1, 3, 5 fail). When ``batch_indices`` is ``None``, the
        summaries are assumed to be in batch-index order
        (the contiguous case where the engine is on the "all
        batches succeed but merge failed" path).
        """
        # Batch boundaries — same heuristic as ``_summarize_chunked``
        # (batch_size=20). When ``len(compactable) < 20``, this is a
        # single batch covering everything.
        batch_size = 20
        sections: list[dict] = []
        # B3 fix (2026-09-01) — ``start_idx`` / ``end_idx`` are
        # minted in ORIGINAL batch coordinates, NOT in
        # survivor-compressed coordinates. ``s_idx`` used to track
        # the END of the previously-emitted section; for
        # non-contiguous survivors (batches 0, 2, 4 succeed but
        # 1, 3, 5 fail) that collapsed the next section's
        # ``start_idx`` to ``s_idx + 1`` of the previous survivor,
        # not the original batch boundary — SECTION 2 carried the
        # wrong batch's summary, the actually-dropped batch was
        # presented as covered. Compute ORIGINAL start/end from
        # the compactable-list position of the batch.
        for i, body in enumerate(summaries):
            if batch_indices is not None:
                batch_i = batch_indices[i]
            else:
                batch_i = i
            batch_groups = compactable[batch_i * batch_size:(batch_i + 1) * batch_size]
            if not batch_groups:
                break
            # ORIGINAL coordinates: walk back over every compactable
            # group BEFORE this batch's start.
            prior_msgs = sum(
                len(g.messages)
                for g in compactable[: batch_i * batch_size]
            )
            start_idx = prior_msgs + 1
            end_idx = prior_msgs + sum(len(g.messages) for g in batch_groups)
            start_id = batch_groups[0].messages[0].id
            end_id = batch_groups[-1].messages[-1].id
            if body:
                sections.append({
                    "start_idx": start_idx,
                    "end_idx": end_idx,
                    "body": body,
                    "start_id": start_id,
                    "end_id": end_id,
                })
            else:
                # Failed batch → dropped span.
                dropped_spans.append((start_idx, end_idx))
        return sections

    @staticmethod
    def _build_global_doc_for_partial(
        batch_sections: list[dict],
        context: CompactionContext,
        *,
        instance_id: str,
        compacted_at: str,
        global_overview: str | None,
        effective_model_name: str,
        previous_overview: str | None = None,
        msg_timestamps: dict | None = None,
    ) -> SystemMessage:
        """Build the global doc for the PARTIAL-summary path.

        Architect §6.2 — the partial path emits one section per
        surviving batch; the GLOBAL OVERVIEW slot is either filled
        by the bounded merge pass or carries the placeholder line on
        failure. Dropped spans (failed batches) appear in the envelope
        header and in the per-section span.

        ``batch_sections`` is the survivor set; the total section
        count is computed from the compactable_groups length and
        the per-batch batch_size=20 slicing (so k/n is honest).

        W1 fix (2026-09-01) — accepts ``previous_overview`` and
        threads it into the doc via :func:`build_compaction_doc`'s
        existing seed slot.

        F1 fix (2026-09-01) — accepts ``msg_timestamps`` and
        threads it into the doc's SECTION DETAIL via
        :func:`build_compaction_doc`'s existing ``msg_timestamps``
        slot, so each per-section conversation-time clause renders
        from the first-appearance map (architect §4).
        """
        compactable = getattr(context, "_compactable_groups_for_doc", None)
        if compactable is None:
            total_sections = len(batch_sections)
            summarized_start = batch_sections[0]["start_idx"] if batch_sections else 1
            summarized_end = (
                batch_sections[-1]["end_idx"] if batch_sections else 0
            )
        else:
            total_sections = max(
                1, (len(compactable) + 19) // 20  # ceil(len/20)
            )
            summarized_start = 1
            summarized_end = sum(len(g.messages) for g in compactable)
        seq = _next_compaction_seq(context.messages, instance_id)
        context_window = get_model_context_limit(
            effective_model_name, context.config
        )
        # Build dropped_spans from the per-batch survivor set:
        # the compactable range is covered by batch_size=20 buckets;
        # any bucket whose body is missing from ``batch_sections`` is
        # dropped.
        dropped_spans: list[tuple[int, int]] = []
        if compactable is not None:
            batch_size = 20
            surviving_starts = {
                b["start_idx"] for b in batch_sections
            }
            s_idx = 0
            for i in range(0, len(compactable), batch_size):
                bg = compactable[i:i + batch_size]
                s = s_idx + 1
                e = s_idx + sum(len(g.messages) for g in bg)
                if s not in surviving_starts:
                    dropped_spans.append((s, e))
                s_idx = e
        return build_compaction_doc(
            instance_id=instance_id,
            seq=seq,
            mode="partial_summary",
            compacted_at=compacted_at,
            global_overview=global_overview,
            sections=batch_sections,
            total_sections=total_sections,
            summarized_start=summarized_start,
            summarized_end=summarized_end,
            preserved_count=_count_preserved(context, compactable),
            dropped_spans=dropped_spans,
            context_window=context_window,
            previous_overview=previous_overview,
            msg_timestamps=msg_timestamps,
        )

    async def _truncate_fallback(
        self,
        compactable: list[MessageGroup],
        preserved: list[MessageGroup],
        context: CompactionContext,
        *,
        instance_id: str = "",
        msg_timestamps: dict | None = None,
    ) -> tuple[list[BaseMessage], str, str | None]:
        """Fallback when summarization produced no per-batch summaries.

        Architect §6.3 — the |S|=0 path emits a single
        ``compaction-global-`` doc with the dropped-spans clause in
        the envelope header. The boundary line is INSIDE the doc
        (W2 fix — the old dangling
        ``truncation-marker-`` SystemMessage is gone).

        Bounded best-effort GLOBAL (architect §6.3):

        * Single LLM call, ``TRUNCATION_GLOBAL_TIMEOUT_S`` (~20s)
          cap.
        * Sampled input capped at ``TRUNCATION_GLOBAL_INPUT_CAP_CHARS``
          (~30-40k chars).
        * Only fires when ``tokens_before ≥ TRUNCATION_GLOBAL_MIN_TOKENS_BEFORE``
          (~2k) — small inputs skip the call and go straight to
          envelope+dropped.
        * Failure (timeout / error) → envelope + dropped spans
          only; doc still emits.
        * Hard fail-open: never blocks the trim. If the bounded call
          fails, the doc has no GLOBAL OVERVIEW body and the
          envelope already enumerates the dropped span — the wire
          outcome is honest without a global frame.

        W1 fix (2026-09-01) — when a prior doc exists in the
        snapshot, its GLOBAL OVERVIEW is extracted by
        :func:`_extract_previous_overview` and threaded into the
        doc via :func:`build_compaction_doc`'s ``previous_overview``
        slot so the pass-2 envelope carries the convergence seed.

        F1 fix (2026-09-01) — accepts ``msg_timestamps`` and
        threads it into the doc. On the truncation path the doc
        has no SECTION DETAIL (sections=[]), so the time clause
        is OMITTED regardless; the parameter is plumbed for
        uniformity with the full / partial doc builders (and to
        keep the call-site symmetry if the path ever evolves).

        Args:
            compactable: Groups that would have been summarized.
            preserved: Groups being kept intact.
            context: Compaction context.
            instance_id: Instance id for the doc id; falls back to
                ``context.instance_id`` when empty.
            msg_timestamps: First-appearance map (msg_id → ISO ts)
                for the SECTION DETAIL conversation-time clause.
                ``None`` → clause OMITTED (never generation-time
                fallback). ``None`` is the legacy / legacy-test
                path; production callers stamp it before invoking
                :func:`_truncate_fallback`.

        Returns:
            Tuple of ``(replacement_messages, compaction_type,
            total_summary_status)``. ``replacement_messages`` is
            ``[doc, *preserved_tail]`` (caller re-attaches
            injected). ``compaction_type`` is ``"truncation"``.
            ``total_summary_status`` is ``"ok"`` when the bounded
            GLOBAL call succeeded, ``"failed"`` otherwise.
        """
        # 1. Try the bounded best-effort GLOBAL call.
        iid = instance_id or _extract_instance_id(context)
        global_text: str | None = None
        status: str | None = None
        # W1 — extract the prior doc's GLOBAL OVERVIEW so the new
        # doc can converge. None on the first compaction of an
        # instance.
        previous_overview = _extract_previous_overview(
            context.messages, iid
        )
        if (
            context.tokens_before_total
            and context.tokens_before_total >= TRUNCATION_GLOBAL_MIN_TOKENS_BEFORE
        ):
            sampled = self._sample_for_truncation_global(compactable)
            if sampled:
                # W1 — seed the bounded GLOBAL prompt with the
                # prior doc's GLOBAL so the model converges across
                # passes instead of re-deriving from scratch.
                previous_overview_block = ""
                if previous_overview:
                    previous_overview_block = (
                        f"Previous overview (treat as authoritative "
                        f"frame; merge in any updates; do not lose "
                        f"entities/goals/decisions carried here):\n"
                        f"{previous_overview}\n\n"
                    )
                try:
                    overview_prompt = (
                        "Write a concise global overview (~300 tokens) of "
                        "the following conversation. Focus on entities, "
                        "goals, and decisions. Do not include a header "
                        "line or timestamp — this is the body of the "
                        "GLOBAL OVERVIEW section. If the content is too "
                        "fragmented to summarize, return an empty string.\n\n"
                        f"{previous_overview_block}"
                        f"Conversation:\n{sampled}"
                    )
                    global_text = await asyncio.wait_for(
                        self._call_summarization_llm(overview_prompt, context),
                        timeout=TRUNCATION_GLOBAL_TIMEOUT_S,
                    )
                    if global_text and global_text.strip():
                        status = "ok"
                    else:
                        global_text = None
                        status = "failed"
                except (TimeoutError, asyncio.TimeoutError, Exception) as e:
                    logger.warning(
                        "bounded best-effort GLOBAL failed on "
                        "truncation path: %s — fail-open",
                        e,
                    )
                    global_text = None
                    status = "failed"
            else:
                status = "failed"
        else:
            # tokens_before < ~2k — skip the GLOBAL call per §6.3.
            status = "failed"

        # 2. Build the doc. The dropped span covers the entire
        # compactable range (architect §6.3 — every message in
        # the compactable window is dropped without summary).
        start_idx = 1
        end_idx = sum(len(g.messages) for g in compactable) if compactable else 0
        start_id = compactable[0].messages[0].id if compactable else None
        end_id = compactable[-1].messages[-1].id if compactable else None
        seq = _next_compaction_seq(context.messages, iid)
        context_window = get_model_context_limit(
            self._effective_model_name(context), context.config
        )
        dropped_spans = [(start_idx, end_idx)] if compactable else []
        # Preserved-tail count for the envelope header.
        context._preserved_count_for_doc = sum(
            len(g.messages) for g in preserved
        )
        doc = build_compaction_doc(
            instance_id=iid,
            seq=seq,
            mode="truncation",
            compacted_at=context.compacted_at_iso or datetime.now(timezone.utc).isoformat(),
            global_overview=global_text or "",  # empty → no body, just envelope
            sections=[],
            total_sections=0,
            summarized_start=start_idx,
            summarized_end=end_idx,
            preserved_count=context._preserved_count_for_doc,
            dropped_spans=dropped_spans,
            context_window=context_window,
            previous_overview=previous_overview,
            msg_timestamps=msg_timestamps,
        )
        # 3. Flatten preserved tail (multimodal content → text,
        # original ids preserved). The caller re-attaches injected.
        replacement: list[BaseMessage] = [doc]
        for group in preserved:
            for msg in group.messages:
                if isinstance(msg.content, list):
                    msg.content = _extract_text_from_content(msg.content)
                replacement.append(msg)
        return replacement, "truncation", status

    @staticmethod
    def _sample_for_truncation_global(
        compactable: list[MessageGroup],
    ) -> str:
        """Sample the compactable window for the bounded GLOBAL call.

        Architect §6.3 — sampled input capped at
        ``TRUNCATION_GLOBAL_INPUT_CAP_CHARS`` (~30-40k chars). Head +
        middle + tail sample so the GLOBAL has at least some
        context from each end of the dropped span.
        """
        msgs = [m for g in compactable for m in g.messages]
        if not msgs:
            return ""
        # Build a head + tail sample.
        parts: list[str] = []
        for msg in msgs[: max(1, len(msgs) // 3)]:
            content = _extract_text_from_content(getattr(msg, "content", ""))
            parts.append(f"{getattr(msg, 'type', 'unknown')}: {content}")
        if len(msgs) > 6:
            parts.append("…")
            for msg in msgs[-max(1, len(msgs) // 3):]:
                content = _extract_text_from_content(getattr(msg, "content", ""))
                parts.append(f"{getattr(msg, 'type', 'unknown')}: {content}")
        sampled = "\n".join(parts)
        if len(sampled) > TRUNCATION_GLOBAL_INPUT_CAP_CHARS:
            sampled = sampled[:TRUNCATION_GLOBAL_INPUT_CAP_CHARS] + "\n…(truncated)"
        return sampled
    
    @staticmethod
    def _is_recently_compacted(last_compacted_at: str) -> bool:
        """Check if compaction occurred recently (within 60 seconds).
        
        Args:
            last_compacted_at: ISO timestamp string.
            
        Returns:
            True if compaction was within last 60 seconds.
        """
        try:
            last_time = datetime.fromisoformat(last_compacted_at)
            if last_time.tzinfo is None:
                last_time = last_time.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return (now - last_time).total_seconds() < 60
        except (ValueError, TypeError):
            return False
