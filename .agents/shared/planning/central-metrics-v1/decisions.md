# Decisions Record — central-metrics-v1 (query API, history buffer, FE page, extension subset)

Date: 2026-11-02 (UTC)
Author: technical-analysis worker (commission: central-metrics-v1 architecture analysis)
Status: Draft — feeds planner → architect → plan council → fresh approver
**AMENDMENT FOLD (2026-11-02):** architect verdict **SOUND-WITH-AMENDMENTS**; AM-1..AM-27 folded
into this record per `architecture-recommendation-v2.md` §4 (the fold source; v1 architecture file is
retained unmodified for diff provenance). Leader rulings LR-1..LR-3 folded verbatim. Amendment ids
are cited inline as `(AM-n)`.
Authority basis: research-findings.md (wave-0, three explorer reports) + the COMMITTED
metrics-collection register (`.agents/shared/planning/metrics-collection/decisions.md` §ACTIVE,
`metric-catalog.md` §1/§8/§14) + worktree spot-verification (branch
`feature/central-metrics-v1`, base == origin/latest).

## How to read this record

- Every ruling carries **Evidence / Options / Ruling / Promote-time-disclosure**.
- This record does NOT amend the metrics-collection register (`metrics-collection/decisions.md`
  §ACTIVE). Where a ruling touches catalog-owned facts (names, vocabularies, series counts) it
  POINTS at the catalog (M-2 pointer discipline) and flags the amendment a plan must make.
- Anchors were spot-verified in this worktree; line numbers are as-of research base.
- Scope discipline: this lane ships the v1 central-metrics system (backend query surface +
  FE page + extension-subset family wiring). **NO live promote anywhere in this lane** — the
  user's 3-factor nonce ceremony owns promotes.

---

## D1 — Activation posture

### Evidence

- Two-class rule, declared in-code: "ON for zero-user-action read/serve surfaces, OFF for
  agent-authored mutation lanes" (`daemon/config.py:4413-4419`, the ProjectSkillSourceConfig
  rationale block). **DEMOTED to SUPPORTING PRECEDENT (AM-19):** the rule is scoped to another
  feature's boot-time knobs, and D1(c) rejects the very pattern it prescribes. The LOAD-BEARING
  basis for this ruling is (i) **intra-family consistency** — the shipped `/api/metrics` text route
  is auth-only, so gating three siblings under the same prefix would create a same-prefix
  split-brain — plus (ii) the **verified blast-radius census** (exactly two behavioural gate reads:
  `metrics_listener.py:449` body; `metrics_census.py:447` census tick).
- `ENSEMBLE_METRICS_ENABLED` gates ONLY: the listener response body
  (`daemon/services/metrics_listener.py:408-419`, OFF ⇒ 200 valid-empty incl. no
  `ensemble_metrics_up`) and the census sampler's DB work (`daemon/services/metrics_census.py:335-340`).
  Research finding 1d is explicit: "/api/metrics route, registry observe paths,
  read-instrumentation seam do NOT consult it" — the complete check-site list is those two.
- `/api/metrics` is auth-gated, not switch-gated: `daemon/routers/metrics.py:70`
  `_SCOPE_READ = "maintenance:read"`, `:113 dependencies=[scope_required(_SCOPE_READ)]`,
  `_require_auth_on` 503 `AUTH_NOT_CONFIGURED` fail-closed BEFORE the registry read (:91-107).
- Registry accumulation is UNGATED: writers flip dirty flags and increment regardless of the
  switch (`checkpoint_metrics.py` observe paths have no gate). Switch-OFF therefore does NOT
  stop data collection — it blanks only the unauthenticated body and the census DB work.
- The listener is an UNAUTHENTICATED exposure surface: loopback-only enforced in-constructor
  (`metrics_listener.py:361-368`, no escape hatch) but no auth — this is the recorded reason
  `limits.metrics_enabled` defaults False (`daemon/config.py:785-793`; CR-2/MA-1 lineage).
- Managed-key live flip needs no restart: per-request env read + DB-managed tier within 5 s TTL
  (`metrics_gate.py:131` `MANAGED_REFRESH_TTL_S=5.0`; `config.py:785-793` "read per request —
  no restart needed").
- FE charts page value depends on the query API being reachable for authenticated sessions.

### Options

- **Option A — uniform switch-gating**: the new query API also consults
  `ENSEMBLE_METRICS_ENABLED`; default the listener+API OFF, deployment enables.
- **Option B — surface-class posture (recommended)**: listener stays switch-gated default-OFF
  (unauthenticated class); `/api/metrics` and the new query API stay auth/scope-gated only
  (authenticated read/serve class); NO config default changes; deployment enables the listener
  via managed key when it wants Prometheus scraping.
- **Option C — flip the listener default ON** in this lane (`limits.metrics_enabled=True`),
  arguing the two-class rule's ON class now covers it.

### Ruling

**Option B.**

(a) **Per-surface posture**:
- Listener body: REMAINS switch-gated, default-OFF. It is the one unauthenticated surface; its
  default-OFF is the recorded CR-2/MA-1 exposure decision, pinned by `test_metrics_managed_keys.py`.
- `/api/metrics` (Prometheus text): unchanged — auth-only, no switch consultation (shipped
  precedent, `metrics.py:70/:113`).
- New query API routes: auth/scope-gated ONLY. They join the authenticated read/serve class.

(b) **The query API does NOT consult `ENSEMBLE_METRICS_ENABLED`** — consistency with the
`/api/metrics` auth-only precedent wins. Three reasons:
1. The switch's documented blast radius is "the unauthenticated listener body + census DB work"
   (research 1d check-site census). Extending it to an authenticated surface changes emergency-off
   semantics: an operator flipping it OFF for exposure reasons would also blind authenticated
   dashboards — conflating exposure control with observability availability.
2. Gating the query API would hide data that is STILL being accumulated (observe path ungated) —
   pure downside, no protective value: the surface it would "protect" is already behind auth.
3. The FE page would be dead-by-default even where auth is healthy, for no risk reduction.

(c) **What ships as the default in `config.py`: NOTHING changes.** No new config field ships;
`limits.metrics_enabled` keeps `default=False` (`config.py:785-793`); the seven managed keys'
**defaults, bounds, types and restart columns are untouched** (the pinned
`test_metrics_managed_keys.py` corpus stays green) — **one managed key
(`limits.metrics_sampler_interval_s`) receives a description-only amendment** because this lane
consumes it (AM-15; see D3). The FE page is useful
immediately because the query API is auth-gated-and-always-on, and the registry accumulates
regardless. Enabling the listener for Grafana/Prometheus remains a live managed-key flip
(≤5 s TTL, no restart) — zero-risk operator action, no code default flip needed. A default-ON
kill-switch for the query API itself (the project-skill-source pattern, `config.py:4436-4446`)
is REJECTED: that precedent ships a switch because the loader changes search BEHAVIOR; the query
API is pure additive read routes whose access control IS the scope system — a switch would add a
truth-table row and pin surface for near-zero value.

(d) **Promote-time disclosure** — see below.

### Promote-time disclosure (exact text)

> central-metrics-v1 ships the metrics query API (`GET /api/metrics/families`,
> `GET /api/metrics/snapshot`, `GET /api/metrics/series`) AUTH-GATED ONLY — scope
> `maintenance:read`, 503 `AUTH_NOT_CONFIGURED` fail-closed before any registry read — and
> deliberately does NOT consult `ENSEMBLE_METRICS_ENABLED`, consistent with the shipped
> `/api/metrics` text-exposition route. The unauthenticated :9798 exposition listener REMAINS
> default-OFF (`limits.metrics_enabled=False` unchanged) — **at the BODY level: the socket still
> binds and serves a valid empty 200 while OFF** (AM-19(a)); enabling it for Prometheus scraping is
> a live managed-key flip (≤5 s TTL, no restart). **The query door has NO kill-switch of its own —
> its emergency-off lever is token revocation of the `maintenance:read` grant** (AM-19(c); the
> runbook names the lever). Switch-OFF does NOT blank the query API: the
> registry accumulates ungated, so query routes serve live data in both switch states; only the
> listener body goes empty and the census sampler's DB work stops (F6 `ensemble_pg_*` families
> go dark with `availability.reason="census_gated"`). Emergency-off semantics are UNCHANGED and
> NARROWER than they appear: the switch never touches authenticated surfaces.

