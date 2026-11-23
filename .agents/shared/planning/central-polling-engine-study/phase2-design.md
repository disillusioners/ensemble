# Phase-2 Pilot Design — OrphanWatcher under the central engine (EVIDENCE lane)

**Date:** 2026-11-23 · **Lane:** `dev-coder-engine-p2-ow` (Central Engine Phase-2 pilot, EVIDENCE lane) · **Base:** `7243143d0` (`feature/engine-pilot-orphan-watcher`) · **Status:** verification + harness lane — **the migration seam was ALREADY LANDED and was NOT rebuilt here**

> **READ THIS FIRST — the scope correction.** The pilot migration code was
> **already landed** on this lane's base commit. An independent read-only
> lane proved it, and this lane re-verified it commit-by-commit. **This
> lane delivered VERIFICATION, EVIDENCE and the MISSING HARNESSES — not the
> seam.** Every claim below distinguishes (i) already-landed-and-verified,
> (ii) fixed by this lane, (iii) still missing.

---

## 1. What was ALREADY LANDED (verified, not rebuilt)

Five commits, all ancestors of the base `7243143d0` (dispatcher pre-verified
via `merge-base --is-ancestor`; this lane re-verified with
`git merge-base --is-ancestor` per commit):

| Commit | Date | Subject |
|---|---|---|
| `5044dc148` | 2026-11-21 | fix: OrphanWatcherSweepService born-dead wiring trap — late-bind the bus singleton per tick, LOUD boot wiring assertion, dead-detection escalation |
| `db2e1cb28` | 2026-11-22 | feat: pilot orphan-watcher sweep onto central engine behind per-job flag |
| `4be00db7e` | 2026-11-22 | fix: close fail-stuck paths in pilot dual-path ownership gate |
| `2dd42d9f2` | 2026-11-22 | fix: harden pilot dual-path gate per leader riders R1-R6 |
| `5b562165e` | 2026-11-22 | fix: pin the production bridge failure seam and truthful executor-drop log |

