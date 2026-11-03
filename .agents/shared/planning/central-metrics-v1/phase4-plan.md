# Phase 4: Extension-Subset Registration (W1 / W2 / W3 Clusters)

Status: pending · Tier **1** per cluster (additive wiring module; producers untouched — the gate doctrine's
mechanical list is not touched, so no escalation)
**FOLDED 2026-11-02:** architect v2 amendments folded — ids cited inline as `(AM-n)`; leader rulings as `(LR-n)`. **Tier unchanged**.
Feeds: architect → plan council → fresh approver → implementation

## Objective

Register the adjudicated **16 of 26** daemon-origin families (10 full + 6 partial) by **reading
already-computed producer values** — a pull adapter / scan adapter over existing state is in scope; a **new
increment or timing call** at a module site with no existing counter is OUT (AD3, strict).

**F2 is NOT in this subset** (AM-25 / leader LR-2): dropped by a **value** ruling, not by the mechanical
rule — prod is Darwin-primary, so its darkness is *Certain* and permanently-dark shipped code is standing
disclosure debt. Flip condition (verbatim): "if the ops surface becomes Linux-primary or the operator
confirms near-term per-thread CPU need, re-admit (+22, one pack)".

Three clusters, one per gate leg, so the frozen-vocabulary pack can re-derive **per cluster** (never
all-at-once):

| Cluster | Families (D5 classification) | Seam |
|---|---|---|
| **W1** | F0.3 spine health (IN) · F1 loop lag (IN) · F3 wedge counters (IN) · F4 pool native (IN) · F5 pool derived (IN) · F6 PG census (IN) — **F2 thread CPU: OUT of v1 (AM-25/LR-2)** | S3, S4, S6, S7, S8 |
| **W2** | F9 readiness (IN) · F10 queue depth (IN) · F12 sweeps (IN) · F11 worker pool (PARTIAL 16/30) · F13 instances (PARTIAL 19/29) · F7 checkpoint ops (IN, wiring only — +10) · F8 store sizes (PARTIAL 4/9) | S9, S10, S11, S12, S13, S17, `maintenance.py:531-536` |
| **W3** | F17 MCP health (IN) · F18 gateway (PARTIAL — sessions + tombstones + amended audit vocabulary) · F21 journal twin (PARTIAL — 3 read gauges) | S14, S15, S16 |

## Tasks

