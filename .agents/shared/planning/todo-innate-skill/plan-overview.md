# Plan Overview: Todo Innate Skill

## Objective
Build a Todo innate skill that gives ALL agents a set of todo tools to manage multi-step workflows. When an agent marks a todo item as done, the system reminds the next pending item in the tool result. Todo lists are emitted via SSE (full-list replacement) to the frontend, which displays them above the chat box with a minimize/collapse button.

## Scope Assessment
**BIG** — spans backend (new TodoManager service + 4 tools + tool category registration), SSE integration (new event type + LiveEventHub method), innate skill (skill.md + global registration across 17 agent meta.json files), and frontend (new Angular component + SSE handler + UI integration). Four cohesive feature areas across two major codebases (Python daemon + Angular frontend).

## Context
- Project: agents-ensemble
- Working Directory: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- PostgreSQL is the default dev/test DB
- Factory pattern: `create_x_tools(manager, current_instance_id) -> list`
- SSE events routed by `event["event_type"]` field automatically in `messages.py:341-342`

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Backend: TodoManager + Tools | TodoManager service, 4 todo tools, tool category registration | None | — (root) | 2-3h |
| 2 | SSE Integration | `stream_todo_update()` in LiveEventHub, wire into tool execution | Phase 1 | tight | 1-1.5h |
| 3 | Innate Skill + Agent Registration | skill.md + add "todo" to all agent meta.json | None | independent | 0.5-1h |
| 4 | Frontend: Todo-List Component | Standalone Angular component + SSE handler + chat.html integration | Phase 2 | loose | 2-3h |

### Coupling Assessment

| Phase Pair | Coupling | Reasoning |
|------------|----------|-----------|
| 1 → 2 | **tight** | Phase 2 needs Phase 1's TodoManager + tools to call `stream_todo_update()`. Tools must trigger SSE emission. Same codepath. |
| 1 → 3 | **independent** | Phase 3 only creates skill.md + edits meta.json. No code dependency on Phase 1's implementation. |
| 2 → 4 | **loose** | Frontend only needs the SSE event payload contract (`{event_type, instance_id, todos[]}`). Doesn't need backend implementation details. Can code against the spec. |
| 3 → 4 | **independent** | Frontend doesn't depend on skill.md or meta.json. |

**Parallelization opportunities:**
- Phases 1+2 can be done as one backend session (tight coupling, same codepath)
- Phase 3 can run **in parallel** with Phases 1+2 (fully independent)
- Phase 4 can start in parallel with Phases 1+2+3 if coding against the SSE payload spec (loose coupling), but integration testing requires Phase 2 complete

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LiveEventHub access from within tools (async context) | med | Tools are async already; `_live_hub` accessible via `manager._live_hub`. Add `try/except` around SSE emit so tool failure doesn't block todo operation. |
| TodoManager memory leak (instances that never terminate) | low | Per-instance dict; cleanup on instance termination via existing lifecycle hooks. Document as known limitation for now. |
| All 17 agent meta.json files need updating | low | Script or careful manual edit. Consider global default approach as alternative (see Phase 3 decisions). |
| Frontend SSE event type matching | low | `event["event_type"]` = SSE event name automatically. Frontend listens for `"todo_update"`. Contract is simple. |
| Angular standalone component integration | low | Well-established pattern (all components are standalone). Follow `job-card` component pattern. |

## Success Criteria
- [ ] `todo_create`, `todo_update`, `todo_list`, `todo_clear` tools work for all agents
- [ ] `todo_update` returns reminder of next pending item when marking one as done
- [ ] SSE `todo_update` event emitted on any list change (create/update/clear)
- [ ] Frontend displays todo list above chat box, collapsible
- [ ] Frontend updates in real-time via SSE
- [ ] Todo list hidden when empty
- [ ] "todo" innate skill available to all agents
- [ ] Tests pass (backend unit tests for TodoManager + tools; frontend compiles)

## Tracking
- Created: 2026-07-08
- Last Updated: 2026-07-08
- Status: draft
