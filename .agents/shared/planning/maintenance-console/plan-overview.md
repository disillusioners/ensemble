# Plan Overview: Maintenance Console — Section 1 (Checkpoint Cleanup)

Date: 2026-09-27
Author: planner[v2] via overview worker (post-architect amendments AM-1…AM-18 applied)
Branch: `feature/maintenance-console` @ `666c089d`
Status: **API Contract v3 frozen** — Phase 1 may begin; phase-file workers build strictly against contract v3 in this document *(v3 = reviewer fix pass 2026-09-27: C-1/C-2/W-1 + R-items applied; architect delta stamp landed; **v3.1 = doc-repair only 2026-09-27 [CF-6 doc-repair, v3.1]** — `internal_error` table/union/count catch-up, no wire-surface change)*
Architect source: `architecture-recommendation.md` (275 lines, 2026-09-27, controller + 4 workers)
Companion: `research-findings.md` (same directory) — full evidence citations
Decision log: `decision-log.md` (same directory) — leader rulings + AM-2 rationale

---

## Objectives

1. **Operator manual control path for checkpoint cleanup**, exposed via the existing gear/settings menu as a new "Maintenance" entry, so destructive ops do not require env-flag pre-arming.
2. **Section 1 = Checkpoint Cleanup**: surface current retention config (keep-N + interval), the last-run summary, a "Dry-run check" preview, and a destructive "Cleanup now" flow with explicit confirmation.
3. **Auto-cycle behavior and defaults remain UNCHANGED** — the manual path is additive; the env-flag dual-arm stays for advanced users.
4. **Phase-gated delivery** so the API contract is FROZEN before FE work begins; no contract drift between phases.

Single-sentence completion test: *An operator can open the Maintenance section in the gear menu, see the current checkpoint retention config + last-run summary, click "Dry-run check" to see what would be deleted (counts + bytes + skipped pairs), then click "Cleanup now" through a confirm flow to execute the destructive op and see the actual results — all without restarting the daemon or setting env flags.*

---

## User Stories

| # | As a… | I want to… | So that… |
|---|---|---|---|
| US-1 | operator | see the current checkpoint retention config (keep-N, interval) and the last-run summary | I know what the auto-cycle is doing and what it last did |
| US-2 | operator | click "Dry-run check" to preview what would be deleted (counts + bytes + skipped pairs) | I can estimate the impact before committing |
| US-3 | operator | click "Cleanup now" → see a confirmation dialog that echoes the expected byte count → confirm → see actual results | I can safely trigger a manual cleanup with no env-flag acrobatics |
| US-4 | operator | see structured results (counts, bytes, duration, skipped pairs with reason codes) | I can decide whether to file a ticket or wait |
| US-5 | operator | have manual runs block auto-cycle runs in flight (and vice versa) | I never have two concurrent destructive ops on the same DB |
| US-6 | future dev | add a new section (e.g. DB vacuum, orphan instances) to the same page | the Maintenance surface scales without redesign |

---

## API Contract v3 — frozen 2026-09-27 (post-architect amendments AM-1…AM-18; leader rulings applied)
> **Changelog v2→v3 (reviewer fix pass 2026-09-27):** C-1/C-2 literals + shape unified (404 code = `not_found` everywhere; 409 body nested under `details` everywhere); W-1 mechanism restated + completion-gate prohibition added (INV-13); counts reconciled [R-1/R-2].
> **v3.1 (2026-09-27, doc-repair only — no wire-surface change):** `internal_error` (500, router catch-all per A-8) added to the frozen error-code table + FE union/pin counts 10→11 + BE case 67; documents code already shipped and reviewed (CF-6 ratification). [CF-6 doc-repair, v3.1]
> **Architect delta-stamp 2026-09-27: v2→v3 delta verified (C-1/C-2/W-1+INV-13 applied; no regression to AM-1…AM-18 rulings); two stale W-1-contradicting residues swept at stamp time (risk-row tail + AM-16c bullet — completed the v3 restatement, no semantic change); contract v3 APPROVED for implementation.**
> **[C1 hardening amendment, 2026-09-27 fix pass]** Rule 2's same-origin derivation additionally requires the request `Host` to pass a Host allowlist (loopback family + hosts parsed from `MAINTENANCE_TRUSTED_ORIGINS` + new env `MAINTENANCE_ALLOWED_HOSTS` CSV, default empty); a Host miss means rule 2 cannot match (fail-closed `origin_not_trusted` via rule 5). The frozen R-7 derivation sentence above is unchanged; rule set/order, the `/availability` exemption, and all literals stand.
> **[A-8 amendment, 2026-09-27 fix pass]** The maintenance router's catch-all for unexpected exceptions now returns a contract-shaped 500 `{error:"internal_error", message, details:{}}` (leader-authorized fix item 8; one new error-code literal `internal_error` added to the frozen string set). All other frozen literals, paths, payloads, enums, and gate-order are unchanged. **(v3.1 doc catch-up [CF-6 doc-repair, v3.1]: the frozen error-code table below now carries the 11th row `internal_error` | 500 — this documents an already-shipped and reviewed code literal; doc catch-up, NOT a wire change.)**
> **Architect re-stamp 2026-09-27: AM-1…AM-18 verified applied; PR-9/PR-10 RATIFIED; contract v2 APPROVED for implementation (Phase 2 merge gate cleared). Decision-log AM-2 rationale direction corrected same date — plan-overview §Manual-execute ordering rationale is authoritative.** *(Historical v2 stamp — superseded as the operative marker by the v3 freeze above; see Re-freeze Checklist row 5.)*
> **Re-freeze rule (INV-5):** this is the contract that Phase 1 implements and Phase 2 builds against. Any drift must update this section and re-freeze.

### Prefix

`/api/maintenance/checkpoint-cleanup/*` — registered in `daemon/api.py` BEFORE the SPA catch-all (api.py:2816-2828), per the established router registration seam (api.py:2687-2726). Phase 1 instantiates a single `daemon/routers/maintenance.py` with one `APIRouter` per section (`/checkpoint-cleanup`, future `/db-vacuum`, …) and one `include_router` at the api.py seam.

### Endpoints

| # | Method | Path | Status | Purpose |
|---|---|---|---|---|
| 1 | GET  | `/api/maintenance/checkpoint-cleanup/availability` | 200 | UI probe — should the gear-menu entry render? (kill-switch-aware; never Origin-guarded) |
| 2 | GET  | `/api/maintenance/checkpoint-cleanup/status` | 200 | Current config + last-run summary + in-flight state |
| 3 | POST | `/api/maintenance/checkpoint-cleanup/dry-run` | 200 | Compute would-delete counts/bytes/skipped (no writes) |
| 4 | POST | `/api/maintenance/checkpoint-cleanup/execute` | 202 | Start destructive run; returns `run_id` for polling |
| 5 | GET  | `/api/maintenance/checkpoint-cleanup/runs/{run_id}` | 200 | Poll a run's progress/result (404 when unknown) |

### Origin guard (AM-1) — `require_trusted_origin` dependency

Applies to endpoints #2, #3, #4, #5. Endpoint #1 (`/availability`) is **exempt** (the FE gear probe must see disabled state cleanly; it is non-destructive).

**Rule order — the guard is the FIRST check** (INV-10): a request whose Origin fails the guard returns `403 origin_not_trusted` without inspecting any other gate.

1. **No `Origin` header → allow.** (curl, systemd, programmatic operators.)
2. **`Origin` matches the daemon's own external origin (same-origin) → allow.** Browsers attach `Origin` even on same-origin POSTs; deny-all would 403 the daemon-served SPA's own execute — the shipped product's primary flow. Zero-config production must work. **Same-origin derivation mechanism [R-7]:** the daemon derives its own external origin from the **request `Host` + scheme at request time**; `X-Forwarded-*` headers are **untrusted and are NOT consulted** for origin derivation (no proxy chain is assumed in v1).
3. **`Origin` host ∈ localhost-family (`localhost`, `127.0.0.1`, `[::1]`, any port, http/https) → allow.** Zero-config dev: FE dev server on `:4199` proxies to `:8079`; the daemon sees `Origin: http://localhost:4199`.
4. **`Origin ∈ MAINTENANCE_TRUSTED_ORIGINS`** (CSV env, default empty) **→ allow.** Explicit opt-in for LAN-browser origins.
5. Anything else (incl. `Origin: null` from sandboxed iframes / `file://`) → **403 `origin_not_trusted`**.

Note: this guard is the **sole browser-borne defense layer** in v1. It does NOT replace the 6-gate mistake/staleness model — the gates and the guard are complementary (gates catch operator error; the guard catches hostile browser choreography).

### Run-id format (AM-8)

`run_id = ckpt-YYYYMMDD_HHMMSSffffff-hex8` — colon-free (URL-clean), lexicographically sortable, aligned with `migration_id` precedent (`migration.py:112-114`); hex8 (not hex4) per Focus Area 1. **[R-18]** The format is a **documented superset** of the `migration_YYYYMMDD_HHMMSS` precedent: same date-time core, plus microsecond precision and an 8-hex random suffix — the lineage must be stated in the `maintenance_run_identity.py` module docstring (close-out checklist item).

Example: `"ckpt-20260927_032000123456-2a18f3c9"`.

### Run lifecycle (AM-4 + AM-5 + AM-6 + AM-7)

