# Test Strategy — Engine Arc Phase 1 (BUILD engine B + event registry)

**Lane:** engine-arc-p1 · **Tree:** `/home/nea/ensemble-src-wt-engine-arc-p1` (HEAD `aed3eabce` = base `b71742fe3`/v0.31.23 + planning docs) · **Date:** 2026-11-21
**Nature:** PLANNING ONLY — this document pins the test contract; the implementation lane authors the files it names. No code, no commits, no test EXECUTION from this lane.
**Grounding:** frozen engine design §B.1–B.3 of `../central-polling-engine-study/decision-report.md`; explorer-verified anchors `research-anchors.md` (this tree) + this lane's own re-greps. Emperor rulings honored verbatim: "Build the engine." · "we build the engine first but don't use yet, then pick one by one" · event registry for "central loging, debug management, metric" · boot SLO "Boot wall clock is 2 minutes" (boot_epoch→readyz-green ≤120s) · JobSpec/register() SELF-DESCRIBING (service_tag/name, category, module_info, description, extensible metadata dict; metrics labels = service_tag+category; anticipates maintenancer tool + /metrics UI tab).

---

## 1. PACK ARCHITECTURE

### 1.1 New unit test files (all under `tests/unit/services/`, worktree-only)

Import paths adjust to wherever the implementation lands the engine module (`daemon/services/engine/…` or sibling); the FILE names below are the stable contract.

| # | File | Capability pinned | Est. nodes |
|---|------|-------------------|-----------|
| T1 | `tests/unit/services/test_engine_jobspec_registration.py` | JobSpec validation: exactly one trigger; `max_runtime>0`; SELF-DESCRIBING fields required (`service_tag`, `service_name`, `category`, `module_info`, `description`, extensible `metadata` dict); metrics labels derive as `service_tag`+`category`; re-registration upsert-by-id with version bump | 8–12 |
| T2 | `tests/unit/services/test_engine_honest_state.py` | State machine `IDLE→DUE→RUNNING→{SUCCEEDED, SKIPPED_<reason>, FAILED_BACKOFF, TIMED_OUT}`; EVERY terminal transition stamps `last_run_at`/`last_outcome`/`last_skip_reason`; DB source-of-truth (`engine_runs` append-only + `engine_jobs` mutable) with RAM cache hot-read | 10–14 |
| T3 | `tests/unit/services/test_engine_due_skip_backoff.py` | due ⇔ `now − last_run_at ≥ interval` (both boundary sides); stamp-on-skip (due-state cannot strand); skip-reason enum; backoff progression on consecutive idle-skips + reset on success; max_staleness forced-run as a COUNTED skip→forced-run transition | 10–14 |
| T4 | `tests/unit/services/test_engine_single_flight.py` | Per-job single-flight: overlap → `SKIPPED_OVERLAP` counted (not queued); lock acquired on loop before dispatch, held across executor threads, released in `finally` | 5–8 |
| T5 | `tests/unit/services/test_engine_dependency_liveness.py` | Getter-AT-FIRST-TICK resolution (never ctor capture — OrphanWatcher born-dead is the trap, INTACT at this tree: ctor capture `orphan_watcher_sweep.py:131-138`, construct `api.py:1910-1931`, `init_dependency_bus` `api.py:2974`, `set_dependency_bus` `api.py:5712`); LOUD boot wiring assertion; `dead=true` after 2×interval with zero runs | 8–12 |
| T6 | `tests/unit/services/test_engine_kill_switch_dualpath.py` | Per-job `kill_switch_key` + engine-wide `ENSEMBLE_ENGINE_ENABLED` + `ENSEMBLE_ENGINE_SHADOW_MODE`; FAIL-TO-LEGACY polarity (unreadable ⇒ legacy, never engine); `SERVICES_<MODULE>_USE_ENGINE` OFF → engine unregisters + legacy loop runs, exactly ONE executor per module (no double-execution); rollback = one flag | 8–12 |
| T7 | `tests/unit/services/test_engine_boot_phases.py` | Boot-phase classes: `BOOT_SWEEP` (awaited, DEADLINED — adds the deadline the substrate sweep lacks, `boot_subprocess_sweep.py:63-105` model), `BOOT_PASS` (awaited best-effort), `FIRST_TICK_IMMEDIATE`, `FIRST_TICK_DELAYED(+offset)`; per-job first-tick jitter | 6–10 |
| T8 | `tests/unit/services/test_engine_dormant_default.py` | Default config ⇒ DORMANT per the **D-3 ADOPT (AMD-3c — supersedes the pre-ruling "default OFF semantics" wording): ships `enabled=true` + `shadow_mode=true`; dormant-default = registered observer specs, zero executables, shadow rows accumulating; per-job `USE_ENGINE` all unset.** Registrations accepted, observability live, tick loop executes NOTHING | 4–6 |
| T9 | `tests/unit/services/test_engine_event_registry.py` | Event registry = registration + observability ONLY: kind catalog, structured emission carrying ids, per-kind metrics (firing counts, consumers, lag where measurable); NO fan-out unification, NO queue-depth triggers (negative pins) | 6–10 |
| T10 | `tests/unit/services/test_engine_watchdog.py` | Engine-independent watchdog: stall + tick-task-death detection within interval+grace; WARNING+ logging; engine-INDEPENDENT recovery path; watchdog self-observability (heartbeat per `job_feedback_observer.py:384` pattern) | 6–9 |

