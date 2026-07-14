# Plan Overview: Skill Bank (User CRUD)

## Objective

Add an isolated "Skill Bank" feature where users can Create, Read, Update, and Delete skills as templates. This is a pure user-facing CRUD system — it is **NOT** connected to the existing skill evolution system (no evolution, no metrics, no lineage, no triggers, no embeddings, no agent integration). This step is user-facing CRUD only.

## Scope Assessment

**MEDIUM** — 2 backend layers (model/repo/factory/wiring + router) and a full frontend page (model/service/route/component/nav). Approximately 11 new files, ~9 modified files. Follows established patterns exactly (verified against existing skill system, mcp_servers router, and frontend Skills page). Half-day to full-day of focused work.

**No service layer** — the router accesses the repository directly via `manager._skill_bank_repo`, matching the `mcp_servers.py` pattern exactly. Validation is done via Pydantic `Field(min_length=1)` on request schemas.

## Context

- **Project:** agents-ensemble
- **Working Directory:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Backend:** Python / FastAPI / SQLModel (dual SQLite + PostgreSQL)
- **Frontend:** Angular 21 standalone components + Angular Material (M3 dark theme)
- **Isolation:** The Skill Bank shares the `skill_bank` table only. No FK to `skills`, no service imports from skill evolution, **not** gated behind `config.skill_evolution`.
- **Naming:** API path `/api/skill-bank/*`. Frontend route `/skills/bank`. Table `skill_bank`. Model `SkillBankItem`. Repository `SkillBankRepository`. These are "skills inside the bank" — never "skill-template".

## Verified Architecture Patterns

All patterns below were confirmed by reading the actual source files:

| Concern | Pattern | Reference File |
|---------|---------|----------------|
| SQLModel model | `SQLModel` subclass, `__tablename__`, `__table_args__`, `Field(...)`, `to_dict()`, `_now_iso()` helper, UUID4 str PK, ISO-8601 TEXT timestamps | `daemon/repositories/skill/models.py` |
| **Model registration for table creation** | Models imported in `daemon/repositories/__init__.py` so `SQLModel.metadata.create_all()` discovers them. Missing import = table not created on fresh PG. | `daemon/repositories/__init__.py` |
| Repository class | Plain class, `__init__(self, engine: Engine)`, sync methods, `with Session(engine) as session`, `session.add/commit/refresh` | `daemon/repositories/skill/repository.py` |
| Factory function | `create_X_repository(config=None, engine=None, create_tables=True)`, calls `SQLModel.metadata.create_all(engine)` if `create_tables`, returns `XRepository(engine)` | `daemon/repositories/factory.py` |
| Manager wiring (non-gated) | `self._skill_bank_repo = create_skill_bank_repository(engine=self._engine, create_tables=False)` + PG CREATE TABLE in `_ensure_postgres_columns()` | `daemon/manager.py` (mirrors `_mcp_server_repository` at line 736) |
| PG table creation | Raw `CREATE TABLE IF NOT EXISTS ...` statements array INSIDE `_ensure_postgres_columns()` (method at line 2460, skill DDL at ~line 2959) + `with engine.begin() as conn: for stmt: conn.execute(text(stmt))` | `daemon/manager.py` lines 2460, 2935–3060 |
| API router (no service layer) | `APIRouter(prefix=..., tags=...)`, DI via `_get_manager(request)` accessing `manager._skill_bank_repo` directly, async endpoints bridge via `asyncio.to_thread` | `daemon/routers/mcp_servers.py` |
| Write-pause guard | `if manager.is_write_paused: raise HTTPException(status_code=503, ...)` on ALL write endpoints (POST/PUT/DELETE) | `daemon/routers/mcp_servers.py` lines 283, 348, 481, 568, 612 |
| Request validation | Pydantic `Field(min_length=1)` on required string fields in request schemas (replaces service-layer validation) | Pydantic standard |
| Router registration | Import in `daemon/routers/__init__.py` + `__all__` + `include_router()` in `daemon/api.py` | `daemon/routers/__init__.py`, `daemon/api.py` |
| SQLite migration | `.sql` file in `daemon/migrations/versions/` with `CREATE TABLE IF NOT EXISTS` + comment header | `daemon/migrations/versions/20260710_000001_create_skill_tables.sql` |
| Frontend model | `export interface X { ... }` plain TS types in `src/app/models/` | `frontend/src/app/models/skill.model.ts` |
| Frontend service | `@Injectable({providedIn:'root'})`, `inject(HttpClient)`, signals for state, `Observable` return + `tap/catchError/finalize` | `frontend/src/app/services/skill.service.ts` |
| Frontend route | Lazy-loaded standalone component in `src/app/app.routes.ts` | `frontend/src/app/app.routes.ts` |
| Frontend component | `@Component({standalone:true, imports:[...]})` + Angular Material modules + signals | `frontend/src/app/pages/skills/skills.component.ts` |
| Frontend nav | `<a routerLink="..." routerLinkActive="active" class="nav-link">` in `app.html` | `frontend/src/app/app.html` |

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Backend Persistence Layer | `skill_bank` table + `SkillBankItem` model + `SkillBankRepository` + factory + `__init__.py` registration + manager wiring + PG/SQLite DDL | None | — (root) | 1.5–2h |
| 2 | Backend API Layer | `/api/skill-bank` router (CRUD, no service layer) + registration in `__init__.py` + `api.py` | Phase 1 | **tight** (same table, router imports repo) | 1–1.5h |
| 3 | Frontend Skill Bank Page | `SkillBankItem` model + `SkillBankService` (frontend) + route `/skills/bank` + list/detail/create-edit page + nav link | Phase 2 | **loose** (depends only on the API contract, not Phase 2 code) | 2–3h |

