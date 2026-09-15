"""Shared UTC timestamp helpers for the job-queue subsystem.

Single source of truth for the timezone standard adopted by
``feature/fix-job-queue-timestamps-tz`` (Phase 3, 2026-09-15):

* **Application-layer values** are timezone-AWARE UTC datetimes.
* **Wire values** (API responses, TEXT columns) are aware ISO-8601
  strings carrying an explicit offset (``+00:00``).
* **Naive-column binds** (``timestamp without time zone`` columns on
  PostgreSQL; ``DATETIME`` on SQLite) carry NAIVE-UTC DIGITS — never
  an aware ``datetime``.

Why naive digits at the bind boundary (root defect DC-A): binding an
aware ``datetime`` into a ``timestamp without time zone`` column makes
PostgreSQL render the value in the SESSION ``TimeZone`` (production:
``Asia/Ho_Chi_Minh``, +07) and store the LOCAL digits — the stored
instant is silently shifted by the session offset and the offset
itself is dropped. Binding pre-stripped naive-UTC digits stores the
UTC wall-clock digits directly, immune to the session setting.

Legacy rows (written by the old aware binds) carry +07 local digits;
``to_utc_iso`` documents the assume-UTC policy for naive values —
legacy skewed-row repair is owned by the backfill proposal in
``.agents/shared/planning/fix-job-timestamps/backfill-proposal.md``,
NOT by this module.
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = [
    "coerce_to_aware_utc",
    "now_utc",
    "now_utc_iso",
    "now_utc_naive",
    "to_utc_iso",
]


def now_utc() -> datetime:
    """Return the current instant as a timezone-AWARE UTC ``datetime``.

    Use for application-layer values that stay in Python (sorting,
    comparisons, arithmetic). Do NOT bind the result into a naive
    timestamp column — use :func:`now_utc_naive` for that.
    """
    return datetime.now(timezone.utc)


def now_utc_naive() -> datetime:
    """Return the current instant as a NAIVE ``datetime`` holding UTC digits.

    The single sanctioned producer of bind values for naive timestamp
    columns (``timestamp without time zone`` / SQLite ``DATETIME``).
    The digits are the UTC wall-clock (``2026-09-15 14:09:59.500140``),
    so the stored value is independent of the PostgreSQL session
    ``TimeZone`` (DC-A fix).
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def now_utc_iso() -> str:
    """Return the current instant as an aware ISO-8601 string (``+00:00``).

    The single sanctioned producer of values for TEXT timestamp
    columns (``job_queue_items.created_at``, ``failed_at``,
    ``report_injections.delivered_at``, ``instances.updated_at``, …).
    Preserves the historical ``datetime.now(timezone.utc).isoformat()``
    format byte-for-byte (microseconds + ``+00:00`` offset) so new rows
    remain string-comparable with existing rows.
    """
    return datetime.now(timezone.utc).isoformat()


def to_utc_iso(value) -> str | None:
    """Serialize a timestamp value to an aware UTC ISO-8601 string.

    The single serialization boundary for timestamp reads destined to
    the wire. Contract:

    * ``None`` → ``None``.
    * aware ``datetime`` → ``value.astimezone(utc).isoformat()``.
    * naive ``datetime`` → **assume-UTC** (documented policy): the
      digits are interpreted as UTC wall-clock and the ``+00:00``
      offset is attached. Rows written by the pre-fix aware binds
      carry session-local (+07) digits and will render 7h-off under
      this policy until the backfill repairs them — that trade-off
      is adjudicated in the Phase-2 diagnosis (uniform policy beats
      per-site guessing; repair is centralized).
    * ``str`` → verbatim pass-through (TEXT columns already hold
      ISO strings; ``default_factory`` sites emit aware ISO).
    * anything else → ``None`` (defensive: unknown shapes degrade
      rather than raise).
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    return None


def coerce_to_aware_utc(value: datetime | None) -> datetime | None:
    """Coerce a ``datetime`` to aware-UTC for sort/compare contexts.

    Companion to :func:`to_utc_iso` for code paths that must COMPARE
    or SORT timestamps (Python refuses to mix naive and aware
    datetimes under ``<`` / ``>=``). Naive values follow the same
    documented assume-UTC policy as :func:`to_utc_iso` — legacy rows
    carrying +07 digits read 7h-off until backfill; new naive values
    ARE UTC digits by construction of :func:`now_utc_naive`.

    ``None`` passes through unchanged (callers decide the sentinel).
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
