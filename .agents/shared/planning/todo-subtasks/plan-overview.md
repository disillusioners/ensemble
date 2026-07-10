# Plan Overview: Todo Node Sub-Tasks

## Objective
Add sub-task (checklist) support to todo graph nodes. A node can contain a list of child sub-tasks — each independently checkable — with optional automatic status propagation to the parent node when all sub-tasks are completed. The graph structure (edges between nodes) remains unchanged; sub-tasks live within a node as a nested list.

## Scope Assessment
**LARGE** — Touches 4 layers (manager, tools, API, frontend) across ~10 files, requires schema evolution of the frozen SSE payload (6→7 keys), adds new agent tools, new API endpoints, and non-trivial frontend rendering. Existing 168 tests require 10 assertion updates (8 schema-key-set + 2 tool-count); ~55-70 new tests added. Estimated 1-2 developer-days.

> ⚠️ **Atomic merge requirement:** Phases 1-4 must be merged together. Do not merge to main without Phase 4 — the frontend 6-field `TodoNode` TypeScript interface would be out of sync with the 7-key SSE payloads.

## Context
- Project: agents-ensemble
- Working Directory: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- Feature branch: `feature/todo-subtasks` (branch from latest)

## Key Files Affected

| Layer | File | Change Type |
|-------|------|-------------|
| Manager | `daemon/services/todo_manager.py` | New `SubTask` dataclass, `subtasks` field on `TodoNode`, 6 new methods, `_to_dict` schema evolution |
| Tools | `daemon/tools/todo_tools.py` | 3 new tools (`todo_add_subtask`, `todo_update_subtask`, `todo_remove_subtask`), `_format_graph` sub-task rendering |
| API | `daemon/routers/instances.py` | 3 new endpoints (add/update/remove sub-task), Pydantic models |
| Frontend TS | `frontend/src/app/components/todo-list/todo-list.component.ts` | Sub-task state, toggle handlers, expand/collapse |
| Frontend HTML | `frontend/src/app/components/todo-list/todo-list.component.html` | Sub-task checklist rendering (linear + graph modes) |
| Frontend SCSS | `frontend/src/app/components/todo-list/todo-list.component.scss` | Sub-task styling |
| Frontend Types | `frontend/src/app/services/sse.service.ts` | `SubTask` interface, `TodoNode.subtasks` field |
| Frontend API | `frontend/src/app/services/api.service.ts` | 3 new API methods |
| Skill Docs | `agents/_prompt_system/innate-skills/todo/skill.md` | Document 3 new tools |
| SSE Hub | `daemon/services/live_event_hub.py` | Docstring update (payload shape changed) |

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Backend Data Model + Service Layer | Add `SubTask` dataclass, `subtasks` field, manager methods, status propagation, schema evolution | None | — (root) | 4-5h |
| 2 | Agent Tools + Skill Docs | 3 new tools, `_format_graph` sub-task rendering, skill docs | Phase 1 | tight (imports manager methods) | 2-3h |
| 3 | API Endpoints | 3 new REST endpoints, Pydantic models, SSE emission | Phase 1 | tight (imports manager methods) | 2h |
| 4 | Frontend Visualization | Sub-task rendering, toggle, expand/collapse, API integration | Phase 3 | loose (depends on API contract only) | 3-4h |
| 5 | Tests + Integration Verification | Comprehensive test suite, backward compat verification, e2e check | Phases 1-4 | loose (verifies all layers) | 2-3h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| 1 → 2 | **tight** | Phase 2 tools directly call new manager methods (`add_subtask`, `update_subtask`, `remove_subtask`) — must exist with correct signatures |
| 1 → 3 | **tight** | Phase 3 API endpoints call same manager methods — same dependency |
| 3 → 4 | **loose** | Frontend depends on API contract (endpoints + JSON shape), not implementation. Can start once Phase 3 contract is defined (even before merged) |
| 2 ↔ 3 | **independent** | Tools and API are separate consumers of the manager; no shared code between them |
| 4 → 5 | **loose** | Tests verify all layers but don't need implementation details beyond contracts |

**Parallelization opportunity:** Phases 2 and 3 can run in parallel once Phase 1 is complete (both depend only on the manager). Phase 4 can start once Phase 3's API contract is agreed (even during Phase 3 implementation).

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Frozen schema break — `_to_dict` changes from 6→7 keys | high | Add `subtasks` as 7th key. **10 existing tests require assertion updates**: 8 use `set(item.keys()) == {6-key-set}` (test_todo_manager.py:399,1069; test_todo_sse.py:324-331,387-394,434-441,481-488; test_todo_api.py:158-160; test_comment_edge_cases.py:187) and 2 check tool count (test_todo_tools.py:75 `len==6`, :84-91 exact name list). Phase 5 includes a task to update all 10. |
| Frozen contract docstrings forbid change — 6+ locations assert "exactly six keys" / "Do NOT change" | high | Audit and update all 6 docstring locations in Phase 1/3: `todo_manager.py:22-28`, `:160-162`, `:476-479`, `:938-958`; `live_event_hub.py:351-370`; `instances.py:428-436`. Evolve wording from "frozen six keys" to "frozen seven keys (subtasks added)." |
| Status propagation surprise — parent auto-marks done when sub-tasks complete, but agent wanted manual control | medium | Make propagation **opt-in**: `auto_complete` flag on `add_subtask` or a manager-level toggle. Default OFF (manual). |
| `_format_graph` complexity explosion with nested sub-tasks in text rendering | medium | Keep sub-task rendering compact: indented checklist lines under each node. Depth-limited to 1 level (no sub-sub-tasks). |
| `_compute_reminder` ignores sub-task state — ready-node logic only sees parent node status | low | Intentional: sub-tasks are within-node detail; graph readiness is based on node-level status only. Document this clearly. |
| Frontend layout breaks — sub-tasks expand node height in graph mode, breaking SVG positioning | medium | Use dynamic `foreignObject` height or expand sub-tasks in a popup/overlay rather than inline. Test with branching graphs. |
| MAX_NODES interaction — should sub-tasks count toward the 200 limit? | low | Separate `MAX_SUBTASKS_PER_NODE = 20` limit. Sub-tasks don't count toward MAX_NODES. |

## Success Criteria
- [ ] `SubTask` dataclass with `{id, text, status}` implemented
- [ ] `TodoNode.subtasks: list[SubTask]` field added (default `[]`)
- [ ] `_to_dict()` returns 7-key schema (6 existing + `subtasks`)
- [ ] 3 manager methods: `add_subtask()`, `update_subtask()`, `remove_subtask()`
- [ ] Optional status propagation: all sub-tasks done → parent auto-done (configurable)
- [ ] 3 agent tools: `todo_add_subtask`, `todo_update_subtask`, `todo_remove_subtask`
- [ ] `_format_graph()` renders sub-tasks as indented checklist
- [ ] 3 API endpoints for sub-task CRUD with SSE emission
- [ ] Frontend renders sub-tasks in both linear and graph modes
- [ ] Sub-task toggle (checkbox) in frontend updates via API
- [ ] All existing tests pass after updating 10 assertions (8 schema-key-set + 2 tool-count) — see Phase 5
- [ ] ~55-70 new tests added covering sub-task CRUD, propagation, edge cases
- [ ] Skill docs updated with 3 new tools

## Tracking
- Created: 2026-07-10
- Last Updated: 2026-07-10 (revision 2 — reviewer fixes applied)
- Status: reviewed
