# Review Report: Virtual Job — Tool-Surface Completeness & Root Scoping

| Field | Value |
|---|---|
| **Date** | 2026-06-27 |
| **Plan** | `docs/plans/virtual-job-tool-completeness.md` |
| **Reviewer** | Reviewer agent (Deep-Review council + 2 standard sessions) |
| **Verdict** | 🟡 **NEEDS CHANGES** (1 blocker, 4 warnings, 6 suggestions) |
| **Parent feature** | D14 virtual-job-management-surface (MERGED) |

---

## Verdict Summary

**🟡 NEEDS CHANGES** — The plan is fundamentally sound, well-researched, and its premises
are accurate against the actual code. However, there is **1 blocking issue** (the
`root_only=True` default silently degrades the frontend "All Work" view) and **4
warnings** that should be addressed before implementation. None of the findings reject
the design; they refine it.

### Tracks
| Track | Verdict | Note |
|---|---|---|
| P-A (root scoping) | 🟡 NEEDS CHANGES | Default-ON breaks frontend; pagination interaction; N+1 cost |
| P-B (job_continue) | ✅ SOUND (with edits) | Core rewrite correct; minor `deleted_at` / cross-kind gaps |
| P-D (honest errors) | ✅ SOUND (cosmetic) | Guard logic correct; message-prefix consistency |
| P-C (deferred) | ✅ Deferral sound | Acknowledged 2-rows-per-job; one minor P-B footgun |

---

## Scope Reviewed

- Plan document: `docs/plans/virtual-job-tool-completeness.md` (333 lines, all sections)
- Code verified against plan claims: `daemon/tools/job_queue.py`, `daemon/services/work_resolver.py`, `daemon/services/job_queue_service.py`, `daemon/services/work_status.py`, `daemon/services/child_reports.py`, `daemon/routers/work.py`, `frontend/src/app/`

### Premise Verification: ✅ ACCURATE
All plan line references confirmed: `job_continue:595`, `list_work:420`, `_task_to_record:573`,
`_job_to_record:599`, `_lookup_instance:628`, `get_work:747`, flag at `job_queue_service.py:148/229`.
The §1.1 audit table matches reality. The plan author did their homework.

---

## Sessions Used

| Session | Type | Focus |
|---|---|---|
| `review-deep` | 🔴 council | P-A + P-B correctness, edge cases |
| `review-test` | standard | §6 test plan coverage |
| `review-ux` | standard | P-D, cross-cutting default, deferral |

---

## Findings

### 🔴 Critical

#### 🔴 C1 — `root_only=True` default silently degrades frontend "All Work" view
**Area:** P-A / cross-cutting | **Plan §:** 2.1, 5, 8.2

The plan defaults `root_only=True` on `GET /api/work` and `job_list`. The frontend "All Work"
view is the dominant HTTP caller and its NAME promises all work:

- `frontend/src/app/services/work.service.ts:25` — `API_BASE = '/api/work'`
- `frontend/src/app/services/work.service.ts:49` — `getWork(filters?: WorkFilters)` has **no `root_only`** parameter in `WorkFilters`
- `frontend/src/app/pages/jobs/jobs.component.ts:459` — `loadWorks()` calls `workService.getWork({project_id, status})` with **no override**

With `root_only=True` as the router default and no frontend parameter, the "All Work" view will
**silently drop child-instance rows** with no UI affordance to recover them. This contradicts the
view's name and operator expectations.

**Fix (pick one):**
1. **Best** — Frontend explicitly opts out: add `root_only` to `WorkFilters` + `WorkService.getWork()`, have `loadWorks()` pass `root_only=false`. HTTP default stays `True` (cheap for unparameterized callers); the All-Work UI explicitly includes children.
2. **Alternative** — Rename `root_only` → `include_children` (default `false`). Neutral default semantics; frontend opts in via `include_children=true`.
3. **Minimum** — Add a "Show child work" toggle to the All Work toolbar (default off), wired to the new param.

