# Phase 3: Settings API, Path Validation & Lifecycle Wiring

## Objective
Add the editor preference REST endpoints (`GET/PUT /api/settings/editor`), a **dedicated path-validated folder endpoint** (C2), wire `VSCodeServerManager` into the FastAPI lifespan (startup/shutdown), and **exclusively own the proxy mount** (W3). This phase is the integration seam connecting the process manager (Phase 1) and proxy (Phase 2) to the frontend (Phases 4-5).

> **Rev 2 changes**: C6 (correct repo signature with `Session` context manager), W3 (owns mount exclusively — Phase 2 no longer touches `api.py`), C2 (path validation endpoint), correct `editor_utils` using the real repository API.
>
> **Rev 3 changes**: R1 (`record.meta_value` not `record.metadata_value`), R2 (`set_metadata(project_id, key, value)` — no session param), R3/N1 (replaced custom `_validate_vscode_folder` with existing `WorkspaceGuard.resolve_strict()` for containment enforcement).

## Coupling
- **Depends on**: Phase 1 (VSCodeServerManager), Phase 2 (proxy app factory)
- **Coupling type**: **tight** with Phase 1 (lifespan creates/stops the manager), **loose** with Phase 2 (just calls `create_vscode_proxy_app(manager)` and mounts)
- **Shared files with other phases**: `daemon/routers/settings.py` (modify), `daemon/api.py` (lifespan modify + mount), `daemon/services/vscode_server_manager.py` (from Phase 1), `daemon/routers/vscode_proxy.py` (from Phase 2)
- **Shared APIs/interfaces**: `GET /api/settings/editor`, `PUT /api/settings/editor`, `GET /api/settings/editor/status`, `GET /api/projects/{id}/vscode-folder`
- **Why this coupling**: The lifespan must construct the manager and mount the proxy; the settings API calls manager methods on preference change.

## Context
- **Previous phases delivered**: 
  - Phase 1: `VSCodeServerManager` class with full lifecycle + auth token
  - Phase 2: `create_vscode_proxy_app(manager)` factory (router + factory only — **no mount**, per W3)
- **Settings pattern**: Uses `project_metadata_records` table + `SYSTEM_DEFAULT_PROJECT_ID` for global preferences. Repository signature is `get_metadata_record(session, project_id, key)` — NOT `get_metadata_record(project_id, key)` (C6 fix). Existing example: language preference in `daemon/services/language_utils.py:48-51`.
- **Lifespan pattern**: `@asynccontextmanager lifespan(app)` in `daemon/api.py:109-743`. ~30 startup steps, reverse-order shutdown. Services stored on `app.state` + module-level setter injection.

## Technical Approach

### C6 Fix: Correct Repository Signature (R1 + R2)

**R1**: The model attribute is `record.meta_value`, NOT `record.metadata_value` (confirmed in `models.py:185`).
**R2**: `set_metadata()` signature is `set_metadata(project_id, key, value)` with NO session param — it opens its own `Session` internally. Mirror `settings.py:58-59`.

```python
# daemon/services/editor_utils.py
import constants

EDITOR_METADATA_KEY = "editor_preference"
EDITOR_DEFAULT = "builtin"
EDITOR_OPTIONS = ["builtin", "vscode"]

async def get_editor_preference(repo) -> str:
    """Read editor preference. R1: use record.meta_value. R2: set_metadata opens its own Session."""
    # R2: set_metadata/get_metadata_record open their own Session internally
    # (mirror settings.py:58-59 — NO manual Session wrapper needed)
    record = await asyncio.to_thread(
        repo.get_metadata_record,
        constants.SYSTEM_DEFAULT_PROJECT_ID,
        EDITOR_METADATA_KEY,
    )
    # R1: attribute is meta_value, NOT metadata_value
    if record and record.meta_value in EDITOR_OPTIONS:
        return record.meta_value
    return EDITOR_DEFAULT

async def set_editor_preference(repo, value: str) -> str:
    """Write editor preference. R2: set_metadata opens its own Session."""
    await asyncio.to_thread(
        repo.set_metadata,
        constants.SYSTEM_DEFAULT_PROJECT_ID,   # project_id
        EDITOR_METADATA_KEY,                    # key
        value,                                  # value
    )
    return value
```

### Editor Preference Storage
Reuse the exact same metadata-KV pattern as language preference:
- **Metadata key**: `EDITOR_METADATA_KEY = "editor_preference"` 
- **Values**: `"builtin"` (default) | `"vscode"`
- **Storage**: `project_metadata_records` against `SYSTEM_DEFAULT_PROJECT_ID`
- **Default**: `"builtin"` (sentinel for "no preference set")

