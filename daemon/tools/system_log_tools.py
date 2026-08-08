"""System log tools for reading and searching daemon logs.

Mirrors the closure-injection pattern of ``daemon.tools.chart_tools``:
``create_system_log_tools(manager, current_instance_id)`` is invoked from
``create_instance_tools`` to assemble the per-instance tool list. The four
tools (list, read, search, tail) provide read-only access to the daemon's own
log files under ``data/logs/``, enabling self-healing — agents can
investigate runtime bugs by inspecting log output.

All tools enforce:
- Path traversal protection (reads restricted to the log directory)
- Size caps (max 500 lines per read response, max 50 per search, max 200 per tail, 12 KB total)
- Per-line length cap (2000 chars — lines truncated with "...(truncated)")
- Log content redaction (mirrors daemon/tools/system.py:108-129 patterns)
- Graceful handling of missing/empty log files
- Streaming I/O (no full-file read_text() — memory-safe for multi-MB logs)
"""

import logging
import os
import re
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

# Maximum context lines per search result.
MAX_CONTEXT = 100
# Maximum lines returned by ens_system_log_read.
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
#
# Patterns are ordered: broader/more-specific patterns first so that
# overlapping narrower patterns are not applied to already-redacted text.
_SECRET_PATTERNS = [
    # 1. JSON quoted-key form: "api_key": "secret", "token": "...", etc.
    re.compile(r'"(?:api_key|token|password|secret)"\s*:\s*"([^"]*)"', re.IGNORECASE),
    # 2. Hyphenated HTTP headers: X-API-Key: secret, X-API-Token: ..., etc.
    re.compile(r'(X-[A-Z][A-Z0-9]*(?:-(?:KEY|TOKEN|SECRET|PASSWORD))\s*:\s*)\S+', re.IGNORECASE),
    # 3. Authorization: <scheme> <value>  — captures and redacts the entire
    #    header value (scheme + credential), not just the scheme word.
    re.compile(r'(Authorization\s*:\s*)\S+\s+\S+', re.IGNORECASE),
    # 4. Standalone Authorization: <single-word> (no second token — e.g. Basic alone)
    re.compile(r'(Authorization\s*:\s*)\S+', re.IGNORECASE),
    # 5. Multi-word env-var value: PASSWORD=my secret → "my secret" redacted
    re.compile(r'(\w*_PASSWORD\s*[=:]\s*)\S+(?:\s+\S+)*', re.IGNORECASE),
    # 6. Multi-word env-var value: SECRET=my secret → "my secret" redacted
    re.compile(r'(\w*_SECRET\s*[=:]\s*)\S+(?:\s+\S+)*', re.IGNORECASE),
    # 7. Multi-word env-var value: TOKEN=my secret → "my secret" redacted
    re.compile(r'(\w*_TOKEN\s*[=:]\s*)\S+(?:\s+\S+)*', re.IGNORECASE),
    # 8. Multi-word env-var value: API_KEY=my secret → "my secret" redacted
    re.compile(r'(\w*_API_KEY\s*[=:]\s*)\S+(?:\s+\S+)*', re.IGNORECASE),
    # 9. Multi-word bare-key value: password=my secret → "my secret" redacted
    re.compile(r'(password\s*[=:]\s*)\S+(?:\s+\S+)*', re.IGNORECASE),
    # 10. Simple single-word forms (fallback — handles bare key without multi-word)
    re.compile(r'(\w*_API_KEY\s*[=:]\s*)\S+', re.IGNORECASE),
    re.compile(r'(\w*_TOKEN\s*[=:]\s*)\S+', re.IGNORECASE),
    re.compile(r'(\w*_PASSWORD\s*[=:]\s*)\S+', re.IGNORECASE),
    re.compile(r'(\w*_SECRET\s*[=:]\s*)\S+', re.IGNORECASE),
    # 11. Bare password/token/Bearer — covers password=xyz and token=xyz
    re.compile(r'(password\s*[=:]\s*)\S+', re.IGNORECASE),
    re.compile(r'(token\s*[=:]\s*)\S+', re.IGNORECASE),
    re.compile(r'(Bearer\s+)\S+', re.IGNORECASE),
]