### Coupling Assessment

| Pair | Coupling | Rationale |
|------|----------|-----------|
| Phase 1 → Phase 2 | **tight** | Phase 2 router imports the `SkillBankItem` model + `SkillBankRepository` class created in Phase 1, accesses `manager._skill_bank_repo` wired in Phase 1. Must run sequential. |
| Phase 2 → Phase 3 | **loose** | Frontend only needs the `/api/skill-bank/*` REST contract (request/response shapes). Can be built against a mock or the live API. Pipeline once the contract is agreed. |

**Scheduling:** Phase 1 → Phase 2 are sequential. Phase 3 can start as soon as the API contract from Phase 2 is finalized (even before Phase 2 is fully merged), but must be tested against a running backend.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| PostgreSQL CREATE TABLE DDL missing → table only exists via `SQLModel.metadata.create_all()` on fresh DBs | **high** — PG is primary DB; existing PG databases won't get the table | Add `CREATE TABLE IF NOT EXISTS skill_bank ...` to the `statements` array inside `_ensure_postgres_columns()` (method at `daemon/manager.py:2460`). This is the critical-notes-documented constraint (🔴 "Must use raw DDL for PG"). |
| Model not registered in `__init__.py` → table not created on fresh PostgreSQL | **high** — `SQLModel.metadata.create_all()` only discovers models that have been imported; without the `__init__.py` import the `SkillBankItem` table model is never registered in metadata | Add `SkillBankItem` to the imports in `daemon/repositories/__init__.py` (both the `from .skill.models import ...` block and `__all__`). This is the same pattern used for all other models (Skill, DependencyWatcher, etc.). |
| Migration `.sql` file silently no-ops on PostgreSQL | **med** — .sql runner skips non-SQLite engines (documented in manager.py comment) | Acceptable: the raw DDL in `_ensure_postgres_columns()` covers PG; the .sql file covers SQLite idempotency. Document this dual path in the migration header. |
| Accidental coupling to skill evolution system | **med** — could create circular imports or config-gating | The `skill_bank` model lives in `skill/models.py` (same file) but has NO FK to `skills` table. Repository is a separate file `skill_bank_repository.py`. Router accesses repo directly (no service layer). NOT gated by `config.skill_evolution`. |
| Frontend route `/skills/bank` conflicts with `/skills/:id` (Angular route order) | **med** — Angular matches `/skills/:id` before `/skills/bank` if order is wrong | Place `/skills/bank` route **before** `/skills/:id` in `app.routes.ts` (Angular evaluates routes top-to-bottom; static segments must precede parameterized ones). |
| Duplicate names allowed → confusion in UI | **low** | Design decision: no UNIQUE constraint on name. UI should show `category` + `created_at` to disambiguate. Document in the page. |
| `asyncio.to_thread` missing on repo calls | **low** | All repository methods are sync; endpoints MUST bridge with `await asyncio.to_thread(repo.method, ...)`. Follow `mcp_servers.py` pattern. |
| Write operations during DB migration | **med** — concurrent writes during schema migration can corrupt state | Add `is_write_paused` guard to ALL write endpoints (POST/PUT/DELETE), returning HTTP 503. Matches `mcp_servers.py` pattern exactly. |

## Success Criteria

