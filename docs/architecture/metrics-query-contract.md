# Metrics Query Contract — central-metrics-v1 (P1 frozen contract)

Date: 2026-11-02 (UTC)
Lane: `feature/central-metrics-v1` (worktree `/home/nea/ensemble-src-wt-central-metrics-v1`)
Provenance: the plan set at `.agents/shared/planning/central-metrics-v1/` (worktree-side) —
`plan-overview.md` §frozen contract is the single source; `decisions.md` D1–D6 are the designated
rulings; `fold-ledger.md` records WHY every pin exists. Architect amendments are cited inline as
`(AM-n)`, leader rulings as `(LR-n)`.

Status: **FROZEN at P1.** Phases 2/3/5 implement against exactly this document. This file is
machine-checked by the registered pack `central_metrics_contract_doc` (the fenced blocks with
info-strings below are the anchors — deleting or widening them turns the pack red).

Zero shipped-code change in P1: this document is repo-only (`docs/` is not part of the release
payload), no config default changes, no managed-key edits, no route or package code.

---

## 1. Activation posture — the per-surface matrix (six surfaces)

The kill-switch `ENSEMBLE_METRICS_ENABLED` (DB tier `limits.metrics_enabled`,
`daemon/config.py:785-793`) has a **verified two-site blast radius** (AM-19: the load-bearing basis
is intra-family consistency + this census; the two-class rule from `daemon/config.py:4413-4419` is
**demoted to supporting precedent**). The complete behavioural check-site census over `daemon/` is
exactly two sites, cited per the anchor convention (the *gate method* as `<file>:<method range>`,
the *behavioural check site* as `<file>:<call line>`):

| # | Surface | Switch-gated? | Auth-gated? | Off-state semantics | Anchor |
|---|---|---|---|---|---|
| 1 | `:9798` exposition **listener body** | YES — default-OFF **at the body level**: the socket still binds and serves a valid empty 200 while OFF (AM-19(a)) | NO — unauthenticated, loopback-only in-constructor (`metrics_listener.py:361-368`) | HTTP 200 with a VALID EMPTY body — no `ensemble_metrics_up`, no family series; the socket is still bound | gate method `metrics_listener.py:408-419`; call line `:419` |
| 2 | `/api/metrics` **text exposition** | NO — never consults the switch | YES — `maintenance:read` (`daemon/routers/metrics.py:70`, `:113`) | served in both switch states | auth seam `metrics.py:91-107` |
| 3 | **Query routes** (`families` / `snapshot` / `series`) | NO — never switch-gated (D-11b / D1(b) wins; AM-17) | YES — `maintenance:read` on all three + the same fail-closed 503 seam | **availability, not emptiness** — the door serves live registry data in both switch states | this contract §6 |
| 4 | **Census DB work** | YES — the census sampler performs NO database work while OFF | n/a (internal thread) | the sampler keeps ticking; its DB leg is skipped; F6 families go dark with `availability.reason="census_gated"` | gate method `metrics_census.py:332-340`; call line `:340` |
| 5 | **Registry accumulation** | NO — ungated, always on | n/a (in-process) | writers flip dirty flags and increment regardless of the switch; switch-OFF does NOT stop data collection | `checkpoint_metrics.py` observe paths carry no gate |
| 6 | **FE charts polling** (the third activation surface, leader F6) | NO — polls the query door only; the FE **never polls the listener surface** | YES — the caller's session scope | the FE polls `/snapshot` (10 s) and `/series` (15 s); when the door is unreachable or the session has expired the tab **degrades to its cached/last-good view, marked stale** (`phase5-plan.md` task 5.4) rather than polling harder or blanking | `phase5-plan.md` 5.4 |

**The switch never touches authenticated surfaces.** Its emergency-off semantics are UNCHANGED
and NARROWER than they appear: flipping OFF blanks only the unauthenticated listener body and stops
the census sampler's DB work.

