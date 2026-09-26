"""Content-hardening predicates — the shared corpus for compaction and snapshots.

This module is the canonical home for the predicates + multimodal flattener
that every summarizer (the compaction engine AND the future ``SnapshotService``
per the agent-snapshot v1 build, design-exploration §2.3 / feasibility-notes
§A) depends on. They were extracted from :mod:`daemon.compaction` in PR1 of
the agent-snapshot v1 build; the move is **behavior-preserving** — every
existing call site keeps working via thin re-exports under the original
private names in :mod:`daemon.compaction`, and NEW callers (Wave 1b) should
import directly from this module.

Location
--------

The module lives at ``daemon/_content_hardening.py`` (top-level daemon
package, leading underscore) rather than under ``daemon/services/`` — the
``services`` subpackage eagerly re-exports ``instance_lifecycle`` from its
``__init__.py``, which eagerly imports ``ContextCompactor`` from
``daemon.compaction`` at module load. Importing
``daemon.services.content_hardening`` from inside ``daemon.compaction``
(self-import during the module's own load) would create the
``compaction → services.__init__ → instance_lifecycle → compaction`` cycle
that the existing ``graph.py:1866-1867`` lazy-import pattern on
``_extract_text_from_content`` was designed to break. The same
``_name as _private_alias`` re-export pattern is used here without the
lazy harness — top-level ``daemon`` is light (just a docstring +
``__version__``) and has no upward eager imports. This is the judgment
call flagged in the build commission's standing note ("if
feasibility-notes doesn't name one, choose a neutral home … and record
the choice as a judgment call").

What's here and why
-------------------

* :func:`is_injected_message` / :func:`has_context_kind` (originally
  ``_is_injected_message`` / ``_has_context_kind`` at compaction.py:108-146) —
  user-intent preservation flags. User-injected ``HumanMessage``s carry
  ``additional_kwargs.injected_message`` (and optionally
  ``context_kind``); both predicates key on the truthy value so the
  summarizer preserves the message verbatim. This is the load-bearing
  contract the empty-response-guard and the injected-notes-hoisting
  fix rely on.

* :func:`extract_text_from_content` (originally
  ``_extract_text_from_content`` at compaction.py:81-105) — the multimodal
  flattener that turns a provider-shape ``list[dict | str]`` content
  (OpenAI vision-style ``{"type": "text", "text": "..."}`` +
  ``{"type": "image_url", ...}`` blocks) into a plain string. Skips
  non-text blocks entirely. Other modules (graph.py:1866-1867 lazy import,
  child_reports, watcher_context_builder, symptom_repair_engine) reuse it
  for the same silent-garbage fix; the existing graph.py lazy import is
  rewritten in PR1 to point at this module.

* :func:`injected_note_absorbed_ids` (originally
  ``_injected_note_absorbed_ids`` at compaction.py:149-188) — the absorb
  gate: ids of BARE-flag injected notes that are ANSWERED (an
  ``AIMessage`` exists at a later index) and therefore selectable for
  the compacted span. Honors the kill-switch
  :func:`daemon.config.resolve_injected_notes_absorb`; OFF → empty
  absorbed set, restoring the legacy two-bucket behavior.

* :func:`is_hoisted_injected` (originally ``_is_hoisted_injected`` at
  compaction.py:191-214) — the hoist/preserve predicate:
  ``context_kind`` messages OR UNANSWERED bare-flag notes are hoisted
  verbatim; ANSWERED bare notes join the selectable pool.

* :func:`partition_injected_for_compaction` (originally
  ``_partition_injected_for_compaction`` at compaction.py:217-250) —
  three-bucket partition: ``(selectable, preserved_injected,
  absorbed_notes)``. The Wave 1b snapshot service will reuse this for
  the R6a negative-space exclusion (``context_kind=snapshot_digest``
  skip) — already-persisted knowledge is filtered out before the
  summarizer sees it, using the same predicate path compaction uses.

Import discipline
-----------------

* The module depends ONLY on ``langchain_core`` message types and on
  :func:`daemon.config.resolve_injected_notes_absorb` (a leaf function
  with no upward deps). No upward imports on
  ``daemon.services.<anything>``, no upward import on
  ``daemon.compaction``, so the module is itself cycle-free.
* The re-export aliases in ``daemon/compaction.py`` (above the
  remaining class definitions) bind at module load time to the
  imported functions in this module — internal callers throughout
  ``daemon/compaction.py`` continue to use the ``_name`` aliases and
  resolve cleanly to the canonical functions here.
* ``daemon/compaction.py`` re-exports these symbols under the original
  private ``_name`` aliases so existing tests
  (``tests/test_injection_compaction.py``,
  ``tests/integration/test_context_injection_integration.py``,
  ``tests/unit/test_compaction.py``,
  ``tests/unit/test_symptom_repair_engine_phase2.py``) keep resolving
  their ``from daemon.compaction import _is_injected_message`` / etc.
  imports verbatim. Internal non-test callers (``graph.py``,
  ``services/symptom_repair_engine.py``, etc.) are updated to point at
  this module.

``from __future__ import annotations`` is on the module: prior lazy
annotations rot on 3.13+Python (string-union forward refs in module-level
signatures raise ``TypeError`` at class-def time — see
``daemon/tools/inner_soul.py:824`` precedent). The signatures in this
module are intentionally simple and don't hit the rot, but the future
import is cheap insurance.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage

from .config import resolve_injected_notes_absorb


def extract_text_from_content(content: str | list) -> str:
    """Extract text from message content, handling multimodal lists.

    Args:
        content: Message content, either a string or a multimodal list
                 (e.g., ``[{"type": "text", "text": "..."}, {"type": "image_url", ...}]``).

    Returns:
        Extracted text string. For multimodal content, joins all text blocks.
        Skips ``image_url`` blocks entirely.
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


