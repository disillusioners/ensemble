# Plan Overview: RAG Knowledge Toolset

## Objective

Build a RAG knowledge management system for agents-ensemble that wraps an external LightRAG server, enabling all agents to query and record project knowledge via `explore()` and `experience()` tools. This introduces an "Agent-as-Tool" pattern where specialized agents (explorer, experiencer) serve as the intelligence behind public tools available to all agents.

## Scope Assessment

**LARGE** — 6 phases spanning new modules (RAG client, completion registry), new tools (15 RAG tools + 2 knowledge tools), 2 new agents, and a migration across all existing agent definitions. Touches core services, tools layer, agent definitions, and introduces a new architectural pattern (agent-as-tool with synchronous invocation).

## Context

- **Project**: agents-ensemble
- **Working Directory**: /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
- **Key Constraint**: LightRAG server is external; all interaction is HTTP API
- **Key Pattern**: Agent-as-Tool requires new synchronous wait infrastructure (CompletionRegistry)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                  Any Agent Instance (coder, leader, ...)        │
│                                                                 │
│   Calls: explore(query)  ──► spawns Explorer ──► WAITS ──► result│
│   Calls: experience(text) ──► spawns Experiencer ──► returns ID │
│                                                                 │
│   Tools: knowledge category (explore, experience)               │
└────────────────────────────┬────────────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              │
   ┌──────────────┐ ┌──────────────┐       │
   │  Explorer    │ │ Experiencer  │       │
   │  Agent       │ │ Agent        │       │
   │              │ │              │       │
   │ Tools: rag   │ │ Tools: rag   │       │
   │ filesystem   │ │ help         │       │
   │ help         │ │              │       │
   └──────┬───────┘ └──────┬───────┘       │
          │                │               │
          ▼                ▼               ▼
   ┌─────────────────────────────────────────────┐
   │           RAG HTTP Client (httpx)           │
   │           daemon/rag/ module                 │
   └──────────────────┬──────────────────────────┘
                      │ HTTP (X-API-Key, LIGHTRAG-WORKSPACE)
                      ▼
   ┌─────────────────────────────────────────────┐
   │           External LightRAG Server           │
   └─────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────┐
   │        CompletionRegistry (new)              │
   │   daemon/services/completion_registry.py     │
   │                                              │
   │   asyncio.Event per instance_id              │
   │   Set on completion in ChildReportsService   │
   │   Enables: explore() synchronous wait        │
   └─────────────────────────────────────────────┘
