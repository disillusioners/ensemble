# Test Gate — maintenance-tabs-ui wire-up + checkpoint cleanup VACUUM FULL option

**Commission**: maintenance page mat-tab-group shell + interactive VACUUM FULL escalation after destructive prune (Parts 1-3).
**Lane**: tester (authoritative verification of record). Worktree `/home/nea/ensemble-src-wt-maint-tabs-vacuum`, branch `feature/maintenance-tabs-vacuum`, HEAD under test `fe5efcd58a38b9c5a01c12976d1aa391e0d06b12` (19 commits from base c8a3fbf6a). Post-gate HEAD `c35d54a46a54be61e8d3c461d3e5c51edb9a8041` (sole addition: w2 baseline PNG refresh, this lane).
**Workers**: b225a07f (be6file) · 976d394b (fejest) · 3f1e2c17 (mockaudit) · 2a44a2be (e2e, ×2 turns) · be4388ed (beneighbor) · 3117cc70 (probes). 6 workers, 0 re-dispatches, 0 quick fixes (test-only lane; sole commit = sanctioned baseline refresh).
**VERDICT: PASS-WITH-FLAGS** — every commissioned check green; flags are adjudications + test-debt, none blocking. See §Flags.

## Results matrix

| Suite (node) | Expected | Actual | Duration | Verdict |
|---|---|---|---|---|
| BE 6-file commissioned pack (A1) | ~122 (ledger est. 119) | **122 passed / 0 failed** — wiring_pin 14, no_full 3, run_vacuum_full 9, execute_request_schema 4, constants_policy 2, service **90** | 74s | ✅ PASS |
| BE neighborhood `tests/unit/services` (A2) | same-9 pre-existing, no new | **2638 P / 9 F** — all 9 = documented pre-existing families (see §Pre-existing) | 245s | ✅ PASS-with-preexisting |
| ensure.md Core: concurrency pack (A2) | PASS | **99 P / 0 F / 74 skips** (in-code) | 69s | ✅ PASS |
| ensure.md Core: dev.sh static (A2) | found | `--timeout-graceful-shutdown 10` @ dev.sh:142 (comment :139) | <1s | ✅ PASS |
| FE Jest 4 maintenance suites (D) | ~230 | **230/230, 4/4 suites** | 7s | ✅ PASS |
| FE tsc --noEmit (D) | clean | **exit 0, zero diagnostics** | 6s | ✅ PASS |
| Mock-quality audit (B) | real-behavior mandate | **largely REAL-BEHAVIOR; 0 tautologies**; important drift-risk gaps (see §Mock audit) | ~15 min read | ✅ PASS (findings) |
| E2e w6 checkbox/payload/dialog (E) | PASS | **PASS** — default ticked, §D-7.6 helper verbatim, dialog R-A+R-C both states, execute body captured | 1.1m run | ✅ PASS |
| E2e w6.5 600px AC-8 (E) | PASS | **PASS** (disambiguated: prior "1 skipped" was the w7 §D-9.6 fixme sub-test, NOT w6.5) | 47s | ✅ PASS |
| E2e w7 tabs/lazy-mount/a11y (E) | PASS | **PASS** — 2 tabs, Tab 1 active, Tab 2 aria-disabled, single mount, keyboard reach | (same run) | ✅ PASS |
| E2e w2 screenshot budget (E) | drift decision | drift 3.54% total / 3.86% body < 5% STOP; cause = commissioned chrome + reflow + version bump → **baseline refreshed + verified green** (§Baseline) | 44s+47s | ✅ PASS (adjudicated) |
| Adversarial probes C1-C4 (C) | 4/4 | **4/4 PASS on real PG 16 + live daemon** (§Probes) | ~18 min | ✅ PASS |

Original-symptom closure (F): tabs render per approved design (w7) ✓ · wizard stepper unregressed — w6 walks Status Strip→dry-run→execute→dialog→cancel→reopen (wiznav envelope intact) ✓ · VACUUM FULL checkbox default-ticked + round-trips to API (w6 route-interceptor body + C3 real-PG escalation) ✓.

## Probes (C1-C4 — real disposable PG :15433 + daemon :8101, log_statement=all + log_min_duration_statement=0)

- **C1 scheduled-lane poison**: `VACUUM_FULL_FORCE=1` env + config claiming vacuum_full → `CheckpointCleanupJob.execute()` completed (audit `succeeded`, env_flags `{destructive:true}`), engine factory never invoked, **0** FULL in 7,009-line window. Guard holds black-box.
- **C2 API without flag**: 202/succeeded; `env_flags_json={"destructive":true,"vacuum_full":false}`; 15 real DELETEs (blobs 7/ckpts 4/writes 4; counts 6→3, 8→6, 6→3); 0 FULL.
- **C3 API with vacuum_full=true**: exactly 3 FULL statements in §D-7.5 order (`VACUUM FULL checkpoints` log-win:810 → `checkpoint_writes` :812 → `checkpoint_blobs` :832), **strictly post-prune** (last prune DELETE win:757 < first FULL win:810, dedicated NullPool engine session between); summary `vacuum_full{escalated:true, vacuumed_tables:[3], refusal:null}`.
- **C4 dry-run with flag**: route ignores body flag; `run_checkpoint_prunes(destructive=False, vacuum_full=True)` → `vacuum_full is None`, recorder never tripped; counts identical 6|7|9 → 6|7|9; **0 FULL and 0 DELETE** in 20,187-line window — structural ignore proven.

