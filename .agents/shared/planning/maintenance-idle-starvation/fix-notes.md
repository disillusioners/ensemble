# Maintenance idle-starvation composite — fix notes

- **Board item:** `wi-4d1fa093` (maintenance starvation composite)
- **Branch:** `feature/maintenance-idle-starvation` (base `origin/latest` @ `c36c3e07c`)
- **Diagnosis (binding, not re-derived):** job `9c2b0126`, 2026-11-18
- **Implemented:** 2026-11-19, worktree `/home/nea/ensemble-src-wt-maint-starve`

## The fault (one paragraph)

`MaintenanceService._is_idle()` was a SINGLE GLOBAL gate applied to
every registered job. On a continuously busy nest it was never
satisfiable (live: 25/25 hourly buckets non-idle, zero
`maintenance_runs` rows for 4d10h after 2026-11-14T12:44:30Z), so
all eleven registered maintenance jobs starved — including the bounded,
idempotent ones whose work does not compete with live traffic. Two
secondary faults compounded it: the skip path never wrote `last_run`
(jobs stayed permanently due) and `last_run` lived ONLY in memory (a
restart reset the clock — exactly the 2026-11-14 artifact). The A/B
resolve path (`skill_evolution_service.py`) had no lock and no CAS:
three non-atomic `asyncio.gather` transactions.

## (A) The idle-policy classification table

The table lives in `daemon/services/maintenance.py`
(`JOB_IDLE_POLICIES`) and is the SINGLE source of truth; an
unregistered job name is treated as `IDLE_GATED` (conservative — an
unknown job never runs unattended).

**Principle:** the idle gate is retained only for work that genuinely
competes for the resources live traffic holds (the DB write path /
WAL / IO bandwidth an in-flight agent turn needs). Bounded, idempotent
bookkeeping, in-process work, and dispatch-only work do not contend
and run un-gated.

| # | job | policy | `max_staleness_hours` | rationale |
|---|-----|--------|------------------------|-----------|
| 1 | `checkpoint_cleanup` | **IDLE_GATED** | 24.0 (persisted) | Deletes from the checkpoint tables (28 GB / 137,598 rows live) + `ANALYZE` — it competes for exactly the write path live checkpointing needs. The persisted guarantee gives it its eventual execution. |
| 2 | `vacuum_maintenance` | **IDLE_GATED** | 24.0 (persisted) | Plain `VACUUM`'s whole contract is "do not run under load". The persisted guarantee gives it its eventual execution. |
| 3 | `room_gc_sweep` | UN_GATED | — | The selection predicate is DORMANCY-based (closed > 14d, idle > 72h, zero living participants) ⇒ the write set is disjoint from live traffic's rows; one transaction per room. |
| 4 | `skill_metric_scan` | UN_GATED | — | Bounded metric aggregation over the skill usage records. |
| 5 | `skill_orphan_sweep` | UN_GATED | — | Bounded delete of usage records that escaped finalization. |
| 6 | `skill_ab_inactivity_sweep` | UN_GATED | — | The ONE sanctioned resolution mechanism's periodic leg. Un-gated **only because** the resolve path is now atomic + CAS-guarded (the CAS landed FIRST — commit `292f5f13c`). |
| 7 | `project_skill_gc` | UN_GATED | — | Bounded purge of a deleted project's skill files + embeddings. |
| 8 | `blueprint_daily_scan` | UN_GATED | — | Dispatch-only: it enqueues rebuild work into the NORMAL worker queue (the same path a user-dispatched job takes). |
| 9 | `malloc_trim` | UN_GATED | — | In-process glibc heap trim — no DB, no contention. |
| 10 | `dependency_bus_lock_reap` | UN_GATED | — | In-memory lock-map reap — no DB, no contention. |
| 11 | `anomaly_retention` | UN_GATED | — | Bounded batched age DELETEs over the anomaly tables (its own docstring already disclosed the starvation risk). |

