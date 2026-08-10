# Quick Fixes: shared_context → shared_meta_kv Rename

**Date**: 2026-08-10
**Commits**: `55b663ed`, `8cbc03d9`
**Branch**: `feature/shared-meta-kv-rename`

## Root Cause
A mechanical rename (`shared_context` → `shared_meta_kv`) was applied across 67 files via IDE tooling. The rename touched source code, meta.json files, and test files, but **missed several locations**:

1. **Test assertion files** (`test_wanderer_agent.py`, `test_gaia_agent.py`) — hardcoded `"shared_context"` in tool allow-list assertions. IDE rename doesn't catch string literals in assertions.
2. **Pack scripts** (6 `.sh` files in `test/packs/`) — reference old test file names (`test_shared_context_*.py`) and old tool category string (`"shared_context"`). Pack scripts are shell files, not Python — IDE rename tools typically don't scan them.
3. **`agents/doc-maintainer/meta.json`** — this agent directory was missed entirely by the rename (not in the original 30-agent sweep). It still had `"shared_context_metadata"` in its tools.allow.

## Fixes Applied

### Commit `55b663ed` (8 files)
- Updated 2 test files: `shared_context` → `shared_meta_kv` in assertion strings
- Updated 6 pack scripts: test file paths + tool category string check + comments

### Commit `8cbc03d9` (1 file)
- Updated `agents/doc-maintainer/meta.json`: `shared_context_metadata` → `shared_meta_kv`

## Lesson
**Mechanical IDE renames miss non-Python files and string literals.** When doing a large rename:
1. Always grep ALL file types (`.sh`, `.json`, `.md`) for old names
2. Check test assertion files for hardcoded string references
3. Verify the agent directory list is complete (the rename missed `doc-maintainer`)
4. Update pack scripts manually — they reference test file paths as strings

## Verification
- All fixes verified: 125 shared_meta_kv unit tests PASS, 22-agent tool filter audit PASS, 160 service/persistence tests PASS, 710 core regression PASS (0 NEW failures)