| # | Task | Depends On | Acceptance | Traceability |
|---|---|---|---|---|
| 4.1 | Create the **wiring module** (e.g. `daemon/services/metrics_query/wiring/`) with a pull-adapter registry that the Phase-2 sampler's stage A calls each tick. Adapters are **readers**: they read an existing producer's already-computed value and write it into the registry. Each registration **must declare `allowed_values` for every label key** (AM-2 — a key without `allowed_values` is char-checked only and can reach the per-metric cap with raw values) and **must pass the family metadata kwargs** (`family`, `source_idiom`, `durability`, `cardinality_weight`, `chart_role` — AM-3) so the new family is discoverable at `/families` with **no door edit**. The registry ships with this phase's families only. | Phase 2 (2.6 stage A, 2.7b) | The wiring module registers no new observe call at any producer site; a diff review confirms zero producer-module edits (except the F7 bytes call site — an existing observe gaining a real value — and **no other producer-module edit**: the C17 docstring fix is **DEFERRED** per council W5, so **no mechanical-list file is touched** by this lane); every new family declares `allowed_values` for every label key and carries its family metadata — including the **`chart_role`** value from the **chart-primary roster** (task 2.7b): a roster family registers with its roster role, a non-roster family with the derived default | FR-B1, FR-B4 · AD3 · D5 rule-3 · **AM-2, AM-3** · AC-B.1, AC-B.4 |
| 4.2 | **DB-pull discipline (AM-11 — pattern CORRECTED)**: every DB-backed adapter (F10 queue counts, F13 instance counts, F18 audit counts) opens a **one-shot direct `psycopg.connect` per tick** — connect_timeout + `application_name` + statement_timeout — and **closes it inside the tick** (the census precedent verbatim, `metrics_census.py:226-240`, `:418-432`). A **persistent per-adapter connection is FORBIDDEN** (pool-in-disguise; it recreates the idle-in-transaction class cured in the DB-pool lane). The reads happen on the **sampler thread** — never on the request path, never on the event loop, never through the shared pool. | 4.1 | A pin asserts the wiring module opens no pooled session on the request path and holds no persistent per-adapter connection; each adapter's connection is closed before the tick ends | FR-A6, NFR-1 · **AM-11** · P-R1 (plan risk) · seams S10, S13, S15 · census discipline (catalog §9) |
| 4.3 | **W1 cluster**: register F0.3 (spine counters, `resource_monitor/flusher.py:161` + `sampler.py`), F1 (lag quantiles — gauges, **not** a histogram, per MA-4), F3 (wedge capture counters), F4/F5 (pool native vs derived kept apart), F6 (census gauges over the frozen `CensusSnapshot`) — **F2 is NOT in this cluster** (dropped by a VALUE ruling, not by rule: AM-25 / LR-2; re-admit +22, one pack, if "the ops surface becomes Linux-primary or the operator confirms near-term per-thread CPU need"). | 4.1, 4.2 | Each family renders in **both** surfaces (`/metrics` text + the query door) with the catalog's canonical names, labels and buckets; every label key declares `allowed_values` (AM-2); each family carries its registration-time metadata (AM-3); **F2 is absent from v1** (AM-25/LR-2) | FR-B1, FR-B5 · D5 (F0.3/F1/F3/F4/F5/F6 rows) · **AM-2, AM-3, AM-25** · seams S3–S8 · AC-B.1, AC-B.5 |
| 4.4 | **WS-A ancestry check for F0.3 (AM-24 — rc semantics)** — the catalog's F0.3 exit gate BLOCKS unless the resource-monitor flusher fix (`feature/resource-monitor-flusher-fix@53547b915`) is in the lane's ancestry. Verify by **ancestry** (`git merge-base --is-ancestor`), not by memory. **rc semantics:** `rc=0` ⇒ PASS ⇒ F0.3 ships (+8 series) and its family pack runs; **`rc=1` ⇒ defer + disclose** (F0.3 leaves the cluster, the lane's series delta drops by 8, and the deferral is disclosed in the evidence pack + the stage disclosure); **`rc` other than 0/1 ⇒ STOP** (the lane halts and reports — an unexpected rc is an unknown state, not a verdict). The **F0.3 family pack is conditional on the PASS verdict** (it is not collected, and not reported as a failure, when the verdict is defer). | 4.3 | The ancestry verdict (rc + the chosen branch) is recorded in the evidence pack; F0.3 is either registered with a green pack or explicitly deferred (never gated open); an unexpected rc halts the lane | FR-B5 · D5 F0.3 row · constraint C-5 · **AM-24** · risk P-R8 |
| 4.5 | **W2 cluster**: register F9 (readiness — read the **published** composite, never re-probe), F10 (queue depth gauges over `job_queue_items.admission_state`), F12 (sweep cycles/errors/rows — **accumulate** the per-tick `counters()` values in the metrics layer; the producers stay untouched), F11-partial (workers/busy/wakeup-efficiency/events + heartbeat age/cadence), F13-partial (status/liveness/waiting-children gauges), F7-completion (pass the **real** byte count at the existing call site `persistence.py:632` — an existing observe gains a real value, no new observe point), F8-partial (blobs + per-table byte gauges). Every registration declares `allowed_values` for every label key (AM-2) and carries its family metadata (AM-3). | 4.1, 4.2 | Every family renders with the catalog's canonical names; F12 accumulates across ticks (a multi-tick test asserts the counter grows); the F7 bytes gauge is non-zero after a real read; the OUT metrics of each PARTIAL family are **absent** | FR-B1, FR-B3 · D5 (F7/F8/F9/F10/F11/F12/F13 rows) · **AM-2, AM-3** · seams S9–S13, S17 · AC-B.1, AC-B.3 |
| 4.6 | **F13 waiting-children verification** — the requirements assumed the durable query exists; confirm it at implementation and, if it does not, move those two gauges to the OUT list with the discrepancy recorded (never guess). | 4.5 | Either the gauges render from an existing durable query, or the family is re-adjudicated OUT with the evidence | FR-B2 · requirements gap #5 · D5 F13 row · AC-B.2 |
| 4.7 | **W3 cluster**: register F17 (MCP health — read the snapshot, never re-probe), F18-partial (gateway sessions + tombstones gauges + `ensemble_mcps_gateway_audit_total{outcome}` under the **amended** vocabulary `{ok, error, timeout}` — `timeout` is never folded into `error`), F21-partial (the three **read** gauges: journal file bytes, history entries, current release version). Every registration declares `allowed_values` for every label key (AM-2) and carries its family metadata (AM-3). | 4.1, 4.2 | The F18 audit gauge exposes exactly 3 outcome series; `timeout` appears as its own label; `denied` (a declared-but-never-written constant) is **not** in the vocabulary; the F21 gauges render | FR-B1, FR-B3, FR-F7 · AD4(a) · D5 (F17/F18/F21 rows) · plan-overview flags D-5, D-6 · **AM-2, AM-3** · seams S14–S16 |
| 4.8 | **F21 conditionality verdict** (AD4(b) / D-4) — record the plan-time evidence that `journal_history_append` writes `{ts, event, detail}` where `event` is the **alert-class** kind set (`upgrade_journal.py:645-649`) and that the promote **phase** trail lives in journal extension fields, not in `history[]` ⇒ **no clean pure-read derivation** ⇒ the F21 phase families stay **OUT** (+30 series not claimed). Add a pin asserting the phase families are **absent** from the registry. | 4.7 | The evidence is filed; the phase families are absent; the OUT-list in the docs names the derivation that would be needed | AD4(b) · D5 F21 row · flag D-4 · risk P-R13 |
| 4.9 | **DEFERRED OUT OF v1** (plan council W5, dispatcher adjudication) — the **C17 docstring correction** (`job_queue_service.py:828-836` says the counter is cumulative while the code returns a per-tick value, `:837`) is **recorded, not shipped** in this lane. Rationale: `daemon/services/job_queue_service.py` is **ON the mechanical Tier-2 list** (ensure.md §Gate Tiers), so a one-line comment edit would mechanically force a **whole-tree dual-side Tier-2 leg** — disproportionate for a comment, and doctrine says mechanical is never a judgment call. The **F12 adapter accumulates the per-tick counters correctly regardless of the docstring** (the disambiguation lives in the metrics layer, not in the comment), so deferring costs no correctness. The fix is recorded in the **fold ledger** (`fold-ledger.md`) + the deferred list, to be picked up by a lane that legitimately edits that file. | 4.5 | The deferral is recorded in the fold ledger + the deferred list with the rationale; **no lane edit touches any mechanical-list file**; the F12 accumulation is proven correct by the multi-tick growth test (independent of the docstring) | FR-B4 (no instrumentation) · D5 F12 row · research §1c · **plan council W5** |
| 4.10 | **Adjudication + budget pack** per cluster: assert (a) every catalog family F0.1–F22 has exactly one verdict (IN / OUT / PARTIAL / ALREADY-LIVE) with a seam anchor, (b) every **OUT** metric is absent from the registry **and** from the `/metrics` body — including **F2** and the F21 phase families, (c) the measured label-space series count per family stays inside its declared band, and (d) the cumulative post-lane count stays within the pinned budget (**+274 → 363 ≈18.2 %** on the F7c-inclusive basis; 361 on the ledger-rows basis; 355 if WS-A defers F0.3). | 4.3, 4.5, 4.7 | The pack is green; any measurement outside its band stops the lane and reports (no widening) | FR-B2, FR-B3, FR-C6, NFR-5 · AD3 · **AM-25** · AC-B.2, AC-B.3 · plan-overview *Re-derived budget* · risk P-R7 |

## Gate legs

| Leg | Tier | Scope | D1 (lane-owned, HEAD-only) | D2 (dual-side, comparable) | Expected D1 / D2 nodes |
|---|---|---|---|---|---|
| **P4-G1** (W1) | **1** | `daemon/services/metrics_query/wiring/**` (+ `metrics_census.py` **additive registration only**) | `metrics_family_f0_3` (**conditional on the WS-A PASS verdict** — AM-24) · `metrics_family_f1` · `metrics_family_f3` · `metrics_family_f4_f5` · `metrics_family_f6` · `metrics_family_adjudication_w1` — **the F2 pack is GONE (AM-25/LR-2)** | **REGISTERED PACK `metrics_p4_w1_dual_side`** — composes: vocabulary (18) + registry (45) + the changed family's own base pack (e.g. `test_metrics_census` 34) | ~24 per cluster (PROJECTION) |
| **P4-G2** (W2) | **1** | wiring module + `persistence.py` (existing call site gains the real byte value) — **no mechanical-list file is edited** (the C17 docstring fix is DEFERRED per council W5) | `metrics_family_f9` · `metrics_family_f10` · `metrics_family_f12` · `metrics_family_f11_partial` · `metrics_family_f13_partial` · `metrics_family_f7_completion` · `metrics_family_f8_partial` · `metrics_family_adjudication_w2` | **REGISTERED PACK `metrics_p4_w2_dual_side`** — composes: vocabulary (18) + registry (45) + the changed family's own base pack | ~30 / ~63–97 (PROJECTION) |
| **P4-G3** (W3) | **1** | wiring module | `metrics_family_f17` · `metrics_family_f18_partial` · `metrics_family_f21_partial` · `metrics_family_adjudication_w3` | **REGISTERED PACK `metrics_p4_w3_dual_side`** — composes: vocabulary (18) + registry (45) + the family's own base pack | ~14 / ~63–97 (PROJECTION) |

**Per-cluster rule:** the frozen-vocabulary pack re-derives **per cluster** — never all-at-once — so a
vocabulary drift names the cluster that caused it.

**Mutation→test table (stub):**

| Label | Mutation | Must go RED |
|---|---|---|
| `P4-M1` | an OUT metric registered (e.g. F11 claim latency) | `metrics_family_adjudication_*` |
| `P4-M2` | a new increment added at a producer site | the diff-review pin + the adjudication pack |
| `P4-M3` | F12 accumulation removed (per-tick values published raw) | `metrics_family_f12` |
| `P4-M4` | F4 native and F5 derived collapsed into one metric | `metrics_family_f4_f5` |
| `P4-M5` | F6 rendered on a non-postgres runtime | `metrics_family_f6` |
| `P4-M6` | F18 `timeout` folded into `error` | `metrics_family_f18_partial` |
| `P4-M7` | F21 phase families registered | the F21 absence pin |
| `P4-M8` | the F7 bytes call site reverted to 0 | `metrics_family_f7_completion` |
| `P4-M9` | a label key registered **without** `allowed_values` (fold protection becomes opt-in) | `metrics_family_adjudication_*` (AM-2) |
| `P4-M10` | a family registered without its metadata kwargs (the door cannot describe it) | `metrics_family_adjudication_*` (AM-3) |
| `P4-M11` | a **persistent** per-adapter DB connection introduced (idle-in-transaction class) | the DB-pull discipline pin (AM-11) — rides the registered pack `metrics_family_adjudication_w2` (approver N3; **no pack-set expansion**) |
| `P4-M12` | F2 registered (the value-ruling drop inverted) | `metrics_family_adjudication_w1` (AM-25) |
| `P4-M13` | the WS-A ancestry check inverted (rc=1 treated as PASS) | the F0.3 conditional pack (AM-24) |

**Slicing proof:** `--collect-only -q` proving each cluster's selected set equals its declared pack scope.

**Pre-exclusion registry:** `regression_performance` (>300 s) · e2e needing live `:8079` (→ Release Gate) ·
`tests/packs/g7` (packs only).

**Invocations (W9/W10):** the D2 sets are the **registered packs `metrics_p4_w1_dual_side` /
`metrics_p4_w2_dual_side` / `metrics_p4_w3_dual_side`** (registered in `.agents/tester/PACKS.md`, invoked by
pack id — never an ad-hoc pytest list). **Mandatory recipe load:**
`.agents/tester/skills/blast-radius-leg/blast-radius-leg.md` (Tier-1 scoped dual-side) **plus**
`.agents/tester/skills/failed-digest-equality-gate/failed-digest-equality-gate.md` for the FAILED-digest
comparison. Node counts are PROJECTIONS, re-derived via `--collect-only -q`.

**Double-scrape discipline (P-R6):** every assertion about a *just-written* gauge uses the house
copy-on-publish pattern (`set_publisher_cadence_provider(lambda: 0.0)` + a **second** scrape) — the
registry publishes at most once per cadence window, so a single scrape after a write can read the previous
copy. This applies to every family pack that asserts a value the adapter just wrote.

## Coupling

- **Tight with Phase 2** — the adapters plug into the sampler's stage A; the ring therefore carries the
  new families' history from the same tick onward.
- **Loose with Phase 3** — the routes do not change; only the availability facts and the family list become
  richer (the contract already carries the reason set).
- **Tight with Phase 6** — the ledger delta comes from this phase's measurements.
- **Loose with Phase 5** — the FE consumes the same door; the family list and the chart-primary roster (task 2.7b) feed its model and its Charts tab.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| An adapter turns into instrumentation (a new increment sneaks in at a producer site) | Rule-3 breach; scope creep | 4.1's diff-review requirement + `P4-M2`'s pin + the adjudication pack's OUT-absence assertions. |
| A DB-backed adapter runs on the request path | The 2026-10-26 incident class | 4.2's discipline (dedicated conn, sampler thread, statement timeout) + a pin. |
| The F12 accumulation mis-reads a per-tick counter as cumulative | Wrong series | `P4-M3`'s mutation + a multi-tick growth test. The **disambiguator is the metrics layer's own accumulation** (it diffs successive `counters()` reads), not the producer's docstring — so the **deferred** C17 comment fix (council W5) costs no correctness here. |
| WS-A is absent ⇒ F0.3's producer is dark | Gate block / dead family | 4.4's ancestry check with an explicit deferral path (−8 series disclosed). |
| F2 would render zeros on the Darwin production host | Misread as "the daemon is idle" | **Avoided — F2 is dropped from v1** (AM-25 / LR-2), so no `proc_unavailable` reason is expected in v1; the dark-with-reason infrastructure still ships for F6/F0.3, and the flip condition is recorded verbatim. |
| The vocabulary pack drifts after a cluster | Frozen-contract break | Per-cluster re-derivation; the vocabulary pack is the authority (never hand-listed values). |
| A cluster's measured series count exceeds its declared band | Budget drift | 4.10's band assertion; the lane **stops and reports** rather than widening. |

## Exit criterion

**Sixteen** families are registered at the canonical seam with the catalog's canonical names, labels and
buckets, every label key declaring `allowed_values` (AM-2) and every family carrying its registration-time
metadata (AM-3); every OUT metric — including **F2** and the F21 phase families — is absent from the
registry and from the `/metrics` body; the F21 conditionality evidence is filed; the F0.3 WS-A ancestry
verdict is recorded with its rc and branch; the C17 docstring fix is **deferred and recorded** (council W5 — no mechanical-list file is edited by this lane); the measured post-lane series
count is inside the pinned band (**+274 → 363 ≈18.2 %**); and P4-G1/G2/G3 ran as three **Tier-1** legs, each
with a declared D1/D2 split, a slicing proof, the double-scrape discipline applied and 13/13 mutation legs
RED→GREEN.