**Census:** 11 registered jobs — **9 UN_GATED**, **2 IDLE_GATED** —
and the two that keep the gate are exactly the two that carry the
persisted `max_staleness_hours` guarantee. That invariant is now
STRUCTURALLY pinned (review cycle 1, item 1): a test derives "every
`IDLE_GATED` job must have a non-`None` guarantee" from the policy
table, so a future 12th job that defaults to `IDLE_GATED` without a
guarantee REDDENS instead of starving silently.

**Ordering constraint honoured:** the CAS (`292f5f13c`) landed BEFORE
the un-gating of `skill_ab_inactivity_sweep` (`c5a4b95c0`).

## (B) The persisted guarantee + the restart-clock invariant

**Persisted `max_staleness_hours`** — the anti-starvation escape
hatch: a job that has not even ATTEMPTED for longer than
`max_staleness_hours` runs EVEN when the system is busy. Defaults
(24.0h, the two reclaim jobs) live in `JOB_MAX_STALENESS_HOURS`; the
PERSISTED value wins over the default; a persisted `0` disables the
guarantee for that job. An override is accepted ONLY for an
`IDLE_GATED` job (see the (D) accuracy note and disclosure (c)) — the
guarantee is the escape hatch from a gate the job actually waits on,
so it is refused for any `UN_GATED` job.

**Storage surfaces — EXISTING ONLY, ZERO new migrations:**

| what | where | why |
|------|-------|-----|
| last-success | `maintenance_runs` (the Section-1 audit table; migration `20260927_000001_create_maintenance_runs_table.sql`) — `status='succeeded'` **AND `kind IN ('auto','manual_execute')`** (AM-9: `manual_dry_run` NEVER surfaces — it mutates nothing), `ORDER BY completed_at DESC LIMIT 1`, per `section` (`checkpoint-cleanup` / `vacuum-maintenance`). Same kind vocabulary as `MaintenanceRunsRepository.latest_completed_for_section`. | the audit table already records every reclaim run's outcome |
| `max_staleness_hours` override | the project-metadata KV (`maintenance.job_state` key on the system-default project `71931ae0-0f25-5fbf-853b-2a78cc978d7e`) | the SAME existing KV the blueprint daily scan uses for its persisted last-run (`blueprint_scan_last_run`) |

The seam is `MaintenanceStateStore` (3 sync methods) + the
production implementation `DbMaintenanceStateStore`
(`daemon/services/maintenance.py`, Section 0b).

**THE RESTART-CLOCK INVARIANT** — `last_run` used to live only in the
in-memory job dict, so every restart reset the clock. Now:
`MaintenanceService.start()` calls `hydrate_persisted_state()` BEFORE
the first tick; the persisted last-success becomes the source of truth
and the in-memory value is only its cache. A job that succeeded 1h ago
(with a 24h interval) is NOT due after a restart; a job whose last
success is 30h old IS due and runs even on a busy system.

The staleness reference clock is `last_success_ts` → `last_run` →
the process-start anchor (so a fresh install gets its guarantee
measured from boot instead of firing heavy work instantly under load).

## (CAS) The atomic A/B resolve

`SkillABTestRepository.resolve_atomic(...)` claims the pair row with a
compare-and-set UPDATE and applies ALL THREE terminal mutations inside
ONE transaction:

- **CAS predicate:** `resolved_at IS NULL` ALWAYS, plus
  `comparisons = 0` on the `stalled` basis (the zero-traffic guard).
- **CAS loser:** NOTHING is mutated (the transaction is rolled back) —
  exactly-once resolution. The service reports `resolved=False`,
  `reason="cas_lost"` (and the sweep counts it).
- **Mid-transaction failure:** the session context manager rolls back
  — ZERO partial mutations. The service reports
  `resolved=False`, `reason="resolve_failed"`.

