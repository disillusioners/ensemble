# Phase 4 — Poignancy Background Worker

**File:** `v2-phase4-plan.md`  
**Depends on:** Phase 1 (`v2-phase1-plan.md`).  
**Flags / Decisions consumed:** `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`, DD-5, DD-20, V2-10, V2-14 (§3-A3), V2-16 (§3-A4), V2-33 (SF11).

---

## 1. Objective

Deliver the Asynchronous Poignancy Scorer for episodic memory:
1. Implement `MemoirPoignancyWorker` cloning the architectural pattern of `MemoirEmbeddingWorker` (lifespan task on `app.state`, dedicated executor `memoir-poignancy`, tick budget 20s, per-tick exception catching, call-time knob re-reading).
2. Implement `MemoirPoignancyService` to evaluate narrative importance (1–10) using the house LLM failover lane (`invoke_raw_with_failover`).
3. **The Importance Ruling (§3-A2 / Register #3):** `importance` remains **WRITE-FROZEN** (the authored, bounded 1–10 record at INSERT); the LLM score lands in the NEW `poignancy INTEGER NULL` column; all consumers read effective importance = `COALESCE(poignancy, importance)`. `RECORD_COLUMNS` pin stays green and untouched by construction; `MEMOIR_INSERT_COLUMNS` pin is deliberately amended to the new insert list as an evidence trail.
4. **The Backfill Ruling (§3-A3 / Register #5):** Pre-existing history is seeded as `poignancy_status = 'legacy'` at cutover using storage literal `'2026-11-09 00:00:00'` (SF11 / V2-33); automatic backfill of pre-cutover history is REJECTED; opt-in backfill is provided via `ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX = 0` (default disabled). Only new post-cutover episodes are automatically scored.
5. Scan for unscored episodes (`poignancy_status IS NULL AND poignancy_attempts < 5`), score them asynchronously, and atomically update `poignancy = score, poignancy_status = 'scored'`.
6. Classify failover errors cloning `classify_embed_failure` (401/403 -> immediate terminal + latch; non-transient -> terminal; transient -> attempt++).
7. Update `daemon/services/memoir_retrieval.py` (:39-42, :849, :953) to switch the recall ranker tie-break in `_rank` (:827) to effective importance `(row.poignancy if getattr(row, "poignancy", None) is not None else (row.importance or 0))` for ORDERING ONLY, with display-freeze preserving raw authored `importance` in projection and rendering (B6 / V2-41).
8. Explicit `poignancy_status` in `MEMOIR_INSERT_SQL` (`repository.py:386-398`): INSERT explicitly lists `poignancy_status` with literal `NULL` in VALUES clause, and purity pin (`test_memoir_write_purity_unit.py:154-176`) is deliberately amended as an evidence trail (B7 / V2-42).
9. Delete delta-notify coupling: reflection derives its trigger sum per tick from SQL; zero in-memory notification coupling (§3-A1 / Register #4).
10. Provide discrete kill-switch governance via `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED` following the DR-3 truth table, read from its own config section without calling the global knob resolver (N1).

---

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected Alternatives |
|----|----------|------------------------------|-----------------------|
| **V2-D 4.1** | The Importance Ruling (§3-A2 / Register #3): `importance` is **write-frozen** (preserving the authored record); LLM score lands in `poignancy INTEGER NULL`; all v2 consumers read `COALESCE(poignancy, importance)`. `RECORD_COLUMNS` pin stays green; `D2_1_SURFACE` pin amended deliberately with recorded rationale. | Overwriting `importance` in place REDs the frozen `RECORD_COLUMNS` purity pin and corrupts authored historical judgment. COALESCE preserves the blessed capability semantically without mutating historical records. | Overwriting `importance` in place. |
| **V2-D 4.2** | Clones `MemoirEmbeddingWorker` architecture: lifespan task on `app.state`, dedicated `ThreadPoolExecutor(max_workers=1, thread_name_prefix="memoir-poignancy")`, 30s timeout, attempt cap of 5, terminal markers (`terminal:<reason>`), structured pinnable tick lines. | Module docstring of `memoir_embedding_worker.py:1-78` represents the battle-tested house worker pattern. Dedicated thread pool avoids event-loop starvation. | Using `asyncio.to_thread` (forbidden in workers); OR unmanaged polling loops. |
| **V2-D 4.3** | Cutover-bounded legacy seed & opt-in backfill knob (§3-A3 / Register #5 / SF11): Pre-cutover episodes seeded `poignancy_status='legacy'` using `'2026-11-09 00:00:00'`. Full-corpus backfill rejected; opt-in backfill governed by `ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX=0` (default disabled). | Full-history backfill would drain ~43k episodes/day on large hosts, replace authored history with model guesses, and flood the reflection trigger sum with stale events. | Full-history automatic backfill on startup. |
| **V2-D 4.4** | Poignancy scoring prompt with integer scale 1–10: 1 = trivial pleasantry; 5 = standard operational; 8–9 = significant milestone; 10 = constitutional decree. Clamped strictly `1..10`. | Aligns with existing CHECK constraint `ck_memoir_episodes_importance`. | Floating-point score (violates integer schema contract). |
| **V2-D 4.5** | Delete delta-notify coupling (§3-A1 / Register #4): Poignancy worker does NOT push delta updates to reflection service. Reflection derives its trigger sum directly from SQL each tick: `SELECT COALESCE(SUM(COALESCE(poignancy, importance)), 0) ...`. | Delta-notifies double-count on re-scores, die on restart, and tightly couple service lifecycles. Derived sum is restart-durable and decoupled. | In-memory rolling counters; delta-notify pub/sub. |
| **V2-D 4.6** | Failover error classification (§3-A3): Clone `classify_embed_failure` (`memoir_embedding_worker.py:300-328`): 401/403 auth errors trigger immediate terminal status + latch; other non-transient errors terminalize; connection/timeout errors increment `poignancy_attempts`. | Blind retries on auth failure; OR swallowing all exceptions. |
| **V2-D 4.7** | Three-class numeric knob governance (§3-A4 / Register #12): `ENSEMBLE_MEMOIR_POIGNANCY_TICK_S`, `ENSEMBLE_MEMOIR_POIGNANCY_BATCH_CAP`, `ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX` validated via `_classify_numeric` before-validator. Never aborts boot. | Bare `Field(gt=...)` on numerics (pre-existing boot-abort defect, anomaly `33028b27`). | Bare `Field` validator killing daemon boot on unparseable numeric env vars. |
| **V2-D 4.8** | Recall tie-break consumer tasking & display-freeze (SF1 / B6 / V2-41): Switch ranker `_rank` (:827, not `_sort_and_rank`) tie-break comparator (:849) in `daemon/services/memoir_retrieval.py` to effective importance `(row.poignancy if getattr(row, "poignancy", None) is not None else (row.importance or 0))` for ORDERING ONLY. Projection in `_project` (:938) and rendering in `_render_episode_block` (`tools/memoir.py:495-508`) keep displaying authored `row.importance` unchanged, preserving byte-identical recall envelope text across all lanes. | Modifying projected `importance` to poignancy score (alters rendered recall text, breaking frozen v1 surfaces); sorting on raw importance alone. | Preserves frozen v1 recall text output byte-identical across all lanes while honoring the importance ruling for retrieval ordering. |
| **V2-D 4.9** | Dedicated worker config isolation (N1): `MemoirPoignancyWorker` reads its own isolated configuration section (`MemoirConfig.poignancy_enabled`), never cloning or calling the shared global knob resolver. **DIVERGENCE NOTE (implemented, phase-4 fix round, 2026-11-09 — wording superseded by implementation):** the shipped config is a SEPARATE `MemoirPoignancyConfig` BaseSettings section (`daemon/config.py:4084`) with its OWN env prefix `ENSEMBLE_MEMOIR_POIGNANCY_` and exactly four knobs (`enabled`, `backfill_max`, `tick_s`, `batch_cap`) — NOT a field on the v1 `MemoirConfig` section as the original wording implied. The worker resolves per-tick via `resolve_poignancy_settings` (`daemon/services/memoir_poignancy_service.py:342`, the ONE per-tick reader; the ENV tier is read live at call time and WINS over the boot-frozen section, so a mid-run kill-switch flip takes effect the next tick without restart) using the SHARED `_classify_enabled_flag` / `_classify_numeric` classifiers — never `resolve_memoir_knob`; the CLASSIFIER is shared, the RESOLVER is per-lane. This is STRICTER isolation than the row's original wording implied: the decision's isolation INTENT is unchanged and is now over-satisfied; the `MemoirConfig.poignancy_enabled` phrasing is superseded by the shipped separate-section shape. | Calling shared global knob resolver. | Preserves architectural boundary: poignancy worker is governed by its own discrete flag, preventing cross-subsystem interference. |

---

## 3. Domain Detail & Worker Architecture

### 3.1 Worker Lifecycle & Tick Loop
- **Tick Interval:** Every 20 seconds (`ENSEMBLE_MEMOIR_POIGNANCY_TICK_S = 20.0`).
- **Batch Cap:** Maximum 10 episodes per tick (`ENSEMBLE_MEMOIR_POIGNANCY_BATCH_CAP = 10`).
- **Knob Re-read (N1):** Every tick re-reads `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED` directly from `MemoirConfig.poignancy_enabled` (isolated config section), NEVER cloning or calling the shared global knob resolver. If OFF, logs one park message and sleeps; resumes execution when turned ON.
- **Structured Log Line:** `[MemoirPoignancy] pending=N scored=K failed=F terminal=T duration=X.Xs`.

### 3.2 Scoring Pipeline & Queue Scanning
1. Worker queries repository for batch:
   `SELECT * FROM memoir_episodes WHERE poignancy_status IS NULL AND poignancy_attempts < 5 ORDER BY created_at ASC LIMIT 10`.
   *(If `ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX > 0`, it additionally scans up to that limit from the `poignancy_status = 'legacy'` set).*
2. For each episode:
   - Formulates scoring prompt containing title, event time, body, and star tags.
   - Invokes LLM via `invoke_raw_with_failover`.
   - Parses integer score 1..10 via tolerant regex / JSON extraction; clamps to `[1, 10]`.
   - On success: updates `poignancy = score, poignancy_status = 'scored'`.
   - On failure: classifies error via `classify_poignancy_failure`. Increments `poignancy_attempts += 1`. If attempts reach 5 or error is non-transient, sets `poignancy_status = 'terminal:<reason>'`.
3. Reflection service automatically observes the updated score on its next tick via `COALESCE(poignancy, importance)`.
4. Retrieval service automatically ranks using updated score in `_rank` (:827) via effective importance `(row.poignancy if getattr(row, "poignancy", None) is not None else (row.importance or 0))` for ordering only, while `_project` (:938) keeps raw authored `importance` for display.

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T4.1** | Implement `MemoirPoignancyService` in `daemon/services/memoir_poignancy_service.py` to evaluate episodes, extract clamped 1–10 scores, and classify failover errors. | Scoring function evaluates text and returns integer 1..10; classifies 401/403 as terminal latch; handles parse errors gracefully. | `daemon/services/memoir_poignancy_service.py` | P1 | R3 | AC1 |
| **T4.2** | Add repository query methods in `daemon/repositories/memoir/repository.py`: `get_pending_poignancy_episodes()`, `record_poignancy_score(episode_id, score)` writing to `poignancy` column. Update `MEMOIR_INSERT_SQL` to explicitly list `poignancy_status` with literal `NULL` in VALUES clause and deliberately amend `MEMOIR_INSERT_COLUMNS` purity pin and `D2_1_SURFACE` pin as an evidence trail; `RECORD_COLUMNS` stays green and untouched (B7 / B3' / V2-42). | Queries fetch rows where `poignancy_status IS NULL`; updates write `poignancy` and `poignancy_status='scored'`, leaving `importance` write-frozen; insert statement explicitly specifies `poignancy_status`. | `daemon/repositories/memoir/repository.py`, `tests/unit/test_memoir_write_purity_unit.py` | P1 | — | AC2 |
| **T4.3** | Implement `MemoirPoignancyWorker` in `daemon/services/memoir_poignancy_worker.py` with lifespan hooks, dedicated thread pool `memoir-poignancy`, attempt caps, and isolated config reading (N1). | Worker runs periodically; reads `MemoirConfig.poignancy_enabled`; catches exceptions; parks when disabled; terminalizes failing rows after 5 attempts. | `daemon/services/memoir_poignancy_worker.py`, `daemon/api.py` | T4.1, T4.2 | R4 | AC3 |
| **T4.4** | Add configuration settings in `daemon/config.py`: `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`, `ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX=0`, `ENSEMBLE_MEMOIR_POIGNANCY_TICK_S`, `ENSEMBLE_MEMOIR_POIGNANCY_BATCH_CAP` (canonicalized under `ENSEMBLE_MEMOIR_` prefix, ND-19), using shared `_classify_numeric` before-validator. | Knob defaults to ON with warning; invalid values fail-closed; numeric values clamp without raising `ValidationError`. | `daemon/config.py`, `daemon/tools/memoir_knob.py` (touchpoint for tool-layer boot-state surfacing and status inspection only, not worker knob resolution; ND-18) | — | — | AC4 |
| **T4.5** | Implement cutover-bounded legacy backfill opt-in scan in `MemoirPoignancyWorker` when `ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX > 0`. | When enabled, worker pulls oldest N legacy episodes per pass; when 0 (default), legacy episodes are skipped. | `daemon/services/memoir_poignancy_worker.py` | T4.3 | — | AC5 |
| **T4.6** | Update `daemon/services/memoir_retrieval.py` (:39-42, :849, :953) to switch the recall ranker tie-break in `_rank` (:827) to effective importance, with display-freeze preserving raw `row.importance` for projection in `_project` (:938) and rendering (SF1 / B6 / V2-41). | `_rank` sorts equal fused-score episodes by `effective_importance DESC` for ordering only; `_project` (:938) retains authored `importance`; rendered episode text stays byte-identical. | `daemon/services/memoir_retrieval.py` | T4.2 | — | AC7 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | Hot-path non-blocking & explicit insert (B7): Calling `memoir_write` returns within standard turn latency; episode is inserted via `MEMOIR_INSERT_SQL` explicitly listing `poignancy_status` with literal `NULL` in VALUES clause; `importance=5`, `poignancy=NULL`. |
| **AC2** | Write-frozen `importance` & `poignancy` update (B7 / B3'): Worker processes pending episode; updates `poignancy` to LLM score (1..10) and `poignancy_status='scored'`; `importance` column remains completely UNCHANGED (asserted by green `RECORD_COLUMNS` purity pin; `MEMOIR_INSERT_COLUMNS` pin amendment is the documented evidence trail, not a violation). |
| **AC3** | Attempt caps and terminal markers: An episode that repeatedly triggers parse failures increments `poignancy_attempts`; after 5 failed attempts, status flips to `terminal:parse_failure` and worker advances past it. |
| **AC4** | Worker kill-switch isolation & isolated config (N1): Setting `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED="false"` parks the worker without affecting episodic writes or memory recall; worker reads its own config section, never cloning the global knob resolver; setting back to `"true"` resumes scoring backlog. |
| **AC5** | Legacy seed & backfill gating: Episodes before cutover carry `poignancy_status='legacy'`; with `ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX=0`, legacy episodes are never scored; setting `ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX=5` processes exactly 5 legacy episodes. |
| **AC6 (Mutation)** | Making `record_poignancy_score` write to `importance` flips AC2 RED (trips `RECORD_COLUMNS` pin); widening legacy seed predicate flips AC5 RED. |
| **AC7** | Poignancy recall tie-break consumer & display-freeze (SF1 / B6): In `memoir_recall`, ranker `_rank` (:827) sorts equal fused-score episodes by effective importance `(row.poignancy if row.poignancy is not None else row.importance) DESC` for ordering only. Rendered episode-block text in `_render_episode_block` displays authored `row.importance` byte-identically (`importance=<N>`) from `_project` (:938) projection. Mutation leg: if retrieval sorts by raw `row.importance` instead of effective importance, test fails RED; if rendered block text changes importance value, test fails RED. |

---

## 6. Test Packs

- `test/packs/memoir_v2_poignancy_service_test.sh`: Tests prompt formatting, score parsing, clamping 1..10, and error classification.
- `test/packs/memoir_v2_poignancy_worker_test.sh`: Tests batch queue polling, attempt increments, terminal status transitions, and thread pool isolation.
- `test/packs/memoir_v2_poignancy_lifecycle_test.sh`: Tests end-to-end integration: `memoir_write` -> background worker pick-up -> database update of `poignancy` -> write-frozen `importance` verified.
- `test/packs/memoir_v2_poignancy_retrieval_test.sh`: Tests `COALESCE(poignancy, importance)` tie-break ranking in `memoir_retrieval.py` and mutation leg.

---

## 7. Flag Consumption

- `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`: Discrete kill-switch for poignancy scoring.
- `ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX`: Opt-in backfill budget knob (default 0).
- **DD-5:** Asynchronous LLM importance scoring replacing v1 static default.
- **DD-20:** Hot append purity preserved.
- **V2-10 / §3-A2:** The Importance Ruling (write-frozen `importance`, new `poignancy` column, COALESCE consumers).
- **V2-14 / §3-A3:** Cutover-bounded legacy seed and opt-in backfill.
- **V2-16 / §3-A4:** Three-class numeric knob governance.
- **V2-33 / SF11:** Space-separated storage format `'2026-11-09 00:00:00'` cutover literal.
- **SF1:** Recall tie-break consumer tasking in `memoir_retrieval.py`.
