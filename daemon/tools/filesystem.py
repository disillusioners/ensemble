"""File system tools for reading files and directories."""

import os
import re
import tempfile
from pathlib import Path
from langchain_core.tools import tool

from ._tool_registry import register_tool_category
from ._truncate import truncate_output

CATEGORY_NAME = "File Operations"
CATEGORY_DOC = """\
Read, write, edit, and search files and directories.

**Rules**:
- `workdir` is required when `path` is relative. If `path` is absolute (e.g. `/abs/path`
  on Unix or `C:\\path\\to\\file` on Windows), `workdir` may be omitted and the path is
  used as-is.
- When `path` is relative, it is resolved against `workdir` and must stay within it.
- If you need to access files outside the project directory, pass an absolute path
  explicitly (e.g. `/abs/path/to/file`).

Example read_file (relative path):
```json
{
  "path": ".agents/shared/planning/<feature>/plan-overview.md",
  "workdir": "/path_to/current/working/project/directory"
}
```

Example read_file (absolute path, workdir not required):
```json
{
  "path": "/tmp/shared/plan-overview.md"
}
```
"""


_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_UNC_RE = re.compile(r"^[\\/]{2}")


def _is_absolute_path(path: str) -> bool:
    """Return True if `path` is absolute on the current OS or matches a Windows
    absolute pattern (drive letter or UNC). Cross-platform safe: a Windows-style
    absolute path is still recognized as absolute when the daemon runs on Unix,
    so agents on either OS get consistent behavior.
    """
    if not path:
        return False
    try:
        if Path(path).is_absolute():
            return True
    except (OSError, ValueError):
        return False
    if _WINDOWS_DRIVE_RE.match(path) or _WINDOWS_UNC_RE.match(path):
        return True
    return False


def _resolve_target_path(
    path: str,
    workdir: str | None,
) -> tuple[Path | None, Path | None, str | None]:
    """Resolve `path` against `workdir` (relative) or use it as-is (absolute).

    Returns:
        (target_path, base_path, error). `base_path` is the workdir Path when
        `path` is relative, and None when `path` is absolute (no boundary check
        is applied). On error, target_path and base_path are None.
    """
    if _is_absolute_path(path):
        try:
            return Path(path).expanduser(), None, None
        except (OSError, RuntimeError) as e:
            return None, None, f"ERROR: Invalid absolute path: {e}"

    if not workdir or not workdir.strip():
        return (
            None,
            None,
            "ERROR: workdir is required for relative paths. Agents must always "
            "specify workdir explicitly — typically the project directory. "
            "Absolute paths do not need workdir.",
        )

    try:
        base = Path(workdir).expanduser().resolve()
        target = (base / path).expanduser().resolve()
    except (OSError, RuntimeError) as e:
        return None, None, f"ERROR: Invalid path: {e}"

    # Verify the resolved workdir actually exists on disk. When the caller
    # passes a typo'd / hallucinated workdir (e.g. `ngienminhkha` instead of
    # `nguyenminhkha`), `base` resolves successfully but is not a real
    # directory. Without this check, every downstream tool would report a
    # misleading "File does not exist" against the (valid) target path while
    # the real cause is the missing workdir. Surface the original workdir
    # string the caller passed in, so the agent can spot its own typo.
    if not base.exists():
        return None, None, (
            f"ERROR: Working directory does not exist: {workdir} "
            "— check the workdir path. Was it typed correctly?"
        )

    return target, base, None


def _resolve_within_workdir(
    path: str,
    workdir: str | None,
) -> tuple[Path | None, str | None]:
    """Resolve `path` and verify it stays within `workdir` (when relative).

    Combines `_resolve_target_path` with the `_is_within_workdir` boundary
    check, so callers get a single (target, err) tuple and can't forget to
    apply the boundary check.

    Returns:
        (target_path, error). On error, target_path is None. For absolute
        paths the boundary check is intentionally skipped.
    """
    target, base, err = _resolve_target_path(path, workdir)
    if err:
        return None, err
    if base is not None and not _is_within_workdir(base, target):
        return None, f"ERROR: Path escapes workdir boundary: {path}"
    return target, None


def _normed_contains(base: Path, target: Path) -> bool:
    """Check if target is within base using OS-appropriate case normalization."""
    try:
        normed_target = Path(os.path.normcase(str(target.resolve())))
        normed_base = Path(os.path.normcase(str(base.resolve())))
        normed_target.relative_to(normed_base)
        return True
    except (ValueError, OSError):
        return False


