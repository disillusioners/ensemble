# Metric Catalog (v2) — the per-family metric specification

**Date** 2026-10-29 · **Status** DRAFT — grounding for `plan-overview.md` + `decisions.md` ·
**Scope** the **daemon-origin** metric surface; exporter-origin series are §6, outside the ≤2k
daemon budget; skill-metrics (`/api/skills/{id}/metrics`) is a **non-goal**, not catalogued ·
**Verified against** worktree `agents-ensemble-wt-metrics-v2` @ `feature/metrics-collection`
(tip `9b8437c9f`; WS-A platform-factual correction round applied same day — §13, §11 C35) —
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
**Current anchors (post-WS-B registry — the `checkpoint_metrics.py:NNN` pointers in this paragraph
describe the PRE-WS-B file, which the registry generalization replaced wholesale):** the
per-object getter is `Metric.get` (`daemon/checkpoint_metrics.py:741`), each metric's own
`threading.Lock` is created at `:482` and exposed at `:493-495`, the single-bucket write is
`_record_bucket` (`:720-727`) and the cumulative accumulation happens at render
(`get_bucket_counts`, `:780-799`), and the registry-level bookkeeping counters are at
`:1027-1061`.

**Publisher cadence (R-m5).** Per-increment copy-on-publish is **O(series)** — a writer must not pay that
on every increment. The registry therefore publishes on a **bounded cadence**: a family sets a dirty
flag on observe and **re-publishes at most once per N seconds** (default N = the scrape interval
hint, `limits.metrics_scrape_interval_hint_s`, 15 s), or, equivalently, **publish-on-scrape-request**
with a bounded staleness (the last published snapshot is never older than N seconds). Writers only
flip the dirty flag (O(1)); the O(series) copy happens at most once per N seconds on the writer's
own lock, never per increment. Render serves the last published snapshot and, on contention, the
last published snapshot plus `# <family> unavailable: lock_contended`. The renderer's snapshot
staleness is therefore **bounded and stated** (≤ N seconds); a scrape never observes an unbounded
staleness. This is the bounded alternative to per-increment copy-on-publish and is required by the
P0-core gate (P-3).

**Freeze rules (A27).** Import the listener on the **lifespan path** (not `daemon/api.py`
module top level) so the PyInstaller analyzer pulls it. Register aliases at
**registry-construction time** (not lazily) so a cold start still emits the deprecated names for
the remainder of their one-release window.

**Off-loop discipline (restated, A28).** The blanket "never adds event-loop work" claim is
literally false — every increment is loop or executor-thread work. The accurate rule: **no
blocking work, no I/O, no logging, and no per-chunk work on the event loop**, plus **≤1
`time.perf_counter()` read per observation window**. Metric increments inside `message_tap` must NOT sit inside the tap's outer
`try/except` (`daemon/services/message_tap.py:308-309`) — that `except` logs a WARNING
(`[MessageTap] non-fatal: ...`), so a metric error there would emit per-turn WARNING noise.
Instead each increment sits in its **own dedicated inner `try/except` that swallows silently**
(or outside the tap's failing region), so a metric failure stays quiet. Per-turn cost budget: ≈4 µs added
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
(`daemon/checkpoint_metrics.py:356-359`) — **too short**: the 17 s writes and 73–99 s reads fall
into `+Inf` and collapse p95 to 10.0. F7 extends the top to `300.0` s.

**0.5 Labels (MA-12) + executable never-exported enforcement (A10).** Every label value set is
written out in full — no `...`, no "etc." — and lives in the **vocabulary annex (§8)**, the
single place each set is defined, with the `file:line` it was verified against. Family entries
reference the annex by number.

**Executable enforcement (three tiers + defence in depth):**
1. **Registration-time enum pin** — `register_histogram(name, label_keys, allowed_values={...})`
   rejects a wrong label *key* at registration (raises, mirroring today's `KeyError` at
   `checkpoint_metrics.py:525-530`); cost 0 at runtime.
2. **Observe-time reserved bucket** — a wrong label *value* is folded to `other` and counted in
   `ensemble_metrics_invalid_label_total{metric,label}` (~40 ns frozenset membership).
3. **Render-time allowlist predicate** — per-family label-name allowlist + per-label value
   membership + defence in depth (also the §7 never-exported hook).

**Hard cardinality cap — atomicity (R-m3).** `limits.metrics_cardinality_cap` is **hard**: observe
beyond the cap is **rejected, not warned**. The observe-time rejection is **atomic under the
metric's own lock** — check-then-increment happens inside the per-metric lock, so there is **no
TOCTOU window** between the cardinality check and the increment. A concurrent observer cannot
slip past the cap. Rejected observations are counted in **`ensemble_metrics_cap_rejected_total`
(folded into F0.2, the canonical-registry family)** — a counter, `{metric}`-labeled, so the
operator can see which metric hit the cap. The cap check and the rejected-counter increment are
part of the same locked critical section. Plus: name regex `^ensemble_[a-z0-9_]+$`, and a
label-value charset gate (no `=`, `,`, `"`, newline). Add the poisoned-registry defence-in-depth
test. All §8 vocabularies are **derived from code, never hand-listed** (A1) — see the rule
stated in §8.

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
default **OFF**. OFF ⇒ the listener returns `200` with a valid empty body (no
`ensemble_metrics_up` series and no family series); the registry keeps accumulating so switching
ON loses no history. `ensemble_metrics_up` is emitted only when the listener is live (NB-11:
one semantic — empty body when off; present-only-when-live). Registered as managed key `limits.metrics_enabled`
in `MANAGED_KEYS` (`daemon/services/settings_service.py:190`) — the registry default must mirror
the pydantic Field default exactly (one truth; the A4-knob lesson — *a managed-key-knob lesson, not an amendment reference*).

**Truthiness & layering (A14) — ONE rule, three cases.** (i) **env explicitly set** (present in
the environment, including set-but-empty) → the truthy set is `{1, true, yes}` case-insensitive ⇒
ON; **every other explicit value is OFF** — `ture`, `on`, `enabled`, `0` and the set-but-empty
string all read as OFF, because an **unrecognised explicit value fails closed**. (ii) **explicit
OFF** ⇒ OFF, and it **wins over** the managed-key chain. (iii) **env UNSET** (absent) ⇒ the
**managed-key chain decides** (unset + managed key on ⇒ ON, because env-unset is not a statement of
intent; unset + default ⇒ OFF); explicit env ON overrides the managed-key default. **"Fail closed"
therefore describes only an invalid *explicit* value — never an unset variable.** Plus **one boot
log line** when the env is unset while the managed key is on: "`ENSEMBLE_METRICS_ENABLED` unset;
deferring to `limits.metrics_enabled`". The per-request env read stays (~50–100 ns, no syscall; a
TTL cache would defeat the emergency-off purpose).

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
| F0.2 | `ensemble_metrics_registry_measurements`, `ensemble_metrics_cap_rejected_total`, `ensemble_metrics_invalid_label_total` | gauge/counter/counter | P0 | reuse `checkpoint_metrics.py:1392` | 3 |
| F0.3 | `ensemble_resource_spine_tables_accepting_rows`, `ensemble_resource_spine_rows_written_total` | gauge/counter | P0 | reuse (resource_monitor spine) | 8 |
| F0.4 | `ensemble_metrics_vocabulary_version` | gauge | P0 | greenfield (freeze marker) | 1 |
| F1 | `ensemble_loop_lag_seconds`, `ensemble_loop_lag_samples`, `ensemble_loop_lag_overflow_total`, `ensemble_loop_lag_probe_state` | gauge×3/counter | P1a | reuse `wedge_capture/lag_probe.py` | 9 |
| F2 | `ensemble_thread_cpu_seconds`, `ensemble_thread_count` | gauge×2 | P1a | reuse `wedge_capture/sampler.py` | 22 |
| F3 | `ensemble_wedge_capture_total`, `ensemble_wedge_capture_write_total` | counter×2 | P1a | reuse `wedge_capture/triggers.py` | 6 |
| F4 | `ensemble_db_pool_native`, `ensemble_db_pool_requests_errors_total` | gauge/counter | P1a | reuse `readiness.py:525`,`:552`; `collector.py:29` | 8 |
| F5 | `ensemble_db_pool_checked_out`, `ensemble_db_pool_waiting_clients` | gauge×2 | P1a | reuse (derived + native, kept apart) | 3 |
| F6 | `ensemble_pg_backends`, `ensemble_pg_idle_in_txn_oldest_seconds`, `ensemble_pg_leaked_txn`, `ensemble_pg_census_last_success_timestamp_seconds` | gauge×4 | P1a | greenfield (census seam, §9) | 15 |
| F7 | `ensemble_checkpoint_op_seconds`, `ensemble_checkpoint_op_bytes`, `ensemble_checkpoint_slow_ops_total`, `ensemble_checkpoint_list_total` | hist×2/counter×2 | P1b | reuse `checkpoint_perf.py:190`,`:198`,`:91` | 21→31 |
| F7b | `ensemble_checkpoint_read_ops_total`, `ensemble_checkpoint_read_rows_total`, `ensemble_checkpoint_read_bytes_total`, `ensemble_checkpoint_read_size_bucket_hits_total`, `ensemble_checkpoint_read_measurement_errors_total`, `ensemble_checkpoint_leak_signal_total`, `ensemble_checkpoint_read_size_bytes` | counter×6/hist | P1b | reuse `checkpoint_perf.py:273-277`,`checkpoint_adapter.py:318`,`readiness.py:1091` | 46 |

| F8 | `ensemble_checkpoint_blobs_table_bytes`, `ensemble_checkpoint_table_bytes`, `ensemble_checkpoint_versions_per_thread`, `ensemble_checkpoint_prune_runs_total` | gauge×3/counter | P1b | reuse `maintenance.py:531` + greenfield (per-thread) | 9 |
| F9 | `ensemble_readiness_probe_total`, `ensemble_readiness_grace_active`, `ensemble_readiness_failure_streak`, `ensemble_readiness_component_state` | counter/gauge×3 | P1b | reuse `readiness.py:64-79` | 53 |
| F10 | `ensemble_job_queue_depth`, `ensemble_dead_letter_items`, `ensemble_job_queue_items_created_total` | gauge/gauge/counter | P1b | reuse (admission_state) | 6 |
| F11 | `ensemble_workers_total`, `ensemble_workers_busy`, `ensemble_worker_pool_wakeup_efficiency`, `ensemble_worker_pool_events_total`, `ensemble_task_heartbeat_age_seconds`, `ensemble_task_heartbeat_cadence_seconds`, `ensemble_job_claim_latency_seconds` | gauge×3/counter/gauge×2/hist | P1b | reuse `worker_pool.py:1518`; `task/models.py:284` | 30 |
| F12 | `ensemble_sweep_cycle_seconds`, `ensemble_sweep_cycles_total`, `ensemble_sweep_errors_total`, `ensemble_sweep_rows_total` | gauge/counter×3 | P1b | reuse (6 sweeps + 7 RDRS lanes) | 69 |
| F13 | `ensemble_instance_status_count`, `ensemble_instance_liveness_count`, `ensemble_waiting_children_count`, `ensemble_waiting_children_max_age_seconds`, `ensemble_wake_turns_total`, `ensemble_instance_lifecycle_total` | gauge×4/counter×2 | P1b | reuse `instance/models.py:20-32` + greenfield (wake outcome) | 29 |
| F14 | `ensemble_llm_tokens_total`, `ensemble_llm_token_usage_missing_total` | counter×2 | P1c | reuse `graph.py:3776-3784` (terminal-usage branch, A2) | 3 |
| F15 | `ensemble_llm_request_total`, `ensemble_llm_request_seconds` | counter/hist | P1c | reuse `llm_error_classifier.py:764-819` | 22 |
| F16 | `ensemble_llm_stream_stalls_total`, `ensemble_llm_stream_watchdog_sweep_total` | counter×2 | P1c | reuse `llm_stream_watchdog.py:456` | 5 |
| F17 | `ensemble_mcp_server_state`, `ensemble_mcp_failures_total` | gauge/counter | P1c | reuse `mcp/init_guard.py:145-147` | 40 |
| F18 | `ensemble_mcps_gateway_sessions`, `ensemble_mcps_gateway_session_total`, `ensemble_mcps_gateway_tombstones`, `ensemble_mcps_gateway_audit_total` | gauge×2/counter×2 | P1c | reuse `services/mcps_gateway/` | 9 |
| F19 | `ensemble_compaction_fired_total`, `ensemble_compaction_occupancy_ratio`, `ensemble_compaction_precall_estimate_tokens` | counter/gauge×2 | P1c | reuse `compaction.py:1128`,`:2299` | 7 |
| F20 | `ensemble_turn_events_total`, `ensemble_turn_duration_seconds`, `ensemble_tool_calls_total` | counter/hist/counter | P1c | reuse `message_tap.py:234` | 75 |
| F21 | `ensemble_journal_file_bytes`, `ensemble_journal_history_entries`, `ensemble_journal_op_seconds`, `ensemble_journal_ops_total`, `ensemble_upgrade_phase_seconds`, `ensemble_upgrade_phase_total`, `ensemble_upgrade_release_current`, `ensemble_upgrade_journal_events_total` | gauge×3/hist/counter×3/gauge | P1c | reuse `tools/upgrade_journal.py` | 65 |
| F22 | `ensemble_process_cpu_seconds` + `ensemble_textfile_last_write_timestamp_seconds` + host/PG exporter series | gauge | P2 | greenfield (textfile + off-the-shelf) | 4 shell / ~67 exporter |

