# Memoir Store v2 "The Reflecting Mind" — Plan Overview (Phase B Implementation Plan)

**Lane:** `feature/memoir-v2-reflecting-mind` @ worktree `/home/nea/ensemble-src-wt-memoir-v2`  
**Base commit:** `f49006b922492f72ca032c56e26d20468f7a3afd` (branched from `origin/latest`)  
**Status:** **AUTHORITATIVE IMPLEMENTATION SPECIFICATION (Additive).** Authored 2026-11-09.  
**Authority stack:** Constitutional Rulings R1–R4 (Emperor, 2026-11-04) > Blessed v2 Scope (Emperor, 2026-11-09) > Sibling R3 Constraint (`v4-federated-sketch.md` §5 KEEP-V2-OPEN checklist) > Architect Adjudication (`v2-decisions.md` §4) > This Plan (`v2-plan-overview.md` + `v2-phaseN-plan.md`) > Code Implementation.  
**Sibling deliverable notice:** Sibling deliverable `v4-federated-sketch.md` (60 lines, design-only, discharging R3's "plan v4 when v2 starts") is PRESENT ON DISK in this planning directory and committed together with this plan set. It is referenced throughout this plan as backwards design pressure; it is NOT re-authored here.  
**Predecessor artifacts (FROZEN):** The 24 v1 planning documents under `.agents/shared/planning/ari-memoir-store/` remain frozen. v2 is strictly additive.  
**Doctrine & Safety Notice (P-7):** All file:line anchors cited in this plan are DATED evidence verified on 2026-11-09. Recorded line numbers are leads — **always re-grep at use time**.

---

## 1. Objective

Deliver **Memoir Store v2 "The Reflecting Mind"** end-to-end: transforming Ari's memory from an episodic log into an active, consolidating mind.

v2 delivers eight interconnected, constitutional capabilities:
1. **The Dream (Reflection Engine):** Dual-cadence consolidation — in-day idle-debounce with warm-up doubling (`1→2→4→8→N` base 60s) for responsive intra-day synthesis, paired with a bounded nightly pass (ReMe `auto_dream` shape: 2-day lookback, strict synthesis and character caps).
2. **Cited Syntheses:** Activating the pre-staged Layer 4 table `memoir_syntheses` with mandatory source citations (`source_episode_ids[]`), structured epistemic verbs (`CREATE`, `CORROBORATE`, `REFINE`, `CORRECT`), confidence scoring, and supersession lineage.
3. **The Constellation (Cold-Path Graph):** Cold-path graph layer across three new relational tables (`memoir_star`, `memoir_star_alias`, `memoir_line`) using 1–2 hop SQL joins (no graph DB, R4). Features alias merging, heat scoring (recency + recall hits), and LightRAG M4 two-tier entity description synthesis.
4. **New Tool `memoir_about(star)`:** Entity-centric recall tool in the existing `memoir` category (ari-only, two-layer gate, refusal vocabulary mapped onto the frozen 19-code set).
5. **The Persona Gate (JEV):** Activating the dormant persona submission seam. Reflection proposes persona diffs gated by a strict classifier (`conflict`, `boundary_change`, `new_rule` force regeneration; `reinforcement` and `none` are suppressed). Proposals land `pending_approval` and require HUMAN session approval (`auth_method == "session"`).
6. **Asynchronous Poignancy Scorer:** Background LLM importance scoring (1–10) cloning the embedding worker pattern, defaulting to 5 at write time to ensure hot append is never delayed. Feeds recall tie-breaks, reflection triggering, and keepsake proposals.
7. **Keepsake Promotion Proposals:** Reflection identifies candidate keepsakes; surfaced via a dedicated Proposals section in the wake briefing + at most ONE gentle `notify_user` per day. Dual governance (Emperor/Empress confirmation required; never auto-pinned).
8. **Wake-Refresh Seam:** Refreshed memory context on self-alarm wakes into existing sessions via a parallel stamped context message block (`CONTEXT_KIND_MEMOIR_REFRESH`), preserving scheduler unstamped bare-HUMAN attestation (L4).

Staged as a release (v0.29.8+ coordinated at stage time via Shared Meta KV census), with **NO live promote** (the Emperor's 3-factor ceremony owns the promotion).

---

## 2. Scope

### In Scope (v2 End-to-End)
- Constellation relational schema (`memoir_star`, `memoir_star_alias`, `memoir_line`) + companion `.sql` migration file + SQLModel models registered before `create_all`.
- `memoir_episodes.poignancy_status` column evolution (ADD COLUMN IF NOT EXISTS in `_ensure_postgres_columns` + model field + `.sql` twin).
- Dual-cadence reflection engine (`MemoirReflectionService` and background `MemoirReflectionWorker`).
- Syntheses writing with mandatory episode citations, epistemic verbs, and dual recall integration in `memoir_recall`.
- Constellation extraction, alias resolution, heat scoring, and LightRAG M4 two-tier description summarization.
- `memoir_about` tool registered in the existing `memoir` category with frozen 19-code refusal mapping.
- JEV persona gate classifier (`MemoirPersonaGateService`) generating `pending_approval` drafts via `MemoirPersonaRepository.create_draft(source='ari')`.
- Poignancy background worker (`MemoirPoignancyWorker`) with dedicated executor, attempt tracking, and park/resume kill-switch logic.
- Keepsake proposal generation, briefing proposals zone, and 1/day `notify_user` gate.
- Wake-refresh context seam at both assembly sites in `daemon/services/context_messages.py`.
- Subsystem kill-switches (`ENSEMBLE_MEMOIR_REFLECTION_ENABLED`, `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`, `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED`) adhering to DR-3 truth tables.
- Ingestion of `v4-federated-sketch.md` §5 KEEP-V2-OPEN checklist constraints (V2-13).
- Cross-driver test packs (real-PG + fresh-SQLite), ensure transaction blast radius test induction (W8), and staged release packaging.

### Related Planning Artifacts
- `v4-federated-sketch.md`: Sibling deliverable on disk in this directory. Discharges R3 by planning v4 federation pressure backwards into v2. Committed together with this plan set.

### Out of Scope (Contractual Guardrails)
- **NO Graph Database (R4):** Postgres relational tables only; no Neo4j, RedisGraph, or graph extensions.
- **NO Federation or Sync (R1):** Single-daemon kingdom memory only. Cross-daemon sync belongs to v4.
- **NO Uncitated Syntheses:** Syntheses without `source_episode_ids` are rejected at the repository boundary.
- **NO Autonomous Persona Self-Editing:** The live model NEVER directly edits the active persona. All proposals require human approval via a session token (Hy-Memory scar).
- **NO Synchronous LLM Latency on Hot Writes (DD-20):** `memoir_write` remains ONE instant INSERT. Both embedding and poignancy remain strictly asynchronous.
- **NO Modification of Frozen v1 Signatures:** `memoir_write`, `memoir_pin`, `memoir_unpin`, and `memoir_timeline` signatures are frozen.
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
4. **v2 Decisions Log (`v2-decisions.md`):** V2-1 scope ruling, V2-2..V2-13 architectural decisions, V2-Q1..V2-Q8 architect open questions, and V2-Q9 advisory import semantics question.
5. **Research Digest (`v2-research-findings.md`):** Verified anchors, ensure single-transaction blast radius, briefing zones, background worker templates, LLM failover contracts, and tool registration checklists.
6. **v4 Federation Design Pressure (`v4-federated-sketch.md` §5):** Ingested constraints: deterministic star canonicalization; well-formed, fully-cited supersedes chains; two-axis time across all new tables; vocabulary separation between episode `stars` and `memoir_star`; kingdom-local heat and access statistics; and markdown+hash mirror round-trip rendering compatibility.

---

## 4. Phase Map & Critical Path

| # | Name | File | Objective | Depends on | Knobs / Flags Consumed |
|---|------|------|-----------|------------|------------------------|
| **1** | Schema Substrate, Constellation Tables & Repositories | `v2-phase1-plan.md` | Create constellation tables (`memoir_star`, `memoir_star_alias`, `memoir_line`) via ONE `.sql` migration file; register models before `create_all`; add `poignancy_status` to `memoir_episodes` via ensure + .sql twin; enforce two-axis time and deterministic canonicalization; implement `MemoirConstellationRepository` and `MemoirSynthesesRepository`. | — | DR-6, DD-2, DD-18, V2-2, V2-10, V2-13 |
| **2** | The Dream: Reflection Engine & Syntheses Store | `v2-phase2-plan.md` | Implement dual-cadence reflection (`MemoirReflectionService` and background `MemoirReflectionWorker` with idle-debounce doubling + nightly ReMe auto_dream); write cited syntheses; integrate dual recall (episodes + syntheses) in `memoir_recall`; carry advisory foreign-belief import rule (V2-Q9). | P1 | `ENSEMBLE_MEMOIR_REFLECTION_ENABLED`, V2-5, V2-6, V2-7, V2-Q9 |
| **3** | The Constellation Layer & `memoir_about` Tool | `v2-phase3-plan.md` | Build cold-path star extraction from `episodes.stars[]`, alias resolution, heat scoring, LightRAG M4 two-tier description summarization; implement `memoir_about` tool in existing `memoir` category with 19-code refusal mapping. | P1, P2 | V2-3, V2-4, V2-8, V2-13 |
| **4** | Poignancy Background Worker | `v2-phase4-plan.md` | Implement asynchronous LLM importance scorer (1–10) cloning embedding worker architecture; dedicated thread executor; attempt caps; terminal markers; feeds recall tie-break, reflection triggering, and keepsake proposals. | P1 | `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`, DD-5, V2-10 |
| **5** | The Persona Gate (JEV) | `v2-phase5-plan.md` | Implement JEV diff classifier (`conflict`, `boundary_change`, `new_rule` force regeneration; `reinforcement`, `none` suppressed); generate `pending_approval` draft versions via `MemoirPersonaRepository.create_draft(source='ari')`; enforce human session approval. | P2 | DD-9, V2-9, V2-13 |
| **6** | Keepsake Proposals & Wake-Refresh Seams | `v2-phase6-plan.md` | Implement reflection keepsake proposal generation; surface in wake briefing proposals zone + 1/day `notify_user` gate; implement wake-refresh context seam at both assembly sites via `CONTEXT_KIND_MEMOIR_REFRESH` (Option B). | P2, P4 | `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED`, R2, V2-11, V2-Q1 |
| **7** | Verification, Safety Gates, Docs & Staged Release | `v2-phase7-plan.md` | Execute cross-driver test packs (real-PG + fresh-SQLite); execute W8 ensure single-transaction induction test; verify boot smoke with worker census; update `tools_note.md` and ceremony census; commit sibling `v4-federated-sketch.md`; stage release v0.29.8+ (NO promote). | P1–P6 | All flags & census pins |

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
| **P1 Schema & Repos** | — | models & syntheses repo consumed | star/alias/line models consumed | poignancy column consumed | persona repo consumed | — | migration delta asserted |
| **P2 Reflection** | — | — | triggers star extraction & summarization | consumes poignancy trigger threshold | generates persona belief diffs | generates keepsake proposal candidates | reflection worker tested |
| **P3 Constellation & Tool** | — | — | — | — | — | star card rendered in briefing/tool | tool registration census asserted |
| **P4 Poignancy Worker** | — | updates importance for reflection | — | — | — | feeds keepsake qualification | worker lifespan tested |
| **P5 JEV Persona Gate** | — | — | — | — | — | — | human-lane approval tested |
| **P6 Keepsakes & Refresh** | — | — | — | — | — | — | context assembly tested |
| **`daemon/manager.py`** | Model registration + ensure column (P1) | — | — | — | — | — | Core seam (Tier-2 trigger) |
| **`daemon/api.py`** | — | Reflection worker lifespan (P2) | — | Poignancy worker lifespan (P4) | — | — | Core seam (Tier-2 trigger) |
| **`context_messages.py`** | — | — | — | — | — | Briefing proposals zone + refresh seam (P6) | Context kind exports |
| **`instance_messaging.py`** | — | — | — | — | — | Consumer once-flag write preserved (P6) | Message delivery |
| **`tools/memoir.py`** | — | Dual recall includes syntheses (P2) | `memoir_about` tool registered (P3) | — | — | — | Tool count == 6, codes == 19 |
| **`tools/_tool_registry.py`** | — | — | Tool factory added (P3) | — | — | — | Known tools census asserted |
| **`agents/ari/*`** | — | — | `tools_note.md` re-stamped (P3) | — | — | Briefing consumed | Payload trees checked |

### Hard-Coupling Hazards & Blast-Radius Defenses
1. **Core Seam Touches in `manager.py` and `api.py`:** Trigger Tier-2 verification. Grouped strictly into Phase 1 (manager model registration and ensure ALTER) and Phases 2/4 (lifespan worker registrations in api.py).
2. **Shared Ensure Single-Transaction Blast Radius:** Adding `poignancy_status` to `memoir_episodes` in `_ensure_postgres_columns` touches the shared list. Proved safe via the W8 test induction pattern (Phase 7).
3. **Consumer Once-Flag Boundary:** Wake refresh must not consume the once-per-instance briefing flag. The refresh block uses an independent state evaluation path.

---

## 6. Risk Register

| ID | Risk | Impact | Likelihood | Mitigation Strategy |
|---|---|---|---|---|
| **R1** | **Ensure DDL failure aborts daemon boot** — Adding `poignancy_status` to `memoir_episodes` rides the shared all-or-nothing ensure transaction in `manager.py`. | HIGH | LOW | Simple idempotent DDL (`ALTER TABLE memoir_episodes ADD COLUMN IF NOT EXISTS poignancy_status TEXT NULL`). Test induction via W8 proves rollback and ensures clean error reporting. |
| **R2** | **Uncitated syntheses corrupt Ari's epistemic provenance** — Reflection produces claims without backing episode IDs. | HIGH | LOW | Repository-layer validation: `MemoirSynthesesRepository.create()` strictly refuses rows where `source_episode_ids` is NULL or empty. |
| **R3** | **Runaway reflection LLM cost / token burn** — Reflection loops over-generate or stall in multi-turn completions. | HIGH | MEDIUM | Multi-layer composed bounds: max 5 syntheses, max 1000 chars/synthesis, max 4 LLM calls/pass, 45s wall-clock cap, and in-worker `estimate_tokens` pass metering. |
| **R4** | **Poignancy scoring delays hot-path conversational writes** — Write path blocks on LLM evaluation. | HIGH | LOW | Hot append remains ONE fast SQL INSERT with default importance 5 (DD-20). Poignancy scoring runs purely in a background worker task. |
| **R5** | **Live persona drift via uncontrolled self-editing** (Hy-Memory production scar) — Model mutates durable persona without oversight. | HIGH | LOW | JEV diff classifier gates changes; proposals land PENDING; approval strictly requires an active human session token (`auth_method == "session"`). |
| **R6** | **Scheduler bare-HUMAN wake stamp corrupted** (L4 landmine) — Attempting to inject memory into scheduler alarm messages alters the message source stamp. | HIGH | LOW | Option B adopted: parallel stamped context message block (`CONTEXT_KIND_MEMOIR_REFRESH`) evaluated at assembly sites. Scheduler source adapter remains untouched. |
| **R7** | **Tool category ordinal drift in tests** — Referencing category #18 or #19 breaks as new categories merge. | MEDIUM | MEDIUM | House rule enforced: category ordinals are NEVER hardcoded. All test packs grep-derive ordinals dynamically at ceremony time. |
| **R8** | **Notification spam on keepsake proposals** — Reflection floods chat channels with pin proposals. | MEDIUM | LOW | Lane-owned date-keyed ledger enforces at most ONE gentle `notify_user` per calendar day. Detailed proposals surface passively in the wake briefing. |
| **R9** | **Constellation N+1 query explosion during recall** — Entity retrieval over-joins lines and stars. | MEDIUM | LOW | Strict query bounds: 1–2 hop SQL joins only, star candidate limit 10, episode mention limit 5. No recursive graph traversal. |
| **R10** | **Tester-lane env poisoning** (Critical Note occ#8) — `env -u POSTGRES_*` fails to unset variables, hitting production DB. | CRITICAL | LOW | Standing critical discipline: test scripts scrub POSTGRES_* PER-VARIABLE, never via globs. Tests run against isolated scratch databases. |
| **R11** | **Frozen binary boot sweep kills live processes** (Critical Note) — Staged smoke boot sweep misclassifies live procs as orphans. | CRITICAL | LOW | Boot smoke scripts explicitly export `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`. |
| **R12** | **Release version slot collision** — Merging concurrently with parallel release lanes. | MEDIUM | LOW | Version slot coordinated via Shared Meta KV census at stage time. Parallel views-fix claims v0.29.7; v2 plans for v0.29.8+. |

---

## 7. Test Strategy & Quality Gates

The implementation enforces a four-tiered verification battery:
1. **Tier 1 (Unit Packs per Phase):** Fast, isolated tests for repository methods, JEV classifier heuristics, refusal mapping, and data contracts.
2. **Tier 2 (Core Seams & Integration):** Triggered by touches to `daemon/manager.py` and `daemon/api.py`. Validates daemon lifecycle, worker startup/shutdown, and context message assembly.
3. **Real-PG & Cross-Driver Batteries:**
   - Fresh SQLite boot: Verifies `.sql` migration files apply cleanly with zero errors.
   - Real PostgreSQL boot: Verifies SQLModel `create_all`, `_ensure_postgres_columns` idempotence, JSONB and ARRAY data types, and GIN/B-tree index usage.
4. **W8 Ensure Blast Radius Test Induction:**
   - Scratch PostgreSQL environment with a deliberately invalid ensure DDL statement to inductively prove single-transaction rollback and diagnostic reporting without risking production databases.
5. **Boot Smoke with Worker Census:**
   - End-to-end daemon boot smoke asserting that `MemoirEmbeddingWorker`, `MemoirReflectionWorker`, and `MemoirPoignancyWorker` are running and responsive to their kill-switches.

---

## 8. Rollout & Staging Plan

1. **Pre-Staging Verification:**
   - All Tier-1 and Tier-2 test packs green.
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
