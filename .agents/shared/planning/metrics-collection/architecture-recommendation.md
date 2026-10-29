# Architecture Recommendation: v2 Metrics Collection (WS-B, step 2)

Date: 2026-10-29 · Author: architect · Status: Ready for review
Worktree: `agents-ensemble-wt-metrics-v2` · Branch `feature/metrics-collection` @ `ad3a33171`
Artifacts read in full: `plan-overview.md` (338) · `metric-catalog.md` (995) · `decisions.md` (310)

**Scope.** This document **validates and deepens** the completed v2 plan. It does **not**
rewrite it. The planner's three files are untouched; every finding below maps to a section
of them and appears as a numbered amendment in §10.

**Method.** Three skill-equipped analysts (one design skill each: `resilience-design`,
`structural-design`, `data-flow-design`) analyzed disjoint focus areas; every load-bearing
claim was then re-verified by the architect directly in the worktree before entering this
document. Claims I re-verified myself are marked **[VERIFIED]**; analyst-only claims are
marked **[REPORTED]** and carry their own `file:line` citation.

**Binding decisions — not relitigated.** (1) host binaries/VM compose, k8s out; (2) journal
metrics via the Python twin `daemon/tools/upgrade_journal.py`; (3) separate 127.0.0.1:9798
listener, default OFF via per-request `ENSEMBLE_METRICS_ENABLED`; (4) Prometheus authoritative
for alerting, `resource_monitor` PG tier authoritative for per-instance API views; (5)
dependency-free renderer, Prometheus classic text exposition. Where a binding decision is
correct, this document deepens it. Where it is *underspecified* in a way that would produce a
wrong implementation, that is flagged as an amendment, not a re-decision.

---

## 0. Verdict summary

| # | Focus area | Verdict | One line |
|---|---|---|---|
| 1 | Listener architecture | **NEEDS-AMENDMENT** | Thread/loop topology and the writer↔renderer concurrency contract are unspecified; a shared-event-loop listener would defeat CR-1. |
| 2 | Registry design | **NEEDS-AMENDMENT** | Locking, label validation and never-exported enforcement need executable contracts; the hand-listed tool-category vocabulary is already falsified. |
| 3 | Kill-switch layering | **NEEDS-AMENDMENT** | The per-request env read is a non-issue (keep it), but env-*unset* must defer to the managed key instead of silently dark. |
| 4 | Hot-path seams | **NEEDS-AMENDMENT** | F14's cited hook misses the terminal usage chunk; §8.13's renderer-as-consumer is unimplementable; the checkpoint write path is uninstrumented. |
| 5 | Census discipline | **NEEDS-AMENDMENT** | Coexistence and the sum-ties invariant are sound; execution home, timeout values, self-exclusion rule and matching mechanics are missing. |
| 6 | Storage split boundary | **SOUND** | The boundary holds; two clarifications needed (no per-instance daemon families; PG retention tiers are 48 h/14 d/180 d, not just rollup intervals). |
| 7 | Phasing risk | **NEEDS-AMENDMENT** | Vocabulary-freeze procedure, thread-naming prerequisite, P0-core/P0-spine split and textfile/alert dependencies need adjustment. |
| X-a | PyInstaller freeze | **SOUND** | Stdlib-only surface; one import-placement rule to keep the analyzer honest. |
| X-b | macOS / Windows dev boxes | **SOUND** | `read_proc_snapshot` degrades to `None`; the listener boots. One missing row in the §6 dark matrix. |
| X-c | Test architecture | **NEEDS-AMENDMENT** | Four P0 tests are missing, including the wedged-writer simulation. |

**Overall: the v2 plan is architecturally sound in its decisions and under-specified in its
mechanics.** Nothing found requires a decision reversal. 5 of 7 focus areas need amendments,
all surgical, none structural. The single most important finding is that **three families
would ship silently wrong** (F14 zero-valued, `wake_turns_total` uncounted, `op="aput"`
never firing) — defects that no phase exit criterion would catch, because each produces a
well-formed but empty series.

---

## 1. Focus 1 — Listener architecture

### 1.1 The gap

The plan commits to the right shape (separate localhost listener, never a route on the main
app, bind-failure degrades to disabled) but never states **which thread and which event loop
serve the socket**. This matters more than any other single line in the plan: CR-1 exists
because a wedged daemon could not export its own `/metrics` on the main port. A listener
that shares the main event loop inherits that failure exactly.

Two independent analysts reached the same conclusion from different skills — Worker A
(`resilience-design`) from the wedge-resilience angle, Worker B (`structural-design`) from
the lock-contention angle: **the listener must own a dedicated thread.**

### 1.2 Threading-model options

| Option | I/O-bound wedge | CPU-spin wedge | Complexity | Teardown | Freeze | Verdict |
|---|---|---|---|---|---|---|
| **A. Own thread + stdlib `http.server` (`ThreadingHTTPServer`)** | ✅ survives — I/O releases the GIL | ❌ starved — spin holds the GIL | Low | stop event → `shutdown()` → `join(timeout)` | ✅ stdlib, auto-collected | **RECOMMEND** |
| B. Second ASGI/uvicorn app in-process | ✅ | ❌ | High (ASGI stack, deps, lifespan coupling) | process-level | ❌ heavier PYZ | Reject |
| C. Second uvicorn **process** (sidecar) | ✅ strongest isolation | ✅ | High (packaging, IPC, two processes) | process-level | ⚠️ spawn friction | Defer |
| D. Socket serviced on the main asyncio loop | ❌ **defeats CR-1** | ❌ | Low | task cancel | ✅ | Reject |

**Recommendation: Option A** — a dedicated daemon thread running `ThreadingHTTPServer`
bound to `127.0.0.1`, started from the FastAPI lifespan. It adds no dependency, no second
event loop, no second process, and survives the failure mode the plan exists to cover
(I/O-bound starvation). It does **not** survive a CPU-spin wedge — the GIL is held
continuously by a spinning thread — which is precisely why CR-1's out-of-process witnesses
(node_exporter textfile, `up==0`/`absent()`, `scrape_duration_seconds`) remain mandatory
rather than optional.

### 1.3 The concurrency contract (render ↔ wedged writer) — the missing centerpiece

**[VERIFIED]** `daemon/checkpoint_metrics.py` today holds a **per-object `threading.Lock`**
(`Counter` `:73-79`, `Histogram` `:160-182`), and `render_metrics()` (`:304`) reaches series
through `.get()`, which takes that lock (`:95-104`, `:224-248`). There is **no
registry-level lock**. Consequence: if a wedged thread holds a metric's lock, the render
**blocks** — the scrape hangs, which is the CR-1 failure mode reproduced inside the CR-1
mitigation.

