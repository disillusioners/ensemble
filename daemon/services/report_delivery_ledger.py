"""Parent-history-side PREFIX ledger for the F-2 RDRS chain.

F-2 (durability-f1-f2 / phase2, task 2.4). The new
``parent_history_has_internal_report`` helper is the
parent-history-side companion to the queue-side
``MessageQueueRepository.find_wake_already_delivered_evidence``
(task 2.3). Both are PREFIX-ledger checks on
``source LIKE 'internal_report:{child_id}:%'`` — the operative
cross-path dedup (per ``decisions.md §14a`` W-1 LOCKED to
fallback (ii); the exact-id equality at
``child_reports.py:3498-3507`` is natural-path-only).

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

SQLModel select convention: the new module uses
``sqlmodel.select`` (per W-5; declared in the file header).
"""

from __future__ import annotations

import logging
from typing import Any

from ..persistence import get_instance_messages

logger = logging.getLogger(__name__)


def _source_key(child_id: str) -> str:
    """The PREFIX-match key for the delivery-evidence scan.

    The exact source shape produced by the natural completion
    path is ``internal_report:{child_id}:{message_id}`` (the
    PREFIX is everything up to and including the second colon).
    PREFIX-match (``startswith``) catches ANY anchor the
    delivering path used — the design review (B3 per
    ``decisions.md §12a``) verified that the anchor id is
    stable-but-different across delivery paths and the
    PREFIX-match is the only correct cross-path check.
    """
    return f"internal_report:{child_id}:"


async def parent_history_has_internal_report(
    checkpointer: Any,
    parent_id: str,
    child_id: str,
    manager: Any | None = None,
) -> bool:
    """Parent-history-side PREFIX ledger check — cross-path dedup companion.

    F-2 (durability-f1-f2 / phase2 task 2.4). Reads the parent's
    ``get_instance_messages`` serialized dicts (the manager is
    passed in to skip the synthetic system-prompt injection —
    the sweep does not need it, per the W-5 ``manager=`` kwarg
    polarity check at ``daemon/persistence.py:312-330``: the
    ``manager=None`` default skips the injection). Returns
    ``True`` if any message has
    ``dict.get("source","").startswith(f"internal_report:{child_id}")``.

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
        starting with ``internal_report:{child_id}:`` (the
        PREFIX match); ``False`` otherwise (no evidence, or
        checkpointer is ``None``, or pre-migration parent with
        no source field).
    """
    if checkpointer is None:
        return False

    messages = await get_instance_messages(
        checkpointer, parent_id, manager=manager
    )
    prefix = _source_key(child_id)
    for msg in messages:
        # The ``source`` key is surfaced at daemon/utils.py:264-266
        # only when set on the source message; ``dict.get("source","")``
        # degrades to ``""`` for pre-migration parents (no field).
        source = msg.get("source", "")
        if isinstance(source, str) and source.startswith(prefix):
            return True
    return False
