# Tool Response Truncation & Paging Plan

> **Council Review (2024-04-20)**: Plan reviewed by multi-LLM council. Critical issues identified and resolved below.

---

## Problem

Tools like `grep_files`, `glob_files`, `read_file`, and `bash` can return results exceeding the 413 error threshold when LLM processes responses. This causes failures in agent execution.

---

## Scope

Tools requiring truncation/paging support:

### String-Returning Tools

| Tool | Risk | Current Limits | Issue |
|------|------|----------------|-------|
| `grep_files` | 🔴 HIGH | Line truncation only (500 chars), **no match count limit** | Can return thousands of matches |
| `glob_files` | 🔴 HIGH | **No limits at all** | Can return entire filesystem |
| `list_directory` | 🟡 MEDIUM | **No limits** | Recursive listing can explode |
| `read_file` | 🟡 MEDIUM | 2000 line limit, but lines can be 2000+ chars | Large files still problematic |
| `bash` | 🔴 HIGH | Timeout only, **no output limit** | `ls -laR /` could explode |

### Dict-Returning Tools

| Tool | Risk | Current Limits | Issue |
|------|------|----------------|-------|
| `project_list` | 🟡 MEDIUM | limit=50 default | Needs consistent truncation metadata |
| `job_list` | 🟡 MEDIUM | limit=50, max=100 | Needs consistent truncation metadata |
| `queue_list` | 🟡 MEDIUM | No pagination | Can return large queue lists |
| `dlq_list` | 🟡 MEDIUM | limit=50 | Needs consistent truncation metadata |

---

## Solution: Unified Truncation Layer

### Strategy

Add a **centralized truncation middleware** in `daemon/tools/` that:
1. Wraps tool outputs before returning to LLM
2. Detects oversized responses
3. Provides pagination hints when truncated

---

## Implementation

### 1. Create Truncation Utility (`daemon/tools/_truncate.py`)

```python
"""Truncation and pagination utilities for tool responses."""

from dataclasses import dataclass
from typing import Literal

# Conservative defaults to prevent 413 errors
# Note: Actual 413 threshold should be verified with your LLM provider
DEFAULT_MAX_CHARS = 6000   # Safe for most LLM contexts
DEFAULT_MAX_LINES = 100    # Reasonable line count before paging

@dataclass
class TruncationResult:
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
) -> TruncationResult:
    """Truncate output with pagination metadata."""
    
    lines = content.split('\n')
    total_lines = len(lines)
    
    # Check if truncation needed
    exceeds_chars = len(content) > max_chars
    exceeds_lines = total_lines > max_lines
    
    if not exceeds_chars and not exceeds_lines:
        return TruncationResult(
            content=content,
            truncated=False,
        )
    
    # Determine truncation type for debugging
    if exceeds_chars and exceeds_lines:
        truncation_type = "both"
    elif exceeds_lines:
        truncation_type = "lines"
    else:
        truncation_type = "chars"
    
    # Build result respecting both limits
    result_lines = []
    char_count = 0
    
    for line in lines:
        # Stop if we've hit line limit
        if len(result_lines) >= max_lines:
            break
            
        remaining = max_chars - char_count
        if remaining <= 0:
            break
            
        # For grep output, prefer line-boundary truncation to preserve structure
        # (file.py:line:content format is parseable only at line boundaries)
        if len(line) > remaining:
            # Truncate at line end, not mid-line
            break
        else:
            result_lines.append(line)
            char_count += len(line) + 1  # +1 for newline
    
    shown_items = len(result_lines)
    truncated_content = '\n'.join(result_lines)
    
    return TruncationResult(
        content=truncated_content,
        truncated=True,
        total_items=total_lines,
        shown_items=shown_items,
        pagination_hint=_build_hint(tool_name, total_lines, shown_items),
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
    """
    items = data.get(list_key, [])
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


def _build_hint(tool_name: str, total: int, shown: int) -> str:
    return f"""
---
⚠️ **Results truncated**: Showing {shown} of {total} items.

**To see more, use paging parameters:**
- `{tool_name}(..., offset={shown})` - Continue from where you left off
- `{tool_name}(..., limit=N)` - Adjust page size

**💡 Better approach:** For complex searches, consider refining your query
(e.g., `include="*.py"`, `pattern="specific_term"`) to narrow results.
"""
```

