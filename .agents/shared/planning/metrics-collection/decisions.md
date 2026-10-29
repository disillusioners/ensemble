# Decisions: v2 Metrics Collection — the reviewer-facing decision register

Date: 2026-10-29 · Author: plan-decisions worker · Branch `feature/metrics-collection`
(worktree `agents-ensemble-wt-metrics-v2`)

Companions in `.agents/shared/planning/metrics-collection/`:
`plan-overview.md` (the phase plan) · `metric-catalog.md` (the per-family spec and the
**counted-values ledger**, §14) · `architecture-recommendation.md` (the architect's review —
a **frozen historical input**, read-only, unmodified by every round since it was committed).

---

## How to read this register (three sections, one rule each)

This file is **restructured** into exactly three delimited sections. The split is the point:
a reviewer must be able to answer "what is current?" and "what was retired, and why?" without
scanning prose.

| Section | What it holds | Status vocabulary |
|---|---|---|
| **§ACTIVE** | the current authoritative decisions — the ratified design, the closures, and the standing stances | `ACTIVE` on every row |
| **§SUPERSEDED** | retained history for audit — a retired row, kept verbatim enough to be audited, with the ACTIVE row that replaced it | `SUPERSEDED` **in the row itself** + a `superseded_by` pointer |
| **§DISPOSITION** | the per-approver-round finding → closure map | `FIXED` / `NOTE` / `WAIVED` per row |

### Rule 1 — anchors are content-anchored (never a bare line number)

Every reference in this register is **section + row-id / heading**, never `file:123`:

- `metric-catalog.md §14.1 → *Daemon-origin total*` — a value in the counted-values ledger.
- `metric-catalog.md §8.13` — a named section of the catalog.
- `plan-overview.md §4 → *P1b task 9*` — a named row of the plan's phase table.
- `decisions.md §ACTIVE → §D → row 3` — a row of this register.

A bare line number is **not** an anchor: line numbers move on every edit, which is exactly how
the previous revision of this register drifted (see §DISPOSITION → §D.4 → row R-6).

### Rule 2 — counts are POINTERED, never restated (M-2)

Per `metric-catalog.md` §14.6, the ledger in catalog §14 is **referenced, not copied**. In this
register, **every** series count, headroom figure, family count, vocabulary size, rule /
fixture / baseline / risk-row count and the daemon-origin total is written as a **pointer into
catalog §14** — never as a number. The number lives in exactly one place: the catalog ledger.

**Rule thresholds and their baseline citations stay where they are** — they are rule
*parameters*, not counts (catalog §14.6). So this register may quote a threshold, a port, a
cadence or a capacity floor; it may not quote a count.

### Rule 3 — retired designs live ONLY in §SUPERSEDED

The retired designs (the superseded write-seam design, the phantom lifecycle values, the
superseded blocking-join teardown form, the mis-labelled claim anchor, the retired
tool-category hand-counts, the superseded totals) appear **only** inside §SUPERSEDED. In §ACTIVE
and §DISPOSITION they are referred to **by their §SUPERSEDED row id** — never quoted.

### Rule 4 — the closure standard (what counts as a disposition)

A finding is **dispositioned** when it carries one of:

- **FIXED** — with a content-anchored pointer to the artefact that now carries the fix;
- **NOTE** — with a rationale for accepting the state as-is;
- **WAIVED** — with a rationale for deliberately not changing it.

