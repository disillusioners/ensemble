# Phase 3 — Trap-impossibility guardrails, kill switches, fail-to-legacy, dual-path harness

**Depends on:** P2 (execution core).
**Feeds:** P4 (the boot phase reads the guardrails and the watchdog observes the engine), P7.

---

## 1. Objective

Make the engine's two defect classes **mechanically impossible** rather than *intended away*, and give the estate a one-flag rollback:

1. **Born-dead impossibility** — dependencies resolve by calling the singleton getter **at tick time**, never at construction; a boot wiring assertion fails LOUD; `dead=true` is persisted after `2 × interval` with zero runs.
2. **Permanently-due impossibility** — already structurally delivered by P2's stamp-on-every-terminal rule; P3 adds the `dead` escalation that makes the *inverse* failure (a job that never runs) visible.
3. **Kill switches** — per-job `kill_switch_key` + engine-wide `ENSEMBLE_ENGINE_ENABLED` + `ENSEMBLE_ENGINE_SHADOW_MODE`, with **FAIL-TO-LEGACY** polarity (unreadable ⇒ the module's own loop owns execution; the engine never fails-stuck).
4. **Dual-path harness** — `SERVICES_<MODULE>_USE_ENGINE`, both paths compiled, the flag selects which one owns execution. Rollback is **one flag, not a code change**.

**Phase 3 migrates nothing.** The harness is built and tested against a *fixture* module; the first real adopter is Phase 2 of the migration program (one-by-one, per the Emperor rule).

---

## 2. Scope

**In:** `daemon/services/engine/guardrails.py` (dependency getter discipline + dead detector + the boot assertion *function*), `daemon/services/engine/killswitch.py`, `daemon/services/engine/dualpath.py`, the in-flight-tick cancellation half of dynamic re-registration, and the supervisor boundary that converts Template-B permanent exits into supervised restarts.

**Out:** the boot assertion *call site* and the watchdog (P4 — they live in `daemon/api.py` / the watchdog module); the event registry (P5); any real module adoption (`SERVICES_<MODULE>_USE_ENGINE` is implemented and unit-tested against a fixture only).

---

## 3. Decisions

| ID | Decision | Rationale (anchor) | Rejected |
|---|---|---|---|
| D3.1 | Dependencies are declared as **singleton getters** (`Callable[[], T | None]`), never as objects. `resolve(spec)` calls each getter **at the moment the job becomes due**, on every attempt | §B.1/§B.2 born-dead impossibility + `research-anchors.md` §5 "dependencies must resolve via singleton getter AT TICK TIME (never constructor capture) so the flag flip re-routes without restart". The counter-example is live and intact: `daemon/api.py:1921` constructs `OrphanWatcherSweepService(dependency_bus=get_dependency_bus())` **before** `await init_dependency_bus(app, manager)` at `:2974` (which calls `set_dependency_bus(bus)` at `daemon/services/dependency_bus.py:3372`, wired from `api.py:5712`) ⇒ the sweep captures `None` at construction and ticks bus-less forever | constructor capture (the live defect); a one-shot late-bind at boot (works until the getter's target changes later in the lifespan — the defect returns in a new shape) |
| D3.2 | A job with a `None`-resolving dependency is `SKIPPED_DEP_MISSING` — **counted**, with the reason, **every** tick; after `2 × interval` of consecutive `DEP_MISSING` the job escalates to `dead=true` and logs at ERROR **once** per escalation | §B.2 meta-liveness flag; the DEBUG-only quiet branch is the failure mode (`orphan_watcher_sweep.py:101-108` ticks and DEBUG-logs when the bus is None — nothing escalates). "Silent no-op paths must be counted" is a §B.1 primitive | skipping silently; a one-shot warning at construction (too early to see the real state) |
| D3.3 | The boot wiring assertion is a **function** `assert_dependencies_resolvable(specs)` returning a report; P4 calls it after the last dependency-producing boot pass and logs ERROR per unresolved dependency + a summary line | §B.2 "boot wiring assertion that fails LOUD"; the live-lane proof that a missing assertion costs boots: `api.py:5712` registers the bus late, and nothing loud happens in between | asserting inside `register()` (registration frequently precedes the dependency's construction — a false RED) |
| D3.4 | `dead` is persisted on `engine_jobs` **and** carried in the projection; recovering from `dead` requires a successful RUN (auto) or a re-registration (operator). **Observer carve-out (CS-9 — leader ruling, approver iteration-002): recovery is N/A for observers (no run exists to recover) — an execute_fn-withheld spec never enters the recovery ladder; a healthy observer stays continuously non-dead via `SKIPPED_SHADOW` evidence, and a dead observer (zero shadow rows in 2× interval) recovers by re-registration/registration repair, not by a RUN** | §B.2. Persisted so the fact survives a restart — the OrphanWatcher lie survived restarts precisely because its state was RAM-only | RAM-only `dead` (the defect class) |
| D3.5 | **Kill-switch polarity is FAIL-TO-LEGACY**, not fail-closed: an unreadable engine-wide switch resolves to "engine does not own execution" (legacy path). A per-job `kill_switch_key` that is unreadable resolves to "job skipped, counted as `KILL_SWITCHED`" | §B.2:124 "engine failure posture = fail-to-legacy, never fail-stuck"; the existing polarity to INVERT is documented at `daemon/services/dependency_bus.py:3361-3368` ("Callers must treat None as a hard error — no fallback path"). Contrast: the decision overlay's kill channel is fail-**CLOSED** (`daemon/services/decision/kill.py:1-67`) because there the safe state is OFF; here the safe state is *the estate keeps running on its own loops* | fail-closed (would STOP estate work if the engine config broke — strictly worse than today) |
| D3.6 | The engine-wide switch is read **at tick time** through a resolution ladder: per-job `kill_switch_key` (env/config tier) → `ENSEMBLE_ENGINE_ENABLED` → `ENSEMBLE_ENGINE_SHADOW_MODE` → spec. A flip takes effect on the **next tick**, no restart | §B.2 + the live-read contract the kill-channel precedent proves (`daemon/services/decision/kill.py`: "LIVE READ, PER CONSULT … a flip takes effect on the next consult — no restart") | reading once at start (a flip needs a restart — the flag would not be a rollback) |
| D3.7 | `SERVICES_<MODULE>_USE_ENGINE` is a **NEW flag family** (verified: no `*_USE_*` flag exists in `daemon/` — `research-anchors.md` §5, only an unrelated `COMPUTER_USE_MCP_ENABLED` comment at `mcp_service.py:823`). Shape: ONE flag per module; OFF (or unset) ⇒ **the engine unregisters the job and the module's legacy loop starts**; ON ⇒ the legacy loop does not start and the engine owns it. Both code paths stay compiled | §B.2:124 verbatim; the closest mechanical models are (1) constructed-but-stopped OFF semantics (`daemon/api.py:3774-3789` service_tool: "OFF = the sweep never starts … Byte-identical to pre-Phase-1 behavior"), (2) per-lane enable flags (`daemon/config.py:2297-2424` report_delivery_recovery lanes), (3) the DB-tier flag with cache invalidation (`decision/kill.py`) | deleting the legacy loop on adoption (rollback becomes a code change — explicitly rejected by §B.2); a single global `USE_ENGINE` (no per-module rollback) |
| D3.8 | Default for every `SERVICES_<MODULE>_USE_ENGINE` is **OFF/unset** | The Emperor phase rule: nothing migrates in Phase 1. OFF is also the safe default under fail-to-legacy | ON by default (contradicts the ruling and the dormant posture) |
| D3.9 | **Supervisor boundary**: `EngineService`'s tick loop wraps its body in a `try/except Exception` that records the failure, logs at ERROR, and **re-enters the loop after a bounded backoff** (never `return`) | Converts the Template-B permanent-exit class (`daemon/services/job_lock_sweep.py:241-250`, catch→log→`return`) into a supervised restart, which is the §B.1 `supervised()` primitive folded into the engine. Leaving `return` here would install the very defect the engine exists to fix | Template-B `return` (a silent permanent death — F-7); no catch at all (an exception kills the engine task permanently) |
| D3.10 | DEDICATED_EXECUTOR a `timeout_policy=extend` hold longer than `2 × max_runtime_s` escalates to `dead=true` and a WARNING | `extend` has no other natural bound (D2.7 holds the lock); without an escalation an `extend` job can wedge invisibly | no bound (silent wedge) |
| D3.11 | **Dynamic re-registration (full)** — `register()` on a live id: validate → if the spec content changed, bump `version`, **cancel an in-flight tick** (if any), unregister any pending event latch, re-arm with the new spec | §B.2:118 "update interval/unregister at runtime, version bump, cancel in-flight tick, re-arm — no restart". This is the half P1 deferred | refusing live re-registration (contradicts the frozen design); restarting the engine (the design says no restart) |
| D3.12 | Kill-switch reads are **pure functions over a resolver** injected into the engine (testable without env), and the resolver caches nothing | Mirrors `ram_guard_switch_from_env(environ)` (a pure function, `daemon/config.py:1180-1204`) and the boot sweep's `kill_switch_from_env` (`daemon/services/boot_subprocess_sweep.py`, `:120-130`); purity keeps the fail-to-legacy polarity testable in isolation | reading `os.environ` directly inside the tick (untestable, and the ambient env is exactly what the fences warn about) |

---

## 4. Task decomposition

| ID | Task | Outcome | Files / anchors |
|---|---|---|---|
| **T3.1** | `guardrails.py` — `DependencyRef` (getter wrapper), `resolve_at_due(spec)`, `DEP_MISSING` counting, `escalate_dead()`, `assert_dependencies_resolvable(specs) -> WiringReport` | resolving happens per-due-evaluation (a spy proves N calls for N ticks); an unresolved dependency yields `SKIPPED_DEP_MISSING` and escalates after `2 × interval`; the report names each unresolved `job_id → dependency label` | `daemon/services/engine/guardrails.py`; model `daemon/services/dependency_bus.py:3361-3381` (`get_dependency_bus`/`set_dependency_bus`), defect site `daemon/api.py:1921` vs `:2974` |
| **T3.2** | `killswitch.py` — `EngineSwitchResolver` (pure), the per-job/en­gine-wide ladder, `KILL_SWITCHED` counting | unreadable ⇒ `engine_enabled=False` (fail-to-legacy) and a counted `KILL_SWITCHED`; explicit OFF ⇒ same; ON ⇒ job proceeds | `daemon/services/engine/killswitch.py`; anchors `daemon/config.py:1180-1204` (`ram_guard_switch_from_env`), `daemon/services/boot_subprocess_sweep.py:120-130` |
| **T3.3** | `dualpath.py` — `use_engine_enabled(module_slug)` + `EngineOwnership` (the decision record: which path owns a module's execution) + a fixture module proving both paths | for a fixture module: OFF ⇒ engine job absent + legacy loop's tick counter climbs; ON ⇒ legacy loop's counter is 0 and the engine's run count climbs; **flipping at runtime** moves ownership without a restart | `daemon/services/engine/dualpath.py`; anchors `daemon/api.py:3774-3789` (constructed-but-stopped), `daemon/config.py:2297-2424` (per-lane flags) |
| **T3.4** | Supervisor boundary in `execution.py` (D3.9) | an injected unexpected exception in the tick body ⇒ ERROR logged + the loop continues after the backoff; the loop task never terminates | `daemon/services/engine/execution.py`; anti-pattern `daemon/services/job_lock_sweep.py:241-250` |
| **T3.5** | Full dynamic re-registration (D3.11) | re-registering an in-flight job cancels the in-flight tick, bumps `version`, and re-arms; the cancelled run is recorded as `SKIPPED_OVERLAP`→ no — as an aborted attempt with outcome `SKIPPED_REREGISTERED` (added to D2.6's vocabulary) | `daemon/services/engine/registry.py` + `execution.py` |
| **T3.6** | `extend` wedge escalation (D3.10) | an `extend` hold past `2 × max_runtime_s` sets `dead=true` + WARNING once | `daemon/services/engine/guardrails.py` |
| **T3.7** | The config surface for the new flag family | `EngineConfig.dual_path_modules: dict[str, bool]` **or** a `SERVICES_<MODULE>_USE_ENGINE` env read (decided: env read, since `SERVICES_*` prefixed names are the estate convention and the flag must be readable without a config reload) | `daemon/services/engine/dualpath.py` (resolver); no `ServicesConfig` edit (fence F-9) |
| **T3.8** | Author the P3 unit pack (getter-at-tick, dead escalation, fail-to-legacy polarity, dual-path flip, supervisor re-entry, re-registration cancel) — registers in P7 | pack present | `test/packs/engine_guardrails_unit_test.sh`, `tests/unit/services/engine/test_guardrails.py`, `test_killswitch.py`, `test_dualpath.py` |

---

## 5. Effort

| Bucket | Low | High | Basis |
|---|---|---|---|
| Trap-impossibility (T3.1, T3.6) | 6 | 10 | component 5 of the brief (10/8-14) minus the boot-call site P4 owns |
| Kill switches + fail-to-legacy (T3.2, T3.3 partially) | 4 | 7 | component 6 (8/6-10) |
| Dual-path harness (T3.3, T3.7) | 5 | 8 | component 9's harness half (20/14-28 minus the pilot, which is NOT in Phase 1) |
| Supervisor boundary (T3.4) | 2 | 3 | |
| Dynamic re-registration (T3.5) | 2 | 4 | |
| P3 unit pack authored (T3.8) | 3 | 5 | |
| **Phase total** | **22** | **37** | ~**29 mid** |

---

## 6. Fences that constrain the design

**F-9 — `ServicesConfig` field additions are pin-adjacent (binds T3.7).**
`ServicesConfig` (`daemon/config.py:2143`) carries 70 fields today. `tests/job_queue/test_running_zombie_sweep.py:2528-2537` pins *which* switch names must and must NOT exist on the class (`mission_strand_heal_running_zombie_enabled` in, `mission_strand_heal_enabled` out), and `tests/test_skill_evolution_config.py:129` shows the house pattern of an explicit `>=`-count pin. **Therefore:** Phase 3 adds **no** `ServicesConfig` field. The per-module `USE_ENGINE` flags are read via a purpose-built resolver from the environment (`SERVICES_<MODULE>_USE_ENGINE`), whose prefix discipline matches the estate convention without touching a pinned class — and each module's adoption (a later program) writes its own flag into `config.yaml` explicitly, so the flag is discoverable by grep.

**F-4 — `MANAGED_KEYS` count is pinned at 19 (binds the kill-switch tier choice).**
`tests/test_settings_resolution.py:676` asserts `len(MANAGED_KEYS) == 19`; the literal is AST-guarded for duplicates and `literal_count == runtime_len` (`tests/test_daemon_settings_api.py:713-760`). **Therefore:** the engine-wide switch is **env/config tier only** in Phase 1. Making it a DB-managed key (the `decision/kill.py` live-flip model) requires in the SAME change: a registry entry with a scalar `config_path` (or explicit `None`), the `19 → 20` bump, and the `tests/test_daemon_settings_api.py` duplicate-guard staying green. That is a deliberate, documented follow-up — **routed, not inline** — because it changes the boot-seed semantics of a global switch.

**F-10 — the ORIGINAL born-dead defect site is owned by a sibling lane.**
`fix/phase1-live-defects` (in flight, base `b71742fe3`) carries the OrphanWatcher late-bind fix (`6f0019f99`) touching `daemon/services/orphan_watcher_sweep.py` + `daemon/api.py` (a boot wiring assertion at `:3021`) and the bash-reaper retention lane (`2598c82b9`, `daemon/services/bash_spawn_reaper.py` + `daemon/api.py` + repo files). **Therefore:** Phase 3 implements the getter discipline as an ENGINE contract (`guardrails.py`) and does **not** touch `orphan_watcher_sweep.py`. The two lanes overlap in `daemon/api.py` only; see §6.1 below for the merge-order facts.

**F-11 — `_NAME_RE` fence** (same as F-3; the `dead` column and the escalation log strings must not name `action_by`/`acted_at`, and must not put `resource_pid_map` near the word `action`).

### 6.1 Fast-lane technical coupling facts (for the decisions worker; NOT a recommendation)

| Question | Measured fact |
|---|---|
| File overlap between this branch and `fix/phase1-live-defects`? | **Only `daemon/api.py`.** The live-defects lane touches `daemon/api.py` (2 hunks: a bash-retention wiring block after the boot reconcile `~:1226-1272`, a bus boot-assertion after `await init_dependency_bus(...)` `~:3018-3041`, a shutdown stop block `~:4532-4547`), `daemon/services/orphan_watcher_sweep.py`, `daemon/services/bash_spawn_reaper.py`, `daemon/repositories/bash_spawn/{models,repository}.py`, plus 3 test files. This branch's P4 edits `daemon/api.py` too (engine start + boot insertion + watchdog + shutdown). Phase 3 itself edits **no** `api.py` line. |
| Which api.py regions? | live-defects: `:1226-1272`, `:3018-3041`, `:4532-4547`. Engine P4 (planned): a start block adjacent to `manager.setup_worker_pool()` / the A6 ServiceReconciliation pass (`:3749-3819` template), and a shutdown block near the existing sweep-stop cluster (`:4440-4600`). **No textual collision with the three live-defect hunks** — the regions are ~700 lines apart and in different functional blocks, so git will auto-merge; the human review cost is architectural (two independent sets of boot-pass insertions), not textual. |
| Does the Phase-2 pilot recommendation depend on the OrphanWatcher fix landing first? | **Yes, structurally.** `decision-report.md` §C.2 picks OrphanWatcherSweepService as the target-pilot *because* it is a "working, small, previously-trapped module" — post-fix. Adopting it while born-dead would make the pilot's parity baseline useless ("engine runs it" vs "engine broke it" is unanswerable, §C.2's own argument against the maintenance tier). So: the *pilot recommendation* is independent of this plan, but the *pilot's executability* requires `6f0019f99` (or equivalent) to be in `latest`. |
| Does the engine's own `dead` detector duplicate the live-defects lane's work? | **Complementary, not duplicate.** The live-defects lane late-binds the ONE service and adds ONE boot assertion. The engine's `dead` detector is the general contract that would have caught it without anyone looking. Both are cheap; the engine's version is dormant in Phase 1. |
| One release or two? | Facts only: three `api.py` hunk-regions are textually disjoint; the live-defects branch is 2 commits on `b71742fe3`; this branch is docs-only today. **Merge order fact:** either order auto-merges. Engine P4 inserts its start block in the `~:3740` region (before the A6 ServiceReconciliation comment block at `:3745-3749`) and its shutdown block before `service_reconciliation.stop()` at `:4600` — neither region contains any of the live-defects anchors (`:1226-1272` / `:3018-3041` / `:4532-4547`), and P4 introduces none of the strings the byte-offset ordering pin requires. P6 adds one `include_router` line at `~:6359` (the router catalogue) — also disjoint. The recommendation belongs to the decisions worker. |

---

## 7. Dependencies

- **Phase:** P2 complete.
- **External:** the sibling lane `fix/phase1-live-defects` (informational only — no code dependency; the engine's guardrail is a NEW module).
- **Blocks:** P4 (boot assertion call site + watchdog), P7.

## 8. Parallelism

- P3 is **serial after P2** (it modifies `execution.py` and `registry.py`, which P2 owns).
- It may run **in parallel with P5** (disjoint trees) and with P6's read-only projection work.
- Worktree-lane parallelizable: yes.

## 9. Risks / mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Fail-to-legacy polarity implemented as fail-closed by habit | a bad engine config STOPS estate work — strictly worse than no engine | D3.5 is explicit and the polarity is a pure-function unit test: unreadable/missing/garbage ⇒ `engine_enabled=False`; a mutation flipping the default REDs |
| The dual-path harness silently keeps BOTH paths running | double execution of a module's work | `EngineOwnership` is a single decision record consulted by both sides; the fixture pack asserts exactly one tick counter climbs per tick. A mutation making both paths start REDs |
| `extend` policy holds a lock forever | invisible wedge | D3.10 escalation at `2 × max_runtime_s`; the escalation is `dead=true` + one WARNING |
| Re-registration cancels a tick mid-DB-write | an interrupted write leaves a half-stamped row | The in-flight cancellation awaits the dispatch's cleanup (`finally` release + a final `engine_runs` row with `outcome=SKIPPED_REREGISTERED`), so the audit always closes |
| Supervisor backoff hides a persistent crash loop | ERROR spam or a silently slow loop | The supervisor logs at ERROR each cycle with a counter; the P4 watchdog observes the supervisor's own restart count (the "who watches the watchman" chain has an end) |
| Guardrail module drifts from the live defect site and "fixes" the wrong thing | effort spent; the live defect survives | F-10: the engine does NOT touch the defective module. The pilot (Phase 2 of the program) is where the two meet |

## 10. Acceptance gates (measurable)

| ID | Gate |
|---|---|
| AC1 | **Getter-at-tick:** a dependency getter is called once per due-evaluation (spy count == tick count over 5 ticks with `interval < tick window`), and a dependency that becomes available *after* the engine started is picked up on the next tick with no restart |
| AC2 | **Born-dead escalation:** a permanently-`None` dependency yields `SKIPPED_DEP_MISSING` per tick, and `dead=true` after `2 × interval` with exactly ONE ERROR line at the escalation. Mutation: the escalation branch removed ⇒ RED |
| AC3 | **Boot wiring assertion:** `assert_dependencies_resolvable` returns a report naming each unresolved `job_id` + dependency label and is a **pure** function (no side effects). P4 consumes it (call site pinned there) |
| AC4 | **Fail-to-legacy:** unreadable engine-wide switch ⇒ `engine_enabled=False`; explicit `0/false/no/off` ⇒ same; explicit truthy ⇒ enabled; per-job unreadable ⇒ `KILL_SWITCHED` counted, per tick. Mutation: polarity flipped to fail-closed ⇒ RED |
| AC5 | **Dual-path:** fixture module OFF ⇒ engine has no such job and the legacy tick counter climbs; ON ⇒ legacy counter stays 0 and engine runs climb; a runtime flip moves ownership on the next tick |
| AC6 | **Supervisor:** an injected unexpected exception ⇒ loop continues (task not done), ERROR logged, restart counter incremented. Mutation: `return` on exception (Template-B) ⇒ RED |
| AC7 | **Re-registration:** re-registering an in-flight job cancels it, records `SKIPPED_REREGISTERED`, bumps `version`, re-arms; the job runs on the new spec on the next due evaluation |
| AC8 | **Fences:** `tests/test_resource_control_seam.py` unchanged-green (F-11); `MANAGED_KEYS` still 19 (F-4); zero `ServicesConfig` field delta (F-9); `daemon/services/orphan_watcher_sweep.py` unmodified (F-10); `daemon/api.py` unmodified by this phase |
