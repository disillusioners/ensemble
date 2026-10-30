# Phase-0 measurement harness — eventloop-sync-DB hot paths lane

**NOT SHIPPED.** Measurement scaffolding committed under
`.agents/tester/harness/eventloop-phase0/` for the
`fix/eventloop-sync-db-hotpaths` lane (board `wi-3f5bea95`). Zero
production code; the daemon is imported and booted read-only.

> **REPAIR 2026-10-30 — HEAD MODE.** The harness as originally committed
> (`d42ba2976`) asserted the defect pins **POSITIVE**: it could only pass
> on a tree where the sync-DB hot paths are still ON the event loop, so it
> **could not pass on the fixed tree** (architect §6, blocking gate
> defects). The harness is now **MODE-AWARE** (`--mode=base|head`), the
> capture predicate is **loop-thread scoped** (the architect's literal
> §6.1 fence), G2 is a **one-sided** band (§6.2), G9 is a
> **per-shape on-loop SET** fence (§6.3), and G4 is a **paired**
> comparison with a STALL count (§6.4). See
> `.agents/tester/RESULTS/2026-10-30-eventloop-harness-head-mode-repair.md`
> for the repair note and the 2×2 truth table.

## What it measures (BASE baseline at the unmodified lane base)

The lane's premise: the FE's hot read paths issue **synchronous DB
round-trips on the event loop** (`daemon/routers/instances.py` calls
`manager.list_instances` directly in the async handler; the per-row
`list_child_ids_permanent` N+1 rides the same frame), and the jobs
enrichment (`/api/jobs` → `list_work` no-limit) saturates the default
executor. The harness baselines exactly the numbers that prove the
phase-1 fix:

| # | Measurement | Instrument |
|---|---|---|
| N1 | FE-shape load: badge 8s forkJoin composite, home 10s BFS(100), jobs page 30s no-limit, per-endpoint floor | concurrent httpx vs an in-process real daemon |
| N2 | wedge-capture stack pin — the **LOOP-thread** section contains the instances-repository frame over sqlalchemy (positive at BASE, must be absent at HEAD) | the daemon's own wedge-capture, knobs tightened via env |
| N3 | loop-lag under load (window-local p50/p95/max + the RAW series for the paired STALL-count comparison) | the daemon's own `LoopLagProbe` via a harness-only route |
| N4 | per-request SQL round-trips + **per-shape on-loop attribution** (flat=3+N, BFS=4+levels+rows, defer-blocked=2+holders, missions=3) | SQLAlchemy `before_cursor_execute` listener on the manager engine; requests tagged via `X-Evloop-Tag` → contextvar |
| N2b | response `body_sha256` + `body_len` per request (+ optional raw bodies) — the G5 byte-diff artifact | the harness ASGI middleware |
| N5 | expected-collected-count fence on EVERY gate (a gate that collected nothing FAILS, never passes) | `Fences` collector; exit 1 on any miss |

## The MODE switch (REQUIRED, mutually exclusive)

```
--mode=base    the defect IS present  → every defect pin asserted POSITIVE
--mode=head    the defect is ABSENT   → every defect pin asserted NEGATIVE
```

There is **no default**: a silent default would let a base-mode run be
read as a HEAD gate run. A mode never changes *what* is measured — only
which side of the defect the assertions expect. The predicates are
shared, so the same instrument decides both verdicts.

