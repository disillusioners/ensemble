HANDOFF_PATH = .agents/shared/planning/sonic-team/jobs-badge-loop-hog/HANDOFF.md

# HANDOFF — jobs-badge-loop-hog (Option E: cheap badge polling + FE visibility gates)

Commission (sonic-leader → sonic-developer, 2026-11-20): make /api/jobs badge polling CHEAP (backend
cost), not rare; FE visibility gates for ungated pollers. EMPEROR RULING (Discord 2026-11-19,
binding): the 8 s badge cadence STAYS; hidden tabs stop polling; visible tabs keep FULL cadence —
NO visible-tab interval changes. Base: origin/latest @ 213ea6c1e (v0.31.13 closure wave). Live at
commission: current → v0.31.10 (NOT promoted by this lane — the Emperor's 3-factor ceremony owns
the flip).

## Files touched across modules

Union of every module's file list (per-module grouping preserved) + integration artifacts.

- **M1a — resolver cost bound** (`sonic/jobs-badge-loop-hog-m1a`, commits 7ae871486):
  - `daemon/services/work_resolver.py`
  - `tests/unit/services/test_work_resolver_badge_pushdown.py` (NEW, 298 L)
- **M1b — badge-path batching + watch-read bridges** (`sonic/jobs-badge-loop-hog-m1b`, commits
  5a44ee819 + 30a8db191 [revision]):
  - `daemon/routers/jobs_crud.py`
  - `daemon/services/job_queue_service.py`
  - `daemon/services/dead_letter_service.py`
  - `daemon/repositories/job_queue/watcher_repository.py`
  - `tests/unit/routers/test_jobs_list_badge_batch.py` (NEW, 208 L)
- **M2 — FE visibility gates, 6 ungated pollers** (`sonic/jobs-badge-loop-hog-m2`, commit d3997ec3e):
  - `frontend/src/app/components/job-queue-indicator/job-queue-indicator.component.ts` (+ `.spec.ts`)
  - `frontend/src/app/pages/home/home.component.ts` (+ `.spec.ts`)
  - `frontend/src/app/pages/metrics/resource/live/live-tab.component.ts` (+ `.spec.ts`)
  - `frontend/src/app/services/instance.service.ts` (+ `.spec.ts`)
  - `frontend/src/app/pages/mcps-gateway/mcps-gateway.component.ts` (+ `.spec.ts`)
  - `frontend/src/app/services/command-state.service.ts` (+ `.spec.ts`)
- **M3 — pins + measurement instrument + baseline** (`sonic/jobs-badge-loop-hog-m3`, commits
  fcf8cebcb + 11b628020 [revision]):
  - `frontend/src/app/poll-visibility.bindings.pins.spec.ts` (NEW, 78 L)
  - `.agents/tester/harness/jobs-badge-cost/README.md` (NEW)
  - `.agents/tester/harness/jobs-badge-cost/common.py` (NEW)
  - `.agents/tester/harness/jobs-badge-cost/seed.py` (NEW)
  - `.agents/tester/harness/jobs-badge-cost/main.py` (NEW)
  - `.agents/tester/harness/jobs-badge-cost/raw/BASELINE.badge_cost.base.json` (NEW, regenerated)
  - `.agents/shared/planning/sonic-team/jobs-badge-loop-hog/MEASUREMENT-BASE.md` (NEW)
- **Integration (sonic-developer)**: 4 × `merge --no-ff` commits (283b9ba73, 25cf7e52e, 26d5a0628,
  a44a3505c), the AFTER raw artifact commit (056bda4ad), planning copies (38410b7e0), PLAN.md
  (eb65b3049), and this HANDOFF.md.
- Merged range (code only): **32 files, +5004 / −28**. `frontend/dist` is gitignored — the FE is
  built by the stage pipeline (`scripts/upgrade/_build_frontend.sh`), never by a bare `npm run build`.

## What changed

