# Plan Overview: Context Injection Restructure (v3 — Final)

## Objective
Restructure context injection so ALL context (project info, shared context files, skills) goes as `[SYSTEM CONTEXT: ...]` tagged HumanMessages BEFORE the real user request, instead of being baked into the system prompt or prepended to user message body. Base system prompt keeps only agent persona content.

## Scope Assessment
**LARGE** — Touches 4 core files (`instance_lifecycle.py`, `instance_messaging.py`, `graph.py`, `persistence.py`), 2 supporting files (`skill_injection_service.py`, `manager.py`), creates 1 new service module, requires DB migration, and affects ~15+ test files. Multi-day effort across 7 phases.

## REVISED Architecture (per reviewer C1/C2/C3)

**Key change from v1**: Context messages are NOT injected via `_build_graph_input()`. Instead, they follow the existing RAM-queue pattern — assembled INSIDE `agent_node` (async) and extended into the LOCAL `full_messages` variable. This gives **true ephemerality** without any filter.

```
agent_node(state, config):
    messages = state['messages']                                      # checkpoint state
    full_messages = [SystemMessage(PERSONA)] + list(messages)         # LOCAL variable
    
    # ── Context injection (NEW) ──
    context_msgs = await assemble_context_messages(...)               # async, per-turn
    full_messages = [SystemMessage] + context_msgs + full_messages    # inject LOCALLY
    
    # ── RAM-queue injections (EXISTING, unchanged) ──
    full_messages.extend(injected_msgs)                               # line 2023
    
    # ── Report injections (EXISTING, unchanged) ──
    full_messages.append(report_msg)                                  # line 2160
    
    # ── LLM call ──
    response = llm.invoke(full_messages)
    
    # ── Return (checkpoint) ──
    return {'messages': [response]}                                   # context NOT persisted
```

Context messages never enter graph input, never enter state, never enter checkpoint. No filter needed.

## Desired Final Format

```
1. SystemMessage — base system prompt (soul.md, rules, tools, workflow, memory + prompt-injection defense instruction)
2. HumanMessage [SYSTEM CONTEXT: Related Project] — project JSON + shared context metadata + critical notes + recent history
3. HumanMessage [SYSTEM CONTEXT: Shared Context] — injected files matched to user request
4. HumanMessage [SYSTEM CONTEXT: Skills] — matched/injected skills
5. ... prior conversation history (user/ai messages from checkpoint) ...
6. (any existing RAM-queue / report injections — appended to full_messages)
7. HumanMessage — the REAL user request for THIS turn (nothing prepended)
```

Each system-injected HumanMessage has prefix: `[SYSTEM CONTEXT: {{title}}]\n\n<content>`

## Out of Scope: Opencode Path

**Constraint: The opencode tool's context injection is OUT OF SCOPE. Do NOT modify it.**

The ensemble has two distinct code paths for context injection:

| Path | Mechanism | Format | This Refactor? |
|------|-----------|--------|----------------|
| **Ensemble agent path** (`agent_node` in `graph.py`) | System prompt appenders + string prepending + skill injection | Being restructured to `[SYSTEM CONTEXT: ...]` HumanMessages | ✅ **YES** |
| **Opencode tool path** (`external_opencode_send_message`) | `related_context_keywords` → auto-prepends matched context files into ONE user message | Single merged user message | ❌ **NO — leave unchanged** |

The opencode API only supports sending one user message, so merging all context into a single message is the correct and intentional approach there. This refactor applies **exclusively** to the ensemble agent path (`agent_node` / `graph.py` / `instance_messaging.py` / `instance_lifecycle.py` / `persistence.py`).

Any code that calls `external_opencode_send_message` (planner, reviewer Deep-Review, etc.) must NOT be modified. The `related_context_keywords` parameter and its single-message merging behavior stay as-is.

## Architectural Decision Records (Summary)

