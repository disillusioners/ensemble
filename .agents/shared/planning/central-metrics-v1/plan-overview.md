# Plan Overview: central-metrics-v1

Date: 2026-11-02 (UTC)
Author: planner[v2] via plan-creation worker (commission: implementation plan set for central-metrics-v1)
Status: Draft — architect verdict **SOUND-WITH-AMENDMENTS**; **AM-1..AM-27 folded** (2026-11-02) with leader rulings **LR-1..LR-3** → feeds plan council → fresh approver → implementation (tiered-gate doctrine) → code council → tidier → tester functional gate → giter staging
**Fold provenance (2026-11-02):** the architect's enriched deliverable `architecture-recommendation-v2.md` (239 L) is the fold source and supersedes v1 for planning purposes; v1 (`architecture-recommendation.md`) stays untouched on disk for diff provenance. Amendment ids are cited inline as `(AM-n)`; leader rulings as `(LR-n)`. **Tier shift: NONE** (P2 stays Tier 1 — AM-3's registration kwargs are additive; P3 stays Tier 2 — confirmed against the canon).
Branch: `feature/central-metrics-v1` (worktree `/home/nea/ensemble-src-wt-central-metrics-v1`, base == `origin/latest`)
Version slot: **NOT chosen in this plan.** The version is derived by giter at stage time (W9). **No version string for THIS lane appears anywhere in the plan set**, and no implementation lane may mint one early. (Historical release references are permitted where they name a prior lane's recipe — e.g. the v0.27.11 compensating-evidence recipe that AM-22 mandates verbatim — and are not version claims for this lane.)

---

## Objective

Grow the landed default-OFF central-metrics lane into the v1 system: **ONE** native query door over the
metric registry (three JSON routes under the existing `/api/metrics` family) with an in-RAM trend buffer,
wired to the adjudicated **16-of-26-family** extension subset and a **zero-dependency** FE charts page —
while the shipped Prometheus-text exposure and the `:9798` listener contracts stay **structurally
identical** to base (modulo the enumerated 3-carrier value-delta allowlist — AM-16), the migration
count stays at zero, the dependency count stays at zero, and **no live promote happens in this lane**.

Single-sentence completion test: a *staged* (not promoted) release in which
`/api/metrics/families|snapshot|series` answer from the registry under scope `maintenance:read`,
the adjudicated extension subset is registered at `VOCABULARY_VERSION=2`, the FE `/metrics` page renders
from those routes with **zero** new npm dependencies, and both frozen exposition contracts
(`:9798` listener body, `/api/metrics` text body) are **structurally identical to base except the
enumerated 3-carrier value-delta allowlist** (version footer · version gauge · F7 HELP) — any delta
outside that allowlist is a gate red (AM-16; AC-A.5 re-scoped).

---

## Dispatcher adjudications (BINDING — recorded with rationale + architect-override hook)

These were ruled by the planner/dispatcher on top of `decisions.md`. Each is a **plan default**, not a
silence: the architect may override at the next gate; the hook column says what changes if it does.

| ID | Ruling (binding) | Rationale (plan-side) | Architect-override hook |
|---|---|---|---|
| **AD1** | Trend storage is **IN-MEMORY ONLY** in v1. Zero migrations; a PG table would need its own plan amendment. | Matches `decisions.md` D3 Option A; keeps the lane additive and preserves the house architecture split (Prometheus = long history, resource_monitor PG tier = per-instance views). | If the council prefers durability: a new phase + one `daemon/migrations/versions/*.sql` per SQLModel table + a retention-sweep coupling — **not** a phase-local edit. |
| **AD2** | The FE charts page ships in the **SAME release/stage** as the query API — Must tier, not a follow-up. | It is a commission deliverable; a split release would ship a door with no consumer and force a second ceremony. | Splitting it means re-tiering FR-E1…E8 to Should and shipping a second stage — architect must rule explicitly. |
| **AD3** | Extension-subset rule applied **STRICTLY**: a metric is IN only where an **already-computed value is READ** (pull adapter / scan adapter over existing state is fine); a **NEW increment/timing call** at a module site that has no existing counter today is OUT. Binding subset = `decisions.md` D5 as amended (AM-25): **10 full + 6 partial = 16/26** daemon families. The plan **re-derives** the series count at plan time (see *Re-derived budget*) and pins it. | D5's rule-3 is the commission's frozen rule; the requirements table had drifted on two families (see *Disagreement flags* D-3/D-4). | Re-admitting a family moves its series delta into the pinned budget and re-opens its family pack. |
| **AD4(a)** | **F18 live vocabulary drift** (`audit.py:53-56` declares `ok/error/denied/timeout`; catalog §8.10 pins `{ok,error}`): **AMEND the frozen vocabulary + bump** — never silently fold to `other`. | Folding loses the denial/timeout signal; the catalog's own C88 fold rule is exactly the silent-fold the adjudication forbids. | Overriding to "fold" re-creates the F7 over-promise defect class; would need a new defect-record row. |
| **AD4(b)** | **F21 phase families stay CONDITIONAL** per D5's conditionality. | D5: IN only if journal history entries already record promote-phase transitions enabling pure-read derivation. | Plan-time verification (P4 task) resolves the conditionality; the architect confirms the verdict. |
| **AD4(c)** | **SUPERSEDED by LR-2 → F2 is DROPPED from v1** (AM-25). The flip condition is recorded verbatim: **"if the ops surface becomes Linux-primary or the operator confirms near-term per-thread CPU need, re-admit (+22, one pack)"**. | The mechanical rule admitted F2, but the architect's 5-axis trade-off (drop 4.55 vs ship-dark 3.40; dominant axis Maintainability — permanently-dark shipped code is standing disclosure debt, and prod is Darwin-primary so the darkness is **Certain**) rules it OUT. AD4(c) had reserved this as a value call; the leader confirmed the drop. | Re-admitting F2 = **+22 series, one family pack**, and the sampler-key disclosure (AM-15 i) applies to it. |
| **AD4(d)** | The trend sampler **reuses the currently-UNWIRED managed key `limits.metrics_sampler_interval_s`** (10 s default) rather than minting a new knob. **AM-15 synthesis:** the key stays the sampler **TICK** (boot-applied, contract-consistent), but the envelope's `window.interval_s` reports the **OBSERVED effective spacing** `max(sampler, publisher)` = 15 s at defaults — never the configured cadence — and the ring evicts **by age** so the 2 h window stays truthful under any cadence change. | It is boot-applied with a pinned restart column, it exists, and its description is already "host-side samplers" — amending that string is cheaper than a new key + new managed-key pin row. | A dedicated constant makes the cadence non-configurable (D3 open question #4 — now adjudicated); a new key needs a `test_metrics_managed_keys.py` row + restart-column ruling. |
| **AD5** | **`VOCABULARY_VERSION` 1→2 rides this lane** (F7 op-enum narrowing to the observable set) **with the §8 annex update** — the interim half of the S-1-superseded `aput` fix. The distinct **`_charge_checkpoint_write` seam stays OUT** (own commission). | AD5 binds the narrowing + the bump; the write seam is a separate instrumentation commission (J-4/V-6/Q1). | Re-admitting the write hook re-opens rule-3 (new instrumentation) and needs its own budget + bucket design. |

---

## Leader rulings (LR-1..LR-3 — BINDING, folded verbatim)

| ID | Ruling (binding) | Fold site | Rationale / consequence |
|---|---|---|---|
| **LR-1** | **The `metrics:read` scope split is a NAMED COMMITMENT, not built now.** v1 keeps `maintenance:read` with AM-20's documented retrofit map; the **committed future scope-split** (the 4-line retrofit map + the manifest shape at items #2/#3 time) is recorded in `decisions.md` **D6** as **binding guidance for the deferred lanes**. | `decisions.md` §D6 (new) · plan-overview §Scope notes · Phase 6 docs | Retrofit is cheap (1 taxonomy line + 1 manifest line + 4 decorator lines; the cage is a subset check; `SESSION_OPERATOR_SCOPES` auto-grants). The trigger: a non-operator machine lane reads the door in this lane's lifetime ⇒ mint `metrics:read` then. Manifest shape at that time: family `"/api/metrics": "maintenance:read\|metrics:read"`, the three new routes declare `metrics:read`, the shipped text route keeps `maintenance:read`. |
| **LR-2** | **F2 DROP CONFIRMED.** The subset is **16/26 (+274, post-lane 363 ≈18.2 %)**. The architect's flip condition is recorded **VERBATIM** in `decisions.md` D5 + in this file's AD4(c) hook. | `decisions.md` D5 F2 row · plan-overview AD4(c) + budget · Phase 4 W1 | Value ruling, not a rule ruling. Re-admission is one family pack + a budget amendment. |
| **LR-3** | **`requirements.md` is PLAN-SET-OWNED** — the AC-A.5 / FR-A7 / AC-A.6 re-scopes are folded **directly** (D-11b: **D1(b) wins**; re-scoped to the **LISTENER** surface; the door's OFF semantics are **availability, not emptiness**). Re-scoped rows are annotated commission-style (provenance: architect AM-16/AM-17 + leader LR-3, date). | `requirements.md` FR-A7 · FR-D3 · AC-A.5 · AC-A.6 · AC-D.1 (+ FR-A6 annotated, not rewritten) · plan-overview flags | Anything genuinely **non-foldable** becomes a **DISCLOSED ADJUDICATED DEVIATION** in the flag table below — never a silent edit. (**Eight** such deviations are recorded: **DEV-1…DEV-8** — see §Disclosed adjudicated deviations.) |

---

## Inherited decisions (from `decisions.md` — binding unless an AD overrides them)

| ID | Ruling (one line) | Where it binds in this plan |
|---|---|---|
| **D1** | Surface-class posture. The listener body stays switch-gated default-OFF (**at the body level** — the socket still binds and serves a valid empty 200 while OFF; AM-19(a)); `/api/metrics` text and the new query routes are **auth/scope-gated only** and **never consult `ENSEMBLE_METRICS_ENABLED`**. **ZERO config defaults ship** (`limits.metrics_enabled` stays `False`; the seven managed keys keep their defaults/bounds/types/restart columns — one key gets a **description-only** amendment, AM-19(b)). The door has **no kill-switch of its own**: its emergency-off lever is **token revocation** (AM-19(c)). The two-class rule is demoted to **supporting precedent**; the load-bearing basis is intra-family consistency + the verified two-site blast-radius census (AM-19). | Phases 1, 2, 3, 5, 6. |
| **D2** | FE charting = **zero-dependency** generalization of the inline-SVG primitive (`svg-timeline.component.ts:17-18` precedent). No first-ever charting dep; the mermaid-transitive d3 is off-limits. | Phase 5. |
| **D3** | Storage/retention = **in-RAM ring buffer** owned by the query layer; tick = reused `limits.metrics_sampler_interval_s` (10 s, boot-applied); reported `interval_s` = the **observed** effective spacing `max(sampler, publisher)` = 15 s (AM-15); window 2 h; **array-backed** ring (`array('d')` + `array('q')`, head/tail index) at **16 B/point ⇒ 11.4 MiB** worst case (AM-9 — the `deque` form was rejected at 129–161 B/pt measured, 3.6× over FR-C4's 32 MiB ceiling); adaptive depth with a **registry-native** divisor + **age eviction** (AM-10); `restart_epoch` = `bash_spawn_reaper.CURRENT_BOOT_ID` (AM-14); lost-on-restart by design. | Phases 2, 3, 6. |
| **D4** | Query API = **three JSON routes UNDER the existing `/api/metrics` family** (`families` / `snapshot` / `series`); `api_families` stays **33**; the **declared-route census** bumps **108→111** (AM-21 — the earlier "route-count pin 34→37" target was a *docstring*, i.e. vacuous; the mount census stays 37); all three carry the family's pinned `maintenance:read` + the fail-closed 503 seam (and that scope also confers checkpoint-cleanup-execute — AM-20, with LR-1's named commitment); provider protocol reads **only** published state; **family metadata lives at registration** (AM-3, so a new family needs no door edit); NOT Prometheus-compatible by design, with the local-semantics deviations declared (AM-8). | Phases 1, 2, 3, 6. |
| **D5** | Extension subset = **16 of 26** daemon families (10 full + 6 partial) after **F2's value-ruling drop** (AM-25 / LR-2); F7 op-enum narrowing folded to `{aget, other}` (→ `VOCABULARY_VERSION` 2, with the derivation RULE recorded so J-4 re-derives rather than re-litigates); no in-scope family needs the bucket fix; the **global ≤2000 budget is a catalog §14 planning convention** — the registry's `DEFAULT_CARDINALITY_CAP=2000` is a **per-metric** label-combination cap and cannot enforce it (AM-27). | Phases 2, 4, 6. |

---

## Scope

### In Scope

- **P1** — Posture contract, per-surface activation semantics, the frozen query-contract envelope, the error/pagination vocabulary, the promote-time disclosure text, and the gate-pack declaration.
- **P2** — New query-substrate package (`daemon/services/metrics_query/`): provider protocol + registry-backed provider, in-RAM ring buffer, cadence-driven sampler thread, envelope builders, availability model; **plus** the registry-side F7 op-enum narrowing and `VOCABULARY_VERSION` 1→2; **plus** the `metrics_sampler_interval_s` description amendment (AD4(d)).
- **P3** — The three JSON routes on `daemon/routers/metrics.py`, the `daemon/api.py` lifespan wiring (provider construction + sampler start/stop), the **declared-route census bump 108→111** (AM-21; the mount census stays 37 and `api_families` stays 33), and the route/invariance pin packs.
- **P4** — The adjudicated extension subset (**16/26 families**) registered at the canonical seam, via pull adapters over existing producers, with per-family packs; F7 completion (real byte count at the existing call site); F18 audit gauge with the amended vocabulary; F21 read-side gauges. **No mechanical-list file is edited** — the C17 docstring correction is **deferred** (plan council W5) and recorded in the fold ledger + the deferred list.
- **P5** — The FE charts page: typed model mirror, service with static URL constants, generalized zero-dep SVG chart primitive, page with Overview/Charts/Families tabs, lazy route + gear-menu entry + ordering pins, colocated specs, Playwright e2e, **two build legs** (one `ng build --configuration development` for AOT **and** one `ng build --configuration production` for the budgets — AM-23).
- **P6** — Catalog/§8 annex/§14 ledger amendments, docs closure, evidence pack, stage-prep checklist (**no version choice, no promote**).

### Out of Scope — VERBATIM from the commission (requirements.md Theme G / FR-G1)

> - Module onboarding beyond mapped pull seams (no new instrumentation at module sites).
> - Tier-3 families: F14 (LLM tokens), F15 (LLM request latency/outcome), F16 (LLM stream stalls), sources breakers/token buckets, badge/request latency, module deepening.
> - Agent tool surface (item #2) — interface anticipates it (FR-G4) but no tools ship.
> - Grafana layer (item #3) — no dashboard or remote_write; stock Prometheus scrape only.
> - Live promote — user's 3-factor nonce ceremony owns it.
> - New FE dependency — default zero-dep (hand-rolled SVG); any dep requires explicit justification.
> - New PG tables — default none; if justified, one `.sql` per SQLModel table.
> - F7 write-side hook `_charge_checkpoint_write` (distinct seam, J-4/V-6/Q1) — separate commission.
> - F8 `ensemble_checkpoint_versions_per_thread` (greenfield).
> - F11 `ensemble_job_claim_latency_seconds` (greenfield).
> - F12 `ensemble_sweep_cycle_seconds{lane}` (no duration measurement).
> - F13 `ensemble_wake_turns_total`, `ensemble_instance_lifecycle_total` (new increments).
> - F19, F20, F22 (compaction, turn/tool, exporters) — catalogued P1c/P2.
> - Per-child RSS/CPU attribution on macOS (WS-A: dark on Darwin by decision).
> - Remote_write to durable store (Q2: separate project).
> - Re-partitioning of v1 phases (order forced by dependency graph).

### Also OUT (uncatalogued / instrumentation-required — consistent with the commission out-list)

Sources circuit-breakers/token-buckets · badge/request latency · mission mint-failure outcomes (log-only) ·
DLQ replay outcomes · loop-breaker state · snapshot-metrics re-surfacing · skill-metrics lane ·
checkout-latency family (CR-4 retired; the F4 superset ships) · **F2 thread CPU — OUT of v1 by VALUE ruling, not by rule** (AM-25 / LR-2; re-admit +22, one pack, if "the ops surface becomes Linux-primary or the operator confirms near-term per-thread CPU need") · F18 `session_total{outcome}` ·
F8 `prune_runs_total{outcome}` (see flag D-3) · F21 op-instrumented families (journal ops / upgrade phases /
journal events — see flag D-4) · F13 `wake_turns_total` / `lifecycle_total` · F11 claim latency ·
the `_charge_checkpoint_write` seam.

### Scope notes folded from the architect (AM-11, AM-20, AM-24, LR-1)

- **Scope over-grant, disclosed (AM-20):** `maintenance:read` ALSO confers **checkpoint-cleanup-execute**
  capability (`daemon/maintenance.py:255`, `:279-283`). The query-API docs state this; the split is a
  **named commitment** (LR-1 → `decisions.md` §D6), not a "revisit later".
- **DB-pull pattern (AM-11):** every DB-backed adapter opens a **one-shot direct `psycopg.connect` per
  tick** and closes it inside the tick — the census precedent verbatim
  (`metrics_census.py:226-240`, `:418-432`). A **persistent per-adapter connection is forbidden**
  (pool-in-disguise; it recreates the idle-in-transaction class cured in the DB-pool lane).
- **The query provider is NOT a `/readyz` component (AM-24):** the soak-gate grace discipline parses the
  readiness composite; the provider must **not** be added to it, so no implementer "helpfully" grows the
  readiness surface.
- **The query door has no kill-switch (AM-19(c))**: its emergency-off lever is **token revocation** of the
  `maintenance:read` grant; the runbook names the lever.

### House conventions this plan binds

- Planning artifacts live in the **worktree feature dir** (`.agents/shared/planning/central-metrics-v1/`); the shared main checkout is **read-only** for this lane.
- `scripts/upgrade/` is **never edited** (metrics-collection decision 2). The journal twin (`daemon/tools/upgrade_journal.py`) is the F21 surface.
- **Every new SQLModel table needs its own `daemon/migrations/versions/*.sql`** — expected **ZERO** in this lane (AD1: trend is in-RAM; no other table is materialised).
- No new backend dependency (no `prometheus_client`, no OTel, no statsd, no psutil) and **no new npm dependency** (D2).
- Commit-type discipline belongs to the implementation lane; this plan set makes no commit.
- No live promote anywhere in this lane; the stage is promote-ready only.

---

## Phase map

| Phase | Name | Objective | Tasks | Coupling | Gate tier | Status |
|---|---|---|---|---|---|---|
| **1** | Posture, activation & the frozen contract envelope | Fix the activation semantics for all three surfaces, freeze the envelope/error/pagination contract, and write the promote-time disclosure — with **zero** config or default changes shipping. | 7 | loose with 5/6; tight with 2/3 | **Tier 0** (docs-only diff) + declared D1 pin packs (executed in 2/3) | pending |
| **2** | Query substrate: provider, ring buffer, sampler, registry narrowing | Build the additive package the door needs — provider protocol, **array-backed** in-RAM trend ring, cadence-driven sampler, availability model, **registration-time family metadata (AM-3)**, the **public `CensusSampler.gated` property (2.4a)** — plus the F7 enum narrowing and `VOCABULARY_VERSION` 1→2. | 11 (9 base + 2.4a + 2.7b) | tight with 3, 4; tight with 1 | **Tier 1 — CONFIRMED** (AM-3's registration kwargs are additive with defaults; neither `daemon/api.py` nor `daemon/manager.py` is touched) | pending |
| **3** | Query API routes + manifest cages + lifespan wiring | Mount the three JSON routes under the existing family, wire the provider + sampler into the lifespan, and bump the **declared-route census 108→111**. | 8 | tight with 2; tight with 5 | **Tier 2 — CONFIRMED** (`daemon/api.py` touched ⇒ whole-tree dual-side, ≤90 min advisory) + **AM-22 execution provisions** (hang-class clause, 137-min precedent, expected-skip census, PROJECTION node counts) | pending |
| **4** | Extension-subset registration (W1/W2/W3 clusters) | Register the adjudicated **16/26** families by reading already-computed producer values — no new increments at module sites — with per-family packs, the **mandatory `allowed_values` declaration (AM-2)** and the F21 conditionality verdict. | 10 | tight with 2 (pull adapters plug into sampler stage A); loose with 3 | **Tier 1** × 3 cluster legs (additive; producers untouched) | pending |
| **5** | FE charts page (zero-dep SVG) | Ship the developer-facing charts surface on the new door: models mirror, service, SVG primitive, three tabs, route + gear + ordering pins, e2e, **dev-build AOT leg + production-config budget leg (AM-23)**. | 9 | tight with 3 | **Tier 1** (FE pack + e2e + 2 build legs) | pending |
| **6** | Docs, evidence & stage-prep closure | Close the ledger (§8.10 / §8 annex / **§14.1/§14.2/§14.3 F7c reconciliation** / §14.4), publish the docs + disclosure, vendor the inventory evidence (AM-24), assemble evidence, and prepare the stage (no version, no promote). | 8 | tight with 1, 4; loose with 2, 3, 5 | **Tier 0** (changed-pack PASS + ledger reconciliation) | pending |

---

## Coupling map

| | P1 Posture | P2 Substrate | P3 Routes | P4 Extension | P5 FE | P6 Closure |
|---|---|---|---|---|---|---|
| **P1** | — | tight (contract) | tight (contract) | loose | loose (OFF-state semantics) | tight (disclosure) |
| **P2** | tight | — | tight (provider + sampler) | tight (pull adapters plug into sampler stage A) | independent | loose |
| **P3** | tight | tight | — | loose (availability facts) | tight (FE mirrors the envelope) | loose |
| **P4** | loose | tight | loose | — | **loose** (the family list + the chart-primary roster feed the FE model; the FE consumes the same door — aligned with `phase4-plan.md` §Coupling) | tight (ledger) |
| **P5** | loose | independent | tight | loose | — | loose |
| **P6** | tight | loose | loose | tight | loose | — |

**Ordering note.** P2→P3→P5 is a hard dependency chain (the door needs the substrate; the FE needs the door).
**P4 is order-independent of P3** — family registration feeds the registry, not the routes — but it is placed
after P3 so the single Tier-2 whole-tree leg runs against a settled door surface. P1 is first because its
contract is what P2/P3 implement against. Re-partitioning the phases is explicitly OUT (commission).

---

## The frozen contract (what Phase 1 must specify and Phases 2/3/5 must implement)

One door, three routes, one envelope vocabulary. Shape decisions are D4's; the contract below is the
implementation-facing restatement this plan pins (the architect may amend shape, not the posture).

**Envelope (all three routes)**

```
schema_version: 1                       # QUERY_SCHEMA_VERSION — envelope-shape axis
vocabulary_version: <mirror of VOCABULARY_VERSION>   # label-vocabulary axis (2 after this lane)
generated_at_utc: "<ISO-8601 aware UTC>"   # AM-5: house producer timestamps.now_utc_iso() — NOT naive-UTC
restart_epoch: "<CURRENT_BOOT_ID>"     # AM-14: bash_spawn_reaper.CURRENT_BOOT_ID (uuid4().hex) — a restart
                                        #   is visible, never a silent gap (boot_epoch.get_boot_epoch() is
                                        #   NOT used: DB-clock domain, None-able)
activation: { listener_enabled: bool, census_enabled: bool, query_api: "auth-gated-always-on" }
                                          # AM-18: census_enabled = not census.gated (honest; .running is
                                          #   switch-independent) — the query package NEVER reads the switch.
                                          #   `gated` = the public property created by phase-2 task 2.4a
                                          #   (read-through of the existing `_gate()`; no new check site)
```

**Series identity (AM-1)** — a series is `(name, labels)`; the labels are the complete discriminator; the
canonical serialization is the registry's `_label_key` (sorted `(k,v)` pairs,
`checkpoint_metrics.py:430`). Ring keys and any future item-#2 watch-tool reference use the same
serialization. The door therefore exposes a **label-aware selector** `?series=<name>{<k>=<v>,…}`
(RFC-3986-escaped) alongside `?names=`; `?names=` stays **name-level** and returns every label variant.

**`GET /api/metrics/families`** — per family, the **canonical frozen shape** (single source; `decisions.md` D4 is the designated contract, and every other site quotes this string **token-identically** — inside a markdown table cell the `|` separators are written `\|`):

```
{id, names:[{name, kind, labels:{key:[values]}|null, help, buckets:[…]|null, cardinality_class, monotonic}], live_series_count, availability:{state:"live"|"dark", reason?}, role, source_idiom, durability, cardinality_contribution}
```

The family-level **`role`** is the chart role from the **chart-primary roster** (task 2.7b; approver B2). **`buckets` is ON the wire** (approver B3 + leader ruling): the provider already derives the bucket boundaries from the registered metric, and the deferred consumers (item #2 agent tools, item #3 Grafana) are exactly who needs them off-box; FR-A8 / AC-A.1 require the bucket sets to be discoverable. Runtime introspection is the serving truth
(registered objects + `allowed_values` + the **registration-time family metadata**, AM-3); the markdown
catalog is authoring truth and is never read at request time. `cardinality_class` is
`closed|open|none` (AM-2): `open` names (the two guard counters) render `labels: null` and are never
value-enumerated. **No second family table** — family metadata lives at registration, so a newly wired
family is discoverable without editing the door (AM-3; a static table would break the incremental-onboarding
fence and would serve zero families at Phase 3).

**`GET /api/metrics/snapshot`** — `{…, series:[{name, labels, kind, monotonic, value, last_publish_age_s}]}`
plus `?family=` / `?name_prefix=` filters. Staleness is explicit metadata, never a silent stale value
(`last_publish_age_s` needs the new public `Metric.last_publish_monotonic()` accessor — AM-13; the private
`_last_publish` is not an acceptable dependency).

**`GET /api/metrics/series`** — `{…, window:{from_utc,to_utc,interval_s,interval_s_source:"observed"},
last_sample_age_s, stale, series:[{name,labels,kind,monotonic,points:[[t,v],…]}]}` plus `?series=`
(label-aware), `?names=` (≤50, name-level), `?window_s=` (clamped to the buffer window), `?max_points=`
(server-side **bucket-max** downsample — named, never every-kth; **default `max_points` = 240**), `?cursor=`. `interval_s` is the
**observed** effective spacing `max(sampler, publisher)` (AM-15); `stale: bool` when
`last_sample_age_s > 3 × effective interval` — a wedged sampler surfaces as **staleness**, never a
silently frozen line (AM-5).

**Typing honesty (AM-4)** — `kind ∈ {gauge, counter, histogram}` (**no `summary`** — the registry never
produces it; `register_summary` renders as a histogram, `checkpoint_metrics.py:985`). Every series carries
`monotonic: bool` (counters are non-decreasing **within one `restart_epoch`**). Histogram series carry
`value: null` + `histogram: {count, sum, buckets}` **as stored** (not Prom-cumulative).

**Error vocabulary** (never a 500 for an expected condition): `404` unknown family (typed body) ·
`400` invalid label filter · `400 invalid_cursor` (expired/invalid cursor — AM-6) · `503` registry
unavailable · `503 AUTH_NOT_CONFIGURED` (fail-closed, before any registry read) · `403` scope miss.

**Cursor (AM-6)** — `next_cursor: string|null`; opaque; pins the evaluation instant + position; an
invalid/expired cursor (older than the buffer's oldest point) ⇒ `400 invalid_cursor`; the byte ceiling
produces a bounded body + a cursor, **never a silent truncation**; the families route is bounded the same
way. (AC-A.8 is unsatisfiable without this.)

**Availability reasons (closed set)**: `census_gated` (switch OFF — sourced from the public
`CensusSampler.gated` property, AM-18) · `census_not_running` (non-postgres runtime) ·
`capture_off` (wedge capture not active — F1/F3) · `series_absent` (family registered, no observation yet) ·
`listener_disabled` (surface note for the exposition listener only). `proc_unavailable` is **not** in the
v1 set (F2 is out of the subset — AM-25); the FE mirror is a widened union with an `unknown(<value>)`
fallback (AM-7), so a future reason never breaks the FE compile.

**Bump policy (AM-7)** — a **breaking** envelope change (field removal/rename/type/semantic) ⇒
`schema_version` bump; an **additive** change (new optional field/filter/enum value) ⇒ no bump, and
consumers must ignore unknown members. `vocabulary_version` is a **separate axis** (label VALUES, never
envelope shape). FE mirrors: `vocabulary_version: number` (never a literal).

**Non-Prometheus deviations clause (AM-8)** — the envelope borrows Prometheus **vocabulary** but its
semantics are local and are declared as such: buckets **as stored**; monotonicity **within one
`restart_epoch`** only; durations in **seconds**. No exposition-format leakage into the JSON envelope.

**Pagination/bounds**: max families per response, max samples per response, cursor; a single response must
stay under a stated byte ceiling; `max_points` defaults **≤ 240** (AM-12, **approver B1 + leader ruling**) so
the off-loop body stays bounded — 240 is the floor the AC-A.3 / AC-C.1 sample count requires (60 min × 4/min
at the 15 s effective spacing), so the default and the acceptance criteria agree by construction.

**Read-path purity (load-bearing)**: the query path reads **published registry state and ring buffers
only**. It never renders, never acquires a metric lock (the no-lock pin allowlists `registered_metrics()`,
the registry-level lock — AM-13), never performs a database read, and **never touches the switch**
(AM-18). Database-backed producers (F10 queue counts, F13 instance counts, F18 audit counts) are pulled by
the **sampler thread** into the registry via **one-shot direct connects** (AM-11); the door reads the
published gauge. **The response BODY is BUILT off the event loop** (AM-12, the `db_offload`/`/api/work`
precedent) — `db_offload.offload` returns a Python object (`db_offload.py:386-392`) and **FastAPI still
encodes it on the loop**; so AM-12 claims **BUILD-OFF-LOOP ONLY**, and the residual on-loop encode is
**bounded and pinned**: `max_points` ≤ **240** × `?names=` ≤ 50 ⇒ **≤ 12 000 points ≈ 3.6 ms** on-loop
(re-derived for the raised default — approver B1 + leader ruling). The **FE Charts tab requests
`window_s=3600`** (the 60-minute window) ⇒ 240 points at the 15 s effective spacing ⇒ **15 s effective
resolution out-of-the-box**, with no FE-side configuration (approver B1 + leader ruling). No
pre-encoded-`Response` precedent is introduced (that would be a new serialization surface). This is the
2026-10-26 event-loop incident class (sync DB / heavy work on the loop)
explicitly designed out — see risks P-R1 and P-R15.

---

## Gate doctrine application (tiered-gate doctrine, `.agents/tester/rules/ensure.md` + `.agents/tester/skills/`)

**Mechanical escalation rule (cited verbatim from the doctrine):** a gate leg whose diff touches
`daemon/graph.py`, `daemon/manager.py`, `daemon/api.py`, `daemon/services/instance_messaging.py`,
`daemon/services/task_processor.py`, `daemon/services/job_queue_service.py` (the list grows only by
amendment) — **or** a promote-blocking leg — is **Tier 2** (whole-tree dual-side, ≤90 min advisory).
Everything else is Tier 1 (scoped dual-side FAILED-list diff over the declared blast radius, ≤30 min
HARD, fail-loud). Tier 0 = docs-only / `.agents/` / test-only diffs (changed-pack PASS).

| Phase | Leg | Tier | Why | `daemon/api.py`? |
|---|---|---|---|---|
| P1 | P1-G1 docs/contract diff | 0 | docs-only | no |
| P2 | P2-G1 additive substrate | 1 | new modules + `checkpoint_metrics.py` + `config.py` (neither is on the mechanical list) | no |
| P3 | P3-G1 route+wiring | **2** | `daemon/api.py` lifespan wiring ⇒ mechanical escalation | **YES** |
| P3 | P3-G2 scope/class cages | 0/1 | test-only pin bumps — **the declared-route census 108→111** (`test_router_scope_manifest.py::test_declared_route_count_pinned`, :333-396; AM-21 corrected the earlier docstring target 34→37, which was vacuous) | no |
| P4 | P4-G1 W1 cluster (F0.3/F1/F3/F4/F5/F6 — **F2 removed**, AM-25/LR-2) | 1 | additive wiring module; the F0.3 family pack is **conditional on the WS-A ancestry PASS** (AM-24) | no |
| P4 | P4-G2 W2 cluster (F7/F8/F9/F10/F11/F12/F13) | 1 | additive wiring module — **no mechanical-list file is edited** (the C17 docstring fix is DEFERRED per council W5) | no |
| P4 | P4-G3 W3 cluster (F17/F18/F21) | 1 | additive wiring module | no |
| P5 | P5-G1 FE pack + e2e + **dev-build AOT leg + production-config budget leg** (AM-23) | 1 | `frontend/` only; budgets + outputHashing live only under the production configuration, so the dev build alone checks nothing | no |
| P6 | P6-G1 docs/ledger diff | 0 | docs-only | no |

**D1 / D2 split (per leg, declared up-front — the executor never silently expands or contracts it)**

- **D1 — lane-owned HEAD-only pins**: new packs written by this lane. They must pass at HEAD; their
  absence at BASE is expected; they are neither net-new failures nor fixes.
- **D2 — dual-side comparable set**: the ONLY set carrying the FAILED-digest diff. HEAD runs D1 + D2; BASE
  runs D2 with an identical command shape; the asymmetry is stated in the pack.

Expected node counts are **PROJECTIONS** (AM-22) — the executor re-derives them with `--collect-only -q`
and records the derived numbers; hand-maintained node counts are forbidden (node identities are rebuilt
from the collect-only output). Every figure in the table below is advisory, never a scope contract.

| Leg | D1 packs (lane-owned) | Expected D1 nodes | D2 **registered pack** (dual-side) | Expected D2 nodes |
|---|---|---|---|---|
| P1-G1 | `central_metrics_contract_doc` | ~16 (PROJECTION) | none (Tier 0 — docs-only diff; changed-pack PASS, no dual-side leg) | ~0 |
| P2-G1 | `metrics_query_provider` · `metrics_history_ring` · `metrics_sampler` · `metrics_registry_narrowing` · `metrics_vocabulary_amendment` | ~45 (PROJECTION) | **REGISTERED PACK `metrics_p2_dual_side`** — composes: `test_checkpoint_metrics_registry` (45) · `test_checkpoint_metrics_vocabulary` (18) · `test_metrics_gate` (35) · `test_metrics_listener` (29) · `test_metrics_listener_registry` (15) · `test_metrics_census` (34) · `test_metrics_managed_keys` (65) · `test_metrics_live_managed_values` (15) · `test_metrics_refresh_path` (15) | ~271 (PROJECTION) |
| P3-G1 | `metrics_query_routes` · `metrics_query_auth_matrix` · `metrics_query_pagination` · `metrics_query_switch_invariance` · **`metrics_query_api_wiring`** (the D1 home of the lifespan wiring test — approver N-b) | ~40 (PROJECTION) | **REGISTERED PACK `metrics_p3_dual_side`** — composes: the `metrics_p2_dual_side` constituent set + `test_metrics_route` (route pack, base-comparable subset) + `test_metrics_api_wiring` (12) | ~300 (PROJECTION) |
| P4-G1..G3 | `metrics_family_<family>` (per family) + `metrics_family_adjudication` (one per cluster) — **F2's pack is gone** (AM-25) | ~24 per cluster (PROJECTION; aligned with `phase4-plan.md` P4-G1) | **REGISTERED PACKS `metrics_p4_w1_dual_side` / `metrics_p4_w2_dual_side` / `metrics_p4_w3_dual_side`** — each composes: the vocabulary pack (18) + the registry pack (45) + the changed family's own base pack | ~63 per cluster (PROJECTION) |
| P5-G1 | `metrics-fe-models` · `metrics-fe-service` · `metrics-fe-svg-chart` · `metrics-fe-page` · `metrics-fe-bindings-pins` | ~35 (PROJECTION) | **REGISTERED PACK `metrics_fe_dual_side`** — composes: the `resources-gear-menu` ordering pin + the FE pack affected by the route table | ~20 (PROJECTION) |
| P6-G1 | `central_metrics_ledger_reconciliation` | ~12 (PROJECTION) | none (Tier 0 — docs-only diff; changed-pack PASS, no dual-side leg) | ~0 |

**Mutation labelling.** Every behavioural pin is planted independently and shown RED→GREEN. Labels are
`P{n}-Mn` (phase-scoped). The mutation→test table lives in `.agents/tester/PACKS.md` (v1.0.2 convention) —
stubs are pre-declared per phase file (the executor fills the observed digests).

**Slicing proof.** Before any FAILED-list comparison, the executor produces a `--collect-only -q` slicing
proof showing the selected node set equals the declared pack scope (no silent over/under-selection).

**Pre-exclusion registry (declared per leg, applies to every leg above):**
`regression_performance` (>300 s) · e2e specs that need a live daemon on `:8079` (→ Release Gate) ·
`tests/packs/g7` (module-level `sys.exit` collection trap — run via packs, never bare pytest).

**Expected-skip census (AM-22)** — declared per leg, because an unexplained skip is indistinguishable
from a silent pass: the `no_xdist` self-skip in `tests/conftest.py:323-336` · PG-gated packs that skip
without `PG_TEST_ALLOWED_DBS` · any pack whose skip count differs between the HEAD and BASE legs. A skip
delta between legs is a **finding**, not a convenience.

**Tier-2 execution provisions (AM-22 — these are part of the P3-G1 leg's contract, not optional):**
- **Hang-class clause.** If a shard survives its cap with **no FAILED digest** (the v0.27.11 async-hang
  class), the leg is not a pass and not a fail: apply the **v0.27.11 compensating-evidence recipe** —
  scoped net-new EMPTY + HEAD digest sha pins + the preserved replay recipe — and record it as a
  **disclosed compensating evidence**, never as a green leg.
- **Budget reported against precedent.** The ≤90 min advisory is reported against the **137-min
  advisory-overrun precedent** (a real Tier-2 overrun in this repo's history), so an overrun is reported
  with its cause rather than silently re-scoped.
- **Projections stay projections.** Node counts are re-derived, never hand-maintained (above).
- **Scope-faithfulness.** The D2 set is the declared composition; the executor never silently expands or
  contracts it, and a scope change is reported with its reason.

**D2 sets are REGISTERED PACKS (W9 — leader ruling).** Every D2 set above is registered in
`.agents/tester/PACKS.md` under a stable pack id and is invoked **by pack id**, never as an ad-hoc pytest
command list. The registered ids are: `central_metrics_contract_doc` (P1-G1) · `metrics_p2_dual_side` · `metrics_p3_dual_side` ·
`metrics_scope_cage_dual_side` · `metrics_p4_w1_dual_side` · `metrics_p4_w2_dual_side` ·
`metrics_p4_w3_dual_side` · `metrics_fe_dual_side` · `central_metrics_ledger_reconciliation`. Each pack
declares its constituent node scopes, the D2/base-comparable set, and the shard/timeout shape; a leg whose
D2 set is not yet registered is **not runnable** (the leg declares the pack id it will register).

**Mandatory recipe load (W10 — ensure.md §Gate Tiers).** Before executing a leg, the recipe skill body is
READ from its deterministic path (never `skill_search` discovery):

| Leg | Tier | Recipe (READ before running) | Digest comparison |
|---|---|---|---|
| P1-G1 | 0 | changed-pack PASS (no regression leg) | n/a |
| P2-G1 | 1 | `.agents/tester/skills/blast-radius-leg/blast-radius-leg.md` | `failed-digest-equality-gate` |
| P3-G1 | 2 | `.agents/tester/skills/whole-tree-base-proven-diff/whole-tree-base-proven-diff.md` **or** `.agents/tester/skills/regression-ab-partition-run/regression-ab-partition-run.md` | `failed-digest-equality-gate` |
| P3-G2 | 0/1 | `.agents/tester/skills/blast-radius-leg/blast-radius-leg.md` (changed scope) | `failed-digest-equality-gate` |
| P4-G1..G3 | 1 | `.agents/tester/skills/blast-radius-leg/blast-radius-leg.md` | `failed-digest-equality-gate` |
| P5-G1 | 1 | `.agents/tester/skills/blast-radius-leg/blast-radius-leg.md` | `failed-digest-equality-gate` |
| P6-G1 | 0 | changed-pack PASS (no regression leg) | n/a |

`failed-digest-equality-gate` is loaded from `.agents/tester/skills/failed-digest-equality-gate/` on every
leg whose FAILED lists are compared (the BARE-ID vs GREP-FULL form). The node-cap + measured-rate
projection guard runs before every leg (projected OR actual breach ⇒ STOP + report + one re-scope).

**Packs, never bare pytest.** Every leg names its `.agents/tester/PACKS.md` pack; bare `pytest <file>` is
not an acceptable gate invocation.

---

## Disagreement & adjudication flags (requirements ↔ decisions ↔ catalog ↔ dispatcher)

These are recorded, **not silently resolved**. The plan default is stated; the architect adjudicates.

| # | Disagreement | Sources | Plan default (binding unless overridden) |
|---|---|---|---|
| **D-1** | Trend cadence key — **ADJUDICATED (AM-15): CONFIRM-WITH-AMENDMENT**. The key stays the sampler **TICK** (`limits.metrics_sampler_interval_s`, boot-applied); the envelope's `window.interval_s` must report the **observed effective spacing** `max(sampler, publisher)` (15 s at defaults) — the earlier implicit "interval_s = configured sampler cadence" is OVERTURNED; and the ring evicts **by age** so the 2 h window stays truthful under any cadence change. | requirements.md FR-C7 vs decisions.md D3 | **D3/AM-15** — `metrics_sampler_interval_s` as the tick; `interval_s` observed; age eviction as the structural backstop. The key is boot-applied ⇒ a cadence change needs a restart (disclosed, risk P-R5). |
| **D-2** | Series budget arithmetic — **ADJUDICATED (AM-26): CONFIRM + AMEND.** 18 objects / 38 closed (15-obj) / 217 / 79 / +296 confirmed as the *plan's* arithmetic; D5's printed **78 / +295 / ≈384 is OVERTURNED** (its own rows summed to 76). The baseline **89** requires the catalog §14 **F7c reconciliation FIRST** (prerequisite), and conditional rows are mandatory. **Reconciliation outcome (folded here):** §1's canonical table carries an explicit **F7c row (2 series)** and its own trail sums to **582** (+ the `+2 (F7c)` term; §1 phase row-sum P1b 265), while §14.1's total row enumerates "F0.1–F21 + F7b" (F7c **absent**), §14.2's P1b row is **263** and its trail omits the `+2 (F7c)` term, and §14.3 has no F7c row ⇒ **582 WINS; the ledger rows are stale-by-2** and are amended in the ledger phase. **Both denominators are pinned: 89 (F7c-inclusive) / 87 (ledger-rows basis).** | requirements.md line 93 vs decisions.md D5 vs catalog §1/§14 | **The v2 §5 corrected table governs** (see *Re-derived budget*): **+274 → post-lane 363 ≈18.2 %** after AM-25's F2 drop. The authoritative ledger remains catalog §14 (pointer discipline), amended in Phase 6. |
| **D-3** | F8 `ensemble_checkpoint_prune_runs_total{outcome}`: requirements adjudicate **IN** ("outcome values already produced by the prune sweep — `dry_run`/refusal paths"); decisions D5 adjudicate **OUT** ("no existing outcome counter exists — `checkpoint_prune.py:91` `would_delete_count` is a per-sweep dry-run accumulator, not a run-outcome counter"; would need new increments at prune sites). | requirements.md Theme B F8 vs decisions.md D5 | **D5's OUT** (AD3 binds D5 as the subset). Re-admitting it = +4 series and a new increment at a prune site (rule-3 breach) ⇒ needs architect ruling + budget amendment. |
| **D-4** | F21 phase families: requirements gap #6 leans "derivable by scanning the journal history (pull)"; decisions D5 marks them **CONDITIONAL** ("IN only if journal history entries already record phase transitions enabling pure-read derivation — plan-time verify"). | requirements.md gap #6 vs decisions.md D5 | **Conditional, resolved OUT at plan time** — evidence: `journal_history_append` (`upgrade_journal.py:392-405`) writes `{ts, event, detail}` where `event` is the **alert-class** kind set (`halt`/`refusal`/`rollback`/`sweep_rollback`/`quarantine` — `upgrade_journal.py:645-649`), and the promote **phase** trail lives in journal *extension fields*, not in `history[]`. No structured phase field ⇒ no clean pure-read derivation ⇒ **+30 series NOT claimed**. AD4(b) keeps the conditional visible; architect confirms. |
| **D-5** | F18 scope: requirements adjudicate **IN (full)** (9 series, `session_total{outcome}` included); decisions D5 adjudicate **PARTIAL 2/9 + conditional** (`session_total` OUT — refusals/expiries are protocol errors, not aggregated counters). | requirements.md Theme B F18 vs decisions.md D5 | **D5's PARTIAL**, with AD4(a)'s amendment applied (see *Re-derived budget*): `sessions` + `tombstones` + `audit_total{outcome}` with the vocabulary **amended**. `session_total{outcome}` stays OUT. |
| **D-6** | F18 amendment target: AD4(a) says "amend the frozen vocabulary + bump" but does not name the target set. The **declared constant set** is 4 (`audit.py:53-56`); the **written** set is **3** — `OUTCOME_DENIED` is defined and exported but **never written** (`service.py:2191-2216` writes `ok`/`error`/`timeout`; `denied` appears only in the constant and the docstring). | AD4(a) vs `daemon/services/mcps_gateway/audit.py` | **Amend §8.10 to the observable written set `{ok, error, timeout}`** (3). `timeout` is preserved as its own label — never folded into `error` (that is AD4(a)'s explicit prohibition). `denied` is a declared-but-unwritten constant ⇒ excluded (a frozen vocab must not over-promise; same defect class as F7) — if a writer starts emitting it, it rides a future vocabulary bump. |
| **D-7** | F7 narrowed enum membership: catalog §14.4 pins "checkpoint op (4): `aget`, `aput`, `adelete`, `alist`"; the decisions D5 fold says "narrow to the catalog-declared observable set (membership re-derived from §14.4 + the observable production set — `aget` + reserved `other` are the only producers today; `alist` is migration-only per the help text)". The two derivations disagree, and §14.4's row also drops the reserved fold target `other`. | catalog §14.4 vs decisions.md D5 fold | **Plan default: `{aget, other}`** (2 values). Rationale: `aget` is the only observed producer (`persistence.py:632`); `other` is the reserved fold target (`checkpoint_metrics.py:533-549` folds disallowed values to it) and must stay declarable or the fold target is itself out-of-vocab; `aput`/`adelete` have **no producer** (they arrive with the J-4 write hook); `alist` is documented migration-only. §14.4's row must be **amended in Phase 6** to match. Architect may rule `{aget, alist, other}` (3) or the catalog's 4 — either way the §14.4 amendment and the vocabulary-pack re-derivation ride with it. |
| **D-8** | FR-E5 (FE renders a "metrics disabled" state when the kill-switch is OFF) vs D1 (the query routes never consult the switch, so data flows in both switch states). | requirements.md FR-E5 vs decisions.md D1 | **Resolution:** the FE empty state is **availability-driven**, not switch-driven: the envelope's `activation` block + per-family `availability` produce the panel ("no metric families are live" + pointer to the activation setting). No false error banner (AC-E.3) and no false "disabled" banner. |
| **D-9** | FR-F4's conditional "api_families bumped to 34 **if** a new `/api` family" vs D4's Option A — **ADJUDICATED (AM-21): the TARGET is OVERTURNED.** The real pin is the **declared-route census `108→111`** (`test_router_scope_manifest.py::test_declared_route_count_pinned`, :333-396, `assert len(declared) == 108`); the "metrics_router (34 unconditional)" string at :298-300 is a **docstring narrative**, so the earlier 34→37 target was **vacuous** (P3-M7 would have passed while the real pin went red). The mount census stays **37** (static); `api_families` stays **33**. | requirements.md FR-F4 + gap #7 vs decisions.md D4 vs the tree | **D4's Option A** with AM-21's corrected target: `api_families` **33** (unchanged), mount census **37** (unchanged), **declared-route census 108→111**. |
| **D-10** | Requirements assumption #3 ("`VOCABULARY_VERSION=1` … no bump needed for v1 unless vocabularies change") vs AD5 (bump 1→2 rides this lane). | requirements.md assumption #3 vs AD5 | **AD5** — bump 1→2 (F7 narrowing + F18 §8.10 amendment), with the §8 annex update in Phase 6. |
| **D-11 (NEW)** | **(a) AC-A.5 "byte-identical exposition" vs AC-F.4's vocabulary bump** — mutually unsatisfiable: the bump changes ≥3 exposition carriers, so a byte-diff gate reds on arrival. **(b) FR-A7 / FR-D3 / AC-A.6 "Must-empty-when-OFF" on the door vs D1(b) "serves-live-when-OFF"** — phase 3's own pin asserted the opposite of the requirement it cited. | architect v2 §2 D-11 | **ADJUDICATED (AM-16 / AM-17, leader LR-3):** (a) the exposition contract is **structural identity modulo an enumerated 3-carrier value-delta allowlist** (version footer · version gauge · F7 HELP) — any other delta is a gate red; (b) **D1(b) WINS** — the door is never switch-gated; FR-A7/FR-D3/AC-A.6 are **re-scoped to the LISTENER surface** and folded into `requirements.md` commission-style (LR-3); the door's OFF semantics are **availability, not emptiness**. Phase 1 (1.1, 1.6) and Phase 3 (3.7, 3.8) now cite **D-11**, not the overridden requirement rows. |

### Disclosed adjudicated deviations (LR-3 scope — the requirements rows are NOT edited; the architect's ruling governs)

These **eight** (DEV-1…DEV-8) are the rows that remain **genuinely non-foldable** under LR-3's narrow
requirements scope (the council-W2 and approver-B2 fold sites were folded directly; see `requirements.md`
annotations), so they are recorded here rather than edited into the requirements file. Each names the
governing ruling **and the exact rows it still covers** — DEV-5 and DEV-6 were narrowed by the council-W2
fold; DEV-7 and DEV-8 were added by the approver-B4 / approver-note rounds.

| # | Deviation | Governing ruling | Why it is not folded into `requirements.md` |
|---|---|---|---|
| **DEV-1** | `requirements.md` Theme B still adjudicates **F8 `ensemble_checkpoint_prune_runs_total{outcome}` as IN** ("outcome values already produced by the prune sweep"). | **D-3 CONFIRMED: OUT** — `checkpoint_prune.py:91` `would_delete_count` is a per-sweep dry-run accumulator, not a run-outcome counter; wiring it needs a new increment at a prune site (rule-3 breach). | LR-3's scope is the AC-A.5 / FR-A7 / AC-A.6 / FR-D3 re-scopes; the Theme-B table is a different section and stays as written. Re-admitting F8 = **+4 series** + a budget amendment. |
| **DEV-2** | `requirements.md` Theme B still adjudicates **F18 as IN (full)** (9 series, `session_total{outcome}` included). | **D-5 CONFIRMED: PARTIAL** — `sessions` + `tombstones` + `audit_total{outcome}` (amended vocabulary, D-6); `session_total{outcome}` stays OUT. | Same LR-3 scope reason. The plan and `decisions.md` carry the PARTIAL verdict; the requirements row is a disclosed deviation, not a silent conflict. |
| **DEV-3** | `requirements.md` FR-C7 still says the trend cadence defaults to `limits.metrics_scrape_interval_hint_s` (15 s, live-adjustable). | **D-1 CONFIRM-WITH-AMENDMENT (AM-15)** — the tick is `metrics_sampler_interval_s` (10 s, boot-applied); `interval_s` is the observed effective spacing; age eviction keeps the window truthful. | FR-C7 is not inside LR-3's re-scope list. The requirements row is left as written and the deviation is disclosed here + in `decisions.md` D3 (which is the contract). |
| **DEV-4** | `requirements.md` Theme B still adjudicates **F2 as IN (full)** (22 series). | **AM-25 / LR-2 CONFIRMED: F2 is OUT of v1 by VALUE ruling** (prod is Darwin-primary ⇒ darkness is Certain). | Theme B is outside LR-3's re-scope list. The architect's ruling governs; the row is left as written and disclosed here. |
| **DEV-5** | `requirements.md` **C-10** ("subset lands ≈367 daemon-origin (≈18.4 %)") and **NFR-5** ("subset lands ≈18.4 %") still carry the pre-fold budget numbers. **NARROWED by the council-W2 fold** (the third copy — the Theme-B series-budget check paragraph — is now folded directly and annotated). | **AM-26 CORRECTED: +274 → post-lane 363 ≈18.2 %** (F7c-inclusive; 361 on the ledger-rows basis). | C-10 and NFR-5 are outside LR-3's re-scope list, so those two rows are left as written and disclosed here; the third copy (the series-budget check) is folded. |
| **DEV-6** | `requirements.md` **FR-E5** ("a distinct \"metrics disabled\" empty state") and **NFR-9** ("200-empty ⇒ \"metrics disabled\" panel") still name the old copy. **NARROWED by the council-W2 fold** (AC-E.3 — the acceptance criterion that carried the stale copy AND the stale kill-switch-OFF trigger — is now folded directly and annotated). | **AM-19(d): the copy is "no metrics available yet"** — "metrics disabled" was itself the false banner D-8 forbids. | FR-E5 and NFR-9 remain outside LR-3's re-scope list, so those two rows are left as written and the copy fix is disclosed here + in Phase 1 (1.3) and Phase 5 (5.7). |
| **DEV-7** | `requirements.md` **FR-D2** still says the two-class kill-switch rule "SHALL be the governing evidence for choosing the posture". | **AM-19 (architect) + D1's demotion**: the rule is a **supporting precedent**; the load-bearing basis is intra-family consistency + the verified two-site blast-radius census. The demotion reasoning was **upheld** by the approver; only the disclosure was defective. | **Disclosure-only by instruction** — FR-D2's own wording is outside every re-scope family (LR-3 / W2 / B2), so the row is NOT edited; the plan set states one thing (see §Research insights + `decisions.md` D1) and the deviation is disclosed here. |
| **DEV-8** | `requirements.md` **FR-A4** says the interface "SHALL publish a contract version (semver)", while the frozen contract publishes an **integer** `schema_version` (envelope-shape axis) plus a separate integer `vocabulary_version` (label-vocabulary axis). | The plan set's two-axis integer scheme stands (AM-7's bump policy needs a discrete shape version, and `vocabulary_version` is a separate axis by construction). | **Disclosure-only**: FR-A4's wording is outside every re-scope family (LR-3 / W2 / B2), so the row is NOT edited — amending it would repeat the defect class the approver flagged at B4. The requirement is satisfiable read as "a versioned contract"; the ledger records the phrasing gap. |

---

## Re-derived budget (AD3 — re-derived at plan time, pinned)

**Derivation method.** Catalog-convention series (the catalog §14 ledger's own counting rule) is used for
the budget line because the ≤2000 cap is tracked there; the registry-native label-space count is reported
separately because that is what the `live_series_count` field exposes. Both are stated so the architect can
see the convention gap instead of inheriting a silent one.

**AUTHORITATIVE TABLE (architect v2 §5, post-AM-25).** ONE number per quantity — the superseded plan
figures appear only in the correction notes below the table.

| Quantity | Value | Derivation |
|---|---|---|
| Registered objects today | **18** (15 import-time + 3 lazy listener) | programmatic: `len(cm._METRICS)` == 15; `metrics_listener.ensure_f0_1_registered()` adds 3 |
| Registry-native closed label-space | **38 over 15 objects; 42 over 18 live** — **both denominators pinned** | programmatic: Σ Π len(`allowed_values[key]) over the 15 import-time objects; the two guard counters (`cap_rejected{metric}`, `invalid_label{label,metric}`) are **open-ended** and excluded from the closed sum |
| Catalog-convention baseline | **89 (F7c-inclusive) / 87 (ledger-rows basis)** — **both denominators pinned** | the F7c reconciliation (above): §1's canonical table = 582 basis; §14's ledger rows were stale-by-2 ⇒ the registered-set baseline is 89 on the §1 basis and 87 on the §14 basis |
| Extension delta — full-IN | **+217** (**ten** families; **F2 was adjudicated IN (full, 22 series)** — its removal changes the full-IN family set **11→10**; it was NOT a partial) | F0.3 8 · F1 9 · F3 6 · F4 8 · F5 3 · F6 15 · F9 53 · F10 6 · F12 69 · F17 40 (catalog §1 rows, pointer discipline) |
| Extension delta — partial-IN | **+57 (6 families)** | F7 +10 (21→31) · F8 4 · F11 16 · F13 19 · **F18 5** (sessions 1 + tombstones 1 + audit_total{ok,error,timeout} 3 — D-5/D-6 applied) · F21 3 |
| **Total extension delta** | **+274** | 217 + 57 (F2's 22 is **not** included — AM-25/LR-2 drop) |
| **Post-lane registered (catalog convention)** — **the PINNED PROJECTION** (the shipped constant `adjudication.CATALOG_LIVE_ESTIMATE_SERIES_PROJECTION = 363` — the A-6 rename of the old `BUDGET_SERIES` (the value is unchanged; the old name read like an enforced budget). It is a **CATALOG LIVE-ESTIMATE PROJECTION**, not a machine-enforced bound, and it must **never** be compared with the **measured closed label space** (a different convention — a live estimate vs a ceiling)) | **363 ≈ 18.2 %** (F7c-inclusive) · 361 (ledger basis) · 364 (if D-6's 4-value F18 audit set is ever admitted) · 355 (if WS-A is absent and F0.3 defers, −8) | 89 + 274 |
| **Bytes/point** | **16 B (array)** — the deque form was rejected at **129–161 B measured** | AM-9: `array('d')` values + `array('q')` timestamps, head/tail ring index, O(1) drop-oldest |
| Buffer worst case | **11.4 MiB** (750 k points, array) vs FR-C4's 32 MiB ceiling | 2.8× under the Must ceiling; 4.2× under the superseded 48 MB deque envelope |
| Effective point interval | **max(publish 15 s, sampler 10 s) = 15 s**; observed 15–25 s | AM-15: reported `interval_s` is OBSERVED, not configured; age eviction is the backstop |
| Points per 2 h window | **≈480** (a 720-deep count-bound would retain 3 h) | AM-10: count-bound + age eviction together |
| Live histogram series | **3** (op_seconds, read_size_bytes, exposition_seconds) | AM-9: not "~7-9"; histogram point = `(ts f64, count i64, sum f64, buckets i64[N])` = 24+8N B (N=17 ⇒ 160 B) |
| Cap that mechanically binds | **per-metric 2000 label-combinations** (`checkpoint_metrics.py:629-664`); the global ≤2000 budget is a **catalog §14 planning convention** | AM-27: the registry cap cannot enforce a global budget; per-family label-space assertions do |
| Fully-wired catalog ceiling | ≈1145 unchanged; registry-native proxy **822** (§14.1) — re-measure at family-pack time | the catalog bound cross-check row |
| Narrowing effect | **−3 label values** on `ensemble_checkpoint_op_seconds{op}` (5 → 2 under D-7's confirmed `{aget, other}`) | all narrowing effects point DOWN |

**Correction notes (superseded figures — recorded once, never used as live values):** the pre-fold plan
printed **+296 / 385 ≈19.3 %** (before AM-25's F2 drop) and decisions D5 printed **+295 / ≈384** (its own
rows summed to 76). Both are OVERTURNED; the table above is the single source of truth.

### The MEASURED figures (Phase 6 close-out, 2026-11-02)

The rows above are the **plan-time re-derivation** — a **projection**, now pinned as the shipped
constant `adjudication.CATALOG_LIVE_ESTIMATE_SERIES_PROJECTION = 363` — the A-6 rename of the old `BUDGET_SERIES` (the value is unchanged; the old name read like an enforced budget). It is a **CATALOG LIVE-ESTIMATE PROJECTION**, not a machine-enforced bound, and it must **never** be compared with the **measured closed label space** (a different convention — a live estimate vs a ceiling).
The rows below are what the lane **MEASURED** against
its own shipped wiring. **They are different quantities and neither replaces the other.**

| Quantity | Measured value | The recipe (reproducible) | Where the detail lives |
|---|---|---|---|
| **The lane's closed label space** (the CEILING the shipped gate measures) | **336** lane series ⇒ **425** against the 89 baseline | the shipped pack's own `_closed_label_space` recipe (a gauge/counter contributes one series per label combination; a histogram contributes `buckets + 2` per combination), applied to `registry.registered_family_metrics()` | the evidence pack §3.1 (the per-family table) |
| **The lane's LIVE count** | **at or UNDER the pinned 363** | three named shortfalls, each independently re-verified: (i) F12's `ensemble_sweep_cycle_seconds` is **OUT** (no sweep service publishes an observed cycle DURATION — publishing the configured interval under an observed name would be a fabrication); (ii) **10 of the 13** sweep lanes render no series (only `watch_reconcile_sweep` / `eligible_pending_sweep` / `orphan_watcher_sweep` expose a `counters()` reader); (iii) F4's manager-pool `max` / `available` render no series (the SQLAlchemy pool exposes neither natively) | the evidence pack §3.2 |
| **The exact live total** | **NOT statically derivable** — it is observation-dependent (which lanes publish at runtime × the observed `(lane, outcome)` combinations). **It is not asserted.** | resolution path: read `live_series_count` from `/api/metrics/families` once the door is live | the evidence pack §13 (the open questions) |
| **What the shipped gate actually asserts** | `BASELINE_SERIES + measured_closed_space <= GLOBAL_SERIES_CAP` ⇒ **425 ≤ 2 000 ✓** — the gate compares the **ceiling** against the **global ≤2 000 cap**, **not** against the 363 planning projection | `daemon/services/metrics_query/wiring/adjudication.py`; the assertion lives in `tests/unit/metrics/test_metrics_family_adjudication.py` | the evidence pack §3.1 |

**The reconciliation in one line.** The **catalog's** 582 / 265 are the catalog's **plan-time
family budget** for the whole program (F0.1→F22); the **lane's** measured count is a **different
number** with a different convention (the closed label space is a **ceiling**; the plan's +274 is a
**live estimate**). The catalog's ledger is amended for **F7c** (582 / 265) — the two quantities
are recorded side by side, each with its provenance, and **neither overwrites the other**.

**Derivation recipe (R-a):** the 89/87 baseline is re-derivable without archaeology via catalog §1
(`:243` total 582 · `:270` trail with the `+2 (F7c)` term · `:251` phase row-sum P1b 265 · `:225` the
F7c row worth 2) versus §14.1 (`:1796` — "F0.1–F21 + F7b + F7c" after the Phase-6 amendment) / §14.2
(`:1870` P1b **265**) / §14.3 (`:1880` — the F7c row) ⇒ **582 wins; the ledger was stale-by-2 and
is now amended**. The registered-today set F0.1 16 + F0.2 3 + F0.4 1 + F7-today 21 + F7b 46 + F7c 2 =
**89**, and that set minus F7c's 2 = **87**.
**The full recipe is now written out as a standalone step in the catalog's §14.1** ("The standalone
derivation recipe — the 89 / 87 baseline (R-a; reproducible WITHOUT archaeology)") — the four numbered
steps, the two published denominators, and the three-quantity non-conflation table. It was
**implemented in Phase 6 task 6.2** (catalog §14.1/§14.2/§14.3/§14.4 + §8's freeze marker + §7's
roll-up; the correction entries are §11 **C97** (the F7 op narrowing) · **C98** (the F18 audit
outcome) · **C99** (the F7c ledger reconciliation)).

**Pin (as planned, now discharged):** the implementation lane re-measured the numbers at the
family-pack level (each family pack asserts its own label-space `live_series_count`), and Phase 6
updated catalog §14 with the pointer-disciplined delta. **No family pack measured outside its
declared band** ⇒ no stop, no report, no band widening. The measured figures are recorded above
under *The MEASURED figures* and in the evidence pack §3.

---

## Risk register

### Carried from `architecture-recommendation.md` §Risk Register

| # | Risk | Likelihood | Impact | Mitigation / disposition |
|---|---|---|---|---|
| R1 | `api_families` manifest bump churn | avoided by design | — | Query routes extend the EXISTING family; class-manifest stays 33 (verified at `test_router_class_manifest.py:385`). |
| R2 | Scope-cage regression (new routes undeclared or mis-scoped) | Medium | Gate red (loud) | All three routes carry the pinned `maintenance:read`; route-count pin bumped in the same commit; the rogue-router negative leg already proves undeclared-mount refusal. |
| R3 | AOT-only template errors (no repo-level AOT gate) | Medium | Broken page found late | Mandatory `ng build --configuration development` leg after template-touching commits; it is a Phase-5 exit criterion. |
| R4 | Kill-switch truth-table regressions — **the pin is CORRECTED (AM-18)** | Low | Silent semantic drift | **ZERO** new consultation sites (D1): the check-site census over `daemon/` stays at exactly **2**. The corrected invariance claim is narrower and exact: **auth behaviour is byte-identical ON/OFF** (status codes, error vocabulary, scope decision, fail-closed seam); the **registry-sourced series set is identical** for every family whose availability reason is not switch-derived; and the ONLY permitted body differences are (i) F6 `availability` → `census_gated`, (ii) `activation.census_enabled` → false, (iii) `last_publish_age_s` growth — all by design and excluded from the claim. `census_gated` is sourced from the public `CensusSampler.gated` property — **created by phase-2 task 2.4a** (a read-through of the existing private `_gate()`; **no new behavioural check site**, so the census over `daemon/` stays == 2) — reading the EXISTING gate site, so **the query package never reads the switch** (no third site; the grep pin passes). The 35 gate + 29 listener + 34 census tests stay untouched. |
| R5 | FE bundle/style budget | Low | Build warn/error | No new deps (D2); ONE shared primitive (no duplication); per-component SCSS < 8 kB warn; palette from `styles.scss:1-19`. |
| R6 | Cardinality drift from new families — **enforcement wording corrected (AM-27)** | Low | Cap rejections (visible, counted) | `DEFAULT_CARDINALITY_CAP=2000` is a **PER-METRIC** label-combination cap (`checkpoint_metrics.py:629-664`) and **cannot** enforce a global budget; the global ≤2000-series budget is a **catalog §14 planning convention**, enforced by the per-family label-space assertions in the family packs. +274 toward the convention; the registry's per-metric cap + `ensemble_metrics_cap_rejected_total` remain the safety net for any single metric; `live_series_count` makes drift observable per family. |
| R7 | History-buffer memory underestimate — **representation amended (AM-9/AM-10)** | Medium | Memory pressure | The deque form was **rejected on measured evidence** (129–161 B/pt ⇒ 92.6–115.0 MiB at 750 k points = 3.6× over FR-C4's 32 MiB ceiling); the **array-backed** ring is 16 B/point ⇒ **11.4 MiB** worst case. Depth is bounded by a **registry-native** divisor (42 label-combinations today, not the catalog count) **and** evicted by age, so the 2 h window stays truthful under any cadence change. A unit pin asserts **measured** bytes (not a nominal constant). |
| R8 | Sampler-thread lifecycle bugs (wedge/shutdown) | Medium | Stranded thread / lost data | Daemon thread + detached-join discipline (listener `stop()` precedent); the sampler reads published refs only — a wedged loop cannot block it; lifespan teardown ordering pinned by a wiring test. |
| R9 | F18 audit-vocabulary drift | **resolved at plan time** | Wrong label set if folded silently | D-6 resolves it: amend §8.10 to `{ok, error, timeout}`; never fold `timeout` into `error`. |
| R10 | F2 would ship series permanently dark on prod (Darwin) | **Certain** without the ruling | Perceived dead code | **Avoided** — F2 is **dropped from v1** (AM-25 / LR-2); the value ruling removes the permanent-darkness debt instead of documenting it. The **dark-with-reason** infrastructure still ships for F6 (`census_not_running`) and F0.3 (`series_absent`), so a future F2 re-admission needs no new FE work. Flip condition recorded verbatim (AD4(c)). |
| R11 | Registry-adjacent drift | Low | Tier escalation surprise | This lane edits **none** of `checkpoint_adapter.py` / `checkpoint_perf.py` / `readiness.py` — only `checkpoint_metrics.py` introspection + `checkpoint_metrics.py` enum narrowing + `config.py` description. |
| R12 | Producer hot-loop cost of F12 accumulation | Low | Loop-lag regression | Accumulation lives in the metrics pull layer (reads `counters()` dicts); no producer edits; a 10 s cadence bounds read frequency; the registry observe path is untouched. |

### Architect risk deltas (v2 §8 — folded as risk rows, each pointing at its amendment)

| Delta | Risk | Severity | Folded into |
|---|---|---|---|
| **M-1** | Tier-2 hang survival (a shard surviving its cap with no FAILED digest) | 🔴 | P3-G1's **hang-class clause** + the v0.27.11 compensating-evidence recipe (AM-22) |
| **M-2** | Byte-contract contradiction (AC-A.5 vs the vocabulary bump) | 🔴 | The **3-carrier value-delta allowlist** (AM-16) + the re-scoped AC-A.5/L4 |
| **M-3** | Pin target (the real cage is the declared-route census) | 🔴 | **108→111** + the re-pointed P3-M7 (AM-21) |
| **M-4** | Family metadata (the "family" had no home) | 🔴 | **Registration-time metadata kwargs** (AM-3) + the new task 2.7b |
| **M-5** | Lifespan start order ("before the listener start" was un-implementable) | 🟡 | Sampler starts **LAST** of the trio; stops **FIRST** (AM-13) |
| **M-6** | FE budget vacuity (budgets live only under the production config) | 🟡 | The **production-config build leg** (AM-23) |
| **M-7** | Disclosure divergence (two disclosure texts drifting) | 🟡 | **Disclosure SSOT in Phase 1**, verified against the shipped constants in Phase 6 (AM-19) |
| **M-8** | WS-A ancestry rc semantics | 🟡 | **rc=1 ⇒ defer + disclose −8; rc ≠ 0/1 ⇒ STOP**; the F0.3 family pack is conditional on a PASS (AM-24) |
| **M-9** | Expected skips | 🟢 | The **expected-skip census** declared per leg (AM-22) |
| **M-10** | Untracked citations (committed docs citing untracked main-checkout paths) | 🟢 | **Vendored copies** in `inventory-vendor/` with a provenance header (AM-24) |
| **M-11** | `/readyz` exclusion | 🟢 | The provider is **NOT** a `/readyz` component (AM-24) |
| **M-12** | FE manifest delta | 🟢 | Declared in the **Phase-6 stage-prep checklist** (AM-23) |
| (W-B) | **FR-C4 breach** (129–161 B/pt vs the 32 MiB ceiling) | 🔴 | **AM-9** array ring, 11.4 MiB |
| (W-B) | **Idle-in-transaction regression** (a persistent adapter connection) | 🔴 | **AM-11** one-shot connect per tick |
| (W-B) | **On-loop stall** (body build + JSON encode on the event loop) | 🔴 | **AM-12** — body **BUILD** off-loop (`db_offload`); the residual on-loop **encode** is bounded by the pin: `max_points` ≤ **240** × ≤ 50 names ⇒ **≤ 12 000 points ≈ 3.6 ms** (W6, re-derived for the raised default — approver B1 + leader ruling) |
| (W-B) | **`census_gated` third check site** | 🔴 | **AM-18** public `CensusSampler.gated`; the query package never reads the switch |
| (W-B) | **Window-truthfulness traps** | 🟡 | **AM-10** age eviction + **AM-15** observed interval |

### Plan-level risks (added by this plan)

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| **P-R1** | **Synchronous DB reads on the request path** — F10/F13/F18 seams are durable-table pulls; a naive wiring would put a DB query behind `/api/metrics/snapshot`. This is the 2026-10-26 incident class (sync DB on the event loop) resurfacing. | Medium | **High** (the lane's own event-loop incident) | The pull adapters run on the **sampler thread**, writing gauges into the registry; the query path reads published state only. Each adapter opens a **one-shot direct `psycopg.connect` per tick** and closes it inside the tick (the census precedent verbatim — AM-11); a **persistent per-adapter connection is forbidden** (pool-in-disguise ⇒ the idle-in-transaction class). Pin: grep the query package for DB/session imports (P2-G1). |
| **P-R2** | **F18 vocabulary amendment ripple** — §8.10, the vocabulary pack, the catalog ledger and the FE model mirror all move together | Medium | Medium | One amendment, four surfaces, one commit cluster; the vocabulary pack re-derives from runtime sources (never hand-listed) and is the authority. |
| **P-R3** | **F7 enum narrowing breaks a frozen-vocabulary consumer** (the vocabulary pack re-derives the op set from runtime sources) | Medium | Medium | Narrowing + bump + §14.4 amendment + pack re-derivation in one cluster (Phase 2); D-7's default keeps the fold target declared. |
| **P-R4** | **AOT-only FE template errors** | Medium | Page broken late | Phase-5 exit criterion + **two build legs**: one `ng build --configuration development` (AOT) **and** one `ng build --configuration production` (the only configuration carrying `budgets` + `outputHashing` — AM-23). |
| **P-R5** | **Cadence semantics + managed-key live-flip TTL gotchas (AM-15)** — `metrics_sampler_interval_s` is **boot-applied** (pinned restart column): a live flip does **not** change the running sampler. | Medium | Test flake / false expectation | **Three disclosures**: (i) the sampler key is boot-applied AND is the `/proc`-samplers' key — a cadence change silently changes count-bound retention; (ii) the publisher cadence is live-adjustable and is the **BINDING** rate; (iii) `interval_s` is **observed**, not configured. The sampler reads its cadence **once at boot** (tested); the live-flip TTL tests stay scoped to the no-restart keys; **age eviction** is the structural backstop that keeps the window truthful under any cadence change. |
| **P-R6** | **Publisher-cadence double-scrape test pattern** — copy-on-publish means a value set after the publish window is invisible until the next publish; a naive test scrapes once and asserts the new value ⇒ false red. | High | False-red churn | The house pattern: `set_publisher_cadence_provider(lambda: 0.0)` + a **double scrape**; every family-pack assertion that touches a just-set gauge uses the double-scrape pattern (documented per pack). |
| **P-R7** | **Cardinality budget** — **+274 series → 363 post-lane ≈18.2 %** (F7c-inclusive; 361 on the ledger-rows basis) | Low | Cap rejections | Pinned budget + per-family label-space assertions; the registry's per-metric cap is the per-metric safety net, not the global budget mechanism (AM-27). |
| **P-R8** | **WS-A precondition for F0.3** — the catalog's F0.3 exit gate BLOCKS unless the WS-A merge (`feature/resource-monitor-flusher-fix@53547b915`) has landed; the per-child attribution is Darwin-dark by design | Medium | **Gate block** | Phase 4 verifies the WS-A ancestry at execution time (ancestry check, not memory); if absent, F0.3 is **deferred out of the cluster** with a disclosed 8-series delta reduction — never silently gated open. |
| **P-R9** | **Scope-cage regression** | Medium | Gate red | Same-commit pin bump (R2). |
| **P-R10** | **FE dependency creep** (someone adds a charting lib "just for the axis") | Medium | Bundle budget + D2 breach | `package.json` diff gate in Phase 5 (no new entries); D2's disclosure names the closure. |
| **P-R11** | **Gate budget creep on the Tier-2 leg** (P3) | Medium | Budget breach (≤90 min advisory) | Declare D1/D2 up-front; declare the pre-exclusion registry; if the leg overruns, the executor reports the overrun rather than shrinking the D2 set silently. |
| **P-R15** | **On-loop body build / JSON encode** (AM-12) — an unbounded payload build/encode would stall the event loop on the request path | Medium | Loop-lag regression (the incident class) | The body **BUILD** runs off the event loop (`await db_offload.offload(...)`, the `/api/work` precedent, `daemon/routers/work.py:181-193`). **AM-12 is BUILD-OFF-LOOP ONLY**: `db_offload.offload` returns a Python object (`db_offload.py:386-392`) and FastAPI encodes it **on the loop** — so the residual is bounded and pinned: `max_points` ≤ **240** × `?names=` ≤ 50 ⇒ **≤ 12 000 points ≈ 3.6 ms** on-loop (re-derived for the raised default — approver B1 + leader ruling). **No pre-encoded-`Response` precedent** is introduced (a new serialization surface). |
| **P-R12** | **Version-slot discipline** — an implementation lane mints a version before staging | Low | Ceremony confusion | This plan names no version; W9 reserves derivation to giter at stage time. |
| **P-R13** | **F21 conditionality misread** — an implementer pulls phase counts from `history[]` and ships a wrong number | Medium | Wrong series | D-4 verdict (OUT) + a pin that asserts the F21 phase families are **absent** from the registry. |
| **P-R14** | **Sampler-thread + DB-pull interaction — pattern CORRECTED (AM-11)** | Low | Pool pressure / idle-in-transaction | The census precedent is a **one-shot `psycopg.connect` per tick, closed inside the tick** (`metrics_census.py:226-240`, `:418-432`) — **not** a persistent per-adapter connection (which is a pool in disguise and recreates the idle-in-transaction class cured in the DB-pool lane). Each adapter uses connect_timeout + `application_name` + statement_timeout and closes before the tick ends; a pinned assertion forbids a persistent connection. |

---

## Success criteria

### Per phase (measurable end-states)

| Phase | Criterion | How to measure | Threshold |
|---|---|---|---|
| P1 | Posture contract + envelope frozen; **zero** config/default changes proposed | grep the P1 diff for `config.py` / `metrics_enabled` / managed-key edits | 0 hits; the posture doc states all three surfaces' OFF semantics + the switch's complete check-site census |
| P2 | Provider answers families/snapshot/history from **published state only**; ring stays under budget; enum narrowed; version bumped | unit packs + a mutated pin proving the provider never touches a live lock | provider pack green; ring peak ≤ POINT_BUDGET; `VOCABULARY_VERSION == 2`; narrowing pin RED→GREEN |
| P3 | The three routes exist under `/api/metrics`, auth-gated, with the cages green | route pack + `test_router_scope_manifest` + `test_router_class_manifest` | 200 with scope · 503 `AUTH_NOT_CONFIGURED` auth-off · 403 scope miss · 401 auth-on-no-session · **declared-route census == 111** · **mount census == 37** · **class manifest == 33** (the three censuses are distinct; AM-21) |
| P4 | **16** families registered; OUT metrics (incl. F2) absent from the registry **and** from `/metrics` | per-family packs + a render diff + the adjudication pack | every family pack green; the OUT-metric absence pin green; budget re-measured within the pinned band |
| P5 | FE charts page renders from the door with zero new deps; **both** build legs green | `ng build --configuration development` (AOT) **and** `--configuration production` (budgets) + jest + Playwright | build 0 errors · `package.json` diff = 0 dependency entries · the OFF-state panel spec green · the polling teardown pin green |
| P6 | Ledger/docs/evidence closed; stage-prep checklist complete; **no version, no promote** | doc diff + the ledger reconciliation check | catalog §8.10/§8 annex/§14.4/§14 amended; the plan-file manifest matches the tree; the stage-prep checklist has every item ticked **except** version+promote |

### Lane-level (the lane is done when)

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| L1 | The lane is **staged, not promoted** | the stage journal + the symlink | no promote performed; the live release untouched; no live promote command run in the lane |
| L2 | Both input lineages are cited in the shipped docs | grep the docs + evidence for both path families | `metrics-unification` (main-checkout, untracked) **and** `metrics-collection` (committed) both cited |
| L3 | Catalog names are used **verbatim** | diff the registered names against catalog §1 | 0 renames; any difference is an amendment with a §14 ledger row |
| L4 | Frozen exposition contracts are **structurally stable** | body diff of `:9798/metrics` and `/api/metrics` against the base artifact, with the enumerated 3-carrier allowlist applied (version footer · version gauge · F7 HELP) | identical modulo the allowlist; **any other delta is a gate red** (AM-16 / re-scoped AC-A.5) |
| L5 | Zero new migrations, zero new backend deps, zero new npm deps | `daemon/migrations/versions/` diff · `pyproject.toml` diff · `frontend/package.json` diff | 0 / 0 / 0 |
| L6 | The reveal of the query door does not change the switch's blast radius | the **corrected** switch-invariance pin (AM-18) + the 35 gate tests | auth behaviour byte-identical ON/OFF; registry-sourced series identical for every non-switch-derived family; only the three permitted differences (F6 `census_gated`, `activation.census_enabled`, `last_publish_age_s` growth); the check-site census over `daemon/` == **2**; the existing gate/listener/census packs unchanged |
| L7 | Every gate leg ran with a declared tier, D1/D2 split, slicing proof, pack invocation and mutation table | the tester evidence pack | all legs present; zero bare-pytest invocations |
| L8 | The **F7c reconciliation** is recorded and the ledger is amended | the catalog §14 diff | §14.1 total → **582** ("F0.1–F21 + F7b + F7c"); §14.2 P1b → **265** and the trail +2 (F7c); §14.3 gains the F7c row; **both** baseline denominators (89 / 87) pinned in the docs |
| L9 | The FE ships **two** build legs | the Phase-5 gate | `ng build --configuration development` (AOT, 0 errors) **and** `ng build --configuration production` (budgets + outputHashing; any budget WARN is a reported finding) |
| L10 | The evidence citations do not dangle | the lane's committed-docs inventory | the three inventory files are **vendored** under `inventory-vendor/` with a provenance header (source path, untracked status, evidence ref `origin/latest@62627265f`); no committed doc cites the untracked main-checkout path alone |
| L11 | The query provider is **not** a readiness component | grep the readiness composite + the route wiring | no `/readyz` component references the provider; the soak-gate grace discipline is untouched (AM-24) |

---

## Research insights (the pointers that shaped this plan)

- **The registry has no windowing** — process-lifetime cumulative, no TTL/eviction anywhere (`checkpoint_metrics.py`; research §1a). Hence the in-RAM ring is not optional (AD1/D3).
- **The switch's complete check-site list is two sites** — the listener response body (`metrics_listener.py:449`, the body check inside `_gate` `:408-419`) and the census sampler's DB work (`metrics_census.py:447`, the tick check inside `_gate` `:332-340`). **Anchor convention (approver note):** the *gate method* is cited as `<file>:<method range>` and the *behavioural check site* as `<file>:<call line>` — both forms are correct, and this convention is used consistently across the plan set. `/api/metrics`, the observe path and the read-instrumentation seam do **not** consult it (research §1c/§1d). D1's posture follows from this census, not from preference.
- **The `:9798` listener is the only unauthenticated surface** (loopback-only enforced in-constructor, `metrics_listener.py:361-368`) — that is why its default-OFF is the recorded exposure decision and why the query door is auth-only.
- **Copy-on-publish** (`_maybe_publish_locked`, `checkpoint_metrics.py:868-882`; cadence 15 s default, `:128`) means the query path can read published copies lock-free and that a just-set value needs a **double scrape** to appear (P-R6).
- **Registry invariants to preserve** (FR-F2): per-metric locks only, no global lock; render try-acquire 0.05 s/family + 0.25 s total budget; hard cap 2000 with atomic check-then-increment; three-tier never-exported enforcement; C-14 `except Exception` (never `BaseException`).
- **The canonical extension seam** is `metrics_listener.py:120-144` — register at module/runtime, canonical `ensemble_` name, declared `label_keys` + `allowed_values` from the frozen vocabularies, buckets from catalog §8.11, handle importable + `__all__`, fail-open increments (C-14).
- **The FE has no charting library** — only mermaid (chat diagrams) and an unpinned mermaid-transitive d3; the load-bearing in-code precedent is the hand-rolled inline-SVG timeline (`svg-timeline.component.ts:17-18`) with pure scale functions exported for unit assertions.
- **The FE page recipe is three integration points** — lazy `loadComponent` route (+`title`), gear-menu entry in `app.ts`'s static array, and ordering pins (`resources-gear-menu.spec.ts` precedent) — plus **two build legs** (`--configuration development` for AOT and `--configuration production` for the budgets, AM-23) — plus a service with static URL constants, `JSON_HEADERS = {Accept:'application/json'}` for auth-gated calls, verbatim model mirrors, `POLL_INTERVAL_MS` pins, and one `ng build --configuration development` leg.
- **`/api/metrics` has ZERO FE consumers today** and agents have ZERO native metrics reads (`git grep 'readiness|loop_lag|pool_state|leak_signal' daemon/tools/` = 0 hits) — the door closes both gaps, which is why it must be a real interface, not a second exposition.
- **The two-class kill-switch rule** (`daemon/config.py:4413-4419`) — "ON for zero-user-action read/serve surfaces, OFF for agent-authored mutation lanes" — is a **SUPPORTING PRECEDENT only** (demoted by AM-19: the rule is scoped to another feature's boot-time knobs and D1(c) rejects the pattern it prescribes). The **load-bearing basis** for D1's posture is **intra-family consistency** (the shipped `/api/metrics` text route is auth-only, so gating three siblings under one prefix would create a same-prefix split-brain) **plus the verified blast-radius census** (exactly two behavioural gate reads). The requirements-side FR-D2 wording is **disclosed as DEV-7** (disclosure only — the requirement row is outside every re-scope family).

---

## Provenance

**Inputs — canonical, read-only, unmodified by this lane:**

| Source | Path | Role |
|---|---|---|
| Wave-0 research digest (3 explorer reports) | `.agents/shared/planning/central-metrics-v1/research-findings.md` | registry internals, kill-switch census, FE ground truth, the 27-family catalog + seams, gate doctrine, known defects |
| Requirements + acceptance criteria | `.agents/shared/planning/central-metrics-v1/requirements.md` | FR-A…G, NFR-1…10, C-1…C-10, AC-A.1…F.4, the Theme-B adjudication table, the Theme-G out-list |
| Technical analysis decisions | `.agents/shared/planning/central-metrics-v1/architecture-recommendation.md` (system view) + `.agents/shared/planning/central-metrics-v1/decisions.md` (the contract: D1–D5 with Evidence/Options/Ruling/Disclosure) | component map, seam inventory S1–S20, phase preview, risk register R1–R12 |
| **Committed lineage** (this worktree, == `origin/latest`) | `.agents/shared/planning/metrics-collection/metric-catalog.md` (1915 L — **§1 is the canonical name registry**, §8 vocabularies, §14 counted-values ledger) · `decisions.md` (§ACTIVE — the ratified constraints) · `plan-overview.md` (954 L) | the canonical registry this lane grows; its §1/§8/§14 are authoritative over every other source on names/labels/counts |
| **Untracked evidence — VENDORED (AM-24)** | `.agents/shared/planning/central-metrics-v1/inventory-vendor/` (`README.md`, `metrics-inventory-backend-producers.md`, `metrics-inventory-surfaces-fe-deps.md`) — each with a provenance header (source path, untracked status, evidence ref `origin/latest@62627265f`, vendoring date) and byte-identical content below the header (sha256 recorded in `inventory-vendor/PROVENANCE.md`) | the inventory that seeded the pull-seam map. **Why vendored:** committed citations of an untracked main-checkout path dangle in every other checkout (M-10). The vendored copies are the citation target; the original stays read-only |
| Untracked evidence — SOURCE (shared main checkout, **READ-ONLY, never modified by this lane**) | `/home/nea/ensemble-src/.agents/shared/planning/metrics-unification/` | the copy source for the vendored files above; still cited by path for provenance, never edited |
| **Architect deliverable (v2 — the fold source)** | `.agents/shared/planning/central-metrics-v1/architecture-recommendation-v2.md` (239 L) | the architect's enriched deliverable: verdict SOUND-WITH-AMENDMENTS, §2 D-1..D-11 adjudications, §3 open-question rulings, §4 AM-1..AM-27 (each with a *Fold:* directive), §5 the corrected budget table, §6 the normative kill-switch truth table, §7 confirmed/overturned, §8 M-1..M-12. **Neither architect file is edited by this lane** (v1 retained for diff provenance) |

**Plan-file manifest (this plan set):**

| File | Purpose |
|---|---|
| `plan-overview.md` | this document — objective, adjudications, scope fences + out-list, phase map, coupling, the frozen contract, gate doctrine application, disagreement flags, re-derived budget, risk register, success criteria, provenance |
| `phase1-plan.md` | Posture, activation & the frozen contract envelope |
| `phase2-plan.md` | Query substrate: provider, ring buffer, sampler, registry narrowing |
| `phase3-plan.md` | Query API routes + manifest cages + lifespan wiring |
| `phase4-plan.md` | Extension-subset registration (W1/W2/W3 clusters) |
| `phase5-plan.md` | FE charts page (zero-dep SVG) |
| `phase6-plan.md` | Docs, evidence & stage-prep closure |
| `fold-ledger.md` | **R-b** — the fold ledger: the 27-row architect amendment table, the W1…W11 council-warning table, R-a/R-b/R-c dispositions, and the noted-not-folded advisory register. **This is the artefact the fresh approver cites.** |
| `architecture-recommendation.md` | architect v1 — **untouched** (retained for diff provenance) |
| `architecture-recommendation-v2.md` | architect v2 — **untouched** (the SOUND-WITH-AMENDMENTS fold source) |
| `research-findings.md` | wave-0 research digest (3 explorer reports) — an input, unmodified |
| `inventory-vendor/PROVENANCE.md` | AM-24 vendor provenance: source paths, untracked status, evidence ref, sha256 table, verification recipe |
| `inventory-vendor/README.md` | AM-24 vendored copy of the inventory's own README (source: the untracked main-checkout inventory; byte-identical below the provenance header) |
| `inventory-vendor/metrics-inventory-backend-producers.md` | AM-24 vendored copy of the backend-producer inventory (byte-identical below the provenance header; sha256 in `inventory-vendor/PROVENANCE.md`) |
| `inventory-vendor/metrics-inventory-surfaces-fe-deps.md` | AM-24 vendored copy of the surfaces/FE-deps inventory (byte-identical below the provenance header; sha256 in `inventory-vendor/PROVENANCE.md`) |
| `inventory-vendor/` (3 vendored copies + `PROVENANCE.md` — listed individually above) | **AM-24 vendored copies** of the untracked metrics-unification inventory, each with a provenance header (source path, untracked status, evidence ref `origin/latest@62627265f`); byte-identical below the header, sha256 recorded in **`inventory-vendor/PROVENANCE.md`** (approver N-d — the hash table lives there, not in the vendored README) |
| `requirements.md` *(folded, plan-set-owned per LR-3)* | the requirements file — the AC-A.5 / FR-A7 / FR-D3 / AC-A.6 / AC-D.1 re-scopes and the FR-A.6 annotation are folded in **commission-style** with provenance |
| `decisions.md` *(folded)* | the technical-analysis decisions record — AM-3/8/9/10/14/15/19/20/21/25/26/27 folded in, plus the new **§D6** (LR-1's named scope-split commitment) |
| `evidence-pack.md` *(Phase 6, task 6.6 — NEW)* | the **evidence pack**: the per-leg results (tier · D1/D2 split · slicing proof · pre-exclusion · expected-skip census), the mutation→test tables with the observed digests, the **measured** budget table, the F7c reconciliation outcome, the WS-A verdict, the F21 conditionality evidence, the AM-16 structural diff, the vendor provenance table, the FE-manifest delta, and a disposition for **every** flag (D-1…D-11) and **every** deviation (DEV-1…DEV-8) |
| `evidence/am16-exposition-base.txt` *(Phase 6 — NEW)* | the **BASE** exposition body for the AM-16 structural diff — the input to the 3-carrier allowlist check |
| `evidence/am16-exposition-head.txt` *(Phase 6 — NEW)* | the **HEAD** exposition body for the same diff |
| `evidence/p4-failed-digests.txt` *(Phase 6 — NEW)* | the **recomputed** P4 FAILED-digests (bare-id + grep-full forms, both legs, all six legs) — preserved so the digests are verifiable without re-running the gate |
| `stage-prep-checklist.md` *(Phase 6, task 6.7 — NEW)* | the **stage-prep checklist**: the three EMPTY deltas (migration · backend dependency · npm dependency), the vendored inventory with its provenance, the declared FE-manifest delta, the untouched live surface, and the two **hand-overs** (the version slot → giter; the promote → the user's nonce ceremony) |
| `docs/architecture/metrics-query-api.md` *(Phase 6, task 6.4 — NEW, outside the plan set)* | the **query API page** — the three routes, the envelope, the error vocabulary, the pagination bounds + the cursor, the availability reasons, the `restart_epoch` semantics, the activation posture + the runbook, the scope over-grant (AM-20) + the LR-1 commitment, the OUT-list with each excluded metric's named seam, the non-Prometheus deviations clause (AM-8), and the Prometheus-text coexistence statement |
| `docs/architecture/metrics-fe-charts.md` *(Phase 6, task 6.4 — NEW, outside the plan set)* | the **FE charts page** — the three tabs, the polling discipline, the chart mode, the window, and the availability-driven states |
| `docs/architecture/metrics-promote-disclosure.md` *(Phase 6, task 6.5 — NEW, outside the plan set)* | the **FINAL** promote-time disclosure text (the eight cross-cutting items, each verified against the shipped constants) + the **ceremony checklist**. It supersedes the Phase-1 draft in `docs/architecture/metrics-query-contract.md` §7, which is retained as the frozen contract's record |

---

## Open questions for the architect — ADJUDICATED (v2 §3 + leader LR-1..LR-3)

| # | Question | Ruling | Where it is folded |
|---|---|---|---|
| Q1 | F7 enum membership | **`{aget, other}`** (D-7 CONFIRMED) — and the **derivation RULE** is recorded so J-4 re-derives rather than re-litigates | `decisions.md` D5 folds · plan-overview flags D-7 · Phase 2 task 2.7 |
| Q2 | F18 amendment target | **`{ok, error, timeout}`** (D-6 CONFIRMED — the observable written set) | `decisions.md` D5 · flags D-6 · Phase 4 task 4.7 · Phase 6 task 6.1 |
| Q3 | F8 / F21 re-admission | **Both stay OUT** (D-3 / D-4 CONFIRMED) — re-admission = a named-seam commission + a budget amendment (+4 / +30) | `decisions.md` D5 · flags D-3/D-4 · disclosed deviations DEV-1 |
| Q4 | FR-E5 vs D1 FE contract | **Availability-driven panel** (D-8 CONFIRMED) with the copy fix: **"no metrics available yet"**, never "metrics disabled" (AM-19(d)) | Phase 1 task 1.3 · Phase 5 task 5.7 |
| Q5 | Which convention governs ≤2000 | **Catalog convention CONFIRMED** — and strengthened: the registry's cap is per-metric (AM-27); the global budget is a §14 planning convention enforced by per-family label-space assertions | *Re-derived budget* · flags D-2 · risk R6 |
| Q6 | P3 Tier-2 D2 scope | **ACCEPT-WITH-AMENDMENTS** — composition scope-faithful; hang-class provisions, 137-min budget precedent, expected-skip census and PROJECTION node counts added | §Gate doctrine (AM-22 block) · Phase 3 whole-tree section |
| (OQ-3) | F2 sequencing | **DROP from v1** (AM-25; leader **LR-2** CONFIRMED) with the flip condition recorded verbatim | `decisions.md` D5 F2 row · AD4(c) · Phase 4 W1 · budget |
| (OQ-4) | Sampler key | **AM-15 synthesis** — the key is the TICK; `interval_s` is observed; age eviction is the backstop | `decisions.md` D3 · AD4(d) · Phase 2 tasks 2.6/2.8 |
| (Pending #1) | `metrics:read` scope split | **NAMED COMMITMENT, not built now** (leader **LR-1**) — v1 keeps `maintenance:read` + AM-20's retrofit map, recorded as binding guidance for the deferred lanes | `decisions.md` **§D6** · §Scope notes · Phase 6 docs |
| (Pending #2) | F2 drop confirmation | **CONFIRMED DROP** (leader LR-2) | AD4(c) · D5 · budget |
| (Pending #3) | requirements ownership | **PLAN-SET-OWNED** (leader LR-3) — the re-scopes are folded directly, commission-annotated; anything non-foldable is a **disclosed adjudicated deviation** (DEV-1..DEV-8) | `requirements.md` (10 edits) · §Disclosed adjudicated deviations |

**Remaining, non-blocking (v2 §3):** the post-lane registry-native ring count (re-measure at family-pack
time; 822 proxy until then) and the exact per-request cost of the largest encode (the off-loop amendment
AM-12 makes the loop question moot). Neither blocks the plan council.

### The FINAL verdict for every open question (Phase 6 close-out, 2026-11-02)

| # | Question | The final verdict — what the lane actually SHIPPED | The evidence |
|---|---|---|---|
| **Q1** | F7 enum membership | **`{aget, other}`** — SHIPPED as the runtime's canonical `CHECKPOINT_OP_VOCABULARY` (2 values). The catalog's §8.9 + §14.4 rows were amended to **2** (§11 **C97**), and the §8 freeze marker now records the 1→2 bump | `daemon/checkpoint_metrics.py:1245`; the catalog §8 + §14.4 |
| **Q2** | F18 amendment target | **`{ok, error, timeout}`** — SHIPPED as the registered label set (3 values, measured from the runtime). The catalog's §8.10 + §14.4 rows were amended to **3** (§11 **C98**) | the measured F18 label set; the catalog §8.10 + §14.4 |
| **Q3** | F8 / F21 re-admission | **Both stay OUT** — CONFIRMED on the evidence: F8's prune outcome counter does not exist, and F21's phase trail lives in the journal's **extension** fields (no pure-read derivation). Their **absence is pinned** (`P4-M1` / `P4-M7`) | the evidence pack §6 + the adjudication table |
| **Q4** | FR-E5 vs D1 FE contract | **Availability-driven panel** — SHIPPED: the panel appears when **no family is live** and reads *"No metrics available yet"*. The forbidden copy "metrics disabled" is **absent** from the shipped copy | `METRICS_EMPTY_PANEL_COPY`; the published FE page §6 |
| **Q5** | Which convention governs ≤2000 | **The catalog convention CONFIRMED** — and the phase sharpened it: the shipped gate asserts the **closed label space** against the **global ≤2 000 cap** (425 ≤ 2 000 ✓), while the **planning projection** (363) is the catalog's live-estimate convention. **Both are now recorded side by side**, and neither is substituted for the other | the evidence pack §3 |
| **Q6** | P3 Tier-2 D2 scope | **ACCEPT-WITH-AMENDMENTS** — discharged: the hang-class provision was met (the whole-tree leg was re-run in the **sharded** form after the single-shot `-n auto` form aborted rc=3 on **both** legs), the expected-skip census is recorded (no skip-count divergence on any leg), and the node counts are the executor's re-derivations, never the projections | the evidence pack §1 |
| **(OQ-3)** | F2 sequencing | **DROPPED from v1** by the VALUE ruling (AM-25 / LR-2), with the flip condition recorded **verbatim** and published in the OUT-list | the published query-API page §6.1 |
| **(OQ-4)** | Sampler key | **AM-15 synthesis SHIPPED** — the key is the TICK; the reported `interval_s` is the **observed** effective spacing (15 s at the defaults); age eviction is the backstop | `constants.py`; the published query-API page §2.3 |
| **(Pending #1)** | the `metrics:read` scope split | **NAMED COMMITMENT, not built now** (LR-1) — the over-grant is **published** in the query-API page §5 with the four-line retrofit map stated as **binding guidance** for the deferred lanes | the published query-API page §5 |
| **(Pending #2)** | F2 drop confirmation | **CONFIRMED DROP** (LR-2) — F2 is absent from the registry and its absence is pinned | the adjudication table |
| **(Pending #3)** | requirements ownership | **PLAN-SET-OWNED** (LR-3) — the re-scopes are folded directly; the non-foldable residue is the **eight disclosed adjudicated deviations DEV-1…DEV-8**, each with a disposition recorded in the evidence pack §11 | the evidence pack §11 |
| **(v2 §3, remaining 1)** | the post-lane registry-native ring count | **CLOSED as far as this lane can close it** — the **measured** closed label space is **336** lane series (the shipped gate's own recipe) against the 822 proxy. The exact **live** ring count is observation-dependent and is **not asserted** | the evidence pack §3.1 + §13 |
| **(v2 §3, remaining 2)** | the exact per-request cost of the largest encode | **MOOT** — AM-12 builds the response **body** off the event loop; the residual on-loop **encode** is bounded at `240 points × ≤ 50 names ⇒ ≤ 12 000 points ≈ 3.6 ms` | the published query-API page §3.2 |
