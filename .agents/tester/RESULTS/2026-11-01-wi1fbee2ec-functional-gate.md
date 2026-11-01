# wi-1fbee2ec Functional Gate — report-obligation visibility + WC-orphan detection

**Commission:** wi-1fbee2ec · **Gate:** tester functional gate (mandatory, last before staging)
**Branch:** `fix/report-obligation-visibility` @ `8e76897e3d40635b00fd2fbac2d64013dc555fbb` · **TRUE BASE:** `f7039c3ffc8627166bd9267c6e2c588b71476206`
**Worktree:** `/home/nea/ensemble-src-wt-obligation` (fence: PORT=8079, POSTGRES_DB=ensemble_dev, ENSEMBLE_SELF_ENV=dev, .env 600 — verified)
**Date:** 2026-11-01 · **Tester dispatch:** 10 workers (recon + 9 gate lanes + 1 disposition follow-up)

## VERDICT: **PASS-WITH-DISCLOSURES — GO** (conditioned on routing the G9 test-side remediation package; see §G9)

The original symptom family is CLOSED at HEAD and proven absent at BASE (both directions, both checkpoints); all 8 mutation legs independently re-proven; the pin corpus is 60/60 green; no regression to the live v0.28.2 strand; PG widening verified on real PostgreSQL; net-new = 9 reds, ALL adjudicated stale pre-widening expectations (zero behavior regressions) with a REQUIRED remediation package routed.

---

## Gates table

| Gate | Verdict | Evidence (one-line pointer) |
|---|---|---|
| **G1** original-symptom (obligation leg, both directions) | ✅ PASS | BASE: `TERMINAL_AFTER_BOUND` close caught behaviorally (gate_pins @ base: `assert <TERMINAL_AFTER_BOUND> is <ALLOWED_LEGITIMATE_PENDING_WAKEUP>`) + emission repro `(True, None, False)` with obligation outstanding == delivered-counterfactual verdict (blind spot proven). HEAD: hold persists at/past bound, both checkpoints. Logs `/tmp/wi1f-g1g2-*` |
| **G2** dogfood window (pin e, both directions) | ✅ PASS | BASE: in-turn walk reads `(busy=0, live=0)` on minted-IDLE → verify-clean `TERMINAL_AFTER_BOUND`. HEAD: `live=1` → hold (`test_minted_dormant_child_holds_at_bound_not_verify_clean` + 3 surfaces green). Delta is in-turn only — BASE emission gate already blocked the shape via tree-liveness (documented asymmetry) |
| **G3** mutation re-proof (8/8, independent) | ✅ PASS | All 8 legs: behavioral RED with EXACT receipt FAILED-id parity, zero collateral, dual-proof restore, GREEN. **M4 RED confirmed behavioral** (un-wrapped `RuntimeError` propagates at runtime from `_root_completion_gate`; no SyntaxError/collection error). M8 two-anchor hunk parity 2==2. Logs `/tmp/wi1f-g3-M{1..8}.*` |
| **G4** receipts archive | ✅ PASS | `RESULTS/2026-11-01-wi1fbee2ec-receipts/` (8 leg dirs + adapted harness incl. `run_receipt.sh` — discrepancy RESOLVED by commit, see §run_receipt) + gate evidence logs + README provenance |
| **G5** pin corpus (3 packs) | ✅ PASS | **60/60** green @ HEAD: emission 22/22 (31.6s) · wc_orphan+suppression 14/14 (18.3s) · gate 24/24 (15 nominal → 24 expanded; 9-case polarity matrix individually verified: 6 hold / 2 close / 1 denied — zero drift). Registered in PACKS.md (newest-first block). Census disclosure: briefed 59, actual 60 |
| **G6** D1 × UNKNOWN-ROW | ✅ PASS — NO HOLE | Co-present `MISSION_READ_UNKNOWN` + genuine obligation-read error → gate returns `(False, "outstanding_report_obligations=unknown(read_error)", False)`, wedge resolver never consulted; 3/3 scratch-pin green incl. non-vacuity control (quiet obligation → verify-clean through resolver). Coverage gap existed (zero co-present pins) → scratch pin written, run, deleted; source archived in §G6-pin. In-turn adjacent observation routed (§Routed R2) |
| **G7** no-regression (v0.28.2 strand) | ✅ PASS | `instance_messaging.py` byte-untouched (targeted diff EMPTY — T1 funnel-top / carve-out / revive-on-send seams structurally preserved). T1/T2/T3 16/16 id-green, RDRS service 43/43, injection substrate 64/64. Compose-both-directions = pin (d) family 6/6 (G5) + M8 RED→GREEN (G3) |
| **G8** WC-orphan detector | ✅ PASS | Fires on direct shape + pure-WC-grandchildren (live WARNING lines `report_delivery_recovery.py:1855/:1866`); detection-only (post-fire DB re-read: statuses untouched); kill-switch `Field(default=True)` @ `daemon/config.py:2142-2143` + disabled-lane-removal pin; counters `wc_orphan_direct`/`wc_orphan_pure_chain`/`skipped_*`/`suppressed_root_terminal` all fire (7× direct, 1× pure_chain, 2× suppressed in evidence run; batch cap 3/7) |
| **G9** net-new discipline | ⚠️ **NET-NEW = 9 — adjudicated ZERO behavior regressions** | Scoped 108-file both-revs corpus, same commands both sides (executed 1953; 0 timeouts; 0 excluded). 18 pre-existing byte-identical (T7 trio + 3 known + 12 more, comm-verified). 9 net-new ALL deterministic (3/3 per side), ALL in 3 UNTOUCHED integration files, ALL characterized **class A stale pre-widening** (§G9). REQUIRED remediation package routed |
| **G10** PG serial | ✅ PASS (1 pre-existing class disclosed) | 6/6 legs real PG (scratch role+DB, serial, dropped + verified gone). lca (lane-modified) 21/21 BOTH sides — widening VERIFIED on real PG, delta = 5 expectation-flip hunks exactly matching the doctrine; triple 5/5 both sides; delivery 24P+2F with ids byte-identical at base = pre-existing kill-switch empty-map debt (nuance routed, §Routed R3) |

