# Plan Overview: Memory System Improvements

## Objective
Make the agent memory system actually useful by fixing the write-only `memories/` problem: improve file naming, surface recent memories in prompts, add a read tool, and increase the `memory.md` word limit.

## Scope Assessment
**Small-Medium** — 4 focused changes across 3-4 files, all in `daemon/`. No architectural changes, no new dependencies. Estimated 2-3 hours of coding.

## Context
- Project: agents-ensemble
- Working Directory: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- Requested by: User
- Key finding: `memories/` files are created by `inner_soul` but **never read back** — they are write-only ghost files

## Current State (Quick Reference)

| File | Role | Key Functions |
|------|------|---------------|
| `daemon/tools/inner_soul.py` | Creates memory files | `_update_memories()` (L336-374), `_slugify()` (L602-607), `_load_growth_rules()` (L573-599) |
| `daemon/loader.py` | Loads prompts into system prompt | `compose_system_prompt()` (L84-174), `load_and_cache_prompt()` (L235-295) |
| `daemon/tools/session.py` | Tool wiring | `create_session_tools()` (L42-206) |
| `daemon/tools/__init__.py` | Tool exports | Import/export registry |

## Phase Index

| Phase | Name | Objective | Dependencies | Est. Time |
|-------|------|-----------|-------------|-----------|
| 1 | Better file naming + word limit | Rename memory files to human-readable format, increase default word limit | None | 45min |
| 2 | Recent memories in system prompt | List 5 most recent memory filenames in agent's system prompt | Phase 1 (naming) | 30min |
| 3 | `access_memory` tool | New read-only tool to retrieve specific memory file content | Phase 2 (agent sees filenames) | 45min |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Filename format change breaks existing memories | Low | Old filenames still work; new format only for new files |
| Cache invalidation missing for `memories/` (existing bug) | Medium | Fix in Phase 2: add mtime tracking for `memories/*.md` |
| Token budget increase from recent memories section | Low | Only filenames (not content), max 5 entries — minimal tokens |
| Word limit increase (500→2000) bloats prompts | Low | `memory.md` is trimmed by LLM during writes; only affects existing large files |

## Success Criteria
- [ ] New memory files use `{datetime}-{description}.md` naming
- [ ] System prompt includes "## Recent Memories" section with up to 5 filenames
- [ ] `access_memory` tool reads and returns content of a specific memory file
- [ ] Default `memory.md` word limit increased from 500 to 2000
- [ ] Cache properly invalidates when `memories/` directory changes
- [ ] All existing tests pass

## Clarification Needed
- **Word limit**: The request says "increase to 200" but 200 < 500. Assuming **2000** was intended. If user confirms 200, adjust accordingly.

## Tracking
- Created: 2026-03-29
- Last Updated: 2026-03-29
- Status: complete
