# Plan Overview: Project Blueprint Evolution — Phases 2–7

Date: 2026-08-03
Author: planner[v2] via plan-creation worker
Status: Draft (Revised — incorporates C1, C3, C6, C7, C8, C10 reviewer fixes)
Reference: `blueprinter-evolution.md` (locked architecture decisions)

> **Revision note (C1 structural resequencing):** The former Phase 8 ("Cross-Cutting Hardening") has been **dissolved**. Its safety controls were *prerequisites* to the automated features shipped in Phases 3–7, not after-thoughts. Each control is now **absorbed into the phase that needs it** — a trigger surface never goes live before its guard does. Phase 8 survives only as an optional telemetry/dashboards phase that can be dropped. There is no "Phase 8 hardening prerequisite" anywhere in this plan.

---

## Objective

Evolve the Project Blueprint subsystem from a single-trigger init/drift model into a **skill-driven, two-workflow (rebuild + incremental) system** backed by a durable pending-experience queue with claim/acknowledge state machine, a unified trigger coordinator that prevents conflicting blueprinter jobs, DB-level one-core-per-project enforcement, safe context injection persistence, and structured worker reports — while fixing five design gaps (G5–G9) that affect correctness, safety, and reliability.

---

## Scope

### In Scope
- **Pending-experience queue** — new `project_blueprint_pending_updates` table with durable claim/acknowledge state machine (C3), `status` column, `processed_at` soft-delete, and hooks in `experience()` and `project_history_add()` (C8 factory-threaded)
- **G6** — BM25 single-candidate normalization fix in `blueprint_matcher.py`
- **G7** — "one core per project" enforcement: **DB-level partial unique index as PRIMARY** + app-level UX guard + pre-flight auto-dedup (C6)
- **G8** — Status filtering consistency (matcher respects `status='published'`)
- **G9** — **Durable trigger coordinator** (replaces in-memory `BlueprintBuildGuard`) — atomic project claim, coalescing, heartbeat, terminal release, startup reconciliation (C7)
- **G5** — Daemon-side daily scan scheduler via `MaintenanceService` (gated by `auto_rebuild_enabled` feature flag)
- **Unified trigger coordinator** — all five trigger surfaces (manual `/rebuild`, manual `/update`, `/scan`, daily maintenance, high-water threshold) go through `try_claim()` before enqueuing (C7)
- **Context injection persistence fix** — add `"blueprint"` to `_CONTEXT_KINDS` allowlist (C10)
- **Context injection E2E tests** — full live injection path: API → agent context → first-turn-only → checkpoint persistence (C10)
- **Compare/stage/publish rebuild semantics** — blueprinter compares existing blueprints before overwrite (C1, absorbed into Phase 5)
- **Versioned structured worker reports** — skill files define the report format from the start (C1, absorbed into Phase 5)
- **Decision-model evaluation** — decide-changes skill notes model tier (`balanced` if available, else `quick`) (C1, absorbed into Phase 5)
- **`auto_rebuild_enabled` feature flag** — daily scan and high-water trigger do not fire until explicitly enabled (safety valve)
- **Embedding model-migration fingerprint** — detect stale vectors when embedding model changes and regenerate
- **Crash-during-rebuild recovery** — E2E test scenario simulating daemon crash mid-rebuild
- **Queue concurrency verification** — test 4-worker fan-out doesn't exceed `system_background_queue` limits or deadlock
- **API changes** — `/rebuild` + `/update` endpoints replacing `/initialize`
- **Blueprinter agent** — skill-set.yaml, 4 skill files, rewritten soul/workflow/rule for two-workflow fan-out/fan-in model
- **Frontend** — dual-mode button (Rebuild / Update popup), polling

### Out of Scope
- **LLM re-rank stage** in the matcher (deferred per architecture doc §"no LLM re-rank")
- **Blueprinter `llm_model` upgrade** beyond tier specified in decide-changes skill (post-implementation tuning)
- **Phase 8 — Telemetry/Dashboards (optional):** operational dashboards for blueprint health, trigger frequency, stale-blueprint alerts. Entirely droppable — no features depend on it.
- **External cron `/scan` endpoint removal** — repurposed for smart daily trigger, not removed

---

## Architecture Summary (Grounded in Current Code)

```
                         ┌─────────────────────┐
   experience() ───────► │  pending_updates    │ ◄──── project_history_add()
   (knowledge_tools.py)  │  table w/ claim/    │      (project_history.py)
                         │  acknowledge SM (C3)│      factory-threaded (C8)
                         └────────┬────────────┘
                                  │
                    ┌─────────────┼──────────────┐
                    ▼                            ▼
          ┌─────────────────┐          ┌──────────────────┐
          │  /rebuild API   │          │  /update API     │
          │  202 / 409      │          │  202 / 409       │
          └────────┬────────┘          └────────┬─────────┘
                   │                            │
                   └──────────┬─────────────────┘
                              ▼
          ┌─────────────────────────────────────────────┐
          │   UNIFIED TRIGGER COORDINATOR (C7, Phase 3)  │
          │   try_claim(project_id, mode, job_id)         │
          │   Heartbeat + coalescing + terminal release   │
          │   Startup reconciliation                      │
          │   ALL 5 surfaces go through this:             │
          │   /rebuild /update /scan daily-maint hi-water │
          └────────────────────┬────────────────────────┘
                               │
                               ▼
          ┌─────────────────────────────────────────────┐
          │           BLUEPRINTER AGENT (Phase 5)        │
          │  skill-set.yaml + 4 skill files              │
          │  Two workflows: rebuild / incremental        │
          │  Structured worker reports (C1)              │
          │  Compare/stage/publish rebuild (C1)          │
          │  decide-changes: model tier note (C1)        │
          └────────────────────┬────────────────────────┘
                               ▲
                               │ enqueues (via coordinator)
          ┌────────────────────┴────────────────────────┐
          │     MaintenanceService (Phase 3, G5)        │
          │  Daily scan: smart trigger (Phase 7)        │
          │  Gated by auto_rebuild_enabled flag          │
          └─────────────────────────────────────────────┘

  MATCHER + DATA LAYER (Phase 2):
    G6: single-candidate BM25 normalization
    G8: filter by status='published' + is_active=True
    G7: DB-level partial unique index (PRIMARY) + app-level UX guard + auto-dedup
    C3: pending queue claim/acknowledge state machine + processed_at soft-delete
    C10: "blueprint" added to _CONTEXT_KINDS allowlist
```

**Key existing mechanisms this plan builds on:**
- Tables created via `SQLModel.metadata.create_all()` at startup (manager.py:437). New tables auto-created. New columns on *existing* tables require `_ensure_postgres_columns()` (manager.py:3176). **Partial unique indexes must NOT use `.sql` files (which NO-OP on PostgreSQL) — use `_ensure_postgres_columns()` pattern or a startup DDL hook.**
- `MaintenanceService` (maintenance.py:68) — generic periodic background service with `register(name, min_interval_hours, execute_fn)`, runs only when idle.
- `skill_seed_service.py` scans `agents/*/skill-set.yaml` + `skills-template/*.md` and populates `skill_bank`. Idempotent. **Codebase convention is `skills-template/` directory.**
- `send_message(..., load_skill="name")` appends `<meta>{"load_skill": "name"}</meta>` tag (instance.py:1617).
- `create_blueprint_tools(manager=manager, ...)` — the existing factory pattern that threads `manager` through. The C8 fix threads `manager` (or `_blueprint_pending_repo`) through `create_project_history_tools()` the same way.
- `create_or_get_by_idempotency_key()` — existing pattern for atomic check-then-insert, the exact class of bug C6 addresses at the DB level.
- `_CONTEXT_KINDS` in `persistence.py:634` — frozenset controlling which context messages persist in checkpoints. **`"blueprint"` is missing → blueprint context messages are NOT persisted (C10).**

---

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 2 | Backend Data Layer + Safety Controls | Pending queue table w/ claim/ack SM (C3) + processed_at soft-delete + `status` column; G6/G7 (DB-level unique index + auto-dedup, C6)/G8 data-layer fixes; C10 context-kind allowlist fix | 12 | tight with Phase 3 (shared table + coordinator consumes pending SM), tight with Phase 5 (blueprinter consumes claim/ack API) | pending |
| 3 | Backend Services + Trigger Coordinator | G5 scheduler (feature-flagged); **C7 unified trigger coordinator** (durable lease, coalescing, heartbeat, startup reconciliation); experience/history hooks (C8 factory-threaded); auto_rebuild_enabled flag | 12 | tight with Phase 2 (table + pending SM), tight with Phase 4 (all API surfaces use coordinator) | pending |
| 4 | API Changes | `/rebuild` + `/update` endpoints via trigger coordinator; `/initialize` alias; repurpose `/scan` | 6 | tight with Phase 3 (coordinator), loose with Phase 5 (triggers blueprinter) | pending |
| 5 | Blueprinter Agent + Safety Controls | Skill files + two-workflow prompt rewrite; **C1 absorbed: compare/stage/publish rebuild semantics, versioned structured worker reports, decide-changes model tier note** | 10 | tight with Phase 2 (pending queue claim/ack), independent of Phase 3/4 | pending |
| 6 | Frontend | Dual-mode button + popup + polling | 6 | tight with Phase 4 (API endpoints), independent of Phase 5 | pending |
| 7 | Smart Daily Scan + E2E Testing | Maintenance job smart trigger + full test suite (PostgreSQL + SQLite); **C10 context injection E2E; crash recovery; queue concurrency; embedding fingerprint** | 12 | tight with ALL prior phases | pending |
| 8 | *(Optional)* Telemetry/Dashboards | Operational dashboards — droppable | — | — | deferred |

---

## Coupling Map

|          | Phase 2 | Phase 3 | Phase 4 | Phase 5 | Phase 6 | Phase 7 |
|----------|---------|---------|---------|---------|---------|---------|
| Phase 2  | —       | tight   | —       | tight   | —       | tight   |
| Phase 3  | tight   | —       | tight   | loose   | —       | tight   |
| Phase 4  | —       | tight   | —       | loose   | tight   | tight   |
| Phase 5  | tight   | loose   | loose   | —       | —       | tight   |
| Phase 6  | —       | —       | tight   | —       | —       | tight   |
| Phase 7  | tight   | tight   | tight   | tight   | tight   | — |

**Key coupling notes (post-C1):**
- **Phase 2 ↔ Phase 3 (tight):** The pending queue claim/acknowledge state machine (C3) defined in Phase 2 is consumed by the trigger coordinator (C7) in Phase 3. The coordinator must understand claim states.
- **Phase 2 ↔ Phase 5 (tight):** The blueprinter (Phase 5) uses `claim_batch` / `acknowledge_batch` / `get_pending_records` from Phase 2's pending-batch contract (C3). The claim/ack API is the bridge.
- **Phase 3 ↔ Phase 4 (tight):** All API endpoints (Phase 4) call `try_claim()` from the coordinator (Phase 3) before enqueuing. Phase 4 cannot ship without Phase 3's coordinator.

**Parallelization opportunities:**
- **Phase 5 (Blueprinter Agent)** can start as soon as **Phase 2** is done — it needs the pending table schema + claim/ack contract but NOT Phase 3 or Phase 4.
- **Phase 6 (Frontend)** can start as soon as **Phase 4** is done — independent of Phase 5.
- **Recommended critical path:** Phase 2 → Phase 3 → Phase 4 → Phase 7, with Phase 5 branching off Phase 2 in parallel and Phase 6 branching off Phase 4 in parallel.

