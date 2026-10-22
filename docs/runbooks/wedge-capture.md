# Runbook — the wedge-capture instrument

Commission: **wedge-capture evidence instrumentation** (incident 2026-10-22).
Plan set: `.agents/shared/planning/wedge-capture/`.

> **A capture is EVIDENCE, never a remedy.**
> This instrument **fixes nothing**. It observes. A capture tells you what
> the daemon SAW while it was wedged — it does not repair the wedge, and
> reading a capture never changes the daemon's behaviour. Any fix is a
> separate commission with its own review.

---

## 1. What is captured automatically

The instrument is **on by default**. A dedicated daemon thread samples once
per second **from outside the event loop** and fires on any of **three
independent triggers**.

### The three triggers

| Trigger | Condition | Default threshold | Default sustain |
|---|---|---|---|
| `sustained_cpu` | the PROCESS burns CPU | **95 % of ONE core** | **30 s** (wall-time) |
| `sustained_loop_lag` | the event loop is not being serviced | **5.0 s** of lag | **15 s** (wall-time) |
| `thread_concentration` | ONE thread holds the process's CPU | **85 %** of process CPU | **30 s** (wall-time) |

The sustain windows are **wall-time**, so the verdict is identical at a 1 s
and a 5 s tick cadence. The concentration ratio is only evaluated when the
process actually burned CPU (an absolute **5 %-of-one-core floor**) — an
unfloored ratio is trivially true on a near-idle daemon and would
manufacture false positives.

The cooldown is **300 s per trigger reason** (measured from the START of the
previous capture). A CPU episode and a lag episode inside one window are
**two different shapes** and produce two captures.

### The capture directory

One directory per capture, under the capture root:

```
<capture_root>/<UTC-ts>-<host>-<reason>-<uuid8>/
```

e.g. `20261022T050351Z-web01-sustained_cpu-3f2a91c4/`.

The capture root is `<data_dir>/wedge-captures`
(`ENSEMBLE_DATA_DIR` > `DATA_DIR` > `./data`), overridable with
`ENSEMBLE_WEDGE_CAPTURE_DIR`. It is **never** inside the repo and **never**
inside a release dir.

### The artifact file set (fixed)

| File | What it carries |
|---|---|
| `manifest.json` | the capture's identity: reason, source, epoch, host, per-artifact status, the executor's OS tid |
| `stacks.txt` | the all-thread Python stack dump (`faulthandler`) — **which frame** is burning |
| `threads.csv` | the per-thread CPU attribution: `tid, native_id, name, comm, cpu_delta_s, share_pct` |
| `lag.json` | the loop-lag histogram: `samples / max / p95 / p50 / overflow_count / loop_unresponsive_s` + the probe's freshness markers |
| `metrics.json` | GC stats, RSS, the cgroup memory line, and the **saver-pool counters** |
| `db.json` | the executing turns, the checkpoint-store census and the resume history |
| `readyz.json` | the readiness composite at capture time (**omitted** when the composite is not published yet) |
| `checkpoint_perf_tail.json` | the tail of the bounded slow-saver-op ledger |

`threads.csv` is the **H1-vs-H2 discriminator**: in an H1 shape one thread
holds ~100 % of the process CPU; in an H2 shape the CPU is quiet and the
**lag** is the signal.

### The metadata row

One row per capture in `wedge_captures` — a **path reference** plus a small
metrics snapshot. The payload lives on disk; the row is an index. If the
row write fails, the capture is still valid on disk and the reconciler
re-inserts it from the `.pending_row.json` sidecar.

---

## 2. The knobs, the kill switch, and the refusals

### The kill switch

```
ENSEMBLE_WEDGE_CAPTURE_ENABLED=0
```

With the kill switch off the watchdog **never starts**, no capture directory
is written, no row is written, and a **manual capture is refused** with the
typed reason **`watchdog_disabled`** (nothing is enqueued, no capture, no
row).