| Gate | `--mode=base` (the positive control) | `--mode=head` (the fix) |
|---|---|---|
| **G1** family pin | ≥1 capture with the instances-repo frame **on the loop thread** | **0** captures with it on the loop thread |
| **G1** narrow pin | ≥1 capture with `list_child_ids_permanent` + the router on the loop thread *(full profile; smoke reports)* | **0** captures |
| **G1** non-vacuity | ≥1 capture **and** ≥1 capture that actually contains the loop thread **and** the predicate discriminator self-test — in BOTH modes | same |
| **G2** loaded flat p95 | the leg sits **outside** the healthy band (the defect is measurable) *(full profile; smoke reports)* | p95 **≤ 0.230 s**, one-sided (a value **below 0.067 s passes** as an improvement) |
| **G4** loop lag | paired vs the BASE reference (report-only without one) | paired vs the BASE reference — **required**, and the reference's **provenance is asserted** (a real `base`-mode run, not simulated, tree recorded); STALL count, max (no tolerance), p95 + one tick, sample non-vacuity |
| **G9** on-loop | the instances/missions shapes put **every** round-trip on the loop; the jobs shapes put **none** | per-shape on-loop **SET equals the disclosed residual**; every other shape **empty** |
| **G9** round-trips | the BASE census holds **exactly** (3+limit, 4+levels+rows, 2+holders, 3) | **O(1)/bounded** (plan §(d) upper anchors) |

### Why the loop thread is identified by `asyncio/base_events.py`

faulthandler's `Thread 0x…` header carries a **pthread id**, a different
id space from the kernel TID in `threads.csv.native_id`. Matching the two
is an id-space error; the architect's literal fence identifies the loop
thread by the presence of `asyncio/base_events.py` in the section's
stack, which is stable and data-derived.

### Why the family pin ignores the bridge thread

At HEAD the **bridge** thread's stack carries
`daemon/repositories/instance/repository.py` over sqlalchemy (the new
batch method lives in that file). A thread-agnostic predicate therefore
reads POSITIVE on a **correct** fix — a near-certain false red
(architect §6.1). Only the LOOP thread counts; the thread-agnostic count
is still reported as `*_DIAGNOSTIC` for transparency.

Other shapes covered: `/api/work`, `/api/jobs?…&limit=10`,
`/api/jobs?limit=100`, `/api/queues/defer-blocked`,
`/api/missions?liveness=…&limit=20`.

## UNIT CLASSES (round-2 Blocking 1) — declared, never implicit

| Quantity | Unit class |
|---|---|
| `statements_by_shape[shape].count` / `.on_loop` | **sample-summed** (raw aggregate) |
| `statements_by_shape` **as the gates read it** (`modes.per_request_by_shape`) | **per-request** — the ONE normalization site, at the gate boundary |
| `round_trips.mean`, `on_loop_round_trips.mean` | **per-request** |
| `HEAD_ON_LOOP_RULES` (exact counts, `max_on_loop`) | **per-request** |
| `HEAD_ROUND_TRIP_MAX` bounds | **per-request** |
| the BASE census formulas (3+limit, 4+levels+rows, 2+holders, 3) | **per-request** |
| G5 normalized body hash | per-body (one request per shape) |
| G5 `jobs_active_badge` non-regression | **per-request** |

The pre-repair gate compared **sample-summed** counts against **per-request**
constants — the council measured missions `{20,10}` over 10 samples vs the
pinned `(2,1)`, a shape the lane never touches failing deterministically
forever. `modes.per_request_by_shape` divides by the sample count exactly
once, and the simulation writes the **summed** form back, so a real run and a
simulated run go through the SAME single normalization path.

## G5 — the response-bytes gate (round-2 increment)

Two parts (plan §(e) + architect §5.2), over **9 requests = the 8 censused
shapes + one `/api/instances/{id}` DETAIL** (the detail path shares the
ordered children source):

* **(i) normalized byte equality** vs the paired reference — the
  children-array order is normalized **post-processing** on the parsed
  payload (the middleware hashes raw bytes and has no hook), then both sides
  are re-serialized with canonical separators and hashed. Children arrays
  carry child **ids** (the measured API shape, `instance_lifecycle.py:5115`).
* **(ii) the positive children-order assertion** — HEAD's children must be
  exactly `parent_id ASC, instance_id ASC`.

