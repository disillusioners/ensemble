# Architecture Recommendation: Durability F-1 + F-2

**Date:** 2026-10-04
**Worktree:** `/home/nea/ensemble-src-wt-durability` @ `18827dbd` (branch `feature/durability-f1-f2`)
**Author:** Architect (controller), aggregating three skill-permissioned workers
**Status:** Complete — consolidated recommendation
**Workers dispatched:**
  - `architect-worker-dataflow-boot-f1` (`data-flow-design`) — focus areas 1, 3
  - `architect-worker-resilience-f2-rollback` (`resilience-design`) — focus areas 2, 5
  - `architect-worker-tradeoff-deferral-missed` (`trade-off-analysis`) — focus areas 4, 6

---

## Status

**Complete** — all three workers reported. Verdicts aggregated below; plan amendments consolidated as drop-in text. No `### Gaps` — every focus area has a verdict.

---

## Consolidated Recommendation

**PROCEED WITH ADJUSTMENTS — 4 REJECTs, 6 ADJUSTs, 2 plan-text accuracy fixes.**

The 3-phase durability plan is sound on the architecture-shape level (boot-sequence ownership, target set disjointness, dedup chain, kill-switch convention). However, **the F-2 plan section contains one phantom API reference, one self-contradicting fallback clause, and one mis-scoped exception handler** that must be fixed before Phase 2 task 2.8 starts implementation. The F-1 plan section needs a hot-path kill-switch (currently revert-only) for over-preservation safety.

### REJECTs (must fix before Phase 2 implementation)

| # | Worker finding | Plan section | Severity |
|---|---|---|---|
| R-A | `MessageMetadataRepository.get_for_message` **does not exist** — the API surface is `get_for_thread` / `upsert_batch` / `delete_for_thread` (`daemon/repositories/message_metadata/repository.py:45-184`). Phase 2 task 2.8 references a phantom API. | `phase2-plan.md` task 2.8 | 🔴 Critical — Phase 2 cannot start |
| R-B | `decisions.md §3` step 6 has an internal contradiction: text says "falling back to the child-instance-id + content-hash pair" AND "logs a WARN and skips the child". `research-findings.md §2` and `plan-overview.md` Out-of-Scope explicitly state content-keyed idempotency does not exist (grep-verified). DROP the content-hash fallback. | `decisions.md` §3 step 6 | 🔴 Critical — design accuracy |
| R-C | Phase 2 task 2.10 wraps `run()` body in a SINGLE top-level try/except but claims "a failure to synthesize one child must not abort the sweep for the remaining parents". These are mutually exclusive. The try/except must be INSIDE the per-parent loop. | `phase2-plan.md` task 2.10 | 🔴 Critical — sweep isolation |
| R-D | F-1 predicate change is hot-path; no kill-switch. Recommend env-direct `ENSEMBLE_BOOT_EPOCH_PRESERVE_HEARTBEAT=0` (default ON; disables ONLY arm 2, leaves arms 1+3). Mirrors the sweep's kill-switch pattern. | `decisions.md` §2 (new sub-section) | 🟡 Significant — production safety net |

### ADJUSTs (refine the design)

| # | Worker finding | Plan section |
|---|---|---|
| A-1 | Add `decisions.md §2b` documenting the bus orphan-sweep interaction with preserved-terminal tasks (`dependency_bus.py:1896-2029`, 30s grace; PENDING watchers on preserved-terminal tasks cancelled as "true orphan"; self-heals via WC sweep backstop). | `decisions.md` §2 (new sub-section) |
| A-2 | Post-lock re-check (Phase 2 task 2.9) MUST re-issue BOTH wake-row check AND parent-history ledger scan (cheap; pre-fetched or 1 DB read). | `phase2-plan.md` task 2.9 |
| A-3 | Service-internal dedup at `child_reports.py:3501-3506` filters `MessageStatus.READY/PROCESSING/COMPLETED` only — does NOT include PENDING. Document the coupling so a future preserved-wake widener widens the dedup in lockstep. | Phase 2 follow-up note |
| A-4 | Couple map should record that both the F-1 fix (`:4380-4394`) and the parallel claim-gate sibling-deadlock fix branch (`:2587-2609`) touch `task/repository.py` — different sites, no merge conflict, but reviewers must read the diff as a set. | `plan-overview.md` Coupling Map |
| A-5 | None-boot_epoch observability — add process-global counter `durability_f1_none_boot_epoch_total` exposed via existing `/readiness` or `/metrics` endpoint. Optional but cheap (~5 lines). | `decisions.md` §2a (minor) |

