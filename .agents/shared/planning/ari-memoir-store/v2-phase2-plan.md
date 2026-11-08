# Phase 2 — The Dream: Reflection Engine & Syntheses Store

**File:** `v2-phase2-plan.md`  
**Depends on:** Phase 1 (`v2-phase1-plan.md`).  
**Flags / Decisions consumed:** `ENSEMBLE_MEMOIR_REFLECTION_ENABLED`, V2-5, V2-6, V2-7, V2-13, V2-15, V2-16, V2-17, V2-19, V2-24, V2-27 (C2), V2-30 (SF4), V2-31 (SF7), V2-35 (SF5), V2-Q9.

---

## 1. Objective

Deliver the Reflection Engine ("The Dream") and activate the Syntheses layer:
1. Implement `MemoirReflectionService` supporting the two Emperor-blessed reflection cadences:
   - **Intra-day idle-debounce with warm-up doubling:** Base 60s window doubling on bursts (`1→2→4→8→16`, capped at 960s), triggered when derived importance sum reaches ≥ 25; plus **force-fire at 3600s** (`MEMOIR_REFLECTION_FORCE_FIRE_S`) to eliminate indefinite deferral under continuous conversational streams.
   - **Nightly bounded pass & Historical Seed (C2 / Leader ruling 2026-11-09):** ReMe `auto_dream` shape with strict caps (max 5 syntheses, max 1000 chars/synthesis, max 4 LLM calls/pass), and a 60s tick budget during quiet hours (02:00–04:00 UTC). Walks unreflected episodes **oldest-first (`ORDER BY created_at ASC LIMIT 30`)** without lookback window restriction during catch-up until the pre-cutover corpus is reflected into Constellation.
2. Implement background `MemoirReflectionWorker` cloning the robust `MemoirEmbeddingWorker` template (lifespan task on `app.state`, dedicated single-thread executor `memoir-reflect`, park/resume kill-switch logic, sequential nightly-before-in-day execution).
3. The trigger sum is **DERIVED PER TICK** from `SELECT COALESCE(SUM(COALESCE(poignancy, importance)), 0), MAX(created_at) FROM memoir_episodes WHERE agent_id=:ari AND reflected_at IS NULL AND created_at >= now()-interval '2 days'`. (Zero in-memory rolling counters; zero delta-notify coupling).
4. Generate structured syntheses rows in `memoir_syntheses` with mandatory source citations (`source_episode_ids[]`), epistemic verbs (`CREATE`, `CORROBORATE`, `REFINE`, `CORRECT`), confidence scoring, NFC-normalized window dedup (SF4), and atomic single-transaction `supersede_with` links.
5. Upgrade `memoir_recall` to dual retrieval mode conforming to §3-A6: returns both active syntheses (capped at 5) and matching episodes in the byte-pinned envelope format, with lane-OFF preserving byte-identical v1 output, weakest-synthesis tail-drop on envelope overflow, and explicit recall heat increment for returned episodes' stars (W5).
6. Record advisory foreign-belief import rule as doc note & writer convention (SF10 / V2-Q9): an imported belief is a `CORROBORATE`-class act citing the original source (no validation machinery in v2).

