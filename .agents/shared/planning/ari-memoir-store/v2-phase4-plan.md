# Phase 4 — Poignancy Background Worker

**File:** `v2-phase4-plan.md`  
**Depends on:** Phase 1 (`v2-phase1-plan.md`).  
**Flags / Decisions consumed:** `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`, DD-5, DD-20, V2-10, V2-14 (§3-A3), V2-16 (§3-A4), V2-33 (SF11).

---

## 1. Objective

Deliver the Asynchronous Poignancy Scorer for episodic memory:
1. Implement `MemoirPoignancyWorker` cloning the architectural pattern of `MemoirEmbeddingWorker` (lifespan task on `app.state`, dedicated executor `memoir-poignancy`, tick budget 20s, per-tick exception catching, call-time knob re-reading).
2. Implement `MemoirPoignancyService` to evaluate narrative importance (1–10) using the house LLM failover lane (`invoke_raw_with_failover`).
3. **The Importance Ruling (§3-A2 / Register #3):** `importance` remains **WRITE-FROZEN** (the authored, bounded 1–10 record at INSERT); the LLM score lands in the NEW `poignancy INTEGER NULL` column; all consumers read effective importance = `COALESCE(poignancy, importance)`. `RECORD_COLUMNS` pin stays green by construction.
4. **The Backfill Ruling (§3-A3 / Register #5):** Pre-existing history is seeded as `poignancy_status = 'legacy'` at cutover using storage literal `'2026-11-09 00:00:00'` (SF11 / V2-33); automatic backfill of pre-cutover history is REJECTED; opt-in backfill is provided via `MEMOIR_POIGNANCY_BACKFILL_MAX = 0` (default disabled). Only new post-cutover episodes are automatically scored.
5. Scan for unscored episodes (`poignancy_status IS NULL AND poignancy_attempts < 5`), score them asynchronously, and atomically update `poignancy = score, poignancy_status = 'scored'`.
6. Classify failover errors cloning `classify_embed_failure` (401/403 -> immediate terminal + latch; non-transient -> terminal; transient -> attempt++).
7. Update `daemon/services/memoir_retrieval.py` (:39-42, :849, :953) to switch the recall ranker tie-break and projection to `COALESCE(poignancy, importance)` (SF1).
8. Delete delta-notify coupling: reflection derives its trigger sum per tick from SQL; zero in-memory notification coupling (§3-A1 / Register #4).
9. Provide discrete kill-switch governance via `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED` following the DR-3 truth table.

---

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected Alternatives |
|----|----------|------------------------------|-----------------------|
| **V2-D 4.1** | The Importance Ruling (§3-A2 / Register #3): `importance` is **write-frozen** (preserving the authored record); LLM score lands in `poignancy INTEGER NULL`; all v2 consumers read `COALESCE(poignancy, importance)`. `RECORD_COLUMNS` pin stays green; `D2_1_SURFACE` pin amended deliberately with recorded rationale. | Overwriting `importance` in place REDs the frozen `RECORD_COLUMNS` purity pin and corrupts authored historical judgment. COALESCE preserves the blessed capability semantically without mutating historical records. | Overwriting `importance` in place. |
| **V2-D 4.2** | Clones `MemoirEmbeddingWorker` architecture: lifespan task on `app.state`, dedicated `ThreadPoolExecutor(max_workers=1, thread_name_prefix="memoir-poignancy")`, 30s timeout, attempt cap of 5, terminal markers (`terminal:<reason>`), structured pinnable tick lines. | Module docstring of `memoir_embedding_worker.py:1-78` represents the battle-tested house worker pattern. Dedicated thread pool avoids event-loop starvation. | Using `asyncio.to_thread` (forbidden in workers); OR unmanaged polling loops. |
| **V2-D 4.3** | Cutover-bounded legacy seed & opt-in backfill knob (§3-A3 / Register #5 / SF11): Pre-cutover episodes seeded `poignancy_status='legacy'` using `'2026-11-09 00:00:00'`. Full-corpus backfill rejected; opt-in backfill governed by `MEMOIR_POIGNANCY_BACKFILL_MAX=0` (default disabled). | Full-history backfill would drain ~43k episodes/day on large hosts, replace authored history with model guesses, and flood the reflection trigger sum with stale events. | Full-history automatic backfill on startup. |
| **V2-D 4.4** | Poignancy scoring prompt with integer scale 1–10: 1 = trivial pleasantry; 5 = standard operational; 8–9 = significant milestone; 10 = constitutional decree. Clamped strictly `1..10`. | Aligns with existing CHECK constraint `ck_memoir_episodes_importance`. | Floating-point score (violates integer schema contract). |
| **V2-D 4.5** | Delete delta-notify coupling (§3-A1 / Register #4): Poignancy worker does NOT push delta updates to reflection service. Reflection derives its trigger sum directly from SQL each tick: `SELECT COALESCE(SUM(COALESCE(poignancy, importance)), 0) ...`. | Delta-notifies double-count on re-scores, die on restart, and tightly couple service lifecycles. Derived sum is restart-durable and decoupled. | In-memory rolling counters; delta-notify pub/sub. |
| **V2-D 4.6** | Failover error classification (§3-A3): Clone `classify_embed_failure` (`memoir_embedding_worker.py:300-328`): 401/403 auth errors trigger immediate terminal status + latch; other non-transient errors terminalize; connection/timeout errors increment `poignancy_attempts`. | Blind retries on auth failure; OR swallowing all exceptions. |
| **V2-D 4.7** | Three-class numeric knob governance (§3-A4 / Register #12): `MEMOIR_POIGNANCY_TICK_S`, `MEMOIR_POIGNANCY_BATCH_CAP`, `MEMOIR_POIGNANCY_BACKFILL_MAX` validated via `_classify_numeric` before-validator. Never aborts boot. | Bare `Field(gt=...)` on numerics (pre-existing boot-abort defect, anomaly `33028b27`). | Bare `Field` validator killing daemon boot on unparseable numeric env vars. |
| **V2-D 4.8** | Recall tie-break consumer tasking (SF1): Switch `_sort_and_rank` tie-break (:849) and `_apply_budget` projection (:953) in `daemon/services/memoir_retrieval.py` to `COALESCE(poignancy, importance)`. | Leaving recall sorting by raw `importance` alone. | Without updating retrieval, poignancy scores would be written but never used for retrieval ranking. |

---

## 3. Domain Detail & Worker Architecture

### 3.1 Worker Lifecycle & Tick Loop
- **Tick Interval:** Every 20 seconds (`MEMOIR_POIGNANCY_TICK_S = 20.0`).
- **Batch Cap:** Maximum 10 episodes per tick (`MEMOIR_POIGNANCY_BATCH_CAP = 10`).
- **Knob Re-read:** Every tick re-reads `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`. If OFF, logs one park message and sleeps; resumes execution when turned ON.
- **Structured Log Line:** `[MemoirPoignancy] pending=N scored=K failed=F terminal=T duration=X.Xs`.

### 3.2 Scoring Pipeline & Queue Scanning
1. Worker queries repository for batch:
   `SELECT * FROM memoir_episodes WHERE poignancy_status IS NULL AND poignancy_attempts < 5 ORDER BY created_at ASC LIMIT 10`.
   *(If `MEMOIR_POIGNANCY_BACKFILL_MAX > 0`, it additionally scans up to that limit from the `poignancy_status = 'legacy'` set).*
2. For each episode:
   - Formulates scoring prompt containing title, event time, body, and star tags.
   - Invokes LLM via `invoke_raw_with_failover`.
   - Parses integer score 1..10 via tolerant regex / JSON extraction; clamps to `[1, 10]`.
   - On success: updates `poignancy = score, poignancy_status = 'scored'`.
   - On failure: classifies error via `classify_poignancy_failure`. Increments `poignancy_attempts += 1`. If attempts reach 5 or error is non-transient, sets `poignancy_status = 'terminal:<reason>'`.
3. Reflection service automatically observes the updated score on its next tick via `COALESCE(poignancy, importance)`.
4. Retrieval service automatically ranks using updated score in `_sort_and_rank` via `COALESCE(poignancy, importance)`.

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T4.1** | Implement `MemoirPoignancyService` in `daemon/services/memoir_poignancy_service.py` to evaluate episodes, extract clamped 1–10 scores, and classify failover errors. | Scoring function evaluates text and returns integer 1..10; classifies 401/403 as terminal latch; handles parse errors gracefully. | `daemon/services/memoir_poignancy_service.py` | P1 | R3 | AC1 |
| **T4.2** | Add repository query methods in `daemon/repositories/memoir/repository.py`: `get_pending_poignancy_episodes()`, `record_poignancy_score(episode_id, score)` writing to `poignancy` column. Update `D2_1_SURFACE` pin. | Queries fetch rows where `poignancy_status IS NULL`; updates write `poignancy` and `poignancy_status='scored'`, leaving `importance` write-frozen. | `daemon/repositories/memoir/repository.py` | P1 | — | AC2 |
| **T4.3** | Implement `MemoirPoignancyWorker` in `daemon/services/memoir_poignancy_worker.py` with lifespan hooks, dedicated thread pool `memoir-poignancy`, and attempt caps. | Worker runs periodically; catches exceptions; parks when disabled; terminalizes failing rows after 5 attempts. | `daemon/services/memoir_poignancy_worker.py`, `daemon/api.py` | T4.1, T4.2 | R4 | AC3 |
| **T4.4** | Add configuration settings in `daemon/config.py`: `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`, `MEMOIR_POIGNANCY_BACKFILL_MAX=0`, etc., using shared `_classify_numeric` before-validator. | Knob defaults to ON with warning; invalid values fail-closed; numeric values clamp without raising `ValidationError`. | `daemon/config.py`, `daemon/tools/memoir_knob.py` | — | — | AC4 |
| **T4.5** | Implement cutover-bounded legacy backfill opt-in scan in `MemoirPoignancyWorker` when `MEMOIR_POIGNANCY_BACKFILL_MAX > 0`. | When enabled, worker pulls oldest N legacy episodes per pass; when 0 (default), legacy episodes are skipped. | `daemon/services/memoir_poignancy_worker.py` | T4.3 | — | AC5 |
| **T4.6** | Update `daemon/services/memoir_retrieval.py` (:39-42, :849, :953) to switch the recall ranker tie-break and projection to `COALESCE(poignancy, importance)` (SF1). | `_sort_and_rank` sorts equal fused-score episodes by `COALESCE(poignancy, importance) DESC`; projection carries `effective_importance`. | `daemon/services/memoir_retrieval.py` | T4.2 | — | AC7 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | Hot-path non-blocking: Calling `memoir_write` returns within standard turn latency; episode is inserted with `importance=5`, `poignancy=NULL`, and `poignancy_status=NULL`. |
| **AC2** | Write-frozen `importance` & `poignancy` update: Worker processes pending episode; updates `poignancy` to LLM score (1..10) and `poignancy_status='scored'`; `importance` column remains completely UNCHANGED (asserted by `RECORD_COLUMNS` purity pin). |
| **AC3** | Attempt caps and terminal markers: An episode that repeatedly triggers parse failures increments `poignancy_attempts`; after 5 failed attempts, status flips to `terminal:parse_failure` and worker advances past it. |
| **AC4** | Worker kill-switch isolation: Setting `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED="false"` parks the worker without affecting episodic writes or memory recall; setting back to `"true"` resumes scoring backlog. |
| **AC5** | Legacy seed & backfill gating: Episodes before cutover carry `poignancy_status='legacy'`; with `MEMOIR_POIGNANCY_BACKFILL_MAX=0`, legacy episodes are never scored; setting `MEMOIR_POIGNANCY_BACKFILL_MAX=5` processes exactly 5 legacy episodes. |
| **AC6 (Mutation)** | Making `record_poignancy_score` write to `importance` flips AC2 RED (trips `RECORD_COLUMNS` pin); widening legacy seed predicate flips AC5 RED. |
| **AC7** | Poignancy recall tie-break consumer (SF1): In `memoir_recall`, two episodes with identical lexical/semantic fused scores are ranked by `COALESCE(poignancy, importance) DESC`. Mutation leg: if retrieval sorts by raw `row.importance` instead of `COALESCE(poignancy, importance)`, test fails RED. |

---

## 6. Test Packs

- `test/packs/memoir_v2_poignancy_service_test.sh`: Tests prompt formatting, score parsing, clamping 1..10, and error classification.
- `test/packs/memoir_v2_poignancy_worker_test.sh`: Tests batch queue polling, attempt increments, terminal status transitions, and thread pool isolation.
- `test/packs/memoir_v2_poignancy_lifecycle_test.sh`: Tests end-to-end integration: `memoir_write` -> background worker pick-up -> database update of `poignancy` -> write-frozen `importance` verified.
- `test/packs/memoir_v2_poignancy_retrieval_test.sh`: Tests `COALESCE(poignancy, importance)` tie-break ranking in `memoir_retrieval.py` and mutation leg.

---

## 7. Flag Consumption

- `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`: Discrete kill-switch for poignancy scoring.
- `MEMOIR_POIGNANCY_BACKFILL_MAX`: Opt-in backfill budget knob (default 0).
- **DD-5:** Asynchronous LLM importance scoring replacing v1 static default.
- **DD-20:** Hot append purity preserved.
- **V2-10 / §3-A2:** The Importance Ruling (write-frozen `importance`, new `poignancy` column, COALESCE consumers).
- **V2-14 / §3-A3:** Cutover-bounded legacy seed and opt-in backfill.
- **V2-16 / §3-A4:** Three-class numeric knob governance.
- **V2-33 / SF11:** Space-separated storage format `'2026-11-09 00:00:00'` cutover literal.
- **SF1:** Recall tie-break consumer tasking in `memoir_retrieval.py`.