---

## D2 — FE charting approach

### Evidence

- NO charting library exists in the stack: direct deps are Angular 21.2.5 + Material/CDK +
  ng-zorro-antd (dark CSS only, zero nz-* elements — dead weight), CodeMirror 6, marked,
  ngx-markdown, mermaid ^11.4.0 (chat diagrams only), rxjs (research ex-fe §1;
  `frontend/package.json:27-61`).
- `d3` exists ONLY as an unpinned mermaid-transitive, never imported (lock :7318, d3-scale :7653).
- Load-bearing in-code precedent: `frontend/src/app/pages/resources/svg-timeline/svg-timeline.component.ts:17-18`
  — "Dependency-free inline SVG timeline primitive (D4 — no chart library exists and v1 adds
  none)". The pattern: fixed viewBox, exported pure scale functions "for direct unit assertions"
  (:88-114), per-series min/max normalization, native `<title>` tooltips, anomaly dots → click
  emits a window; zoom/brush/gradients explicitly OUT (:20-33).
- Charts needed: time-series lines/areas + stat cards + tooltips, dark theme (Material M3 dark,
  violet/cyan palette `styles.scss:1-19`).
- Build budgets: initial 1MB warn / 6MB error (`angular.json:64-75`); anyComponentStyle 8kB warn /
  24kB error. No repo-level AOT gate — template-touching commits need one
  `ng build --configuration development` pass (research ex-fe §6).
- Deferred item #3 (Grafana) remains the heavy dashboard layer; the in-app page is the
  developer-facing quick surface.

### Options

- **Option A (recommended) — generalize the hand-rolled inline-SVG primitive family**: extract
  the svg-timeline patterns into a shared chart primitive component (e.g.
  `components/charts/svg-line-chart/`) parametrized over series defs + window; pure scale/axis
  functions exported for unit specs; zero new dependencies.
- **Option B — add a first-ever charting dependency**: candidates: chart.js ^4 (~60-70 KB gzip
  tree-shaken, via ng2-charts or direct), echarts + ngx-echarts (~180-300 KB gzip),
  plotly.js (~1 MB+, would dominate any chunk), d3 v7 as a NEW direct pin (~50-130 KB core +
  hand-built axes/scales on top).
- **Option C — import d3 as-is from the mermaid transitive**: rejected on sight (see below).

### Ruling

**Option A — zero-dependency SVG primitive generalization.** Justification, point by point:

1. **A written in-code architectural ruling already covers this decision** — and this analysis is
   the sanctioned place to revisit it. Revisited conclusion: the ruling still holds, because the
   requirements have not grown past what the primitive family provides (lines/areas/stat
   cards/tooltips/dark theme — exactly svg-timeline's shipped shape).
2. **The d3 transitive is unusable as-is** (Option C): importing an unpinned transitive couples
   the metrics page to mermaid's dependency tree; any mermaid bump could break chart rendering
   with no lock-level contract. Pinning d3 fresh (Option B variant) buys a low-level toolkit we
   would use to rebuild the scale functions we already have as pure, unit-tested exports.
3. **Bundle economics**: a lazy page chunk carrying chart.js/echarts adds 60-300 KB per page
   visit for zero functional gain at our scale (a bounded set of series × a bounded window,
   1-axis-per-series normalization). The ng-zorro dead-weight precedent shows this stack already
   pays for libs it barely uses; do not add another.
4. **Testability**: the house pattern exports pure scale functions for direct unit assertions
   (svg-timeline:88-114); chart libs would push tests toward TestBed/canvas mocks — the opposite
   of the house logic-mirror spec style.
5. **The Grafana deferral bounds the need**: stock Grafana remains the heavy dashboard layer
   (deferred item #3). The in-app page answers "is the daemon healthy, what's trending" — not
   "build me a custom dashboard". That bounds charting sophistication to exactly the primitive
   family's shape. Feature-freeze the primitive v1: lines + **steps** + areas + per-series normalization +
   native `<title>` tooltips + UTC axis; zoom/brush/crosshair/gradients OUT (same closure
   svg-timeline declared). **Step rendering is IN the freeze (approver B5 + leader ruling): counters
   render STEP and gauges render LINEAR — a linear interpolation between counter samples would assert values
   that were never observed (data honesty); the ring stores step-function samples.
6. **Costs acknowledged**: hand-rolled charts need axis-tick and windowing discipline; mitigated
   by (i) ONE shared primitive (never per-page copies), (ii) pure functions unit-tested, (iii)
   feature-frozen scope, (iv) the mandatory AOT dev-build leg after template work.

Stat cards need no charting at all (Material cards + signals — the resources Live-tab pattern).

### Promote-time disclosure

> The FE Metrics page adds ZERO new npm dependencies. Charts are rendered by a generalized
> dependency-free inline-SVG primitive extracted from the svg-timeline precedent
> (svg-timeline.component.ts:17-18 in-code ruling reaffirmed). Consequence: chart interaction is
> bounded to the primitive v1 shape (no zoom/brush/crosshair); rich dashboarding remains
> Grafana's job (deferred item #3). If a future commission needs interaction beyond the
> primitive, that is a NEW dependency decision with its own justification — not a silent add.

---

## D3 — Storage/retention for the query API

### Evidence

- Registry retention: NONE — process-lifetime cumulative, no TTL/eviction/windowing anywhere
  (`checkpoint_metrics.py`; research 1a: "Retention/rollover/windowing: NONE"). The only bounded
  structure in the subsystem is the slow-saver ledger `deque(maxlen=128)` (`checkpoint_perf.py:158-164`).
- Commission preference: registry in-memory + pull seams over new tables; `resource_monitor` is
  already the durable time-series tier (leader-ratified decision 4 — Prometheus authoritative for
  alerting, resource_monitor PG tier for per-instance API views; `metrics-collection/decisions.md`
  §D row 4).
- House fact: every new SQLModel table requires its own `daemon/migrations/versions/*.sql`
  (blueprint: Migration Runner & Schema Evolution; 109+ files, zero exceptions).
- Copy-on-publish cadence: writers flip O(1) dirty flags; O(series) snapshot at most once per
  cadence window (`checkpoint_metrics.py:23-28, :868-882`; default 15.0 s :128); readers can take
  `published_snapshot` lock-free.
- Managed key `limits.metrics_sampler_interval_s` default 10, ge=1 le=60, boot-applied
  (`config.py:825-833`) — described as "host-side samplers" cadence; grep shows NO runtime
  consumer yet (only the settings-registry spec, `settings_service.py:545-550`).
- Durability table precedent (catalog §10.0): registry in-RAM counters are reset-on-restart;
  `rate()`-safe by design; long history belongs to the Prometheus TSDB.

### Options

- **Option A (recommended) — in-RAM ring buffer sampled by a dedicated query-layer thread** at a
  bounded cadence, reading published snapshots lock-free.
- **Option B — new PG table** (one `daemon/migrations/versions/*.sql` + SQLModel + retention
  sweep integration).
- **Option C — client-side FE buffering only** (no BE history; FE accumulates points across
  polls in-memory).

### Ruling

**Option A — in-RAM ring/window buffer owned by the query layer.** The buffer is the
quick-view tier ONLY; durable long history stays where decision 4 put it (Prometheus TSDB when
enabled; resource_monitor PG tier for the per-instance views it already owns). This preserves the
commission's registry+pull-seams preference, adds zero migrations, and keeps restart semantics
identical to the registry's own (in-RAM, reset-on-restart — an absence the envelope states
honestly via `history.window` bounds, not silently).

**Sized numbers (all fixed module constants in the query-layer package, no new managed keys):**

| Parameter | Value | Rationale |
|---|---|---|
| Sample cadence (the TICK) | `limits.metrics_sampler_interval_s` (default 10 s, [1,60], boot-applied) | REUSE of the existing managed key — it is unwired today (grep: only `settings_service.py:545-550`); its restart column (pinned by `test_metrics_managed_keys.py`) matches a lifespan-started thread that reads config once. Description string amended to name **BOTH** consumers (host-side samplers + the in-RAM trend history sampler); disclosed (AM-15). |
| Effective point interval | **`max(sampler_cadence, publisher_cadence)` = 15 s at defaults** (observed 15–25 s) | AM-15: `window.interval_s` reports the **OBSERVED effective spacing**, never the configured cadence. The publisher cadence is live-adjustable and is the BINDING rate; age-eviction (AM-10) is the structural backstop. |
| Window target | 7200 s (2 h) | Enough for the FE charts tab and agent-tool reads; deliberate NOT-long-history (TSDB owns that). |
| Ring representation | **parallel preallocated arrays** — `array('d')` values + `array('q')` timestamps, head/tail ring index, O(1) drop-oldest — **16 B/point** | AM-9: the `deque(maxlen)` form was **REJECTED on measured evidence** (129–161 B/pt on-host ⇒ 92.6–115.0 MiB at 750 k points = **3.6× over FR-C4's Must-tier 32 MiB ceiling**). |
| Ring depth per series | `depth = clamp(POINT_BUDGET // max(1, ring_series), MIN_DEPTH=60, window_s // effective_interval_s)` where `ring_series` = the **registry-native label-combination count** (42 today over 18 live objects — NOT the catalog-convention count) | AM-10: the count-bound divisor is registry-native (a catalog-count divisor mis-scales the ring); a 720-deep count-bound would retain 3 h, not 2 h. Depth **recomputes** when `ring_series` changes (registration events); the rule is written down. |
| Eviction | per-series depth bound **PLUS age eviction** (`ts < now − window_s`); series GC'd after 3 consecutive samplings with no published entry | AM-10 (the window stays truthful under any cadence change) + AM-13 (the GC key is "the metric still HAS a published entry" — idle-but-live families are never dropped). |
| POINT_BUDGET | 750,000 points total | **11.4 MiB** worst case at 16 B/point (array) — 2.8× under FR-C4's 32 MiB ceiling and 4.2× under the superseded 48 MB deque envelope. |
| Histogram points | `(ts f64, count i64, sum f64, buckets i64[N])` = 24+8N B (N=17 ⇒ 160 B) | Live histogram series = **3** (op_seconds, read_size_bytes, exposition_seconds — not "~7-9"); ≈1.4 MB at full depth. |
| Memory envelope | today 42 registry-native label-combinations → ≈0.3 MB; the extension delta stays under 11.4 MiB via the depth clamp; the fully-wired ceiling (≈1145 catalog-convention / 822 registry-native proxy) clamps depth further | Stated worst case; identity-dedupe below reduces it further; re-measure the registry-native proxy at family-pack time. |
| Copy-on-publish interplay | sampler appends only when the published snapshot OBJECT IDENTITY changed since the previous tick (per metric) | Publish cadence (15 s default) > sampler cadence (10 s) ⇒ ~1/3 ticks dedupe to zero cost; a wedged writer ⇒ stale published copies (stated in the envelope as staleness, never fabricated). |
| Render-never-blocks | sampler reads `published_snapshot` references ONLY — zero per-metric lock acquisition | The registry's `RENDER_TRY_ACQUIRE`/`RENDER_TOTAL_BUDGET` machinery is render-path-only; the query path never touches live locks, so lock contention is structurally impossible on this path (degradation = staleness metadata, see D4). The "no lock" pin allowlists `registered_metrics()` (the registry-level `_METRICS_LOCK`, not a metric lock) — AM-13. |
| Restart epoch | `daemon.services.bash_spawn_reaper.CURRENT_BOOT_ID` (per-process `uuid4().hex`, import-side-effect-free, already imported by api.py) | AM-14: `boot_epoch.get_boot_epoch()` is NOT used (DB-clock domain, None-able). |

Restart semantics: buffer lost on restart (same class as the registry itself, catalog §10.0);
the envelope's `window.from` makes the gap visible and `restart_epoch`
(`CURRENT_BOOT_ID`, AM-14) makes the restart itself visible — a consumer can distinguish a restart
from a gap; consumers requiring durable history use
Prometheus (item #3) or resource_monitor (decision 4). Options B and C rejected: B adds a
migration + retention-sweep coupling duplicating the resource_monitor tier's job (decision 4
split violated); C makes history page-session-scoped — useless for agent tools (#2) and lost on
every tab close.

### Promote-time disclosure

> The metrics history buffer is in-RAM ONLY: default 2 h window, sampled on a
> `limits.metrics_sampler_interval_s` tick (default 10 s, boot-applied), with the reported
> `window.interval_s` being the **observed effective spacing** (`max(sampler, publisher)` = 15 s at
> defaults), an array-backed ring capped at **11.4 MiB** worst case (16 B/point; the deque form was
> rejected at 129–161 B/point measured) with depth bounded by count AND evicted by age. It is LOST
> on daemon restart — by design, mirroring the registry's own durability class — and the envelope
> carries a `restart_epoch` so the restart is visible. Durable long-range history remains
> Prometheus TSDB (deferred item #3) and the resource_monitor PG tier (decision 4).
> `limits.metrics_sampler_interval_s` — previously an unwired managed key — is now consumed by this
> sampler (boot-applied, per its pinned restart column); its description string was amended to name
> both consumers. Three disclosures (AM-15): (i) the sampler key is boot-applied AND is the
> /proc-samplers' key — a cadence change silently changes count-bound retention; (ii) the publisher
> cadence is live-adjustable and is the BINDING rate; (iii) `interval_s` is observed, not
> configured.

---

## D4 — Query API architecture (the ONE DOOR)

### Evidence

- Existing family: `daemon/routers/metrics.py` (141 L), APIRouter prefix `/metrics` (:59),
  mounted `daemon/api.py:5278` (`api_router.include_router(metrics_router)  # /api/metrics`).
- **api_families class-cage**: `tests/integration/test_router_class_manifest.py:385`
  `assert len(api_families) == 33` — a NEW family bumps 33→34; extending the existing family
  does NOT.
- **Scope-manifest cage (additional, material)**: `tests/unit/test_router_scope_manifest.py`
  pins the family scope `"/api/metrics": "maintenance:read"` (:161-169) AND a **DECLARED-ROUTE
  CENSUS** — `test_declared_route_count_pinned` (:333-396) asserts `len(declared) == 108`, i.e. the
  count of scope-decorated routes across the app. Adding three scope-decorated routes takes the
  census **108→111**. (AM-21 corrects the earlier reading: the "metrics_router (34 unconditional)"
  string at :298-300 is a **docstring narrative**, not the pin — editing it changes nothing, so the
  earlier "route-count pin 34→37" target was **vacuous**.) The mount census stays **37** (static) and
  `api_families` stays **33**.
- Scope taxonomy: `maintenance:read` exists (`daemon/auth/scopes.py:60-101`); lighter candidates
  (`system:read`, `settings:read`) exist but no scope is metrics-specific today.
- Fail-closed auth seam: `_require_auth_on` → 503 `AUTH_NOT_CONFIGURED` BEFORE registry read
  (`metrics.py:91-107`).
- FE conventions: static URL constants as the single FE↔BE surface truth
  (`resources.service.ts:35-39`); `JSON_HEADERS = {Accept:'application/json'}` to defeat the
  auth-middleware 302 for browser calls (`mcps-gateway.service.ts:67`); models are a VERBATIM
  mirror of the frozen BE contract (`resources.models.ts:1-13`); polling constants are
  pin-tested.
- Registry introspection surface: registered metric objects carry name/kind/label_keys/
  allowed_values/buckets; `known_label_keys()` (`checkpoint_metrics.py:392-398`); render-side
  quantile/bucket helpers exist (`_format_le` :1319-1336, cumulative render :1394-1409).
- VOCABULARY_VERSION gauge + frozen-vocabulary footer already version registry semantics
  (`checkpoint_metrics.py:190, :1414-1429`).
- Query path never renders: `render_metrics()` and its try-acquire/budget machinery are
  exposition-path-only; published copies are readable lock-free.

### Options

- **Option A (recommended) — extend the EXISTING `/api/metrics` family** with JSON sub-resource
  routes; provider-based query layer.
- **Option B — new `/api` family** (e.g. `/api/metrics-query`): requires the class-manifest bump
  33→34 + new scope-manifest entry + a new mount + a new cage row.

### Ruling

**Option A — one family, three routes, one provider.** `metrics.py` gains:

| Route | Purpose | Notes |
|---|---|---|
| `GET /api/metrics/families` | family/series catalog as-served: per family, the **canonical frozen shape** (quoted token-identically from `plan-overview.md` §frozen contract; `\|` = an escaped cell separator): `{id, names:[{name, kind, labels:{key:[values]}\|null, help, buckets:[…]\|null, cardinality_class, monotonic}], live_series_count, availability:{state:"live"\|"dark", reason?}, role, source_idiom, durability, cardinality_contribution}` | Derived from RUNTIME registry introspection (registered objects + allowed_values + the AM-3 registration-time family metadata), NOT from the markdown catalog — runtime is serving truth, catalog is authoring truth. `availability` reports dark families honestly (e.g. F6 with `reason="census_gated"` under switch-OFF, non-postgres census absence). AM-3: family metadata lives **at registration** (kwargs on the registry seam) so a new family is discoverable without a second table and without editing the door. AM-2: `cardinality_class: closed\|open\|none`; `open` names render `labels: null` and are never value-enumerated. |
| `GET /api/metrics/snapshot` | current published values: `{schema_version, vocabulary_version, generated_at_utc, restart_epoch, activation, series:[{name, labels, kind, monotonic, value, last_publish_age_s}]}` (+ `?family=` / `?name_prefix=` filters) | One round trip per Overview-tab tick. Staleness is explicit metadata (published-copy age), never a silent stale value. AM-4: `kind ∈ {gauge, counter, histogram}` (no `summary` — the registry never produces it); histogram series carry `value: null` + `histogram: {count, sum, buckets}` **as stored** (not Prom-cumulative); AM-13: `last_publish_age_s` needs the new public `Metric.last_publish_monotonic()` accessor. |
| `GET /api/metrics/series` | windowed history from the ring buffer: `{schema_version, vocabulary_version, generated_at_utc, restart_epoch, window:{from_utc, to_utc, interval_s, interval_s_source:"observed"}, last_sample_age_s, stale, series:[{name, labels, kind, monotonic, points:[[t,v],…]}]}` (+ `?series=<name>{<k>=<v>,…}` label-aware selector, + `?names=` name-level ≤50, `?window_s=` clamped to the buffer window, `?max_points=` server-side **bucket-max** downsample, `?cursor=`) | One round trip per Charts-tab tick. AM-1: series identity = `(name, labels)` with the registry's `_label_key` serialization; `?names=` stays name-level and returns every label variant. AM-5: all wire timestamps are **aware-UTC** (`timestamps.now_utc_iso()`); `window_s` anchors at the server's `generated_at_utc`; `stale: bool` when `last_sample_age_s > 3 × effective interval` (a wedged sampler surfaces as staleness, never a silently frozen line). AM-6: `next_cursor` is an opaque token pinning the evaluation instant + position; an invalid/expired cursor ⇒ 400 `invalid_cursor`. AM-12: the body is **built** off the event loop (`db_offload`) — `db_offload.offload` returns a Python object (`db_offload.py:386-392`) and FastAPI still encodes it on the loop, so the claim is **BUILD-OFF-LOOP ONLY**; `max_points` defaults **≤ 240** (approver B1 + leader ruling — the default is the floor AC-A.3 / AC-C.1 require: 60 min × 4/min at the 15 s effective spacing), and the residual on-loop encode is **bounded and pinned** at **240 × ≤ 50 names ⇒ ≤ 12 000 points ≈ 3.6 ms**. |

**Contract versioning**: envelope carries `schema_version` (query-API schema, starts at 1,
exported constant `QUERY_SCHEMA_VERSION`) AND `vocabulary_version` (mirrors
`VOCABULARY_VERSION` — one mirror, two axes: envelope shape vs label vocabulary). FE models
mirror verbatim (house rule); a breaking envelope change bumps `schema_version` and the FE
model in the same commit.

**Bump policy (AM-7)**: a **breaking** envelope change (field removal/rename/type/semantic) ⇒
`schema_version` bump; an **additive** change (new optional field / filter / enum value) ⇒ NO bump,
and consumers MUST ignore unknown members. `vocabulary_version` is a **separate axis** (label VALUES,
never envelope shape). FE mirrors: `vocabulary_version: number` (never a literal), the availability
reason as a widened union with an `unknown(<value>)` fallback — mirrors are verbatim on field SETS,
never on values.

**Auth/scope posture**: `maintenance:read` on all three routes + the same `_require_auth_on`
503 fail-closed seam BEFORE any registry read. Rationale for NOT inventing a lighter
observability scope: (i) exact alignment with the family's pinned scope manifest
(`test_router_scope_manifest.py:169` — new routes must declare a scope within the pinned set, so
`maintenance:read` is the zero-amendment choice); (ii) the maintenance-class agents who need
these reads (maintenancer, devops) already hold `maintenance:read` as the observability scope;
(iii) one scope for one family keeps the cage trivially green.

**Over-grant disclosure + retrofit map (AM-20)**: `maintenance:read` ALSO confers
checkpoint-cleanup-EXECUTE capability (`maintenance.py:255`, `:279-283`) — the query-API docs must
state this. The scope split is a **named commitment** (leader LR-1), not a "revisit later": the
retrofit is 1 taxonomy line + 1 manifest line + 4 decorator lines (the cage is a subset check, and
`SESSION_OPERATOR_SCOPES` auto-grants). **Manifest shape at that time:** family manifest
`"/api/metrics": "maintenance:read|metrics:read"`, the three new routes declare `metrics:read`, the
shipped text route keeps `maintenance:read`. Until that retrofit lands, v1 keeps `maintenance:read`
— see the D6 section below for the binding guidance to the deferred lanes.

**Swappability guarantee (the ONE DOOR)**: routes depend on a `MetricsQueryProvider` protocol
(`families() / snapshot() / history()`), constructed in lifespan beside the listener, stored on
`app.state.metrics_query`. The provider READS ONLY published registry state — it never mutates
(no inc/observe calls reachable from the query layer; enforced by review + a pin that greps the
query package for mutation calls). Backend swap (e.g., a future PG-backed provider) replaces the
provider under unchanged routes. Future agent tools (deferred item #2) call the SAME provider
functions directly — the provider is importable, side-effect-free, and the single read substrate
for both the HTTP door and the tool door.

**Kill-switch interplay**: per D1 — no switch consultation on the routes. Data honesty is
handled by availability/staleness metadata (families endpoint `availability`, snapshot
`last_publish_age_s`). Under switch-OFF: registry-sourced series flow normally; F6 census
families go dark with a reason; the listener body is independently empty (unrelated surface).

**`census_gated` source (AM-18)**: the `census_gated` reason is read from a **public
`CensusSampler.gated` property** that wraps the EXISTING gate site
(`metrics_census.py:332-340` → `metrics_gate.metrics_enabled()`). **The property does not exist in the
tree — phase-2 task 2.4a creates it** (a read-through of the existing private `_gate()`; **NO new
behavioural check site**, so the census over `daemon/` stays at exactly **2**); `metrics_census.py` is
declared in the P2-G1 file scope for that one additive property; `activation.census_enabled =
not census.gated` (honest — `.running` is switch-independent). **The query package NEVER reads the
switch** ⇒ the check-site census over `daemon/` stays at exactly **2** (no third site), and the
route-level grep pin stays green.

**Degradation semantics**:
- *Lock contention*: structurally N/A on the query path (lock-free published reads). What the
  exposition path reports as `lock_contended` shows up here as stale `last_publish_age_s`.
- *Render budget*: N/A — the query path renders nothing (`RENDER_TOTAL_BUDGET_SECONDS` is
  exposition-only).
- *Census-when-pg-off*: census sampler is only started on postgres (`api.py:2454-2483`) ⇒ F6
  families absent ⇒ `availability.state="dark", reason="census_not_running"`.
- *Darwin prod*: the dark-family infrastructure still ships (F6 `census_not_running`,
  F0.3 `series_absent`), but **F2 is NOT in the v1 subset** (AM-25 / LR-2: dropped by value
  ruling, not by rule) — so no `proc_unavailable` reason is expected in v1. When F2 is re-admitted
  the reason set gains `proc_unavailable` (catalog §13).

**Future-consumer anticipation**: agent tools (#2) get cheap structured reads (no HTML-scraping
Prometheus text, no bash-curl loopback); Grafana (#3) rides the exposition text UNCHANGED — the
query API is explicitly NOT Prometheus-compatible and MUST NOT become so (it complements;
exposition stays the Prometheus contract per MA-16 classic-text ruling).

**Non-Prometheus deviations clause (AM-8)**: the JSON envelope borrows Prometheus **vocabulary**
but its semantics are local and must be declared as such — `kind` ∈ {gauge, counter, histogram};
histogram buckets are reported **as stored** (not Prom-cumulative); monotonicity holds only
**within one `restart_epoch`**; durations are in **seconds**. No exposition-format leakage into the
JSON envelope (verified: no `# HELP`/`# TYPE`/escaping constructs in the wire format).

**FE polling ergonomics**: Overview tab polls `snapshot` every 10 s; Charts tab polls `series`
every 15 s (visible-tab-gated, lazy-body teardown per house pattern); Families tab loads
manually. Both endpoints are single round trips; constants pin-tested (jobs-poll precedent).

**Cage cost accounting (Option A vs B)** (AM-21 corrected): Option A = **declared-route census
108→111** (`test_router_scope_manifest.py::test_declared_route_count_pinned`, :333-396) + zero
family changes; the **mount census stays 37** (static) and **`api_families` stays 33**; Option B =
class-manifest 33→34 + new scope entry + new mount + new cage rows. Option A is strictly fewer
cages and keeps the FE base path coherent (`/api/metrics/*` = one surface, one scope story). The
earlier "route-count pin 34→37" target was a **docstring**, not a pin — it would have left the real
census red.

### Promote-time disclosure

> Three JSON routes were added UNDER the existing `/api/metrics` family (families / snapshot /
> series). The `api_families` count is UNCHANGED (33); the **declared-route census was bumped
> 108→111** and the mount census stays 37 (AM-21 corrected an earlier docstring-based target);
> all three routes carry the family's pinned `maintenance:read` scope with the same fail-closed
> 503 seam — and that scope also confers checkpoint-cleanup-execute capability (AM-20). Family
> metadata lives at registration (AM-3), so a newly wired family is discoverable without editing
> the door. The query layer reads ONLY published registry state — it cannot mutate metrics; a
> future backend swap replaces the provider, not the routes. The query API is not
> Prometheus-compatible by design (AM-8: local semantics — as-stored buckets, restart-scoped
> monotonicity, seconds); Prometheus scraping continues to consume the unchanged `/api/metrics`
> text exposition.

---

## D5 — Extension subset derivation (18 objects → toward 27 families)

### The frozen rule (from the commission, applied mechanically)

A family enters the v1 extension subset iff ALL of:
1. committed catalog family (CAT §1 registry);
2. EXISTING producer/pull seam at the research base (data already produced or pull-able);
3. NO new module instrumentation (registration/wiring only — no new observe/inc calls at new
   code points in production modules).

Phase labels (P1a/P1b/P1c) are NOT part of the rule — they order the work clusters.

### Classification table

| Family | Class | Series Δ (CAT §14-quoted) | Seam anchor | One-line rationale |
|---|---|---|---|---|
| F0.3 spine health | **IN** | +8 | `resource_monitor/flusher.py:161` FlusherCounters + `sampler.py` SamplerCounters (canonical-counters rule, CAT F0.3) | Registration over existing counters; producer landed (WS-A merge precedent). |
| F1 loop lag | **IN** | +9 | `wedge_capture/lag_probe.py:107-116, :185` (`snapshot_histogram`, quantiles) | Computed quantiles exported as gauges (MA-4); dark-when-capture-off = absence, not zero. |
| F2 thread CPU | **OUT — by VALUE ruling (AM-25 / LR-2), not by rule** | +0 (was +22) | `wedge_capture/sampler.py:151-192` (per-thread jiffies, ONE /proc walker; MA-2) | The mechanical rule admits F2, but the architect's 5-axis trade-off (drop 4.55 vs ship-dark 3.40; dominant axis Maintainability — permanently-dark shipped code is standing disclosure debt, and prod is Darwin-primary so the darkness is **Certain**) rules it OUT of v1. **Flip condition (verbatim, leader LR-2):** "if the ops surface becomes Linux-primary or the operator confirms near-term per-thread CPU need, re-admit (+22, one pack)". The sampler-key-is-`/proc`-samplers'-key disclosure (AM-15 i) is retained for that future. |
| F3 wedge counters | **IN** | +6 | `wedge_capture/triggers.py:202/:237/:281` + `watchdog.py:417-418` | Existing capture counters; registration + wiring. |
| F4 pool native | **IN** | +8 | `readiness.py:525/:552` + `wedge_capture/collector.py:19-29` | Existing samples already published at `/readyz` detail.pool_state; one status source (R13). |
| F5 pool derived | **IN** | +3 | same + derived `checked_out = pool_size − pool_available` (`collector.py:19-20`) | Derived vs native kept apart (m2). |
| F6 PG census | **IN** | +15 | `daemon/services/metrics_census.py` — P0-scope-discipline block: "deliberately registers NO metric families"; frozen `CensusSnapshot` dataclass | BORDERLINE RESOLVED: the catalog's "greenfield" note is historical — the module is LANDED at the research base; registering gauges over the existing snapshot is pure registration (rule-3 satisfied). Census discipline §9 inherited; sampler already runs on postgres (`api.py:2454-2483`). |
| F7 completion | **PARTIAL (wiring only)** | +10 (21→31) | `checkpoint_perf.py:148` `bytes_=` param + the ONE production call site `persistence.py:632` (op="aget") | Pass the real byte count at the existing call site — an existing observe gains a real value, not a new observe point. The J-4 write-side hook (`_charge_checkpoint_write`) is NOT in this lane. |
| F8 store sizes | **PARTIAL 4/9** | +4 | `maintenance.py:531-536` `_VACUUM_FULL_TABLE_BYTES_SQL` | Byte gauges (blobs 1 + per-table 3) = pull of an existing query ⇒ IN. `versions_per_thread` + `prune_runs_total{outcome}` ⇒ OUT: no existing outcome counter exists (`checkpoint_prune.py:91` `would_delete_count` is a per-sweep dry-run accumulator, not a run-outcome counter) — would require new increments at prune sites. |
| F9 readiness | **IN** | +53 | published composite `api.py:5015` (`detail.component_classes/grace/leak_signal/failure_streak`); taxonomy `readiness.py:64-79` | Reads the published composite, never re-probes (MA-5). Largest single family — wire as its own work cluster. |
| F10 queue depth | **IN** | +6 | `job_queue_items.admission_state` enum (`:40-43`) + count seams | Depth gauges pull (TIER-1 durable). Durations NOT derivable (G-11/C15) — no duration family claimed. |
| F11 worker pool | **PARTIAL 16/30** | +16 | `worker_pool.py:1518` `get_stats()` (+ `:1537` efficiency) + busy-derivation `instance_read.py:2092` + heartbeat `task/models.py:284`, cadence `worker_pool.py:45` | Gauges + events counter + heartbeat age/cadence IN (pull). **Claim latency OUT** (commission-confirmed): GREENFIELD G-6 — seam `worker_pool.py:302`→`task_processor.py:1829`→`repository.py:2619` needs a NEW observe at the claim site. |
| F12 sweeps | **IN** | +69 (ceiling 117) | per-service `counters()`: `watch_reconcile_sweep.py:356`, `eligible_pending_sweep.py:404`, `orphan_watcher_sweep.py:260` + RDRS per-lane row counters | BORDERLINE RESOLVED (accumulation-vs-instrumentation): producers return PER-TICK values; the CUMULATIVE accumulation belongs in the metrics pull layer (registry counters are cumulative; the layer diffs successive `counters()` reads). Producers untouched. **C17 doc-drift fix is DEFERRED OUT of this lane** (plan council W5): `daemon/services/job_queue_service.py` is on the mechanical Tier-2 list, so a one-line comment edit would mechanically force a whole-tree dual-side leg — disproportionate for a comment, and the metrics layer's accumulation is the real disambiguator. The fix is recorded in the fold ledger + the deferred list for a lane that legitimately edits that file. |
| F13 instances | **PARTIAL 19/29** | +19 | `instance_read.py:2019` `_build_capacity_summary` (`counts_by_status`, `counts_by_liveness`) + waiting-children query | Status/liveness/waiting-children gauges IN (pull). `wake_turns_total` OUT (greenfield — §8.13 Carrier-Threading producer/carrier contract is new increments at `worker_pool.py:302`/`graph.py:7319`); `lifecycle_total` OUT (new increments at `event_publisher.py:38-80`). |
| F14 LLM tokens | **OUT (commission)** | +0 | `graph.py:3776-3784` usage captured-then-DROPPED (zero consumers) | Increment = new instrumentation; commission OUT. |
| F15 LLM requests | **OUT (commission)** | +0 | `llm_error_classifier.py:818` in-closure dict | Observe at same points = new instrumentation; commission OUT. |
| F16 stream stalls | **OUT (commission)** | +0 | `llm_stream_watchdog.py:456` | Counter registration at `_force_unblock` = new instrumentation; commission OUT. |
| F17 MCP health | **IN** | +40 | `mcp/init_guard.py:145-147` per-boot quarantine registry, published `/readyz` detail.mcp (`api.py:3840-3878`), consumed `mcp_servers.py:302` | Reads the snapshot, never re-probes. Catalog phase P1c — the rule is phase-agnostic; scheduled in the P1c-order cluster. |
| F18 gateway | **PARTIAL 5/9** | **+5** | `mcps_gateway/store.py:79/:103` roster + `tombstones.py` + the durable `mcp_gateway_audit` table | **ADJUDICATED (D-5 + D-6 + AD4(a)) — PARTIAL 5/9, +5 series**: sessions (1) + tombstones (1) + `ensemble_mcps_gateway_audit_total{outcome}` (3) under the **amended vocabulary `{ok, error, timeout}`** (catalog §8.10 amended; `VOCABULARY_VERSION` rides the 1→2 bump). `session_total{outcome}` stays OUT (refusals/expiries are protocol errors — `sessions.py:135/:288` — not aggregated counters). `OUTCOME_DENIED` is declared+exported but **never written**, so it is excluded from the frozen vocabulary (an over-promise would repeat the F7 defect class); `timeout` is preserved as its own label — never folded into `error`. |
| F19 compaction | **OUT** | +0 | `compaction.py:1128/:2299` | No pull-able snapshot; registration = NEW observe calls in compaction hot paths (rule 3 fails). |
| F20 turn/tool seam | **OUT (commission)** | +0 | `message_tap.py:234` | Increments at the tap = new instrumentation (C66 silent-swallow wrapper requirement); module deepening; commission OUT. |
| F21 journal twin | **PARTIAL 3/65 (+30 conditional)** | +3 | `tools/upgrade_journal.py` — `:159` current, `:165` history len, `:255` `stat().st_size` | BORDERLINE RESOLVED (read-gauges-vs-op-instrumentation): the three point gauges are PURE READS of the twin's own accessors ⇒ IN. `journal_op_seconds` + `journal_ops_total{op}` OUT (wrapping the twin's op functions = instrumentation; also the op vocabulary is 26 values of pure cost). `upgrade_phase_seconds/total{phase}` CONDITIONAL: IN only if journal history entries already record phase transitions enabling pure-read derivation — plan-time verify; else OUT. `scripts/upgrade/` remains NEVER-edited (leader-ratified decision 2). |
| F22 exporters | **OUT (P2, commission)** | +0 | out-of-process textfile writer, devops-owned | Commission OUT. |

**Also OUT (uncatalogued or instrumentation-required, consistent with commission OUT):** sources
circuit-breakers/token-buckets (no family), badge/request latency (no family), mission
mint-failure outcomes (log-only), DLQ replay outcomes, loop-breaker state (TIER-3), snapshot
metrics re-surfacing (m5 exclusion), skill-metrics lane (m5), checkout-latency family (CR-4
retired; superset ships via F4).

### Folds

- **Interim aput-enum narrowing (S-1 → ACTIVE row J-4 interim) — FOLDED INTO THIS LANE.** Code
  still ships the 5-value enum `{aget, aput, adelete, alist, other}`
  (`checkpoint_metrics.py:1011`); the catalog already narrowed the documented set (CAT §14.4
  "checkpoint op 4 — narrowed to the observable set until the write hook lands"). Fold: narrow
  `allowed_values` to the catalog-declared observable set. **Derivation RULE (recorded so J-4
  re-derives rather than re-litigates — architect v2 D-7 CONFIRMED):** the observable set =
  `aget` (the sole producer, `persistence.py:632`) + `other` (the RESERVED FOLD TARGET — it must
  stay declarable or the fold target is itself out-of-vocab). `aput` / `adelete` cannot fire (the
  J-4 write hook has not landed); `alist` is migration-only and its walk was deleted
  (`persistence.py:628`). **Final membership: `{aget, other}` (2 values)** — the catalog's §14.4 row
  ("checkpoint op 4") is STALE and is amended in the ledger phase. Registry-side only — no
  producer instrumentation. **VOCABULARY_VERSION bump 1→2 required** (A24: any vocabulary change
  bumps the gauge). Breaking-alert check: no shipped alert references the removed values — the
  Prometheus stack itself is deferred item #3 (nothing shipped consumes PromQL yet); disclose
  anyway.
- **Bucket fix: NO in-scope family needs it.** No in-scope addition introduces a new histogram
  (claim latency OUT ⇒ its 14-bucket set not needed; journal op OUT; op_bytes uses distinct byte
  bounds already registered). The `DEFAULT_LATENCY_BUCKETS` 10 s-top residual
  (`register_summary` :985 / bucket-less `register_histogram` :965) stays with the deferred
  write-seam commission (J-4), where the write-path latency histogram will be designed with its
  own buckets.

### Series-count delta vs the ≤2000 budget

Pointers per M-2: all per-family values above are CAT §14-quoted; re-derive against the ledger
at plan time (the F7b figure was "measured against the live registry", so live totals may drift
slightly from catalog sums).

**RECONCILIATION PREREQUISITE (AM-26) — RESOLVED:** the catalog's daemon-origin total is stated
as **582** in §1 (`:224`, `:232`, `:251` — "F0.1→F21 + F7b + F7c"; the §1 delta trail carries the
`+2 (F7c)` term and sums to 582; §1's own phase row-sum is P0 28 + P1a 63 + P1b **265** + P1c 226
= 582) but as **580** in §14.1 (`:1057` — the total row enumerates "F0.1–F21 + F7b", F7c **absent**)
and §14.2 (`:1075` — the trail omits the `+2 (F7c)` term; §14.2's P1b row is **263** = 265 − 2) and
F7c is absent from §14.3's per-family rows. §1 also carries an explicit **F7c row worth 2 series**
(`:206`). **Verdict: 582 WINS** — the ledger rows are stale-by-2 (the F7b retro-cataloguing added
F7b's 46 but never carried F7c's 2), and §1 (the canonical name registry) plus §7's own arithmetic
both include F7c. The **baseline is therefore pinned with BOTH denominators**: **89 (F7c-inclusive)
/ 87 (ledger-rows basis)**. The ledger amendment lands in the ledger phase (catalog §14.1 → 582
with "F0.1–F21 + F7b + F7c"; §14.2 P1b 263 → 265 and the trail +2 (F7c); §14.3 gains the F7c row).

- Registered TODAY (18 objects): F0.1 16 + F0.2 3 + F0.4 1 + F7-today 21 + F7b 46 + F7c 2 =
  **89 series (F7c-inclusive) / 87 (ledger-rows basis)** — both denominators pinned.
- Extension delta (this lane, post-AM-25): full-IN **+217** (**ten** families after F2's removal —
  F0.3 8 + F1 9 + F3 6 + F4 8 + F5 3 + F6 15 + F9 53 + F10 6 + F12 69 + F17 40; **F2 was adjudicated
  IN (full, 22 series)**, so its removal changes the full-IN family set **11→10** — it was NOT a
  partial) + partial-IN **+57** (6 families: F7 +10 + F8 4 + F11 16 + F13 19 + F18 5 + F21 3)
  = **+274**. (The earlier printed "+295 / 78" is OVERTURNED — its own
  rows summed to 76.)
- Post-lane registered: **363 ≈18.2 % of budget** (F7c-inclusive) / **361** (ledger basis) /
  **364** (if D-6's 4-value F18 audit set is ever admitted) / **355** (if WS-A is absent and F0.3
  defers, −8). Conditional rows are **not** counted: F21 phases (+30, OUT per D-4), F8 prune-runs
  (+4, OUT per D-3), F2 (+22, OUT by value ruling per AM-25/LR-2).
- Governing bound unchanged: fully-wired catalog ceiling ≈1145 (CAT §14.1 cross-check row) —
  still <2000; the F7 enum narrowing SHRINKS the F7 /op fully-wired ceiling (121 → fewer ops ×
  buckets), so all narrowing effects point DOWN. The registry-native proxy is **822** (§14.1) —
  re-measure at family-pack time.
- **Cardinality enforcement wording (AM-27):** `DEFAULT_CARDINALITY_CAP=2000` is a **PER-METRIC**
  label-combination cap (`checkpoint_metrics.py:629-664`, same-critical-section rejection counting
  at `:41-45`; managed key `metrics_cardinality_cap`, no restart). It **cannot** enforce a global
  budget. The global ≤2000-series budget is a **catalog §14 planning convention**, enforced by
  per-family label-space assertions (the family packs) — the registry's cap is the safety net for
  any single metric, not the budget mechanism.

### Promote-time disclosure

> The extension subset wires **16 of the 26** daemon-origin families (10 full + 6 partial),
> **+274** series (catalog-quoted) toward the ≤2000 planning budget (post-lane **363 ≈18.2 %**,
> F7c-inclusive; 361 on the ledger-rows basis; the fully-wired catalog ceiling ≈1145 governs and
> is unchanged). **F2 is NOT in v1** — dropped by value ruling, not by rule (AM-25 / LR-2; re-admit
> +22, one pack, if "the ops surface becomes Linux-primary or the operator confirms near-term
> per-thread CPU need"). Ten families stay OUT: those requiring new module instrumentation
> (F14/F15/F16/F19/F20 commission-excluded; F11-claim, F13-wake/lifecycle, F18-session, F21-op
> deferred to their named-seam commissions), F8 prune-runs (no outcome counter exists), F21 phase
> families (no structured phase field in the journal history) and F2 (value ruling); F22 stays
> P2 exporter-origin. `VOCABULARY_VERSION` bumps 1→2 from the F7 op-enum narrowing to `{aget,
> other}` (observable-set fold, decisions J-4 interim) plus the F18 §8.10 amendment to
> `{ok, error, timeout}`; no shipped alert consumes the removed values. The catalog's daemon-origin
> total is reconciled to **582** (the F7c-inclusive figure — the §14.1/§14.2 ledger rows were
> stale-by-2).

---

## Cross-cutting disclosure block (single promote-time list)

1. Query API auth posture (D1d text above).
2. Zero FE dependencies; SVG primitive generalization (D2 disclosure).
3. In-RAM history buffer: window/cadence/memory envelope + restart-loss + sampler-key reuse
   (D3 disclosure).
4. Query routes under the existing family: `api_families` 33 unchanged; **declared-route census
   108→111** (the mount census stays 37); `maintenance:read` on all three — which also confers
   checkpoint-cleanup-execute capability (AM-20) — plus the provider read-only guarantee and the
   registration-time family metadata (AM-3) (D4 disclosure).
5. Extension subset **16/26** + exclusions (incl. F2's value-ruling drop) + VOCABULARY_VERSION
   1→2 + the catalog §14 F7c reconciliation to 582 (D5 disclosure, AM-26).
6. `limits.metrics_sampler_interval_s` now consumed (was unwired) — the seven managed keys keep
   their defaults, bounds, types and restart columns; this one key gets a **description-only**
   amendment naming both consumers; the reported `interval_s` is the OBSERVED effective spacing
   (AM-15) (D3 disclosure).
7. **C17 docstring correction is DEFERRED, not shipped** (`job_queue_service.py:828-836` is on the mechanical Tier-2 list; the one-line comment fix is disproportionate against a whole-tree leg — plan council W5). It is recorded in the fold ledger + the deferred list.
8. NO live promote in this lane; user 3-factor nonce ceremony owns it.

## PLAN-AUTHORITY — the ordering/census pins (D-e)

**The durable rule:** every **ordering** or **census** pin is quoted from the plan set
**VERBATIM, with its `file:line`**. A dispatch brief, a task summary, a chat instruction or a
paraphrase is **never** the authority for an ordering pin — **the plan row is**.

**The authoritative order row — `phase3-plan.md:36` (task 3.4, AM-13).** Verbatim:

> start the sampler **LAST of the metrics trio** — after the census block (`api.py:2467-2483`),
> and **the listener starts FIRST and must NOT be reordered** (AM-13 corrects the earlier
> un-implementable "after census, before listener" instruction); on shutdown, the sampler stops
> **FIRST**, before the listener stop (`api.py:3700-3709`) with the detached-join discipline
> (listener `stop()` precedent `:551-593`).

and its acceptance clause, verbatim:

> The wiring test asserts the exact start order (listener → census → sampler) and the reverse
> stop order (sampler → listener)

**So the order IS `listener → census → sampler`** — the sampler is **LAST of the trio** — with
the **reverse stop order `sampler → listener`**. The earlier "census before listener start"
phrasing is **EXPLICITLY FORBIDDEN**: the row quoted above says so in its own words ("AM-13
corrects the earlier un-implementable 'after census, before listener' instruction"), and the
plan's own risk row records it — `plan-overview.md:511` — *"Lifespan start order ('before the
listener start' was un-implementable)"*, mitigated by *"Sampler starts **LAST** of the trio;
stops **FIRST** (AM-13)"*.

**The precedent (recorded, per the council):** *the developer's Stage-B dispatch brief inverted
this pin; the code followed the plan and was RIGHT; the brief was WRONG.* The brief's own text is
**not** in the repository, so this is recorded as the **council's ruling**, not as a quote of the
brief — the quotable authority is the plan row above.

**Why the rule exists (the pin surface):** `phase3-plan.md:92` (`P3-M5`) plants the defect that
the order forbids — *"the **listener start moved AFTER the census block** (the order AM-13
forbids: the listener starts FIRST, the sampler starts LAST of the trio)"* — and
`phase3-plan.md:98` (`P3-M10`) plants the un-implementable form — *"the sampler start moved
before the listener start (the un-implementable instruction)"*. Both are **RED-when-planted**
pins on the D1 pack `metrics_query_api_wiring`, so an inverted brief surfaces as a **RED leg**,
never as a shipped inversion. That is the whole point of quoting the row: the brief is not the
authority, and the authority is machine-checked.

**Census pins are covered by the same rule.** The census-related pins in the same row — the
census block at `api.py:2467-2483`, the check-site census (exactly **2**), and the
`CensusSampler.gated` public property (the read-through of the EXISTING private `_gate()`,
**no new check site**) — are quoted from `phase3-plan.md:36` and `decisions.md` §D1/§D3 with
their `file:line`, never from a summary.

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

---

## Open questions for the architect/plan council

**All four are now ADJUDICATED (architect v2 §3 + leader LR-1..LR-3).**

1. **F18 audit-outcome vocabulary** — **ADJUDICATED (D-6 CONFIRMED): amend catalog §8.10 to
   `{ok, error, timeout}`** (the observable *written* set; `OUTCOME_DENIED` is declared+exported
   but never written). Never fold `timeout` into `error` (AD4(a)). The §8.10 amendment lands in
   the ledger phase; the `VOCABULARY_VERSION` ride-along is the same 1→2 bump.
2. **F21 phase-family conditionality** — **ADJUDICATED (D-4 CONFIRMED, evidence amended): OUT.**
   The twin's `event` vocabulary is broader than the 5 alert kinds, but phase names live in
   promote.sh free text / narrative logs and are never structured in `history[]` ⇒ no clean
   pure-read derivation ⇒ **+30 not claimed**; the absence is pinned.
3. **F2 sequencing** — **ADJUDICATED: DROP from v1 (AM-25; leader LR-2 CONFIRMED)** with the
   flip condition recorded verbatim in the D5 classification table. Subset = **16/26**.
4. **Sampler-key reuse** — **ADJUDICATED (AM-15 synthesis): the key stays the sampler TICK**
   (boot-applied, contract-consistent); `window.interval_s` reports the **observed** effective
   spacing; the ring evicts by age so the window stays truthful under any cadence change.

**Remaining (non-blocking):** the post-lane registry-native ring count (re-measure at family-pack
time; 822 proxy until then) and the exact per-request cost of the largest encode (the off-loop
amendment makes the loop question moot).
