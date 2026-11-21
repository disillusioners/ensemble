# Plan Overview — Tool-Usage Metrics, Phase 1 (measurement slice)

**Commission:** ENSEMBLE-37 (TokenEconomizer adoption) — Phase 1 measurement slice, Emperor-approved 2026-11-21.
**Lane:** plan-only (planner → architect enrichment → reviewer → approver). Zero implementation in this lane.
**Worktree:** `/home/nea/ensemble-src-wt-tool-metrics-p1` @ branch `feature/tool-usage-metrics-p1`, base `0b5624dc9`.
**Status:** COMPLETE — 5 phase plans + decision ledger, architect pass folded (`fd254b335`), both leader rulings applied (LR1/LR2), and the **review fix pass U1–U16 folded** (council-backed APPROVED-WITH-NOTES-CONDITIONAL conditions discharged; §PREDICATE LEDGER + §CLOSING ATTESTATION appended to `decisions.md`). Approver iteration-001 blockers B1–B7 + carried notes FIXED (attestation re-run honest, 7/7 rows PASS); ready for iteration-002.
**Prior evidence salvaged in-branch:** `tokeneconomizer-study/` (5 files incl. `writing-block-tiering.md`) + `.agents/shared/writing-block.md` — commit `0dbf8d180`.

---

## Objective

Build the measurement slice that, after ~7 days of operation, lets the data alone answer **"which tools go to layer 2 per project"** (writing-block Hybrid Tool Architecture dispatcher tier; ranking key = schema token weight × call frequency; zero/near-zero usage = strongest candidates; per-agent allow-list intersection matters). Four deliverables: durable per-(project × tool × day [+ agent]) counters, an aggregate-only central metrics family, FE visibility on `/metrics`, and a deterministic daily 08:00 ICT digest — released staged, zero live contact, Emperor's ceremony owns the flip.

## Phase map (post-architect)

| Phase | File | Objective | Architect verdict | Exit gate |
|---|---|---|---|---|
| 1 — BE ledger + tap + door | `phase1-plan.md` (42,431 B) | `tool_usage_daily` (sentinel `__none__`), tools-node observation wrapper (amended placement + A-1…A-4), counter-map flusher (A-5*), additive migration, `/api/tool-usage/*` door with replication pin | VALIDATE w/ amendments A-1…A-7 | §7 |
| 2 — Metrics family | `phase2-plan.md` (21,216 B) | F24 `ensemble_tool_usage_calls_total{tool_category, result}` via `w5.py`; **measured total 625 ≤ 2000** (convention corrected; U16b +1 reverted per approver B5); `metrics_gate.py` citation | VALIDATE w/ 2 corrections | §11 |
| 3 — Digest + delivery | `phase3-plan.md` (29,969 B) | Deterministic SQL digest (windows D−1 / [D−7,D−1]; canonical root), uuid5-hardened startable seeder (adapter `schedule=` shape; opt-out key) | **AMEND MATERIALLY** — D-1…D-9 folded (as written it would never fire) | §12 |
| 4 — FE tab | `phase4-plan.md` (46,238 B) | 9th tab `slot:'custom'`; **LAZY host pattern frozen (feasible, HIGH)**; B4b re-baselined **185,000 → 188,000 by leader ruling LR1**; phase 4 owns the threshold-artifact amendment | VALIDATE + leader decision RULED | §8 |
| 5 — Release tail | `phase5-plan.md` (38,408 B) | Gates G1–G7 (updated lists), I1 re-specified as the 5-step gate-time proof, staged release via house `stage.sh live` (LR2 CONFIRMED), hard STOP — no flip | VALIDATE, I1 reframed, OQ1 resolved | §12 |

Ledger: `decisions.md` (41,203 B) — D1–D11 original + **D12–D22 appended** (append-only; D11 superseded by D16). Architect source: `architecture-recommendation.md` (35,533 B, stands as written).

