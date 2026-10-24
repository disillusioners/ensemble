# Phase 4 evidence — the coder lane's records for the TESTER (T4.4 / T4.7 / T4.8)

Lane: `fix/nest-ops-shakedown-defects`. Written at Phase-4 close (2026-10-24).
The FULL 12-cycle mutation matrix (T4.4) and the net-new ladder runs (T4.7)
are the TESTER's; this doc records what the coder lane MEASURED, the exact
commands to re-run, and the pin/mutation map.

---

## 1. T4.8 — the scan-cost MEASUREMENT (D-20's arbiter) — MEASURED, not estimated

Fixture: the TWO-TIER >window fixture on real PG16 (`127.0.0.1:5432`) —
2 real-spawn husks (12d/16d backdated) + 1,001 production-ORM filler rows
= 1,003 rows. Mechanism (N-7): an in-process SQLAlchemy
`before_cursor_execute` event-listener counter on the manager's engine;
per-window attribution keys on the repository list-path page reads
(`SELECT instances.instance_id, instances.project_id, … FROM instances …
OFFSET …`) and slices the statement sequence between consecutive page
reads. No DB proxy, no `pg_stat_statements`.

| Measure | Value |
|---|---|
| fixture rows | 1,003 |
| statements_total (one full `_build_health_report`) | **1,012** |
| pre-loop fixed statements | 2 (1 job-active global + 1 COUNT) |
| windows executed (page reads) | 3 (two data windows + the empty terminator) |
| **per-window counts** | **[1002, 5, 3]** |
| **per-window max** | **1002** |
| D-20 gate (`FLEET_SCAN_LIMIT + 2`) | 1002 |
| threshold (i): per-window ≤ gate | **PASS — window 1 sits EXACTLY at the gate** |
| wall-time (full scan) | **3.2–3.6 s** (multiple runs) |
| budget (`FLEET_SCAN_TIME_BUDGET_SECONDS`) | 10 s |
| threshold (ii): within budget OR the budget path fired | **PASS** (within budget; `scan_stop_reason="end_of_set"`) |
| threshold (iii): `scan_truncated=false` only at true end-of-set | **PASS** (`end_of_set` + `truncated=false`; both husks covered) |

Composition of window 1 (1,000 rows): 1 page read + 1,000 per-row child
lookups + the next window's COUNT = 1,002. This is EXACTLY the arch §4
arithmetic — the A2 N+1 (one `list_child_ids_permanent` per row) is the
disclosed cost; batched children (`list_child_ids_permanent_bulk`, ~20
lines / 2 files) remain the recorded first-class follow-up. The gate holds
because the N+1 is **bounded at exactly one query per row** — a second
per-row query of any kind would exceed the gate and FAIL threshold (i).
The tail window (3 rows) carries 5 statements (1 read + 3 lookups + its
COUNT) and the empty terminator carries 3 (1 read + the 2 batched
husk-evidence queries).

(The COUNT is the term the original composition sentence omitted, and the
reason it is easy to omit: the repository's list path issues
`SELECT count(*) …` BEFORE its page read, and the attribution slice for
window N runs FROM window N's page read UP TO window N+1's page read — so
window N+1's COUNT lands INSIDE window N's slice. The FIRST COUNT is the
one that lands before the first page read, which is why the pre-loop row
above reads "2 (1 job-active global + 1 COUNT)". Re-measured against the
live fixture on 2026-10-25: PRE-LOOP = [job-active, COUNT], window 1 = 1
page read + 1,000 child lookups + COUNT, window 2 = 1 page read + 3 child
lookups + COUNT, terminator = 1 page read + the 2 batched husk-evidence
queries.)

Re-run:

```bash
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT \
    timeout 300 .venv/bin/pytest \
    tests/integration/test_nestops_husk_fleet_real_pg.py::TestT48ScanCostMeasurement \
    --override-ini="addopts=" -m integration -q -s -p no:cacheprovider
```

(the `-s` prints the full T4.8 JSON record).

## 2. T4.7 — the CORRECTED net-new ladder (D-21) — labels VERIFIED FROM GIT

`git rev-list --parents -n1 cdf348057` → `cdf348057 dfed5926f 074833b45` ⇒
**M^1 = `dfed5926f`** (proven, not from prose). Labels:

| Leg | Range | Attribution |
|---|---|---|
| **leg 0** | `b257842c9` vs **`dfed5926f`** (M^1) | **OURS (Phase 1)** |
| **leg A** | **`dfed5926f`** vs **`cdf348057`** (M) | **MERGED LANES** (+ conflict resolution) |
| **leg B** | **`cdf348057`** vs **HEAD** | **OURS (Phase 3/4)** |
| **containment** | `b257842c9` vs HEAD | overall `net_new = ∅` |

### Exact leg commands (IDENTICAL commands + pack list on every leg)

Per leg: a DETACHED worktree at the leg's base SHA, `uv sync --frozen`,
byte-matched interpreter, then:

```bash
# one leg, from a detached worktree at <BASE_SHA>:
git worktree add --detach /tmp/ladder-<LEG> <BASE_SHA>
cd /tmp/ladder-<LEG>
uv sync --frozen
# the pack list (the blast-radius packs + the extended watch-list):
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT \
    PYTEST_TIMEOUT=280 timeout 300 .venv/bin/pytest \
    tests/unit/test_instance_read_shakedown_defects.py \
    tests/unit/test_instance_ops_behaviour.py \
    tests/unit/test_instance_ops_delegation_chain.py \
    tests/unit/test_instance_ops_registration.py \
    tests/unit/test_instance_ops_prompt_layer.py \
    tests/unit/test_instance_ops_dismiss_service.py \
    tests/unit/test_pause_never_dispatched_ghosts.py \
    tests/unit/tools/test_frozen_tool_name_discovery.py \
    tests/unit/test_manager_list_instances_source.py \
    tests/api/test_list_instances_source_param.py \
    tests/api/test_instance_ui_prefs_api.py \
    tests/test_job_queue_tools.py \
    tests/postgres/test_df4_provenance_closure.py \
    tests/unit/tools/test_df4_sweep_reserved_fallback.py \
    --override-ini="addopts=" -q --tb=short -p no:cacheprovider \
    --timeout=280 2>&1 | tee /tmp/ladder-<LEG>.log
# extract the FAILED set, sorted:
grep -E '^FAILED' /tmp/ladder-<LEG>.log | sort > /tmp/ladder-<LEG>.failed
```

Diff (on SORTED SETS, never counts):

```bash
comm -13 /tmp/ladder-<BASE>.failed /tmp/ladder-<HEAD>.failed   # net_new
comm -23 /tmp/ladder-<BASE>.failed /tmp/ladder-<HEAD>.failed   # fixed
```

Plus the PACKS (identical on every leg):

```bash
timeout 240 bash test/packs/instance_tools_unit_test.sh     # legacy instance-category pack
timeout 330 bash test/packs/nest_ops_shakedown_unit_test.sh # this patch's pack (legs A/B/containment only — the files do not exist pre-Phase-3; record as N/A on leg 0/A)
timeout 300 bash test/packs/self_heal_prefix_worktree_test.sh 2>/dev/null || true  # merged-lane packs recorded under leg A
```

Load-sensitive reds: 3× isolated re-runs per side. The
pre-existing base debt ALREADY proven by the Phase-2/3 coder in throwaway
legs (do NOT re-chase): `tests/test_job_queue_tools.py::TestResolverRoutedTools`
(4 red) and `test_orphan_watcher_cancelled_on_startup_sweep` (1 red) fail
at BOTH `dfed5926f` and `cdf348057`.

Watch-list reminders (from D-21/arch §11.1): any pin asserting the
`instance_list` PAYLOAD must be RE-DERIVED (F4/F12 change payload VALUES),
not merely re-run; the merged-lane packs (self_alarm, wedge-capture) are
recorded under leg A, never excused.

## 3. T4.4 — the mutation log (coder-lane spot-checks + the full matrix map)

The coder lane ran TWO spot-check mutation legs (the commission's
allowance) to prove the pin design. The FULL 12-cycle matrix is the
TESTER's, per the tables in read-side §9.3 + ops-side §7.2 + arch §11.3
(+ the PLANNED MUTATION docstrings now seeded in every pin class —
verified/extended this phase: the read-side unit file carried 12; the
Phase-3 ops-side files carried NONE and now carry class-level blocks for
F6/F7/F9/F10 and the F11 module).

### Coder-lane spot-check legs (executed 2026-10-24, measured)

| Leg | Mutation (surgical) | Predicted RED | Measured RED | Restore |
|---|---|---|---|---|
| SP-1 | `daemon/tools/instance_read.py::_build_health_report`: break after window 1 (the single-window regression) | `TestF3xF4CouplingRealFleet::test_both_husks_flagged_over_the_window_fleet` + `TestT48ScanCostMeasurement::test_scan_cost_measured` | **exactly those 2 RED** (2 failed) | GREEN (2 passed) |
| SP-2 | `daemon/tools/job_queue.py` `job_create`: `dispatch_chain = None` at the mint site (S2) | `TestF11DelegationChainE2E::test_f11_a_genuine_chain_admits_end_to_end` | **exactly that 1 RED** | GREEN |

Both legs: plant → RED exactly as predicted → restore → GREEN; worktree
porcelain clean after restore (`git diff` empty).

### The full matrix the tester executes (pointer, not duplication)

* F3: age-leg deletion / JobItem-leg drop / `last_activity_at` neuter /
  fresh-spawn threshold / six negative evidence fixtures / canonical-filter
  bypass — read-side §9.3 + `tests/unit/test_instance_read_shakedown_defects.py`
  class docstrings (the real-PG coupling pin is the decisive F3×F4 leg — SP-1).
* F2 (SPLIT): (a) windowed-loop revert → coverage pin; (b) independent-total
  drop → `reconciled` pin; (c) hard-coded `scan_truncated=True` → the
  literal-`False` pin.
* F4: (a) force `truncated=False`; (b) break the loop break-condition.
* F5: break `dead_jobs_total == Σ per-queue dead`; remove the docstring's
  two-table distinction.
