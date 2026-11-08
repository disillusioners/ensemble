# decisions.md — Memoir Store v2 "The Reflecting Mind" — Rulings, Embedded Decisions & Adjudication Log

**Lane:** `feature/memoir-v2-reflecting-mind` @ worktree `/home/nea/ensemble-src-wt-memoir-v2`  
**Base commit:** `f49006b922492f72ca032c56e26d20468f7a3afd` (branched from `origin/latest`)  
**Status:** **PHASE B — ADJUDICATED IMPLEMENTATION SPECIFICATION.** Scope ratified by Emperor on 2026-11-09; architect adjudication folded from `v2-architecture-recommendation.md` (commit `3cdbcb712`); leader rulings and review council repairs incorporated 2026-11-09.  
**Companions:** `v2-plan-overview.md` (the v2 implementation plan), `v2-architecture-recommendation.md` (Level-4 authority), `v2-research-findings.md` (technical digest), `v4-federated-sketch.md` (sibling deliverable on disk — R3 discharge, committed together in this lane).  
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
Level 4: Architect Adjudication (v2-architecture-recommendation.md — 9 rulings, 7 addenda)
Level 5: Leader Rulings & Review Council Adjudications (2026-11-09)
Level 6: This Plan Set (v2-plan-overview.md, v2-phaseN-plan.md, v2-decisions.md)
Level 7: Code Implementation
```
Any proposal violating Levels 1–2 is invalid by construction. Level 4/5 supersedes Level 6 surgically (P-4).

---

## 2. Decision Protocol (House Standard P-1 to P-7)

| Rule | Statement |
|---|---|
| **P-1** | The blessed v2 scope is binding input. Silence is not permission to invent — it triggers an architect open question and adjudication. |
| **P-2** | **No silent deviation.** Any deviation from the blessed scope or roadmap sketch that is not explicitly recorded here is a defect. |
| **P-3** | Deviations and architectural choices are adjudicated by the **architect** or **leader**, not by the implementing worker. |
| **P-4** | Adjudication is **surgical**: it supersedes only the specific clause addressed; all other frozen and blessed text stands. |
| **P-5** | Constitutional rulings R1–R4 cannot be adjudicated downward by any architect. Only the Emperor can modify R1–R4. |
| **P-6** | Open questions carried into implementation live in §4 below and are marked `RESOLVED` once adjudicated. |
| **P-7** | Recorded line numbers are dated leads — **grep at use time**. Anchor drift is not a design deviation; it requires a re-grep and a dated note. |

---

## 3. Decisions Log (Attributed & Indexed)

| # | Decision | Alternative NOT Taken | Reason / Architectural Grounding |
|---|---|---|---|
| **V2-1** | **Scope Ruling:** Blessed v2 scope (2026-11-09) is authoritative. Items from v1 roadmap's v2 sketch not in blessed scope (`memoir_thread`, dedicated domain taxonomy, searchable archive) are **deferred to v3**, NOT silently dropped. | Build `memoir_thread` and archive tier now; OR silently drop them. | Rolling roadmap doctrine (R3): scope discipline requires explicit deferral. Epistemic links between episodes are already captured via `memoir_line` and synthesis citations (`source_episode_ids`). |
| **V2-2** | **No Graph Database (R4):** Constellation is implemented via 3 relational tables (`memoir_star`, `memoir_star_alias`, `memoir_line`) using 1–2 hop SQL joins. | Graph DB (Neo4j, Memgraph, etc.) or embedded vector graph. | Constitutional ruling R4: Postgres only; no new external memory services. 1–2 hop relational queries suffice at personal scale. |
| **V2-3** | **Cold-Path Graph Drawing:** Constellation nodes and edges are updated ONLY during background reflection passes, never during hot `memoir_write`. | Real-time graph indexing on every write. | Preserves DD-20 invariant: hot append must never block the conversation turn. Graph consolidation cost scales with change, not corpus size. |
| **V2-4** | **LightRAG M4 Two-Tier Star Descriptions:** Plain join of episode fragments while small; LLM summarization into a consolidated entity card only after fragment thresholds are reached. | LLM summarize on every mention; OR never summarize (raw concatenation forever). | Balances token cost and card coherence. Small entities don't need expensive LLM calls; large entities need coherent condensation. |
| **V2-5** | **The Dream Dual-Cadence Reflection:** (a) In-day idle-debounce with warm-up doubling (`1→2→4→8→16` base 60s, ceiling 960s) + force-fire at 3600s; (b) Nightly bounded pass (ReMe `auto_dream` shape: 2-day lookback, strict caps, 02:00–04:00 UTC). Trigger is DERIVED per tick (`SUM(COALESCE(poignancy, importance)) >= 25`), deleting rolling counter state. | Fixed periodic cron (e.g. hourly); OR continuous background loop; OR in-memory counter. | A fixed cron over-reflects on quiet days and under-reflects on busy days. Debounce matches reflection rhythm to conversation activity. Derived sum is restart-durable (§3-A1). |
| **V2-6** | **Citations Mandate on Syntheses:** Every synthesis row in `memoir_syntheses` MUST populate `source_episode_ids` with a non-empty JSON list of episode UUIDs. | Freehand synthesis claims without citations. | Provenance is non-negotiable. Ari must be able to answer "what do I believe, and *how did I come to believe it*?" Forensic traceability. |
| **V2-7** | **Dual Retrieval in `memoir_recall`:** Recall queries return both matching episodes and matching syntheses in clearly typed, distinct blocks within the byte-pinned envelope. Lane OFF preserves byte-identical v1 output. | Separate `memoir_recall_syntheses` tool; OR synthesize on the fly at recall time. | Reuses existing caller ergonomics. A single memory recall should surface both raw narrative memories and consolidated beliefs. |
| **V2-8** | **`memoir_about` Refusal Mapping:** Map all `memoir_about` error conditions onto the existing 19-code refusal vocabulary (`MEMOIR_CODES`). | Add a 20th error code (e.g. `memoir_star_not_found`). | Preserves the frozen 19-code contract (`assert len(MEMOIR_CODES) == 19`), avoiding widespread test churn across dozens of assertion sites. |
| **V2-9** | **The Persona Gate (JEV):** Reflection may propose persona updates, but only when changes represent `conflict`, `boundary_change`, or `new_rule`. All proposals land `pending_approval` and require HUMAN session approval (`auth_method == "session"`). | Auto-apply persona edits; OR allow live model to edit self directly. | The Hy-Memory production scar: a live model freehand-editing its durable self drifts into incoherence. Human oversight preserves identity stability. |
| **V2-10** | **Asynchronous Poignancy Scoring & Importance Ruling (§3-A2):** `importance` stays WRITE-FROZEN (authored record preserved); LLM score (1–10) lands in a NEW `poignancy INTEGER NULL` column; all v2 consumers read effective importance = `COALESCE(poignancy, importance)`. Default 5 at write time. **DISCLOSED SEMANTIC (Leader ruling 2026-11-09):** The storage detail divergence (score in `poignancy`, not mutating `importance`) stands as-is; disclosure rides the leader's milestone report to the Emperor. | Overwriting `importance` in place. | Overwriting `importance` REDs the frozen `RECORD_COLUMNS` purity pin and corrupts authored historical judgment. COALESCE preserves capability semantically (§3-A2). |
| **V2-11** | **Keepsake Proposals Governance:** Reflection proposes candidate keepsakes (effective poignancy ≥ 8); surfaced via briefing proposals zone + at most ONE gentle `notify_user` per day. Never auto-pinned. | Automatic pinning by the model; OR frequent chat notifications. | Ruling R2: pin authority belongs exclusively to Empress and Emperor. Anti-spam constraint protects chat ergonomics. |
| **V2-12** | **Isolated Subsystem Kill-Switches (DR-3):** Discrete environment variables for reflection, poignancy, and wake-refresh, each replicating the DR-3 truth table (fail-closed on unparseable garbage). | One global `ENSEMBLE_MEMOIR_ENABLED` switch for everything. | Allows surgical isolation during incidents (e.g. disable reflection LLM calls without disabling memory recall or episodic storage). |
| **V2-13** | **Ingestion of v4 Backward Design Pressure (`v4-federated-sketch.md` §5):** Adopt the 6 architectural constraints of the KEEP-V2-OPEN checklist: (1) Deterministic star canonicalization; (2) Well-formed, fully-cited supersedes chains; (3) Two-axis time; (4) Vocabulary discipline: episode `stars` column vs `memoir_star` entity rows; (5) Heat and access stats are strictly kingdom-local; (6) All rendered artifacts must round-trip a markdown+hash mirror expression. | Ignore future v4 federation constraints; invent freeform canonical names; drop two-axis time. | Constitutional ruling R3: "crafting into future correct and improve the present plan." Decisions made cheaply now prevent costly schema demolition in v3/v4. |
| **V2-14** | **Cutover-Bounded Legacy Seed & Opt-In Backfill (§3-A3 / Register #5):** Seed pre-existing episodes via `UPDATE memoir_episodes SET poignancy_status='legacy' WHERE poignancy_status IS NULL AND created_at < '2026-11-09 00:00:00'`. Opt-in backfill via `MEMOIR_POIGNANCY_BACKFILL_MAX=0` (default disabled). | Full-history automatic backfill on startup. | Mass-scoring history is an LLM storm (drain ceiling 43k/day), violates the write-frozen record contract, and instantly floods the reflection trigger sum with stale events. |
| **V2-15** | **Atomic Supersession `supersede_with(old_id, new_row)` (§3-A2 / Register #6):** One single-transaction repository writer performing insert of new synthesis + updating old-row `superseded_by` pointer + new-row `supersedes` back-pointer in ONE `engine.begin()`. | Two-call shape (`create_synthesis` then `mark_superseded`). | Two-call shape leaves an unlinked fork if the process dies between calls, violating the v4 conflict-resolution invariant. |
| **V2-16** | **Three-Class Numeric Knob Governance & Shared Before-Validator (DR-3 / §3-A4 / Register #12):** ONE shared `_classify_numeric` helper in `daemon/config.py` consumed by `mode="before"` Pydantic validator on every v2 numeric field. Three classes: Enabled flags (unset ON, garbage fail-closed OFF); Budget bounds (unset aggregate warning, garbage clamp to default); Cadence tunables (unset default, garbage default + warning). Never boot-abort. | Bare `Field(gt=...)` on numerics (pre-existing boot-abort defect, anomaly `33028b27`). | Bare `Field` raises `ValidationError` and aborts boot on unparseable numeric env vars, violating DR-3. |
| **V2-17** | **Synthesis Insertion Dedup Key (§2-Q3 / Register #13):** Enforce dedup key `sha256(claim_normalized + sorted(source_episode_ids))` before inserting synthesis claims. | Inserting without content dedup. | Converts at-least-once reflection retries into safe no-ops instead of duplicate claim proliferation. |
| **V2-18** | **Briefing Proposals Zone Trim-Rung Priority (§3-A6 / Register #14):** Proposals zone is its OWN measured section (≤ 1000 chars). Explicit trim-rung order: fit dynamic → trim dynamic → trim proposals (weakest first) → rung-2 withhold (>48k). | Inserting proposals into stable text or unmeasured zone. | Prevents proposals from prematurely tripping the 24,000-character ceiling and silently wiping all dynamic episodes. |
| **V2-19** | **Durable Reflection State & Per-Episode Watermark (§3-A1 / Register #15):** Durable tracking via `memoir_episodes.reflected_at TIMESTAMPTZ NULL` + `memoir_reflection_state` row (`id='ari'`, `last_nightly_date`, `claimed_at`). | Storing rolling counters in memory; OR polling un-indexed history. | Restart-safe, idempotent, and self-clearing upon pass completion. |
| **V2-20** | **JEV Candidate Parking on Gate Failure (§2-Q4 / Register #16):** If the JEV classifier fails (LLM down or parse error), park the candidate record (`synthesis_id`, `attempts`, `last_error`) and retry next pass; terminalize after 5 attempts as `terminal:gate_unavailable`. | Defaulting to `none` on failure. | Defaulting to `none` silently drops candidate persona changes. Parking provides visibility and resilience. |
| **V2-21** | **Plain SQLite `ADD COLUMN` in Migration File (§3-A5 / Register #1):** The `.sql` migration file must use plain `ADD COLUMN` (never `ADD COLUMN IF NOT EXISTS`). | Using `ADD COLUMN IF NOT EXISTS` in the `.sql` migration file. | `ADD COLUMN IF NOT EXISTS` is a hard syntax error in SQLite and breaks `MigrationError` discovery. Precedent: POK `20261107_110003`. |
| **V2-22** | **Constellation Fragment Count & Cooldown Watermark (§2-Q5 / Register #9, #18):** Fragments measured as `COUNT(*)` of `memoir_line` rows (distinct citing episodes, never `SUM(mention_count)`). Add `memoir_star.description_as_of DATETIME NULL` to carry the 24h cooldown and v4 derivation watermark. | Measuring mentions via `SUM(mention_count)`; OR overloading `last_touched_at` for cooldown. | A single repetitive episode must not alone graduate a star. `last_touched_at` updates on any touch, making it unusable as a summarization cooldown clock. |
| **V2-23** | **Notification Ledger Consumed Only on Accepted Send (§3-A4 / Register #17):** The 1/day proposal notification ledger consumes its daily slot ONLY when `notify_user` successfully dispatches. | Consuming the slot on refusal or attempt. | An unbound background session or temporary refusal would permanently starve chat notifications for that calendar day. |
| **V2-24** | **Nightly-Before-In-Day Sequential Ordering (§3-A1 / Register #19):** Inside the reflection worker tick, the nightly pass gate runs FIRST, advancing episode `reflected_at` marks before in-day evaluation. | In-day evaluation before nightly; OR concurrent passes. | Nightly consolidation advances the cursor, leaving the in-day branch with sum < 25 so it idles cleanly. Sequential execution prevents pass collisions by construction. |
| **V2-25** | **`memoir_about` Zero-Episode Empty Dossier Success (§2-Q7):** A resolved star with zero linked episodes returns an empty-dossier SUCCESS render (sections say "none"), NOT `memoir_not_found`. | Returning `memoir_not_found` when a star has no episodes. | V-1 truthfulness bar: `memoir_not_found` mislabels an existing entity as absent. |
| **V2-26** | **TIMESTAMPTZ Typing for `reflected_at` in Postgres Ensure (C1):** In `_ensure_postgres_columns`, `reflected_at` is added as `TIMESTAMPTZ` (matching `DateTime(timezone=True)` in model; dated anchors `manager.py:8396/:8410`). | Using `DATETIME` in PG ensure statement. | `DATETIME` is SQLite-specific and causes DDL syntax error on PostgreSQL, aborting boot. |
| **V2-27** | **Bounded One-Time Seed Pass from v1 Corpus (C2 / Leader ruling 2026-11-09):** Nightly pass scans oldest-unreflected-first (`ORDER BY created_at ASC LIMIT 30`) without lookback window restriction until unreflected backlog is caught up, seeding Constellation entities from historical episodes under existing caps. | Requiring manual backfill script; OR skipping pre-cutover history (leaving entities missing). | Without a seed pass, historical entities like "The Emperor" return `memoir_not_found` until re-mentioned. Bounded nightly passes absorb the backlog cleanly without LLM storms. |
| **V2-28** | **Durable Proposal Ledger Table in Phase 1 (C3 / Leader ruling 2026-11-09):** Create `memoir_proposal_ledger` (`id`, `agent_id`, `episode_id`, `proposal_date`, `proposed_at`, `notification_status`, `notified_at`, `reason`, `created_at`) with `uq_memoir_proposal_agent_episode UNIQUE (agent_id, episode_id)` in Phase 1 migration. Stamped `notification_status='sent'` enforces restart-safe 1/day notification constraint, and the unique constraint enforces once-ever-per-episode proposal tracking (W2). | In-memory ledger; OR ad-hoc metadata file. | Restart-safe 1/day notification constraint and once-ever proposal tracking are blessed requirements; in-memory state is ruled out. |
| **V2-29** | **memoir_line Upsert Guard via `last_pass_id` (Should-Fix #2):** Add `last_pass_id TEXT NULL` to `memoir_line` in Phase 1 DDL. Dialect-aware upsert increments `mention_count` only when `last_pass_id IS DISTINCT FROM :pass_id`. | Plain `+= 1` on conflict without pass check. | A pass that crashes mid-flight and re-processes an episode would double-count mentions without the pass-id guard. |
| **V2-30** | **Claim Normalization & Per-Agent Window Dedup (Should-Fix #4):** Normalization = `NFC + whitespace-collapse + casefold`. Dedup enforced against active un-superseded syntheses within the 2-day lookback window (`WHERE agent_id=:ari AND created_at >= now()-interval '2 days' AND superseded_by IS NULL`). | Adding an indexed column to `memoir_syntheses`; OR skipping dedup. | Scale rationale: Reflection operates over a bounded 2-day window (~dozens of claims). An in-window query is instantaneous (<1ms) and avoids altering the pre-staged v1 syntheses table schema (honoring DD-2). |
| **V2-31** | **Constellation LLM Accounting (Should-Fix #7):** Constellation has its own bounded pass budget: `MEMOIR_CONSTELLATION_MAX_LLM_CALLS_PER_PASS = 2` (max 1 extraction, max 1 tier-2 card summarization per pass), separate from the 4-call reflection synthesis cap. Both ride `invoke_raw_with_failover`. | Shared 4-call cap; OR unmetered constellation calls. | Decoupled accounting ensures constellation extraction never starves core synthesis generation. |
| **V2-32** | **Strike Ad-hoc Router `daemon/routers/memoir.py` (Should-Fix #9):** Strike T5.5. Reuse v1 `MemoirPersonaRepository` surface where `_require_human_lane` (`repository.py:231`) already enforces `actor.auth_method == "session"`. Dated anchors: `daemon/services/prompt_version_service.py:184` (defined), `:678-716` (raised); `daemon/routers/prompts.py:51-56` (mapped to 403 `HUMAN_LANE_REQUIRED`). | Creating a new unblessed router `daemon/routers/memoir.py`. | Keeps scope disciplined; avoids creating unblessed API surfaces when repository-level human-lane enforcement is already tested and authoritative. |
| **V2-33** | **Storage-Format Space-Separated Cutover Literal (Should-Fix #11):** Use `'2026-11-09 00:00:00'` in SQLite legacy seed DML (`WHERE poignancy_status IS NULL AND created_at < '2026-11-09 00:00:00'`). | ISO-8601 with `'T'` literal (`'2026-11-09T00:00:00Z'`). | SQLite text comparison is lexicographic: `' ' < 'T'`. Comparing `'2026-11-09 12:00:00'` against `'2026-11-09T00:00:00'` would incorrectly evaluate as true, misclassifying cutover-day rows. |
| **V2-34** | **Two-Part Heat Model & Named Decay Lambda (Should-Fix #3, Suggestion #11):** Stored accumulator in `memoir_star.heat` incremented ONLY on explicit-recall hits (`memoir_recall`, `memoir_about`); recency decay computed dynamically at RENDER time: `effective_heat = round(stored_heat * exp(-MEMOIR_HEAT_DECAY_LAMBDA * days_idle), 2)` with `MEMOIR_HEAT_DECAY_LAMBDA = 0.005` (~138 days half-life). Background extraction never increments heat. | Incrementing heat during background extraction; OR baking static decay into DB updates. | Preserves DD-12: background reflection is not human attention. Dynamic decay keeps stored data append-pure. |
| **V2-35** | **Nightly Claim Stale-Takeover (Should-Fix #5):** Claims older than 15 minutes (`claimed_at < :now - interval '15 minutes'`) can be re-taken by a subsequent pass for crash recovery. | Permanent locking; OR zero-timeout claim. | Allows automatic recovery if a worker process terminates mid-pass while holding a claim. |
| **V2-36** | **Proposal Once-Everness Per Episode (W2 / Leader ruling 2026-11-09):** An episode is eligible for keepsake promotion proposal at most ONCE in its lifetime (option c). If an episode already has a row in `memoir_proposal_ledger` (regardless of outcome — accepted, rejected, stale, or ignored), reflection skips it permanently. | Re-proposing after 30 days (option b); OR unlimited re-proposals. | Rationale: No-spam doctrine and keepsake sacredness; a rejected or stale proposal must not re-nag. Re-proposal semantics can be added later if the Emperor asks. Enforced by database constraint `uq_memoir_proposal_agent_episode UNIQUE (agent_id, episode_id)`. |

---

## 4. Architect Adjudication Log (Q1–Q9 Resolved)

All 9 questions are formally adjudicated per Level-4 authority (`v2-architecture-recommendation.md` §1 and §2):

### V2-Q1: Wake-Refresh Delivery Lane — **RESOLVED: Adopt Option B, Amended (§1-Q1, §2-Q1)**
- **Adjudication:** Adopt parallel stamped refresh block (`CONTEXT_KIND_MEMOIR_REFRESH`, stable id `memoir_refresh:{instance_id}`, **no once-flag**) evaluated at both context assembly sites (:2564–2570 and :2928–2934 in `daemon/services/context_messages.py`).
- **Amendment:** Thread a new `wake_source: str | None` parameter from dispatch (`instance_messaging.py:4005-4029`) through `assemble_context_messages` to the refresh builder. Gate: `agent_id == "ari"` → `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED` → `wake_source == "scheduler"`. Do NOT use `instance_metadata.original_source` (first-write only). Option A is rejected because scheduler wakes must remain unstamped bare `MessageType.HUMAN` to preserve attestation source U (L4 product ruling).

### V2-Q2: Poignancy Reflection Triggering & Warm-Up — **RESOLVED: Adopt Constants, Amend Mechanism (§1-Q2, §2-Q2)**
- **Adjudication:** Adopt numbers: `MEMOIR_REFLECTION_TRIGGER_IMPORTANCE_SUM = 25`, base debounce 60s, doubling on arrival to ceiling 960s, reset to base after pass. Add `MEMOIR_REFLECTION_FORCE_FIRE_S = 3600.0` to eliminate indefinite deferral under continuous conversational load.
- **Mechanism Amendment:** Replace in-memory rolling counter with DERIVED SUM per tick: `SELECT COALESCE(SUM(COALESCE(poignancy, importance)), 0), MAX(created_at) FROM memoir_episodes WHERE agent_id=:ari AND reflected_at IS NULL AND created_at >= now()-interval '2 days'`. Delete V2-D 4.5 delta-notify (`score - 5`). Derived sum is restart-durable and self-resets when episodes are marked `reflected_at`.

### V2-Q3: Budget Numerology for Reflection Passes — **RESOLVED: Adopt Numbers + Binding Enforcement Table (§1-Q3, §2-Q3)**
- **Adjudication:** Adopt binding enforcement table:
  1. Max syntheses / pass: 5 (accept-loop post-validate / pre-insert; keep committed).
  2. Max chars / synthesis: 1000 (**REJECT the row**, never truncate; truncation alters meaning).
  3. Max LLM calls / pass: 4 (**increment at dispatch**, JEV classifier counts towards call cap).
  4. Tick budget: 60.0s (per-item pre-dispatch break; defer remainder).
  5. Timeout: `httpx.Timeout(45.0, connect=5.0)`.
  6. Scan window: 2 days (SQL predicate).
  7. Nightly cap: 30 episodes (scan `LIMIT 30`, window 02:00–04:00 UTC).
  8. Dedup key: `sha256(claim_normalized + sorted(source_episode_ids))` enforced on insert to ensure crash-safe at-least-once idempotence.

### V2-Q4: JEV Classifier Placement & Write Purity — **RESOLVED: Adopt Placement, Amend Failure Parking (§1-Q4, §2-Q4)**
- **Adjudication:** Place in `daemon/services/memoir_persona_gate.py`, called by `memoir_reflection_service.py`. Two-stage: rule-based filter (zero LLM calls on trivial diffs) -> LLM classifier. Exercised strictly from reflection SERVICE layer, never tool module (`daemon/tools/memoir.py` stays read/append pure).
- **Amendment:** On classifier failure, do NOT default to `none` (silently lossy); PARK candidate (`synthesis_id`, `attempts`, `last_error`), retry next pass, terminalize at 5 attempts as `terminal:gate_unavailable`.

### V2-Q5: Constellation Two-Tier Thresholds — **RESOLVED: Adopt Thresholds, Amend Carriers (§1-Q5, §2-Q5)**
- **Adjudication:** Adopt thresholds: 5 fragments, 1200 characters, 24h cooldown, tier-2 card ~300 words.
- **Amendments:**
  1. Fragments = `COUNT(*)` of distinct `memoir_line` rows (citing episodes), never `SUM(mention_count)`.
  2. Add `memoir_star.description_as_of DATETIME NULL` to carry the 24h cooldown and v4 derivation watermark. Below threshold, `memoir_about` plain-joins excerpts; `description_as_of` remains NULL until first tier-2 summarization.

### V2-Q6: Cost-Bound Composition Without a Cost Ledger — **RESOLVED: Adopt Composed Bounds (§1-Q6, §2-Q6)**
- **Adjudication:** Adopt structural clamps + `daemon.loader.estimate_tokens` metering (cl100k tiktoken) + structured per-pass log line: `[MemoirReflection] pass=completed units=K prompt_tokens=X comp_tokens=Y duration=Z.Zs syntheses=S kept=D dropped=R`. Clamp-then-meter. No ledger table (unblessed write surface). Cross-restart cost queries are a v3 boundary.

### V2-Q7: `memoir_about` Refusal Vocabulary Mapping — **RESOLVED: Adopt Frozen 19 Mapping (§1-Q7, §2-Q7)**
- **Adjudication:** Map onto existing 19 codes in `MEMOIR_CODES`: star not found -> `memoir_not_found`; empty star -> `memoir_query_required`; star >100 chars -> `memoir_stars_out_of_bounds`; disabled/unauthorized/internal -> their respective codes.
- **Edge Rule:** A resolved star with zero linked episodes returns an **empty-dossier SUCCESS render**, NOT `memoir_not_found` (V-1 truthfulness bar). `assert len(MEMOIR_CODES) == 19` untouched.

### V2-Q8: Scope Boundaries on Wider Roadmap Items — **RESOLVED: Adopt as Recorded (§1-Q8, §2-Q8)**
- **Adjudication:** Blessed scope governs. `memoir_thread` and searchable archive tier are deferred to v3 under R3 rolling doctrine. No amendment.

### V2-Q9: Foreign Belief Import Semantics — **RESOLVED: Adopt as Advisory One-Liner (§1-Q9, §2-Q9)**
- **Adjudication:** Lands now as an advisory writer-level convention in `MemoirSynthesesRepository`: an imported foreign belief is a NEW local synthesis row, verb `CORROBORATE`, citing the foreign original. Chains remain kingdom-local; no silent copies. No v2 execution machinery (no federation exists in v2, R1).