## Leader rulings (verbatim, character-for-character in ledger + governing phase)

- **LR1** (in `decisions.md` D12 + `phase4-plan.md` §3): "R-08 B4b re-baseline: RULED YES — threshold_B4b_route_fetch_set_lazy_only_bytes 185,000 → 188,000. Lazy tab body REMAINS PRIMARY (minimizes incremental fetch-set growth); the re-baseline covers the architect-proven ~1.0–1.3 kB static residue with ~2.2 kB margin. Static fallback REJECTED." The amendment lands in-branch as a phase-4 task (bundle-baseline.json + `metrics.bundle-guard.spec.ts` pin + tester re-measure instruction); expected measured B4b ≈ 185,800–186,200 B.
- **LR2** (in `decisions.md` D13 + `phase5-plan.md` §3.4): "OQ1 stage.sh reading: CONFIRMED — staging uses house stage.sh; ENSEMBLE_UPGRADE_LIVE=1 there is the live-dir write guard, not a flip; promote.sh never invoked; phase5 no-flip invariants (state.json untouched, livez uptime continuous, no service restart) stay as gates."

## Decision rulings (A–F → D1–D22, post-architect state)

| Point | Ruling (amended where marked) | Ledger |
|---|---|---|
| A Ledger | Aggregated counters; PK `(day, project_id, tool_name, agent_id)`; agent `'unknown'`; **project `"__none__"` (D16 supersedes D11)**; NAMES ONLY frozen | D1, D11→D16 |
| B Tap | Tools-node observation wrapper **between compact_note and wrapped_tools_node**; `manager._instance_repository` (A-1); per-batch identity (A-2); 256-char choke (A-3); factory/subclass alternatives REJECTED | D2→D14 |
| C Family | F24 aggregate-only; labels `tool_category` (v11, no bump) + `result`; **measured 625 ≤ 2000 is THE claim** (D18; U16b +1 reverted per approver B5); gauge REJECTED; family key registration rides phase 1's settings edit | D8→D18 |
| D FE | 9th tab `custom`; LAZY host (5-input pattern frozen); **B4b ≤ 188,000 via LR1**; static fallback REJECTED; phase 4 owns the threshold-artifact amendment (D22) | D9→D12/D22 |
| E Digest | Deterministic SQL rollup; **canonical root (phantom `manager.workdir` removed, D-6)**; windows D−1 / [D−7,D−1] (D-7); uuid5 idempotent startable seeder with adapter `schedule=` shape (D-1..D-4); opt-out key (D-5); rollback disclosure (D-9) | D10→D19 |
| F Migration | Additive register-before-`create_all` + own `.sql`; rollback-safe holds; A-7: LimitsConfig field backs the collection key (v0.31.8 crash-class guard) | D5 |

**usage_metadata** assessed and rejected as the source (per-LLM-invoke, `graph.py:3881-3896`; not persisted) — assessment in `phase1-plan.md`.

## Frozen contracts (amended superset — architect §10.6)

(1) ledger schema + NAMES ONLY + sentinels `'unknown'`/`"__none__"`; (2) sink signature (no args slot) + 256-char tap choke; (3) door shapes + replicated auth posture + replication-invariant pin + "project_id is a filter, not a boundary" + 1 MB guard + top-50 agents cap; (4) off-loop contract as amended (counter-map + dirty-set, 30 s/500-key, never-drop, switch-blind flusher with tail flush, explicit 10 s drain before `dispose_db_pools`, main-loop-only emits); (5) counting contract (at-least-once, verbatim in ledger + phase1); (6) F24 closed labels + band 134≤160 + v11 + measured-total convention; (7) B4b lazy-only ≤ **188,000** (LR1) + host-pattern pins; (8) gate order G1→G7 + NO-LIVE-FLIP; (9) digest windows + determinism + canonical root + same-day overwrite; (10) seeder idempotency (uuid5) + opt-out flag + rollback disclosure.

## Cross-phase integration invariants

