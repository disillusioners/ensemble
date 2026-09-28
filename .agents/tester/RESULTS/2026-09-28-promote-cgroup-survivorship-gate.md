# PRE-MERGE TESTER GATE — Cgroup-Survivorship Fix: fix/promote-cgroup-survivorship @ 481d1934

Date: 2026-09-28 · Branch `fix/promote-cgroup-survivorship` @ `481d1934` (7 commits on base `84869379` = v0.16.1 tip; local-only) · Commission: cgroup incident r-20260928-005506-f82e; reviewer CLEARED (2-cycle fixbacks); tidier PASS (T1 refactor included).
Changed surfaces (16 files, all in claimed scope — static worker verified): daemon/manager.py, daemon/services/upgrade_journal_sweep.py, daemon/tools/upgrade_journal.py, scripts/upgrade/{lib,promote,restart,rollback,stage}.sh, launcher.sh, tests ×6, incident doc.
Safety: PRE-MERGE REPO ONLY — zero demo/live touches; POSTGRES_* + ENSEMBLE_UPGRADE_LIVE + ENSEMBLE_ROLLBACK_SAFE + SSL_CERT_* scrubbed before every run (ambient live-DB signature present per prior gates).
Workers: base `775bf5f8`, feature `d8a3936f`, static `d17c966b`, flake `7153e617`, boot (see §5).

## VERDICT: (pending §4/§5)

## 1. A/B vs base 84869379 — touched-surface battery (full-dir-per-merge)

Battery (path truth: upgrade suites live under `tests/unit/tools/`, NOT `tests/unit/`; cgroup python suite is a standalone script, not pytest-collectable; cgroup suites are feature-only additions):

| Component | Base | Feature | Adjudication |
|---|---|---|---|
| `tests/unit/tools/` (full) | 3138P / **9F** / 5S | 3139P / **9F** / 5S | **Both-red ×9 — identical sets**: TestAccessMemoryArchive ×5 (quarantined archive-access defect), TestExploreCallerModelOverrides ×2 (stale vs 1a40bc56, deferred), test_watch_job_mission_terminal ×2 (asyncmock/claim-drift, mission-terminal family). Pre-existing. |
| upgrade suites (journal+tools+registration) | 295P / 0F | 296P / 0F | green both legs (+1 test on feature). Journal file alone: 107/107 at feature (§2). |
| `tests/test_release_journal.sh` | **42F** / 246P | **14F** / 274P | **Base-only ×28 — fixed by the branch** (GNU `_iso_to_epoch` arm + suite updates). See BSD-19 note below. |
| `tests/job_queue/` (manager.py touched) | 1905P / **15F** / 38S | 1905P / **15F** / 38S | **Both-red ×15 — the U1-gate-adjudicated pre-existing set** (Site1×3, fake_sync×3, settled-token×3, census×2, tool-drift×2 incl. macOS path, event-loop×1), identical both legs. |
| cgroup suites (shell + python) | *(absent at base — feature-only additions)* | 45/45 + 10 PASS + 1 sandbox-skip | new tests, green. |

**Feature-only (NEW) failures: 0 → no blockers.** Base-only: 28 (release-journal, branch-fixed). Both-red: 38 (9 + 14 + 15 — all pre-existing families).

**BSD-19 expectation — adjudicated with correction:** the "expected 19 BSD-date failures" is an undercount of base reality: **42 failures at base share ONE mechanism** (BSD-only `date -j` syntax on this GNU-coreutils-9.4 host), spanning BOTH the suite's own in-line fixture helpers AND `lib.sh:_iso_to_epoch`. The claimed in-line-fallback insulation holds only on BSD hosts — on GNU the in-line fallbacks are themselves BSD-only (the documented P2.1 GNU debt, pre-existing). Attribution (git blame, all 42): **692b9741 = 17 names** (the expected cluster — attribution CONFIRMED) + e058df2d ×7, 0f3fc913 ×6, 28bde95c ×4, 9be59635 ×3, 35cb9c2e ×3, a89c56cb ×1, +1 dynamic. The branch's GNU arm reduces GNU-host reds 42→14; the residual 14 are the same pre-existing mechanism at other BSD-date sites (cooldown arm, retention/eviction, aged-txn fixtures). Zero feature-only reds in this component.