**Explicit deviation (flagged, not silent):** the spec's predicate is
`WHERE resolved_at IS NULL AND comparisons = 0`. It is applied on the
`stalled` basis (the sweep's path — where `comparisons == 0` is
already enforced upstream). The `defective_arm` basis (the operator
naming a VERIFIED-defective member, possibly of a traffic-bearing
pair) keeps `resolved_at IS NULL` alone: forcing `comparisons = 0`
there would make the C1 retirement a permanent no-op. Both bases get
the full atomicity + exactly-once guarantee.

**The counter itself is OUT OF SCOPE** (known-broken: 8/10 stalled
pairs have real selections with `comparisons = 0`) — no counter
semantics were touched.

## (D) Skip-streak telemetry

- The FIRST skip of a streak logs at **WARNING**, carrying the
  consecutive-skip count and the last-success timestamp.
- Every LATER skip of the same streak logs at **DEBUG** (the streak is
  the signal; an hour of identical WARNING lines would only dilute it).
- The counter resets on the next success.
- One metric block per job: `MaintenanceService.job_metrics()` →
  `{job_name: {"last_success_ts": <ISO-8601|None>, "consecutive_skips": <int>}}`.

### (D) Accuracy note — review cycle 1 (item 2)

The "running despite a busy system" WARNING is the message that tells
an operator `max_staleness_hours` just overrode a real gate. It is now
emitted **only for an `IDLE_GATED` job**: `forced` is computed as
`gated and self._is_stale(job)`, and `set_max_staleness_hours` REFUSES
an override targeting an `UN_GATED` job (nothing persisted, nothing
applied) while hydration ignores a stale persisted override on one.
Before the fix an `UN_GATED` job carrying an override emitted the
false line on every tick. Pins:
`test_an_un_gated_job_never_logs_the_busy_override_line` (both
defences), `test_a_gated_job_still_logs_the_busy_override_line` (the
truthful counterpart), `test_hydration_ignores_an_override_for_an_un_gated_job`.

## Scope & wiring disclosures

Three deliverable claims are deliberately scoped below — the SURFACE
exists in each case, the WIRING is deferred (never overclaim):

**(a) CAS coverage is the SANCTIONED A/B path ONLY.** Four non-CAS
`resolve()` call sites remain, all on paths disjoint from the
sanctioned resolver:

| site | path |
|------|------|
| `daemon/services/skill_deactivation.py:1657` | the deactivation lane |
| `daemon/services/skill_evolution_service.py:1952` | `check_ab_test_resolution` — forced-winner (Path 0) |
| `daemon/services/skill_evolution_service.py:2016` | `check_ab_test_resolution` — threshold met (Path 2) |
| `daemon/services/skill_evolution_service.py:2055` | `check_ab_test_resolution` — max_extensions force-resolve (Path 3) |

Routing the deactivation lane through `resolve_atomic` (and folding
the three `check_ab_test_resolution` paths) is a **NAMED follow-up**,
not part of this lane: those paths carry their own already-resolved
guards and their predicates differ (see "Deliberately NOT done"). The
resident residual risk is upstream, not discovered by this lane.

**(b) `job_metrics()` exists but has NO production consumer yet.** The
surface is implemented and pinned; wiring it into a metrics scrape /
Maintenance Console view is deferred. Relatedly, `last_success_ts`
hydrates only for the TWO gated jobs today — they are the only jobs
that write Section-1 audit rows, so the other nine have no persisted
last-success to read.

**(c) `set_max_staleness_hours` is SERVICE-API-ONLY.** There is no
route and no agent tool calling it, so no operator can change the
value at runtime today; the **effective value is the built-in 24.0h**
for both gated jobs. The persisted-override path is proven end-to-end
(store round-trip + hydration + refusal semantics) and stands ready
for a future settings/API consumer.

## Files changed

