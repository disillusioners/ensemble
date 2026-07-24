"""Shared editor preference utility.

Lives in the service layer so both the settings router and the project
routers can import it without a service→router import inversion.

Mirrors ``language_utils.py`` exactly:
- Read uses ``record.meta_value`` (NOT ``record.metadata_value``) — R1
- ``set_metadata(project_id, key, value)`` opens its own Session internally
  and does NOT take a session parameter — R2. Mirror settings.py:58-59.
- ``get_metadata_record(session, project_id, key)`` DOES require a session
  argument — C6. Wrap in a ``with Session(repo.engine) as session:`` block.
"""
import asyncio
import logging

from sqlmodel import Session

from daemon import constants

logger = logging.getLogger(__name__)


async def get_editor_preference(repo) -> str:
    """Read the stored editor preference, or default ``EDITOR_DEFAULT``.

    Used by:
    - daemon/routers/settings.py (GET /api/settings/editor)

    Args:
        repo: A ``SQLModelProjectRepository`` instance.

    Returns:
        The preferred editor (``"builtin"`` or ``"vscode"``), or
        ``EDITOR_DEFAULT`` if unset, system project missing, or DB error.
    """
    if repo is None or constants.SYSTEM_DEFAULT_PROJECT_ID is None:
        return constants.EDITOR_DEFAULT
    try:
        # C6: get_metadata_record requires a session parameter.
        def _read():
            with Session(repo.engine) as session:
                record = repo.get_metadata_record(
                    session,
                    constants.SYSTEM_DEFAULT_PROJECT_ID,
                    constants.EDITOR_METADATA_KEY,
                )
                # R1: use record.meta_value (NOT record.metadata_value).
                if record and record.meta_value in constants.EDITOR_OPTIONS:
                    return str(record.meta_value)
                return constants.EDITOR_DEFAULT

        return await asyncio.to_thread(_read)
    except Exception as e:
        logger.warning(f"Failed to read editor preference: {e}")
        return constants.EDITOR_DEFAULT


async def set_editor_preference(repo, value: str) -> str:
    """Write the editor preference to the metadata KV.

    R2: ``set_metadata`` opens its own Session internally — do NOT pass a
    session parameter. Mirror ``settings.py:58-59`` exactly.

    Args:
        repo: A ``SQLModelProjectRepository`` instance.
        value: One of ``constants.EDITOR_OPTIONS``.

    Returns:
        The stored value.
    """
    # R2: set_metadata opens its own Session — NO session param passed.
    # Off the event loop so it cannot block other in-flight requests.
    await asyncio.to_thread(
        repo.set_metadata,
        constants.SYSTEM_DEFAULT_PROJECT_ID,
        constants.EDITOR_METADATA_KEY,
        value,
    )
    return value