```activation-surfaces
listener_body
api_metrics_text
query_routes
census_db_work
registry_accumulation
fe_charts_polling
```

## 2. Kill-switch blast radius + emergency-off semantics for the door

- Switching OFF does **not** blank the query door. Only the listener body empties and the census
  sampler's DB work stops (surface rows 1 and 4 above).
- The registry keeps accumulating (surface row 5), so the door serves live data in both switch
  states.
- F6 (`ensemble_pg_*` census families) goes **dark** with
  `availability: {state: "dark", reason: "census_gated"}` — never with a zero-as-value.
- The door's **own** emergency-off lever is **token revocation** of the `maintenance:read` grant
  (AM-19(c)) — the door has **no kill-switch of its own**. The runbook names the lever:
  revoke the session/token that carries `maintenance:read` (the auth service's grant/session
  revocation path); the census==2 invariant is preserved because no code path consults the switch
  for the door.
- The census's honest state flag: `activation.census_enabled = not census.gated` —
  `.running` is switch-independent, so `census_enabled` is the honest switch-derived bit and
  `listener_enabled` (row 1's socket state) is a **process fact** and is therefore
  switch-INVARIANT (it is not in the permitted-difference list of invariant 3).

## 3. FE empty state — availability-driven, never "disabled"

Resolution of flag D-8 (FR-E5 vs D1, CONFIRMED by the architect):

- The FE shows the empty panel when **no family is live** — an availability-driven condition read
  from the envelope's `activation` block plus per-family `availability` — never an
  error banner (AC-E.3) and never a "disabled" banner.
- The FE must NOT infer "disabled" from an empty series set alone: an empty set with
  `availability.reason="series_absent"` means *no observation yet*, and an empty set with
  `reason="census_gated"` means *the census families are gated*, not that the product is disabled.
- **Copy fix (AM-19(d)): the panel says "no metrics available yet" — never "metrics disabled".**
  The old copy was itself the false banner D-8 forbids.

## 4. The frozen contract envelope

Envelope-shape axis vs label-vocabulary axis are SEPARATE (AM-7): `schema_version` is the envelope
shape version (integer `QUERY_SCHEMA_VERSION = 1`); `vocabulary_version` mirrors the registry's
`VOCABULARY_VERSION` (2 after this lane). A **breaking** envelope change (field removal / rename /
type / semantic) bumps `schema_version`; an **additive** change (new optional field / filter / enum
value) does NOT bump and consumers must ignore unknown members. FE mirrors are verbatim on field
SETS, never on values: `vocabulary_version: number` (never a literal) and the availability reason
as a widened union with an `unknown(<value>)` fallback (AM-7) so a future reason never breaks the
FE compile.

### 4.1 Envelope header (all three routes)

```
schema_version: 1                                  # QUERY_SCHEMA_VERSION — envelope-shape axis
vocabulary_version: <mirror of VOCABULARY_VERSION> # label-vocabulary axis (2 after this lane)
generated_at_utc: "<ISO-8601 aware UTC>"           # AM-5: the house producer timestamps.now_utc_iso()
                                                   #   (daemon/services/timestamps.py:63) — NOT naive-UTC
restart_epoch: "<CURRENT_BOOT_ID>"                 # AM-14: bash_spawn_reaper.CURRENT_BOOT_ID
                                                   #   (daemon/services/bash_spawn_reaper.py:106,
                                                   #   uuid4().hex) — a restart is visible, never a
                                                   #   silent gap. boot_epoch.get_boot_epoch() is NOT
                                                   #   used: DB-clock domain, None-able.
activation: { listener_enabled: bool,              # process fact — the exposition socket is bound
              census_enabled: bool,                # = not census.gated (AM-18; honest)
              query_api: "auth-gated-always-on" }  # literal — the door's own posture
```

`census_enabled` is sourced from the **public `CensusSampler.gated` property** (phase-2 task 2.4a,
a read-through of the EXISTING private `_gate()` at `metrics_census.py:332-340` — **no new check
site**; the census over `daemon/` stays exactly 2). **The query package NEVER reads the switch.**

### 4.2 Series identity (AM-1)

A series is `(name, labels)`; the labels are the complete discriminator; the canonical
serialization is the registry's `_label_key` (sorted `(k, v)` pairs,
`daemon/checkpoint_metrics.py:430`). Ring keys and any future item-#2 watch-tool reference use the
same serialization. The door therefore exposes a **label-aware selector**
`?series=<name>{<k>=<v>,…}` (RFC-3986-escaped) alongside `?names=`; `?names=` stays **name-level**
and returns every label variant.

