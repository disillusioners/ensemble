"""Settings API endpoints."""
import asyncio
import logging
import re
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from daemon.repositories import SQLModelProjectRepository
from daemon.services.language_utils import get_language_preference, LANGUAGE_METADATA_KEY, DEFAULT_LANGUAGE
from daemon.services.editor_utils import get_editor_preference, set_editor_preference
from daemon.services.vscode_server_manager import (
    VSCodeServerNotInstalledError,
    VSCodeServerState,
    VSCodeServerManager,
    VSCodeServerError,
)
from daemon import constants
from .schemas import (
    LanguagePreferenceResponse,
    LanguagePreferenceUpdate,
    EditorPreferenceResponse,
    EditorPreferenceUpdate,
    VSCodeStatus,
    VSCodeStatusResponse,
)

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


def _resolve_vscode_binary(config: Any) -> str | None:
    """Return the configured binary path (if set) or the PATH lookup result."""
    path = getattr(config, "binary_path", None) if config else None
    if path:
        return path
    return shutil.which("code-server")


def _build_vscode_status(manager: VSCodeServerManager | None) -> VSCodeStatus:
    """Build a ``VSCodeStatus`` snapshot from the manager (or absence of one)."""
    if manager is None:
        # No manager wired yet (Phase 3b not yet run) — report a stopped, unavailable
        # VS Code server. The frontend renders this as "VS Code not configured".
        config = None
        try:
            binary = shutil.which("code-server")
        except Exception:
            binary = None
        return VSCodeStatus(
            available=bool(binary),
            binary_path=binary,
            status="stopped",
            allow_remote=False,
        )

    state: VSCodeServerState = manager.state
    config = getattr(manager, "config", None)
    binary_path = _resolve_vscode_binary(config)
    return VSCodeStatus(
        available=bool(binary_path),
        binary_path=binary_path,
        status=state.status,
        allow_remote=bool(getattr(config, "allow_remote", False)),
    )


def _get_vscode_manager(request: Request) -> VSCodeServerManager | None:
    """Return the VSCodeServerManager from app.state, or None if not wired yet.

    Phase 3b wires the manager into ``app.state.vscode_manager`` during the
    FastAPI lifespan. Endpoints must remain import-safe even when the manager
    is not yet constructed (e.g. unit tests, early boot).
    """
    return getattr(request.app.state, "vscode_manager", None)


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


# ==================== Editor Settings Endpoints ====================


@router.get("/editor", response_model=EditorPreferenceResponse)
async def get_editor(request: Request):
    """Get the current editor preference and VS Code server status.

    Returns the stored editor preference (``"builtin"`` or ``"vscode"``) plus a
    snapshot of the VS Code server lifecycle state. The VS Code block is read
    from ``app.state.vscode_manager`` (wired by Phase 3b in the lifespan) or
    falls back to a stopped / unavailable snapshot when the manager is not
    yet installed.
    """
    # ``get_editor_preference`` is async and internally off-loads the sync
    # SQLAlchemy session read to a thread, so we just ``await`` it directly.
    editor = await get_editor_preference(_project_repo)
    manager = _get_vscode_manager(request)
    return EditorPreferenceResponse(
        editor=editor,
        vscode=_build_vscode_status(manager),
    )


@router.put("/editor", response_model=EditorPreferenceResponse)
async def set_editor(body: EditorPreferenceUpdate, request: Request):
    """Set the editor preference and trigger lifecycle side effects.

    - ``editor=vscode`` → lazy-start the VS Code server via ``ensure_running()``.
    - ``editor=builtin`` → stop the VS Code server if running.

    Error handling:
    - Invalid values are rejected by the ``EditorPreferenceUpdate`` schema (422).
    - ``code-server`` binary not found returns 503 with install instructions
      (NOT 500).
    """
    # Defense-in-depth: strip control characters (parity with language pref).
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", body.editor).strip()
    if cleaned not in constants.EDITOR_OPTIONS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Invalid editor value",
                "expected": constants.EDITOR_OPTIONS,
                "received": cleaned,
            },
        )

    repo = get_project_repository()  # raises 503 if not initialized
    manager = _get_vscode_manager(request)

    # W13/W14: Side-effect FIRST, then persist.
    # This prevents the preference from being persisted if the server fails
    # to start (e.g. binary missing, port collision, timeout). The previous
    # order (persist → side-effect) would leave the preference at "vscode"
    # even when the underlying server never came up, forcing the user into
    # an inconsistent state until they manually flipped back to "builtin".
    if cleaned == "vscode":
        if manager is None:
            # Manager not yet wired — return 503 with install instructions.
            install = (
                "Install: curl -fsSL https://code-server.dev/install.sh | sh"
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "VS Code server manager not initialized",
                    "detail": (
                        "The VS Code server manager has not been wired into "
                        "the FastAPI lifespan. Restart the daemon. "
                        f"{install}"
                    ),
                },
            )
        try:
            await manager.ensure_running()
        except VSCodeServerNotInstalledError as e:
            install = (
                "Install: curl -fsSL https://code-server.dev/install.sh | sh"
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "code-server binary not found",
                    "detail": f"{install} (resolved: {e})",
                },
            )
        except VSCodeServerError as e:
            # Catches VSCodeServerStartError, VSCodeServerTimeoutError, and
            # any other base-class error from the manager.
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "VS Code server failed to start",
                    "detail": str(e),
                },
            )
    elif cleaned == "builtin":
        if manager is not None and manager.is_running():
            await manager.stop()

    # NOW persist the preference (after side effects succeed).
    await set_editor_preference(repo, cleaned)

    return EditorPreferenceResponse(
        editor=cleaned,
        vscode=_build_vscode_status(manager),
    )


@router.get("/editor/status", response_model=VSCodeStatusResponse)
async def get_editor_status(request: Request):
    """Lightweight VS Code server status (no metadata read).

    Used by the frontend for periodic polling. Reads only the in-memory
    ``VSCodeServerManager.state`` — does not touch the metadata KV.
    """
    manager = _get_vscode_manager(request)
    if manager is None:
        return VSCodeStatusResponse(status="stopped")
    state = manager.state
    return VSCodeStatusResponse(status=state.status)


@router.get("/vscode/status", response_model=VSCodeStatusResponse, include_in_schema=False)
async def get_vscode_status_alias(request: Request):
    """Alias for ``GET /api/settings/editor/status`` (Phase 5 frontend compatibility)."""
    return await get_editor_status(request)


@router.post("/vscode/start", response_model=VSCodeStatusResponse)
async def post_vscode_start(request: Request):
    """Start the VS Code server (``manager.ensure_running()``)."""
    manager = _get_vscode_manager(request)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "VS Code server manager not initialized",
                "detail": "Restart the daemon to wire the VS Code manager.",
            },
        )
    try:
        await manager.ensure_running()
    except VSCodeServerNotInstalledError as e:
        install = "Install: curl -fsSL https://code-server.dev/install.sh | sh"
        raise HTTPException(
            status_code=503,
            detail={
                "error": "code-server binary not found",
                "detail": f"{install} (resolved: {e})",
            },
        )
    state = manager.state
    return VSCodeStatusResponse(status=state.status)


@router.post("/vscode/stop", response_model=VSCodeStatusResponse)
async def post_vscode_stop(request: Request):
    """Stop the VS Code server (``manager.stop()``)."""
    manager = _get_vscode_manager(request)
    if manager is None:
        return VSCodeStatusResponse(status="stopped")
    if manager.is_running():
        await manager.stop()
    state = manager.state
    return VSCodeStatusResponse(status=state.status)
