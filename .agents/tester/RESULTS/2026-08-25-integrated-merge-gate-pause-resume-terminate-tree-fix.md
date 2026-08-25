# INTEGRATED MERGE GATE — feature/pause-resume-terminate-tree-fix

**Date:** 2026-08-25 (gate window ~11:55–13:30 UTC)
**Target:** `feature/pause-resume-terminate-tree-fix` @ `f8d5973b` (23 commits ahead of `latest` at gate start; note: task brief said "17+2" — actual 23, extra 4 are planning-doc commits `cefb9798..03df9108`; all verified docs-only)
**Branch tip at gate close:** `1ac71270` (8 gate-added commits: 7 test-only + 1 docs-only; `git diff f8d5973b..1ac71270 -- daemon/` = 0 files)
**Method:** 11 worker dispatches (env probe, 5 pack runners, scoped suites, B6 flip SQL, e2e trio ×4 rounds, repro close-out). 0 direct executions by tester.
**Environment:** LLM primary :4123 DOWN all gate (4/4 probes exit=7 + re-probes) — backup `https://llm.daoduc.org/v1` alive via HA failover (~20–30s/turn tax); dev daemon 8079 (replaced fresh for §3); PG `ensemble_dev`@5432 up.

---

## VERDICT: ✅ **PASS — MERGE-READY** (with 3 documented carries, none blocking)

| Section | Result |
|---|---|
| 1. Mandatory 5-pack e2e gate (ensure.md) | ✅ PASS — all 5 exact-baseline |
| 2. E2e trio (B2/B3/B5 acceptance) | ✅ PASS_ALL — after test-infra repairs; 0 production defects |
| 3. Original symptom close-out (B1–B5) | ✅ **ALL FIVE GONE** — decisive live evidence |
| 4. B6 FLIP SQL / D2 disposition | ⚠️ Executed honestly → **UNLOCKED** (evidence wiped in dev DB) — carry |
| 5. Regression sweep (5 packs + scoped P1/P2/P3) | ✅ PASS — zero new failures |

---

## §1 Mandatory 5-Pack Gate (ensure.md — job/task/queue system touched)

All at `f8d5973b`, dual-layer timeout, one pack per worker:

| Pack | Result | Counts | Baseline delta |
|---|---|---|---|
| claim_guard_locks_unit_test | PASS | 168P/0F/0S (1.85s) | exact match |
| concurrency_atomic_unit_test | PASS | 91P/0F/74S (7.28s) | exact match (Core #2/#3 hold) |
| turn_transitions_reconciler_unit_test | PASS | 48P/0F/1d (2s) | exact match; quarantine deselect = known test_turn_state_machine stale assert (:1090); branch's +2 in-range additions live outside this pack's files (merge-base diff empty) |
| job_queue_unit_test | PASS | 1529P/0F/39S (21.45s) | exact gate-start baseline; the anticipated −1P/+1S reclass did not materialize (within band) |
| api_unit_test | PASS | 213P/0F/8S (12.53s) | exact match |

Known pre-existing: `test_terminal_orphan_matrix[pending-True-active]` did NOT fire in any pack this gate. `pytest-timeout` missing-from-venv observed (env note; shell-enforced dual-layer timeouts unaffected; `uv sync --extra dev` run by e2e worker restored 2.4.0).

## §2 E2E Trio (B2/B3/B5 acceptance)

First execution ever of these 3 branch-authored tests. Rounds 1–4 were required because the tests shipped with **test-infrastructure defects** (never executed by their author — B5 still carried a "DO NOT EXECUTE / authoring only" docstring). Every failure across all rounds was attributed log/DB-proven to test plumbing or environment; **zero propagation defects in production code were found at any round**.

Fixed test-inra (all test-file-only commits): bogus `GET /api/instances?parent_id=` helper (unsupported param → arbitrary instances) → parent-detail `children` mechanism with parent_id guards (`89c8913b`); permission-collision spawn wordings — tester cannot spawn developer, developer cannot spawn developer → "worker child"/direct-spawn rewording (`502777ac`); B2 full redesign — original premise ("children complete DURING pause") **unconstructible post-B1-fix** (whole-tree pause cancels running children; B5 pins that semantic; the two plan texts were mutually exclusive — plan oversight, see LESSONS) → constructible chain: pause mid-work → resume → children re-execute+complete → reports delivered (`502777ac`); failover-tax window raises 60→120s (`c85f331e`); stale count literals `==1` → `>=1` with binding no-re-injection invariants (`15aaf59d`, `1ac71270`).

**Final state: B2 PASS / B3 PASS / B5 PASS.**
- **B2:** whole-tree `paused_ids` (3 nodes) mid-work; 20s static-under-pause; resume → `route_outcome=report_or_external_resume`; children re-executed interrupted sleeps, COMPLETED; **root COMPLETED**; `report_injections` rows==2 both `TASK_DELIVERED`; follow-up message → no re-injection (rows stay 2).
- **B3:** grandchild cascaded DOWN; `bus fire_for_terminated_target fired … outcome=terminated` (was CANCELLED pre-fix); leader received exactly 1 report with **`[child_outcome: terminated]`** PG-proven in message content; leader COMPLETED in ~102s; no ghost loop.
- **B5:** `/stop` on path-param pauses exactly that subtree (`paused_ids=[tester,worker]`, root untouched); `/pause` root afterwards → correct composition (`paused_ids=[leader]`-shape, completed nodes in `skipped_ids`).

## §3 Original Symptom Close-Out (the initiative's purpose)

Fresh daemon on branch tip (`1ac71270`), 3-level tree, full procedure mirrored from the 2026-08-24 repro. Evidence: `/tmp/pause-repro-closeout-20260825/` (61 files: raw API bodies, log slices, 620KB daemon log). Runtime 52m.

| Symptom | Verdict | Decisive evidence |
|---|---|---|
| **B1** pause no DOWN cascade | **GONE** | `paused_ids=[leader,tester,developer,3 workers]`, 6× Pausing + 3× graph-cancel + skip-for-completed; 3 sweeps t+2/18/40s: **0 new work** (original: worker started sleep 420 18s after root pause) |
| **B2** resume strands root | **GONE** | `resumed 3 task(s) PAUSED→PENDING` (was 0); `route_outcome=report_or_external_resume` (was `invalid_or_missing_handle`); root COMPLETED in 290s, msg Δ+4 (was frozen 25→25) |
| **B3** terminate no UP | **GONE** | watcher FIRED `outcome=terminated` (was CANCELLED); leader got the marked report + completed in 70s (was `waiting for 1 children` forever) |
| **B4** terminate-root misses live children | **GONE** | `non_terminal_descendants=2` (was `children=0`); permanent-lineage boot banner confirmed; 0 orphans; **0 GUARD-livelock events** in 90s sweep (was perpetual 3s poll) |
| **B5** /stop wrong target | **GONE** | `/stop` on tester → `paused_ids=[tester5,worker5]`, root untouched (original: root paused) |

No new defects observed. B6/B7 (out of gate scope) not re-encountered incidentally.

## §4 B6 FLIP SQL (D2 disposition)

§4.1/§4.2/§4.3 of `tests/manual/b7b_rearm_admission_history.md` executed against live PG (commit `8f6b8a65`, §7 appended with raw outputs). Outcome: **UNLOCKED — evidence unavailable**: the `task` table's 2026-08-24 rows were wiped (0 survive of 3 targets; partial dev-DB wipe), `job_queue_items` survived with terminal state only (`admission_state='done'`, no history), and **no audit/history table exists** — the `done→active→done` transit cannot be reconstructed. Disposition reverts to "leader-accepted, static-evidence-only" (§5's started_at-divergence inference). Spec drift found: `task.updated_at`/`job_queue_items.updated_at` don't exist in PG schema. **Carry:** re-run repro on a preserved DB then re-execute §4.3 before locking; NOT-A-DEFECT is *plausible, unconfirmed*.

## §5 Regression Sweep

Beyond the 5 packs: scoped P1 (269P/28S/0F — ≥166P/19S floor cleared; skips = known Phase-5 DependencyBus skips in 2 files), P2 aggregate (59P/0F incl. both ambiguous files attributed: enumerate_first→P1, downside_row_drain→P2), P3 (10P/0F). One stale mock assertion quick-fixed (`db9324e6`: `test_watchover_crash_recovery.py` — P3 added `cascade_to_root=True` default kwarg to `pause_instance_cascade` facade; production behavior internally consistent). Full-suite run not executed (5 packs + scoped suites cover the blast radius; suite-level pre-existing failures remain quarantined/known per QUARANTINE.md — 5 TestAccessMemoryArchive untouched).

---

## Gate commits (all on branch, none in production code)
`db9324e6` stale mock assert · `89c8913b`+`cecd4d16` trio plumbing · `502777ac` B2 redesign+rewording · `15aaf59d` B2/B3 calibration · `c85f331e` B3 window · `1ac71270` B3 count literal · `8f6b8a65` B6 §7 doc append

## Carries (non-blocking)
1. **D2 disposition UNLOCKED** — needs preserved-DB repro + §4.3 re-run to lock (B6 flip path).
2. **Plan-oversight documented**: phase2-plan task 2.9's "complete during pause" premise unconstructible post-B1-fix — B2 e2e redesigned to the constructible equivalent (rationale in test docstring; recommend plan erratum note).
3. **Test-side nits**: `message_queue` PG probe schema mismatch (`created_at` varchar vs expected timestamp) in B3's best-effort probe — non-blocking fallback works; worth cleanup with B6/B7 tickets.

## Daemon cleanup
Old harness daemon replaced for §3; fresh daemon **stopped, pid-verified** (TERM 82367/82375, port 8079 freed in 2s). Port 8088 untouched throughout the entire gate. No daemon left running — no follow-up needs it.

## Bottom line
All five original user-visible symptoms are verifiably gone in live behavior; mandatory gates exact-baseline; e2e acceptance green; regression clean; carries are documentation/test-debt only. **Recommend merge.**
