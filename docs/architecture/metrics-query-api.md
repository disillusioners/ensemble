# Metrics Query API — the central-metrics-v1 door

> **Status:** published by the `central-metrics-v1` lane (Phase 6, task 6.4).
> **Date:** 2026-11-02 (UTC).
> **Lane:** `feature/central-metrics-v1` (worktree `/home/nea/ensemble-src-wt-central-metrics-v1`).
> **Contract:** [`docs/architecture/metrics-query-contract.md`](metrics-query-contract.md) is the
> **frozen contract** this page describes; the plan set at
> `.agents/shared/planning/central-metrics-v1/` is its source, and
> `.agents/shared/planning/metrics-collection/metric-catalog.md` is the **single source of truth
> for names, vocabularies and counts**.
> **Citation rule (AM-24):** evidence citations point at the **vendored** copies in
> `.agents/shared/planning/central-metrics-v1/inventory-vendor/` — never at the untracked
> main-checkout path alone (see §7).

This page is the **operator- and consumer-facing** description of the metrics query door. It
describes what the door serves, how it behaves, how it is switched on and off, what it
deliberately does **not** expose, and the scope it runs under. Every number quoted here is
either a **shipped constant** (cited to the code) or a **pointer** into the catalog's §14
ledger (never a restated copy).

---

## 1. The three routes

The door is **one door**: the three routes live under the existing `/api/metrics` mount
(`daemon/routers/metrics.py` — the same router that serves the Prometheus **text** exposition).
They add **no new mount** and **no new scope**.

| Route | Serves | Notes |
|---|---|---|
| `GET /api/metrics/families` | the **family catalog as served** — one entry per registered family | derived from the registry (the registered objects + their registration-time metadata); there is **no second family table** |
| `GET /api/metrics/snapshot` | the **current published values** of the matching series | filters: `?family=`, `?name_prefix=` |
| `GET /api/metrics/series` | the **windowed trend** of the matching series | filters: `?series=`, `?names=`, `?window_s=`, `?max_points=`, `?cursor=` |

**Scope:** all three require `maintenance:read` and share the same **fail-closed** auth seam:
when auth is not configured the door returns **503 `AUTH_NOT_CONFIGURED`** *before any registry
read* — it never serves an unauthenticated body. See §5 for the scope over-grant disclosure.

**Filters.** `?series=<name>{<k>=<v>,…}` is the **label-aware** selector (RFC-3986-escaped) — a
series is the pair `(name, labels)` and the labels are the complete discriminator. `?names=` is
**name-level** and returns every label variant of the named metrics; it is **rejected**, never
silently truncated, when it exceeds the **50-name ceiling** (→ `400 invalid_label_filter`).

---

## 2. The envelope

Every route returns the same header block. The **envelope-shape axis** and the
**label-vocabulary axis** are separate: `schema_version` is the envelope's shape version; an
additive change (a new optional field, a new filter, a new enum value) does **not** bump it,
and consumers must ignore unknown members.

```
schema_version: 1                     # QUERY_SCHEMA_VERSION — the envelope-shape axis
vocabulary_version: 2                 # mirrors the registry's VOCABULARY_VERSION (2 after this lane)
generated_at_utc: "<ISO-8601 aware UTC>"   # the house producer (timestamps.now_utc_iso()) — NOT naive UTC
restart_epoch: "<CURRENT_BOOT_ID>"    # bash_spawn_reaper.CURRENT_BOOT_ID (uuid4().hex)
activation: { listener_enabled: bool, # a PROCESS FACT — the exposition socket is bound
              census_enabled: bool,   # = not census.gated (the honest switch-derived bit)
              query_api: "auth-gated-always-on" }   # literal — the door's own posture
```

**`restart_epoch` semantics.** `restart_epoch` is the current **boot id**. Its purpose is to make
a restart **visible**: the trend buffer is in-RAM only (§4), so its history is lost on restart by
design — a consumer that carries a window across a restart must compare `restart_epoch` and
**discard the carried window** rather than read a silent gap. `restart_epoch` is **not** a
database-clock value: `boot_epoch.get_boot_epoch()` is a DB-clock domain and is `None`-able, so
it is deliberately not used here.

**`activation` is a posture block, not a status block.** `listener_enabled` is a **process
fact** (is the `:9798` exposition socket bound?) and is therefore **switch-invariant** — it does
not change when the kill-switch flips. `census_enabled` is the honest switch-derived bit. The
`query_api` literal states the door's posture: **auth-gated and always on**.

### 2.1 The family shape (`/families`)

