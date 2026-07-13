"""Shared language preference utility.

Lives in the service layer so both the settings router and the instance
lifecycle service can import it without a service→router import inversion.
"""
import logging
from sqlmodel import Session

from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID

logger = logging.getLogger(__name__)

LANGUAGE_METADATA_KEY = "user_language"
# "Auto" means "no preference — skip language injection and the language_check
# node entirely". The LLM is free to reply in whatever language matches the
# user's input.
DEFAULT_LANGUAGE = "Auto"


def get_language_preference(project_repo) -> str:
    """Get the stored language preference, or default 'Auto'.
    
    Used by:
    - daemon/routers/settings.py (GET endpoint)
    - daemon/services/instance_lifecycle.py (spawn + restore paths)
    
    Args:
        project_repo: A SQLModelProjectRepository instance.
    
    Returns:
        The preferred language string, or 'Auto' if unset, system
        project missing, or DB error. 'Auto' is the sentinel for
        "no preference — skip language handling".
    """
    if project_repo is None or SYSTEM_DEFAULT_PROJECT_ID is None:
        return DEFAULT_LANGUAGE
    try:
        with Session(project_repo.engine) as session:
            record = project_repo.get_metadata_record(
                session, SYSTEM_DEFAULT_PROJECT_ID, LANGUAGE_METADATA_KEY
            )
            if record and record.meta_value:
                return str(record.meta_value)
    except Exception as e:
        logger.warning(f"Failed to read language preference: {e}")
    return DEFAULT_LANGUAGE