| file | what |
|------|------|
| `daemon/services/maintenance.py` | `IdlePolicy`, `JOB_IDLE_POLICIES`, `JOB_MAX_STALENESS_HOURS`, `JOB_MAINTENANCE_SECTIONS`, the `MaintenanceJob` fields, `register(idle_policy=, max_staleness_hours=)`, `set_state_store`, `hydrate_persisted_state`, `_is_stale`, `_record_skip`, `job_metrics`, `set_max_staleness_hours`, `MaintenanceStateStore`, `DbMaintenanceStateStore` |
| `daemon/services/skill_evolution_service.py` | the sanctioned resolve path now rides `resolve_atomic` (CAS + one transaction) |
| `daemon/repositories/skill/repository.py` | the new `SkillABTestRepository.resolve_atomic` (the `resolve` method is UNCHANGED) |
| `daemon/manager.py` | the state-store wiring; the sweep's `cas_lost` counter |
| `tests/unit/test_maintenance_idle_starvation.py` | the new unit pack (**35 tests** at tip — 30 after the advisory-1 cycle, +5 in review cycle 1) |
| `tests/unit/test_skill_evolution_resolve_atomic.py` | the new CAS pack (**9 tests**) |

## Commits

| sha | subject |
|-----|---------|
| `292f5f13c` | `fix(skill-evolution): atomic CAS resolve for the sanctioned A/B path` |
| `c5a4b95c0` | `fix(maintenance): starvation composite — per-job idle policy, persisted staleness guarantee, skip telemetry` |
| `a517779bd` | `test(maintenance): make the idle-policy mutation red on the behavioural assertion` |
| `b4a4bb89a` | `fix(maintenance): gate the staleness escape hatch on the last ATTEMPT — a failing job must not be force-run every tick` |

Parts (A), (B) and (D) share one file and are interdependent (the
gate consults the staleness override), so they landed as ONE commit
rather than as three artificially-split ones.

### The ATTEMPT clock (`b4a4bb89a`)

The staleness escape hatch references the last ATTEMPT (in memory)
first, then the persisted last-success, then `last_run`, then the
process-start anchor. **Why:** a job that keeps ATTEMPTING (even
failing) is not starved — without the attempt clock, a persistently
failing reclaim job would become "stale forever" and the guarantee
would force it on EVERY 15-minute tick on a busy system (a real
operational risk this composite must not introduce). Pinned by
`test_a_persistently_failing_job_is_not_force_run_every_tick`.

## Verification

All commands run from `/home/nea/ensemble-src-wt-maint-starve` with
the worktree venv and the fenced dev env pinned explicitly:

```
env POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB=ensemble_dev \
    POSTGRES_USER=ensemble_dev_app POSTGRES_PASSWORD=<dev> \
    ENSEMBLE_SELF_ENV=dev DAEMON_PORT=8079 \
    .venv/bin/python -m pytest <EXPLICIT file list> -p no:randomly --tb=short -q
```

| batch | result |
|-------|--------|
| the two new packs (maintenance + CAS) | **44 passed** (35 + 9) — re-run at review-cycle-1 tip |
| the maintenance surface (`tests/test_maintenance.py`, `tests/unit/test_anomaly_retention.py`, `tests/unit/services/test_maintenance_run_lock_and_capture.py`, `tests/unit/test_checkpoint_retention_config_probes.py`, `tests/unit/services/test_malloc_tuner.py`, `tests/integration/test_maintenance_checkpoint_cleanup_api.py`, `tests/integration/test_vacuum_maintenance_job_no_full.py`) | **134 passed**, 47 deselected |
| the skill-evolution surface (9 files, incl. `tests/repositories/test_skill_repository.py`, `tests/unit/test_skill_evolution_lane_gate.py`, `tests/unit/test_composite_scoring.py`, `tests/unit/test_skill_stale_deactivation.py`, `tests/unit/routers/test_skills.py`, `tests/tools/test_skill_evolution_tools.py`) | **427 passed**, 1 skipped |

**Pre-existing failures (NOT caused by this change):**
`tests/manager/test_skill_service_init.py` + `tests/manager/test_phase4_metrics_trigger_init.py`
fail with `sqlite3.OperationalError: near "EXISTS": syntax error`
(migration `20260714_000001` on the SQLite runner). Proven pre-existing:
the SAME 9 failures reproduce on the TRUE pre-fix base extracted with
`git archive c36c3e07c` and run with the identical command.

