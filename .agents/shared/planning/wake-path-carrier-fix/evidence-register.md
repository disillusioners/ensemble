# Evidence Register — wake-path-carrier-fix (Phase B, RECONCILED)

**Base commit:** `eca98397d9e0109611764ac418132c9d35afde2c` (verified `git rev-parse HEAD` == `git rev-parse origin/latest` == base; branch `feature/wake-path-carrier-fix`; `git status --porcelain` shows only the untracked planning dir).
**Tree measured:** `/home/nea/ensemble-src-wt-wake-path-carrier-fix` (ABSOLUTE). `/home/nea/ensemble-src` was NEVER measured (wrong tree).
**Measurement modes:** plan-author (direct read/grep in the worktree) and explorer 80bd1cfc (tree-pinned reads, VERBATIM report forwarded by the dispatcher). Every anchor is tagged with who measured it.
**Re-pin rule (house):** anchors are leads — content-match at use time. `git grep -n '<pat>' eca98397d -- <path>` / `git show eca98397d:<path>`.

**Status vocabulary**
- `FOUND` — measured at base by the plan author, content confirmed.
- `FOUND/EXPLORER` — measured by explorer 80bd1cfc; plan author did not independently re-verify.
- `MOVED` — an earlier digest's line range does not exist at base; the corrected range is given.
- `NOT RE-VERIFIED` — quoted earlier, not re-measured; **content-match before citation**.

> **RECONCILIATION NOTE (post-council):** this register was patched in one pass against the council's evidence-layer findings. Corrections are marked **[RECONCILED]** with the both-wrong-and-right anchors shown. **Nothing was re-pinned to a contended tip** (see §8 CONTESTED-TIP POSTURE).

---

## 0. DESCRIPTION CORRECTIONS

| # | Earlier description | Correction at base | Evidence |
|---|---|---|---|
| **D1** | "job_recovery_service.py is a registry of drift shapes" | **lettered PATTERN FAMILY** — sequential in-method blocks `(a)(b)(c)(d)(e)(f1)(f2)(g)(h)` documented in one method's docstring. **No registry abstraction exists.** | `daemon/services/job_recovery_service.py:27-47` — FOUND |
| **D2** | "the watchdog reads bus/task rows to decide the wedge" | The watchdog performs **NO bus-row read at all**. Its only `dependency_bus` occurrence is a docstring mention at `:76`. | `daemon/services/waiting_children_watchdog.py` — `grep -n "dependency_bus\|DependencyWatcher\|count_pending\|dependency_watcher"` = exactly one hit, `:76`, prose — FOUND (grep-negative for row reads) |
| **D3 [RECONCILED]** | "`watch_reconcile_sweep.py` is a bus caller" | **FALSE PREMISE — REMOVED.** It belongs to the **JOB-watcher** system (job/work notify rows); **zero** `dependency_bus` hits. | grep over the file — FOUND (zero hits) |
| **D4 [RECONCILED]** | "the observer is a candidate hook site" | **WITHDRAWN.** `_finalize_instance` has **zero production callers** — its own code says so at `:3645-3650` — and the method contains **no bus seam**. `_dispatch_post_commit_side_effects` is the live dispatch seam. | `daemon/services/job_feedback_observer.py:3645-3650`; `daemon/services/child_reports.py:3130` — FOUND |

---

## 1. THE BUS FIRE-PRIMITIVE CENSUS (decisive)