---

### 2. Apply to String-Returning Tools

#### `daemon/tools/filesystem.py` - `grep_files`

```python
from ._truncate import truncate_output

def grep_files(
    pattern: str,
    include: str | None = None,
    path: str | None = None,
    offset: Annotated[int, Field(default=0, ge=0)] = 0,
    limit: Annotated[int, Field(default=100, ge=1, le=500)] = 100,
) -> str:
    # ... existing logic (skip matches based on offset/limit) ...
    
    result = truncate_output(
        output.getvalue(),
        tool_name="grep_files",
        max_chars=6000,
        max_lines=100,
    )
    
    return result.content + (result.pagination_hint if result.truncated else "")
```

#### `daemon/tools/filesystem.py` - `glob_files`

```python
from ._truncate import truncate_output

def glob_files(
    pattern: str,
    path: str | None = None,
    offset: Annotated[int, Field(default=0, ge=0)] = 0,
    limit: Annotated[int, Field(default=100, ge=1, le=500)] = 100,
) -> str:
    # ... existing logic (skip results based on offset/limit) ...
    
    result = truncate_output(
        output.getvalue(),
        tool_name="glob_files",
        max_chars=6000,
        max_lines=100,
    )
    
    return result.content + (result.pagination_hint if result.truncated else "")
```

#### `daemon/tools/filesystem.py` - `list_directory`

```python
from ._truncate import truncate_output

def list_directory(...) -> str:
    # ... existing logic ...
    
    result = truncate_output(
        output.getvalue(),
        tool_name="list_directory",
        max_chars=6000,
        max_lines=150,
    )
    
    return result.content + (result.pagination_hint if result.truncated else "")
```

#### `daemon/tools/filesystem.py` - `read_file`

```python
def read_file(
    file_path: str,
    workdir: str | None = None,
    offset: Annotated[int, Field(default=1, ge=1)] = 1,
    limit: Annotated[int, Field(default=200, ge=1, le=500)] = 200,
) -> str:
    # ... existing logic ...
    
    # Apply truncation for safety (in case of very long lines)
    result = truncate_output(output, tool_name="read_file")
    
    if result.truncated:
        return result.content + result.pagination_hint
    
    return result.content
```

#### `daemon/tools/bash.py`

```python
from ._truncate import truncate_output

def bash(
    command: str,
    timeout: int | None = None,
    workdir: str | None = None,
    max_output_chars: Annotated[int, Field(default=10000, ge=1000, le=50000)] = 10000,
) -> str:
    # ... existing logic ...
    
    result = truncate_output(
        output.getvalue(),
        tool_name="bash",
        max_chars=min(6000, max_output_chars),  # Hard limit of 6000 for LLM safety
        max_lines=100,
    )
    
    return result.content + (result.pagination_hint if result.truncated else "")
```

---

### 3. Apply to Dict-Returning Tools

#### `daemon/tools/project.py` - `project_list`

```python
from ._truncate import truncate_dict_result

def project_list(limit: int = 50) -> dict:
    # ... existing logic ...
    
    result = truncate_dict_result(raw_result, list_key="projects", limit=limit)
    return result
```

#### `daemon/tools/job_queue.py` - `job_list`

```python
from ._truncate import truncate_dict_result

def job_list(
    status: JobStatus | None = None,
    queue: str | None = None,
    limit: Annotated[int, Field(default=50, ge=1, le=100)] = 50,
) -> dict:
    # ... existing logic ...
    
    result = truncate_dict_result(raw_result, list_key="jobs", limit=limit)
    return result
```