```
{id, names:[{name, kind, labels:{key:[values]}|null, help, buckets:[…]|null,
             cardinality_class, monotonic, chart_role}],
 live_series_count, availability:{state:"live"|"dark", reason?},
 role, source_idiom, durability, cardinality_contribution}
```

- `kind ∈ {gauge, counter, histogram}` — **there is no `summary`**; the registry never produces
  one.
- `buckets` is **on the wire** (the bucket boundaries are discoverable off-box); it is `null`
  for a non-histogram kind.
- `cardinality_class ∈ {closed, open, none}` — a metric with a frozen value set for **every**
  label key is `closed`; a metric that declares label keys **without** a frozen value set is
  `open` and renders `labels: null` (it is never value-enumerated); a metric with no label keys
  is `none`.
- `role ∈ {chart, stat}` — the family-level classification (`chart` = chart-primary, the series
  are required for the chart; `stat` = stat-primary, the current value only). It is carried from
  the registration-time metadata, so a newly wired family is chart-primary **without any door
  edit**.
- `source_idiom ∈ {registry, postgres_census, wedge_capture}`; `durability ∈ {process, durable}`
  (default `process` — in-RAM, reset on restart).
- `cardinality_contribution = live_series_count × cardinality_weight` (the default weight is 1).
- `chart_role ∈ {line, step}` is an **additive per-name member** — it carries the chart mode for
  that series (counters render STEP, gauges render LINEAR).

### 2.2 The snapshot shape (`/snapshot`)

```
{…envelope header…, series:[{name, labels, kind, monotonic, value, last_publish_age_s}]}
```

Staleness is **explicit metadata**, never a silently stale value: `last_publish_age_s` is the
seconds since the metric's last publish. Histogram series carry `value: null` plus
`histogram: {count, sum, buckets}` **as stored**.

### 2.3 The series shape (`/series`)

```
{…envelope header…,
 window:{from_utc, to_utc, interval_s, interval_s_source:"observed"},
 last_sample_age_s, stale,
 series:[{name, labels, kind, monotonic, points:[[t, v],…]}]}
```

- `interval_s` is the **observed** effective spacing `max(sampler, publisher)` — **15 s** at the
  defaults — and `interval_s_source` says so literally. The **configured** cadence is never
  reported as the observed spacing.
- `stale: true` when `last_sample_age_s > 3 ×` the effective interval — a wedged sampler
  surfaces as **staleness**, never a silently frozen line.
- `window_s` anchors at the server's `generated_at_utc`; a cursor pins its evaluation instant.

---

## 3. Errors, pagination and the cursor

### 3.1 The error vocabulary

An expected condition is **never** a 500.

