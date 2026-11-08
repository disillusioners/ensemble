# Memoir Store v2 "The Reflecting Mind" — Plan Overview (Phase B Implementation Plan)

**Lane:** `feature/memoir-v2-reflecting-mind` @ worktree `/home/nea/ensemble-src-wt-memoir-v2`  
**Base commit:** `f49006b922492f72ca032c56e26d20468f7a3afd` (branched from `origin/latest`)  
**Status:** **AUTHORITATIVE IMPLEMENTATION SPECIFICATION (Adjudicated).** Folded against architect's adjudication (`v2-architecture-recommendation.md`, commit `3cdbcb712`).  
**Authority stack:** Constitutional Rulings R1–R4 (Emperor, 2026-11-04) > Blessed v2 Scope (Emperor, 2026-11-09) > Sibling R3 Constraint (`v4-federated-sketch.md` §5 KEEP-V2-OPEN checklist) > Architect Adjudication (`v2-architecture-recommendation.md` — 9 rulings, 7 addenda, 19 register items) > This Plan (`v2-plan-overview.md` + `v2-phaseN-plan.md`) > Code Implementation.  
**Sibling deliverable notice:** Sibling deliverable `v4-federated-sketch.md` (60 lines, design-only, discharging R3's "plan v4 when v2 starts") is PRESENT ON DISK in this planning directory and committed together with this plan set. It is referenced throughout this plan as backwards design pressure; it is NOT re-authored here.  
**Predecessor artifacts (FROZEN):** The 24 v1 planning documents under `.agents/shared/planning/ari-memoir-store/` remain frozen. v2 is strictly additive.  
**Doctrine & Safety Notice (P-7):** All file:line anchors cited in this plan are DATED evidence verified on 2026-11-09. Recorded line numbers are leads — **always re-grep at use time**.

---

## 1. Objective

Deliver **Memoir Store v2 "The Reflecting Mind"** end-to-end: transforming Ari's memory from an episodic log into an active, consolidating mind.

v2 delivers eight interconnected, constitutional capabilities:
1. **The Dream (Reflection Engine):** Dual-cadence consolidation — in-day idle-debounce with warm-up doubling (`1→2→4→8→16` base 60s, ceiling 960s) + force-fire at 3600s, paired with a bounded nightly pass (ReMe `auto_dream` shape: 2-day lookback, 30-episode cap, 02:00–04:00 UTC). Trigger is DERIVED per tick from `SELECT COALESCE(SUM(COALESCE(poignancy, importance)), 0) ...` (deleting rolling counter state).
2. **Cited Syntheses:** Activating the pre-staged Layer 4 table `memoir_syntheses` with mandatory source citations (`source_episode_ids[]`), structured epistemic verbs (`CREATE`, `CORROBORATE`, `REFINE`, `CORRECT`), insert dedup key (`sha256(claim_normalized + sorted(sources))`), confidence scoring, and atomic single-transaction `supersede_with(old_id, new_row)` links.
3. **The Constellation (Cold-Path Graph):** Cold-path graph layer across three new relational tables (`memoir_star`, `memoir_star_alias`, `memoir_line`) and `memoir_reflection_state` using 1–2 hop SQL joins (no graph DB, R4). Features alias merging, heat scoring (recency + recall hits), LightRAG M4 two-tier entity description synthesis (`COUNT(*)` fragment metric, `description_as_of` watermark), and deterministic star canonicalization (`v4-federated-sketch.md` §5).
4. **New Tool `memoir_about(star)`:** Entity-centric recall tool in the existing `memoir` category (ari-only, two-layer gate, refusal vocabulary mapped onto the frozen 19-code set). Zero-episode stars return an empty-dossier SUCCESS render (sections say "none"), NOT `memoir_not_found`.
5. **The Persona Gate (JEV):** Activating the dormant persona submission seam. Reflection proposes persona diffs gated by a strict classifier (`conflict > boundary_change > new_rule` force regeneration; `reinforcement` and `none` are suppressed). Proposals land `pending_approval` and require HUMAN session approval (`auth_method == "session"`). Classifier failure parks candidates (`terminal:gate_unavailable` at 5 attempts) rather than silently dropping them.
6. **Asynchronous Poignancy Scorer & Importance Ruling (§3-A2):** `importance` remains **WRITE-FROZEN** (authored record preserved); LLM score (1–10) lands in the NEW `poignancy INTEGER NULL` column; all consumers read effective importance = `COALESCE(poignancy, importance)`. Pre-existing history is seeded `poignancy_status = 'legacy'` at cutover; opt-in backfill via `MEMOIR_POIGNANCY_BACKFILL_MAX=0` (default disabled).
7. **Keepsake Promotion Proposals:** Reflection identifies candidate keepsakes; surfaced via a dedicated Proposals section in the wake briefing (measured ≤ 1000 chars, explicit trim-rung order protecting dynamic episodes) + at most ONE gentle `notify_user` per day (consumed ONLY on an accepted send). Dual governance (Emperor/Empress confirmation required; never auto-pinned).
8. **Wake-Refresh Seam (Option B Amended):** Refreshed memory context on self-alarm wakes into existing sessions via a parallel stamped context message block (`context_kind="memoir_refresh"`, stable id `memoir_refresh:{instance_id}`, no once-flag), gated on threaded `wake_source == "scheduler"`, preserving scheduler unstamped bare-HUMAN attestation (L4).

Staged as a release (v0.29.8+ coordinated at stage time via Shared Meta KV census), with **NO live promote** (the Emperor's 3-factor ceremony owns the promotion).

---

## 2. Scope

### In Scope (v2 End-to-End)
- Constellation relational schema (`memoir_star`, `memoir_star_alias`, `memoir_line`) and `memoir_reflection_state` via ONE `.sql` migration file using plain `ADD COLUMN` (SQLite-safe, Register #1).
- Evolution of `memoir_episodes` via four plain `ADD COLUMN` statements (`poignancy_status`, `poignancy`, `poignancy_attempts`, `reflected_at`) + one cutover-bounded legacy seed `UPDATE`.
- PostgreSQL ensure evolution via TWO entries in `_ensure_postgres_columns` (multi-clause DDL ALTER + DML legacy seed).
- Dual-cadence reflection engine (`MemoirReflectionService` and background `MemoirReflectionWorker` with sequential nightly-before-in-day execution).
- Syntheses writing with mandatory episode citations, epistemic verbs, insert dedup keys, atomic single-transaction `supersede_with`, and dual recall integration in `memoir_recall` (§3-A6).
- Constellation extraction, alias resolution, heat scoring, and LightRAG M4 two-tier description summarization with `description_as_of` watermark.
- `memoir_about` tool registered in the existing `memoir` category with frozen 19-code refusal mapping and empty-dossier success render.
- JEV persona gate classifier (`MemoirPersonaGateService`) generating `pending_approval` drafts via `MemoirPersonaRepository.create_draft(source='ari')` with candidate parking on failure.
- Poignancy background worker (`MemoirPoignancyWorker`) with dedicated executor, attempt tracking, failover error classification, and park/resume kill-switch logic.
- Keepsake proposal generation, briefing proposals zone (measured ≤ 1000 chars, dynamic-first trim rungs), and 1/day accepted-send `notify_user` gate.
- Wake-refresh context seam at both assembly sites in `daemon/services/context_messages.py` via `CONTEXT_KIND_MEMOIR_REFRESH` with threaded `wake_source` parameter.
- Three-class numeric knob governance (`_classify_numeric` shared before-validator) preventing boot aborts across all v2 numerics (Register #12).
- Ingestion of `v4-federated-sketch.md` §5 KEEP-V2-OPEN checklist constraints (V2-13).
- Advisory foreign-belief import rule: foreign beliefs land as `CORROBORATE`-class local synthesis rows citing the foreign source (V2-Q9).
- Cross-driver test packs (real-PG + fresh-SQLite), ensure census pins (retrieval_pg=2, pg_corpus=4), ensure transaction blast radius test induction (W8), 7 mutation-verified safety pins (§3-A7), and staged release packaging.

### Related Planning Artifacts
- `v4-federated-sketch.md`: Sibling deliverable on disk in this directory. Discharges R3 by planning v4 federation pressure backwards into v2. Committed together with this plan set.
- `v2-architecture-recommendation.md`: Level-4 authoritative architect enrichment (9 rulings, 7 addenda, 19 register items).

### Out of Scope (Contractual Guardrails)
- **NO Graph Database (R4):** Postgres relational tables only; no Neo4j, RedisGraph, or graph extensions.
- **NO Federation or Sync (R1):** Single-daemon kingdom memory only. Cross-daemon sync belongs to v4.
- **NO Uncitated Syntheses:** Syntheses without `source_episode_ids` are rejected at the repository boundary.
- **NO Autonomous Persona Self-Editing:** The live model NEVER directly edits the active persona. All proposals require human approval via a session token (Hy-Memory scar).
- **NO Synchronous LLM Latency on Hot Writes (DD-20):** `memoir_write` remains ONE instant INSERT. Both embedding and poignancy remain strictly asynchronous.
- **NO Modification of Frozen v1 Signatures:** `memoir_write`, `memoir_pin`, `memoir_unpin`, and `memoir_timeline` signatures are frozen.
- **NO Overwriting Write-Frozen `importance` (§3-A2):** `importance` is write-frozen; score lands in `poignancy`.
- **NO Scheduler Source Stamping (L4):** Scheduler wakes must remain unstamped bare `MessageType.HUMAN`.
- **NO New Tool Categories:** `memoir_about` joins the existing privileged category `memoir`.
- **NO `memoir_thread` in v2 (V2-1):** Episode-to-episode thread continuity is deferred to v3.
- **NO Searchable Archive Tier in v2 (V2-1):** Archive lifecycle belongs to v3 per `roadmap.md`.
- **NO Live Promotion:** Staging only (`scripts/upgrade/stage.sh`).

---

## 3. Binding-Input Summary

This implementation plan treats the following artifacts as binding specification:
1. **The Blessed v2 Scope (Emperor, 2026-11-09):** The 7 core capabilities detailed in the task commission.
2. **Constitutional Rulings R1–R4 (`decisions.md` §1):** Kingdom-level only, dual keepsake governance, rolling roadmap doctrine, learn-not-adopt.
3. **v1 Adjudicated Architecture (`plan-overview.md` and `decisions.md`):** DR-1 (24,000 ceiling / 48,000 backstop), DR-3 (kill-switch truth table), DD-8 (append-only), DD-9 (human-lane persona approval), DD-10 (context block, no system prompt freeze), DD-13 (factory-captured agent id), DD-20 (hot-path append purity).
4. **Architect Adjudication (`v2-architecture-recommendation.md`):** Level-4 authority: 9 rulings (Q1–Q9), 7 addenda (A1–A7), and 19-item corrections register.
5. **v2 Decisions Log (`v2-decisions.md`):** V2-1 scope ruling, V2-2..V2-25 architectural decisions, and resolved V2-Q1..V2-Q9 rulings.
6. **v4 Federation Design Pressure (`v4-federated-sketch.md` §5):** Ingested constraints: deterministic star canonicalization; well-formed, fully-cited supersedes chains; two-axis time across all new tables; vocabulary separation between episode `stars` and `memoir_star`; kingdom-local heat and access statistics; and markdown+hash mirror round-trip rendering compatibility.
7. **Research Digest (`v2-research-findings.md`):** Verified anchors, ensure single-transaction blast radius, briefing zones, background worker templates, LLM failover contracts, and tool registration checklists.

---

## 4. Phase Map & Critical Path

| # | Name | File | Objective | Depends on | Knobs / Flags Consumed |
|---|------|------|-----------|------------|------------------------|
| **1** | Schema Substrate, Constellation Tables & Repositories | `v2-phase1-plan.md` | Create constellation tables (`memoir_star`, `memoir_star_alias`, `memoir_line`) and `memoir_reflection_state` via ONE `.sql` migration file using plain `ADD COLUMN` (Register #1); register models before `create_all`; add 4 columns (`poignancy_status`, `poignancy`, `poignancy_attempts`, `reflected_at`) + legacy seed to PG ensure (Register #2, #3, #5, #15); implement repositories with atomic `supersede_with` (Register #6) and synthesis insert dedup key (Register #13). | Predecessor v1 schema | DR-6, DD-2, DD-18, V2-2, V2-10, V2-13, V2-14, V2-15, V2-20, V2-21, V2-22 |
| **2** | The Dream: Reflection Engine & Syntheses Store | `v2-phase2-plan.md` | Implement dual-cadence reflection (`MemoirReflectionService` and background `MemoirReflectionWorker` with sequential nightly-before-in-day execution, Register #19); derive trigger sum per tick (Register #4); force-fire at 3600s (Register #11); enforce budget table (5 syntheses, 1000-char reject, 4 LLM calls); integrate dual recall in `memoir_recall` (§3-A6, Register #7); carry advisory foreign-belief import rule (V2-Q9). | P1 | `ENSEMBLE_MEMOIR_REFLECTION_ENABLED`, V2-5, V2-6, V2-7, V2-15, V2-16, V2-17, V2-19, V2-24, V2-Q9 |
| **3** | The Constellation Layer & `memoir_about` Tool | `v2-phase3-plan.md` | Build cold-path star extraction from `episodes.stars[]`, deterministic alias resolution, heat scoring, LightRAG M4 two-tier description summarization (`COUNT(*)` fragment metric, Register #18; `description_as_of` cooldown carrier, Register #9); implement `memoir_about` tool in existing `memoir` category with 19-code refusal mapping and empty-dossier success render (Register #25). | P1, P2 | V2-3, V2-4, V2-8, V2-13, V2-22, V2-25 |
| **4** | Poignancy Background Worker | `v2-phase4-plan.md` | Implement asynchronous LLM importance scorer (1–10) cloning embedding worker architecture; dedicated thread executor `memoir-poignancy`; write to `poignancy` column (never `importance`, Register #3); cutover-bounded legacy seed and opt-in backfill max=0 (Register #5); failover error classification; feeds recall tie-break, reflection triggering, and keepsake proposals. | P1 | `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`, `MEMOIR_POIGNANCY_BACKFILL_MAX`, DD-5, V2-10, V2-14, V2-16 |
| **5** | The Persona Gate (JEV) | `v2-phase5-plan.md` | Implement JEV diff classifier (`conflict > boundary_change > new_rule` force regeneration; `reinforcement`, `none` suppressed); candidate parking on failure (`terminal:gate_unavailable` at 5 attempts, Register #16); generate `pending_approval` draft versions via `create_draft(source='ari')` from reflection service; enforce human session approval. | P2 | DD-9, V2-9, V2-13, V2-20 |
| **6** | Keepsake Proposals & Wake-Refresh Seams | `v2-phase6-plan.md` | Implement reflection keepsake proposal generation; surface in wake briefing proposals zone (measured ≤ 1000 chars, explicit dynamic-first trim rungs, Register #14) + 1/day accepted-send `notify_user` gate (Register #17); implement wake-refresh context seam at both assembly sites via `CONTEXT_KIND_MEMOIR_REFRESH` with threaded `wake_source` parameter (Option B Amended, Register #10). | P2, P4 | `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED`, R2, DR-1, V2-11, V2-18, V2-23, V2-Q1 |
| **7** | Verification, Safety Gates, Docs & Staged Release | `v2-phase7-plan.md` | Execute cross-driver test packs (real-PG + fresh-SQLite); verify asymmetric ensure census pins (retrieval_pg=2, pg_corpus=4, Register #8); execute W8 ensure single-transaction induction test; execute 7 mutation-verified safety pins (§3-A7); verify boot smoke with 3-worker census; update `tools_note.md` and ceremony census; commit sibling `v4-federated-sketch.md`; stage release v0.29.8+ (NO promote). | P1–P6 | All flags, W8 blast radius proof, R10/R11 hazards, V2-12, V2-16, V2-21 |

### Critical Path Analysis
```text
           ┌──> Phase 3 (Constellation & memoir_about) ──┐
           │                                             │
Phase 1 ───┼──> Phase 2 (Reflection Core) ──┬─────────────┼──> Phase 5 (JEV Persona Gate) ───┐
           │                               │             │                                  │
           └──> Phase 4 (Poignancy Worker) ─┴─────────────┼──> Phase 6 (Keepsakes & Refresh) ┼──> Phase 7 (Verify & Stage)
                                                         │                                  │
                                                         └──────────────────────────────────┘
```
- **Phase 1** is the foundational database substrate gating all subsequent work.
- **Phase 2** (Reflection Core) and **Phase 4** (Poignancy Worker) run in parallel after Phase 1.
- **Phase 3** (Constellation & Tool) depends on Phase 1 models and Phase 2 reflection pipelines.
- **Phase 5** (JEV Persona Gate) depends on Phase 2 synthesis outputs.
- **Phase 6** (Keepsakes & Refresh) depends on Phase 2/4 outputs and briefing services.
- **Phase 7** is the final integration, verification, and staging gate.

---

## 5. Coupling Map

| Core Seams & Phases | P1 Schema & Repos | P2 Reflection | P3 Constellation & Tool | P4 Poignancy Worker | P5 JEV Persona Gate | P6 Keepsakes & Refresh | P7 Verification & Staging |
|---|---|---|---|---|---|---|---|
| **P1 Schema & Repos** | — | models & syntheses repo consumed | star/alias/line models consumed | poignancy columns consumed | persona repo consumed | — | migration delta asserted |
| **P2 Reflection** | — | — | triggers star extraction & summarization | consumes derived importance sum | generates persona belief diffs | generates keepsake proposal candidates | reflection worker tested |
| **P3 Constellation & Tool** | — | — | — | — | — | star card rendered in briefing/tool | tool registration census asserted |
| **P4 Poignancy Worker** | — | updates poignancy for derived sum | — | — | — | feeds keepsake qualification | worker lifespan tested |
| **P5 JEV Persona Gate** | — | — | — | — | — | — | human-lane approval tested |
| **P6 Keepsakes & Refresh** | — | — | — | — | — | — | context assembly tested |
| **`daemon/manager.py`** | Model registration + 2 ensure entries (P1) | — | — | — | — | — | Core seam (Tier-2 trigger) |
| **`daemon/api.py`** | — | Reflection worker lifespan (P2) | — | Poignancy worker lifespan (P4) | — | — | Core seam (Tier-2 trigger) |
| **`context_messages.py`** | — | — | — | — | — | Briefing proposals zone + refresh seam (P6) | Context kind exports |
| **`instance_messaging.py`** | — | — | — | — | — | `wake_source` threaded to context assembly (P6) | Message delivery |
| **`tools/memoir.py`** | — | Dual recall includes syntheses (P2) | `memoir_about` tool registered (P3) | — | — | — | Tool count == 6, codes == 19 |
| **`tools/_tool_registry.py`** | — | — | Tool factory added (P3) | — | — | — | Known tools census asserted |
| **`agents/ari/*`** | — | — | `tools_note.md` re-stamped (P3) | — | — | Briefing consumed | Payload trees checked |

### Hard-Coupling Hazards & Blast-Radius Defenses
1. **Core Seam Touches in `manager.py` and `api.py`:** Trigger Tier-2 verification. Grouped strictly into Phase 1 (manager model registration and 2 ensure entries) and Phases 2/4 (lifespan worker registrations in api.py).
2. **Shared Ensure Single-Transaction Blast Radius:** Adding the four columns and legacy seed to `_ensure_postgres_columns` touches the shared list. Proved safe via the W8 test induction pattern (Phase 7).
3. **Consumer Once-Flag Boundary:** Wake refresh does not use a once-flag; turn deduplication is structural via `add_messages` supersede-by-stable-id (`memoir_refresh:{instance_id}`).

---

## 6. Risk Register

| ID | Risk | Impact | Likelihood | Mitigation Strategy |
|---|---|---|---|---|
| **R1** | **Ensure DDL failure aborts daemon boot** — Adding columns and legacy seed to `memoir_episodes` rides the shared all-or-nothing ensure transaction in `manager.py`. | HIGH | LOW | Simple idempotent DDL (`ALTER TABLE memoir_episodes ADD COLUMN IF NOT EXISTS ...`) + cutover-bounded seed UPDATE. Test induction via W8 proves rollback and ensures clean diagnostic reporting. |
| **R2** | **Uncitated syntheses corrupt Ari's epistemic provenance** — Reflection produces claims without backing episode IDs. | HIGH | LOW | Repository-layer validation: `MemoirSynthesesRepository.create_synthesis()` strictly refuses rows where `source_episode_ids` is NULL or empty. |
| **R3** | **Runaway reflection LLM cost / token burn** — Reflection loops over-generate or stall in multi-turn completions. | HIGH | MEDIUM | Multi-layer composed bounds: max 5 syntheses, max 1000 chars/synthesis (REJECTED not truncated), max 4 LLM calls/pass (incremented at dispatch), 45s wall-clock cap, and in-worker `estimate_tokens` pass metering. |
| **R4** | **Poignancy scoring delays hot-path conversational writes** — Write path blocks on LLM evaluation. | HIGH | LOW | Hot append remains ONE fast SQL INSERT with default importance 5 (DD-20). Poignancy scoring runs purely in a background worker task; `importance` is write-frozen. |
| **R5** | **Live persona drift via uncontrolled self-editing** (Hy-Memory production scar) — Model mutates durable persona without oversight. | HIGH | LOW | JEV diff classifier gates changes; proposals land PENDING; approval strictly requires an active human session token (`auth_method == "session"`). Candidate parking on classifier failure prevents silent drops. |
| **R6** | **Scheduler bare-HUMAN wake stamp corrupted** (L4 landmine) — Attempting to inject memory into scheduler alarm messages alters the message source stamp. | HIGH | LOW | Option B adopted: parallel stamped context message block (`context_kind="memoir_refresh"`) evaluated at assembly sites with threaded `wake_source == "scheduler"`. Scheduler source adapter remains untouched. |
| **R7** | **Tool category ordinal drift in tests** — Referencing category #18 or #19 breaks as new categories merge. | MEDIUM | MEDIUM | House rule enforced: category ordinals are NEVER hardcoded. All test packs grep-derive ordinals dynamically at ceremony time. |
| **R8** | **Notification spam on keepsake proposals** — Reflection floods chat channels with pin proposals. | MEDIUM | LOW | Lane-owned date-keyed ledger consumed ONLY on an accepted send enforces at most ONE gentle `notify_user` per calendar day. Detailed proposals surface passively in the wake briefing. |
| **R9** | **Constellation N+1 query explosion during recall** — Entity retrieval over-joins lines and stars. | MEDIUM | LOW | Strict query bounds: 1–2 hop SQL joins only, star candidate limit 10, episode mention limit 5. No recursive graph traversal. |
| **R10** | **Tester-lane env poisoning** (Critical Note occ#8) — `env -u POSTGRES_*` fails to unset variables, hitting production DB. | CRITICAL | LOW | Standing critical discipline: test scripts scrub POSTGRES_* PER-VARIABLE, never via globs. Tests run against isolated scratch databases. |
| **R11** | **Frozen binary boot sweep kills live processes** (Critical Note) — Staged smoke boot sweep misclassifies live procs as orphans. | CRITICAL | LOW | Boot smoke scripts explicitly export `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`. |
| **R12** | **Release version slot collision** — Merging concurrently with parallel release lanes. | MEDIUM | LOW | Version slot coordinated via Shared Meta KV census at stage time. Parallel views-fix lane claims `v0.29.7`; v2 plans for `v0.29.8+`. |
| **R13** | **Numeric knob boot-abort defect** (Anomaly `33028b27`) — Bare `Field(gt=...)` on unparseable numeric env vars aborts daemon boot with `ValidationError`. | HIGH | LOW | Three-class numeric knob governance (`_classify_numeric` shared before-validator) clamps unparseable budget bounds and logs warnings without raising `ValidationError`. |

---

## 7. Test Strategy & Quality Gates

The implementation enforces a four-tiered verification battery:
1. **Tier 1 (Unit Packs per Phase):** Fast, isolated tests for repository methods, JEV classifier heuristics, refusal mapping, dedup hash, and data contracts.
2. **Tier 2 (Core Seams & Integration):** Triggered by touches to `daemon/manager.py` and `daemon/api.py`. Validates daemon lifecycle, worker startup/shutdown, and context message assembly.
3. **Real-PG & Cross-Driver Batteries:**
   - Fresh SQLite boot: Verifies `.sql` migration files with plain `ADD COLUMN` apply cleanly with zero errors.
   - Real PostgreSQL boot: Verifies SQLModel `create_all`, `_ensure_postgres_columns` idempotence, JSONB and ARRAY data types, and GIN/B-tree index usage.
   - Ensure census pin assertions: `test_memoir_retrieval_pg.py` == 2, `test_memoir_pg_corpus.py` == 4.
4. **W8 Ensure Blast Radius Test Induction:**
   - Scratch PostgreSQL environment with a deliberately invalid ensure DDL statement to inductively prove single-transaction rollback and diagnostic reporting without risking production databases.
5. **The 7 Mutation-Verified Safety Pins (§3-A7):**
   - Proven across kill-switch truth tables, budget clamps, 19-code freeze, notification/refresh dedup, supersede chains, legacy seed cutover, and record-column purity vs poignancy.
6. **Boot Smoke with 3-Worker Census:**
   - End-to-end daemon boot smoke asserting that `MemoirEmbeddingWorker`, `MemoirReflectionWorker`, and `MemoirPoignancyWorker` spawn on separate tasks with dedicated executors, respond to kill-switches, and start cleanly with unset knobs (unset⇒ON visibility).

---

## 8. Rollout & Staging Plan

1. **Pre-Staging Verification:**
   - All Tier-1 and Tier-2 test packs green.
   - All 7 mutation pins verified.
   - Clean git status in `/home/nea/ensemble-src-wt-memoir-v2`.
   - Tool census updated: `MEMOIR_TOOL_NAMES` holds exactly 6 tools (`memoir_write`, `memoir_pin`, `memoir_unpin`, `memoir_recall`, `memoir_timeline`, `memoir_about`); `MEMOIR_CODES` holds exactly 19 codes.
   - Sibling deliverable check: `v4-federated-sketch.md` verified present in `.agents/shared/planning/ari-memoir-store/` and staged together with the v2 implementation set.
2. **Version Slot Coordination:**
   - Query Shared Meta KV census for active release branches.
   - Coordinate slot: views-fix lane claims `v0.29.7`; Memoir Store v2 targets **`v0.29.8`** (or next available slot).
3. **Packaging via `stage.sh`:**
   - Execute `bash scripts/upgrade/stage.sh`.
   - Verify creation of `releases/v0.29.X/` with binary, agent definitions, frontend dist, manifest, and sidecars.
   - Verify 4-way SHA-256 match: release dir == dist == manifest == sidecar.
   - Annotated git tag cut and pushed to origin.
4. **Handoff (NO Promote):**
   - The promote script (`promote.sh`) is reserved exclusively for the Emperor's 3-factor ceremony. This lane does NOT promote.
