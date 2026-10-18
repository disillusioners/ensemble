"""Settings API endpoints.

Sub-systems covered: language/timezone/editor preferences, VS Code
server control, default agent versions, blueprint peak hours, Plane
config, agent-snapshot toggle/metrics, and the DB-backed daemon
runtime settings section (``/settings/daemon``, db-backed-settings).
Grows by one ``# ==================== <Surface> ====================``
divider block per new surface; sync DB/repository work in handlers
stays behind ``asyncio.to_thread`` (file-wide idiom — see the
daemon-settings endpoints for the pattern).
"""
import asyncio
import json
import logging
import os
import re
import shutil
from typing import Any, TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, Response
from sqlmodel import Session
from zoneinfo import available_timezones

from daemon.auth.decorators import scope_required
from daemon.repositories import SQLModelProjectRepository
from daemon.registry import get_registry
from daemon.services.language_utils import get_language_preference, LANGUAGE_METADATA_KEY, DEFAULT_LANGUAGE
from daemon.services.user_timezone_utils import (
    current_utc_offset,
    get_user_timezone_preference,
)
from daemon.services.editor_utils import get_editor_preference, set_editor_preference
from daemon.services.vscode_server_manager import (
    VSCodeServerNotInstalledError,
    VSCodeServerState,
    VSCodeServerManager,
    VSCodeServerError,
)
from daemon import constants
from daemon.auth.context import AUTH_METHOD_BEARER, AUTH_METHOD_SESSION
from daemon.util.tz import _validate_iana
from daemon.services.settings_service import (
    MANAGED_KEYS,
    get_settings_resolution_snapshot as _svc_get_snapshot,
    parse_and_validate,
    resolve,
    set_settings_resolution_snapshot as _svc_set_snapshot,
)
from daemon.routers.snapshots import _proxy_to_snapshots_metrics
from .schemas import (
    LanguagePreferenceResponse,
    LanguagePreferenceUpdate,
    TimezoneOptionsResponse,
    TimezonePreferenceResponse,
    TimezonePreferenceUpdate,
    EditorPreferenceResponse,
    EditorPreferenceUpdate,
    VSCodeStatus,
    VSCodeStatusResponse,
    DefaultAgentVersionsResponse,
    DefaultAgentVersionUpdate,
    BlueprintPeakHoursResponse,
    BlueprintPeakHoursUpdate,
    PlaneConfigResponse,
    SnapshotCreatePreferenceResponse,
    SnapshotCreatePreferenceUpdate,
    SnapshotUsageMetricsResponse,
    DaemonSettingEntry,
    DaemonSettingsResponse,
    DaemonSettingUpdate,
)

# Peak-hours gate metadata keys — these are the same keys read by
# ``BlueprintScanService`` on every execute(). Keeping the literal
# strings here (rather than re-exporting from scan_service) avoids a
# circular import: the scan service itself never imports from the router.
_PEAK_HOURS_START_KEY = "blueprint_peak_hours_start"
_PEAK_HOURS_END_KEY = "blueprint_peak_hours_end"
_PEAK_HOURS_TZ_KEY = "blueprint_peak_hours_tz_offset"
# Default peak-hours window when no metadata has been stored yet. Match
# ``BlueprintScanService._DEFAULT_*`` (defaults are 12:00 - 20:00 GMT+7).
_DEFAULT_PEAK_START = 12
_DEFAULT_PEAK_END = 20
_DEFAULT_TZ_OFFSET = 7

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # Type-only (db-backed-settings P1 t6) — the laziness note covers
    # SettingsService ONLY: MANAGED_KEYS, parse_and_validate and
    # resolve are EAGER module-level imports (import block above).
    from daemon.services.settings_service import SettingsService

router = APIRouter(prefix="/settings", tags=["settings"])

_default_versions_lock = asyncio.Lock()
_project_repo: SQLModelProjectRepository | None = None


