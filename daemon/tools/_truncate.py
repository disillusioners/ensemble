"""Truncation and pagination utilities for tool responses.

This module provides centralized truncation to prevent 413 errors when
tool responses are too large for the LLM context.
"""

from dataclasses import dataclass
from typing import Literal


# Conservative defaults to prevent 413 errors
# Note: Adjust based on your LLM provider's actual payload limits
DEFAULT_MAX_CHARS = 6000  # Safe for most LLM contexts
DEFAULT_MAX_LINES = 100   # Reasonable line count before paging


@dataclass
class TruncationResult:
    """Result of a truncation operation."""

    content: str
    truncated: bool
    total_items: int | None = None
    shown_items: int | None = None
    pagination_hint: str | None = None
    truncation_type: Literal["lines", "chars", "both"] | None = None


def truncate_output(
    content: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_lines: int = DEFAULT_MAX_LINES,
    tool_name: str = "tool",
    offset_indexed: bool = False,
) -> TruncationResult:
    """Truncate output with pagination metadata.

    Args:
        content: The string content to truncate.
        max_chars: Maximum character count before truncation.
        max_lines: Maximum line count before truncation.
        tool_name: Name of the tool for pagination hints.
        offset_indexed: If True, offset starts at 0; if False, offset starts at 1.
            Used to calculate correct next offset in pagination hint.

    Returns:
        TruncationResult with truncated content and pagination info.
    """
    if not content:
        return TruncationResult(content="", truncated=False)

    lines = content.split('\n')
    total_lines = len(lines)

    # Check if truncation needed
    exceeds_chars = len(content) > max_chars
    exceeds_lines = total_lines > max_lines

    if not exceeds_chars and not exceeds_lines:
        return TruncationResult(content=content, truncated=False)

    # Determine truncation type for debugging
    if exceeds_chars and exceeds_lines:
        truncation_type = "both"
    elif exceeds_lines:
        truncation_type = "lines"
    else:
        truncation_type = "chars"

    # Build result respecting both limits
    # Prefer line-boundary truncation to preserve structure
    # (e.g., file:line:content format is only parseable at line boundaries)
    result_lines = []
    char_count = 0

    for i, line in enumerate(lines):
        # Stop if we've hit line limit
        if len(result_lines) >= max_lines:
            break

        remaining = max_chars - char_count
        if remaining <= 0:
            break

        # Truncate at line end, not mid-line, to preserve structure
        if len(line) >= remaining:
            # Only add if there's room for at least something
            if remaining > 10:
                result_lines.append(line[:remaining - 3] + "...")
            char_count = max_chars
            break
        else:
            result_lines.append(line)
            # Only add newline for non-last lines
            if i < len(lines) - 1:
                char_count += len(line) + 1
            else:
                char_count += len(line)

    shown_items = len(result_lines)
    truncated_content = '\n'.join(result_lines)

    return TruncationResult(
        content=truncated_content,
        truncated=True,
        total_items=total_lines,
        shown_items=shown_items,
        pagination_hint=_build_hint(tool_name, total_lines, shown_items, offset_indexed),
        truncation_type=truncation_type,
    )


def truncate_dict_result(
    data: dict,
    list_key: str,
    limit: int = 50,
) -> dict:
    """Truncate list within dict response, adding pagination metadata.

    Used for tools that return dicts (project_list, job_list, etc.)
    instead of strings.

    Args:
        data: The dictionary response to process.
        list_key: The key containing the list to truncate.
        limit: Maximum items to include.

    Returns:
        Dictionary with truncated list and pagination metadata.
    """
    items = data.get(list_key, [])
    if not isinstance(items, list):
        # Not a list, return as-is
        return data

    total = len(items)

    if total <= limit:
        return data

    return {
        **data,
        list_key: items[:limit],
        "_pagination": {
            "truncated": True,
            "total": total,
            "shown": limit,
            "hint": f"Showing {limit} of {total}. Use offset={limit} for next page.",
        }
    }


def _build_hint(
    tool_name: str,
    total: int,
    shown: int,
    offset_indexed: bool = False,
) -> str:
    """Build pagination hint message.
    
    Args:
        tool_name: Name of the tool for pagination hints.
        total: Total number of items.
        shown: Number of items shown.
        offset_indexed: If True, offset starts at 0; if False, offset starts at 1.
    """
    # Calculate next offset based on indexing
    # For 0-indexed (offset starts at 0): next_offset = shown
    # For 1-indexed (offset starts at 1): next_offset = shown + 1 (to skip shown items)
    next_offset = shown + 1 if offset_indexed else shown
    
    hint_lines = [
        "---",
        f"⚠️ **Results truncated**: Showing {shown} of {total} items.",
        "",
        "**To see more, use paging parameters:**",
        f"- `{tool_name}(..., offset={next_offset})` - Continue from where you left off",
        f"- `{tool_name}(..., limit=N)` - Adjust page size",
    ]
    
    # Don't suggest redirect-to-file for tools with built-in paging
    if tool_name not in ("read_file", "grep_files", "glob_files"):
        hint_lines.extend([
            "",
            "**💡 Better approach:** For large output, redirect to a file:",
            "  `command > /tmp/output.txt` then use `read_file` to view it.",
        ])
    
    return "\n".join(hint_lines)
