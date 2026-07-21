# LESSON: Filesystem workdir-error coverage (2026-07-21)

## Context
Verified the workdir-existence error reporting change in `_resolve_target_path` (`daemon/tools/filesystem.py`, commit `19b8fd40`, branch `feature/file-read-workdir-error`).

## What was verified
The fix adds a `base.exists()` check on the workdir before resolving relative paths. When the workdir doesn't exist, it returns a specific error:
> `ERROR: Working directory does not exist: {workdir} — check the workdir path. Was it typed correctly?`

instead of a misleading `"File does not exist"`. This helps LLM agents spot typos in their workdir paths (the real-world bug: an agent typed `/Users/ngienminhkha/...` instead of `/Users/nguyenminhkha/...`).

## Coverage strengths (keep these patterns)
1. **Negative-control regression guard** — `test_relative_path_nonexistent_workdir_does_not_fall_through_to_file_error` explicitly asserts the OLD misleading message does NOT appear. This is the key regression test — keep it.
2. **Explicit "NOT in result" assertion** — `test_read_file_valid_workdir_missing_file_keeps_existing_message` asserts `"Working directory does not exist" not in result`, ensuring a valid-workdir-missing-file case still gives the normal file error (not the workdir error). Good disambiguation guard.
3. **Typo verbatim echo** — the typo-username test asserts the original (typo'd) workdir string is echoed back in the error. This is the feature's core value: the agent sees its own typo.
4. **Parametrized across all 6 tools** — `test_filesystem_absolute_path.py:233-251` parametrizes the absolute-path-ignores-workdir behavior across write_file/read_file/edit_file/list_directory/glob_files/grep_files. Good breadth.

## Known coverage gap (pre-existing, deferred — NOT from this change)
Per project knowledge: the workdir-existence check uses `base.exists()` rather than `base.is_dir()`. If workdir points to a **regular file**, `exists()` returns True and the check passes, causing a confusing downstream "Not a file" error instead of a workdir-specific message. Deferred fixes: (1) switch `exists()` → `is_dir()`, (2) wrap `.exists()` in try/except for network-FS OSError robustness. Not blocking for this change.

## Packs created
- `test/packs/filesystem_resolver_unit_test.sh` — resolver-level unit tests (31 tests)
- `test/packs/filesystem_tools_unit_test.sh` — end-to-end tool tests (38 tests)

Both registered in PACKS.md. Commit `730f7952`.
