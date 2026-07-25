# Plan Overview: Governor — Council-Manager Agent

> **Revision 3 (2026-07-25):** D9 revised — degraded quorum (1 result → degraded notice) + tiered deadlines (30min soft / 1h hard cap with governor extension). New D9 tests + E2E scenarios added.
> **Revision 2 (2026-07-25):** All 6 critical review issues verified against source code. Phase structure revised to foundation-first. C1-C6 risks + mitigations added. D4 revised (5→4 councilors). New decisions D7-D10 added.

## Objective

Create a new **governor** agent that acts as a council-manager: it spawns multiple instances of ONE agent_id (the `councilor_agent_id`), each with a DIFFERENT LLM model, forwards the same user request to each, then collects, aggregates, and refines their outputs to produce a high-confidence, high-correctness final answer.

This is a **PLANNING ONLY** deliverable. No code is written — these are structured specs ready for an implementation agent to execute.

## Scope Assessment

**LARGE** — justified by:
- Touches 4 distinct subsystems: agent definitions (`agents/governor/`), tools (`daemon/tools/instance.py`), instance lifecycle (`daemon/services/instance_lifecycle.py` appender chain), and config (`daemon/registry.py` AgentMetadata + loader).
- Introduces 1 new agent (5 markdown files), 2 new tools (`spawn_councilor`, `clear_councilor_errors`), 1 new appender (`append_allowed_models`), 2 new meta.json flags (`inject_allowed_models`), and 1 new tool category registration.
- Requires solving the **error-propagation problem** (C1) — the dependency bus's sticky `_parent_errored` flag would force the governor to ERROR on any child failure, invalidating the council's fault-tolerance.
- Requires careful design of aggregation workflow (soul.md/workflow.md/rule.md).

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`

## Architecture Summary (Verified by Source Exploration — Rev 2)

| Subsystem | Extension Point | How Governor Uses It | C-Fix |
|-----------|-----------------|----------------------|-------|
| Agent definition | `agents/governor/` dir with `meta.json` + 4 `.md` files | New directory, standard structure | — |
| Tool definition | Inside `create_instance_tools()` (`instance.py:679-1314`) | `spawn_councilor` + `clear_councilor_errors` defined as closures | **C5** |
| Tool registration | `@register_tool_category("council")` + `@tool` decorator; add to `CATEGORY_MODULES` | Category enables `tools.allow: ["council"]` filtering | — |
| Model validation | `InstanceLifecycleService._resolve_model_override()` | Reused; returns `None` on invalid → tool RAISES | **C3,C4** |
| Context injection | `_apply_post_cache_appends()` chain in `instance_lifecycle.py:771-841` | New `append_allowed_models` appender | **C2** |
| Config access | `manager.config.llm.allowed_models` (NO underscore) | Read by appender + tool | **C2** |
| AgentMetadata | `daemon/registry.py:138` (`extra="ignore"`) + loader `254-272` | New field + loader line BOTH needed | **C6** |
| Error propagation | `dependency_bus.py:418` `_parent_errored` (sticky) | Mitigated by `clear_councilor_errors` tool | **C1** |

## Phase Index (Foundation-First Scheduling — REVISED)

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| **0** | **Foundation: Tool Contract + Metadata Schema** | Freeze `spawn_councilor` signature, `SpawnCouncilorInput`, `inject_allowed_models` field + loader, `clear_councilor_errors` signature. Lock interfaces so Phases 1-3 can parallelize. | None | — (root) | 1-1.5h |
| 1 | Governor Agent Definition | Create `agents/governor/` with meta.json + soul/rule/workflow/tools_note | Phase 0 (contracts) | loose | 2-3h |
| 2 | `spawn_councilor` + `clear_councilor_errors` Tools | Implement both tools inside `create_instance_tools()`, strict validation, error-clear | Phase 0 (contracts) | loose | 3-4h |
| 3 | Models List Injection | `append_allowed_models` appender + `inject_allowed_models` field + loader | Phase 0 (contracts) | loose | 1.5-2h |
| 4 | Integration, Wiring & Testing | Wire meta.json flags, E2E verification, C1-C6 test scenarios | Phases 1, 2, 3 | tight | 2.5-3.5h |

**Total estimated time: 10-14h** (sequential). With foundation-first + parallel Phases 1-3: ~5h wall-clock + Phase 4.

### Why Foundation-First (Per Suggestion #3)

Phase 2 and Phase 3 both touch `daemon/services/instance_lifecycle.py` (appender chain) and `daemon/registry.py` (metadata schema). If they run fully in parallel without a frozen contract, they risk merge conflicts on:
- The `AgentMetadata` field additions
- The `_apply_post_cache_appends` chain insertion point
- The `create_instance_tools()` closure additions

**Phase 0 freezes the contracts** (signatures, field names, chain positions) in a short spec. Then Phases 1-3 implement against frozen interfaces with no cross-talk.

### Coupling Assessment

| Phase Pair | Coupling | Reasoning |
|------------|----------|-----------|
| 0 → {1,2,3} | **loose** | Phase 0 defines interfaces; others implement against them |
| 1 ↔ 2 | **loose** | Phase 1's `meta.json` references `tools.allow: ["council"]` — contract from Phase 0 |
| 1 ↔ 3 | **loose** | Phase 1's `meta.json` sets `inject_allowed_models: true` — contract from Phase 0 |
| 2 ↔ 3 | **loose** | Both touch `instance_lifecycle.py` but at different locations (tool factory vs appender chain). Phase 0 freezes the insertion points. |
| 4 ↔ {1,2,3} | **tight** | Phase 4 verifies end-to-end. Must wait for all three. |

## Key Design Decisions (see decisions.md for full rationale)

| Decision | Choice | Revision |
|----------|--------|----------|
| **D1**: Tool approach | New `spawn_councilor` tool (Option B) | C5: defined INSIDE `create_instance_tools()` |
| **D2**: Aggregation | LLM-synthesize with leader-picks-best | + runtime counter (Phase 4 hardening) |
| **D3**: Models injection | New `append_allowed_models` appender | C2: `manager.config` (no underscore); C6: field + loader both needed |
| **D4**: Councilor count | **Max 4** (was 5) | W3: WorkerPool=4 |
| **D5**: Iteration cap | Max 2 refinement rounds | Unchanged |
| **D6**: Self-containment | Intelligence in markdown | Unchanged |
| **D7**: Error propagation ✨NEW | `clear_councilor_errors` tool clears sticky flag before delivery | C1 |
| **D8**: Crash recovery ✨NEW | Council manifest in `shared_context_metadata` | W4 |
| **D9**: Quorum + tiered deadline ✨(Rev 3) | Degraded quorum (0→fail, 1→degraded notice, 2+→normal). Tiered deadlines: 30min soft (governor extension) / 1h hard cap (terminal) | W5 |
| **D10**: Model canonicalization ✨NEW | Normalize to canonical name before dedup | W7 |

## Risks & Mitigations (REVISED with C1-C6)

| Risk | Impact | Mitigation | Source |
|------|--------|------------|--------|
| **C1**: Sticky `_parent_errored` forces governor ERROR on any child failure | **CRITICAL** — invalidates fault-tolerance | `clear_councilor_errors` tool (D7) clears flag before delivery on successful synthesis | Verified: dependency_bus.py:418, job_feedback_observer.py:148-151 |
| **C2**: `manager._config` doesn't exist → appender silently never fires | **HIGH** — governor never sees models | Use `manager.config` (no underscore) at manager.py:481 | Verified |
| **C3**: `_check_team_membership` returns str, doesn't raise → gate bypassed | **HIGH** — any agent_id passes team check | Check return value: `if err is not None: raise ValueError(err)` | Verified: instance.py:248-322 |
| **C4**: `resolve_to_id` returns None, doesn't raise → dead try/except | **HIGH** — None flows to confusing late crash | Check: `if resolved is None: raise ValueError(...)` | Verified: registry.py:321-344 |
| **C5**: No per-category factory dispatch → standalone factory never called | **CRITICAL** — tool absent/unclosured | Define `spawn_councilor` INSIDE `create_instance_tools()` | Verified: instance.py:679-1314 |
| **C6**: `extra="ignore"` + per-field loader → flag silently discarded | **HIGH** — appender no-op, no error | BOTH: add field to AgentMetadata AND add loader line | Verified: registry.py:138-140, 254-272 |
| W3: 5 councilors > WorkerPool=4 | Med | Max 4 councilors (D4) | Verified: constants.py:48-50 |
| W6: TOCTOU — lifecycle revalidates with silent fallback | Low | Pass validated model immutably through chain | Verified: instance_lifecycle.py:1145 |
| W7: Case-insensitive resolver returns caller spelling → duplicate models | Med | Normalize to canonical name (D10) | Verified |
| W8: Fail-open appender = ambiguous dead-end | Low | Append `<allowed_models status="error">` on failure (D3/W8) | — |

## Success Criteria (REVISED — Rev 3 adds D9)

- [ ] `agents/governor/` directory exists with valid `meta.json`, `soul.md`, `rule.md`, `workflow.md`, `tools_note.md`
- [ ] `spawn_councilor` defined INSIDE `create_instance_tools()` as a closure (C5)
- [ ] `spawn_councilor` REJECTS invalid `model` — raises ValueError with "not in allowed_models" (C3/C4 corrected)
- [ ] `spawn_councilor` REJECTS invalid `councilor_agent_id` — checks `resolve_to_id` for None AND `_check_team_membership` return value
- [ ] `clear_councilor_errors` tool exists and calls `bus.clear_parent_error()` (C1/D7)
- [ ] `append_allowed_models` uses `manager.config` (NOT `manager._config`) (C2)
- [ ] `inject_allowed_models` field EXISTS on AgentMetadata model AND in loader (C6)
- [ ] Integration test: loading governor meta.json → `agent_meta.inject_allowed_models == True` (C6)
- [ ] Integration test: governor instance has `spawn_councilor` bound (C5)
- [ ] Governor with `inject_allowed_models: true` sees `<allowed_models>` block in system prompt
- [ ] **C1 test:** governor with 1 errored councilor + 3 successful → terminal status COMPLETED (not ERROR) after `clear_councilor_errors`
- [ ] Max 4 councilors (not 5) — aligned with WorkerPool=4
- [ ] No changes to `spawn_instance` behavior (backward compatible)
- [ ] **D9-1:** 1 result → output starts with "⚠️ Confidence Notice:" block
- [ ] **D9-2:** extension past 30min soft limit → manifest shows `deadline_extended=true`
- [ ] **D9-3:** 1h hard limit → `terminate_instance` + partial result counted as 1 degraded result
- [ ] **D9-4:** extension past 1h hard cap rejected (no extension possible)

## Deliverables in This Plan

```
.agents/shared/planning/governor-council-manager/
├── plan-overview.md          ← this file
├── phase0-plan.md            ← Foundation: frozen contracts (signatures, schema, positions)
├── phase1-plan.md            ← Governor Agent Definition (agents/governor/*)
├── phase2-plan.md            ← spawn_councilor + clear_councilor_errors Tools
├── phase3-plan.md            ← Models List Injection Appender
├── phase4-plan.md            ← Integration, Wiring & Testing
└── decisions.md              ← Architecture decisions & trade-offs (D1-D10)
```

## Tracking

- **Created**: 2026-07-25
- **Last Updated**: 2026-07-25 (Rev 2 — C1-C6 fixes)
- **Status**: draft (ready for implementation review)