Required contract (three rules):

1. **No global registry lock.** Keep per-metric locks. A single global lock lets one wedged
   family stall all 26.
2. **Render never blocks.** The renderer reads via **non-blocking try-acquire** (bounded
   wait, e.g. 50 ms). On contention it serves the **last published snapshot** and emits
   `# <family> unavailable: lock_contended` — consistent with the plan's §6 degradation
   contract (`# <family> unavailable: <reason>`) and its absent-≠-zero rule.
3. **Copy-on-publish.** Writers update a family's own state under its own short lock and
   atomically swap a published pointer; the pointer swap is a plain assignment (atomic
   under the GIL), so the renderer can pick up a consistent snapshot without a lock.

Per-op cost is irrelevant to the decision; the *blocking* behaviour is. Worker B measured
`Counter.inc()` ≈ 150 ns and `Histogram.observe` (B=17) ≈ 1394 ns — the latter dominated by
the **O(B) cumulative bucket scan** (`:220-222`, the loop does not break). **Amendment A9**:
store non-cumulative bucket counts and accumulate at render → observe becomes O(1) (~300 ns)
and render pays O(B) once per series per scrape. This also means the plan's bucket-set
extensions (§8.11, 7 histograms, top bucket 300 s) do not scale the hot path.

### 1.4 Boot / teardown lifecycle

**[REPORTED, consistent with plan §4 P0]** The boot order must be:

| Step | Where | Why |
|---|---|---|
| Start after `resource_monitor` | `daemon/api.py:2241-2288` | `app.state` populated; the spine is live before a scrape can be meaningful |
| Start after `llm_stream_watchdog` | `daemon/api.py:2212-2226` | same rationale; the watchdog is the template for "start a thread in lifespan" |
| Inside the FastAPI **lifespan** | — | the import must live on the lifespan path so the PyInstaller analyzer pulls it (§8.1) |
| Bind failure → **disabled + logged** | mirror `api.py:2282-2287` | never abort boot (decision 3) |
| Teardown: stop event → `server.shutdown()` → `join(timeout=5)` → close | after `resource_monitor` stop (`:3411-3433`) and wedge-capture stop (`:3435-3464`) | drain order; `daemon=True` thread so a wedged join cannot hang shutdown |

### 1.5 Failure-mode table

| Failure | Detection | Listener behaviour | Prometheus-visible signal |
|---|---|---|---|
| Event loop wedged ≥30 s (I/O) | lag probe / `scrape_duration_seconds` | serves last snapshot; empty if none yet | `scrape_duration_seconds`, `ensemble_loop_lag_seconds{stat="p95"}` |
| Registry lock held by a wedged writer | render try-acquire timeout | skip family, emit comment line | `# <family> unavailable: lock_contended` |
| **CPU-spin wedge (GIL held)** | external only | **starved — cannot serve** | `up==0`/`absent()` + textfile + exporters (CR-1 witnesses) |
| Listener thread crashed | `app.state.metrics_listener is None` | disabled, daemon continues | `up==0` / absent target |
| Port collision (9798) | `bind()` raises | disabled, logged | target absent |
| `ENSEMBLE_METRICS_ENABLED` removed mid-run | per-request read | empty 200 < 1 ms | `ensemble_metrics_up=0` |
| Daemon OOM-killed | process gone | no listener | `up==0` |
| Render exception in one family | per-family try/except | skip that family, others render | `ensemble_metrics_scrape_requests_total{outcome="error"}` + comment line |

---

## 2. Focus 2 — Registry design

### 2.1 Locking primitives

| Option | Writer cost | Reader cost | Wedge behaviour | Verdict |
|---|---|---|---|---|
| One global registry lock | + cross-family contention | blocks all families | worst — one wedged writer stalls 26 families | Reject |
| Per-metric lock (today `:79`/`:182`) | ~150 ns | ~150 ns | blocks only the contended family | **Keep + extend** |
| Lock-free `dict[k] += 1` | ~80 ns | ~80 ns | none | Reject — lost-update defect class |
| Lock-free atomic counter (ctypes) | ~100 ns | ~100 ns | none | Viable, stdlib-complexity not justified |
| **Per-metric lock + non-blocking render snapshot** | 150 ns | ~0 (cached) | **render never blocks** | **RECOMMENDED** |

### 2.2 Label validation — three tiers, not one

**[REPORTED]** A single validation point cannot catch both code typos and runtime drift.
Recommendation:

| Tier | Where | Catches | Cost |
|---|---|---|---|
| 1. Enum pin | **registration** — `register_histogram(name, label_keys, allowed_values={...})` | wrong label *key* (raises, mirroring today's `KeyError` at `:206-211`) | 0 at runtime |
| 2. Reserved bucket | **first observe** | wrong label *value* → folded to `other` + counted in `ensemble_metrics_invalid_label_total{metric,label}` | ~40 ns (frozenset membership) |
| 3. Allowlist predicate | **render** | anything that escaped 1–2 (defence in depth; also the §7 never-exported hook) | 0 at runtime |

Tier 3 is also where the plan's §7 never-exported list becomes **executable** instead of
prose: per-family label-name allowlist + per-label value membership + **hard** cardinality
cap + a name regex `^ensemble_[a-z0-9_]+$` + a label-value charset gate (no `=`, `,`, `"`,
newline). Note the plan currently specifies `limits.metrics_cardinality_cap` as a **soft**
cap ("warn on breach", plan §5) — which permits unbounded growth that §7 forbids. **Amendment
A7** makes it hard.

### 2.3 🔴 The tool-category vocabulary is already falsified

The catalog (§8.6) states the 37-value list is "the complete set of `register_tool_category(...)`
calls under `daemon/tools/`". **[VERIFIED — falsified]** The tree registers categories the
list omits, including at least: `board-link`, `dynamic-skill`, `ens-db`, `ens-env`,
`image-gen`, `self-alarm`, `skill-evolution`, `system-log`. Worker B measured **49** distinct
categories in-tree.

Under a closed-enum registry this is not cosmetic: an unregistered value either raises at
observe (F20 breaks) or silently inflates cardinality past the F20 estimate of 37 and the
521-series total. **Amendment A1**: derive the vocabulary **at runtime** from
`get_tool_categories()` **[VERIFIED to exist at `daemon/tools/_tool_registry.py:1598`]**,
cross-check it against the annex in a test, and keep a reserved `other`. The lesson generalizes:
**every closed vocabulary in §8 must be derived from code, not hand-listed** — the same drift
class the plan already caught once for the sweep lanes (§8.4, 7 → 13).

### 2.4 Name / alias governance

**[REPORTED]** The one-release alias rule (§0.3) has no mechanics. Required:

- The alias is a **second exposition of the same series**, never a second measurement.
- A **deprecation log line at first emission**, once per process (not per scrape).
- The two legacy names (`message_api_checkpoint_list_total`, `message_api_saver_op_latency_seconds`)
  are referenced by four test files only → migrate them so the alias is droppable.
- **Prometheus consequence:** when the alias disappears, any consumer still on `message_api_*`
  sees `absent()`. Removal procedure must include a documented "one release + one burn-in"
  rule or a recording rule. (This is a 🟢 — the plan's alias lifetime is one release and the
  legacy names have no known dashboard consumers in-repo.)
- The legacy names violate the `^ensemble_` rule the plan wants going forward → alias-only,
  never canonical.

### 2.5 Memory ceiling — SOUND, do not spend plan budget here

**[REPORTED, measured]** Per-series cost ≈ 580 B (labeled counter) / ≈ 940 B (B=17 histogram).
521 rendered series ≈ **~263 KiB**; at the 2000-series cap, worst case ≈ **2.1 MiB** — under
0.15 % of the daemon's ~1.9 GiB RSS. **Memory is a non-issue; cardinality is the risk** — and
§2.3 shows the cardinality budget was computed from a hand-list that has already drifted.

---

## 3. Focus 3 — Kill-switch layering

**Verdict: NEEDS-AMENDMENT (narrow). The design is right; two semantics are undefined.**

**(a) The per-request env read is not a cost.** **[REPORTED]** `os.environ.get` is a
dict-backed mapping lookup (~50–100 ns, no syscall). At a 15 s scrape cadence this is
~7 ns/s. **Keep the per-request read** — a TTL cache would add staleness that defeats the
entire emergency-off purpose. The plan's "per-request read (MA-1)" is correct as written; no
amendment needed. (The plan never claims it is expensive — it just needs to be stated.)

**(b) Truthy set.** **[REPORTED]** For a security-sensitive exposition surface, fail closed:
accept only `{1, true, yes}` case-insensitive; everything else — including `ture`, `on`,
`enabled`, `0`, empty — is OFF. The accepted set must be documented in catalog §0.8.

**(c) Layer disagreement — the real gap.** The plan says the per-request env read is "the
authoritative emergency off-switch", layered under a restart-gated managed key. As written,
that produces an unexplainable state: an operator sets `limits.metrics_enabled=true` in the
DB, leaves the env unset, and the system is **all dark with no hint** — §6's "empty-shape
200" cannot distinguish "switched off" from "unset".

**Resolution (recommended, within mandate):**

| Env value | Managed key | Effective state | Rationale |
|---|---|---|---|
| unset | off (default) | OFF | default-OFF, fail-closed |
| unset | **on** | **ON** (managed chain decides) | env-unset is not a statement of intent |
| explicit off (`0`/`false`/`no`) | any | **OFF** | explicit env off always wins — the emergency switch |
| explicit on (`1`/`true`/`yes`) | off | ON | env on overrides the default |
| explicit on | on | ON | — |

Plus **one boot log line** when the env is unset while the managed key is on
("`ENSEMBLE_METRICS_ENABLED` unset; deferring to `limits.metrics_enabled`"), so the state is
never mysterious. §6's dark-families matrix needs the matching row.

---

## 4. Focus 4 — Hot-path seams

### 4.1 🔴 F14's cited hook misses the terminal usage chunk

The catalog (F14) cites `daemon/graph.py:3811-3812` as where `usage_metadata` is attached.
**[VERIFIED]** Two attach paths exist:

- `graph.py:3771-3774` computes `usage_metadata`; when the chunk carries **no choices**
  (`len(choices) == 0`), it is attached through the **constructor** at `:3778-3780` and the
  function **returns early at `:3784`**.
- `graph.py:3811-3812` (`message_chunk.usage_metadata = usage_metadata`) is the **non-terminal**
  path — reached only when the chunk also carries choices.

A terminal usage chunk with usage-but-no-choices — the shape that carries the actual token
counts — takes the early return and never reaches `:3811-3812`. A counter hooked only at the
cited line would **ship permanently zero or near-zero**. The converter runs **per chunk**, so
the cited line is also a per-chunk site (hot path), the opposite of what the plan wants.

**Amendment A2**: hook the **terminal branch `:3776-3784`** (or the aggregated call site
`graph.py:9176-9179`), add a per-call dedupe (some OpenAI-compatible backends put `usage` on
every chunk → double counting), and state the "terminal-chunk only" rule explicitly.

### 4.2 🔴 §8.13's Carrier-Threading contract is unimplementable as written

§8.13 says: producer computes once → carried on `ProcessingContext` → **"the exposition
renderer reads the carried value and increments `ensemble_wake_turns_total{outcome=...}`"**.

Two defects:

1. **The consumer cannot read a per-message carrier.** The carrier field lives on
   `ProcessingContext` (`daemon/services/message_processing_pipeline.py:121`, sibling carrier
   `is_fresh_episode_user_message` at `:176`) and dies with the message. The renderer runs at
   **scrape time** and sees only the registry. The blueprint's Carrier-Threading Pattern
   terminates at a **semantic in-graph consumer** — not at an exposition layer.
2. **The named producer cannot produce the outcomes.** `task_processor.py:569-598` computes
   the **fresh-episode flag** (`priority == 1 AND type == HUMAN`, the blueprint's ledger
   identity), reached only after a claim succeeds. The claim-path outcomes
   (`empty_claim_attempts`, `claims_skipped_due_to_busy_instance`, `notifications_sent`,
   `workers_woken_by_timeout`) live in `worker_pool.py:1381-1386`; `fresh_episode_reset` /
   `stale_episode` are knowable only at the graph seam `graph.py:7319`
   (`_detect_fresh_episode_reset`). One producer can yield at most 2 of the 7 outcomes.

**Amendment A3** (both halves): split into **two producers** — (a) the claim path in
`worker_pool.py`, (b) the episode outcome at `graph.py:7319` — each computed **once**, each
carried; and replace "the exposition renderer reads the carried value" with "**a live turn-end
consumer increments the counter; the renderer only reads the accumulated counter**". The
"consumer-seam re-derivation is forbidden" rule survives unchanged. The `LostWake` alert
depends on this family, so it is the highest-leverage fix in the plan.

### 4.3 🟡 F20's "per-turn" definition and the turn-duration anchor

**[VERIFIED]** `tap_node_return` has **5 call sites** (`graph.py:7852`, `:9510`, `:10174`;
`instance_messaging.py:1661`, `:5244`). The `agent_node_return` site (`graph.py:10174`) sits
inside `agent_node`, and the graph runs an agent→tools→agent cycle — so the tap fires **once
per node return**, i.e. N+1 times per turn with N tool rounds.

- `ensemble_turn_events_total{source}` therefore counts **node returns**, not turns. The
  `source` vocabulary is itself node-oriented (`agent_node_return`), so this is consistent —
  but the plan must say so in the HELP text rather than let "turn" imply one-per-turn.
- `ensemble_turn_duration_seconds` has **no start anchor**: the tap measures its own latency,
  which **includes** its `asyncio.to_thread(upsert_batch)` DB write
  (`message_tap.py:302-306`). A histogram fed from the tap as-is measures **DB-write latency
  mislabelled as turn duration**.

**Amendment A4**: define "turn event = one `tap_node_return` invocation" on the HELP text;
specify the anchor pair (entry-tap timestamp → node-return timestamp) for the duration
histogram, or drop that family; state the tool→category lookup plus an `other` fallback.

### 4.4 🟡 The checkpoint **write** path is uninstrumented

**[VERIFIED]** `time_saver_op` has exactly **one** non-test caller: `persistence.py:509`
(`time_saver_op("aget", …)`). The checkpoint **write** path runs through
`daemon/checkpoint_adapter.py:377-379` (`aput` → `scrub_saver_write_args` → `_call_with_retry`)
and never touches `checkpoint_perf`. The only other `aput` reference is the offline migrator.

Consequence: `ensemble_checkpoint_op_seconds{op="aput"}` **can never fire**. The catalog
acknowledges this in F7's series estimate ("only `op=aget` fires in production"), so this is a
known limitation — but the plan's §1 incident table still maps the **write-side** incident
("17 s writes") onto the family, and the `op` enum still declares four values. The plan's
own corrected "Why" (§1) argues writes are *not* the bottleneck, so the alert (`op="aget"`) is
right — but the declared enum over-promises.

**Amendment A5**: instrument the write path at the seam that **already exists** —
`checkpoint_adapter.py` carries a `_charge_checkpoint_read(...)` charge hook on `aget_tuple`
(the P2 T3 charge seam). Extending that seam to `aput`/`aput_writes` is the natural
implementation. Until it lands, narrow the declared `op` enum to the observable set and restate
the write-side mapping in §1.

### 4.5 Off-loop discipline — restate the claim

The plan's blanket claim (§3) is "Metric collection never adds event-loop work". Literally
false — every increment is loop or executor-thread work — but materially harmless if the
rules hold. **Amendment A6** restates it as: **no blocking work, no I/O, no logging, and no
per-chunk work on the event loop**, plus ≤1 `time.perf_counter()` per observation window.

**Flush model — confirmed pull-only.** No flush exists: the registry accumulates in RAM and is
read at scrape time; nothing is pushed. The exceptions are the **sampler-pushed gauges**
(F1 lag probe, F2 thread-CPU, F3 wedge counters, F6 census, F8 table sizes, F21 journal) —
all off-loop by construction.

### 4.6 Per-turn counter-update cost budget

**[REPORTED — measured in-tree, CPython 3.13]**

| Increment | Fires per | Site | Cost |
|---|---|---|---|
| `ensemble_llm_tokens_total{direction}` | 1 per LLM **call** | `graph.py:3776-3784` (executor thread) | 2 × 150 ns |
| `ensemble_llm_token_usage_missing_total` | 1 per LLM call | same | 150 ns |
| `ensemble_llm_request_total{outcome}` | 1 per attempt | `llm_error_classifier.py:820`/`:959` | 150 ns |
| `ensemble_llm_request_seconds` | 1 per attempt | `graph.py:9176` | 1 clock + ~1.2 µs observe (B=12) |
| `ensemble_turn_events_total{source}` | 1 per **node return** | `message_tap.py:234` | 150 ns + the tap's own await |
| `ensemble_tool_calls_total{category}` | 1 per tool call | tap + category lookup | 150 ns + 1 dict lookup |
| `ensemble_wake_turns_total{outcome}` | 1 per claimed message | producer (per A3) | 150 ns |
| `ensemble_instance_lifecycle_total{event}` | 1 per lifecycle event | `instance_lifecycle.py` | 150 ns |
| `ensemble_checkpoint_op_seconds{op=aget}` | 1 per **/messages API read** — *not* per turn | `persistence.py:509` | 1.4 µs (B=17) → ~0.3 µs after A9 |

**Budget rule:** dict/hash increments only · no I/O · no logging on the hot path · ≤1 clock
read per window · no per-chunk work. Total added work for a 1-tool-round turn ≈ **4 µs**
(8 counter incs + 2 histogram observes) against a multi-second turn — **< 0.01 %**. The tap's
own DB write exceeds any metric cost there by 3–4 orders of magnitude. The metric increments
must sit **inside** the tap's existing `try/except` (`message_tap.py:308-309`) so a metric
error cannot produce per-turn WARNING noise.

---

## 5. Focus 5 — Census discipline

**Coexistence: SOUND.** Three flows, three consumers, no overlap:

| Flow | Reads | Feeds | Touches /metrics? |
|---|---|---|---|
| `readiness.py:784` `_evaluate_leak` | **pool counters** (`requests_waiting` / `pool_available` decay) — not `pg_stat_activity` | readiness composite (`pool_leak` class) | No |
| `wedge_capture/db_capture.py` | one-shot connection, `application_name='wedge-capture'` | wedge artifacts (`threads.csv`/`lag.json`/`readyz.json`) | No |
| `metrics_census` (greenfield) | `pg_stat_activity`, dedicated non-pooled | `/metrics` (`ensemble_pg_*`) | Yes |

Double-counting is impossible because no consumer receives two flows. The `application_name`
tags keep the three distinguishable.

**Amendment A8 — five additions to the §9 census annex:**

1. **Execution home: a dedicated daemon thread** (`CensusSampler`, `daemon=True`,
   `time.monotonic` tick loop). *Not* the listener thread (request-driven → no series
   between scrapes) and *not* the event loop (it would die in a wedge, defeating CR-1 for the
   census). The plan never says where the census runs.
2. **`statement_timeout = 5 s`, `connect_timeout = 3 s`** — the plan says "set" but not to what.
   5 s < ⅓ of the 15 s cadence.
3. **Self-exclusion rule: exclude only `ensemble-metrics-census`; count `wedge-capture` rows
   under `other`.** Excluding wedge-capture rows would transiently break the invariant
   `sum(ensemble_pg_leaked_txn) == ensemble_pg_backends{state="idle_in_transaction"}`.
   Consequence: the `other`-growth signal must use a **sustained window** (5 min), not
   single-tick spikes, so an incident-correlated capture does not read as map drift.
4. **Matching in SQL, not Python.** Evaluate the fragment→site `CASE`/`LIKE` map **inside the
   census query** (one round trip, server-side) rather than fetching `query` text and matching
   in Python. Drift guard: one unit test per site (6) + the sustained `other`-growth signal.
   **Residual risk to state honestly:** under psycopg3 extended/pipeline protocol, `pg_stat_activity.query`
   is the last statement text and may be blank for some prepared-statement paths — those
   backends land in `other`, which the invariant still ties correctly, but the site map
   under-attributes. The plan should say so rather than claim exact attribution.
5. **Failure posture already correct** — timeout/refusal ⇒ no series + stale
   `ensemble_pg_census_last_success_timestamp_seconds` (absent ≠ zero, §0.11).

---

## 6. Focus 6 — Storage split boundary

**Verdict: SOUND, with two clarifications.**

### 6.1 Family classification (26 families, idiom A/B/C)

No daemon-origin family is per-instance. The per-instance tier is exactly what §7 of the plan
already excludes from `/metrics`; the catalog's families are aggregate (count-by-status,
max-across-instances, table-level, lane-level). **[VERIFIED]** The plan's MA-10 cardinality
concern is therefore satisfied by construction — the split boundary can be stated as a rule
rather than as a per-family judgement.

