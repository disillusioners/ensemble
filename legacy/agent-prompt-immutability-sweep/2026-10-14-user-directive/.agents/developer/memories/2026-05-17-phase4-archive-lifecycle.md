# Phase 4: Archive Lifecycle & Visibility

## What was implemented
- Archive path support in `access_memory()` with `ARCHIVE_PATTERN` regex validation (`YYYY/MM/<safe_name>.md`)
- `load_recent_memories(include_archived=True, archive_limit=5)` for listing archived files with prefix
- `_archive_memory_file()` — moves files to `memories/archive/YYYY/MM/` with collision handling
- `_archive_old_memories(agent_path, ttl_days=90)` — archives files older than TTL using mtime
- `memory_archive_ttl_days` parsing in `_load_growth_rules()` with default 90
- Archival integrated into both `_update_memory_md()` compaction flow and `create_inner_soul_tool()` entry
- Updated `growth.md` and `rule.md` with archive documentation + token budget analysis

## Key Security Design
- `ARCHIVE_PATTERN` regex: `r'^(\d{4})/(\d{2})/[a-zA-Z0-9_\-]+\.md$'` — blocks path traversal
- Invalid archive paths fall back to filename sanitization
- `resolve()` + `startswith()` check still applies for archive paths
- Symlinks filtered out in both access and listing

## Key Architecture
- Archival runs on every inner_soul call (lightweight TTL check + mtime scan)
- TTL=0 disables archiving entirely
- Archive failures are non-fatal (logged but don't block writes)
- Archived files in loader output: `archive/2026/01/file.md` format
- Token budget: 5 active (~30 tokens) + 5 archived (~115 tokens total) < 1.5% of 8k

## Files Changed
- `daemon/tools/access_memory.py` — Archive path routing
- `daemon/loader.py` — include_archived parameter
- `daemon/tools/inner_soul.py` — Archive functions + TTL parsing + integration
- `agents/_baby_template/growth.md` — Archive docs
- `agents/_inner_soul/rule.md` — Archive rules
- `tests/unit/tools/test_archive_lifecycle.py` — 31 new tests

## Commit
- `4609e9a` — feat(memory): add archive lifecycle with safe path handling
- 8 files changed, +1389/-8 lines
- 326 tests pass (31 new + 295 existing)
- 2 pre-existing test pollution failures (gaia tests) unrelated to our changes
