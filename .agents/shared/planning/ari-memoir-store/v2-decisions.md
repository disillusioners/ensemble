# decisions.md — Memoir Store v2 "The Reflecting Mind" — Rulings, Embedded Decisions & Adjudication Log

**Lane:** `feature/memoir-v2-reflecting-mind` @ worktree `/home/nea/ensemble-src-wt-memoir-v2`  
**Base commit:** `f49006b922492f72ca032c56e26d20468f7a3afd` (branched from `origin/latest`)  
**Status:** **PHASE A — BLESSED SCOPE CAPTURE & ARCHITECT INTAKE.** Scope ratified by Emperor on **2026-11-09**.  
**Companions:** `v2-plan-overview.md` (the v2 implementation plan), `v2-research-findings.md` (technical digest), `v4-federated-sketch.md` (sibling deliverable on disk — R3 discharge, committed together in this lane).  
**Predecessor artifacts (FROZEN):** The 24 v1 planning documents under `.agents/shared/planning/ari-memoir-store/` remain frozen and are superseded only to the extent stated in this document.

---

## 1. The Emperor's Rulings — Authority Stack Top (VERBATIM, BINDING)

The constitutional rulings R1–R4 from 2026-11-04 remain the supreme authority of the Memoir Store:

> **R1.** KINGDOM-LEVEL ONLY in v1/v2 — single daemon, local Postgres, NO federation/sync. Format choices must not foreclose the Constella-era door (markdown+hash mirror is v3, introduced-not-synced).
>
> **R2.** KEEPSAKE GOVERNANCE = BOTH: user (via Ari in chat) and ari (via tool) may pin/unpin. pinned_by ∈ {empress, emperor}.
>
> **R3.** ROLLING ROADMAP DOCTRINE: build v1, plan v2+v3 ahead; plan v4 when v2 starts. Doctrine: "crafting into future correct and improve the present plan."
>
> **R4.** LEARN-NOT-ADOPT: no new external memory dependencies. Postgres only; no graph DB; no new services.

### Authority Stack Hierarchy
```text
Level 1: R1–R4 (Emperor, 2026-11-04)
Level 2: Blessed v2 Scope Directive (Emperor, 2026-11-09)
Level 3: Sibling R3 Constraint (v4-federated-sketch.md §5 KEEP-V2-OPEN checklist)
Level 4: Architect Adjudication of Open Questions (architecture enrichment)
Level 5: This Plan Set (v2-plan-overview.md, v2-phaseN-plan.md, v2-decisions.md)
Level 6: Code Implementation
```
Any proposal violating Levels 1–2 is invalid by construction.

---

## 2. Decision Protocol (House Standard P-1 to P-7)

| Rule | Statement |
|---|---|
| **P-1** | The blessed v2 scope is binding input. Silence is not permission to invent — it triggers an architect open question and adjudication. |
| **P-2** | **No silent deviation.** Any deviation from the blessed scope or roadmap sketch that is not explicitly recorded here is a defect. |
| **P-3** | Deviations and architectural choices are adjudicated by the **architect**, not by the implementing worker. |
| **P-4** | Adjudication is **surgical**: it supersedes only the specific clause addressed; all other frozen and blessed text stands. |
| **P-5** | Constitutional rulings R1–R4 cannot be adjudicated downward by any architect. Only the Emperor can modify R1–R4. |
| **P-6** | Open questions carried into implementation live in §4 below as `PENDING-ARCHITECT` entries and must be resolved before implementation phases close. |
| **P-7** | Recorded line numbers are dated leads — **grep at use time**. Anchor drift is not a design deviation; it requires a re-grep and a dated note. |

---

## 3. Decisions Embedded in Blessed v2 Scope (Attributed & Indexed)