| Idiom | Families | Restart behaviour |
|---|---|---|
| **A — in-process registry (resets)** | F0.1, F0.2, F0.4, F1, F2, F3, F4, F5, F6, F7, F8, F9, F11, F12, F14, F15, F16, F19, F20 | counters reset; `rate()`/`increase()`-safe (§10.2) |
| **B — PG/durable** | F0.3, F10, F13 (status part), F17, F18 | survive daemon restart; queryable directly from PG |
| **C — file/disk** | F21 (journal), F22 (textfile) | survive daemon restart; readable from disk |

**Amendment A10**: state the rule explicitly in §3 — *"per-instance attribution = PG-only
(`/api/resources/*`); every daemon-origin `/metrics` family is aggregate. No daemon-origin
family carries a per-instance label."* Add it to the §8 vocabulary annex as a cardinality
rule so it is enforced, not just stated.

### 6.2 Retention interplay — the plan understates the PG tiers

**[VERIFIED]** `daemon/config.py:818-888`: `retention_raw_minutes` default **2880 (48 h)**,
`retention_minute_hours` default **336 (14 d)**, `retention_hour_days` default **180 (180 d)**.
The plan's §9 R5 line states only the sampling/flush/rollup **intervals** ("sampler 30 s, ring
48 h, flusher 60 s") and never states the retention **windows**. Prometheus (§2) is 30 d raw /
90 d 5 m-downsampled.

