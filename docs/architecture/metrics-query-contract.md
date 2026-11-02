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
| 2 | `/api/metrics` **text exposition** | NO — never consults the switch | YES — `maintenance:read` (`daemon/routers/metrics.py:96`, `:143`) | served in both switch states | auth seam `metrics.py:117-133` |
| 3 | **Query routes** (`families` / `snapshot` / `series`) | NO — never switch-gated (D-11b / D1(b) wins; AM-17) | YES — `maintenance:read` on all three + the same fail-closed 503 seam | **availability, not emptiness** — the door serves live registry data in both switch states | this contract §6 |
| 4 | **Census DB work** | YES — the census sampler performs NO database work while OFF | n/a (internal thread) | the sampler keeps ticking; its DB leg is skipped; F6 families go dark with `availability.reason="census_gated"` | gate method `metrics_census.py:347-355`; call line `:355` |
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
a read-through of the EXISTING private `_gate()` at `metrics_census.py:347-355` — **no new check
site**; the census over `daemon/` stays exactly 2). **The query package NEVER reads the switch.**

### 4.2 Series identity (AM-1)

A series is `(name, labels)`; the labels are the complete discriminator; the canonical
serialization is the registry's `_label_key` (sorted `(k, v)` pairs,
`daemon/checkpoint_metrics.py:481`). Ring keys and any future item-#2 watch-tool reference use the
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

The route is **bounded** (W-4): `?offset=` / `?limit=` page the series (the limit is clamped to
`MAX_SNAPSHOT_SERIES` = 500), the byte ceiling applies, and a bounded body carries the
continuation cursor — the ceiling promise is the contract's safety property, so this route is not
exempt from it. The body carries `truncated: bool` (additive — the other two routes already
carried it).

