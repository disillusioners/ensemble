# Runbook: Promoting Mission Records to `enforce`

**Component:** the mission-record read/write substrate (Proposal B, PR1–PR3) —
`mission_records` (one row per mission EPOCH) + `mission_commissions` (one row
per commissioned worker), the mode ladder `ENSEMBLE_MISSION_RECORDS =
off | dry | enforce`, and the RDRS reconciliation sweep (lane 7) that retires
outstanding commissions.

**Risk class:** BEHAVIOR-CHANGING (read-side gating). The promotion turns the
mission record LOUD: missions with outstanding commissions start HOLDING their
terminal publish and stop reaching the quiet state. That is the intended effect
of the D2 premature-settle fix — it is also the failure mode an operator will
notice first.

**Code owners:** `daemon/services/mission_record_service.py` (mode ladder, TTL
resolver, terminators), `daemon/services/mission_record_reads.py` (the read
sites), `daemon/repositories/mission_record/repository.py` (the substrate),
`daemon/services/child_reports.py` (the L3 root-completion gate + the
delivery-side terminator), `daemon/services/report_delivery_recovery.py`
(RDRS lane 7, the reconciliation sweep).

**Authority (implementation truth):** the three adoption-delta notes —
`.agents/shared/planning/completion-subsystem-mission-records-pr1/`,
`…-pr2/`, `…-pr3/adoption-delta-notes.md`. The design docs
(`proposal-b-mission-record-anchor.md`, `architecture-map.md`,
`reconciliation-sweep-design.md`) are UNTRACKED in the MAIN checkout
(`.agents/shared/planning/completion-subsystem/`) and are reference material
only.

**Scope of this runbook:** the OPERATOR promotion step (flip `dry` → `enforce`
and verify). It does NOT cover the install/upgrade pipeline — see
`docs/runbooks/upgrade-drills.md` for staging/promote/rollback mechanics.

---

## 0. The one-paragraph version

The ship default is `dry`: rows are minted, stamped and logged, but every READ
site still runs the legacy behaviour, so nothing about the daemon's decisions
changes. `enforce` flips the read sites onto the record (with the legacy
behaviour kept as the declared fallback leg) and flips the reconciliation
sweep from "detect and count" to "write". The promotion is a one-line env
change plus a RESTART (the knob is restart-read — there is no live flip). It
was GATED on PR3 landing; PR3 has landed, so the gate is open — but the
promotion still wants the SWEEP's own evidence (the `dry`-mode counters) before
the flip. Read this runbook's §1.3, then §3, before you flip.

---

## 1. Preconditions

### 1.1 PR3 merged, rebuilt and RESTARTED — staging is not activation

The promotion only takes effect on a running binary that contains PR3. The
house deploy trap applies in full:

* **Staging ≠ activation.** `scripts/upgrade/stage.sh` stages a release
  (`releases/<ver>/` + the `current` symlink) without touching the running
  process; the live daemon keeps executing the binary it was started with. A
  merge or a stage does NOT change behaviour.
* **Activation = rebuild + restart.** The frozen PyInstaller binary is what
  runs; source changes do not reach it until the release is built and the
  daemon is restarted against it.
* **The stop script can refuse with exit 78.** `scripts/stop-ensemble.sh`
  exits **78** when the upgrade pipeline is not settled (Layer (i)) or the
  rollback lock is busy (Layer (ii)) — that is a REFUSAL, not a crash. When
  the pipeline is genuinely settled and you still hit 78, the operator-emergency
  path is `bash scripts/stop-ensemble.sh <install_dir> <port> --force`
  (`--force` bypasses both refusal layers and is auditable in shell history —
  prefer it over any env-var equivalent).
* **Start via the launcher, then probe.** The detached launcher re-exec is what
  serves the daemon (`launcher.sh` → `current/ensemble-prod`, or the flat
  layout `ensemble-prod`); `scripts/upgrade/restart.sh` encodes the canonical
  sequence — *step 2 stop → step 3 detached launcher re-exec → step 4 the
  `/livez` + `/readyz` gate*. Probe `http://localhost:<port>/livez` (and
  `/readyz`) after the restart; a start without a `/livez` 200 is not an
  activation.
* **The rollback artifact is the `.bak` binary** plus
  `ENSEMBLE_MISSION_RECORDS=dry` and a restart (§4.4).

