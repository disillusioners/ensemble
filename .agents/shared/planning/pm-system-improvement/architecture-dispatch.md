# Architecture Recommendation: PM→Leader Dispatch with Instance Reuse

**Date:** 2026-08-13
**Architect Instance:** architect (controller)
**Worker Instances:** architect-worker-pm-dispatch-lifecycle (c1ed2eb6), architect-worker-pm-security-tooling (472179e3)
**Status:** Complete — 2/2 worker reports aggregated
**Confidence:** High — instance reuse is a first-class system capability with explicit infrastructure support (revive-fix 2026-07-01)

> **Note:** This is a predecessor document (architect deep-review). The canonical KV schema was updated in the reviewer pass (W5): the key is now `"pm_leader_instances"` (JSON array) per `plan-overview.md`, not `"pm_dispatch_registry"` as referenced in some sections below. Where this document says `"pm_dispatch_registry"`, read `"pm_leader_instances"` with the W5 array schema.

---

## Executive Summary

The ensemble infrastructure **already supports** the PM→leader dispatch pattern with instance reuse. No system-level code changes are required. The implementation is entirely **agent-level**: PM's `meta.json` tool configuration, `team_members` list, Cardinal Rules, and workflow prompt. The existing instance lifecycle (spawn → send_message → child completion → PROCESS_REPORT → revival) handles every phase of the pattern, including reusing a COMPLETED leader instance for follow-up work via the `COMPLETED → RUNNING` revive-fix in `_prepare_enqueued_message`.

The architecture has three pillars:
1. **Dispatch enablement** — PM gains `spawn_instance`, `send_message`, `get_instance_info`, `list_instances` via the `instance` tool category
2. **Instance tracking** — PM uses `shared_meta_kv` to maintain a `task_id → leader_instance_id` mapping
3. **Instance reuse** — PM calls `send_message` on a COMPLETED leader instance; the system revives it `COMPLETED → RUNNING` and re-registers the dependency watcher via the permanent `instances.parent_id` field

---

## 1. Instance Reuse — The Core Finding ✅

**Instance reuse is fully supported by the existing system.** This is the single most important finding of this investigation.

### How It Works

After a leader completes a task and reports back, its `instance` row persists in the DB with `status=COMPLETED`. The `instance_hierarchy` working-set row is deleted (cleanup), but the `instances.parent_id` field is **never cleared**. When PM calls `send_message(completed_leader_id, follow_up_task)`:

1. **Status guard passes** — `send_message` (`daemon/tools/instance.py:1661-1668`) only rejects `TERMINATED` and `ERROR`. `COMPLETED` is explicitly allowed.
2. **Queue guard passes** — After completion, the leader's message queue is drained, so the `pending_count > 0 || processing_count > 0` check at line 1670 returns clean.
3. **Revive-fix activates** — `_prepare_enqueued_message` (`daemon/services/instance_messaging.py:1486-1510`) detects `status == COMPLETED` and transitions it to `RUNNING`. The comment in the code states: *"the checkpoint, message history, and LangGraph thread all persist in the DB and reload on the next graph.astream."*
4. **Watcher re-registers** — `_register_child_completion_watcher` (`daemon/tools/instance.py:484`) checks `target_instance.parent_id` from the **`instances` table** (permanent record), NOT the `instance_hierarchy` working set. Since `parent_id` is never cleared, the watcher registers correctly even after hierarchy cleanup.

### What This Means for PM

PM does not need to spawn a new leader for each message about the same task. Instead:
- **First message**: `spawn_instance("leader")` → `send_message(new_instance_id, task)` → END TURN
- **Follow-up messages**: `send_message(same_instance_id, follow_up)` → END TURN
- The leader instance retains its full conversation history and checkpoint state, so it has context continuity.

### Reuse Flow Diagram