### All 15 knobs

| Knob | Default | What it does |
|---|---|---|
| `ENSEMBLE_WEDGE_CAPTURE_ENABLED` | `1` | the kill-switch |
| `ENSEMBLE_WEDGE_CAPTURE_DIR` | `<data_dir>/wedge-captures` | the capture root |
| `ENSEMBLE_WEDGE_CAPTURE_CPU_PCT` | `95` | the process-CPU threshold, % of ONE core |
| `ENSEMBLE_WEDGE_CAPTURE_CPU_SUSTAIN_S` | `30` | the CPU trigger's wall-time sustain |
| `ENSEMBLE_WEDGE_CAPTURE_LAG_S` | `5.0` | the loop-lag threshold, seconds |
| `ENSEMBLE_WEDGE_CAPTURE_LAG_SUSTAIN_S` | `15` | the lag trigger's wall-time sustain |
| `ENSEMBLE_WEDGE_CAPTURE_THREAD_CONCENTRATION_PCT` | `85` | one thread's share of the process CPU |
| `ENSEMBLE_WEDGE_CAPTURE_SAMPLE_INTERVAL_S` | `1.0` | the sampler cadence (minimum 0.2) |
| `ENSEMBLE_WEDGE_CAPTURE_COOLDOWN_S` | `300` | the per-reason cooldown |
| `ENSEMBLE_WEDGE_CAPTURE_DEADLINE_S` | `20` | the whole-capture wall-clock budget |
| `ENSEMBLE_WEDGE_CAPTURE_MAX_CAPTURES` | `20` | the retention count cap |
| `ENSEMBLE_WEDGE_CAPTURE_MAX_AGE_HOURS` | `168` | the retention age cap |
| `ENSEMBLE_WEDGE_CAPTURE_MAX_DISK_MB` | `512` | the retention disk cap |
| `ENSEMBLE_WEDGE_CAPTURE_SLOW_SAVER_MS` | `5000` | the stall-ledger cut |
| `ENSEMBLE_WEDGE_CAPTURE_OBJECT_CENSUS` | `0` | the O(heap) object census (off by default) |

**The lag/interval coupling.** `LAG_S` **must** be strictly greater than
`SAMPLE_INTERVAL_S`. A healthy daemon measures roughly **one tick** of loop
lag, so a lag threshold at or below the sample interval would fire on a
perfectly healthy daemon. A violating value falls back to the default with a
warning.

### The manual-capture refusals

| Token | When | What happened |
|---|---|---|
| `watchdog_disabled` | the kill-switch is off **or** the watchdog is stopped | nothing was enqueued; no capture, no row |
| `manual_rate_limited` | a **third** request inside one minute | refused **at enqueue** — nothing was queued |
| `capture_busy` | the depth-1 manual slot is already occupied | nothing was enqueued, **no 202**, and **no coalescing** — retry |

The manual-capture **rate cap is 2 per minute**, enforced in-process.

---

## 3. Evidence only — never repair

Read this twice.

* The instrument **observes**. It changes no behaviour of the daemon, no
  pool sizing, no checkpoint-store policy, no CPU sink, no scrub.
* A capture is a **diagnostic artifact**. It is not a remediation, not a
  restart, not a fix. Reading one tells you what happened; doing something
  about it is a separate decision with a separate change.
* The instrument **writes only into its own capture directory and its own
  table**. It never writes to production state.

---

## 4. Triage

### The CLI (a thin client over the admin API)

```
python3 scripts/wedge_capture.py list
python3 scripts/wedge_capture.py get <capture_id>
python3 scripts/wedge_capture.py capture
python3 scripts/wedge_capture.py status
```

The CLI is **stdlib-only** — it runs with no venv. It never opens a database
connection and never reads the capture directory directly.

The base URL is resolved as `--base-url` → `ENSEMBLE_DAEMON_URL` →
`DAEMON_PORT` → `PORT` → `http://127.0.0.1:9797`. The port is **never**
hard-coded.

