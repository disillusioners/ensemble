# TEST GATE — commit `3ad48b9a` supervision_classify nohup-survivor heal (supervisor war r3)

- **Date:** 2026-09-30 19:15–19:45 UTC
- **Gate type:** Independent validation, REPORT-ONLY (no fixes, no commits, no pushes, no version/stage/promote, LIVE install untouched)
- **Branch / commit:** `feature/deploy-ownership-r3-fix` @ `3ad48b9a` (parent/base: `fc285a27`)
- **Dispatcher:** tester (11 workers: 7 pack-run w/ `test-pack-execution`, 3 infra w/o skill, 1 forensic)

## VERDICT: 🟢 **GREEN — commit validated** (all four gate sections pass; one integrity event adjudicated, evidence fully pinned; see §6)

---

## 1. Independent suite re-run (all exact-match vs implementer expectations)

| Suite | Expected | Actual | Runtime | Worker |
|---|---|---|---|---|
| `tests/test_supervision_classify.sh` | 237/0/0 | **PASS=237 FAIL=0 SKIP=0** ✅ | 6s | 2be95cdf |
| `tests/test_supervision_stop_handback.sh` | 133/0/0 | **PASS=133 FAIL=0 SKIP=0** ✅ | 122s | c2501b23 |
| `tests/test_adopt_unit.sh` | 63/0 | **PASS=63 FAIL=0** ✅ | <1min | 4004846b |
| `tests/test_supervision_twins.sh` | 84/0 ×2 envs | **PASS=84 FAIL=0** ambient AND scrubbed ✅ | ~3s each | 57312bb2 |
| `tests/test_supervision_journal.sh` | 39/0/0 | **PASS=39 FAIL=0 SKIP=0** ✅ | 23.3s | 6f02f161 |
| `tests/test_supervision_e2e.sh` | 1P/0F/1S | **PASS=1 FAIL=0 SKIP=1** ✅ (named fence) | 0.05s | e76e9e1c |
| `tests/test_release_journal.sh` | 320P/14F | **320P/14F both legs** ✅ (see §1.1) | ~2min/~3min | 998bd08d |

Notes:
- classify §6 family (nohup-survivor heal) present and green in-suite; §1a–§5 families all green.
- stop_handback prints sections A/A′/B/C/D only — no per-cell B1h runtime labels (audit done at code level, §3).
- twins: no internal mode selector — caller-side env axis; ambient run passed with ambient `ENSEMBLE_RESTART_UNIT` present, proving the suite's internal hermetic `unset` (its B-section documents the desync hazard).
- e2e caveat: fence-handling only — zero e2e legs executed in this executor (`is-system-running` fence). Evidentiary weight = "fence works as designed"; acceptance burden rests on classify §6 + simulation (§2), both green.
- adopt_unit carries its own internal 240s watchdog → dual-layer timeout held there; other suites ran with single-layer outer `timeout 300` (report-only: no suite modification permitted — dispatcher-acknowledged deviation).

### 1.1 release_journal A/B — base attribution RE-DERIVED at rebased base `fc285a27`

- Pack byte-identical between legs: `git diff fc285a27..3ad48b9a -- tests/test_release_journal.sh` = **EMPTY** (only `scripts/upgrade/lib.sh` carries the delta).
- LEG A (branch @3ad48b9a, main checkout, 19:29:00→19:31:07Z): **320P/14F**.
- LEG B (detached worktree @fc285a27, cd-isolated, 19:32→19:34:52Z): **320P/14F**.
- LEG C: **NEW=0, MASKED=0, COMMON=14** — failure sets name-identical (single raw diff = wall-clock-dependent `(got delta N)` suffix, normalized); full stderr **byte-identical** after timing-artifact normalization (`filecmp.cmp` → True). Implementer claim independently re-proven (first attribution was vs `792ff304`; this gate re-derived vs `fc285a27` per instruction).
- The 14 (name-stable family): `8c fresh txn journal byte-identical (untouched)`, `8c fresh txn symlink unchanged`, `8c fresh+dead-owner adopt refuses (rc 78)`, `8d fresh+live-owner adopt refuses (rc 78)`, `8k NO repoint on quarantined-previous halt`, `8k halt event cites quarantine (M4)`, `8k quarantined-previous adopt halts (rc 78)`, `8k txn left in place for diagnosis`, `8l M5 counter-failure WARN still fires (loud, not silent)`, `9 evicts the true-oldest (2020 ISO, not pinned)`, `cap boundary: count=3 in-window reads 3`, `cooldown_until ≈ now+600`, `count=2 in-window reads 2`, `count_rollback after stale window resets then counts → 1`.
- Root cause (both legs): **P2.1 GNU debt** — BSD-only `date -j` (`date: invalid option -- 'j'`), BSD-only `sed -i ''`, quoted-key quarantine-list reads. Zero intersection with the classify delta. Now quarantined as base-attributed ×2 (QUARANTINE.md).
- Worktree removed + pruned; no leak; main HEAD unchanged during the worker's session.

## 2. Original-symptom simulation (acceptance core) — 4/4 cells PASS + BASE A/B ✅