### Mutation proof (deliverable 3)

Target: `tests/unit/test_maintenance_idle_starvation.py::TestIdlePolicyTiering::test_un_gated_job_runs_even_when_the_system_is_busy`

Mutation applied to `daemon/services/maintenance.py`:
`"skill_orphan_sweep": IdlePolicy.UN_GATED` → `IdlePolicy.IDLE_GATED`

```
FAILED tests/unit/test_maintenance_idle_starvation.py::TestIdlePolicyTiering::test_un_gated_job_runs_even_when_the_system_is_busy
    assert execute_fn.await_count == 1
E   AssertionError: assert 0 == 1
    +  where 0 = <AsyncMock id='125412570610192'>.await_count
------------------------------ Captured log call -------------------------------
WARNING  daemon.services.maintenance:maintenance.py:581 Maintenance job 'skill_orphan_sweep' skipped (system not idle): consecutive_skips=1 last_success_ts=never

FAILED tests/unit/test_maintenance_idle_starvation.py::TestIdlePolicyTiering::test_every_idle_gated_job_carries_a_staleness_guarantee
E   AssertionError: assert {'skill_orphan_sweep'} == set()
E     Extra items in the left set:
E     'skill_orphan_sweep'

FAILED ...::test_a_gated_job_registered_without_a_policy_entry_is_caught
FAILED ...::test_bounded_jobs_are_un_gated
4 failed, 7 passed, 1 warning in 0.41s
```

(Re-run at review-cycle-1 tip, 2026-11-19: the mutation now reds FOUR
pins — the behavioural target plus the three classification pins,
including the new STRUCTURAL guarantee pin from item 1.)

Restore (byte-identical, verified by sha256 of the whole file; the
restore was done from a FILE COPY, not `git checkout --` — see the
note below):

| | sha256 of `daemon/services/maintenance.py` |
|---|---|
| before the mutation (review-cycle-1 tip) | `5ec2fe7cf78d7e919d0c442ac7afea1e5dd325bfc92a6fc66a5bf652efee3611` |
| after the mutation | `5b020b2cae2dfcbd737def7564354ed42f069c1dcb8e0870e65353dad523e050` |
| after the restore | `5ec2fe7cf78d7e919d0c442ac7afea1e5dd325bfc92a6fc66a5bf652efee3611` — **IDENTICAL to before** |

Restored from a saved file copy; tree clean at the restore point.

A second mutation (the static pin) also reds:
A second mutation (the static classification pin) also reds:
`test_bounded_jobs_are_un_gated` →
`Extra items in the right set: 'skill_orphan_sweep'`.

> **Process note (worth carrying forward):** `git checkout -- <file>`
> restores the file to HEAD and therefore discards **ALL** uncommitted
> work in it — during this lane it silently reverted an uncommitted
> refinement (the attempt clock) that had nothing to do with the
> mutation. The correct restore for a mutation is a saved COPY of the
> pre-mutation bytes, or a reverse edit; `git checkout --` is only
> safe on a file that is otherwise clean.

## Deliberately NOT done

- **The comparisons counter** (known-broken, 8/10 stalled pairs have
  real selections with `comparisons = 0`) — explicitly OUT OF SCOPE per
  the spec; no counter semantics were touched.
- **The other two A/B resolve paths** (`check_ab_test_resolution`'s
  forced-winner path and its threshold-met path) keep their three
  non-atomic `asyncio.gather` transactions. They are OUT of the
  specced scope (the spec targets `2396-2407` = the SANCTIONED path),
  the specced predicate `comparisons = 0` cannot apply to them (both
  run with `comparisons >= ab_sample_size > 0`), and existing pins
  (`tests/unit/test_skill_evolution_lane_gate.py`,
  `tests/unit/test_composite_scoring.py`) assert that
  `_ab_test_repo.resolve` is called on those paths.