```
PM Turn 1:
  spawn_instance("leader")           → instance B created (status=IDLE)
  send_message(B, "implement X")     → B: IDLE→RUNNING, PM enqueues, watcher registered
  shared_meta_kv(set: {task-1: {leader: B, status: in_progress}})
  END TURN                           → PM status: RUNNING→IDLE (bus gates, not WAITING_CHILDREN)

Leader B runs, spawns developer/tester (grandchildren of PM):
  ... developer implements, tester tests, reviewer reviews ...
  B terminal completion              → B: COMPLETED
                                    → ReportInjection(PM, B, report) PENDING
                                    → MessageQueue(PM, type=COMPLETION_REPORT)
                                    → Task(PROCESS_REPORT, instance_id=PM)
                                    → instance_hierarchy row for B deleted
                                    → bus.emit_terminal → PM resumes

PM Turn 2 (report received):
  PM sees report in context
  shared_meta_kv(set: {task-1: {leader: B, status: completed, summary: "..."}})
  // User says "now add feature Y to the same area"
  send_message(B, "add feature Y")   → B: COMPLETED→RUNNING (revive-fix!)
                                    → watcher re-registers (parent_id permanent)
  shared_meta_kv(set: {task-2: {leader: B, status: in_progress}})
  END TURN

Leader B runs again with full context from task-1...
```

---

## 2. meta.json Changes — Exact Configuration

### Current State (v1, non-dispatching)

```jsonc
{
  "tools": {
    "allow": [
      "explore", "project_get", "project_list", "project_search",
      "project_get_by_instance", "project_get_by_directory",
      "project_history_list", "project_history_search", "project_cn_list",
      "filesystem", "todo_view", "chart", "image", "plane"
    ],
    "deny": [
      "experience", "project_cn_add", "project_cn_remove",
      "project_history_add", "project_history_delete",
      "project_set_status", "project_update", "project_create",
      "project_delete", "project_set_tags", "project_add_tag",
      "project_remove_tag", "project_set_shortnames",
      "project_add_shortname", "project_remove_shortname",
      "project_set_metadata", "project_delete_metadata",
      "project_link", "project_unlink",
      "project_add_directory", "project_remove_directory",
      "edit_file", "write_file", "bash",
      "instance", "self", "shared_meta_kv",
      "send_message", "spawn_instance", "terminate_instance",
      "question", "mcp"
    ]
  },
  "team_members": []
}
```

### Required Changes

```diff
 "tools": {
   "allow": [
     "explore", "project_get", "project_list", "project_search",
     "project_get_by_instance", "project_get_by_directory",
     "project_history_list", "project_history_search", "project_cn_list",
-    "filesystem", "todo_view", "chart", "image", "plane"
+    "filesystem", "todo_view", "chart", "image", "plane",
+    "instance", "shared_meta_kv"
   ],
   "deny": [
     "experience", "project_cn_add", "project_cn_remove",
     "project_history_add", "project_history_delete",
     "project_set_status", "project_update", "project_create",
     "project_delete", "project_set_tags", "project_add_tag",
     "project_remove_tag", "project_set_shortnames",
     "project_add_shortname", "project_remove_shortname",
     "project_set_metadata", "project_delete_metadata",
     "project_link", "project_unlink",
     "project_add_directory", "project_remove_directory",
     "edit_file", "write_file", "bash",
-    "instance", "self", "shared_meta_kv",
-    "send_message", "spawn_instance", "terminate_instance",
-    "question", "mcp"
+    "terminate_instance", "council",
+    "self", "question", "mcp"
   ]
 },
-"team_members": [],
+"team_members": ["leader"],
```

### Rationale Per Change

