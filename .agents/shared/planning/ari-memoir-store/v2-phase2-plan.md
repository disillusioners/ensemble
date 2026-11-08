# Phase 2 — The Dream: Reflection Engine & Syntheses Store

**File:** `v2-phase2-plan.md`  
**Depends on:** Phase 1 (`v2-phase1-plan.md`).  
**Flags / Decisions consumed:** `ENSEMBLE_MEMOIR_REFLECTION_ENABLED`, V2-5, V2-6, V2-7, V2-13, V2-Q9.

---

## 1. Objective

Deliver the Reflection Engine ("The Dream") and activate the Syntheses layer:
1. Implement `MemoirReflectionService` supporting the two Emperor-blessed reflection cadences:
   - **Intra-day idle-debounce with warm-up doubling:** Base 60s window doubling on bursts (`1→2→4→8→16`, capped at 960s), triggered when cumulative importance sum reaches ≥ 25.
   - **Nightly bounded pass:** ReMe `auto_dream` shape with a bounded 2-day lookback window, strict caps (max 5 syntheses, max 1000 chars/synthesis, max 4 LLM calls/pass), and a 60s tick budget.
2. Implement background `MemoirReflectionWorker` cloning the robust `MemoirEmbeddingWorker` template (lifespan task on `app.state`, dedicated executor, park/resume kill-switch logic).
3. Generate structured syntheses rows in `memoir_syntheses` with mandatory source citations (`source_episode_ids[]`), epistemic verbs (`CREATE`, `CORROBORATE`, `REFINE`, `CORRECT`), confidence scoring, and supersession pointers.
4. Upgrade `memoir_recall` to dual retrieval mode: returns both matching episodes and active syntheses in distinct, clearly-typed blocks within the byte-pinned envelope.
5. Ingest advisory foreign-belief import rule (V2-Q9): an imported belief is a `CORROBORATE`-class act citing the original source.

---

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected Alternatives |
|----|----------|------------------------------|-----------------------|
| **V2-D 2.1** | Two reflection cadences: in-day idle debounce with doubling (`MEMOIR_REFLECTION_IDLE_BASE_S = 60.0`, ceiling 960s) + nightly auto-dream pass (02:00–04:00 UTC). | TencentDB `pipeline-manager` + ReMe `auto_dream` house patterns. A static cron over-reflects after quiet periods; debounce adapts to conversational volume while nightly pass ensures regular consolidation. | Constant polling loop; OR single daily cron with unbounded scan. |
| **V2-D 2.2** | Strict epistemic verb semantics: `CREATE` (novel belief), `CORROBORATE` (reinforces existing belief with new citations), `REFINE` (adjusts nuances, superseding previous), `CORRECT` (overturns an outdated or contradicted belief, superseding previous). | Pre-staged `memoir_syntheses.verb` CHECK constraint (`20261105_create_memoir_tables.sql:141`). Enforces epistemic rigor and provides the foundation for v4 conflict resolution. | Freehand arbitrary verbs; OR simple prose claims without verbs. |
| **V2-D 2.3** | Mandatory citations on every synthesis row: `source_episode_ids` must be a non-empty list of valid episode UUIDs. | Ari must be able to explain *how* she came to believe a claim. Uncitated syntheses are hallucinations masquerading as memory. | Permitting uncited global beliefs. |
| **V2-D 2.4** | Dual retrieval in `memoir_recall`: returns an `[Episodes]` block followed by a `[Syntheses]` block within the byte-pinned string response. | Callers should not need to know whether an insight is stored as a raw episode or a consolidated synthesis. A unified recall query surfaces both. | Separate `memoir_recall_syntheses` tool (adds unnecessary tool cognitive load). |
| **V2-D 2.5** | Background worker `MemoirReflectionWorker` clones `MemoirEmbeddingWorker` architecture: lifespan task on `app.state`, dedicated `ThreadPoolExecutor(max_workers=1, thread_name_prefix="memoir-reflect")`, per-tick exception catching, call-time knob re-reading (`ENSEMBLE_MEMOIR_REFLECTION_ENABLED`). | `asyncio.to_thread` is forbidden in worker modules (D3.5); dedicated thread pool isolates long-running LLM reflection from the main event loop. | Synchronous reflection in request handlers; OR spawning raw unmanaged asyncio tasks. |
| **V2-D 2.6** | Composed cost bounds & token metering: pass bounded by `max_syntheses` (5), `max_chars` (1000), `max_llm_calls` (4), `wall_clock_cap_s` (45s per call), and measured via `daemon.loader.estimate_tokens` (cl100k tiktoken). | Ensemble lacks a global USD cost table. Composed bounds prevent runaway token consumption deterministically. | Unbounded reflection loops; OR complex external cost tracking proxies. |
| **V2-D 2.7** | Advisory foreign belief import constraint (V2-Q9): any imported belief must be recorded as a `CORROBORATE`-class synthesis row explicitly citing the foreign origin. | `v4-federated-sketch.md` §2 import semantics. Enforcing this one-line validation rule now ensures v2 writers never emit uncitated copies. | Ignoring v4 design pressure; OR silent in-place copy. |

