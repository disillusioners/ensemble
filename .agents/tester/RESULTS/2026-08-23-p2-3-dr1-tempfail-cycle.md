# DR-1 — tempfail→respawn full cycle on demo (P2.3 batch B5, drill 1 of 3)

- **Date:** 2026-08-23 · **Recorded by:** worker (B5 DR-1 dispatch)
- **Branch:** `feature/self-restart-p2p3-ladder-drills` @ `68b54d96` (runbook version with all B4.5 corrections incl. F5)
- **Runbook:** `docs/runbooks/upgrade-drills.md` §0 (prereqs) + §2 (DR-1 procedure), executed as written except where the friction log records a deviation
- **Verdict line: `DR-1 FAIL: exit-75 tempfail path unreachable on the deployed frozen-binary boot — PG-unreachability yields uvicorn exit-3 on the crash track (burst-budget-consuming), not exit-75; 0 tempfail cycles observed, crash_count 1→4 in the attempt; demo restored green, live untouched`**

**Redaction rule:** the live port is rendered `<live-port>` throughout — zero live-port literals in this file. Demo port 7979 and the drill's closed probe port 39417 are not restricted.

---

## 1. Verdict table (pass criteria vs observed)

| # | Criterion (runbook §2) | Evidence excerpt | Result |
|---|---|---|---|
| C1 | ≥2 exit-75→backoff→respawn cycles observed end-to-end | `grep -c "exited 75"` over the drill window = **0**. Observed instead: 4 cycles of `child exited 3 (crash #N …) — restarting in {10,20,40,80}s` | **FAIL** |
| C2 | Backoff timestamps within capped 5s→60s tempfail schedule | Backoffs observed 10→20→40→80s = the **crash-track** schedule (10s→300s ×2), not the 75-track 5s→60s. (Schedule itself behaved per launcher code — wrong *track*.) | **FAIL** |
| C3 | Burst-budget-EXEMPT — `.launcher-state` `crash_count` UNCHANGED | `crash_count` **1 → 4** across the drill (pre: `crash_count=1`; cycle captures: 3 then 4; final: 4) | **FAIL** |
| C4 | Zero abort marker | No `burst`/`abort` line in `data/launcher.log`; loop stopped manually at crash #4, below the >5/10min threshold | **PASS** |
| C5 | Post-restore `/livez` 200 AND `/readyz` 200 `reasons: []` | `{"status":"alive",...,"version":"0.10.5"}` + `{"status":"ready","components":{"database":true,...},"detail":{"reasons":[],...},"draining":false}` (17:54:39Z) | **PASS** |
| C6 | Demo `.env` restored bit-exact, no drill knob left behind | md5 `1ba30c018078a60281cba4baeacc03c4` before == after; `^DATABASE_URL=` grep empty before==after; no `.env.drill-backup` exists (fallback branch never taken — override stayed ambient) | **PASS** |
| C7 | Live pid checkpoints byte-identical start/end | pid 31150 / ppid 31130 / lstart `Sat Aug 22 10:04:07 2026` — start, intermediate #1, #2, and end all byte-identical (§4) | **PASS** |

**Pass criterion (one line) NOT met → DR-1 FAIL.** The launcher supervisor logic itself is healthy (correct crash-track schedule, correct exempt logic *for actual 75s*); the failure is that the deployed daemon **cannot emit exit 75 for PG-unreachability** — root cause F-DR1-1 below.

---

## 2. Execution timeline (UTC; local log lines are +0700)

