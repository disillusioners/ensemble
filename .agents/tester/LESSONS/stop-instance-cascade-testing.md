# Stop Instance Cascade — Testing Notes

## Date: 2026-05-15

## Key Findings

### Feature Design
- Stop cascade uses DFS (depth-first) traversal via `_visited` set
- Soft stop: sets status to `IDLE`, not terminated — instances remain resumable
- Depth limit: 256 levels
- Circular reference detection via shared `_visited` set across recursion

### Edge Case Coverage
All major edge cases covered by existing tests:
- Circular reference (self-referential)
- Exception during child stop (siblings continue)
- Depth limit exceeded (graceful skip)
- Already-idle instances (no-op)
- Mixed status children (running stopped, idle skipped)

### Minor Gaps (Low Risk)
1. **Mutual circular reference** (A→B, B→A) — only self-circular (A→A) is tested
2. **Database error during update_status** — no try/except around update_status in real code at `instance_lifecycle.py:431-434`
3. **Race condition** — instance deleted during cascade, no re-fetch after children processed

### Mock Accuracy
All mocks accurately reflect real interfaces. The `_enrich_instance` converts children from JSON to Python list, and tests correctly mock children as lists.
