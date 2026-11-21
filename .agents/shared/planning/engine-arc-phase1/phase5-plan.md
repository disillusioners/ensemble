# Phase 5 — Event registry v1: kind catalog, structured emission, per-kind observability (registration + observability ONLY)

**Depends on:** P1 (`TriggerSpec` / `engine_jobs.event_kind` / `EngineConfig`).
**Parallel with:** P2, P3, P4 (disjoint tree: `daemon/services/event_registry/`; the only shared-file edits are four ≤5-line bridge calls in `child_reports.py` / `error_reporting.py` / the RDRS or its repository seam / the attestation ledger's callers — none of which any other phase touches).
**Feeds:** P6 (kind metadata is part of the shape-ready projection), P7.

---

## 1. Objective

Deliver ruling #3 literally: **a first-class registry of event KINDS** — every emission site registers its kind centrally, and the registry answers *what fired, how often, who consumes it, how far behind a consumer is* — with **no fan-out unification** and **no queue-depth triggers**.

Verbatim (Emperor, Discord 2026-11-21): "**For no 3: event register is need. Why: it help on central loging, debug management, metric...**"

**Phase-1 boundary is a REGISTRATION + OBSERVABILITY layer only.** Kinds are declared; emission sites are *bridged* (an existing emitter calls the registry with a structured envelope); a per-kind metric-shaped projection exists. **Nothing consumes a kind to trigger work** — that is the engine's Event trigger (already specified in P1 `EventTrigger`; the engine dispatches on it, but Phase 1 registers no such job).

---

## 2. Scope

**In:** `daemon/services/event_registry/{__init__,catalog,emit,metrics}.py`; the bridging of **class 1-4** kinds over substrates that EXIST today (see §3.1); the closed per-class failure posture; the per-kind observability projection (kind → firing count, consumer count, lag where measurable); the self-describing kind metadata (addendum parity with JobSpec). **No repository module and no migration** (D5.2/D5.3): the registry's durable substrate IS the existing `event` table + the state tables it observes.

**Out:** fan-out envelope unification (GAP-15 — six incompatible primitives: EventBus / DispatchEventBus / LiveEventHub / Broadcaster / dependency_bus / lifecycle_hooks); queue-depth triggers (GAP-14 — no plumbing exists; the seam is named, not built); `turn_transitions` → EventBus planting (class 5, **deferred to v2 by split ruling** — the coding councilor's position; the hottest path in the daemon); **any** consumer that acts on a kind (that is a later phase sized from what Phase 1 reveals); per-producer kill switches (GAP-16 — named, not built).

---

## 3. Decisions

### 3.1 The v1 catalog — classes 1-4 only (§B.3), over substrates that EXIST at this tree

| Class | Kind(s) | Substrate (verified anchor) | Failure posture |
|---|---|---|---|
| **1. Terminalization** | `instance_terminalized` (child-keyed, outcome-tagged) | `DependencyBus.fire_for_terminal_child` `daemon/services/dependency_bus.py:1921` — durable row + guarded CAS `transition_state`; wired at `daemon/services/child_reports.py:6444 / :6643 / :6945` (all `await self._fire_terminal_child_and_deliver(`, wrapper def `:741`) and `daemon/services/error_reporting.py:693` | **FAIL-MISS** — a missed terminalization strands a waiter (the S1 defect class, now closed at source) |
| **2. Durable bus events** | `event_bus:<EventKind>` (one registry kind per EventKind emission, kind-parameterized) | `EventBus.create_event` `daemon/services/event_bus.py:155` (durable row + global subscribers via `subscribe_all` `:286`; `JobFeedbackObserver` proves the pattern — constructed `daemon/api.py:2958-2965`, started `:2966`) | **FAIL-MISS for the durable kinds, drop-counted for the in-proc fan-out** — the DB row is the truth; a subscriber queue overflow is a counted drop (the queue has `maxsize`, `event_bus.py:251/:289-303`) |
| **3. State-table triggers** | `report_injection_obligation` (PENDING/DEFERRED rows), `mission_commission_transition` | `report_injections` (`daemon/repositories/report_injection/models.py:124`, states at `:83-90`; partial unique index `uq_report_injections_oblig_triple` `:235`) and `mission_commissions` (`daemon/repositories/mission_record/models.py:372`, migration `20261025_122700_create_mission_commissions.sql`) | **FAIL-MISS** — a durable obligation that nobody observes IS the RDRS failure class |
| **4. Attestation epochs** | `attestation_epoch` (ledger increment / reset / escalation) | `AttestationLedger` Protocol `daemon/services/attestation_ledger.py:101-131` (`increment` / `reset` / `set_escalated_and_reset`), already **fail-open** (`_log_db_error` at `:137+`, "C3 fail-open structured error log") | **fail-OPEN** — the ledger's own posture; the registry must LABEL it fail-open, never silently inherit it |
| *(5)* | `turn_transition` | dead outbox: consumers still DEBUG-only at HEAD — `daemon/services/instance_lifecycle.py:5976` and `:6589` (GAP-13, verified intact) | **NOT v1** (split ruling) — recorded in the catalog as `deferred_v2` with the anchor, so the next phase starts from a named position |

### 3.2 Decisions table

| ID | Decision | Rationale (anchor) | Rejected |
|---|---|---|---|
| D5.1 | The registry is a **code-level catalog** (a frozen `KindSpec` table in `catalog.py`) **plus a durable projection** (`event_registry_kinds` is NOT created — see D5.2). The catalog is import-time data | The addendum requires "each service bring it info/meta when register" — declarative data, not a build step. A code catalog is reviewable in a diff, greppable, and impossible to drift from the emitter | a DB-registered kind table (adds a write path for a value that only changes with a code change) |
| D5.2 | **No new table for kinds.** The durable projections are (a) per-kind counters in the existing in-proc metrics registry (see F-1 constraint) or the engine-local snapshot (D5.5), and (b) the `event` row itself, which already carries `kind` | The `event` table already persists every durable kind (`daemon/repositories/event/models.py:88+`, `Event.kind: str`). Adding a kinds table would be a second truth for the same fact | a `event_registry_*` table pair (schema churn for zero new information) |
| D5.3 | **One migration file only if a column is genuinely needed.** Decision: **none needed**. The registry's per-kind dimensions are derivable from `event.kind` + the code catalog | 134 `.sql` files at this tree; the house rule is "every new TABLE gets its own file" — there is no new table. **Zero migrations in P5.** | adding an `event_registry_consumers` table (see D5.4) |
| D5.4 | Consumer visibility is **declared in the catalog** (`KindSpec.consumers: tuple[str, ...]` naming subscriber ids / lanes) and **verified at boot** by cross-checking the live subscriber sets (`EventBus._global_subscribers` keys, the dependency bus's lane names) | The ruling asks what helps "debug management" — the question an operator actually asks is "who is listening to this kind?" A declared list that is verified against the live subscriber set answers it and detects a dead consumer (a listener that stopped subscribing) | inferring consumers by inspection at read time (cannot distinguish "no consumer" from "registry could not see it") |
| D5.5 | **Observability projection is engine-local first.** `event_registry.metrics.snapshot()` returns per-kind `{fired_total, last_fired_at, consumers: [...], consumers_live: [...], lag_seconds (where measurable), posture: FAIL_MISS | FAIL_OPEN | DROP_COUNTED}` | Fence **F-1** (the metrics family census is closed at 28 verdicts / 18 registered — `tests/unit/metrics/test_metrics_family_adjudication.py:415-453` **[TREE-TRUTH RECONCILE — 2026-11-21 (P6 review #3): 28 → 29 verdicts / 18 → 19 registered (verified at tree 2f36e8aed94c); planning-time figures predate the landed family extension (F24)]**) and fence **F-2** (`job_metrics()`'s block shape pinned at `tests/unit/test_maintenance_idle_starvation.py:800-804`). Publishing `ensemble_event_registry_*` metrics is a separate census ceremony (P6 T6.3) | registering a new metrics family here (REDs two pins and hijacks a separate lane's ceremony) |
| D5.6 | **Bridging, not rewriting.** Each emission site gets ONE call to `event_registry.record(kind, **dims)` adjacent to its existing emitter — the existing emitter stays the delivery mechanism | The commission forbids refactoring existing mechanisms. Bridging is additive and revertible per site; each site's diff is ≤ 5 lines | replacing `fire_for_terminal_child` with a registry dispatch (a rewrite of the exact primitive that was just fixed at v0.31.22) |
| D5.7 | The bridge is **non-blocking and never raises into the emitter**: a bounded in-proc ring + a periodic flush, `record()` returns immediately; a full ring drops and counts `record_dropped_total` | A telemetry bridge that can block the terminalization path would be strictly worse than no telemetry. Precedent: the metrics layer's accumulation discipline (`daemon/services/metrics_query/wiring/accumulate.py`) and the sampler's "a raising adapter never kills the tick" (`daemon/services/metrics_query/sampler.py`) | an awaited DB write at every emission site (adds a DB round-trip to the hottest paths) |
| D5.8 | The per-class **failure posture is LABELED in the catalog**, never inherited implicitly: `FAIL_MISS` (terminalization, state-table) / `FAIL_OPEN` (attestation) / `DROP_COUNTED` (in-proc fan-out) | §B.3 :141 "Mandatory per-class failure posture, labeled not inherited: FAIL-MISS (commissions/RDRS) vs fail-OPEN (attestation, report-integrity) vs drop-counted (RAM-guard)". The label is data, so a reviewer can see what a missed kind costs | one global posture (both wrong for half the catalog) |
| D5.9 | Kind metadata is **self-describing** exactly like `JobSpec` (addendum parity): `kind_id`, `class`, `service_tag` (the emitting service), `module_info`, `description`, `posture`, `consumers`, `metadata` | Addendum: "the register function should include like service tag name, category, module information … Later we may even have tool for maintenancer agent". A kind the maintenancer cannot attribute to a service is not queryable | flat kind strings |
| D5.10 | **Queue-depth seam is named, not built**: the catalog carries a `SEAM_DEPTH_TRIGGERS` note documenting that no depth plumbing exists (GAP-14: all depth signals are pull-only SQL counts) and naming the enqueue seam (EV-24) as the future plant point | §B.3 "Queue-depth triggers: cannot be delivered in v1 … Name the seam; build it only when a consumer exists" | building depth counters now (no consumer — the exact anti-pattern the study prices) |
| D5.11 | The engine's `EventTrigger` consumes the registry through an **`ingest_event(kind_id, payload)`** callback the registry invokes after `record()` (registered by the engine at start; no engine import from the registry) | Keeps the dependency one-way (registry → callback), so P5 compiles and tests without the engine, and P2/P3/P4 can proceed in parallel | the registry importing `EngineService` (a cycle and a merge hazard) |
| D5.12 | **Lag is measured only where it is meaningful**: `event_bus` kinds (the subscriber queue has a known enqueue time and a dequeue time) and state-table kinds (row `created_at`/stamp vs observation time). Terminalization lag is **not** measurable at this tree and stays `None` with a documented reason | Fabricating a lag for a substrate that carries no timestamp pair is a lie the study explicitly warns against (`F12`'s `ensemble_sweep_cycle_seconds` note: publishing a CONFIGURED value under an "observed" name is a fabrication, `daemon/services/metrics_query/wiring/w2.py:331-338`) | computing lag from log timestamps (wrong clock frame; the boot-telemetry explorer flagged exactly this trap) |

---

## 4. Task decomposition

| ID | Task | Outcome | Files / anchors |
|---|---|---|---|
| **T5.1** | `catalog.py` — `EventClass`, `FailurePosture`, `KindSpec` (frozen), the v1 catalog of classes 1-4 + the `deferred_v2` class-5 entry + the `SEAM_DEPTH_TRIGGERS` note | the catalog is a single frozen tuple; every entry has `kind_id`, `class`, `service_tag`, `module_info`, `description`, `posture`, `consumers`, `metadata`; `validate_catalog()` refuses a duplicate `kind_id` or a missing posture | `daemon/services/event_registry/catalog.py`; substrate anchors §3.1 above |
| **T5.2** | `emit.py` — `record(kind_id, **dims)`: bounded ring, drop-counted, never raises, never blocks; `flush()` | a full ring increments `record_dropped_total` and never raises into the caller (mutation: make `record` raise ⇒ RED); `record` is sync and < 20µs (asserted as a coarse bound) | `daemon/services/event_registry/emit.py`; precedent `daemon/services/metrics_query/wiring/accumulate.py`, `daemon/services/metrics_query/sampler.py` (raising-adapter discipline) |
| **T5.3** | Bridge **class 1** — terminalization: one `record(...)` adjacent to each of the four wiring sites, plus the `fire_for_terminal_child` return-value dimension (outcome + fired count) | four call sites, ≤5 lines each; a child terminalization increments `instance_terminalized` with the outcome dimension; **the bridge is not inside the CAS transaction** | `daemon/services/child_reports.py:6444/:6643/:6945`, `daemon/services/error_reporting.py:693`; substrate `daemon/services/dependency_bus.py:1921` |
| **T5.4** | Bridge **class 2** — durable bus kinds: `record(EventKind.<x>.value, ...)` inside/adjacent to `EventBus.create_event`'s **callers**, not inside `create_event` (which is shared) | the `event_bus:*` kinds count; `record` never sits inside `create_event`'s durable write | `daemon/services/event_bus.py:155` (read-only reference), plus the observer's consumption side `daemon/services/job_feedback_observer.py` |
| **T5.5** | Bridge **class 3** — state-table kinds: RDRS obligation observation (a `PENDING` row observed) and `mission_commissions` state transitions | counts observable without adding a write to the state machines (observe at the read seams: RDRS sweep + the commission mutation seam) | `daemon/services/report_delivery_recovery.py` (lane reads), `daemon/repositories/report_injection/models.py:83-90, :124`; `daemon/repositories/mission_record/models.py:372` |
| **T5.6** | Bridge **class 4** — attestation epochs (ledger `increment` / `reset` / `set_escalated_and_reset`) | counts + posture `FAIL_OPEN` labeled; the bridge never changes the ledger's own fail-open behavior | `daemon/services/attestation_ledger.py:101-131, :137+` |
| **T5.7** | `metrics.py` — `snapshot()` per D5.5 + the **consumer liveness verification** (declared vs live subscriber sets) | snapshot keys are the declared dims; a declared consumer that is not live appears in `consumers_live` as absent + a WARNING once | `daemon/services/event_registry/metrics.py`; live-set sources `daemon/services/event_bus.py:286-306` (`_global_subscribers`), the bus lane names |
| **T5.8** | `ingest_event` callback registration point (D5.11) | the registry exposes `set_event_sink(cb)`; the engine (P4's wiring) installs it; without a sink `record()` still works | `daemon/services/event_registry/emit.py` |
| **T5.9** | Author the P5 unit pack (catalog validation, ring drop-counting, four bridges, posture labels, consumer liveness, no-metrics-family assertion) — registers in P7 | pack present | `test/packs/event_registry_unit_test.sh`, `tests/unit/services/event_registry/*.py` |

**No migration in this phase** (D5.2/D5.3) — recorded explicitly so the release's migration-delta assertion expects **+0** from P5.

---

## 5. Effort

| Bucket | Low | High | Basis |
|---|---|---|---|
| Catalog + emit ring (T5.1, T5.2) | 5 | 8 | |
| Four class bridges (T5.3-T5.6) | 7 | 12 | 4 sites for class 1 + 2 for class 2 + 2 for class 3 + 1 for class 4 |
| Observability projection + consumer liveness (T5.7) | 3 | 5 | |
| Sink seam (T5.8) | 1 | 2 | |
| P5 unit pack authored (T5.9) | 2 | 4 | |
| **Phase total** | **18** | **31** | ~**24 mid** |

---

## 6. Fences that constrain the design

**F-1 — the metrics family census is CLOSED (the single biggest constraint on this phase).**
`tests/unit/metrics/test_metrics_family_adjudication.py` pins, at this tree:
- `:415-426` `len(CATALOG_FAMILY_VERDICTS) == 28` **and** the exact key set; **[TREE-TRUTH RECONCILE — 2026-11-21 (P6 review #3): 28 → 29 (verified at tree 2f36e8aed94c — fence pin now `:436`); planning-time figure predates the landed family extension (F24)]**
- `:428-434` exactly **12 IN + 6 PARTIAL** **[TREE-TRUTH RECONCILE — 2026-11-21 (P6 review #3): 12 IN → 13 IN; 19 registered total (verified at tree 2f36e8aed94c); planning-time split predates the landed family extension]**, and `REGISTERED_FAMILIES == full + partial`;
- `:436-438` `set(registry.registered_families()) == set(REGISTERED_FAMILIES)`;
- `:440-452` every registered family carries a `FAMILY_SERIES_BANDS` entry;
- `:649-676` the wiring package may not increment a foreign metric, may not import sqlalchemy or a pool, and `WIRING_ROOT` (`:391`) is `daemon/services/metrics_query/wiring`;
- `:873-881` each family's measured closed label space ≤ its band;
- `:883-906` `BASELINE_SERIES (89) + measured ≤ GLOBAL_SERIES_CAP (2000)`.

**Therefore:** the event registry registers **ZERO** metrics in Phase 1. Its observability is an engine-local snapshot (D5.5), exactly like the engine's (D2.14). Registering `ensemble_event_registry_*` requires, in one conscious change: a verdict row, a `REGISTERED_FAMILIES` member, a band, and — because this module would live outside `WIRING_ROOT` — an `ALLOWED_DAEMON_DIFF`/`PRE_EXISTING_DAEMON_OFFENDERS` record. That is P6's T6.3 ceremony, gated, not a side effect. **This is the constraint that most shapes P5**, and it is the reason the phase can be parallel and additive.

**F-17 — the `event.kind` SQLite CHECK is stale (constrains D5.2/D5.4).**
`daemon/migrations/versions/20260412_000002_create_event_table.sql:14-19` declares a `CHECK(kind IN (...))` listing only **8** kinds, while `EventKind` has **24** members at this tree. The model column itself carries no CHECK (`daemon/repositories/event/models.py`, `kind: str`). **Therefore:** the registry must never rely on that table's CHECK to accept a kind, and must never claim a kind is "persisted and validated" on SQLite. The registry's kind identity is the **catalog** (code), not the SQLite CHECK.

**F-18 — the dead outbox is a HARD anti-target (constrains the class-5 deferral).**
`daemon/services/instance_lifecycle.py:5976` (pause) and `:6589` (resume) are the outbox's only consumers and they are **DEBUG-only** — GAP-13 verified intact. **Therefore:** Phase 5 must not "revive" the outbox as part of class 5; the deferred entry is a pointer, not a plan, and any future plant is a separate commission (the coding councilor's v2 ruling).

**F-19 — the S1 terminalization fix is the substrate; do not re-implement it.**
`DependencyBus.fire_for_terminal_child` (`:1921`) is durable + exactly-once via a guarded `WHERE state='PENDING'` UPDATE, and it is **already wired** at `child_reports.py:6444/:6643/:6945` + `error_reporting.py:693`. **Therefore:** the class-1 bridge observes the **return value** (`list[FollowUp]`) at the caller and must never add a second fire, a second CAS, or a parallel "did it fire?" probe. A bridge that re-fires is a correctness defect, not telemetry.

**F-3 / F-11 — `_NAME_RE` fence.** The registry's column/dim names must not name `action_by` / `acted_at`; no string literal may combine `resource_pid_map` with `action` (`tests/test_resource_control_seam.py:294-346`).

---

## 7. Dependencies

- **Phase:** P1 only (`TriggerSpec`, `EngineConfig`, `engine_jobs.event_kind`). Deliberately **not** P2 — the registry compiles and tests standalone.
- **External:** none.
- **Consumes:** `event` table (existing), `dependency_watchers` (existing), `report_injections` + `mission_commissions` (existing), `AttestationLedger` Protocol.
- **Blocks:** P6 (kind metadata feeds the shape-ready projection), the later engine phase that consumes event triggers for real.

## 8. Parallelism

- **Fully parallel** with P2/P3/P4 (disjoint trees: `daemon/services/event_registry/` and four ≤5-line bridge edits in `child_reports.py` / `error_reporting.py` / RDRS / `attestation_ledger`'s callers).
- **Bridge edits are the only shared-file risk.** `child_reports.py` and `error_reporting.py` are **not** touched by any other phase in this program; `daemon/services/report_delivery_recovery.py` is not touched either; `daemon/services/attestation_ledger.py` is not touched (P5 observes at callers).
- Worktree-lane parallelizable: **yes** — this is the lane to parallelize first.

## 9. Risks / mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| A bridge adds latency to a hot path | terminalization/report delivery slows | D5.7: sync, non-blocking, bounded ring; T5.2 asserts a coarse µs bound; the bridge sits AFTER the existing emitter, never inside a transaction (T5.3's "not inside the CAS transaction") |
| The bridge accidentally becomes a second delivery path | double delivery (a correctness defect) | F-19: read-only observation of the existing call's return value; the pack asserts the fire count is unchanged by the bridge (a mutation adding a fire REDs) |
| Kind catalog drifts from real emitters | a registered kind never fires (dead entry) or an emitter fires an unregistered kind | `validate_catalog()` + a pack leg that scans the bridge call sites and asserts every `record(...)` kind is in the catalog and every catalog kind has ≥1 bridge (a bidirectional census, house style: `tests/job_queue/test_terminal_write_census.py`) |
| A declared consumer is not actually subscribed | the operator's "who listens?" answer is wrong | D5.4's live-set verification + WARNING via T5.7 |
| Someone registers a metrics family "while here" | two pins RED + a closed census widened without a decision | F-1 stated in the module docstring; the P5 pack asserts `registered_families()` is unchanged (a hard leg, not a comment) |
| The class-5 deferral is read as "we forgot" | a future lane silently plants the outbox | the catalog carries an explicit `deferred_v2` entry with the GAP-13 anchors and the split-ruling reason |

## 10. Acceptance gates (measurable)

| ID | Gate |
|---|---|
| AC1 | Catalog completeness: every v1 kind carries `kind_id`, `class`, `service_tag`, `module_info`, `description`, `posture`, `consumers`, `metadata`; `validate_catalog()` refuses duplicates and missing postures (mutation ⇒ RED) |
| AC2 | Bidirectional bridge census: every `record(...)` call site's kind is in the catalog; every catalog kind (except the `deferred_v2` entry) has ≥1 bridge. Mutation: add a `record` with an unregistered kind ⇒ RED |
| AC3 | `record()` never raises and never blocks: a full ring drops and increments `record_dropped_total`; a caller in a hot loop completes within the coarse bound. Mutation: `record` raises on full ⇒ RED |
| AC4 | **Zero delivery change**: for each bridged site, the existing emitter's observable behavior (row counts, CAS rowcount, delivery outcomes) is byte-identical with and without the bridge |
| AC5 | Posture labels: terminalization + state-table are `FAIL_MISS`; attestation is `FAIL_OPEN`; in-proc fan-out is `DROP_COUNTED`; each is asserted from the catalog (mutation: flip one label ⇒ RED) |
| AC6 | Consumer liveness: with a declared consumer absent from the live subscriber set, `consumers_live` reports it absent and one WARNING is logged |
| AC7 | Lag honesty: lag is populated ONLY for `event_bus`/state-table kinds; terminalization lag is `None` with the documented reason (a mutation that fabricates a lag from `now − created_at` on terminalization ⇒ RED) |
| AC8 | **Fences:** `tests/unit/metrics/test_metrics_family_adjudication.py` passes with **zero** delta (no family registered); `tests/unit/test_maintenance_idle_starvation.py` `test_job_metrics_exposes_one_block_per_job` unchanged; `tests/test_resource_control_seam.py` unchanged; **migration delta from P5 == 0** |
| AC9 | Deferral honesty: the class-5 entry is present as `deferred_v2` with the GAP-13 anchors, and no code path plants anything on `turn_transitions` |
