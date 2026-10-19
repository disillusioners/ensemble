# Approach Comparison — resource-cost-control

Supporting artifact for `architecture-recommendation.md`. Records the competitive fan-out comparison across the **five fixed axes** (Complexity · Scalability · Maintainability · Risk · Cost) and the adjudicated council divergences.

Method: 1 council (2/2 councilors, `agentic` + `coding`, uniform skill `data-flow-design`) + 3 skill-equipped workers. Where the council and a worker disagreed, the architect adjudicated on verified source evidence.

---

## 1. Per-instance attribution — approach fan-out

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **A — in-process charge seams only** (charge message/checkpoint sizes at seams) | Low | Good | High | **High** — counter drift, no ground truth, no correction | Low | ❌ rejected alone |
| **B — cgroup/subtree + pid registry only** (external-style per-pid reads) | Low | Good | Med | High — blind to in-process agent RAM (the dominant holder) | Low | ❌ rejected alone |
| **C — HYBRID: charge seams + per-pid exact + 1/tick deep-scan reconcile, with explicit residual** | Med | Good | High | **Med** — bounded drift, corrected; residual *published* | Med | ✅ **ADOPTED** |
| **D — always-on `tracemalloc`/per-object tracking** | High | Poor | Low | High — hot-path cost, violates self-cost budget | High | ❌ rejected (Constraint B) |

**Why C wins:** the daemon is one shared process, so A can't self-correct and B can't see the dominant in-process holder. C is the only approach that is both *grounded* (per-pid exact for children, deep-scan reconcile for in-process) and *honest* (`unattributed_kb`, `estimated`, `coverage_pct`). D is disqualified outright by the self-cost budget.

**Dominant axis:** Risk — C is the only option whose error is bounded *and* disclosed.

---

## 2. Sampler thread model

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **asyncio task on the event loop** | Low | Good | Med | **High** — goes silent during exactly the loop stalls it must detect; injects IO latency into LLM streaming | Low | ❌ rejected |
| **`threading.Thread(daemon=True)` + one flusher thread** | Low | Good | High | Low — samples *through* loop stalls; boot failure degrades subsystem only | Low | ✅ **ADOPTED** |

**Dominant axis:** Risk/observability validity — an asyncio sampler is blind precisely when the anomaly occurs.

---

## 3. Storage / write path

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **Ring-buffer only (RAM)** | Low | Good | Low | High — no durability, no history | Low | ❌ |
| **Unbatched per-sample DB writes** | Low | **Poor** — transaction storm | Med | High | Med | ❌ violates Constraint B |
| **Bounded RAM ring (primary) + flusher thread batched `executemany` (durability)** | Med | Good | High | Low | Low | ✅ **ADOPTED** |

**Dominant axis:** Cost/Scalability — batching is required to stay inside the write budget.

---

## 4. Roll-down + retention

| Position | raw | minute | hour | Steady state @N=50 | Ruling |
|---|---|---|---|---|---|
| **agentic (council)** | 48 h | 14 d | 180 d | ~185–390 MB | ✅ **ADOPTED** — forensics fidelity; the 8.3 GB event was "yesterday's" |
| **coding (council)** | 24 h | 7 d | 90 d | ~130 MB | Retained as the tunable **floor** |

**Divergence class:** tuning, not structural. Adjudicated to the wider window; both are env-tunable.

---

## 5. Anomaly placement

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **Query-time evaluation on every read** | Low | Poor — repeated compute per request | Med | Med | Med | ❌ |
| **In-daemon background pass → append-only findings** | Med | Good | High | Low — recomputable on restart | Low | ✅ **ADOPTED** |
| **Black-box model score** | High | Med | Low | High — unexplainable | High | ❌ rejected (R3 requires evidence) |

**Dominant axis:** Maintainability/explainability — findings must carry `{baseline, observed, deviation, contributors}`.

---

## 6. Control seam

| Approach | Rework later | v1 risk | Verdict |
|---|---|---|---|
| Implement kill/throttle now | n/a | 🔴 high — live enforcement without policy | ❌ |
| **Seam + dormant `resource_limit_policy` table (`user_gated=true`, `enabled=false`), observe-only** | **Zero** — enforcement is a new port behind the same schema | 🟢 none | ✅ **ADOPTED** |
| No seam at all | 🔴 schema rework later | Low | ❌ |

---

## 7. Council divergences — adjudicated