### C2 Fix: Path-Validated Folder Endpoint (R3 + N1)

**Problem**: `main_directory` is accepted from HTTP body with zero router-layer validation. Exploit: `POST /api/projects {"main_directory":"/"}` → `/vscode/?folder=/` → entire filesystem exposed.

**R3**: The previous custom `_validate_vscode_folder()` rejected `..`, root `/`, symlinks, and non-existent dirs — but **accepted `/etc`, `/root`, `/var/log`** because it had no containment check. This is a security regression.

**N1**: Zero references to the existing `WorkspaceGuard` in all plan files. The plan reinvented a weaker validator.

**Solution**: Use the existing `WorkspaceGuard.resolve_strict()` everywhere filesystem paths are validated. It already handles `..`, symlinks, AND containment. Remove all custom path validation code.

#### New Endpoint: `GET /api/projects/{id}/vscode-folder`

```python
from daemon.services.workspace_guard import WorkspaceGuard

@router.get("/projects/{project_id}/vscode-folder")
async def get_vscode_folder(project_id: str, request: Request):
    """Return a server-side validated folder path for VS Code.
    
    C2/N1: Uses WorkspaceGuard.resolve_strict() for containment enforcement.
    Never expose main_directory directly.
    """
    project = await _get_project(request, project_id)
    if not project or not project.main_directory:
        raise HTTPException(404, "Project or main_directory not found")
    
    # R3/N1: Use WorkspaceGuard — NOT custom validator.
    # resolve_strict enforces: no .., no symlink escape, containment within allowed root.
    guard = WorkspaceGuard(project.main_directory)
    resolved, error = guard.resolve_strict(project.main_directory)
    if error:
        raise HTTPException(403, f"Path outside allowed root: {error}")
    
    return {"folder": resolved, "encoded": urllib.parse.quote(resolved)}
```

#### Also: Harden Project Creation (C2)

Use `WorkspaceGuard` on project creation too — NOT a custom validator:
```python
@router.post("/projects")
async def create_project(request: Request, body: ProjectCreate):
    if body.main_directory:
        # R3/N1: Use WorkspaceGuard — NOT custom validator
        guard = WorkspaceGuard(body.main_directory)
        resolved, error = guard.resolve_strict(body.main_directory)
        if error:
            raise HTTPException(422, f"main_directory is not safe: {error}")
    ...
```

### API Endpoints

#### `GET /api/settings/editor`
Returns current editor preference + VS Code server status:
```json
{
  "editor": "builtin",
  "vscode": {
    "available": false,
    "binary_path": null,
    "status": "stopped",
    "port": null,
    "allow_remote": false
  }
}
```

#### `PUT /api/settings/editor`
Sets editor preference. Side effects:
- If `editor=vscode` → call `manager.ensure_running()` (lazy start)
- If `editor=builtin` → call `manager.stop()` (stop if running)

```json
// Request
{ "editor": "vscode" }
// Response 200
{ "editor": "vscode", "vscode": { "status": "running", "port": 9234, "allow_remote": false } }
// Response 503 (binary not found)
{ "error": "code-server binary not found", "detail": "Install: curl -fsSL https://code-server.dev/install.sh | sh" }
```

#### `GET /api/settings/editor/status`
Lightweight status check (for frontend polling):
```json
{
  "status": "running",
  "port": 9234,
  "pid": 12345
}
```

### Lifespan Integration (W3 — owns the mount exclusively)

Add to lifespan startup sequence (after Phase 4: Recovery & Reconciliation, ~line 551, before Router DI):

```python
# --- VS Code Server Manager ---
from daemon.services.vscode_server_manager import VSCodeServerManager
from daemon.routers.vscode_proxy import create_vscode_proxy_app

vscode_manager = VSCodeServerManager(
    data_dir=data_dir,
    config=ensemble_config.vscode,    # VSCodeConfig from config.py
    daemon_host=ensemble_config.daemon.host,  # C1: for allow_remote check
)
await vscode_manager.init()  # crash recovery (PID file check)
app.state.vscode_manager = vscode_manager

# W3: Phase 3 owns the mount — Phase 2 only delivered the factory
vscode_app = create_vscode_proxy_app(vscode_manager)
app.mount("/vscode", vscode_app)
```

Add to lifespan shutdown sequence (before `manager.shutdown()`):

```python
# --- VS Code Server shutdown ---
if hasattr(app.state, 'vscode_manager'):
    await app.state.vscode_manager.stop()
```

### Catch-All Guard Update (S1)

**S1: Keep the `path.startswith('vscode')` guard.** Starlette mount prefix matching does NOT match `/vscodefoo` to the `/vscode` mount. The guard is required.