def _build_iana_timezone_options() -> list[str]:
    """Return the sorted list of IANA keys the validator accepts.

    :func:`zoneinfo.available_timezones` returns the canonical tzdb
    keyset (the alias / deprecated links are not present on modern
    tzdata — e.g. ``Asia/Saigon`` was retired when it merged into
    ``Asia/Ho_Chi_Minh`` in tzdata 2020b, so it is NOT in the list).
    Every entry is by construction a key that :class:`zoneinfo.ZoneInfo`
    resolves cleanly on this host, which is the same check the
    validator (``daemon.util.tz._validate_iana``) runs — invariant
    ``list ⊆ keys the validator accepts`` holds by construction.

    The result is computed once at import time and cached in
    ``_IANA_TIMEZONE_OPTIONS``; the tzdb is fixed for the process
    lifetime, so per-request work would be wasteful.
    """
    return sorted(available_timezones())


_IANA_TIMEZONE_OPTIONS: list[str] = _build_iana_timezone_options()


def get_project_repository() -> SQLModelProjectRepository:
    if _project_repo is None:
        raise HTTPException(status_code=503, detail={"error": "Project repository not initialized"})
    return _project_repo


def set_project_repository(repo: SQLModelProjectRepository) -> None:
    global _project_repo
    _project_repo = repo


# ==================== Daemon Runtime Settings — module DI (db-backed-settings P1 t6) ====================
# Ownership invariant (plan-overview.md:66, Shape A): these DI symbols
# are created HERE, in Phase 1, and are the ONLY creation site — every
# other phase consumes them. Exact idiom of get_project_repository /
# set_project_repository above; both getters fail identically with
# 503-when-unwired (a cheap backstop for test harnesses that bypass
# the lifespan — not load-bearing in production, where uvicorn gates
# request serving on lifespan completion).


_settings_service: "SettingsService | None" = None


def get_settings_service() -> "SettingsService":
    if _settings_service is None:
        raise HTTPException(status_code=503, detail={"error": "Settings service not initialized"})
    return _settings_service


def set_settings_service(service: "SettingsService") -> None:
    global _settings_service
    _settings_service = service


def set_settings_resolution_snapshot(snapshot: dict[str, dict[str, Any]]) -> None:
    """Store the boot resolution snapshot (delegates to the service
    module's module-global — the SSOT storage; ONE copy, not two)."""
    _svc_set_snapshot(snapshot)