| Divergence | coding | agentic | Ruling |
|---|---|---|---|
| Migration granularity | 4 per-table `.sql` | 1 family file (secrets precedent) | **4 per-table files, one timestamp** — satisfies the explicit project rule ("every new SQLModel table needs its OWN `.sql`") and the blueprint; family-file remains a requester preference (flagged as open Q2). |
| Retention defaults | 24h / 7d / 90d | 48h / 14d / 180d | **agentic's** (forensics fidelity); coding's as tunable floor. |
| Table naming | `resource_sample_*` / `subprocess_attribution` | `resource_*` / `resource_pid_map` | **agentic's** (pid table carries the enforcement-audit columns); merged in coding's `command` / `parent_pid` / `exit_code`. |
| Cardinality cap | "10 K rows or N %" (vague) | ≤64 rows/tick + `__overflow__` sentinel | **agentic's** (concrete, tunable). |

Council converged on every load-bearing structural point in Round 0 — no refinement rounds needed.

---

## 8. Optimization candidates — weighted matrix (W1, `trade-off-analysis`)

| Candidate | Complexity (20%) | Scalability (20%) | Maintainability (25%) | Risk (20%) | Cost (15%) | Weighted | Verdict |
|---|---|---|---|---|---|---|---|
| O3 instance-cache TTL knob | 5 | 4 | 4 | 4 | 5 | **4.40** | SHIP |
| O1 `MALLOC_ARENA_MAX=2` (+trim) | 5 | 4 | 4 | 5 | 5 | **4.55** | SHIP |
| O4 backoff-reset fix (+lock reap) | 4 | 5 | 4 | 4 | 4 | **4.20** | SHIP |
| O7 test-DB cleanup | 5 | 3 | 4 | 5 | 5 | **4.40** | SHIP |
| O2 chromium semaphore | 3 | 5 | 3 | 3 | 3 | **3.40** | NEEDS-CEREMONY |
| O5 bash registry unregister | 4 | 2 | 3 | 3 | 4 | **3.05** | DEFER |
| O6 streaming projection | 2 | 3 | 2 | 1 | 2 | **2.00** | DEFER |

**Recommendation:** ship **O1 + O3 + O4 + O7(-test-DB)** config-line/knob-only on `latest` (live effect at next promote); stage **O2** separately with a knob + `render_e2e` re-certification; defer **O5 + O6**.

**The recommendation flips if** O1's real RSS reduction is < 5 % (i.e. the flat 1.7 GB is dominated by constant-state dynamic graphs, not arena retention).

---

## 9. Refuted leader recon (corrected — verify before fixing)

| Leader recon claim | Status | Correct anchor |
|---|---|---|
| O4 fix site `daemon/sources/registry.py:705-718` | 🔴 **REFUTED** — that region is `def stop_adapter` at `daemon/sources/registry.py:621` (adapter eviction, unrelated) | start-coupled **unconditional** reset at `daemon/sources/registry.py:781-782`: `last_success_time = time.monotonic()` + `backoff = 2.0` execute on EVERY successful `start()` (before the health-check inner loop) ⇒ start-then-immediate-death still zeroes backoff ⇒ sustained-outage backoff pinned at ~2–5 s ⇒ plane/computer-use 60 s retry storm. `daemon/sources/registry.py:839-846` is the verified-correct conditional CRASH-arm guard (`if last_success_time > 0: elapsed = monotonic() - last_success_time; if elapsed >= success_threshold: backoff = 2.0`) — its CONTENT is right but its ROLE in the bug is **masked**, because `:781-782` already zeroed backoff and stamped `last_success_time = now` (so `elapsed` is always < 60 s and the threshold never fires). `_handle_message` (def `daemon/sources/registry.py:858`) contains ZERO `last_success_time` references — the "message-coupled reset" model is the mis-modelling. **Fix site: `daemon/sources/registry.py:781-782`** — gate the start-success reset on the crashed run having actually lived ≥ `success_threshold` (60 s) so start-then-immediate-death keeps the accumulated backoff; `stop_adapter` (`:621`) UNTOUCHED (regression-pinned). |
| O6 `instance_messaging.py:4888` = unbounded all-history live objects | 🔴 **REFUTED at that line** — it is an `astream` loop inside `_llm_semaphore`; `_graph_tasks[iid]` (`:4883`) is a single assignment | needs `tracemalloc` measurement before any fix |
| `manager.py:534` instances cache | ✅ CONFIRMED | `dict[str, tuple[CompiledStateGraph, str]]` (the `str` = `agent_dir`) |
| `task` naive-UTC vs `event` aware-UTC | ✅ CONFIRMED | `task/models.py:~270-275`; `event/models.py:~80` |
| bash registry success-path leak | ✅ CONFIRMED | register `:413`; timeout-arm unregister `:427`; cancellation-arm unregister `:483`; success path `:429-458` — **NO unregister** (re-verified at HEAD `a023a7ddb`; matches arch §3 O5 + XC3/XC5) |
| chart render uncapped | ✅ CONFIRMED | per-charter-id guard only (`chart_tools.py:24-28`); `fresh=True` parallel |
