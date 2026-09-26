# v0.15.3 Upgrade Tool-Lane Fix — INDEPENDENT ACCEPTANCE GATE

**Date:** 2026-09-26 (19:45Z–20:15Z window)
**Commission:** Independent final gate before release-cut (merge→bump→tag→stage; promote stays user-manual)
**Branch/HEAD:** `feature/upgrade-tool-lane-fix` @ `a95b702837c946c990912f341fa41c6bf0b8163f` (pre-flight-verified by every worker)
**Base:** `139ba352` (= v0.15.2; tag v0.15.2=ccd199ab)
**Mission footprint (verified via `git diff --stat 139ba352..a95b7028 -- daemon/`):** exactly 6 files, +938/−12 — `api.py +84`, `config.py +36`, `manager.py +108`, `services/upgrade_journal_sweep.py +481 (NEW)`, `tools/upgrade_journal.py +80`, `tools/upgrade_tools.py +161/−12` — plus `scripts/upgrade/stage.sh` (rider) and tests/pack extensions. NO other daemon/ files touched (verified twice, independently).
**Workers:** f29e00ee (1a), 74b1f5de (1b), 48b25799 (1c), c8369a40 (2a+attr), 679effea (2b+attr), 90b6ffdf (3), 99c89726 (4). 7 workers, 0 direct executions by tester.

---

## FINAL VERDICT: ✅ **PASS — CLEARED FOR RELEASE-CUT**

Zero mission-caused failures across the entire independent battery + breadth regression. All five original-defect arms verified **DEAD** on named evidence. Tool-lane e2e on dev: all phases PASS. Mock-quality audit: all greens EARNED (no vacuous passes). Every red observed anywhere is base-evidenced pre-existing (A/B-proven at `139ba352` where needed).

---

## Task 1 — Independent Battery (fresh reproduction, prior claims not trusted)

### 1a. pytest battery — `test_upgrade_journal.py` + `test_upgrade_tools.py` + `test_upgrade_registration.py`
**RESULT: PASS — 295/295 passed in 21.75s** (exit 0). Exact fresh reproduction of prior claim (295/0). Log: `/tmp/v0153-acc-1a.log`.

### 1b. Pack — `test/packs/upgrade_tool_interlock_unit_test.sh` (registered in PACKS.md)
**RESULT: PASS — 274 passed in 13.29s** (pack `RESULT: PASS`, exit 0). Matches prior claim (274). Extensions (verified-arm tests, arm-preflight refusal tokens, child-exit watcher tests) all green. Log: `/tmp/v0153-acc-1b.log`.

### 1c. `tests/test_release_journal.sh` at HEAD + GNU-debt A/B at base
**RESULT: ATTRIBUTION-CONFIRMED — zero mission-caused failures.**

| Run | Passed | Failed | Total | Note |
|---|---|---|---|---|
| HEAD 1st run | 245 | 43 | 288 | +1 = B2b flake (see below) |
| **HEAD definitive rerun** | **246** | **42** | 288 | **exact match to prior claim** |
| BASE `139ba352` | 226 | 42 | 268 | no section 14 (predates v0.15.3) |

- **Failure-set diff HEAD-rerun vs BASE: EMPTY both directions** (`diff` of sorted fail labels = nothing). The +20 pass delta = NEW section 14 (rider tests 9a–9g), **20/20 GREEN** at HEAD.
- **Mechanism attribution (all 42):** `lib.sh:84-89` `_iso_to_epoch` BSD `date -ju -f` → 41 failures (`date: invalid option -- 'j'`; sections: cap/cooldown/quarantine ×4, adopt_stale_txn 8a–8n ×28, B4 sweep 11a–11e ×6, 12c/12e ×3); `lib.sh:1238-1295` retention eviction → 1. The `:706-712` cooldown-arm site is the mechanism-subset of the same `date -j/-v` debt inside the 41. **Zero failures outside the 3 documented GNU-debt sites.** (GNU debt remains OPEN backlog — standing P2.1 critical note; failures are host-arch, not code-health.)
- **B2b flake (first HEAD run only):** `B2b _probe keeps the lock heartbeat fresh` — proven flake: `lib.sh` sha256 `d68403c9…f58b4c` **byte-identical** HEAD↔BASE, B2b test bytes identical, only-suite-delta = new section 14 (lines 1360-1517), vanished on identical rerun. Mechanism: `_probe` (lib.sh:1074-1087) curl-latency under host load vs `sleep 2`/`LOCK_STALE_S=3` budget. Pre-existing host-load sensitivity; noted for the flake ledger.
- Worktree `/tmp/ab-base-139ba352` added (detached, sanctioned) and **removed cleanly** (verified gone; main checkout unchanged at a95b7028).
- Logs: `/tmp/v0153-acc-1c-{head,base,head-rerun}.log` + fail-list txts.
- Adjacent (non-counting, both runs, pre-existing): `tests/test_release_journal.sh:203` references undefined helper `assert_not_contains` — script stub-defect, flag for the shell-suite owner.

