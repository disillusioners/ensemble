# P4 Drill Harness Spec — hard-reboot-recovery (hrr-20261021)

**Status:** HARNESS-PREP spec (authoring + dry-run plumbing proof). NOT the final acceptance run.
**Owner:** tester lane. **Implementation:** delegated to a worker; this file is the binding spec.
**Plan:** `.agents/shared/planning/hard-reboot-recovery/plan-overview.md` (P4 row), `phase1-plan.md` (AC6), `c4-lane6-ruling.md` (lane-6 scope narrowing), `architecture-recommendation.md` (A3/A7).
**Binding drill spec:** `docs/runbooks/hard-reboot-recovery.md` §6 — THE HARNESS MUST RECONCILE WITH IT, not invent a parallel procedure. §3 (PG drop-in) targets the LIVE unit and is NOT part of the drill.

---

## 0. Mandate

Build a repeatable drill harness and prove it executes end-to-end on THIS host. Deliver the plumbing facts the final acceptance run needs. Do NOT issue final acceptance verdicts.

**Acceptance-critical truth rule:** an unobservable acceptance item is a REPORT ITEM, never a silent PASS.

---

## 1. Hard fences (non-negotiable)

### 1.1 ENV-POISON fence (known incident class — 6 prior occurrences)

The ambient shell carries LIVE `POSTGRES_HOST=10.44.0.2`, `POSTGRES_DB=ensemble_prod`, `POSTGRES_USER=ensemble`, `POSTGRES_PASSWORD` (len 29), `ENSEMBLE_SELF_ENV=live`, `ENSEMBLE_UPGRADE_LIVE=1`.

**Fence form — the assertion MUST be evaluated INSIDE the scrubbed env** (asserting in the parent shell spuriously fires, because the parent still carries the poison vars):

```bash
env -u POSTGRES_HOST -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u ENSEMBLE_SELF_ENV -u ENSEMBLE_UPGRADE_LIVE -u ENSEMBLE_RESTART_UNIT \
    sh -c '[ "${POSTGRES_DB:-}" != "ensemble_prod" ] || { echo "FENCE BREACH"; exit 1; }; exec "$@"' sh "$@"
```

Every DB-touching command goes through this wrapper. The harness itself MUST export a scrubbed env file for the daemon process.

### 1.2 NEVER-TOUCHED SET

| Surface | Identity (recon 2026-10-21) |
|---|---|
| live main daemon | `ensemble-main.service`, `0.0.0.0:9797`, MainPID 528942, CGroup `/system.slice/ensemble-main.service` |
| live demo daemon | `ensemble-demo.service`, `127.0.0.1:7979`, pid 1147 (ensemble-prod), install `/home/nea/agents-ensemble-demo` |
| live installs | `/home/nea/agents-ensemble` (`current` → `releases/v0.26.3`), `/home/nea/agents-ensemble-demo` (`current` → `releases/v0.16.3`) |
| live DB | remote PG `10.44.0.2` / `ensemble_prod` — never connect, never write |
| local PG cluster | `16/main` on 127.0.0.1:5432 — ONLY the drill's own throwaway role+DB may be created/dropped there |
| installed units | `/etc/systemd/system/*` — never created, never edited, never stopped |
| port 8088 | ensemble self-system — never signal |
| other live units | `ensemble-install-snapshot.{service,timer}`, `ensemble-treecheck.{service,timer}` — never touch |

### 1.3 Live-shape path fence

Harness `HOME` MUST be a `mktemp -d` FAKE_HOME (verified empty of live-shaped paths). `scripts/stop-ensemble.sh:128-130` still carries the legacy `$HOME/agents-ensemble` shape — **the harness must never invoke `stop-ensemble.sh`**.

### 1.4 Signal fence

The harness may signal ONLY PIDs it started, and only after verifying that PID's cgroup is the drill's own transient unit (`/system.slice/<drill-unit>.service`). Never signal by name-pattern, never signal the daemon's process group blindly.

### 1.5 Isolation mandate (hard)

**Tool/bash children inherit the LIVE daemon's cgroup** (`/system.slice/ensemble-main.service`). The drill daemon MUST run inside its own system-level transient unit — `systemd-run --unit=<name> --wait --collect ...` → `/system.slice/<name>.service`. Isolation is asserted by reading `/proc/<pid>/cgroup`, not assumed. This is the 2026-10-21 boot-smoke isolation incident class.

