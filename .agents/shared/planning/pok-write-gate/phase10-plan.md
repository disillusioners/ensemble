# Wave 2 · Phase 10: Metrics + Per-Project Soft/Smart Max (NEW SUBSYSTEM)

## Objective
Emperor ruling (addendum-3 item 7): the POK librarian reads per-entry metrics for management and maintains a metrics-derived per-project soft/smart max below the 1024 hard cap. Binding picks: **I1** (soft max in `project_metadata_records` — zero migration), **H3** (hybrid periodic pass), **J** (metric set + 3 amendments). Do NOT ship a librarian flying only on access_count. Consolidation ONLY under ruled archive bases.

## Tasks
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Librarian reads per-entry metrics (access_count, verify_count, pinned/disputed/curated tier, family, author, timestamps) from the ledger + POK rows | Phase 1 + Phase 5 | Unit test reader |
| 2 | Soft/smart max derivation: `derived = max(floor, f(utilization))` from measured utilization (rows actually injected/cited); NOT a hand-set constant; **floor ≥ current active-count band** so tightening stays DORMANT until real gate activity lifts the value | T1 | Unit test derivation; below 1024; unit "no tightening when active_count < floor" (highest-value test) |
| 3 | Soft-max placement = **I1**: `project_metadata_records` key `pok.soft_max.v1` = JSON `{value, inputs, computed_at, drift}` — table EXISTS (`project/models.py:358-373`, `uq_project_metadata_project_key`) ⇒ **ZERO migration**; written by the pass job (daemon-internal writer — does not violate the authority split, which governs agent-facing tool calls); read at write time by one unique-index SELECT (sub-ms), NO in-process memo. I3 (curated POK row) is DOCTRINE-WRONG — a count-that-changes is rolling state, which the store's own rule routes OUT of POK; I2 (ledger row) REJECTED — conflates state with an event log and risks retention deleting the soft-max. **Prohibition: pass summaries do NOT live in the admission ledger.** | T2 | PIN: value lives in project metadata; mutation: a soft-max/meta row inside the POK store → RED (doctrine pin) |
| 4 | Periodic pass = **H3 hybrid**: ONE global MaintenanceService job registered at manager initialize, looping project_ids internally + the on-write bound-check (subsumed — upsert already loads `active_count` for the 1024 cap at `pok_service.py:458-465`); `idle_policy=UN_GATED` (IDLE_GATED starves on a continuously busy nest); membership in `JOB_MAINTENANCE_SECTIONS` (persisted clock + free observability); **writes a `maintenance_runs` row (`section='pok-librarian'`, table exists, zero migration)**; per-project try/except; boot-SLO 120s unaffected (dict-append registration, `_loop` sleeps 60s before first tick, hydration fail-soft) | T1 | PIN: UN_GATED + registered + maintenance_runs row written; mutation: boot path contains pass execution → RED (SLO guard) |
| 5 | Recompute on pass + drift report | T4 | Unit test |
| 6 | Near soft max: admission TIGHTENS (more ASK rounds, prefer MERGE over new rows) | T2 + Phase 7 | Unit test |
| 7 | Consolidation ONLY under ruled archive bases — covered-by-surviving-row · relocated-to-routed-home · NOW-FALSE; NEVER age, NEVER access_count alone | T6 | Unit test (no age/access-alone archive) |
| 8 | Trusted-vs-ignored metric enforcement = **J CONFIRMED + 3 amendments**: TRUST gate-stamped verify_count, curator tiers, post-fix family, verdict-derived counts, timestamps as provenance; IGNORE access_count ALONE, age, vector/embeddings, pre-stamp verify_count, tier=0. Amendments: (1) ledger retrieval counts supersede access_count once Phase 1 lands (they disambiguate query-vs-injection); access_count becomes legacy corroboration. (2) Timestamps corroborate, never trigger. (3) Label per-row vs per-store metrics separately | T1 | Unit test; mutation: access_count-alone triggers archive → RED |
| 9 | Proactive consolidation pass trigger near soft max | T6 | Unit test |
| 10 | Surface expired / never-answered gates from the ASK chain (the librarian's periodic pass is the safety net for non-returning callers — fail-closed otherwise means the lesson is never written) | Phase 8 | Unit test; audit `phase=ASK_EXPIRED` |
| 11 | J partition wiring: MERGE-TARGET selection ← verify_count + tier + family-match + recency + high-access corroboration; redundancy evidence ← family + content_sha256 + prior MERGE verdicts (a RAG twin is NEVER redundancy); staleness ← corroborator for ruled bases only; access_count only in COMBINATION (high-access+zero-verify+stale → verification rounds/ASK, never archive; zero-access+boundary-ELSEWHERE → surfaced for relocated-to-routed-home) | T8 | Unit test per partition rule |
| 12 | **R-RET (census R6) librarian detection**: per-entry metrics (never-retrieved / NOFAM∧ZA) feed R-RET as a DETECTION signal into T7's ruled-bases consolidator — MERGE if a well-slugged active row already carries the fact (count-preserving supersede + archive mis-slugged duplicate under `covered-by-surviving-row`); re-slug proposal if unique content but bad slug. NEVER a standalone archive basis; never age, never access_count alone | T7 + T8 | Unit test: R-RET proposes under ruled basis only; mutation: R-RET alone archives → RED |

## Coupling
- **Tight with:** Phase 1 (ledger), Phase 5 (metrics foundation), Phase 7 (tightening feeds ASK/MERGE), Phase 8 (expired-ASK surfacing).
- **Loose with:** Phase 12 (guideline measured-state).

## Risks
- Metrics dead-code at cold start — `record_verification` is now wired (Phase 5) but verify_count starts at 0; the floor policy keeps tightening dormant until real gate activity lifts the derived value. First weeks = observe-and-stamp only.
- Hard cap 1024 is a backstop, not the working ceiling — never conflate.
- A soft-max row inside the POK store would be doctrine-wrong and self-gating — pinned against.

## Exit Criterion
Librarian reads metrics; soft-max derived with a floor ≥ current-active band; stored at `pok.soft_max.v1` in project metadata (zero migration); H3 pass registered UN_GATED with a boot-SLO guard; drift reported; tightening + consolidation under ruled bases only; J metric set + 3 amendments enforced; expired ASKs surfaced; R-RET detection feeds the ruled-bases consolidator (never a standalone archive basis).