def get_settings_resolution_snapshot() -> dict[str, dict[str, Any]]:
    """Return the boot resolution snapshot — 503 when the lifespan
    hasn't wired it (same unwired idiom as ``get_settings_service``)."""
    snapshot = _svc_get_snapshot()
    if snapshot is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Settings resolution snapshot not initialized"},
        )
    return snapshot


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
            # last_error / exit_code default to None — no manager means
            # no observed crashes yet.
        )

    state: VSCodeServerState = manager.state
    config = getattr(manager, "config", None)
    binary_path = _resolve_vscode_binary(config)
    return VSCodeStatus(
        available=bool(binary_path),
        binary_path=binary_path,
        status=state.status,
        allow_remote=bool(getattr(config, "allow_remote", False)),
        # Surface the watchdog's crash diagnostic (exit code + log tail)
        # so the frontend can show operators WHY code-server died.
        # Both fields are reset to ``None`` by ``start()`` on each fresh
        # spawn, so non-None values imply a recent crash worth surfacing.
        last_error=state.last_error,
        exit_code=state.exit_code,
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


@router.get("/timezone", response_model=TimezonePreferenceResponse)
async def get_timezone():
    """Get the current user timezone preference (+ its current UTC offset).

    Both fields are ``null`` when the preference is unset.
    """
    stored = await asyncio.to_thread(get_user_timezone_preference, _project_repo)
    if not stored:
        return TimezonePreferenceResponse(timezone=None, utc_offset=None)
    # Display-only: the zone's CURRENT offset, DST-correct at read time.
    offset = await asyncio.to_thread(current_utc_offset, stored)
    return TimezonePreferenceResponse(timezone=stored, utc_offset=offset)


@router.put("/timezone", response_model=TimezonePreferenceResponse)
async def set_timezone(request: TimezonePreferenceUpdate):
    """Set (or clear) the user timezone preference.

    ``null`` or an empty string CLEARS the setting (falls back through the
    tz resolution chain); a non-empty value must resolve to a real IANA
    timezone via ``daemon.util.tz._validate_iana`` — anything else is a 422.
    """
    raw = (request.timezone or "").strip()
    repo = get_project_repository()  # raises 503 if not initialized
    if constants.SYSTEM_DEFAULT_PROJECT_ID is None:
        raise HTTPException(status_code=503, detail="System default project not initialized")
    if not raw:
        # null / empty → clear the row entirely (unset = fall through the chain).
        await asyncio.to_thread(
            repo.delete_metadata, constants.SYSTEM_DEFAULT_PROJECT_ID, constants.USER_TIMEZONE_METADATA_KEY
        )
        return TimezonePreferenceResponse(timezone=None, utc_offset=None)
    # Defense-in-depth: strip control characters (newlines, tabs, etc.) so that
    # any payload that slips past schema length bounds cannot inject text into
    # downstream system prompts (parity with the language pref's C1 fix).
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", raw).strip()
    if not cleaned:
        raise HTTPException(
            status_code=422,
            detail="timezone must contain at least one non-whitespace character",
        )
    if not _validate_iana(cleaned):
        raise HTTPException(status_code=422, detail=f"Invalid IANA timezone: '{cleaned}'")
    # ``repo.set_metadata`` opens a sync SQLAlchemy session and commits; off the
    # event loop so it cannot block other in-flight requests.
    await asyncio.to_thread(
        repo.set_metadata, constants.SYSTEM_DEFAULT_PROJECT_ID, constants.USER_TIMEZONE_METADATA_KEY, cleaned
    )
    offset = await asyncio.to_thread(current_utc_offset, cleaned)
    return TimezonePreferenceResponse(timezone=cleaned, utc_offset=offset)


@router.get("/timezones", response_model=TimezoneOptionsResponse)
async def list_timezone_options():
    """Return the canonical IANA timezone list for the Settings picker.

    Sourced from :func:`zoneinfo.available_timezones` — the SAME tzdata
    the validator (``_validate_iana``) uses — so the picker list and the
    validator cannot drift apart. Every entry in the response is, by
    construction, a key that :class:`zoneinfo.ZoneInfo` resolves on the
    host (the deprecated / alias keys are not present in modern tzdata
    — e.g. ``Asia/Saigon`` was retired in tzdata 2020b when it merged
    into ``Asia/Ho_Chi_Minh``). Nothing here is hand-maintained, so
    invariant ``list ⊆ keys the validator accepts`` holds.

    The list is computed once at import time and cached for the
    process lifetime — the underlying tzdb does not change without a
    process restart.
    """
    return TimezoneOptionsResponse(timezones=_IANA_TIMEZONE_OPTIONS)


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


# ==================== Default Agent Versions Endpoints ====================


@router.get("/default-agent-versions", response_model=DefaultAgentVersionsResponse)
async def get_default_agent_versions():
    """Get the current default version overrides for agents.

    Returns a map of ``agent_id → version_tag`` (e.g.,
    ``{"developer": "v2", "tester": None}``). An empty dict means no
    overrides are configured — all agents use their base version.

    Stored as a JSON dict under the ``DEFAULT_AGENT_VERSIONS_METADATA_KEY``
    metadata record on the system default project.
    """
    repo = get_project_repository()  # raises 503 if not initialized
    project_id = constants.SYSTEM_DEFAULT_PROJECT_ID
    if project_id is None:
        raise HTTPException(status_code=503, detail="System default project not initialized")

    # ``get_metadata_record`` requires a session (R2 mirror: same pattern as
    # ``editor_utils.py:42-46``). Open the session inside the thread so it
    # cannot block other in-flight requests.
    def _read() -> dict[str, str | None]:
        with Session(repo.engine) as session:
            record = repo.get_metadata_record(
                session,
                project_id,
                constants.DEFAULT_AGENT_VERSIONS_METADATA_KEY,
            )
        if record is None:
            return {}
        # Stored value is a JSON dict; ``meta_value`` holds the raw JSON string.
        raw = getattr(record, "meta_value", None)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            # Corrupt JSON — surface as empty dict rather than 500. The next
            # PUT will overwrite the bad value with a valid one.
            logger.warning(
                "Corrupt default_agent_versions metadata, returning empty dict: %r",
                raw,
            )
            return {}
        if not isinstance(parsed, dict):
            return {}
        # Normalize: only keep str→str|None entries.
        normalized: dict[str, str | None] = {}
        for k, v in parsed.items():
            if isinstance(k, str) and (v is None or isinstance(v, str)):
                normalized[k] = v
        return normalized

    result = await asyncio.to_thread(_read)
    return DefaultAgentVersionsResponse(default_versions=result)


@router.put("/default-agent-versions", response_model=DefaultAgentVersionsResponse)
async def set_default_agent_version(request: DefaultAgentVersionUpdate):
    """Set the default version override for a single agent.

    Reads the existing dict (or starts empty), updates one ``agent_id`` key,
    and writes the result back. Setting ``version_tag=null`` resets that
    agent to its base version (removes the override).
    """
    repo = get_project_repository()  # raises 503 if not initialized
    project_id = constants.SYSTEM_DEFAULT_PROJECT_ID
    if project_id is None:
        raise HTTPException(status_code=503, detail="System default project not initialized")

    if request.version_tag is not None:
        available = get_registry().list_versions(request.agent_id)
        if available and request.version_tag not in available:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Version tag '{request.version_tag}' not found for agent "
                    f"'{request.agent_id}'. Available: {available}"
                ),
            )

    # Read existing dict (or empty dict if no record / no value).
    def _read() -> dict[str, str | None]:
        with Session(repo.engine) as session:
            existing_record = repo.get_metadata_record(
                session,
                project_id,
                constants.DEFAULT_AGENT_VERSIONS_METADATA_KEY,
            )
        if existing_record is None:
            return {}
        raw = getattr(existing_record, "meta_value", None)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            # Corrupt existing JSON — start fresh rather than 500.
            logger.warning(
                "Corrupt default_agent_versions metadata, overwriting: %r",
                raw,
            )
            return {}
        if not isinstance(parsed, dict):
            return {}
        existing: dict[str, str | None] = {}
        for k, v in parsed.items():
            if isinstance(k, str) and (v is None or isinstance(v, str)):
                existing[k] = v
        return existing

    async with _default_versions_lock:
        existing_dict = await asyncio.to_thread(_read)

        # Mutate the one key.
        if request.version_tag is None:
            # Reset to base: remove the entry entirely.
            existing_dict.pop(request.agent_id, None)
        else:
            existing_dict[request.agent_id] = request.version_tag

        # Persist. ``repo.set_metadata`` opens its own Session internally (R2);
        # off the event loop so it cannot block other in-flight requests.
        await asyncio.to_thread(
            repo.set_metadata,
            project_id,
            constants.DEFAULT_AGENT_VERSIONS_METADATA_KEY,
            existing_dict,
        )
    return DefaultAgentVersionsResponse(default_versions=existing_dict)


