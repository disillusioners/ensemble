# Pre-Merge Verification: P2.1 Release & Upgrade Pipeline

- **Date:** 2026-08-23 · **Tester:** tester agent (Test Leader) — 9 worker dispatches, 0 direct executions
- **Branch:** `feature/self-restart-p2p1-release-pipeline` @ `e058df2d7917240f2e2596e4a72c567410da1910` (base `9e525c1b` == `latest` tip, ancestor-confirmed)
- **Verdict: ✅ PASS — MERGE-READY** (all mandated packs executed at exact tip SHA; e2e gate ruled NOT triggered; regression slice exact-baseline; evidence file consistent; live isolation proven static + dynamic)

## 1. Mandated packs (reviewer's standing merge condition — EXECUTED, not counted)

| Pack | Expected | Actual | Result | Runtime | Evidence |
|---|---|---|---|---|---|
| `tests/test_launcher.sh` | 190/190 | **190 passed, 0 failed** ("ALL PASS") | ✅ PASS | 4s | `/tmp/p21-verify-launcher.log` |
| `tests/test_release_journal.sh` | 243/243 | **243 passed, 0 failed** | ✅ PASS | 55s | `/tmp/p21-verify-journal.log` |
| `test/drills/p21_upgrade_pipeline_drill.sh` | 96/96 | **96 passed, 0 failed** (`═══ DRILLS COMPLETE: 96 passed, 0 failed ═══`) | ✅ PASS | 901s | `/tmp/p21-drills/verify-full/transcript.txt` + `/tmp/p21-drills/verify-full.log` |

All three verified at `HEAD == e058df2d…` on the feature branch, preconditions (branch/porcelain) checked before AND after each run. Zero failures anywhere. Post-run hygiene on every run: `git status` unchanged (only the pre-existing ` M .agents/approver/active.md`), tag-set invariant (63→63, drill transient tags self-erased), no process leaks, fixture/drill ports freed.

### Drill-suite timeout note (documented deviation)
First drill attempt under the standard 300s dual-layer guard → **TIMEOUT at Phase D (37 PASS / 0 FAIL at kill)**. Root cause: dispatcher's runtime estimate was wrong — the suite's ~15-min pacing is structural (sequential real stop→flip→start→gate→soak cycles across 4 sandboxes; fast knobs already set; no accelerable config/env; read-only mandate forbids splitting). Committed run12 evidence measured 14m40s. Resolution: ONE authorized full-length re-run with raised, still-dual-layer guards (inner `timeout 1180` under outer 1200s monitor) → 901s PASS, neither guard engaged (279s headroom). **This is a deliberate single-use deviation from the 5-min pack cap, reported here per rule** — the drill suite is not a registered pack; bounded + interruptible was preserved throughout. See follow-up F2.

## 2. Change-set verification (independent) + e2e-gate ruling

`git diff --name-status 9e525c1b..e058df2d`: **16 files, +5633/−39, 28 commits** — `scripts/upgrade/` ×5 (lib/stage/promote/rollback/status), `launcher.sh`, `Makefile`, `tests/` ×2, `test/` ×4 (3 drills + release_journal pack), `.agents/` ×3 (2 docs + committed demo evidence). **ZERO `daemon/` paths; zero `tests/unit|e2e` paths** — the "zero daemon/ changes" claim is independently CONFIRMED.

**e2e-gate ruling: NOT TRIGGERED.** ensure.md mandates full e2e only when changes touch the job/task/queue system (claim_pending_task, turn_transitions, reconcile_turn_mirror, job_processor, job_locks). No changed path reaches any of those surfaces (scripts/launcher/Makefile/tests only). Precedent-consistent: AR-Phase-1's triggered ruling rested on `daemon/api.py` lifespan changes — nothing comparable here. Release Gate not run.

## 3. Regression slice (blast-radius justified)

| Pack | Baseline | Actual | Result | Justification |
|---|---|---|---|---|
| `deploy_pipeline_unit_test` | 53/53 | **53/53** | ✅ PASS (~2s) | deploy.sh = bootstrap sibling of scripts/upgrade/ (shared topology/guard conventions); Makefile modified on branch |
| `stop_ownership_unit_test` | 43/43 | **43/43** | ✅ PASS (19s) | promote/rollback consume stop-ensemble.sh semantics (D6) |
| `watchdog_watcher_unit_test` | 27/27 | **27/27** | ✅ PASS (5.1s) | launcher.sh heavily modified (+660/−12); watcher observes launcher-supervised state |
| `boot_probes_unit_test` | — | **skipped** | — | daemon-only surface; zero daemon/ changes — out of blast radius |

All three executed packs exact-baseline → zero collateral damage.

## 4. Evidence-file verdict: `.agents/tester/RESULTS/2026-08-22-p2-1-demo-e2e-release-pipeline.md`

