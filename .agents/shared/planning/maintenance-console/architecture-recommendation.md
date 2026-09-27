# Architecture Recommendation: Maintenance Console — Section 1 (Checkpoint Cleanup)

Date: 2026-09-27
Architect: architect (controller) — competitive/dimension fan-out, 4 workers, all reports received
Worker instances: `ab54852a` (security-design, corrected after adjudication challenge) · `f8c2b821` (resilience-design) · `676e4a77` (data-flow-design) · `94df2787` (structural-design)
Input plan: `.agents/shared/planning/maintenance-console/{plan-overview,research-findings,phase1-backend,phase2-frontend}.md` @ `feature/maintenance-console 666c089d`
Mode: Standard Design (dimension fan-out). Council not triggered (0–1 of 4 criteria; contested sub-decisions resolved by evidence-cited worker analysis + architect rulings below).

---

## ARCHITECTURE VERDICT: **READY-WITH-AMENDMENTS**

The plan is structurally sound, evidence-cited, and builds on verified repo precedent. It must NOT proceed to Phase 1 until the amendments below are applied and the contract re-frozen. Two findings are blocking-class:

- 🔴 **BLOCKER 1 — Op D→E ordering deadlock** (AM-2): as drafted, manual execute reuses the auto-cycle's D-then-E order; the dry-run's anti-join counts blobs against *all current checkpoint rows*, so whenever excess rows exist, execute deletes more blob bytes than the dry-run promised → the `byte_count_mismatch` gate refuses every attempt, and re-running dry-run reproduces the same undercount. The primary operator flow is unshippable without AM-2.
- 🔴 **BLOCKER 2 — browser-borne destructive choreography is real** (AM-1): under the daemon's actual CORS config (`allow_origins=["*"]` + `allow_credentials=True`, `daemon/api.py:2613-2619`), **non-credentialed cross-origin fetches can read response bodies** (verified against installed Starlette `middleware/cors.py`: the explicit-origin override fires only on cookie-bearing requests). An http-origin page can therefore run dry-run → *read* `run_id`+`expected_bytes` → execute with valid echoes. None of the 6 gates, and not the proposed `idempotency_key`, stop this class. The feature adds Origin validation to the destructive namespace (AM-1).

Everything else is ratification with targeted corrections. Individual focus-area recommendations follow; the consolidated amendment list (AM-1…AM-18) is authoritative.

---

## Focus Area 1 — Security posture (OQ §6.2, Register A-4)

### Finding (corrected mechanics — this overrides the plan's assumption)

| Attacker class | Can complete dry-run→read→execute today? | Why |
|---|---|---|
| **http-origin page (LAN or localhost)** | **YES** | http→http: no mixed-content block; preflight passes (ACAM `*`, ACAH `*`); actual response carries `ACAO: *` for non-credentialed fetch → body readable (`starlette/middleware/cors.py` L155-176: the `allow_explicit_origin` override fires only when a `Cookie` header is present). |
| https public page → http LAN target | No | Active mixed-content blocking. |
| https public page → https LAN target | No | Chrome Private Network Access preflight requires `Access-Control-Allow-Private-Network: true`; Starlette default `allow_private_network=False` → preflight 400. **Do not break this defense.** |
| Non-browser LAN client (curl, scripts) | Yes — unchanged | CORS is browser-only; baseline zero-auth `0.0.0.0` (config.py:517-518) already exposes full RCE-equivalent surface (instance spawn → shell tools). Marginal risk of this feature = convenience of one-shot irreversible deletion, not a new capability class. |

Consequence: the 6-gate model is a **mistake/staleness defense, not a request-authenticator**. `confirm:true` is trivially settable by JS; `expected_bytes` and any server-stamped key are readable; a client-generated key is attacker-mintable. Gates stand **only with a browser-borne defense layer on top**.

### Recommendation (decided)