---

## 3. Domain Detail & Reflection Execution

### 3.1 Dual Cadence Architecture
```text
Conversation Write ──> memoir_write ──> importance added to rolling counter
                              │
                    Sum >= 25 & Idle >= 60s
                              │
                              ▼
                 [In-Day Idle Debounce Pass] ──> syntheses written
                              ▲
                              │
                    Quiet Hours (02:00 UTC)
                              │
                              ▼
                 [Nightly Auto-Dream Pass] ───> bounded 2-day consolidation
```

### 3.2 Reflection Prompt & Parsing Specification
- **Prompt:** Instructs the LLM to analyze recent unreflected episodes (and active syntheses) to identify:
  1. New durable claims (`CREATE`).
  2. Evidence reinforcing prior claims (`CORROBORATE`).
  3. Narrowing or nuance adjustments (`REFINE` with `supersedes=<id>`).
  4. Direct contradictions or errors (`CORRECT` with `supersedes=<id>`).
- **Extraction:** Tolerant JSON parsing via `_extract_json_object` (`daemon/services/skill_search_service.py:374`).
- **Purity & Validation:**
  - Every synthesis must contain: `claim` (str, 1..1000 chars), `source_episode_ids` (list[str], len ≥ 1), `verb` (in `{CREATE, CORROBORATE, REFINE, CORRECT}`), `confidence` (float 0.1..1.0).
  - If invalid, row is dropped and logged; parse failure falls back cleanly without crashing the tick.

