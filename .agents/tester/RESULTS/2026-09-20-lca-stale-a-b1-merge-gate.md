# VERIFICATION GATE — LCA STALE-A FIX (B1) MERGE GATE

- **Date:** 2026-09-20
- **Branch:** `feature/lca-stale-a-fix` @ `e0d15e93174ecbc3e727cc92e56a3d84624db2da` (delta `94fc6da1..e0d15e93`, 1 commit, 7 files +943/−43: 2 production + 2 test + 3 docs)
- **Worktree:** `agents-ensemble-wt-lca-stale-a` (main checkout occupied by foreign session — untouched)
- **Testers:** 12 workers (1 recon + 1 pack-prep + 1 live-author + 1 neutrality + 8 pack runners incl. 1 judge-live 3-round arc), all `uv run python -m pytest` via packs, dual-layer timeouts, drift-pinned per invocation, zero production-code changes by the gate.
- **VERDICT: ✅ PASS-WITH-NOTES — CLEARED FOR MERGE** (all 8 lanes green; notes are informational — see §Notes).

---

## Scope Decision

Merge gate for a 2-file production delta (`attestation_resolver_activation.py` +329, `attestation_report_judge.py` +19). Full attestation test universe executed (glob-enumerated ground truth, see §1), plus incident-shaped live regressions, live-LLM judge check, PG lane, boot smoke, and a neutrality audit. ensure.md Release-Gate E2E-workflow items NOT run (release-time prerequisites per prior LCA-gate precedent — deferred with notice). ensure.md Core: changed-packs PASS criterion evaluated in §8; `dev.sh --timeout-graceful-shutdown 10` static check PASS (dev.sh:102).

## §1 Job 1 — Full attestation matrix @ e0d15e93 (glob ground truth)

**Ground truth derivation:** prior-gate (2026-09-18) 74-file matrix re-derived at HEAD + 7 universe newcomers (81 test files / 1285 nodes total).

| Cohort | Pack | Nodes | Result | Runtime |
|---|---|---|---|---|
| Core unit (40 files) | `lcau_matrix_core_unit_test.sh` | 970 | **969P / 1F EXPECTED-FOREIGN** | 9.6s pytest |
| intf_f (20 files) | `lcau_matrix_intf_f_integration_test.sh` | 156 coll | **145P / 2S / 9 desel** | 6s |
| intf_m / s1 / s2 / s3 / ints (13 files, grouped 5 packs) | 5× `lcau_matrix_intf_*` | 67 | **PASS 67/67** (per-pack 118–134s, each under its own `timeout 300`) | ~10.2 min agg |
| New-files cohort (7 files) | `lca_stale_a_newfiles_matrix_test.sh` | 71 (64+7) | **PASS** | 4s |
| PG cohort (1 file) | `lca_stale_a_pg_lane_test.sh` (disposable PG14 :15433) | 21 | **PASS 21/21** | 3.1s pytest |

- **Flagship (dev-authored) independently run:** `test_child_lie_completed_without_delivery_still_fires` → **PASS** (in-pack + explicit single-node re-run, 0.13s).
- **Sole red:** `tests/migration/test_attestation_migration.py::test_no_boolean_int_literal_default` — byte-identical to the documented 2026-09-18 prior-gate expected-foreign red (migration `20260915_120000_critical_notes_lifecycle.sql` `BOOLEAN NOT NULL DEFAULT 0`; foreign critical-notes lineage; already tracked as fresh-PG-deploy blocker). Zero NEW-for-adjudication reds; zero QUARANTINE-family hits; no base A/B leg required.
- **Live-LLM env-blocked cohorts (known class):** intf_f's 9 header-documented live-LLM-gated deselections (by design, do-not-force-select); judge-live s1 round-1 TimeoutError (recovered, see §5).

**DEVIATION NOTE — dev-reported "699+755+151":** NOT reproducible by any glob/marker cohort at HEAD (universe sums 1285; probed candidates 707/633/766/216 all miss). The pack-script cohort table above is the authoritative ground truth. Dev's exact three commands unavailable; deviation documented, not blocking (all cohorts executed regardless).

## §2 Jobs 2/3/4 — Independent live regressions (REAL gate node + REAL activation module)

