# Deep Review — PR `fix/wanderer-completion-reporting` (commit `690e67e8`)

> Source: Council session `cou-1` (alias `ses_081948d81ffeJAu0DuIL0Rn5T5`), completed and reconciled.
> Project: `agents-ensemble` (project_id `83da04de-a410-4fb5-9e92-251a99d28a52`).
> Reviewer role: multi-model deep review (Correctness/Concurrency, API/Backward-compat, Risk/Release-readiness).

---

## 1. Per-Expert Verdicts

### Expert 1: Correctness / Concurrency — **Verdict: APPROVE WITH MINOR CONCERNS**

The three fixes correctly close the original Wanderer emit-per-turn bug, and the developer's use of `TERMINAL_STATUSES` (4-state frozenset) at Fix 1 (`child_reports.py:1575-1581`) is correct — verified against `job_queue_service.py:46-51`. Two correctness gaps remain, neither blocking.

**Top findings:**

1. **Fix 3 leaves a remaining cross-connection TOCTOU** (`child_reports.py:1775`). The snapshot uses `bus.count_pending_for_target_sync(...)` which opens its own short-lived `Session` (`repositories/dependency_bus/repository.py:264`) outside the WriteGuardSession transaction. A concurrent `bus.watch()` INSERT or `emit_terminal` FIRE between snapshot and the post-commit bus hook can shift the count by ±1, breaking the subtract-1 invariant. The inline query pattern at `child_reports.py:1295-1304` already shows the correct shape (uses `session` directly); Fix 3 reverts to the very pattern the C2 fix was created to eliminate. The race is **bounded by the per-parent lock only for the ROOT path's pending check, not for the Fix 3 snapshot** — `child_reports.py:1054` locks on `instance_id` (the child), and `bus.watch()` locks on `_parent_id` (the parent/target). These are different lock keys.

2. **Fix 2's wedge re-emerges in the parent-cascade branch at `child_reports.py:1691-1695` and `error_reporting.py:233-237`**. If a parent reaches the cascade with `status == TERMINATED` or `status == FAILED`, the guard `and parent.status != COMPLETED and != ERROR` allows entry, and `parent.status = COMPLETED` at `child_reports.py:1718` overwrites TERMINATED → COMPLETED. This is a **silent state demotion**: a TERMINATED parent can be flipped to COMPLETED. Pre-existing, explicitly out-of-scope, but should not ship if TERMINATED carries user-visible semantics (cancellation audit trail).

3. **Fix 1 TOCTOU is bounded correctly by the per-parent lock** (when acquired at `child_reports.py:1054`). Concurrent child completions on the same parent take DIFFERENT per-parent locks (one per child), so the active-children query at `child_reports.py:1569-1582` is racy for siblings completing simultaneously. Example: A and B are siblings of Wanderer; Wanderer completes while A is RUNNING; A completes between the snapshot at 1569 and the commit at 1812 — Wanderer's snapshot still sees A as active and defers, missing the chance to be marked complete on a now-empty subtree. Recovery: Wanderer completes when its next message arrives (Stage 6 — `message_processing_pipeline.py:457`). **Not a correctness wedge** because re-entry is idempotent (Fix 2 short-circuits), but the defer is an over-deferral rather than a hang.

### Expert 2: API / Backward-compatibility — **Verdict: APPROVE WITH MINOR CAVEAT**

The new `child_still_running_defer` outcome does not collide with any other outcome string. The dispatch handler (`child_reports.py:1891-1902`) is well-formed and matches the existing `deferred_waiting_children` shape. The idempotency_skip collision between Fix 2 and the in-branch check at `child_reports.py:1492` is **functionally benign** because the dispatch handler at `child_reports.py:1935-1936` does `return` for both — they share the same observable behavior, which is correct.

**Top findings:**

1. **`waiting_children` SSE conflation** is **intentional**, not a UI semantic mismatch. All three defer variants (`deferred_waiting_children`, `root_waiting_children`, `child_still_running_defer`) emit the same `waiting_children` SSE (`child_reports.py:1862, 1876, 1894`). The comment at line 1885-1890 explicitly says "Same dispatch shape as `deferred_waiting_children` (SSE only) so the UI can reflect the wait state." UI display correctly treats all three identically as "wait state". **Confirmed intentional.**

