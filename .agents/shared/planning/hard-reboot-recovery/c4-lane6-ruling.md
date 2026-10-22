# C4 Ruling — Lane-6 Composition Claim, Hard-Reboot Recovery (`hrr-20261021`)

Date: 2026-10-21
Author: architect (controller) — ruling on reviewer-council finding C4 (`006396b0`)
Worktree: `/home/nea/ensemble-src-wt-hard-reboot-recovery` (branch `feature/hard-reboot-recovery` @ `9897c1f1a`, fenced `.env`: `ENSEMBLE_SELF_ENV=dev` / `ensemble_dev` / `:8079`)
Reviewed artifacts: `architecture-recommendation.md`, `plan-overview.md`, `decisions.md`, `phase1-plan.md`, `docs/runbooks/hard-reboot-recovery.md`, `daemon/services/message_queue_boot_recovery.py`, `daemon/repositories/message_queue/repository.py`, `daemon/repositories/report_injection/repository.py`, `daemon/repositories/task/repository.py`, `daemon/api.py`, `daemon/services/pool_orchestrator.py`, `daemon/tools/instance.py`, `tests/unit/test_message_queue_boot_recovery.py`
Artifacts written by this ruling: **this file only** — no implementation or test code was touched.

---

## 0. Verdict

**RULING: option (a) — NARROW THE CLAIM. The skip-guard stays exactly as implemented.**

The C4 contradiction is real, and the reviewer's read of it is correct in substance: the plan ratified a *guard* and a *benefit* that cannot coexist, so the shipped code is safe while the *claim* is false. But the reviewer's framing leaves the resolution open, and the framing matters here, because the natural-looking fix ("narrow the skip-guard so the composition becomes reachable") is **not safe** — it would break the queue-ownership invariant and create a double-report-delivery window in exactly the hard-reboot-resume case the feature exists for.

Three findings drive the ruling:

1. **The composition is unreachable in EVERY boot ordering, not merely some.** The reset is the *first* recovery pass on the boot path (`daemon/api.py:747`), ahead of StaleTaskRecovery, RDRS, and AutoContinue — and no lane terminalizes an orphan task before it runs (STR's own boot pass is a no-op at boot, by the boot-epoch amnesty clamp). The lane-6 predicate (`mq.status='ready' AND tk.status='running'`) and the reset's skip-guard (task NOT `running`/`paused`) are *mutually exclusive for one row at one instant*. There is no ordering in which they co-hold.
2. **The skip-guard is NOT redundant.** The boot-epoch predicate answers "was this row in-flight when the previous process died?" (an *age* question). The guard answers "does a task row own this row?" (an *ownership* question). At the instant the reset runs, **no task is live in the current boot** (the worker pool starts later, at `api.py:776`), so a `running` task row is always a pre-boot orphan — and the row's fate belongs to that task's lifecycle (AutoContinue resumes it, or STR reaps it at ~boot+10min). Resetting such a row to `ready` would create a second, unowned claim path for a message whose turn is being resumed → **double delivery**. The guard is the only thing preventing it.
3. **The "real, user-visible wedge" claim also fails as written**, and this is a *second, independent* scope-truth defect the C4 review only partially surfaced: `get_stats()` counts `ready` rows in `pending_count` (`message_queue/repository.py:1477-1488`) and the busy-guard trips on `pending_count > 0 OR processing_count > 0` (`daemon/tools/instance.py:4200`). Moving a row `processing → ready` therefore leaves the guard **still tripped**. The wedge is *transformed* into visible-pending, not cleared — on shipped-default installs (`config.yaml:318` `discard_on_startup: false`). The plan already contains this honest wording at `plan-overview.md:176` ("A3 reframing"); the rest of the docs still carry the older, false claim.

**Escalation flag (binding, per the leader's decision rule):** the commission's scope statement to the user asserted lane-6 healing. That assertion is **not deliverable as written**. This ruling therefore corrects the *scope statement*, not just a doc sentence — it must be surfaced in the user-facing report (§7). No production-code change is required, so the ">10 LOC / alters at-least-once delivery semantics" branch of the decision rule resolves to *narrow + escalate*, not *ship a guard change*.

---

## 1. Findings

### 1.1 Q1 — Boot-order reachability: **NO. The composition is unreachable in every ordering.**

Canonical boot order, worktree-verified at `9897c1f1a` (`daemon/api.py` unless noted):

| # | Step | Anchor | Relation to the MQ reset |
|---|---|---|---|
| 0 | PG preflight (exit 75 = tempfail / 78 = permanent) | `daemon/__main__.py:143-247` | — |
| 1 | `InstanceManager.__init__` (engine, `create_all`, migrations, column-ensures, discard CAPTURE) | `api.py:562-568` | — |
| 2 | `await manager.initialize()` | `api.py:723` | — |
| 3 | `capture_boot_epoch(manager.engine)` | `api.py:736` | epoch must exist first (the predicate is epoch-relative) |
| 4 | **MQ boot recovery — the reset** | `api.py:747` → `daemon/services/message_queue_boot_recovery.py:63-103` | — |
| 5 | `probe_critical_notes_boot_state` | `api.py:~755` | — |
| 6 | `recover_stale_leases()` (no-op — `asyncio.Lock`) | `api.py:768` | — |
| 7 | `manager.setup_worker_pool()` | `api.py:776` | claims begin here |
| 7a | └ StaleTaskRecovery wiring + `recover_on_startup()` + `.start()` | `pool_orchestrator.py:313, 315` | **after** the reset |
| 7b | └ StalePauseSweepService wiring | `pool_orchestrator.py:~348-360` | after |
| 7c | └ ReportDeliveryRecoveryService wiring + fire-and-forget boot sweep (lanes 1–6, incl. lane 6) | `pool_orchestrator.py:363, 467/499` | after (async — may interleave, never precedes) |
| 8 | `_boot_rehydrate_question_packs` | `api.py:~790` | after |
| 9 | Deferred discard DELETE_PHASE (only when `QUEUE_DISCARD_ON_STARTUP=true`) | `api.py:~660-676` (post-readyz +1..5s) | after |
| 10 | JobRecoveryService + snapshot sweep | `api.py:1150-1170` | after |
| 11 | DependencyBus `start()` | `api.py:2043` | after |
| 12 | JobProcessor.start; sources start | `api.py:2098-2153` | after |
| 13 | UpgradeJournalSweep wake sweep → **AutoContinue boot pass** | `api.py:2605` | after |
| 14 | Stale-pause boot sweep | `api.py:~2640` | after |

**Does any lane mark orphan tasks dead/failed BEFORE the MQ reset?** **No.** The reset (step 4) precedes every recovery lane in the table. Specifically:

- **STR's boot pass is a no-op at boot, by design.** `find_stale_running_tasks(threshold_minutes, boot_epoch)` returns `[]` whenever `boot_epoch >= threshold` (`daemon/repositories/task/repository.py:3913-3914`) — the stop-frozen-heartbeat amnesty. At boot, `boot_epoch ≈ now` and `threshold = now - 10min` (`stale_task_recovery_threshold_minutes` default 10, `daemon/config.py:1821-1822`, wired at `pool_orchestrator.py:289`), so the boot pass reaps **nothing**. The reap of a pre-boot orphan happens at ~**boot+10min** on the 60s periodic tick.
- **RDRS's boot sweep** (step 7c) runs after the reset and is fire-and-forget on a worker thread (`pool_orchestrator.py:462-470`) — it may interleave with later boot work but can never precede step 4.
- **AutoContinue** (step 13) runs last — the reset always runs *before* any interrupted turn is resumed.

**Can lane 6's `tk.status='running'` ever match a healed row?** — *Yes, the task half can match; the row half never does.*

- The task half: at the reset's instant, the orphan wake task is still `running` (STR has not reaped it — it cannot, per the amnesty clamp). So `tk.status='running'` **holds** throughout the boot → boot+10min window.
- The row half: lane 6 requires `mq.status='ready'` (`report_injection/repository.py:2142`). The reset's skip-guard excludes every row whose task is `running`/`paused` (`message_queue/repository.py:470-471`) — i.e. it excludes **exactly** the rows lane 6 would heal. A claimed-then-killed wake row therefore stays `processing` forever (until STR's reap terminalizes it to `failed` via `reconcile_turn_mirror`, `task/repository.py:2365-2385` — **not** to `ready`).

**Conclusion (definitive):** the conjunction `mq.status='ready' AND tk.status='running'` is unsatisfiable for the reset's target class in every boot ordering. The composition claimed at `architecture-recommendation.md:84` and `:116(b)` does not exist.

**Independent corroboration that lane 6 is not dead code:** lane 6's own docstring (`report_injection/repository.py:1980-2012`) documents its LIVE producer — the *claim-window* shape (wake task claimed while its row is still `ready`, preserved by the F-1 epoch-belt in `clear_all`'s arm-3, `message_queue/repository.py:1360-1372` + `:1396-1404`). That producer is independent of the MQ reset, so **narrowing the claim does not disable lane 6** — it only removes the false attribution. (This is the single most important point for the user report: *lane 6 keeps working; the reset was never one of its inputs.*)