Exit codes: `0` ok · `1` not-found · `2` usage · `3` transport error.

### The admin API

| Endpoint | Method | What it does |
|---|---|---|
| `/api/wedge-captures` | GET | the capture list (the table, with a filesystem fallback) |
| `/api/wedge-captures/status` | GET | the watchdog status + the effective auto-continue mode |
| `/api/wedge-captures/capture` | POST | **202** + the pre-allocated id — the capture is *enqueued* |
| `/api/wedge-captures/{capture_id}` | GET | one capture's full payload |

The read routes require the **`maintenance:read`** scope (auth-ON only; the
scope dependency is inert when auth is OFF). The POST additionally carries
the trusted-Origin guard.

### SQL

```sql
SELECT capture_id, captured_at, trigger_reason, auto_continue_mode,
       file_count, total_bytes, capture_dir
  FROM wedge_captures
 ORDER BY captured_at DESC
 LIMIT 20;
```

### Reading the artifacts

* **`threads.csv`** — sort by `share_pct`. One thread at ~85-100 % is the H1
  shape. A flat distribution with a high `lag.json` max is the H2 shape. The
  `comm` column names the thread (`MainLoopBridge`, `WedgeCaptureWatchdog`,
  a `psycopg` worker, …) even when the `native_id` mapping is wrong.
* **`lag.json`** — `max` / `p95` / `p50` are the lag distribution;
  `loop_unresponsive_s` is how long the loop was unresponsive at capture
  time; `last_probe_age_s` + `stale` / `never_fired` say whether the lag
  telemetry was still alive. **`never_fired` means the loop never answered
  the probe at all** — that is a different (and worse) shape than "the lag
  was small".
* **`metrics.json` → `pool`** — the saver-pool counters. `pool_checked_out`
  is **derived** (`pool_size − pool_available`); `pool_size_current` is the
  live size; `pool_max` is the ceiling; `pool_waiting_clients` is the only
  *satisfiable* waiting test. **`pool_checkout_timeouts` is always `None`** —
  `psycopg_pool` 3.3.1 does not expose it, and `pool_requests_errors` is a
  superset that must never be read as a timeout count. There is **no**
  `pg_stat_activity` fallback: the daemon's own connections carry an EMPTY
  `application_name`, so such a number would be fabricated.
* **`checkpoint_perf_tail.json`** — the slow saver ops (at or above the
  stall-ledger cut). Note the accuracy boundary: the **only** timed saver
  call site is the messages-API `aget` path, so this ledger **cannot** see a
  readiness-sentinel stall. Sentinel visibility comes from the
  `readyz.json` checkpoint-saver component plus the pool counters.
* **`db.json`** — the executing turns (`instance_id`, `work_id`, the
  heartbeat, the resume chain) and the checkpoint-store census.

### The effective auto-continue mode

The status surface reports `auto_continue_mode` ∈ {`on`, `off`, `unknown`}
together with the `boot_epoch` it belongs to. It comes from the **boot
record** — what the boot pass actually read at its boot — and is **never**
re-derived from the `.env` / unit / `set-environment` precedence chain. A
capture taken in epoch E reports **E's** mode even after a later boot
flipped it.

---

## 5. The manual-capture drill — **on a NON-LIVE host only**

> **Never drill on the live daemon.** A drill is a *capture*, and a capture
> on a live host is an unwanted incident artifact — it pollutes the very
> evidence store you would be reading during a real incident.

The drill is **ENQUEUE-THEN-POLL**:

```
# 1. submit — prints the 202's pre-allocated id + directory
python3 scripts/wedge_capture.py capture

# 2. read the outcome (the watchdog services the request on its next tick)
python3 scripts/wedge_capture.py list
python3 scripts/wedge_capture.py get <capture_id>
```

The submit command does **not** wait for the capture to finish, and there
is no `--wait` flag: re-run `list` / `get`.

