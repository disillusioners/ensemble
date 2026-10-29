# Decisions: v2 Metrics Collection — reviewer-facing finding → resolution map
### (v2 council round §A–§H + **v3 architecture round §I–§M** + **correction round §N–§O**)

Date: 2026-10-29 · Author: plan-decisions worker (fallback channel, no skill)
v3 round: plan-decisions worker (WS-B wave 3) · 2026-10-29
Correction round (WS-B wave 3 FINAL): plan-decisions worker · 2026-10-29 · §N (Item A — macOS/WS-A) + §O (Item B — partition unification)
Status: **Ready for review** — this document is the reviewer's entry point.
Companions: `plan-overview.md` (411 lines), `metric-catalog.md` (1339 lines) and
`architecture-recommendation.md` (697 lines) in this directory. Every row below points
**into** those files; no row asserts anything that they do not already contain.

**Two rounds live in this file.** §A–§H is the **v2 council round** (31 findings from the
2-model council review), preserved as written — it is history and is annotated, never
rewritten. §I–§M is the **v3 architecture round**: the 28 numbered amendments of
`architecture-recommendation.md` §10, the five architect-critical findings, the two
open-question answers, the CR-3 record correction, and the updated companions/counts.

Code citations in this document are the **corrected** references from
`metric-catalog.md` §11 (corrections log C1–C26), re-verified in the worktree
`agents-ensemble-wt-metrics-v2` @ `feature/metrics-collection` (tip `9bbef71c1`). The stale
digest references are **not** reproduced here. Where `plan-overview.md` still carries a
stale reference, it is logged as an open item in **§H** rather than silently copied.

---

## A. Verdict & method

v2 **resolves** the 2-model council review (verdict NEEDS-REVISION on the v1 plan): all
**5 CRITICAL** and all **16 MAJOR** findings are **resolved by construction** — none is
waived — and the **10 MINOR** findings are 8 fixed, 1 fixed-with-one-waived-clause (m9), 1
waived with rationale (m5).

How a reviewer verifies that nothing was dropped: this document carries **31 finding rows**
(§B 5 + §C 16 + §D 10) and each row's *implemented at* cell resolves to a numbered section
of `plan-overview.md` or to a family/annex of `metric-catalog.md`. The count is the check —
31 rows in, 31 findings out, one row per finding id from the council review. The 5
leader-ratified decisions (§E) and the 18 reuse rows (§F) are cross-checked against
`plan-overview.md` §2/§3/§9 and `metric-catalog.md` §1/§2–§6. §G carries every waived
item and known limitation; §H carries the row counts, the cross-file name-consistency
result (**34 alignment edits E1–E34 across two passes**; all **11 open items H-1…H-11 are
CLOSED**), and both commits.

**v3 round (WS-B wave 3).** The architect's `architecture-recommendation.md` §10 enumerates
**28 required amendments**, and §0/§9 flag **5 findings that would have shipped a
well-formed but EMPTY series**. Every one of the 28 was **materially verified** in the two
sibling files before its row was written (§I); the five were resolved against the code
(§J). Two open questions were answered with the architect's defaults (§K). One v2 record
was corrected (§L). §I carries **28 rows** — the reviewer's count check — and §M carries the
companions, counts, waiver additions, the v3 edit ledger and the consistency-sweep result.
**v3 edits E35–E78 (44)**; **one new open item (H-12)**.
**Correction round (WS-B wave 3 FINAL).** §N records the macOS/WS-A root-cause correction (leader-supplied facts, wave 1): prod is macOS, the resource tier was dark because of a **dead producer** (no `/proc` on Darwin), the "flusher binds bool→INTEGER 42804" framing is **refuted** (C22→C35), and the A1–A5 resolution rows. §O records the leader-sanctioned G-20/H-12 partition unification (wave 2): one canonical partition, the full F0.1–F22 → phase 1:1 table, 533 daemon-origin series, and the 42/42 task-row completion pass. §G closes G-20 and adds the platform-fact limitation rows; §H/§M are refreshed for the correction round.

---

## B. CRITICAL findings (5/5 resolved)

| ID | Finding (one line) | Resolution in v2 | Implemented at | Status |
|---|---|---|---|---|
| **CR-1** | Wedged daemon cannot export its own `/metrics` on the main port (event-loop starvation **is** the incident; livez/readyz timed out 25–30 s Oct-26/28; no external liveness alert). | `/metrics` is explicitly **not** a witness for its own wedge. Witnesses live out-of-process: external liveness `up==0` / `absent()` on the scrape target, `scrape_duration_seconds` as a starvation precursor, a node_exporter **textfile `*.prom`** directory written by an out-of-process writer, node_exporter + postgres_exporter, and the wedge-capture artifacts (`threads.csv`, `lag.json`, `readyz.json`) already produced by the capture path. | `plan-overview.md` §1 incident table ("Wedged daemon cannot export own /metrics" row) + §3 "Out-of-process witnesses (CR-1)" + §4 P2 T1/T3 + §8 `ExternalLivenessDown` + `ScrapeDurationHigh`; `metric-catalog.md` F22 (shell-origin textfile, 5 series) + F3 (`ensemble_wedge_capture_total{reason}`) + §7 (exporter-origin ≈67 counted separately) | **resolved** |
| **CR-2** | `:9797/metrics` landed on the main app public port with zero auth / bind / gating (host binds `0.0.0.0` — `daemon/config.py:566`; `AuthConfig.enabled` defaults False — `daemon/config.py:3524`; CORS falls back to `*` — `daemon/api.py:4693`). | A **separate localhost-bound listener** (`127.0.0.1` only, own port, managed key `limits.metrics_listener_port` default 9798) — never a route on `daemon/api.py`. An explicit **never-exported list** is enforced at render time. On/off is decided in P0: kill-switch default **OFF**. A bind failure degrades the listener to *disabled*, never a boot abort. | `plan-overview.md` §3 "Separate localhost-bound /metrics listener (decision 3 + CR-2)" + §4 P0 T1/T2/T3 + §5 (managed keys) + §7 (never-exported list) + §6; `metric-catalog.md` §0.7 (listener contract) + F0.1 (greenfield seam `daemon/services/metrics_listener.py`, degradation) + F0.4 | **resolved** |
| **CR-3** | Duplicated the live checkpoint histogram, and **no bucket set** existed for any of the 5 histograms (existing buckets stop at `10.0` — `daemon/checkpoint_metrics.py:59-62`), so 17 s writes / 73–99 s reads landed in `+Inf` and p95 collapsed to 10.0 (CheckpointSlow spurious forever). | `daemon/checkpoint_metrics.py` (top-level) becomes the **single canonical registry**; bucket sets are stated **once** for all **7** histograms with the checkpoint top extended to `300.0` s (≥120 s satisfied); **one measurement, one name** — the two legacy `message_api_*` names are re-registered under canonical `ensemble_*` names with a one-release deprecated alias; the existing bounded **slow-op ledger** (`daemon/checkpoint_perf.py:91`, `MAXLEN=128`) is the CheckpointSlow source — no parallel detector. | `plan-overview.md` §4 P0 T4 + P0 exit criteria + §8 `CheckpointSlow` + §10 AC9 + §1 (corrected "Why" — read path, not write throughput); `metric-catalog.md` §0.2 + §0.3 + §0.4 + F7 (observation sites `daemon/checkpoint_perf.py:190,:198`) + §8.11 (bucket annex) + §10.2 + §11 C1/C4 | **resolved** — ⚠️ **v3 record correction: see §L** (the reuse story is "registry wired to collectors, exposition unwired", not "unwired registry") |
| **CR-4** | `ensemble_db_pool_checkout_timeouts_total{pool}` has **no available source** — psycopg_pool 3.3.1 exposes no timeout counter (`daemon/services/wedge_capture/collector.py:24-26` states it verbatim: `pool_checkout_timeouts` is ALWAYS `None`, reason `not_exposed_by_psycopg_pool_3.3.1`; `requests_errors` is a documented **superset**). | The timeout name is **retired**. v2 ships `ensemble_db_pool_requests_errors_total{pool,kind}` as the **documented superset** (not a timeout count) and **rebases** PoolExhausted onto it. The `kind` label is **reserved** (`unclassified` only today) so the checkout-seam follow-up can split it later **without a breaking label change**. The SQLAlchemy manager pool has no `requests_errors` equivalent ⇒ `pool="manager"` renders no series (an absence, not a gap). | `plan-overview.md` §1 incident table + §4 P1b "Note (CR-4)" + §8 `PoolExhausted` + §10 AC10; `metric-catalog.md` F4 (a/b/c/d) + §8.9 (kind set) + §10.2 (restated alert) + §11 C12 + §12 open item 1 | **resolved** |
| **CR-5** | P0 deploy rested on a **nonexistent** `infranstructure/k8s/` (misspelled and absent in both trees). | Deploy target is **host binaries / VM compose** (leader-ratified, decision 1). k8s is **explicitly out of scope — a separate project**; the v1 reference is withdrawn and **no k8s manifest ships** with this plan. | `plan-overview.md` §2 "Deploy target (RATIFIED, decision 1)" + §2 "Out of scope (explicit)" + §11 (Non-goals) + §4 P2 (exporters on the host); `metric-catalog.md` F22 + §6 (host + PostgreSQL exporters, no k8s) | **resolved** |

---

## C. MAJOR findings (16/16 resolved)

| ID | Finding (one line) | Resolution in v2 | Implemented at | Status |
|---|---|---|---|---|
| **MA-1** | No kill-switch; the managed-key-only gate is restart-gated (`daemon/services/settings_service.py:190`). | `ENSEMBLE_METRICS_ENABLED` is read **per request** (no restart), default **OFF**. It is *layered under* the managed-keys registry as `limits.metrics_enabled` so the DB/env/yaml override chain can also flip it, but the per-request env read is the authoritative emergency off-switch. OFF ⇒ empty-shape 200, samplers stop ticking, **no history lost** (the registry keeps accumulating). | `plan-overview.md` §5 (kill-switch + 7 managed keys) + §4 P0 T3 + P0 exit criteria + §6 dark-families table; `metric-catalog.md` §0.8 + F0.1 (degradation) + F0.2 | **resolved** |
| **MA-2** | `thread_cpu_seconds` would **rebuild** the existing `/proc` sampler (`wedge_capture/sampler.py`). | **Reuse, post-process only.** `daemon/services/wedge_capture/sampler.py` stays the ONE `/proc` walker (per-thread jiffies `:160`, process jiffies `:175`, `comm` `:151`, thread-name map `:192`); bucketing into **11 fixed thread groups** happens downstream. One-`/proc`-dialect rule is explicit. | `plan-overview.md` §4 P1a T2 + §9 R4; `metric-catalog.md` F2 + §8.2 (thread group, 11 values) | **resolved** |
| **MA-3** | `/api/resources` + the `resource_monitor` spine (9 files, tiered retention) were unmentioned; the per-instance tier is broken (`flusher.py:770` binds a Python `bool` into an `INTEGER` column → PG SQLSTATE 42804, rolling back all 4 tables); "4 tables accepting rows" was not an exit criterion. | `/metrics` is a **VIEW over the same services**, never a parallel collector. A dedicated **spine health gate** (F0.3) makes "all four tables accepting rows" a **P0 exit criterion**; the runtime fix is `daemon/manager.py:7341` `_ensure_postgres_columns` (the PG migration runner is a NO-OP). Per-instance attribution stays in the PG tier (cardinality). WS-A slippage is tracked as P0 risk 1 with the `ensemble_resource_*` families gated on it. | `plan-overview.md` §3 "Prometheus-vs-resource_monitor split (decision 4)" + §4 P0 T8 + P0 exit criteria + P0 risk 1 + §6 dark-families row + §2 cardinality; `metric-catalog.md` §0.6 (idiom B) + F0.3 (P0 exit criterion, `flusher.py:770`) + §11 C22 | **resolved** |
| **MA-4** | The lag-probe is a **300-sample rolling window with a computed p95**, not a Prometheus histogram, and it is **dark** when `ENSEMBLE_WEDGE_CAPTURE_ENABLED` is off. | Export the **computed quantiles as gauges** (`stat` ∈ p50/p95/max) — not a histogram; v1's `histogram_quantile(..._bucket)` expression is invalid (no `_bucket` series exists). The dark dependency is documented on the HELP text, and the alert is paired with a sample-count guard so it cannot fire on a half-full window. | `plan-overview.md` §4 P1a T1 + §6 dark-families row + §12 OQ-1 + §8 `LoopStarved` (expr corrected, see §H); `metric-catalog.md` F1 (window `:59`, p95 `:115`, p50 `:116`, max `:114`) + §10.2 + §11 C5 | **resolved** |
| **MA-5** | Readiness labels collapsed the normative **11-class** taxonomy; grace is **already** on `/readyz`, so re-probing would duplicate work. | The 11-class taxonomy is preserved verbatim (`daemon/services/readiness.py:64-79`; grace subset `:83-89`; components `:94-99`). The family **reads the published `/readyz` composite** and never re-probes; the grace-eligible subset is published on the HELP text, not as a label. FalseDegraded is expressed in **pure PromQL**. | `plan-overview.md` §4 P1b T5 + P1b exit criteria + §8 `FalseDegraded`; `metric-catalog.md` F9 + §8.3 (11 + 4 + 4 values) + §10.2 + §11 C6 | **resolved** |
| **MA-6** | `SweepHotLoop` referenced an undefined `ensemble_sweep_cycles_total`; the proposed `rate(..._cycle_seconds_count[5m])` is invalid because the cycle metric is a **gauge**. | The counter `ensemble_sweep_cycles_total{lane}` ships and the alert is stated as `rate(ensemble_sweep_cycles_total{lane="rdrs_mission_reconciliation"}[5m]) * 300 > 2`. The `lane` label is **13 values** (7 RDRS lanes + 6 standalone sweep services) and each lane's **configured interval is published on the HELP text**, so actual-vs-configured needs no second metric. Per-tick `orphan_released` is accumulated into a cumulative counter (doc drift at `daemon/services/job_queue_service.py:828-837`). | `plan-overview.md` §4 P1c T7 + P1c exit criteria + §8 `SweepHotLoop` (expr corrected, see §H); `metric-catalog.md` F12 + §8.4 (lane 13 / outcome 6) + §10.2 + §11 C14/C17 | **resolved** |
| **MA-7** | `BashSpin` compared a **cumulative-CPU gauge** to 0.9 ⇒ fires forever; and a spinning bash process **cannot self-report** from inside the daemon. | The textfile gauge is compared **directly** (`ensemble_process_cpu_seconds{name=~"promote_sh\|stage_sh\|launcher"} > 0.9`) — no `rate()` over a per-tick delta gauge. The measurement owner is the **external** writer: the shell scripts rewrite `*.prom` in `$INSTALL_DIR/data/metrics/`, and node_exporter's textfile collector reads them. The value is a per-tick delta, so "always > 0.9" now means "a real spin". | `plan-overview.md` §3 "Textfile collector (R18)" + §4 P2 T1 + §8 `BashSpin` (expr corrected, see §H); `metric-catalog.md` F22 (gauge semantics + `name` vocabulary) + §10.2 | **resolved** |
| **MA-8** | LLM tokens/cost were absent, and an `ok`/`failover`/`error` split **collapses the 4-layer retry model**. | `ensemble_llm_tokens_total{direction}` (input/output) with the truncation/undercount caveat made **visible** by `ensemble_llm_token_usage_missing_total`; `ensemble_llm_request_total{outcome}` carries the **real 8-value outcome enum** derived from the 4-layer ladder (L1 tenacity per-category retry → L2 failover to backup → L3 reset to primary → L4 attempt ceiling), with the layer mapping tabulated; stall counts are taken at the **watchdog abort site** (`daemon/services/llm_stream_watchdog.py:456`). The latency histogram deliberately carries **no** outcome label (splitting it would cost 8 × 14 = 112 series). | `plan-overview.md` §4 P1a T4 + P1a risk 1 + §4 P1b family list; `metric-catalog.md` F14 + F15 + F16 + §8.12 (ladder table) + §8.10 + §11 C8/C11 | **resolved** |
| **MA-9** | AC1/AC3 were **untestable** ("fires within 5 min" / "<1% CPU"). | AC1 is decomposed into **fault-injection fixtures per alert, per phase**, plus a per-incident-row replay acceptance; AC2 is a **measurable method** — daemon CPU delta ≤1 % measured via `/api/resources/self-cost` under a 15 s scrape; AC7 repeats the replay as an independent criterion. | `plan-overview.md` §10 AC1/AC2/AC7 + §4 P1a/P1b/P1c/P2 "Test plan" lines + §5 `limits.metrics_scrape_interval_hint_s`; `metric-catalog.md` §0.10 (owner roles: developer / devops / reviewer / tester) + each family's Owner field | **resolved** |
| **MA-10** | The ≤2k series cap **holds for daemon-origin** but stock exporters breach 2k on their own. | The budget is restated as **daemon-origin ≤ 2 k** (actual **521**, 26 % consumed) with **stock exporters measured separately** (≈67) and explicitly excluded from the daemon budget. The roll-up table is the proof, and an AC asserts the split. | `plan-overview.md` §2 "Cardinality budget (MA-10)" + §10 AC3 + §4 P1a exit criteria (cardinality census) + §7 never-exported; `metric-catalog.md` §1 (521 / 526 / ≈593) + §7 (the ≤2k proof) + F22 (exporter-origin counted separately) | **resolved** |
| **MA-11** | "P1 in 2–4 days" was **not credible** — one phase for every subsystem. | P1 is **split into P1a / P1b / P1c**, each with its own owner role, dependency map, per-task acceptance, exit criteria, risk table and test plan. The order is **forced by the dependency graph** (registry → pools/census → checkpoint/jobs/sweeps/instances → LLM/MCP/compaction/journal → exporters), not by calendar. | `plan-overview.md` §4 (coupling map + P1a + P1b + P1c) + §5; `metric-catalog.md` §2.1 (why the partition is forced) + §3/§4/§5 (per-phase family sections) | **resolved** |
| **MA-12** | Ellipses violated the fixed-vocabulary rule (instance status 10 values, sweep lanes, MCP roster, version-label churn). | Every label set is **written out in full** in one place — the vocabulary annex (§8.1–§8.10) — each with the `file:line` it was verified against; family entries reference the annex by number. A **vocabulary freeze marker** (`ensemble_metrics_vocabulary_version`) pins the revision. The version label lives only on the release gauge (OQ-3). | `plan-overview.md` §4 P0 T6 + P0 exit criteria + §7 + §12 OQ-3; `metric-catalog.md` §0.5 + §8.1–§8.10 + F0.4 + §11 C13/C14 + §12 open item 5 | **resolved** |
| **MA-13** | Leaked-txn **site attribution was asserted, not derived**, and daemon connections carry an **empty `application_name`**. | A **closed fragment→site map** (6 values incl. the `other` residual, so the sum always ties to `ensemble_pg_backends{state="idle_in_transaction"}`) plus the `db_capture.py` discipline: dedicated **non-pooled** connection, `application_name='ensemble-metrics-census'`, `statement_timeout`, `connect_timeout`, self-exclusion of the census's own connection, ≥15 s cadence, never-raises. Read-only: it never touches `pg_stat_activity` and never terminates a backend. | `plan-overview.md` §1 "Leak story (P1 pool poisoned)" + §4 P0 T7 + P1b T2 + P1b risk 1; `metric-catalog.md` F6 + §9.1 + §9.2 + §9.3 + §9.4 | **resolved** |
| **MA-14** | Journal metrics were **untied to releases/state.json**, the incident was an **entry-count O(n²)** walk (not bytes), and the example exposition was invalid. | Journal metrics come from the **Python twin** `daemon/tools/upgrade_journal.py` — `scripts/upgrade/` is **observed, never edited** (leader-ratified, decision 2). The family ties to the release set (`data["current"]`) and the history entry count (`len(data["history"])`), so the alert fires on **either** the file size **or** the entry count; the twin degrades to the last good copy on a torn journal. | `plan-overview.md` §2/§3 (decision 2) + §4 P2 T2 + P2 exit criteria + §8 `JournalWalkRisk`; `metric-catalog.md` F21 (`journal_read :259`, `journal_write :331`, `journal_update_field :374`, `journal_history_append :391`) + §8.7 (26 op kinds) + §8.8 (10 phases) + §11 C26 + §10 (durable) | **resolved** |
| **MA-15** | No **degradation contract** — a 503 would read as "target DOWN" and fire `absent()`/`up==0` alerts spuriously. | **Empty-shape 200, never 503**, with a `# <family> unavailable: <reason>` comment line. Per-family dark/live matrix: a family whose dependency is off emits **no series** (absent ≠ zero), with two declared real-zero exceptions (pool "no waiting clients"; spine "table accepting rows"). Kill-switch-off scrape stays **<1 ms**. | `plan-overview.md` §6 (whole section incl. the dark-families matrix) + §10 AC4 + §2; `metric-catalog.md` §0.11 + each family's "Degradation" bullet (F0.1, F4, F6, F7, F9, F11, F12, F14, F17, F18, F20, F21, F22) | **resolved** |
| **MA-16** | No owners / test plan / textfile-dir line item; the v1 plan **silently reversed** the Gap-5/A-8 `no-prometheus_client` decision; classic exposition was **mislabelled as OpenMetrics**. | The **dependency-free renderer is KEPT and REAFFIRMED LOUDLY** — no `prometheus_client`, no OTel, no new PyInstaller surface; the supersession is named so a reviewer sees a deliberate reaffirmation, not an oversight. Exposition is **Prometheus classic text format, NOT OpenMetrics**. Owner roles (developer / devops / reviewer / tester) are assigned per family and per phase; a test plan exists per phase; the textfile dir is a managed key (`limits.metrics_textfile_dir`) plus OQ-2. | `plan-overview.md` §3 "Dependency-free renderer (decision 5 + MA-16, REAFFIRMED loudly)" + §5 (managed keys + textfile dir) + §12 OQ-2 + per-phase "Owner role" lines + per-phase "Test plan" lines; `metric-catalog.md` §0.2 + §0.10 + every family's "Owner" field + F22 (textfile dir) | **resolved** |