### Plan-text accuracy fixes (drop in)

| # | Worker finding |
|---|---|
| T-1 | `decisions.md` §3 step 6 method name correction: `manager.message_metadata_repo.get_for_message` → use `MessageMetadataRepository.get_for_thread(thread_id)` and look up the specific `message_id`. The plan's "(or equivalent)" wording obscures that the named API is phantom. |
| T-2 | `decisions.md` §3 step 2 + step 7 — `discover_pending_wakes(child_id)` helper does NOT exist on the worktree (grep-verified). Phase 2 must ADD it as part of the sweep's read-side surface. The helper is a small read-only repository method. |

---

## Approach Comparison

This was not a competitive fan-out (the plan is set; we are validating depth). All three workers operated on the same plan with three different lenses. Comparison is therefore **lens-by-axis**, not approach-by-axis.

| Lens | Complexity | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|
| Data-flow (FA1+FA3) | Low — current boot order is straight-line; new step slots cleanly | High — ownership block + inline comments + sweep's parent-history ledger all readable | 🟡 Orphan-sweep cancels preserved-terminal's PENDING watcher — bounded by WC sweep backstop | Low | ENDORSE + A-1 |
| Resilience (FA2+FA5) | Medium — sweep's isolation requires per-loop try/except (not per-function); kill-switch for F-1 adds one env var | Medium — phantom API must be replaced before Phase 2; drop the content-hash fallback | 🔴 R-A (phantom API), R-B (contradictory), R-C (exception scope) must be fixed | Low-Medium | 3 REJECTs + A-2 + A-3 |
| Trade-off (FA4+FA6) | Low — design (b) covers all three windows; dedup chain verified across three layers | High — coupling map doc aligns branches; waiting_children_watchdog / terminalizer / observer all orthogonal | 🟢 No wedge hazards, no merge conflicts, no races found | Low | ENDORSE + A-4 + T-2 |

**Net verdict:** proceed with the 4 REJECTs fixed (blockers) and the 6 ADJUSTs / 2 plan-text fixes applied. The plan is structurally sound; the blockers are scope/contract errors, not architecture errors.

---

## Per-Area Verdict (Detailed)

### Focus Area 1 — Boot-sequence ownership contract

**Verdict:** ✅ **ENDORSE** with ADJUST A-1 (orphan-sweep interaction note).

**Evidence (worker-1, `data-flow-design`):**

- All 13 ownership steps in `decisions.md §5` verified against worktree anchors. One-owner-per-step confirmed. No interleaving hazard: auto-continue pass selection at `repository.py:915-921` explicitly excludes `waiting_children` parents; the sweep targets `waiting_children` parents. Disjoint sets.
- Epoch-before-wipe (`capture_boot_epoch(engine)` BEFORE wipe in InstanceManager ctor): the function requires a pre-existing engine (`boot_epoch.py:91-133` calls `engine.connect()` at `:112-113`). `self._engine` is set at `manager.py:535/538`; the wipe block starts at `:771` — engine is available. `boot_epoch.py:103-105` is first-wins / idempotent — the retained `api.py:412` call returns the same value. Move is benign.
- All 5 `get_boot_epoch()` consumers verified safe (auto-continue pass at `api.py:1553`, readiness `queue_freshness` at `:2361`, `stale_task_recovery.py:766` and `:407`, `repository.py:3363`). The consumer reads at `stale_task_recovery.py:766` runs in `recover_on_startup` at `setup_worker_pool` (step 6), AFTER the constructor wipe — safe.
- WC sweep slot position (`api.py:1547-1564` → between `:1564-1571`, inside envelope-try): correct. The sweep captures the WC parent list at entry (`decisions.md §3`) and works from the captured snapshot — no mid-sweep drift hazard.

