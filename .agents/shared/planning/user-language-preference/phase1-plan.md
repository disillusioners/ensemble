# Phase 1: Backend API + Storage for Language Preference

## Objective
Create a REST API endpoint (`GET/PUT /api/settings/language`) that stores and retrieves the user's preferred language. Storage uses the existing `ProjectMetadataRecord` table on the system default project, keyed as `"user_language"`. Default value is `"English"`. A shared utility module `daemon/services/language_utils.py` provides `get_language_preference()` for both the router and the lifecycle service (fixing W3 — service→router import inversion).

## Coupling
- **Depends on**: None
- **Coupling type**: — (root phase)
- **Shared files with other phases**: 
  - `daemon/services/language_utils.py` — Phase 2 imports `get_language_preference()` from here
  - `daemon/routers/settings.py` — Phase 3 frontend calls the API defined here
- **Shared APIs/interfaces**: `get_language_preference() -> str` (used by Phase 2)
- **Why this coupling**: Phase 2 needs to read the stored language at spawn time. The function lives in the service layer (`language_utils.py`), not the router layer, to avoid import inversion.

## Context
- No existing settings/preferences endpoint exists in the codebase
- `ProjectMetadataRecord` table already supports key-value storage per project with dialect-aware upsert
- `SYSTEM_DEFAULT_PROJECT_ID` is set at startup in `daemon/api.py:467`
- Router pattern: module-level `router = APIRouter(prefix="...", tags=[...])`, registered in `daemon/routers/__init__.py`, included in `daemon/api.py`
- `SQLModelProjectRepository` has `get_metadata_record(session, project_id, key)` and `set_metadata(project_id, key, value)` methods

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create shared utility module | New `daemon/services/language_utils.py` with `get_language_preference(project_repo) -> str`. Reads `SYSTEM_DEFAULT_PROJECT_ID` metadata key `"user_language"`. Returns `"English"` if not set, system project missing, or DB error. This module is imported by BOTH the router and the lifecycle service — no import inversion | `daemon/services/language_utils.py` (NEW) |
| 2 | Create settings router module | New `daemon/routers/settings.py` with `APIRouter(prefix="/settings", tags=["settings"])`. Two endpoints: `GET /settings/language` and `PUT /settings/language`. Router imports `get_language_preference()` from `daemon.services.language_utils` | `daemon/routers/settings.py` (NEW) |
| 3 | Create Pydantic request/response schemas | `LanguagePreferenceResponse(language: str)`, `LanguagePreferenceUpdate(language: str)`. Validate language is non-empty string, max 50 chars | `daemon/routers/schemas.py` (MODIFY — add schemas) |
| 4 | Implement GET endpoint | Call `get_language_preference(project_repo)`. Return `{"language": "English"}` if not set | `daemon/routers/settings.py` |
| 5 | Implement PUT endpoint | Call `project_repo.set_metadata(system_project_id, "user_language", language)`. Return updated preference | `daemon/routers/settings.py` |
| 6 | Wire up router dependency | Add `_project_repo` module-level variable + `get_project_repository()` dependency (same pattern as `daemon/routers/projects.py:28-42`). Add `set_project_repository()` for startup wiring | `daemon/routers/settings.py` |
| 7 | Register router in app | Export `settings_router` from `daemon/routers/__init__.py`. Add `api_router.include_router(settings_router)` in `daemon/api.py` after existing routers | `daemon/routers/__init__.py` (MODIFY), `daemon/api.py` (MODIFY) |
| 8 | Wire up repository at startup | In `daemon/api.py` lifespan, after `set_project_repository(repo)` for projects router, also call `set_settings_project_repo(repo)` | `daemon/api.py` (MODIFY) |
| 9 | Write tests | Test GET returns default "English" when unset. Test PUT stores and GET retrieves. Test invalid language (empty string) returns 422. Test system project not found returns default. Test `get_language_preference()` handles DB errors gracefully | `tests/test_settings_api.py` (NEW) |

## Key Files

### NEW Files
- `daemon/services/language_utils.py` — Shared `get_language_preference()` utility (service layer)
- `daemon/routers/settings.py` — Settings router with language preference endpoints
- `tests/test_settings_api.py` — API tests

