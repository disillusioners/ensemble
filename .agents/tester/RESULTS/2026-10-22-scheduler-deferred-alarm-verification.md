# Commission `scheduler-deferred-alarm-delivery` — Final Functional Gate Verification

- **Date:** 2026-10-22 · tester lane (this document)
- **Tree:** worktree `/home/nea/ensemble-src-wt-sched-deferred-alarm`, branch `feature/scheduler-deferred-alarm-delivery` @ `8f06c7f921c1a13eeab36600dfffac90c25c723f` (FROZEN — never pulled/merged/rebased; HEAD verified unmoved at every lane boundary)
- **Version at HEAD:** 0.27.1 · **Migrations:** 108 at tip, **our delta +0** (incoming: `20261021_120000_create_instance_ops_audit.sql`, `20260622… 20261022_000001_create_mcp_gateway_audit.sql`)
- **Tree state at close:** `git status --porcelain` == exactly ` M .agents/shared/planning/designer-agent/install-audit.jsonl` (cross-lane, excluded, untouched); 0 untracked files; byte-identity of every mutation-touched file proven (`git hash-object` == `HEAD:<path>`)

---

## VERDICT: **PASS-WITH-DISCLOSURES** — release lane

**Go/No-Go: GO** for merge-into-latest + stage-v0.27.2, with the disclosures in §6/§7 (none blocking; R15 plan-wording correction + two pin-hardening notes recommended as riders).

**Basis (one line each):**
- E2e Receipt 1 (D1): **PASS** — 2/2 independent runs, 40/40 assertions each, ~105 s.
- E2e Receipt 2 (D6): **PASS** — 60/60 assertions, 140 s (pause-between-turns shape; mid-turn branch not exercised — disclosed).
- Acceptance suite: **47/47 PASS** (223.22 s).
- Neighborhood sweep (46 files): 853 collected / 839 passed / **9 failed, all attributed — 0 new reds**.
- Mutation matrix: **31 targets / 34 plant cycles → 32 RED + 2 mutation-sensitivity findings** (both behaviors covered at sibling sites); every cycle restored byte-identical, every GREEN re-run passed first attempt, zero flakes.
- Disclosure re-verification: **7/8 TRUTHFUL; R15 MISMATCH** (wording over-claims; the code behavior itself is real and sound).
- Live system untouched throughout (MainPID 2199019, NRestarts 0, identical before/after every lane; no cgroup contact; no prod DB contact).

**Five most important evidence lines:**
1. D1 delivered body (scratch PG, verbatim): `[DEFERRED — 3 occurrences coalesced] The scheduled alarm could not be delivered at the fire instant: the target instance was running. It is queued as the target's NEXT turn and carries the live count of missed occurrences (3).` + `scheduler.deferred_count=3`.
2. D1 real-graph proof: mock-minted nonce `1c08ae2f4e4b4129` present in **3 `checkpoint_blobs` rows** for the alarm turn (11 checkpoints total) — no synthetic insert can place a nonce in the LangGraph blob store.
3. D6 first-turn-after-resume (daemon log): `17:13:04 [TRACE] _process_next_job: found PENDING job b84319f8… instance=9c130faa…` → `Processing message task 2` → `[LLM][9c130faa] Invoking LLM (STANDARD) with 4 messages`; delivered body verbatim carries `[DEFERRED — 1 occurrence coalesced] … (1)`.
4. Mutation kill-shot (B1, outcome-first): `assert breach == adapter.FIRE_OUTCOME_REFUSED_PERMANENT` → *"the 24h age cap did NOT trip on the untracked episode (outcome=None)"* — fired on the OUTCOME, not the clock value.
5. W-A polarity proof: `return True` guard → **Leg 2** RED (*"the LATER episode's legitimate terminal row was suppressed"*) while Leg 1 passed; latest-row-only guard → **Leg 1** RED (*"the duplicate terminal row was written for the SAME episode"*) — exactly the two predicted polarities.

---

## 0. Frozen-tree invariants & live baseline (recon lane, read-only)

