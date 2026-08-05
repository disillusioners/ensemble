"""Restricted doc-write tool for the doc-maintainer agent.

The doc-maintainer agent is mechanically prevented from calling ``write_file`` /
``edit_file`` — this module is its ONLY write surface for markdown docs.

Path safety:
  * **Allowlist** — paths must start with ``docs/``, ``doc/``, or be a top-level
    ``*.md`` file (e.g., ``README.md``).
  * **Denylist** — paths under ``.agents/``, ``daemon/``, ``frontend/``,
    ``node_modules/`` are rejected regardless of prefix.
  * **Realpath containment** — resolved paths must stay inside the project
    workdir (defends against ``../`` escapes).
  * **Binary rejection** — paths with binary file extensions are rejected
    even if the allowlist would otherwise permit them.

Write safety:
  * **Atomic write** via ``tempfile.NamedTemporaryFile`` + ``os.replace``.
  * **File locking** via ``fcntl.flock`` with a short bounded timeout.

Phase 1 scope: docs/ + top-level *.md. comment_edit handles code comments
separately (see :mod:`daemon.tools.comment_edit`).
"""

from __future__ import annotations

import fcntl
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "DocMaintenance"
CATEGORY_DOC = """Restricted doc-write tools for the doc-maintainer agent.

doc_write() creates or updates markdown documentation files within an allowlisted
path scope (docs/, doc/, top-level *.md). Rejects .agents/, daemon/, frontend/,
node_modules/, and binary files. Atomic write via tempfile + os.replace.

This tool is the ONLY write surface available to the doc-maintainer agent —
write_file / edit_file are deliberately absent from its tools.allow list.
"""

# Path prefixes that are NEVER writable by doc_write, even if they would
# otherwise match the allowlist (defense in depth).
_DENYLIST_PREFIXES: tuple[str, ...] = (
    ".agents/",
    "daemon/",
    "frontend/",
    "node_modules/",
    ".git/",
    "__pycache__/",
)

# Top-level directory prefixes that ARE writable. The check uses prefix
# match against the normalized relative path.
_ALLOWLIST_PREFIXES: tuple[str, ...] = (
    "docs/",
    "doc/",
)

# File extensions that are NEVER writable regardless of path (binary content).
_BINARY_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".webm",
    ".ttf", ".otf", ".woff", ".woff2",
    ".key", ".pem", ".crt", ".p12",  # secrets-ish; never auto-edit
})

# File-locking timeout (seconds) — short to keep doc writes responsive.
_LOCK_TIMEOUT_S: float = 2.0


def _validate_doc_path(rel_path: str, workdir: Path) -> tuple[Path, str | None]:
    """Validate a relative path for doc_write. Returns (resolved_path, error_or_None).

    A non-None error string indicates rejection; the caller should return it
    as the tool's result without performing any write.
    """
    if not rel_path:
        return workdir, "PATH_EMPTY: relative path is required"

    # Reject absolute paths outright — caller should pass a relative path.
    if os.path.isabs(rel_path):
        return workdir, f"PATH_ABSOLUTE: must be relative to project root, got {rel_path!r}"

    # Normalize: convert backslashes, strip at most ONE leading "./" prefix.
    # Use a regex-style approach: only strip if the path actually starts with "./".
    normalized = rel_path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    elif normalized.startswith("/"):
        normalized = normalized.lstrip("/")

    # Reject paths containing parent-directory references — even if the
    # textual prefix matches the allowlist, allowing `docs/../secrets.md`
    # would let the writer escape scope. The simplest, safest rule: no `..`
    # path segments ever.
    if ".." in normalized.split("/"):
        return (
            workdir,
            f"PATH_REJECTED: {rel_path!r} contains '..' path segments — disallowed",
        )

    # Check binary extension FIRST (cheap, decisive).
    suffix = Path(normalized).suffix.lower()
    if suffix in _BINARY_EXTENSIONS:
        return workdir, f"BINARY_REJECTED: extension {suffix!r} is not writable"

    # Check denylist — substring match against normalized prefixes.
    for denied in _DENYLIST_PREFIXES:
        if normalized.startswith(denied) or f"/{denied}" in normalized:
            return workdir, f"PATH_REJECTED: {denied!r} is in the denylist"

    # Check allowlist — either prefix-match OR top-level *.md.
    allowed = any(normalized.startswith(prefix) for prefix in _ALLOWLIST_PREFIXES)
    if not allowed:
        # Allow top-level *.md files only (e.g., README.md, CHANGELOG.md).
        # Top-level means no "/" in the path. Reject nested *.md under
        # arbitrary directories.
        if "/" not in normalized and suffix == ".md":
            allowed = True
        else:
            return (
                workdir,
                f"PATH_REJECTED: {normalized!r} is not under docs/, doc/, or a top-level *.md file",
            )

    # Realpath containment check — defends against sneaky escapes (symlinks,
    # bind mounts, edge cases the textual check can't see).
    try:
        target_abs = (workdir / normalized).resolve()
        workdir_abs = workdir.resolve()
        # Use os.path.commonpath for the canonical containment check.
        try:
            common = os.path.commonpath([str(target_abs), str(workdir_abs)])
        except ValueError:
            return workdir, f"PATH_REJECTED: {normalized!r} resolves outside project root"
        if common != str(workdir_abs):
            return workdir, f"PATH_REJECTED: {normalized!r} resolves outside project root"
    except (OSError, RuntimeError) as exc:
        return workdir, f"PATH_REJECTED: realpath resolution failed: {exc}"

    return target_abs, None


