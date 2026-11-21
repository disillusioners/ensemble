# Plan Overview — Tool-Usage Metrics, Phase 1 (measurement slice)

**Commission:** ENSEMBLE-37 (TokenEconomizer adoption) — Phase 1 measurement slice, Emperor-approved 2026-11-21.
**Lane:** plan-only (planner → architect enrichment → reviewer → approver). Zero implementation in this lane.
**Worktree:** `/home/nea/ensemble-src-wt-tool-metrics-p1` @ branch `feature/tool-usage-metrics-p1`, base `0b5624dc9` (= origin/latest at lane open).
**Status:** COMPLETE — 5 phase plans + decision ledger delivered; architect pass follows.
**Prior evidence salvaged in-branch:** `tokeneconomizer-study/` (5 files incl. `writing-block-tiering.md`) + `.agents/shared/writing-block.md` — commit `0dbf8d180` (the whole study set was absent from the worktree, not just the tiering doc).

---

## Objective

Build the measurement slice that, after ~7 days of operation, lets the data alone answer **"which tools go to layer 2 per project"** (writing-block Hybrid Tool Architecture dispatcher tier; ranking key = schema token weight × call frequency; zero/near-zero usage = strongest candidates; per-agent allow-list intersection matters). Four deliverables: durable per-(project × tool × day [+ agent]) counters, an aggregate-only central metrics family, FE visibility on `/metrics`, and a deterministic daily 08:00 ICT digest — released staged, zero live contact, Emperor's ceremony owns the flip.

## Phase map

| Phase | File | Objective | Exit gate |
|---|---|---|---|
| 1 — BE ledger + tap + door | `phase1-plan.md` (26,326 B) | `tool_usage_daily` table (aggregated counters, NAMES ONLY, per-agent dimension), tools-node observation wrapper, off-loop queue+flusher, additive migration, `/api/tool-usage/*` domain door, collection kill-switch | §7 |
| 2 — Metrics family | `phase2-plan.md` (18,008 B) | F24 `ensemble_tool_usage_calls_total{tool_category, result}` via new `wiring/w5.py`, registrar-only, band 134≤160, adjudication/catalog/pack amendments, independent kill-switch | §11 |
| 3 — Digest + delivery | `phase3-plan.md` (19,894 B) | Deterministic SQL digest tool + rollup (weight×frequency sort, zero/near-zero + per-agent sections), idempotent boot-time seeder (08:00 Asia/Ho_Chi_Minh), GC-safe, activates at promote | §12 |
| 4 — FE tab | `phase4-plan.md` (35,167 B) | 9th `/metrics` tab (slot `custom`, type-system-ruled), new domain-door service, LAZY tab body (first dynamically-imported tab; exact-cover compile contract preserved), house SVG, B4b ≤ 185,000 B | §8 (verification gates; no literal "exit gate" heading — GAP-1 in phase5 §5.4) |
| 5 — Release tail | `phase5-plan.md` (30,895 B) | Non-reorderable consolidated gate sequence G1–G7, integration sweep I1–I4, staged release via house `stage.sh live` (4-way sha, `rollback_safe=true`), hard STOP — no flip | §12 |

Decision ledger: `decisions.md` (20,706 B) — rulings D1–D11, explorer corrections, carried open questions.

## Decision rulings (A–F → D1–D11)

| Commission point | Ruling | Ruled by | Where |
|---|---|---|---|
| A Ledger schema | Aggregated counters; PK `(day, project_id, tool_name, agent_id)`; agent sentinel `'unknown'`; project sentinel `""`; NAMES ONLY frozen | dispatcher (D1, D11) | `decisions.md`, `phase1-plan.md` §3.1 |
| B Tap point | Tools-node OBSERVATION WRAPPER in the `wrapped_tools_node` chain — evidence: NO existing slot observes tool executions (`message_tap` = ids-only; `tools_node` explicitly out of scope); 10-slot map untouched | dispatcher, evidence-locked (D2) | `phase1-plan.md` §3.2/§4 T4-T5 |
| C Metrics family | Aggregate-only F24; labels `tool_category` (existing vocab, NO version bump — stays 11) + `result{ok,error}`; NO project/agent/tool-name labels (decision-module B-2); independent kill-switch under global gates | dispatcher (D8) + worker detail | `phase2-plan.md` §3 |
| D FE | 9th tab slot `custom` (ruled from type system: `MetricsCentralTab` requires `familyIds`); domain door (first project-scoped); LAZY tab body; B4b 185,000 INVARIANT | dispatcher (D9) + worker ruling | `phase4-plan.md` §3/§4 |
| E Digest + delivery | Deterministic SQL rollup tool (`tool_usage_digest`); agent=ari message; boot-time idempotent seeder → automatic activation at promote; fallback one-liner in appendix | dispatcher (D10) | `phase3-plan.md` §3/§11 |
| F Migration | Additive-only: register-before-`create_all` (memoir precedent) + own `.sql` (SQLite path + audit record); rollback-safe (fix-loss only) | dispatcher (D5) | `phase1-plan.md` §4 T2-T3 |

**usage_metadata assessed and rejected as the source** (per-LLM-invoke token usage, `graph.py:3881-3896`; not per-tool-call; not persisted) — assessment recorded in `phase1-plan.md`.

## Frozen contracts (index — each phase's §3 is authoritative)