| ADR | Decision | Status |
|-----|----------|--------|
| **ADR-1** | Per-turn ephemeral HumanMessages | Accepted |
| **ADR-2** | **REVISED**: Build context INSIDE `agent_node` (local `full_messages`), NOT in `_build_graph_input` | Accepted (per C1) |
| **ADR-3** | Unify skill injection into ContextMessageBuilder | Accepted |
| **ADR-4** | Format: `[SYSTEM CONTEXT: {{title}}]\n\n<content>` | Accepted |
| **ADR-5** | `additional_kwargs` with `context_kind` enum (`project`, `shared_context`, `skills`) | Accepted (per S1) |
| **ADR-6** | ~~Filter at `agent_node` return~~ — **REMOVED** (unnecessary per ADR-2) | Removed (per C1) |
| **ADR-7** | Drop XML fences + ADD prompt-injection defense instruction to system prompt | Accepted (per W2) |
| **ADR-8** | Per-agent feature flag, **TWO modes only** (`system_prompt`, `human_messages`). No `BOTH`. | Accepted (per W1) |
| **ADR-9** | `format_project_context()` deprecated end of Phase 3 | Accepted |
| **ADR-10** | Preserve `<meta>` skill tag with REPLACE semantics | Accepted |
| **ADR-11** | KV metadata merges into `[SYSTEM CONTEXT: Related Project]` message | Accepted |
| **ADR-12** | Context assembly is async inside `agent_node`. `_build_graph_input` stays sync/unchanged. | Accepted (per C2) |
| **ADR-13** | Opencode tool path (`related_context_keywords` single-message merge) is OUT OF SCOPE — must NOT be modified | Accepted (per user constraint) |

See `decisions.md` for full rationale.

## Phase Index (v2 — 7 phases, was 8)

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | ContextMessageBuilder Foundation | Create pure builder functions for all 3 context kinds | None | — | 4-6h |
| 2 | System Prompt Appender Dormancy | Make context appenders skippable + add prompt-injection defense instruction | Phase 1 (loose) | tight | 3-5h |
| 3 | Inject Context into `agent_node` | Assemble context inside `agent_node`, remove string prepending, unify skill injection. **Fixes B1 (loop-breaker), B2 (manager indirection), B3 (retry-safe skills)** | Phase 2 (tight) | tight | 7-9h |
| 4 | GET /messages API Integration | Surface context messages as synthetic in API response | Phase 3 (loose) | loose | 3-4h |
| 5 | Per-Turn Freshness | Verify context is fresh each turn (no stale caches) | Phase 3 (loose) | loose | 2-3h |
| 6 | Backward Compat & Testing | Feature flag, migration, full test matrix | Phases 1-5 (tight) | tight | 6-8h |

### Changes from v1

| v1 Phase | v2 Status | Reason |
|----------|-----------|--------|
| Phase 1 (Builder) | **Phase 1** (unchanged) | — |
| Phase 2 (Appender Dormancy) | **Phase 2** (adds prompt-injection defense per W2) | — |
| Phase 3 (Wire into Graph Input) | **Phase 3** (REWRITTEN: inject into `agent_node` local, not `_build_graph_input`) | C1/C2 |
| Phase 4 (Ephemerality & Checkpoint Filter) | **DELETED** — unnecessary per ADR-2 | C1 |
| Phase 5 (GET /messages API) | **Phase 4** (renumbered) | — |
| Phase 6 (Compaction Survival) | **DELETED** — unnecessary per ADR-2 | C1/C3 |
| Phase 7 (Per-Turn Freshness) | **Phase 5** (renumbered) | — |
| Phase 8 (Testing) | **Phase 6** (expanded per S2) | S2 |

### Coupling Assessment

| Phase Pair | Coupling | Scheduling |
|------------|----------|------------|
| 1 → 2 | loose | Sequential but can pipeline |
| 2 → 3 | tight (Phase 3 needs mode flag) | Must run sequential |
| 3 → {4, 5} | loose for both | Phases 4, 5 can run parallel |
| {4, 5} → 6 | tight (all must complete) | Must run sequential |

**Parallelization opportunity**: Phases 4 and 5 can run in parallel after Phase 3 (different files: persistence.py / instance_lifecycle.py).

