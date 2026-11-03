# Requirements: central-metrics-v1

Date: 2026-11-01 (UTC)
Author: planner[v2] via requirements-analysis worker (commission: requirements decomposition for central-metrics-v1)
Status: Draft / Ready for Review
**PLAN-SET OWNERSHIP + AMENDMENT LOG (leader LR-3, 2026-11-02):** this file is owned by the
central-metrics-v1 plan set and is folded by it. Amendments folded here are annotated in place,
commission-style, with provenance — architect `architecture-recommendation-v2.md` AM-16 (D-11a) /
AM-17 (D-11b) + leader ruling LR-3:
- **AM-17 (D-11b)** — FR-A7, FR-D3, AC-A.6, AC-D.1 re-scoped to the **LISTENER** surface (the
  query door is never switch-gated; its OFF semantics are availability, not emptiness).
- **AM-16 (D-11a)** — AC-A.5 re-scoped from *byte-identical* to *structurally identical modulo an
  enumerated 3-carrier value-delta allowlist* (the ratified AD5 vocabulary bump changes exactly
  those carriers). FR-A.6 is annotated, not rewritten.
- Rows **outside** this scope are NOT edited. Any residual conflict is recorded as a **DISCLOSED
  ADJUDICATED DEVIATION** in `plan-overview.md` §Disagreement & adjudication flags — never a
  silent edit.
