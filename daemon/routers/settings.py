"""Settings API endpoints."""
import asyncio
import logging
import re
from fastapi import APIRouter, HTTPException

from daemon.repositories import SQLModelProjectRepository
from daemon.services.language_utils import get_language_preference, LANGUAGE_METADATA_KEY, DEFAULT_LANGUAGE
from daemon import constants
from .schemas import LanguagePreferenceResponse, LanguagePreferenceUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

_project_repo: SQLModelProjectRepository | None = None


def get_project_repository() -> SQLModelProjectRepository:
    if _project_repo is None:
        raise HTTPException(status_code=503, detail={"error": "Project repository not initialized"})
    return _project_repo


def set_project_repository(repo: SQLModelProjectRepository) -> None:
    global _project_repo
    _project_repo = repo


@router.get("/language", response_model=LanguagePreferenceResponse)
async def get_language():
    """Get the current language preference."""
    # ``get_language_preference`` does a sync SQLAlchemy session read; off the
    # event loop so it cannot block other in-flight requests.
    language = await asyncio.to_thread(get_language_preference, _project_repo)
    return LanguagePreferenceResponse(language=language)


@router.put("/language", response_model=LanguagePreferenceResponse)
async def set_language(request: LanguagePreferenceUpdate):
    """Set the language preference."""
    if not request.language or not request.language.strip():
        raise HTTPException(status_code=422, detail="language must be a non-empty string")
    # Defense-in-depth: strip control characters (newlines, tabs, etc.) so that
    # any payload that slips past the schema's regex cannot inject text into
    # downstream system prompts (see C1 prompt-injection fix).
    cleaned_language = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", request.language).strip()
    if not cleaned_language:
        raise HTTPException(
            status_code=422,
            detail="Language must contain at least one non-whitespace character",
        )
    repo = get_project_repository()  # raises 503 if not initialized
    if constants.SYSTEM_DEFAULT_PROJECT_ID is None:
        raise HTTPException(status_code=503, detail="System default project not initialized")
    # ``repo.set_metadata`` opens a sync SQLAlchemy session and commits; off the
    # event loop so it cannot block other in-flight requests.
    await asyncio.to_thread(
        repo.set_metadata, constants.SYSTEM_DEFAULT_PROJECT_ID, LANGUAGE_METADATA_KEY, cleaned_language
    )
    return LanguagePreferenceResponse(language=cleaned_language)