* F8 (SPLIT): (a) restore `manager.get_instance` in the resolver → the
  MCP-discovery spy fires; (b) altered-row return → the row-byte-identical
  pin.
* F1: remove one registry-list entry → the frozen-name drift pin.
* F9: restore the old nudge-recommending copy (N-4).
* F6: two fork legs + the F6-iv guard-order swap (ops-side §7.2, D-8-corrected).
* F7: un-single-home the reflection → static-marker case RED.
* F10: drop the echoed row id → all three RED.
* F11: mint-site drop (SP-2), S3-pop removal (b4), factory mis-bind,
  expiry-compare removal, depth-cap removal. F11-b5 ANCHOR-NEUTRAL —
  declared, NOT counted as coverage.
* F12: remove the `status` forward at the lifecycle→repo seam → the
  filtered-page pin RED.

Anchor-neutral pins, declared: F11-b5 (legacy chainless row) and the
`TestEnvironmentFence::test_ambient_snapshot_records_the_poison` snapshot
pin (trivially satisfied when the ambient var is absent).

## 4. The fixture architecture (as built — for the tester's leg runs)

* **T4.1** `tests/helpers/nestops_pg_fixtures.py`: `DisposableDB` =
  throwaway LOGIN role (`ens_shakedown_<10hex>`) + `CREATE DATABASE …
  OWNER` on `PG_TEST_HOST/PORT` (default `localhost:5432`); drop =
  `pg_terminate_backend` + `DROP DATABASE … WITH (FORCE)` (older-PG
  fallback) + `DROP ROLE`; denylist + positive scratch-shape rule (the
  DF-4 fence); ambient `POSTGRES_*` captured at import and pinned
  per-variable in-fixture (never `env -u POSTGRES_*`).
* **T4.2 tier 1**: `manager.spawn_instance_with_mcp(instance_id=<exact
  UUID>, agent_id="maintenancer")` for
  `5ba21b2d-43b7-4378-be53-cca781f9c124` (12d backdate) and
  `7ccd53c3-2448-4f1f-9bb2-969ee9d1f37f` (16d backdate) — the FULL REAL
  spawn order; ONE synthetic touch each (the backdate UPDATE, production
  ISO format).
* **T4.2 tier 2**: 1,001 rows via the production `Instance(...)`
  constructor (`_spawn_instance_db_sync`'s exact kwargs), ONE session
  (`add_all` + `commit`), `agent_id="worker"` (non-KB), microsecond-spaced
  production-ISO timestamps. Result: the husks land at offsets 1001/1002
  of `created_at DESC` — OUTSIDE the first 1,000-row window (asserted).
* Cost: DB+schema ≈ 4 s; manager ≈ 3 s; first real spawn ≈ 7 s (MCP
  preload); second spawn ≈ 1.2 s; filler ≈ 0.35 s. Module-scoped: built
  ONCE per pytest invocation.
* The ops-side file: module-scoped disposable DB + per-test TRUNCATE
  (the `tests/postgres` pattern) + function-scoped REAL manager;
  `manager._task_repo = TaskRepository(engine)` wired in-fixture (the
  `pool_orchestrator.py:245` step the declared no-worker-pool harness
  replaces).

## 5. Pack record

`test/packs/nest_ops_shakedown_unit_test.sh` — clean invocation
**38/38 PASS in 135.9 s** (layer-2 cap 300 s). Registered in
`.agents/tester/PACKS.md` (newest-first commission section). Carries the
`.venv` in-worktree assertion (arch §11.5 gap) and the corrected
`ensure.md` citation (`:6` + `:45`).

## 6. Deviations (with authority)

1. **T4.8 per-window attribution upgraded from average to EXACT** (the
   plan said "counting statements per scan window"; an average over 2
   windows would read 506 and mask a real violation — the gate is tight,
   window 1 sits exactly at 1002). The exact attribution keys on the page
   reads and is recorded here + in the pin. No plan contradiction: D-20's
   threshold is per-window; the measurement now measures per-window.
2. **The b4/b5 e2e dispatches ride the agent lane** (`agent:ari`) instead
   of the REST `api` source: an `api`-sourced dispatch mints a DIRECT
   window on the child via the F-6 REST-ingress residual (cn `257488dc`,
   open, explicitly NOT closed by F11) — asserting through it would test
   the residual, not the S3 pop. The forged-metadata vector (the REST job
   row source) is unchanged; the declared harness shape (plan T4.3) is
   unchanged. Disclosed here + in the pin docstrings.
3. **`manager._task_repo` wired in-fixture** (one attribute; the
   `setup_worker_pool` step) — required by the declared no-worker-pool
   claim path. Declared in the fixture docstring.
4. **Phase-3 ops-side pin files had NO seeded mutation docstrings**
   (the commission's "Phase 1/3 already seeded them" was true only of the
   read-side file) — extended: class-level PLANNED MUTATION blocks added
   to F6/F7/F9/F10 (`test_instance_ops_behaviour.py`) and the F11 module
   docstring (`test_instance_ops_delegation_chain.py`). Doc-only; 85/85
   still green.
