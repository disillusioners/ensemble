"""File system tools for reading files and directories."""

import os
import tempfile
from pathlib import Path
from langchain_core.tools import tool
from typing import Optional

from ._tool_registry import register_tool_category
from ._truncate import truncate_output

CATEGORY_NAME = "File Operations"
CATEGORY_DOC = """\
Read, write, edit, and search files and directories.

**Rules**:
- `workdir` parameters are MUST for all file operations. Always specify them to avoid errors.
- `path` is always relative to `workdir`. Never use absolute paths.

Example read_file:
```json
{
  "path": ".agents/shared/planning/<feature>/plan-overview.md",
  "workdir": "/path_to/current/working/project/directory"
}
```
"""

def _is_within_workdir(workdir: Path, target: Path) -> bool:
    """Check if target path is within workdir boundary or a temp directory.
    
    Paths are allowed if they are:
    1. Within the workdir, OR
    2. Within the system temp directory or common temp directories
    """
    try:
        target.relative_to(workdir)
        return True
    except ValueError:
        pass
    
    # Allow access to system temp directories
    # Check multiple common temp locations (handles macOS /tmp -> /private/tmp symlink)
    temp_dirs = [
        Path(tempfile.gettempdir()).resolve(),  # System temp (e.g., /var/folders/...)
        Path("/tmp").resolve(),                  # Common Unix temp
        Path("/private/tmp").resolve(),          # macOS symlink target
        Path("/var/tmp").resolve(),              # Unix persistent temp
    ]
    
    # On Windows, also check common Windows temp locations
    if os.name == 'nt':
        temp_dirs.extend([
            Path(os.environ.get("TEMP", "")).resolve(),
            Path(os.environ.get("TMP", "")).resolve(),
        ])
    
    for temp_dir in temp_dirs:
        try:
            target.relative_to(temp_dir)
            return True
        except (ValueError, OSError):
            continue
    
    return False


@register_tool_category("filesystem")
@tool
def list_directory(
    path: str,
    workdir: str | None = None,
    show_hidden: bool = False
) -> str:
    """List directory contents. Use tool_help("list_directory") for details."""
    if not workdir or not workdir.strip():
        return "ERROR: workdir is required. Agents must always specify workdir explicitly — typically the project directory."
    
    try:
        base_path = Path(workdir).expanduser().resolve()
        dir_path = (base_path / path).expanduser().resolve()
        
        if not _is_within_workdir(base_path, dir_path):
            return f"ERROR: Path escapes workdir boundary: {path}"
        
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
        )
        
        if result.truncated:
            return result.content + "\n💡 **Tip:** Use more specific paths (e.g., `path=\"subdir\"`) to narrow the listing."
        return content
        
    except Exception as e:
        return f"ERROR: {str(e)}"

list_directory._full_doc_ = """List contents of a directory.

Args:
    path: Directory path to list (relative to workdir)
    workdir: Base directory for relative paths (required)
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
    limit: int = 200,
) -> str:
    """Read file contents. Use tool_help("read_file") for details."""
    if not workdir or not workdir.strip():
        return "ERROR: workdir is required. Agents must always specify workdir explicitly — typically the project directory."
    
    try:
        base_path = Path(workdir).expanduser().resolve()
        file_path = (base_path / path).expanduser().resolve()
        
        if not _is_within_workdir(base_path, file_path):
            return f"ERROR: Path escapes workdir boundary: {path}"
        
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
        
        # Apply final truncation for safety
        trunc_result = truncate_output(formatted_content, tool_name="read_file")
        
        if trunc_result.truncated:
            return trunc_result.content + trunc_result.pagination_hint
        
        return formatted_content
        
    except UnicodeDecodeError:
        return f"ERROR: Cannot read file as text (binary file?): {path}"
    except Exception as e:
        return f"ERROR: {str(e)}"

read_file._full_doc_ = """Read contents of a file.

Args:
    path: File path to read (relative to workdir)
    workdir: Base directory for relative paths (required)
    offset: Line number to start from (1-indexed, default: 1)
    limit: Maximum number of lines to read (default: 200)

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
    if not workdir or not workdir.strip():
        return "ERROR: workdir is required. Agents must always specify workdir explicitly — typically the project directory."
    
    try:
        base_path = Path(workdir).expanduser().resolve()
        search_path = (base_path / path).expanduser().resolve()
        
        if not _is_within_workdir(base_path, search_path):
            return f"ERROR: Path escapes workdir boundary: {path}"
        
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
        
        # Apply truncation
        content = "\n".join(result)
        trunc_result = truncate_output(content, tool_name="glob_files", max_chars=6000, max_lines=100)
        
        if trunc_result.truncated:
            return trunc_result.content + trunc_result.pagination_hint
        return content
        
    except Exception as e:
        return f"ERROR: {str(e)}"

glob_files._full_doc_ = """Find files matching a glob pattern.

Args:
    pattern: Glob pattern (e.g., "**/*.py", "*.md", "src/**/*.ts")
    workdir: Base directory for relative paths (required)
    path: Directory to search in (relative to workdir, default: ".")
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
    if not workdir or not workdir.strip():
        return "ERROR: workdir is required. Agents must always specify workdir explicitly — typically the project directory."
    
    try:
        base_path = Path(workdir).expanduser().resolve()
        file_path = (base_path / path).expanduser().resolve()
        
        if not _is_within_workdir(base_path, file_path):
            return f"ERROR: Path escapes workdir boundary: {path}"
        
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
    path: File path to write to (relative to workdir)
    workdir: Base directory for relative paths (required)
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
    if not workdir or not workdir.strip():
        return "ERROR: workdir is required. Agents must always specify workdir explicitly — typically the project directory."
    
    try:
        import re
        
        base_path = Path(workdir).expanduser().resolve()
        search_path = (base_path / path).expanduser().resolve()
        
        if not _is_within_workdir(base_path, search_path):
            return f"ERROR: Path escapes workdir boundary: {path}"
        
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
        trunc_result = truncate_output(content, tool_name="grep_files", max_chars=6000, max_lines=100)
        
        if trunc_result.truncated:
            return trunc_result.content + trunc_result.pagination_hint
        return content
        
    except re.error as e:
        return f"ERROR: Invalid regex pattern: {e}"
    except Exception as e:
        return f"ERROR: {str(e)}"

grep_files._full_doc_ = """Search file contents using regex patterns.

Args:
    pattern: Regex pattern to search for
    workdir: Base directory for relative paths (required)
    path: Directory to search in (relative to workdir, default: ".")
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
    if not workdir or not workdir.strip():
        return "ERROR: workdir is required. Agents must always specify workdir explicitly — typically the project directory."
    
    try:
        base_path = Path(workdir).expanduser().resolve()
        file_path = (base_path / path).expanduser().resolve()
        
        if not _is_within_workdir(base_path, file_path):
            return f"ERROR: Path escapes workdir boundary: {path}"
        
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
    path: File path to edit (relative to workdir)
    old_string: The exact string to find and replace (supports multi-line)
    new_string: The replacement string
    workdir: Base directory for relative paths (required)
    replace_all: If True, replace all occurrences; if False, replace only the first (default: False)

Returns:
    Success message with number of replacements made

Note:
    Use replace_all=True when the string appears multiple times and you want to replace all.
    Omit replace_all (or set False) for single replacements to avoid unintended changes.
"""