## Task 2 — Breadth Regression

### 2a. `tests/unit/tools/` whole dir — **RESULT: PASS — 3098 passed / 7 failed / 5 skipped in 221.97s**
All 7 reds classified pre-existing:
- `TestAccessMemoryArchive` ×5 (`test_archive_lifecycle.py`) — standing QUARANTINE family (access_memory denies all archive access; triple-attributed pre-branch). Expected.
- `test_watch_job_mission_terminal.py` ×2 (`TestWatchJobMissionTerminalToolGate::test_mission_terminal_fires_when_both_terminal` — `'>' not supported between 'AsyncMock' and 'int'`; `TestNotifyWatchersMissionTerminalGate::test_mission_terminal_watcher_held_when_mission_not_terminal` — claims made while mission NOT terminal): initially flagged NEW; **A/B adjudicated PRE-EXISTING-AT-BASE** — byte-identical signatures at `139ba352` (base leg: 2F/12P), deterministic at HEAD (solo ×2 = 2F/12P both), mission diff EMPTY on the test file AND all 3 production files (`daemon/tools/{missions,job_queue,instance}.py`); last-touch commits `3f9fca81`/`874d6b7c`/`36b63be1` all pre-mission (mission-terminal lineage landed at latest@39607e12, ancestor of base). → Mission-terminal lineage debt, separate commission; quarantined (new family row).
- Upgrade trio green in this dir run (battery corroborated).
- Logs: `/tmp/v0153-acc-2a.log` + `-watch-solo{1,2}.log` + `-watch-base.log`.

### 2b. API/config surface — **RESULT: PASS — 45/47, both reds pre-existing-at-base**
- **Path correction (disclosed):** commission specified `tests/unit/test_api.py`; that path does not exist. Worker correctly refused silent substitution; dispatcher re-issued with the canonical `/home/nea/ensemble-src/tests/test_api.py` (the file `bump_version.py` pins). Same intent (config.py +36 blast radius).
- HEAD: 45P/2F in 4.60s. Both failures (`test_send_message_success` :876, `test_global_exception_handler` :946) = `TypeError: object Mock can't be used in 'await' expression` at `daemon/routers/messages.py:325` (`await manager.command_dispatcher.dispatch`).
- **A/B adjudicated PRE-EXISTING-AT-BASE:** identical 2F/45P at `139ba352` (5.39s; import-root parity proven: `daemon.__file__` → worktree), mission diff EMPTY on `messages.py` + `command_dispatcher.py` + `tests/test_api.py`. These are 2 nodes MISSED by the standing QUARANTINE family "messages.py MagicMock-await class" (seam await introduced f9d377b9 2026-08-31, pre-base; 32 nodes quarantined 2026-09-06) → family now 32→34. Separate commission; NOT mission-caused.
- Logs: `/tmp/v0153-acc-2b{,-base}.log`.

## Task 3 — Mock-Quality Audit (TrueAuto mandate) — **RESULT: ALL TARGETS MATCH-REAL-SEMANTICS; greens EARNED**

