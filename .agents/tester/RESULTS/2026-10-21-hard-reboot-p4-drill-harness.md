# Test Report — hard-reboot recovery P4 drill HARNESS PREP (hrr-20261021)

**Date:** 2026-10-21
**Worktree:** `/home/nea/ensemble-src-wt-hard-reboot-recovery`, branch `feature/hard-reboot-recovery`
**Mandate:** build + prove the P4 drill harness executes on this host; report the plumbing facts the
FINAL acceptance run needs. **This report does NOT issue final acceptance verdicts.**
**Binding spec:** `.agents/shared/planning/hard-reboot-recovery/p4-drill-harness-spec.md` (tester-authored)
**Plan refs:** `plan-overview.md` P4 row · `phase1-plan.md` AC6 · `c4-lane6-ruling.md` · `architecture-recommendation.md` A3/A7

**Lanes:** recon-host `b29c08ed` · recon-code `7e7a2f49` · author-harness `9356af92` ·
execute-drill `61d1ac73` · harness-fidelity `3f91d52f` · pack-registration `11ffce6b`

---

## Summary

| Item | Result |
|---|---|
| Drill executes end to end on this host | **YES** — PASS ×2 consecutive (`Failed checks: 0`) |
| **Final independent verification** | **PASS at the COMMITTED artifact `8eb8ef643`** — `Failed checks: 0`, wall ≈71 s, pack file verified CLEAN in git before the run (the verification covers the commit, not a working-tree variant), `tree_drift_during_run: "no"` |
| Harness | `test/packs/hard_reboot_drill_e2e_test.sh` — 9 phases P0–P8, one self-contained script |
| Runs | 4 total: #1 FAIL (harness typo) → #2 FAIL (3 harness expectation defects) → **#3 PASS 55 s** → **#4 PASS 52 s** |
| Daemon feature under test | **Every substantive assertion passed on the first fully-executed run (#2)** — all failures were harness-side |
| **AutoContinue RESUME (P4-1)** | **NOW OBSERVED** — `candidates=1 scheduled=1` + `[BOOT_CONTINUE] instance=17c56c83 work_id=c0a65258 epoch=2026-10-21T13:25:00.642543]` in the ANCHORED post-restart journal (commit `8eb8ef643`) |
| Marker anchoring (false-positive fix) | **PROVEN** — the FULL journal holds 2 `MQ boot pass: reset=` lines (pre-crash `reset=0` + post-restart `reset=1`); the ANCHORED view holds exactly **1** (the post-restart boot's). The pre-fix poll would have matched the stale line instantly |
| A3 accounting observed | pre `processing=1/pending=0` → post `processing=0/pending=1` — exactly the M5 pin |
| MQ boot pass | `[HardReboot] MQ boot pass: reset=1` — the seeded fresh-dead row WAS recovered |
| Schedule survival | row survived the reboot (`source_configs` row present, `running`, enabled) |
| Zero residue | confirmed after every run; independently re-verified outside the pack |
| Live surface | **NEVER TOUCHED** — attested before/after on every run |
| Final acceptance verdicts | **NOT ISSUED** (out of mandate for this lane) |

---

## Scope Decision

The full P0–P8 drill was run end to end (not a reduced scope) because the drill IS a single
self-contained pack and the question asked was "does it execute on this host" — a partial run
would not answer it. The **final acceptance verdicts** are explicitly out of scope: the feature
code is mid-fold (the developer is applying the architect's ruling; the reviewer delta check has
not run). Running the drill again as the acceptance gate is a separate commission.

---

## (a) Does the drill execute end to end on this host — YES

Exact command sequence:

```bash
cd /home/nea/ensemble-src-wt-hard-reboot-recovery

# Setup smoke only (creates+drops a throwaway PG role/DB, scratch CWD, FAKE_HOME, port):
HRR_PHASE="P0,P1,P2" timeout 300 bash test/packs/hard_reboot_drill_e2e_test.sh

# Full drill (P0..P8):
timeout 300 bash test/packs/hard_reboot_drill_e2e_test.sh
```

Observed phase detail (run #3, the first green run):

```
P0 preflight + env-poison fence  -> fence self-test REFUSED a deliberate ensemble_prod pointer (rc=1)
P1 scratch PG                    -> throwaway role + DB created on LOCAL 16/main
P2 scratch env/CWD/port          -> FAKE_HOME + scratch CWD + foreign-CWD resolution PROVEN
P3 isolated boot                 -> daemon in /system.slice/<drill-unit>.service, cgroup ASSERTED
P4 seed                          -> RUNNING instance + schedule + fresh-dead MQ row (M1 shape)
P5 hard kill                     -> kill -9 on the cgroup-verified PID
P6 simulated reboot              -> unit stop + FRESH start (new PID 665325, new boot epoch)
P7 observe                       -> markers + A3 accounting + timings
P8 cleanup                       -> scratch db/role dropped, unit gone, port free, dirs removed
RESULT: PASS
```

Isolation was **asserted, not assumed**: `post_restart.json.daemon_cgroup` =
`0::/system.slice/hrr-drill-20261021123309-664204.service` — never `ensemble-main.service`.

---

## (b) Environment blockers

| Capability | Status | Notes |
|---|---|---|
| `systemd-run` system-level transient | **WORKS, no sudo** | `systemd-run --unit=<n> --wait --collect …` → `/system.slice/<n>.service` |
| `systemctl stop <system-unit>` | **needs `sudo -n`** | without it: "Interactive authentication required." — `sudo -n` is passwordless here |
| `journalctl -u <unit>` | **WORKS, no sudo** | |
| local PostgreSQL 16 | **OK** | `16/main` on 127.0.0.1:5432 (16.15); `sudo -n -u postgres psql` passwordless |
| throwaway role + DB | **OK (created + DROPped, proven gone)** | TCP 127.0.0.1 = scram-sha-256 → the role password is required |
| shared `ensemble` role CREATEDB | **NO** (`rolcreatedb=f`) | worked around: `CREATE DATABASE … OWNER <throwaway>` — migration/schema creation PROVEN |
| `ensemble_sandbox` DB | **DOES NOT EXIST locally** | the house note is confirmed; the drill uses a purpose-named DB instead |
| port 10000–10100 | **101/101 free** | drill takes 2 (daemon + future mock); 8079/9797/7979/8088 rejected |
| `ensemble-dev` daemon on :8079 | free | not used |
| `shellcheck` | **NOT INSTALLED** | `bash -n` is the only available static gate — a real gap in lint evidence |
| **Second live daemon discovered** | `ensemble-demo.service` on **:7979** (`/home/nea/agents-ensemble-demo`) | added to the never-touched set |

**Blockers that remained: none.** Every capability the drill needs exists on this host.

---

## (c) Measured timings and the AC6 ≤30 s basis

| Run | T_restart→T_readyz | T_recovery (as reported) | T_end_to_end | AC6 ≤30 s |
|---|---|---|---|---|
| #3 (pre-anchor) | 14 s | 15 s | **15 s** | ✅ |
| #4 (pre-anchor) | 13 s | 13 s | **13 s** | ✅ |
| **final @ `8eb8ef643`** (anchored) | 15 s | 15 s | **15 s** | ✅ |

**The AC6 criterion will be measured against the DAEMON recovery path only:** transient-unit
stop → fresh start issued → `/readyz` 200 → the recovery markers observed. It does **NOT**
include host-boot time (firmware/bootloader/kernel), which is exactly the qualifier the runbook
already carries ("`host boot time + ~14 s`").

**Measurement caveat — FIXED AND PROVEN.** The pre-anchor `T_recovery` could latch a STALE
pre-crash journal marker. Commit `8eb8ef643` anchors every post-restart observation to a journal
cursor captured immediately before the simulated reboot. **Negative control at the committed
artifact:** the FULL journal contains **2** `MQ boot pass: reset=` lines (pre-crash `reset=0`,
pid 763311, and post-restart `reset=1`, pid 764409), while the cursor-anchored view contains
exactly **1** — the post-restart boot's. Corroborated on an independent marker: `Boot epoch
captured` is 2 in FULL vs 1 in ANCHORED. The anchoring is behavioural, not cosmetic.

---

## (d) What the drill CAN vs CANNOT observe

### CAN observe (all evidenced)
- Boot epoch: `Boot epoch captured (DB clock): 2026-10-21T12:33:56.022366`
- MQ boot pass firing **and its effect**: `[HardReboot] MQ boot pass: reset=1` + SQL counts flipped 1/0 → 0/1
- AutoContinue pass firing: `AutoContinue boot pass: ContinueResult(candidates=…, scheduled=…)`
- Task-less orphan sweep: `[BOOT_CONTINUE] orphan-sweep summary: candidates=… …`
- DependencyBus: `DependencyBus started (recovered=0 FIRED-but-unsent watchers)`
- Schedule survival: `source_configs` row present after the reboot (same row, enabled)
- Health: `/livez` 200, `/readyz` 200 (`status=ready`, all components true)
- Scratch-PG binding: `Creating PostgreSQL engine: 127.0.0.1:5432/<scratch>`
- Busy-guard **predicate** state from real SQL counts + an explicit branch label

### CANNOT observe — REPORT ITEMS, never silent passes
1. **No real host reboot.** The reboot leg is unit stop + fresh start. Kernel power-loss
   semantics — cold-PG WAL replay, filesystem journal replay, fsync-loss of in-flight writes —
   are **not** reproduced. `kill -9` of a process is not a host power-loss.
2. **PG is NOT started in parallel with the daemon.** The local cluster is already up, so AC6's
   "PG starting concurrently → preflight exit-75 → backoff" race is **not** reproduced. The G6
   `After=postgresql.service` ordering is covered by the unit-template grep + the P1
   `connect_timeout` pack — **not** by this drill.
3. **The transient unit is not the production unit.** `Restart=on-failure` / `RestartSec=10` /
   `KillMode=mixed` / `TimeoutStopSec=90` are only partially mirrored; `After=` is irrelevant in
   a transient unit.
4. **G6 boot-preflight success is UNOBSERVABLE in logs** — a *daemon-side* observability defect:
   `daemon/__main__.py main()` emits its INFO records **before** `daemon.api` installs the
   logging handlers, so the root logger's `lastResort` (WARNING) drops them. Proven empirically
   (the `__main__` logger produced ZERO records for its own INFO lines; a stubbed probe reached
   the preflight and exited 75). The drill substitutes a stronger observable marker.
5. **StaleTaskRecovery (STR) is not observable inside a 5-minute cap — CONFIRMED from source.**
   Threshold default = 10 minutes (`daemon/config.py:1821`, `stale_task_recovery_threshold_minutes`);
   at boot `recover_on_startup` passes `boot_epoch` into `find_stale_running_tasks`, whose epoch
   clamp deliberately defers a frozen-heartbeat orphan to ≈ boot+threshold
   (`daemon/services/stale_task_recovery.py:752-766`, `:763`), and the background loop re-checks
   every 60 s (`:211`; interval `daemon/config.py:1746`). Earliest reap ≈ boot+10min+≤60s ≫ the
   300 s outer cap — and post-resume the task is live and heartbeating, so STR has nothing to
   reap inside the window anyway. Not required when the primary paths fire (that is the design),
   but any "all mechanisms fire" claim must say STR was not observed, by design.
   **The P4-1 weakness is now CLOSED**: the resumable instance yields `candidates=1 scheduled=1`
   plus the per-instance `[BOOT_CONTINUE] instance=… work_id=… epoch=…` success line — the
   drill now observes an actual RESUME, not merely a pass that fired.
6. **D13 task-less RUNNING ghosts are OUT OF SCOPE** (critical note `e82c89e8`) — reported
   (`d13_taskless_running_ghosts: 1`), never failed.
7. **Lane-6 healing does not ship** — the P2 claim was architecturally narrowed
   (`c4-lane6-ruling.md`). Never asserted.
8. **Turn-resume vs re-dispatch is not distinguishable**, the one-shot scheduler duplicate-fire
   marker is in-RAM (`SchedulerAdapter._is_one_time_executed`), and an interrupted tool call may
   re-run (NG-4 at-least-once).
9. **Busy-guard CLEARING is override-gated.** The role-free REST path does not expose the agent
   tool lane's busy-guard; the drill evaluates the **predicate** on real SQL counts (the M5
   method). On the shipped default (`QUEUE_DISCARD_ON_STARTUP=false`) the expected outcome is
   **visible-pending** (predicate stays True), NOT cleared.
10. **Tree drift** — a concurrent dev lane mutates the worktree; the drill records HEAD+porcelain
    at boot and exit and flags `tree_drift_during_run`.

---

## (e) Harness location and how to run it

- **Harness:** `test/packs/hard_reboot_drill_e2e_test.sh` (house pack dir, `<scope>_<type>_test`)
- **Registered in:** `.agents/tester/PACKS.md` (commission section at the top)
- **Evidence bundle:** `/tmp/hrr-drill-<ts>/` — `preflight.json`, `pg_setup.log`, `boot.log`,
  `journal-<unit>.log`, `seed.json`, `pre_crash.json`, `post_restart.json`, `timings.json`,
  `cleanup.json`, `tree_state.json` (+ `livez.json`, `readyz*.json`, `*_create.json`)
- **Phase selector:** `HRR_PHASE="P0,P1,P2"` for partial runs; unset = all of P0–P8
- **Timeout:** internal watchdog 280 s + external `timeout 300` (dual-layer)

---

## (f) Live-untouched attestation

Byte-identical before and after **all four runs**, from `cleanup.json` **and** independently
re-verified outside the pack:

| Surface | Value | Status |
|---|---|---|
| `ensemble-main.service` MainPID | 528942 | unchanged |
| `ensemble-main` NRestarts | 0 | unchanged |
| `ensemble-main` ActiveEnterTimestamp | Wed 2026-10-21 10:38:47 UTC | unchanged |
| `ensemble-demo.service` MainPID | 898 | unchanged |
| live install `current` | `/home/nea/agents-ensemble/releases/v0.26.3` | unchanged |
| demo install `current` | `/home/nea/agents-ensemble-demo/releases/v0.16.3` | unchanged |
| ports 9797 / 7979 | same live `ensemble-prod` pids (529346 / 1147) | unchanged |
| remote PG `10.44.0.2` / `ensemble_prod` | never connected | — |
| port 8088 | never signalled | — |
| `/etc/systemd/system/*` | never touched | — |

**Zero residue:** scratch DB rows `0`, scratch role rows `0`, `hrr-*` units `0`, drill port
`free`, scratch CWD removed, FAKE_HOME removed — reproduced on both green runs.

---

## Quick fixes applied (harness-only; the daemon feature was correct throughout)

| Commit | Fix | Size |
|---|---|---|
| `ddb3f1a42` | `READY_CODE` vs `READYZ_CODE` unbound variable under `set -u` (run #1 aborted AFTER a healthy `/readyz 200`) | 4 functional lines |
| `5480743ef` | 3 expectation defects: `POST /api/instances` mints its own UUID (not the requested id); `POST /api/schedules` mints its own `source_id` PK and stores the request as the **label** in `source_configs.name`; the G6 preflight marker is structurally unobservable → replaced with the observable, stronger `Creating PostgreSQL engine: <scratch>` marker | 14 functional lines (33 raw incl. comments) |
| `8eb8ef643` | **2 harness defects:** (1) *correctness* — post-restart observations now anchored to a journal cursor captured immediately BEFORE the simulated reboot, so a stale pre-crash marker can no longer latch (a false positive on the most important assertion); (2) *fidelity* — a stalled mock-LLM turn (+300 s sleep, non-streaming house shape, no tool call) creates a REAL task row + checkpoint, so the auto-continue pass has something to resume; the drill now **fail-closes** if the resumable seed cannot be established (anti-`candidates=0` tripwire) | +424 / −29 (one file) |

No fence was weakened. Both commits touched ONLY the pack file (targeted `git add`).

---

## ensure.md validation status — PRE-ACCEPTANCE SIGNAL (not the final gate)

**Deliberately scoped.** This lane's own change set is the harness; the FEATURE branch's
ensure.md gate belongs to the acceptance commission after the fold + review land. What follows
is pre-acceptance signal collected because the packs are cheap and the branch's new test files
had **no covering pack at all** (a PACKS.md integrity gap, now closed).

### In-scope Core results (each an independent worker run, scrubbed, one pack per worker)

| Requirement | Pack | Result | Note |
|---|---|---|---|
| Critical: no regressions in changed packs | `hrr_connect_timeout_unit_test` | **PASS 9/9** (0.43 s, ×2 author + ×1 independent) | fence self-test refused a deliberate `ensemble_prod` pointer; mock-based — pins the `connect_timeout=BOOT_DB_TIMEOUT_S` binding, does NOT exercise a real firewall-DROP host (E2E concern, correctly not claimed) |
| Critical: no regressions in changed packs | `hrr_mq_boot_recovery_unit_test` | **PASS 21/21** (3.52 s author ×2 + **3.31 s independent**) | pure file-backed SQLite; PG never contacted. **D19 lane-6 scope reconciled — see below** |
| Critical: no regressions in changed packs | `boot_probes_unit_test` (covers the branch-modified `tests/test_main_entry.py`) | **PASS 83/83** (12.84 s + 13.69 s ×2) | run under an explicit child-env scrub — the pack has **no scrub of its own**. `test_health_probes.py` 56 + `test_main_entry.py` 27. **The branch's 12-line change to `test_main_entry.py` is PROVEN docstring-only** — `ast.dump` identical with all docstrings stripped (True) vs present (False); all 27 nodes green |
| Critical: deadlock/concurrency integrity | `concurrency_atomic_unit_test` | **PASS 99P/0F/74S** (99.12 s) — **baseline-exact** | see the coverage-truth qualification below |
| Critical: `dev.sh` includes `--timeout-graceful-shutdown 10` | static grep (worker lane `8603ea53`) | **PASS** — `dev.sh:155` carries the flag on the live uvicorn command line; `git diff 4c888fbbb..HEAD -- dev.sh` is EMPTY (the branch did not touch it) |
| Important: async-await correctness (`_get_system_prompt_tokens`, `_compute_context_usage`, `get_queue_stats`) | AST census — every call site classified (worker lane `8603ea53`) | **PASS — zero unawaited call sites.** 3/3 + 1/1 + 5/5 production calls awaited; 3/3 + 1/1 + 12/12 test calls awaited; all remaining hits are AsyncMock assignments / references / docstrings. The branch's MQ-repo diff is purely ADDITIVE (two new methods `:425` + `:447`); `get_stats`/`dequeue`/neighbours byte-identical to base; `instance_messaging.py` and `manager.py` have EMPTY diffs |
| Nice-to-have: no dead code from the fix | added/removed-symbol reference table (worker lane `8603ea53`) | **PASS** — 4 added symbols (`find_stuck_messages_before`, `reset_stuck_message_to_ready`, `_boot_pass_enabled`, `recover_stuck_messages_on_startup`), EACH with ≥1 production caller; 0 removed `def`/`class`; both rewritten URL-builders still exist and are still called; `tests/helpers/hard_reboot_fixtures.py` symbols 9/9 consumed; scrubbed import check OK |

### ✅ D19 lane-6 scope reconciliation — CLOSED (no dead-scope tests)

The concern: `c4-lane6-ruling.md` narrowed P2 so lane-6 healing does **not** ship, yet the test
file contained "D19 lane-6" tests. **Verdict: all of them pin the NARROWED behavior; none
asserts dead scope.** They are the ruling's own *replacement* artifact for the withdrawn
"heals exactly once" mandate, not tests for behavior that does not ship.

Correction to the count: there are **THREE** D19/lane-6 tests, not four — the 4th D19 symbol is
the shared fixture helper `_seed_lane6_wake_shape` (`test_message_queue_boot_recovery.py:404`),
not a test.

| Test (file:line) | Asserts | Verdict |
|---|---|---|
| `test_lane6_composition_is_mutually_exclusive_documented` `:449` | the MQ pass does **nothing** to a lane-6-eligible row (`recover… == 0`, status stays `processing`, `find_stuck_wake_candidates == []` before AND after) | pins narrowed behavior — the ruling §3-mandated replacement for the withdrawn claim |
| `test_lane6_composition_control_the_guard_is_what_holds_the_row` `:485` | with the task flipped to `pending` the pass **does** flip the row (`== 1` → `ready`), and lane 6 still cannot match it | control proving the non-composition is caused by the **guard**, not a malformed fixture |
| `test_lane6_live_shape_proves_the_fixture_is_lane6_eligible` `:511` | lane 6's own live predicate (`find_stuck_wake_candidates`) matches its own producer shape; **the MQ boot pass is never invoked in this test** | positive control pinning the ruling's affirmation that **lane 6 keeps working via its live producer**, independent of the reset |

The narrowed doctrine is carried inside the docstrings themselves (`:458` "the earlier
architecture-recommendation claim that the two 'compose' was FALSE and is withdrawn by D19";
`:459-460` why forcing the composition would re-arm the row and deliver the message twice).

**Two non-defect notes for the leader:** (1) scope hygiene — the third test lives in the P2
(MQ boot-recovery) pack but its subject is RDRS lane 6; it is ruling-mandated so it should
stay, flagged only so a future pack-scope audit does not mistake it for an MQ-pass test.
(2) Name divergence — the ruling §4 names `test_post_pass_accounting_is_honest_and_busy_guard_is_not_cleared`;
the implementation ships `…_moves_the_stranded_row_from_processing_to_pending` (`:203`) with a
docstring mapping itself to ruling §4 M5-a assertion-for-assertion — the sanctioned
"round A's test already satisfies it" path per ruling `:569`. Name divergence only.

### 🟠 Coverage-truth qualification on `concurrency_atomic_unit_test` (must ride into the acceptance record)

The pack collects **173 nodes but 74 (42.7%) are permanently inert** — module-level `pytestmark`
skips because 6 of the 7 race families named in its own header (cascade races, cascade
unified/integration, observer race, observer correlation, observer late-msg, finalize H15)
tested the **removed CorrelationManager** and execute zero nodes. The families that actually
run: deadlock fix + DB-sync-off-loop (10), instance atomic locks (22), project atomic locks
(32), threading-serialization gate (5), report-lane phase 2 (30). The skips are **by design and
identical in every historical baseline** (standing, not a branch regression) — but an acceptance
record citing this pack as "deadlock/concurrency integrity" must state that the cascade/observer
families are not executed. The ensure.md "no sync DB calls on the event loop" claim **is**
genuinely covered (`TestProcessChildCompletionDbSyncOffloaded`, 2 nodes).

### 🟠 Pack-quality findings (recorded for follow-up; none block the harness lane)

1. `concurrency_atomic_unit_test`: `RESULT: FAIL`/`RESULT: TIMEOUT` are **dead code** (`set -e`
   terminates before the exit code is captured) — read the EXIT CODE, never grep the RESULT line.
2. `concurrency_atomic_unit_test` has **no env scrub of its own**, and its historically
   registered invocation (`env -u POSTGRES_DB -u DATABASE_URL_POSTGRES`) **under-scrubs** — it
   leaves `POSTGRES_HOST`/`USER`/`PASSWORD`, the `PG*` family, `SSL_CERT_FILE`/`SSL_CERT_DIR`
   and `ENSEMBLE_SELF_ENV=live` ambient. Inert for this PG-free pack; unsafe as a template.
   The registered form should be widened to the full 22-var scrub.
3. `boot_probes_unit_test` likewise has **no scrub of its own** — run only under an explicit
   wrapper. Recommend the house scrub be baked in.
4. **🔴 HOUSE-SAFETY GAP in `scripts/run_tests_scrubbed.sh`** — the canonical scrub wrapper does
   **NOT** unset `ENSEMBLE_SELF_ENV` or `ENSEMBLE_UPGRADE_LIVE`. An ambient `ENSEMBLE_SELF_ENV=live`
   shell therefore **passes its gate**. It also cannot wrap a command at all (it hard-execs
   `.venv/bin/pytest "$@"`), so it cannot guard a pack invocation — every pack that relies on it
   is guarded only for the `POSTGRES_*`/libpq family. Recommend: extend the family list with
   `ENSEMBLE_SELF_ENV` + `ENSEMBLE_UPGRADE_LIVE`, and add a command-wrapping mode so packs can
   source one guard instead of each re-implementing it.
5. `boot_probes_unit_test` header doc drift — claims addopts `-m 'not integration and not postgres'`
   while `pyproject.toml:94` adds `and not slow`. Excludes nothing today; a future `slow`-marked
   test in scope would be silently deselected under an unchanged header claim.

### Release Gate

**Not run** (requires a live dev daemon + real LLM calls; out of scope for harness prep).

---

## Open questions carried to the leader

1. **G6 preflight observability gap is a DAEMON defect, not a drill artifact.** Operators never
   see the preflight success on the `python -m daemon` / frozen entry points because `main()`
   logs INFO before `daemon.api` installs handlers. Proposed fix: install the logging handlers
   (or bind a handler) before `main()`'s INFO records, or emit the preflight outcome through a
   handler-independent path. Not fixed here — daemon code was out of the tester's scope.
2. **Should the drill's timing anchoring + resumable-instance fidelity land before the final
   acceptance run?** Both are in flight in this lane (marker anchoring is a correctness fix; the
   resumable instance converts "the pass fired with `candidates=0`" into "the pass scheduled a
   real resume"). My recommendation: yes — otherwise P4-1 ("AutoContinue resumes RUNNING
   instances") is asserted from a pass that had nothing to resume.
