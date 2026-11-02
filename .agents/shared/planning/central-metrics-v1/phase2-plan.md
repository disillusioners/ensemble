# Phase 2: Query Substrate — Provider, Trend Ring Buffer, Sampler Thread, Registry Narrowing

Status: pending · Tier **1 — CONFIRMED** (additive modules; `daemon/api.py` is **not** touched here — the
mechanical Tier-2 escalation belongs to Phase 3; AM-3's registration kwargs are additive with defaults, so
they do not shift the tier)
**FOLDED 2026-11-02:** architect v2 amendments folded — ids cited inline as `(AM-n)`.
Feeds: architect → plan council → fresh approver → implementation

## Objective

Build the **additive substrate** the door will stand on, without touching the shipped routes, the lifespan
or any producer module:

1. a **provider protocol** over the registry (`families()` / `snapshot()` / `history()`) that reads **published
   state only** — never a metric lock, never `render_metrics()`, never a database;
2. the **in-RAM trend ring buffer** (AD1: in-memory only, zero migrations) with an adaptive depth under a
   stated point budget;
3. the **cadence-driven sampler thread** that pulls producer values into the registry (stage A) and appends
   published snapshots to the rings (stage B), at the reused managed-key cadence (AD4(d));
4. the **registry-side F7 op-enum narrowing + `VOCABULARY_VERSION` 1→2** (AD5), and the
   `metrics_sampler_interval_s` description amendment that makes the previously-unwired key honest.

## Tasks