- **Target 1 (thread-name/executor isolation): REAL-PROOF.** Production stamps `thread_name_prefix="UpgradeJournalReaperWaitpid"` at `upgrade_journal_sweep.py:173-177` (dedicated bounded executor, `max_workers=min(16, cpu+4)`); tests OBSERVE production-created state (`test_reaper_uses_dedicated_executor_isolated_from_default` :1444; `test_reaper_waitpid_runs_on_dedicated_executor_not_default` :1473 — the gold standard: real child via Popen, real `enqueue_reaper`, real executor, tracer only on `_waitpid_blocking`, asserts every recorded thread name starts `UpgradeJournalReaperWaitpid_` and none `asyncio_`; a regression to `asyncio.to_thread` flips names and fails). No test-set-name leak. (Anchor note: commission's ":3677/:3724" anchors drifted — those are the M-2 run_id-binding attestation pins :3677/:3724; thread-name pins live at test_upgrade_journal.py:1444/:1473.)
- **Target 2 (reaper-executor tests): REAL-SEAM.** Every reaper test instantiates the REAL `UpgradeJournalSweepService` + real `_reaper_queue`; no ad-hoc InstanceManager reaper; manager.py:3916-3931 is the spawn-seam ENQUEUE (sole-caller pin `test_spawn_executor_has_sole_production_caller_manager_drain` :1105 asserts callers=={daemon/manager.py:1}); `executor_exit`/`executor_still_running`/`executor_orphaned` emitted by real code (`upgrade_journal_sweep.py:437/:450-456`, `manager.py:3789-3792`) and confirmed ABSENT from `_TERMINAL_EVENTS` (`upgrade_journal.py:970`) per plan §11a.
- **Target 3 (9a–9g rider fixtures): REAL DECISION PATH.** Rider greps `$REPO_ROOT/daemon/migrations/versions/*.sql` (stage.sh:163-171); FAKE_REPO populates exactly that path (no-DROP) and FAKE_REPO_DROP populates DROP-DDL — no fixture→rider path drift; explicit 1/true/0/false honored; unset+DROP refuses exit 78 with all five WARNING tokens; unset+no-DROP quietly defaults true (`source=default=true`). **9g is a true regression pin, not a tautology** — a silent-false regression would produce rc=0 + manifest.json and fail 9g's `rc=78 && no-manifest` assertion.
- **Findings: 0 🔴 / 0 🟠 / 1 🟢** (🟢: a 9h empty-delta+DROP case would close a documentation loop; not required — stage.sh:182-184 comment + 9e WARNING already name the full-history intent).

## Task 4 — Tool-Lane E2E on Dev (port 8079) — **RESULT: ALL PHASES PASS**

- **Env fence PROVEN in-band:** standalone `#!/bin/bash` wrapper (not `source`d under dash — per LESSONS mandate); `POSTGRES_SURVIVORS=0` echoed on both boots; boot log: `SessionManager initialized with PostgreSQL checkpointer (localhost:5432/ensemble_dev)` — NOT prod. Ambient hazard confirmed pre-scrub (POSTGRES_HOST=10.44.0.2, ensemble_prod, ENSEMBLE_UPGRADE_LIVE=1 present — all stripped). `ENSEMBLE_SELF_ENV=dev` explicit.
- **ensure.md grep-verified before citing:** 53 lines, pack-mapped; boot-probe line = "Daemon running: ./dev.sh (health at localhost:8079)" (Release-Gate Prerequisites). Literal "30s must-not-crash" phrase absent from file; applied per commission directive. ensure.md is a BOOT probe doc, not an e2e-suite mandate — honored.
- **Phase A (boot):** healthy at 20:11:47Z (10s after start), alive at t+35s, 0 tracebacks. Sweep wiring verbatim: `UpgradeJournalSweepService started: interval=90s (default 90s), reaper_timeout=660s (default 660s), install_dir=<none — dev/unresolved>` + `[ServiceTool] reconcile_boot_sweep alive=0 reaped=0 errors=0`. Banner "v0.15.2" = branch version string pre-bump (cosmetic; bump is a release-cut step).
- **Phase B (read-only mirrors):** `/openapi.json` (135 paths) exposes NO HTTP routes for release_info/upgrade_status — they are agent tools; the read surface IS the tool lane. Exercised through a real ari instance (`tools.allow` includes `system_upgrade`) over real HTTP. Verbatim outputs captured: dev correctly reports its OWN state (`dir=none`, `journal: none`, `current symlink: none`, live self-probe `/livez version=0.15.2`). The v0.15.1-current/v0.15.2-staged state lives in the LIVE install (`~/agents-ensemble`, read-only verified: `current → releases/v0.15.1`, v0.15.2 present) — the dev daemon correctly CANNOT see it cross-env (env-self-match fence, D-FA2.3). **State surfaced CORRECTLY against the actual dev state.**
- **Phase C (tool-lane dry_run — the core e2e):** real `system_upgrade` invocation `{"target_env": "dev", "dry_run": true}` through manager → graph → ToolNode → tool body. Verbatim result: `"Error: UPGRADE REFUSED — reason=no-staged-install: no staged install dir resolves for this env — upgrades act on a staged releases/ install (run stage.sh first)."` — **exactly the expected refusal token; NOT a crash, NOT a live arm.** Daemon healthy post-turn (`/readyz` all-green).
- **Phase D (sweep liveness + no-arm-mutation):** startup wiring confirmed in log AND source (`api.py:1399 upgrade_journal_sweep.start()` + `:1400 app.state` + `manager.set_upgrade_journal_sweep`); boot reconcile ran; idle periodic ticks are `logger.debug`-gated (no INFO lines when idle — by design). Dev journal `<checkout>/releases/state.json` absent before AND after (never created). **Live journal sha256 IDENTICAL before/after** (`ce96e9d4…05dda`, 1634 bytes, mtime 12:17:23Z unchanged) — cross-env proof, read-only. Zero arm-class log lines.
- **Phase E (teardown):** identity-by-port (8079 tree: reloader 1921108 → server 1921110 → context7-mcp 1921122), SIGTERM → all tree pids gone ~4s, no SIGKILL; ports 8079/4124 free; **live 9797 (pid 1781911) and demo 7979 (pid 1781910) same pids as task start, untouched**; 8088 never contacted; repo HEAD unchanged.
- **Warnings (environmental, NOT mission defects):** (1) Boot #1 errored on dev LLM upstream (no primary; HA failover to dead backup `localhost:4001` → connection refused; LLM-HA latched in-memory on backup) — resolved with a scripted tool-call-emitting mock LLM on :4124 + restart; boot #2 is the evidence boot; the error lane itself behaved correctly (job finalized status=error, locks released). (2) ari instance `6cbbe3a9…` + 2 jobs left in `ensemble_dev` (harmless dev residue). (3) Mock LLM used dev-range port 4124 rather than the 10000-19999 mock range (free port, no conflict; convention note logged to LESSONS).

## Task 5 — Original-Defect Verification (per arm)

| # | Defect arm | Verdict | Evidence |
|---|---|---|---|
| (i) | Executor child exits invisible (no journal event; exit-78 unseen) | **DEAD** | Unit: `test_reaper_journals_exit_code_on_child_exit_78` (test_upgrade_journal.py:1339 — real child exits 78 → `executor_exit` journaled w/ pid+exit_code+log tail), `test_reaper_benign_detaches_and_journals_executor_still_running_on_timeout` (:1363), `test_reaper_continues_after_journal_write_oserror` (:1399), `test_reaper_stop_is_bounded_on_hung_child` (:1513). Code: emit at upgrade_journal_sweep.py:437; sole-caller pin :1105; `executor_exit` ∉ `_TERMINAL_EVENTS` (upgrade_journal.py:970). Mock audit: REAL-PROOF (tracer flows through production executor). |
| (ii) | Stale pending_op starves ~20min (reconcile tool-call-only) | **DEAD** | Unit: `test_boot_sweep_clears_stale_pending_op` (:1254), `test_periodic_sweep_skips_live_executor` (:1275), `test_periodic_sweep_stale_evidence_does_not_block` (:1306). E2E: sweep service started at boot (interval=90s, reaper_timeout=660s) + `reconcile_boot_sweep alive=0 reaped=0 errors=0` executed — reconcile is boot/tick-driven, no longer tool-call-only. |
| (iii) | Arm preflight ran AFTER nonce burn (nonce burned on doomed arms) | **DEAD** | Unit: `TestArmPreflightBeforeBurn` ×4 (test_upgrade_tools.py:2756 `executor-scripts-unavailable`, :2768 `preflight-argv-unconstructable`, :2787 `preflight-argv-malformed` — each asserts nonce INTACT (`consumed_at is None`, no pending_op/lock/markers) after refusal; :2810 control: healthy path burns nonce only AFTER checks pass → "UPGRADE ARMED"). All in the 274/274 green pack. |
| (iv) | `upgrade_status` reports TERMINAL on non-terminal state (PENDING-class noise) | **DEAD** | Unit: `TestTerminalOutcomeFilter` ×7 (test_upgrade_tools.py:808/:818/:827/:836/:848/:855/:862) — `nonce_consumed` filtered from `_terminal_outcome`; armed journal renders `PENDING`; label renamed to "awaiting executor (pending)" (old string absent). |
| (v) | stage.sh silent rollback_safe=false (v0.14.2/v0.15.1 accident class) | **DEAD** | Bash section 14: 9a–9g **20/20 GREEN** at HEAD (A/B-proven new-at-HEAD, all passing); 9g regression pin traced non-tautological (silent-false ⇒ rc=0+manifest ⇒ 9g FAILS); 9e WARNING carries all 5 tokens (`ENSEMBLE_ROLLBACK_SAFE=1`, `=0`, `migration delta`, `v0.14.2`, `ADR-035`); fixtures grep the same path the rider greps. |

**All five arms: DEAD. No arm ALIVE. No arm UNTESTABLE** — arm (ii)'s live-rung tail (actual live arm → executor → gate → commit) is by design NOT exercisable on dev (no staged install) and NOT permitted on live (promote is user-manual); the unit+e2e layers cover everything dev-exercisable, per plan §5f lane classification (lane intersection empty).

## ensure.md Status (blast-radius scoped)

- **Core #1 (no regressions in changed packs): ✅** — upgrade battery 295/295; interlock pack 274/274; release-journal suite failures 100% base-symmetric GNU-debt (mission sections green).
- **Core #4 (dev.sh `--timeout-graceful-shutdown 10`): out of diff** (dev.sh untouched by mission; last-validated state stands; e2e observed graceful SIGTERM exit ~4s functionally).
- **Core #2/#3 (concurrency pack): out of blast radius** — mission adds no asyncio event-loop DB patterns outside its new service; the new service's thread-identity IS pinned by `test_reaper_waitpid_runs_on_dedicated_executor_not_default` (dedicated executor, not the loop's default).
- **Release Gate: not triggered** — per plan §5f lane-intersection rule (empty by import audit) and change-type classification; boot probe executed within e2e Phase A anyway.
- No ensure.md contradictions with my pack/timeout/scoping rules → no Improvement Notices.

## Quarantine Updates (this gate)

1. `messages.py MagicMock-await` family 32→34: +2 missed nodes `tests/test_api.py::{test_send_message_success, test_global_exception_handler}` (base-evidenced 2026-09-26 @ 139ba352).
2. NEW family row: `test_watch_job_mission_terminal.py` ×2 (mission-terminal lineage debt; base-evidenced 2026-09-26 @ 139ba352; deterministic).
3. Release-journal GNU host-arch family re-verified at v0.15.3 gate: 246P/42F base-symmetric; +B2b load-flake member noted.

## Gaps

**None.** All 7 dispatched workers reported with verbatim evidence. No re-dispatches needed (one path-correction re-issue on 2b, disclosed above).

## Follow-ups (non-blocking, for the caller)

- 🟠 Separate commission: `daemon/routers/messages.py:325` await-seam vs the 34-node Mock family (test-side AsyncMock migration).
- 🟠 Separate commission: mission-terminal watch lineage debt (2 nodes, `test_watch_job_mission_terminal.py`; incl. the held-condition gate + AsyncMock-compare surface).
- 🟢 `tests/test_release_journal.sh:203` undefined `assert_not_contains` helper (script stub; pre-existing, non-counting).
- 🟢 Optional 9h empty-delta+DROP rider case (documentation loop-closer).
- 🟢 P2.1 GNU date debt (3 lib.sh sites) — standing backlog; v0.15.3 does not regress it.
- 🟢 Dev LLM bring-up gotcha (no primary upstream; HA latch on dead backup) — recorded to KB by the e2e worker; skill `dev-bringup-tool-lane-e2e` created.