def _is_within_workdir(workdir: Path, target: Path) -> bool:
    """Check if target path is within workdir boundary or a temp directory.
    
    Paths are allowed if they are:
    1. Within the workdir, OR
    2. Within the system temp directory or common temp directories
    """
    if _normed_contains(workdir, target):
        return True
    
    # Allow access to system temp directories
    # Check multiple common temp locations (handles macOS /tmp -> /private/tmp symlink)
    temp_dirs = [
        Path(tempfile.gettempdir()).resolve(),
        Path("/tmp").resolve(),
        Path("/private/tmp").resolve(),
        Path("/var/tmp").resolve(),
    ]
    
    # On Windows, also check common Windows temp locations
    if os.name == 'nt':
        system_drive = os.environ.get("SystemDrive", "C:")
        temp_dirs.extend([
            Path(os.environ.get("TEMP") or tempfile.gettempdir()).resolve(),
            Path(os.environ.get("TMP") or tempfile.gettempdir()).resolve(),
            Path(f"{system_drive}\\tmp").resolve(),
        ])
    
    for temp_dir in temp_dirs:
        if _normed_contains(temp_dir, target):
            return True
    
    return False


@register_tool_category("filesystem")
@tool
def list_directory(
    path: str,
    workdir: str | None = None,
    show_hidden: bool = False
) -> str:
    """List directory contents. Use tool_help("list_directory") for details."""
    dir_path, err = _resolve_within_workdir(path, workdir)
    if err:
        return err

    try:
        if not dir_path.exists():
            return f"ERROR: Path does not exist: {path}"
        
        if not dir_path.is_dir():
            return f"ERROR: Not a directory: {path}"
        
        entries = []
        for entry in sorted(dir_path.iterdir()):
            name = entry.name
            
            # Skip hidden files unless requested
            if not show_hidden and name.startswith("."):
                continue
            
            # Add type indicator
            if entry.is_dir():
                name += "/"
            elif entry.is_symlink():
                name += "@"
            elif os.access(entry, os.X_OK):
                name += "*"
            
            entries.append(name)
        
        if not entries:
            return f"(empty directory: {dir_path})"
        
        # Apply truncation for safety
        content = "\n".join(entries)
        result = truncate_output(
            content,
            tool_name="list_directory",
            max_chars=6000,
            max_lines=150,
            offset_indexed=False,  # 1-indexed offset (for consistency with other tools)
        )
        
        if result.truncated:
            return result.content + "\n💡 **Tip:** Use more specific paths (e.g., `path=\"subdir\"`) to narrow the listing."
        return content
        
    except Exception as e:
        return f"ERROR: {str(e)}"

list_directory._full_doc_ = """List contents of a directory.

Args:
    path: Directory path to list. Absolute paths are allowed (workdir not needed);
          relative paths are resolved against `workdir`.
    workdir: Base directory for relative paths. Required when `path` is relative;
              optional (ignored) when `path` is absolute.
    show_hidden: Whether to show hidden files (default: False)

Returns:
    Directory listing with file type indicators:
    - / suffix for directories
    - @ suffix for symlinks
    - * suffix for executables
"""

@register_tool_category("filesystem")
@tool
def read_file(
    path: str,
    workdir: str | None = None,
    offset: int = 1,
    limit: int = 2000,
) -> str:
    """Read file contents. Use tool_help("read_file") for details."""
    file_path, err = _resolve_within_workdir(path, workdir)
    if err:
        return err

    try:
        if not file_path.exists():
            return f"ERROR: File does not exist: {path}"
        
        if not file_path.is_file():
            return f"ERROR: Not a file: {path}"
        
        lines = file_path.read_text(encoding="utf-8").splitlines()
        
        # Apply offset and limit (1-indexed offset)
        start = max(0, offset - 1)
        end = start + limit
        selected_lines = lines[start:end]
        
        # Format with line numbers
        result = []
        for i, line in enumerate(selected_lines, start=offset):
            # Truncate very long lines
            if len(line) > 2000:
                line = line[:2000] + "... (truncated)"
            result.append(f"{i}: {line}")
        
        total_lines = len(lines)
        header = f"File: {file_path} ({total_lines} lines total)\n{'-' * 40}\n"
        formatted_content = header + "\n".join(result)
        
        # Check if truncation needed (character limit for safety)
        if len(formatted_content) > 6000:
            # Find a good truncation point at line boundary
            truncated_lines = []
            char_count = 0
            for line in result:
                if char_count + len(line) + 1 > 6000:
                    break
                truncated_lines.append(line)
                char_count += len(line) + 1
            
            shown_lines = len(truncated_lines)
            end_line = offset + shown_lines - 1
            next_offset = offset + shown_lines
            
            # Build pagination hint
            pagination_hint = (
                f"\n---\n"
                f"Showing lines {offset} to {end_line} of {total_lines}. "
                f"Use offset={next_offset} for more."
            )
            
            truncated_content = header + "\n".join(truncated_lines)
            return truncated_content + pagination_hint
        
        # Add hint when content fits char limit but lines exceed limit
        end_line = offset + len(selected_lines) - 1
        if total_lines > end_line:
            return formatted_content + f"\n\n---\nShowing lines {offset} to {end_line} of {total_lines}. Use offset={end_line + 1} for more."
        
        return formatted_content
        
    except UnicodeDecodeError:
        return f"ERROR: Cannot read file as text (binary file?): {path}"
    except Exception as e:
        return f"ERROR: {str(e)}"