**Amendment A11**: state the full PG tier ladder (48 h raw / 14 d minute-rollup / 180 d
hour-rollup) and the interplay rule: *any quantity visible on both sides is governed by
Prometheus for alerting (30 d+) and by PG for per-instance API views; questions beyond
Prometheus's 90 d window are answered from PG's 180 d hour-rollup tier.*

**Two-sampler consistency rule (Amendment A12).** F2 (thread CPU) is produced by the
wedge-capture sampler; the `resource_monitor` tables are produced by the resource-monitor
sampler. Both read `/proc` — reusing one reader module (`resource_monitor/proc_readers.py`,
per plan R4) but running **two independent loops**. They are not required to agree
byte-for-byte; consistency is eventual within one resource-monitor sample interval. The plan
must state this, or a future reader will "fix" a non-bug.

### 6.3 Restart-loss table (three scenarios)

| Series class | Daemon restart | Prometheus **process** restart | Prometheus **storage** loss | Mitigation |
|---|---|---|---|---|
| Idiom A (in-RAM counters/gauges) | counters reset; raw history lost | **preserved** (WAL replay) | lost | `rate()`/`increase()` only; never raw, never `absent()` (§10.2) |
| Idiom B (PG-backed) | preserved | preserved | recoverable **by direct PG query** — *not* by Prometheus back-fill | dashboards query PG for 30 d+ |
| Idiom C (file/disk) | preserved | preserved | recoverable by reading the file | dashboards read the file |
| Prometheus TSDB | unaffected | preserved (WAL replay) | **all history lost** | remote_write to a durable store — 🟢 out of v2 scope |

**Amendment A13**: add this table to catalog §10 and state explicitly that a Prometheus
*process* restart is not a history loss (WAL replay) — the plan's §2 retention sentence
currently leaves a reader assuming the opposite.

---

## 7. Focus 7 — Phasing risk

| # | Risk | Current plan location | Recommended adjustment |
|---|---|---|---|
| R1 | Vocabulary freeze (F0.4) is a **P0 exit**, but some vocabularies depend on P1a/P1b code facts (thread groups need threads named — open item 3) | §4 P0 exit criteria | Freeze label **names** + closed enums pinned to *current* code at P0, plus a **versioned amendment procedure** (bump `ensemble_metrics_vocabulary_version`); a vocabulary bump that changes a literal label value in shipped PromQL is a **breaking alert change** and needs a stated compatibility rule |
| R2 | Worker-thread naming (`worker_pool.py:253`) is a P1a-internal task, but F2 is partly blind until it lands | §4 P1a; catalog open item 3 | **Move thread naming to P0** — one line per construction site (`name=f"worker-{lane}-{worker_id}"`); F2 is then honest from first render. Costs P0 nothing; costs P1a a rework cycle otherwise |
| R3 | P0 exit "4 tables accepting rows" is **blocked by the external WS-A flusher fix**, while P0 risk 1 says "P0 can ship without the resource spine" | §4 P0 exit criteria vs §4 P0 risk 1 | **Split the exit**: **P0-core** (listener, registry, kill-switch, census discipline, vocabularies, managed keys) ships independently; **P0-spine** (the 4-table gate) is owned by the `rm-flusher-fix` worktree and tracked as a separate gate. The contradiction is currently resolved in two directions at once |
| R4 | The textfile writer (F22) is P2, but BashSpin is a P2 alert — the textfile data arrives in the same phase as the alert, leaving no data window for validation | §4 P2 T1/T5 | Move the **minimal** textfile writer (`ensemble_process_cpu_seconds`) to **P1b** so the burn-in has a data source before the alert rules land |
| R5 | P2 alert rules reference label sets still being pinned in P1a/b/c (e.g. `SweepHotLoop` → `lane="rdrs_mission_reconciliation"`) | §4 coupling map ("loose" P1↔P2) | Tighten: P2 **alert authoring** depends on the referenced families' completion; dashboards may be drafted earlier |
| R6 | Claim-latency producer deferred to P1b (G-6) while histogram registration is P0 | — | **No change** — verified: P0 adds no families (plan §4 P0 "none new"), F10 is P1b. Not a gap |

