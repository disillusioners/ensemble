# Phase 4 — Poignancy Background Worker

**File:** `v2-phase4-plan.md`  
**Depends on:** Phase 1 (`v2-phase1-plan.md`).  
**Flags / Decisions consumed:** `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`, DD-5, DD-20, V2-10.

---

## 1. Objective

Deliver the Asynchronous Poignancy Scorer for episodic memory:
1. Implement `MemoirPoignancyWorker` cloning the architectural pattern of `MemoirEmbeddingWorker` (dedicated executor, lifespan management, tick budget, per-tick exception catching, call-time knob re-reading).
2. Implement `MemoirPoignancyService` to evaluate narrative importance (1–10) using the house LLM failover lane (`invoke_raw_with_failover`).
3. Scan for unscored episodes (`poignancy_status IS NULL`), score them asynchronously, and atomically update `importance` and `poignancy_status = 'scored'`.
4. Preserve conversational latency: `memoir_write` writes importance default 5 immediately; scoring runs entirely in the background.
5. Feed scored poignancy into downstream consumers:
   - Recall tie-breaking (existing v1 hybrid retrieval).
   - Reflection trigger: accumulates importance towards the cumulative threshold (≥ 25).
   - Keepsake proposal eligibility: flags episodes with poignancy ≥ 8 for keepsake promotion review.
6. Provide discrete kill-switch governance via `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED` following the DR-3 truth table.

---

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected Alternatives |
|----|----------|------------------------------|-----------------------|
| **V2-D 4.1** | Strictly asynchronous importance scoring via background worker: `memoir_write` writes `importance=5` and `poignancy_status=NULL` immediately. | DD-20 invariant: hot append must never block the conversation turn on LLM calls. Conversation latency must remain in milliseconds. | Synchronous LLM scoring during `memoir_write`. |
| **V2-D 4.2** | Clones `MemoirEmbeddingWorker` architecture: lifespan task on `app.state`, dedicated `ThreadPoolExecutor(max_workers=1, thread_name_prefix="memoir-poignancy")`, 30s timeout, attempt cap of 5, terminal markers (`terminal:<reason>`), structured pinnable tick lines. | Module docstring of `memoir_embedding_worker.py:1-78` represents the battle-tested house worker pattern. Dedicated thread pool avoids event-loop starvation. | Using `asyncio.to_thread` (forbidden in workers); OR unmanaged polling loops. |
| **V2-D 4.3** | Unscored detection via `poignancy_status IS NULL`: empty column IS the queue (mirroring `embedding IS NULL` in v1). | No side queue tables needed; column emptiness forms a simple, restart-durable indexable queue. | Introducing a dedicated `poignancy_queue` table. |
| **V2-D 4.4** | Poignancy scoring prompt with integer scale 1–10: 1 = trivial fleeting pleasantry; 5 = standard operational interaction; 8–9 = significant milestone / emotional bond; 10 = constitutional decree / foundational event. | Aligns with existing CHECK constraint `ck_memoir_episodes_importance` (`importance BETWEEN 1 AND 10`). | Floating-point 0.0–1.0 score (would require altering table CHECK constraints). |
| **V2-D 4.5** | Integration with reflection and keepsake proposals: When poignancy is updated, the service notifies `MemoirReflectionService` to increment the rolling importance counter. | Decoupled observer pattern: poignancy completion drives reflection readiness organically without circular service dependencies. | Polling the database repeatedly to sum importance. |

---

## 3. Domain Detail & Worker Architecture

### 3.1 Worker Lifecycle & Tick Loop
- **Tick Interval:** Every 20 seconds (`MEMOIR_POIGNANCY_TICK_S = 20.0`).
- **Batch Cap:** Maximum 10 episodes per tick (`MEMOIR_POIGNANCY_BATCH_CAP = 10`).
- **Knob Re-read:** Every tick re-reads `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`. If OFF, logs one park message and sleeps; resumes execution when turned ON.
- **Structured Log Line:** `[MemoirPoignancy] pending=N scored=K failed=F terminal=T duration=X.Xs`.