```
Phase 2 (data layer + safety controls)
  ├──► Phase 3 (services + coordinator) ──► Phase 4 (API) ──► Phase 7 (smart scan + E2E)
  │                                              │
  └──► Phase 5 (blueprinter + rebuild safety) ───┼──────► Phase 7
                                                 │
                                          Phase 6 (frontend) ─► Phase 7
```

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | `experience()` pending-queue INSERT adds latency to a hot path | Medium | Medium | INSERT is a single-row DB write (~1ms). Wrap in try/except + WARNING log. Never blocks the experience tool. |
| 2 | ~~Concurrent-rebuild guard uses in-memory lock — lost on restart~~ → **RESOLVED by C7:** Durable trigger coordinator with DB-backed lease, heartbeat, and startup reconciliation. In-memory guard removed entirely. | — | — | C7 eliminates this risk class. |
| 3 | BM25 fix (G6) changes ranking behavior for existing projects with single area blueprint | Low | High | Fix only affects `span == 0` edge case. Unit test both single-candidate and multi-candidate. |
| 4 | Blueprinter skill files use `skills-template/` convention but evolution doc says `skills/` | Low | High | Follow codebase convention (`skills-template/`). |
| 5 | Pending queue grows unbounded if daily scan is delayed | Medium | Medium | Phase 7 smart scan + max-pending threshold (50 records) forces incremental. |
| 6 | Blueprinter fan-out/fan-in with 4 workers hits rate limits or exceeds `system_background_queue` limits | Medium | Medium | Workers do exploration only (no writes). Blueprinter consolidates and writes sequentially with rate-limit checks. Phase 7 adds queue concurrency test. |
| 7 | **C6 risk:** DB-level partial unique index DDL may differ between SQLite and PostgreSQL (boolean representation: SQLite=0/1, PostgreSQL=true/false) | Medium | Medium | Use `WHERE is_active = 1` in the index condition (SQLModel `bool` maps to INTEGER on SQLite, 1/0 works). Test both drivers. Add PostgreSQL concurrency test. |
| 8 | Removing `/initialize` breaks existing frontend/external callers | Medium | Low | Keep `/initialize` as alias that redirects to `/rebuild` internally. |
| 9 | **C7 risk:** Trigger coordinator lease table could accumulate stale leases if blueprinter jobs crash without terminal release | Medium | Medium | Startup reconciliation scans for active leases with no live job → releases them. Heartbeat expiry provides runtime cleanup. |
| 10 | **C8 risk:** `project_history_add()` hook silently swallows pending-queue failures | Medium | Low | C8 fix: log WARNING (not silent pass). Factory-threaded manager reference. Integration test through real construction path. |
| 11 | **C10 risk:** Blueprint context messages lost on checkpoint reload | High | High (currently broken) | C10 fix: add `"blueprint"` to `_CONTEXT_KINDS` in Phase 2. E2E test verifies checkpoint persistence. |
| 12 | **Embedding model migration:** Changing the embedding model leaves stale vectors that produce wrong match scores | Medium | Low | Add `embedding_model_fingerprint` field to blueprint table. On mismatch, mark vectors stale for regeneration. |
| 13 | **Crash mid-rebuild:** Daemon crashes during a rebuild → orphaned lease + half-written corpus | Medium | Low | C7 startup reconciliation releases orphaned leases. Compare/stage/publish semantics (Phase 5) ensure partial writes don't corrupt published blueprints. E2E crash test in Phase 7. |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | A lone area blueprint with BM25-relevant content gets a non-zero fusion score | Unit test: single candidate, matching query → bm25_norm > 0.0 | Score > 0.0 |
| 2 | Creating a second `core` blueprint for the same project fails at DB level | Integration test: two concurrent create-core calls → one succeeds, one gets constraint violation | Exactly 1 core, 1 constraint error |
| 3 | Draft/inactive blueprints are never returned by the matcher | Integration test: draft + published area blueprints → only published in results | 0 draft/inactive in results |
| 4 | Two simultaneous `/rebuild` calls return 202 + 409 via trigger coordinator | API test: concurrent rebuilds → exactly one 202, one 409 | One 202, one 409 |
| 5 | `experience()` inserts into pending_updates table | Integration test: call experience → query pending table → row exists | Row present, source='experience' |
| 6 | Daily scan triggers rebuild on empty corpus, incremental on non-empty+pending, skips on no-pending | E2E test: seed each state, trigger scan → verify correct trigger | Correct trigger in 3/3 states |
| 7 | Blueprinter rebuild workflow creates core + area blueprints via fan-out/fan-in | E2E test: trigger rebuild → verify ≥1 core + ≥1 area blueprint created | ≥1 core, ≥1 area |
| 8 | Frontend shows correct dual-mode button | Manual UI test or component test | Correct button state in both modes |
| 9 | All existing blueprint tests pass after changes | Run full blueprint test suite | 0 regressions |
| 10 | **C3:** Pending-batch claim/acknowledge works — records claimed by a run token can only be acknowledged by that token | Integration test: claim_batch → acknowledge_batch with wrong token → failure; correct token → success | Wrong token rejected, correct token accepted |
| 11 | **C7:** Trigger coordinator coalesces concurrent claims for the same project | Concurrency test: two try_claim calls same project → one gets claim, one gets existing job_id (coalesced) | Exactly 1 new claim, 1 coalesced |
| 12 | **C8:** `project_history_add(entry_type='feature')` through real factory path inserts pending record | Integration test through factory-created tool → verify pending table has row | Row present, source='history' |
| 13 | **C10:** Blueprint appears in agent context and survives checkpoint reload | E2E test: create blueprint → start agent → verify via `/messages` → save+reload checkpoint → blueprint still present | Blueprint present in both initial and reloaded context |
| 14 | **C7:** Startup reconciliation releases orphaned leases | Test: create lease → simulate crash (no terminal release) → restart daemon → verify lease released | Lease released on startup |
| 15 | **Crash recovery:** Daemon crash mid-rebuild does not corrupt published blueprints | E2E test: simulate crash during rebuild → restart → published blueprints intact → new rebuild succeeds | No corruption, rebuild completes |
| 16 | **Queue concurrency:** 4 concurrent workers on `system_background_queue` don't deadlock or exceed limits | Concurrency test: spawn 4 workers simultaneously → all complete without errors | 4/4 complete, no deadlock |

---

## Research Insights

- **blueprint_matcher.py:342–356** — G6 bug: `span = bm25_max - bm25_min`. When `n_docs == 1`, `span == 0`, `bm25_norm = 0.0`. Fix: detect `span == 0 && raw > 0` → `bm25_norm = 1.0`.
- **repository.py:41–49** — `get_core()` filters `kind == 'core' AND is_active == True` but uses `.first()`. G7 requires DB-level enforcement, not just app-level (C6).
- **repository.py:189–222** — `search_candidates()` filters `kind == 'area' AND is_active == True` but NOT `status`. G8 root cause.
- **knowledge_tools.py:411–434** — Blueprinter sidecar commented out. `_enqueue_blueprinter_scan` helper (line 445) intact but unused. Phase 3 replaces with pending-queue INSERT.
- **maintenance.py:68–127** — `MaintenanceService.register()` is the G5 hook. Already wired in manager.py:1700–1765.
- **skill_seed_service.py:323** — Scans `agents/*/skills-template/`.
- **instance.py:1564–1619** — `send_message(load_skill="name")` appends `<meta>` tag.
- **manager.py:758** — Blueprint repository wired. Pending-updates repo wired alongside.
- **persistence.py:634** — `_CONTEXT_KINDS = frozenset({"project", "shared_context", "auto_load_skills", "skills", "task_context"})`. **`"blueprint"` is MISSING** (C10) → blueprint context messages are NOT persisted in checkpoints.
- **project_history.py:94** — `create_project_history_tools(store, ...)` takes `store` but NOT `manager`. C8 fix: add `manager` param matching `create_blueprint_tools(manager=manager, ...)` pattern (blueprint.py:40). Call site at instance.py:1872.
- **blueprint.py:40** — `create_blueprint_tools(manager, current_instance_id, agent_id)` — the pattern to mirror for C8.
- **instance.py:1872** — `create_project_history_tools(manager.project_store, current_instance_id, agent_id)` — call site to update for C8. Should become `create_project_history_tools(manager.project_store, current_instance_id, agent_id, manager=manager)`.

---

## Open Questions

1. **Pending queue max-size trigger** — Configurable max-pending threshold (50 records) forces incremental outside the daily scan window. → **Resolved:** yes, add to Phase 7 smart scan logic.
2. **`/initialize` backward compatibility** — Handle internally as alias to `/rebuild` logic, return 202. → **Resolved:** alias approach.
3. **Rebuild deletion strategy** — During full rebuild, compare/stage/publish semantics (C1): stage new blueprints, compare against existing, publish (update in place or soft-delete+create). → **Resolved:** compare/stage/publish in Phase 5.
4. **Decision model tier** — Use `balanced` if available, else `quick`. → **Resolved by LEADER DECISION #3.**

---

---

# Phase 2: Backend Data Layer + Safety Controls

## Objective

Establish the data foundation with all safety controls needed before any automated feature goes live: the pending-experience queue table with a **durable claim/acknowledge state machine** (C3), `processed_at` soft-delete, `status` column; the one-core-per-project guard with **DB-level partial unique index as PRIMARY mechanism** + auto-dedup (C6); status filtering (G8); BM25 single-candidate fix (G6); and the **C10 context-kind allowlist fix** (one-line, high impact).

## Files Touched

| File | Change Type |
|------|-------------|
| `daemon/repositories/blueprint/models.py` | **Modify** — add `BlueprintPendingUpdate` model with `status` + `processed_at` + `claimed_by_token` columns; add `embedding_model_fingerprint` to `Blueprint` model |
| `daemon/repositories/blueprint/repository.py` | **Modify** — add pending-batch contract methods (claim/ack/get); G7 auto-dedup pre-flight; app-level UX guard |
| `daemon/services/blueprint_matcher.py` | **Modify** — fix G6 single-candidate BM25 normalization (lines 342–356) |
| `daemon/manager.py` | **Modify** — instantiate + wire `_blueprint_pending_repo`; create G7 partial unique index via `_ensure_postgres_columns()` pattern |
| `daemon/persistence.py` | **Modify** — add `"blueprint"` to `_CONTEXT_KINDS` frozenset (line 634) (C10) |
| `tests/test_blueprint_repository.py` | **Modify** — add pending-batch contract tests, one-core guard tests, auto-dedup test, status filter test |
| `tests/test_blueprint_matcher.py` | **Modify** — add single-candidate BM25 test |

## Key Changes

### 2.1 — New Table: `project_blueprint_pending_updates` (with C3 state machine)

Add to `daemon/repositories/blueprint/models.py`:

```python
class BlueprintPendingUpdate(SQLModel, table=True):
    """Accumulated experience/history changes awaiting incremental blueprint update.

    Implements durable claim/acknowledge state machine (C3):
    available → claimed → applied → (soft-deleted via processed_at)
                        ↓
                   retryable → claimed (re-dispatch)
                        ↓
                   abandoned (after N retries or lease timeout)
    """
    __tablename__ = "project_blueprint_pending_updates"
    __table_args__ = (
        Index("ix_bp_pending_project_id", "project_id"),
        Index("ix_bp_pending_created_at", "created_at"),
        Index("ix_bp_pending_status", "status"),
        Index("ix_bp_pending_project_status", "project_id", "status"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=64)
    project_id: str = Field(sa_column=Column(String, nullable=False), max_length=64)
    source: str = Field(default="experience", max_length=16)  # 'experience' | 'history'
    text: str = Field(sa_column=Column(Text, nullable=False))  # truncated to 10k
    created_at: str = Field(default_factory=_now_iso)

    # C3: Claim/acknowledge state machine
    status: str = Field(default="available", max_length=16)
    # Values: 'available' | 'claimed' | 'applied' | 'retryable' | 'abandoned'

    # C3: Run/lease token that claimed this record
    claimed_by_token: str | None = Field(default=None, max_length=64)

    # C3: Timestamp when claimed (for lease timeout → retryable transition)
    claimed_at: str | None = Field(default=None)

    # C3 + LEADER DECISION #1: processed_at soft-delete
    # acknowledge_batch sets this. Periodic cleanup hard-deletes old records.
    processed_at: str | None = Field(default=None)

    # C3: retry counter
    retry_count: int = Field(default=0)
```

**No `_ensure_postgres_columns()` needed** — this is a brand-new table, auto-created by `SQLModel.metadata.create_all()` at startup. The model must be imported wherever existing blueprint models are imported so it registers with `SQLModel.metadata`.

### 2.2 — C3: Durable Pending-Batch Contract

Add to `BlueprintRepository` (or a dedicated `BlueprintPendingRepository` — recommendation: same repo for cohesion):

```python
# ── C3: Pending-Batch Contract ──

def add_pending(self, project_id: str, source: str, text: str) -> BlueprintPendingUpdate:
    """INSERT a pending update with status='available'. Text truncated to 10k chars."""

def claim_batch(self, project_id: str, batch_size: int, run_token: str) -> list[BlueprintPendingUpdate]:
    """Atomically claim the N oldest 'available' records for a project.

    Sets status='claimed', claimed_by_token=run_token, claimed_at=now().
    Returns the claimed records. If fewer than batch_size available,
    returns whatever is available.
    """

def acknowledge_batch(self, run_token: str, record_ids: list[str]) -> int:
    """Mark specified records as 'applied' (sets processed_at=now()).

    Only the matching run_token can acknowledge.
    Records NOT in the ack list remain 'claimed' until lease timeout → 'retryable'.
    Returns count acknowledged.
    """

def get_pending_records(self, record_ids: list[str]) -> list[BlueprintPendingUpdate]:
    """Retrieve full text of specific pending records by ID.
    Used by the blueprinter to read claimed records.
    """

def list_pending(self, project_id: str, limit: int = 100) -> list[BlueprintPendingUpdate]:
    """Load pending updates for a project, oldest first.
    Returns records with status IN ('available', 'retryable').
    """

def count_pending(self, project_id: str) -> int:
    """Count unprocessed pending updates (status IN ('available', 'retryable'))."""

def mark_retryable(self, lease_timeout_minutes: float = 30.0) -> int:
    """Transition 'claimed' records whose claimed_at is older than
    lease_timeout_minutes to 'retryable'. Increment retry_count.
    Records exceeding MAX_RETRIES (e.g., 3) → 'abandoned'.
    Returns count transitioned.
    """

def cleanup_processed(self, older_than_days: int = 7) -> int:
    """Hard-delete records with processed_at older than N days.
    Periodic cleanup job calls this. Provides crash recovery.
    Returns count deleted.
    """
```

**State machine transitions (C3):**

```
available ──(claim_batch)──► claimed ──(acknowledge_batch)──► applied ──(cleanup_processed)──► [hard-deleted]
                                │
                                │ (lease timeout / mark_retryable)
                                ▼
                            retryable ──(claim_batch)──► claimed
                                │
                                │ (retry_count >= MAX_RETRIES)
                                ▼
                            abandoned
```