**Adjustment A-1:** Add `decisions.md §2b` documenting that the bus orphan-sweep at `dependency_bus.py:1896-2029` (def `:1896`, grace `:1984-1988` = 30s default, active-set predicate at `:1919-1920` filtering `status IN ('running','pending','paused')`) cancels PENDING watchers on preserved-terminal tasks as "true orphans". Normal flow: a child that terminal-ed via `emit_terminal` has its watcher FIRED (atomic PENDING→FIRED), not PENDING — no interaction. F-1 wedge flow: orphan sweep runs at `bus.start()` (boot step 7) BEFORE the WC sweep (boot step 11). For a wedged parent in `waiting_children`, the orphan sweep may cancel the PENDING child watcher within 30s. The WC sweep is the compensating backstop (per-parent lock + re-invocation). Operator-facing risk bounded by (30s grace + sweep-duration). Drop-in text below.

### Focus Area 2 — F-2 sweep design soundness

**Verdict:** 🔴 **REJECT** (3 REJECTs + 5 ADJUSTs). Sweep architecture is correct; implementation contract has 3 errors that must be fixed before Phase 2.

**Evidence (worker-2, `resilience-design`):**

| Sub-concern | Verdict | Evidence |
|---|---|---|
| Pre-lock ledger check on the right `source` shape | ✅ ENDORSE | `daemon/utils.py:264-266` surfaces `additional_kwargs.source` as `serialized["source"]`; `graph.py:8476` confirms stamp shape; `startswith("internal_report:<child_id>")` matches. |
| Service-internal dedup status set | ⚠️ A-3 | `child_reports.py:3501-3506` filters READY/PROCESSING/COMPLETED only — PENDING wake would NOT be caught. |
| Preserved wake + sweep synthesizing | ⚠️ A-2 | Sweep's derived `completed_message_id` must equal preserved `msg_id` for service-internal dedup to catch. Metadata preservation is required; on absence the wedge persists for that child (acceptable per R-P2-3). |
| `completed_message_id` derivation API | 🔴 R-A | `MessageMetadataRepository.get_for_message` **does not exist**. Actual API: `get_for_thread` / `upsert_batch` / `delete_for_thread` (`daemon/repositories/message_metadata/repository.py:45-184`). Plan task 2.8 references phantom API. |
| Content-hash fallback in §3 step 6 | 🔴 R-B | Self-contradicts: "content-hash fallback" + "log WARN and skip" in same paragraph. `research-findings.md §2` and Out-of-Scope explicitly state content-keyed idempotency does not exist. DROP the content-hash fallback. |
| Per-child try/except scope | 🔴 R-C | Task 2.10 says "wrap entire run() body" + "must not abort remaining parents" — mutually exclusive. Need try/except INSIDE the per-parent loop. |
| Sweep crash mid-parent (envelope-try scope) | ⚠️ see A | With R-C fix (per-parent try/except), the outer envelope-try becomes redundant safety net only. |
| Sweep is singleton / boot-only | ✅ ENDORSE | `wc_wedge_sweep.py` does NOT exist on disk; only call site is `api.py:1547-1571`. No admin endpoint, no hot-reload trigger. |
| Parent-history scan race | ⚠️ A-2 | Between pre-lock `get_instance_messages` and post-lock `discover_pending_wakes`, normal delivery could inject a report. Post-lock re-check MUST re-issue BOTH wake rows AND parent-history ledger scan. |
| `bus._get_parent_lock` acquisition | ✅ ENDORSE | `dependency_bus.py:1627`; `child_reports.py:2563`; per-parent lock-ordering at `:1637-1649`. No deadlock risk. |

### Focus Area 3 — F-1 preserve-predicate disjunction

**Verdict:** ✅ **ENDORSE** with A-1 + A-5.

**Evidence (worker-1):**

