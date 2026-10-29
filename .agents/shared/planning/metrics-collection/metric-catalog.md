# Metric Catalog (v2) — the per-family metric specification

**Date** 2026-10-29 · **Status** DRAFT — grounding for `plan-overview.md` + `decisions.md` ·
**Scope** the **daemon-origin** metric surface; exporter-origin series are §6, outside the ≤2k
daemon budget; skill-metrics (`/api/skills/{id}/metrics`) is a **non-goal**, not catalogued ·
**Verified against** worktree `agents-ensemble-wt-metrics-v2` @ `feature/metrics-collection`
(tip `51bbfc000`; WS-A platform-factual correction round applied same day — §13, §11 C35) —
every `file:line` was read there; corrections to v1 and to the inventory
digest are logged in §11 · **Compat** Prometheus scrape model + Grafana, **Prometheus classic text exposition format**
(NOT OpenMetrics — MA-16 corrects the v1 mislabel); m9 —
VictoriaMetrics/Mimir **dropped**, no compatibility asserted for an untested stack.

---

## 0. Conventions

**0.1 Naming.** `ensemble_<subsystem>_<measure>[_<unit>]`; `_total` ⇒ counter; `_seconds` /
`_bytes` / `_ratio` ⇒ unit suffix. Names are **stable** — `plan-overview.md`'s alert table and
`decisions.md` use them verbatim; a rename is a breaking change.

**0.2 Canonical registry.** `daemon/checkpoint_metrics.py` (355 lines, **top-level**, not
`daemon/services/`) becomes the general registry, growing `register_counter` / `register_gauge` /
`register_histogram` / `register_summary` on top of the existing `Counter` (`:65`),
`_LabeledHistogram` (`:111`), `_HistogramSeries` (`:138`), `Histogram` (`:151`),
`render_metrics()` (`:304`). No `prometheus_client`, no OTel, no new PyInstaller surface.

**CR-3 record correction (architect Appendix A #4).** The two singletons DO have a production
consumer: `daemon/checkpoint_perf.py:28-30` imports them, observes at `:190`/`:198` and
increments at `:233`. The precise reuse story is therefore **"registry wired to collectors,
exposition unwired"** — NOT "unwired registry". Only `render_metrics()` itself has zero
non-test callers in production; the collectors are live and feeding the registry.

**Registry locking contract (A9).** Per-metric locks only — **no global registry lock** (one
wedged writer must not stall all families). **The defect this fixes:** `render_metrics()`
today reaches every series through the per-object getters (`.get()` — `checkpoint_metrics.py:95-104`,
`:224-248`), and each getter takes that object's `threading.Lock` (`:79` Counter, `:182`
Histogram). There is **no registry-level lock**, so a wedged writer holding one metric's lock
would make the **scrape hang** — the CR-1 failure mode reproduced inside the CR-1 mitigation. Writers update a family's own state under its own
short lock and atomically swap a published pointer (plain assignment, atomic under the GIL).
Render uses **non-blocking try-acquire** (bounded wait, e.g. 50 ms): on contention it serves
the **last published snapshot** and emits `# <family> unavailable: lock_contended`. Bucket
counts are stored **non-cumulative** and accumulated at render, so `Histogram.observe` is
O(1) (~300 ns) instead of the current O(B) cumulative scan (`checkpoint_metrics.py:220-222`,
~1394 ns) — the plan's bucket extensions (§8.11) therefore do not scale the hot path.

**Freeze rules (A27).** Import the listener on the **lifespan path** (not `daemon/api.py`
module top level) so the PyInstaller analyzer pulls it. Register aliases at
**registry-construction time** (not lazily) so a cold start still emits the deprecated names for
the remainder of their one-release window.

**Off-loop discipline (restated, A28).** The blanket "never adds event-loop work" claim is
literally false — every increment is loop or executor-thread work. The accurate rule: **no
blocking work, no I/O, no logging, and no per-chunk work on the event loop**, plus **≤1
`time.perf_counter()` read per observation window**. Metric increments inside `message_tap`
must sit **within the tap's existing `try/except`** (`daemon/services/message_tap.py:308-309`)
so a metric error cannot produce per-turn WARNING noise. Per-turn cost budget: ≈4 µs added
work per tool-round turn (8 counter incs + 2 histogram observes) against a multi-second turn
— **<0.01%**. CR-3 reuse story: the registry is **wired to collectors, exposition unwired** —
only `render_metrics()` is unwired in production (`checkpoint_perf.py:28-30` imports the
singletons, observes `:190`/`:198`, increments `:233`).

**0.3 One measurement, one name (CR-3) + alias mechanics (A11).** The two metrics that exist
carry non-canonical names — `message_api_checkpoint_list_total` (`:274`),
`message_api_saver_op_latency_seconds` (`:282`). Both are re-registered under canonical
`ensemble_*` names; the old names ride as **one-release deprecated aliases**. Alias mechanics:

- The alias is a **second exposition of the same series**, never a second measurement (no
  double-counting, no parallel collector).
- A **deprecation log line at first emission, once per process** (not per scrape).
- Removal procedure includes a documented Prometheus `absent()` guard: the legacy name stays for
  "one release + one burn-in", or a recording rule is provided; when the alias disappears any
  consumer still on `message_api_*` sees `absent()`.
- The legacy names violate the `^ensemble_` rule and are therefore **alias-only, never
  canonical**.
- Migration of the four test files referencing the legacy names
  (`message_api_checkpoint_list_total`, `message_api_saver_op_latency_seconds`) so the alias can
  be dropped cleanly.

No second exposition path.

**0.4 Buckets.** Every histogram's bucket set is stated once, in the **bucket-set annex
(§8.11)**. The checkpoint latency set today spans `0.001 → 10.0` s
(`daemon/checkpoint_metrics.py:59-63`) — **too short**: the 17 s writes and 73–99 s reads fall
into `+Inf` and collapse p95 to 10.0. F7 extends the top to `300.0` s.

**0.5 Labels (MA-12) + executable never-exported enforcement (A10).** Every label value set is
written out in full — no `...`, no "etc." — and lives in the **vocabulary annex (§8)**, the
single place each set is defined, with the `file:line` it was verified against. Family entries
reference the annex by number.

**Executable enforcement (three tiers + defence in depth):**
1. **Registration-time enum pin** — `register_histogram(name, label_keys, allowed_values={...})`
   rejects a wrong label *key* at registration (raises, mirroring today's `KeyError` at
   `checkpoint_metrics.py:206-211`); cost 0 at runtime.
2. **Observe-time reserved bucket** — a wrong label *value* is folded to `other` and counted in
   `ensemble_metrics_invalid_label_total{metric,label}` (~40 ns frozenset membership).
3. **Render-time allowlist predicate** — per-family label-name allowlist + per-label value
   membership + defence in depth (also the §7 never-exported hook).

Plus: **hard** cardinality cap (`limits.metrics_cardinality_cap` is currently **soft** in
plan §5 — make it **hard**: observe beyond the cap is rejected, not warned), name regex
`^ensemble_[a-z0-9_]+$`, and a label-value charset gate (no `=`, `,`, `"`, newline). Add the
poisoned-registry defence-in-depth test. All §8 vocabularies are **derived from code, never
hand-listed** (A1) — see the rule stated in §8.

**0.6 Three coexisting idioms (m10).** Each family names the idiom it draws from: **A** =
in-process registry (`daemon/checkpoint_metrics.py`; resets on restart) · **B** = PG table spine
(`resource_monitor` tables; durable) · **C** = log markers (`ensemble.log` — a *source* for
greenfield counters, never for a gauge). `/metrics` is a **VIEW** over these, never a parallel
collector (MA-3).

**0.7 Listener (CR-2).** All exposition goes through a **separate localhost-bound listener** on
its own port, one route (`GET /metrics`), bound `127.0.0.1` only — **never** a route on the main
public app (`daemon/api.py`, 8079 in dev). A bind failure degrades it to *disabled* (logged,
daemon continues), never a boot abort.

**Thread/loop topology (A12).** A **dedicated daemon thread** running stdlib
`http.server.ThreadingHTTPServer` bound to `127.0.0.1`, started from the FastAPI lifespan
(Option A). A shared-main-event-loop listener is **REJECTED** — it inherits the CR-1 failure.
**Concurrency contract (render ↔ wedged writer):** no global registry lock; render uses
non-blocking try-acquire (bounded wait, e.g. 50 ms) → serves the **last published snapshot**
plus `# <family> unavailable: lock_contended`; copy-on-publish pointer swap (plain assignment,
atomic under the GIL). **GIL split:** an I/O-bound wedge survives (I/O releases the GIL); a
CPU-spin wedge does **not** (GIL held continuously) — so CR-1's out-of-process witnesses
(node_exporter textfile, `up==0`/`absent()`, `scrape_duration_seconds`) remain **mandatory**.

**0.8 Kill-switch (MA-1).** `ENSEMBLE_METRICS_ENABLED` gates **every scrape** individually,
default **OFF**. OFF ⇒ the listener returns `200` with a valid empty body; the registry keeps
accumulating so switching ON loses no history. Registered as managed key `limits.metrics_enabled`
in `MANAGED_KEYS` (`daemon/services/settings_service.py:190`) — the registry default must mirror
the pydantic Field default exactly (one truth; the A4-knob lesson — *a managed-key-knob lesson, not an amendment reference*).

**Truthiness & layering (A14).** Truthy set = `{1, true, yes}` case-insensitive; everything
else — including `ture`, `on`, `enabled`, `0`, empty — is **OFF** (fail closed). Layering:
env explicit-OFF always wins; env UNSET defers to the managed-key chain (unset + managed key
on ⇒ ON, because env-unset is not a statement of intent; unset + default ⇒ OFF); explicit env
ON overrides the managed-key default. Plus **one boot log line** when the env is unset while
the managed key is on: "`ENSEMBLE_METRICS_ENABLED` unset; deferring to `limits.metrics_enabled`".
The per-request env read stays (~50–100 ns, no syscall; a TTL cache would defeat the
emergency-off purpose).

**0.9 Phases.** P0 foundation (listener, kill-switch, registry generalization, census
discipline, spine health gate, vocabulary freeze); P1a/P1b/P1c per-subsystem rollout; P2
exporters + dashboards + burn-in. **Re-partition of v1** (v1 put "P1" on everything) — the order
is forced by the dependency graph, see §2.1.