**M1a — the badge leg's resolver fetch is now BOUNDED (the load-bearing fix).**
`WorkResolverService.list_work` now pushes the client's EXPLICIT limit into SQL: `_query_jobs`
applies `.limit(limit + len(ensure_job_ids or ()) + TIE_PAD)` (TIE_PAD = 8), so the batched
lookups (`_batch_instances`, `_batch_mission_fields`, `_task_repo.get_timing_by_work_ids`) and the
per-row conversion loop run over a BOUNDED set instead of hydrating EVERY matching terminal job
(pre-fix: the whole `completed,settled,failed,cancelled,dead_letter` history — 420 rows in the
harness seed, thousands in the fleet). The Python stable sort + slice stays authoritative, so wire
order (`created_at` desc, append-order tie-break) is unchanged. Page-completeness (D-29/D-69) is
preserved ALWAYS: any `ensure_job_ids` id missing from the bounded page is recovered by ONE targeted
IN re-query before conversion. The NO-LIMIT path is deliberately untouched — D-68 leader ruling
(Q1-ii, wi-3f5bea95): "no server-side default cap".

**M1b — the router-level per-row N+1s are batched, and the watch reads leave the event loop.**
(a) The per-QUEUED-job `await service._get_queue_position(...)` loop in `list_jobs` (each call
re-fetched the whole pending list) is replaced by ONE batched computation (`_get_queue_positions`,
one pending-list fetch per distinct scope) producing byte-identical values — the badge path stays
PROJECT-GLOBAL (the old route never passed `queue_id`; the helper keeps an optional `queue_id`
param for other callers only). (b) The per-dead_letter-row SYNC `get_dlq_by_job_id` read is replaced
by one IN-query (`DeadLetterService.get_dlq_by_job_ids`). (c) The three unbounded
`get_all_active_watches()` reads — `job_queue_service.py:556` (`reconcile_terminal_watches`), `:711`
(`_reconcile_terminal_watches_legacy`), `:844` (`reconcile_held_watches_for_instance`) — are bridged
off the event loop with `asyncio.to_thread` (house precedent: the `list_work` bridge at :1841) and
read through the new bounded `watcher_repository.get_active_watches()`.

**M2 — hidden tabs stop polling; visible tabs are untouched.** Six ungated always-on FE pollers got
the house visibility-gate pattern (listener + skip-while-hidden tick + ONE debounced 2 s refocus
refresh + supersede token where completion order can invert + listener teardown): the 8 s 5-leg
badge indicator, home instances (120 s), metrics resources-live (8 s), instance.service (60 s),
mcps-gateway rxjs timer (10 s), command-state (5 s). **Every interval literal is unchanged**
(8000 / 120000 / 8000 / 60000 / 10000 / 5000) — the Emperor's contract is pinned by source tests.
The badge's per-leg `catchError` + "retain last good count" rule is untouched (never a bare 0/0).

**M3 — pins + a repeatable measurement instrument.** A source-pin spec asserts (a) all six interval
literals are exactly as above and (b) all six surfaces carry the visibility-gate markers — 18/18
green on the merged tree. A badge-cost harness (`.agents/tester/harness/jobs-badge-cost/`, adapted
from the `eventloop-phase0` harness) seeds a realistic population (30 instances, 420 terminal jobs,
10 queued, 5 active, 5 orphans, 60 watchers) and replays the 5-leg badge composite ×8 rounds with
a statement census; `--mode base|head` gives a reproducible before/after pair.

**Integrated before/after (harness, service layer, SQLite fixture — 8 rounds; BASE = 213ea6c1e):**

