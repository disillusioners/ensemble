"""File system tools for reading files and directories."""

import os
from pathlib import Path
from langchain_core.tools import tool
from typing import Optional


@tool
def list_directory(
    path: str = ".",
    show_hidden: bool = False
) -> str:
    """List contents of a directory.
    
    Args:
        path: Directory path to list (default: current directory)
        show_hidden: Whether to show hidden files (default: False)
    
    Returns:
        Directory listing with file type indicators:
        - / suffix for directories
        - @ suffix for symlinks
        - * suffix for executables
    """
    try:
        dir_path = Path(path).expanduser().resolve()
        
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
        
        return "\n".join(entries)
        
    except Exception as e:
        return f"ERROR: {str(e)}"


@tool
def read_file(
    path: str,
    offset: int = 1,
    limit: int = 2000
) -> str:
    """Read contents of a file.
    
    Args:
        path: File path to read
        offset: Line number to start from (1-indexed, default: 1)
        limit: Maximum number of lines to read (default: 2000)
    
    Returns:
        File contents with line numbers (format: "line_num: content")
    """
    try:
        file_path = Path(path).expanduser().resolve()
        
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
        
        return header + "\n".join(result)
        
    except UnicodeDecodeError:
        return f"ERROR: Cannot read file as text (binary file?): {path}"
    except Exception as e:
        return f"ERROR: {str(e)}"


@tool
def glob_files(
    pattern: str,
    path: str = "."
) -> str:
    """Find files matching a glob pattern.
    
    Args:
        pattern: Glob pattern (e.g., "**/*.py", "*.md", "src/**/*.ts")
        path: Base directory to search from (default: current directory)
    
    Returns:
        List of matching file paths, sorted by modification time (newest first)
    """
    try:
        base_path = Path(path).expanduser().resolve()
        
        if not base_path.exists():
            return f"ERROR: Path does not exist: {path}"
        
        # Find matching files
        matches = list(base_path.glob(pattern))
        
        # Filter to only files (not directories)
        files = [m for m in matches if m.is_file()]
        
        if not files:
            return f"No files matching pattern: {pattern}"
        
        # Sort by modification time (newest first)
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        
        # Format output relative to base path
        result = []
        for f in files:
            try:
                rel_path = f.relative_to(base_path)
                result.append(str(rel_path))
            except ValueError:
                result.append(str(f))
        
        return "\n".join(result)
        
    except Exception as e:
        return f"ERROR: {str(e)}"