**0.10 Owner roles (house roles).** `developer` (code) · `devops` (infra/alerting) ·
`reviewer` (correctness of the metric's meaning) · `tester` (verification + burn-in).

**0.11 Degradation (MA-15).** **No family renders a zero for "I have no data."** A family whose
dependency is off emits **no series** (absent ≠ zero) unless its contract says the zero is real
(pool "no waiting clients", spine "table accepting rows"). **0.12 Cross-restart counters** — §10.

---

## 1. Summary table

`reuse` = an existing instrument generalized; `greenfield` = none exists, seam named.

| # | Family — canonical names | Type | Phase | Source | Series |
|---|---|---|---|---|---|
| F0.1 | `ensemble_metrics_up`, `ensemble_metrics_scrape_requests_total`, `ensemble_metrics_exposition_seconds` | gauge/counter/hist | P0 | greenfield (listener seam) | 16 |
| F0.2 | `ensemble_metrics_registry_measurements` | gauge | P0 | reuse `checkpoint_metrics.py:304` | 1 |
| F0.3 | `ensemble_resource_spine_tables_accepting_rows`, `ensemble_resource_spine_rows_written_total` | gauge/counter | P0 | reuse (resource_monitor spine) | 8 |
| F0.4 | `ensemble_metrics_vocabulary_version` | gauge | P0 | greenfield (freeze marker) | 1 |
| F1 | `ensemble_loop_lag_seconds`, `ensemble_loop_lag_samples`, `ensemble_loop_lag_overflow_total`, `ensemble_loop_lag_probe_state` | gauge×3/counter | P1a | reuse `wedge_capture/lag_probe.py` | 9 |
| F2 | `ensemble_thread_cpu_seconds`, `ensemble_thread_count` | gauge×2 | P1a | reuse `wedge_capture/sampler.py` | 22 |
| F3 | `ensemble_wedge_capture_total`, `ensemble_wedge_capture_write_total` | counter×2 | P1a | reuse `wedge_capture/triggers.py` | 5 |
| F4 | `ensemble_db_pool_native`, `ensemble_db_pool_requests_errors_total` | gauge/counter | P1a | reuse `readiness.py:525`,`:552`; `collector.py:29` | 8 |
| F5 | `ensemble_db_pool_checked_out`, `ensemble_db_pool_waiting_clients` | gauge×2 | P1a | reuse (derived + native, kept apart) | 3 |
| F6 | `ensemble_pg_backends`, `ensemble_pg_idle_in_txn_oldest_seconds`, `ensemble_pg_leaked_txn`, `ensemble_pg_census_last_success_timestamp_seconds` | gauge×4 | P1a | greenfield (census seam, §9) | 15 |
| F7 | `ensemble_checkpoint_op_seconds`, `ensemble_checkpoint_op_bytes`, `ensemble_checkpoint_slow_ops_total`, `ensemble_checkpoint_list_total` | hist×2/counter×2 | P1b | reuse `checkpoint_perf.py:190`,`:198`,`:91` | 22→33 |
| F8 | `ensemble_checkpoint_blobs_table_bytes`, `ensemble_checkpoint_table_bytes`, `ensemble_checkpoint_versions_per_thread`, `ensemble_checkpoint_prune_runs_total` | gauge×3/counter | P1b | reuse `maintenance.py:531` + greenfield (per-thread) | 9 |
| F9 | `ensemble_readiness_probe_total`, `ensemble_readiness_grace_active`, `ensemble_readiness_failure_streak`, `ensemble_readiness_component_state` | counter/gauge×3 | P1b | reuse `readiness.py:64-79` | 53 |
| F10 | `ensemble_job_queue_depth`, `ensemble_job_claim_latency_seconds`, `ensemble_dead_letter_items`, `ensemble_job_queue_items_created_total` | gauge/hist/gauge/counter | P1b | reuse (admission_state) + greenfield (claim latency) | 21 |
| F11 | `ensemble_workers_total`, `ensemble_workers_busy`, `ensemble_worker_pool_wakeup_efficiency`, `ensemble_worker_pool_events_total`, `ensemble_task_heartbeat_age_seconds`, `ensemble_task_heartbeat_cadence_seconds` | gauge×3/counter/gauge×2 | P1b | reuse `worker_pool.py:1518`; `task/models.py:284` | 16 |
| F12 | `ensemble_sweep_cycle_seconds`, `ensemble_sweep_cycles_total`, `ensemble_sweep_errors_total`, `ensemble_sweep_rows_total` | gauge/counter×3 | P1b | reuse (6 sweeps + 7 RDRS lanes) | 69 |
| F13 | `ensemble_instance_status_count`, `ensemble_instance_liveness_count`, `ensemble_waiting_children_count`, `ensemble_waiting_children_max_age_seconds`, `ensemble_wake_turns_total`, `ensemble_instance_lifecycle_total` | gauge×4/counter×2 | P1b | reuse `instance/models.py:20-32` + greenfield (wake outcome) | 33 |
| F14 | `ensemble_llm_tokens_total`, `ensemble_llm_token_usage_missing_total` | counter×2 | P1c | reuse `graph.py:3776-3784` (terminal-usage branch, A2) | 3 |
| F15 | `ensemble_llm_request_total`, `ensemble_llm_request_seconds` | counter/hist | P1c | reuse `llm_error_classifier.py:764-819` | 23 |
| F16 | `ensemble_llm_stream_stalls_total`, `ensemble_llm_stream_watchdog_sweep_total` | counter×2 | P1c | reuse `llm_stream_watchdog.py:456` | 5 |
| F17 | `ensemble_mcp_server_state`, `ensemble_mcp_failures_total` | gauge/counter | P1c | reuse `mcp/init_guard.py:145-147` | 40 |
| F18 | `ensemble_mcps_gateway_sessions`, `ensemble_mcps_gateway_session_total`, `ensemble_mcps_gateway_tombstones`, `ensemble_mcps_gateway_audit_total` | gauge×2/counter×2 | P1c | reuse `services/mcps_gateway/` | 9 |
| F19 | `ensemble_compaction_fired_total`, `ensemble_compaction_occupancy_ratio`, `ensemble_compaction_precall_estimate_tokens` | counter/gauge×2 | P1c | reuse `compaction.py:1128`,`:2299` | 7 |
| F20 | `ensemble_turn_events_total`, `ensemble_turn_duration_seconds`, `ensemble_tool_calls_total` | counter/hist/counter | P1c | reuse `message_tap.py:234` | 69 |
| F21 | `ensemble_journal_file_bytes`, `ensemble_journal_history_entries`, `ensemble_journal_op_seconds`, `ensemble_journal_ops_total`, `ensemble_upgrade_phase_seconds`, `ensemble_upgrade_phase_total`, `ensemble_upgrade_release_current`, `ensemble_upgrade_journal_events_total` | gauge×3/hist/counter×3/gauge | P1c | reuse `tools/upgrade_journal.py` | 66 |
| F22 | `ensemble_process_cpu_seconds` + host/PG exporter series | gauge | P2 | greenfield (textfile + off-the-shelf) | 5 shell / ~67 exporter |

**Daemon-origin total: 533** (F0.1→F21) + **5 shell-origin** = **538**. Exporter-origin ≈67 (§6).
**Budget ≤2k — ≈27 % consumed (headroom 1 467).**

*Reconciliation basis:* §7 counts the §1 row values, with the one range cell (**F7**
`22→33`) taken at its **today** value **22** — the bytes histogram's +11 arrives with the P1b
byte wiring (catalog G-5). Row sums: P0 26 + P1a 62 + P1b 223 + P1c **222** = **533**. The
tool-category change (F20 57 → 69 = +12) moved P1c 210 → 222 and the daemon total 521 → **533**.

**Platform note (WS-A correction, 2026-10-29).** Every series count above is
platform-independent — the platform support matrix (§13) changes **which fields render** on
macOS-Darwin vs Linux, never **how many series exist**. The prod deployment host is macOS, so the
spine-derived families (F0.3 etc.) are dark in prod until `feature/resource-monitor-flusher-fix@53547b915`
merges; see §13 for the per-family field split and the consumer-visible effects.

---

## 2. P0 — Foundation

**2.1 Why this partition.** v1 put "P1" on everything. The dependency graph forces an order:
listener + registry + spine gate (P0) → runtime/pool/census (P1a) → checkpoint/jobs/sweeps/
instances (P1b) → LLM/MCP/compaction/journal (P1c) → exporters (P2). P1a comes first because
the census (F6) and the pool families (F4/F5) are the incident root-causes and they validate
the registry under load before 200+ more series land on it.

### F0.1 — Exposition listener & per-request kill-switch

- **Name:** `ensemble_metrics_up` (gauge, no labels) · `ensemble_metrics_scrape_requests_total{outcome}`
  (counter) · `ensemble_metrics_exposition_seconds` (histogram, buckets §8.11).
- **Type:** gauge; counter; histogram (13 series incl. `_count`/`_sum`). **Labels:** `outcome` ∈
  `ok`, `error` (§8.3). No other labels.
- **Source:** **greenfield** — seam is the new `daemon/services/metrics_listener.py`. Why
  greenfield: `daemon/api.py` has **no** `/metrics` route and the repo contains **no**
  `prometheus_client` / OTel import — no exposition endpoint exists at all. The listener is a
  separate app on its own bound port (CR-2), never a route on `daemon/api.py`'s app.
- **Phase** P0 · **Owner** developer (implement), devops (port + scrape config) ·
  **Series estimate** 1 + 2 + 13 = **16** (`ensemble_metrics_up` is a single **no-label** scalar —
  the earlier `{component}` info split is dropped, so the family entry and the §1 summary row
  agree; see decisions.md §H H-2).
- **Degradation:** kill-switch OFF ⇒ `ensemble_metrics_up` = `0`; the scrape counter still
  increments `outcome="ok"` (the scrape happened, the body was empty); **every other family
  renders no series**. The exposition histogram records the empty render, so the operator sees
  "listener alive, switch off".

### F0.2 — Canonical registry generalization

- **Name:** `ensemble_metrics_registry_measurements` (gauge, no labels). **Type:** gauge — count
  of registered measurements. **Labels:** none.
- **Source:** **reuse** — `daemon/checkpoint_metrics.py:304` `render_metrics()` becomes the
  registry renderer; the two existing singletons (`:273`, `:281`) become registry-registered
  instances under their canonical names.
- **Clarification (A8):** `ensemble_metrics_registry_measurements` counts **measurements**
  (registered measurement objects), NOT series — dashboards must not conflate it with the
  ≤2 k series budget. CR-3 record correction: the registry is **wired to collectors**
  (`checkpoint_perf.py:28-30` imports the singletons, observes `:190`/`:198`, increments
  `:233`); only `render_metrics()` itself has zero non-test callers.
- **Phase** P0 · **Owner** developer; reviewer signs the naming migration · **Series estimate** **1**.
- **Degradation:** always present — a property of the registry, not of a dependency.

### F0.3 — Resource-spine health gate

- **Name:** `ensemble_resource_spine_tables_accepting_rows{table}` (gauge) ·
  `ensemble_resource_spine_rows_written_total{table}` (counter). **Type:** gauge; counter.
  **Labels:** `table` ∈ `resource_daemon_bucket`, `resource_instance_sample`,
  `resource_pid_map`, `resource_limit_policy` (§8.9).
- **Source:** **reuse** — the resource-monitor spine. `daemon/services/resource_monitor/flusher.py`
  writes in one `engine.begin()` window per flush (`flusher.py:336`); the counters are
  `FlusherCounters` (`flusher.py:161`). `resource_limit_policy` accepts zero rows **by design**
  (`daemon/routers/resources.py:1095`) ⇒ its gauge renders `1` (accepting) while its row counter
  stays `0` — the two answer different questions and the asymmetry is deliberate.
- **CANONICAL-COUNTERS RULE (WS-A correction, 2026-10-29).** The resource spine's own counters —
  including the corrected skipped-sample counters — are the **canonical source for
  skipped/daemon-resource facts**; no parallel counters are introduced by the metrics work. Any
  `/metrics` family that needs a "samples skipped / spine healthy" fact **reads the spine's
  counters and never re-measures**. The exact names, verified at
  `feature/resource-monitor-flusher-fix@53547b915` (`daemon/services/resource_monitor/sampler.py`,
  `SamplerCounters`): `counters.ticks` = ticks that **PRODUCED a sample** (surfaced as the
  `samples` field of `/api/resources/self-cost`); `counters.missed_samples` = ticks skipped for
  overrun **or any structural read failure** (the documented contract, previously violated — only
  the overrun path bumped it); `counters.ticks_skipped` = the same skip set, in parity with
  `missed_samples`; a skipped sample is **excluded from `counters.ticks`**; the first structural
  read failure logs **one one-shot WARNING** (`_no_snapshot_warned` guard, `_missing_table_warned`
  precedent). Flusher side: `FlusherCounters` (`flush.batches` / `flush.bytes` / `flush.errors`)
  remain the canonical write-side facts. No planned family in this catalog proposes its own
  skipped-sample counter (the existing `skipped` vocabulary values — sweep row outcome §8.4, wake
  outcome `skipped_busy_instance` §8.1, prune `skipped_non_postgres` §8.9 — are different domains,
  not sample-skip counters); if one is ever proposed, it is replaced by a reference to the spine's.
- **Phase** P0 · **Owner** developer · **Series estimate** 4 + 4 = **8**.
- **Degradation:** resource monitor disabled (`RESOURCE_MONITOR_ENABLED=false`,
  `daemon/config.py:854`) ⇒ **both metrics render no series** and the spine-derived families
  render nothing. Platform dimension (catalog §13): on the **macOS prod host before the WS-A
  merge**, the monitor runs but the sampler produced no rows (`proc_readers` returned `None` on
  every tick), so both metrics read `0`-producing — the gate below BLOCKS, not passes. After the
  merge the Darwin ps-based fallback restores the producer and the gate can go green (daemon-level
  fields only; per-child stays dark — A2 non-goal, §13).
- **P0 exit criterion:** **all four tables accepting rows** — and the producer precondition is
  the WS-A merge: this gate is satisfied only on a tree that carries
  **`feature/resource-monitor-flusher-fix@53547b915`** ("restore the dark resource-telemetry tier
  (the macOS /proc gap)"), which merges to latest **before P0 executes**. If that branch is **not
  merged, this gate BLOCKS** — it does not silently pass and does not downgrade to a warning
  (MA-3; plan §4 P0-spine). **Factual correction (supersedes the earlier text, see §11 C35):** the
  v3 framing that the per-instance tier was "broken by the `estimated` column's
  `INTEGER`/`BOOLEAN` mismatch (PG SQLSTATE 42804) rolling back all four tables through the shared
  `engine.begin()`, with the flusher still binding a Python `bool` at
  `daemon/services/resource_monitor/flusher.py:770`" is **REFUTED**. The prod column is
  `boolean NOT NULL` (`daemon/models/resource_monitor.py:231-233`,
  `Column("estimated", Boolean, nullable=False)`), the `bool(...)` bind at `flusher.py:770` is
  **correct** against it (the WS-A bind-audit test,
  `tests/postgres/test_resource_monitor_flusher_bind_pg.py` @ 53547b915, asserts every
  flusher-bound column against the live `information_schema` type and round-trips a real bool
  into the real boolean column), and the window-rollback amplifier was already fixed
  (`7be1b40bf` per-tier SAVEPOINT + `69cea24ea` `flush.errors` surfacing; the historical 42804
  account in the `flusher.py:790-806` docstring is exactly that — history). The **real** reason the
  tier was dark: **a dead PRODUCER on macOS** — the prod daemon runs on Darwin, where there is no
  `/proc` and no cgroup v2, so `proc_readers` returned `None` on every tick, the sampler skipped
  every tick, and the flusher had nothing to write. Live signature (prod, 2026-10-29):
  samples climbing, `flush {batches:1139, bytes:0, errors:0}` — **"produced nothing", not "write
  failed"** — while `resource_pid_map` kept climbing (45,575 rows at commit time) because it is fed
  by the spawn path, **masking the outage**. **F0.3 does not report green until the spine writes
  land on real PostgreSQL and the 4-table census is non-zero.**

### F0.4 — Vocabulary freeze marker

- **Name:** `ensemble_metrics_vocabulary_version` (gauge, no labels). **Type:** gauge — an
  integer pinned to the §8 annex revision. **Labels:** none.
- **Source:** **greenfield** — a module-level int in `daemon/checkpoint_metrics.py`. Why
  greenfield: no existing version marker exists for a label vocabulary.
- **Phase** P0 · **Owner** developer; reviewer owns the annex text · **Series estimate** **1**.
- **Degradation:** always present.
- **Versioned amendment procedure (A24):** label NAMES + closed enums are pinned to current code
  at P0. Any vocabulary change bumps `ensemble_metrics_vocabulary_version`. A bump that changes a
  literal label VALUE appearing in shipped PromQL is a **breaking alert change** and needs a
  stated compatibility rule. Paired with plan §4 P0 exit criteria.

---

## 3. P1a — Runtime, wedge, pools, PostgreSQL census

### F1 — Event-loop lag (MA-4)

- **Name:** `ensemble_loop_lag_seconds{stat}` (gauge) · `ensemble_loop_lag_samples` (gauge) ·
  `ensemble_loop_lag_overflow_total` (counter) · `ensemble_loop_lag_probe_state{state}` (gauge).
  **Type:** gauges + one counter — **NOT a histogram**: MA-4, the probe is a 300-sample rolling
  window with a *computed* p95, not buckets. Computed quantiles ship as gauges.
  **Labels:** `stat` ∈ `p50`, `p95`, `max` · `state` ∈ `initializing`, `ok`, `unknown`, `stopped`
  (§8.3).
- **Source:** **reuse** — `daemon/services/wedge_capture/lag_probe.py`. `MAX_LAG_SAMPLES = 300`
  (`:59`, ≈5 min at 1 Hz); quantiles computed in `LagHistogram.as_dict()` — `max` `:114`, `p95`
  `:115`, `p50` `:116` — nearest-rank (`:107`); `overflow_count` `:115-119`; snapshot entry
  `LoopLagProbe.snapshot_histogram()` `:185`.
- **Phase** P1a · **Owner** developer (wire), devops (alert) · **Series estimate** 3+1+1+4 = **9**.
- **Degradation:** the probe is **dark when `ENSEMBLE_WEDGE_CAPTURE_ENABLED` is off** (the
  watchdog thread that drives it never starts). Dark ⇒ `ensemble_loop_lag_samples` = `0`, the
  `stat` gauges render **no series**, `ensemble_loop_lag_overflow_total` = `0`,
  `ensemble_loop_lag_probe_state{state="stopped"}` = `1`. The dependency is on the HELP text.
- **Alert change (MA-4):** v1's `histogram_quantile(0.95, rate(ensemble_loop_lag_seconds_bucket[5m])) > 2`
  is **invalid** (no `_bucket` series exists). Correct: `ensemble_loop_lag_seconds{stat="p95"} > 2`.

### F2 — Thread CPU attribution (MA-2)

- **Name:** `ensemble_thread_cpu_seconds{thread_group}` (gauge) · `ensemble_thread_count{thread_group}`
  (gauge). **Type:** gauge — CPU seconds accumulated by the group since the last sample (a
  per-tick jiffy delta, **not** a monotonic counter). **Labels:** `thread_group` ∈ 11 values (§8.2).
- **Source:** **reuse** — `daemon/services/wedge_capture/sampler.py` is the ONE `/proc` walker
  (per-thread jiffies `:160`, process jiffies `:175`, `/proc/self/task/<tid>/comm` `:151`,
  Python-thread-name map `:192`). **One-/proc-dialect rule:** post-process bucketing only, never
  a second sampler (MA-2). **P1a prerequisite:** worker threads are **unnamed** — `Worker` calls
  `super().__init__(daemon=True)` at `daemon/services/worker_pool.py:253` with no `name=`, so
  every worker is `Thread-N` and the `worker_pool` vs `chat_worker_pool` split is **not
  observable from `/proc` today**. P1a must name them at construction
  (`name=f"worker-{lane}-{worker_id}"`; lane vocabulary `default` / `chat`,
  `daemon/services/worker_pool.py:207`) before F2 can emit those two groups honestly. Until then
  both fold into `other`.
- **Phase** P1a · **Owner** developer · **Series estimate** 11 + 11 = **22**.
- **Degradation:** wedge capture off ⇒ the sampler does not run ⇒ **F2 renders no series**.
  **Platform dimension (WS-A correction, catalog §13):** the wedge sampler is **`/proc`-only** —
  WS-A's Darwin fallback lives in `resource_monitor/proc_readers.py` (`_read_platform_daemon_snapshot`
  @ `53547b915`) and the WS-A commit touches nothing under `wedge_capture/`; per-thread jiffies
  (`/proc/self/task/<tid>/stat`, `:151`) have no portable fallback. On the **macOS prod host F2 is
  therefore dark today and stays dark after the WS-A merge** — thread-CPU attribution is a
  Linux-host capability, not a WS-A deliverable (dev-box degradation and prod coincide here).

### F3 — Wedge-capture counters

- **Name:** `ensemble_wedge_capture_total{reason}` (counter) ·
  `ensemble_wedge_capture_write_total{outcome}` (counter). **Type:** counter; counter.
  **Labels:** `reason` ∈ `sustained_cpu`, `sustained_loop_lag`, `thread_concentration` ·
  `outcome` ∈ `ok`, `failed` (§8.3).
- **Source:** **reuse** — trigger evaluation sites
  (`daemon/services/wedge_capture/triggers.py:202`, `:237`, `:281`) and the watchdog's capture
  dispatch (`daemon/services/wedge_capture/watchdog.py:417`).
- **Phase** P1a · **Owner** developer · **Series estimate** 3 + 2 = **5**.
- **Degradation:** wedge capture off ⇒ **no series** (the trigger never fires). The v1
  `ensemble_db_pool_checkout_timeouts` family is deliberately **absent** here — see F4.

### F4 — DB pool native gauges + request errors (CR-4)

- **Name:** `ensemble_db_pool_native{pool,quantity}` (gauge) ·
  `ensemble_db_pool_requests_errors_total{pool,kind}` (counter). **Type:** gauge; counter.
  **Labels:** `pool` ∈ `manager`, `checkpoint` · `quantity` ∈ `size`, `max`, `available`,
  `overflow` (native quantities only) · `kind` ∈ `waiting_exception`, `too_many_requests`,
  `unclassified` (§8.9).
- **Source:** **reuse** — `daemon/services/readiness.py:525` `sample_sqlalchemy_pool_state`
  (`checkedout`/`size`/`overflow`) and `:552` `sample_psycopg_pool_state` (`pool_size`/
  `pool_max`/`pool_available`/`requests_waiting`); the same shape is published at `/readyz` →
  `detail.pool_state` (`daemon/api.py:5015`).
- **CR-4 — the timeout counter does not exist.** `psycopg_pool` 3.3.1 exposes no
  checkout-timeout counter. `daemon/services/wedge_capture/collector.py:24-26` states it
  verbatim: `pool_checkout_timeouts` is **ALWAYS `None`** with
  `pool_checkout_timeouts_reason = "not_exposed_by_psycopg_pool_3.3.1"`, and `requests_errors`
  "is a SUPERSET — it counts every waiting exception plus `TooManyRequests`". Therefore:
  (a) `ensemble_db_pool_requests_errors_total` is the primary family, **documented as a
  superset, not a timeout count**; (b) `kind="unclassified"` is the **only** value emitted today
  (psycopg exposes the aggregate, not the breakdown) — the `kind` label exists so the follow-up
  seam can split it without a breaking label change; (c) the v1 name
  `ensemble_db_pool_checkout_timeouts_total` is **retired** and the PoolExhausted alert is
  **rebased** onto `increase(ensemble_db_pool_requests_errors_total[5m]) > 0`; (d) **optional
  follow-up (outside this catalog's P1 scope):** instrument the daemon's own checkout seams to
  emit `kind="waiting_exception"` / `kind="too_many_requests"` precisely. The SQLAlchemy manager
  pool raises `TimeoutError` on checkout and has **no** `requests_errors` equivalent ⇒
  `pool="manager"` renders **no** series for this counter — an absence, not a gap.
- **Phase** P1a · **Owner** developer · **Series estimate** native gauge: `manager` yields
  `size`,`max`,`available`,`overflow` (4), `checkpoint` yields `size`,`max`,`available` (3;
  psycopg has no `overflow`) ⇒ **7**; request-errors counter: `checkpoint` only,
  `kind="unclassified"` ⇒ **1**. Total **8**.
- **Degradation:** the saver pool is not pool-backed (SQLite) or `get_stats` unavailable ⇒
  `sample_psycopg_pool_state` returns `None` ⇒ the `pool="checkpoint"` series render no series.
  Absent manager pool ⇒ `pool="manager"` renders no series.

### F5 — DB pool derived vs native quantities (m2)

- **Name:** `ensemble_db_pool_checked_out{pool}` (gauge) ·
  `ensemble_db_pool_waiting_clients{pool}` (gauge). **Type:** gauge. **Labels:** `pool` ∈
  `manager`, `checkpoint` (§8.9).
- **Source:** **reuse**, with the derived/native split kept honest (m2):
  `ensemble_db_pool_checked_out` is **DERIVED** — `pool_size − pool_available`, clamped at 0
  (there is no native key; `daemon/services/wedge_capture/collector.py:19-20`) — and it is a
  **separate metric** so the derivation is visible in the name, not merged into a `state=` label
  that would pretend parity with native quantities. `ensemble_db_pool_waiting_clients` is
  **NATIVE** — `requests_waiting`, which exists on the **async pool only** (`collector.py:23`);
  the manager (sync SQLAlchemy) pool has no such counter ⇒ `pool="manager"` renders **no series**.
- **Phase** P1a · **Owner** developer · **Series estimate** 2 + 1 = **3**.
- **Degradation:** same as F4 — absent pool ⇒ absent series.

### F6 — PostgreSQL census (MA-13)

- **Name:** `ensemble_pg_backends{state}` (gauge) ·
  `ensemble_pg_idle_in_txn_oldest_seconds` (gauge) · `ensemble_pg_leaked_txn{site}` (gauge) ·
  `ensemble_pg_census_last_success_timestamp_seconds` (gauge). **Type:** gauges.
  **Labels:** `state` ∈ 7 values (PostgreSQL's `pg_stat_activity.state` domain, snake-cased for
  label safety, plus `unknown` for NULL) · `site` ∈ the 6-value fixed fragment→site map
  (§8.9, §9.2).
- **Source:** **greenfield** — seam is a **new** module `daemon/services/metrics_census.py`. Why
  greenfield: the daemon has **no** `pg_stat_activity` census today. The closest artefacts are
  the wedge-capture's DB sub-capture (`daemon/services/wedge_capture/db_capture.py`, which runs
  **only during a capture**, on a one-shot connection with
  `CAPTURE_APPLICATION_NAME = "wedge-capture"` at `:62`) and the readiness leak oracle
  (`daemon/services/readiness.py:784` `_evaluate_leak`, which reads *pool counters*, not
  `pg_stat_activity`). Neither is a census. The census inherits `db_capture.py`'s discipline
  (§9) — dedicated non-pooled connection, `application_name='ensemble-metrics-census'`,
  `statement_timeout`, ≥15 s cadence.
- **Phase** P1a · **Owner** developer (census), devops (connection grants) · **Series estimate**
  7 + 1 + 6 + 1 = **15**.
- **Degradation:** census disabled / connection refused / `statement_timeout` ⇒ **all four
  metrics render no series** and `ensemble_pg_census_last_success_timestamp_seconds` goes stale —
  a stale timestamp is the operator's cue (the metric is the census's own health signal). The
  census **never** runs through the P1/P2 pools (§9.1).

