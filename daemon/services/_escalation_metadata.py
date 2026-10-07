"""Per-instance proactive-compaction escalation metadata seam.

Phase 1 of the COMPACTION NEVER-BLOCKED fix (Verdict A framing,
fix/compaction-never-blocked commission, 2026-10-07).

When the proactive trigger has skipped N consecutive times (status-reject
or non-quiescent) AND the context keeps growing, the proactive trigger
writes a sticky ``compaction_escalation_until`` field to the instance
row's ``instance_metadata`` JSONB column. The 95% pre-call reactive
hook (``daemon/graph.py::_maybe_precall_compact_95``) reads this field
and lowers its trigger from 0.95 to 0.80 for this instance until the
escalation is cleared (by a successful compaction, or by the timestamp
expiring).

The escalation is a SOFT mechanism — it does NOT force-compact. It
WIDENS the reactive gate so an injected-heavy instance can shrink
before the 95% band is reached. This is the answer to the Phase-1
report's C-finding ("no shrink-before-the-line path").

Why this lives in its own module:
  * Avoids a daemon.services.instance_messaging -> daemon.graph cycle
    on top of the already-cyclic import path (the messaging service is
    imported by graph).
  * The metadata reader is consumed by ``_maybe_precall_compact_95``,
    which lives in graph.py; the writer lives in the proactive trigger
    in instance_messaging. A tiny shared module is the cleanest seam.
"""
from __future__ import annotations

import logging
from typing import Any
from datetime import datetime, timezone


logger = logging.getLogger(__name__)


#: Metadata key for the per-instance sticky escalation window. Lives on
#: ``instance_metadata.compaction_escalation_until`` (ISO timestamp).
#: The 95% pre-call hook reads this on every invoke and lowers its
#: trigger ratio when the timestamp is in the future.
ESCALATION_UNTIL_KEY = "compaction_escalation_until"
#: Companion metadata: the threshold (N) at which the escalation was
#: triggered. Stored for observability + future tuning; not consumed by
#: the 95% hook (the hook only checks the timestamp).
ESCALATION_THRESHOLD_KEY = "compaction_escalation_threshold"
#: Companion metadata: the LAST SEEN message count at the time of
#: escalation. Stored for observability + the post-mortem
#: "was-context-growing" check.
ESCALATION_PREV_MESSAGES_KEY = "compaction_escalation_prev_messages"
#: Companion metadata: the consecutive skip count at the time of
#: escalation. Stored for observability.
ESCALATION_SKIP_COUNT_KEY = "compaction_escalation_skip_count"


def is_proactive_escalation_active(
    instance: Any, *, now_iso: str | None = None
) -> bool:
    """Return ``True`` when the proactive-trigger escalation is
    active for this instance.

    Args:
        instance: An ``Instance`` row (or any object with a
            ``metadata`` attribute that is a dict-like). The reader
            accepts the row directly — the test suite passes a
            ``MagicMock(spec=Instance)`` and the prod caller passes
            the real DB row.
        now_iso: Optional ISO timestamp for the "now" comparison
            (test-injection). When ``None``, uses
            :func:`datetime.now(timezone.utc).isoformat`.

    Returns:
        ``True`` when ``instance.metadata[ESCALATION_UNTIL_KEY]`` is
        set to a future timestamp. ``False`` otherwise (including
        the missing-key case).
    """
    md = getattr(instance, "metadata", None)
    if not isinstance(md, dict):
        return False
    until = md.get(ESCALATION_UNTIL_KEY)
    if not until:
        return False
    if now_iso is None:
        now_iso = datetime.now(timezone.utc).isoformat()
    try:
        # ISO timestamps are lexicographically comparable in UTC.
        return str(until) > str(now_iso)
    except Exception:  # pragma: no cover — defensive
        return False


