# Lessons — hard-reboot P4 drill harness (2026-10-21)

Context: building the P4 acceptance-drill harness for the hard-reboot-recovery feature
(worktree `feature/hard-reboot-recovery`). Everything below was established empirically on this
host; each item cost a cycle to learn, so each is recorded to avoid paying it twice.

---

## L1 — The env-poison fence assertion must be evaluated INSIDE the scrubbed env

The ambient shell carries LIVE `POSTGRES_HOST=10.44.0.2` / `POSTGRES_DB=ensemble_prod` /
`ENSEMBLE_SELF_ENV=live` / a live password. Asserting the fence in the PARENT shell fires
spuriously (the parent still holds the poison), and asserting it in the parent also proves
nothing about the child that actually touches the DB.

Correct form — assertion runs in the scrubbed child, immediately before `exec`:

```bash
env -u POSTGRES_HOST -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u ENSEMBLE_SELF_ENV -u ENSEMBLE_UPGRADE_LIVE -u ENSEMBLE_RESTART_UNIT \
    sh -c '[ "${POSTGRES_DB:-}" != "ensemble_prod" ] || { echo "FENCE BREACH"; exit 1; }; exec "$@"' sh "$@"
```

**Self-test the fence.** The harness points the wrapper deliberately at `ensemble_prod` and
asserts it REFUSES (rc=1, "FENCE BREACH") before it ever reaches `exec psql`. A fence that is
never exercised is a fence that is never proven.

---

## L2 — Tool/bash children INHERIT THE LIVE DAEMON'S CGROUP

`/proc/<pid>/cgroup` of a tool child = `/system.slice/ensemble-main.service`. A drill or
boot-smoke launched from a tool child therefore sits INSIDE the live daemon's cgroup — a
cgroup-wide signal can hit the live daemon's process tree.

**Rule:** any daemon booted for a drill must escape into its own system-level transient unit
(`systemd-run --unit=<n> --wait --collect ...` → `/system.slice/<n>.service`), and isolation
must be **ASSERTED** by reading `/proc/<pid>/cgroup` before any signal — never assumed.
This is the same class as the 2026-10-21 boot-smoke isolation incident.

---

## L3 — systemd-run needs no sudo; `systemctl stop` does

- `systemd-run --unit=<n> --wait --collect …` — system-level transient, **no sudo** (`Slice=system.slice`).
- `systemctl stop <system-unit>` — **needs `sudo -n`**; without it: "Interactive authentication required."
- `journalctl -u <unit>` — **no sudo**.

Asymmetry matters for teardown design: a drill that only ever uses `--wait --collect` units
self-exits, but a long-lived drill unit needs `sudo -n systemctl stop`.

---

## L4 — `PORT` beats `DAEMON_PORT` when `config.yaml` is present

`load_config` copies the YAML `daemon:` section into the config dict and passes it as an
**init kwarg**; pydantic-settings gives init kwargs priority over env vars. So with the
shipped `config.yaml` present, `PORT`/`HOST` win and `DAEMON_PORT`/`DAEMON_HOST` are silently
IGNORED. Always set BOTH. The `PORT` default is 8088 — the ensemble self-system port — so a
drill that forgets this can land on a forbidden port.

---

## L5 — `dev.sh` is unusable for a drill (two independent reasons)

1. It launches with **`--reload`**: the reloader process respawns the worker child, so
   `kill -9 <pid>` kills the WRONG process and the "crashed" daemon silently respawns.
2. It **`source`s `.env`**, which RESTORES any ambient value `.env` defines — re-poisoning an
   env you carefully scrubbed.

Use the scrubbed shape from `test/packs/lcau_boot_smoke_test.sh` instead:
`unset SSL_CERT_FILE SSL_CERT_DIR`, explicit `POSTGRES_*` + `PORT`/`DAEMON_PORT`/`HOST`,
and `python -m uvicorn daemon.api:app --host 127.0.0.1 --port <p> --log-level info
--no-access-log --timeout-graceful-shutdown 10` (**no `--reload`**).

`POSTGRES_URL` MUST be plain `postgresql://…` — it is the CHECKPOINTER DSN; `+psycopg` breaks it.

---

## L6 — `data/checkpoints.db` is CWD-relative, NOT data_dir-relative

`data_dir` honours `ENSEMBLE_DATA_DIR`/`DATA_DIR`, but the SQLite **checkpoints** path comes
from `EnsembleConfig.sqlite.checkpoints_db` = `./data/checkpoints.db` — relative to the
**process CWD**. Setting `ENSEMBLE_DATA_DIR` alone does NOT redirect it. A drill whose
scratch CWD is not the daemon's CWD must either run with CWD = scratch or pre-seed an
`ensemble.json` carrying ABSOLUTE sqlite paths.

Corollary: a foreign CWD also changes where `import daemon` and the `agents/` definitions
resolve from — prove that resolution explicitly (`PYTHONPATH` + `ENSEMBLE_CONFIG=<wt>/config.yaml`)
before trusting a scrubbed boot.

---

## L7 — Daemon INFO logs emitted before the logging handlers are installed are DROPPED

`daemon/__main__.py main()` emits its INFO records (including the G6 boot-preflight success)
BEFORE `daemon.api` is imported and installs the handlers. With no handler bound, the root
logger falls back to `lastResort` (WARNING) and the INFO record is discarded — the
`__main__` logger produced ZERO records for its own INFO lines in the drill's journal.

