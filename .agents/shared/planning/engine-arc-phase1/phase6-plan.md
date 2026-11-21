# Phase 6 — Observability shape-readiness: the query substrate, the convergence wiring, and the gated metrics-family ceremony

**Depends on:** P2 (engine snapshot), P5 (kind metadata), P1 (row contract).
**Parallel with:** P3/P4 after their inputs land (this phase touches no `daemon/api.py` line and no engine module owned by P3/P4).
**Feeds:** P7, P8 — and, beyond Phase 1, the future `maintenancer` tool and the `/metrics` UI tab.

---

## 1. Objective

Make the Emperor addendum **structurally satisfiable later** without a redesign:

> "the register function should include like service tag name, category, module information (use for metric, management,..debug easier) … Later we may even have tool for maintenancer agent to use or even a table chart on metrics tabs"

Phase 1 builds **the query substrate and the wire shape**, not the consumers:

1. A **read-only HTTP surface** over the engine's registered jobs + the event-kind catalog, carrying `service_tag` / `category` / `module_info` / schedules / health state as first-class fields — i.e. exactly the columns a future tab or tool would bind to.
2. The **convergence wiring**: the engine's projection and the landed `job_metrics()` family are read through ONE mapping so an operator sees the estate's scheduling health in one place — with the maintenance tier's own pinned shape untouched (fence **F-2**).
3. The **metrics-family ceremony, gated and explicit**: whether `engine_jobs` becomes a chart-primary metrics family is a *conscious one-shot decision* executed here (or explicitly deferred), never a side effect (fence **F-1**).

**Nothing in this phase is a mutating surface.** No steering verb, no "run now", no re-registration endpoint: the addendum says "later we may even have tool", and a write surface without a consumer is a liability shipped early.

---

## 2. Scope

**In:** `daemon/services/engine/projection.py` (the read model), `daemon/routers/engine.py` (a NEW router under `/api/engine`), the convergence mapping wiring (the function P2 authored, wired here), the route registration in `daemon/api.py`'s router-include block (a single `include_router` line — **the only `api.py` edit P6 makes, and it is a one-line addition inside the router catalogue, not the lifespan**), and the gated metrics-family decision.

**Out:** any mutating endpoint (steer / run-now / enable-disable over HTTP); any frontend file (FE is a later phase; this phase only fixes the wire shape it would bind to); a new auth scope (the existing `maintenance:read` / `metrics:read` pair is reused — no taxonomy churn); the `job_metrics()` edit (forbidden by F-2); the metrics census edits **unless** T6.3 is explicitly taken (see D6.5).

---

## 3. Decisions