def _redact_line(line: str) -> str:
    """Redact sensitive content from a log line.

    Mirrors the masking patterns in daemon/tools/system.py:108-129
    (_SECRET_KEY_SUBSTRINGS, _SECRET_SUFFIXES). Replaces matched values
    with [REDACTED] before returning to the agent.

    Patterns covered (defense-in-depth, format-aware):
      - JSON quoted-key form (``"api_key": "secret"``)
      - Hyphenated HTTP headers (``X-API-Key: secret``)
      - ``Authorization: <scheme> <value>`` (full credential, not just scheme)
      - Multi-word env-var values (``password=my secret`` → both words redacted)
      - Single-word forms (legacy ``_API_KEY=...`` etc.)

    Args:
        line: The raw log line.

    Returns:
        The line with sensitive values masked.
    """
    # JSON pattern: value is in group 1; replace the whole `"key":"value"`.
    line = _SECRET_PATTERNS[0].sub('"[REDACTED]"', line)
    redacted = line
    # Remaining patterns: group 1 is the key/separator, replace with `<group1>[REDACTED]`.
    for pattern in _SECRET_PATTERNS[1:]:
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


def _open_log_file(filepath: Path, log_dir: Path, mode: str = "r"):
    """Open a log file anchored to the log directory's file descriptor.

    Defense against TOCTOU/symlink-swap: instead of ``open(filepath)``
    (which trusts the path argument after static validation has already
    run — a race window exists between validation and open()), open
    the directory first with ``O_DIRECTORY``, then open the target file
    relative to that directory using ``O_NOFOLLOW`` (refuse symlinks).

    The dir_fd is closed before returning; the caller's ``with open()``
    block operates on the file descriptor only.

    Args:
        filepath: Resolved absolute path to the log file (output of
            ``_validate_filename``).
        log_dir: Resolved absolute path to the log directory.
        mode: Open mode — ``"r"`` (text, default) or ``"rb"`` (binary).

    Returns:
        An open file object (use as ``with ... as f:``).

    Raises:
        FileNotFoundError: If the file does not exist.
        IsADirectoryError: If ``filepath`` is a directory.
        OSError: For other I/O errors (permission denied, symlink detected, etc.).
    """
    dir_fd = os.open(str(log_dir), os.O_RDONLY | os.O_DIRECTORY)
    try:
        # ``O_NOFOLLOW`` rejects symlinks (raises ELOOP) so a swap cannot
        # redirect the read outside the directory after validation.
        # Anchor to dir_fd so the path is resolved RELATIVE to the
        # directory even if a concurrent rename/move happens.
        filename = os.path.basename(str(filepath))
        if mode == "rb":
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
        else:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
        # Wrap the raw fd in a Python file object. The dir_fd is no
        # longer needed once we hold the file fd.
        if mode == "rb":
            f = os.fdopen(fd, "rb")
        else:
            f = os.fdopen(fd, "r", encoding="utf-8", errors="replace")
        return f
    finally:
        os.close(dir_fd)