**Key properties:**
- **Oldest-first claim:** `claim_batch` always takes the N oldest available records.
- **Immutable record IDs:** Each pending record has a stable UUID. Acknowledgement uses these IDs.
- **Idempotent acknowledgement:** Only the matching `run_token` can acknowledge. Records not in the ack list remain 'claimed' until lease timeout.
- **processed_at soft-delete (LEADER DECISION #1):** Records are NOT hard-deleted by `acknowledge_batch`. `processed_at` is set. A periodic cleanup job hard-deletes records older than N days with `processed_at IS NOT NULL`.

### 2.3 — C6: G7 One-Core-Per-Project — DB-Level Primary + Auto-Dedup

**Step 1: Auto-dedup pre-flight (LEADER DECISION #2)**

Add to `BlueprintRepository`:

```python
def auto_dedup_cores(self, project_id: str) -> int:
    """G7 pre-flight: if multiple active cores exist for a project,
    keep the most recent (highest version or latest updated_at) and
    soft-disable the rest (is_active = False). Log each dedup action.

    Run BEFORE creating the unique index.
    Returns count of cores soft-disabled.
    """
    cores = self._session.exec(
        select(Blueprint)
        .where(Blueprint.project_id == project_id)
        .where(Blueprint.kind == "core")
        .where(Blueprint.is_active == True)
        .order_by(Blueprint.version.desc(), Blueprint.updated_at.desc())
    ).all()

    if len(cores) <= 1:
        return 0

    # Keep the first (highest version, latest updated_at)
    kept = cores[0]
    disabled = 0
    for core in cores[1:]:
        core.is_active = False
        logger.info(
            "G7 auto-dedup: soft-disabling duplicate core %s for project %s "
            "(kept %s)", core.id, project_id, kept.id
        )
        disabled += 1
    self._session.commit()
    return disabled
```

**Step 2: DB-level partial unique index (PRIMARY mechanism)**

Create in `manager.py` startup (after table creation, using `_ensure_postgres_columns()` pattern or a startup DDL hook):

```python
def _ensure_blueprint_g7_unique_index(self):
    """C6: Create the one-core-per-project partial unique index.

    MUST use raw DDL via the engine, NOT a .sql migration file
    (which NO-OPs on PostgreSQL per critical note).
    """
    # Step 1: Auto-dedup existing duplicates (LEADER DECISION #2)
    if self._blueprint_repo is not None:
        for project_id in self._get_all_project_ids():
            self._blueprint_repo.auto_dedup_cores(project_id)

    # Step 2: Create partial unique index
    # SQLite stores booleans as 0/1 (SQLModel bool → INTEGER).
    # PostgreSQL uses true/false. WHERE is_active = 1 works on BOTH:
    #   - SQLite: 1 matches the integer representation
    #   - PostgreSQL: 1 is implicitly cast to boolean true
    ddl = """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_blueprint_one_core
        ON project_blueprints (project_id)
        WHERE kind = 'core' AND is_active = 1
    """
    with self._engine.connect() as conn:
        conn.execute(text(ddl))
        conn.commit()
    logger.info("G7: Created partial unique index ux_blueprint_one_core")
```

> **SQLite boolean verification (C6 §4):** SQLModel's `is_active: bool = Field(default=True)` maps to `INTEGER` on SQLite (0/1). The partial index `WHERE is_active = 1` must be verified on both drivers. On PostgreSQL, `1` is implicitly cast to `true`. Test both. If PostgreSQL requires explicit `true`, add a driver-conditional DDL branch.

**Step 3: App-level UX guard (convenience only)**

Keep a lightweight check in `repository.py:create()` for a friendly error message BEFORE the DB constraint fires:

```python
def create(self, **fields: Any) -> Blueprint:
    # G7 UX guard: friendly error before DB constraint fires
    if fields.get("kind") == "core":
        existing = self.get_core(fields["project_id"])
        if existing is not None:
            raise ValueError(
                f"Project {fields['project_id']} already has a core blueprint "
                f"(id={existing.id}). Only one core per project is allowed."
            )
    # ... existing create logic
    # The DB-level partial unique index (ux_blueprint_one_core) is the
    # PRIMARY enforcement. This app-level check is UX convenience only.
```

### 2.4 — G8: Status Filtering in `search_candidates()`

Modify `repository.py:search_candidates()` (line 189):

```python
# Before:
Blueprint.kind == "area",
Blueprint.is_active == True,

# After:
Blueprint.kind == "area",
Blueprint.is_active == True,
Blueprint.status == "published",  # G8: draft blueprints are NOT matchable
```

### 2.5 — G6: BM25 Single-Candidate Fix

Modify `blueprint_matcher.py` lines 342–356:

```python
if span > 0:
    bm25_norm = (bm25_raw[idx] - bm25_min) / span
elif bm25_raw[idx] > 0:
    # G6: single-candidate or all-equal edge case.
    # A non-zero raw BM25 score means the candidate has relevant terms.
    # With no spread to normalize against, treat it as fully relevant.
    bm25_norm = 1.0
else:
    bm25_norm = 0.0
```

### 2.6 — C10: Context-Kind Allowlist Fix (one-line, high impact)

In `daemon/persistence.py:634`, add `"blueprint"` to the frozenset:

```python
# Before:
_CONTEXT_KINDS = frozenset({
    "project", "shared_context", "auto_load_skills", "skills",
    "task_context",
})

# After:
_CONTEXT_KINDS = frozenset({
    "project", "shared_context", "auto_load_skills", "skills",
    "task_context", "blueprint",  # C10: blueprint context messages MUST persist in checkpoints
})
```

This is a **prerequisite** for blueprint injection to work correctly with checkpoint persistence. Without it, blueprint context messages are silently dropped on checkpoint reload.

### 2.7 — Embedding Model Fingerprint

Add `embedding_model_fingerprint` to the `Blueprint` model:

```python
# In Blueprint model:
embedding_model_fingerprint: str | None = Field(default=None, max_length=128)
# Set when trigger_queries are embedded. On matcher load, compare against
# current embedding model fingerprint. Mismatch → mark stale for regeneration.
```

The fingerprint is a hash of the embedding model name + version. When the embedding model changes, the matcher detects stale fingerprints and triggers regeneration (Phase 7 test covers this).

## Dependencies

- **Phase 1** should be complete so the matcher has working vector scoring.
- **None otherwise** — this is the foundation phase.

## Testing Approach

1. **Unit test — pending queue CRUD:** `add_pending` → `list_pending` → `count_pending` → `claim_batch` → `acknowledge_batch` → verify `processed_at` set.
2. **Unit test — C3 claim/acknowledge state machine:**
   - `add_pending` → status='available'
   - `claim_batch(run_token_A)` → records status='claimed', claimed_by_token=run_token_A
   - `acknowledge_batch(run_token_B, ...)` → FAILS (wrong token)
   - `acknowledge_batch(run_token_A, ...)` → status='applied', processed_at set
   - Records NOT in ack list remain 'claimed'
3. **Unit test — C3 lease timeout:** `claim_batch` → manually set `claimed_at` to 31 min ago → `mark_retryable()` → status='retryable'. `claim_batch` picks up 'retryable' records.
4. **Unit test — C3 abandoned:** Set retry_count = MAX_RETRIES → `mark_retryable()` → status='abandoned'. `count_pending` excludes 'abandoned'.
5. **Unit test — C3 cleanup:** Set `processed_at` to 8 days ago → `cleanup_processed(older_than_days=7)` → record hard-deleted.
6. **Unit test — G7 auto-dedup:** Create 3 active cores for same project → `auto_dedup_cores()` → 1 active, 2 soft-disabled.
7. **Integration test — G7 DB-level enforcement:** Two concurrent `create(core)` calls → one succeeds, one gets `IntegrityError` (constraint violation).
8. **Unit test — G7 app-level UX guard:** Create core → attempt second → `ValueError` with friendly message.
9. **Unit test — status filtering (G8):** Create published + draft area blueprints → `search_candidates()` → only published returned.
10. **Unit test — BM25 single-candidate (G6):** 1 area candidate with matching content → fused score > 0.0.
11. **Unit test — BM25 multi-candidate regression:** 3 candidates → ranking order unchanged.
12. **Integration test — table creation:** Start daemon fresh against PostgreSQL → verify `project_blueprint_pending_updates` table exists with all C3 columns.

## Risks

- **R3 (BM25 behavior change):** Only affects `span == 0` edge case. Covered by unit tests.
- **R7 (G7 constraint enforcement):** DB-level index created via `_ensure_postgres_columns()` pattern (not `.sql`). Auto-dedup runs first. SQLite boolean representation verified.
- **R11 (C10):** High likelihood of being currently broken. Fix is one-line. E2E verification in Phase 7.

## Exit Criterion

All 12 tests pass. Pending-updates table exists with C3 state machine columns. G7 partial unique index created (auto-dedup ran first). C10 allowlist fix applied. G6, G7, G8 verified against PostgreSQL. `embedding_model_fingerprint` column added.

---

# Phase 3: Backend Services + Trigger Coordinator (C7)

## Objective

Wire the pending-experience queue into the live system, implement the **C7 unified trigger coordinator** (durable lease with coalescing, heartbeat, terminal release, and startup reconciliation) BEFORE any trigger surface goes live, implement the daemon-side daily scan scheduler (G5) gated by the `auto_rebuild_enabled` feature flag, and fix the C8 factory-threading for `project_history_add()`.

> **Critical safety invariant (C7):** No trigger surface (manual `/rebuild`, `/update`, `/scan`, daily maintenance, high-water threshold) enqueues a blueprinter job without first calling `try_claim()` on the trigger coordinator. The coordinator is the single chokepoint for all blueprint build enqueuing.

## Files Touched

| File | Change Type |
|------|-------------|
| `daemon/tools/knowledge_tools.py` | **Modify** — replace disabled sidecar with pending-queue INSERT; remove keyword filtering |
| `daemon/tools/project_history.py` | **Modify** — factory signature change (add `manager` param, C8); add pending-queue INSERT |
| `daemon/tools/instance.py` | **Modify** — update `create_project_history_tools()` call site (line 1872) to pass `manager` (C8) |
| `daemon/manager.py` | **Modify** — register blueprint daily-scan maintenance job (gated by flag); wire `_blueprint_pending_repo`; instantiate trigger coordinator; add startup reconciliation |
| `daemon/services/blueprint_scan_service.py` (NEW) | **Create** — `BlueprintScanService` with smart trigger logic |
| `daemon/services/blueprint_trigger_coordinator.py` (NEW) | **Create** — C7 unified trigger coordinator |
| `daemon/services/maintenance.py` | **No change** (use existing `register()` pattern) |

## Key Changes

### 3.1 — C7: Unified Trigger Coordinator

Create `daemon/services/blueprint_trigger_coordinator.py`:

```python
class ClaimResult:
    """Result of try_claim()."""
    claimed: bool          # True if this caller acquired the lease
    job_id: str           # The job_id to use (new or existing if coalesced)
    coalesced: bool       # True if an existing active build was found
    conflict_mode: str | None  # The mode of the conflicting build (if not claimed)

class BlueprintTriggerCoordinator:
    """C7: Unified trigger coordinator for ALL blueprint build enqueuing.

    Five trigger surfaces MUST go through try_claim() before enqueuing:
    1. Manual /rebuild
    2. Manual /update
    3. /scan (manual smart scan)
    4. Daily maintenance (BlueprintScanService)
    5. High-water threshold trigger

    Guarantees:
    - Atomic project claim across modes (no two builds for same project)
    - Coalescing (second claim returns existing job_id, no new enqueue)
    - Heartbeat-based lease (expired heartbeat → lease released)
    - Terminal release on job completion (success/failure/cancellation)
    - Startup reconciliation (orphaned leases released on daemon start)
    """

    LEASE_TTL_SECONDS = 600  # 10 min heartbeat expiry
    HEARTBEAT_INTERVAL_SECONDS = 120  # blueprinter sends heartbeat every 2 min

    def __init__(self, store_or_repo, job_queue_service):
        # Lease stored in project-metadata table via unique (project_id, meta_key)
        # meta_key = 'blueprint_build_lease'
        # OR in a dedicated project_blueprint_build_leases table.
        ...

    async def try_claim(
        self, project_id: str, mode: str, job_id: str
    ) -> ClaimResult:
        """Atomically acquire a build lease for the project.

        Returns ClaimResult:
        - claimed=True, job_id=job_id: this caller acquired the lease
        - claimed=False, job_id=existing_job_id, coalesced=True:
            an active build exists; return its job_id (no new enqueue)
        - claimed=False, conflict_mode=...: a different-mode build is active
        """
        ...

    async def heartbeat(self, project_id: str, run_token: str) -> bool:
        """Blueprinter job sends periodic heartbeats.
        If run_token doesn't match current lease → False (stale).
        Updates last_heartbeat_at.
        """
        ...

    async def release(self, project_id: str, run_token: str) -> bool:
        """Terminal release. Only releases if run_token matches.
        Called on job completion (success, failure, cancellation).
        """
        ...

    async def reconcile_on_startup(self) -> int:
        """Scan for active leases with no live job → release them.
        Called once during daemon startup.
        Returns count of orphaned leases released.
        """
        ...

    async def _sweep_expired_leases(self) -> int:
        """Periodic sweep: release leases whose heartbeat expired.
        Called by MaintenanceService.
        """
        ...
```

**Lease storage:** Use the project-metadata table with `meta_key = 'blueprint_build_lease'`. The lease record contains:
```json
{
  "run_token": "<uuid>",
  "job_id": "<job_id>",
  "mode": "rebuild" | "incremental",
  "claimed_at": "<iso>",
  "last_heartbeat_at": "<iso>"
}
```

**Atomicity:** The claim uses an upsert with a conditional check: `INSERT ... ON CONFLICT (project_id, meta_key) DO NOTHING` (PostgreSQL) or equivalent atomic check-then-set. If the insert succeeds, the lease is acquired. If it fails (row exists), read the existing lease and return coalesced/conflict.

**Startup reconciliation:** During daemon startup (after services initialize), call `coordinator.reconcile_on_startup()`. This queries the job queue for each active lease's job_id. If the job doesn't exist or is in a terminal state, release the lease.

### 3.2 — `auto_rebuild_enabled` Feature Flag

Add to daemon configuration:

```python
# In manager.py or config:
self._auto_rebuild_enabled: bool = False  # DEFAULT OFF

# The daily scan and high-water trigger check this flag:
if not self._auto_rebuild_enabled:
    logger.debug("Blueprint auto-rebuild disabled (auto_rebuild_enabled=False)")
    return  # skip scan
```

This flag is `False` by default. It must be explicitly enabled (via config file, env var, or API setting) before the daily scan and high-water trigger fire. **This provides a safety valve during development/testing** — Phases 3–7 can be developed and tested without automatic triggers firing unexpectedly.

### 3.3 — `experience()` Pending-Queue Hook

In `daemon/tools/knowledge_tools.py`, replace the disabled sidecar block (lines 411–434) with:

```python
# ── Blueprint pending-queue INSERT (replaces disabled sidecar) ──
try:
    pending_repo = getattr(manager, "_blueprint_pending_repo", None)
    if pending_repo is not None:
        pending_repo.add_pending(
            project_id=project_id,
            source="experience",
            text=text[:10_000],
        )
except Exception as bp_err:
    logger.warning("Blueprint pending-queue INSERT failed (non-fatal): %s", bp_err)
```

**Key behavior change:** No keyword filtering. ALL experience text goes into the pending queue. The filtering responsibility moves to the blueprinter's `explore-for-incremental` skill and `decide-changes` fan-in.

### 3.4 — C8: `project_history_add()` Hook — Factory-Threaded (LEADER DECISION #4)

**Step 1: Change factory signature** in `daemon/tools/project_history.py`:

```python
def create_project_history_tools(
    store, current_instance_id: str = "", agent_id: str = "",
    manager=None,  # C8: thread manager for blueprint pending-queue access
) -> list:
    """Create project history tools bound to a project store.

    Args:
        store: The project store.
        current_instance_id: The ID of the current instance.
        agent_id: The agent_id of the calling instance.
        manager: The InstanceManager instance (for blueprint pending-queue
            access). Optional — if None, the history→blueprint hook is skipped.
    """
```

**Step 2: Add the hook** inside `project_history_add` (after `store.add_history_entry()` succeeds, line 137):

```python
# ── C8: Blueprint pending-queue hook for history events ──
# Feature/milestone events indicate structural changes worth a blueprint
# incremental update. Insert into pending table (fire-and-forget).
pending_repo = getattr(manager, "_blueprint_pending_repo", None) if manager else None
if pending_repo is not None and entry_type in ("feature", "milestone"):
    try:
        text_for_pending = f"[{entry_type}] {summary}"
        if details:
            text_for_pending += f"\n{details[:5000]}"
        pending_repo.add_pending(
            project_id=project_id,
            source="history",
            text=text_for_pending[:10_000],
        )
    except Exception as e:
        # C8: Make failures OBSERVABLE — WARNING log, not silent pass
        logger.warning(
            "Blueprint pending-queue INSERT failed for history event (non-fatal): %s", e
        )
```

**Step 3: Update call site** in `daemon/tools/instance.py:1872`:

```python
# Before:
history_tools = create_project_history_tools(
    manager.project_store, current_instance_id, agent_id
)

# After:
history_tools = create_project_history_tools(
    manager.project_store, current_instance_id, agent_id,
    manager=manager,  # C8: thread manager for blueprint pending-queue access
)
```

This matches the existing `create_blueprint_tools(manager=manager)` pattern (blueprint.py:40).

### 3.5 — G5: Daemon-Side Daily Scan Scheduler (Feature-Flagged)

Create `daemon/services/blueprint_scan_service.py`:

```python
class BlueprintScanService:
    """Daemon-side blueprint daily scan, registered with MaintenanceService.

    Runs when the system is idle AND auto_rebuild_enabled is True.
    Implements smart trigger logic (full logic in Phase 7):
    - Empty corpus → enqueue REBUILD (via coordinator)
    - Bare core only → enqueue REBUILD (via coordinator)
    - Has blueprints + pending → enqueue INCREMENTAL (via coordinator)
    - Has blueprints + no pending → skip
    """

    def __init__(self, blueprint_repo, pending_repo, job_queue_service,
                 coordinator: BlueprintTriggerCoordinator):
        ...

    async def execute(self) -> None:
        """Called by MaintenanceService on its interval."""
        # Feature flag gate
        if not self._auto_rebuild_enabled:
            return
        for project_id in self._get_active_projects():
            await self._scan_project(project_id)
```

Register in `manager.py` near line 1765:

```python
if self._blueprint_repo is not None and self._blueprint_pending_repo is not None:
    self._blueprint_trigger_coordinator = BlueprintTriggerCoordinator(
        store=self._metadata_store,
        job_queue_service=self._job_queue_service,
    )
    self._blueprint_scan_service = BlueprintScanService(
        blueprint_repo=self._blueprint_repo,
        pending_repo=self._blueprint_pending_repo,
        job_queue_service=self._job_queue_service,
        coordinator=self._blueprint_trigger_coordinator,
    )
    self._maintenance_service.register(
        "blueprint_daily_scan",
        min_interval_hours=24.0,
        execute_fn=self._blueprint_scan_service.execute,
    )
    # Register lease sweeper
    self._maintenance_service.register(
        "blueprint_lease_sweep",
        min_interval_hours=0.5,  # every 30 min
        execute_fn=self._blueprint_trigger_coordinator._sweep_expired_leases,
    )

# Startup reconciliation
async def _on_startup(self):
    if self._blueprint_trigger_coordinator:
        released = await self._blueprint_trigger_coordinator.reconcile_on_startup()
        if released > 0:
            logger.info("C7: Released %d orphaned blueprint build leases on startup", released)
```

## Dependencies

- **Phase 2** must be complete — pending-updates table, C3 state machine, and G7 index must exist.
- **C7 coordinator** must be implemented BEFORE any trigger surface (Phase 4 API endpoints, Phase 7 scan logic) goes live.

## Testing Approach

1. **Integration test — experience hook:** Call `experience()` → query pending table → row exists with `source='experience'`.
2. **Integration test — C8 history hook (through real factory path):** Call `create_project_history_tools(store, iid, aid, manager=manager)` → call resulting `project_history_add(entry_type='feature')` → query pending table → row exists with `source='history'`. Call with `entry_type='bugfix'` → no row.
3. **Integration test — C8 failure observability:** Mock `pending_repo.add_pending` to raise → verify WARNING logged (not silent).
4. **Unit test — C7 coordinator try_claim:** `try_claim(proj, "rebuild", job_A)` → claimed=True. `try_claim(proj, "rebuild", job_B)` → claimed=False, coalesced=True, job_id=job_A.
5. **Unit test — C7 coordinator cross-mode conflict:** `try_claim(proj, "rebuild", job_A)` → claimed. `try_claim(proj, "incremental", job_B)` → claimed=False, conflict_mode='rebuild'.
6. **Unit test — C7 heartbeat:** After claim → `heartbeat(proj, token)` → True. With wrong token → False.
7. **Unit test — C7 release:** After claim → `release(proj, token)` → True. After release → `try_claim` succeeds again.
8. **Unit test — C7 startup reconciliation:** Create lease → simulate crash (no terminal release) → `reconcile_on_startup()` → lease released.
9. **Integration test — G5 scheduler (feature-flagged):** With `auto_rebuild_enabled=False` → scan does nothing. Set `True` → scan fires.
10. **Latency test — experience hook overhead:** Measure `experience()` call time before/after → overhead < 5ms.

## Risks

- **R1 (experience latency):** Single-row INSERT (~1ms). Fire-and-forget. Covered by latency test.
- **R9 (stale lease accumulation):** Mitigated by startup reconciliation + periodic sweep + heartbeat expiry.
- **R10 (C8 silent failures):** C8 fix: WARNING log + factory threading + integration test.

## Exit Criterion

Trigger coordinator implemented and tested. Pending-queue hooks fire on `experience()` and `project_history_add(feature/milestone)` through real factory path. Daily scan service registered but gated by `auto_rebuild_enabled` flag. Startup reconciliation releases orphaned leases. All tests pass.

---

# Phase 4: API Changes

## Objective

Replace the single `/initialize` endpoint with `/rebuild` and `/update`, both routed through the **C7 trigger coordinator** (not the old in-memory guard). The `/scan` endpoint is repurposed for manual smart-scan triggers, also via the coordinator.

> **Safety invariant:** All API endpoints call `coordinator.try_claim()` before enqueuing. No direct blueprinter enqueue bypasses the coordinator.

## Files Touched

| File | Change Type |
|------|-------------|
| `daemon/routers/blueprints.py` | **Modify** — add `/rebuild` and `/update` endpoints using coordinator; convert `/initialize` to alias; repurpose `/scan` |
| `frontend/src/app/services/blueprint.service.ts` | **Modify** (in Phase 6) |

## Key Changes

### 4.1 — `POST /rebuild` Endpoint (via C7 Coordinator)

```python
@router.post("/rebuild", response_model=dict, status_code=202)
async def rebuild_project_blueprints(request: Request, project_id: str):
    """Trigger a full blueprint rebuild for a project.

    202 Accepted (enqueues blueprinter with trigger: 'rebuild').
    409 Conflict if a build is already in progress for this project.
    """
    coordinator = request.app.state.blueprint_trigger_coordinator
    job_id = str(uuid.uuid4())

    result = await coordinator.try_claim(project_id, "rebuild", job_id)
    if not result.claimed:
        if result.coalesced:
            return {"job_id": result.job_id, "status": "already_in_progress", "mode": "rebuild"}
        raise HTTPException(status_code=409, detail=f"Blueprint {result.conflict_mode} already in progress")

    # Enqueue blueprinter job
    job = await job_service.enqueue(
        agent_id="blueprinter",
        message=f"Rebuild all project blueprints...\n[trigger: rebuild]",
        source="admin-endpoint",
        project_id=project_id,
        priority=9,
        queue_id=bg_queue.queue_id,
        metadata={"trigger": "rebuild", "source": "admin-endpoint", "run_token": result.job_id},
    )
    return {"job_id": job.job_id, "status": "enqueued", "mode": "rebuild"}
```

### 4.2 — `POST /update` Endpoint (via C7 Coordinator)

```python
@router.post("/update", response_model=dict, status_code=202)
async def update_project_blueprints(request: Request, project_id: str):
    """Trigger an incremental blueprint update for a project.

    202 Accepted (enqueues blueprinter with trigger: 'incremental').
    409 Conflict if a build is already in progress.
    404 if no blueprints exist (incremental requires existing corpus).
    """
    coordinator = request.app.state.blueprint_trigger_coordinator
    job_id = str(uuid.uuid4())

    result = await coordinator.try_claim(project_id, "incremental", job_id)
    if not result.claimed:
        if result.coalesced:
            return {"job_id": result.job_id, "status": "already_in_progress", "mode": "incremental"}
        raise HTTPException(status_code=409, detail=f"Blueprint {result.conflict_mode} already in progress")

    # Guard: incremental requires existing corpus
    existing = await asyncio.to_thread(repo.list_by_project, project_id, active_only=True)
    if not existing:
        await coordinator.release(project_id, job_id)  # release the claim
        raise HTTPException(status_code=404, detail="No blueprints found. Use /rebuild first.")

    job = await job_service.enqueue(
        agent_id="blueprinter",
        message=f"[trigger: incremental]\nReason: manual update request",
        ...
    )
    return {"job_id": job.job_id, "status": "enqueued", "mode": "incremental"}
```

### 4.3 — `/initialize` → Alias for `/rebuild`

```python
@router.post("/initialize", response_model=dict, status_code=202, deprecated=True)
async def initialize_project_blueprints(request: Request, project_id: str):
    """DEPRECATED — use /rebuild instead. Alias for backward compatibility."""
    return await rebuild_project_blueprints(request, project_id)
```

### 4.4 — Repurpose `/scan` (via C7 Coordinator)

```python
@router.post("/scan", response_model=dict, status_code=202)
async def trigger_blueprint_scan(request: Request, project_id: str):
    """Trigger an immediate smart blueprint scan (via coordinator)."""
    result = await manager._blueprint_scan_service.scan_project_now(project_id)
    return {"status": result.trigger or "skip", "reason": result.reason}
```

The `scan_project_now()` method internally uses the coordinator's `try_claim()`.

## Dependencies

- **Phase 3** must be complete — the C7 trigger coordinator must exist.
- **Phase 2** transitively.

## Testing Approach

1. **API test — `/rebuild` on empty corpus:** POST → 202. Verify blueprinter job enqueued.
2. **API test — `/rebuild` on existing corpus:** POST → 202 (not 409).
3. **API test — `/update` on empty corpus:** POST → 404 (and claim released).
4. **API test — `/update` on existing corpus:** POST → 202.
5. **API test — C7 concurrent guard:** Fire two concurrent `/rebuild` calls → one 202, one 409 (or coalesced 202).
6. **API test — C7 cross-mode conflict:** Active rebuild → `/update` → 409.
7. **API test — `/initialize` alias:** POST → 202.
8. **API test — `/scan` smart trigger:** Empty → 202 rebuild. Non-empty + pending → 202 incremental. Non-empty + no pending → 200 skip.

## Risks

- **R8 (breaking existing callers):** `/initialize` alias ensures backward compatibility.
- **Claim release on error:** If `/update` returns 404 after claiming, the claim must be released (shown in code above).

## Exit Criterion

`/rebuild`, `/update`, `/scan` endpoints work via the C7 coordinator. `/initialize` is a working alias. No concurrent builds for the same project. All API tests pass.

---

# Phase 5: Blueprinter Agent + Safety Controls (C1 Absorbed)

## Objective

Transform the blueprinter from a single-trigger agent into a skill-driven, two-workflow (rebuild + incremental) system. **C1 controls absorbed into this phase:**
- **Compare/stage/publish rebuild semantics** — compare existing blueprints before overwrite
- **Versioned structured worker reports** — skill files define the report format from the start
- **Decision-model evaluation** — decide-changes skill notes model tier (`balanced` if available, else `quick`)

This phase can start as soon as **Phase 2** is complete — it needs the pending table schema + C3 claim/ack contract but NOT Phase 3 or Phase 4.

## Files Touched

| File | Change Type |
|------|-------------|
| `agents/blueprinter/skill-set.yaml` (NEW) | **Create** — 4 skill definitions |
| `agents/blueprinter/skills-template/explore-for-rebuild.md` (NEW) | **Create** — worker skill with structured report format |
| `agents/blueprinter/skills-template/explore-for-incremental.md` (NEW) | **Create** — worker skill with structured report format |
| `agents/blueprinter/skills-template/build-blueprint.md` (NEW) | **Create** — worker skill with structured report format |
| `agents/blueprinter/skills-template/decide-changes.md` (NEW) | **Create** — blueprinter skill with model tier note |
| `agents/blueprinter/soul.md` | **Rewrite** — two-workflow identity + compare/stage/publish |
| `agents/blueprinter/workflow.md` | **Rewrite** — rebuild + incremental fan-out/fan-in + C3 claim/ack + structured reports |
| `agents/blueprinter/rule.md` | **Modify** — trigger handling, fan-out rules, compare/stage/publish rules |
| `agents/blueprinter/meta.json` | **Modify** — add `skill_injection: true` |

## Key Changes

### 5.1 — `skill-set.yaml`

```yaml
agent_id: blueprinter
skills:
  - name: explore-for-rebuild
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "Worker skill: explore project at overview level for full rebuild — directories, module scope, entry points, patterns, dependencies. Returns STRUCTURED REPORT."

  - name: explore-for-incremental
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "Worker skill: explore specific changed areas from pending-experience records — what changed, what blueprints are affected, what's stale. Returns STRUCTURED REPORT."

  - name: build-blueprint
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "Worker skill: craft concise blueprint content (200-500 words) from exploration data. Returns STRUCTURED REPORT with name, content, file_refs, trigger_queries."

  - name: decide-changes
    version: "1.0.0"
    auto_load: false
    category: planning
    description: "Blueprinter skill: analyze structured exploration reports, decide create/update/disable/no-op. Notes model tier: 'balanced' if available, else 'quick'."
```

### 5.2 — C1: Versioned Structured Worker Report Format

ALL worker skills return reports in this exact format. The blueprinter's fan-in parses this structure:

```markdown
## Worker Report

### Summary
[1-2 sentence overview of findings]

### Areas Found
- **[Area Name]** — [1-sentence purpose]
  - Key files: `path/to/file.py`, `path/to/other.py`
  - Patterns: [repository, factory, etc.]
  - Dependencies: [internal/external]

### Blueprint Recommendations
- **CREATE**: [area name] — [why this area needs a blueprint]
- **UPDATE**: [area name] — [what changed, what's stale]
- **NO-OP**: [area name] — [why no change needed]

### File References (Verified)
- `path/to/file.py` — [what it contains]
- `path/to/module/` — [directory purpose]

### Confidence: [high/medium/low]
```

This structured format replaces free-form text (addresses C1 "versioned structured worker reports" and the old Open Question #6).

### 5.3 — Skill File: `explore-for-rebuild.md`

```markdown
---
version: 1.0.0
category: execution
auto_load: false
---

# Explore for Rebuild

You are a **worker** loaded with the explore-for-rebuild skill. Your task is to explore a specific group of project directories at an overview level and report a **structured architectural summary**.

## Input
You receive a directory group assignment from the blueprinter. Explore ONLY those directories.

## What to Report
Use the **Worker Report** format (see skill-set). For each directory/module:
1. **Module purpose** — 1-2 sentences
2. **Key files** — entry points, main classes/functions (with file paths)
3. **Patterns** — architectural patterns observed
4. **Dependencies** — internal and external

## Constraints
- Do NOT read every file. Sample key files (entry points, __init__.py, main classes).
- Skip generated/build directories (node_modules, __pycache__, .git, dist, build).
- Report using the STRUCTURED format. Keep total output under 500 words.
- You do NOT write blueprints — you report findings for the blueprinter to decide.
- Verify all file paths you reference actually exist.
```

### 5.4 — Skill File: `explore-for-incremental.md`

```markdown
---
version: 1.0.0
category: execution
auto_load: false
---

# Explore for Incremental

You are a **worker** loaded with the explore-for-incremental skill. Your task is to explore specific areas that have changed (from pending-experience records) and determine how blueprints should be updated.

## Input
You receive pending-experience records (full text via C3 `get_pending_records`) + the current blueprint content for your assigned area.

## What to Report
Use the **Worker Report** format (see skill-set):
1. **What changed** — summarize the pending records' architectural impact
2. **Affected blueprints** — which existing blueprints need updating and why
3. **Stale references** — file paths in current blueprints that no longer exist or have moved
4. **New areas** — architectural areas not yet covered by any blueprint

## Constraints
- Focus on the pending records' topics. Do NOT re-scan the entire project.
- Report using the STRUCTURED format. Keep total output under 500 words.
- You do NOT write blueprints — you report change analysis for the blueprinter.
```

### 5.5 — Skill File: `build-blueprint.md`

```markdown
---
version: 1.0.0
category: execution
auto_load: false
---

# Build Blueprint

You are a **worker** loaded with the build-blueprint skill. Your task is to craft a single concise blueprint from exploration data.

## Input
You receive: (a) exploration report for one area, (b) the area name/scope assignment.

## Output Format
Return a **Worker Report** with:
1. **Name** — short, descriptive (e.g., "Authentication Layer")
2. **Content** — 200-500 words of declarative architectural knowledge
3. **File references** — verified file paths (no paths you haven't confirmed exist)
4. **Trigger queries** — 3-10 diverse natural-language queries that would match this blueprint

## Constraints
- Content is declarative (facts about the architecture), not imperative (instructions).
- Never duplicate system-prompt material or generic LLM knowledge.
- Verify file references against the actual directory structure.
- 200-500 words for area blueprints.
```

### 5.6 — C1: Skill File: `decide-changes.md` (with Model Tier Note)

```markdown
---
version: 1.0.0
category: planning
auto_load: false
---

# Decide Changes

You are the **blueprinter** using the decide-changes skill during fan-in. You analyze structured worker reports and decide the final set of blueprint actions.

## Model Tier Note (C1)
The decide phase requires good judgment. Use the **'balanced'** model tier if available. If 'balanced' is not configured, fall back to **'quick'** and note the downgrade in the report for future tuning.

## Decision Framework
For each reported area/change:
1. **Create** — a durable architectural concern not covered by any existing blueprint
2. **Update** — an existing blueprint with confirmed drift (file paths moved, patterns changed)
3. **Disable** — a blueprint with persistent staleness or confirmed irrelevance (soft-retire)
4. **No-op** — evidence is insufficient or current content remains accurate

## Priority Rules
- Core.md is highest priority — review first whenever any drift is present.
- Prefer no-op over speculative revision. Missing evidence is not evidence of drift.
- High-value = stable architectural knowledge that recurs across multiple tasks.
- Low-value = implementation detail that changes frequently (skip or split into area).

## Manual Content Protection
When an existing blueprint has `source="manual"`, require a higher confidence threshold before replacing its content.

## Compare/Stage/Publish Semantics (C1)
During rebuild:
1. **Compare** — stage new blueprint content against existing. Diff content + file refs.
2. **Stage** — prepare the updated blueprint as a new version (status='draft').
3. **Publish** — flip status to 'published' and set old version to is_active=False.
This ensures partial writes during a crash don't corrupt published blueprints.
```

### 5.7 — Rewrite `soul.md`

Key changes:
- **Remove** single "maintenance run" framing. Replace with two workflows: **Rebuild** and **Incremental**.
- **Remove** "Bootstrap Path" — first build IS a rebuild.
- **Add** skill-driven identity: "I am a skill-driven blueprint maintenance agent."
- **Add** fan-out/fan-in coordination model (max 4 workers, structured reports).
- **Add** compare/stage/publish rebuild semantics (C1).
- **Add** C3 pending-batch contract interaction: `claim_batch` → process → `acknowledge_batch`.
- **Keep** core.md priority rule, manual-content protection, fire-and-forget discipline, evidence-driven posture.

### 5.8 — Rewrite `workflow.md`

**Rebuild Workflow:**
```
Phase 1 — EXPLORE (fan-out, max 4 workers)
  1. List top-level directories → split into ≤4 groups
  2. For each group: spawn worker with load_skill="explore-for-rebuild"
  3. Wait for all worker reports (fan-in) — workers return STRUCTURED reports

Phase 1 — DECIDE (fan-in, blueprinter alone)
  1. Review all structured worker reports using decide-changes skill
  2. Decide: which blueprints to create/update/disable
  3. Assign one blueprint per area to craft in Phase 2

Phase 2 — CRAFT (fan-out, max 4 workers)
  1. For each blueprint: spawn worker with load_skill="build-blueprint"
  2. Wait for all worker reports (fan-in) — structured blueprint drafts

Phase 2 — SAVE (fan-in, blueprinter alone) — COMPARE/STAGE/PUBLISH (C1)
  1. For each crafted blueprint:
     a. COMPARE against existing blueprint (if any) — content diff
     b. STAGE as new version (status='draft')
     c. PUBLISH: set status='published', old version is_active=False
  2. Rate-limit every write
  3. Send heartbeat to trigger coordinator (C7)
  4. Report outcome
```

**Incremental Workflow:**
```
Phase 0 — CLAIM PENDING (C3 contract)
  1. Generate run_token
  2. claim_batch(project_id, batch_size=50, run_token) → list[record_ids]
  3. If empty → exit (nothing to update)
  4. If corpus empty or bare-core → release claim, switch to rebuild workflow
  5. get_pending_records(record_ids) → full text

Phase 1 — EXPLORE (fan-out, max 4 workers)
  1. Split pending records into groups by topic/module similarity
  2. For each group: spawn worker with load_skill="explore-for-incremental"
     Include pending texts + current blueprint content
  3. Wait for all worker reports (fan-in)

Phase 1 — DECIDE (fan-in)
  1. Review structured reports using decide-changes skill

Phase 2 — CRAFT (fan-out, max 4 workers)
  1. For each blueprint to update: spawn worker with load_skill="build-blueprint"
  2. Wait for all worker reports (fan-in)

Phase 2 — SAVE + ACKNOWLEDGE (fan-in)
  1. Save updated blueprints via compare/stage/publish (C1)
  2. acknowledge_batch(run_token, record_ids) → marks records as applied
  3. Send heartbeat
  4. Report outcome
```

### 5.9 — Modify `rule.md`

Key changes:
- **Remove** rule 7 (self-re-enqueue). Daily scan is daemon-side (Phase 3).
- **Remove** references to `post-experience` trigger (replaced by pending queue).
- **Update** trigger handling: accepted triggers are `rebuild` and `incremental` only.
- **Add** fan-out rules: max 4 concurrent workers, one skill per worker, structured reports.
- **Add** C3 claim/acknowledge rules: always claim before processing, always acknowledge after save, never process without claiming.
- **Add** compare/stage/publish rules: never overwrite a published blueprint directly; always stage then publish.
- **Add** heartbeat rule: send heartbeat to coordinator every 2 minutes during build.
- **Keep** rate-limit-every-write, preserve-manual-edits, protect-core.md, enforce-word-limits, never-duplicate-system-prompt, fire-and-forget discipline.

### 5.10 — Modify `meta.json`

```json
{
  "id": "blueprinter",
  "name": "Blueprinter",
  "description": "Skill-driven blueprint maintenance agent with rebuild and incremental workflows",
  "icon": "📐",
  "color": "accent-teal",
  "version": "2.0.0",
  "llm_model": "quick",
  "blueprint_inactive": true,
  "skill_injection": true,
  "tools": {
    "allow": ["blueprint", "knowledge", "filesystem", "time", "self", "help", "instance"]
  },
  "team_members": ["worker"]
}
```

**Critical:** `skill_injection: true` is required for skill seeding.

The blueprinter needs tools for the C3 pending-batch contract: `blueprint_claim_pending`, `blueprint_acknowledge_pending`, `blueprint_get_pending_records`. These are added as blueprint tool wrappers over the Phase 2 repository methods.

## Dependencies

- **Phase 2** must be complete — pending table + C3 claim/ack contract must exist.
- **No dependency on Phase 3 or Phase 4.**

## Testing Approach

1. **Skill seeding test:** Start daemon → verify 4 skills seeded into `skill_bank`.
2. **Load-skill test:** Spawn worker with `load_skill="explore-for-rebuild"` → verify skill content in context.
3. **Structured report format test:** Verify worker output matches the Worker Report structure.
4. **Prompt review:** Review soul.md/workflow.md/rule.md against `docs/agent-prompt-writing-guide.md`.
5. **Dry-run rebuild:** Trigger rebuild on small project → verify fan-out/fan-in + compare/stage/publish.
6. **Dry-run incremental:** Seed pending → claim_batch → trigger incremental → verify claim/ack lifecycle.
7. **C1 compare/stage/publish test:** Trigger rebuild on project with existing blueprints → verify old blueprints are compared, new versions staged, then published (old versions is_active=False).

## Risks

- **R4 (skills-template vs skills/):** Follow `skills-template/` convention.
- **R6 (rate limits):** Workers explore only. Blueprinter writes sequentially.
- **C1 compare/stage/publish complexity:** The staging flow adds complexity but ensures crash safety. Test thoroughly.

## Exit Criterion

4 skill files exist with structured report format. soul.md/workflow.md/rule.md reflect two-workflow model with C3 claim/ack and C1 compare/stage/publish. Manual rebuild + incremental dry-runs produce blueprints via fan-out/fan-in. All prompt files pass the writing-guide checklist.

---

# Phase 6: Frontend

## Objective

Update the Blueprint UI from a single "Initialize" button to a dual-mode system: "Rebuild Blueprints" when the corpus is empty, "Update Blueprints" (with a popup choosing Incremental vs Full Rebuild) when the corpus is non-empty.

## Files Touched

| File | Change Type |
|------|-------------|
| `frontend/src/app/services/blueprint.service.ts` | **Modify** — add `rebuild()` and `update()` methods; deprecate `initialize()` |
| `frontend/src/app/pages/blueprint/blueprint.component.ts` | **Modify** — dual-mode button logic + popup |
| `frontend/src/app/pages/blueprint/blueprint.component.html` | **Modify** — button + popup template |
| `frontend/src/app/pages/blueprint/blueprint.component.scss` | **Modify** — popup styling |
| `frontend/src/app/models/blueprint.model.ts` | **Modify** (if needed) |

## Key Changes

### 6.1 — Blueprint Service: `rebuild()` and `update()` Methods

```typescript
rebuild(projectId: string): Observable<{ job_id: string; status: string; mode: string }> {
  this.loading.set(true);
  return this.http
    .post<{ job_id: string; status: string; mode: string }>(
      `${this.baseUrl(projectId)}/rebuild`, {}
    )
    .pipe(
      catchError((err) => {
        if (err?.status === 409) {
          this.error.set('Blueprint rebuild already in progress');
          return throwError(() => new Error('Rebuild already in progress'));
        }
        this.error.set(err?.message || 'Failed to rebuild blueprints');
        return throwError(() => err);
      }),
      finalize(() => this.loading.set(false)),
    );
}

updateBlueprints(projectId: string): Observable<{ job_id: string; status: string; mode: string }> {
  this.loading.set(true);
  return this.http
    .post<{ job_id: string; status: string; mode: string }>(
      `${this.baseUrl(projectId)}/update`, {}
    )
    .pipe(
      catchError((err) => {
        if (err?.status === 404) {
          this.error.set('No blueprints found. Use Rebuild first.');
          return throwError(() => new Error('No blueprints to update'));
        }
        if (err?.status === 409) {
          this.error.set('Blueprint update already in progress');
          return throwError(() => new Error('Update already in progress'));
        }
        this.error.set(err?.message || 'Failed to update blueprints');
        return throwError(() => err);
      }),
      finalize(() => this.loading.set(false)),
    );
}
```

### 6.2 — Component: Dual-Mode Button Logic

```typescript
readonly showRebuildButton = computed(() => this.blueprints().length === 0);
readonly showUpdateButton = computed(() => this.blueprints().length > 0);
readonly showUpdatePopup = signal(false);

onRebuildClick(): void {
  this.service.rebuild(this.currentProjectId()!).subscribe({
    next: () => {
      this.snackBar.open('Blueprint rebuild started...', 'Close', { duration: 5000 });
      this.startRebuildPolling(this.currentProjectId()!);
    },
    error: (err) => this.showMutationError(err, 'rebuild'),
  });
}

onUpdateClick(): void { this.showUpdatePopup.set(true); }

onIncrementalUpdate(): void {
  this.showUpdatePopup.set(false);
  this.service.updateBlueprints(this.currentProjectId()!).subscribe({
    next: () => {
      this.snackBar.open('Incremental update started...', 'Close', { duration: 5000 });
      this.startRebuildPolling(this.currentProjectId()!);
    },
    error: (err) => this.showMutationError(err, 'update'),
  });
}

onFullRebuild(): void {
  this.showUpdatePopup.set(false);
  this.service.rebuild(this.currentProjectId()!).subscribe({
    next: () => {
      this.snackBar.open('Full rebuild started...', 'Close', { duration: 5000 });
      this.startRebuildPolling(this.currentProjectId()!);
    },
    error: (err) => this.showMutationError(err, 'rebuild'),
  });
}
```

### 6.3 — Polling for Completion

```typescript
private rebuildPollingSub?: Subscription;

startRebuildPolling(projectId: string): void {
  this.rebuildPollingSub?.unsubscribe();
  const poll$ = timer(0, 10_000).pipe(
    take(30),
    switchMap(() => this.service.list(projectId)),
  );
  this.rebuildPollingSub = poll$.subscribe({
    next: (blueprints) => this.blueprints.set(blueprints),
    complete: () => this.snackBar.open('Blueprint refresh complete', 'Close', { duration: 3000 }),
  });
}
```

### 6.4 — Template: Popup

```html
@if (showRebuildButton()) {
  <button mat-raised-button color="primary" (click)="onRebuildClick()">
    <mat-icon>refresh</mat-icon> Rebuild Blueprints
  </button>
}

@if (showUpdateButton()) {
  <button mat-raised-button color="accent" (click)="onUpdateClick()">
    <mat-icon>update</mat-icon> Update Blueprints
  </button>
}

@if (showUpdatePopup()) {
  <div class="update-popup-overlay" (click)="showUpdatePopup.set(false)">
    <div class="update-popup" (click)="$event.stopPropagation()">
      <h3>Update Blueprints</h3>
      <button mat-button (click)="onIncrementalUpdate()">
        <mat-icon>auto_fix_high</mat-icon>
        Incremental Update
        <small>Process recent changes</small>
      </button>
      <button mat-button (click)="onFullRebuild()">
        <mat-icon>construction</mat-icon>
        Full Rebuild
        <small>Re-scan entire project</small>
      </button>
      <button mat-button (click)="showUpdatePopup.set(false)">Cancel</button>
    </div>
  </div>
}
```

## Dependencies

- **Phase 4** must be complete — endpoints must exist.
- **No dependency on Phase 5.**

## Testing Approach

1. **Component test — empty corpus:** `blueprints()` = `[]` → "Rebuild" visible, "Update" not.
2. **Component test — non-empty:** `[mockBlueprint]` → "Update" visible, "Rebuild" not.
3. **Component test — popup:** Click "Update" → popup appears.
4. **Integration test — rebuild call:** Click "Rebuild" → HTTP POST to `/rebuild` → snackbar → polling.
5. **Integration test — 409 handling:** Mock 409 → "already in progress" error.
6. **E2E (manual):** Load page → click Rebuild → wait for polling → verify new blueprints.

## Risks

- **Polling overhead:** 10s × 30 = 30 requests. Acceptable for background maintenance.
- **Inline overlay vs Material Dialog:** Inline for simplicity. Dialog in a polish pass.

## Exit Criterion

Frontend shows correct button mode. Popup works. Calls hit correct endpoints. Polling refreshes list. All component tests pass.

---

# Phase 7: Smart Daily Scan + End-to-End Testing

## Objective

Implement the smart daily scan trigger logic (deciding rebuild vs incremental vs skip based on corpus state + pending queue via the C7 coordinator), add a max-pending threshold as a safety valve, and run the **full end-to-end test suite** covering both workflows, the pending queue lifecycle (C3), the trigger coordinator (C7), context injection persistence (C10), crash recovery, queue concurrency, and the G5–G9 fixes — against **both PostgreSQL and SQLite**.

## Files Touched

| File | Change Type |
|------|-------------|
| `daemon/services/blueprint_scan_service.py` | **Modify** (from Phase 3) — add full smart trigger logic + max-pending threshold + coordinator integration |
| `tests/test_blueprint_e2e.py` (NEW or existing) | **Create/Modify** — comprehensive E2E test suite |

## Key Changes

### 7.1 — Smart Daily Scan Logic (via C7 Coordinator)

The `BlueprintScanService._scan_project()` method gets the full decision logic:

```python
async def _scan_project(self, project_id: str) -> None:
    """Smart scan: decide rebuild / incremental / skip based on state.
    All enqueues go through the C7 trigger coordinator.
    """
    blueprints = self._blueprint_repo.list_by_project(project_id, active_only=True)
    pending_count = self._blueprint_pending_repo.count_pending(project_id)

    # State 1: Empty corpus → REBUILD
    if len(blueprints) == 0:
        await self._enqueue(project_id, "rebuild", reason="empty corpus")
        return

    # State 2: Only bare core.md → REBUILD
    if len(blueprints) == 1 and blueprints[0].kind == "core":
        await self._enqueue(project_id, "rebuild", reason="bare core only")
        return

    # State 3: Has blueprints + pending → INCREMENTAL
    if pending_count > 0:
        await self._enqueue(project_id, "incremental", reason=f"{pending_count} pending updates")
        return

    # State 3b (safety valve): Max-pending threshold
    MAX_PENDING_FORCE = 50  # configurable
    if pending_count >= MAX_PENDING_FORCE:
        await self._enqueue(project_id, "incremental", reason=f"max-pending threshold ({pending_count})")
        return

    # State 4: Has blueprints + no pending → SKIP
    logger.debug(f"Blueprint scan skip for project {project_id}: no pending updates")
```

### 7.2 — `_enqueue()` Helper (via C7 Coordinator)

```python
async def _enqueue(self, project_id: str, trigger: str, reason: str) -> None:
    """Enqueue a blueprinter job via the C7 trigger coordinator."""
    try:
        job_id = str(uuid.uuid4())
        result = await self._coordinator.try_claim(project_id, trigger, job_id)
        if not result.claimed:
            logger.info(f"Blueprint scan coalesced for {project_id}: existing build active")
            return  # coalesced or conflict — don't enqueue duplicate

        message = f"[trigger: {trigger}]\nReason: {reason}"
        if trigger == "incremental":
            pending = self._blueprint_pending_repo.list_pending(project_id, limit=50)
            summaries = [f"- [{p.source}] {p.text[:200]}" for p in pending]
            message += f"\n\nPending changes ({len(pending)}):\n" + "\n".join(summaries)

        await self._job_queue_service.enqueue(
            agent_id="blueprinter",
            message=message,
            source="daily-scan",
            project_id=project_id,
            priority=9,
            queue_id=bg_queue.queue_id,
            metadata={"trigger": trigger, "source": "daily-scan", "reason": reason, "run_token": job_id},
        )
    except Exception as e:
        logger.warning(f"Blueprint scan enqueue failed for {project_id}: {e}")
```

### 7.3 — End-to-End Test Suite

Create `tests/test_blueprint_e2e.py`. **All tests run against BOTH PostgreSQL and SQLite.**

**Test 1: Rebuild Flow (Empty → Full Corpus)**
```
1. Ensure empty corpus for test project
2. POST /rebuild
3. Poll blueprint list until core + area blueprints appear
4. Assert: ≥1 core (kind=core), ≥1 area (kind=area)
5. Assert: all blueprints have status='published'
6. Assert: all blueprints have non-empty trigger_queries
7. Assert: compare/stage/publish worked (no old drafts left active)
```

**Test 2: Incremental Flow (Existing + Pending → Updated)**
```
1. Ensure corpus has core + 1 area blueprint
2. Insert 3 pending records via experience()/project_history_add()
3. POST /update
4. Poll until updated_at changes on area blueprint
5. Assert: pending records acknowledged (status='applied', processed_at set)
6. Assert: count_pending == 0 (no available/retryable records)
7. Assert: area blueprint content reflects the pending changes
```

**Test 3: C3 Pending-Batch Lifecycle**
```
1. Call experience() 5 times → 5 pending records (status='available')
2. claim_batch(run_token_A, batch_size=3) → 3 records claimed
3. Assert: 3 records status='claimed', claimed_by_token=run_token_A
4. Assert: 2 records still status='available'
5. acknowledge_batch(run_token_A, [id1, id2]) → 2 applied, 1 still claimed
6. acknowledge_batch(run_token_B, [id3]) → FAILS (wrong token)
7. mark_retryable() on id3 (simulate lease timeout) → status='retryable'
8. claim_batch(run_token_C) → id3 picked up (retryable → claimed)
```

**Test 4: C7 Trigger Coordinator**
```
1. try_claim(proj, "rebuild", job_A) → claimed=True
2. try_claim(proj, "rebuild", job_B) → claimed=False, coalesced=True, job_id=job_A
3. try_claim(proj, "incremental", job_C) → claimed=False, conflict_mode='rebuild'
4. release(proj, job_A_token) → released
5. try_claim(proj, "incremental", job_C) → claimed=True
6. Simulate crash (no release) → reconcile_on_startup() → lease released
```

**Test 5: G5 Daily Scan (Smart Trigger, Feature-Flagged)**
```
1. auto_rebuild_enabled=False → scan does nothing
2. Set True
3. State: empty corpus → scan → rebuild job enqueued (via coordinator)
4. State: core only → scan → rebuild job enqueued
5. State: core + areas + pending → scan → incremental job enqueued
6. State: core + areas + no pending → scan → skip (no job)
```

**Test 6: G6 BM25 Single-Candidate** (verify in E2E context)
```
1. Create 1 area blueprint with content matching a known query
2. Call matcher.match() → area blueprint appears with score > 0.0
```

**Test 7: G7 One-Core Guard** (DB-level + auto-dedup)
```
1. Create core blueprint for project
2. Attempt concurrent second core → constraint violation (exactly 1 succeeds)
3. Pre-seed 2 active cores → auto_dedup → 1 active, 1 soft-disabled
4. get_core() returns exactly one
```

**Test 8: G8 Status Filtering**
```
1. Create published + draft area blueprints
2. Call matcher.match() → only published in results
```

**Test 9: API Backward Compatibility**
```
1. POST /initialize → 202 (alias works)
2. POST /scan on empty corpus → 202 with mode='rebuild'
```

**Test 10: C10 Context Injection (Full Live Path)** ← NEW, critical
```
1. Create a blueprint via the API (with trigger_queries)
2. Start an agent instance for that project
3. GET /messages → verify blueprint context message appears in response
4. Verify first-turn-only behavior:
   a. Send first user message → blueprint context IS present
   b. Send second user message → blueprint context NOT re-injected
5. Verify checkpoint persistence:
   a. Save checkpoint
   b. Reload instance from checkpoint
   c. GET /messages → blueprint still in context
6. Run against BOTH PostgreSQL and SQLite
```

**Test 11: Crash-During-Rebuild Recovery** ← NEW
```
1. Trigger rebuild via coordinator
2. Simulate daemon crash mid-rebuild (kill process, no terminal release)
3. Restart daemon → startup reconciliation releases orphaned lease
4. Verify published blueprints are intact (compare/stage/publish ensured no corruption)
5. Trigger new rebuild → succeeds (claim acquired)
```

**Test 12: Queue Concurrency (4-Worker Fan-Out)** ← NEW
```
1. Trigger rebuild → blueprinter spawns 4 workers
2. Monitor system_background_queue: verify 4 workers enqueued
3. Assert: no queue limit exceeded (concurrency setting respected)
4. Assert: no deadlock (all 4 workers complete within timeout)
5. Assert: blueprinter fans in all 4 reports
```

**Test 13: Embedding Model Fingerprint** ← NEW
```
1. Create blueprint with embedding_model_fingerprint = "model-A"
2. Change embedding model to "model-B"
3. Trigger scan → matcher detects fingerprint mismatch
4. Verify stale vectors flagged for regeneration
5. After regeneration → fingerprints match new model
```

**Test 14: Full Regression**
```
Run entire existing blueprint test suite → 0 failures
```

## Dependencies

- **ALL prior phases** (2–6) must be complete.

## Testing Approach

This phase IS the testing approach. Additionally:
- Run the full suite against **PostgreSQL** (primary) AND **SQLite** (compatibility).
- Run with `MaintenanceService` interval set to 1 minute for test speed.
- Run with `auto_rebuild_enabled=True` for scan tests, `False` for flag-gating tests.

## Risks

- **R5 (pending queue growth):** Max-pending threshold (50 records) forces incremental.
- **Test flakiness:** E2E tests with LLM-generated blueprints are non-deterministic. Assert on structure, not content.
- **Idle gate blocking scan:** `MaintenanceService` runs only when idle. Acceptable for best-effort background maintenance. Max-pending threshold provides safety valve.

## Exit Criterion

All 14 E2E tests pass against PostgreSQL AND SQLite. Smart daily scan correctly decides rebuild/incremental/skip. C3 pending-batch lifecycle verified. C7 coordinator verified (claim, coalesce, heartbeat, release, startup reconciliation). C10 context injection verified (live path + checkpoint persistence). Crash recovery verified. Queue concurrency verified. No regressions.

---

# Dependency Graph

```
                    ┌──────────────────────────────────────────┐
                    │       PHASE 2 (Data Layer + Safety)       │
                    │  Pending queue w/ C3 claim/ack SM          │
                    │  G7 DB-level unique index + auto-dedup     │
                    │  G6/G8 fixes                               │
                    │  C10 context-kind allowlist fix            │
                    │  embedding_model_fingerprint               │
                    └──────────┬──────────┬────────────────────┘
                               │          │
             ┌─────────────────┘          └─────────────────────┐
             ▼                                                   ▼
┌──────────────────────────────┐              ┌───────────────────────────────────┐
│  PHASE 3 (Services + C7)     │              │  PHASE 5 (Blueprinter + C1)        │
│  C7 trigger coordinator      │              │  Skills + two-workflow prompt       │
│  G5 scheduler (flag-gated)   │              │  Structured reports (C1)            │
│  experience/history hooks(C8)│              │  Compare/stage/publish (C1)         │
│  auto_rebuild_enabled flag   │              │  decide-changes model tier (C1)     │
│  Startup reconciliation      │              │  (parallel with Phase 3)            │
└──────────┬───────────────────┘              └──────────┬────────────────────────┘
           │                                              │
           ▼                                              │
┌──────────────────────────────┐                        │
│     PHASE 4 (API)            │                        │
│  /rebuild + /update + alias  │                        │
│  All via C7 coordinator      │                        │
└──────────┬──────┬────────────┘                        │
           │      │                                      │
           │      └────────────────────┐                 │
           ▼                           ▼                 │
┌──────────────────────────────┐  ┌───────────────────────────────────┐
│    PHASE 6 (Frontend)        │  │   (Phase 5 still running)           │
│  Dual-mode button + popup    │  │                                     │
└──────────┬───────────────────┘  └──────────┬────────────────────────┘
           │                                 │
           └───────────────┬─────────────────┘
                           ▼
            ┌──────────────────────────────────────────┐
            │  PHASE 7 (Smart Scan + E2E)               │
            │  C3 lifecycle + C7 coordinator tests      │
            │  C10 context injection E2E                │
            │  Crash recovery + queue concurrency       │
            │  Embedding fingerprint                    │
            │  PostgreSQL + SQLite                      │
            └──────────────────────────────────────────┘

            ┌──────────────────────────────────────────┐
            │  PHASE 8 (Optional — droppable)           │
            │  Telemetry/dashboards only                │
            └──────────────────────────────────────────┘
```

**Critical path:** Phase 2 → Phase 3 → Phase 4 → Phase 7

**Parallel tracks:**
- Phase 5 branches off Phase 2 (independent of Phase 3/4)
- Phase 6 branches off Phase 4 (independent of Phase 5)
- Phase 8 is fully optional and droppable

**Estimated effort (rough, for planning):**
- Phase 2: 2–3 days (data layer + C3 state machine + G7 DB index + C10 fix + unit tests)
- Phase 3: 3–4 days (C7 coordinator + G5 scheduler + C8 factory threading + hooks)
- Phase 4: 1 day (API endpoints via coordinator + tests)
- Phase 5: 2–3 days (4 skill files + structured reports + compare/stage/publish + prompt rewrite)
- Phase 6: 1–2 days (frontend button + popup + polling)
- Phase 7: 2–3 days (smart scan logic + 14-test E2E suite incl. C10, crash, concurrency)

**Total: ~11–16 days** (with Phase 5 and Phase 6 parallelizable, the critical path is ~8–11 days)

> Note: effort estimates increased from the original plan due to the C1 dissolution absorbing safety controls into earlier phases, the C3 state machine, C7 coordinator, and expanded E2E suite. This is expected — the original plan under-estimated by deferring safety controls to a separate Phase 8.

---

## C1 Dissolution Summary — Where Former Phase 8 Controls Now Live

| Former Phase 8 Control | New Location | Rationale |
|------------------------|-------------|-----------|
| Exact pending claims with `processed_at` soft-delete | **Phase 2** (§2.1, §2.2) | Pending queue table definition includes claim/ack state machine from the start |
| Durable lease + coalesced admission coordinator | **Phase 3** (§3.1) | Coordinator exists BEFORE any trigger surface goes live |
| Canonical write boundary | **Phase 1** (C5, another worker) | Already being added |
| Compare/stage/publish rebuild semantics | **Phase 5** (§5.6, §5.8) | Blueprinter compares before rebuild runs |
| Versioned structured worker reports | **Phase 5** (§5.2) | Skill files define report format from the start |
| PostgreSQL + SQLite integration tests | **Phase 7** (§7.3) | E2E testing (stays) |
| Decision-model evaluation | **Phase 5** (§5.6) | decide-changes skill notes model tier |
| Phase 8 itself | **Phase 8 (optional)** | Telemetry/dashboards only — droppable |

---

## Summary

This revised plan dissolves the former Phase 8 (C1) by absorbing its safety controls into the phases that need them — a trigger surface never goes live before its guard does. The pending-batch contract (C3) provides durable claim/acknowledge with crash recovery. The unified trigger coordinator (C7) prevents conflicting blueprinter jobs across all five trigger surfaces. The G7 guard is DB-level primary with auto-dedup (C6). The `project_history_add` hook is factory-threaded with observable failures (C8). The context-kind allowlist fix (C10) ensures blueprint injection persists in checkpoints. The blueprinter uses structured worker reports and compare/stage/publish semantics (C1). Phase 7's expanded E2E suite verifies all of this against both PostgreSQL and SQLite, including crash recovery and queue concurrency. Phase 8 survives only as an optional, droppable telemetry phase.