| ID | Decision | Rationale (anchor) | Rejected |
|---|---|---|---|
| D6.1 | **A NEW router** `daemon/routers/engine.py`, mounted under `/api/engine`, rather than adding routes to `jobs` / `maintenance` / `metrics` | Three separate route-count doc pins exist for the obvious hosts: `tests/unit/test_phase5_jobs_router.py:135` pins the jobs router at exactly **13** endpoints; `tests/unit/test_memoir_v2_persona_router.py:322` pins 3; `tests/unit/metrics/test_central_metrics_contract_doc.py:146-149` pins the metrics doc's "routes block lists exactly the three routes". A new router costs one `include_router` line and breaks **none** of them | extending the metrics query door (breaks the contract-doc pin, and the door is a registry-of-metrics surface, not a service-inventory surface); extending maintenance (breaks nothing pinned, but conflates the maintenance console with the engine inventory) |
| D6.2 | Three read routes, all `GET`: `/api/engine/jobs` (list; filter by `category` / `service_tag` / `enabled`; paged), `/api/engine/jobs/{job_id}` (detail + recent `engine_runs` tail + the resolved kill-switch state), `/api/engine/events` (the kind catalog snapshot + per-kind firing/consumer facts) | The addendum's two named futures (a maintenancer tool; a table/chart tab) both need exactly "list the services with their metadata and health" and "list the event kinds with what is listening". A paged list + a detail read is the minimum substrate that supports both without a second design round | one mega-endpoint (a future tab cannot page it; a tool cannot address one service); a GraphQL/query language (massive surface for two reads) |
| D6.3 | **Scope reuse, no new taxonomy:** all three routes carry the any-of set `maintenance:read` **OR** `metrics:read`, declared via `dependencies=[scope_required(...)]` | The existing metrics query door uses exactly this pair ("the same ``maintenance:read`` OR ``metrics:read`` scope (the any-of set minted by the metrics-agent-tools lane)", `daemon/routers/metrics.py:32-40`; the scope tuple lives at `daemon/auth/scopes.py:76-86`). An engine inventory is a diagnostic read: the same audience | a new `engine:read` scope (taxonomy churn, a migration of every agent grant) |
| D6.4 | The response shape is **tab-ready and tool-ready**: stable `id` (= `service_tag`), `category` (grouping key), `module_info`, `description`, `schedule` (a normalized `{kind, interval_seconds, jitter_seconds, first_tick, offset_seconds}` block), `health` (`{last_run_at, last_outcome, last_skip_reason, consecutive_skips, dead, running, skips_by_reason}`), `metadata`, plus a `chart_hint` (`"counter" | "gauge" | None`) | Addendum: "a table chart on metrics tabs". A tab binds to flat, stable, sorted rows; a nested ad-hoc dict forces an FE redesign per field. `chart_hint` lets a future tab render a series per service WITHOUT the engine pretending to be a metrics family (F-1) | an opaque `metadata`-only body (a tab cannot render it, a tool cannot `ORDER BY` it) |
| D6.5 | **The metrics-family ceremony is GATED, not performed by default.** T6.3 is an explicit, separately-approved task: either (a) register a new chart-primary family for the engine (which requires a `CATALOG_FAMILY_VERDICTS` row **28 → 29**, a `REGISTERED_FAMILIES` member, a `FAMILY_SERIES_BANDS` ceiling, and a `PRE_EXISTING_DAEMON_OFFENDERS` record because the engine module sits outside `WIRING_ROOT`), or (b) explicitly **defer** it with the reason recorded. **Phase 1's default is (b): defer.** | Fence **F-1** is a closed census with a documented ceiling; the study's own rule is "converge, don't mint". Shipping the engine's inventory on its own read surface satisfies the addendum now; adding a family is a value decision with a cost (the census ceremony + a band that must never drift) that belongs to whoever wants a CHART, not to whoever builds the substrate | registering the family as part of "obviously needed" work (widens a closed census silently — the exact defect class the pack exists to catch); never deciding (the deferral must be recorded, or the next lane re-litigates it) |
| D6.6 | The `job_metrics()` convergence is a **read-time union**, not an edit: `project_merged(maintenance_service, engine_service)` returns per-job blocks where each field carries its source | Fence **F-2**: `tests/unit/test_maintenance_idle_starvation.py:800-804` pins `set(block) == {"last_success_ts", "consecutive_skips"}` for every maintenance job. Editing that dict turns a live pin RED. A read-time union delivers "one place to look" without touching the maintenance tier's contract | editing `job_metrics()` (breaks a live pin, and changes a shipped contract for a new consumer's convenience) |
| D6.7 | The engine detail read includes the **resolved kill-switch state** (per-job key + engine-wide + shadow), computed through P3's pure resolver at request time | The single most useful diagnostic for an operator staring at "why is this job not running?" — and P3 already made it a pure function (D3.12), so the read is free | exposing raw env values (leaks the config surface and is ambiguous about precedence) |
| D6.8 | **No mutating route.** Steering is design-for only; the route table is frozen at three GETs and a pack asserts the engine router's method set is exactly `{GET}` | The addendum says "later we may even have tool"; a write surface needs a scope decision, an audit story and a consumer. Shipping it unconsumed is the failure mode GAP-16 documents (kill switches nobody can find; surfaces nobody uses) | shipping a `/run-now` for convenience (an unauthenticated-ish trigger into the estate's scheduling — unacceptable without a consumer and an audit design) |
| D6.9 | The list route is **bounded and paged** (`limit` default 100, hard cap 500; opaque cursor) and built **off-loop** | House convention for every daemon read surface in this tree: the metrics query door pages with an opaque `next_cursor`, bounds the byte ceiling and builds off-loop (`daemon/routers/metrics.py:30-46`; `_render_off_loop` `:538`; `MAX_FAMILIES = 500` `daemon/services/metrics_query/constants.py`). An unbounded engine list would grow with the estate | an unpaged list (unbounded response, on-loop build) |
| D6.10 | The route reads the **RAM snapshot** (engine) + the **row contract** (engine_jobs) — never a per-request DB scan of `engine_runs` beyond a bounded tail (`LIMIT 20`) | The hot-read discipline (D2.12) exists precisely so diagnostics do not become load. A detail read returning 20 recent runs is enough to answer "why did it skip?" | scanning `engine_runs` per request (unbounded; grows monotonically) |