**Verify before continuing:** the running daemon's boot log shows the mode
resolution, and the record substrate is present in the live database. The
schema lands ADDITIVELY — SQLite via the migration runner
(`20261025_122631_create_mission_records.sql`,
`20261025_122700_create_mission_commissions.sql`,
`20261026_060000_add_mission_commission_resolved_at.sql`), PostgreSQL via
`SQLModel.metadata.create_all` + the `_ensure_postgres_columns` mirror. The
`resolved_at` column and the F1 open-epoch index are applied by the boot
path, so a pre-PR3 binary simply ignores them — the daemon must have BOOTED
once on the new binary before those PG-side statements have run.

### 1.2 The ship default stays `dry` — and an invalid token fails the boot LOUDLY

`ENSEMBLE_MISSION_RECORDS` is unset ⇒ `dry` (`mission_record_service.py:
resolve_mission_records_mode`, the empty-token branch sets `DRY`). Do not
change the default; the promotion is an operator action against a specific
install's env.

The knob is validated at boot: an **unrecognized token raises a loud
`ValueError`** naming the env var and the expected set — it does not silently
degrade to `dry`. That is deliberate (a misconfigured knob must fail the boot,
not quietly disable a feature you thought you armed). The lane-facing
`…_fail_open` wrappers never propagate it, so a lane can never break on a
records-side problem.

### 1.3 The PR2 §(f) enforce-promotion precondition is now SATISFIED

PR2 shipped the READS but no writer that retired a commission row, so PR2's
adoption notes §(f) said, in capitals: **DO NOT PROMOTE TO `enforce` BEFORE
THE PR3 TERMINATOR LANDS.** The promotion checklist had three steps. Two are
now closed and one is this runbook:

| PR2 §(f) checklist step | State | Closure evidence |
|---|---|---|
| 1. Land PR3's commission reconciliation sweep (the terminator: the `satisfied` write on delivery evidence + the `abandoned` write on worker-terminal / TTL, with the fail-MISS probes) | ✅ **CLOSED** | `13aa51e1a` (RDRS lane 7) + `3bd21ecbb` (the sweep's data plane: the R0 admission query, the R5 guarded transition, the `resolved_at` stamp, the TTL resolver) |
| 2. **Close MAJOR-1** — the epoch-supersession orphan commissions (the `job_continue` lane). MUST be closed before enforce. | ✅ **CLOSED** | `a3e033984` (user ruling (c) CROSS-EPOCH HOLD — the anchor-scoped outstanding count) + `be8806ff5` (the delivery-side terminator) |
| 3. THEN flip `ENSEMBLE_MISSION_RECORDS=enforce` + restart | ⬜ **THIS RUNBOOK** | §2 |

The PR2 chain that made this a gate (verbatim from the PR2 notes §(f)): the
`job_continue` continuation supersedes the anchor's prior OPEN epoch → the
prior epoch's commission rows are ORPHANED (they keep
`mission_id = <the superseded epoch>` and `state='outstanding'`) → the census
record leg read the NEW (open) epoch's rows → `attestation_required=False` →
the gate was OFF → the mission could settle while the orphaned commissioned
work was still running. Ruling (c) closes that by making the count
ANCHOR-scoped: a commission orphaned on a superseded epoch STILL counts, so the
hold fires until a terminator retires it.

PR3's own ship posture (the PR3 adoption notes, "Ship + promotion posture")
says the same thing: the PR2 §(f) gate "is now SATISFIED on the delivery
side", lane 7 "HAS landed", "so a commission whose report is never delivered no
longer holds forever: the sweep abandons it under the fail-MISS probes + the
TTL horizon."

### 1.4 What else PR3 brought that the promotion depends on

* The **delivery-side terminator** (`be8806ff5`): the natural completion write
  path SATISFIES the routing commission when the report is actually written and
  addressed to its commissioner — a guarded `UPDATE … WHERE state='outstanding'`,
  ATOMIC with the delivery write (it rides the `WriteGuardSession` via a
  SAVEPOINT), and FAIL-OPEN (a satisfy failure never breaks the delivery; a
  rowcount of 0 is a lost race, counted and never retried in place).
* The **reconciliation sweep** (`13aa51e1a`): the escape valve that retires the
  rows the delivery path cannot (a worker that died, a report that was never
  produced, a delivery that was lost in a crash).