## Risk Register

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|------------|
| **B1: Loop-breaker drops context** | High (silent context loss) | Confirmed | Re-append `context_msgs` after `_maybe_repair_loop` rebuild — object identity check, parallel to report-msg re-append at graph.py:2284-2287. Phase 3 Task 6. |
| **B2: ContextSlot can't reach messaging path** | High (skill results unreachable) | Confirmed | Mirror `InjectionSlot` manager-indirection pattern: `manager._context_skill_results` dict. Phase 3 Task 2 + 11. |
| **B3: Skills lost on retry** | High (silent skill loss) | Confirmed | `assemble_context_messages()` reads stored result from manager, or re-runs search if missing. Phase 3 Task 7. |
| `agent_node` closure parameter growth (needs manager, repos, agent_meta) | Medium | Medium | Create `ContextSlot` class (like `InjectionSlot`) to encapsulate dependencies. |
| Compaction re-append gap (C3 analog for context) | Medium | Low | Explicitly re-append context_msgs to `compact_messages` after compaction. Phase 3 Task 8. |
| `get_shared_context()` + skill search latency exceeds 50ms | Medium | Medium | Cache KV per-turn; add timeout fallback; benchmark in Phase 6. |
| Existing tests assume "frozen at spawn" context semantics | Medium | High | ~15+ test files affected. Budget 6-8 hours for test updates (per S2). |
| `append_auto_load_skills` DB write side-effect on GET /messages poll | High | Confirmed | Phase 4 eliminates this by moving context build off `_apply_post_cache_appends`. |
| GET /messages skill search latency on every poll | Medium | Medium | Consider caching (reviewer note #2). Phase 4 Task 7 benchmarks. |

## Test Blast Radius (expanded per S2)

Tests touching appender output format, LLM input/response shape, message construction, and persistence.

### Directly Affected (assert on appender/format behavior)
| File | Tests | What it tests |
|------|-------|---------------|
| `tests/unit/test_auto_load_skills.py` | ~19 | `append_auto_load_skills` output format |
| `tests/unit/test_shared_context_injection.py` | ~14 | `append_shared_context_metadata` output |
| `tests/unit/test_shared_context_prompt_injection.py` | ~4 | Prompt-injection defense in metadata appender |
| `tests/unit/test_shared_context_message_body_injection.py` | ~10 | Message-body KV injection (will be removed) |
| `tests/unit/test_context_key.py` | 8 | `append_context_key` (PERSONA — stays, minimal) |
| `tests/unit/test_auto_load_metrics.py` | ~5 | `append_auto_load_skills` tracking |
| `daemon/tests/test_inject_allowed_models.py` | 4 | `append_allowed_models` (PERSONA — stays) |
| `tests/test_language_check.py` | ~10 | `append_user_language` (PERSONA — stays) |

### Integration / E2E Affected (LLM input/response shape)
| File | Tests | What it tests |
|------|-------|---------------|
| `tests/integration/test_shared_context_e2e.py` | ~5 | `append_shared_context_metadata` in child context |
| `tests/services/test_instance_messaging_shared_context_injection.py` | ? | Message-body shared context injection |
| `tests/services/test_instance_lifecycle_h10_l14.py` | ? | `append_context_key` in spawn flow |
| `tests/services/test_context_usage_emission.py` | 14 | Context usage tracking |
| `tests/tools/test_send_message_load_skill.py` | 6 | Skill injection via send_message |
| `tests/unit/test_coder_developer_migration.py` | ? | Patches `append_context_key` |
| `tests/integration/test_skill_evolution_e2e.py` | 24 | Skill lifecycle (touches injection) |
| `tests/integration/test_skill_cross_phase_flow_c.py` | 12 | Skill cross-phase flow |
| `tests/services/test_option_b_message_branching.py` | 15 | Message branching (touches injection) |

**Estimated test update budget: 6-8 hours** (per S2)

## Success Criteria
- [ ] LLM receives messages in order: SystemMessage → [SYSTEM CONTEXT] messages → (RAM/report injections) → user request → history
- [ ] System prompt contains ONLY persona content + prompt-injection defense instruction (no project data, no files, no skills)
- [ ] Context messages are fresh each turn (per-turn rebuild inside `agent_node`)
- [ ] Context messages NEVER in LangGraph checkpoint DB (local `full_messages` only)
- [ ] GET /messages shows context messages as synthetic/identifiable
- [ ] Compaction retry correctly re-appends context messages to `compact_messages`
- [ ] All existing tests pass in `system_prompt` mode (backward compat)
- [ ] Tests pass on both SQLite and PostgreSQL
- [ ] Skill injection works via both auto-search and `<meta>` explicit tag

## Tracking
- Created: 2026-07-28
- Last Updated: 2026-07-28 (v3 — per reviewer B1/B2/B3 + non-blocking notes)
- Status: draft (v3 — final iteration)
