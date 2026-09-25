"""Shared snapshot-create-enabled preference utility (R15 settings toggle).

Lives in the service layer so the settings router and the snapshot tool
surface can both import it without a service→router import inversion.

Mirrors ``editor_utils.py`` exactly:

- Read returns the stored ``on|off`` (or the unset-default ``OFF``
  fallback when no record exists — fail-closed opt-in rollout).
- ``set_metadata(project_id, key, value)`` opens its own Session
  internally and does NOT take a session parameter (R2, mirror
  ``editor_utils.py:78-85``).
- ``get_metadata_record(session, project_id, key)`` DOES require a
  session argument (C6). Wrap in a ``with Session(repo.engine) as
  session:`` block.

Unset / unknown value → ``False`` (R15 default OFF — fail-closed).
A snapshot tool that consults this MUST treat ``False`` as the
authoritative answer and refuse the call.

Rider (i) isolation: only ``snapshot_create`` consults this helper
(``is_snapshot_create_enabled`` in ``daemon/tools/snapshot_tools.py``).
``snapshot_search`` (read) and ``spawn_hot_instance`` (consumption) are
NEVER gated — they ship always-on. Toggle OFF = instant cold fallback
per R14 (the spawn succeeds but no snapshot is found because none is
being created).
"""
from __future__ import annotations

import asyncio
import logging

from sqlmodel import Session

from daemon import constants

logger = logging.getLogger(__name__)


async def get_snapshot_create_enabled(repo) -> bool:
    """Read the R15 settings toggle, with fail-closed default.

    Args:
        repo: A ``SQLModelProjectRepository`` instance.

    Returns:
        ``True`` when the SYSTEM_DEFAULT_PROJECT metadata record at
        ``constants.SNAPSHOT_CREATE_METADATA_KEY`` resolves to an
        enabled value (``"on"``, ``"true"``, ``"1"``, ``"yes"`` —
        case-insensitive); ``False`` otherwise (unset default, unknown
        value, missing system project, DB error).
    """
    if repo is None or constants.SYSTEM_DEFAULT_PROJECT_ID is None:
        return False
    try:
        def _read() -> bool:
            with Session(repo.engine) as session:
                record = repo.get_metadata_record(
                    session,
                    constants.SYSTEM_DEFAULT_PROJECT_ID,
                    constants.SNAPSHOT_CREATE_METADATA_KEY,
                )
            if record is None:
                return False
            stored = getattr(record, "meta_value", None)
            if not stored:
                return False
            return _coerce_to_bool(stored)

        return await asyncio.to_thread(_read)
    except Exception as exc:  # pragma: no cover — defensive belt
        logger.warning(
            f"Failed to read snapshot_create_enabled preference: {exc}"
        )
        return False


async def set_snapshot_create_enabled(repo, enabled: bool) -> bool:
    """Persist the R15 settings toggle.

    Mirrors ``editor_utils.set_editor_preference``: delegates to
    ``repo.set_metadata`` which opens its own Session. A None return
    (system default project missing) is re-raised so the caller surfaces
    a 503 rather than silently reporting success.

    Args:
        repo: A ``SQLModelProjectRepository`` instance.
        enabled: ``True`` (writes ``"on"``) or ``False`` (writes
            ``"off"``). The stored value uses the literal string form so
            the persistence layer never has to guess a boolean encoding.

    Returns:
        The boolean value actually stored (``True`` when ``enabled`` was
        ``True``; ``False`` otherwise — the persistence layer does not
        roundtrip).

    Raises:
        RuntimeError: ``repo.set_metadata`` returned ``None`` (system
            default project row is missing).
    """
    stored = "on" if enabled else "off"
    result = await asyncio.to_thread(
        repo.set_metadata,
        constants.SYSTEM_DEFAULT_PROJECT_ID,
        constants.SNAPSHOT_CREATE_METADATA_KEY,
        stored,
    )
    if result is None:
        raise RuntimeError(
            "Failed to set snapshot_create_enabled preference: metadata "
            "write returned None (system default project missing?)"
        )
    return enabled


def _coerce_to_bool(stored: str) -> bool:
    """Parse a stored metadata value to a bool (fail-closed default ``False``).

    Accepted truthy (case-insensitive, stripped): ``on``, ``true``, ``1``,
    ``yes``. Anything else → ``False``. The empty string is explicitly
    rejected so a blank accidental write falls back to OFF rather than
    resetting to ON.
    """
    cleaned = str(stored or "").strip().lower()
    if not cleaned:
        return False
    if cleaned in constants.SNAPSHOT_CREATE_ENABLED_VALUES:
        return True
    return False