```

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Core Infrastructure | CompletionRegistry + sync wait + error propagation + invoke_agent_and_wait() | None | — | 2-3 days |
| 2 | RAG HTTP Client | AsyncLightRAGClient module with httpx + Pydantic schemas | None | — | 1-2 days |
| 3 | RAG & Knowledge Tools | 15 RAG tools + explore/experience knowledge tools + registry wiring | Phase 1, Phase 2 | tight (both) | 2-3 days |
| 4 | Explorer Agent | Agent definition (soul, rules, workflow) with RAG tools | Phase 3 | tight | 1-2 days |
| 5 | Experiencer Agent | Agent definition with RAG tools for knowledge insertion | Phase 3 | loose | 1-2 days |
| 6 | Project Experience Migration | Classification-aware inner_soul redirect + deprecate project-level file memory (`.agents/`) + migration docs. Agent core memory (`agents/*/`) untouched. | Phase 3, 4, 5 | loose | 1-2 days |

### Coupling Assessment

| Phase Pair | Coupling | Reasoning |
|------------|----------|-----------|
| 1 → 3 | **tight** | Phase 3's `explore()` tool directly calls `invoke_agent_and_wait()` from Phase 1 |
| 2 → 3 | **tight** | Phase 3's RAG tools directly import `AsyncLightRAGClient` from Phase 2 |
| 3 → 4 | **tight** | Explorer agent uses `rag` tool category defined in Phase 3 |
| 3 → 5 | **loose** | Experiencer uses same `rag` tools but only write-side tools |
| 4 → 6 | **loose** | Migration updates agent markdowns but doesn't change code |
| 5 → 6 | **loose** | Same as above |

### Parallelization Opportunities

| Can Run In Parallel | Reason |
|---------------------|--------|
| Phase 1 + Phase 2 | Independent: infrastructure vs HTTP client |
| Phase 4 + Phase 5 | Independent: two separate agent definitions (both wait for Phase 3) |

## Critical Investigation Results

### Instance Lifecycle (from codebase exploration)

```
Worker Thread ──claim_task()──► MainLoopBridge.run_async() ──► graph.astream/ainvoke()
                                                                      │
                                                                ReAct Loop
                                                                      │
                                                              ToolNode dispatches
                                                                      │
                                                              Message processed
                                                                      │
                                                    task_processor.py:266
                                                    _process_child_completion_and_notify_parent()
                                                                      │
                                                    child_reports.py:493
                                                    Creates completion report → enqueues for parent
```

### Synchronous Wait Solution

The system currently has **NO** synchronous wait mechanism. However, the pattern already exists in `dispatch_event_bus.py` (per-project asyncio.Event). The solution:

1. **CompletionRegistry** — asyncio.Event per instance_id, thread-safe, with `CompletionResult` wrapper (distinguishes success vs error) and **buffered completions** (handles `complete()` before `register()` race)
2. **Hook into child_reports.py at exact exit points** — Signal at EXIT 5a (root completing, line ~557) and MAIN PATH (child with parent, after `session.commit()` at line 597). `last_content` is always available (fetched at line 507 before session).
3. **Hook into error_reporting.py** — Signal with `is_error=True` after instance status set to ERROR (line ~166), preventing `invoke_agent_and_wait()` from hanging on agent crashes.
4. **`invoke_agent_and_wait()`** — Spawns instance, registers for completion, sends message, awaits completion event. Handles timeout (best-effort terminate orphan), error propagation (returns `"Error: ..."` string), and always cleans up registry entry in `finally`.
5. **Deadlock prevention via semaphore** — `asyncio.Semaphore(WORKER_POOL_SIZE - 1)` caps concurrent `invoke_agent_and_wait()` calls, ensuring at least 1 worker thread stays free to process agent-as-tool instances. Without this, all workers can block on their own `wait_for()` calls with no worker free to process the spawned agent.

### Tool Execution Context

| Property | Value |
|----------|-------|
| Async context | ✅ Yes — tools can be `async def` |
| Can use `await` | ✅ Yes — full asyncio support |
| Thread model | Worker thread → MainLoopBridge → async event loop |
| Timeout | Bash default 1800s; tools have no inherent limit |
| Cancellation | Token-based via `CancellationCallbackHandler` |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Worker pool deadlock via explore() | **Critical** — all workers block, system freezes | `asyncio.Semaphore(WORKER_POOL_SIZE - 1)` caps concurrent invoke-and-wait calls; ensures ≥1 free worker |
| CompletionRegistry race: complete() before register() | **High** — result dropped, caller times out | Buffered completions: `complete()` stores in `_buffered` dict; `register()` consumes and immediately resolves |
| Spawned agent errors not propagated | High — caller hangs | CompletionRegistry signaled on BOTH success (child_reports.py) AND error (error_reporting.py) |
| Orphaned instances after timeout | Medium — resource leak | `_try_terminate_orphan()` on timeout; periodic `cleanup_stale()` for registry leaks |
| LightRAG unavailable | Medium — knowledge tools fail | Graceful degradation: return error message, don't crash |
| Memory leak in CompletionRegistry | Medium | Always `unregister()` in `finally`; periodic `cleanup_stale()` removes entries >1hr |
| Race condition: complete() before wait_for() | Low | asyncio.Event.set() before wait() returns immediately |
| inner_soul mis-routing self-modification to RAG | Medium — agent loses identity changes | `_should_redirect_to_rag()` only redirects when ALL targets are memories/memory; agent core memory files (`agents/*/`) never modified |
| Phase 3 size (17 tools) | Medium | Split into two coding sessions if needed |

## Success Criteria

- [ ] `explore(query)` returns RAG-enhanced response within timeout
- [ ] `experience(text)` spawns background processing, returns immediately
- [ ] Explorer agent queries RAG, browses files if weak confidence, returns answer
- [ ] Experiencer agent extracts entities/relationships, inserts into RAG
- [ ] All existing agents updated to use explore/experience
- [ ] File-based project memory deprecated (`.agents/{agent-id}/memories/`) but inner_soul self-modification preserved
- [ ] Agent core memory (`agents/*/soul.md`, `agents/*/rule.md`, etc.) completely untouched
- [ ] Graceful degradation when LIGHTRAG_HOST not configured
- [ ] No regression in existing agent functionality
- [ ] Unit tests for CompletionRegistry, RAG client, tools

## File Structure (New Files)

```
daemon/
├── rag/                              # Phase 2: RAG module
│   ├── __init__.py
│   ├── client.py                     # AsyncLightRAGClient
│   ├── config.py                     # RAGConfig (ENV-based)
│   ├── schemas.py                    # Pydantic request/response models
│   ├── exceptions.py                 # RAGError, RAGConnectionError, etc.
│   └── endpoints.py                  # Endpoint path constants
├── tools/
│   ├── rag_tools.py                  # Phase 3: 15 RAG tools (rag category)
│   ├── knowledge_tools.py            # Phase 3: explore/experience (knowledge category)
│   └── _tool_registry.py             # Modified: add rag, knowledge categories
├── services/
│   └── completion_registry.py        # Phase 1: CompletionRegistry service

agents/
├── explorer/                         # Phase 4: Explorer agent
│   ├── meta.json
│   ├── soul.md
│   ├── rule.md
│   ├── tools.md
│   └── workflow.md
└── experiencer/                      # Phase 5: Experiencer agent
    ├── meta.json
    ├── soul.md
    ├── rule.md
    ├── tools.md
    └── workflow.md

.agents/shared/planning/rag-knowledge-toolset/
├── plan-overview.md                  # This file
├── phase1-plan.md                    # Core Infrastructure
├── phase2-plan.md                    # RAG HTTP Client
├── phase3-plan.md                    # RAG & Knowledge Tools
├── phase4-plan.md                    # Explorer Agent
├── phase5-plan.md                    # Experiencer Agent
└── phase6-plan.md                    # Project Experience Migration
```

## Tracking

- Created: 2026-04-26
- Last Updated: 2026-04-26 (Fixes: C1 exit points, C2 error recovery, C3 inner_soul classification, CRITICAL deadlock prevention, buffered completions, C4 agent core memory vs project memory distinction)
- Status: draft