---

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected Alternatives |
|----|----------|------------------------------|-----------------------|
| **V2-D 2.1** | Two reflection cadences inside ONE sequential worker tick with **nightly-before-in-day ordering** (load-bearing; §3-A1 / Register #19): (1) Nightly pass (02:00–04:00 UTC, max 30 episodes, oldest-unreflected-first catch-up per C2); (2) In-day idle debounce (`base=60.0s`, `ceiling=960.0s`, `force_fire=3600.0s`; §2-Q2 / Register #11). | Nightly pass runs first and advances `reflected_at` marks, so the subsequent in-day branch sees sum < 25 and idles cleanly. Sequential execution in one thread eliminates race conditions by construction. Oldest-first scan absorbs historical backlog safely. | Concurrent passes; OR in-day evaluation before nightly. |
| **V2-D 2.2** | Reflection trigger sum is **DERIVED PER TICK**, never stored in memory or pushed via delta-notifies (§3-A1 / Register #4): `SELECT COALESCE(SUM(COALESCE(poignancy, importance)), 0), MAX(created_at) FROM memoir_episodes WHERE agent_id=:ari AND reflected_at IS NULL AND created_at >= now()-interval '2 days'`. | Delta-notifies double-count on re-scores, die on restart, and tightly couple service lifecycles. A derived SQL query is restart-durable, immune to re-scores, and self-clearing when episodes are marked `reflected_at`. | In-memory rolling counter; OR pub/sub delta notifications. |
| **V2-D 2.3** | Mandatory citations on every synthesis row: `source_episode_ids` must be a non-empty list of valid episode UUIDs. | Ari must be able to explain *how* she came to believe a claim. Uncitated syntheses are hallucinations masquerading as memory. | Permitting uncited global beliefs. |
| **V2-D 2.4** | Dual retrieval in `memoir_recall` conforming to §3-A6 real envelope shape: header → [synthesis section: `[synthesis:ID] (verb, confidence=…) claim` + `sources:` lines, cap 5] → untouched episode blocks → footer + `syntheses=M`. Lane OFF produces byte-identical v1 output; Lane ON with zero matches emits no section and `syntheses=0` in footer. Syntheses tail-drop weakest on 6000-token envelope breach. Explicit recall hit increments star heat for returned episodes' stars (W5). | Freehand reformatting of the recall envelope. The footer is byte-pinned in existing unit tests (`test_memoir_tools_unit.py:559-569`); syntheses render first so budget re-checks drop episodes before syntheses. | Separate `memoir_recall_syntheses` tool; OR dropping footer compatibility. |
| **V2-D 2.5** | Background worker `MemoirReflectionWorker` clones `MemoirEmbeddingWorker` architecture: lifespan task on `app.state`, dedicated `ThreadPoolExecutor(max_workers=1, thread_name_prefix="memoir-reflect")`, per-tick exception catching, call-time knob re-reading (`ENSEMBLE_MEMOIR_REFLECTION_ENABLED`). | `asyncio.to_thread` is forbidden in worker modules (D3.5); dedicated thread pool isolates long-running LLM reflection from the main event loop. | Synchronous reflection in request handlers; OR unmanaged asyncio tasks. |
| **V2-D 2.6** | Binding budget enforcement table (§2-Q3): 5 syntheses/pass; 1000 chars **REJECTED** (never truncated; truncation manufactures false propositions); 4 LLM calls (**incremented at dispatch**); 60s tick budget; `httpx.Timeout(45.0, connect=5.0)`. Partial-commit everywhere, abort nowhere, defer on time. | Truncating claims to 1000 chars; OR aborting entire pass on single unit failure. |
| **V2-D 2.7** | Dedup key normalization & per-agent window check (SF4 / V2-30): Normalization = NFC + whitespace-collapse + casefold; checked against active un-superseded syntheses within 2-day lookback window. | Scale rationale: Reflection operates over a bounded 2-day window (~dozens of claims). In-window check is instantaneous (<1ms) and avoids altering pre-staged `memoir_syntheses` table (honoring DD-2). | Adding indexed column to pre-staged table; OR skipping dedup. |
| **V2-D 2.8** | Atomic supersession writer: `supersede_with(old_id, new_row)` in `MemoirSynthesesRepository` links prior synthesis and new synthesis in ONE transaction (§3-A2 / Register #6). | Two-call shape (`create_synthesis` then `mark_superseded`) leaves unlinked forks on crash. | Two separate repository calls. |
| **V2-D 2.9** | Three-class numeric knob governance & shared before-validator (DR-3 / §3-A4 / Register #12): `_classify_numeric` in `daemon/config.py` validates budget bounds (clamp to default + aggregate warning on unset) and cadence tunables (default + silent). Never raises `ValidationError` to avoid boot aborts. | Bare `Field(gt=...)` on numerics (pre-existing boot-abort defect, anomaly `33028b27`). | Bare `Field` validator killing daemon boot on unparseable numeric env vars. |
| **V2-D 2.10** | Nightly-claim stale-takeover (SF5 / V2-35): Claims in `memoir_reflection_state` older than 15 minutes (`claimed_at < :now - interval '15 minutes'`) are re-takeable by a subsequent pass for automatic crash recovery. | Permanent locking; zero-timeout claim. | Allows automatic recovery if a worker process crashes mid-pass while holding a claim. |
| **V2-D 2.11** | Decoupled LLM call accounting (SF7 / V2-31): Reflection synthesis pass cap is 4 calls (`MEMOIR_REFLECTION_MAX_LLM_CALLS_PER_PASS = 4`) for synthesis + JEV; constellation extraction has separate budget (`MEMOIR_CONSTELLATION_MAX_LLM_CALLS_PER_PASS = 2`). Metered `[MemoirReflection]` line owned here. | Shared 4-call cap; unmetered constellation calls. | Decoupled accounting ensures constellation extraction never starves core synthesis generation. |

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
    ├── YES ──> Acquire DB claim (memoir_reflection_state, 15-min stale takeover, SF5)
    │           Run nightly consolidation (scan LIMIT 30 episodes, oldest-unreflected-first, C2)
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
| Scan window | 2 days (nightly catch-up walks backlog) | SQL predicate in repo query | Query bounds regular episode intake to ~48 hours; backlog catch-up is capped at 30/pass. |
| Nightly cap | 30 episodes | SQL `LIMIT 30` + accept loop | Process max 30 episodes; defer remainder. |
| Dedup key | `sha256(claim_norm + sorted(sources))` | repository window query | Skip insert; return existing row (idempotent retry). |

### 3.3 Dual Retrieval Envelope in `memoir_recall` (§3-A6)

When Ari calls `memoir_recall(query="...", k=5)`:
1. If reflection lane is OFF: returns byte-identical v1 output (episodes only, no syntheses section, no footer syntheses token).
2. If reflection lane is ON:
   - Queries `memoir_syntheses` for active beliefs (`superseded_by IS NULL`) matching query (up to `MEMOIR_RECALL_MAX_SYNTHESES = 5`).
   - Queries `memoir_episodes` via existing hybrid lexical ∥ semantic RRF pipeline (up to `k` episodes).
   - If syntheses match, renders `--- SYNTHESES ---` block FIRST (consolidated beliefs lead), followed by `--- EPISODES ---` block.
   - Tail-drop semantics: If the combined content tips the 6000-token envelope, the budget re-check tail-drops episodes first, and then tail-drops the weakest synthesis line if needed.
   - Appends `syntheses=M` to the service footer: `[memoir_recall: episodes=N dropped=D body_tokens=T budget=6000 rank=rrf semantic=... stats_updated=false syntheses=M]`.
   - If zero syntheses match, the syntheses section is omitted, and footer reports `syntheses=0`.
   - **Heat Increment (W5):** Increments stored heat in `memoir_star.heat` for stars cited by returned episodes via `MemoirConstellationRepository.increment_heat`.

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T2.1** | Implement `MemoirReflectionService` with derived-sum trigger query, debounce doubling timer (60s→960s), and force-fire timer (3600s). | Service derives unreflected importance sum per tick; triggers when sum ≥ 25 and idle window or force-fire expires; no in-memory counter persistence. | `daemon/services/memoir_reflection_service.py` | P1 | R3 | AC1 |
| **T2.2** | Implement reflection generation pipeline with dispatch-time call increments, 1000-char reject validator, NFC normalized dedup check, and metered `[MemoirReflection]` pass log line. | Syntheses validated against 1000-char cap; claims >1000 chars rejected with log; duplicate claims skipped via hash dedup; uncitated rows rejected. | `daemon/services/memoir_reflection_service.py` | T2.1 | R2, R3 | AC2 |
| **T2.3** | Implement `MemoirReflectionWorker` lifespan task on `app.state` with sequential nightly-before-in-day execution, 15-min stale takeover (SF5), and dedicated thread pool `memoir-reflect`. | Worker executes nightly pass at 02:00–04:00 UTC (oldest-unreflected-first, C2); executes in-day pass otherwise; takes over claims >15 min old; marks `reflected_at` upon pass completion; parks when disabled. | `daemon/services/memoir_reflection_worker.py`, `daemon/api.py` | T2.2 | R4 | AC3 |
| **T2.4** | Implement three-class numeric knob configuration in `daemon/config.py` using shared `_classify_numeric` before-validator (Register #12). | Unset budget knobs emit aggregate warning and clamp to default; invalid numerics clamp without raising `ValidationError`; boot never aborts. | `daemon/config.py`, `daemon/tools/memoir_knob.py` | — | R7 | AC4 |
| **T2.5** | Update `daemon/tools/memoir.py` and `daemon/services/memoir_retrieval.py` to implement §3-A6 dual recall envelope with lane-OFF byte-identity, weakest-synthesis tail-drop, and star heat increments on recall hits (W5). | `memoir_recall` renders syntheses leading episodes; footer gains `syntheses=M`; tail-drop drops weakest synthesis on envelope overflow; returned episodes' stars have heat incremented (W5); lane OFF preserves byte-identical v1 output. | `daemon/tools/memoir.py`, `daemon/services/memoir_retrieval.py` | T2.2 | — | AC5 |
| **T2.6** | Record advisory foreign-belief import rule as writer convention & documentation note (SF10 / V2-Q9): foreign beliefs land as `CORROBORATE`-class local synthesis rows citing foreign original. | Convention documented in repository docstring; zero v2 runtime validation machinery required. | `daemon/repositories/memoir/syntheses_repository.py` | P1 | — | AC6 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | Derived trigger & debounce doubling: 1 unreflected episode with importance 10 does NOT trigger; episodes totaling derived sum 26 trigger after 60s idle; arriving episode during debounce doubles window to 120s (ceiling 960s); continuous load without idle triggers at 3600s force-fire; marking `reflected_at` drops derived sum to 0. |
| **AC2** | Budget enforcement & log metering: Pass terminates after 5 accepted syntheses; candidate synthesis with 1001 chars is REJECTED (never truncated); 4th LLM call dispatches and stops pass; duplicate synthesis returns existing row without double-insertion; pass emits structured `[MemoirReflection]` log line. |
| **AC3** | Sequential worker lifecycle & crash takeover (SF5): Nightly gate runs before in-day; restart at 02:30 runs nightly on first tick; claim older than 15 minutes is taken over by subsequent pass; worker runs in dedicated thread `memoir-reflect`; knob OFF parks cleanly within 1 tick; shutdown cancels and awaits worker task. |
| **AC4** | Three-class knob governance (DR-3): Unset `ENSEMBLE_MEMOIR_REFLECTION_ENABLED` -> True + warning; garbage -> False + warning; unparseable numeric env var clamps to default with warning without raising `ValidationError` or aborting boot. |
| **AC5** | Dual recall contract, tail-drop & heat increment (§3-A6 / W5 / Optional #4): With lane ON and matches present, syntheses section renders first; footer carries `syntheses=M`; with lane ON and zero matches, syntheses section is omitted and footer reports `syntheses=0`; when combined results tip 6000-token envelope, episodes tail-drop first, then weakest synthesis tail-drops; explicit recall hit in `memoir_recall` increments `memoir_star.heat` for stars cited by returned episodes (W5); with lane OFF, response is byte-identical to v1. |
| **AC6** | Advisory import convention parity (SF10): Repository docstring records the foreign-belief convention; verify no foreign-validation exception is raised during normal v2 execution. |
| **AC7 (Mutation)** | Restored mutation legs (W1): (a) Nightly-before-in-day order-reversal leg: Reversing pass execution order to evaluate in-day before nightly flips RED (trips pass sequencing assertion); (b) Dual-recall lane-disable leg: With `ENSEMBLE_MEMOIR_REFLECTION_ENABLED='false'`, any delta in recall envelope structure or footer vs v1 flips RED (asserts byte-identity). (Placement choice: kept in Phase 2 AC7 for direct testability and referenced in Phase 7 §3.3). |

---

## 6. Test Packs

- `test/packs/memoir_v2_reflection_service_test.sh`: Tests derived SQL trigger sum, debounce doubling, force-fire timer, prompt composition, and NFC-normalized dedup hash.
- `test/packs/memoir_v2_reflection_worker_test.sh`: Tests sequential nightly-before-in-day execution, 15-minute claim stale takeover (SF5), tick budget, exception isolation, and kill-switch park/resume.
- `test/packs/memoir_v2_dual_recall_test.sh`: Tests §3-A6 dual retrieval envelope formatting in `memoir_recall`, synthesis tail-drop on overflow, star heat increments (W5), lane-OFF byte-identity, and footer tokens.
- `test/packs/memoir_v2_numeric_knob_test.sh`: Tests three-class numeric knob classifier, Pydantic before-validator, and boot-abort prevention.

---

## 7. Flag Consumption

- `ENSEMBLE_MEMOIR_REFLECTION_ENABLED`: Discrete kill-switch for reflection.
- **V2-5 / §3-A1:** Dual-cadence reflection engine with derived trigger and force-fire timer.
- **V2-6:** Mandatory citations on syntheses.
- **V2-7 / §3-A6:** Dual retrieval in `memoir_recall` with real envelope formatting and tail-drop.
- **V2-13:** Well-formed supersedes chains per `v4-federated-sketch.md` §5.
- **V2-15 / §3-A2:** Atomic `supersede_with` writer.
- **V2-16 / §3-A4:** Three-class numeric knob governance.
- **V2-17 / §2-Q3:** Synthesis insert dedup key.
- **V2-19 / §3-A1:** `reflected_at` watermark column and `memoir_reflection_state` table.
- **V2-24 / §3-A1:** Nightly-before-in-day sequential ordering.
- **V2-27 / C2:** Bounded one-time seed pass oldest-unreflected-first nightly scan.
- **V2-30 / SF4:** NFC claim normalization and per-agent window dedup.
- **V2-31 / SF7:** Decoupled LLM cost accounting.
- **V2-35 / SF5:** Nightly claim stale-takeover (15 minutes).
- **V2-Q9 / SF10:** Advisory foreign-belief import rule.
- **W1:** Restored mutation legs in AC7.
- **W5:** `memoir_recall` heat increment.
