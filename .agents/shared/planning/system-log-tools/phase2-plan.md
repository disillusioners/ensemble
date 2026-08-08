# Phase 2: System Log Tool Module

## Objective

Create `daemon/tools/system_log_tools.py` — the closure factory `create_system_log_tools(manager, current_instance_id) -> list` producing four read-only tools: `ens_system_log_list`, `ens_system_log_read`, `ens_system_log_search`, `ens_system_log_tail`. This module follows the exact pattern of `chart_tools.py` (module docstring → imports → constants → factory → return list). Security enforcement (path traversal, size caps, redaction) lives inside the tool implementations.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Create `daemon/tools/system_log_tools.py` with module docstring, imports, `CATEGORY_NAME` / `CATEGORY_DOC` constants | none | File exists, imports resolve, module is importable without error |
| 2 | Implement `_resolve_log_dir()` helper — reads `DAEMON_LOG_DIR` env var (default `./data/logs`), resolves to absolute `Path` | Task 1 | Returns `Path` object; handles missing env var gracefully |
| 3 | Implement `_validate_filename(filename)` security helper — rejects absolute paths, `..` sequences, symlinks escaping the log dir; returns safe `Path` or raises `ValueError` | Task 2 | Passes all security test cases (path traversal blocked, valid filenames accepted) |
| 4 | Implement `_redact_line(line)` helper — scans for `*_API_KEY=`, `password=`, `token=`, `Bearer ...`, `*_SECRET=` patterns; replaces values with `[REDACTED]` | Task 1 | Mirrors `daemon/tools/system.py:108-129` patterns; tested with sample sensitive lines |
| 5 | Implement `ens_system_log_list` tool — lists `.log` files in log dir with sizes and last-modified timestamps | Tasks 2, 3 | Returns tabular listing; gracefully handles missing dir |
| 6 | Implement `ens_system_log_read` tool — iterator-based paged read with offset/limit, line numbers, file selection (current vs rotated), size cap (500 lines / 12 KB), line truncation (2000 chars), redaction | Tasks 2, 3, 4 | Returns numbered lines; respects offset/limit; caps at 500 lines AND 12 KB; per-line cap at 2000 chars; blocks path traversal; redacts secrets |
| 7 | Implement `ens_system_log_search` tool — streaming regex search with context_before/context_after lines, optional level filter, line scan limit (50K), per-line cap (2000 chars BEFORE regex), redaction, byte cap (12 KB) | Tasks 2, 3, 4 | Returns matching lines with context; supports regex; supports level filter; respects caps; redacts secrets; streams line-by-line (no `read_text()`) |
| 8 | Implement `ens_system_log_tail` tool — seek-from-end read of last 64KB chunk, last N lines (default 100, max 200), optional level filter, redaction, byte cap (12 KB) | Tasks 2, 3, 4 | Returns last N lines with line numbers; supports level filter; caps at 200 lines; redacts secrets; uses `os.SEEK_END` (no `read_text()`) |
| 9 | Wire factory to return all four tools | Task 5–8 | `create_system_log_tools(MagicMock(), "test")` returns a list of exactly 4 tools; each has `_tool_category == "system-log"` |

## Detailed File Changes

### New file: `daemon/tools/system_log_tools.py`

**Structure (following chart_tools.py pattern):**