# ==================== Blueprint Peak Hours Endpoints ====================


@router.get("/blueprint-peak-hours", response_model=BlueprintPeakHoursResponse)
async def get_blueprint_peak_hours():
    """Get the current blueprint scan peak-hours gate.

    Reads ``blueprint_peak_hours_start`` / ``_end`` / ``_tz_offset`` from
    the system default project metadata. Missing keys fall back to the
    defaults (12:00 - 20:00 GMT+7) so the scan service always sees a
    valid window even on first boot.
    """
    repo = get_project_repository()  # raises 503 if not initialized
    project_id = constants.SYSTEM_DEFAULT_PROJECT_ID
    if project_id is None:
        raise HTTPException(status_code=503, detail="System default project not initialized")

    # ``get_metadata`` is sync SQLAlchemy (ADR-12) — off the event loop.
    start = await asyncio.to_thread(
        repo.get_metadata, project_id, _PEAK_HOURS_START_KEY
    )
    end = await asyncio.to_thread(
        repo.get_metadata, project_id, _PEAK_HOURS_END_KEY
    )
    tz_offset = await asyncio.to_thread(
        repo.get_metadata, project_id, _PEAK_HOURS_TZ_KEY
    )
    return BlueprintPeakHoursResponse(
        start=int(start) if start is not None else _DEFAULT_PEAK_START,
        end=int(end) if end is not None else _DEFAULT_PEAK_END,
        tz_offset=int(tz_offset) if tz_offset is not None else _DEFAULT_TZ_OFFSET,
    )