def is_injected_message(msg: BaseMessage) -> bool:
    """Phase 1 / C3: detect a user-injected message by ``additional_kwargs``.

    Mirrors the ``language_check_reminder`` skip pattern at
    ``daemon/graph.py:493``. An injected message was deliberately placed
    into the conversation by the user via the injection slot (Phase 1 /
    C2) and MUST survive any compaction pass — both proactive (this
    module) and reactive (``daemon/graph.py:641-684``). Summarizing it
    would erase user intent.

    Args:
        msg: Candidate ``BaseMessage`` (typically ``HumanMessage``).

    Returns:
        ``True`` when the message is flagged as injected, ``False`` otherwise.
    """
    additional_kwargs = getattr(msg, "additional_kwargs", None)
    if not additional_kwargs:
        return False
    return bool(additional_kwargs.get("injected_message"))


def has_context_kind(msg: BaseMessage) -> bool:
    """True when the injected message is a REAL ``[SYSTEM CONTEXT]`` block.

    Context messages are stamped by ``_make_context_message``
    (``daemon/services/context_messages.py``) with BOTH
    ``injected_message=True`` AND a ``context_kind`` enum value. They
    are permanently non-selectable: preserved verbatim and hoisted
    above the compaction doc at every pass (unchanged behavior).

    Bare-flag injected messages (operator notes via the FIFO injection
    drain — ``daemon/services/instance_messaging.py``) carry
    ``injected_message=True`` with NO ``context_kind``; only those are
    eligible for the answered-note lifecycle.
    """
    additional_kwargs = getattr(msg, "additional_kwargs", None)
    if not additional_kwargs:
        return False
    return bool(additional_kwargs.get("context_kind"))


