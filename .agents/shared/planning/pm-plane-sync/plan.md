# Plan Overview: PM-Plane Project Sync

Date: 2026-08-14
Author: planner[v2] via plan-creation worker
Status: Draft
Architecture Ref: `.agents/shared/planning/pm-system-improvement/project-sync-architecture.md`

## Objective

When an Ensemble project is created or updated, its core identity (name, description, status, tags) is automatically mirrored to a Plane project via direct REST API calls — without ever going through the MCP tool layer. PM stays read-only (Cardinal #1) and triggers manual sync by dispatching to leader. Plane data (issues, cycles, milestones) stays on Plane and is NOT synced in this phase.

## Scope

### In Scope
- **PlaneHttpClient** — new async HTTP client (`daemon/clients/plane_http_client.py`) calling Plane REST API directly using `httpx` (already a dependency — `pyproject.toml:43`)
- **PlaneSyncService** — new daemon service (`daemon/services/plane_sync_service.py`) orchestrating create/update sync, status mapping, metadata storage, circuit-breaker resilience
- **Auto-create hook** — fire-and-forget sync on project creation, mirroring the existing queue-provisioning pattern in both router and tool paths
- **Manual sync endpoint** — `POST /api/plane/sync/{project_id}` for PM-triggered sync via leader dispatch
- **Status mapping** — E→P mapping dict with docstrings (`active→backlog`, `paused→paused`, `completed→completed`, `archived→cancelled`)
- **Sync metadata** — 3 simplified keys in `project_metadata_records` table via `set_metadata_record()`
- **New constants** — `PLANE_PROJECT_ID_METADATA_KEY`, `PLANE_SYNC_STATE_METADATA_KEY`, `PLANE_SYNCED_AT_METADATA_KEY` in `daemon/constants.py`
- **Feature gating** — `PLANE_API_URL` env var; empty = feature disabled (same pattern as `PlaneServerDefinition.is_available()`)
- **PM prompt updates** — sync awareness in `workflow.md` (Flow 2, Flow 6); sync state checking via `project_get`
- **Unit + integration tests** — sync service tests, HTTP client tests with mocked Plane API, full round-trip test

### Out of Scope
- **Semantic sync (issues, cycles, milestones)** — deferred; architecture doc documents two viable paths requiring user decision (lines 111–187). Structural sync is the prerequisite for any future semantic sync.
- **Scheduled pull / drift detection** — architecture doc proposed a 15-min polling reconciler (line 92); deferred. Current scope is push-only (E→P).
- **Bidirectional sync** — explicitly out of scope. Ensemble is master; Plane edits to mapped fields do not propagate back.
- **Tags → Plane labels mapping** — architecture doc marks tags as E-only v1 (line 60). Tags are stored but not mapped to Plane labels.
- **Per-agent `read_only_tools` override** — architecture Path 2 (line 138). Not needed for structural sync (daemon bypasses MCP entirely).
- **Rich 8-key state machine** — architecture doc proposed 8 `plane_*` metadata keys (lines 70–77). This plan uses the simplified 3-key spec from the task. The 8-key model is noted as a future enhancement.
- **Modifying `plane.py` builtin MCP server** — sync is daemon-internal and bypasses MCP. The builtin server is NOT modified.

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 0 | Plane API Contract Verification | Verify the inferred REST API endpoints before building on them | 2 | independent | pending |
| 1 | Plane HTTP Client | Build the async HTTP client with circuit breaker resilience and feature gating | 5 | independent | pending |
| 2 | Plane Sync Service | Build the orchestration service: field mapping, metadata storage, error handling | 6 | tight with Phase 1 (depends on client) | pending |
| 3 | Auto-Create Hook | Wire fire-and-forget sync into both project creation paths | 4 | tight with Phase 2 (depends on service) | pending |
| 4 | Manual Sync Endpoint | Add `POST /api/plane/sync/{project_id}` daemon endpoint | 3 | tight with Phase 2 (depends on service) | pending |
| 5 | PM Prompt Updates | Add sync awareness to PM workflow.md and tools_note.md | 3 | loose with Phase 4 (PM dispatches to endpoint) | pending |
| 6 | Testing | Unit tests, integration tests, error-path coverage | 5 | tight with Phases 1-4 | pending |

## Coupling Map

| | Phase 0 | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 | Phase 6 |
|---|---|---|---|---|---|---|---|
| Phase 0 | — | independent* | independent* | independent* | independent* | independent | independent* |
| Phase 1 | independent* | — | tight | independent* | independent* | independent | tight |
| Phase 2 | independent* | tight | — | tight | tight | independent | tight |
| Phase 3 | independent* | independent* | tight | — | independent | loose | tight |
| Phase 4 | independent* | independent* | tight | independent | — | loose | tight |
| Phase 5 | independent | independent | independent | loose | loose | — | independent |
| Phase 6 | independent* | tight | tight | tight | tight | independent | — |

\* Phase 0 can be done in parallel with Phases 1–4, but **Phase 1 task 1.2 and Phase 2 task 2.3 must incorporate Phase 0 findings** before the client and service are finalized. Phase 0 is a go/no-go gate: if the REST API contract is materially different from what's inferred, Phases 1–2 need adjustment.

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **Plane REST API contract is INFERRED, not verified** — endpoints, auth headers, request/response shapes are deduced from the MCP tool surface (architecture doc line 288). Actual API may differ. | High | Medium | Phase 0 explicitly verifies the contract via manual curl + test. Any deviation discovered feeds back into Phase 1/2 before implementation is finalized. |
| 2 | **Auto-create hook blocks project creation** — if sync is not properly fire-and-forget, a slow/unavailable Plane API delays or fails project creation. | High | Low | Mirror the proven `asyncio.ensure_future()` / `background_tasks.add_task()` pattern from queue provisioning (`daemon/tools/project.py:390-410`). Circuit breaker short-circuits when OPEN. All sync errors caught and logged, never re-raised. |
| 3 | **Plane API returns unexpected response shapes** — 200 with error body, missing fields, or unexpected content-type. | Medium | Medium | Defensive response parsing: validate status code, check for expected fields, log unexpected shapes at WARNING. Circuit breaker records failure on malformed responses. |
| 4 | **Workspace slug / auth header format differs** — `PLANE_MCP_WORKSPACE_SLUG` may need to be sent differently for REST vs MCP. | Medium | Medium | Phase 0 verifies exact header format. Client header construction is centralized in one method for easy adjustment. |
| 5 | **`PLANE_API_URL` not set in most deployments** — feature silently disabled. Operators expect sync but don't realize they need the new env var. | Low | High | Feature disabled = documented behavior (same as MCP server `is_available()`). Log INFO on daemon startup: "Plane sync disabled (PLANE_API_URL not set)". Document in README/env docs. |
| 6 | **Status mapping surprises operators** — `archived→cancelled` may confuse users who expect archived projects to remain visible in Plane. | Low | Medium | Document mapping in code docstrings, in the plan, and in PM workflow guidance. PM Flow 2 (Progress Reporting) notes sync state. |
| 7 | **Concurrent sync attempts for same project** — auto-create + manual sync fire simultaneously. | Low | Low | Circuit breaker serializes attempts. Sync service is idempotent: if `plane_project_id` already exists in metadata, update path is used instead of create. |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Project creation succeeds when Plane is unavailable | Create project with `PLANE_API_URL` unset or pointing to dead host | Project created, response <200ms, `plane_sync_state="error"` or metadata absent, no exception raised |
| 2 | Project creation succeeds when Plane is available | Create project with valid `PLANE_API_URL` | Project created, Plane project exists with matching name, `plane_sync_state="synced"`, `plane_project_id` stored |
| 3 | Manual sync endpoint updates existing project | `POST /api/plane/sync/{project_id}` after changing project name | Plane project name updated, `plane_synced_at` refreshed |
| 4 | Feature is disabled when `PLANE_API_URL` is empty | Start daemon without `PLANE_API_URL` | No sync attempts, no errors logged, daemon starts normally |
| 5 | Circuit breaker opens after repeated failures | Simulate 5+ consecutive Plane API failures | `can_execute()` returns False, subsequent sync attempts short-circuit without HTTP call, daemon log shows circuit OPEN |
| 6 | Status mapping is correct | Create/sync projects with each Ensemble status | Each maps to expected Plane state (backlog/paused/completed/cancelled) |
| 7 | PM can read sync state | PM calls `project_get` on a synced project | `plane_sync_state` visible in metadata, PM can detect "error" state |
| 8 | No MCP layer involvement | Search code for MCP tool calls in sync path | Zero references to `mcp_*` or `PlaneServerDefinition` in sync service/client |
| 9 | Dual-driver DB compatibility | Run metadata read/write tests against SQLite and PostgreSQL | All tests pass on both drivers |
| 10 | Full e2e test passes | Run the mandatory e2e test if changes touch job/task/queue system | Per `.agents/tester/rules/ensure.md` — only if Phase 3 hook touches those systems |

## Research Insights

- **Two creation paths** (`R1`): Router `POST /api/projects` (`daemon/routers/projects.py:203-257`) and tool `project_create` (`daemon/tools/project.py:358-426`). Both must fire the hook, or the hook must be at the repository layer. The fire-and-forget queue-provisioning pattern in the tool path (`asyncio.ensure_future()` if loop running, else `ThreadPoolExecutor + asyncio.run()`) is the exact pattern to mirror.
- **CircuitBreaker** (`R2`): `daemon/sources/circuit_breaker.py` — dataclass with `can_execute()` / `record_success()` / `record_failure()`. Thread-safe via `asyncio.Lock`. Constructor: `CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)`. On service restart, call `reset()`. Excludes 429 (rate limit) and 4xx permanent errors from failure counting.
- **Metadata storage** (`R3`): `project_metadata_records` table, NOT the JSONB `metadata` column. Method: `set_metadata_record(session, project_id, key, value)` — atomic upsert with `on_conflict_do_update` (`daemon/repositories/project/repository.py:797-816`). Higher-level convenience: `set_metadata(project_id, key, value)` opens its own session (`repository.py:851-864`). Existing key pattern: `BLUEPRINT_ACTIVE_METADATA_KEY`, `EDITOR_METADATA_KEY`, `DEFAULT_AGENT_VERSIONS_METADATA_KEY` (all in `daemon/constants.py:96,126,133`).
- **Plane env vars** (`R4`): `PLANE_MCP_URL` (MCP endpoint), `PLANE_MCP_API_KEY` (Bearer token), `PLANE_MCP_WORKSPACE_SLUG` (workspace). `is_available()` checks URL + API key. **Critical**: MCP URL ≠ REST API base URL. New env var `PLANE_API_URL` needed (architecture doc recommends this — line 288). REST endpoints are under `/api/v1/workspaces/{slug}/projects/`.
- **PM agent config** (`R5`): `team_members: ["leader"]`, PM dispatches via Flow 5 (spawn/reuse leader → send_message → END TURN). PM reads metadata via `project_get`. PM needs NO meta.json changes — it stays read-only and dispatches to leader for writes. `workflow.md` has 8 flows; Flow 2 (Progress Reporting) and Flow 6 (Roadmap) need sync awareness added.
- **Status mapping** (`R6`): Ensemble has `active`, `paused`, `completed`, `archived` (`daemon/repositories/project/models.py:74-79`). Architecture doc line 57 specifies the mapping; line 308 flags `active→backlog` vs `active→planned` as a decision point. Recommendation: `active→backlog` (Plane's default for new projects).

## Open Questions

1. **`PLANE_API_URL` default** — Should we auto-derive from `PLANE_MCP_URL` (e.g., strip `/mcp` suffix, replace host)? Recommendation: NO — keep `PLANE_API_URL` as a standalone env var. Derivation is fragile (URL structure assumptions). If unset, feature disabled. Operator sets it explicitly.
2. **Rate limiting** — Does Plane REST API have rate limits? The circuit breaker excludes 429 from failure counting (`R2`). If Plane does rate-limit, we may need an explicit rate limiter (token bucket) in the HTTP client. For v1, circuit breaker is sufficient; add rate limiter if 429s appear in production.
3. **Retry strategy for auto-create** — When auto-create fails (Plane down), state is set to "error". Is there an automatic retry? For v1: no automatic retry. PM or operator triggers manual sync via `POST /api/plane/sync/{project_id}` when Plane is back. Future: scheduled pull reconciler (architecture doc line 92).
4. **`project_type` → Plane field** — Architecture doc (line 56) suggests prefixing description with `[type=software]` if no Plane equivalent. Should this be in v1? Recommendation: YES, include as it's a simple one-line operation and preserves type information on the Plane side.
5. **E2e test trigger** — Does Phase 3 (auto-create hook) touch the job/task/queue system? The hook fires fire-and-forget like queue provisioning, but does NOT interact with the job queue itself. If it only calls the sync service directly (not through a job), the mandatory e2e test (`.agents/tester/rules/ensure.md`) may not apply. Confirm during Phase 3 implementation.

---

# Phase Details

---

# Phase 0: Plane API Contract Verification

## Objective
Verify the inferred Plane REST API endpoints, auth headers, and request/response shapes before building client and service on unverified assumptions. This is the risk-mitigation gate identified in the architecture doc (line 288).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 0.1 | Write a manual curl/script test that exercises the four REST endpoints against a live (or staging) Plane instance: `POST /api/v1/workspaces/{slug}/projects/` (create), `PATCH /api/v1/workspaces/{slug}/projects/{project_id}/` (update), `GET /api/v1/workspaces/{slug}/projects/{project_id}/` (get), `GET /api/v1/workspaces/{slug}/projects/` (list) | none | Script runs successfully against Plane; response bodies captured for all four endpoints. Auth header format confirmed (`Authorization: Bearer <key>` + optionally `x-workspace-slug`). |
| 0.2 | Document findings: exact auth headers required, request body field names for create/update (e.g., is it `name` or `project_name`? `state` or `status`? what's the identifier field in the response?), response shape for each endpoint, and any discrepancies from the inferred contract | 0.1 | Findings written to `.agents/shared/planning/pm-plane-sync/api-contract-notes.md`. Any deviations from the plan's assumed field names are flagged for Phase 1/2 adjustment. |

## Coupling
- **Independent of:** All other phases (can run in parallel)
- **Gates:** Phase 1 task 1.2 (request/response model) and Phase 2 task 2.3 (field mapping) must incorporate Phase 0 findings

## Risks
- If the Plane REST API is materially different (different base path, different auth mechanism, different field names), Phases 1–2 need revision. This is expected and acceptable — the point of Phase 0 is to catch this early.

## Exit Criterion
A documented, verified API contract exists in `.agents/shared/planning/pm-plane-sync/api-contract-notes.md` with real response examples. Phases 1–2 can proceed with confidence.

---

# Phase 1: Plane HTTP Client

## Objective
Build `PlaneHttpClient` — a resilient async HTTP client that calls Plane REST API directly, with circuit breaker protection and feature gating.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1.1 | Create `daemon/clients/__init__.py` (if not exists) and `daemon/clients/plane_http_client.py`. Define `PlaneHttpClient` class with `__init__` reading env vars: `PLANE_API_URL` (NEW), `PLANE_MCP_API_KEY` (reuse), `PLANE_MCP_WORKSPACE_SLUG` (reuse). Instantiate `CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)` from `daemon/sources/circuit_breaker.py`. Add `is_enabled()` classmethod mirroring `PlaneServerDefinition.is_available()` pattern: returns True only when `PLANE_API_URL` is set and non-empty. | none | File exists, class imports cleanly, `is_enabled()` returns False when `PLANE_API_URL` unset, True when set |
| 1.2 | Implement the four API methods using `httpx.AsyncClient`: `create_project(name, description, state, ...)` → POST, `update_project(plane_project_id, ...)` → PATCH, `get_project(plane_project_id)` → GET, `list_projects()` → GET. Each method: (a) check `circuit_breaker.can_execute()` — return None/raise if circuit OPEN; (b) construct request with auth headers (`Authorization: Bearer <key>`, `x-workspace-slug: <slug>`); (c) call `record_success()` on 2xx or `record_failure()` on 5xx/timeout/connection error; (d) parse and return response JSON. Incorporate Phase 0 findings for exact field names and response shapes. | 1.1, 0.2 | All four methods implemented. Each wraps the HTTP call in circuit-breaker pattern. Returns parsed JSON dict on success. On circuit OPEN: returns None (graceful degradation). |
| 1.3 | Implement error handling: catch `httpx.TimeoutException`, `httpx.ConnectError`, `httpx.HTTPStatusError` (5xx only). Do NOT count 429 (rate limit) or 4xx as circuit failures (per `R2` exclusion rules). Log at WARNING level with context (endpoint, status code). Raise a custom `PlaneApiError` exception for callers to handle. | 1.2 | Error handling covers all httpx exception types. 429/4xx do NOT trip circuit breaker. Custom exception is importable from the module. |
| 1.4 | Implement `close()` / async context manager support for clean `httpx.AsyncClient` lifecycle. Add `reset()` method that calls `circuit_breaker.reset()` (per `R2` — clear failure tracking on service restart). | 1.1 | Client supports `async with` usage. `reset()` clears circuit breaker state. |
| 1.5 | Write unit tests in `tests/unit/clients/test_plane_http_client.py` using `httpx.MockTransport` (or `respx`) to mock Plane API responses. Test: (a) successful create/get/update/list; (b) circuit breaker opens after 5 failures; (c) graceful degradation when circuit OPEN; (d) 429 does NOT trip circuit; (e) `is_enabled()` gating. | 1.2, 1.3, 1.4 | All unit tests pass. Circuit breaker behavior verified. Error classification verified (5xx trips, 429 doesn't). |

## Coupling
- **Tight with:** Phase 2 (PlaneSyncService depends on PlaneHttpClient)
- **Independent of:** Phase 0 (but task 1.2 incorporates Phase 0 findings)

## Risks
- If `httpx.AsyncClient` lifecycle needs to be shared across requests (connection pooling), the client must be a long-lived singleton, not per-request. Design as a module-level singleton initialized at daemon startup (same pattern as other daemon services).

## Exit Criterion
`PlaneHttpClient` is implemented, tested, and can successfully communicate with a mock Plane API. Circuit breaker opens/closes correctly. Feature gating works. Phase 2 can build on this client.

---

# Phase 2: Plane Sync Service

## Objective
Build `PlaneSyncService` — the orchestration layer that maps Ensemble project fields to Plane fields, calls the HTTP client, stores sync metadata, and handles errors gracefully.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 2.1 | Create `daemon/services/plane_sync_service.py`. Define `PlaneSyncService` class. Constructor takes `PlaneHttpClient` instance (injected) and `SQLModelProjectRepository` (or engine) for metadata access. Instantiate as module-level singleton (same pattern as other daemon services). Add `is_enabled()` property delegating to `client.is_enabled()`. | Phase 1 | File exists, class imports cleanly, singleton pattern established |
| 2.2 | Add constants to `daemon/constants.py` (following existing pattern at lines 96, 126, 133): `PLANE_PROJECT_ID_METADATA_KEY = "plane_project_id"`, `PLANE_SYNC_STATE_METADATA_KEY = "plane_sync_state"`, `PLANE_SYNCED_AT_METADATA_KEY = "plane_synced_at"`. Add `PLANE_API_URL` env var name constant if desired for consistency. | none | Constants defined, importable, follow existing naming convention |
| 2.3 | Implement `sync_project(project_id, project_data)` — the main orchestration method. Steps: (a) check `is_enabled()` — return early if disabled; (b) check if `plane_project_id` exists in metadata (via `get_metadata()`); (c) if no mapping → call `client.create_project()`; if mapping exists → call `client.update_project()`; (d) map Ensemble fields to Plane fields (see task 2.4); (e) on success: store `plane_project_id`, `plane_sync_state="synced"`, `plane_synced_at=now` via `set_metadata()`; (f) on failure: store `plane_sync_state="error"` and continue (never raise). Incorporate Phase 0 findings for exact Plane field names. | 2.1, 2.2, 0.2 | Method handles create-vs-update branching. Success and error paths both store metadata. Never raises to caller (all exceptions caught). |
| 2.4 | Implement status mapping dict in the service (or a standalone module `daemon/services/plane_status_mapper.py`): `STATUS_MAP = {"active": "backlog", "paused": "paused", "completed": "completed", "archived": "cancelled"}`. Add docstring documenting the mapping and the `active→backlog` decision (Plane's default for new projects). Add `map_status(ensemble_status) -> str` helper with validation (raises ValueError on unknown status, or logs warning and defaults to "backlog"). | 2.1 | Mapping dict defined, documented, unit-testable. Unknown status handled gracefully. |
| 2.5 | Implement field mapping logic in `sync_project`: Ensemble `name` → Plane `name`, `description` → `description` (optionally prefixed with `[type=<project_type>]` per architecture doc line 56), `status` → `state` (via `map_status()`). Tags are stored in Ensemble metadata but NOT mapped to Plane labels in v1. | 2.3, 2.4 | All four core fields mapped correctly. Type prefix included. Tags explicitly documented as v1-unmapped. |
| 2.6 | Implement `get_sync_state(project_id) -> dict` — convenience method returning `{"plane_project_id": ..., "plane_sync_state": ..., "plane_synced_at": ...}` by reading the 3 metadata keys. Returns `{"plane_sync_state": "unlinked"}` if no metadata exists. | 2.1, 2.2 | Method returns structured sync state. Handles missing metadata gracefully. |

## Coupling
- **Tight with:** Phase 1 (depends on PlaneHttpClient)
- **Tight with:** Phase 3 (auto-create hook calls this service)
- **Tight with:** Phase 4 (manual sync endpoint calls this service)

## Risks
- **Metadata write conflicts** — if `set_metadata()` is called concurrently for the same project. Mitigation: `set_metadata_record` uses `on_conflict_do_update` (atomic upsert, `repository.py:810-813`), so concurrent writes resolve to last-write-wins. For sync metadata this is acceptable (idempotent).
- **Project deleted between read and sync** — if the Ensemble project is deleted after the hook fires but before sync completes, `get_metadata` / `set_metadata` will return None. Handle: check return value, log warning, skip sync.

## Exit Criterion
`PlaneSyncService.sync_project()` successfully creates a Plane project (mock), stores metadata, handles errors without raising. Status mapping is correct and documented. Phase 3 can wire the hook.

---

# Phase 3: Auto-Create Hook

## Objective
Wire fire-and-forget Plane sync into both project creation paths (router and tool) so every new project is mirrored to Plane automatically.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 3.1 | Add hook to **router path** (`daemon/routers/projects.py` `create_project` handler, around line 250 where queue provisioning fires). After `repo.create()` succeeds and before the response is returned, add: `background_tasks.add_task(plane_sync_service.sync_project, project.project_id, project.to_dict())` — mirroring the existing `background_tasks.add_task(queue_mgmt.auto_provision_system_queues, ...)` pattern at line 250-252. Guard with `if plane_sync_service.is_enabled():` so disabled feature = zero overhead. | Phase 2 | Router creates project, fires sync via BackgroundTasks, returns 201 immediately without waiting for sync. Project creation latency unaffected. |
| 3.2 | Add hook to **tool path** (`daemon/tools/project.py` `project_create` tool, around line 406 where queue provisioning fires). After `store.create()` succeeds and queues are provisioned, add the same fire-and-forget sync. Use the tool path's pattern: `asyncio.ensure_future()` if loop running, else `ThreadPoolExecutor + asyncio.run()`. Guard with `if plane_sync_service.is_enabled():`. Import the service at module level (or lazy import to avoid circular dependency). | Phase 2 | Tool creates project, fires sync fire-and-forget, returns `project.to_dict()` immediately. Sync runs in background. |
| 3.3 | Wire `PlaneSyncService` initialization into daemon startup. In `daemon/manager.py` (or wherever services are initialized), instantiate `PlaneHttpClient` and `PlaneSyncService` at startup. Export the singleton for import by the router and tool paths. Add startup log: `"Plane sync enabled"` or `"Plane sync disabled (PLANE_API_URL not set)"` at INFO level. | Phase 1, Phase 2 | Service initialized at daemon startup. Singleton accessible from router and tool paths. Startup log confirms feature state. |
| 3.4 | Verify the hook does NOT block: write a test that measures project creation latency with Plane sync enabled (mocked slow Plane API, e.g., 5s response time) and confirms the creation response returns in <500ms. | 3.1, 3.2 | Test confirms project creation returns promptly regardless of Plane API latency. Sync runs in background. |

## Coupling
- **Tight with:** Phase 2 (depends on PlaneSyncService)
- **Loose with:** Phase 5 (PM prompt can reference sync state set by this hook)

## Risks
- **Circular import** — `daemon/tools/project.py` importing `daemon/services/plane_sync_service.py` which might import project repository. Mitigation: use lazy import inside the hook function, or inject the service via the existing factory pattern (`create_project_tools()` receives dependencies).
- **Thread safety of metadata writes** — the fire-and-forget task runs in an event loop or thread pool. `set_metadata()` opens its own session (`repository.py:851-864`), so each call is self-contained. SQLModel sessions are not thread-safe, but each call creates its own — safe.

## Exit Criterion
Both creation paths (router + tool) fire background Plane sync on project creation. Project creation latency is unaffected. Feature is disabled cleanly when `PLANE_API_URL` is not set.

---

# Phase 4: Manual Sync Endpoint

## Objective
Add a daemon HTTP endpoint `POST /api/plane/sync/{project_id}` that triggers sync for an existing project. PM dispatches to leader, leader calls this endpoint.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 4.1 | Create `daemon/routers/plane_sync.py` (new router). Define `POST /api/plane/sync/{project_id}` endpoint. Implementation: (a) load project from repo; (b) call `plane_sync_service.sync_project(project_id, project.to_dict())`; (c) return sync state via `plane_sync_service.get_sync_state(project_id)` as JSON response. Return 200 on success, 404 if project not found, 503 if Plane sync disabled. Use `APIRouter(prefix="/plane", tags=["plane"])`. | Phase 2 | Endpoint returns 200 with sync state on success. 404 for unknown project. 503 when feature disabled. |
| 4.2 | Register the router in `daemon/api.py` (or wherever routers are included). Follow existing pattern: `app.include_router(plane_sync_router, prefix="/api")`. | 4.1 | Router is accessible at `/api/plane/sync/{project_id}`. Visible in `/docs` OpenAPI schema. |
| 4.3 | Write API tests in `tests/api/test_plane_sync.py` following the pattern in `tests/api/test_plane_settings.py` (httpx.AsyncClient + ASGITransport). Test: (a) successful sync (mock PlaneSyncService); (b) 404 for unknown project; (c) 503 when disabled; (d) sync state returned correctly. | 4.1, 4.2 | All API tests pass. Endpoint behavior verified end-to-end through FastAPI test client. |

## Coupling
- **Tight with:** Phase 2 (depends on PlaneSyncService)
- **Loose with:** Phase 5 (PM dispatches to leader which calls this endpoint)

## Risks
- **Endpoint authorization** — should this endpoint be protected? Existing project endpoints (`POST /api/projects`) have no auth middleware in dev. Follow the same pattern for consistency. Production hardening is a separate concern.

## Exit Criterion
`POST /api/plane/sync/{project_id}` works correctly. Returns sync state. Handles errors (404, 503). Phase 5 PM prompt can reference this endpoint.

---

# Phase 5: PM Prompt Updates

## Objective
Update PM's workflow documentation so PM is aware of Plane sync, can read sync state, and can trigger manual sync via dispatch to leader.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 5.1 | Update `agents/project-manager/workflow.md` **Flow 2 (Progress Reporting)** (currently at line 43). Add a step after step 3: "Check `plane_sync_state` via `project_get`. If 'error' or absent, note the data gap — Plane data may be stale or unavailable. Do NOT attempt to fix the sync yourself (Cardinal #1)." | Phase 4 | Flow 2 includes sync state checking. PM knows to flag sync errors in progress reports. |
| 5.2 | Update `agents/project-manager/workflow.md` **Flow 6 (Roadmap Generation)** (currently at line 119). Add guidance: "If `plane_sync_state` is 'error', the Plane project may not exist or may be stale. Flag this as a data quality issue. To trigger re-sync, dispatch to leader via Flow 5 with message: 'Sync project `<name>` to Plane — call POST /api/plane/sync/`<project_id>`'." | Phase 4 | Flow 6 includes sync trigger guidance. PM knows the dispatch pattern for manual sync. |
| 5.3 | Update `agents/project-manager/rule.md` (or `tools_note.md` if it exists) to document: (a) the 3 `plane_*` metadata keys and their meanings; (b) the E→P status mapping; (c) the sync trigger flow (PM → leader → `POST /api/plane/sync/{project_id}`). No meta.json changes — PM stays read-only. | Phase 4 | Sync documentation exists in PM's reference files. PM has all information needed to understand and trigger sync. |

## Coupling
- **Loose with:** Phase 4 (PM dispatches to leader which calls the endpoint from Phase 4)
- **Loose with:** Phase 3 (PM reads sync state set by the auto-create hook from Phase 3)
- **Independent of:** Phases 0-2 (PM doesn't interact with sync internals)

## Risks
- **PM attempts to write** — PM might try to call a Plane write tool directly instead of dispatching. Mitigation: PM's `tools.deny` already blocks all `plane_create_*`/`plane_update_*`/`plane_delete_*` tools. The workflow.md guidance explicitly states "dispatch to leader" and "Cardinal #1".

## Exit Criterion
PM workflow.md and reference docs include sync awareness. PM can read sync state, flag errors, and trigger manual sync via dispatch. No meta.json changes. Cardinal #1 preserved.

---

# Phase 6: Testing

## Objective
Comprehensive test coverage for the Plane sync feature: unit tests for each component, integration tests for the full round-trip, and error-path coverage.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 6.1 | **Unit tests for PlaneSyncService** in `tests/unit/services/test_plane_sync_service.py`. Test: (a) `sync_project` creates Plane project when no mapping exists; (b) `sync_project` updates Plane project when mapping exists; (c) status mapping for all 4 statuses; (d) metadata stored correctly on success; (e) metadata set to "error" on failure; (f) `is_enabled()` gating; (g) `get_sync_state()` for synced/unsynced/error projects. Mock `PlaneHttpClient` and project repository. | Phase 2 | All unit tests pass. Service logic fully covered including all error paths. |
| 6.2 | **Unit tests for PlaneHttpClient** in `tests/unit/clients/test_plane_http_client.py` (from Phase 1 task 1.5). Ensure comprehensive coverage: all four methods, circuit breaker open/close/recovery, error classification (5xx trips, 429 doesn't), feature gating. | Phase 1 | All unit tests pass. Client behavior fully verified in isolation. |
| 6.3 | **Integration test for auto-create hook** in `tests/integration/test_plane_auto_sync.py`. Test the full flow: create project via API → verify sync fires → verify metadata stored. Mock the Plane HTTP layer (use `httpx.MockTransport` or mock `PlaneHttpClient`). Verify project creation is NOT blocked by sync. | Phase 3 | Integration test passes. Full auto-create flow verified end-to-end. |
| 6.4 | **Integration test for manual sync endpoint** in `tests/api/test_plane_sync.py` (from Phase 4 task 4.3). Test: successful sync, 404, 503, sync state response. Follow `tests/api/test_plane_settings.py` pattern (httpx.AsyncClient + ASGITransport). | Phase 4 | All API tests pass. Endpoint behavior verified end-to-end. |
| 6.5 | **Dual-driver metadata test**: verify `set_metadata` / `get_metadata` for `plane_*` keys works on both SQLite and PostgreSQL. Follow existing dual-driver test patterns. Use `@pytest.mark.postgres` marker for PostgreSQL variant. | Phase 2 | Metadata read/write works on both drivers. Tests pass with default SQLite config and with PostgreSQL (`pytest -m postgres`). |

## Coupling
- **Tight with:** Phases 1-4 (tests verify all prior work)

## Risks
- **Mock fidelity** — mocked Plane API may not match real API behavior. Mitigation: Phase 0 contract verification provides real response examples to base mocks on. Phase 0 is the foundation for realistic test mocks.
- **E2e test requirement** — per the critical notes (`.agents/tester/rules/ensure.md`), a full e2e test is MANDATORY if changes touch the job/task/queue system. The auto-create hook mirrors the queue-provisioning pattern but does NOT interact with the job queue itself — it calls the sync service directly via `background_tasks` / `asyncio.ensure_future`. Confirm during Phase 3 implementation whether the e2e test applies. If it does, run it.

## Exit Criterion
All unit and integration tests pass. Circuit breaker behavior verified. Feature gating verified. Error paths covered. Dual-driver compatibility confirmed. The feature is ready for deployment.