### 4.3 `GET /api/metrics/families`

Per family, the canonical frozen shape (the single source is
`plan-overview.md` §frozen contract; `decisions.md` D4 is the designated contract; every other site
quotes it token-identically):

```
{id, names:[{name, kind, labels:{key:[values]}|null, help, buckets:[…]|null, cardinality_class, monotonic}], live_series_count, availability:{state:"live"|"dark", reason?}, role, source_idiom, durability, cardinality_contribution}
```

- `buckets` is **ON the wire** (approver B3 + leader ruling): the provider derives the bucket
  boundaries from the registered metric; the deferred consumers (item #2 agent tools, item #3
  Grafana) are exactly who needs them off-box; FR-A8 / AC-A.1 require the bucket sets to be
  discoverable. `buckets` is `null` for non-histogram kinds.
- The per-name **`chart_role`** carries the chart-primary roster (approver B2; `phase1-plan.md` 1.4;
  `phase5-plan.md` 5.3 — "the per-series mode comes from the family metadata `chart_role`").
  `chart_role ∈ {line, step}`; counters render STEP, gauges render LINEAR (approver B5 + leader
  ruling). It is an **additive per-name member** under the AM-7 bump policy (no `schema_version`
  bump; consumers ignore unknown members). The roster itself is enumerated in phase-2 task 2.7b
  (12 families) and is carried at registration time.
- The family-level **`role`** is the FR-C2 classification carried from the chart-primary roster:
  `"chart"` for a roster family (chart-primary — series required), `"stat"` otherwise
  (stat-primary — current value only). Derived from the registration-time metadata, so a newly
  wired roster family is chart-primary **without any door edit** (AM-3; a static table would break
  the incremental-onboarding fence and would serve zero families at Phase 3).
- Runtime introspection is the serving truth (registered objects + `allowed_values` + the
  registration-time family metadata, AM-3); the markdown catalog is authoring truth and is never
  read at request time. **No second family table.**
- `cardinality_class` is `closed|open|none` (AM-2): a metric with `allowed_values` for every label
  key is `closed`; a metric that declares label keys without a frozen value set is `open` (the two
  guard counters) and renders `labels: null` and is never value-enumerated; a metric with no label
  keys is `none`.
- `live_series_count` = the number of live label combinations observed in the family.
  `cardinality_contribution` = `live_series_count × cardinality_weight` (the family's weighted
  contribution to the global ≤2000-series budget; `cardinality_weight` defaults 1).
- `source_idiom` and `durability` are registration-time metadata (AM-3) with derived defaults:
  `source_idiom ∈ {registry, postgres_census, wedge_capture}` (default `registry`),
  `durability ∈ {process, durable}` (default `process` — in-RAM, reset on restart).

### 4.4 `GET /api/metrics/snapshot`

```
{…envelope header…, series:[{name, labels, kind, monotonic, value, last_publish_age_s}]}
```

plus `?family=` / `?name_prefix=` filters. Staleness is explicit metadata, never a silent stale
value: `last_publish_age_s` = seconds since the metric's last publish, via the **new public
`Metric.last_publish_monotonic()` accessor** (AM-13 — the private `_last_publish` is not an
acceptable dependency).

