# Plan Overview: Todo System — Flat List → Graph (DAG) Transformation

## Objective

Transform the per-instance todo system from a flat ordered list (`TodoItem[]` indexed by position) into a directed acyclic graph (DAG) of `TodoNode`s (keyed by string ID, with `next_ids` adjacency lists), enabling branching task structures, parallel workstreams, and merge points — while maintaining full backward compatibility with existing flat-list callers (agents, API clients, frontend).

## Scope Assessment

**LARGE** — Multi-layer transformation across backend data model, agent tools, API endpoints, SSE payload, frontend visualization, and 101 existing tests across 5 files. No new infrastructure (in-memory storage stays), but the data model change ripples through every consumer.

**Justification:**
- `TodoItem` → `TodoNode` changes the core identity from `int index` to `str id` + adjacency list
- All 4 agent tools need new signatures (backward-compatible overloads)
- 2 API endpoints change shape + 2 new endpoints for edge management
- Frontend must render a graph instead of a flat list (new visualization component)
- 101 tests across 5 files need updating + ~45 new graph-specific tests
- DAG validation (cycle detection, orphan detection, reference integrity) is new logic

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Current files**:
  - `daemon/services/todo_manager.py` (260 lines) — `TodoItem` dataclass + `TodoManager` class
  - `daemon/tools/todo_tools.py` (209 lines) — 4 tools: `todo_create`, `todo_update`, `todo_list`, `todo_clear`
  - `daemon/routers/instances.py` (499 lines, todo section ~375-499) — GET todos + POST comment
  - `daemon/services/live_event_hub.py` (376 lines, todo section ~336-356) — `stream_todo_update()`
  - `daemon/services/instance_lifecycle.py` (2623 lines, cleanup at ~836) — `clear()` on terminate
  - `daemon/manager.py` (4323 lines, init at ~716) — `self._todo_manager = TodoManager()`
  - `agents/_prompt_system/innate-skills/todo/skill.md` (16 lines) — tool inventory documentation
  - `frontend/src/app/components/todo-list/` — component TS (181), HTML (78), SCSS (223)
  - `frontend/src/app/services/sse.service.ts` (394 lines) — `TodoItem` interface + `todo_update` listener
  - `frontend/src/app/services/api.service.ts` — `getTodos()`, `setTodoComment()`
  - `frontend/src/app/pages/chat/chat.component.ts` (443 lines, line 310) — direct SSE signal write
- **Test files** (101 tests total — verified via `pytest --collect-only`):
  - `tests/test_todo_manager.py` — 35 tests (6 classes)
  - `tests/test_todo_tools.py` — 19 tests (6 classes)
  - `tests/test_todo_sse.py` — 11 tests (6 classes)
  - `tests/test_todo_comment_edge_cases.py` — 18 tests (3 classes)
  - `tests/unit/routers/test_todo_api.py` — 18 tests (3 classes)
- **Frontend already has `mermaid ^11.4.0`** installed (not used — custom SVG is lighter)

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Backend Data Model + DAG Service | `TodoNode` dataclass, `TodoGraphManager` with DAG validation, cycle detection, backward-compatible `create()` that auto-chains flat lists. **Freezes SSE payload schema.** | None | — | 4-6h |
| 2 | Agent Tools (Backward Compatible) | Overloaded `todo_create`/`todo_update`/`todo_list`/`todo_clear` accepting graph structure + new `todo_add_edge`/`todo_remove_edge` tools, updated SSE emission, skill.md update | Phase 1 (frozen schema) | tight | 3-4h |
| 3 | API Endpoints + SSE Payload | Updated GET/POST endpoints (node ID-based), new edge management endpoints, SSE `todo_update` payload documentation (frozen schema) | Phase 1 (frozen schema) | loose | 2-3h |
| 4 | Frontend Graph Visualization | `TodoNode` TypeScript interface (with preserved `index`), SVG-based graph layout (nodes + directed edges), per-node status/comment, backward-compatible flat-list rendering | Phase 1 (frozen schema) | loose | 4-6h |
| 5 | Test Suite Update + Graph Tests | Update all 101 existing tests for graph model, add ~45 new DAG-specific tests (cycles, branches, merges, orphan nodes, edge management) | Phases 1-4 | loose | 4-5h |

### Coupling Assessment

> **W10 fix**: The SSE payload schema is frozen as a Phase 1 deliverable. This eliminates the coupling between Phase 2 (tools) and Phase 3 (API) — both depend only on Phase 1's frozen schema, not on each other.

| Coupling | Meaning | Scheduling |
|----------|---------|------------|
| **independent** | Different files/modules, no shared APIs | Can run in parallel |
| **loose** | Depends on planned interfaces only, not implementation | Can pipeline (overlap review + next coding) |
| **tight** | Depends on actual code from prior phase (same files, models, APIs) | Must run sequential — wait for review approval |

| Phase Pair | Coupling | Rationale | Scheduling |
|------------|----------|-----------|------------|
| 1 → 2 | **tight** | Tools call `TodoGraphManager` methods directly | Sequential — wait for Phase 1 review |
| 1 → 3 | **loose** | API endpoints call manager methods but only need the interface contract + frozen SSE schema | Can pipeline (start after Phase 1 interface stable) |
| 1 → 4 | **loose** | Frontend depends on frozen SSE payload schema, not backend implementation | Can pipeline (start after Phase 1 schema frozen) |
| 2 ↔ 3 | **independent** | Both depend only on Phase 1 frozen schema; no cross-dependency (W10 fix) | Can run in parallel |
| 1-4 → 5 | **loose** | Tests validate all layers but can be written against the interface contracts | Parallel with Phases 2-4 (write tests as contracts stabilize) |