**A measured retraction.** The BASE-side "unordered" control is impossible on
this host: `list_child_ids_permanent` has **no ORDER BY** (verified
statically, `repository.py:434-437`) yet the planner returns rows in
primary-key order, so children arrive **already sorted** — measured 130/130
BFS arrays and 1/1 detail array canonical at BASE, under **both** ascending
and **reversed** sibling insertion orders (the `seed.py` G5 discriminator
experiment, kept in the fixture). The order is contractually unspecified but
behaviorally sorted today; asserting "unordered" would red a non-defective
tree. Part (ii) is therefore **asserted at HEAD only** (the fix makes the
order a contract), and the unspecified-order half is carried by §5.2
correction 3's **static SQL ORDER BY pin** in the pin pack — reported here,
never asserted at BASE.

**Determinism.** `seed.py` pins the fixture clock (`FIXTURE_EPOCH`,
env-overridable for a deliberate re-baseline) so two runs of the same tree
produce byte-identical bodies: measured, all **9/9** normalized hashes
identical across two independent smoke runs.

**jobs_active_badge** is in the canonical 8-shape set (the plan froze 7). The
caller sends no limit param, so C-4 cannot bound it without a semantics
change: it is an **ENUMERATED RESIDUAL**, gated on **non-regression only**
(HEAD ≤ BASE, per-request), never on an absolute bound.

## The SIMULATION (`--simulate=head`) — read this before quoting a number

`--simulate=head` transforms the **measured** evidence of the tree under
test into the evidence the FIXED tree would produce, using the fix's
published semantics. It is **NOT a production fix and NOT a
measurement**. It exists only so HEAD mode can be exercised *before* the
developer's fix lands.

Every transformed quantity carries its rule and its derivation
(`report.simulation.provenance.rules`), e.g.

```
S2.instances: count' = 13 - 10 children (rows, measured) + 1 batched children select = 4
S2.batch:     count' = (1873 - 1869 per-row instance lookups) + 3 batched selects = 7
S2.g2:        the loaded flat-leg p95 913.69 ms is replaced by the MEASURED
              unloaded floor p95 67.8 ms of the same run
S4.lag:       every sample clamped to the measured tick floor p50=0.4990 s
```

A simulated run is labelled three ways: a loud banner, a
`BASELINE.<profile>.<mode>.simulated.json` filename, and
`report.simulation.applied = true` with a disclaimer. It can never be
mistaken for gate evidence.

## Entrypoint (re-runnable; the tester gate RE-RUNS this at BASE and HEAD)

```bash
# BASE mode — the positive control (also produces the G4 reference)
bash .agents/tester/harness/eventloop-phase0/run_all.sh \
     --mode=base --profile=full

# HEAD mode — REQUIRES the paired BASE reference
bash .agents/tester/harness/eventloop-phase0/run_all.sh \
     --mode=head --profile=full --reference=raw/BASELINE.full.base.json

# the 2x2 truth table, sequential (cells share one host ⇒ never parallel)
bash .agents/tester/harness/eventloop-phase0/run_truth_table.sh --profile=full

# the fast instrument check (no daemon boot, seconds)
python3 .agents/tester/harness/eventloop-phase0/selftest.py
```

Outputs land in `raw/`:
* `BASELINE.<profile>.<mode>[.simulated].json` — mode-suffixed, so a
  mode-aware run can never overwrite the committed **pre-repair**
  baseline artifact `BASELINE.full.json` (kept as the historical record)
  nor a run of the other mode;
* `run_<profile>.<mode>[.simulated].out`;
* per-run workdirs (`run-<ts>/`, gitignored) incl. the wedge captures.

### Profiles and the scale-dependent gates

The `full` profile is prod-shaped (~3.3k instances / ~5.3k JobItems).
`--profile=smoke` exercises **every measurement path** at 1/10 scale.
Three gates are **scale-dependent** and are asserted on `full` /
reported on `smoke` (each prints a loud `[fence:INFO]`):

