# Decisions: Durability F-1 + F-2

Date: 2026-10-04
Worktree: `/home/nea/ensemble-src-wt-durability` @ `18827dbd` (branch `feature/durability-f1-f2`)
Author: planner via plan-creation worker

> **Format.** Each decision is numbered. Each section records: the
> decision, the rationale, the alternatives considered (with the
> rejection reason), and the citation that settles the question.
> Citations reference `research-findings.md` (worktree-anchored
> explorer reports) or specific line-anchored code in the worktree.

---

## §1 — F-1 bus-side fix: only the two DependencyBus gates (Option 1)

> **🟢 Phase 1 LANDED 2026-10-04** (concurrent landing by the
> developer in the UNCOMMITTED working tree atop
> `18827dbd`). **Anchors re-based to the landed state:**
> * `_has_truthy_error` def at `dependency_bus.py`**:98**
>   (reviewer cited `:111` — landed at `:98`; re-grep before
>   citing, the developer may have advanced further)
> * Truthy-error gate calls at `dependency_bus.py`**:702**
>   (first gate, `emit_terminal`) and **`:907`** (second
>   gate, `emit_terminal_for_child_instance`)
> * None-path defensive `WARNING` log references at
>   `dependency_bus.py`**:697** (first gate) and **`:901`**
>   (second gate)
> * Prior cycle's pre-landing anchors `:671` / `:859` are
>   SUPERSEDED by the landed state. See `phase1-plan.md`
>   task 1.3 for the read-back verification task.
> The decision is UNCHANGED: only the two DependencyBus
> gates need the fix (per the surprise-scan at task 1.1).
> Below: the ORIGINAL decision rationale + the landed
> anchor map for cross-referencing.

**Decision.** Apply the truthy-error condition at the two and only two
DependencyBus gates that currently stamp `_parent_errored` /
`_parent_error_message`:

* `daemon/services/dependency_bus.py:671` — `emit_terminal` —
  change `if outcome.status == "error":` to
  `if outcome.status == "error" and outcome.error:` (mirrored as a
  helper `_has_truthy_error(outcome)` for the two sites so the
  condition reads identically).
* `daemon/services/dependency_bus.py:859` —
  `emit_terminal_for_child_instance` — same helper, same condition.

The inside-block change is the same at both sites: when the truthy
condition fails, the `_parent_errored` flip is **skipped entirely**
(do not stamp `True` and do not stamp the `"child agent error"`
fallback). The downstream `JobFeedbackObserver._resolve_finalize_status`
at `job_feedback_observer.py:171-174` already has its OWN fallback
(`bus.parent_error_message(...) or CHILD_AGENT_ERROR_FALLBACK`, the
constant defined at `:122`); that observer path is unchanged and
remains the safety net for any caller that bypasses these two gates.

**Rationale.** The F-1 wedge is a producer bug, not an observer bug.
`stale_task_recovery.py:805-810` is on a different code path
(STR `fail_task` with explicit non-None error text, off the wedge
because STR filters `status == 'running'` at `repository.py:3380` and
the F-1 row is already terminal at boot N+1). A bus-level gate is the
narrowest possible fix.

**Alternatives considered.**

* **Tighten the producer contracts** at
  `child_reports.py:420, :653` (`_emit_terminal_via_bus` /
  `_emit_terminal_for_child_instance_via_bus`) to require explicit
  error text when `status == "error"`. **REJECTED for this release.**
  Rationale: changing producer contracts is a wider blast radius
  (every call site would need to be audited for "no error but
  status==error" semantics; the producer is intentionally
  None-tolerant for the terminated branch at
  `instance_lifecycle.py:256`); a downstream gate is safer and
  narrower. **However** — see §1a below: the producer side gets a
  defensive `logging.warning` for any future regression that reaches
  these two gates with `status==error and error is None`, so a
  regression is loud in the log, not silent.
* **Tighten the observer** (`job_feedback_observer.py:171-174`) to
  re-check error truthiness before flipping. **REJECTED.** The
  observer has its own `CHILD_AGENT_ERROR_FALLBACK` for a reason: it
  is the safety net. Tightening it would not fix the upstream
  `_parent_errored=True` flip on the bus; that sticky True is the
  wedge input, regardless of what the observer's own fallback
  contains.
* **Wider bus fix that changes `Outcome.status == "error"` consumers
  more broadly.** **REJECTED.** Surprise scan (Explorer A Q6) shows
  no other boot-time error=None → terminal-error site; expanding
  scope would touch unrelated code paths and is not justified by
  evidence.

**Settling citation.** Explorer A Q1, Q6; the observer's own fallback
at `job_feedback_observer.py:122, :171-174`; the F-1 finding at
`.agents/tester/RESULTS/2026-10-04-auto-continue-merge-gate.md:78`.

### §1a — Producer-side defensive log (companion to §1)

**Decision.** Add a single `logger.warning(...)` line at each of the
two bus gates (`:671`, `:859`) when the gate is reached with
`outcome.status == "error"` but `outcome.error is None`. The log line
includes the `task_id` (truncated to 8 chars), the `parent_instance_id`
(or the target set, for `:671`), and the `outcome.status`. **No code
path change beyond the log** — the bus still log-and-skips the
`_parent_errored` flip (per §1).

**Rationale.** Producer contracts in `child_reports.py:420, :653` are
intentionally None-tolerant; tightening them is deferred (see §1).
A loud log keeps the latent producer bug visible in operator logs
when a regression recurs, while leaving the narrower bus-side fix
intact.

**Alternatives considered.** Promoting the warning to an `assert` —
**REJECTED**, asserts can be stripped under `-O`; promoting to an
exception — **REJECTED**, that would break the terminated branch at
`instance_lifecycle.py:256` (which produces `Outcome(status="error",
error=None)` legitimately in the
"child terminated without an error message" code path).

---

## §2 — F-1 wipe-side extension: arm-3-only disjunction (REVISION CYCLE 2: arm 2 DROPPED per W-3)

**Decision (current).** The keep-set is a 2-arm disjunction
applied to BOTH `TaskRepository.clear_all`
(`repository.py:4380-4394` and JOURNAL mirror `:4337-4364`) AND
`MessageQueueRepository.clear_all` (`:1002-1008` and JOURNAL
`:978-994`). The disjunction is:

1. `status IN ('running', 'paused')` (current first arm), OR
2. `auto_continued_at IS NOT NULL` AND `EXISTS (SELECT 1 FROM
   instances WHERE instances.id = task.instance_id AND
   instances.status NOT IN TERMINAL_INSTANCE_STATUSES)`
   (arm 3 with instance-non-terminal co-condition — see §13b
   for the lifecycle bound).

