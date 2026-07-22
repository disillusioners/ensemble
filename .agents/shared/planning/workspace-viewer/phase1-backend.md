# Phase 1: Backend API — WorkspaceRouter, WorkspaceGuard, GitDiffService

## Objective

Create the complete backend API layer for the workspace viewer: a new `WorkspaceRouter` with 4 endpoints (file tree, file content, git diff, SSE file-change events), a reusable `WorkspaceGuard` security boundary extracted from `filesystem.py`, and a `GitDiffService` for subprocess-based git operations.

## Coupling

- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `daemon/tools/filesystem.py` (modified — delegates to extracted `WorkspaceGuard`)
- **Shared APIs/interfaces**: REST contract consumed by Phase 2 (loose coupling — Phase 2 can mock these endpoints)
- **Why this coupling**: Phase 1 defines the API contract. Phase 2 depends on the contract shapes, not the implementation.

## Context

### Existing Patterns to Follow

**Router registration** (`daemon/api.py:1354-1372`):
```python
api_router.include_router(agents_router)        # /api/agents
api_router.include_router(settings_router)       # /api/settings
# NEW:
api_router.include_router(workspace_router)      # /api/workspace
```

**Module-level repository pattern** (`daemon/routers/settings.py:16-27`):
```python
_project_repo: SQLModelProjectRepository | None = None

def get_project_repository() -> SQLModelProjectRepository:
    if _project_repo is None:
        raise HTTPException(status_code=503, detail={"error": "Project repository not initialized"})
    return _project_repo

def set_project_repository(repo: SQLModelProjectRepository) -> None:
    global _project_repo
    _project_repo = repo
```

**SSE pattern** (`daemon/routers/notifications.py:28-90`):
```python
@router.get("/stream")
async def stream_notifications(request: Request):
    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue(maxsize=SSE_QUEUE_MAXSIZE)
        connection_id = await broadcaster.add_connection(queue)
        try:
            yield {"event": "connected", "data": json.dumps({"status": "connected"})}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    notification = await asyncio.wait_for(queue.get(), timeout=SSE_TIMEOUT_S)
                    yield {"event": "notification", "data": json.dumps(notification)}
                except asyncio.TimeoutError:
                    yield {"event": "keepalive", "data": "{}"}
        finally:
            await broadcaster.remove_connection(connection_id)
    return EventSourceResponse(event_generator(), ping=SSE_PING_INTERVAL)
```

**Async-to-thread pattern** for blocking I/O (`daemon/routers/settings.py:35`):
```python
language = await asyncio.to_thread(get_language_preference, _project_repo)
```

**Project model** (`daemon/repositories/project/models.py:210`):
```python
main_directory: str | None = None
```

### Filesystem Security Functions to Extract

From `daemon/tools/filesystem.py`:
- `_resolve_within_workdir(path, workdir) -> tuple[Path | None, str | None]` — main security entry point
- `_resolve_target_path(path, workdir) -> tuple[Path | None, Path | None, str | None]` — path resolution
- `_is_within_workdir(workdir, target) -> bool` — boundary check
- `_normed_contains(base, target) -> bool` — containment check
- `_is_absolute_path(path) -> bool` — absolute path detection

These are currently private functions. They need to become importable from a shared module.

---

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Extract WorkspaceGuard | Create `daemon/services/workspace_guard.py` with `WorkspaceGuard` class wrapping the path resolution + boundary check logic. Move `_resolve_within_workdir`, `_resolve_target_path`, `_is_within_workdir`, `_normed_contains`, `_is_absolute_path` from `filesystem.py`. Update `filesystem.py` to import from new module. | `daemon/services/workspace_guard.py` (new), `daemon/tools/filesystem.py` (modify) |
| 2 | Create Pydantic schemas | Define request/response models: `FileTreeNode`, `FileTreeResponse`, `FileContentResponse`, `GitDiffResponse`, `WorkspaceErrorResponse` | `daemon/routers/schemas.py` (modify) or `daemon/routers/workspace_schemas.py` (new) |
| 3 | Create GitDiffService | Subprocess-based git diff service. `git show HEAD:{path}` and `git diff HEAD -- {path}`. Timeout, error handling, not-a-repo detection. | `daemon/services/git_diff_service.py` (new) |
| 4 | Create WorkspaceRouter | 4 endpoints: tree, file, diff, events. Use WorkspaceGuard for path resolution. Resolve workdir from project_id via project repository. | `daemon/routers/workspace.py` (new) |
| 5 | Register router in api.py | Add workspace router import + `include_router()` call. Add `set_project_repository()` wiring. | `daemon/api.py` (modify) |
| 6 | Create FileChangeMonitor service | Optional watchdog-based file watcher with polling fallback. Debounces events, pushes to SSE queue. | `daemon/services/file_change_monitor.py` (new) |
| 7 | Write backend tests | Unit tests for WorkspaceGuard, GitDiffService, WorkspaceRouter endpoints. Integration tests for path traversal rejection. | `tests/test_workspace_guard.py`, `tests/test_workspace_api.py` (new) |