| Check | Result |
|---|---|
| HEAD / branch | `8f06c7f921c1a13eeab36600dfffac90c25c723f` / `feature/scheduler-deferred-alarm-delivery` ✅ |
| Porcelain | exactly 1 line — the excluded cross-lane artifact ✅ (re-verified at every lane boundary) |
| Migrations | 108 files; both incoming files present; feature commit touches none ⇒ **our delta +0** ✅ |
| Version | pyproject + `daemon/__init__.py` = 0.27.1 ✅ |
| .env fence | `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_DB=ensemble_dev` ✅ |
| Live daemon | `ensemble-main` MainPID **2199019**, NRestarts **0**, epoch 2026-10-22 05:25:49Z, cgroup `/system.slice/ensemble-main.service`, `:9797/livez` = 0.27.1 — identical before AND after every lane ✅ |
| Informational | live is v0.27.1; project `promote_state_2026_10_21` metadata key still reads v0.26.3 (stale pre-ceremony record) |

Count reconciliation: the reviewer handoff's "29 targets" is an ITEM count (13 plan + 11 review + 5 adjacent); the tree holds **32 test functions** — plan items expand to 15 functions (pin-2 L-1 sibling, pin-4 ADR-3 sibling, U4 safety-net leg) — 31 mutation targets + the substrate-bounded `TestPausedCaller` decision pin held out of the matrix by design.

---

## 1. E2e receipts (both on real PostgreSQL 16, isolated `systemd-run` transient units, scratch CWD + scratch PG role/DB + `mktemp` FAKE_HOME, mock LLM only, never inside the live daemon's cgroup)

### Receipt 1 (D1) — PASS · 2/2 runs · 40/40 assertions each · ~105 s

Scenario: real instance busy in a live `graph.astream` turn (paced mock LLM) → alarm fired 3× while busy → coalesced → delivered+executed as the NEXT serialized turn → control message third.

| # | Assertion | Evidence (short verbatim) |
|---|---|---|
| A1 | All 3 fires deferred, none dropped/skipped | `schedule_executions`: 3 rows `deferred-…`/`deferred`/`busy_caller: instance still running`; journal: fire 1 = the only enqueue (`scheduled fire DEFERRED to the pinned caller … generation=1`), fires 2–3 = `deferred fire coalesced into the pending delivery … (count=2)`/`(count=3)`; exactly 1 keyed JobItem + 1 Task + 1 MQ row (`source='scheduler'`) |
| A2 | Live count in body | body verbatim: `…Original scheduled task: d1 deferred alarm payload… [DEFERRED — 3 occurrences coalesced] … carries the live count of missed occurrences (3).`; metadata `scheduler.deferred=true, scheduler.deferred_count=3` |
| A3 | NEXT serialized turn, in order | busy Task `17:11:07.61→17:12:04.07 completed`; ALARM Task created `17:11:17.02` (during busy), started `17:12:08.01` (after busy completed), completed `17:12:09.33`; control third; SQL `busy.completed_at <= alarm.started_at → 1`; MQ rows same order (`api`→`scheduler`→`api`) |
| A4 | Real `graph.astream` | mock-minted nonce `1c08ae2f4e4b4129` in 3 `checkpoint_blobs` rows; `checkpoints`=11; alarm MQ row `completed`; alarm turn prompt = the deferred body (mock sha16 ties prompt↔reply) |

Isolation/teardown: daemon pid cgroup `0::/system.slice/sched-alarm-d1-20261022171036-2805079.service` (asserted pre-signal); live table MainPID 2199019/NRestarts 0/`ensemble-main.service`/livez 0.27.1 **identical before/after both runs**; scratch role+DB DROP-verified gone (0 rows `pg_database`/`pg_roles`); unit gone; ports 10020/10021 free; fence self-test refused a deliberate `ensemble_prod` pointer. Pack: `/tmp/sched-alarm-d1-20261022/d1_e2e_test.sh` (+ `d1_mock_llm.py`); bundles `/tmp/sched-alarm-d1-20261022170823/`, `/tmp/sched-alarm-d1-20261022171036/`.

### Receipt 2 (D6) — PASS · 60/60 · 140 s

Scenario: real HTTP/tool path — instance tree (caller linked via `instances.parent_id`) → schedule (`reuse_instance`, `recurrence=once`) → self_alarm-shape binding via the REAL repository call (`create_instance_mapping`, metadata `{"source":"self_alarm","bound_to_caller":true,…}`) → alarm fires at the busy caller → **pause cascade** → 23 s gated window → **resume**.