Arm 2 (`last_heartbeat_at >= boot_epoch`) is **DROPPED per
W-3** — vacuous at the wipe seam (epoch captured microseconds
pre-wipe in a fresh process). The grace-window rewrite
alternative is **REJECTED** (new tunable + over-preservation
accumulation + wider blast radius; root cause is closed
write-side by §1's truthy-error gate). See §13a for full
rationale.

**Why the boot-epoch capture stays (§2a is still valid).**
The `capture_boot_epoch(self.engine)` call in the
`InstanceManager.__init__` is RETAINED even though arm 2 is
dropped: `boot_epoch` is also consumed by the auto-continue
boot pass at `api.py:1553` and other consumers at
`:2361, stale_task_recovery.py:766/:407, repository.py:3363`.
The auto-continue boot pass is in Phase 1 scope; removing the
constructor capture would break its epoch availability. The
`None` boot_epoch fallback (`§2a`) stays — degraded behavior
on `boot_epoch` capture failure still applies to the
auto-continue pass.

**Settling citation.** W-3 directive (REVISION CYCLE 2);
§13a (arm-2 removal rationale); §13b (arm-3 lifecycle bound);
the call-site-gate pattern at `manager.py:11673-11683`;
`TERMINAL_INSTANCE_STATUSES` at `daemon/constants.py:584-589`.
  disjunction — implementers can drop arm 2 alone, leaving arms 1
  and 3 intact, and ship a follow-up to enable arm 2.
* **Use `started_at` instead of `last_heartbeat_at`.** **REJECTED.**
  `started_at` is set at row creation and never updated; an
  in-flight task with a stale `started_at` (e.g. the parent set the
  task up 30 minutes ago and the worker just acquired it) would be
  incorrectly preserved. `last_heartbeat_at` is the writer the
  worker loop already maintains; using it keeps semantics
  consistent with the existing boot-epoch amnesty logic at
  `repository.py:3375-3376`.

**Settling citation.** Dispatcher synthesis item 2; the boot sequence
walk in Explorer B; `daemon/services/boot_epoch.py:70-76, :91-133`;
`daemon/repositories/task/repository.py:4380-4394, :3375-3376`.

### §2a — None-boot_epoch case

**Decision.** When `boot_epoch is None` at the wipe (capture failed —
DB unreachable, schema drift, etc.), the keep-set is the
**disjunction of arm 1 and arm 3 only** (`status IN ('running',
'paused')` OR `auto_continued_at IS NOT NULL`); arm 2 is skipped. The
boot log carries a single `WARN` line at the predicate's call site
naming the missing-epoch path. The `None` case is **not** an error
path — the feature continues to operate; it just falls back to the
narrower keep-set.

**Rationale.** The epoch is best-effort by design (Explorer A Q4,
`boot_epoch.py:115-130`); the system must boot in the absence of a
captured epoch. A degraded but correct keep-set is preferable to a
boot that aborts.

**Settling citation.** Same as §2: dispatcher synthesis item 2; the
boot sequence walk in Explorer B; `daemon/services/boot_epoch.py:115-130`.

### §2b — Orphan-sweep interaction with preserved-terminal tasks (NEW)

**Decision.** The F-1 preserve-predicate extension (2-arm
disjunction per W-3, REVISION CYCLE 2: arm 1 `status IN
('running','paused')` + arm 3 `auto_continued_at IS NOT NULL`
with the `EXISTS instances` instance-non-terminal
co-condition per §13b) keeps terminal tasks with an
`auto_continued_at` marker. Such preserved-terminal tasks
are NOT in the bus's orphan-sweep active-set predicate
(`dependency_bus.py:1919-1920`:
`status IN ('running','pending','paused')`). A PENDING watcher on
a preserved-terminal task would be cancelled by the orphan sweep as
a "true orphan" (30s grace at `:1984-1988`).

**Normal flow:** a child that terminal-ed via `emit_terminal` has its
watcher FIRED (atomic PENDING→FIRED transition), not PENDING — no
orphan-sweep interaction.

**F-1 wedge flow:** the orphan sweep at `bus.start()` (boot step 7)
runs BEFORE RDRS's `recover_on_startup` (boot envelope).
RDRS lane 2 (the no-row backstop, extended per W-4 — see §12)
is the compensating backstop: it admits anchor-less completed
children of non-terminal parents post-wipe, drives the
existing deferred-marker chain, and materializes a
`PROCESS_REPORT` Task + `MessageQueue` row that the parent
wakes through. The orphan sweep may cancel the PENDING child
watcher within the 30s grace; RDRS's periodic 300s loop
re-evaluates; the wedge for a non-terminal parent heals.

**Operator-facing risk:** the 30s orphan-sweep grace + RDRS's
per-row pass duration bound the recovery latency to
(grace + sweep-duration) ≈ 30s + O(N) for N waiting_children
parents (RDRS's per-row pass serializes the parent scan;
the periodic 300s loop is the steady-state canary).

**Settling citation.** `daemon/services/dependency_bus.py:1896-2029`
(sweep def `:1896`, grace `:1984-1988` = 30s default, active-set
predicate at `:1919-1920` filtering
`status IN ('running','pending','paused')`); the 13-step boot
ownership contract at §5 (step 11 is now RDRS lane-2 boot
pass, NOT the eliminated WC sweep — see §5 step 11 + §12a);
the RDRS lane-2 compensating backstop is documented at §12.

### §2c — F-1 predicate kill-switch (NEW) (REVISION CYCLE 2: renamed + rescoped to arm 3 per W-3)

**Decision (current).** The F-1 predicate extension
(arm 3 with instance-non-terminal co-condition per §13b) is
hot-path (every daemon restart exercises it). If arm 3
over-preserves in production, task / message_queue rows
accumulate across boots — a recovery is a multi-site code-ship.
Recommend an env-direct kill-switch
`ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE` (default ON; `=0`
disables ONLY arm 3 of the 2-arm disjunction in
`TaskRepository.clear_all` and `MessageQueueRepository.clear_all`,
leaving arm 1 (`status IN ('running','paused')`) intact). When
OFF, the wipe predicate is the exact pre-fix predicate
(arm 1 only). Read per boot at the wipe call, mirroring
`auto_continue_boot_pass.py:145-156`. When OFF, the boot log
carries a single `WARN` line naming the disabled-arm path. The
F-1 bus-side fix (§1) remains active; only the arm-3 preserve
is gated.

**Pattern (mirrors §4):**

* Module-level constant
  `BOOT_AUTO_CONTINUED_PRESERVE_KILL_SWITCH_ENV =
  "ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE"`.
* `_boot_auto_continued_preserve_enabled() -> bool` =
  `os.environ.get(ENV, "1") != "0"`. Default ON.
* Read at the wipe call site (NOT cached) so an operator flip
  takes effect on the next daemon restart.
* The kill-switch is consulted only in the predicate's arm 3
  condition; arm 1 is never gated.

**Rename history.** The prior cycle's env var
`ENSEMBLE_BOOT_EPOCH_PRESERVE_HEARTBEAT` (which governed
arm 2, now removed) is RETIRED. The new name
`ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE` is semantically
accurate. The rename is announced in the post-merge-gate file.

**Settling citation.** §13c (kill-switch rescoping rationale);
the auto-continue kill-switch convention at
`auto_continue_boot_pass.py:145-156, :245-248`; the bus
orphan-sweep at `dependency_bus.py:1896-2029` (which does not
clean preserved rows).

---

## §3 — F-2 sweep design (b): in-scope

> **⚠ REVISION CYCLE 2 SUPERSEDED.** The planned
> `daemon/services/wc_wedge_sweep.py` module and the entire
> design (b) sweep are **ELIMINATED** by the W-4 pivot. The F-2
> architecture is the RDRS lane-2 extension (see §12). This
> section is preserved for historical record ONLY — every task,
> every kill-switch, every test in this section is RETIRED.
> Do NOT implement anything in this section. See §12 for the
> active F-2 design.

**Decision (HISTORICAL — SUPERSEDED).** Implement design (b) — the boot-time WC wedge sweep
as the F-2 primary fix. The sweep module is a new
`daemon/services/wc_wedge_sweep.py` (proposed name; final name
chosen at implementation time to match existing service naming
convention). The sweep is registered as a single `await
wc_wedge_sweep.run(manager, boot_epoch=...)` call placed in
`api.py` between `:1564` and `:1571`, inside the same envelope-try
(except `:1565-1570`), before `upgrade_journal_sweep.start()`.

Sweep contract (one pass, idempotent across re-runs):

1. **Read-only scan of `instance.status='waiting_children'`.** The
   list is captured in a single read at sweep entry; subsequent
   operations work from the captured list (no re-scan mid-sweep).
2. **For each parent,** iterate its children via
   `instances.parent_id` (read-only), filtered to:
   * child is in a terminal state (`COMPLETED` / `ERROR` / `FAILED`)
     OR has a preserved wake row (sweep re-check via the new
     `discover_pending_wakes(child_id)` helper — a small
     repository-layer read that returns the count of
     `Task(task_type=PROCESS_REPORT, instance_id=parent_id,
     message_id=<...>, status='pending')` rows for that child.
     **⚠ This helper does NOT exist on the worktree (architect
     cycle T-2, grep-verified).** Phase 2 task 2.4 MUST add it
     — a small read-only method on `TaskRepository`, parallel to
     `find_stale_running_tasks` at `repository.py:3329-3395`.
     Both this filter and the post-lock re-check at step 7
     depend on this helper existing.), AND
   * the child is NOT in `RUNNING` / `PAUSED` (boot-pass-resumed
     children are RUNNING → naturally excluded by the read; the
     sweep is not the place to handle them).
3. **For each child that matches,** the sweep re-invokes the
   **existing idempotent notify-parent service** —
   `ChildReportsService._process_child_completion`
   (`child_reports.py:2560-2594`, lock at `:2563`, content at
   `:2573`) — with the child's surviving checkpoint's last
   assistant content (read via
   `completion_content.get_last_assistant_message`).
4. **Serialize under `bus._get_parent_lock`** per the existing
   notify-parent service contract; the sweep REUSES the same lock
   helper (`:2560-2574`) — it does NOT take a fresh lock of its
   own.
5. **Parent-history ledger check** (the "already reported" skip):
   before invoking the service for a child, the sweep reads the
   parent's checkpoint history via `get_instance_messages` (which
   surfaces `additional_kwargs.source` per the §3 verification
   below) and scans for any message with
   `source.startswith("internal_report:<child_iid>")`. If found,
   the child is skipped (the ledger shows the report was already
   delivered to this parent checkpoint, so re-synthesizing would
   produce a double-report). **Helper home + name (per N-10):**
   this check is implemented in the new
   `daemon/services/report_delivery_ledger.py` module as
   `parent_history_has_internal_report(checkpointer, parent_id,
   child_id, manager=None) -> bool`. The prior cycle's
   `get_for_thread_message` name is RETIRED (it collided with
   `MessageMetadataRepository.get_for_thread` at
   `daemon/repositories/message_metadata/repository.py:123`).