@router.put("/blueprint-peak-hours", response_model=BlueprintPeakHoursResponse)
async def set_blueprint_peak_hours(body: BlueprintPeakHoursUpdate):
    """Set the blueprint scan peak-hours gate.

    Writes the three values atomically (best-effort — there is no
    transaction wrapping the three calls because ``set_metadata``
    commits per call). Validation is enforced by the Pydantic
    ``BlueprintPeakHoursUpdate`` schema (start/end 0-23, tz_offset
    -12..14). The scan service picks up the new values on its next
    ``execute()`` tick — no daemon restart required.
    """
    repo = get_project_repository()  # raises 503 if not initialized
    project_id = constants.SYSTEM_DEFAULT_PROJECT_ID
    if project_id is None:
        raise HTTPException(status_code=503, detail="System default project not initialized")

    await asyncio.to_thread(
        repo.set_metadata, project_id, _PEAK_HOURS_START_KEY, body.start
    )
    await asyncio.to_thread(
        repo.set_metadata, project_id, _PEAK_HOURS_END_KEY, body.end
    )
    await asyncio.to_thread(
        repo.set_metadata, project_id, _PEAK_HOURS_TZ_KEY, body.tz_offset
    )
    return BlueprintPeakHoursResponse(
        start=body.start, end=body.end, tz_offset=body.tz_offset,
    )


# ==================== Plane Config Endpoint ====================


@router.get("/plane", response_model=PlaneConfigResponse)
async def get_plane_config():
    """Get the Plane integration config.

    Reads the ``PLANE_BASE_URL`` environment variable. Returns
    ``enabled=True`` with the URL when set (non-empty); otherwise
    ``enabled=False`` with an empty URL string so the frontend hides
    the feature.
    """
    url = (os.environ.get("PLANE_BASE_URL", "") or "").strip()
    if not url or not re.match(r'^https?://', url, re.IGNORECASE):
        return PlaneConfigResponse(enabled=False, url="")
    return PlaneConfigResponse(enabled=True, url=url)


# ==================== Agent Snapshot — R15 Toggle (write side ONLY) ====================
# Gates ONLY ``snapshot_create`` (rider (i) isolation —
# ``snapshot_search`` and the warm-start consumption path inside
# ``spawn_instance`` are NEVER gated; consumption is gated per-agent
# by the TARGET's ``snapshot_enabled`` meta flag instead).
# Default OFF — unset / missing / unknown values all read as False
# (fail-closed opt-in rollout). Storage shape mirrors
# ``editor_preference`` (R2: ``set_metadata`` opens its own Session).
# Imported lazily at call sites to avoid a snapshot→settings import
# inversion (the utils live in ``daemon/services/``).


@router.get("/snapshot-create", response_model=SnapshotCreatePreferenceResponse, dependencies=[scope_required("snapshot:write")])
async def get_snapshot_create_preference():
    """Read the R15 settings toggle for ``snapshot_create``.

    Unset / missing / unknown values all return ``enabled=False``
    (fail-closed opt-in rollout — design §6.3 R15).
    """
    repo = get_project_repository()  # raises 503 if not initialized
    from daemon.services.snapshot_settings_utils import get_snapshot_create_enabled

    enabled = await get_snapshot_create_enabled(repo)
    return SnapshotCreatePreferenceResponse(enabled=enabled)


