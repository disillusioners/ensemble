# Research Findings: Maintenance Console — Section 1 (Checkpoint Cleanup)

Date: 2026-09-27
Author: planner[v2] via overview worker
Source: BE core + frontend + API/tests explorer reports (injected in dispatch). BE-core and API/tests reports were truncated mid-section in the injection; load-bearing claims verified directly against the worktree at `feature/maintenance-console @ 666c089d`. All file:line refs are pinned to the post-merge tree.

> **Reading order for phase workers.** Start here, then jump to `plan-overview.md` for the deliverable contract. Phase 1 (BE) MUST read §1 + §2 + §4. Phase 2 (FE) MUST read §3 + §4 + §5.

---

## §1. Backend Core (Checkpoint Cleanup)

### 1.1 MaintenanceService (the source of truth)

`daemon/services/maintenance.py` owns the existing auto cycle. The section boundary for our work is lines **800–940** (Op D row prune) and **909–934** (Op E blob prune). Other regions of `maintenance.py` (instance cleanup, repair, repair log) are OUT OF SCOPE.

| Symbol | Location | Behavior |
|---|---|---|
| `_is_idle()` | `daemon/services/maintenance.py:258` | async; gates auto-cycle. Wraps `list_all_pending` on the job queue + checks workers. Has known blind-spots (see maintenance.py:107 / 167 / 228 docblocks). **Advisory only for manual path** — see Risk R-2. |
| `_prune_thread_checkpoints` (Op D) | `daemon/services/maintenance.py:~810-895` | Calls `find_excess_checkpoint_groups(N)`, then per-pair `delete_checkpoints_excluding` + `delete_writes_excluding`. Running-total pattern (`observed_total_deleted`) so partial progress is logged. **No structured summary returned today** — just DEBUG/INFO logs. |
| `_prune_unreferenced_blobs` (Op E) | `daemon/services/maintenance.py:909-934` | Calls `await prune_unreferenced_blobs(self._checkpointer)` then **discards the `BlobPruneSummary` return value** (maintenance.py:934). PostgreSQL-only — no-ops with WARNING on SQLite (checkpoint_prune.py:130-137). This is the leak we MUST fix. |
| `_config.checkpoint_max_per_thread` | `daemon/constants.py:54` (constant + env name `CHECKPOINT_MAX_PER_THREAD`); `ge=1` enforced via pydantic (commit be6ff625). Default 3. Floor 1 prevents no-op wedge (`find_excess_checkpoint_groups(N=0)` returns nothing — maintenance.py:803). | Configurable; surfaces on `/status` response. |

### 1.2 checkpoint_prune.py (the algorithm)

`daemon/services/checkpoint_prune.py:88-103` — `BlobPruneSummary` dataclass:

```
@dataclass
class BlobPruneSummary:
    backend: str = "postgres"
    dry_run: bool = True
    scanned_pairs: int = 0
    total_deleted: int = 0
    total_bytes_freed: int = 0
    skipped: list[tuple[str, str, str]] = field(default_factory=list)
    # skipped: (thread_id, checkpoint_ns, reason)

    @property
    def destructive(self) -> bool:
        return not self.dry_run
```

Key constants / predicates:

- `_BLOB_ANTI_JOIN_PREDICATE` (`daemon/services/checkpoint_prune.py:58`) — SQL anti-join text; never log the full text (security posture: predicate strings reveal schema).
- `CHECKPOINT_BLOB_PRUNE_DRY_RUN` (env, default `"1"` — read at CALL time, not boot time). `daemon/services/checkpoint_prune.py:67,75-80`.
- `CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD` (constant) — fail-safe cap on per-thread refs.
- `ZERO_REFS_FAIL_SAFE` skip reason — channel_versions extraction yielded 0 refs while checkpoints remain (schema-drift detection). Loud ERROR + skip pair, no deletes. (`daemon/services/checkpoint_prune.py:170-186`).
- `MAX_REFS_EXCEEDED` skip reason — refs > max; skip pair.
- `blob_prune_destructive_enabled()` gate (module-level) — BOTH `CHECKPOINT_BLOB_PRUNE_DRY_RUN=0` AND `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1` required. Structurally unreachable otherwise.