* **The sweep's own stall horizon (MAJ-4).** A commissioned worker whose newest
  NON-TERMINAL task stamp is OLDER than the commission TTL is **not live** for
  sweep purposes: the sweep abandons the commission under its OWN horizon
  instead of waiting forever for a permanently-stalled `RUNNING` task to clear.
  The horizon predicate is the mission-live guard's own task-stamp probe
  (`mission_live_guard.member_live_task_attempt_at` — reused, not re-derived).

  **PRECONDITION — `StaleTaskRecovery` must be running.** The sweep's horizon
  decides whether a commission may be ABANDONED; it does NOT reclaim the
  stalled task itself. `StaleTaskRecovery` is the boot-time reaper that
  actually retires the stalled `RUNNING` task rows and lets the worker make
  progress — so a promotion on a daemon where that service is disabled
  converts "stalled worker" into "abandoned commission" (the mission releases,
  the work is lost). Before MAJ-4 the sweep's only escape from a stalled task
  was `StaleTaskRecovery` — an unnamed cross-subsystem dependency; the horizon
  makes the sweep self-sufficient for the DECISION, not for the RECLAIM.

### 1.5 Be aware: promoting also COMMITS the boot backfill

The boot backfill is mode-gated (`d97132cff`): `off` skips it, `dry` runs the
dry-run preview, `enforce` runs the **COMMITTING** run (`dry_run=False`) and
mints the historical advisory nudge commission rows at boot. The run is
idempotent (deterministic UUID5 ids + the epoch-0 resolve-if-none probe), so
re-boots are no-ops.

Those advisory rows are excluded from EVERY commission-consulting read by the
one consistent `advisory_backfill = False` predicate (routing, census, quiet,
L3, sweep admission), so they cannot arm the gate — they are an audit ledger.
They are durable by design ("if dropped, the ledger is worse than no ledger";
no pruning, no TTL). This is a consequence of the promotion, not a separate
action.

### 1.6 The two binding rulings, and the incident chain this promotion closes

**Ruling ① — Term-1 bypass-recording: REMOVED.** The attested arm stays a
no-op / bypass BY DESIGN: there is NO bypass-recording and NO quiet-mission
blocking rule. The design's NEW(i) / NEW(ii) rows were superseded by the ruling
and were NEVER built — so nothing in this promotion records a bypass, and
nothing blocks a quiet mission on the strength of one. If you are looking for a
"bypass was recorded" artefact, there is none and there is not going to be
one: the ruling removed the requirement.

**Ruling ② — Scheduler anchoring: UNCHANGED (and inert).** The scheduler mint
hook stays INERT: `ENSEMBLE_MISSION_RECORDS_SCHEDULER_MINT` stays default-OFF
and the mode ladder alone does NOT arm it (both gates must open). Lane 9 stays
bare. Promoting to `enforce` does not change this: a scheduled mission mints no
commission rows, so the sweep has nothing of the scheduler's to reconcile.

**The incident chain this promotion closes (`fd302262`).** The investigation
re-derived the chain and OVERTURNED its premise — the workers were real; the
phantom was in the RECORD of what happened. Three defects sat underneath:

* **D1** — the `instance_nudge` report routed to the STRUCTURAL PARENT, not the
  nudger (the audit ledger had ZERO readers). **CLOSED** by the routing flip.
* **D2** — the completion gate was blind to OUT-OF-TREE commissioned work (the
  mission settled ~5 minutes early while a commissioned worker was still
  running). **CLOSED** by the quiet + L3 legs (PR2) and hardened to the
  anchor-scoped count (PR3).
* **D3** — the phantom-id LIKE-fusion seam. **DEFERRED BY DESIGN** — the
  record's blast is reduced (the routing keys on FULL ids resolved at mint
  time), but the unique-prefix-or-raise lane itself has NOT landed.

The chain's full table and the per-defect closure verdicts live in
`.agents/shared/planning/completion-subsystem-mission-records-pr3/b-arc-closure-table.md`
§(c) + §(a). This promotion is the LAST step of that chain: without it, D2's
fix is armed but the sweep that releases the hold is not yet trusted to write.

---

## 2. The promotion

### 2.1 Set the env var

In the target install's env file — `INSTALL_DIR/.env` (for the live install,
`~/agents-ensemble/.env`); `scripts/upgrade/stage.sh` stages the
`ENSEMBLE_SELF_ENV` marker INTO that file and the upgrade library reads
`PORT` and the rest of the install's env state from it:

```
ENSEMBLE_MISSION_RECORDS=enforce
```

Valid tokens are exactly `off`, `dry`, `enforce` (case-insensitive; the token
is stripped and lower-cased). Anything else fails the boot loudly (§1.2).

### 2.2 Restart — the knob is restart-read, there is NO live flip

The mode is resolved ONCE at boot and cached (`resolve_mission_records_mode`).
A mid-run env mutation cannot change a resolved knob; the operator flips the
env and restarts. Restart mechanics are in §1.1 (`stop --force` → launcher
re-exec → `/livez`).