@router.put("/snapshot-create", response_model=SnapshotCreatePreferenceResponse, dependencies=[scope_required("snapshot:write")])
async def set_snapshot_create_preference(request: SnapshotCreatePreferenceUpdate):
    """Persist the R15 settings toggle for ``snapshot_create``.

    Mirrors ``set_editor_preference``: writer responsibility is the
    metadata record only. The ``snapshot_create`` tool reads it on
    every invocation via the async
    ``get_snapshot_create_enabled`` util (awaited at the single
    tool call site — the R15 gate in
    ``daemon/tools/snapshot_tools.py``); the deprecated
    ``is_snapshot_create_enabled`` sync stub is NOT on the tool path.
    """
    repo = get_project_repository()  # raises 503 if not initialized
    from daemon.services.snapshot_settings_utils import set_snapshot_create_enabled

    try:
        enabled = await set_snapshot_create_enabled(repo, request.enabled)
    except RuntimeError as exc:
        # system default project row is missing (W12 parity)
        raise HTTPException(status_code=503, detail=str(exc))
    return SnapshotCreatePreferenceResponse(enabled=enabled)


# ==================== Agent Snapshot — R16 Monitoring Metrics ====================
# MONITORING ONLY — explicitly NOT a ranking signal (design §6.3 R16;
# R10 forbids usage-ranking in v1). The endpoint surfaces the counters
# the R16 storage collects: per-agent capture counts + per-snapshot
# spawn-warm counts. Cold spawns (``None`` snapshot consumption) are
# NOT counted — see ``daemon/services/snapshot_metrics_service.py``.
#
# As of snapshot-uiux v1 this legacy path is **deprecated**. The new
# canonical home is ``GET /api/snapshots/metrics`` (see
# ``daemon/routers/snapshots.py:get_snapshot_metrics``). The legacy
# handler below is kept operational for one release window with
# manual ``Deprecation`` / ``Sunset`` / ``Link`` response headers
# (FastAPI's ``deprecated=True`` ONLY marks the route in the OpenAPI
# schema — it does NOT emit actual response headers; we set them
# manually, copied from the project precedent at
# ``daemon/routers/blueprints.py:551-557``, per
# RFC 8594 Deprecation + RFC 8288 Link). The body is delegated to
# the canonical handler via the ``_proxy_to_snapshots_metrics``
# helper (LEADER RULING, sequencing pass 4 amendment blocker #6 —
# the helper is defined in ``daemon/routers/snapshots.py`` and
# imported here, NOT defined alongside this legacy handler, to keep
# the coupling direction one-way: ``settings.py`` → ``snapshots.py``).


@router.get(
    "/snapshot-usage-metrics",
    response_model=SnapshotUsageMetricsResponse,
    dependencies=[scope_required("snapshot:write")],
    deprecated=True,
)
async def get_snapshot_usage_metrics_deprecated(
    request: Request, response: Response
) -> SnapshotUsageMetricsResponse:
    """DEPRECATED — use ``GET /api/snapshots/metrics`` instead.

    Re-export to preserve any non-FE caller (CLI, scripts, curl
    dashboards) for one release window. Removal planned once the
    FE has shipped and a CHANGELOG deprecation note is in place.

    **Deprecation signal.** ``FastAPI``'s ``deprecated=True`` flag
    only marks the route in the OpenAPI schema (it does NOT emit
    HTTP response headers — that would have to be a middleware).
    We set the ``Deprecation``, ``Sunset``, and ``Link`` headers
    manually on the response object, copying the project precedent
    at ``daemon/routers/blueprints.py:551-557``. The ``Link`` header
    points at the successor ``/api/snapshots/metrics`` route per
    RFC 8288.

    **Counting semantics** (unify-spawn-tools):

    * capture counts — incremented on every ``snapshot_create``
      invocation regardless of R9 verdict (REUSE + NEW + SUPERSEDE +
      CREATE-FRESH all count);
    * per-snapshot spawn counts — incremented on the warm-start
      path of ``spawn_instance`` (the unified spawn tool; formerly the
      removed ``spawn_hot_instance``) only — cold / no-hit / expired /
      verify-failed paths DO NOT count (R16 rider j).

    The body delegates to the canonical handler via
    :func:`daemon.routers.snapshots._proxy_to_snapshots_metrics`
    (imported above) — no logic duplication.
    """
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "Sun, 31 Dec 2026 23:59:59 GMT"
    response.headers["Link"] = '</api/snapshots/metrics>; rel="successor-version"'
    return await _proxy_to_snapshots_metrics(request)