**Live containment (giter's binary forensics, cited as fact):** live runs
`v0.31.42` (`manifest source_commit=ea5325a60ed3c7c04b32a3eeeaa5dea83565bf26`
= the peeled `v0.31.42` tag); the PyInstaller PYZ was carved, its TOC
parsed (4,809 modules) and the marshalled code byte-probed —
`daemon.services.orphan_watcher_sweep` carries `engine_owns_execution`,
`build_engine_executor`, `rearm_legacy_loop`,
`SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE`, `ORPHAN_WATCHER_ENGINE_FLAG`.
⇒ **the migration is ALREADY LIVE AND DORMANT** (the flag family is unset
in production). This lane's staged release carries the **evidence +
harnesses**, NOT the migration.

### 1.1 Verification findings (each landed piece)

| # | Landed piece | Verdict | Evidence (code + test) |
|---|---|---|---|
| V1 | Kill-switch gating, **default OFF** | **VERIFIED** | `dualpath.use_engine_enabled` returns False for an absent key (`dualpath.py:33-38`); `build_observer_specs` derives `kill_switch_key = SERVICES_{TAG}_USE_ENGINE` (`boot.py:179-181`); the flag family is unset in production (posture line: `use_engine_flags=all unset (0/12)`) |
| V2 | `execute_fn` is **SYNC** | **VERIFIED** | `build_engine_executor` returns a plain `def _execute()` (`orphan_watcher_sweep.py:228`); `dispatch.run_db_offload` **refuses coroutine functions** (`dispatch.py:51-57`) |
| V3 | The bus is **late-bound per tick** (born-dead immunity) | **VERIFIED** | `build_engine_executor._execute` calls `get_dependency_bus()` on EVERY tick (`orphan_watcher_sweep.py:229`); the legacy service re-reads the singleton per tick (`orphan_watcher_sweep.py:423-425`); the api.py construct site passes `dependency_bus=None` deliberately (`api.py:2049-2060`) |
| V4 | Registration class **DB_OFFLOAD** | **VERIFIED** | `dispatch.run_db_offload` is the DB_OFFLOAD path (`execution.py:465-466`); the spec's per-tick `UPDATE dependency_watchers` disqualifies MAIN_LOOP |
| V5 | **Fail-to-legacy** on engine failure (never abort boot, never a second legacy loop) | **VERIFIED** | the boot block's `except` drops the executor LOUDLY and re-arms the legacy loop (`api.py:4123-4180`); `rearm_legacy_loop` is idempotent and fail-soft (`orphan_watcher_sweep.py:137-181`) |
| V6 | **Exactly one owner** (no double execution, no dead lane) | **VERIFIED** | both api.py sites consult the SAME conjunct — exactly two `engine_owns_execution(` call sites in `daemon/api.py` (source-pinned); the truth table resolves all 8 combinations with **no BOTH and no NEITHER** row (§2) |
| V7 | The legacy path is **byte-unchanged** when the flag is OFF | **VERIFIED** | `git diff 5044dc148..HEAD -- daemon/services/dependency_bus.py` is **EMPTY**; `git diff 5044dc148..HEAD -- daemon/services/orphan_watcher_sweep.py` is **181 insertions, 0 deletions** — the pilot added the seam and removed nothing (§3.5, Direction B) |
| V8 | **Loop-affinity** in the engine path (the real dead-lane risk) | **VERIFIED ABSENT (measured)** | §3 — the sweep's call graph touches no loop-bound primitive; the two paths were MEASURED running on different loops; a lock-touching MUTATION is proven to fail the harness |
| V9 | `EngineSwitchResolver.resolve_job` "SET BUT NEVER READ" (a commission lead) | **REFUTED AT THIS BASE** | `execution.py:315` calls `self._resolve_switch(spec)` → `killswitch.py:127-133` on EVERY dispatch. The resolver IS injected (`api.py:3979` → `engine.py:49`). The *rollback* is still boot-scoped (the legacy suppression is armed at boot), which is a different fact — see §7 |

---

## 2. The OWNERSHIP TRUTH TABLE

Resolved by the REAL production predicates — the ownership conjunct
(`orphan_watcher_sweep.engine_owns_execution`), the engine's real
kill-switch ladder (`EngineExecutionLoop._resolve_switch` → Tier 1 per-job
key, Tier 2 `ENSEMBLE_ENGINE_ENABLED`) and the real shadow gate
(`EngineExecutionLoop.shadow_active`, Tier 3).

**Ownership conjunct:** `flag ∧ engine_enabled ∧ ¬engine_shadow_mode`.

| # | per-job flag | shadow | engine enabled | engine stamp | legacy loop | **OWNER** |
|---|---|---|---|---|---|---|
| 1 | ON (`1`) | ON | ON | `SKIPPED_SHADOW` | **RUNS** | **legacy** |
| 2 | ON (`1`) | ON | OFF | `SKIPPED_KILL_SWITCHED` | **RUNS** | **legacy** |
| 3 | ON (`1`) | OFF | ON | **`SUCCEEDED`** | **suppressed** | **engine** |
| 4 | ON (`1`) | OFF | OFF | `SKIPPED_KILL_SWITCHED` | **RUNS** | **legacy** |
| 5 | OFF (`0`) | ON | ON | `SKIPPED_KILL_SWITCHED` | **RUNS** | **legacy** |
| 6 | OFF (`0`) | ON | OFF | `SKIPPED_KILL_SWITCHED` | **RUNS** | **legacy** |
| 7 | OFF (`0`) | OFF | ON | `SKIPPED_KILL_SWITCHED` | **RUNS** | **legacy** |
| 8 | OFF (`0`) | OFF | OFF | `SKIPPED_KILL_SWITCHED` | **RUNS** | **legacy** |

**Row 3 is the only engine-owned row.** No row is BOTH; no row is NEITHER.
Test: `tests/unit/services/test_orphan_watcher_truth_table.py` (15 nodes).

### 2.1 The contested dead-lane claim — **REFUTED**

The Phase-0 study asserted: *"flag ON + shadow ON ⇒ dead sweep lane (legacy
suppressed while the engine refuses to execute it)."*

**Refuted by the landed code.** The legacy suppression is gated on the FULL
conjunct (`api.py:2077` — `elif engine_owns_execution(engine_config=getattr(config, "engine", None)):`),
so with shadow ON the conjunct is FALSE and **the legacy loop is not
suppressed**; the executor is not attached either (the boot block uses the
SAME conjunct, `api.py:4018`). The docstring at
`orphan_watcher_sweep.py:112-115` states this intent explicitly:
*"Conjuncting the flag with the engine's own posture is what makes the
migration fail-to-legacy instead of fail-stuck: flag ON + shadow ON (the
SHIPPED default) would otherwise suppress the legacy loop while the engine
refuses to execute it — a dead sweep lane."*