- [ ] `skill_bank` table created on both SQLite and PostgreSQL (fresh + existing DBs)
- [ ] `SkillBankItem` imported in `daemon/repositories/__init__.py` (table discoverable by `create_all`)
- [ ] `GET /api/skill-bank` returns list (optionally filtered by project_id / category)
- [ ] `POST /api/skill-bank` creates a new skill bank item (201) — with Pydantic validation
- [ ] `GET /api/skill-bank/{id}` returns a single item (404 if missing)
- [ ] `PUT /api/skill-bank/{id}` updates fields (404 if missing)
- [ ] `DELETE /api/skill-bank/{id}` hard-deletes (404 if missing)
- [ ] All write endpoints (POST/PUT/DELETE) return 503 when `manager.is_write_paused`
- [ ] Feature works WITHOUT `skill_evolution` config enabled (isolated)
- [ ] Frontend `/skills/bank` page lists, creates, edits, and deletes bank items
- [ ] Navigation link present under Skills menu
- [ ] No imports from skill evolution services in skill bank code
- [ ] No `SkillBankService` class — router accesses repo directly

## Key Design Decisions (from Leader + Approver, verified)

1. **Table:** `skill_bank` — isolated, simple schema. No FK to `skills`.
2. **Not gated** by `config.skill_evolution` — standalone user feature.
3. **API path:** `/api/skill-bank/*`
4. **Frontend route:** `/skills/bank` (sub-page under Skills)
5. **Schema fields:** `id` (UUID4 PK), `project_id` (nullable, NULL=global), `name` (TEXT NOT NULL), `description` (TEXT, default `''`), `content` (TEXT NOT NULL), `category` (TEXT, default `'workflow'`), `created_at`, `updated_at`
6. **No UNIQUE on name** — duplicates allowed (user manages their own).
7. **No "skill-template" naming** — these are "skills inside the bank".
8. **No service layer** — router accesses `manager._skill_bank_repo` directly. Validation via Pydantic `Field(min_length=1)`. Matches `mcp_servers.py` pattern.
9. **`is_write_paused` guard** on all write endpoints — returns 503 when paused.

## Complete File Manifest

### New Files (10)

| # | File | Phase |
|---|------|-------|
| 1 | `daemon/repositories/skill/skill_bank_repository.py` | 1 |
| 2 | `daemon/routers/skill_bank.py` | 2 |
| 3 | `daemon/migrations/versions/20260713_000001_create_skill_bank.sql` | 1 |
| 4 | `frontend/src/app/models/skill-bank.model.ts` | 3 |
| 5 | `frontend/src/app/services/skill-bank.service.ts` | 3 |
| 6 | `frontend/src/app/pages/skill-bank/skill-bank.component.ts` | 3 |
| 7 | `frontend/src/app/pages/skill-bank/skill-bank.component.html` | 3 |
| 8 | `frontend/src/app/pages/skill-bank/skill-bank.component.scss` | 3 |

### Modified Files (9)

| # | File | Phase | Change |
|---|------|-------|--------|
| 1 | `daemon/repositories/skill/models.py` | 1 | Add `SkillBankItem` class |
| 2 | `daemon/repositories/__init__.py` | 1 | Add `SkillBankItem` to model imports + `__all__` (CRITICAL — enables `create_all` table discovery) |
| 3 | `daemon/repositories/factory.py` | 1 | Add `create_skill_bank_repository()` + import |
| 4 | `daemon/manager.py` | 1 | Wire repo (non-gated) + PG CREATE TABLE DDL in `_ensure_postgres_columns()` |
| 5 | `daemon/routers/__init__.py` | 2 | Import + export `skill_bank_router` |
| 6 | `daemon/api.py` | 2 | Import + `include_router(skill_bank_router)` |
| 7 | `frontend/src/app/app.routes.ts` | 3 | Add `/skills/bank` route (before `/skills/:id`) |
| 8 | `frontend/src/app/app.html` | 3 | Add nav link under Skills |

> **Note:** No `daemon/services/skill_bank_service.py` — the service layer was dropped per Approver decision. The router accesses the repository directly.

## Tracking

- **Created:** 2026-07-13
- **Last Updated:** 2026-07-13 (Revision 2 — Approver fixes applied)
- **Status:** active
- **Revision History:**
  - Rev 1 (2026-07-13): Initial draft.
  - Rev 2 (2026-07-13): Fixed 5 issues from Approver review:
    1. Dropped `SkillBankService` — router accesses repo directly.
    2. Added `daemon/repositories/__init__.py` import to manifest (critical for PG table creation).
    3. Added `is_write_paused` guard to all write endpoints.
    4. Fixed method name `_create_postgres_objects()` → `_ensure_postgres_columns()` (line 2460).
    5. Updated file manifest to reflect service removal + `__init__.py` addition.