Typing honesty (AM-4): `kind ∈ {gauge, counter, histogram}` (**no `summary`** — the registry never
produces it; `register_summary` renders as a histogram, `checkpoint_metrics.py:985`). Every series
carries `monotonic: bool` (counters are non-decreasing **within one `restart_epoch`**). Histogram
series carry `value: null` + `histogram: {count, sum, buckets}` **as stored** (not Prom-cumulative).

### 4.5 `GET /api/metrics/series`

```
{…envelope header…,
 window:{from_utc, to_utc, interval_s, interval_s_source:"observed"},
 last_sample_age_s, stale,
 series:[{name, labels, kind, monotonic, points:[[t, v],…]}]}
```

plus `?series=` (label-aware, AM-1), `?names=` (≤ 50, name-level), `?window_s=` (clamped to the
buffer window), `?max_points=` (server-side **bucket-max** downsample — named, never every-kth;
**default `max_points` = 240** — approver B1 + leader ruling), `?cursor=`.

- `interval_s` is the **observed** effective spacing `max(sampler, publisher)` = 15 s at defaults
  (AM-15) — never the configured cadence (the cadence-key deviation is DEV-3).
- `stale: bool` when `last_sample_age_s > 3 × effective interval` — a wedged sampler surfaces as
  **staleness**, never a silently frozen line (AM-5).
- `window_s` anchors at the server's `generated_at_utc`; a cursor pins its evaluation instant.
- `max_points` default 240 is the floor AC-A.3 / AC-C.1 require (60 min × 4/min at the 15 s
  effective spacing), so the default and the acceptance criteria agree by construction; the FE
  Charts tab requests `window_s=3600` ⇒ 240 points ⇒ 15 s effective resolution out-of-the-box.
- The residual on-loop encode is **bounded and pinned**: `max_points` ≤ 240 × `?names=` ≤ 50 ⇒
  ≤ 12 000 points ≈ 3.6 ms (AM-12 is **BUILD-OFF-LOOP ONLY** — `db_offload.offload` returns a
  Python object and FastAPI still encodes on the loop; no pre-encoded-`Response` precedent is
  introduced).

### 4.6 Non-Prometheus deviations clause (AM-8)

The envelope borrows Prometheus **vocabulary** but its semantics are local and are declared as
such: buckets **as stored**; monotonicity **within one `restart_epoch`** only; durations in
**seconds**. No exposition-format leakage into the JSON envelope.

### 4.7 Read-path purity (load-bearing)

The query path reads **published registry state and ring buffers only**. It never renders, never
acquires a metric lock (the no-lock pin allowlists `registered_metrics()` — the registry-level
lock, not a metric lock), never performs a database read, and **never touches the switch** (AM-18).
Database-backed producers (F10 queue counts, F13 instance counts, F18 audit counts) are pulled by
the **sampler thread** into the registry via **one-shot direct connects** (AM-11 — a persistent
per-adapter connection is forbidden: pool-in-disguise ⇒ the idle-in-transaction class); the door
reads the published gauge. The response BODY is **built off the event loop** (AM-12).

```query-routes
GET /api/metrics/families
GET /api/metrics/snapshot
GET /api/metrics/series
```

## 5. Error vocabulary, pagination, cursor, staleness, cardinality class

**Error vocabulary (never a 500 for an expected condition):**

```error-vocabulary
404 unknown_family
400 invalid_label_filter
400 invalid_cursor
503 registry_unavailable
503 AUTH_NOT_CONFIGURED
403 scope_insufficient
```

- `404` unknown family (typed body)
- `400` invalid label filter
- `400 invalid_cursor` (expired/invalid cursor — AM-6)
- `503` registry unavailable
- `503 AUTH_NOT_CONFIGURED` (fail-closed, **before any registry read**)
- `403` scope miss