Typing honesty (AM-4): `kind ∈ {gauge, counter, histogram}` (**no `summary`** — the registry never
produces it; `register_summary` renders as a histogram, `checkpoint_metrics.py:1188`). Every series
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
403 SCOPE_INSUFFICIENT
```

> **The 403 token is the SHIPPED spelling** — `SCOPE_INSUFFICIENT`
> (`daemon/auth/decorators.py:75-78` — `_SCOPE_403_DETAIL = {"error":
> "SCOPE_INSUFFICIENT", "message": "Token lacks required scope"}`). This block previously
> rendered it lowercase as `scope_insufficient`; that was a **token-rendering** defect in the
> documentation, not a behavioural one, and it is amended here to the shipped form. **The wire
> and the decorator are NOT changed** — the decorator is the pre-existing auth seam and is
> outside this lane's file set. The sibling blocks in this contract already use the shipped
> spelling (`AUTH_NOT_CONFIGURED`, `registry_unavailable`, `unknown_family`,
> `invalid_label_filter`, `invalid_cursor`).

- `404` unknown family (typed body)
- `400` invalid label filter — a malformed label-aware selector, or a
  `?names=` request that exceeds the 50-name ceiling (the filter is
  REJECTED, never silently truncated)
- `400 invalid_cursor` (expired/invalid cursor — AM-6)
- `503` registry unavailable
- `503 AUTH_NOT_CONFIGURED` (fail-closed, **before any registry read**)
- `403` scope miss

**Pagination/bounds:** max families per response, max samples per response, cursor; a single
response must stay under a stated byte ceiling; `max_points` defaults **≤ 240** (AM-12, approver
B1 + leader ruling) so the off-loop body stays bounded.

**THE THREE ROUTES ARE BOUNDED THE SAME WAY (W-2 / W-3 / W-4, fix cycle 1).**

* **`/snapshot` is bounded like the other two** — it was NOT exempt from
  the byte ceiling. It now accepts `?offset=` / `?limit=` (the limit is
  CLAMPED to the snapshot ceiling, `MAX_SNAPSHOT_SERIES` = 500), the body
  is bounded by the byte ceiling, and a bounded body carries the
  continuation cursor. The response additionally carries `truncated:
  bool` — an ADDITIVE field (the other two routes already carried it),
  never a wire break.
* **`/series` bounds the EMITTED body, not only the REQUEST** — the
  selector ceiling (50) bounds the *requested* selectors, and a request
  with no selector matched EVERY published series. The page is therefore
  bounded on two axes during the OFF-LOOP build: the emitted-series
  ceiling (`MAX_SERIES` = 500) and the **total emitted-points** ceiling
  (`MAX_TOTAL_POINTS` = 12 000 — the number the AM-12 claim is about: it
  is now an ENFORCEMENT, not the constant identity `240 × 50`).
* **A bound that drops something is never silent** — the builder sets
  `truncated` when it drops something, and a body that dropped something
  ALWAYS carries the continuation cursor; `truncated` also marks a partial
  view (a page reached with a cursor) and such a page may carry no cursor.
* **THE BYTE-CEILING PROBE AND THE TRIM RUN OFF THE EVENT LOOP** — the
  probe is a full JSON dump and the trim is a binary search over it; both
  run inside the off-loop page builder, never in the route handler.

**Cursor (AM-6):** `next_cursor: string|null`; opaque; pins the evaluation instant + position; an
invalid/expired cursor (older than the buffer's oldest point) ⇒ `400 invalid_cursor`; the byte
ceiling produces a bounded body + a cursor, **never a silent truncation**; the families route is
bounded the same way. (AC-A.8 is unsatisfiable without this.)

**Cursor precedence (ND-5).** The cursor-expiry probe runs **FIRST**: all three page
builders call `_expire_cursor(provider, cursor)` before any other check
(`daemon/routers/metrics.py:465` families · `:504` snapshot · `:539` series). On
`GET /api/metrics/snapshot` the **unknown-family check sits downstream of it** — so an
**expired cursor combined with an unknown `family` answers `400 invalid_cursor`, not
`404 unknown_family`** (the probe at `:504` precedes the family check at `:505-508`, so the
expiry wins). `/snapshot` is the only route with a family check downstream of the probe; the
other two routes have no family check in the builder at all.

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

> **SUPERSEDED as the ceremony text — the FINAL disclosure is published at
> [`docs/architecture/metrics-promote-disclosure.md`](metrics-promote-disclosure.md)** (Phase 6,
> task 6.5), together with the ceremony checklist. The block below is the **Phase-1 draft**,
> retained as the frozen contract's record of what the disclosure must carry; the final text
> carries the same eight cross-cutting items, **verified against the shipped code's constants**
> rather than copied from this draft. Where the two differ, the **final text governs**.

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

### The re-derived node counts (P2-G1, 2026-11-02 — the PROJECTIONS re-derived)

The figures below are the executor's `--collect-only -q` output, NOT the plan's
projections. They are the registration input for the pack rows the tester lane owns.

| Pack id | Leg | D1 / D2 | Re-derived nodes | Per-file breakdown |
|---|---|---|---|---|
| `central_metrics_contract_doc` | P1-G1 | D1 | **21** | `tests/unit/metrics/test_central_metrics_contract_doc.py` 21 |
| `metrics_p2_dual_side` | P2-G1 | D2 | **281** | `test_checkpoint_metrics_registry` 45 · `test_checkpoint_metrics_vocabulary` 18 · `test_metrics_gate` 35 · `test_metrics_listener` 41 · `test_metrics_listener_registry` 15 · `test_metrics_census` 34 · `test_metrics_managed_keys` 65 · `test_metrics_live_managed_values` 16 · `test_metrics_refresh_path` 12 |
| `metrics_query_provider` | P2-G1 | D1 | **66** | `tests/unit/metrics/test_metrics_query_provider.py` 66 |
| `metrics_history_ring` | P2-G1 | D1 | **28** | `tests/unit/metrics/test_metrics_history_ring.py` 28 |
| `metrics_sampler` | P2-G1 | D1 | **20** | `tests/unit/metrics/test_metrics_sampler.py` 20 |
| `metrics_registry_narrowing` | P2-G1 | D1 | **12** | `tests/unit/metrics/test_metrics_registry_narrowing.py` 12 |
| `metrics_vocabulary_amendment` | P2-G1 | D1 | **11** | `tests/unit/metrics/test_metrics_vocabulary_amendment.py` 11 |

**The P2-G1 leg's D2 set was invoked by its declared COMPOSITION** (the nine files
above), not by pack id: the nine pack ids are still **unregistered** in
`.agents/tester/PACKS.md` (the tester lane owns that file and the rows land with
this lane's evidence). The pack id + the node scope + the re-derived count are
recorded here so the registration is a copy, not a re-derivation.

**A FINDING for the pack registration (P2-G1):** the declared `metrics_p2_dual_side`
composition does **not** reach `tests/unit/persistence/test_checkpoint_perf_logging.py`,
which the blast-radius derivation DOES reach (it imports `daemon.checkpoint_metrics`
and pins the F7 op vocabulary). The F7 narrowing invalidates two of that file's
assertions; they were re-pointed at the adjudicated vocabulary (a DISCLOSED test
edit — the file's own premises were void under the D-7 ruling). **The registered
composition should be widened to include that file**, otherwise the next lane that
narrows a vocabulary repeats the miss.

### The re-derived node counts (P3-G1/P3-G2, 2026-11-02 — the PROJECTIONS re-derived)

The figures below are the executor's `--collect-only -q` output, NOT the plan's
projections. They are the registration input for the pack rows the tester lane owns.

| Pack id | Leg | D1 / D2 | Re-derived nodes | Per-file breakdown |
|---|---|---|---|---|
| `metrics_query_routes` | P3-G1 | D1 | **30** | `tests/unit/metrics/test_metrics_query_routes.py` 30 |
| `metrics_query_auth_matrix` | P3-G1 | D1 | **25** | `tests/unit/metrics/test_metrics_query_auth_matrix.py` 25 |
| `metrics_query_pagination` | P3-G1 | D1 | **24** | `tests/unit/metrics/test_metrics_query_pagination.py` 24 |
| `metrics_query_switch_invariance` | P3-G1 | D1 | **18** | `tests/unit/metrics/test_metrics_query_switch_invariance.py` 18 |
| `metrics_query_api_wiring` | P3-G1 | D1 | **13** | `tests/unit/metrics/test_metrics_query_api_wiring.py` 13 |
| `metrics_query_scope_cage` | P3-G2 | D1 | **7** | `tests/unit/metrics/test_metrics_query_scope_cage.py` 7 |
| `metrics_p3_dual_side` | P3-G1 | D2 | **302** | the `metrics_p2_dual_side` constituents 281 · `tests/unit/routers/test_metrics_route.py` 9 · `tests/unit/services/test_metrics_api_wiring.py` 12 |
| `metrics_scope_cage_dual_side` | P3-G2 | D2 | **12** | `tests/unit/test_router_scope_manifest.py` 7 · `tests/integration/test_router_class_manifest.py` 5 |

**The `gate-pack-ids` block above is the PLAN's declared set (nine ids) and is NOT
widened.** The P3-G2 D1 pin (the three-way cage form) has no id in the plan's table —
its home is `tests/unit/metrics/test_metrics_query_scope_cage.py` (7 nodes), recorded
here as the registration input. A tenth id would widen the frozen nine-id block, which
the `central_metrics_contract_doc` pack pins at exactly nine.

**The P3-G2 D1 pin lives in its OWN file on purpose:** the two-way cage form is the
defect class the fold ledger recorded, and a D1 pin embedded in a D2 file would change
that file's node count between the legs — the slicing proof would no longer hold.

**A FINDING for the pack registration (P3-G1) — the declared composition does not
reach the lane's own blast radius.** The derivation (the changed production surfaces
`daemon/api.py` · `daemon/routers/metrics.py` · `daemon/services/metrics_query/**` and
the two router manifests, and the test files that reach them) reaches **13 files** the
declared `metrics_p3_dual_side` composition does not carry:

`tests/unit/persistence/test_checkpoint_perf_logging.py` (the P2-G1 finding, still
open) · `tests/unit/persistence/test_checkpoint_read_instrumentation.py` ·
`tests/unit/checkpoint_adapter/test_checkpoint_read_cache.py` ·
`tests/unit/test_log_hygiene.py` ·
`tests/unit/tools/test_readiness_leak_signal_consumer.py` ·
`tests/unit/tools/test_tool_category_artifact.py` ·
`tests/unit/test_post_restart_arm_notify_no_parallel.py` ·
`tests/test_custom_agents_router_authz.py` · `tests/integration/test_scope_matrix.py` ·
`tests/integration/test_consumer_enablement.py` ·
`tests/integration/test_get_instance_messages_observed_count_zero.py` ·
`tests/integration/test_message_metadata_retry_recovery.py` ·
`tests/integration/test_message_metadata_send_message_revive.py`.

They ran as a **DISCLOSED supplementary dual-side leg** (226 nodes; both legs green;
the FAILED digests byte-identical ⇒ net-new EMPTY). The REGISTERED composition should
be widened so the next lane inherits the coverage — the same finding the P2-G1 leg
recorded, now closed on the evidence rather than on the declaration.

**P3 DEVIATION (recorded, not silent) — the 3-carrier allowlist's THIRD carrier is
spelled TWO ways.** The contract names `# HELP ensemble_checkpoint_op_seconds …` — the
ALIAS spelling of the saver-op-latency histogram. The same metric renders the IDENTICAL
help text under its CANONICAL name (`message_api_saver_op_latency_seconds`), and an
alias-only mask leaves that canonical line as a FALSE delta: the masked BASE and HEAD
bodies differed on exactly that one line. The mask therefore covers BOTH spellings of
the ONE carrier — an allowlist that covered one spelling of a two-spelling carrier would
be incomplete against its own carrier (the same class as the P2-G1
composition-vs-blast-radius finding). The contract's own text is unchanged: it names
the CARRIER, not its spellings.

### The re-derived node counts + the P4 evidence (P4-G1/G2/G3, 2026-11-02)

The figures below are the executor's `--collect-only -q` output and the recorded gate
digests — NOT the plan's projections. They are the registration input for the pack rows the
tester lane owns.

| Pack id | Leg | D1 / D2 | Re-derived nodes | Per-file breakdown |
|---|---|---|---|---|
| `metrics_family_f0_3` | P4-G1 | D1 | (in `metrics_family_w1`) | `tests/unit/metrics/test_metrics_family_w1.py` |
| `metrics_family_f1` | P4-G1 | D1 | (in `metrics_family_w1`) | the same file |
| `metrics_family_f3` | P4-G1 | D1 | (in `metrics_family_w1`) | the same file |
| `metrics_family_f4_f5` | P4-G1 | D1 | (in `metrics_family_w1`) | the same file |
| `metrics_family_f6` | P4-G1 | D1 | (in `metrics_family_w1`) | the same file |
| `metrics_family_f7_completion` | P4-G2 | D1 | (in `metrics_family_w2`) | `tests/unit/metrics/test_metrics_family_w2.py` |
| `metrics_family_f8_partial` | P4-G2 | D1 | (in `metrics_family_w2`) | the same file |
| `metrics_family_f9` | P4-G2 | D1 | (in `metrics_family_w2`) | the same file |
| `metrics_family_f10` | P4-G2 | D1 | (in `metrics_family_w2`) | the same file |
| `metrics_family_f11_partial` | P4-G2 | D1 | (in `metrics_family_w2`) | the same file |
| `metrics_family_f12` | P4-G2 | D1 | (in `metrics_family_w2`) | the same file |
| `metrics_family_f13_partial` | P4-G2 | D1 | (in `metrics_family_w2`) | the same file |
| `metrics_family_f17` | P4-G3 | D1 | (in `metrics_family_w3`) | `tests/unit/metrics/test_metrics_family_w3.py` |
| `metrics_family_f18_partial` | P4-G3 | D1 | (in `metrics_family_w3`) | the same file |
| `metrics_family_f21_partial` | P4-G3 | D1 | (in `metrics_family_w3`) | the same file |
| `metrics_family_adjudication_w1/w2/w3` | P4-G1..G3 | D1 | **24** | `tests/unit/metrics/test_metrics_family_adjudication.py` |
| `metrics_p4_w1_dual_side` | P4-G1 | D2 | **494** | the 21 derived files (below) |
| `metrics_p4_w2_dual_side` | P4-G2 | D2 | **494** | the same 21 files |
| `metrics_p4_w3_dual_side` | P4-G3 | D2 | **494** | the same 21 files |

**The D1 pack ids are CLASSES inside three files** (one file per cluster + the shared
adjudication file) — the per-family ids above name the CLASS each family pack's pins live
in, so the pack rows the tester lane registers are one row per family with its class's node
count.

**The D2 composition (derived, not declared).** The plan's declared composition is
"vocabulary (18) + registry (45) + the changed family's own base pack". The DERIVATION
(the blast-radius recipe's three passes over the stage's diff — `daemon/persistence.py`,
`daemon/services/metrics_query/sampler.py` and the new `wiring/` package) reaches **21**
dual-side files / **494** nodes: the two checkpoint-metrics packs (45 + 18), the persistence
mirror (`test_checkpoint_perf_logging`, `test_checkpoint_read_instrumentation`,
`test_get_instance_messages_no_alist`), the whole `tests/unit/metrics/` substrate (the
sampler, the provider, the ring, the five query-door packs, the narrowing + vocabulary
packs, the contract-doc pack), the persistence module's own packs
(`tests/test_persistence.py`, `test_checkpoint_adapter_resilience.py`, the two
`tests/unit/test_persistence_*` files) and the contract doc's own pack. The declared
composition's "the changed family's own base pack" is INSIDE this set.

**A FINDING for the pack registration (P4) — the declared composition is short by 17 files.**
The plan's declared D2 set (vocabulary + registry + one base pack) reaches **4** files /
~120 nodes; the derivation reaches **21** files / **494** nodes. The widened set ran as a
DISCLOSED supplementary dual-side leg (the recipe's rule from P2-G1/P3-G1): **20 further
files / 280 nodes**, both sides, FAILED digests byte-identical ⇒ net-new EMPTY. The
registered composition should be the DERIVED set.

**The gate results (Tier 1 ×3).**

| Leg | HEAD (D1 + D2) | BASE (D2) | FAILED digest (BARE-ID / GREP-FULL) | Verdict |
|---|---|---|---|---|
| P4-G1 (W1) | 534 passed · 1 failed · **exit 1** | 493 passed · 1 failed · **exit 1** | `d78b09cf2549c371` / `d9bff11a731d8ba3` — **IDENTICAL both sides** | net-new **EMPTY** |
| P4-G2 (W2) | 536 passed · 1 failed · **exit 1** | 493 passed · 1 failed · **exit 1** | `d78b09cf2549c371` / `d9bff11a731d8ba3` — **IDENTICAL both sides** | net-new **EMPTY** |
| P4-G3 (W3) | 528 passed · 1 failed · **exit 1** | 493 passed · 1 failed · **exit 1** | `d78b09cf2549c371` / `d9bff11a731d8ba3` — **IDENTICAL both sides** | net-new **EMPTY** |
| the widened supplementary leg (all three) | 295/296/289 passed · 2 failed · **exit 1** | 278 passed · 2 failed · **exit 1** | `ec0572e69c9ab178` / `041380df27ec0d4e` — **IDENTICAL both sides** | net-new **EMPTY** |

**The ONE failure on every D2 leg is the KNOWN base red**
(`test_rdrs_lanes_come_from_the_config_kill_switchs` — the 8-vs-7 RDRS lane drift, identical
on both legs, base-proven). The widened leg's two failures are also base-proven
(`test_the_stop_event_is_honored_mid_batch`,
`test_the_field_defaults_mirror_the_constants`). **Zero collection errors on every leg**
(the `N errors` summary line is empty everywhere — the discipline finding from P2/P3: a
collection-time error emits no `ERROR <nodeid>` line).

**The mutation table — P4-M1..P4-M13, planted independently, 13/13 RED→GREEN.**

| Label | Mutation | RED node |
|---|---|---|
| `P4-M1` | an OUT metric registered (F11's claim latency) | `metrics_family_adjudication_*` (the OUT-absence pin) |
| `P4-M2` | a producer module grows an edit | the no-producer-instrumentation diff pin |
| `P4-M3` | the F12 accumulation removed (the per-tick value published raw) | `metrics_family_f12` (the exact-value pin) |
| `P4-M4` | F4's native and F5's derived collapsed into one family | `metrics_family_f4_f5` |
| `P4-M5` | F6 rendered on a non-postgres runtime | `metrics_family_f6` |
| `P4-M6` | the F18 `timeout` folded into `error` | `metrics_family_f18_partial` |
| `P4-M7` | the F21 phase families registered | the F21 absence pin |
| `P4-M8` | the F7 bytes call site reverted to 0 | `metrics_family_f7_completion` |
| `P4-M9` | a label key registered without `allowed_values` | `metrics_family_adjudication_*` (the AM-2 pin) |
| `P4-M10` | a family registered without its metadata kwargs | `metrics_family_adjudication_*` (the AM-3 pin) |
| `P4-M11` | a persistent per-adapter DB connection | the DB-pull discipline pin |
| `P4-M12` | F2 registered (the value ruling inverted) | `metrics_family_adjudication_w1` |
| `P4-M13` | the WS-A ancestry check inverted (rc=1 treated as a PASS) | the F0.3 conditional pack |

**The P4 VERDICTS (recorded, not silent).**

* **WS-A ancestry (task 4.4):** `git merge-base --is-ancestor 53547b915 HEAD` ⇒ **rc=0 ⇒
  PASS**. F0.3 ships (+8 series) and its family pack runs. (The first implementation of the
  probe compared a COMMIT to a PATH — git answers 128, which the rc semantics map to STOP;
  the probe now compares two commit-ishs. The rc SEMANTICS are pinned by the pack: rc=0 ⇒
  PASS, rc=1 ⇒ DEFER, anything else ⇒ STOP.)
* **F21 conditionality (task 4.8):** **OUT — confirmed on the evidence.**
  `journal_history_append` writes `{ts, event, detail}` with `event` drawn from the
  ALERT-CLASS kind set (`upgrade_journal.py:645-649` — halt / refusal / rollback /
  sweep_rollback / quarantine), and the promote PHASE trail lives in the journal's EXTENSION
  fields, not in `history[]` ⇒ no clean pure-read derivation ⇒ the F21 phase families stay
  OUT (+30 series not claimed) and their absence is pinned (`P4-M7`).
* **F13 waiting children (task 4.6):** the durable query EXISTS —
  `SELECT COUNT(*), EXTRACT(EPOCH FROM (NOW() - MIN(last_activity_at))) FROM instances WHERE
  status = 'waiting_children'` — so the two gauges stay IN.
* **The vocabulary version:** **NOTHING to bump.** The P1 contract freezes
  `vocabulary_version: 2` ("2 after this lane") and P2's criterion is
  `VOCABULARY_VERSION == 2` — Stage A delivered that bump. P4's new families' label
  vocabularies are part of the SAME frozen annex (D5's F18 row puts the amended audit
  vocabulary on the 1→2 bump), so the registry-side version stays **2**. A bump to 3 would
  break the frozen contract's pinned value. The finding is pinned.
* **DISCLOSED SHORTFALLS against the catalog's series estimates** (the families land BELOW
  their estimates — never above): F12's `ensemble_sweep_cycle_seconds` is **OUT** (no sweep
  service publishes an observed cycle DURATION; publishing the CONFIGURED interval under an
  "observed duration" name would be a fabrication), and the seven RDRS lanes + three
  standalone sweeps that expose no `counters()` reader render **no series** (the vocabulary
  stays the closed 13-lane set). F4's manager-pool `max` / `available` render no series (the
  SQLAlchemy pool exposes neither natively). The cumulative count is therefore under the
  pinned budget.
* **The C17 docstring fix (task 4.9):** **DEFERRED and RECORDED** (council W5) — no
  mechanical-list file is touched by this lane; the fold ledger's `DEF-1` row carries the
  deferral and its rationale, and the F12 accumulation is correct regardless of the
  docstring (the metrics layer's own accumulation is the disambiguator).

---

### The re-derived node counts + the P5 evidence (P5-G1, 2026-11-02)

The figures below are the executor's derived numbers — NOT the plan's projections. They are the
registration input for the pack rows the tester lane owns.

**The D1 set (lane-owned, HEAD-only).** The FE has no `--collect-only` equivalent, so the node
counts are the per-file `Tests: N` summaries of the registered pack's own jest leg.

| Pack id | Leg | D1 / D2 | Re-derived nodes | Per-file breakdown |
|---|---|---|---|---|
| `metrics-fe-models` | P5-G1 | D1 | **18** | `frontend/src/app/models/metrics.model.spec.ts` |
| `metrics-fe-service` | P5-G1 | D1 | **9** | `frontend/src/app/services/metrics.service.spec.ts` |
| `metrics-fe-svg-chart` | P5-G1 | D1 | **29** | `frontend/src/app/components/charts/svg-line-chart/svg-line-chart.component.spec.ts` |
| `metrics-fe-page` | P5-G1 | D1 | **46** | `metrics.component.spec.ts` 14 · `metrics-gear-menu.spec.ts` 9 · `overview-tab.component.spec.ts` 11 · `charts-tab.component.spec.ts` 7 · `families-tab.component.spec.ts` 5 |
| `metrics-fe-bindings-pins` | P5-G1 | D1 | **17** | `frontend/src/app/pages/metrics/metrics.bindings.pins.spec.ts` |
| **the D1 total** | P5-G1 | D1 | **119** | 9 spec files (the plan's projection was ~35 — the derivation is the truth) |
| `metrics_fe_dual_side` | P5-G1 | D2 | **138** (HEAD) / **137** (BASE) | the 5 declared files: `app.routes.spec.ts` · `app.component.spec.ts` · `resources-gear-menu.spec.ts` · `mcps-gateway.pins.spec.ts` · `maintenance.bindings.pins.spec.ts` |
| `metrics_fe_dual_side` (the WIDENED derived leg) | P5-G1 | D2 | **1801** (HEAD) / **1791** (BASE) | the **38** derived files (37 at the base) — the specs that import the MODELS BARREL (the barrel gained `export * from './metrics.model'` — a name collision would break any of them) plus the specs that READ the route table / the gear menu |

**The D2 composition (derived, not declared).** The plan's declared composition is "the
`resources-gear-menu` ordering pin + the FE pack affected by the route table" (5 files). The
DERIVATION reaches **38** dual-side files: every spec that imports `frontend/src/app/models`
(the barrel) plus every spec that reads `app.routes.ts` / `app.ts` / `app.html`. The widened set
ran as a DISCLOSED supplementary dual-side leg (the recipe's rule from P2/P3/P4): **33 further
files / 1663 nodes**, both sides, FAILED digests byte-identical ⇒ net-new EMPTY. The registered
composition should be the DERIVED set.

**The gate results (Tier 1 — `metrics_fe_dual_side`, the D1 + D2 legs).**

| Leg | HEAD (D1 + D2) | BASE (D2) | FAILED digest (BARE-ID / GREP-FULL) | Verdict |
|---|---|---|---|---|
| the D1 leg (HEAD-only) | 119 passed · 0 failed · **exit 0** | SKIPPED-BY-CONSTRUCTION (the files do not exist at the base) | — | the lane's own corpus is green |
| the D2 DECLARED leg | 138 passed · 0 failed · **exit 0** | 137 passed · 0 failed · **exit 0** | (empty both sides) | net-new **EMPTY** |
| the D2 DERIVED leg (the widened supplementary) | 1799 passed · 2 failed · **exit 1** | 1789 passed · 2 failed · **exit 1** | `95c48e0111632e3` / `92b3ecbf67931b30` — **IDENTICAL both sides** | net-new **EMPTY** |
| the scoped type-check leg (`tsconfig.metrics-fe.json`) | **exit 0** | SKIPPED-BY-CONSTRUCTION | — | the P5-M9 surface |
| the AOT build leg (`ng build --configuration development`) | **exit 0** | SKIPPED-BY-CONSTRUCTION | — | 0 errors |
| the budgets build leg (`ng build --configuration production`) | **exit 0** | SKIPPED-BY-CONSTRUCTION | — | 0 errors; the budget WARNINGS are RECORDED FINDINGS (below) |
| the `package.json` dependency diff | **0 lines** | 0 lines | — | the D2 zero-dependency guard holds |

**The TWO failures on the derived leg are the KNOWN base red** — `settings.component.spec.ts`'s
two timezone-picker expectations assert the New York **DST** offset (`UTC-04:00`) and the host
date (2026-11-02) is **standard** time (`UTC-05:00`). The nodes are byte-identical on both legs
⇒ a pre-existing, DATE-DEPENDENT rot, not a branch regression. The digest is recorded in BOTH
forms (BARE-ID `95c48e0111632e3…`, GREP-FULL `92b3ecbf67931b30…`); the jest output embeds a
per-file wall-clock (`(5.54 s)`), so the digest strips the timing suffix — the raw digest would
false-mismatch on the timing alone.

**The mutation table — P5-M1..P5-M11, planted independently, 11/11 RED→GREEN.** The harness is
`test/packs/metrics_fe_mutation_matrix_test.py` (it refuses to run on a dirty tree; each mutation
is restored with `git checkout --` and the restore is asserted BYTE-IDENTICAL).

| Label | Mutation | RED node |
|---|---|---|
| `P5-M1` | the named `POLL_INTERVAL_MS` renamed | `metrics-fe-bindings-pins` (+ the overview spec, which reads the constant) |
| `P5-M2` | the `clearInterval` teardown removed | `metrics-fe-bindings-pins` (+ the overview spec's runtime teardown assertion) |
| `P5-M3` | the supersede token check inverted (the older response wins) | `metrics-fe-page` (the overview spec's supersede pins) |
| `P5-M4` | the empty-state condition switched from "no family live" to "no families at all" | `metrics-fe-page` (the OFF-state spec) |
| `P5-M5` | the model mirror's field name drifted from the wire | `metrics-fe-models` (the contract-doc-parsed field-set pin) |
| `P5-M6` | the SVG scale function's normalisation removed | `metrics-fe-svg-chart` (the y-scale pins) |
| `P5-M7` | the gear-menu ordering pin's expected position changed | the D2 ordering pin (`resources-gear-menu.spec.ts`) |
| `P5-M8` | `JSON_HEADERS` dropped from the service call | `metrics-fe-service` |
| `P5-M9` | the availability union narrowed to a closed literal set | the TYPE-CHECK leg — `metrics.model.spec.ts(141,11) TS2322` (jest does not type-check, so the union is invisible to the jest corpus; that is why the type leg exists) |
| `P5-M10` | the panel copy reverted to "metrics disabled" | `metrics-fe-page` (the copy pin) |
| `P5-M11` | the production-config build leg dropped from the gate | `metrics-fe-bindings-pins` (the build-leg pin) |

**The FULL FE corpus (the whole-tree reference for an FE-only lane).** `jest --ci --silent
--runInBand` over `frontend/` (every `**/*.spec.ts`): HEAD **4279 passed · 9 failed · 4288
total** (134 suites, 110 s) against BASE **9 failed** on the same four files — the 9 failing node
ids are BYTE-IDENTICAL on both sides (digest `05d054bb483a90b0c769e8f270d6ba2e15ecc375bc61c22d8c8c4d73830c1b7d`)
⇒ net-new **EMPTY**. The nine are the pre-existing family: the timezone picker's date-dependent
New York DST expectation (settings ×2 + schedule-create-dialog ×3) and the two documented
`jobs-grouping` (×3) / `jobs-filter-state` (×1) live reds.

**The e2e.** `frontend/e2e/metrics-gate.spec.ts` under `frontend/playwright.metrics-gate.config.ts`
(own proxy config + gate pattern; the door routes are stubbed pre-network; zero daemon):
**2/2 PASS** (31.3 s) — the journey (gear menu → `/metrics` → Overview stat cards → Charts →
Families, a dark family with its reason) and the deep-link. The load-bearing assertion is the
**STEP-RENDERING** one: the counter series' path carries `data-render-mode="step"` and its `d`
contains the horizontal-then-vertical commands (`H`/`V`) and NO diagonal segment, while the gauge
series' path carries `data-render-mode="line"` with `L` and neither `H` nor `V`.

**The budget WARNINGS (the production leg) — RECORDED FINDINGS, never auto-fixed.** Every one of
them is PRE-EXISTING and in ANOTHER component's SCSS: the initial bundle (5.91 MB vs the 1 MB
warn budget) · `agent-selector.scss` 8.77 kB · `add-source-modal.scss` 8.32 kB ·
`blueprint.component.scss` 18.32 kB · `checkpoint-cleanup.component.scss` 8.70 kB ·
`snapshot-detail-drawer.component.scss` 8.70 kB · `snapshots.component.scss` 12.29 kB ·
`instance-list.scss` 10.17 kB · `jobs.component.scss` 14.77 kB · `chat-interface.scss` 16.15 kB.
**NONE of the lane's own components is over the 8 kB warn budget.** The build exits 0 (the budgets
are WARN-level except the initial total's 6 MB error ceiling, which is not breached).

**The FE-manifest delta (the Phase-6 stage-prep checklist).** The staged `frontend` tree is the
BUILT bundle (`frontend/dist/frontend/browser`), not the source tree, and the chunk names are
content-hashed — so ANY source change renames the whole chunk set. The STABLE statements:
* the `frontend_manifest` entry count goes **93 → 95** (+2 `chunk-*.js` entries: the metrics
  page's lazy chunk — 25,938 B in the local production build, carrying the
  `metrics-empty-panel` marker — plus one shared chunk split by the new import graph);
* `frontend_tree_sha256` **CHANGES** (recomputed at stage time — it cannot be predicted pre-stage);
* `fe_index_sha256` **CHANGES** (`index.html` references the new `main-*.js` hash);
* the per-file map therefore shows a full chunk-hash cascade (83 added / 81 removed / 1 changed
  against the v0.28.5 staged manifest) — the cascade is the hash naming, NOT a payload change.
* the `agents/` and `plugins/` trees are UNCHANGED by this phase.

**The deviations (recorded, never silent).**
1. **P5-M7's ordering pin lives in the D2 file.** The plan points P5-M7 at "the ordering pin
   (D2 leg)"; the Metrics entry's position is asserted in the D2 `resources-gear-menu.spec.ts`
   (the house's gear-menu ordering pin) AND in the lane's own `metrics-gear-menu.spec.ts`. The
   D2 file gained ONE describe block (the shared ordering surface every gear entry depends on);
   the base leg runs the pre-existing version of that file, so the two-class asymmetry is
   unchanged.
2. **The D1 set is 119 nodes, not the plan's ~35 projection.** The projection was made before the
   specs existed; the derived number is the truth (the plan's own rule: projections only).
3. **The type-check leg is SCOPED** (`tsconfig.metrics-fe.json`) rather than the whole
   `tsconfig.spec.json`, because the latter carries a PRE-EXISTING syntax error at
   `src/app/app.component.spec.ts:2127` (a stray `});`; jest tolerates it — ts-jest transpiles
   best-effort — but `tsc` does not). That defect is NOT this lane's file and is left untouched;
   it is reported to the FE owner.
4. **The route placement.** The plan says "placed BEFORE any `:id` routes"; the `/metrics` route
   is declared before the `skills/:id` family (the static-vs-dynamic family that could shadow a
   static path — the `/skills/bank` + `/skills/triggers` + `/persona` precedent). The two
   earlier `:id` routes (`instances/:instanceId`, `projects/:projectId/…`) are in a different
   first-segment namespace and cannot shadow a top-level static path.
5. **The chart mode source.** The per-series mode comes from the catalog's per-name
   `chart_role` (the roster's truth, contract §4.3) with the door's own kind-derived fallback
   (a counter renders STEP) when the catalog names no mode — the FE never invents a mode the
   door did not publish.