### 2.3 Optional knobs (all documented; all restart-read)

| Knob | Where | Default | Meaning |
|---|---|---|---|
| `ENSEMBLE_MISSION_RECORDS` | `daemon/services/mission_record_service.py` (`MISSION_RECORDS_MODE_ENV`) | `dry` | The MODE. `off` → no mints, no reads. `dry` → mints + stamps + logs; every read site stays on legacy behaviour (zero behaviour change). `enforce` → the read sites consult the record (legacy behaviour = the declared fallback leg) and the sweep writes. **There is no second mode knob.** |
| `ENSEMBLE_MISSION_COMMISSION_TTL_SECONDS` | `daemon/constants.py::MISSION_COMMISSION_TTL_ENV` (resolved by `mission_record_service.resolve_mission_commission_ttl_seconds`) | `21600` (6 h) | The commission TTL horizon. It **OVERRIDES** the config-family knob below. An invalid value warns and falls back to the default — it NEVER raises (a misconfigured knob must not strand every commission). |
| `SERVICES_REPORT_DELIVERY_RECOVERY_MISSION_COMMISSION_TTL_SECONDS` | `ServicesConfig.report_delivery_recovery_mission_commission_ttl_seconds` | `21600` | The config-family default for the horizon (env prefix `SERVICES_` on `ServicesConfig`). |
| `SERVICES_REPORT_DELIVERY_RECOVERY_LANE_MISSION_RECONCILIATION` | `ServicesConfig.report_delivery_recovery_lane_mission_reconciliation` | `True` | Lane 7's OWN kill-switch (master + lane, like the other six lanes). `False` → the lane does not run at all — see §5.3, this can make holds permanent. |
| `SERVICES_REPORT_DELIVERY_RECOVERY_ENABLED` | `ServicesConfig.report_delivery_recovery_enabled` | `True` | The RDRS MASTER kill-switch. `False` → the service is constructed but DISABLED: no lane runs (the periodic thread never starts, the boot sweep is skipped, and the crash-recovery endpoint returns **503** with "disabled by config" — MAJ-2). Lane 7 is off with it. |

### 2.4 What `enforce` actually changes (the surface you are arming)

**Read sites (now consult the record; the legacy behaviour is the declared
fallback leg):**

1. The **delegation census** — `attestation_required` becomes the UNION of the
   record's answer and the legacy delegation scan (the record's contribution is
   ADDITIVE: it can only ARM the gate, never disarm it — the safe direction for
   a premature-settle fix).
2. The **5th R2 input** `outstanding_commissions` — mission-scoped, fail-open
   `-1` sentinel (never a 0 default); the quiet predicate requires `== 0`. A
   running out-of-tree worker now holds the mission in the deny/HOLD band.
3. The **L3 root-completion mission leg** — the terminal publish is held while
   the mission has outstanding commissions. The bus-pending leg, the
   tree-liveness leg and the freshness leg are untouched (this leg is ADDITIVE).
4. The **epoch-boundary ledger clear** — armed only in `enforce` (it resets the
   deny bound and epoch-tags the `denial_epochs` entries). The withhold budget
   and the run-deny cap do NOT reset.

**Write sites (the sweep):**

5. The **RDRS lane 7** reconciliation sweep goes from "detect + count, zero
   writes" to "write": it resolves the provably-dead outstanding commissions to
   `abandoned` and the provably-delivered ones to `satisfied`.

**Boot:**

6. The **boot backfill** commits (§1.5).

Everything else — the mints, the stamps, the amend-at-delivery, the logs — runs
identically in `dry` and `enforce`.

---

## 3. What to watch after promotion

### 3.1 L3 holds on REAL commissions (expected — this is the fix working)

The hold fires at the root-completion emission-time gate when the mission has
outstanding commissions. The log line:

```
root_completion: emission-time gate blocked for <instance_id[:8]>... (reason=mission_commissions=N); downgrading to WAITING_CHILDREN and suppressing publish
```

The reason string is literally `mission_commissions=N` (`child_reports.py` —
the L3 mission leg). N is the count of outstanding commissions for that
anchor.

**Reading it:** a hold while a commissioned worker is still running is
CORRECT. The publish is suppressed and the parent is downgraded to
`WAITING_CHILDREN` — the mission cannot settle while commissioned work is
outstanding. The hold releases when the commission is satisfied (the delivery
terminator) or abandoned (the sweep).