---

## 8. Cross-cutting concerns

### 8.1 PyInstaller / frozen binary — SOUND

`http.server`, `socketserver` and `threading` are stdlib and are collected automatically; the
renderer adds no dependency (decision 5 stands). Two rules:

- **Import the listener inside the lifespan**, not at `daemon/api.py` module top level, so the
  PyInstaller analyzer pulls it through the lifespan import path.
- **Register aliases at registry-construction time**, not lazily — a cold-start render must
  emit the deprecated names for the remainder of their one-release window.

### 8.2 macOS / Windows dev boxes — SOUND (one missing row)

**[VERIFIED]** `resource_monitor/proc_readers.py:read_proc_snapshot` returns
`Optional[ProcSnapshot]` and yields `None` when `/proc` is absent (the reader's documented
None paths). Consequence: on a macOS dev box, `ensemble_thread_cpu_seconds` /
`ensemble_thread_count` (F2) and the resource-spine families (F0.3) go dark — **the listener
itself still boots** (pure stdlib socket, no `/proc` dependency), so every other family still
renders. **Amendment A14**: add an explicit "non-Linux host" row to the §6 dark-families
matrix so the plan states it rather than leaving it to be discovered on a dev laptop.

### 8.3 Test architecture — NEEDS-AMENDMENT

The P0 test plan (plan §4 P0) covers registry render, bucket extension, listener bind,
kill-switch flip, the 4-table gate and the census connection. Four tests are missing, and the
listener must be testable **without a real port bind**:

| Test | How (no real port) | Asserts |
|---|---|---|
| Origin-guard 403 | handler-level request through the guard | non-loopback origin → 403 `origin_not_trusted` |
| Bind-failure degradation | inject a failing bind (port already bound, or a patched `socket.bind`) | listener disabled + logged, **boot continues** |
| **Wedged-writer simulation** | acquire a metric's lock in the test thread, then render | scrape still returns within 100 ms, family skipped with `lock_contended` |
| Kill-switch typo parsing | set `ENSEMBLE_METRICS_ENABLED=ture` | OFF (fail-closed) |

**Harness recommendation:** inject the socket factory so the listener can bind an ephemeral
port (port 0) or no socket at all; keep `render_metrics()` pure so the bulk of the testing is
handler-free and port-free. This is the single cheapest way to make the wedge-resilience
contract testable — and the wedge-resilience contract is the plan's reason for existing.

---

## 9. RISK REGISTER

Architectural risks the plan should absorb before implementation.

| ID | Sev | Risk | Evidence | Impact | Mitigation | Phase |
|---|---|---|---|---|---|---|
| RK-1 | 🔴 | **`ensemble_wake_turns_total` never counts** — §8.13's consumer is a scrape-time renderer reading a per-message carrier, and the named producer can emit ≤2 of 7 outcomes | catalog §8.13; `message_processing_pipeline.py:121`/`:176`; `worker_pool.py:1381-1386`; `graph.py:7319` | LostWake alert blind; the lost-wake incident class unmonitored | A3 — two producers, live turn-end consumer | P1c (before alert deploy) |
| RK-2 | 🔴 | **`ensemble_llm_tokens_total` ships near-zero** — F14's cited hook is the non-terminal attach path; the terminal usage chunk returns early at `graph.py:3778-3784` | `graph.py:3771-3784`, `:3811-3812` **[VERIFIED]** | Token/cost family and its undercount caveat both meaningless | A2 — hook the terminal branch; per-call dedupe | P1c |
| RK-3 | 🔴 | **Tool-category closed enum is falsified** — catalog claims 37 complete; tree has 49, ≥8 verified omissions | `daemon/tools/` registrations **[VERIFIED]**; `_tool_registry.py:1598` | Closed-enum observe raises, or cardinality budget breaks; F20 series estimate wrong | A1 — derive at runtime from `get_tool_categories()`; test cross-check | P1c |
| RK-4 | 🔴 | **Listener shares the event loop** — plan never states thread/loop topology | plan §3, catalog §0.7 (both silent) | `/metrics` dies with the daemon: CR-1's primary witness fails exactly when needed | A15 — dedicated thread + own socket; state the topology in the plan | P0 |
| RK-5 | 🟡 | **Render blocks on a wedged writer's lock** — `render_metrics()` takes per-metric locks today | `checkpoint_metrics.py:79`/`:182`/`:304` **[VERIFIED]** | Scrape hangs instead of degrading | §1.3 contract (try-acquire + last-good snapshot) + wedged-writer test | P0 |
| RK-6 | 🟡 | **Per-request env read mis-modelled as costly** → a TTL cache gets added later | `os.environ` is dict-backed | Cache staleness defeats the emergency-off switch | A16 — state the measured cost; per-request read stays | P0 |
| RK-7 | 🟡 | **env-unset + managed-key-on yields an unexplained dark system** | plan §5/§6 semantics | Operator cannot tell "off" from "unset"; dark-by-mystery | §3 resolution table + boot log line + §6 row | P0 |
| RK-8 | 🟡 | **`ensemble_checkpoint_op_seconds{op="aput"}` can never fire** — write path bypasses `time_saver_op` | `persistence.py:509` (sole caller); `checkpoint_adapter.py:377-379` **[VERIFIED]** | Write-side stall invisible; declared `op` enum over-promises | A5 — extend the adapter's existing charge seam, or narrow the enum | P1b |
| RK-9 | 🟡 | **F20 counts node returns, not turns; turn-duration has no anchor** | 5 tap sites; `graph.py:10174` inside `agent_node` **[VERIFIED]**; `message_tap.py:302-306` | Mislabeled series; duration histogram measures DB-write latency | A4 — define the unit in HELP; specify the anchor or drop the family | P1c |
| RK-10 | 🟡 | **Cardinality cap is soft** — §7 forbids unbounded values; §5 allows unbounded growth with a warning | plan §5 vs §7 | §7's guarantee is unenforceable | A7 — hard cap + `ensemble_metrics_invalid_label_total` | P0 |
| RK-11 | 🟡 | **Vocabulary freeze pins enums before the code settles them** (thread groups) | catalog open item 3; §8.2 | Freeze marker misrepresents reality; churn during P1a | A17 — versioned amendment procedure; thread naming to P0 (A18) | P0 |
| RK-12 | 🟡 | **P0 exit criterion blocked by an external worktree** while the risk row says otherwise | plan §4 P0 exit vs risk 1 | Phase boundary ambiguous; P0 blocks on WS-A | A19 — split P0-core / P0-spine | P0 |
| RK-13 | 🟡 | **Census execution home unspecified** | plan §4 P1b T2; catalog §9 | Census could be scheduled on the event loop → dies in a wedge | A8(1) — dedicated daemon thread | P1a |
| RK-14 | 🟡 | **PG retention tiers understated** — plan states intervals, not windows (48 h/14 d/180 d) | `config.py:818-888` **[VERIFIED]** | 30 d+ questions answered from the wrong tier | A11 — state the ladder + interplay rule | P1b |
| RK-15 | 🟢 | **Alias removal leaves `absent()` for legacy consumers** | catalog §0.3 | A legacy dashboard silently blanks one release later | A20 — removal procedure with an `absent()` guard; migrate the 4 test files | P1b |
| RK-16 | 🟢 | **Prometheus storage loss is unrecoverable** (single local TSDB) | plan §2 | A host loss takes the alerting history with it | Document as a known limitation (deferred remote_write) | P2 |
| RK-17 | 🟢 | **Histogram observe is O(B)** and the plan extends buckets to 17+ | `checkpoint_metrics.py:220-222` | Observe cost scales with future bucket growth | A9 — non-cumulative buckets, accumulate at render | P0 |

