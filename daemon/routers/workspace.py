"""Workspace file viewer API endpoints.

Provides read-only file tree, file content, git diff, and SSE file-change
events for a project's working directory. All paths are validated through
WorkspaceGuard before filesystem access.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from daemon.constants import SSE_PING_INTERVAL, SSE_TIMEOUT_S
from daemon.repositories import SQLModelProjectRepository
from daemon.services.workspace_guard import WorkspaceGuard
from daemon.services.git_diff_service import GitDiffService
from daemon.services.file_change_monitor import FileChangeMonitor

from .workspace_schemas import (
    FileTreeResponse, FileContentResponse, GitDiffResponse,
    FileTreeNode, FileWriteRequest, FileWriteResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspace", tags=["workspace"])

_project_repo: SQLModelProjectRepository | None = None


def set_project_repository(repo: SQLModelProjectRepository) -> None:
    global _project_repo
    _project_repo = repo


async def _get_workdir(project_id: str) -> str:
    """Resolve the workdir for a project. Raises HTTPException on error."""
    if _project_repo is None:
        raise HTTPException(status_code=503, detail={"error": "Project repository not initialized"})
    project = await asyncio.to_thread(_project_repo.get, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail={"error": "Project not found"})
    if not project.main_directory:
        raise HTTPException(status_code=400, detail={"error": "Project has no main_directory configured"})
    return project.main_directory


def _get_guard(workdir: str) -> WorkspaceGuard:
    """Create WorkspaceGuard, catching ValueError for missing/deleted dirs."""
    try:
        return WorkspaceGuard(workdir)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "Workspace directory not found"},
        )


@router.get("/{project_id}/tree", response_model=FileTreeResponse)
async def get_file_tree(
    project_id: str,
    path: str = Query(default=".", description="Directory path relative to workdir"),
    depth: int = Query(default=WorkspaceGuard.DEFAULT_TREE_DEPTH, ge=1, le=10),
):
    """Get file tree for a directory within the project workspace."""
    workdir = await _get_workdir(project_id)
    guard = _get_guard(workdir)
    target, err = guard.resolve_strict(path)
    if err:
        raise HTTPException(status_code=403, detail={"error": err})
    tree = await asyncio.to_thread(_build_tree, target, guard.workdir, guard, depth)
    return FileTreeResponse(project_id=project_id, path=path, tree=tree)


@router.get("/{project_id}/file", response_model=FileContentResponse)
async def get_file_content(
    project_id: str,
    path: str = Query(..., description="File path relative to workdir"),
    offset: int = Query(default=1, ge=1),
    limit: int = Query(default=2000, ge=1, le=5000),
):
    """Read file content from the project workspace."""
    workdir = await _get_workdir(project_id)
    guard = _get_guard(workdir)
    target, err = guard.resolve_strict(path)
    if err:
        raise HTTPException(status_code=403, detail={"error": err})
    content_data = await asyncio.to_thread(_read_file_safe, target, guard, offset, limit)
    if content_data.get("error"):
        status = 413 if content_data["error"] == "file_too_large" else 400
        raise HTTPException(status_code=status, detail={"error": content_data["error"]})
    return FileContentResponse(
        project_id=project_id, path=path,
        content=content_data["content"],
        language=_detect_language(target.name),
        total_lines=content_data["total_lines"],
        offset=offset, limit=limit,
        truncated=content_data["truncated"],
        binary=content_data["binary"],
        size_bytes=content_data["size_bytes"],
    )


@router.put("/{project_id}/file", response_model=FileWriteResponse)
async def put_file_content(
    project_id: str,
    body: FileWriteRequest,
):
    """Write file content to the project workspace.

    Mirrors the read endpoint's path-safety story: ``resolve_strict``
    enforces the workdir boundary before any filesystem write, and the
    content size is bounded by the same ``MAX_FILE_SIZE_BYTES`` cap
    applied on read.
    """
    workdir = await _get_workdir(project_id)
    guard = _get_guard(workdir)
    target, err = guard.resolve_strict(body.path)
    if err:
        raise HTTPException(status_code=403, detail={"error": err})
    content_bytes = body.content.encode("utf-8")
    if len(content_bytes) > guard.MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"error": "file_too_large", "size_bytes": len(content_bytes)},
        )
    try:
        await asyncio.to_thread(_write_file_safe, target, body.content)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "path_is_directory"})
    return FileWriteResponse(
        project_id=project_id,
        path=body.path,
        size_bytes=len(content_bytes),
        saved=True,
    )


@router.get("/{project_id}/diff", response_model=GitDiffResponse)
async def get_file_diff(
    project_id: str,
    path: str = Query(..., description="File path relative to workdir"),
):
    """Get git diff of a file against HEAD."""
    workdir = await _get_workdir(project_id)
    guard = _get_guard(workdir)
    target, err = guard.resolve_strict(path)
    if err:
        raise HTTPException(status_code=403, detail={"error": err})
    git_service = GitDiffService(guard.workdir)
    relative_path = str(target.relative_to(guard.workdir))
    diff_data = await git_service.get_file_diff(relative_path)
    return GitDiffResponse(project_id=project_id, path=path, **diff_data)


@router.get("/{project_id}/events")
async def stream_file_events(project_id: str, request: Request):
    """SSE stream of file-change events for the project workspace."""
    workdir = await _get_workdir(project_id)
    monitor = FileChangeMonitor.get_or_create(workdir)
    async def event_generator():
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


# --- Helper functions ---

def _build_tree(target: Path, workdir: Path, guard: WorkspaceGuard, depth: int) -> list[FileTreeNode]:
    if depth <= 0 or not target.is_dir():
        return []
    nodes = []
    for entry in sorted(target.iterdir()):
        if entry.name in guard.IGNORE_PATTERNS:
            continue
        try:
            stat_info = entry.lstat()
            is_symlink = entry.is_symlink()
            if is_symlink:
                node_type = "symlink"
            elif stat_info.st_mode & 0o170000 == 0o040000:  # S_ISDIR
                node_type = "directory"
            else:
                node_type = "file"
            rel = str(entry.relative_to(workdir))
            node = FileTreeNode(
                name=entry.name, path=rel, type=node_type,
                size=stat_info.st_size if node_type == "file" else None,
            )
            if node_type == "directory" and depth > 1:
                node.children = _build_tree(entry, workdir, guard, depth - 1)
            nodes.append(node)
        except (PermissionError, OSError) as e:
            logger.debug("Skipping %s in tree: %s", entry.name, e)
            continue
    return nodes


def _read_file_safe(target: Path, guard: WorkspaceGuard, offset: int, limit: int) -> dict:
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


def _write_file_safe(target: Path, content: str) -> None:
    """Write ``content`` to ``target``, creating parent dirs as needed.

    Synchronous — call via ``asyncio.to_thread`` from async endpoints
    so the event loop isn't blocked on filesystem I/O. Caller is
    responsible for any size validation (see the endpoint's
    ``MAX_FILE_SIZE_BYTES`` check) and path containment
    (``resolve_strict``).
    """
    if target.is_dir():
        raise ValueError("path_is_directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


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