- Predicate math is correct. A row is DELETED only if it fails ALL three arms: NOT in (running, paused) AND (heartbeat is NULL OR pre-epoch) AND no `auto_continued_at` marker. This is the correct delete-set.
- Inverse (preserve) is correct: arm 2 catches "terminal + recent heartbeat" (F-1 wedge target); arm 3 catches "auto-continue stamped" (durable backstop); arm 1 catches legacy RUNNING/PAUSED. Disjunction widens keep-set only for rows the feature intends to keep. **No unintended deletion math.**
- None boot_epoch case (`:2a`): narrows to arms 1+3 — correct degraded behavior.
- Regression surface — only 2 production callers of `clear_all(preserve_in_flight=True)`:
  - `daemon/manager.py:772` (queue wipe in ctor) — covered by task 1.9
  - `daemon/manager.py:869` (task wipe in ctor) — covered by task 1.8
  - All other matches are test files or comments. **No other production callers.** Default `boot_epoch=None` parameter keeps tests compiling unchanged.
- WARNING-log-on-None observability: `dependency_bus.py:95` uses module logger with no `propagate=False`; daemon's root logger at `api.py:65, :90` catches everything. Warnings land in operator log. **A-5 (optional):** add process-global counter `durability_f1_none_boot_epoch_total` exposed via `/readiness` or `/metrics`.

### Focus Area 4 — Deferral of design (a)

**Verdict:** ✅ **ENDORSE**. Design (b) covers all three sub-windows W-A / W-B / W-C.

**Evidence (worker-3):**

- W-A (terminal-write → wake-rows-commit), W-B (commit → enqueued_at stamp), W-C (stamp → wake-task claim): all handled by design (b). The child's checkpoint content is durable across the wipe — `completion_content.get_last_assistant_message` (`daemon/services/completion_content.py:22-56`) reads via `get_instance_messages` (`:46`), which surfaces LangGraph checkpoint content, NOT the wiped `task` / `message_queue` tables.
- Sweep re-invokes `_process_child_completion_db_sync` (`child_reports.py:2674`) which extracts the last assistant content from the surviving checkpoint and writes a fresh synthesized report. **Same checkpoint → same `completed_message_id` on every re-run → inner exact-equality dedup at `:3501` hits on second pass.**
- Dedup safety chain (verified across three layers):
  1. Sweep's parent-history ledger check: `source.startswith("internal_report:<child_iid>")` via `get_instance_messages`.
  2. Service-internal dedup at `child_reports.py:3498-3510`: exact equality on `(child_id, completed_message_id)`.
  3. Sweep's `discover_pending_wakes` re-check inside the lock (`decisions.md §3` step 7).
- If (a) is later added: preserved wake-row → `discover_pending_wakes` finds it → sweep skips. Correct behavior. No race.

### Focus Area 5 — Rollback story

**Verdict:** ⚠️ **REJECT** R-D (F-1 kill-switch needed); ✅ **ENDORSE** for the sweep kill-switch.

**Evidence (worker-2):**

