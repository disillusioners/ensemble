"""``MessageTapSlot`` — non-load-bearing hook for the C2 ``message_metadata`` upserts.

Phase 1 C2 of the langgraph-checkpoint-perf plan (Solution M;
``daemon/repositories/message_metadata`` + ``MessageTapSlot``).

The slot is constructed against a :class:`MessageMetadataRepository` and
fires at the four approved tap sites (decisions.md D1):

* ``"user_message_entry"``           — at ``_build_graph_input``
  (``daemon/services/instance_messaging.py:237-244``, F1 fix).
* ``"agent_node_return"``           — post-F2 single-return refactor of
  the dual-return block at ``daemon/graph.py:3386-3397``.
* ``"compaction_aupdate_reactive"`` — after the reactive-compaction
  ``aupdate_state`` inside the CLE handler's in-frame persist block
  (``compaction_tap_slot.tap_node_return``).
* ``"compaction_aupdate_messaging"`` — after the messaging-side
  ``aupdate_state`` at ``daemon/services/instance_messaging.py:810-822``.

OOS / explicitly NOT tapped
---------------------------
* Watchover synthesis cascade (LD-D2; ``daemon/persistence.py``'s
  ``serialize_message`` skip — line 405-407 in the post-PR1 codebase —
  handles the display-side invisibility).
* ``tools_node`` (no custom ToolNode wrapper; Critical 4; the
  serializer skip at persistence.py:405-407 handles the display).
* ``question_pause_node`` (no tap there).
* ``nudge`` / ``language_check`` (id-less; never tapped; fall to
  ``state.ts`` if surfaced — no lag mechanism).
* Direct ``ainvoke`` invocation at ``instance_messaging.py:1055``
  (B1 / D19; inline ``{"messages": [message]}`` bypasses
  ``_build_graph_input``; zero production callers; id-less input;
  ``state.ts`` fallback applies; mirrors the watchover handling).
* ``RemoveMessage`` markers — filtered inside ``_extract_ids``.

Rev 2 idempotency
-----------------
Every tap is an ``INSERT ... ON CONFLICT DO NOTHING`` against the PK
``(thread_id, message_id)``. Re-taps on revive + compaction collapse to
no-ops at the constraint level. The first tap wins; subsequent taps
preserve first-appearance semantics. See decisions.md D3 + the
``test_message_metadata_revive_stability`` integration test.

The sync / async bridge
-----------------------
Per decisions.md D14 the repository is intentionally SYNC — the engine
factory at ``daemon/repositories/factory.py:10`` returns
``sqlalchemy.Engine`` (not ``AsyncEngine``). The tap bridges via
``asyncio.to_thread(self._repo.upsert_batch, ...)`` so the sync DB
write never blocks the asyncio event loop (matches the
``daemon/services/context_messages.py::assemble_context_messages`` and
``instance_messaging.py:1026`` ``asyncio.to_thread`` patterns).

Failure mode
------------
The hook is non-load-bearing — the slot's internal
``try / except Exception`` (around the entire ``tap_node_return``
body, at ``daemon/services/message_tap.py`` below) is the **SOLE
containment layer**. Call sites do NOT wrap
``await slot.tap_node_return(...)`` in ``try / except``: they use a
plain ``if slot is not None`` (or repo-presence) guard only. This is
by design — ``asyncio.CancelledError`` MUST propagate through the
await so pause cancellation reaches the outer ``agent_node`` /
``_maybe_compact_context`` task and the turn quiesces at the next
checkpoint boundary. On Python 3.13+ ``CancelledError`` is promoted
to ``BaseException`` (no longer caught by ``except Exception``), so
the internal handler's narrow ``except Exception`` already lets it
through — but a wider ``except BaseException`` at a call site would
SWALLOW it and break pause. The ``test_message_tap_failure_is_non_fatal``
unit test pins that ordinary errors never propagate;
``test_cancelled_error_propagates`` (in
``tests/unit/services/test_message_tap_slot.py``) pins that
``CancelledError`` MUST propagate. Together they enforce the
"internal handler is sole containment" invariant — do NOT add a
second ``try / except`` around the call at any of the 4 tap sites.

Over-record property (benign)
-----------------------------
The tap fires BEFORE the node's checkpoint commit returns. If a
pause lands between the tap-await and the node's return, the
``message_metadata`` rows are already persisted for messages whose
node return never completed (and therefore were never checkpointed).
This is an **over-record only — never an under-record**: a message
that DID reach checkpoint always has at least one tap row (revive +
first-write-wins from the ``ON CONFLICT DO NOTHING`` PK constraint
guarantees re-taps don't add rows), but a message whose node return
never completed may have a tap row with no checkpoint to join to.

This is benign once the PR3 read path joins ``message_metadata`` at
the aget-only serialization loop (side-table = enrichment, never
authoritative — extra rows are simply never joined). No
real-time reader depends on side-table exhaustiveness. PR3-era
reviewers should NOT flag over-records as a bug; under-records (a
checkpoint message with no tap row) would be a bug, over-records
are not.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any

from daemon.checkpoint_perf import log_message_tap

if TYPE_CHECKING:
    from daemon.repositories.message_metadata.repository import (
        MessageMetadataRepository,
    )

logger = logging.getLogger(__name__)


# The source-label values are part of the contract — see decisions.md
# D1 and the ``test_message_metadata_hook_placement`` AST scan (which
# asserts the site count and that each label is used).
# P1b (proactive-compaction-fix ADDENDUM A.9 T-tap): a FIFTH label was
# added for the 95% pre-call compaction hook (LOCKED decision — the
# hook must NOT reuse ``SOURCE_COMPACTION_REACTIVE`` so per-site
# observability stays intact). The gate test now enumerates 5 sites /
# 5 labels.
SOURCE_USER_MESSAGE_ENTRY = "user_message_entry"
SOURCE_AGENT_NODE_RETURN = "agent_node_return"
SOURCE_COMPACTION_REACTIVE = "compaction_aupdate_reactive"
SOURCE_COMPACTION_MESSAGING = "compaction_aupdate_messaging"
SOURCE_COMPACTION_PRECALL_95 = "compaction_precall_95"


class MessageTapSlot:
    """Non-load-bearing post-node hook for ``message_metadata`` upserts.

    Construction is duck-typed: the slot only requires a repo with
    ``upsert_batch(thread_id, items) -> int``. The slot does not import
    the repo class at module load time so test fixtures (a mock repo,
    an in-memory repo, …) can be passed without circular-import hazards.

    Args:
        repo: A :class:`MessageMetadataRepository` (or any object
            exposing ``upsert_batch(thread_id: str,
            items: list[tuple[str, str, int|None]]) -> int``).
        source: One of the four source-label constants above
            (``SOURCE_*``). The slot logs the source on every emit so
            per-site observability is preserved (PR1's
            ``log_message_tap`` consumer).
    """

    def __init__(self, repo: "MessageMetadataRepository", source: str) -> None:
        self._repo = repo
        self._source = source

    @property
    def source(self) -> str:
        """Return the source label — ``user_message_entry`` etc."""
        return self._source

    @staticmethod
    def _extract_ids(persisted: list[Any]) -> list[str]:
        """Dedupe by ``message.id``; skip ``RemoveMessage`` markers.

        The persisted list is the post-merge ``outgoing`` / ``persisted``
        list at the tap site. ``RemoveMessage`` (LangChain's
        ``add_messages`` reducer with ``remove_messages=True``) emits
        ``RemoveMessage(id='x')`` markers that signal "delete this id
        from state" — these MUST NOT be inserted into
        ``message_metadata`` as new-message rows (D17). The filter is
        ``getattr(m, "type", None) == "remove"`` which is the same shape
        ``langchain-core`` uses for the marker.

        Args:
            persisted: List of ``BaseMessage`` instances (or duck-typed
                objects with ``.id`` and ``.type`` attributes) carried
                to a tap site.

        Returns:
            Ordered list of unique ``message.id`` strings, deduped on
            first appearance; ``RemoveMessage`` markers excluded.
        """
        seen: set[str] = set()
        ids: list[str] = []
        for m in persisted:
            # D17: RemoveMessage marker — skip; would otherwise leave an
            # orphan "first-appearance timestamp" row for a delete-marker
            # that never had a real persisted message.
            if getattr(m, "type", None) == "remove":
                continue
            mid = getattr(m, "id", None)
            if mid and mid not in seen:
                seen.add(mid)
                ids.append(mid)
        return ids

    async def tap_node_return(
        self,
        persisted_list: list[Any],
        thread_id: str,
    ) -> int:
        """Upsert (message_id, created_at) rows for the persisted list.

        Filters ``type=='remove'`` markers, dedupes by ``message.id``,
        batches the unique ids into ONE ``upsert_batch`` call bridged
        via ``asyncio.to_thread`` so the sync repo write does not block
        the event loop. Returns the rows-affected count from the repo
        (0 on a no-op re-tap under ``ON CONFLICT DO NOTHING``).

        Failure path: ANY exception is swallowed and logged at WARNING
        with the source label + a thread-id prefix — the hook is
        non-load-bearing and must NEVER break the graph turn
        (``test_message_tap_failure_is_non_fatal`` pins this).

        Args:
            persisted_list: The local list at the tap site (the
                ``outgoing`` list at the F2 refactor's single return,
                the ``graph_input_messages`` list at the entry path,
                or ``result.replacement_messages`` at the compaction
                sites).
            thread_id: The LangGraph ``configurable.thread_id``
                (``instance_id`` per the project's
                ``thread_id == instance_id`` invariant).

        Returns:
            Number of rows actually inserted (0..len(unique ids)). A
            re-tap of an already-recorded ``(thread_id, message_id)``
            pair returns 0 — the first-appearance semantics from D3.
        """
        try:
            ids = self._extract_ids(persisted_list)
            if not ids:
                # No-op path: the persisted list contained only
                # RemoveMessage markers or id-less messages
                # (nudge / language_check are construction-stamped since
                # the iter-2 identity remediation; direct-ainvoke entries
                # and pre-side-table threads remain the id-less cases).
                # Log nothing — there is
                # nothing to record and a noisy log would multiply
                # by the per-turn volume.
                return 0
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            # Sync repo bridge (Critical 2 / D14): the repo's factory
            # is synchronous (``daemon/repositories/factory.py:10``
            # returns ``sqlalchemy.Engine``). ``asyncio.to_thread`` puts
            # the DB call on the default executor so the event loop is
            # not blocked by the SQLite/PG write.
            count = await asyncio.to_thread(
                self._repo.upsert_batch,
                thread_id,
                [(mid, now_iso, None) for mid in ids],
            )
            log_message_tap(thread_id, count, self._source)
            return count
        except Exception as exc:
            logger.warning(
                f"[MessageTap] non-fatal: source={self._source} "
                f"thread={thread_id[:8] if thread_id else '?'} "
                f"error={type(exc).__name__}: {exc}"
            )
            return 0


__all__ = [
    "MessageTapSlot",
    "SOURCE_USER_MESSAGE_ENTRY",
    "SOURCE_AGENT_NODE_RETURN",
    "SOURCE_COMPACTION_REACTIVE",
    "SOURCE_COMPACTION_MESSAGING",
    "SOURCE_COMPACTION_PRECALL_95",
]