**Pagination/bounds:** max families per response, max samples per response, cursor; a single
response must stay under a stated byte ceiling; `max_points` defaults **≤ 240** (AM-12, approver
B1 + leader ruling) so the off-loop body stays bounded.

**Cursor (AM-6):** `next_cursor: string|null`; opaque; pins the evaluation instant + position; an
invalid/expired cursor (older than the buffer's oldest point) ⇒ `400 invalid_cursor`; the byte
ceiling produces a bounded body + a cursor, **never a silent truncation**; the families route is
bounded the same way. (AC-A.8 is unsatisfiable without this.)

**Window semantics (AM-5):** `window_s` anchors at the server's `generated_at_utc`; a cursor pins
its evaluation instant; `max_points` downsample is **bucket-max**, named, never every-kth. The
staleness fields are `last_sample_age_s` + `stale: bool` — stale when
`last_sample_age_s > 3 × effective interval`.

**Cardinality class (AM-2):** `closed|open|none`; `open` names render `labels: null` and are never
value-enumerated.

## 6. The normative kill-switch truth table (architect v2 §6 — adopted verbatim)

| Surface | Auth configured | Switch | Runtime | Scope | Result |
|---|---|---|---|---|---|
| `/api/metrics/{families,snapshot,series}` | OFF | any | any | any | **503 `AUTH_NOT_CONFIGURED`** — before any provider read (fail-closed) |
| the three query routes | ON | ON | any | `maintenance:read` | **200** — registry-sourced series live |
| the three query routes | ON | OFF | any | `maintenance:read` | **200** — registry-sourced series **identical** to the switch-ON case; only the permitted diffs below appear |
| the three query routes | ON | any | any | scope miss | **403** |
| the three query routes | ON | any | any | no session | **401** |
| the three query routes | ON | any | non-PG | `maintenance:read` | **200** — F6 `availability` → `census_not_running` (the switch state is NOT inferable) |
| `:9798` listener body | n/a (unauthenticated, loopback-only) | OFF | any | n/a | **200 valid empty** — no `ensemble_metrics_up`, no family series; the socket is still bound |
| `:9798` listener body | n/a | ON | any | n/a | **200** — full exposition body |

**Five pinned invariants (every one becomes a Phase-3 pin):**

```truth-table-invariants
auth_behaviour_identical_across_switch_states
registry_series_set_identical_for_non_switch_derived_families
only_permitted_body_differences
check_site_census_equals_two
no_direct_switch_read_in_query_package
```

1. **Auth behaviour is identical across switch states** (status codes, error vocabulary, scope
   decision, fail-closed seam).
2. **The registry-sourced series set is identical** for every family whose availability reason is
   not switch-derived.
3. **The only permitted body differences** are (i) F6 `availability` → `census_gated`,
   (ii) `activation.census_enabled` → false, (iii) `last_publish_age_s` growth — all by design and
   excluded from the invariance claim.
4. **The check-site census over `daemon/` remains exactly 2** (the listener body call line
   `metrics_listener.py:419`; the census tick call line `metrics_census.py:340`).
5. **No direct switch read in the query package or the routes** — `census_gated` is sourced from
   the public `CensusSampler.gated` property (created by phase-2 task 2.4a; a read-through of the
   EXISTING private `_gate()`; **no new check site**), and the query package NEVER reads the
   switch.

**Availability reason set (closed):**

```availability-reasons
census_gated
census_not_running
capture_off
series_absent
listener_disabled
```

`census_gated` (switch OFF — sourced from the public `CensusSampler.gated` property, AM-18) ·
`census_not_running` (non-postgres runtime) · `capture_off` (wedge capture not active — F1/F3) ·
`series_absent` (family registered, no observation yet) · `listener_disabled` (surface note for
the exposition listener only; the query door never emits it). `proc_unavailable` is **not** in the
v1 set (F2 is out of the subset — AM-25). Each reason is reachable by exactly one runtime fact:
census-derived family + switch OFF ⇒ `census_gated`; census-derived family + census not running /
non-postgres ⇒ `census_not_running`; capture-derived family + capture inactive ⇒ `capture_off`;
registered family with no observation ⇒ `series_absent`.

## 7. Promote-time disclosure (AM-19-corrected, D1/D2/D3/D4/D5 + the cross-cutting list)

> central-metrics-v1 ships the metrics query API (`GET /api/metrics/families`,
> `GET /api/metrics/snapshot`, `GET /api/metrics/series`) AUTH-GATED ONLY — scope
> `maintenance:read`, 503 `AUTH_NOT_CONFIGURED` fail-closed before any registry read — and
> deliberately does NOT consult `ENSEMBLE_METRICS_ENABLED`, consistent with the shipped
> `/api/metrics` text-exposition route. The unauthenticated `:9798` exposition listener REMAINS
> default-OFF (`limits.metrics_enabled=False` unchanged) — **at the BODY level: the socket still
> binds and serves a valid empty 200 while OFF** (AM-19(a)). Enabling it for Prometheus scraping
> is a live managed-key flip (≤ 5 s TTL, no restart).
> **The query door has NO kill-switch of its own — its emergency-off lever is token revocation of
> the `maintenance:read` grant** (AM-19(c); the runbook names the lever). Switch-OFF does NOT
> blank the query API: the registry accumulates ungated, so query routes serve live data in both
> switch states; only the listener body goes empty and the census sampler's DB work stops (F6
> `ensemble_pg_*` families go dark with `availability.reason="census_gated"`). Emergency-off
> semantics are UNCHANGED and NARROWER than they appear: the switch never touches authenticated
> surfaces.
>
> **Managed keys:** defaults, bounds, types and restart columns of the seven metrics managed keys
> are untouched; **one** managed key (`limits.metrics_sampler_interval_s`) receives a
> **description-only** amendment because this lane consumes it (AM-15/AM-19(b)) — the key is the
> trend-sampler TICK (boot-applied), and the envelope's `window.interval_s` reports the OBSERVED
> effective spacing `max(sampler, publisher)` = 15 s at defaults, never the configured cadence.
>
> **Trend storage is IN-MEMORY ONLY** (D3/AD1): an array-backed ring buffer, 16 B/point,
> `POINT_BUDGET = 750 000` ⇒ 11.4 MiB worst case; lost on restart by design — a restart is visible
> via `restart_epoch` (AM-14), never a silent gap.
>
> **Scope disclosure (AM-20):** `maintenance:read` ALSO confers checkpoint-cleanup-EXECUTE
> capability (`daemon/routers/maintenance.py`). The scope split (`metrics:read`) is a NAMED
> COMMITMENT (LR-1, `decisions.md` §D6), not built in v1.
>
> **Cardinality budget (AM-26/LR-2):** +274 series → post-lane 363 ≈ 18.2 % (F7c-inclusive; 361 on
> the ledger-rows basis). `VOCABULARY_VERSION` bumps 1→2 (AD5).
>
> **FE panel copy:** "no metrics available yet" — never "metrics disabled" (AM-19(d)).
>
> **Zero migrations, zero new backend dependencies, zero new npm dependencies. No live promote in
> this lane** — the user's 3-factor nonce ceremony owns it.