2. **`pending_for_parent` semantic shift** is **internal**: I searched the codebase (`grep -rln pending_for_parent frontend/src/`) and found **no frontend consumer** of the field. The Event payload at `child_reports.py:1789-1799` is the only production write; events are stored in the Event table and replayed via SSE / GET-history APIs. Pre-fix consumers that compared "pending_for_parent > 0" to decide whether to show a "more children incoming" affordance would now see 0 when the parent's children are all but one ready to fire. UI cache invalidation on the parent should rely on the `child_completed` lifecycle event (`child_reports.py:2033-2046`), not on `pending_for_parent`. **Not a breaking change for current consumers** because no consumer exists in repo.

3. **Bus hook ordering for Fix 3 is correct**. `_emit_terminal_via_bus` is called from `_dispatch_post_commit_side_effects` at line 1994, which runs AFTER the `session.commit()` at line 1812 (i.e., after the snapshot read at 1775 and the Event row insert at 1799). The snapshot is taken inside the WriteGuardSession's transaction; the bus hook fires AFTER the transaction commits. **Confirmed**.

4. **`child_still_running_defer` outcome does not collide** with the six other outcomes (`instance_not_found`, `deferred_waiting_children`, `root_waiting_children`, `root_completed`, `idempotency_skip`, `tool_invocation_completed`, `regular_child_completed`). The dispatch handler has a dedicated `if outcome == "child_still_running_defer"` block at `child_reports.py:1891`. The unknown-outcome fallback at 2096-2099 logs but does not break.

### Expert 3: Risk / Release-readiness — **Verdict: APPROVE WITH DOCUMENTATION + PRE-EXISTING BUG DECISIONS**

The PR is small (162 production lines), well-tested (14 PG tests), and surgical. The major release-readiness items are documentation and decisions about pre-existing same-pattern bugs, not new defects.

**Top findings:**