### MODIFIED Files
- `daemon/routers/__init__.py` — Export `settings_router`
- `daemon/routers/schemas.py` — Add `LanguagePreferenceResponse`, `LanguagePreferenceUpdate`
- `daemon/api.py` — Include `settings_router` in `api_router` + wire up repository at startup

## Implementation Details

### Shared Utility (`daemon/services/language_utils.py`)

```python
"""Shared language preference utility.

Lives in the service layer so both the settings router and the instance
lifecycle service can import it without a service→router import inversion.
"""
import logging
from sqlmodel import Session

from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID

logger = logging.getLogger(__name__)

LANGUAGE_METADATA_KEY = "user_language"
DEFAULT_LANGUAGE = "English"


def get_language_preference(project_repo) -> str:
    """Get the stored language preference, or default 'English'.
    
    Used by:
    - daemon/routers/settings.py (GET endpoint)
    - daemon/services/instance_lifecycle.py (spawn + restore paths)
    
    Args:
        project_repo: A SQLModelProjectRepository instance.
    
    Returns:
        The preferred language string, or 'English' if unset, system
        project missing, or DB error.
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
```

### Router (`daemon/routers/settings.py`)

```python
"""Settings API endpoints."""
import logging
from fastapi import APIRouter, HTTPException

from daemon.repositories import SQLModelProjectRepository
from daemon.services.language_utils import get_language_preference, LANGUAGE_METADATA_KEY, DEFAULT_LANGUAGE
from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID
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
    language = get_language_preference(_project_repo)
    return LanguagePreferenceResponse(language=language)


@router.put("/language", response_model=LanguagePreferenceResponse)
async def set_language(request: LanguagePreferenceUpdate):
    """Set the language preference."""
    if not request.language or not request.language.strip():
        raise HTTPException(status_code=422, detail="language must be a non-empty string")
    if SYSTEM_DEFAULT_PROJECT_ID is None:
        raise HTTPException(status_code=503, detail="System default project not initialized")
    _project_repo.set_metadata(SYSTEM_DEFAULT_PROJECT_ID, LANGUAGE_METADATA_KEY, request.language.strip())
    return LanguagePreferenceResponse(language=request.language.strip())
```

### Schemas (`daemon/routers/schemas.py` — add to existing)

```python
class LanguagePreferenceResponse(BaseModel):
    language: str

class LanguagePreferenceUpdate(BaseModel):
    language: str = Field(..., min_length=1, max_length=50, description="Preferred language name (e.g., 'English', 'Spanish', 'Chinese')")
```

### Router Registration (`daemon/api.py`)

```python
# In the import block:
from daemon.routers import (
    ...,
    settings_router,       # /api/settings
)

# In the include_router block:
api_router.include_router(settings_router)        # /api/settings
```

### Startup Wiring (`daemon/api.py` — in lifespan/startup)

```python
# After set_project_repository(repo) for projects router:
from daemon.routers.settings import set_project_repository as set_settings_project_repo
set_settings_project_repo(repo)
```

## Constraints
- PostgreSQL is the PRIMARY dev/test DB — all code must work on both SQLite and PostgreSQL
- `ProjectMetadataRecord` already handles dialect-aware upsert via `_get_dialect_insert()`
- `SYSTEM_DEFAULT_PROJECT_ID` may be None if startup hasn't completed — `get_language_preference()` must handle this gracefully
- Language is stored as a plain string (e.g., "English", "Spanish", "Chinese") — no locale codes needed
- `get_language_preference()` must be importable without side effects (no DB connection at import time)
- `get_language_preference()` lives in `daemon/services/language_utils.py` (service layer) — NOT in the router — to avoid import inversion (W3 fix)

## Deliverables
- [ ] `daemon/services/language_utils.py` created with `get_language_preference()` + constants
- [ ] `daemon/routers/settings.py` created with GET/PUT `/settings/language`
- [ ] `daemon/routers/__init__.py` exports `settings_router`
- [ ] `daemon/api.py` includes `settings_router` + wires up repository at startup
- [ ] `daemon/routers/schemas.py` has `LanguagePreferenceResponse` + `LanguagePreferenceUpdate`
- [ ] Tests in `tests/test_settings_api.py` pass (GET default, PUT+GET, validation, error cases, DB error handling)
- [ ] All existing tests still pass