Update catch-all at `api.py:1405`:
```python
# S1: guard must include 'vscode' prefix — prevents /vscodefoo from hitting SPA fallback
if path.startswith('api') or path.startswith('ws') or path.startswith('vscode'):
    return JSONResponse(status_code=404, content={"error": "Not found"})
```

### Router Registration
The settings router already exists and is registered at `api.py:1376`. Add the new endpoints to `daemon/routers/settings.py`. The projects router (for `/api/projects/{id}/vscode-folder`) already exists at `api.py:1368`.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Define editor preference constants | `EDITOR_METADATA_KEY`, `EDITOR_DEFAULT`, `EDITOR_OPTIONS` | `daemon/constants.py` |
| 2 | Implement `editor_utils.py` with correct repo signature | **R1: `record.meta_value`** (not `metadata_value`); **R2: `set_metadata(project_id, key, value)` — NO session param** (mirror `settings.py:58-59`) | `daemon/services/editor_utils.py` (new) |
| 3 | Add `GET /api/settings/editor` endpoint | Return editor pref + VS Code status dict | `daemon/routers/settings.py` |
| 4 | Add `PUT /api/settings/editor` endpoint | Validate input, call `set_editor_preference()` (R2 pattern), trigger start/stop side effects, return updated status | `daemon/routers/settings.py` |
| 5 | Add `GET /api/settings/editor/status` endpoint | Lightweight status (no metadata read, just manager state) | `daemon/routers/settings.py` |
| 6 | **R3/N1: Use `WorkspaceGuard.resolve_strict()` for path validation** | Replace all custom path validation with existing `WorkspaceGuard` — enforces containment, rejects `..`, symlinks, outside-root | `daemon/routers/projects.py`, `daemon/services/workspace_guard.py` (existing) |
| 7 | **C2: Add `GET /api/projects/{id}/vscode-folder` endpoint** | Return pre-validated, encoded folder path via `WorkspaceGuard.resolve_strict()` | `daemon/routers/projects.py` |
| 8 | **C2: Harden `POST /api/projects` handler** | Validate `main_directory` on project creation via `WorkspaceGuard.resolve_strict()` | `daemon/routers/projects.py` |
| 9 | Wire manager + proxy mount into lifespan startup | **W3: Phase 3 owns mount exclusively**. Construct manager, `init()`, `app.state`, `create_vscode_proxy_app()`, `app.mount("/vscode", ...)` | `daemon/api.py` |
| 10 | Wire manager into lifespan shutdown | Call `vscode_manager.stop()` before final manager shutdown | `daemon/api.py` |
| 11 | **S1: Update catch-all guard** | Add `path.startswith('vscode')` to the guard at `api.py:1405` | `daemon/api.py` |
| 12 | Write API tests | Test GET/PUT endpoints with **mocked repo (R1: meta_value, R2: no session param)**; test validation; test side effects | `tests/api/test_editor_settings.py` |
| 13 | Write path validation tests | Test `WorkspaceGuard.resolve_strict()`: **reject `/etc` (R3)**, reject `..`, root `/`, symlinks, non-existent; accept valid dirs within project root | `tests/unit/test_vscode_path_validation.py` |
| 14 | Write lifespan integration test | Test startup creates manager + mounts proxy; shutdown stops manager | `tests/integration/test_vscode_lifespan.py` |

## Key Files
- `daemon/routers/settings.py` — **MODIFY**: Add 3 new endpoints (~80 lines)
- `daemon/routers/projects.py` — **MODIFY**: Add vscode-folder endpoint + harden POST via WorkspaceGuard (C2) (~35 lines)
- `daemon/api.py` — **MODIFY**: Lifespan startup (mount) + shutdown + catch-all guard (~20 lines)
- `daemon/constants.py` — **MODIFY**: Editor preference constants (~5 lines)
- `daemon/services/editor_utils.py` — **NEW**: Helper functions with **R1/R2 correct repo signature** (~40 lines)
- `daemon/services/workspace_guard.py` — **EXISTING**: Used for path validation (no changes needed)
- `tests/api/test_editor_settings.py` — **NEW**: API tests
- `tests/unit/test_vscode_path_validation.py` — **NEW**: Path validation tests (C2/R3/N1)
- `tests/integration/test_vscode_lifespan.py` — **NEW**: Lifespan tests