- **States:** `running | succeeded | failed | interrupted`. `overlap_refused` is **deleted** (architect ruling over the contract worker's rows-for-refusals detail: 404-noise churn from double-clicks outweighs attempt-audit value; one INFO log line carries requester forensics instead).
- **Single-flight gate** (per run, auto or manual): `acquire asyncio.Lock → conditional INSERT (partial unique index `ON (section) WHERE status='running'`; INSERT conflict → 409, no row) → run → finalize in `finally`. This **supersedes research-findings §5.2's "row before acquire"** — that ordering has a crash window producing a phantom `running` row that 409s the feature until manual DB surgery.
- **Partial unique index** `(section) WHERE status='running'` is the DB-side claim (AM-5). Both PG and SQLite support partial indexes; declare with dialect `where` (`postgresql_where`/`sqlite_where`) so `create_all` builds on both drivers. Phase 1 must verify render on SQLite (fallback: plain conditional `INSERT … WHERE NOT EXISTS` with documented racy-belt caveat).
- **Dry-run takes the same gate** (it is a `kind=manual_dry_run` run row; its scan needs a stable blob set for `expected_bytes` to mean anything). Non-blocking acquire, no nesting, dry-run is terminal before execute references it.
- **Daemon restart:** unconditional boot sweep at lifespan start — rowcount-guarded CAS `running → interrupted` (PlaneSync `fail_stale_syncing` pattern, `plane_sync_watchdog_service.py:299-316`), no age gate (a boot-time `running` row is an orphan by definition under the single-daemon assumption). One summary log line. **No live watchdog in v1.**
- **Cancellation:** NO abort exists in the prune loop today (single per-pair `for` with unconditional `continue`s, `checkpoint_prune.py:152-258`). No cancel endpoint in v1; the per-pair loop top is the pre-built v2 insertion point (~10 lines cooperative `asyncio.Event`).

### `last_run` scope (AM-9, overrides §6.5)

- `last_run` = latest **`succeeded | failed`** of `kind ∈ {auto, manual_execute}`.
- `manual_dry_run` **never** surfaces in `last_run`. Dry-run history is queryable via `GET /runs/{id}` by ID.
- `in_flight` = any `running` row (any kind).

### Timestamp suffix sweep (A-7 confirmed)

All wire timestamps use `now_utc_iso()` (`daemon/services/timestamps.py:63-104`) — emits `+00:00` (NOT `Z`). JS `Date` parses both equivalently. Examples in this doc use `+00:00`.

### 1. GET `/availability`

**Response 200** (always — even when ineligible):
```json
{
  "eligible": true,
  "backend": "postgres",
  "state": "ready",
  "reason": null
}
```

Or:
```json
{
  "eligible": false,
  "backend": "postgres",
  "state": "kill_switched",
  "reason": "MAINTENANCE_ENDPOINTS_ENABLED=0"
}
```

Other `state` values:
- `backend_unsupported` — SQLite (blob prune is PG-only).
- `subsystem_disabled` — MaintenanceService not wired by lifespan (transient startup race). The frozen 503 `not_initialized` shape carries this state during the race.
- `ready` — eligible.
- `kill_switched` — `MAINTENANCE_ENDPOINTS_ENABLED=0`.

`eligible` is **derived** (`state === 'ready'`). `reason` is a diagnostic string for log/UI display, **NOT for FE branching** (FE branches on `state` only).

**Errors**: 503 if MaintenanceService not yet initialized by lifespan (`{"error": "not_initialized", ...}`).

### 2. GET `/status`

**Response 200** *(config block read from the LIVE effective env at request time — call-time env read, precedent `checkpoint_prune.py:75-84` (`blob_prune_destructive_enabled()`), NOT boot-cached config [R-6, leader ruling (a) → decision-log])*
```json
{
  "config": {
    "checkpoint_max_per_thread": 3,
    "checkpoint_max_per_thread_floor": 1,
    "cleanup_interval_hours": 24,
    "blob_prune_dry_run_env_default": "1",
    "blob_prune_destructive_armed": false
  },
  "last_run": {
    "run_id": "ckpt-20260927_031409123456-1f4a8c2e",
    "kind": "auto",
    "started_at": "2026-09-27T03:14:09.123456+00:00",
    "completed_at": "2026-09-27T03:14:11.946279+00:00",
    "status": "succeeded",
    "summary": {
      "checkpoint_rows": {"scanned_pairs": 12, "deleted": 0, "excess_pairs": 0},
      "writes": {"deleted": 0},
      "blobs": {
        "scanned_pairs": 12,
        "would_delete_count": 4,
        "would_free_bytes": 268435456,
        "would_delete": 4,
        "bytes": 268435456,
        "destructive": false,
        "skipped": [],
        "skipped_truncated": false
      },
      "duration_ms": 1823
    }
  } | null,
  "in_flight": {
    "run_id": "ckpt-20260927_031822987654-9bc2d4a1",
    "kind": "manual_execute",
    "started_at": "2026-09-27T03:18:22.987654+00:00",
    "triggered_by": "user"
  } | null
}
```

`last_run` is **only** `auto` or `manual_execute` in `{succeeded, failed}` — see Run Lifecycle § above. `manual_dry_run` runs do NOT surface here.

`config.blob_prune_destructive_armed` [R-6] = the effective state of the auto-cycle dual-arm (BOTH `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1` AND `CHECKPOINT_BLOB_PRUNE_DRY_RUN=0`), evaluated at request time via the same call-time read the auto-cycle uses (`blob_prune_destructive_enabled()`, `checkpoint_prune.py:75-84`). Informational only — the manual path never reads it (INV-2). The whole config block is read per-request, so env flips show up on the next `/status` without a restart (unlike the boot-read kill-switch).

When `last_run.summary` came from a **destructive** run (not a dry-run), `blobs` uses the destructive flavor (deleted count + bytes_freed) and `destructive: true`. FE branches on `destructive` to render the correct set of keys.

`blobs.skipped[]` (AM-10): `[{thread_id, checkpoint_ns, reason}]`. See /dry-run for the `reason` enum. `skipped_truncated` appears in BOTH the §2 and §3 examples and is `false` unless the 1000-entry cap fired [R-9, v3 fix pass — examples now match the schema].

**Errors**: 503 if MaintenanceService not initialized (`{"error": "not_initialized", "message": "..."}`).

### 3. POST `/dry-run`

**Request** (no body required):
```json
{}
```

**Response 200** (synchronous — dry-run runs the per-pair scan but performs no DELETEs):
```json
{
  "run_id": "ckpt-20260927_032000123456-2a18f3c9",
  "would_delete": {
    "checkpoint_rows": 0,
    "writes": 0,
    "blobs": 4,
    "bytes": 268435456
  },
  "would_delete_count": 4,
  "would_free_bytes": 268435456,
  "scanned": {
    "thread_ns_pairs": 12
  },
  "skipped": [
    { "thread_id": "3f2a9c1e-…", "checkpoint_ns": "", "reason": "ZERO_REFS_FAIL_SAFE" },
    { "thread_id": "77b0d4aa-…", "checkpoint_ns": "snap:x", "reason": "MAX_REFS_EXCEEDED" }
  ],
  "skipped_truncated": false,
  "duration_ms": 412,
  "fresh_until": "2026-09-27T03:25:00.123456+00:00"
}
```
*(field `skipped_truncated` added to this example per [R-9, v3 fix pass] — it is in the schema and was missing from the example)*

`fresh_until` is `now() + MAINTENANCE_DRY_RUN_FRESH_SECONDS` (default 300s / 5 min) — execute must reference a fresh enough dry-run to proceed. Echoed by the client as `expected_bytes` to the execute endpoint.

`would_delete_count` and `would_free_bytes` are the canonical fields (AM-11). `would_delete.blobs` + `would_delete.bytes` remain for the destructive-flavor summary shape symmetry.

`skipped[]` (AM-10): closed set `{ZERO_REFS_FAIL_SAFE, MAX_REFS_EXCEEDED}` (stable literals, `checkpoint_prune.py:185-200`) ∪ open family `ERROR:<ExceptionName>` (`:250-256`). Documented as extensible enum; FE renders known codes with badges, `ERROR:*` generically. `skipped[]` is **informational, not part of the confirm echo** (AM-3). Scope pin stays `expected_bytes` equality ONLY — skip pairs contribute 0 bytes to both runs; divergence is deletion-conservative (skipped pairs are never deleted); pair-count/skip-hash pins were rejected.

**Errors**:
- 409 if a run is already in flight (`{"error": "run_in_flight", "message": "...", "details": {"run_id": "...", "started_at": "..."}}` — NESTED under `details` [C-2, v3 fix pass; same shape as the execute endpoint and the 409-adoption contract])
- 403 if Origin fails the guard (`{"error": "origin_not_trusted", ...}`)
- 503 if MaintenanceService not initialized
- 503 if kill-switch off (`{"error": "maintenance_disabled", ...}`)

### 4. POST `/execute`

**Request** (explicit destructive payload — **no `idempotency_key`** per AM-17):
```json
{
  "dry_run_run_id": "ckpt-20260927_032000123456-2a18f3c9",
  "expected_bytes": 268435456,
  "confirm": true
}
```

Server-side checks (all must pass; first failure → 4xx):

1. **Origin guard (AM-1)** — 403 `origin_not_trusted` if Origin fails.
2. **`confirm == true`** (else 400 `{"error": "confirm_required"}`).
3. **`dry_run_run_id` exists and was a dry-run** (else 404).
4. **Dry-run age ≤ `MAINTENANCE_DRY_RUN_FRESH_SECONDS`** (default 300s; else 400 `{"error": "dry_run_stale", "age_seconds": 372, "max_age_seconds": 300}`).
5. **`expected_bytes` matches the stored dry-run summary's `bytes`** (AM-3 — the ONLY scope pin; else 400 `{"error": "byte_count_mismatch", "expected": ..., "stored": ...}`).
6. **No run in flight (auto OR manual)** — single-flight gate (AM-4 + AM-5); else 409 `{"error": "run_in_flight", "message": "...", "details": {"run_id": "...", "started_at": "..."}}` — `run_id`/`started_at` are NESTED under `details` [C-2, v3 fix pass; matches case 63 and the FE `adoptRunIdFromError` pin].
7. **Advisory:** if `_is_idle()` is false, log + include `advisory: "system_busy"` in the 202 response body — NOT a refusal.

**Manual execute order (AM-2 — blocking correctness fix):** manual execute composes **Op E → Op D** inside `MaintenanceApiService`. The auto-cycle order is UNCHANGED (INV-1). See §Manual-execute ordering rationale (below).

**Response 202** (run started):
```json
{
  "run_id": "ckpt-20260927_032130456789-7e11f3a2",
  "status": "running",
  "started_at": "2026-09-27T03:21:30.456789+00:00",
  "advisory": null,
  "expected_duration_ms_hint": 412
}
```

`expected_duration_ms_hint` (AM-12, A-11 ratified) = the referenced dry-run's `duration_ms`, **unit: milliseconds** (the field name says ms and ms is what it carries — this is the single canonical unit definition in the contract [R-5, v3 fix pass]; the old "ceil-seconds" wording was wrong. The FE may render "~Xs"-style copy derived from the ms value, but the wire field is always ms). The original OQ-draft check #6 typo ("MaintenanceService idle advisory — log + 200 but include...") is fixed: response code is **202**, not 200.

**Errors**:
- 400 (validation failures 2–5 above)
- 403 (Origin guard failure)
- 409 (`run_in_flight` — body carries `details.run_id` for 409-adoption; see FE §)
- 503 (`not_initialized`, `maintenance_disabled`)

### Manual-execute ordering rationale (AM-2 — mechanism restated [W-1, v3 fix pass])

The auto-cycle's order is D→E (`maintenance.py:437-480`). Reusing that order in the manual path **silently over-deletes** on the primary operator flow. The precise mechanism: the dry-run's anti-join counts blobs referenced by *any remaining checkpoint row* (`checkpoint_prune.py:12-17`). Under D-first, **every server-side execute check PASSES — including check 5**, because the byte-equality gate compares the client's echoed `expected_bytes` against the *stored dry-run row's* bytes (AM-3): both derive from the same dry-run row, so they always match. The failure is therefore **never a refusal**. Op D runs first and unreferences the blobs referenced only by the now-deleted excess rows; the subsequent blob pass then deletes those extra bytes too — **silent over-deletion beyond the confirmed byte echo**. The deleted-extra bytes are exactly the blobs that D-first unreferences. This is caught only by the regression assertions in cases 44/57 (freed-bytes == dry-run-estimate), **never by the byte-equality gate** — and a post-run actual-vs-expected completion gate must NOT be added to "catch" it (see INV-13).

Manual execute composes **Op E → Op D** (delete unreferenced blobs first, then drop the now-orphan excess rows). The auto-cycle order is **unchanged** (INV-1). Residual cost: blobs referenced only by excess rows survive one extra cycle (conservative under-delete, self-healing). Required test: execute with excess rows present must NOT over-delete (cases 44/57 prove this; under the old order they fail by over-deleting, not by erroring).

### 409-adoption contract (AM-17 + AM-14)

When the execute endpoint returns **409** `run_in_flight` (the single-flight gate is the cause), the body carries `details.run_id` and `details.started_at`. The FE contract note: **on 409 from execute, adopt `details.run_id` and resume polling `GET /runs/{details.run_id}`** — covers the double-click and network-retry classes with zero new contract surface. The Origin guard (not a key) is the browser defense.

### 5. GET `/runs/{run_id}`

**Response 200** (run found):
```json
{
  "run_id": "ckpt-20260927_032130456789-7e11f3a2",
  "kind": "manual_execute",
  "status": "running" | "succeeded" | "failed" | "interrupted",
  "started_at": "2026-09-27T03:21:30.456789+00:00",
  "completed_at": null | "2026-09-27T03:23:14.123456+00:00",
  "summary": { ...same shape as last_run.summary, dual-flavor keys on `destructive`... } | null,
  "error": null | {"code": "run_interrupted", "message": "..."}
}
```

`running` → `{status, completed_at:null, summary:null}`; terminal → full body; unknown → 404.

**Errors**:
- 404 `{"error": "not_found", "run_id": "..."}` — 404 error code literal is **`not_found`** everywhere in this surface [C-1, v3 fix pass]
- 403 (Origin guard)
- 503 (`not_initialized`, `maintenance_disabled`)

### Common Error Detail Body Shape

All 4xx/5xx responses use the structured dict pattern from `daemon/routers/plane.py:71-170` (including the 500 catch-all: unexpected exceptions return `{error:"internal_error", message, details:{}}` — table row added [CF-6 doc-repair, v3.1]):

```json
{
  "error": "<stable_machine_code>",
  "message": "<human-readable>",
  "details": { ...optional, e.g. "run_id", "expected", "stored", "age_seconds", "max_age_seconds"...
}
```

Stable machine codes for Section 1:

| Code | HTTP | When |
|---|---|---|
| `not_initialized` | 503 | MaintenanceService not wired by lifespan |
| `backend_unsupported` | 503 | SQLite (blob prune is PG-only) |
| `run_in_flight` | 409 | another run (auto or manual) is active; `details.run_id`/`started_at` for 409-adoption |
| `not_found` | 404 | `run_id` does not exist *(code literal unified to `not_found` at every site — [C-1, v3 fix pass])* |
| `confirm_required` | 400 | execute without `confirm: true` |
| `dry_run_required` | 400 | execute without `dry_run_run_id` |
| `dry_run_stale` | 400 | dry-run older than `MAINTENANCE_DRY_RUN_FRESH_SECONDS` (default 300s) |
| `byte_count_mismatch` | 400 | echoed `expected_bytes` doesn't match stored dry-run |
| `origin_not_trusted` | 403 | Origin guard (AM-1) refused |
| `maintenance_disabled` | 503 | `MAINTENANCE_ENDPOINTS_ENABLED=0` |
| `internal_error` | 500 | unexpected exception (router catch-all per A-8) *(row added [CF-6 doc-repair, v3.1] — documents the already-shipped literal; 11 codes total)* |

---

## `maintenance_runs` schema (AM-15, Focus Area 5 — conventions-validated)

```sql
-- MANUAL: TRUE   (canonical doc; runner no-ops on PG — runner.py:693-730; table lands via create_all)
CREATE TABLE IF NOT EXISTS maintenance_runs (
    run_id               TEXT PRIMARY KEY,   -- ckpt-<YYYYMMDD_HHMMSSffffff>-<hex8>
    section              TEXT NOT NULL,      -- 'checkpoint-cleanup' v1
    kind                 TEXT NOT NULL,      -- 'auto' | 'manual_dry_run' | 'manual_execute'
    started_at           TEXT NOT NULL,      -- now_utc_iso()
    completed_at         TEXT,               -- NULL while running
    status               TEXT NOT NULL,      -- 'running'|'succeeded'|'failed'|'interrupted'
    triggered_by         TEXT NOT NULL,      -- 'system' | 'user'   (no session ids exist)
    requester_json       JSONBType,          -- {peer_ip, user_agent, origin}; NULL for auto
    dry_run_run_id       TEXT,               -- soft ref, no FK (dialect-divergent cascade trap)
    expected_bytes       INTEGER,            -- echoed promise
    dry_run_summary_json JSONBType,          -- full dry-run snapshot (incl. skipped[]) — self-contained audit
    confirm              BOOLEAN,
    advisory             TEXT,               -- 'system_busy' | NULL
    env_flags_json       JSONBType,          -- {blob_prune_dry_run, blob_prune_destructive, destructive_override}
    summary_json         JSONBType,          -- outcome: BlobPruneSummary + Op D counts + duration_ms
    error_json           JSONBType           -- {code, message}
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_maintenance_runs_running_section
    ON maintenance_runs(section) WHERE status = 'running';   -- the DB single-flight claim (AM-5)
CREATE INDEX IF NOT EXISTS ix_maintenance_runs_section_completed
    ON maintenance_runs(section, completed_at);
```

Audit semantics (closes §6.7): **one channel — `maintenance_runs` IS the audit trail.** Minimum destructive-run record: decision inputs (`dry_run_run_id`, `expected_bytes`, full `dry_run_summary_json` incl. `skipped[]`, `confirm`, `advisory`, `env_flags_json`, `requester_json` {peer_ip, user_agent, origin} — forensics, never attribution) + pre-state (inside the dry-run snapshot) + outcome (`summary_json`) + failure (`error_json`). `skipped[]` capped at 1000 entries with `skipped_truncated:true` flag. Revisit >100k rows or >100 MB.

Conformance: TEXT PK (repo pattern), TEXT ISO via `now_utc_iso()` (zero `sa.DateTime` columns in repo — sidesteps naive/tz trap entirely), `JSONBType` for portable JSON (raw PG `JSONB` breaks SQLite `create_all`), `-- MANUAL: TRUE` raw `.sql` + `SQLModel.metadata.create_all` on both drivers at boot (`manager.py:546`, model-registration block `manager.py:530-546`) [R-3 cite fix]; no `_ensure_postgres_columns` entries (new tables need none — snapshot precedent). **Cannot regress fresh-SQLite boot** (20260714 trap class structurally unreachable here).

---

## FE Structure (Phase 2 builds against this — AM-14 amendments applied)

### Route

`frontend/src/app/app.routes.ts` — new lazy route ABOVE the wildcard. **Route target [R-10]: the lazy route loads the PAGE SHELL (`MaintenanceComponent`); the shell renders the checkpoint-cleanup SECTION component via its local sections registry** — section-component-via-page-shell is the single canonical route target (aligned with phase2 T3). The route **must be gated** on `availability.state === 'ready'` (resolver or probe) so a stale FE dist + missing BE router doesn't 404 into the SPA fallback:

```ts
{ path: 'maintenance/checkpoint-cleanup',
  loadComponent: () => import('./pages/maintenance/maintenance.component')
    .then(m => m.MaintenanceComponent),   // PAGE SHELL — renders sections from the registry [R-10]
  canMatch: [maintenanceAvailabilityGuard],   // gates on state === 'ready'
  title: 'Maintenance · Checkpoint Cleanup' },
```

### Gear-menu entry (AM-14: state-enum gating)

`frontend/src/app/app.ts:554-558` — `settingsMenuItems` signal starts with three static items. We add Maintenance via the **availability-probe append** pattern (app.ts:737-755, mirrors `checkMigrationAvailability()`). The menu entry appears ONLY when `state === 'ready'`:

```ts
private checkMaintenanceAvailability(): void {
  this.http.get<MaintenanceAvailability>('/api/maintenance/checkpoint-cleanup/availability')
    .subscribe({
      next: (data) => {
        if (data.state === 'ready' && !this.settingsMenuItems().some(i => i.route === '/maintenance/checkpoint-cleanup')) {
          this.settingsMenuItems.update(items => [
            ...items,
            { label: 'Maintenance', icon: 'build', route: '/maintenance/checkpoint-cleanup' },
          ]);
        }
      },
      error: () => { /* section stays hidden */ },
    });
}
```

`MaintenanceAvailability` adds `state: 'ready' | 'backend_unsupported' | 'subsystem_disabled' | 'kill_switched'` (replacing or augmenting the prior `eligible` boolean — `eligible` becomes derived). The FE never branches on `reason`.

### Section registry (greenfield, inside the Maintenance page)

```ts
interface MaintenanceSection { id: string; label: string; component: Type<unknown>; }
readonly sections: MaintenanceSection[] = [
  { id: 'checkpoint-cleanup', label: 'Checkpoint Cleanup', component: CheckpointCleanupComponent },
];
```

Rendered with `@for` + `<ng-container *ngComponentOutlet="section.component">`. No shared registry across pages — YAGNI. Single `Maintenance` gear entry → landing page (no per-section gear entries).

### Components (under `frontend/src/app/pages/maintenance/checkpoint-cleanup/`) — AM-14 colocated service (A-9 ratified)

| File | Responsibility |
|---|---|
| `checkpoint-cleanup.component.ts/html/scss` | Top-level — orchestrates the section; renders config + last-run + 2 action buttons + result panels |
| `checkpoint-cleanup.service.ts` | **Colocated** under the section (NOT `services/maintenance.service.ts`). Multi-section surface must not share a single-section service file. Mirrors `migration.service.ts` — five HTTP methods (availability, status, dryRun, execute, getRun) |
| `checkpoint-cleanup.component.spec.ts` | Jest logic-mirror: status render, dry-run result render, execute confirm flow, **skipped[] render + badge map**, **409-adoption behavior** |

### Confirm flow

Reuse `frontend/src/app/components/confirm-dialog/confirm-dialog.component.ts`. Pass:
```ts
{
  title: 'Run checkpoint cleanup',
  message: `This will permanently delete ~${bytes} MB of unreferenced blobs and ${rows} excess checkpoint rows. This cannot be undone.`,
  confirmLabel: 'Cleanup now',
  cancelLabel: 'Cancel',
  destructive: true,
}
```

### Result display (AM-14)

- **Status block:** `<dl>` of config + last-run (date, kind, bytes freed). Dual-flavor branch on `summary.blobs.destructive`:
  - `destructive:false` (or absent — dry-run flavor): render `would_delete_count` + `would_free_bytes`.
  - `destructive:true`: render `deleted` + `bytes_freed`.
- **Skipped pairs render** (AM-14, AM-10): when `blobs.skipped.length > 0`, show a "N pairs skipped — fail-safe" badge line. Reason badge map: known reasons (`ZERO_REFS_FAIL_SAFE`, `MAX_REFS_EXCEEDED`) get human labels + styled chips; `ERROR:*` renders generically as "Error: <name>".
- **Dry-run result:** card with would-delete counts/bytes + a `<pre>` JSON dump of the raw response.
- **Execute progress:** poll `GET /runs/{run_id}` every **2000 ms** (`POLL_INTERVAL_MS = 2000` — source-grep pin in spec) while `status === 'running'`; on terminal, render the same result panel.
- **409-adoption behavior** (AM-14, AM-17): on `409 run_in_flight` from execute, adopt `details.run_id` and **resume polling** `GET /runs/{details.run_id}` immediately (no operator-visible error toast). Covers double-click and network-retry classes.
- No shared table component exists yet — keep it inline. (Future: extract to `frontend/src/app/shared/components/result-table/` if a second section needs it.)

---

## Phase Breakdown

| Phase | Name | Objective | Tasks | Coupling | Status |
|---|---|---|---|---|---|
| 1 | Backend APIs + service wiring + tests | Expose the manual control path with structured results, audit, single-flight gate, Origin guard, kill-switch, boot sweep, and full disposable-PG test coverage | 9 | independent of Phase 2 (contract v3 is the seam) | pending |
| 2 | Frontend gear-menu + Maintenance section | Operator-facing UI for status/dry-run/execute, polling, confirm flow, skipped render, 409-adoption, Playwright e2e against a disposable-PG dev daemon | 7 | **depends on Phase 1 contract v3** | pending |
| 3 | Activation | Rebuild FE dist + restart daemon; live spot-check on disposable PG; close out | 3 | depends on Phase 2 | pending |

**Contract-freeze gate**: Phase 1 must merge before Phase 2 starts. The `/api/maintenance/checkpoint-cleanup/*` surface in this document is the contract v3; any drift must update this doc and re-freeze.

### Phase 1 — Backend APIs + service wiring + tests (9 tasks)

1. **Capture `BlobPruneSummary`** instead of discarding at `maintenance.py:934`. Capture into a structured `CheckpointRunResult` object that also includes Op D counts (`CheckpointRowPruneSummary` dataclass — new).
2. **Add `destructive` override kwarg** to `prune_unreferenced_blobs` (`daemon/services/checkpoint_prune.py:104`). Manual path passes `destructive=True`; auto-cycle continues to read env. SERIALIZABLE+retry wrap + ZERO_REFS_FAIL_SAFE stay unchanged. **Env gate untouched** — auto-cycle behavior preserved.
3. **New `MaintenanceRunLock`** (asyncio.Lock on MaintenanceService) — both auto-cycle body and new manual path `await self._run_lock`. Acquire BEFORE the conditional INSERT; release in `finally`. Fail-fast → 409 with in-flight `run_id`.
4. **`MaintenanceRunsRepository`** + `MaintenanceRun` SQLModel model + Alembic migration `20260927_000001_create_maintenance_runs_table.sql` (AM-15). Partial unique index `(section) WHERE status='running'` (AM-5). Composite index `(section, completed_at)`. JSONBType for portable JSON. PG-safe migration only (no SQLite path). Text timestamps via `now_utc_iso()`.
5. **`MaintenanceApiService`** (`daemon/services/maintenance_api_service.py`) — wraps the new manual endpoints. Methods: `availability()`, `status()`, `dry_run()`, `execute(payload)`, `get_run(run_id)`. Owns the run_id generation, lock acquire/release, run row lifecycle, summary capture. **Manual execute composes Op E → Op D** (AM-2). Single-flight sequence: `acquire lock → conditional INSERT → run → finalize in finally` (AM-4).
6. **Boot sweep** (AM-7): unconditional CAS `running → interrupted` at lifespan start (rowcount-guarded). One summary log line. No live watchdog.
7. **`daemon/routers/maintenance.py`** — five endpoints per the contract v3 above. `require_trusted_origin` FastAPI dependency on endpoints 2–5 (AM-1). Mirror `daemon/routers/migration.py` for dependency injection shape; mirror `daemon/routers/plane.py` for structured 4xx detail bodies. Register in `daemon/api.py:2687-2726` BEFORE the SPA catch-all.
8. **Auto-cycle integration**: wire MaintenanceService auto-cycle to ALSO use the run lock and write its run row to `maintenance_runs` (so `/status` last_run can surface auto-cycle history). Env gate stays the same; auto-cycle destructive arm still requires `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1`. Auto-cycle order remains D→E (INV-1, AM-2 unchanged for auto).
9. **Disposable-PG test suite** — `tests/integration/test_maintenance_checkpoint_cleanup_api.py` + unit tests for the service layer. Use `tests/helpers/checkpoint_prune_pg.py` (per-test `ensemble_blob_prune_<uuid>` DB). Tests (expanded per AM-16 — see §Test Strategy).

### Phase 2 — Frontend gear-menu + Maintenance section (7 tasks)

1. **Service layer** (AM-14 colocated) — `frontend/src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.service.ts`. Five typed Observables mirroring the API exactly (incl. error code union with `origin_not_trusted`, `maintenance_disabled`, `run_in_flight`).
2. **Gear-menu availability probe** — add `checkMaintenanceAvailability()` to `frontend/src/app/app.ts`. Mirror `checkMigrationAvailability()` (app.ts:737-755). Branch on `state === 'ready'`. Call from `ngOnInit`. **Don't touch** the static `settingsMenuItems` signal entries (Blueprints/MCP/Settings).
3. **Route registration** — add lazy route to `frontend/src/app/app.routes.ts` per the kebab-case convention; gate with `canMatch` guard on `availability.state === 'ready'`.
4. **Section registry + page shell** — `frontend/src/app/pages/maintenance/maintenance.component.ts/html` with the local section registry + `<ng-container *ngComponentOutlet>`.
5. **CheckpointCleanupComponent** — full UI: status block (dual-flavor branching on `destructive`), dry-run button + result panel (with skipped[] render), execute button → confirm dialog → execute → poll (`POLL_INTERVAL_MS = 2000` source-grep pin) → result panel. Reuse ConfirmDialogComponent. **409-adoption on `run_in_flight`** (adopt `details.run_id`, resume polling — no error toast).
6. **Jest logic-mirror tests** — `checkpoint-cleanup.component.spec.ts`. Render with mocked service; verify status/dl branches, dry-run button enabled-state transitions, confirm dialog wiring (source-grep pin), execute payload shape (no `idempotency_key`), error rendering per stable error code, **skipped[] render + reason badge map**, **409-adoption behavior**, **`POLL_INTERVAL_MS = 2000` source-grep pin**.
7. **Playwright e2e** — `frontend/e2e/maintenance-checkpoint-cleanup.spec.ts`. Auto-boots dev daemon against disposable PG. Validates:
   - gear menu shows Maintenance when `state === 'ready'`
   - status renders config block (dual-flavor branch coverage)
   - dry-run shows counts/bytes + skipped[] badge
   - confirm dialog opens, cancel → no execute, confirm → execute → result renders
   - 409-adoption: second tab sees 409, adopts run_id, resumes polling (no error toast)
   - **cross-origin 403** (Playwright with `Origin: http://evil.example` → expect 403 `origin_not_trusted`)
   - **kill-switch hide** (boot dev daemon with `MAINTENANCE_ENDPOINTS_ENABLED=0` → gear menu does NOT contain Maintenance; `availability` returns `state:"kill_switched"`)
   - **MUST NOT run destructive ops against the operator's real prod DB** — the spec sets a dedicated dev daemon env (`CHECKPOINT_*` + `ENSEMBLE_DB_DSN` pointing to disposable PG).

### Phase 3 — Activation (3 tasks)

1. **FE dist rebuild** — `make install` or `npm run build` in `frontend/`. (Daemon rebuild does NOT cover the FE dist.)
2. **Daemon rebuild + restart** — `make install` (per established convention; activation-pending notes consistently show this is the close-out gate).
3. **Live spot-check on disposable-PG dev daemon** — open the Maintenance section, run dry-run, run execute (confirm), poll until terminal, verify `maintenance_runs` row was written. **Then close the activation-pending notes.**

### Coupling Map

| | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| Phase 1 | — | **tight (frozen contract v3 is the only seam)** | independent |
| Phase 2 | tight | — | independent |
| Phase 3 | independent | independent | — |

---

## Design Decisions (answers to the 6 design questions, post-amendment)

Each answer is **evidence-cited**. See `research-findings.md §6` for the full citations index.

### Q1. On-demand invocation — manual vs auto cycle, idle/busy, blocking vs async

**Decision**: Async via 202 + run_id + poll (migration.py pattern), reusing the auto-cycle's Op D/E core with an added `destructive` override kwarg on `prune_unreferenced_blobs`. **Manual execute composes Op E → Op D** (AM-2); auto-cycle order stays D→E (INV-1). Idle gate is **advisory, not refusable**. CommandDispatcher pause→quiesce→op→resume is **overkill** — maintenance ops are read-mostly on the checkpointer side and the SERIALIZABLE+retry wrap + `MaintenanceRunLock` give us the concurrency control we need without pausing the daemon.

**Rationale**:
- Op E over 33GB of blobs will not be fast (production probe: `ensemble_prod` has 33GB in `checkpoint_blobs`, 97% of DB). Synchronous HTTP would time out.
- Migration router (`daemon/routers/migration.py:134-180`, start handler `:144` with `BackgroundTasks`) is the established repo pattern for long manual actions: 202 + `migration_id` + `GET /status` (`:188`) + optional `GET /events` SSE (`:223`) [R-3 cite fix — was `:120-170`].
- The `MaintenanceRunLock` serializes both auto and manual — simpler than CommandDispatcher's pause→quiesce state machine.
- `_is_idle()` is already known to have blind-spots (maintenance.py:107, 167, 228 docblocks — `list_all_pending` does not see all pending). Refusing on a stale "busy" signal would cause user confusion. Advisory warning is honest.
- Manual execute MUST be Op E → Op D (AM-2): under D-first every execute check passes (the byte-equality gate compares echo vs stored dry-run — always equal), so the failure is silent over-deletion beyond the confirmed byte echo, caught only by regression cases 44/57. See §Manual-execute ordering rationale and INV-13 (no completion gate).

**Evidence**: `daemon/routers/migration.py:134-180` (202+poll shape); `daemon/services/maintenance.py:258` (`_is_idle` blind-spots); `daemon/services/checkpoint_prune.py:104` (`prune_unreferenced_blobs` is the natural unit of work); `checkpoint_prune.py:12-17` (anti-join blob-count semantics); `maintenance.py:437-480` (auto-cycle D→E order, unchanged).

### Q2. Structured results — JSON shapes + last-run storage

**Decision**: Capture `BlobPruneSummary` instead of discarding (research §1.2). Define a new `CheckpointRowPruneSummary` dataclass for Op D counts. Last-run summary persists in a new `maintenance_runs` table per the schema in §`maintenance_runs` schema above (AM-15, conventions-validated). `BlobPruneSummary` carries dual-flavor keys (`would_delete_count`/`would_free_bytes` for dry-run; `deleted`/`bytes_freed`/`destructive:true` for destructive) — FE branches on `destructive`.

**Rationale**:
- `BlobPruneSummary` already has the right shape (`scanned_pairs`, `total_deleted`, `total_bytes_freed`, `skipped: [(thread, ns, reason)]`); just plumb it through.
- Op D currently has no aggregate — assemble from `find_excess_checkpoint_groups(N)` (yields per-pair counts) plus the destructive arm's DELETE return.
- Last-run storage options:
  - `shared_meta_kv` — instance-scoped, not daemon-wide. Wrong shape.
  - `project_metadata_records` — project-scoped; would need `__system_default__` hack. Plane-* precedent is project-scoped config, not operator audit.
  - **`maintenance_runs` table (NEW)** — daemon-scoped, audit-supporting, future-extensible. Mirrors `repair_log`-style "who did what when" already in the codebase. Stable schema, one migration.
- Recomputing live is **not viable** — Op E over 33GB is exactly what we don't want to re-run on every `/status` GET.
- Dual-flavor keys (AM-11, A-5 ratified): single `BlobPruneSummary` dataclass carries both key sets; downstream FE/API readers branch on `destructive:bool` to pick the active set. Avoids two near-identical dataclasses.

**Evidence**: `daemon/services/checkpoint_prune.py:88-103` (BlobPruneSummary); `daemon/migrations/versions/20260524_000003_create_project_metadata_records_table.sql` (existing precedent for project-scoped kv); `daemon/services/maintenance.py:934` (today's discard point); `daemon/migrations/versions/20260714_000001_*.sql` (PG-only `DROP CONSTRAINT IF EXISTS` trap class — new table via `create_all` cannot regress this).

### Q3. Destructive safety model — guards, audit, backup

**Decision**: 
- **Origin guard (AM-1)** — `require_trusted_origin` FastAPI dependency on endpoints 2–5; allows no-Origin / same-origin / localhost-family / `MAINTENANCE_TRUSTED_ORIGINS` CSV; else 403 `origin_not_trusted`. Sole browser-borne defense layer; `/availability` exempt.
- **Refresh dry-run required** (≤ `MAINTENANCE_DRY_RUN_FRESH_SECONDS` default 300s) before execute accepted — server-side age check.
- **Explicit confirm payload**: `confirm: true` + echoed `expected_bytes` (server validates byte-equality with stored dry-run — AM-3, the ONLY scope pin).
- **No `idempotency_key`** (AM-17 DROPPED, A-1 DROPPED) — 409-adoption replaces it (on 409 from execute, FE adopts `details.run_id` and resumes polling).
- **Server-side busy/idle check is advisory, not refusal** (Q1 rationale).
- **Auto-cycle behavior unchanged** — env-flag dual-arm path stays for the AUTOMATIC path. Auto-cycle order D→E unchanged (AM-2 only applies to manual).
- **Audit every manual destructive run** in the `maintenance_runs` table per AM-15 (decision inputs + pre-state + outcome + failure). `triggered_by` captures `'system' | 'user'` (no session ids exist). One source of truth.
- **Kill-switch (AM-13):** `MAINTENANCE_ENDPOINTS_ENABLED` default `1`; OFF → endpoints 2–5 return 503 `maintenance_disabled`; `/availability` returns `state:"kill_switched"`. Auto-cycle's env dual-arm is **unaffected** by the kill-switch (INV-1) — correct by design, document in runbook.
- **Pre-cleanup backup step**: **OMIT in v1**. 33GB checkpoint_blobs backup copies are a disk-fill risk we should NOT ship in the same PR. Flag for architect as a v2 option (offer-default-off-with-warning). Recommend: revisit in a separate RFC after we see the first few manual runs succeed.

**Rationale**:
- The 6-gate model is a **mistake/staleness defense** — Origin guard is a separate **request-authenticator** for browser-borne destructive choreography. The two are complementary, not redundant.
- Refresh-window check is cheap (one row read, one datetime compare) and prevents the "I ran dry-run yesterday, click execute" class.
- Echoed byte count forces the operator to read the dry-run result, not just the dialog.
- `_is_idle()` blind-spots (maintenance.py:107, 167, 228) make it wrong as a hard gate.
- `maintenance_runs` IS the audit trail — no need for two tables.
- Kill-switch on the API surface only; auto-cycle stays destructive-if-armed. Documented.
- Backup is a real risk: prod DB is 34GB, ~33GB in `checkpoint_blobs`. A full backup would need ~33GB free on disk AND a copy operation that's another minute of disk pressure.

**Evidence**: migration.py:134-180 (confirm + ID pattern); maintenance.py:107, 167, 228 (_is_idle blind-spots); checkpoint_prune.py:170-186 (ZERO_REFS_FAIL_SAFE — destructive path must preserve this); `starlette/middleware/cors.py:155-176` (verified — `allow_explicit_origin` override fires only when `Cookie` header is present); `daemon/api.py:2613-2619` (current CORS posture); `daemon/services/plane_sync_watchdog_service.py:299-316` (`fail_stale_syncing` pattern for boot sweep, AM-7).

### Q4. API surface & extensibility — naming, auth, SSE

**Decision**:
- **Naming**: `/api/maintenance/checkpoint-cleanup/{availability,status,dry-run,execute,runs/{run_id}}`. Section-namespaced, kebab-case. Future sections: `/api/maintenance/db-vacuum/...`, `/api/maintenance/orphan-instances/...`. Single `daemon/routers/maintenance.py` with one `APIRouter` per section, mounted via one `include_router`.
- **Auth**: **no auth + Origin guard on destructive namespace + `MAINTENANCE_ENDPOINTS_ENABLED` kill-switch (default ON).** Browser-borne hostile choreography blocked; curl/LAN direct unchanged (= baseline zero-auth risk that already exists). Global posture (bind/CORS/auth) — **deferred to a separate cross-cutting ticket** (Focus Area 1 ratified contents, file during backlog grooming; see Out-of-Scope §).
- **Long destructive pass**: **202 + poll**, no SSE in v1. Established migration.py:223 (`GET /events`) SSE machinery exists, but adding it now doubles the surface area. v1 polling at 2s on `/runs/{run_id}` is sufficient for operator UX (operator is at the keyboard, expects 30s–5min waits). Flag SSE as v2 follow-up.
- **Cancellation:** NO cancel endpoint in v1 (no abort point in the prune loop today). Pre-built v2 insertion point = per-pair loop top (~10 lines cooperative `asyncio.Event`).

**Rationale**:
- Section-namespaced paths scale for future maintenance ops without router proliferation.
- Migration router used bare words (no sub-resource) because migration is the singleton. We have a multi-section future → use namespaced paths.
- No handler-timeout config exists today; HTTP client-side timeouts are common but not enforced server-side. Async poll avoids the whole question.
- No existing precedent for SSE on manual destructive ops — plane sync returns 200 (synchronous), migration has SSE. We have a worse tail than migration (5min vs 30s) so polling is acceptable.

**Evidence**: `daemon/api.py:2687-2726` (router registry); `daemon/routers/migration.py:134-223` (202+poll+SSE precedent — start handler `:144`, status `:188`, events `:223` [R-3 cite fix — was `:120-223`]); `daemon/routers/plane.py:54-170` (synchronous precedent); `daemon/routers/schemas.py:1-50` (structured pydantic patterns); `daemon/mcp/builtin_servers/plane.py:122-138` (kill-switch `PLANE_SYNC_ENABLED`/`PLANE_MCP_ENABLED` vocabulary precedent).

### Q5. FE structure — gear-menu entry pattern, registry, components

**Decision**:
- **Gear-menu entry**: availability-probe append (mirror `checkMigrationAvailability()`, app.ts:737-755). Branches on **`state === 'ready'`** (AM-14 state-enum gating). Section hides on every non-`ready` state (`backend_unsupported`, `subsystem_disabled`, `kill_switched`).
- **Route**: lazy `loadComponent` in `frontend/src/app/app.routes.ts`, gated with `canMatch` on `availability.state === 'ready'` (AM-14). **Route target [R-10]: the page shell (`MaintenanceComponent`)** — the shell renders the checkpoint-cleanup section component via the local sections registry; the section component is never the direct route target.
- **Section registry**: **local** inside the new Maintenance page (`@for` over `MaintenanceSection[]`). No shared registry across pages — premature abstraction.
- **ConfirmDialogComponent**: reuse for the destructive confirm flow (`destructive: true`).
- **Result display**: `<dl>` for status + `<pre>` JSON dump for full payload. No shared table component — greenfield.
- **Service (AM-14):** `checkpoint-cleanup.service.ts` **colocated** under `pages/maintenance/checkpoint-cleanup/` (matches extensibility requirement — multi-section surface must not share a single-section service file).
- **POLL_INTERVAL_MS (AM-14, A-10):** 2000 ms pinned as a `readonly` constant with a source-grep pin in `checkpoint-cleanup.component.spec.ts`.

**Rationale**:
- Availability-probe append is the established pattern for "show menu item only when backend supports it" (migration, plane). Maintenance has the same property (PG-only).
- State-enum gating (replacing boolean `eligible`) gives the FE a stable vocabulary that survives `MAINTENANCE_ENDPOINTS_ENABLED=0` (kill_switched), `subsystem_disabled` (transient startup race), and SQLite rejection (backend_unsupported) without boolean flags multiplying.
- Local registry keeps the section-scope clean and avoids inventing a cross-page registry that has one consumer.
- `ConfirmDialogComponent` already supports `destructive: true` (research §3.5) — no new dialog code needed.
- Material `dl` + `<pre>` is sufficient; no need to introduce a shared `<result-table>` yet.

**Evidence**: `frontend/src/app/app.ts:554-558` (static items); `frontend/src/app/app.ts:737-755` (probe pattern); `frontend/src/app/components/confirm-dialog/confirm-dialog.component.ts:30-50` (data shape); `frontend/src/app/app.routes.ts` (route registration pattern); `frontend/src/app/services/migration.service.ts` (service shape mirror).

### Q6. Testing — BE + FE + Playwright

**Decision**: (See §Test Strategy for the expanded AM-16 test list.)

- **BE**: disposable PG only via `tests/helpers/checkpoint_prune_pg.py` (per-test `ensemble_blob_prune_<uuid>` DB, DROP WITH FORCE on teardown). NEVER fresh SQLite (migration `20260714_000001` is PG-only). NEVER `ensemble_prod`. `uv run python -m pytest` from worktree root. Markers `integration` and `postgres`.
- **FE**: Jest logic-mirror specs + source-grep pins (confirm-dialog wiring, template branches, error code unions, `POLL_INTERVAL_MS = 2000`, skipped render, 409-adoption).
- **Playwright e2e**: `frontend/e2e/maintenance-checkpoint-cleanup.spec.ts`. Uses existing `frontend/playwright.config.ts` webServer auto-boot. **CRITICAL**: the spec sets a dedicated dev daemon env pointing at disposable PG (scrub `POSTGRES_*` env, set `ENSEMBLE_DB_DSN` to disposable). E2e validates the **happy path with destructive=true** ONLY against the disposable dev daemon — never against prod. The spec asserts that `maintenance_runs` row was written, then asserts that the dev daemon's checkpoint_blobs shrunk by the expected bytes.

**Rationale**:
- Established convention: PG-only tests, no SQLite, no prod touch (research §1.2, project critical-notes).
- Source-grep pins prevent FE regression when the service signature changes (count-pin and order-pin patterns from Job Queue blueprint).
- Playwright webServer auto-boots both BE and FE — e2e is the only place the full UI flow is validated. Polling-based result panel MUST be tested in a real browser.

**Evidence**: `tests/helpers/checkpoint_prune_pg.py` (disposable-PG pattern); `frontend/playwright.config.ts` (webServer array); Job Queue blueprint (Testing & QC Conventions §WHOLE-TREE RE-RUN, ORDER-PIN EXCEPTION, COUNT-CLAIM DECOMPOSITION, SELF-READING PIN TAUTOLOGY).

---

## Standardization Boundary (AM-18, Focus Area 4)

The Maintenance surface is designed to scale to multiple sections (db-vacuum, orphan-instances, …). The boundary below MUST be uniform across all sections; everything else MAY be section-specific.

| Surface | MUST (uniform) | MAY (section-specific) |
|---|---|---|
| Availability response | `{eligible, state, backend, reason}` + state enum vocabulary (`ready`\|`backend_unsupported`\|`subsystem_disabled`\|`kill_switched`); `eligible` derived | — |
| Error body | `{error, message, details?}` `plane.py` shape; stable code table (the v1 codes in §Error codes are the seed) | new section codes appended to enum |
| Run lifecycle | status vocabulary `running`\|`succeeded`\|`failed`\|`interrupted`; 202 + `run_id` + poll; boot-sweep semantics (AM-7); single-flight gate (AM-4); `overlap_refused` state deleted (AM-6); `interrupted` carries `error.code="run_interrupted"` | summary JSON shape (per-section) |
| Concurrency | single-flight gate including DB partial-unique-index claim; kill-switch `MAINTENANCE_ENDPOINTS_ENABLED` (default ON) | — |
| Persistence | `maintenance_runs` schema (TEXT PK, TEXT ISO timestamps, JSONBType, partial unique index `(section) WHERE status='running'`, composite `(section, completed_at)`); `section` discriminator | summary / error / dry-run-summary payload contents |
| Origin guard (AM-1) | `require_trusted_origin` on the destructive namespace; `/availability` exempt | — |
| Router layout | single `daemon/routers/maintenance.py` with one `APIRouter` per section; one `include_router` at the api.py seam; per-section files only above 5 sections | section-specific route handlers |
| Everything else | — | dry-run/execute payloads, config block, FE rendering, summary keys, env dual-arm |

### BE-driven discovery migration thresholds (Focus Area 4, deferred)

Migrate from FE-local registry + per-section availability probe to BE-driven discovery (`GET /api/maintenance/sections`) **only when ANY of:**
- third-party / plugin sections exist
- per-deployment section config (e.g. visibility toggles)
- >5 sections with BE-controlled labels / icons / ordering
- per-user visibility

None hold for v1. Pre-build nothing.

Production is monolithic (daemon serves the FE dist via the SPA catch-all, `api.py:2816-2828` → FE version = BE version), which kills the version-skew argument. Dev-skew (`:4199` proxy) is handled by section components tolerating `state != ready`.

---

## Re-freeze Checklist Status (per Focus Area 2 of architecture-recommendation)

Per INV-5; the architect-recommendation's 5-item checklist:

| # | Item | Status | Disposition |
|---|---|---|---|
| 1 | `plan-overview.md` §API Contract: §3 dry-run + `skipped[]`; §2 `/status` summary example (blobs.skipped + destructive-flavor example); §4 execute checks (Op-E-first note; `advisory` field on 202; fix check #6 typo "200"→"202"); error-code table (+`maintenance_disabled` 503, +`origin_not_trusted` 403); `Z`→`+00:00` example sweep (A-7); run_id format examples | **satisfied** | this document, §API Contract (applied at v2; carried into the v3 freeze) |
| 2 | Addendum register: mark A-1 DROPPED, A-2/A-3/A-5/A-6/A-8/A-11 RATIFIED, A-4 RATIFIED (renamed), A-7/A-9/A-10/A-12 CONFIRMED; §6.5 CLOSED (overridden by A-6) | **satisfied** | this document, §Contract-Feedback Register addendum — dispositions updated |
| 3 | `phase1-backend.md` schema re-sync + 4 new BE cases: (a) dry-run renders seeded ZERO_REFS pair in `skipped[]`; (b) execute succeeds when a new skip pair appeared in-window (bytes unaffected); (c) excess-rows execute does NOT mismatch (AM-2 proof); (d) `last_run` excludes `manual_dry_run` (AM-9) | **satisfied (verified at fan-in 2026-09-27)** | phase1-backend.md amended 569→724 lines, 195 inline AM tags; cases (a)–(d) = cases 43/56/57/58; AM-15 schema re-synced verbatim; verification: dispatcher spot-checks (AM-tag density + key-token greps) |
| 4 | `phase2-frontend.md` schema re-sync + FE additions: skipped list render + reason badge map + generic `ERROR:*` fallback, 409-adoption behavior, `+00:00` parsing note | **satisfied (verified at fan-in 2026-09-27)** | phase2-frontend.md amended 816→1124 lines, 166 inline AM tags; skipped render + badge map + ERROR:* fallback, 409-adoption wired (service+component+spec+pin), +00:00 note documented; verification: dispatcher spot-checks (AM-tag density + key-token greps) |
| 5 | Architect re-stamps the freeze (date + approver) before Phase 2 merges | **partially satisfied — architect re-stamped v2; v3 delta stamp pending** | The architect re-stamped **v2** on 2026-09-27 (see the historical re-stamp line under the freeze marker). **Contract v3 (this re-freeze) is the reviewer-fix freeze (C-1/C-2/W-1 + R-items applied); it awaits the architect's quick delta pass** — the delta is exactly the v3 changelog line: literals/shape unification, W-1 mechanism restatement + INV-13, count reconciliation. Phase-file workers conform to v3 in the interim. |
| 6 | **v3 fix pass (this row — added by the reviewer-fix re-freeze 2026-09-27)** | **stamped** | Architect delta-stamp landed 2026-09-27 (see the freeze-marker stamp above the v3 changelog) — this row's `applied` status is now `stamped`. Reviewer findings applied across plan-overview / phase1-backend / phase2-frontend / decision-log: [C-1] 404 error-code literal unified to `not_found` at every site (stale literal grep-verified 0 remaining); [C-2] 409 bodies nested under `details` everywhere (case 63 + `adoptRunIdFromError` pins untouched); [W-1] AM-2 mechanism restated at 3 sites + INV-13 completion-gate prohibition added (plan-overview §Mandatory Invariants + phase1 §0); [R-1] BE count reconciled = 66 *(66 at v3; 67 after [CF-6 doc-repair, v3.1])*; [R-2] FE pins = 15 (incl. new sections-registry pin), Playwright = 14; [R-3] line-cites fixed; [R-5]–[R-10] doc-level; [R-11]–[R-17] PR/task annotations; [R-18]–[R-23] close-out rows; leader rulings (a)/(b) in decision-log. Tag `[C-n]/[W-n]/[R-n]` inline at every application point. |

---

## Risks (post-amendment, severity-ordered)

- 🔴 **AM-2 unapplied → silent over-deletion on the primary flow** (excess rows present). **Blocking.** Manual execute reuses auto-cycle's D→E order; dry-run's anti-join counts blobs against *all current checkpoint rows*; D-first unreferences blobs the dry-run counted → every execute check PASSES (check 5 compares the echo against the stored dry-run row — always equal) and the blob pass silently over-deletes beyond the confirmed echo [W-1 restated, v3; caught only by regression cases 44/57, never by the gate — see INV-13]. Mitigation: AM-2 (Op E→D in manual path; auto-cycle unchanged). Test: Phase 1 task 9 case (c).
- 🔴 **AM-1 unapplied → http-origin browser page can execute the destructive op unauthenticated** (readable CORS responses). **Blocking.** Under CORS `*` + `allow_credentials=True`, an http-origin page can run dry-run → read `run_id`+`expected_bytes` → execute with valid echoes. Mitigation: `require_trusted_origin` FastAPI dependency on the 4 non-availability endpoints; localhost-family auto-trust; default-empty `MAINTENANCE_TRUSTED_ORIGINS` CSV. Tests: Phase 1 task 9 (Origin guard matrix); Phase 2 task 7 (Playwright cross-origin 403).
- 🟡 **Bytes-pin blind spot:** in-window skip-set drift invisible to byte equality — accepted (deletion-conservative; per-pair fail-safes are the true boundary). AM-3.
- 🟡 **Kill-switch is boot-read env** — flipping requires restart (bounded by Phase 3 activation anyway). INV-1 preserved (kill-switch gates API surface only; auto-cycle stays destructive-if-armed).
- 🟡 **Dev dual-daemon on shared PG:** closed by AM-5 partial unique index claim. Phase 1 must verify partial-index render on SQLite (`create_all` with `postgresql_where`/`sqlite_where`); fallback = plain conditional `INSERT … WHERE NOT EXISTS` with documented racy-belt caveat.
- 🟡 **Hung pass without restart wedges the maintenance surface only; no watchdog in v1** (honest). Operator recourse = restart → boot sweep (AM-7).
- 🟢 **Dry-run minutes-scale on 33 GB** (412 ms example aspirational) — FE copy + freshness-window interplay. AM-14 FE copy reflects minutes-scale honestly.
- 🟢 **`maintenance_runs` growth** deferred with quantified trigger (>100k rows or >100 MB) + skipped-cap guard (1000 entries + `skipped_truncated:true`).
- 🟢 **Local malicious http page passes the Origin guard** (localhost-family auto-trust) — accepted as machine-level compromise (leader ruling 1).

---

## Test Strategy (post-AM-16)

### BE (Phase 1)

**Total: 67 enumerated BE cases** [R-1 v3 recount + case 67 added [CF-6 doc-repair, v3.1]; every count site agrees on 67: §4.1 = 10 (incl. 9a), §4.2 = 29 (incl. 11a, 17a), §4.3 = 27 (numbering 37–67, with 53 retired/replaced by 64; case 67 = A-8 catch-all shape pin), §4.4 wiring pin = 1]:

- **Unit** (`tests/unit/test_maintenance_checkpoint_cleanup_service.py`):
  - `MaintenanceApiService.availability()` shape (state enum + derived `eligible`)
  - `dry_run()` produces `CheckpointRowPruneSummary` + `BlobPruneSummary` with skipped[] + would_delete_count + would_free_bytes
  - `execute()` validation chain (confirm → dry-run freshness → byte match → lock acquire)
  - `MaintenanceRunLock` acquire/release semantics (mocked)
  - 400/403/404/409/503 error body shape per stable code
  - **Origin guard matrix** (4 cases per AM-16): no-Origin allowed; same-origin allowed; localhost allowed; `MAINTENANCE_TRUSTED_ORIGINS` match allowed; `evil.example` → 403 `origin_not_trusted`; `Origin: null` → 403
- **Integration** (`tests/integration/test_maintenance_checkpoint_cleanup_api.py`) on disposable PG:
  - End-to-end `GET /status → POST /dry-run → POST /execute → poll /runs/{id}` happy path
  - Auto-cycle concurrent → 409
  - Manual execute concurrent with another manual → 409
  - Dry-run stale (advance `now_utc_naive()` clock) → 400 `dry_run_stale`
  - Byte mismatch → 400 `byte_count_mismatch`
  - Confirm missing → 400 `confirm_required`
  - Destructive path on SQLite → 503 `backend_unsupported`
  - ZERO_REFS_FAIL_SAFE preserved under `destructive=True`
  - Auto-cycle env-gate behavior unchanged (separate test, sets `CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=0`, asserts destructive=true override on auto-cycle path is REFUSED)
  - **(AM-16 a)** dry-run renders seeded ZERO_REFS pair in `skipped[]` (verify reason code = `"ZERO_REFS_FAIL_SAFE"`)
  - **(AM-16 b)** execute succeeds when a new skip pair appeared in-window (bytes unaffected — byte_count_mismatch check still passes since skip pairs contribute 0 bytes)
  - **(AM-16 c)** excess-rows execute does NOT mismatch (AM-2 proof; fails by silent over-deletion under the old D→E order — W-1 mechanism, INV-13: no completion gate)
  - **(AM-16 d)** `last_run` excludes `manual_dry_run` (AM-9)
  - **(AM-16 boot sweep)** boot sweep unconditionally CAS `running → interrupted` for any stale row at lifespan start; no age gate
  - **(AM-16 dual-arm)** auto-cycle running + manual execute arrives → 409 `run_in_flight` (single global lane); auto-cycle losing the lock = non-raising skip + last_run update + DEBUG with in-flight run_id
  - **(AM-16 kill-switch)** `MAINTENANCE_ENDPOINTS_ENABLED=0` → endpoints 2–5 return 503 `maintenance_disabled`; `/availability` returns `state:"kill_switched"`
- **Migration**: PG-only; `IF NOT EXISTS` clauses; rollback path = drop table. Verify partial-index render on SQLite via `create_all` introspection.
- **Test infra**:
  - `uv run python -m pytest tests/integration/test_maintenance_checkpoint_cleanup_api.py -m integration`
  - Scrub `POSTGRES_*` env; use `PG_TEST_*` env (matches `tests/helpers/checkpoint_prune_pg.py`)
  - NEVER `ensemble_prod` (asserted in fixture)

### FE (Phase 2)

- **Jest** (`checkpoint-cleanup.component.spec.ts`):
  - Render status block from `status()` observable (dual-flavor branching on `destructive`)
  - Dry-run button enabled-state transitions (idle → running → done)
  - Confirm dialog opens on execute click; cancel → no execute; confirm → execute
  - Error rendering per stable error code (400/403/404/409/503)
  - Polling lifecycle (start on execute, stop on terminal status)
  - **(AM-16)** Skipped pairs render — badge line + reason badge map (known codes) + generic `ERROR:*` fallback
  - **(AM-16)** 409-adoption — on 409 from execute, adopt `details.run_id`, resume polling, no error toast
- **Source-grep pins** (mirrors Job Queue Testing conventions §ORDER-PIN EXCEPTION + §COUNT-CLAIM DECOMPOSITION) — **15 pins total in phase2 T6.3 [R-2 + sections-registry addition, v3 fix pass]**:
  - `confirm-dialog` is wired in template (grep pin)
  - Error code union is exhaustive (count pin)
  - Five service methods present (count pin)
  - `POLL_INTERVAL_MS = 2000` is a `readonly` constant in spec (grep pin)
  - No `idempotency_key` in execute payload (grep pin; AM-17)
  - State-enum branch on `state === 'ready'` in probe (grep pin)
  - **Sections registry is load-bearing** ([R-addition] source-grep pin: the Maintenance page renders its sections FROM the `sections` registry array — user requirement #2 extensibility; the registry must not be decorative)
- **Playwright e2e** (`frontend/e2e/maintenance-checkpoint-cleanup.spec.ts`):
  - Boots dev daemon against disposable PG (`ENSEMBLE_DB_DSN` overridden)
  - Asserts env at start: refuses to run if `ENSEMBLE_DB_DSN` matches `ensemble_prod`
  - Navigates to `/maintenance/checkpoint-cleanup`
  - Asserts status block renders (dual-flavor branch coverage)
  - Clicks dry-run, asserts would-delete counts/bytes + skipped badge appear
  - Clicks execute, asserts confirm dialog opens, clicks Cleanup now
  - Asserts poll resolves to terminal status, asserts result panel renders
  - Asserts `maintenance_runs` row was written (via a dedicated debug endpoint OR by re-fetching `/status` and checking `last_run.run_id`)
  - **(AM-16)** Second tab sees 409, adopts run_id, resumes polling (no error toast)
  - **(AM-16)** Cross-origin 403: launch second Playwright context with `Origin: http://evil.example` set; expect 403 `origin_not_trusted` on /dry-run and /execute
  - **(AM-16)** Kill-switch hide: boot dev daemon with `MAINTENANCE_ENDPOINTS_ENABLED=0`; assert gear menu does NOT contain Maintenance; `/availability` returns `state:"kill_switched"`
  - Cleans up disposable DB on teardown

### Manual smoke (Phase 3)

- After Phase 1 + Phase 2 merge + FE dist rebuild + daemon restart:
  - Open the gear menu → "Maintenance" entry visible
  - Status block: keep-N=3, interval=24h, no prior run
  - Dry-run: should be empty (no excess threads + no unreferenced blobs on a fresh daemon)
  - Trigger an excess-checkpoint condition (run a test that creates 5 checkpoints for one thread), then dry-run → should show 2 excess rows
  - Execute with confirm → poll → succeeded
  - Re-check `/status` → last_run populated
  - Trigger concurrent run (open two browser tabs) → second tab gets 409, adopts run_id, resumes polling
  - Re-trigger with `MAINTENANCE_ENDPOINTS_ENABLED=0` → gear menu entry disappears; /availability returns `state:"kill_switched"`; other endpoints return 503

---

## Out of Scope (explicit)

1. **Authentication / authorization on the maintenance endpoints** (sub-feature). For v1, Origin guard (AM-1) is the sole browser-borne defense; kill-switch (AM-13) provides a hard API-surface gate. **Global posture hardening** (DAEMON_HOST → 127.0.0.1 + LAN-bind boot warning; CORS `*` → env-driven origin list; optional `ENSEMBLE_API_BEARER_TOKEN` for `/api/*` minus health; middleware token-bucket rate limit; failed-auth audit log) — **contents ratified in Focus Area 1, filing deferred to backlog grooming** per leader ruling 2. **Do NOT add `Access-Control-Allow-Private-Network: true`** — PNA is currently the strongest accidental defense.
2. **Pre-cleanup backup step**. 33GB copies = disk-fill risk. Flag for architect as a v2 option (offer-default-off-with-warning). Recommend: revisit in a separate RFC after we see the first few manual runs succeed.
3. **SSE / live progress streaming + cancel endpoint**. Polling at 2s on `/runs/{run_id}` is sufficient for v1; no abort point exists in the prune loop today. **Both SSE and cancel are deferred to v2**, leader-endorsed (ruling 3). **v2 insertion point** for cancel = per-pair loop top (`checkpoint_prune.py:152-258`), cooperative `asyncio.Event`, ~10 lines. SSE insertion point = pre-existing migration.py:223 (`GET /events`) machinery.
4. **Other maintenance sections** (DB vacuum, orphan instances, etc.). v2+ sections. Cross-section listing (`GET /api/maintenance/runs` across sections) is **deferred to v2** — no v1 consumer; `maintenance_runs.section` is the per-section discriminator.
5. **Modifying the auto-cycle's env-flag gate or default interval**. Both stay at current values.
6. **Repair log integration for manual runs**. `maintenance_runs` IS the audit trail; no double-write.
7. **Cleanup of the maintenance_runs table itself** (archival/pruning). Future operational concern; revisit >100k rows or >100 MB.
8. **Live `ensemble_prod` validation**. Activation is on disposable-PG dev daemon only. The actual prod migration is a separate operations runbook.
9. **UI for editing `checkpoint_max_per_thread` or interval**. Status display only; config still comes from env. UI for config = future section.
10. **`idempotency_key` payload field on `/execute`** (AM-17 DROPPED). 409-adoption contract replaces it.

---

## Open Questions for the Architect (post-amendment dispositions)

The eight original questions are **RESOLVED** with the architect's dispositions (cited from `architecture-recommendation.md` OQ dispositions line and the §6.x subsections). No new open questions are introduced by the amendments.

### §6.1. Error detail body shape — structured dict (plane.py) vs bare string (migration.py)?

**RESOLVED.** Architect disposition: §6.1 = A8 structured dict ✓. **Adopted: structured dict (`plane.py:71-170` shape), binding for all 5 endpoints.** Confirmed in error-code table.

### §6.2. Auth/permissions on destructive endpoints — keep zero-auth posture, add a kill-switch, or full auth?

**RESOLVED.** Architect disposition: §6.2 = AM-1/AM-13 ✓. **Adopted: Origin guard (AM-1) on the destructive namespace + `MAINTENANCE_ENDPOINTS_ENABLED` kill-switch (default ON, AM-13). No auth in v1.** Global posture (bind/CORS/auth) deferred to a separate cross-cutting ticket (leader ruling 2 — backlog grooming).

### §6.3. Should Maintenance show in the gear menu when the auto-cycle is disabled?

**RESOLVED.** Architect disposition: §6.3 = AM-13 state enum ✓. **Adopted: availability gains `state` enum; `eligible` derived; gear menu hides on every non-`ready` state.** `subsystem_disabled` = service not wired (transient startup race keeps the frozen 503 `not_initialized`). `MAINTENANCE_SERVICE_DISABLED` idea dropped (YAGNI; one switch is enough).

### §6.4. Dry-run freshness window — 5 minutes enough, too short, too long?

**RESOLVED.** Architect disposition: §6.4 = 5 min + env, keep ✓. **Adopted: `MAINTENANCE_DRY_RUN_FRESH_SECONDS=300` default; configurable via env.** Honest FE copy that dry-run may take minutes on 33 GB.

### §6.5. Should `/status`'s `last_run` be the most recent of ANY kind (auto/manual) or split?

**RESOLVED (CLOSED, overridden).** Architect disposition: §6.5 = AM-9 (A-6 wins) ✓. **Adopted: `last_run` = latest `succeeded|failed` of `kind ∈ {auto, manual_execute}`; `manual_dry_run` never surfaces.** The earlier v1-draft recommendation (most recent of any kind) is overridden. Per-register addendum: §6.5 CLOSED.

### §6.6. Concurrency limit on the manual path — single in-flight globally, or per-kind?

**RESOLVED.** Architect disposition: §6.6 = single global lane ✓. **Adopted: single in-flight globally, enforced by `MaintenanceRunLock` + AM-5 partial unique index DB claim.** Both manual classes + auto serialize on ONE global lane.

### §6.7. Should the destructive manual path log to a separate "operator action" audit channel?

**RESOLVED.** Architect disposition: §6.7 = one channel, AM-15 ✓. **Adopted: `maintenance_runs` IS the audit trail; no separate channel.** Forensic fields (`requester_json` {peer_ip, user_agent, origin}) included per AM-15.

### §6.8. Should the `confirm: true` payload also require an idempotency key (UUID) to prevent double-clicks?

**RESOLVED (DROPPED).** Architect disposition: §6.8 = A-1 dropped, AM-17 ✓. **Adopted: NO `idempotency_key` field; on 409 from execute, FE adopts `details.run_id` and resumes polling.** Security framing removed; the Origin guard (not a key) is the browser defense. Per-register addendum: A-1 DROPPED.

---

## Mandatory Invariants (carried from dispatch)

These are HARD constraints. Any plan that violates them is wrong.

- **INV-1**: Auto-cycle behavior and defaults UNCHANGED. MaintenanceService loop, idle gate, env dual-arm for the AUTOMATIC path stay as-is. **Auto-cycle order stays D→E** (AM-2 applies to manual execute only).
- **INV-2**: Manual execute must NOT require pre-arming env flags. Design the explicit destructive path into checkpoint_prune (e.g., a `destructive` override kwarg on `prune_unreferenced_blobs`) WITHOUT loosening the auto-cycle env gate.
- **INV-3**: Preserve the SERIALIZABLE+retry wrap and ZERO_REFS_FAIL_SAFE in checkpoint_prune.py.
- **INV-4**: An overlap guard between auto cycle and manual runs MUST be designed (none exists today). **Single-flight gate (AM-4 + AM-5): `MaintenanceRunLock` + partial unique index `(section) WHERE status='running'`.**
- **INV-5**: The frozen API contract v3 (this document's API Contract section) is the single dependency for Phase 2. No Phase 2 code merges without contract-freeze signoff.
- **INV-6**: Capture BlobPruneSummary instead of discarding (maintenance.py:934); assemble Op D structured counts.
- **INV-7**: Every new timestamp bind uses `now_utc_naive()` (naive-UTC convention). **For TEXT ISO columns in `maintenance_runs`, use `now_utc_iso()`** (sidesteps naive/tz trap entirely per repo precedent — zero `sa.DateTime` columns in repo).
- **INV-8**: New migrations are PG-safe; prefer zero-migration options (shared_context_metadata / pre-create_all SQLModel registration) where equal — we did NOT prefer the zero-migration option because the use case (operator audit) genuinely needs a table.
- **INV-9 (new, AM-2)**: Manual execute composes Op E → Op D. Auto-cycle order unchanged.
- **INV-10 (new, AM-1)**: `require_trusted_origin` is the **first check** on endpoints 2–5. `/availability` exempt.
- **INV-11 (new, AM-7)**: Boot sweep unconditionally CAS `running → interrupted` at lifespan start. No live watchdog in v1.
- **INV-12 (new, AM-15)**: `maintenance_runs` schema follows the conventions-validated form (TEXT PK, TEXT ISO timestamps, JSONBType, partial unique index, composite index). `skipped[]` capped at 1000 + `skipped_truncated:true`.
- **INV-13 (new, v3 fix pass [W-1])**: **NO server-side actual-vs-expected completion gate.** The byte-equality check (AM-3) is a PRE-RUN scope pin only — it compares the client's echoed `expected_bytes` against the stored dry-run row. A post-run comparison of actual deleted bytes vs `expected_bytes` is **FORBIDDEN**: benign in-window drift (new skip pairs appearing, live-app writes) would false-refuse completed runs — the opposite of AM-3's ratified design. The AM-2 E-first ordering is what keeps actual == expected on the happy path; the D-first failure mode (silent over-deletion beyond the confirmed echo) is caught by regression cases 44/57, never by a gate. Implementers may not "fix" the ordering by adding this gate.

---

## Activation Gates (Phase 3 contract)

Per project convention (activation-pending notes are a known failure mode):

1. **Phase 1 merge** → contract v3 frozen.
2. **Phase 2 merge** → FE gate.
3. **`make install` rebuilds FE dist** (daemon rebuild does NOT cover FE; project critical-notes). **Order is FE build FIRST → daemon rebuild LAST [R-8]** — the serving daemon must never present a stale dist against the new API; see phase2 PR-5.
4. **Daemon rebuild + restart** — required for the new router + service wiring to be live; runs AFTER the FE dist rebuild.
5. **Live spot-check on disposable-PG dev daemon** — close-out proof for the activation-pending notes.
6. **No live `ensemble_prod` validation in this PR.** Prod migration is a separate operations runbook; flag for the operator.

---

## Sign-off Checklist

- [x] Architect review on §6.1–§6.8 (8 open questions) — all RESOLVED via AM-1…AM-18
- [ ] Phase 1 lead confirms: contract v3 signed, all 9 tasks have acceptance criteria (including AM-16 new BE cases)
- [ ] Phase 2 lead confirms: contract v3 is buildable as-is, no missing endpoints
- [ ] Test lead confirms: disposable-PG harness ready, Playwright spec drafted (with AM-16 cross-origin 403 + kill-switch hide + 409-adoption)
- [ ] Ops lead confirms: activation runbook drafted (separate doc)

### Close-out checklist additions (v3 fix pass [R-18…R-23] — verify at PR close-out)

| # | Close-out item | Owning phase / PR |
|---|---|---|
| [R-18] | `run_id` format documented as a **superset of the `migration_YYYYMMDD_HHMMSS` precedent** (`maintenance_run_identity.py` docstring states the lineage: same date-time core + microseconds + hex8 suffix) | Phase 1 / PR-2 |
| [R-19] | `run_checkpoint_prunes` carries a **`MANUAL-ONLY` docstring tag** + an **AST pin** asserting the auto-cycle `execute()` never calls it (auto keeps its inline D→E sequence) | Phase 1 / PR-1 |
| [R-20] | `maintenance_service=None` **disables the busy advisory** (probe returns True → `advisory: null` always) — degradation documented in the service docstring so the silent-downgrade is discoverable | Phase 1 / PR-3 |
| [R-21] | **INFO log for suspect origins**: `require_trusted_origin` logs ONE INFO line (origin, peer_ip, path) on every 403 refusal — forensics for near-miss requests | Phase 1 / PR-3 |
| [R-22] | **Lazy-load note**: page structure stays flat section list; **revisit page structure (per-section lazy chunks) if sections ever exceed 5** (alignment with the BE-driven discovery thresholds) | Phase 2 |
| [R-23] | **Stale-dry-run FE wording aligns to phase1's seed-old-row test approach**: copy describes age-vs-persisted-`started_at` staleness (the exact condition phase1 test 24 proves by seeding an old row — no clock skew implied) | Phase 2 |

---

## Addendum — Contract-Feedback Register (post-amendment dispositions)

Date: 2026-09-27 · Aggregated by: planner[v2] (dispatcher) · Sources: `phase1-backend.md` §7 (BE-CF-1…8) and `phase2-frontend.md` §Contract Feedback (FE-CF-1…5) · Updated after architect amendments AM-1…AM-18.

Deduped register (FE and BE flags describing the same seam are merged) — **DISPOSITIONS UPDATED**:

| # | Seam | Flagged by | Default path in phase plans | Architect decision (post-AM) |
|---|------|------------|------------------------------|------------------------------|
| A-1 | `POST /execute` payload `idempotency_key` (↔ OQ §6.8) | FE-CF-1 | FE generates `crypto.randomUUID()` and sends it; BE tolerance assumed | **DROPPED** (AM-17). 409-adoption replaces: on 409 from execute, FE adopts `details.run_id` and resumes polling. Security framing removed. |
| A-2 | Dry-run blob accounting fields | BE-CF-1 | BE adds additive `would_delete_count/bytes` fields to `BlobPruneSummary` | **RATIFIED** (AM-11). Canonical names: `would_delete_count`, `would_free_bytes`. Dry-run flavor additionally carries `would_delete.blobs`/`would_delete.bytes` for destructive-flavor symmetry. |
| A-3 | ZERO_REFS_FAIL_SAFE surfacing in dry-run response | BE-CF-6 | additive `skipped: [{thread_id, checkpoint_ns, reason}]` | **RATIFIED** (AM-10). Reason enum closed set `{ZERO_REFS_FAIL_SAFE, MAX_REFS_EXCEEDED}` ∪ open `ERROR:<ExceptionName>`. Informational only; not part of the confirm echo (AM-3). |
| A-4 | Kill-switch error code (↔ OQ §6.2) | BE-CF-7 | add `maintenance_disabled` → 503 row to the error-code table | **RATIFIED — renamed semantics** (AM-13). `MAINTENANCE_ENDPOINTS_ENABLED` default `1`; four endpoints 503 `maintenance_disabled`; `/availability` returns `state:"kill_switched"`. The earlier `MAINTENANCE_SERVICE_DISABLED` idea is DROPPED (YAGNI). |
| A-5 | `/status` summary key flavor | BE-CF-2 | dry-run flavor (`would_delete`/`would_free_bytes`) vs destructive flavor (`deleted`/`bytes_freed`, `destructive: true`); FE branches on `destructive` | **RATIFIED** (AM-11). Dual-flavor keys present on every summary blob; FE branches on `destructive:bool`. |
| A-6 | `/status` `last_run` scope (↔ OQ §6.5) | BE-CF-3 | default excludes `manual_dry_run` from `last_run` (dry-run history queryable via `/runs/{id}`) | **RATIFIED** (AM-9). `last_run` = latest `succeeded|failed` of `kind ∈ {auto, manual_execute}`; `manual_dry_run` never surfaces. **§6.5 CLOSED.** |
| A-7 | Wire timestamp suffix | BE-CF-5 | keep `to_utc_iso()` `+00:00` (JS `Date`-equivalent to `Z`); no formatter wrap | **CONFIRMED** (cosmetic). `+00:00` sweep applied to all examples in this document. |
| A-8 | Error detail body shape (↔ OQ §6.1) | BE-CF-4, FE | structured dict (plane.py style) binding for all 5 endpoints | **RATIFIED** (A-8). Adopted in §Common Error Detail Body Shape and §Error codes table. |
| A-9 | FE service file location | FE-CF-3 | colocated `pages/maintenance/checkpoint-cleanup/` (matches extensibility requirement) vs `services/maintenance.service.ts` | **CONFIRMED** (AM-14). Colocated service RATIFIED. |
| A-10 | Poll interval | FE-CF-4 | FE default 2000 ms + source-grep pin | **CONFIRMED** (AM-14). `POLL_INTERVAL_MS = 2000` as `readonly` constant + source-grep pin in `checkpoint-cleanup.component.spec.ts`. |
| A-11 | `advisory: "system_busy"` field on 202 | FE-CF-2 | referenced in Q1, absent from frozen body; FE tolerates extra keys | **RATIFIED** (AM-12). 202 body gains `advisory: "system_busy" \| null` + `expected_duration_ms_hint` (= the referenced dry-run's `duration_ms`; **unit: ms** — canonical definition in §4 `/execute` [R-5]). |
| A-12 | Migration wording | BE-CF-8 | repo pattern: raw `.sql` (doc + manual apply) + `SQLModel.metadata.create_all` + `_ensure_postgres_columns` mirror; runner is NO-OP on PG | **CONFIRMED** (AM-15). The new table is built by `SQLModel.metadata.create_all` on BOTH drivers at boot; **no `_ensure_postgres_columns` entries** (new tables need none). `-- MANUAL: TRUE` on the raw `.sql` for canonical doc. |

**Re-freeze rule (INV-5):** this document carries the **API Contract v3 — frozen 2026-09-27** marker (v2→v3: C-1/C-2 literals + shape unified; W-1 mechanism restated + completion-gate prohibition added; counts reconciled). Any further drift must update this document and re-freeze.