Evidence: `/tmp/ens-maintvac/probes/` (22 files incl. REPORT-c1-c4-adversarial-guard-probes.md), `/tmp/ens-maintvac/logs/pg-probe.log`.

## w2 baseline decision (chrome-ripple, adjudicated)

Drift 3.54% (27,841/786,432 px), bbox full-page; body region (y≥64) 3.858% — **below the 5% wizard-body STOP**; chrome strip itself 0.046%. Visual cause (top-200 crops): expected = no tab bar + header `v0.16.9`; actual = `mat-tab-bar` (Checkpoint active / Database disabled) + divider + header `v0.20.0` — both intentional (commissioned chrome; version bump from merges since baseline d8ee196e2). Protocol 3a (strictly chrome-confined) NOT met, 3b (>5% body) NOT met → dispatcher ruled refresh authorized under **"chrome-ripple (reflow)"**: body pixels are layout reflow of the chrome addition (PNG shrank −3,723 B; page got shorter), all 3 w2 runtime gates (no-overflow / wizard-only ≤760 / absolute ≤808) green pre- and post-refresh.
- OLD sha `ec268fc29cf2a289462f3d45b40f10bdf70bde17cdd2b9d5df835112fd1cb1eb` → NEW sha `455e016850c009e55104d6b603ddf8a52a66b84c9417284438ea1e3c6add81f1`
- Commit `c35d54a46a54be61e8d3c461d3e5c51edb9a8041` (single file: the baseline PNG; nothing else staged). Post-refresh verify PASS (exit 0, drift-free).

## Pre-existing 9 (neighborhood) — all documented, zero new

×7 `test_job_queue_proxy_phase1` derived-status (= 2026-09-14 consolidated QUARANTINE row, A/B-proven base-attributed) · ×1 `test_scheduling_service::TestUserTimezoneRung::test_user_setting_rung_covers_once_anchor_site` (= 2026-10-07 upgrade-resilience residuals TIME-BOMB family, A/B-proven at base 2753ee78d) · ×1 `test_service_tool_manager::test_stop_pid_already_dead_returns_pid_dead` (= same row, ZOMBIE-RACE family). Worker grep-verified: no maintenance/vacuum/tab literal in any of the 9 failure messages. Dev-lane "same 9" claim consistent; expected-9 list itself not committed anywhere (flag below).

## Mock audit (B) — careful-mock mandate

REAL-BEHAVIOR: engine factory is the only stub; orchestration, headroom math, SQL emission, ordering all real; F1(iv) recorder hooked at `conn.execute()` on actual SQL text, case-insensitive; headroom mirrors `f_bavail × f_frsize` (maintenance.py:1970) with GB-scale numbers; table set exact list-equality; ENOSPC discriminator `retriable:true` pinned both sides. 0 tautologies (every mock is recorder/boundary).
GAPS (test-debt, not theater): 🟠 timeout constants 3600000/300000/120000 + `_VACUUM_FULL_TABLES` tuple duplicated as literals (test_run_vacuum_full_after_cleanup.py:51,210-212; test_maintenance_checkpoint_cleanup_service.py:655-677) instead of imported — silent-pass drift risk; 🟢 exact `free==required` boundary unpinned (`>=` at maintenance.py:1993 → equality proceeds); 🟠/🟢 F1(iv) matcher whitespace-fragile (`VACUUM␣␣FULL` would slip; production hard-codes single space); 🟢 stubbed-engine tests don't interleave prune SQL before FULL SQL in one stream (order pinned instead by spy-order test :317-371 + C3 real-PG proof).

## ensure.md scoping

Core validated scoped to change set: changed packs PASS (A1/D/E), concurrency_atomic PASS, dev.sh static FOUND. Release Gate NOT warranted (no boot/architecture/release change; e2e disposable-daemon boot + canary provides stronger boot evidence than the dev.sh probe). No contradictions found. No new QUARANTINE rows needed (all 9 already documented).

## Flags (non-blocking)

1. **Baseline refresh adjudication** — authorized under "chrome-ripple" extension of the chrome-only protocol; full evidence in §w2. Leader should ratify the classification (commit already on branch).
2. 🟠 **Test-debt: constant literal duplication** (timeouts + tables tuple) — recommend follow-up commission to import from `daemon.constants` / `maintenance._VACUUM_FULL_TABLES` and add the exact-boundary headroom test. audit cites: test_run_vacuum_full_after_cleanup.py:51,210-212.
3. 🟢 Dev lane's expected-9 list was never committed (reconciliation fell back to documented QUARANTINE families — sufficient here); future lanes should commit the list.
4. 🟢 Leader's per-file estimate stale (service 87 vs actual 90; total 122 exact).
5. 🟢 Out-of-scope grep over-match: `-g "w2"` case-insensitively matched `[W2]` in workspace-file-tabs (ERR_CONNECTION_REFUSED on :4199) — unrelated, not run further.

## Evidence index

`/tmp/ens-maintvac/`: be6file.log · beneighbor.log · concurrency.log · fejest.log · fetsc.log · e2e-focused.log(+.clean) · e2e-w65-disambig.log · e2e-w2-refresh.log · e2e-w2-verify.log · analyze_w2_diff.py · probes/ (22 files) · logs/pg-probe.log. Pre-refresh diff PNGs preserved under frontend/test-results (gitignored). All ports verified freed (15432/8099/4299/15433/8101); 8088/8079 never touched; worktree clean except pre-existing scratch + planning artifacts.