| # | Assertion | Evidence (short verbatim) |
|---|---|---|
| P1 | Task persists through pause | pre-pause trio `task: 2|b84319f8…|pending` / `job: queued` / `mq: ready` / `exec: deferred-145fd59e…|deferred` — intact post-pause (`task_after_pause: pending||` — `started_at` empty ⇒ never claimed, never failed) |
| P2 | Gated while paused | 23 s window: `mock_requests_at_start: 2 → at_end: 2`, `alarm_bearing_requests: 0 → 0` (**delivery-attempt evidence — mock-LLM request log — not row counts**); instance `paused` throughout |
| P3 | Lands FIRST turn after resume | resume 200 → `_process_next_job: found PENDING job b84319f8…` → `Processing message task 2` → `[LLM][9c130faa] Invoking LLM (STANDARD) with 4 messages`; first alarm-bearing request (`req-003.json`) body verbatim `[DEFERRED — 1 occurrence coalesced] … (1)`; Task `pending→running→completed` |
| P4 | Real graph | `checkpoints` for thread `9c130faa…` = 10 rows written by the live daemon + LLM invocation line |

Bonus — **D6 DEC-11 observed live**: a second alarm fired AT the paused caller produced `exec: d2b4737e…|queued` (NOT `deferred`) + a PENDING delivery queued behind the gate; post-resume it delivered a DIRECT body (no DEFERRED marker) on the second post-resume turn (FIFO) — PAUSED is not in the busy set ⇒ the fire DELIVERS, exactly the decision the substrate pin encodes.

Shape disclosure: the pause landed **between turns** (caller resting in `running` with the delivery pending); `resume_results:{target:{"status":"no_active_job"}}` proves the release was the gate (ADR-7 mechanism), not a suspended-turn resume. The **mid-turn pause branch** (cancel-at-node-boundary / DB-only resume / `is_retry` from checkpoint) was NOT exercised — see §7.

Isolation/teardown: cgroup `0::/system.slice/sched-alarm-d6-20261022171124-2806169.service` asserted pre-signal + pack refuses any pid in `ensemble-main.service`; live before/after identical (incl. demo PID 898, `current → releases/v0.27.1`); scratch DB/role verified gone (0/0); ports 10040/10041 free; 0 leftover units system-wide. Pack: `/tmp/sched-alarm-d6-20261022163555/sched_alarm_d6_e2e_test.sh`; bundle `/tmp/sched-alarm-d6-20261022171124-2806169/`, final log `/tmp/sched-alarm-d6-20261022163555/final_run.log`.

---

## 2. Mutation matrix — 31 targets / 34 plant cycles · **32 RED / 2 sensitivity findings** · every cycle byte-identical-restored + GREEN

Protocol per cycle: plant → run pin (`timeout 300`, per-variable `POSTGRES_*` scrub, single node, `-x -q --tb=short`) → RED with predicted symptom → `git checkout -- <file>` → `git diff -- <file>` EMPTY + porcelain == 1 excluded line → re-run GREEN. No mutant ever committed; no mutant survived a completed cycle; the one interruption-leftover mutant (sweep 2, T26-P2) was found and restored byte-identically at recovery STEP 0. GREEN re-runs: 34/34 passed first attempt, zero flakes.

**Standing markers (binding):**
- **W-C (F-N2): NOT-MUTATION-PROVEN** — `daemon/sources/adapters/scheduler.py:1827-1831`, the null-repository guard in `_coalesce_into` ⇒ `_COALESCE_WRITE_UNKNOWN`. Production-dead conservative branch, unpinned by design (mutating it back leaves tests green); not mutated, not cleaned up.
- **F5 controls** (enqueue-attempt counters, pin file :3145/:3265): wiring, NOT mutation targets — untouched in both sweeps and positively proven wired (`attempts == [1]`).
- **Probe file** `tests/unit/test_scheduler_deferred_mechanism_probe.py`: diagnosis-only, NO mutation specs — never mutated.
- **F-N1** is Leg 3 inside H3(i) (target 23B below) — covered.

### 2.1 Plan-anchored (sweep 1 — 15 functions, 16 cycles)

