# C-1 — the fenced-dev runtime observation (fix cycle 1)

**Commission:** central-metrics-v1 fix cycle 1 · the C-1 blocking fix
**Executor:** the coder (developer lane) · **Date:** 2026-11-02
**Worktree:** `/home/nea/ensemble-src-wt-central-metrics-v1` ·
**Branch:** `feature/central-metrics-v1` · **HEAD at the observation:**
`61e5c6b30` (the C-1 fix + the mutation-verified pins)

**WHY THIS FILE EXISTS.** The code council established C-1 **statically**
("the runtime source bundle is a permanent snapshot taken before the
handles exist"). The council's own honest gap was that nobody ever
observed a boot. This file is the observation: a fenced-dev daemon on
`:8079`, read through the **real locator path** (no `bind_sources`), with
the commands, the outputs, the numbers and the verdict.

---

## 1. The fence (verified BEFORE the boot — the house rule)

| Check | Result |
|---|---|
| the install-root / journal derivation resolves **inside** the worktree | `_resolve_install_dir("dev")` → **`None`** ⇒ **no journal path at all** ⇒ the boot cannot write the live `releases/state.json` |
| the boot sweep is pinned **explicitly** | `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` exported (the ambient `=0` guard is stripped by `env -u`; the sweep kills live processes when the var is absent) |
| the sweep's lane default | `is_prod_lane()` → **False** (source-mode `uvicorn daemon.api:app`), `kill_switch_from_env()` → **False** ⇒ a complete no-op |
| the ambient environment is scrubbed **per variable** | `POSTGRES_{DB,USER,PASSWORD,HOST,PORT,URL}`, `PLANE_MCP_API_KEY`, `ENSEMBLE_UPGRADE_LIVE`, `ENSEMBLE_UPGRADE_SCRIPTS_DIR`, `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART`, `PORT`, `DAEMON_PORT`, `DATA_DIR`, `ENSEMBLE_DATA_DIR` — `env -u POSTGRES_*` is a **silent no-op** (bash does not glob env names) and was NOT used |
| the port | `PORT=8079` / `DAEMON_PORT=8079`; the metrics listener was moved to **9799** (the live daemon holds 9798) |
| the database | `SELECT current_database()` → **`ensemble_dev`** on `127.0.0.1:5432` (the live daemon's prod DB is on a **remote** host, `10.44.0.2`) — verified **before** the boot |
| the live daemon | untouched — `:9797` / `:9798` / `:7979` are the live/demo processes, never contacted |
| the Plane workspace | `PLANE_MCP_API_KEY` scrubbed ⇒ the plane MCP booted **disabled** ("Builtin 'plane' skipped — missing environment configuration"); the plane sync watchdog logged "not configured … no re-drives" |
| the sampler | **read-only** — the census + the DB-backed adapters SELECT; the observation's only writes were to the dev DB's own boot-time schema |

**Boot command** (the whole fence, verbatim):

```bash
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT POSTGRES_URL
unset PLANE_MCP_API_KEY ENSEMBLE_UPGRADE_LIVE ENSEMBLE_UPGRADE_SCRIPTS_DIR
unset ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART PORT DAEMON_PORT DATA_DIR ENSEMBLE_DATA_DIR
set -a; . ./.env; set +a          # the WORKTREE's fence .env: ENSEMBLE_SELF_ENV=dev + the DEV db
export PORT=8079 DAEMON_PORT=8079 LIMITS_METRICS_LISTENER_PORT=9799
export DATA_DIR=./data_dev ENSEMBLE_DATA_DIR=./data_dev PERSISTENCE_DB_PATH=./data_dev/instances.db
export ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0
export ENSEMBLE_METRICS_ENABLED=1        # the observation knobs (see §3)
export ENSEMBLE_AUTH_ENABLED=true ENSEMBLE_AUTH_BOOTSTRAP_TOKEN=… ENSEMBLE_AUTH_CLIENT_ID=…
export ENSEMBLE_AUTH_CLIENT_SECRET=… ENSEMBLE_AUTH_ISSUER=https://127.0.0.1:8079
export ENSEMBLE_AUTH_ALLOWED_ORIGINS=http://127.0.0.1:8079 ENSEMBLE_AUTH_SCOPES="openid,profile,email,maintenance:read"
exec .venv/bin/python -m uvicorn daemon.api:app --host 127.0.0.1 --port 8079 --log-level info --no-access-log
```

**The two observation knobs, and why they are needed.** The query door
is **fail-closed when auth is OFF** (`503 AUTH_NOT_CONFIGURED` — the
correct posture), and the exposition listener serves a valid empty body
while the metrics gate is OFF. So the observation boots with the metrics
gate **ON** (the census samples and the exposition renders) and with
**auth ON** plus a locally minted bearer token
(`python -m daemon.auth.cli issue-token --label c1-observation --ttl 3600
--scopes "maintenance:read,instances:read"`), so the door can be read as
an operator would read it. The token is a **dev** artifact; the
bootstrap material was random per boot.

**Health at the observation:** `livez` → `{"status":"alive","version":"0.28.5"}`;
`readyz` → `200`, `status: ready`, all four components true,
`reasons: []`, `grace_active: false`, `leak_signal.detected: false`.

---

## 2. THE C-1 EVIDENCE — the dark→live transition, OBSERVED

The C-1 ⑤ probe (the resolved bundle's handle presence) logged **twice**
in the observed boot — once at the sampler's **first tick** (the lifespan
had not assigned the producer handles yet) and once at the **next tick**
(after the lifespan continued). The presence map **changed**, which is
itself the proof that the bundle is rebuilt per resolution.

```
14:46:15 - daemon.services.metrics_query.wiring.sources - INFO - [Metrics] runtime source bundle resolved:
  present=census,install_dir,mcp_snapshot,readiness_composite,resource_monitor,sweeps
  absent=db_connect,dsn,manager,mcps_gateway_service,wedge_watchdog          ← DARK

14:46:25 - daemon.services.metrics_query.wiring.sources - INFO - [Metrics] runtime source bundle resolved:
  present=census,dsn,install_dir,manager,mcp_snapshot,mcps_gateway_service,readiness_composite,resource_monitor,sweeps,wedge_watchdog
  absent=db_connect                                                          ← LIVE
```

**The handles that flipped ABSENT → PRESENT:** `dsn` (the DSN — the
handle 7 of the 10 dark families depended on), `manager`,
`mcps_gateway_service`, `wedge_watchdog`. `db_connect` stays absent by
design (it is the test-injection seam).

**THE `install_dir` FLAG IN THE LINES ABOVE IS THE PRE-ND-5 SEMANTICS**
(the delta-3 review's rider ND-5, fixed after this observation was
taken): the flag then reported the presence of the install-dir
**RESOLVER CALLABLE** — which the bundle always carries — instead of
the **RESOLVED PATH**'s presence.  So the observed lines above claim
`present=…install_dir…` even though **no install dir resolves in the
dev lane** (the fence resolves `_resolve_install_dir("dev")` to
`None` — §1).  ND-5 fixed the flag to report the resolved path, so a
re-run of the same observation would read **`absent=…install_dir…`** in
the first line and `present=…install_dir…` only in a lane that HAS an
install dir.  The DARK→LIVE TRANSITION this section exists to prove is
unaffected: it is `dsn` / `manager` / `mcps_gateway_service` /
`wedge_watchdog` that flipped, and none of them is `install_dir`.

**On the pre-fix code the second line would never have appeared**: the
bundle was cached by `id(app.state)`, `app.state` is never replaced, and
the first tick's bundle (every handle `None`) would have been served for
the life of the process. The probe's own change-detection is the
runtime proof that the cache is gone.

---

## 3. The door reads (the REAL locator path — no `bind_sources`)

`GET /api/metrics/families` → **HTTP 200**, 28 627 bytes,
`truncated: false`, no cursor. The auth middleware logged
`[AuthAudit] event=bearer_validated subject=c1-observation path=/api/metrics/families`.

### 3.1 The lane's 16 admitted families — the re-derived counts

| family | verdict | live series | availability | why |
|---|---|---|---|---|
| F0.3 | IN | 6 | **live** | the resource spine (WS-A) |
| F1 | IN | 9 | dark · `capture_off` | see §4.2 |
| F3 | IN | 0 | dark · `capture_off` | see §4.2 |
| F4 | IN | 5 | **live** | the pool native quantities |
| F5 | IN | 3 | **live** | the pool derived quantities |
| F6 | IN | 15 | dark · `census_gated` | see §4.1 — **the reason is INVERTED** |
| F7 | PARTIAL | 0 | dark · `series_absent` | see §4.3 — the observation **did** fire |
| F8 | PARTIAL | 4 | **live** | the checkpoint byte gauges |
| F9 | IN | 13 | **live** | the readiness family |
| F10 | IN | 3 | **live** | the job-queue depth |
| F11 | PARTIAL | 15 | **live** | the worker-pool gauges |
| F12 | IN | 3 | **live** | the sweep counters (3 of the 6 declared lanes — §5) |
| F13 | PARTIAL | 11 | **live** | the instance census |
| F17 | IN | 12 | **live** | the MCP health family |
| F18 | PARTIAL | 0 | dark · `series_absent` | the MCPS-gateway store is empty in dev |
| F21 | PARTIAL | 0 | dark · `series_absent` | the journal family needs an install dir; **dev has none by design** (the fence — §1) |

**RE-DERIVED COUNTS (the honest measurement, D-a):**

* **LIVE: 10 of the 16** admitted families.
* **DARK: 6 of the 16** — each with an explicit reason.
* **TOTAL live series across the lane families: 99.**
* The council's expectation was "16/16 or the re-scoped count after the
  Q1 verdict adjustment". **The re-scoped count is 10/16** — and **NO
  family is dark because of the C-1 locator**: every dark family has a
  named, non-C-1 cause (§4). The C-1 defect was worth **seven
  dsn-gated adapters** — **F0.3, F3, F8, F10, F11, F13 and F18** (the
  seven whose adapters read `sources.dsn`; **F4/F5 carry no dsn
  gate** — they were handle-dark — and **F6 was never dsn-dark**). Of
  those seven, **five are live now** (F0.3, F8, F10, F11, F13); **F3
  and F18 are still dark**, for their own non-C-1 reasons
  (`capture_off` and `series_absent` — §4).

### 3.2 The other two routes

* `GET /api/metrics/snapshot` → **200**, **112 series** over 41 distinct
  metric names, `truncated: false`.
* `GET /api/metrics/series?window_s=600` → **200**, **104 series**,
  **335 points**, `window.interval_s = 15.0`,
  `interval_s_source = "observed"`, `stale: false`, `truncated: false`.

### 3.3 The F7 read path (the W-1 gate, in the real path)

`GET /api/instances/{id}/messages` (a dev instance, HTTP 200, `[]`) drove
the saver-op seam. The exposition then carried the observation:

```
ensemble_checkpoint_op_bytes_count{op="aget"} 1
ensemble_checkpoint_op_bytes_sum{op="aget"} 0
ensemble_checkpoint_op_bytes_bucket{le="1024",op="aget"} 1
```

So the W-1 gate is **ON by default** (the switch unset ⇒ enabled) and the
observation fires in the real path. The byte count is `0` because the
thread had no checkpoint (a MISS is a read with zero bytes).

---

## 4. The three findings the observation surfaced (none is a C-1 defect)

These are **pre-existing / adjacent** defects that no static gate could
have found — they are exactly what the council's "C-1 was established
statically, never from an observed boot" gap was about. **None is in the
lane's own file set**, so none was fixed here; each is routed below.

### 4.1 F6's availability reason is INVERTED (the census's `gated` property)

* **Anchor:** `daemon/services/metrics_census.py:323-335` (`gated`) and
  `:347-355` (`_gate()`), consumed by
  `daemon/services/metrics_query/provider.py:103-109` (`census_enabled`).
* **The defect:** `gated` is documented as *"whether the census
  sampler's DB work is currently gated OFF"*, but it returns `_gate()`,
  which returns `metrics_gate.metrics_enabled()` — **True when the
  census's DB work is ALLOWED** (`collect()` runs `if self._gate():`).
  So the property is the **inverse** of its own contract.
* **The observed consequence:** with the switch ON, the census published
  **15 live series** and the door still reported F6 as
  `dark / census_gated`; `activation.census_enabled` is false for the
  same reason. The lane's own switch-invariance pack
  (`metrics_query_switch_invariance`) pins the *documented* polarity
  (`gated=True` ⇒ dark `census_gated`) using a fake census — so the
  production census and the lane's contract disagree.
* **Why it was not fixed here:** `daemon/services/metrics_census.py` is
  the metrics-collection P0 lane's surface (the lane's F6 seam), not the
  central-metrics lane's file; the query package must keep reading the
  PUBLIC property (the "the switch is consulted at exactly two sites"
  invariant — AM-13/AM-18/D-11b). Inverting the provider's read instead
  would break the lane's own frozen switch-invariance contract.
* **ROUTING (for the leader):** one-line polarity fix in the census's
  `gated` property (or in `_gate()`'s call site) — owner: the
  metrics-collection lane.

### 4.2 F1/F3 are permanently dark: the provider's `capture_active` is never wired

* **Anchor:** `daemon/services/metrics_query/provider.py:70/:78/:134`
  (`capture_active`) — `daemon/api.py:2528-2533` constructs the provider
  with `census` / `ring` / `listener_enabled` / `sampler_interval_s`
  **only**; `capture_active` keeps its `False` default.
* **The observed consequence:** the wedge-capture family F1 published
  **9 live series** and the door still reported it as
  `dark / capture_off`. F3 published 0 (its capture is genuinely idle
  in dev) but carries the same reason.
* **Why it was not fixed here:** the wiring site is `daemon/api.py` — the
  lane's **Tier-2 mechanical file**, which the council's C-1 ⑥ forbids
  the lane from touching at fix-cycle time.
* **ROUTING (for the leader):** pass the capture-active fact into the
  provider at the api.py construction site (or derive it from the
  watchdog's state) — owner: the mechanical-file lane.

### 4.3 F7's `live_series_count` is always 0: a non-internal histogram never publishes

* **Anchor:** `daemon/checkpoint_metrics.py:830-874` — the **non-internal**
  histogram `observe()` path sets `_dirty` but **never calls
  `_maybe_publish_locked()`** (only the `internal` branch at :851 does).
  So `published_snapshot()` stays `None` for every non-internal histogram,
  and the door's `live_series_count` is 0 for histogram families.
* **The observed consequence:** F7 (`ensemble_checkpoint_op_bytes`, the
  lane's only histogram family) reported `live_series_count = 0` while the
  exposition carried the observation (`_count{op="aget"} 1`) — the metric
  IS live; the door's family count cannot see it.
* **Why it was not fixed here:** the registry's publish path is the
  canonical registry's surface (F0.2), not the lane's; making F7
  `internal=True` would change the exposition shape (and the frozen
  contract's series census) — a contract decision, not a fix-cycle edit.
* **ROUTING (for the leader):** either the registry publishes
  non-internal histograms on the writer path, or the lane's family count
  reads the histogram's live state for histograms.

---

## 5. Q1 — `stale_pause_sweep` (the leader's ruling): the facts and the verdict adjustment

**The fact.** `stale_pause_sweep` is **never assigned to `app.state`**:
`daemon/api.py:3058-3074` calls `sweep_stale_pauses(...)` inline on the
lifespan loop and never publishes the service; the service instance
lives at `manager._stale_pause_sweep` (`daemon/services/pool_orchestrator.py:341-345`).
The other five sweep services **are** published
(`app.state.eligible_pending_sweep` :1613 · `orphan_watcher_sweep` :1664 ·
`job_lock_sweep` :1708 · `watch_reconcile_sweep` :1757 ·
`upgrade_journal_sweep` :3139).

**The measured consequence (this is the load-bearing part).** The
snapshot's F12 sweep lanes published in the observed boot were exactly:

```
['eligible_pending', 'orphan_watcher', 'watch_reconcile']
```

— i.e. **3 of the 6 declared lanes**, and the three that publish are the
three whose services expose a **`counters()` reader**. The other three
(`job_lock`, `upgrade_journal`, `stale_pause`) expose **no `counters()`
method at all** (`grep -n "counters" daemon/services/{job_lock,upgrade_journal,stale_pause}_sweep.py` → no hits), so the F12 adapter skips them
(`getattr(service, "counters", None)` is not callable ⇒ `continue`).

**THE VERDICT ADJUSTMENT (the leader's (a), applied honestly):**

* **NO family verdict changes.** F12's verdict stays **IN**: its seam
  ("the sweep services' `counters()` readers") is satisfied by the three
  readers that exist, and the contract's DISCLOSED SHORTFALLS section
  already records that "the seven RDRS lanes + **three standalone
  sweeps that expose no `counters()` reader** render no series (the
  vocabulary stays the closed 13-lane set)".
* **The stale_pause lane is DOCUMENTED-DARK for TWO independent
  reasons**, and only the second one is the app.state gap:
  1. the service exposes **no `counters()` reader** ⇒ it would render no
     series **even if it were published**;
  2. the service is **not published on `app.state`** ⇒ the locator
     cannot see it either.
  The leader's premise ("any family whose seam depends on that sweep
  becomes a DARK family") **does not hold**: F12's liveness comes from
  the three lanes with readers, so **no family is dark because of the
  gap**. Stating it otherwise would be the "pinned fiction" D-a forbids.
* **The counts are re-derived from the measurement**, not from the
  projection: **10/16 live, 6/16 dark (99 live series)** — see §3.1.

**THE ROUTING FACTS (the leader's (c) — report, do not fix):**

1. `app.state.stale_pause_sweep` is never published. The gap is
   **pre-existing** (the lane did not create it; it only made the
   locator look for it).
2. **Publishing it alone would change nothing** — the F12 adapter needs a
   `counters()` reader, and `StalePauseSweepService` has none. Covering
   the stale_pause lane needs **BOTH**: an `app.state` publication **and**
   a `counters()` reader on the service (the same shape the other three
   readers have).
3. The same is true of `job_lock_sweep` and `upgrade_journal_sweep`:
   both ARE published, and both render no series (no reader).
4. **Owner:** the sweep services' own lane (the `counters()` readers) +
   the mechanical-file lane (the `app.state` publication). Neither is
   the central-metrics lane; the central-metrics lane's job here was to
   measure and report, which this file does.

---

## 6. The verdict

| Claim | Verdict |
|---|---|
| the C-1 locator resolves the LIVE handles through the real path | **PROVEN** — the probe's presence map flipped `dsn`/`manager`/`mcps_gateway_service`/`wedge_watchdog` from ABSENT to PRESENT between the sampler's first and second tick (§2) |
| the seven dsn-gated adapters (F0.3, F3, F8, F10, F11, F13, F18) | **five LIVE** (F0.3, F8, F10, F11, F13) — **F3 and F18 are STILL DARK**, for their own non-C-1 reasons (`capture_off` · `series_absent`, §4). F4/F5 carry **no** dsn gate; F6 was **never** dsn-dark |
| the live family set | **10 of 16** — the re-scoped count, with a named reason for each of the 6 dark families (§3.1) |
| any dark family caused by the C-1 locator | **NONE** |
| the Q1 verdict adjustment | **no verdict change**; the stale_pause lane is documented-dark for two independent reasons; F12's live lanes are the three with `counters()` readers (§5) |
| the W-1 gate in the real path | **ON by default**, the observation fires (§3.3) |
| the isolation fences | **all verified before the boot** (§1); the live daemon (`:9797`/`:9798`), the demo daemon (`:7979`) and the live prod DB (a remote host) were never contacted |

**THE COUNCIL'S HONEST GAP IS CLOSED.** C-1 is no longer a static claim:
the dark→live transition is observed, the live family set is measured,
and the six dark families each carry a named cause that is not the
locator. Three of those causes are **new findings** (§4) that no static
gate in the lane could have surfaced — they are routed, not fixed.