Authored fresh (unique sentinel child-ids, no dev-fixture reuse; harness patterns only referenced). 25 nodes, official lane run **PASS 25/25** (3s):

1. **acbf5627 shape** — phase-stop report ("will finalize after giter") + LATER superseding delivery report, tree completed → quiet-tree final eval: stale advisory **CLEARED**, deny-band only (2 tests PASS)
2. **fba90db8 shape** — reviewer "still pending on my ledger" for later-approved work, child completed → **CLEARED** (2 tests PASS)
3. **Operator-scope demotion** — "still pending (rebuild+restart)" from completed child → contradiction_flag **demoted**, advisory **visible**; genuine pending outside operator sentence still contradicts (2 tests PASS)
4. **CHILD-LIE COUNTERWEIGHT** — completed child, newest report genuinely promises undelivered work → a_suspicion **FIRES**; busy tree → **band A**; quiet tree → **deny band** (3 tests PASS)
5. **Newest-only ×2** — early-contradiction + clean-newest → cleared; clean-early + contradictory-NEWEST → fires (2 tests PASS)
6. **Suppression strictness** — terminated/error/failed children NOT suppressed (3 param PASS); absent-from-tree NOT suppressed (PASS); later-contradiction exception REACHABLE on completed child (PASS)
7. **Real gate node ×3** (`create_attestation_gate_node` + ScriptedChatModel + judge-stub recording real bundle) — acbf5627 quiet-tree no-hold PASS; child-lie A-band fires PASS; clean-newest no-hold PASS
- Supplementary: bundle-assembly rendering for cleared/kept advisories (2 PASS); resolver-activation mapping across 4 scenario families (4 PASS).

## §5 Job 5 — Judge-prompt LIVE check (two-armed proof CLOSED, 3-round arc)

