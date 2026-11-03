# Research — Metrics Surface for the Agent-Tool Lane (item #2)

**Commission:** `metrics-agent-tools` (item #2 of the ratified metrics sequence; item #1 = central-metrics-v1, shipped v0.28.6).
**Research date:** 2026-11-03 (UTC)
**Worktree (read-only basis):** `/home/nea/ensemble-src-wt-metrics-tools` (branch `feature/metrics-agent-tools`, base `origin/latest @ 03a2297c3180e79c6b5c26a92b2caca7c0452725`).
**Method:** direct file reads of the frozen contract, the provider seam, the auth decorators, the central-metrics-v1 decision record, and the vendored metrics-unification inventories. Every claim below carries `file:line`.

**Contract posture:** the query contract is **FROZEN** (`docs/architecture/metrics-query-contract.md:10-12`). The tool layer **adapts to it, never changes it**. The only named, in-scope change to the frozen surface is the `metrics:read` scope split (§D6 below) — and even that is a *named commitment with a 4-line retrofit map*, not a licence to alter the envelope.

---

## 0. Executive map — what a tool implementer calls

| Concern | Call |
|---|---|
| Read substrate | `daemon.services.metrics_query.protocol.MetricsQueryProvider` (Protocol, `runtime_checkable`) — `daemon/services/metrics_query/protocol.py:22-52` |
| Concrete provider | `daemon.services.metrics_query.provider.RegistryMetricsQueryProvider` — `daemon/services/metrics_query/provider.py:58` |
| Where the live instance lives | `app.state.metrics_query` — set at `daemon/api.py:2533`; the router reads it at `daemon/routers/metrics.py:226` |
| Family catalog | `provider.families() -> list[dict[str, Any]]` — `provider.py:211` |
| Current values | `provider.snapshot(*, family: str|None=None, name_prefix: str|None=None) -> list[dict[str, Any]]` — `provider.py:281-286` |
| Trend | `provider.history(*, names=None, series=None, window_s=None, max_points=None, now=None, max_series=None) -> list[dict[str, Any]]` — `provider.py:318-327` |
| Envelope (REST parity) | `envelope.build_families_envelope(provider, *, limit=MAX_FAMILIES, offset=0)` · `build_snapshot_envelope(provider, *, family, name_prefix, offset=0, limit=MAX_SNAPSHOT_SERIES)` · `build_series_envelope(provider, *, names, series, window_s, max_points, now, offset=0, max_series=MAX_SERIES, max_total_points=MAX_TOTAL_POINTS)` — `daemon/services/metrics_query/envelope.py:85, :109, :154` |
| Label-aware selector | `selectors.parse_series_selector(text) -> tuple[str, dict[str,str]]` (raises `InvalidLabelFilter`) · `selectors.selector_matches(metric_name, labels, selector) -> bool` — `daemon/services/metrics_query/selectors.py:32, :64` |
| Availability | `availability.derive_availability(*, source_idiom, live_series_count, facts) -> dict` · `AVAILABILITY_REASONS` — `daemon/services/metrics_query/availability.py:34, :65` |
| Freshness | `provider.last_sample_age_s() -> float` · `provider.last_sample_timestamp() -> int|None` · `provider.oldest_sample_timestamp() -> int|None` · `provider.effective_interval_s -> float` — `provider.py:397, :406, :412, :125` |
| Public re-exports | `daemon/services/metrics_query/__init__.py:83-127` (importing the package is **side-effect-free**: no thread, no connection, no registration — `__init__.py:16-19`) |

---

## 1. THE FROZEN CONTRACT

Source: `docs/architecture/metrics-query-contract.md` (818 lines).

### 1.1 The three endpoints — exact semantics

```
GET /api/metrics/families
GET /api/metrics/snapshot
GET /api/metrics/series
```
(`metrics-query-contract.md:225-229` — the fenced `query-routes` block, machine-pinned by pack `central_metrics_contract_doc`, `:423-433`.)

**`/families`** — the family catalog as served. Per-family shape (`:128-130`):

```
{id, names:[{name, kind, labels:{key:[values]}|null, help, buckets:[…]|null,
 cardinality_class, monotonic}], live_series_count,
 availability:{state:"live"|"dark", reason?}, role, source_idiom,
 durability, cardinality_contribution}
```

- `role` ∈ `{chart, stat}` — chart-primary vs stat-primary, derived from registration metadata (`:142-146`); the constants are `cm.FAMILY_ROLE_CHART = "chart"` / `cm.FAMILY_ROLE_STAT = "stat"` (`daemon/checkpoint_metrics.py:240-241`).
- `source_idiom` ∈ `{registry, postgres_census, wedge_capture}` (default `registry`); `durability` ∈ `{process, durable}` (default `process` — in-RAM, reset on restart) — `:157-159`; the frozen sets are `checkpoint_metrics.SOURCE_IDIOMS` (`:225-227`) and `DURABILITIES` (`:232`).
- `live_series_count` = number of live label combinations observed; `cardinality_contribution = live_series_count × cardinality_weight` (weight default 1) — `:154-156`.
- **`buckets` is ON THE WIRE** (`:132-135`): the provider derives bucket boundaries from the registered metric; `null` for non-histogram kinds. **Binding clause:** the deferred consumers "item #2 agent tools, item #3 Grafana are exactly who needs them off-box" — the tool layer MUST read `buckets` from the family entry, not re-derive them.
- **`chart_role`** is an *additive per-name member* (`:136-141`): `chart_role ∈ {line, step}`; counters render STEP, gauges LINEAR. It does **not** bump `schema_version` (AM-7 additive policy).
- **No second family table**: runtime introspection is the serving truth; the markdown catalog is authoring truth and is never read at request time (`:147-149`).

**`/snapshot`** — current published values (`:161-176`):

```
{…envelope header…, series:[{name, labels, kind, monotonic, value, last_publish_age_s}]}
```
plus `?family=` / `?name_prefix=` filters. Bounded: `?offset=` / `?limit=` (limit clamped to `MAX_SNAPSHOT_SERIES = 500`), byte ceiling applies, body carries the continuation cursor, plus `truncated: bool`.
- `kind ∈ {gauge, counter, histogram}` — **no `summary`** (`:178-180`); every series carries `monotonic: bool` (counters non-decreasing **within one `restart_epoch`**); histogram series carry `value: null` + `histogram: {count, sum, buckets}` **as stored** (not Prom-cumulative).
- **Staleness is explicit metadata, never a silently stale value**: `last_publish_age_s` = seconds since the metric's last publish, via the public `Metric.last_publish_monotonic()` accessor (`:167-170`; accessor at `daemon/checkpoint_metrics.py:628-638`).

**`/series`** — windowed trend (`:183-207`):

```
{…envelope header…,
 window:{from_utc, to_utc, interval_s, interval_s_source:"observed"},
 last_sample_age_s, stale,
 series:[{name, labels, kind, monotonic, points:[[t, v],…]}]}
```
plus `?series=` (label-aware), `?names=` (≤ 50, name-level), `?window_s=` (clamped to the buffer window), `?max_points=` (server-side **bucket-max** downsample — named, never every-kth; **default 240**), `?cursor=`.

- `interval_s` is the **OBSERVED** effective spacing `max(sampler, publisher)` = 15 s at defaults — **never the configured cadence** (`:196-197`).
- `stale: bool` when `last_sample_age_s > 3 × effective interval` (`:198-199`); a wedged sampler surfaces as **staleness**, never a silently frozen line.
- `window_s` anchors at the server's `generated_at_utc`; a cursor pins its evaluation instant (`:200`).
- Default `max_points = 240` is the floor the acceptance criteria require (60 min × 4/min at 15 s spacing) (`:201-203`).

### 1.2 Series identity rules (AM-1) — `:113-120`

- A series is `(name, labels)`; **the labels are the complete discriminator**.
- The canonical serialization is the registry's `_label_key` (sorted `(k, v)` pairs) — `daemon/checkpoint_metrics.py:485` (public face `label_key()` at `:481-497`).
- Ring keys and any future item-#2 watch-tool reference use the same serialization (`:116-118`).
- The door therefore exposes a **label-aware** selector `?series=<name>{<k>=<v>,…}` (RFC-3986-escaped) alongside `?names=`; `?names=` stays **name-level** and returns every label variant.
- Implementation: `selectors.parse_series_selector` (`selectors.py:32-61`) parses `<name>` or `<name>{k=v,…}`; `selectors.selector_matches` (`selectors.py:64-77`) compares via `label_key(labels) == label_key(selector_labels)` — so a selector can never disagree with the ring keys.
- **Binding clause for the tool layer:** a tool's series reference MUST be `(name, labels)`; a name-only reference is ambiguous by construction.

### 1.3 Cardinality classes and their guards (AM-2) — `:150-153`, `:310-311`

- `cardinality_class ∈ {closed, open, none}`:
  - **closed** — a metric with `allowed_values` for **every** label key;
  - **open** — declares label keys without a frozen value set → renders `labels: null` and is **never value-enumerated**;
  - **none** — no label keys.
- Derivation (code): `RegistryMetricsQueryProvider._cardinality_class` (`provider.py:182-191`); the declared value sets come from `_declared_labels` (`provider.py:193-202`), which returns `None` unless the class is closed.
- **Binding clause:** "`open` names render `labels: null` and are never value-enumerated" (`:310-311`). A tool that enumerates label values for an `open` metric violates the contract.
- Two **guard counters** back the `open` class: `ensemble_metrics_registry_measurements` / `ensemble_metrics_cap_rejected_total` / `ensemble_metrics_invalid_label_total` (catalog F0.2, `metrics-collection/metric-catalog.md:214`).

### 1.4 Cursor policy — two-axis bounds (series + points) and truncation surfacing

**Cursor (AM-6)** — `:291-294`:
- `next_cursor: string | null`; **opaque**; pins the evaluation instant + position.
- An invalid/expired cursor (older than the buffer's oldest point) ⇒ `400 invalid_cursor` — never a 500, never a silent gap.
- Implementation: `envelope.encode_cursor(payload)` / `decode_cursor(cursor)` (`envelope.py:263-282`), `cursor_is_expired(cursor, *, oldest_timestamp, now)` (`envelope.py:285+`), `envelope.InvalidCursor` (`envelope.py:39`).

**Two-axis bounds** — `:267-289`:
- `/snapshot` is bounded like the other two (`?offset=` / `?limit=`, limit clamped to `MAX_SNAPSHOT_SERIES = 500`).
- `/series` bounds the **EMITTED body**, not only the request: the emitted-series ceiling `MAX_SERIES = 500` **and** the total emitted-points ceiling `MAX_TOTAL_POINTS = 12 000`. The selector ceiling (`MAX_NAMES = 50`) bounds only the *requested* selectors — a no-selector request previously matched every published series.
- **Binding clause (`:283-286`): "A bound that drops something is never silent — the builder sets `truncated` when it drops something, and a body that dropped something ALWAYS carries the continuation cursor; `truncated` also marks a partial view (a page reached with a cursor) and such a page may carry no cursor."**
- The byte-ceiling probe and the trim run **off the event loop**, inside the off-loop page builder (`:287-289`).

**Cursor precedence (ND-5)** — `:296-303`: the cursor-expiry probe runs **FIRST** in all three page builders (`daemon/routers/metrics.py:465` families · `:504` snapshot · `:539` series). On `/snapshot` the unknown-family check sits **downstream** of it ⇒ an expired cursor + unknown `family` answers `400 invalid_cursor`, **not** `404 unknown_family`.

Constants (`daemon/services/metrics_query/constants.py`):
`QUERY_SCHEMA_VERSION = 1` (`:25`) · `POINT_BUDGET = 750_000` (`:29`) · `WINDOW_SECONDS = 7200` (`:33`) · `MIN_DEPTH = 60` (`:37`) · `DEFAULT_MAX_POINTS = 240` (`:42`) · `MAX_NAMES = 50` (`:46`) · `MAX_POINTS_CEILING = 240` (`:55`) · `MAX_FAMILIES = 500` (`:59`) · `MAX_SERIES = 500` (`:69`) · `MAX_TOTAL_POINTS = 12_000` (`:78`) · `MAX_SNAPSHOT_SERIES = 500` (`:83`) · `RESPONSE_BYTE_CEILING = 1_048_576` (`:87`) · `STALE_INTERVAL_MULTIPLIER = 3.0` (`:92`) · `IDLE_GC_SAMPLINGS = 3` (`:98`).

### 1.5 Buckets-on-the-wire shape

- Family entry `names[i].buckets` = `list[float] | None` (`provider.py:231-242`: `buckets = getattr(metric, "buckets", None)` → `list(buckets) if buckets else None`).
- The **metric** stores buckets as a tuple of floats at construction: `Metric.__init__(..., buckets: Optional[Iterable[float]] = None, ...)` → `self.buckets = tuple(float(b) for b in buckets)` (`daemon/checkpoint_metrics.py:570, :584`).
- Snapshot histogram series carry `histogram: {count, sum, buckets}` **as stored** (`provider.py:306-312`).
- `/series` histogram series carry the **count** as the point value (not the bucket vector) (`provider.py:371-375`).
- **Binding clause:** the provider derives the boundaries; the consumer (item #2 tools, item #3 Grafana) must not re-derive them (`:132-135`).

### 1.6 `VOCABULARY_VERSION` 2 and the vocabulary it pins

- `VOCABULARY_VERSION: int = 2` — `daemon/checkpoint_metrics.py:208`. The docstring records the 1→2 cause: **the F7 checkpoint `op` enum was narrowed to `{aget, other}`** (`:202-207`) — `aget` is the sole observed producer (`daemon/persistence.py:632`), `other` is the **reserved fold target** and must stay declarable.
- The same bump carries **F18's audit-outcome amendment** to `{ok, error, timeout}` — `docs/architecture/metrics-promote-disclosure.md:61-62`; the vocabulary constant is `wiring/vocab.py:232` `GATEWAY_AUDIT_OUTCOMES: tuple[str, ...] = ("ok", "error", "timeout")`. `timeout` is preserved as its own label — **never folded into `error`** (`decisions.md:479`).
- Other frozen vocabularies pinned at v2 (all in `wiring/vocab.py`): `SPINE_TABLES` (`:50`), `LAG_STATS = ("p50","p95","max")` (`:61`), `LAG_PROBE_STATES` (`:62`), `WEDGE_REASONS` (`:66`), `WEDGE_WRITE_OUTCOMES = ("ok","failed")` (`:72`), `POOL_NAMES = ("manager","checkpoint")` (`:78`), `POOL_NATIVE_QUANTITIES = ("size","max","available","overflow")` (`:79`), `POOL_REQUEST_ERROR_KINDS` (`:80`), `PG_BACKEND_STATES` (`:91`), `PG_LEAK_SITES` (`:100`), `CHECKPOINT_TABLES` (`:111`), `READINESS_COMPONENTS` (`:116`), `READINESS_OUTCOMES` (`:122`), `ADMISSION_STATE_KEY = "admission_state"` (`:149`), `WORKER_POOL_NAMES = ("worker","chat")` (`:161`), `WORKER_POOL_EVENTS` (`:162`), `SWEEP_LANES` (`:174`), `SWEEP_ROW_OUTCOMES` (`:189`), `INSTANCE_LIVENESS` (`:208`), `MCP_ROSTER` (`:222`), `MCP_STATES = ("ok","degraded","quarantined","unknown")` (`:223`), `MCP_FAILURE_KINDS` (`:224`).
- Registry-side frozen sets in `daemon/checkpoint_metrics.py`: `INSTANCE_STATUS_VOCABULARY` (10 values, `:263-276`), `SWEEP_LANE_VOCABULARY` (13, `:290-306`).

### 1.7 `schema_version` policy (AM-7) — `:83-90`, `:340-351`

- Two **separate axes**: `schema_version` = envelope-shape version (integer `QUERY_SCHEMA_VERSION = 1`); `vocabulary_version` = mirror of the registry's `VOCABULARY_VERSION` (2 after this lane).
- **A BREAKING envelope change** (field removal / rename / type / semantic) **bumps `schema_version`**.
- **An ADDITIVE change** (new optional field / filter / enum value) **does NOT bump**, and **consumers must ignore unknown members**.
- `vocabulary_version` is a **separate axis** (label values, never envelope shape).
- FE mirrors are verbatim on field SETS, never on values: `vocabulary_version: number` (never a literal), and the availability reason is a widened union with an `unknown(<value>)` fallback so a future reason never breaks the compile.
- **Binding clause for the tool layer:** the tool must treat unknown members as ignorable and must NOT hard-code `vocabulary_version == 2` as a gate.

### 1.8 Availability / status reason vocabulary — and the REQUIRED reason ORDER

**Closed reason set** (`:350-367`; code: `availability.AVAILABILITY_REASONS`, `availability.py:34-40`):

```
census_gated
census_not_running
capture_off
series_absent
listener_disabled
```

- `census_gated` — switch OFF; sourced from the public `CensusSampler.gated` property (AM-18).
- `census_not_running` — non-postgres runtime / census thread not running.
- `capture_off` — wedge capture not active (F1/F3).
- `series_absent` — family registered, no observation yet.
- `listener_disabled` — **surface note for the exposition listener only; the query door never emits it**.
- `proc_unavailable` is **not** in the v1 set (F2 is out of the subset — AM-25).
- **Each reason is reachable by exactly ONE runtime fact** (`:364-367`).

**REQUIRED ORDER — `census_not_running` BEFORE `census_gated`** — `:360-367` and code `availability.derive_availability` (`availability.py:76-92`):
```
if source_idiom == "postgres_census":
    if not facts.census_running: return {"state":"dark","reason":"census_not_running"}   # FIRST
    if facts.census_gated:       return {"state":"dark","reason":"census_gated"}         # SECOND
elif source_idiom == "wedge_capture":
    if not facts.capture_active: return {"state":"dark","reason":"capture_off"}
if live_series_count <= 0:        return {"state":"dark","reason":"series_absent"}
return {"state":"live"}
```
Rationale in code (`availability.py:77-88`): the census-not-running fact "is the fact that EXPLAINS the darkness"; the truth-table row is "F6 availability → `census_not_running` (the switch state is NOT inferable)". The **polarity** of `census.gated` is a known OUT-OF-LANE defect (R-i) — the pin asserts only the ORDER, never the polarity.

**Backend-side reason-set assertion (A-9)** — `provider.py:256-266`: the provider `assert`s the emitted reason is `None` or in `AVAILABILITY_REASONS` — "the backend therefore refuses to emit a reason outside the CLOSED v1 set — the surface stays observable". A tool must not widen the union on the backend side.

**Availability facts are INJECTED** (`availability.AvailabilityFacts`, `availability.py:49-62`): `census_gated: bool = False`, `census_running: bool = False`, `capture_active: bool = False`. "The facts are INJECTED — the query package never goes looking for them itself (no switch read, no DB probe, no thread inspection)."

### 1.9 Error vocabulary — `:233-258`

```
404 unknown_family
400 invalid_label_filter
400 invalid_cursor
503 registry_unavailable
503 AUTH_NOT_CONFIGURED
403 SCOPE_INSUFFICIENT
```
- **"never a 500 for an expected condition"** (`:233`).
- The 403 token is the **SHIPPED spelling** `SCOPE_INSUFFICIENT` (`daemon/auth/decorators.py:75-78` — `_SCOPE_403_DETAIL = {"error": "SCOPE_INSUFFICIENT", "message": "Token lacks required scope"}`). The contract's own block previously rendered it lowercase; that was a **documentation** defect, not behavioural (`:244-252`).
- `400 invalid_label_filter` covers a malformed label-aware selector **or** a `?names=` request exceeding the 50-name ceiling — "the filter is REJECTED, never silently truncated" (`:255-257`).

### 1.10 The kill-switch truth table — `:313-324`

| Surface | Auth configured | Switch | Runtime | Scope | Result |
|---|---|---|---|---|---|
| `/api/metrics/{families,snapshot,series}` | OFF | any | any | any | **503 `AUTH_NOT_CONFIGURED`** — before any provider read (fail-closed) |
| the three query routes | ON | ON | any | `maintenance:read` | **200** — registry-sourced series live |
| the three query routes | ON | OFF | any | `maintenance:read` | **200** — registry-sourced series **identical** to the switch-ON case |
| the three query routes | ON | any | any | scope miss | **403** |
| the three query routes | ON | any | any | no session | **401** |
| the three query routes | ON | any | non-PG | `maintenance:read` | **200** — F6 `availability` → `census_not_running` |
| `:9798` listener body | n/a | OFF | any | n/a | **200 valid empty** (socket still bound) |
| `:9798` listener body | n/a | ON | any | n/a | **200** — full exposition body |

**Five pinned invariants** (`:326-334`, the fenced `truth-table-invariants` block):
```
auth_behaviour_identical_across_switch_states
registry_series_set_identical_for_non_switch_derived_families
only_permitted_body_differences
check_site_census_equals_two
no_direct_switch_read_in_query_package
```
1. Auth behaviour identical across switch states (status codes, error vocabulary, scope decision, fail-closed seam) — `:336-337`.
2. The registry-sourced series set is identical for every family whose availability reason is not switch-derived — `:338-339`.
3. The **only permitted body differences**: (i) F6 `availability` → `census_gated`, (ii) `activation.census_enabled` → false, (iii) `last_publish_age_s` growth — `:340-342`.
4. The check-site census over `daemon/` remains **exactly 2** (listener body call line `metrics_listener.py:419`; census tick call line `metrics_census.py:355`) — `:343-344`.
5. **No direct switch read in the query package or the routes** — `census_gated` is sourced from the public `CensusSampler.gated` property — `:345-348`.

**The door has NO kill-switch of its own** — its emergency-off lever is **token revocation of the `maintenance:read` grant** (`:58-62`).

### 1.11 Read-path purity (load-bearing) — `:215-223`

- The query path reads **published registry state and ring buffers only**. It never renders, never acquires a metric lock (the no-lock pin allowlists `registered_metrics()` — the registry-level lock, not a metric lock), never performs a database read, and **never touches the switch** (AM-18).
- Database-backed producers (F10 queue counts, F13 instance counts, F18 audit counts) are pulled by the **sampler thread** into the registry via **one-shot direct connects** (AM-11 — a persistent per-adapter connection is forbidden: pool-in-disguise ⇒ the idle-in-transaction class); the door reads the published gauge.
- **Binding clause:** the tool layer inherits this — a tool must read published state, never query the DB for metrics.

### 1.12 Envelope header (all three routes) — `:92-107`

```
schema_version: 1
vocabulary_version: <mirror of VOCABULARY_VERSION>
generated_at_utc: "<ISO-8601 aware UTC>"     # timestamps.now_utc_iso()  (daemon/services/timestamps.py:63)
restart_epoch: "<CURRENT_BOOT_ID>"           # bash_spawn_reaper.CURRENT_BOOT_ID (daemon/services/bash_spawn_reaper.py:106)
activation: { listener_enabled: bool, census_enabled: bool, query_api: "auth-gated-always-on" }
```
- `restart_epoch` = the boot id (uuid4().hex) — a restart is visible, never a silent gap. `boot_epoch.get_boot_epoch()` is **NOT** used (DB-clock domain, None-able) — `:99-103`.
- `census_enabled` is sourced from the **public `CensusSampler.gated` property** — a read-through of the EXISTING private `_gate()`, **no new check site** — `:109-111`.
- The **shipped** envelope also carries `activation.registry_read_ok` (the H5 degraded flag: `False` only when the provider's registry read RAISED; a successful read of an empty registry leaves it `True`) — `envelope.activation_block`, `envelope.py:56-71`. This is additive.

### 1.13 The activation-surface matrix (six surfaces) — `:28-35`

1. `:9798` exposition **listener body** — switch-gated default-OFF **at the body level** (socket still binds, serves a valid empty 200), unauthenticated, loopback-only.
2. `/api/metrics` **text exposition** — NOT switch-gated; auth-gated `maintenance:read`.
3. **Query routes** — NOT switch-gated (D-11b / D1(b) / AM-17); auth-gated `maintenance:read`; "**availability, not emptiness** — the door serves live registry data in both switch states".
4. **Census DB work** — switch-gated; the sampler keeps ticking, its DB leg is skipped; F6 goes dark with `availability.reason="census_gated"`.
5. **Registry accumulation** — ungated, always on.
6. **FE charts polling** — never switch-gated; polls only the query door; degrades to cached/last-good marked stale.

**"The switch never touches authenticated surfaces"** (`:37-39`).

---

## 2. THE PROVIDER SEAM

Package: `daemon/services/metrics_query/`. Files: `__init__.py`, `availability.py`, `constants.py`, `downsample.py`, `envelope.py`, `protocol.py`, `provider.py`, `ring.py`, `roster.py`, `sampler.py`, `selectors.py`, `wiring/`.

### 2.1 The protocol — `daemon/services/metrics_query/protocol.py:22-52`

```python
@runtime_checkable
class MetricsQueryProvider(Protocol):
    def families(self) -> list[dict[str, Any]]: ...
    def snapshot(self, *, family: str | None = None,
                       name_prefix: str | None = None) -> list[dict[str, Any]]: ...
    def history(self, *, names: Sequence[str] | None = None,
                      series: Sequence[str] | None = None,
                      window_s: int | None = None,
                      max_points: int | None = None) -> list[dict[str, Any]]: ...
```
- The protocol is **HTTP-agnostic**: "no `Request`, no FastAPI type, no web-framework import appears in any signature (plan task 2.2). A duck-typed fake provider satisfies it, which is what makes the routes testable without a registry" (`:3-8`).
- The three methods ARE the contract's three views: `families()` = the family catalog (the frozen shape); `snapshot()` = current published values; `history()` = windowed trend points (`:10-14`).

### 2.2 The concrete provider — `daemon/services/metrics_query/provider.py`

**Constructor (`:61-71`)** — all keyword-only:
```python
RegistryMetricsQueryProvider(
    *,
    census: Any | None = None,
    ring: TrendRingStore | None = None,
    listener_enabled: bool = False,
    capture_active: bool = False,
    metrics_provider: Callable[[], list[Any]] | None = None,
    sampler_interval_s: float = 10.0,
    publisher_cadence: Callable[[], float] | None = None,
)
```
The load-bearing contract (`:3-12`):
- reads **PUBLISHED registry state only** — never renders, never acquires a METRIC lock, never mutates, never touches a database, never reads the kill-switch;
- the only registry lock taken is the REGISTRY-level lock inside `registered_metrics()` — "a metric's own lock is never taken, so a wedged writer can never block the door";
- "the staleness of a value is EXPLICIT metadata (`last_publish_age_s`), never a silently stale number."

**Public surface:**

| Member | Kind | Location | Notes |
|---|---|---|---|
| `listener_enabled` | property → `bool` | `:87-96` | a PROCESS fact, deliberately NOT the switch state (switch-INVARIANT) |
| `registry_read_ok` | property → `bool` | `:98-107` | H5 degraded flag; `False` only when a read RAISED |
| `census_enabled` | property → `bool` | `:109-122` | read-through of the public `CensusSampler.gated`; a broken census never breaks the door |
| `effective_interval_s` | property → `float` | `:124-131` | OBSERVED spacing `max(sampler, publisher)`; falls back to `cm.DEFAULT_PUBLISHER_CADENCE_SECONDS` |
| `census_running` | property → `bool` | `:133-140` | |
| `availability_facts` | property → `AvailabilityFacts` | `:142-148` | `census_gated=not census_enabled`, `census_running`, `capture_active` |
| `families()` | → `list[dict]` | `:211-279` | the canonical frozen shape |
| `snapshot(*, family=None, name_prefix=None)` | → `list[dict]` | `:281-316` | one entry per live label combination |
| `history(*, names=None, series=None, window_s=None, max_points=None, now=None, max_series=None)` | → `list[dict]` | `:318-393` | `max_series` is a **work bound**, "not a silent truncation of the ANSWER" |
| `last_sample_age_s()` | → `float` | `:397-404` | 0.0 when the ring is empty |
| `last_sample_timestamp()` | → `int | None` | `:406-410` | newest ring sample |
| `oldest_sample_timestamp()` | → `int | None` | `:412-422` | **the cursor's EXPIRY probe** |

**Provider-level behaviours a tool must respect:**
- `history()` raises `InvalidLabelFilter` when `len(name_set) > MAX_NAMES` (`provider.py:346-349`) — the same class the route maps to `400 invalid_label_filter`.
- `history()` clamps `window_s` into `[1, WINDOW_SECONDS]` (`:350-351`); `max_points` defaults to `DEFAULT_MAX_POINTS` (`:353`).
- `history()` returns `[]` when `self._ring is None` (`:340-341`) — **no ring ⇒ no trend**, not an error.
- `families()` groups by `getattr(metric, "family", "") or metric.name` and sorts families (`:213-220`).
- `families()` computes `role` as `cm.FAMILY_ROLE_CHART` iff any member `is_chart_primary()` (`:246-250`).
- `snapshot()` skips metrics with no published snapshot entirely (`:294-296`) — a family with no observation yields **no rows**, and the *family-level* `series_absent` reason lives on `/families`.

### 2.3 How a caller obtains a provider instance

- **Wiring (production):** `daemon/api.py:2502-2547`, inside the lifespan:
  - `TrendRingStore(effective_interval_s=_metrics_effective_interval_s)` — `:2524-2526`;
  - `RegistryMetricsQueryProvider(census=app.state.metrics_census, ring=_metrics_ring, listener_enabled=app.state.metrics_listener is not None, sampler_interval_s=_metrics_sampler_interval_s)` — `:2527-2532`;
  - **`app.state.metrics_query = _metrics_query_provider`** — `:2533`;
  - `MetricsSampler(ring=_metrics_ring, interval_s=_metrics_sampler_interval_s)` + `.start()` — `:2534-2538`; `app.state.metrics_sampler = _metrics_sampler` — `:2539`.
  - **Degradation:** a construction/start failure sets `app.state.metrics_query = None` and logs ERROR — "the daemon CONTINUES" (`:2540-2547`).
- **Router read path:** `_query_provider(request)` reads `request.app.state.metrics_query`; **its absence is the "registry unavailable" 503** — `daemon/routers/metrics.py:219-233`.
- **The provider is not a `/readyz` component** (AM-24) — `daemon/api.py:2495-2497`: "the readiness composite is parsed by the soak-gate grace discipline and the query door must not grow that surface."
- **Import is side-effect-free** — `daemon/services/metrics_query/__init__.py:16-19`: "importing this package starts NO thread, opens NO connection and registers NO metric. The package is a pure library; the lifespan wires the provider and the sampler."
- **⇒ For an in-process agent tool, the provider is reachable via the app/manager handle; there is currently NO factory function.** The only public re-exports are the classes/functions in `__init__.py:83-127`. The tool implementer must decide how to reach `app.state.metrics_query` from the tool factory (the router's `request.app` is not available in a tool) — **this is an open seam the planner must close.**

### 2.4 The envelope / response object shape

- The envelope builders live in `daemon/services/metrics_query/envelope.py` and are **provider-agnostic** (they take a `MetricsQueryProvider`):
  - `envelope_header(provider) -> dict` (`:74-82`) — the five header fields;
  - `activation_block(provider) -> dict` (`:56-71`);
  - `build_families_envelope(provider, *, limit=MAX_FAMILIES, offset=0) -> dict` (`:85-106`) — returns `{**header, "families": page, "truncated": bool}`;
  - `build_snapshot_envelope(provider, *, family=None, name_prefix=None, offset=0, limit=MAX_SNAPSHOT_SERIES) -> dict` (`:109-132`) — `{**header, "series": page, "truncated": bool}`;
  - `build_series_envelope(provider, *, names=None, series=None, window_s=None, max_points=None, now=None, offset=0, max_series=MAX_SERIES, max_total_points=MAX_TOTAL_POINTS) -> dict` (`:154-228`) — `{**header, "window": {...}, "last_sample_age_s", "stale", "series": page, "truncated": bool}`;
  - `effective_interval_s(provider) -> float` (`:149-151`) — reads the provider's `effective_interval_s` (property **or** method, via `_provider_float`, `:135-146` — the duck-typed-tolerant reader).
- `restart_epoch() -> str` (`:43-53`) — the boot id.
- Cursor helpers: `encode_cursor` (`:263`), `decode_cursor` (`:269`), `cursor_is_expired` (`:285`), `InvalidCursor` (`:39`).
- **How truncation/cursor/status are exposed:**
  - **truncation** — a boolean `truncated` on every envelope (`envelope.py:105, :131, :227`), set when a page dropped something OR when more remain;
  - **cursor** — the envelope builders do **NOT** mint a cursor; the **route** does, in `_bound_page(...)` (`daemon/routers/metrics.py:454-473, :476-522, :525-555`) with `_CURSOR_VERSION = 1` (`metrics.py:208`) and the cursor payload carrying `t` (the pinned instant) and `offset`. `decode_cursor` expects `{"t": <int>, ...}` (`envelope.py:285-290` reads `cursor.get("t")`).
  - **status** — there is no HTTP status in the provider/envelope; the status vocabulary (`404/400/503/403`) is a **router** concern. In-process, the equivalent exceptions are `_UnknownFamily` (`metrics.py:211-212`), `_ExpiredCursor` (`metrics.py:215-216`), `InvalidLabelFilter` (`selectors.py:28-29`), `InvalidCursor` (`envelope.py:39`).

### 2.5 How families are enumerated — the roster

`daemon/services/metrics_query/roster.py`:
- The roster is a **DECLARATION** of chart roles passed at **REGISTRATION** time (`chart_role=` kwarg); "the door serves whatever the registry carries. Adding a family to the roster is one `chart_role` kwarg at registration + one row here — no door edit, no FE edit" (`:9-14`).
- `CHART_PRIMARY_ROSTER: tuple[RosterFamily, ...]` (`:67-199`) — **12 families**: F1, F4, F5, F6, F9, F10, F11, F13, F7, F8, F18, F21.
- `RosterSeries(name, chart_role, labels="")` (`:39-45`); `RosterFamily(family, series)` with a `chart_role` property = the mode of its **first** declared series (`:48-64`).
- `CHART_ROLE_LINE = "line"` / `CHART_ROLE_STEP = "step"` (`:35-36`); the closed set is `checkpoint_metrics.CHART_ROLES = frozenset({"line","step"})` (`checkpoint_metrics.py:237`).
- `DROPPED_FAMILIES = ("F2",)` (`:204`) — recorded so the absence is a decision.
- Helpers: `roster_role_for(family, name) -> str | None` (`:207-220`), `roster_family_names() -> tuple[str, ...]` (`:223-225`), `assert_roster_is_well_formed()` (`:228-251`) — exactly 12 families, F2 absent, roles ∈ `CHART_ROLES`, ids unique.
- **Roster rules** (`:16-25`): a family NOT in the roster is stat-primary; every roster series carries a `chart_role`; counters render STEP, gauges LINEAR ("a linear interpolation between counter samples would assert values that were never observed").

**Family ids are NOT the catalog ids.** The door's `family` id is the **registration-time `family` kwarg** on the metric (`Metric.family`, `checkpoint_metrics.py:591` — "a metric without an explicit family IS its own family"). The catalog ids (F0.3, F1, F3, …) are the *planning* ids and appear as the `family` value for the 16 wired families (`wiring/registry.py` `FamilySpec` / `register_family_metric`, `:64, :74`).

### 2.6 The wiring sub-package (producer side)

`daemon/services/metrics_query/wiring/`:
- `wiring/__init__.py:58` `default_adapters() -> tuple[PullAdapter, ...]`;
- `wiring/registry.py` — `FAMILY_SPECS: dict[str, FamilySpec]` (`:64`), `FAMILY_METRICS: list[cm.Metric]` (`:68`), `register_family_metric(...)` (`:74`), `register_pull_adapter(family, adapter)` (`:187`), `pull_adapters()` (`:192`), `registered_families()` (`:197`), `registered_family_metrics()` (`:202`), `family_metric_names()` (`:207`);
- `wiring/db.py` — `connect_once(...)` (`:81`), `one_shot(...)` (`:103`), `fetch_rows(...)` (`:138`), `CONNECT_TIMEOUT_S = 3` (`:40`), `STATEMENT_TIMEOUT_MS = 5_000` (`:43`), `APPLICATION_NAME = "ensemble-metrics-query"` (`:46`), failure-log rate limit 60 s (`:54`);
- `wiring/adjudication.py` — the machine-readable OUT-list (see §5.2);
- `wiring/vocab.py` — the frozen label vocabularies (see §1.6);
- `wiring/sources.py` — `WiringSources`, `bind_sources` (`:129`), `unbind_sources` (`:139`), `current_sources()` (`:368`), `bundle_handle_presence(bundle)` (`:306`);
- `wiring/accumulate.py` — `DeltaAccumulator` (`:72`) — the cumulative-vs-per-tick diffing the F12 sweep families need;
- `wiring/w1.py` / `w2.py` / `w3.py` / `ws_a.py` — the per-cluster family registrations (W1 cluster = F0.3, F1, F3, F4, F5, …).

### 2.7 What the seam does NOT support

- **No direct DB access from the read path.** `provider.py:5-7` — "never touches a database". DB-backed families are pulled by the sampler thread via one-shot connects (contract `:219-223`).
- **No write path.** "it never mutates (no inc/observe calls reachable from the query layer; enforced by review + a pin that greps the query package for mutation calls)" — `decisions.md:372-374`.
- **No metric lock.** Only the registry-level lock via `registered_metrics()` (`provider.py:48-55`).
- **No switch read.** "The query package NEVER reads the switch" (contract `:111`; `availability.py:10-15`).
- **No exposition rendering.** `render_metrics()` is exposition-path-only (`decisions.md:320-321`).
- **No durability / long history.** The trend ring is **in-RAM only**, lost on restart by design (`contract:400-402`); "The Prometheus TSDB owns that" (`constants.py:31-32`).
- **No pagination cursor minting in the envelope layer** — the route does it (see §2.4).
- **No HTTP/auth types in any signature** — the protocol is deliberately web-framework-free (`protocol.py:3-8`).
- **No family-name→metric-name mapping table in the door** — "No second family table" (contract `:149`).
- **No `summary` kind** — the registry never produces it (`contract:178-179`).

### 2.8 Provider vs. the REST router — what the router ADDS

| Concern | Provider | REST router (`daemon/routers/metrics.py`) |
|---|---|---|
| Scope enforcement | **none** | `dependencies=[scope_required(_SCOPE_READ)]` — `:143` (text), `:560` (families), `:604` (snapshot), `:673` (series) |
| Auth-ON fail-closed 503 | none | `_require_auth_on(request)` — `:121-137`, called **before** the provider read (`:167, :585, :632, :696`) |
| Provider-availability 503 | `None` is a plain attribute | `_query_provider(request)` → `503 registry_unavailable` — `:219-233` |
| HTTP status mapping | n/a | `_err(status_code, error, message)` → `HTTPException(detail={"error","message"})` — `:109-118`; `_UnknownFamily` → 404 (`:662-665`); `_ExpiredCursor` → 400 (`:598-599, :666-667`) |
| Query-param parsing / clamping | raw kwargs | `family`/`name_prefix` (`:634-635`), `offset`/`limit` clamped (`:642-649`), `max_points` clamped to `MAX_POINTS_CEILING`, `names`/`series` split |
| Cursor decode + expiry | `decode_cursor` / `cursor_is_expired` helpers exist in the package | `_cursor_state(request)` + `_expire_cursor(provider, cursor)` — run **first** (`:465, :504, :539`) |
| Cursor minting + byte ceiling | not in the envelope | `_bound_page(...)` — `:454-473` etc. |
| Off-loop body build | n/a | `_build_off_loop(fn, *args, **kwargs)` via `daemon.services.db_offload.offload` — `:441-451` |
| Unknown-family check | none | only on `/snapshot` — `:505-508` (**downstream** of the expiry probe) |

**⇒ For the tool lane: the tool consumes the PROVIDER (or the envelope builders) directly. There is no HTTP round trip, and therefore no scope check, no 401/403, no 503 AUTH_NOT_CONFIGURED, no cursor minting, and no off-loop build.** See §3.3.

---

## 3. AUTH AT THE DOOR

### 3.1 How `/api/metrics/*` is gated today

- **Scope string:** `_SCOPE_READ = "maintenance:read"` — `daemon/routers/metrics.py:100`. Deliberate scope **REUSE** (the same scope the `/api/wedge-captures` family carries); "NO new scope is minted for the metrics surface" (`:96-99`).
- **Route registration:** `dependencies=[scope_required(_SCOPE_READ)]` on each of the four routes — `:143` (text exposition), `:560` (families), `:604` (snapshot), `:673` (series).
- **Auth-ON (401) is the middleware's job:** the route carries **no class tag**, so it is protected by default (an untagged route is protected — `daemon/auth/route_meta.py`); "The middleware 401s an unauthenticated caller, and the scope check 403s an authenticated caller WITHOUT `maintenance:read` (`SCOPE_INSUFFICIENT`)" — `metrics.py:59-63`.
- **Auth-OFF (503) fail-closed:** `_require_auth_on(request)` — `metrics.py:121-137`:
  ```python
  cfg = getattr(getattr(request.app, "state", None), "auth_config", None)
  if cfg is None:
      raise _err(503, _ERR_AUTH_NOT_CONFIGURED, "Auth not configured (fail-closed)")
  ```
  `create_app` sets `app.state.auth_config` **only** when auth is enabled; its absence is the authoritative OFF signal (`:124-129`). The gate is **inline in the handler body** (not a route dependency) so it runs AFTER the middleware's authentication decision and BEFORE any metric is read (`:72-74`). `_ERR_AUTH_NOT_CONFIGURED = "AUTH_NOT_CONFIGURED"` (`:106`).

### 3.2 Where scopes are declared and enforced

- **Taxonomy (single source of truth):** `daemon/auth/scopes.py` — `SCOPE_TAXONOMY: frozenset[str]` (`:60-101`), containing `"maintenance:read"` at `:70`. `SCOPE_WILDCARD = "*"` (`:104`). `SESSION_OPERATOR_SCOPES: frozenset[str] = SCOPE_TAXONOMY` (`:111`) — the operator lane auto-carries the whole taxonomy. `BOOTSTRAP_DEFAULT_SCOPES = ("auth:admin",)` (`:116`). `normalize_scope_list(raw)` (`:119`).
- **Enforcement:** `daemon/auth/decorators.py` — `scope_required(...)` (the FastAPI dependency form) and `require_scope(*required)` (the wrapper form) (`:41-46, :117`); both stamp `required_scopes` onto the callable so the router-scope manifest cage can introspect every decorated route (`:48-50`).
- **Decision semantics:** `_enforce(request, required)` (`:108-114`) — **inert when auth is OFF** (`:110-111`), else grant-check with `403` (`_SCOPE_403_DETAIL`, `:75-78`) on a miss. `_context_satisfies(ctx, required)` (`:94-105`) — "**NO lane satisfies scopes by identity.** Every lane — session (cookie operator) and bearer (machine) alike — satisfies a requirement only when its GRANTED scope set contains one of the required scopes (or the `*` wildcard). `None` context never satisfies (fail-closed)."
- **Manifest cage (the pin a new scope must satisfy):**
  - `tests/unit/test_router_scope_manifest.py:169` — `"/api/metrics": "maintenance:read"`;
  - `tests/unit/metrics/test_metrics_query_scope_cage.py:147` — `assert PINNED_SCOPE_MANIFEST["/api/metrics"] == FAMILY_SCOPE`;
  - `tests/integration/test_router_class_manifest.py:147` — `"/api/metrics": frozenset({"pd"})` (the class/pd tag);
  - the same file also pins a **declared-route count** (`test_declared_route_count_pinned`, `tests/unit/test_router_scope_manifest.py:333-396`, `len(declared) == 108` at the research base — **adding three scope-decorated routes takes it 108→111**, per `decisions.md:298-305`).
- **The scope-manifest is a SET OF STRINGS declared in the test**, not a runtime registry; the taxonomy constant is `daemon/auth/scopes.py`.

### 3.3 Is the provider seam itself scope-checked?

**NO.** Evidence:
- The provider module imports **nothing** from `daemon.auth` — `provider.py:26-43` imports only `daemon.checkpoint_metrics`, the `metrics_query` submodules, and stdlib.
- The protocol's docstring is explicit: "no `Request`, no FastAPI type, no web-framework import appears in any signature" (`protocol.py:3-8`).
- The scope check lives exclusively in the **router** (`metrics.py:143, :560, :604, :673`) and the fail-closed 503 lives exclusively in the router (`metrics.py:121-137`).
- The provider is stored on `app.state.metrics_query` (`daemon/api.py:2533`) with **no auth wrapper**.

**⇒ This is the single most important open design question for the tool lane.** An in-process agent tool calling the provider **bypasses the entire HTTP auth posture** — no scope, no 401/403, no `AUTH_NOT_CONFIGURED` fail-closed. The tool layer therefore must supply its own gate (an agent-id allow-list, mirroring the house pattern `INSTANCE_READ_AUTHORIZED_AGENTS` / `WEDGE_CAPTURE_AUTHORIZED_AGENTS` / `INSTANCE_OPS_AUTHORIZED_AGENTS` — see §5.4) or it must consume the **HTTP** door instead of the provider.

Note the contract's own statement that "Future agent tools (deferred item #2) call the SAME provider functions directly — the provider is importable, side-effect-free, and the single read substrate for both the HTTP door and the tool door" (`decisions.md:375-377`). This is the **architecture decision** — but it is silent on the auth question, which §D6 partially answers (see below).

### 3.4 What a NEW `metrics:read` scope would have to hook into — exact sites

Per `decisions.md` §D6 (`:641-658`) and `docs/architecture/metrics-query-api.md` §5 (`:268-292`):

1. **1 taxonomy line** — add `"metrics:read"` to `daemon/auth/scopes.py` `SCOPE_TAXONOMY` (the block at `:60-101`).
2. **1 manifest line** — the family scope becomes `"/api/metrics": "maintenance:read|metrics:read"` in the **scope manifest** (`tests/unit/test_router_scope_manifest.py:169`) and the scope-cage pin (`tests/unit/metrics/test_metrics_query_scope_cage.py:147`).
3. **4 decorator lines** — the three query routes declare `metrics:read`; the shipped text route keeps `maintenance:read` (`daemon/routers/metrics.py:560, :604, :673` for the three; `:143` unchanged).
4. **Cage note** — the scope cage is a **subset check**, so the three routes stay inside the pinned set; `SESSION_OPERATOR_SCOPES` auto-grants (`scopes.py:111` = `SCOPE_TAXONOMY`), so **no operator re-grant is needed**.

**Trigger for the retrofit (the recorded condition):** *"a non-operator machine lane reads the door in this lane's lifetime (e.g. a Grafana / deferred-#3 token) ⇒ mint `metrics:read` at that point."* (`:655-656`)

**For THIS lane (item #2 = the agent-tool door), the D6 trigger is arguably MET** — an agent-tool lane IS a non-operator consumer. But the D6 retrofit map is written for the **HTTP** door; an in-process tool bypasses the scope system entirely (§3.3). The planner must decide whether item #2 mints `metrics:read` (and if so, whether the tool is gated by the scope, by an agent allow-list, or by both).

**Also note:** `maintenance:read` **also confers checkpoint-cleanup-EXECUTE capability** (`daemon/routers/maintenance.py:255`, `:279-283`) — `metrics-query-api.md:270-273`: "The query door is therefore **not** a narrower privilege than the maintenance scope it borrows: a caller who can read the metrics can also trigger a checkpoint cleanup."

---

## 4. THE COMMITMENT (`decisions.md` §D6) — VERBATIM

`.agents/shared/planning/central-metrics-v1/decisions.md:641-658`:

```
## D6 — Deferred scope split (`metrics:read`) — NAMED COMMITMENT (leader LR-1)

**Status: NAMED COMMITMENT, not built now.** v1 keeps `maintenance:read` (zero amendment; the
scope-manifest cage is trivially green). This section is **binding guidance for the deferred
lanes** (deferred item #2 agent tools, deferred item #3 Grafana) — it is not a "revisit later".

**Retrofit map (4-line + manifest, cheap — AM-20):**
1. **1 taxonomy line** — add `metrics:read` to the scope taxonomy (`daemon/auth/scopes.py:60-101`).
2. **1 manifest line** — family scope becomes `"/api/metrics": "maintenance:read|metrics:read"`.
3. **4 decorator lines** — the three new query routes declare `metrics:read`; the shipped text
   route keeps `maintenance:read`.
4. **Cage note** — the scope cage is a **subset check**, so the three routes stay inside the
   pinned set; `SESSION_OPERATOR_SCOPES` auto-grants, so no operator re-grant is needed.

**Trigger for the retrofit (the recorded condition):** a non-operator machine lane reads the door
in this lane's lifetime (e.g. a Grafana / deferred-#3 token) ⇒ mint `metrics:read` at that point.
**Until then:** v1 stays on `maintenance:read` and the query-API docs disclose the over-grant
(checkpoint-cleanup-execute also rides `maintenance:read`).
```

**Public restatement (shipped doc)** — `docs/architecture/metrics-query-api.md:286-292`:
> "**The trigger for the retrofit (the recorded condition):** a non-operator machine lane reads the door in this lane's lifetime (a Grafana token, or **the deferred agent-tool door**) ⇒ mint `metrics:read` at that point. … **This section is binding guidance for the deferred lanes** (the deferred agent-tool door and the deferred Grafana layer). It is not a 'revisit later'."

**Fold-ledger cross-reference:** `.agents/shared/planning/central-metrics-v1/fold-ledger.md:119` —
> "| **DEF-3** | `metrics:read` scope split | **named commitment** (LR-1), not built now — the retrofit map (1 taxonomy line + 1 manifest line + 4 decorator lines) is recorded in `decisions.md` §D6 as binding guidance for the deferred lanes (items #2/#3). | the deferred agent-tool / Grafana lanes |"

### 4.1 Every OTHER decision that binds a downstream agent-tool lane

| Decision | Location | Binding content for item #2 |
|---|---|---|
| **D1 — Activation posture** | `decisions.md:28-125` (ruling `:70-125`) | The query API is **auth/scope-gated ONLY**; it **does NOT consult `ENSEMBLE_METRICS_ENABLED`** (`:81-85`). Option B: per-surface posture. The switch's blast radius is exactly **2** behavioural check sites; a tool that added a third would break invariant 4. |
| **D2 — FE charting approach** | `:126-202` (ruling `:160-191`) | "**Step rendering is IN the freeze** (approver B5 + leader ruling): counters render STEP and gauges LINEAR — a linear interpolation between counter samples would assert values that were never observed." The tool layer inherits the `chart_role` semantics. |
| **D3 — Storage/retention** | `:204-288` (ruling `:235-288`) | Trend storage is **IN-MEMORY ONLY** — array-backed ring, 16 B/point, `POINT_BUDGET = 750 000` ⇒ 11.4 MiB worst case; **lost on restart by design**; a restart is visible via `restart_epoch`, never a silent gap. **This is the freshness contract for a tool.** |
| **D4 — Query API architecture (the ONE DOOR)** | `:289-444` (ruling `:330-444`) | Option A — one family, three routes, **one provider**. The three route shapes are quoted token-identically (`:336-338`). Bump policy AM-7 (`:346-351`). Auth/scope posture (`:353-359`). Over-grant disclosure + retrofit map (`:361-368`). **Swappability guarantee (`:370-377`): "Future agent tools (deferred item #2) call the SAME provider functions directly — the provider is importable, side-effect-free, and the single read substrate for both the HTTP door and the tool door."** |
| **D5 — Extension subset derivation** | `:445-599` | The frozen membership rule (`:449-455`) and the per-family classification table (`:459-489`). **Subset = 16/26** (10 full + 6 partial) — see §5.2. |
| **Open-questions adjudication** | `:662-682` | F18 outcome vocabulary `{ok,error,timeout}` (`:666-669`); F21 conditionality OUT (`:670-673`); **F2 DROP from v1** ⇒ "Subset = **16/26**" (`:674-675`); sampler-key reuse ⇒ `window.interval_s` reports the **observed** effective spacing (`:676-678`). |

**Decisions that EXPLICITLY scope item #2 / #3 out of v1:**
- `central-metrics-v1/requirements.md:163` (FR-G1): "**no agent tool surface (item #2 deferred); no Grafana layer (item #3 deferred)**" — a user-frozen out-list.
- `central-metrics-v1/plan-overview.md:86`: "Agent tool surface (item #2) — interface anticipates it (FR-G4) but **no tools ship**."
- `central-metrics-v1/requirements.md:34`: "maintenancer/developer agents (future item #2 — **no tool surface in v1**, but the interface must anticipate it)".
- `central-metrics-v1/requirements.md:292`: same statement.
- `decisions.md:645` — D6 is "binding guidance for the deferred lanes (deferred item #2 agent tools, deferred item #3 Grafana)".
- The **Grafana (item #3) layer** stays deferred: `decisions.md:145-146` ("Deferred item #3 (Grafana) remains the heavy dashboard layer; the in-app page is the developer-facing quick surface"); `requirements.md:94` (F22 exporters OUT — "no Grafana layer #3 in v1").
- `decisions.md:472-473` (F11/F12) and `:482` (F21) — `scripts/upgrade/` remains **never-edited** (leader-ratified decision 2).

**FR-G4 (the anticipation requirement)** — `requirements.md:34, :292`: the interface must anticipate the tool surface. The shipped anticipation: `buckets` on the wire (`:132-135`), the `chart_role` per-name member (`:136-141`), the label-aware `?series=` selector whose serialization is shared with "any future item-#2 watch-tool reference" (`:116-118`).

---

## 5. INVENTORIES

### 5.1 The metrics-collection catalog (authoring truth)

`.agents/shared/planning/metrics-collection/` — 4 files: `architecture-recommendation.md` (697 L), `decisions.md` (845 L), `metric-catalog.md` (2010 L), `plan-overview.md`.

`metric-catalog.md` §1 Summary table (`:211-241`) — the **27 catalogued families** (F0.1–F0.4, F1–F22, F7b, F7c) with canonical `ensemble_*` names, type, phase, source (reuse/greenfield) and series estimates. Catalog conventions §0 (`:34-205`) include the **render-time allowlist predicate** (per-family label-name allowlist + per-label value allowlist, `:137`) and the **owner roles** (house roles) at `:198`: `developer` (code) · `devops` (infra/alerting) · …

Reconciled-counts ledger §14 (`:1788-2010`) is the single source of truth for series counts (B-13/B-06/B-08/B-09); §14.4 vocabulary sizes at `:1913`; §14.6 "Cross-doc alignment instruction (M-2 — the ledger is referenced, not copied)" at `:1999`.

### 5.2 The FAMILY CATALOG reality — 16 registered, 26 catalogued, 10 live / 6 dark

**Machine-readable authority** — `daemon/services/metrics_query/wiring/adjudication.py`:
- `CATALOG_FAMILY_VERDICTS: dict[str, tuple[str, str]]` (`:51-78`) — family id → `(verdict, seam anchor / rationale)`.
- `VALID_VERDICTS = ("IN", "OUT", "PARTIAL", "ALREADY-LIVE")` (`:81`).
- **`REGISTERED_FAMILIES` (the IN + PARTIAL set) — 16 ids** (`:84-101`):
  `F0.3, F1, F3, F4, F5, F6, F7, F8, F9, F10, F11, F12, F13, F17, F18, F21`
- `OUT_METRIC_NAMES` (the metrics that must be ABSENT from the registry and the `/metrics` body) — `:105-141`.
- `FAMILY_SERIES_BANDS: dict[str, int]` — the per-family CLOSED label-space ceiling (`:151-168`): F0.3=8, F1=9, F3=6, F4=14, F5=4, F6=15, F7=20, F8=4, F9=53, F10=6, F11=16, F12=117, F13=19, F17=40, F18=5, F21=32.
- `BASELINE_SERIES = 89` (`:171`) · `CATALOG_LIVE_ESTIMATE_SERIES_PROJECTION = 363` (`:206`) · `GLOBAL_SERIES_CAP = 2000` (`:209`).
- The module docstring's verdict table ends: "**10 full + 6 partial = 16 of 26** — the plan's binding subset" (`:45`).

**Live vs dark (measured, observation-dependent)** — `docs/architecture/metrics-promote-disclosure.md:53-57`:
> "**the MEASURED LIVE counts** — from the fenced-dev observation (`evidence/c1-runtime-observation.md`): **10 of the 16** families **live**, **6 dark**, **99 live series** across the lane families, **112 snapshot series**, `/series` **104 series / 335 points**. The live count is **observation-dependent and NOT statically derivable**."

**The six dark families, each with a NAMED cause** — `:69-80`:
- **F1**, **F3** — `capture_off`, because the provider's `capture_active` is **never wired** (`daemon/api.py:2528-2533` constructs the provider without it);
- **F6** — `census_gated`, and that reason is **INVERTED** (the census published 15 live series while the door reported dark);
- **F7** — `series_absent`, because the non-internal histogram never publishes (`daemon/checkpoint_metrics.py:830-874`);
- **F18** — `series_absent`, the MCPS-gateway store is empty in dev;
- **F21** — `series_absent`, the journal family needs an install dir and **dev has none by design**.

**PRODUCTION DEFECT — `dark` while publishing live series** — `:82-98`:
> "**THREE OF THE SIXTEEN FAMILIES REPORT `dark` WHILE PUBLISHING LIVE SERIES — AND THAT IS A PRODUCTION DEFECT, NOT A DEV ARTEFACT.** **F1** (9 live series …), **F3** … and **F6** (15 live series) all report `dark` while their series are LIVE. The causes are the two out-of-lane defects routed as **R-i/R-ii**: the provider's `capture_active` is **never wired** (`daemon/api.py:2528-2533`), so the `capture_off` reason is unconditional; and the census's public `gated` property is **INVERTED** (`daemon/services/metrics_census.py:323-335` returns "enabled", the door reads it as "gated off"), so the `census_gated` reason fires exactly when the census is running and publishing. … **a reader of the `/families` body must know that `dark` + live series is possible**, and for these three it is the NORM."

**⇒ A tool MUST NOT treat `availability.state == "dark"` as "no data"** — it must consult `live_series_count` and the actual series rows.

**F12's documented-dark lanes** — `:100-110`: the `stale_pause` sweep lane is dark for TWO independent reasons (no `counters()` reader **and** never assigned to `app.state` — `daemon/api.py:3058-3074` calls `sweep_stale_pauses(...)` inline). The observed F12 lanes were `['eligible_pending', 'orphan_watcher', 'watch_reconcile']`.

**Other OUT-list detail** — `docs/architecture/metrics-query-api.md:296-330` §6 "The OUT-list — what the door deliberately does not serve" (every excluded metric with its **named greenfield seam**); F2's verbatim flip condition at `:307`:
> *"if the ops surface becomes Linux-primary or the operator confirms near-term per-thread CPU need, re-admit (+22, one pack)"*

### 5.3 THE AGENT-TOOL ALLOW-LIST CENSUS and PER-AGENT VERDICTS

Source: `.agents/shared/planning/central-metrics-v1/inventory-vendor/metrics-inventory-surfaces-fe-deps.md` (vendored copy of the untracked `metrics-unification/` report; provenance header at `:1-7`).

**THE NEGATIVE FINDING (load-bearing)** — `:101`:
> "`git grep 'readiness|loop_lag|pool_state|leak_signal|grace_active|resume_hold' daemon/tools/` → **ZERO hits**. No agent tool exposes readiness components, grace, leak signal, pool state, resume-hold, loop lag, or resource-monitor samples. Those live exclusively on HTTP (`/readyz`, `/api/resources`, `/api/metrics`) + the loopback Prometheus listener. Agents *can* `bash`-curl loopback endpoints, but that is outside any tool contract."

**THE TWO GAPS** — `:175-178`:
> "1. **Two disconnected planes.** The daemon's self-observability (readiness components, grace, leak signal, pool state, resume-hold, loop lag, resource samples, idle-in-txn census, Prometheus registry) flows ONLY to HTTP/loopback/log surfaces that **no agent tool wraps** (zero grep hits in `daemon/tools/`). … 
> 2. **Target-agent asymmetry.** devops (most metrics-hungry) has zero native metrics tools — bash-curl workaround only; wanderer has no fleet census; maintenancer alone holds census/pool/logs. Queue depths exist ONLY inside `fleet_capacity_summary` (maintenancer-exclusive)."

**PER-AGENT VERDICTS (§S3.3, "TODAY, at 62627265f")** — `:103-108`:

- **maintenancer** — allow (`meta.json:12-22`): `system-log, ens-db, knowledge, system_upgrade, system_restart, db, instance_read, instance_ops, wedge-capture`. **"Richest of the three":** sole holder of fleet census + 7-detector wedge report + queue/worker-pool/DLQ capacity summary (`instance_read`), daemon-DB SELECT + table stats + **the only DB-pool metrics** (`ens-db`), wedge captures, daemon logs. **"Lacks: the entire HTTP probe plane"** — no tool wraps `/readyz` components, `grace_active`, `leak_signal`, `pool_state`, `resume_hold`, loop lag, resource-monitor samples, or the Prometheus registry (loopback-only listener).
- **devops** — allow (`meta.json:12`): `bash, proc, filesystem, time, self, help, image, knowledge, mcp, context, shared_meta_kv, db, infra, project, dynamic-skill, question, midflight, service`. **"Zero native metrics surfaces"** — no `instance_read`, no `ens-db`, no `system-log`, no `wedge-capture`. Only generic carriers: raw `bash` (can curl `/livez`/`/readyz`/`/api/resources` or psql — "powerful but uncontracted"), `db_postgres_dml_select` (external conns only), `service_status/list` pid/uptime. **"The most metrics-hungry agent (host/daemon ops) has the thinnest sanctioned read surface."**
- **wanderer** — allow (`meta.json:12`): `bash, proc, filesystem, time, self, help, explore, mcp, context, shared_meta_kv, rag, instance, blueprint, db, infra, service`. Investigator posture: `instance` category gives deep **single-instance** reads; `db` external SELECT; bash/service carriers. **"No fleet census"** (`instance_read` is maintenancer-gated at `instance_read.py:128`), no daemon logs, no wedge captures, no pool status. "Can describe one instance deeply; **cannot natively answer 'how sick is the fleet'**."
- **Contrast:** ari holds wedge-capture + job/job_progress + view-views; leader holds wedge-capture + view-views + job_pause/resume — **neither has `instance_read`**.

**Per-tool agent allow-lists already in the tool plane** (`:82-99`):
| Tool(s) | Category | Agent allow-list |
|---|---|---|
| `instance_list` / `instance_get` / `instance_health` / `fleet_capacity_summary` | `instance_read` | **maintenancer only** — `INSTANCE_READ_AUTHORIZED_AGENTS = {"maintenancer"}` (`instance_read.py:128`) |
| `instance_terminate/reclaim/nudge/mark/answer/dismiss` | `instance_ops` | **maintenancer only** — `INSTANCE_OPS_AUTHORIZED_AGENTS` (`instance_ops.py:167`), kill-switch `ENSEMBLE_INSTANCE_OPS_ENABLED` default ON |
| `wedge_capture_list` / `wedge_capture_get` | `wedge-capture` | **{ari, leader, maintenancer}** — `WEDGE_CAPTURE_AUTHORIZED_AGENTS` (`wedge_capture.py:84`) |
| `db_conn_*`, `db_postgres_dml_select` | `db` | maintenancer, devops, wanderer, tester |
| `ens_db_postgres_select` / `ens_db_inspect` / `ens_db_repair_execute` | `ens-db` | **maintenancer only** |
| `ens_db_pool_status` | `ens-db` | maintenancer only — "**the only agent-facing DB-pool metrics**" (`ens_db_tools.py:1184`) |
| `ens_system_log_list/read/search/tail` | `system-log` | maintenancer + worker |
| `system_health` | `system` | gaia only — "**STATIC** config masquerading as health" (naming hazard, `:186`) |
| `proc_*`, `service_*` | `proc`, `service` | devops, wanderer, worker, tester, coder, giter, designer, planner, reviewer |
| `skill_feedback` | `dynamic-skill` | 20 agents incl. all three targets |

**The same verdict, restated in the central-metrics-v1 research digest** — `.agents/shared/planning/central-metrics-v1/research-findings.md:185`:
> "Agent tools: maintenancer richest (fleet census, wedge report, ens-db, system-log, wedge-capture); **devops most metrics-hungry but ZERO native metrics surfaces (bash-curl only)**; wanderer no fleet census; ari/leader hold wedge-capture + view-views not instance_read (INV-S §S3.3 :97-100). Queue depths exist ONLY inside fleet_capacity_summary (maintenancer-exclusive, :170)."

**⇒ The census's verdict for THIS lane:** the highest-value, lowest-risk consumer set is **devops** (zero native metrics surface, highest hunger) and **wanderer** (no fleet census, cannot answer fleet-level health); **maintenancer** already has overlapping surfaces (fleet census, pool status, logs) so a metrics tool is additive but partially redundant. ari/leader hold only wedge-capture — the census does not propose them as metrics consumers. The lane must decide its own allow-list; the inventory gives no ruling, only the asymmetry.

### 5.4 The producer-seam inventory (what a consumer needs to know about origin + freshness)

`.agents/shared/planning/central-metrics-v1/inventory-vendor/metrics-inventory-backend-producers.md` (vendored; the header `:11` records that the original was written by a read-only wanderer and is a message, not a file).

Producer facts relevant to a consumer (from the sibling surfaces doc §S3.4, `:110-121`, and §S4 `:175-188`):
- **Readiness gates** — `daemon/services/readiness.py`: composite of 4 components (database, queue_freshness, services, checkpoint_saver `:93-98`); failure classes split grace-eligible (`probe_timeout/pool_busy/queue_stale/read_timeout`) vs never (`pool_leak/hard_component_red/…` `:63-77`); `GraceTracker` (`:754`) bounded by `READINESS_GRACE_HORIZON_S`; `leak_signal` from SQLAlchemy pool + psycopg oracle-pool sampling (`:658`, `:685`); `resume_hold` promote-txn counters (`:416`). Served via a **cached composite refresh loop** (`api.py:4340`, `:4416-4450`) ⇒ the readiness family is refreshed by that loop, not by the metrics sampler.
- **Soak gate** — `scripts/upgrade/lib.sh` `gate_soak` `:6046`; GREEN/HELD/RESET counters `:6238-6293`; 3 consecutive greens pass `:241`; parses the `/readyz` body ⇒ **shell-coupled to the readiness payload shape** (so a tool that reshapes that payload is dangerous).
- **metrics_census** — `daemon/services/metrics_census.py`: PG idle-in-txn backend census by `application_name` (one SQL round-trip `:130-172`; self-excluding; 5 s statement_timeout); cadence `limits.metrics_census_interval_s` default **15 s** (floor 15 / cap 300); output lands in the daemon **LOG ONLY** (`:467`) — no table, no API. Only consumer: whoever reads logs (maintenancer via system-log tools) (`:121`).
- **Wedge detector** — `daemon/services/wedge_capture/`: triggers `sustained_cpu` (95%-core/30 s) and `sustained_loop_lag` (5.0 s/15.0 s) over trailing windows (`triggers.py:54-55, :131-166`); watchdog tick → capture (stacks, threads.csv, memory, pool, process, readiness payload `:522-532`) → filesystem dir (authority, `writer.py:38`) + `wedge_captures` row. **Post-hoc forensics only** (`:118`).
- **Producer families reach the registry through PULL ADAPTERS** run by the sampler thread (stage A), each adapter reading an existing producer's already-computed value and writing it into the registry; a broken adapter is logged and the tick continues (`sampler.py:4-12, :198-209`).
- **Duplication guards already ruled:** skill metrics excluded from `/metrics` re-surfacing (plan m5 — double-count); snapshots alias deprecated with RFC-8594 headers — "the central system must respect both" (`:182`).
- **Naming scatter** (`:179`) — the tool layer MUST use the door's vocabulary, not the pre-existing scattered ones: global Prom-text `/api/metrics`; module REST `/api/<noun>/metrics`; family REST `/api/resources/*`; nested `/readyz` `detail.*`; colon-namespaced counter keys (`capture:agent:<id>`); tool-side naming (`ens_db_pool_status`). Prometheus families use `ensemble_*`.

---

## 6. REFRESH / FRESHNESS — how a consumer states staleness

### 6.1 The sampler's tick

- **The sampler thread:** `MetricsSampler` (`daemon/services/metrics_query/sampler.py:65-259`), a **daemon thread** named `metrics-trend-sampler` (`:116-125`).
- **Cadence:** the reused managed key `limits.metrics_sampler_interval_s` (default **10 s**, boot-applied), read **ONCE at construction** — "a boot-applied key that is read live would produce two cadences in one process" (`sampler.py:14-18`; the construction site `daemon/api.py:2510-2512`, `:2534-2537`).
- **Effective spacing:** `max(sampler, publisher)` — the publisher cadence default is **15 s** (`daemon/checkpoint_metrics.py:128` `DEFAULT_PUBLISHER_CADENCE_SECONDS = 15.0`; `publisher_cadence()` at `:178`). So the OBSERVED effective spacing is **15 s** at defaults (`contract:196-197`; `provider.effective_interval_s`, `provider.py:124-131`).
- **One tick = stage A + stage B** (`sampler.py:3-12`): stage A runs the PULL ADAPTERS (each isolated; a raising adapter is logged and the tick continues, `:198-209`); stage B reads the published snapshot, **identity-dedupes** it against the previous tick per metric, and appends the CHANGED series to the rings (`:211-256`).
- **Fail-safe posture:** "an adapter error, a registry error or a ring error is logged and swallowed. The thread NEVER propagates into the daemon loop and NEVER dies." (`:22-24`)
- **GC:** a metric with no published entry for `IDLE_GC_SAMPLINGS = 3` consecutive ticks is garbage-collected — but "a metric that HAS a published entry never reaches the GC threshold: an idle-but-live family is never dropped" (`sampler.py:224-230`; `constants.py:94-98`).

### 6.2 The ring buffer's retention / window semantics

`daemon/services/metrics_query/ring.py`:
- **Window:** `WINDOW_SECONDS = 7200` (2 h) — `constants.py:33`; `TrendRingStore.window_s` property (`ring.py:371-373`). "Deliberately NOT long history — the Prometheus TSDB owns that" (`constants.py:31-32`).
- **Depth:** `depth = clamp(POINT_BUDGET // max(1, ring_series), MIN_DEPTH, window_s // effective_interval_s)` — `ring.py:72-101`; `MIN_DEPTH = 60`; `POINT_BUDGET = 750_000`; the depth **RECOMPUTES** when the registry-native series count changes (`sampler.py:216-218` calls `self._ring.set_series_count(...)`; `ring.py:418`).
- **Memory:** array-backed (`array('d')` value + `array('q')` timestamp) at **16 B/point** ⇒ **11.4 MiB** worst case (`ring.py:3-8`, `BYTES_PER_POINT = 16` at `:64`). **Floor caveat:** above 12 500 series the `MIN_DEPTH` floor binds and the footprint grows beyond 11.4 MiB (`ring.py:10-23`).
- **Eviction is by COUNT and by AGE** — "the age eviction is the structural backstop that keeps the 2 h window truthful under ANY cadence change" (`ring.py:35-39`; `evict_before(cutoff)` `ring.py:481`; called each tick at `sampler.py:254`).
- **Locking:** the store is written by the sampler thread and read by the event loop; ONE store lock + per-ring locks; lock order is always store → ring (`ring.py:339-343`; `TrendRing` docstring `:104-113`).
- **Lost on restart by design** (`contract:400-402`); a restart is visible via `restart_epoch`.

### 6.3 The timestamp fields available to a consumer

| Field | Where | Meaning |
|---|---|---|
| `generated_at_utc` | envelope header (`envelope.py:79`) | aware-UTC ISO-8601, `daemon/services/timestamps.py:63` `now_utc_iso()` — **not naive UTC** |
| `restart_epoch` | envelope header (`envelope.py:80`) | the boot id — a restart is visible, never a silent gap |
| `last_publish_age_s` | per snapshot series (`provider.py:304`) | seconds since the metric's last publish, via `Metric.last_publish_monotonic()` (`checkpoint_metrics.py:628-638`) — **explicit staleness metadata, never a silently stale value** |
| `last_sample_age_s` | `/series` envelope (`envelope.py:224`) | age of the newest ring sample |
| `stale` | `/series` envelope (`envelope.py:225`) | `age > STALE_INTERVAL_MULTIPLIER (3.0) × interval` |
| `window.from_utc` / `window.to_utc` | `/series` envelope (`envelope.py:219-220`) | the evaluated window |
| `window.interval_s` + `interval_s_source: "observed"` | `/series` envelope (`envelope.py:221-222`) | the OBSERVED effective spacing, never the configured cadence |
| `window_s` anchor | contract `:200` | anchors at the server's `generated_at_utc`; **a cursor pins its evaluation instant** |
| provider `last_sample_timestamp()` / `oldest_sample_timestamp()` | `provider.py:406, :412` | the newest / oldest ring sample epoch second; `oldest_sample_timestamp()` is **the cursor's EXPIRY probe** |

### 6.4 How a tool should state freshness to an LLM operator

The contract's own doctrine is **staleness as explicit metadata, never a silently frozen line** (`contract:198-199`) and **availability as the reason, not a fabricated zero** (`availability.py:3-5`). Concretely, a tool result should carry:
1. the **envelope header verbatim** (`schema_version`, `vocabulary_version`, `generated_at_utc`, `restart_epoch`, `activation`);
2. per-series `last_publish_age_s` (snapshot) and the top-level `last_sample_age_s` + `stale` (series);
3. `availability.state` + `availability.reason` per family — **plus the caveat that `dark` + live series is possible for F1/F3/F6** (`metrics-promote-disclosure.md:82-98`);
4. `truncated: bool` + the continuation cursor whenever the body was bounded (contract `:283-286`);
5. `window.interval_s` (observed) so the operator can reason about resolution.

---

## 7. FROZEN vs. NEGOTIABLE — the planner's cheat-sheet

### FROZEN (the tool layer must adapt, not change)
- The envelope shape and the three route semantics — `metrics-query-contract.md:81-228`.
- The family entry shape — `:128-130`; the per-name member set — `:129`.
- Series identity = `(name, labels)` with the registry's `label_key` serialization — `:113-120`.
- `cardinality_class ∈ {closed, open, none}` and the "open ⇒ `labels: null`, never value-enumerated" rule — `:150-153`, `:310-311`.
- The closed availability reason set and the `census_not_running`-before-`census_gated` ORDER — `:350-367` / `availability.py:76-92`.
- The 5 kill-switch truth-table invariants — `:326-348`.
- The error vocabulary and the `SCOPE_INSUFFICIENT` spelling — `:233-258`.
- The bounds: `MAX_NAMES=50`, `MAX_POINTS_CEILING=240`, `MAX_SERIES=500`, `MAX_TOTAL_POINTS=12_000`, `MAX_SNAPSHOT_SERIES=500`, `MAX_FAMILIES=500`, `RESPONSE_BYTE_CEILING=1 MiB` — `constants.py`.
- The `schema_version` / `vocabulary_version` two-axis policy and the AM-7 bump rules — `:83-90`, `:340-351`.
- `buckets` on the wire; `chart_role` ∈ {line, step} — `:132-141`.
- The window/staleness semantics — `:196-203`, `:305-308`.
- The read-path purity rules (no DB, no switch read, no metric lock, no mutation) — `:215-223`, `provider.py:3-12`.
- The 16 registered families and the OUT metric names — `wiring/adjudication.py:84-141`.

### NEGOTIABLE / THIS LANE'S CALL
- **The tool surface itself** (names, parameters, return shape, count) — deferred item #2, no prior design exists.
- **The agent allow-list** — the inventory gives the asymmetry, not a ruling (§5.3).
- **Whether to mint `metrics:read`** — the D6 trigger condition is arguably met by an agent-tool lane, but the D6 retrofit map is written for the HTTP door; the tool bypasses the scope system in-process (§3.3, §3.4).
- **Whether the tool consumes the PROVIDER directly (in-process) or the HTTP door (loopback + auth)** — the architecture decision says "the SAME provider functions directly" (`decisions.md:375-377`), but that loses the auth posture.
- **How the tool reaches `app.state.metrics_query`** — there is no factory today (§2.3).
- **Whether the tool offloads the body build** — the REST layer does (`db_offload.offload`, `metrics.py:441-451`); an in-process tool run inside the agent's tool execution is already off the event loop in most cases, but the tool's call site matters.
- **The freshness/verbosity presentation** to the LLM operator (§6.4) — no prior design.

### KNOWN DEFECTS THE TOOL MUST TOLERATE (do not "fix" silently)
- **R-i / R-ii** — `capture_active` never wired (`daemon/api.py:2528-2533`) and the census `gated` polarity inverted (`daemon/services/metrics_census.py:323-335`) ⇒ **`dark` can coexist with live series** for F1/F3/F6 (`metrics-promote-disclosure.md:82-98`).
- **F12** — `stale_pause`/`job_lock`/`upgrade_journal` sweep lanes render no series (no `counters()` reader / no `app.state` publication) — `:100-110`.
- The `F7` non-internal histogram never publishes ⇒ F7 is `series_absent` (`:73-74`).
- `F21` needs an install dir — dark on any host without one (`:75-76`).
- `F18` — the MCPS-gateway store is empty in dev (`:74-75`).

---

## 8. FILE INDEX (all paths relative to the worktree root)

**Frozen contract + published docs**
- `docs/architecture/metrics-query-contract.md` (818) — the FROZEN contract.
- `docs/architecture/metrics-query-api.md` (380) — the published API page (§5 scope disclosure `:268-292`; §6 OUT-list `:296-330`).
- `docs/architecture/metrics-promote-disclosure.md` (216) — the live/dark measurement + the 8 cross-cutting disclosure items.
- `docs/architecture/metrics-fe-charts.md` — the FE charts page.

**Provider seam**
- `daemon/services/metrics_query/__init__.py` (127) · `protocol.py` (55) · `provider.py` (425) · `envelope.py` (309) · `ring.py` (589) · `sampler.py` (259) · `selectors.py` (84) · `availability.py` (107) · `constants.py` (98) · `roster.py` (264) · `downsample.py`.
- `daemon/services/metrics_query/wiring/` — `__init__.py`, `registry.py`, `adjudication.py`, `vocab.py`, `db.py`, `sources.py`, `accumulate.py`, `w1.py`, `w2.py`, `w3.py`, `ws_a.py`.

**Registry + router + auth + wiring site**
- `daemon/checkpoint_metrics.py` (1824) — `VOCABULARY_VERSION` `:208`, `SOURCE_IDIOMS` `:225`, `DURABILITIES` `:232`, `CHART_ROLES` `:237`, `FAMILY_ROLE_*` `:240-241`, `registered_metrics()` `:437`, `label_key()` `:485`, `Metric.__init__` `:563`, `last_publish_monotonic()` `:628`, `effective_chart_role()` `:640`, `is_chart_primary()` `:652`, `published_snapshot()` `:1057`.
- `daemon/routers/metrics.py` (780) — the whole REST door.
- `daemon/auth/scopes.py` (150) · `daemon/auth/decorators.py` (208).
- `daemon/api.py:2502-2547` — the provider/sampler wiring.

**Planning**
- `.agents/shared/planning/central-metrics-v1/` — `decisions.md` (682), `plan-overview.md`, `fold-ledger.md`, `research-findings.md`, `requirements.md`, `architecture-recommendation.md`, `architecture-recommendation-v2.md`, `phase3-plan.md`, `phase6-plan.md`, `evidence-pack.md`, `inventory-vendor/{PROVENANCE,README,metrics-inventory-backend-producers,metrics-inventory-surfaces-fe-deps}.md`.
- `.agents/shared/planning/metrics-collection/` — `metric-catalog.md` (2010), `decisions.md` (845), `architecture-recommendation.md` (697), `plan-overview.md`.

**Tests (the gate surface a lane must satisfy)**
- `tests/unit/metrics/` — `test_metrics_query_provider.py`, `test_metrics_query_routes.py`, `test_metrics_query_auth_matrix.py`, `test_metrics_query_pagination.py`, `test_metrics_query_switch_invariance.py`, `test_metrics_query_api_wiring.py`, `test_metrics_query_scope_cage.py`, `test_metrics_history_ring.py`, `test_metrics_sampler.py`, `test_metrics_registry_narrowing.py`, `test_metrics_vocabulary_amendment.py`, `test_metrics_family_w1/w2/w3.py`, `test_central_metrics_contract_doc.py`.
- `tests/unit/test_router_scope_manifest.py` (`:169` the family scope pin) · `tests/integration/test_router_class_manifest.py` (`:147` the class pin) · `tests/unit/routers/test_metrics_route.py` · `tests/unit/services/test_metrics_api_wiring.py`.