Hermetic driver (502 lines) at `/tmp/r3_sim_setup/driver.sh`, full report `/tmp/r3_sim.9XFa8M/REPORT.txt`; harness seams reused (`INSTALL_DIR`, `SUPERVISION_UNIT_DIR`, `SYSTEMCTL_BIN`, `_supervision_owned_pids`/`_supervision_pid_cgroup_leaf` overrides, PATH-stubbed lsof, journal template); dual-layer timeout (outer `timeout 300`, inner 280s self-abort).

| Cell | Verdict | Key evidence |
|---|---|---|
| CELL-1 original symptom (session-scope cgroup + `.env` pin `ensemble-main.service` + unit file present) | **PASS** | `ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-main.service`; heal line verbatim `adopting UNIT_MANAGED (healing from nohup-survivor state`; NO `SCRIPT_NOHUP` final state; rc 0 |
| CELL-2a guard (healed + armed/active unit + real owned pid outside cgroup) | **PASS** | outer rc **78**; heal fired pre-halt; journal `"event":"halt"` + `supervision DUAL_FIGHT (owned-pids-outside-unit-cgroup)`; systemctl stub recorder: **zero stop dispatches**; `unreachable-post-svs` sentinel never printed (halt pre-mutation). Real `sleep` pid + real `/proc/<pid>/cgroup` read vs stubbed ControlGroup |
| CELL-2b guard (healed + dormant unit + foreign port holder) | **PASS** | rc **1 loud** (`STILL not confirmed stopped`, `failing loud`, `stop: UNIT path`); stop+kill escalation dispatched via stubs; **no script-arm/nohup fallback**; 4 lsof port checks |
| CELL-3 adversarial — stale-lane NON-adoption (gate-authored, beyond implementer's set: `ensemble-live.service` unit file PRESENT + NO `.env` pin + install dir basename `agents-ensemble`) | **PASS** | derivation rung resolves **`ensemble-main.service`**; `ensemble-live.service` NEVER appears in output |

**BASE A/B (proves the fix changes the failure shape):** identical CELL-1 fixture against `fc285a27` lib.sh → `STATE=SCRIPT_NOHUP, UNIT=empty, MODE=script` (no heal line) vs `3ad48b9a` lib.sh → `UNIT_MANAGED:ensemble-main.service`. Behavior change demonstrated at the exact r3 failure shape.

**Provenance (hash-pinned — driver never sourced the contaminated working tree):**
- fix-leg lib.sh sha256 `4af0bdfe71c508cf166d53b70a42496b5e42aac86306214f39cf69526bc83d82` == `git show 3ad48b9a:scripts/upgrade/lib.sh` ✅
- base-leg lib.sh sha256 `de6ba7fec99bdab77f42e0cbd91950204cdcfd81cab6bca7b47fe339ed00bdd5` == `git show fc285a27:scripts/upgrade/lib.sh` ✅
- working tree at run time: `f3d2c883…` (third state, §6) — **not sourced**; `git status --porcelain` initial ≡ final.

## 3. Mock/stub fidelity audit (careful-mock rule) — 14 stubs, 0 critical divergences ✅

- Inventory: 14 load-bearing stubs across the 5 suites (systemctl is-active/show, lsof rc-polarity, cgroup leaf fixtures, uname Darwin, python stub daemon, adopt-unit tripwires, function-override seams). **All OK** vs the parser contract in `lib.sh` (the parser consumes stdout via `$(…) || true`; exit codes deliberately discarded → stub always-rc-0 convention harmless for consumers in scope).
- **B1h1/B1h2 drive the REAL classifier — verified at code level** (quoted evidence): `stop_via_stop_script` invoked unmodified; real `/proc/$$/cgroup` read against stubbed `ControlGroup`; `unreachable-svs-rc` sentinel proves halt-pre-mutation (B1h1 rc 78); B1h2's exit 1 produced by the real `_unit_stopped` poll → WAIT_S exhaustion → real kill escalation → real loud-fail path. No canned classification anywhere.
- MEDIUM (non-blocking) coverage notes: (1) lsof port-FREE polarity not directly exercised in the heal×unit-path cell (mitigated: `LSOF_OK`/`LSOF_NO` pair + `_unit_stopped` cells A1–A6); (2) B1h2 stub's pid output never consumed (same gap family). LOW notes: post-kill `.killed` marker doesn't flip ControlGroup (no current consumer), cgroup-parse seam pinned at source level by twins C2 anchor instead of suite level.

## 4. Commit hygiene ✅

`git show --stat 3ad48b9a`: **exactly 5 files, +505/−4** (all `M`): `scripts/upgrade/lib.sh` (116+), `tests/test_supervision_classify.sh` (247+), `tests/test_supervision_stop_handback.sh` (121+), `tests/test_adopt_unit.sh` (19+), `tests/test_supervision_twins.sh` (6+). Scratch census: 3 modified + 11 untracked (cross-agent `.agents/*` + drill artifacts incl. stray root `current` symlink); **OVERLAP=none** — no scratch in the commit, committed files clean in tree. Metadata: `3ad48b9a Worker Wed Sep 30 18:28:39 2026 +0000 fix(upgrade): supervision_classify unit-file existence fallback — nohup-survivor heal (supervisor war r3)`.

## 6. ⚠️ Integrity event — external mid-gate tree mutation (adjudicated; evidence stands)

- **What:** an external actor (NOT any gate worker — all report-only, verified) staged a **third-state** partial unwind at **19:31:02.142Z** (3 files written in a ~3ms burst, staged in the same op): `lib.sh` + classify + stop_handback, −182/+6 net. Reflog shows a foreign `rebase (pick) → commit (amend) → reset` cycle (intermediate `03ccf9fe`, reset to `3ad48b9a`), a parked `stash@{0}` "WIP on feature/deploy-ownership-r3-fix", and a foreign PID ran `bash tests/test_release_journal.sh` against the checkout at 19:37. The staged content **matches neither `3ad48b9a` nor `fc285a27`** (lib.sh `f3d2c883…` vs `4af0bdfe…`/`de6ba7fe…`); per the sim worker's diff inspection it removes `heal_on` tracking, the POST-HEAL §3 WITHHOLD block, the `ensemble-*.service` pin-conformance check, and test cells 6k/6k′/6k″ — i.e., an in-progress rework/unwind of the r3 fix.
- **Timeline adjudication (forensic worker):** mutation window `[19:30:01Z, 19:31:02.142Z]`; **all 7 suite artifacts completed 19:23:55–19:28:50** (margins 2m11s–7m07s before the window) against the clean census tree → all suite evidence is `3ad48b9a`-pinned. rjournal LEG A (ended 19:31:07Z) overlapped the window by **≤5s at its tail** — disclosed; immaterial: suite file untouched (mtime 07:34), mutated region has zero intersection with release_journal's tested surface, and LEG A output is byte-identical to the immune detached-worktree base leg. Simulation legs sourced hash-pinned blobs (§2), never the working tree.
- **Standing warning for the leader:** HEAD is still `3ad48b9a`, but the **working tree/index is NO LONGER the gated content** (third state staged, un-committed). Anything consuming this checkout after ~19:31Z is NOT looking at what this gate validated. The gate took no action on it (report-only; possibly the implementer's in-flight r4 work).

## 7. Notes & deviations

- **Frontend:** no frontend files touched by the commit or the gate → web automation N/A (stated per instruction).
- **Ambient env (systemic, non-fault):** 5 workers independently flagged inherited `ENSEMBLE_SELF_ENV=live`, `ENSEMBLE_UPGRADE_LIVE=1`, `ENSEMBLE_RESTART_UNIT=ensemble-main.service`, live `POSTGRES_*` incl. real password (env-poison family / PB-F1). All packs stayed hermetic (fixture-owned env, PATH stubs; no `10.44.0.2` contact; live-install mtimes verified untouched). Recurrence logged in LESSONS.
- **ensure.md:** Core #1 (no regressions in changed packs) = the suite list above, all green (scoped: classifier fix, no daemon/architecture change → Release Gate not warranted; no ensure.md contradictions encountered). The 14 release_journal reds are quarantined base-attributed (QUARANTINE.md), so they do not red the gate.
- **Code changes by gate: NONE.** Tester doc writes only (RESULTS/PACKS/QUARANTINE/LESSONS), left uncommitted in the working tree per repo convention and the report-only constraint ("pending" by design).

## 8. Action items (for leader)
- [ ] Branch-state decision: staged third-state unwind on `feature/deploy-ownership-r3-fix` (post-gate) — whoever drives r4 must re-gate; this GREEN applies to `3ad48b9a` only.
- [ ] P2.1 GNU-debt port (3 BSD-only sites) — would turn the quarantined release_journal 14 green on Linux; twice base-attributed now.
- [ ] (optional, nice-to-have) MEDIUM mock-coverage notes: port-free polarity cell in the heal×unit-path composition.
- [ ] (optional) stray untracked root `current` symlink + `data_dev_scratch/` cleanup pass.

## 9. Artifacts
- Suite outputs: `/tmp/supervision_classify.out/.err`, `/tmp/sup_handback_run.out`, `/tmp/supervision_journal.eM3TIM.log`, `/tmp/supervision_e2e.out`, `/tmp/suptwins_ambient_1790796486.log`, `/tmp/suptwins_scrubbed_1790796514.log`
- rjournal A/B: `/tmp/gate-rj-A/` (leg stdout/stderr, normalized lists + byte-identical verdict); base worktree removed+pruned
- Simulation: `/tmp/r3_sim_setup/driver.sh`, `/tmp/r3_sim.9XFa8M/REPORT.txt`
- Forensics: hashes + mtimes reproduced in §6 (worker 16bfeb9e report)

## 10. Worker roster
2be95cdf classify · c2501b23 stophb · 4004846b adopt · 57312bb2 twins · 6f02f161 sjournal · e76e9e1c se2e · 998bd08d rjournal A/B · 4ce79e15 simulation · 23538734 mock audit · 393080c4 hygiene · 16bfeb9e forensics. All 11 reported; zero re-dispatches; zero incomplete nodes.