## 2. Suite verification (independent re-runs, verbatim counts) — ALL HIT
- cgroup shell `tests/test_promote_cgroup_survivorship.sh`: **PASS=45 FAIL=0** (`=== ALL TESTS PASSED ===`) ✅
- cgroup python `tests/unit/tools/test_promote_cgroup_survivorship_python.py` (standalone): **10 PASS** (1a/1b/1c/2/3/4a/5/6a/6b/6c) + **4b sandbox-skip annotation** (SIGTERM not observed via WIFSIGNALED on this host — sandbox quirk) + **6a runs** (caller-supplied env forwarded to 2 subprocess.run calls) ✅
- `tests/unit/tools/test_upgrade_journal.py`: **107 passed in 5.48s** ✅

## 3. Static byte-check + collection — CLEAN
- BSD arms in `lib.sh:_iso_to_epoch` (:171) and `launcher.sh:_js_iso_to_epoch` (:240) **byte-identical** to base (original BSD line verbatim inside the new uname-dispatch case; no `mv -h/-T` or atomic_flip-body hunks; cooldown arm comment-only). GNU arms added at `lib.sh:173-175` + `launcher.sh:242-244` (`date -d "$ts" +%s`), mirroring the atomic_flip precedent (47630be0). Tidier's verification independently confirmed.
- Repo collection @ 481d1934: **23,566 collected (761 deselected), 2 errors — both known pre-existing** (playwright; live-HTTP probe). **Zero NEW.**

## 4. Flake matrix — **15/15 shape-matched, zero flakes**
3 targets × 5 serial runs: cgroup shell `PASS=45 FAIL=0 exit=0` ×5 (34–35s each) · cgroup python `PASS=10 + INFO_4b=1, exit=0` ×5 (1s each) · journal pytest `107 passed` ×5 (5.29–5.51s). No divergent runs; no stray processes or transient systemd units left by the suites (self-cleaning confirmed); HEAD pinned across all 15 runs; scrub verified before every run — **the ambient baseline carried `ENSEMBLE_UPGRADE_LIVE=1` + live-DB POSTGRES_*, proving the expanded scrub load-bearing.**

## 5. dev.sh boot gate — **PASS**
- HEAD 481d1934 verified; scrub envelope (POSTGRES_* + ENSEMBLE_UPGRADE_LIVE=1 + ENSEMBLE_ROLLBACK_SAFE + SSL_CERT_*) verified EMPTY before boot — the ambient baseline literally carried `ENSEMBLE_UPGRADE_LIVE=1` + `ensemble_prod` vars.
- Engine marker: `Creating PostgreSQL engine: localhost:5432/ensemble_dev` ✓ (DEV ONLY held).
- **`UpgradeJournalSweepService started: interval=90s (default 90s), reaper_timeout=660s (default 660s), install_dir=/home/nea/agents-ensemble`** — the changed sweep service boots; `install_dir` resolution in dev demonstrates the detection-env == spawn-env parity fix.
- Health: v0.16.1 healthy; port-up 10s; ~49s stable; no sweep anomalies (90s interval no-op on quiet journal).
- Static: `dev.sh:102` `--timeout-graceful-shutdown 10` ✓. Graceful shutdown: port free in 1s, full cleanup trail, no orphans; live(9797)/demo(7979)/8088 untouched (uptime-verified).
- Carve-out: stale `.env` `POSTGRES_PASSWORD` (testpw vs local `ensemble`) — patched within the byte-exact backup/restore carve-out, restored (sha `11db2814…9490`, diff empty). THIRD gate hitting this env-rot — follow-up stands.
- Anomalies (pre-existing, non-blocking): maintenancer deny-entry config warning; plane MCP session failures (dev-expected).

## 6. Combined verdict

**PASS — `fix/promote-cgroup-survivorship` @ `481d1934` CLEARED FOR MERGE** (next per commission: DEMO E2E under its separate brief → v0.16.2).

- A/B touched-surface battery: **0 feature-only failures** (both-red 38 = pre-existing families across tools-9 / release-journal-14 / job_queue-15; base-only 28 = release-journal reds FIXED by the branch's GNU arm + suite updates; cgroup suites are feature-only additions, green).
- BSD-19 expectation corrected with evidence: base reality is **42 same-mechanism failures** (GNU host breaks the suite's in-line BSD fallbacks too); 692b9741 attribution cluster (17 names) CONFIRMED; branch reduces 42→14; residual 14 = pre-existing BSD-date debt at other sites.
- Suite verification: 45/45 · 10 PASS + 4b sandbox-skip + 6a live · 107/107 — all verbatim counts hit.
- Flake matrix: 15/15 shape-matched, zero flakes; suites self-clean (no strays, no transient units).
- Boot gate: PASS with the changed sweep service's start marker + spawn-env parity evidence.
- Static: BSD arms byte-identical vs base; GNU arms mirror the atomic_flip precedent; collection zero NEW.

No fixes attempted; no repo mutations; zero demo/live touches (pre-merge scope honored).