---

## Task Details

### Task 1: Extract WorkspaceGuard

**Goal**: Single security boundary shared between agent tools and HTTP endpoints.

**New file**: `daemon/services/workspace_guard.py`

```python
"""Shared workspace path resolution and security boundary.

Extracted from daemon/tools/filesystem.py so both agent tools and HTTP
routers use the same path-traversal protection.
"""
import os
import re
import tempfile
from pathlib import Path


class WorkspaceGuard:
    """Path resolution + boundary checking for workspace file access.

    All HTTP workspace endpoints MUST route through this guard before
    touching the filesystem. The guard:
    1. Resolves relative paths against the project workdir
    2. Canonicalizes via Path.resolve() (resolves .., symlinks)
    3. Verifies target is within workdir (or allowed temp dirs)
    4. Returns (Path, None) on success, (None, error_msg) on failure
    """

    # Configurable limits
    MAX_FILE_SIZE_BYTES = 1_048_576  # 1 MB
    DEFAULT_TREE_DEPTH = 5
    IGNORE_PATTERNS = frozenset({
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".next", ".pytest_cache", ".mypy_cache",
        ".tox", "egg-info", ".eggs",
    })

    def __init__(self, workdir: str):
        self.workdir = Path(workdir).expanduser().resolve()
        if not self.workdir.exists():
            raise ValueError(f"Working directory does not exist: {workdir}")

    def resolve(self, relative_path: str) -> tuple[Path | None, str | None]:
        """Resolve a relative path within the workspace. Returns (path, error).

        Mirrors the logic from _resolve_within_workdir / _resolve_target_path /
        _is_within_workdir / _normed_contains in the original filesystem.py.
        For absolute paths, the boundary check is intentionally skipped (trusted
        by design, same semantics as the agent tools).
        """
        target, base, err = self._resolve_target(relative_path)
        if err:
            return None, err
        if base is not None and not self._contains(base, target):
            return None, f"ERROR: Path escapes workdir boundary: {relative_path}"
        return target, None

    def is_within(self, target: Path) -> bool:
        """Check if a resolved path is within the workspace or allowed temp dirs."""
        return self._contains(self.workdir, target)

    # ------------------------------------------------------------------
    # Internal helpers — ported verbatim from daemon/tools/filesystem.py
    # ------------------------------------------------------------------

    _WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
    _WINDOWS_UNC_RE = re.compile(r"^[\\/]{2}")

    @classmethod
    def _is_absolute_path(cls, path: str) -> bool:
        """Return True if *path* is absolute on the current OS or matches a
        Windows absolute pattern (drive letter or UNC)."""
        if not path:
            return False
        try:
            if Path(path).is_absolute():
                return True
        except (OSError, ValueError):
            return False
        return bool(cls._WINDOWS_DRIVE_RE.match(path) or cls._WINDOWS_UNC_RE.match(path))

    def _resolve_target(
        self, path: str,
    ) -> tuple[Path | None, Path | None, str | None]:
        """Resolve *path* against the workspace workdir.

        Returns ``(target_path, base_path, error)``. ``base_path`` is the
        workdir when *path* is relative (boundary check applies), and ``None``
        when *path* is absolute (no boundary check).
        """
        if self._is_absolute_path(path):
            try:
                return Path(path).expanduser(), None, None
            except (OSError, RuntimeError) as e:
                return None, None, f"ERROR: Invalid absolute path: {e}"

        if not path or not path.strip():
            return (
                None, None,
                "ERROR: workdir is required for relative paths. "
                "Pass an absolute path if workdir is not applicable.",
            )

        base = self.workdir  # already resolved in __init__
        try:
            target = (base / path).expanduser().resolve()
        except (OSError, RuntimeError) as e:
            return None, None, f"ERROR: Invalid path: {e}"
        return target, base, None

    @staticmethod
    def _normed_contains(base: Path, target: Path) -> bool:
        """Check if *target* is within *base* using OS-appropriate case norm."""
        try:
            normed_target = Path(os.path.normcase(str(target.resolve())))
            normed_base = Path(os.path.normcase(str(base.resolve())))
            normed_target.relative_to(normed_base)
            return True
        except (ValueError, OSError):
            return False

    def _contains(self, base: Path, target: Path) -> bool:
        """Check if *target* is within *base* OR an allowed temp directory."""
        if self._normed_contains(base, target):
            return True
        # Allow access to system temp directories (handles macOS /tmp symlink)
        temp_dirs = [
            Path(tempfile.gettempdir()).resolve(),
            Path("/tmp").resolve(),
            Path("/private/tmp").resolve(),
            Path("/var/tmp").resolve(),
        ]
        if os.name == "nt":
            system_drive = os.environ.get("SystemDrive", "C:")
            temp_dirs.extend([
                Path(os.environ.get("TEMP") or tempfile.gettempdir()).resolve(),
                Path(os.environ.get("TMP") or tempfile.gettempdir()).resolve(),
                Path(f"{system_drive}\\tmp").resolve(),
            ])
        return any(self._normed_contains(td, target) for td in temp_dirs)
```
```

**Modify**: `daemon/tools/filesystem.py`
- Replace inline implementations with `from daemon.services.workspace_guard import ...`
- Keep the same function signatures for backward compatibility
- The `@tool`-decorated functions (`read_file`, `list_directory`, etc.) remain unchanged in behavior

**Key Decision**: Keep the existing function-based API in `filesystem.py` as a thin wrapper around `WorkspaceGuard` so no agent tool behavior changes.

### Task 2: Pydantic Schemas

**New file**: `daemon/routers/workspace_schemas.py`

```python
from pydantic import BaseModel, Field


