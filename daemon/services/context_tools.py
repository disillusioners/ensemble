"""Low-level filesystem helpers for the shared context directory.

Provides:

- :func:`resolve_context_dir` — canonical location for ``{tempdir}/ensemble/context/{context_key}``.
- :func:`list_context_files` — enumerate ``.md`` files for a context key.
- :func:`read_context_file` — safely read a single context file (no path traversal).

These are pure filesystem helpers (sync). Async wrappers in tool and MCP
modules call them via :func:`asyncio.to_thread` to avoid blocking the event loop.
"""

import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def resolve_context_dir(context_key: str | None) -> Path:
    """Resolve the canonical context directory for a given context key.

    Args:
        context_key: The context key (typically a tree-root instance id).
            If ``None`` or empty, the directory is still resolved (just won't exist
            on disk) so callers can rely on a stable Path object.

    Returns:
        The :class:`pathlib.Path` to the context directory. Never raises.
    """
    try:
        base = Path(tempfile.gettempdir())
    except Exception as e:
        logger.debug("resolve_context_dir: tempfile.gettempdir() failed: %s", e)
        base = Path("/unknown")
    return base / "ensemble" / "context" / str(context_key or "")


_TIMESTAMP_PATTERN = re.compile(r"_\d{8}_\d{6}(?:_[A-Za-z0-9_-]{1,32})?\.md$")