6. **`completed_message_id` derivation (CORRECTION).** The sweep
   derives `completed_message_id` from the surviving child
   checkpoint's last assistant message metadata via
   `MessageMetadataRepository.get_for_thread(thread_id)` (returns
   `{message_id: (created_at, seq)}` per
   `daemon/repositories/message_metadata/repository.py:123`),
   then post-filters to find the `message_id` matching the last
   assistant turn. **The `get_for_message(thread_id, message_id)`
   method cited in the prior version of `phase2-plan.md` task 2.8
   does not exist on the current repository surface** — use
   `get_for_thread` and post-filter, OR (Phase 2 implementer's
   choice) add a new `get_for_message` method to the repository
   in Phase 2.

   The source-diff dedup at `child_reports.py:3498-3510` keys on
   `(child_id, completed_message_id)` exact equality — so a
   sweep that derives the same `completed_message_id` on every
   re-run will hit the dedup naturally on subsequent passes.

   If metadata is unavailable, the sweep logs a WARN and skips
   the child (preserves idempotency over dedup-by-status).
   **No content-hash fallback is implemented** — content-keyed
   idempotency does not exist in the codebase
   (`research-findings.md §2`, grep-verified); introducing it
   would expand the scope beyond the commission's evidence.

   **⚠ Load-bearing precondition for the implementing developer
   (architect cycle R-A, D1):** the `message_metadata` side
   table's `message_id` column is populated for LangChain
   `BaseMessage.id` (UUID4 — `daemon/repositories/message_metadata/models.py:53`),
   NOT for `MessageQueue.message_id` (the queue-side id that
   `completed_message_id` actually carries at
   `child_reports.py:3498-3510`). The tap points at
   `instance_messaging.py:1390-1407` and `:4364-4366` are entry-
   path / graph-input (HUMAN messages) — NOT assistant turns.
   If the implementing developer finds the metadata side table
   does not surface the queue-side id for the last assistant
   turn, the implementer must choose ONE of: (i) add a new
   repository method that maps from checkpoint content to
   `MessageQueue.message_id`; (ii) re-derive
   `completed_message_id` from a different source (e.g., the
   child's last assistant content hash, or the wake-row's
   queue id which the surviving message-queue side may still
   carry); or (iii) accept the sweep uses a different dedup
   key (just `child_id` alone) and update the source-diff
   dedup in `child_reports.py:3501` to match. The sweep's
   status-level guards (terminal short-circuit + atomic
   conditional UPDATE + parent lock + root-gate fresh-
   assistant) provide re-run safety even if the dedup key
   is loosened. **See `amendment-summary.md` D1 entry.**
7. **Re-check pending wakes AND ledger after lock acquisition
   (architect A-2 — extended re-check):** inside the parent
   lock, the sweep re-issues BOTH:
   * `discover_pending_wakes(child_id)` for wake rows (the
     `discover_pending_wakes` helper is added in Phase 2 task
     2.4 — see step 2 ⚠ note), AND
   * the parent-history ledger scan (`get_instance_messages`
     on the parent → `source.startswith("internal_report:<child_iid>")`)
     for newly-injected reports (cheap; pre-fetched at step 5
     or one additional DB read inside the lock).
   Either finding triggers the skip: a wake row means the
   normal path will deliver; a parent-history source-match
   means a report was injected between sweep entry and lock
   acquisition (the source-diff dedup at
   `child_reports.py:3498-3510` would also catch the
   double-injection, but the ledger check short-circuits the
   service invocation). The previous prose checked wake rows
   only; the extension closes the W-C race window where a
   normal delivery completes between the sweep's pre-lock
   scan and its post-lock invocation.

**Rationale.** The idempotency guard stack (a) source-diff dedup,
(b) terminal short-circuit + atomic conditional UPDATE, (c)
root-gate fresh-assistant (the only post-injection safety is
that the parent gate must re-evaluate), and (d) the parent lock
— together — provide the necessary re-run safety. Design (b) is
**strictly narrower** than design (a) (it does not change the
preserve-predicate contract for either clear_all) and reuses
the exact same notify-parent service that the original
child-completion path uses, so the injection shape is
identical to a normal delivery. The synthesis item 4 caveat
(source-surfacing in `get_instance_messages`) is RESOLVED
post-dispatch (see `research-findings.md §3` and the
verification note in the worktree plan).

**Alternatives considered.**

* **Design (a) — extend BOTH clear_all predicates to preserve
  PENDING `process_report` tasks + backing messages with
  `source LIKE 'internal_report:%'`.** **DEFERRED** (see §3a
  below for the explicit in-scope/deferred recommendation). The
  design preserves the wake rows across restarts, which is
  attractive (no synthesis), but introduces a claim-side gap
  (PAUSED / TERMINATED parents never claim the preserved wake;
  full PROCESS_REPORT claim → task_processor → revive path
  NOT line-walked in this commission). Design (b) is a
  strictly safer primary because it reuses the same delivery
  shape and does not require the claim-side verification.
* **Always re-invoke the service for every terminal child of a
  waiting_children parent (no ledger check).** **REJECTED.**
  This would re-synthesize for already-reported children and
  produce double-reports visible to the user. The ledger check
  is the minimum necessary de-duplication.
* **Synthesize the report by writing directly to the message
  queue / task table (bypass `ChildReportsService`).** **REJECTED.**
  Bypassing the service loses the source-diff dedup, the
  terminal short-circuit, the parent lock, and the post-commit
  side-effects. The whole point of reusing the service is
  picking up the existing guard stack for free.
* **Move the sweep to a periodic background task instead of
  boot-time.** **REJECTED.** The wedge is a one-shot
  straddle-residue; a periodic task would either fire too
  often (wasting work) or too late (the parent is wedged for
  the periodic interval). Boot-time is the correct seam.

**Settling citation.** Dispatcher synthesis items 3, 4; Explorer B
boot-sequence walk; idempotency-guard inventory in `research-findings.md
§2`; the FIFO-drain convention at `daemon/graph.py:8479` and stamp
site `:8518-8534`.

### §3a — Design (a) explicit recommendation: DEFERRED

**Decision.** Design (a) is **DEFERRED** for this release. The
follow-up to consider it is the next durability/auto-continue
commission after this one, scoped to verify the
PROCESS_REPORT claim → task_processor → message-processing → revive
path end-to-end and resolve the PAUSED-parent silent-row case.

**Rationale (in-scope vs. deferred).**

* **In-scope arguments (for design (a)):** preserves the wake
  rows across restarts (no synthesis needed); narrower blast
  radius (a SQL predicate change, no new service); zero new
  code in the boot envelope.
* **Deferred arguments (against design (a) for this release):**
  (i) the claim-side PAUSED/TERMINATED silent-row case is a
  known gap (Explorer B §2 design (a) facts); (ii) the full
  PROCESS_REPORT claim → task_processor → revive path was NOT
  line-walked in this commission, so we cannot bound the
  behaviour; (iii) the change to BOTH clear_all predicates is
  hot-path (every daemon restart exercises it), and a wrong
  predicate could over-preserve queue entries that the bus
  orphan-sweep then leaves dangling.
* **Risk-of-not-doing-it (acceptable for this release):** the
  design (b) sweep covers the same wedge, so the F-2 user-
  visible behaviour is healed by the primary; design (a) is a
  *belt-and-suspenders* that the primary does not require.
  Recording the deferred recommendation in this plan and
  `decisions.md` is sufficient to make the follow-up trackable.

**Follow-up verification task (recorded here for the next
commission):** line-walk the PROCESS_REPORT claim path from
`claim_pending_task` (`repository.py:2148-2243`) through
`task_processor` to the message-processing delivery and the
revival logic at `instance_messaging.py:1486-1510`, with explicit
test cases for PAUSED, TERMINATED, COMPLETED, and FAILED parent
states. The unknown does NOT block this release.

---

## §4 — Sweep kill-switch

> **⚠ REVISION CYCLE 2 SUPERSEDED.** The sweep module is
> ELIMINATED by the W-4 pivot (see §12). The
> `ENSEMBLE_WC_WEDGE_SWEEP_ON_BOOT` env var is RETIRED. RDRS
> lane 2 has its own per-lane kill switches in
> `daemon/config.py:1302-1378` — see §12a for the active
> F-2 design. This section is preserved for historical record
> ONLY. Do NOT implement anything in this section.

**Decision (HISTORICAL — SUPERSEDED).** The sweep gets its OWN env-direct kill-switch,
following the exact pattern documented in
`research-findings.md §2` (kill-switch convention):