- **The `app_settings` managed-key registry** was NOT used for the
  `max_staleness_hours` override: adding a managed key requires the
  `constants → LimitsConfig → registry` chain (4 extra files outside
  the stated touch surface) and its resolution ladder is boot-time
  only. The project-metadata KV (an existing KV with a direct
  precedent in the same registration block) was used instead.
- **No live DB writes.** No A/B pair was resolved. The Maintenance
  Console execute path was not run. The upgrade journal, the rollback
  counter and the queue brake were not touched. No release tag or
  release directory was touched.
- **No push / merge / worktree removal** — giter + leader own the
  finalize.

## Verification cycle 1 follow-up (advisory closure)

Independent verification returned 4/4 parts PASS. One moderate advisory
was empirically proven and is now closed; two cheap riders landed.

### Advisory #1 — dry-run contamination of the persisted last-success (FIXED)

`DbMaintenanceStateStore.load_last_success` selected the latest
`status='succeeded'` row of a section **regardless of `kind`**, so a
`manual_dry_run` "succeeded" row (created at
`maintenance_api_service.py:362`, marked succeeded `:473`) could hydrate
as last-success and delay the next auto reclaim by up to
`max_staleness_hours`.

**Fix:** the query now carries `AND kind IN ('auto','manual_execute')` —
the module's own AM-9 convention
(`MaintenanceRunsRepository.latest_completed_for_section`: "`manual_dry_run`
NEVER surfaces"). Both kind values were confirmed from the repository
source and from the two writers (`kind="manual_dry_run"` /
`kind="manual_execute"`), not guessed.

**Mutation-verified:** removing the kind clause reproduces the
verifier's exact finding (`datetime(2026-11-18, 9, 0)` — the DRY-RUN
timestamp — returned where the AUTO timestamp was expected) and both
new pins go RED; byte-identical restore verified
(`0609aaf3281263376651a03e026d3d469a743f05b8a3f9129837a6a2969e9018`
before and after).

New pins (`TestDbStateStore`):
- `test_load_last_success_ignores_a_newer_manual_dry_run_row` — newer
  dry-run + older auto success ⇒ the AUTO timestamp.
- `test_load_last_success_counts_a_manual_execute_row` — newer manual
  execute ⇒ the MANUAL EXECUTE timestamp (a real execute IS a run).
- `test_a_dry_run_only_section_has_no_last_success` — a section whose
  only success is a dry run hydrates to `None`.

### Rider #2 — honest return from `set_max_staleness_hours` (LANDED)

`DbMaintenanceStateStore.save_max_staleness_hours` now raises when the
write did NOT apply (no project repository wired, or `set_metadata`
returned `None` because the system-default project row is absent — a
silent no-op). The caller's existing guard turns that into an honest
`False` **and leaves the in-memory value untouched**, so nothing lies
about persistence. New pins:
`test_set_max_staleness_hours_is_false_when_the_write_noops`,
`test_set_max_staleness_hours_is_false_without_a_repository`.

### Rider #3 — stale idle-gated doc drift (LANDED, comments/docstrings only)

Reworded every remaining claim that a now-`UN_GATED` job is idle-gated:

| site | job |
|------|-----|
| `maintenance.py` class docstring | the global "Jobs only run when the system is idle" claim |
| `manager.py:322` (`PROJECT_SKILL_GC_INTERVAL_HOURS` comment) | `project_skill_gc` |
| `manager.py` room-GC registration comment | `room_gc_sweep` |
| `manager.py` project-skill registration comment | `project_skill_gc` |
| `manager.py` malloc_trim registration comment | `malloc_trim` |
| `manager.py` dependency-bus registration comment | `dependency_bus_lock_reap` |
| `manager.py` anomaly-retention registration comment (quotes the old disclosure, then marks it RESOLVED) | `anomaly_retention` |
| `manager.py` `_run_malloc_trim` docstring | `malloc_trim` |
| `manager.py` `_run_dependency_bus_lock_reap` docstring | `dependency_bus_lock_reap` |
| `manager.py` `_run_skill_orphan_sweep` docstring | `skill_orphan_sweep` |
| `manager.py` `_run_skill_metric_scan` docstring | `skill_metric_scan` |
| `manager.py` `_run_skill_ab_inactivity_sweep` comment | `skill_ab_inactivity_sweep` |