| Time (UTC) | Step | Event |
|---|---|---|
| 17:47 | §0.1 | `status.sh demo`: triple `demo / ~/agents-ensemble-demo / 7979 / ensemble_demo`; journal healthy; `current → releases/v0.10.5-p2.1-e2e2`; version smoke OK |
| 17:48 | §0.1 | `/livez` 200 alive 0.10.5 (uptime ≈68343s ⇒ same daemon lineage as DR-0); `/readyz` 200 `reasons:[]` |
| 17:48 | §0.3 | `uv sync --extra dev` clean |
| 17:48 | §0.2/§0.4 | No drift vs DR-0 (same release, same launcher 69871 lstart `Aug 23 05:49:20`, same daemon lineage) → no DR-0 re-run needed. Live baseline captured: **31150/31130**, lstart `Sat Aug 22 10:04:07 2026` — matches DR-0 record |
| 17:48 | D1.1 | `grep '^DATABASE_URL='` demo `.env` → **empty** (PG config is `POSTGRES_*` parts, lines 34–38); md5 baseline taken |
| 17:49 | D1.2 | Assert-then-SIGTERM on launcher 69871 (cwd=demo, lstart match, no live-port/live-path opens) → `stop-ensemble.sh` TERM → clean stop, 7979 clear. Closed-port probe: `nc -z 127.0.0.1 39417` → refused |
| 17:50:52 | D1.2 (as written) | Launch with ambient `DATABASE_URL=postgresql://drill:drill@127.0.0.1:39417/drill_unreachable` |
| 17:50:55 | D1.2 | Engine line: `Creating PostgreSQL engine: localhost:5432/ensemble_demo` (REAL DB) → override **inert** |
| 17:51:15 | D1.2 | `/livez` 200 green, `exited 75` count 0 → runbook's documented fallback trigger fires **on observation** |
| 17:52 | fallback branch | Runbook fallback (edit `DATABASE_URL` in `.env`) is **also inert** — no reader of that key exists (repo-wide grep; deployed launcher grep). NOT executed (dead mutation). **Adapted knob:** ambient `POSTGRES_URL` (documented deviation, see friction log FL-3). Assert+stop #2 (87762/87760) clean |
| 17:51:58 | drill loop | Relaunch with ambient `POSTGRES_URL='postgresql://drill:drill@127.0.0.1:39417/drill_unreachable'` |
| 17:52:02 | D1.3 | `child exited 3 (crash #1, uptime 4s) — restarting in 10s` — traceback: lifespan → `manager.initialize` → `create_postgres_checkpointer` → `psycopg.OperationalError: connection to server at "127.0.0.1", port 39417 failed: … Connection refused` → `Application startup failed. Exiting.` Same boot's engine line: real `ensemble_demo` (split-brain, F-DR1-2) |
| 17:52:15 | D1.3 | crash #2 → backoff 20s |
| 17:52:38 | D1.3 | crash #3 → backoff 40s (`launcher-state-cycle1.txt`: `last_exit=3 crash_count=3 last_backoff=40`) |
| 17:53:22 | D1.3 | crash #4 → backoff 80s (`launcher-state-cycle2.txt`: `crash_count=4`) |
| 17:53:52 | STOP | Dispatch stop-condition hit (unexpected exit code + budget decrement). No further drill attempts, no patching. Evidence frozen |
| 17:54 | D1.4 | Assert-then-SIGTERM on 89830/89832 (cwd=demo, lstart `Aug 24 00:51:58` match, no live-port/live-path opens; 89830 identified as the drill's own `/bin/sh -c` nohup wrapper) → `stop-ensemble.sh` TERM both; port free (child was mid-backoff) |
| 17:54:16 | D1.4 | Restore launch, **zero ambient override** |
| 17:54:39 | D1.4 | `/livez` 200 + `/readyz` 200 `reasons:[]`; engine back on `localhost:5432/ensemble_demo`; `.launcher-state` retains drill history (`crash_count=4`, no abort marker — window ages out naturally) |
| 17:55 | close | `.env` md5 unchanged; final live checkpoint byte-identical; demo healthy (launcher 93988 → 94003 → 94004) |

Evidence files: `/tmp/dr1-ev/` (status, env md5 before/after, launcher-state pre/cycle1/cycle2/restored, stop transcripts ×3, loop lines, live checkpoints ×4, log offset).

---

## 3. Root cause + findings needing triage (findings, not fixes)

**F-DR1-1 (root cause — blocks DR-1 as designed): the exit-75 boot preflight is unreachable on the deployed frozen-binary path.**
`_boot_db_preflight()` (exit-75 site, `daemon/__main__.py:104/:239`) runs only under the `python -m daemon` dev entry. The deployed launcher runs `current/ensemble-prod`, whose PyInstaller entry is `run_app.py` (`ensemble.spec` `Analysis(['run_app.py'])`) — **no preflight call anywhere in run_app.py**. Boot order on deployed installs: uvicorn lifespan → `manager.initialize` → `create_postgres_checkpointer` → psycopg `OperationalError` (refused = immediate, not a timeout budget) → uvicorn `Application startup failed. Exiting.` → **exit code 3** → launcher crash track (10→300s ×2, burst-budget-consuming). Consequence: the launcher's 75-track (implemented, unit-proven 74/74) is **dead code on every frozen-binary install for the PG-unreachability class**. DR-1's premise ("Daemon boot PG-preflight fails → exit 75") holds only for the dev entry. Triage options for the planner: invoke the preflight from `run_app.py` before uvicorn starts (and map uvicorn startup failure to 75 where the cause is DB-unreachable), or re-scope DR-1's trigger class.

**F-DR1-2: split-brain PG resolution in one process — `POSTGRES_URL` honored by the checkpointer, ignored by the repositories engine.**
`daemon/persistence.py` DSN chain: `POSTGRES_URL` > parts > config. `daemon/repositories/factory.py:189-193`: parts-only (`POSTGRES_HOST/PORT/DB/USER/PASSWORD`) — `POSTGRES_URL` never consulted. Observed live in one boot: engine line `localhost:5432/ensemble_demo` (real) while the checkpointer connected to `127.0.0.1:39417` (drill DSN). Also a doc-drift instance: the project blueprint's "Dev DB selection" note cites `factory.py:189-198` as part of the `POSTGRES_URL` precedence chain, which the code does not implement.

**F-DR1-3: runbook knob `DATABASE_URL` is read by nothing (primary and fallback both dead).**
Primary (ambient) and fallback (`.env` edit) both reference a key with no consumer in `daemon/`, `scripts/`, or either launcher copy. Working adapted knob used: ambient `POSTGRES_URL` (dominant chain step for the checkpointer). Note `daemon/tools/system.py` tracks `DATABASE_URL_POSTGRES` — a different, similarly-named key — and a `migration_worker.py:487` comment loosely claims "DATABASE_URL/POSTGRES_* overrides still work", perpetuating the confusion.

**F-DR1-4 (informational): `.launcher-state` retains drill history after a healthy restore boot** (`crash_count=4`, `window_start` = drill window). Launcher-owned state, ages out per its own window logic; no drill knob left behind. Recorded so the next drill's `crash_count` baseline is read *fresh*, not assumed 0/1.

**F-DR1-5 (informational): the runbook's launch shape leaves an undocumented `/bin/sh -c` nohup wrapper parent** next to the launcher. `stop-ensemble.sh` TERMs both correctly (observed twice). Friction only.

---

## 4. Live pid checkpoint table (read-only; zero live contact)

| Checkpoint | Moment | pid/ppid | lstart | Byte-diff vs start |
|---|---|---|---|---|
| start (§0.4) | 17:48Z, pre-drill | 31150 / 31130 | Sat Aug 22 10:04:07 2026 | — (baseline; matches DR-0 record) |
| #1 | 17:49Z, pre-stop | 31150 / 31130 | same | identical ✓ |
| #2 | 17:55Z, post-restore | 31150 / 31130 | same | identical ✓ |
| end (final) | 17:55Z, drill close | 31150 / 31130 | same | identical ✓ |

Listener resolve method (§0.4 as written): port read from the live install's own `.env`; `lsof -nP -iTCP -sTCP:LISTEN` filtered by that port string; only `ps` lines tee'd to evidence (cmdline carries no port literal); lsof lines sed-redacted to `<live-port>` before capture. **No signal, no HTTP, no write to live at any point.**

---

## 5. Constraint compliance

- **Live:** READ-ONLY throughout — `ps`, `lsof`, `.env` PORT read. Zero signals, zero HTTP, zero writes. Invariance proven by §4 (byte-identical ×4).
- **Demo:** mutations exactly the runbook DR-1 set — one clean stop, two drill-scoped launches (ambient env only), assert-then-SIGTERM stops, one restore launch. `.env` never edited (md5 identical); no `.env.drill-backup`; no pipeline/journal op (`status.sh` is read-only); closed probe port 39417 verified refused pre-use, never a real service.
- **PID discipline:** every stop preceded by the full 3-part assertion (cwd=demo install, lstart identity vs recorded start, no live-port resource + no trailing-slash-anchored live-path open files) — 4 assertions, 4 passes (69871; 87762/87760; 89830/89832). DR-0's recycling lesson applied; no bare-pid kill performed at any point.
- **Repo:** single artifact written (this file). `git status --porcelain` at close: ` M .agents/approver/active.md` (pre-existing, per DR-0 record, untouched) + this new file.
- **Port literals:** none for live in this file or any evidence file produced (redaction applied at capture time).

---

## 6. Operator friction log (T1-closure deliverable)

| runbook §/step | doc says | observed | classification |
|---|---|---|---|
| §2 D1.1 | `grep '^DATABASE_URL=' ~/agents-ensemble-demo/.env` records "the real demo DATABASE_URL" | Key does not exist in demo `.env` — PG config is `POSTGRES_*` parts (lines 34–38); grep returns nothing, nothing to record | **WRONG** (assumes a key the env shape doesn't have) |
| §2 D1.2 primary | ambient `DATABASE_URL='postgresql://…unreachable…'` → boot tempfail | Override inert: no reader of `DATABASE_URL` anywhere (repo-wide; deployed launcher). Daemon booted green on the real DB (`/livez` 200, engine line real DSN) | **WRONG** (dead knob — F-DR1-3) |
| §2 D1.2 fallback sentence | "fall back to: `cp .env .env.drill-backup` → edit `DATABASE_URL` in place" | Equally inert (same missing reader). Executing it would mutate shared `.env` for zero effect. **Deviation taken (recorded):** adapted the override to ambient `POSTGRES_URL` — the supported dominant chain key — keeping the primary path's best property (zero `.env` contact) | **WRONG** (dead knob; deviation was necessary and is disclosed here) |
| §2 D1.2 closed-port | "pick an unreachable LOCAL socket verified closed first (never a real service port)" | No guidance on candidate selection or how many probes. Used `nc -z` + `lsof` double-check on 39417 (refused + unlisted). Worked; under-specified | **AMBIGUOUS** |
| §2 D1.2/D1.4 launch lines | `(cd … && nohup ./launcher.sh … &)` | Leaves a `/bin/sh -c` wrapper parent pid alongside the launcher; undocumented process shape (stop script TERMs both fine — F-DR1-5) | **MISSING** |
| §2 expected outputs | "Daemon boot PG-preflight fails → **exit 75** (boot-time tempfail)" | No preflight exists on the frozen path; PG-refusal surfaces as uvicorn startup failure → **exit 3**, crash track (F-DR1-1). The entire DR-1 premise rests on this line | **WRONG** (root cause) |
| §2 D1.3 | `tail -f` the launcher log | Non-interactive executor: used offset-anchored `tail -n +N` + timed captures instead; equivalent evidence, but the doc implies an interactive terminal | **OK-but-confusing** |
| §2 D1.4 | Stop the looping launcher first (F5 correction), assert-then-SIGTERM per DR-0 protocol, restart only after TERM verified | Followed exactly; stop transcripts show TERM of wrapper+launcher before any relaunch. Worked as documented | clean |
| §2 D1.4 checks | `curl /livez; curl /readyz`; `diff` env before/after | Executed as written (both 200 `reasons:[]`; diff empty) | clean |
| §0.1/0.2/0.3/0.4/0.5/0.6 | Prereq checklist incl. baseline capture + triple + string discipline | All steps executed as written, all PASS; DR-0 re-run correctly not required (no drift found) | clean |

**Friction summary:** the runbook's §0 preflight discipline is solid; §2's *mechanism* (which env knob moves the daemon's PG target, and which exit code PG-failure produces on the deployed binary) is wrong at three linked points (D1.1 key, D1.2 knob ×2, expected-output exit code) — all downstream of F-DR1-1/F-DR1-3.

---

## 7. Verdict

`DR-1 FAIL: exit-75 tempfail path unreachable on the deployed frozen-binary boot — PG-unreachability yields uvicorn exit-3 on the crash track (burst-budget-consuming), not exit-75; 0 tempfail cycles observed, crash_count 1→4 in the attempt; demo restored green, live untouched`

Environment left healthy: demo daemon v0.10.5 green on 7979 (`/livez` + `/readyz` `reasons:[]`), `.env` bit-exact, no drill residue; live byte-identical to baseline at every checkpoint. Findings F-DR1-1…F-DR1-5 need triage before any DR-1 re-attempt — F-DR1-1 is the blocker (either wire the preflight into the frozen entry or re-scope the drill's trigger class).
