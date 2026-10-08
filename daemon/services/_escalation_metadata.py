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

Why this lives in its own module:
  * Avoids a daemon.services.instance_messaging -> daemon.graph cycle
    on top of the already-cyclic import path (the messaging service is
    imported by graph).
  * The metadata reader is consumed by ``_maybe_precall_compact_95``,
    which lives in graph.py; the writer lives in the proactive trigger
    in instance_messaging. A tiny shared module is the cleanest seam.

C2 (REVIEWER iteration-3 fix) — read/write path:
  The previous iteration used ``getattr(instance, "metadata")`` and
  ``setattr(instance, "metadata", dict)``. On a real SQLModel
  ``Instance`` row, the class-level ``metadata`` attribute is the
  SQLAlchemy ``MetaData()`` instance (a class-level registry of
  schema tables), NOT the JSONB column. The column is exposed as
  the Python attribute ``instance_metadata`` (and persisted as the
  DB column ``metadata`` per the ``sa_column=Column("metadata",
  JSONBType)`` declaration). The pre-fix code:
    1. Always read the SQLAlchemy ``MetaData()`` registry on a real
       row (the ``isinstance(md, dict)`` guard never matched).
    2. Wrote a dict onto the in-memory ``metadata`` attribute,
       which the repository's ``update`` would either reject
       (rejection guard at ``repository.py:1272-1299``) or silently
       overwrite the MetaData class object. The JSONB column was
       never touched.
  The fix:
    * Reader: read ``instance_metadata`` (the real JSONB column).
      Fall back to ``metadata`` for legacy test fixtures that
      construct mock instances with a dict-typed ``metadata``
      attribute (the regression guard in
      ``tests/unit/test_compaction_never_blocked.py::TestHEscalationAfterNConsecutiveSkips``
      uses a dedicated ``_FakeRepo`` with the dict-typed ``metadata``
      attribute to exercise the writer logic without an SQLModel
      dependency).
    * Writer: route through the dedicated repository helpers
      ``set_metadata`` / ``delete_metadata`` (dialect-aware atomic
      JSONB / json_set UPDATE; the same helpers the watchover
      activation path uses for the same 3-4 key bundle). The
      generic ``update`` method REJECTS the ``instance_metadata``
      kwarg by design (rejection guard at
      ``repository.py:1272-1299``) — using it would either raise
      a ValueError or fall through to the legacy ``setattr``
      MetaData-clobbering path. The atomic helpers are the
      single correct write path.
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


def _read_instance_metadata_dict(instance: Any) -> dict | None:
    """Read the JSONB ``instance_metadata`` column off an
    ``Instance`` row, falling back to the legacy ``metadata``
    attribute (test fixtures / MagicMock with a dict-typed
    ``metadata`` attr).

    C2 fix: the canonical read is ``getattr(instance,
    "instance_metadata")`` — the SQLAlchemy-mapped column on
    real rows. The fallback exists for the test suite (which
    constructs ``_FakeRepo`` with a dict-typed ``metadata``
    attribute — the same shape the pre-fix code accidentally
    relied on). The fallback path is gated on a real-Dict
    result; on a real row where ``metadata`` is a SQLAlchemy
    ``MetaData()`` class object, ``isinstance(md, dict)`` is
    False and the fallback returns ``None`` (the read is
    considered "not present", which is the safe default).
    """
    md = getattr(instance, "instance_metadata", None)
    if isinstance(md, dict):
        return md
    legacy = getattr(instance, "metadata", None)
    if isinstance(legacy, dict):
        # Test-fixture path: ``metadata`` is a dict (NOT the
        # SQLAlchemy MetaData class object). Use it. This is the
        # only path the regression guard in
        # tests/unit/test_compaction_never_blocked.py
        # exercises; real rows never land here.
        return legacy
    return None