```python
"""System log tools for reading and searching daemon logs.

Mirrors the closure-injection pattern of ``daemon.tools.chart_tools``:
``create_system_log_tools(manager, current_instance_id)`` is invoked from
``create_instance_tools`` to assemble the per-instance tool list. The four
tools (list, read, search, tail) provide read-only access to the daemon's own
log files under ``data/logs/``, enabling self-healing — agents can
investigate runtime bugs by inspecting log output.

All tools enforce:
- Path traversal protection (reads restricted to the log directory)
- Size caps (max 500 lines per read/search response, max 200 per tail, 12 KB total)
- Per-line length cap (2000 chars — lines truncated with "...(truncated)")
- Log content redaction (mirrors daemon/tools/system.py:108-129 patterns)
- Graceful handling of missing/empty log files
- Streaming I/O (no full-file read_text() — memory-safe for multi-MB logs)
"""

import logging
import os
import re
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "System Log"
CATEGORY_DOC = """\
System log tools for reading and searching the ensemble daemon's own logs.

Provides read-only access to log files under ``data/logs/`` (configurable
via ``DAEMON_LOG_DIR``). Four tools: ``ens_system_log_list`` (list available
log files with sizes and timestamps), ``ens_system_log_read`` (paged read),
``ens_system_log_search`` (regex grep with context), ``ens_system_log_tail``
(recent lines). All tools enforce path traversal protection, size caps,
per-line length cap (2000 chars), and log content redaction (API keys,
tokens, passwords, Bearer tokens replaced with [REDACTED]).
"""

# Maximum lines returned by ens_system_log_read and ens_system_log_search.
MAX_LINES_READ = 500
# Maximum lines returned by ens_system_log_tail.
MAX_LINES_TAIL = 200
# Maximum total bytes per response (safety valve against token explosion).
MAX_BYTES_RESPONSE = 12 * 1024  # 12 KB
# Maximum lines scanned by ens_system_log_search to prevent DoS.
MAX_LINES_SCAN = 50_000
# Per-line length cap — lines longer than this are truncated BEFORE regex
# and redaction to prevent pathological input from blowing the response.
MAX_LINE_LENGTH = 2000
# Chunk size read from end of file in ens_system_log_tail.
TAIL_SEEK_CHUNK = 64 * 1024  # 64 KB

# Redaction patterns mirroring daemon/tools/system.py:108-129
# (_SECRET_KEY_SUBSTRINGS, _SECRET_SUFFIXES). Each pattern captures the
# key/value separator in group 1, then the value is replaced with [REDACTED].
_SECRET_PATTERNS = [
    re.compile(r'(\w*_API_KEY\s*[=:]\s*)\S+', re.IGNORECASE),
    re.compile(r'(\w*_TOKEN\s*[=:]\s*)\S+', re.IGNORECASE),
    re.compile(r'(\w*_PASSWORD\s*[=:]\s*)\S+', re.IGNORECASE),
    re.compile(r'(\w*_SECRET\s*[=:]\s*)\S+', re.IGNORECASE),
    re.compile(r'(password\s*[=:]\s*)\S+', re.IGNORECASE),
    re.compile(r'(token\s*[=:]\s*)\S+', re.IGNORECASE),
    re.compile(r'(Bearer\s+)\S+', re.IGNORECASE),
    re.compile(r'(Authorization\s*:\s*)\S+', re.IGNORECASE),
]


def _redact_line(line: str) -> str:
    """Redact sensitive content from a log line.

    Mirrors the masking patterns in daemon/tools/system.py:108-129
    (_SECRET_KEY_SUBSTRINGS, _SECRET_SUFFIXES). Replaces matched values
    with [REDACTED] before returning to the agent.

    Args:
        line: The raw log line.

    Returns:
        The line with sensitive values masked.
    """
    redacted = line
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(r'\1[REDACTED]', redacted)
    return redacted


def _truncate_line(line: str) -> str:
    """Truncate a line to MAX_LINE_LENGTH with a truncation suffix.

    Truncation happens BEFORE regex matching and redaction to prevent
    pathological inputs from causing backtracking or response explosion.
    """
    if len(line) > MAX_LINE_LENGTH:
        return line[:MAX_LINE_LENGTH] + "...(truncated)"
    return line


def _format_size(num_bytes: int) -> str:
    """Format a byte count as a human-readable size string."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    else:
        return f"{num_bytes / (1024 * 1024):.1f} MB"


def _resolve_log_dir() -> Path:
    """Resolve the log directory from environment, returning an absolute Path.

    Reads ``DAEMON_LOG_DIR`` (default ``./data/logs``). Returns a resolved
    absolute path.
    """
    log_dir = os.environ.get("DAEMON_LOG_DIR", "./data/logs")
    return Path(log_dir).resolve()


def _validate_filename(filename: str) -> Path:
    """Validate a filename and return a safe, resolved Path inside the log dir.

    Security checks:
    - Reject absolute paths (e.g., ``/etc/passwd``)
    - Reject path separators and ``..`` traversal sequences
    - Resolve against the log directory and verify containment

    Args:
        filename: The requested filename (e.g., ``"ensemble.log"``,
            ``"ensemble.log.1"``).

    Returns:
        Resolved ``Path`` object guaranteed to be inside the log directory.

    Raises:
        ValueError: If the filename fails validation.
    """
    if not filename:
        raise ValueError("Filename is required.")

    # Reject absolute paths
    p = Path(filename)
    if p.is_absolute():
        raise ValueError(f"Absolute paths are not allowed: {filename}")

    # Reject path separators and traversal sequences
    if "/" in filename or "\\" in filename:
        raise ValueError(f"Path separators are not allowed in filename: {filename}")
    if ".." in filename:
        raise ValueError(f"Directory traversal is not allowed: {filename}")

    # Resolve against log directory and verify containment
    log_dir = _resolve_log_dir()
    resolved = (log_dir / filename).resolve()

    # Containment check: resolved path must be inside log_dir
    try:
        resolved.relative_to(log_dir)
    except ValueError:
        raise ValueError(f"Filename resolves outside the log directory: {filename}")

    return resolved


def create_system_log_tools(manager: "InstanceManager", current_instance_id: str) -> list:
    """Create system log tools with injected manager reference.

    Args:
        manager: The InstanceManager instance (unused by these tools but
            accepted for pattern parity with other factories).
        current_instance_id: The ID of the current instance (unused by
            these tools but accepted for pattern parity).

    Returns:
        List of tool functions:
        [ens_system_log_list, ens_system_log_read, ens_system_log_search, ens_system_log_tail]
    """

    @register_tool_category("system-log")
    @tool
    def ens_system_log_list() -> str:
        """List all available daemon log files with metadata.

        Returns a tabular listing of all ``.log`` files in the log directory
        (configured via ``DAEMON_LOG_DIR``) with each file's size and
        last-modified timestamp. Useful for discovering rotated backups
        before reading them with ``ens_system_log_read``.

        Returns:
            Tabular listing with File, Size, Last Modified columns, or an
            error message if the log directory is missing or empty.
        """
        log_dir = _resolve_log_dir()
        if not log_dir.exists():
            return f"Log directory not found: {log_dir}"
        if not log_dir.is_dir():
            return f"Log path is not a directory: {log_dir}"

        try:
            log_files = sorted(log_dir.glob("ensemble.log*"))
        except OSError as e:
            return f"Error listing log directory: {e}"

        if not log_files:
            return f"No log files found in {log_dir}"

        lines = [
            f"{'File':<30} {'Size':<12} Last Modified",
            f"{'-' * 30} {'-' * 12} {'-' * 19}",
        ]
        total_bytes = 0
        for log_file in log_files:
            try:
                stat = log_file.stat()
                size_str = _format_size(stat.st_size)
                mtime_str = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                row = f"{log_file.name:<30} {size_str:<12} {mtime_str}"
            except OSError as e:
                row = f"{log_file.name:<30} {'?':<12} (error: {e})"

            # Apply per-line + byte cap (defensive — should never trip here)
            row = _truncate_line(_redact_line(row))
            if total_bytes + len(row) > MAX_BYTES_RESPONSE:
                lines.append(f"... (truncated at {MAX_BYTES_RESPONSE // 1024} KB limit)")
                break
            lines.append(row)
            total_bytes += len(row) + 1

        header = f"Log directory: {log_dir} | {len(log_files)} file(s)\n"
        return header + "\n".join(lines)

    @register_tool_category("system-log")
    @tool
    def ens_system_log_read(
        filename: str = "ensemble.log",
        offset: int = 0,
        limit: int = 100,
    ) -> str:
        """Read lines from a daemon log file with paging support.

        Returns lines with line numbers (1-indexed). Use offset/limit to
        page through large log files.

        Args:
            filename: Log file name (e.g., "ensemble.log" for current,
                "ensemble.log.1" for the most recent rotated backup).
                Defaults to "ensemble.log". Must not contain path separators
                or ".." sequences.
            offset: Starting line number (0-indexed). Defaults to 0 (first line).
            limit: Maximum number of lines to return. Capped at 500.
                Defaults to 100.

        Returns:
            Numbered log lines (e.g., "  42: 2026-08-08 08:41:48 - daemon.api - INFO - ..."),
            or an error message if the file is missing/empty or filename is invalid.
        """
        limit = min(limit, MAX_LINES_READ)
        try:
            filepath = _validate_filename(filename)
        except ValueError as e:
            return f"Error: {e}"

        if not filepath.exists():
            return f"Log file not found: {filename}"
        if not filepath.is_file():
            return f"Not a file: {filename}"

        # Stream line-by-line (no read_text — memory-safe for multi-MB logs).
        result_lines = []
        total_bytes = 0
        end_line = offset + limit

        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                for i, raw_line in enumerate(f):
                    if i < offset:
                        continue  # skip to offset
                    if len(result_lines) >= limit:
                        break
                    line = raw_line.rstrip("\n")
                    line = _truncate_line(line)
                    line = _redact_line(line)
                    numbered = f"{i + 1:>6}: {line}"
                    if total_bytes + len(numbered) > MAX_BYTES_RESPONSE:
                        result_lines.append(f"... (truncated at {MAX_BYTES_RESPONSE // 1024} KB limit)")
                        break
                    result_lines.append(numbered)
                    total_bytes += len(numbered) + 1
        except OSError as e:
            return f"Error reading file: {e}"

        if not result_lines:
            return f"Log file is empty: {filename}"

        # Reconstruct the header range based on how many lines we actually
        # returned (could be less than limit if EOF hit first).
        first_line_no = offset + 1
        last_line_no = offset + len(result_lines)
        header = f"File: {filename} | Lines {first_line_no}-{last_line_no} (offset={offset}, limit={limit})\n"
        return header + "\n".join(result_lines)

    @register_tool_category("system-log")
    @tool
    def ens_system_log_search(
        pattern: str,
        filename: str = "ensemble.log",
        context_before: int = 0,
        context_after: int = 0,
        level: str | None = None,
        limit: int = 50,
    ) -> str:
        """Search daemon log lines matching a regex pattern.

        Returns matching lines with optional context (lines before/after
        each match). Optionally filter by log level (e.g., "ERROR",
        "WARNING", "INFO").

        Args:
            pattern: Regular expression to search for (Python re syntax).
            filename: Log file to search. Defaults to "ensemble.log".
            context_before: Number of lines to show before each match.
                Defaults to 0.
            context_after: Number of lines to show after each match.
                Defaults to 0.
            level: Optional log level filter (e.g., "ERROR", "WARNING",
                "INFO", "DEBUG"). Only matching lines are returned. Case-
                insensitive. Defaults to None (no level filter).
            limit: Maximum number of matches to return. Capped at 50.
                Defaults to 50.

        Returns:
            Matching lines with line numbers and context, grouped by match,
            or an error message if the pattern is invalid or file is missing.
        """
        limit = min(limit, 50)
        try:
            filepath = _validate_filename(filename)
        except ValueError as e:
            return f"Error: {e}"

        if not filepath.exists():
            return f"Log file not found: {filename}"

        # Compile regex
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Error: Invalid regex pattern: {e}"

        level_upper = level.upper() if level else None

        # Streaming search — never read the whole file. Buffer context_before
        # lines via deque; read ahead for context_after lines.
        results = []
        match_count = 0
        total_bytes = 0
        scanned = 0
        context_buffer = deque(maxlen=context_before) if context_before else deque()
        after_remaining = 0  # how many post-context lines we still owe

        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                for i, raw_line in enumerate(f):
                    if scanned >= MAX_LINES_SCAN:
                        break
                    scanned += 1
                    line = raw_line.rstrip("\n")
                    # Truncate BEFORE regex (W4 — caps line length to prevent
                    # catastrophic backtracking on pathological input).
                    line = _truncate_line(line)
                    # Redact AFTER truncation (so [REDACTED] doesn't get
                    # truncated in the middle of the placeholder).
                    line = _redact_line(line)

                    # Level filter (substring match on standard format)
                    passes_level = (
                        level_upper is None
                        or f" - {level_upper} - " in line
                    )

                    is_match = passes_level and bool(regex.search(line))

                    if is_match:
                        match_count += 1
                        if match_count > limit:
                            break
                        # Build block: context_before (buffer) + match line + context_after (forward)
                        block = []
                        for ctx_no, ctx_line in context_buffer:
                            block.append(f"{ctx_no + 1:>6}     {ctx_line}")
                        block.append(f"{i + 1:>6} >>> {line}")
                        after_remaining = context_after
                    elif after_remaining > 0:
                        block.append(f"{i + 1:>6}     {line}")
                        after_remaining -= 1
                    else:
                        # Not a match, not in context — just update buffer
                        if context_before:
                            context_buffer.append((i, line))
                        continue

                    # Emit block (built for either match or post-context lines)
                    if is_match or after_remaining == 0:
                        # Finalize the previous block when starting a new match
                        # or finishing the after-context window.
                        if is_match and results:
                            # Append separator before this new block
                            sep = "---"
                            if total_bytes + len(sep) > MAX_BYTES_RESPONSE:
                                results.append(f"... (truncated at {MAX_BYTES_RESPONSE // 1024} KB limit)")
                                break
                            results.append(sep)
                            total_bytes += len(sep) + 1
                        block_text = "\n".join(block)
                        if total_bytes + len(block_text) > MAX_BYTES_RESPONSE:
                            results.append(f"... (truncated at {MAX_BYTES_RESPONSE // 1024} KB limit)")
                            break
                        results.append(block_text)
                        total_bytes += len(block_text) + 1
                        block = []  # reset for next iteration
                    else:
                        # Still accumulating after-context for current match
                        # — fall through, don't emit yet.
                        pass

                    # Update rolling context buffer for next iteration
                    if context_before:
                        context_buffer.append((i, line))
        except OSError as e:
            return f"Error reading file: {e}"

        if not results:
            return (
                f"No matches found for pattern '{pattern}'"
                + (f" at level {level_upper}" if level_upper else "")
                + f" in {filename} (scanned {scanned} lines)"
            )

        header = (
            f"File: {filename} | Pattern: {pattern}"
            + (f" | Level: {level_upper}" if level_upper else "")
            + f" | {match_count} match(es) (showing {min(match_count, limit)}) | scanned {scanned} lines\n"
        )
        return header + "\n".join(results)

    @register_tool_category("system-log")
    @tool
    def ens_system_log_tail(
        filename: str = "ensemble.log",
        lines: int = 100,
        level: str | None = None,
    ) -> str:
        """Read the last N lines of a daemon log file (tail equivalent).

        Returns recent log lines with line numbers. Optionally filter by
        log level (e.g., "ERROR", "WARNING") to focus on recent errors.

        Args:
            filename: Log file name. Defaults to "ensemble.log".
            lines: Number of lines to return from the end of the file.
                Capped at 200. Defaults to 100.
            level: Optional log level filter (e.g., "ERROR", "WARNING",
                "INFO"). Only matching lines are returned. Case-insensitive.
                Defaults to None (no level filter).

        Returns:
            Last N matching lines with line numbers, or an error message
            if the file is missing/empty.
        """
        lines = min(lines, MAX_LINES_TAIL)
        try:
            filepath = _validate_filename(filename)
        except ValueError as e:
            return f"Error: {e}"

        if not filepath.exists():
            return f"Log file not found: {filename}"
        if not filepath.is_file():
            return f"Not a file: {filename}"

        # Read the last TAIL_SEEK_CHUNK bytes from end of file. Avoids
        # loading multi-MB files into memory just to tail them.
        try:
            file_size = os.path.getsize(filepath)
            if file_size == 0:
                return f"Log file is empty: {filename}"
            read_size = min(TAIL_SEEK_CHUNK, file_size)
            with open(filepath, "rb") as f:
                if read_size < file_size:
                    f.seek(-read_size, os.SEEK_END)
                    chunk = f.read()
                else:
                    chunk = f.read()
        except OSError as e:
            return f"Error reading file: {e}"

        decoded = chunk.decode("utf-8", errors="replace")
        all_lines = decoded.splitlines()

        # If we seeked mid-file, the first line is likely partial — drop it.
        if read_size < file_size and all_lines:
            all_lines = all_lines[1:]

        if not all_lines:
            return f"Log file is empty: {filename}"

        # Apply level filter
        level_upper = level.upper() if level else None
        if level_upper:
            filtered = [
                (i + 1, line)
                for i, line in enumerate(all_lines)
                if f" - {level_upper} - " in line
            ]
        else:
            filtered = [(i + 1, line) for i, line in enumerate(all_lines)]

        if not filtered:
            return f"No lines at level {level_upper} in {filename}"

        total = len(filtered)
        sliced = filtered[-lines:]

        # Build numbered output with redaction, line truncation, byte cap
        result_lines = []
        total_bytes = 0
        for line_no, line_text in sliced:
            line_text = _truncate_line(line_text)
            line_text = _redact_line(line_text)
            numbered = f"{line_no:>6}: {line_text}"
            if total_bytes + len(numbered) > MAX_BYTES_RESPONSE:
                result_lines.append(f"... (truncated at {MAX_BYTES_RESPONSE // 1024} KB limit)")
                break
            result_lines.append(numbered)
            total_bytes += len(numbered) + 1

        header = f"File: {filename} | Last {len(result_lines)} of {total} lines"
        if level_upper:
            header += f" at level {level_upper}"
        header += "\n"
        return header + "\n".join(result_lines)

    return [ens_system_log_list, ens_system_log_read, ens_system_log_search, ens_system_log_tail]
```