**Parallelization opportunity**: Phases 2, 3, and 4 can ALL overlap once Phase 1's frozen SSE schema is stable. Phase 5 (tests) can be written incrementally alongside each phase.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Backward compatibility break** — agents using `todo_create(items=["A","B"])` break | high | `todo_create` accepts `list[str]` OR graph structure; flat list auto-converts to linear chain. `todo_update(index=0, status="done")` preserves positional parameter order (C5 fix). |
| **DAG cycle validation performance** — large graphs with many edges | low | Kahn's algorithm (O(V+E)) with `collections.deque.popleft()` (C1 fix). Graphs are typically <50 nodes. Max 200 nodes guard. |
| **Numeric node ID collision** — all-numeric IDs route to wrong API path | high | All generated IDs prefixed with `n-` (e.g., `n-a1b2c3d4`). Never all-numeric. (C3 fix.) For `create_graph()` user-supplied IDs: reject all-numeric IDs with `ValueError` to prevent API `isdigit()` misfire. |
| **Frontend graph layout complexity** — arbitrary DAGs are hard to lay out | medium | Simple topological-sort-based layered layout with `yCursor` positioning (W12 fix). Fall back to vertical stack if linear chain. Container width via `ResizeObserver` (W13 fix). |
| **SSE payload backward compatibility** — removing `index` breaks old frontend | high | `index` field PRESERVED in payload alongside new `id` and `next_ids` (C4 fix). Augmented, not replaced. |
| **TypeScript compile failures** — `TodoItem extends TodoNode` breaks `item.index` | high | `TodoNode` includes `index: number` as required field. `TodoItem = TodoNode` type alias (no extends). (C6 fix.) |
| **101 test breakage** — existing tests assert on `index` field and key sets | medium | Phase 5 systematically updates all tests. Only 4 `set(item.keys())` assertions need updating (C8 fix). `index` field preserved so most assertions stay valid. |
| **`_todo_manager` rename confusion** — 6 call sites across codebase | low | Keep the attribute name `_todo_manager`. `TodoManager = TodoGraphManager` alias preserves all imports. (ADR-8.) |
| **Reminder logic for branching** — "next pending" is ambiguous in a DAG | medium | Reminder shows ALL "ready" pending nodes (predecessors all done). Multiple ready nodes listed together. |
| **Undeclared template constants** — `NODE_WIDTH`, `graphWidth()` used but not declared | medium | All constants declared as class members or computed signals (C11 fix). |
| **Direct SSE signal write in chat.component.ts** — not in original Phase 4 scope | low | Added to Phase 4 Key Files for verification (C12 fix). |

## Success Criteria

- [ ] `TodoGraphManager` stores nodes as `dict[str, TodoNode]` with adjacency lists
- [ ] DAG validation rejects cycles on create/update/add_edge (Kahn's algorithm with `deque.popleft()` — C1 fix)
- [ ] `todo_create(items=["A","B","C"])` (flat list) still works — auto-converts to linear chain
- [ ] `todo_create(nodes=[...], edges=[...])` (graph structure) creates a DAG (edges as `list[dict]` — C2 fix)
- [ ] `todo_update(node_id="n-abc", status="done")` works by node ID
- [ ] `todo_update(0, "done")` positional call still works (C5 fix — `index` stays first param)
- [ ] `todo_update(index=0, status="done")` keyword call still works
- [ ] Node IDs are `n-`-prefixed (C3 fix — never all-numeric)
- [ ] Reminder logic shows all "ready" pending nodes (predecessors all done)
- [ ] Comment-fence prompt-injection protection PRESERVED: done + non-empty comment → `"User commented:\n---\n{comment}\n---\n"` prefix before base reminder
- [ ] New `todo_add_edge` / `todo_remove_edge` tools manage graph edges
- [ ] `_format_graph()` handles merge nodes with `(merged)` annotation (W6 fix)
- [ ] API endpoints accept node IDs in URL paths (numeric index auto-detected for backward compat)
- [ ] SSE `todo_update` payload includes `id`, `index` (preserved), `text`, `status`, `comment`, `next_ids` (6 keys — C4 fix)
- [ ] Frontend renders graph with nodes as cards + directed edges as arrows
- [ ] Frontend falls back to flat list rendering when graph is a linear chain
- [ ] All 101 existing tests pass (updated for graph model)
- [ ] ~45 new tests cover: cycle rejection, branching, merging, orphan nodes, edge management, merge rendering
- [ ] Thread safety preserved (threading.Lock pattern)
- [ ] Comment max length (1000) preserved
- [ ] Status normalization (16 aliases) preserved
- [ ] `MAX_NODES` guard (200) enforced on `create()`, `create_graph()`, AND `add_node()` (W4 fix)
- [ ] `skill.md` updated with 6-tool inventory (C10 fix)
- [ ] All template constants declared (`NODE_WIDTH`, `graphWidth()`, `graphHeight()` — C11 fix)
- [ ] Container width tracked via `ResizeObserver` (W13 fix)
- [ ] **Frontend type-check passes (`ng build` succeeds)** (W14 fix)

## Tracking

- Created: 2026-07-10
- Last Updated: 2026-07-10 (review revision — 12 critical + 7 warning fixes applied)
- Status: draft (revised)