---

## 4. Task decomposition

| ID | Task | Outcome | Files / anchors |
|---|---|---|---|
| **T6.1** | `projection.py` — the read model: `list_view(filters, limit, cursor)`, `detail_view(job_id)`, `event_view()`; pure functions over injected sources | each view has a frozen key set (asserted); `chart_hint` derives from the spec's trigger kind only (never a guess); a job with no runs yet still renders (`health.last_run_at = None`) | `daemon/services/engine/projection.py`; the shape's precedent — a flat, sorted, paged read model (`daemon/services/metrics_query/provider.py`, `envelope.py`) |
| **T6.2** | Wire the convergence mapping (P2's `project_merged`) into the list/detail views as an `estate` block | a maintenance-tier job (e.g. `checkpoint_cleanup`) appears with `last_success_ts` + `consecutive_skips` sourced from `job_metrics()` **and** `next_due`/engine fields from the engine, in ONE row, each field carrying `source: "maintenance" | "engine"`; `job_metrics()` itself is untouched | `daemon/services/engine/projection.py`; F-2 pin `tests/unit/test_maintenance_idle_starvation.py:800-804`; source `daemon/services/maintenance.py:603-622` |
| **T6.3** | **The metrics-family ceremony — GATED.** Produce the decision record + (only if taken) the census edits: a new verdict row, `REGISTERED_FAMILIES` member, `FAMILY_SERIES_BANDS` ceiling, `PRE_EXISTING_DAEMON_OFFENDERS`/`ALLOWED_DAEMON_DIFF` records, and the `28 → 29` pin update | **default outcome: DEFERRED with a written reason** stored in `projection.py`'s module docstring + this plan's §6.1. If taken: the adjudication pack stays green with the +1 and the band is zero-headroom-correct | `daemon/services/metrics_query/wiring/adjudication.py`; pack `tests/unit/metrics/test_metrics_family_adjudication.py:415-453, :873-906` |
| **T6.4** | `daemon/routers/engine.py` — the three GET routes, scope-wired, paged, off-loop build | routes served at `/api/engine/*`; scope enforced (401 unauthenticated / 403 insufficient); a paged body carries `next_cursor`; the method set is exactly `{GET}` | `daemon/routers/engine.py`; router precedent `daemon/routers/maintenance.py` (scope + guard form `:135-172`), off-loop `daemon/routers/metrics.py:538` |
| **T6.5** | Router registration | exactly ONE `api_router.include_router(engine_router)` line in the existing catalogue block, **before** the SPA catch-all | `daemon/api.py` — the `include_router` catalogue at `:6359-6378+`; fence **F-15** (`tests/integration/test_vscode_routing.py:433-435` pins the catch-all as the LAST route) |
| **T6.6** | The route/method freeze pin | the engine router's method set == `{GET}` and its path set == the three declared paths; a mutation adding a POST REDs | P7 pack (authored here) |
| **T6.7** | Author the P6 unit pack (view key sets, cursor paging, scope matrix, method freeze, convergence union with `job_metrics()` unchanged, deferral record present) — registers in P7 | pack present | `test/packs/engine_observability_unit_test.sh`, `tests/unit/services/engine/test_projection.py`, `tests/api/test_engine_routes.py` |

---

## 5. Effort

| Bucket | Low | High | Basis |
|---|---|---|---|
| Read model + convergence wiring (T6.1, T6.2) | 4 | 6 | three views over already-computed state |
| Router + scope + paging + off-loop (T6.4, T6.5, T6.6) | 3 | 5 | new file, existing patterns |
| Gated census ceremony (T6.3) | 1 | 2 | a decision record (default defer) |
| P6 unit pack authored (T6.7) | 1 | 2 | |
| **Phase total** | **9** | **15** | ~**12 mid** |

---

## 6. Fences that constrain the design

### 6.1 F-1 — the metrics family census (the reason T6.3 is gated)

Pinned at this tree (`tests/unit/metrics/test_metrics_family_adjudication.py`):
- `:415-426` — `len(CATALOG_FAMILY_VERDICTS) == 28`, exact key set;
- `:428-434` — 12 IN + 6 PARTIAL; `REGISTERED_FAMILIES == full + partial`;
- `:436-438` — `registered_families()` == that set;
- `:440-452` — every registered family has a `FAMILY_SERIES_BANDS` entry;
- `:873-881` — measured closed label space ≤ band, per family;
- `:883-906` — `BASELINE_SERIES (89) + measured ≤ GLOBAL_SERIES_CAP (2000)`;
- `:391` — `WIRING_ROOT = "daemon/services/metrics_query/wiring"`; a family registered outside it needs an `ALLOWED_DAEMON_DIFF`/`PRE_EXISTING_DAEMON_OFFENDERS` record (the Decision Module P1 lane's own precedent, `:78-133`).

**Consequence recorded as the Phase-1 answer:** the engine's inventory ships on its own read surface (`/api/engine/*`), which needs **no** census change. A chart-primary family — which would let a future `/metrics` tab render an engine chart from the registry rather than from `/api/engine` — is **DEFERRED**, with this reasoning: (a) the addendum's request is satisfied by a labelled, attributed inventory (D6.4) without a family; (b) the census is a closed set with a zero-headroom band convention (`F23`'s 67 = 67 and `decision_overlay`'s 411 = 411, `adjudication.py:196-211`), so adding a family is a deliberate act by whoever wants the chart; (c) Phase 1's engine is dormant and empty — a chart of nothing would be dead surface. **Unfencing path if a later phase wants it:** one change carrying verdict row + registry member + band + allowlist record + the `28 → 29` pin edits, mutation-verified.

### 6.1a The DIFF-CENSUS cost that this program MUST pay (measured; not optional)

The same pack contains `test_the_daemon_diff_is_confined_to_the_allowed_files` (`:530-556`), which runs `git diff --name-only LANE_BASE` where `LANE_BASE = "644c2bfaaa48514ec7de79f7f4bcd078a700cdbe"` (`:58`) — a **two-dot diff against a FIXED commit**, so every daemon path the engine program touches appears in that list. `_assert_no_new_daemon_offenders` (`:373-387`) then requires each changed path to be in `ALLOWED_DAEMON_DIFF` (29 entries, measured) **or** `PRE_EXISTING_DAEMON_OFFENDERS` (99 entries, measured). The pack's own docstring states the maintenance rule: "**THE RECORD IS MAINTAINED BY THE CHANGING LANE**" (`:177-186`).

**Measured disposition of the paths this program will touch:**

| Path class | Status at this tree | Action |
|---|---|---|
| `daemon/api.py`, `daemon/config.py`, `daemon/manager.py` | **already in `ALLOWED_DAEMON_DIFF`** | none — free |
| `daemon/services/engine/**` (≈ 16 new modules across P1-P6) | NEW | must be recorded in `PRE_EXISTING_DAEMON_OFFENDERS` |
| `daemon/repositories/engine/**` (3 new modules) | NEW | must be recorded |
| `daemon/services/event_registry/**` (≈ 4 new modules) | NEW | must be recorded |
| `daemon/routers/engine.py` (new) | NEW | must be recorded |
| `daemon/migrations/versions/20261121_120000_create_engine_jobs.sql` + `…_120001_create_engine_runs.sql` | NEW | must be recorded |
| `daemon/services/child_reports.py`, `daemon/services/error_reporting.py`, `daemon/services/report_delivery_recovery.py` (P5 class-1/3 bridges) | **NOT in either set** (verified: zero hits in the 99-entry offender set) → NEW offenders | must be recorded |
| `daemon/services/maintenance.py` | untouched by this program (F-2) | — |
| `daemon/services/orphan_watcher_sweep.py` | untouched (F-10 — the sibling lane owns it) | — |

**Cost estimate:** ≈ **27 added lines** to one frozenset in a test file, in ONE change, with the list spelled out. That is the whole price of the census fence — but it is a **release-blocking** line item (P7 Leg G re-runs this pack), and it must be paid by the phase that first touches each path, not bolted on at release. **Owner: P6 T6.3** (it runs last before P7 and can record the union in one edit); each phase's contribution is enumerated in the table above so the union is auditable rather than discovered.

> Note: `daemon/services/report_delivery_recovery.py` is an offender only if P5 observes at that seam (T5.5). If T5.5 observes at the *repository* read instead, that path is `daemon/repositories/report_injection/**` — also not in the set. Either way one path is added; the implementation picks one and records it.

### 6.2 F-2 — `job_metrics()`'s block shape (the reason D6.6 is a read-time union)

`tests/unit/test_maintenance_idle_starvation.py:800-804` asserts `set(block) == {"last_success_ts", "consecutive_skips"}`. **Consequence:** the convergence is a union performed in `projection.py`; the maintenance tier's published contract is untouched. The engine does not "improve" a shipped surface to publish its own numbers.

### 6.3 F-15 — the SPA catch-all is the LAST route

`tests/integration/test_vscode_routing.py:433-435` asserts the catch-all is at `len(app.routes) - 1`. **Consequence:** the `include_router(engine_router)` line goes in the catalogue block at `daemon/api.py:6359+`, which precedes the catch-all's registration. The pack asserts the catch-all remains last.

### 6.4 F-20 — route-count and contract-doc pins on the neighbouring surfaces

- `tests/unit/test_phase5_jobs_router.py:135` — jobs router == **13** endpoints.
- `tests/unit/test_memoir_v2_persona_router.py:322` — persona router == **3**.
- `tests/unit/metrics/test_central_metrics_contract_doc.py:146-149` — the architecture doc's "routes block lists exactly the three routes" + their scope.
- `tests/test_custom_agents_router_authz.py:123` — custom-agents router == **4**.

**Consequence:** D6.1 (a NEW router) is not a style preference — it is the only shape that adds an engine read surface without breaking a neighbouring surface's census. The new router gets its OWN freeze pin (T6.6) so the same class of drift is caught there too.

### 6.5 F-3/F-11 — `_NAME_RE` dormancy fence (view/field naming)

`tests/test_resource_control_seam.py:294-346`. The projection's field names (`last_outcome`, `last_skip_reason`, `dead`) are clear; no `action_by` / `acted_at`; no `resource_pid_map` + `action` string pairing.

### 6.6 Auth posture (not a fence, a contract)

An untagged route is **protected by default** (`daemon/auth/route_meta.py` per `daemon/routers/metrics.py:56-62`). The three engine routes carry NO class tag ⇒ auth-on default, plus the explicit any-of scope. An AUTH-OFF deployment's behavior must match the metrics door's inline refusal branch (asserted in the scope matrix leg).

---

## 7. Dependencies

- **Phase:** P2 (snapshot), P5 (kind metadata), P3 (resolved-switch function for D6.7).
- **External:** none.
- **Blocks:** P7 (packs), P8 (release content), and — beyond Phase 1 — the maintenancer tool and the `/metrics` tab.

## 8. Parallelism

- Runs **concurrently with P4** (P4 owns the lifespan; P6 owns the router catalogue line — different `api.py` regions, ~2600 lines apart, and both are single contiguous additions).
- **Only two lanes may touch `daemon/api.py` in this program: P4 (lifespan) and P6 (router catalogue).** Recorded so a third lane does not appear.
- Worktree-lane parallelizable: yes.

## 9. Risks / mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| The engine read surface becomes a de-facto metrics API and drifts from the registry | two truths for "what runs" | D6.2's `chart_hint` + the deferral record state the boundary explicitly; a future family addition is the T6.3 ceremony, not a second surface |
| A mutating route is added "while here" | an un-audited trigger into the estate's scheduling | D6.8 + T6.6's method-set freeze pin (a POST REDs) |
| Unbounded list/tail responses | a diagnostics endpoint that degrades the daemon | D6.9/D6.10: paged list, bounded run tail, off-loop build |
| The convergence union confuses operators about ownership | "which number is real?" | every field carries `source: "maintenance" | "engine"` (D6.6) |
| The census ceremony is silently skipped and a later lane assumes the family exists | a dead chart | T6.3's default outcome is a **written deferral** in the module docstring, so the absence is a decision with a pointer (the `DROPPED_FAMILIES` convention, `daemon/services/metrics_query/roster.py:232-234`) |
| New router breaks the SPA catch-all ordering | every route 404s or the SPA swallows an API path | F-15 assertion plus P7's integration leg |

## 10. Acceptance gates (measurable)

| ID | Gate |
|---|---|
| AC1 | **View shapes frozen:** the three views' key sets are asserted exactly; `chart_hint` derives only from the trigger kind; a job with zero runs renders with `last_run_at = None` and no fabricated numbers |
| AC2 | **Convergence:** a maintenance-tier job appears once, with `last_success_ts`/`consecutive_skips` from `job_metrics()` and engine fields from the snapshot, each carrying its `source`. **`job_metrics()`'s own block set is unchanged** (F-2 leg green) |
| AC3 | **Routes:** three GETs at the declared paths; the router's method set is exactly `{GET}` (mutation: add a POST ⇒ RED); scope matrix — unauthenticated ⇒ 401; authenticated without either scope ⇒ 403; either scope ⇒ 200; AUTH-OFF ⇒ the documented refusal, byte-comparable to the metrics door's branch |
| AC4 | **Paging:** `limit` default 100, cap 500; an opaque cursor resumes without duplication or omission; the build runs off the event loop (thread-identity assertion) |
| AC5 | **Catch-all stays last:** `tests/integration/test_vscode_routing.py` green; the new router's routes appear before the catch-all |
| AC6 | **Gated ceremony:** the deferral record is present in `projection.py`'s docstring naming the unfencing path; `tests/unit/metrics/test_metrics_family_adjudication.py` passes with **zero** delta (proof the census was not widened) |
| AC7 | **Neighbour pins green unchanged:** `tests/unit/test_phase5_jobs_router.py` (13), `tests/unit/test_memoir_v2_persona_router.py` (3), `tests/test_custom_agents_router_authz.py` (4), `tests/unit/metrics/test_central_metrics_contract_doc.py` |
| AC8 | **No frontend file touched** by this phase (asserted by a path-scope check) — only the wire shape is fixed |