---

## G9 — the 9 net-new reds: disposition record

**Where:** `tests/integration/test_attestation_idle_orphan_incident.py` (1) · `tests/integration/test_attestation_incident_acceptance_lca.py` (1) · `tests/integration/test_attestation_live_descendants.py` (7: TestBusyClassification ×1, TestFacadeBfsCap ×6). Lane touched NONE of them (`git diff f7039c3ff..HEAD -- tests/integration/` empty).

**Characterization (disposition worker, 9/9 rows with fixture+assertion evidence):** every failing assertion expects a dormant **IDLE/QUEUED non-terminal** descendant to be quiet/not-counted — precisely the semantics the 2026-11-01 dogfood adjudication overturned ("descendant-liveness counting treats ANY non-terminal descendant as non-quiet"). HEAD actuals equal the lane's own flipped PG expectations and green pin values for the SAME shapes (PG↔sqlite cross-map: 0→4, 0→1, 0→1, 2→3 — identical values). Terminal-members-still-excluded tests PASS at HEAD (`manager.py:12970-12971` terminal check first); busy classification untouched (dormant NEVER busy — LCA trigger-suppression semantics unchanged, `manager.py:12950-12954/:12994-12995`); `test_idle_with_unprocessed_message_live_not_busy` + `test_terminal_descendant_not_live_not_busy` pass at HEAD. The two b08f40fe incident ids assert the superseded posture the lane pins honestly as superseded (`test_b08f40fe_orphan_now_reads_nonquiet_superseded`, docstring verbatim in pins :359-370). **0 pin-vs-integration contradictions; 0 ambiguous.**

**Adjudication (tester, per v0.28.3 precedent "stale pre-existing test assertions on architect-ratified contract widening"):** the 9 are the **unfolded sqlite half** of a doctrine update the lane already applied to its own pins + the PG counterpart. NOT behavior regressions. HOWEVER the branch tip carries 9 deterministic default-suite reds until folded — every future whole-tree / merge-composition gate will see them.