def set_proactive_escalation_metadata(
    repository: Any,
    instance_id: str,
    *,
    until: str,
    threshold: int,
    prev_message_count: int,
    skip_count: int,
) -> None:
    """Best-effort write of the escalation metadata to the instance
    row. The repository contract is the standard
    ``update_metadata`` / ``set_metadata`` / ``get`` + write pattern
    used elsewhere in the codebase; we tolerate a missing method by
    silently no-op'ing (the proactive trigger must complete even on
    a DB hiccup).

    The write is a SHALLOW MERGE on the ``metadata`` JSONB column —
    other metadata keys are preserved. If the repository only supports
    full-replace ``metadata`` writes, the caller must read+merge+write
    themselves; that case is handled here by trying a few common method
    names in order.

    Args:
        repository: The instance repository (typically
            ``manager._instance_repository``).
        instance_id: Target instance.
        until: ISO timestamp until which the escalation is active.
        threshold: N at which the escalation triggered.
        prev_message_count: Last-seen message count for observability.
        skip_count: Consecutive skip count for observability.
    """
    payload = {
        ESCALATION_UNTIL_KEY: until,
        ESCALATION_THRESHOLD_KEY: threshold,
        ESCALATION_PREV_MESSAGES_KEY: prev_message_count,
        ESCALATION_SKIP_COUNT_KEY: skip_count,
    }
    # The repository contract: read the row, shallow-merge the
    # metadata field, write back. The standard repository surface
    # is ``get`` + ``update`` (the only contract callers rely on
    # for mutable row updates in the codebase). Defensive try/except
    # so a missing method or transient DB error never propagates
    # into the proactive trigger.
    try:
        if not hasattr(repository, "get") or not hasattr(repository, "update"):
            logger.debug(
                "[Compaction][escalation] repository lacks get/update; "
                "no-op write for instance=%s",
                instance_id[:8],
            )
            return
        row = repository.get(instance_id)
        if row is None:
            return
        md = getattr(row, "metadata", None)
        if not isinstance(md, dict):
            md = {}
        else:
            md = dict(md)  # shallow copy; do not mutate row
        md.update(payload)
        # The repository's ``update`` accepts kwargs matching the
        # row's mutable fields. ``metadata`` is one of them.
        try:
            repository.update(instance_id, metadata=md)
        except TypeError:
            # Some repository impls use a positional ``row`` arg
            # instead of kwargs.
            row.metadata = md
            repository.update(row)
    except Exception as e:  # pragma: no cover — defensive
        logger.debug(
            "[Compaction][escalation] failed to write metadata for "
            "instance=%s: %s",
            instance_id[:8], e,
        )


def clear_proactive_escalation_metadata(
    repository: Any, instance_id: str
) -> None:
    """Best-effort clear of the escalation metadata. Defensive: a
    missing key or a DB hiccup is silently no-op'd. The escalation
    has a 1-hour TTL; if the clear is missed, the next skip will
    re-set (or the TTL will expire).
    """
    try:
        if not hasattr(repository, "get") or not hasattr(repository, "update"):
            return
        row = repository.get(instance_id)
        if row is None:
            return
        md = getattr(row, "metadata", None)
        if not isinstance(md, dict):
            return
        if ESCALATION_UNTIL_KEY not in md:
            return  # already clear
        md = dict(md)
        for k in (
            ESCALATION_UNTIL_KEY,
            ESCALATION_THRESHOLD_KEY,
            ESCALATION_PREV_MESSAGES_KEY,
            ESCALATION_SKIP_COUNT_KEY,
        ):
            md.pop(k, None)
        try:
            repository.update(instance_id, metadata=md)
        except TypeError:
            row.metadata = md
            repository.update(row)
    except Exception:  # pragma: no cover — defensive
        pass


__all__ = [
    "ESCALATION_UNTIL_KEY",
    "ESCALATION_THRESHOLD_KEY",
    "ESCALATION_PREV_MESSAGES_KEY",
    "ESCALATION_SKIP_COUNT_KEY",
    "is_proactive_escalation_active",
    "set_proactive_escalation_metadata",
    "clear_proactive_escalation_metadata",
]