- Sweep kill-switch (`ENSEMBLE_WC_WEDGE_SWEEP_ON_BOOT`): mirrors `auto_continue_boot_pass.py:145-156` pattern correctly. Module-level constant + `_wc_wedge_sweep_enabled() = os.environ.get(ENV, "1") != "0"`, per-boot read. `skipped_kill_switch=1` accounting per `auto_continue_boot_pass.py:245-248` mirrored in `WcWedgeSweepResult`. ✅
- F-1 rollback is REVERT-ONLY currently. R-D: the predicate change is hot-path (every daemon restart); if the new arms 2/3 over-preserve in production, dead/live task rows accumulate across boots. The bus orphan-sweep at `dependency_bus.py:1896-1923` only cleans watchers whose source-task is GONE — not preserved rows with stale source-tasks. **Recommend kill-switch** `ENSEMBLE_BOOT_EPOCH_PRESERVE_HEARTBEAT=0` (default ON; disables ONLY arm 2, leaves arms 1+3).
- Option-2 fallback (§2's Option (iii) emergency-shipping fallback): a code-ship change. The kill-switch (R-D) is the operator-friendly version — no code-ship needed if pre-prod S4/S5 fail.
- Producer-side defensive `logger.warning` observability: ✅ lands in operator log via `api.py:65, :90` root logger; no `propagate=False` on `dependency_bus.py:95`.

### Focus Area 6 — Architectural smell test

**Verdict:** ✅ **ENDORSE** with A-4 (coupling map note) + T-2 (`discover_pending_wakes` helper origin).

**Evidence (worker-3):**

- **Parallel claim-gate fix branch** (claim-gate sibling deadlock, separate commission): different sites — `:2587-2609` (cross-system guard) vs `:4380-4394` (clear_all preserve DELETE). No merge conflict. Semantic interaction: minor (F-1 arm 3 preserves MORE rows visible to claim-gate fix; two changes compose). **A-4: add note to coupling map.**
- **Terminalizer** (`manager.py:11660-11721`, gate conjunct `:11700-11702` accepts only `auto_continued_at is not None`): no feedback loop. Terminalizer scope = (CAS-stamped) ∩ (RUNNING). A terminal row is no longer RUNNING → terminalizer skips. Arm 3 preserves audit trail only. ✅
- **JobFeedbackObserver terminal-token contract** (`job_feedback_observer.py:1698-1714` accepts these two strings): swept does NOT introduce new tokens. Sweep is idempotent — produces same token sequence as original completion path. **No wedge hazard.** ✅
- **Waiting children watchdog** (`daemon/services/waiting_children_watchdog.py:73` enqueues `system:watchdog` HANG NOTICE via `enqueue_message`; line 73/179 — `WEDGE_SOURCE = "system:watchdog:wedge"`): different child populations. Watchdog targets HUNG (non-terminal); sweep targets TERMINAL. Watchdog's scan-driven purge at `:140-144` excludes terminal. **No race for same child.** Edge case: if sweep heals parent, watchdog's next tick drops the pair (no double-delivery). ✅
- **Auto-continue pass interaction** (`repository.py:915-921` excludes `waiting_children`): auto-continue could pick up a RUNNING child of a `waiting_children` parent; sweep excludes RUNNING children. **No race.** A child itself in `waiting_children` (waiting on its own children) — auto-continue doesn't select its tasks; sweep doesn't target it. ✅
- **Atomic-flip / portable mv-Tf**: plan does NOT touch file-flush surface. ✅ N/A.
- **R18 spawn_hot_instance AUTO-DISPATCH**: sweep is service invocation, not spawn. Plan does not call `spawn_hot_instance`. ✅ N/A.

---

## Plan Amendments (Drop-in Text)

These are consolidated drop-in patches for the planner's phase files. Apply as-is.

### Amendment P-A — `decisions.md` §2 new sub-section §2b (after §2a)

> **§2b — Orphan-sweep interaction with preserved-terminal tasks (NEW)**
>
> The F-1 preserve-predicate extension (3-arm disjunction) keeps terminal tasks with a recent heartbeat OR an `auto_continued_at` marker. Such preserved-terminal tasks are NOT in the bus's orphan-sweep active-set predicate (`dependency_bus.py:1919-1920`: `status IN ('running','pending','paused')`). A PENDING watcher on a preserved-terminal task would be cancelled by the orphan sweep as a "true orphan" (30s grace at `:1984-1988`).
>
> Normal flow: a child that terminal-ed via `emit_terminal` has its watcher FIRED (atomic PENDING→FIRED transition), not PENDING — no orphan-sweep interaction.
>
> F-1 wedge flow: the orphan sweep at `bus.start()` (boot step 7) runs BEFORE the WC wedge sweep (boot step 11). For a wedged parent in `waiting_children`, the orphan sweep may cancel the PENDING child watcher within the 30s grace. The WC sweep at step 11 is the compensating backstop: it re-invokes `ChildReportsService._process_child_completion` for every `waiting_children` parent, which itself acquires `bus._get_parent_lock` and re-delivers the synthesized report.
>
> Operator-facing risk: the 30s orphan-sweep grace + the WC sweep's per-parent lock acquisition bound the recovery latency to (grace + sweep-duration) ≈ 30s + O(N) for N waiting_children parents.

### Amendment P-B — `decisions.md` §3 step 6 method-name correction + drop content-hash fallback

> **Step 6 — `completed_message_id` derivation (CORRECTION).**
>
> The sweep derives `completed_message_id` from the surviving child checkpoint's last assistant message metadata via `MessageMetadataRepository.get_for_thread(thread_id)` (returns `{message_id: (created_at, seq)}` per `daemon/repositories/message_metadata/repository.py:123`), then looks up the specific `message_id` for the last assistant turn. **The `get_for_message(thread_id, message_id)` method cited in `phase2-plan.md` task 2.8 does not exist on the current repository surface** — use `get_for_thread` and post-filter, OR add a new `get_for_message` method to the repository in Phase 2.
>
> The source-diff dedup at `child_reports.py:3498-3510` keys on `(child_id, completed_message_id)` exact equality — so a sweep that derives the same `completed_message_id` on every re-run will hit the dedup naturally on subsequent passes.
>
> If metadata is unavailable, the sweep logs a WARN and skips the child (preserves idempotency over dedup-by-status). **No content-hash fallback is implemented** — content-keyed idempotency does not exist in the codebase (`research-findings.md §2`, grep-verified); introducing it would expand the scope beyond the commission's evidence.

### Amendment P-C — `decisions.md` §3 step 2 + step 7 — `discover_pending_wakes` helper origin

> The plan references a new `discover_pending_wakes(child_id)` repository helper that "returns the count of `Task(task_type=PROCESS_REPORT, instance_id=parent_id, message_id=<...>, status='pending')` rows for that child". **This helper does NOT exist on the worktree (grep-verified).** Phase 2 task 2.4 MUST add it — a small method on `TaskRepository`, parallel to `find_stale_running_tasks` at `repository.py:3329-3395`. The sweep's re-check at step 7 and the parent-scan filter at step 2 both depend on this helper existing.

### Amendment P-D — `phase2-plan.md` task 2.8 — replace API reference

> Replace `manager.message_metadata_repo.get_for_message(thread_id, message_id)` with `manager.message_metadata_repo.get_for_thread(thread_id)` (returns `{message_id: (created_at, seq)}` — see `daemon/repositories/message_metadata/repository.py:123`), then post-filter to find the `message_id` matching the child's last assistant turn.

### Amendment P-E — `phase2-plan.md` task 2.10 — restructure try/except scope

> **Restructured task 2.10 (replace the previous prose).** Place the `try/except` INSIDE the per-parent loop, wrapping the per-parent work (parent scan + child filter + ledger check + service invocation + post-lock re-check). On a non-fatal exception, log at WARNING with `parent_id` (truncated 8 chars) and the exception class+message, increment `result.errors`, and `continue` to the next parent. The top-level `try/except` around `run()` is a redundant safety net that logs once if everything blows up. **Critical:** a single top-level try/except is mutually exclusive with the "must not abort remaining parents" requirement — the previous prose was internally inconsistent.

### Amendment P-F — `phase2-plan.md` task 2.9 — extend post-lock re-check

> The post-lock re-check MUST re-issue BOTH (a) `discover_pending_wakes(child_id)` for wake rows AND (b) the parent-history ledger scan (`get_instance_messages` → `source.startswith(...)`) for newly-injected reports. Either the wake row OR a parent-history source-match triggers the skip. The previous prose checked wake rows only.

### Amendment P-G — `decisions.md` §2 new sub-section §2c — F-1 kill-switch

> **§2c — F-1 predicate kill-switch (NEW).**
>
> The predicate extension (3-arm disjunction) is hot-path (every daemon restart exercises it). If arm 2 (heartbeat) over-preserves in production, task / message_queue rows accumulate across boots — a recovery is a multi-site code-ship. Recommend an env-direct kill-switch `ENSEMBLE_BOOT_EPOCH_PRESERVE_HEARTBEAT` (default ON; `=0` disables ONLY arm 2 of the 3-arm disjunction in `TaskRepository.clear_all` and `MessageQueueRepository.clear_all`, leaving arms 1 (status) and 3 (`auto_continued_at`) intact). Read per boot at the wipe call, mirroring the sweep's `ENSEMBLE_WC_WEDGE_SWEEP_ON_BOOT` pattern at `auto_continue_boot_pass.py:145-156`. When OFF, the boot log carries a single `WARN` line naming the disabled-arm path. The F-1 fix remains active; only the heartbeat arm is gated.

### Amendment P-H — `plan-overview.md` Coupling Map — claim-gate sibling-deadlock fix

> Add a row to the Coupling Map table:
>
> | | Phase 1 | Phase 2 | Phase 3 |
> |---|---|---|---|
> | Parallel claim-gate sibling-deadlock fix (separate commission, project note) | independent: different site (`task/repository.py:2587-2609` cross-system guard) — F-1 touches `:4380-4394` (clear_all preserve DELETE) | independent | independent |
>
> Observation: F-1 arm 3 (`auto_continued_at IS NOT NULL`) increases the number of preserved rows visible to the claim-gate fix; the two changes compose (no semantic conflict, no merge conflict), but both touch `task/repository.py` and reviewers must read the diff as a set.

### Amendment P-I (optional) — `decisions.md` §2a — None-boot_epoch observability

> Add a process-global counter `durability_f1_none_boot_epoch_total` (an `int` in `boot_epoch.py`, incremented on None-capture, exposed via the existing `/readiness` or `/metrics` endpoint). Operators get a histogram bin for "how many boots degraded". Cost: ~5 lines; value: visible in `/metrics` without log-grep. **Optional follow-up; the WARN line is sufficient to ship.**

---

## Risks

Aggregated from all three workers, severity-ranked:

### 🔴 Critical (block Phase 2 implementation)

1. **Phantom `get_for_message` API** (R-A, worker-2). `phase2-plan.md` task 2.8 references an API that does not exist on `MessageMetadataRepository`. Phase 2 cannot start until task 2.8 is rewritten to use `get_for_thread` + post-filter, OR until a new `get_for_message` method is added to the repository.

2. **Self-contradicting content-hash fallback** (R-B, worker-2). `decisions.md §3` step 6 contains both "content-hash fallback" and "log WARN and skip" in the same paragraph. `research-findings.md §2` and Out-of-Scope explicitly state content-keyed idempotency does not exist (grep-verified). Drop the content-hash fallback before Phase 2 starts.

3. **Mis-scoped exception handler** (R-C, worker-2). `phase2-plan.md` task 2.10 says "wrap entire run() body" + "must not abort remaining parents" — mutually exclusive. Restructure to per-parent try/except inside the loop.

### 🟡 Significant

4. **F-1 over-preservation has no operator-friendly kill-switch** (R-D, worker-2). The hot-path predicate extension is revert-only currently. Recommend `ENSEMBLE_BOOT_EPOCH_PRESERVE_HEARTBEAT=0`.

5. **Bus orphan-sweep cancels PENDING watchers on preserved-terminal tasks** (A-1, worker-1). The 30s grace + WC sweep backstop self-heals, but the plan does not name it. Drop-in §2b.

6. **`PENDING.value` MessageQueue status not in service-internal dedup set** (A-3, worker-2). `child_replication.py:3501-3506` filters READY/PROCESSING/COMPLETED only. A preserved wake in PENDING status would NOT be caught. Mitigated by the sweep's post-lock re-check at task 2.9 (A-2 — re-check both wake rows AND parent-history ledger).

### 🟢 Improvement opportunities

7. **`discover_pending_wakes` helper does not exist** (T-2, worker-3). Phase 2 task 2.4 must add it.

8. **`get_instance_messages` parent-history ledger check needs a re-scan after the lock** (A-2, worker-2). The post-lock re-check should re-issue BOTH wake rows AND parent-history scan.

9. **None-boot_epoch metrics counter** (A-5, worker-1, optional). Cheap follow-up.

10. **Parallel claim-gate fix branch coupling map entry** (A-4, worker-3). Different sites, no merge conflict, but reviewers must read the diff as a set.

---

## Decisions Pending

These decisions should be resolved before Phase 2 implementation begins:

1. **`get_for_message` API choice** — Add a new method to `MessageMetadataRepository`, or use the existing `get_for_thread` + post-filter? The latter is the smaller change. (Implementer's choice; the plan should call this out.)

2. **F-1 kill-switch scope** — Include `ENSEMBLE_BOOT_EPOCH_PRESERVE_HEARTBEAT` in this commission, or defer to Phase 1.5? The change is ~3 lines plus tests. Recommend inclusion (low-cost safety net for a hot-path change).

3. **None-boot_epoch metrics counter** — Include in this commission, or defer? (~5 lines, optional follow-up.)

4. **PENDING MessageQueue status dedup coverage** — Widen the service-internal dedup at `child_reports.py:3501-3506` to include `PENDING.value`, or rely on the sweep's post-lock re-check (A-2)? Recommend the latter (minimal scope) but document the coupling at `:3502`.

5. **Service-internal dedup status-set widening** — A-3 is a Phase 2 follow-up note in `child_reports.py:3502`. Does the implementation include the comment, or defer?

---

## Open Questions

1. **`JobFeedbackObserver._resolve_finalize_status` metrics** — does it have a per-finalize counter, or is the WARN log the only signal? (Worker-1 question.) Plan should explicitly check `job_feedback_observer.py:125, :171-174` for a counter; if none, A-5's optional counter covers it.

2. **`wc_wedge_sweep.py` parent-list SQL** — what is the exact repository method for "list waiting_children parents"? `decisions.md §3` says "captured in a single read at sweep entry"; the SQL is `SELECT id FROM instance WHERE status = 'waiting_children'`. A `repository.list_waiting_children_parents()` method is the natural home. (Worker-1 question.)

3. **`api.py:2459+` step 8 (finalization-only recovery loop) exact entry** — line-walked at high level but not byte-verified. If this is the `JobFeedbackObserver._process_event` lifecycle path, the bus-side truthy-error fix flows into the observer's read; the plan should cite the exact finalization loop entry. (Worker-1 question.)

4. **Sweep tested under parent-history-scan-race scenario** — task 2.9 covers wake-row re-check; the plan's S7-S14 don't cover "normal delivery between pre-lock scan and post-lock re-check inserts a report". Recommend a test. (Worker-2 question.)

5. **Actual `MessageQueue.status` distribution for preserved wakes** — does a preserved wake ever have `MessageQueue.status = 'pending'`? The enum at `models.py:44` defines PENDING.value. If yes, the dedup at `:3501` is bypassed for those rows. (Worker-2 question.)

6. **Source-prefix distinctness** — `WEDGE_SOURCE = "system:watchdog:wedge"` (`waiting_children_watchdog.py:179`) and `internal_report:<child>:<completed_message_id>` (F-2 sweep) do not collide. Worth a one-liner in the plan noting the prefix distinctness. (Worker-3 question, minor.)

---

## Skill / Worker Notes

- **Worker 1 (`data-flow-design`)**: applied = True, usefulness = 9. Note: clean skill — every judgment rule traced to evidence; the §2b amendment format is reusable. Improvement note: skill could explicitly remind workers to validate "non-existent API" claims by `grep`-ping the codebase before reporting an API as canonical.

- **Worker 2 (`resilience-design`)**: applied = True, usefulness = 9. Note: caught the phantom API + content-hash contradiction + exception scope — the three highest-impact findings. Improvement note: skill should remind workers to verify ALL named APIs in a plan against the actual repository surface before accepting the plan as canonical — the worker did this and it paid off.

- **Worker 3 (`trade-off-analysis`)**: applied = True, usefulness = 9. Note: clean cross-cutting analysis; verified the dedup chain end-to-end. Improvement note: skill could remind workers to check the FULL list of files matching claim for an architectural component (e.g., `waiting_children_watchdog` → grep `find daemon -name "*waiting*"`) before declaring "no interactions found".

---

## Confidence

**High** that the F-1 architecture is correct after A-1 + A-5 are applied (the predicate math is right; the orphan-sweep interaction is bounded by the WC sweep backstop).

**High** that the F-2 architecture is correct after R-A + R-B + R-C + A-2 + A-3 are applied (the sweep's target set is disjoint from auto-continue; the dedup chain has three layers; the kill-switch convention is mirrored).

**High** that the design (a) deferral is acceptable (W-A/W-B/W-C all closed by design (b); dedup chain verified across three layers).

**The single assumption that, if wrong, would flip the recommendation:** if `MessageMetadataRepository.get_for_thread` does not surface the `message_id` for the last assistant turn (i.e., the metadata side-table is not populated for older sweeps), then the `completed_message_id` derivation fails for the majority of straddle cases, and the wedge persists at the sweep layer. Validation = S7-S14 unit tests must cover this case.

---

## End of Architecture Recommendation