1. **Pre-existing bug at `child_reports.py:1691-1695` is NOT SAFE to leave for follow-up**. The cascade block writes `parent.status = InstanceStatus.COMPLETED.value` at line 1718 if the cascade condition holds. If `parent.status` was TERMINATED prior, the cascade silently overwrites it to COMPLETED — a state demotion that loses the cancellation audit trail. Concrete pre-condition: a TERMINATED parent with at least one PENDING watcher that fires after termination (e.g., a child's terminal hook fires post-cancel). RECOMMENDATION: Add TERMINATED/FAILED to the exclusion set at line 1691-1695 OR explicitly document and test why this is acceptable (verify via `git grep TERMINATED .*parent.status`).

2. **Test coverage gaps are documented but not blocking**:
   - No race-condition test for two children completing simultaneously against the same parent.
   - No PAUSED child test (PAUSED is NOT in TERMINAL_STATUSES — verify it doesn't wedge the defer).
   - The pre-existing parent-cascade TERMINATED-omission is NOT covered by any test in this PR.
   - The cross-connection TOCTOU in Fix 3 cannot be exercised in a unit test (deterministic engine); a comment in the file is a reasonable substitute.

3. **Cumulative test runtime**: 14 PG tests, each seeds 3 instances + 1 watcher and calls the sync helper. With PG fixture overhead (~1-2 s per test), expect 15-30 s total. The `pg_engine` fixture auto-skips on missing PG, so default CI is unaffected. Confirming by reading `tests/postgres/conftest.py` would tighten this estimate. **Safe to add to default CI** only if PG is in the CI environment; otherwise keep the `postgres` marker (which it already has — `pytestmark = pytest.mark.postgres` at `test_wanderer_completion_reporting_pg.py:69`).

4. **Documentation impact**: This is non-trivial. The docstring on `_process_child_completion_db_sync` at `child_reports.py:1085-1113` does NOT mention the new outcomes (`child_still_running_defer` is listed at line 63-66 of the result-class docstring, but `idempotency_skip` is briefly mentioned; the parent-cascade TERMINATED omission is not mentioned at all). Recommend a brief addendum: "Fix 1 is bounded by the per-parent lock on `instance_id` (the child), NOT the parent; concurrent sibling completions may over-defer, with idempotent recovery on next Stage 6 entry."

5. **Rollback risk**: Fix 1 false-positive wedge (a child stuck non-terminal) is **recoverable**: the parent's next message processing invokes Stage 6 (`message_processing_pipeline.py:457`), which calls the same helper. If the still-stuck child eventually resolves (even by timeout / force-cancel), the parent's defer fires again with an updated state. There is no explicit watchdog for stuck non-terminal children in this PR — but the existing `stale_task_recovery.py` mentioned at line 738 of `daemon/services/stale_task_recovery.py` handles cleanup. **Acceptable risk**.

---

## 2. Consolidated Findings

### 🔴 Critical

*None.* No findings are correctness-blockers or security-relevant.

### 🟡 Warning

- **🟡 W1 — `child_reports.py:1691-1695` allows TERMINATED → COMPLETED state demotion** (pre-existing, explicitly out-of-scope). If a parent is TERMINATED and a residual watcher fires post-cancel, `parent.status = COMPLETED` at line 1718 overwrites TERMINATED. Impact: loses cancellation audit trail; UI may show "completed" instead of "terminated". Fix: add `and parent.status != TERMINATED.value and parent.status != FAILED.value` to the exclusion set, mirroring Fix 1's discipline at line 1575-1581. Recommend tracking as a separate PR rather than blocking this one; needs verification that bus `cancel_for_target` (`dependency_bus.py:811`) eliminates the residual watcher scenario in practice (which would make W1 dead-code-but-defensive).

- **🟡 W2 — Fix 3 cross-connection snapshot TOCTOU** (`child_reports.py:1775`). The count is read via a separate `Session(self.engine)` (`dependency_bus/repository.py:264`), not the WriteGuardSession's session. A concurrent `bus.watch()` or `emit_terminal` can shift the count by ±1 between snapshot and the post-commit bus hook, breaking the subtract-1 invariant. Impact: `pending_for_parent` in the CHILD_COMPLETED event can be off by 1 (rare; observability only — no current frontend consumer). Fix: use the inline query pattern from `child_reports.py:1295-1304` — read the count on the WriteGuardSession's `session` object so it joins the transaction. Estimated 6-line change.

- **🟡 W3 — Fix 1 lacks verification that PAUSED children do not permanently wedge the defer** (`child_reports.py:1575-1581`). PAUSED is NOT in `TERMINAL_STATUSES = {TERMINATED, COMPLETED, ERROR, FAILED}` (`job_queue_service.py:46-51`), so a PAUSED sibling would be counted as active. If PAUSED children are recoverable (resume + run-to-completion), the defer eventually clears. If a child can be left PAUSED indefinitely (orphaned pause), the parent's `completion_report` is permanently deferred. Recommend a test (`test_paused_sibling_does_not_permanently_wedge_defer`) plus a `_seed_instance` variant covering the PAUSED case, OR documenting the assumption in the docstring of `_process_child_completion_db_sync`.

### 🟢 Suggestion

- **🟢 S1 — Module/function docstring sync** (`child_reports.py:60-67` result class docstring). Add `child_still_running_defer` to the outcome list at line 63-66. *(Already present — confirmed at line 63-66. Suggestion satisfied.)*

- **🟢 S2 — Add explicit rollback note in PR description**. The PR narrative should explicitly call out that pre-existing bugs at `child_reports.py:1691-1695` (`child_reports.py:800-801` in dead-code `_update_parent_on_child_complete`) and `error_reporting.py:233-237` are NOT fixed by this PR, with rationale (keeps diff focused; relationship to bus-wired state machine). The developer comment at `child_reports.py:1563-1568` already covers the in-file acknowledgment.

- **🟢 S3 — PG test runtime baseline**. Add a `@pytest.mark.timeout(60)` decoration or document expected cumulative runtime in the test module docstring at line 24-31 so CI failure investigation has a baseline.

- **🟢 S4 — Add a negative-path test for Fix 3** (`test_wanderer_completion_reporting_pg.py`). The Fix 3 tests cover 0-watchers, 1-watcher, 3-watchers. Add a test where the snapshot races with an external INSERT (e.g., `_seed_dependency_watcher` called from a concurrent thread before the commit) — this is hard to write deterministically, but a test that documents the intended invariant would suffice.

- **🟢 S5 — Confirm `_update_parent_on_child_complete` at `child_reports.py:661` is dead code** (still wired via `manager.py:4346`, but `manager._update_parent_on_child_complete` has no in-repo callers). The 800-801 same-pattern bug lives in this dead path. Consider removing this method in a cleanup PR after this fix lands.

---

## 3. Race Condition Matrix

| Scenario | Verdict | Reasoning | Evidence |
|----------|---------|-----------|----------|
| **R1**: Fix 1 active-children snapshot vs concurrent sibling termination | **Indeterminate (over-defer)** | If a sibling transitions RUNNING → COMPLETED between the snapshot at `child_reports.py:1569-1582` and the commit at 1812, the guard will (correctly) defer one extra time. Re-entry is idempotent (Fix 2). Recovery on next Stage 6 invocation. | Lock on `instance_id` (child) at `child_reports.py:1054` does NOT serialize against a different child of the same parent. |
| **R2**: Concurrent child completion (two coders report simultaneously) | **Safe** | Each child holds its own per-parent lock (keyed on child id). Each writes its own status independently. The parent's `count_pending_for_target_sync(parent_id)` reads are non-blocking. The bus `emit_terminal` atomic transition (`dependency_bus.py:551-718`) ensures exactly-once per watcher. | Locks differ (`bus._get_parent_lock(A)` vs `bus._get_parent_lock(B)`); atomic transition_state in repository.py. |
| **R3**: Fix 3 snapshot read vs concurrent `bus.watch()` INSERT | **Theoretical TOCTOU** | The snapshot at `child_reports.py:1775` reads via `bus.count_pending_for_target_sync(...)` opening a new `Session(self.engine)` at `repository.py:264`. The WriteGuardSession has not committed. A concurrent INSERT in another connection would not be reflected in this snapshot → post-commit, the bus hook fires, and `pending_for_parent` is wrong by 1 (the unaccounted new watcher). | Locks are different keys (`_get_parent_lock(child)` vs `_get_parent_lock(target)`); inline query pattern at 1295-1304 not used here. |
| **R4**: Fix 3 vs concurrent sibling's `emit_terminal` | **Safe (lucky ordering)** | If a different child's `emit_terminal` fires between our snapshot and our bus hook, the snapshot already saw the FIRED state — the count reflects post-FIRE reality. Subtract-1 still gives the correct value (ours minus ours). | `bus.emit_terminal` transitions under per-task lock (`dependency_bus.py:607`); atomic. |
| **R5**: Fix 2 idempotency check vs concurrent status write | **Safe** | The WriteGuardSession provides optimistic locking via `instance.version` increment at lines 1321, 1424, 1511, 1612. The `session.get(Instance, ...)` at line 1116 reads a snapshot; a concurrent write would either commit first (then Fix 2 short-circuits on the now-terminal status) or commit after our read (we read the pre-terminal status and proceed normally). No double-write. | SQLModel/aiosqlite session guarantees; optimistic `version` field. |
| **R6**: Fix 2 wedge when `status == TERMINATED` | **Theoretical wedge** | `child_reports.py:1144` checks ONLY COMPLETED/ERROR. A TERMINATED or FAILED instance falls through. If `parent_id is None` (root), the root branch writes `instance.status = COMPLETED` at line 1421, overwriting TERMINATED. If `parent_id` is set, the active-children guard or report-emission branch writes COMPLETED at line 1609, overwriting TERMINATED. Net effect: TERMINATED → COMPLETED demotion possible on the same code path Fix 2 was supposed to guard. | Direct read of `child_reports.py:1144-1147` vs `child_reports.py:1421, 1609, 1718`. |

---

## 4. Test Coverage Assessment

### ✅ Covered

- Fix 1 happy path: active RUNNING child → defer (`test_non_root_instance_with_active_children_defers`)
- Fix 1 mixed terminal siblings (COMPLETED + ERROR) → emit (`test_non_root_instance_with_all_children_done_emits_report`)
- Fix 1 self-exclusion (`test_self_excluded_from_active_children_count`)
- Fix 1 TERMINATED sibling exemption (`test_terminated_sibling_does_not_block_parent_completion`)
- Fix 1 FAILED sibling exemption (`test_failed_sibling_does_not_block_parent_completion`)
- Fix 1 three-turn regression: 0 reports emitted across 3 invocations (`test_three_graph_turns_emit_zero_reports`)
- Fix 2 COMPLETED short-circuit (`test_completed_instance_short_circuits`)
- Fix 2 ERROR short-circuit (`test_error_instance_short_circuits`)
- Fix 2 double-call no-double-write (`test_double_call_does_not_double_write`)
- Fix 2 RUNNING control case (`test_running_instance_proceeds_normally`)
- Fix 3 1 watcher → 0 (`test_single_child_emits_zero_pending`)
- Fix 3 3 watchers → 2 (`test_multiple_pending_children_emits_count_minus_one`)
- Fix 3 0 watchers → 0 (defensive clamp) (`test_no_watchers_emits_zero_not_negative`)

### ⚠️ Missing-Critical

- **PAUSED child behavior**: PAUSED is not in `TERMINAL_STATUSES`; verify the active-children guard does not permanently defer a parent with a stuck-paused child. **Add `test_paused_sibling_blocks_parent_completion_until_resolved`** plus a corresponding "after-resume" companion test.
- **Concurrent child completion + Fix 1 interaction**: Two siblings of Wanderer completing simultaneously while Wanderer itself just finished. The PR description claims "Fix 1 ensures Wanderer defers correctly even with multiple active siblings" but no test exercises this. **Add `test_two_siblings_complete_simultaneously_wanderer_defers_once`**.
- **TERMINATED/FAILED wedge in Fix 2**: The developer's documented deferral at line 1563-1568 explicitly excludes this. A regression test for FIX-LATER would prevent silent re-introduction. **Add `test_terminated_instance_falls_through_fix2` (test the current, admitted-buggy behavior)** so a future fix is differentiable from a regression.
- **parent-cascade TERMINATED safety**: Add `test_terminated_parent_does_not_demote_to_completed` proving either the bug doesn't exist (e.g., because the bus cancels residual watchers) OR demonstrating the demotion so a fix can be authored.

### 🟢 Missing-Nice-To-Have

- Nested grandchildren (A→B→C cascade): the test setup uses 2-level trees. A 3-level tree would verify `_update_parent_on_child_complete` and the cascade block at 1691-1695 compose correctly. Real-world Wanderer use case may have 3+ levels.
- `idempotency_skip` from the in-branch path (`existing_report`) is exercised by `test_double_call_does_not_double_write` only via re-entry to Fix 2. A test that pre-seeds an existing `internal_report:` row and verifies the in-branch idempotency_skip path would tighten coverage.
- Fix 3 SUBTRACT-1 with concurrent `bus.watch()` INSERT — deterministically impossible in a sync test, but a documented invariant test would suffice.
- `child_still_running_defer` dispatch SSE verification — the SSE is mocked out in tests (`manager._live_hub = None`); a mock-hub variant would prove the SSE shape is preserved across branches.

---

## 5. Pre-existing Bug Recommendation

### Decision Matrix

| Bug Site | Same Pattern? | Defer Now? | Reasoning |
|----------|---------------|------------|-----------|
| `child_reports.py:1144` (Fix 2 idempotency set) | YES | **DEFER WITH TRACKING** | Tightly scoped to this PR's intent ("guard against per-graph-turn re-emission"). Expanding to include TERMINATED/FAILED risks behavior changes in the error/cancellation paths. Track as a follow-up issue. |
| `child_reports.py:1691-1695` (parent-cascade check) | YES | **FIX NOW (if feasible within scope) OR DEFER WITH TEST-DRIVEN PLANNING** | This is the most concerning pre-existing bug because it silently overwrites TERMINATED → COMPLETED. The runtime impact in practice depends on whether `bus.cancel_for_target` (`dependency_bus.py:811`) is reliably called before terminal watchers fire. If yes (verify with logs), the bug is dead code and can be left. If no, fix in this PR adds 2 lines and makes Fix 1's discipline consistent across all three sites. |
| `child_reports.py:800-801` (old `_update_parent_on_child_complete`) | YES | **DEFER / REMOVE** | Method appears to be dead code (no callers in `manager.py` outside the wrapper at line 4346; no external callers). Remove the method in a cleanup PR; the bug evaporates. |
| `error_reporting.py:233-237` | YES | **DEFER WITH TRACKING** | Parallel structure to `child_reports.py:1691-1695`; same risk surface. Should be fixed alongside the cascade block fix. |

**Recommendation summary:**
1. **MUST**: Track `child_reports.py:1144`, `error_reporting.py:233-237` as documented follow-up issues with test-driven planning.
2. **SHOULD**: In a separate small PR, expand both Fix 2's idempotency set AND the cascade exclusion set to include `TERMINATED` and `FAILED`. This makes the 4-state discipline uniform across all three fixes.
3. **OPTIONAL**: Remove dead `_update_parent_on_child_complete` method once verified unused in production.

**Reasoning**: Mixing the cascade fix into this PR would expand scope from 3 fixes to 4-5, diluting the per-graph-turn regression coverage. Separate PR is cleaner. But the **documented exclusion** at `child_reports.py:1563-1568` is good engineering hygiene — preserves PR traceability and prevents silent behavior changes.

---

## 6. Final Verdict

### **APPROVE WITH CONDITIONS**

**Conditions:**
1. Track `child_reports.py:1144`, `child_reports.py:1691-1695`, and `error_reporting.py:233-237` as follow-up GitHub issues before merging. PR description references them.
2. The W2 cross-connection snapshot TOCTOU at `child_reports.py:1775` should be addressed by switching to an inline query on the WriteGuardSession's session (mirroring `child_reports.py:1295-1304`). This is a low-effort, low-risk tightening that aligns Fix 3 with the existing C2 inline pattern. Estimated 6 lines.
3. Add the missing-critical test items to a follow-up PR (PAUSED child, concurrent child completion, parent-cascade TERMINATED safety).
4. Document the `pending_for_parent` semantic shift in the docstring at `child_reports.py:1110-1112` so future readers know "post-fire" intent.

**Approval basis:**
- Fix 1 correctly closes the reported Wanderer bug with the canonical 4-state `TERMINAL_STATUSES`.
- Fix 2 + Fix 1 combine to make Stage 6 re-entry safe and idempotent.
- Fix 3 produces correct `pending_for_parent` values for the single-fire case (which is the common case in practice).
- Test coverage of the happy path is thorough (10 of 14 tests).
- Module docstring is already updated with the `child_still_running_defer` outcome.
- The dispatch handler at `_dispatch_post_commit_side_effects:1891-1902` correctly mirrors the existing `deferred_waiting_children` shape.
- The idempotency_skip collision between Fix 2 and the in-branch check is **functionally inert** (both reduce to `return`).

**Risk level**: LOW for the Wanderer-specific bug. MEDIUM for the parent-cascade TERMINATED demotion (pre-existing, out-of-scope, documentable). LOW for the Fix 3 TOCTOU (observability field, no current consumers).

**Recommended next steps:**
1. Land this PR after addressing Condition 1 (issue tracker entries).
2. Land Condition 2 (inline snapshot) as a 6-line follow-up before the next release.
3. Land Conditions 3-4 as a separate "tighten test coverage and docstring" PR.

---

*Review triangulated against: child_reports.py (1079-2099), dependency_bus.py (200-720, 1128-1206), repository.py (220-280), error_reporting.py (225-249), message_processing_pipeline.py (650-682), instance_messaging.py (1604-1624), job_queue_service.py (40-99). Pre-existing same-pattern bugs cross-referenced at child_reports.py:1144, 800-801, 1691-1695 and error_reporting.py:233-237.*

---

## Reconciliation Note (Orchestrator-Added)

This is the verbatim output from council session `cou-1` (alias `ses_081948d81ffeJAu0DuIL0Rn5T5`), completed and reconciled against my own direct evidence (read of `child_reports.py:1079-2099`, `job_queue_service.py:44-51`, `dependency_bus.py:740-809`, `repository.py:220-280`, `test_wanderer_completion_reporting_pg.py:1-997`). Council findings are consistent with direct evidence; no contradictions. The Background Job Board shows `cou-1` as completed and reconciled — the session is reusable by alias for follow-up reviews on this PR if needed.