`prune_unreferenced_blobs(checkpointer, *, max_refs_per_thread=...) -> BlobPruneSummary` (line 104). Already has `destructive: bool` (read from env) but currently NEVER overrides it. **The fix**: add a `destructive: bool | None = None` kwarg that overrides the env gate (manual UI path). Keep the env-only path for auto-cycle. Per-pair SERIALIZABLE+retry wrap + `ZERO_REFS_FAIL_SAFE` skip MUST stay unchanged.

### 1.3 CheckpointAdapter (PostgresCheckpointerAdapter)

`daemon/services/checkpoint_prune.py:104` calls into adapter methods that already exist:

| Method | Source | Purpose |
|---|---|---|
| `find_all_thread_ns_pairs()` | adapter | D21 — enumerate ALL (thread_id, ns) pairs (not just excess). Needed for blob prune so single-checkpoint threads still get scanned. |
| `count_refs_for_blob_thread(thread, ns)` | adapter | Per-pair ref count — fail-safe pre-check. |
| `find_excess_checkpoint_groups(N)` | adapter | Op D — HAVING-aggregated excess enumeration. |
| `delete_checkpoints_excluding(thread, ns, keep_ids)` | adapter | Op D destructive arm. |
| `delete_writes_excluding(thread, ns, keep_ids)` | adapter | Op D destructive arm. |

**Gap.** `find_excess_checkpoint_groups(N)` returns the would-delete count as `sum(cnt - N)` per pair, but no aggregate summary class exists. We need a new `CheckpointRowPruneSummary` dataclass (analogous to `BlobPruneSummary`) to capture Op D structured counts. Already needed: `count_writes_for_thread_ns` if we want pre-delete write-counts (currently we get them via the destructive DELETE return).

### 1.4 Just-shipped context (commit 666c089d)

Auto-cycle already works and is shipped. The two ops it runs:

- **Op D**: prune checkpoint rows/writes beyond newest-N per (thread_id, ns). Adapter already supports the read + write sides.
- **Op E**: prune unreferenced blobs (`prune_unreferenced_blobs`). Dry-run default. Results emitted as ONE INFO line/sweep via `log_blob_prune`; per-pair logs gated by `CHECKPOINT_PERF_LOGS`.

### 1.5 Migration router precedent (the manual-action pattern)

`daemon/routers/migration.py:120-170`:

```
@router.get("/availability")           # always 200; returns {can_migrate, reasons}
@router.post("/start", status_code=202) # returns {migration_id, status: "running", message}
@router.get("/status")                 # returns latest snapshot
@router.post("/cancel", responses={200,...})
@router.get("/events")                 # SSE stream
```

- Validate preconditions synchronously → 400/409 returned in HTTP response (NOT async failure).
- Returns 202 with a `migration_id` for correlation.
- Worker manages its own progress; router just delegates.
- MigrationWorker pattern: 503-style "not configured" response via dependency injection (`get_migration_worker`).

This is the strongest precedent for "long-running manual action with structured result".

---

## §2. API Surface (Routers, Errors, Schemas)

### 2.1 Router registration seam

`daemon/api.py:2687-2726` — flat APIRouter set registered BEFORE the SPA catch-all (`@app.get("/{path:path}")` at api.py:2816-2828). Hard rule: any new router MUST mount here or 404.

Current registered routers include `migration_router`, `database_router`, `settings_router`, `recovery_router`, `plane_router`, `tmp_images_router`, etc. (lines 2704-2724). **`/api/maintenance` does NOT exist** — greenfield.

### 2.2 Path + naming conventions

- kebab-case for multi-word segments: `/api/mcp-servers`, `/api/skill-bank` (api.py:2702,2707).
- Underscore outlier: `/api/tmp_images` (api.py:2724) — established convention does allow underscores when path is anchored to a noun (image files); not preferred.
- POST `/api/plane/sync/{project_id}` (plane.py:85-95) — action-subresource pattern. **Apply same shape for `/api/maintenance/checkpoint-cleanup/execute`**.
- The Migration router uses bare words (`/availability`, `/start`, `/status`, `/cancel`) WITHOUT a sub-resource — that's because the migration worker IS the singleton. We have multiple maintenance sections coming → use namespaced paths.

**Recommendation**: `/api/maintenance/checkpoint-cleanup/{availability|status|dry-run|execute}` (kebab-case action verbs). Future sections get their own sub-path: `/api/maintenance/db-vacuum/...`, `/api/maintenance/instance-orphans/...`.