class FileTreeNode(BaseModel):
    name: str
    path: str  # relative to workdir
    type: str  # "file" | "directory" | "symlink"
    size: int | None = None  # bytes, for files only
    children: list["FileTreeNode"] | None = None  # None = not expanded


class FileTreeResponse(BaseModel):
    project_id: str
    path: str  # root path of this tree node
    tree: list[FileTreeNode]
    truncated: bool = False  # true if depth/file-count limit hit


class FileContentResponse(BaseModel):
    project_id: str
    path: str
    content: str
    language: str | None = None  # detected from extension
    total_lines: int
    offset: int = 1  # 1-indexed
    limit: int = 2000
    truncated: bool = False
    binary: bool = False
    size_bytes: int


class GitDiffResponse(BaseModel):
    project_id: str
    path: str
    has_changes: bool
    diff: str | None = None  # unified diff text
    head_content: str | None = None  # HEAD version of file
    working_content: str | None = None  # working tree version
    error: str | None = None  # "not_a_git_repo", etc.


class WorkspaceErrorResponse(BaseModel):
    error: str
    detail: str | None = None
```

> **📋 CONTRACT FREEZE GATE**: After Task 2 (schemas) is complete, the Pydantic
> response models (`FileTreeNode`, `FileTreeResponse`, `FileContentResponse`,
> `GitDiffResponse`) form the frozen API contract. Phase 2 (frontend) can start
> in parallel against mock data matching these exact shapes. Any subsequent
> changes to response shapes MUST be coordinated with Phase 2.

### Task 3: GitDiffService

**New file**: `daemon/services/git_diff_service.py`

```python
"""Git diff service using subprocess invocations.

Security: Path is pre-validated by WorkspaceGuard before reaching this service.
Git is invoked via subprocess.run() with argument list (never shell=True).
"""
import asyncio
import logging
from pathlib import Path

from daemon.constants import GIT_TIMEOUT_S  # e.g., 10

logger = logging.getLogger(__name__)