Zero behavior change (comments/docstrings only); `daemon.manager` still
imports.

### Skipped (recorded, not worth churn)

- Advisory #4 (duplicate import), #5 (test docstring says "StaticPool"
  where the file-backed fixture now applies), #6 (fix-notes count nit).
- The capture-observability sink hermeticity nit — recorded only.

## Review fix cycle 1 (council b442854d, 2026-11-19T02:08Z)

Verdict APPROVE-WITH-ADVISORIES (2/2 quorum), zero blocking; the AM-9
fix and the classification were upheld. Four items landed.

### Item 1 — the classification guard is STRUCTURAL now

`test_policy_table_covers_every_registered_job` still pins the eleven
names, but the real recurrence guard is new and DERIVED:
`test_every_idle_gated_job_carries_a_staleness_guarantee` asserts that
every `IDLE_GATED` entry in `JOB_IDLE_POLICIES` has a non-`None`
`JOB_MAX_STALENESS_HOURS` value. A future 12th job that (a) is
registered without a policy entry — so the unknown-name default makes
it `IDLE_GATED` — and (b) has no guarantee entry now REDDENS a test
instead of starving forever. `test_a_gated_job_registered_without_a_policy_entry_is_caught`
covers the registration side of the same shape.

The mutation-red property is INTACT and expanded: flipping
`skill_orphan_sweep` to `IDLE_GATED` now reds FOUR pins (the
behavioural target + the bounded-set pin + both structural pins).

### Item 2 — the busy-override log is truthful

Chosen fix shape: **gate the log on the job's idle policy AND refuse
the override at the setter** (belt-and-braces, rather than one or the
other). One line on why: the log must be truthful even if an override
attribute ever survives on an un-gated job (e.g. a future
re-classification order), while the setter/hydration refusals stop
such an override from existing in the first place — the log fix alone
would leave inert-but-armed configuration on the object.

- `_run_pending_jobs`: `gated = policy is IDLE_GATED; forced = gated and self._is_stale(job)`.
- `set_max_staleness_hours`: refuses (`False`, nothing persisted,
  nothing applied) when the target job is `UN_GATED`.
- `hydrate_persisted_state`: ignores a persisted override on an
  `UN_GATED` job (DEBUG line — inert by design).

### Item 3 — every count in this document re-verified by RUNNING

| command | actual output (review-cycle-1 tip) |
|---------|-------------------------------------|
| `pytest tests/unit/test_maintenance_idle_starvation.py` | **35 passed** |
| `pytest tests/unit/test_skill_evolution_resolve_atomic.py` | **9 passed** |
| both packs together | **44 passed** |
| the maintenance surface (7 files) | **134 passed**, 47 deselected |
| the skill-evolution surface (9 files) | **427 passed**, 1 skipped |

### Item 4 — disclosures

See "Scope & wiring disclosures" above ((a) CAS coverage is the
sanctioned path only, 4 non-CAS `resolve()` sites remain with the
deactivation lane as a NAMED follow-up; (b) `job_metrics()` has no
production consumer and `last_success_ts` hydrates only the 2 gated
jobs; (c) `set_max_staleness_hours` is service-API-only, effective
value = built-in 24.0).

### Leader-deferred (NOT in this cycle, do not fold in)

- Advisory #6 — the `rowcount != 1` conflation branch (fail-closed
  safe today).
- Advisory #7 — the KV blob read-modify-write window.

Both ride upstream as residual risks and were deliberately left
untouched.