**Key design decisions:**

1. **`_resolve_log_dir()` reads env directly** — Same pattern as Phase 1: reads `DAEMON_LOG_DIR` from `os.environ` rather than importing `DaemonConfig` to avoid circular import risk. Tools are called at runtime (not import time), so a config import would be safe here, but env-read keeps consistency with `api.py`.

2. **`_validate_filename()` containment check** — Three layers of defense: (a) reject absolute paths, (b) reject `/`, `\`, `..` in the filename string, (c) resolve against log dir and verify `relative_to()` containment. This is defense-in-depth — even if (a) or (b) are bypassed, (c) catches it.

3. **Level filter uses substring match** — The daemon log format is `... - LEVEL - ...` (e.g., `2026-08-08 - daemon.api - INFO - message`). The level filter checks for ` - ERROR - ` substring. This is fast (no regex needed) and matches the standard format. If the format changes, this needs updating — documented in the tool docstring.

4. **Byte cap as safety valve** — Even with line caps (500/200), a single pathological log line could be huge. `MAX_BYTES_RESPONSE = 12 KB` truncates output regardless of line count. This prevents token explosion in the agent's context window.

5. **`manager` and `current_instance_id` accepted but unused** — Pattern parity with other factories. System log tools don't need per-instance context (logs are global to the daemon). But the factory signature must match `create_instance_tools`'s calling convention.

6. **Read-only** — No tool modifies, deletes, or writes to log files. All tools open files in read-only mode (`"r"` or `"rb"`).

7. **Synchronous `def`, not `async def` (C1)** — All four tools use `def` (not `async def`). This matches the `filesystem.read_file` precedent and avoids blocking the event loop. The tools do synchronous file I/O which is fine for a sync `def` — LangChain tool calls already run the function directly when not async. Async wrapping would add no benefit since the underlying I/O is sync anyway.

8. **Streaming I/O (C2)** — None of the tools call `read_text()` on the whole file. `ens_system_log_read` and `ens_system_log_search` iterate `for line in f` (Python's file iterator is lazy, memory-safe). `ens_system_log_tail` seeks from end and reads only the last 64KB chunk. `ens_system_log_list` only calls `stat()` on filenames (no file content read). This ensures multi-MB logs don't blow the agent's memory.

9. **Mandatory redaction (C3)** — Every line returned by `read`, `search`, and `tail` passes through `_redact_line()` BEFORE being added to the response. The patterns mirror `daemon/tools/system.py:108-129` (`_SECRET_KEY_SUBSTRINGS`, `_SECRET_SUFFIXES`): `*_API_KEY=`, `*_TOKEN=`, `*_PASSWORD=`, `*_SECRET=`, `password=`, `token=`, `Bearer ...`, `Authorization: ...` are all replaced with `[REDACTED]`. The `list` tool's output is also passed through `_redact_line` defensively (filenames shouldn't contain secrets, but cheap to be safe).

10. **Per-line truncation BEFORE regex (W4, W3)** — `_truncate_line()` caps each line at `MAX_LINE_LENGTH = 2000` chars BEFORE any regex matching or redaction. This prevents two failure modes: (a) catastrophic backtracking on a 1MB single line, and (b) one pathological line blowing the entire response. Truncated lines get a `...(truncated)` suffix.

11. **TOCTOU accepted risk (C5)** — There is a residual symlink-swap TOCTOU race between `_validate_filename()` resolving the path and `open()` reading it. An attacker who creates a symlink inside the log dir pointing outside in that window could read arbitrary files. This is explicitly accepted: the daemon is single-process, the log directory is dedicated (`data/logs/`), and the attack requires write access to the log dir at the exact moment. Probability is negligible. No symlink following is done — `Path.resolve()` resolves but does not follow symlinks for the filename component (only for parent dirs).

## Coupling

- **Tight with:** Phase 3 — the registry entry and `DYNAMIC_TOOL_NAMES` reference the module path and tool names defined here
- **Tight with:** Phase 5 — tests import `create_system_log_tools` and test each tool's behavior
- **Loose with:** Phase 1 — tools read files from the log dir, but don't care how they got there
- **Independent of:** Phase 4

## Risks

- **R2 (token explosion):** Hard caps on lines (500/200) + per-line cap (2000 chars) + byte cap (12 KB). Mitigated in implementation and tested in Phase 5.
- **R3 (path traversal):** Three-layer validation in `_validate_filename`. Tested in Phase 5 security test class.
- **R6 (regex DoS):** Regex compiled with try/except on `re.error`. Line scan capped at `MAX_LINES_SCAN = 50_000`. Per-line cap at 2000 chars BEFORE regex prevents pathological input. Documented in docstring.
- **C3 (log content disclosure):** `_redact_line()` applied to all lines in all tools. Patterns mirror `daemon/tools/system.py:108-129`. Tested in Phase 5 with sample sensitive lines (e.g., `OPENAI_API_KEY=sk-xxxx`).
- **C2 (memory exhaustion):** Streaming I/O — no `read_text()` on whole file. `read` and `search` use lazy line iteration; `tail` seeks from end (64KB cap); `list` only calls `stat()`.

## Exit Criterion

- `daemon/tools/system_log_tools.py` exists and is importable
- `create_system_log_tools(MagicMock(), "test")` returns a list of exactly 4 tools
- Each tool has `_tool_category == "system-log"`
- All four tools return informative error strings for missing files, invalid filenames, and empty logs
- Path traversal is blocked (all `../`, absolute path, and separator injection attempts rejected)
- `_redact_line()` correctly masks `OPENAI_API_KEY=sk-xxxx` → `OPENAI_API_KEY=[REDACTED]`
- `ens_system_log_list` returns tabular listing of `.log` files with sizes + timestamps
- `ens_system_log_tail` uses `os.SEEK_END` (no full-file read)
- `ens_system_log_read` and `ens_system_log_search` stream line-by-line (verified via `git diff` showing no `read_text()` calls in the tool body)
