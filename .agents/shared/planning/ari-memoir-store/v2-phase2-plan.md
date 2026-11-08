# Phase 2 — The Dream: Reflection Engine & Syntheses Store

**File:** `v2-phase2-plan.md`  
**Depends on:** Phase 1 (`v2-phase1-plan.md`).  
**Flags / Decisions consumed:** `ENSEMBLE_MEMOIR_REFLECTION_ENABLED`, V2-5, V2-6, V2-7, V2-13, V2-15, V2-16, V2-17, V2-19, V2-24, V2-Q9.

---

## 1. Objective

Deliver the Reflection Engine ("The Dream") and activate the Syntheses layer:
1. Implement `MemoirReflectionService` supporting the two Emperor-blessed reflection cadences:
   - **Intra-day idle-debounce with warm-up doubling:** Base 60s window doubling on bursts (`1→2→4→8→16`, capped at 960s), triggered when derived importance sum reaches ≥ 25; plus **force-fire at 3600s** (`MEMOIR_REFLECTION_FORCE_FIRE_S`) to eliminate indefinite deferral under continuous conversational streams.
   - **Nightly bounded pass:** ReMe `auto_dream` shape with a bounded 2-day lookback window, strict caps (max 5 syntheses, max 1000 chars/synthesis, max 4 LLM calls/pass), and a 60s tick budget during quiet hours (02:00–04:00 UTC).
2. Implement background `MemoirReflectionWorker` cloning the robust `MemoirEmbeddingWorker` template (lifespan task on `app.state`, dedicated single-thread executor, park/resume kill-switch logic, sequential nightly-before-in-day execution).
3. The trigger sum is **DERIVED PER TICK** from `SELECT COALESCE(SUM(COALESCE(poignancy, importance)), 0), MAX(created_at) FROM memoir_episodes WHERE agent_id=:ari AND reflected_at IS NULL AND created_at >= now()-interval '2 days'`. (Zero in-memory rolling counters; zero delta-notify coupling).
4. Generate structured syntheses rows in `memoir_syntheses` with mandatory source citations (`source_episode_ids[]`), epistemic verbs (`CREATE`, `CORROBORATE`, `REFINE`, `CORRECT`), confidence scoring, insert dedup keys, and atomic single-transaction `supersede_with` links.
5. Upgrade `memoir_recall` to dual retrieval mode conforming to §3-A6: returns both active syntheses (capped at 5) and matching episodes in the byte-pinned envelope format, with lane-OFF preserving byte-identical v1 output.
6. Ingest advisory foreign-belief import rule (V2-Q9): an imported belief is a `CORROBORATE`-class act citing the original source.

