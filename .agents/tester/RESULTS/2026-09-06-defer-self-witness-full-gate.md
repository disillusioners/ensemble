# Full Gate — fix/defer-self-witness-and-cleanup @ f9ee8cc0 (→ 26a7e625 test-only)

**Date:** 2026-09-06 · **Base:** `afd7c387` (26+1 commits, 37 files: 12 daemon/, 12 tests/, 11 FE, 2 docs)
**VERDICT: ✅ PASS — merge green-lit.** Zero branch-caused behavioral regressions; the one branch-caused
failure (route-count ledger pin) fixed test-only (`26a7e625`) and re-verified; the incident wedge is
structurally dead with RED-at-base proof; destructive FE surfaces browser-verified.

Workers: 30 total (1 ground-truth, 1 venv-audit, 2 FE, 12 partitions, 2 census, 4 incident-class,
1 E2E quartet, 4 attribution, 1 quick-fix, 1 web-automation, 1 ensure-static). Verification-only
except the single test-ledger commit. ~19.6k test executions (≈16.9k partition + 233 scoped + FE 2,459 + incident/PG/solo runs).

---

## 1. Full regression — 12 partitions @ f9ee8cc0 (quarantine-aware, no -x, SSL unset, timeout 300 each)

| Partition | Result @ HEAD | Attribution |
|---|---|---|
| P1 regression_unit_tools | ✅ PASS 1,155P/0F/1S (12.0s) — exact baseline | — |
| P2 regression_unit_services | ⚠️ FAIL-by-baseline 1,273P/7F — proxy_phase1 family (ledger 8F; −1) | baseline |
| P3 regression_unit_smaller_subdirs_routers | ✅ PASS 620P/0F/0E (11.9s; +81 growth incl. test_27-file + defer_holder_actions) | — |
| P4 regression_unit_loose_a_d | ⚠️ FAIL-by-baseline 1,180P/10F/21E — exact parity (api-size 2067 + misc ×9 + slash fixtures ×21); +163 growth all-passing | baseline |
| P5 regression_unit_loose_e_l | ⚠️ FAIL-by-baseline 1,110P/11F — exact (misc ×9 + llm ×2); new fe_dialog_survivor 5/5 PASS | baseline |
| P6 regression_unit_loose_m_r | ⚠️ FAIL-by-baseline 1,842P/8F → **after 26a7e625: 7F baseline-only** (models_split ×1, phase4 ×1, paused_auto_resume ×5); route-count pin fixed | 1 branch-caused LEDGER-CHURN → FIXED |
| P7 regression_unit_loose_s_z | ⚠️ FAIL-by-baseline 980P/54F/2E/11S — watchover 47 + ledger 5 + vision/terminal_reason + webfetch 2E; task_reconciliation row-16 partial (2/6 fire); +11 growth passing (logging-pin file green) | baseline + quarantine family |
| P8 regression_top_level_a_h | ⚠️ FAIL-by-baseline 1,025P/22F/1E — test_api ×2 + misc ×16 + jsonb ×3 (quarantine ctx-flake) + **perf ×2 → LOAD-FLAKE** (3/3 solo whole-module PASS) | baseline + quarantine + WATCH |
| P9 regression_top_level_i_q | ⚠️ FAIL 2,368P/60F — injection ×26 + sqlite-migration ×18 + named singles ×3 (47 baseline) + **13 NEW-flagged → all PRE-EXISTING-AT-BASE** (byte-similar; latest-lineage drift post-09-03-ledger) | 13/13 exonerated |
| P10 regression_top_level_r_z_misc | ⚠️ FAIL-by-baseline 2,260P/12F stable ×3 runs — ALL quarantine families (spawn_limit migration ×9, skill_evolution ×2, terminal_orphan ×1); −2 vs baseline | quarantine families |
| P11 regression_job_queue | ⚠️ FAIL 1,670P/7F/38S (+58 growth) — **7/7 = QUARANTINE row 11** (node-for-node; worker misread corrected at leader; sealed: 7F @ HEAD ≡ 7F @ base afd7c387, 41-line normalized signature diff EMPTY) | quarantine row 11 re-verified |
| P12 regression_integration_opencode_e2e | ⚠️ FAIL 913P/12F/24E — httpx family ×19 baseline; **9 unattributed → 9/9 PRE-EXISTING at base** (8-mirror reconcile ×4 byte-identical; pause/answer e2e ×4; W7 mock-migration ×1); bucket5 (WS4) ZERO F/E | 9/9 exonerated |