| Gate | Why it needs `full` |
|---|---|
| narrow pin (BASE) | the N+1 dwell is only long enough to be caught at scale |
| G2 non-vacuity | the architect requires ≥5 badge ticks; the smoke window is 2 |
| G2 band | the flat leg only queues behind multi-second loop blockers at scale (measured 0.139 s at smoke vs **1.1027 s** at full — the committed cell-1 `loaded_flat_p95_s`; the earlier round-1 figure 0.914 s was a hardcoded string, corrected 2026-10-30) |

Everything else — the family pin, the on-loop attribution, the
round-trip counts, the predicate self-test — is asserted on **both**
profiles.

## The daemon child

`child.py` boots the REAL daemon in-process (`create_app()` + manual
`lifespan` + uvicorn on a free TCP port — the
`tests/helpers/soak_gate_daemon_child.py` shape) and layers
harness-only instrumentation AFTER `create_app()`:

* a pure-ASGI tagging middleware (`X-Evloop-Tag` → contextvar; records
  latency, status, `body_sha256`, `body_len`);
* `/harness/lag`, `/harness/counters`, `/harness/loopinfo` routes;
* the N4 event listener on `manager._engine` (per-request **and**
  per-shape on-loop attribution).

The capture ROOT is `ENSEMBLE_DATA_DIR`-derived: the real boot passes
`data_dir` to `WedgeCaptureConfig.from_env`, which WINS over
`ENSEMBLE_WEDGE_CAPTURE_DIR` — so the env var alone cannot redirect it.
The harness pins `ENSEMBLE_DATA_DIR` at a mktemp tree instead
(documented gotcha; no production change needed).

## Fences (binding)

* **Scratch PG only** — throwaway LOGIN role + owned DB created and
  DROPped by the harness (`common.create_scratch_pg` /
  `drop_scratch_pg`, the soak-gate `soak_pack_scratch_db` shape). The
  child REFUSES to boot unless the DB name is scratch-shaped.
* **Per-variable env scrub** — `run_all.sh` unsets every
  `POSTGRES_*`/`PORT`/`DAEMON_PORT` variable **BY NAME** before exec
  (`env -u POSTGRES_*` is a silent no-op; never used), and
  `common.assert_clean_env()` re-checks in-process. The child env is then
  constructed explicitly (POSTGRES_* pins, `ENSEMBLE_SELF_ENV=demo`,
  `ENSEMBLE_AUTH_ENABLED=false`, `MCP_DISABLE_BUILT_IN_{CONTEXT7,PLANE}=true`,
  tightened `ENSEMBLE_WEDGE_CAPTURE_*` knobs).
* **No live contact** — free port picked by bind (never 9797/8079/7979,
  asserted + proven freed at teardown), `HOME` and `ENSEMBLE_DATA_DIR`
  under a harness mktemp tree, no live install path.
* **No `.env` edits** — the child env is constructed in-process; the
  worktree `.env` is never read by the harness.
* **Dual-layer timeouts** — runner `timeout` (full 2400 s / smoke 900 s)
  + per-phase internal budgets.

## Fixture (deterministic, seed=20261029)

`seed.py` bulk-inserts the prod-shaped population through the daemon's
real table metadata after `create_all`:

* full: **3372 instances** (392 roots; branching 2–3 depth 2–3 + ten
  depth-10 chains + loose leaves + KB rows), **4000 done JobItems** on
  real instances, **1250 orphan-instance JobItems**, 30 active + 50
  queued, ~25 defer-gate witness Task rows (paused+running holders);
* smoke: the same shape at ~1/10 scale (350 instances / 400 done / 125
  orphans).

## Formula table (N4 validation targets)

| Shape | Census formula |
|---|---|
| `/api/instances?limit=10…include_descendants=false` | 3 + limit = 13 round-trips |
| `/api/instances?limit=100…include_descendants=true` | 4 + levels + rows |
| `/api/queues/defer-blocked` | 2 + holders |
| `/api/missions?liveness=…&limit=20` | 3 / page |

The jobs shapes are reported as measured (enrichment-heavy; the
decomposition splits row-hydration vs orphan `_lookup_instance`
statements by shape).
