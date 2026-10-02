"""Shared user-timezone preference utility.

Lives in the service layer so the settings router, the instance lifecycle
service, and the scheduling service can all import it without a
service→router import inversion.

Mirrors :mod:`daemon.services.language_utils` (same table, same singleton
shape): the preference is a GLOBAL user setting — one row in
``project_metadata_records`` keyed ``(SYSTEM_DEFAULT_PROJECT_ID, key)`` —
storing the raw IANA timezone name (e.g. ``"Asia/Bangkok"``). No user/auth
identity layer exists, so there is nothing else to key it on.

Unset semantics: unlike the language preference (whose unset sentinel is
the string ``"Auto"``), an unset timezone returns ``None`` — there is NO
default value baked in, because "unset" means "fall through the tz
resolution chain" (env default → host-local → UTC) wherever the preference
is consumed. A stored value that fails IANA validation at read time is
treated exactly like unset (no crash).
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlmodel import Session

from daemon import constants
from daemon.util.tz import _validate_iana

logger = logging.getLogger(__name__)


def get_user_timezone_preference(project_repo) -> str | None:
    """Get the stored user timezone preference, or ``None`` when unset.

    Used by:
    - daemon/routers/settings.py (GET endpoint)
    - daemon/services/instance_lifecycle.py (spawn + restore paths)
    - daemon/services/scheduling_service.py (tz resolution chain rung)

    Args:
        project_repo: A SQLModelProjectRepository instance.

    Returns:
        The stored IANA timezone name, or ``None`` when unset, invalid at
        read time, system project missing, repo missing, or DB error.
        ``None`` is the sentinel for "no preference — fall through the
        tz chain"; it is NEVER a default timezone.
    """
    if project_repo is None or constants.SYSTEM_DEFAULT_PROJECT_ID is None:
        return None
    try:
        with Session(project_repo.engine) as session:
            record = project_repo.get_metadata_record(
                session, constants.SYSTEM_DEFAULT_PROJECT_ID, constants.USER_TIMEZONE_METADATA_KEY
            )
            if record and record.meta_value:
                stored = str(record.meta_value)
                # Invalid-at-read → treat as unset (fall through silently,
                # never crash the spawn/restore/scheduling paths on a bad row).
                if _validate_iana(stored):
                    return stored
                logger.warning(
                    "Stored user timezone %r is not a valid IANA name; treating as unset",
                    stored,
                )
    except Exception as e:
        logger.warning(f"Failed to read user timezone preference: {e}")
    return None


def format_utc_offset(offset) -> str:
    """Format a ``datetime.timedelta`` UTC offset as ``+07:00`` style."""
    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"{sign}{hours:02d}:{minutes:02d}"


def current_utc_offset(tz_name: str) -> str | None:
    """The zone's CURRENT UTC offset (DST-correct at read time), e.g. ``+07:00``.

    Display-only helper for the settings GET surface. Returns ``None`` when
    ``tz_name`` is not a valid IANA name (mirrors the read-time treat-as-
    unset posture — display code must never crash on a bad stored value).
    """
    if not tz_name:
        return None
    try:
        zone = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 — invalid stored names are data, not bugs
        return None
    return format_utc_offset(datetime.now(timezone.utc).astimezone(zone).utcoffset())