Consequence: **the G6 preflight's success is invisible to operators** on the `python -m daemon`
/ frozen-binary entry points. The drill had to assert a different, stronger marker
(`Creating PostgreSQL engine: 127.0.0.1:5432/<db>`, which also proves it bound to the SCRATCH PG).
This is a **daemon-side observability defect** for operators, not a drill artifact — reported,
not fixed (daemon code was out of the tester's scope).

Lesson for any future marker-based observation: a marker that is ASSERTED but never EMITTED is
an observability gap, not a passing signal. Prove emission empirically (a stubbed probe that
reaches the code path) before claiming the marker is unobservable.

---

## L8 — Journal-marker polling can latch a STALE pre-crash marker (false-positive risk)

The drill polls `journalctl -u <unit>` for a marker string. The unit journal spans BOTH the
pre-crash boot AND the post-restart boot, so the poll can match the OLD boot's line and
declare "recovery observed" before the new boot has emitted anything.

Observed: reported `T_recovery` 15 s vs true 10 s (the stale `reset=0` line latched).

This is worse than imprecision — it can produce a **false positive on the single most important
assertion** (the post-restart boot ran the reset). Anchor every post-restart observation to the
post-restart boot: filter by the new daemon PID, by journal timestamp >= the restart instant,
or by a cursor/marker-count captured before the reboot.

Generalisation: **an observation anchored to a shared, append-only stream must be scoped to the
epoch it is meant to measure.** Any pre/post comparison over one journal, one log file, or one
stdout needs an explicit epoch anchor.

---

## L9 — A task-less RUNNING instance is invisible to BOTH recovery passes

The seeded instance had NO task row and NO LangGraph checkpoint. Consequence:
- the auto-continue pass roots on `task` → `candidates=0`;
- the task-less orphan sweep requires a CHECKPOINT → skips with "no checkpoint" → `candidates=0`.

So a drill seeded with only that shape proves *"the passes FIRE"*, not *"an instance was
RESUMED"*. The plan's P4 gate literally says "observe … all fire", so the run was not wrong —
but the evidence was weak, and the final acceptance lane needs a genuinely RESUMABLE instance
(task row + checkpoint) to claim P4-1.

How to get one without a real LLM: a **local mock OpenAI-compatible endpoint that STALLS** the
response, so the turn is still in flight at kill time — the graph then writes a real task row
and a real checkpoint, and the kill leaves exactly the state the reboot is supposed to recover.

Note the symmetry with D13: the task-less RUNNING ghost is an OUT-OF-SCOPE known defect
(critical note e82c89e8) and must be REPORTED, never failed. The two seeded instances in the
final harness are therefore labelled distinctly: `resumable` (task + checkpoint) vs
`taskless_ghost` (D13, reported only).

---

## L10 — StaleTaskRecovery cannot be observed inside a 5-minute pack cap

STR is a documented **boot+10min** backstop (`daemon/services/stale_task_recovery.py`, the
"boot+10min clamp" in the runbook RTO table). No pack with a 5-minute cap can observe it fire.
It is not required when the primary paths (auto-continue, MQ boot pass) succeed — that is the
design — but any "all mechanisms fire" claim must say STR was **not observed, by design**.

---

## L11 — `POST /api/instances` and `POST /api/schedules` mint their own identifiers

- `POST /api/instances` **ignores** a client-supplied `instance_id` and mints its own UUID.
  Keying fixture queries on the requested id silently matches 0 rows.
- `POST /api/schedules` mints its own `source_id` PK and stores the caller's id as the
  **LABEL** in `source_configs.name` (`scheduling_service: label=row.name`). Querying
  `WHERE source_id='<the label you sent>'` finds nothing.

Both are silent-zero-match failures that look like "the feature did not fire". Always read the
identifier back from the CREATE RESPONSE and use the returned value for every later query.

---

## L12 — `$!` after `wrapper_fn … &` is the WRAPPER SUBSHELL, not the exec'd server

Starting a background server via a *function* wrapper — `scrub … server_cmd &` — makes bash
fork a subshell to run the function; `$!` is that subshell's PID. If the wrapper `exec`s, the
PID is transferred; if it merely calls, the subshell may exit and the server is **orphaned**
with nobody tracking it. Symptom in this lane: run 1 leaked a bound port because the teardown
killed the wrapper subshell, not the listener.

**Rule:** for a process you must be able to kill later, start it with an in-place exec
(`env -i … server_cmd &` — `env` replaces itself with the server, so `$!` IS the server PID),
and belt-and-braces re-resolve the authoritative PID from the LISTENING SOCKET
(`ss -tlnpH` → the pid owning the port). Kill by exact PID, never by name pattern.

This is the same class as L2: on a shared host, an unowned process holding a port is not just
untidy — it is a live-surface hazard if the port ever collides with a forbidden one.

---

## L13 — A "PASS" from a pass that had nothing to do is not coverage

The drill's first green run reported `AutoContinue boot pass: ContinueResult(candidates=0,
scheduled=0)` and the orphan sweep `candidates=0`. Both markers were PRESENT, both passes
FIRED, and the run was green — yet nothing had been resumed, because the seeded instance had
no task row and no checkpoint. Every marker string the plan told us to assert on was there.

**Rule:** for any "X resumes/recovers Y" acceptance item, assert on the **count** of Y acted
upon (`candidates>=1`, `scheduled>=1`), never on the firing marker alone. A marker proves the
mechanism ran; only the count proves the mechanism had work and did it. When the fixture
cannot provide real work (e.g. no LLM), manufacture the minimal real state — here, a stalled
mock-LLM turn that writes a genuine task row and checkpoint before the kill.