class GitDiffService:
    """Executes git commands via subprocess with timeout and error handling."""

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self._is_repo: bool | None = None  # lazy cache

    async def is_git_repo(self) -> bool:
        """Check if workdir is inside a git repository."""
        if self._is_repo is not None:
            return self._is_repo
        try:
            result = await asyncio.to_thread(
                self._run_git, ["rev-parse", "--is-inside-work-tree"]
            )
            self._is_repo = result.returncode == 0
        except Exception:
            self._is_repo = False
        return self._is_repo

    async def get_file_diff(self, relative_path: str) -> dict:
        """Get diff of a file against HEAD.

        Returns dict with: has_changes, diff, head_content, working_content,
        error (if any).
        """
        if not await self.is_git_repo():
            return {"has_changes": False, "error": "not_a_git_repo"}

        # W4: Use HEAD:./{path} form for pathspec safety. The ``./`` prefix
        # disambiguates a path from a revision name (e.g., a file named
        # ``master`` won't be confused with the ``master`` branch).
        try:
            # git diff HEAD -- <path> — empty output = no changes
            diff_result = await asyncio.to_thread(
                self._run_git, ["diff", "HEAD", "--", relative_path]
            )
            # git show HEAD:./{path} — committed version of the file
            head_result = await asyncio.to_thread(
                self._run_git, ["show", f"HEAD:./{relative_path}"]
            )
        except Exception as e:
            logger.warning("Git subprocess error for %s: %s", relative_path, e)
            return {"has_changes": False, "error": f"git_error: {e}"}

        # W6: Diff output size limit (same 1MB budget as file content)
        diff_text = diff_result.stdout
        if len(diff_text) > 1_048_576:
            return {"has_changes": True, "diff": "(diff too large to display)",
                    "head_content": None, "working_content": None, "error": "diff_too_large"}

        # File is new (not in HEAD) — git show returns non-zero
        head_content = head_result.stdout if head_result.returncode == 0 else None
        has_changes = bool(diff_text.strip()) or head_content is None

        # Read working tree content for the "b" side of the merge view.
        # Fall back to reading the file directly if git diff is empty but
        # we still need the working content for display.
        working_content = None
        working_file = self.workdir / relative_path
        try:
            working_content = working_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            working_content = None  # binary or deleted — UI handles gracefully

        return {
            "has_changes": has_changes,
            "diff": diff_text if has_changes else None,
            "head_content": head_content,
            "working_content": working_content,
            "error": None,
        }

    def _run_git(self, args: list[str]):
        """Synchronous git subprocess call. Called via asyncio.to_thread.

        W7: All git exceptions (TimeoutExpired, FileNotFoundError, OSError)
        are caught by the caller's try/except. Returns CompletedProcess even
        on non-zero exit (returncode is checked by the caller).
        """
        import subprocess
        return subprocess.run(
            ["git"] + args,
            cwd=self.workdir,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
        )