| leg | BASE p50/max ms | HEAD p50/max ms | BASE stmts | HEAD stmts | returned rows BASE→HEAD |
|---|---:|---:|---:|---:|---|
| jobs_active_badge (NO limit — untouched by design) | 29.07 / 85.23 | 39.05 / 85.16 | 5 | 5 | 435 → 435 |
| jobs_terminal_page (limit=10 + ensure_job_ids — the fixed shape) | 25.39 / 29.48 | 24.58 / 29.92 | 5 | 6 | 10 → 10 |
| missions (limit=20) | 10.05 / 11.68 | 8.80 / 10.58 | 3 | 3 | 20 → 20 |
| instances (limit=10) | 5.20 / 7.90 | 4.53 / 6.96 | 2 | 2 | 10 → 10 |
| defer_blocked watch read | 3.69 / 5.10 | 3.65 / 5.19 | 1 | 1 | 60 → 60 |
| reconcile_terminal_watches (×5, median/min/max ms) | 872.7 / 476.4 / 941.5 | 526.7 / 509.2 / 733.6 | 121 | 121 | — |
| reconcile_held_watches_for_instance (×5) | 1756.9 / 1589.6 / 1907.3 | 1611.4 / 1521.6 / 1763.6 | 121 | 121 | — |

Reading these numbers honestly: the harness's row metric counts RETURNED records, not rows FETCHED
from the DB, so it CANNOT display the unbounded fetch that M1a removes — the fetched-row bound is
proven by the engine-bound pins (fetched-row budget ≤ limit+margin), not by this table. The
watch-read bridge shows up as a real wall-clock drop in the reconcile legs (−40 % median on
`reconcile_terminal_watches`). The +1 JobItem statement on the limit-explicit leg is the
page-completeness recovery re-query (see Known risks).

## What is UNTESTED

- **The fleet-scale claim is NOT re-measured in-lane.** The Emperor's contract — "per-tick loop
  occupancy drops to noise; the 3.7–4× felt inflation GONE — MEASURED before/after under
  browser-open load simulation with the loop-lag probe (lag − 1.0 s)" — is **NOT** proven by this
  lane. The harness runs in-process on a SQLite fixture: it has no LoopLagProbe (loop-lag raw =
  null), no PG scale (420 rows, not thousands), no executor/pool contention (queueing is invisible
  in-process), no HTTP framing, and no browser. The lane proves the CONTRACT and the MECHANISM
  (bounded fetch, batched N+1s, watch reads off-loop, hidden-tab gates, intervals pinned); the
  at-scale loop-occupancy/3.7–4× number remains to be measured at the ceremony, against the live
  fleet. **Do not read this lane as "the 3.7–4× is measured gone".**
- No live/PG end-to-end run of the changed paths (dev/scratch fixture + in-memory SQLite only, by
  commission fence). The dev PG was never contacted; live 9797 was never contacted.
- FE: no `ng build` (AOT) and no browser observation — the stage pipeline builds the dist; jsdom
  only, `visibilityState` simulated via a property override.