| # | anchor (pin) | plant | RED symptom (short verbatim; assertion line) | GREEN |
|---|---|---|---|---|
| 1 | §(e)1 D1 | old busy branch restored (SKIPPED callback + bare return) | `:810 PIN 1 RED: no live (non-terminal) Task exists for the busy caller — the alarm was dropped (tasks=[])` | 5.78s |
| 2 | §(e)2 D2 | audit-row start+complete legs short-circuited in `_defer_delivery` | `:895 …expected exactly one schedule_executions row for the deferred fire, got 0` | 5.29s |
| 3 | §(e)2 L-1 | enqueue-first order + failure leg not terminal | `:957 …expected record_execution_start BEFORE enqueue_message_job, got order=['enqueue_message_job','record_execution_start']` | 13.72s |
| 4a | §(e)3 D3 | `update_ready_content` status-guard removed | **STAYED GREEN — FINDING F-M1** (pin fixture never claims the row; see §5) | — |
| 4b | §(e)3 D3 | coalescing re-enqueues instead of in-place update | `:1094 …expected exactly 1 live (non-terminal) Task, got 3` | 6.67s |
| 5 | §(e)3 B3 | liveness gate narrowed to PENDING-only | `:1243 …expected still exactly 1 MessageQueue row, got 0` (one-shot stable key ⇒ rollover = dedup hit ⇒ claimed delivery destroyed — mechanism note §7) | 4.83s |
| 6 | §(e)4 W3/D5 | unconditional post-`_emit` consumption + deferral enqueue suppressed | `:1356 …left NO durable pending delivery (pending tasks=[]) — consumed-and-lost, the original defect` | 5.51s |
| 7 | §(e)4 ADR-3 | no-target branch consumes (`_is_one_time_executed=True` + callback) | `:1414 …the no-target re-arm leg CONSUMED the one-shot (enabled=False, status='stopped') — ADR-3 violation` | 7.74s |
| 8 | §(e)7 Blocking-1 | classification on `message_id` alone (Task-existence predicate removed) | `:1512 …no 'failed' execution row was written for the marker-window fire (rows=[('deferred','busy_caller…')])` | 12.07s |
| 9 | §(e)8 | consume unconditionally on breach (abandon guard bypassed) | `:2455 …a 'nondelivery_age_cap' failed row was written although the guard returned NO row — the breach consumed a delivery that was about to land` | 12.96s |
| 10a | U4 | substrate terminal-bypass mint removed (`job_queue_service.py`) | **STAYED GREEN — FINDING F-M2** (`v2` keys never enter the branch — pin's own Q2 ruling; see §5) | — |
| 10b | U4 | adapter rollover reuses key verbatim (`key_gen = gen`) | `:1682 …key '…deferred:v1#9431080c' is not the NEW generation key '…deferred:v2'` | 5.64s |
| 11 | U4 safety-net | safety-net mint removed | `:1846 …expected exactly ONE fresh delivery under the synthetic key, got 0` | 4.83s |
| 12a/12b | W7(i) | add `PAUSED` / drop `WAITING_CHILDREN` (both variants) | `:1918 …_is_instance_active for status 'paused' returned True` / `…for status 'waiting_children' returned False` | 6.23s |
| 13 | W7(ii) | defer path advances `_run_counter` | `:2028 …deferred fire advanced _run_counter to 1 — deferrals must be invisible to the dispatch counter` | 4.85s |
| 14 | W7(iii) | `MARKER-MISS FALLBACK` branch removed | `:2132 …the fallback bumped the generation — a dedup hit must resume the SAME generation, not roll over` | 5.20s |
| 15 | ADR-9 | old drop-on-busy restored for WAITING/WAITING_CHILDREN | `:2200 …a fire at 'waiting' did NOT defer (deferred rows=[])` | 7.45s |

### 2.2 Review-anchored (sweep 2 — 11 targets, 12 cycles, 12/12 RED)

| # | anchor | plant | RED symptom (short verbatim; assertion) | GREEN |
|---|---|---|---|---|
| 16 | Q1-3a | delivered-occurrence leg falls into no-row-no-consume | `:2584 …the carrier was not cleared (…'2026-10-21T16:52:07…') — the delivered episode must end` | 4.75s |
| 17 | Q1-3b/c | no-marker leg takes the delivered path | `:2678 …expected exactly one terminal 'failed' row with reason 'nondelivery_age_cap', got []` | 4.57s |
| 18 | C2 claim-window | Task cross-check removed (`if False and …`) | `:2802 …a 'failed' row was written for a delivery that is CLAIMED and WILL land` — **the false-failed-row assertion fired** (not the weak "no consume") | 12.17s |
| 19 | C1 leg 1 (re-arm) | hoist dropped (`breach=None`, call commented) | `:2935 assert len(breach_calls) == 1` → *"the age-cap breach was evaluated 0 times in ONE fire"* (hoist = the only pre-semaphore site; 0 calls ⇒ no cap ⇒ spin; termination leg masked by first-assertion abort) | 4.92s |
| 20 | C1 leg 2 (direct-`failed`) | same de-hoist | `:3063 …two uncapped attempts, zero 'nondelivery_age_cap' rows` | 4.54s |
| 21 | H1 | coalescing-write exception → `_COALESCE_ROLLOVER` (except-branch only; W-C untouched) | `:3199 assert enqueue_attempts == []` → *"a fresh enqueue was ATTEMPTED (1 attempts) after the coalescing write raised"* (F5 control passed) | 4.59s |
| 22 | H2 | liveness-read exception → `return False` | `:3311 …a fresh enqueue was ATTEMPTED (1 attempts) after the liveness read raised — an unknown liveness is not 'not live'* (F5 control passed) | 4.89s |
| 23A | H3(i) | failed carrier OPEN treated as success | `:3460 (LEG 2)` → *"no 'episode_open_failed' ERROR was logged — the untracked episode is not visible"* | 16.84s |
| 23B | **F-N1** (H3(i) Leg 3) | missing-row (`apply_scheduler_defer_state → None`) reported SUCCESS | `:3517 (LEG 3)` → *"no 'episode_open_failed' ERROR for the MISSING-ROW carrier write — a None repository result was reported as a success"* | 17.63s |
| 24 | H3(ii) | failed carrier CLEAR treated as success | `:3598 …no 'episode_clear_failed' ERROR was logged — the lingering episode is not visible` (duplicate-row leg independently guarded by W-A — loudness is the discriminator here; disclosed) | 4.31s |
| 25 | B1 | clock re-stamped unconditionally at BOTH set-sites | **`:3693 assert breach == adapter.FIRE_OUTCOME_REFUSED_PERMANENT`** → *"the 24h age cap did NOT trip on the untracked episode (outcome=None)"* — **the OUTCOME assertion fired first**, as required | 5.18s |
| 26-P1 | W-A polarity 1 | guard `return True` (any terminal row suppresses) | `:3879 (LEG 2)` → *"the LATER episode's legitimate terminal row was suppressed … terminates SILENTLY"*; Leg 1 passed — predicted polarity | 4.04s |
| 26-P2 | W-A polarity 2 | latest-row-only (`[:1]` + key ignored) | `:3821 (LEG 1)` → *"the duplicate terminal row was written for the SAME episode (2 rows)"*; Leg 2 masked by first-assertion abort — predicted polarity | 4.62s |

### 2.3 Adjacent (sweep 3 — 5 targets, 6 cycles, 6/6 RED)

| # | anchor | plant | RED symptom (short verbatim; assertion) | GREEN |
|---|---|---|---|---|
| 27 | D4 visibility | `deferred_count=`/`last_execution_status=` dropped from `_row_to_detail` | `:605 …D4 — the deferral visibility surface is ABSENT: … deferred_count=0, expected 3 … last_execution_status=None, expected 'deferred'` | 3.22s |
| 28 | D5 guard | transient dummy migration `99999999_999999_mutation_sweep_d5_ddl.sql` (`CREATE TYPE execution_status AS ENUM…`) — docstring-specified D5-as-DDL plant | `:839 …D5 — the additive-only contract is broken: migration file(s) create/alter an execution-status type` (legs 5 AND 6 fired); dummy deleted, untracked-clean verified | 1.60s |
| 29a | §(e)5 E1 | `bound_to_caller` refusal removed (delete+spawn restored) | `:680 Failed: DID NOT RAISE BoundCallerRefusal` + captured pre-fix log `"Deleting orphan mapping and creating a fresh instance."` | 3.61s |
| 29b | §(e)5 E1 | refusal token remapped `REFUSED_PERMANENT → FAILED` (token-only, :2556) | `:732 …the one-shot was NOT consumed on the refusal leg — a provably dead target stays armed and re-fires every 5s` + log *"the one-time fire was NOT consumed (outcome=failed)"* | 3.74s |
| 30 | §(e)5 sibling | `bound_to_caller` dropped from binding-key carry-forward | `:897 …E1 RED: the rebuilt instance-mapping DROPPED the binding keys ['bound_to_caller']` | 2.45s |
| 31 | §(e)6 DEC-21 | best-effort `return None` re-arm path restored | `:1001 Failed: DID NOT RAISE BoundCallerRefusal` + log *"…continuing best-effort (pre-fix behaviour)"* | 2.35s |

Byte-identity close-out (sweep 3, `git hash-object` == `HEAD:<path>`): `scheduling_service.py` `aff3b428e…` ✅ · `mapper.py` `f3551e690…` ✅ · `scheduler.py` `4d2659e60…` ✅ · `self_alarm.py` `8452fb5c3…` ✅. Final pin-file runs green (5 passed / 10.01 s).

---

## 3. Disclosure re-verification (plan §(f)) — 7/8 TRUTHFUL, R15 MISMATCH

| R-id | Verdict | Basis (code-anchored) |
|---|---|---|
| R0 | **TRUTHFUL** | one DEFERRED `schedule_executions` row per coalesced fire (`scheduler.py:1896` comment + `:1900/:1908` legs; cap only pins the DISPLAY `min(count+1,cap)` `:1832`) |
| R15 | **MISMATCH** | the widening is real (`:2528-2532` `… or REUSE_INSTANCE → _route_via_job_queue`) BUT: (a) "previously … dead: no MQ row, no Task, no delivery, no audit row" is FALSE (pre-fix immediate route wrote TRIGGERED/COMPLETED rows and delivered via registry `enqueue_message_job`); (b) "load-bearing for D1 / revert ⇒ inert" is UNSUPPORTED (D1 defers at `:2788-2803`, upstream of the routing gate at `_execute_run:2825`); (c) stale pointer `:1869-1877` → actual gate `:2524-2532` |
| R16 | **TRUTHFUL** | shared vocabulary + `DO NOT UNIFY (DEC-9)` comment at assignment `:1608-1616`; `nondelivery_age_cap` token `:137/:150-162`; E1 refusal path distinguishable (`mapper.py:450` → `scheduler.py:2564`) |
| R17 | **TRUTHFUL** | rollover `:1876-1877 → :2004-2019 → :2078-2086`; claim ordering Task-RUNNING (`task/repository.py:2799-2804`) before MQ-leaves-ready — residual present, bounded, not fixed, exactly as worded |
| R18 | **TRUTHFUL** | `_scrub_body` gate `:1837-1846` → `_COALESCE_SKIPPED` (guarded update unreached, stale count); seam `secret_persistence_scrub.py:1250` |
| R19 | **TRUTHFUL** | hoist unconditional and FIRST (`:2765-2770`, before semaphore `:2777` and busy check `:2788`); every-fire evaluation + visible `failed`/`nondelivery_age_cap` — the behavior widening is truthfully worded |
| R21 | **TRUTHFUL** | set-if-absent at `:2183`/`:2241`, retire at `:2252`/`:2279`, RAM-only `:438`; worst-case-REMOVED claim holds (nit: retire condition is `_defer_since`-bearing writes / episode clear, slightly narrower than "first successful carrier write") |
| R22 | **TRUTHFUL** | count-commit-failure tolerated (`:1878-1893`, `carrier_count_update_failed` token; body already rewritten `:1852-1857`); cap bounds DISPLAY only |

Precision notes (non-blocking, suggested rewordings recorded by the disclosures lane): R15 census clause time-bounded (post-D11 self-alarm rows DO carry `project_id`); R18/R19/R0 bounds are defaults (env-overridable `ENSEMBLE_SCHEDULING_DEFER_*`); R19 citation drift (`:2777-2782` → `:2765-2770`); R21 retire-condition breadth; R15 + `decisions.md` DEC-23 wording should move together.

---

## 4. Suite counts (merged tree)

- **Acceptance:** `test_scheduler_deferred_delivery_pin.py` + `test_mapper_e1_stranger_spawn_refusal_pin.py` + `test_self_alarm_deferred_visibility_pin.py` + `test_scheduler_deferred_mechanism_probe.py` → **47 passed / 0 failed** (223.22 s, exit 0).
- **Neighborhood (46 files, 8 chunks):** 853 collected / **839 passed / 9 failed / 0 errors** / 2 transient skips (B2, absent on identical re-run) / 3 deselected (repo `addopts` PG-marker parity nodes — consistent, one is a standing quarantined PG-arm node).
- **The 9 reds, all attributed:** base-red #1 `test_user_setting_rung_covers_once_anchor_site` (`assert None == '2026-10-04T23:00:00+00:00'` — date-dependent once-anchor past; CONFIRMED-KNOWN) · base-red #2 `test_router_forwards_queue_id_to_enqueue_message_job` (`TypeError: 'MagicMock' object can't be awaited` @ `messages.py:326`; CONFIRMED-KNOWN) · 7× standing quarantined `job_queue_proxy_phase1` derived-status-map family (base-attributed at 4 independent gates).
- **New reds: NONE.** Quarantine overlaps checked and disclosed; nothing silently skipped.

---

## 5. Findings register (adjudicated ours vs pre-existing)

| # | Sev | Finding | Adjudication |
|---|---|---|---|
| F-M1 | 🟠 pin-coverage | §(e)3 sub-mutation (a) — `update_ready_content` status-guard removal STAYS GREEN: the pin's fixture never claims the delivery (row stays `ready`), so the guard is invisible to this pin. Load-bearing coverage exists at sibling leg (b) (RED clean). | **Ours (pin fixture)** — recommend pin hardening: add a claimed-row variant so (a) discriminates. Non-blocking. |
| F-M2 | 🟠 pin-coverage | U4 leg-1 at the specified substrate site (`job_queue_service.py` synthetic-key mint) STAYS GREEN: the generation rollover mints a NEW key (`v2`) which never has a terminal predecessor, so the branch is unreachable for this fixture (pin's own Q2 ruling documents it). Coverage exists at sibling T11 + the adapter-site variant 10b (both RED). | **Ours (pin docstring site)** — recommend docstring re-point to the adapter rollover or a fixture that reuses a key with a terminal predecessor. Non-blocking. |
| F-D1 | 🟡 docs | R15 disclosure wording MISMATCH (§3): 2 over-claims + stale citation + DEC-23 twin wording. Code behavior is real and sound. | **Ours (plan text)** — reword at merge time (rider); tree frozen, no edit made here. |
| F-E1 | 🟢 pre-existing | Pausable-caller shape constraints (root auto-COMPLETES at turn end; never-dispatched ghost skipped by pause cascade) forced the e2e to model a live child session via direct `instances.parent_id` insert. | Pre-existing daemon semantics (`child_reports` root-completion + ghost skip) — not this commission's files. |
| F-E2 | 🟢 pre-existing | Post-resume delivery latency governed by the JobItem slot (~30 s; queue-awareness gate) rather than the resume instant; ADR-7-anticipated (3 s poll + 90 s sweep fallbacks). Correctness unaffected. | Pre-existing (`task/repository.py` outside the commission's files). |
| F-E3 | 🟢 INFO | Manual trigger twin `POST /api/schedules/{id}/trigger` still takes the legacy busy-SKIP (the deferred path exists only on the scheduled path). Matches the pin corpus; = designed DEC-3 non-goal. | By-design; promote-time docs note. |
| F-E4 | 🟢 INFO | Background title-generation replays the delivered alarm body into its own LLM call — naive "exactly 1 alarm-shaped request" counters false-positive. | Observability note only. |
| F-E5 | 🟢 INFO | Deferred-carrier metadata is NESTED (`scheduler.deferred_count`) — top-level `metadata->>'deferred'` reads NULL; trap for evidence queries. | Pack-side/environmental; pin corpus already asserts the nested shape. |
| F-P1 | 🟢 process | Two sweep-lane interruptions (upstream model deprecation `minimax-m3.1-flash-free`); each revived ONCE; the single interruption-leftover mutant (T26-P2) was caught and restored byte-identically by the recovery STEP 0 hygiene gate. Zero mutants survived; HEAD never moved. | Process disclosure — hygiene protocol proven under real interruption. |

Symptom-divergence notes (pins RED via an adjacent assertion than the docstring predicted — disclosed, not defects): T5 (one-shot stable key ⇒ rollover = dedup hit ⇒ destruction of the claimed delivery, not "second live delivery"), T14 (generation-bump assert `:2132` reachable; `:2120` unreachable for one-shot), T7 (consumption had to be planted inside the branch — the loop's consumption gate is never reached during the 5 s re-arm sleep), T24 (loudness leg discriminates; duplication independently guarded by W-A).

---

## 6. Limitations / not exercised (honest gaps)

1. **D6 mid-turn pause branch NOT exercised** (pause-between-turns shape only): cancel-at-node-boundary / DB-only resume / `is_retry`-from-checkpoint semantics remain covered by the existing pause/resume corpus, not by this receipt.
2. **R15 census clause** ("live self-alarm population carries no `_project_id`") is not statically verifiable without DB access — noted as time-bounded (pre-D11 rows only).
3. **E2e packs are /tmp-external by design** (frozen tree ⇒ zero untracked repo files): scripts at `/tmp/sched-alarm-d1-20261022/`, `/tmp/sched-alarm-d6-20261022163555/` are ephemeral; invocations recorded here and in PACKS.md.
4. Mutation sweeps prove discrimination at the sites the fixtures can reach; the two specified-site gaps (F-M1/F-M2) are covered at sibling sites instead.

---

## 7. Go/No-Go — **GO** (merge-into-latest + stage-v0.27.2)

Conditions/recommendations riding the GO (non-blocking):
1. **Docs rider at merge:** R15 rewording (+ DEC-23 twin) per §3; optional precision touch-ups R18/R19/R21/R0 wording and the R19 citation.
2. **Pin-hardening follow-up (test lane):** F-M1 claimed-row variant for §(e)3(a); F-M2 docstring re-point for U4 leg-1.
3. **Promote-materials note:** manual-trigger busy-SKIP (DEC-3 non-goal) — one line in release notes so operators don't expect "run-now on a busy caller" to defer.
4. No code changes requested by this gate; the tree is byte-identical to `8f06c7f92`.

---

## Appendix — lanes, instances, artifacts

| Lane | Instance | Outcome |
|---|---|---|
| Recon (read-only) | `f9022f8e` | invariants + full 32-function matrix + fence idiom |
| E2e D1 | `74909f18` | PASS 2/2 runs |
| E2e D6 | `5dc8011d` | PASS 60/60 |
| Disclosures | `a1afab0e` | 7/8 TRUTHFUL, R15 MISMATCH |
| Suites | `51989934` | 47/47 + 839P/9F-attributed/0-new |
| Mutation 1/3 | `73316e0b` | 13 RED + F-M1/F-M2 findings |
| Mutation 2/3 | `46eb38e3` (1 interruption, recovered) | 12/12 RED |
| Mutation 3/3 | `2592ddc8` (1 interruption, recovered) | 6/6 RED |

Evidence bundles: `/tmp/sched-alarm-d1-20261022170823/`, `/tmp/sched-alarm-d1-20261022171036/`, `/tmp/sched-alarm-d6-20261022171124-2806169/`; mutation notes `/tmp/mut-plan/`, `/tmp/mut-review/`, `/tmp/mut-adjacent/`; suite chunk logs `/tmp/r2_A{1..4}.log`, `/tmp/r2_B{1,2}.log`, `/tmp/r2_B2_rs.log`, `/tmp/r2_C{1,2}.log`, `/tmp/r3_node{1,2}.log`.

Live-safety record: `ensemble-main` MainPID 2199019 / NRestarts 0 / livez 0.27.1 verified identical before and after EVERY lane; no process ever killed by name without a bound-port check; ports 8088/9797/8079/7979 never bound by any lane; `releases/`, `current`, remote PG untouched.