---

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected Alternatives |
|----|----------|------------------------------|-----------------------|
| **V2-D 2.1** | Two reflection cadences inside ONE sequential worker tick with **nightly-before-in-day ordering** (load-bearing; §3-A1 / Register #19): (1) Nightly pass (02:00–04:00 UTC, max 30 episodes, 2-day window); (2) In-day idle debounce (`base=60.0s`, `ceiling=960.0s`, `force_fire=3600.0s`; §2-Q2 / Register #11). | Nightly pass runs first and advances `reflected_at` marks, so the subsequent in-day branch sees sum < 25 and idles cleanly. Sequential execution in one thread eliminates race conditions by construction. | Concurrent passes; OR in-day evaluation before nightly. |
| **V2-D 2.2** | Reflection trigger sum is **DERIVED PER TICK**, never stored in memory or pushed via delta-notifies (§3-A1 / Register #4): `SELECT COALESCE(SUM(COALESCE(poignancy, importance)), 0), MAX(created_at) FROM memoir_episodes WHERE agent_id=:ari AND reflected_at IS NULL AND created_at >= now()-interval '2 days'`. | Delta-notifies double-count on re-scores, die on restart, and tightly couple service lifecycles. A derived SQL query is restart-durable, immune to re-scores, and self-clearing when episodes are marked `reflected_at`. | In-memory rolling counter; OR pub/sub delta notifications. |
| **V2-D 2.3** | Mandatory citations on every synthesis row: `source_episode_ids` must be a non-empty list of valid episode UUIDs. | Ari must be able to explain *how* she came to believe a claim. Uncitated syntheses are hallucinations masquerading as memory. | Permitting uncited global beliefs. |
| **V2-D 2.4** | Dual retrieval in `memoir_recall` conforming to §3-A6 real envelope shape: header → [synthesis section: `[synthesis:ID] (verb, confidence=…) claim` + `sources:` lines, cap 5] → untouched episode blocks → footer + `syntheses=M`. Lane OFF produces byte-identical v1 output; Lane ON with zero matches emits no section and `syntheses=0` in footer. | Freehand reformatting of the recall envelope. The footer is byte-pinned in existing unit tests (`test_memoir_tools_unit.py:559-569`); syntheses render first so budget re-checks drop episodes before syntheses. | Separate `memoir_recall_syntheses` tool; OR dropping footer compatibility. |
| **V2-D 2.5** | Background worker `MemoirReflectionWorker` clones `MemoirEmbeddingWorker` architecture: lifespan task on `app.state`, dedicated `ThreadPoolExecutor(max_workers=1, thread_name_prefix="memoir-reflect")`, per-tick exception catching, call-time knob re-reading (`ENSEMBLE_MEMOIR_REFLECTION_ENABLED`). | `asyncio.to_thread` is forbidden in worker modules (D3.5); dedicated thread pool isolates long-running LLM reflection from the main event loop. | Synchronous reflection in request handlers; OR unmanaged asyncio tasks. |
| **V2-D 2.6** | Binding budget enforcement table (§2-Q3): 5 syntheses/pass; 1000 chars **REJECTED** (never truncated; truncation manufactures false propositions); 4 LLM calls (**incremented at dispatch**); 60s tick budget; `httpx.Timeout(45.0, connect=5.0)`. Partial-commit everywhere, abort nowhere, defer on time. | Truncating claims to 1000 chars; OR aborting entire pass on single unit failure. |
| **V2-D 2.7** | Insert dedup key: `sha256(claim_normalized + sorted(source_episode_ids))` enforced on synthesis insert (§2-Q3 / Register #13). | At-least-once reflection retries after mid-pass crashes become safe no-ops instead of duplicating beliefs. | Inserting without content dedup. |
| **V2-D 2.8** | Atomic supersession writer: `supersede_with(old_id, new_row)` in `MemoirSynthesesRepository` links prior synthesis and new synthesis in ONE transaction (§3-A2 / Register #6). | Two-call shape (`create_synthesis` then `mark_superseded`) leaves unlinked forks on crash. | Two separate repository calls. |
| **V2-D 2.9** | Three-class numeric knob governance & shared before-validator (DR-3 / §3-A4 / Register #12): `_classify_numeric` in `daemon/config.py` validates budget bounds (clamp to default + aggregate warning on unset) and cadence tunables (default + silent). Never raises `ValidationError` to avoid boot aborts. | Bare `Field(gt=...)` on numerics (pre-existing boot-abort defect, anomaly `33028b27`). | Bare `Field` validator killing daemon boot on unparseable numeric env vars. |

---

## 3. Domain Detail & Reflection Execution

### 3.1 Worker Tick Loop & Cadence Sequencing (§3-A1)

The worker executes a single sequential tick body every 30s:
```text
[Tick Start]
    │
    ▼
Re-read ENSEMBLE_MEMOIR_REFLECTION_ENABLED
    │ (if OFF: log park message, sleep 30s)
    ▼
Check Nightly Pass Gate:
    02:00 <= now < 04:00 UTC AND last_nightly_date != today
    │
    ├── YES ──> Acquire DB claim (memoir_reflection_state)
    │           Run nightly consolidation (scan LIMIT 30 episodes, 2-day window)
    │           Commit syntheses + lines + update reflected_at in ONE txn
    │           Set last_nightly_date = today; clear claim
    │
    └── NO  ──> Evaluate In-Day Trigger:
                Derive SUM = SELECT SUM(COALESCE(poignancy, importance)) over unreflected 2-day episodes
                Derive MAX_TS = MAX(created_at) over unreflected set
                IF SUM >= 25 AND (now - MAX_TS >= debounce_s OR elapsed >= 3600.0s):
                    Run in-day reflection pass (max 5 syntheses, max 4 calls)
                    Commit syntheses + lines + update reflected_at in ONE txn
                    Reset debounce_s = 60.0s; clear claim
```

### 3.2 Budget & Enforcement Table (§2-Q3)

| Bound | Constant | Enforcement Point | Breach Behavior |
|---|---|---|---|
| Max syntheses / pass | 5 | accept-loop, post-validate / pre-insert | Stop accepting; keep committed rows; log `synthesis_cap_hit kept=5 dropped=K`. |
| Max chars / synthesis | 1000 | validator, before insert | **REJECT the row** (`claim_too_long` skip + log). Never truncate. |
| Max LLM calls / pass | 4 | call site, **increment at dispatch** | Stop pass; keep committed; log `call_cap_hit`. JEV call counts towards this cap. |
| Tick budget | 60.0 s | per-item pre-dispatch check | Defer remainder to next pass; log `pass_deferred`. |
| Per-call timeout | `httpx.Timeout(45.0, connect=5.0)` | client construction | Failure increments attempt; strictly < tick budget so tick guard can fire. |
| Scan window | 2 days | SQL predicate in repo query | Query bounds episode intake to ~48 hours. |
| Nightly cap | 30 episodes | SQL `LIMIT 30` + accept loop | Process max 30 episodes; defer remainder. |
| Dedup key | `sha256(claim_norm + sorted(sources))` | repository insert | Skip insert; return existing row (idempotent retry). |

### 3.3 Dual Retrieval Envelope in `memoir_recall` (§3-A6)

When Ari calls `memoir_recall(query="...", k=5)`:
1. If reflection lane is OFF: returns byte-identical v1 output (episodes only, no syntheses section, no footer syntheses token).
2. If reflection lane is ON:
   - Queries `memoir_syntheses` for active beliefs (`superseded_by IS NULL`) matching query (up to `MEMOIR_RECALL_MAX_SYNTHESES = 5`).
   - Queries `memoir_episodes` via existing hybrid lexical ∥ semantic RRF pipeline (up to `k` episodes).
   - If syntheses match, renders `--- SYNTHESES ---` block FIRST (consolidated beliefs lead), followed by `--- EPISODES ---` block.
   - Appends `syntheses=M` to the service footer: `[memoir_recall: episodes=N dropped=D body_tokens=T budget=6000 rank=rrf semantic=... stats_updated=false syntheses=M]`.
   - If zero syntheses match, the syntheses section is omitted, and footer reports `syntheses=0`.

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T2.1** | Implement `MemoirReflectionService` with derived-sum trigger query, debounce doubling timer (60s→960s), and force-fire timer (3600s). | Service derives unreflected importance sum per tick; triggers when sum ≥ 25 and idle window or force-fire expires; no in-memory counter persistence. | `daemon/services/memoir_reflection_service.py` | P1 | R3 | AC1 |
| **T2.2** | Implement reflection generation pipeline with dispatch-time call increments, 1000-char reject validator, and synthesis insert dedup key. | Syntheses validated against 1000-char cap; claims >1000 chars rejected with log; duplicate claims skipped via hash dedup; uncitated rows rejected. | `daemon/services/memoir_reflection_service.py` | T2.1 | R2, R3 | AC2 |
| **T2.3** | Implement `MemoirReflectionWorker` lifespan task on `app.state` with sequential nightly-before-in-day execution and dedicated thread pool. | Worker executes nightly pass at 02:00–04:00 UTC; executes in-day pass otherwise; marks `reflected_at` upon pass completion; parks when disabled. | `daemon/services/memoir_reflection_worker.py`, `daemon/api.py` | T2.2 | R4 | AC3 |
| **T2.4** | Implement three-class numeric knob configuration in `daemon/config.py` using shared `_classify_numeric` before-validator (Register #12). | Unset budget knobs emit aggregate warning and clamp to default; invalid numerics clamp without raising `ValidationError`; boot never aborts. | `daemon/config.py`, `daemon/tools/memoir_knob.py` | — | R7 | AC4 |
| **T2.5** | Update `daemon/tools/memoir.py` and `daemon/services/memoir_retrieval.py` to implement the §3-A6 dual recall envelope with lane-OFF byte-identity. | `memoir_recall` renders syntheses leading episodes; footer gains `syntheses=M`; lane OFF preserves byte-identical v1 output. | `daemon/tools/memoir.py`, `daemon/services/memoir_retrieval.py` | T2.2 | — | AC5 |
| **T2.6** | Implement advisory foreign-belief validation in `MemoirSynthesesRepository` (V2-Q9): imported claims require non-empty foreign source citations and `CORROBORATE` verb. | Import without foreign citation is rejected. | `daemon/repositories/memoir/syntheses_repository.py` | P1 | — | AC6 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | Derived trigger & debounce doubling: 1 unreflected episode with importance 10 does NOT trigger; episodes totaling derived sum 26 trigger after 60s idle; arriving episode during debounce doubles window to 120s (ceiling 960s); continuous load without idle triggers at 3600s force-fire; marking `reflected_at` drops derived sum to 0. |
| **AC2** | Budget enforcement: Pass terminates after 5 accepted syntheses; candidate synthesis with 1001 chars is REJECTED (never truncated); 4th LLM call dispatches and stops pass; duplicate synthesis returns existing row without double-insertion. |
| **AC3** | Sequential worker lifecycle: Nightly gate runs before in-day; restart at 02:30 runs nightly on first tick; worker runs in dedicated thread `memoir-reflect`; knob OFF parks cleanly within 1 tick; shutdown cancels and awaits worker task. |
| **AC4** | Three-class knob governance (DR-3): Unset `ENSEMBLE_MEMOIR_REFLECTION_ENABLED` -> True + warning; garbage -> False + warning; unparseable numeric env var clamps to default with warning without raising `ValidationError` or aborting boot. |
| **AC5** | Dual recall contract (§3-A6): With lane ON and matches present, syntheses section renders first; footer carries `syntheses=M`; with lane ON and zero matches, syntheses section is omitted and footer reports `syntheses=0`; with lane OFF, response is byte-identical to v1. |
| **AC6 (Mutation)** | Truncating instead of rejecting a 1005-char claim flips AC2 RED; reversing nightly-before-in-day order flips AC3 RED; disabling dual recall in `memoir.py` flips AC5 RED. |

---

## 6. Test Packs

- `test/packs/memoir_v2_reflection_service_test.sh`: Tests derived SQL trigger sum, debounce doubling, force-fire timer, prompt composition, and dedup hash.
- `test/packs/memoir_v2_reflection_worker_test.sh`: Tests sequential nightly-before-in-day execution, tick budget, exception isolation, and kill-switch park/resume.
- `test/packs/memoir_v2_dual_recall_test.sh`: Tests §3-A6 dual retrieval envelope formatting in `memoir_recall`, lane-OFF byte-identity, and footer tokens.
- `test/packs/memoir_v2_numeric_knob_test.sh`: Tests three-class numeric knob classifier, Pydantic before-validator, and boot-abort prevention.

---

## 7. Flag Consumption

- `ENSEMBLE_MEMOIR_REFLECTION_ENABLED`: Discrete kill-switch for reflection.
- **V2-5 / §3-A1:** Dual-cadence reflection engine with derived trigger and force-fire timer.
- **V2-6:** Mandatory citations on syntheses.
- **V2-7 / §3-A6:** Dual retrieval in `memoir_recall` with real envelope formatting.
- **V2-13:** Well-formed supersedes chains per `v4-federated-sketch.md` §5.
- **V2-15 / §3-A2:** Atomic `supersede_with` writer.
- **V2-16 / §3-A4:** Three-class numeric knob governance.
- **V2-17 / §2-Q3:** Synthesis insert dedup key.
- **V2-19 / §3-A1:** `reflected_at` watermark column and `memoir_reflection_state` table.
- **V2-24 / §3-A1:** Nightly-before-in-day sequential ordering.
- **V2-Q9:** Advisory foreign-belief import rule.