## 8. Gate-pack declaration (approver N-a registration obligation)

Nine gate-leg pack ids. **The plan set commits the ids and their declared node scopes; the actual
`.agents/tester/PACKS.md` rows land with the lane's evidence (the tester lane owns the file)** —
today `.agents/tester/PACKS.md` carries **0** of the nine. A leg invokes its D2 set **by pack id**,
so an unregistered id makes the leg unrunnable.

```gate-pack-ids
central_metrics_contract_doc
metrics_p2_dual_side
metrics_p3_dual_side
metrics_scope_cage_dual_side
metrics_p4_w1_dual_side
metrics_p4_w2_dual_side
metrics_p4_w3_dual_side
metrics_fe_dual_side
central_metrics_ledger_reconciliation
```

| Leg | Tier | D1 packs (lane-owned, HEAD-only; MUST pass at HEAD) | D2 registered pack (dual-side) |
|---|---|---|---|
| P1-G1 | 0 | `central_metrics_contract_doc` | none (docs-only diff; changed-pack PASS) |
| P2-G1 | 1 | `metrics_query_provider` · `metrics_history_ring` · `metrics_sampler` · `metrics_registry_narrowing` · `metrics_vocabulary_amendment` | `metrics_p2_dual_side` |
| P3-G1 | 2 | `metrics_query_routes` · `metrics_query_auth_matrix` · `metrics_query_pagination` · `metrics_query_switch_invariance` · `metrics_query_api_wiring` | `metrics_p3_dual_side` |
| P3-G2 | 0/1 | — | `metrics_scope_cage_dual_side` |
| P4-G1..G3 | 1×3 | `metrics_family_<family>` (per family) + `metrics_family_adjudication` (per cluster) | `metrics_p4_w1/w2/w3_dual_side` |
| P5-G1 | 1 | `metrics-fe-models` · `metrics-fe-service` · `metrics-fe-svg-chart` · `metrics-fe-page` · `metrics-fe-bindings-pins` | `metrics_fe_dual_side` |
| P6-G1 | 0 | `central_metrics_ledger_reconciliation` | none (docs-only diff; changed-pack PASS) |