### 3.3 Dual Retrieval Formatting in `memoir_recall`
When Ari calls `memoir_recall(query="...", k=5)`:
1. Lexical ∥ semantic hybrid search runs over `memoir_episodes` (RRF fused, existing v1 pipeline).
2. Lexical search runs over `memoir_syntheses` (`tsvector` or text match on `claim`).
3. Results are rendered into the standard byte-pinned envelope:
   ```text
   Memoir recall results for: <query>
   
   --- SYNTHESES (Beliefs & Consolidated Knowledge) ---
   [synthesis:SYN_ID] (<verb>, confidence=<conf>) <claim>
     sources: EP_ID1, EP_ID2
   
   --- EPISODES (Narrative Memory) ---
   [episode:EP_ID] <title> (event_at=<date>, importance=<N>)
   <body>
   
   Footer: retrieved N episodes, M syntheses ...
   ```

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T2.1** | Implement `MemoirReflectionService` with rolling importance counter, idle-debounce doubling timer, and nightly scan selector. | Service triggers in-day pass when importance sum ≥ 25 and idle window expires; resets timer upon new episode. | `daemon/services/memoir_reflection_service.py` | P1 | R3 | AC1 |
| **T2.2** | Implement reflection generation pipeline using `invoke_raw_with_failover`, tolerant JSON extraction, and structured synthesis validation. | LLM completion converts into validated `MemoirSynthesis` objects; uncitated rows rejected. | `daemon/services/memoir_reflection_service.py` | T2.1 | R2, R3 | AC2 |
| **T2.3** | Implement `MemoirReflectionWorker` as an `app.state` lifespan task cloning `MemoirEmbeddingWorker` pattern. | Worker ticks every 30s; re-reads knob; executes reflection when triggered; parks cleanly when knob is OFF. | `daemon/services/memoir_reflection_worker.py`, `daemon/api.py` | T2.2 | R4 | AC3 |
| **T2.4** | Add configuration settings in `daemon/config.py`: `ENSEMBLE_MEMOIR_REFLECTION_ENABLED`, `MEMOIR_REFLECTION_IDLE_BASE_S`, `MEMOIR_REFLECTION_MAX_SYNTHESES_PER_PASS`, etc., adhering to DR-3 truth table. | Knob defaults to ON with warning; invalid values fail-closed with warning; values discoverable at runtime. | `daemon/config.py`, `daemon/tools/memoir_knob.py` | — | R7 | AC4 |
| **T2.5** | Update `daemon/tools/memoir.py` and `daemon/services/memoir_retrieval.py` to retrieve active syntheses alongside episodes and format dual-block response. | `memoir_recall` returns both `--- SYNTHESES ---` and `--- EPISODES ---` sections; footer correctly accounts for both. | `daemon/tools/memoir.py`, `daemon/services/memoir_retrieval.py` | T2.2 | — | AC5 |
| **T2.6** | Implement advisory foreign-belief validation in `MemoirSynthesesRepository` (V2-Q9): imported claims require non-empty foreign source citations and `CORROBORATE` verb. | Import without foreign citation is rejected. | `daemon/repositories/memoir/syntheses_repository.py` | P1 | — | AC6 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | Idle-debounce doubling: 1 episode with importance 10 does NOT trigger; 3 episodes totaling importance 26 trigger after 60s idle; arriving episode during idle doubles window to 120s; completing reflection resets counter to 0. |
| **AC2** | Synthesis write purity: All generated syntheses written to DB carry valid non-empty `source_episode_ids`; `REFINE` and `CORRECT` verbs update `superseded_by` on prior synthesis; confidence clamped 0.0..1.0. |
| **AC3** | Worker lifecycle: Starts during `daemon/api.py` lifespan; catches per-tick exceptions without dying; knob OFF causes worker to log park message and sleep; knob ON resumes execution. |
| **AC4** | Knob DR-3 truth table: Unset -> True + warning; "false" -> False; "garbage" -> False + warning; call-time updates take effect within 1 tick. |
| **AC5** | Dual recall contract: `memoir_recall` output contains both typed blocks; if syntheses match, `--- SYNTHESES ---` appears first; footer discloses both episode count and synthesis count; byte-pinned envelope format preserved. |
| **AC6 (Mutation)** | Bypassing citation check in synthesis generation flips AC2 RED; changing debounce trigger threshold flips AC1 RED; disabling dual recall in `memoir.py` flips AC5 RED. |

---

## 6. Test Packs

- `test/packs/memoir_v2_reflection_service_test.sh`: Tests debounce doubling, threshold triggers, prompt composition, and JSON extraction.
- `test/packs/memoir_v2_reflection_worker_test.sh`: Tests worker lifespan, tick scheduling, exception isolation, and kill-switch park/resume.
- `test/packs/memoir_v2_dual_recall_test.sh`: Tests dual retrieval formatting in `memoir_recall`, synthesis ranking, and footer counts.
- `test/packs/memoir_v2_syntheses_lifecycle_test.sh`: Tests verb transitions, supersedes chain traversal, and citation validation.

---

## 7. Flag Consumption

- `ENSEMBLE_MEMOIR_REFLECTION_ENABLED`: Discrete kill-switch for reflection.
- **V2-5:** Dual-cadence reflection engine.
- **V2-6:** Mandatory citations on syntheses.
- **V2-7:** Dual retrieval in `memoir_recall`.
- **V2-13:** Well-formed supersedes chains per `v4-federated-sketch.md` §5.
- **V2-Q9:** Advisory foreign-belief import rule.