**REQUIRED remediation package (routed to the dev lane, fold BEFORE stage recommended):** rewrite the 3 integration files to the widened doctrine — expectation rewrites (incl. inverting #1/#2's branch-5 negative assertions: `allowed_legitimate_pending_wakeup` is now the EXPECTED non-quiet outcome for the incident shape), plus the sqlite module docstring section (h) which still documents the RETIRED two-set doctrine. Fold-first vs staged-disclosure is the leader's call.

## Tester adjudications made this gate (within verification mandate)

1. **G3 Option A:** dev receipts minted at `701c6c27e` (16:12Z) pre-tidier (918cdec7e 16:37Z + aa5f2cd6c 16:39Z landed after) — 5 legs' snapshots stale vs HEAD. Ruled: baselines from the HEAD git object (restoring stale snapshots would have made GREEN legs run at receipt-era code). Drift diff proven non-executable (`/tmp/wi1f-g3-drift-evidence.txt`: attestation_gate 41Δ docstrings, manager 7Δ value-identical reformat, rdr 36Δ value-identical anchoring) — and behaviorally proven by 5/5 Option-A legs passing RED→GREEN at HEAD bytes. Receipt snapshots retained as provenance only.
2. **G10 kill-switch nuance:** HEAD's all-lanes-disabled sweep map now holds 2 offending lanes (new `wc_orphan_detector` joins pre-existing `mission_reconciliation`) — same defect class, byte-identical failing ids at base ⇒ pre-existing, NOT a blocker; the lane's own kill-switch is pinned green at unit level. Routed (R3).
3. **G9 disposition:** above — 9/9 class A; remediation routed, leader rules fold-vs-disclosure.

## run_receipt.sh discrepancy — RESOLVED (option: write + commit)

The re-runner was claimed but absent from the receipts root and all commits (recon: `git log --all` empty both by path and `-S`). It DID exist as a `/tmp`-only artifact (`/tmp/wi1fbee2ec-mut/run_receipt.sh`, hardcoding `WT=/home/nea/ensemble-src-wt-obligation`). **Resolution: the adapted harness (`run_receipt.sh` + `run_mutation.sh` + `m1..m8.py` + `backup.py`, WT repointed to the isolated mutation worktree) is COMMITTED beside the evidence** under `RESULTS/2026-11-01-wi1fbee2ec-receipts/harness/` — the claim is now true and durable against /tmp pruning. Provenance in the archive README.

## G6 scratch pin (source archived; permanent commit ROUTED)

Coverage gap confirmed (no co-present pin existed). Scratch pin `tests/unit/services/test_zz_g6_unknown_row_scratch.py` written in the lane worktree, run 3/3 PASS, deleted, porcelain verified clean. The gate's commit boundary is `.agents/tester/**` only — the pin's permanent home is a dev-lane commit. Three nodes: (1) sentinel + genuine obligation raise → HOLD with reason `outstanding_report_obligations=unknown(read_error)`, resolver `assert_not_awaited()`; (2) facade-raise variant (both production constructions of the sentinel) → same HOLD; (3) non-vacuity control — same arrangement with quiet obligation (0) → verify-clean through resolver `assert_awaited_once()`. Full source: see disposition worker report / `RESULTS/2026-11-01-wi1fbee2ec-receipts/` evidence bundle (g6 pin source captured verbatim in the gate transcript archived with this doc).

## Routed items (leader/dev-lane)

- **R1 (REQUIRED, G9):** fold the 3 integration test files to the widened doctrine (expectation rewrites + module docstring §h + #1/#2 branch-5 assertion inversion). Will redden future whole-tree gates until folded.
- **R2 (observation, G6):** the IN-TURN attestation gate early-returns `ALLOWED` on `MISSION_READ_UNKNOWN` (`attestation_gate.py:1779`) without consulting decide() step 2b, dropping a co-present genuine `report_obligation_read_error` flag (field docstring :476-489 documents the discard). Consistent with the adjudicated review-cycle-1 Priority-7 leg-2 fail-open ruling (byte-frozen polarity) — surfaced for the record, no action requested by this gate.
- **R3 (G10):** kill-switch empty-map seam debt — `test_lane1_disabled_absent_from_sweep_on_pg` + `test_all_lanes_disabled_returns_empty_result` red at BOTH revs; the new lane inherits the pattern (2-off vs 1-off). Pre-existing class; route a kill-switch/lane-map consistency follow-up.
- **R4 (flake ledger candidate):** `tests/test_manager_shutdown.py::TestManagerShutdownDurableBashSweep::test_durable_sweep_sibling_alive_skip_and_dead_writer_swept` — flaky on BOTH sides (BASE P/F/P, HEAD P/P/F; ~4.2s each) — not lane-caused.
- **R5 (cosmetic):** pin name typo `test_minted_dormant_idie_child_is_live_the_dogfood_window` ("idie" → "idle") — rename in the dev lane.
- **R6 (test-infra observation, disposition probe):** `GraphTestManager object has no attribute 'get_tree_ids_permanent'` → resolver fail-open to counts-only C evidence in `test_attestation_incident_acceptance_lca.py` infra — pre-existing, present both sides.
- **R7 (dormant-orphan reaping gap):** unchanged standing routed follow-up from review cycle-1 (aged never-dispatched IDLE orphans non-quiet indefinitely; reaping belongs to healer/WC-orphan detector lane) — the widened gate deliberately routes there; confirmed by #1/#2 disposition.

## Environment / envelope compliance

Zero live contact: :9797, `ensemble_prod`, `/home/nea/agents-ensemble` untouched by any worker; all DBs file-backed SQLite/pytest tmp or the throwaway local-PG scratch (`wi1fbee2ec_g`/`wi1fbee2ec_test` — DROP proven, 0 rows on verification sweep). Per-variable env scrubs throughout (the `env -u POSTGRES_*` glob trap avoided); G9 additionally pinned `ENSEMBLE_SELF_ENV=dev PORT=8079` over the ambient live shell. Both worktrees porcelain-tracked across all runs (HEAD clean at every checkpoint; BASE dirt = only the 2 declared copied pin files). Scratch worktrees (base, mut) removed at teardown; lane worktree + branch UNTOUCHED except `.agents/tester/**` evidence (this commit). No pushes.

## Evidence index

- Receipts + adapted harness + README: `RESULTS/2026-11-01-wi1fbee2ec-receipts/`
- Gate logs (working copies): `/tmp/wi1f-g{1g2,3,5,6,7,9,9disp,10}-*`, `/tmp/wi1f-g9/rc.tsv`, `/tmp/recon-g9-{base,head}.txt`
- Key /tmp-only artifacts also copied into the archive: g3 M1-M8 red/green logs + shas + drift evidence; g5 pack logs + matrix verbose; g7 three runs + collect; g1g2 base gatepins + base emission repro (.py + .log) + head targeted; g9 driver + rc.tsv + disposition probes; g10 six legs + diffs (password file EXCLUDED, secret never archived).

**Overall: FUNCTIONAL GATE PASS-WITH-DISCLOSURES — GO.** Recommend: route R1 (fold-first preferred), then proceed to staging per the lane's own sequencing.