**CONSISTENT and tester-verifiable without rerunning.** Checks: internal timestamps coherent; verbatim transcripts (promote/rollback/refusals with exit codes 0/78/1) match script/pack-verified behavior; journal JSON state (counter 2/3, cooldown_until, quarantined[], history events) matches D4 schema and pack-proven math; 6-checkpoint live-pid table byte-identical (`31130`+`31150`, listener 31150) with pids still alive/unchanged today (corroborated by drill runs' live checkpoints `30054 31150` — note live pid set differs from demo-evidence's capture; both invariance claims held within their own windows); deviations honestly disclosed (harness kill, vC halt sequencing, ENSEMBLE_ROLLBACK_SAFE author override). Demo optional re-validation: covered via the tip-SHA drill execution (§1) — sandbox stage→promote→gate→rollback→sweep, all acceptance rows live-proven. Caveat stated plainly: demo evidence documents tip `50a85ab9`; W1–W3 deltas since then are exactly the surfaces re-proven at `e058df2d` by launcher 190/190 + journal 243/243 + drills 96/96. A real-binary demo cycle at final tip remains an optional post-merge operational validation, not a merge blocker.

### Run12 staleness forensics (closed)
Run12 (96/96) ran 04:33:20Z→~04:48Z on HEAD `9be59635` + the uncommitted W1–W3 working-tree delta; zero commits landed mid-run; sole later commit is the tip itself (04:50:33Z) packaging that code + evidence. SHA-identity was unverifiable read-only → discharged by my own full-length run at exact tip SHA (901s, 96/0).

## 5. Live-isolation verification: **PASS** (by construction + dynamically)

**Static (all changed paths):** `require_live_guard` (exit 78) wired in ALL FOUR scripts incl. observation-only `status.sh`; `TARGET=live` unreachable without `ENSEMBLE_UPGRADE_LIVE=1` (unit-asserted + drill Phase 0); sandbox REQUIRES explicit INSTALL_DIR+PORT (exit 78 otherwise), refuses canonical live/demo dirs incl. symlink aliases, refuses live/demo/dev port collisions (unreadable live `.env` → fail-closed 78), substitutes ambient `ensemble_prod|demo|dev` DB names with `ensemble_sandbox`; demo values hardcoded (ambient-env immune); default target = demo; zero `9797` literals in `scripts/upgrade/`; all Makefile live-dir hits pre-existing/untouched; `tests/test_release_journal.sh` runs entirely under `HOME=$(mktemp)` fixture. **Dynamic:** both independent drill runs self-asserted live pid-set invariance (baseline == end: `30054 31150`) and left :9797 listener (pid 31150) untouched; no worker ever contacted live; no `ENSEMBLE_*_LIVE` set anywhere.

## 6. ensure.md status (blast-radius scoped)

- Critical "No regressions in changed packs": **PASS** (all scoped packs green).
- Deadlock/concurrency + sync-DB items: N/A — zero daemon/ changes (out of blast radius).
- `dev.sh` graceful-shutdown static check: N/A — dev.sh untouched by branch.
- Release Gate: not run — e2e ruling NOT TRIGGERED (§2).
- No contradictions between ensure.md methods and this run; no Improvement Notices.

## 7. Follow-ups (non-blocking, post-merge)

- **F1 🟢** PACKS.md row drift: `release_journal_unit_test` row still says "121/121 … 95/95 drills" — update to 243 / drills 96 (also add drills row, see F2). Deferred to post-merge because this verification ran under a no-commit mandate.
- **F2 🟠** Register the drill suite in PACKS.md with an explicit documented cap (~1200s wrapper, per MOCK_TESTS-style long-timeout convention) or split by phases (0,A,B,C,D,E,F,G,H — only Phase F ~290s nears 300s alone). Today it has NO pack row and NO internal timer; every future invocation must hand-roll dual-layer guards.
- **F3 🟢** Demo operational note: stuck `promote.sh` pid 69870 (+child 69871) on `~/agents-ensemble-demo`, ~6.5h old at inspection — traced to the documented 22:33:46Z harness-kill incident in the demo evidence; demo-side, known-benign; owner decision to clear.
- **F4 🟢** Optional: one real-binary demo promote cycle at the final tip post-merge (operational confidence; not a gate).
- **F5 🟢** Informational: 4 stale `launcher-*` TMPDIR dirs (Aug 16–20) predate this verification; `/tmp/journal-timing.log` likewise pre-existing. Not from these runs.

## 8. Instance ledger

| Worker | Instance ID | Task | Outcome |
|---|---|---|---|
| p21-gitverify-scan | 0b257c7c | change-set + inventory + static isolation | ✅ |
| p21-pack-launcher | 2f2a3e8a | launcher pack | ✅ 190/190 |
| p21-pack-journal | dc5486e3 | journal pack | ✅ 243/243 |
| p21-pack-drills | 246fa346 | drills @300s guard | ⏱ TIMEOUT (37/0 at kill) → superseded |
| p21-reg-deploy | 8a209a57 | deploy pack | ✅ 53/53 |
| p21-reg-stop | 848a92a6 | stop pack | ✅ 43/43 |
| p21-reg-watchdog | 14c626e4 | watchdog pack | ✅ 27/27 |
| p21-run12-timing | cabb4162 | run12 staleness forensics | ✅ facts delivered |
| p21-drills-fullrun | b1931c9c | drills full-length | ✅ 96/96 @ tip |

**Overall: PASS — cleared to merge `feature/self-restart-p2p1-release-pipeline` → `latest`.**