---

## D. MINOR findings (10 rows — 8 fixed · 1 split · 1 waived)

| ID | Finding (one line) | Resolution in v2 | Implemented at | Status |
|---|---|---|---|---|
| **m1** | `wake_turns_total` had **no named producer** (Carrier-Threading rule) and no consumer. | The full contract is specified: **producer** = the worker-pool claim path (`daemon/services/task_processor.py:569-598`, the same identity that derives `is_fresh_episode_user_message`), computed **once**; **carrier** = a new keyword-only field on `ProcessingContext` (`daemon/services/message_processing_pipeline.py:121`, sibling carrier at `:176`) defaulting to `None`; **threading** = keyword-only, pipeline → facade → service; **consumer** = the renderer, and **consumer-seam re-derivation is forbidden**. | `plan-overview.md` §4 P1c T5 + P1c exit criteria + P1c risks 1–2; `metric-catalog.md` F13 + §8.13 (the contract, verbatim) + §10 (in-RAM) | **fixed** |
| **m2** | The pool gauge **mixed derived vs native** quantities. | Split into two metrics so the derivation is visible in the **name**: `ensemble_db_pool_checked_out` (DERIVED — `pool_size − pool_available`, clamped at 0; no native key exists) and `ensemble_db_pool_waiting_clients` (NATIVE — `requests_waiting`, async pool only). Native quantities stay on `ensemble_db_pool_native{pool,quantity}`. | `plan-overview.md` §4 P1b family list + §9 R12; `metric-catalog.md` F5 + F4 + §8.9 + §11 C12 | **fixed** |
| **m3** | Queue depth must read `admission_state`; **claim-latency has no existing instrument** — name the seam. | Depth reads the `job_queue_items.admission_state` column (the `AdmissionState` enum, sole write authority) with the label named `admission_state` ∈ queued/active/done/dead. Claim latency is **greenfield** with the **seam named**: the claim boundary in `daemon/services/worker_pool.py` (`wait_for_work` → task-claim call) where `enqueued_at` and the claim moment are both available; the observation is taken **once** and carried, never re-derived. | `plan-overview.md` §4 P1c T1/T2 + exit criteria; `metric-catalog.md` F10 + §8.10 + §12 open item 4 + §11 C15 | **fixed** |
| **m4** | Heartbeat age had **no cadence and no source**. | Source column named (`daemon/repositories/task/models.py:284` `last_heartbeat_at`, a `timestamp without time zone` ⇒ bind/compare with `now_utc_naive()`), and the **configured cadence is published as its own series** (`DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0`, `daemon/services/worker_pool.py:45`, read from the pool's constructor argument — never hard-coded), so "40 s old at a 30 s cadence" is distinguishable from "40 s old at a 300 s cadence". | `plan-overview.md` §4 P1c T4 + exit criteria; `metric-catalog.md` F11 + §8.10 | **fixed** |
| **m5** | The skill-metrics lane should not be re-surfaced in `/metrics`. | **Waived — Non-Goal.** The lane already has its own surface (`/api/skills/{id}/metrics`); re-surfacing it in `/metrics` would duplicate the same numbers and double-count them. It is therefore excluded from the catalog entirely rather than given a half-family. (The durable `skill_usage_records` table is a *different* artefact and is tracked separately — see §G / §H.) | `plan-overview.md` §11 (Non-goals) + §2 "Out of scope (explicit)"; `metric-catalog.md` scope line (header) — "skill-metrics (`/api/skills/{id}/metrics`) is a **non-goal**, not catalogued" | **waived** — rationale: duplicating an existing surface double-counts and inflates cardinality for no new information. |
| **m6** | Compaction pressure needs a counter, not just an occupancy reading. | `ensemble_compaction_fired_total{rung}` (rung ∈ `l1_pre_dispatch_80`, `l2_precall_95`, `l3_backstop_600k`) observes the sites that are currently **log-only**; `ensemble_compaction_occupancy_ratio{stage}`; `ensemble_compaction_precall_estimate_tokens` renders the **max across instances** (a per-instance label would explode cardinality — 6 324 instances in prod). The rung recorded is the one **actually applied**, because escalation lowers the gate from 0.95 to 0.80. | `plan-overview.md` §4 P1a T5 + P1a risk 3; `metric-catalog.md` F19 + §8.10 (`daemon/compaction.py:1128`, `:2299`; gate `daemon/graph.py:7497`, `:7656-7670`) | **fixed** |
| **m7** | Tool-call perf must instrument the `message_tap.py` seam, **not** graph internals. | The tool-call and turn-performance families instrument `MessageTapSlot.tap_node_return` (`daemon/services/message_tap.py:234`) — the per-turn seam (R17) — with the tool **category** read from the tool's own registration metadata (37 categories). The tap is **non-load-bearing** (it swallows exceptions and logs at WARNING), so a tap failure yields *no series*, never a broken turn. | `plan-overview.md` §4 P1a T6 + §9 R17; `metric-catalog.md` F20 + §8.6 + §8.10 + §11 C24 | **fixed** |
| **m8** | Lifecycle was **point-in-time only** — no spawn/revive/pause/fan-in counters. | `ensemble_instance_lifecycle_total{event}` carries the 7-value event set (`spawned`, `revived`, `paused`, `resumed`, `terminated`, `fan_in_completed`, `fan_in_failed`), explicitly **including revive-on-`send_message`** — the lost-wakes class. | `plan-overview.md` §4 P1a family list + P1c exit criteria; `metric-catalog.md` F13 + §8.1 + §8.10 | **fixed** |
| **m9** | The VictoriaMetrics/Mimir compatibility claim was **asserted without basis** — test it or drop it; the off-loop spec needed to be pinned at the 2 hottest seams. | **Split — compat claim WAIVED (dropped), off-loop spec FIXED.** No compatibility is asserted for an untested stack: the target is **stock Prometheus + Grafana**, and the claim is removed from the scope statement, the out-of-scope list and the non-goals. The off-loop half is fixed by the non-negotiable off-loop discipline: metric collection never adds event-loop work (background threads, atomic counters, samplers off-loop; the existing lag probe + wedge watchdog are the templates), one `/proc` dialect, and the per-request kill-switch read — with the census on a dedicated non-pooled connection and the textfile writer out-of-process. | `plan-overview.md` §2 "Out of scope (explicit)" + §11 (Non-goals) + §3 "Off-loop discipline (non-negotiable)" + §1 incident table (textfile witness); `metric-catalog.md` header (compat line, m9) + §11 C25 + F22 (out-of-process writer) + F6/§9.1 (census isolation) | **resolved (split)** — compat claim dropped; off-loop spec fixed. |
| **m10** | Three metrics idioms coexist — state how `/metrics` aggregates them. | Every family names its idiom: **A** = in-process registry (resets on restart) · **B** = PG table spine (durable) · **C** = log markers (a *source* for greenfield counters, **never** for a gauge). `/metrics` is a **VIEW** over these — samplers are shared, the render path only formats — never a parallel collector. | `plan-overview.md` §3 "Prometheus-vs-resource_monitor split (decision 4)"; `metric-catalog.md` §0.6 + each family's "Source" field | **fixed** |

**Minor split summary:** fixed **8** (m1, m2, m3, m4, m6, m7, m8, m10) · fixed-with-one-waived-clause **1**
(m9: compat claim dropped, off-loop spec fixed) · waived **1** (m5, Non-Goal with rationale).

---

## E. Leader-ratified decisions (5/5 — all binding, all implemented)

| # | Decision | Where implemented |
|---|---|---|
| **1** | **Deploy target = host binaries / VM compose; k8s is out of scope** (a separate project). The v1 `infranstructure/k8s/` reference is withdrawn; no k8s manifest ships. | `plan-overview.md` §2 "Deploy target (RATIFIED, decision 1)" + §2 "Out of scope (explicit)" + §11 (Non-goals) + §4 P2 (exporters deployed on the host); `metric-catalog.md` F22 + §6 (node_exporter / postgres_exporter on the host, no k8s) |
| **2** | **Journal metrics via the Python twin `daemon/tools/upgrade_journal.py`; `scripts/upgrade/` is NEVER edited** (the shell scripts are *observed* through the twin, not modified). | `plan-overview.md` §3 "Textfile collector (R18, genuinely new)" + §4 P2 T2 + P2 exit criteria + §8 `JournalWalkRisk`; `metric-catalog.md` F21 ("Leader-ratified: NEVER edit `scripts/upgrade/`") + §8.7 (shell-written op kinds observed through the twin) + §11 C26 |
| **3** | **Separate localhost-bound `/metrics` listener** — own app, own port, `127.0.0.1` only; never a route on the main public app; bind failure degrades to *disabled*, never a boot abort. | `plan-overview.md` §3 "Separate localhost-bound /metrics listener (decision 3 + CR-2)" + §4 P0 T1/T2 + §5 (`limits.metrics_listener_port`, default 9798) + §7; `metric-catalog.md` §0.7 + F0.1 (greenfield `daemon/services/metrics_listener.py`) |
| **4** | **Prometheus is authoritative for alerting series; the `resource_monitor` PG tier is authoritative for per-instance API views** (`/api/resources/*`); `/metrics` is a **view over the same services**, not a second implementation. | `plan-overview.md` §3 "Prometheus-vs-resource_monitor split (decision 4)" + §2 cardinality + §10 AC3 + §6; `metric-catalog.md` §0.6 (idioms A/B/C; view-not-collector) + F0.3 (PG tier authority, spine gate) + F22 + §7 (budget split) |
| **5** | **The dependency-free renderer is KEPT — Gap-5/A-8 `no-prometheus_client` is REAFFIRMED LOUDLY** (named as a deliberate reaffirmation, not an oversight). Exposition is **Prometheus classic text format, NOT OpenMetrics**; no `prometheus_client`, no OTel, no new PyInstaller surface. | `plan-overview.md` §3 "Dependency-free renderer (decision 5 + MA-16, REAFFIRMED loudly)" + §10; `metric-catalog.md` §0.2 ("No `prometheus_client`, no OTel, no new PyInstaller surface") + header (compat line — see §G G-14 for the wording nit) + F0.2 (the existing registry generalizes in place) |

---

## F. Reuse ledger (18 rows — none rebuilt instead)

| R | What it is | Where v2 builds on it (catalog family / overview row) |
|---|---|---|
| **R1** | `daemon/checkpoint_metrics.py:273-315` — the registry + renderer (`render_metrics()` at `:304`) | `metric-catalog.md` **F0.2** (`render_metrics()` *becomes* the registry renderer; the two singletons at `:273`/`:281` become registry-registered instances under canonical names) + **F7**; `plan-overview.md` §9 R1 — ⚠️ **v3 record correction: see §L** (the singletons DO have a production consumer; only `render_metrics()` is unwired) |
| **R2** | `daemon/checkpoint_perf.py:190,:198` + the bounded slow-op ledger (`:91`, `MAXLEN=128`, default 5 000 ms cut) | `metric-catalog.md` **F7** (both observation sites already exist — *reuse, do not duplicate*; the ledger **is** the CheckpointSlow source, no parallel detector) + §8.11 (buckets) + §10.2; `plan-overview.md` §9 R2 |
| **R3** | `wedge_capture/lag_probe.py` — 300-sample rolling window (`MAX_LAG_SAMPLES=300` at `:59`), computed p95 `:115` / p50 `:116` / max `:114` | `metric-catalog.md` **F1** (computed quantiles exported as gauges; dark when wedge-capture is off) + §10.2 + §11 C5; `plan-overview.md` §9 R3 |
| **R4** | `wedge_capture/sampler.py` — the ONE `/proc` walker (reuses `resource_monitor/proc_readers.py`) | `metric-catalog.md` **F2** (post-process bucketing into 11 groups; never a second sampler) + §8.2; `plan-overview.md` §9 R4 |
| **R5** | The `resource_monitor` spine (sampler 30 s, ring 48 h, flusher 60 s, retention sweep, 4 tables) | `metric-catalog.md` **F0.3** (the spine health gate + "4 tables accepting rows" P0 exit criterion; the flusher counters at `flusher.py:161`/`:336` are the source) + §0.6 (idiom B) + §11 C22; `plan-overview.md` §9 R5 |
| **R6** | `/api/resources/*`, `maintenance`, `/api/snapshots/metrics` surfaces | `metric-catalog.md` §0.6 (**/metrics is a VIEW over the same services**) + **F0.3**; `plan-overview.md` §3 (decision 4) + §9 R6. *Partial:* the maintenance/snapshot **counter families** are not yet catalogued → §H H-7 |
| **R7** | `daemon/services/maintenance.py:531-536` `_VACUUM_FULL_TABLE_BYTES_SQL` (one round trip summing `checkpoints` + `checkpoint_writes` + `checkpoint_blobs`) | `metric-catalog.md` **F8** (`ensemble_checkpoint_blobs_table_bytes` is a **split-out** of that one round trip; `ensemble_checkpoint_table_bytes{table}` is its generalization) + §11 C9; `plan-overview.md` §9 R7 |
| **R8** | `daemon/services/readiness.py:64-79` — the 11-class taxonomy (grace subset `:83-89`, components `:94-99`) + the grace already on `/readyz` | `metric-catalog.md` **F9** (labels preserved; the family *reads* the published composite, never re-probes) + §8.3 + §11 C6 + §10.2; `plan-overview.md` §9 R8 |
| **R9** | The `MANAGED_KEYS` registry (`daemon/services/settings_service.py:190`) | `metric-catalog.md` §0.8 (the registry default must mirror the pydantic Field default exactly) + **F0.1**/**F0.2**; `plan-overview.md` §5 (7 new managed keys, bounds mirroring Fields) + §4 P0 T5 + §9 R9 |
| **R10** | `daemon/services/worker_pool.py:1518` `get_stats()` (incl. `wakeup_efficiency` at `:1537`, 4 in-RAM counters at `:1381-1386`) | `metric-catalog.md` **F11** (`workers_total`/`workers_busy`/`wakeup_efficiency`/`events_total`) + §8.10 + §10 (in-RAM ⇒ resets on restart); `plan-overview.md` §9 R10 |
| **R11** | `wedge_capture/db_capture.py` census discipline (`application_name`, `statement_timeout`, `connect_timeout`, driver-stripped DSN, never-raises) | `metric-catalog.md` **F6** + **§9.1** (the census inherits that discipline on a dedicated non-pooled connection) + §9.4 (complementary to the readiness leak oracle, not duplicate); `plan-overview.md` §9 R11 |
| **R12** | `wedge_capture/collector.py` A2 vocabulary (`checked_out` derived at `:19-20`, `requests_waiting` native at `:23`, `pool_checkout_timeouts` ALWAYS `None` at `:24-26`, `pool_requests_errors` at `:29`) | `metric-catalog.md` **F5** (derived vs native kept apart) + **F4** (the `kind` vocabulary + the CR-4 superset semantics) + §8.9 + §11 C12; `plan-overview.md` §9 R12 |
| **R13** | `daemon/tools/ens_db_tools.py:1157` — the pool-status seam | `metric-catalog.md` **F4** (pool status is already published at `/readyz` → `detail.pool_state`; the family reads that shape — one status source, not two) + §11 C12; `plan-overview.md` §9 R13. *Note:* the catalog collapses both reads into F4 — §H H-7 |
| **R14** | The MCP **MachineStore** roster (gateway sessions/tombstones) and the per-boot MCP **quarantine registry** | `metric-catalog.md` **F18** (`mcps_gateway/store.py:79`/`:103` → sessions, tombstones, audit) + **F17** (`mcp/init_guard.py:145-147` → server state, read from `/readyz` `detail.mcp`, never re-probed) + §8.5 (bounded 5-value roster); `plan-overview.md` §9 R14 (the plan's pointer conflated the two — §H H-10, now **CLOSED**: the plan §9 R14 row is split into the two correct references (see §H.2 E28) |
| **R15** | The sweep interval registry (300 s / 90 s configured intervals) | `metric-catalog.md` **F12** (13 lanes, each lane's configured interval published on the HELP text so actual-vs-configured needs no second metric) + §8.4 + §10.2; `plan-overview.md` §9 R15 |
| **R16** | The `MAINTENANCE_ENDPOINTS_ENABLED` gating pattern (a kill-switch analog) | `metric-catalog.md` §0.8 (kill-switch contract: per-request read, default OFF, registry keeps accumulating) + **F0.1**; `plan-overview.md` §5 + §9 R16 |
| **R17** | `daemon/services/message_tap.py` — the per-turn seam (`tap_node_return` at `:234`) | `metric-catalog.md` **F20** (tool-call + turn-performance families instrument *this* seam, not graph internals) + §8.6 + §8.10 + §11 C24; `plan-overview.md` §9 R17 |
| **R18** | The node_exporter **textfile collector** pattern (genuinely new — the only greenfield exporter piece) | `metric-catalog.md` **F22** (5 shell-origin series, counted **outside** the daemon-origin ≤2k budget) + §1/§7 (exporter-origin ≈67 measured separately); `plan-overview.md` §3 "Textfile collector (R18, genuinely new)" + §9 R18 |

---

## G. Deliberate waivers & known limitations register

Each entry: what is waived/limited · where it is handled in the two sibling files · one-line rationale.
Every entry below is traceable to `plan-overview.md` or `metric-catalog.md`; nothing here is invented.

| # | Waiver / limitation | Where handled | Rationale |
|---|---|---|---|
| **G-1** | **`metric-catalog.md` is 992 lines against its 400–650 target** — waived. | The catalog as a whole: §1–§12, with 101 verified `file:line` references, 26 families × 8 fields each, and 8 annexes (vocabulary, buckets, ladder, census, counter durability). | **Waived for citation fidelity.** A truncated catalog loses the exact `file:line` a reviewer needs to check a claim; the length buys the verified refs (and §11's 26 corrections) that the whole review rests on. The 400–650 target is a size preference, not a correctness constraint. |
| **G-2** | **MCP roster label bounded to 5 values** (`context7`, `plane`, `webfetch`, `custom`, `other`) although `mcp_servers.name` is unbounded. | `metric-catalog.md` §8.5 (roster + **churn policy**: user-created servers are never named individually, they collapse into `custom`; `other` is reserved so the roster can never exceed 5 live values) + F17 + §12 open item 5. | `mcp_servers.name` is user-controlled ⇒ naming it as a label is unbounded cardinality. The churn policy keeps the label set closed while leaving room for an additive future roster; per-user-server metrics would be a **second, explicitly-labelled** family, not a smuggle-in. |
| **G-3** | **`ensemble_db_pool_requests_errors_total` emits only `kind="unclassified"` today** (psycopg 3.3.1 exposes the aggregate, not the breakdown). | `metric-catalog.md` F4(b)/(c)/(d) + §8.9 (kind set: `waiting_exception`, `too_many_requests`, `unclassified`) + §12 open item 1 + §10.2 (precise follow-up expression). | The **label is reserved ahead of the instrument** so the checkout-seam follow-up can split the value later **without a breaking label change**; today's aggregate is documented as a superset, not a timeout count. |
| **G-4** | **`ensemble_thread_cpu_seconds` cannot split worker vs chat-worker thread groups** until worker threads are named at construction. | `metric-catalog.md` F2 (P1a prerequisite: `Worker` calls `super().__init__(daemon=True)` with no `name=` at `daemon/services/worker_pool.py:253`, so every worker is `Thread-N`) + §12 open item 3 + §8.2 (the 11-value group set). | Until the threads are named, the `worker_pool` / `chat_worker_pool` split is **not observable from `/proc` at all**; both fold into `other` rather than shipping a label whose value would be fiction. Naming the threads at construction is a P1a prerequisite, not a metric redesign. |
| **G-5** | **`ensemble_checkpoint_op_bytes` under-reports for `aget`** until the blob byte count is captured at the `log_saver_op` seam. | `metric-catalog.md` F7 (`log_saver_op(..., bytes_=0, ...)` at `daemon/checkpoint_perf.py:148`; the hot path `time_saver_op` at `:202` forwards `bytes_=0`) + §12 open item 2. | The seam already carries a `bytes_` parameter ⇒ this is a **wiring** gap, not a new instrument; P1b must either wire the real blob size or record the metric as "bytes for maintenance carveouts only". Shipping a knowingly-undercounting bytes histogram without that decision would be the real risk. |
| **G-6** | **The `ensemble_job_claim_latency_seconds` producer is not yet chosen** (no existing instrument). | `metric-catalog.md` F10 (greenfield; the claim boundary inside `worker_pool.wait_for_work` → the task-claim call is the named seam; the observation is taken once and carried) + §12 open item 4 + §11 C15. | The only related signal (`WorkerPool._stats["empty_claim_attempts"]`, `daemon/services/worker_pool.py:1383`) counts empty polls, **not** enqueue→claim latency. Choosing the producer is a P1b task with a named seam, so the ambiguity is bounded — and the metric is not claimed until then. |
| **G-7** | **`ensemble_sweep_rows_total{lane,outcome}` is per-lane in practice** — most lanes emit 2–3 of the 6 outcomes. | `metric-catalog.md` F12 (the shared 6-value outcome enum is fixed; the ≈30 live (lane, outcome) combinations are what the series estimate uses) + §8.4 + §12 open item 6. | A **shared, closed enum** keeps the vocabulary stable for alerting and dashboards; the live-combination count is a property of which lanes are enabled (they are individually kill-switchable), not of the label design. |
| **G-8** | **`plan-overview.md` §12 open questions OQ-1 … OQ-4** (waiver choices the plan defers to P0). | `plan-overview.md` §12. Dispositions: **OQ-1** — the catalog already answers it: gauges, not a histogram (F1); adopt at P0. **OQ-2** — the textfile dir is a managed key `limits.metrics_textfile_dir` (default `$INSTALL_DIR/data/metrics`, §5); needs a devops decision on a dedicated mount. **OQ-3** — recommend the version label **only** on `ensemble_upgrade_release_current{version}` (F21, bounded by the retention-3 release set). **OQ-4** — the catalog's two per-event families (`ensemble_turn_events_total{source}`, `ensemble_instance_lifecycle_total{event}`) already cover the intent, so no new `ensemble_event_total{kind}` vocabulary is required (the plan's family list was aligned — E15 — see H-11). | These are **P0 decisions with the catalog's recommendation already stated** — deferring the choice to P0 keeps the vocabulary freeze (F0.4) the single moment where label sets are pinned. OQ-1 and OQ-4 are closed by the catalog; OQ-2 and OQ-3 remain genuine devops/reviewer choices. |
| **G-9** | **Any alert over a reset-on-restart counter must use `rate()`/`increase()`** — never a raw comparison, never `absent()`. | `metric-catalog.md` §10.1 (per-family durability table) + §10.2 (every v1 alert over a reset-on-restart counter is restated: PoolExhausted, CheckpointSlow, LoopStarved, LostWake, SweepHotLoop, JournalWalkRisk, CheckpointBloat, PoolLeak, FalseDegraded, BashSpin) + §6 (degradation contract). | A raw comparison reads "0" right after a restart and produces a **false negative for the whole fill window** — the alert would be blind exactly when the daemon comes back. Two plan alerts were rewritten for this reason during the cross-file check (§H). |
| **G-10** | **LLM token counts are a stale/undercounting proxy** and are lost when the backend omits the final usage chunk. | `metric-catalog.md` F14 (`usage_metadata` is "never the primary" — `daemon/graph.py:7632`; lost entirely when the final usage chunk is absent — `:3873-3881`) + `ensemble_llm_token_usage_missing_total` + §11 C8. | The caveat is made **visible as a counter** rather than hidden in a docstring: an operator can see how many observations had no usage metadata, so a low token total is never mistaken for a quiet hour. Non-streaming call sites increment the missing counter instead. |
| **G-11** | **Queue-item durations are not derivable.** | `metric-catalog.md` F10 + §11 C15 (`job_queue_items` carries only `created_at` as a TEXT ISO string — `daemon/repositories/job_queue/models.py:220`, `:357`; no `started_at`/`completed_at`). | No duration family is claimed for queue items. Claiming one would require a schema change (a migration on existing data) that this plan does not authorize — so the metric is **absent by design** rather than wrong. |
| **G-12** | **`pool="manager"` renders no series for `ensemble_db_pool_requests_errors_total`.** | `metric-catalog.md` F4 (the SQLAlchemy manager pool raises `TimeoutError` on checkout and has **no** `requests_errors` equivalent) + §8.9. | An **absence, not a gap**: the manager pool's checkout failures are observable through the readiness composite, not through a psycopg-shaped counter. The family must not invent a parallel counter for it. |
| **G-13** | **The leak-site map is a closed set with an `other` residual.** | `metric-catalog.md` §9.2 (`other` = "the residual bucket — **never dropped**, so the census total always equals the sum") + F6 + §8.9 (6 site values). | `sum(ensemble_pg_leaked_txn) == ensemble_pg_backends{state="idle_in_transaction"}` must hold at all times; a growing `other` is the **signal** to extend the map through a deliberate, reviewed change rather than an automatic label drift. |
| **G-14** | ~~Catalog header said "OpenMetrics text"~~ — **CLOSED in the alignment pass (H-1)**: the header now reads "**Prometheus classic text exposition format** (NOT OpenMetrics — MA-16 corrects the v1 mislabel)", matching `plan-overview.md` §3 and the MA-16 resolution. | `metric-catalog.md` header (compat line) — edited; `plan-overview.md` §3. | The catalog's substantive position was already unambiguous (§0.2 forbids `prometheus_client`/OTel; the renderer is the existing classic-text `render_metrics()`) — only the wording drifted. Now the wording matches the decision, so a reader cannot re-import the OpenMetrics mislabel. |
| **G-15** | ~~F0.1's series estimate mentioned an `ensemble_metrics_up{component}` info split~~ — **CLOSED in the alignment pass (H-2)**: the split is dropped, `ensemble_metrics_up` is a single no-label scalar, and the family entry, the §1 summary row and the §7 roll-up now agree (F0.1 = 16 series; daemon-origin total 521). | `metric-catalog.md` F0.1 + §1 + §7 — edited. | The no-label shape was already the catalog's dominant reading (name line, Labels line "No other labels", degradation semantics, and no `component` vocabulary anywhere in §8); the stray `{component}` parenthetical was the outlier. Dropping it removes an unbounded-label risk and makes the ≤2k proof arithmetically true end to end. |
| **G-17** | **The tool-category label set is derived at runtime (49 + a reserved `other`), never hand-listed** — the closed list is deliberately **not** written out. | `metric-catalog.md` §8.6 (runtime source `get_tool_categories()`, `daemon/tools/_tool_registry.py:1598`; 49 distinct across 254 `register_tool_category(` sites, re-verified in this worktree by the v3 round) + the §8 opening rule ("every closed vocabulary is derived from code, never hand-listed") + §0.5 (folded values counted in `ensemble_metrics_invalid_label_total{metric,label}`) + F20 (the `other` fallback) + §11 C27; `plan-overview.md` §4 P1a (the tool-call family) | A hand-written list is the drift class the architect falsified (37/38 claimed vs 49 in-tree). The **derivation** is the contract; a cross-check test pins the annex to `get_tool_categories()` at P0. A tool whose category is not in the runtime-derived set folds to `other` and is counted — never silently inflating cardinality. |
| **G-18** | **Prometheus storage loss is unrecoverable** — v2 runs a **single local TSDB** (30 d raw / 90 d downsample), so a host loss takes the whole alerting history with it. | `plan-overview.md` §4 P2 Risks row 4 (RK-16) + §2 (retention sentence) + §4 P2 exit criteria; `metric-catalog.md` §10.0 (the Prometheus TSDB row of the restart-loss table: "all history lost — remote_write to a durable store, out of v2 scope") | **Accepted for v2** (architect's Q2 default — see §K Q2). The deploy target is host binaries (decision 1), which makes host loss the dominant scenario — but the deferral is explicit, recorded as **RK-16**, and `remote_write` is named as a **separate project**, not a v2 task. Idiom B/C families survive a Prometheus storage loss via their durable backing store (PG tables / journal files). |
| **G-19** | **`ensemble_turn_duration_seconds` is anchored, with a drop-condition** — the tap measures its own latency (including its `asyncio.to_thread(upsert_batch)` DB write), which would be DB-write latency mislabelled as turn duration. | `metric-catalog.md` F20 (the "Anchor pair specified" paragraph — entry-tap timestamp → node-return timestamp, carried on the §8.13 carrier) + §11 C31; `plan-overview.md` §4 P1a task 6 | The architect allowed "specify the anchor pair **or** drop the family". v3 **specifies** the pair (entry-tap → node-return) and keeps a **drop-condition**: a source with no entry-tap counterpart contributes **no observation** (absent ≠ zero) and the family is dropped for that source rather than shipped mislabelled. The family therefore keeps its 14 series in the ≤2k proof. |
| **G-20** | ~~**The plan's §4 phase lists and the catalog's §1/§7 ship phases use two different partitions** (pre-existing v2, not wave-induced).~~ — **RESOLVED by §O (Item B, wave 2)**: the partition is now unified (one canonical partition, F0.1–F22 → phase 1:1, zero exceptions; catalog §7 states the convention; plan §4 family lists align). See §O. | `plan-overview.md` §4 (four work-package phase sections) vs `metric-catalog.md` §1 (the Phase column) + §7 (the per-phase roll-up) — **unified** per §O; the historical divergence is recorded in §O's census (15 split + 4 catalog-only = 19 discrepancies) and the "36 families" v3-audit figure is corrected (not reproducible at family granularity) | **RESOLVED (§O).** The catalog's §1/§7 phase column is authoritative for the ≤2k roll-up and for "which phase must complete before a family ships"; the plan's §4 headings are work-package labels (the subsystem whose instrumentation lands there). The intra-family phased-rollout convention (task-level early-ship note, family phase = completion phase) is stated in catalog §7. F22's minimal textfile writer ships in P1b per A25 (task-level carve-out). All 42 plan task rows resolve to their own phase (§O). |
| **G-21** | **T3 per-child RSS/CPU attribution on macOS is a decided non-goal (A2)** — per-child families/values are dark on Darwin; no partial per-child data ships on macOS; a future per-pid Darwin story is a new, separately-scoped decision. | `plan-overview.md` §11 (Non-goals, A2 non-goal) + §2 (deploy target, macOS) + §6 (dark-families matrix, non-Linux host row); `metric-catalog.md` §13.4 (non-goal — decided, implement as decided) + §13.1 (per-child T3 row) + §13.2 (F2/F3 notes) | **Ratified by the planner under mission authority "make it an explicit decision"** (available for leader override). Rationale: the Darwin ps-based reader (`feature/resource-monitor-flusher-fix@53547b915`) is a coarse **daemon/process-level** view (`_read_darwin_daemon_snapshot`), not a per-pid sampler; `read_child_snapshot` has **no** Darwin fallback; per-pid sampling has no Darwin path even post-merge. Promising per-child on macOS would ship a silently-empty series (the J-class defect). |
| **G-22** | **Platform-field nuances (WS-A correction):** (a) `hw_peak_kb` IS supplied on Darwin post-merge via `ru_maxrss` (cross-platform, not Linux-only — the "Linux-only" label sometimes attached to hw-peak is wrong for the post-merge reader); (b) `anon_kb` degrades to the `rss_kb` proxy on Darwin (not `None`), making `coverage_pct` rss-denominated and not comparable to Linux anon-based coverage; (c) `swap_kb` is **unwired on every platform today** (column exists, no reader populates it — NULL on both). | `metric-catalog.md` §13.1 (field-level split table: hw_peak_kb, anon_kb, swap_kb rows) + §13.2 (F0.3 notes) + §13.3 (coverage_pct semantics); `plan-overview.md` §3 (platform support summary) + §6 (non-Linux host row) | These are **documented degradations, not defects** — the consumer-visible shape changes are pinned (§13.3) so a Darwin host never silently renders a Linux-shaped payload. `pid_map` census-moment figures differ (46,448 vs the commit's 45,575) — same climbing counter, different observation moment; no action. |
| **G-16** | **`plan-overview.md` §1 incident table names three metrics the catalog does not ship** (`ensemble_db_pool_connections{pool,state}`, `ensemble_db_pool_checkout_seconds{pool}`, `ensemble_pg_leaked_txn_rows{site}`). | `plan-overview.md` §1 (not edited — §1 is outside this task's permitted edit scope). | The catalog's equivalents are `ensemble_db_pool_native{pool,quantity}` + F5's two gauges, and `ensemble_pg_leaked_txn{site}`; **no** checkout-latency family ships in v2 (the checkout seam is deferred — CR-4). Carried as open item **H-3** with the corrected names. |

---

## H. Reviewer verification checklist

### H.1 Row counts (the "nothing dropped" check)

| Artifact | Expected | Present | OK |
|---|---|---|---|
| CRITICAL finding rows (§B) | 5 (CR-1 … CR-5) | 5 | ✅ |
| MAJOR finding rows (§C) | 16 (MA-1 … MA-16) | 16 | ✅ |
| MINOR finding rows (§D) | 10 (m1 … m10) | 10 | ✅ |
| **Total finding rows** | **31** | **31** | ✅ |
| CRITICAL resolved (none waived) | 5 | 5 | ✅ |
| MAJOR resolved (none waived) | 16 | 16 | ✅ |
| MINOR fixed / split / waived | 8 / 1 / 1 | 8 / 1 / 1 | ✅ |
| Leader-ratified decisions (§E) | 5 | 5 | ✅ |
| Reuse ledger rows (§F) | 18 (R1 … R18), **none "rebuilt instead"** | 18 | ✅ |
| Waiver / limitation register entries (§G) | 22 (G-1 … G-16 + v3's G-17 … G-20, of which G-20 **closed** by §O; + correction round's G-21, G-22) | 22 | ✅ |
| Catalog §12 open items carried | 6 of 6 (→ G-2, G-3, G-4, G-5, G-6, G-7) | 6 of 6 | ✅ |
| Plan §12 open questions carried | 4 of 4 (→ G-8) | 4 of 4 | ✅ |
| Cross-file alignment edits — pass 1 | 21 (E1–E21) on 4 §4 family-list lines + 5 §8 alert rows | 21 | ✅ |
| Cross-file alignment edits — pass 2 (alignment pass) | 13 (E22–E34) across `plan-overview.md`, `metric-catalog.md`, `decisions.md` | 13 | ✅ |
| **Total alignment edits** | **34** | **34** | ✅ |
| **v3 — architect amendment rows (§I)** | **28** (canonical §10 numbering 1–28) | **28** | ✅ |
| **v3 — architect critical-finding rows (§J)** | **5** | **5** | ✅ |
| **v3 — open-question resolutions (§K)** | **2** (Q1, Q2) | **2** | ✅ |
| **v3 — record correction (§L)** | **1** (CR-3 reuse story) | **1** | ✅ |
| **v3 — cross-file edits** | **44** (E35–E78) | **44** | ✅ |
| Open items H-1…H-11 | 11, all **CLOSED** (closure table below) | 11 closed | ✅ |
| **v3 — open items** | 1 (**H-12**, phase-partition divergence) — **RESOLVED by §O (Item B)**: partition unified, 1:1, zero exceptions | 1 | ✅ |
| **Correction round (§N/§O)** | Item A platform/WS-A correction (A1–A5) + Item B partition unification (B) | 2 items | ✅ |
| **Correction-round verification** | 7 checks (alert table 15/15 incl. 2 histogram suffixes; plan §4 family lists 69/69; task rows 42/42; family→phase 1:1 zero exceptions; series 533 reconcile; 5 decisions + 28 amendments + WS-A BLOCKS + C22→C35 + platform §13; all citations verified) | 7/7 | ✅ |

Every finding id from the council review appears exactly once: 5 CRITICAL, 16 MAJOR, 10 MINOR
(5+16+10 = 31). No finding is silently merged into another row.

### H.2 Cross-file name-consistency result (Step 3)

Scope per spec: every metric name in **`plan-overview.md` §8 alert table** and **§4 family
lists**, checked against **`metric-catalog.md` §1 Summary table** (modulo `_bucket`/`_count`/
`_sum` histogram suffixes and Prometheus built-ins). The check is run **programmatically**
(regex extraction of `ensemble_*` tokens from the §8 table body and the four §4
`Metric families added (names only…)` lines, then a set-diff against the canonical names in
catalog §1) — not by eye.

**Extraction result — after the alignment pass (pass 2)**

| Source | Tokens | Resolve to catalog §1 | Do not resolve | Δ vs pass 1 |
|---|---|---|---|---|
| §4 P0 family list (L72) | 0 `ensemble_*` | — | — | unchanged (the two pre-migration `message_api_*` aliases stay by design — catalog §0.3 / F7) |
| §4 P1a family list | 15 | 15 | 0 | 17 → 15 (the P2-owned textfile family and the uncatalogued GII throttle were dropped) |
| §4 P1b family list | 13 | 13 | 0 | 18 → 13 (checkout-latency name retired; 3 uncatalogued names dropped) |
| §4 P1c family list | 14 | 14 | 0 | 18 → 14 (3 uncatalogued names dropped; the retired event-total vocabulary is referenced only in a parenthetical) |
| §4 P2 family list | 6 | 6 | 0 | unchanged |
| **§4 totals** | **48** | **48** | **0** | was 59 / 50 / 9 → **unresolved 9 → 0** |
| §1 incident table | 27 tokens (25 distinct) | 27 | 0 | was 3 unresolved (`ensemble_db_pool_connections`, `ensemble_db_pool_checkout_seconds`, `ensemble_pg_leaked_txn_rows`) → **unresolved 3 → 0** |
| §8 alert table (13 rows; 11 reference daemon series) | 15 distinct series tokens | 13 + 2 legitimate histogram suffixes | 0 | unchanged (0 unresolved) |

**Catalog internal consistency:** verified consistent after H-2 — each family's detail section
reconciles with §1 (F7 `22→33`, F10 `20→21`, F15 `22→23`, F20 `56→57`, F21 `65→66` — each
difference explicitly reconciled in the catalog text), the §7 roll-up sums to the §1 total
(`26 + 62 + 223 + 210 = 521` daemon-origin), and §8.11's **7** histogram bucket sets match the
**7** histograms declared across F0.1 / F7 / F10 / F15 / F20 / F21.

**Edits — pass 1 (E1–E21, name / label / expression alignment in `plan-overview.md`)**

| # | Location | Before | After | Driver |
|---|---|---|---|---|
| **E1** | §4 P1a family list | `ensemble_llm_request_seconds{outcome}` | `ensemble_llm_request_total{outcome}` + `ensemble_llm_request_seconds` (no outcome label on the histogram) | MA-8 — catalog F15 (splitting the latency histogram by outcome would cost 8 × 14 = 112 series) |
| **E2** | §4 P1a family list | `ensemble_tool_calls_total{tool,outcome}` | `ensemble_tool_calls_total{category}` | m7 — catalog F20 (37 tool categories) |
| **E3** | §4 P1a family list | `ensemble_tool_call_duration_seconds{tool}` | `ensemble_turn_duration_seconds` (catalog F20 — the per-turn seam; a per-tool latency histogram is NOT shipped) | m7 — a per-tool latency histogram would cost 37 × 14 = 518 series |
| **E4** | §4 P1a family list | `ensemble_resource_*` (RSS/PSS/anon/swap/threads/fds/cumulative-CPU-usec, cgroup v2, per-child, per-instance) | `ensemble_resource_spine_tables_accepting_rows{table}` + `ensemble_resource_spine_rows_written_total{table}` (catalog F0.3 — the spine health gate; per-instance attribution stays in the resource_monitor PG tier) | MA-3 — F0.3 is the only catalogued resource family |
| **E5** | §4 P1a family list | `ensemble_spawn_total`, `ensemble_revive_total`, `ensemble_pause_total`, `ensemble_fanin_total` | `ensemble_instance_lifecycle_total{event}` (catalog F13 — 7 events, incl. revive-on-`send_message`) | m8 |
| **E6** | §4 P1a family list | `ensemble_gii_throttle_active` | annotated as uncatalogued in pass 1; **dropped in pass 2** (E29) | no catalog equivalent exists |
| **E7** | §4 P1a family list | `ensemble_process_cpu_seconds{name}` (textfile) | annotated with a phase note in pass 1; **moved to P2 in pass 2** (E30) | H-8 — catalog F22 owns the family in P2 |
| **E8** | §4 P1b family list | `ensemble_db_pool_connections{pool,state}` | `ensemble_db_pool_native{pool,quantity}` + `ensemble_db_pool_checked_out{pool}` + `ensemble_db_pool_waiting_clients{pool}` | m2 — catalog F4 + F5 (derived vs native kept apart) |
| **E9** | §4 P1b family list | `ensemble_db_pool_checkout_seconds{pool}` | annotated as deferred in pass 1; **retired in pass 2** (E29) | CR-4 — no checkout-latency family ships in v2 |
| **E10** | §4 P1b family list | `ensemble_db_pool_requests_errors_total{pool}` | `ensemble_db_pool_requests_errors_total{pool,kind}` | CR-4 — catalog F4's label set |
| **E11** | §4 P1b family list | `ensemble_pg_leaked_txn_rows{site}` | `ensemble_pg_leaked_txn{site}` | MA-13 — catalog F6 / §8.9 (6 site values) |
| **E12** | §4 P1b family list | `ensemble_maintenance_runs_total{operation,outcome}`, `ensemble_snapshot_usage_total{outcome}`, `ensemble_db_pool_status{pool}` | annotated as uncatalogued in pass 1; **dropped in pass 2** (E29) | no catalog family exists |
| **E13** | §4 P1c family list | `ensemble_job_queue_depth{state}` | `ensemble_job_queue_depth{admission_state}` | m3 — catalog F10 / §8.10 (the `AdmissionState` enum is the sole write authority) |
| **E14** | §4 P1c family list | `ensemble_message_queue_depth`, `ensemble_message_queue_oldest_age_seconds`, `ensemble_skill_usage_duration_seconds` | annotated as uncatalogued in pass 1; **dropped in pass 2** (E29) | no catalog family exists |
| **E15** | §4 P1c family list | `ensemble_event_total{kind}` | `ensemble_turn_events_total{source}` + `ensemble_instance_lifecycle_total{event}` | MA-12 / OQ-4 — catalog F20 + F13 already cover per-event counting (see H-11) |
| **E16** | §4 P2 family list | `ensemble_journal_op_seconds{op}` | `ensemble_journal_op_seconds` + `ensemble_journal_ops_total{op}` (the `op` label lives on the counter) | catalog F21 — the 26-value op vocabulary is on the counter, not the histogram |
| **E17** | §8 — `LoopStarved` | `histogram_quantile(0.95, rate(ensemble_loop_lag_seconds_bucket[5m])) > 2` | `ensemble_loop_lag_seconds{stat="p95"} > 2 and ensemble_loop_lag_samples > 150` | MA-4 — catalog F1: **no `_bucket` series exists** (300-sample rolling window, not a histogram) + §10.2 |
| **E18** | §8 — `CheckpointSlow` | `histogram_quantile(0.95, rate(ensemble_checkpoint_op_seconds_bucket[10m])) > 5` | `histogram_quantile(0.95, rate(ensemble_checkpoint_op_seconds_bucket{op="aget"}[10m])) > 5 and increase(ensemble_checkpoint_op_seconds_count[10m]) > 0` | CR-3 + G-9 — catalog §10.2 (bucket extension + the restart-safe pairing) |
| **E19** | §8 — `SweepHotLoop` | `rate(ensemble_sweep_cycle_seconds_count[5m]) * 300 > 2` | `rate(ensemble_sweep_cycles_total{lane="rdrs_mission_reconciliation"}[5m]) * 300 > 2` | MA-6 — the cycle metric is a **gauge** (no `_count` series); the counter is `ensemble_sweep_cycles_total` (catalog F12, §10.2) |
| **E20** | §8 — `BashSpin` | `rate(ensemble_process_cpu_seconds{name=~"promote_sh\|stage_sh\|launcher"}[5m]) > 0.9` | `ensemble_process_cpu_seconds{name=~"promote_sh\|stage_sh\|launcher"} > 0.9` | MA-7 — the textfile value is a per-tick delta gauge; `rate()` over it is meaningless (catalog F22, §10.2) |
| **E21** | §8 — `PoolExhausted` | `increase(ensemble_db_pool_requests_errors_total{pool=~"manager\|checkpoint"}[5m]) > 0` | `increase(ensemble_db_pool_requests_errors_total{pool="checkpoint"}[5m]) > 0` (+ the "manager renders no series" note in the incident column) | CR-4 + G-12 — the manager pool has no `requests_errors` equivalent, so the old label set implied a series that never exists |

**Edits — pass 2 (alignment pass, E22–E34: closes H-1…H-11)**

| # | Location | Before | After | Closes |
|---|---|---|---|---|
| **E22** | `plan-overview.md` §1 incident table — "Pool checkout timeouts / exhaustion" row | `ensemble_db_pool_connections{pool,state}` + `ensemble_db_pool_checkout_seconds{pool}` + `ensemble_db_pool_requests_errors_total{pool}` | `ensemble_db_pool_native{pool,quantity}` + `ensemble_db_pool_checked_out{pool}` + `ensemble_db_pool_waiting_clients{pool}` + `ensemble_db_pool_requests_errors_total{pool,kind}` (superset, CR-4 — no native checkout-timeout counter exists) | H-3 |
| **E23** | `plan-overview.md` §1 incident table — "P1 pool poisoned" row + leak-story paragraph | `ensemble_pg_leaked_txn_rows{site}`; site list `begin`/`instances_list`/`parent_lookup`/`queue_count`/`other` | `ensemble_pg_leaked_txn{site}`; the catalog's **closed 6-value site set** (`instance_read`/`instance_parent_lookup`/`job_queue_read`/`checkpoint_saver`/`agent_db_tool`/`other`, with `other` never dropped so the census total ties) | H-3, H-9 |
| **E24** | `plan-overview.md` §7 never-exported list | "census `site` is a fixed **5**-value enum"; "sweep lane **7**-value" | "census `site` is a fixed **6**-value enum — 5 named sites + the `other` residual"; "`site` (6-value), `lane` (13-value), `server` (5-value), status 10-value" | H-9 |
| **E25** | `plan-overview.md` §9 R3 | "p95 at **:185**" | "p95 at **:115**, p50 :116, max :114; window :59; :185 is `snapshot_histogram()`" | H-4 |
| **E26** | `plan-overview.md` §9 R7 | "`maintenance.py` VACUUM_FULL_TABLE_BYTES_SQL (**~:533**)" | "`maintenance.py:531-536` … sums **three** relations (`checkpoints` + `checkpoint_writes` + `checkpoint_blobs`; there is **no** single-table blob-size query) … a split-out of that one round trip" | H-5 |
| **E27** | `plan-overview.md` §9 R8 | "readiness.py 11-class taxonomy (**:56-79**)" | "readiness.py 11-class taxonomy (**:64-79**; grace subset :83-89, components :94-99)" | H-6 |
| **E28** | `plan-overview.md` §9 R13 / R14 | R13 → `ensemble_db_pool_status`; R14 → "MCP MachineStore roster — MCP server state source" | R13 → resolves to catalog F4's single pool-status family `ensemble_db_pool_native{pool,quantity}` (no second pool-status metric); R14 → **split**: server state from the per-boot quarantine registry (`mcp/init_guard.py:145-147`, published at `/readyz` → `detail.mcp`) and gateway session state from the MCPS MachineStore (`services/mcps_gateway/store.py:79`,`:103`) | H-10 (+ H-7 in-class for R13) |
| **E29** | `plan-overview.md` §4 family lists (P1a/P1b/P1c) + preamble ×3 | 8 uncatalogued names annotated in-line (`ensemble_gii_throttle_active`, `ensemble_db_pool_checkout_seconds{pool}`, `ensemble_maintenance_runs_total`, `ensemble_snapshot_usage_total`, `ensemble_db_pool_status`, `ensemble_message_queue_depth`, `ensemble_message_queue_oldest_age_seconds`, `ensemble_skill_usage_duration_seconds`) + the retired `ensemble_event_total{kind}` | **every uncatalogued name dropped** — an uncatalogued name is not shipped in v2; the two deliberately retired names survive only as parentheticals: "the checkout-latency histogram is retired, see decisions.md §B CR-4" and "the greenfield event-total vocabulary is retired, see decisions.md §H H-11". The 8 dropped signals are recorded in the closure table below as **deferred — they need a catalog family before they can ship**. | H-7 |
| **E30** | `plan-overview.md` §4 P1a family list + P1a task 3 | `ensemble_process_cpu_seconds{name}` listed in P1a; task 3 = "Wedge-capture counter hook **+ textfile process CPU**" | the textfile family removed from P1a (catalog F22 assigns it to **P2**); task 3 = "Wedge-capture counter hook … the shell textfile writer ships in **P2** (catalog F22 — external sampler owner)" | H-8 |
| **E31** | `plan-overview.md` §4 P1b task 1 + the CR-4 note | "DB pool gauges **+ checkout histogram** (wrap both pools)"; "Note (CR-4): `ensemble_db_pool_checkout_timeouts_total{pool}` is NOT shipped" | "DB pool gauges — native quantities + derived checked-out/waiting (both pools) … the checkout-latency histogram is (retired, see decisions.md §B CR-4)"; "Note (CR-4): the checkout-timeout counter is **retired** (see decisions.md §B CR-4) — psycopg_pool 3.3.1 exposes no native timeout counter" — the retired name is no longer written as a live metric | H-7 |
| **E32** | `plan-overview.md` §12 OQ-4 | "OQ-4: `ensemble_event_total{kind}` requires a new event-kind vocabulary; confirm with the events subsystem owner before P1c. (Greenfield.)" | "OQ-4 (**RESOLVED 2026-10-29**): **no new event-kind vocabulary is required** — per-event counting is already covered by `ensemble_turn_events_total{source}` (catalog F20) + `ensemble_instance_lifecycle_total{event}` (catalog F13); the events-subsystem owner confirms those two vocabularies at the P1c review" | H-11 |
| **E33** | `metric-catalog.md` header (compat line) | "Prometheus scrape model + Grafana, **OpenMetrics text**" | "Prometheus scrape model + Grafana, **Prometheus classic text exposition format** (NOT OpenMetrics — MA-16 corrects the v1 mislabel)" | H-1 |
| **E34** | `metric-catalog.md` F0.1 entry + §1 summary row + §1 total + §7 roll-up | F0.1 estimate "1 + 2 + 13 + 1 (the `ensemble_metrics_up{component}` info split) = **17**" vs the same entry's "(gauge, **no labels**)"; §1 row = 17; §1 total 522/527; §7 P0 27, daemon 522, grand ≈594, headroom 1 478 | **shape = no labels** (the family's own name line, Labels line "No other labels", degradation semantics, and the absence of any `component` vocabulary in §8 all agree); F0.1 estimate "1 + 2 + 13 = **16**"; §1 row = 16; §1 total **521 / 526**; §7 P0 **26**, daemon **521**, grand **≈593**, headroom **1 479** | H-2 (+ the arithmetic cascade that keeps the ≤2k proof true) |

**Edit accounting:** **34 edit sites** in total — 21 in pass 1 (E1–E21) and 13 in pass 2
(E22–E34). Pass 2 closed every remaining drift: `plan-overview.md` and `metric-catalog.md` now
agree on **every** metric name, label set and code reference in the reviewed scope, and the
only remaining discrepancies are the recorded waivers in §G (which are deliberate design
choices, not drift).

### H.2.1 Closure table — H-1 … H-11 (all CLOSED)

| # | Open item (as raised in pass 1) | Fix applied | Where | Status |
|---|---|---|---|---|
| **H-1** | Catalog header says "OpenMetrics text" | Replaced with "**Prometheus classic text exposition format** (NOT OpenMetrics — MA-16 corrects the v1 mislabel)" — now matches plan §3 and the MA-16 resolution | `metric-catalog.md` header (compat line) — edit E33 | **CLOSED** |
| **H-2** | F0.1's series estimate (`ensemble_metrics_up{component}`) contradicts the same family's "no labels" claim; §1 row and §7 roll-up disagreed with the family entry | **Shape picked: no labels** (name line, Labels line "No other labels", degradation semantics and the absence of any `component` vocabulary in §8 all agree; the `{component}` parenthetical was the outlier). F0.1 estimate → `1 + 2 + 13 = 16`; §1 row → 16; §1 total 522/527 → **521/526**; §7 P0 27 → **26**, daemon 522 → **521**, grand ≈594 → **≈593**, headroom 1 478 → **1 479** (cascade keeps the ≤2k proof true) | `metric-catalog.md` F0.1 + §1 + §7 — edit E34 | **CLOSED** |
| **H-3** | Plan §1 incident table named `ensemble_db_pool_connections{pool,state}`, `ensemble_db_pool_checkout_seconds{pool}`, `ensemble_pg_leaked_txn_rows{site}` | Pool row → `ensemble_db_pool_native{pool,quantity}` + `ensemble_db_pool_checked_out{pool}` + `ensemble_db_pool_waiting_clients{pool}` + `ensemble_db_pool_requests_errors_total{pool,kind}` (superset, CR-4 — no native checkout-timeout counter exists); leak row → `ensemble_pg_leaked_txn{site}`; leak-story prose → the catalog's closed 6-value site set | `plan-overview.md` §1 — edits E22, E23 | **CLOSED** |
| **H-4** | Plan §9 R3 cited the lag-probe p95 at `:185` | "p95 at **:115**, p50 :116, max :114; window :59; :185 is `snapshot_histogram()`" | `plan-overview.md` §9 R3 — edit E25 | **CLOSED** |
| **H-5** | Plan §9 R7 cited `maintenance.py` at `~:533` as a single-table blob query | "`maintenance.py:531-536` … sums **three** relations (`checkpoints` + `checkpoint_writes` + `checkpoint_blobs`; there is **no** single-table blob-size query) … a split-out of that one round trip" | `plan-overview.md` §9 R7 — edit E26 | **CLOSED** |
| **H-6** | Plan §9 R8 cited the readiness taxonomy at `:56-79` | "11-class taxonomy (**:64-79**; grace subset :83-89, components :94-99)" | `plan-overview.md` §9 R8 — edit E27 | **CLOSED** |
| **H-7** | Plan §4 task tables + family lists referenced the retired checkout histogram, `ensemble_event_total` and 8 uncatalogued names | Task 1 → "DB pool gauges — native quantities + derived checked-out/waiting … the checkout-latency histogram is (retired, see decisions.md §B CR-4)"; the CR-4 note now states "the checkout-timeout counter is **retired**" without writing the name; the family lists lost all 8 uncatalogued names (an uncatalogued name is **not shipped** in v2) and the retired event-total vocabulary survives only as a parenthetical | `plan-overview.md` §4 (P1a/P1b/P1c family lists + task tables) + preamble — edits E29, E31 | **CLOSED** — the 8 dropped signals (`ensemble_gii_throttle_active`, `ensemble_maintenance_runs_total`, `ensemble_snapshot_usage_total`, `ensemble_db_pool_status`, `ensemble_message_queue_depth`, `ensemble_message_queue_oldest_age_seconds`, `ensemble_skill_usage_duration_seconds`, and the checkout-latency histogram) are **deferred**: each needs a catalog family (F-number + vocabulary annex entry) before it can ship, and the reuse map's R6/R13 now point at the catalog families that replace them |
| **H-8** | Plan §4 P1a listed `ensemble_process_cpu_seconds` (textfile) while catalog F22 assigns the family to P2 | Removed from the P1a family list; P1a task 3 is now the wedge-capture counter hook only, with "the shell textfile writer ships in **P2** (catalog F22 — external sampler owner)" | `plan-overview.md` §4 P1a — edit E30 | **CLOSED** |
| **H-9** | Plan §7 said the census `site` is a "5-value" enum and "sweep lane 7-value" | "census `site` is a fixed **6**-value enum — 5 named sites + the `other` residual"; "`site` (6-value), `lane` (13-value), `server` (5-value), status 10-value" | `plan-overview.md` §7 (+ the §1 leak-story site list) — edits E23, E24 | **CLOSED** |
| **H-10** | Plan §9 R14 conflated catalog F17 (quarantine registry / server state) with F18 (MCPS MachineStore) | R14 split into two correct references: **server state** from the per-boot quarantine registry (`mcp/init_guard.py:145-147`, published read-only at `/readyz` → `detail.mcp`) and **gateway session state** from the MCPS MachineStore (`services/mcps_gateway/store.py:79`,`:103` → sessions/tombstones/audit). R13 (in-class) now resolves to F4's single pool-status family | `plan-overview.md` §9 R13 / R14 — edit E28 | **CLOSED** |
| **H-11** | Plan §12 OQ-4 asked for a new `ensemble_event_total{kind}` vocabulary | OQ-4 recorded as **RESOLVED 2026-10-29**: no new event-kind vocabulary is required — `ensemble_turn_events_total{source}` (catalog F20) + `ensemble_instance_lifecycle_total{event}` (catalog F13) cover per-event counting; the events-subsystem owner confirms those two vocabularies at the P1c review | `plan-overview.md` §12 OQ-4 — edit E32 | **CLOSED** |

### H.3 Commit

**Four path-limited commits, all on `feature/metrics-collection` (worktree
`agents-ensemble-wt-metrics-v2`):**

| Pass | Commit | Base | Content |
|---|---|---|---|
| 1 — original v2 plan | `cec78e0e881665450e1ca38c8bf555d915f4d54c` | `9bbef71c1f357d00f2a1dbd62e5c8057fb1620f1` | the three planning files as first authored (plan-overview + metric-catalog + decisions), plus the 21 pass-1 alignment edits |
| 2 — alignment pass | `ad3a331716823998dee567cb6dfbad700b59c854` | `cec78e0e881665450e1ca38c8bf555d915f4d54c` | the H-1…H-11 closure (edits E22–E34 + this document's refreshed §H) |
| 3 — architect doc (WS-B step 2) | `c0f0694396d118e26fb7b40946005cc8bac8c4d4` | `ad3a331716823998dee567cb6dfbad700b59c854` | `architecture-recommendation.md` — the **read-only input** to the v3 round (697 lines; §10's 28 amendments, §11's Q1/Q2, Appendix A's verification log). It is a companion, **not** edited by v3 |
| 4 — **v3 (this one)** | **HEAD of `feature/metrics-collection`** — a commit cannot contain its own sha, so it is reported verbatim in the task report and retrieved with `git rev-parse HEAD` | `c0f0694396d118e26fb7b40946005cc8bac8c4d4` | **waves 1+2** (amendments 1–28 applied across plan-overview + metric-catalog, uncommitted until now) **+ the v3 verification/fix pass** (the amendments that were missing or thin — see §I's `applied-with-adjustment` rows and §M's edit ledger E35–E76) **+ this document's §I–§M** |
| 5 — **correction round (this one)** | **HEAD of `feature/metrics-collection`** — sha reported in the task report, retrieved with `git rev-parse HEAD` | 51bbfc000 (v3) | **§N (Item A — macOS/WS-A correction) + §O (Item B — partition unification)** + §G closure (G-20) + §G additions (G-21/G-22) + §H/§M refresh + the final verification sweep; edits path-limited to the three planning files |

- **Committed paths (passes 1, 2 and 4 — path-limited):** `.agents/shared/planning/metrics-collection/`
  — exactly `plan-overview.md`, `metric-catalog.md`, `decisions.md`. **Pass 3** adds only
  `architecture-recommendation.md`. Nothing outside that directory is in any commit; the
  pre-existing stashes were not touched; **nothing was pushed**.
- **Verify:**

```bash
git rev-parse HEAD                 # the v3 commit (≠ c0f06943, ≠ ad3a33171, ≠ cec78e0e8)
git show --stat --oneline HEAD     # exactly three planning files, nothing else
git show --stat --oneline c0f0694396d118e26fb7b40946005cc8bac8c4d4   # pass 3 — architecture-recommendation.md only
git log --oneline -4
```

### H.4 Sign-off checklist for the reviewer

1. ☐ §B — 5 CRITICAL rows, every one pointing at a construct in §1/§3/§4/§5/§6/§7/§8 of
   `plan-overview.md` or a family in `metric-catalog.md`.
2. ☐ §C — 16 MAJOR rows, same check.
3. ☐ §D — 10 MINOR rows; the two waivers (m5, m9-compat-clause) carry explicit rationale.
4. ☐ §E — the 5 leader-ratified decisions are present **and** implemented in both sibling files.
5. ☐ §F — 18 reuse rows; for each, confirm the catalog family *builds on* the instrument (no
   row reads "rebuilt instead").
6. ☐ §G — 16 register entries; confirm each is traceable to the two files.
7. ☐ §H.1 — row counts (5 / 16 / 10 / 5 / 18 / 16) match the tables above.
8. ☐ §H.2 — the 34 alignment edits (E1–E21 pass 1, E22–E34 pass 2) are present across the
   three files, and the 11 items (H-1…H-11) are **CLOSED** per the §H.2.1 closure table.
9. ☐ §H.3 — all four commits are path-limited to the planning directory; the v3 commit contains
   exactly the three planning files (the architect doc is **not** in it); the tree is clean;
   nothing outside the planning directory changed; nothing was pushed.
10. ☐ **§I** — 28 rows, one per canonical §10 amendment (1–28), grouped A–G; every row's
    *implemented at* cell opens the named anchor in `plan-overview.md` / `metric-catalog.md`.
11. ☐ **§J** — the 5 architect-critical findings; for each, confirm the empty-series defect is
    closed at the cited code anchor.
12. ☐ **§K** — Q1 and Q2 answered with the architect's defaults, each recorded where it lives
    in the plan (§4 P1b risk 4 / §4 P2 risk 4).
13. ☐ **§L** — the CR-3 record correction is stated, and §B CR-3 / §F R1 point at it.
14. ☐ **§M** — companions, counts, §G additions G-17…G-20, the E35–E76 edit ledger, and the
    consistency-sweep result.

---

## I. v3 architecture round — the 28 amendments (canonical §10 numbering)

**Source.** `architecture-recommendation.md` §10 "LIST OF REQUIRED PLAN AMENDMENTS", numbered
**1–28**, grouped **A–G**. The numbering below is that canonical one — **not** the draft
"A1…A20" labels used inside the architect's prose (those are a *different* scheme; see §M.5
for the label normalization that reconciled the two files with the canonical numbering).

**How each row was produced.** Every amendment was **materially verified** against the two
sibling files **and** the worktree code before its row was written: the claimed anchor was
opened, the substance was confirmed, and the code citations the architect gave were
re-verified in `agents-ensemble-wt-metrics-v2`. Where the substance was **missing or thin**,
the amendment was **implemented first** (v3 has merge authority over both files) and the row
says so — `applied-with-adjustment` names what changed. **No amendment is waived; no row is
missing.** Count: **28**.

### I.A — Metric catalog: vocabularies and families (§10 group A, amendments 1–8)

| # | Prescription (architect §10) | HOW resolved in v3 | Implemented at (verified anchor) | Status |
|---|---|---|---|---|
| **1** | §8.6 / F20 / §1 / §7 — the tool-category vocabulary is **49**, not 37/38. Derive it at runtime from `get_tool_categories()`; add the omitted values plus a reserved `other`; update the F20 series estimate and the §1/§7 totals. Apply the same rule to every §8 vocabulary: **derived from code, never hand-listed.** | The vocabulary is **re-derived from code** — `get_tool_categories()` (`daemon/tools/_tool_registry.py:1598`) is the source. v3 **re-counted it independently**: **49 distinct categories across 254 `register_tool_category(` sites** (the architect's figure — confirmed). The label set is the runtime-derived set **+ a reserved `other`** (50 values); a tool outside the set folds to `other` and is counted in `ensemble_metrics_invalid_label_total{metric,label}`. The rule was **generalized**: §8 now opens with "every closed vocabulary in this annex is DERIVED FROM CODE, never hand-listed", citing the sweep-lane 7→13 drift as the precedent class. F20's estimate moved **57 → 69**; the §1 total and the §7 roll-up were re-based (see §M.4). | `metric-catalog.md` §8.6 (L891) · §8 opening rule (L832) · F20 labels (L711) + series estimate (L728–L733) · §1 F20 row (L184) + §1 totals (L188) + reconciliation basis (L191) · §7 roll-up (L804–L815) · §11 C27 (L1157); `plan-overview.md` §4 P1a family list (L142) | **applied** |
| **2** | F14 — change the observation site from `graph.py:3811-3812` to the **terminal-usage branch `:3776-3784`** (or the aggregated call site `:9176-9179`); mandate a **per-call dedupe**; add the "terminal-chunk only, never per-chunk" rule. | Both files now name the hook: `daemon/graph.py:3776-3784` (the `len(choices) == 0` path that carries the token counts and **returns early**), with `:9176-9179` as the aggregated alternative; `:3811-3812` is explicitly named as the **non-terminal, per-chunk** path that must **not** be hooked. A **per-call dedupe** is mandated (some OpenAI-compatible backends attach `usage` to every chunk ⇒ double counting). The rule "**terminal-chunk only, never per-chunk**" is stated verbatim. *v3 addition:* wave 2 carried this **catalog-only**; v3 added the **plan-side carrier** (P1a task 4) so the plan cannot be implemented from the stale hook. | `metric-catalog.md` F14 Source (L606) + §1 F14 row (L178) + §11 C28 (L1158); `plan-overview.md` §4 P1a task 4 (L150) + P1a risk 1 (L164) | **applied** |
| **3** | §8.13 + m1 — split the wake-outcome producer into (a) the `worker_pool.py` claim path and (b) the `graph.py:7319` episode seam; each computed once, each carried. | §8.13 now names **both** producers and the outcomes each can yield — (a) the claim path (`daemon/services/worker_pool.py:1381-1386`) yields `empty_claim`, `skipped_busy_instance`, `timeout_wake`, `claimed`, `notifications_sent`, `workers_woken_by_timeout`; (b) the episode seam (`daemon/graph.py:7319` `_detect_fresh_episode_reset`) yields `fresh_episode_reset`, `stale_episode`, `no_op_resume`. Each is computed **once** and carried on the same `ProcessingContext` carrier; neither re-derives from partial signals. *v3 addition:* the plan's P1c task / exit criteria / risk row named only **one** producer — v3 named both in all three. | `metric-catalog.md` §8.13 (L978) + producer (a) (L982) + producer (b) (L985) + §11 C29 (L1159); `plan-overview.md` §4 P1c task 5 (L215) + exit criteria (L220) + risk 1 (L229) | **applied** |
| **4** | §8.13 + m1 — replace "the exposition renderer reads the carried value and increments" with "**a live turn-end consumer increments the counter; the renderer only reads the accumulated counter**". | §8.13's consumer bullet is rewritten to that contract, with the reason stated (the carrier field lives on `ProcessingContext` and **dies with the message**; the renderer runs at scrape time and sees only the registry) and the blueprint's rule preserved verbatim — "**consumer-seam re-derivation is forbidden**". Mirrored in the plan's P1c task, exit criteria and risk row (v3 addition). | `metric-catalog.md` §8.13 consumer (L993) + §11 C29 (L1159); `plan-overview.md` §4 P1c task 5 (L215) + exit criteria (L220) + risk 1 (L229) | **applied** |
| **5** | F20 — define the counting unit ("one `tap_node_return` invocation") on the HELP text; specify the anchor pair for `ensemble_turn_duration_seconds` **or** drop that family; specify the tool→category lookup with an `other` fallback. | The counting unit is stated ("**one `tap_node_return` invocation**" — the tap fires at 5 sites and N+1 times per turn with N tool rounds; the `source` vocabulary is itself node-oriented, so "turn" is not misread as one-per-turn). The anchor pair is now **specified** — the elapsed time between the **entry tap** (`user_message_entry`, `message_tap.py:120-124`) and the **node-return tap**, carried on the §8.13 carrier and **never re-derived** at the consumer — with a **drop-condition**: a source with no entry-tap counterpart contributes **no observation** (absent ≠ zero) and the family is dropped for that source rather than shipped mislabelled. The tool→category lookup states the `other` fallback. *v3 additions:* the anchor was left as an unresolved "either/or" and the plan's P1a task 6 still said "latency **per tool**" (a per-tool latency histogram that does **not** ship) — both corrected. | `metric-catalog.md` F20 (L715–L733; anchor paragraph L728) + §11 C31 (L1161); `plan-overview.md` §4 P1a task 6 (L152) | **applied-with-adjustment** — the amendment allowed "specify the anchor pair **or** drop the family"; v3 specifies the pair and keeps a per-source drop-condition (the family keeps its 14 series). Recorded as **G-19**. |
| **6** | F7 + plan §1 — add a **task** to instrument the checkpoint write path at the existing `checkpoint_adapter.py` charge seam (`aput`/`aput_writes`); until it lands, narrow the declared `op` enum to the observable set and restate the write-side incident mapping. | The `op` enum is **narrowed to `aget`** in both files (catalog F7's label bullet; plan §1's write-side restatement), §1's write-side incident mapping is restated (writes are a stall/contention artifact, not throughput), **and a task now exists**: plan §4 **P1b task 8** extends `_charge_checkpoint_read` (`:371-374`) to `aput`/`aput_writes` (`:377-379`) — the seam **already exists**, so this is a wiring task, not a new instrument. The **risk row** (P1b risk 4) is the Q1 carrier (see §K). *v3 addition:* wave 2 carried only the risk row, not the task the amendment asks for. | `metric-catalog.md` F7 labels (L417) + write-path task (L435) + §11 C32 (L1162); `plan-overview.md` §1 write-side restatement (L14) + §4 P1b task 8 (L186) + P1b risk 4 (L200) | **applied** |
| **7** | F7 / §0.4 / §8.11 — after the write-path seam lands, re-derive the checkpoint `op` enum from **observed call sites** rather than inheriting the legacy HELP text (`:281-290`). | Stated in the catalog's F7 entry ("After the write-path seam lands, re-derive the `op` enum from observed call sites rather than inheriting the legacy HELP text") and carried into the new plan task 8's acceptance. §8.9 still shows the legacy **4-value** set with its source citation (`checkpoint_metrics.py:281-290`) — it is the **inherited** set, explicitly marked re-derivable, not a frozen contract. | `metric-catalog.md` F7 (L417–L421) + §8.9 checkpoint op (L927); `plan-overview.md` §4 P1b task 8 (L186) | **applied** |
| **8** | F0.2 — clarify that `ensemble_metrics_registry_measurements` counts **measurements**, not series, so dashboards cannot conflate it with the 521-series budget. | F0.2 carries an explicit clarification bullet: it counts **measurements** (registered measurement objects), **NOT** series, with the ≤2 k budget warning inline — and the CR-3 correction (registry wired to collectors) alongside it. | `metric-catalog.md` F0.2 (L232) + §11 C33 (L1163) | **applied** |

### I.B — Registry mechanics (catalog §0.2 / plan §3) (§10 group B, amendments 9–11)

| # | Prescription (architect §10) | HOW resolved in v3 | Implemented at (verified anchor) | Status |
|---|---|---|---|---|
| **9** | §0.2 + plan §3 — adopt the locking contract: per-metric locks (no global registry lock), **non-blocking render snapshot** (try-acquire → last-good published snapshot + `lock_contended` comment line), and **non-cumulative bucket counts** accumulated at render (O(1) observe). | All three clauses are in **both** files: per-metric locks only (no global registry lock — one wedged writer must not stall 26 families); render uses **non-blocking try-acquire** (bounded wait, e.g. 50 ms) and on contention serves the **last published snapshot** plus `# <family> unavailable: lock_contended`; writers update their family's state under its own short lock and atomically swap a published pointer (plain assignment, atomic under the GIL) — copy-on-publish; bucket counts are stored **non-cumulative** and accumulated at render, so `Histogram.observe` is O(1). *v3 addition:* the contract now **names the defect it fixes** — `render_metrics()` reaches series through the per-object getters (`.get()`), each of which takes that object's `threading.Lock`, so a wedged writer would make the **scrape hang** (the CR-1 failure mode inside the CR-1 mitigation). | `metric-catalog.md` §0.2 (L32, incl. the defect clause); `plan-overview.md` §3 locking contract (L52) + §6 matrix row (L295) + §4 P0 test plan (L127, wedged-writer simulation) | **applied** |
| **10** | §0.5 + plan §7 — make the never-exported list **executable**: registration-time enum pin, observe-time reserved bucket + `ensemble_metrics_invalid_label_total{metric,label}`, render-time allowlist predicate, **hard** cardinality cap, name regex `^ensemble_[a-z0-9_]+$`, label-value charset gate; add the poisoned-registry defence-in-depth test. | The three enforcement tiers are written out in **both** files (registration-time enum pin raising on a wrong label key; observe-time fold-to-`other` counted in `ensemble_metrics_invalid_label_total{metric,label}`; render-time allowlist predicate as the §7 hook), plus the **hard** cap, the name regex, the charset gate (no `=`, `,`, `"`, newline) and the poisoned-registry test. *v3 fix:* plan §7 still said the cap was "currently soft — make hard" while plan §5 already said **hard** — v3 reconciled the two (§7 now cites §5's hard cap). | `metric-catalog.md` §0.5 (L85–L99); `plan-overview.md` §7 (L301–L311) + §5 managed keys (L277) | **applied** |
| **11** | §0.3 / CR-3 — specify alias mechanics: dual exposition of one series, one-shot deprecation log, removal procedure with a Prometheus `absent()` guard, and migration of the four test files that reference the legacy names. | §0.3 carries all four mechanics: the alias is a **second exposition of the same series** (never a second measurement); a **deprecation log line at first emission, once per process**; a removal procedure with a documented `absent()` guard ("one release + one burn-in", or a recording rule); the legacy names are **alias-only, never canonical** (they violate the `^ensemble_` rule); and the **four test files** referencing `message_api_checkpoint_list_total` / `message_api_saver_op_latency_seconds` are named for migration. Aliases are registered at **registry-construction time** so a cold start still emits them (amendment 27). | `metric-catalog.md` §0.3 (L61–L78) + §0.2 freeze rules (L45); `plan-overview.md` §3 freeze rules (L68) | **applied** |

### I.C — Listener + kill-switch (plan §3/§4/§5/§6, catalog §0.7/§0.8) (§10 group C, amendments 12–16)

| # | Prescription (architect §10) | HOW resolved in v3 | Implemented at (verified anchor) | Status |
|---|---|---|---|---|
| **12** | §3 / catalog §0.7 — state the listener's **thread/loop topology** (dedicated thread, own socket, stdlib `http.server`) and the **render-never-blocks** rule; state the GIL split (I/O-bound wedge survives; **CPU-spin wedge does not** → CR-1's out-of-process witnesses remain mandatory). | The topology is stated as **Option A**: a **dedicated daemon thread** running stdlib `http.server.ThreadingHTTPServer` bound to `127.0.0.1`, started from the FastAPI lifespan; a shared-main-event-loop listener is **REJECTED** (it inherits the CR-1 failure). The render-never-blocks contract is restated in the same paragraph, and the **GIL split** is stated with its consequence: an I/O-bound wedge survives, a CPU-spin wedge does **not**, so CR-1's out-of-process witnesses (node_exporter textfile, `up==0`/`absent()`, `scrape_duration_seconds`) remain **mandatory**, not optional. | `plan-overview.md` §3 topology (L58) + §6 matrix CPU-spin row (L296); `metric-catalog.md` §0.7 (L116–L127) | **applied** |
| **13** | §4 P0 T1 — specify boot placement (inside the lifespan, after `resource_monitor` `api.py:2241-2288` and `llm_stream_watchdog` `:2212-2226`), bind-failure degradation (mirror `api.py:2282-2287`), and teardown order (stop event → `shutdown()` → `join(timeout=5)`, `daemon=True` thread). | All three are in plan §3 **and** in P0 task 1's acceptance: boot inside the **FastAPI lifespan**, after `resource_monitor` (`daemon/api.py:2241-2288`) and after `llm_stream_watchdog` (`:2212-2226`); a bind failure degrades the listener to **disabled + logged**, mirroring `daemon/api.py:2282-2287` (never abort boot — decision 3); teardown = stop event → `server.shutdown()` → `join(timeout=5)` → close, **after** the resource-monitor stop (`:3411-3433`) and the wedge-capture stop (`:3435-3464`), on a `daemon=True` thread so a wedged join cannot hang shutdown. | `plan-overview.md` §3 boot/teardown (L60) + §4 P0 task 1 (L95) | **applied** |
| **14** | §4 P0 T3 / §5 / catalog §0.8 — specify the **truthy set** (`{1,true,yes}`, case-insensitive; everything else OFF) and the layering rule: **env explicit-OFF always wins; env UNSET defers to the managed-key chain**; add the boot log line. | The truthy set is fail-closed and exhaustive ("`ture`", `on`, `enabled`, `0`, empty → OFF"); the layering table is written out (unset + managed key on ⇒ **ON**; unset + default ⇒ OFF; explicit env OFF always wins; explicit env ON overrides the managed-key default); the **one boot log line** is quoted verbatim ("`ENSEMBLE_METRICS_ENABLED` unset; deferring to `limits.metrics_enabled`"); and the per-request read stays (measured ~50–100 ns, dict-backed, no syscall — **no TTL cache**, which would defeat the emergency-off purpose). | `plan-overview.md` §4 P0 task 3 (L97) + §6 matrix row (L294) + §5 kill-switch (L265); `metric-catalog.md` §0.8 (L132–L139) | **applied** |
| **15** | §6 — extend the dark-families matrix with: **env-unset-but-managed-key-on**, **`lock_contended`**, **CPU-spin wedge** (→ external witnesses), and **non-Linux host** (`/proc` absent ⇒ F2 + F0.3 dark, listener still live). | **v3 addition — the four rows were absent from the wave-1/2 result.** All four are now in the plan §6 matrix: env-unset + managed-key-on ⇒ **not dark** (the managed-key chain decides; one boot log line); a writer holding a metric lock ⇒ **that family only** is skipped with `# <family> unavailable: lock_contended` + the last published snapshot; a **CPU-spin wedge** ⇒ the listener thread is starved, so **only** the out-of-process witnesses remain; a **non-Linux host** ⇒ F2 + F0.3 dark (`read_proc_snapshot` returns `None`) while **the listener still boots** and every other family renders. | `plan-overview.md` §6 dark-families matrix (L294–L297) | **applied** (v3 — rows were missing) |
| **16** | §4 P0 test plan — add the **four missing tests**: origin-guard 403, bind-failure degradation, wedged-writer simulation, kill-switch typo parsing; specify the **injectable-socket harness** so no test requires a real port. | The four tests are written into P0's test plan with their assertions: origin-guard 403 (handler-level request, non-loopback origin → 403 `origin_not_trusted`); bind-failure degradation (inject a failing bind → listener disabled + logged, **boot continues**); **wedged-writer simulation** (acquire a metric's lock in the test thread, then render → the scrape still returns **within 100 ms**, the family is skipped with `lock_contended`); kill-switch typo parsing (`ENSEMBLE_METRICS_ENABLED=ture` → OFF). **Harness:** an injectable socket factory (ephemeral port 0, or no socket) so no test needs a real port, and `render_metrics()` stays pure/port-free. | `plan-overview.md` §4 P0 test plan (L127) | **applied** |

### I.D — Census (catalog §9, plan §4 P1b) (§10 group D, amendment 17)

| # | Prescription (architect §10) | HOW resolved in v3 | Implemented at (verified anchor) | Status |
|---|---|---|---|---|
| **17** | §9 — add: **dedicated daemon-thread execution home**; `statement_timeout = 5 s` / `connect_timeout = 3 s`; **self-exclusion of `ensemble-metrics-census` only** (wedge-capture rows counted under `other`); **SQL-side** `CASE`/`LIKE` matching in one round trip with **six per-site unit tests**; the **sustained (5-minute)** `other`-growth signal; and an honest note that blank `pg_stat_activity.query` (extended protocol) lands in `other`. | All six elements are in the catalog's census annex: §9.1's connection table carries the transport (direct `psycopg.connect`, no pool), `application_name`, **5 s / 3 s** timeouts, the **dedicated daemon thread** `CensusSampler` (not the listener thread, not the event loop — it would die in a wedge), the self-exclusion rule (**only** the census's own connection; `wedge-capture` rows counted under `other`, because excluding them would transiently break the sum-ties invariant), the ≥15 s cadence and the never-raises failure posture; §9.5 states **server-side** `CASE`/`LIKE` matching with **one unit test per site (6)** and the honest residual risk (blank `pg_stat_activity.query` under the extended protocol lands in `other` — the sum still ties, the attribution under-counts); §9.6 states the **sustained 5-minute** `other`-growth signal (a single-tick spike is an incident-correlated capture, not drift). The plan carries the execution-home task (P0 task 10). | `metric-catalog.md` §9.1 (L1005–L1021; timeouts L1013/L1014; execution home L1015; self-exclusion L1016) + §9.5 (L1043) + §9.6 (L1051) + §11 C34 (L1164); `plan-overview.md` §4 P0 task 10 (L104) | **applied** |

### I.E — Storage split + durability (plan §3, catalog §10) (§10 group E, amendments 18–21)

| # | Prescription (architect §10) | HOW resolved in v3 | Implemented at (verified anchor) | Status |
|---|---|---|---|---|
| **18** | §3 — state the boundary as a **rule**: per-instance attribution = PG-only via `/api/resources/*`; every daemon-origin `/metrics` family is **aggregate**; **no daemon-origin family carries a per-instance label** (add to catalog §8 as a cardinality rule). | The rule is stated in plan §3 and **enforced** as a cardinality rule at the head of the catalog's §8 vocabulary annex (so it is a rule, not prose). Both name the same three clauses: per-instance attribution = PG-only via `/api/resources/*`; every daemon-origin family is aggregate; no daemon-origin family carries a per-instance label. | `plan-overview.md` §3 boundary rule (L62); `metric-catalog.md` §8 cardinality rule (L834) | **applied** |
| **19** | §3 / §9 R5 — state the **PG retention ladder** (raw 48 h / minute 14 d / hour 180 d; `config.py:818-888`) and the **interplay rule** (Prometheus governs alerting and 30 d+; PG governs per-instance views; >90 d answered from the 180 d tier). | Plan §3 states the full ladder with the config citations (`retention_raw_minutes=2880`, `retention_minute_hours=336`, `retention_hour_days=180` — `daemon/config.py:818-888`) **and** the interplay rule; plan §2's retention sentence now distinguishes the two tiers (Prometheus 30 d raw / 90 d downsample; PG 48 h / 14 d / 180 d) and states that a Prometheus **process** restart preserves history (WAL replay) while **storage** loss does not. | `plan-overview.md` §3 retention ladder (L64) + §2 (L38); `metric-catalog.md` §10.0 (L1068–L1079) | **applied** |
| **20** | §3 — add the **two-sampler consistency rule** (wedge sampler vs resource-monitor sampler read `/proc` independently; eventual consistency within one sample interval). | Plan §3 states it: one shared reader module (`resource_monitor/proc_readers.py`), **two independent loops**, **not** required to agree byte-for-byte, consistency eventual within one resource-monitor sample interval — "stated so a future reader does not 'fix' a non-bug". | `plan-overview.md` §3 two-sampler consistency (L66) | **applied** |
| **21** | catalog §10 — add the **three-scenario restart-loss table** and state that a Prometheus *process* restart preserves history (WAL replay) while *storage* loss does not. | Catalog §10.0 carries the table (3 scenarios × the A/B/C idioms + the Prometheus TSDB row) and the sentence "A Prometheus **process** restart is **not** a history loss (WAL replay); only **storage** loss is." The mitigation column states `remote_write` to a durable store is out of v2 scope (RK-16 → §G G-18). | `metric-catalog.md` §10.0 (L1068–L1079); `plan-overview.md` §2 (L38) | **applied** |

### I.F — Phasing (plan §4) (§10 group F, amendments 22–26)

| # | Prescription (architect §10) | HOW resolved in v3 | Implemented at (verified anchor) | Status |
|---|---|---|---|---|
| **22** | §4 P0 exit criteria — split into **P0-core** (listener, registry, kill-switch, census discipline, vocabularies, managed keys) and **P0-spine** (the 4-table gate, owned by the WS-A `rm-flusher-fix` worktree); resolve the contradiction with P0 risk 1. | Plan §4 carries the split: **P0-core** ships independently; **P0-spine** is the "4 tables accepting rows" gate, owned by the parallel `rm-flusher-fix` worktree (WS-A) and tracked as a **separate gate** — reconciling P0 risk 1's "P0 can ship without the resource spine" with the exit criterion. The **gate criteria survive verbatim** inside the split: `resource_daemon_bucket`, `resource_instance_sample`, `resource_pid_map`, `resource_limit_policy` all accept rows. | `plan-overview.md` §4 P0 exit-criteria split (L119–L125) + P0 risk 1 (L134) + catalog F0.3 exit criterion (L255–L263) | **applied** |
| **23** | §4 P0 — add the **worker-thread-naming** task (`worker_pool.py:253`) so F2 is honest from first render; add the **census execution-home** task. | Both are P0 tasks with acceptance criteria: task 9 names worker threads at construction (`name=f"worker-{lane}-{worker_id}"`; the lane vocabulary `default`/`chat` at `worker_pool.py:207`) so `thread_group="worker_pool"`/`"chat_worker_pool"` no longer folds into `other`; task 10 gives the census its execution home (dedicated daemon thread, `CensusSampler`, `daemon=True`, `time.monotonic` tick — not the listener thread, not the event loop) with `statement_timeout` 5 s / `connect_timeout` 3 s. | `plan-overview.md` §4 P0 tasks 9–10 (L103–L104) + catalog F2 prerequisite (L307–L313) | **applied** |
| **24** | §4 P0 exit criteria / F0.4 — add the **versioned vocabulary-amendment procedure** (bump `ensemble_metrics_vocabulary_version`); state that a vocabulary bump changing a literal label value in shipped PromQL is a **breaking alert change**. | Both sides carry it: plan §4's P0 exit criteria (label **names** + closed enums pinned to current code at P0; any vocabulary change bumps `ensemble_metrics_vocabulary_version`; a bump that changes a literal label **value** appearing in shipped PromQL is a breaking alert change and needs a stated compatibility rule) and catalog F0.4 (the freeze marker family + the same rule, paired with the plan). | `plan-overview.md` §4 P0 exit criteria (L114–L117); `metric-catalog.md` F0.4 (L256–L274) | **applied** |
| **25** | §4 P2 T1 — move the **minimal textfile writer** to **P1b** so BashSpin has a data source before the P2 alert rules land. | The **minimal** writer (`ensemble_process_cpu_seconds`) is now a P1b family + P1b task 7 ("only the BashSpin data source"); P2 keeps the full exporter/textfile work and says so explicitly ("`ensemble_process_cpu_seconds{name}` moved to P1b per A25"). *v3 addition:* the **catalog** now records the phase split on the F22 family entry and under the §7 roll-up table, so the plan's P1b placement no longer contradicts the catalog's F22 phase cell (this was the one **wave-induced** phase divergence — see §M.6 / H-12). | `plan-overview.md` §4 P1b family list (L172) + P1b task 7 (L185) + P2 note (L237); `metric-catalog.md` F22 phase (L790) + §7 phase-split note (L821) | **applied** |
| **26** | §4 P2 T5 — state that **alert authoring depends on the referenced families' completion** (dashboards may be drafted earlier). | Stated twice: the §4 coupling note (P2 alert authoring depends on the referenced families' completion — e.g. `SweepHotLoop` depends on the `lane` vocabulary pinned in P1a/b/c; **dashboards may be drafted earlier**; the coupling map marks P0↔P2 "loose" for the textfile dir only) and P2 task 5's acceptance. | `plan-overview.md` §4 coupling note (L84) + P2 task 5 (L246) | **applied** |

### I.G — Cross-cutting (§10 group G, amendments 27–28)

| # | Prescription (architect §10) | HOW resolved in v3 | Implemented at (verified anchor) | Status |
|---|---|---|---|---|
| **27** | catalog §0.2 / plan §3 — add the **freeze rules**: import the listener on the **lifespan path** (not `api.py` module top level); register aliases at **registry-construction time** so a cold start emits them. | Both rules are stated in both files: the listener is imported on the **lifespan path** (not `daemon/api.py` module top level) so the PyInstaller analyzer pulls it; aliases are registered at **registry-construction time** (not lazily) so a cold start still emits the deprecated names for the remainder of their one-release window. | `plan-overview.md` §3 freeze rules (L68); `metric-catalog.md` §0.2 freeze rules (L45) | **applied** |
| **28** | plan §4 P0 T4 / catalog §0.2 — restate the off-loop discipline as "**no blocking work, no I/O, no logging, no per-chunk work on the event loop**" + ≤1 clock read per window; state that metric increments inside `message_tap` must sit within the tap's existing `try/except`. | The blanket "never adds event-loop work" claim is retracted in both files and replaced by the four-part rule plus "**≤1 `time.perf_counter()` read per observation window**"; the `message_tap` rule names the tap's existing `try/except` (`daemon/services/message_tap.py:308-309`) so a metric error cannot produce per-turn WARNING noise; both files carry the measured budget (≈4 µs added work per tool-round turn = 8 counter incs + 2 histogram observes, **< 0.01 %** of a multi-second turn; the tap's own DB write exceeds any metric cost by 3–4 orders of magnitude). | `plan-overview.md` §3 off-loop discipline (L54); `metric-catalog.md` §0.2 off-loop discipline (L50–L59) | **applied** |

**§I count check:** group A = 8 (1–8) · B = 3 (9–11) · C = 5 (12–16) · D = 1 (17) · E = 4 (18–21) ·
F = 5 (22–26) · G = 2 (27–28) → **28 rows, no gaps, no merges, none waived.**

---

## J. The five architect-critical findings — defects that ship a well-formed but EMPTY series

These are the architect's §0/§9 headline findings: **each would have shipped a series that
looks right and contains nothing**, and **no phase exit criterion would catch it**. Each was
re-verified against the worktree code before the row was written. Status: **5/5 resolved**.

| # | Finding (architect) | Why it would ship silently wrong | HOW resolved in v3 | Final anchor (verified in-tree) | Status |
|---|---|---|---|---|---|
| **J-1** | **F14's cited hook misses the terminal usage chunk** — the family would ship zero/near-zero. | The catalog cited `daemon/graph.py:3811-3812` as where `usage_metadata` is attached. That is the **non-terminal** path, reached only when the chunk also carries choices. A terminal usage chunk (the shape that carries the actual token counts) takes the `len(choices) == 0` branch, is attached through the **constructor**, and **returns early** — so a counter hooked only at the cited line never fires. Worse, the cited line is **per-chunk** (the converter runs per chunk) — the opposite of the plan's hot-path rule. | The observation site is now the **terminal-usage branch `daemon/graph.py:3776-3784`** (or the aggregated call site `:9176-9179`), with a mandatory **per-call dedupe** (some OpenAI-compatible backends put `usage` on every chunk ⇒ double counting) and the explicit rule "**terminal-chunk only, never per-chunk**". Both the catalog family entry **and** the plan's P1a task now carry it. | Verified in-tree: `graph.py:3776` = `if len(choices) == 0:` → constructor attach `:3778-3780` → `return generation_chunk` **`:3784`**; `graph.py:3811-3812` = the non-terminal `message_chunk.usage_metadata = usage_metadata`. Plan: `metric-catalog.md` F14 (L606) + §1 F14 row (L178) + C28 (L1158); `plan-overview.md` §4 P1a task 4 (L150) | **resolved** |
| **J-2** | **`ensemble_wake_turns_total` never counts** — the LostWake alert would be blind. | §8.13's consumer was "the exposition renderer reads the carried value and increments" — but the carrier field lives on `ProcessingContext` and **dies with the message**; the renderer runs at **scrape time** and sees only the registry. And the named producer could yield **at most 2 of the 7** outcomes (the other five live at different seams). | **Two producers**, each computed **once** and carried: (a) the **claim path** `daemon/services/worker_pool.py:1381-1386`, (b) the **episode seam** `daemon/graph.py:7319` `_detect_fresh_episode_reset`. The consumer is now "**a live turn-end consumer increments the counter; the renderer only reads the accumulated counter**" — with the blueprint's "consumer-seam re-derivation is forbidden" rule preserved. The plan's P1c task/exit/risk now name both producers and the consumer. | Verified in-tree: `worker_pool.py:1381-1386` = the four claim-path counters (`notifications_sent`, `empty_claim_attempts`, `workers_woken_by_timeout`, `claims_skipped_due_to_busy_instance`); `graph.py:7319` = `def _detect_fresh_episode_reset(...)`. Plan: `metric-catalog.md` §8.13 (L978, L982, L985, L993) + C29 (L1159); `plan-overview.md` §4 P1c task 5 (L215) + exit (L220) + risk 1 (L229) | **resolved** |
| **J-3** | **The tool-category closed enum is falsified** — 37/38 claimed vs **49** verified. | The catalog stated the 37-value list was "the complete set of `register_tool_category(...)` calls". Under a closed-enum registry an unregistered value either **raises at observe** (F20 breaks) or **silently inflates cardinality** past the series budget — and the budget itself was computed from the falsified list. | The vocabulary is **derived at runtime** from `get_tool_categories()` (`daemon/tools/_tool_registry.py:1598`), with a **reserved `other`** for the tool→category lookup (a tool outside the set folds to `other` and is counted in `ensemble_metrics_invalid_label_total{metric,label}`), plus a cross-check test. The lesson was **generalized**: §8 now opens with "every closed vocabulary is derived from code, never hand-listed" (the sweep-lane 7→13 drift is cited as the precedent class). v3 **re-counted independently**: **49 distinct categories across 254 sites** — the architect's figure confirmed. | Verified in-tree: `daemon/tools/_tool_registry.py:1598` = `def get_tool_categories(...)`; 254 `register_tool_category(` sites; 49 distinct names (re-counted by the v3 round). Plan: `metric-catalog.md` §8.6 (L891) + §8 rule (L832) + F20 (L711, L728) + C27 (L1157) | **resolved** |
| **J-4** | **`ensemble_checkpoint_op_seconds{op="aput"}` can never fire** — the declared `op` enum over-promises. | `time_saver_op` has exactly **one** non-test caller — `daemon/persistence.py:509` (`aget`). The checkpoint **write** path runs through `daemon/checkpoint_adapter.py:377-379` (`aput` → `scrub_saver_write_args` → `_call_with_retry`) and never touches `checkpoint_perf`, so the family would declare four `op` values and emit one. | The `op` enum is **narrowed to the observable set** (`aget`) until the **existing** charge seam is extended: `checkpoint_adapter.py` already carries `_charge_checkpoint_read(...)` (`:371-374`) on `aget_tuple` — extending it to `aput`/`aput_writes` is a **wiring** task, not a new instrument. That task now exists (**P1b task 8**) with its **own risk row** (P1b risk 4 — the Q1 default, §K). Plan §1's write-side incident mapping is restated (writes are a stall/contention artifact, not throughput). | Verified in-tree: `persistence.py:509` = `await time_saver_op("aget", ...)` — the only `time_saver_op(` call site; `checkpoint_adapter.py:371-374` = `_charge_checkpoint_read(args, kwargs, result)`; `:377-379` = `aput` → `scrub_saver_write_args` → `_call_with_retry` (**no** charge). Plan: `metric-catalog.md` F7 (L417, L435) + C32 (L1162); `plan-overview.md` §1 (L14) + P1b task 8 (L186) + risk 4 (L200) | **resolved** |
| **J-5** | **Render can block on a wedged writer** — CR-1 reproduced inside the CR-1 mitigation. | `daemon/checkpoint_metrics.py` holds a **per-object** `threading.Lock` (`Counter` `:79`, `Histogram` `:182`), and `render_metrics()` (`:304`) reaches series through `.get()` (`:95-104`, `:224-248`), which **takes that lock**. A wedged thread holding one metric's lock would make the **scrape hang** — the exact failure the listener exists to avoid. | The locking contract: **per-metric locks only, no global registry lock** (one wedged writer must not stall all families); **render never blocks** — non-blocking **try-acquire** (bounded wait, e.g. 50 ms) → serve the **last published snapshot** + `# <family> unavailable: lock_contended`; **copy-on-publish** (writers swap a published pointer — a plain assignment, atomic under the GIL); **non-cumulative bucket counts** accumulated at render so `observe` is O(1). v3 also added the **defect-naming clause** (the contract now states what it fixes) and the matrix row + the wedged-writer test. | Verified in-tree: `checkpoint_metrics.py:79` / `:182` = the per-object `threading.Lock`; the getters take it under `with self._lock:` (`:95-104`, `:224-248`); `:304` = `render_metrics()`. Plan: `metric-catalog.md` §0.2 (L32); `plan-overview.md` §3 (L52) + §6 matrix (L295) + P0 test plan (L127) | **resolved** |

---

## K. Leader decisions — the architect's two open questions (Q1/Q2)

Both were resolved by the **planner, adopting the architect's stated defaults** (the architect
framed both as "genuinely the leader's call" and supplied an actionable default for each;
per the WS-B v3 mission instruction the planner adopts those defaults).

| # | Question (architect §11) | Architect's default (rationale) | **v3 answer (as recorded)** | Where it lives |
|---|---|---|---|---|
| **Q1** | **Checkpoint write-path instrumentation — how much write-path risk is acceptable?** A5 offers two shapes: extend the existing `checkpoint_adapter.py` charge seam to `aput`/`aput_writes` (small, but it touches the checkpoint **write** path — the path under stress during the very incidents this plan exists to diagnose), or narrow the family to the observable `aget` set and restate the write-side incident mapping in §1. | *"Default: instrument the adapter seam in P1b with its own risk row — the read path is where the incident actually lives (the plan's corrected §1 'Why'), but an unobservable write path is a hole an operator will trust wrongly."* | **ADOPTED — extend the existing `checkpoint_adapter.py` charge seam to `aput`/`aput_writes`, instrumented in P1b with its own risk row.** The seam already exists (`_charge_checkpoint_read`, `:371-374`), so this is wiring, not a new instrument; but it touches the write path under stress, so it is carried as a **P1b task (task 8) with a dedicated risk row (P1b risk 4)** — Medium/Medium, mitigated by keeping the declared `op` enum narrowed to `aget` until the seam lands. | `plan-overview.md` §4 **P1b task 8** (L186) + **P1b risks row 4** (L200) + §1 write-side restatement (L14); `metric-catalog.md` F7 write-path task (L435) + C32 (L1162) |
| **Q2** | **Prometheus storage-loss exposure.** v2 assumes a single local Prometheus (30 d raw / 90 d downsample); a host loss takes the entire alerting history with it, and no durable remote store is in scope. | *"Default: accept for v2 and record it as a known limitation (RK-16); remote_write is a separate project. Confirm that deferral is acceptable, since the deploy target (host binaries) makes host loss the dominant scenario."* | **ADOPTED — accept a single local Prometheus TSDB for v2; host-storage loss is recorded as a known limitation (RK-16); `remote_write` to a durable store is a separate project, explicitly out of scope for v2.** The limitation is carried in three places so it cannot be lost: the P2 risk row, the catalog's §10.0 mitigation column, and the §G register (G-18). The deploy target (host binaries — decision 1) is what makes host loss the dominant scenario, which is why the deferral is recorded rather than left implicit. | `plan-overview.md` §4 **P2 risks row 4** (L261) + §2 retention (L38); `metric-catalog.md` §10.0 (L1068–L1079); `decisions.md` **§G G-18** |

---

## L. Record correction (CR-3 reuse story) — the dispatch brief's claim was wrong

**The claim that was wrong.** The dispatch brief (and v2's `decisions.md` §F R1 / §B CR-3
wording) said `checkpoint_metrics.py`'s singletons have **"zero non-test callers"** — i.e. an
unwired registry.

**The truth (architect Appendix A #4, re-verified by v3 in-tree).** The two singletons **DO**
have a production consumer:

- `daemon/checkpoint_perf.py:28-30` — **imports** `checkpoint_list_total` and
  `saver_op_latency_seconds` from `daemon/checkpoint_metrics.py`;
- `daemon/checkpoint_perf.py:190` and `:198` — **observe** into the histogram on the log-gated
  and ungated paths;
- `daemon/checkpoint_perf.py:233` — **increments** `checkpoint_list_total`.

**Only `render_metrics()` itself is unwired** (zero non-test callers in production — the sole
test caller is `tests/unit/persistence/test_checkpoint_perf_logging.py:1063`).

**The v3 wording, used everywhere from here on:** **"registry wired to collectors, exposition
unwired."** Not "unwired registry". The two halves are different facts and the catalog now
states both.

**Where the corrected wording appears (v3):** `metric-catalog.md` §0.2 (L26–L30), §0.2's
off-loop paragraph (L53–L59), F0.2 (L232–L238) and §11 **C30** (L1160); `plan-overview.md` §3
listener paragraph (L58) and §1's write-side restatement (L14). The §11 corrections log records
the claim and the correction side by side.

**v2 history is annotated, not rewritten:** §B's **CR-3** row and §F's **R1** row each carry a
one-line pointer to this section (`⚠️ v3 record correction: see §L`); their original wording
stands.

---

## M. v3 companions, counts, waiver additions, edit ledger and consistency-sweep result

### M.1 Companions (4 files)

| File | Lines | Role | In the v3 commit? |
|---|---|---|---|
| `plan-overview.md` | **411** | the phase plan (P0/P1a/P1b/P1c/P2), the alert table, the managed keys, the degradation contract | **yes** |
| `metric-catalog.md` | **1339** | the per-family spec + the 8 vocabulary annexes + the census annex + the corrections log + §13 platform matrix | **yes** |
| `decisions.md` | **898** | the reviewer-facing map (v2 §A–§H, v3 §I–§M, correction round §N–§O) | **yes** |
| `architecture-recommendation.md` | **697** | the architect's review — the **input** to v3 (§0 verdict, §10 the 28 amendments, §11 Q1/Q2, Appendix A the verification log). **Read-only for v3** — committed separately at `c0f06943` and **unmodified** | **no** — must not appear in the v3 commit |

### M.2 Counts

| Artifact | Count |
|---|---|
| Council findings (v2 §B/§C/§D) | **31** (5 CRITICAL + 16 MAJOR + 10 MINOR) — unchanged |
| Leader-ratified decisions (§E) | **5** — unchanged in substance |
| Reuse ledger rows (§F) | **18** — unchanged |
| Waiver / limitation register (§G) | **20** (16 + v3's G-17…G-20) |
| Architect amendments (§I) | **28** (canonical §10 1–28, groups A–G) — **none waived** |
| Architect critical findings (§J) | **5** — all resolved |
| Open-question resolutions (§K) | **2** (Q1, Q2) — architect's defaults adopted |
| Record corrections (§L) | **1** (CR-3 reuse story) |
| Cross-file edits | **34** (v2: E1–E34) + **44** (v3: E35–E78) = **78** |
| Open items | **11 closed** (H-1…H-11) + **H-12 RESOLVED by §O** (partition unified) |
| Correction round (§N/§O) | Item A platform/WS-A (A1–A5) + Item B partition unification (B) — 2 items recorded |
| Waiver / limitation register (§G) | **22** (G-1…G-16 + G-17…G-20 [G-20 closed] + G-21/G-22) |
| Catalog §12 open items carried / plan §12 open questions carried | 6 of 6 / 4 of 4 |

### M.3 §G additions in v3

| # | Waived / limited | One-line |
|---|---|---|
| **G-17** | Tool-category label set is runtime-derived (49 + reserved `other`), never hand-listed | derivation is the contract; the closed list is deliberately not written out |
| **G-18** | Single local Prometheus TSDB — host-storage loss is unrecoverable (RK-16) | accepted for v2; `remote_write` is a separate project |
| **G-19** | `ensemble_turn_duration_seconds` anchored (entry-tap → node-return) with a per-source drop-condition | the anchor pair is specified; a source that cannot be anchored contributes no observation |
| **G-20** | Plan §4 work-package phase lists vs catalog §1/§7 ship phases — two partitions | **RESOLVED by §O (Item B)** — partition unified, 1:1, zero exceptions |
| **G-21** | T3 per-child RSS/CPU attribution on macOS is a decided non-goal (A2) | per-child dark on Darwin; future per-pid Darwin story is a new, separately-scoped decision |
| **G-22** | Platform-field nuances: hw_peak cross-platform (ru_maxrss), anon proxy (rss_kb), swap_kb unwired everywhere | documented degradations, consumer-visible shape pinned (§13.3) |
| **G-20** | Plan §4 work-package phase lists vs catalog §1/§7 ship phases — two partitions | the catalog is authoritative for the roll-up; the 35 pre-existing divergences are enumerated and **deferred** (structural) |

### M.4 The series roll-up, reconciled (v2 → v3)

| | v2 (`cec78e0e8` + `ad3a33171`) | v3 | Why |
|---|---|---|---|
| P0 | 26 | 26 | — |
| P1a | 62 | 62 | — |
| P1b | 223 | 223 | — |
| P1c | 210 | **222** | **F20 57 → 69** (tool categories 37 → 49 + reserved `other` = +12) |
| **Daemon-origin total** | **521** | **533** | 521 + 12 |
| Shell-origin (textfile) | 5 | 5 | — |
| Exporter-origin | ≈67 | ≈67 | measured separately, outside the daemon budget |
| **Grand total on the Prometheus server** | ≈593 | **≈605** | — |
| **Budget** | ≤2 000, 26 % consumed, headroom 1 479 | ≤2 000, **≈27 %**, headroom **1 467** | 2 000 − 533 |

**Basis (now stated in the catalog, so the proof is checkable):** §7 counts the §1 row values,
with the one **range** cell (**F7** `22→33`) taken at its **today** value **22** — the bytes
histogram's +11 arrives with the P1b byte wiring. Row sums: 26 + 62 + 223 + 222 = **533**.

> ⚠️ **A v3 correction to the wave-2 hand-off.** The wave-1/2 result carried the daemon-origin
> total as **534** with P1c = 223. The §1 rows sum to **222** for P1c (F14 3 + F15 23 + F16 5 +
> F17 40 + F18 9 + F19 7 + F20 69 + F21 66), so **534 was off by one** — a `210 + 12 = 222`
> slip in the hand-off arithmetic, not a substantive disagreement (the headroom moves by one
> series and the percentage does not move). v3 reconciles the roll-up to **533 / 222 / ≈605 /
> headroom 1 467** and records the basis so a reviewer can recompute it in one line.

### M.5 Label normalization (STEP 3) — 20 relabels, E35–E54

The two files mixed **three** label schemes: the architect's canonical §10 numbering (1–28),
the architect's own **draft** "A1…A20" labels used inside its prose (a *different* numbering
— e.g. its draft "A15" = the listener topology, whose canonical number is **12**), and the
council ids (CR-1/MA-1/m1). Every inline `A<n>` was **content-matched** to the canonical §10
numbering and relabelled to `A<n>` where **n is the §10 number**. **Relabel only — no content
changes.** These already matched and were **left alone**: **A1** (tool-category derivation),
**A2** (F14 terminal hook), **A3/A4** (producer split / live consumer), **A5** (F20 counting
unit), **A8** (measurements ≠ series), **A9** (locking contract), **A10** (executable
never-exported / hard cap), **A11** (alias mechanics), **A16** (the four P0 tests), **A18**
(the boundary rule in catalog §8), **A22** (P0-core/P0-spine), **A24** (vocabulary amendment),
**A25** (textfile → P1b), **A26** (alert-authoring dependency), **A28** (off-loop discipline).

| # | File · location | Before | After | Canonical §10 |
|---|---|---|---|---|
| **E35** | plan §1 write-side restatement | `A5/A6` | `A6/A7` | #6 write path + #7 op re-derive |
| **E36** | plan §3 off-loop discipline | `A6/A28` | `A28` | #28 |
| **E37** | plan §3 listener topology | `A15` | `A12` | #12 |
| **E38** | plan §3 boot/teardown lifecycle | `A15` | `A13` | #13 |
| **E39** | plan §3 storage-split boundary rule | `A10` | `A18` | #18 |
| **E40** | plan §3 PG retention ladder | `A11` | `A19` | #19 |
| **E41** | plan §3 two-sampler consistency | `A12` | `A20` | #20 |
| **E42** | plan §3 freeze rules | `A14` | `A27` | #27 |
| **E43** | plan §4 P0 task 1 (boot placement) | `A15` | `A13` | #13 |
| **E44** | plan §4 P0 task 3 (kill-switch semantics) | `A16` | `A14` | #14 |
| **E45** | plan §4 P0 task 9 (thread naming) | `A18` | `A23` | #23 |
| **E46** | plan §4 P0 task 10 (census execution home) | `A17` | `A23` | #23 |
| **E47** | plan §4 P1b risk 4 (write-path risk row) | `A5/A6` | `A6` | #6 |
| **E48** | catalog §0.2 freeze rules | `A14/A27` | `A27` | #27 |
| **E49** | catalog §0.2 off-loop discipline | `A6/A28` | `A28` | #28 |
| **E50** | catalog §0.7 thread/loop topology | `A15` | `A12` | #12 |
| **E51** | catalog §0.8 truthiness & layering | `A16` | `A14` | #14 |
| **E52** | catalog F7 (op enum narrowing) | `A5/A6` | `A6/A7` | #6 + #7 |
| **E53** | catalog F7 (write-path task) | `A5/A6` | `A6/A7` | #6 + #7 |
| **E54** | catalog §10.0 restart-loss scenarios | `A13/A21` | `A21` | #21 |

> **Not an amendment label:** plan §9 **R12**'s "collector.py **A2** vocabulary" is the
> wedge-capture collector's *own* vocabulary name (see §F R12), not an amendment reference —
> left untouched on purpose.

### M.6 Content fixes and drift fixes, E55–E78 (24 edit sites) + the new open item H-12

**Amendment-driven fixes (the amendment was thin or missing — v3 implemented it first).**

| # | File · location | Fix | Amendment |
|---|---|---|---|
| **E55** | plan §4 P1a task 4 | the F14 **terminal-usage hook** (`graph.py:3776-3784`) + per-call dedupe + "terminal-chunk only" — the plan had no hook at all (wave 2 was catalog-only) | #2 |
| **E56** | plan §4 P1a task 6 | F20 counting unit + the **anchor pair**; removed "latency **per tool**" (no per-tool latency histogram ships) | #5 |
| **E57** | plan §4 P1a task 2 | thread groups "~8" → **11** (catalog F2 / §8.2) | consistency |
| **E58** | plan §4 P1a risk 2 | thread groups "~8" → **11** | consistency |
| **E59** | plan §4 P1b **new task 8** | the checkpoint **write-path** instrumentation task (the risk row alone was not "a task") | #6/#7 |
| **E60** | plan §4 P1c task 5 | **two producers** + the **live turn-end consumer** (the plan named one producer and the renderer-as-consumer) | #3/#4 |
| **E61** | plan §4 P1c exit criteria | same | #3/#4 |
| **E62** | plan §4 P1c risk 1 | same | #3/#4 |
| **E63** | plan §6 dark-families matrix | the **four missing rows** (env-unset+managed-key-on, `lock_contended`, CPU-spin wedge, non-Linux host) | #15 |
| **E64** | plan §7 | the cardinality cap is **hard** (§7 said "currently soft — make hard" while §5 said hard) | #10 |
| **E65** | catalog §1 F14 row | the stale source citation `graph.py:3760-3812` → `graph.py:3776-3784` (terminal-usage branch) | #2 |
| **E66** | catalog F20 | the anchor pair **specified** (was an unresolved "either/or") | #5 |
| **E67** | catalog §0.2 | the locking contract now **names the defect it fixes** (the `.get()` lock path → the scrape hangs) | #9 |
| **E68** | catalog §1 totals + §7 roll-up | the arithmetic reconciled to **533 / 222 / ≈605 / headroom 1 467** (+ the basis note) | §M.4 |

**Pre-existing drift found by the v3 consistency sweep (not wave-induced).**

| # | File · location | Fix | Why |
|---|---|---|---|
| **E69** | catalog §7 | the **duplicated stale paragraph** (F20 "tool calls 57" / "≈1 100") removed — wave 2 had prepended the corrected paragraph without deleting the old one | the roll-up must say one thing |
| **E70** | catalog F22 + §7 | the **F22 phase split** recorded (minimal writer in P1b per A25; family completes in P2) | the one wave-induced phase divergence |
| **E71** | plan §4 P1c task 7 + exit criteria | the **invalid** sweep expression `rate(..._cycle_seconds_count[5m])` → `rate(ensemble_sweep_cycles_total{lane="rdrs_mission_reconciliation"}[5m]) * 300 > 2` (the cycle metric is a **gauge**; no `_count` series exists) — the §8 alert table had been fixed in v2, these two lines had not | the alert expression must reference a catalog-real series |
| **E72** | plan §4 P1b exit criteria | `readiness.py:56–79` → **`:64-79`** (grace subset `:83-89`, components `:94-99`) — the same correction v2 applied at §9 R8 (E27) | stale citation |
| **E73** | plan §4 P1a risk 1 | the LLM-token risk now names the **terminal-usage branch** (`:3776-3784`) | #2 |
| **E74** | plan §8 alert table — the `BashSpin` row | the PromQL regex `promote_sh\|stage_sh\|launcher` had **unescaped pipes**, which split the markdown table cell (a pre-existing v2 rendering defect — the v2 pass escaped the same expression in `decisions.md` E20 but not in the plan's alert table); the pipes are now escaped | rendering |
| **E75** | plan §9 R12 | "collector.py **A2** vocab" → "collector.py's own '**A2**' vocabulary … *(not an amendment reference)*" — so no reviewer can mistake it for amendment **#2** | label hygiene |
| **E76** | catalog §0.8 | "the **A4-knob** lesson" → "the A4-knob lesson — *a managed-key-knob lesson, not an amendment reference*" (same hygiene) | label hygiene |

**This document's own v3 edits (E77–E78).**

| # | File · location | Fix | Why |
|---|---|---|---|
| **E77** | `decisions.md` §B MA-7 row | the same unescaped PromQL pipes escaped | rendering (same defect as E74) |
| **E78** | `decisions.md` §M.2 counts table | one row had 3 cells in a 2-column table | rendering |

**New open item H-12 — RESOLVED (structural, §O).**

| # | Open item | Detail | Status |
|---|---|---|---|
| **H-12** | The plan's §4 phase lists and the catalog's §1/§7 ship phases use **two different partitions** (historically cited as "36 families"; correction-round census at family granularity found 15 split + 4 catalog-only = 19 discrepancies — see §O.2). | The catalog's partition is the dependency-graph one (P1a = runtime/pools/census · P1b = checkpoint/jobs/sweeps/instances · P1c = LLM/MCP/compaction/journal · P2 = exporters); the plan's §4 headings are work-package labels. The divergence was pre-existing v2 (identical set at `cec78e0e8`). **Correction round (Item B) unified the partition** (§O): one canonical partition, 1:1, zero exceptions; F0.1–F22 → P0/P1a/P1b/P1c/P2; 42/42 task rows resolve to their own phase. Amendments (A22/A23/A25/A26) and the P0-core/P0-spine split preserved. Recorded as **G-20 (closed)**. | **RESOLVED** — §O (Item B) |

### M.7 Consistency-sweep result (STEP 5)

| Check | Result |
|---|---|
| **Name check** — plan §8 alert table → catalog §1 | **15 tokens, 15 resolve** (13 canonical names + 2 histogram suffixes `_bucket` / `_count` of `ensemble_checkpoint_op_seconds`). **0 unresolved.** Re-run after wave 1's tool-category change and wave 2's phase move. |
| **Name check** — plan §4 family lists → catalog §1 | **48 names, 48 resolve** to a catalog §1 family. **0 uncatalogued** (v2's 8 uncatalogued names stayed dropped — H-7 holds). |
| **Phase placements** — plan §4 vs catalog §1/§7 | ✅ **0 discrepancies (unified)** — correction round (Item B) unified the partition (§O): 1:1, zero exceptions; F0.1–F22 → P0/P1a/P1b/P1c/P2. Historical "36 families differ" (§M.7) is superseded by §O.2's 19-discrepancy census (15 split + 4 catalog-only) — recorded honestly, not hidden. |
| **Series roll-up** | ✅ reconciled — 26 + 62 + 223 + 222 = **533** daemon-origin; shell 5; exporter ≈67; grand ≈605; headroom **1 467** (§M.4). |
| **Exporter-origin** | ✅ ≈67, measured separately, outside the daemon budget; the ≤2k proof covers daemon-origin only. |
| **The 5 ratified decisions (§E)** | ✅ unchanged **in substance**: (1) host binaries / VM compose, **k8s out** (no manifest ships); (2) journal metrics via the Python twin `daemon/tools/upgrade_journal.py` (`scripts/upgrade/` observed, never edited); (3) the separate **127.0.0.1:9798** listener (never a route on the main app; bind failure ⇒ disabled); (4) **Prometheus for alerting vs the PG tier for per-instance views**; (5) the **dependency-free renderer** + **Prometheus classic text exposition**. The v3 round deepened decisions 3, 4 and 5 (topology, retention ladder, freeze rules) **without reversing** any of them. |
| **WS-A "4 tables accepting rows" gate** | ✅ survives **verbatim** inside the P0-spine split — `resource_daemon_bucket`, `resource_instance_sample`, `resource_pid_map`, `resource_limit_policy` all accept rows (plan §4, L119–L125; catalog F0.3 exit criterion). |
| **Alert expressions** | ✅ every §8 expression references a catalog-real series; the one stale expression in plan §4 (the gauge `_count` form) is fixed (E71). |
| **Code anchors** | ✅ every architect citation re-verified in-tree: `graph.py:3776-3784` / `:3811-3812` · `worker_pool.py:1381-1386` / `:253` / `:45` · `graph.py:7319` · `checkpoint_adapter.py:371-374` / `:377-379` · `checkpoint_perf.py:28-30` / `:190` / `:198` / `:233` · `persistence.py:509` · `checkpoint_metrics.py:59-63` / `:79` / `:182` / `:304` · `message_tap.py:234` / `:302-306` / `:308-309` · `readiness.py:64-79` · `config.py:818-888` · `api.py:2212-2226` / `:2241-2288` / `:2282-2287` / `:3411-3433` / `:3435-3464` · `_tool_registry.py:1598` (49 categories / 254 sites re-counted). |

### M.8 Commits

| Pass | Commit | Base | Content |
|---|---|---|---|
| 1 — original v2 plan | `cec78e0e881665450e1ca38c8bf555d915f4d54c` | `9bbef71c1f357d00f2a1dbd62e5c8057fb1620f1` | the three planning files as first authored + the 21 pass-1 alignment edits |
| 2 — alignment pass | `ad3a331716823998dee567cb6dfbad700b59c854` | `cec78e0e881665450e1ca38c8bf555d915f4d54c` | the H-1…H-11 closure (E22–E34) + §H refreshed |
| 3 — architect doc (WS-B step 2) | `c0f0694396d118e26fb7b40946005cc8bac8c4d4` | `ad3a331716823998dee567cb6dfbad700b59c854` | `architecture-recommendation.md` — the read-only input to v3 |
| 4 — **v3 (this commit)** | **HEAD** — a commit cannot contain its own sha; it is reported verbatim in the task report and retrieved with `git rev-parse HEAD` | `c0f0694396d118e26fb7b40946005cc8bac8c4d4` | **waves 1+2** (amendments 1–28 across plan + catalog, uncommitted until now) **+ the v3 verification/fix pass** (E35–E76) **+ §I–§M** |
| 5 — **correction round (this commit)** | **HEAD** — sha reported in the task report, retrieved with `git rev-parse HEAD` | 51bbfc000 (v3) | **§N (Item A) + §O (Item B)** + §G closure/additions + §H/§M refresh + final verification sweep; path-limited to the three planning files |

- **Committed paths (this commit, path-limited):** exactly
  `.agents/shared/planning/metrics-collection/plan-overview.md`,
  `.agents/shared/planning/metrics-collection/metric-catalog.md`,
  `.agents/shared/planning/metrics-collection/decisions.md`.
  **`architecture-recommendation.md` must NOT appear** — it is already committed (`c0f06943`)
  and unmodified.
- Nothing outside that directory is in the commit; the pre-existing stashes were **not touched**;
  **nothing was pushed**.
- **Verify:**

```bash
git rev-parse HEAD                 # the v3 commit (≠ c0f06943, ≠ ad3a33171, ≠ cec78e0e8)
git show --stat --oneline HEAD     # exactly three planning files, nothing else
git status --short                 # clean
```
---

## N. Item A — macOS/WS-A root-cause correction (leader-supplied facts, wave 1)

**Status.** RESOLVED and recorded. Wave 1 (Item A) applied the platform/WS-A correction across
`plan-overview.md` and `metric-catalog.md` (uncommitted until the correction-round commit). This
section records what it did, with file:line anchors into the post-wave-1 files. The earlier
"flusher binds bool→INTEGER 42804, window rolls back" hypothesis is **REFUTED** (catalog §11 C22
annotated SUPERSEDED; C35 records the refutation). No code changes in this round — markdown only.

### N.1 The deployment fact

- **Prod host is macOS.** The prod daemon is a macOS host binary (not a dev-box aside) — `plan-overview.md`
  §2 ("The PROD host is macOS (WS-A correction, 2026-10-29)"), §3 (platform support summary), §6
  (non-Linux host row, "PROD IS macOS"), §11 (non-goals, A2), `metric-catalog.md` §13 (platform
  matrix), §13.3/§13.4.
- **Since v0.25.0 the prod daemon received ZERO rows** in `resource_daemon_bucket` (T1) and
  `resource_instance_sample` (T2) — `proc_readers` returned `None` on every Darwin tick (no `/proc`,
  no `/sys/fs/cgroup`) → the sampler skipped every tick → the flusher had nothing to write.
- **Live signature (prod, 2026-10-29):** samples climbing, `flush {batches:1139, bytes:0, errors:0}`
  = **"produced nothing", NOT "write failed"**. `resource_pid_map` (T3) climbed to 45,575 rows
  (WS-A commit-time figure) / 46,448 (census moment) from the **spawn path** — **masking the
  outage** rather than refuting it. `metric-catalog.md` §13 (opening paragraph), §11 C35.
- The fix: `feature/resource-monitor-flusher-fix@53547b915` (Darwin ps-based reader, engaged **only**
  when the proc root is ABSENT **and** the host is Darwin; corrected counters `missed_samples`/
  `ticks_skipped` parity; skips excluded from `counters.ticks`; one-shot WARNING) merges to latest
  **before P0 executes**. `plan-overview.md` §4 P0 task 8, P0 risk 1, P0-spine split (L121–L138);
  `metric-catalog.md` F0.3, §11 C35.

### N.2 The REFUTED hypothesis (do not re-introduce)

- "flusher binds bool→INTEGER 42804, window rolls back" — **REFUTED.** Prod `estimated` column is
  `boolean NOT NULL` (`daemon/models/resource_monitor.py:231-233`); the bind at `flusher.py:770` is
  correct; the rollback amplifier was already fixed (`7be1b40bf` per-tier SAVEPOINT +
  `69cea24ea` `flush.errors`). The 42804 reading was a historical incident (the `flusher.py:790-806`
  docstring), not a live defect. `metric-catalog.md` §11 C22 (annotated SUPERSEDED) and C35
  (REFUTED, with the bind-audit test reference).
- **Catalog annotation:** C22 → "SUPERSEDED by C35 (2026-10-29): REFUTED"; C35 records the
  producer-side root cause. `plan-overview.md` §1 incident table + §6 (resource-spine-dark row) +
  §9 R5 carry the corrected framing.

### N.3 Resolution rows A1–A5 (each with anchors)

| # | Item | Resolution | Anchors |
|---|---|---|---|
| **A1** | Platform matrix — prod is macOS stated everywhere it matters | PROD-is-macOS stated in plan §2 (deploy target), §3 (platform support summary), §6 (non-Linux host row, "PROD IS macOS"), §11 (non-goals, A2); catalog §13 (platform matrix), §13.1 (field split), §13.2 (per-family), §13.3 (payload shape), §13.4 (non-goal). | `plan-overview.md` L38, L68, L315, L402; `metric-catalog.md` L1249–L1339 |
| **A2** | T3 per-child attribution = explicit **Non-Goal** (decided, implement as decided) | Per-child stays `/proc`-only even after the WS-A merge; the Darwin reader is daemon/process-level, not per-pid; no partial per-child data ships on macOS. Recorded as the planner's ratified decision under mission authority ("make it an explicit decision"), rationale: per-pid sampling has no Darwin path even post-merge; promising per-child on macOS would ship a silently-empty series (the J-class defect). Available for leader override. | `plan-overview.md` §11 (L402); `metric-catalog.md` §13.4 (L1330–L1339); §13.1 (per-child T3 row); §13.2 (F2/F3) |
| **A3** | WS-A merge dependency in the P0-spine exit criterion — **BLOCKS** semantics | The 4-table gate (`resource_daemon_bucket`, `resource_instance_sample`, `resource_pid_map`, `resource_limit_policy`) **BLOCKS** if `feature/resource-monitor-flusher-fix@53547b915` is not merged — it does not silently pass and does not downgrade. P0-core ships independently (A22 split). | `plan-overview.md` §4 P0 task 8 (L104), P0-spine split (L121–L138), P0 risk 1 (L147); `metric-catalog.md` F0.3 (L278–L284), §11 C35 |
| **A4** | Refutation purged from plan/catalog (C22→C35) | The 42804 framing is purged from the live text: C22 annotated SUPERSEDED, C35 records the refutation; plan §1/§6/§9 R5 carry the corrected producer-side framing. Historical/refuted mentions with pointers are correct and expected. | `metric-catalog.md` §11 C22 (L1209), C35 (L1222); `plan-overview.md` L14, L28, L38, L309, L371 |
| **A5** | Canonical-counters rule (catalog F0.3) | The spine's counters — including the new skipped-sample counters (`missed_samples`/`ticks_skipped`) — are **THE** source; no parallel counters. Sweep found none proposed. | `metric-catalog.md` F0.3 (L278–L284), §13 (opening, L1258–L1266); `plan-overview.md` §6 (non-Linux host row, L315) |

### N.4 Verified fact-nuances (encode, don't hide)

Wave 1 verified three nuances against the leader's description (and one extra). They are recorded
here so a reviewer cannot be surprised by them:

1. **`hw_peak_kb` IS supplied on Darwin post-merge** via `ru_maxrss` (BYTES→KB) — cross-platform,
   not Linux-only. The "Linux-only" label sometimes attached to hw-peak is wrong for the post-merge
   reader. `metric-catalog.md` §13.1 (hw_peak_kb row).
2. **`anon_kb` degrades to the `rss_kb` proxy on Darwin** (not `None`) — `coverage_pct` becomes an
   rss-denominated ratio, directionally useful but not comparable to Linux anon-based coverage.
   `metric-catalog.md` §13.1 (anon_kb row).
3. **`pid_map` census-moment figures differ** (46,448 vs the commit's 45,575) — same climbing
   counter, different observation moment; no action required.
4. **`swap_kb` is unwired on every platform today** — the T1 column exists (`models/resource_monitor.py:124`)
   but no reader populates it; NULL on both Linux and Darwin. `metric-catalog.md` §13.1 (swap_kb row).

### N.5 §G impact

- G-20 is **closed** by §O (Item B) — see §O and the G-20 row (now marked resolved).
- G-21 (T3 per-child non-goal, A2) and G-22 (platform-field nuances) are **added** to §G.
- No other §G entries change.

---

## O. Item B — G-20/H-12 partition unification (LEADER-SANCTIONED structural change, wave 2)

**Status.** RESOLVED and recorded. Wave 2 (Item B) performed the leader-sanctioned structural
change: one canonical partition, every family in exactly one phase, with the schedule intent, the 28
amendments and the P0-core/P0-spine split preserved. The change landed in `plan-overview.md` and
`metric-catalog.md` (uncommitted until the correction-round commit). This section records the
sanction, the census, the choice, the full 1:1 table, the counts, and the task-row completion pass.

### O.1 Sanction and requirement

- **Sanction:** leader-sanctioned structural change (wave 2, Item B) — "make it an explicit
  decision" authority extended to the partition.
- **Requirement:** one canonical partition; every family in **exactly one phase**; schedule intent
  preserved; the 28 amendments (§I) preserved; the P0-core/P0-spine split (A22) preserved;
  amendments win over both partitions where they touch phasing (A22/A23/A25/A26).
- **Canonical home of the convention:** `metric-catalog.md` §7 — "Intra-family phased rollouts —
  convention (G-20/H-12 unification): A family maps to exactly one phase (1:1, zero exceptions).
  When a family needs an early, partial ship, the carve-out is a **task-level early-ship note
  inside the owning phase's task list** — never the family occupying two phases. The family's phase
  is the phase in which it **completes** (F22 = P2)."

### O.2 Census (pre-unification) — honest correction of the v3 figure

The v3 audit recorded H-12 as "**36 families differ**" (`decisions.md` §M.7 / G-20). That figure is
**not reproducible at family granularity** — it was a count of (family, phase-cell) mismatches
including duplicate/ambiguous rows, not of distinct families. The correction-round census at family
granularity found:

- **15 families split** across the plan's phase lists (of which **3 appeared in more than one plan
  phase**), plus **4 catalog-only families absent from the plan lists** = **19 discrepancies**.
- The 28 amendments (§I) and the P0-core/P0-spine split (A22) are preserved.
- **No wave introduced the divergence** — the identical 19-discrepancy set is present at `cec78e0e8`
  (pre-wave); the divergence is pre-existing v2, not wave-induced. Wave 2 fixed it.

This is recorded honestly rather than silently: the "36 families" figure in §M.7 / G-20 is
superseded by this census. (G-20's row is marked resolved.)

### O.3 Choice: canonical = the catalog partition

- **Canonical partition:** the catalog's §1/§7 phase column —
  **P0 = F0.1–F0.4 · P1a = F1–F6 · P1b = F7–F13 · P1c = F14–F21 · P2 = F22**.
- **Rationale:**
  1. It matches **MA-11's dependency order** already recorded in frozen decisions.md §C (MA-11):
     registry → pools/census → checkpoint/jobs/sweeps/instances → LLM/MCP/compaction/journal →
     exporters. The catalog partition IS that order.
  2. The **plan §4 family lists + task rows were the drift source** (they used work-package labels:
     "Runtime & resource spine" / "Persistence, pools & checkpoint" / "Jobs, instances, sweeps &
     MCP" / "Exporters, textfile, journal twin…"), not the dependency order.
  3. Amendments win over both partitions where they touch phasing: A22 (P0-core/P0-spine), A23
     (thread-naming + census execution-home in P0), A25 (F22 minimal writer → P1b), A26 (alert
     authoring depends on referenced families).
- **The plan's §4 headings remain work-package labels** (the subsystem whose instrumentation lands
  there) — they are NOT relabelled to the dependency order; only the family→phase mapping is
  canonicalised. This keeps the plan's schedule and day-estimates untouched.

### O.4 Full 1:1 reconciliation table (family → phase, zero exceptions)

| Family | Phase | Family | Phase |
|---|---|---|---|
| F0.1 | P0 | F12 | P1b |
| F0.2 | P0 | F13 | P1b |
| F0.3 | P0 | F14 | P1c |
| F0.4 | P0 | F15 | P1c |
| F1 | P1a | F16 | P1c |
| F2 | P1a | F17 | P1c |
| F3 | P1a | F18 | P1c |
| F4 | P1a | F19 | P1c |
| F5 | P1a | F20 | P1c |
| F6 | P1a | F21 | P1c |
| F7 | P1b | F22 | P2 |
| F8 | P1b | | |
| F9 | P1b | | |
| F10 | P1b | | |
| F11 | P1b | | |

**23 families, 23 phase cells, zero exceptions.** F22's P1b partial ship is a **task-level
carve-out** (A25 — minimal textfile writer in P1b so BashSpin has a data source before the P2
alert rules land); the family **completes** in P2. This is the single carve-out convention,
stated in catalog §7.

### O.5 Per-phase series counts (one-line reconciliation)

| Phase | Families | Daemon-origin series |
|---|---|---|
| P0 | F0.1–F0.4 | 26 |
| P1a | F1–F6 | 62 |
| P1b | F7–F13 | 223 |
| P1c | F14–F21 | 222 |
| **Daemon total** | F0.1–F21 | **533** |
| Shell-origin (textfile) | F22 | 5 |
| Exporter-origin (node/postgres) | F22 | ≈67 |
| **Grand total** | | **≈605** |
| **Budget** | ≤2 000 | ≈27 % consumed, headroom **1 467** |

**Reconciliation: 26 + 62 + 223 + 222 = 533 daemon-origin** (headroom 1 467; counting basis stated
in catalog §7 — F7's range cell `22→33` taken at today value 22; +11 arrives with the P1b byte
wiring). Counting basis: catalog §7 ("§7 counts the §1 row values, with the one range cell (F7)
taken at its today value 22").

### O.6 Task-row completion pass

- **42/42 plan task rows resolve to their own phase** (zero cross-phase rows), with three
  documented exceptions/notes:
  1. **F22/A25 early-ship** — the minimal textfile writer task sits in P1b while the family
     completes in P2 (task-level carve-out, not a phase violation).
  2. **CR-4 note** — moved P1b→P1a (the checkout-latency histogram is retired; the note lives with
     the pool family in P1a).
  3. **P2 heading/tasks/exit-criteria** — the journal twin is **P1c** (F21), not P2; P2's
     journal references were dropped so P2 holds only the exporter/textfile work (F22).
- **Wake-producer task row verification (per the correction-round check):** the two producers
  (`worker_pool.py:1381-1386` + `graph.py:7319`, with the live turn-end consumer) sit in **P1b**
  (F13, task 9) — confirmed. The F14 hook task sits in **P1c** (task 1) — confirmed.
- Plan §4 family lists: **69/69 tokens resolve** to catalog §1 families (0 uncatalogued, 0
  duplicates) post-unification.

### O.7 §H/§M impact

- H-12 is **RESOLVED** (no longer deferred) — the partition is unified.
- §M.6's "36 families differ — pre-existing v2" line is superseded by O.2's 19-discrepancy
  census; the historical note is retained for audit but marked superseded.
- §M.7 consistency-sweep "Phase placements" row is updated to "0 discrepancies (unified)".

---

*End of decisions.md (correction round §N–§O appended).*