**Daemon-origin total: 580** (F0.1→F21 + F7b) + **4 shell-origin** series (F22) — the
**daemon-budget total**. The certified **grand total on the Prometheus server** is **≈651**: it
adds the **≈67 exporter-origin** series (§6), which are measured separately and are NOT part of
the daemon-origin budget (catalog §14.1 → *Grand total on Prometheus server*).
**Budget ≤2k — ≈29 % consumed (headroom 1 420).**

*Reconciliation basis:* §7 counts the §1 row values, with the one range cell (**F7**
`21→31`) taken at its **today** value **21** — the bytes histogram's +10 arrives with the P1b
byte wiring (catalog G-5). Row sums: P0 28 + P1a 63 + P1b 263 + P1c 226 = 580.

*Delta trail (the basis of the 580).* Every delta is stated against the **§1 summary table** —
the table is what summed to the **prior, retired daemon-origin total (538)**, so a delta quoted
from a family *detail line* instead would not reconcile. Each item carries its own baseline:

| Item | Baseline (the retired total's own §1 table cell) | Current (§1 table cell) | Δ | Note |
|---|---|---|---|---|
| F0.2 | 2 | 3 | **+1** | the invalid-label counter (B-09). The cap-rejected counter (R-m3) was **already inside** the retired baseline (F0.2 = 1 + 1) — it is **not** a second +1. |
| F10 | 21 | 6 | **−15** | the claim-latency family (14) moved to F11 (B-08). The v5 *detail line* read 20 while its table cell read 21; the baseline here is the table cell (the one that summed to the retired total). |
| F11 | 16 | 30 | **+14** | receives `ensemble_job_claim_latency_seconds` (12 buckets + 2 = 14). |
| F13 | 31 | 29 | **−2** | wake 7→5 (B-06) + lifecycle 7→5 (B-07) against the **table** baseline 31; the v5 *detail line* read 33, and mixing the two bases is where the old "−4" came from. |
| F15 | 23 | 22 | **−1** | the table cell dropped the reserved `3600.0` bucket rounding (the v5 detail line already read 22). |
| F21 | 66 | 65 | **−1** | the table cell dropped the v5 rounding (the v5 detail line already read 65). |
| F7 | 21 | 21 | **0** | the 22→21 decomposition (R-m8) was **already inside** the retired baseline — it is **not** a delta. |
| F7b | 0 | 46 | **+46** | the checkpoint quick-wins READ family, catalogued **retroactively** (C95): it SHIPPED from the checkpoint read-path quick-wins lane **before this catalog existed**, so it was never inside the retired baseline — it is an addition, not a move. |
| F22 (shell-origin) | 5 | 4 | −1 | **outside** the daemon-origin budget (NB-8 + B-02(b) freshness gauge) — it moves the **shell-origin line** (the daemon + shell subtotal), not the daemon-origin 580. |

**538 (the retired pre-reconciliation baseline) + 1 (F0.2) − 15 (F10) + 14 (F11) − 2 (F13) − 1 (F15) − 1 (F21) + 46 (F7b) = 580.**
Headroom vs. the ≤2 000 budget: **1 420**.

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
- **Degradation:** kill-switch OFF ⇒ empty body, no series at all (including no
  `ensemble_metrics_up`); the scrape counter still increments `outcome="ok"` (the scrape
  happened, the body was empty); **every other family renders no series**. `ensemble_metrics_up`
  is emitted only when the listener is live. The exposition histogram records the empty render,
  so the operator sees "listener alive, switch off" (NB-11).

### F0.2 — Canonical registry generalization

- **Name:** `ensemble_metrics_registry_measurements` (gauge, no labels) · **`ensemble_metrics_cap_rejected_total` (counter, `{metric}`-labeled)** (R-m3 — folded into this family; the hard cap's atomic observe-time rejection count) · **`ensemble_metrics_invalid_label_total` (counter, `{metric,label}`-labeled)** (B-09 — the rejected-value signal for the 3-tier validation: a wrong label *value* folded to `other`). **Type:** gauge; counter; counter. **Labels:** none (measurements) / `metric` (rejected, cap) / `metric` + `label` (invalid label).
- **Source:** **reuse** — `daemon/checkpoint_metrics.py:1392` `render_metrics()` becomes the registry renderer; the two existing singletons (`:996` and `:1007`) become registry-registered instances under their canonical names. The rejected counter is emitted by the hard cap's locked check-then-increment path (catalog §0.5, R-m3). The invalid-label counter is emitted by the observe-time reserved-bucket fold path (catalog §0.5 tier 2) when a wrong label *value* is folded to `other`.
- **Clarification (A8):** `ensemble_metrics_registry_measurements` counts **measurements** (registered measurement objects), NOT series — dashboards must not conflate it with the ≤2 k series budget. CR-3 record correction: the registry is **wired to collectors** (`checkpoint_perf.py:28-30` imports the singletons, observes `:190`/`:198`, increments `:233`); only `render_metrics()` itself has zero non-test callers.
- **Phase** P0 · **Owner** developer; reviewer signs the naming migration · **Series estimate** 1 + 1 + 1 = **3** (the rejected counter is `{metric}`-labeled; estimate assumes a single representative metric hits the cap in the proof; cap rejections are rare by construction. The invalid-label counter is `{metric,label}`-labeled; `{metric}` is the family name from the closed **25-family** set (F0.1–F0.4 = 4 + F1–F21 = 21 → 25; the same set §14.1 quotes as the daemon-origin family count) and `{label}` is the label *key* name from the registry's allowed label-key set (a closed enum, never free text), with a reserved `other` residual — estimate assumes a single representative (metric,label) pair; invalid labels are rare by construction).
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
  **Labels:** `reason` ∈ `sustained_cpu`, `sustained_loop_lag`, `thread_concentration`,
  `manual` (§8.3; `REASON_MANUAL = "manual"` at
  `daemon/services/wedge_capture/triggers.py:57`, in `CANONICAL_REASONS` `:60-65`) ·
  `outcome` ∈ `ok`, `failed` (§8.3).
- **Source:** **reuse** — trigger evaluation sites
  (`daemon/services/wedge_capture/triggers.py:202`, `:237`, `:281`) and the watchdog's capture
  dispatch (`daemon/services/wedge_capture/watchdog.py:417`, `:418` `trigger_source=REASON_MANUAL`).
- **Phase** P1a · **Owner** developer · **Series estimate** 4 + 2 = **6** (R-m9: +1 for `manual`).
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
  declared in the registry's `allowed_values` (`daemon/checkpoint_metrics.py:1011`) and
  echoed in its HELP text (`:1012-1016`); `alist` is
  migration-only). **Narrowed until the write path is instrumented (A6/A7):** only `op="aget"`
  fires in production today; `aput`/`aput_writes` bypass `time_saver_op` and run through
  `daemon/checkpoint_adapter.py:617`/`:621`. **After the write-path seam lands, re-derive the `op`
  enum from observed call sites rather than inheriting the legacy HELP text
  (`checkpoint_metrics.py:1011`).** Buckets §8.11.
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
- **Write-path instrumentation task (V-6 — distinct write-side hook, do NOT reuse the read seam):** add a **separate** `_charge_checkpoint_write` (its own function, own budget/labels) firing once per successful outer call (same once-per-outer-call contract as `_charge_checkpoint_read`), called from `aput`/`aput_writes` (`checkpoint_adapter.py:617` / `:621`). The read seam `_charge_checkpoint_read` (`daemon/checkpoint_adapter.py:361` — the **single shared** charge call, reached by **both** read forwarders through `_observe_read` (`:335`; the `aget` forwarder `:607`, the `aget_tuple` forwarder `:614`); the two separate per-forwarder charge sites this entry used to cite were collapsed into one shared call by the read-instrumentation lane's M-2 refactor — see §11 C96) is **not** extended to writes — routing write bytes through the read charge would bill them to the READ budget. **Early-return-on-None for writes (decided):** a `None` result on a write path is an **error, not a skip** — the hook does **not** early-return on `None`; instead it records the failure so a missing write charge is visible. Until it lands, the declared `op` enum stays narrowed to `aget`. Risk row 3 carries the risk. After it lands, re-derive the `op` enum from observed call sites rather than inheriting the legacy HELP text (`checkpoint_metrics.py:1011`).
- **Phase** P1b · **Owner** developer; reviewer pins the bucket set · **Series estimate** only
  `op="aget"` fires in production (the sole prod call site is `daemon/persistence.py:509`,
  `time_saver_op("aget", ...)`) ⇒ seconds histogram 19, bytes histogram 0 today → 10 after the
  byte wiring, slow-ops counter 1, list counter 1 ⇒ **21 today, 31 after the byte wiring**
  (R-m8 — corrected decomposition: 19 + 0 + 1 + 1 = 21 today; 19 + 10 + 1 + 1 = 31 post;
  the prior "22/33" did not reconcile). Fully-wired ceiling: 76 + 40 + 4 + 1 = 121.
- **Degradation:** the metric side-effect is recorded **regardless of the log gate**
  (`daemon/checkpoint_perf.py:187-189` — "Metric records REGARDLESS of the log gate"), so
  `CHECKPOINT_PERF_LOGS=0` does **not** starve the histogram. A non-psycopg backend (SQLite)
  ⇒ the `op` series are absent.

### F7b — Checkpoint READ instrumentation (read-cost family — shipped, catalogued retroactively)

> **Origin (C95).** This family **SHIPPED** from the checkpoint read-path quick-wins lane
> (`feature/checkpoint-quick-wins`, from §quick-wins + §9 of the locked checkpoint read-path
> fixes plan) **before this catalog existed** — it is catalogued here **retroactively**, which is
> why its §1 delta is an **addition** (+46), not a move. It is the **read-side sibling of F7**:
> F7 measures the **saver-op** lane (write-side latency/bytes), F7b measures the **checkpoint
> READ** lane (payload size + row count at the saver retry proxy). The two seams stay separate
> for the same reason F7's V-6 note insists on a distinct write-side hook — routing read bytes
> through a write budget (or vice versa) would bill one to the other.

- **Name:** `ensemble_checkpoint_read_ops_total{op}` (counter) ·
  `ensemble_checkpoint_read_rows_total{op}` (counter) ·
  `ensemble_checkpoint_read_bytes_total{op}` (counter) ·
  `ensemble_checkpoint_read_size_bucket_hits_total{op,bucket}` (counter) ·
  `ensemble_checkpoint_read_measurement_errors_total` (counter) ·
  `ensemble_checkpoint_leak_signal_total` (counter) ·
  `ensemble_checkpoint_read_size_bytes{op}` (histogram). **Type:** counter; counter; counter;
  counter; counter; counter; histogram. **Labels:** `op` ∈ `aget`, `aget_tuple` (§8.9 — the
  checkpoint READ op set); `bucket` ∈ the 9 integral byte bounds of §8.9 (the read-size bucket
  labels). The two counters that carry no label (`..._measurement_errors_total`,
  `..._leak_signal_total`) are **plain** by design: the leak reason tokens embed tick counts
  ("(3 ticks)"), so labeling by reason would explode series cardinality.
- **Alias (one measurement, one name — §0.3).** Each metric also renders under its **historical**
  name as a **deprecated alias** — a **second exposition of the SAME series**, never a second
  measurement: `checkpoint_read_ops_total`, `checkpoint_read_rows_total`,
  `checkpoint_read_bytes_total`, `checkpoint_read_size_bucket_hits_total`,
  `checkpoint_read_measurement_errors_total`, `checkpoint_leak_signal_total`,
  `checkpoint_read_size_bytes`. The alias names violate the `^ensemble_` rule and are therefore
  **alias-only, never canonical**. The **Python symbol names were kept** (the deprecation is the
  exposition NAME, not the symbol) — `daemon/checkpoint_perf.py:28-35`,
  `daemon/checkpoint_adapter.py:57` and `daemon/services/readiness.py:39` import the historical
  names and are unaffected. A deprecation log line fires once per process per alias name.
- **Source:** **reuse — do not duplicate.** The observation seam already exists: the saver retry
  proxy (`daemon/checkpoint_adapter.py:607` — the `aget` forwarder; `:614` — the `aget_tuple`
  forwarder) calls `_observe_read` (`:335`), which walks the decoded payload **once** and hands
  the size to **both** consumers (the resource-charge seam and the instrumentation). The
  recording call site is `daemon/checkpoint_perf.py:273-277` — five metric updates
  (`:273` ops, `:274` bytes, `:275` rows, `:276` bucket hits, `:277` the size histogram
  observation). The **fail-open tripwire** is `daemon/checkpoint_adapter.py:318`
  (`checkpoint_read_measurement_errors_total.inc()`), reached from `_measure_checkpoint_read`
  (`:368`); the **leak-signal consumer** is `daemon/services/readiness.py:1091`
  (`checkpoint_leak_signal_total.inc()`), consumed from the readiness leak predicate.
- **Reset semantics:** idiom **A** (§0.6) — in-process registry, **resets on restart**; the
  registry's `reset_for_tests()` clears every series of the family (all 7 are registered in
  `daemon/checkpoint_metrics.py`, so the reset reaches them). A `rate()` / `increase()` is
  required of any alert over these counters (§10.2).
- **Phase** P1b · **Owner** backend (the checkpoint read-path quick-wins lane) · **Series
  estimate** 46 — `op` has 2 values, `bucket` has 9, and the size histogram contributes
  (8 finite buckets + 2) × 2 ops: `read_ops_total` 2 + `read_rows_total` 2 +
  `read_bytes_total` 2 + `read_size_bucket_hits_total` (2 × 9) 18 +
  `read_measurement_errors_total` 1 + `leak_signal_total` 1 + `read_size_bytes`
  (8 + 2) × 2 ops 20 = **46** (measured against the live registry, not estimated). The
  **alias** expositions add **no** series — they are the same series under a second name.
- **Degradation:** the measurement is **FAIL-OPEN** — a measurement failure never breaks or
  slows the read, and the failure is counted in the tripwire (the measurement is observability,
  so a silent failure would be a silent observability failure). The whole recording lane is
  gated by `CHECKPOINT_PERF_LOGS=0` (the kill-switch turns the metric recording off entirely,
  not just the log line). The **bucket-label vocabulary is integral** (`65536` … `8388608`,
  never a scientific-notation form), so the histogram's `le=` label and the bucket-hits
  counter's `bucket=` label are the **same string** for the same bound — the two surfaces join
  by label.

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

### F10 — Job queue depth (m3)

- **Name:** `ensemble_job_queue_depth{admission_state}` (gauge) ·
  `ensemble_dead_letter_items` (gauge) · `ensemble_job_queue_items_created_total` (counter).
  **Type:** gauge; gauge; counter. **Labels:** `admission_state` ∈ `queued`, `active`,
  `done`, `dead` (§8.10 — the `AdmissionState` enum, the **sole write authority** for queue
  gating).
- **Source:** **reuse** for depth (the `job_queue_items.admission_state` column with index
  `idx_job_queue_admission_state`, `daemon/repositories/job_queue/models.py:283`); the live
  surfaces already exist — `GET /api/queues`, `/api/jobs`, `/api/dlq`, `/api/work`.
  **Claim latency is GREENFIELD (m3):** there is **no** existing claim-latency instrument — the
  only related signal is the in-RAM `WorkerPool._stats["empty_claim_attempts"]`
  (`daemon/services/worker_pool.py:1383`), which counts empty polls, not enqueue→claim latency.
  **Claim latency moved to F11 (worker pool family):** the seam is the claim boundary
  inside `daemon/services/worker_pool.py` `wait_for_work` → the task-claim call, where the
  enqueue timestamp comes from the **task queue** (`task.created_at` / `task.started_at`,
  `daemon/repositories/task/models.py:273-274`), NOT `message_queue/models.py:83`
  `enqueued_at` (the claim path is the TASK queue — `worker_pool.py:302-304` →
  `task_processor.py:1829` → `task/repository.py:2619`; `grep message_queue worker_pool.py` = 0
  hits). The observation is taken **once**, at the claim site, and carried — never re-derived
  downstream. (B-08 re-anchor.)
- **Phase** P1b · **Owner** developer · **Series estimate** 4 + 1 + 1 = **6** (depth 4 +
  dead_letter 1 + created 1; claim latency (14) moved to F11, sourced from the task queue —
  `task.created_at` / `task.started_at`, `daemon/repositories/task/models.py:273-274` — not from
  `job_queue_items`, which carries only `created_at` TEXT ISO and no started_at).
- **Degradation:** queue repository unavailable ⇒ no depth series. `job_queue_items` carries
  **only** `created_at` as a TEXT ISO string (`daemon/repositories/job_queue/models.py:220`
  and `:357`) — no `started_at`/`completed_at` ⇒ **queue-item durations are NOT derivable**; any
  such metric is greenfield by construction and is not claimed here.

### F11 — Worker pool utilization and task heartbeat age (m4)

- **Name:** `ensemble_workers_total{pool}` (gauge) · `ensemble_workers_busy{pool}` (gauge) ·
  `ensemble_worker_pool_wakeup_efficiency{pool}` (gauge) ·
  `ensemble_worker_pool_events_total{pool,event}` (counter) ·
  `ensemble_task_heartbeat_age_seconds` (gauge) · `ensemble_task_heartbeat_cadence_seconds`
  (gauge) · `ensemble_job_claim_latency_seconds` (histogram, buckets §8.11). **Type:** gauge;
  gauge; gauge; counter; gauge; gauge; histogram. **Labels:** `pool` ∈ `worker`, `chat` ·
  `event` ∈ the **complete** `_stats` key set (§8.10) · claim latency carries no labels.
- **Source:** **reuse** — `WorkerPool.get_stats()` (`daemon/services/worker_pool.py:1518`)
  returns `num_workers`, `notifications_sent`, `empty_claim_attempts`,
  `workers_woken_by_timeout`, `claims_skipped_due_to_busy_instance` and the computed
  `wakeup_efficiency` (`:1537` — `notifications / max(1, notifications + empty_claims)`). The
  "busy" count is derived the way the live tool derives it
  (`daemon/tools/instance_read.py:2092` — a worker is busy when its `_current_task_id` is not
  `None`). These counters are in-RAM and **lost on restart** (§10).
  **Claim latency (moved here from F10 by B-08 — P0-e):** the seam is the **claim CALL**
  `daemon/services/worker_pool.py:302` (`task = self._task_processor.claim_task(...)`), reached
  via `daemon/services/worker_pool.py:302` → `daemon/services/task_processor.py:1829`
  `claim_task` → `daemon/repositories/task/repository.py:2619` `claim_pending_task`; the measured
  interval is the **task queue's** `task.created_at` / `task.started_at` columns
  (`daemon/repositories/task/models.py:273-274`) — **not** `message_queue/models.py:83`
  `enqueued_at` (the claim path is the TASK queue; `grep message_queue worker_pool.py` = 0
  hits). The observation is taken **once**, at the claim site, and carried — never re-derived
  downstream.
  **Heartbeat age (m4):** source column `daemon/repositories/task/models.py:284`
  `last_heartbeat_at` — a `timestamp without time zone` column, so bind/compare with
  `now_utc_naive()` per the project timestamp convention. **Cadence:** the per-worker
  `TaskHeartbeat` thread updates it at `DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0`
  (`daemon/services/worker_pool.py:45`; constructor parameter at `:197`). The metric publishes
  the **configured cadence** as its own series, so the operator can tell "heartbeat 40 s old,
  cadence 30 s" (stale) from "heartbeat 40 s old, cadence 300 s" (fine). The value is read from
  the pool's constructor argument, never hard-coded.
- **Phase** P1b · **Owner** developer · **Series estimate** 2 + 2 + 2 + 8 + 1 + 1 + 14 = **30** (adds `ensemble_job_claim_latency_seconds`: 12 buckets + 2 = 14, sourced from the task queue per F10's re-anchor).
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
  (lane, outcome) combinations = **≈69**. **Derivation (explicit — NB-5, §11 C80):** the three
  lane-scoped series (`cycle_seconds`, `cycles_total`, `errors_total`) are counted **one per
  lane** ⇒ 13 + 13 + 13 = 39; `ensemble_sweep_rows_total{lane,outcome}` is counted at its
  **observation-bounded** combination count ≈**30** — an OBSERVED figure, **not a rule**: each
  lane emits 2–3 of the 6 outcomes (13 lanes × 2–3 ≈ 26–39, midpoint 30), and the family
  **ceiling** is the full `lane` × `outcome` product 13 × 6 = 78 ⇒ family ceiling
  39 + 78 = **117**. The catalogued figure (39 + 30 = **69**) is therefore the bounded
  observation estimate; the ceiling, not 69, is what the every-label-combination-live bound
  (§7) uses.
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
  **complete 10-value** `InstanceStatus` enum · `liveness` ∈ 6 reachable + reserved `other` ·
  `outcome` ∈ 5 (wake-turn) · `event` ∈ 4 real + reserved `other` (lifecycle) (§8.1, §8.10).
  m8: lifecycle **beyond** point-in-time, including **revive-on-`send_message`** (the lost-wakes
  class).
- **Source:** **reuse** for status/liveness (the live census is already computed by
  `daemon/tools/instance_read.py:2019` `_build_capacity_summary`; `counts_by_status` and
  `counts_by_liveness` are the same two buckets) and for the wake event counters
  (`daemon/services/worker_pool.py:1381-1386` already tracks `empty_claim_attempts` and
  `claims_skipped_due_to_busy_instance`). **GREENFIELD for `ensemble_wake_turns_total` (m1)** —
  the Carrier-Threading contract (producer / carrier / threading / consumer) is specified in
  full in **§8.13**.
- **Phase** P1b · **Owner** developer (producer + carrier), reviewer (Carrier-Threading
  compliance), devops (LostWake alert) · **Series estimate** 10 + 7 + 1 + 1 + 5 + 5 = **29**.
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

  **R-m4 — streaming-only gap (state explicitly).** The hook at `graph.py:3776-3784` sits
  inside `_convert_delta_to_message_chunk` (def at `:3721`) = the **STREAMING path only**.
  Non-streaming completion paths carry usage elsewhere and are **not** captured by this hook:
  `current_llm.invoke(full_messages)` at `daemon/graph.py:9176-9179`, `:9236`, `:9643`,
  `:9672` return an `AIMessage` whose `.usage_metadata` is populated by the backend, but the
  streaming converter is never invoked there. Consequence: **under-count risk** for any call
  made with `streaming=False` (preserved verbatim at `:3889-3891`) — the tokens counter does
  not increment and `ensemble_llm_token_usage_missing_total` does. Either (a) add an observation
  at those four invoke sites (preferred — closes the gap) or (b) document the under-count as
  an accepted limitation. The F14 series estimate is unchanged (3); only the **coverage** note
  changes.
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
- **Phase** P1c · **Owner** developer · **Series estimate** 8 + 14 = **22** (8 outcome
  values + 14 histogram series: 12 buckets incl. the reserved `1800.0`/`3600.0` + `_count`/`_sum`;
  no extra reserved series beyond the bucket set).
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
  `category` ∈ the **runtime-derived tool categories (V-4 artifact: 55 in this worktree) +
  reserved `other`** (§8.6).
- **Source:** **reuse** — `MessageTapSlot.tap_node_return`
  (`daemon/services/message_tap.py:234`) is the **per-turn seam** (R17). m7: the tool-call and
  turn-performance families instrument **this seam**, NOT graph internals — graph internals would
  multiply measurement sites and re-derive per-turn state the tap already owns. The tool
  category is read from the tool's own registration metadata at the tap site, with an `other`
  fallback for any tool whose category is not in the runtime-derived set (V-4 `other`-fold).

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
for that source rather than shipped mislabelled. The metric increments sit in a **dedicated
inner `try/except` that swallows silently** — the tap's outer `try/except`
(`message_tap.py:308-309`) logs a WARNING, so putting increments inside it would emit per-turn
WARNING noise on a metric failure (NB-5).
- **Phase** P1c · **Owner** developer · **Series estimate** 5 + 14 + 56 = **75** (5 turn sources
  + 14 duration buckets + 56 tool categories: the V-4 artifact count (55 in this worktree) +
  reserved `other`).
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
  1 + 1 + 13 + 26 + 10 + 10 + 1 + 3 = **65**. (The §1 table cell and this decomposition now
  agree at 65 — the v5 "summary table rounds to 66" note is withdrawn, §11 C78.)
  **Histogram counting convention (NB-6, §11 C81):** the **13** is the
  `ensemble_journal_op_seconds` bucket series counted **once** — that histogram carries **no
  `op` label** (§8.11 marks it flat, unlike the two checkpoint histograms which are marked
  `/ op`). The convention is catalog-wide: a histogram's bucket series are counted **once**
  (the F7 "today" form), and the `/op` fully-wired form is stated separately where it applies
  (F7: 76 + 40 + 4 + 1 = 121). If the journal histogram were ever op-partitioned, the family
  ceiling would be 1 + 1 + (13 × 26 = 338) + 26 + 10 + 10 + 1 + 3 = **390**.
- **Degradation:** twin unreachable (no install dir, journal torn) ⇒ **no series**; a torn
  journal raises `JournalTorn` (`:251`) and the twin degrades to the last good copy.

---

## 6. P2 — Exporters and shell-origin series

### F22 — Host, PostgreSQL and shell-process metrics

- **Name:** `ensemble_process_cpu_seconds{name}` (gauge) + `ensemble_textfile_last_write_timestamp_seconds` (gauge) — shell-origin. Exporter-origin (not
  daemon-origin): node_exporter per-core CPU, per-device disk `await`/`util`, memory, load;
  postgres_exporter `pg_stat_activity` rollup, `pg_stat_statements` top-N, table sizes, WAL,
  autovacuum, `pg_stat_database`. **Type:** gauge; gauge; the rest are exporter-native.
  **Labels:** `name` ∈ `launcher`, `promote_sh`, `stage_sh` (the 3 processes the sampler actually
  samples — `rollback.sh`/`restart.sh` are NOT sampled and are not label values); the freshness
  gauge carries no labels.
- **Source:** **greenfield** — the node_exporter **textfile collector** pattern: an out-of-process **external sampler** (V-8 — bash scripts never write their own `*.prom`). Owner = node_exporter textfile-collector writer (devops); mechanism = per 10 s tick samples `ps -o %cpu=,comm=` (Darwin) / `/proc/<pid>/stat` (Linux) for the named processes (`promote.sh`, `stage.sh`, `launcher`) and writes `ensemble_process_cpu_seconds{name="…"}` (per-tick delta gauge) + `ensemble_textfile_last_write_timestamp_seconds` (freshness gauge); pinned 10 s cadence (alert math assumes it); spin→stale interaction: when the textfile goes stale (>120 s) while a spin is in progress, `TextfileStale` fires (see plan §8 V-8). The house shell scripts are **observed**, never modified (decision 2). No daemon dependency, crash-safe, and the only way to see the **bash** spins (the O(n²) `state.json` walk) that in-daemon metrics cannot. The canonical journal shape is `ensemble_journal_file_bytes` / `ensemble_journal_history_entries` (F21) — the textfile carries the same numbers so a daemon-down incident still has them.
- **Phase** P2 — the **minimal** textfile writer ships in **P1b** (A25: the BashSpin data
  source must exist before the P2 alert rules land); the full exporter/textfile work stays in
  P2 · **Owner** devops (exporters, textfile dir, dashboards), developer (the textfile
  writer **inside the twin** — never inside `scripts/upgrade/`) · **Series estimate** **4 shell-origin** (3 process-name series + 1 freshness gauge,
  counted separately from the daemon budget) + ≈ **67 exporter-origin** (§1 excludes both from
  the ≤2k daemon-origin budget).
- **Degradation:** node_exporter absent ⇒ its series are absent (daemon families unaffected).
  Textfile staleness is detectable by file mtime.

---

## 7. Series roll-up (the ≤2k proof)

| Phase | Families | Daemon-origin series |
|---|---|---|
| P0 | F0.1–F0.4 | 28 |
| P1a | F1–F6 | 63 |
| P1b | F7–F13 + F7b | 263 |
| P1c | F14–F21 | 226 |
| **Daemon total** | **F0.1–F21 + F7b** | **580** |
| Shell-origin (textfile) | F22 | 4 |
| Exporter-origin (node/postgres) | F22 | ≈67 |
| **Grand total on the Prometheus server** | | **≈651** |

**580 daemon-origin series against a ≤2 000 budget — ≈29 % consumed; headroom 1 420.** The four
largest families (F12 sweeps 69, F21 journal 65, F20 tool calls 75, F9 readiness 53) are each
**bounded, enumerated** label sets, so growth there is bounded by construction. The three
unbounded risks in v1 are explicitly closed: per-instance compaction occupancy is a max; the
leak site is a 6-value fixed map; the MCP roster is capped at 5 values. If every label
combination in the catalog were live at once the total would be **≈1 160** — still inside
budget. (*Provenance:* the v5 fully-wired bound was ≈1 118; it moves by exactly the same
per-family deltas that moved the daemon total, because each moved family's label structure is
unchanged by the move — **1 118 + 1 (F0.2) − 15 (F10) + 14 (F11) − 2 (F13) − 1 (F15) − 1
(F21) + 46 (F7b) = 1 160**. The v5 bound's own derivation was never written down, so the figure
is an **adjusted bound**, not a re-derived product — see §11 C79.)
**Delta trail (the basis of the 580):** see the §1 reconciliation table —
**538 (the retired pre-reconciliation baseline) + 1 (F0.2) − 15 (F10) + 14 (F11) − 2 (F13) − 1
(F15) − 1 (F21) + 46 (F7b) = 580** (F7 = 0 — the 22→21 decomposition was already inside the
retired baseline; F22 shell 5→4 is outside the daemon-origin budget).

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

### 7.1 Task / owner mapping (V-5 orphan sweep)

Every catalog §1 family maps to a plan §4 task row + owner role (back-reference). Post-sweep
orphan count = **0** (every series has a task row + owner). V-5 added dedicated task rows for
the previously orphaned clusters: F0.1 (P0 task 11) and F18 (P1c task 9); F0.2/F0.3/F0.4
were attached to existing tasks (P0 T4/T8/T6) with explicit metric names in their acceptance.

| Family | Phase | Plan §4 task row(s) | Owner role | Series |
|---|---|---|---|---|
| F0.1 | P0 | P0 T1, T2, T3, **T11** (V-5) | developer + devops | 16 |
| F0.2 | P0 | P0 T4 (extended) | developer (+ reviewer naming) | 3 |
| F0.3 | P0 | P0 T8 (extended) | developer | 8 |
| F0.4 | P0 | P0 T6 (extended) | developer (+ reviewer annex) | 1 — `ensemble_metrics_vocabulary_version` (**EMITTED**, named in the plan's P0 T6 acceptance) |
| F1 | P1a | P1a T1 | backend | 9 |
| F2 | P1a | P1a T2 (+ P0 T9) | backend | 22 |
| F3 | P1a | P1a T3 | backend | 6 |
| F4 | P1a | P1a T4 | backend | 8 |
| F5 | P1a | P1a T4 | backend | 3 |
| F6 | P1a | P1a T5 (+ P0 T7, T10) | backend + devops | 15 |
| F7 | P1b | P1b T1, T14 (V-6) | backend + devops (PG) | 21→31 |
| F7b | P1b | **the checkpoint read-path quick-wins lane** — `feature/checkpoint-quick-wins`, from §quick-wins + §9 of the locked checkpoint read-path fixes plan (shipped; **catalogued retroactively**, C95) | backend | 46 |
| F8 | P1b | P1b T2, T4 | backend + devops (PG) | 9 |
| F9 | P1b | P1b T3 | backend + devops | 53 |
| F10 | P1b | P1b T5 | backend + devops (PG) | 6 |
| F11 | P1b | P1b T7, T8, **T6** (P0-e — the claim-latency task moved with the family) | backend + devops (PG) | 30 |
| F12 | P1b | P1b T11 | backend + devops (PG) | 69 |
| F13 | P1b | P1b T9, T10, T12 | backend + devops (PG) | 29 |
| F14 | P1c | P1c T1 | backend | 3 |
| F15 | P1c | P1c T2 | backend | 22 |
| F16 | P1c | P1c T3 | backend | 5 |
| F17 | P1c | P1c T6 | backend + devops | 40 |
| F18 | P1c | P1c T7, **T9** (V-5) | backend | 9 |
| F19 | P1c | P1c T4 | backend | 7 |
| F20 | P1c | P1c T5 | backend | 75 |
| F21 | P1c | P1c T8 | backend + devops | 65 |
| F22 | P2 (minimal P1b) | P2 T1, T2, T3, T5 (+ P1b T13) | devops (+ developer writer) | 4 + ~67 |

**Orphan census (V-5):** found 33 series without a task row/owner (F0.1 16 + F18 9 + F0.3 counters 4 + F0.2 1 + F0.4 1 + F7 list/slow 2 — enumerated from the catalog, not trusted blindly); closed 33 by adding/extending task rows above; remaining **0**. Every catalog §1 series now has a task row + owner role.

**One family has no metrics-collection §4 task row — F7b.** It **SHIPPED** from the checkpoint
read-path quick-wins lane before this catalog existed, so its task/owner reference is the locked
checkpoint read-path fixes plan (the lane's own §quick-wins + §9), not a §4 task row. The
invariant is preserved by pointing at that plan, not by minting a retroactive task row.

## 8. Vocabulary annex

Every enumerated label set, with the `file:line` it was verified against.

**Rule (A1): every closed vocabulary in this annex is DERIVED FROM CODE, never hand-listed.** Hand-listing is the drift class that already falsified the tool-category set (§8.6) and the sweep lanes (§8.4: 7 → 13). Each set must be pinned to a runtime source (`get_tool_categories()`, an enum, a constant) and cross-checked by a test; a vocabulary that cannot be derived from code must carry a reserved `other` residual and a documented amendment procedure (bump `ensemble_metrics_vocabulary_version`).

**P-1 freeze rule (V-4 companion).** The P0 vocabulary freeze must NOT freeze tool-categories
from a hand list. It freezes from the V-4 generated artifact (§8.6), or defers the freeze
with the `other`-fold as the closure. **This rule is wired into plan §4 P0 T6** (the P0 T6
acceptance carries the freeze-from-artifact-or-`other`-fold closure); this annex defines the
rule it references.

**Cardinality rule (A10/A18):** per-instance attribution = PG-only (`/api/resources/*`); no
daemon-origin `/metrics` family carries a per-instance label — every daemon-origin family is
aggregate. (Companion: plan §3 storage-split boundary rule.)

**8.1 Instance + lifecycle vocabularies** — **Instance status (10)** —
`daemon/repositories/instance/models.py:20-32` (`InstanceStatus`): `idle`, `running`, `waiting`,
`paused`, `completed`, `error`, `terminated`, `queued`, `waiting_children`, `failed`.
**Instance liveness (6 reachable + reserved `other`)** — `daemon/services/work_status.py:67-99`
(`_STATUS_CANONICAL_MAP`): `pending`, `processing`, `paused`, `completed`, `failed`, `cancelled`;
`unknown`/garbage statuses fold to the reserved `other` residual. Live consumer
`daemon/tools/instance_read.py:228` `_liveness_for` (returns `unknown` for non-mapped input, which
folds to `other`). **Wake-turn outcome (5, closable set — V-3)**: `claimed`, `skipped_busy_instance`,
`empty_claim`, `timeout_wake`, `fresh_episode_reset`. Attribution: `claimed` /
`empty_claim` / `timeout_wake` / `skipped_busy_instance` ← the **claim CALL's** counters,
`daemon/services/worker_pool.py:1381-1386` (the `self._stats` **dict literal** — the four
`_stats` keys
`notifications_sent`/`empty_claim_attempts`/`workers_woken_by_timeout`/
`claims_skipped_due_to_busy_instance`); `fresh_episode_reset` ← episode seam
`daemon/graph.py:7319` `_detect_fresh_episode_reset` returning a non-`None` sentinel id.
A `None` return from the seam yields **no increment** (absent ≠ zero) — there is no
`no_op_resume`/`stale_episode` producer in code, so both are cut (no aspirational values).
**Instance lifecycle event (4 real + reserved `other`)**: derived from the real publisher
`daemon/services/event_publisher.py:38-80` `_publish_instance_lifecycle_event(instance_id, status,
error, parent_id, result_summary)`, whose `status` axis carries only terminal values:
`completed` (error_reporting.py:827, child_reports.py:5930/:6118), `failed`
(child_reports.py:5469), `terminated` (instance_lifecycle.py:3096), `error` (job_feedback_observer
terminal path, InstanceStatus.ERROR). The two retired `fan_in_*` phantom values exist NOWHERE under
`daemon/` (grep = 0 hits) and are removed; `spawned`/`revived`/`paused`/`resumed` are not published
by this publisher and fold to reserved `other`. Amendment procedure: adding a new published status
requires (a) a producer emitting it, (b) a bump of `ensemble_metrics_vocabulary_version`, and
(c) a catalog §11 correction entry.

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
`queue_freshness`, `services`, `checkpoint_saver` (`:94-99`). **Cross-check (V-2):** the
catalog readiness outcome vocabulary matches the `readiness.py` taxonomy exactly (11 classes) —
no drift. **Wedge-capture reason (4)** — `daemon/services/wedge_capture/triggers.py:54-64`
(`CANONICAL_REASONS`, `:57` `REASON_MANUAL = "manual"`): `sustained_cpu`,
`sustained_loop_lag`, `thread_concentration`, `manual`. (R-m9: `manual` was missing from the
v1 set; the path is `daemon/services/wedge_capture/triggers.py`, not `daemon/services/triggers.py`.)
**Wedge write outcome (2)**: `ok`, `failed`. **Exposition outcome (2)**:
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

**8.6 Tool category (V-4 — generated artifact, not a hand list).** The tool-category
vocabulary is **process-dependent and unstable** (recounts give 41/53/54/55 depending on
process: MCP name-prefix injection and `scan_tools_for_full_docs` process-dependence — the
instability IS the defect). It is therefore NOT hand-listed and NOT frozen from prose.

- **Canonical source (V-4):** a **CI/test-GENERATED JSON artifact** produced by a generator
  test (new test, e.g. `tests/unit/tools/test_tool_category_artifact.py`) that imports the full
  tool registry, calls `get_tool_categories()` (`daemon/tools/_tool_registry.py:1598`) over the
  complete tool universe (all agents + MCP-injected names), and writes the sorted distinct
  category list + count to a committed artifact, e.g.
  `.agents/shared/metrics/tool-categories.json` (or `tests/fixtures/tool-categories.json`).
  The artifact is canonical; the prose number is not.
- **Drift check:** a test that re-runs the generator and fails (non-zero exit) when the
  on-disk artifact diverges from the freshly generated set — so a new `register_tool_category`
  site or a renamed `CATEGORY_NAME` breaks CI instead of silently drifting the metric label set.
- **`other`-fold rule:** any tool whose category is not in the runtime-derived set (or not
  resolvable at runtime — e.g. MCP-injected tools whose category cannot be resolved) folds to
  the reserved `other` bucket and is counted in
  `ensemble_metrics_invalid_label_total{metric,label}`. The `other` bucket is always present, so
  the cardinality is bounded by (derived count + 1) regardless of process.
- **Freeze rule (P-1):** the P0 vocabulary freeze must NOT freeze tool-categories from a hand
  list. It either (a) freezes from the V-4 generated artifact (the artifact's sorted list
  becomes the frozen label set for that release) or (b) defers the freeze and ships with the
  `other`-fold as the closure (a bump of `ensemble_metrics_vocabulary_version` is required to
  change the frozen set — see F0.4). **This rule is wired into plan §4 P0 T6** (the P0 T6
  acceptance carries it); this annex defines the rule it references.
- **My own count in this worktree (freeze-time placeholder, NOT canonical): 55** distinct
  `CATEGORY_NAME` values from the 55 `CATEGORY_MODULES` keys (all importable, no missing
  modules). The artifact is canonical; 55 is only the value I observe today. Series math uses
  55 + reserved `other` = 56 tool-category label values (F20).
- The hand-listed v1 set (37/38 values) is **falsified**: it omits at least `board-link`,
  `dynamic-skill`, `ens-db`, `ens-env`, `image-gen`, `self-alarm`, `skill-evolution`,
  `system-log`, `view-views`, `view-write`, `wedge-capture`. The full closed list is
  intentionally NOT hand-written here — deriving it at runtime (and pinning via the artifact)
  is the contract.

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
(`daemon/checkpoint_metrics.py:1011`); **checkpoint READ op (2): `aget`, `aget_tuple`** (the
read-side op set — the two saver retry-proxy forwarders that are instrumented,
`daemon/checkpoint_adapter.py:607` and `:614`; the registry's `allowed_values` for the read
family is `daemon/checkpoint_metrics.py:1117`); **checkpoint read-size bucket label (9)**:
`65536`, `131072`, `262144`, `524288`, `1048576`, `2097152`, `4194304`, `8388608`, `+Inf` — the
frozen set `daemon/checkpoint_metrics.py:1109` (`_READ_SIZE_BUCKET_LABELS`, derived from
`READ_SIZE_BUCKETS` at `:1100`); every member is **integral**, so the label renders as a plain
integer and is the SAME string as the size histogram's `le=` label for the same bound; checkpoint table (3): `checkpoints`,
`checkpoint_writes`, `checkpoint_blobs` (`daemon/services/maintenance.py:531-536`); checkpoint
prune outcome (4): `dry_run`, `executed`, `skipped_non_postgres`, `error`; spine table (4):
`resource_daemon_bucket`, `resource_instance_sample`, `resource_pid_map`,
`resource_limit_policy`.

**8.10 Job / LLM / compaction / turn / gateway vocabularies** — admission state (4): `queued`,
`active`, `done`, `dead` (`daemon/repositories/job_queue/models.py:40-43`); worker pool name (2):
`worker`, `chat` (`daemon/services/pool_orchestrator.py:565` — the default worker pool — and
`:643` — the chat pool, `lane="chat"`); worker pool event (4): `notifications_sent`,
`empty_claim_attempts`, `workers_woken_by_timeout`, `claims_skipped_due_to_busy_instance`
(`daemon/services/worker_pool.py:1381-1386`); LLM outcome (8): §8.12; LLM token direction (2):
`input`, `output` (`daemon/graph.py:3771-3812`); stream-stall phase (3): `abort_attempt`,
`aborted`, `recovered` (`daemon/services/llm_stream_watchdog.py:456,497`); stream-watchdog sweep
outcome (2): `none`, `claimed` (`daemon/services/llm_stream_watchdog.py:551-565` — `sweep_once`
returns the CLAIMED count, `:561` init / `:564` increment; `none` = a pass that claimed 0);
compaction rung (3): `l1_pre_dispatch_80`, `l2_precall_95`,
`l3_backstop_600k` (`daemon/compaction.py:1128`); compaction stage (3): `pre_dispatch`,
`precall_95`, `post_compaction` (`daemon/compaction.py:1589` — the 0.80 pre-dispatch gate
threshold; `daemon/graph.py:7497` — `PRECALL_COMPACTION_RATIO = 0.95`, the pre-call stage;
`daemon/compaction.py:305` — the `post_compaction_channel` node-return prefix); turn source (5):
`user_message_entry`, `agent_node_return`,
`compaction_aupdate_reactive`, `compaction_aupdate_messaging`, `compaction_precall_95`
(`daemon/services/message_tap.py:120-124`); gateway session outcome (5): `begin`, `end`,
`expired`, `superseded`, `refused` (`daemon/services/mcps_gateway/sessions.py:135` — `begin`,
`:208` — `end`, `:239` — `expire_due`; `daemon/services/mcps_gateway/tombstones.py:90` —
`REASON_EXPIRED = "expired"`, `:120` — `REASON_SUPERSEDED = "superseded"`; `refused` is the
machine-busy refusal, `daemon/services/mcps_gateway/sessions.py:118-133` —
`assert_machine_free` raising `CODE_MACHINE_BUSY`); gateway audit outcome (2): `ok`, `error`
(`daemon/services/mcps_gateway/audit.py:53-54` — `OUTCOME_OK`, `OUTCOME_ERROR`; the audit
table's wider outcome constants at `:55-56` — `denied`, `timeout` — are **not** part of the
F18 label set and fold to the reserved `other` residual under the §0.5 observe-time rule, so
the family's series count is unchanged).

**8.11 Histogram bucket sets (the eight histograms).** Each is stated once; `_count`/`_sum` are
implied.

| Histogram | Buckets (seconds or bytes) | Series |
|---|---|---|
| `ensemble_checkpoint_op_seconds` | `0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0` (**top ≥120 s** — CR-3; the existing `daemon/checkpoint_metrics.py:356-359` set stops at 10.0 and is **extended, not replaced**) | 17 + 2 = 19 / op |
| `ensemble_checkpoint_op_bytes` | `1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216` | 8 + 2 = 10 / op |
| `ensemble_job_claim_latency_seconds` | `0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0` | 12 + 2 = 14 |
| `ensemble_llm_request_seconds` | `0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0` (plus `1800.0`, `3600.0` reserved for slow models) | 12 + 2 = 14 |
| `ensemble_turn_duration_seconds` | `0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0` | 12 + 2 = 14 |
| `ensemble_journal_op_seconds` | `0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0` | 11 + 2 = 13 |
| `ensemble_metrics_exposition_seconds` | `0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0` | 11 + 2 = 13 |
| `ensemble_checkpoint_read_size_bytes` | `65536, 131072, 262144, 524288, 1048576, 2097152, 4194304, 8388608` (bytes; **every bound is integral**, so the `le=` label renders as a plain integer and is the SAME string as the `bucket=` label of `ensemble_checkpoint_read_size_bucket_hits_total` — the join-by-label surface) | 8 + 2 = 10 / op |

`DEFAULT_LATENCY_BUCKETS` is **not** reused for the LLM or claim-latency histograms — those
ranges are seconds-to-minutes, not milliseconds. The checkpoint read-size set is a **bytes** set
(log2 from 64 KiB to 8 MiB), not a seconds set; it is **not** derived from
`DEFAULT_LATENCY_BUCKETS` and is never mixed with the latency families' buckets.

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
the producer is **split** (A3) across two sites:

- **Producer (a)** — the **claim CALL** `daemon/services/worker_pool.py:302`
  (`task = self._task_processor.claim_task(self.worker_id, lane=self._lane)`), yields four closable
  outcomes: `claimed` (from `notifications_sent`), `empty_claim` (`empty_claim_attempts`),
  `timeout_wake` (`workers_woken_by_timeout`), `skipped_busy_instance`
  (`claims_skipped_due_to_busy_instance`). Those four raw counters live in the `self._stats`
  **dict literal** at `daemon/services/worker_pool.py:1381-1386` — that literal is the source of the
  outcome **vocabulary** (the stat keys), **not** the claim path itself (anchor corrected 2026-10-29,
  B-10). The two raw counters `notifications_sent` and `workers_woken_by_timeout` are stat keys, not
  separate outcome values. **Canonical carrier producer for the fresh-episode flag (blueprint
  Carrier-Threading Pattern):** the flag is computed **once** at
  `daemon/services/task_processor.py:569-576` (re-derived from the persisted MessageQueue row) and
  carried on `ProcessingContext` at `daemon/services/task_processor.py:598`; ledger identity
  `daemon/services/instance_messaging.py:2611-2613`; carrier field
  `daemon/services/message_processing_pipeline.py:176`.
- **Producer (b)** — the **episode seam** `daemon/graph.py:7319` `_detect_fresh_episode_reset`
  yields `fresh_episode_reset` ONLY when it returns a non-`None` sentinel id. A `None` return
  yields **no increment** (absent ≠ zero) — there is no `stale_episode`/`no_op_resume` producer
  in code (the function returns `str | None`, never those strings), so both are cut (no
  aspirational values). (B-06 reconciliation.)
- Each producer is computed **once**, each carried. Neither re-derives from partial signals.
- **Carrier** — a new keyword-only field on `ProcessingContext`
  (`daemon/services/message_processing_pipeline.py:120-121` — the `@dataclass` decorator at
  `:120`, the **class definition** at `:121`; the existing carrier field
  `is_fresh_episode_user_message` sits at `:176`), defaulting to `None`. (Both numbers are now
  given together wherever the carrier is cited, so the class anchor and the field anchor can
  never be confused — §11 C82.)
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

**9.4 Relation to the existing leak oracle.** `daemon/services/readiness.py:784`
`_evaluate_leak` arms the `pool_leak` readiness class from **pool counters** (the P2 psycopg
pool's `requests_waiting` and `pool_available` decay), not from `pg_stat_activity`. The census
and the leak oracle are **complementary, not duplicate**: the oracle says "the pool is
misbehaving"; the census says "these N backends have been idle-in-transaction for M seconds, at
these sites". The oracle feeds the readiness composite; the census feeds only `/metrics`.

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
| F7 `ensemble_checkpoint_*`, F7b `ensemble_checkpoint_read_*` | **No** — in-RAM registry | resets on restart |
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
  `rate(ensemble_readiness_probe_total{outcome=~"probe_timeout|read_timeout"}[5m]) > 0.1` **and**
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
| C27 | tool-category vocabulary = 37/38 (§8.6) | **falsified** — the v1 hand-count (later stated as "49 distinct across 254 sites") was never a code-derived count and is **withdrawn**: the tree's `daemon/tools/_tool_registry.py` `CATEGORY_MODULES` carries **55** keys, and §8.6's freeze-time value is **55 + reserved `other` = 56**. The **V-4 generated artifact is canonical** (CI/test-generated JSON + drift check); 55 is the freeze-time observation in this worktree, not a hand list | `daemon/tools/_tool_registry.py:1598` (`CATEGORY_MODULES` = 55 keys) |
| C28 | F14 observation site = `graph.py:3811-3812` | **wrong hook** — `:3811-3812` is the non-terminal, per-chunk path; terminal usage chunk returns early at `:3776-3784`; fix: hook terminal branch (or `:9176-9179`) + per-call dedupe | `daemon/graph.py:3776-3784` |
| C29 | `ensemble_wake_turns_total` consumer = exposition renderer reads carrier | **unimplementable** — carrier dies with the message; split producer into the **claim call** (`worker_pool.py:302`) + episode seam (`graph.py:7319`); a live turn-end consumer increments, renderer reads accumulated counter. **Anchor corrected (B-10, 2026-10-29; NB-13/NB-14, 2026-10-29):** `worker_pool.py:1381-1386` is the `self._stats` **dict literal** — the source of **four** of the five outcome values (the fifth, `fresh_episode_reset`, comes from the episode seam), **not** the claim path; the **claim CALL** is `worker_pool.py:302`; the canonical carrier producer is `task_processor.py:569-598` (carrier field `message_processing_pipeline.py:176`; the `ProcessingContext` class is at `message_processing_pipeline.py:120-121` — decorator `:120`, class `:121`) | `daemon/services/message_processing_pipeline.py:120-121`, `:176`; `daemon/services/worker_pool.py:302`, `:1381-1386`; `daemon/services/task_processor.py:569-598` |
| C30 | "unwired registry" / "zero non-test callers" | **corrected** — registry is wired to collectors (`checkpoint_perf.py:28-30` imports, observes `:190`/`:198`, increments `:233`); only `render_metrics()` is unwired in production | `daemon/checkpoint_perf.py:28-30` |
| C31 | F20 counting unit unspecified | **defined** — one `tap_node_return` invocation (5 sites, N+1 per turn); turn-duration needs an anchor pair or drop (tap measures its own DB-write latency) | `daemon/services/message_tap.py:234`, `:302-306` |
| C32 | `ensemble_checkpoint_op_seconds{op="aput"}` declared | **cannot fire** — write path bypasses `time_saver_op`; instrument the `checkpoint_adapter.py` charge seam (`:368` = the `aget` charge; `:371-374` = the `aget_tuple` charge → `aput`/`aput_writes` `:377-379`) or narrow enum | `daemon/checkpoint_adapter.py:377-379` |
| C33 | `ensemble_metrics_registry_measurements` ambiguity | **clarified** — counts measurements, not series | `daemon/checkpoint_metrics.py:304` |
| C34 | census statement/connect timeouts unspecified | **set** — 5 s / 3 s; execution home = dedicated daemon thread; self-exclusion = census only; blank `pg_stat_activity.query` lands in `other` | `daemon/services/wedge_capture/db_capture.py` |
| C35 | the resource tier's darkness was caused by the flusher (`bool`→`INTEGER` 42804 every window, all four tables rolled back through the shared `engine.begin()`; runtime fix pending at `flusher.py:770` / `manager.py:7341`) | **REFUTED (2026-10-29, WS-A correction round).** Prod facts: the `estimated` column is `boolean NOT NULL` (`daemon/models/resource_monitor.py:231-233`); the bind is correct (bind-audit test @ `feature/resource-monitor-flusher-fix@53547b915` checks every flusher column against live `information_schema` + round-trips a real bool); the rollback amplifier was already fixed (`7be1b40bf` per-tier SAVEPOINT, `69cea24ea` `flush.errors` surfacing). **Real cause — a dead PRODUCER on macOS:** the prod daemon is a macOS binary; Darwin has no `/proc` and no `/sys/fs/cgroup`, so `proc_readers` returned `None` every tick → the sampler skipped every tick → the flusher had nothing to write. Live signature: samples climbing, `flush {batches:1139, bytes:0, errors:0}` = "produced nothing", NOT "write failed"; `resource_pid_map` climbed (45,575 rows at commit time) from the spawn path, **masking the outage**. Fix = the WS-A branch: Darwin ps-based reader (engaged ONLY when the proc root is ABSENT and the host is Darwin) + corrected sampler counters (`missed_samples`/`ticks_skipped` parity, skips excluded from `counters.ticks`, one-shot WARNING) + 7 unit / 6 real-PG tests. Merges to latest BEFORE P0 executes. Platform consequences: catalog §13; per-child T3 stays `/proc`-only (§13.4 non-goal) | `feature/resource-monitor-flusher-fix@53547b915` (`proc_readers.py`, `sampler.py`, `tests/postgres/test_resource_monitor_flusher_bind_pg.py`); prod census 2026-10-29 |
| C36 | alert rules published in markdown tables with `\|` escapes → YAML inert for copy-paste | **fixed (R-m1):** authoritative rule text now lives in fenced ```yaml blocks with unescaped pipes; table retained for incident mapping + baselines | plan-overview.md §8 |
| C37 | multi-vector rules used bare `and` between disjoint label sets → empty vector → never fires (LoopStarved, FalseDegraded, CheckpointSlow) | **fixed (V-1):** explicit `and on()` label guards; evaluation fixtures added (AC8) | plan-overview.md §8 + §10 AC8 |
| C38 | FalseDegraded selected `outcome="timeout"` which does not exist in the 11-class taxonomy | **fixed (V-2):** `outcome=~"probe_timeout\|read_timeout"`; both grace-eligible; probe_timeout replays the P2 deadline miss | `daemon/services/readiness.py:64-79` |
| C39 | `ensemble_wake_turns_total{outcome}` claimed 7 values; producers emit only 5 closable values | **fixed (V-3):** cut to `claimed`, `skipped_busy_instance`, `empty_claim`, `timeout_wake`, `fresh_episode_reset`; None yields no increment | `daemon/services/worker_pool.py:1381-1386`, `daemon/graph.py:7319` |
| C40 | tool-category count hand-listed as 49 (unstable 41/53/54/55 by process) | **fixed (V-4):** CI/test-generated JSON artifact is canonical + drift check + `other`-fold; freeze-time value in this worktree = 55 | `daemon/tools/_tool_registry.py:1598` |
| C41 | F3 `reason` vocabulary omitted `manual` (`REASON_MANUAL` at triggers.py:57) | **fixed (R-m9):** 4 values: `sustained_cpu`, `sustained_loop_lag`, `thread_concentration`, `manual` | `daemon/services/wedge_capture/triggers.py:54-64` |
| C42 | F14 hook documented only at streaming path; non-streaming completion paths carry usage elsewhere | **documented (R-m4):** streaming-only gap stated; invoke sites `graph.py:9176-9179`, `:9236`, `:9643`, `:9672` named | `daemon/graph.py:3721`, `:3776-3784` |
| C43 | BashSpin spinning script cannot write its own `*.prom`; `\|` escapes inert | **fixed (V-8/R-m1):** external sampler named (owner + mechanism + 10 s cadence); TextfileStale rule added; pipes unescaped | plan-overview.md §8 |
| C44 | alert thresholds uncited | **fixed (V-7):** Baseline column cites code constant or incident/bench evidence for all 14 rules | plan-overview.md §8 |
| C45 | AC2 self-cost dark on macOS; AC6 lag-probe dark when wedge-capture off | **fixed (V-9):** explicit precondition rows on every dark-able AC | plan-overview.md §10 |
| C46 | P0 vocabulary freeze from hand list | **fixed (P-1):** freeze from V-4 artifact or defer with `other`-fold | catalog §8.6 + plan §4 P0 T6 |
| C47 | 33 orphaned series with no task row/owner (V-5) | **fixed (V-5):** dedicated task rows added for F0.1 (P0 T11) and F18 (P1c T9); F0.2/F0.3/F0.4 attached to existing tasks with explicit metric names; mapping annex added at catalog §7.1; orphan count now 0. **P0-b (2026-10-29):** F0.4's single series `ensemble_metrics_vocabulary_version` is now named as an **EMITTED** series in the plan's P0 T6 acceptance (it was only implied by "version-label policy") | plan-overview.md §4 P0 T6 + catalog §7.1 |
| C48 | P0 family claim "none new — P0 builds the plumbing" contradicts F0.1–F0.4 shipping in P0 (V-5) | **fixed (V-5):** claim restated as "F0.1–F0.4 foundation families" (G-20 canonical partition) | plan-overview.md §4 P0 |
| C49 | Checkpoint write path reused the read charge seam, billing writes to READ budget (V-6) | **fixed (V-6):** distinct `_charge_checkpoint_write` hook with own budget/labels; None on write = error not skip; Q1 risk row kept | plan-overview.md §4 P1b T14 + catalog F7 |
| C50 | Teardown used a **bounded blocking join** (a `join` call with a timeout argument) instead of the house detached-join precedent (R-m2) | **fixed (R-m2):** aligned to detached join (`daemon/api.py:3411-3433`, `file_change_monitor.py:180-196`); `daemon=True` thread dies with process | plan-overview.md §3 + §4 P0 T1 |
| C51 | Hard cardinality cap not atomic; no rejected counter (R-m3) | **fixed (R-m3):** observe-time rejection atomic under the metric's own lock (check-then-increment, no TOCTOU); `ensemble_metrics_cap_rejected_total` folded into F0.2 (+1 series) | catalog §0.5 + plan §5/§7 |
| C52 | Publisher copy-on-publish O(series) per increment unbounded (R-m5) | **fixed (R-m5):** dirty-flag + re-publish at most once per N seconds, or publish-on-scrape-request with bounded staleness | catalog §0.2 |
| C53 | Plan §12 closing sentence claimed no open questions while OQ-1/2/3 exist (R-m7) | **fixed (R-m7):** sentence restated; OQ-1/2/3 remain open (decided in P0), OQ-4 RESOLVED | plan-overview.md §12 |
| C54 | F7 decomposition did not reconcile (21→31 vs declared 22→33) (R-m8) | **fixed (R-m8):** corrected to 21 today / 31 post (19+0+1+1 / 19+10+1+1); §1 F7 row + §7 P1b (221→220) + reconciliation restated | catalog §1 + §4 + §7 |
| C55 | Plan §6 dark matrix omitted F3 (wedge-capture /proc-dependent) (R-m10) | **fixed (R-m10):** F3 row added consistent with catalog §13 | plan-overview.md §6 |
| C56 | Plan §4 P0 T6 freeze rule not wired to V-4 artifact (P-1) | **fixed (P-1):** freeze from V-4 generated artifact or defer with `other`-fold; never hand list | plan-overview.md §4 P0 T6 |
| C57 | P0-core gate lacked detached-join / atomic-cap / publisher-cadence line items (P-3) | **fixed (P-3):** three mechanics items added as explicit APPLIED line items with anchors | plan-overview.md §4 P0 exit criteria |
| C58 | V-8 external sampler not referenced in plan §3/§4 P2 or catalog F22 Source | **fixed (V-8):** owner + mechanism + 10 s cadence + stale-file rule referenced in all three | plan-overview.md §3, §4 P2 T1; catalog F22 |
| C59 | `ensemble_textfile_last_write_timestamp_seconds` shipped by TextfileStale rule but uncatalogued (B-02(b)) | **catalogued in F22:** freshness gauge added to F22 name list + §1/§7 proof; shell-origin 5→4 (3 process names + 1 freshness) | catalog F22, §1, §7, §14.3 |
| C60 | Catalog §10.2 FalseDegraded used `outcome="timeout"` (zero series under 11-class taxonomy) (B-02(c)) | **fixed (V-2 alignment):** `outcome=~"probe_timeout\|read_timeout"` | catalog §10.2 |
| C61 | Wake vocabulary three-way contradictory: 7 values (line 638) / 33 series (line 649) / 5 values (§8.1) / producers §8.1 rules out (§8.13) / 31 (lines 195,926) (B-06) | **reconciled to 5 closable values:** `claimed`, `skipped_busy_instance`, `empty_claim`, `timeout_wake`, `fresh_episode_reset`; F13 33/31→29; §8.13 producer (b) yields `fresh_episode_reset` only | catalog F13, §8.1, §8.13, §14.3/14.4 |
| C62 | Lifecycle-event vocabulary unanchored hand-list of 7 incl. the two `fan_in_*` phantom values (0 grep hits) (B-07) | **derived from publisher:** `completed`, `failed`, `terminated`, `error` + reserved `other`; amendment procedure added | event_publisher.py:38-80, catalog F13, §8.1 |
| C63 | F10 mixed `job_queue_items` depth with task-queue claim latency; cited `message_queue/models.py:83 enqueued_at` (wrong table) (B-08) | **re-scoped:** F10 = job-queue only (6 series); claim latency (14) moved to F11, sourced from `task.created_at`/`task.started_at` (task/models.py:273-274) | catalog F10, F11, §14.3 |
| C64 | `ensemble_metrics_invalid_label_total{metric,label}` uncatalogued + `{label}` unbounded (B-09) | **catalogued in F0.2:** `{metric}` = family name (closed **25-set** — 25 daemon-origin families, §14.1; the "22" was the pre-F22 count), `{label}` = label-key name (closed enum) + reserved `other`; F0.2 2→3 | catalog F0.2, §0.5, §14.3 |
| C65 | "≤2k proof" self-contradicted: 538 vs 535; F10 20 vs 21, F15 22 vs 23, F21 65 vs 66 (B-13) | **reconciled to 534:** P0 28 + P1a 63 + P1b 217 + P1c 226; F15 22, F21 65, F10 6, F11 30, F13 29, F0.2 3, F22 4 shell | catalog §1, §7, §14 |
| C66 | NB-5: tap increments must not sit in outer try/except (message_tap.py:308 is the `except` branch) | **specified:** dedicated inner try/except that swallows silently | catalog §0.2, F20 |
| C67 | NB-7: `limits.metrics_census_interval_s` lacked upper bound | **added** `le=300` | plan-overview.md line 294 |
| C68 | NB-8: F22 declared 5 `name` values but samples 3 | **aligned to 3:** `launcher`, `promote_sh`, `stage_sh` | catalog F22, **§8.9** (the pool / PG / checkpoint / spine vocabulary block — the process-name set lives there; **not** §8.5), §14.4 |
| C69 | NB-10: §8.1 liveness set stated 7 reachable; actually 6 | **corrected to 6 reachable + reserved `other`** | catalog §8.1, §14.4 |
| C70 | NB-11: kill-switch-off empty body vs `ensemble_metrics_up = 0` contradiction | **one semantic:** empty body when off; `ensemble_metrics_up` present only when live | catalog §0.8, F0.1 |
| C71 | NB-12: §9 subsection order 9.1,9.2,9.3,9.5,9.6,9.4 | **reordered to 9.1,9.2,9.3,9.4,9.5,9.6** | catalog §9 |
| C72 | NB-11/NB-5 etc. also reflected in §14 reconciled ledger (single source of truth) | **added §14** | catalog §14 |
| C73 | Wake producer anchor mis-labelled: `worker_pool.py:1381-1386` called the **claim path** (B-10) | **corrected:** `:1381-1386` is the `self._stats` **dict literal** (the outcome *vocabulary* source); the **claim call** is `worker_pool.py:302` (`claim_task`); the canonical **carrier producer** is `task_processor.py:569-598` (carrier field `message_processing_pipeline.py:176`, ledger identity `instance_messaging.py:2611-2613`) | catalog §8.13, §11 C29; `daemon/services/worker_pool.py:302`, `daemon/services/task_processor.py:569-598` |
| C74 | Kill-switch §0.8 said "everything else … is OFF (fail closed)", which reads as if **unset ⇒ OFF** — contradicting the same paragraph's "env UNSET defers to the managed-key chain" (NB-6) | **one semantic, three cases:** (i) env explicitly set (incl. set-but-empty) ⇒ ON only for `{1,true,yes}`, every other explicit value ⇒ OFF (an invalid explicit value fails closed); (ii) explicit OFF ⇒ OFF and wins over the managed-key chain; (iii) env UNSET ⇒ the managed-key chain decides. "Fail closed" describes **only** an invalid *explicit* value | catalog §0.8; plan-overview.md §4 P0 T3 + §5 |
| C75 | §0.2 carried a **dangling paragraph fragment** — the head of the A27 "Freeze rules" paragraph ("**Freeze rules (A27).** Import the listener on the **lifespan path** (not `daemon/api.py`") was missing, leaving an orphaned "module top level) so the PyInstaller analyzer pulls it." | **repaired:** the full A27 paragraph restored, matching plan §3 "Freeze rules (A27)" verbatim | catalog §0.2; plan-overview.md §3 |
| C76 | **The delta trail did not sum to the certified total** (R-4 / B-13a): three items were mis-stated (F0.2's cap-rejected counter was double-counted; F10 21→6 = −15 with F11 +14 is a net −1, not "net 0"; F13 31→29 = −2, not −4 — the −4 mixed v4's *detail* baseline 33 with the 538 *summary-table* baseline), and F7's 22→21 was already inside the 538 baseline | **fixed:** the trail is restated as a table with an **explicit per-item baseline** (the §1 summary-table cell — the one that summed to 538) and sums on one line in **both** places (§1 and §7) — the trail itself is stated in those two places and is **not** restated here: `§1 → *Reconciliation basis*` + `§7 → *Delta trail (the basis of …)*` (F7 = 0; F22 shell 5→4 sits outside the daemon-origin budget) | catalog §1, §7 |
| C77 | `ensemble_metrics_invalid_label_total{metric}` bound to a "closed 22-family set" while the catalog carries **26** families (P0-a) | **fixed:** 26 in both places (F0.2's series-estimate text and this row); the `{metric}` description now names the same closed 26-family set §14.1 quotes | catalog F0.2, §11 C64, §14.1 |
| C78 | F21 carried "(the summary table rounds to 66)" while §1 had moved to 65 (P0-d) | **fixed:** the parenthetical is withdrawn; §1's F21 cell and the family decomposition now agree at 65 | catalog F21, §1 |
| C79 | "≈1 118" (the every-label-combination-live bound) was carried forward unchanged after five families moved (NB-4) | **fixed:** recomputed to **≈1 114**, with the provenance stated in §7 — the v5 bound ≈1 118 adjusted by the same five per-family deltas (the moved families' label structures are unchanged by the move). The v5 bound's own derivation was never written down, so the figure is an **adjusted bound**, not a re-derived product; §11 C80 records the family-level derivation rule | catalog §7, §14.1 |
| C80 | F12's "≈30 live (lane, outcome) combinations" was a **rule-less rounding** that propagated as a hard 69 (NB-5) | **fixed:** the derivation is now explicit — three lane-scoped series counted one per lane (13 + 13 + 13 = 39) plus the rows counter at its **observation-bounded** ≈30 (an OBSERVED figure: each lane emits 2–3 of the 6 outcomes), with the family **ceiling** stated separately as the full `lane` × `outcome` product 13 × 6 = 78 ⇒ ceiling 39 + 78 = **117** | catalog F12, §7 |
| C81 | The journal-op series convention was ambiguous (NB-6): F21 counted `ensemble_journal_op_seconds` as 13 with no `/op` multiplier, while the checkpoint family states its `/op` fully-wired form (F7: 121) | **fixed:** the convention is stated catalog-wide — a histogram's bucket series are counted **once** (the F7 "today" form); the `/op` fully-wired form is stated separately where it applies. §8.11 now marks the two checkpoint histograms `/ op` and the journal histogram **flat** (no `op` label), so F21's 13 is unambiguous; F21's op-partitioned ceiling (if ever applied) is 390 | catalog §8.11, F21, F7 |
| C82 | `ProcessingContext` was cited at `message_processing_pipeline.py:121` without stating that the decorator sits at `:120` and the carrier field at `:176` (NB-7) | **fixed:** the class anchor is now given as `:120-121` (decorator + class definition) with the field at `:176` wherever the carrier is cited — the two numbers can no longer be confused | catalog §8.13 |
| C83 | C68's editorial marker (an annex-subsection pointer carrying a query mark) was left unresolved (NB-8) | **fixed:** the F22 process-name vocabulary lives in **§8.9** (the pool / PG / checkpoint / spine block) — **not** §8.5; the marker is resolved | catalog §11 C68 |
| C84 | §14.3 did not flag F7 as changed although the section heading promises changed families are flagged (NB-9) | **fixed:** F7's row carries an explicit **CHANGED** marker (today 21 / post byte-wiring 31, R-m8) | catalog §14.3 |
| C85 | §14.4's wake-turn row pointed at the placeholder "catalog §11 C-corr", naming no correction (NB-10) | **fixed:** the real correction ids are named — **C29** (the consumer/producer split), **C39** (the 5-value closable wake set), **C73** (the `worker_pool.py:1381-1386` anchor correction) | catalog §14.4 |
| C86 | C27 asserted "verified 49 distinct across 254 sites" — the tree's `_tool_registry.py` `CATEGORY_MODULES` carries **55** keys, so the correction row itself was the stale claim (NB-11) | **fixed:** the row records the **V-4 generated artifact as canonical**, the freeze-time value **55 + reserved `other` = 56**, and explicitly withdraws the v1 hand-count (the "49 across 254 sites" figure was never code-derived) | catalog §11 C27, §8.6 |
| C87 | §14.4 listed "F1 turn source" where the family is **F20** (NB-2) | **fixed:** renamed to F20; the entry keeps its §8.10 anchor | catalog §14.4 |
| C88 | §8.10 carried **five** label sets with no `file:line` anchor, violating §0.5's "every set is anchored" rule (P0-c) | **fixed:** worker pool name → `pool_orchestrator.py:565` + `:643`; stream-watchdog sweep outcome → `llm_stream_watchdog.py:551-565`; compaction stage → `compaction.py:1589` + `graph.py:7497` + `compaction.py:305`; gateway session outcome → `mcps_gateway/sessions.py:135`/`:208`/`:239` + `mcps_gateway/tombstones.py:90`/`:120`; gateway audit outcome → `mcps_gateway/audit.py:53-54` (the wider audit constants at `:55-56` are outside the F18 label set and fold to `other`, so the series count is unchanged) | catalog §8.10, §0.5 |
| C89 | F0.4's producer was assigned to P0 T6 in the catalog annex, but the plan's P0 T6 acceptance never named `ensemble_metrics_vocabulary_version` as an **EMITTED** series (P0-b) | **fixed:** the plan's P0 T6 acceptance now names it; catalog §7.1's F0.4 row states the emitted series explicitly | plan-overview.md §4 P0 T6 + catalog §7.1 |
| C90 | §7.1's task map still assigned the claim-latency task (P1b T6) to F10 after the F10→F11 split, and F11 carried **no** claim-latency Source anchor (P0-e) | **fixed:** T6 moves to F11 in the task map; F11's Source now carries the full claim-latency chain (`worker_pool.py:302` → `task_processor.py:1829` → `task/repository.py:2619`) and the measured columns (`task/models.py:273-274`) | catalog §7.1, F11 |
| C91 | §14.5/§14.6 carried a "copies must be updated … wave 2/3" deferral note | **dissolved by M-2:** the ledger is now *referenced, not copied* — §14.6 states the pointer discipline and the deferral is removed | catalog §14.5, §14.6 |
| C92 | The "claim path" phrase named `worker_pool.py:1381-1386` (the `self._stats` dict literal) as the claim path | **fixed:** `:302` is the **claim CALL**; `:1381-1386` is the `self._stats` dict literal = the source of the four claim-side outcome values (the fifth, `fresh_episode_reset`, comes from the episode seam `graph.py:7319`) | catalog §8.1, §8.13, F11 |
| C93 | The Prometheus TSDB capacity formula (plan §13.1.6 + the P2 risk row 4) omitted the **scrape-interval** input and did not reproduce its own figure — as written ≈124.4 GB, at the plan's own 15 s hint ≈8.3 GB, and the stated ≈21 GB matched neither (NB-17) | **fixed:** the derivation now states **both** inputs — the **scrape interval (15 s)** and the **series count** — and is self-consistent: `30 d = 2 592 000 s` ÷ 15 s = **172 800 samples per series** × **16 B/sample** × **1.5** (WAL + compaction headroom) = **4 147 200 B per series** × **2 000 series** (the ≤2 k daemon-origin budget RULE — the ceiling the gate is sized against) = **8 294 400 000 B ≈ 8.3 GB**. The **observed** count (the grand total quoted in §14.1) is **≈2.5 GB** at the same interval; the gate uses the budget ceiling so it still holds if the surface grows into its budget, and it stays an **install-blocking** precondition | plan-overview.md §13.1.6 + §4 P2 risk row 4 |
| C94 | The read seam `_charge_checkpoint_read` was cited at `checkpoint_adapter.py:371-374` only — that range covers the **`aget_tuple`** charge; the **`aget`** charge is at `:368` (NB-16) | **fixed:** both sites are now cited together — `daemon/checkpoint_adapter.py:368` (aget) and `:371-374` (aget_tuple) — in the plan (§1, the P1b T14 task row, the P1b risk row 3, §13.1.1) and in the catalog's F7 Source. **(Anchor superseded by C96):** the read-instrumentation lane later collapsed those two charge sites into ONE shared call inside `_observe_read`, so the anchors are now `:361` (both forwarders) | plan-overview.md §1/§4/§13.1.1 + catalog F7 |
| C95 | The **checkpoint quick-wins READ family** (7 metrics, 46 series) **SHIPPED** from the checkpoint read-path quick-wins lane (`feature/checkpoint-quick-wins`) **before this catalog existed** and was **absent from it entirely** — no §1 row, no §4 entry, no §8 vocabulary, no §14 count | **fixed:** catalogued retroactively as **F7b** (the read-side sibling of F7) — §1 summary row (+46), §4 family entry (canonical names, aliases, labels, type, source, reset semantics, owner/origin note, measured series estimate), §7 roll-up + delta trail, §7.1 task/owner row, §8.9 vocabularies (the read `op` set and the 9 integral read-size bucket labels), §8.11 bucket set, §10.1 reset row, and the §14.1/14.2/14.3/14.4 ledger (daemon-origin total **534 → 580**, grand total **≈605 → ≈651**, families **25 → 26**, headroom **1 466 → 1 420**). The series count is **measured** against the live registry, not estimated. The alias expositions add **no** series (they are the same series under a second name) | catalog §1/§4 (F7b)/§7/§7.1/§8.9/§8.11/§10.1/§14 |
| C96 | The **file:line anchors** this catalog cites into `daemon/checkpoint_metrics.py` and `daemon/checkpoint_adapter.py` were written against the **pre-WS-B** registry file; the WS-B registry generalization **replaced `checkpoint_metrics.py` wholesale** and the read-instrumentation lane **refactored the adapter's read seam**, so a set of anchors no longer resolved | **fixed for the live sections:** every stale anchor in a **live** section was re-anchored against the merged tree — the bucket sets (`checkpoint_metrics.py:356-359`, `:364-368`), the renderer (`:1392`), the per-object getter and the per-metric lock (`:741`, `:482`/`:493-495`), the single-bucket write and the render-time accumulation (`:720-727`, `:780-799`), the registration-time label-key rejection (`:525-530`), the registry singletons and the F7 `op` enum (`:996`, `:1007`, `:1011`), the read family's `op` enum and bucket-label set (`:1117`, `:1109`), the aput/aput_writes forwarders (`checkpoint_adapter.py:617`/`:621`), and F7's read seam (the **single shared** `_charge_checkpoint_read` call at `checkpoint_adapter.py:361` — the two separate per-forwarder charge sites this catalog used to cite were collapsed into one by the read-instrumentation lane's M-2 refactor). **Intentionally NOT rewritten:** (i) §0.2's "before" narrative, whose `checkpoint_metrics.py:95-104`/`:220-222` pointers describe the **pre-WS-B file** — the paragraph now carries an explicit *Current anchors* sentence with the post-WS-B locations; (ii) the §11 correction rows (C1, C4, C33), whose anchors are a **historical record** of the pre-WS-B state — rewriting a correction log's history would falsify it | catalog §0.2, §0.4, §0.5, §1 (F0.2), F0.2, F7, §8.9, §8.11, §11 |

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

---

## 14. Reconciled counts ledger — single source of truth (B-13, B-06, B-08, B-09)

**Purpose.** ONE authoritative place for every count the plan asserts. Every other document
(plan-overview.md, decisions.md) **references** these values — it does not copy them (M-2, see
§14.6); no number may appear in two documents with different values, and no number may be
restated where a pointer will do. Derived from the §1 summary table + §7 roll-up after the
B-series reconciliation.

### 14.1 Quoted values — the single quoted-values table

**Every count the other documents assert is quoted here, once.** plan-overview.md and
decisions.md **point** at the row they need (M-2 — see §14.6); they do not restate the value.
The `Scope` column says which document owns the artifact the count describes.

| Value | Quoted number | Scope | Derivation / source |
|---|---|---|---|
| Daemon-origin total | **580** | catalog | P0 28 + P1a 63 + P1b 263 + P1c 226 (§7 roll-up) |
| Shell-origin (textfile) | **4** | catalog | F22: 3 process-name series + 1 freshness gauge |
| Exporter-origin | **≈67** | catalog | node_exporter + postgres_exporter (§6) — measured separately, NOT part of the daemon-origin budget |
| Grand total on Prometheus server | **≈651** | catalog | 580 + 4 + 67 |
| Budget | ≤2 000 | catalog (the RULE) | headroom **1 420** (≈29 % consumed) |
| Daemon-origin families | **26** | catalog | F0.1–F0.4 (4) + F1–F21 (21) + F7b (1) — the same set `ensemble_metrics_invalid_label_total{metric}` names (F22 is exporter-origin and counted separately: 27 = 26 daemon-origin + F22) |
| Fully-wired bound (every label combination live) | **≈1 160** | catalog | the v5 bound ≈1 118 adjusted by the same five per-family deltas (§7) + F7b's 46 (C95) — an adjusted bound, see §11 C79 |
| Shipped alert rules | **14** | plan | 13 original + `TextfileStale` (V-8) — plan §8 |
| promtool fixture files | **17** | plan | 11 × 1 + 3 × 2 (the V-1 defect/repro pairs) — plan §10 |
| Alert-rule baselines | **14** | plan | one per rule — plan §8 Baseline column |
| Risk rows carrying a BACK-OUT | **17** | plan | P0 3 + P1a 2 + P1b 5 + P1c 3 + P2 4 — plan §4 |
| **Bound cross-check row** — the fully-wired series bound (three DIFFERENT quantities; do not conflate) | catalogued **≈1 160** (the **provenance-disclosed adjusted bound** — **not** re-derived) · **derived catalogued-ceiling 822** (first-principles) · **conservative expanded-label ceiling 1 145** (first-principles) | catalog | **Derivation status — the three are not interchangeable.** (i) The **≈1 160** figure quoted in §7 is the **adjusted** bound: it is the v5 bound moved by the same per-family deltas, and **its own derivation was never written down** (§11 **C79**) ⇒ it is **not reproducible by rule** and is labelled *adjusted* everywhere it appears. (ii) The **derived catalogued-ceiling 822** is **first-principles**: for each of the 26 daemon-origin families (F0.1–F0.4 + F1–F21 + F7b), each metric's **full label product** over the label sets that metric actually carries (§8, §14.4), times the histogram bucket-series count where §8.11 states one, summed. It reproduces the family ceilings the catalog states explicitly (F7 121, F12 117) and exceeds the catalogued estimate exactly where that estimate assumed a subset (F0.2, F4, F5, F21). (iii) The **conservative expanded-label ceiling 1 145** is the same first-principles sum with every **documented expanded form** substituted (F21's op-partitioned journal histogram — §11 **C81**; F7's fully-wired `/op` form; F12's full lane × outcome product) — the worst case the catalog's own documented variants allow. **Cross-check result:** the derived expanded ceiling and the adjusted bound agree to within **15 series (≈1.3 %)**, so the adjusted bound is **corroborated in magnitude** but remains **not re-derivable** — that residual is the evidence for keeping it labelled *adjusted*. **All three are inside the ≤2 000 budget rule** (headroom 1 178 and 855 against the two derived ceilings); the **certified daemon-origin total in the first row of this table is unchanged**. |

**Reading the bound cross-check row.** The row exists because the fully-wired bound was
previously quoted in more than one sense with no statement of which sense it was. It is now
**one** row carrying **three** labelled quantities with their derivation status, so a reader
can never mistake the **adjusted** bound (provenance-disclosed, §11 C79) for a **derived**
ceiling (computed here by rule). Any future change to a family's label structure must bump all
three consistently and append a §11 correction entry (the ledger discipline of §14.6).

### 14.2 Per-phase totals

| Phase | Families | Series | Derivation |
|---|---|---|---|
| P0 | F0.1–F0.4 | **28** | F0.1 16 + F0.2 3 + F0.3 8 + F0.4 1 |
| P1a | F1–F6 | **63** | 9+22+6+8+3+15 |
| P1b | F7–F13 + F7b | **263** | 21+9+53+6+30+69+29+46 |
| P1c | F14–F21 | **226** | 3+22+5+40+9+7+75+65 |
| **Daemon total** | F0.1–F21 + F7b | **580** | |

### 14.3 Per-family series values (changed families flagged)

| Family | Series | Notes |
|---|---|---|
| F0.1 | 16 | unchanged |
| F0.2 | **3** | +1 cap-rejected (R-m3) +1 invalid-label (B-09) |
| F0.3 | 8 | unchanged |
| F0.4 | 1 | unchanged |
| F1 | 9 | unchanged |
| F2 | 22 | unchanged |
| F3 | 6 | unchanged (incl. `manual`, R-m9) |
| F4 | 8 | unchanged |
| F5 | 3 | unchanged |
| F6 | 15 | unchanged |
| F7 | 21→31 | **CHANGED** — today 21 / post byte-wiring 31 (R-m8) |
| F7b | **46** | **NEW** — the shipped checkpoint quick-wins read family, catalogued retroactively (C95); 46 measured against the live registry |
| F8 | 9 | unchanged |
| F9 | 53 | unchanged |
| F10 | **6** | was 20/21; claim latency moved to F11 (B-08) |
| F11 | **30** | was 16; +14 claim latency (B-08) |
| F12 | 69 | unchanged |
| F13 | **29** | was 31/33; wake 7→5 (B-06) + lifecycle 7→5 (B-07) |
| F14 | 3 | unchanged |
| F15 | **22** | was 23 (B-13) |
| F16 | 5 | unchanged |
| F17 | 40 | unchanged |
| F18 | 9 | unchanged |
| F19 | 7 | unchanged |
| F20 | 75 | unchanged |
| F21 | **65** | was 66 (B-13) |
| F22 | 4 shell | 3 process names + 1 freshness gauge (NB-8, B-02(b)) |

### 14.4 Vocabulary sizes (single canonical value per set)

| Vocabulary | Value | Source anchor | Notes |
|---|---|---|---|
| Wake-turn outcome | **5** | claim call `worker_pool.py:302` (claim_task) + episode seam `graph.py:7319` (`_detect_fresh_episode_reset`); canonical carrier producer `task_processor.py:569-598` (fresh-episode flag computed once at `:569-576`, carried on `ProcessingContext` at `:598`; ledger identity `instance_messaging.py:2611-2613`; carrier field `message_processing_pipeline.py:176`); `worker_pool.py:1381-1386` is the `self._stats` dict literal = source of the outcome *vocabulary* (the **four** claim-side outcome values; the fifth, `fresh_episode_reset`, comes from the episode seam `graph.py:7319`), NOT the claim path — the claim CALL is `worker_pool.py:302` (B-10, B-11; catalog §11 **C29** — the consumer/producer split, **C39** — the 5-value closable wake set, **C73** — the `worker_pool.py:1381-1386` anchor correction; §11 C92) | `claimed`, `skipped_busy_instance`, `empty_claim`, `timeout_wake`, `fresh_episode_reset`; `None` yields no increment (B-06) |
| Instance lifecycle event | **4 real + reserved `other`** | event_publisher.py:38-80 | `completed`, `failed`, `terminated`, `error`; `fan_in_*` removed (B-07) |
| Instance status | 10 | instance/models.py:20-32 | unchanged |
| Instance liveness | **6 reachable + reserved `other`** | work_status.py:67-99 | `unknown` folds to `other` (NB-10) |
| Readiness outcome | 11 | readiness.py:64-79 | unchanged |
| Wedge-capture reason | 4 | triggers.py:54-64 | incl. `manual` (R-m9) |
| Sweep lane | 13 | report_delivery_recovery.py:219-266 + 6 sweeps | unchanged |
| Sweep row outcome | 6 | §8.4 | unchanged |
| Tool category | 55 + reserved `other` (freeze-time) | _tool_registry.py:1598 | artifact is canonical (V-4) |
| F3 reasons | 4 | triggers.py:54-64 | unchanged |
| F22 process names | **3** | textfile sampler | `launcher`, `promote_sh`, `stage_sh` (NB-8) |
| F0.3 spine table | 4 | §8.9 | unchanged |
| F17 MCP roster | 5 | §8.5 | unchanged |
| Checkpoint READ op | **2** | `daemon/checkpoint_adapter.py:607` (aget) + `:614` (aget_tuple) — the two instrumented saver retry-proxy forwarders; the registry's `allowed_values` at `daemon/checkpoint_metrics.py:1117` | `aget`, `aget_tuple` |
| Checkpoint read-size bucket label | **9** | `daemon/checkpoint_metrics.py:1109` (`_READ_SIZE_BUCKET_LABELS`, derived from `READ_SIZE_BUCKETS` at `:1100`) | 8 integral byte bounds + `+Inf`; every member is integral, so the label is the SAME string as the size histogram's `le=` label |
| F17 MCP state | 4 | init_guard.py:145-147 | unchanged |
| F17 MCP failure kind | 4 | §8.5 | unchanged |
| F9 readiness component | 4 | readiness.py:94-99 | unchanged |
| F11 worker pool event | 4 | worker_pool.py:1381-1386 | unchanged |
| F12 sweep outcome | 6 | §8.4 | unchanged |
| F21 journal op | 26 | §8.7 | unchanged |
| F21 upgrade phase | 10 | §8.8 | unchanged |
| F21 alert kind | 3 | §8.8 | unchanged |
| F19 compaction rung | 3 | §8.10 | unchanged |
| F19 compaction stage | 3 | §8.10 | unchanged |
| F14 token direction | 2 | §8.10 | unchanged |
| F16 stream-stall phase | 3 | §8.10 | unchanged |
| F16 sweep outcome | 2 | §8.10 | unchanged |
| F18 gateway session outcome | 5 | §8.10 | unchanged |
| F18 gateway audit outcome | 2 | §8.10 | unchanged |
| F8 checkpoint table | 3 | §8.9 | unchanged |
| F8 prune outcome | 4 | §8.9 | unchanged |
| F7 checkpoint op | 4 | checkpoint_metrics.py:1011 | unchanged |
| F6 PG backend state | 7 | §8.9 | unchanged |
| F6 leak site | 6 | §9.2 | unchanged |
| F4 pool name | 2 | §8.9 | unchanged |
| F4 native quantity | 4 | §8.9 | unchanged |
| F4 request-error kind | 3 | §8.9 | unchanged |
| F5 pool name | 2 | §8.9 | unchanged |
| F20 turn source | 5 | §8.10 | unchanged (the turn-source set belongs to **F20**, not F1 — §11 C87) |
| F1 loop-lag stat | 3 | §8.3 | unchanged |
| F1 probe state | 4 | §8.3 | unchanged |
| F3 write outcome | 2 | §8.3 | unchanged |
| F2 thread group | 11 | §8.2 | unchanged |
| F13 status | 10 | instance/models.py:20-32 | unchanged |
| F13 liveness | 6 reachable + reserved `other` | §8.1 | (NB-10) |
| Exposition outcome | 2 | §8.3 | unchanged |
| Admission state | 4 | §8.10 | unchanged |
| Worker pool name | 2 | §8.10 | unchanged |
| LLM outcome | 8 | §8.12 | unchanged |
| LLM token direction | 2 | §8.10 | unchanged |
| Stream-stall phase | 3 | §8.10 | unchanged |
| Compaction rung | 3 | §8.10 | unchanged |
| Compaction stage | 3 | §8.10 | unchanged |
| Journal op | 26 | §8.7 | unchanged |
| Upgrade phase | 10 | §8.8 | unchanged |
| Alert kind | 3 | §8.8 | unchanged |
| MCP roster | 5 | §8.5 | unchanged |
| MCP state | 4 | §8.5 | unchanged |
| MCP failure kind | 4 | §8.5 | unchanged |
| Gateway session outcome | 5 | §8.10 | unchanged |
| Gateway audit outcome | 2 | §8.10 | unchanged |
| Spine table | 4 | §8.9 | unchanged |
| Checkpoint table | 3 | §8.9 | unchanged |
| Checkpoint op | 4 | §8.9 | unchanged |
| Checkpoint prune outcome | 4 | §8.9 | unchanged |
| Pool native quantity | 4 | §8.9 | unchanged |

### 14.5 Places each total appears

- **580 (daemon total):** catalog §1 summary, §7 roll-up, §14.1/14.2. **plan-overview.md and decisions.md do not restate it — they POINT at §14.1** (M-2): every series count, headroom, family count, vocabulary size, rule/fixture/baseline/risk-row count and the daemon-origin total are quoted in §14.1 and referenced from the other documents, never duplicated (the old "copies must be updated … wave 2/3" deferral is dissolved — §11 C91).
- **28 (P0):** catalog §1, §7, §14.2.
- **63 (P1a):** catalog §1, §7, §14.2.
- **263 (P1b):** catalog §1, §7, §14.2.
- **226 (P1c):** catalog §1, §7, §14.2.
- **4 (shell):** catalog §1 F22, §6, §7, §14.1.
- **≈651 (grand total):** catalog §7, §14.1.

### 14.6 Cross-doc alignment instruction (M-2 — the ledger is referenced, not copied)

plan-overview.md and decisions.md **pointer** to the values in §14.1–14.4 instead of restating
them: where a document needs a series count, headroom, family count, vocabulary size,
rule/fixture/baseline/risk-row count or the daemon-origin total, it cites the §14 subsection
that holds the value. **No number may appear in two documents with a different value, and no
number may be restated where a pointer will do.** Rule thresholds and their baseline citations
stay where they are — they are rule *parameters*, not counts — and the budget RULE
(daemon-origin ≤2 k; exporters measured separately) stays with the rule. Where a value differs
from a pre-reconciliation copy, fix the copy, not the ledger. The ledger is the single source of
truth; any future change bumps `ensemble_metrics_vocabulary_version` (F0.4) and appends a §11
correction entry.