def is_proactive_escalation_active(
    instance: Any, *, now_iso: str | None = None
) -> bool:
    """Return ``True`` when the proactive-trigger escalation is
    active for this instance.

    Args:
        instance: An ``Instance`` row (or any object with an
            ``instance_metadata`` attribute that is a dict).
            The prod caller passes the real DB row; the test
            suite passes a ``_FakeRepo`` row with a dict-typed
            ``metadata`` attribute.
        now_iso: Optional ISO timestamp for the "now" comparison
            (test-injection). When ``None``, uses
            :func:`datetime.now(timezone.utc).isoformat`.

    Returns:
        ``True`` when the JSONB column's
        ``compaction_escalation_until`` is set to a future
        timestamp. ``False`` otherwise (including the missing-
        key case).
    """
    md = _read_instance_metadata_dict(instance)
    if md is None:
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
    """Best-effort write of the escalation metadata to the
    ``instance_metadata`` JSONB column. C2 fix: routes through
    the repository's dedicated atomic helpers
    (``set_metadata_many`` / ``set_metadata``); the legacy
    ``update(instance_id, instance_metadata=payload)`` call is
    REJECTED by the repository's write-guard at
    ``repository.py:1272-1299`` (and would have clobbered the
    SQLAlchemy ``MetaData()`` class object via
    ``setattr(instance, "metadata", ...)`` — the pre-fix bug).

    The atomic helpers are dialect-aware (PostgreSQL
    ``jsonb_set`` / SQLite ``json_set``) and the watchover
    activation path uses the same pattern for its 3-4 key
    bundle. The bundled 4-key write is atomic on the column
    (one UPDATE; concurrent writers don't see torn state).

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
    try:
        if hasattr(repository, "set_metadata_many"):
            # Preferred atomic path. The dialect-aware
            # ``jsonb_set`` / ``json_set`` UPDATE in one
            # statement preserves other column keys.
            repository.set_metadata_many(instance_id, payload)
        elif hasattr(repository, "set_metadata"):
            # Fallback for repos that only expose the single-key
            # helper. N round-trips; the keys are still all
            # written (no torn state on a single connection).
            for k, v in payload.items():
                repository.set_metadata(instance_id, k, v)
        else:
            # No atomic helper exposed. The test-fixture
            # ``_FakeRepo`` (see TestHEscalationAfterNConsecutiveSkips)
            # takes this path: its surface is a plain ``get`` +
            # ``update(instance_id, metadata=payload)`` shim.
            # The shim's ``update`` accepts ``metadata=`` as a
            # kwarg (test-only contract) and the test asserts
            # the writer saw the dict. In production this
            # branch is unreachable (the canonical
            # InstanceRepository exposes set_metadata_many),
            # but the defensive fallback keeps the seam
            # non-fatal on a missing API.
            row = repository.get(instance_id) if hasattr(
                repository, "get"
            ) else None
            if row is None:
                return
            md = _read_instance_metadata_dict(row)
            if md is None:
                md = {}
            else:
                md = dict(md)
            md.update(payload)
            try:
                repository.update(instance_id, metadata=md)
            except TypeError:
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
    """Best-effort clear of the escalation metadata. C2 fix:
    routes through the repository's dedicated atomic helper
    (``delete_metadata``) — dialect-aware and concurrent-safe.
    Falls back to the test-fixture ``_FakeRepo`` shim path
    (dict-typed ``metadata`` attribute) when the atomic helper
    is not exposed.
    """
    try:
        if hasattr(repository, "delete_metadata"):
            # Single-key delete is enough (the rest of the
            # bundle is observable-only; if any of the
            # companion keys leaked they're harmless and
            # will be overwritten on the next escalation).
            # We still delete all four for tidiness.
            for k in (
                ESCALATION_UNTIL_KEY,
                ESCALATION_THRESHOLD_KEY,
                ESCALATION_PREV_MESSAGES_KEY,
                ESCALATION_SKIP_COUNT_KEY,
            ):
                try:
                    repository.delete_metadata(instance_id, k)
                except Exception:
                    # Idempotent: a missing key is a no-op.
                    pass
        else:
            # Test-fixture shim path. Same dict-typed
            # ``metadata`` attribute, full-merge clear.
            if not hasattr(repository, "get") or not hasattr(
                repository, "update"
            ):
                return
            row = repository.get(instance_id)
            if row is None:
                return
            md = _read_instance_metadata_dict(row)
            if md is None:
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
