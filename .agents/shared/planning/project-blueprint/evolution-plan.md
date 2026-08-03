# Project Blueprint Evolution — Master Execution Plan (v2, Phase 8 dissolved)

**Status:** Final (synthesized — v2 resequencing)
**Date:** 2026-08-03
**Author:** planner[v2] — synthesis of three plan-creation/technical-analysis workers, revised per C1–C10 reviewer issues and 5 leader decisions
**Scope:** Phased execution plan for evolving the Project Blueprint subsystem
**Parent documents:**
- `blueprinter-evolution.md` — locked architecture decisions
- `plan-overview.md` — final architecture (14 locked decisions)

---

## Specialist Files (authored by workers — read for implementation detail)

| File | Scope | Lines |
|---|---|---|
| [`evolution-phase1-fixes.md`](evolution-phase1-fixes.md) | Phase 1: Critical wiring fixes (G1–G4) + canonical write boundary (NEW) | 790 |
| [`evolution-phases-detailed.md`](evolution-phases-detailed.md) | Phases 2–7: Design gaps + evolution architecture | 1,418 |
| [`evolution-questions-and-risks.md`](evolution-questions-and-risks.md) | 7 open questions resolved + 56-entry risk analysis (revised per leader decisions) | 320+ |

> **This document is the master synthesis (v2).** It provides the unified phase roadmap, gap-numbering traceability, dependency graph, cross-document reconciliations, risk priorities, and executive summary. Per-phase implementation detail lives in the specialist files linked above.

---

## 0. Resequencing Note (C1 — Critical Revision)

The original v1 plan reserved Phase 8 for "cross-cutting hardening" — durable leases, structured worker reports, exact-pending acknowledgement, canonical write path, and decision-model eval were all queued for a single late phase.

**C1 finding (dissolve Phase 8):** Bundling safety controls at the end is unsafe because:
1. Each control is meaningful only alongside the feature it protects — a durable build lease shipped without the API endpoints that use it cannot be exercised.
2. A monolithic Phase 8 creates a "do-everything-before-auto-rebuild" cliff; partial adoption of auto-rebuild with weakened safety is too tempting.
3. Reviewers found that risks like **D2** (clear-all pending), **O1** (in-memory guard), **O3** (rate-limiter bypass), and **I4** (router bypass) are mitigated more cheaply if their fix lives with the code that previously violated the invariant.

**C1 decision:** Phase 8 is **dissolved**. Each safety control is absorbed into the phase where the feature it protects ships. The result:

- Exact pending claims (processed_at soft-delete) → **Phase 2**
- Durable lease + admission coordinator → **Phase 3**
- Canonical write boundary (`BlueprintWriteService`) → **Phase 1**
- Compare/stage/publish rebuild semantics → **Phase 5a**
- Structured worker reports → **Phase 5a**
- Decision-model eval → **Phase 5a**
- PostgreSQL + SQLite integration tests → **Phase 7**
- A new **Phase 8 (OPTIONAL)** is reserved purely for telemetry/dashboards — it can be **dropped entirely** if no observability work is needed.

**Key message: each phase ships WITH its safety controls, not after.**

---

## 1. Executive Summary

The Project Blueprint subsystem is architecturally complete but has **4 critical wiring gaps** that block basic functionality, **5 design gaps** that affect correctness and safety, and a major **evolution proposal** that transforms the blueprinter into a skill-driven, two-workflow system.

This plan (v2) sequences the work into **8 phases**, with safety controls absorbed into the phases that need them (per C1):

1. **Phase 1 (P0):** Fix 4 critical wiring gaps + introduce the canonical write boundary (`BlueprintWriteService`) so revisions, trigger replacement, and rate limiting always run together.
2. **Phase 2:** Backend data layer — pending-experience queue table with **processed_at soft-delete** + claim/acknowledge state machine, G6/G7/G8 data-layer fixes, context-kind allowlist fix.
3. **Phase 3:** Backend services + **admission coordinator** — daily scan, **durable DB-backed lease**, unified trigger coordinator (so `/rebuild`, `/update`, daily scan, post-threshold trigger all share one admission path), experience/history hooks wired through the factory, `auto_rebuild_enabled` feature flag.
4. **Phase 4:** API changes — `/rebuild` + `/update` endpoints all go through the trigger coordinator (no direct enqueue).
5. **Phase 5a:** Blueprinter artifacts — compare/stage/publish semantics, versioned structured worker report envelope, `llm_model` upgrade (quick→balanced), skill files. **Phase 5b:** Integration testing with coordinator.
6. **Phase 6:** Frontend — dual-mode button, popup, **job-status polling**, error code preservation.
7. **Phase 7:** Smart daily scan logic + comprehensive E2E (**PG + SQLite**), **crash-during-rebuild recovery** test scenario, queue concurrency verification, embedding fingerprint migration.
8. **Phase 8 (OPTIONAL):** Telemetry dashboards only — can be dropped.

**Critical path:** Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 7
**Estimated effort:** ~15–21 days (critical path ~10–14 days with parallelization; see Appendix B for breakdown)
**Feature flag:** `auto_rebuild_enabled=False` gates all automated triggers (daily scan, post-threshold coalesced jobs, high-water triggers) until explicitly enabled in production.

---

## 2. Phase Overview Table (v2)

| Phase | Name | Gaps Fixed | Safety Controls Absorbed (from old Phase 8) | Effort | Status |
|---|---|---|---|---|---|
| **1** | Critical Fixes + Canonical Write | G1, G2, G3, G4 | `BlueprintWriteService` (canonical write boundary); write-budget management; revision capture in **same transaction** as row write (not post-commit) | 3–4 days | Ready |
| **2** | Data Layer | G6, G7, G8 | Pending-batch state machine (claim/acknowledge); `processed_at` soft-delete (exact-pending-claim, no clear-all); one-core DB constraint + G7 auto-dedup; context-kind allowlist fix | 2–3 days | Ready |
| **3** | Services + Admission Coord | G5, G9 | Durable DB-backed build lease; unified **admission coordinator** (single path for `/rebuild`, `/update`, daily scan, high-water trigger); experience/history hooks through factory; `auto_rebuild_enabled` feature flag | 3–4 days | Ready |
| **4** | API Changes | — | `/rebuild` + `/update` + `/initialize` alias all routed through the admission coordinator | 1 day | Ready |
| **5a** | Blueprinter Artifacts | — | **Compare/stage/publish** spec; **structured versioned worker-report envelope**; `llm_model` upgrade (`quick`→`balanced`); skill files + prompt rewrite | 2–3 days | Ready |
| **5b** | Blueprinter Integration | — | Live end-to-end verification of artifacts with coordinator + `BlueprintWriteService` | 1–2 days | Ready |
| **6** | Frontend | G10, G11, G12 | Job-status polling (`job_id` → job-status endpoint); error code preservation (409/404) | 1–2 days | Ready |
| **7** | Smart Scan + E2E | — | **Crash-during-rebuild recovery**; full live injection tests; **PG + SQLite**; queue concurrency verification; embedding-fingerprint mismatch detection | 2–3 days | Ready |
| **8** | **(OPTIONAL) Telemetry** | — | Dashboards, metrics, alerts — **can be dropped** | 1–2 days | Droppable |

---

## 3. Dependency Graph (v2 — Phase 8 dissolved)

```
                      ┌──────────────────────────────────────────────┐
                      │        PHASE 1 (Critical Fixes + CW)         │
                      │  G4 → G1, G2, G3 (parallel where possible)  │
                      │  + BlueprintWriteService (canonical boundary)│
                      │  + write-budget + post-commit revision      │
                      └──────────┬───────────────────────────────────┘
                                 │
                                 ▼
                      ┌──────────────────────────────────────────────┐
                      │        PHASE 2 (Data Layer)                  │
                      │  Pending table + claim/ack state machine     │
                      │  + processed_at soft-delete                  │
                      │  + G6/G7/G8 + context-kind allowlist         │
                      └──────────┬─────────────┬─────────────────────┘
                                 │             │
                                 │             │ (parallel)
                                 ▼             ▼
         ┌───────────────────────────────┐  ┌──────────────────────────────────┐
         │    PHASE 3 (Services + AC)    │  │ PHASE 5a (Blueprinter Artifacts) │
         │  Admission coordinator        │  │   skill files + prompt rewrite  │
         │  + durable lease + auto_     │  │   + report envelope spec        │
         │    rebuild_enabled           │  │   + compare/stage/publish spec  │
         │  + hooks through factory     │  │   (Phase 2 ONLY — no dispatch)  │
         └──────────┬───────────────────┘  └──────────┬───────────────────────┘
                    │                                 │
                    ▼                                 │ (5a parallel with Phase 3/4/6)
         ┌───────────────────────────────┐            │
         │     PHASE 4 (API)             │            │
         │  /rebuild + /update + alias   │            │
         │  → admission coordinator     │            │
         └──────────┬──────┬─────────────┘            │
                    │      │                          │
                    │      └─────────────┐            │
                    ▼                    ▼            ▼
         ┌─────────────────────────┐  ┌──────────────────────────────────┐
         │  PHASE 6 (Frontend)     │  │  PHASE 5b (Integration Testing)   │
         │  Job-status polling     │  │  end-to-end with coordinator      │
         │  + 409/404 preserved    │  │  (depends on Phase 3)             │
         └──────────┬──────────────┘  └──────────────┬──────────────────┘
                    │                                 │
                    └──────────────┬──────────────────┘
                                   ▼
                    ┌──────────────────────────────────┐
                    │   PHASE 7 (Smart Scan + E2E)     │
                    │  + crash-during-rebuild recovery │
                    │  + PG + SQLite + concurrency     │
                    │  + embedding fingerprint         │
                    └──────────────┬───────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────────┐
                    │   PHASE 8 (OPTIONAL Telemetry)   │
                    │   Droppable — no critical dep    │
                    └──────────────────────────────────┘
```