**A hold that does not release** is the thing to investigate: the commission
was never satisfied and the sweep never retired it. Check the sweep counters
(§3.3).

### 3.2 The census arm

`attestation_required=True` with `outstanding_commissions > 0` — the record's
leg has armed the gate.

**Scope nuance (important, and it looks like a bug if you do not know it):** the
attestation CENSUS stays **open-epoch-scoped**, while the L3 hold and the R2
quiet predicate are **ANCHOR-scoped** (the MAJOR-1 ruling (c) scope). This is
deliberate and pinned by a test (`test_the_census_stays_open_epoch_scoped`) —
the ruling text names the L3 hold + the R2 quiet input only. So a mission can
show a HOLD while its census row reads `attestation_required=False`: that is
exactly the superseded-epoch orphan case (the orphan lives on the superseded
epoch, so the open-epoch census cannot see it, but the anchor-scoped hold
does). Do not "fix" it by widening the census.

### 3.3 The sweep's first runs

**Force one pass** (the operator seam — the periodic sweep's interval is
independent of your promotion):

```
POST /api/recovery/recover_report_delivery
```

It returns the sweep result verbatim:

```json
{ "lanes": { "mission_reconciliation": { …counters… } }, "total_recovered": N }
```

The `mission_reconciliation` lane dict carries the counters (plus the
inherited lane counters `recovered` / `errors` / `skipped_busy` /
`busy_check_failed` / `already_recovered` / `orphan_disposition` /
`skipped_already_reported`):

| Counter | Meaning |
|---|---|
| `abandoned_worker_terminal` | The worker reached a terminal state with no report ⇒ resolved to `abandoned`. |
| `abandoned_ttl` | Past the TTL horizon with a SUCCESSFUL "not live" liveness probe ⇒ resolved to `abandoned` (the time-bounded fail-MISS). |
| `abandoned_worker_missing` | The worker's row is missing ⇒ resolved to `abandoned`. |
| `satisfied_on_evidence` | Positive delivery evidence with a still-outstanding row ⇒ resolved to `satisfied` (the natural satisfy write was lost). |
| `skipped_live` | The worker is LIVE ⇒ the row is held. A live worker is never abandoned, at any age (R2a). |
| `skipped_watcher_pending` | A PENDING bus watcher exists ⇒ the row is held (the work is still legitimately in flight). |
| `below_ttl_hold` | The row is YOUNG (the worker row is missing, or the worker is non-terminal and idle) ⇒ held until the horizon; retried next cycle. |
| `already_resolved` | Another actor won the guarded transition (rowcount 0) ⇒ counted, never retried in place. |
| `masked_suspected` | The D4(iii) masked-enqueue signal (see below). |
| `probe_errors` | A probe could not prove its question ⇒ the row is HELD (fail-MISS). |
| `masked_probe_errors` | The MASKED-ENQUEUE probe's own fault (MAJ-3). It has its own counter because it is an OBSERVABILITY probe; a fault HOLDS the row for that pass (no abandon, no satisfy) and is retried next cycle. |
| `diagnostic_probe_errors` | A DIAGNOSTIC delivery-evidence leg's own fault (F2 — the leader ruling). Legs 2/3 (the queue-side + parent-history PREFIX ledgers) are DIAGNOSTIC ONLY: they never authorise a `satisfied` and never authorise a hold-against-abandon. Their fault is counted here (its own counter, the `masked_probe_errors` precedent) and the row is NOT held — the TTL backstop stays reachable. |
| `errors` | A crashed row — logged + counted + untouched. |

**Log lines to recognise:**

```
RDRS lane 7: mission_reconciliation admitted N outstanding commission(s) (mode=…, ttl=…s, batch_cap=…)
sweep mission_reconciliation ABANDONED mission_id=…, commission_id=…, commissioner=…, worker=…, lane=…, abandon_reason=…, age_seconds=…
sweep mission_reconciliation SATISFIED mission_id=…, commission_id=…, commissioner=…, worker=…, lane=…, satisfy_reason=delivery_evidence, age_seconds=…
sweep mission_reconciliation MASKED ENQUEUE suspected mission_id=…, commission_id=…, commissioner=…, worker=…, lane=…, age_seconds=… — no queue row since the mint; NO auto-action
```

In `dry` mode the verb on the first two lines is the dry-run variant
(`WOULD ABANDON (dry-run)` / `WOULD SATISFY (dry-run)`) — that is how you tell
a detection from a write. The abandon reason vocabulary is FROZEN:
`worker_terminal_no_report` · `ttl_expired_not_live` · `worker_row_missing`.