```

**Key Decisions**:
- **subprocess, not GitPython**: Subprocess is simpler, no dependency, and git is universally available.
- **asyncio.to_thread**: Blocking subprocess calls wrapped in `asyncio.to_thread()` per existing pattern.
- **Timeout**: 10-second hard timeout per git invocation (configurable constant).
- **Path pre-validation**: GitDiffService trusts that `relative_path` has already passed through `WorkspaceGuard.resolve()`. Git receives only the relative path (never absolute), which is safe against injection.

### Task 4: WorkspaceRouter

**New file**: `daemon/routers/workspace.py`

```python
"""Workspace file viewer API endpoints.

Provides read-only file tree, file content, git diff, and SSE file-change
events for a project's working directory. All paths are validated through
WorkspaceGuard before filesystem access.
"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from daemon.constants import SSE_PING_INTERVAL, SSE_TIMEOUT_S
from daemon.repositories import SQLModelProjectRepository
from daemon.services.workspace_guard import WorkspaceGuard
from daemon.services.git_diff_service import GitDiffService
from daemon.services.file_change_monitor import FileChangeMonitor

from .workspace_schemas import (
    FileTreeResponse, FileContentResponse, GitDiffResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspace", tags=["workspace"])

_project_repo: SQLModelProjectRepository | None = None


def set_project_repository(repo: SQLModelProjectRepository) -> None:
    global _project_repo
    _project_repo = repo


async def _get_workdir(project_id: str) -> str:
    """Resolve the workdir for a project. Raises HTTPException on error.

    Uses ``repo.get(project_id)`` (not ``get_by_id`` which does not exist).
    The repository call is synchronous SQLAlchemy — wrapped in
    ``asyncio.to_thread`` so it does not block the event loop.
    """
    if _project_repo is None:
        raise HTTPException(status_code=503, detail={"error": "Project repository not initialized"})
    project = await asyncio.to_thread(_project_repo.get, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail={"error": "Project not found"})
    if not project.main_directory:
        raise HTTPException(status_code=400, detail={"error": "Project has no main_directory configured"})
    return project.main_directory


# --- GET /workspace/{project_id}/tree ---
@router.get("/{project_id}/tree", response_model=FileTreeResponse)
async def get_file_tree(
    project_id: str,
    path: str = Query(default=".", description="Directory path relative to workdir"),
    depth: int = Query(default=WorkspaceGuard.DEFAULT_TREE_DEPTH, ge=1, le=10),
):
    """Get file tree for a directory within the project workspace."""
    workdir = await _get_workdir(project_id)
    guard = WorkspaceGuard(workdir)
    target, err = guard.resolve(path)
    if err:
        raise HTTPException(status_code=403, detail={"error": err})

    tree = await asyncio.to_thread(_build_tree, target, workdir, guard, depth)
    return FileTreeResponse(project_id=project_id, path=path, tree=tree)


# --- GET /workspace/{project_id}/file ---
@router.get("/{project_id}/file", response_model=FileContentResponse)
async def get_file_content(
    project_id: str,
    path: str = Query(..., description="File path relative to workdir"),
    offset: int = Query(default=1, ge=1),
    limit: int = Query(default=2000, ge=1, le=5000),
):
    """Read file content from the project workspace."""
    workdir = await _get_workdir(project_id)
    guard = WorkspaceGuard(workdir)
    target, err = guard.resolve(path)
    if err:
        raise HTTPException(status_code=403, detail={"error": err})

    content_data = await asyncio.to_thread(_read_file_safe, target, guard, offset, limit)
    if content_data.get("error"):
        status = 413 if content_data["error"] == "file_too_large" else 400
        raise HTTPException(status_code=status, detail={"error": content_data["error"]})

    return FileContentResponse(
        project_id=project_id,
        path=path,
        content=content_data["content"],
        language=_detect_language(target.name),
        total_lines=content_data["total_lines"],
        offset=offset,
        limit=limit,
        truncated=content_data["truncated"],
        binary=content_data["binary"],
        size_bytes=content_data["size_bytes"],
    )


# --- GET /workspace/{project_id}/diff ---
@router.get("/{project_id}/diff", response_model=GitDiffResponse)
async def get_file_diff(
    project_id: str,
    path: str = Query(..., description="File path relative to workdir"),
):
    """Get git diff of a file against HEAD."""
    workdir = await _get_workdir(project_id)
    guard = WorkspaceGuard(workdir)
    target, err = guard.resolve(path)
    if err:
        raise HTTPException(status_code=403, detail={"error": err})

    git_service = GitDiffService(guard.workdir)
    relative_path = str(target.relative_to(guard.workdir))
    diff_data = await git_service.get_file_diff(relative_path)
    return GitDiffResponse(project_id=project_id, path=path, **diff_data)


# --- GET /workspace/{project_id}/events (SSE) ---
@router.get("/{project_id}/events")
async def stream_file_events(
    project_id: str,
    request: Request,
):
    """SSE stream of file-change events for the project workspace."""
    workdir = await _get_workdir(project_id)
    monitor = FileChangeMonitor.get_or_create(workdir)

    async def event_generator():
        import json
        queue = asyncio.Queue(maxsize=100)
        conn_id = await monitor.add_subscriber(queue)
        try:
            yield {"event": "connected", "data": json.dumps({"status": "connected", "project_id": project_id})}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=SSE_TIMEOUT_S)
                    yield {"event": "file_changed", "data": json.dumps(event)}
                except asyncio.TimeoutError:
                    yield {"event": "keepalive", "data": "{}"}
        finally:
            await monitor.remove_subscriber(conn_id)

    return EventSourceResponse(event_generator(), ping=SSE_PING_INTERVAL)
```

**Helper functions** (in the same file or a `_workspace_helpers.py`):

```python
def _build_tree(target: Path, workdir: Path, guard: WorkspaceGuard, depth: int) -> list[FileTreeNode]:
    """Recursively build file tree to specified depth.

    Uses ``entry.lstat()`` (not ``stat()``) to avoid following symlinks when
    reading metadata. Symlinks are reported as type ``"symlink"`` and never
    traversed (``children = None``). The loop body is wrapped in
    ``try/except`` so a single permission error does not abort the entire tree.
    """
    if depth <= 0 or not target.is_dir():
        return []
    nodes = []
    for entry in sorted(target.iterdir()):
        if entry.name in guard.IGNORE_PATTERNS:
            continue
        try:
            stat_info = entry.lstat()  # lstat: don't follow symlinks
            is_symlink = entry.is_symlink()

            if is_symlink:
                node_type = "symlink"
            elif stat_info.st_mode & 0o170000 == 0o040000:  # S_ISDIR
                node_type = "directory"
            else:
                node_type = "file"

            rel = str(entry.relative_to(workdir))
            node = FileTreeNode(
                name=entry.name,
                path=rel,
                type=node_type,
                size=stat_info.st_size if node_type == "file" else None,
            )
            # Only recurse into real directories (never symlinks, even if they
            # point to a directory — prevents escaping the workspace via symlink).
            if node_type == "directory" and depth > 1:
                node.children = _build_tree(entry, workdir, guard, depth - 1)
            nodes.append(node)
        except (PermissionError, OSError) as e:
            logger.debug("Skipping %s in tree: %s", entry.name, e)
            continue
    return nodes


def _read_file_safe(target: Path, guard: WorkspaceGuard, offset: int, limit: int) -> dict:
    """Read file with size/binary checks. Returns dict with content + metadata.

    Handles file-read races gracefully: if the file is deleted or modified
    between the stat() and read(), returns an error dict rather than crashing.
    """
    try:
        stat = target.stat()
    except FileNotFoundError:
        return {"error": "file_not_found", "binary": False, "content": "",
                "total_lines": 0, "truncated": False, "size_bytes": 0}
    except OSError as e:
        return {"error": f"stat_error: {e}", "binary": False, "content": "",
                "total_lines": 0, "truncated": False, "size_bytes": 0}

    if stat.st_size > guard.MAX_FILE_SIZE_BYTES:
        return {"error": "file_too_large", "size_bytes": stat.st_size}

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"binary": True, "content": "", "size_bytes": stat.st_size,
                "total_lines": 0, "truncated": False, "error": None}
    except FileNotFoundError:
        return {"error": "file_not_found", "binary": False, "content": "",
                "total_lines": 0, "truncated": False, "size_bytes": 0}
    except OSError as e:
        return {"error": f"read_error: {e}", "binary": False, "content": "",
                "total_lines": 0, "truncated": False, "size_bytes": stat.st_size}

    lines = content.splitlines()
    start = max(0, offset - 1)
    selected = lines[start:start + limit]
    return {
        "content": "\n".join(selected),
        "total_lines": len(lines),
        "truncated": len(lines) > start + limit,
        "binary": False,
        "size_bytes": stat.st_size,
        "error": None,
    }


# Language detection from file extension
_LANGUAGE_MAP = {
    ".py": "python", ".ts": "typescript", ".js": "javascript",
    ".html": "html", ".css": "css", ".scss": "scss",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".md": "markdown", ".sql": "sql", ".sh": "shell",
    ".go": "go", ".rs": "rust", ".java": "java",
    ".tsx": "tsx", ".jsx": "jsx",
}

def _detect_language(filename: str) -> str | None:
    return _LANGUAGE_MAP.get(Path(filename).suffix)
```

### Task 5: Register Router in api.py

**Modify**: `daemon/api.py`

Add to the import section (near line ~50 where other routers are imported):
```python
from daemon.routers.workspace import router as workspace_router
```

Add to the router registration block (after line ~1372):
```python
api_router.include_router(workspace_router)     # /api/workspace
```

Add to the app initialization section (where `set_project_repository` is called for other routers):
```python
from daemon.routers.workspace import set_project_repository as set_workspace_project_repo
set_workspace_project_repo(project_repo)
```

### Task 6: FileChangeMonitor Service

**New file**: `daemon/services/file_change_monitor.py`

```python
"""File change monitor with optional watchdog integration.