def injected_note_absorbed_ids(messages: list[BaseMessage]) -> frozenset[str]:
    """Ids of BARE-flag injected notes that are ANSWERED (selectable).

    The conservative "protect until answered" contract (injected-notes
    hoisting fix): a bare injected note is ANSWERED when an
    ``AIMessage`` exists at a LATER index in the channel order.
    Unanswered notes — the newest message, or notes followed only by
    ``ToolMessage``s — stay permanently preserved. ``context_kind``
    messages never qualify (they are permanent regardless of position),
    and id-less bare notes are conservatively treated as UNANSWERED
    (never absorbed).

    Args:
        messages: The FULL pre-compaction channel (conversation order).

    Returns:
        Frozenset of message ids that may be absorbed into the
        compacted span. Empty when there are no answered bare notes —
        or when the ``ENSEMBLE_INJECTED_NOTES_ABSORB`` kill-switch
        resolves OFF, in which case the absorb contract is disabled
        entirely and the three-bucket partition degenerates to the
        legacy two-bucket behavior (ALL injected preserved + hoisted;
        ``context_kind`` handling unchanged in both states). This is
        the SINGLE check site for the flag — the gate, numerator,
        envelope, and seam all consume this set downstream.
    """
    if not resolve_injected_notes_absorb():
        # Kill-switch OFF → empty absorbed set: every bare-flag note
        # fails the ``msg_id not in absorbed_note_ids`` hoist check and
        # returns to preserve-forever hoisting (old behavior).
        return frozenset()
    absorbed: set[str] = set()
    for idx, msg in enumerate(messages):
        if not is_injected_message(msg) or has_context_kind(msg):
            continue
        msg_id = getattr(msg, "id", None)
        if not msg_id:
            continue  # id-less → conservative: preserved, never absorbed
        if any(isinstance(m, AIMessage) for m in messages[idx + 1:]):
            absorbed.add(msg_id)
    # frozenset-wrap matches the declared ``-> frozenset[str]`` contract.
    return frozenset(absorbed)


def is_hoisted_injected(
    msg: BaseMessage, absorbed_note_ids: frozenset[str]
) -> bool:
    """The hoist/preserve predicate for injected messages.

    Hoisted (preserved verbatim above the compaction doc) when:

    * the message carries ``context_kind`` (real system context —
      permanent), OR
    * it is a bare-flag note that is NOT answered (no later AIMessage
      in the pre-compaction channel — or an unresolvable id, treated
      conservatively as unanswered).

    Answered bare notes are NOT hoisted: they join the selectable
    pool and are absorbed into the compacted span like regular
    history.
    """
    if not is_injected_message(msg):
        return False
    if has_context_kind(msg):
        return True
    msg_id = getattr(msg, "id", None)
    if not msg_id:
        return True  # id-less bare note → conservative preserve
    return msg_id not in absorbed_note_ids


def partition_injected_for_compaction(
    messages: list[BaseMessage],
) -> tuple[list[BaseMessage], list[BaseMessage], list[BaseMessage]]:
    """Three-bucket partition of the pre-compaction channel.

    Replaces the former unconditional two-way injected split: bare-flag
    operator notes now join the selectable pool once ANSWERED (an
    ``AIMessage`` exists at a later index — see
    :func:`injected_note_absorbed_ids`), instead of being hoisted
    forever.

    Returns:
        Tuple ``(selectable, preserved_injected, absorbed_notes)`` where:

        * ``selectable`` — regular history PLUS answered bare notes,
          in original channel order (order matters: boundary grouping
          and tail preservation are order-sensitive).
        * ``preserved_injected`` — ``context_kind`` messages plus
          UNANSWERED bare notes (hoisted verbatim above the doc).
        * ``absorbed_notes`` — the answered bare-note subset of
          ``selectable`` (same objects), for envelope accounting.
    """
    absorbed_note_ids = injected_note_absorbed_ids(messages)
    selectable: list[BaseMessage] = []
    preserved_injected: list[BaseMessage] = []
    absorbed_notes: list[BaseMessage] = []
    for msg in messages:
        if is_hoisted_injected(msg, absorbed_note_ids):
            preserved_injected.append(msg)
        else:
            selectable.append(msg)
            if is_injected_message(msg):
                absorbed_notes.append(msg)
    return selectable, preserved_injected, absorbed_notes


__all__ = [
    "extract_text_from_content",
    "has_context_kind",
    "injected_note_absorbed_ids",
    "is_hoisted_injected",
    "is_injected_message",
    "partition_injected_for_compaction",
]