**A corrections-log entry is NOT a disposition.** Landing a note in a corrections log records
the observation; it does not disposition the finding, and it is not counted as one. Every
harvested finding therefore has its **own row** in §DISPOSITION (this is the closure standard
that replaced the previous revision's "folding a note into a correction-log entry counts as
resolved").

---

# §ACTIVE — the current authoritative decisions

**Status.** Every row below is `ACTIVE`. No row in this section carries a hardcoded series or
count value; counts are pointers into `metric-catalog.md` §14 (Rule 2). Superseded
counterparts are in §SUPERSEDED with a `superseded_by` pointer back into this section.

## A. Council round — CRITICAL findings (5/5 resolved)

| ID | Finding (one line) | Resolution in v2 | Anchor (content-anchored) | Status |
|---|---|---|---|---|
| **CR-1** | A wedged daemon cannot export its own `/metrics` on the main port — event-loop starvation **is** the incident, and there was no external liveness alert. | `/metrics` is explicitly **not** a witness for its own wedge. The witnesses live **out of process**: external liveness (`up==0` / `absent()`) on the scrape target, `scrape_duration_seconds` as a starvation precursor, a node_exporter **textfile** directory written by an out-of-process writer, node_exporter + postgres_exporter, and the wedge-capture artefacts already produced by the capture path. | `plan-overview.md §1 → *incident table*` (the "Wedged daemon cannot export own /metrics" row) + `§3 → *Prometheus-vs-resource_monitor split (decision 4)*` + `§4 → *P2 task 1*` + `§8 → *ExternalLivenessDown*` + `§8 → *ScrapeDurationHigh*`; `metric-catalog.md §6 → *F22*` + `§3 → *F3*` + `§7 → *Exporter-origin*` | **ACTIVE** |
| **CR-2** | `/metrics` landed on the main app's public port with zero auth / bind / gating. | A **separate localhost-bound listener** (`127.0.0.1` only, own port, managed key `limits.metrics_listener_port`) — never a route on the main app. An explicit **never-exported list** is enforced at render time. On/off is decided in P0; the kill-switch default is **OFF**. A bind failure degrades the listener to *disabled*, never a boot abort. | `plan-overview.md §2 → *Deploy target (RATIFIED, decision 1)*` + `§3 → *Separate localhost-bound /metrics listener (decision 3 + CR-2)*` + `§4 → *P0 task 1*` + `§5 → *managed keys*` + `§7 → *never-exported list*` + `§6 → *degradation matrix*`; `metric-catalog.md §0.7` + `§2 → *F0.1*` + `§2 → *F0.4*` | **ACTIVE** |
| **CR-3** | The live checkpoint histogram was duplicated, and **no bucket set** existed for any histogram — slow writes and reads landed in `+Inf` and p95 collapsed. | `daemon/checkpoint_metrics.py` (top-level) becomes the **single canonical registry**; bucket sets are stated **once** for all histograms with the checkpoint top extended; **one measurement, one name** — the two legacy `message_api_*` names are re-registered under canonical `ensemble_*` names with a one-release deprecated alias; the existing bounded **slow-op ledger** is the CheckpointSlow source — no parallel detector. | `plan-overview.md §4 → *P0 task 4*` + `§4 → *P0 exit criteria*` + `§8 → *CheckpointSlow*` + `§10 → *AC9*` + `§1 → *Corrected "Why"*`; `metric-catalog.md §0.2` + `§0.3` + `§0.4` + `§4 → *F7*` + `§8.11 → *bucket sets*` + `§10.2` + `§11 → *C1*` + `§11 → *C4*` | **ACTIVE** |
| **CR-4** | `ensemble_db_pool_checkout_timeouts_total{pool}` has **no available source** — the pool library exposes no timeout counter. | The timeout name is **retired**. v2 ships `ensemble_db_pool_requests_errors_total{pool,kind}` as the **documented superset** (not a timeout count) and **rebases** PoolExhausted onto it. The `kind` label is **reserved** so the checkout-seam follow-up can split it later **without a breaking label change**. The SQLAlchemy manager pool has no `requests_errors` equivalent ⇒ `pool="manager"` renders no series (an absence, not a gap). | `plan-overview.md §1 → *incident table*` + `§4 → *P1a family list*` + `§8 → *PoolExhausted*` + `§10 → *AC10*`; `metric-catalog.md §3 → *F4*` + `§8.9 → *request-error kind*` + `§10.2` + `§11 → *C12*` + `§12 → *open item 1*` | **ACTIVE** |
| **CR-5** | The P0 deploy rested on a **nonexistent** `infranfrastructure/k8s/` path (misspelled and absent). | Deploy target is **host binaries / VM compose** (leader-ratified, decision 1). k8s is **explicitly out of scope — a separate project**; the v1 reference is withdrawn and **no k8s manifest ships**. | `plan-overview.md §2 → *Deploy target (RATIFIED, decision 1)*` + `§2 → *Out of scope (explicit)*` + `§11 → *Non-goals*` + `§4 → *P2*`; `metric-catalog.md §6 → *F22*` | **ACTIVE** |

## B. Council round — MAJOR findings (16/16 resolved)

| ID | Finding (one line) | Resolution in v2 | Anchor (content-anchored) | Status |
|---|---|---|---|---|
| **MA-1** | No kill-switch; the managed-key-only gate is restart-gated. | `ENSEMBLE_METRICS_ENABLED` is read **per request** (no restart), default **OFF**. It is layered under the managed-keys registry so the DB/env/yaml chain can also flip it, but the per-request env read is the authoritative emergency off-switch. OFF ⇒ empty-shape 200, samplers stop ticking, **no history lost**. | `plan-overview.md §5 → *kill-switch*` + `§4 → *P0 task 3*` + `§4 → *P0 exit criteria*` + `§6 → *degradation matrix*`; `metric-catalog.md §0.8` + `§2 → *F0.1*` + `§2 → *F0.2*` | **ACTIVE** |
| **MA-2** | `thread_cpu_seconds` would **rebuild** the existing `/proc` sampler. | **Reuse, post-process only.** The existing sampler stays the ONE `/proc` walker; bucketing into the fixed thread groups happens downstream. A one-`/proc`-dialect rule is explicit. | `plan-overview.md §4 → *P1a task 2*` + `§9 → *R4*`; `metric-catalog.md §3 → *F2*` + `§8.2 → *thread group*` | **ACTIVE** |
| **MA-3** | Per-instance resource metrics would multiply by instance count and blow the cardinality budget. | Per-instance attribution stays in the **PG tier** (`/api/resources/*`); every daemon-origin family is **aggregate**; **no daemon-origin family carries a per-instance label**. Enforced in the catalog's vocabulary annex as a cardinality rule. | `plan-overview.md §3 → *Storage-split boundary rule (A18)*` + `§2 → *Cardinality budget (MA-10)*` + `§6 → *degradation matrix*`; `metric-catalog.md §0.6` + `§2 → *F0.3*` + `§13.2` | **ACTIVE** |
| **MA-4** | Event-loop lag was only in the wedge-capture path (dark unless capturing). | The lag probe is exported as gauges/quantiles from the existing rolling-window instrument — **computed quantiles**, dark when wedge-capture is off (absence, not zero). | `plan-overview.md §4 → *P1a task 1*` + `§8 → *LoopStarved*`; `metric-catalog.md §3 → *F1*` + `§8.3 → *loop-lag stat*` + `§11 → *C5*` | **ACTIVE** |
| **MA-5** | The readiness taxonomy would be re-invented (and already is — the v1 count was wrong). | Labels are preserved exactly: the family **reads the published composite**, never re-probes; the grace subset is a fixed subset published on the HELP text, not a label. | `plan-overview.md §4 → *P1b task 3*` + `§4 → *P1b exit criteria*` + `§8 → *FalseDegraded*`; `metric-catalog.md §4 → *F9*` + `§8.3 → *readiness outcome*` + `§11 → *C6*` + `§10.2` | **ACTIVE** |
| **MA-6** | Sweep hot-loop had no measurable cycle metric (the v1 rule could never fire). | A per-lane cycle counter plus a per-lane duration gauge; the configured interval per lane is published on the HELP text, so actual-vs-configured needs no second metric. The alert expression filters one lane and uses `rate()` on the **counter**. | `plan-overview.md §4 → *P1b task 11*` + `§8 → *SweepHotLoop*`; `metric-catalog.md §4 → *F12*` + `§8.4 → *sweep lane*` + `§8.4 → *sweep row outcome*` | **ACTIVE** |
| **MA-7** | A spinning bash script cannot report its own CPU, so the in-daemon metrics cannot see it. | An **out-of-process external sampler** reads the OS's view of the named processes and writes the textfile; the house shell scripts are **observed, never modified**. | `plan-overview.md §3 → *Textfile collector (R18, genuinely new)*` + `§4 → *P2 task 1*` + `§8 → *BashSpin*` + `§8 → *TextfileStale*`; `metric-catalog.md §6 → *F22*` + `§11 → *C43*` + `§11 → *C58*` | **ACTIVE** |
| **MA-8** | LLM tokens/cost were absent, and an `ok`/`failover`/`error` split **collapses the real retry ladder**. | A token counter with the truncation/under-count caveat made **visible** as its own counter; a request counter carrying the **real outcome enum** derived from the retry ladder, with the layer mapping tabulated. The latency histogram deliberately carries **no** outcome label. | `plan-overview.md §4 → *P1c task 1*` + `§4 → *P1c task 2*` + `§8 → *LoopStarved*` (adjacent rule) + `§10 → *AC7*`; `metric-catalog.md §5 → *F14*` + `§5 → *F15*` + `§5 → *F16*` + `§8.12` + `§8.10 → *LLM outcome*` | **ACTIVE** |
| **MA-9** | No acceptance criteria that a reviewer could execute. | A numbered acceptance-criteria list, with every dark-able surface carrying an explicit precondition row. | `plan-overview.md §10 → *AC1…AC12*` + `§10 → *promtool fixture layout*`; `metric-catalog.md §11 → *C45*` | **ACTIVE** |
| **MA-10** | No cardinality budget or census. | The budget **rule**: daemon-origin ≤ 2 k series; the stock exporters are **measured separately** and are NOT counted against the daemon budget; an acceptance criterion asserts the split. The counts live once, in the catalog ledger; the plan and this register **point** at them. | `plan-overview.md §2 → *Cardinality budget (MA-10)*` + `§7 → *never-exported list*` + `§10 → *AC3*`; `metric-catalog.md §14.1 → *Daemon-origin total*` + `§14.1 → *Budget*` + `§14.6` | **ACTIVE** |
| **MA-11** | The catalogue had no per-family spec (names, types, labels, sources). | The catalog **is** the per-family spec: every family declares name, type, labels, source, phase, owner, degradation, plus a vocabulary annex and a census annex. | `metric-catalog.md §1` + `§2 → *F0.1*` … `§6 → *F22*` + `§8` + `§9` | **ACTIVE** |
| **MA-12** | Closed vocabularies were hand-listed (and drifted). | Every closed vocabulary is **derived from code, never hand-listed**, and is written out in full in the vocabulary annex with the `file:line` it was verified against. | `metric-catalog.md §8 → *opening rule*` + `§0.5` + `§8.1` … `§8.13`; `plan-overview.md §4 → *P0 task 6*` | **ACTIVE** |
| **MA-13** | The PG leak-site attribution was asserted, not derived. | A **census** on a dedicated non-pooled connection at a bounded cadence, with a **closed** site map plus an `other` residual that is never dropped; the census inherits the existing db-capture discipline. | `plan-overview.md §1 → *Leak story*` + `§4 → *P1a task 5*` + `§8 → *PoolLeak*` + `§10 → *AC5*`; `metric-catalog.md §3 → *F6*` + `§9` + `§9.1` + `§9.2 → *leak site*` + `§8.9 → *leak site*` | **ACTIVE** |
| **MA-14** | The checkpoint store's size metrics had no source (single-table sizes do not exist). | The blob-size metric is a **split-out** of the existing one-round-trip vacuum query; the per-table size metric is its generalisation. | `plan-overview.md §4 → *P1b task 2*` + `§8 → *CheckpointBloat*`; `metric-catalog.md §4 → *F8*` + `§11 → *C9*` | **ACTIVE** |
| **MA-15** | No durable-vs-reset-on-restart discipline for the counters. | Every family declares its idiom (**A** in-process registry / **B** PG table spine / **C** log markers) and its restart behaviour; `/metrics` is a **VIEW** over the same services, never a second collector. | `plan-overview.md §3 → *Prometheus-vs-resource_monitor split (decision 4)*` + `§6 → *degradation contract*`; `metric-catalog.md §0.6` + `§10 → *durability table*` + `§10.0` | **ACTIVE** |
| **MA-16** | The exposition format was mislabelled (OpenMetrics) and a `prometheus_client` dependency was implied. | Exposition is **Prometheus classic text format, NOT OpenMetrics**; no `prometheus_client`, no OTel, no new PyInstaller surface — the **dependency-free renderer** is kept (decision 5). | `plan-overview.md §3 → *Dependency-free renderer (decision 5 + MA-16, REAFFIRMED loudly)*` + `§10`; `metric-catalog.md §0.2` + `§0.3 → *alias mechanics*` | **ACTIVE** |

## C. Council round — MINOR findings (10 rows — 8 fixed · 1 split · 1 waived)

| ID | Finding (one line) | Resolution in v2 | Anchor (content-anchored) | Status |
|---|---|---|---|---|
| **m1** | `wake_turns_total` had **no named producer** and no consumer. | The full Carrier-Threading contract is specified: **producer** = the canonical carrier producer (the fresh-episode flag computed once at the canonical producer in the task processor, carried on the `ProcessingContext` carrier field); **carrier** = a keyword-only field on `ProcessingContext` defaulting to `None`; **threading** = keyword-only, pipeline → facade → service; **consumer** = a live turn-end consumer that increments the counter while the renderer only reads the accumulated value; **consumer-seam re-derivation is forbidden**. | `plan-overview.md §4 → *P1b task 9*` + `§4 → *P1b exit criteria*` + `§4 → *P1b risk row 4*`; `metric-catalog.md §8.13 → *wake-turn contract*` + `§11 → *C29*` + `§11 → *C73*` + `§14.4 → *wake-turn outcome*`; `decisions.md §ACTIVE → §G → row 3` | **ACTIVE** |
| **m2** | The pool gauge **mixed derived vs native** quantities. | Split into two metrics so the derivation is visible in the **name**: a derived checked-out gauge and a native waiting-clients gauge. Native quantities stay on their own family. | `plan-overview.md §4 → *P1a family list*` + `§9 → *R12*`; `metric-catalog.md §3 → *F5*` + `§3 → *F4*` + `§8.9 → *pool name*` + `§11 → *C12*` | **ACTIVE** |
| **m3** | Queue depth must read the admission state; claim latency had no existing instrument. | Depth reads the `job_queue_items.admission_state` column (the enum is the sole write authority). Claim latency is **greenfield** with the **seam named**: the claim call in the worker pool, where the enqueue and the claim moment are both available; the observation is taken **once** and carried, never re-derived. | `plan-overview.md §4 → *P1c task 5*` + `§4 → *P1c task 6*` + `§4 → *P1c exit criteria*`; `metric-catalog.md §4 → *F10*` + `§4 → *F11*` + `§8.10 → *admission state*` + `§11 → *C15*` + `§12 → *open item 4*` | **ACTIVE** |
| **m4** | Heartbeat age had **no cadence and no source**. | The source column is named (a naive-timestamp column ⇒ bound and compared with the sanctioned naive-UTC helper), and the **configured cadence is published as its own series**, so "older than one cadence" is distinguishable from "older than a longer cadence". The value is read from the pool's constructor argument, never hard-coded. | `plan-overview.md §4 → *P1c task 4*` + `§4 → *P1c exit criteria*`; `metric-catalog.md §4 → *F11*` + `§8.10 → *worker pool name*` | **ACTIVE** |
| **m5** | The skill-metrics lane should not be re-surfaced in `/metrics`. | **Waived — Non-Goal.** The lane already has its own surface; re-surfacing it would duplicate the same numbers and double-count them. It is excluded from the catalog entirely rather than given a half-family. (The durable usage table is a *different* artefact and is tracked separately.) | `plan-overview.md §11 → *Non-goals*` + `§2 → *Out of scope (explicit)*`; `metric-catalog.md §12 → *open items*` (the header scope line names the non-goal) | **ACTIVE** — waived with rationale |
| **m6** | Compaction pressure needs a counter, not just an occupancy reading. | A fired counter carrying the rung label, an occupancy ratio carrying the stage label, and a pre-call estimate that renders the **max across instances** (a per-instance label would explode cardinality). The rung recorded is the one **actually applied**, because escalation lowers the gate. | `plan-overview.md §4 → *P1c task 4*` + `§4 → *P1c risk 2*`; `metric-catalog.md §5 → *F19*` + `§8.10 → *compaction rung*` + `§8.10 → *compaction stage*` | **ACTIVE** |
| **m7** | Tool-call perf must instrument the per-turn seam, **not** graph internals. | The tool-call and turn-performance families instrument the per-turn tap seam, with the tool **category** read from the tool's own registration metadata (the runtime-derived category set — never a hand list). The tap is **non-load-bearing**: a tap failure yields *no series*, never a broken turn. | `plan-overview.md §4 → *P1c task 5*` + `§9 → *R17*`; `metric-catalog.md §5 → *F20*` + `§8.6 → *tool category*` + `§11 → *C24*` | **ACTIVE** |
| **m8** | Lifecycle was **point-in-time only** — no spawn/revive/pause/fan-in counters. | The lifecycle counter carries the **code-derived** event set from the instance-lifecycle publisher — the real events plus a reserved `other` residual — **not** a hand-listed set, and the catalog states the **amendment procedure** for extending it. (The superseded hand-listed set is retained in §SUPERSEDED → row S-3.) | `plan-overview.md §4 → *P1b task 12*` + `§4 → *P1b exit criteria*`; `metric-catalog.md §4 → *F13*` + `§8.1 → *instance lifecycle event*` + `§14.4 → *instance lifecycle event*` + `§11 → *C62*` | **ACTIVE** |
| **m9** | The VictoriaMetrics/Mimir compatibility claim was **asserted without basis**. | **Split — compat claim WAIVED (dropped), off-loop spec FIXED.** No compatibility is asserted for an untested stack: the target is **stock Prometheus + Grafana**, and the claim is removed from the scope statement, the out-of-scope list and the non-goals. The off-loop half is fixed by a non-negotiable off-loop discipline: no blocking work, no I/O, no logging and no per-chunk work on the event loop; one `/proc` dialect; the per-request kill-switch read; the census on a dedicated non-pooled connection; the textfile writer out of process. | `plan-overview.md §2 → *Out of scope (explicit)*` + `§11 → *Non-goals*` + `§3 → *Off-loop discipline (non-negotiable)*` + `§1 → *incident table*`; `metric-catalog.md §0.2` + `§11 → *C25*` + `§6 → *F22*` + `§9.1` | **ACTIVE** — resolved (split) |
| **m10** | Three metric idioms coexist — state how `/metrics` aggregates them. | Every family names its idiom (**A** in-process registry · **B** PG table spine · **C** log markers — a *source* for greenfield counters, **never** for a gauge). `/metrics` is a **VIEW** over these — samplers are shared, the render path only formats — never a parallel collector. | `plan-overview.md §3 → *Prometheus-vs-resource_monitor split (decision 4)*`; `metric-catalog.md §0.6` + each family's *Source* field (e.g. `§3 → *F4*`) | **ACTIVE** |

**Minor split summary:** fixed **8** (m1, m2, m3, m4, m6, m7, m8, m10) · fixed-with-one-waived-clause
**1** (m9 — compat claim dropped, off-loop spec fixed) · waived **1** (m5, Non-Goal with
rationale). **No MINOR finding is open.**

## D. Leader-ratified decisions (5/5 — all binding, all implemented)

| # | Decision | Anchor (content-anchored) | Status |
|---|---|---|---|
| **1** | **Deploy target = host binaries / VM compose; k8s is out of scope** (a separate project). The v1 k8s path reference is withdrawn; no k8s manifest ships. | `plan-overview.md §2 → *Deploy target (RATIFIED, decision 1)*` + `§2 → *Out of scope (explicit)*` + `§11 → *Non-goals*` + `§4 → *P2*`; `metric-catalog.md §6 → *F22*` | **ACTIVE** |
| **2** | **Journal metrics via the Python twin `daemon/tools/upgrade_journal.py`; `scripts/upgrade/` is NEVER edited** (the shell scripts are *observed* through the twin, not modified). | `plan-overview.md §3 → *Textfile collector (R18, genuinely new)*` + `§4 → *P2 task 2*` + `§4 → *P2 exit criteria*` + `§8 → *JournalWalkRisk*`; `metric-catalog.md §5 → *F21*` + `§8.7 → *journal op*` + `§11 → *C26*` | **ACTIVE** |
| **3** | **Separate localhost-bound `/metrics` listener** — own app, own port, `127.0.0.1` only; never a route on the main public app; bind failure degrades to *disabled*, never a boot abort. | `plan-overview.md §3 → *Separate localhost-bound /metrics listener (decision 3 + CR-2)*` + `§4 → *P0 task 1*` + `§5 → *managed keys*` + `§7 → *never-exported list*`; `metric-catalog.md §0.7` + `§2 → *F0.1*` | **ACTIVE** |
| **4** | **Prometheus is authoritative for alerting series; the `resource_monitor` PG tier is authoritative for per-instance API views** (`/api/resources/*`); `/metrics` is a **view over the same services**, not a second implementation. | `plan-overview.md §3 → *Prometheus-vs-resource_monitor split (decision 4)*` + `§2 → *Cardinality budget (MA-10)*` + `§10 → *AC3*` + `§6 → *degradation contract*`; `metric-catalog.md §0.6` + `§2 → *F0.3*` + `§7 → *Exporter-origin*` | **ACTIVE** |
| **5** | **The dependency-free renderer is KEPT — the no-`prometheus_client` position is REAFFIRMED LOUDLY** (named as a deliberate reaffirmation, not an oversight). Exposition is **Prometheus classic text format, NOT OpenMetrics**; no `prometheus_client`, no OTel, no new PyInstaller surface. | `plan-overview.md §3 → *Dependency-free renderer (decision 5 + MA-16, REAFFIRMED loudly)*` + `§10`; `metric-catalog.md §0.2` + `§2 → *F0.2*` + `§11 → *C25*` | **ACTIVE** |

## E. Reuse ledger (18 rows — none rebuilt instead)

| R | What it is | Where v2 builds on it (catalog family / plan row) | Status |
|---|---|---|---|
| **R1** | `daemon/checkpoint_metrics.py` — the registry + renderer | `metric-catalog.md §2 → *F0.2*` (the renderer *becomes* the registry renderer; the two singletons become registry-registered instances under canonical names) + `§4 → *F7*`; `plan-overview.md §9 → *R1*` — ⚠️ the reuse story is "**registry wired to collectors, exposition unwired**" (see `decisions.md §ACTIVE → §J → row 1`) | **ACTIVE** |
| **R2** | `daemon/checkpoint_perf.py` + the bounded slow-op ledger | `metric-catalog.md §4 → *F7*` (both observation sites already exist — *reuse, do not duplicate*; the ledger **is** the CheckpointSlow source) + `§8.11 → *bucket sets*` + `§10.2`; `plan-overview.md §9 → *R2*` | **ACTIVE** |
| **R3** | `wedge_capture/lag_probe.py` — the rolling-window lag instrument | `metric-catalog.md §3 → *F1*` (computed quantiles exported as gauges; dark when wedge-capture is off) + `§10.2` + `§11 → *C5*`; `plan-overview.md §9 → *R3*` | **ACTIVE** |
| **R4** | `wedge_capture/sampler.py` — the ONE `/proc` walker | `metric-catalog.md §3 → *F2*` (post-process bucketing; never a second sampler) + `§8.2 → *thread group*`; `plan-overview.md §9 → *R4*` | **ACTIVE** |
| **R5** | The `resource_monitor` spine (sampler, ring, flusher, retention sweep, tables) | `metric-catalog.md §2 → *F0.3*` (the spine health gate + the "tables accepting rows" P0 exit criterion; the flusher counters are the source) + `§0.6` + `§11 → *C22*`; `plan-overview.md §9 → *R5*` | **ACTIVE** |
| **R6** | `/api/resources/*`, `maintenance`, `/api/snapshots/metrics` surfaces | `metric-catalog.md §0.6` (**/metrics is a VIEW over the same services**) + `§2 → *F0.3*`; `plan-overview.md §3 → *Prometheus-vs-resource_monitor split (decision 4)*` + `§9 → *R6*` — *Partial:* the maintenance/snapshot **counter families** are not catalogued → open item H-7 | **ACTIVE** |
| **R7** | `daemon/services/maintenance.py` — the one-round-trip vacuum-size query | `metric-catalog.md §4 → *F8*` (the blob-size metric is a **split-out** of that one round trip; the per-table size metric is its generalisation) + `§11 → *C9*`; `plan-overview.md §9 → *R7*` | **ACTIVE** |
| **R8** | `daemon/services/readiness.py` — the normative readiness taxonomy | `metric-catalog.md §4 → *F9*` (labels preserved; the family *reads* the published composite, never re-probes) + `§8.3 → *readiness outcome*` + `§11 → *C6*` + `§10.2`; `plan-overview.md §9 → *R8*` | **ACTIVE** |
| **R9** | The `MANAGED_KEYS` registry | `metric-catalog.md §0.8` (the registry default must mirror the pydantic Field default exactly) + `§2 → *F0.1*` + `§2 → *F0.2*`; `plan-overview.md §5 → *managed keys*` + `§4 → *P0 task 5*` + `§9 → *R9*` | **ACTIVE** |
| **R10** | `daemon/services/worker_pool.py` `get_stats()` (incl. the wakeup-efficiency gauge and the in-RAM counters) | `metric-catalog.md §4 → *F11*` + `§8.10 → *worker pool name*` + `§10 → *durability table*`; `plan-overview.md §9 → *R10*` | **ACTIVE** |
| **R11** | `wedge_capture/db_capture.py` census discipline | `metric-catalog.md §3 → *F6*` + `§9` + `§9.1` (the census inherits that discipline on a dedicated non-pooled connection) + `§9.4`; `plan-overview.md §9 → *R11*` | **ACTIVE** |
| **R12** | `wedge_capture/collector.py` pool-status vocabulary | `metric-catalog.md §3 → *F5*` (derived vs native kept apart) + `§3 → *F4*` (the `kind` vocabulary + the CR-4 superset semantics) + `§8.9 → *request-error kind*` + `§11 → *C12*`; `plan-overview.md §9 → *R12*` | **ACTIVE** |
| **R13** | `daemon/tools/ens_db_tools.py` — the pool-status seam | `metric-catalog.md §3 → *F4*` (pool status is already published at `/readyz`; the family reads that shape — one status source, not two) + `§11 → *C12*`; `plan-overview.md §9 → *R13*` — *Note:* the catalog collapses both reads into one family → open item H-7 | **ACTIVE** |
| **R14** | The MCP **MachineStore** roster (gateway sessions/tombstones) and the per-boot MCP **quarantine registry** | `metric-catalog.md §5 → *F18*` + `§5 → *F17*` + `§8.5 → *MCP roster*`; `plan-overview.md §9 → *R14*` (the plan's pointer conflated the two — open item H-10, now **CLOSED**: the plan's reuse row is split into the two correct references) | **ACTIVE** |
| **R15** | The sweep interval registry | `metric-catalog.md §4 → *F12*` (each lane's configured interval is published on the HELP text so actual-vs-configured needs no second metric) + `§8.4 → *sweep lane*` + `§10.2`; `plan-overview.md §9 → *R15*` | **ACTIVE** |
| **R16** | The `MAINTENANCE_ENDPOINTS_ENABLED` gating pattern (a kill-switch analog) | `metric-catalog.md §0.8` (kill-switch contract: per-request read, default OFF, registry keeps accumulating) + `§2 → *F0.1*`; `plan-overview.md §5` + `§9 → *R16*` | **ACTIVE** |
| **R17** | `daemon/services/message_tap.py` — the per-turn seam | `metric-catalog.md §5 → *F20*` (tool-call + turn-performance families instrument *this* seam, not graph internals) + `§8.6` + `§11 → *C24*`; `plan-overview.md §9 → *R17*` | **ACTIVE** |
| **R18** | The node_exporter **textfile collector** pattern (the only greenfield exporter piece) | `metric-catalog.md §6 → *F22*` (the shell-origin series, counted **outside** the daemon-origin budget) + `§7 → *Exporter-origin*`; `plan-overview.md §3 → *Textfile collector (R18, genuinely new)*` + `§9 → *R18*` | **ACTIVE** |

## F. Deliberate waivers & known limitations register

Each entry: what is waived/limited · where it is handled · one-line rationale. Every entry is
traceable to `plan-overview.md` or `metric-catalog.md`; nothing here is invented. Counts and
vocabulary sizes are **pointers** (Rule 2), not values.

| # | Waiver / limitation | Where handled | Rationale | Status |
|---|---|---|---|---|
| **G-1** | **The catalog is longer than its length target** — waived. | The catalog as a whole: its family sections, the verified `file:line` reference set, the vocabulary annexes, the census annex and the corrections log. (This register holds **no** copy of the catalog's length — the size is read from the file itself; see Rule 2.) | **Waived for citation fidelity.** A truncated catalog loses the exact `file:line` a reviewer needs to check a claim; the length buys the verified refs and the corrections log that the whole review rests on. The length target is a size preference, not a correctness constraint. | **ACTIVE** |
| **G-2** | **The MCP roster label is bounded** although the underlying server name is unbounded. | `metric-catalog.md §8.5 → *MCP roster*` (roster + **churn policy**: user-created servers are never named individually, they collapse into a bounded bucket; a residual bucket is reserved so the roster can never exceed its cap) + `§5 → *F17*` + `§12 → *open item 5*`. | The server name is user-controlled ⇒ naming it as a label is unbounded cardinality. The churn policy keeps the label set closed while leaving room for an additive future roster; per-user-server metrics would be a **second, explicitly-labelled** family, not a smuggle-in. | **ACTIVE** |
| **G-3** | **The pool request-error counter emits only the aggregate `kind` value today** (the library exposes the aggregate, not the breakdown); the full kind vocabulary is the catalog's `request-error kind` set. | `metric-catalog.md §3 → *F4*` + `§8.9 → *request-error kind*` + `§12 → *open item 1*` + `§10.2` (the precise follow-up expression). | The **label is reserved ahead of the instrument** so the checkout-seam follow-up can split the value later **without a breaking label change**; today's aggregate is documented as a superset, not a timeout count. | **ACTIVE** |
| **G-4** | **`ensemble_thread_cpu_seconds` cannot split the worker vs chat-worker thread groups** until the worker threads are named at construction. | `metric-catalog.md §3 → *F2*` (the P1a prerequisite: the worker is constructed with no thread name, so every worker thread is an anonymous `Thread-N`) + `§12 → *open item 3*` + `§8.2 → *thread group*`. | Until the threads are named, the split is **not observable at all** from the proc reader; both fold into the residual bucket rather than shipping a label whose value would be fiction. Naming the threads at construction is a P1a prerequisite, not a metric redesign. | **ACTIVE** |
| **G-5** | **`ensemble_checkpoint_op_bytes` under-reports** until the blob byte count is captured at the saver-op seam. | `metric-catalog.md §4 → *F7*` (the saver-op seam already carries a `bytes_` parameter ⇒ this is a **wiring** gap, not a new instrument) + `§12 → *open item 2*`. | P1b must either wire the real blob size or record the metric as "bytes for maintenance carve-outs only". Shipping a knowingly-under-counting bytes histogram without that decision would be the real risk. | **ACTIVE** |
| **G-6** | **The job-claim-latency producer is not yet implemented** (no existing instrument). | `metric-catalog.md §4 → *F11*` (greenfield; the claim call is the named seam; the observation is taken once and carried) + `§12 → *open item 4*` + `§11 → *C15*`. | The only related signal counts empty polls, **not** enqueue→claim latency. Choosing the producer is a P1b task with a named seam, so the ambiguity is bounded — and the metric is not claimed until then. | **ACTIVE** |
| **G-7** | **The sweep rows counter is per-lane in practice** — most lanes emit only a few of the shared outcome values. | `metric-catalog.md §4 → *F12*` (the shared outcome enum is fixed; the live-combination count is an **observation-bounded** figure, and the family's **ceiling** is stated separately as the full lane × outcome product — see `§11 → *C80*`) + `§8.4` + `§12 → *open item 6*`. | A **shared, closed enum** keeps the vocabulary stable for alerting and dashboards; the live-combination count is a property of which lanes are enabled (they are individually kill-switchable), not of the label design. | **ACTIVE** |
| **G-8** | **The plan's open questions** (waiver choices the plan defers to P0). | `plan-overview.md §12 → *OQ-1*` … `*OQ-4*`. Dispositions: **OQ-1** — the catalog already answers it (gauges, not a histogram); adopt at P0. **OQ-2** — the textfile dir is a managed key; needs a devops decision on a dedicated mount. **OQ-3** — recommend the version label **only** on the current-release gauge (bounded by the retention release set). **OQ-4** — the catalog's two per-event families already cover the intent, so no new event-kind vocabulary is required. | These are **P0 decisions with the catalog's recommendation already stated** — deferring the choice to P0 keeps the vocabulary freeze the single moment where label sets are pinned. OQ-1 and OQ-4 are closed by the catalog; OQ-2 and OQ-3 remain genuine devops/reviewer choices. | **ACTIVE** |
| **G-9** | **Any alert over a reset-on-restart counter must use `rate()`/`increase()`** — never a raw comparison, never `absent()`. | `metric-catalog.md §10.1 → *durability table*` + `§10.2` (every alert over a reset-on-restart counter is restated) + `§6 → *degradation contract*`. | A raw comparison reads "0" right after a restart and produces a **false negative for the whole fill window** — the alert would be blind exactly when the daemon comes back. | **ACTIVE** |
| **G-10** | **LLM token counts are a stale/under-counting proxy** and are lost when the backend omits the final usage chunk. | `metric-catalog.md §5 → *F14*` (the usage metadata is "never the primary"; the gap is lost entirely when the final usage chunk is absent) + the missing-usage counter + `§11 → *C8*`. | The caveat is made **visible as a counter** rather than hidden in a docstring: an operator can see how many observations had no usage metadata, so a low token total is never mistaken for a quiet hour. Non-streaming call sites increment the missing counter instead. | **ACTIVE** |
| **G-11** | **Queue-item durations are not derivable.** | `metric-catalog.md §4 → *F10*` + `§11 → *C15*` (the queue table carries only a created-at column, as TEXT ISO; no started-at / completed-at). | No duration family is claimed for queue items. Claiming one would require a schema change on existing data that this plan does not authorize — so the metric is **absent by design** rather than wrong. | **ACTIVE** |
| **G-12** | **The manager pool renders no series for the pool request-error counter.** | `metric-catalog.md §3 → *F4*` (the SQLAlchemy manager pool raises a checkout timeout and has **no** `requests_errors` equivalent) + `§8.9`. | An **absence, not a gap**: the manager pool's checkout failures are observable through the readiness composite, not through a psycopg-shaped counter. The family must not invent a parallel counter for it. | **ACTIVE** |
| **G-13** | **The leak-site map is a closed set with a residual bucket.** | `metric-catalog.md §9.2 → *leak site*` (the residual bucket is "**never dropped**, so the census total always equals the sum") + `§3 → *F6*` + `§8.9 → *leak site*`. | The census total must equal the sum of the site series at all times; a growing residual bucket is the **signal** to extend the map through a deliberate, reviewed change rather than an automatic label drift. | **ACTIVE** |
| **G-14** | ~~The catalog header said "OpenMetrics text"~~ — **CLOSED in the alignment pass (H-1)**: the header now reads "**Prometheus classic text exposition format** (NOT OpenMetrics)", matching the plan and the MA-16 resolution. | `metric-catalog.md` header (compat line) — edited; `plan-overview.md §3 → *Dependency-free renderer (decision 5 + MA-16, REAFFIRMED loudly)*`. | The catalog's substantive position was already unambiguous (the renderer is the existing classic-text renderer); only the wording drifted. Now the wording matches the decision, so a reader cannot re-import the mislabel. | **ACTIVE** — closed |
| **G-15** | ~~F0.1's series estimate mentioned an info split~~ — **CLOSED in the alignment pass (H-2)**: the split is dropped, the metric is a single no-label scalar, and the family entry, the §1 summary row and the §7 roll-up now agree (values: `metric-catalog.md §14.3 → *F0.1*`, `§14.1 → *Daemon-origin total*`). | `metric-catalog.md §2 → *F0.1*` + `§1` + `§7` — edited. | The no-label shape was already the catalog's dominant reading; the stray split parenthetical was the outlier. Dropping it removes an unbounded-label risk and makes the budget proof arithmetically true end to end. | **ACTIVE** — closed |
| **G-16** | **The plan's incident table names three metrics the catalog does not ship.** | `plan-overview.md §1 → *incident table*` (not edited — §1 is outside this task's permitted edit scope). | The catalog's equivalents are the native-pool family + the derived pool gauges and the leak counter; **no** checkout-latency family ships in v2 (the checkout seam is deferred — CR-4). Carried as open item **H-3** with the corrected names. | **ACTIVE** |
| **G-17** | **The tool-category label set is derived at runtime, never hand-listed** — the closed list is deliberately **not** written out. | `metric-catalog.md §8.6 → *tool category*` (the runtime source is the registry's category-module map; the V-4 generated artifact is canonical) + `§8 → *opening rule*` + `§0.5` + `§5 → *F20*` + `§11 → *C27*` + `§11 → *C86*`; `plan-overview.md §4 → *P0 task 6*`. | A hand-written list is the drift class the architect falsified. The **derivation** is the contract; a cross-check test pins the annex to the runtime source at P0. A tool whose category is not in the runtime-derived set folds to the reserved bucket and is counted — never silently inflating cardinality. | **ACTIVE** |
| **G-18** | **Prometheus storage loss is unrecoverable** — v2 runs a **single local TSDB**, so a host loss takes the whole alerting history with it. | `plan-overview.md §4 → *P2 risk row 4*` + `§2 → *Deploy target (RATIFIED, decision 1)*` + `§4 → *P2 exit criteria*`; `metric-catalog.md §10.0 → *restart-loss table*` (the TSDB row: "all history lost — remote_write to a durable store, out of v2 scope"). | **Accepted for v2** (the architect's Q2 default — see `decisions.md §ACTIVE → §I → row 2`). The deploy target is host binaries (decision 1), which makes host loss the dominant scenario — but the deferral is explicit, recorded in the risk register, and `remote_write` is named as a **separate project**, not a v2 task. | **ACTIVE** |
| **G-19** | **`ensemble_turn_duration_seconds` is anchored, with a drop-condition** — the tap measures its own latency (including its own DB write), which would be DB-write latency mislabelled as turn duration. | `metric-catalog.md §5 → *F20*` (the "Anchor pair specified" paragraph — entry-tap timestamp → node-return timestamp, carried on the §8.13 carrier) + `§11 → *C31*`; `plan-overview.md §4 → *P1a task 6*`. | The architect allowed "specify the anchor pair **or** drop the family". v3 **specifies** the pair and keeps a **drop-condition**: a source with no entry-tap counterpart contributes **no observation** (absent ≠ zero) and the family is dropped for that source rather than shipped mislabelled. | **ACTIVE** |
| **G-20** | **The plan's phase headings and the catalog's ship phases used two different partitions** — **RESOLVED** by the leader-sanctioned unification: one canonical partition, family → phase 1:1, zero exceptions. | `metric-catalog.md §7 → *Intra-family phased rollouts (G-20/H-12 unification)*` + `§7.1 → *Task / owner mapping*`; `plan-overview.md §4 → *Coupling map*`. | The catalog's §1/§7 phase column is authoritative for the roll-up and for "which phase must complete before a family ships"; the plan's §4 headings are work-package labels. The intra-family phased-rollout convention (task-level early-ship note, family phase = completion phase) is stated in the catalog. The historical divergence is recorded in the catalog's corrections log, not hidden. | **ACTIVE** — resolved |
| **G-21** | **Per-child RSS/CPU attribution on macOS is a decided non-goal** — per-child families are dark on Darwin; no partial per-child data ships on macOS; a future per-pid Darwin story is a new, separately-scoped decision. | `plan-overview.md §11 → *Non-goals*` + `§2 → *Deploy target (RATIFIED, decision 1)*` + `§6 → *degradation matrix*`; `metric-catalog.md §13.4 → *Non-goal*` + `§13.1` + `§13.2`. | **Ratified by the planner under mission authority "make it an explicit decision"** (available for leader override). The Darwin reader is a coarse **daemon/process-level** view, not a per-pid sampler; per-pid sampling has no Darwin path even post-merge. Promising per-child on macOS would ship a silently-empty series. | **ACTIVE** |
| **G-22** | **Platform-field nuances:** the peak-RSS field is supplied on Darwin post-merge via the OS-reported value (the "Linux-only" label attached to it is wrong for the post-merge reader); the anonymous-memory field degrades to the RSS proxy on Darwin (not `None`), making the coverage percentage RSS-denominated and not comparable with the Linux anonymous-memory basis; the swap field is **unwired on every platform today** (the column exists, no reader populates it). | `metric-catalog.md §13.1 → *field-level split table*` + `§13.2` + `§13.3 → *payload shape*`; `plan-overview.md §3 → *platform support summary*` + `§6 → *degradation matrix*`. | These are **documented degradations, not defects** — the consumer-visible shape changes are pinned so a Darwin host never silently renders a Linux-shaped payload. The pid-map census-moment figures differ (the same climbing counter, different observation moment); no action. | **ACTIVE** |
| **G-23** | **The F14 streaming-only gap** — the LLM token hook sits in the streaming-path converter; the non-streaming invoke sites are not captured ⇒ under-count risk. | `metric-catalog.md §5 → *F14*` (the R-m4 paragraph) + `§11 → *C42*`. | **Noted, not fixed** (accepted limitation, or close by adding observations). The series estimate is unchanged; it is a coverage note only. | **ACTIVE** — noted |

## G. v3 architecture round — the amendments (canonical §10 numbering)

**Source.** `architecture-recommendation.md` §10 "LIST OF REQUIRED PLAN AMENDMENTS", numbered
**1–28**, grouped **A–G**. The numbering below is that canonical one — **not** the draft
"A1…A20" labels used inside the architect's prose (that is a *different* scheme; see
`§ACTIVE → §Q → row *label-normalisation ledger*`).

**How each row was produced.** Every amendment was **materially verified** against the two
sibling files **and** the worktree code before its row was written: the claimed anchor was
opened, the substance was confirmed, and the code citations were re-verified in the worktree.
Where the substance was **missing or thin**, the amendment was **implemented first** and the row
says so. **No amendment is waived; no row is missing.**

### G.A — Metric catalog: vocabularies and families (§10 group A, amendments 1–8)

| # | Prescription (architect §10) | HOW resolved | Anchor (content-anchored) | Status |
|---|---|---|---|---|
| **1** | The tool-category vocabulary must be **derived at runtime**; apply the same rule to every vocabulary: **derived from code, never hand-listed.** | The vocabulary is **re-derived from code** — the registry's runtime category source is the source of truth, and the **V-4 generated artifact** is canonical (CI/test-generated + drift check). The label set is the runtime-derived set **+ a reserved bucket**. The rule was **generalized**: the vocabulary annex now opens with "every closed vocabulary in this annex is DERIVED FROM CODE, never hand-listed", citing the sweep-lane drift as the precedent class. F20's estimate moved accordingly; the §1 total and the §7 roll-up were re-based (values: `metric-catalog.md §14.3 → *F20*`, `§14.1 → *Daemon-origin total*`). | `metric-catalog.md §8.6 → *tool category*` + `§8 → *opening rule*` + `§5 → *F20*` + `§1` + `§7 → *Daemon total*` + `§11 → *C27*` + `§11 → *C86*`; `plan-overview.md §4 → *P0 task 6*` | **ACTIVE** |
| **2** | F14 — change the observation site to the **terminal-usage branch**, mandate a **per-call dedupe**, and state the "terminal-chunk only, never per-chunk" rule. | Both files now name the hook: the terminal-usage branch (the path that carries the token counts and **returns early**), with the aggregated call site as the alternative; the previously-cited per-chunk path is explicitly named as the path that must **not** be hooked. A **per-call dedupe** is mandated (some OpenAI-compatible backends attach usage to every chunk ⇒ double counting). The "terminal-chunk only, never per-chunk" rule is stated verbatim. The plan-side carrier (a P1a task) was added so the plan cannot be implemented from the stale hook. | `metric-catalog.md §5 → *F14*` + `§1` + `§11 → *C28*`; `plan-overview.md §4 → *P1a task 4*` + `§4 → *P1a risk 1*` | **ACTIVE** |
| **3** | §8.13 + m1 — split the wake-outcome producer into (a) the **claim call** and (b) the **episode seam**; each computed once, each carried. | §8.13 now names **both** producers and the outcomes each can yield — (a) the **claim call** `daemon/services/worker_pool.py:302` (the call that takes a task from the task queue) and (b) the **episode seam** `daemon/graph.py:7319` (`_detect_fresh_episode_reset`). The worker-pool `_stats` dict literal is the source of the **claim-side outcome vocabulary** — it is **not** the claim call. The **canonical carrier producer** is `daemon/services/task_processor.py:569-598` (the fresh-episode flag computed once and carried on `ProcessingContext`), with the carrier field at `daemon/services/message_processing_pipeline.py:176` and the ledger identity at `daemon/services/instance_messaging.py:2611-2613`. Each value is computed **once** and carried on the same carrier; neither re-derives from partial signals. | `metric-catalog.md §8.13` + `§11 → *C29*` + `§11 → *C73*` + `§11 → *C92*` + `§14.4 → *wake-turn outcome*`; `plan-overview.md §4 → *P1b task 9*` + `§4 → *P1b exit criteria*` + `§4 → *P1b risk row 4*` | **ACTIVE** |
| **4** | §8.13 + m1 — replace "the exposition renderer reads the carried value and increments" with "**a live turn-end consumer increments the counter; the renderer only reads the accumulated counter**". | §8.13's consumer bullet is rewritten to that contract, with the reason stated (the carrier field lives on `ProcessingContext` and **dies with the message**; the renderer runs at scrape time and sees only the registry) and the blueprint's rule preserved verbatim — "**consumer-seam re-derivation is forbidden**". Mirrored in the plan's P1b task, exit criteria and risk row. | `metric-catalog.md §8.13 → *consumer*` + `§11 → *C29*` + `§11 → *C73*`; `plan-overview.md §4 → *P1b task 9*` + `§4 → *P1b exit criteria*` + `§4 → *P1b risk row 4*` | **ACTIVE** |
| **5** | F20 — define the counting unit on the HELP text; specify the anchor pair for the turn-duration histogram **or** drop that family; specify the tool→category lookup with a fallback. | The counting unit is stated ("**one `tap_node_return` invocation**"). The anchor pair is now **specified** — the elapsed time between the **entry tap** and the **node-return tap**, carried on the §8.13 carrier and **never re-derived** at the consumer — with a **drop-condition**: a source with no entry-tap counterpart contributes **no observation** (absent ≠ zero) and the family is dropped for that source rather than shipped mislabelled. The tool→category lookup states the reserved fallback. | `metric-catalog.md §5 → *F20*` + `§11 → *C31*`; `plan-overview.md §4 → *P1a task 6*` | **ACTIVE** — applied-with-adjustment (the pair is specified and a per-source drop-condition kept; recorded as G-19) |
| **6** | F7 + plan §1 — add a **task** to instrument the checkpoint write path; until it lands, narrow the declared `op` enum to the observable set and restate the write-side incident mapping. | The `op` enum is **narrowed to the observable set** in both files, §1's write-side incident mapping is restated (writes are a stall/contention artifact, not throughput), **and a distinct write-side hook now exists**: `_charge_checkpoint_write` — **its own function, its own budget and labels, once per outer call, and a `None`-on-write is an error, not a skip** — called from the adapter's write path. The **read charge seam is NOT extended to writes** (routing write bytes through the read charge would bill them to the READ budget). The write path carries **its own risk row**. | `metric-catalog.md §4 → *F7*` + `§11 → *C32*` + `§11 → *C49*`; `plan-overview.md §1 → *Write-side restatement (A6/A7)*` + `§4 → *P1b task 14*` + `§4 → *P1b risk row 3*` + `§13.1.1` | **ACTIVE** |
| **7** | F7 / §0.4 / §8.11 — after the write-path seam lands, re-derive the checkpoint `op` enum from **observed call sites** rather than inheriting the legacy HELP text. | Stated in the catalog's F7 entry and carried into the new plan task's acceptance. The vocabulary annex still shows the legacy **inherited** set with its source citation — it is the **inherited** set, explicitly marked re-derivable, not a frozen contract. | `metric-catalog.md §4 → *F7*` + `§8.9 → *checkpoint op*`; `plan-overview.md §4 → *P1b task 14*` | **ACTIVE** |
| **8** | F0.2 — clarify that the registry-measurements metric counts **measurements**, not series, so dashboards cannot conflate it with the series budget. | F0.2 carries an explicit clarification bullet: it counts **measurements** (registered measurement objects), **NOT** series, with the budget warning inline — and the CR-3 correction (registry wired to collectors) alongside it. | `metric-catalog.md §2 → *F0.2*` + `§11 → *C33*` | **ACTIVE** |

### G.B — Registry mechanics (catalog §0.2 / plan §3) (§10 group B, amendments 9–11)

| # | Prescription (architect §10) | HOW resolved | Anchor (content-anchored) | Status |
|---|---|---|---|---|
| **9** | Adopt the locking contract: per-metric locks (no global registry lock), **non-blocking render snapshot** (try-acquire → last-good published snapshot + a contention comment line), and **non-cumulative bucket counts** accumulated at render. | All three clauses are in **both** files: per-metric locks only (one wedged writer must not stall every family); render uses **non-blocking try-acquire** and on contention serves the **last published snapshot** plus a per-family contention comment; writers update their family's state under its own short lock and atomically swap a published pointer (copy-on-publish); bucket counts are stored **non-cumulative** and accumulated at render, so an observe is O(1). The contract now **names the defect it fixes** — the renderer reaches series through the per-object getters, each of which takes that object's lock, so a wedged writer would make the **scrape hang**. | `metric-catalog.md §0.2` + `§11 → *C52*`; `plan-overview.md §3 → *Separate localhost-bound /metrics listener (decision 3 + CR-2)*` (the locking contract) + `§6 → *degradation matrix*` + `§4 → *P0 test plan*` | **ACTIVE** |
| **10** | §0.5 + plan §7 — make the never-exported list **executable**: registration-time enum pin, observe-time reserved bucket + the invalid-label counter, render-time allowlist predicate, a **hard** cardinality cap, a name regex, a label-value charset gate; add the poisoned-registry defence-in-depth test. | The three enforcement tiers are written out in **both** files (registration-time enum pin raising on a wrong label key; observe-time fold-to-reserved counted in the invalid-label counter; render-time allowlist predicate), plus the **hard** cap, the name regex, the charset gate and the poisoned-registry test. The plan's own soft/hard contradiction was reconciled (the cap is hard, stated once). | `metric-catalog.md §0.5` + `§2 → *F0.2*` + `§11 → *C51*` + `§11 → *C64*` + `§11 → *C77*`; `plan-overview.md §7 → *never-exported list*` + `§5 → *managed keys*` | **ACTIVE** |
| **11** | §0.3 / CR-3 — specify alias mechanics: dual exposition of one series, a one-shot deprecation log, a removal procedure with an `absent()` guard, and migration of the legacy-name test files. | §0.3 carries all four mechanics: the alias is a **second exposition of the same series** (never a second measurement); a deprecation log line at first emission, once per process; a removal procedure with a documented `absent()` guard; the legacy names are **alias-only, never canonical**; and the **test files** referencing them are named for migration. Aliases are registered at registry-construction time so a cold start still emits them. | `metric-catalog.md §0.3` + `§0.2 → *freeze rules*`; `plan-overview.md §3 → *Freeze rules (A27)*` | **ACTIVE** |

### G.C — Listener + kill-switch (plan §3/§4/§5/§6, catalog §0.7/§0.8) (§10 group C, amendments 12–16)

| # | Prescription (architect §10) | HOW resolved | Anchor (content-anchored) | Status |
|---|---|---|---|---|
| **12** | §3 / catalog §0.7 — state the listener's **thread/loop topology** and the **render-never-blocks** rule; state the GIL split (an I/O-bound wedge survives; a CPU-spin wedge does **not** → the out-of-process witnesses remain mandatory). | The topology is stated as **Option A**: a **dedicated daemon thread** running the stdlib threading HTTP server bound to `127.0.0.1`, started from the FastAPI lifespan; a shared-main-event-loop listener is **REJECTED** (it inherits the CR-1 failure). The render-never-blocks contract is restated in the same paragraph, and the **GIL split** is stated with its consequence, so the out-of-process witnesses remain **mandatory**, not optional. | `plan-overview.md §3 → *Listener thread/loop topology (A12, CR-1 mitigation)*` + `§6 → *degradation matrix*`; `metric-catalog.md §0.7` | **ACTIVE** |
| **13** | §4 P0 T1 — specify boot placement, bind-failure degradation, and teardown order (stop event → shutdown → **detached join**, `daemon=True` thread). | All three are in the plan's §3 **and** in the P0 task's acceptance: boot inside the **FastAPI lifespan**, after the resource monitor and after the stream watchdog; a bind failure degrades the listener to **disabled + logged** (never abort boot — decision 3); teardown = stop event → server shutdown → **detached join (no blocking join; the `daemon=True` thread dies with the process)** → close, **after** the resource-monitor stop and the wedge-capture stop, aligned to the house detached-join precedent. The earlier blocking-join teardown wording is **SUPERSEDED** (see `decisions.md §SUPERSEDED → row S-4`); a wedged join must never block the event loop. | `plan-overview.md §3 → *Boot/teardown lifecycle (A13)*` + `§4 → *P0 task 1*`; `metric-catalog.md §11 → *C50*` | **ACTIVE** |
| **14** | §4 P0 T3 / §5 / catalog §0.8 — specify the **truthy set** and the layering rule (**env explicit-OFF always wins; env UNSET defers to the managed-key chain**); add the boot log line. | The truthy set is fail-closed and exhaustive; the layering table is written out (unset + managed key on ⇒ ON; unset + default ⇒ OFF; explicit env OFF always wins; explicit env ON overrides the managed-key default); the **one boot log line** is quoted verbatim; and the per-request read stays (no TTL cache, which would defeat the emergency-off purpose). | `plan-overview.md §4 → *P0 task 3*` + `§5 → *kill-switch*` + `§6 → *degradation matrix*`; `metric-catalog.md §0.8` + `§11 → *C74*` | **ACTIVE** |
| **15** | §4 P0 / §6 — the degradation matrix must cover every dark-able surface, including the env/managed-key combination, the render-contention path, the CPU-spin wedge and the non-Linux host. | The matrix carries the missing rows; each dark-able acceptance criterion carries an explicit precondition row. | `plan-overview.md §6 → *degradation contract*` + `§10 → *AC2*` + `§10 → *AC6*`; `metric-catalog.md §11 → *C45*` + `§13.2` | **ACTIVE** |
| **16** | §4 P0 — the P0 exit criteria must be an executable list, not prose. | The P0 exit criteria are an itemised list, each item naming its artefact and its check. | `plan-overview.md §4 → *P0 exit criteria*` + `§4 → *P0 test plan*`; `metric-catalog.md §11 → *C57*` | **ACTIVE** |

### G.D — Census (catalog §9, plan §4 P1b) (§10 group D, amendment 17)

| # | Prescription (architect §10) | HOW resolved | Anchor (content-anchored) | Status |
|---|---|---|---|---|
| **17** | The census must have a dedicated execution home, a bounded statement timeout, and a documented self-exclusion rule. | The census runs on a dedicated non-pooled connection with a bounded connect/statement timeout, on its own daemon thread, and the census's own sessions are excluded from the census. | `metric-catalog.md §9` + `§9.1` + `§11 → *C34*`; `plan-overview.md §4 → *P0 task 7*` + `§4 → *P0 task 10*` + `§10 → *AC5*` | **ACTIVE** |

### G.E — Storage split + durability (plan §3, catalog §10) (§10 group E, amendments 18–21)

| # | Prescription (architect §10) | HOW resolved | Anchor (content-anchored) | Status |
|---|---|---|---|---|
| **18** | §3 — state the **storage-split boundary rule** explicitly. | Per-instance attribution is **PG-only** via the resource API; every daemon-origin `/metrics` family is **aggregate**; **no daemon-origin family carries a per-instance label**. Enforced in the catalog's vocabulary annex as a cardinality rule (not just stated). | `plan-overview.md §3 → *Storage-split boundary rule (A18)*` + `§2 → *Cardinality budget (MA-10)*`; `metric-catalog.md §8 → *opening rule*` | **ACTIVE** |
| **19** | §3 — state the PG retention ladder and the interplay with the Prometheus window. | The resource-monitor PG tiers are stated (raw / minute-rollup / hour-rollup) with their configured windows; the interplay rule is stated: any quantity visible on both sides is governed by **Prometheus for alerting** and by **PG for per-instance API views**, and questions beyond the Prometheus window are answered from the PG hour-rollup tier. | `plan-overview.md §3 → *PG retention ladder (A19)*` + `§2 → *Deploy target (RATIFIED, decision 1)*`; `metric-catalog.md §0.6` | **ACTIVE** |
| **20** | §3 — the two samplers (daemon-side and textfile-side) must agree on what a "sample" is, and must not double-count. | The two-sampler consistency rule is stated: the daemon-side sampler observes the daemon's own state; the out-of-process sampler observes the OS's view of the named processes; the two never observe the same subject. | `plan-overview.md §3 → *Textfile collector (R18, genuinely new)*` + `§4 → *P2 task 1*`; `metric-catalog.md §6 → *F22*` + `§13.2` | **ACTIVE** |
| **21** | §10.0 — the restart-loss table must cover every idiom, not just the in-process one. | The restart-loss table has a row per idiom and per durable artefact, naming what survives a restart and what does not, and pointing the durable loss at the remote-store deferral. | `metric-catalog.md §10.0 → *restart-loss table*` + `§10 → *durability table*`; `plan-overview.md §6 → *degradation contract*` | **ACTIVE** |

### G.F — Phasing (plan §4) (§10 group F, amendments 22–26)

| # | Prescription (architect §10) | HOW resolved | Anchor (content-anchored) | Status |
|---|---|---|---|---|
| **22** | §4 — the P0 work must be split into a **core** spine and the remaining foundation work, with the split stated. | The P0 work-package split (core spine vs the rest) is stated, with the dependency that the spine's data path must exist before the spine's health gate is meaningful. | `plan-overview.md §4 → *P0 — Foundation*` + `§4 → *Coupling map*`; `metric-catalog.md §11 → *C48*` | **ACTIVE** |
| **23** | §4 — the worker-thread naming and the census execution home must be explicit prerequisites, not implicit. | Both are explicit P0 task rows with their acceptance criteria stated (the thread-naming task and the census-execution-home task). | `plan-overview.md §4 → *P0 task 9*` + `§4 → *P0 task 10*`; `metric-catalog.md §11 → *C75*` | **ACTIVE** |
| **24** | §4 — the vocabulary-freeze amendment must state how a vocabulary is added after the freeze (the amendment procedure). | The freeze rules state the amendment procedure: a new label value is added by a deliberate, reviewed change that bumps the freeze marker, and the frozen set is re-derived from the generated artifact — never hand-edited. | `plan-overview.md §3 → *Freeze rules (A27)*` + `§4 → *P0 task 6*`; `metric-catalog.md §0.2 → *freeze rules*` + `§8.1 → *instance lifecycle event*` | **ACTIVE** |
| **25** | §4 — F22's minimal textfile writer must ship early (a task-level carve-out) without breaking the family→phase 1:1 rule. | The carve-out convention is stated: a family occupies exactly one phase (the phase in which it **completes**), and an early partial ship is expressed as a **task-level early-ship note inside the owning phase's task list**. F22's minimal writer is that carve-out. | `metric-catalog.md §7 → *Intra-family phased rollouts (G-20/H-12 unification)*` + `§7 → *Phase split (F22)*`; `plan-overview.md §4 → *P1b task 13*` + `§4 → *P2 task 1*` | **ACTIVE** |
| **26** | §8 — the alert-authoring dependency must be explicit: a rule may only reference a series that a named phase produces. | The rule is stated in the alert section and is enforced by the rule→series→phase census (every referenced series has a catalog row AND appears in the family list of a named phase task row); the census result is recorded in `§ACTIVE → §Q`. | `plan-overview.md §8 → *alert table*` + `§8 → *series-producibility rule*`; `metric-catalog.md §8 → *opening rule*` + `§11 → *C47*` | **ACTIVE** |

### G.G — Cross-cutting (§10 group G, amendments 27–28)

| # | Prescription (architect §10) | HOW resolved | Anchor (content-anchored) | Status |
|---|---|---|---|---|
| **27** | §0.2 / §3 — the freeze rules must state that the listener is imported on the **lifespan path** (not the main app module), so the packaged binary keeps the analyzer's view correct. | The freeze rules state the import constraint verbatim in both files, and the paragraph is complete (no dangling fragment). | `metric-catalog.md §0.2 → *freeze rules*` + `§11 → *C75*`; `plan-overview.md §3 → *Freeze rules (A27)*` | **ACTIVE** |
| **28** | §3 / §0.2 — the off-loop discipline must be stated precisely (it is the rule that keeps the metrics from causing the incident they measure). | The accurate rule is stated: **no blocking work, no I/O, no logging and no per-chunk work on the event loop**, plus a bounded clock read per observation window; background threads and atomic counters stay off-loop; the existing lag probe and wedge watchdog are the templates; one proc dialect; the per-request kill-switch read; the tap increments sit inside the tap's own error boundary so a metric error cannot produce per-turn warning noise. | `plan-overview.md §3 → *Off-loop discipline (restated, A28)*`; `metric-catalog.md §0.2` + `§11 → *C66*` | **ACTIVE** |

**Amendment count check:** group A = 8 (1–8) · B = 3 (9–11) · C = 5 (12–16) · D = 1 (17) ·
E = 4 (18–21) · F = 5 (22–26) · G = 2 (27–28) → **28 rows, no gaps, no merges, none waived.**

## H. The five architect-critical findings — defects that ship a well-formed but EMPTY series

Each would have shipped a series that looks right and contains nothing, and **no phase exit
criterion would catch it**. Each was re-verified against the worktree code before the row was
written.

| # | Finding (architect) | Why it would ship silently wrong | HOW resolved | Anchor (content-anchored) | Status |
|---|---|---|---|---|---|
| **J-1** | **F14's cited hook misses the terminal usage chunk** — the family would ship near-zero. | The cited path is the **non-terminal** path, reached only when the chunk also carries choices. A terminal usage chunk (the shape that carries the actual token counts) takes the early-return branch and is attached through the **constructor** — so a counter hooked only at the cited line never fires. Worse, the cited line is **per-chunk** — the opposite of the plan's hot-path rule. | The observation site is now the **terminal-usage branch** (or the aggregated call site), with a mandatory **per-call dedupe** and the explicit rule "**terminal-chunk only, never per-chunk**". Both the catalog family entry **and** the plan's P1a task carry it. | `metric-catalog.md §5 → *F14*` + `§1` + `§11 → *C28*`; `plan-overview.md §4 → *P1a task 4*` | **ACTIVE** |
| **J-2** | **`ensemble_wake_turns_total` never counts** — the LostWake alert would be blind. | §8.13's consumer was "the exposition renderer reads the carried value and increments" — but the carrier field lives on `ProcessingContext` and **dies with the message**; the renderer runs at **scrape time** and sees only the registry. And the named producer could yield only a **subset** of the outcome values (the rest live at different seams). | **Two producers**, each computed **once** and carried: (a) the **claim call** `daemon/services/worker_pool.py:302`, (b) the **episode seam** `daemon/graph.py:7319`. The consumer is now "**a live turn-end consumer increments the counter; the renderer only reads the accumulated counter**" — with the blueprint's "consumer-seam re-derivation is forbidden" rule preserved. The canonical carrier producer is `daemon/services/task_processor.py:569-598` (carrier field `daemon/services/message_processing_pipeline.py:176`; ledger identity `daemon/services/instance_messaging.py:2611-2613`). | `metric-catalog.md §8.13` + `§11 → *C29*` + `§11 → *C73*` + `§11 → *C92*` + `§14.4 → *wake-turn outcome*`; `plan-overview.md §4 → *P1b task 9*` + `§4 → *P1b exit criteria*` + `§4 → *P1b risk row 4*` | **ACTIVE** |
| **J-3** | **The tool-category closed enum is falsified** — the catalog's hand-count did not match the tree. | The catalog stated a hand-written list was "the complete set of registration calls". Under a closed-enum registry an unregistered value either **raises at observe** (the family breaks) or **silently inflates cardinality** past the budget — and the budget itself was computed from the falsified list. | The vocabulary is **derived at runtime** from the registry's category source, with a **reserved bucket** for the tool→category lookup (a tool outside the set folds to the reserved bucket and is counted in the invalid-label counter), plus a cross-check test. The lesson was **generalized**: the vocabulary annex now opens with "every closed vocabulary is derived from code, never hand-listed" (the sweep-lane drift is cited as the precedent class). The **V-4 generated artifact** is canonical; the freeze-time value is an observation in this worktree, not a hand list. | `metric-catalog.md §8.6 → *tool category*` + `§8 → *opening rule*` + `§5 → *F20*` + `§11 → *C27*` + `§11 → *C40*` + `§11 → *C86*` + `§14.4 → *tool category*`; `plan-overview.md §4 → *P0 task 6*` + `§10 → *AC12*` | **ACTIVE** |
| **J-4** | **`ensemble_checkpoint_op_seconds{op="aput"}` can never fire** — the declared `op` enum over-promises. | The saver-op wrapper has exactly **one** non-test caller, and it is the read path. The checkpoint **write** path runs through the adapter and never touches the wrapper, so the family would declare several `op` values and emit one. | The `op` enum is **narrowed to the observable set** until a **distinct write-side hook** lands: `_charge_checkpoint_write` — its own function, its own budget and labels, once per outer call, and a `None`-on-write is an **error, not a skip** — called from the adapter's write path. The **read charge seam is not reused for writes**. That task now exists with **its own risk row** (the Q1 carrier, see `§ACTIVE → §I → row 1` and the reversibility rows). The plan's write-side incident mapping is restated (writes are a stall/contention artifact, not throughput). | `metric-catalog.md §4 → *F7*` + `§11 → *C32*` + `§11 → *C49*`; `plan-overview.md §1 → *Write-side restatement (A6/A7)*` + `§4 → *P1b task 14*` + `§4 → *P1b risk row 3*` + `§13.1.1` | **ACTIVE** |
| **J-5** | **Render can block on a wedged writer** — the CR-1 failure reproduced inside the CR-1 mitigation. | The registry holds a **per-object** lock, and the renderer reaches series through the getters, which **take that lock**. A wedged thread holding one metric's lock would make the **scrape hang** — the exact failure the listener exists to avoid. | The locking contract: **per-metric locks only, no global registry lock**; **render never blocks** — non-blocking try-acquire → serve the **last published snapshot** + a contention comment; **copy-on-publish**; **non-cumulative bucket counts** accumulated at render. The contract also states the **defect-naming clause** (what it fixes), plus the matrix row and the wedged-writer test. | `metric-catalog.md §0.2` + `§11 → *C52*`; `plan-overview.md §3 → *Listener thread/loop topology (A12, CR-1 mitigation)*` + `§6 → *degradation matrix*` + `§4 → *P0 test plan*` | **ACTIVE** |

## I. Leader decisions — the architect's two open questions (Q1/Q2)

Both were resolved by the **planner, adopting the architect's stated defaults** (the architect
framed both as "genuinely the leader's call" and supplied an actionable default for each).

| # | Question (architect §11) | The adopted answer (as recorded) | Anchor (content-anchored) | Status |
|---|---|---|---|---|
| **Q1** | **Checkpoint write-path instrumentation — how much write-path risk is acceptable?** The architect's default: "instrument the adapter seam in P1b with its own risk row — the read path is where the incident actually lives, but an unobservable write path is a hole an operator will trust wrongly." | **ADOPTED — instrument the write path in P1b with a DISTINCT write-side hook** (`_charge_checkpoint_write`: own function, own budget/labels, once per outer call, `None`-on-write is an error, not a skip), carried as a **P1b task with a dedicated risk row**, with the declared `op` enum narrowed to the observable set until the hook lands. The **read charge seam is not extended to the write path**. | `plan-overview.md §4 → *P1b task 14*` + `§4 → *P1b risk row 3*` + `§1 → *Write-side restatement (A6/A7)*` + `§13.1.1`; `metric-catalog.md §4 → *F7*` + `§11 → *C32*` + `§11 → *C49*`; `decisions.md §ACTIVE → §H → row 4` | **ACTIVE** |
| **Q2** | **Prometheus storage-loss exposure.** The architect's default: "accept for v2 and record it as a known limitation; remote_write is a separate project." | **ADOPTED — accept a single local Prometheus TSDB for v2; host-storage loss is recorded as a known limitation; `remote_write` to a durable store is a separate project, explicitly out of scope for v2.** The limitation is carried in three places so it cannot be lost: the P2 risk row, the catalog's restart-loss mitigation column, and the §F register (G-18). The deploy target is host binaries (decision 1), which is what makes host loss the dominant scenario — which is why the deferral is recorded rather than left implicit. | `plan-overview.md §4 → *P2 risk row 4*` + `§2 → *Deploy target (RATIFIED, decision 1)*`; `metric-catalog.md §10.0 → *restart-loss table*`; `decisions.md §ACTIVE → §F → row G-18` | **ACTIVE** |

## J. Record correction — the CR-3 reuse story (the dispatch brief's claim was wrong)

**The claim that was wrong.** The dispatch brief (and this register's reuse row for the registry)
said the checkpoint-metrics singletons have "zero non-test callers" — i.e. an unwired registry.

**The truth (re-verified in-tree).** The two singletons **DO** have a production consumer:
the perf module **imports** them, **observes** into the histogram on the log-gated and ungated
paths, and **increments** the list counter.

**Only `render_metrics()` itself is unwired** (zero non-test callers in production — the sole
test caller is named in the catalog's corrections log).

**The wording, used everywhere from here on:** **"registry wired to collectors, exposition
unwired."** Not "unwired registry". The two halves are different facts and the catalog states
both.

| # | Correction | Anchor (content-anchored) | Status |
|---|---|---|---|
| **L-1** | The reuse story for the checkpoint registry is "**registry wired to collectors, exposition unwired**". | `metric-catalog.md §0.2` + `§0.2 → *off-loop paragraph*` + `§2 → *F0.2*` + `§11 → *C30*`; `plan-overview.md §3 → *Separate localhost-bound /metrics listener (decision 3 + CR-2)*` + `§1 → *Write-side restatement (A6/A7)*` | **ACTIVE** |

**v2 history is annotated, not rewritten:** the CRITICAL row for the duplicated-histogram finding
and the reuse row for the registry each carry a one-line pointer to this section; their original
wording stands.

## K. Revision round — the round-001 blocking findings, as closed (13/13)

These rows record **what was decided** for the round-001 blocking findings. The per-round
finding→closure map (with the disposition label for each) is in
`decisions.md §DISPOSITION → §D.1`; each row there points back into this section.

| ID | The decision now in force | Anchor (content-anchored) | Status |
|---|---|---|---|
| **B-1** | The wake-outcome vocabulary is reconciled to the **closable** set; the family estimate is re-based on it. The producer attribution and the "no increment on a null outcome" rule are stated once, in the catalog. | `metric-catalog.md §14.4 → *wake-turn outcome*` + `§4 → *F13*` + `§8.1` + `§8.13` + `§14.3 → *F13*` + `§11 → *C39*` + `§11 → *C61*`; `decisions.md §ACTIVE → §M → row V-3` | **ACTIVE** |
| **B-2** | No shipped alert may reference a series that no phase and no catalog row assigns to produce. Three sub-cases are closed: the loop-lag sample series is produced by the P1a lag task and listed in that phase's family list; the textfile freshness gauge is catalogued in the textfile family; the false-degraded expression uses the two taxonomy members that actually exist. | `plan-overview.md §8 → *LoopStarved*` + `§8 → *FalseDegraded*` + `§8 → *TextfileStale*` + `§4 → *P1a task 1*`; `metric-catalog.md §3 → *F1*` + `§6 → *F22*` + `§10.2` + `§11 → *C59*` + `§11 → *C60*`; `decisions.md §ACTIVE → §M → rows V-1, V-2` | **ACTIVE** |
| **B-3** | The P2 textfile task's title and exit criterion state the **external sampler** as the writer; the house shell scripts are **observed, never modified**. | `plan-overview.md §4 → *P2 task 1*` + `§8 → *V-8 — BashSpin external sampler design (stand-alone)*` + `§3 → *Textfile collector (R18, genuinely new)*`; `metric-catalog.md §11 → *C43*` + `§11 → *C58*`; `decisions.md §ACTIVE → §D → row 2` | **ACTIVE** |
| **B-4** | The sweep cycle metric's **type** is stated once: the cycle counter is a **counter** (so `rate()` is valid) and the cycle duration is a separate **gauge** with no count series derived from it. | `plan-overview.md §4 → *P1b task 11*` + `§8 → *SweepHotLoop*`; `metric-catalog.md §4 → *F12*` + `§8.4` | **ACTIVE** |
| **B-5** | The vocabulary freeze pins the **full** sweep-lane set — the reconciliation lanes **plus** the standalone sweep services — not the lane subset alone. | `plan-overview.md §4 → *P0 task 6*`; `metric-catalog.md §4 → *F12*` + `§8.4 → *sweep lane*` + `§14.4 → *sweep lane*` + `§11 → *C14*` | **ACTIVE** |
| **B-6** | The wake-outcome vocabulary has **one** value set across the catalog (the closable set), and the family estimate follows it. | `metric-catalog.md §14.4 → *wake-turn outcome*` + `§4 → *F13*` + `§8.13` + `§14.3 → *F13*` + `§11 → *C39*` + `§11 → *C61*` | **ACTIVE** |
| **B-7** | The lifecycle-event vocabulary is **code-derived** from the instance-lifecycle publisher, with a reserved residual bucket and a stated amendment procedure. | `metric-catalog.md §4 → *F13*` + `§8.1 → *instance lifecycle event*` + `§14.4 → *instance lifecycle event*` + `§11 → *C62*`; `plan-overview.md §4 → *P1b task 12*`; `decisions.md §ACTIVE → §C → row m8` | **ACTIVE** |
| **B-8** | The job-queue family is **re-scoped** to the job queue only; the claim-latency series move to the worker-pool family and are sourced from the task queue's own columns, anchored on the claim call. | `metric-catalog.md §4 → *F10*` + `§4 → *F11*` + `§11 → *C63*` + `§11 → *C90*` + `§14.3 → *F10*` + `§14.3 → *F11*`; `plan-overview.md §4 → *P1b task 6*` | **ACTIVE** |
| **B-9** | The invalid-label counter is **catalogued** with a closed family-name dimension and a closed label-key dimension plus a reserved residual — no unbounded label. | `metric-catalog.md §2 → *F0.2*` + `§0.5` + `§14.3 → *F0.2*` + `§11 → *C64*` + `§11 → *C77*`; `plan-overview.md §4 → *P0 task 4*` | **ACTIVE** |
| **B-10** | The wake-counter producer anchor is **corrected**: the worker-pool `_stats` dict literal is the **outcome-vocabulary source**, the **claim call** is `daemon/services/worker_pool.py:302`, and the **canonical carrier producer** is `daemon/services/task_processor.py:569-598` (carrier field `daemon/services/message_processing_pipeline.py:176`; ledger identity `daemon/services/instance_messaging.py:2611-2613`). | `metric-catalog.md §11 → *C73*` + `§11 → *C92*` + `§8.13` + `§14.4 → *wake-turn outcome*`; `plan-overview.md §4 → *P1b task 9*`; `decisions.md §ACTIVE → §G → row 3` + `§H → row J-2` | **ACTIVE** |
| **B-11** | The register's own anchor column is **content-anchored** (section + row id), and the teardown row carries the detached-join form. | `decisions.md` preamble → *Rule 1 — anchors are content-anchored*; `decisions.md §ACTIVE → §G → row 13` + `§H → row J-2`; `metric-catalog.md §11 → *C50*` | **ACTIVE** |
| **B-12** | A **reversibility / back-out stance** exists: every risk row carries a BACK-OUT cell, the named prod-affecting changes each carry undo / trigger / verification, and the revert-only vs irreversible-by-design classification is stated. | `plan-overview.md §13 → *Reversibility & back-out*` + `§13.0` + `§13.1.1` … `§13.1.7` + `§13.2` + `§4 → *risk tables*`; `decisions.md §ACTIVE → §P` | **ACTIVE** |
| **B-13** | The budget proof has **one** source of truth: the catalog's reconciled ledger. The daemon-origin total, the per-phase split, the per-family values and the headroom are quoted **once** (in the catalog ledger) and **pointed at** everywhere else. | `metric-catalog.md §14 → *Reconciled counts ledger*` + `§14.1` + `§14.2` + `§14.3` + `§14.5` + `§14.6` + `§1` + `§7` + `§11 → *C65*` + `§11 → *C76*`; `plan-overview.md §2 → *Cardinality budget (MA-10)*` | **ACTIVE** |

## L. Revision round — the round-001 non-blocking findings, as closed (12/12)

| ID | The decision now in force | Anchor (content-anchored) | Status |
|---|---|---|---|
| **NB-1** | **Source-anchor corrections** — the harvested notes about mis-anchored sources are closed by the corrections that re-point the affected families: the job-queue/claim-latency re-scope, the claim-latency source chain on the worker-pool family, and the read-charge citation that covers both charge sites. (The approver tracking file carries the note *counts*, not the note texts, so this row names the **correction cluster** the placeholder covered; every harvested extra in the cluster has its own disposition row in `§DISPOSITION → §D.3`.) | `metric-catalog.md §11 → *C63*` + `§11 → *C90*` + `§11 → *C94*` + `§4 → *F11*` + `§14.3 → *F11*`; `decisions.md §DISPOSITION → §D.3 → rows H-027, H-028` | **ACTIVE** |
| **NB-2** | The register's stale cross-document line references are replaced by **content anchors** throughout (Rule 1). | `decisions.md` preamble → *Rule 1*; `decisions.md §DISPOSITION → §D.1 → row B-11` + `§D.4 → row R-6` | **ACTIVE** |
| **NB-3** | **Vocabulary-annex corrections** — the harvested notes about the annex's structure are closed by the corrections that fix the mis-filed family, the placeholder correction id, the unresolved editorial marker, the missing `file:line` anchors on five label sets, and the family mislabel in the ledger. (Same provenance note as NB-1; each extra has its own row in `§DISPOSITION → §D.3`.) | `metric-catalog.md §11 → *C83*` + `§11 → *C85*` + `§11 → *C87*` + `§11 → *C88*` + `§14.3` + `§14.4`; `decisions.md §DISPOSITION → §D.3 → rows H-016, H-017, H-018, H-019, H-020, H-021, H-022` | **ACTIVE** |
| **NB-4** | The macOS self-cost fallback uses an **OS-level** view of the daemon process plus the cross-platform catalogued series — not a proc-only thread series that is dark on macOS. | `plan-overview.md §10 → *AC2*`; `metric-catalog.md §2 → *F0.1*` | **ACTIVE** |
| **NB-5** | The tap's metric increments sit in a **dedicated inner** error boundary, so a metric error cannot produce per-turn warning noise. | `metric-catalog.md §0.2` + `§5 → *F20*` + `§11 → *C66*` | **ACTIVE** |
| **NB-6** | The kill-switch has **one** semantic in three cases (explicit set · explicit OFF · unset). "Fail closed" describes only an invalid *explicit* value. | `plan-overview.md §4 → *P0 task 3*` + `§5 → *kill-switch*`; `metric-catalog.md §0.8` + `§11 → *C74*` | **ACTIVE** |
| **NB-7** | The census-interval managed key carries an **upper bound**. | `plan-overview.md §5 → *managed keys*`; `metric-catalog.md §11 → *C67*` | **ACTIVE** |
| **NB-8** | The textfile family's process-name vocabulary matches what the sampler **actually** samples; the family and the ledger agree. | `metric-catalog.md §6 → *F22*` + `§8.9 → *F22 process names*` + `§14.4 → *F22 process names*` + `§11 → *C68*` | **ACTIVE** |
| **NB-9** | **Counts/bounds corrections** — the harvested notes about the ledger's bounds are closed by the corrections that fix the boundedness constant's family-set size, the fully-wired bound's provenance, the rule-less rounding that propagated as a hard family count, and the corrections-log row that was itself the stale claim. (Same provenance note as NB-1; each extra has its own row in `§DISPOSITION → §D.3`.) | `metric-catalog.md §11 → *C77*` + `§11 → *C79*` + `§11 → *C80*` + `§11 → *C86*` + `§14.1 → *Fully-wired bound*`; `decisions.md §DISPOSITION → §D.3 → rows H-001, H-023, H-024, H-025` | **ACTIVE** |
| **NB-10** | The liveness set states the **reachable** values plus the reserved residual (the unreachable one folds to the residual). | `metric-catalog.md §8.1` + `§14.4 → *instance liveness*` + `§11 → *C69*`; `plan-overview.md §4 → *P1b task 10*` | **ACTIVE** |
| **NB-11** | One kill-switch-off semantic: an **empty body** when off; the liveness metric is present only when the listener is live. | `metric-catalog.md §0.8` + `§2 → *F0.1*` + `§11 → *C70*` | **ACTIVE** |
| **NB-12** | The census annex's subsections are in ascending order. | `metric-catalog.md §9` + `§11 → *C71*` | **ACTIVE** |

## M. Final-consolidation round — the V-items (dead alerts, vocabularies, orphan sweep, baselines, external sampler, acceptance preconditions)

| ID | The decision now in force | Anchor (content-anchored) | Status |
|---|---|---|---|
| **V-1** | Every multi-vector rule carries an explicit label guard, and the acceptance criterion gates on **executable** evaluation fixtures (a fenced block in the markdown is documentation, **not** a promtool input). The fixture-**file** count and the **rule** count are **different quantities** — both are pointers into the catalog ledger. | `plan-overview.md §8 → *LoopStarved (MA-4) — fixed multi-vector (V-1)*` + `§8 → *CheckpointSlow (CR-3) — fixed multi-vector (V-1)*` + `§8 → *FalseDegraded (MA-5) — V-1 fixed multi-vector*` + `§10 → *AC8*` + `§10 → *promtool fixture layout*`; `metric-catalog.md §14.1 → *shipped alert rules*` + `§14.1 → *promtool fixture files*` + `§11 → *C37*`; `decisions.md §ACTIVE → §Q → row *fixture-count identity*` | **ACTIVE** |
| **V-2** | The false-degraded expression selects the two taxonomy members that actually exist; both are grace-eligible. | `plan-overview.md §8 → *FalseDegraded (MA-5)*` + `§8 → *V-2 — FalseDegraded taxonomy note*`; `metric-catalog.md §8.3 → *readiness outcome*` + `§4 → *F9*` + `§10.2` + `§11 → *C38*` | **ACTIVE** |
| **V-3** | The wake-outcome set is the **closable** set; each value is attributed to the producer that emits it; a null outcome yields no increment. | `metric-catalog.md §14.4 → *wake-turn outcome*` + `§8.13` + `§4 → *F13*` + `§11 → *C39*`; `plan-overview.md §4 → *P1b task 9*` | **ACTIVE** |
| **V-4** | The tool-category vocabulary is **frozen from the generated artifact** (never a hand list); the process-instability of any hand count is named as the defect; the reserved bucket is the closure if the freeze is deferred. | `plan-overview.md §4 → *P0 task 6*` + `§10 → *AC12*`; `metric-catalog.md §8.6 → *tool category*` + `§11 → *C40*` + `§11 → *C86*` | **ACTIVE** |
| **V-5** | The orphan sweep is **closed**: every catalogued series has a named phase task row and an owner, and the mapping lives in the catalog's task-map annex. | `metric-catalog.md §7.1 → *Task / owner mapping*` + `§11 → *C47*` + `§11 → *C48*`; `plan-overview.md §4 → *P0 task 11*` + `§4 → *P1c task 9*` + `§4 → *P0 — Foundation*` | **ACTIVE** |
| **V-6** | The checkpoint write path is instrumented through a **distinct write-side hook** — own function, own budget and labels, once per outer call, a null-on-write is an **error, not a skip** — and the **read charge seam is not reused**. The write path keeps **its own risk row**. | `plan-overview.md §4 → *P1b task 14*` + `§4 → *P1b risk row 3*` + `§1 → *Write-side restatement (A6/A7)*` + `§13.1.1`; `metric-catalog.md §4 → *F7*` + `§11 → *C49*`; `decisions.md §ACTIVE → §H → row J-4` + `§I → row Q1` | **ACTIVE** |
| **V-7** | Every shipped alert rule carries a **cited baseline** (a code constant or the incident/bench evidence). | `plan-overview.md §8 → *V-7 — Baseline column (per-rule citation)*` + `§8 → *alert table*`; `metric-catalog.md §14.1 → *Alert-rule baselines*` + `§11 → *C44*` | **ACTIVE** |
| **V-8** | The spinning-process signal comes from the **external sampler**, and the textfile-staleness rule covers the spin→stale interaction; the writer is the sampler, never the script. | `plan-overview.md §3 → *Textfile collector (R18, genuinely new)*` + `§4 → *P2 task 1*` + `§8 → *V-8 — BashSpin external sampler design (stand-alone)*` + `§8 → *BashSpin (MA-7)*` + `§8 → *TextfileStale (V-8)*`; `metric-catalog.md §6 → *F22*` + `§11 → *C43*` + `§11 → *C58*` | **ACTIVE** |
| **V-9** | Every acceptance criterion that depends on a dark-able surface carries an **explicit precondition row**. | `plan-overview.md §10 → *AC2*` + `§10 → *AC6*` + `§10 → *AC8*`; `metric-catalog.md §11 → *C45*` | **ACTIVE** |

## N. Final-consolidation round — the R-m items (review minors)

| ID | The decision now in force | Anchor (content-anchored) | Status |
|---|---|---|---|
| **R-m1** | The authoritative rule text lives in **fenced YAML blocks with unescaped pipes**; the table is retained for incident mapping and baselines. | `plan-overview.md §8 → *Authoritative rule text (fenced YAML — R-m1, unescaped pipes)*` + `§8 → *alert table*`; `metric-catalog.md §11 → *C36*` | **ACTIVE** |
| **R-m2** | The teardown uses the house **detached-join** precedent; the listener thread is a daemon thread, so a wedged join cannot hang shutdown. | `plan-overview.md §3 → *Boot/teardown lifecycle (A13)*` + `§4 → *P0 task 1*`; `metric-catalog.md §11 → *C50*` | **ACTIVE** |
| **R-m3** | The cardinality cap is **hard** and atomic under the metric's own lock (no check-then-act race), and rejected observations are counted. | `plan-overview.md §5 → *managed keys*` + `§7 → *never-exported list*`; `metric-catalog.md §0.5` + `§2 → *F0.2*` + `§11 → *C51*` | **ACTIVE** |
| **R-m4** | The F14 streaming-only gap is **documented** — accepted limitation, not fixed. | `metric-catalog.md §5 → *F14*` + `§11 → *C42*`; `decisions.md §ACTIVE → §F → row G-23` | **ACTIVE** |
| **R-m5** | The publisher cadence is **bounded** (dirty flag, at most one re-publish per interval, or publish-on-scrape with bounded staleness). | `metric-catalog.md §0.2` + `§11 → *C52*` | **ACTIVE** |
| **R-m6** | The two stale spots found by the sync check are closed: the row that carried a refuted fix locus now carries the **dead-producer** framing, and the row that carried a superseded count now carries a **pointer** into the catalog ledger. | `decisions.md §ACTIVE → §B → row MA-3` + `§ACTIVE → §Q → row *counts ledger*`; `metric-catalog.md §11 → *C35*` | **ACTIVE** |
| **R-m7** | The plan's closing sentence states the open questions' status accurately (some open, one resolved). | `plan-overview.md §12`; `metric-catalog.md §11 → *C53*` | **ACTIVE** |
| **R-m8** | The checkpoint family's decomposition is corrected (today / post byte-wiring) and the family estimate and the summary row agree. | `metric-catalog.md §14.3 → *F7*` + `§1` + `§7 → *Daemon total*` + `§11 → *C54*`; `decisions.md §ACTIVE → §Q → row *counts ledger*` | **ACTIVE** |
| **R-m9** | The wedge-capture reason vocabulary includes the manual reason, and the source path is corrected to the wedge-capture triggers module. | `metric-catalog.md §3 → *F3*` + `§8.3 → *wedge-capture reason*` + `§11 → *C41*` | **ACTIVE** |
| **R-m10** | The degradation matrix carries the wedge-capture row (counters fire; the content is proc-dependent). | `plan-overview.md §6 → *degradation contract*`; `metric-catalog.md §13.2` + `§11 → *C55*` | **ACTIVE** |

## O. Final-consolidation round — the mechanics items

| ID | The decision now in force | Anchor (content-anchored) | Status |
|---|---|---|---|
| **P-1** | The P0 vocabulary freeze is wired to the **generated artifact**, never to a hand list; the deferral with the reserved-bucket fold is the stated alternative. | `plan-overview.md §4 → *P0 task 6*` + `§10 → *AC12*`; `metric-catalog.md §8.6 → *tool category*` + `§11 → *C46*` + `§11 → *C56*` | **ACTIVE** |
| **P-3** | The three mechanics items are **applied** with no deviation: the detached-join teardown, the atomic hard cap with its rejected-observation counter, and the bounded publisher cadence. | `plan-overview.md §4 → *P0 exit criteria*` + `§3 → *Boot/teardown lifecycle (A13)*` + `§5 → *managed keys*`; `metric-catalog.md §0.2` + `§0.5` + `§11 → *C57*` | **ACTIVE** |

## P. Reversibility & back-out stance (B-12)

**Recorded in** `plan-overview.md §13 → *Reversibility & back-out*` and in every phase risk
table; summarised here for the reviewer's entry point.

**Classification rule (plan §13.2).** Anything added to the **daemon** is a **code change** →
revertible by `git revert`. Anything added to the **host** (port, plist, role, credential,
TSDB, sampler, the textfile artefact) is **removable by an explicit operational procedure** —
and **none** of them is covered by the kill-switch, so each gets its own row. The only
genuinely irreversible-by-design facts are (a) dropped observations under the cardinality cap,
(b) loss of collected history when a local TSDB is deleted, and (c) an externally visible
vocabulary/alias change in shipped PromQL — all three bounded, counted or stated, and all
three are why the plan ships a **reserved residual** and a **one-release alias window**
instead of hard renames.

### P.1 The named changes — undo / trigger / verification

| # | Change | Class | Undo (one line) | Trigger | Verification |
|---|---|---|---|---|---|
| **1** | The distinct write-side checkpoint hook (P1b task 14; the write-path risk row) | **Revert-only** | `git revert` the commit; the write path returns to uninstrumented; the `op` enum stays narrowed to the observable read value; no series is removed | the hook adds measurable latency under incident-like load, or the write-charge error ledger fires repeatedly | the reverted adapter makes no write-charge call; the scrape renders only the read-side `op` value |
| **2** | The vocabulary-freeze bump (P0 task 6; the P0 exit criteria) | **Revert-only for code / irreversible-by-design for shipped rules** | revert the freeze commit; re-freeze from the generated artifact with the reserved-bucket closure | the frozen set diverges from the generated artifact (the drift check fails), or a shipped rule's literal label value is not in the frozen set | the artifact drift check is green; the frozen label set equals the artifact's sorted list (or the reserved-bucket fold is recorded); the freeze marker matches the release notes |
| **3** | The hard cardinality cap that **rejects** observations (managed key + the never-exported list) | **Revert-only for the cap; dropped observations are irreversible-by-design** | raise the cap (bounds mirrored on the managed key, no restart) and/or revert the cap commit | the rejected-observation counter is non-zero in any scrape window, or the projected series count crosses the cap's warning fraction | the rejected-observation counter is absent/zero; the rendered daemon-origin series count is back under the cap |
| **4** | The listener port + the launchd plist (P0 task 1) — prod externals | **Removable** (revert-only for code; the plist is a host artefact to unload) | the emergency kill-switch (does **not** unbind the socket) or `launchctl unload <plist>` + remove the plist + move the port / revert the commits | bind failure on the metrics port; port collision; security review | the port is no longer listening (`lsof`); `launchctl list` has no metrics entry; the plist is absent; the main daemon port still serves |
| **5** | `postgres_exporter` against prod (P2 task 2) — role, credential, the statistics extension | **Removable** | stop + uninstall the unit; remove the config + credential; `DROP ROLE`; if the statistics extension was enabled by this plan, undo needs a **PG restart** | PG CPU/IO regression traced to the exporter's queries; the role is found with write grants; the credential is in a checked-in file | the activity view shows only exporter sessions; the role shows no write privileges; the `DROP ROLE` succeeds |
| **6** | The Prometheus TSDB on the prod macOS host (plan §2 retention; P2 tasks 2 and 4) | **Removable, but the collected history is irreversible-by-design** | `launchctl unload <prometheus plist>`; delete the TSDB dir; remove the generated config and the generated alert-rules file; remove the plist | the volume drops below the free-space floor; WAL replay fails at startup; the TSDB grows faster than the retention ladder; the operator moves to a remote Prometheus | the Prometheus unit is unloaded; the TSDB dir is empty; `df -h` shows the space reclaimed; the generated config and alert-rules file are gone; `promtool check config` has nothing left to parse |
| **7** | The out-of-process external sampler + the textfile artefacts (P1b task 13 minimal; P2 task 1 full) | **Removable** (the artefacts are files; the house scripts are never touched) | `launchctl unload <plist>`; delete the textfile artefact; remove the textfile dir from node_exporter's textfile-collector directory | the textfile-staleness rule fires; the sampler unit exits non-zero or thrashes; the OS sampling adds measurable host load | `launchctl list` has no textfile entry; the artefact is gone; node_exporter no longer lists the file; the staleness rule stops (the series is absent, not zero) |

**Capacity precondition (item 6).** The TSDB install on the prod macOS host is **blocked**
unless the free disk on the Prometheus data volume is at least **8.3 GB** and at least
**15 % free**. The derivation is stated inline in `plan-overview.md §13.1.6` (scrape interval ×
samples per series × bytes per sample × the headroom factor × the budget-ceiling series
count); the observed-count variant is stated alongside it. The prior figure was neither
reproducible nor self-consistent — the formula omitted the scrape-interval input
(`metric-catalog.md §11 → *C93*`).

### P.2 Revert-only vs irreversible-by-design (summary)

| Change | Class | What can be undone | What cannot |
|---|---|---|---|
| The write-side checkpoint hook | Revert-only | the commit; the hook; the write-side series (which never shipped) | nothing — the write path is unchanged by the revert |
| The vocabulary-freeze bump | Revert-only for code / irreversible-by-design for shipped rules | the bump commit; the frozen set (re-freeze from the artifact) | the fact that a deployed rule briefly stopped matching; the alias removal after its one-release window |
| The hard cardinality cap | Revert-only for the cap | raise or remove the cap | the **rejected observations** — dropped, never recorded (visible only via the rejected-observation counter) |
| The listener port + the launchd plist | Removable | unload + delete the plist; move the port; revert the commits | nothing (the daemon keeps serving); the kill-switch alone does **not** unbind the socket |
| The exporter + its role + its credential | Removable | stop the unit; delete the config + credential; `DROP ROLE` | nothing — unless the statistics extension was enabled by this plan (removal needs a PG restart) |
| The TSDB on prod | Removable, the history is irreversible | unload the unit; delete the TSDB dir; remove the generated config | **the collected history** (raw + downsampled) — bounded by the v2 remote-store non-goal |
| The external sampler + the textfile artefacts | Removable | unload the unit; delete the artefact; de-register the textfile dir | nothing — the house scripts were never touched |

### P.3 What the kill-switch does NOT cover (why the BACK-OUT column exists)

The kill-switch gates **daemon-origin exposition only**. It does **not**: unbind the listener
socket (the socket stays bound and returns an empty-shape 200); remove a launchd plist (daemon
/ listener unit, external sampler, node_exporter, postgres_exporter, Prometheus); remove the
textfile artefacts; stop Prometheus/TSDB/deployed alert rules; drop the PostgreSQL monitoring
role or disable the statistics extension. Each prod external has its own back-out (rows 4–7
above).

## Q. Reviewer verification ledger

### Q.1 Counts ledger — one source of truth

**Rule (catalog §14.6).** The ledger in `metric-catalog.md §14` is **referenced, not copied**.
This register holds **no** series count, headroom figure, family count, vocabulary size,
rule / fixture / baseline / risk-row count, or daemon-origin total — each is a **pointer** into
the ledger. **Rule thresholds and their baseline citations stay where they are** — they are
rule *parameters*, not counts.

| Quantity | Where the value lives (pointer) |
|---|---|
| Daemon-origin total, shell-origin count, exporter-origin count, grand total, headroom, family count, the fully-wired bound | `metric-catalog.md §14.1` |
| Per-phase totals | `metric-catalog.md §14.2` |
| Per-family series values (changed families flagged) | `metric-catalog.md §14.3` |
| Vocabulary sizes (one canonical value per set) | `metric-catalog.md §14.4` |
| Which document quotes which total | `metric-catalog.md §14.5` |
| The pointer discipline itself | `metric-catalog.md §14.6` |

**The delta trail.** The reconciliation trail that produces the certified daemon-origin total
is stated **once**, in `metric-catalog.md §7 → *Delta trail (the basis of …)*` and
`§11 → *C76*`, with an **explicit per-item baseline** for every item — and it sums on one line
in both places. This register holds no copy of it.

### Q.2 Cross-document identity

**Claim (as amended).** Among the **live planning documents** — `plan-overview.md`,
`metric-catalog.md` and this register — **no count appears in two documents with different
values**, and no count is restated where a pointer will do.

**Scope note (explicit, per the round-002 review).** The claim covers the **live planning
documents only**. `architecture-recommendation.md` is a **frozen historical input** by design
— it is the architect's review, committed separately and **unmodified** since; its figures are
**historical by design** and are not restated or reconciled by any later round. The same
applies to the retired rows inside this register's `§SUPERSEDED` section: they are kept for
audit and are **not** live guidance.

### Q.3 Rule → series → phase census

Every series referenced by a shipped rule has a **catalog §1 row** AND appears in the family
list of a **named phase task row** in the plan's phase tables. The census was re-run twice
independently (round-001 harvest and round-002 re-review) and the result is **no gaps**. The
one gap found in an earlier round — the loop-lag sample series on the loop-starved rule's
right-hand side — is closed: it is produced by the P1a lag task and listed in that phase's
family list. The census lives in `metric-catalog.md §7.1 → *Task / owner mapping*` and
`plan-overview.md §8 → *series-producibility rule*`.

### Q.4 Fixture-count identity (rule count vs fixture-file count)

The **rule count** and the **fixture-file count** are **different quantities** and are never
interchanged:

- the shipped-alert **rule** count → `metric-catalog.md §14.1 → *shipped alert rules*`;
- the standalone promtool **fixture-file** count → `metric-catalog.md §14.1 → *promtool fixture files*`.

The plan pins both by pointer (`plan-overview.md §10 → *AC8*` and
`§10 → *promtool fixture layout*`), and the CI wrapper asserts the fixture-file count against
the pointer and the rule set against the rule pointer. The acceptance criterion gates on the
**standalone files** passing under `promtool test rules` — a fenced block in the markdown is
documentation, **not** a promtool input.

### Q.5 Open items carried from the council pass

| # | Open item | Disposition | Anchor (content-anchored) | Status |
|---|---|---|---|---|
| **H-1** | The catalog header said "OpenMetrics text" | Replaced with the Prometheus-classic wording; matches the plan and the MA-16 resolution | `metric-catalog.md` header (compat line); `decisions.md §ACTIVE → §F → row G-14` | **CLOSED** |
| **H-2** | The exposition-liveness family contradicted its own no-labels claim (the summary row and the roll-up disagreed with the family entry) | **Shape picked: no labels** — the family entry, the summary row and the roll-up now agree | `metric-catalog.md §2 → *F0.1*` + `§1` + `§7`; `decisions.md §ACTIVE → §F → row G-15` | **CLOSED** |
| **H-3** | The plan's incident table named three metrics the catalog does not ship | Replaced with the catalog's equivalents; no checkout-latency family ships in v2 | `plan-overview.md §1 → *incident table*`; `decisions.md §ACTIVE → §F → row G-16` | **CLOSED** (carried as a §F limitation) |
| **H-4** | The plan's reuse row cited the lag-probe p95 at the wrong line | Corrected: the p95 line, the p50/max lines, the window constant and the "this is the snapshot helper" line are now distinguished | `plan-overview.md §9 → *R3*`; `metric-catalog.md §11 → *C5*` | **CLOSED** |
| **H-5** | The plan's reuse row cited the maintenance size query as a single-table blob query | Corrected: the query sums **three** relations and no single-table blob-size query exists; the metric is a split-out of that one round trip | `plan-overview.md §9 → *R7*`; `metric-catalog.md §11 → *C9*` | **CLOSED** |
| **H-6** | The plan's reuse row cited the readiness taxonomy at the wrong lines | Corrected to the taxonomy's real range plus its grace subset and components | `plan-overview.md §9 → *R8*`; `metric-catalog.md §11 → *C6*` | **CLOSED** |
| **H-7** | The plan's task tables and family lists referenced a retired histogram, a retired event-total vocabulary and uncatalogued names | The retired name is written as retired (without writing it); the family lists carry only catalogued names; the uncatalogued names stay dropped | `plan-overview.md §4 → *P1b family list*` + `§4 → *P1c family list*`; `metric-catalog.md §11 → *C12*` | **CLOSED** |
| **H-8** | The plan's P1a listed the textfile family that the catalog assigns to P2 | Removed from the P1a family list; the P1a task is the wedge-capture counter hook only | `plan-overview.md §4 → *P1a task 3*`; `metric-catalog.md §7 → *Phase split (F22)*` | **CLOSED** |
| **H-9** | The plan's never-exported list stated the wrong vocabulary sizes | Corrected to the catalogued sizes, each a pointer | `plan-overview.md §7 → *never-exported list*`; `metric-catalog.md §14.4` | **CLOSED** |
| **H-10** | The plan's reuse row conflated the MCP quarantine registry with the MCPS gateway store | Split into two correct references: **server state** from the per-boot quarantine registry (published read-only on `/readyz`) and **gateway session state** from the gateway store | `plan-overview.md §9 → *R14*`; `metric-catalog.md §5 → *F17*` + `§5 → *F18*` | **CLOSED** |
| **H-11** | The plan's open-question OQ-4 asked for a new event-kind vocabulary | **RESOLVED** — no new event-kind vocabulary is required; the catalog's two per-event families cover per-event counting, and the events-subsystem owner confirms those two vocabularies at the P1c review | `plan-overview.md §12 → *OQ-4*`; `metric-catalog.md §5 → *F20*` + `§4 → *F13*`; `decisions.md §ACTIVE → §F → row G-8` | **CLOSED** |
| **H-12** | The plan's phase headings and the catalog's ship phases used two different partitions | **RESOLVED** — one canonical partition, family → phase 1:1, zero exceptions; the historical divergence is enumerated in the catalog's corrections log rather than hidden | `metric-catalog.md §7 → *Intra-family phased rollouts (G-20/H-12 unification)*` + `§7.1`; `plan-overview.md §4 → *Coupling map*`; `decisions.md §ACTIVE → §F → row G-20` | **CLOSED** |

### Q.6 Companions

| File | Role | Editable in this round? |
|---|---|---|
| `plan-overview.md` | the phase plan (P0/P1a/P1b/P1c/P2), the alert table, the managed keys, the degradation contract, the reversibility section | **yes (wave C)** — wave A landed its corrections and its §14 pointers; wave C applied the M-4 stale-string sweep only — **no design change** |
| `metric-catalog.md` | the per-family spec, the vocabulary annexes, the census annex, the corrections log, the platform matrix and **§14 — the counted-values ledger** | **yes (wave C)** — the §14.1 bound cross-check row landed in the round-002 wave (see `§DISPOSITION → §D.5 → row H-120`); wave C refreshed the header tip pointer and applied the M-4 stale-string sweep only — **no design change** |
| `architecture-recommendation.md` | the architect's review — the **input** to the v3 round (its amendment list, its two open questions, its verification appendix) | **no** — a **frozen historical input**; read-only and unmodified since it was committed |
| `decisions.md` (this file) | the reviewer-facing register | **yes** — restructured into §ACTIVE / §SUPERSEDED / §DISPOSITION |

**No line counts are held here** — the sizes are read from the files themselves (Rule 2).

### Q.7 Commit record

All on `feature/metrics-collection` (worktree `agents-ensemble-wt-metrics-v2`).

| # | Round | Commit | Base | Content |
|---|---|---|---|---|
| 1 | v2 original plan | `cec78e0e881665450e1ca38c8bf555d915f4d54c` | `9bbef71c1f357d00f2a1dbd62e5c8057fb1620f1` | the three planning files as first authored + the pass-1 alignment edits |
| 2 | v2 alignment pass | `ad3a331716823998dee567cb6dfbad700b59c854` | `cec78e0e881665450e1ca38c8bf555d915f4d54c` | the H-1…H-11 closure + the refresh of this register's verification section |
| 3 | architect doc (WS-B step 2) | `c0f0694396d118e26fb7b40946005cc8bac8c4d4` | `ad3a331716823998dee567cb6dfbad700b59c854` | `architecture-recommendation.md` — the read-only input to the v3 round; a companion, **not** edited by any later round |
| 4 | v3 (architecture absorbed) | `51bbfc000e47654bc86df4a23e69e6ab774c7567` | `c0f0694396d118e26fb7b40946005cc8bac8c4d4` | amendments 1–28 across the plan + catalog + this register's v3 sections |
| 5 | correction round | `c7dbdea4b813190b1e13adefa637bf8b6092bae0` | `51bbfc000e47654bc86df4a23e69e6ab774c7567` | the macOS/WS-A platform correction + the leader-sanctioned partition unification + the register's refresh |
| 6 | final consolidation | `614ec222e19e08a81eec08482f2fe7600f3b9fca` | `c7dbdea4b813190b1e13adefa637bf8b6092bae0` | the V-items, the R-m items, the mechanics items + the register's refresh |
| 7 | **revision round v5 (approver iteration 001)** | `9b8437c9f1f2a8923acb6115096ed8ee08917fe0` | `614ec222e19e08a81eec08482f2fe7600f3b9fca` | the round-001 blocking + non-blocking closures + the reversibility section + the reconciled counts ledger + the cross-doc sweep |
| 8 | **this round (revision v6 — approver iteration 002 → 003)** | **the wave-C commit on this branch** (a commit cannot carry its own SHA — the commit *is* the record; read it with `git rev-parse HEAD` on this branch) | `9b8437c9f1f2a8923acb6115096ed8ee08917fe0` | the §ACTIVE / §SUPERSEDED / §DISPOSITION restructure + the round-002 residual-blocker fixes + the catalog §14.1 bound cross-check row + the M-4 stale-string sweep (wave C) |

**Pointer note (resolved in wave C).** `plan-overview.md`'s header self-declares the
round-7 tip — correct after wave A. `metric-catalog.md`'s header self-declared the
**round-4** tip, which was stale relative to this register's tip; **wave C refreshed it to the
round-7 tip (`9b8437c9f`)**, so both sibling headers now declare the same tip — the tip this
revision sits on. (The wave-C commit itself becomes the new tip; the next round refreshes both
pointers again.)

**Committed paths (rounds 1, 2, 4–7 — path-limited):** exactly `plan-overview.md`,
`metric-catalog.md`, `decisions.md` under `.agents/shared/planning/metrics-collection/`. Round 3
adds only `architecture-recommendation.md`. Nothing outside that directory is in any commit; the
pre-existing stashes were not touched; **nothing was pushed**.

**Verify:**

```bash
git rev-parse HEAD                 # the wave-C tip after wave C commits (the round-7 tip before it)
git show --stat --oneline HEAD     # exactly three planning files, nothing else
git log --oneline -8
```

### Q.8 Cross-file edit ledger (ids only — this register holds no edit *counts*)

- **v2 alignment pass** — edits `E1`…`E34` (name / label / expression alignment across the
  plan and the catalog, then the H-1…H-11 closure).
- **v3 edit pass** — edits `E35`…`E78` (the amendment-driven fixes, the drift fixes, and this
  register's own fixes).
- **Correction / consolidation / revision rounds** — the `§N`, `§O`, `§P` and `§Q` edits
  recorded in the corresponding catalog corrections-log rows.

### Q.9 Label-normalisation ledger (the architect's draft labels → the canonical numbering)

The two files mixed **three** label schemes: the architect's canonical amendment numbering,
the architect's own **draft** labels used inside its prose (a *different* numbering — e.g. its
draft "A15" is the listener topology, whose canonical number is **12**), and the council ids
(CR-1 / MA-1 / m1). Every inline draft label was **content-matched** to the canonical
numbering and relabelled — **relabel only, no content changes**. The mapping is edits
`E35`…`E54` (plan §1 / §3 / §4 label columns against the catalog's F-family label columns and
the file/location columns).

**Not an amendment label:** the plan's reuse row for the pool collector's own vocabulary name
is the collector's *own* vocabulary name, not an amendment reference — left untouched on
purpose (`plan-overview.md §9 → *R12*`).

---

# §SUPERSEDED — retained history (NOT current guidance)

> **WARNING — read this before any row in this section.**
> Every row below is **SUPERSEDED**. None of it is current guidance, none of it may be
> implemented from, and none of it may be quoted as a live decision. It is retained **only**
> so an auditor can see what was retired, when it was retired, and **which ACTIVE row
> replaced it**. Each row carries its own `SUPERSEDED` marker and a `superseded_by` pointer.

| ID | The retired content (SUPERSEDED — audit only) | Where it lived | `superseded_by` → ACTIVE row |
|---|---|---|---|
| **S-1** | **SUPERSEDED — the superseded write-seam design.** The retired rows described the write-side instrumentation as "**extend the existing `_charge_checkpoint_read` charge seam to `aput`/`aput_writes` … wiring, not a new instrument**". That design was **rejected**: routing write bytes through the read charge bills them to the READ budget. | The architect-critical-finding row for the write-path family and the leader-decision row for the write-path question | `decisions.md §ACTIVE → §H → row J-4` + `§ACTIVE → §I → row Q1` + `§ACTIVE → §M → row V-6` + `§ACTIVE → §K → row B-1` |
| **S-2** | **SUPERSEDED — the mis-labelled claim anchor.** The retired rows named `daemon/services/worker_pool.py:1381-1386` as "**the claim path**". That range is the `self._stats` **dict literal** — the source of the **outcome vocabulary**, not a claim. The retired rows also attributed the full outcome set to it. | The architecture-amendment row for the wake-outcome producer split; the architect-critical-finding row for the wake counter; the correction-round wake-producer verification; the consolidation-round wake-outcome row | `decisions.md §ACTIVE → §G → row 3` + `§ACTIVE → §H → row J-2` + `§ACTIVE → §K → row B-10` + `§ACTIVE → §N → row R-m1` |
| **S-3** | **SUPERSEDED — the phantom lifecycle vocabulary.** The retired row declared the shipped lifecycle set as a hand-listed **7-value** set including `fan_in_completed` and `fan_in_failed` (zero occurrences anywhere in `daemon/`) plus stale phase anchors, and carried the status "fixed" while still asserting it. | The council-round MINOR row for the lifecycle counters | `decisions.md §ACTIVE → §C → row m8` + `§ACTIVE → §K → row B-7` + `§ACTIVE → §M → row V-5` |
| **S-4** | **SUPERSEDED — the blocking-join teardown form.** The retired row recorded the listener teardown as `join(timeout=5)` and marked it "applied". The house precedent is a **detached join** on a daemon thread; a blocking join on the loop thread is forbidden by the plan. | The architecture-amendment row for the listener boot/teardown | `decisions.md §ACTIVE → §G → row 13` + `§ACTIVE → §N → row R-m2` + `§ACTIVE → §K → row B-11` |
| **S-5** | **SUPERSEDED — the tool-category hand-counts.** The retired rows asserted the tool-category vocabulary as a hand count — "37/38 claimed", later restated as "49 distinct across 254 sites", and in one case a per-family estimate move of "57 → 69". None of these was a code-derived count; the corrections-log row that carried one of them **was itself** the stale claim. | The waiver row for the tool-category label set; the architecture-amendment row for the tool-category vocabulary; the architect-critical-finding row for the tool-category enum; the consolidation-round tool-category row | `decisions.md §ACTIVE → §F → row G-17` + `§ACTIVE → §G → row 1` + `§ACTIVE → §H → row J-3` + `§ACTIVE → §M → row V-4` + `§ACTIVE → §L → row NB-9` |
| **S-6** | **SUPERSEDED — the pre-reconciliation totals.** The retired reconciliation carried the daemon-origin total and its headroom as **hardcoded values** in a history table, in a per-phase table, in a restatement paragraph, and in the name-consistency roll-up — including the earlier totals (the pre-v3 total, the correction-round total, the consolidation-round total, the prior headroom, the shell/exporter subtotals and the per-phase splits). Every one of those is a **count copy**; the certified values live once, in the catalog ledger. | The v3 reconciliation table and its basis note; the correction-round per-phase table; the consolidation-round restatement; the v2 name-consistency roll-up paragraph; the v2 verification-sweep row | `decisions.md §ACTIVE → §Q → §Q.1 counts ledger` + `metric-catalog.md §14` + `§7 → *Delta trail (the basis of …)*` |
| **S-7** | **SUPERSEDED — the duplicated partition-unification row.** The retired v3 additions table carried the partition-unification waiver **twice**, with **contradictory dispositions** on the two rows (one "resolved", one "deferred"). | The v3 waiver-additions table | `decisions.md §ACTIVE → §F → row G-20` + `§ACTIVE → §Q → §Q.5 → row H-12` |
| **S-8** | **SUPERSEDED — the duplicated waiver-register count.** The retired counts table carried the waiver register at **two different values** on two rows. | The v3 counts table | `decisions.md §ACTIVE → §F` (the register itself) + `§ACTIVE → §Q → §Q.1` |
| **S-9** | **SUPERSEDED — the commit-record placeholder and the duplicate commit row.** The retired commit table carried a literal **unfilled placeholder** in the *Base* cell of one row and **two** rows both titled "final consolidation", and it had **no row** for the round-001 revision commit. | The v3 commit table | `decisions.md §ACTIVE → §Q → §Q.7 commit record` |
| **S-10** | **SUPERSEDED — the companion line-count copies.** The retired header and the retired catalog-size waiver row quoted **line counts** for the companion files and for the catalog. Those counts are copies of a file property that changes on every edit; the size is read from the files themselves. | The register's preamble (companions line) and the waiver row for the catalog's length | `decisions.md §ACTIVE → §Q → §Q.6 companions` + `§ACTIVE → §F → row G-1` |
| **S-11** | **SUPERSEDED — the inverted reconciliation labels.** The retired note called the **older** total "the final reconciliation" and described the **current** one as "an earlier slip" — the labels were the wrong way round. | The v3 reconciliation basis note | `decisions.md §ACTIVE → §Q → §Q.1 counts ledger` + `metric-catalog.md §7 → *Delta trail (the basis of …)*` + `§11 → *C76*` |
| **S-12** | **SUPERSEDED — the absolute cross-document claim.** The retired sweep asserted, without scope, that no number appears in two documents with different values — a claim that also swept in the **frozen historical input** (the architect's review, whose figures are historical by design) and the register's own retired rows. | The revision-round cross-document consistency sweep | `decisions.md §ACTIVE → §Q → §Q.2 cross-document identity` |
| **S-13** | **SUPERSEDED — the closure standard that recorded instead of dispositioning.** The retired note stated that "folding a note into a correction-log entry counts as resolved" and that no harvested note remained open. A corrections-log entry is a **record**, not a **disposition**, and the blanket claim was unsupported. | The revision-round non-blocking harvest note | `decisions.md` preamble → *Rule 4 — the closure standard* + `decisions.md §DISPOSITION → §D.3` + `§DISPOSITION → §D.5` |
| **S-14** | **SUPERSEDED — the content-free non-blocking placeholders.** Three non-blocking rows carried no finding text and no fix — only "harvested from approver worker reports; folded into catalog §11". They recorded nothing that could be verified. | The revision-round non-blocking findings table (three rows) | `decisions.md §ACTIVE → §L → rows NB-1, NB-3, NB-9` |
| **S-15** | **SUPERSEDED — the retired fixture-count identity.** Three rows stated the **rule count** as the **fixture-file count** ("14 … evaluation fixtures"), while the plan distinguishes the standalone fixture-file count from the rule count. | The consolidation-round dead-alerts row; the consolidation-round final-verification list; the council-round verification row | `decisions.md §ACTIVE → §M → row V-1` + `§ACTIVE → §Q → §Q.4 fixture-count identity` + `metric-catalog.md §14.1 → *shipped alert rules*` + `§14.1 → *promtool fixture files*` |
| **S-16** | **SUPERSEDED — the blanket harvest closure claim.** The retired note asserted that every harvested note was resolved, without a countable ledger and without a per-extra row. | The revision-round non-blocking harvest note | `decisions.md §DISPOSITION → §D.3 → tally` + `§DISPOSITION → §D.5 → tally` |
| **S-17** | **SUPERSEDED — the retired wake-outcome figure set.** The retired rows quoted the wake-outcome set at its pre-cut size and the family estimate at its pre-cut size, and one of them restated the family estimate at two different values. | The revision-round blocking-finding rows for the wake vocabulary | `decisions.md §ACTIVE → §K → rows B-1, B-6` + `§ACTIVE → §M → row V-3` + `metric-catalog.md §14.4 → *wake-turn outcome*` |
| **S-18** | **SUPERSEDED — the retired orphan-census figure set.** The retired rows quoted the pre-sweep orphan count and the per-family breakdown of it as a live number. | The consolidation-round orphan-sweep row and the final-verification list | `decisions.md §ACTIVE → §M → row V-5` + `metric-catalog.md §7.1 → *Task / owner mapping*` |
| **S-19** | **SUPERSEDED — the retired per-family decomposition figures.** The retired rows quoted the checkpoint family's decomposition, and the job-queue / request / journal family estimates, at their pre-reconciliation values — the source of the "≤2k proof" self-contradiction. | The revision-round blocking-finding row for the budget proof; the consolidation-round per-family reconciliation rows | `decisions.md §ACTIVE → §K → row B-13` + `§ACTIVE → §N → row R-m8` + `metric-catalog.md §14.3` |

---

# §DISPOSITION — the per-approver-round finding → closure map

**What this section is.** The reviewer's audit trail: every finding raised by every approver
round, and what happened to it. It is **not** a decision section — the decisions live in
`§ACTIVE`; this section points at them.

**Closure standard (Rule 4, restated).** A finding is dispositioned **only** as:

- **FIXED** — with a content-anchored pointer to the artefact that now carries the fix;
- **NOTE** — with a rationale for accepting the state as-is;
- **WAIVED** — with a rationale for deliberately not changing it.

**A corrections-log entry is NOT a disposition.** Landing a note in a catalog corrections row
records the observation; it does not close the finding, and it is **not** counted as closed.
Every harvested finding therefore has its **own row** below.

**Anchor rule.** Every cell below is **section + row-id / heading**. No bare line numbers
appear in this section.

**Double-counting rule.** An extra is dispositioned **once**, in the round that first raised
it. A later round's re-raise of the same extra is listed in that round's ledger as a
**re-raise** (cross-referenced to the earlier row) and is **not** counted again in the tally.

---

## D.1 Round 001 — the blocking findings (13/13 dispositioned)

Source: the approver tracking file's iteration-001 section (the three section workers'
reports, deduped to 13 blocking findings).

| ID | Finding (as the round raised it) | Disposition | Closure (content-anchored) |
|---|---|---|---|
| **B-1** | The wake-outcome vocabulary was contradictory across the catalog (the family detail block declared one size, the summary and the annex another), so the family estimate did not reconcile. | **FIXED** | `metric-catalog.md §14.4 → *wake-turn outcome*` + `§4 → *F13*` + `§8.13` + `§14.3 → *F13*` + `§11 → *C39*` + `§11 → *C61*`; the decision itself: `decisions.md §ACTIVE → §K → row B-1` |
| **B-2** | Shipped alert rules referenced series that no phase and no catalog row assigns to produce (three sub-cases: the loop-lag sample series, the uncatalogued textfile freshness gauge, and the false-degraded expression selecting a taxonomy member that does not exist). | **FIXED** | `plan-overview.md §8 → *LoopStarved*` + `§8 → *TextfileStale*` + `§8 → *FalseDegraded*` + `§4 → *P1a task 1*`; `metric-catalog.md §3 → *F1*` + `§6 → *F22*` + `§10.2` + `§11 → *C59*` + `§11 → *C60*`; the decisions: `decisions.md §ACTIVE → §K → row B-2` + `§M → rows V-1, V-2` |
| **B-3** | The P2 textfile task's title and exit criterion contradicted the ratified decision that the house shell scripts are observed and never modified. | **FIXED** | `plan-overview.md §4 → *P2 task 1*` + `§8 → *V-8 — BashSpin external sampler design (stand-alone)*`; `metric-catalog.md §11 → *C43*` + `§11 → *C58*`; the decisions: `decisions.md §ACTIVE → §K → row B-3` + `§D → row 2` |
| **B-4** | The sweep cycle metric's declared type contradicted the rule that referenced it (the cycle metric was called a gauge while the rule used a rate over a counter). | **FIXED** | `plan-overview.md §4 → *P1b task 11*` + `§8 → *SweepHotLoop*`; `metric-catalog.md §4 → *F12*`; the decisions: `decisions.md §ACTIVE → §K → row B-4` |
| **B-5** | The P0 vocabulary freeze pinned only the reconciliation sweep lanes, dropping the standalone sweep services' series. | **FIXED** | `plan-overview.md §4 → *P0 task 6*`; `metric-catalog.md §8.4 → *sweep lane*` + `§14.4 → *sweep lane*` + `§11 → *C14*`; the decisions: `decisions.md §ACTIVE → §K → row B-5` |
| **B-6** | The wake-outcome vocabulary was three-way contradictory and the family estimate diverged with it. | **FIXED** | `metric-catalog.md §14.4 → *wake-turn outcome*` + `§4 → *F13*` + `§8.13` + `§11 → *C39*` + `§11 → *C61*`; the decisions: `decisions.md §ACTIVE → §K → row B-6` |
| **B-7** | The lifecycle-event vocabulary was a hand list, not code-derived: two of its values occur nowhere in the daemon, it had no residual bucket and no amendment procedure. | **FIXED** | `metric-catalog.md §8.1 → *instance lifecycle event*` + `§14.4 → *instance lifecycle event*` + `§4 → *F13*` + `§11 → *C62*`; `plan-overview.md §4 → *P1b task 12*`; the decisions: `decisions.md §ACTIVE → §K → row B-7` + `§C → row m8` |
| **B-8** | The job-queue family's source anchor pointed at the wrong table, and the family mixed job-queue depth with task-queue claim latency. | **FIXED** | `metric-catalog.md §4 → *F10*` + `§4 → *F11*` + `§11 → *C63*` + `§11 → *C90*` + `§14.3 → *F10*` + `§14.3 → *F11*`; the decisions: `decisions.md §ACTIVE → §K → row B-8` |
| **B-9** | The invalid-label counter appeared only in prose: no summary row, no family entry, no type/source/mechanism, absent from the budget proof, and its label dimension was an arbitrary string. | **FIXED** | `metric-catalog.md §2 → *F0.2*` + `§0.5` + `§14.3 → *F0.2*` + `§11 → *C64*` + `§11 → *C77*`; the decisions: `decisions.md §ACTIVE → §K → row B-9` |
| **B-10** | The wake counters' producer anchor named the worker-pool statistics literal as the producer — the wrong producer, so the LostWake rule and the waiting-children replay had no incrementing producer. The retired framing is retained at `decisions.md §SUPERSEDED → row S-2`. | **FIXED** | `metric-catalog.md §11 → *C73*` + `§11 → *C92*` + `§14.4 → *wake-turn outcome*`; `plan-overview.md §4 → *P1b task 9*`; the decisions: `decisions.md §ACTIVE → §K → row B-10` + `§G → row 3` + `§H → row J-2`; the retired framing is retained at `decisions.md §SUPERSEDED → row S-2` |
| **B-11** | The register's anchor column mis-anchored the wake producer/consumer contract, and it recorded the retired teardown form as applied. | **FIXED** | `decisions.md` preamble → *Rule 1*; `decisions.md §ACTIVE → §G → row 13` + `§H → row J-2`; `metric-catalog.md §11 → *C50*`; the decisions: `decisions.md §ACTIVE → §K → row B-11`; the retired teardown form is retained at `decisions.md §SUPERSEDED → row S-4` |
| **B-12** | No reversibility / back-out stance existed for the risky decisions or the prod-affecting steps; the write-path charge touches the path under stress during the very incidents the plan exists to diagnose. | **FIXED** | `plan-overview.md §13 → *Reversibility & back-out*` + `§13.0` + `§13.1.1` … `§13.1.7` + `§13.2` + `§4 → *risk tables*`; the decisions: `decisions.md §ACTIVE → §P` + `§K → row B-12` |
| **B-13** | The budget proof contradicted itself: two different totals, three per-family mismatches, and the orphan census was stated on one of them. | **FIXED** | `metric-catalog.md §14` + `§14.1` + `§14.2` + `§14.3` + `§14.6` + `§7 → *Delta trail (the basis of …)*` + `§11 → *C65*` + `§11 → *C76*`; the decisions: `decisions.md §ACTIVE → §K → row B-13` + `§Q → §Q.1` |

**Round-001 blocking tally:** harvested **13** · dispositioned **13** · open **0**.

---

## D.2 Round 001 — the non-blocking findings (12/12 dispositioned)

| ID | Finding (as the round raised it) | Disposition | Closure (content-anchored) |
|---|---|---|---|
| **NB-1** | The harvested source-anchor notes (the wrong-table anchor, the claim-latency source chain, the read-charge citation). | **FIXED** | `metric-catalog.md §11 → *C63*` + `§11 → *C90*` + `§11 → *C94*` + `§4 → *F11*`; the decisions: `decisions.md §ACTIVE → §L → row NB-1`; the per-extra rows: `§DISPOSITION → §D.3 → rows H-027, H-028` |
| **NB-2** | Stale cross-document line references inside the register. | **FIXED** | `decisions.md` preamble → *Rule 1*; the decisions: `decisions.md §ACTIVE → §L → row NB-2` |
| **NB-3** | The harvested vocabulary-annex notes (the mis-filed family, the placeholder correction id, the unresolved marker, the missing anchors). | **FIXED** | `metric-catalog.md §11 → *C83*` + `§11 → *C85*` + `§11 → *C87*` + `§11 → *C88*`; the decisions: `decisions.md §ACTIVE → §L → row NB-3`; the per-extra rows: `§DISPOSITION → §D.3 → rows H-016, H-017, H-018, H-019, H-020, H-021, H-022` |
| **NB-4** | The macOS self-cost fallback cited a proc-only thread series that is dark on macOS. | **FIXED** | `plan-overview.md §10 → *AC2*`; `metric-catalog.md §2 → *F0.1*`; the decisions: `decisions.md §ACTIVE → §L → row NB-4` |
| **NB-5** | The tap's metric increments were placed where a metric error would produce per-turn warning noise. | **FIXED** | `metric-catalog.md §0.2` + `§5 → *F20*` + `§11 → *C66*`; the decisions: `decisions.md §ACTIVE → §L → row NB-5` |
| **NB-6** | The kill-switch semantics read as if an unset variable means OFF, contradicting the same paragraph's deferral to the managed-key chain. | **FIXED** | `plan-overview.md §4 → *P0 task 3*` + `§5 → *kill-switch*`; `metric-catalog.md §0.8` + `§11 → *C74*`; the decisions: `decisions.md §ACTIVE → §L → row NB-6` |
| **NB-7** | The census-interval managed key had no upper bound. | **FIXED** | `plan-overview.md §5 → *managed keys*`; `metric-catalog.md §11 → *C67*`; the decisions: `decisions.md §ACTIVE → §L → row NB-7` |
| **NB-8** | The textfile family declared more process names than the sampler actually samples. | **FIXED** | `metric-catalog.md §6 → *F22*` + `§8.9 → *F22 process names*` + `§14.4 → *F22 process names*` + `§11 → *C68*`; the decisions: `decisions.md §ACTIVE → §L → row NB-8` |
| **NB-9** | The harvested counts/bounds notes (the boundedness constant, the fully-wired bound's provenance, the rule-less rounding, the stale corrections row). | **FIXED** | `metric-catalog.md §11 → *C77*` + `§11 → *C79*` + `§11 → *C80*` + `§11 → *C86*` + `§14.1 → *bound cross-check row*`; the decisions: `decisions.md §ACTIVE → §L → row NB-9`; the per-extra rows: `§DISPOSITION → §D.3 → rows H-001, H-023, H-024, H-025` |
| **NB-10** | The liveness set stated more reachable values than actually exist. | **FIXED** | `metric-catalog.md §8.1` + `§14.4 → *instance liveness*` + `§11 → *C69*`; the decisions: `decisions.md §ACTIVE → §L → row NB-10` |
| **NB-11** | The kill-switch-off empty body contradicted the liveness metric being present. | **FIXED** | `metric-catalog.md §0.8` + `§2 → *F0.1*` + `§11 → *C70*`; the decisions: `decisions.md §ACTIVE → §L → row NB-11` |
| **NB-12** | The census annex's subsections were out of order. | **FIXED** | `metric-catalog.md §9` + `§11 → *C71*`; the decisions: `decisions.md §ACTIVE → §L → row NB-12` |

**Round-001 non-blocking tally:** harvested **12** · dispositioned **12** · open **0**.

---

## D.3 Round 001 — the harvested extras ledger

**Provenance.** The approver tracking file records the round-001 workers' note **counts** (ten
from the decisions worker, fifteen-plus from the catalog worker) and the round-002 workers'
**re-harvests** of them by artefact site. The tracking file does not carry the note texts, so
this ledger enumerates the harvest at the granularity the tracking file supports: **each
distinct artefact defect** is a row, with its own disposition and its own content-anchored
closure pointer. No extra is folded into another extra, and no extra is closed by "it is in
the corrections log".

| ID | The harvested extra (as re-harvested and verified) | Disposition | Closure (content-anchored) |
|---|---|---|---|
| **H-001** | The corrections-log row for the tool-category vocabulary **was itself the stale claim** — it asserted a hand count the tree contradicts. | **FIXED** | `metric-catalog.md §11 → *C86*` + `§8.6 → *tool category*` + `§14.4 → *tool category*` |
| **H-002** | The waiver row for the tool-category label set repeated the same retired hand count. | **FIXED** | `decisions.md §ACTIVE → §F → row G-17` (the retired form: `decisions.md §SUPERSEDED → row S-5`) |
| **H-003** | The architecture-amendment row for the tool-category vocabulary repeated the retired hand count and the pre-correction family estimate. | **FIXED** | `decisions.md §ACTIVE → §G → row 1` (retired form: `decisions.md §SUPERSEDED → row S-5`) |
| **H-004** | The architect-critical-finding row for the tool-category enum repeated the retired hand count. | **FIXED** | `decisions.md §ACTIVE → §H → row J-3` (retired form: `decisions.md §SUPERSEDED → row S-5`) |
| **H-005** | The consolidation-round tool-category row repeated the retired hand count. | **FIXED** | `decisions.md §ACTIVE → §M → row V-4` (retired form: `decisions.md §SUPERSEDED → row S-5`) |
| **H-006** | Two register rows carried a second, older tool-category figure. | **FIXED** | `decisions.md §ACTIVE → §F → row G-17` + `§ACTIVE → §G → row 1` (retired form: `decisions.md §SUPERSEDED → row S-5`) |
| **H-007** | The register's preamble quoted companion **line counts**. | **FIXED** | `decisions.md §ACTIVE → §Q → §Q.6 companions` (retired form: `decisions.md §SUPERSEDED → row S-10`) |
| **H-008** | The catalog-size waiver row quoted a stale catalog **line count**. | **FIXED** | `decisions.md §ACTIVE → §F → row G-1` (retired form: `decisions.md §SUPERSEDED → row S-10`) |
| **H-009** | The v3 waiver-additions table carried the partition-unification waiver **twice with contradictory dispositions**. | **FIXED** | `decisions.md §ACTIVE → §F → row G-20` (retired form: `decisions.md §SUPERSEDED → row S-7`) |
| **H-010** | The v3 counts table carried the waiver register at **two different values**. | **FIXED** | `decisions.md §ACTIVE → §F` + `§ACTIVE → §Q → §Q.1` (retired form: `decisions.md §SUPERSEDED → row S-8`) |
| **H-011** | The v2 name-consistency roll-up paragraph quoted a pre-reconciliation total. | **FIXED** | `decisions.md §ACTIVE → §Q → §Q.1` (retired form: `decisions.md §SUPERSEDED → row S-6`) |
| **H-012** | The v3 reconciliation history columns quoted several superseded totals and the prior headroom. | **FIXED** | `decisions.md §ACTIVE → §Q → §Q.1` + `metric-catalog.md §7 → *Delta trail (the basis of …)*` (retired form: `decisions.md §SUPERSEDED → row S-6`) |
| **H-013** | The correction-round per-phase table quoted the superseded per-phase split and headroom. | **FIXED** | `decisions.md §ACTIVE → §Q → §Q.1` + `metric-catalog.md §14.2` (retired form: `decisions.md §SUPERSEDED → row S-6`) |
| **H-014** | The consolidation-round restatement quoted the superseded total and its headroom. | **FIXED** | `decisions.md §ACTIVE → §Q → §Q.1` (retired form: `decisions.md §SUPERSEDED → row S-6`) |
| **H-015** | The council-round verification row quoted a superseded series total **and** stated the rule count as the fixture-file count. | **FIXED** | `decisions.md §ACTIVE → §Q → §Q.1` + `§ACTIVE → §Q → §Q.4` (retired forms: `decisions.md §SUPERSEDED → rows S-6, S-15`) |
| **H-016** | Five vocabulary-annex label sets carried **no `file:line` anchor**, violating the annex's own rule that every set is anchored. | **FIXED** | `metric-catalog.md §11 → *C88*` + `§8.10` + `§0.5` |
| **H-017** | A corrections-log row carried an **unresolved editorial marker** naming the wrong annex subsection. | **FIXED** | `metric-catalog.md §11 → *C83*` + `§11 → *C68*` |
| **H-018** | The ledger's vocabulary row named a **placeholder** correction id instead of a real one. | **FIXED** | `metric-catalog.md §11 → *C85*` + `§14.4` |
| **H-019** | The ledger's vocabulary row **filed a metric under the wrong family**. | **FIXED** | `metric-catalog.md §11 → *C87*` + `§14.4` |
| **H-020** | The ledger's per-family table **did not flag a changed family**, although its heading promises it. | **FIXED** | `metric-catalog.md §11 → *C84*` + `§14.3` |
| **H-021** | The carrier class anchor was cited **without its decorator line**, so the class and the carrier field could not be told apart. | **FIXED** | `metric-catalog.md §11 → *C82*` + `§8.13` |
| **H-022** | The **histogram counting convention** differed between families (one counted its bucket series once, another stated a fully-wired form), making a family estimate ambiguous. | **FIXED** | `metric-catalog.md §11 → *C81*` + `§8.11` + `§5 → *F21*` + `§4 → *F7*` |
| **H-023** | A **rule-less rounding figure** propagated into a hard family count. | **FIXED** | `metric-catalog.md §11 → *C80*` + `§4 → *F12*` |
| **H-024** | The **fully-wired bound** was carried forward unchanged after five families moved, with no provenance and no derivation. | **FIXED** | `metric-catalog.md §11 → *C79*` + `§14.1 → *bound cross-check row*` + `§7` |
| **H-025** | The **boundedness constant** named a smaller family set than the catalog carries. | **FIXED** | `metric-catalog.md §11 → *C77*` + `§2 → *F0.2*` + `§11 → *C64*` |
| **H-026** | A family entry's **parenthetical disagreed with the summary row** it summarises. | **FIXED** | `metric-catalog.md §11 → *C78*` + `§5 → *F21*` + `§1` |
| **H-027** | The **task map was not moved** with the family split, and the receiving family lost its own source anchor. | **FIXED** | `metric-catalog.md §11 → *C90*` + `§7.1 → *Task / owner mapping*` + `§4 → *F11*` |
| **H-028** | The **read-charge citation** covered only one of the two charge sites, so the other was invisible to a reader checking the write-path plan. | **FIXED** | `metric-catalog.md §11 → *C94*` + `§4 → *F7*`; `plan-overview.md §1 → *Write-side restatement (A6/A7)*` + `§4 → *P1b task 14*` + `§4 → *P1b risk row 3*` + `§13.1.1` |
| **H-029** | The corrections-log spot-checks the plan worker re-harvested: **C53** (the open-questions closing sentence), **C47/V-5** (the orphan sweep and the freeze producer), **C9** (the maintenance size query), **C15** (the queue-item duration claim), **C5** (the lag-probe quantile lines), **C8** (the token-usage caveat lines) — all verified against the tree and genuinely dispositioned. | **NOTE** — each verified against the tree; the closure is the corrections-log row itself **plus** the substantive decision it records. | `metric-catalog.md §11 → *C53*` + `§11 → *C47*` + `§11 → *C9*` + `§11 → *C15*` + `§11 → *C5*` + `§11 → *C8*`; the substantive decisions: `decisions.md §ACTIVE → §F → row G-8` + `§M → row V-5` + `§E → rows R7, R3` + `§F → row G-11` + `§F → row G-10` |
| **H-030** | The **companion line counts** in the v3 counts table were stale copies of a file property. | **FIXED** | `decisions.md §ACTIVE → §Q → §Q.6 companions` (retired form: `decisions.md §SUPERSEDED → row S-10`) |

**Round-001 harvest tally:** harvested **30** · dispositioned **30** (29 FIXED + 1 NOTE) ·
open **0**.

---

## D.4 Round 002 — the residual blockers (9/9 dispositioned)

Source: the approver tracking file's iteration-002 section — the three re-review workers'
reports, deduped to nine residual blockers. **This is the final permitted approver round
(003)**, so each row below names the **mechanism** that was fixed, not just the site.

| ID | Finding (as round 002 raised it) | Disposition | Closure (content-anchored) |
|---|---|---|---|
| **R-1** | **[B-10] The wrong producer anchor survived in the reviewer-facing register.** Four live rows still named the worker-pool statistics literal as the producer — the retired framing, retained at `decisions.md §SUPERSEDED → row S-2` — contradicting the sections the previous revision added to fix it. **Mechanism fixed:** the register now has a single **anchor vocabulary**, and every producer anchor in it is the corrected one (the claim **call** `daemon/services/worker_pool.py:302`; the episode seam `daemon/graph.py:7319`; the canonical carrier producer `daemon/services/task_processor.py:569-598` with the carrier field `daemon/services/message_processing_pipeline.py:176` and the ledger identity `daemon/services/instance_messaging.py:2611-2613`); the retired framing exists only in the superseded section. | **FIXED** | `decisions.md §ACTIVE → §G → row 3` + `§ACTIVE → §H → row J-2` + `§ACTIVE → §K → row B-10` + `§ACTIVE → §N → row R-m1`; `metric-catalog.md §11 → *C73*` + `§11 → *C92*` + `§14.4 → *wake-turn outcome*`; `plan-overview.md §4 → *P1b task 9*`; the retired framing: `decisions.md §SUPERSEDED → row S-2` |
| **R-2** | **[B-1] The rejected write-seam design survived in two register rows.** Two rows still described the write-side instrumentation using the **V-6-rejected** design (the retired form at `§SUPERSEDED → row S-1`), contradicting the rows that carry the ratified decision. **Mechanism fixed:** the register now has **one** write-side row per concern (the architect-critical row and the leader-decision row), and both state the **distinct write-side hook**; the retired design is retained only in the superseded section. | **FIXED** | `decisions.md §ACTIVE → §H → row J-4` + `§ACTIVE → §I → row Q1` + `§ACTIVE → §M → row V-6` + `§ACTIVE → §K → row B-1` + `§ACTIVE → §G → row 6`; `metric-catalog.md §4 → *F7*` + `§11 → *C49*`; `plan-overview.md §4 → *P1b task 14*` + `§4 → *P1b risk row 3*`; the retired design: `decisions.md §SUPERSEDED → row S-1` |
| **R-3** | **[B-7] The phantom lifecycle vocabulary survived in the reviewer-facing register.** A row still declared the shipped lifecycle set as the hand-listed set — including the two values that occur nowhere in the daemon — and cited stale phase anchors, while its status column already said "fixed". **Mechanism fixed:** the register no longer carries a hand-listed lifecycle set at all; the lifecycle row points at the **code-derived** set, the reserved residual and the amendment procedure in the catalog. | **FIXED** | `decisions.md §ACTIVE → §C → row m8` + `§ACTIVE → §K → row B-7`; `metric-catalog.md §8.1 → *instance lifecycle event*` + `§14.4 → *instance lifecycle event*` + `§4 → *F13*` + `§11 → *C62*`; `plan-overview.md §4 → *P1b task 12*`; the retired set: `decisions.md §SUPERSEDED → row S-3` |
| **R-4** | **[B-13a] The catalog's own delta trail did not sum to the total it certified.** Three items in the trail were mis-stated (one double-counted, one netted to the wrong figure, one mixing a detail baseline with a summary baseline) and a fourth was already inside the baseline. **Mechanism fixed (catalog side):** the trail is now a table with an **explicit per-item baseline** per item and sums on one line in **both** places where it appears. | **FIXED** | `metric-catalog.md §7 → *Delta trail (the basis of …)*` + `§1 → *Reconciliation basis*` + `§11 → *C76*`; `decisions.md §ACTIVE → §Q → §Q.1 counts ledger` (the register holds **no** copy of the trail — it points at it) |
| **R-5** | **[B-13b] Cross-document number identity failed in nine live register sites.** The register carried hardcoded copies of superseded totals, headroom figures and per-phase splits, while a sweep row asserted that no number appears in two documents with different values — an assertion that also swept in the **frozen historical input**. **Mechanism fixed (three parts):** (a) the register now holds **no** count value at all — every count is a pointer into the catalog ledger (Rule 2); (b) the cross-document claim is now **scoped** to the live planning documents, and explicitly excludes the frozen historical input and this register's own superseded rows; (c) the inverted reconciliation labels (the retired total labelled "final", the current one labelled a slip) are corrected — the register points at the ledger, which carries the correctly-labelled trail. | **FIXED** | `decisions.md §ACTIVE → §Q → §Q.1 counts ledger` + `§ACTIVE → §Q → §Q.2 cross-document identity` + `decisions.md` preamble → *Rule 2*; `metric-catalog.md §14.5` + `§14.6` + `§11 → *C91*`; the retired copies: `decisions.md §SUPERSEDED → rows S-6, S-11, S-12` |
| **R-6** | **[§Q] The disposition map's anchor and source-of-truth columns were systematically stale** — cells resolving to blank lines, to headings, or to unrelated rows; and the source-of-truth column conflated the plan's section numbering with the catalog's. **Mechanism fixed:** the disposition map was **regenerated** against the current tree; every cell is now **section + row-id**; and the source-of-truth column is gone entirely — a disposition points at the artefact that carries the fix, and the counts live in one ledger (Rule 1). | **FIXED** | `decisions.md §DISPOSITION → §D.1` … `§D.5` (every cell re-derived against the current tree); `decisions.md` preamble → *Rule 1 — anchors are content-anchored*; `metric-catalog.md §14.5 → *Places each total appears*` |
| **R-7** | **[§L/§Q.5] The commit record was not updated and carried an unfilled placeholder.** The table had no row for the revision commit, a literal placeholder in the base cell, and two rows with the same title; the sibling documents' self-declared tips were stale. **Mechanism fixed:** the commit record now has **one row per round**, no placeholder, no duplicate, and the correct bases; the sibling tip pointers are verified; the one remaining discrepancy (the catalog header's stale self-declared tip) was **recorded in the register** and **resolved by wave C**, which refreshed the catalog header to the same tip the plan declares. | **FIXED** | `decisions.md §ACTIVE → §Q → §Q.7 commit record` (row for every round; the placeholder and the duplicate are gone — retired form: `decisions.md §SUPERSEDED → row S-9`); `plan-overview.md` header (self-declared tip, corrected by wave A); the catalog header's stale self-declared tip was **recorded** at `decisions.md §ACTIVE → §Q → §Q.7 → pointer note` and **refreshed by wave C** |
| **R-8** | **[Harvest] The round-001 non-blocking extras were not dispositioned.** The harvest was spot-checked and the spot-checks failed: the stale tool-category count in the corrections log (whose own correction row was the stale claim), the stale companion line counts, the duplicate partition-unification rows, the counts ledger's two values for the register, and the stale roll-up. The closure standard also counted a corrections-log entry as a disposition. **Mechanism fixed (four parts):** (a) the harvest is now a **countable ledger with a tally**; (b) **every** harvested extra has **its own row** with a disposition and a pointer; (c) a corrections-log entry is explicitly **not** a disposition (Rule 4); (d) the closure standard is stated once, in the preamble. | **FIXED** | `decisions.md §DISPOSITION → §D.3` (30 rows + tally) + `decisions.md` preamble → *Rule 4 — the closure standard*; the decisions: `decisions.md §ACTIVE → §F → rows G-1, G-17, G-20` + `§ACTIVE → §Q → §Q.1` + `§Q → §Q.6`; the retired forms: `decisions.md §SUPERSEDED → rows S-5, S-7, S-8, S-10, S-13, S-16` |
| **R-9** | **[Fixture count] The rule count and the fixture-file count were used interchangeably.** Three register rows stated the **rule** count as the **fixture-file** count, while the plan distinguishes them and the sweep row asserted the fixture count. **Mechanism fixed:** the register now states **that they are different quantities**, and points at the catalog ledger for **both** — the rule count at one row, the fixture-file count at another. Neither count is quoted in the register. | **FIXED** | `decisions.md §ACTIVE → §Q → §Q.4 fixture-count identity` + `§ACTIVE → §M → row V-1` + `§ACTIVE → §K → row B-1`; `metric-catalog.md §14.1 → *shipped alert rules*` + `§14.1 → *promtool fixture files*`; `plan-overview.md §10 → *AC8*` + `§10 → *promtool fixture layout*`; the retired form: `decisions.md §SUPERSEDED → row S-15` |

**Round-002 residual-blocker tally:** harvested **9** · dispositioned **9** · open **0**.

---

## D.5 Round 002 — the workers' note and harvest ledger

**Provenance.** The round-002 re-review workers raised non-blocking notes alongside the nine
residual blockers; the catalog-side fixes landed in the catalog's corrections log, and the
plan-side fixes landed in the plan. Each note below has **its own row** with a disposition and
a content-anchored pointer — including the notes that were **re-raises** of an extra already
dispositioned in §D.3 (those are cross-referenced and are **not** counted again in the tally).

| ID | The note (as round 002 raised it) | Disposition | Closure (content-anchored) |
|---|---|---|---|
| **H-101** | The plan's self-declared base commit named the revision commit's **parent**, not its own tip. | **FIXED** | `plan-overview.md` header (the self-declared tip) |
| **H-102** | The read-charge citation covered only one of the two charge sites. | **FIXED** | `metric-catalog.md §11 → *C94*` + `§4 → *F7*`; `plan-overview.md §1 → *Write-side restatement (A6/A7)*` + `§4 → *P1b task 14*` + `§4 → *P1b risk row 3*` + `§13.1.1` |
| **H-103** | The plan attributed the full wake-outcome set to the worker-pool statistics literal. | **FIXED** | `plan-overview.md §4 → *P1b task 9*` + `§4 → *P1b exit criteria*`; `metric-catalog.md §14.4 → *wake-turn outcome*` |
| **H-104** | The series-producibility rule was not scoped to daemon-origin, so the Prometheus built-ins would count against the daemon budget. | **FIXED** | `plan-overview.md §8 → *series-producibility rule*`; `metric-catalog.md §7.1 → *Task / owner mapping*` |
| **H-105** | The vocabulary-freeze producer was named in the catalog annex but **not** in the plan's acceptance criterion, so the freeze could pass without the marker being emitted. | **FIXED** | `metric-catalog.md §11 → *C89*` + `§7.1 → *Task / owner mapping*`; `plan-overview.md §4 → *P0 task 6*` |
| **H-106** | The TSDB back-out assumed a pre-install Prometheus-config backup that no task creates — the plan installs Prometheus on this host for the first time. | **FIXED** | `plan-overview.md §13.1.6` (the back-out names the generated config and the generated alert-rules file, both created by the plan) + `decisions.md §ACTIVE → §P → row 6` |
| **H-107** | The register's disposition anchors were on the previous revision's numbering and did not resolve into the current plan. | **FIXED** | `decisions.md` preamble → *Rule 1*; `decisions.md §DISPOSITION → §D.1` … `§D.5` (every anchor regenerated against the current tree) |
| **H-108** | The closure standard counted a corrections-log entry as a resolution, and the harvest's "no note remains open" claim was unsupported. | **FIXED** | `decisions.md` preamble → *Rule 4*; `decisions.md §DISPOSITION → §D.3` + `§D.5` (countable ledgers with tallies); the retired form: `decisions.md §SUPERSEDED → rows S-13, S-16` |
| **H-109** | The reconciliation note's labels were inverted: the retired total was called the final reconciliation and the current one an earlier slip. | **FIXED** | `decisions.md §ACTIVE → §Q → §Q.1 counts ledger`; `metric-catalog.md §7 → *Delta trail (the basis of …)*`; the retired form: `decisions.md §SUPERSEDED → row S-11` |
| **H-110** | The catalog-size waiver row still quoted a stale catalog line count. | **FIXED** | `decisions.md §ACTIVE → §F → row G-1` (retired form: `decisions.md §SUPERSEDED → row S-10`) |
| **H-111** | Three non-blocking rows were content-free placeholders. | **FIXED** | `decisions.md §ACTIVE → §L → rows NB-1, NB-3, NB-9`; the retired forms: `decisions.md §SUPERSEDED → row S-14` |
| **H-112** | The cross-document identity claim was absolute and did not exclude the frozen historical input. | **FIXED** | `decisions.md §ACTIVE → §Q → §Q.2 cross-document identity` (scope note added); the retired form: `decisions.md §SUPERSEDED → row S-12` |
| **H-113** | The plan's architecture paragraph carried the retired producer anchor for the wake counters. | **FIXED** | `plan-overview.md §4 → *P1b task 9*`; `metric-catalog.md §11 → *C73*` + `§11 → *C92*`; the retired form: `decisions.md §SUPERSEDED → row S-2` |
| **H-114** | A family entry's parenthetical disagreed with the summary row it summarises. | **FIXED** | `metric-catalog.md §11 → *C78*` + `§5 → *F21*` + `§1` |
| **H-115** | The catalog's summary block used one total in **two senses** (a daemon-plus-shell subtotal and a prior-total reference). | **NOTE** — verified against the tree: the two figures are a **daemon-origin subtotal plus the shell-origin contribution** and a **prior-total historical reference**, and the summary block labels each of them; the ledger is the single quoted place (`metric-catalog.md §14.1`). No copy of either number is held in the register. | `metric-catalog.md §1 → *Reconciliation basis*` + `§14.1`; `decisions.md §ACTIVE → §Q → §Q.1 counts ledger` |
| **H-116** | The ledger's vocabulary row filed a metric under the wrong family. | **FIXED** | `metric-catalog.md §11 → *C87*` + `§14.4` |
| **H-117** | The boundedness constant named a smaller family set than the catalog carries. | **FIXED** | `metric-catalog.md §11 → *C77*` + `§2 → *F0.2*` + `§11 → *C64*` |
| **H-118** | The task map was not moved with the family split. | **FIXED** | `metric-catalog.md §11 → *C90*` + `§7.1 → *Task / owner mapping*` |
| **H-119** | The receiving family's Source bullet carried no anchor for its own claim-latency series. | **FIXED** | `metric-catalog.md §11 → *C90*` + `§4 → *F11*` |
| **H-120** | The fully-wired bound was carried forward unchanged after five families moved, and its derivation was never written down — so a reader could not tell it apart from a **derived** ceiling. | **FIXED** | `metric-catalog.md §11 → *C79*` + `§14.1 → *bound cross-check row*` (the cross-check row states which figure is the **provenance-disclosed adjusted bound** and which two are **derived ceilings**) + `§7`; `decisions.md §ACTIVE → §Q → §Q.1` |
| **H-121** | A rule-less rounding figure propagated into a hard family count. | **FIXED** | `metric-catalog.md §11 → *C80*` + `§4 → *F12*` |
| **H-122** | The ledger's vocabulary row named a placeholder correction id instead of a real one. | **FIXED** | `metric-catalog.md §11 → *C85*` + `§14.4` |
| **H-123** | The histogram counting convention was ambiguous across families. | **FIXED** | `metric-catalog.md §11 → *C81*` + `§8.11` + `§5 → *F21*` + `§4 → *F7*` |
| **H-124** | The carrier class anchor was cited without its decorator line. | **FIXED** | `metric-catalog.md §11 → *C82*` + `§8.13` |
| **H-125** | The ledger's per-family table did not flag a changed family although its heading promises it. | **FIXED** | `metric-catalog.md §11 → *C84*` + `§14.3` |
| **H-126** | **Re-raise** of the five unanchored annex label sets (first raised in round 001). | **FIXED** — cross-referenced to `§D.3 → row H-016`; not counted again in the tally. | `metric-catalog.md §11 → *C88*` + `§8.10` + `§0.5` |
| **H-127** | The Prometheus TSDB capacity derivation omitted its **scrape-interval** input and did not reproduce its own figure. The dispatcher downgraded this to a **note** (the derivation is present inline and the stated floor is conservative under the plan's own configuration), with a required correction: state the scrape interval and the observed series count in the formula and propagate the figure for cross-document identity. | **NOTE** — the required correction was applied, and the figure now appears consistently in both documents. | `metric-catalog.md §11 → *C93*`; `plan-overview.md §13.1.6` + `§4 → *P2 risk row 4*`; `decisions.md §ACTIVE → §P → row 6` (the capacity precondition and its derivation) |

**Round-002 note/harvest tally:** harvested **27** (26 raised in round 002 + 1 dispatcher
judgment note) · dispositioned **27** (25 FIXED + 1 NOTE + 1 FIXED re-raise, cross-referenced) ·
open **0**.

---

## D.6 Disposition totals — the closure ledger

| Ledger | Harvested | FIXED | NOTE | WAIVED | **Open** |
|---|---|---|---|---|---|
| **Round 001 — blocking** (§D.1) | 13 | 13 | 0 | 0 | **0** |
| **Round 001 — non-blocking** (§D.2) | 12 | 12 | 0 | 0 | **0** |
| **Round 001 — harvested extras** (§D.3) | 30 | 29 | 1 | 0 | **0** |
| **Round 002 — residual blockers** (§D.4) | 9 | 9 | 0 | 0 | **0** |
| **Round 002 — worker notes / harvest** (§D.5) | 27 | 25 | 2 | 0 | **0** |
| **TOTAL** | **91** | **88** | **3** | **0** | **0** |

**"No harvested note remains open" is demonstrable:** every harvested finding has its **own
row** with a disposition and a content-anchored pointer, and the tally above closes at
**zero open**. A corrections-log entry is **not** counted as a disposition (Rule 4).

**Wave C note (this commit).** The M-4 stale-string sweep re-checked every retired string
across the four planning documents (the catalog, the plan, this register and the frozen
architect input). Each live-guidance residual it closed was a **residual of a row already
listed above** — the sibling-tip pointer → `§D.4 → row R-7`; the retired count copies and the
retired design wording → `§D.4 → row R-5` + `§D.5`; the retired vocabulary and anchor strings →
`§D.1 → rows B-7, B-10, B-11`. No **new** finding was raised, so the ledger above is unchanged:
harvested 91 · dispositioned 91 · open 0.

**Waivers.** No finding in either round was waived — every open item was either fixed or
explicitly noted with a rationale. The plan's own *deliberate* limitations (the §F register)
are decisions about the **design**, not waivers of **findings**, and are recorded in §ACTIVE.

---

*End of decisions.md — §ACTIVE · §SUPERSEDED · §DISPOSITION.*