**D1/D2 asymmetry statement:** D1 packs exist only at HEAD (their absence at BASE is expected —
neither a net-new failure nor a fix); D2 is the ONLY set carrying the FAILED-digest diff; HEAD runs
D1 + D2, BASE runs D2 with an identical command shape.

**Mutation labels:** `P{n}-Mn` (phase-scoped namespace; the mutation→test table lands in
`.agents/tester/PACKS.md` with the lane's evidence; the executor fills the observed digests).
Declared legs: P1-M1..P1-M3 · P2-M1..P2-M15 · P3-M1..P3-M12 · P4-M1..P4-M13 · P5-M1..P5-M11 ·
P6-M1..P6-M12. **Every behavioural pin is planted independently and shown RED→GREEN.**

**Node counts are PROJECTIONS** (AM-22) — the executor re-derives them with
`pytest --collect-only -q` and records the derived numbers; hand-maintained node counts are
forbidden (node identities are rebuilt from the collect-only output).

**Slicing proof obligation:** before any FAILED-list comparison, produce `--collect-only -q` output
proving the selected node set equals the declared pack scope (no silent over/under-selection).

**Pre-exclusion registry (applies to every leg):** `regression_performance` (> 300 s) · e2e specs
that need a live daemon on `:8079` (→ Release Gate) · `tests/packs/g7` (module-level `sys.exit`
collection trap — run via packs, never bare pytest).

**Expected-skip census (AM-22):** the `no_xdist` self-skip (`tests/conftest.py`) · PG-gated packs
that skip without `PG_TEST_ALLOWED_DBS` · any pack whose skip count differs between the HEAD and
BASE legs is a **finding**, not a convenience.

**Mandatory recipe load (W10):** before executing a leg, the recipe skill body is READ from its
deterministic path (never `skill_search` discovery): Tier 0 ⇒ changed-pack PASS; Tier 1 ⇒
`.agents/tester/skills/blast-radius-leg/blast-radius-leg.md`; Tier 2 ⇒
`.agents/tester/skills/whole-tree-base-proven-diff/whole-tree-base-proven-diff.md`; FAILED-digest
comparisons load `.agents/tester/skills/failed-digest-equality-gate/failed-digest-equality-gate.md`.

**Packs, never bare pytest.** Every leg names its `.agents/tester/PACKS.md` pack; bare
`pytest <file>` is not an acceptable gate invocation.