> Note: The MCP `job_list` consumer (the jober agent) genuinely wants `root_only=True` — the
> asymmetry between MCP and HTTP defaults is justified by their different consumers.

---

### 🟡 Warnings

#### 🟡 W1 — Pagination drift: `root_only` + client-side `offset`/`limit`
**Area:** P-A | **Plan §:** 2.1, 3.3 | **Evidence:** `daemon/tools/job_queue.py:443-453`

`job_list` applies `offset`/`limit` **client-side** after `list_work` returns (`:448`):
`page = records[offset : offset + limit]`. The plan places `root_only` filtering **inside**
`list_work` (post-fetch, before return). This is correct for `list_work` itself, BUT `job_list`'s
pagination assumes the returned `records` is the full set to page over.

If `list_work(root_only=True)` already excluded children, the returned list is the root-scoped set
— and pagination works on that. **This is actually fine** *if* `root_only` filtering happens inside
`list_work` (as the plan specifies). The concern would only arise if filtering were re-applied at
the `job_list` layer. **Confirm the filtering stays inside `list_work`, not duplicated in `job_list`.**

**Action:** Add a test asserting `job_list(limit=20)` returns ≤20 root-scoped rows (not
20 minus children). The plan's §6 has no such test.

#### 🟡 W2 — N+1 `_lookup_instance` cost on the Task side
**Area:** P-A | **Plan §:** 2.1, 3.1 | **Evidence:** `work_resolver.py:551-556, 586`

The plan claims the Task-side root filter is "cheap — the lookup is already paid for." This is
**partially misleading**: `_task_to_record` (`:586`) calls `_lookup_instance(task.instance_id)`
per row, which is `self._instance_repo.get(instance_id)` (`:644`) — a **separate DB round-trip
per Task row**. The lookup IS already done for `agent_id`/`project_id`, so adding the `parent_id`
check adds zero NEW cost. The plan's claim is correct **for the marginal cost of the filter itself**,
but the existing N+1 pattern remains.

This is not a regression (the N+1 exists today), but for large work sets the Task-side branch is
O(n) DB calls. The JobItem side's batched `IN (...)` lookup is the right pattern; consider
applying the same batching to the Task side (pre-fetch all instance rows for the page's
`instance_id`s in one `SELECT ... WHERE instance_id IN (...)`).

**Action:** Optional optimization — batch the Task-side `_lookup_instance` calls. At minimum,
document that the existing per-row lookup is the cost ceiling, not the filter addition.

#### 🟡 W3 — `job_continue` `deleted_at` guard: two-step lookup race
**Area:** P-B | **Plan §:** 2.2 (point 4) | **Evidence:** `job_queue_service.py:747-784`

The plan's recommended approach: resolve via `get_work` → if `kind=="job"`, do a second
`get_job` lookup to read `deleted_at`. This creates a TOCTOU window: between `get_work` (returns
a job record) and `get_job` (reads `deleted_at`), the job could be soft-deleted. In that window
`get_job` returns the row with `deleted_at` set → correctly rejected. The reverse race
(`get_work` returns a record, then the row is deleted before `get_job`) would yield `get_job`
returning `None` → the plan doesn't specify the response.

**Fix:** When `kind=="job"` and the follow-up `get_job` returns `None`, treat it as a soft-deleted
(reject with "Job has been deleted") rather than passing through to `enqueue_message`. Document
this in §2.2.

#### 🟡 W4 — Cross-kind `job_continue` bypasses soft-delete intent
**Area:** P-B | **Plan §:** 2.2, risk 10.2 | **Evidence:** `child_reports.py:649-656`