**v1 line = 6 gates + Origin validation on the destructive namespace + `MAINTENANCE_ENDPOINTS_ENABLED=1` kill-switch (default ON). No auth. Global posture (bind/CORS/auth) stays in a deferred cross-cutting ticket.**

Origin rule (`require_trusted_origin` FastAPI dependency, first check on the four non-availability endpoints):
1. **No `Origin` header → allow** (curl, systemd, programmatic operators).
2. **`Origin` matches the daemon's own external origin (same-origin) → allow.** *(Architect correction to the worker's deny-all default: browsers attach `Origin` even on same-origin POSTs, so deny-all would 403 the daemon-served SPA's own execute — the shipped product's primary flow. Zero-config production must work.)*
3. **`Origin` host ∈ localhost-family (`localhost`, `127.0.0.1`, `[::1]`, any port, http/https) → allow.** *(Zero-config dev: FE dev server on :4199 proxies to :8079; the daemon sees `Origin: http://localhost:4199`. Residual: a local malicious http page passes — accepted, a local process is already machine-level compromise.)*
4. **`Origin ∈ MAINTENANCE_TRUSTED_ORIGINS` (CSV env, default empty) → allow** (explicit opt-in for LAN-browser origins, e.g. an operator browsing the UI from another machine).
5. Anything else (incl. `Origin: null` from sandboxed iframes / `file://`) → **403 `origin_not_trusted`**.

`/availability` is exempt from the guard (FE gear probe must see the disabled state cleanly; it is non-destructive).

### Trade-off matrix (contested hardening lines, 5 axes)

| Line | Complexity | Scalability | Maintainability | Risk (reduction) | Cost |
|---|---|---|---|---|---|
| **(a) gates + kill-switch only** (plan as drafted) | Low | High | High | **Insufficient** — browser choreography unblocked | Zero |
| **(b)+(a) + Origin validation** ← RECOMMENDED | Low-Med (one dependency + one env) | High | High (single guard fn, source-grep pinnable) | Blocks the realistic browser class; curl class unchanged (=baseline) | ~Zero operator friction (auto-trust same-origin + localhost) |
| (c) kill-switch default OFF | Low | High | High | Medium | **High** — defeats Section 1's "no env acrobatics" objective |
| (d) static token env | Medium | Medium | Medium (token lifecycle, SPA wiring) | Medium (blocks browsers; LAN direct if leaked) | Medium |
| (e) global posture now (bind 127.0.0.1 + auth) | Very High | Low | Low | Very High | Very High — out of scope, separate ticket |

### Kill-switch (A-4)