**FE:** jest **2,459/2,459, 70/70 suites** (11s, --no-cache, rev-parse bracketed; suite grew 1,931→2,459, all
branch-suspect suites green incl. new cleanup-preflight.model.spec) · tsc **0 errors** · build **exit 0**
(via EXPECTED_BRANCH pin; run-1 false-DRIFT = stale pack default). ⚠️ warning composition drift 4→10
(2 deprecations + 6 bundle budgets + 1 initial-bundle + 1 NG8113) — `jobs.component.scss` budget warning
sits in a branch-changed file; non-gating per pack design (informational).

**ensure.md Release-Gate E2E (real-LLM, one-by-one):** happy-path ✅ 237s · terminate-revive ✅ 147s ·
3-level cascade ✅ 271s · pause-resume = QUARANTINE-EXEMPT (row 2026-08-21). 9 instances spawned,
queue leftovers cleaned pre-run, own daemon tree killed, 8079 freed, 8088 untouched.

## 2. The incident class — all 4 verified

- **Self-witness differential: DIFFERENTIAL-PROVEN.** Incident shape (settled mirrors + revived instance +
  own defer candidate) encoded by `test_ws1_self_witness_carveout_admits_candidate` (idle-for-candidate) +
  `test_ws1_carveout_excludes_only_self` (busy-for-others) — **PREDICATE-RED at base**: `TypeError … got an
  unexpected keyword argument 'requester_instance_id'` at the gate-predicate call (the wedge: base predicate
  rejects requester-aware queries). HEAD 5/5 green in 0.73s. 3 watchdog-detector nodes are ENV-RED at base
  (branch scaffolding absent) — correctly classified, orthogonal evidence.
- **Carve-out PG parity: MATRIX-EXECUTED-PASS (4/4 cells on real PG 14.22).** `test_ws1_carveout_pg_sqlite_parity`
  via disposable `ensemble_dsw_parity_gate` (created→dropped FORCE, prod untouched; `PG_TEST_HOST=localhost`
  — /tmp socket is not a URL host). Every cell `pg_result is expected` + `sq is pg` + bool-typed. 693a4ffc
  AmbiguousParameter class does not reappear. ⚠️ sibling 2-body baseline node has the known pre-existing
  `_insert_queue` int→bool fixture defect (CHANGELOG-ledgered).