**Parallelization opportunities (v2):**
- **Phase 5a** (Blueprinter artifacts — skill files, prompt authoring, report envelope spec, compare/stage/publish spec) branches off **Phase 2** — runs in parallel with Phase 3/4/6. No dependency on the admission coordinator.
- **Phase 5b** (Integration testing — end-to-end with coordinator) depends on **Phase 3** (admission coordinator) and **Phase 5a** (artifacts must exist). Runs after both are complete.
- Phase 6 (Frontend) branches off Phase 4 — runs in parallel with Phase 5b.
- Phase 8 (Telemetry) is fully optional and independent of the critical path.

**Critical path (v2):** Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 7.

---

## 4. Phase Details (v2)

### Phase 1 — Critical Wiring Fixes + Canonical Write (P0)

> **Full detail:** [`evolution-phase1-fixes.md`](evolution-phase1-fixes.md) (790 lines) — extended to cover `BlueprintWriteService`.

**Objective:** Fix the 4 wiring gaps (G1–G4) that block basic functionality **AND** introduce the canonical write boundary so that every write — from router, tool, or blueprinter — captures a revision, replaces trigger embeddings, and checks the rate limiter in one place.

**Fix ordering:** G4 → G1 → G2 → G3 → **`BlueprintWriteService` refactor**.

| Gap | Root Cause | Key Change | Files |
|---|---|---|---|
| **G4** | Manager uses `skill_evolution` config for blueprint embedding service | Create blueprint-specific `SkillEmbeddingService` instance | `daemon/manager.py:929-938` |
| **G1** (BLOCKER) | `blueprint_create`/`blueprint_update` tools have no `trigger_queries` param | Add param + `_embed_and_store_triggers` helper | `daemon/tools/blueprint.py:246-351` |
| **G2** | `update()` increments version but never calls `add_revision()` | Insert revision capture in **same transaction** as row write (via `BlueprintWriteService`; avoids TOCTOU window between commit and revision insert) | `daemon/repositories/blueprint/repository.py:73-89` |
| **G3** | Rate limiter instantiated but `can_proceed`/`record_success`/`record_failure` never called | Add limiter gate + recording in both write tools (preliminary — moved to canonical service) | `daemon/tools/blueprint.py:246-351` |
| **C1-CW** | Router (`POST /blueprints`, `PUT /blueprints/{id}`) calls `BlueprintRepository.update()` directly, bypassing trigger embedding, revision capture, and rate limiting (verified risk I4) | Introduce `BlueprintWriteService` (NEW) — single entry point for ALL writes: create / update / disable / soft-delete. Wraps revision capture + trigger replacement + rate limit + manual-content guard. Router, tools, and blueprinter ALL route through it. | `daemon/services/blueprint_write_service.py` (NEW); `daemon/routers/blueprints.py:204-229, 353-400`; `daemon/tools/blueprint.py:240-353`; `daemon/repositories/blueprint/repository.py:73-89` |

**`BlueprintWriteService` contract:**
```python
class BlueprintWriteService:
    async def create(source: Literal["manual","auto"], *, payload: CreateBlueprint, actor: Actor) -> Blueprint: ...
    async def update(source: str, *, blueprint_id: str, payload: UpdateBlueprint, actor: Actor) -> Blueprint: ...
    async def soft_disable(source: str, *, blueprint_id: str, reason: str, lease_id: str, actor: Actor) -> None: ...
    async def claim(*, project_id: str, max_pending: int) -> list[PendingRecord]: ...  # NEW
    async def acknowledge(*, lease_id: str, record_ids: list[str]) -> int: ...          # NEW (sets processed_at)

    # Internal invariants enforced on EVERY write:
    #  1. Rate-limiter check (BlueprintRateLimiter.can_proceed)
    #  2. Optimistic version check (caller-provided If-Match or last_seen_version)
    #  3. Single transaction: row write + revision snapshot + trigger replacement
    #  4. Source-aware guards: source="manual" requires explicit actor confirmation for any disable/overwrite
    #  5. Failure: throw and release limiter on rollback
```

**Migration plan for the canonical service:**
1. Implement `BlueprintWriteService` skeleton + the five invariants above.
2. Refactor `blueprint_create` / `blueprint_update` tools to call the service.
3. Refactor `POST /blueprints`, `PUT /blueprints/{id}`, `DELETE /blueprints/{id}` (soft-delete) router endpoints to call the service.
4. Refactor Phase 3 admission coordinator (no direct repo writes).
5. Refactor Phase 5a blueprinter (staging writes + publish step).

**Cross-cutting concern:** G1 and G3 both modify `blueprint_create` and `blueprint_update`. Implement sequentially (G1 then G3) to avoid merge conflicts. G2 is in a different file and can proceed in parallel. **`BlueprintWriteService` refactor MUST come AFTER G1/G2/G3/G4 are merged** — it consolidates their preliminary fixes into one canonical path, then deletes the duplicate code paths.

**Testing:** 10 measurable exit criteria — vector scores > 0, revision rows created, rate limiter blocks at capacity, C8 invariant compliance (`except Exception` not `BaseException`).

**New exit criteria (canonical write service):**
- All five write paths (tool:create, tool:update, router:create, router:update, router:delete) call `BlueprintWriteService` (zero direct `BlueprintRepository` writes).
- Rate-limiter count and revision count match across all five paths.
- Manual-content guard refuses any disable/overwrite without `force=True` + `actor.elevated_role`.

**Dependencies:** None — Phase 1 is foundational.

---

### Phase 2 — Backend Data Layer (P1)

> **Full detail:** [`evolution-phases-detailed.md`](evolution-phases-detailed.md) §Phase 2

**Objective:** Establish the data foundation for evolution: pending-experience queue table with **claim/acknowledge state machine** (replaces the unsafe clear-all), one-core-per-project guard (G7) with auto-dedup, status filtering (G8), BM25 single-candidate fix (G6), and the **context-kind allowlist fix** so `blueprint` survives persistence.

| Task | Files |
|---|---|
| New `project_blueprint_pending_updates` table with `processed_at` column (soft-delete marker) | `daemon/repositories/blueprint/models.py` |
| Pending-update CRUD methods (`add_pending`, `list_pending`, `count_pending`, `claim_pending(batch_size)`, `acknowledge_pending(ids)`, `release_pending(claim_token)`) | `daemon/repositories/blueprint/repository.py` |
| G7: One-core guard in `create()` (app-level, cross-driver safe) **+ G7 auto-dedup** (digest dedup key on `(project_id, source_kind, content_hash)`) | `daemon/repositories/blueprint/repository.py:41-49` |
| G8: Add `status == 'published'` filter to `search_candidates()` | `daemon/repositories/blueprint/repository.py:189-222` |
| G6: Fix BM25 single-candidate normalization (`span == 0` edge case) | `daemon/services/blueprint_matcher.py:342-356` |
| Wire `_blueprint_pending_repo` in manager | `daemon/manager.py:758` |
| **Context-kind allowlist fix** (C10): add `blueprint` to `CONTEXT_KIND_*` allowlists + synthetic-system tests | `daemon/persistence.py:630-637`; `daemon/services/context_messages.py:66-107, 1347-1384` |
| **Embedding-model fingerprint column** on trigger table (`embedding_model_id: str`); detect mismatched vectors at read time | `daemon/repositories/blueprint/models.py` (NEW migration); `daemon/services/blueprint_matcher.py` |