---

## 4. P1b — Checkpoint, jobs, sweeps, instances, lifecycle

### F7 — Checkpoint op latency / bytes / slow ops (CR-3)

- **Name:** `ensemble_checkpoint_op_seconds{op}` (histogram) ·
  `ensemble_checkpoint_op_bytes{op}` (histogram) · `ensemble_checkpoint_slow_ops_total{op}`
  (counter) · `ensemble_checkpoint_list_total` (counter). **Type:** histogram; histogram;
  counter; counter. **Labels:** `op` ∈ `aget`, `aput`, `adelete`, `alist` (§8.9 — the set
  declared in the registry HELP text, `daemon/checkpoint_metrics.py:281-290`; `alist` is
  migration-only). **Narrowed until the write path is instrumented (A6/A7):** only `op="aget"`
  fires in production today; `aput`/`aput_writes` bypass `time_saver_op` and run through
  `daemon/checkpoint_adapter.py:377-379`. **After the write-path seam lands, re-derive the `op`
  enum from observed call sites rather than inheriting the legacy HELP text
  (`checkpoint_metrics.py:281-290`).** Buckets §8.11.
- **Source:** **reuse — do not duplicate** (CR-3). The observation sites already exist:
  `daemon/checkpoint_perf.py:190` (log-gated path) and `:198` (ungated path) both call
  `saver_op_latency_seconds.labels(op=op).observe(...)`; `:191` / `:199` call
  `record_slow_saver_op(...)` — the bounded slow-op ledger (`:91`,
  `SLOW_SAVER_LEDGER_MAXLEN = 128` at `:63`, `DEFAULT_SLOW_SAVER_CUT_MS = 5000` at `:60`).
  **The slow-op ledger is already the CheckpointSlow alert source — REUSE it; do not build a
  parallel detector.** `ensemble_checkpoint_list_total` replaces
  `message_api_checkpoint_list_total` (`daemon/checkpoint_metrics.py:273`), incremented via
  `daemon/checkpoint_perf.py:218`. **Bytes:** `daemon/checkpoint_perf.py:148`
  `log_saver_op(..., bytes_=0, deleted=0)` already carries `bytes_` at the seam ⇒ the bytes
  histogram is a **reuse observation, not a new instrument** — but the hot path `time_saver_op`
  (`daemon/checkpoint_perf.py:202`, docstring `:205`) forwards `bytes_=0`, so the bytes histogram is
  **under-reported for `aget`** until P1b wires the real blob byte count.
- **Write-path instrumentation task (A6/A7, Q1 ratified default):** extend the **existing**
  `checkpoint_adapter.py` charge seam (`_charge_checkpoint_read` at `:371-374`) to
  `aput`/`aput_writes` (`:377-379`) in P1b with its own risk row — it touches the checkpoint
  write path under stress during the incidents this plan exists to diagnose. Until it lands,
  the declared `op` enum is narrowed to the observable set (`aget`) and the write-side
  incident mapping is restated in plan §1 (writes are a stall/contention artifact, not
  throughput).
- **Phase** P1b · **Owner** developer; reviewer pins the bucket set · **Series estimate** only
  `op="aget"` fires in production (the sole prod call site is `daemon/persistence.py:509`,
  `time_saver_op("aget", ...)`) ⇒ seconds histogram 19, bytes histogram 0 today → 10 after the
  byte wiring, slow-ops counter 1, list counter 1 ⇒ **22 today, 33 after the byte wiring**.
  Fully-wired ceiling: 76 + 40 + 4 + 1 = 121.
- **Degradation:** the metric side-effect is recorded **regardless of the log gate**
  (`daemon/checkpoint_perf.py:187-189` — "Metric records REGARDLESS of the log gate"), so
  `CHECKPOINT_PERF_LOGS=0` does **not** starve the histogram. A non-psycopg backend (SQLite)
  ⇒ the `op` series are absent.

### F8 — Checkpoint store size gauges

- **Name:** `ensemble_checkpoint_blobs_table_bytes` (gauge) ·
  `ensemble_checkpoint_table_bytes{table}` (gauge) ·
  `ensemble_checkpoint_versions_per_thread` (gauge, p95) ·
  `ensemble_checkpoint_prune_runs_total{outcome}` (counter). **Type:** gauge; gauge; gauge;
  counter. **Labels:** `table` ∈ `checkpoints`, `checkpoint_writes`, `checkpoint_blobs` ·
  `outcome` ∈ `dry_run`, `executed`, `skipped_non_postgres`, `error` (§8.9).
- **Source:** **reuse** — `daemon/services/maintenance.py:531-536`
  `_VACUUM_FULL_TABLE_BYTES_SQL` sums `pg_relation_size` over exactly `checkpoints`,
  `checkpoint_writes`, `checkpoint_blobs`. `ensemble_checkpoint_blobs_table_bytes` is a
  **split-out** of that one round trip, and `ensemble_checkpoint_table_bytes{table}` is its
  generalization. The per-thread version count is **greenfield** — the seam is the
  `checkpoint_prune` candidate scan (`daemon/services/checkpoint_prune.py:224`), which already
  materialises `(thread_id, checkpoint_ns, _ckpt_count)` triples.