- The DLQ enrichment has a VALUE-PARITY pin but no engine-bound SELECT-count flatness pin (the
  brief's flatness pin was authored for queue positions only).
- `reconcile_terminal_watches` / `reconcile_held_watches_for_instance` under the new `to_thread`
  bridge are covered by the EXISTING watcher suites (50 tests) — no new end-to-end bridge test.
- Mixed-fixture SELECT counts when queue scopes DIFFER (per-scope pending fetches) are not asserted.
- The full-suite pytest regression was NOT run (scoped packs only — the reviewer/tester wave owns
  the wide gate). A wide `tests/unit/services/` run WAS executed as supplementary assurance:
  **28 failed / 4176 passed** (851 s). Those 28 were then re-run (a) on the merged tree in isolation
  and (b) at BASE 213ea6c1e in isolation (separate detached worktree, same commands): **20 failed /
  8 passed on BOTH trees, with byte-identical FAILED sets (zero diff)** — so the 28 are pre-existing
  at base plus 8 order/load-induced reds that only appear in the wide session (the house's known
  order/load pattern). **No failure touches a file this lane changed** (`work_resolver.py`,
  `jobs_crud.py`, `job_queue_service.py`, `dead_letter_service.py` appear in no failed set).
  Reproduction: `pytest $(cat /tmp/jbh-failed-ids.txt | tr '\n' ' ') -p no:randomly -q` in the merged
  tree and in a base worktree at 213ea6c1e, then diff the FAILED sets.
- M1a's `TIE_PAD = 8` assumption (tie groups at the SQL cut ≤ 8) is exercised by a tie-heavy
  fixture but not fuzzed.

## Known risks

- **TIE_PAD = 8** (M1a): if a `created_at` tie group straddling the SQL cut exceeds 8 rows, the
  bounded page could differ from the unbounded order. Mitigated by SQL `ORDER BY created_at DESC` +
  stable Python sort, but the tie-order of a tie group is DB-dependent (SQLite vs PG may differ).
- **Recovery re-query scope** (M1a): an ensured id that no longer matches the request's
  project/instance/status filters will NOT be recovered by the fallback IN re-query — it is
  filtered out, exactly as the pre-fix path would have. Edge, disclosed.
- **+1 statement on the limit-explicit leg** (M1a): the completeness recovery re-query fires when
  an ensured id falls below the cut. In the production route the ensured ids ARE the page's own
  top-N (same filter + same `created_at DESC` ordering), so the query is expected NOT to fire
  except on tie/out-of-order edges — this expectation is reasoned, not measured.
- **The watch read stays a full-table read** (M1b): `get_active_watches()` carries no new WHERE
  because `job_watchers` has NO persisted active-state column — the read is query-identical to
  `get_all_active_watches`. What changed is that it no longer runs on the event loop. The "bound"
  half of the census-suggested shape ("bridge+bound the watch reads") is therefore only HALF
  delivered: bridged, not bounded.
- **MEASUREMENT-BASE.md overstates M1b** (M3): it says "M1b adds the active-state WHERE and
  bridges via asyncio.to_thread" — M1b's actual shape adds NO WHERE (no persisted column). This
  handoff supersedes that line; the doc was written before M1b's revision landed.
- **First AFTER run was instrumented on the wrong shape** (disclosed): the first head run used the
  pre-revision harness, whose leg 2 called `list_work` WITHOUT limit — i.e. the no-limit path M1a
  deliberately does not touch. It produced flat numbers; that artifact was deleted, the harness was
  corrected (M3 revision) and both BASE and HEAD raws were regenerated. The table above is the
  corrected pair.
- **M1b's first revision was a wire-contract deviation** (corrected): the batch helper initially
  scoped queue positions per `job.queue_id`, which the old route never did — `position` values
  would have changed for queue-scoped jobs. Bounced and fixed to project-global (commit
  30a8db191) with a value-equality fixture that includes queue_id-carrying jobs.
- **M2 made one pre-existing FE flake deterministic** (disclosed): a badge-mirror spec's
  time-resolution flake was removed by pinning an explicit shared `created_at` (TIE) — preserves
  the intended stable-sort tie; no assertion weakened.
- **FE gate runs need a usable `frontend/node_modules`** (this lane symlinked
  `/home/nea/ensemble-src-wt-stage-v0319/frontend/node_modules` — the main checkout's copy lacks
  `jest-canvas-mock`). A verifier must re-create that symlink (or run `npm ci`) before jest/tsc.
- **Sibling lane ordering**: the agent-turn-CPU lane (row 2; `daemon/compaction.py`,
  `daemon/tools/**`, `daemon/graph.py`, `daemon/loader.py`) had NOT landed on origin/latest at this
  lane's release cut — surfaces are VERIFIED DISJOINT, so this lane's staged release does not
  contain row-2 work, and their lane stages its own release. See the report line for the exact
  origin/latest sha at cut time.
- **Release-slot derivation + sibling-lane ordering (disclosed):** the slot was derived LIVE per W9 —
  tags ≤ v0.31.13, release dirs ≤ v0.31.13, `current` → v0.31.10 — and **v0.31.14 was deliberately
  left FREE for the agent-turn-CPU lane** (the leader's "the two lanes MUST NOT claim the same
  version"), so this lane cut **v0.31.15** (the slot-skip is the untagged intermediate bump commit
  `184bc754d` "! New version 0.31.14"; only `026833336` "! New version 0.31.15" is tagged/pushed).
  The CPU lane's merge had NOT landed on origin/latest at this lane's cut (their integration branch
  `feature/agent-turn-cpu-row2` had all three row-2 modules merged but was unlanded; origin/latest was
  still 213ea6c1e when this lane pushed). **This staged release therefore does NOT contain the row-2
  agent-turn-CPU work**, and the CPU lane will need to merge this lane's release lineage before its
  own cut. Surfaces are VERIFIED DISJOINT (compaction.py / tools/** / graph.py / loader.py vs
  routers / services / repositories / FE), so no file conflict is expected — but the ORDERING
  disclosure is on the record.
- **Rollback-safety adjudication (disclosed):** the stage refused to derive `rollback_safe` silently
  because a destructive-DDL grep (`DROP TABLE|DROP COLUMN`) matches PRE-EXISTING migration files
  (e.g. `20260627_000002_drop_job_watchers_fk.sql`, `20260514_000001_add_project_id_to_instances.sql`,
  `20261105_102821_create_memoir_tables.sql`). The gate's own instruction was followed after
  PROVING the migration delta is empty: `git diff --name-only v0.31.13..v0.31.15 -- daemon/migrations/`
  → EMPTY, and zero `DROP TABLE|DROP COLUMN` in that delta; this lane's diff touches **zero**
  migration files. Hence `ENSEMBLE_ROLLBACK_SAFE=1` (explicit), recorded in the manifest as
  `rollback_safe=true source=explicit`.
- **Whole-tree jest**: 9 pre-existing failures (schedule-create-dialog / jobs-grouping / settings /
  jobs-filter-state families) + a metrics banner-geometry flake are baseline, not net-new; only the
  scoped suites were run green here.

## How to verify

Commands for a verifier, run ON THE MERGED TREE.

- Range: `git diff 213ea6c1e..<merged-tip>` (daemon 4 files + 2 new test files; FE 13 files;
  harness 5 new files; planning dir).
- Daemon scoped packs (worktree venv, PER-VAR env scrub — ambient `ENSEMBLE_SELF_ENV`/`POSTGRES_*`
  SHADOW `.env` for direct invocations; never trust ambient):
  `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u ENSEMBLE_SELF_ENV .venv/bin/python -m pytest tests/unit/services/test_work_resolver_badge_pushdown.py tests/unit/routers/test_jobs_list_badge_batch.py tests/unit/services/test_work_resolver.py tests/unit/services/test_work_resolver_partial_collapse.py tests/unit/services/test_work_resolver_query_budget.py tests/unit/routers/test_jobs_list_limit.py tests/unit/routers/test_jobs_mission_id_filter.py tests/unit/routers/test_jobs_streaming_resolver.py tests/unit/routers/test_jobs_cleanup_endpoint.py tests/job_queue/test_mission_live_guard.py -p no:randomly -q`
  → **133 + 100 passed** on the merged tree (two invocations; 0 failures).
- FE gates: `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` → exit 0;
  `./node_modules/.bin/jest src/app/poll-visibility.bindings.pins.spec.ts --maxWorkers=8` → **18/18
  passed** (6 interval pins + 12 gate pins; these are the Emperor-contract pins);
  `./node_modules/.bin/jest <the 6 surface specs> --maxWorkers=8` → **363/363 passed, 6 suites**.
- Measurement: `env -u POSTGRES_* -u ENSEMBLE_SELF_ENV .venv/bin/python .agents/tester/harness/jobs-badge-cost/main.py --mode base|head --out .agents/tester/harness/jobs-badge-cost/raw` → compare `raw/BASELINE.badge_cost.base.json` (committed) vs `raw/BASELINE.badge_cost.head.json`
  (committed) — leg latency/statements/rows + reconcile medians. Interpret per "What is UNTESTED".
- **Staged release (as built, 2026-11-20T05:07:59Z)**: `/home/nea/agents-ensemble/releases/v0.31.15`
  — manifest `version=v0.31.15`, `binary_version=0.31.15`, `rollback_safe=true` (source=explicit),
  `contains_contract_phase=true`, `known_schema_gen=20261119_000001_add_proposal_decision_columns.sql`,
  `pipeline_scripts_floor_commit=70a0960e54c8ba479cdd110ec0b6b52de0a1403c`; binary sha256
  **3f600d177390d9810974c28fe0da2f933e6f1e47bfd77db5e07fcdc4e34d0223** (release dir == dist ==
  manifest — 3-way IDENTICAL); FE tree sha256 `0a958800d3a903869c97345d06eddb6bcde3214da2f086f353469ed20476e0e5`
  with the FE provenance sidecar `git_head=026833336f7e dirty=false artifact_sha256=6b194edd81bff0333089922b0fea331985d20b5f26a40e6b8fba9e1fbb5b09d0`;
  prompt-artifact baseline clean (217 versioned artifacts); stamp coverage validated
  (`PIPELINE_SCRIPTS_REV=70a0960e5…` contains every shipped executor-family path); plugin-staleness
  entry-check PASS. **LIVE UNTOUCHED**: `releases/current` → v0.31.10, no flip performed (promote.sh
  owns `current`; the Emperor's 3-factor ceremony owns the flip). Banner probe (sanctioned non-boot
  smoke, mktmp ENSEMBLE_DATA_DIR + per-var PG scrub + `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`
  pinned) → `daemon.api - INFO - Starting Ensemble v0.31.15`; no stray process left behind
  (nothing on :8088; only the pre-existing live v0.31.10 + demo v0.16.3 daemons).
- Tag: annotated `v0.31.15` → commit `026833336f7e0625d0b743656ae1ac5464ef4af2`; origin/latest ==
  `026833336` (fast-forward from 213ea6c1e); feature branch `sonic/jobs-badge-loop-hog-release` and
  `sonic/jobs-badge-loop-hog` pushed; task branch carries the release lineage via merge `f7729a720`.
- Staged release: `status.sh` shows the staged version, live `current` unchanged (no promote);
  4-way binary sha identical (release dir == dist == manifest == sidecar); banner probe (sanctioned
  non-boot smoke) prints `Starting Ensemble v<version>`.
- ANCHOR NOTE (house rule — anchors are leads): every file:line in this handoff was re-verified at
  base 213ea6c1e; re-grep before citing.

## Bash-raises used

none — every command ran ≤ 600 s in the foreground; the long `tests/unit/services/` pack ran as a
background proc (`proc_run`, auto-kill 1200 s). No `proc` raise consumed, no timeout override.

## Complexity self-assessment

**MEDIUM** (assessed §2.5 at plan-open, after the single explore + the module split; NOT raised,
NOT overridden). Drivers: four disjoint single-turn modules; zero migrations; additive-only; the
wire contract protected by engine-bound pins (fetched-row budget, per-row-lookup reddens pin,
SELECT-count flatness, page-completeness, order compatibility) and by FE source pins; the D-68 /
Q1-ii "no server-side default cap" constraint fenced into M1a as a hard rule; the sibling CPU lane
VERIFIED DISJOINT (compaction.py / tools/** / graph.py / loader.py vs routers / services /
repositories / FE). Two module revisions were needed (M1b wire-contract scoping; M3 measurement
instrument measuring the wrong shape) — both caught by integration, both closed before the handoff.
The load-bearing residual is the unmeasured at-scale loop-lag/3.7–4× claim, recorded in
"What is UNTESTED" rather than asserted.