@contextmanager
def _lock_path(path: Path, timeout: float = _LOCK_TIMEOUT_S):
    """Acquire an exclusive fcntl flock on a sibling .lock file.

    Used to serialize concurrent doc_write calls touching the same path.
    Raises TimeoutError if the lock cannot be acquired within `timeout`.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    fd = open(lock_path, "r+", encoding="utf-8")
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while time.monotonic() < deadline:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (IOError, OSError, BlockingIOError):
                time.sleep(0.05)
        if not acquired:
            raise TimeoutError(f"could not acquire lock on {path} within {timeout}s")
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        try:
            fd.close()
        except Exception:
            pass
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


def _atomic_write_text(target: Path, content: str) -> None:
    """Atomically write `content` to `target` via tempfile + os.replace.

    Ensures the target is either fully written or untouched. Writes to a
    sibling .tmp file in the same directory (same filesystem guarantees
    atomic rename), fsyncs, then renames into place.
    """
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(str(tmp_path), str(target))
    except Exception:
        # Best-effort cleanup of the temp file on failure.
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def create_doc_write_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    agent_id: str = "",
) -> list:
    """Create doc_write tool with injected manager reference.

    Args:
        manager: The InstanceManager instance used to resolve project context.
        current_instance_id: The ID of the current instance.
        agent_id: The ``agent_id`` of the calling instance. Defaults to ``""``.

    Returns:
        List containing the ``doc_write`` tool (single-element list).
    """

    def _get_workdir() -> Path | None:
        """Resolve the project workdir from instance context.

        Falls back to ``manager.workdir`` if instance lookup fails.
        """
        try:
            inst = manager._instance_repository.get(current_instance_id)
            if inst is not None and getattr(inst, "project_id", None):
                project_id = inst.project_id
                try:
                    project = manager._project_repository.get(project_id)
                    if project is not None and getattr(project, "workdir", None):
                        return Path(project.workdir)
                except Exception:
                    pass
        except Exception:
            pass
        # Fallback to the manager-level workdir attribute if present.
        fallback = getattr(manager, "workdir", None)
        if fallback is not None:
            return Path(fallback)
        return None

    @register_tool_category("doc_maintenance")
    @tool
    def doc_write(
        path: str,
        content: str,
        mode: str = "update",
    ) -> str:
        """Write a markdown documentation file within the doc-maintainer's allowlisted path scope.

        Restricted tool — only the doc-maintainer agent should invoke this. Rejects
        paths outside docs/, doc/, or top-level *.md files; rejects .agents/,
        daemon/, frontend/, node_modules/, and binary extensions. Performs atomic
        write via tempfile + os.replace.

        Args:
            path: Relative path from project workdir (e.g., "docs/api/auth.md").
            content: Full file content as a UTF-8 string.
            mode: "create" to fail if the file exists, "update" to overwrite.

        Returns:
            Success message with the written path, or a rejection error.
        """
        workdir = _get_workdir()
        if workdir is None:
            return "Error: project workdir not available from instance context"

        # Validate mode first (cheap).
        if mode not in ("create", "update"):
            return f"MODE_REJECTED: mode must be 'create' or 'update', got {mode!r}"

        target, err = _validate_doc_path(path, workdir)
        if err is not None:
            return f"Error: {err}"

        # Mode-specific existence check.
        exists = target.exists()
        if mode == "create" and exists:
            return f"Error: MODE_REJECTED: file already exists at {path!r} (mode=create)"
        if mode == "update" and not exists:
            # Update mode creates if missing — same as update semantics in
            # most editors. Surface a hint for the caller.
            logger.info("doc_write: update on non-existent file %s (creating)", path)

        # Acquire file lock + atomic write.
        try:
            with _lock_path(target):
                _atomic_write_text(target, content)
        except TimeoutError as exc:
            return f"Error: LOCK_TIMEOUT: {exc}"
        except OSError as exc:
            return f"WRITE_FAILED: {exc}"

        return f"OK: wrote {len(content)} bytes to {path} (mode={mode})"

    return [doc_write]