### 2.3 Error semantics

`plane.py` is the copy-paste template for structured error detail bodies:

```python
raise HTTPException(
    status_code=503,
    detail={"error": "plane_disabled", "project_id": "...", "message": "..."},
)
```

`migration.py` uses bare-string detail bodies:

```python
raise HTTPException(status_code=409, detail="Migration is already running")
```

**Decision (architect question)**: which pattern for our 4xx? Recommendation is the plane.py structured dict shape — gives FE a parseable error code without coupling to message text. See `Open Questions §6.1` in plan-overview.

Common error shapes we will need *(wire shapes here mirror Contract v3 — `plan-overview.md` is authoritative on any drift)*:

| HTTP | When | Detail body |
|---|---|---|
| 400 | Prereqs not met (e.g. non-PG backend, fresh dry-run stale) | `{"error": "<code>", "message": "...", "details": {...}}` |
| 404 | Resource id not found | `{"error": "not_found", "message": "..."}` |
| 409 | Run already in flight (manual OR auto) | `{"error": "run_in_flight", "message": "...", "details": {"run_id": "...", "started_at": "..."}}` — nested under `details` [C-2, v3 fix pass] |
| 503 | Maintenance subsystem not initialized (lifespan hasn't run) | `{"error": "not_initialized", "message": "..."}` |

### 2.4 Schemas home

`daemon/routers/schemas.py` (1766 lines) — shared pydantic models. New response models go there: `CheckpointCleanupStatusResponse`, `CheckpointCleanupDryRunResponse`, `CheckpointCleanupExecuteRequest`, `CheckpointCleanupExecuteResponse`, `CheckpointCleanupErrorResponse`.

Pydantic v2 patterns observed: `BaseModel + Field(..., description=...)`, `field_validator` (incl. `mode="before"` — mandatory isinstance guard rule per MCP blueprint), `json_schema_extra` for examples.

### 2.5 No auth today (architect risk)

API has zero auth; CORS `*`; bind `0.0.0.0`. Match existing posture for v1. Hardening is an architect question (Open Questions §6.2).

### 2.6 Migration vs Plane choice for execute

- Migration: `POST /start` returns 202; status via `GET /status`; SSE stream via `GET /events` for live progress.
- Plane: synchronous POST returns 200 with state transition.

For checkpoint cleanup, execute is long (Op E over 33GB). **Use the Migration shape** (202 + run_id + poll). SSE is optional future work (architect question).

---

## §3. Frontend Structure

### 3.1 Settings menu entry point

`frontend/src/app/app.html:54` — gear-icon button, `<mat-icon>settings</mat-icon>`, opens `settingsMenu` mat-menu.

`frontend/src/app/app.ts:554-558` — `settingsMenuItems` signal:

```ts
interface SettingsMenuItem { label: string; icon: string; route: string; }
readonly settingsMenuItems = signal<SettingsMenuItem[]>([
  { label: 'Blueprints', icon: 'architecture', route: '/projects/all/blueprints' },
  { label: 'MCP Servers', icon: 'settings_input_hdmi', route: '/mcp-servers' },
  { label: 'Settings', icon: 'language', route: '/settings' }
]);
```

`frontend/src/app/app.html:57-58` — rendered via `@for (item of settingsMenuItems(); track item.route)`.

### 3.2 Conditional menu entries (precedent for availability-probe append)

`frontend/src/app/app.ts:737-755` — `checkMigrationAvailability()` issues `GET /api/migration/availability`, then `settingsMenuItems.update(items => [...items, {...}])` if eligible. **Pre-condition probe + dynamic append.**

`frontend/src/app/app.ts` also has `checkPlaneAvailability()` — same shape.

For Maintenance: choose one of two patterns:

- **(A)** Plain signal entry (like Blueprints/MCP Servers) — always visible, simple.
- **(B)** Availability probe + append (like Database/Plane) — show only when backend reports eligible.

**Recommendation**: **(B)** with an `availability` endpoint on the BE side. Rationale: maintenance ops depend on a PostgreSQL backend (Op E is PG-only per checkpoint_prune.py:130-137). On SQLite the section should hide, not render a "backend not supported" card. See `Open Questions §6.3`.

### 3.3 Route registration

`frontend/src/app/app.routes.ts` — lazy-loaded routes. Add a new line ABOVE the wildcard `{ path: '**', redirectTo: '' }`:

```ts
{ path: 'maintenance/checkpoint-cleanup',
  loadComponent: () => import('./pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.component')
    .then(m => m.CheckpointCleanupComponent),
  title: 'Maintenance · Checkpoint Cleanup' },
```

Match the kebab-case convention (`mcp-servers`, `skill-bank`).

### 3.4 Section registry (greenfield — no existing pattern)

The Maintenance page will host multiple sections over time (per requirements). Recommend a local registry inside the Maintenance page component:

```ts
interface MaintenanceSection { id: string; label: string; component: Type<unknown>; }
readonly sections: MaintenanceSection[] = [
  { id: 'checkpoint-cleanup', label: 'Checkpoint Cleanup', component: CheckpointCleanupComponent },
];
```

Render via `@for (section of sections; track section.id)` + `<ng-container *ngComponentOutlet="section.component">`. No shared registry exists in `frontend/src/app/core/services/` today; don't invent one prematurely.

### 3.5 ConfirmDialogComponent (reusable)

`frontend/src/app/components/confirm-dialog/confirm-dialog.component.ts` — `ConfirmDialogData { title?, message?, confirmLabel?, cancelLabel?, destructive? }`. Returns `true` on confirm, `false`/undefined on cancel. Material `MatDialog` standard pattern. **Reuse** for the destructive "Cleanup now" confirmation.

### 3.6 Result display (greenfield)

No shared table component exists in the FE (`frontend/src/app/shared/components/` is empty of table primitives). Use a local `<dl>` / `<table>` with `<pre>` JSON dump for raw payload, OR inline angular-material cards. Per the dispatch, simplest path is `<dl>` for status + `<pre>` JSON for the full response — no shared abstraction yet.

### 3.7 Service model — mirror migration.service.ts

`frontend/src/app/services/migration.service.ts` (or equivalent) is the model. BE call → typed Observable → component subscribes. Mirror this for `maintenance.service.ts` with three methods: `getStatus()`, `dryRun()`, `execute(payload)`, plus a `getAvailability()` for the menu probe.

### 3.8 E2E test infra

`frontend/playwright.config.ts`:

- `webServer` array auto-boots BE (`bash dev.sh` on port 8079) + FE (`ng serve --port 4199`), `reuseExistingServer: true`, 60s/120s timeouts.
- `testDir: './e2e'`, `fullyParallel: false`, `workers: 1` (sequential, for data deps).
- Tests are in `frontend/e2e/*.spec.ts` (existing: `instances-state-cache-*.spec.ts`, `fe_liveness_*.spec.ts`, etc.).
- New file: `frontend/e2e/maintenance-checkpoint-cleanup.spec.ts`.

---

## §4. Storage / Persistence for Last-Run Summary

### 4.1 Why a dedicated `maintenance_runs` table

Three options were considered (full reasoning in plan-overview §6.2):

- **(A) `shared_meta_kv`** — already exists for cross-cutting ephemeral metadata, BUT scoped per `(context_key)`. Wrong shape (instance-scoped, not daemon-wide) and no schema.
- **(B) project_metadata_records** — exists (`daemon/migrations/versions/20260524_000003_create_project_metadata_records_table.sql`); used for `plane_*`. Project-scoped, not daemon-scoped. Would require a `__system_default__` project hack.
- **(C) New `maintenance_runs` table** — daemon-scoped, audit-supporting, future-extensible. Mirrors `repair_log`-style "who did what when" pattern (already used elsewhere in the codebase — `daemon/services/maintenance.py` repair log). One migration, one new SQLModel model, one repo.

**Recommendation: (C)**. Justification: maintenance history is operator-grade audit data, not per-project config; needs to outlive any single project context; schema is stable enough to warrant a table.

Schema sketch:

```
maintenance_runs
  id            BIGSERIAL PK
  run_id        TEXT UNIQUE          -- returned to FE for polling
  section       TEXT                 -- 'checkpoint-cleanup' for v1
  kind          TEXT                 -- 'auto' | 'manual_dry_run' | 'manual_execute'
  started_at    TIMESTAMP
  completed_at  TIMESTAMP NULL
  status        TEXT                 -- 'running' | 'succeeded' | 'failed' | 'overlap_refused'
  triggered_by  TEXT                 -- 'system' | 'user:<session_id>'
  summary_json  JSONB                -- BlobPruneSummary + CheckpointRowPruneSummary + duration
  error_json    JSONB NULL
```

Index on `(section, completed_at DESC)` for the status endpoint's "last run" lookup.

---

## §5. Manual-vs-Auto Overlap Guard

### 5.1 The gap

`daemon/services/maintenance.py:225-235` — auto cycle uses `_is_idle()` to gate. There is **NO shared lock** between the auto cycle and any future manual path. Two concurrent runs of `_prune_unreferenced_blobs` against the same checkpointer would race.

### 5.2 Lock design (recommendation)

`MaintenanceRunLock` — single asyncio.Lock on the `MaintenanceService` instance. Both the auto-cycle body and the new manual API path `await self._run_lock`. Acquired before any Op D/E call, released in `finally`. Live status row written to `maintenance_runs` BEFORE acquire attempt — if lock contention, the second acquire fails fast with 409 + run_id of the in-flight run.

This is the simplest model; the existing `MigrationWorker` already serializes per-migration_id (we don't have that id pre-wait). See plan-overview §6.1 Q1.

---

## §6. Citations Index (for phase workers)

| Topic | File:line |
|---|---|
| `BlobPruneSummary` dataclass | `daemon/services/checkpoint_prune.py:88-103` |
| `prune_unreferenced_blobs` signature | `daemon/services/checkpoint_prune.py:104` |
| ZERO_REFS_FAIL_SAFE skip | `daemon/services/checkpoint_prune.py:170-186` |
| env-flag structural gate | `daemon/services/checkpoint_prune.py:30-86` |
| `_prune_unreferenced_blobs` (BE wrapper) | `daemon/services/maintenance.py:909-934` |
| `_is_idle` | `daemon/services/maintenance.py:258` |
| `MaintenanceService` auto-cycle structure | `daemon/services/maintenance.py:225-340` |
| CHECKPOINT_MAX_PER_THREAD config | `daemon/constants.py:54`; pydantic ge=1 in config module |
| Router registration seam | `daemon/api.py:2687-2726` |
| SPA catch-all (router MUST precede) | `daemon/api.py:2816-2828` |
| Migration router pattern (202+poll) | `daemon/routers/migration.py:120-220` |
| Plane router pattern (structured 4xx) | `daemon/routers/plane.py:54-170` |
| Shared pydantic schemas home | `daemon/routers/schemas.py` (1766 lines) |
| project_metadata_records migration | `daemon/migrations/versions/20260524_000003_create_project_metadata_records_table.sql` |
| Disposable-PG test harness | `tests/helpers/checkpoint_prune_pg.py` |
| FE gear menu trigger | `frontend/src/app/app.html:54-58` |
| FE settingsMenuItems signal | `frontend/src/app/app.ts:554-558` |
| FE availability-probe append | `frontend/src/app/app.ts:737-755` |
| FE route registration | `frontend/src/app/app.routes.ts` |
| ConfirmDialogComponent | `frontend/src/app/components/confirm-dialog/confirm-dialog.component.ts` |
| Playwright config | `frontend/playwright.config.ts` |

---

## §7. Anti-Patterns / Traps Carried Forward

From the project Critical Notes:

- **PG-only ops**: SQLite can't run blob prune; migration 20260714_000001 is PG-only and breaks fresh-SQLite boot. Always test against disposable PG.
- **Timestamp binds**: every new `now_utc_naive()` (`daemon/services/timestamps.py`); PG session is `timezone=UTC` (`daemon/repositories/factory.py:206`). TEXT timestamps use `now_utc_iso()`.
- **No 401/403 today**: HTTP has zero auth; do NOT introduce auth in this PR — match existing posture and flag for architect.
- **Naive datetime in PG columns**: tz-aware binds render in PG session TZ → +07 wall digits read as UTC. New maintenance_runs columns are TIMESTAMP (no tz) → use naive binds.
- **No facade-forwarding seam to worry about here**: the new Maintenance router doesn't go through `enqueue_message`, so the Facade-Forwarding Discipline (Job Queue blueprint) is N/A. But anywhere new code calls `InstanceManager`, audit.
- **Log forensic traps**: timestamps are local +07 with no date prefix; two append regions in `ensemble.log`. Time-bracket searches only, never line-window.