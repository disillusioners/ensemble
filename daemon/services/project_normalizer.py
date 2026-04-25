"""Project ID normalization utility for system default project handling."""

from __future__ import annotations

import daemon.constants


def normalize_project_id(project_id: str | None) -> str:
    """Normalize project_id, replacing None/empty with system default.

    Args:
        project_id: The project ID to normalize. Can be None, empty string,
                    or a valid project ID string.

    Returns:
        The system default project ID if input is None or empty,
        otherwise the input unchanged.

    Raises:
        RuntimeError: If SYSTEM_DEFAULT_PROJECT_ID is None (called before startup).
    """
    system_default_project_id = daemon.constants.SYSTEM_DEFAULT_PROJECT_ID
    if system_default_project_id is None:
        raise RuntimeError(
            "normalize_project_id() called before system default project was initialized"
        )

    # Handle None
    if project_id is None:
        return system_default_project_id

    # Normalize and check for empty/whitespace-only
    normalized = project_id.strip()

    # Handle empty string
    if normalized == "":
        return system_default_project_id

    # Handle null/none string variants (case-insensitive)
    lower = normalized.lower()
    if lower in ("null", "none"):
        return system_default_project_id

    # Return the original input (preserving original case, not stripped)
    return project_id