| # | Decision (as blessed) | Alternative NOT Taken | Reason / Architectural Grounding |
|---|---|---|---|
| **V2-1** | **Scope Ruling:** Blessed v2 scope (2026-11-09) is authoritative. Items from v1 roadmap's v2 sketch not in blessed scope (`memoir_thread`, dedicated domain taxonomy, searchable archive) are **deferred to v3**, NOT silently dropped. | Build `memoir_thread` and archive tier now; OR silently drop them. | Rolling roadmap doctrine (R3): scope discipline requires explicit deferral. Epistemic links between episodes are already captured via `memoir_line` and synthesis citations (`source_episode_ids`). |
| **V2-2** | **No Graph Database (R4):** Constellation is implemented via 3 relational tables (`memoir_star`, `memoir_star_alias`, `memoir_line`) using 1–2 hop SQL joins. | Graph DB (Neo4j, Memgraph, etc.) or embedded vector graph. | Constitutional ruling R4: Postgres only; no new external memory services. 1–2 hop relational queries suffice at personal scale. |
| **V2-3** | **Cold-Path Graph Drawing:** Constellation nodes and edges are updated ONLY during background reflection passes, never during hot `memoir_write`. | Real-time graph indexing on every write. | Preserves DD-20 invariant: hot append must never block the conversation turn. Graph consolidation cost scales with change, not corpus size. |
| **V2-4** | **LightRAG M4 Two-Tier Star Descriptions:** Plain join of episode fragments while small; LLM summarization into a consolidated entity card only after fragment thresholds are reached. | LLM summarize on every mention; OR never summarize (raw concatenation forever). | Balances token cost and card coherence. Small entities don't need expensive LLM calls; large entities need coherent condensation. |
| **V2-5** | **The Dream Dual-Cadence Reflection:** (a) In-day idle-debounce with warm-up doubling (`1→2→4→8→N` base 60s); (b) Nightly bounded pass (ReMe `auto_dream` shape: 2-day lookback, strict caps). | Fixed periodic cron (e.g. hourly); OR continuous background loop. | A fixed cron over-reflects on quiet days and under-reflects on busy days. Debounce matches reflection rhythm to conversation activity while nightly pass bounds costs. |
| **V2-6** | **Citations Mandate on Syntheses:** Every synthesis row in `memoir_syntheses` MUST populate `source_episode_ids` with a non-empty JSON list of episode UUIDs. | Freehand synthesis claims without citations. | Provenance is non-negotiable. Ari must be able to answer "what do I believe, and *how did I come to believe it*?" Forensic traceability. |
| **V2-7** | **Dual Retrieval in `memoir_recall`:** Recall queries return both matching episodes and matching syntheses in clearly typed, distinct blocks within the byte-pinned envelope. | Separate `memoir_recall_syntheses` tool; OR synthesize on the fly at recall time. | Reuses existing caller ergonomics. A single memory recall should surface both raw narrative memories and consolidated beliefs. |
| **V2-8** | **`memoir_about` Refusal Mapping:** Map all `memoir_about` error conditions onto the existing 19-code refusal vocabulary (`MEMOIR_CODES`). | Add a 20th error code (e.g. `memoir_star_not_found`). | Preserves the frozen 19-code contract (`assert len(MEMOIR_CODES) == 19`), avoiding widespread test churn across dozens of assertion sites. |
| **V2-9** | **The Persona Gate (JEV):** Reflection may propose persona updates, but only when changes represent `conflict`, `boundary_change`, or `new_rule`. All proposals land `pending_approval` and require HUMAN session approval (`auth_method == "session"`). | Auto-apply persona edits; OR allow live model to edit self directly. | The Hy-Memory production scar: a live model freehand-editing its durable self drifts into incoherence. Human oversight preserves identity stability. |
| **V2-10** | **Asynchronous Poignancy Scoring:** Episode importance is scored 1–10 by an LLM in a background worker (`MemoirPoignancyWorker`), defaulting to 5 at write time. | Synchronous LLM scoring during `memoir_write`. | Preserves DD-20 hot-path latency. The conversation turn completes in milliseconds; poignancy arrives seconds later in the background. |
| **V2-11** | **Keepsake Proposals Governance:** Reflection proposes candidate keepsakes (poignancy ≥ 8); surfaced via briefing proposals zone + at most ONE gentle `notify_user` per day. Never auto-pinned. | Automatic pinning by the model; OR frequent chat notifications. | Ruling R2: pin authority belongs exclusively to Empress and Emperor. Anti-spam constraint protects chat ergonomics. |
| **V2-12** | **Isolated Subsystem Kill-Switches (DR-3):** Discrete environment variables for reflection, poignancy, and wake-refresh, each replicating the DR-3 truth table (fail-closed on unparseable garbage). | One global `ENSEMBLE_MEMOIR_ENABLED` switch for everything. | Allows surgical isolation during incidents (e.g. disable reflection LLM calls without disabling memory recall or episodic storage). |
| **V2-13** | **Ingestion of v4 Backward Design Pressure (`v4-federated-sketch.md` §5):** Adopt the 6 architectural constraints of the KEEP-V2-OPEN checklist: (1) Deterministic star canonicalization (canonical name is a deterministic function of the alias set + type); (2) Well-formed, fully-cited supersedes chains (the conflict-resolution primitive); (3) Two-axis time (`created_at` / `event_at` or `first_seen_at` / `last_touched_at`) on all new v2 tables; (4) Vocabulary discipline: keep episode `stars` column distinct from `memoir_star` entity rows in schema, code, and docs; (5) Heat and access stats are strictly kingdom-local; (6) All rendered artifacts must round-trip a markdown+hash mirror expression. | Ignore future v4 federation constraints; invent freeform canonical names; drop two-axis time. | Constitutional ruling R3: "crafting into future correct and improve the present plan." Decisions made cheaply now prevent costly schema demolition in v3/v4. |

