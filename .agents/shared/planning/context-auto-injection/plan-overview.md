# Plan Overview: Explorer Context Auto-Injection

## Objective
Eliminate the Explorer agent's context-skipping behavior by auto-injecting matched context file content at the system layer (before the agent receives the query), so the agent always has relevant prior results without needing to manually read files.

## Scope Assessment
**MEDIUM** — 3 files modified + 1 new module + 1 new test file. A new standalone service module (`daemon/services/context_injection.py`) provides a clean public API (~150 lines). Agent prompt changes are straightforward format additions. Well-bounded with clear interfaces.

**Justification:** The change spans two domains (Python infrastructure + agent prompts) but the interface is clean: a reusable `get_shared_context()` function that any caller (explore tool, MCP server, external integrations) can use, and the explorer prompt gets a new `## Concise` section. No database changes, no multi-module architectural shifts.

## Context
- Project: agents-ensemble
- Working Directory: /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
- Key insight: Current slug format is query-derived (e.g., `maintenance-service-checkpoint-cleanup-background-task-patterns_20260531_231255.md`)

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Infrastructure: Context Injection Service | Build `daemon/services/context_injection.py` with public API `get_shared_context(context_key, query)` and private matching/injection helpers | None | — | 3h |
| 2 | Explorer Prompt: Concise Section | Add `## Concise` section to explorer response format and update `_save_explorer_result` to validate it | Phase 1 (needs format contract) | loose | 1h |
| 3 | Integration & Tests | Wire `get_shared_context()` into `explore()` function, write tests for the public API + unit tests for internal helpers | Phase 1, Phase 2 | tight | 3h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| Phase 1 → Phase 2 | **loose** | Phase 2 only needs to know the concise section format (a markdown heading). Phase 1's matching logic doesn't depend on Phase 2. |
| Phase 1 + 2 → Phase 3 | **tight** | Phase 3 wires Phase 1's injection into the explore() call site AND verifies Phase 2's concise format is preserved end-to-end. Needs both complete. |

### Scheduling Recommendation
- Phase 1 and Phase 2 can run **in parallel** (loose coupling).
- Phase 3 runs **after both complete** (tight coupling).

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Slug tokenization misses relevant files (false negatives) | Medium — injection is best-effort, agent still has RAG | Recall-oriented scoring avoids penalizing short queries; log match scores for debugging |
| Large context directory (100+ files) causes slow injection | Medium — adds latency before agent spawn | Cap file scanning at 50 most recent; `asyncio.to_thread()` prevents event loop blocking |
| Concise section missing from old files | Low — old files predate format change | Graceful fallback: use first sentence of Answer section |
| Token budget exceeded by injection content | Medium — could overflow context window | Global injection token cap of 2000 tokens with proportional reduction; file index exempted from cap |
| Match score thresholds miscalibrated | Medium — too aggressive or too conservative | Start conservative (80/60/40 thresholds); add logging for tuning; asymmetric scoring reduces false negatives on short queries |
| Sync I/O blocks event loop | High — freezes all async operations | `asyncio.to_thread()` runs `get_shared_context` on thread pool |

## Success Criteria
- [ ] Explorer agent responses include `## Concise` section with 1-3 sentence summary
- [ ] New `daemon/services/context_injection.py` module with public `get_shared_context(context_key, query) -> str | None`
- [ ] `explore()` tool calls `get_shared_context()` via `asyncio.to_thread()` — no injection logic in knowledge_tools.py
- [ ] Context files are auto-matched via recall-oriented scoring on filename slugs
- [ ] Short queries (2 tokens) correctly match long slugs (no false negatives from symmetric Jaccard)
- [ ] Tiered injection works: high → Answer truncated, medium → Concise, low → first sentence
- [ ] Global injection token cap of 2000 enforced with proportional reduction
- [ ] File index appended to all explorer messages when context dir has files
- [ ] Empty context dir produces no injection (no errors, no delay)
- [ ] `context_key=None` edge case handled gracefully (no injection, no errors)
- [ ] Sync I/O runs on thread pool (`asyncio.to_thread`) — never blocks event loop
- [ ] `_save_explorer_result()` preserves the new Concise section in saved files
- [ ] All existing tests pass unchanged
- [ ] New utility functions have >90% test coverage

## Tracking
- Created: 2026-06-01
- Last Updated: 2026-05-31
- Status: revised (extracted reusable service module + 4 critical + 6 should-fix reviewer notes)
