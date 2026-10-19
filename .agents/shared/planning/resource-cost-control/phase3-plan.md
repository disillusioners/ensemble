# Phase 3: Join + Traceback (arch §2.6)

Plan for commission `resource-cost-control`, slice 3 of 8.
Primary spec: `architecture-recommendation.md` §2.6 (join model) + §2.8 (API row `GET /api/resources/traceback`). Architecture is ADJUDICATED — this file turns it into implementer-ready tasks; no redesign.
Date: 2026-10-18 · Author: plan-creation worker (via planner) · Status: Ready for Review

---

## Goal

Answer "why was RAM high at 14:07?" for any historical window: one read-only join layer + one endpoint that combines the resource signal (P1/P2 tables) with the EXISTING lineage spines — alive instances, in-flight tasks, owning agent/work, wallclock bridge, spawned children (the `chart_render` 8.3 GB-class query), and live structural counters — with timestamps normalized ONCE at the read seam so the aware-vs-naive TZ split cannot skew results.

**Binding constraint (from §2.6):** the metrics store holds ONLY the resource signal keyed by existing ids + time bucket. NO lineage duplication, no message bodies, no prompts, no checkpoints. The join layer reads lineage from the live tables at query time; it never copies it.

## Legal join spines (closed set)

| Spine | Role in this phase | Verified anchor |
|---|---|---|
| `task` | in-flight-at-T, owning work/agent, message bridge source | `daemon/repositories/task/models.py` — `idx_task_status_created` :173, `work_id` UNIQUE :202, naive-UTC `created_at/started_at/completed_at` :273-275, `instance_id` + `message_id` indexed, `status` indexed |
| `message_metadata` | wallclock bridge (PK lookup only) | `daemon/repositories/message_metadata/models.py` :75-76 composite PK `(thread_id, message_id)`; `thread_id == instance_id` invariant (model docstring); `created_at` is TEXT ISO — payload only, never a filter column |
| `event` (`kind='instance_lifecycle'`) | alive-at-T history | `daemon/repositories/event/models.py` — `idx_event_instance_created` :61, tz-AWARE `created_at` :80 |
| `instances` | sample-time status snapshot (status is CURRENT-only, no terminal ts) | `daemon/repositories/instance/models.py` :62 |
| `service_tracking` | LIVE pid→instance lookup | `daemon/repositories/service_tool/models.py` — `idx_service_tracking_pid` :166, `pid` :185, starttime jiffies :191; timestamps are TEXT ISO (`now_utc_iso`) — display only |
| `resource_daemon_bucket` / `resource_instance_sample` / `resource_pid_map` | the resource signal (P1/P2-created, T1/T2/T3) | arch §2.4; indexes `(instance_id, bucket_ts)`, `(kind, spawned_at)` |

**EXCLUDED BY DESIGN (never a join spine — enforced by a pin test, task 8):**
- `message_queue` — BOOT-WIPED at `daemon/manager.py` (~:962, under `config.queue.discard_on_startup`); historical queue state does not exist.
- Checkpoints — ZERO time columns, 168 h TTL (`daemon/constants.py:147` `CHECKPOINT_TTL_HOURS`); any checkpoint-derived join ages out.

## Deliverables

| # | Path | New/Existing | Content |
|---|---|---|---|
| D1 | `daemon/services/resource_traceback.py` | NEW | Join query layer: window normalization, the 7 §2.6 questions, structural block, response assembly. Pure dual-dialect SQL (no PG-only syntax: no `now()`, no native enums, no `USING`), parameter-bound |
| D2 | `daemon/routers/resources.py` (created in P1) | EXTEND | `GET /traceback` handler — thin: parse params → `asyncio.to_thread(resource_traceback.build_window_view, …)` → JSON. Degrade to the empty shape on internal failure, NEVER 503 (snapshots DI precedent) |
| D3 | P1's schema module for `/api/resources` (home established in P1; precedent `daemon/routers/snapshot_schemas.py`) | EXTEND | `TracebackResponse` + child models matching the §2.8 shape byte-for-byte |
| D4 | `tests/unit/services/test_resource_traceback.py` | NEW | Join semantics + TZ normalization + excluded-spine pin (see task breakdown) |
| D5 | `tests/unit/routers/test_resources_traceback.py` | NEW | HTTP contract: params, shape, degrade-not-503, window clamp |

No migrations in this phase (tables exist from P1/P2). No new runtime dependencies.

## The 7 join questions — keys, indexes, cost (each = one tested unit)