| # | Task | Depends On | Acceptance | Traceability |
|---|---|---|---|---|
| 2.1 | Create `daemon/services/metrics_query/` with `__init__.py` exporting the public surface (provider protocol, envelope builders, availability helpers). Keep the package **import-side-effect free** (importing it must not start a thread or touch the DB). | Phase 1 contract | `import daemon.services.metrics_query` in a fresh interpreter starts no thread, opens no connection, and registers no metric | FR-A5, FR-G4 · D4 · anticipation hook (deferred #2) |
| 2.2 | Define the **provider protocol** `MetricsQueryProvider` with `families() / snapshot() / history()`. **HTTP-agnostic**: no `Request`/FastAPI types in any signature (the future agent-tool door must call the same functions). | 2.1 | A duck-typed fake provider satisfies the protocol; the protocol module imports no web framework | FR-A5, FR-G4, FR-A4 · D4 · architecture anticipation hook #1 |
| 2.3 | Implement the **registry-backed provider**: read `published_snapshot()` references only (lock-free), derive family metadata from the registered objects (`name`, `kind`, `label_keys`, `allowed_values`, `help`) **plus the bucket boundaries** (served on the wire — approver B3 + leader ruling) **plus the registration-time family metadata (AM-3, task 2.7b)**, and serve exactly the **canonical frozen family shape** — quoted token-identically from `plan-overview.md` §frozen contract (`decisions.md` D4 is the designated contract): `{id, names:[{name, kind, labels:{key:[values]}\|null, help, buckets:[…]\|null, cardinality_class, monotonic}], live_series_count, availability:{state:"live"\|"dark", reason?}, role, source_idiom, durability, cardinality_contribution}`, report `live_series_count`, and expose `last_publish_age_s` via the **new public `Metric.last_publish_monotonic()` accessor** (AM-13 — the private `_last_publish` is not an acceptable dependency). Emit **typing honesty** (AM-4): `kind ∈ {gauge, counter, histogram}` (no `summary`), `monotonic: bool` within one `restart_epoch`, histogram `value: null` + `histogram:{count,sum,buckets}` **as stored**. Never call `render_metrics()`, never acquire a metric lock (the no-lock pin allowlists `registered_metrics()` — the registry-level lock, not a metric lock), never mutate. | 2.2 | A pin asserts the provider module contains **no** `render_metrics` call and no registry mutation call (`inc(`/`observe(`/`set(`); a contention test proves a wedged writer cannot block the provider (it serves a stale copy + age, never blocks); the no-lock pin allowlists only `registered_metrics()` | FR-A1, FR-A2, FR-A8, FR-F2 · D4 · AM-3, AM-4, AM-13 · seams S1 (`checkpoint_metrics.py:902-904`), S2 (`:392-398`) |
| 2.4 | Implement the **availability model**: per-family `{state:"live"\|"dark", reason?}` from runtime facts — census running? postgres runtime? wedge capture active? series observed yet? — using the closed reason set frozen in Phase 1, with `census_gated` read from the **public `CensusSampler.gated` property** that wraps the EXISTING gate site (`metrics_census.py:332-340` → `metrics_gate.metrics_enabled()`); `activation.census_enabled = not census.gated` (honest — `.running` is switch-independent). **The query package NEVER reads the switch** ⇒ the check-site census stays at 2 and the Phase-3 grep pin stays green (AM-18). | 2.3 | Each reason is reachable by exactly one runtime fact; a dark family never reports `0` as if it were a value; a grep pin proves the query package contains no switch read | FR-A8, FR-D3 · D1, D4 · AM-18 · seams S4, S8 |
| 2.4a | **MICRO-TASK — expose the public `CensusSampler.gated` property (approver NB5).** `daemon/services/metrics_census.py` currently has only the **private** `_gate()` (`:332-340`) and the public `running` (`:319`); **no public `gated` exists in the tree**, yet the plan's Phase-1 invariant (iv), `P2-M13`, `P3-M11` and AM-18's census==2 all name it. Add a **public `gated` property that wraps the EXISTING `_gate()`** — a read-through, **NO new check site**: the behavioural gate census over `daemon/` stays at exactly **2** (the listener body + this census tick). `daemon/services/metrics_census.py` is therefore **declared in the P2-G1 file scope** (additive property only — the census's behaviour, cadence and DB discipline are untouched). `P2-M13` is re-based on this property. | 2.4 | The property exists and returns exactly what `_gate()` returns; the check-site census over `daemon/` is **still 2** (a pin asserts it); the census's own behaviour is unchanged (its existing packs stay green) | FR-D3, FR-D5 · D1 · **AM-18** · **approver NB5** |
| 2.5 | Implement the **trend ring buffer** as **parallel preallocated arrays** — `array('d')` values + `array('q')` timestamps, head/tail ring index, O(1) drop-oldest — at **16 B/point** (AM-9: the `deque(maxlen)` form is **rejected on measured evidence**, 129–161 B/pt ⇒ 92.6–115.0 MiB at 750 k points = 3.6× over FR-C4's 32 MiB ceiling). `POINT_BUDGET = 750_000` ⇒ **11.4 MiB** worst case; the unit pin asserts **measured** bytes (`sys.getsizeof`-based), not a nominal constant. Histogram points are `(ts f64, count i64, sum f64, buckets i64[N])` = 24+8N B (N=17 ⇒ 160 B); live histogram series = **3**. Depth: `depth = clamp(POINT_BUDGET // max(1, ring_series), MIN_DEPTH=60, window_s // effective_interval_s)` where `ring_series` is the **registry-native label-combination count** (42 today over 18 live objects — NOT the catalog count) and `effective_interval_s = max(sampler_cadence, publisher_cadence)` = 15 s at defaults (AM-10); depth **recomputes** when `ring_series` changes (registration events) and the rule is written down. Eviction is **by count AND by age** (`ts < now − window_s`) so the 2 h window stays truthful under any cadence change. Series GC after 3 consecutive samplings with no published entry, keyed on "the metric still HAS a published entry" (idle-but-live families are never dropped — AM-13). Ring keys use the registry's `_label_key` serialization (AM-1). | 2.1 | A unit test overflows the buffer and asserts: oldest-first eviction, **measured** memory ≤ 32 MiB and ≤ the 11.4 MiB envelope, and the sample count never exceeds `POINT_BUDGET`; the age-eviction test asserts the window shrinks when the cadence changes | FR-C3, FR-C4, FR-C6, NFR-7 · D3 (sizing table) · AM-1, AM-9, AM-10, AM-13 · AC-C.3 |
| 2.6 | Implement the **sampler thread** (`daemon` thread, one tick = stage A + stage B): stage A = run the registered **pull adapters** and write their values into the registry (the seam Phase 4 plugs into — empty in this phase); stage B = read the published snapshot, **identity-dedupe** against the previous tick (per metric), append to the rings. The tick is `limits.metrics_sampler_interval_s` (default 10 s, boot-applied, read **once at boot**); the reported `window.interval_s` is the **observed** effective spacing `max(sampler, publisher)` (AM-15) — **the cadence-key deviation is DEV-3** (requirements FR-C7 still names the scrape-cadence key; the architect's D-1 / AM-15 ruling governs). **Lifecycle (AM-13): the sampler starts LAST of the metrics trio (after the census block; the listener starts FIRST and must NOT be reordered) and stops FIRST (before the listener stop).** The envelope's `restart_epoch` = `bash_spawn_reaper.CURRENT_BOOT_ID` (AM-14). Fail-safe: an adapter or a registry error must never kill the thread and never propagate into the daemon loop. | 2.5 | The thread survives a raising adapter (logged, tick continues); a wedged registry writer yields a **stale** ring entry (documented, never fabricated); the cadence is asserted at boot time; the start/stop order is pinned | FR-C1, FR-C5, FR-C7, FR-D5 · D3 · AD4(d) · AM-13, AM-14, AM-15 · seams S1, S19 (`config.py:825-833`) |
| 2.7 | **Narrow the F7 op enum** in `daemon/checkpoint_metrics.py` to the adjudicated membership (plan default `{aget, other}`; see plan-overview flag D-7), update the `help` text accordingly, and **bump `VOCABULARY_VERSION` 1→2**. Registry-side only — **no producer instrumentation** (the J-4 write seam stays OUT). | Phase 1 (flag D-7 ruling) | `ensemble_checkpoint_op_seconds{op}`'s `allowed_values` == the ruled set; `ensemble_metrics_vocabulary_version` renders 2; the fold target `other` remains declarable; the vocabulary pack re-derives green | FR-F7, AD5 · D5 fold · defect #1 (research §1g) · AC-F.4 |
| 2.7b | **NEW — registration-time family metadata (AM-3) — and the CHART-PRIMARY ROSTER (approver B2).** Extend the registry seam with **additive keyword metadata**: `family`, `source_idiom`, `durability`, `cardinality_weight`, `chart_role`, with **derived defaults** so the **18 existing registrations are untouched**. **This task is also the home of the chart-primary roster** — the explicit, self-contained subject set for FR-C2 / AC-A.3 / AC-C.1: the FR-C2 floor set **minus F2** (dropped by AM-25 / LR-2) **plus the D5 partials that qualify** = **12 families**, each with its per-family/per-series `chart_role` (see *The chart-primary roster* below). `/families` then serves `{id, names[…], live_series_count, availability, role, source_idiom, durability, cardinality_contribution}`. **NO second family table** — a static table would make every onboarding wave edit the door (breaking the incremental-onboarding fence) and, as scoped, would serve zero families at Phase 3. Stays **Tier 1**: additive kwargs with defaults, not on the mechanical escalation list. | 2.1, 2.3 | A new family registered with metadata appears in `/families` with its role/idiom/durability **without any door edit**; the 18 pre-existing registrations still render with derived defaults (their packs stay green) | FR-A1, FR-A8, FR-C2 · D4 · **AM-3** · AC-A.1 (the Must-gap AM-3 unblocks) |
| 2.8 | Amend the `limits.metrics_sampler_interval_s` **description** in `daemon/config.py` to name **BOTH** consumers — the host-side samplers **and** the in-RAM trend history sampler (it previously read "host-side samplers" and was unwired). Preserve the field's default (10), bounds (1–60), boot-applied semantics and its pinned restart column; the amendment is **description-only** (AM-15/AM-19(b)). | 2.6 | The managed-key pack stays green (default/bounds/type/restart column unchanged); the description names both consumers; the seven managed keys keep their defaults, bounds, types and restart columns | AD4(d) · D3 · FR-F7 · AM-15, AM-19(b) · `test_metrics_managed_keys.py` |
| 2.9 | Implement the **envelope builders + pagination helpers** exactly as frozen in Phase 1 (`QUERY_SCHEMA_VERSION = 1`, `vocabulary_version` mirror, `generated_at_utc` **aware-UTC**, `restart_epoch` = `CURRENT_BOOT_ID`, `activation`, `availability` with the AM-18 `census_gated` source, **the AM-5 staleness fields `last_sample_age_s` + `stale`**; filters `?family=`, `?name_prefix=`, `?series=` (label-aware, AM-1), `?names=` (name-level), `?window_s=`, `?max_points=` (**bucket-max**, **default 240** — approver B1 + leader ruling); the AM-6 cursor with `400 invalid_cursor`; the AM-7 bump policy constants). | 2.3, 2.4, 2.5 | The envelope matches the Phase-1 contract field-for-field; the `restart_epoch` changes across a simulated restart while the series set is empty; an expired cursor returns 400 `invalid_cursor` | FR-A3, FR-A4, FR-A11 · D4 · AM-1, AM-5, AM-6, AM-7 · AC-A.3, AC-A.4, AC-A.8, AC-C.4 |

**Not in this phase:** routes, lifespan wiring, the scope/class cages, any producer-module edit, any DB read.


### The chart-primary roster (approver B2 — the FR-C2 / AC-A.3 / AC-C.1 subject set)

**12 families** = the FR-C2 floor set **minus F2** (dropped by the value ruling, AM-25 / leader LR-2) **plus
the four D5 partials that qualify** (F7, F8, F18, F21). Every roster series carries a `chart_role`;
`chart_role ∈ {line, step}` — **counters render STEP, gauges render LINEAR** (approver B5 + leader ruling: a
linear interpolation between counter samples would assert values that were never observed). `area` is a
supported primitive rendering mode but is **not assigned by default**.

| Family | Chart-primary series | chart_role |
|---|---|---|
| **F1** loop lag | `ensemble_loop_lag_seconds{stat}` | line |
| **F4** pool native | `ensemble_db_pool_native{pool,quantity}` | line |
| | `ensemble_db_pool_requests_errors_total{pool,kind}` | **step** (counter) |
| **F5** pool derived | `ensemble_db_pool_checked_out{pool}` · `ensemble_db_pool_waiting_clients{pool}` | line |
| **F6** PG census | `ensemble_pg_backends{state}` · `ensemble_pg_idle_in_txn_oldest_seconds` · `ensemble_pg_leaked_txn{site}` · `ensemble_pg_census_last_success_timestamp_seconds` | line |
| **F9** readiness | `ensemble_readiness_component_state{component}` · `ensemble_readiness_grace_active` · `ensemble_readiness_failure_streak{component}` | line |
| | `ensemble_readiness_probe_total{component,outcome}` | **step** (counter) |
| **F10** queue depth | `ensemble_job_queue_depth{admission_state}` | line |
| | `ensemble_job_queue_items_created_total` | **step** (counter) |
| **F11** worker pool | `ensemble_workers_total{pool}` · `ensemble_workers_busy{pool}` · `ensemble_worker_pool_wakeup_efficiency{pool}` · `ensemble_task_heartbeat_age_seconds` · `ensemble_task_heartbeat_cadence_seconds` | line |
| | `ensemble_worker_pool_events_total{pool,event}` | **step** (counter) |
| **F13** instances | `ensemble_instance_status_count{status}` · `ensemble_instance_liveness_count{liveness}` · `ensemble_waiting_children_count` · `ensemble_waiting_children_max_age_seconds` | line |
| **F7** checkpoint ops (partial) | `ensemble_checkpoint_op_bytes{op}` · `ensemble_checkpoint_slow_ops_total{op}` | **step** (counters) |
| **F8** store sizes (partial) | `ensemble_checkpoint_blobs_table_bytes` · `ensemble_checkpoint_table_bytes{table}` | line |
| **F18** gateway (partial) | `ensemble_mcps_gateway_sessions` · `ensemble_mcps_gateway_tombstones` | line |
| | `ensemble_mcps_gateway_audit_total{outcome}` | **step** (counter) |
| **F21** journal twin (partial) | `ensemble_journal_file_bytes` | line |

**Rules**

- A family **not** in this roster is **stat-primary** (current value only) — FR-C2's default.
- The roster is carried in the **registration-time metadata** (`chart_role`), so the FE renders it
  **dynamically from `/families`** — the FE holds **no hard-coded family list**.
- Every roster family is in the D5 IN set; **F2 is absent** because it was dropped by value ruling
  (AM-25 / LR-2), so v1 has no F2 dark-with-reason path to render.
- Adding a family to the roster = one `chart_role` kwarg at registration + one row here — **no door edit,
  no FE edit**.
- The roster is the subject set of **AC-A.3** and **AC-C.1** (both re-scoped in `requirements.md`).

## Gate legs

| Leg | Tier | Scope | D1 (lane-owned, HEAD-only) | D2 (dual-side, comparable) | Expected D1 / D2 nodes |
|---|---|---|---|---|---|
| **P2-G1** | **1** | new package `daemon/services/metrics_query/**` + `daemon/checkpoint_metrics.py` (enum + version) + `daemon/config.py` (description only) + **`daemon/services/metrics_census.py` (the public `gated` property ONLY — task 2.4a; no behavioural change)** | `metrics_query_provider` · `metrics_history_ring` · `metrics_sampler` · `metrics_registry_narrowing` · `metrics_vocabulary_amendment` | **REGISTERED PACK `metrics_p2_dual_side`** — composes: `test_checkpoint_metrics_registry` (45) · `test_checkpoint_metrics_vocabulary` (18) · `test_metrics_gate` (35) · `test_metrics_listener` (29) · `test_metrics_listener_registry` (15) · `test_metrics_census` (34) · `test_metrics_managed_keys` (65) · `test_metrics_live_managed_values` (15) · `test_metrics_refresh_path` (15) | ~45 / ~271 (PROJECTION) |

**D2 asymmetry statement (required in the pack):** HEAD runs D1 + D2; BASE runs **D2 only** with an
identical command shape; D1's absence at BASE is expected and is neither a net-new failure nor a fix.

**Mutation→test table (stub — the executor records the observed digests in `.agents/tester/PACKS.md`):**

| Label | Mutation | Must go RED |
|---|---|---|
| `P2-M1` | provider reads a live lock instead of the published copy | `metrics_query_provider` |
| `P2-M2` | provider calls `render_metrics()` | `metrics_query_provider` |
| `P2-M3` | ring depth clamp floor removed (depth → 0) | `metrics_history_ring` |
| `P2-M4` | eviction changed to drop-newest | `metrics_history_ring` |
| `P2-M5` | sampler stops on the first adapter exception | `metrics_sampler` |
| `P2-M6` | identity-dedupe removed (every tick appends) | `metrics_sampler` |
| `P2-M7` | op enum re-widened to the 5-value set | `metrics_registry_narrowing` |
| `P2-M8` | `VOCABULARY_VERSION` left at 1 | `metrics_vocabulary_amendment` |
| `P2-M9` | the ring switched back to `deque(maxlen)` (bytes-per-point regression) | `metrics_history_ring` |
| `P2-M10` | age eviction removed (the window no longer tracks `window_s`) | `metrics_history_ring` |
| `P2-M11` | the depth divisor switched to the catalog count | `metrics_history_ring` |
| `P2-M12` | `last_publish_age_s` read from the private `_last_publish` | `metrics_query_provider` |
| `P2-M13` | the public `CensusSampler.gated` property (task 2.4a) removed or replaced by a **direct switch read** in the query package — a THIRD behavioural check site (the census==2 invariant must hold) | `metrics_query_provider` (grep pin) |
| `P2-M14` | the family-metadata kwargs made mandatory (the 18 existing registrations break) | `metrics_query_provider` |
| `P2-M15` | `buckets` dropped from the `/families` name shape (the provider already derives them — approver B3) | `metrics_query_provider` — rides the EXISTING registered pack (no pack-set expansion) |

**Slicing proof:** `--collect-only -q` proving the selected set equals the declared pack scope, before any
FAILED-digest comparison.

**Pre-exclusion registry:** `regression_performance` (>300 s) · e2e needing live `:8079` (→ Release Gate) ·
`tests/packs/g7` (packs only).

**Invocations (W9/W10):** the D2 set is the **registered pack `metrics_p2_dual_side`** (registered in
`.agents/tester/PACKS.md`, invoked by pack id — never an ad-hoc pytest list). **Mandatory recipe load:**
`.agents/tester/skills/blast-radius-leg/blast-radius-leg.md` (Tier-1 scoped dual-side) **plus**
`.agents/tester/skills/failed-digest-equality-gate/failed-digest-equality-gate.md` for the FAILED-digest
comparison. **Executor re-derives** the node counts: the D1/D2 node figures in this table are PROJECTIONS
re-derived at run time via `--collect-only -q` (the pack's own collect-only output is the record; a
hand-maintained count is a finding).

**Pin discipline:** every behavioural pin is planted independently and shown RED→GREEN; the FAILED-digest
comparison runs identical commands on both legs.

## Coupling

- **Tight with Phase 3** — the provider protocol, the envelope builders and the sampler lifecycle are the
  door's implementation; the route pack asserts against them.
- **Tight with Phase 4** — the pull adapters plug into the sampler's stage A (the plug-in point ships empty
  here and is filled per cluster).
- **Tight with Phase 1** — the shapes implemented here are the contract frozen there.
- **Independent of Phase 5** — the FE never imports the provider.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The provider accidentally acquires a metric lock (the render-path machinery is exposition-only) | The query door could block on a wedged writer — the incident class | The provider reads published references only; two pins (no `render_metrics`, no lock acquisition) + a contention test. |
| The sampler thread wedges at shutdown | Stranded thread / leak | Daemon thread + detached-join discipline (listener `stop()` precedent); lifecycle ordering is pinned in Phase 3's wiring test. |
| Ring memory underestimated at the fully-wired ceiling | Memory pressure | The **array-backed** ring is 16 B/point ⇒ **11.4 MiB** worst case (AM-9); the depth clamp (registry-native divisor) shrinks depth before memory grows, and the unit pin asserts **measured** bytes — not a nominal constant (AM-9/AM-10). |
| The F7 narrowing breaks a frozen-vocabulary consumer | Vocabulary pack red | Narrowing + bump + §14.4 amendment + pack re-derivation move in one cluster; the fold target stays declarable. |
| The cadence key is boot-applied but read live somewhere | Two cadences, confusing traces | The cadence is read **once** at sampler construction; a test asserts the boot-time binding. |
| A "just one more metric" query sneaks into the provider | Sync DB on the request path (P-R1) | A pin greps the query package for DB/session imports; DB-backed pulls happen in the sampler thread (Phase 4). |

## Exit criterion

The package imports cleanly with no side effects; the provider serves families/snapshot/history from
published state only (pinned); **family metadata is discoverable at registration** with the 18 existing
registrations untouched (pinned — AM-3); the ring is **array-backed**, obeys its **measured** byte budget
(≤ 11.4 MiB, ≤ FR-C4's 32 MiB ceiling), evicts by count **and** age, and uses the registry-native depth
divisor (pinned — AM-9/AM-10); the sampler starts LAST / stops FIRST, survives a raising adapter, dedupes
per metric and reports the **observed** interval (pinned — AM-13/AM-15); `census_gated` comes from the
public `CensusSampler.gated` property, never a direct switch read (pinned — AM-18); the F7 enum is narrowed
to `{aget, other}` and `VOCABULARY_VERSION` reads 2 (pinned); the managed-key pack is green with the
description-only amendment; the P2-G1 leg ran under **Tier 1** with a declared D1/D2 split, a slicing proof
and **15/15** mutation legs (P2-M1…P2-M15 — approver NB2: P2-M15 is the buckets-dropped guard from B3, so an executor stopping at 14 would never plant it).