* Module constant `WC_WEDGE_SWEEP_KILL_SWITCH_ENV =
  "ENSEMBLE_WC_WEDGE_SWEEP_ON_BOOT"` (name to be finalized at
  implementation; this plan author's proposal).
* `_wc_wedge_sweep_enabled() -> bool` =
  `os.environ.get(ENV, "1") != "0"`. Default ON.
* Read per boot at sweep entry, NOT cached. Reads from the
  `os.environ` at the call site so an operator flip takes effect
  on the next daemon restart.
* The sweep entry logs `skipped_kill_switch=1` in its result
  accounting when disabled (mirroring the
  `ContinueResult.skipped_kill_switch=1` accounting at
  `auto_continue_boot_pass.py:245-248`).

**Rationale.** Per the commission's binding constraint: the
sweep MUST NOT piggyback on
`ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART`. The two features are
independent in semantics (the auto-continue feature continues
RUNNING tasks; the sweep heals a straddle wedge). An operator
who wants one OFF and the other ON must be able to do that, and
a single env var would not let them.

**Alternatives considered.**

* **A single `ENSEMBLE_DURABILITY_FEATURES_ON_BOOT=0` umbrella
  var covering both the auto-continue feature and the sweep.**
  **REJECTED.** Operators who want to disable one without the
  other lose that ability. The two features are independent
  enough that they should fail and heal independently.
* **No kill-switch at all.** **REJECTED.** The auto-continue
  precedent (`auto_continue_boot_pass.py:145-156`) is the
  reason the feature has a kill-switch — operators needed the
  opt-out during the initial rollout to disable the feature
  without code changes. The sweep needs the same affordance.
* **A typed-config field in `config.py` plus an env override.**
  **REJECTED.** The auto-continue feature deliberately uses
  env-direct (no `config.py` field) per its ADR-044
  commentary; mirroring that decision keeps the convention
  uniform.

**Settling citation.** Dispatcher synthesis item 6;
`auto_continue_boot_pass.py:141-156, 245-248`;
`upgrade_journal.py:886` (the `ENSEMBLE_POST_RESTART_ARM_NOTIFY`
precedent).

---

## §5 — Boot-order handling: ownership contract

**Decision.** Document the boot sequence ownership as a single
block in `daemon/services/instance_lifecycle.py` (beside the
existing Pause-First Then Quiesce convention), and add inline
comments at the two key boundary points:
`daemon/services/dependency_bus.py:671` and
`daemon/repositories/task/repository.py:4380` (the
`task.clear_all` preserve DELETE).

The documented boot order, **one owner per step**, is:

1. `InstanceManager` constructor (`api.py:393-398`,
   `manager.py:771-873`) — OWNER: `manager.py`. Runs the
   `discard_on_startup` wipe (with the §2 epoch-capture fix);
   no other subsystem may run here.
2. `manager.initialize()` (`api.py:399`) — OWNER: `manager.py`.
   Engine, repos, pool, etc.
3. `capture_boot_epoch(manager.engine)` (`api.py:412`) — OWNER:
   `boot_epoch.py`. Best-effort; idempotent.
4. Critical-notes boot-state probe (`api.py:422`) — OWNER:
   `config.py`.
5. Execution-gate stale-lease recovery (`api.py:431-436`) —
   OWNER: gating service.
6. `manager.setup_worker_pool()` (`api.py:439`) — OWNER: STR.
   `PoolOrchestrator.recover_on_startup` + periodic `start`.
7. `init_dependency_bus(app, manager)` (`api.py:1306`) — OWNER:
   `dependency_bus.py`. Includes bus-internal order (warm cache
   → `_recover_fired_unsent` → `_sweep_orphan_watchers`); the
   F-1 §1 truthy-error gates are inside this owner and are
   exercised by every daemon that reaches the bus.
8. Finalization-only recovery loop (`api.py:2459+`) — OWNER:
   `dependency_bus.py`. Per-target finalize-or-defer; never
   re-drives wake delivery.
9. `UpgradeJournalSweepService` boot reconcile + wake sweep
   (`api.py:1498-1521`) — OWNER: `upgrade_journal_sweep.py`.
10. **Auto-continue boot pass** (`api.py:1547-1564`) — OWNER:
    `auto_continue_boot_pass.py`. Selection excludes
    WAITING_CHILDREN.
11. **RDRS lane-2 boot pass (already wired, no new boot step
    under W-4 pivot)** — OWNER: `report_delivery_recovery.py`.
    Per W-4, the `wc_wedge_sweep.py` module is ELIMINATED
    (see §12c — the module was never created). The
    compensating F-2 backstop is RDRS's
    `recover_on_startup` at
    `pool_orchestrator.py:407-412` (fire-and-forget
    off-loop, post-wiring, post-wipe; reads post-wipe state
    immediately post-boot) + the periodic 300s loop.
    The lane-2 query extension at
    `report_injection/repository.py:1039-1253` (per §12a,
    B2) is the load-bearing change. No new boot wiring is
    needed in this commission; RDRS already runs on boot.
    See `decisions.md §12, §12a` for the W-4 pivot
    justification. The prior cycle's step 11 ("NEW — WC
    wedge sweep") is RETIRED.
12. `upgrade_journal_sweep.start()` (`api.py:1571`) +
    `manager.set_upgrade_journal_sweep` (`:1573`) — OWNER:
    `upgrade_journal_sweep.py`.
13. `ServiceReconciliationService` await (`api.py:1615-1684`) —
    OWNER: `service_reconciliation.py`.

The `instance_lifecycle.py` block is a numbered list mapping
1:1 to the steps above, with each step citing the boot-time
file/line and naming the owner module. The two inline comments
at `dependency_bus.py:671` and `repository.py:4380` are short
(≤5 lines each) and reference the `instance_lifecycle.py`
block by name, so a reader at the gate can navigate to the
ownership contract in two hops.

**Rationale.** The current boot sequence is straight-line
(`lifespan()` at `api.py:204`, no hook registry per Explorer
B), so "owner" is the only contract that can prevent one
step from stepping on another's invariants. Documenting the
contract in `instance_lifecycle.py` (alongside the existing
Pause-First Then Quiesce convention) keeps the discovery cost
low for future boot-sequence contributors. The inline
comments are deliberately short so they do not drift; the
authoritative source is the block.

**Alternatives considered.**

* **A hook-registry-based boot order** (a list of `BootHook`
  callables, ordered, with explicit predecessors). **REJECTED.**
  The current straight-line is a load-bearing choice (no
  cycles, no precedence-resolution cost); introducing a
  registry would expand the boot architecture for the sake
  of two new steps. The ownership-contract block is the
  minimal change.
* **A doc-only change with no inline comments.** **REJECTED.**
  The F-1 wedge is specifically a "two-step" coupling (the
  bus gate and the wipe); without inline reminders at the
  boundary lines, a future contributor to either file would
  not know the other file is its peer in the contract.

**Settling citation.** Dispatcher synthesis item (ownership
contract); the Pause-First Then Quiesce convention block in
`instance_lifecycle.py` (the natural home for this
documentation); the existing inline-comment precedent at
`repository.py:4337-4364` (the JOURNAL mirror block, which
already carries a similar inline rationale).

---

## §6 — Reconciliation verdict: F-1 producer-contract decision

**Decision.** **Do NOT** tighten the producer contracts at
`child_reports.py:420, :653` (`_emit_terminal_via_bus` /
`_emit_terminal_for_child_instance_via_bus`) to require explicit
error text when `status == "error"`. Instead, the producer side
is left unchanged and the F-1 wedge is closed by the §1
bus-side truthy-error gate + the §1a defensive log.

**Rationale.** Producer contracts are intentionally None-tolerant
for the terminated branch at `instance_lifecycle.py:256`; the
None case is legitimate in the "child terminated without an
error message" path. Tightening the producer would either (a)
require auditing every call site to confirm it always carries
explicit text, or (b) break the terminated branch. The bus-side
gate is the narrower fix that does not touch producer semantics
or the terminated-branch semantics.

The defensive log (§1a) makes any future regression that reaches
the bus gates with `status==error and error is None` visible
in operator logs. If the regression recurs, the next
commission can address the producer side with a focused
remediation informed by the log evidence.

**Alternatives considered.** All considered in §1; this section
records the **explicit no-action** decision on the producer
side so the next commission does not re-litigate it.

**Settling citation.** Explorer A Q6 (surprise scan — no other
boot-time error=None → terminal-error site; the
`instance_lifecycle.py:256` terminated branch is the legitimate
None case); §1 rationale; the observer's own fallback at
`job_feedback_observer.py:122`.

---

## §7 — Re-anchor notice (binding)

**Decision.** All anchors used in this plan are as-verified on
the worktree `/home/nea/ensemble-src-wt-durability` @ `18827dbd`,
branch `feature/durability-f1-f2`. Implementation occurs in
this worktree. The main workdir `/home/nea/ensemble-src` is
occupied by a sibling commission and is read-only to this
commission.

**Discrepancies vs. older docs** (re-verified on this worktree;
see `research-findings.md §6`):

* `_assistant_message_fresh` def line: `~:1822` (older docs) →
  `:1801-1872` with def line `:1801`.
* Wake Task row mint: `~:3835` (older docs) → `:3862-3872`.
* `task.clear_all` end: `~:4450` (older docs) → `:4411`.
* `_recover_fired_unsent` DEF: `~:1840` (older docs) → `:1839`;
  call site `:1541`.
* `get_instance_messages` source-surfacing: "UNVERIFIED"
  (dispatch synthesis) → `daemon/utils.py:264-266` — YES,
  truthy, **verified by this plan author**.

Implementation must spot-check any line number that did not
appear in the worktree-anchored research; the discrepancies
table above is the authoritative correction list.

**Settling citation.** This plan; `research-findings.md §6`;
the dispatcher's binding "Re-anchor note" in the commission
brief.

---

## §8 — Backward-compat: keep-green test list (binding)

**Decision.** The following four unit-test files and two packs
must remain green throughout the implementation:

* `tests/unit/services/test_auto_continue_boot_pass.py`
* `test_auto_continue_interleaving.py` (path under `tests/` —
  exact path verified at implementation)
* `test_auto_continue_terminalizer.py`
* `tests/unit/repositories/test_auto_continue_candidates.py`
* `test/packs/auto_continue_boot_pass_unit_test.sh`
* `test/packs/auto_continue_interleaving_unit_test.sh`

The implementation run sequence in each phase includes the
corresponding pack as a regression gate. The packs follow the
transparent-wrapper convention (`set -u`, `cd` to repo root,
inner `timeout 110s .venv/bin/pytest <explicit file list>
--tb=short -q`, exit-code mapping `0=PASS / 1=FAIL /
124=TIMEOUT`, trailing `RESULT:` line). No `PACKS.md` exists
under `test/` in this worktree — packs are discovered by
directory listing.

**Rationale.** Per the commission's binding constraint "auto-
continue feature's existing tests/packs stay green" and per
the keep-green inventory in `research-findings.md §2`.

**Alternatives considered.** A `PACKS.md` introduction for
discoverability — **DEFERRED** to a follow-up; this
commission's packs are added with the same convention and
discovered by directory listing for this release.

**Settling citation.** Dispatcher synthesis item 7; the
auto_continue_boot_pass pack file (verified anchor).

---

## §9 — Demo E2E gating + evidence bar (REVISION CYCLE 2, ITERATION-002 REMEDIATION)

**Decision.**

* **F-2 demo E2E is UNCONDITIONAL for first landing per
  C-2** (leader call). The trigger is not "unit evidence
  leaves W-A / W-B / W-C unproven" — the trigger is "F-2
  ships". The demo runs on every F-2 landing; the evidence
  lands durably in
  `.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/`
  (logs/ + findings.md + recipe-pointer.md). The
  `wc_wedge_sweep.py` ELIMINATED context is retained in the
  evidence-bar wording (the F-2 architecture is the RDRS
  lane-2 extension per the W-4 pivot, see §12).

* **F-1 demo E2E is OPTIONAL per tester judgment.** The
  skip condition (when the F-1 demo can be skipped) is
  now satisfiable via the **Issue-2 real two-boot test**:
  a real file-backed-SQLite two-boot test using the
  existing F9-parity harness
  `tests/unit/repositories/test_task_auto_continued_lifecycle.py`.
  The two-boot test (a) exercises real `clear_all` SQL on
  both repositories (including the arm-3 `EXISTS
  instances` join + kill-switch gating) on a real DB and
  (b) exercises the double-restart probe (candidates==1,
  already_resuming==0, no double-continue, boot-pass CAS
  stamp actually invoked across TWO manager constructions).
  When the two-boot test passes deterministically, the F-1
  demo CAN be skipped (the Issue-2 test satisfies the C-2
  evidence bar for the F-1 wipe seam). The F-1 demo is
  exercised only if the developer/tester judges it needed.

**Evidence bar counts (REVISION CYCLE 2, ITERATION-002).**

* **F-1:** **seven** (7) unit tests — S1 through S7 (S1:
  double-restart no-double-continue; S2: None-error
  does not flip `_parent_errored`; S3: real-error
  still flips; S4: terminal + `auto_continued_at`
  survives `clear_all`; S5: terminal-no-marker is
  deleted; S6: boot-sequence candidates==1; S7: F-1
  kill-switch test for
  `ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE`). PLUS the
  Issue-1 real-SQL coverage on the queue-side 2-arm
  disjunction (task 1.9). PLUS the Issue-2 real two-boot
  test. The unit tests + the Issue-1 + the Issue-2
  constitute the F-1 evidence bar.

* **F-2:** **seven** (7) unit tests + **two** (2) real-PG
  integration tests = **9 total**. The 7 unit tests:
  S21 (anchor-less child admission), S22
  (`find_wake_already_delivered_evidence` + the
  `parent_history_has_internal_report` helper), S23
  (RUNNING exclusion), S25 (run-twice idempotency), S26
  (kill-switch gate), S28 (live-delivery race — renamed
  from prior-cycle S15 per C-2), plus the
  `child_message_id` derivation assertion (the
  W-1 stable-but-different verification). The 2
  integration tests: **S24** (multi-child mixed, on
  real PG seam per C-2) AND **S27** (no-duplicate-
  execution regression, on real PG seam per C-2). **S27
  is NON-DROPPABLE from the gate** — dropping S27 would
  leave the no-duplicate-execution invariant unverified
  on the real seam, and the issue-7 carry-forward
  invariant (issue-7 = phase-2 inventory contradiction)
  requires the integration test count to be **2** not 1.

* **Total corpus:** 7 F-1 unit + 7 F-2 unit + 2 F-2
  integration = **16 tests** gating the F-1/F-2 commission,
  plus the UNCONDITIONAL F-2 demo E2E for real-world
  evidence. The keep-green list (per §8) is the
  additional cross-feature regression gate.

**Rationale.** The prior cycle's "six F-1 unit / six F-2
unit" wording was stale (predated the F-1 kill-switch S7 +
the F-2 W-4 pivot). The current cycle's "8 unit + 1
integration" was also stale (S24 AND S27 are BOTH real-PG
integration, not unit). The C-2 principle (real-DB
integration is the window-proof for mock-only F-2 tests)
requires S24 AND S27 to be on the real PG seam. **S27 is
NON-DROPPABLE** because the no-duplicate-execution
regression is the load-bearing correctness invariant the
RDRS chain provides; dropping it would mean the gate
claims green on unit tests alone, which C-2 explicitly
rejects.

