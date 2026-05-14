# Plan Overview: Project-Based Tabs for Instance UI

## Objective
Add a tab-based UI to the instance list sidebar on the Chat page. Each tab represents a project, filtering instances to that project only. Includes an "All" tab (permanent), a "+" button to add project tabs, close buttons per tab, and backend API support for filtering instances by project.

## Scope Assessment
**LARGE** — Spans backend (model migration, API changes across 4 layers) and frontend (new component, service, polling lifecycle, e2e tests). Involves 12+ files across both layers, with database migration risk and full-stack 4-layer call chain updates.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Stack**: Python/FastAPI backend (SQLModel/SQLite), Angular 21 frontend (Signals, Angular Material)
- **Testing**: Jest 29 for unit tests, **no e2e framework yet** (need Playwright)
- **Backend port**: 8088 (see `config.yaml:18`)
- **Backend entry**: `python -m daemon` (see `daemon/__main__.py`)
- **Migration system**: SQL files in `daemon/migrations/versions/` with `-- UP` / `-- DOWN` sections, run by `daemon/migrations/runner.py`

## Key Architectural Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **project_id storage** | Add real indexed column to `instances` table | First-class query dimension; JSON extraction is not indexable in SQLite |
| **Tab state** | Angular Signals service + localStorage persistence | Aligns with existing patterns; survives page refresh |
| **Routing** | No URL change for tab selection | Tabs filter sidebar content only; avoids route complexity |
| **Component structure** | New `ProjectTabBarComponent` in ChatComponent sidebar | Minimal restructuring; tabs only on Chat page (not Home) |
| **E2E framework** | Playwright | Angular-recommended; best TS integration; auto-wait features |
| **Polling ownership** | `InstanceService` owns polling lifecycle from Phase 3 | Centralized in one service; components call `startPolling`/`stopPolling` in lifecycle hooks |
| **Background tab inactivity** | Stop 10s polling interval for non-active tabs | SSE is per-instance (not per-tab); only the polling is tab-scoped |

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Backend: Schema & Migration | Add `project_id` column to instances table | None | — | 1-2h |
| 2 | Backend: API Filter (Full Stack) | Add `project_id` param through all 4 layers to `GET /api/instances` | Phase 1 | tight | 1-2h |
| 3 | Frontend: Tab Service, Component & Polling | `TabStateService` + `ProjectTabBarComponent` + `InstanceService` with polling | Phase 2 | loose | 4-5h |
| 4 | Frontend: UX Polish & Edge Cases | Animations, tab-switch loading state, edge-case handling | Phase 3 | tight | 1-2h |
| 5 | E2E Tests with Playwright | Install Playwright, write browser automation tests | Phases 2 + 3 | independent | 2-3h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| 1 → 2 | **tight** | Phase 2 queries the column Phase 1 creates; same model files |
| 2 → 3 | **loose** | Phase 3 only needs the API contract (`?project_id=xxx`); different codebase layer |
| 3 → 4 | **tight** | Phase 4 polishes the same components/services Phase 3 creates |
| 2+3 → 5 | **independent** | E2E tests verify Phases 2+3 output but don't share code; can pipeline with Phase 4 |

### Scheduling Recommendation
```
Phase 1 → Phase 2 (sequential, tight coupling)
                ↓
           Phase 3 (after Phase 2 API is ready)
            ↙      ↘
     Phase 4      Phase 5  (can run in parallel; Phase 5 depends on Phase 2 + 3)
```
Total critical path: **7-11h**. With parallelization: **7-11h** (Phase 5 overlaps Phase 4).

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Old instances have no `project_id` (NULL column) | Medium | "All" tab shows everything; project tabs gracefully omit NULL instances. Migration backfills from `metadata` JSON where available. |
| SQLite migration on existing databases | Medium | Column is nullable; no data loss. Use existing `MigrationRunner` with SQL file. |
| Tab state lost on refresh (before localStorage) | Low | Phase 3 persists tab state to localStorage on every change using `ensemble-project-tabs` key. |
| SSE confusion: per-instance vs per-tab | Low | Clarified: SSE is per-instance. "Inactive tab" = stop polling, not SSE (SSE already tied to active instance). |
| Playwright install fails in CI | Medium | Add Playwright as devDependency with browsers; provide fallback instructions. |
| No DELETE /api/projects endpoint for test cleanup | Medium | Use test-specific project names; cleanup via direct DB manipulation or add temporary endpoint. |
| 4-layer backend call chain must all be updated | Medium | Phase 2 explicitly lists all 4 layers: Router → Manager → LifecycleService → Repository. |

## Success Criteria
- [ ] "All" tab shows all instances (backward compatible)
- [ ] Clicking "+" shows context menu of unopened projects
- [ ] Selecting a project opens a tab filtering instances to that project
- [ ] Close button removes a project tab (not "All")
- [ ] Background tabs stop polling (no network activity)
- [ ] Tab state persists across page refresh (localStorage key `ensemble-project-tabs`)
- [ ] Backend `GET /api/instances?project_id=xxx` returns filtered results through all 4 layers
- [ ] Instance creation accepts `project_id` via `InstanceCreate` model
- [ ] E2E test covers tab open/close/filter flow
- [ ] Existing functionality unaffected (no regressions)

## Tracking
- Created: 2026-05-14
- Last Updated: 2026-05-14
- Status: draft (revised — v2)
