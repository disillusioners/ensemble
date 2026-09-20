"""Shared completion-content helpers.

Single canonical implementation for extracting an instance's last assistant
message from its LangGraph checkpoint history. Consumers:

- ``ChildReportsService._get_last_assistant_message`` — wraps the content with
  the instance report prefix for child completion reports.
- ``JobFeedbackObserver`` — uses the raw content as the job ``result_summary``
  on terminal job transitions (fix for empty "Result:" in watcher
  notifications and ``result_summary: null`` in job_get).
"""

import logging
from datetime import datetime, timezone
from typing import Any

from ..persistence import get_instance_messages

logger = logging.getLogger(__name__)


async def get_last_assistant_message(
    checkpointer: Any,
    instance_id: str,
) -> tuple[str | None, str | None]:
    """Get the instance's last non-empty assistant message.

    This is the single canonical extraction used for both completion reports
    and job result summaries. Callers that need the report prefix wrap the
    returned content themselves (see ChildReportsService).

    Args:
        checkpointer: Shared checkpointer instance (AsyncSqliteSaver or
            compatible). When None, returns (None, None).
        instance_id: The instance ID to get messages from.

    Returns:
        Tuple of (content, created_at) for the last non-empty assistant
        message. ``created_at`` is the raw checkpoint timestamp string (ISO
        8601) of when the message first appeared, or None when unavailable.
        Either element may be None independently.
    """
    if checkpointer is None:
        return None, None

    messages = await get_instance_messages(checkpointer, instance_id)

    # Find the last assistant message with non-empty content
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            created_at = msg.get("created_at")
            if isinstance(content, str) and content.strip():
                return content.strip(), created_at

    return None, None


async def get_last_assistant_timestamp(
    checkpointer: Any,
    instance_id: str,
) -> str | None:
    """Get the timestamp of the instance's last AI message regardless of content.

    Unlike ``get_last_assistant_message`` (which skips empty-content AI messages),
    this returns the timestamp of ANY assistant message — even one with no
    visible content (e.g., pure tool-call turns).

    Used by the root-completion gate to close the empty-final-turn wedge: a
    parent instance may legitimately respond to a child completion report with
    only tool calls (no content), and the gate must treat that as a fresh
    response, not as "the parent never replied."

    Args:
        checkpointer: Shared checkpointer instance (AsyncSqliteSaver or
            compatible). When None, returns None.
        instance_id: The instance ID to query.

    Returns:
        Raw checkpoint timestamp string (ISO 8601) of the last assistant
        message regardless of content, or None when unavailable.
    """
    if checkpointer is None:
        return None

    messages = await get_instance_messages(checkpointer, instance_id)

    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            return msg.get("created_at")

    return None


def parse_checkpoint_ts(ts: str | None) -> datetime | None:
    """Parse a checkpoint timestamp string into a tz-aware UTC datetime.

    Returns None when the value is missing or unparseable.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def event_created_at_as_utc(dt: datetime) -> datetime:
    """Normalize a DB-read datetime to tz-aware UTC.

    SQLite drops the tzinfo on round-trip; naive values are UTC by
    construction (all writers use datetime.now(timezone.utc)).
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