After P-B, `job_continue` accepts BOTH a JobItem work_id AND a Task (root turn) work_id. If a job
is soft-deleted (`deleted_at` set on JobItem) but the root turn Task still exists (terminal), the
jober can continue via the **task work_id** — which skips the `deleted_at` check entirely (tasks
have no soft-delete). The plan acknowledges this in risk §10.2 as "low risk," but it's a real
semantic hole: `job_delete` no longer reliably prevents continuation.

**Fix options:**
- **Accept + document** (plan's stance) — acceptable given `job_delete` is rare and `enqueue_message`
  re-drives a live instance.
- **Tighter** — when `kind=="turn"`, additionally check whether a JobItem exists for the same
  `instance_id` with `deleted_at` set, and reject. Higher cost; only worth it if `job_delete`
  semantics are load-bearing.

**Recommendation:** Accept per the plan, but add a test (#8 should be extended) documenting that
a soft-deleted job's root turn CAN be continued (making the behavior explicit, not accidental).

---

### 🟢 Suggestions

#### 🟢 S1 — Plan line-reference corrections (3 minor)
The plan cites line numbers slightly off from the merged state:
- `:551-556` (Task loop) → actual `:545-556`
- `_lookup_instance` call site cited as `:644` → actual `:586`
- `_lookup_instance` body cited as `:628-637` → actual `:628-659`

Fix for plan-doc accuracy; no functional impact.

#### 🟢 S2 — Reports are parent-bound, not child-bound (clarify the premise)
**Evidence:** `child_reports.py:649-656, 1519-1526` — `report_task.instance_id = instance.parent_id`.

PROCESS_REPORT/SEND_REPORT task rows are created with `instance_id = child.parent_id` — i.e., the
**report task lives on the PARENT/root instance**, not the child. So `root_only=True` (filter on
non-null `parent_id`) does **NOT exclude report tasks**. Only child-instance `process_message`
(turn) tasks are excluded.

**Implication:** The plan's §1.2 framing ("those children emit their own process_message (turn)
and process_report/send_report (report) tasks... every one of those child rows is returned") is
imprecise. The **turn** rows are child-bound (excluded by root_only ✅); the **report** rows are
parent-bound (kept by root_only ✅). This is actually the *correct* outcome, but the plan should
state it explicitly so future readers don't assume reports are dropped.

**Action:** Add a one-line note to §2.1: *"Reports (process_report/send_report) are bound to the
parent instance (child_reports.py), so root_only does not exclude them; only child turns are filtered."*

#### 🟢 S3 — `job_continue` terminal-status equivalence is SOUND
**Evidence:** `work_status.py:84-86` vs `watcher_models.py:13`.

`_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "dead_letter"}` (canonical) ==
`ALL_TERMINAL_STATES = ["completed", "failed", "cancelled", "dead_letter"]` (JobItem). The plan's
switch from JobItem `TERMINAL_STATES` to `work_status.is_terminal(record.status)` is **exactly
equivalent** — no semantic drift. ✅ No action needed; documenting for confidence.

#### 🟢 S4 — P-D message prefix consistency
**Plan §:** 2.4 | **Evidence:** `job_queue.py:557, 570, 583` use `"ERROR: "` prefix; `job_continue`
returns a dict `{"error": ...}`.

The proposed `"Operation not applicable: ..."` omits the `"ERROR: "` prefix. Functionally safe
(skill doc only parses `[JOB_EVENT]` notifications, not tool return strings — confirmed at
`skill.md:91-108, 142-187`). For consistency, either add `"ERROR: "` or document that "not
applicable" is intentionally a *classification* not an *error*.

#### 🟢 S5 — P-C deferral is sound; one minor P-B footgun
**Plan §:** 2.3, 7, 8.4

After P-B, a jober could `job_continue` a root-turn work_id, producing a NEW turn Task (3 visible
rows: old JobItem + old turn + new turn). P-C ("watch the root turn") was designed to collapse this.
The deferral is sound, but P-B *enables* the footgun. Mitigation: the §3.5 skill-doc update should
note that `job_continue`'s `old_job_id` may be either a JobItem or a Task work_id, and that
continuing a Task produces a new turn (P-C territory).

#### 🟢 S6 — Kill-switch completeness: render §2.4 as explicit `if/else`
**Plan §:** 2.4, 5. The plan's code comment says "only on the flag-ON path" but doesn't spell out
the `else` branch. Render the §2.4 snippet as a full `if job_service.use_virtual_job_resolver: ...`
guard so the kill-switch is a literal code branch, not an implicit fall-through. Definition of Done
§9 already requires this; making the snippet explicit closes the gap.

---

## Test Plan Assessment (§6)

**Verdict: 🟡 NEEDS ADDITIONS** — 5 must-add tests to close high-risk gaps.

### Gap closure (test #6 — the D14 test #9 gap): ✅ ADEQUATE with one addition
Test #6 closes the round-trip gap. **Add:** assert the returned `instance_id` matches the original
root instance (proving it didn't re-drive a child). Without this, a bug where `job_continue` re-drives
a child instance would pass the test.

### Must-add tests (5)
1. **`test_list_work_root_only_default_is_true`** — assert calling `list_work()` with NO `root_only`
   arg excludes children (the default-ON behavioral change). Critical — no current test pins the default.
2. **`test_job_list_pagination_after_root_filter`** — `job_list(limit=20)` returns ≤20 root-scoped rows;
   a page doesn't silently shrink. (Addresses W1.)
3. **`test_root_only_keeps_reports`** — a `process_report` task on the parent instance is NOT excluded
   by `root_only=True` (parent-bound). (Addresses S2 — pins the reports-are-parent-bound invariant.)
4. **`test_job_continue_task_work_id_returns_original_instance`** — the returned `instance_id` ==
   original root instance (not a child). (Strengthens test #6.)
5. **`test_job_retry_delete_restore_each_task_kind_message`** — each of the 3 tools gets its own
   explicit "not applicable for task-type work" assertion (test #5 says "same for" but isn't explicit).

### Recommended additions (4)
6. `test_job_continue_kill_switch_uses_get_job` — flag OFF → `job_continue` uses legacy `get_job` path.
7. `test_job_continue_terminated_instance_rejected_from_task_work_id` — continuing a task work_id whose
   instance is TERMINATED/PAUSED is rejected (not silently enqueued).
8. `test_work_endpoint_root_only_false_returns_children` — explicit HTTP `?root_only=false` (escape hatch).
9. `test_job_continue_soft_deleted_job_via_task_work_id_allowed` — document the W4 behavior explicitly
   (soft-deleted job's root turn CAN be continued).

---

## Recommendations (ranked)

1. **🔴 [BLOCKER] Fix frontend "All Work" silent drop (C1)** — the `root_only=True` default on
   `GET /api/work` must not silently degrade the All-Work view. Frontend opts out or rename to
   `include_children`.
2. **🟡 Confirm root-filtering lives INSIDE `list_work`, not duplicated in `job_list` (W1)** — add
   pagination-after-filter test.
3. **🟡 Specify the `get_job`-returns-None response in §2.2 (W3)** — treat as "deleted," not pass-through.
4. **🟡 Document the cross-kind soft-delete bypass (W4)** — add test making it explicit.
5. **🟢 Fix the 3 plan line references (S1)** — accuracy for future readers.
6. **🟢 Clarify reports-are-parent-bound in §2.1 (S2)** — prevents a false "reports get dropped" assumption.
7. **🟢 Add the 5 must-add tests** to §6.

---

## Overall

This is a **well-researched, narrowly-scoped hardening plan** that correctly identifies real gaps in
the D14 surface. The premises are verified-accurate against the merged code. The design is sound.
The blocker (C1) is a default-value UX issue, not a correctness flaw — easily fixed by having the
frontend opt out or renaming the param. With the blocker resolved and the 4 warnings documented/tested,
this plan is **ready to implement**.