| id | Primitive | Site | Matches on | Target scope | Production callers |
|---|---|---|---|---|---|
| **F1** | `emit_terminal(task_id, outcome)` | `daemon/services/dependency_bus.py:667-883` | `source_task_id == task_id` (`repository.py:172`) | **target-agnostic** | child lane: `child_reports.py:467` helper → `:5431`, `:6222`; `error_reporting.py:612`, `:708`; plus the F2 loop's internal already-fired skip at **`:1087`** (approver N9 — a skip site, not a caller seam). **No generic task-completed seam.** |
| **F2** | `emit_terminal_for_child_instance(parent, child)` | `daemon/services/dependency_bus.py:885-1090+` | **pair**: `target_instance_id == parent` AND `follow_up_payload.metadata.child_id == child` | parent-scoped | `child_reports.py:5436 / :6070 / :6256 / :6567`; `error_reporting.py:654 / :765`. **Root short-circuit lives in the CALLER helper** — `child_reports.py:676-683` (*"parent_instance_id is None — no watcher to fire (root instance)"*); **the bus method itself has NO parent-None guard** (approver anchor fix: `dependency_bus.py:676-683` is F1's docstring region — the prior attribution was wrong; the conclusion holds) |
| **F3** | `fire_for_terminated_target(target)` | `daemon/services/dependency_bus.py:1373-1480+` | `follow_up_payload.metadata.child_id == target` **across EVERY target** (`repository.py:343-395`, filter `:393`; docstring `:352`) | target-agnostic | **ONE caller**: `instance_lifecycle.py:385` ← `terminate_instance` `:3189-3191`. **TERMINATE ONLY.** |

**F2 matcher — [RECONCILED] anchor:** `daemon/repositories/dependency_bus/repository.py:239-309` (`fetch_pending_for_target_and_child` def at `:241`; the child-id filter at `:305-309`). The earlier `:1048-1052` is **past EOF**. FOUND.

**Bus states:** `PENDING / FIRED / CANCELLED` only — **no `RETIRED`**. `daemon/repositories/dependency_bus/models.py:49-71` (values `:69/:70/:71`; column at `:208`). FOUND.

**`_recover_fired_unsent` — [RECONCILED] anchor:** `daemon/services/dependency_bus.py:2443` ("Load FIRED-but-not-enqueued rows for the caller to re-enqueue"). Confirmed: **it only LISTS; it never re-enqueues.** The earlier `:1699` was `start()`'s docstring. FOUND.

**The bus documents this defect class in its own docstrings:**
- `daemon/services/dependency_bus.py:1597-1604` — *"Production incident 2026-06-26 (**instance** 06f500af stuck in `waiting_children` for hours): this was the missing link between the cancel-and-retry flow and the bus's PENDING watcher state. The retry's natural completion fired `emit_terminal(task_id=retry)` but the watcher was registered against the cancelled task's id — the watcher stayed PENDING, `count_pending_for_target` stayed > 0, and the leader never reached COMPLETED."* — inside **`cancel_for_source`'s** docstring. FOUND.
- `daemon/services/dependency_bus.py:894-906` — F2's docstring: *"`emit_terminal(task_id=child's_final_task)` cannot match the watcher — the parent stays PENDING forever (stuck `waiting_children`)"*. FOUND.

### 1a. CORRECTION C-06f [RECONCILED]

**`06f500af` is the June incident's INSTANCE id — NOT a commit.** `git show 06f500af` → **unknown revision**. The docstring was introduced by commit **`4926a2eb6`** and lives at **`:1597-1604`**, inside `cancel_for_source`'s docstring. **`dependency_bus.py:2514-2515` mislabels it** (*"the production incident pattern recorded in commit 06f500af"*) — a **pre-existing documentation defect**. **Note it; do NOT fix it in this lane.**

### 1b. TERMINAL-LANE COVERAGE MATRIX (the defect surface)

| Terminal lane | F1 | F2 | **F3-class (target-agnostic child-keyed)** |
|---|---|---|---|
| TERMINATE | — | — | **YES** (`instance_lifecycle.py:385`) |
| COMPLETED (`root_completed` `:5483`, `regular_child_completed` `:6195`) | yes (`:6222`) | yes (`:6256`) | **NO** |
| **ERROR** (`_send_error_report`) | yes (`:612`) | yes (`:654`) | **NO** |
| **TOOL-INVOCATION** (`:6161`) | **NO** | **NO** | **NO** — the census gap |

---

## 2. THE ARM SITES — ONLY TWO `bus.watch` CALL SITES REPO-WIDE

| id | Site | Lines | Guard | Verified by |
|---|---|---|---|---|
| **A-arm-1** | `daemon/tools/instance.py` — `_register_child_completion_watcher` (spawn / `send_message`) | `:1220-1459`; `bus.watch` **`:1436-1439`**; **tree guard `:1273-1275`** (`if not target_instance or target_instance.parent_id != parent_instance_id: return None`); task-gone edge `:1278-1287` | **TREE-GUARDED** | plan author FOUND (`:1428-1442`, `:1270-1280`) + explorer A6 |
| **A-arm-2** | `daemon/tools/instance_ops.py` — `_register_nudge_completion_watcher` (nudge lane) | `:609-752`; `bus.watch` **`:729-732`**; FollowUp payload `:708-728` (`target_instance_id=nudger_instance_id` `:709`; `metadata.kind="nudge_complete"`, `metadata.child_id=worker_instance_id` `:716-717`; **no `source_task_id` in the payload**); task-id resolution `:673-694`; lane choice `:1561-1572`; mint call site `:1642-1652`; **nudger-id guard `:655-661` [RECONCILED from `:665-671`]** | **NO TREE GUARD. NO SELF GUARD.** FAIL-OPEN (`:743-752`). | plan author FOUND |

**A-arm-2's `source_task_id` ABSENCE (load-bearing for S1's delivery):** the payload metadata carries `kind`, `child_id`, `parent_id`, `message_id`, optional `mission_id` — **not** `source_task_id` (`instance_ops.py:713-725`). ⇒ the post-enqueue stamp cannot key without the **`ENRICH(w)`** step.

**SIBLING SELF-SURGERY REFUSALS (the precedent S2 follows):** `instance_ops.py:891-894`, `:1331-1332`, `:1767-1768` (`"self-surgery-refused"`); contract prose `:43`, `:1209`, `:1440`. **The nudge act body (`:1495-1589`) has NO such gate** — verified by reading the whole body and by grep (3 hits, none in the nudge body).

**B2 EVIDENCE [RECONCILED iter 003 — SUPERSEDED, then re-adjudicated] — BOTH candidate discriminators are unusable; the plain matcher stands:** the in-memory per-parent **generation counter** (`self.generation`, `dependency_bus.py:481`; `.clear()` `:1787`; bumped by `watch()` `:2149`) is never persisted onto the matched row — `watch()` inserts only `source_task_id` + `target` + `follow_up_payload` (`:605-611`) and writes **no `watcher_metadata`** (`repository.py:357`) ⇒ **nothing on the row to compare against**; per-parent monotonicity would strand a just-armed row. **AND the row-level `source_task_id`-terminal form (iter-002's `STALE_EPISODE`) is WRONG at fire time** — live row `8a1f60d7`: source task 39320 terminal **18:08Z**, watched child terminal **19:04Z** (56 min later) ⇒ that exclusion drops exactly the row the fix exists to fire. **LEADER RULING (iteration 003):** `FIRE_SET` is the **plain matching form** with no staleness term; `watch()` **refuses a missing `child_id`** (load-bearing); **"terminal" lives only in S3's `FULFILLABLE(w)`, keyed on the child's INSTANCE status** (`INSTANCE_TERMINAL_SET`); a stale-but-PENDING row delivers **at most one spurious wake** (disclosed, plan risk 21). Two nudgers on one child = two waiters = both wake.

**B3 EVIDENCE [RECONCILED iter 002; CONTRACT CORRECTED AT IMPL-REVIEW, tip `077a41a33`] — a same-transaction marker is unachievable, AND the marker does not prevent re-delivery:** `instance_lifecycle.py:6015-6018` verbatim — *"a single transaction spanning both passes is NOT implementable because `manager.enqueue_message` owns its own session."* ⇒ the `delivered_watch_id` marker is **committed BEFORE the enqueue, in its own transaction**. **CONTRACT CORRECTED (impl-review): this yields AT-LEAST-ONCE, not at-most-once** — resume Pass 1 never consults the marker, so a crash in the enqueue-commit→stamp gap re-enqueues once (bounded duplicate; the second turn is a no-op).

**B4 EVIDENCE [RECONCILED iter 002] — the pop sites:** `_wedge_notified.pop` occurs at **`:1607`, `:1617`, `:1746`** (measured; the prior `:1460` is the hang-pass purge tail, `:1466` a comment header). `:1746` pops on **WC-membership**, and the notice's own `enqueue_message` flips WC→RUNNING (`:1643-1645`) ⇒ episode-end must be the **ROW-CHECK** (`S4_NOTICE_SUBJECT`), not WC membership.

**C-1 RESOLVED (implementer check):** the nudge mint ordering is **enqueue → commission → watcher**, with an **early return on an enqueue exception** (`instance_ops.py:1545-1670`); **a failed enqueue mints nothing**.

**Child id rides in the payload JSONB:** `repository.py:352-365` — *"`watch()` writes no `watcher_metadata` — the `child_id` key lives only in the serialized FollowUp payload"*; filter `:388-396` (`if meta.get("child_id") == child_instance_id`). Model columns: `models.py` — `watch_id`, `source_task_id`, `target_instance_id`, `follow_up_payload` (JSONB), `watcher_metadata` (JSONB, column `metadata`), `created_at`, `fired_at`, `enqueued_at`, `state`. **No child-id column exists; none is needed.**

---

## 3. THE PARK / LIVENESS PREDICATE PAIR

| id | Role | Site | Measured | Verified by |
|---|---|---|---|---|
| P-park-1 | **PARK predicate** | `daemon/services/message_processing_pipeline.py:1279-1390`; decisive read **`:1329`**; early-out `:1330`; CAS `:1335-1340` | consults **ONLY** the bus count | plan author FOUND (`:1320-1345`) + explorer A14 |
| P-count | the raw count | `daemon/repositories/dependency_bus/repository.py:471-515` (`:471` def; predicates `:510-514`) | counts **ALL** PENDING rows; never inspects the watched work | plan author FOUND + explorer A13 |
| **P-status [RECONCILED — S3's liveness read]** | **by-id batched status** | **`daemon/repositories/instance/repository.py:1071` `get_statuses(instance_ids: list[str]) -> dict[str, str]`** | **THE read S3 must use** — batched, by id, no tree linkage | plan author FOUND |
| P-park-2 | root-lane completion defer #1 | `daemon/services/child_reports.py:3499-3538` — `:3503`, `:3511`, `:3534`, `:3538` | the ROOT lane **emits nothing** | plan author FOUND + explorer A15 |
| **P-park-2b [RECONCILED — NEW DOOR]** | root-lane defer **#2** | `daemon/services/child_reports.py:3578` — `all_children_done = bus.count_pending_for_target_sync(instance_id) == 0` | a **second** raw-count defer door | plan author FOUND (`:3576-3580`) |
| **P-child-live [RECONCILED iter 003 — NOT a bus door]** | **child-liveness door — WITHDRAWN from the switched set** | `daemon/services/child_reports.py:3379-3437` — `:3397-3410` counts **CHILD-INSTANCE rows** (ghost-filtered), writes `WAITING_CHILDREN` at `:3425` | **NOT a bus-count door.** Switching it to `PARK(parent)` would change its meaning and break terminal-row reactivation / ghost dedup ⇒ **deliberately unchanged** | plan author FOUND (verbatim `:3395-3430`) |
| **P-park-3 [RECONCILED iter 003 — THE REAL PARK DOOR]** | **the third raw-count PARK door (writes AND commits the park) — now R4, SWITCHED** | `daemon/services/child_reports.py:3664-3698` — inline `SELECT count(*) FROM dependency_watchers WHERE target_instance_id = :1 AND state='PENDING'` (`:3664-3673`) → `instance.status = WAITING_CHILDREN` + `session.commit()` (`:3688-3691`) | **NOT a mere completion defer** (the iter-002 misfiling as D-1); it is the door that kept the ROOT parked after R2/R3 were qualified | plan author FOUND (verbatim `:3655-3700`) |
| **P-park-4 [RECONCILED]** | deprecated self-queue park | `daemon/services/child_reports.py:1297-1301` (`parent.status = InstanceStatus.WAITING_CHILDREN.value` at `:1298`) | — | plan author FOUND |
| P-park-5 | terminal-row re-arm park | `daemon/services/child_reports.py:3421-3435` | — | NOT RE-VERIFIED |
| **P-park-x [RECONCILED iter 003]** | unswitched completion doors (**THREE**, not four — D-1 was a park and is now R4) | `child_reports.py:3702` (D-2), `:3757` (D-3), `:4434` (D-4) | all three read the **raw** count and can **defer a completion**; **none can create or hold a park** | plan author FOUND (producer literals) |
| P-live-1 | **LIVENESS predicate** ("zero non-terminal children") | `daemon/repositories/instance/repository.py:3838-3916`; terminal set `:2344-2349` | **status-only AND parent-linkage-keyed** — SQL is `WHERE child.parent_id IN … AND child.status NOT IN (terminal)` ⇒ **cannot see a nudge-watched child (not tree-linked)** | NOT RE-VERIFIED; **the linkage-keying is the disqualifier for S3** |
| P-live-2 | ghost carve-outs | `daemon/services/child_reports.py:3409 / :3742 / :4329`; `_ghost_child_filter` `:3135-3172` | — | NOT RE-VERIFIED |
| P-guard | the WEDGE guard | `daemon/services/waiting_children_watchdog.py:1466-1618`; children leg `:1602-1608`; live-carrier leg `:707-772`; query-failure fallback `:1538-1543`; **cooldown `:197` = `WEDGE_REARM_COOLDOWN_SECONDS: float = 30 * 60` = 1800 s**; re-arm cooldown `:1619-1636`; notice enqueue `:1641-1653`; second notify `:1698-1703` | the guard's subject leg reads **instances**; the park reads **watchers** | plan author FOUND (`:190-205`, `:420-440`) + explorer A27 (rest NOT RE-VERIFIED) |
| P-guard-boot **[RECONCILED]** | `_enabled` is **boot-time** | `waiting_children_watchdog.py:536` `self._enabled = bool(enabled)` — assigned **once** in `__init__` | **a settings-registry key alone cannot change runtime behaviour** | plan author FOUND |
| P-notice | the notice prose | `waiting_children_watchdog.py:429-434` — *"…A PROCESS_REPORT message is in your queue but no worker is scheduled to deliver it."* | asserts queue state the detector never verified — **100 % false rate** | plan author FOUND (`:420-440`) |
| P-episode **[RECONCILED iter 003]** | episode identity map + the REAL pop sites | `waiting_children_watchdog.py:561` / `:581` — `self._wedge_notified: dict[str, float] = {}` (comment `:561-583`); the `:188-196` docstring defines `WEDGE_REARM_COOLDOWN_SECONDS` as a **re-arm allowance**, not a suppression TTL. **`_wedge_notified.pop` occurs at exactly THREE sites: `:1607`, `:1617`, `:1746`** | S4's episode dedupe keys on `parent_id`; **episode END = the `S4_NOTICE_SUBJECT(parent)` row-check being EMPTY** — `:1746` pops on **WC-membership** and the notice's own enqueue flips WC→RUNNING (`:1643-1645`), so that pop must be **re-based** or the notice ends its own episode. **`:1460` is a log-string tail and `:1466` a comment header — NOT pop sites** (the iter-002 attribution was wrong) | plan author FOUND |
| P-heal-1 | healer bus leg (1) | `daemon/services/mission_strand_healer.py:555-574`; count **`:562`**; refusal `:568-574` | **the healer refuses to heal exactly the wedge a stuck PENDING watcher creates** | plan author FOUND + explorer A23 |
| P-heal-2 | healer RZ bus probe | `mission_strand_healer.py:1240`; skips `:1234-1239`, `:1243-1248` | same population, second reader | plan author FOUND |
| P-heal-3 | healer ghost exclusion | `mission_strand_healer.py:619-640` (`filter_never_dispatched_ids` `:634-636`) | — | explorer A24, NOT RE-VERIFIED |
| **P-hold [RECONCILED — NEW]** | **the mission HOLD (both guards)** | `daemon/services/report_delivery_recovery.py:2779` **and** `:2857` — each `if watchers: out.skipped_watcher_pending += 1; return` | **a PENDING watcher blocks ALL commission abandonment, INCLUDING the TTL backstop** | plan author FOUND (both sites) |
| **P-gate [RECONCILED — NEW]** | the mission gate | `daemon/services/child_reports.py:2691-2695` — `mission_commissions = self._count_outstanding_mission_commissions(instance_id); if mission_commissions > 0: return False, f"mission_commissions={mission_commissions}", False` | **refuses root completion forever while a commission is outstanding** | plan author FOUND |

---

## 4. THE ROOT LANE EMITS NOTHING (the dispatch gap)

| id | Fact | Site | Verified by |
|---|---|---|---|
| R-root-1 | Root lane gate | `daemon/services/child_reports.py:3503` → `:3511` → `:3534` (`deferred_waiting_children`) → `:3538` | plan author FOUND + explorer A15 |
| R-root-2 | `root_completed` contains **no bus emit** | `daemon/services/child_reports.py:5483` — the branch is the emission-time gate + publish | explorer A20 + plan author FOUND |
| R-root-3 | the corrective F2 helper **short-circuits for roots** | `child_reports.py:676-683` | plan author FOUND + explorer A18 |
| R-root-4 | **final-task keying** | `child_reports.py:5421-5431` (same shape `:6216-6226`) | plan author FOUND |
| R-root-5 | **the bus is a state machine — no enqueue** | `child_reports.py:412-417`; the ERROR fallback repeats it (`error_reporting.py:708` region: *"No FollowUp enqueue — the bus does not deliver messages"*) | plan author FOUND |
| **R-root-6 [RECONCILED]** | delivery precedent + **true order** | **`daemon/services/instance_lifecycle.py:315-577`**, delivery slice **`:469-540`**, `_repurge_fired_follow_ups` def **`:580`**. Order: fire `:385` → **dead-letter skip `:412-468`** → **enqueue `:472-477`** → **stamp `:494-499`** → **re-purge `:508-540` (in a `finally`; comment at `:511`)** → cancel `:565` (terminate-only) | plan author FOUND |
| R-root-7 | exactly-once of a supplementary fire | `child_reports.py:5415-5440` — *"`transition_state` returns rowcount==0 when the watcher is already FIRED, so a later `regular_child_completed` turn's emit is a no-op"* | plan author FOUND + explorer A17 |
| **R-root-8 [RECONCILED — NEW]** | the single dispatch call site | `daemon/services/child_reports.py:3130` — `await self._dispatch_post_commit_side_effects(result, last_content, completed_message_id)`; reached unconditionally for `:5483` / `:6161` / `:6195`; the dispatcher's branch set is `:5348 / :5395 / :5483 / :6028 / :6133 / :6150 / :6161 / :6195` — **no `error` branch**; **every canonical branch EARLY-RETURNS** — measured return lines `:5345 / :5360 / :5448 / :5622 / :5694 / :6014 / :6123 / :6139 / :6158 / :6192 / :6464` (approver B1: the unknown-outcome backstop at `:6469+` is reachable only for unknown outcomes, so the S1 hook must be placed PER-OUTCOME, not at the tail) | plan author FOUND |
| **R-root-9 [RECONCILED — NEW]** | **the ERROR lane's own emitter** | `daemon/services/error_reporting.py::_send_error_report` (`:439`); emits `:612` (F1), `:654` (F2), `:708` / `:765` (fallbacks); rationale comment `:590-601` | plan author FOUND |
| **R-root-10 [RECONCILED — NEW]** | **the tool-invocation branch emits nothing** | `child_reports.py:6161-6195` — `grep -n "emit_terminal\|bus"` over `:6161-6200` yields only the **comment** header of the next branch | plan author FOUND |

---

## 5. THE ORPHAN-SWEEP QUESTION — RESOLVED FROM SOURCE

**Answer: BOTH a boot-only sweep and a periodic lane exist, and the periodic lane is PERMANENTLY INERT — a wiring-order defect.**

| id | Fact | Site | Verified by |
|---|---|---|---|
| O-1 | **BOOT-ONLY sweep** | `_sweep_orphan_watchers` def `dependency_bus.py:2501`; **called once at `:1753`** **[RECONCILED from `:1752`]** from `DependencyBus.start()`; boot log `:1754-1759` | plan author FOUND |
| O-2 | **PERIODIC lane exists** | `daemon/services/orphan_watcher_sweep.py` — `start()` `:154-174` (`asyncio.create_task(self._run_loop())`); `_run_loop` calls `sweep_once()`; `sweep_once` calls `bus._sweep_orphan_watchers(min_watcher_age_seconds=…)`; defaults `DEFAULT_ORPHAN_SWEEP_INTERVAL_SECONDS = 90` (`:72`), `DEFAULT_ORPHAN_SWEEP_GRACE_SECONDS = 30` (`:79`) | plan author FOUND |
| **O-3 [RECONCILED]** | **the periodic lane is wired with a NULL bus** | `daemon/api.py:1919-1920` constructs `OrphanWatcherSweepService(dependency_bus=get_dependency_bus(), …)`; the singleton is set **later** at `daemon/api.py:2972` `await init_dependency_bus(app, manager)` ⇒ **`set_dependency_bus(bus)` at `daemon/api.py:5710`** (and `None` at `:5720` / `:5739`). **NOT in `dependency_bus.py`** — that file is **2,812 lines** and its own `set_dependency_bus` def is at `:2801`; `:5710` is **past EOF**. ⇒ `get_dependency_bus()` at `:1920` returns **None** ⇒ every tick takes the "dependency_bus is not wired" branch and cancels **nothing** | plan author FOUND (ordering 1919 < 2972 measured) |
| O-4 | the periodic lane is ALWAYS-ON (no env flag) | `daemon/api.py:1901` *"ALWAYS ON — no env flag"*; `:1934` `.start()`; interval/grace from `config.services.orphan_watcher_sweep_{interval,grace}_seconds` (`:1924-1931`) | explorer A22 + plan author FOUND |
| O-5 | sweep predicate is **task-based** | `dependency_bus.py:2653-2678` — `source_task_id NOT IN (SELECT CAST(id AS TEXT) FROM task WHERE status IN ('running','pending','paused'))`; tables `task` (`daemon/repositories/task/models.py:171`) and `dependency_watchers` (`daemon/repositories/dependency_bus/models.py:139`) | plan author FOUND |
| O-6 | age grace uses `created_at` (ISO-8601 **string**) | `models.py` `created_at` = `datetime.now(timezone.utc).isoformat()`; `dependency_bus.py:2634-2636` cutoff; lexicographic comparison `:2571`, `:2578` | plan author FOUND |

**CONSEQUENCES:**
1. **The anomaly resolves into a defect** — the rows survived 4.5 h because the mid-session lane is **never wired to the bus** (O-3), not because "boot-only ⇒ expected". ⇒ **the plan must not rely on the sweep.** Filed anomaly `fcf89d88` (cluster `d685a9ee`).
2. **A restart cancels watchers whose source task is terminal** (O-1 + O-5).
3. **A restart does not release the parent while any watcher's source task is still active.** **BUT SEE §13** — as of **23:03:54Z** that qualifier no longer holds for the four Ari watchers.
4. **CANCEL ≠ FIRE** — the sweep cancels without re-homing ⇒ **no wake, and no lost report delivered**.
5. The periodic lane's docstring (*"the structural backstop for the steady state"*) is **false** today. Not fixed by this lane.

---

## 6. LIVE-FORENSICS ROWS (read-only census on live `ensemble_prod`; ROWS WIN over framing)

| id | Fact | Source |
|---|---|---|
| L-1 | Parent `17efd2ef` (ari root): `message_queue` 16 rows all-time (10 agent, 3 human, 3 system), ALL completed; **ZERO `completion_report` rows ever**; `task` 16 rows all-time, ALL `process_message`; **ZERO `process_report` tasks ever minted**; `report_injections` = 0 | brief F1 |
| L-2 | `b0633a1e` = `task.id=36706`, mid-tree carrier for `5d4d2b2a` (sonic-developer), minted 2026-11-15, COMPLETED 2026-11-17 18:05:36 `{"success":true,"skipped":true}`, DELIVERED — **not tonight's child** | brief F2 |
| L-3 | `dependency_watchers` 2 PENDING targeting the parent: **(i) `8a1f60d7`** source_task 39320, PENDING since 18:06:07.999, `metadata.child_id` = sonic-leader `542b49a6`, mission `0cdc0b31`; **(ii) `999efa71`** source_task 39424, PENDING since 20:30:47.131, self-nudge (commissioner = worker = target = `17efd2ef`), mission `90493206` | brief F3 |
| L-4 | sonic-leader `542b49a6` ran, took 4 team reports, terminal (completed) **19:04:27**; source_task 39320 COMPLETED **18:08:20** — 56 min EARLIER | brief F3 |
| L-5 | `bus.count_pending_for_target(17efd2ef) = 2 > 0` holds the park; the "zero non-terminal children" leg is trivially true (ZERO instance children). Re-park observed 21:24:46, 21:30:57 | brief F4 |
| **L-6 [RECONCILED — arithmetic]** | **Fleet @22:55Z: `13` TOTAL PENDING — of which `2` structurally dead (both on the Ari root), `10` confirmed legitimate, and `1` UNCLASSIFIED.** The earlier "2 + 10 + 0 = 13" arithmetic is **wrong (2+10+0 = 12)** — the plan now states **13 total / 2 dead / 10 legit / 1 unclassified** and **does not** present the three as summing. `dependency_watchers` holds 11,858 FIRED / 256 CANCELLED — **current state only, no archived non-fire history**. DEFERRED injection state: ZERO all-time | adjudication note 3 + consolidation fact 4 |
| **L-12 [RECONCILED — timestamped]** | **Accretion, with row ids and clocks:** `5602e91d` @22:51:59.886 and `c83e12a2` @22:53:05.940, both keyed on source task **39564** on leader `82551f59`, mission `cb904c16` (which owns the two dead rows). **Legitimate ONLY while 39564/82551f59 stay non-terminal.** **TASK 39564 COMPLETED 23:03:54Z** ⇒ at the bus level they join the structurally-dead set ⇒ **the fleet's "total PENDING" and its "structurally dead" count must be re-read at ceremony time; the 22:55Z census was TRUE WHEN WRITTEN and is FALSE NOW** | consolidation fact 5 + adjudication note 4 + item 13-ii |
| L-7 | Exactly **1 of 9** WAITING_CHILDREN instances is contradictory (ari: pending watchers, 0 non-terminal children); the other 8 are legitimate parks | consolidation fact 4 |
| L-8 | Only **4** wedge notices ever minted fleet-wide, all on Ari (19:30:01, 20:30:01, 21:30:02, 22:30:02). Cross-tab: both-legs-right 0; report-assertion-FALSE-only **4**; both-false 0. The report claim was FALSE in **100%**; zero-non-terminal-children TRUE in 100% | consolidation fact 6 |
| L-9 | Inter-notice intervals **3600.7 / 3600.6 / 3600.5 s** — hourly to the half-second, invariant under activity. The **1800 s** cooldown never suppresses (60 min scan > 30 min cooldown) | consolidation fact 7 + P-guard `:197` |
| L-10 | PENDING-dedupe would have prevented **0 of 3** repeats (prior-pending-at-remint 0, prior-completed 3; each notice consumed within ~25-85 s) ⇒ **condition-fingerprint dedupe REQUIRED** | consolidation fact 8 |
| L-11 | `app_settings` holds exactly 3 rows, all `limits.*` (all `requires_restart`). **`settings_service.py` holds 6 `requires_restart=False` keys** (`:519`, `:545`, `:623`, `:644`, `:661`, `:678`) — the earlier "≥7" was wrong. Env vars frozen at process start; no per-tick admin toggle ⇒ **no restart-free kill-switch surface TODAY**; the switch is NEW CODE | consolidation fact 9 + plan-author measurement |
| L-13 | The queued-report-stuck-on-parked-turn class does NOT exist fleet-wide: `completion_report` messages ready=1 / completed=108; the single ready row is a FRESH HEALTHY triple (carrier task 39586 PENDING +11 ms; injection 74577298 PENDING SYNC_MINT +16 ms; message completed ~2 min later); message-only-no-injection 0; injection DEFERRED 0; terminal-injection-with-ready-message 0 | consolidation preamble |
| **L-15 [iter 003 — live recurrence]** | **The defect family recurred during the approval process itself: anomaly `1be5734c` (cluster `51b544f3`)** — the approver's iteration-002 cures worker's report arrived as an **EMPTY PARENT WAKE** and the revive-enqueue re-delivery was **never consumed** (1 h+ idle; recovered via transcript reads). Same class as this lane repairs (a waiter never woken / a delivery with no carrier) ⇒ **the lane's urgency evidence**; carried as **plan risk 22** | approver disclosure, iteration 002 |
| L-14 | LOG RETENTION ≈ **90 minutes** (6×10 MB rotating under DEBUG `[LLM] Stream`) ⇒ **tests must not depend on log-window forensics**; the 18:06-22:33 window is gone | brief |

---

## 7. TEST-HOME / PACK REGISTRATION — **CITE BY NAME, NEVER BY LINE** (item 17)

`.agents/tester/PACKS.md` = 1,523,869 bytes; plan author read the relevant rows directly. **Copies diverge** ⇒ every citation in the plan uses the **pack NAME**.

| Pack (BY NAME) | Script | Files | Measured state |
|---|---|---|---|
| **`wedge_fix_suites_unit_test`** | `test/packs/wedge_fix_suites_unit_test.sh` | `tests/unit/test_reconciler_wedge_fix.py` (10) + `tests/job_queue/test_seam_invariants.py` (68 — incl. T1 `:3270` / T2b `:3413`) | ✅ PASS 78/78 @ `ae837a98`; later `wlts-wedge` view row PASS 78/78 @ `d195316a6` (3.5 s) |
| **`waiting_children_watchdog_unit_test`** | `test/packs/waiting_children_watchdog_unit_test.sh` | `tests/unit/services/test_waiting_children_watchdog.py` — **PACK row says 49; the file has 51 test defs at base** (measured `grep -c`); the pack row is stale by 2, which is a PACKS.md bookkeeping delta, NOT a collection failure — the implementer sees 51 run | ✅ PASS 49P/0F @ `f8c5ce8f` (pack-era count) |
| **`claim_guard_locks_unit_test`** | `test/packs/claim_guard_locks_unit_test.sh` | incl. `tests/job_queue/test_seam_invariants.py` (66) | ✅ PASS 178/0F |
| **`orphan_active_job_recovery_suites_unit_test`** | `test/packs/orphan_active_job_recovery_suites_unit_test.sh` | 19+3+3+8 (incl. `tests/unit/test_child_still_running_defer_bus_terminal.py`) | ✅ PASS 51P/0F @ `e6cd5fc8` |
| **`security_boundary_hygiene_suites_unit_test`** | `test/packs/security_boundary_hygiene_suites_unit_test.sh` | 27 + 14 + 4 | ✅ PASS 45/45/0 @ `16c59375` |

**Pack script shape (measured, `wedge_fix_suites_unit_test.sh`):** `set -euo pipefail`; unset `SSL_CERT_FILE` / `SSL_CERT_DIR`; `SCRIPT_DIR`/`PROJECT_DIR`; `cd "$PROJECT_DIR"`; `timeout 120s .venv/bin/pytest <files> --tb=short -q 2>&1`; triage `124`→TIMEOUT, `0`→PASS, else FAIL (`exit 1`). Dual-layer (300 s outer).

**Test-home anchors:**
- `tests/unit/test_reconciler_wedge_fix.py` — `TestSubshapeCCarrierRevivalSync` `:214`, `:272`, `:360`; `TestSubshapeCCarrierRevivalAsync` `:437`, `:482`; `TestWedgeBackstop` `:541` — `:549`, `:604`, `:653`, `:697`, `:781`; `TestAliveInstanceStatusesMembership` `:799`. FOUND.
- `tests/job_queue/test_seam_invariants.py` — `:3268-3270` (T1), `:3411-3413` (T2b). FOUND.
- `tests/unit/services/test_a5_wedge_notify_work.py` — asserts `census_stays_23_1_0` (*"no new writer/creator/mint site"*). **REGRESSION HAZARD**; **the file is in NO pack**. FOUND.

---

## 8. FILE-OWNERSHIP CENSUS AT THE PIN (**22 code-bearing branches** — approver-verified; a local `rev-list origin/latest..branch` sweep reads 27 including `evidence/*`, `backup/*`, `merge/*` and tester-evidence branches, which the adjudicated 22 excludes)

**CENSUS BASIS (reproducible):** **19 `origin/*` remote-unmerged vs `origin/latest`; 27 local-unmerged; union 41 short-name entries** (the sets overlap by name) ⇒ **the census TOTAL is rule-dependent; the per-file contention table is the load-bearing evidence.** **Measurement caveat:** the union sweep stripped the `origin/` prefix from remote names, so it **under-reports remote-only contention** — the authoritative REMOTE numbers are the `origin/*` sweep's; **both sweeps are cited and the combined per-file totals are the truth.** **Union-pass result for this lane's AVOID / hard-excluded files:** `instance_lifecycle.py` = **3** (`feature/langgraph-checkpoint-perf` + `feature/video-mock` + `feature/video-mock-room` — the **video pilot** lane); `daemon/tools/instance.py` = **3** (`feature/done-marker` + the same two video-pilot branches); `daemon/manager.py` = **5** (`feature/langgraph-checkpoint-perf` + `fix/empty-job-completed-event` + `test/test-pack-20260407015139` + **local-only** `feature/self-restart-drill-fixes` + `feature/skill-capture-gates`); `child_reports.py` = **4** (unchanged); `instance_messaging.py` = **1** (unchanged). **All three files are AVOID or hard-excluded ⇒ zero footprint change** — S2's guard lands in `instance_ops.py`, S1's hook in `child_reports.py`, and `instance_lifecycle.py:385` is only a fence observable. **The primary-fix layer remains entirely uncontended under both passes.**

**ZERO pressure (uncontended):** `daemon/services/message_processing_pipeline.py`, `daemon/tools/instance_ops.py`, `daemon/services/dependency_bus.py`, `daemon/repositories/dependency_bus/repository.py`, `daemon/repositories/dependency_bus/models.py`, `daemon/services/report_delivery_recovery.py`, `daemon/services/job_recovery_service.py`, `daemon/services/waiting_children_watchdog.py`, `daemon/services/mission_live_guard.py`, `daemon/services/mission_strand_healer.py`, `daemon/repositories/task/repository.py`, `daemon/repositories/instance/repository.py`, `daemon/tools/_auth.py`, new migration `.sql`.

**[RECONCILED — NEW measurements]:**
- **`daemon/services/error_reporting.py` — ZERO unmerged commits across all 22 code-bearing branches** (937 lines). ⇒ **S1's ERROR hook lives in the cheapest file in the lane.**
- **`daemon/services/settings_service.py` — ZERO unmerged commits across all 22 code-bearing branches** (1,377 lines). ⇒ the S4 kill-switch registry key is uncontended.
- `daemon/services/job_feedback_observer.py` — uncontended, **but DEAD as a hook** (D4).

**CONTENDED:**
| File | Contending branch |
|---|---|
| `daemon/services/child_reports.py` | `fix/empty-job-completed-event` — **4 commits** (`5368a12d4`, `96f1f6d35` +2) |
| `daemon/manager.py` | ×3 branches (`fa31a5208`, `964f6b11a`, a test merge) — **and HARD-EXCLUDED** |
| `daemon/services/instance_messaging.py` + `daemon/services/instance_lifecycle.py` | `feature/langgraph-checkpoint-perf` @ `c37c870c56` (**2026-08-26**) |
| `daemon/tools/instance.py` | `feature/done-marker` @ `e5e5c4a80c` |

### 8a. CONTESTED-TIP POSTURE (item 19 — REPLACES "re-measure at the contended tip")

> **The contention is a STALE FORK from 2026-09-20.** It **neither descends from nor contains** base `eca98397d`. **ZERO plan anchors exist at its tip.** ⇒ **DO NOT re-pin anything to it.** The prior instruction to "re-measure at the contended tip" is **WITHDRAWN** — it would anchor the plan to a tree that cannot produce this lane's code.
> **Implement against base.** If that branch is ever **rebased and landed**, re-measure **only** the **root-gate** hunks and the **dead-letter-publish** hunks.
> **The same staleness applies** to `feature/langgraph-checkpoint-perf` @ `c37c870c56` (**2026-08-26**).
> **IMPLEMENTER PRE-FLIGHT (Phase 1 task 0):** re-measure `instance_lifecycle.py`, `instance_messaging.py`, `tools/instance.py`, `manager.py` at implementation time.

**HARD EXCLUSIONS:** `daemon/services/skill_*.py`, `daemon/tools/skill_*.py`, `daemon/services/job_processor.py`, `daemon/config.py`, `daemon/manager.py`, `daemon/graph.py`. (Skill-system tip MOVING — re-measure at edit time.)

**REMOVED [RECONCILED]:** `daemon/services/watch_reconcile_sweep.py` — **not a bus caller** (D3).

**Migrations:** 133 `.sql` in-tree at the pin; newest `20261116_083000_add_room_resolution_warnings.sql` (the commission's "130 / head 20261112" is **STALE**). `20260825_000001_create_message_metadata.sql` exists (added `a9a71e9bb`); `feature/langgraph-checkpoint-perf` carries the only unmerged migration commit and it **touches that existing file** ⇒ a fresh post-`20261116` timestamp cannot collide. **This lane adds NO migration.**

---

## 9. RECONCILIATION OF THE TWO OQ1 BRANCHES (OQ1 RESOLVED-BY-CENSUS)

| Branch | Why F1 could not fire | Why F2 could not fire | Why F3 could not fire |
|---|---|---|---|
| **M-A — sonic-leader took the ROOT lane** | the root lane emits nothing (`:3503-3538`; `:5483` has no emit) | F2 short-circuits on `parent_instance_id is None` (`:676-683`) | F3 is TERMINATE-only; sonic-leader went **COMPLETED** |
| **M-B — sonic-leader is a child of a third instance** | F1 is keyed on the child's **FINAL** task (`:5421-5431`), not the watcher's `source_task_id` 39320 ⇒ multi-turn never matches | F2 is invoked with the **STRUCTURAL** parent; ari is not it | as M-A |

⇒ **OQ1 RESOLVED-BY-CENSUS**: the mechanism differs; the missing primitive is identical — **no target-agnostic child-keyed fire on the COMPLETED (or ERROR, or TOOL-INVOCATION) lane.** S1 covers all.

---

## 10b. THE REVIEWER'S DISCLOSED LIMITS — RECORDED, NOT PAPERED OVER (fold round 4)

**The reviewer ran NO tests itself.** The following are **implementer/tester-attested**, not reviewer-observed:

| Claim | Attested by | Reviewer's own verification |
|---|---|---|
| **113/113** | implementer / tester | **not run by the reviewer** |
| **51/51** | implementer / tester | **not run by the reviewer** |
| **45/45** | implementer / tester | **not run by the reviewer** |
| the PACKS numbers | — | verified **ARITHMETICALLY against static `def test_` counts**, **NOT against a run** |
| the counter-mutation **RED→GREEN** transitions | implementer | corroborated by the reviewer's **static trace** — **NOT independently observed** |
| PG-dialect behaviour of the changed read paths | — | **NOT exercised** (the delta adds **no PG-specific path**) |

**The reviewer's conceded anchor error:** its **T5 cite `:1766-1775` did not point at T5**; the real **T5(a)/(b)/(c)** are at **`:2662` / `:2686` / `:2724`** — **the implementer's correction stands**. And **ALL SIX** of the reviewer's earlier **R1 cites were stale** (the file grew **~100 lines**).

⇒ **Record the trust boundary explicitly:** the reviewer's contribution is a **static, per-hunk analysis** (its fence evidence of record) plus **arithmetic** pack-count verification — **not** an executed test pass. Anyone reading this register should treat the green counts as **implementer/tester-attested** and the reviewer's contribution as **static**, and should re-derive the pack numbers against the main-copy `PACKS.md` before relying on them.

## 10c. THE REVIEWER'S DISCLOSED **UNVERIFIED** LIST (fold round 5, VERBATIM — ATTESTED, not independently observed)

The following are **ATTESTED by the implementer/tester, NOT independently observed**:

| Claim | Status |
|---|---|
| The **restore-proof sha256s** | **ATTESTED, not independently observed** |
| The pack run **115/115** | **ATTESTED, not independently observed** |
| The **four mutation REDs** | **ATTESTED, not independently observed** — *the worker's discrimination analysis is a **static control-flow trace**; the tester's earlier independent mutation legs corroborate the family but **not this commit's specific REDs*** |
| **F-2's raiser reachability** | **ATTESTED, not independently observed** |
| The residual's **live tick behaviour** | **ATTESTED, not independently observed** |

**Read together with §10b**: the reviewer ran no tests itself; the green counts are implementer/tester-attested; the reviewer's contribution is a **static, per-hunk analysis** plus arithmetic pack-count verification.

## 10a. TWO REGISTERED FACTS (iteration-003 fold, docs-only)

**(a) `TERMINAL_INSTANCE_STATUSES` consolidation — DELIBERATELY REFUSED (not deferred).** A future lane may attempt to consolidate the local `TERMINAL_INSTANCE_STATUSES` tuple into the shared enum. **Do not.** The tuple's **deterministic bind order** is **LOAD-BEARING** for the rendered `IN (...)` clause — the bind order changes the query plan and the rendered SQL. Rationale recorded here so a future lane does not re-attempt the consolidation.

**(b) THE PG LIST IS NOW EXECUTED (real PG 16.15, throwaway `ensemble_test`, `ensemble_dev` untouched).** The "no PG on this box" caveat is **RESOLVED WITH EVIDENCE**, not assumed. **111 tables before/after; 12 scratch rows cleaned with `count=0` proof; least-privilege role.** The **HIGHEST-RISK path is proven**: `mark_delivered_watch_id` claim True→False plus **5/5 exactly-one-winner under PG row locking** — the mechanism is a **guarded CAS** `UPDATE … WHERE metadata->>'k' IS NULL RETURNING` under the row lock with **READ-COMMITTED re-evaluation** (NOT `SELECT … FOR UPDATE`); `mark_enqueued_by_source_target` stamps `enqueued_at` (`repository.py:736-745`), so a normal delivery leaves **NO unstamped row** ⇒ the crash window is exactly **one row** ⇒ **exactly one duplicate**. **Res8 MEASURED:** EXPLAIN `fetch_pending_for_child_instance` = **Seq Scan, cost 0.00..12.25, est. rows=1 vs ACTUAL 11, execution 0.046 ms** (planner estimate 11× low; seq-scan by design for a rare event).

## 10. OPEN QUESTIONS CARRIED FORWARD (post-reconciliation)

| # | Question | Status | Who can answer |
|---|---|---|---|
| **OQ-A** | Which lane armed `8a1f60d7` — injection or enqueue? | **OPEN** — the `lane=%s` log (`instance_ops.py:733-741`) is beyond the **90-min** retention ⇒ **likely permanently unanswerable**; requires future instrumentation | staged-fix instrumentation |
| **OQ-B** | Why did the periodic orphan sweep leave both rows PENDING? | **CLOSED — §5 O-3 (NULL bus at construction).** Anomaly `fcf89d88` | closed by source |
| **OQ-C** | Did the healer ever log `bus_pending=2`? | **OPEN** — log likely gone | staged-fix instrumentation |
| **OQ-D** | Was `999efa71` minted against an already-terminal source task? | **OPEN** — log likely gone | live rows |
| **OQ-E** | Will leader `82551f59` / task 39564 flip the two new watchers to dead? | **CLOSED at the bus level — 39564 COMPLETED 23:03:54Z** (§6 L-12) | — |
| **OQ-F** | Re-verify the `NOT RE-VERIFIED` anchors before citing them | **OPEN — implementer pre-flight (Phase 1 task 0)** | implementer/reviewer |
| **OQ-G [RECONCILED]** | Does the healer's **drive-side in-session** bus gate read the RAW count? (if raw ⇒ a **sixth** switched reader) | **OPEN — C-2** | Phase 3 task 6a |

---

## 11a. LESSON (R1 adjudication, iteration 003 — generalisable form)

**The pre-delta purge was fail-safe BY CONSTRUCTION** — a missing value compared False (`subjects.get(p,(0,[]))[0] > 0` ⇒ `0 > 0` ⇒ never dropped). The B2 delta **inverted** that to fail-open by replacing the implicit default with an explicit read. **Generalisable form:** *"a rewrite that replaces a fail-safe-by-construction default (a missing value that compares False) with an explicit read can silently invert the failure posture — the cure introduced the hazard it was meant to fix."* Record it with the lane's lessons and grep for it before any future 'rewrite the purge' pass.

## 11. THE ERROR LANE — C-3 MEASURED (§A3 of the plan)

**Question:** does `child_reports._dispatch_post_commit_side_effects` (`:5285`; single call site `:3130`) run for **ERROR** outcomes?

**ANSWER: NO.**

1. `grep -n 'outcome="error"\|outcome="failed"' daemon/services/child_reports.py` → **NO MATCH**. The producer's complete outcome vocabulary (all `outcome="…"` literals): `instance_not_found` `:3255`; `deferred_waiting_children` `:3437/:3534/:3558/:3592/:3702/:3757`; `idempotency_skip` `:3448/:3897/:4057/:4203/:4263/:4434/:4928`; `deferred_pause` `:3493`; `root_waiting_children` `:3875`; `root_completed` `:4066`; `tool_invocation_completed` `:4271`; `child_still_running_defer` `:4376`; `dead_parent_skip` `:4965`; `regular_child_completed` `:5267`. **No error member.**
2. The dispatcher's branch set (`:5348 / :5395 / :5483 / :6028 / :6133 / :6150 / :6161 / :6195`) has **no `error` branch** ⇒ an error outcome would fall through with **no bus hook** even if produced.
3. The ERROR lane's own emitter: `daemon/services/error_reporting.py::_send_error_report` (`:439`) emits **F1 at `:612`**, **F2 at `:654`**, and the defensive fallbacks at **`:708` / `:765`**. Its comment at `:590-601`: *"Delegate the bus emission + FollowUp enqueue + re-trigger loop to `child_reports._emit_terminal_via_bus` so the finalize re-trigger fires uniformly for BOTH completion and error paths."*

⇒ **The ERROR lane emits F1 + F2 and NO F3-class (target-agnostic child-keyed) fire** ⇒ identical hole to COMPLETED.

**Why it is load-bearing (measured):** `report_delivery_recovery.py:2779` **and** `:2857` — each `if watchers: out.skipped_watcher_pending += 1; return` ⇒ a PENDING watcher blocks **ALL** commission abandonment **including the TTL backstop**; the mission gate (`child_reports.py:2691-2695`) then **refuses root completion forever**. **Strictly worse than tonight's wedge.**

**Branch selected: (A)** — fold the ERROR outcome into S1, hooking at `error_reporting.py:439` (**uncontended**), beside the existing `:612`/`:654` emits.

---

## 12. DELIVERY DURABILITY + MATCHER ROBUSTNESS (the two engineering constraints)

### 12a. The guarded UPDATE is FIRE-ONCE, not ENQUEUE-ONCE

| Window | Mechanism | Consequence |
|---|---|---|
| crash **after** fire, **before** enqueue | row is `FIRED`, `enqueued_at IS NULL`; `_recover_fired_unsent` (`:2443-2499`) **only LISTS**; the boot caller **finalizes and never re-enqueues** (`api.py:5379-5530`) | **LOST delivery** |
| crash **after** enqueue, **before** stamp | **Resume Pass 1** re-enqueues with **no paused filter in its SQL** (`instance_lifecycle.py:6103-6116`) | **DOUBLE delivery** |

**`enqueued_at` semantics are ALREADY SPLIT across four write sites with two incompatible meanings** (`models.py:125-128`) ⇒ the plan **does not depend on it**; it uses a **distinct `delivered_watch_id` marker**.

**CONTRACT CORRECTED AT IMPLEMENTATION REVIEW (tip `077a41a33`): AT-LEAST-ONCE, not at-most-once.** Measured: resume Pass 1's SQL (`daemon/services/instance_lifecycle.py:6105-6112`) is `WHERE target_instance_id = :instance_id AND state = :fired_state AND enqueued_at IS NULL AND fired_at <= :now_iso` — it **does not consult `delivered_watch_id`** (the marker is written by `dependency_bus/repository.py:612` `mark_delivered_watch_id` / `DELIVERED_WATCH_ID_KEY` at `:80`, but Pass 1 never reads it). ⇒ a crash between the **enqueue commit** and the **`enqueued_at` stamp** leaves `FIRED ∧ enqueued_at IS NULL ∧ marker set ∧ message queued` ⇒ **Pass 1 re-enqueues ⇒ ONE DUPLICATE**. **The lost-wake window is covered** (Pass 1 delivers once). **Bounded cost: one redundant MESSAGE; the parent wakes twice and the second turn is a no-op.** The marker-aware Pass-1 filter is a **named follow-up** (`instance_lifecycle.py` is an AVOID file). **A `FIRED` row never holds the park** (S3 does not count it).

### 12b. The matcher keys on child_id alone across every target

`fetch_pending_for_child_instance` (`repository.py:392-393`) matches **every** row with that `child_id`, **any target**.

**THE ADJUDICATED DESIGN (leader ruling, iteration 003 — replaces the withdrawn generation-counter prescription AND the withdrawn `STALE_EPISODE` form):**

- **DEDUPE KEY (implementation state @ `077a41a33`):** `(parent_id, reason_code_set)` — `watchdog.py:1946-1953` `_fingerprint = tuple(sorted(_codes))`. **S4 population (implementation state @ `077a41a33`):** the **UNION** — `_subject_rows` (qualified-unfulfillable) **∪** `elif _total_pending == 0 and not _has_non_terminal_children` (the classic wedge), `watchdog.py:1884-1897`.

**`FIRE_SET` is the PLAIN matching form** — `{w in PENDING : w.follow_up_payload.metadata.child_id == instance.instance_id}`. **NO staleness exclusion, NO generation term.** Every PENDING row whose `child_id` matches is a **genuine waiter**; firing them all is correct semantics, not a false wake. **Two nudgers on one child = two waiters = both wake.**
- **`watch()` REFUSES a missing `child_id`** — this is the load-bearing control (without it the matcher cannot key at all).
- **The generation counter is UNUSABLE and WITHDRAWN** (`self.generation`, `dependency_bus.py:481`; `.clear()` `:1787`; bumped by `watch()`): never persisted onto the matched row (`:605-611`; `repository.py:357`), so nothing on the row can be compared against it — and per-parent monotonicity would strand a just-armed row.
- **The `source_task_id`-terminal form is ALSO WITHDRAWN** — it is TRUE at fire time (live row `8a1f60d7`: source task 39320 terminal 18:08Z, watched child terminal 19:04Z), so it would drop **exactly the row the fix exists to fire**.
- **"Terminal" lives in S3, not S1**: `FULFILLABLE(w)` keys on the **child's INSTANCE status** (`INSTANCE_TERMINAL_SET` = `{completed, terminated, error, failed}` — instance values; `TaskStatus` has no `terminated`/`error`, which was the type confusion). Rows that remain PENDING after the fire are **backlog** (their child is already terminal) and S3 excludes them from `PARK(parent)`. **The two predicates are complementary, not competing.**
- **DISCLOSED RESIDUAL:** a stale-but-PENDING row fires **once** and leaves PENDING ⇒ **at most one spurious wake**, bounded and self-limiting (plan risk 21).

---

## 13. THE LIVE REPAIR BUNDLE — **DISCHARGED-BY-EXECUTION** (live, 2026-11-18 21:18-21:20Z; independently verified)

**STATUS: EXECUTED.** The Emperor-attested 5-block repair bundle was **executed on live** at 21:18-21:20Z and independently verified: parent `17efd2ef` **completed at 21:21:24Z**, wedge notices **ceased**, `--verify` checks 1-4 **PASS**, zero drift. §13 (below) is retained as the historical procedure + the executed record.

## 13. THE LIVE REPAIR BUNDLE (external — item 13, historical)

| Property | Value |
|---|---|
| **Artifact** | `.agents/shared/planning/wake-path-carrier-fix/repair/emitted/live_wedge_repair_request__20261118T010144Z.{sql,receipt.md,receipt.json}` |
| **Shape** | **8 blocks — MEASURED** (`grep -c "^-- BLOCK"` = 8): `W1` 8a1f60d7 · `W2` 999efa71 · `W3` 5602e91d · `W4` c83e12a2 · `M1` 0cdc0b31 · `M2` 90493206 · `M3` 06001f88 · `M4` a1561f19 |
| **Status** | **ceremony-ready; NOTHING EXECUTED** (receipt banner; no `--apply`; `default_transaction_read_only=on`) |
| **Provenance — CORRECTED** | Emitted by the **repair developer** (lane-external author). *"Not present in either tree's `repair/emitted/`"* is **true of the REPO-ROOT path** and **false of the lane** — the bundle **IS present, untracked, in this lane's planning directory**. **Cite it by its in-lane path.** |
| **Corroborating pre-checks (from the receipt)** | **A14** task 39564 exists ∧ **status=completed** ⇒ independently corroborates §13's restart re-analysis. **A3** task 39320 `completed_at 2026-11-17 18:08:26.230850` (**precision correction** to the brief's "18:08:20"). **A4** watched worker 542b49a6 `status=completed, updated_at 2026-11-17T19:04:27.795218+00:00`. **A8** `pending_for_parent=4 (unknown=0)` ∧ `outstanding_for_parent=4 (unknown=0)`. **A1/A9** parent `status=waiting_children`. All A1-A14 verdicts **PASS**; a re-run never clobbers an unknown status (A9's guard) |
| **This lane** | neither executes nor re-derives it; references it and states its clock pressure |
| **Clock pressure** | lane-7 TTL #1 ~**02:30Z**; TTL #2 / mission drain ~**04:52-04:53Z** ⇒ **regenerate at ceremony time if past them**; **remainder-only re-emission is the artifact's designed path** |
| **Restart analysis (CORRECTED)** | **task 39564 completed** (A14) ⇒ **all four** watchers have terminal source tasks ⇒ the **boot** sweep (`dependency_bus.py:1753`) would cancel **all four** ⇒ the **bus gate clears** on restart ⇒ **the MISSION gate alone holds** (4 outstanding commissions; draining ~04:53Z). The plan's 22:55Z census was **true when written, false now** ⇒ **re-inspect at ceremony time** |
