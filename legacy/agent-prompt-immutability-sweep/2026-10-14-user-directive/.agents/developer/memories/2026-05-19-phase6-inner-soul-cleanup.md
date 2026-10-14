# Phase 6: `_inner_soul/` Cleanup — Audit and Document

## Summary
Audited all references to `agents/_inner_soul/` across the codebase, cleaned up stale references, and added comprehensive documentation.

## Key Findings from Audit
- **3 files exist**: `soul.md`, `rule.md`, `workflow.md` (NO `tools_note.md`)
- **No `meta.json`** → directory is invisible to agent discovery (`daemon/registry.py`)
- **Files NEVER loaded at runtime** — no code path loads them
- The `create_inner_soul_tool` function in `daemon/tools/inner_soul.py` is unrelated to the directory

## Changes Made
1. **`daemon/tools/agent_mother.py`**: Removed `_inner_soul` from protection tuples (lines ~295, ~352). Now only `_mother` is accessible.
2. **`agents/_mother/workflow.md`**: Removed "Modifying Inner Soul" section
3. **`agents/_mother/soul.md`**: Removed "Modify Inner Soul" capability
4. **`docs/agent-architecture.md`**: Updated section to accurately describe `_inner_soul/` as NOT a real agent
5. **`agents/_inner_soul/README.md`**: NEW file explaining purpose, why no meta.json, file listing, history

## Tests: 1457 unit + 44 agent API — all pass
## Commit: 426e6b8