**Total ≈ 71–107 nodes.**

### 1.2 Pack scripts (`tests/packs/`)

| Pack | Form | Purpose |
|------|------|---------|
| `engine_arc_phase1_mutation_matrix.py` | `.py` (style of `ram_guard_phase6_mutation_matrix.py`) | **Centerpiece** — §2. Runs every pin leg as RED-with-defect + GREEN-after-restore. |
| `engine_dormant_boot_smoke_test.sh` | `.sh` (boot-driven gate, style of `prompt_reload_fresh_swap_e2e` family) | §3 dormant-default proof + `SERVICES_*_USE_ENGINE=1` single-job activation; carries the §5 boot-SLO timed probe. |
| `engine_watchdog_drill_test.sh` | `.sh` | §4 watchdog drill; produces the evidence artifact. |
| `engine_arc_phase1_pins.txt` | targets `.txt` (house style: `preflight_census.txt` etc.) | Explicit node-id list of all engine pins for shard sizing (no testpaths fallback). |

### 1.3 PACKS.md registration flow (implementation lane owns it)

Ownership chain, one link per pack: **task** (phase-plan task that creates the artifact) → **artifact** (the test file / pack script) → **File cell** (the phase plan's file list claims it) → **pack** (the `tests/packs/` script binding the file) → **PACKS.md row** (registration block in `.agents/tester/PACKS.md`).

- Registration block format per house style: `<!-- engine-arc-phase1-<pack>-registration-begin -->` … `<!-- …-registration-end -->`, header "What this block registers" + the 5-column table `Pack / leg | Invocation | Scope | Last run | Status`.
- **`PENDING` is the honest pre-build value**: while only the plan exists, `Status` = `PENDING — pack authored at planning, run lands with implementation lane`, `Last run` = `—`. Flipped only after a real run with its sha.
- Each row must eventually carry **detection-power proof** (F/P counts vs base and vs the planted mutation — house precedent in every recent row).
- Gotcha (critical note): never look up PACKS.md rows from the canonical checkout (detached `bf4bfde6e`, behind origin/latest — later rows MISSING there); cross-check the branch tip copy. Grep-empty ≠ unregistered.

### 1.4 Test legs ↔ build phases (single-ownership binding)

The sibling `phaseN-plan.md` owns final numbering; this table states the BINDING RULE and default bindings. **Plan-contract invariant (REJECT-grade) applies to tests too: each test file is claimed by exactly ONE phase task.** A pin rides the phase that CREATES the symbol it pins — never a later "verification phase" (that would violate symbol-creation ordering).

| Capability (build) | Owning test legs | Default binding |
|---|---|---|
| JobSpec + registration API + self-describing validation | T1; matrix M-14, M-01 partially | earliest engine-surface phase |
| Honest state machine + terminal stamping + persistence | T2; M-01, M-02 | same or next phase |
| Due/skip/backoff/staleness-escape | T3; M-02, M-03, M-05, M-07 | state-machine phase |
| Single-flight | T4; M-04 | execution phase |
| Dependency getter + boot assertion + dead flag | T5; M-08, M-09, M-10 | trap-impossibility phase |
| Kill switches + dual-path | T6; M-11, M-12 | controls phase |
| Boot-phase classes | T7 (+ boot smoke pack legs) | boot-integration phase |
| Dormant default | T8 + dormant boot smoke | boot-integration phase |
| Event registry | T9; M-13 | event-registry phase |
| Watchdog | T10 + drill pack | watchdog phase (watchdog CODE is build-side; the drill is test-side) |

---

## 2. MUTATION-VERIFIED STATE-MACHINE PINS (centerpiece)

### 2.1 Matrix mechanics (precedent: `tests/packs/ram_guard_phase6_mutation_matrix.py:27/:299`)

- Exit 0 only when **EVERY selected leg is RED-with-defect AND GREEN-after-restore**. A leg GREEN with the defect planted is reported **NON-DISCRIMINATING and fails the run** — a pin that cannot fail is worse than no pin.
- Mutations planted **in-process with `finally`-restore** (house process note: mutation runs never share a worktree with a read-only review).
- Child pytest env: `POSTGRES_*` scrubbed PER-VARIABLE; `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned EXPLICITLY (never `env -u`); `-p no:randomly`; explicit node lists from `engine_arc_phase1_pins.txt`; `--timeout=120` per leg (shard-level wall budgets per §6).

### 2.2 The matrix — 14 pin families / 23 legs

| Leg(s) | Pin (GREEN assertion) | Mutation planted (must turn it RED) | RED signal |
|---|---|---|---|
| M-01a/b | Every terminal transition (`SUCCEEDED`, `SKIPPED_*`, `FAILED_BACKOFF`, `TIMED_OUT`) stamps `last_run_at`+`last_outcome` (+`last_skip_reason` on skips) | Drop the stamp on ONE terminal branch (a: success path; b: skip/timeout path) | stamp-missing pin fires |
| M-02 | Stamp-on-skip: a skipped tick still advances `last_run_at` so due-state cannot strand | Skip path `continue`s BEFORE the stamp (the historical `maintenance.py:260-269` trap shape — [citation SUPERSEDED — approver iteration-001: those lines no longer exist at this tree; the landed v0.31.11 shape is stamp-on-attempt `:524-528` + stamp-on-success `:530-532` + `_record_skip` `:568-601`]) | stranded-due pin: job due forever after a skip |
| M-03a/b | due ⇔ `now − last_run_at ≥ interval` — BOTH sides: (a) exactly-at-interval IS due; (b) just-below is NOT due | `≥` → `>` (a red), or threshold shifted by epsilon (b red) | boundary pin fires |
| M-04 | Overlap → `SKIPPED_OVERLAP` counted, never queued; second dispatch cannot run while first in flight | Remove lock acquire (or release before completion) | concurrent-second-execution pin |
| M-05a/b | Backoff progression on consecutive idle-skips (base/cap formula), (b) reset on success | (a) break cap/formula; (b) skip the reset | backoff-sequence pin |
| M-06a/b | `timeout_policy=cancel` → `TIMED_OUT` + task cancelled; `=extend` → `TIMED_OUT` recorded, task allowed to finish, next tick waits | Swap the policy mapping (cancel behaves as extend / vice versa) | per-policy behavior pin |
| M-07 | Retry exhaustion → `FAILED_BACKOFF` terminal | Retry counter never increments (or exhaustion returns SUCCEEDED) | terminal-state pin |
| M-08 | **Getter-at-first-tick**: dependency resolved via getter AT FIRST TICK, never ctor capture | Change resolver to ctor capture (the OrphanWatcher shape) | **born-dead detection MUST fire**: `dead=true` after 2×interval with zero runs + boot assertion raised |
| M-09 | Boot wiring assertion fails LOUD (WARNING+/exception visible at boot) | Demote assertion log to DEBUG (or swallow the raise) | boot-loudness pin (asserts WARNING+ in captured boot log) |
| M-10a/b | `dead=true` ⇔ zero runs in 2×interval | (a) flag never set (false-negative); (b) flag set at 1×interval (false-positive) | liveness-flag pin |
| M-11a/b/c | Kill switches: (a) per-job `kill_switch_key` OFF ⇒ no execution; (b) `ENSEMBLE_ENGINE_ENABLED` OFF ⇒ nothing executes; (c) FAIL-TO-LEGACY: unreadable switch state ⇒ legacy path, never engine | (a)/(b) switches ignored; (c) polarity inverted (unreadable ⇒ engine) | execution-observed / wrong-path pin |
| M-12a/b | Dual-path `SERVICES_<MODULE>_USE_ENGINE`: (a) OFF ⇒ engine job unregistered AND legacy loop runs — exactly one executor; (b) ON ⇒ engine runs, legacy suppressed | (a) engine still registers with flag OFF, or legacy fails to start; (b) both run (double-execution) | single-executor-count pin |
| M-13a/b | Event-kind counters: (a) per-kind firing counts increment on the right kind; (b) metrics labels = `service_tag`+`category` (Emperor addendum) | (a) increment on wrong kind / not at all; (b) label key renamed | counter/label pin |
| M-14a/b/c | SELF-DESCRIBING registration validation: spec missing `service_tag` (a) or `category` (b) is REJECTED at registration; `max_runtime=0` / two-trigger spec (c) rejected | Validator accepts the invalid spec | rejection pin fires (spec must NOT register) |

### 2.3 Non-negotiables

- Every leg names its defect explicitly in the pack (self-documenting matrix, reviewable without running).
- Detection-power disclosure per leg: which mutations are known NON-discriminating and why (disclosed, never silent).
- The matrix runs only in the worktree `.venv`; never main `.venv`.

---

## 3. DORMANT-DEFAULT VERIFIABILITY

### 3.1 Unit-level (T8)

- **D-3 ADOPT (AMD-3c — supersedes the pre-ruling "default OFF semantics" wording): ships `enabled=true` + `shadow_mode=true`; dormant-default = registered observer specs, zero executables, shadow rows accumulating; per-job `USE_ENGINE` all unset.** Default config ⇒ DORMANT: registrations accepted (catalog populated); observability surfaces live (per-kind counters present at zero); shadow rows accumulate BY DESIGN (one `SKIPPED_SHADOW` row per due job per interval — D2.13).
- `ENSEMBLE_ENGINE_ENABLED` explicitly OFF ⇒ engine constructs, tick loop runs, executes NOTHING (the one-flag full-off; unset resolves to the shipped `enabled=true` default per AMD-8's absent-vs-malformed rule).
- Registration without any `SERVICES_<MODULE>_USE_ENGINE` ⇒ zero dispatch attempts.

### 3.2 Boot-smoke proof (`engine_dormant_boot_smoke_test.sh`, dev lane)

Three boots in ONE run, all fenced: disposable PG cluster (`initdb -A trust`, `createdb` over TCP, `$$`-suffixed dirs, scratch DB asserted ≠ `ensemble_prod`/`ensemble_dev`), ALL FIVE `POSTGRES_*` + `ENSEMBLE_SELF_ENV=dev` pinned explicitly in the boot env, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned, daemon booted from tree-under-test via the worktree `.venv` (tree identity re-verified via `/proc/<pid>/cwd`), evidence to a `.txt`.

1. **Default boot (dormant):** **N observer specs registered, zero executables (`execute_fn` withheld), shadow rows present** — `engine_runs` carries ONLY `outcome=SKIPPED_SHADOW` rows (one per due job per interval, per D2.13/AMD-1); all `SERVICES_*_USE_ENGINE` unset; every per-job dispatch counter == 0; event-kind catalog QUERYABLE (registered kinds listed via the observability surface); per-kind counters wired (present, at zero); one pre-existing legacy service still ticking (legacy loop undisturbed). ⇒ proves "registered, observing, executing NOTHING."
2. **`SERVICES_<CANARY>_USE_ENGINE=1` boot:** synthetic canary job (pilot-shaped — NOT a module migration; no module migrates in Phase 1). Asserts EXACTLY one engine job activates: `engine_runs` rows carry the canary id only, all other jobs stay 0, and the canary's legacy loop is suppressed (single-executor).
3. **Flag-off boot:** engine job unregistered, legacy loop resumes — **rollback = one flag, proven end-to-end** in the same run.

Final line `GATE: PASS`/`GATE: FAIL`; exit code matches.

---

## 4. WATCHDOG DRILL (who-watches-the-watchman)

**Design constraints (pinned by T10):** the watchdog is ENGINE-INDEPENDENT — a **daemon thread** (`threading.Thread(daemon=True)`, the `daemon/services/wedge_capture/watchdog.py:1-13` precedent), **NEVER a coroutine** (a loop-scheduled detector cannot see loop starvation, the exact engine-tick wedge it exists to catch). Its state channel is a shared atomic — the engine writes its last-tick monotonic, the thread reads lock-free (never an engine API call) — and its recovery action is engine-independent (direct DB re-arm / fresh supervisor, never `engine.register()` on the wedged engine). It is NOT the readyz composite (readiness stays standalone per decision-report §A.6.3) and is NOT hosted by the engine it watches. **[SUPERSEDED SHAPE — round-2 fold R-2, 2026-11-21:** the earlier asyncio-loop wording ("a plain asyncio loop owning its own task, modeled on the readyz-refresher loop (`api.py:5197`, first tick t=0, fail-soft per tick `:5219-5223`) + the `JobFeedbackObserver` health-check pattern (`job_feedback_observer.py:384`, `observer_health_check_interval_seconds`)") was the RESEARCH-TIME shape, retained here as research history; the BUILT watchdog is a daemon THREAD per AMD-2/D4.6 — a loop-scheduled detector cannot detect loop starvation, the exact engine-tick wedge the watchdog exists to catch.**]**

**DRILL procedure** (`engine_watchdog_drill_test.sh`; "drilled" = reproducible procedure + captured evidence file, not a claim):

1. Boot dev-lane daemon from tree-under-test: engine dormant, watchdog armed, canary registered with `SERVICES_<CANARY>_USE_ENGINE=1`.
2. Inject fault — **leg A (stall):** canary `execute_fn` hangs past `max_runtime`+grace; **leg B (death):** cancel the engine tick task outright (PEXIT shape).
3. Assert the chain **detect → log → recover**: watchdog flags the job within its interval+grace (`dead`/stall record), logs at WARNING+ with job id + reason, stamps `TIMED_OUT`/`FAILED` per timeout policy; for leg B the ENGINE-INDEPENDENT recovery path re-arms the job per backoff WITHOUT the dead tick task — that independence is the point of the drill.
4. **Watchdog self-observability:** the watchdog's own health heartbeat (observer-pattern counter) is asserted CONTINUOUS throughout the engine outage — while the watched engine is dead, the watcher's telemetry stays live.
5. **Evidence artifact:** `/tmp/engine_watchdog_drill_$$_<ts>.txt` (`.txt` not `.log` — `*.log` is gitignored) containing: boot lines, fault-injection marker, detection line + timestamp, recovery lines, heartbeat continuity, final state. The pack asserts ON the file content; drill pass = the detect→log→recover chain present with watcher liveness throughout.

---

## 5. BOOT-SLO ACCEPTANCE GATE

- **Contract (Emperor, 2026-11-21):** boot_epoch → readyz-green **≤ 120s** WITH the engine registered-dormant. "Boot wall clock is 2 minutes."
- **Measurement method (AMD-6 — applied approver iteration-001, 2026-11-21; anchors verified at this tree):** start = the **harness-owned HOST-frame start anchor** — unit `ActiveEnterTimestamp` (`systemctl show -p ActiveEnterTimestamp`) for systemd-managed boots, or the launcher `starting: <binary>` line write time (`data/launcher.log:3` shape) for the bare-restart class (the gate pack captures `ActiveEnterTimestamp` in its evidence for that class); end = `/readyz` green (`daemon/api.py:6598`, cached composite; refresher first tick t=0 `:5215-5217`) — poll until `status == "ready"`; delta = `readyz_green − boot_start`, **both anchors host clock — ONE frame, never a mixed-clock subtraction** (boot-slo-measurement.md §2 + §5(a) hard prohibition). The gate wires its own timed probe (parse the host-frame start anchor from the captured boot evidence, poll `/readyz` until green, record the wall clock). `boot_epoch` → first-ready DB stamp = **TELEMETRY ONLY — never a gate input** (the ~4.0–4.7 s DB/host skew makes any mixed subtraction an inflated false-fail; the literal-`boot_epoch` gate stays the routed follow-up, decisions.md Part D item 3). **No dedicated boot-duration telemetry exists today** — the daemon-side readyz-green emit (`api.py:1082-1093` deferred-task pattern) stays the NAMED FALLBACK (ruling (d)) if promote-lane capture proves brittle or the host is non-systemd. The pack records the measurement in its evidence file. **[SUPERSEDED — AMD-6, approver iteration-001, 2026-11-21:** the earlier construction here ("start = the boot-epoch stamp log line `daemon/services/boot_epoch.py:132` (`logger.info(\"Boot epoch captured (DB clock): %s\", …)`); … parse the epoch line from the captured boot `.txt`, poll `/readyz` until green") was the MIXED-FRAME probe — a DB-clock start subtracted from a host-clock end, the exact subtraction D-2 forbids; retained as research history.**]**
- **Must-not-regress:** current baseline ≈10–30s typical per dispersed evidence; the FORMAL baseline lives in the sibling doc **`boot-slo-measurement.md`** (cited by name; not duplicated or re-derived here). Gate = ≤120s hard AND within the noise band that doc establishes; any engine-attributable delta is measured, not assumed.
- **Adjacent pin that must not be perturbed:** the boot-epoch→readyz queue-freshness amnesty contract, `tests/e2e/test_promote_stop_frozen_amnesty_e2e.py:11/:158` — the engine's boot phase must not alter amnesty semantics (that file is also the 120s breach-point precedent).

