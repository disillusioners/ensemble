"""Parent-history-side PREFIX ledger for the F-2 RDRS chain.

F-2 (durability-f1-f2 / phase2, task 2.4). The new
``parent_history_has_internal_report`` helper is the
parent-history-side companion to the queue-side
``MessageQueueRepository.find_wake_already_delivered_evidence``
(task 2.3). Both are PREFIX-ledger checks on the child's
report-source shapes — the operative cross-path dedup (per
``decisions.md §14a`` W-1 LOCKED to fallback (ii); the exact-id
equality at ``child_reports.py:3498-3507`` is natural-path-only).

REAL EVIDENCE SHAPES (iteration 2): the parent-history evidence
is the report-frame ``HumanMessage`` stamped at
``daemon/graph.py:8528-8534`` — ``additional_kwargs["source"] =
f"internal_report:{child_id}"`` (NO trailing colon) — which
serializes with ``role == "user"`` (``daemon/utils.py:109``
role_map). The queue-side mint shape (``daemon/manager.py``, all
``MessageQueue`` write sites) is
``internal_report:{child_id}:{message_id}`` (colon-delimited).
The matcher accepts BOTH via the boundary rule (exact no-colon
match OR colon-delimited continuation) and rejects
child-boundary violations (``internal_report:{child}2``,
``internal_report:{other}``).

The helper does NOT belong in ``completion_content.py`` (per
N-10): it is a delivery-recovery concern, not a
content-extraction concern. The new module home also avoids
name-collision with ``MessageMetadataRepository.get_for_thread``
at ``daemon/repositories/message_metadata/repository.py:123``.

The ``source`` key is surfaced by ``daemon/utils.py:264-266``
(the same ``additional_kwargs.source`` surface that the prior
cycle verified for the F-2 sweep's ledger check). The
pre-migration parent's absence of the ``source`` field
degrades gracefully to "not yet reported" (no false-positive
skip — the absence of evidence is treated as "no report yet",
which is the correct default).
"""

from __future__ import annotations

import logging
from typing import Any

from .. import persistence as _persistence

logger = logging.getLogger(__name__)


def _source_prefix(child_id: str) -> str:
    """The no-colon PREFIX for the delivery-evidence scan.

    TWO real-world minting shapes exist (both verified against the
    production code, iteration 2):

    * Parent-history stamp (``daemon/graph.py:8528``): the
      completion report is a ``HumanMessage`` whose
      ``additional_kwargs["source"]`` is
      ``internal_report:{child_id}`` — NO trailing colon, NO
      anchor suffix.
    * Queue-side mint (``daemon/manager.py`` — its six
      ``MessageQueue(...)`` write sites — plus the seventh at
      ``daemon/services/child_reports.py:3762``): ``source`` is
      ``internal_report:{child_id}:{message_id}`` — colon plus
      anchor suffix.

    The ledger scans parent HISTORY, so the no-colon form is the
    operative evidence; the colon form is accepted too because
    both shapes exist historically. Child-boundary safety: a
    source of ``internal_report:{child_id}2`` (a DIFFERENT child
    whose id merely extends this one) must NOT match — hence the
    boundary rule (exact match OR colon-delimited continuation),
    NOT a bare ``startswith``.
    """
    return f"internal_report:{child_id}"


def _is_child_report_source(source: Any, child_id: str) -> bool:
    """Boundary-safe match against the child's report-source shapes.

    Matches ``internal_report:{child_id}`` (exact — the
    parent-history HumanMessage stamp at ``graph.py:8528``) and
    ``internal_report:{child_id}:...`` (colon-delimited — the
    queue-side mint shape). Rejects ``internal_report:{child_id}2``
    and ``internal_report:{other}`` (child-boundary violations).
    """
    if not isinstance(source, str) or not source:
        return False
    exact = _source_prefix(child_id)
    return source == exact or source.startswith(exact + ":")


async def parent_history_has_internal_report(
    checkpointer: Any,
    parent_id: str,
    child_id: str,
    manager: Any | None = None,
) -> bool:
    """Parent-history-side PREFIX ledger check — cross-path dedup companion.

    F-2 (durability-f1-f2 / phase2 task 2.4). Reads the parent's
    ``get_instance_messages`` serialized dicts (``manager=None``
    skips the synthetic system-prompt injection — the sweep does
    not need it, per the W-5 ``manager=`` kwarg polarity check at
    ``daemon/persistence.py:312-330``). Returns ``True`` if any
    message's ``source`` is ``internal_report:{child_id}`` (exact)
    or starts with ``internal_report:{child_id}:`` (colon-
    delimited) — the boundary-safe match per
    ``decisions.md §14a`` (startswith semantics, realized with
    child-boundary safety).

    **STATE EXPLICITLY:** the pre-migration parent's absence of
    the ``source`` field degrades gracefully to "not yet
    reported" — the ``dict.get("source","")`` call returns
    ``""`` for messages without the field, and
    ``"".startswith(prefix)`` is ``False`` (no false-positive
    skip). This is the correct default: a parent that
    pre-dates the ``additional_kwargs.source`` surfacing has
    no report markers; the sweep's "no evidence" signal is
    the right one to act on.

    The ``source`` key is surfaced at
    ``daemon/utils.py:264-266`` (the same
    ``additional_kwargs.source`` surface that the prior cycle
    verified for the F-2 sweep's ledger check).

    Args:
        checkpointer: Shared checkpointer instance
            (``AsyncSqliteSaver`` or compatible). When ``None``,
            returns ``False`` (no evidence available).
        parent_id: The parent instance ID whose message history
            is scanned.
        child_id: The child instance ID (the source-suffix
            segment after ``internal_report:``).
        manager: Optional ``InstanceManager`` reference. When
            provided, ``get_instance_messages`` injects the
            synthetic system prompt (which is not relevant to
            the PREFIX scan). The sweep passes ``None`` to skip
            the injection per the W-5 polarity check.

    Returns:
        ``True`` if any parent-side message has a ``source``
        equal to ``internal_report:{child_id}`` or starting with
        ``internal_report:{child_id}:`` (the boundary-safe
        PREFIX match); ``False`` otherwise (no evidence, or
        checkpointer is ``None``, or pre-migration parent with
        no source field).
    """
    if checkpointer is None:
        return False

    # Use the module reference (not a local import) so unit tests
    # can patch ``daemon.persistence.get_instance_messages`` at the
    # module level. A local import would create a binding in this
    # function's namespace that bypasses the patch.
    messages = await _persistence.get_instance_messages(
        checkpointer, parent_id, manager=manager
    )
    for msg in messages:
        # NO role guard (iteration-2 blocker 2): the real delivery
        # evidence is the report-frame HumanMessage stamped at
        # ``daemon/graph.py:8528-8534`` — serialized role is
        # ``"user"`` (``daemon/utils.py:109`` role_map maps
        # ``human → user``). The prior assistant-only guard made
        # the ledger blind BY CONSTRUCTION to the evidence it
        # exists to find.
        # The ``source`` key is surfaced at daemon/utils.py:264-266
        # only when set on the source message; ``msg.get("source")``
        # returns ``None`` for pre-migration parents (no field) —
        # ``_is_child_report_source`` treats non-str as no match
        # (no false-positive skip).
        if _is_child_report_source(msg.get("source"), child_id):
            return True
    return False