**Alternatives considered.** "6 F-1 + 6 F-2 unit" — **REJECTED**
as outdated. "8 F-2 unit + 1 integration" — **REJECTED**
as understating the integration test count (S27 missing).
"7 F-1 unit + 7 F-2 unit + 2 integration" (this decision) —
**ADOPTED**.

**Settling citation.** The Issue-2 two-boot test (F9-parity
harness `tests/unit/repositories/test_task_auto_continued_lifecycle.py`
on the branch, dispatcher-verified); the Issue-7 inventory
correction (S24 AND S27 are both real-PG integration per
C-2); the C-2 evidence-bar principle at
`plan-overview.md:131-133`; the UNCONDITIONAL F-2 demo per
C-2 leader call.

---

## §11 — Deferred follow-ups (architect cycle)

### §11a — Service-internal dedup status-set widening (D5)

**Decision.** DEFERRED. The service-internal dedup at
`child_reports.py:3501-3506` filters `MessageStatus.READY /
PROCESSING / COMPLETED` only — does NOT include `PENDING`. A
preserved wake in `PENDING` status would NOT be caught by the
service-internal dedup. Mitigated by the sweep's extended
post-lock re-check at §3 step 7 (architect A-2 — re-checks
BOTH wake rows AND parent-history ledger scan). The current
behaviour is acceptable: the sweep's status-level guards
(terminal short-circuit + atomic conditional UPDATE + parent
lock + root-gate fresh-assistant) provide re-run safety even
if the dedup set is widened later.

**Follow-up for the next commission:** if design (a) is
later in-scope, widen the status-set at `child_reports.py:3502`
to include `MessageStatus.PENDING.value` so preserved-wake
synthesizes are caught by the service-internal dedup before
they reach the sweep. The widening is a one-line change; the
wider impact (which other callers depend on the
`PENDING`-excluded set?) needs a line-walk.

**Settling citation.** Architect cycle D5; the service-internal
dedup at `child_reports.py:3501-3506`; the extended post-lock
re-check at §3 step 7.

### §11b — `PENDING MessageQueue` status dedup coverage (D4)

**Decision.** DEFERRED. Architect A-3 / D4 — widen the
service-internal dedup status-set to include `PENDING.value`
in a Phase 2 follow-up. The sweep's post-lock re-check at
§3 step 7 is sufficient for this release; no plan change
needed.

**Settling citation.** Architect cycle D4; same as §11a.

### §11c — Lane-2 stuck-counter observability for the 300s degraded-retry loop (ITERATION-002 carried)

**Decision.** RECORD (not implement). The F-2 lane-2 per-row
pass (Phase 2 task 2.5, per `phase2-plan.md`) increments a
result counter on failure (per the `WcWedgeSweepResult`-
equivalent accounting in the F-2 inventory, see `decisions.md
§12a` + the Phase 2 Exit Criterion enumeration). On a
**permanently-stuck candidate** (e.g. a child whose checkpoint
content is permanently empty and whose PREFIX-ledger check
fails on every re-run), the per-row pass logs `WARNING` and
`continue`s; the periodic 300s RDRS loop re-evaluates and
the failure counter increments WITHOUT bound. An operator
inspecting the failure counter would see a steadily
increasing value with no resolution path. This is a
**bounded-by-time, not bounded-by-success** observability
gap.

**Follow-up for the next commission:** add a
**stuck-counter** to the RDRS per-row pass that detects
"this candidate has been failed N times in a row" and
escalates to a structured log line at WARNING (or to a
metric at `/metrics` — see A-5 in the architect cycle). The
stuck-counter is gated on the same env-direct kill-switch
pattern as the F-1 + F-2 kill-switches; the
`/metrics` exposure is the architect's A-5 P-I (DEFERRED
per D3 in the prior cycle). This follow-up is **RECORDED
NOT IMPLEMENTED** per the ITERATION-002 carried items
directive.

**Settling citation.** ITERATION-002 carried item (v);
the dispatch contract: "RECORD (do NOT implement) the
stuck-counter-for-300s-degraded-retry-loop as a follow-up
note in `decisions.md §11` Deferred follow-ups (one entry:
the lane-2 per-row pass's failure counters could mask a
permanently-stuck candidate behind the 300s retry loop;
follow-up = stuck-counter observability)."

---

## §12 — F-2 architecture PIVOT: RDRS lane-2 extension (W-4, REVISION CYCLE 2)

### §12a — Pivot decision