---

## Hint Messages Design

When a tool exceeds one page, display **two hints**:

### Hint 1: Paging Guide

```
💡 **To see more, use paging parameters:**
- `tool_name(..., offset=N)` - Continue from where you left off
- `tool_name(..., limit=N)` - Adjust page size
```

### Hint 2: Query Refinement

> ⚠️ **Note**: Council identified that `simplify` skill does NOT currently exist.
> Using query refinement hint instead.

```
💡 **Better approach:** Consider refining your query
(e.g., `include="*.py"`, `pattern="specific_term"`) to narrow results.
```

---

## Tool Parameter Updates

| Tool | Type | Add/Modify |
|------|------|------------|
| `grep_files` | string | Add `offset=0`, `limit=100` |
| `glob_files` | string | Add `offset=0`, `limit=100` |
| `list_directory` | string | Add truncation (already has implicit limits) |
| `read_file` | string | Reduce default `limit` from 2000 → 200 |
| `bash` | string | Add `max_output_chars=10000` |
| `project_list` | dict | Add `_pagination` metadata |
| `job_list` | dict | Add `_pagination` metadata |
| `queue_list` | dict | Add pagination support |
| `dlq_list` | dict | Add `_pagination` metadata |

---

## Files to Create/Modify

| File | Action | Priority |
|------|--------|----------|
| `daemon/tools/_truncate.py` | **CREATE** - New truncation utility | P0 |
| `daemon/tools/filesystem.py` | MODIFY - grep, glob, list_directory, read_file | P0 |
| `daemon/tools/bash.py` | MODIFY - bash with truncation | P0 |
| `daemon/tools/project.py` | MODIFY - project_list with pagination metadata | P1 |
| `daemon/tools/job_queue.py` | MODIFY - job_list, queue_list, dlq_list | P1 |
| `daemon/tools/__init__.py` | MODIFY - Export new utility | P1 |

---

## Implementation Order

1. **Phase 1 - Core Utility** (`_truncate.py`)
2. **Phase 2 - High-Risk String Tools** (grep, glob, bash)
3. **Phase 3 - Medium-Risk String Tools** (list_directory, read_file)
4. **Phase 4 - Dict-Returning Tools** (project_list, job_list, queue_list, dlq_list)

---

## Testing Checklist

### Unit Tests

- [ ] Content exactly at `MAX_CHARS` is NOT marked truncated
- [ ] Empty results produce no truncation hints
- [ ] Grep output remains parseable (file:line:content) after truncation
- [ ] Dict-returning tools get proper `_pagination` metadata
- [ ] Binary/bash output handled gracefully
- [ ] Pagination hint shows correct next offset (shown, not shown+1)

### Integration Tests

- [ ] `grep_files` with 500+ matches truncates correctly with hint
- [ ] `glob_files` with 500+ files truncates correctly with hint
- [ ] `read_file` on 10k+ line file shows pagination hint
- [ ] `bash` with large output truncates without breaking
- [ ] Dict tools include pagination metadata in response

### Edge Cases

- [ ] Unicode/special characters handled correctly
- [ ] Very long single line (>10k chars) truncated properly
- [ ] Mixed content types (text + binary) don't corrupt output
- [ ] Concurrent truncation calls don't interfere

---

## Open Questions

1. **413 Threshold**: Actual LLM provider limit should be confirmed to tune `DEFAULT_MAX_CHARS`
2. **Skill Implementation**: Consider implementing `simplify` skill in future for complex search use cases
3. **Streaming**: Consider streaming for very large outputs in future iteration

---

## Changelog

| Date | Change |
|------|--------|
| 2024-04-20 | Initial plan |
| 2024-04-20 | Council review: Added `list_directory`, dict-returning tools, fixed `simplify` skill reference, added `truncation_type` field, renamed `capture_limit` → `max_output_chars` |