- **Name:** `MAINTENANCE_ENDPOINTS_ENABLED` (matches `PLANE_SYNC_ENABLED`/`PLANE_MCP_ENABLED` vocabulary). **Default `1`.**
- **Reach:** the whole `/api/maintenance/*` API namespace: `/status`, `/dry-run`, `/execute`, `/runs/{id}` → 503 `maintenance_disabled`; `/availability` → **200** with `eligible:false, state:"kill_switched"` (FE hides the menu entry cleanly). One switch — do **not** ship the §6.3 `MAINTENANCE_SERVICE_DISABLED` variant (YAGNI; see AM-14 for the state mapping).
- **INV-1 preserved:** the kill-switch gates the *API surface only*; the auto-cycle's env dual-arm is untouched. Auto-cycle stays destructive-if-armed while the manual API refuses — correct by design, document it in the runbook.
- Boot logs one INFO line when OFF (PlaneSyncWatchdog no-key precedent).
- Weakest gate confirmed: `expected_bytes` echo (only meaningful once Origin validation makes responses the operator's own). `run_id` suffix widened to hex8 as defense-in-depth (AM-8) — with readable responses brute-force is moot for execute, but `/runs/{id}` enumeration gets cheaper to harden now.

### What NOT to build in v1

Origin globbing beyond the exact rules above · token auth (v2/deferred ticket) · per-session CSRF token store · rate limiting (deferred ticket) · per-section kill-switches · bind/CORS/auth changes.

### Deferred global-posture ticket (file separately; contents ratified)

(a) `DAEMON_HOST` default → `127.0.0.1` with LAN-bind boot warning (one-version deprecation note); (b) CORS `*` → env-driven origin list (default FE origins); (c) optional `ENSEMBLE_API_BEARER_TOKEN` for `/api/*` minus health endpoints; (d) middleware token-bucket rate limit (closes residual brute-force classes); (e) failed-auth audit log. **Do NOT add `Access-Control-Allow-Private-Network: true`** — PNA is currently the strongest accidental defense.

---

## Focus Area 2 — A-3 contract amendment + re-freeze (INV-5)

### Amended dry-run contract (ratified into the frozen contract)

`POST /dry-run` 200 gains top-level `skipped[]`; `/status last_run.summary.blobs.skipped` and `GET /runs/{id}.summary` inherit the entry shape:

```json
{
  "run_id": "ckpt-20260927_032000123456-2a18f3c9",
  "would_delete": { "checkpoint_rows": 0, "writes": 0, "blobs": 4, "bytes": 268435456 },
  "scanned": { "thread_ns_pairs": 12 },
  "skipped": [
    { "thread_id": "3f2a9c1e-…", "checkpoint_ns": "", "reason": "ZERO_REFS_FAIL_SAFE" },
    { "thread_id": "77b0d4aa-…", "checkpoint_ns": "snap:x", "reason": "MAX_REFS_EXCEEDED" }
  ],
  "duration_ms": 412,
  "fresh_until": "2026-09-27T03:25:00.123456+00:00"
}
```

- `reason` is machine-code-stable: closed set `{ZERO_REFS_FAIL_SAFE, MAX_REFS_EXCEEDED}` (stable literals, `checkpoint_prune.py:185-200`) ∪ open family `ERROR:<ExceptionName>` (`:250-256`). Documented as an extensible enum; FE renders known codes with badges, `ERROR:*` generically.
- **`skipped[]` is informational, not part of the confirm echo.** Scope pin stays `expected_bytes` equality ONLY (AM-3): skip pairs contribute 0 bytes to both runs; divergence is deletion-conservative (skipped pairs are never deleted); pair-count/skip-hash pins were rejected — they false-refuse in a live daemon and defeat the freshness window.

### 🔴 AM-2 — Op E before Op D in the manual path (correctness, blocking)

Dry-run's anti-join counts blobs referenced by *any remaining checkpoint row* (`checkpoint_prune.py:12-17`). Manual execute in auto-cycle order (D→E, `maintenance.py:437-480`) first deletes excess rows, which unreferences blobs the dry-run counted as referenced → execute's blob bytes exceed the promise → `byte_count_mismatch` on every attempt while excess rows exist, and re-dry-run reproduces the undercount. **Manual execute composes Op E → Op D** inside `MaintenanceApiService`; the auto-cycle order is UNCHANGED (INV-1). Residual cost: blobs referenced only by excess rows survive one extra cycle (conservative under-delete, self-healing). Required test: execute with excess rows present must NOT mismatch (deadlocks under the old order).

### Re-freeze checklist (per INV-5)

1. `plan-overview.md` §API Contract: §3 dry-run + `skipped[]`; §2 `/status` summary example (blobs.skipped + destructive-flavor example); §4 execute checks (Op-E-first note; `advisory` field on 202; fix check #6 typo "200"→"202"); error-code table (+`maintenance_disabled` 503, +`origin_not_trusted` 403); `Z`→`+00:00` example sweep (A-7 — `to_utc_iso` emits `+00:00`, `timestamps.py:63-104`; JS `Date` parses both); run_id format examples.
2. Addendum register: mark A-1 **DROPPED**, A-2/A-3/A-5/A-6/A-8/A-11 **RATIFIED**, A-4 **RATIFIED (renamed semantics below)**, A-7/A-9/A-10/A-12 **CONFIRMED**; §6.5 CLOSED (overridden by A-6).
3. `phase1-backend.md` schema re-sync + 4 new BE cases: (a) dry-run renders seeded ZERO_REFS pair in `skipped[]`; (b) execute succeeds when a new skip pair appeared in-window (bytes unaffected); (c) excess-rows execute does NOT mismatch (AM-2 proof); (d) `last_run` excludes `manual_dry_run` (AM-9).
4. `phase2-frontend.md` schema re-sync + FE additions: skipped list render ("N pairs skipped — fail-safe"), reason badge map + generic `ERROR:*` fallback, 409-adoption behavior (below), `+00:00` parsing note.
5. Architect re-stamps the freeze (date + approver) before Phase 2 merges.

**A-1 `idempotency_key`: DROPPED** (architect ruling, resolving the security-vs-lifecycle split): the 409 `run_in_flight` body already carries the in-flight `run_id` + `started_at`; FE contract note = *on 409 from execute, adopt `details.run_id` and resume polling* — covers double-click and network-retry classes with zero new contract surface. The Origin gate (not a key) is the browser defense. Security framing removed.

**A-11: RATIFIED** — 202 body gains `"advisory": "system_busy" | null` + `"expected_duration_ms_hint"` (ceil-seconds of the referenced dry-run's `duration_ms`) — see Focus Area 3.

---

## Focus Area 3 — Async run lifecycle (202 + poll)

### Run state machine (final)

```
(none) ──acquire lock + conditional INSERT──► running ──finally──► succeeded (summary_json)
                                            │        └─finally──► failed    (error_json; infra faults only —
                                            │                       pair failures live in summary.skipped)
                                            ▼ daemon restart mid-run
                                         interrupted (error.code="run_interrupted", completed_at=sweep time)

Contention (any kind in flight): NO row written → 409 run_in_flight {run_id, started_at}
GET /runs/{id}: running → {status, completed_at:null, summary:null}; terminal → full body; unknown → 404
```

- **States: `running | succeeded | failed | interrupted`.** `overlap_refused` is **deleted** from the schema and the audit story (architect ruling over the contract worker's rows-for-refusals detail: 404-noise churn from double-clicks outweighs attempt-audit value; one INFO log line carries requester forensics instead).
- **Cancellation: NO abort exists in the prune loop today** (single per-pair `for` with unconditional `continue`s, `checkpoint_prune.py:152-258`) — stated honestly: **no mid-pass abort in v1, no cancel endpoint.** The per-pair loop top is the pre-built v2 insertion point (cooperative `asyncio.Event`, ~10 lines). Recourse for a wedged run = restart daemon → boot sweep.
- **Daemon restart:** unconditional boot sweep at lifespan start — rowcount-guarded CAS `running → interrupted` (PlaneSync `fail_stale_syncing` pattern, `plane_sync_watchdog_service.py:299-316`), no age gate (a boot-time `running` row is an orphan by definition under the single-daemon assumption; an age gate would orphan young rows), one summary log line. Resume is rejected: prune is retention-idempotent (re-run converges; per-pair independence means a partial pass finishes next run). **Boot-sweep-only — no live stale-running watchdog in v1** (unlike Plane, no live foreign process can own the row).
- **Timeout: none server-side, honestly.** (Verified: the migration worker has none either — only a 15s SSE keepalive.) Poll cadence **2000 ms confirmed** (A-10; one indexed single-row SELECT). 202 carries `expected_duration_ms_hint`. Hang mid-run → no watchdog; operator recourse = restart → sweep. A hung-pass-without-restart wedges only the maintenance surface (lock held), not the daemon.
- **Lock scope: in-process `asyncio.Lock` + DB conditional claim as the real gate.** Sequence per run (auto or manual): `acquire asyncio.Lock → conditional INSERT (unique claim, below) → run → finalize in finally`. This **supersedes research-findings §5.2's "live status row written BEFORE acquire"** — that ordering has a crash window producing a phantom `running` row that 409s the whole feature until manual DB surgery. The DB claim: **partial unique index `ON (section) WHERE status='running'`; INSERT conflict → 409** — kills the two-dev-daemons-on-shared-PG class (not corruption — per-pair SERIALIZABLE+retry + RETURNING accounting + idempotent deletes keep two pruners *correct* — but double-scan churn and divergent audit totals). `pg_advisory_xact_lock` rejected: pins a pool connection for minutes against the shared 15-conn pool. Both PG and SQLite support partial indexes; declare with dialect where-clauses (`postgresql_where`/`sqlite_where`) so `create_all` builds it on both drivers (verify render in Phase 1; fallback = plain conditional `INSERT … WHERE NOT EXISTS` with the racy-belt caveat documented).
- **Dry-run takes the same single-flight gate** (it is a `kind=manual_dry_run` run row; its scan needs a stable blob set for `expected_bytes` to mean anything; the frozen contract's dry-run 409 already implies it). No deadlock: non-blocking acquire, no nesting, dry-run is terminal before execute references it.
- **Dry-run realism:** the contract's 412 ms example is aspirational — on 33 GB the dry-run performs the same per-pair anti-join scans minus DELETEs; expect minutes. FE copy must not promise seconds; the 5-min freshness window may legitimately expire on slow disks (re-run dry-run — acceptable). `MAINTENANCE_DRY_RUN_FRESH_SECONDS=300` env ratified (OQ §6.4 answer: keep 5 min).

---

## Focus Area 4 — Extensibility architecture

### Decisions

1. **Section discovery: FE-local registry + per-section availability probe — RATIFIED for v1.** Production is monolithic (daemon serves the FE dist via the SPA catch-all, `api.py:2816-2828` → FE version = BE version), which kills the version-skew argument for BE-driven discovery. Dev-skew (:4199 proxy) is handled by section components tolerating `state != ready`. **Migrate to BE-driven (`GET /api/maintenance/sections`) only when ANY of:** third-party/plugin sections · per-deployment section config · >5 sections with BE-controlled labels/icons/ordering · per-user visibility. None hold for v1 — pre-build nothing.
2. **Availability contract (closes OQ §6.3):** response gains a stable `state` enum — `ready | backend_unsupported | subsystem_disabled | kill_switched`; `eligible` is derived (`state === 'ready'`); `reason` stays a documented diagnostic string (NOT for FE branching). Gear menu hides on every non-`ready` state. (`subsystem_disabled` = service not wired; the transient startup race keeps the frozen 503 `not_initialized`.)
3. **API namespace:** `availability` is the only REQUIRED per-section endpoint; `status/dry-run/execute/runs/{id}` are section-specific MAY (future sections may ship only availability+status). Router: **single `daemon/routers/maintenance.py`** with one APIRouter sub-router per section (`/checkpoint-cleanup`, future `/db-vacuum`, …), mounted via one `include_router` at the api.py seam (BEFORE the SPA catch-all, mirroring the plane_router comment). Split per-section files above 5 sections. Runs stay per-section (`/api/maintenance/{section}/runs/{id}`); cross-section listing DEFERRED (no v1 consumer; `maintenance_runs.section` is the discriminator).
4. **FE shape:** local `MaintenanceSection[] {id,label,component}` + `ngComponentOutlet` RATIFIED; **A-9 colocated service** under `pages/maintenance/checkpoint-cleanup/` RATIFIED (multi-section surface must not share a single-section service file); **A-10 2000 ms** pinned as a `readonly` constant with a source-grep pin; single `Maintenance` gear entry → landing page (no per-section gear entries).
5. **Standardization boundary** (write into the plan as a subsection; this is the actual extensibility architecture):

| Surface | MUST (uniform) | MAY (section-specific) |
|---|---|---|
| Availability response | `{eligible, state, backend, reason}` + state enum vocabulary | — |
| Error body | `{error, message, details?}` plane.py shape; stable code table | new section codes appended to enum |
| Run lifecycle | status vocabulary `running\|succeeded\|failed\|interrupted`; 202+`run_id`+poll; boot-sweep semantics | summary JSON shape |
| Concurrency | single-flight gate incl. DB claim; kill-switch `MAINTENANCE_ENDPOINTS_ENABLED` | — |
| Persistence | `maintenance_runs` schema; `section` discriminator | summary/error payloads |
| Everything else | — | dry-run/execute payloads, config block, FE rendering |

Route hardening (from the extensibility risk list): gate the lazy route on `state === 'ready'` (resolver or probe) so a stale FE dist + missing BE router doesn't 404 into the SPA fallback.

---

## Focus Area 5 — `maintenance_runs` persistence (conventions-validated)

### Final schema (replaces plan/research §4.1 sketch)

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

### Conformance rulings (evidence-cited)

- **PK:** `run_id TEXT PRIMARY KEY` (repo pattern: UUID/text PKs, `job_queue/models.py:513-522`, `project/models.py:192-196`; BIGSERIAL survives only as legacy). No FK children exist.
- **Timestamps: TEXT ISO via `now_utc_iso()`** — repo models contain **zero `sa.DateTime` columns** (E16); this sidesteps the naive/tz trap entirely and honors the convention's letter ("TEXT timestamps use `now_utc_iso()`"). INV-7's `now_utc_naive()` clause binds only if true TIMESTAMP columns are ever chosen — then `DateTime(timezone=False)` + naive binds, never tz-aware.
- **JSON columns: `JSONBType`** (`repositories/infra/types.py:32-60`) — raw PG `JSONB` breaks SQLite `create_all`; JSONBType is the sanctioned portable decorator.
- **Migration mechanics (A-12 CONFIRMED):** raw `.sql` = canonical doc with `-- MANUAL: TRUE` (runner skips, `runner.py:113,737`); the table is built by `SQLModel.metadata.create_all` on BOTH drivers at boot (`manager.py:547,531-539`); **no `_ensure_postgres_columns` entries** (that path is for existing-table evolution — new tables need none, snapshot precedent verbatim). **Cannot regress fresh-SQLite boot** (the 20260714 trap class is structurally unreachable here).
- **run_id format:** `ckpt-YYYYMMDD_HHMMSSffffff-hex8` — colon-free (URL-clean), lexicographically sortable, aligned with `migration_id` precedent (`migration.py:112-114`); hex8 (not hex4) per Focus Area 1.
- **Indexes:** composite `(section, completed_at)` for last_run; partial unique claim index per AM-5; NO partial lookup index on `running` (O(10s) rows/day; revisit on measured /status p99).

### Audit semantics (OQ §6.7: one channel — `maintenance_runs` IS the audit trail)

Minimum destructive-run record: **decision inputs** (`dry_run_run_id`, `expected_bytes`, full `dry_run_summary_json` incl. `skipped[]` — survives any future dry-run-row pruning, `confirm`, `advisory`, `env_flags_json` — proves INV-2: the override kwarg, not env, armed the DELETE, `requester_json` {peer_ip, user_agent, origin} — forensics, never attribution; no auth exists) + **pre-state** (inside the dry-run snapshot) + **outcome** (`summary_json`) + **failure** (`error_json`). Growth: Out-of-Scope #7 deferral CONFIRMED (~1-3 KB/run; ≪100 MB/yr) with one cheap guard now — `skipped[]` capped at 1000 entries + `skipped_truncated:true`. Revisit at >100k rows or >100 MB.

---

## Focus Area 6 — Env/flag interplay (final matrix)

| Env DRY_RUN / DESTRUCTIVE | POST /dry-run | POST /execute | Auto-cycle arm | Ruling |
|---|---|---|---|---|
| default (1 / 0) | 200 | 202 destructive=True (INV-2) | dry | default posture |
| 0 / 1 (dual-arm) | 200 | 202 | **destructive** | advanced posture |
| 0 / 0-or-unset | 200 | 202 | dry | DRY_RUN=0 alone arms nothing |
| 1 / 1 | 200 | 202 | dry | ⚠ pre-existing surprise cell: DESTRUCTIVE=1 inert unless DRY_RUN=0 — document, no amendment |
| kill-switch OFF (any env) | 503 `maintenance_disabled` | 503 | destructive if armed | **Correct by design (INV-1)** — API-only reach; runbook documents |
| run in flight (any kind) | 409 `run_in_flight` | 409 | skip + DEBUG + last_run update | both manual classes + auto serialize on ONE global lane |

Arbitration rulings: **first-acquirer wins** (no manual priority — with dual-arm armed, the auto pass IS the identical destructive work); auto-tick losing the lock = non-raising skip + `last_run` update + DEBUG with in-flight `run_id` (the strictly sequential `_loop`, `maintenance.py:209-220`, guarantees no stacking and no loop death); the manual-vs-auto race window is closed by the single global lane (OQ §6.6 = single in-flight globally, CONFIRMED). The only previously-ambiguous cell — dry-run-while-run-in-flight — is resolved by AM-4 (dry-run takes the gate; 409 names the in-flight run).

---

## CONSOLIDATED PLAN AMENDMENTS (authoritative; apply + re-freeze before Phase 1)

| # | Plan section | Amendment |
|---|---|---|
| **AM-1** | §API Contract (new guard note) + §6.2 + Out-of-Scope #1 | `require_trusted_origin` dependency on `/status`,`/dry-run`,`/execute`,`/runs/{id}`: allow no-Origin / same-origin / localhost-family; `MAINTENANCE_TRUSTED_ORIGINS` CSV extras; else 403 `origin_not_trusted` (add to error table). `/availability` exempt. INV-10: guard is first check; sole browser-borne defense. |
| **AM-2** | §API Contract #4 + Phase 1 task 5 | **Manual execute = Op E → Op D** (auto-cycle order unchanged). Blocking correctness fix. |
| **AM-3** | §API Contract #4 check 4 | `expected_bytes` equality remains the ONLY scope pin; skipped[] informational; pair-count/skip-hash pins rejected. |
| **AM-4** | §API Contract #3/#4 + research §5.2 | Single-flight sequence = acquire lock → conditional INSERT → run → finalize-in-finally; dry-run takes the same gate; supersedes "row before acquire". |
| **AM-5** | Phase 1 task 4 | Partial unique index `(section) WHERE status='running'` = DB claim; conflict → 409, no row. |
| **AM-6** | research §4.1 + contract §5 runs/{id} | Status enum `running\|succeeded\|failed\|interrupted`; delete `overlap_refused`; no refusal rows (INFO log w/ requester forensics). |
| **AM-7** | Phase 1 task 5 (new) | Boot sweep: unconditional CAS `running→interrupted` at lifespan start + one log line. No live watchdog. |
| **AM-8** | §API Contract examples | run_id = `ckpt-YYYYMMDD_HHMMSSffffff-hex8`. |
| **AM-9** | §API Contract #2 + Register A-6 (overrides §6.5) | `last_run` = latest `succeeded|failed` of kind ∈ {auto, manual_execute}; `manual_dry_run` never surfaces; `in_flight` = any `running` row. |
| **AM-10** | §API Contract #3/#4 + A-3 | Dry-run/`/status`/`/runs` gain `skipped[{thread_id,checkpoint_ns,reason}]`; reason enum documented extensible. |
| **AM-11** | §API Contract #4 + A-2/A-5/A-8 | `BlobPruneSummary` gains `would_delete_count/would_free_bytes` (dry arm accumulates — currently discarded, `checkpoint_prune.py:203-217`); dual-flavor keys with FE branching on `destructive`; structured error dict for all 5 endpoints. |
| **AM-12** | §API Contract #4 + A-11 | 202 body: `advisory: "system_busy"\|null` + `expected_duration_ms_hint`; fix check #6 "200"→"202". |
| **AM-13** | §API Contract #1 + A-4 + §6.2/§6.3 | `MAINTENANCE_ENDPOINTS_ENABLED` default 1; four endpoints 503 `maintenance_disabled`; availability 200 + `state:"kill_switched"`; `state` enum added to availability; drop `MAINTENANCE_SERVICE_DISABLED` idea. |
| **AM-14** | §FE Structure + Phase 2 tasks | FE: 409-adoption (resume polling `details.run_id`), skipped[] render + badge map, state-enum gating of route + menu, `+00:00` sweep, POLL_INTERVAL_MS=2000 source-grep pin, colocated service (A-9). |
| **AM-15** | Phase 1 task 4 + §6.7 | Schema per Focus Area 5 (TEXT PK, TEXT ISO timestamps, JSONBType, both indexes); audit field list; skipped-cap 1000 + truncation flag. |
| **AM-16** | §Test Strategy (Ph1 t8, Ph2 t6/t7) | New tests: Origin guard matrix (4 cases), kill-switch behavior, excess-rows execute no-mismatch (AM-2), in-window new-skip-pair execute success, last_run exclusion, boot sweep, dual-arm auto-vs-manual 409, FE skipped render + Playwright cross-origin 403 + kill-switch hide. |
| **AM-17** | Register A-1 | **DROPPED** — idempotency_key removed; 409-adoption replaces it. |
| **AM-18** | §Out-of-Scope + new §Standardization Boundary | Add boundary table (Focus Area 4); defer BE-driven discovery w/ explicit thresholds; global-posture ticket scoped (Focus Area 1). |

OQ dispositions: §6.1=A8 structured dict ✓ · §6.2=AM-1/AM-13 ✓ · §6.3=AM-13 state enum ✓ · §6.4=5 min + env, keep ✓ · §6.5=AM-9 (A-6 wins) ✓ · §6.6=single global lane ✓ · §6.7=one channel, AM-15 ✓ · §6.8=A-1 dropped, AM-17 ✓.

---

## Risks (severity-ordered, post-amendment)

- 🔴 AM-2 unapplied → feature deadlocks on its primary flow (excess rows present). **Blocking.**
- 🔴 AM-1 unapplied → http-origin browser page can execute the destructive op unauthenticated (readable CORS responses). **Blocking.**
- 🟡 Bytes-pin blind spot: in-window skip-set drift invisible to byte equality — accepted (deletion-conservative; per-pair fail-safes are the true boundary).
- 🟡 Kill-switch is boot-read env — flipping requires restart (bounded by Phase 3 activation anyway).
- 🟡 Dev dual-daemon on shared PG: closed by AM-5 claim; verify partial-index render on SQLite in Phase 1 (fallback documented).
- 🟡 Hung pass without restart wedges the maintenance surface only; no watchdog in v1 (honest).
- 🟢 Dry-run minutes-scale on 33 GB (412 ms example aspirational) — FE copy + freshness-window interplay.
- 🟢 `maintenance_runs` growth deferred with quantified trigger + skipped-cap guard.

## Confidence

**High** on security mechanics (verified against installed Starlette source after one worker correction), **High** on AM-2 accounting (code-verified anti-join semantics), **Medium-High** on the SQLite partial-index claim (SQLAlchemy dual-dialect `where` supported; render unverified in this repo). Flip assumption for the overall verdict: if manual execute ever absorbs auto-cycle ops A–C, re-derive AM-2's ordering proof.

## Decisions Pending (leader/operator)

1. Default `MAINTENANCE_TRUSTED_ORIGINS` = empty with localhost-family auto-trust — accepted? (Alternative: strict same-origin only; breaks nothing in prod but requires dev env var.)
2. File the deferred global-posture ticket now or defer to backlog grooming.
3. v2 cancel endpoint + SSE — endorse deferral.

## Gaps

None — all four dispatched workers reported with skill-confirmation first lines and code-level evidence; the one challenged claim (security response-readability) was re-verified and corrected.
