# Lesson: GitDiffService file_not_found Bug (Workspace Viewer)

Date: 2026-07-22
Branch: feature/workspace-viewer
Commit: a690aa59
Found by: workspace-web-automation worker (ed53b32a)

## Bug Description

`GitDiffService.get_file_diff()` returned `has_changes: true` with empty diff and null content for **non-existent files**. The UI would show a misleading "changes detected" state for files that don't exist.

## Root Cause

Line 78 of `daemon/services/git_diff_service.py`:
```python
has_changes = bool(diff_text.strip()) or head_content is None
```

A file that doesn't exist in the HEAD commit gives `head_content=None`. This caused `has_changes` to be `True` even when the file doesn't exist on disk either — the condition couldn't distinguish between:
1. **New untracked file** (head_content=None, file exists on disk → correctly has_changes=True)
2. **Non-existent file** (head_content=None, file does NOT exist on disk → should be error, not has_changes=True)

## Fix

Added a file-existence check after the working-content read:
- If `head_content is None AND not file_exists` → return `error: "file_not_found"` with `has_changes: false`
- This preserves correct behavior for genuinely new (untracked) files.

## Regression Test

Added `test_diff_nonexistent_file_returns_file_not_found` to `tests/test_workspace_api.py` → verifies the error response for non-existent file paths.

## Takeaway

When a boolean condition combines multiple signals (`diff_text` OR `head_content is None`), ensure each signal path is independently valid. The `head_content is None` path conflated two distinct states (new file vs non-existent file) that needed separation.