**LOG LEVELS.** `WARNING` is for sweep ACTIONS only — a REAL ledger write in
`enforce` (`ABANDONED` / `SATISFIED`). The dry-run previews are `INFO`: a dry
pass writes nothing, so a WARNING there would fire on every detection and
drown the real signal. The `MASKED ENQUEUE suspected` line is a WARNING for a
different reason — it is an ANOMALY signal, not an action (and it has a
documented false-positive population: see §3.6 — do not trip the rollback on
it alone).

**`masked_suspected` is the D4(iii) observability signal.** It fires when a
commissioned worker's queue row is entirely absent past the grace window — the
signature of a mis-addressed (masked) enqueue. It is a COUNTER + LOG ONLY: the
sweep never auto-acts on it and never abandons a row on this signal alone. The
D4(iii) FIX is deferred (it is another architect's seam); this counter is the
observability half, shipped.

### 3.4 Dry-then-enforce sequencing — do not skip the soak

The intended sequence is: **soak in `dry` first.** In `dry` the detection
counters move and NOTHING is written — `recovered` stays 0. That counter set is
the tuning dataset. Then:

1. Run in `dry` for a meaningful window (long enough to see a full mission
   lifecycle: a nudge, a `send_message` delegation, a worker completion, and at
   least one worker that dies or never reports).
2. Read the `mission_reconciliation` counters on a forced sweep pass.
3. Tune the TTL if the counters say so (§3.5).
4. **Only then** flip to `enforce` and restart.

Flipping straight to `enforce` without the dry counters means you are tuning
the TTL on live traffic with writes enabled — the abandonment is real and
durable.

### 3.5 Tuning the TTL (the mission-drain speed ↔ false-abandon trade)

The TTL default is 21600 s (6 h) — a starting point, not a decision (the
TTL ruling was deliberately left open; the `dry` counters are the evidence).

* **TTL too SHORT** → `abandoned_ttl` climbs → an OLD worker that is NOT live
  (idle, or a missing row) is abandoned → the hold releases while that work
  might still produce a report later. The mission can settle prematurely — the
  exact D2 class the record was built to close.
* **TTL too LONG** → `below_ttl_hold` climbs → provably-dead rows keep holding
  the mission → time-to-settle grows.
* **The TTL is NOT a blunt timer.** It applies only to the *unprovable* case:
  past the horizon AND with a SUCCESSFUL "not live" liveness probe. A
  **live** worker is held regardless of age (`skipped_live`); a liveness-probe
  **ERROR** holds the row at any age (`probe_errors`); a PENDING bus watcher
  holds it (`skipped_watcher_pending`); a MISSING worker row is held while it
  is young and abandoned once it is old.
* **Tuning rule of thumb:** raise the TTL if `abandoned_ttl` is significant
  relative to `skipped_live` (you are abandoning work that is still alive);
  lower it if `below_ttl_hold` dominates and the held rows are provably dead.
* Changing the TTL is a **restart-read** knob ⇒ another restart.

### 3.6 Rollback tripwires (flip back to `dry` if you see these)

* `probe_errors` climbing at a sustained rate — the sweep cannot prove its
  questions, so rows accumulate as holds rather than resolving.
* **NOISY — do NOT trip the rollback on `masked_suspected` ALONE.** It has a
  documented FALSE-POSITIVE population: the signal fires when the commissioned
  worker has NO queue row at all since the mint, and a legitimate worker path
  can write no MessageQueue row of its own (the signal is OBSERVATION ONLY — it
  never triggers an action, and it never abandons a row). It is a tuning
  signal, not a standalone rollback tripwire: read it together with
  `probe_errors` / `already_resolved` and with a hold that does not release.
  (The D4(iii) FIX itself is deferred — another architect's seam.)
* Holds that do not release and no sweep counter explains why.
* `already_resolved` climbing — another actor is winning the guarded
  transitions; check that the delivery-side terminator and the sweep are not
  racing each other.
* **`diagnostic_probe_errors` is NOT a rollback tripwire (F2).** It counts the
  faults of the DIAGNOSTIC delivery-evidence legs, whose faults do NOT hold a
  row and do NOT block the abandon path — a climb there degrades the
  observability signal, not the resolution. Investigate the cause (a queue
  ledger read error, a missing checkpointer), but do NOT flip back to `dry`
  on this counter alone; the TTL backstop still resolves those rows.

---

## 4. Rollback path

### 4.1 The normal rollback: flip back to `dry` + restart

Set `ENSEMBLE_MISSION_RECORDS=dry` and restart (same mechanics as §2.2).

**What reverts:** every read site returns to legacy behaviour — the census's
legacy leg, the legacy quiet predicate, the legacy publish path, and the
epoch-boundary ledger clear is **dis-armed** again (it was `enforce`-only).
The sweep stops writing; its detection counters keep moving.

**What does NOT revert — state this plainly:**

* **`satisfied` and `abandoned` commission rows are DURABLE HISTORY.** A row
  the sweep retired stays retired. Reverting the mode does NOT resurrect it,
  and it does NOT un-abandon it. The rows record what actually happened to the
  commissioned work; the abandonment decision was made under evidence that is
  still true.
* **The advisory backfill rows committed in `enforce` stay committed** (§1.5).
  They are durable by design — no pruning, no TTL.
* **The `mission_records` epochs stay.** `dry` mints too — it is the READS
  that revert, not the writes.
* **The epoch-boundary ledger clear already FIRED is not replayed.** The deny
  bound that was reset at a boundary stays reset; the clear is not a queue of
  pending resets.

### 4.2 The schema is additive — nothing to drop

`mission_records`, `mission_commissions` (+ the `resolved_at` resolution
stamp) are ADDITIVE tables/columns. Leaving them in place after a rollback is
harmless: a `dry`/`off` daemon ignores them, and a later re-promotion reuses
them. There is no destructive migration to reverse.

### 4.3 The full unwind

`git revert` the PR3 commits + rebuild + restart. The schema stays behind
(additive ⇒ harmless). Per the proposal's rollback story, reverting the code
plus the mode flip is a complete unwind of the behaviour change.

### 4.4 The live-binary rollback (the house pattern)

Restore the `.bak` binary (the install dir keeps one per activation, e.g.
`backup-ensemble-prod-<timestamp>.bak`) OR roll back the release via
`scripts/upgrade/rollback.sh`, AND set `ENSEMBLE_MISSION_RECORDS=dry` in the
env before the restart. The mode change is independent of the binary — a
rollback that leaves `enforce` in the env leaves the intent ambiguous (an
older binary may ignore the knob entirely, a partially-rolled-back one may not
honour it the same way), so pin `dry` explicitly either way.

---

## 5. Known-caution inventory (honesty section)

### 5.1 More HOLDs and more DENIES — BY DESIGN

`enforce` makes the mission record LOUD. Missions with outstanding
commissions will hold their terminal publish (L3) and will not reach the quiet
state (the 5th R2 input) until the commissions resolve. Operators who see
"more denials" after the promotion are seeing the D2 premature-settle class
being closed, not a regression. The record's contribution is additive by
design: it can only ARM the gate, never disarm it.

### 5.2 Orphaned cross-epoch commissions hold until the sweep retires them

MAJOR-1 is ruled **(c) CROSS-EPOCH HOLD**: NO re-pointing and NO
auto-supersede of the orphaned rows — attribution stays with the owning epoch.
A `job_continue` continuation supersedes the anchor's prior OPEN epoch; the
prior epoch's commission rows keep `state='outstanding'` and keep holding the
mission until a terminator retires them (the delivery path cannot — the
continuation's report was never addressed to that commissioner's commissioner).
The sweep retires them (worker-terminal / TTL / missing-row). Until then, the
hold stands — that is the ruling, not a bug.

### 5.3 The eternal-mission escape valve IS the sweep (TTL-bounded) — and it can be turned off

This is the sharpest edge in the whole promotion:

* An outstanding commission whose commissioned work will NEVER produce a
  report holds its mission open forever. That class is real (proposal-b §8).
* The escape valve is **RDRS lane 7** — the reconciliation sweep — bounded by
  the TTL. If the sweep runs, a dead commission is abandoned within the TTL
  horizon and the hold releases.
* **If the sweep is disabled** (`SERVICES_REPORT_DELIVERY_RECOVERY_ENABLED=False`
  — the service is constructed but DISABLED, so no lane runs — or
  `SERVICES_REPORT_DELIVERY_RECOVERY_LANE_MISSION_RECONCILIATION=False` — lane
  7 does not run), then **the hold can be PERMANENT**: `enforce` holds
  publishes for missions with outstanding commissions, and with the sweep off
  nothing retires the rows. A mission in that state never settles.
* Corollary: **do not disable the sweep while running `enforce`.** If you must,
  flip the mode back to `dry` first.

### 5.4 The sweep is FAIL-MISS everywhere

Every probe error HOLDS the row (`probe_errors`++, zero writes). The sweep
never resolves a commission on a failed probe. The single time-bounded
exception is the TTL: past the horizon AND with a SUCCESSFUL "not live"
liveness probe, the unprovable state resolves LOUDLY; a liveness-probe ERROR
still holds at any age. The sweep writes ONLY `mission_commissions` — no
instance status, no `mission_records`, no `report_injections`, no
`dependency_watchers`.

**The masked-enqueue probe is the one exception to the counter rule
(MAJ-3).** It is an OBSERVABILITY probe (the signal never authorises an
action by itself), so its faults are counted in their OWN counter
(`masked_probe_errors`), not in `probe_errors` — whose contract is "a DECISION
probe could not prove its predicate ⇒ the row was HELD". A masked-probe fault
still HOLDS the row for that pass: the abandon and the satisfy are both
skipped, and the row is retried on the next cycle.

**The diagnostic delivery-evidence legs are the other exception (F2 — the
leader ruling).** The delivery-evidence read has ONE authority (leg 1 — the
commission-bound, state-filtered injection-row read) and TWO diagnostics (leg 2
— the queue-side PREFIX ledger; leg 3 — the parent-history PREFIX ledger).
The two diagnostic legs are PAIR-keyed and TIME-unbounded: they carry no
commission id, so an EARLIER commission's delivery (or a mere ENQUEUE) used to
satisfy a FRESH one and release the publish hold early. They now KEEP RUNNING
for observability, but they may NEVER authorise a `satisfied` and may NEVER
authorise a hold-against-abandon. Their faults are counted in their OWN
counter (`diagnostic_probe_errors`) and the row is NOT held — the TTL backstop
stays reachable, which is what keeps the publish hold BOUNDED ("it releases
NO EARLIER than the TTL horizon — fail-closed, bounded, safe"). A commission
whose delivery is visible ONLY through the diagnostic legs therefore resolves
via the terminator on the real delivery path, or via the TTL-bounded abandon.

**The liveness probe has a horizon (MAJ-4).** "Live" is not a bare
`has_live_work()`: a member whose newest non-terminal task stamp is older than
the commission TTL is NOT live (the sweep abandons it under the TTL backstop).
A task-stamp probe that CANNOT RUN keeps the member live (fail-safe — the row
is held). See §1.4 for the `StaleTaskRecovery` precondition this sits next to.

An UNWIRED `mission_record_repo` degrades to ZERO with ONE INFO log (the
`force_cancel_and_schedule_retry` precedent) — so an unwired sweep looks like
"nothing happens", not like an error.

### 5.5 The routing leg has no liveness filter

The commission-first routing leg picks the OLDEST outstanding commission for
the worker (ruling ④) and does NOT filter on liveness. If the commissioner is
TERMINATED, the write path's dead-recipient guard falls back to the structural
parent and the commission **stays outstanding**. Note that the sweep's abandon
reasons are WORKER-shaped (`worker_terminal_no_report` · `ttl_expired_not_live`
· `worker_row_missing`), not commissioner-shaped: a dead commissioner with a
still-running worker is held by `skipped_live`, and it is only retired once the
worker itself goes idle past the horizon (`abandoned_ttl`).

### 5.6 The census and the hold have different scopes

Covered in §3.2 — the census is open-epoch-scoped; the hold and the quiet
predicate are anchor-scoped. This asymmetry is deliberate and pinned.

### 5.7 The boot backfill commits under `enforce`

Covered in §1.5. The advisory rows are excluded from every commission read, so
they cannot arm anything; they are durable history.

### 5.8 Read the `dry` counters before you flip

Covered in §3.4. The `dry` counters ARE the TTL-tuning dataset; flipping
without them means tuning on live traffic with writes enabled.

---

## 6. Where to look when something surprises you

| Question | Where |
|---|---|
| What does the mode ladder do in each mode? | `daemon/services/mission_record_service.py` (module docstring + `resolve_mission_records_mode`) |
| Which read sites are gated? | `daemon/services/mission_record_reads.py`; the census consumer `daemon/services/attestation_gate.py`; the L3 gate `daemon/services/child_reports.py` |
| What the sweep does and its counters | `daemon/services/report_delivery_recovery.py` (lane 7 + `MissionReconciliationLaneResult`) |
| The implementation truth (read this first) | the three `…/completion-subsystem-mission-records-pr{1,2,3}/adoption-delta-notes.md` |
| The design (reference only, UNTRACKED in the main checkout) | `.agents/shared/planning/completion-subsystem/` |