1. Ledger schema + NAMES ONLY (`phase1` §3.1) — no argument/argv/content column can exist by construction.
2. Observer sink signature `(tool_name, tool_category?, agent_id, project_id, ok)` (`phase1` §3.2) — phase 2 consumes with zero phase-1 edits.
3. Door `/api/tool-usage/{summary,daily}` shapes + replicated auth posture (`phase1` §3.4) — phase 4 consumes; drift = contract-break flag, never silent adapt (`phase5` I3).
4. Off-loop write contract (`phase1` §3.3): bounded queue → batched background flusher; ledger failure never fails a tool call.
5. F24 closed label spaces + band + `VOCABULARY_VERSION==11` (`phase2` §3) — a version bump is a contract-break flag.
6. B4b lazy-only fetch set ≤ 185,000 B; `bundle-baseline.json` INVARIANT (`phase4` §4.2).
7. Gate order G1→G7 non-reorderable + NO-LIVE-FLIP boundary (`phase5` §3.1/§3.2).

## Cross-phase integration invariants (checked at phase5 I1–I3)

- `daemon/manager.py` THREE disjoint hunks: phase 1 × 2 (model registration ~:1006-1020; flusher/kill-switch boot ~:1143-1150), phase 3 × 1 (seeder call post-:1166). Line-disjointness proven textually before commit (phase5 §5.1).
- `daemon/routers/__init__.py`: exactly one registration line + one `__all__` entry (phase 1 T8).
- Single-ownership sweep done at plan level: every product file edit claimed by exactly one phase task; no duplicates found.

## Research provenance & corrections carried

Two explorer passes (BE + FE anchors) re-verified every recon anchor in the worktree (base ahead of the recon's main checkout). Corrections carried into `decisions.md` §Corrections:
1. `VOCABULARY_VERSION` = **11** @ `daemon/checkpoint_metrics.py:290` (recon's "10 in wiring/vocab.py" wrong on both).
2. "tester pack `t1_bundle_guard` @ PACKS.md:940" is stale — enforcement = `bundle-guard.js` + `frontend …/metrics.bundle-guard.spec.ts:133` pin.
3. Anchor drift table (graph.py slots +17; agent_node sites +~350-375; taps +~370; manager.py seed :1095-1117→:1144-1166; decision/kill.py →:105/:128-131).
4. `fc9_baselines.json` carries NO per-tool weights (aggregate ~230/~269 only) → phase 3 rules a committed per-tool weight artifact + 230-average fallback (`phase3-plan.md` §3.3).
5. `SYSTEM_DEFAULT_PROJECT_ID` is set at lifespan (`api.py:2979`), after the boot seed site → seeder derives the id via `ensure_system_default_project()` (`phase3-plan.md` §9.9).

## Adjudicated by the planner (open questions resolved)

- **Per-tool weights:** ACCEPT committed artifact + 230-average fallback for P1 (deterministic, no scope creep); per-tool measurement lane = R2+ work, stays parked on the PM ledger.
- **`stage.sh live` guard (phase5 OQ1):** ACCEPT the house form — `ENSEMBLE_UPGRADE_LIVE=1` at STAGE time is the live-dir write guard (`stage.sh:78` "NO FLIP", `:190 require_live_guard`), matching house staging precedent; the flip boundary (`promote.sh` never runs) holds. Carried for lane-leader confirmation in the report, not blocking.
- **Digest-tool holder set** (inherits to jober via `scheduling` category): acceptable with disclosure; architect may tighten to a dedicated category.
- **Digest destination:** runtime-canonical resolution anchored on the live project tree's planning dir (`phase3-plan.md` rules the mechanism); architect validates.

## Delegated to the architect pass (flagged, not blocking)

- Feasibility validation of the LAZY tab body mechanism (phase4 argues from `app.ts:391` precedent; fallback = explicit leader re-baseline per R-08, never a silent threshold edit).
- F24 numbering confirmation (next-slot derivation from catalog §1) and band headroom taste.
- Phase-4 heading inconsistency (GAP-1): use `phase4-plan.md` §8 as its functional exit gate.

## Top risks (full registers per phase file)

1. **B4b headroom exhaustion** (134 B) — mitigated by the lazy tab body being the PRIMARY strategy, not a fallback (`phase4` R-table).
2. **manager.py hunk collision** — textual disjointness proof owed before commit (`phase5` I1).
3. **Determinism drift in the digest** — weight artifact + fixed fixture byte-identity test (`phase3` G5).
4. **Contract drift door↔FE** — I3 field-by-field re-check; drift = flag, never silent adapt.
5. **Stage-argv misread** (R4, critical/low) — `promote.sh` never invoked; live fence byte-identity check post-stage.

## Execution order

Phases 1 → {2, 3} → 4 → 5 (phase 4 may parallel 2/3 — the door contract is frozen; phase 5 consolidates). Gates: `phase5-plan.md` §4 G1–G7; staging: §6 (STOP at staging); activation disclosure: §8.

## File index

`decisions.md` 20,706 B · `phase1-plan.md` 26,326 B · `phase2-plan.md` 18,008 B · `phase3-plan.md` 19,894 B · `phase4-plan.md` 35,167 B · `phase5-plan.md` 30,895 B · this overview. Total plan set ≈ 151 KB + overview.

*Synthesized by the planner from worker outputs (explorers: BE/FE anchor passes; workers: be-core, be-analytics, fe, ops-tail). Workers own the phase content; this overview cites and links it.*