The three refusals (`capture_busy`, `manual_rate_limited`,
`watchdog_disabled`) are **documented outcomes of the drill**, not
failures — the drill reports them verbatim and the operator retries.

---

## 6. Retention

| Cap | Default | What it prunes |
|---|---|---|
| count | **20 captures** | the OLDEST capture directories |
| age | **168 h** (7 days) | capture directories older than the cap |
| disk | **512 MB** | the oldest capture directories first |

**The directories are the authority, never the table.** A capture whose row
write failed is still pruned — the cap is computed by walking the capture
directory, so an orphan can never leak past the cap.

Two exclusions:

* **`boot-records/`** — the effective-mode carrier. It is **not** a capture
  and it is **never** pruned with the captures (pruning it would erase the
  mode the surviving captures cite). It has its **own 50-file cap**,
  prune-oldest, never the newest.
* **`.pending_row.json`** — a reconcile sidecar **inside** a capture
  directory. It is never treated as a capture and never deleted
  independently of its parent capture.

To disable the instrument entirely, use the kill switch. To prune manually,
delete capture directories under the capture root; the reconciler re-inserts
any orphaned row from its sidecar.

---

## 7. Residual limitations — what this instrument structurally CANNOT see

Read this before you over-trust a capture.

* **Pure-Python starvation only.** The instrument observes an event loop
  starved by *Python* code. A **C extension that holds the GIL without
  releasing it** starves the watchdog thread too — the capture cannot even
  start. A capture's absence is therefore NOT proof that no wedge happened.
* **`pg_stat_activity` cannot corroborate pool attribution.** The daemon's
  own connections carry an **empty `application_name`**, so there is no way
  to attribute a backend to the daemon's pool from the PG side. (The
  capture's **own** connection does set `application_name=wedge-capture`.)
  Tagging the daemon's connections is a **note-for-architect**, deliberately
  out of scope here.
* **The stall ledger cannot see a sentinel stall.** Only the messages-API
  `aget` path is timed; the readiness sentinel's `aget_tuple` is not. See
  §4.
* **`pool_checkout_timeouts` does not exist.** `psycopg_pool` 3.3.1 does not
  expose it. The capture reports `None` with an explicit reason rather than
  a plausible-looking number.
* **A missing row is not a missing capture.** The row write is
  best-effort; the files are the evidence. The LIST endpoint falls back to
  a filesystem scan.

### The PG connection budget

The capture opens its **own one-shot** connection — it never borrows the
saver pool (the resource it may be diagnosing). That connection consumes
`max_connections` headroom. The requirement is **≥ 2× the daemon's
connection demand + 1** (the daemon's demand is roughly 5 saver-pool + 5
asyncpg + the engine pool).

Measured on the host's PostgreSQL server (2026-10-22, the implementation
lane's fenced dev connection — ``max_connections`` is a SERVER-level
setting, so the value is the same for every database on that server):

```
max_connections = 100
current_connections = 6   (at measurement time)
```

The requirement is **≥ 2× the daemon's connection demand + 1**. The
daemon's demand is roughly 5 (saver pool) + 5 (asyncpg) + the engine
pool ≈ 12-15, so the requirement is ≈ 25-31 — **100 is comfortably
above it**. The capture's own one-shot connection therefore has
headroom, and one capture at a time cannot exhaust the server.

---

## Where the code lives

| Piece | Path |
|---|---|
| the instrument | `daemon/services/wedge_capture/` |
| the metadata table | `daemon/repositories/wedge_captures/` |
| the admin API | `daemon/routers/wedge_capture.py` |
| the agent tools | `daemon/tools/wedge_capture.py` |
| the operator CLI | `scripts/wedge_capture.py` |
| the migration | `daemon/migrations/versions/20261022_000002_create_wedge_captures.sql` |
| the plan set | `.agents/shared/planning/wedge-capture/` |