read_file._full_doc_ = """Read contents of a file.

Args:
    path: File path to read. Absolute paths are allowed (workdir not needed);
          relative paths are resolved against `workdir`.
    workdir: Base directory for relative paths. Required when `path` is relative;
              optional (ignored) when `path` is absolute.
    offset: Line number to start from (1-indexed, default: 1)
    limit: Maximum number of lines to read (default: 2000)

Returns:
    File contents with line numbers (format: "line_num: content")
"""


@register_tool_category("filesystem")
@tool
def glob_files(
    pattern: str,
    workdir: str | None = None,
    path: str = ".",
    offset: int = 0,
    limit: int = 100,
) -> str:
    """Find files matching a glob pattern. Use tool_help("glob_files") for details."""
    search_path, err = _resolve_within_workdir(path, workdir)
    if err:
        return err

    try:
        if not search_path.exists():
            return f"ERROR: Path does not exist: {path}"
        
        # Find matching files
        matches = list(search_path.glob(pattern))
        
        # Filter to only files (not directories)
        files = [m for m in matches if m.is_file()]
        
        if not files:
            return f"No files matching pattern: {pattern}"
        
        # Sort by modification time (newest first)
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        
        # Format output relative to search_path
        result = []
        for f in files:
            try:
                rel_path = f.relative_to(search_path)
                result.append(str(rel_path))
            except ValueError:
                result.append(str(f))
        
        # Apply pagination
        if offset > 0:
            result = result[offset:]
        if limit and limit > 0:
            result = result[:limit]
        
        if not result:
            return f"No files matching pattern: {pattern}"
        
        content = "\n".join(result)
        
        # Check if truncation needed
        if len(content) > 6000 or len(result) > limit:
            # Truncate at line boundary
            truncated_lines = result[:limit]
            shown = len(truncated_lines)
            total = len(files)
            next_offset = offset + limit
            
            # Build pagination hint
            pagination_hint = (
                f"\n---\n"
                f"Showing results {offset + 1} to {offset + shown} of {total}. "
                f"Use offset={next_offset} for next page."
            )
            
            return "\n".join(truncated_lines) + pagination_hint
        
        return content
        
    except Exception as e:
        return f"ERROR: {str(e)}"

glob_files._full_doc_ = """Find files matching a glob pattern.

Args:
    pattern: Glob pattern (e.g., "**/*.py", "*.md", "src/**/*.ts")
    workdir: Base directory for relative paths. Required when `path` is relative;
              optional (ignored) when `path` is absolute.
    path: Directory to search in. Absolute paths are allowed (workdir not needed);
          relative paths are resolved against `workdir`. Default: "."
    offset: Number of results to skip (default: 0)
    limit: Maximum results to return (default: 100)

Returns:
    List of matching file paths, sorted by modification time (newest first)
"""


@register_tool_category("filesystem")
@tool
def write_file(
    content: str,
    path: str,
    workdir: str | None = None,
    append: bool = False
) -> str:
    """Write or append content to a file. Use tool_help("write_file") for details."""
    file_path, err = _resolve_within_workdir(path, workdir)
    if err:
        return err

    try:
        # Create parent directories if they don't exist
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        mode = "a" if append else "w"
        with open(file_path, mode, encoding="utf-8") as f:
            f.write(content)
        
        action = "Appended to" if append else "Written to"
        return f"SUCCESS: {action} {file_path}"
        
    except Exception as e:
        return f"ERROR: {str(e)}"

write_file._full_doc_ = """Write or append content to a file.

Args:
    content: The text content to write
    path: File path to write to. Absolute paths are allowed (workdir not needed);
          relative paths are resolved against `workdir`.
    workdir: Base directory for relative paths. Required when `path` is relative;
              optional (ignored) when `path` is absolute.
    append: If True, append to existing file; if False, overwrite (default: False)

Returns:
    Success message with the file path
"""


