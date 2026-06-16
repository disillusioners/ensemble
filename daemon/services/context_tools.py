"""Low-level filesystem helpers for the shared context directory.

Provides:

- :func:`resolve_context_dir` — canonical location for ``{tempdir}/ensemble/context/{context_key}``.
- :func:`list_context_files` — enumerate ``.md`` files for a context key.
- :func:`read_context_file` — safely read a single context file (no path traversal).

These are pure filesystem helpers (sync). Async wrappers in tool and MCP
modules call them via :func:`asyncio.to_thread` to avoid blocking the event loop.
"""

import logging
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


_TIMESTAMP_PATTERN = re.compile(r"_\d{8}_\d{6}\.md$")

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


def _extract_preview(file_path: Path, max_lines: int = _PREVIEW_MAX_LINES, max_chars: int = _PREVIEW_MAX_CHARS) -> str:
    """Build a multi-line preview of a context file.

    The preview collects up to ``max_lines`` non-empty lines (preserving order,
    so the title heading — if present — comes first followed by real content
    lines), joins them with ``\\n``, and truncates to ``max_chars`` characters
    with an ellipsis suffix when longer.

    Args:
        file_path: Path to the file to read.
        max_lines: Maximum number of non-empty lines to include.
        max_chars: Maximum total character length of the returned preview.

    Returns:
        A multi-line string suitable for ``concise_preview``. Empty when the
        file has no readable content. Never raises.
    """
    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as fh:
            lines: list[str] = []
            for raw in fh:
                if len(lines) >= max_lines:
                    break
                stripped = raw.strip()
                if stripped:
                    lines.append(stripped)
    except Exception as e:
        logger.debug("list_context_files: failed to read preview of %s: %s", file_path, e)
        return ""

    if not lines:
        return ""

    preview = "\n".join(lines)
    if len(preview) > max_chars:
        preview = preview[: max_chars - 3] + "..."
    return preview


def _file_content_matches(file_path: Path, needle: str) -> bool:
    """Check whether ``needle`` (already lower-cased) occurs in the file body.

    Used as a fallback when the metadata-only filter (filename / slug /
    preview) does not match, so that callers can still find content that
    only appears later in the file.

    Args:
        file_path: Path to the file to read.
        needle: Lower-cased query string.

    Returns:
        ``True`` if ``needle`` appears anywhere in the file. ``False`` on any
        read error (caller should treat unknown matches as "no match" rather
        than raising).
    """
    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as fh:
            for chunk in iter(lambda: fh.read(8192), ""):
                if needle in chunk.lower():
                    return True
    except Exception as e:
        logger.debug("list_context_files: content search failed for %s: %s", file_path, e)
    return False


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
                preview = _extract_preview(file_path)

                results.append({
                    "filename": file_path.name,
                    "slug": _extract_slug_from_filename(file_path.name),
                    "size_bytes": int(stat.st_size),
                    "modified_at": modified_at,
                    "concise_preview": preview,
                })
            except Exception as e:
                logger.debug("list_context_files: skipping %s: %s", file_path, e)
                continue
    except OSError as e:
        logger.debug("list_context_files: failed to list %s: %s", context_dir, e)
        return []

    if not query:
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
        # Fallback: search the file body in chunks (handles large files).
        file_path = context_dir / entry["filename"]
        if _file_content_matches(file_path, needle):
            filtered.append(entry)
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

    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.debug("read_context_file: read failed for %s/%s: %s", context_key, filename, e)
        return None
