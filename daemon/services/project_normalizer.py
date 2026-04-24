"""Project ID normalization utility for system default project handling."""

from __future__ import annotations

from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID


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
    if SYSTEM_DEFAULT_PROJECT_ID is None:
        raise RuntimeError(
            "normalize_project_id() called before system default project was initialized"
        )

    # Handle None
    if project_id is None:
        return SYSTEM_DEFAULT_PROJECT_ID

    # Normalize and check for empty/whitespace-only
    normalized = project_id.strip()

    # Handle empty string
    if normalized == "":
        return SYSTEM_DEFAULT_PROJECT_ID

    # Handle null/none string variants (case-insensitive)
    lower = normalized.lower()
    if lower in ("null", "none"):
        return SYSTEM_DEFAULT_PROJECT_ID

    # Return the original input (preserving original case, not stripped)
    return project_id