**Decision.** **ELIMINATE the planned `daemon/services/wc_wedge_sweep.py` module entirely.** Extend RDRS lane 2 (the no-row backstop) at `daemon/repositories/report_injection/repository.py:1039-1253` instead. The previously-planned kill-switch `ENSEMBLE_WC_WEDGE_SWEEP_ON_BOOT` is RETIRED (module eliminated). The W-3 ↔ W-4 review verdict and the Explorers B+C evidence CONVERGE on this pivot (see `research-findings.md` Revision Cycle 2 section).

**Rationale.** The F-2 wedge is a no-row straddle: parent in `waiting_children`, child completed, MessageQueue wake rows deleted by the boot wipe. Three independently-verified observations drive the pivot:

* **(B1 — starvation hypothesis confirmed)** the wipe deletes the anchor (child's `message_queue` COMPLETED row) before RDRS lane 2's anchor-requirement filter at `repository.py:1241` evaluates; the wedge child is structurally filtered out regardless of any other predicate. Anchor blindness is the failure mode, not the exclusions.
* **(B2 — instance-anchored, wipe-immune)** RDRS lane 2 is already `FROM instances c JOIN instances p ON p.instance_id=c.parent_id` (`:1216-1220`) — the only anchor that survives the wipe is the **instance row** itself. The query is ~10-30 lines of change to admit anchor-less completed children (the wedge children) by deriving the child_message_id from the surviving child checkpoint in Python (`completion_content.get_last_assistant_message` + `serialize_message` chain — see §14a W-1 final choice).
* **(B3 — same machinery, maximum reuse)** RDRS already drives the full chain `ensure_deferred → transition_deferred_to_pending → _reconcile_deferred_report → _create_subshape_a_artifacts → [TRANSITIVE] _process_child_completion_and_notify_parent` (Explorer C Q1 line-walk). The transitive 5th hop is **SANCTIONED** through the RDRS chain (not a direct call): the artifacts (MessageQueue READY + PROCESS_REPORT PENDING) are minted in one txn at `manager.py:9178-9331` BEFORE re-entry, and the `:2773-2787` child-status guard returns `idempotency_skip` on re-entry (no exception, no erase, no duplicate); the parent wakes via `claim_pending_task` (PROCESS_REPORT ranked FIRST at `task/repository.py:2648-2650`; WAITING_CHILDREN exception at `:2789-2830`). A new sweep module would have to independently re-derive this chain or fabricate an injection row anyway (converges to the same code with more hazard surface).

**Window-to-lane map (binding).** All three F-2 sub-windows (W-A: terminal-write→wake-rows-commit; W-B: commit→`enqueued_at` stamp; W-C: stamp→wake-task claim) converge to the same post-wipe shape: PENDING wake rows are deleted regardless of stamp state. RDRS lane 2 (no-row backstop, `parent_not_terminal=True`) covers all three. Lanes 1/3/4 own the marker-minted cases (pre-restart with `report_injection` rows). Lane 5 owns the orphan-deferred-of-terminal-parents revival. Mixed cases where a marker WAS minted pre-restart are already handled by lanes 1/3/4 (+ sub-shape (c) carrier revival at `manager.py:8971-9017`).

### §12b — Comparison table (W-4 review)

| Option | Coverage | Code reuse | Hazards | Verdict |
|---|---|---|---|---|
| **(A) Re-anchored RDRS lane 2** | All 3 windows (post-wipe state is window-invariant) | Maximum: ~10-30 modified lines in the ONE query + tests; existing reconcile profile (write_guard, parent-lock, 8s bridge) | Anchor-keyed idempotency must be reasoned about (ca14e233/c3ac30f7 incident) — addressed by the PREFIX ledger check (see §14a) | **ADOPTED** |
| **(B) New sweep module → deferred-marker path** | All 3 windows IF instance-anchored | Must independently re-derive the zero-evidence test; new module + wiring + tests; same hazard profile as (A) | Higher blast radius (new module, new wiring, new tests) | REJECTED — converges to (A) with more surface |
| **(C) Direct row mints (no deferred-marker)** | All 3 windows | Cannot reuse `_create_subshape_a_artifacts` without fabricating an injection row | Bypasses write-guard + write-once triple gate; hand-rolls dead-parent guards; largest hazard surface | REJECTED — converges to (B) with more hazard |
| (D1) RDRS + anchor patch only | All 3 windows | Same as (A) | Same as (A) — the smallest viable variant | (Same as A; D1 = D for the rubric) |
| (D2) Watcher-row-driven scan | All 3 windows | Moderate | Wipe-immune rows but the scan is novel | DEFERRED (not evaluated as primary) |
| (D3) Lane inside JobRecoveryService boot | All 3 windows | Reuses `job_recovery_service.py:1790/:2143` | Different lane ownership, different terminalization contract | DEFERRED (not evaluated as primary) |

### §12c — Rejected items (frozen)

The following items remain REJECTED from the prior cycle and are explicitly NOT re-evaluated in this cycle:

* **Amending the `child_reports.py:2773` guard** (the "no terminal instance" check inside the public `_process_child_completion`): the guard is correct for the natural completion path; the wedge is a no-row straddle, not a guard-amendment opportunity.
* **Re-running natural completion** post-wipe: the natural path's terminal-write is what produced the wedge row; re-running is not a fix.
* **The previously-planned `wc_wedge_sweep.py` module** and its `ENSEMBLE_WC_WEDGE_SWEEP_ON_BOOT` kill-switch: ELIMINATED. The module's spec, the kill-switch convention, the WcWedgeSweepResult dataclass, the per-parent lock, the parent-history ledger, and the post-lock re-check are ALL retired. Any code or docs referencing the eliminated module are superseded by this section.

### §12d — W-2 guardrails (do-not-call section)

The following seams are documented as **NEVER** direct-call entry points from any F-2 recovery lane. The RDRS chain is the only sanctioned path:

* **DO NOT** make a **DIRECT** call into `ChildReportsService._process_child_completion_and_notify_parent(self, instance_id, completed_message_id)` (real signature at `child_reports.py:2458`) — takes the **CHILD** id first; passing the parent id silently no-ops on the root branch (`deferred_waiting_children` → SSE-only at `:2828-2854, :4481-4495`) or mints a report to the wrong grandparent for non-root parents. **The prohibition is on DIRECT calls; the transitive re-entry into `_process_child_completion_and_notify_parent` POST-materialization, through the sanctioned RDRS chain, is NOT prohibited** — the artifacts (MessageQueue READY + PROCESS_REPORT PENDING) are minted in one txn at `manager.py:9178-9331` BEFORE re-entry, and the `:2773-2787` child-status guard returns `idempotency_skip` (no exception, no erase, no duplicate). The transitive re-entry is the 5th hop of the chain listed in §12a (B3) and is the only path by which the parent-wake claim reaches the WAITING_CHILDREN exception at `task/repository.py:2789-2830`.
* **DO NOT** wrap any public `ChildReportsService` entry in `bus._get_parent_lock` — `_get_parent_lock` at `dependency_bus.py:1627-1665` constructs a **plain non-reentrant** `asyncio.Lock()` (`:1664`); same-task re-acquire blocks forever with no timeout. The public entry self-acquires the same key at `child_reports.py:2563`; wrapping it deadlocks deterministically.
* **DO NOT** call the async twin `_handle_recover_deferred_report_async` (`manager.py:8393`) from the sweep thread — it is loop-only. The sweep-side seam is the **SYNC** `_handle_recover_deferred_report` (`manager.py:8487`, "Sweep-side entry point" docstring at `:8497`, calls `_reconcile_deferred_report` at `:8546`, bridges re-entry via `run_coroutine_threadsafe(...).result(8.0)` at `:8586-8596`).
* **DO NOT** call `_dispatch_post_commit_side_effects` (`child_reports.py:4435-4440`) standalone — its input is a `_ChildCompletionDbResult` produced only by the db-sync helper; standalone caller must synthesize the object + hand-pick an outcome. Pure post-commit dispatcher; holds NO transaction context.

**Settling citation.** Explorer A W-1 CONFIRMED + W-5 CONFIRMED; Explorer B Q1-Q4; Explorer C Q1-Q4; the deferred-marker path's documented caller census at `report_delivery_recovery.py:735/:1016/:1119` + `manager.py:10780` (RESUME ROUTER revival-first precedent at `:10716-10753`).

---

## §13 — W-3: arm-3 lifecycle bound (REVISION CYCLE 2)

### §13a — Arm-2 removal

**Decision.** **DROP arm 2** (`last_heartbeat_at >= boot_epoch`) from the 3-arm disjunction in `TaskRepository.clear_all` and `MessageQueueRepository.clear_all`. The 2-arm keep-set is now: (1) `status IN ('running', 'paused')` OR (3) `auto_continued_at IS NOT NULL`.

**Rationale.** Arm 2 is vacuous at the wipe seam: the epoch is captured microseconds pre-wipe in a fresh process, so `last_heartbeat_at >= boot_epoch` is effectively `last_heartbeat_at IS NOT NULL` (every heartbeat written in this process is >= epoch). The grace-window rewrite (alternative path) is REJECTED (new tunable + over-preservation accumulation + wider blast radius). The root cause of the F-1 wedge is closed write-side (Phase 1 tasks 1.3, 1.4, 1.5 — the truthy-error gate + defensive log); arm 2 was a belt for a not-required suspenders.

**Epoch-capture task disposition.** The `capture_boot_epoch(self.engine)` call in the `InstanceManager.__init__` (Phase 1 task 1.7) is now UNNECESSARY for arm 2. However, `boot_epoch` is also consumed by: (a) the auto-continue boot pass at `api.py:1553`; (b) readiness `queue_freshness` at `:2361`; (c) `stale_task_recovery.py:766` and `:407`; (d) `repository.py:3363`. Of these, the auto-continue boot pass IS in Phase 1 scope. **Therefore the epoch-capture call is RETAINED** in the constructor (it is the predecessor of the api.py:412 call, and removing it would break the auto-continue boot pass's epoch availability during the constructor's wipe). Phase 1 task 1.7 stays. The `None` boot_epoch fallback in `decisions.md §2a` stays (degraded behavior for `boot_epoch` capture failure still applies to the auto-continue pass).

### §13b — Arm-3 lifecycle bound (composition of two options)

**Decision.** Adopt the **composition of (ii) marker-clearing at the terminalizer call site** AND **(i) instance-non-terminal co-condition on arm 3**.

* **(ii) Marker clearing.** A new dedicated repository method (e.g., `clear_task_auto_continued` at `daemon/repositories/task/repository.py`, mirroring the existing `mark_task_auto_continued` at `:1017-1096` — def line at `:1017`; the prior citation at `:988-1067` was a pre-patch anchor and is **SUPERSEDED** by the post-patch `+29` shift; see the dated correction note at the end of this §13b) is invoked after successful `complete_task` at the terminalizer call site (`manager.py:11660-11721`, gate conjunct at `:11700-11702`). The D18 r3 / D29 RATIFIED call-site-gate pattern is followed: the gate is AT THE CALL SITE; the shared `complete_task` SQL stays byte-identical (per the `manager.py:11673-11683` comment: "the r2 fold's … conjunct inside the shared SQL was REJECTED in D29"). This is the symmetric precedent for the single-writer stamp method `:1017-1096`.
* **(i) Instance-non-terminal co-condition on arm 3.** Arm 3's predicate is tightened to require an existing, non-terminal instance row: `EXISTS instances WHERE instance_id = task.instance_id AND status NOT IN TERMINAL_INSTANCE_STATUSES` (canonical `TERMINAL_INSTANCE_STATUSES` frozenset at `daemon/constants.py:584-589` = `{completed, terminated, error, failed}`). **NOTE:** `clear_all`'s SQL joins `instances` NOWHERE today (`repository.py:4386-4393`); this is the FIRST instance-join introduced.

**Rationale.** The leak is real and unbounded (Explorer A Q1): every auto-continued-then-terminalized task row survives every subsequent boot forever. No retention job touches task rows; job-queue reconciliation is status-writes only; the System Cleanup Bucket 5 zombie reaper targets NON-terminal instances only (`instance/repository.py:1751-1755`). Three options were evaluated:

| Option | Coverage | Hazard | Verdict |
|---|---|---|---|
| (i) instance-non-terminal co-condition | Stamped rows of WAITING_CHILDREN / revived / long-lived instances are wrongly preserved (leak shrinks to instance-lifetime, not boot-lifetime); stamped rows of terminal instances are correctly deleted (functionally safe — boot-pass selection is running-only at `repository.py:953` + excludes terminal/WC at `:975-981`, so a terminal stamped row is never a candidate; audit value already covered by PP1 doomed-id JOURNAL at `:4344-4364`). | Wrongly-preserved: stamped rows of WAITING_CHILDREN/revived/long-lived instances; wrongly-deleted: stamped rows of terminal instances. | **ADOPTED (compose)** |
| (ii) marker-clearing at terminalizer call site | Stamped rows of auto-continued-then-terminalized tasks are cleared at completion (primary path). Residual = STR-terminalized + race stamps. | Symmetric to the stamp writer; single-writer discipline; D18 r3/D29 ratified call-site-gate pattern. | **ADOPTED (compose)** |
| (iii) drop arm 3 entirely | Arm 3's unique coverage IS the terminal-stamped class (the leak class). Still-RUNNING stamped orphans covered by arm 1. Forensics covered by PP1 journal. | Re-wedges for the F-1 case the marker was added to prevent. | REJECTED |

**Composition rationale.** (i) alone leaves a per-instance-lifetime residual (stamped rows of WAITING_CHILDREN/revived/long-lived instances are wrongly preserved); (ii) alone leaves the STR-terminalized + race-stamps residual. The composition closes both. The combined predicate is:

```sql
-- In TaskRepository.clear_all (per the prior cycle's disjunction;
-- the column name is `instances.instance_id` — the `Instance`
-- SQLModel's primary key — not `instances.id`; see the dated
-- correction note at the end of this §13b)
status IN ('running', 'paused')
OR (
  auto_continued_at IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM instances
    WHERE instances.instance_id = task.instance_id
    AND instances.status NOT IN ('completed', 'terminated', 'error', 'failed')
  )
)
```

**Settling citation.** Explorer A Q1 (leak evidence); the single-writer stamp method at `repository.py:1017-1096` (post-patch, **SUPERSEDES** the pre-patch `:988-1067` anchor; the def line shifted by `+29` after the F-1 patch landed — see the dated correction note at the end of this §13b); the call-site-gate pattern comment at `manager.py:11673-11683`; the `TERMINAL_INSTANCE_STATUSES` canonical set at `daemon/constants.py:584-589`; the boot-pass selection exclusion at `repository.py:953/:975-981`; the PP1 doomed-id JOURNAL at `:4344-4364`.

---

**DATED CORRECTION NOTE (2026-10-04, ITERATION-002 reviewer follow-up).
The §13b composition-rationale SQL snippet above showed
`WHERE instances.id = task.instance_id` — that is WRONG.
The `Instance` SQLModel at
`daemon/repositories/instance/models.py:47-51` defines
`instance_id: str = Field(primary_key=True)` (NOT `id`);
the production SQL on the F-1 branch
(`daemon/repositories/task/repository.py:4394-4405` +
`daemon/repositories/message_queue/repository.py:1034-1045`)
uses `instances.instance_id = task.instance_id`. The
PK proof: `daemon/repositories/instance/models.py:51`
(`instance_id: str = Field(primary_key=True)`). The
reviewer-cited column `instances.id` was a plan
transcription error; the production code is correct.
The pre-patch anchor `:988-1067` for
`mark_task_auto_continued` was also SUPERSEDED by the
post-patch anchor `:1017-1096` (def line shifted by
`+29` after the F-1 patch landed — verified via
`git show 18827dbd:daemon/repositories/task/repository.py`
shows the def at `:988`, the post-F-1 file shows the def
at `:1017`; the `clear_task_auto_continued` method at
`:1098-1156` is the new mirror). This note is additive —
the original text is preserved above per the additive
principle ("If a delta supersedes an existing entry,
append a new entry that records the supersession").**

### §13c — §2c kill-switch rescoping

**Decision.** The F-1 predicate kill-switch is rescoped to govern arm 3 (`auto_continued_at IS NOT NULL`) instead of arm 2. The kill-switch name is renamed for semantic clarity: `ENSEMBLE_BOOT_EPOCH_PRESERVE_HEARTBEAT` → `ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE` (final name chosen for clarity; default ON; off = exact pre-fix wipe predicate — i.e., arms 1 only, no `auto_continued_at` arm at all). The pattern mirrors `auto_continue_boot_pass.py:145-156` (env-direct module constant + `_feature_enabled()` helper, `os.environ.get(ENV, "1") != "0"`, default ON, read at the relevant call site, no `config.py` field). The `WARN` line on the disabled path is retained.

**Alternatives considered.** Renaming the env var introduces a one-cycle break for any operator who set the old name. **CONSIDERED:** retain the old name (`ENSEMBLE_BOOT_EPOCH_PRESERVE_HEARTBEAT`) and just change what it gates. **REJECTED** for semantic clarity — the name would lie about what it does. The rename is announced in the post-merge-gate file.

**Settling citation.** The auto-continue kill-switch convention at `auto_continue_boot_pass.py:145-156, :245-248`; the W-3 directive (drop arm 2, kill-switch now governs arm 3).

---

## §14 — W-1 final choice + W-5 correction note (REVISION CYCLE 2)

### §14a — W-1: D1 LOCKED to fallback (ii)

**Decision.** D1 is **LOCKED to fallback (ii)**: checkpoint-derived `child_message_id` via the `completion_content / serialize_message` chain. The derived id is the child's last-assistant-turn `BaseMessage.id` (UUID4) read from the surviving child checkpoint.

**Feasibility evidence (Explorer A W-1 CONFIRMED both halves).** `serialize_message` at `daemon/utils.py:181-215` surfaces `message_id` as a top-level output key (`:205-206`); the id is resolved at `:186-188` with a mint-writeback fallback at `:189-192` (so id-less messages are stamped on first read). The chain `completion_content.get_last_assistant_message` at `daemon/services/completion_content.py:22-56` returns `(content, created_at)` only (precision note: it does NOT return the id directly); the id is read from the underlying serialized dicts returned by `get_instance_messages` at `daemon/persistence.py:312/:521/:524` (every dict in the returned list carries the derived `message_id` because `serialize_message` adds it).

**Id-space mismatch (load-bearing).** The derived id is `BaseMessage.id` (UUID4). The natural path's `completed_message_id` is `MessageQueue.message_id` (also UUID4, but a different UUID4 minted by the natural-completion `enqueue_shared` path at `child_reports.py:3757`). These two id spaces are **stable-but-different** for the same logical child completion event. The dedup key at `child_reports.py:3498-3507` is `MessageQueue.message_id`-space exact equality, which would never match the checkpoint-derived id.

**Operative cross-path dedup: the PREFIX ledger.** The cross-path dedup is therefore the `internal_report:{child_id}` PREFIX check, NOT exact-id equality:

* **Queue-side generalization** (per W-1 directive): `WHERE instance_id=:parent AND source LIKE 'internal_report:{child}:%' AND status IN ('ready', 'processing', 'completed')` — generalizes the exact-equality check at `child_reports.py:3498-3507` to a prefix-match. Precedent commits: `dfac6ff0` and `000f39db` (per Explorer A's verification).
* **Parent-history-side scan:** read the parent's `get_instance_messages` serialized dicts and check `serialized.get("source", "").startswith(f"internal_report:{child}")` — `source` is surfaced at `daemon/utils.py:264-266` (the same `additional_kwargs.source` surface that the prior cycle verified for the F-2 sweep's ledger check).

**STATE THIS EXPLICITLY** in `phase2-plan.md` wherever the dedup is discussed. The exact-id equality is NEVER the operative check under the pivot.

**Fallback (iii) status: CONDITIONAL.** Fallback (iii) — loosen the dedup key to `child_id` alone and rely on the status-level guards (terminal short-circuit + atomic conditional UPDATE + parent lock + root-gate fresh-assistant) — remains documented as a CONDITIONAL on code-verified infeasibility of fallback (ii) + pinning tests only. It is NOT the primary path.

**Fallback (i) status: STRUCK.** Fallback (i) — add a new `get_for_message(thread_id, message_id)` method to `MessageMetadataRepository` — is **STRUCK** from the plan entirely. It was the prior cycle's option for the D1 conditional but is **SUPERSEDED** by the W-4 pivot: under the pivot, the lane-2 anchor fallback IS the mechanism (the child_message_id is checkpoint-derived in Python, not retrieved from a side table). Adding a new repository method is therefore dead code.

### §14b — W-5: correction note (amendment-summary.md check (c) was wrong)

**Correction note (dated 2026-10-04, REVISION CYCLE 2).** The prior cycle's `amendment-summary.md` check (c) row claimed the metadata side table is "populated for HUMAN messages (entry-path taps at `instance_messaging.py:1390-1407, :4364-4366`), NOT assistant turns". This is **PARTIALLY WRONG**: the `tap_node_return` site at `daemon/services/message_tap.py:189-252` upserts `(message_id, created_at)` via `upsert_batch` at `:240-244` (extracted by `_extract_ids` at `:180-187`), wired at `daemon/graph.py:9418-9419` with the comment at `:9406-9414` "tool_calls AI messages and the AIMessage response are tapped normally". The metadata side table **DOES** capture AIMessages (the assistant's reply).

**The load-bearing ID-SPACE MISMATCH STANDS — and is in fact STRONGER.** All three id spaces that matter are mutually disjoint:

* `MessageMetadata.message_id` = `BaseMessage.id` (UUID4) — `daemon/repositories/message_metadata/models.py:53, :76`.
* Wake row's own `message_id` = fresh UUID4 minted at `child_reports.py:3757` (MessageQueue-space).
* Source-suffix `completed_message_id` = `MessageQueue.message_id` — the queue-side id retrieved at `child_reports.py:698, :1039/:4579` (`task get_by_message`).

The side table can never join the wake row (different id spaces). The PREFIX-ledger cross-path dedup (see §14a) is therefore the ONLY correct cross-path check.

**Document this correction** in `amendment-summary.md` (append a dated note, do not rewrite the prior cycle's record — per the dispatch directive "Do NOT rewrite history silently — add a dated correction note").

**The decision is unchanged.** The decisions in this cycle rest on the PREFIX-ledger cross-path dedup, not on the side table. The W-1 fallback (ii) is checkpoint-derived id; the PREFIX ledger is the operative cross-path check; the metadata side table is irrelevant to the F-2 architecture.

**Settling citation.** `daemon/services/message_tap.py:189-252`; `daemon/graph.py:9406-9419`; `daemon/repositories/message_metadata/models.py:53, :76`; `child_reports.py:698, :3757, :4579`; precedent commits `dfac6ff0` + `000f39db`.

---

## §15 — Plan author additions (post-dispatch verification, REVISION CYCLE 2)

### §15a — `get_instance_messages` source-surfacing RESOLVED (prior cycle)

**Decision.** The synthesis item 4 verification step is
**CLOSED**. `daemon/utils.py:264-266` surfaces
`additional_kwargs.source` to `serialized["source"]` whenever
truthy, so `get_instance_messages` returns dicts that include
the `"source"` key for messages with the marker. The Phase 2
ledger check relies on this. **No additional verification task
is required.** (Under the W-4 pivot, the F-2 PREFIX ledger
check at `phase2-plan.md` task 2.7 uses the same
`get_instance_messages` surface; the verification is still
valid.)

**Settling citation.** `research-findings.md §3`; the explicit
read-back of `daemon/utils.py:245-279` on the worktree.

### §15b — Wake Task NULL `work_id` confirms design (a) is
out-of-scope for this release (prior cycle)

**Decision.** Confirm §3a: design (a) is DEFERRED. The
NULL-`work_id` fact (Explorer B corrected-anchor table) means
the JobItem-anchor preserve clause at
`repository.py:4385-4394` can never match wake tasks; a
predicate extension that does not also widen the JobItem-anchor
clause would do nothing. Widening that clause is a hot-path
change that this commission's evidence does not justify.

**Settling citation.** Explorer B corrected-anchor table;
`repository.py:1316-1322` (explicit NULL-work-id skip in
`find_work_ids_on_active_jobs_with_alive_instances`).

### §15c — `from __future__ import annotations` discipline (prior cycle)

**Decision.** All new Python code added by this commission
MUST carry `from __future__ import annotations` at the top of
the module (or use no module-level string forward-refs at all),
per the binding repo gotcha (`.venv` is CPython 3.13 while code
targets 3.14).

**Settling citation.** The commission brief's binding gotchas
section; the existing `auto_continue_boot_pass.py` module (which
already carries the import) as the in-repo precedent.

### §15d — REVISION CYCLE 2: structure note on section ordering

**Note.** The section ordering in this file follows the
additive principle: each revision cycle appends its load-bearing
decisions at the end of the main design section (§1-§9) and
moves post-dispatch verifications to this §15 group. The
prior cycle's §10/§10a/§10b/§10c content is preserved verbatim
in §15a/§15b/§15c (no semantic change). The prior cycle's
§11/§11a/§11b deferred follow-ups are preserved at their
original numbering (see §11 below).

---


### §16 — POST-REVIEW HARDENING BACKLOG (recorded-not-implemented, 2026-10-04)

**Appendix (additive; no history rewrites).** The external
post-implementation code review (APPROVE-WITH-NOTES, 2026-10-04)
recorded the following hardening items as backlog. They are
RECORDED-NOT-IMPLEMENTED: none is a merge blocker for the F-1/F-2
branch; each is a candidate for a follow-up hardening cycle.

(a) **S28 PG positive ledger-skip test.** A real-PG test proving
the parent-history PREFIX ledger POSITIVE path (a parent history
carrying the real no-colon HumanMessage stamp ⇒ the lane-2
per-row pass skips with `skipped_already_reported += 1`). The
shipped S24/S27 pair covers admission + no-duplicate-execution;
the positive-skip path is unit-pinned only.

(b) **`except (Exception, CancelledError)` at both bridge seams.**
The manager-loop bridge call sites (parent-history PREFIX check
and anchor-less derivation in
`daemon/services/report_delivery_recovery.py`) catch bare
`Exception`; a `CancelledError` crossing the seam would escape
the per-row isolation. Python 3.13-safe re-raise semantics
apply (see the lifecycle-hooks precedent).

(c) **Bridge `.result(timeout)` coroutine-cancel on timeout.**
When the manager-loop bridge times out, the scheduled coroutine
keeps running on the manager loop (fire-and-forget). A follow-up
could cancel the future/wrapped task on timeout so a wedged
read cannot accumulate across sweep cycles. Both seams (bridge
method + the step-4 seam in `manager.py`).

(d) **Pre-manager-init sweep debug log**
(`report_delivery_recovery.py:1233-1236` area): the anchor-less
"no derivable child_message_id" WARNING currently fires before
the manager is fully initialized during boot-adjacent sweeps;
a debug-grade pre-init marker would distinguish that expected
boot-noise from genuine wedge persistence.

(e) **Falsy-non-None error operator-visibility warning.** An
`Outcome(status="error", error="")` (falsy-but-not-None error)
currently trips neither the `_has_truthy_error` flip nor any
WARNING — the bus silently skips the parent-error flip. A
one-line operator-visibility warning for the falsy-non-None
shape would keep that state observable.