@register_tool_category("filesystem")
@tool
def grep_files(
    pattern: str,
    workdir: str | None = None,
    path: str = ".",
    include: str = "",
    case_sensitive: bool = False,
    whole_word: bool = False,
    offset: int = 0,
    limit: int = 100,
) -> str:
    """Search file contents using regex patterns. Use tool_help("grep_files") for details."""
    search_path, err = _resolve_within_workdir(path, workdir)
    if err:
        return err

    try:
        if not search_path.exists():
            return f"ERROR: Path does not exist: {path}"
        
        # Build glob pattern from include filter
        glob_pattern = include if include else "**/*"
        
        # Compile regex
        flags = 0 if case_sensitive else re.IGNORECASE
        if whole_word:
            pattern = rf"\b{re.escape(pattern)}\b"
        
        regex = re.compile(pattern, flags)
        
        # Search files
        matches = []
        for file_path in search_path.glob(glob_pattern):
            if not file_path.is_file():
                continue
            
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, PermissionError, IsADirectoryError):
                continue
            
            for line_num, line in enumerate(lines, start=1):
                if regex.search(line):
                    # Truncate long lines
                    display_line = line[:500] + "..." if len(line) > 500 else line
                    matches.append(f"{file_path}:{line_num}: {display_line}")
        
        # Apply pagination
        if offset > 0:
            matches = matches[offset:]
        if limit and limit > 0:
            matches = matches[:limit]
        
        if not matches:
            return f"No matches found for: {pattern}"
        
        content = "\n".join(matches)
        
        # Check if truncation needed
        if len(content) > 6000 or len(matches) > limit:
            # Truncate at line boundary
            truncated_matches = matches[:limit]
            shown = len(truncated_matches)
            total = len(matches)
            next_offset = offset + limit
            
            # Build pagination hint
            pagination_hint = (
                f"\n---\n"
                f"Showing results {offset + 1} to {offset + shown} of {total}. "
                f"Use offset={next_offset} for next page."
            )
            
            return "\n".join(truncated_matches) + pagination_hint
        
        return content
        
    except re.error as e:
        return f"ERROR: Invalid regex pattern: {e}"
    except Exception as e:
        return f"ERROR: {str(e)}"

grep_files._full_doc_ = """Search file contents using regex patterns.

Args:
    pattern: Regex pattern to search for
    workdir: Base directory for relative paths. Required when `path` is relative;
              optional (ignored) when `path` is absolute.
    path: Directory to search in. Absolute paths are allowed (workdir not needed);
          relative paths are resolved against `workdir`. Default: "."
    include: Glob pattern to filter files (e.g., "*.py", "*.{js,ts}")
    case_sensitive: Whether search is case-sensitive (default: False)
    whole_word: Match whole words only (default: False)
    offset: Number of results to skip (default: 0)
    limit: Maximum matches to return (default: 100)

Returns:
    Matching lines with file path and line number (format: "path:line: content")
"""


@register_tool_category("filesystem")
@tool
def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    workdir: str | None = None,
    replace_all: bool = False
) -> str:
    """Replace text in a file using exact string matching. Use tool_help("edit_file") for details."""
    file_path, err = _resolve_within_workdir(path, workdir)
    if err:
        return err

    try:
        if not file_path.exists():
            return f"ERROR: File does not exist: {path}"
        
        if not file_path.is_file():
            return f"ERROR: Not a file: {path}"
        
        content = file_path.read_text(encoding="utf-8")
        
        if old_string not in content:
            return f"ERROR: String not found in file: {old_string[:100]}"
        
        count = content.count(old_string)
        
        if replace_all:
            new_content = content.replace(old_string, new_string)
            action = f"Replaced all {count} occurrences"
        else:
            if count > 1:
                return f"ERROR: String found {count} times. Use replace_all=True to replace all occurrences."
            new_content = content.replace(old_string, new_string, 1)
            action = "Replaced 1 occurrence"
        
        file_path.write_text(new_content, encoding="utf-8")
        
        return f"SUCCESS: {action} in {file_path}"
        
    except Exception as e:
        return f"ERROR: {str(e)}"

edit_file._full_doc_ = """Replace text in a file using exact string matching.

Args:
    path: File path to edit. Absolute paths are allowed (workdir not needed);
          relative paths are resolved against `workdir`.
    old_string: The exact string to find and replace (supports multi-line)
    new_string: The replacement string
    workdir: Base directory for relative paths. Required when `path` is relative;
              optional (ignored) when `path` is absolute.
    replace_all: If True, replace all occurrences; if False, replace only the first (default: False)

Returns:
    Success message with number of replacements made

Note:
    Use replace_all=True when the string appears multiple times and you want to replace all.
    Omit replace_all (or set False) for single replacements to avoid unintended changes.
"""