**Pending-batch claim/acknowledge state machine (NEW, replaces clear-all):**
```
                  ┌──────────────┐
                  │   INSERT     │  (experience()/history_add side)
                  └──────┬───────┘
                         ▼
                  ┌──────────────┐
   ┌──────────────┤   pending    │  (immutable, has record_id)
   │              └──────┬───────┘
   │  (high-water         
   │   INSERT trigger)    │
   │                     ▼
   │              ┌──────────────┐
   │              │   claimed    │  (claim_token, claimed_at, lease_id)
   │              └──────┬───────┘
   │  (lease expiry       │
   │   sweeps here)       ▼
   │              ┌──────────────┐
   └─────────────►│  processed   │  (processed_at soft-delete marker)
                  └──────────────┘
   never hard-deleted
```

Invariants:
1. **Never clear-all.** Records persist forever, marked `processed_at` at acknowledge time.
2. **Atomic claim.** `claim_pending(batch_size, lease_token)` is one transaction that flips `pending → claimed` for the oldest N rows by `record_id`. Concurrent callers see disjoint claim sets.
3. **Bounded retry window.** Unclaimed rows have no TTL; lease expiry (default 30 min) returns them to `pending`. Crash during incremental run → on next scan, rows are re-claimable.
4. **Crash-safe.** A row can be in any state on read; a `claimed` row whose `lease_id` no longer exists is orphan-reclaimable.

**Embedding fingerprint (NEW, design consideration for C1):**
- Trigger table stores `embedding_model_id: str` (e.g., `bge-small-en-v1.5@2025-12-01`) per row.
- On matcher init and on every read, fetch the configured `BLUEPRINT_EMBEDDING_MODEL_ID`.
- Compare to stored fingerprint. **Mismatch → log a warning and regenerate the affected embeddings in the background** (not on the hot path).
- Migration: existing rows get `embedding_model_id=""` (unknown) → background sweep classifies them as "needs-recompute" on first read after config change.

**Key technical notes:**
- New pending table auto-created by `SQLModel.metadata.create_all()` — NO `_ensure_postgres_columns()` needed.
- New `embedding_model_id` column on existing trigger table **DOES** need `_ensure_postgres_columns()`.
- G7 guard: **DB-level partial unique index is the PRIMARY enforcement** (`CREATE UNIQUE INDEX ux_blueprint_one_core ON project_blueprints (project_id) WHERE kind = 'core' AND is_active = 1`). The app-level check in `create()` is a **UX convenience only** — its check-then-act race window is acceptable because the DB constraint catches any TOCTOU violation. Created via `_ensure_postgres_columns()` pattern (NOT `.sql` which NO-OPs on PostgreSQL).
- G6 fix: when `span == 0 && raw_score > 0`, set `bm25_norm = 1.0`.
- G7 auto-dedup: dedup key on `(project_id, source_kind, content_hash)` — if a record exists in last 24h with the same hash, do not insert. **Audit signal preserved** in a separate `pending_duplicates_audit` table.

**Dependencies:** None (foundation phase for evolution). Phase 1 should be complete so the matcher has working vector scoring and the canonical write service is the only write path.

---

### Phase 3 — Services + Admission Coordinator (P1)

> **Full detail:** [`evolution-phases-detailed.md`](evolution-phases-detailed.md) §Phase 3 + admission coordinator design.

**Objective:** Wire the pending-experience queue into the live system, implement daemon-side daily scan (G5), introduce the **durable DB-backed build lease** (replaces in-memory guard, absorbs old Phase 8 control #1), and stand up a **unified admission coordinator** so every trigger source shares one path.

| Task | Files |
|---|---|)
| `experience()` → pending-queue INSERT (replaces disabled sidecar) | `daemon/tools/knowledge_tools.py:411-434` |
| `project_history_add()` → pending-queue INSERT for feature/milestone **with manager threaded through factory** (leader decision: hook manager via factory, not just store) | `daemon/tools/project_history.py`; `daemon/repositories/project/repository.py:1248-1288` (factory update) |
| `BlueprintAdmissionCoordinator` (NEW) — single entry point for all rebuild/update triggers | `daemon/services/blueprint_admission_coordinator.py` (NEW) |
| `BlueprintBuildLease` (NEW) — DB-backed lease table with `(project_id, mode, job_id, lease_token, heartbeat_at, expires_at, state)` | `daemon/services/blueprint_build_lease.py` (NEW); `daemon/repositories/blueprint/models.py` (NEW `BlueprintBuildLease` SQLModel) |
| `BlueprintScanService` registered with `MaintenanceService` | `daemon/services/blueprint_scan_service.py` (NEW), `daemon/manager.py:1765` |
| `auto_rebuild_enabled` feature flag (default **False**) — gates all automated triggers | `daemon/manager.py` (NEW settings section); `daemon/config.py` |

**Admission Coordinator contract (NEW, C7):**
```python
class BlueprintAdmissionCoordinator:
    """Single point of admission for ALL blueprinter triggers:
       - /rebuild (manual)
       - /update (manual)
       - daily scan (daemon)
       - high-water threshold (pending-side)
       - bootstrap
    """

    async def request_rebuild(self, *, project_id: str, trigger_source: Literal["api","daily","high_water","bootstrap"], actor: Actor | None = None) -> AdmissionResult: ...
    async def request_update(self, *, project_id: str, trigger_source: str, actor: Actor | None = None) -> AdmissionResult: ...

    # Internal:
    #   1. Auto-rebuild flag check (only for non-manual sources)
    #   2. Build-lease claim (atomic, token-based)
    #   3. Queue enqueue (only on successful claim; release on enqueue failure)
    #   4. Returns 202 (queued) | 409 (conflict) | 503 (lease/store unavailable)
```

Sources of triggers ALL route through `request_rebuild` / `request_update`:
- Daily scan → `request_rebuild(project_id, trigger_source="daily")`
- High-water pending event → `request_update(project_id, trigger_source="high_water")`
- `/rebuild` API → `request_rebuild(project_id, trigger_source="api")`
- `/update` API → `request_update(project_id, trigger_source="api")`

**Durable build lease (NEW):**
- Storage: dedicated `BlueprintBuildLease` SQLModel table with a unique constraint on `(project_id, mode, state='active')` (PostgreSQL partial unique index; SQLite app-level enforcement).
- Atomic claim: `INSERT ... ON CONFLICT DO NOTHING RETURNING lease_token` (PostgreSQL) or `create_or_get_by_idempotency_key` (SQLite fallback path).
- Heartbeat: lease owner updates `heartbeat_at` every 60s; sweeper expires leases where `expires_at < now()` AND `heartbeat_at < now() - lease_ttl`.
- Release: only on token-matched path (job success/failure/cancel). Stale-sweep is the safety net.
- Crash recovery: on daemon startup, scan leases; orphan-sweep (no live JobItem referencing the lease) → release.

**Feature flag `auto_rebuild_enabled`** (NEW, leader intent):
- Default `False` in production. `True` only after Phase 7 E2E + crash-recovery tests pass.
- Gates: daily scan dispatch; high-water coalesced trigger; bootstrap on first inbox event.
- Manual `/rebuild` and `/update` from the API/frontend are **NEVER** gated by the flag (explicit user action).
- Read via `config.get("blueprint.auto_rebuild_enabled", False)` at every coordinator entry; cheap to flip.

**Queue concurrency verification (leader intent):**
- During Phase 3 implementation, measure `system_background_queue` concurrency under blueprinter load (4-worker fan-out × N projects).
- Add a `verify_queue_concurrency_for_blueprint_workload()` integration test that submits 8 concurrent rebuilds across 4 projects and asserts each gets one job (no duplicates) and exactly one admission failure per project (others 202).
- File: `tests/integration/test_blueprint_admission_coordinator.py` (NEW). **Defer to Phase 7 final verification** if implementation race allows.

**Cross-document reconciliation — durable lease (C7):**
- v1 plan recommended in-memory `set[str]` with 30-min TTL.
- Risk analysis strongly recommended durable DB-backed lease.
- **Synthesis (v2):** Durable lease from the start. In-memory caching may be added later as a fast-path optimization, but **never as the sole defense.**

**Dependencies:** Phase 2 must be complete (pending table + repo methods must exist).

---

### Phase 4 — API Changes (P1)

> **Full detail:** [`evolution-phases-detailed.md`](evolution-phases-detailed.md) §Phase 4 + admission integration.

**Objective:** Replace `/initialize` with `/rebuild` and `/update`. All three endpoints route through the **admission coordinator** (NOT direct enqueue). Returns 202 (queued), 409 (conflict), 503 (lease/store unavailable).