| Status | Code | When |
|---|---|---|
| 404 | `unknown_family` | the requested family is not registered |
| 400 | `invalid_label_filter` | a malformed label-aware selector, or a `?names=` request over the 50-name ceiling (**rejected, never silently truncated**) |
| 400 | `invalid_cursor` | the cursor is invalid or expired (older than the buffer's oldest point) |
| 401 | — | auth is ON and the caller has no session (the auth middleware) |
| 403 | `SCOPE_INSUFFICIENT` | the caller lacks `maintenance:read` — the body is `{"error": "SCOPE_INSUFFICIENT", "message": "Token lacks required scope"}` (`daemon/auth/decorators.py:75-78`). **Never a 404** — the caller is authenticated, just not permitted |
| 503 | `AUTH_NOT_CONFIGURED` | auth is **not configured** — fail-closed, **before any registry read** |
| 503 | `registry_unavailable` | the query provider is not wired |

> **Rendering note (resolved — D-c).** The frozen contract's error-vocabulary block used to
> render the scope miss lowercase as `scope_insufficient`; the **shipped** body code is
> uppercase `SCOPE_INSUFFICIENT` (`daemon/auth/decorators.py:75-78`). That was a **token-
> rendering** difference in the contract's prose, **not** a behavioural one — the status (403)
> and the meaning are identical, and the shipped spelling is the one a consumer sees on the
> wire. **The contract now carries the shipped spelling** (the D-c ruling; the wire and the
> decorator are untouched). The shipped pack `central_metrics_contract_doc` pins that block
> against a literal in the test file that still carried the superseded lowercase token, so it
> goes red on exactly that one assertion until the pin follows the ruling — the pin lives in
> the test surface (outside this lane's fence) and is **routed**, not silently ignored. The
> measured run is preserved in `evidence/d-c-contract-token-pin.txt`.

### 3.2 The bounds

| Bound | Value | Behaviour when exceeded |
|---|---|---|
| families per response | `MAX_FAMILIES = 500` | bounded + a cursor |
| names per request (`?names=`) | `MAX_NAMES = 50` | **rejected** — `400 invalid_label_filter` |
| points per series (`?max_points=`) | `DEFAULT_MAX_POINTS = 240`, `MAX_POINTS_CEILING = 240` | **clamped down** to the ceiling — a caller may not widen the daemon's own budget |
| response body | `RESPONSE_BYTE_CEILING = 1 048 576` (1 MiB) | **bounded + a cursor** — never a silent truncation |

The residual on-loop encode is bounded by construction: `240 points × ≤ 50 names ⇒ ≤ 12 000
points ≈ 3.6 ms`.

### 3.3 The cursor

`next_cursor` is a **string or null**. It is **opaque** to consumers and pins **both** the
evaluation instant and the position. An invalid or expired cursor (older than the buffer's
oldest point) returns **`400 invalid_cursor`**. When the byte ceiling is reached the response is
**bounded and carries a cursor** — it is never silently truncated.

---

## 4. Activation posture and the runbook

### 4.1 The three activation surfaces — named together

The metrics program has **three activation surfaces**. They are named together here because
their postures are independent, and an operator who reads one of them in isolation will
mis-guess the others:

1. **The query API** — `families` / `snapshot` / `series`. **Auth-gated and always serving.**
   It does **not** consult the kill-switch; it serves live registry data in both switch states.
2. **The trend buffer** — the in-RAM ring behind `/series`. **It accumulates in BOTH switch
   states**; the kill-switch does not stop the registry from accumulating, so the buffer keeps
   filling whichever way the switch is set.
3. **The FE charts polling** — the browser polls `/snapshot` (10 s) and `/series` (15 s). When
   the door is unreachable or the session has expired, the tab **degrades to its cached / last-good
   view, marked stale** — it never polls harder and never blanks.

### 4.2 The per-surface matrix

| # | Surface | Switch-gated? | Auth-gated? | Off-state semantics |
|---|---|---|---|---|
| 1 | `:9798` exposition **listener body** | YES — default-OFF **at the body level** | NO — unauthenticated, loopback-only | HTTP **200 with a valid empty body**; the socket **is still bound** |
| 2 | `/api/metrics` **text exposition** | NO — never consults the switch | YES — `maintenance:read` | served in both switch states |
| 3 | the **three query routes** | NO — never switch-gated | YES — `maintenance:read` + the same fail-closed 503 seam | **availability, not emptiness** — live registry data in both switch states |
| 4 | **census DB work** | YES — the census sampler does no DB work while OFF | n/a (internal thread) | the sampler keeps ticking; its DB leg is skipped; the F6 families go dark with `availability.reason="census_gated"` |
| 5 | **registry accumulation** | NO — ungated, always on | n/a (in-process) | writers flip dirty flags and increment regardless of the switch |
| 6 | **FE charts polling** | NO — polls the query door only, never the listener | YES — the caller's session scope | degrades to the cached/stale view, never blanking |

**The switch never touches an authenticated surface.** Its emergency-off semantics are
**unchanged and narrower than they appear**: flipping it OFF blanks only the unauthenticated
listener body and stops the census sampler's DB work.

### 4.3 Enabling the exposition listener for Prometheus

The `:9798` listener is **default-OFF** (`limits.metrics_enabled=False`, unchanged by this lane).
Enabling it is a **live managed-key flip** — `limits.metrics_enabled = true` — with a **≤ 5 s
TTL and no restart**: the gate is read per request, not cached.

**The default-OFF is BODY-LEVEL.** With the switch OFF the socket **still binds** and still
serves a **valid empty 200** — no `ensemble_metrics_up`, no family series. This is deliberate:
the socket's existence is a process fact, and the switch decides only what the **body** carries.
An operator must therefore read "the listener answers 200" as **"the listener is up"**, not as
"metrics are being served" — the body is the discriminator.

### 4.4 The door's emergency-off lever

**The query door has no kill-switch of its own.** Its emergency-off lever is **token revocation**
of the `maintenance:read` grant — revoke the session/token that carries the scope (the auth
service's grant/session revocation path). The `census == 2` invariant is preserved because no
code path in the door consults the switch.

### 4.5 The managed keys

The **seven** metrics managed keys keep their **defaults, bounds, types and restart columns**.
Exactly **one** of them — `limits.metrics_sampler_interval_s` — receives a **description-only**
amendment, because this lane consumes it as the trend sampler's tick:

| Key | Default | Bounds | Restart | Amendment |
|---|---|---|---|---|
| `limits.metrics_enabled` | `False` | — | read per request, no restart | none |
| `limits.metrics_listener_port` | `9798` | 1024–65535 | boot-applied | none |
| `limits.metrics_scrape_interval_hint_s` | `15` | 5–300 | read per request | none |
| `limits.metrics_census_interval_s` | `15` | 15–300 | boot-applied | none |
| `limits.metrics_sampler_interval_s` | `10` | 1–60 | boot-applied | **description-only** — the description now names **both** consumers (the host-side samplers **and** the in-RAM trend-history sampler) |
| `limits.metrics_textfile_dir` | `$INSTALL_DIR/data/metrics` | — | boot-applied | none |
| `limits.metrics_cardinality_cap` | `2000` | 100–10000 | read per observation | none |

The reported `window.interval_s` is the **observed** effective spacing `max(sampler, publisher)`
— **15 s** at the defaults — never the configured cadence.

### 4.6 The trend buffer is in-RAM only

The trend storage is an **array-backed ring buffer** in memory — **16 B/point**, with a
`POINT_BUDGET = 750 000` ⇒ **11.4 MiB** worst case. It is **lost on restart by design**; a
restart is visible through `restart_epoch`, never a silent gap. The Prometheus TSDB owns long
history — the ring is a 2-hour working window, not a store.

---

## 5. Scope disclosure — the over-grant (AM-20)

**`maintenance:read` also confers checkpoint-cleanup-EXECUTE capability**
(`daemon/routers/maintenance.py`). The query door is therefore **not** a narrower privilege
than the maintenance scope it borrows: a caller who can read the metrics can also trigger a
checkpoint cleanup.

This is **not** an accident and it is **not** being fixed in v1. The scope split
(`metrics:read`) is a **NAMED COMMITMENT** (leader ruling LR-1) — see
`.agents/shared/planning/central-metrics-v1/decisions.md` §D6 for the retrofit map:

1. **1 taxonomy line** — add `metrics:read` to the scope taxonomy (`daemon/auth/scopes.py`).
2. **1 manifest line** — the family's scope becomes `"/api/metrics": "maintenance:read|metrics:read"`.
3. **4 decorator lines** — the three new query routes declare `metrics:read`; the shipped text
   route keeps `maintenance:read`.
4. **Cage note** — the scope cage is a **subset** check, so the three routes stay inside the
   pinned set, and `SESSION_OPERATOR_SCOPES` auto-grants, so **no operator re-grant is needed**.

**The trigger for the retrofit (the recorded condition):** a non-operator machine lane reads the
door in this lane's lifetime (a Grafana token, or the deferred agent-tool door) ⇒ mint
`metrics:read` at that point. **Until then** v1 stays on `maintenance:read` and the query-API
docs disclose the over-grant — this section.

**This section is binding guidance for the deferred lanes** (the deferred agent-tool door and
the deferred Grafana layer). It is not a "revisit later".

---

## 6. The OUT-list — what the door deliberately does not serve

Every excluded metric is listed with its **named greenfield seam** — the specific place a future
lane would have to instrument or read to admit it. The machine-readable form of this table is
`daemon/services/metrics_query/wiring/adjudication.py` (`CATALOG_FAMILY_VERDICTS` and
`OUT_METRIC_NAMES`), and the **absence** of every OUT metric is pinned by the shipped gate pack.

### 6.1 Families OUT of v1

| Family / metric | Why OUT | The named greenfield seam |
|---|---|---|
| **F2** — `ensemble_thread_cpu_seconds`, `ensemble_thread_count` | **by VALUE ruling, not by rule** (AM-25 / LR-2) — prod is Darwin-primary, so the darkness is certain | **Flip condition (verbatim):** *"if the ops surface becomes Linux-primary or the operator confirms near-term per-thread CPU need, re-admit (+22, one pack)"* |
| **F7** — the write-side hook | the write path is billed to the READ budget if it reuses the read seam | the **distinct** `_charge_checkpoint_write` hook (its own function, own budget/labels), called from `aput`/`aput_writes` — **a separate commission** |
| **F8** — `ensemble_checkpoint_versions_per_thread` | greenfield | a per-thread version counter (the threads are **not named** today, so the label has no closed vocabulary) |
| **F8** — `ensemble_checkpoint_prune_runs_total{outcome}` | no outcome counter exists | a prune-outcome counter at the prune seam (the current prune code returns no outcome) |
| **F11** — `ensemble_job_claim_latency_seconds` | greenfield | an instrument at the **enqueue→claim** interval (the interval must be *observed* once and carried, never re-derived) |
| **F12** — `ensemble_sweep_cycle_seconds{lane}` | **no sweep service publishes an observed cycle DURATION** — publishing the *configured* interval under an observed name would be a fabrication | a timing call at a sweep site — i.e. **new instrumentation**, not a re-read |
| **F13** — `ensemble_wake_turns_total`, `ensemble_instance_lifecycle_total` | new increments | the wake-turn outcome seam and the instance-lifecycle event publisher |
| **F18** — `ensemble_mcps_gateway_session_total{outcome}` | refusals/expiries are **protocol errors**, not aggregated counters | a session-outcome counter at the gateway session seam |
| **F21** — the op/phase families (`ensemble_journal_op_seconds`, `ensemble_journal_ops_total`, `ensemble_upgrade_phase_seconds`, `ensemble_upgrade_phase_total`, `ensemble_upgrade_journal_events_total`) | wrapping the journal twin's op functions is **instrumentation**, and the 26-value op vocabulary is a pure cost | a structured phase field in the journal history — the phase trail lives in the journal's **extension** fields, so there is no pure-read derivation today |
| **F19 / F20 / F22** | the commission's OUT — compaction, turn/tool-call, and the out-of-process exporter writer | a pull-able snapshot for F19; an increment at the message tap for F20; an out-of-process textfile writer for F22 |

### 6.2 Tier-3 and uncatalogued OUTs

- **Tier-3 LLM families:** F14 (LLM tokens), F15 (LLM request outcome/latency), F16 (LLM stream
  stalls) — each needs a **new observe** at a request/usage site.
- **Sources circuit-breakers / token buckets** — no family; the breakers and the buckets are
  per-source state, not a metric family.
- **Badge / request latency** — no family.
- **Mission mint-failure outcomes** (log-only), **DLQ replay outcomes**, **loop-breaker state**,
  **snapshot-metrics re-surfacing**, **skill-metrics lane**, **checkout-latency family** (the
  F4 superset ships it).

---

## 7. Evidence citations (AM-24)

The evidence this page rests on — the metrics-inventory pull-seam map — lives **untracked** in
the shared main checkout, so any committed document that cites it by path dangles in every other
checkout. The evidence is therefore **vendored** into the lane directory with a provenance
header, and **committed documents cite the vendored copies**:

- `.agents/shared/planning/central-metrics-v1/inventory-vendor/metrics-inventory-backend-producers.md`
- `.agents/shared/planning/central-metrics-v1/inventory-vendor/metrics-inventory-surfaces-fe-deps.md`
- `.agents/shared/planning/central-metrics-v1/inventory-vendor/README.md`
- `.agents/shared/planning/central-metrics-v1/inventory-vendor/PROVENANCE.md` — the source path,
  the per-file sha256 table, the byte-identity verification recipe, and the source-side
  integrity record.

The vendored bodies are **byte-identical** to their sources; the only difference is the
8-line provenance header that sits above each body.

---

## 8. The non-Prometheus deviations clause (AM-8)

The envelope borrows Prometheus **vocabulary** but its semantics are **local**, and are declared
as such:

- **Buckets are AS STORED** — the histogram's own bucket boundaries, not a Prometheus-rendered
  cumulative series.
- **Monotonicity holds WITHIN ONE `restart_epoch` only** — a counter is non-decreasing inside a
  boot; across a restart the process state is gone (and `restart_epoch` says so).
- **Durations are in SECONDS.**
- **No exposition-format leakage into the JSON envelope** — the JSON door never renders
  Prometheus text.

### 8.1 The Prometheus-text coexistence statement

**The query door is NOT Prometheus-compatible, by design.** It is a JSON door with its own
envelope; it does not implement the Prometheus exposition format and does not aim to. The
**Prometheus text exposition** lives on the **other two surfaces** and continues to coexist with
it without overlap:

| Surface | Format | Consumer |
|---|---|---|
| `GET /api/metrics` (the shipped text route) | Prometheus exposition **text** | an in-process consumer / a manual scrape |
| `127.0.0.1:9798/metrics` (the listener) | Prometheus exposition **text** | a Prometheus server scraping loopback |
| `GET /api/metrics/{families,snapshot,series}` (the door) | **JSON** | the FE charts page, and the deferred agent-tool / Grafana lanes |

The two surfaces are not mirrors of one another: the text exposition is **default-OFF at the body
level** (§4.3) and unauthenticated-loopback-only, while the JSON door is **auth-gated and always
serving** (§4.1). A consumer that wants Prometheus semantics scrapes the text surface; a consumer
that wants the family catalog, the labels, the bucket boundaries or the trend window uses the
door.
