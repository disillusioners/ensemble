# Architecture Recommendation — wake-path-carrier-fix (Architect Gate)

**Date:** 2026-11-18 (analysis window 2026-11-17T23:25Z → 2026-11-18)
**Base:** `eca98397d9e0109611764ac418132c9d35afde2c` (verified by all three analysts via `git rev-parse HEAD` in `/home/nea/ensemble-src-wt-wake-path-carrier-fix`; tree clean except the untracked plan dir)
**Analysts:** architect-worker-f1-hooksite `643e807c` (data-flow-design) · architect-worker-f2-delivery `e10630af` (resilience-design) · architect-worker-f4-predicate `3bff6e0b` (trade-off-analysis). All citations below were measured at the pinned base by these workers; anything not measured is flagged UNVERIFIED.
**Status:** Complete. Three verifications HIGH confidence, two bounded UNVERIFIED items carried to implementation (§12).

---

## 0. VERIFICATION SUMMARY — the plan's evidence is real, two of its decisions are wrong

The plan's anchor register survives spot-checking almost entirely (fire-primitive census, arm-site guards, park predicate, watchdog anchors, healer legs, inert periodic sweep — all re-measured and confirmed). The plan is **wrong about two decisions, not about its facts**:

1. **The S1 hook site (plan §(a.2)(i), §(i) row 3, §7 Phase-1 task 3, risk 1, §12 Q1): `job_feedback_observer.py` is a measured miss.** `job_feedback_observer.py:2346` gates `_dispatch_instance_post_commit_side_effects` on `not db_result.instance_was_terminal`, and `:4482-4490` sets that flag True whenever the instance row is already terminal — which is the **normal** case, because `child_reports` stamps COMPLETED first and publishes the lifecycle event afterwards. The observer's post-commit path is skipped exactly when the plan needs it. Additionally, a non-root child never publishes a self lifecycle event (`child_reports.py:6369` publishes only for `completed_parent_id`; all 9 `_publish_instance_lifecycle_event` sites enumerated) — the observer is **structurally blind to the M-B branch** (child of a third instance). Plan risk 1 is not a risk; it is realized. **Correct hook: `child_reports._dispatch_post_commit_side_effects` (`child_reports.py:5285`)** — see §1.
2. **The S4 dedupe design (plan §(d.2)): reusing the 1800s window suppresses nothing and inverts documented semantics.** The watchdog's scan interval is 3600s (`daemon/config.py:2619-2622` default → `waiting_children_watchdog.py:1798/:1818` sleep loop); consecutive same-condition emissions are 3600s apart, so an 1800s window expires before every next scan. The observed 3600.5–3600.7s cadence (register L-9) is thereby mechanically explained. `WEDGE_REARM_COOLDOWN_SECONDS = 30*60` (`watchdog:197`, docstring `:188-196`) is a **re-arm allowance** ("a re-parked parent is re-eligible by the next tick"), not a suppression window. **Split the clocks** — see §5.

And one load-bearing fact the plan understates: **the S1 delivery hook is not a mechanism detail — it is the only wake a non-structural-parent target ever gets** (§2). Fire-only S1 is a regression, not a fix.

---

## 1. F1 — THE HOOK-SITE ADJUDICATION

### 1.1 Every COMPLETED-stamp site (exhaustive, measured)

| # | Site | Enclosing function | Root-lane? | Multi-turn safe? | Post-commit? |
|---|---|---|---|---|---|
| S-a | `child_reports.py:4029` | `_process_child_completion_db_sync` (`:3214`), **root branch** (gate `:3503`) | **YES — is the root branch** | YES (instance-keyed) | commits `:4024` |
| S-b | `child_reports.py:4248` | same fn, tool-invocation branch | YES | YES | YES |
| S-c | `child_reports.py:4414` | same fn, regular-child branch (`:4361-4440`) | no (requires parent_id) | YES | YES |
| S-d | `job_feedback_observer.py:4507-4520` | `_finalize_job_db_sync` (`:3672`) Step 2 | **effectively NO** (gate `:2346`, flag `:4482-4490`; normal path pre-stamped by child_reports) | YES | YES |
| S-e | `child_reports.py:1242` / `:5168` | `_update_parent_on_child_complete` (**zero production callers**, self-documented `:1055`) / inline twin | parent-only | YES | in-session |
| S-f | `error_reporting.py:337` | `_send_error_report_db_sync` | ERROR lane only | YES | in-session |

There is **no central instance-status setter** — six redundant writers by design; the repository forbids generic `update(status=…)` (`instance/repository.py:1795-1800`) and `transition_status_if` has zero COMPLETED callers. A "hook the one setter" design does not exist to hook.

### 1.2 The ruling

**S1's hook is `ChildReportsService._dispatch_post_commit_side_effects` (`child_reports.py:5285`), placed PER-OUTCOME at the END of each terminal branch — `regular_child_completed` before its `return` at `:6464` (AFTER the pair-emit at `:6256`), `tool_invocation_completed` before `:6192`, `root_completed` before `:6014` ONLY (NOT `:5622`/`:5694`, which demote a non-terminal instance) — fired when `result.outcome ∈ {root_completed, tool_invocation_completed, regular_child_completed}` and the instance id is non-null.** NOT at the top of the method: every canonical branch early-returns (measured `:5345/:5360/:5448/:5622/:5694/:6014/:6123/:6139/:6158/:6192/:6464`), so a top-of-method placement would pre-empt the pair-emit and inject a synthetic bus message to the structural parent on every healthy completion. Why this site wins:

- **It is the only site reached on both live branches.** The dispatcher is invoked for the root lane (`:5331+`), the tool-invocation lane (`:6162`), and the regular-child lane (`:6195`) — i.e. M-A (root child) and M-B (child of a third instance) both pass through it. The plan's own fallback stamps (`:4029/:4248/:4414`) live *inside* the sync helper, which holds `bus._get_parent_lock(instance_id)` across the whole call (`child_reports.py:3092-3117`) — the same lock `watch()` takes (`dependency_bus.py:640`). A fire issued there can self-deadlock; the dispatcher is the async sibling **outside** the lock window. Hook at `:5285`, not at the stamps.
- **It is multi-turn safe by construction** — the fire keys on `instance_id` (via the payload `follow_up_payload.metadata.child_id` matcher), never on a task id. The live shape (watcher `source_task_id` 39320 ≠ the child's final task) is covered regardless of lane or turn count.
- **The matcher carries NO staleness exclusion (leader ruling, iteration 003)** — `FIRE_SET` is the plain matching form `{w in PENDING : w.follow_up_payload.metadata.child_id == instance.instance_id}`. Do **not** add a `source_task_id`-terminal filter: on the live row `8a1f60d7` the source task is terminal 56 min **before** its watched child, so the filter would drop exactly the row the fix exists to fire. Firing every matching PENDING row is correct (each is a genuine waiter; two nudgers on one child both wake); a stale row fires once and leaves PENDING ⇒ at most one spurious wake, disclosed (plan risk 21). "Terminal" belongs to S3's fulfillable predicate, keyed on the **child's INSTANCE status**.
- **It is ungated on `instance_was_terminal`** — unlike the observer's dispatcher (`:2346`).
- **Cost, honestly:** `child_reports.py` is CONTENDED (`fix/empty-job-completed-event` @ `5368a12d4` +3). The plan's "uncontended primary layer" claim dies here; per cn `888d612d` the lane lands first and the stale owner reconciles onto our tip. This is a reconciliation cost, not a blocker — but it must be priced into Phase 1 (plan risk 5 upgrades from Medium to accepted-fact).

**The observer is NOT a belt by default.** A second S1 call in `job_feedback_observer._process_event` guarded on `status == COMPLETED` would cover only completions that bypass `child_reports` — a population none of the three analysts could demonstrate exists on the COMPLETED lane (the ERROR lane is separate; see residual R-3 in §6). Under the minimality doctrine (r9) the belt is **default-OFF**; the implementer turns it on only if a real bypass path is demonstrated.

### 1.3 Emit side vs resolve side vs mid-session resolver

- **EMIT (adopted):** the fire is issued at the moment of terminal transition by the completion path itself. Matches the TERMINATE precedent (`instance_lifecycle.py:385` → `fire_for_terminated_target`), is O(1) per transition, adds no polling, keeps the bus a pure state machine (the bus method returns FollowUps; the caller delivers). Exactly-once is carried by the guarded `WHERE state='PENDING'` UPDATE (`repository.py:717-791`, rowcount semantics; the same invariant the bus already documents at `child_reports.py:5415-5440`).
- **RESOLVE (rejected):** a lazy resolve inside the count/fetch reads would mutate on the hottest read path in the daemon (every park decision `message_processing_pipeline.py:1329`, every finalize gate `job_feedback_observer.py:3838`, the root defer `child_reports.py:3511`), requires an instance-status read per watcher row, and — decisively — **cannot deliver**: a read-path resolver that flips PENDING→FIRED without a delivery hook is precisely the silently-ignorant-parent regression of §2. It also inverts the bus's own contract (`child_reports.py:410-417`).
- **MID-SESSION RESOLVER (not adopted; named escalation):** a resolver integrated into MissionStrandHealer's existing passes (r6-compliant — the healer already reads the population at `mission_strand_healer.py:562`/`:1240` and refuses at `:570-574`) is the correct *escalation* if the backlog-row consequence (§6, R-2) is judged load-bearing. It is not needed for the measured defect: S1 covers future transitions, S3's qualified count cures backlog **parks**, and the healer's refusal gate is itself one of S3's switched readers (R4/R5).

### 1.4 F6's consequence for F1 (see §7 for the archaeology)

Three successive fixes each patched one *lane* at the arm/retry/terminate layer (2026-06-26 `4926a2eb6` retry-rekey cancel; 2026-07-21 `1655397298` multi-turn corrective scoped to the structural-parent pair; 2026-08-25 `06878b630` terminate-path fire). The class leaked for five months because **arm-layer patches cannot close a fire-layer gap** — every new arm family (the nudge lane, minted later with no tree guard and no self guard, `instance_ops.py:655/:729`) re-opened it. The durable fix is the missing primitive itself: a target-agnostic child-keyed fire on the COMPLETED lane. The archaeology **supports the plan's layer choice and refutes its site choice** — which is exactly what §1.2 corrects.

---

## 2. F2 — THE DELIVERY-SEMANTICS VERDICT (the most important section)

### 2.1 What the bus is, and is not

The bus is the **"is the wait over?" oracle**; the report lane is the **carrier**. On the healthy spawn-lane path (`child_reports.py:3220+`, one transaction): completion_report MessageQueue row (`:4457-4476`) + PROCESS_REPORT Task (`:4601-4612`) + report_injections PENDING SYNC_MINT (`:4772-4800` — transactional outbox), then F1 (`:6222`) + F2 (`:6256`) fire the watcher and **the fired FollowUps are discarded** — only `enqueued_at` is stamped (`:537-560`). The parent's wake is the PROCESS_REPORT task (the per-instance claim guard explicitly exempts WAITING_CHILDREN instances), and the report **content** rides `report_injections` into the parent's next LLM call via `claim_for_injection` (`report_injection/repository.py:1509-1560`, guarded consumable token; the loser of an INJECTED vs TASK_DELIVERED race sees no PENDING row). **No bus FollowUp is ever delivered on the healthy path** (`child_reports.py:410-417`).

### 2.2 What the target learns after S1 fires (the live shape)

For a watcher whose target is a **non-structural parent** (the nudge shape):

- **S1 fire-only (NO delivery hook): the parent learns NOTHING, and nothing wakes it.** The park is re-evaluated only at a turn end (`message_processing_pipeline.py:1329`), and a turn requires an external trigger; nothing in the codebase wakes a WAITING_CHILDREN instance on a bus-count change. Today the accidental wake is the watchdog's hourly wedge notice (`watchdog:1641-1653` — an `enqueue_message` wake; this is exactly why Ari answers hourly and re-parks). **S4 as planned removes that wake** once the subject population is re-based — so fire-only S1 + S4 leaves the parent in WAITING_CHILDREN with count 0, receiving nothing, until a human dispatches. **That is the wedged→silently-ignorant regression: park released, result never delivered, mask removed. Fire-only S1 must be treated as a non-option.**
- **S1 fire + delivery hook (adopted): the parent learns "the instance you nudged completed", and is woken exactly once.** The FollowUp message is the nudge lane's own text — `"[dependency_bus] nudged instance {worker} completed for message {message_id}"`, `source = internal_agent:{nudger}` (`instance_ops.py:707-728`) — a **content-free completion notice**: a real wake (enqueue flips WAITING_CHILDREN→RUNNING, `instance_messaging.py:2569-2574`; MessageQueue+Task in one txn; does not reset the leader attestation counter, `:2596-2600`), on whose turn end the count is now 0 and the parent **leaves WAITING_CHILDREN** (park early-out `:1330`). This is the nudge lane's designed contract. It is **not** the child's report content — and for the live shape nothing content-bearing was ever owed to the nudger (L-1: zero completion_report rows for the Ari root; the D1 recipient resolution at `child_reports.py:3294-3306` routes content elsewhere). State this plainly in the plan: **S1 restores the WAKE; the report CONTENT carrier remains the report lane, by design.**

### 2.3 The delivery hook's required shape (three details the plan omits)

The hook must replicate the TERMINATE precedent (`instance_lifecycle.py:315-548`) **in full**, not just its happy path:

1. **Per-FollowUp delivery + stamp:** `manager.enqueue_message(instance_id=fu.target_instance_id, …)` then `bus.mark_enqueued_by_source_target(fu.metadata["source_task_id"], fu.target_instance_id)` (`:469-494`). The S1 bus method must therefore enrich `metadata["source_task_id"]` exactly as F3 does (`dependency_bus.py:1487-1490`) — the stamp is keyed on it.
2. **Parent-liveness dead-letter:** skip enqueue when the target is already terminal (`:412-467`); plus the post-fire re-purge in `finally` (`:509-535`). Without the dead-letter, S1 enqueues onto terminal targets — the exact hazard the precedent documents.
3. **No double delivery, by construction:** `fire_for_terminated_target` returns **newly-fired rows only** (fetch selects `state='PENDING'` only, `repository.py:381-395`; the loop `continue`s on a rowcount-0 transition, `dependency_bus.py:1470-1479`). A sibling built the same way inherits both guards: on the healthy single-turn path F1 fires first and S1's fetch never sees the row. Exactly-once is carried by the guarded UPDATE; the `enqueued_at` stamp is only the crash-recovery marker (`_recover_fired_unsent` re-delivers FIRED-but-unstamped rows at boot, `dependency_bus.py:2443-2499`).

One inversion to document: on the healthy lane, `child_reports.py:537-560` stamps `enqueued_at` on rows whose FollowUps were **never enqueued** — so **FIRED+stamped ≠ delivered**. Do not build any diagnostic on "unstamped FIRED ⇒ a wake is owed".

### 2.4 The discriminating test the plan must add

T1/T7 assert row states; neither asserts the **wake**. Add to Phase 1: *fire-only ⇒ NO message_queue row for the target and `instances.status` unchanged; fire+hook ⇒ exactly one wake row and the parent leaves WAITING_CHILDREN on that turn.* This pins §2.2 as an invariant rather than an accident.

---

## 3. F3 — PER-SHAPE MINIMALITY VERDICTS (house doctrine cn 2509c6d5)

| Shape | Verdict | Required because… | …and it is NOT covered by |
|---|---|---|---|
| **S1** fire-at-terminal **+ delivery hook** | **PRIMARY** | the COMPLETED path lacks any target-agnostic child-keyed fire, so a nudge-armed watcher whose child completes via the root lane or across trees is left permanently PENDING with its wake never delivered | the orphan sweep (inert mid-session — constructed with a NULL bus, `api.py:1919-1920` vs `init_dependency_bus` `:2972`, spot-verified; boot-only; and CANCEL ≠ FIRE), the healer (refuses at `mission_strand_healer.py:570-574` while `bus_pending>0`), RDRS (no obligation row is ever minted for the nudger), revive (needs an external send). The delivery hook is PART of S1, not an optional extra (§2.2). |
| **S2** nudge arm-time **self**-guard | **PRIMARY** (class ii prevention) | a self-referential watcher can never be fired by ANY terminal event — the watched instance IS the waiter, and a live root never transitions terminal while alive | S1 (no event ever occurs), S3 (stops the count, not the row: a minted-unfulfillable row is a permanent table leak that keeps feeding raw readers), the ceremony repair (tonight's rows only). ~3 lines at `instance_ops.py` before the mint (`:1642-1652`), using the sibling refusal shape (`:894/:1332/:1768`). Must remain a SELF guard only — a non-child nudge is a real workflow and delivered in the live incident (L-3). |
| **S3** qualified count, **SIX switched readers (R1-R6)** | **PRIMARY** (backlog/liveness cure) | already-minted dead rows hold parks and block healer legs with no terminal event coming — S1 is a transition hook and cannot fire backwards at rows whose child went terminal before deployment | S1 (future transitions only), the ceremony repair (2+2 rows, Emperor-gated), the boot sweep (inert mid-session; cancels without delivering). |
| **S4** corrected notice + fingerprint dedupe + kill-switch | **BACKSTOP / NOISE-CONTROL** (kept, with corrections §5) | the class can recur via lanes the fix does not cover (ERROR-lane non-fire, §6 R-3; dispatch-failure mints, §6 R-4; DOWN-side revive orphans), and the current notice text is false in 100% of occurrences (L-8) while suppressing nothing (L-9/L-10) | nothing — there is no other operator-visible surface for the residual classes; with the corrected population (§5.3) S4 becomes the visibility layer for exactly what S1/S2/S3 leave behind (backlog rows). |

**Does S1 alone dissolve the two dead-watcher classes?** No — and the plan already knows it, correctly: class (i) (terminal-worker non-fire) is cured *going forward* by S1 at the transition, but class (ii) (self-referential) is **unfirable by construction** — no terminal event can ever occur for the watched instance — so S2 (prevention) + S3's self-referential clause (cure of the park effect) are both load-bearing. The dropped shapes (carrier-mint, drain-widening) stay dropped: the fleet census (L-13) says the class they targeted is empty.

**S2 scope adjudication (reconciling two analysts):** the proposal to widen S2 into a target-liveness refusal at arm time ("never arm against an already-terminal worker") is **rejected by default** under r9, because the revive carve-out (`instance_messaging.py:2432-2445`) means a nudged terminal instance transitions RUNNING→terminal *again*, and S1 fires at that later transition — the widened guard would refuse legitimate workflows. The genuinely born-dead residual is narrower: a watcher minted whose accompanying dispatch failed silently (the nudge lane's fail-open tail, `instance_ops.py:743-752`, mints in the act body at `:1642-1652` — **UNVERIFIED whether mint precedes or follows enqueue success**; implementer check C-1 in §12). If mint-follows-failure is possible, the one-line fix is an ordering assertion, not new refusal semantics.

---

## 4. F4 — THE PREDICATE, DEEPENED

### 4.1 Definition (sharpened; two pins the plan omits)

A PENDING watcher for target T is **fulfillable** iff **all** of:
- **(a)** `follow_up_payload.metadata.child_id` is present;
- **(b)** an instance row exists for that child_id;
- **(c)** that instance's status ∉ {completed, error, terminated, failed} (the terminal set, `instance/repository.py:2344-2349` — PAUSED and WAITING_CHILDREN are non-terminal and MUST count);
- **(d)** `child_id != T` (not self-referential).

**Fail-closed pins (mandatory, plan is silent):** rows failing (a) or (b) — missing child_id (legacy-theoretical: both arm lanes write `child_id` today, `instance.py:1436-1439`, `instance_ops.py:716-717`, so none can be born) or ghost child rows — **count as fulfillable** (held). Precedent posture: healer ghost exclusion `mission_strand_healer.py:619-640`, `_ghost_child_filter` `child_reports.py:3135-3172`.

**The liveness read MUST be by payload child_id.** The plan cites `instance/repository.py:3838-3916` — but that helper is `parents_with_non_terminal_children`, keyed by **parent_id linkage**, and nudge watchers are not tree-linked (ari is not sonic-leader's structural parent). Implemented as cited, a target with any parent-linked live child keeps counting its non-linked dead watcher — **the live wedge is not cured**. The read is a batched by-id status query; the PENDING-per-target set is bounded (`repository.py:360-364`).

### 4.2 Inclusion/exclusion table (the normative spec)

| Row shape | raw | qualified | correct? |
|---|---|---|---|
| fulfillable spawn watcher (child live) | ✓ | ✓ | park holds |
| fulfillable nudge watcher (worker live) | ✓ | ✓ | park holds |
| child WAITING_CHILDREN (waiting on ITS children) | ✓ | ✓ | holds — non-terminal |
| child PAUSED | ✓ | ✓ | holds — non-terminal |
| watcher on terminal child (class i) | ✓ | ✗ excluded | releases — S1 fires future transitions; exclusion is the only cure for backlog/arm-after-terminal |
| self-referential (class ii) | ✓ | ✗ excluded | releases; row itself leaks until disposed |
| missing child_id (legacy-theoretical) | ✓ | ✓ **fail-closed** | holds |
| ghost child (no instance row) | ✓ | ✓ **fail-closed** | holds |

### 4.3 Premature-release analysis (the dangerous direction)

- **A live child misclassified terminal** requires its status ∈ the terminal set — WAITING_CHILDREN/PAUSED children count, so a correctly-parked mid-tree parent holds. A COMPLETED child with a PENDING watcher is class (i) and *should* release; the content carrier (report_injections) is a separate substrate, disclosed out of scope (§(j)(i)).
- **Under-count via the edge rows** is impossible post-pins: fail-closed rows count.
- **The read-to-commit race** (child goes terminal just after R1's count read ⇒ parent parks once more) exists identically under the raw count today; and once S1 lands, the transition itself fires the watcher and delivers the wake — the race self-resolves on the next turn end. S1 and S3 are complements, not alternatives.
- **R2 (root-lane completion) releasing early** requires under-counting a live child — same analysis. If it ever fires early, the `root_completed` fan-out (`child_reports.py:5483`) fires once, early; the child's later report hits a COMPLETED instance and rides the revive carve-out + RDRS. Recoverable-but-not-un-fireable — and **pre-existing semantics, not worsened by S3**.
- **Verdict on the plan's "superset-safe" claim (risk 4): TRUE, conditional on the two pins** (fail-closed edges; by-id liveness). As written in the plan, it is false in practice (§4.1 liveness mismatch).

### 4.4 Reader blast radius (corrected census)

**ALIGNED TO THE LANDED DESIGN (iter 003 — this paragraph previously carried the pre-B5 door story and is corrected here).** Measured sync readers of the count family: `mission_strand_healer.py:562`, `:1240`; `job_recovery_service.py:1319`; `job_feedback_observer.py:470→:3838`; the `child_reports.py:378` helper feeding `:1134/:2637/:3511/:3578/:5050`; `error_reporting.py:263`; `manager.py:13852`. **The plan switches SIX (R1-R6):** R1 `message_processing_pipeline.py:1329`; R2 `child_reports.py:3511`; **R3 `child_reports.py:3578`** (`all_children_done`, **SWITCHED — it is a root-lane defer door**); **R4 `child_reports.py:3664-3698`** (the **REAL third raw-count PARK door** — inline SQL inside an open session that writes `WAITING_CHILDREN` **and commits**, so it needs the sync twin + in-transaction form); R5 `msh:562`; R6 `msh:1240`.

**`:3379-3437` is NOT switched and must not be** — it counts **CHILD-INSTANCE rows** (`:3397-3410`, ghost-filtered) and writes `WAITING_CHILDREN` at `:3425`: a **child-liveness** door serving terminal-row reactivation / ghost dedup. `PARK(parent)` has no meaning there; switching it would change its semantics.

**THREE unswitched completion doors remain** — `child_reports.py:3702` (D-2), `:3757` (D-3), `:4434` (D-4); they read the raw count and can **defer a completion** but **cannot create or hold a park**. (The iter-002 text listed D-1 `:3664-3698` among them — that was wrong; it was a **park** and is now R4.)

Re-frame the claim as **"six readers SWITCHED; the measured raw boundary documented"** — see residual R-2. Finally, **UNVERIFIED**: the healer's *drive-side* in-session bus gate — if it reads the raw count, R5/R6 alone do not unblock the healer end-to-end (implementer check C-2).

---

## 5. F5 — KILL-SWITCH AND DEDUPE

### 5.1 Kill-switch: feasible, cheap, and the plan's fallback posture is wrong in a good way

- **A restart-free DB-backed surface EXISTS**: `daemon/services/settings_service.py` `MANAGED_KEYS` registry with a `requires_restart: bool` spec, and **SIX** existing `requires_restart=False` keys — **measured**: `:519`, `:545`, `:623`, `:644`, `:661`, `:678` (the earlier "≥7" figure, which listed `:652`, is **withdrawn**: `:652` is a `ManagedKeySpec` body line, not a `requires_restart=False` declaration); live CRUD (`get_overrides` `:1111-1116`, `set_override` `:1118`); HTTP GET/PUT/DELETE (`daemon/routers/settings.py:943/:966/:1020`). A per-tick reader precedent exists: `daemon/services/metrics_gate.py` (managed-key tier refreshed on a 5s TTL, `:131/:199`). The watchdog holds `manager` (`:501`) and the same service is on the manager (`daemon/api.py:3984`, `manager.py:6449-6457`) — one DB read per hourly tick, no hard-excluded file touched. **Plan risk 10's docs-only fallback does NOT trigger.**
- **Consequences for the plan:** the registration lands in `daemon/services/settings_service.py` — a file **absent from the plan's §(i) file list** (which names `daemon/api.py`, the wiring site). Add it; re-run the ownership census for it.
- **The honesty claim stands, with a corrected basis:** "no restart-free kill-switch surface exists today" is true *of the watchdog* (it reads no settings rows; its only `dependency_bus` mention is prose at `:76`) but misleading *of the daemon* — the surface and precedents exist; what's missing is the key and the read. The runbook's operative statement — **the switch cannot quiet tonight's noise; the noise stops because the condition becomes false** — remains correct and must stay in the plan verbatim (§(h) already says exactly this).
- **Boundary confirmed correct:** the switch gates **notice emission only** (the single enqueue branch, `watchdog:1641-1653`) and never detection, classification, S1/S2/S3, or the healer. T6's fail-if-wired-into-healing purpose is right. The owed `docs/guidelines/` file has a template and 4 precedents in-tree (`docs/guidelines/TEMPLATE.md` + 4 guideline files) — the obligation is dischargeable as specified.

### 5.2 Dedupe: the 1800s-window reuse is mechanically void — split the clocks

- **Measured cadence machinery:** scan interval default 3600s (`daemon/config.py:2619-2622`) drives the loop (`watchdog:1798/:1818`). 3600s scan vs 1800s window ⇒ the window expires 1800s before every next scan ⇒ **the re-keyed (parent, reason) dedupe suppresses 0 of the live repeats**. T5 as specified is satisfiable only by synthetic re-entrance, not by production cadence.
- **The 1800s constant is the wrong clock to reuse:** its docstring (`:188-196`) defines it as a RE-ARM allowance ("a re-parked parent is re-eligible by the next tick… still bounding duplicate notices when the cadence is tightened"). Re-keying it and calling it the dedupe inverts its documented semantics — it would re-arm and re-notify after 30 minutes, the very repeat it claims to suppress.
- **Adopted design:** **condition-fingerprint persistence.** Keep the cooldown map re-keyed to `(parent, reason)`, but let the **episode boundary pops** (which already exist: `:1607`, `:1617`, clearing entries exactly when the wedge condition becomes false) end the episode — i.e. **suppression persists while the condition persists**; drop the TTL for the suppression decision (or set it ≫ scan interval, e.g. 7200s), keeping 1800s solely as the re-arm allowance. Net behavior: exactly one notice per (parent, reason) **episode**; a different reason inside the window still passes (new information); the condition clearing re-arms. T5's assertion changes from "inside the window" to "within the episode".

### 5.3 The notice population (a blast the plan doesn't price)

If S4 re-bases the watchdog's subject population on the **raw PENDING watcher set**, the 8 legitimately-parked WAITING_CHILDREN instances (L-7) become notice-eligible: a **~9× notice-volume regression**. The population must be the **qualified-unfulfillable watcher set** (rows S3 would exclude: terminal child or self-referential) — which also makes S4 coherent with S3 (same predicate, read-side) and turns the notice into the visibility surface for the residual/backlog classes. Note the interaction this creates, and it is a *good* one: while backlog rows exist post-promote, the corrected notice fires (with TRUE text) and its enqueue **is itself a wake** — the parent is not silently stranded even before its rows are disposed.

---

## 6. RESIDUALS THE FIX DOES NOT CLOSE (disclosed, named)

> **IMPLEMENTATION-REVIEW FOLD (tip `077a41a33`) — three contract corrections that this section must not contradict:** (1) **DELIVERY IS AT-LEAST-ONCE with a bounded duplicate window**, not at-most-once — resume Pass 1 does not consult the delivery marker, so a crash in the enqueue-commit→stamp gap re-enqueues once; the duplicate is a **MESSAGE**, the parent's second turn is a no-op; the lost-wake window is covered; the marker-aware Pass-1 filter is a named follow-up. (2) **S4's population is the UNION** — the qualified-unfulfillable set **∪** the classic wedge (zero watchers ∧ no non-terminal children). (3) **R7's gate leg is a FAIL-OPEN WRAPPER OUTSIDE the gate's session**, deliberately not R4's in-transaction shape (a wrapper cannot wedge the completion path on a read error).

- **R-1 (backlog PARKS vs backlog ROWS).** S3 cures backlog parks; the **rows** leak. Raw readers keep seeing them: concretely, the observer's raw finalize gates (`job_feedback_observer.py:1173/:2786/:2942/:3838`) defer job finalization indefinitely for any instance carrying a stale watcher — a future job on such an instance never finalizes. The plan is silent on the fleet's ~13 other PENDING rows beyond tonight's 2+2. **Escalation (adopt only if judged load-bearing): the healer-integrated resolver (Option C, §8) or a bounded dispose** — under r9 this is NOT in the default package; it is the named next commission if the raw-reader consequence bites.
- **R-2 (three unswitched completion doors — CORRECTED census, iteration 003).** `child_reports.py:3702` (D-2), `:3757` (D-3), `:4434` (D-4) — completion defers, **none creates or holds a park**. `:3578` is **SWITCHED** (R3); `:3379-3437` is a **child-liveness** door and is **not** a bus door. `error_reporting.py:263` is dead-letter liveness, not a park. The earlier "four doors incl. `:3578`" count was mis-derived (D-2/D-3/D-4 are the only raw readers among the completion doors).
- **R-3 (ERROR lane) — ALIGNED to the adopted branch (A).** The ERROR lane **is** covered going forward: S1's ERROR hook at `error_reporting.py:439` (branch A — measured UNCONTENDED) fires the target-agnostic child-keyed fire, beside the existing `:612`/`:654` emits. **Pre-release**, an erroring child with a nudge-armed watcher stays PENDING (F1+F2 only) and that row holds the lane-7 mission HOLD — DOOR-1 (§A5). **Post-release the door is closed by code**; the residual is the backlog rows accumulated while it was open (§(j)(v)).
- **R-4 (dispatch-failure mint).** See §3 S2 adjudication — implementer check C-1.
- **R-5 (the inert periodic sweep).** Stays disclosed, unfixed, unrelied-upon (anomaly `fcf89d88`). Confirmed again by direct read: `get_dependency_bus()` returns None at construction (`dependency_bus.py:2787` until `:2801`).

---

## 7. F6 — THE HISTORICAL FINDING, VERBATIM AND DECISIVE

**The class is documented since June and the June fix was at the wrong layer.** Verbatim, measured:

- `dependency_bus.py:1597-1604` (inside `cancel_for_source`, docstring `:1586-1612`; the plan's ":1590-1612" is the enclosing block): *"Production incident 2026-06-26 (instance 06f500af stuck in ``waiting_children`` for hours): this was the missing link between the cancel-and-retry flow and the bus's PENDING watcher state. The retry's natural completion fired ``emit_terminal(task_id=retry)`` but the watcher was registered against the cancelled task's id — the watcher stayed PENDING, ``count_pending_for_target`` stayed > 0, and the leader never reached COMPLETED."*
- `dependency_bus.py:894-906` (F2 docstring): *"…task-keyed ``emit_terminal`` … is INCORRECT for multi-turn children like Wanderer … The task-keyed ``emit_terminal(task_id=child's_final_task)`` cannot match the watcher — the parent stays PENDING forever (stuck ``waiting_children``)."*

**The June fix** (`4926a2eb6`, 2026-06-26, git-verified): added `cancel_for_source(task_id)` wired into the cancel-and-retry call sites (today: `worker_pool.py:695` at `:867/:1033` → `dependency_bus.py:364`). **It covered the retry-rekey mechanism ONLY** — cancel the mis-keyed row when its task is force-cancelled and retried. It never contemplated a watcher whose watched instance lives on and completes normally, and it cancels; it never fires.

**The five-month leak, mechanically:** three local patches each closed one lane — retry-rekey (June `4926a2eb6`), multi-turn corrective scoped to the **structural-parent pair** with a root short-circuit (July `1655397298`; `child_reports.py:676-683`), terminate-path child-keyed fire (Aug `06878b630`). The nudge lane — added later, no tree guard, no self guard (`instance_ops.py:655`, `bus.watch` `:729`) — inherited the gap by construction, and the watchdog's children-based subject leg (`watchdog:1602-1608`) kept misdescribing the resulting state (false in 4/4 notices ever minted, L-8), so the class stayed invisible.

**Consequence for F1 (the deepest answer in this commission):** a class documented for five months that survived three fixes is decisive evidence that **arm-layer and lane-layer patches cannot establish the missing invariant** — "some lane fires a watcher whose watched instance is terminal." Every new arm family re-opens the gap. The durable fix is the primitive itself at the EMIT layer on the COMPLETED lane (target-agnostic, child-keyed), with the qualified count as the read-side cure for what already exists. **The plan's layer choice is archaeologically confirmed; its site choice was wrong and is corrected in §1.2.**

---

## 8. TRADE-OFF MATRIX — S1 placement options (5 axes)

| Option | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| **A: S1-emit** — child-keyed fire at `child_reports._dispatch_post_commit_side_effects` (`:5285`) + full delivery hook | **Low-Med** — one hook site; delivery reuses the `instance_lifecycle.py:315-548` precedent verbatim | **High** — O(1) per terminal transition; no polling; no read-path mutation | **Med** — bus method uncontended; hook site CONTENDED (reconciliation with `fix/empty-job-completed-event`, priced) | **Med** — coverage proven for root+tool+regular branches at the measured site; delivery hook must carry dead-letter + re-purge + source_task_id enrichment; exactly-once by guarded UPDATE | **Low** + reconciliation | **ADOPTED** — matches the TERMINATE precedent; the only site covering both live branches (M-A/M-B); archaeology-confirmed layer |
| **B: S1-resolve** — bus resolves PENDING watchers at read time (count/fetch) | **Med** — no hook, but write side-effects inside read predicates | **Med** — instance-status read per watcher on the hottest read path (every park decision, every finalize gate) | **Low** — hidden mutation in a COUNT; the CM gate calls it inside an open session; inverts the bus contract (`child_reports.py:410-417`) | **HIGH** — resolution-without-delivery silently drops the wake: row FIRED, count drops, park releases, parent NEVER receives anything (§2.2); under-delivery is worse than over-hold | Low surface / **High tail** (debugging silent side effects) | **REJECTED** |
| **C: S1-emit + healer-integrated mid-session resolver** — A plus backlog fire-and-deliver inside MissionStrandHealer's existing passes (r6-compliant) | **Med-High** — A plus a disposition matrix per backlog row | **Med** — bounded by healer cadence | **Med** — a second delivery path to keep coherent with A's | **Low-Med** — stale-wake delivery needs an age guard (a row fired long after its child's transition delivers a belated "completed") | **Low-Med** — uncontended files only | **NAMED ESCALATION** — adopt iff R-1 (backlog-row raw-reader poisoning) is judged load-bearing; not in the default package under r9 |

**Coverage summary:** future transitions — A ✓ (proven site), B ✓, C ✓; pre-existing backlog parks — A ✗ (S3 covers), B ✓, C ✓; backlog rows (disposition) — only C; races with in-flight completion — all three safe (guarded UPDATE); delivery integrity — A ✓ (hook), B ✗ (cannot deliver), C ✓.

---

## 9. RECOMMENDED OPTION

**Option A, packaged as S1(+delivery) + S2(self-guard) + S3(qualified count, corrected) + S4(corrected, split clocks, settings-registry switch), with R-1..R-5 disclosed.** It beats the alternatives because:

1. **It is the only option that delivers the wake** — F2 established that delivery, not resolution, is what the defect destroys; B fails this outright, and A carries the TERMINATE delivery precedent verbatim.
2. **The site is now proven, not preferred** — the plan's uncertainty (risk 1, Q1) is resolved by measurement: the observer is gated off for exactly the live shape; `child_reports._dispatch_post_commit_side_effects` (`:5285`) is reached for all three COMPLETED outcomes and is instance-keyed.
3. **The archaeology says this layer holds** — three lane-patches over five months each leaked; the primitive at the emit layer is the invariant the arm layers cannot establish (§7).
4. **Minimality is preserved** — no parallel sweep (r6), no new table/column/migration (r10), the kill-switch lands on an existing registry with a per-tick reader precedent (so risk 10's fallback is moot), and the one genuine scope question (backlog rows) is named as an escalation rather than silently absorbed.

**Confidence: HIGH** on the hook-site ruling, the delivery verdict, the dedupe arithmetic, the kill-switch feasibility, and the archaeology (all direct source/git reads at the pinned base). **MEDIUM** on the S2-widening rejection and the R-1 severity judgment (design-intent calls). **Flip assumptions:** (i) a demonstrated completion path bypassing `child_reports` would restore the observer belt to required; (ii) a healer drive-side gate reading the raw count would add a sixth switched reader (C-2); (iii) a live trace showing the D1 resolver routed the live child's report to the nudger would contradict L-1/L-3 and demote S1 — the single highest-value open check (read `instance_ops_audit` for the 18:06:07 nudge against the resolver's recency window).

---

## 10. DISCREPANCY LIST (plan claims vs measurement; consolidated, severity-ordered)

| # | Plan claim | Measurement | Severity |
|---|---|---|---|
| 1 | §(a.2)(i)/§(i) row 3/§7 task 3/risk 1/Q1: observer is the preferred S1 hook, "must fire for a root lane instance" | `job_feedback_observer.py:2346` gate + `:4482-4490` flag ⇒ skipped on the normal path; no self-event for non-root children (`child_reports.py:6369`) ⇒ blind to M-B. **Correct site: `child_reports.py:5285`** | 🔴 |
| 2 | §(a.2) treats the delivery hook as mechanism; no statement that it is the ONLY wake for non-structural targets; §(d) removes the masking watchdog wake | Fire-only S1 = park released + parent silent (§2.2); promote to an invariant + a discriminating test | 🔴 |
| 3 | §(d.2): reuse the 1800s window re-keyed to (parent, reason) | 3600s scan ⇒ suppresses 0 of live repeats; the constant is a re-arm allowance by its own docstring — split the clocks (§5.2) | 🔴 |
| 4 | §(d.1): population re-based on "watchers" (raw PENDING) | 8 legitimately-parked instances become notice-eligible (~9× volume); must be the qualified-unfulfillable set (§5.3) | 🟠 |
| 5 | §(c)/register: "the two existing readers of count_pending_for_target_sync" (implying only two) | measured sync-reader population (≥9); **THREE** unswitched completion doors (D-2/D-3/D-4) share R2's disease; the iter-002 "4" included `:3664-3698`, which is a PARK and is now R4 (§4.4) | 🟠 |
| 6 | §(c) hook: liveness via `instance/repository.py:3838-3916` | parent-linkage-keyed — fix-breaking for nudge watchers; must read by payload child_id (§4.1) | 🟠 |
| 7 | §(c): predicate silent on missing-child_id / ghost rows | fail-closed counting must be pinned or risk-4's "superset-safe" wording is false (§4.1) | 🟠 |
| 8 | §(c) R2 = `child_reports.py:3511` only | root branch has three defer doors: `:3511`, `:3578`, `:3379-3437` (§4.4) | 🟠 |
| 9 | §(i) file list: kill-switch registration at `daemon/api.py`; risk 10 contemplates docs-only fallback | registry is `daemon/services/settings_service.py` (omitted from the list); surface + per-tick reader precedent exist ⇒ fallback moot (§5.1) | 🟠 |
| 10 | §(a.2)(ii): fallback hook at the stamps `:4029/:4248/:4414` | inside the `bus._get_parent_lock` window (`:3092-3117`) — latent self-deadlock; hook belongs at the async dispatcher `:5285` | 🟡 |
| 11 | S1 hook spec omits TERMINATE's dead-letter (`instance_lifecycle.py:412-467`), re-purge (`:509-535`), and `metadata["source_task_id"]` enrichment (`dependency_bus.py:1487-1490`) | required for stamp-keyed bookkeeping and terminal-target safety (§2.3) | 🟡 |
| 12 | implied "FIRED+stamped ⇒ delivered" hygiene | healthy lane stamps `enqueued_at` without delivering (`child_reports.py:537-560`) — FIRED+stamped ≠ delivered | 🟡 |
| 13 | §(h)/line 39: "all 8 WC-exit paths" | unenumerated in either artifact; substance (transient wakes re-park while count>0) confirmed; count UNVERIFIED | 🟡 |
| 14 | register §1: F2 range ":885-1090+"; F3 ":1373-1480+" | F2 ends `:1142`; F3 ends `:1583` (plan's range correct) | 🟢 |
| 15 | §4's PENDING-exit enumeration | omits `cancel_for_target` (`dependency_bus.py:1298`, called `instance_lifecycle.py:565`) — a fifth PENDING→terminal writer | 🟢 |
| 16 | §(j)(i): obligation mint anchor `:5264` | mint txn at `:4772-4800` (`:5264` is the result return) | 🟢 |
| 17 | register O-1 boot sweep call ":1752"; §(b) refusal precedents ":891/:1331/:1767" | `:1753`; `:894/:1332/:1768` (token lines) | 🟢 |
| 18 | §(c) R3 "same bus predicate" | R3 is **inline SQL inside an open session** (`:3664-3673`), not the repo method — implementation must respect the session | 🟢 |

---

## 11. DECISIONS PENDING (for the council / approver / leader)

1. **Price the reconciliation (accepted fact):** S1's hook is in CONTENDED `child_reports.py`. Confirm the lane-lands-first posture (cn `888d612d`) and the minimal-footprint discipline inside that file (dispatcher-head hook only; R2/R3 switches are pre-existing plan items in the same file).
2. **R-1 backlog rows:** accept as disclosed (default, r9) or commission the healer-integrated resolver (Option C) / bounded dispose. The deciding evidence is whether any production instance currently carries a stale watcher into a future job's finalize gate.
3. **S4's dedupe re-spec:** adopt episode-persistence (§5.2) and re-state T5's assertion ("within the episode", not "inside the 1800s window"). This changes plan text and one test; it is not optional — the current design is mechanically void.
4. **The observer belt (§1.2):** default-OFF confirmed unless a bypass path is demonstrated. The reviewer re-measures `child_reports.py` at the contended tip before implementation begins (all §1 line numbers are base-pinned and will drift if `fix/empty-job-completed-event` lands first).

## 12. IMPLEMENTER CHECKS (UNVERIFIED at this gate, bounded)

- **C-1:** nudge mint ordering — can the watcher mint (`instance_ops.py:1642-1652`) survive a failed/dispatch-rejected enqueue (fail-open tail `:743-752`)? If yes, add the mint-order assertion (§3).
- **C-2:** locate the healer's drive-side in-session bus gate; if it reads the raw count, it becomes the sixth switched reader (§4.4).
- **C-3:** confirm `_dispatch_post_commit_side_effects` never runs for ERROR outcomes (residual R-3 coverage boundary).

---

*Artifacts: this file completes the architect-gate deliverable set alongside `plan-overview.md` and `evidence-register.md` (both untracked, untouched). No code was modified; no commits were made; the gate order (architect → council → approver → implement → tidier → tester → reviewer → staged release, NO live promote) is preserved.*