| Endpoint | Behavior |
|---|---|
| `POST /rebuild` | Calls `coordinator.request_rebuild(trigger_source="api")`. Returns 202 + `{job_id, mode: "rebuild"}`. 409 if in-progress. |
| `POST /update` | Calls `coordinator.request_update(trigger_source="api")`. 404 if no corpus. 409 if in-progress. |
| `POST /initialize` (deprecated alias) | Internal delegation to `coordinator.request_rebuild(...)`. Returns 202 + `{mode: "rebuild"}` (NOT the old 409-on-exists semantics). Not removed in this evolution. |
| `POST /scan` (repurposed) | Calls `coordinator.request_*` based on smart trigger logic. Returns `{status, mode, reason, job_id?}`. |

**Semantic change documented:** Old `/initialize` returned 409 if a core already existed. New `/rebuild` allows rebuilding an existing corpus. 409 is now reserved for concurrent conflict only.

**Migration policy (leader-confirmed Q3):**
1. Add `/rebuild` and `/update` as canonical endpoints.
2. Route `/initialize` through the same coordinator; do **not** duplicate enqueue or lease logic.
3. Return a deprecation header + OpenAPI flag; log the caller/path.
4. Update the Angular service/component to call `/rebuild` immediately.
5. Remove only after documented telemetry shows no alias use and a release boundary has passed. If no telemetry is available, **retain the alias indefinitely** — its maintenance cost is small compared to an unknown external break.

**Dependencies:** Phase 3 (admission coordinator + durable lease must exist).

**Phase 4 exit criteria:**
- `/rebuild` returns 202 + `{job_id, mode: "rebuild"}` on empty and existing corpus.
- `/update` returns 404 on empty corpus, 202 on existing corpus.
- `/initialize` returns 202 via internal delegation (alias verified), includes `Deprecation` header.
- 409 returned for concurrent rebuild/update only (NOT for existing corpus).
- All three endpoints route through `coordinator.request_rebuild/request_update` (no direct enqueue).
- Migration policy compliance: frontend service calls `/rebuild` (not `/initialize`); `/initialize` retained but logs caller/path.

---

### Phase 5a — Blueprinter Artifacts (P2)

> **Full detail:** [`evolution-phases-detailed.md`](evolution-phases-detailed.md) §Phase 5 (artifacts created in 5a, integration tested in 5b)

**Objective:** Create all skill artifacts and prompt files for the two-workflow blueprinter: skill files, prompt rewrites, structured report envelope spec, compare/stage/publish semantics spec, and decide-changes model tier config. This sub-step is **artifact authoring only** — no live blueprinter dispatch or integration testing.

| Artifact | Path | Notes |
|---|---|---|
| Skill definitions | `agents/blueprinter/skill-set.yaml` (NEW) | Manifest; version matches skill files |
| `explore-for-rebuild.md` | `agents/blueprinter/skills-template/` (NEW) | First-wave worker skill |
| `explore-for-incremental.md` | `agents/blueprinter/skills-template/` (NEW) | First-wave worker skill |
| `build-blueprint.md` | `agents/blueprinter/skills-template/` (NEW) | Second-wave worker skill |
| `decide-changes.md` | `agents/blueprinter/skills-template/` (NEW) | Fan-in skill (blueprinter-only, NEVER sent to workers) |
| Two-workflow identity | `agents/blueprinter/soul.md` (rewrite) | Outcome-focused, declarative style |
| Fan-out/fan-in coordination | `agents/blueprinter/workflow.md` (rewrite) | Cap 4 workers, bounded retry, no-delete-on-missing |
| Trigger handling + fan-out rules | `agents/blueprinter/rule.md` (modify) | Manual-content protection explicit |
| Model + flag config | `agents/blueprinter/meta.json` (modify) | **NO** `skill_injection: true`; `llm_model` upgrade |

**⚠️ Directory convention:** Use `skills-template/` NOT `skills/`. The live seeder (`skill_seed_service.py:323`) scans `agents/*/skills-template/`. The evolution doc's mention of `skills/` is a discrepancy with the actual seeding code.

**⚠️ `skill_injection: true` clarification (Q1, leader-confirmed):** This flag gates **automatic dynamic injection** in the messaging path, NOT seeding. `seed_all()` scans manifests regardless of the flag. Enabling it on the blueprinter risks auto-injecting worker skills into the blueprinter's own context (self-referential). **OMIT** `skill_injection: true` and rely solely on explicit `send_message(..., load_skill=...)`. Provide a `DEGRADED — skill bank miss` fallback if resolution fails.