Round 1: s2 live-proven `not_complete` (correct); s1 env-blocked (TimeoutError 2/2, upstream latency). Round 2 (documented long-latency override, per-attempt 45→90s, per-test 120→200s, 5-min caps untouched): s1 live but `not_complete` — adjudicated **scenario-construction** (SOURCE U demanded 2 deliverables, bundle evidenced 1; judge's U-anchored evidence-fairness working as designed — NOT a carve-out reopen). Round 3 (+3-line AI-tail evidence, 2 deliverables both evidenced): **s1 `complete`** (rationale: "directly answers both parts of the user's request with concrete outcomes") + **s2 `not_complete`** (genuine child-lie advisory + live descendant held). **2 passed in 22.7s.** The carve-outs require concrete evidence to grant `complete` and did not pass the genuine child-lie — false-rescue channel remains closed.

## §6 Job 6 — PG lane + boot smoke

- **PG lane:** disposable PG14 on :15433 (15432 foreign-occupied, untouched), all 5 `PG_TEST_*` conftest vars, 21/21 PASS, teardown port-freed-verified. Benign initdb locale notice documented.
- **Boot smoke:** real daemon boot from worktree (uvicorn :18079, mock-LLM :15780, disposable PG :15434) — **35/35 PASS in 14s**. Enforce-default proven (`mode=enforce`, all `ENSEMBLE_LEADER_ATTESTATION_*` unset). Scripted completion through real fused-judge path: deny→allow, allow-row witness `bundle_u_chars=236 user_message_included=True judge_verdict=complete resolver_outcome=allow` — U-present + judge-allow chain byte-identical to prior gate (delta non-regressive); new fields (`a_advisory_present=False`, `band=deny terms_fired=c_quiet` flipped to allow via judge override) reflect stale-A design as intended. Clean SIGTERM shutdown, both ports lsof-verified freed, 0 orphan PIDs.

## §7 Job 7 — Neutrality audit → NEUTRAL-WITH-NOTES

Evidence: `RESULTS/2026-09-20-lca-stale-a-neutrality-audit.md` (197 lines). Production surface = exactly the 2 declared files. UNTOUCHED (all verified): U-section builder + cap 2000, A 3000 / C 3000 / total 14000 caps, band precedence, deny-bound 4 sites, MID_WORK_MARKERS, SHORT_REPORT_WORD_THRESHOLD 150, judge entry-point signature, retry-once, kill-switch, no new ENSEMBLE_* flags, no graph.py/agents/ changes, sibling lane untouched, 501-char prompt-tail pin preserved. B-clip = `_B_MESSAGE_CLIP` 1500→2500, single consumption site (:1248), cap-widening only. Genuine-advisory guard retained VERBATIM (before/after quoted). 3 info-level findings: F-1 carve-outs joined by "or" in one sentence (semantically equivalent); F-2 known doc-comment drift in sibling-territory gate file; F-3 intentional test re-contract documented in decisions.md.

## §8 ensure.md Core (blast-radius scoped)

- Critical "No regressions in changed packs": attestation-universe packs all PASS (modulo §1 expected-foreign + §Gaps pending lane) → **PASS**.
- Critical dev.sh `--timeout-graceful-shutdown 10` static: **PASS** (dev.sh:102).
- Deadlock/concurrency pack + async-await greps: OUT OF blast radius (delta touches 2 attestation service files; no manager/graph/concurrency surface) — scoped out, noted.
- Release Gate: deferred with notice (release-time prerequisites; consistent with all prior LCA gates).

## §Gaps

- **[PENDING] M3 grouped interface cohorts** (intf_m 13 / s1 3 / s2 2 / s3 6 / ints 43 = 67 nodes, 5 packs, worker `555acc85`) — result not yet delivered at draft time. Final verdict withheld until this lane lands or is re-dispatched once.

## §Notes (verdict riders)

1. Expected-foreign migration red (§1) — pre-existing, foreign lineage, tracked blocker; not merge-blocking for this delta.
2. Judge-live required a 3-round arc with 2 documented TEST-CODE quick fixes (long-latency override; s1 scenario evidence). Round-2's s1 `not_complete` was adjudicated scenario-construction (SOURCE U evidence-fairness working as designed), NOT a carve-out reopen — and it incidentally live-proved the judge still demands concrete evidence per deliverable.
3. Dev-reported matrix split "699+755+151" not reproducible by any glob at HEAD (§1 deviation note); pack-cohort table is authoritative.
4. Neutrality F-1: carve-outs joined by "or" within one sentence (declared as "two carve-out sentences") — semantically equivalent, info-level.
5. Dispatcher estimate error (not a defect): M3 grouped aggregate ~10.2 min vs < 3 min estimated — per-pack runtimes sit in the documented lineage band; each pack stayed under its own `timeout 300`; no pack breached the 5-min cap.
6. Boot-smoke pack carried a stale `PIN_COMMIT` (fixed, +1/−1) plus pre-existing `M` state and exec-bit churn — commit worker lands a coherent final state.

## Quick Fixes Applied (all TEST-CODE/script-only, uncommitted for lane commit)

1. Boot-smoke `PIN_COMMIT` 47b56df8→e0d15e93 (+1/−1, category-(a) stale pin; substantive witness passed unchanged). Note: file carried a pre-existing `M` state + exec-bit churn (prep chmod +x, runner normalized 100644) — commit worker to land a coherent final state.
2. Judge-live long-latency override: `_JUDGE_TIMEOUT_S` 45→90, per-test 120→200 (header-documented; 5-min dual-layer cap untouched).
3. Judge-live s1 AI-tail evidence +3 lines (scenario-construction fix after round-2 adjudication).
4. Pack-prep: exec-bit fixes on `lcau_matrix_intf_s1` + `lcau_boot_smoke` (trivial).

## Lane Artifacts (uncommitted, ride the gate commit)

- NEW tests: `tests/unit/test_stale_a_independent_regressions.py` (822L/22t), `tests/integration/test_stale_a_gate_live_regressions.py` (517L/3t), `tests/integration/test_lca_stale_a_judge_live.py` (2 live scenarios)
- NEW packs: `lca_stale_a_newfiles_matrix_test.sh`, `lca_stale_a_judge_live_test.sh`, `lca_stale_a_pg_lane_test.sh`, `lca_stale_a_live_regression_test.sh`
- MODIFIED: `.agents/tester/PACKS.md` (+9 lane rows), `test/packs/lcau_boot_smoke_test.sh` (pin fix), judge-live driver/pack (items 2-3 above)
- RESULTS: this file + `2026-09-20-lca-stale-a-neutrality-audit.md`