---

## 6. HOUSE TEST DISCIPLINE (binding on the implementation lane)

1. **Net-new proof:** FAILED-list diff vs TRUE pre-fix base with the SAME commands; 3× isolated order+load reds before a pin is called stable.
2. **Pytest scope:** the four dirs (integration/postgres/slow/conformance) are deselected by default — always pass EXPLICIT `tests/` lists (from `engine_arc_phase1_pins.txt`); never rely on testpaths fallback.
3. **QUARANTINE pre-check:** per failing file, grep `.agents/tester/QUARANTINE.md` + `git diff base..HEAD <file>`; empty diff = commission untouched (pre-existing red, do not "fix" in-lane).
4. **Timeout sizing:** default shards ≈300s wall budget; shard-3-class (boot smokes, drill) need `timeout 600`. Pytest-side `--timeout=120` per node in the matrix children.
5. **Node-ids-with-spaces:** parametrized ids break word-splitting — `tr '\n' '\0' < items | xargs -0 pytest …`.
6. **Shard polluter:** `tests/integration/test_migration_e2e_comprehensive.py` sets `POSTGRES_DB` at MODULE IMPORT (pollutes its whole pytest process even when deselected) — run it, if ever, in its OWN pytest process; never in the engine-pin shard.
7. **`--ignore`/`--ignore-glob` are NO-OPs on explicitly-passed file args** → use `--deselect <file>`.
8. **Worktree `.venv` ONLY** (`uv` at `/home/nea/.local/bin/uv` — export PATH); never main `.venv`; no bare `python`.
9. **Port 8079 is lane-shared:** daemon boots staggered or `DAEMON_PORT`/`PORT` overridden explicitly per boot (ambient `PORT` rebinds via repo-root `config.yaml` — export PORT explicitly).
10. **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned EXPLICITLY** on every daemon-child boot smoke (never `env -u` — it strips the ambient guard and the sweep kills LIVE processes).
11. **Evidence transcripts named `.txt`** (never `.log`); `/tmp` scratch names PID/`$$`-suffixed.
12. **Dev fence:** pin ALL FIVE `POSTGRES_*` + `ENSEMBLE_SELF_ENV=dev` (single-DB override is insufficient); `env -u POSTGRES_*` is a silent NO-OP — scrub per-variable.