**Compare / stage / publish semantics (NEW, old Phase 8 control #4):**
1. Compare desired set (worker findings) with current active set, keyed by stable identity (slug + content fingerprint).
2. Classify each item: `noop`, `update`, `create`, `disable`.
3. **Stage**: write new/changed rows as `status="draft"` in a separate transaction. Trigger embeddings **not yet** regenerated. Matcher continues to see old `published` set.
4. **Validate**: run a coherence check — every staged row has triggers, every trigger embedding succeeded, every revision captured, no orphan embeddings left from disabled rows.
5. **Publish**: set `status="published"` atomically per row, regenerate embeddings in the same transaction, soft-disable confirmed-stale auto rows (`source="auto"`, not in new desired set, manual-protected by default).
6. **Rollback**: on any step after Stage failing, the published set is intact.

Manual content (`source="manual"`) is **NEVER** auto-disabled or overwritten. Review-needed state preserved for human triage.

**Structured worker-report envelope (NEW, old Phase 8 control #5):**
```json
{
  "schema_version": 1,
  "workflow": "rebuild" | "incremental",
  "phase": "explore" | "build",
  "assigned_scope": "...",
  "summary": "...",
  "findings": [
    {
      "target": "<blueprint_slug or scope>",
      "action": "create" | "update" | "disable" | "noop",
      "evidence": "...",
      "verified_paths": ["/abs/path1", "/abs/path2"],
      "confidence": 0.0,
      "gaps": ["..."]
    }
  ],
  "craft_payload": { ... },         // build-phase only
  "status": "complete" | "incomplete",
  "submitted_at": "2026-08-03T...",
  "skill_id": "<host-attached>",
  "skill_version": "<host-attached>",
  "worker_instance_id": "<host-attached>"
}
```

The blueprinter validates required fields, performs at most one repair retry on parse failure, and treats an invalid report as incomplete evidence (no writes). The host attaches skill/worker IDs — workers cannot spoof attribution.

**Decide-changes model tier (NEW, old Phase 8 control #7, leader-confirmed Q4):**
- Ensemble assigns **one model per agent instance** — per-skill/per-phase model switching does not exist.
- Therefore the model tier is set on `meta.json:llm_model`, upgrading from `quick` to `balanced`.
- Leader-confirmed default: **use `balanced` if configured and available, else `quick`**. Add an upgrade note documenting the trade-off (cost vs decision quality at fan-in).
- A higher-cost model for the entire blueprinter run is cheaper than four worker calls on premium models (workers use their own models).

**Dependencies:** Phase 2 (pending table for incremental workflow reference).

---

### Phase 5b — Blueprinter Integration Testing (P2)

**Objective:** Verify the Phase 5a artifacts work end-to-end with the admission coordinator and `BlueprintWriteService`. This is the live integration gate — the artifacts can't be validated without the coordinator.

| Task | Files |
|---|---|
| Dry-run rebuild (skill files load, fan-out/fan-in executes) | `agents/blueprinter/*` (read-only verification) |
| Worker report envelope validation (invalid reports → no writes) | test harness |
| Compare/stage/publish dry-run (stage failure doesn't pollute published) | test harness |
| Decide model tier verification (balanced used when available, quick fallback) | test harness |
| Pending-batch claim/acknowledge through the incremental workflow | `daemon/services/blueprint_write_service.py` |

**Testing approach:**
- Skill seeding test: daemon starts → 4 skills seeded into `skill_bank`.
- Load-skill test: `send_message(load_skill="explore-for-rebuild")` → worker context includes skill content.
- Dry-run rebuild on a small test project → verify blueprinter spawns workers, fans out, fans in, creates blueprints via `BlueprintWriteService`.
- Dry-run incremental: seed pending records → trigger → verify blueprinter reads pending, explores, crafts, saves, acknowledges (sets `processed_at`).

**Dependencies:** Phase 3 (admission coordinator for blueprinter dispatch) + Phase 5a (artifacts must exist) + Phase 1 (`BlueprintWriteService` for canonical writes).

---

### Phase 6 — Frontend (P2)

> **Full detail:** [`evolution-phases-detailed.md`](evolution-phases-detailed.md) §Phase 6

**Objective:** Update the Blueprint UI from a single "Initialize" button to dual-mode: "Rebuild" when empty, "Update" (with popup) when non-empty. Also fixes the 3 frontend gaps (G10–G12).

| Task | Files |
|---|---|
| `rebuild()` and `updateBlueprints()` service methods | `frontend/src/app/services/blueprint.service.ts` |
| Dual-mode button + popup component | `frontend/src/app/pages/blueprint/blueprint.component.ts/html` |
| **Job-status polling** via returned `job_id` (NOT blueprint existence) | `blueprint.component.ts` |
| G10: Type response as `Observable<{job_id, mode}>` not `Observable<void>` | `blueprint.service.ts` |
| G11: Preserve `err.status` in `catchError`; show 409 (conflict) and 404 (no corpus) distinctly | `blueprint.service.ts` |
| G12: Replace `canInitialize` with `canRebuild` / `canUpdate` computed signals | `blueprint.component.ts` |

**Job-status polling implementation (leader-confirmed Q3 follow-up):**
- Poll `/api/jobs/{job_id}` with bounded backoff (1s, 2s, 5s, 10s, 30s; max 5 min total).
- Terminal states: `succeeded`, `failed`, `timeout`, `cancelled` — stop polling.
- On `succeeded`: refresh blueprint list.
- Cancel polling on component destroy / project change.
- Manual refresh button preserved.

**Dependencies:** Phase 4 (endpoints + admission coordinator must exist).

---

### Phase 7 — Smart Daily Scan + E2E Testing (P2)

> **Full detail:** [`evolution-phases-detailed.md`](evolution-phases-detailed.md) §Phase 7 + new E2E sections.

**Objective:** Implement smart scan decision logic, verify the full E2E path against **both PostgreSQL and SQLite**, exercise **crash-during-rebuild recovery**, verify queue concurrency under blueprinter fan-out, and detect embedding-fingerprint mismatches.

**Smart scan logic:**
```
empty corpus (0 blueprints)            → trigger REBUILD
only bare core.md (1, kind=core)       → trigger REBUILD
has blueprints + pending_count > 0     → trigger INCREMENTAL
has blueprints + no pending            → SKIP
```

**⚠️ Bug found by risk analysis (preserved in v2):** The v1 smart scan code at `evolution-phases-detailed.md:1204-1214` has an **unreachable code branch**. `if pending_count > 0` returns at line 1206 before the `if pending_count >= MAX_PENDING_FORCE` check at line 1213 can ever execute. **Fix:** high-water threshold checks happen at INSERT time (trigger coalesced incremental job immediately when count crosses threshold), NOT in the scan logic where it's unreachable.

**Crash-during-rebuild recovery (NEW, explicit Phase 7 scenario):**
- Simulate crash at these points and assert correct post-recovery state:
  - (a) Before claim: lease released on next scan.
  - (b) After claim, before enqueue: lease sweeped to released; next scan retries.
  - (c) After enqueue, before scan-side processing: job queue retains row; next startup reconnects lease + job.
  - (d) During active rebuild (mid-worker dispatch): heartbeat expires → lease released → another rebuild claimable; original job either times out via queue sweeper or completes; either way no double-publish (compare/stage/publish protects).
  - (e) After Stage, before Publish: rollback by re-marking drafts as `status="draft"`; published set intact.
  - (f) During Publish: partial transaction; on retry, orphan drafts detected and either completed or rolled back.
  - (g) After Publish, before ack: claim_release → next scan re-claims records → re-process idempotently (compare protects against duplicates).
  - (h) After ack: records have `processed_at`; ignore.
- Tests live in `tests/integration/test_blueprint_crash_recovery.py` (NEW).

**E2E test suite (v2):**
- 10 user-flow tests: rebuild (empty corpus), rebuild (existing corpus), incremental (small backlog), incremental (high-water coalesce), dual-mode frontend, alias `/initialize`, scan mode transitions (4 states), concurrent rebuild (2 × 409 + 202), PG-only contract tests, SQLite-contract tests.
- 8 crash-recovery tests (a–h above).
- 5 context-injection tests: persistent first-turn (blueprint), `/messages` read path, synthetic system, compaction, checkpoint rebuild.
- 3 embedding-fingerprint tests: same-model (no-op), mismatch + background regen, migration sweep.

**PG + SQLite verification (leader-confirmed):**
- All integration tests run once against PG (`docker compose up postgres`), once against SQLite (in-memory) — `conftest.py` parameterize.
- New `_ensure_postgres_columns()` for any new columns on existing tables (`embedding_model_id`).

**Queue concurrency verification (leader-confirmed):**
- `tests/integration/test_blueprint_queue_concurrency.py` (NEW) — 8 concurrent rebuilds across 4 projects; assert exactly one admission per project + coordinator deduplication.
- Confirm `system_background_queue` concurrency settings accommodate the blueprinter's 4-worker fan-out × N projects without starving user work.
- **Cross-project worker starvation mitigation:** If multiple projects trigger rebuilds simultaneously, the 4-worker fan-out per project can exhaust the worker pool. Mitigation: (a) serialize project processing in the scan service (one project at a time, bounded concurrency), or (b) impose a per-project queue depth limit so a single project's fan-out can't starve others. Verify during Phase 3 implementation which approach fits the `system_background_queue` concurrency model.

**Dependencies:** ALL prior phases (1–6) must be complete.

---

### Phase 8 — (OPTIONAL) Telemetry (P2, droppable)

> **Full detail:** N/A (droppable phase).

**Objective:** Add dashboards, metrics, and alerts for blueprinter operations:
- Queue depth per project.
- Admission denial rate.
- Pending backlog age (median, p95, oldest).
- Worker report invalid-rate.
- Crash-recovery sweep frequency.
- Embedding-mismatch count.

**Decision criterion:** If no observability work is needed by the ship deadline, **drop Phase 8 entirely**. The blueprinter is fully functional without dashboards; metrics can be added later by following the existing observability patterns elsewhere in the codebase.

**Dependencies:** None critical. Soft dependency: Phase 7 metrics need stable run IDs and lease tokens.

---

## 5. Gap Numbering Traceability Matrix (NEW)

All 12 gaps (G1–G12) and all 10 reviewer issues (C1–C10) are accounted for. Cross-references below show where each is closed.

| Gap/Issue | Closed By | Phase | Verification |
|---|---|---|---|
| **G1** blueprint tool trigger_queries | Phase 1 | 1 | Phase 1 exit criteria #1 |
| **G2** update() no revision | Phase 1 + Phase 1 `BlueprintWriteService` | 1 | Phase 1 exit criteria #2 |
| **G3** rate-limiter not called | Phase 1 + Phase 1 `BlueprintWriteService` | 1 | Phase 1 exit criteria #3 |
| **G4** blueprint embedding uses skill_evolution config | Phase 1 | 1 | Phase 1 exit criteria #4 |
| **G5** no daily scan | Phase 3 (admission coordinator + scan service) | 3 | Phase 7 E2E #1 |
| **G6** BM25 single-candidate bug | Phase 2 | 2 | Phase 2 exit criteria (BM25 norm > 0 with one candidate) |
| **G7** no one-core enforcement | Phase 2 (+G7 auto-dedup) | 2 | Phase 2 exit criteria (ValueError on 2nd core) |
| **G8** matcher returns drafts | Phase 2 (status filter) | 2 | Phase 2 exit criteria (0 drafts in matched results) |
| **G9** no concurrent rebuild guard | Phase 3 (durable lease + admission coordinator) | 3 | Phase 7 concurrent integration test |
| **G10** no frontend tracking | Phase 6 (job-id polling) | 6 | Phase 6 exit criteria |
| **G11** frontend error codes lost | Phase 6 | 6 | Phase 6 exit criteria |
| **G12** canInitialize unused | Phase 6 | 6 | Phase 6 exit criteria |
| **C1** dissolve Phase 8 | This v2 reorganization | all | Phase 1–7 reviews |
| **C2** exact pending acknowledgement | Phase 2 (claim/acknowledge state machine + `processed_at`) | 2 | Phase 2 exit criteria |
| **C3** claim/acknowledge state machine | Phase 2 (NEW diagram) | 2 | Phase 2 E2E |
| **C4** compare/stage/publish | Phase 5a (blueprinter artifacts) | 5a | Phase 5b + Phase 7 |
| **C5** structured worker reports | Phase 5a (envelope contract) | 5a | Phase 5b + Phase 7 |
| **C6** decision-model eval | Phase 5a (llm_model upgrade in meta.json) | 5a | Phase 5b |
| **C7** durable lease / admission coordinator | Phase 3 | 3 | Phase 3 + Phase 7 crash recovery |
| **C8** exact-pending claim | Phase 2 (replaces clear-all) | 2 | Phase 2 E2E |
| **C9** canonical write service | Phase 1 (`BlueprintWriteService`) | 1 | Phase 1 + Phase 7 |
| **C10** context-kind allowlist | Phase 2 (`persistence.py:630-637`) | 2 | Phase 7 |

**All 12 gaps + 10 reviewer issues RESOLVED in this plan.**

---

## 6. Feature Flag Section (NEW)

### `auto_rebuild_enabled` (default `False`)

A single global feature flag controls whether **automated** blueprinter triggers dispatch. Manual API calls (`/rebuild`, `/update`) are **always allowed**.

**Triggers gated by the flag:**
- Daily scan dispatch (`BlueprintScanService` may invoke `coordinator.request_rebuild`).
- High-water pending coalesced trigger (`pending_count >= MAX_PENDING_TRIGGER` → `coordinator.request_update`).
- Bootstrap on first inbox event (any project hook).

**Triggers NOT gated:**
- `POST /rebuild` (user-initiated).
- `POST /update` (user-initiated).
- `POST /initialize` (alias of `/rebuild`).
- `POST /scan` (manual diagnostic).

**Read sites:**
- `coordinator.request_rebuild(trigger_source="daily" | "high_water" | "bootstrap")` → early-return `{admitted: False, reason: "auto_rebuild_disabled"}` if flag is `False`.
- Logged at info level on every suppressed trigger so an operator can correlate backlog with the flag.

**Write sites:**
- Single config key in `daemon/config.py`: `blueprint.auto_rebuild_enabled: bool = False`.
- API endpoint `POST /admin/blueprint/feature-flags` (root-only) for runtime toggle during Phase 7 soak.
- Persisted via the existing project-metadata pattern; read on coordinator cold-start.

**Rationale:** A feedback loop where a half-shipped safety control enables automatic rebuilds is exactly the failure mode C1 was designed to prevent. Hold the flag `False` in production until Phase 7 E2E + crash-recovery tests pass and a soak window confirms stability.

---

## 7. The 7 Open Questions — All RESOLVED

> **Full detail:** [`evolution-questions-and-risks.md`](evolution-questions-and-risks.md) §Section 1 (revised per leader decisions).

| # | Question | Resolution (leader decision, see link) |
|---|---|---|
| **Q1** | Worker skill ownership | **Blueprinter-owned**, stored in `agents/blueprinter/skills-template/` (NOT `skills/`). Passed via explicit `load_skill` (`skill_injection: true` is **NOT** a seeding prerequisite — it gates automatic dynamic injection and risks self-referential injection; omit it). |
| **Q2** | Concurrent rebuild guard | **Durable DB-backed build lease** — not in-memory. Lease carries `project_id, mode, job_id, lease_token, heartbeat_at, expires_at, state`. Ref **admission coordinator (C7)** — single point of admission for all trigger sources. |
| **Q3** | `/initialize` backward compat | **Keep as deprecated internal alias** to `/rebuild` for at least one release/telemetry window. Internal delegation via the admission coordinator (not HTTP 308 redirect). Update frontend to `/rebuild` immediately. |
| **Q4** | Blueprinter model for decide phase | **`balanced` tier if configured + available, else `quick` fallback.** Ensemble assigns one model per agent instance — no per-skill switching. Upgrade `meta.json:llm_model` from `quick` to `balanced` (applies to the whole blueprinter run, not just decide). Add an upgrade note documenting the trade-off. |
| **Q5** | Pending queue growth | **High-water mark + coalesced early triggering + bounded claim batches + `processed_at` soft-delete (exact-record acknowledgement).** See the **claim/acknowledge state machine (C3, C8)** in Phase 2. Never TTL-expire or clear-all. |
| **Q6** | Worker report format | **Versioned structured JSON envelope** with free-form evidence strings inside bounded fields. **Now in Phase 5a** (was deferred in v1). |
| **Q7** | Blueprint deletion during rebuild | **Compare and stage; update unchanged in place; create new; soft-disable confirmed-stale auto areas only after replacement is validated.** See compare/stage/publish in Phase 5a. Manual content protected by default. |

**Hook decision (leader):** `project_history_add` pending-queue hook threads the manager through the **factory** so the hook can reach the pending repository. This was a v1 risk (I5) — resolved at Phase 3.

**Queue concurrency decision (leader):** Queue concurrency and blueprinter fan-out interaction is **verified during implementation** (Phase 3 carry-over and Phase 7 final verification).

---

## 8. Risk Priorities (v2 — Updated)

> **Full detail:** [`evolution-questions-and-risks.md`](evolution-questions-and-risks.md) §Section 2.

### Top 10 Critical Risks (v2, ordered by urgency)

| Pri | ID | Risk | L | I | Mitigation | Phase |
|---|---|---|---|---|---|---|
| 1 | **D3** | No revision row written — current verified gap | High | High | `BlueprintWriteService` enforces post-commit revision capture on every write path | **1** |
| 2 | **C-O3** | Rate limiter bypassed (v1 O3 — moved into canonical service) | High | High | `BlueprintWriteService` is the single write path; the limiter gate is on the service, not the tools | **1** |
| 3 | **C-D2** | Clear-all pending races with concurrent arrivals (v1 D2) | High | High | Phase 2 claim/acknowledge state machine with `processed_at` exact-claim; never clear-all | **2** |
| 4 | **C-A1** | `quick` model poor fan-in decisions (v1 A1) | High | High | Phase 5a `llm_model` upgrade (quick→balanced); structured evidence gate | **5a** |
| 5 | **C-I4** | Router writes bypass revision/trigger/rate-limit (v1 I4) | High | High | Phase 1 `BlueprintWriteService` — zero direct `BlueprintRepository` writes from API | **1** |
| 6 | **C-O1** | Restart loses in-memory guard (v1 O1) | Med | High | Phase 3 durable DB-backed lease + heartbeat/expiry + startup reconciliation | **3** |
| 7 | **P3** | BM25 single-candidate bug — current verified gap | High | Med/High | G6 normalization fix | **2** |
| 8 | **O2** | Queue flooding from per-event jobs | High | High | Coalesce per project/mode; high-water trigger at INSERT time | **3** |
| 9 | **M5** | `skills/` vs `skills-template/` path mismatch | High | Med/High | Use `skills-template/`; seed-test on startup; degraded report fallback | **5** |
| 10 | **C-I1** | Context-kind allowlist omits `blueprint` (v1 I1) | Med | High | Phase 2 `persistence.py:630-637` allowlist update + synthetic-system E2E | **2 + 7** |

### Risks Mitigated IN-Phase (no longer separate Phase 8 controls)

The following risks from v1 were "deferred to Phase 8." In v2 they are **closed in their owning phase**:

- v1 **D2** → **C-D2**, Phase 2.
- v1 **O1** → **C-O1**, Phase 3.
- v1 **O3** → **C-O3**, Phase 1 (`BlueprintWriteService`).
- v1 **I4** → **C-I4**, Phase 1 (`BlueprintWriteService`).
- v1 **A1** → **C-A1**, Phase 5a (llm_model upgrade).
- v1 **D3** → **C-D3**, Phase 1 (`BlueprintWriteService`).

### Risks Added in v2

- **C-CW-1:** `BlueprintWriteService` complexity — five invariants in one service invites subtle bugs. Mitigation: extensive unit tests for each invariant; mutation tests on the rate-limiter and revision-capture paths.
- **C-AC-1:** Admission coordinator crash recovery — orphan leases + retry storms. Mitigation: lease sweep on startup + heartbeat-based expiry.
- **C-EF-1:** Embedding model-migration staleness — old vectors live alongside new config; mismatch detection must be cheap. Mitigation: background regen on read-mismatch; never on hot path.
- **C-I1 (v1 I1):** Context-kind allowlist missing in `persistence.py` — known gap at `persistence.py:630-637`.

### Risk Distribution by Category

| Category | Count | Top Risks |
|---|---|---|
| Data integrity (D1–D7) | 7 | C-D2 (claim/ack), C-D3 (canonical revision), C-D6 (partial publish leaves mixed version — mitigated by compare/stage/publish in Phase 5a) |
| Operational (O1–O8) | 8 | C-O1 (durable lease), C-O3 (rate-limit canonical), O2 (queue flooding) |
| Migration (M1–M6) | 6 | M3 (schema absent on existing PG), M5 (skills/ path mismatch) |
| Performance (P1–P7) | 7 | P2 (matcher latency on message path), P3 (BM25 bug), P5 (stale injection) |
| Agent quality (A1–A6) | 6 | C-A1 (decide model), C-A3 (inconsistent worker reports — mitigated by structured JSON envelope in Phase 5a) |
| Integration (I1–I8) | 8 | C-I1 (allowlist), C-I4 (router bypass), C-I5 (history hook can't reach repo) |

---

## 9. Crash-During-Rebuild Recovery (NEW — explicit Phase 7 test scenario)

A rebuild run has seven failure windows. Each must be tested with a daemon kill -9 simulation.

| Window | Expected Behavior | Test |
|---|---|---|
| (a) **Before claim** (admission coord → lease) | Next scan retries; no orphan | `tests/integration/test_blueprint_crash_recovery.py::test_a` |
| (b) **After claim, before enqueue** | Lease sweeped to released on next startup | `test_b` |
| (c) **After enqueue, before scan-side claim** | Queue retains JobItem; next startup reconnects lease + job | `test_c` |
| (d) **Active rebuild, mid-worker dispatch** | Heartbeat expires → lease released; another rebuild claimable; original job retried or swept; NO double-publish (compare/stage/publish protects) | `test_d` |
| (e) **Stage written, before Publish** | Rollback to `status="draft"`; published set intact | `test_e` |
| (f) **During Publish** | Partial transaction; retry detects orphan drafts and either completes or rolls back | `test_f` |
| (g) **After Publish, before ack** | `processed_at` not yet set → next scan re-claims records → re-processes idempotently (compare protects) | `test_g` |
| (h) **After ack** | Records have `processed_at`; ignored | `test_h` |

Tests run with `pytest --blueprint=crash_recovery --repeat=3` for non-determinism.

---

## 10. Embedding Model-Migration Fingerprint (NEW — design consideration)

Trigger rows store `embedding_model_id: str` to detect model upgrades and trigger background regen.

**Schema:**
```sql
ALTER TABLE project_blueprint_triggers
  ADD COLUMN embedding_model_id TEXT NOT NULL DEFAULT '';
```

For PostgreSQL the column is added via `_ensure_postgres_columns()` (per project critical note on dual-driver migrations). For SQLite, `ALTER TABLE` is similarly applied at startup.

**Match logic:**
- On matcher init: fetch `config.get("blueprint.embedding_model_id", "bge-small-en-v1.5@2025-12-01")`.
- On every read of trigger embeddings: if `embedding_model_id != current_id` and the row is referenced (i.e., the trigger belongs to an active blueprint that was matched in the last 7 days, OR is a trigger_query referenced in the blueprint's current content) → schedule background regen; log a warning.
- Background regen: a `BlueprintEmbeddingRegenService` (NEW in Phase 7 E2E) walks all triggers with mismatched fingerprints, regenerates embeddings in batches, updates `embedding_model_id`. **NEVER** blocks the matcher hot path.

**Migration on first startup after config change:**
- Existing rows get `embedding_model_id=""` (unknown).
- On first read, the matcher enumerates any row referenced by a recent query; for those, set the trigger to "needs-recompute" and re-embed.
- A single full sweep handles all older rows.

**Why this matters:** Silent embedding drift from a model upgrade is one of the largest blind spots in the v1 plan. Without a fingerprint, the blueprint corpus can become a "zombie" — words match, scores drop, and no obvious error appears.

---

## 11. Cross-Document Reconciliations (v2 — most resolved by resequencing)

### 11.1 — G9 Guard: In-Memory vs Durable Lease

**Resolution (C7):** Durable DB-backed lease from the start. In-memory cache may be added as a fast-path optimization later (NOT in this evolution). Lease carries `project_id, mode, job_id, lease_token, heartbeat_at, expires_at, state`. **Closed by Phase 3.**

### 11.2 — Q5 Pending Queue: Unreachable Code Branch

**Resolution (C8, C3):** Move the high-water check to **INSERT time** — when a pending record is inserted and count crosses threshold, coalesce-trigger an incremental job immediately. Don't rely on the daily scan to check the threshold. Add the claim/acknowledge state machine (Phase 2) so daily scan is no longer the primary recorder.

### 11.3 — Q6 Worker Reports: Free-Form vs Structured

**Resolution (C5):** Start with the structured JSON envelope from the beginning (Phase 5a). The decide phase needs reliable fields; retrofitting structure later means re-testing all skill files. The envelope includes free-form evidence strings, so LLM expressiveness is preserved.

### 11.4 — `skill_injection: true` on Blueprinter

**Resolution (Q1):** Omit `skill_injection: true`. Rely on explicit `send_message(..., load_skill=...)`. Add a `DEGRADED — skill bank miss` fallback if resolution fails.

### 11.5 — Frontend Polling Strategy

**Resolution:** Poll `job_id` through the existing job-status endpoint (`/api/jobs/{job_id}`). Use bounded backoff. **Closed by Phase 6.**

### 11.6 — `project_history_add` Hook Factory Threading

**Resolution (Leader):** Thread the manager through the **factory** so the hook can reach the pending repository. **Closed by Phase 3.**

### 11.7 — Queue Concurrency Under Blueprinter Fan-Out

**Resolution (Leader):** Verified during Phase 3 implementation and again during Phase 7 E2E.

---

## 12. Frontend Gaps G10–G12 (Integrated into Phase 6)

| Gap | Description | Fix |
|---|---|---|
| **G10** | No polling/refresh after initialize — service types response as `Observable<void>`, ignores `{job_id}` | Use returned `job_id`; poll job-status endpoint; refresh blueprint list on completion |
| **G11** | 409 error handling broken — service replaces error with generic message, loses status code | Preserve `err.status` in `catchError`; show specific messages for 409 (conflict), 404 (no corpus), 503 (lease/store unavailable) |
| **G12** | `canInitialize` getter declared but unused | Replace with `canRebuild` / `canUpdate` computed signals |

---

## 13. Success Criteria (v2 — expanded)

| # | Criterion | Phase | How to Measure | Threshold |
|---|---|---|---|---|
| 1 | G1: Vector matching returns non-zero scores | 1 | Create + search, set `bm25_weight=0` | Score > 0.0 |
| 2 | G2: `update()` with content change creates revision row | 1 | `repo.update` + `repo.list_revisions` | ≥1 revision with correct snapshot |
| 3 | G3: Rate limiter blocks writes at capacity | 1 | Fill limiter, call `blueprint_create` | "Rate-limited" message |
| 4 | G4: Blueprint embedding service uses `BLUEPRINT_EMBEDDING_*` config | 1 | Set env var, inspect service config | Matches, not `skill_evolution` default |
| 5 | **Canonical write boundary (NEW):** all 5 write paths route through `BlueprintWriteService` | 1 | `grep -r "BlueprintRepository.update\|create\|soft_delete"` — only `BlueprintWriteService` matches | Zero direct repo writes |
| 6 | **Canonical write boundary (NEW):** rate-limiter count equals revision count across all paths | 1 | Integration test driving all 5 paths N times | Count identical |
| 7 | **Canonical write boundary (NEW):** manual-content guard refuses disable/overwrite | 1 | Try to disable `source="manual"` without `force=True` | ValueError raised |
| 8 | G6: Lone area blueprint gets non-zero BM25 score | 2 | Single candidate, matching query | bm25_norm > 0.0 |
| 9 | G7: Second core creation fails | 2 | Create core, attempt second | ValueError raised |
| 10 | G7 auto-dedup: identical content inserts only once / 24h | 2 | Insert twice | Second is no-op + audit row |
| 11 | G8: Draft blueprints not returned by matcher | 2 | Create draft + published, match | 0 drafts in results |
| 12 | **Pending-batch contract (NEW):** claim returns disjoint sets; ack sets `processed_at`; no clear-all | 2 | Two concurrent claims | Disjoint IDs; `processed_at` populated |
| 13 | **Context-kind allowlist (NEW):** `blueprint` survives `/messages` read path + synthetic-system | 2 + 7 | E2E + synthetic-system test | Kind preserved |
| 14 | **Embedding fingerprint (NEW):** mismatch detected and regen scheduled, not on hot path | 7 | Set `BLUEPRINT_EMBEDDING_MODEL_ID` to a new value | Background regen triggered; matcher latency unchanged |
| 15 | G9: Concurrent rebuilds via admission coordinator return 202 + 409 | 3 | Two concurrent `/rebuild` | One 202, one 409 |
| 16 | **Durable lease (NEW):** survives API process restart | 3 | Kill -9 mid-rebuild; restart; assert lease state correct + recoverable | Lease recovered |
| 17 | Pending queue inserts on experience() + history(feature/milestone) | 3 | Call both, query pending table | Rows present with correct source |
| 18 | **Auto-rebuild flag (NEW):** suppressed triggers return `admitted=False, reason="auto_rebuild_disabled"` | 3 | Set flag False; trigger high-water | No job dispatched |
| 19 | Smart scan triggers correct mode in all 4 states | 7 | Seed each state, trigger scan | 4/4 correct |
| 20 | **Crash-during-rebuild recovery (NEW):** all 8 windows (a–h) leave correct state | 7 | `pytest --blueprint=crash_recovery` | 8/8 pass |
| 21 | **Context injection E2E (NEW):** persistent first-turn + `/messages` read path + synthetic-system + compaction + checkpoint rebuild | 7 | 5 tests | 5/5 pass |
| 22 | Rebuild creates core + area via fan-out/fan-in | 5b + 7 | Trigger rebuild, poll | ≥1 core, ≥1 area |
| 23 | **Structured worker-report envelope (NEW):** version, required fields, host-attached IDs | 5a + 5b | Run worker with degraded input | Invalid reports = no writes; complete reports commit |
| 24 | **Decide model tier (NEW):** `llm_model=balanced` used when available, `quick` fallback | 5a + 5b | Mock both; assert correct routing | Right tier used |
| 25 | **Compare/stage/publish (NEW):** Stage failures don't pollute published; Publish failures roll back | 5a + 7 | Inject Stage failure; assert published intact | No spurious writes |
| 26 | Frontend shows correct button mode | 6 | Empty vs non-empty corpus | Correct button in both modes |
| 27 | Frontend polls job_id (not blueprint existence) | 6 | Verify polling endpoint and backoff | Right endpoint hit |
| 28 | All existing blueprint tests pass against **PG** | 7 | Full test suite against PG | 0 failures |
| 29 | All new tests pass against **SQLite** | 7 | Full test suite against SQLite | 0 failures |
| 30 | Queue concurrency verified under blueprinter fan-out | 3 + 7 | 8 concurrent rebuilds across 4 projects | Exactly 4 admitted, 4 conflicted |

---

## 14. Implementation Notes

### PostgreSQL First
All tests must run against PostgreSQL (the primary dev/test DB). No SQLite-only syntax. New columns on existing tables use `_ensure_postgres_columns()`. New tables auto-created by `SQLModel.metadata.create_all()`.

### C8 Invariant (Fire-and-Forget)
All error paths use `except Exception` (NOT `BaseException`). Per critical note: `except BaseException: pass` swallows `CancelledError` and breaks async cancellation. All Phase 1–7 fixes, the pending-queue hooks, the admission coordinator, and the scan service must honor this.

### Agent Prompt Convention
All blueprinter prompt file changes (`soul.md`, `rule.md`, `workflow.md`, `meta.json`, `skill-set.yaml`, skill files) must follow `docs/agent-prompt-writing-guide.md` — 10 sections + pre-commit checklist.

### Rate Limiter as Safety Valve
The rate limiter (default 5 revisions/hour) prevents runaway writes. In Phase 1, the limiter is wired into `BlueprintWriteService`. Workers do exploration only (no writes); the blueprinter consolidates and writes sequentially through the service.

### Feature Flag Plumbing
- Read: `config.get("blueprint.auto_rebuild_enabled", False)` at every coordinator entry.
- Write: persistent via project-metadata KV with a `meta_key="blueprint.auto_rebuild_enabled"`. Admin API endpoint flips at runtime.
- Audit: every flip + every suppression is logged.

---

## Appendix A — File Impact Matrix (v2)

| File | Phase(s) | Change Type |
|---|---|---|
| `daemon/services/blueprint_write_service.py` | 1 | **CREATE** — canonical write boundary (review/replace/trigger/limit in one place) |
| `daemon/services/blueprint_admission_coordinator.py` | 3 | **CREATE** — single admission path for all trigger sources |
| `daemon/services/blueprint_build_lease.py` | 3 | **CREATE** — durable lease service + heartbeat/expire |
| `daemon/services/blueprint_embedding_regen_service.py` | 7 | **CREATE** — background regen on fingerprint mismatch |
| `daemon/repositories/blueprint/repository.py` | 1, 2, 3 | Modify: revision capture, one-core guard, status filter, pending CRUD + claim/ack, lease repo |
| `daemon/repositories/blueprint/models.py` | 2, 3 | Modify: `BlueprintPendingUpdate` + `embedding_model_id` column, `BlueprintBuildLease` table |
| `daemon/services/blueprint_matcher.py` | 2 (G6), 7 (fingerprint) | Modify: fix BM25 single-candidate normalization, fingerprint mismatch handling |
| `daemon/manager.py` | 1 (G4), 2, 3, 7 | Modify: blueprint embedding service, pending repo, scan service registration, admission coordinator init, auto_rebuild_enabled flag plumbing |
| `daemon/config.py` | 3 | Modify: add `auto_rebuild_enabled` (default False) |
| `daemon/persistence.py` | 2 | Modify: add `blueprint` to context-kind allowlist (lines 630-637) |
| `daemon/services/context_messages.py` | 2 + 7 | Modify: blueprint kind in synthetic-system + compaction paths |
| `daemon/tools/knowledge_tools.py` | 3 | Modify: replace disabled sidecar with pending-queue INSERT (via factory) |
| `daemon/tools/project_history.py` | 3 | Modify: add pending-queue hook for feature/milestone; **manager threaded through factory** |
| `daemon/services/blueprint_scan_service.py` | 3, 7 | Create: smart scan service (uses coordinator; gates on auto_rebuild_enabled) |
| `daemon/routers/blueprints.py` | 4 | Modify: `/rebuild`, `/update`, alias `/initialize`, repurpose `/scan` — **all via coordinator** |
| `agents/blueprinter/skill-set.yaml` | 5a | CREATE |
| `agents/blueprinter/skills-template/{explore-for-rebuild,explore-for-incremental,build-blueprint,decide-changes}.md` | 5a | CREATE |
| `agents/blueprinter/soul.md` | 5a | Rewrite: two-workflow identity |
| `agents/blueprinter/workflow.md` | 5a | Rewrite: fan-out/fan-in + compare/stage/publish |
| `agents/blueprinter/rule.md` | 5a | Modify: trigger handling + manual-content protection |
| `agents/blueprinter/meta.json` | 5a | Modify: `llm_model` upgrade (quick→balanced); **NO** skill_injection |
| `frontend/src/app/services/blueprint.service.ts` | 6 | Modify: `rebuild()`, `update()`, error handling, job tracking |
| `frontend/src/app/pages/blueprint/*` | 6 | Modify: dual-mode button, popup, polling |
| `tests/test_blueprint_*.py` | 1, 2, 3, 5, 7 | Create/modify: unit + integration + E2E + crash-recovery |
| `tests/integration/test_blueprint_admission_coordinator.py` | 3, 7 | CREATE |
| `tests/integration/test_blueprint_crash_recovery.py` | 7 | CREATE |
| `tests/integration/test_blueprint_queue_concurrency.py` | 3, 7 | CREATE |
| `tests/integration/test_blueprint_context_persistence.py` | 7 | CREATE |

---

## Appendix B — Estimated Effort (v2)

| Phase | Effort | Parallelizable? |
|---|---|---|
| 1 — Critical Fixes + Canonical Write | 3–4 days | G2 parallel with G1+G3; `BlueprintWriteService` after preliminary fixes merge |
| 2 — Data Layer | 2–3 days | Mostly sequential; G6 + G8 + claim/ack + context-kind allowlist |
| 3 — Services + Admission Coord | 3–4 days | Lease table + coordinator + scan service + factory hook + flag plumbing |
| 4 — API | 1 day | After Phase 3 |
| 5a — Blueprinter Artifacts | 2–3 days | **Yes** (parallel with Phase 3/4/6); skill files + prompt rewrite + report envelope spec + compare/stage/publish spec |
| 5b — Blueprinter Integration | 1–2 days | After Phase 3 + 5a; live integration testing with coordinator + write service |
| 6 — Frontend | 1–2 days | **Yes** (parallel with Phase 5b) |
| 7 — Smart Scan + E2E | 2–3 days | No (integration phase) |
| 8 — (Optional) Telemetry | 1–2 days | Independent; **droppable** |
| **Total (with optional Phase 8)** | **~16–23 days** | **Critical path: ~10–14 days** |
| **Total (Phase 8 dropped)** | **~15–21 days** | **Critical path: ~10–14 days** |

Effort increased ~2–4 days over v1 because `BlueprintWriteService`, admission coordinator, durable lease, crash-recovery E2E, and embedding fingerprint are now first-class deliverables (previously soft-deferred). Each new component is smaller than the equivalent Phase 8 work it replaces.