- **Phase** P1b · **Owner** developer · **Series estimate** 1 + 3 + 1 + 4 = **9**.
- **Degradation:** PostgreSQL only ⇒ on SQLite these render **no series**
  (`checkpoint_prune.py:195-199` is the refusal precedent). The query needs `pg_class` +
  `pg_relation_size` — read-only superuser is enough.

### F9 — Readiness probe outcomes and grace (MA-5)

- **Name:** `ensemble_readiness_probe_total{component,outcome}` (counter) ·
  `ensemble_readiness_grace_active` (gauge) · `ensemble_readiness_failure_streak{component}`
  (gauge) · `ensemble_readiness_component_state{component}` (gauge). **Type:** counter; gauge;
  gauge; gauge. **Labels:** `component` ∈ 4 (the composite's components) · `outcome` ∈ the
  **normative 11-class readiness taxonomy**; the grace-eligible subset is a **fixed subset
  published on the HELP text**, not a label (§8.3).
- **Source:** **reuse** — the composite is already computed by the background refresher and
  published at `daemon/api.py:5015` `/readyz` (`detail.component_classes`, `detail.grace`,
  `detail.leak_signal`, `detail.failure_streak`). MA-5: **grace is already on `/readyz` — the
  metric family READS the published composite, it never re-probes.**
- **Phase** P1b · **Owner** developer; devops owns the FalseDegraded alert · **Series estimate**
  4 × 11 = 44, + 1 + 4 + 4 = **53**.
- **Degradation:** no composite yet (refresher has not ticked) ⇒ the readiness payload carries
  the `boot_incomplete` class (`daemon/api.py:5053-5056`) ⇒
  `ensemble_readiness_probe_total{component=*,outcome="boot_incomplete"}` increments and
  `ensemble_readiness_grace_active` = `0`. A composite that cannot be reached at all ⇒ the
  family renders **no series**.

### F10 — Job queue depth and claim latency (m3)

- **Name:** `ensemble_job_queue_depth{admission_state}` (gauge) ·
  `ensemble_job_claim_latency_seconds` (histogram, buckets §8.11) ·
  `ensemble_dead_letter_items` (gauge) · `ensemble_job_queue_items_created_total` (counter).
  **Type:** gauge; histogram; gauge; counter. **Labels:** `admission_state` ∈ `queued`, `active`,
  `done`, `dead` (§8.10 — the `AdmissionState` enum, the **sole write authority** for queue
  gating).
- **Source:** **reuse** for depth (the `job_queue_items.admission_state` column with index
  `idx_job_queue_admission_state`, `daemon/repositories/job_queue/models.py:283`); the live
  surfaces already exist — `GET /api/queues`, `/api/jobs`, `/api/dlq`, `/api/work`.
  **Claim latency is GREENFIELD (m3):** there is **no** existing claim-latency instrument — the
  only related signal is the in-RAM `WorkerPool._stats["empty_claim_attempts"]`
  (`daemon/services/worker_pool.py:1383`), which counts empty polls, not enqueue→claim latency.
  **Seam:** the claim boundary inside `daemon/services/worker_pool.py` `wait_for_work` → the
  task-claim call, where the enqueue timestamp
  (`daemon/repositories/message_queue/models.py:83` `enqueued_at`) and the claim moment are both
  available. The observation is taken **once**, at the claim site, and carried — never
  re-derived downstream.
- **Phase** P1b · **Owner** developer · **Series estimate** 4 + 14 + 1 + 1 = **20** (the
  summary table rounds to 21; the extra is a per-queue `queue_id` variant deliberately **not**
  claimed — `queue_id` is unbounded).
- **Degradation:** queue repository unavailable ⇒ no depth series. `job_queue_items` carries
  **only** `created_at` as a TEXT ISO string (`daemon/repositories/job_queue/models.py:220`
  and `:357`) — no `started_at`/`completed_at` ⇒ **queue-item durations are NOT derivable**; any
  such metric is greenfield by construction and is not claimed here.

### F11 — Worker pool utilization and task heartbeat age (m4)

- **Name:** `ensemble_workers_total{pool}` (gauge) · `ensemble_workers_busy{pool}` (gauge) ·
  `ensemble_worker_pool_wakeup_efficiency{pool}` (gauge) ·
  `ensemble_worker_pool_events_total{pool,event}` (counter) ·
  `ensemble_task_heartbeat_age_seconds` (gauge) · `ensemble_task_heartbeat_cadence_seconds`
  (gauge). **Type:** gauge; gauge; gauge; counter; gauge; gauge. **Labels:** `pool` ∈ `worker`,
  `chat` · `event` ∈ the **complete** `_stats` key set (§8.10).
- **Source:** **reuse** — `WorkerPool.get_stats()` (`daemon/services/worker_pool.py:1518`)
  returns `num_workers`, `notifications_sent`, `empty_claim_attempts`,
  `workers_woken_by_timeout`, `claims_skipped_due_to_busy_instance` and the computed
  `wakeup_efficiency` (`:1537` — `notifications / max(1, notifications + empty_claims)`). The
  "busy" count is derived the way the live tool derives it
  (`daemon/tools/instance_read.py:2092` — a worker is busy when its `_current_task_id` is not
  `None`). These counters are in-RAM and **lost on restart** (§10).
  **Heartbeat age (m4):** source column `daemon/repositories/task/models.py:284`
  `last_heartbeat_at` — a `timestamp without time zone` column, so bind/compare with
  `now_utc_naive()` per the project timestamp convention. **Cadence:** the per-worker
  `TaskHeartbeat` thread updates it at `DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0`
  (`daemon/services/worker_pool.py:45`; constructor parameter at `:197`). The metric publishes
  the **configured cadence** as its own series, so the operator can tell "heartbeat 40 s old,
  cadence 30 s" (stale) from "heartbeat 40 s old, cadence 300 s" (fine). The value is read from
  the pool's constructor argument, never hard-coded.
- **Phase** P1b · **Owner** developer · **Series estimate** 2 + 2 + 2 + 8 + 1 + 1 = **16**.
- **Degradation:** no worker pool ⇒ the pool-scoped series render **no series**;
  `ensemble_task_heartbeat_age_seconds` renders `0` only when there are **zero** tasks (a real
  zero); with tasks present and no heartbeat row it renders the age of the oldest missing
  heartbeat — the alarm condition.

### F12 — Sweep cycle and row counters

- **Name:** `ensemble_sweep_cycle_seconds{lane}` (gauge) · `ensemble_sweep_cycles_total{lane}`
  (counter) · `ensemble_sweep_errors_total{lane}` (counter) ·
  `ensemble_sweep_rows_total{lane,outcome}` (counter). **Type:** gauge; counter; counter; counter.
  **Labels:** `lane` ∈ **13 values** (§8.4: the 7 RDRS lanes + the 6 standalone sweep services,
  with each configured interval); `outcome` ∈ 6 values (§8.4).
- **Source:** **reuse** — each service exposes `counters()`:
  `watch_reconcile_sweep.py:356` (`ticks`, `fired_total`, `retired_total`, `errors_total`),
  `eligible_pending_sweep.py:404` (`ticks`, `eligible_total`, `notified_total`, `errors_total`,
  `skipped_promote_hold_total`), `orphan_watcher_sweep.py:260` (`ticks`, `cancelled_total`,
  `errors_total`); the RDRS per-lane row counters supply the lane outcomes.
  **Doc drift to correct:** `daemon/services/job_queue_service.py:828-836` documents
  `orphan_released` as "a cumulative counter on the WatchReconcileSweepService", but the code
  returns it **per-tick** (`job_queue_service.py:837` `orphan_released = 0`, reset each call).
  The metric accumulates per-tick values into a cumulative counter — it never exposes the
  per-tick value directly. **The 8 s hot loop:** v1's `rate(ensemble_sweep_cycles_total[5m]) * 300 > 2`
  becomes `rate(ensemble_sweep_cycles_total{lane="rdrs_mission_reconciliation"}[5m]) * 300 > 2`;
  the configured interval per lane is published on the HELP text, so an operator compares actual
  vs configured without a second metric.
- **Phase** P1b · **Owner** developer · **Series estimate** 13 + 13 + 13 + ≈30 live
  (lane, outcome) combinations (most lanes emit 2–3 of the 6 outcomes) = **≈69**.
- **Degradation:** a lane disabled (the RDRS lanes are individually kill-switchable,
  `daemon/config.py:1951-2020`) ⇒ that lane renders **no** cycle/cycle-count series; its error
  counter renders `0` — a real zero ("the lane ran zero times because it is off") differs from
  "the lane errored".

### F13 — Instance status, lost wakes, lifecycle (m1, m8)

- **Name:** `ensemble_instance_status_count{status}` (gauge) ·
  `ensemble_instance_liveness_count{liveness}` (gauge) · `ensemble_waiting_children_count`
  (gauge) · `ensemble_waiting_children_max_age_seconds` (gauge) ·
  `ensemble_wake_turns_total{outcome}` (counter) · `ensemble_instance_lifecycle_total{event}`
  (counter). **Type:** gauge; gauge; gauge; gauge; counter; counter. **Labels:** `status` ∈ the
  **complete 10-value** `InstanceStatus` enum · `liveness` ∈ 7 · `outcome` ∈ 7 (wake-turn) ·
  `event` ∈ 7 (lifecycle) (§8.1, §8.10). m8: lifecycle **beyond** point-in-time, including
  **revive-on-`send_message`** (the lost-wakes class).
- **Source:** **reuse** for status/liveness (the live census is already computed by
  `daemon/tools/instance_read.py:2019` `_build_capacity_summary`; `counts_by_status` and
  `counts_by_liveness` are the same two buckets) and for the wake event counters
  (`daemon/services/worker_pool.py:1381-1386` already tracks `empty_claim_attempts` and
  `claims_skipped_due_to_busy_instance`). **GREENFIELD for `ensemble_wake_turns_total` (m1)** —
  the Carrier-Threading contract (producer / carrier / threading / consumer) is specified in
  full in **§8.13**.
- **Phase** P1b · **Owner** developer (producer + carrier), reviewer (Carrier-Threading
  compliance), devops (LostWake alert) · **Series estimate** 10 + 7 + 1 + 1 + 7 + 7 = **33**.
- **Degradation:** instance census unavailable ⇒ the two count gauges render **no series**; the
  wake/lifecycle counters are in-RAM and reset on restart (§10).

---

## 5. P1c — LLM, MCP, compaction, tool-call, journal twin

### F14 — LLM token accounting (MA-8)

- **Name:** `ensemble_llm_tokens_total{direction}` (counter) ·
  `ensemble_llm_token_usage_missing_total` (counter). **Type:** counter; counter.
  **Labels:** `direction` ∈ `input`, `output` (§8.10).
- **Source:** **reuse** — the streaming chunk converter. **Observation site corrected (A2):**
  hook the **terminal-usage branch `daemon/graph.py:3776-3784`** (the `len(choices) == 0` path
  that carries the actual token counts and returns early), or the aggregated call site
  `:9176-9179` — NOT `:3811-3812`, which is the **non-terminal** path reached only when the
  chunk also carries choices and is a **per-chunk** site. Mandate a **per-call dedupe** (some
  OpenAI-compatible backends put `usage` on every chunk ⇒ double counting). Rule: **terminal-chunk
  only, never per-chunk.** `usage_metadata` is built from the terminal usage chunk at
  `:3771-3774`. **Documented caveat (MA-8):** `usage_metadata` is a **stale/undercounting proxy**
  (`daemon/graph.py:7632` — "never the primary") and is **lost entirely** when the backend
  omits the final usage chunk (`daemon/graph.py:3873-3881` — the `stream_usage` injection is
  what requests it; without it `usage_metadata` is `None`).
  `ensemble_llm_token_usage_missing_total` counts observations where `usage_metadata` was
  absent — the **undercount caveat made visible** rather than hidden.
- **Phase** P1c · **Owner** developer · **Series estimate** 2 + 1 = **3**.
- **Degradation:** non-streaming call sites (`streaming=False` preserved verbatim,
  `daemon/graph.py:3889-3891`) ⇒ `usage_metadata` may be absent ⇒ the tokens counter does not
  increment and the missing counter does. **No series are removed.**

### F15 — LLM request outcome and latency (MA-8, the real 4-layer ladder)

- **Name:** `ensemble_llm_request_total{outcome}` (counter) · `ensemble_llm_request_seconds`
  (histogram, buckets §8.11). **Type:** counter; histogram — the latency histogram carries **no**
  outcome label (14 series); the outcome breakdown lives on the counter. Splitting the histogram
  by outcome would cost 8 × 14 = 112 series for no analytical gain. **Labels:** `outcome` ∈ the
  **8-value enum reflecting the real 4-layer retry ladder** (`ok`, `transient_retry`,
  `timeout_retry`, `failover_to_backup`, `reset_to_primary`, `terminal_transient`,
  `terminal_permanent`, `context_overflow`) — the layer each value maps to is specified in
  **§8.12**.
- **Source:** **reuse** — the failover bookkeeping currently lives in the **in-closure dict**
  (`daemon/llm_error_classifier.py:818`) and is visible only as `[LLM-HA]` log lines. The
  registry observes at the same points; the closure dict is **not** a second source of truth.
  The predicate is built by `make_llm_retry_strategy` (`:764`).
- **Phase** P1c · **Owner** developer · **Series estimate** 8 + 14 = **22** (the summary table
  rounds to 23 with `3600.0` reserved for slow models).
- **Degradation:** always present (the LLM lane is always configured); an outcome with zero
  observations renders no series — the enum is the **contract**, not a promise that every value
  occurs.

### F16 — LLM stream stall

- **Name:** `ensemble_llm_stream_stalls_total{phase}` (counter) ·
  `ensemble_llm_stream_watchdog_sweep_total{outcome}` (counter). **Type:** counter; counter.
  **Labels:** `phase` ∈ `abort_attempt`, `aborted`, `recovered` (the three stages of the
  force-abort) · `outcome` ∈ `none`, `claimed` (the return shape of `sweep_once`) (§8.10).
- **Source:** **reuse** — `daemon/services/llm_stream_watchdog.py:456` `_force_unblock` is the
  abort site; the `[LLM-WATCHDOG-STALL]` marker is emitted at `:497-498`; the sweep is
  `sweep_once` (`:551`). The watchdog thread starts at `daemon/api.py:2212-2226` with
  `config.llm.stream_stall_threshold_seconds` (default 45 s,
  `daemon/services/llm_stream_watchdog.py:4-5`).
- **Phase** P1c · **Owner** developer · **Series estimate** 3 + 2 = **5**.
- **Degradation:** watchdog thread start failure ⇒ `daemon/api.py:2229` logs
  `[StreamWatchdog] DISABLED` and the daemon continues ⇒ **the family renders no series**
  (operator sees the absence plus the log line).

### F17 — MCP server health and failures

- **Name:** `ensemble_mcp_server_state{server,state}` (gauge) ·
  `ensemble_mcp_failures_total{server,kind}` (counter). **Type:** gauge; counter.
  **Labels:** `state` ∈ `ok`, `degraded`, `quarantined`, `unknown` · `server` ∈ the **bounded
  5-value roster** (3 built-ins + `custom` + `other`) with the churn policy · `kind` ∈ `connect`,
  `initialize`, `tool_call`, `quarantine` (§8.5).
- **Source:** **reuse** — the per-boot quarantine registry (`daemon/mcp/init_guard.py`) is
  already published read-only at `/readyz` → `detail.mcp` (`daemon/api.py:3840-3878`) and
  consumed by `_mcp_server_health_state` (`daemon/routers/mcp_servers.py:302`). The metric reads
  that snapshot; it **never re-probes** an MCP server.
- **Phase** P1c · **Owner** developer; devops (quarantine alert) · **Series estimate**
  5 × 4 + 5 × 4 = **40**.
- **Degradation:** the quarantine registry is unreachable ⇒ `/readyz` **omits** the `mcp` key
  (`daemon/api.py:3867-3873`, the older-consumer tolerance contract) ⇒ the family renders
  **no series**.

### F18 — MCPS gateway sessions and audit

- **Name:** `ensemble_mcps_gateway_sessions` (gauge) ·
  `ensemble_mcps_gateway_session_total{outcome}` (counter) ·
  `ensemble_mcps_gateway_tombstones` (gauge) · `ensemble_mcps_gateway_audit_total{outcome}`
  (counter). **Type:** gauge; counter; gauge; counter. **Labels:** session `outcome` ∈ `begin`,
  `end`, `expired`, `superseded`, `refused`; audit `outcome` ∈ `ok`, `error` (§8.10).
- **Source:** **reuse** — `daemon/services/mcps_gateway/store.py:79` `MachineStore` / `:103`
  `roster()`; `daemon/services/mcps_gateway/sessions.py` (session lifecycle);
  `daemon/services/mcps_gateway/tombstones.py` (bounded, owner-scoped tombstone set);
  `daemon/services/mcps_gateway/audit.py` (the only durable surface — the `mcp_gateway_audit`
  table, `daemon/migrations/versions/20261022_000001_create_mcp_gateway_audit.sql:78`).
- **Phase** P1c · **Owner** developer · **Series estimate** 1 + 5 + 1 + 2 = **9**.
- **Degradation:** the gateway is disabled (`config.gateway_enabled`, default **OFF**) ⇒ **no
  series**. The audit table survives restarts; the in-RAM roster and tombstone set do not (§10).

### F19 — Compaction rungs and context occupancy (m6)

- **Name:** `ensemble_compaction_fired_total{rung}` (counter) ·
  `ensemble_compaction_occupancy_ratio{stage}` (gauge) ·
  `ensemble_compaction_precall_estimate_tokens` (gauge). **Type:** counter; gauge; gauge.
  **Labels:** `rung` ∈ `l1_pre_dispatch_80`, `l2_precall_95`, `l3_backstop_600k` (the three-rung
  ladder) · `stage` ∈ `pre_dispatch`, `precall_95`, `post_compaction` (§8.10).
- **Source:** **reuse** — `daemon/compaction.py:1128` `DEFAULT_CONTEXT_LIMIT = 700000` is the
  window the rungs are computed against; the per-instance estimate cache is
  `self._precall_estimates` (`daemon/compaction.py:2299`), a `dict[str, tuple[int, int]]` of
  `(msg_count, total_tokens)`. The rung fires are currently **log-only**
  (`[Compaction][precall-95]`); the counter observes at the same sites. The pre-call gate lives
  in `daemon/graph.py:7616-7670` — the 95 % gate at `:7656-7670` (`PRECALL_COMPACTION_RATIO =
  0.95` at `daemon/graph.py:7497`); note the escalation lowers the ratio to **0.80** when
  proactive-trigger escalation metadata is active, so the metric must record the rung
  **actually applied**.
- **Phase** P1c · **Owner** developer · **Series estimate** 3 + 3 + 1 = **7**.
- **Degradation:** compaction disabled ⇒ **no series** (the rungs never fire).
  `ensemble_compaction_precall_estimate_tokens` renders the **max across instances** — a
  per-instance label would explode cardinality (6 324 instances in prod,
  `daemon/repositories/instance/repository.py:187`).

### F20 — Turn and tool-call seam (m7)

- **Name:** `ensemble_turn_events_total{source}` (counter) · `ensemble_turn_duration_seconds`
  (histogram, buckets §8.11) · `ensemble_tool_calls_total{category}` (counter).
  **Type:** counter; histogram; counter. **Labels:** `source` ∈ the 5 `SOURCE_*` tap constants ·
  `category` ∈ the **49 runtime-derived tool categories + reserved `other`** (§8.6).
- **Source:** **reuse** — `MessageTapSlot.tap_node_return`
  (`daemon/services/message_tap.py:234`) is the **per-turn seam** (R17). m7: the tool-call and
  turn-performance families instrument **this seam**, NOT graph internals — graph internals would
  multiply measurement sites and re-derive per-turn state the tap already owns. The tool
  category is read from the tool's own registration metadata at the tap site, with an `other`
  fallback for any tool whose category is not in the runtime-derived set.

**Counting unit + anchor (A5):** `ensemble_turn_events_total{source}` counts **one
`tap_node_return` invocation** (the tap fires at 5 sites and N+1 times per turn with N tool
rounds) — state this on the HELP text so "turn" is not misread as one-per-turn.
**Anchor pair specified (A5):** `ensemble_turn_duration_seconds` observes the elapsed time
between the **entry tap** (`user_message_entry`, `message_tap.py:120-124`) and the
**node-return tap** — **not** the tap's own latency, which includes its
`asyncio.to_thread(upsert_batch)` DB write (`message_tap.py:302-306`) and would be
DB-write latency mislabelled as turn duration. The entry-tap timestamp is carried on the same
carrier as the wake outcome (§8.13) and is never re-derived at the consumer. A source with no
entry-tap counterpart contributes **no observation** (absent ≠ zero); the family is **dropped**
for that source rather than shipped mislabelled. The metric increments sit **inside** the tap's
existing `try/except` (`message_tap.py:308-309`) so a metric error cannot produce per-turn
WARNING noise.
- **Phase** P1c · **Owner** developer · **Series estimate** 5 + 14 + 50 = **69** (5 turn sources
  + 14 duration buckets + 50 tool categories: 49 runtime-derived via `get_tool_categories()`
  (`daemon/tools/_tool_registry.py:1598`) + reserved `other`).
- **Degradation:** the tap is **non-load-bearing** — it swallows every exception and logs at
  WARNING (`daemon/services/message_tap.py:308-309`) — so a tap failure yields **no series**,
  never a broken turn.

### F21 — Journal twin and upgrade pipeline

- **Name:** `ensemble_journal_file_bytes` (gauge) · `ensemble_journal_history_entries` (gauge) ·
  `ensemble_journal_op_seconds` (histogram, buckets §8.11) ·
  `ensemble_journal_ops_total{op}` (counter) · `ensemble_upgrade_phase_seconds{phase}` (gauge) ·
  `ensemble_upgrade_phase_total{phase}` (counter) ·
  `ensemble_upgrade_release_current{version}` (gauge) ·
  `ensemble_upgrade_journal_events_total{kind}` (counter). **Type:** gauge; gauge; histogram;
  counter; gauge; counter; gauge; counter. **Labels:** `op` ∈ **26 journal history event
  types** (§8.7) · `phase` ∈ **10 promote phases** (§8.8) · `kind` ∈ the 3 terminal-class alert
  kinds (§8.8) · `version` ∈ release directory names (`v0.27.12` …), bounded by the retention-3
  release set.
- **Source:** **reuse** — the **Python twin** `daemon/tools/upgrade_journal.py` is the
  instrumented surface: `journal_read` (`:259`), `journal_write` (`:331`), `journal_update_field`
  (`:374`), `journal_history_append` (`:391`), `ensure_extensions` (`:409`).
  **Leader-ratified: NEVER edit `scripts/upgrade/`** — the shell scripts are *observed* (their
  journal events are read through the twin), not modified. `ensemble_journal_file_bytes` =
  `journal_path(install_dir).stat().st_size` (`:255`); `ensemble_journal_history_entries` =
  `len(data["history"])` (the top-level `history` key, `:165`);
  `ensemble_upgrade_release_current{version}` = `data["current"]` (`:159`).
- **Phase** P1c · **Owner** developer (twin), devops (journal-growth alert) · **Series estimate**
  1 + 1 + 13 + 26 + 10 + 10 + 1 + 3 = **65** (the summary table rounds to 66).
- **Degradation:** twin unreachable (no install dir, journal torn) ⇒ **no series**; a torn
  journal raises `JournalTorn` (`:251`) and the twin degrades to the last good copy.

---

## 6. P2 — Exporters and shell-origin series

### F22 — Host, PostgreSQL and shell-process metrics

- **Name:** `ensemble_process_cpu_seconds{name}` (gauge) — shell-origin. Exporter-origin (not
  daemon-origin): node_exporter per-core CPU, per-device disk `await`/`util`, memory, load;
  postgres_exporter `pg_stat_activity` rollup, `pg_stat_statements` top-N, table sizes, WAL,
  autovacuum, `pg_stat_database`. **Type:** gauge; the rest are exporter-native.
  **Labels:** `name` ∈ `launcher`, `promote_sh`, `stage_sh`, `rollback_sh`, `restart_sh`.
- **Source:** **greenfield** — the node_exporter **textfile collector** pattern: the house shell
  scripts rewrite `*.prom` files in a well-known directory under the install dir
  (`$INSTALL_DIR/data/metrics/`). No daemon dependency, crash-safe, and the only way to see the
  **bash** spins (the O(n²) `state.json` walk) that in-daemon metrics cannot. The canonical
  journal shape is `ensemble_journal_file_bytes` / `ensemble_journal_history_entries` (F21) —
  the textfile carries the same numbers so a daemon-down incident still has them.
- **Phase** P2 — the **minimal** textfile writer ships in **P1b** (A25: the BashSpin data
  source must exist before the P2 alert rules land); the full exporter/textfile work stays in
  P2 · **Owner** devops (exporters, textfile dir, dashboards), developer (the textfile
  writer **inside the twin** — never inside `scripts/upgrade/`) · **Series estimate** **5
  shell-origin** (counted separately from the daemon budget) + ≈ **67 exporter-origin** (§1
  excludes both from the ≤2k daemon-origin budget).
- **Degradation:** node_exporter absent ⇒ its series are absent (daemon families unaffected).
  Textfile staleness is detectable by file mtime.

---

## 7. Series roll-up (the ≤2k proof)

| Phase | Families | Daemon-origin series |
|---|---|---|
| P0 | F0.1–F0.4 | 26 |
| P1a | F1–F6 | 62 |
| P1b | F7–F13 | 223 |
| P1c | F14–F21 | 222 |
| **Daemon total** | **F0.1–F21** | **533** |
| Shell-origin (textfile) | F22 | 5 |
| Exporter-origin (node/postgres) | F22 | ≈67 |
| **Grand total on the Prometheus server** | | **≈605** |

**533 daemon-origin series against a ≤2 000 budget — ≈27 % consumed; headroom 1 467.** The four
largest families (F12 sweeps 69, F21 journal 66, F20 tool calls 69, F9 readiness 53) are each
**bounded, enumerated** label sets, so growth there is bounded by construction. The three
unbounded risks in v1 are explicitly closed: per-instance compaction occupancy is a max; the
leak site is a 6-value fixed map; the MCP roster is capped at 5 values. If every label
combination in the catalog were live at once the total would be ≈1 113 — still inside budget.

**Intra-family phased rollouts — convention (G-20/H-12 unification).** A family maps to
**exactly one phase** in the table above (1:1, zero exceptions). When a family needs an early,
partial ship (e.g. F22's minimal textfile writer in P1b ahead of the full exporter work in P2
per A25), the carve-out is expressed as a **task-level early-ship note inside the owning
phase's task list** — never as the family occupying two phases. The family's phase is the phase
in which it **completes** (F22 = P2). Per-phase counts below are computed on that basis.

**Phase split (F22).** The **minimal** textfile writer (`ensemble_process_cpu_seconds`) ships in
**P1b** (A25) so BashSpin has a data source before the P2 alert rules land; the **family**
completes in P2 (full exporter/textfile work). This is a deliberate phase split inside one
family — it does not change the series count or the ≤2 k proof.

---

## 8. Vocabulary annex

Every enumerated label set, with the `file:line` it was verified against.

**Rule (A1): every closed vocabulary in this annex is DERIVED FROM CODE, never hand-listed.** Hand-listing is the drift class that already falsified the tool-category set (§8.6: 37/38 claimed vs 49 verified) and the sweep lanes (§8.4: 7 → 13). Each set must be pinned to a runtime source (`get_tool_categories()`, an enum, a constant) and cross-checked by a test; a vocabulary that cannot be derived from code must carry a reserved `other` residual and a documented amendment procedure (bump `ensemble_metrics_vocabulary_version`).

**Cardinality rule (A10/A18):** per-instance attribution = PG-only (`/api/resources/*`); no
daemon-origin `/metrics` family carries a per-instance label — every daemon-origin family is
aggregate. (Companion: plan §3 storage-split boundary rule.)

**8.1 Instance + lifecycle vocabularies** — **Instance status (10)** —
`daemon/repositories/instance/models.py:20-32` (`InstanceStatus`): `idle`, `running`, `waiting`,
`paused`, `completed`, `error`, `terminated`, `queued`, `waiting_children`, `failed`.
**Instance liveness (7)** — `daemon/services/work_status.py:67-99` (`_STATUS_CANONICAL_MAP`):
`pending`, `processing`, `paused`, `completed`, `failed`, `cancelled`, `unknown`; live consumer
`daemon/tools/instance_read.py:228` `_liveness_for`. **Wake-turn outcome (7)**: `claimed`,
`skipped_busy_instance`, `empty_claim`, `timeout_wake`, `no_op_resume`, `fresh_episode_reset`,
`stale_episode`. **Instance lifecycle event (7)**: `spawned`, `revived`, `paused`, `resumed`,
`terminated`, `fan_in_completed`, `fan_in_failed`.

**8.2 Thread group (11)** — `main`, `worker_pool`, `chat_worker_pool`,
`resource_monitor_sampler`, `resource_monitor_flusher`, `resource_monitor_shutdown`,
`wedge_capture_watchdog`, `wedge_capture_shutdown`, `sweep_services`, `llm_stream_watchdog`,
`other`. Sources: `daemon/services/resource_monitor/sampler.py:296`,
`daemon/services/resource_monitor/flusher.py:257`, `daemon/api.py:3425`, `daemon/api.py:2217`,
`daemon/services/wedge_capture/watchdog.py` (`WedgeCaptureWatchdog`, `wedge-capture-shutdown`),
`daemon/services/worker_pool.py:166,207,253` (`Worker`, `lane`),
`daemon/services/stale_task_recovery.py` / `stale_pause_sweep.py` / `report_delivery_recovery.py`
(`StaleTaskRecovery`, `StalePauseSweepService`, `ReportDeliveryRecoveryService`).

**8.3 Readiness + wedge vocabularies** — **Readiness outcome (11)** —
`daemon/services/readiness.py:64-79`: `probe_timeout`, `pool_busy`, `queue_stale`,
`read_timeout`, `boot_incomplete`, `identity_mismatch`, `hard_component_red`, `not_serving`,
`pool_leak`, `unknown`, `ok`. Grace-eligible subset (4): `probe_timeout`, `pool_busy`,
`queue_stale`, `read_timeout` (`:83-89`). Readiness component (4): `database`,
`queue_freshness`, `services`, `checkpoint_saver` (`:94-99`). **Wedge-capture reason (3)** —
`daemon/services/wedge_capture/triggers.py:54-56`: `sustained_cpu`, `sustained_loop_lag`,
`thread_concentration`. **Wedge write outcome (2)**: `ok`, `failed`. **Exposition outcome (2)**:
`ok`, `error`.

**8.4 Sweep lane (13) + outcome (6)** — RDRS lanes (7): `rdrs_deferred`,
`rdrs_no_row_backstop`, `rdrs_pending_age`, `rdrs_recovery_retry`, `rdrs_orphan`,
`rdrs_stuck_wake`, `rdrs_mission_reconciliation`
(`daemon/services/report_delivery_recovery.py:219-266`; kill-switches
`daemon/config.py:1951-2020`). Standalone sweeps (6) + configured interval: `watch_reconcile`
300 s (`daemon/services/watch_reconcile_sweep.py:88`), `eligible_pending` 90 s
(`daemon/services/eligible_pending_sweep.py:67`), `stale_pause` 300 s
(`daemon/services/stale_pause_sweep.py:129`), `orphan_watcher` 90 s
(`daemon/services/orphan_watcher_sweep.py:75`), `job_lock` 90 s
(`daemon/services/job_lock_sweep.py:90`), `upgrade_journal` 90 s
(`daemon/services/upgrade_journal_sweep.py:96`). Sweep row outcome (6): `processed`, `skipped`,
`held`, `released`, `retired`, `error`.

**8.5 MCP roster (5) + churn policy** — `context7`, `plane`, `webfetch` (built-ins,
`daemon/mcp/builtin_servers/`), `custom` (all user-created `mcp_servers` rows,
`daemon/repositories/mcp_server/models.py:17`), `other` (overflow bucket). **Policy:** the three
built-ins are sticky named series; user-created servers are **never** named individually — they
collapse into `custom`; `other` is reserved so the roster can never exceed 5 live values and a
future roster change is additive rather than breaking. MCP state (4): `ok`, `degraded`,
`quarantined`, `unknown` (`daemon/mcp/init_guard.py:145-147`; unknown at
`daemon/routers/mcp_servers.py:302-307`). MCP failure kind (4): `connect`, `initialize`,
`tool_call`, `quarantine`.

**8.6 Tool category (derived from code, not hand-listed — A1).** The vocabulary is derived at
runtime from `get_tool_categories()` (`daemon/tools/_tool_registry.py:1598`); verified in the
worktree: **49 distinct categories** across **254 `register_tool_category(` sites** (the
architect's figure was 49 — matches). The hand-listed v1 set (37/38 values) is **falsified**:
it omits at least `board-link`, `dynamic-skill`, `ens-db`, `ens-env`, `image-gen`,
`self-alarm`, `skill-evolution`, `system-log` (all verified present in-tree), plus
`view-views`, `view-write`, `wedge-capture`. The metric label set is the **runtime-derived set
plus a reserved `other` fallback** (50 values) for the tool→category lookup; a tool whose
category is not in the set folds to `other` and is counted in
`ensemble_metrics_invalid_label_total{metric,label}`. A cross-check test pins the annex to
`get_tool_categories()`. The full closed list is intentionally NOT hand-written here — deriving
it at runtime is the contract; listing it would re-introduce the drift class the plan already
caught once for the sweep lanes (§8.4, 7 → 13).

**8.7 Journal op (26)** — shell-written (16): `commit`, `halt`, `intent_flip`, `live`,
`quarantine`, `refusal`, `restart`, `restart_gate`, `rollback`, `soak_deferred`,
`soak_grace_extended`, `subprocess_wait_timeout`, `supervision_handback`, `sweep`,
`sweep_rollback`, `warn` (`scripts/upgrade/lib.sh` `journal_history_append` call sites).
Twin-written (10): `arm_notify_no_instance`, `executor_error`, `executor_exit`,
`executor_orphaned`, `executor_still_running`, `nonce_consumed`, `restart_abandoned`,
`supervision_boot`, `wake_abandoned`, `wake_coalesce_overflow`
(`daemon/tools/upgrade_journal.py`, `daemon/services/upgrade_journal_sweep.py`,
`daemon/manager.py:5402`, `daemon/mcp/init_guard.py:561`).

**8.8 Upgrade phase (10) + alert kind (3)** — phases `preflight`, `stop`, `launcher_swap`,
`flip`, `restart`, `livez`, `readyz`, `version`, `soak`, `commit`
(`scripts/upgrade/promote.sh:218,373,387,409,437,445,454,507,516,528`); kinds
`upgrade_cap_halt`, `upgrade_promote_refusal`, `upgrade_auto_rollback`
(`daemon/tools/upgrade_journal.py:645-648`).

**8.9 Pool / PG / checkpoint / spine vocabularies** — pool name (2): `manager`, `checkpoint`;
pool native quantity (4): `size`, `max`, `available`, `overflow`; pool request-error kind (3):
`waiting_exception`, `too_many_requests`, `unclassified`; PG backend state (7): `active`, `idle`,
`idle_in_transaction`, `idle_in_transaction_aborted`, `fastpath_function_call`, `disabled`,
`unknown` (PostgreSQL's `pg_stat_activity.state` domain); PG leak site (6): `instance_read`,
`instance_parent_lookup`, `job_queue_read`, `checkpoint_saver`, `agent_db_tool`, `other`
(§9.2); checkpoint op (4): `aget`, `aput`, `adelete`, `alist`
(`daemon/checkpoint_metrics.py:281-290`); checkpoint table (3): `checkpoints`,
`checkpoint_writes`, `checkpoint_blobs` (`daemon/services/maintenance.py:531-536`); checkpoint
prune outcome (4): `dry_run`, `executed`, `skipped_non_postgres`, `error`; spine table (4):
`resource_daemon_bucket`, `resource_instance_sample`, `resource_pid_map`,
`resource_limit_policy`.

**8.10 Job / LLM / compaction / turn / gateway vocabularies** — admission state (4): `queued`,
`active`, `done`, `dead` (`daemon/repositories/job_queue/models.py:40-43`); worker pool name (2):
`worker`, `chat`; worker pool event (4): `notifications_sent`, `empty_claim_attempts`,
`workers_woken_by_timeout`, `claims_skipped_due_to_busy_instance`
(`daemon/services/worker_pool.py:1381-1386`); LLM outcome (8): §8.12; LLM token direction (2):
`input`, `output` (`daemon/graph.py:3771-3812`); stream-stall phase (3): `abort_attempt`,
`aborted`, `recovered` (`daemon/services/llm_stream_watchdog.py:456,497`); stream-watchdog sweep
outcome (2): `none`, `claimed`; compaction rung (3): `l1_pre_dispatch_80`, `l2_precall_95`,
`l3_backstop_600k` (`daemon/compaction.py:1128`); compaction stage (3): `pre_dispatch`,
`precall_95`, `post_compaction`; turn source (5): `user_message_entry`, `agent_node_return`,
`compaction_aupdate_reactive`, `compaction_aupdate_messaging`, `compaction_precall_95`
(`daemon/services/message_tap.py:120-124`); gateway session outcome (5): `begin`, `end`,
`expired`, `superseded`, `refused`; gateway audit outcome (2): `ok`, `error`.

**8.11 Histogram bucket sets (the seven histograms).** Each is stated once; `_count`/`_sum` are
implied.

| Histogram | Buckets (seconds or bytes) | Series |
|---|---|---|
| `ensemble_checkpoint_op_seconds` | `0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0` (**top ≥120 s** — CR-3; the existing `daemon/checkpoint_metrics.py:59-63` set stops at 10.0 and is **extended, not replaced**) | 17 + 2 = 19 / op |
| `ensemble_checkpoint_op_bytes` | `1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216` | 8 + 2 = 10 / op |
| `ensemble_job_claim_latency_seconds` | `0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0` | 12 + 2 = 14 |
| `ensemble_llm_request_seconds` | `0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0` (plus `1800.0`, `3600.0` reserved for slow models) | 12 + 2 = 14 |
| `ensemble_turn_duration_seconds` | `0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0` | 12 + 2 = 14 |
| `ensemble_journal_op_seconds` | `0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0` | 11 + 2 = 13 |
| `ensemble_metrics_exposition_seconds` | `0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0` | 11 + 2 = 13 |

`DEFAULT_LATENCY_BUCKETS` is **not** reused for the LLM or claim-latency histograms — those
ranges are seconds-to-minutes, not milliseconds.

**8.12 The LLM 4-layer retry ladder → the 8-value outcome enum.** The enum in §8.10 is derived
from the real ladder in `daemon/llm_error_classifier.py`, not invented:

| Layer | Mechanism | Source | Outcome value(s) |
|---|---|---|---|
| L1 | tenacity per-category retry — transient and timeout budgets tracked **independently**; timeout checked first because `openai.APITimeoutError` inherits from `APIConnectionError` | `make_llm_retry_strategy` `:764`; closure `counts = {"transient": 0, "timeout": 0, "swapped": False}` `:818`; ordering note `:775-779` | `transient_retry`, `timeout_retry` |
| L2 | failover to the backup endpoint — `base_url` swap when the primary slice is exhausted; caps `PRIMARY_TRANSIENT_MAX = 3`, `PRIMARY_TIMEOUT_MAX = 2` | `FailoverController` `:596`; caps `:574-575` | `failover_to_backup` |
| L3 | reset to primary — the predicate's `attempt_number == 1` branch calls `reset_to_primary()`; sticky-on-success | `:688-692` | `reset_to_primary` |
| L4 | total attempt ceiling — `budget + max(PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX)` when HA is active; graph.py's `stop_after_attempt` derives from the same constants | `derive_ha_attempt_ceiling` `:578-592` | (bounds L1–L3) |
| — | success | — | `ok` |
| — | budget exhausted (transient class) | — | `terminal_transient` |
| — | non-retryable status (auth, invalid params — blocklist `:110-113`) | — | `terminal_permanent` |
| — | context-length overflow (handled by `daemon/compaction.py`) | — | `context_overflow` |

**8.13 The Carrier-Threading contract for `ensemble_wake_turns_total` (m1, A3/A4).** The wake
outcome is a per-message **flag**, so it follows the blueprint's Carrier-Threading Pattern, but
the producer is **split** (A3) because one producer can yield at most 2 of the 7 outcomes:

- **Producer (a)** — the **claim path** in `daemon/services/worker_pool.py:1381-1386` yields
  `empty_claim`, `skipped_busy_instance`, `timeout_wake`, `claimed`, `notifications_sent`,
  `workers_woken_by_timeout` (claim-path outcomes).
- **Producer (b)** — the **episode seam** `daemon/graph.py:7319` `_detect_fresh_episode_reset`
  yields `fresh_episode_reset` / `stale_episode` / `no_op_resume`.
- Each producer is computed **once**, each carried. Neither re-derives from partial signals.
- **Carrier** — a new keyword-only field on `ProcessingContext`
  (`daemon/services/message_processing_pipeline.py:121`; the existing carrier field
  `is_fresh_episode_user_message` sits at `:176`), defaulting to `None`.
- **Threading** — keyword-only, `= None` default, threaded pipeline → facade → service, exactly
  as the blueprint's Carrier-Threading Pattern requires.
- **Consumer** — **a live turn-end consumer increments the counter; the renderer only reads the
  accumulated counter** (A4). The per-message carrier dies with the message, so the exposition
  renderer (scrape-time) cannot read it — only the accumulated registry value. **Consumer-seam
  re-derivation is forbidden**: a consumer that re-derives the outcome from partial signals
  drifts from the producer gate (the defect class the fresh-episode flag already suffered).
- **Not to be conflated** — the **semantic** consumer of the related fresh-episode flag is
  `daemon/graph.py:7319` `_detect_fresh_episode_reset`.

---

## 9. Census discipline annex (MA-13)

**9.1 The connection.** The census runs on a **dedicated, non-pooled connection** — never
through the P1 manager pool or the P2 checkpoint pool. A probe that consumes pool slots is the
exact failure mode it exists to detect (the readiness-probe amplifier lesson).

| Property | Value | Verified against |
|---|---|---|
| Transport | a direct `psycopg.connect` (no pool) | the wedge-capture's one-shot connect, `daemon/services/wedge_capture/db_capture.py:185` `_connect` |
| `application_name` | `ensemble-metrics-census` | the capture's discipline, `db_capture.py:22-25`, `:62` `CAPTURE_APPLICATION_NAME = "wedge-capture"` |
| `statement_timeout` | **5 s** (every census query bounded; < ⅓ of the 15 s cadence) | `db_capture.py:71-73` |
| `connect_timeout` | **3 s**; the DSN is driver-stripped (`db_capture.py:132` `_strip_driver`) | `db_capture.py:29`, `:132` |
| Execution home | **dedicated daemon thread** `CensusSampler`, `daemon=True`, `time.monotonic` tick loop — NOT the listener thread (request-driven ⇒ no series between scrapes) and NOT the event loop (dies in a wedge, defeating CR-1 for the census) | greenfield (A17) |
| Self-exclusion | excludes **only** `application_name='ensemble-metrics-census'`; `wedge-capture` rows are counted under `other` (excluding them would transiently break the sum-ties invariant) | A17 |
| Cadence | **≥15 s** (never faster than the resource-monitor's 15 s floor) | `daemon/config.py:864` `sample_seconds` `ge=15` |
| Self-exclusion | the census **excludes its own connection** from every count (matched on `application_name`) | same reasoning as `db_capture.py:22-25` |
| Failure posture | a census failure renders **no series** and leaves `ensemble_pg_census_last_success_timestamp_seconds` stale — it never raises into the daemon | `db_capture.py`'s "never-raises, degrade one artifact" discipline |

**9.2 Fragment → site map (the fixed site set).** The census attributes an `idle in
transaction` backend to a **site** by matching the backend's `query` fragment against a
**fixed, closed** set of patterns, pinned in the census module (not a config file) so the label
set cannot drift.

| `site` | Matching fragment (pinned pattern) | The real seam it names |
|---|---|---|
| `instance_read` | `SELECT … FROM instance` / `instancehierarchy` | `daemon/repositories/instance/repository.py:414`, `:433`, `:582` (`SQLModelSession` scopes) |
| `instance_parent_lookup` | `SELECT … FROM instance WHERE id =` (single-row get) | `daemon/repositories/instance/repository.py:717-723` `get_parent` |
| `job_queue_read` | `SELECT … FROM job_queue_items` | `daemon/repositories/job_queue/repository.py:337`, `:437`, `:539`, `:555` |
| `checkpoint_saver` | `SELECT … FROM checkpoints` / `checkpoint_blobs` | the psycopg saver pool (`daemon/persistence.py:175-176`); the `aget` path at `daemon/persistence.py:509` |
| `agent_db_tool` | `SELECT` issued by the agent-facing DB tool | `daemon/tools/ens_db_tools.py:1067` (`with Session(repair_engine)`), `:1038` (vacuum engine) |
| `other` | no pattern matched | the residual bucket — **never dropped**, so the census total always equals the sum |

`other` is what makes the metric honest: `sum(ensemble_pg_leaked_txn) ==
ensemble_pg_backends{state="idle_in_transaction"}` must hold at all times. A new leak site lands
in `other` and the residual grows — that is the signal to extend the map (a deliberate, reviewed
change to the closed set).

**9.3 What the census does NOT do.** It never issues a query against the daemon's *own* pools,
never calls `pg_terminate_backend`, never mutates `pg_stat_activity`. Read-only.

**9.5 Matching mechanics (A17).** The fragment→site map (§9.2) is evaluated **inside the
census SQL query** (one round trip, server-side `CASE`/`LIKE`), not by fetching `query` text and
matching in Python. Drift guard: one unit test per site (6) + the sustained `other`-growth
signal. **Honest residual risk:** under psycopg3 extended/pipeline protocol,
`pg_stat_activity.query` is the last statement text and may be blank for some prepared-statement
paths — those backends land in `other`. The sum-ties invariant still holds, but the site map
under-attributes. State this rather than claiming exact attribution.

**9.6 Sustained `other`-growth signal (A17).** Single-tick spikes in `other` are
incident-correlated captures (e.g. a wedge-capture row), not map drift. The drift signal is a
**sustained 5-minute** growth in `other`, so a one-off capture does not read as a new leak
site. Only a sustained rise warrants extending the closed site set (a deliberate, reviewed
change).

**9.4 Relation to the existing leak oracle.** `daemon/services/readiness.py:784`
`_evaluate_leak` arms the `pool_leak` readiness class from **pool counters** (the P2 psycopg
pool's `requests_waiting` and `pool_available` decay), not from `pg_stat_activity`. The census
and the leak oracle are **complementary, not duplicate**: the oracle says "the pool is
misbehaving"; the census says "these N backends have been idle-in-transaction for M seconds, at
these sites". The oracle feeds the readiness composite; the census feeds only `/metrics`.

---

## 10. Cross-restart counters and the alert algebra

**10.0 Restart-loss scenarios (A21).** Three scenarios × three idioms:

| Series class | Daemon restart | Prometheus **process** restart | Prometheus **storage** loss | Mitigation |
|---|---|---|---|---|
| Idiom A (in-RAM counters/gauges) | counters reset; raw history lost | **preserved** (WAL replay) | lost | `rate()`/`increase()` only; never raw, never `absent()` (§10.2) |
| Idiom B (PG-backed) | preserved | preserved | recoverable **by direct PG query** — *not* by Prometheus back-fill | dashboards query PG for 30 d+ |
| Idiom C (file/disk) | preserved | preserved | recoverable by reading the file | dashboards read the file |
| Prometheus TSDB | unaffected | preserved (WAL replay) | **all history lost** | remote_write to a durable store — out of v2 scope (RK-16) |

A Prometheus **process** restart is **not** a history loss (WAL replay); only **storage** loss is.
Idiom A alerts must use `rate()`/`increase()` (§10.2). Idiom B/C survive a Prometheus storage loss
via their durable backing store.

**10.1 What resets on restart.** Everything held in RAM resets — that is **every** counter in the
catalog except those read from a durable table.

| Family | Durable? | Note |
|---|---|---|
| F7 `ensemble_checkpoint_*` | **No** — in-RAM registry | resets on restart |
| F10 `ensemble_job_queue_items_created_total` | **Partly** — derived from durable `job_queue_items.created_at` (TEXT ISO) | survives restart |
| F11 `ensemble_worker_pool_*`, `ensemble_workers_busy`, `..._wakeup_efficiency` | **No** — `WorkerPool._stats` is in-RAM | resets on restart |
| F13 `ensemble_wake_turns_total` | **No** | resets on restart |
| F18 `ensemble_mcps_gateway_audit_total` | **Yes** — `mcp_gateway_audit` table | survives restart |
| F21 `ensemble_journal_*`, `ensemble_upgrade_*` | **Yes** — the journal file on disk | survives restart |
| F0.3 `ensemble_resource_spine_rows_written_total` | **Yes** — the resource-monitor tables | survives restart |
| Everything else | **No** | resets on restart |

**10.2 The alert rule.** Any alert over a reset-on-restart counter **must** use `rate()` or
`increase()` — never a raw comparison, never `absent()`. A raw comparison reads "0" right after
a restart and produces a false-negative for the whole fill window. Every v1 alert that touches
a reset-on-restart counter is restated:

- **PoolExhausted** — **rebased** (CR-4):
  `increase(ensemble_db_pool_requests_errors_total[5m]) > 0` (documented as a **superset**, not
  a timeout count). The precise follow-up
  `increase(ensemble_db_pool_requests_errors_total{kind="waiting_exception"}[5m]) > 0` becomes
  available when the checkout seam is instrumented.
- **CheckpointSlow** —
  `histogram_quantile(0.95, rate(ensemble_checkpoint_op_seconds_bucket{op="aget"}[10m])) > 5`
  **paired with** `increase(ensemble_checkpoint_op_seconds_count[10m]) > 0`, so a restart with no
  traffic does not read as "p95 = 0, all good".
- **LoopStarved** — **rewritten** (MA-4): `ensemble_loop_lag_seconds{stat="p95"} > 2`, paired
  with `ensemble_loop_lag_samples > 150` so the alert does not fire on a half-full window.
- **LostWake** — `ensemble_waiting_children_max_age_seconds > 1800` (gauge; read from durable
  rows, survives restarts).
- **SweepHotLoop** —
  `rate(ensemble_sweep_cycles_total{lane="rdrs_mission_reconciliation"}[5m]) * 300 > 2`.
- **JournalWalkRisk** — `ensemble_journal_file_bytes > 200000` (durable).
- **CheckpointBloat** — `ensemble_checkpoint_blobs_table_bytes > 8e9` (durable).
- **PoolLeak** — `ensemble_pg_idle_in_txn_oldest_seconds > 60` (re-established within one
  census cadence).
- **FalseDegraded** —
  `rate(ensemble_readiness_probe_total{outcome="timeout"}[5m]) > 0.1` **and**
  `ensemble_pg_backends{state="active"} < 3`.
- **BashSpin** — `ensemble_process_cpu_seconds{name="promote_sh"} > 0.9` (textfile; durable
  across daemon restarts because the writer is the shell script).

---

## 11. Corrections to the v1 concept and the inventory digest

| # | v1 / digest said | The worktree says | Verified at |
|---|---|---|---|
| C1 | checkpoint buckets `0.001–10.0` ⇒ 17 s / 73–99 s land in `+Inf` | **confirmed** — must extend to ≥120 s | `daemon/checkpoint_metrics.py:59-63` |
| C2 | `checkpoint_metrics.py` lives in `daemon/services/` | **top-level** `daemon/checkpoint_metrics.py` | as cited (355 lines) |
| C3 | `checkpoint_perf.py` lives in `daemon/services/` | **top-level** `daemon/checkpoint_perf.py` | as cited (344 lines) |
| C4 | the metric names are `message_api_*` | **confirmed** — `:274` and `:282`; both re-registered under canonical `ensemble_*` with a one-release alias | `daemon/checkpoint_metrics.py:273-290` |
| C5 | p95 computed at `lag_probe.py:185` | `:185` is `snapshot_histogram()`; p95 is computed at `:115` (p50 `:116`, max `:114`); window `:59` | `daemon/services/wedge_capture/lag_probe.py` |
| C6 | readiness taxonomy at `readiness.py:56-79` | the 11 constants run **`:64-79`**; `:83-89` grace subset; `:94-99` components | `daemon/services/readiness.py` |
| C7 | `llm_error_classifier.py:773+` | **top-level** `daemon/llm_error_classifier.py`; `:773` = `make_llm_retry_strategy` signature, `:818` = closure counters dict | as cited |
| C8 | terminal-chunk undercount caveat at `graph.py:3876-3910` | streaming-usage docstring `:3873-3881`; "stale/undercounting proxy" at **`:7632`** | `daemon/graph.py` |
| C9 | `maintenance.py:533` computes `checkpoint_blobs_table_bytes` verbatim | `:531-536` sums **three** relations; **no** single-table `checkpoint_blobs` size query exists — the metric is a split-out of that one round trip | `daemon/services/maintenance.py:531-536` |
| C10 | wedge-capture reason `concentration` | the constant is **`thread_concentration`** | `daemon/services/wedge_capture/triggers.py:56` |
| C11 | failover bookkeeping is an in-closure dict | **confirmed** — `counts = {"transient": 0, "timeout": 0, "swapped": False}` | `daemon/llm_error_classifier.py:818` |
| C12 | pool-stat honesty vocabulary at `collector.py:24-25` | `:24-26` = `pool_checkout_timeouts` ALWAYS-`None`; `:19-20` derived `checked_out`; `:23` native `requests_waiting`; `:29` `pool_requests_errors` | `daemon/services/wedge_capture/collector.py` |
| C13 | instance statuses = "the actual 10 DB statuses" | **confirmed** — 10 values | `daemon/repositories/instance/models.py:20-32` |
| C14 | sweep lanes = "the actual 7" | the 7 RDRS **lanes** are right, but the `lane` label is **13** (7 RDRS + 6 standalone sweep services) | `daemon/services/report_delivery_recovery.py:219-266` + the six `DEFAULT_*_INTERVAL_SECONDS` |
| C15 | `job_queue_items` has only `created_at` | **confirmed** — TEXT ISO at `:220` and `:357`; no `started_at`/`completed_at` ⇒ durations NOT derivable | `daemon/repositories/job_queue/models.py` |
| C16 | message_queue timestamps are boot-scoped wiped | **confirmed** — `QUEUE_DISCARD_ON_STARTUP` at `daemon/config.py:6429`; columns at `daemon/repositories/message_queue/models.py:83-89` | as cited |
| C17 | `orphan_released` is cumulative | the **docstring** says cumulative; the **code** returns it per-tick (reset at `job_queue_service.py:837`) ⇒ doc drift; the metric accumulates | `daemon/services/job_queue_service.py:828-837` |
| C18 | worker pool has 4 in-RAM counters | **confirmed** | `daemon/services/worker_pool.py:1381-1386` |
| C19 | `instance_fleet_summary` = richest live stats, agent-tool only | **confirmed** — an alias of `fleet_capacity_summary` (`daemon/tools/instance_read.py:1152`), agent-tool only | `daemon/tools/instance_read.py:1152-1164` |
| C20 | `long_tool_nudge.py:955` | the module is `daemon/services/long_tool_nudge.py`, not `daemon/tools/` | `daemon/services/long_tool_nudge.py` |
| C21 | GII throttle dict at `manager.py:1470` | **confirmed** — `self._gii_throttle: dict[str, int] = {}` | `daemon/manager.py:1470` |
| C22 | per-instance tier broken by bool→INTEGER 42804 | ~~**confirmed**~~ — **SUPERSEDED by C35 (2026-10-29): REFUTED.** The `estimated` column in prod is `boolean NOT NULL` (`daemon/models/resource_monitor.py:231-233`), the `bool(...)` bind at `flusher.py:770` is correct against it, and the window-rollback amplifier was already fixed (`7be1b40bf` per-tier SAVEPOINT + `69cea24ea` `flush.errors`). The 42804 reading was a historical incident (the `flusher.py:790-806` docstring), not a live defect; the real cause of the dark tier is the macOS dead producer (C35) | as cited + `feature/resource-monitor-flusher-fix@53547b915` |
| C23 | `lag_probe` dark when `ENSEMBLE_WEDGE_CAPTURE_ENABLED` off | **confirmed** — the probe is driven from the watchdog thread | `daemon/services/wedge_capture/lag_probe.py:124-134` |
| C24 | the tool-call family instruments graph internals | **reversed** by m7 — it instruments the `message_tap.py` per-turn seam | `daemon/services/message_tap.py:234` |
| C25 | VictoriaMetrics / Mimir are compatible targets | **dropped** (m9) — only stock Prometheus + Grafana is claimed | — |
| C26 | journal metrics come from bash | **reversed** — they come from the Python twin `daemon/tools/upgrade_journal.py`; `scripts/upgrade/` is read-only, never edited | `daemon/tools/upgrade_journal.py` |
| C27 | tool-category vocabulary = 37/38 (§8.6) | **falsified** — verified 49 distinct across 254 sites; v1 hand-list omitted 8+ (`board-link`, `dynamic-skill`, `ens-db`, `ens-env`, `image-gen`, `self-alarm`, `skill-evolution`, `system-log`); fix: derive at runtime from `get_tool_categories()` (`daemon/tools/_tool_registry.py:1598`) + reserved `other` | `_tool_registry.py:1598` |
| C28 | F14 observation site = `graph.py:3811-3812` | **wrong hook** — `:3811-3812` is the non-terminal, per-chunk path; terminal usage chunk returns early at `:3776-3784`; fix: hook terminal branch (or `:9176-9179`) + per-call dedupe | `daemon/graph.py:3776-3784` |
| C29 | `ensemble_wake_turns_total` consumer = exposition renderer reads carrier | **unimplementable** — carrier dies with the message; split producer into claim path (`worker_pool.py:1381-1386`) + episode seam (`graph.py:7319`); a live turn-end consumer increments, renderer reads accumulated counter | `daemon/services/message_processing_pipeline.py:121` |
| C30 | "unwired registry" / "zero non-test callers" | **corrected** — registry is wired to collectors (`checkpoint_perf.py:28-30` imports, observes `:190`/`:198`, increments `:233`); only `render_metrics()` is unwired in production | `daemon/checkpoint_perf.py:28-30` |
| C31 | F20 counting unit unspecified | **defined** — one `tap_node_return` invocation (5 sites, N+1 per turn); turn-duration needs an anchor pair or drop (tap measures its own DB-write latency) | `daemon/services/message_tap.py:234`, `:302-306` |
| C32 | `ensemble_checkpoint_op_seconds{op="aput"}` declared | **cannot fire** — write path bypasses `time_saver_op`; instrument `checkpoint_adapter.py` charge seam (`:371-374` → `aput`/`aput_writes` `:377-379`) or narrow enum | `daemon/checkpoint_adapter.py:377-379` |
| C33 | `ensemble_metrics_registry_measurements` ambiguity | **clarified** — counts measurements, not series | `daemon/checkpoint_metrics.py:304` |
| C34 | census statement/connect timeouts unspecified | **set** — 5 s / 3 s; execution home = dedicated daemon thread; self-exclusion = census only; blank `pg_stat_activity.query` lands in `other` | `daemon/services/wedge_capture/db_capture.py` |
| C35 | the resource tier's darkness was caused by the flusher (`bool`→`INTEGER` 42804 every window, all four tables rolled back through the shared `engine.begin()`; runtime fix pending at `flusher.py:770` / `manager.py:7341`) | **REFUTED (2026-10-29, WS-A correction round).** Prod facts: the `estimated` column is `boolean NOT NULL` (`daemon/models/resource_monitor.py:231-233`); the bind is correct (bind-audit test @ `feature/resource-monitor-flusher-fix@53547b915` checks every flusher column against live `information_schema` + round-trips a real bool); the rollback amplifier was already fixed (`7be1b40bf` per-tier SAVEPOINT, `69cea24ea` `flush.errors` surfacing). **Real cause — a dead PRODUCER on macOS:** the prod daemon is a macOS binary; Darwin has no `/proc` and no `/sys/fs/cgroup`, so `proc_readers` returned `None` every tick → the sampler skipped every tick → the flusher had nothing to write. Live signature: samples climbing, `flush {batches:1139, bytes:0, errors:0}` = "produced nothing", NOT "write failed"; `resource_pid_map` climbed (45,575 rows at commit time) from the spawn path, **masking the outage**. Fix = the WS-A branch: Darwin ps-based reader (engaged ONLY when the proc root is ABSENT and the host is Darwin) + corrected sampler counters (`missed_samples`/`ticks_skipped` parity, skips excluded from `counters.ticks`, one-shot WARNING) + 7 unit / 6 real-PG tests. Merges to latest BEFORE P0 executes. Platform consequences: catalog §13; per-child T3 stays `/proc`-only (§13.4 non-goal) | `feature/resource-monitor-flusher-fix@53547b915` (`proc_readers.py`, `sampler.py`, `tests/postgres/test_resource_monitor_flusher_bind_pg.py`); prod census 2026-10-29 |

---

## 12. Open items (carried to `decisions.md`)

1. **`ensemble_db_pool_requests_errors_total{kind}` emits only `unclassified` today.** The
   precise split (`waiting_exception` / `too_many_requests`) needs the daemon's own checkout
   seam instrumented. Decide whether that seam lands in P1a or is deferred; the label set is
   forward-compatible either way.
2. **`ensemble_checkpoint_op_bytes` is under-reported for `aget`** until the blob byte count is
   captured at the `log_saver_op` seam (`daemon/checkpoint_perf.py:148`). P1b must either wire
   it or record the metric as "bytes for maintenance carveouts only".
3. **`ensemble_thread_cpu_seconds{thread_group="worker_pool"}` / `chat_worker_pool` need the
   worker threads named** (`daemon/services/worker_pool.py:253`). Until then they fold into
   `other` and the metric is partially blind.
4. **`ensemble_job_claim_latency_seconds` has no existing instrument** (m3). The producer must
   be chosen so the enqueue→claim interval is observed once and carried, not re-derived.
5. **The MCP roster churn policy** (§8.5) collapses user-created servers into `custom`. If
   operators need per-user-server metrics that is a second, explicitly-labelled family — it is
   not smuggled into F17.
6. **`ensemble_sweep_rows_total{lane,outcome}` is per-lane in practice** (most lanes emit 2–3
   of the 6 outcomes). The shared enum is fixed; the live combination count (≈30) is what the
   series estimate uses.

---

## 13. Platform support matrix — Linux vs macOS-Darwin (WS-A correction, 2026-10-29)

**Why this section exists (the corrected fact).** The resource_monitor spine was **100% dark on
this deployment's prod host (macOS)**. Since v0.25.0 the prod daemon (a macOS host binary) received
**ZERO rows** in `resource_daemon_bucket` (T1) and `resource_instance_sample` (T2):
`proc_readers` returned `None` on every tick (no `/proc`, no `/sys/fs/cgroup` on Darwin) → the
sampler skipped every tick → the flusher had nothing to write. Live signature (prod, 2026-10-29):
samples climbing, `flush {batches:1139, bytes:0, errors:0}` — **"produced nothing", NOT "write
failed"**. Only `resource_pid_map` (T3) climbed (45,575 rows at WS-A commit time) because it is fed
by the spawn path — **masking the outage**. The fix is the parallel workstream
**`feature/resource-monitor-flusher-fix@53547b915`** (merges to latest **before P0 executes**): a
**Darwin ps-based reader** (`_read_platform_daemon_snapshot` → `_read_darwin_daemon_snapshot`,
`daemon/services/resource_monitor/proc_readers.py`) engaged **ONLY when the proc root is ABSENT and
the host is Darwin** (an existing-but-unreadable proc root keeps the historical `None` contract;
Linux `/proc` stays authoritative), plus the corrected sampler counters (F0.3 canonical-counters
rule: `missed_samples` / `ticks_skipped` parity, skipped samples excluded from `counters.ticks`,
one-shot WARNING). The earlier "flusher binds bool → INTEGER 42804, window rolls back" hypothesis
is **REFUTED** — see §11 C35.

**Reader verification (in-tree @ 53547b915).** The Darwin fallback supplies, at the **daemon
level only** (`DaemonProcSnapshot`): `rss_kb`/`vsize_kb` via `ps -o rss=,vsz=` (Darwin reports KB),
`threads` via `ps -M`, `fds` via `/dev/fd`, `cpu_user_usec`/`cpu_sys_usec` via
`resource.getrusage(RUSAGE_SELF)`, `hw_peak_kb` via `ru_maxrss` (BYTES on Darwin, ÷1024),
`anon_kb=rss_kb` (degraded proxy — Darwin exposes no private-anonymous counter), `pss_kb=None`
(no `smaps_rollup`). It is **not** a per-pid sampler: `read_child_snapshot` has **no** Darwin
fallback, so per-child (T3) attribution stays `/proc`-only even after the merge (A2 non-goal
below). `read_cgroup_snapshot` returns `None` without a cgroup v2 root — all cgroup fields stay
Linux-only.

### 13.1 Field-level split (the spine's own fields)

| Field (spine schema) | Linux | macOS-Darwin (post-WS-A) | Consumer-visible effect |
|---|---|---|---|
| daemon `rss_kb`, `vsize_kb` | `/proc/self/{statm,status}` | `ps -o rss=,vsz=` (KB) | cross-platform post-merge |
| daemon `cpu_user_usec`/`cpu_sys_usec` | `/proc/self/stat` jiffies | `getrusage(RUSAGE_SELF)` | cross-platform post-merge |
| daemon `threads`, `fds` | `/proc/self/{status,fd}` | `ps -M`, `/dev/fd` | cross-platform post-merge |
| daemon `hw_peak_kb` | `status.VmHWM` | `ru_maxrss` (BYTES→KB) | cross-platform post-merge — **present on both** (the "Linux-only" label sometimes attached to hw-peak is wrong for the post-merge reader) |
| daemon `anon_kb` | true private-anon (`VmData`, PSS-refined) | **degraded proxy** `anon_kb=rss_kb` | `coverage_pct = min(1.0, attributed/anon_bytes)` (`sampler.py:505-517`) becomes an **rss-denominated** ratio on Darwin — directionally useful, not comparable to Linux anon-based coverage |
| daemon `pss_kb` | `smaps_rollup` (1/10 cadence, env-gated) | **Linux-only** — always `None` | any PSS-derived view renders the no-data contract on Darwin |
| `swap_kb` (T1 column, `models/resource_monitor.py:124`) | reserved — **no reader populates it today** | NULL (same as Linux today) | column exists on both; unwired everywhere |
| cgroup memory (`current_mb`, `peak_mb`, `anon_mb`, `file_mb`, `slab_mb`) | cgroup v2 | **Linux-only** — `read_cgroup_snapshot` → `None` | cgroup-derived views render nothing on Darwin; sampler degrades to daemon-only |
| cgroup cpu (`usage_usec`, `throttled_usec`, `nr_throttled`) | cgroup v2 | **Linux-only** | same |
| per-child T3 (`child_rss_kb`, `child_cpu_user_usec`, `cpu_share_usec_est`, attribution rows in `resource_pid_map`) | `/proc/<pid>/{stat,status}` via `read_child_snapshot` | **Linux-only, permanently** (A2 non-goal — no Darwin fallback by design) | see 13.2 |

### 13.2 Per-family matrix (every family, F0.1–F22)

| Family | Scope | Notes (fields / consumer-visible effect) |
|---|---|---|
| F0.1 exposition listener | **cross-platform** | pure stdlib socket — no `/proc` dependency; boots and serves on Darwin |
| F0.2 registry measurements | **cross-platform** | registry is in-process |
| F0.3 spine health gate | **dark in prod TODAY → live after WS-A merge** | gate BLOCKS until `feature/resource-monitor-flusher-fix@53547b915` is merged; post-merge the gauge/counter work on Darwin with daemon-level fields only |
| F0.4 vocabulary version | **cross-platform** | |
| F1 loop lag | **cross-platform** | lag probe is in-process timing, not `/proc` |
| F2 thread CPU/count | **Linux-only — dark on macOS prod today AND after the merge** | wedge sampler is `/proc`-only; WS-A touches nothing under `wedge_capture/`; per-thread jiffies have no portable fallback |
| F3 wedge capture counters | **cross-platform** (triggers), F1-dependent | capture *content* on macOS lacks thread-level detail (F2 side); the counters themselves fire |
| F4/F5 DB pools | **cross-platform** | psycopg pool state |
| F6 PG census | **cross-platform** | SQL against `pg_stat_activity` |
| F7–F9 checkpoint / readiness | **cross-platform** | registry + PG |
| F10–F13 jobs / workers / sweeps / instances | **cross-platform** | |
| F14–F16 LLM | **cross-platform** | |
| F17–F18 MCP / gateway | **cross-platform** | |
| F19 compaction | **cross-platform** | |
| F20 turn/tool seam | **cross-platform** | message_tap |
| F21 journal/upgrade twin | **cross-platform** | file-backed |
| F22 textfile + exporters | **cross-platform** (host) | node_exporter runs on macOS for host CPU/mem; the textfile writer is shell-side |

### 13.3 `/api/resources/live` and `/api/resources/history` payload shape on Darwin

- **Pre-merge (prod today):** the monitor is *enabled with an empty ring* — `/live` renders the
  live card with `pid: null`, no cpu series (`_cpu_pct_from_rows` has no rows), and `/history`
  returns the empty-points shape (`_empty_history_shape` carries `resolution_used` + `truncated`;
  D11 never-503). This is the "monitor green, data absent" masking state.
- **Post-merge (after 53547b915):** both endpoints return real points with **daemon-level fields
  populated** (rss/vsize/cpu/threads/fds/hw-peak) and **cgroup + PSS + per-child blocks absent**
  (§13.1). `coverage_pct` semantics change denominator as described in §13.1 (rss-based on
  Darwin). T3 `resource_pid_map` keeps behaving as today (spawn-path-fed) — it is NOT the sampler's
  per-child view on Darwin.
- **Dev-box degradation:** a Linux dev box renders the FULL shape; a macOS dev box post-merge
  renders the same coarse shape as prod. Windows is not a supported host for the reader (the
  fallback is Darwin-guarded; other non-Linux hosts keep the `None` contract and stay dark).

### 13.4 Non-goal — T3 per-child RSS/CPU attribution on macOS (DECIDED, implement as decided)

**Per-child (T3) RSS/CPU attribution stays `/proc`-only** (`read_child_snapshot` /
`resource_pid_map`) **even after the WS-A fix** — the Darwin ps-based reader is a **coarse
daemon/process-level view, not a per-pid sampler**, and must not be silently promised to grow into
one. Consequences, stated plainly: per-child families/values are **dark on Darwin**; the
`coverage_pct` denominator on Darwin is the degraded rss proxy (§13.1) and per-child attribution
contributes nothing to it on macOS; **no partial per-child data ships on macOS** — the absence is
the honest contract, not a defect to be backfilled later. Any future per-pid Darwin story is a new,
separately-scoped decision.