def _build_filename(slug: str, suffix: str, instance_id: str | None = None) -> str:
    """Build a timestamped context filename."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    iid_suffix = f"_{instance_id[:8]}" if instance_id is not None else ""
    return f"{slug}_{timestamp}{iid_suffix}{suffix}"


def write_context_file(
    context_key: str | None,
    content: str,
    slug: str,
    suffix: str = ".md",
    instance_id: str | None = None,
) -> Path:
    """Atomically write content to a timestamped shared-context file."""
    dir_path = resolve_context_dir(context_key)
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / _build_filename(slug, suffix, instance_id)
    tmp_path = Path(f"{file_path}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, file_path)
    return file_path

# Preview extraction tuning knobs.
_PREVIEW_MAX_LINES = 5
_PREVIEW_MAX_CHARS = 300


def _extract_slug_from_filename(filename: str) -> str:
    """Strip the ``_YYYYMMDD_HHMMSS.md`` suffix from a context filename.

    Args:
        filename: The bare filename (no directory part).

    Returns:
        The slug portion of the filename, with the ``.md`` extension removed.
    """
    slug = _TIMESTAMP_PATTERN.sub("", filename)
    if slug.endswith(".md"):
        slug = slug.removesuffix(".md")
    return slug


def _read_context_file_text(file_path: Path) -> str:
    """Read a context file's full body as a single UTF-8 string.

    Single source of truth for both preview extraction and body search.
    Returns an empty string on any read error (caller treats as "no content
    matched" rather than raising).
    """
    try:
        return file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.debug("list_context_files: read failed for %s: %s", file_path, e)
        return ""


def _extract_preview(
    content: str,
    max_lines: int = _PREVIEW_MAX_LINES,
    max_chars: int = _PREVIEW_MAX_CHARS,
) -> str:
    """Build a multi-line preview from already-read file content.

    The preview collects up to ``max_lines`` non-empty lines (preserving order,
    so the title heading — if present — comes first followed by real content
    lines), joins them with ``\\n``, and truncates to ``max_chars`` characters
    with an ellipsis suffix when longer.

    Args:
        content: The full file body (already read by the caller).
        max_lines: Maximum number of non-empty lines to include.
        max_chars: Maximum total character length of the returned preview.

    Returns:
        A multi-line string suitable for ``concise_preview``. Empty when the
        file has no readable content. Never raises.
    """
    lines: list[str] = []
    for raw in content.splitlines():
        if len(lines) >= max_lines:
            break
        stripped = raw.strip()
        if stripped:
            lines.append(stripped)

    if not lines:
        return ""

    preview = "\n".join(lines)
    if len(preview) > max_chars:
        preview = preview[: max_chars - 3] + "..."
    return preview


def list_context_files(context_key: str, query: str = "") -> list[dict[str, Any]]:
    """List ``.md`` files in the shared context directory for ``context_key``.

    Each entry contains:
    - ``filename``: bare filename (no directory).
    - ``slug``: filename with the timestamp suffix and ``.md`` stripped.
    - ``size_bytes``: file size on disk.
    - ``modified_at``: ISO 8601 modification timestamp (UTC).
    - ``concise_preview``: multi-line preview (up to ~300 chars) made of the
      first few non-empty lines, joined with ``\\n``. Useful for showing
      both the title heading and a sentence or two of real content.

    When ``query`` is non-empty, results are filtered to files whose
    ``filename``, ``slug``, ``concise_preview``, or full content contains the
    query (case-insensitive). When ``query`` is empty, all files are returned.

    Returns an empty list if the directory does not exist, cannot be read, or
    the filter matches nothing. Never raises — errors are logged and an empty
    list is returned.
    """
    context_dir = resolve_context_dir(context_key)
    if not context_dir.is_dir():
        return []

    results: list[dict[str, Any]] = []
    try:
        for file_path in sorted(context_dir.iterdir(), key=lambda p: p.name):
            if not file_path.is_file() or file_path.suffix.lower() != ".md":
                continue
            try:
                stat = file_path.stat()
                try:
                    modified_at = datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat()
                except Exception:
                    modified_at = ""
                # Read the file body exactly once; derive both the preview
                # and any later body search from the same string.
                content = _read_context_file_text(file_path)
                preview = _extract_preview(content)

                results.append({
                    "filename": file_path.name,
                    "slug": _extract_slug_from_filename(file_path.name),
                    "size_bytes": int(stat.st_size),
                    "modified_at": modified_at,
                    "concise_preview": preview,
                    "_content": content,
                })
            except Exception as e:
                logger.debug("list_context_files: skipping %s: %s", file_path, e)
                continue
    except OSError as e:
        logger.debug("list_context_files: failed to list %s: %s", context_dir, e)
        return []

    if not query:
        # No filtering: strip the internal-only "_content" key from output.
        for entry in results:
            entry.pop("_content", None)
        return results

    # Filter by case-insensitive substring match against metadata first, then
    # fall back to a full file-body scan for files whose metadata did not match.
    needle = query.lower()
    filtered: list[dict[str, Any]] = []
    for entry in results:
        if (
            needle in entry["filename"].lower()
            or needle in entry["slug"].lower()
            or needle in entry["concise_preview"].lower()
        ):
            filtered.append(entry)
            continue
        # Fallback: search the already-read body in-memory (no extra I/O).
        if needle in entry["_content"].lower():
            filtered.append(entry)
    # Strip the internal-only "_content" key from the final payload.
    for entry in filtered:
        entry.pop("_content", None)
    return filtered


def read_context_file(context_key: str, filename: str) -> str | None:
    """Read a specific context file by name.

    Security: rejects any ``filename`` that contains a path separator (``/``,
    ``\\``), a parent-traversal component (``..``), or that resolves outside
    the canonical context directory. Returns ``None`` on any failure (missing
    file, directory does not exist, read error, security rejection).

    Args:
        context_key: The context key (tree-root instance id).
        filename: Bare filename as returned by :func:`list_context_files`.

    Returns:
        The file contents as a UTF-8 string, or ``None`` on failure.
    """
    if not filename or not isinstance(filename, str):
        return None
    if "/" in filename or "\\" in filename or ".." in filename.split("/"):
        logger.debug("read_context_file: rejected unsafe filename %r", filename)
        return None
    if not filename.lower().endswith(".md"):
        logger.debug("read_context_file: rejected non-md filename %r", filename)
        return None

    context_dir = resolve_context_dir(context_key)
    if not context_dir.is_dir():
        return None

    try:
        target = (context_dir / filename).resolve()
    except Exception as e:
        logger.debug("read_context_file: resolve failed for %s/%s: %s", context_key, filename, e)
        return None

    try:
        canonical_dir = context_dir.resolve()
    except Exception as e:
        logger.debug("read_context_file: resolve dir failed: %s", e)
        return None

    if canonical_dir not in target.parents and target != canonical_dir:
        logger.debug("read_context_file: rejected traversal for %s/%s", context_key, filename)
        return None

    if not target.is_file():
        return None

    return _read_context_file_text(target)