### 1.6 No real host reboot

The reboot leg is **simulated**: full transient-unit stop + fresh start. A real host reboot is forbidden (user's live host).

---

## 2. Host facts (recon, 2026-10-21 11:31Z)

| Capability | Status | Exact working form |
|---|---|---|
| `systemd-run` system-level transient | **PASS, no sudo** | `systemd-run --unit=<n> --wait --collect /bin/sh -c '<cmd>'` |
| cgroup isolation proof | **PASS** | `systemctl show <u> -p ControlGroup --value` → `/system.slice/<u>.service`; `cat /proc/<pid>/cgroup` |
| `systemctl stop <system-unit>` | **needs `sudo -n`** | `sudo -n systemctl stop <u>` (no-sudo → "Interactive authentication required.") |
| `journalctl -u <unit>` | **PASS, no sudo** | `journalctl -u <u> -n 50 --no-pager` |
| local PG16 | **PASS** | `16/main` on 127.0.0.1:5432, 16.15, socket peer / TCP scram-sha-256 |
| superuser SQL | **PASS** | `sudo -n -u postgres psql -c "<sql>"` (passwordless) |
| throwaway role + DB | **PASS (proven + dropped)** | `CREATE ROLE … LOGIN PASSWORD …` + `CREATE DATABASE … OWNER …`; role can create schema/tables/`schema_migrations` |
| CREATEDB | not needed if DB pre-created by `postgres` + role is OWNER; else `ALTER ROLE <throwaway> CREATEDB` | never grant to a shared role |
| `ensemble_sandbox` DB | **DOES NOT EXIST locally** | use a purpose-named DB (e.g. `ensemble_hrr_drill`) |
| ports 9797 / 7979 | **IN USE (live)** | forbidden |
| ports 8079 / 8088 | free | 8088 forbidden anyway |
| ports 10000–10100 | **101/101 FREE**, bind+listen+echo proven | drill takes 2 (daemon + one spare) |
| FAKE_HOME fence | **PASS** | `mktemp -d /tmp/hrr-fakehome-XXXXXX`; empty of live-shaped paths |
| launcher INSTALL_DIR | resolves from the SCRIPT's own physical path, not `$HOME`; `ENSEMBLE_INSTALL_DIR` overrides | see §4.2 |

**Shell caveats:** `/bin/sh` is dash → `$RANDOM` is EMPTY (use `$$` / `date +%s`). `grep -w ":9797"` gives false negatives (`:` is not a word char) → use `grep -E ":9797[[:space:]]"`.

**Worktree is a MOVING TARGET:** a concurrent dev lane is active in `/home/nea/ensemble-src-wt-hard-reboot-recovery` (HEAD moved `4c888fbbb` → `3d7ad8d5`; `tests/helpers/hard_reboot_fixtures.py` was modified mid-recon). The harness MUST capture `git rev-parse HEAD` + `git status --porcelain` at boot AND at exit, and flag `TREE DRIFT DURING RUN` if they differ. The harness MUST NOT fail the drill on a dirty tree — it must record it.

---

## 3. Harness artifact

- **Location:** `test/packs/hard_reboot_drill_e2e_test.sh` (house pack dir + `<scope>_<type>_test` naming).
  - *Deviation from the plan's file-touch list (`tests/test_hard_reboot_e2e.py`):* disclosed — house pack convention + PACKS.md registration integrity require the pack dir. Record the deviation in the report.
- **Dual-layer timeout:** script self-timeout ≤ 300s (internal watchdog) AND external `timeout 300 bash <pack>`.
- **Output contract:**
  ```
  === Test Pack: hard_reboot_drill_e2e_test ===
  ...phase output...
  RESULT: PASS|FAIL|TIMEOUT
  ```
  exit `0` PASS / `1` FAIL / `124` TIMEOUT.
- **Estimate:** ≤ 4 min (PG setup ~5s, boot ~15–30s, kill+restart ~20s, observe ~30s, cleanup ~10s). If it threatens 5 min → shorten, never raise the cap.
- **Port:** daemon on a free port from 10000–10100 (dynamic probe, fallback 10007 then 10008). NEVER 8079/9797/7979/8088.

---

## 4. Phases

### P0 — preflight + fence (no side effects)
Assert: `POSTGRES_DB != ensemble_prod` inside scrubbed env; live daemon PIDs/ports unchanged; worktree HEAD+porcelain captured; port band free; `systemd-run` available.

### P1 — scratch PG (local 16/main only)
`sudo -n -u postgres psql -c "CREATE ROLE <r> LOGIN PASSWORD '<p>';"` → `CREATE DATABASE <db> OWNER <r>;` → verify TCP connect as `<r>` works (pg_hba: TCP 127.0.0.1 = scram-sha-256, so a password is REQUIRED). Unique names via `$$`/`date +%s`. Teardown: `DROP DATABASE` then `DROP ROLE`, verified gone.

### P2 — scratch env + CWD + port
- FAKE_HOME = `mktemp -d`; scratch CWD = `mktemp -d`.
- **Scrubbed boots write CWD data/logs** → the scratch CWD must absorb `data/`, `data/logs/`, `data/ensemble.json`, `data/launcher.err.log`, and any `.launcher-state`.
- Env file for the daemon, containing ONLY: `ENSEMBLE_SELF_ENV=dev|sandbox`, `POSTGRES_HOST=127.0.0.1`, `POSTGRES_PORT=5432`, `POSTGRES_DB=<scratch>`, `POSTGRES_USER=<role>`, `POSTGRES_PASSWORD=<role pw>`, the drill port, plus whatever the daemon needs to boot (no live credentials — never copy the ambient live password).
- The daemon must resolve `daemon/`, `agents/`, `config.yaml` from the worktree while writing data/logs into the scratch CWD. **The author must establish the actual resolution mechanism from the source and prove it works — do not guess.** (`launcher.sh` resolves INSTALL_DIR from its own physical path, so a scratch copy of `launcher.sh` makes INSTALL_DIR the scratch dir; `ENSEMBLE_INSTALL_DIR=<scratch>` overrides.)
- **CWD gotcha:** `data/checkpoints.db` is **CWD-relative** (`./data/checkpoints.db`), NOT data_dir-relative — `ENSEMBLE_DATA_DIR` alone does NOT redirect it. Either run with CWD = scratch, or pre-seed an `ensemble.json` with ABSOLUTE sqlite paths. The scratch CWD must absorb: `data/ensemble.json`, `data/ensemble.json.tmp`, `data/instances.db`, `data/checkpoints.db`, `data/logs/ensemble.log` (+5 rotations), `data/reports/`, `data/tmp_images/`, `data/vscode-server.pid`.
- If CWD = scratch, `import daemon` + the `agents/` definitions must still resolve from the worktree (PYTHONPATH / `ENSEMBLE_CONFIG=<worktree>/config.yaml`). **Prove this in the P0–P2 smoke**; if agents/ cannot resolve from a foreign CWD, report it and use the alternative (CWD = worktree + absolute data paths + scratch DATA_DIR) rather than improvising.

### P3 — isolated boot
Start the daemon in a system-level transient unit (`systemd-run --unit=<drill-unit> --wait --collect ...`), then **assert isolation** by reading `/proc/<daemon-pid>/cgroup` and requiring `/system.slice/<drill-unit>.service` (reject `/system.slice/ensemble-main.service`). Capture the boot banner, `/livez` 200, `/readyz` 200, and the daemon PID.

**Boot recipe — use the house shape from `test/packs/lcau_boot_smoke_test.sh:269-294` (read it).** `dev.sh` is FORBIDDEN for the drill: it launches with `--reload` (the reloader respawns the child → `kill -9` would hit the WRONG pid) and it `source`s `.env` (which RE-POISONS the env).

- `unset SSL_CERT_FILE SSL_CERT_DIR` first.
- `POSTGRES_URL` MUST be plain `postgresql://…` (NO `+psycopg`) — it is the CHECKPOINTER DSN (`daemon/persistence.py:145`).
- **PORT PRECEDENCE:** with the shipped `config.yaml` present, `PORT`/`HOST` WIN and `DAEMON_PORT`/`DAEMON_HOST` are IGNORED (pydantic-settings init kwargs beat env vars). **Set BOTH.**
- `--host 127.0.0.1 --port <drill port> --log-level info --no-access-log --timeout-graceful-shutdown 10`, NO `--reload`.
- Auth is OFF by default (`config.yaml:129-130`) → `scope_required(...)` is inert → REST routes callable without a token. Verify at runtime; add a token path only if it is actually enabled.
- **The boot epoch is a PROCESS-GLOBAL, not a DB column** — there is no table/column for it. The drill MUST parse it from the log line `Boot epoch captured (DB clock): <iso>`.

### P4 — seed the pre-crash state
- A RUNNING instance (created via the real API surface — see recon-code sheet).
- A schedule (survival observation).
- A **fresh-dead stranded MQ row**: a `message_queue` row with `status='processing'` whose timestamp is older than the boot epoch (the P2 reset's age predicate). Use the exact columns/values from `tests/unit/test_message_queue_boot_recovery.py` + `tests/helpers/hard_reboot_fixtures.py` — REUSE those helpers, do not reinvent them.
- Record the pre-crash snapshot: instance status, schedule row, MQ stats (`processing_count`/`pending_count`).

### P5 — hard kill
`kill -9 <daemon-pid>` (PID verified in the drill's own cgroup first). No clean shutdown.

### P6 — simulated reboot
Full transient-unit stop (`sudo -n systemctl stop <drill-unit>`) then a FRESH `systemd-run` start of the same drill unit name (new PID, new boot epoch). This is the "reboot" leg.

### P7 — observe
Assert (log strings from the runbook §6 / feature code — the author resolves them exactly from source):
- `Boot preflight: PostgreSQL reachable` (G6 preflight fired)
- `DependencyBus started`
- `AutoContinue boot pass:` (AutoContinue fired)
- `[HardReboot] MQ boot pass: reset=<n>` (P2 fired; n ≥ 1 given the seeded fresh-dead row)
- MQ stats post-boot, read via **SQL on the SCRATCH DB** (REST `GET /api/instances/{id}` exposes only `pending_count`, never `processing_count`):
  - `processing_count == 0` — **holds ALWAYS after the reset** (the reset flips `processing` → `ready`), independent of `QUEUE_DISCARD_ON_STARTUP`. This is the A3 assertion.
  - `pending_count == 1` — the reset MOVES the row from processing into pending; it does **not** make the target idle.
  - Busy-guard predicate `(pending_count > 0 OR processing_count > 0)` evaluates **True** post-reset → the guard does NOT clear on the shipped default (`QUEUE_DISCARD_ON_STARTUP=false`). It clears ONLY with the `true` override (which wipes the doomed pending rows). **P4-7 (busy-guard clears) is the override-gated item — NOT `processing_count == 0`.** The drill must label WHICH branch it observed.
  - Pin: `tests/unit/test_message_queue_boot_recovery.py:193-252` (M5) — pre 1/0 → post 0/1, predicate True.
  - The busy-guard is reachable only via the AGENT TOOL lane (`daemon/tools/instance.py:4200`); the REST send path has its own routing. Assert the PREDICATE via SQL counts, not the guard message.
  - Counts: `processing_count` = `status='processing'`; `pending_count` = `status='ready'` OR (`status='retrying'` AND `next_retry_at <= now`).
- schedule row survived (same row, still scheduled)
- instance resumed (RUNNING → resumed/terminal per AutoContinue)
- `/livez` + `/readyz` 200 on the restarted daemon

### P8 — cleanup (zero residue)
Stop the transient unit (`sudo -n systemctl stop`), drop the scratch DB + role, remove the scratch CWD + FAKE_HOME, free the port, verify: no `hrr-*` units, no scratch role/DB rows, port free, live daemon PIDs + `NRestarts` + `ActiveEnterTimestamp` UNCHANGED, live symlink target unchanged.

---

## 5. Timing instrumentation → AC6 (≤ 30s)

The AC6 criterion is *"boot against a freshly-rebooted host, PG started by systemd in parallel, recovery completes ≤ 30s"*. The drill MUST instrument and report, separately:

1. **T_restart** = transient-unit stop → fresh start issued.
2. **T_readyz** = fresh start → `/readyz` 200.
3. **T_recovery** = fresh start → the recovery markers observed (AutoContinue pass completed + MQ boot pass completed).
4. **T_end_to_end** = T_recovery − T_restart.

**Report T_end_to_end against the ≤30s criterion, and state exactly which AC6 sub-conditions the drill DOES and DOES NOT reproduce** (see §7). Do not let a green T_end_to_end silently stand in for the full AC6 leg.

---

## 6. Evidence bundle

The harness writes a machine-readable evidence bundle (JSON + raw logs) under a run dir, e.g. `/tmp/hrr-drill-<ts>/`:
`preflight.json`, `pg_setup.log`, `boot.log`, `journal-<unit>.log`, `seed.json`, `pre_crash.json`, `post_restart.json`, `timings.json`, `cleanup.json`, `tree_state.json` (HEAD+porcelain at boot and exit).
Print the bundle path in the pack output. `RESULT:` line stays the last line.

---

## 7. Blind spots — MUST be reported explicitly (never a silent pass)

1. **No real host reboot.** The reboot leg is unit stop + fresh start. Kernel-level power-loss semantics (e.g. PG WAL replay on a cold cluster, filesystem journal replay, fsync-loss of in-flight writes) are NOT reproduced. A `kill -9` of the daemon is not a host power-loss.
2. **PG is NOT started by systemd in parallel with the daemon.** The local PG16 cluster is already running. The "PG started in parallel" AC6 race (daemon starts before PG accepts connections → preflight exit-75 → backoff) is NOT reproduced by this drill.
3. **The transient unit is not the production unit.** `After=postgresql.service` ordering, `Restart=`, `RestartSec=10` semantics from `scripts/systemd/ensemble-daemon.service` are only partially mirrored. The G6 ordering fix itself is verified by the unit-template grep + the P1 `connect_timeout` pack, not by this drill.
4. **D13 — task-less RUNNING ghost instances are OUT OF SCOPE** (critical note e82c89e8). The drill must NOT treat a task-less RUNNING instance as a failure; it should REPORT its presence as an out-of-scope observation.
5. **Lane-6 healing does not ship** (c4-lane6-ruling.md narrowed the P2 claim) — the drill must not assert lane-6 recovery.
6. **Busy-guard-clears (P4-7) is override-gated** — only meaningful with `QUEUE_DISCARD_ON_STARTUP=true`; on the shipped default (`false`) the expected outcome is the wedge transformed to visible-pending, and the busy-guard NOT clearing. The drill must run/report BOTH branches if it can, and label which branch it observed.
7. **Tree drift** — a concurrent dev lane mutates the worktree; the drill observes a moving target unless HEAD is pinned.
8. **LLM-dependent paths** — any observation that requires a real model call (an instance actually resuming a turn) may be non-deterministic or blocked by LLM availability; the drill must degrade to "structural observation" and say so.

---

## 8. Author's obligations

- Implement ONLY the harness file(s) under `test/packs/`. Do NOT modify daemon source, agents/, config.yaml, or `.agents/tester/rules/`.
- READ `docs/runbooks/hard-reboot-recovery.md` §6 FIRST and reconcile; the runbook is binding.
- Reuse `tests/helpers/hard_reboot_fixtures.py` helpers.
- Resolve every API/column/env name from the actual source — never guess. Report any item you could not resolve as `NOT FOUND`.
- The harness must be **re-runnable** (unique names per run, full teardown even on failure — use `trap`).
- **COMMIT REQUIRED** before reporting: `test: add hard-reboot P4 drill harness` — include the commit hash in the report. Do NOT push.
- Quick-fix authorization: YES for harness-file fixes < 20 lines; NO for daemon/source changes (report instead).

---

## 9. Report obligations (the final answer to the leader)

(a) does the drill execute end to end on this host — yes/no + the exact command sequence
(b) environment blockers (systemd-run permissions, port allocation, PG role creation, journald access, sandbox DB provisioning) with exact error text
(c) measured timings for the recovery leg + what the AC6 ≤30s criterion will be measured against
(d) what the drill CAN vs CANNOT observe (blind spots, §7)
(e) harness location + how to run it
(f) confirmation that the live daemon (:9797), the live install, and `ensemble_prod` were never touched
