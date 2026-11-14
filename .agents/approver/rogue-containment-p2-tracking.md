# Tracking: rogue-containment-p2

Plan: rogue-containment-p2 — open-remainder execution plan
Artifact: .agents/shared/planning/rogue-containment-p2/ (plan-overview.md + component-A2 + component-B + component-E2 + decisions.md + architecture-addendum.md; 1382 lines) + grounding bind-race/rogue-port-containment-analysis.md (573 lines)
Tree: /home/nea/ensemble-src-wt-rogue-contain @ feature/rogue-port-containment

## Iteration 001 — APPROVED (FINAL) — 2026-11-14T20:32Z
Worker: approve-worker-rogue-p2 (9860dc1e-54f9-4ef1-a32e-8510d9201df2), skill=plan-approval, cold context, single worker whole-directory cross-file scope (no fan-in graph).
Worker verdict: APPROVED — 0 blocking, 8 non-blocking notes. ~55 load-bearing file:line anchors/test-counts/constants/git-facts independently verified READ-ONLY at tip af2286dd8 (tree moved past the corpus's bf4bfde6e anchor via the 2026-11-14T20:05Z absorb-rebase; every checked anchor still held). C1 harness false-green defect reproduced first-hand (claim planted at $ENSEMBLE_DATA_DIR/port-claims; ENSEMBLE_PORT_CLAIMS_DIR never set; gate never reads it).
Cross-file invariants checked and HOLDING: single-ownership of edits (daemon/__main__.py touched by three phases in DISJOINT regions with stated ordering + Phase-3 gate); no forward symbol references; phase/task numbering coherent incl. addendum 🟡-8 fix applied; ledger-vs-body agreement on all binding constraints diffed (None→degrade/RAISE→78; 6a pre-bind + exhaustion attributes-only; no-knob + pre-A2-tip measurement; per-family marker channels; exact-ensemble_prod predicate + _self_env_marker carve-out).
Aggregation: none required — no downgrades, no upgrades, no new blocking issues (judgment band respected).
Notes carried (non-blocking, from worker):
1. CHANGELOG/version-slot anchor superseded EXTERNALLY: corpus says "consolidated catch-up under v0.30.10 ONLY, no backfill" (overview L6/L81; E2 § CHANGELOG; decisions.md §9) — lane finalize rulings (shared-KV 2026-11-14T20:05Z post-absorb) rule [0.30.12], three attributed subsections, catch-up range v0.29.19–v0.30.9, v0.30.10 = phantom bump commit 3622216d2 with zero tags. The plan's own "version slot from the LIVE census at stage time" (overview L68) + E2 leader-DP-1 absorb the change — worker judged non-blocking; EXECUTOR MUST TAKE THE STAGE-TIME ANCHOR FROM THE LANE KV, NOT THE CORPUS TEXT.
2. AH pin-numbering divergence A2 vs E2 (E2-AH-8=A2-AH-14; E2-AH-9=A2-AH-15; E2-AH-10⊂A2-AH-13; E2-AH-11=A2 §5.6 unnumbered row; E2-AH-12⊂A2-AH-10) — shape union complete/non-contradictory; reconcile via one mapping line in the matrix artifact.
3. Stale S6 census inside E2 (omits BF-M9, AH-M15/M17/M18/M19, A2 AH-M9..M16) — literal S6 execution would under-mutate; matrices declared to carry all rows.
4. BF pin file-attribution drift: E2 header "extends tests/unit/test_db_resolution_guard.py 10→~18" vs B2/addendum landing P1–P10 in NEW tests/unit/test_prod_dsn_fence.py — implementation owner unambiguous (B2).
5. Tip re-bind at merge: corpus shas superseded by absorb-rebase mapping (cbe04f4ff→f11d4635f; ad814dae4→fab975f50; +rescue af2286dd8); two-pin drift guard per lane rulings.
6. factory.py cite drift (:383-396 → ~:378-387), non-load-bearing, substance verified.
7. Live-unit RestartPreventExitStatus check is template-verified-only by design — S9b owns the root-side check (non-blocking if access-refused).
8. Two-sample residual (holder dies between sample-2 and exit) disclosed and attributed (A2 §5.5); claim-evidence path correctly excluded from re-sampling.
Unverified-by-worker (acceptable, disclosed): live unit file (unreadable by design), /proc walk cost re-measurement, a few long-file line boundaries (within drift tolerance).