---

## 7. FENCES THE SUITE MUST NOT BREAK (verified at this tree)

| Fence | Location (this tree) | Rule for engine work |
|---|---|---|
| `_NAME_RE` dormancy fence (HARD) | `tests/test_resource_control_seam.py:294-346` | Only `daemon/models/resource_monitor.py` may name `action_by`/`acted_at`; the `resource_pid_map`+`action` string-token combination is banned outside the flusher seam. Engine code/tests must not introduce these names. The `_NAME_RE` pattern is the house style IF the engine adds its own dormancy fence (names `engine_jobs`/`engine_runs`/`ENSEMBLE_ENGINE`/`job_metrics` are currently UNCLAIMED — zero fences exist on them). |
| Boot-sweep behavior pins | `tests/test_opt_o8_stale_subprocess_sweep.py:33-40` — **CORRECTION: the file is `opt_o8`, not `opt_u8` as the commission drafted** (re-grepped; the draft authorized re-grep) | The engine's BOOT_SWEEP class must keep the sweep's default-ON + bidirectional env-override semantics (it ADDS a deadline; it must not flip defaults or polarity). |
| Ram-guard / sweep env fence | `tests/helpers/ram_guard_fence.py:8/:30`; `tests/test_bind_retry.py:262-293`; pack-level `tests/packs/ram_guard_phase6_mutation_matrix.py:27/:299` | The explicit-pin discipline (`=0` pinned, never `env -u`) is itself fence-pinned — engine smokes reuse the helper, never re-implement it looser. |
| Readyz / readiness pins | `tests/test_health_probes.py`; `tests/unit/tools/test_readyz_grace_detail.py`; `tests/unit/services/test_boot_epoch.py`; `tests/e2e/test_promote_stop_frozen_amnesty_e2e.py:11/:158`; (also `tests/e2e/test_auth_lifecycle.py:142-166/:443`) | Engine registration must not perturb the readyz composite components, the epoch capture, or the amnesty contract. Boot-SLO gate READS these surfaces; it must not mutate them. |
| Queue admission invariant | `daemon/repositories/job_queue/models.py:51-58` (`ACTIVE_ADMISSION_STATES`) | If any engine table/logic touches `job_queue_items`, membership filters route through the constant — never hand-rolled `IN ('queued','active')` literals. (Watch-item: a grep pin for raw literals is a candidate follow-on, routed decision.) |