def create_system_log_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    agent_id: str = "",
) -> list:
    """Create system log tools with injected manager reference.

    Args:
        manager: The InstanceManager instance (unused by these tools but
            accepted for pattern parity with other factories).
        current_instance_id: The ID of the current instance (unused by
            these tools but accepted for pattern parity).
        agent_id: Optional agent identifier (unused by these tools but
            accepted for pattern parity with knowledge_tools factory).

    Returns:
        List of tool functions:
        [ens_system_log_list, ens_system_log_read, ens_system_log_search, ens_system_log_tail]
    """

    @register_tool_category("system-log")
    @tool
    def ens_system_log_list() -> str:
        """List all available daemon log files with metadata.

        Returns a tabular listing of all ``ensemble.log*`` files in the log
        directory (configured via ``DAEMON_LOG_DIR``) with each file's size
        and last-modified timestamp. Useful for discovering rotated backups
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
        limit: int = 200,
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
                Defaults to 200.

        Returns:
            Numbered log lines (e.g., "    42: 2026-08-08 08:41:48 - daemon.api - INFO - ..."),
            or an error message if the file is missing/empty or filename is invalid.
        """
        limit = min(limit, MAX_LINES_READ)
        try:
            filepath = _validate_filename(filename)
        except ValueError as e:
            return f"Error: {e}"

        log_dir = _resolve_log_dir()

        # Stream line-by-line (no read_text — memory-safe for multi-MB logs).
        result_lines = []
        total_bytes = 0

        try:
            # Open via dir_fd + O_NOFOLLOW — eliminates the TOCTOU window
            # between validation and open() (symlink swap, rotation race).
            # Missing files / directories surface as FileNotFoundError /
            # IsADirectoryError which we map to friendly messages.
            try:
                f = _open_log_file(filepath, log_dir)
            except (FileNotFoundError, IsADirectoryError):
                # Distinguish "missing" vs "not a file" for caller clarity.
                # Re-resolve inside the try block so we still answer correctly
                # for the legacy not-a-file path (e.g. a directory named
                # ensemble.log inside log_dir).
                if not filepath.exists():
                    return f"Log file not found: {filename}"
                if not filepath.is_file():
                    return f"Not a file: {filename}"
                # Race: file vanished between open and check → treat as missing.
                return f"Log file not found: {filename}"
            with f:
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
        level: str | None = None,
        context=0,  # type-annotation-free: accept int OR non-int (validated below)
        limit: int = 50,
    ) -> str:
        """Search daemon log lines matching a regex pattern.

        Returns matching lines with optional context (lines before and
        after each match, equal count controlled by ``context``).
        Optionally filter by log level (e.g., "ERROR", "WARNING", "INFO").

        Args:
            pattern: Regular expression to search for (Python re syntax).
            filename: Log file to search. Defaults to "ensemble.log".
            level: Optional log level filter (e.g., "ERROR", "WARNING",
                "INFO", "DEBUG"). Only matching lines are returned. Case-
                insensitive. Defaults to None (no level filter).
            context: Number of context lines to show both before and after
                each match. Defaults to 0.
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

        # Compile regex
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Error: Invalid regex pattern: {e}"

        level_upper = level.upper() if level else None
        # Normalize context to non-negative int and clamp to MAX_CONTEXT.
        # Non-integer values (e.g. strings like "abc") return a friendly
        # error instead of raising — matches the rest of the file's style.
        try:
            ctx = int(context)
        except (ValueError, TypeError):
            return (
                f"Error: Invalid context value {context!r}: "
                "must be an integer between 0 and {MAX_CONTEXT}".format(MAX_CONTEXT=MAX_CONTEXT)
            )
        ctx = min(max(0, ctx), MAX_CONTEXT)
        context_before = ctx
        context_after = ctx

        # Streaming search — never read the whole file. Buffer context_before
        # lines via deque; read ahead for context_after lines.
        results = []
        match_count = 0
        total_bytes = 0
        scanned = 0
        context_buffer = deque(maxlen=context_before) if context_before else deque()
        after_remaining = 0  # how many post-context lines we still owe
        block = []  # current match block being assembled

        try:
            # dir_fd + O_NOFOLLOW — closes the TOCTOU window between
            # validation and open(). A symlink swap or rotation race
            # between _validate_filename and this open() would have
            # pointed the read outside the log dir; now O_NOFOLLOW
            # raises ELOOP and we return an error.
            log_dir = _resolve_log_dir()
            try:
                f = _open_log_file(filepath, log_dir)
            except FileNotFoundError:
                return f"Log file not found: {filename}"
            with f:
                for i, raw_line in enumerate(f):
                    if scanned >= MAX_LINES_SCAN:
                        break
                    scanned += 1
                    line = raw_line.rstrip("\n")
                    # Truncate BEFORE regex (caps line length to prevent
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
                        # Finalize any prior block before starting a new match
                        if results and block:
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
                            block = []
                        # Build new block: context_before (buffer) + match line
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

                    # Emit block when current match finishes after-context window
                    if is_match or after_remaining == 0:
                        block_text = "\n".join(block)
                        if total_bytes + len(block_text) > MAX_BYTES_RESPONSE:
                            results.append(f"... (truncated at {MAX_BYTES_RESPONSE // 1024} KB limit)")
                            break
                        results.append(block_text)
                        total_bytes += len(block_text) + 1
                        block = []  # reset for next iteration

                    # Update rolling context buffer for next iteration
                    if context_before:
                        context_buffer.append((i, line))

                    # Cap match count at limit (allow current match to render)
                    if match_count >= limit and after_remaining == 0:
                        break
        except OSError as e:
            return f"Error reading file: {e}"

        # Flush any trailing block if we hit limits mid-context
        if block and not (
            results
            and results[-1].startswith(f"... (truncated at {MAX_BYTES_RESPONSE // 1024} KB limit)")
        ):
            block_text = "\n".join(block)
            if total_bytes + len(block_text) <= MAX_BYTES_RESPONSE:
                results.append(block_text)

        if not results:
            return (
                f"No matches found for pattern '{pattern}'"
                f" in {filename} (scanned {scanned} lines)"
            )

        header = (
            f"File: {filename} | Pattern: {pattern}"
            f" | {match_count} match(es) | scanned {scanned} lines\n"
        )
        return header + "\n".join(results)

    @register_tool_category("system-log")
    @tool
    def ens_system_log_tail(
        filename: str = "ensemble.log",
        lines: int = 50,
    ) -> str:
        """Read the last N lines of a daemon log file (tail equivalent).

        Returns recent log lines with line numbers. Uses seek-from-end
        to avoid loading multi-MB files into memory.

        Args:
            filename: Log file name. Defaults to "ensemble.log".
            lines: Number of lines to return from the end of the file.
                Capped at 200. Defaults to 50.

        Returns:
            Last N lines with line numbers, or an error message if the
            file is missing/empty.
        """
        lines = min(lines, MAX_LINES_TAIL)
        try:
            filepath = _validate_filename(filename)
        except ValueError as e:
            return f"Error: {e}"

        # Read the last TAIL_SEEK_CHUNK bytes from end of file. Avoids
        # loading multi-MB files into memory just to tail them.
        #
        # Open via dir_fd + O_NOFOLLOW (TOCTOU defense) and read the
        # size from the open fd (``os.fstat(fd).st_size``) rather than
        # ``os.path.getsize(path)`` — the latter races against rotation
        # and could return a different value than the fd's actual size.
        try:
            log_dir = _resolve_log_dir()
            try:
                f = _open_log_file(filepath, log_dir, mode="rb")
            except (FileNotFoundError, IsADirectoryError):
                if not filepath.exists():
                    return f"Log file not found: {filename}"
                if not filepath.is_file():
                    return f"Not a file: {filename}"
                return f"Log file not found: {filename}"
            with f:
                # Use fstat on the open fd to avoid racing against rotation.
                fd = f.fileno()
                file_size = os.fstat(fd).st_size
                if file_size == 0:
                    return f"Log file is empty: {filename}"
                read_size = min(TAIL_SEEK_CHUNK, file_size)
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

        total = len(all_lines)
        sliced = all_lines[-lines:]

        # Build numbered output with redaction, line truncation, byte cap
        result_lines = []
        total_bytes = 0
        # Offset the displayed line number by the dropped prefix lines so
        # numbers reflect actual position in the original file.
        dropped = total - len(sliced)
        for idx, line_text in enumerate(sliced):
            line_text = _truncate_line(line_text)
            line_text = _redact_line(line_text)
            numbered = f"{dropped + idx + 1:>6}: {line_text}"
            if total_bytes + len(numbered) > MAX_BYTES_RESPONSE:
                result_lines.append(f"... (truncated at {MAX_BYTES_RESPONSE // 1024} KB limit)")
                break
            result_lines.append(numbered)
            total_bytes += len(numbered) + 1

        header = f"File: {filename} | Last {len(result_lines)} of {total} lines\n"
        return header + "\n".join(result_lines)

    return [ens_system_log_list, ens_system_log_read, ens_system_log_search, ens_system_log_tail]
