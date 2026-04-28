# Plan Overview: Explorer/Experiencer RAG Upgrade

## Objective
Fix Explorer agent leaking implementation details about Experiencer/KB updates, and add automatic KB update via the job queue system when Explorer discovers knowledge gaps.

## Scope Assessment
**MEDIUM** — 6 files to modify across agent definitions (5 markdown files) and tool layer (1 Python file), plus test updates. Changes are well-bounded with clear before/after states.

## Context
- Project: agents-ensemble
- Working Directory: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`

## Current Behavior (Problems)
1. **Explorer mentions experience()/experiencer**: In soul.md (line about "Upsert Knowledge", "Async upserting"), rule.md (upsert rule), workflow.md (Step 6), knowledge.md (Async Upsert Strategy), tools_note.md (rag_insert_text docs). Explorer should NOT know about persistence.
2. **Explorer does its own upserts**: Explorer directly calls `rag_insert_text` for KB updates. This is fragile — Explorer isn't designed for knowledge extraction.
3. **No auto-update mechanism**: When exploration reveals gaps, nothing automatically triggers the Experiencer to learn from the findings.

## Desired Behavior
1. Explorer outputs `## Should Update KB: true/false` in its structured response
2. Explorer NEVER mentions experiencer, experience(), KB updates, rag_insert_text, or persistence
3. `explore()` tool parses the flag and auto-creates an experiencer job via the job queue
4. KB update happens asynchronously via the job system (parallel queue)
5. Explorer returns fast — caller is never blocked by the KB update

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Clean Explorer Agent | Remove all KB update references from Explorer agent files | None | — | 30min |
| 2 | Auto KB Update via Job Queue | Add should_update_kb parsing + experiencer job creation in explore() tool | None | independent | 45min |
| 3 | Tests | Add/update tests for the new behavior | Phase 1, Phase 2 | loose | 30min |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| Phase 1 ↔ Phase 2 | **independent** | Agent files (markdown) and tool code (Python) are separate. Phase 2 only needs to know the output format, not the agent files. |
| Phase 1+2 ↔ Phase 3 | **loose** | Tests verify the behavior defined in Phase 1+2 but don't share code. |

**Recommendation**: Phase 1 and Phase 2 can run in **parallel**. Phase 3 after both complete.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Explorer LLM doesn't reliably output `## Should Update KB` | medium | Use regex pattern with case-insensitive matching; default to false if not found |
| Experiencer job fails silently | low | Fire-and-forget with logging; experiencer already has error handling |
| Parallel queue doesn't exist for project | low | Fall back to FIFO queue; log warning |
| Job queue service not available on manager | low | Guard with `getattr` check; skip silently with warning log |

## Success Criteria
- [ ] Explorer agent files contain zero references to: experience(), experiencer, rag_insert_text, "Upsert", "KB update"
- [ ] Explorer output format includes `## Should Update KB: true/false`
- [ ] explore() tool parses the flag and creates experiencer job when true
- [ ] explore() tool returns the Explorer response unchanged (no delay for KB update)
- [ ] All existing tests pass
- [ ] New tests cover: flag parsing, job enqueue on true, no job on false, graceful failure

## Tracking
- Created: 2026-04-24
- Last Updated: 2026-04-24
- Status: draft