# ==================== Daemon Runtime Settings endpoints (db-backed-settings P2 t3+t4) ====================
# GET /api/settings/daemon — gated settings:read; PUT/DELETE
# /api/settings/daemon/{key} — gated settings:write (architect
# Ruling 4: the infrastructure precedent class — this surface reports
# effective daemon config incl. env/yaml tier values; its right
# precedent is instances/maintenance, NOT the ungated
# language/timezone preference family). Both scopes are registered in
# SCOPE_TAXONOMY (closed taxonomy — registration is mandatory).
# Gates are inert when auth is OFF.


def _actor_subject_from_request(request: Request) -> str | None:
    """Extract the acting subject for ``updated_by`` / audit lines.

    REPLICATES the ``actor_from_request`` pattern
    (daemon/repositories/prompt_version/repository.py:593-637 —
    getattr-based extraction off ``request.state.auth_context``)
    WITHOUT importing that helper verbatim: it RAISES on auth-off and
    internal-lane callers, and this surface must record ``None``
    instead (auth-off => NULL ``updated_by`` row, never an error).

    Guards, in the same order as the precedent:

    * no ``auth_context`` on request.state → ``None`` (auth OFF or
      resolution failed — same recording posture);
    * ``auth_method`` not in {session, bearer} → ``None`` (the
      ``internal`` lane is reserved for in-process submitters; an
      HTTP request presenting it is a forgery attempt (S16) — we do
      not raise here, we simply record no actor);
    * empty ``subject`` → ``None``.

    NEVER body-asserted (the request body is not a source of actor
    identity — decisions.md R3); NEVER a ``request.client.host``
    fallback.
    """
    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return None
    auth_method = getattr(auth_ctx, "auth_method", "")
    if auth_method not in (AUTH_METHOD_SESSION, AUTH_METHOD_BEARER):
        return None
    subject = getattr(auth_ctx, "subject", None)
    if not subject or not isinstance(subject, str):
        return None
    return subject


def _build_daemon_entries(
    overrides: dict[str, Any],
) -> tuple[list[DaemonSettingEntry], bool]:
    """Build one entry per registered key + the derived
    ``restart_required`` flag.

    ``value`` = ``snapshot.applied_value`` (boot-applied running
    truth — carried from the P1 snapshot, NOT re-resolved);
    ``source`` = LIVE re-resolve via the ONE shared resolver over
    (live DB row > live env > snapshot yaml > default). Divergence
    between the two is the honest restart-required reporting.
    Sensitive keys omit the tier detail fields entirely (security
    addendum 1 — omitted via None + response_model_exclude_none).
    """
    snapshot = get_settings_resolution_snapshot()
    entries: list[DaemonSettingEntry] = []
    restart_required = False
    for key, spec in MANAGED_KEYS.items():
        resolution = resolve(key, snapshot, db_overrides=overrides)
        if resolution["source"] == "db":
            restart_required = True
        applied = snapshot.get(key, {}).get("applied_value")
        if applied is None:
            # Defensive: a hand-built snapshot without applied_value
            # (test harness) falls back to the live resolved value.
            applied = resolution["value"]
        if spec.sensitive:
            env_value = yaml_value = db_override = None
        else:
            env_value = resolution["env_value"]
            yaml_value = resolution["yaml_value"]
            db_override = resolution["db_override"]
        entries.append(
            DaemonSettingEntry(
                key=key,
                value=applied,
                source=resolution["source"],
                default=resolution["default"],
                env_value=env_value,
                yaml_value=yaml_value,
                db_override=db_override,
                requires_restart=spec.requires_restart,
                sensitive=spec.sensitive,
                description=spec.description,
            )
        )
    return entries, restart_required


def _daemon_entry_for(key: str, overrides: dict[str, Any]) -> DaemonSettingEntry:
    """Return the refreshed bare entry for one key (PUT/DELETE
    response body — the t1-frozen write shape)."""
    entries, _ = _build_daemon_entries(overrides)
    for entry in entries:
        if entry.key == key:
            return entry
    # Unreachable: _build_daemon_entries iterates MANAGED_KEYS and
    # the caller has already 404-rejected unknown keys.
    raise HTTPException(status_code=404, detail={"error": f"Unknown managed setting key: {key}"})