Uses watchdog for efficient filesystem notifications when available.
Falls back to polling (5s interval) when watchdog is not installed.

Events are debounced: rapid successive changes to the same file are
coalesced into a single event with a minimum 2-second gap.
"""
import asyncio
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Try importing watchdog — optional dependency
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False


class FileChangeMonitor:
    """Per-workdir file change monitor.

    Uses watchdog for efficient filesystem notifications when available.
    Falls back to polling (5s interval) when watchdog is not installed.

    Events are debounced: rapid successive changes to the same file are
    coalesced into a single event with a minimum 2-second gap.

    Thread safety: watchdog's Observer runs on its own thread. asyncio.Queue
    is NOT thread-safe, so ``_emit`` uses ``loop.call_soon_threadsafe`` to
    schedule the ``put_nowait`` on the event loop (same pattern as
    ``daemon/services/dispatch_event_bus.py:67`` and
    ``daemon/services/completion_registry.py:133``).
    """

    _instances: dict[str, "FileChangeMonitor"] = {}

    @classmethod
    def get_or_create(cls, workdir: str) -> "FileChangeMonitor":
        """Get existing monitor or create a new one.

        If the existing instance was stopped (no subscribers), it is evicted
        from the registry and a fresh one is created. This prevents returning
        a dead instance with a terminated Observer.
        """
        key = str(Path(workdir).resolve())
        existing = cls._instances.get(key)
        if existing is not None and existing._started:
            return existing
        # Evict dead instance (W2)
        if existing is not None:
            cls._instances.pop(key, None)
        instance = cls(workdir)
        cls._instances[key] = instance
        return instance

    def __init__(self, workdir: str):
        self.workdir = Path(workdir)
        self._subscribers: dict[str, asyncio.Queue] = {}
        self._debounce: dict[str, float] = {}  # path -> last_emit_time
        self._observer: Observer | None = None
        self._poll_task: asyncio.Task | None = None
        self._started = False
        self._loop: asyncio.AbstractEventLoop | None = None

    async def add_subscriber(self, queue: asyncio.Queue) -> str:
        import uuid
        # Capture the running loop for thread-safe callbacks (Blocking Fix 1).
        # Must be set here (inside the async context) rather than __init__
        # which may be called from get_or_create without a running loop.
        self._loop = asyncio.get_running_loop()
        conn_id = str(uuid.uuid4())
        self._subscribers[conn_id] = queue
        if not self._started:
            self._start()
        return conn_id

    async def remove_subscriber(self, conn_id: str):
        self._subscribers.pop(conn_id, None)
        if not self._subscribers:
            self._stop()

    def _emit(self, event_data: dict):
        """Emit event to all subscribers with debounce.

        Called from watchdog's Observer thread (non-async context).
        Uses ``call_soon_threadsafe`` to schedule the queue put on the event
        loop, since asyncio.Queue is not thread-safe.
        """
        path = event_data.get("path", "")
        now = time.time()
        if path in self._debounce and now - self._debounce[path] < 2.0:
            return  # debounce: skip
        self._debounce[path] = now

        if self._loop is None:
            # No event loop captured yet — can happen if _emit fires before
            # any subscriber connects. Safe to drop.
            return

        for queue in self._subscribers.values():
            try:
                # Thread-safe: schedule put_nowait on the event loop
                self._loop.call_soon_threadsafe(self._safe_put, queue, event_data)
            except RuntimeError:
                # Event loop closed between check and call — drop event
                logger.debug("Event loop closed, dropping file-change event")
                continue

    def _safe_put(self, queue: asyncio.Queue, event_data: dict):
        """Scheduled on the event loop via call_soon_threadsafe."""
        try:
            queue.put_nowait(event_data)
        except asyncio.QueueFull:
            logger.warning("File change queue full, dropping event")

    def _start(self):
        """Start monitoring."""
        self._started = True
        if HAS_WATCHDOG:
            self._start_watchdog()
        else:
            self._poll_task = asyncio.create_task(self._poll_loop())

    def _start_watchdog(self):
        """Create a fresh Observer and schedule it.

        Blocking Fix 3: watchdog.Observer is single-shot — once ``stop()`` is
        called, its internal thread terminates and the instance cannot be
        restarted. We always create a new Observer here, never reuse.
        """
        class _Handler(FileSystemEventHandler):
            def __init__(self, monitor: FileChangeMonitor):
                self._monitor = monitor

            def on_any_event(self, event):
                if event.is_directory:
                    return
                rel = os.path.relpath(
                    event.src_path, str(self._monitor.workdir)
                )
                self._monitor._emit({
                    "path": rel,
                    "change_type": event.event_type,
                    "timestamp": time.time(),
                })

        self._observer = Observer()
        self._observer.schedule(
            _Handler(self), str(self.workdir), recursive=True
        )
        self._observer.start()

    def _stop(self):
        """Stop monitoring and evict from registry when no subscribers remain.

        Blocking Fix 3 + W2: When the last subscriber disconnects, stop the
        monitor and remove it from ``_instances`` so the next
        ``get_or_create`` call creates a fresh instance with a new Observer.
        """
        self._started = False
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        # Evict from singleton registry (W2)
        key = str(self.workdir)
        FileChangeMonitor._instances.pop(key, None)

    async def _poll_loop(self):
        """Fallback polling implementation (no watchdog).

        Snapshots the directory listing and compares against the previous
        snapshot every 5 seconds, emitting events for changed/added/removed
        files.
        """
        import os
        last_snapshot: dict[str, float] = {}
        # Initial snapshot
        last_snapshot = self._scan_mtimes()
        while self._started:
            await asyncio.sleep(5)
            current = self._scan_mtimes()
            for path, mtime in current.items():
                if path not in last_snapshot or last_snapshot[path] != mtime:
                    self._emit({
                        "path": path,
                        "change_type": "modified",
                        "timestamp": time.time(),
                    })
            for path in last_snapshot:
                if path not in current:
                    self._emit({
                        "path": path,
                        "change_type": "deleted",
                        "timestamp": time.time(),
                    })
            last_snapshot = current

    def _scan_mtimes(self) -> dict[str, float]:
        """Walk workdir and return {relative_path: mtime} for all files."""
        result: dict[str, float] = {}
        try:
            for root, dirs, files in os.walk(self.workdir):
                # Prune ignored dirs
                dirs[:] = [
                    d for d in dirs
                    if d not in WorkspaceGuard.IGNORE_PATTERNS
                ]
                for fname in files:
                    full = os.path.join(root, fname)
                    try:
                        rel = os.path.relpath(full, str(self.workdir))
                        result[rel] = os.path.getmtime(full)
                    except OSError:
                        continue
        except OSError:
            pass
        return result
```

### Task 7: Backend Tests

**New files**:

`tests/test_workspace_guard.py`:
- Test path traversal rejection (`../../../etc/passwd`)
- Test symlink resolution (symlink pointing outside workdir)
- Test absolute path handling
- Test temp directory access
- Test missing workdir error
- Test ignore pattern filtering

`tests/test_workspace_api.py`:
- Test `GET /tree` returns correct structure
- Test `GET /file` returns content with metadata
- Test `GET /file` rejects path traversal (403)
- Test `GET /file` returns 413 for oversized files
- Test `GET /file` returns binary=true for binary files
- Test `GET /diff` returns diff for modified file
- Test `GET /diff` returns error for non-git repo
- Test `GET /events` SSE stream connects and receives keepalive
- Test project without `main_directory` returns 400
- Test nonexistent project returns 404

Test pattern follows `tests/api/test_projects.py`:
```python
@pytest_asyncio.fixture
async def client(engine):
    repo = SQLModelProjectRepository(engine)
    # ... create project with main_directory = tempdir ...
    set_project_repository(repo)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, repo
```

---

## Key Files

| File | Action | Purpose |
|------|--------|---------|
| `daemon/services/workspace_guard.py` | **CREATE** | Extracted path security boundary (from filesystem.py) |
| `daemon/services/git_diff_service.py` | **CREATE** | Git diff via subprocess |
| `daemon/services/file_change_monitor.py` | **CREATE** | File change watcher (watchdog/polling) |
| `daemon/routers/workspace.py` | **CREATE** | 4 REST/SSE endpoints |
| `daemon/routers/workspace_schemas.py` | **CREATE** | Pydantic request/response models |
| `daemon/tools/filesystem.py` | **MODIFY** | Delegate to WorkspaceGuard |
| `daemon/api.py` | **MODIFY** | Register workspace router |
| `daemon/constants.py` | **MODIFY** | Add `GIT_TIMEOUT_S`, `SSE_QUEUE_MAXSIZE` (if missing) |
| `tests/test_workspace_guard.py` | **CREATE** | Security boundary tests |
| `tests/test_workspace_api.py` | **CREATE** | API endpoint tests |

## Constraints

- All filesystem reads MUST go through `WorkspaceGuard.resolve()` — no raw `open()` or `Path.read_text()` in the router
- Git subprocess MUST use argument list (never `shell=True`)
- Git subprocess MUST have a timeout (default 10s)
- All blocking I/O (file read, git, tree build) MUST be wrapped in `asyncio.to_thread()`
- File content response MUST include size limit check (1MB default)
- Binary file detection MUST happen before text decode attempt
- SSE events MUST be debounced (min 2s per path)
- Tests MUST run against both SQLite and PostgreSQL (per project convention)

## Deliverables

- [ ] `daemon/services/workspace_guard.py` with `WorkspaceGuard` class
- [ ] `daemon/services/git_diff_service.py` with `GitDiffService` class
- [ ] `daemon/services/file_change_monitor.py` with `FileChangeMonitor` class
- [ ] `daemon/routers/workspace.py` with 4 endpoints
- [ ] `daemon/routers/workspace_schemas.py` with all Pydantic models
- [ ] `daemon/tools/filesystem.py` updated to use `WorkspaceGuard`
- [ ] `daemon/api.py` registers workspace router
- [ ] `daemon/constants.py` has `GIT_TIMEOUT_S`
- [ ] All existing filesystem tests still pass (regression check)
- [ ] New workspace tests pass
- [ ] Path traversal attack tests pass