### 1.2 Q2 — The TRUE delivered benefit of the reset as implemented

The reset flips a row iff **all** hold (`repository.py:452-471`):

1. `status='processing'`, and
2. `last_activity_at IS NULL OR last_activity_at < boot_epoch` (stranded before this boot), and
3. NOT (owning instance is TERMINAL) — revive hazard, and
4. NOT (a task with `task.message_id = mq.message_id` is `running`/`paused`) — the skip-guard.

Flipped row classes, with their real consequences:

| # | Row class | Post-reset state | Delivery? | Busy-guard effect |
|---|---|---|---|---|
| A | **Task-less stranded row** (no task row for that `message_id` — the task was deleted/never created/terminalized without the mirror running) | `ready` | **No.** Delivery is task-driven; a task-less row sits at `ready` forever (the repository's own warning, `repository.py:138-150`). The message_queue finalizer will additionally log "task-less READY row counts as pending conservatively" (`message_queue/predicates.py:372-384`). | `processing_count` −1, `pending_count` +1 → **still busy** |
| B | **Row whose task is non-running, non-terminal** (e.g. `pending`/`deferred` after a retry re-arm) | `ready` | **Yes, potentially** — the row becomes claimable again (`claim_specific` requires `status='ready'`, `repository.py:326-360`) once the task's `next_retry_at` comes due. This is the *only* class where the pass unblocks delivery. Narrow and rarely the hard-kill shape. | unchanged (still counted) until claimed+completed |
| C | **Row whose task is already terminal** | `ready` | No (the task is gone) | unchanged |

**Not delivered by the reset, under any row class:**

- ❌ **Lane-6 enablement** — unreachable (§1.1).
- ❌ **Busy-guard wedge clearance** — `get_stats()` counts `ready` as `pending_count` (`repository.py:1477-1488`) and the guard is a disjunction (`tools/instance.py:4200`); the guard's outcome is *unchanged* for every flipped row.
- ❌ **Report-family wake delivery** — a wake row's task is `running`/`paused` → skipped → never flipped.
- ❌ **Re-dispatch of anything** — by design (§2.4 "reset-only"; re-dispatch is permanently out of scope).

**What the reset *does* deliver (the honest claim):** the `processing` bucket is empty after boot; stranded rows are **visible as queued** rather than as *in progress*; and the narrow class B becomes claimable again. On installs with `QUEUE_DISCARD_ON_STARTUP=true` the deferred wipe (`api.py:~660-676`, `clear_all` arm 1) removes the same doomed rows entirely — which is the only path that actually clears the busy-guard.

### 1.3 Q3 — The crux: is the skip-guard redundant?

**Ruling: the guard is the ONLY discriminator, and it is doing a different job than the boot-epoch predicate. It is load-bearing. It is not redundant.**

| | Boot-epoch predicate | Skip-guard |
|---|---|---|
| Question answered | "Was this row in-flight when the previous process died?" | "Does a task row own this row?" |
| Axis | age / epoch (temporal) | ownership (referential) |
| Value at reset time | selects the crash-stranded class | separates *task-owned* rows from *task-less* rows |

Three facts make the guard non-redundant and unsafe to remove:

1. **The guard cannot be replaced by the epoch predicate.** A pre-boot-stranded row whose task is `running` satisfies the epoch predicate and is *still* owned by that task row. Nothing in the epoch predicate says "no live owner".
2. **At reset time, no task is live in the current boot.** The worker pool starts at `api.py:776`, *after* the reset at `:747`; no heartbeat writer, no claimer. So every `running` task row visible to the guard is a **pre-boot orphan**. The guard is therefore "this row belongs to a task lifecycle that will reconcile it (AutoContinue resume, or STR reap)".
3. **Re-arming such a row creates a second owner.** If the guard were narrowed, the row would sit at `ready` while its task is still `running`; the claim path (`claim_specific`, `repository.py:326-360`) would let a worker claim it — producing a **second delivery** of a message whose turn AutoContinue is resuming. Regular instance messages have **no dedup at all**; report-family rows are guarded only for `PENDING`/`DEFERRED` obligations (`report_injection/models.py:47-63`), so a re-delivered report is *not* blocked. This is precisely the at-least-once hazard the reset-only rule was written to avoid (`architecture-recommendation.md:83`).

**The boot-path invariant to state (and to pin in a test):**

> **INV-1 (queue ownership).** A `message_queue` row whose `message_id` is owned by a `task` row in status `running`/`paused` is owned by the TASK lifecycle, not by the queue. The boot pass must never make such a row claimable. Conversely, a `processing` row with **no** owning task (or an owning task that is terminal) has no lifecycle that will ever move it — the boot pass is its only chance to become visible again.

**Corollary:** G2 (queue-rooted, task-blind) and RDRS lane 6 (RDRS-rooted, task-joined) are **disjoint by construction**. A queue-rooted, task-blind pass cannot be a producer for a task-joined predicate. Any future proposal to make G2 feed lane 6 must first break INV-1, and therefore needs a *task-aware* design, not a narrower guard.

### 1.4 Q4 — Recommended resolution: **(a) NARROW THE CLAIM**

**Recommended: (a).** Reasons, in order of weight:

1. **Safety.** (b) breaks INV-1 and creates a double-delivery window in the hard-reboot-resume case (§1.3, §1.5).
2. **The leader's decision rule.** Narrowing the guard *alters at-least-once delivery semantics* — the rule's second trigger. It resolves to *narrow + escalate*, regardless of LOC.
3. **LOC/scope.** A "small" narrowing does not exist. To narrow safely you must exclude the AutoContinue-resumable instances — which requires knowing whether the instance has a resumable checkpoint: a new join against `instances` **and** the checkpointer's thread list (`checkpoint_adapter`/`checkpoints` table), plus a new failure mode (checkpoint probe errors). That is ≫10 LOC and a new subsystem dependency in a plan whose ceiling is ≈44 Python LOC (`architecture-recommendation.md:187`). The residual (task `running` + instance not resumable + no checkpoint) is exactly the **no-checkpoint-orphan class (G4)** that this plan explicitly defers to runbook-only (`plan-overview.md:42`).
4. **Reversibility.** (a) is doc+test only — zero production-code change, zero runtime risk, and the claim it removes was never true.

**Option (c) I do see, and it belongs to a *future* commission, not this one:** a **task-aware MQ ownership reconciliation** pass — for rows whose owning task is dead/terminal, *terminalize* the row via the existing `reconcile_turn_mirror` primitive (row → `failed`) instead of inventing a `ready` state, and for task-less stranded rows, terminalize rather than re-queue. That converts "invisible wedge" into "honest terminal state" without ever creating a second owner. It is a new subsystem (needs task↔message↔instance correlation plus a terminal-state vocabulary decision) and is recorded here as a **tracked follow-up candidate** (see §6, D19-FU1) — explicitly *not* part of this commission.

### 1.5 Q5 — At-least-once delivery safety

**Can a reset of a row whose task is `running` cause double report delivery? — YES, and the boot ordering makes it the DEFAULT, not an edge case.**

If the skip-guard were narrowed for `running`-task rows:

| Boot ordering | Consequence |
|---|---|
| **Any ordering** (the reset always precedes AutoContinue — `api.py:747` < `api.py:2605`) | The row is flipped to `ready` at boot. AutoContinue then resumes the interrupted turn (`:2605`). The resumed turn delivers the message; independently, the row is `ready` and claimable, so a worker can claim it → **the same message content is delivered twice** (a second visible turn for regular messages; a duplicate report for report-family rows, where the obligation triple only guards `PENDING`/`DEFERRED`). |
| Ordering where STR reaps the task first (~boot+10min) | The mirror terminalizes the row to `failed` (`task/repository.py:2365-2385`); the reset already ran, so the row is `ready`→ claimable → a stale, pre-crash message is re-delivered **after** the task was declared dead. |
| `QUEUE_DISCARD_ON_STARTUP=true` install | The deferred wipe would delete the row instead — masking the hazard on those installs while leaving shipped-default installs exposed. |

**Under the CURRENT implementation, no double delivery is possible**: the reset only touches rows whose owning task is not `running`/`paused`, so no live or resumable turn owns them (INV-1 holds).

### 1.6 Q6 — Wording and acceptance rulings (made explicit)

#### (a) The "real, user-visible wedge" claim vs the runbook's disclosure

The **runbook is right and the architecture recommendation is wrong** on this point. The runbook (`docs/runbooks/hard-reboot-recovery.md:94-98`) says *"accounting is honest, but the row is still queued"* — correct, but it understates the consequence. The correct statement:

> The MQ boot pass moves stranded rows from `processing` to `ready`. `get_stats()` counts `ready` rows in `pending_count`, and the agent-to-agent busy-guard trips on `pending_count > 0 OR processing_count > 0` — so **the instance still reads as busy** after the pass. The wedge clears only when the row is subsequently **claimed and completed** (task-driven delivery), or **deleted** by the discard-on-startup wipe (`QUEUE_DISCARD_ON_STARTUP=true`). On the shipped default (`config.yaml:318` `discard_on_startup: false`) the pass **transforms** the wedge into *visible-pending*; it does not clear it. `processing_count == 0` is therefore **not** the same as "not busy".

This wording already exists, in approved form, at `plan-overview.md:176` (the "A3 reframing"). **It is the house-approved wording; propagate it.**

#### (b) Phase-4 acceptance restatement

`plan-overview.md:32` (and the phase-table rows at `:124` and `:178`) currently assert *"post-boot `get_stats()["processing_count"] == 0` + the busy-guard clears"*. That assertion is only true on `QUEUE_DISCARD_ON_STARTUP=true` installs. **Ruling: split the acceptance into a mandatory part and an install-conditional part** (§5 below).

### 1.7 Q7 — The M5 tautology

`tests/unit/test_message_queue_boot_recovery.py:208-210` asserts `processing_count == 0` and then re-asserts `not (processing_count > 0)` — a tautology that (i) never asserts `pending_count`, (ii) never evaluates the guard's actual predicate, and (iii) silently certifies the false "the guard does not wedge" claim in its own docstring. **Replacement assertion set in §4.**

---

## 2. Exact doc-amendment list (the dev applies these)

> Convention: file:line anchors are as-verified on this worktree at `9897c1f1a`. Line anchors drift — re-grep at apply time.

### A1 — `.agents/shared/planning/hard-reboot-recovery/architecture-recommendation.md:84`

**CURRENT:**
> `- **MQ pass × RDRS lanes**: disjoint at read time by `mq.status` (`processing` vs lane-6's required `ready`, `report_injection/repository.py:1974+`). They **compose**: a wake row claimed-then-killed (stuck `processing`, invisible to lane 6) becomes lane-6-healable after the reset; lane-6's task-retry then delivers **once** under the obligation triple. This composition — not "delivery recovery" — is G2's real second benefit.`

**PROPOSED:**
> `- **MQ pass × RDRS lanes**: disjoint — and **mutually exclusive for one row at one instant**, not merely disjoint at read time. Lane 6 requires `mq.status='ready'` (`report_injection/repository.py:2122-2147`), while the reset's skip-guard requires the row's owning task NOT to be `running`/`paused` (`message_queue/repository.py:470-471`) — and a row lane 6 could heal is by construction a row whose wake task is `running` (lane 6 also requires `tk.status='running'`). The reset therefore skips exactly the rows lane 6 would heal. **Correction (D19, 2026-10-21):** the earlier claim that the two "compose" is **FALSE** — the composition is unreachable in **every** boot ordering (the reset runs at `daemon/api.py:747`, ahead of StaleTaskRecovery / RDRS / AutoContinue; no lane terminalizes an orphan task before it runs, and STR's boot pass is a no-op at boot under the boot-epoch amnesty clamp, `task/repository.py:3913-3914`). The reset is **not** a producer of lane-6 candidates, and G2 has **no** lane-6 benefit. Lane 6 remains live on its own production producer — the claim-window shape (wake task claimed while its row is still `ready`, preserved by the F-1 epoch-belt, `report_injection/repository.py:1980-2012`, `message_queue/repository.py:1360-1372`) — which this plan does not affect. **Ruling:** narrow the claim; the skip-guard stays (INV-1, §1.3 of the C4 ruling).`

### A2 — `architecture-recommendation.md:116`

**CURRENT:**
> `- **G2's actual value = (a) queue-accounting hygiene** — stale `processing` rows are counted by `get_stats()` (`repository.py:1415-1480`) and trip the `send_message` busy-guard (`daemon/tools/instance.py:4037`: `pending_count>0 or processing_count>0`), making a *revived* instance look permanently busy to agent-to-agent sends — **a real, user-visible wedge**; **(b) lane-6 enablement** for report-family wake rows (§2.4).`

**PROPOSED:**
> `- **G2's actual value = (a) queue-accounting hygiene** — stale `processing` rows leave the `processing` bucket, so post-boot `get_stats()["processing_count"] == 0` and the stranded row is visible as *queued* rather than *in progress*. **Correction (D19, 2026-10-21):** the earlier text claimed this clears the `send_message` busy-guard (`daemon/tools/instance.py:4200` — the cited `:4037` has drifted): it does **NOT**. `get_stats()` counts `ready` rows in `pending_count` (`message_queue/repository.py:1477-1488`) and the guard is a disjunction (`pending_count>0 or processing_count>0`), so the instance still reads as busy. The wedge clears only when the row is subsequently **claimed and completed** (task-driven delivery) or **deleted** by the discard-on-startup wipe (`QUEUE_DISCARD_ON_STARTUP=true`); on the shipped default (`config.yaml:318` `discard_on_startup: false`) the pass **transforms** the wedge into *visible-pending*. **(b) lane-6 enablement — REMOVED (D19):** unreachable; see the corrected §2.4 above. **Delivered benefit, stated positively:** stranded rows become visible and, for the narrow class whose owning task is non-running and non-terminal, claimable again.`

### A3 — `architecture-recommendation.md:118` (acceptance sentence, same line)

**CURRENT (tail of the line):**
> `**Acceptance re-specified**: assert `get_stats()["processing_count"] == 0` post-boot and the busy-guard clears — NOT "the message was re-delivered."`

**PROPOSED:**
> `**Acceptance re-specified**: assert the stranded row's status is `'ready'` (not `'processing'`) and `get_stats()["processing_count"] == 0` post-boot — NOT "the message was re-delivered" and NOT "the busy-guard clears". The busy-guard-clears assertion holds **only** on installs with `QUEUE_DISCARD_ON_STARTUP=true` (live `.env`; the demo install); on the shipped default the honest assertion is the opposite — the guard still trips (D19). See the C4 ruling §5 for the split acceptance.`

### A4 — `architecture-recommendation.md:172` (phasing verdict)

**CURRENT (fragment):**
> `**P2 remains second** — its re-scoped value (busy-guard wedge clearance + lane-6 enablement) is real but lower-frequency than boot survival.`

**PROPOSED:**
> `**P2 remains second** — its re-scoped value (queue-accounting hygiene: stranded rows become *visible-pending* instead of *in-progress*; the busy-guard is **transformed, not cleared**, on shipped-default installs) is real but lower-frequency than boot survival. The lane-6-enablement claim is **withdrawn** (D19).`

### A5 — `architecture-recommendation.md:182` (LOC table, Phase 2 row, delta-drivers cell)

**CURRENT (fragment):**
> `tests +fresh-dead fixture +busy-guard assertion`

**PROPOSED:**
> `tests +fresh-dead fixture +M5 accounting assertions (pending_count + busy-guard predicate pinned as NOT-cleared on the shipped default; D19)`

### A6 — `architecture-recommendation.md:209` (decision item 3)

**CURRENT:**
> `3. **Ratify G2 action = guarded `processing→ready` reset** (lane-6-enabling, evidence-preserved) over the `complete()`/`fail` alternative. Implementation-time check: one unit test asserting a flipped report-family wake row enters lane-6's predicate and heals exactly once.`

**PROPOSED:**
> `3. **Ratify G2 action = guarded `processing→ready` reset** (evidence-preserved) over the `complete()`/`fail` alternative. ~~lane-6-enabling~~ — **withdrawn (D19)**: the reset is not a producer of lane-6 candidates, because the skip-guard excludes exactly the rows lane 6 would heal (INV-1). **Implementation-time check (replaced, D19):** one unit test that **pins the mutual exclusivity** — a report-family wake row whose task is `running` is NOT flipped by the pass, and the same row does NOT enter lane-6's candidate predicate before OR after the pass. The test documents the non-composition; it does not assert a heal. Specification: `.agents/shared/planning/hard-reboot-recovery/c4-lane6-ruling.md` §3.`

### A7 — `architecture-recommendation.md:229` (evidence index)

**CURRENT:**
> `| Busy-guard wedge | C | `repository.py:1415-1480`; `daemon/tools/instance.py:4037` |`

**PROPOSED:**
> `| Busy-guard predicate (counts `ready` as pending → the wedge is transformed, not cleared) | C | `message_queue/repository.py:1463-1493` (`pending_count` at `:1477-1488`); `daemon/tools/instance.py:4200` (anchor refreshed at D19; the cited `:4037` has drifted) |`

### A8 — `plan-overview.md:32` (Phase-4 demo E2E)

**CURRENT:**
> `- **Phase-4 demo E2E** — REAL environment, NON-live, kill -9 the daemon + reboot the host, observe the daemon come back and the existing AutoContinue + bus + STR + new MQ recover fire; amended per A3 to assert `get_stats()["processing_count"] == 0` post-boot + the busy-guard clears (NOT "the message was re-delivered" — delivery is task-driven only, see `repository.py:138-150`); AND a fresh-dead fixture (row claimed 60s pre-power-loss IS recovered) because the NULL-`last_activity_at` fixture alone would mask §3.3 of the architect's review.`

**PROPOSED:**
> `- **Phase-4 demo E2E** — REAL environment, NON-live, kill -9 the daemon + reboot the host, observe the daemon come back and the existing AutoContinue + bus + STR + new MQ recover fire; amended per A3 to assert `get_stats()["processing_count"] == 0` post-boot (NOT "the message was re-delivered" — delivery is task-driven only, see `repository.py:138-150`); AND a fresh-dead fixture (row claimed 60s pre-power-loss IS recovered) because the NULL-`last_activity_at` fixture alone would mask §3.3 of the architect's review. **Busy-guard assertion (D19, split):** the busy-guard-clears assertion is **install-conditional** — it holds only on a `QUEUE_DISCARD_ON_STARTUP=true` demo install (the deferred wipe removes the doomed rows); on the shipped default (`false`) the mandatory assertion is that the busy-guard **still trips** (visible-pending), and the evidence bundle must record the `pending_count`/`processing_count` pair. See the C4 ruling §5.`

### A9 — `plan-overview.md:124` and `plan-overview.md:178` (phase-table rows)

**CURRENT (fragments):**
> `:124` — `**amended (A3)**: fresh-dead fixture + `get_stats()["processing_count"] == 0` assertion + busy-guard-clears assertion`
> `:178` — `**amended (A3)**: fresh-dead fixture (row claimed 60s pre-power-loss IS recovered) + `get_stats()["processing_count"] == 0` assertion + busy-guard-clears assertion`

**PROPOSED:**
> `:124` — `**amended (A3, D19)**: fresh-dead fixture + `get_stats()["processing_count"] == 0` assertion + busy-guard assertion **split**: cleared only on a `QUEUE_DISCARD_ON_STARTUP=true` demo install; on the shipped default the assertion is that the guard **still trips** (visible-pending)`
> `:178` — `**amended (A3, D19)**: fresh-dead fixture (row claimed 60s pre-power-loss IS recovered) + `get_stats()["processing_count"] == 0` assertion + busy-guard assertion **split** (cleared only on a `QUEUE_DISCARD_ON_STARTUP=true` demo install; still-tripping on the shipped default)`

### A10 — `plan-overview.md:36` and `decisions.md:214` — **BROKEN D13 TRIAGE QUERY**

Both copies ship `t.instance_id = i.id`. The `instances` table's primary key is **`instance_id`** (`daemon/repositories/instance/models.py:51`); there is **no `id` column** on `instances`, so the query fails at runtime with `column i.id does not exist`. The runbook copy (`docs/runbooks/hard-reboot-recovery.md:61-65`) is already correct — the plan and decisions copies are the broken ones.

**CURRENT (both sites):**
> `SELECT ... FROM instances i WHERE i.status='running' AND NOT EXISTS (SELECT 1 FROM task t WHERE t.instance_id=i.id AND t.status IN ('pending','running','paused'))`

**PROPOSED (both sites):**
> `SELECT ... FROM instances i WHERE i.status='running' AND NOT EXISTS (SELECT 1 FROM task t WHERE t.instance_id=i.instance_id AND t.status IN ('pending','running','paused'))`

*(The `i.instance_id` form is also what `docs/runbooks/hard-reboot-recovery.md:62-64` already ships — keep the three copies byte-identical.)*

### A11 — `docs/runbooks/hard-reboot-recovery.md:94-98` (operator disclosure — sharpen)

**CURRENT:**
> `- **QUEUE_DISCARD_ON_STARTUP interplay.** Shipped default is `false` (`config.yaml` → `queue.discard_on_startup`): the MQ boot pass clears stranded rows and the wedge becomes a visible `ready` row — accounting is honest, but the row is still queued. On an install overriding it to `true`, the deferred discard wipe removes the doomed rows instead and `processing_count` reaches 0 post-boot.`

**PROPOSED:**
> `- **QUEUE_DISCARD_ON_STARTUP interplay.** Shipped default is `false` (`config.yaml:318` → `queue.discard_on_startup`): the MQ boot pass moves stranded rows from `processing` to `ready` — accounting is honest, but the row is still queued **and still counted**: `get_stats()` counts `ready` rows in `pending_count`, and the agent-to-agent busy-guard trips on `pending_count > 0 OR processing_count > 0`, so the instance **still reads as busy** after the pass. `processing_count == 0` is not the same as "not busy". The wedge clears only when the row is subsequently claimed and completed (task-driven delivery) or removed by the discard-on-startup wipe. On an install overriding it to `true`, the deferred discard wipe removes the doomed rows instead and both counts reach 0 post-boot. (D19, 2026-10-21.)`

### A12 — `decisions.md` — append **D19** (text in §6).

### A13 — `docs/runbooks/hard-reboot-recovery.md:16` + `plan-overview.md:34` — minor anchor/threshold drift (🟢, optional but recommended)

Both say *"StaleTaskRecovery, `boot+10min` clamp"*. The effective threshold **is** 10 minutes (`daemon/config.py:1821-1822` default `stale_task_recovery_threshold_minutes: int = 10`, wired at `pool_orchestrator.py:289`), but the module constant it falls back to is 15 (`daemon/services/stale_task_recovery.py:25`). Make the wording unambiguous: *"StaleTaskRecovery `boot+~10min` reap (config `stale_task_recovery_threshold_minutes`, default 10; the module fallback constant is 15) — this is the backstop that terminalizes task-owned stranded rows (`reconcile_turn_mirror` → row `failed`), not the MQ pass."* This also removes the reader's likely inference that the MQ pass is what frees a task-owned stranded row.

---

## 3. The §9-3 composition test — exact specification (replaces the "heals exactly once" mandate)

**Name:** `test_lane6_composition_is_mutually_exclusive_documented`
**Home:** `tests/unit/test_message_queue_boot_recovery.py` (the boot-pass pack — the pass is the actor under test).
**Harness:** the shared SQLite harness in `tests/helpers/hard_reboot_fixtures.py` — **extend it** (one canonical harness, house convention):
- add `daemon.repositories.report_injection.models` to the registered-model imports,
- add `ReportInjection.__table__` to the `SQLModel.metadata.create_all(tables=[...])` list in the `engine` fixture,
- add a `seed_report_injection(eng, *, injection_id, parent_instance_id, child_instance_id, child_message_id, content)` helper that inserts a row with `state = ReportInjectionState.PENDING.value` (`report_injection/repository.py:137` — `_PENDING_STATE`),
- add a `seed_task(...)` extension for `last_heartbeat_at` and `task_type` (the current helper signature is `seed_task(eng, work_id, instance_id, message_id, status)` — extend with keyword-only `task_type=...` and `last_heartbeat_at=...`; **keep the existing positional signature intact** so `test_connect_timeout.py` does not break).

**Fixture shape (the live lane-6 wedge, per `report_injection/repository.py:1980-2012` and the PG lane-6 test at `tests/postgres/test_report_delivery_recovery_pg.py:2244`):**

| Entity | Value |
|---|---|
| parent instance | `p1`, `status='running'` |
| child instance | `c1`, `status='completed'` |
| wake message row | `message_id='wake-msg-1'`, `instance_id='p1'`, `source='internal_report:c1:child-msg-1'`, `status='processing'`, `last_activity_at = BOOT_EPOCH - 2min` |
| wake task | `work_id='wake-task-1'`, `instance_id='p1'`, `message_id='wake-msg-1'`, `task_type='process_report'`, `status='running'`, `last_heartbeat_at = BOOT_EPOCH - 2min` (stale ⇒ satisfies lane 6's `last_heartbeat_at < threshold`) |
| report-injection marker | `parent_instance_id='p1'`, `child_instance_id='c1'`, `child_message_id='child-msg-1'`, `state='PENDING'` |

**Assertions (all five, in this order):**

1. `find_stuck_wake_candidates(limit=10) == []` — **before** the pass. Reason: the wake row is `processing`, and lane 6 requires `ready`.
2. `recover_stuck_messages_on_startup(repo, BOOT_EPOCH) == 0` — the pass **does not flip** the row (its task is `running` → skip-guard). This is the INV-1 assertion.
3. `message_status(engine, 'wake-msg-1') == MessageStatus.PROCESSING.value` — the row is untouched.
4. `find_stuck_wake_candidates(limit=10) == []` — **after** the pass, still no candidate. *This is the assertion the old mandate wanted, inverted: the composition does NOT happen.*
5. `repo.get_stats('p1')["processing_count"] == 1` — the row is still counted as in-progress (the guard, not the pass, is what holds it).

**Required test docstring** (the documentation payload of this test — it must state the reason, so a future reader cannot "fix" the test into the old claim):

> `"""D19 — the MQ boot pass and RDRS lane 6 are MUTUALLY EXCLUSIVE for one row.`
> `Lane 6 heals a wake row that is 'ready' while its wake task is 'running'`
> `(report_injection/repository.py:2122-2147). The boot pass flips 'processing'`
> `-> 'ready' ONLY for rows whose owning task is NOT 'running'/'paused'`
> `(message_queue/repository.py:470-471) — INV-1, queue ownership. A row that`
> `lane 6 could heal is therefore skipped by the pass, and a row the pass flips`
> `can never enter lane 6's predicate. The earlier architecture-recommendation`
> `claim that the two 'compose' was FALSE and is withdrawn by D19.`
> `Narrowing the skip-guard to force the composition would re-arm the row while`
> `its task is still running -> the message is delivered twice.`

**Also required:** the same file gets a **control** that the non-composition is not a vacuous artifact of a bad fixture — i.e. a sibling test that flips the *task* to `pending` (so the guard no longer applies), runs the pass, and asserts the row **is** flipped to `ready` — proving the fixture shape is otherwise lane-6-eligible, i.e. that assertion 4 is caused by the **guard**, not by a malformed fixture. (Assert `find_stuck_wake_candidates` on the flipped row is `[]` too — with the task `pending`, lane 6 still cannot match, because lane 6 requires `tk.status='running'`. That is the second half of the mutual exclusivity and must be asserted.)

---

## 4. M5 replacement assertion set

**Replace** `tests/unit/test_message_queue_boot_recovery.py:188-210` (`test_post_pass_processing_count_is_zero_and_guard_does_not_wedge`) with the following two tests.

### M5-a — `test_post_pass_accounting_is_honest_and_busy_guard_is_not_cleared`

```python
def test_post_pass_accounting_is_honest_and_busy_guard_is_not_cleared(repo, engine):
    """M5 — post-pass accounting: the processing bucket empties, the pending
    bucket does NOT, so the busy-guard STILL trips (D19).

    The busy-guard predicate is ``pending_count > 0 or processing_count > 0``
    (daemon/tools/instance.py:4200) and ``get_stats()`` counts 'ready' rows
    in ``pending_count`` (message_queue/repository.py:1477-1488). A stranded
    row flipped 'processing' -> 'ready' therefore leaves the TOTAL pending
    count unchanged: the wedge is TRANSFORMED into visible-pending, NOT
    cleared. This test pins that fact so the claim cannot silently drift
    back to "the busy-guard clears".
    """
    seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    reset = recover_stuck_messages_on_startup(repo, BOOT_EPOCH)
    stats = repo.get_stats("i1")

    assert reset == 1
    # (1) the row is now a normal, deliverable queued row
    assert message_status(engine, "m_fresh") == MessageStatus.READY.value
    # (2) the processing bucket is empty
    assert stats["processing_count"] == 0
    # (3) ...but the pending bucket is NOT — the count moved, it did not drop
    assert stats["pending_count"] == 1
    # (4) the busy-guard predicate is STILL satisfied -> the wedge is
    #     transformed, not cleared (the shipped-default behaviour)
    assert stats["pending_count"] > 0 or stats["processing_count"] > 0
```

### M5-b — `test_stranded_row_is_indistinguishable_from_a_freshly_queued_row` (control)

```python
def test_stranded_row_is_indistinguishable_from_a_freshly_queued_row(repo, engine):
    """M5 — control: after the pass a stranded row is EXACTLY a queued row.

    This is the real invariant of the pass: a row that was stranded in
    'processing' by the crash is indistinguishable, to every consumer
    (claim path, accounting, finalizer), from a row that was enqueued and
    never claimed. It is what makes the reset safe; it is also why the
    busy-guard does not clear (both rows count as pending).
    """
    seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    seed_instance(engine, "i2", InstanceStatus.RUNNING.value)
    seed_message(                       # stranded by the crash
        engine, "m_stranded", "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )
    seed_message(                       # never claimed, enqueued normally
        engine, "m_fresh_queued", "i2",
        last_activity_at=None, status=MessageStatus.READY.value,
    )

    recover_stuck_messages_on_startup(repo, BOOT_EPOCH)

    stranded = repo.get_stats("i1")
    queued = repo.get_stats("i2")

    assert message_status(engine, "m_stranded") == MessageStatus.READY.value
    assert stranded["processing_count"] == queued["processing_count"] == 0
    assert stranded["pending_count"] == queued["pending_count"] == 1
```

### M5-c — the guard's predicate, exercised on the accounting axis (add to M3)

Extend `test_live_task_row_is_untouched` (`:135-150`) with the accounting counterpart so the guard is proven to be what holds the row:

```python
    # the guard — not the pass — is what keeps this row out of the reset;
    # assert it on the accounting axis too (processing_count stays 1)
    assert repo.get_stats("i1")["processing_count"] == 1
```

*(The dev may fold this into the existing parametrized M3 test — the requirement is that the guard's **actual predicate** is asserted at least once on the accounting axis, and that `pending_count` is asserted at least once. The old tautological `assert not (stats["processing_count"] > 0)` must be deleted, not kept.)*

---

## 5. Phase-4 acceptance restatement (exact)

**Mandatory on every install shape (shipped default or otherwise):**

| # | Assertion | Where |
|---|---|---|
| P4-1 | The stranded row's `status` is `'ready'` (not `'processing'`) after boot | `SELECT status FROM message_queue WHERE message_id='<seeded>'` |
| P4-2 | `get_stats()["processing_count"] == 0` for the owning instance | the demo's own stats read |
| P4-3 | `get_stats()["pending_count"] >= 1` — the row is still counted | same read; **recorded in the evidence bundle** |
| P4-4 | The busy-guard **still trips** on that instance (`pending_count > 0 or processing_count > 0`) on the shipped default | agent-to-agent send attempt to the instance must be rejected as busy; the rejection line records `pending_count`/`processing_count` |
| P4-5 | The fresh-dead fixture (row claimed 60s pre-power-loss) IS recovered — the boot-epoch predicate works | as today |
| P4-6 | A task-owned stranded row (task `running`) is NOT flipped — INV-1 holds | `SELECT status` on the task-owned row; `processing_count == 1` for that instance |

**Install-conditional (only on a `QUEUE_DISCARD_ON_STARTUP=true` demo install):**

| # | Assertion |
|---|---|
| P4-7 | `processing_count == 0` **and** `pending_count == 0` for the owning instance, and the busy-guard **does not** trip — the deferred wipe removed the doomed rows. |

**Discipline:** the evidence bundle (`.agents/tester/RESULTS/2026-10-19-hard-reboot-recovery-e2e.md` per `plan-overview.md:178`) must record the `pending_count`/`processing_count` pair and the busy-guard outcome for the seeded instance, and must state which install shape produced it. A run that reports only "processing_count == 0" without the pending count and the guard outcome is **incomplete**.

---

## 6. Proposed **D19** entry text (for `decisions.md`)

> ## D19. G2's lane-6-enablement claim is WITHDRAWN — the claim is narrowed to queue-accounting hygiene; the skip-guard stays (INV-1)
>
> **Decision (recorded ruling 6, 2026-10-21 — architect ruling on reviewer-council finding C4, `006396b0`):**
>
> 1. **The claim "the MQ boot reset enables RDRS lane-6 healing" is WITHDRAWN as factually unreachable.** The reset's skip-guard (a row whose owning task is `running`/`paused` is skipped) excludes exactly the rows lane 6 heals (lane 6 requires `mq.status='ready'` AND `tk.status='running'`). The composition is unreachable in **every** boot ordering: the reset runs at `daemon/api.py:747`, ahead of StaleTaskRecovery (`api.py:776` → `pool_orchestrator.py:313`), the RDRS boot sweep (`pool_orchestrator.py:467/499`), and AutoContinue (`api.py:2605`); no lane terminalizes an orphan task before it runs; and STR's own boot pass is a no-op at boot under the boot-epoch amnesty clamp (`task/repository.py:3913-3914`).
> 2. **The skip-guard is NOT narrowed — it is load-bearing (INV-1).** *Queue ownership:* a `message_queue` row whose `message_id` is owned by a `task` row in status `running`/`paused` is owned by the TASK lifecycle, not by the queue, and the boot pass must never make it claimable. Narrowing the guard would re-arm such a row while AutoContinue is resuming its turn (`api.py:2605`), and the row would then be claimed a second time → **double delivery** of the same message. Regular instance messages have no dedup; report-family rows are guarded only for `PENDING`/`DEFERRED` obligations. Narrowing the guard also exceeds the plan's ≈44-Python-LOC ceiling (it needs a checkpoint-existence probe for the instance) and would drag in the no-checkpoint-orphan class (G4) that this plan defers to runbook-only.
> 3. **The delivered benefit is narrowed to what the pass actually does:** stranded rows leave the `processing` bucket and become visible as *queued* (`processing_count == 0` post-boot); the narrow class whose owning task is non-running and non-terminal becomes claimable again. **The busy-guard is NOT cleared on shipped-default installs** — `get_stats()` counts `ready` in `pending_count` (`message_queue/repository.py:1477-1488`) and the guard is a disjunction (`daemon/tools/instance.py:4200`), so the wedge is **transformed into visible-pending**, not cleared. It clears only on claim-and-complete or on a `QUEUE_DISCARD_ON_STARTUP=true` install (deferred wipe).
> 4. **The §9-3 implementation-time check is replaced** with the mutual-exclusivity (documentation) test specified in `.agents/shared/planning/hard-reboot-recovery/c4-lane6-ruling.md` §3 — it pins that a report-family wake row whose task is `running` is not flipped and does not enter lane 6's predicate before or after the pass.
> 5. **Scope-truth correction is surfaced, not buried.** The commission's scope statement to the user asserted lane-6 healing; that assertion is not deliverable as written. It is escalated with the leader for the user report (C4 ruling §7).
> 6. **Lane 6 is unaffected.** Its own production producer (the claim-window shape — wake task claimed while its row is still `ready`, preserved by the F-1 epoch-belt) is independent of the MQ reset (`report_injection/repository.py:1980-2012`). Narrowing this claim disables nothing that works today.
>
> **Rationale:**
> - The contradiction is **inherited**: `plan-overview.md:28` mandates the guard and `architecture-recommendation.md:116` endorsed a benefit that cannot coexist with it. The implementer followed the more specific instruction literally, which was the correct call under contradiction — **the code is SAFE; the CLAIM is what fails.** This ruling fixes the claim, not the code.
> - The skip-guard and the boot-epoch predicate answer different questions (ownership vs age). The guard is the only discriminator between "a task lifecycle owns this row" and "nothing will ever move this row". At reset time no task is live in the current boot (the worker pool starts at `api.py:776`, after the reset at `:747`), so a `running` task row is always a pre-boot orphan whose fate belongs to AutoContinue (resume) or STR (reap at ~boot+10min).
> - The leader's decision rule applies twice over: narrowing the guard **alters at-least-once delivery semantics** (the rule's second trigger) and would exceed 10 LOC. Both branches resolve to *narrow the claim + escalate*.
> - The honest wording already exists in the plan (`plan-overview.md:176`, the "A3 reframing"); this ruling propagates it to the architecture recommendation, the phase rows, and the runbook, and pins it with a non-tautological M5.
>
> **Rejected alternatives:**
> - **Narrow the skip-guard to make the composition reachable** — breaks INV-1, creates a double-delivery window in the hard-reboot-resume case, needs a checkpoint probe (≫10 LOC), and imports the G4 no-checkpoint-orphan class. Rejected on safety, scope, and the leader's decision rule.
> - **Make G2 lane-aware (join RDRS) to feed lane 6 directly** — violates the G2.A "no RDRS join" amendment (`architecture-recommendation.md:92, :96`), inherits the loop-thread hazard the RDRS wiring already carries, and is a seventh lane in a framework whose value-add does not apply.
> - **Delete the pass** — it still delivers real value (the processing bucket empties; the stranded row becomes visible; a narrow class becomes claimable again) at zero risk. Rejected.
> - **Fix the claim only in the architecture recommendation** — the same false claim appears in `plan-overview.md:32/:124/:178` and (in weakened form) in the Phase-4 acceptance. Rejected as incomplete.
>
> **D19-FU1 (tracked follow-up candidate, NOT part of this commission):** a *task-aware MQ ownership reconciliation* pass — for rows whose owning task is dead/terminal, terminalize the row through the existing `reconcile_turn_mirror` primitive (row → `failed`) instead of re-queueing it to `ready`, and terminalize task-less stranded rows rather than re-queuing them. This converts "invisible wedge" into "honest terminal state" without ever creating a second owner, and it is the only path that would clear the busy-guard for the stranded classes on shipped-default installs. It is a new subsystem (task↔message↔instance correlation + a terminal-state vocabulary decision) and is out of scope here.

---

## 7. Escalation to the leader (binding — scope-truth correction)

**This must be surfaced in the user-facing report, not buried in the docs.**

1. **What the user was told vs what ships.** The commission's scope statement asserted lane-6 healing of report-family rows as a delivered benefit of the hard-reboot recovery work. **That benefit is not deliverable as specified and is not shipped.** The implemented pass is safe and does deliver queue-accounting hygiene; the lane-6 attribution is withdrawn.
2. **The second correction, same family.** The pass does not clear the agent-to-agent busy-guard on shipped-default installs — it converts the wedge into *visible-pending*. On the live install (`QUEUE_DISCARD_ON_STARTUP=true`) the deferred wipe clears it; on shipped-default installs it does not.
3. **What is NOT broken.** No production code changes; no regression risk introduced by this ruling; the implemented behavior is safe under the at-least-once model. Lane 6 itself continues to work on its own producer.
4. **Leader decisions requested (two, both small):**
   - **(i) D17 re-open?** D17 parked the `QUEUE_DISCARD_ON_STARTUP` default flip. With the pass's wedge-clearing value now shown to be nil on shipped-default installs, that default is the *only* mechanism that clears the busy-guard for stranded rows. Flipping it is **destructive** (it DELETES queued backlog at boot), so this is a trade-off, not a fix — but the parking rationale ("G2's hygiene value justifies flipping it") is now void, and the decision should be re-taken with the corrected facts. **My recommendation: keep it parked** (silent backlog deletion is worse than a visible-pending row), and instead make the runbook's operator triage explicit.
   - **(ii) D19-FU1 commissioning.** The task-aware ownership reconciliation (§6, D19-FU1) is the only mechanism that would truly clear these wedges without destructive defaults. It is a new subsystem and needs its own commission (~45–60 LOC, over this plan's ceiling). Recommend it be tracked as a follow-up commission alongside D13.

---

## 8. Evidence index (all anchors worktree-verified at `9897c1f1a`)

| Finding | Anchor |
|---|---|
| Reset placement (first recovery pass) | `daemon/api.py:736` (epoch), `:747` (reset), `:768` (leases), `:776` (pool), `:2605` (AutoContinue) |
| STR boot pass is a no-op at boot (amnesty clamp) | `daemon/repositories/task/repository.py:3913-3914`; threshold 10 min from `daemon/config.py:1821-1822`, wired `pool_orchestrator.py:289` |
| STR reap terminalizes the MQ row (`failed`), never `ready` | `daemon/repositories/task/repository.py:2365-2385` (mirror), `:5435-5590` (cancel path → mirror) |
| RDRS boot sweep ordering | `daemon/services/pool_orchestrator.py:313, 315, 363, 467, 499` |
| Skip-guard (task `running`/`paused`) | `daemon/repositories/message_queue/repository.py:470-471` |
| Terminal-instance skip-guard | `daemon/repositories/message_queue/repository.py:468-469` |
| Boot-epoch selector | `daemon/repositories/message_queue/repository.py:425-445` |
| Lane-6 predicate (`mq.status='ready'`, `tk.status='running'`, stale heartbeat) | `daemon/repositories/report_injection/repository.py:2122-2147` (query), `:1974-2055` (docstring / live producer) |
| Lane-6's own producer (claim-window shape) | `daemon/repositories/report_injection/repository.py:1980-2012`; F-1 epoch-belt `daemon/repositories/message_queue/repository.py:1360-1372, 1396-1404` |
| `get_stats` counts `ready` as pending | `daemon/repositories/message_queue/repository.py:1463-1493` (`pending_count` at `:1477-1488`) |
| Busy-guard predicate | `daemon/tools/instance.py:4200` (cited `:4037` has drifted) |
| Task-less READY row finalizer warning | `daemon/repositories/message_queue/predicates.py:372-384` |
| Resume path skips messages whose task is RUNNING | `daemon/manager.py:12530-12620` |
| Claim path requires `ready` | `daemon/repositories/message_queue/repository.py:290-360` (`dequeue`), `:326-360` (`claim_specific`) |
| Report obligation triple guards only PENDING/DEFERRED | `daemon/repositories/report_injection/models.py:47-63` |
| `instances` PK is `instance_id` (no `id` column) | `daemon/repositories/instance/models.py:51` |
| `discard_on_startup` shipped default `false` | `config.yaml:318`; env override `daemon/config.py:6096-6098` |
| Reviewer's executed probe (reset count 0; lane-6 candidates empty; counterfactual `ready` → 1 candidate) | reviewer council `006396b0`, finding C4 |

---

*End of ruling. The dev applies the §2 doc amendments, the §3 test specification, the §4 M5 replacement, and the §5 Phase-4 restatement; the §6 D19 entry is appended to `decisions.md`. No implementation or test code is changed by this ruling itself.*

---

## 9. Leader rulings on this artifact (2026-10-21) — Q1, Q2, scope-truth

> Appended by the implementation lane (fold round B) so this artifact is
> self-consistent: the architect's §7 asked the leader two questions; the
> answers are recorded HERE, next to the finding they answer.

### 9.1 Q1 — D17 (`QUEUE_DISCARD_ON_STARTUP` default flip): **STAYS PARKED**

**Decision (leader, 2026-10-21):** D17 **STAYS PARKED**. Its "G2 hygiene value"
rationale is **VOID** — the pass does not clear the busy-guard on
shipped-default installs (D19: `get_stats()` counts `ready` rows in
`pending_count`, so the wedge is *transformed into visible-pending*, not
cleared). But flipping the default is **NOT the answer**: silent backlog
deletion at boot is worse than a visible-pending row. The decision is
therefore re-taken with the corrected facts and the default stays as it is.

**What replaces the void rationale:** an **explicit operator triage** for a
visible-pending row. The runbook now carries it —
`docs/runbooks/hard-reboot-recovery.md` **§6.2 "Operator triage — a
visible-pending row after the boot (D17 parked)"**: confirm the row is the
pass's output (`ready` + `processing_started_at IS NULL`) → check whether a
task owns it (a `running`/`paused` task ⇒ task-owned, leave it to AutoContinue
/ STR's reap; a `pending`/`deferred` task ⇒ the claim path will deliver it; NO
task ⇒ the D13 task-less class, report it) → and never hand-edit the row
(R-03). The busy-guard clears only when the row is claimed and completed, or on
a `QUEUE_DISCARD_ON_STARTUP=true` install where the deferred wipe removes the
doomed rows.

**Recorded in:** `decisions.md` D17 (dated addendum) + the runbook §6.2.

### 9.2 Q2 — D19-FU1: **COMMISSIONED as a tracked follow-up, NOT part of this commission**

**Decision (leader, 2026-10-21):** the task-aware MQ ownership reconciliation
described in §6 (D19-FU1) is **COMMISSIONED as a tracked follow-up** — it is
**NOT** part of this commission and does **NOT** ship with it.

**Scope marker (carried verbatim into the plan record):** D19-FU1 ≈ **45–60
LOC** of production code — for rows whose owning task is dead/terminal,
terminalize the row through the existing `reconcile_turn_mirror` primitive
(row → `failed`) instead of re-queueing it to `ready`, and terminalize
task-less stranded rows rather than re-queuing them. It converts "invisible
wedge" into "honest terminal state" without ever creating a second owner, and
it is the only path that would clear the busy-guard for the stranded classes
on shipped-default installs. It is a new subsystem (task↔message↔instance
correlation + a terminal-state vocabulary decision) and it exceeds this plan's
≈44-Python-LOC ceiling — hence a separate commission, not a scope creep here.

**Recorded in:** `plan-overview.md` §6 (the PARKED row now names
**D19-FU1 MQ ownership reconciliation ≈45–60**), `decisions.md` D19
(D19-FU1 paragraph), and §9.2 here.

### 9.3 SCOPE-TRUTH (carried verbatim into the plan record)

> The commission's scope statement asserted RDRS lane-6 healing of
> report-family rows — that is **NOT delivered and does NOT ship**; the code
> is safe, the claim was false. Lane 6 keeps working via its live producer
> (claim-window shape, F-1 epoch-belt), independent of the MQ reset; narrowing
> the claim disables nothing that works today.

**Recorded in:** `plan-overview.md` §2 (the SCOPE-TRUTH bullet, in-scope list),
`decisions.md` D19 §5, and §9.3 here. The **user-facing** scope-truth
correction is carried by the leader in the commission report (this ruling's
§7); the plan record carries the same sentence so no artifact re-asserts the
withdrawn benefit.

### 9.4 What fold round B applied against this ruling (for the audit trail)

| Ruling item | Applied | Where |
|---|---|---|
| §2 A1–A9, A11, A13 | YES — the amendment text is applied VERBATIM (extracted programmatically from this file) | `architecture-recommendation.md` (:84, :116, :118, :172, :182, :213, :233), `plan-overview.md` (:32, :128, :182, STR-backstop bullet), `docs/runbooks/hard-reboot-recovery.md` (:16, the QUEUE_DISCARD bullet) |
| §2 A10 (the broken D13 query) | ALREADY CORRECT in all four copies — verified, no edit needed; proof by execution recorded in the round-B report | `plan-overview.md:36`, `decisions.md:214`, `architecture-recommendation.md:168`, `docs/runbooks/hard-reboot-recovery.md:65` |
| §2 A12 (D19) | YES — appended verbatim | `decisions.md` (new `## D19.` entry, placed after D18, before the honest-audit table) |
| §3 (the §9-3 composition test) | YES — the five ordered assertions + the required docstring + the control + a positive control | `tests/unit/test_message_queue_boot_recovery.py` |
| §4 (M5 replacement set) | M5-a: round A's test already satisfies it (kept, mapping documented in its docstring) · M5-b: ADDED (round A lacked it) · M5-c: already present in round A's M3 test | `tests/unit/test_message_queue_boot_recovery.py` |
| §5 (Phase-4 acceptance split) | YES — P4-1..P4-6 mandatory on every install, P4-7 install-conditional | `plan-overview.md:32` + `:128` + `:182`, `docs/runbooks/hard-reboot-recovery.md` §6.1 |
| §6 (D19 text) | YES — verbatim | `decisions.md` |
| §9.1–§9.3 (leader rulings) | YES — recorded here, in `decisions.md` (D17 addendum + D19) and in the plan | this file, `decisions.md`, `plan-overview.md` |