| # | Question | Join | Key / index | Cost |
|---|---|---|---|---|
| J1 | Alive-at-T | `event` WHERE `kind='instance_lifecycle'` AND `created_at ≤ T`, latest per `instance_id`; UNION CURRENT `instances.status` snapshot for still-running instances | `idx_event_instance_created` | cheap — indexed range + GROUP-BY-latest |
| J2 | In-flight-at-T | `task` WHERE `created_at ≤ T AND (completed_at IS NULL OR completed_at > T)` | `idx_task_status_created` | cheap — verified index :173 |
| J3 | Owning agent / work | enrichment of J2 rows: `task.work_id` (UNIQUE :202), `task.instance_id` (indexed); `agent_id` via one hop to the P2 sample snapshot (known §2.6 gap #5 — surfaced, not solved) | indexed cols | cheap |
| J4 | Wallclock bridge | `task.message_id → message_metadata.message_id` with `thread_id == instance_id`; composite-PK point lookups only | PK `(thread_id, message_id)` | cheap |
| J5 | Resource-at-T | nearest `resource_instance_sample` per instance with `bucket_ts ≤ T` (30 s blur acknowledged in payload) | `(instance_id, bucket_ts)` (T2) | cheap |
| J6 | Children-at-T | `resource_pid_map` WHERE `spawned_at ≤ T AND (exited_at IS NULL OR exited_at > T)`; `?kind=chart_render` filter = **the 8.3 GB-class query** | `(kind, spawned_at)` (T3) | cheap |
| J7 | pid → instance (historical) | historical: `resource_pid_map` by `pid` (+ starttime field-22 match); live fallback: `service_tracking` `idx_service_tracking_pid` | indexed | cheap |

Cost claims are acceptance-tested: the SQLite `EXPLAIN QUERY PLAN` pin test (task 8) asserts the expected index appears for J1/J2/J5/J6 — a plan flip to TABLE SCAN fails the gate.

## 🔴 TZ hazard — normalization contract (the load-bearing rule)

Ground truth (verified): `event.created_at` is **tz-AWARE** (`event/models.py:80`, `datetime.now(timezone.utc)`); `task.created_at/started_at/completed_at` are **naive-UTC digits** (`task/models.py:273-275`, produced by `now_utc_naive`); `bucket_ts` (T1/T2) is **naive-UTC** by P1 schema. Mixing them raw skews every traceback by the session TZ offset (up to ±h; production session is `Asia/Ho_Chi_Minh`, +07).

Contract — ONE seam, ZERO ad-hoc conversions:
1. The ONLY normalization helpers are `daemon/services/timestamps.coerce_to_aware_utc` (assume-UTC policy for naive values — module exists at HEAD, verified) and its naive-digit companion derived from it. `resource_traceback.py` MUST import both from `daemon.services.timestamps`; writing a local re-implementation fails review.
2. Request params `t0`/`t1` (ISO-8601; naive input → assume-UTC, aware input → `astimezone(utc)`) normalize to aware-UTC ONCE at the entry of `build_window_view`. Internally the layer keeps ONE canonical window as aware-UTC and derives two comparison views from it: `_naive_digits(dt)` (strip tzinfo — for `task.*` and `bucket_ts` predicates) and the aware value (for `event.created_at` predicates).
3. Every datetime READ from any spine passes `coerce_to_aware_utc` before comparison or arithmetic — regardless of what the driver returns.
4. Every timestamp written to the WIRE passes `timestamps.to_utc_iso` (aware ISO-8601 with offset) — task models already do this at `task/models.py:346-348`; the traceback layer does the same at its own boundary.

**Mandated test (task 1):** seed an `event` row stamped aware `09:00+07:00` and a `task` row stamped naive `02:00` (same instant); assert `alive_at_T(02:00Z)` and `in_flight_at_T(02:00Z)` BOTH see them; assert the same window queried as naive `02:00` and as aware `09:00+07:00` produce IDENTICAL responses.

## Task breakdown

| # | Task | Depends on | Acceptance |
|---|---|---|---|
| 1 | TZ seam: param parsing + `coerce_to_aware_utc`-based normalization helpers + the mandated equivalence test above | none | equivalence test green; naive-window and ±07:00-window responses byte-identical (assert on serialized JSON) |
| 2 | J1 + J2 queries (alive-at-T, in-flight-at-T) as typed functions returning rows + sources-of-truth labels (`event`-derived vs `status`-snapshot) | 1 | unit tests: instance alive across window via lifecycle event; instance alive via current-status fallback; task in-flight with NULL `completed_at`; task excluded once `completed_at ≤ T` |
| 3 | J3 + J4 enrichment (work/agent + message bridge) | 2 | tests: `work_id` unique lookup present; bridge resolves for a task with `message_id`; missing `message_metadata` row degrades to `bridge: null` — never raises |
| 4 | J5 nearest-bucket resource lookup (30 s blur; tolerance = 1 tick, configurable) | 2 | tests: exact tick hit; 29 s-old bucket accepted; >interval-stale bucket returns `resource: null` (honest gap, not interpolated) |
| 5 | J6 + J7 children-at-T + historical pid→instance; `kind` filter param; `chart_render` aggregate row (count + total `peak_rss_kb`) | 2 | tests: live child (`exited_at IS NULL`); exited child excluded at T; `kind=chart_render` filter returns only renders; pid-recycle: two map rows same pid different `start_time` → correct-era row wins |
| 6 | Structural block (LIVE readings at query time, NOT historical): `queue_depth` (live queue counter — the queue is boot-wiped, only current depth is meaningful), `workers` (live worker-pool size, `daemon/services/worker_pool.py:1252` `num_workers` / started-worker count), `checkpoints` (count/estimate only — no time cols exist), `gc_events` (gc counters snapshot). Each best-effort guarded; unavailable → `null`, never raise | 2 | tests: each counter mocked present/absent; absent → `null` and HTTP 200 |
| 7 | Router endpoint `GET /traceback` in `daemon/routers/resources.py` + response models; `t0`/`t1` required, max span 168 h (422 over), `limit` per list default 100 cap 500; sync queries bridged via `asyncio.to_thread`; internal failure → empty-shape 200 + WARN log (no 503); wire timestamps via `to_utc_iso` | 2-6 | router tests: happy path shape; missing `t0` → 422; span > 168 h → 422; forced service exception → 200 empty shape; unknown instance ids simply absent |
| 8 | Pin tests: (a) EXCLUDED-SPINE static test — grep `resource_traceback.py` source for `message_queue` and checkpoint-store imports → must be absent; (b) EXPLAIN QUERY PLAN assertions: J2 uses `idx_task_status_created`, J5 uses the `(instance_id, bucket_ts)` index, J6 uses `(kind, spawned_at)`, J1 uses `idx_event_instance_created` | 2-5 | both pin tests green; response carries `findings: []` (key reserved for P4 — contract stability) |
| 9 | Full-suite gate + module docstring documenting the closed spine set and the excluded spines (self-documenting for P4/P5) | 7-8 | full pytest green (gate below); docstring names all 6 legal spines + 2 exclusions |

3-10 tasks per phase: 9 tasks ✓ — each independently completable and testable.

## Acceptance criteria (with exact gate commands)

Run from the worktree `/home/nea/ensemble-src-wt-resource-cost-control` using the dev checkout's venv (worktree shares it; `make` sync target exists if a local venv is needed):

```bash
cd /home/nea/ensemble-src-wt-resource-cost-control && python -m pytest tests/unit/services/test_resource_traceback.py tests/unit/routers/test_resources_traceback.py -q
# (run from the worktree root with the worktree's own venv — `uv sync` if absent; never the main checkout's editable `.venv`, which imports the wrong branch's daemon)
# → all pass, 0 skipped

cd /home/nea/ensemble-src-wt-resource-cost-control && python -m pytest -q
# (run from the worktree root with the worktree's own venv — `uv sync` if absent; never the main checkout's editable `.venv`, which imports the wrong branch's daemon)
# → FULL suite green (worktree GREEN at phase boundary; no half-states)
```

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| A1 | TZ normalization holds | mandated equivalence test (task 1) | naive `02:00` vs aware `09:00+07:00` windows → identical JSON responses |
| A2 | All 7 join questions answered | per-question unit tests (tasks 2-5) | each J1-J7 has ≥1 positive + ≥1 negative/boundary test |
| A3 | Index-backed cost | EXPLAIN QUERY PLAN pin (task 8) | expected index name present in plan for J1/J2/J5/J6; zero TABLE SCAN on the big predicates |
| A4 | Excluded spines hold | static grep pin (task 8) | zero `message_queue` / checkpoint-store references in the traceback layer |
| A5 | API contract | router tests (task 7) | response keys exactly `{window, instances_alive, in_flight_tasks, spawned_children, structural, findings}`; `findings == []` in P3; degrade path returns 200 |
| A6 | Bounded response | `limit` clamp tests | every list ≤ `limit` (default 100, cap 500) |
| A7 | No regression | full pytest | exit 0 |
| A8 | Live-host hygiene | review + grep | zero live-host mutations; tests use synthetic fixtures only (tmp DB), never the live DB path (incident-class 2026-10-12 fencing discipline applies to any path-resolving fixture) |

## Coupling

- **Tight with P1** — consumes `resource_daemon_bucket`/`resource_instance_sample` schema (T1/T2), the `bucket_ts` naive-UTC convention, the router skeleton + schema module, and the config block. Contract change in P1 (column names/index names) breaks this phase → index names are part of the 3-site byte-identical convention; P1 must not rename post-hoc.
- **Tight with P2** — consumes `resource_pid_map` (T3) rows and `agent_id` enrichment on instance samples (the §2.6 gap #5 one-hop). Without P2 rows flowing, J5/J6/J7 return empty (degrades honestly; endpoint still ships).
- **Tight with P4** — `findings[]` key reserved here; P4 fills it from the anomaly journal. Shape reserved: array of finding objects (P4 owns the element schema per §2.7).
- **Loose with P5** — FE drill-down calls `?t0=&t1=` around an anomaly marker; response must be stable JSON, no streaming.
- **Independent of** P6/P7/P8.

## Risks + rollback

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | 🔴 TZ skew ships anyway (a stray raw comparison bypasses the seam) | High — tracebacks off by ±h, wrong window silently | Medium | single-module seam (all normalization in `resource_traceback.py`), mandated equivalence test (A1), review checklist item "no bare datetime comparison outside the seam" |
| R2 | 🟡 Query cost on large `task`/`event` tables | Low-Med — slow endpoint | Medium | verified indexes + A3 plan-pin test + 168 h window clamp + per-list `limit`; queries are window-bounded range scans, not full scans |
| R3 | 🟡 Dual-dialect divergence (PG vs SQLite) | Med — endpoint broken on one backend | Low | portable SQL only (no `now()`/enums/`USING`); tests run on the repo's SQLite test harness; PG path exercised via column-ensure boot in dev boot-scan, not in this phase's gate |
| R4 | 🟡 `message_metadata.created_at` is TEXT ISO — accidental string comparison against naive datetimes | Med — silent wrong filtering | Low | bridge is PK-point-lookup only; `created_at` there is payload, never a predicate (contract stated in module docstring + tested) |
| R5 | 🟢 `structural` counters unavailable mid-boot | Low | Medium | per-counter best-effort guards → `null`, tested; degrade-not-fail house rule |
| R6 | 🟢 P1/P2 land after P3 starts | Low — empty resource signal | Medium | dependency order enforced (see Dependencies); endpoint degrades honestly in the interim |

**Rollback:** additive-only (one new service module + one endpoint + schemas + tests; zero migrations, zero edits to existing tables). Revert of the phase commit removes the endpoint cleanly; the router keeps serving P1 endpoints. If the join layer must be disabled live post-merge, feature-flag path: return the empty shape via the existing degrade branch (config kill-switch only if P1 exposed one; otherwise revert-commit is the rollback — no data at risk because nothing is written).

## Dependencies

- **Blocked by:** P1 (T1/T2 tables + `bucket_ts` convention + router skeleton + schema module home + config block) and P2 (T3 pid-map rows + `agent_id` on instance samples). P3 does NOT start before P1 merges; it may start before P2's rows flow (honest-empty degradation), but its acceptance gate requires P2 rows present in fixtures.
- **Blocks:** P4 (anomaly contributors resolve through this join layer; `findings[]` wiring lands in P4 task 9) and P5 (FE click-a-marker → `GET /traceback?t0=&t1=` drill-down).
- **Exit criterion:** all acceptance criteria A1-A8 green, full suite green, phase commit independently mergeable to `feature/resource-cost-control` with the worktree GREEN.

## Verified anchors appendix (read-only, this session)

- `task`: `daemon/repositories/task/models.py` :173 `idx_task_status_created`, :202 `work_id` UNIQUE, :273-275 naive-UTC TS, :26-37 `_default_created_at_naive_utc`, :346-348 `to_utc_iso` wire precedent
- `event`: `daemon/repositories/event/models.py` :61 `idx_event_instance_created`, :80 aware `created_at`, `EventKind.INSTANCE_LIFECYCLE`
- normalization: `daemon/services/timestamps.py` `coerce_to_aware_utc` / `to_utc_iso` / `now_utc_naive` (assume-UTC policy documented)
- bridge: `daemon/repositories/message_metadata/models.py` :75-76 composite PK, TEXT-ISO `created_at`
- live pid: `daemon/repositories/service_tool/models.py` :166 `idx_service_tracking_pid`, :191 starttime jiffies
- exclusions: `daemon/manager.py` ~:962 boot wipe; `daemon/constants.py:147` `CHECKPOINT_TTL_HOURS=168`
- router/DI precedent: `daemon/routers/snapshots.py:16-31` (docstring contract), re-export `daemon/routers/__init__.py:26`, mount `daemon/api.py:4094`
- worker pool: `daemon/services/worker_pool.py:1252` `num_workers`