### 3.2 Scoring Pipeline & Failure Isolation
1. Worker queries repository for batch: `SELECT * FROM memoir_episodes WHERE poignancy_status IS NULL AND (poignancy_attempts < 5) ORDER BY created_at ASC LIMIT 10`.
2. For each episode:
   - Formulates scoring prompt containing title, event time, body, and star tags.
   - Invokes LLM via `invoke_raw_with_failover`.
   - Parses integer score 1..10 via tolerant regex / JSON extraction.
   - On success: updates `importance = score, poignancy_status = 'scored'`.
   - On failure: increments `poignancy_attempts += 1`. If attempts reach 5, sets `poignancy_status = 'terminal:max_attempts_exceeded'`.
3. Notifies reflection service of updated importance delta (`score - 5`).

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T4.1** | Implement `MemoirPoignancyService` in `daemon/services/memoir_poignancy_service.py` to prompt LLM, extract 1–10 scores, and update episode rows. | Scoring function evaluates episode text and returns clamped integer 1..10; handles parse errors gracefully. | `daemon/services/memoir_poignancy_service.py` | P1 | R3 | AC1 |
| **T4.2** | Add repository query methods in `daemon/repositories/memoir/repository.py`: `get_pending_poignancy_episodes()` and `record_poignancy_score()`. | Queries fetch rows where `poignancy_status IS NULL`; updates atomically write score and status. | `daemon/repositories/memoir/repository.py` | P1 | — | AC2 |
| **T4.3** | Implement `MemoirPoignancyWorker` in `daemon/services/memoir_poignancy_worker.py` with lifespan hooks, dedicated thread pool, and attempt caps. | Worker runs periodically; catches exceptions; parks when disabled; terminalizes failing rows after 5 attempts. | `daemon/services/memoir_poignancy_worker.py`, `daemon/api.py` | T4.1, T4.2 | R4 | AC3 |
| **T4.4** | Add configuration settings in `daemon/config.py`: `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`, `MEMOIR_POIGNANCY_TICK_S`, etc., with DR-3 truth table validation. | Knob defaults to ON with warning; invalid values fail-closed; call-time updates take effect within 1 tick. | `daemon/config.py`, `daemon/tools/memoir_knob.py` | — | — | AC4 |
| **T4.5** | Wire poignancy completion hook to `MemoirReflectionService` rolling importance counter. | Scoring an episode with importance 9 increments reflection importance counter by +4 (relative to default 5). | `daemon/services/memoir_reflection_service.py` | T4.1, P2 | — | AC5 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | Hot-path non-blocking: Calling `memoir_write` returns within standard turn latency; episode is inserted with `importance=5` and `poignancy_status=NULL`. |
| **AC2** | Async scoring accuracy: Worker processes pending episodes; updates `importance` to LLM-scored integer (1..10) and sets `poignancy_status='scored'`; score persists across restarts. |
| **AC3** | Attempt caps and terminal markers: An episode that repeatedly triggers parse failures increments `poignancy_attempts`; after 5 failed attempts, status flips to `terminal:parse_failure` and worker advances past it. |
| **AC4** | Worker kill-switch isolation: Setting `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED="false"` parks the worker without affecting episodic writes or memory recall; setting back to `"true"` resumes scoring backlog. |
| **AC5** | Reflection trigger coupling: Scored high-poignancy episodes correctly advance the reflection service's cumulative counter towards the trigger threshold (≥ 25). |
| **AC6 (Mutation)** | Removing clamp in poignancy score parser flips AC2 RED; changing attempt cap flips AC3 RED; disabling reflection counter hook flips AC5 RED. |

---

## 6. Test Packs

- `test/packs/memoir_v2_poignancy_service_test.sh`: Tests prompt formatting, score parsing, clamping 1..10, and error fallback.
- `test/packs/memoir_v2_poignancy_worker_test.sh`: Tests batch queue polling, attempt increments, terminal status transitions, and thread pool isolation.
- `test/packs/memoir_v2_poignancy_lifecycle_test.sh`: Tests end-to-end integration: `memoir_write` -> background worker pick-up -> database update -> reflection counter notification.

---

## 7. Flag Consumption

- `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`: Discrete kill-switch for poignancy scoring.
- **DD-5:** Asynchronous LLM importance scoring replacing v1 static default.
- **DD-20:** Hot append purity preserved.
- **V2-10:** Poignancy worker architecture.