**There is no window in which the legacy loop is suppressed while the engine
refuses to execute**: the two sites share one predicate, so their decisions
cannot diverge.

### 2.2 The flag-UNSET vs explicit-`0` distinction (material for the ceremony)

The commission's note *"with the per-job flag OFF the spec records
SKIPPED_KILL_SWITCHED (Tier 1 fires before the shadow gate) while the other
11 record SKIPPED_SHADOW"* is **true only for an EXPLICIT `0`**. The
**shipped default is UNSET**, and an absent per-job key is *"no switch
active"* (`killswitch.py:103-105`) ⇒ the job reaches the shadow gate ⇒
`SKIPPED_SHADOW` — the same stamp as the other 11.

**Measured on the live dev DB** (fenced `ensemble_dev`, after two boots of
this lane):

```
orphan-watcher   SKIPPED_SHADOW        skip_reason=SHADOW       rows=44
orphan-watcher   SKIPPED_KILL_SWITCHED skip_reason=KILL_SWITCHED rows=4
engine_jobs['orphan-watcher']: last_run_at=2026-11-23T18:01:56Z
                               last_outcome=SKIPPED_SHADOW  dead=0  interval=90.0
```

⇒ At the shipped posture the orphan job is **indistinguishable from the
other 11 observer specs** in `engine_runs`. The ceremony's evidence must
use the **posture line** (`executable_specs=`) plus **SUCCEEDED rows**, not
the skip stamp, to attribute ownership.

---

## 3. The runtime parity proof (machine-checkable, BOTH directions)

### 3.1 The gap this closes

The landed unit pack (`tests/unit/services/test_orphan_watcher_engine_parity.py`,
59 nodes, GREEN at `5b562165e`) drives the sync bridge against a
**SHAPE-MOCKED** bus (`_RealShapedBus`). A shape-mock cannot see
**loop affinity**, which is the production hazard:

* the LEGACY path awaits `bus._sweep_orphan_watchers(...)` on the **daemon's
  main loop**;
* the ENGINE path runs the SAME method via `asyncio.run(...)` **inside a
  worker thread** (`dispatch.run_db_offload` → `asyncio.to_thread`) — a
  **different event loop**.

Any `asyncio.Lock`/`Event`/`Queue`/`Future`/`Condition` held by
`DependencyBus` and touched inside that method's call graph would fail
**only** under the engine path — and with the legacy loop suppressed by the
ownership conjunct, that failure **is** the real dead sweep lane.

### 3.2 Measured results (not designed)