---

## 10. LIST OF REQUIRED PLAN AMENDMENTS

Surgical, numbered, each mappable to a plan section. **No amendment reverses a ratified
decision.**

**A. Metric catalog — vocabularies and families**

1. **§8.6 / F20 / §1 / §7** — the tool-category vocabulary is **49**, not 37/38. Derive it at
   runtime from `get_tool_categories()` (`daemon/tools/_tool_registry.py:1598`); add the
   omitted values plus a reserved `other`; update the F20 series estimate and the §1/§7
   totals. Apply the same rule to every §8 vocabulary: **derived from code, never hand-listed.**
2. **F14** — change the observation site from `graph.py:3811-3812` to the terminal-usage
   branch `:3776-3784` (or the aggregated call site `:9176-9179`); mandate a per-call dedupe;
   add the "terminal-chunk only, never per-chunk" rule.
3. **§8.13 + m1** — split the wake-outcome producer into (a) the `worker_pool.py` claim path
   and (b) the `graph.py:7319` episode seam; each computed once, each carried.
4. **§8.13 + m1** — replace "the exposition renderer reads the carried value and increments"
   with "a live turn-end consumer increments the counter; the renderer only reads the
   accumulated counter".
5. **F20** — define the counting unit ("one `tap_node_return` invocation") on the HELP text;
   specify the anchor pair for `ensemble_turn_duration_seconds` or drop that family; specify
   the tool→category lookup with an `other` fallback.
6. **F7 + plan §1** — add a task to instrument the checkpoint write path at the existing
   `checkpoint_adapter.py` charge seam (`aput`/`aput_writes`); until it lands, narrow the
   declared `op` enum to the observable set and restate the write-side incident mapping.
7. **F7 / §0.4 / §8.11** — after the write-path seam lands, re-derive the checkpoint `op` enum
   from observed call sites rather than inheriting the legacy HELP text (`:281-290`).
8. **F0.2** — clarify that `ensemble_metrics_registry_measurements` counts **measurements**,
   not series, so dashboards cannot conflate it with the 521-series budget.

**B. Registry mechanics (catalog §0.2 / plan §3)**

9. **§0.2 + plan §3** — adopt the locking contract: per-metric locks (no global registry
   lock), **non-blocking render snapshot** (try-acquire → last-good published snapshot +
   `lock_contended` comment line), and **non-cumulative bucket counts** accumulated at render
   (O(1) observe).
10. **§0.5 + plan §7** — make the never-exported list executable: registration-time enum pin,
    observe-time reserved bucket + `ensemble_metrics_invalid_label_total{metric,label}`,
    render-time allowlist predicate, **hard** cardinality cap, name regex
    `^ensemble_[a-z0-9_]+$`, label-value charset gate; add the poisoned-registry
    defence-in-depth test.
11. **§0.3 / CR-3** — specify alias mechanics: dual exposition of one series, one-shot
    deprecation log, removal procedure with a Prometheus `absent()` guard, and migration of
    the four test files that reference the legacy names.

**C. Listener + kill-switch (plan §3/§4/§5/§6, catalog §0.7/§0.8)**

12. **§3 / catalog §0.7** — state the listener's thread/loop topology (dedicated thread,
    own socket, stdlib `http.server`) and the render-never-blocks rule; state the GIL split
    (I/O-bound wedge survives; **CPU-spin wedge does not** → CR-1's out-of-process witnesses
    remain mandatory).
13. **§4 P0 T1** — specify boot placement (inside lifespan, after `resource_monitor`
    `api.py:2241-2288` and `llm_stream_watchdog` `:2212-2226`), bind-failure degradation
    (mirror `api.py:2282-2287`), and teardown order (stop event → `shutdown()` →
    `join(timeout=5)`, `daemon=True` thread).
14. **§4 P0 T3 / §5 / catalog §0.8** — specify the truthy set (`{1,true,yes}`,
    case-insensitive; everything else OFF), and the layering rule: **env explicit-OFF always
    wins; env UNSET defers to the managed-key chain**; add the boot log line.
15. **§6** — extend the dark-families matrix with: env-unset-but-managed-key-on,
    `lock_contended`, CPU-spin wedge (→ external witnesses), and non-Linux host (`/proc`
    absent ⇒ F2 + F0.3 dark, listener still live).
16. **§4 P0 test plan** — add the four missing tests: origin-guard 403, bind-failure
    degradation, wedged-writer simulation, kill-switch typo parsing; specify the
    injectable-socket harness so no test requires a real port.

**D. Census (catalog §9, plan §4 P1b)**

17. **§9** — add: dedicated daemon-thread execution home; `statement_timeout = 5 s` /
    `connect_timeout = 3 s`; self-exclusion of `ensemble-metrics-census` **only**
    (wedge-capture rows counted under `other`); SQL-side `CASE`/`LIKE` matching in one round
    trip with six per-site unit tests; the sustained (5-min) `other`-growth signal; and an
    honest note that blank `pg_stat_activity.query` (extended protocol) lands in `other`.