| Change | Reason |
|--------|--------|
| **Add `"instance"` to allow** | Expands to 5 tools: `spawn_instance`, `send_message`, `terminate_instance`, `list_instances`, `get_instance_info`. Cleaner than 5 individual names; matches leader's pattern. |
| **Add `"shared_meta_kv"` to allow** | PM needs to track task→leader-instance mappings. The tool is fresh-read per LLM turn, ideal for state tracking. |
| **Deny `"terminate_instance"`** | PM should not destroy instances. Termination is destructive and cascades to grandchildren. If a leader needs to be stopped, PM should ask the user or leader should self-terminate. PM can still `get_instance_info` to check status. |
| **Deny `"council"`** | PM does not convene governor councils. Irrelevant capability. |
| **Keep `"self"` denied** | `self` category enables prompt self-modification (inner_soul). PM should not rewrite its own prompt. |
| **Keep `"question"` denied** | PM does not ask the user questions via the `ask_questions` mechanism — it provides advisory output. |
| **Keep `"mcp"` denied** | PM does not need external MCP integrations for strategic oversight. |
| **Keep `"bash"`, `"edit_file"`, `"write_file"` denied** | PM remains read-only on code. This is non-negotiable (Cardinal #1). |
| **Add `"leader"` to `team_members`** | The `_check_team_membership` auth gate (`daemon/tools/_auth.py:185`) checks the **caller's** `team_members`. Without `"leader"`, `spawn_instance("leader")` returns an authorization error. PM must NOT have access to spawn other agents (developer, tester, etc.) — only leader. |

### Tool Expansion Verification

The `instance` category (`@register_tool_category("instance")` in `daemon/tools/instance.py`) expands to exactly these 5 tools:

| Tool | Line | Capability | PM Needs It? |
|------|------|-----------|--------------|
| `spawn_instance` | 971 | Create new instance (gated by team_members) | ✅ Yes — spawn leaders |
| `send_message` | 1563 | Enqueue message to any instance by id | ✅ Yes — dispatch tasks + reuse |
| `terminate_instance` | 1741 | Destroy instance (destructive, cascading) | ❌ Denied — too dangerous for oversight agent |
| `list_instances` | 1762 | List active instances (read) | ✅ Yes — see what's running |
| `get_instance_info` | 1775 | Read instance metadata (read) | ✅ Yes — check leader status |

**`resolve_tool_filter` deny-wins-over-allow** is verified at `daemon/tools/instance.py:226-258`. Denying `terminate_instance` by exact name overrides the `instance` category expansion.

---

## 3. Instance Tracking via shared_meta_kv

### Why shared_meta_kv

PM needs to remember "I spawned leader instance X for task Y" across multiple turns. The options are:

| Mechanism | Pros | Cons | Verdict |
|-----------|------|------|---------|
| `shared_meta_kv` | Fresh-read per turn; shared across tree; purpose-built for cross-turn state | Write-capable (breaks pure read-only) | ✅ **Recommended** |
| Read `list_instances()` each turn | No write needed | No task→instance mapping; PM can't correlate instances to tasks | ❌ Insufficient |
| `instance_metadata` JSONB | Already on Instance row | Requires DB access PM doesn't have | ❌ No tool exposed |
| `.agents/shared/planning/` files | Human-readable | PM denies `write_file`; would need file-write tools | ❌ Scope creep |

### Recommended Data Structure

PM stores a single JSON value under one meta_key (`"pm_dispatch_registry"`):

```json
{
  "pm_dispatch_registry": {
    "task-001": {
      "leader_instance_id": "abc123...",
      "leader_instance_name": "implement-auth",
      "spawned_at": "2026-08-13T13:00:00Z",
      "status": "in_progress",
      "task_description": "Implement OAuth2 authentication flow",
      "last_report_summary": null,
      "last_report_at": null
    },
    "task-002": {
      "leader_instance_id": "def456...",
      "leader_instance_name": "fix-bug-1234",
      "spawned_at": "2026-08-13T13:15:00Z",
      "status": "completed",
      "task_description": "Fix race condition in job processor",
      "last_report_summary": "Fixed in commit abc123. All tests pass.",
      "last_report_at": "2026-08-13T13:30:00Z"
    }
  }
}
```

### Tracking Lifecycle

1. **On spawn**: PM calls `shared_meta_kv(set_kv={"pm_dispatch_registry": {task_id: {leader_instance_id, status: "in_progress", ...}}})` AFTER receiving the instance_id from `spawn_instance`.
2. **On report**: When a PROCESS_REPORT task delivers a leader's report, PM updates the entry: `status: "completed"`, `last_report_summary: <summary>`, `last_report_at: <timestamp>`.
3. **On reuse**: When PM decides to reuse a leader for a follow-up, it reads the registry to find the instance_id, updates the entry to `status: "in_progress"` with the new task description, and calls `send_message(existing_instance_id, new_task)`.
4. **On error**: If `get_instance_info` shows the leader is in ERROR status, PM marks the task `status: "failed"` and may spawn a fresh leader.

### Write-Ordering Discipline

🔴 **PM MUST write the tracking entry AFTER `spawn_instance` returns** (not before). If PM writes the KV first and is killed before spawn completes, the registry has a phantom instance_id. Writing after spawn ensures the instance_id is valid. The `enqueue_message` that follows is crash-recoverable via the durable message queue.

---

## 4. Report-Back Flow — How PM Receives Leader Reports

The report-back mechanism is fully handled by existing infrastructure. PM does not need any special configuration for this.

### The Process

1. **Leader completes** — its terminal graph turn fires `_process_child_completion_db_sync` (`daemon/services/child_reports.py:2300+`).
2. **ReportInjection created** — a durable row is inserted: `ReportInjection(parent_instance_id=PM, child_instance_id=leader, content=last_assistant_message, state=PENDING)`.
3. **PROCESS_REPORT task created** — `Task(task_type=PROCESS_REPORT, instance_id=PM, status=PENDING)` in the same DB transaction.
4. **DependencyBus emits** — `bus.emit_terminal(source_task=leader_task_id)` fires the FollowUp registered during `send_message`, which resumes PM's turn.
5. **Live drain (fast path)** — if PM has a live graph turn, `ReportInjectionSlot.claim_for_injection` (`daemon/graph.py:2566-2590`) drains PENDING report rows before the next LLM call, injecting them as HumanMessages.
6. **Fallback (crash recovery)** — if PM is not live, the `PROCESS_REPORT` task is claimed by the worker pool when PM next dispatches.

### PM's Perspective

PM experiences the report as a **new message in its conversation** — the leader's final assistant message appears as a HumanMessage in PM's next LLM turn. PM then processes it: updates the tracking registry, decides whether the task is done, and either reports to the user or dispatches follow-up work.

---

## 5. Lifecycle Concerns

### Session Hierarchy & context_key

When PM spawns a leader:
- `leader.parent_id = PM.instance_id` (permanent, never cleared)
- `leader.project_id` is auto-inherited from PM (if PM has a project context)
- `context_key` for both PM and leader resolves to the **tree-root instance_id** (walks up parent chain via `get_tree_root_id`)
- If PM is the root (spawned directly by user/ari): `context_key = PM.instance_id`
- If PM is a child (spawned by ari): `context_key` walks up to ari's instance_id

The `shared_meta_kv` is partitioned by `context_key`, so PM and all its leaders share the same KV namespace. This is **desirable** — leaders can read PM's task registry to understand their context, and PM can read any data leaders write back.

### Leader Knows It Was Spawned by PM

The leader instance has `parent_id = PM.instance_id` in the `instances` table. The leader's system prompt includes context injection (project blueprint, critical notes, shared context metadata). The leader does NOT receive explicit "you were spawned by PM" messaging — it simply sees the task message from PM via `send_message`. The `source` field on the MessageQueue row is `"internal_agent:<PM_instance_id>"`, but this is not surfaced to the leader's LLM context. This is acceptable: the leader treats the message like any user/parent message.

### PM Terminated While Leaders Running

`terminate_instance(PM)` cascades in parallel to all children via `InstanceHierarchy` rows:
- All active leader children are terminated with `terminal_reason="abandoned"` (cascading)
- Each leader's own children (developer, tester) cascade-terminate recursively
- Grandchildren are cleaned up before children (depth-first cascade)
- All `instance_hierarchy` rows are deleted

**🟡 Note**: If a leader is in `COMPLETED` status (not in `instance_hierarchy`), it is **NOT cascaded** — the hierarchy row was already deleted on completion. A COMPLETED leader lingers in the `instances` table with no active tasks. This is benign (no resource consumption) but contributes to the `max_instances=100` cumulative count.

### Leader Error or Crash

If a leader errors:
- Leader's `status` flips to `ERROR`
- PM's `send_message(leader_id, ...)` is **blocked** by the status guard at `instance.py:1661-1668`
- PM must spawn a fresh leader for retry
- The DependencyBus still emits a `child_complete` follow-up for the failed task, but the report payload reflects the error

### Interaction with Job Queue

PM→leader dispatch is **internal agent-to-agent** (no JobItem created). Per the JAFP convention (Job-as-Front-Primitive), only external entry points (user messages, source adapters) create JobItems. The `send_message` → `enqueue_message` path creates MessageQueue + Task records only. The leader's own dispatch to its children (developer, tester) follows the same internal pattern.

---

## 6. Cardinal Rule Changes

### Current Cardinals (7)

1. Read-only on code, plans, and project state
2. **No work dispatch — stand-alone** ← MUST CHANGE
3. Answer in proportion to the question
4. Evidence-cite every claim
5. Frame decisions, do not make them
6. Scope discipline
7. No secrets in output

### Proposed Cardinals (7 — same count)

1. **Read-only on code, plans, and project state.** *(unchanged)* — I never edit, write, commit, or mutate source code, plans, configurations, or project state. My output is messages and dispatch instructions only.

2. **Dispatch execution to `leader` only.** *(REPLACES #2)* — I may spawn `leader` instances to execute work. I spawn exactly the agents in my `team_members` — currently `leader` only. I never spawn `developer`, `tester`, `reviewer`, or any other specialist directly — that is `leader`'s job. I always END MY TURN after `send_message` and wait for the leader's report (no polling, no looping).

3. **Answer in proportion to the question.** *(unchanged)*

4. **Evidence-cite every claim.** *(unchanged)*

5. **Frame decisions, do not make them.** *(modified parenthetical)* — When I surface options, I list trade-offs and a recommendation; the final call is human. For tactical execution, I dispatch to `leader` per Cardinal #2.

6. **Scope discipline.** *(unchanged)*

7. **No secrets in output.** *(unchanged)*

### Guideline Changes

- **Guideline #8 (Hand-back)** — RETIRE. Current text: *"If you want this acted on, hand to `leader`."* This becomes a contradiction once PM can dispatch itself. Replace with a new Guideline #8:

  > **Dispatch vs advisory mode.** If the user asks me to act ("implement X", "fix Y"), I dispatch to `leader`. If the user asks me to assess ("what's our risk?", "where are we?"), I deliver my analysis and stop. I never both dispatch and deliver a full report in the same turn — dispatching ends my turn.

### workflow.md Changes

1. **Add Flow 5 — Dispatch & Delegation** (new flow for the PM-as-dispatcher mode):

   ```markdown
   ## Flow 5 — Dispatch & Delegation

   1. Identify the work that needs doing (from user request or strategic assessment).
   2. Check the dispatch registry (`shared_meta_kv`) for an existing leader instance
      handling this task or a related task.
   3. If a suitable leader exists and is not in ERROR state:
      - Reuse it: `send_message(existing_leader_id, follow_up_task)`.
   4. If no suitable leader exists:
      - Spawn a new leader: `spawn_instance("leader", instance_name="<task-name>")`.
      - Record in registry: `shared_meta_kv(set_kv={"pm_dispatch_registry": {task_id: {leader_instance_id, status: "in_progress", ...}}})`.
      - Dispatch the task: `send_message(new_leader_id, task)`.
   5. END TURN — wait for the leader's report. Do not poll, sleep, or loop.
   6. When the report arrives:
      - Update the registry: status → "completed", last_report_summary → <summary>.
      - If the work is done, report the result to the user.
      - If follow-up is needed, reuse the same leader (step 3) or spawn a new one (step 4).
   ```

2. **Retire the "Closing" hand-back line** — Replace *"If you want this acted on, hand to `leader`"* with: *"If the user asked me to act, I have dispatched to `leader` and am awaiting the report. If the user asked me to assess, my analysis is above."*

3. **Add the END TURN contract** — from `docs/agent-prompt-writing-guide.md`: after every `send_message` dispatch, PM must END TURN. Holding the turn open blocks report delivery and deadlocks the run.

---

## 7. Security & Threat Model

### STRIDE Analysis

| Threat | Attack Vector | Impact | Mitigation |
|--------|--------------|--------|------------|
| **Elevation of Privilege** | PM tries to spawn `developer` directly | High if unmitigated | 🔴 **Blocked at two layers**: (1) `_check_team_membership` rejects — only `"leader"` in PM's `team_members` (`_auth.py:185`); (2) Cardinal #2 explicitly forbids it. |
| **DoS** | Prompt injection → PM spawns many leaders in a loop | Medium | 🟡 **Bounded**: `max_children_per_instance=50` (`instance_lifecycle.py:957`) caps children per PM. Cardinal #2 *"always END TURN after send_message"* prevents tight loops. |
| **Spoofing** | PM's `send_message` to an instance it didn't spawn | Medium | 🟡 **No system-level enforcement**: `send_message` (`instance.py:1660`) does NOT check parent-ownership. PM can message any instance by id. Mitigation: Cardinal #2 discipline + prompt instruction "only send_message to instances you spawned." |
| **Tampering** | PM writes garbage to `shared_meta_kv` | Low | 🟢 **Scoped**: PM's KV writes are partitioned to PM's `context_key` (tree-root). Worst case: PM's own tracking breaks. |
| **Repudiation** | PM denies dispatching a leader | Low | 🟢 **Automatic audit trail**: every spawn creates an `Instance` DB row with `parent_id`. Every `send_message` creates a `MessageQueue` row with `source="internal_agent:<PM_id>"`. |
| **Info Disclosure** | `list_instances` / `get_instance_info` leak sibling details | Low | 🟢 **Acceptable**: PM is a privileged oversight agent. Seeing all instances is within its strategic mandate. |

### Runaway Chain Prevention

The instance tree is bounded by **per-parent caps**, not a global depth limit:

```
PM (root)
├── leader-1 (child of PM, counts toward PM's 50-child cap)
│   ├── developer-1 (child of leader-1, counts toward leader-1's 50-child cap)
│   ├── tester-1
│   └── reviewer-1
├── leader-2 (child of PM)
│   └── developer-2
└── ...
```

- PM can have at most **50 concurrent active children** (leaders)
- Each leader can have at most **50 concurrent active children** (developer, tester, etc.)
- Leaders **cannot spawn more leaders** — `"leader"` is NOT in leader's `team_members`
- Completed children are removed from `instance_hierarchy` — they don't count toward the active cap

**🟢 No recursion risk.** The team_members allow-list prevents leader→leader chains by design.

### Blast Radius if PM Goes Rogue

- PM can spawn ≤50 leaders concurrently
- Each leader can spawn ≤50 children → worst case 50 × 50 = 2500 grandchildren
- Total worst case: 1 PM + 50 leaders + 2500 grandchildren = 2551 instances
- All bounded by their own per-parent `max_children_per_instance=50` cap
- PM cannot modify code directly (denies `edit_file`, `write_file`, `bash`)
- PM cannot self-modify its prompt (denies `self`)

---

## 8. System-Level Changes — None Required

**No system-level code changes are needed.** The entire implementation is agent-level:

| Layer | Change Required? | Details |
|-------|-----------------|---------|
| `daemon/services/instance_lifecycle.py` | ❌ None | `spawn_instance` already supports PM→leader |
| `daemon/services/instance_messaging.py` | ❌ None | `enqueue_message` + revive-fix already handle COMPLETED→RUNNING |
| `daemon/services/child_reports.py` | ❌ None | PROCESS_REPORT + ReportInjection already deliver reports to PM |
| `daemon/tools/instance.py` | ❌ None | spawn_instance, send_message already work for PM |
| `daemon/tools/_auth.py` | ❌ None | `_check_team_membership` already checks caller's team_members |
| `agents/project-manager/meta.json` | ✅ **Change** | Add `instance` + `shared_meta_kv` to allow; remove from deny; add `"leader"` to team_members |
| `agents/project-manager/rule.md` | ✅ **Change** | Rewrite Cardinal #2; update Cardinal #5 parenthetical; retire Guideline #8 hand-back |
| `agents/project-manager/workflow.md` | ✅ **Change** | Add Flow 5 (Dispatch & Delegation); update Closing section |

---

## 9. Risks & Mitigations

| Severity | Risk | Mitigation |
|----------|------|------------|
| 🟡 | **`send_message` has no parent-ownership check** — PM could message any instance in the system, not just its children | Cardinal #2 instruction + prompt discipline: "only send_message to instances you spawned, identified by your dispatch registry." System-level fix (adding ownership check in `send_message`) is a separate enhancement, not a blocker. |
| 🟡 | **`shared_meta_kv` write capability** breaks PM's "read-only" identity | `shared_meta_kv` is a bookkeeping tool, not a code/plan/project-state mutation tool. It only writes to the KV namespace scoped to PM's context_key. The write is to PM's own tracking data, not to code or project state. Cardinal #1 ("read-only on code, plans, and project state") still holds — KV tracking data is neither code nor plan nor project state. |
| 🟡 | **`max_instances=100` is cumulative** — long-running PM sessions accumulate completed leader rows | PM should periodically clean up: when a task is fully done and no reuse is anticipated, PM can recommend the user terminate old completed instances. PM cannot `terminate_instance` (denied) — it should ask the user. This is deliberate: PM advises, user decides. |
| 🟡 | **Dual-write gap between `shared_meta_kv` and `enqueue_message`** — PM writes tracking entry but is killed before `send_message` | Write ordering: PM writes KV AFTER `spawn_instance` returns the instance_id, then immediately calls `send_message`. If killed between KV write and send_message, the registry has a valid instance_id but no task dispatched — PM can detect this on next turn (leader is IDLE with no messages) and re-dispatch. |
| 🟢 | **`instance` category future-proofs auto-grant** — new tools added to the `instance` category will be auto-granted to PM | Add a CI check recommendation: any new `@register_tool_category("instance")` decorator should trigger a review of all agents with `"instance"` in `tools.allow`. Not a blocker for this feature. |
| 🟢 | **Leader's `team_members` could change in the future** — if a future leader v2 adds `"leader"` to its `team_members`, leader→leader chains become possible | Track in `agents/leader/memory.md` as a regression guard. Not a current risk (verified: leader's team_members does not include "leader"). |

---

## 10. Implementation Checklist

### Phase 1: meta.json (configuration)
- [ ] Add `"instance"` to `tools.allow`
- [ ] Add `"shared_meta_kv"` to `tools.allow`
- [ ] Remove `"instance"`, `"shared_meta_kv"`, `"send_message"`, `"spawn_instance"`, `"terminate_instance"` from `tools.deny`
- [ ] Add `"terminate_instance"`, `"council"` to `tools.deny`
- [ ] Change `team_members` from `[]` to `["leader"]`
- [ ] Update `description` from "non-dispatching" to "strategic oversight with leader dispatch"

### Phase 2: rule.md (cardinals)
- [ ] Rewrite Cardinal #2: "No work dispatch" → "Dispatch execution to `leader` only"
- [ ] Update Cardinal #5 parenthetical: remove stale "leader decides dispatch" reference
- [ ] Retire Guideline #8 (hand-back); replace with "Dispatch vs advisory mode" guideline

### Phase 3: workflow.md (flows)
- [ ] Add Flow 5 — Dispatch & Delegation
- [ ] Update Closing section: remove hand-back, add END TURN contract
- [ ] Add dispatch registry pattern (shared_meta_kv task→instance tracking)

### Phase 4: Validation
- [ ] Smoke test: PM spawns leader → leader runs → report arrives → PM processes
- [ ] Reuse test: PM sends follow-up to COMPLETED leader → leader revives (COMPLETED→RUNNING)
- [ ] Multi-task test: PM spawns 2 leaders for 2 tasks → tracks both in registry → receives both reports
- [ ] Error test: leader goes to ERROR → PM detects via get_instance_info → PM spawns fresh leader
- [ ] Cascade test: PM terminated → all active leaders cascade-terminate

---

## Decisions Pending

1. **Should PM be able to `terminate_instance`?** — Currently recommended NO (deny it). PM advises termination, user/leader executes. Alternative: allow it for self-cleanup of stale leaders. **Architect recommendation: deny. PM is advisory; destruction is execution.**

2. **Should PM always reuse leaders, or spawn fresh per task?** — Reuse is more efficient (context continuity, no new instance overhead) but risks context pollution. **Architect recommendation: reuse for the same task area; spawn fresh for unrelated tasks. PM decides based on task affinity.**

3. **Should PM spawn multiple leaders in parallel?** — The system supports it (up to 50 concurrent). But PM's strategic oversight role suggests sequential dispatch (one task at a time). **Architect recommendation: allow parallel but default to sequential in the prompt. PM may parallelize when the user explicitly requests it.**

---

## Open Questions

1. **`shared_meta_kv` cross-instance visibility when PM is spawned by ari** — If PM is a child of ari (not a root), all PMs under the same ari session share the same `context_key`. Is this desired (cross-PM coordination) or a privacy concern? Likely a non-issue since PM is typically spawned directly by the user as a root instance.

2. **Whether `DependencyBus.watch` re-registration handles reuse correctly** — When PM reuses a COMPLETED leader, the watcher re-registers via `instances.parent_id` (permanent). The bus keys on `source_task_id`, so a new task gets a new watcher. This should work but has no explicit test for the reuse scenario.

3. **Queue guard TOCTOU** — Between `get_queue_stats` and `enqueue_message` in `send_message`, a concurrent enqueue could theoretically appear. Low frequency (ms-scale race window) but worth a focused test for PM's multi-leader scenario.

---

## Appendix: Worker Reports

### Worker A — Data Flow & Lifecycle (c1ed2eb6, `data-flow-design` skill)
- Verified instance reuse is supported via the revive-fix (`instance_messaging.py:1486-1510`)
- Verified `instance_hierarchy` row deletion does NOT break reuse (parent_id is permanent)
- Mapped the full spawn→send→report→reuse data flow with file:line references
- Confirmed `shared_meta_kv` is the correct tracking mechanism (fresh-read per turn)
- Identified the dual-write gap between KV tracking and enqueue_message
- Flagged `max_instances=100` cumulative ceiling as a long-term concern

### Worker B — Security & Tooling (472179e3, `security-design` skill)
- Produced exact meta.json diff with rationale per entry
- Verified `instance` category expands to exactly 5 tools
- Identified `send_message` has no parent-ownership check (🟡)
- Verified `_check_team_membership` checks caller's (not spawned agent's) team_members
- Confirmed leader's `team_members` does not include "leader" (no recursion risk)
- Produced STRIDE threat model with mitigations
- Proposed Cardinal #2 rewrite and Guideline #8 retirement
- Flagged `instance` category future-proofing risk (🟢)