- `daemon/manager.py` three hunks (h1 registration ~:1006-1020; h2 flusher/kill-switch boot; h3 seeder — **may land ≥ :2113 per D-1**; the enumeration, not the line number, is the claim) proven by the **5-step gate-time I1 proof** (enumerate → internal disjointness → drift re-check vs origin/latest newcomers → file-set sweep incl. `config.py`, `api.py`, `_tool_registry.py` → upstream freshness).
- Three managed keys in phase 1's settings edit: `limits.tool_usage_collection_enabled` (LimitsConfig-backed, A-7), `limits.metrics_tool_usage_enabled` (config_path=None), `limits.tool_usage_digest_seeding_enabled` (config_path=None, D-5) — no-collision gate in phase5 GAP-4.
- Char-for-character discipline: LR1/LR2, the counting-contract blockquote, and shared ruling sentences verified identical between `decisions.md` and the phase bodies (whitespace-normalized; ledger append-only).

## Adjudicated / resolved during the pass

- **OQ1** → leader CONFIRMED (LR2). **R-08** → leader RULED YES (LR1). **OQ2** (lazy feasibility) → FEASIBLE (HIGH; B4b exclusion by construction); re-baseline is for the ~1.0–1.3 kB static residue, not lazy failure.
- Per-tool weights: committed artifact + 230-average fallback stands (measurement lane stays parked, R2+).
- I1 old premise (unmerged `868c6c616f`) corrected — merged ancestor, never touched manager.py; census never cited as proof.

## Top risks (post-amendment)

1. B4b re-baseline dependency — the threshold amendment must land before G4 runs (phase5 ordering note).
2. Seeder activation wiring — D-1/D-2/D-3 were load-bearing (would never fire as originally written); now pinned with tests.
3. Gate-time drift on manager.py — the 5-step proof re-runs at execution; never trusts this plan's census.
4. Contract drift door↔FE — I3 field-by-field re-check; drift = flag, never silent adapt.
5. Rollback noise channel — the seeded scheduler row degrades to an ari error turn post-rollback (disclosed, D-9).

## Execution order

Phases 1 → {2, 3} → 4 → 5; gates G1–G7 (`phase5-plan.md` §4); staging §6 (STOP at staging); activation disclosure §8. Both activation surfaces automatic at promote (FE bytes + boot seeder).

## File index (post-amendment)

`plan-overview.md` (this file) · `decisions.md` 41,203 B (incl. §PREDICATE LEDGER + §CLOSING ATTESTATION, iteration-001 corrected) · `phase1-plan.md` 42,431 B · `phase2-plan.md` 21,216 B · `phase3-plan.md` 29,969 B · `phase4-plan.md` 46,238 B · `phase5-plan.md` 38,408 B · `architecture-recommendation.md` 35,533 B (architect-owned, unamended). Set total ≈ 265 KB.

*Synthesized by the planner. Workers own phase content; the architect doc governs amendments; this overview cites and links both.*

## Approver iteration-001 fold (dispatcher notes)
- **A1 canonical statement:** the per-phase "§0 Dispatch mode" sections across phase1–5 record each file's authoring history (sanctioned parallel-writer variance); THIS overview is the canonical dispatch record: bodies by worker instances, ledger by worker instances, rulings LR1/LR2 lane-leader-owned, architect doc architect-owned and unamended.
- **A6 commission disclosure:** the F24 family ships **wired-but-gated** — the global `limits.metrics_enabled` defaults False in the target deployment, so central-metrics emission starts only when an operator enables it at/after promote. The ledger, door, digest, and FE tab are NOT gated by that key (collection key `limits.tool_usage_collection_enabled` defaults ON).
- **B5 final state:** measured closed-space total **491 today + 134 = 625 ≤ 2000** (architect §5 stands as written; the U16b +1 renumbering was proven derivable-by-no-convention and reverted; full arc in `decisions.md` D18 + correction entry).