---

## 4. Open Architect Questions & Adjudication Log

The following 9 items are carried into the architect enrichment phase. Each item states the dilemma, candidate options, verified evidence, and the recommended planning baseline.

### V2-Q1: Wake-Refresh Delivery Lane (Carried as PENDING-ARCHITECT)
- **Question:** How should the wake briefing be refreshed on self-alarm wakes into an existing session?
- **Candidate Options:**
  - *Option A (Alarm Template Compose):* Modify `compose_alarm_message` in `self_alarm_service.py` / `scheduler.py` to inject the briefing into the alarm message text.
  - *Option B (Parallel Stamped Refresh Block — RECOMMENDED):* Add `_maybe_memoir_refresh_message` in `context_messages.py` beside `_maybe_memoir_briefing_message`, evaluated at both assembly sites (:2564–2570 and :2928–2934), emitting a new `CONTEXT_KIND_MEMOIR_REFRESH`.
- **Evidence:**
  - L4 Landmine: Scheduler wakes are deliberately unstamped bare `MessageType.HUMAN` to preserve attestation source U (`constants.py:1376-1399`, pinned by `test_source_stamp_never_stamped_guards.py`). Touching the alarm template violates this product ruling and freezes memory at alarm ARM time.
  - Precedent: Option B mirrors the POK refresh pattern (`_maybe_pok_context_message` in `context_messages.py:2572-2588`), which successfully performs per-turn context re-evaluation without touching message stamps.
- **Recommended Adjudication:** **Adopt Option B.** Clean context block, evaluated at wake time, compaction-hoistable, zero interference with human message attestation.

### V2-Q2: Poignancy Reflection Triggering & Warm-Up Curve Constants (Carried as PENDING-ARCHITECT)
- **Question:** What are the exact triggering thresholds for cumulative importance and the idle-debounce doubling parameters?
- **Candidate Options:**
  - Option A: Static fixed thresholds.
  - Option B: Dynamic adaptive curves.
- **Evidence:**
  - Generative Agents literature recommends cumulative importance ~15–25 to trigger reflection. In ensemble, episodes range 1–10.
  - Base idle debounce: 60s is standard for interactive pauses. Doubling `1→2→4→8→16` gives intervals of 60s, 120s, 240s, 480s, 960s (ceiling 16 minutes).
- **Recommended Constants:**
  - `MEMOIR_REFLECTION_TRIGGER_IMPORTANCE_SUM = 25` (e.g. ~3 high-importance episodes or ~5 average episodes trigger reflection).
  - `MEMOIR_REFLECTION_IDLE_BASE_S = 60.0`
  - `MEMOIR_REFLECTION_IDLE_CEILING_S = 960.0`
  - Warm-up factor: doubles on new episode arrival during active debounce; resets to base after reflection completes.

### V2-Q3: Budget Numerology for Reflection Passes (Carried as PENDING-ARCHITECT)
- **Question:** What are the hard upper bounds on synthesis output, scan windows, and execution budgets per pass?
- **Recommended Constants:**
  - `MEMOIR_REFLECTION_MAX_SYNTHESES_PER_PASS = 5` (bounds database write volume).
  - `MEMOIR_SYNTHESIS_MAX_CHARS = 1000` (~250 tokens per claim).
  - `MEMOIR_REFLECTION_SCAN_WINDOW_DAYS = 2` (bounds episode intake to ~48 hours).
  - `MEMOIR_REFLECTION_MAX_LLM_CALLS_PER_PASS = 4` (1 episode batch synthesis, 1 star consolidation, 1 JEV classification, 1 keepsake check).
  - `MEMOIR_REFLECTION_TICK_BUDGET_S = 60.0` (with per-call `httpx.Timeout(45.0, connect=5.0)`).
  - Nightly pass bounds: Max 30 episodes evaluated; scheduled during off-peak window (02:00–04:00 UTC).