- **test_27 wiring: DIFFERENTIAL-GENUINE (WIRING-RED).** Actual home: `test_nuclear_cleanup_bucket5.py::
  TestWS4MissionLens::test_27_preflight_defer_count_via_real_singleton_wiring` (dispatch pointer was stale —
  the unit file has 36 tests, no singleton test). Production-shape wiring (`api.py:976-978` path), manager
  spec'd WITHOUT hand-set resolver → `defer_blocked_count == 2` at HEAD; at base: `KeyError
  'defer_blocked_count'` (surface + wiring absent) — same defect family, one notch earlier. Inverse pin
  `test_27b` (unwired ⇒ 0) present.
- **Force-complete guards: W2 arms EXECUTION-LEVEL, W1 mock-shape.** arm-1 live-task-no-JobItem and arm-2
  live-child: real engine + real `has_live_work` SQL + assert-not-terminated — catch probe-SQL AND
  orchestration defects. W1 TOCTOU re-check: probe mocked (`side_effect=[False, True]`, `call_count == 2`,
  `terminate not awaited`) — orchestration pinned, probe SQL not. GUARD-ABSENT-RED at base (`force_complete_defer_holder`
  missing; f8c97c4b not ancestor of base). **🟠 Finding: W2 arm-3 (quiet→PROCEED) has NO execution-level pin**
  (test_21 mock-shape + test_21e structural only) — follow-up recommended.

## 3. Destructive-surface web automation — DESTRUCTIVE-SURFACE-VERIFIED (4/4 browser-executed)

Playwright chromium headless + live daemon (8079) + ng serve (4199). (1) Double-confirm dialog:
truth-split copy VERBATIM both sentences + survivor note gated on liveIds (real preflight data:
zombie=6, live=1) + stage-2 armed, final destructive confirm NOT clicked; (2) refusal snackbar with the
exact server refusal text (`Refused: … live non-defer work …`), real MatSnackBar via ng.getComponent;
(3) paused-vs-stalled defer notes VERBATIM and zero cross-leak + empty-scenario gates off + mixed-scenario
composes all branches; (4) will-remain list renders real + arbitrary IDs. Screenshots: /tmp/webauto-shots/
(11). Teardown: seed project deleted, own PID trees killed, 8079/4199 freed, 8088 untouched, no foreign
instances touched.

## 4. Census + hygiene

- Constitution drift pack ✅ PASS (24/24, 4.75s, EXPECTED_BRANCH pinned): **WRITERS=23 / CREATORS=1 / MINTS=0**
  — load-bearing here (branch touches job_queue/repository.py, task/repository.py, job_recovery_service.py
  near registered sites); zero drift.
- Scoped cohort (12 branch files): **230/230 PASS, 3 deselects** — gate context "172+1" was stale: +58
  branch growth enumerated; the 1 deselect is now a class-level `@pytest.mark.postgres` (3 nodes, correct
  under no-PG default filter). Zero-DML/SELECT-only pins (TestPurity ×2 + TestBoundedQueryCount ×3) all PASS
  on fresh-engine snapshot diffs.
- ensure.md static: dev.sh `--timeout-graceful-shutdown 10` ✅ (line 102); await-caller sanity ✅ (7 hits all
  docstring/log-strings); dead-code ✅ (zero orphan symbols).

## 5. ensure.md scorecard

Core Critical 4/4 · Core Important 2/2 · Core Nice-to-have 1/1 · Release Gate: full suite via partitions ✅
(quarantine-aware), E2E 3/3 + 1 quarantine-exempt. **All in-scope requirements PASS.** (Concurrency-integrity
items satisfied via full-suite partition coverage per house practice — no NEW failures in that family.)

## 6. Quick fix applied (the only repo change)

`26a7e625` — `test: bump jobs route-count pin 10->12 (WS4 defer-holder force-complete + resend-foreground)`
(5+/3−, tests/unit/test_phase5_jobs_router.py ONLY; production byte-identical). Verified: file 34/34; P6 pack
re-run 1,843P/7F baseline-only. Staged exclusively (no .agents sweep).

## 7. Non-blocking findings / ledger rows

- 🟠 W2 arm-3 PROCEED-path execution-level pin missing (§2).
- 🟠 P12 W7 `test_pause_race_w7_jobitem_skip` mock-migration pending (pre-existing at base; Mock-Migration convention).
- 🟢 perf-matrix WATCH row added (load-flake + vacuous class-scoped selection trap — LESSONS/2026-09-06-perf-matrix-run-unit-trap.md).
- 🟢 QUARANTINE row 11 re-verified at afd7c387 (appended).
- 🟢 P7: branch logger-defect fix deactivates 4/6 of row-16 task_reconciliation nodes at HEAD (positive; row remains for residual).
- 🟢 N3 meta-test portability defect (hardcoded cwd — LESSONS §8).
- 🟢 FE build warnings 4→10 composition drift (jobs.component.scss budget in branch-changed file; non-gating).
- 🟢 Orphaned disposable PG DB `ensemble_blob_prune_3c04971bc4c7` (timeout-killed worker residue; safe manual drop).
- 🟢 Gate-context staleness: "172+1-deselect" scoped figure and "26 tests" defer_blocked_api pointer both stale vs branch reality (230+3; 36) — corrected during gate, no correctness impact.

## 8. Acceptance criterion — the wedge is structurally dead

"Idle-for-candidate in a live-mission system": PROVEN on both engines. At base `afd7c387` the busy
predicate rejects requester-aware queries entirely (PREDICATE-RED); at HEAD the 4-body carve-out admits the
requester's own candidate while every other requester still sees the holder busy — asserted per-node on
SQLite, executed per-cell on real PG (project+system × self/other), mirrored by the watchdog detector layer,
and green inside the 230/230 scoped cohort. The original prod shape (defer-to-completed-instance
self-block) cannot recur through this gate path.