@router.get(
    "/daemon",
    response_model=DaemonSettingsResponse,
    dependencies=[scope_required("settings:read")],
    response_model_exclude_none=True,
)
async def get_daemon_settings():
    """List effective daemon settings with per-key tier reporting.

    Registry-driven (v1: exactly one key, ``limits.llm_concurrency``).
    Raises 503 when the lifespan hasn't wired the settings service or
    the resolution snapshot (test-harness backstop).
    """
    service = get_settings_service()  # raises 503 if not initialized
    # Sync DB call — kept off the event loop (same to_thread idiom as
    # every other repository read in this file, e.g. the peak-hours
    # metadata reads above).
    overrides = await asyncio.to_thread(service.get_overrides)
    entries, restart_required = _build_daemon_entries(overrides)
    return DaemonSettingsResponse(settings=entries, restart_required=restart_required)


@router.put(
    "/daemon/{key}",
    response_model=DaemonSettingEntry,
    dependencies=[scope_required("settings:write")],
    response_model_exclude_none=True,
)
async def put_daemon_setting(
    key: str,
    update: DaemonSettingUpdate,
    request: Request,
):
    """Set the DB override for a managed key.

    404 for keys outside the registry; 422 when the value fails the
    registry validator (parse / range, e.g. ge=1). Persists via the
    runtime SettingsService (manager's shared engine). Response: the
    BARE refreshed entry (source now "db"; ``value`` stays the
    boot-applied truth — restart-required is carried by the entry's
    own ``requires_restart`` / the value-vs-source divergence).
    """
    if key not in MANAGED_KEYS:
        raise HTTPException(
            status_code=404,
            detail={"error": f"Unknown managed setting key: {key}"},
        )
    service = get_settings_service()  # raises 503 if not initialized
    spec = MANAGED_KEYS[key]
    ok, parsed, failure_class, detail = parse_and_validate(spec, update.value)
    if not ok:
        raise HTTPException(
            status_code=422,
            detail={
                "error": f"Invalid value for {key}",
                "failure_class": failure_class,
                "constraint": spec.constraint_desc(),
                "detail": detail,
            },
        )
    subject = _actor_subject_from_request(request)
    await asyncio.to_thread(service.set_override, key, parsed, updated_by=subject)
    # Audit (ruling O2): ONE INFO line per PUT carrying subject + key
    # ("auth-off" for NULL updated_by rows). Sensitive keys log the
    # literal "<redacted>" in place of the value (sensitive=True
    # tripwire); the "value=" term stays present for grep-ability.
    logged_value = "<redacted>" if spec.sensitive else parsed
    logger.info(
        "[Settings] db override set key=%s value=%r subject=%s",
        key,
        logged_value,
        subject or "auth-off",
    )
    return _daemon_entry_for(key, await asyncio.to_thread(service.get_overrides))


@router.delete(
    "/daemon/{key}",
    response_model=DaemonSettingEntry,
    dependencies=[scope_required("settings:write")],
    response_model_exclude_none=True,
)
async def delete_daemon_setting(key: str, request: Request):
    """Clear the DB override for a managed key (falls back down the
    ladder: env > yaml > default on the next boot / live re-resolve).

    404 for keys outside the registry; idempotent on a missing row.
    Response: the BARE refreshed entry.
    """
    if key not in MANAGED_KEYS:
        raise HTTPException(
            status_code=404,
            detail={"error": f"Unknown managed setting key: {key}"},
        )
    service = get_settings_service()  # raises 503 if not initialized
    subject = _actor_subject_from_request(request)
    await asyncio.to_thread(service.clear_override, key)
    # Audit (ruling O2): ONE INFO line per DELETE carrying subject +
    # key ("auth-off" for NULL updated_by rows).
    logger.info(
        "[Settings] db override cleared key=%s subject=%s",
        key,
        subject or "auth-off",
    )
    return _daemon_entry_for(key, await asyncio.to_thread(service.get_overrides))
