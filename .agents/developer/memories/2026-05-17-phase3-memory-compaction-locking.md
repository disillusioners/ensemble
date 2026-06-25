# Phase 3: Memory Compaction + File Locking

## What was implemented
- File locking via `_lock_memory_file()` using `fcntl.flock()` with separate `.lock` file
- Atomic writes via `_atomic_write_memory()` with temp file + backup + rollback + fsync
- Compaction detection and deduplication (`_compact_memory()`) — proactive at 80% threshold
- Integration into `_update_memory_md()` with full lock-read-compact-write-invalidate cycle
- Updated `agents/_inner_soul/rule.md` and `agents/_baby_template/growth.md` with compaction docs
- Fixed 500 → 2000 default discrepancy for `max_memory_words`

## Key Design Decisions
1. Lock uses separate `.lock` file (not the memory file itself) to avoid read interference
2. Compaction is proactive (triggers at 80%) and reactive (always tries at 100%)
3. `_should_compact()` is a standalone function for external use but `_update_memory_md()` does inline check to avoid redundant I/O
4. `_format_rejection()` signature was changed to `(target, max_words, word_count, rules)` — affects `tests/test_memory_system.py`
5. Cache invalidation (`manager.prompt_cache.invalidate(agent_id)`) added after successful write

## Files to NOT touch (Phase 4 territory)
- `daemon/access_memory.py`
- `daemon/loader.py`
- Archive paths (`memories/archive/`)

## Commit
- `368801b` — feat(memory): add file locking, atomic writes, and compaction detection
- 6 files changed, +1157/-95 lines
- 228 tests pass