**Conflict protocol:** if an engine design need collides with any fence above, it is named as a ROUTED DECISION in the phase plan — fences are NEVER edited in-lane.

---

## 8. EFFORT ESTIMATE (test-phase workstream only)

Boundary: engine CODE (including the watchdog implementation and any boot-telemetry line the build adds) belongs to the build phases; this estimate covers authoring pins/packs/drills + mutation proofs + registration rows.

| Component | Low | High |
|---|---|---|
| Unit pin suites (T1–T10, ~71–107 nodes) | 7h | 12h |
| Mutation matrix harness + 23 legs (plant → RED proof → restore-GREEN proof, per leg) | 7h | 12h |
| Dormant boot smoke pack (disposable PG + 3 boots + single-executor assertions) | 4h | 7h |
| Watchdog drill pack (procedure + evidence + content assertions) | 3h | 5h |
| Boot-SLO timed probe wiring + measurement capture | 2h | 3h |
| PACKS.md registration rows + detection-power proofs (F/P vs base + vs mutations) | 3h | 5h |
| House-discipline compliance (shard sizing, QUARANTINE pre-checks, evidence hygiene) | 2h | 4h |
| **Total** | **28h** | **48h** |

~15–22% of the 118–222h total band; the high band carries contingency. Dispatcher reconciles the phase sum with the sibling plan's numbers.

---

## 9. Open questions / routed decisions (non-blocking)

1. **Engine module location** (`daemon/services/engine/…` vs sibling file): test import paths in T1–T10 adjust at implementation; file NAMES are the stable contract.
2. **New-names fence (routed decision, optional):** whether Phase 1 adds a `_NAME_RE`-style fence over `engine_jobs`/`engine_runs`/`ENSEMBLE_ENGINE*` names (house style exists; names currently unclaimed). Default: defer to the implementation lane's phase plan.
3. **Boot-SLO probe carrier:** pack-side timed probe (parse `.txt` + poll `/readyz`) vs build-side readyz-green log line via the `api.py:1082-1093` deferred-task pattern. Default: pack-side first (zero production-code coupling); promote to a build-side line only if the sibling plan already touches that seam.