| Measurement | Result |
|---|---|
| Source pin: loop-affine primitives in `_sweep_orphan_watchers` | **NONE** (only `asyncio.to_thread`, which binds to the CALLER's running loop) |
| Legacy run — running-loop id / thread ident | `126269715734144` / `126269768595264` (the main thread) |
| Engine run — running-loop id / thread ident | `126269658339984` / `126269625419456` (a worker thread, a **different loop**) |
| Cross-loop exception raised? | **NO** |
| Adversarial: the bus's `_locks_guard` **bound to the main loop AND held** | the engine path **still completed** (`cancelled=1`) |
| MUTATION self-proof (a sweep that acquires the guard) | the engine path **RAISED** `RuntimeError: ... is bound to a different event loop` ⇒ the harness has teeth |

**CPython semantics measured** (3.14, this worktree's interpreter):
`asyncio.Lock` binds to a loop ONLY on a **contended** acquire; an
uncontended acquire takes the fast path and never checks the loop
(`_loop` stays `None`). A cross-loop failure therefore requires a lock that
is **bound to another loop AND still held** — which is exactly the
adversarial state the harness builds.

### 3.3 Direction A — engine-executed sweep ≡ standalone sweep

Two independently-seeded, identically-shaped fixture sets in one DB
(sets are seeded SEQUENTIALLY because the sweep is global — it cancels
every orphan PENDING watcher in the DB):

| Shape | Seeded state | Expected after the sweep |
|---|---|---|
| `orphan_old` (PENDING, older than the grace, source task GONE) | PENDING | **CANCELLED** |
| `orphan_young` (PENDING, inside the grace window) | PENDING | PENDING (no-op) |
| `active_old` (PENDING, older than the grace, source task `running`) | PENDING | PENDING (no-op) |
| `fired_old` (FIRED) | FIRED | FIRED (no-op) |
| `cancelled_old` (CANCELLED) | CANCELLED | CANCELLED (no-op) |

**Measured:** the legacy path and the engine path produced the **same
outcome map**, the **same single action** (`orphan_old: PENDING →
CANCELLED`), the **same four no-ops**, and the same count (`cancelled=1`
both sides). Engine-path repeat ticks: `1 → 0 → 0` (idempotent, no double
execution).

### 3.4 THE RUNTIME PROOF AT FLAG-ON (MEASURED, in situ — the last missing artifact)

The landed unit pack and the §3.1–3.5 legs above exercise the bridge and
the dispatch CLASS. The artifact that was still missing is the proof that
the **engine really executes the sweep when the flag is ON, in a real
daemon, on a different event loop**. That was produced here on the **fenced
dev DB** (a dev boot with the pilot switches set — NOT a live flip, NOT the
live daemon):

```
bash scripts/measure/engine_pilot_boot_slo.sh --source <tree> \
     --engine-flag 1 --shadow-mode 0 --timeout 60 --out flagon.jsonl
```

**Observed (boot log):**

```
OrphanWatcherSweepService legacy loop SUPPRESSED — engine owns execution
  (SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=1, engine enabled, shadow off);
  sweep object retained for shutdown
orphan-watcher pilot: engine executor registered, legacy loop suppressed
  (SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=1); dependency_bus late-bound at tick
engine boot posture: enabled=True shadow=False registrations=12 observer specs
  (execute_fn withheld); use_engine_flags=SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=1 (1/12);
  executable_specs=1 (orphan-watcher); block_elapsed_ms_gated=144.249
```

**Observed (fenced dev DB, `engine_runs`):**

```
id=687  job_id='orphan-watcher'  started_at=2026-11-23T18:18:29.857842Z
        finished_at=2026-11-23T18:18:29.868268Z  outcome='SUCCEEDED'
        skip_reason=None  duration_seconds=0.010382  error_class=None
engine_jobs['orphan-watcher']: last_run_at=2026-11-23T18:18:29.868268Z
        last_outcome='SUCCEEDED'  dead=0  interval=90.0
```

**Observed (absence of the failure modes):**

| Check | Result |
|---|---|
| legacy loop ticks during the flag-ON boot | **0** (grep on the boot log) ⇒ exactly one owner |
| cross-loop errors (`different event loop` / `attached to a different loop`) | **0** |
| `Traceback` / `FAILED_BACKOFF` in the boot log | **0** |
| `engine_jobs.dead` | **0** (no false-dead) |
| the sweep's own cancellation count | **0 orphans to cancel** — the dev DB held **0** PENDING watchers, so the engine executed a real sweep that found nothing (stated explicitly rather than implied) |

⇒ **Truth-table row 3 is now empirically confirmed end-to-end**: flag ON ∧
shadow OFF ∧ engine enabled ⇒ the engine executes the sweep, the legacy
loop is suppressed, and the run is stamped `SUCCEEDED`. The engine path ran
on its worker-thread loop in a live daemon and raised **nothing**.

(The `executable_specs=1 (orphan-watcher)` field is this lane's honesty
fix — it is what makes the ceremony's posture line attributable.)

### 3.5 Direction B — the flag-OFF fallback is byte-provably unchanged

* `git diff 5044dc148..HEAD -- daemon/services/dependency_bus.py` → **EMPTY**
  (the swept-over machinery was not touched; both paths run the same
  implementation, which is what makes the parity comparison meaningful).
* `git diff 5044dc148..HEAD -- daemon/services/orphan_watcher_sweep.py` →
  **181 insertions, 0 deletions** — the pilot added the seam and removed
  nothing that the legacy path executes.

Both are asserted as tests (they fail loudly if the diff is unavailable —
never a vacuous pass).

### 3.6 The failure-accounting asymmetry (mapped explicitly)

The bus's own sweep is internally **FAIL-OPEN** (it catches DB errors,
logs a warning, returns 0). Therefore:

* a **DB-level** failure ⇒ the bus swallows it ⇒ BOTH paths observe
  `cancelled == 0` and neither records an error (parity HOLDS);
* only a **BRIDGE-level** failure (the bus getter raising, or the
  `asyncio.run` bridge failing) reaches the engine, which stamps
  `FAILED_BACKOFF` + `error_class`; the legacy path's equivalent counts it
  in `_errors_total` and logs ERROR.

⇒ Both paths **fail open**; only the ACCOUNTING differs. The parity pack
maps this asymmetry explicitly rather than hiding it.

---

## 4. The soak harness (read-only vs the live DB)

**Script:** `scripts/soak/engine_pilot_orphan_soak.py` · **Doc:** `soak-harness.md` (this directory)

**Read-only contract:** a strictly read-only role (`ensemble_prod_ro` —
never the daemon's write credentials), the session pinned server-side with
`default_transaction_read_only = on`, no DDL, no temp tables, no LOCKs, no
writes, no flag flips, no restarts.

**Evidence sources** (the engine publishes NO events — registry→engine is
one-way): `engine_runs` (append-only), `engine_jobs`, `dependency_watchers`
(functional proxy), daemon log lines (the posture line = ownership
evidence; the boot epoch = boot-SLO evidence), and the D6 boot measurements.

**Verdict rule (exact):** `VIOLATION` if any rule fires (a violation with
partial data is still a violation) → else `INCONCLUSIVE` if the sufficiency
gate did not pass → else `PASS`. **MISSING DATA ⇒ INCONCLUSIVE, never a
silent PASS**; the report names exactly which evidence is missing.

**Rules:** R1 sufficiency · R2 cadence (tolerance: ONE missed tick) ·
R3 stuck run · R4 overlap (double execution) · R5 outcome hygiene ·
R6 dead flag (the CS-9 carve-out must not fire on a live lane) ·
**R7 ownership** (executions observed while the posture says
`executable_specs=0` ⇒ the engine ran a sweep it must not run) ·
R8 functional (stale PENDING watchers must not accumulate) ·
R9 boot-SLO (every boot ≤ 120 s; a boot with no measurement ⇒ INCONCLUSIVE).

**The 48 h clock starts at the Emperor's flip ceremony** — this harness
builds and validates the machinery; it does not start the clock.

---

## 5. The boot-SLO tooling

**Script:** `scripts/measure/engine_pilot_boot_slo.sh` (new) — it REUSES the
house machinery (`daemon/services/boot_epoch.py`,
`daemon/constants.py:228 READINESS_BOOT_WARM_WINDOW_S=120.0`,
`daemon/services/readiness.py`, `/livez` + `/readyz`).

House fence, every leg load-bearing: `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`
is **pinned explicitly** (never `env -u`'d — the frozen-binary sweep KILLS
live processes when the guard is absent); the environment is scrubbed
**one variable at a time** (`env -u POSTGRES_*` is a silent no-op in bash);
never 9797/9798/8079; the `--release` path boots an **isolated copy** under
`<tmproot>/releases/<name>`.

### 5.1 MEASURED — the flip-precondition number

Two WARM-class fenced boots of this lane's tree (dev DB, own port, own data
dir) plus **two more from the SANCTIONED pack**
(`test/packs/engine_boot_slo_gate.sh` LEG 3, which runs the same fenced
boot independently):

| Boot | Producer | `block_elapsed_ms_gated` | `block_elapsed_ms_total` | boot→livez | boot→readyz | verdict |
|---|---|---|---|---|---|---|
| run 1 | this lane's D6 script | **142.808 ms** | 161.710 ms | 28.525 s | **28.525 s** | PASS |
| run 2 | this lane's D6 script | **89.332 ms** | 105.086 ms | 24.361 s | **24.361 s** | PASS |
| run 3 | sanctioned pack LEG 3 | **106.228 ms** | — | 26.540 s | **26.540 s** | PASS |
| run 4 | sanctioned pack LEG 3 (post-commit) | **112.560 ms** | — | 22.415 s | **22.415 s** | PASS |

**Adjudication of the flip-precondition number.** The shipped flipprep boot
measured `block_elapsed_ms_gated=499.918 ms` against the SLO-1 ≤250 ms cost
bound. **This lane's four measurements are 89.332 / 106.228 / 112.560 /
142.808 ms** — every one **within** the WARM bound (250 ms; headroom
1.75×–2.80×) and within the COLD bound (360 ms). The flipprep figure
breaches **both** bounds (2.00× the WARM bound, 1.39× the COLD bound), so it
was **not** produced by this tree in this posture; it is either out-of-class
(a contended host / cold first boot) or was measured on a different tree.
**The Emperor's flip decision should not be priced against 499.918 ms** —
the landed engine boot block costs 89–143 ms WARM. (The number is
re-derivable: the D6 script emits one JSONL record per boot.)

All four boots are also within the Emperor's binding boot SLO
(boot_epoch → readyz-green ≤ 120 s): **22.4 s – 28.5 s**.

---

## 6. The flip recipe this lane concludes is safe

**Effective flip (the engine owns the sweep):**

```
SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=1
ENSEMBLE_ENGINE_SHADOW_MODE=0
ENSEMBLE_ENGINE_ENABLED=1        (the shipped default — omit unless disabling)
```

then **RESTART** the daemon (the ownership decision is boot-scoped — §7).

**Rollback (one flag, plus a restart):** set
`SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=0` (or restore shadow mode) and
restart ⇒ the legacy loop owns again.

---

## 7. The rollback story — BOOT-SCOPED

The legacy suppression and the executor attachment are both decided **at
boot**. A **runtime** trip of the pilot's flag (or a runtime shadow flip)
does **not** re-arm the legacy loop: the engine stops executing the job
(`SKIPPED_KILL_SWITCHED`) while the legacy loop stays suppressed ⇒ **that
lane is owned by NOBODY until the next restart**. This is documented in the
landed code (`killswitch.py:16-22`, `execution.py:306-314`).

⇒ **A kill is a RESTART with the flag off**, not a runtime flip. The
ceremony must plan for a restart-scoped rollback (a restart is also the
mechanism the boot-scoped ownership decision requires anyway).

---

## 8. Risk list (with evidence)

| Risk | Severity | Evidence | Mitigation |
|---|---|---|---|
| Loop-affinity in the engine path (the REAL dead lane) | would be **BLOCKING** | **ABSENT — measured** (§3.2), incl. a mutation self-proof | none needed; re-run the pack if the sweep's call graph changes |
| The study's dead-lane claim (flag ON + shadow ON) | would be **BLOCKING** | **REFUTED** (§2.1) | the conjunct is the mitigation, already landed |
| A runtime flag trip leaves the lane owned by NOBODY | **HIGH** (operational) | `killswitch.py:16-22`, `execution.py:306-314` | boot-scoped rollback only; the ceremony documents "kill = restart with the flag off" |
| The engine's boot block exceeds the SLO-1 cost bound | MEDIUM | **not** observed: 89–143 ms vs 250 ms (§5.1) | re-measure with the D6 script at flip time |
| The boot SLO (120 s) regresses | MEDIUM | **not** observed: 24.4–28.5 s (§5.1) | R9 in the soak harness; D6 per boot |
| The ceremony reads the wrong ownership evidence (`SKIPPED_SHADOW` cannot distinguish shadow-on from no-executor) | MEDIUM | `execution.py:438-440`; measured on the dev DB (§2.2) | the posture line's `executable_specs=` + SUCCEEDED rows; R7 in the soak harness |
| A false `dead=true` on a live lane from the metadata change | HIGH (if mishandled) | the CS-9 carve-out keys off the **executors dict**, not the metadata (`metrics.py:69`) — pinned by test | none needed; pinned |
| The soak harness is run with write credentials | HIGH | — | the harness pins `default_transaction_read_only = on`; use `ensemble_prod_ro` |

---

## 9. What THIS lane changed (and what it did not)

**Fixed by this lane (minimal, verification-justified):**

1. `daemon/services/engine/boot.py` — the migrated spec's description and
   metadata are now **truthful** (dual-path), and the other 11 specs are
   untouched. The dead-detection carve-out is unaffected (it keys off the
   executors dict).
2. `daemon/api.py` — the boot posture line now carries
   `executable_specs=` (count + job ids). **Additive**: both pinned
   substrings (`"...registrations=%d"`, `"specs (execute_fn withheld);
   use_engine_flags=%s;"`) are unchanged, so no existing pin moved.
3. `tests/unit/services/test_engine_diff_audit.py` — the ownership map
   gained **one** row for a **FOREIGN pre-existing path**
   (`daemon/tests/test_inject_allowed_models.py`, governor Phase-3
   `fc3c8ad1b`) that reddened the release-blocking Leg E gate at this
   lane's base. `BASE_SHA` is **UNCHANGED** (never widened). This lane's own
   `daemon/` diff adds **no** new path (`boot.py` and `api.py` were
   already declared rows).

**Delivered by this lane (new):** the runtime parity pack (32 nodes), the
truth table (15 nodes), the spec-truthfulness pins (7 nodes), the soak
harness, the boot-SLO script, and the two planning artifacts.

**NOT done by this lane (and not this lane's work):** the migration seam
itself (already landed, already live and dormant), any promote / stage /
tag / merge / push, any live flag flip, any daemon restart, any write to the
live install or the prod DB, and the JobLockSweep spec (the ruled second
migrant — out of scope).