**E. Storage split + durability (plan §3, catalog §10)**

18. **§3** — state the boundary as a rule: per-instance attribution = PG-only via
    `/api/resources/*`; every daemon-origin `/metrics` family is aggregate; **no
    daemon-origin family carries a per-instance label** (add to catalog §8 as a cardinality
    rule).
19. **§3 / §9 R5** — state the PG retention ladder (raw 48 h / minute 14 d / hour 180 d;
    `config.py:818-888`) and the interplay rule (Prometheus governs alerting and 30 d+;
    PG governs per-instance views; >90 d answered from the 180 d tier).
20. **§3** — add the two-sampler consistency rule (wedge sampler vs resource-monitor sampler
    read `/proc` independently; eventual consistency within one sample interval).
21. **catalog §10** — add the three-scenario restart-loss table and state that a Prometheus
    *process* restart preserves history (WAL replay) while *storage* loss does not.

**F. Phasing (plan §4)**

22. **§4 P0 exit criteria** — split into **P0-core** (listener, registry, kill-switch, census
    discipline, vocabularies, managed keys) and **P0-spine** (the 4-table gate, owned by the
    WS-A `rm-flusher-fix` worktree); resolve the contradiction with P0 risk 1.
23. **§4 P0** — add the worker-thread-naming task (`worker_pool.py:253`) so F2 is honest from
    first render; add the census execution-home task.
24. **§4 P0 exit criteria / F0.4** — add the versioned vocabulary-amendment procedure
    (bump `ensemble_metrics_vocabulary_version`); state that a vocabulary bump changing a
    literal label value in shipped PromQL is a breaking alert change.
25. **§4 P2 T1** — move the minimal textfile writer to **P1b** so BashSpin has a data source
    before the P2 alert rules land.
26. **§4 P2 T5** — state that alert authoring depends on the referenced families'
    completion (dashboards may be drafted earlier).

**G. Cross-cutting**

27. **catalog §0.2 / plan §3** — add the freeze rules: import the listener on the lifespan
    path (not `api.py` module top level); register aliases at registry-construction time so
    a cold start emits them.
28. **plan §4 P0 T4 / catalog §0.2** — restate the off-loop discipline as "no blocking work,
    no I/O, no logging, no per-chunk work on the event loop" + ≤1 clock read per window;
    state that metric increments inside `message_tap` must sit within the tap's existing
    `try/except`.

---

## 11. Open questions

Two items are genuinely the leader's call; my default recommendation is stated for each so
either answer is actionable.

1. **Checkpoint write-path instrumentation — how much write-path risk is acceptable?**
   Amendment A5 offers two shapes: extend the **existing** `checkpoint_adapter.py` charge
   seam to `aput`/`aput_writes` (small, but it touches the checkpoint **write** path — the
   path under stress during the very incidents this plan exists to diagnose), or narrow the
   family to the observable `aget` set and restate the write-side incident mapping in §1.
   *Default: instrument the adapter seam in P1b with its own risk row — the read path is
   where the incident actually lives (the plan's corrected §1 "Why"), but an unobservable
   write path is a hole an operator will trust wrongly.*
2. **Prometheus storage-loss exposure.** v2 assumes a single local Prometheus (30 d raw /
   90 d downsample). A host loss takes the entire alerting history with it, and no durable
   remote store is in scope.
   *Default: accept for v2 and record it as a known limitation (RK-16); remote_write is a
   separate project. Confirm that deferral is acceptable, since the deploy target (host
   binaries) makes host loss the dominant scenario.*

---

## Appendix A — Verification log (architect-executed)

| # | Claim | Command / source | Result |
|---|---|---|---|
| 1 | Registry uses per-object `threading.Lock`; no registry-level lock | `grep -n "Lock" daemon/checkpoint_metrics.py` → `:73/:79` (Counter), `:160/:182` (Histogram), `with self._lock` ×11 | **CONFIRMED** |
| 2 | `render_metrics()` reaches series through locking getters | `sed -n '295,320p'` → `.get()` on both singletons; `get()` takes the lock | **CONFIRMED** |
| 3 | `render_metrics()` has zero non-test callers | worker report: only `tests/unit/persistence/test_checkpoint_perf_logging.py:1063` | **CONFIRMED (reported)** |
| 4 | The two legacy singletons have a production consumer (dispatch brief said "zero non-test callers") | `daemon/checkpoint_perf.py:28-30` imports; observes `:190/:198`; increments `:233` | **CORRECTION — brief was wrong** |
| 5 | `read_proc_snapshot` degrades to `None` without `/proc` | `proc_readers.py:492` `-> Optional[ProcSnapshot]`; `return None` paths `:112-219` | **CONFIRMED** |
| 6 | F14's cited hook is the non-terminal attach path | `sed -n '3760,3800p'` → usage at `:3771-3774`, constructor attach + early return `:3778-3784`; `sed -n '3800,3820p'` → assign at `:3811-3812` | **CONFIRMED — F14 defect** |
| 7 | Tool-category vocabulary is incomplete | 254 `register_tool_category(` sites; ≥8 omitted values verified present (`board-link`, `dynamic-skill`, `ens-db`, `ens-env`, `image-gen`, `self-alarm`, `skill-evolution`, `system-log`) | **CONFIRMED — §8.6 falsified** |
| 8 | `get_tool_categories` exists as the derivation source | `daemon/tools/_tool_registry.py:1598` | **CONFIRMED** |
| 9 | `op="aput"` has no instrumented call site | `grep '"aput"'` → `checkpoint_adapter.py:378-379`, `secret_persistence_scrub.py:1188`; `time_saver_op(` callers → only `persistence.py:509` ("aget") | **CONFIRMED** |
| 10 | The checkpoint adapter already carries a charge seam to extend | `checkpoint_adapter.py:371-374` `_charge_checkpoint_read(args, kwargs, result)` | **CONFIRMED** |
| 11 | The tap fires at multiple sites, including inside `agent_node` | `grep tap_node_return` → `graph.py:7852`, `:9510`, `:10174`; `instance_messaging.py:1661`, `:5244` | **CONFIRMED — 5 sites** |
| 12 | PG retention tiers are 48 h / 14 d / 180 d (plan states only intervals) | `daemon/config.py:818-888` — `retention_raw_minutes=2880`, `retention_minute_hours=336`, `retention_hour_days=180` | **CONFIRMED — plan understates** |
| 13 | `checkpoint_metrics.py` is 355 lines with the cited class/function lines | worker report `:65/:111/:138/:151/:273/:281/:304`; plan's own citations match | **CONFIRMED (reported)** |