### V2-Q4: JEV Classifier Placement & Execution Strategy (Carried as PENDING-ARCHITECT)
- **Question:** Where does the JEV classifier live, and should it run as a single combined LLM call with synthesis or a separate pipeline step?
- **Recommended Architecture:**
  - Place in `daemon/services/memoir_persona_gate.py` called by `memoir_reflection_service.py`.
  - Two-stage execution:
    1. *Stage 1 (Rule-based filter):* Compute diff between candidate persona belief and current approved persona text. If text delta is formatting, whitespace, or verbatim substring overlap -> classify as `reinforcement` or `none` and exit immediately (zero LLM calls).
    2. *Stage 2 (LLM Classifier):* If text introduces novel behavioral claims, invoke LLM classifier with prompt returning `{category: conflict|boundary_change|new_rule|reinforcement|none, reasoning: "..."}`.
  - Run as a separate modular call AFTER synthesis. Only executed if reflection produced a candidate persona belief.

### V2-Q5: Constellation Two-Tier Thresholds (LightRAG M4 Pattern) (Carried as PENDING-ARCHITECT)
- **Question:** At what fragment count and character volume does star description graduate from plain-join to LLM summarization?
- **Recommended Constants:**
  - `MEMOIR_STAR_SUMMARY_MIN_FRAGMENTS = 5` mentions/lines.
  - `MEMOIR_STAR_SUMMARY_MIN_CHARS = 1200` characters total.
  - `MEMOIR_STAR_SUMMARY_COOLDOWN_HOURS = 24` (do not re-summarize an already-summarized star unless at least 24 hours have passed and new mentions exist).
  - Below threshold: `memoir_about` simply joins recent episode excerpts.
  - Above threshold: Background reflection summarizes into a concise entity card (~300 words).

### V2-Q6: Cost-Bound Composition Without a Global Cost Ledger (Carried as PENDING-ARCHITECT)
- **Question:** How do we guarantee reflection stays within budget given that ensemble lacks a per-service USD cost ledger table?
- **Recommended Composed Bound:**
  1. Structural clamps: `max_syntheses` (5) + `max_chars` (1000) + `max_llm_calls` (4) + `timeout` (45s) + `tick_budget` (60s).
  2. Software token meter: Use `daemon.loader.estimate_tokens` (`cl100k_base` tiktoken) inside the worker to meter prompt and completion tokens.
  3. Structured logging: Log every pass tick: `[MemoirReflection] pass=completed units=K prompt_tokens=X comp_tokens=Y duration=Z.Zs`.

### V2-Q7: `memoir_about` Refusal Vocabulary Mapping (Carried as PENDING-ARCHITECT)
- **Question:** Should `memoir_about` map onto the existing 19-code frozen vocabulary or introduce a 20th code?
- **Recommended Adjudication:** **Map onto existing 19 codes.**
  - Star not found -> `memoir_not_found`
  - Empty star argument -> `memoir_query_required`
  - Invalid / too long star name -> `memoir_stars_out_of_bounds`
  - Disabled / Unauthorized / Internal -> `memoir_disabled`, `memoir_not_authorized`, `memoir_internal_error`.
  - Preserves the frozen contract `assert len(MEMOIR_CODES) == 19` without opening a high-churn V-1 truthfulness bar across dozens of tests.

### V2-Q8: Scope Ruling on Wider Roadmap Items (Carried as RECORDED-DECISION)
- **Ruling:** The blessed scope (2026-11-09) governs v2. `memoir_thread` (episode-to-episode continuity) is deferred to v3 because episode-to-star lines (`memoir_line`) and synthesis citations (`source_episode_ids`) already provide episodic connectivity. Searchable archive tier is explicitly v3 per `roadmap.md`. These deferrals are recorded here under R3.

### V2-Q9: Foreign Belief Import Semantics as CORROBORATE-Class Act (ADVISORY — PENDING-ARCHITECT)
- **Question:** Should the v4 sketch's §2 import-semantics rule ("a foreign belief lands as a NEW local synthesis row citing the foreign original — an import is a CORROBORATE-class act") land NOW as a one-line structural constraint on v2's syntheses writer, or remain unratified v4 design pressure?
- **Evidence:** `v4-federated-sketch.md` §2 notes that foreign beliefs must never be silently copied into local chains; each kingdom's chain stays kingdom-local and gains a citable cross-kingdom edge. In v2, the synthesis writer is being created fresh.
- **Recommended Adjudication:** **Carry as ADVISORY constraint.** Encode a one-line validation rule in `MemoirSynthesesRepository`: an imported/corroborated belief must cite its source. This costs one sentence in the code and specification, adds zero architectural overhead, and forecloses nothing for v3/v4.