Source Request: Grow the landed default-OFF central-metrics lane into the v1 system — one native query interface over the registry (`daemon/checkpoint_metrics.py`) serving the FE charts page now and future consumers (agent tools item #2, module-onboarding waves, optional stock-Grafana item #3) without rework; extend the 18 already-registered metric objects toward the 27 catalog families using only families that are committed in the catalog AND map to an existing producer/pull seam AND need no new instrumentation at module sites; add recent-trend capability; resolve activation/posture; ship an FE charts page; preserve frozen contracts.

Canonical inputs (read-only, not modified by this lane):
- `/home/nea/ensemble-src-wt-central-metrics-v1/.agents/shared/planning/central-metrics-v1/research-findings.md` (wave-0 research digest, provenance: explorers cd71259f, 4ca94081, 8f71d64f)
- `/home/nea/ensemble-src-wt-central-metrics-v1/.agents/shared/planning/metrics-collection/metric-catalog.md` (v2 — §1 is the CANONICAL NAME REGISTRY; authoritative over every other source on family names/labels)
- `/home/nea/ensemble-src-wt-central-metrics-v1/.agents/shared/planning/metrics-collection/decisions.md` (§ACTIVE — ratified constraints)
- Shared read-only evidence: `/home/nea/ensemble-src/.agents/shared/planning/metrics-unification/` (README.md, metrics-inventory-backend-producers.md, metrics-inventory-surfaces-fe-deps.md)
- Live code anchors verified in `/home/nea/ensemble-src-wt-central-metrics-v1` (branch feature/central-metrics-v1, base == origin/latest).

No live promote anywhere in this lane. Requirements ONLY — implementation plan is owned by a separate worker.

---

## Stakeholders

- **Requester:** user (via planner commission; v1 grow-the-lane scope)
- **Affected users:** daemon operators (Prometheus/Grafana scrape + FE charts page); maintenancer/developer agents (future item #2 — no tool surface in v1, but the interface must anticipate it); FE users (charts page consumers).
- **Affected systems:** daemon registry (`daemon/checkpoint_metrics.py`), exposition listener (`daemon/services/metrics_listener.py`), `/api/metrics` route (`daemon/routers/metrics.py`), FE Angular app (`frontend/`), metric-catalog ledger (§14), router-class manifest (`tests/integration/test_router_class_manifest.py`).

---

## Functional Requirements

### Theme A — Query API: THE ONE DOOR

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-A1 | The system shall expose ONE native query interface over the metric registry (`daemon/checkpoint_metrics.py`) that returns family registry metadata (names, types, labels, help, vocabularies, bucket sets, series counts) for every registered family. | The FE charts page and future consumers (#2 agent tools, #3 Grafana) must bind to one surface; duplicating exposition (listener vs /api/metrics vs a new API) is the drift class the catalog's "one measurement, one name" rule (§0.3) forbids. | Must | Query API |
| FR-A2 | The interface shall return current snapshot values (instant values) for every registered family, with per-label series preserved. | Charts need instant gauge values and counter totals on first render before any trend accumulates. | Must | Query API |
| FR-A3 | The interface shall return recent-trend series for the chart-primary families (Theme C) as bounded time-series with timestamp + value per sample, for a lookback window of at least 60 minutes at no coarser than 15-second resolution. | The registry is process-lifetime cumulative (§1a: "Retention/rollover/windowing: NONE"); charts require windowed recent data, which the registry itself does not provide. | Must | Query API |
| FR-A4 | The interface shall publish a contract version (semver) and a metric-family schema version, and SHALL enumerate the registered families with a stable ID per family so consumers can detect additions/removals. | Future consumers (#2/#3) must be able to detect contract drift without rework; the catalog is frozen per §0.5/§8 + `VOCABULARY_VERSION=1` (`daemon/checkpoint_metrics.py:190`). | Must | Query API |
| FR-A5 | The interface shall be defined as a versioned ABSTRACTION over the registry, not over the concrete module — a backend swap (e.g., future Prometheus remote-read) must not change the contract. | The user's FROZEN interface requirement: storage/exposition backend behind the interface must be swappable; the concrete `checkpoint_metrics` module is an implementation detail. | Must | Query API |
| FR-A6 | Prometheus-text exposition SHALL remain intact and UNCHANGED alongside the query API; the query API is additive, never a replacement. | `/api/metrics` precedent (`daemon/routers/metrics.py:70`, `:91-107`): untagged class ⇒ protected by default (401/403); auth middleware 302-redirects wildcard-Accept browsers. | Must | Query API |
> **[ANNOTATED 2026-11-02 — architect AM-16 (D-11a) + leader LR-3]** Requirement text unchanged; "UNCHANGED" is now read through the AC-A.5 3-carrier value-delta allowlist (the ratified AD5 vocabulary bump changes the version footer, the version gauge and the F7 HELP text — nothing else).
| FR-A7 | **RE-SCOPED to the LISTENER surface (D-11b).** The **exposition listener** shall carry an explicit kill-switch posture: when metrics are OFF, the listener body SHALL be a valid 200 with an empty/absent series set (a real state, not an error), mirroring the listener's OFF semantics (`metrics_listener.py:446-454`); the OFF state SHALL NOT leak `ensemble_metrics_up` or any family series. **The query door is NEVER switch-gated (D1(b) wins): its OFF semantics are AVAILABILITY — per-family `availability.state`/`reason` plus the `activation` block — not emptiness; a switch-OFF door keeps serving live registry-sourced data.** | Catalog §0.8: OFF ⇒ 200 valid-empty body, no `ensemble_metrics_up`, accumulation continues; absent ≠ zero (§0.11). **D-11b (architect v2 AM-17 + leader LR-3):** the original wording was self-contradictory with D1(b) — phase 3's own pin asserted the opposite. | Must | Query API |
> **[RE-SCOPED 2026-11-02 — architect AM-17 (D-11b) + leader LR-3]**
| FR-A8 | The interface shall expose family capability metadata sufficient for future consumers to discover what is queryable without hard-coding family names: type, labels, allowed values, buckets, help, source idiom (A/B/C per §0.6), durability class (durable vs in-RAM), and cardinality contribution. | Anticipates #2 (agent tools) and #3 (Grafana) without building them: discovery + stable metadata = no rework. | Must | Query API |
| FR-A9 | The interface shall be auth-gated consistently with `/api/metrics`: scope `maintenance:read`, fail-closed 503 `AUTH_NOT_CONFIGURED` when auth is OFF, and the FE shall send `Accept: application/json` (`JSON_HEADERS` nuance, `mcps-gateway.service.ts:67`). | `/api/metrics` precedent (`daemon/routers/metrics.py:70`, `:91-107`): untagged class ⇒ protected by default (401/403); auth middleware 302-redirects wildcard-Accept browsers. | Must | Query API |
| FR-A10 | The interface shall define error semantics for: unknown family (404 with typed body), invalid label filter (400), and registry unavailable (503) — never a 500 for expected conditions. | Testable, consumer-stable error vocabulary. | Should | Query API |
| FR-A11 | The interface shall be paginated/bounded for series payloads (max samples per response, max families per response) with continuation tokens, so a single response cannot exceed a stated byte ceiling. | Prevents FE/backend memory blow-up; supports Grafana's bounded scrape. | Should | Query API |

### Theme B — Extension subset (18 registered objects → toward 27 families)

Decision rule (user-frozen, encoded here): a catalog family enters scope IFF (i) it is committed in the catalog §1, AND (ii) it maps to an EXISTING producer/pull seam per the inventory (research §1b consolidated seam table), AND (iii) it needs NO new instrumentation at module sites. Families failing (iii) are OUT even if (i)+(ii) hold; families failing (ii) are OUT regardless.

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-B1 | The system shall register every IN-scope family from §Theme B's adjudication table using the canonical extension seam (`metrics_listener.py:120-144`): register at module/runtime, canonical `ensemble_` name, declared `label_keys` + `allowed_values` from frozen vocabularies (§8), buckets from catalog §8.11, handle importable + `__all__`, increment at the production seam fail-open (`except Exception`, C-14). | The seam is the canonical in-repo pattern; following it keeps exposition consistent and testable. | Must | Extension |
| FR-B2 | The system shall adjudicate each catalog family per the decision rule and record IN / OUT / PARTIAL with the seam anchor, per the adjudication table below. | Traceable, attackable by the architect/plan council. | Must | Extension |
| FR-B3 | Any family adjudicated PARTIAL shall register ONLY the IN metrics inside it; the OUT metrics shall NOT be registered in v1 and shall be listed in the requirements' out-list (Theme G) with the specific greenfield seam named. | Prevents scope creep while preserving the catalog's family identity. | Must | Extension |
| FR-B4 | No new instrumentation shall be added at module sites beyond (a) registering a family and (b) reading an existing producer's already-computed value; any metric requiring a new increment/timing at a producer site is OUT of v1. | User-frozen rule #3; instrumentation additions are a separate commission. | Must | Extension |
| FR-B5 | Registration of any family whose source producer is PG-only (F6 census) or macOS-dark (F0.3 pre-WS-A) shall degrade to "no series" on the unsupported platform, per catalog degradation rows; the gate shall not silently pass. | Catalog §F0.3: gate BLOCKS if WS-A not merged; §F6: census PG-only. | Must | Extension |

**Theme B — Adjudication table (IN / OUT / PARTIAL).**

| Family | Verdict | In-scope metrics (if PARTIAL) | Seam anchor | Evidence |
|---|---|---|---|---|
| F0.3 Resource spine | IN | (full) | `flusher.py:161` (FlusherCounters), `sampler.py` (SamplerCounters) | counters exist; registration reads them; no new instrumentation. Catalog phase P0. Platform caveat: macOS prod pre-WS-A → dark until WS-A merge. |
| F1 Event-loop lag | IN | (full) | `wedge_capture/lag_probe.py` (MAX_LAG_SAMPLES=300, `LagHistogram.as_dict()` max/p95/p50) | single-sourced quantiles; registration wires them; no new instrumentation. |
| F2 Thread CPU | IN | (full) | `wedge_capture/sampler.py` (`:151` comm, `:160` per-thread jiffies, `:175` process, `:192` name map) | ONE /proc walker (MA-2); worker naming landed (`test_metrics_worker_naming`, 5 tests). Post-process bucketing at registration, not new measurement. |
| F3 Wedge capture | IN | (full) | `wedge_capture/triggers.py:202/:237/:281`, `watchdog.py:417-418` | counters already exist; registration only. |
| F4 DB pool native | IN | (full) | `readiness.py:525` `sample_sqlalchemy_pool_state`, `:552` `sample_psycopg_pool_state`, `collector.py:29` `pool_requests_errors` | existing samples; wiring only. Note: `pool_checkout_timeouts` is ALWAYS None (`collector.py:24-26`) — disclosed dark sub-value, not a gate failure. |
| F5 DB pool derived | IN | (full) | `collector.py:19-20` (checked_out derived) | derived + native kept apart (m2); no new measurement. |
| F6 PG census | IN | (full) | `metrics_census.py` (502 L, `CensusSnapshot`: state_counts, idle_in_txn_by_site, idle_in_txn_max_age_by_site, last_success_epoch); dedicated non-pooled conn, 15s floor | module landed; registration reads the existing snapshot. PG-only: renders no series on SQLite/non-PG (catalog degradation). |
| F7 Checkpoint op | PARTIAL | IN: `ensemble_checkpoint_op_bytes{op}` (bytes seam `log_saver_op(..., bytes_=0)` at `checkpoint_perf.py:148` — seam exists, under-reported; disclose as data-quality caveat, not new instrumentation) + `ensemble_checkpoint_slow_ops_total{op}` (slow ledger IS the source, bounded maxlen 128). OUT: `ensemble_checkpoint_op_seconds{op="aput"}` and `aput_writes` — the distinct write-side hook `_charge_checkpoint_write` is a NEW seam (J-4/V-6/Q1: read charge seam NOT reused for writes); interim = narrow enum to observable set (`aget` only) until the hook lands. | `checkpoint_perf.py:190/:198/:91`; `checkpoint_adapter.py:384-447`; `persistence.py:632` | S-1 superseded design explicitly rejected; write bytes must not be billed to the read budget. |
| F8 Checkpoint store size | PARTIAL | IN: `ensemble_checkpoint_blobs_table_bytes`, `ensemble_checkpoint_table_bytes{table}`, `ensemble_checkpoint_prune_runs_total{outcome}` (outcome values already produced by the prune sweep — `checkpoint_prune.py` `dry_run`/refusal paths). OUT: `ensemble_checkpoint_versions_per_thread` (greenfield — no existing producer; candidate scan `checkpoint_prune.py:224` materialises triples but version count is not produced today). | `maintenance.py:531-536` `_VACUUM_FULL_TABLE_BYTES_SQL`; `checkpoint_prune.py:224` | seam table marks table bytes pull YES / versions-per-thread greenfield. |
| F9 Readiness | IN | (full) | `readiness.py:64-79` (11-class taxonomy), grace subset `:83-89`, components `:94-99`; composite published at `/readyz`. Reads the published composite, never re-probes (MA-5). | no new instrumentation. |
| F10 Job queue depth | IN | (full) | `job_queue_items.admission_state` + index `:283` (enum `:40-43`); `/api/queues`, `/api/jobs`, `/api/dlq`, `/api/work`; `DeadLetterService.count_dlq`; created_total derived from durable `created_at` (TEXT ISO, §10.1). | TIER-1 durable; no new instrumentation. |
| F11 Worker pool / heartbeat | PARTIAL | IN: `ensemble_workers_total{pool}`, `ensemble_workers_busy{pool}`, `ensemble_worker_pool_wakeup_efficiency{pool}`, `ensemble_worker_pool_events_total{pool,event}`, `ensemble_task_heartbeat_age_seconds`, `ensemble_task_heartbeat_cadence_seconds` (pull from `WorkerPool.get_stats()` `:1518/:1537` and `task/models.py:284` + constructor cadence `worker_pool.py:45/:197`). OUT: `ensemble_job_claim_latency_seconds` (greenfield — claim latency is not measured today; seam = claim call `worker_pool.py:302` → `task_processor.py:1829` → `repository.py:2619`, interval from `task.created_at`/`started_at` `models.py:273-274`; new timing). | `worker_pool.py:1518`, `instance_read.py:2092`, `task/models.py:284` | seam table: get_stats + heartbeat pull YES; claim latency GREENFIELD. |
| F12 Sweep cycle | PARTIAL | IN: `ensemble_sweep_cycles_total{lane}` (accumulate per-tick `counters().ticks`), `ensemble_sweep_errors_total{lane}` (accumulate), `ensemble_sweep_rows_total{lane,outcome}` (accumulate per-tick values; doc-drift C17: `job_queue_service.py:828-837` per-tick reset — accumulation is the work, not new measurement). OUT: `ensemble_sweep_cycle_seconds{lane}` (no per-cycle duration measurement exists in any sweep service — verified: grep perf_counter/duration/elapsed = 0 across the three sweep services). | `watch_reconcile_sweep.py:356`, `eligible_pending_sweep.py:404`, `orphan_watcher_sweep.py:260`, RDRS per-lane counters | accumulation of existing per-tick counters is wiring; timing is new measurement. |
| F13 Instance status/lifecycle | PARTIAL | IN (gauges): `ensemble_instance_status_count{status}`, `ensemble_instance_liveness_count{liveness}`, `ensemble_waiting_children_count`, `ensemble_waiting_children_max_age_seconds` (pull from `_build_capacity_summary` `instance_read.py:2019` + durable instance rows for waiting-children age). OUT (counters needing new increments): `ensemble_wake_turns_total{outcome}` (Carrier-Threading contract, §8.13; J-2 two producers), `ensemble_instance_lifecycle_total{event}` (event_publisher seam, J-3 vocabulary). | `instance_read.py:2019`, `worker_pool.py:1381-1386`, `graph.py:7319`, `event_publisher.py:38-80` | seam table: status/liveness pull YES; wake + lifecycle counters = new increments. |
| F17 MCP health | IN | (full) | `mcp/init_guard.py:145-147` per-boot quarantine registry; published `/readyz` → `detail.mcp` (`api.py:3840-3878`); consumed `_mcp_server_health_state` (`mcp_servers.py:302`). Reads the snapshot, never re-probes. | no new instrumentation. |
| F18 MCPS gateway | IN | (full) | `services/mcps_gateway/store.py:79` MachineStore, `:103` roster(), sessions, tombstones, audit (durable `mcp_gateway_audit`, migration `20261022_000001`). | no new instrumentation. |
| F19 Compaction | OUT | — | `compaction.py:1128/:2299` (rung fires log-only; pre-call gate `graph.py:7616-7670`) | new registration at existing sites = instrumentation (counters/histograms not produced today); catalogued P1c (Tier-3-equivalent effort). Out of v1 per commission (no module deepening). |
| F20 Turn/tool seam | OUT | — | `message_tap.py:234` `tap_node_return`; entry `:120-124`; tap DB write `:302-306` | new increments at the tap = instrumentation; catalogued P1c. |
| F21 Journal twin | PARTIAL | IN (read-side gauges): `ensemble_journal_file_bytes` (`upgrade_journal.py:255` stat size), `ensemble_journal_history_entries` (`:165` len history), `ensemble_upgrade_release_current{version}` (`:159` current). OUT (op instrumentation): `ensemble_journal_op_seconds`, `ensemble_journal_ops_total{op}`, `ensemble_upgrade_phase_seconds{phase}`, `ensemble_upgrade_phase_total{phase}`, `ensemble_upgrade_journal_events_total{kind}` (increments/timing at twin op sites `:259/:331/:374/:391/:409`). | `upgrade_journal.py` twin | commission borderline: read-side gauges pull-able vs op instrumentation = push. |
| F22 Exporters/shell | OUT | — | textfile collector greenfield + off-the-shelf node_exporter/postgres_exporter | P2 scope (devops-owned, out-of-process); commission OUT-list (no Grafana layer #3 in v1). |
| F0.1 Exposition listener | ALREADY LIVE | (registered P0) | `metrics_listener.py:106-146` | 3 series, 16 cardinality; already in the 18 objects. |
| F0.2 Registry generalization | ALREADY LIVE | (registered P0) | `checkpoint_metrics.py:1027-1061` | 3 series. |
| F0.4 Vocabulary freeze | ALREADY LIVE | (registered P0) | `checkpoint_metrics.py:1058-1061` | 1 series; `VOCABULARY_VERSION=1`. |
| F7b Checkpoint read | ALREADY LIVE | (registered) | `checkpoint_adapter.py:384-447`, `checkpoint_perf.py:341-395` | 46 series. |
| F7c Read cache | ALREADY LIVE | (registered) | `checkpoint_adapter.py:741/:795/:823` | 2 series. |
| F14 LLM tokens | OUT (Tier-3) | — | `graph.py:3776-3784` terminal-usage branch | commission OUT-list: Tier-3 (LLM tokens). |
| F15 LLM request latency/outcome | OUT (Tier-3) | — | `llm_error_classifier.py:764-819` | commission OUT-list: Tier-3. |
| F16 LLM stream stalls | OUT (Tier-3) | — | `llm_stream_watchdog.py:456` | commission OUT-list: Tier-3. |
| (uncatalogued) sources breakers/token buckets | OUT | — | zero exposure today (INV-B) | commission OUT-list. |
| (uncatalogued) badge/request latency | OUT | — | no producer today | commission OUT-list. |
| (uncatalogued) module deepening | OUT | — | beyond mapped pull seams | commission OUT-list. |

**Series-budget check (CORRECTED):** the extension subset (IN + PARTIAL-IN) adds **+274** daemon-origin series over the current **89** (F7c-inclusive) / **87** (ledger-rows basis) — 18 registered objects (15 import-time + 3 lazy listener) — landing at **363 ≈18.2 %** of the ≤2000 cap (361 on the ledger-rows basis; 364 if the 4-value F18 audit set is ever admitted; 355 if WS-A defers F0.3), well inside the headroom and consistent with the catalog's 582/≤2000.
> **[RE-SCOPED 2026-11-02 — architect AM-26 + plan council W2]** This paragraph carried a **third** pre-fold copy of the budget numbers (≈291 / ≈76 / ≈367 / 18.4 %) that the plan-overview's DEV-5 did not name. The figures are now the AM-26-corrected ones (+274 → 363 ≈18.2 %); the **F7c reconciliation** (catalog §14.1/§14.2/§14.3 stale-by-2 → 582) is recorded in `plan-overview.md` §Re-derived budget and `phase6-plan.md` task 6.2, which carries the standalone derivation recipe. The tech worker SHALL re-derive this number from `--collect-only` and state it in the plan; the requirement is only the bound (≤400 daemon-origin series for the v1 subset).

### Theme C — Trend / History Requirement

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-C1 | The system shall expose recent-trend series (timestamped samples) for chart-primary families, with a lookback of at least 60 minutes at ≥15 s resolution, via the query API (FR-A3). | The registry has NO windowing (§1a); charts need recent trends. | Must | Trend |
| FR-C2 | The system shall classify every registered family as chart-primary (series required) or stat-primary (current value only). Chart-primary set SHALL include at least: F1, ~~F2~~, F4, F5, F6, F10 (depth gauges), F11 (gauges), F13 (status/liveness/waiting-children gauges), F9 (readiness states). All other families are stat-primary by default unless the tech worker justifies otherwise. The concrete roster (the 8 floor families **plus the D5 partials that qualify** — F7, F8, F18, F21 = **12 families**), with the per-family/per-series `chart_role` values, is enumerated in **plan task 2.7b** (the registration-time family metadata home) and is the subject set for AC-A.3 / AC-C.1. | Gives the FE a deterministic series requirement without over-scoping. | Must | Trend |
> **[RE-SCOPED 2026-11-02 — approver B2 + leader LR-2]** **F2 is removed from the FR-C2 floor set** — it was dropped from the v1 subset by the value ruling (AM-25 / leader LR-2), so keeping it in the floor set made the requirement unimplementable. This is the same class of fold as the council-W2 re-scopes (the requirement row is inside the AM-17/AM-26 re-scope families, so it is folded directly rather than left as a disclosed deviation). The roster it now points to is explicit and self-contained (task 2.7b).
| FR-C3 | The trend mechanism shall be bounded: in-process retention (ring buffer) is the house-preferred default (resource_monitor TierRing precedent — RAM ring, flusher to PG is a separate lane); NO new PG table unless the inventory justifies it. If a table ever materializes, one `daemon/migrations/versions/*.sql` file per SQLModel table is mandatory (blueprint convention — 109 files, zero exceptions). | Avoids unbounded memory growth and schema proliferation; keeps the v1 scope additive. | Must | Trend |
| FR-C4 | The trend buffer memory ceiling shall be stated in the plan and SHALL NOT exceed the equivalent of 32 MiB of sample points (≈ families × lookback/cadence × 16 bytes); the implementation shall fail closed (drop oldest) rather than grow unbounded. | Bounded memory is an NFR; unbounded ring buffers are the incident class (Oct-26). | Must | Trend |
| FR-C5 | Restart resets trend buffers (in-RAM, idiom A per catalog §10.0) and the API contract SHALL disclose the restart-epoch boundary explicitly (a `restart_epoch`/`boot_id` field on series responses) so consumers can distinguish a restart from a gap. | Catalog §10.0: idiom A resets on daemon restart; hiding it is the false-negative class. | Must | Trend |
| FR-C6 | Cardinality interplay: trend storage counts toward the same ≤2000 cardinality budget as the registry; a family's trend series count SHALL NOT inflate the cardinality cap (trend points are time-indexed, not label-indexed). | Prevents trend storage from consuming the label-cardinality budget. | Should | Trend |
| FR-C7 | The trend sampling cadence shall default to the registry publisher cadence (`limits.metrics_scrape_interval_hint_s`, 15 s, `daemon/api.py:2496-2511`) and SHALL be adjustable via the existing managed key without a restart. | Reuses the existing managed-key chain; no new knob. | Should | Trend |

### Theme D — Activation / Posture Requirements

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-D1 | The system shall define per-surface activation semantics for: (a) the query API, (b) the trend buffer, (c) the FE charts page polling — and disclose all three at promote time. | Catalog §0.8 + two-class rule: posture must be explicit, not implicit. | Must | Activation |
| FR-D2 | The two-class kill-switch rule (`daemon/config.py:4413-4419`: "ON for zero-user-action read/serve surfaces, OFF for agent-authored mutation lanes") SHALL be the governing evidence for choosing the posture; the chosen posture and its rationale SHALL be recorded in the plan. | User-frozen governing rule; metrics exposition is a read/serve surface ⇒ default-ON candidate, but the listener precedent is default-OFF — the tech worker decides and discloses. | Must | Activation |
| FR-D3 | **RE-SCOPED to the LISTENER surface (D-11b).** Whatever posture is chosen, the **listener's** OFF semantics SHALL be inert-safe: observe-accumulates-always (listener precedent: gate gates ONLY the response body; socket binds; accumulation continues); OFF ⇒ 200 valid-empty (no `ensemble_metrics_up`, no family series) — never an error; no series leakage including no `ensemble_metrics_up` when OFF. **The query door's OFF semantics are availability-driven (FR-A7), not emptiness; the registry accumulates ungated in both switch states, so the door keeps serving live data.** | Catalog §0.8, §0.11 degradation: absent ≠ zero; OFF is a real state. **D-11b (architect v2 AM-17 + leader LR-3).** | Must | Activation |
> **[RE-SCOPED 2026-11-02 — architect AM-17 (D-11b) + leader LR-3]**
| FR-D4 | The query API's OFF semantics (FR-A7) SHALL be consistent with the chosen listener posture: either both follow 200-valid-empty, or the difference SHALL be documented with a stated reason. | Avoids a split-brain where `/api/metrics` (auth-gated, no kill-switch consult) and the query API disagree. Note: `/api/metrics` does NOT consult the kill-switch today (research §1c) — auth posture gates it. | Must | Activation |
| FR-D5 | The trend buffer SHALL accumulate in both ON and OFF states (observe-accumulates-always) so switching ON loses no history. | Listener precedent: registry keeps accumulating when OFF. | Must | Activation |

### Theme E — FE Charts Page Requirements

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-E1 | The FE shall add a charts page fed by the query API (FR-A1–A3), following the house recipe: lazy `loadComponent` route with `title` (before any `:id` routes), gear-menu entry in `app.ts` static array with ordering pins (`resources-gear-menu.spec.ts` precedent), and a Playwright e2e gate config. | Research §2 app structure: three integration points + ordering pins. | Must | FE Charts |
| FR-E2 | The FE shall implement a service with static readonly URL constants (single source of truth for the FE↔BE surface, `resources.service.ts:35-39`), relative `/api/...` paths, and `JSON_HEADERS = {Accept:'application/json'}` if auth-gated (auth middleware 302-redirects wildcard-Accept). | Research §3: static URLs + JSON_HEADERS nuance. | Must | FE Charts |
| FR-E3 | The FE shall use typed models in `src/app/models/` (barrel) as a VERBATIM mirror of the frozen BE contract — drift escalated, never normalized client-side (`resources.models.ts:1-13`); naive-UTC timestamps via `parseNaiveUtc`. | House rule: models are a verbatim mirror. | Must | FE Charts |
| FR-E4 | The FE shall poll at a named `POLL_INTERVAL_MS` constant with teardown (`setInterval`+`clear` or `timer`+`takeUntilDestroyed`), lazy tab bodies stopping polls on destroy, and an error banner + retry CTA retaining last-good data (`live-tab.component.ts:69-99`); concurrent responses superseded via monotonic seq tokens. | Research §3 polling inventory + error handling. | Must | FE Charts |
| FR-E5 | The FE shall render kill-switch OFF as a real state (200-empty), not an error: a distinct "metrics disabled" empty state with a pointer to the activation setting. | Consistent with FR-A7/FR-D3; prevents false error banners. | Must | FE Charts |
| FR-E6 | The FE shall be standalone components, OnPush, signals + computed, Material per-component imports, zoneless (no polyfills) — consistent with `frontend/` conventions. | Research §2/§6: zoneless Angular 21. | Must | FE Charts |
| FR-E7 | The FE shall include colocated `*.spec.ts` (pure-function specs for scale/format helpers + optional bindings pins grepping source constants) and run one `ng build --configuration development` pass after template work; no lint script exists (tsc + jest discipline). | Research §6 build/test. | Must | FE Charts |
| FR-E8 | Charting library choice is the tech worker's decision; the requirements state capability requirements: (a) line + step + area series for gauge/counter trends; (b) native `<title>`/tooltip minimums + legend; (c) dark-theme consistency (Material M3 dark slate `#0f172a`/`#e2e8f0`, `styles.scss:1-19`); (d) bundle-budget respect (initial 1MB warn/6MB error; component style 8kB warn/24kB error); (e) zero-new-dependency is the default (hand-rolled SVG precedent `svg-timeline.component.ts`); any new dep requires explicit justification in the plan. | Research §1: no charting lib in-stack; d3 only mermaid-transitive; hand-rolled SVG precedent. | Must | FE Charts |

### Theme F — Non-Functional + Test Requirements

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-F1 | No new backend dependencies: no `prometheus_client`, no OTel, no statsd, no psutil, no metrics library — exposition hand-rendered (ratified D5, `metric-catalog.md §0.2`). | Ratified decision 5; PyInstaller surface stays unchanged. | Must | NFR |
| FR-F2 | Registry invariants preserved: render-never-blocks (`RENDER_TRY_ACQUIRE_SECONDS=0.05` per family, `RENDER_TOTAL_BUDGET_SECONDS=0.25`), per-metric locks only (no global lock), try-acquire → last-published snapshot + `# <family> unavailable: lock_contended`, copy-on-publish cadence (`limits.metrics_scrape_interval_hint_s`), hard cardinality cap `DEFAULT_CARDINALITY_CAP=2000` with atomic check-then-increment + `ensemble_metrics_cap_rejected_total`, three-tier never-exported enforcement, C-14 `except Exception` not `BaseException`. | Catalog §0.2, §0.5; these are the regression invariants. | Must | NFR |
| FR-F3 | No regression to the `:9798` listener or `/api/metrics` behavior: frozen contracts (listener OFF ⇒ 200 valid-empty incl. no `ensemble_metrics_up`; auth-gated `/api/metrics` 503 `AUTH_NOT_CONFIGURED`; scope `maintenance:read`; origin guard; loopback-only bind). | Catalog §0.7, §0.8; these are the shipped contracts. | Must | NFR |
| FR-F4 | `api_families` pinned count 33 SHALL be bumped to 34 if the query API adds a new `/api` router family, with a corresponding update to `tests/integration/test_router_class_manifest.py:383-385` and the manifest test. | Research §1c: any new /api family requires the manifest test bump. | Must | NFR |
| FR-F5 | Test requirements SHALL be expressed as PACKS.md pack shapes per the gate doctrine: D1 lane-owned HEAD-only pins (must pass at HEAD; base absence expected) + D2 dual-side comparable set (the only set carrying the FAILED-digest diff); Tier 1 (≤30 min, fail-loud) for additive modules; Tier 2 (≤90 min advisory) for any `daemon/api.py` or `daemon/manager.py` wiring change (mechanical escalation). | Research §3 gate doctrine; registry-adjacent files near core seams escalate. | Must | NFR |
| FR-F6 | Mutation-verification: every new behavioral pin SHALL be planted independently and shown RED→GREEN with a documented mutation→test table in PACKS.md (v1.0.2 convention); net-new-failure = full FAILED-list diff vs true pre-fix base, same commands both sides. | Research §3 non-negotiables. | Must | NFR |
| FR-F7 | Vocabulary changes (if any) SHALL bump `VOCABULARY_VERSION` (`daemon/checkpoint_metrics.py:190`) and update the §8 annex; closed vocabularies derived from code (A1), never hand-listed. | Catalog §0.5, §8 P-1 freeze rule. | Must | NFR |

### Theme G — Explicit Out-List + Anticipation of #2/#3

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-G1 | The following are OUT of v1 (verbatim from commission): no module onboarding beyond mapped pull seams; no Tier-3 (LLM tokens/LLM request latency/outcome/stream stalls/breakers); no agent tool surface (item #2 deferred); no Grafana layer (item #3 deferred); no live promote; no new FE dependency unless explicitly justified; no new PG tables unless justified. | User-frozen out-list. | Must | Out-list |
| FR-G2 | Module deepening, sources breakers/token buckets, badge/request latency (uncatalogued families) are OUT — they need new instrumentation. | Commission out-list + inventory Tier-3. | Must | Out-list |
| FR-G3 | F19 (compaction), F20 (turn/tool seam), F22 (exporters/textfile), F14/F15/F16 (LLM) are OUT of v1 (catalogued P1c/P2; new instrumentation or out-of-process). | Commission out-list. | Must | Out-list |
| FR-G4 | The query API and trend buffer SHALL be shaped so that item #2 (agent tools) and item #3 (Grafana) can be added WITHOUT rework: stable contract version (FR-A4), capability discovery (FR-A8), bounded paginated series (FR-A11), auth scope reuse, and Prometheus-text coexistence (FR-A6). | Anticipate without building. | Must | Out-list |

---

## Non-Functional Requirements

| ID | Category | Requirement | Metric | Target | Measurement |
|----|----------|-------------|--------|--------|-------------|
| NFR-1 | Performance | Query API response time | p95 latency | ≤ 250 ms for snapshot; ≤ 1 s for 60-min trend series | integration test + Playwright |
| NFR-2 | Performance | Registry render never blocks | per-family try-acquire | 0.05 s/family; 0.25 s total budget | existing registry tests |
| NFR-3 | Performance | FE charts page bundle | initial bundle | ≤ 1 MB warn / 6 MB error (`angular.json:64-75`) | `ng build --configuration development` |
| NFR-4 | Performance | FE component style budget | per-component SCSS | 8 kB warn / 24 kB error | Angular build |
| NFR-5 | Scalability | Cardinality cap preserved | daemon-origin series | ≤ 2000 (currently ≈29% planned; subset lands ≈18.4%) | `--collect-only` + ledger §14 |
| NFR-6 | Reliability | OFF-state inertness | series leakage when OFF | zero family series + no `ensemble_metrics_up` | listener registry tests (`test_metrics_listener_registry.py`, 15 tests) |
| NFR-7 | Reliability | Trend buffer memory | RSS growth | ≤ 32 MiB sample points; drop-oldest on overflow | unit test |
| NFR-8 | Maintainability | No new backend deps | dependency diff | zero new entries in pyproject.toml | diff review |
| NFR-9 | Usability | FE OFF-state rendering | distinct empty state | 200-empty ⇒ "metrics disabled" panel, not error banner | component spec |
| NFR-10 | Security | Auth posture | scope enforcement | `maintenance:read` + 503 `AUTH_NOT_CONFIGURED` fail-closed | `test_metrics_route.py` |

---

## Constraints

| ID | Type | Description | Source | Impact |
|----|------|-------------|--------|--------|
| C-1 | Technical | Registry is top-level `daemon/checkpoint_metrics.py` (not services/); no global lock; per-metric locks. | Catalog §0.2 | Query API must not introduce a global lock. |
| C-2 | Technical | No new PG tables unless justified; if one materializes, one `.sql` per SQLModel table mandatory. | Blueprint + research §C | Trend buffer defaults to in-memory. |
| C-3 | Technical | api_families pinned at 33; new /api family requires manifest test bump. | `test_router_class_manifest.py:383-385` | If query API is a new router family, bump to 34. |
| C-4 | Technical | `metrics_census` runs only when `ensemble_config.is_postgres` (`api.py:2454-2460`); F6 renders no series on SQLite. | Research §1b | F6 dark on non-PG. |
| C-5 | Technical | F0.3 P0 exit gate BLOCKS unless WS-A merge (`feature/resource-monitor-flusher-fix@53547b915`) lands first. | Catalog §F0.3 | Gate, not warning. |
| C-6 | Technical | Platform: macOS prod host has no /proc/cgroup; F0.3 per-child stays dark on Darwin (WS-A is coarse daemon-level only). | Catalog §13 | Per-child attribution deferred. |
| C-7 | Business | No live promote anywhere in this lane; promote is the user's 3-factor nonce ceremony. | Commission | Staged only. |
| C-8 | Business | Default OFF vs default ON posture decision is the tech worker's with disclosure (two-class rule). | Catalog §0.8 | Decide + disclose at promote. |
| C-9 | Resource | Tiered-gate doctrine: Tier 1 (≤30 min) default; Tier 2 (≤90 min) for `daemon/api.py`/`daemon/manager.py` wiring changes. | Research §3 | Test budget. |
| C-10 | Resource | Budget headroom: ≤2000 cardinality; subset lands ≈367 daemon-origin (≈18.4%). | Catalog §1, §14 | Room for #2/#3 later. |

---

## Acceptance Criteria

### Theme A — Query API (FR-A1..FR-A11)

- **AC-A.1** (FR-A1): Given the registry has N registered families, when a client calls the query API for metadata, then the response lists exactly N families with name, type, labels, allowed values, buckets, help, source idiom, durability class, and cardinality contribution; test: a new family registered via the canonical seam appears without code change to the API. Test type: integration.
- **AC-A.2** (FR-A2): Given a registered family with current values, when queried, then the response contains the instant value per label series; test type: integration.
- **AC-A.3** (FR-A3): Given 60 minutes of scrape history, when a **chart-primary family from the roster** (the 12 families enumerated in plan task 2.7b — the FR-C2 floor set minus F2, plus the qualifying D5 partials F7/F8/F18/F21) is queried for trend, then the response contains ≥240 samples (60 min × 4/min at 15 s) with monotonically non-decreasing timestamps; test type: integration.
  > **[RE-SCOPED 2026-11-02 — approver B2 + leader LR-2]** The subject set is now **defined** (the roster in task 2.7b) — the criterion previously said "a chart-primary family" with no enumeration, so it had no defined subject. The ≥240-sample threshold is **unchanged** and is now satisfiable by construction: the `max_points` default was raised to 240 (approver B1 + leader ruling), so 60 min at 15 s = 240 points fits the default cap exactly.
- **AC-A.4** (FR-A4): Given a contract-version field in the response, when a consumer compares it to a pinned value, then a mismatch is detectable; test type: unit.
- **AC-A.5** (FR-A6): Given the query API is live, when stock Prometheus scrapes `:9798/metrics`, then the exposition body is **structurally identical** to the pre-change body (same series set, TYPE lines, label vocabularies, HELP structure) **except the enumerated 3-carrier value-delta allowlist** — (1) the `# frozen vocabulary version: N` footer, (2) the `ensemble_metrics_vocabulary_version` gauge value, (3) the `# HELP ensemble_checkpoint_op_seconds …` text — and any delta outside that allowlist is a gate red; test: diff against the baseline artifact with the allowlist applied. Test type: integration.
  > **[RE-SCOPED 2026-11-02 — architect AM-16 (D-11a) + leader LR-3]** The original row demanded *byte-identical*, which is unsatisfiable against the ratified AD5 `VOCABULARY_VERSION` 1→2 bump (the bump changes ≥3 carriers — AM-16 counted them). The re-scope makes the gate honest about exactly those carriers; the exposition fence itself is untouched (the Prom-text door remains intact alongside).
- **AC-A.6** (FR-A7): Given `ENSEMBLE_METRICS_ENABLED` is OFF, when the **listener** is scraped, then the body is 200 with an empty series set and no `ensemble_metrics_up`; accumulation continues (switching ON later shows prior data). **The query door is NOT switch-gated: with the switch OFF it keeps serving live registry-sourced data, and the only permitted body differences are (i) F6 `availability` → `census_gated`, (ii) `activation.census_enabled` → false, (iii) `last_publish_age_s` growth — all by design and excluded from the invariance claim (architect AM-18).** Test type: integration + unit.
  > **[RE-SCOPED 2026-11-02 — architect AM-17 (D-11b) + leader LR-3]** The original row required the *door* to be empty when OFF — the exact opposite of D1(b), which phase 3's pin asserted. D1(b) wins: the door is auth-only; OFF semantics are availability.
- **AC-A.7** (FR-A9): Given auth is OFF, when the query API is called, then the response is 503 `AUTH_NOT_CONFIGURED`; given auth is ON without scope, then 403; given valid scope, then 200. Test type: integration (`test_metrics_route.py` pattern).
- **AC-A.8** (FR-A11): Given a series payload exceeding the page size, when queried, then the response is bounded and carries a continuation token; test type: unit.

### Theme B — Extension subset (FR-B1..FR-B5)

- **AC-B.1** (FR-B1): Given an IN-scope family, when registered via the canonical seam, then it appears in both `/metrics` and the query API with the correct name/labels/buckets/help; test: extend `test_checkpoint_metrics_registry.py` + `test_checkpoint_metrics_vocabulary.py` per family.
- **AC-B.2** (FR-B2): Given the adjudication table, when reviewed, then every catalog family (F0.1–F22) has exactly one of IN/OUT/PARTIAL/ALREADY-LIVE with a seam anchor; test: doc review (plan council).
- **AC-B.3** (FR-B3): Given a PARTIAL family, when its module is inspected, then only the IN metrics are registered; the OUT metrics are absent from the registry and from `/metrics`. Test type: integration (grep + render diff).
- **AC-B.4** (FR-B4): Given the diff, when inspected, then no module site gained a new increment/timing call beyond reading an existing producer's already-computed value; test: diff review + `git diff` inspection.
- **AC-B.5** (FR-B5): Given F6 on a SQLite/non-PG runtime, when queried, then the family renders no series (not an error); given F0.3 on macOS pre-WS-A, then the gate blocks (not passes). Test type: integration (postgres pack + platform note).

### Theme C — Trend (FR-C1..FR-C7)

- **AC-C.1** (FR-C1): Given 60 minutes of synthetic scrape history at 15 s cadence, when the trend endpoint is queried, then ≥240 samples are returned **per chart-primary family of the roster** (the 12 families enumerated in plan task 2.7b). Test type: integration.
  > **[RE-SCOPED 2026-11-02 — approver B2 + leader LR-2]** Same subject-set definition as AC-A.3 (the roster in task 2.7b); the ≥240 threshold is unchanged and is consistent with the raised `max_points` default of 240 (approver B1 + leader ruling).
- **AC-C.2** (FR-C3): Given the implementation, when inspected, then no new `.sql` migration file exists unless justified in the plan; trend storage is in-memory. Test type: diff review.
- **AC-C.3** (FR-C4): Given a synthetic load exceeding the memory ceiling, when the buffer overflows, then oldest samples are dropped and memory stays ≤32 MiB; test type: unit.
- **AC-C.4** (FR-C5): Given a daemon restart, when the trend endpoint is queried post-restart, then the response carries a new `restart_epoch`/`boot_id` and an empty series set with a disclosed boundary. Test type: integration.

### Theme D — Activation (FR-D1..FR-D5)

- **AC-D.1** (FR-D3): Given the **listener** is OFF, when the listener surface is exercised, then observe-accumulates-always holds (switching ON shows prior data) and no series leaks (including no `ensemble_metrics_up`). Test type: integration (`test_metrics_listener_registry.py` pattern).
  > **[RE-SCOPED 2026-11-02 — architect AM-17 (D-11b) + leader LR-3]** Acceptance criterion of the re-scoped FR-D3: the original "any surface" wording would have re-created the D-11b contradiction on the query door.
- **AC-D.2** (FR-D4): Given the chosen posture, when documented, then the query API and listener OFF semantics are stated as consistent or the difference is documented with a reason. Test type: doc review.

### Theme E — FE Charts (FR-E1..FR-E8)

- **AC-E.1** (FR-E1): Given the FE build, when `ng build --configuration development` runs, then it succeeds with zero errors; route + gear-menu entry + ordering pins green. Test type: build + jest.
- **AC-E.2** (FR-E4): Given the charts page, when unmounted, then polling stops (no leaked intervals); test: bindings pin greps `POLL_INTERVAL_MS` and `clearInterval`/`takeUntilDestroyed`. Test type: jest bindings pin.
- **AC-E.3** (FR-E5): Given the **query door** is queried while `ENSEMBLE_METRICS_ENABLED` is OFF, the door **still serves live registry-sourced data** (D-11b: the door is never switch-gated); the charts page therefore renders its **availability-driven** state — the "no metrics available yet" panel when **no family is live** (never a "metrics disabled" claim, never the error banner), with the envelope's `activation` block naming the listener/census state. Test type: jest + Playwright.
  > **[RE-SCOPED 2026-11-02 — plan council W2]** The original row asserted a **kill-switch-OFF trigger** and the stale "metrics disabled" empty state — both contradict **D-11b** (architect AM-17 + leader LR-3: the door's OFF semantics are availability, not emptiness) and the **AM-19(d)** copy fix. Folded directly per the council's W2 instruction (within the AM-17 re-scope family).
- **AC-E.4** (FR-E8): Given the FE, when inspected, then no new charting dependency is added unless justified in the plan; default is hand-rolled SVG. Test type: diff review of `package.json`.

### Theme F — NFR/Test (FR-F2..FR-F7)

- **AC-F.1** (FR-F2): Given the wedged-writer scenario, when a scrape occurs, then it completes within 0.25 s and emits `# <family> unavailable: lock_contended` for the contended family. Test type: unit (existing registry tests).
- **AC-F.2** (FR-F4): Given a new `/api` family is added, when the manifest test runs, then `api_families == 34` (bumped) and passes. Test type: integration.
- **AC-F.3** (FR-F5): Given the gate, when run, then Tier 1 (≤30 min) or Tier 2 (≤90 min) per the mechanical escalation rule; D1/D2 split with node counts and slicing proof in PACKS.md. Test type: gate execution.
- **AC-F.4** (FR-F7): Given a vocabulary change, when the registry renders, then `ensemble_metrics_vocabulary_version` increments and the §8 annex is updated. Test type: unit.

---

## Gaps & Ambiguities

| # | Gap / Ambiguity | Question for Caller | Severity |
|---|-----------------|---------------------|----------|
| 1 | Trend mechanism choice (in-memory ring buffer vs. PG table) is left to the tech worker per the commission; the requirement only bounds it. | Should the tech worker propose a concrete mechanism in the plan, or is a second requirements pass needed? | Medium |
| 2 | The exact FE charting library decision (hand-rolled SVG vs. introducing d3/echarts) is the tech worker's; the requirement states capability needs only. | Confirm zero-new-dep default is acceptable for the charts page's series types. | Medium |
| 3 | F8's `ensemble_checkpoint_prune_runs_total{outcome}`: the prune sweep produces outcome-ish data (`dry_run`, refusal), but a counter increment at the run-completion site is arguably "new instrumentation". Adjudicated IN-with-caveat; the tech worker should confirm the increment site exists or move it OUT. | Confirm or re-classify. | Low |
| 4 | F12's `ensemble_sweep_cycle_seconds{lane}`: no cycle-duration measurement exists in any sweep service (verified). Adjudicated OUT; if the tech worker can derive duration from existing timestamps, it may be re-classified IN. | Confirm derivation feasibility. | Low |
| 5 | F13's waiting-children gauges: `_build_capacity_summary` computes status/liveness but waiting-children count/max-age may require a separate durable-row query. Adjudicated IN assuming the durable query exists. | Confirm the query exists. | Low |
| 6 | F21's `ensemble_upgrade_phase_*` and `ensemble_upgrade_journal_events_total{kind}`: arguably derivable by scanning the journal history (pull) rather than incrementing at twin op sites. Adjudicated OUT (push) per the commission's "op instrumentation = push" framing; the tech worker may propose a scan-based adapter if latency permits. | Confirm or re-classify. | Low |
| 7 | Whether the query API is a new `/api` router family (bumping api_families 33→34) or rides `/api/metrics` is undecided; this affects FR-F4. | Tech worker to decide and state in the plan. | Medium |
| 8 | The exact lookback window default (60 min proposed) and resolution floor (15 s) are requirement-level proposals; the tech worker may adjust within the stated bounds. | Confirm. | Low |
| 9 | Platform caveat for F0.3: the gate BLOCKS if WS-A is not merged; the tech worker must state the merge as a precondition in the plan. | Confirm ordering. | Medium |

---

## Assumptions

| # | Assumption | Reason | Risk if Wrong |
|---|------------|--------|---------------|
| 1 | The catalog §1 (1915 L) is authoritative over family names/labels; research-findings is the verified digest. | Commission directive. | Mis-named metrics; mitigated by citing catalog verbatim. |
| 2 | The 18 registered objects count (F0.1, F0.2, F0.4, F7 partial, F7b, F7c) is accurate as of base origin/latest. | Research §1b. | Subset delta off by a few series; mitigated by re-deriving in plan. |
| 3 | `VOCABULARY_VERSION=1` is the current freeze marker; no bump needed for v1 unless vocabularies change. | `checkpoint_metrics.py:190`. | Stale freeze; mitigated by FR-F7. |
| 4 | The FE charts page is a new page, not a tab on an existing page; three integration points (route + gear + ordering pins). | Research §2 (MCP Gateway precedent). | Integration count off; mitigated by following the recipe. |
| 5 | Trend buffer in-process is acceptable (restart resets disclosed); no PG table in v1. | Commission §C house preference. | If durability is required later, a migration + plan amendment needed. |
| 6 | The query API can be implemented without touching `daemon/api.py` or `daemon/manager.py` wiring (Tier 1); if it does, Tier 2 escalation applies. | Research §3 mechanical rule. | Test budget escalation; mitigated by FR-F5. |

---

## Out of Scope (Deferred)

- Module onboarding beyond mapped pull seams (no new instrumentation at module sites).
- Tier-3 families: F14 (LLM tokens), F15 (LLM request latency/outcome), F16 (LLM stream stalls), sources breakers/token buckets, badge/request latency, module deepening.
- Agent tool surface (item #2) — interface anticipates it (FR-G4) but no tools ship.
- Grafana layer (item #3) — no dashboard or remote_write; stock Prometheus scrape only.
- Live promote — user's 3-factor nonce ceremony owns it.
- New FE dependency — default zero-dep (hand-rolled SVG); any dep requires explicit justification.
- New PG tables — default none; if justified, one `.sql` per SQLModel table.
- F7 write-side hook `_charge_checkpoint_write` (distinct seam, J-4/V-6/Q1) — separate commission.
- F8 `ensemble_checkpoint_versions_per_thread` (greenfield).
- F11 `ensemble_job_claim_latency_seconds` (greenfield).
- F12 `ensemble_sweep_cycle_seconds{lane}` (no duration measurement).
- F13 `ensemble_wake_turns_total`, `ensemble_instance_lifecycle_total` (new increments).
- F19, F20, F22 (compaction, turn/tool, exporters) — catalogued P1c/P2.
- F21 op-instrumented families (journal ops, upgrade phases, journal events).
- Per-child RSS/CPU attribution on macOS (WS-A: dark on Darwin by decision).
- Remote_write to durable store (Q2: separate project).
- Re-partitioning of v1 phases (order forced by dependency graph).

---

## Open questions for the orchestrator

1. Should the trend mechanism (in-memory ring buffer) be specified at requirements level as the house default, or should the tech worker be free to propose a PG-table variant? Current requirement (FR-C3) states house preference but leaves the mechanism to the tech worker. If you want a hard default, I'll tighten FR-C3 to "in-memory only in v1".
2. Is the charts page expected to ship in the same release as the query API, or is it a follow-up? Current requirements assume same-release (FR-E1..E8 Must). If follow-up, I'll re-tier them to Should.
3. The adjudication of F8-prune-runs, F12-cycle-seconds, F13-waiting-children, and F21-upgrade-phase as IN/OUT (with caveats) is judgment-based on the "no new instrumentation" rule. If the plan council prefers a stricter reading, those four should move to OUT. Confirm.