## Constraints
- **R1: Use `record.meta_value`** — NOT `record.metadata_value` (model attribute confirmed in `models.py:185`).
- **R2: `set_metadata()` signature is `set_metadata(project_id, key, value)`** — NO session param. It opens its own Session internally. Mirror `settings.py:58-59` — do NOT wrap in `Session(repo.engine)`.
- **R3/N1: Use `WorkspaceGuard.resolve_strict()` for ALL path validation** — NOT a custom validator. `WorkspaceGuard` already handles `..`, symlinks, AND containment. The custom `_validate_vscode_folder()` accepted `/etc` (no containment check) — a security regression.
- **C2: Never pass `main_directory` directly to code-server** — always via the validated `/api/projects/{id}/vscode-folder` endpoint.
- **W3: Phase 3 owns the mount exclusively** — `app.mount("/vscode", ...)` happens here in lifespan, NOT in Phase 2.
- **S1: Catch-all guard MUST include `vscode`** — Starlette mount prefix matching doesn't match `/vscodefoo`.
- **Follow language preference pattern exactly**: `asyncio.to_thread()` wrapping repo calls, `SYSTEM_DEFAULT_PROJECT_ID`, same validation approach (strip control chars, length check)
- **Side effects on PUT must be async**: `manager.start()`/`stop()` are async; the endpoint must `await` them
- **Lifespan ordering**: Manager init must happen AFTER `manager.initialize()` (needs DB for config) but BEFORE router DI section
- **Shutdown ordering**: `vscode_manager.stop()` must happen BEFORE `manager.shutdown()` (process cleanup before DB teardown)
- **Error handling on PUT**: If `code-server` binary not found, return 503 with install instructions (not 500)
- **No DB migration needed**: Uses existing `project_metadata_records` table — just a new metadata key

## Deliverables
- [ ] `GET /api/settings/editor` returns editor pref + VS Code status
- [ ] `PUT /api/settings/editor` validates, stores (**R1: `meta_value`, R2: no session**), and triggers start/stop
- [ ] `GET /api/settings/editor/status` returns lightweight status
- [ ] **C2/R3/N1: `GET /api/projects/{id}/vscode-folder`** returns pre-validated folder path via `WorkspaceGuard.resolve_strict()`
- [ ] **C2/R3/N1: `POST /api/projects` rejects unsafe `main_directory`** via `WorkspaceGuard.resolve_strict()`
- [ ] **W3: Proxy mounted in lifespan** (Phase 3 owns mount exclusively)
- [ ] **S1: Catch-all guard updated** to include `vscode` prefix
- [ ] Manager wired into lifespan startup (init + mount proxy) + shutdown (stop)
- [ ] API tests with mocked dependencies (R1/R2 correct signature)
- [ ] Path validation tests (C2/R3 — including `/etc` rejection)
- [ ] Lifespan integration test
- [ ] Error handling for binary-not-found (503 with instructions)

## Testing Strategy

### API Tests (Phase 3)
Using FastAPI `TestClient` with mocked dependencies:
- **GET editor pref**: Returns `"builtin"` when no metadata, returns stored value when set
- **PUT editor pref**: 
  - Valid `"vscode"` → stores, calls `manager.ensure_running()`, returns running status
  - Valid `"builtin"` → stores, calls `manager.stop()`, returns stopped status
  - Invalid value (e.g., `"monaco"`) → returns 422
  - Binary not found → returns 503 with install instructions
- **GET status**: Returns manager state dict
- **Security**: Control characters stripped, length validated
- **R1**: Verify code reads `record.meta_value` (NOT `record.metadata_value`)
- **R2**: Verify `set_metadata` called with `(project_id, key, value)` — NO session arg

### C2/R3/N1 Path Validation Tests
Using `WorkspaceGuard.resolve_strict()`:
- **R3**: `resolve_strict("/etc")` → returns error (outside allowed root) — **THIS WAS THE BUG**
- `resolve_strict("/")` → returns error (root rejected)
- `resolve_strict("../../etc/passwd")` → returns error (traversal)
- `resolve_strict("/nonexistent")` → returns error (doesn't exist)
- `resolve_strict("/symlink/to/outside")` → returns error if target outside root
- `resolve_strict("valid/subdir")` → returns resolved path (within project root)
- `GET /api/projects/{id}/vscode-folder` → returns validated + encoded path
- `POST /api/projects {"main_directory": "../"}` → 422

### Lifespan Integration Test
- **Startup**: Verify `app.state.vscode_manager` exists, `/vscode` mount is registered
- **Shutdown**: Verify `manager.stop()` is called on shutdown
- **Crash recovery**: Pre-create PID file, verify manager adopts/cleans up on init

### Test commands
```bash
pytest tests/api/test_editor_settings.py -v
pytest tests/unit/test_vscode_path_validation.py -v
pytest tests/integration/test_vscode_lifespan.py -v
```
