# Phase 3: PM→Leader Dispatch & Instance Reuse Integration

Date: 2026-08-13
Author: plan-creation worker (via planner dispatch)
Status: Draft
Predecessor: `architecture-dispatch.md` (architect deep-review, same directory)
**Canonical source:** `plan-overview.md` — Cardinal/Guideline text, meta.json spec, Flow numbering, KV schema, and unified task list are authoritative there. This document provides implementation detail and lifecycle verification only.

---

## 1. Architecture Overview

### What This Phase Delivers

The Project Manager agent upgrades from a **stand-alone, read-only advisory agent** to a **strategic dispatcher** that can spawn `leader` instances to execute work and reuse those instances for ongoing task coordination. This is achieved entirely through **agent prompt + meta.json changes** — no daemon code, no new API endpoints.

### Dispatch Model

```
User → PM (strategic assessment + dispatch decision)
              │
              ├── spawn_instance("leader", instance_name="task-xyz")
              │       └── send_message(leader_id, task_message)
              │              └── END TURN
              │
              [Leader runs: spawns developer/tester/reviewer as needed]
              [Leader completes → DependencyBus → PM auto-resumed with report]
              │
              ├── Process report → update shared_meta_kv registry
              ├── Report to user (if task complete)
              └── Reuse leader for follow-up (if ongoing task)
                     └── send_message(same_leader_id, follow_up) → END TURN
```

### Three Pillars

| Pillar | Mechanism | Files Changed |
|--------|-----------|---------------|
| **Dispatch enablement** | `meta.json` tool allow + team_members | `agents/project-manager/meta.json` |
| **Instance tracking** | `shared_meta_kv` task→leader registry | `agents/project-manager/workflow.md` |
| **Instance reuse** | `send_message` on COMPLETED leader (revive-fix) | `agents/project-manager/workflow.md` |

### Why No Daemon Code Is Needed

Verified across the codebase (see §6 — Lifecycle Compatibility):
- `spawn_instance` works for any agent with the target in `team_members` — no PM-specific code path
- `send_message` on a COMPLETED instance triggers the revive-fix (`instance_messaging.py:1486-1510`) — COMPLETED→RUNNING transition
- `_register_child_completion_watcher` keys on permanent `instances.parent_id` — re-registers correctly on reuse
- `DependencyBus` emits `child_complete` follow-up → PM auto-resumed with leader's report
- `_check_team_membership` checks the **caller's** `team_members` — adding `"leader"` to PM's list is sufficient

---

## 2. meta.json Change Spec

### Current State (v1, stand-alone)

```json
{
  "version": "1.0.0",
  "description": "Strategic project oversight. Stand-alone, non-dispatching, read-only on code (v1).",
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

### Target State (v2, strategic dispatcher)

```json
{
  "version": "2.0.0",
  "description": "Strategic project oversight with leader dispatch. Read-only on code; dispatches execution to leader instances.",
  "tools": {
    "allow": [
      "explore", "project_get", "project_list", "project_search",
      "project_get_by_instance", "project_get_by_directory",
      "project_history_list", "project_history_search", "project_cn_list",
      "filesystem", "todo_view", "chart", "image", "plane",
      "instance", "shared_meta_kv"
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
      "terminate_instance", "council",
      "charter", "image-reader",
      "self", "question", "mcp"
    ]
  },
  "team_members": ["leader"]
}
```

> **C1:** `"charter"` and `"image-reader"` denied by exact name — PM holds `chart`/`image` categories which auto-derive these as spawnable agents. Without this deny, PM could spawn charter/image-reader instances, violating Cardinal #2 (leader only).
>
> **C2:** Plane write tools (`plane_create_*`, `plane_update_*`, etc.) denied by exact name — see §2 "Plane Write Tool Deny Pattern" below. Exact tool names enumerated at build time via task U-PM-9.

### Exact Diff

```diff
- "version": "1.0.0",
+ "version": "2.0.0",

- "description": "Strategic project oversight. Stand-alone, non-dispatching, read-only on code (v1).",
+ "description": "Strategic project oversight with leader dispatch. Read-only on code; dispatches execution to leader instances.",

  "allow": [
    ...
-   "filesystem", "todo_view", "chart", "image", "plane"
+   "filesystem", "todo_view", "chart", "image", "plane",
+   "instance", "shared_meta_kv"
  ],
  "deny": [
    ...
    "edit_file", "write_file", "bash",
-   "instance", "self", "shared_meta_kv",
-   "send_message", "spawn_instance", "terminate_instance",
-   "question", "mcp"
+   "terminate_instance", "council",
+   "charter", "image-reader",
+   "self", "question", "mcp"
  ]

- "team_members": [],
+ "team_members": ["leader"],
```

### Rationale Per Change

| Change | Reason |
|--------|--------|
| **Add `"instance"` to allow** | Expands to 4 tools PM needs: `spawn_instance`, `send_message`, `list_instances`, `get_instance_info`. Cleaner than 4 individual names; matches leader's pattern (`leader/meta.json:14`). |
| **Add `"shared_meta_kv"` to allow** | PM needs persistent task→leader-instance tracking that survives context compaction. `shared_meta_kv` is fresh-read per LLM turn, partitioned by `context_key`. |
| **C1: Deny `"charter"` by exact name** | PM holds `chart` category which auto-derives `charter` as a spawnable agent. Without this deny, PM could spawn charter instances — violates Cardinal #2 (leader only). |
| **C1: Deny `"image-reader"` by exact name** | PM holds `image` category which auto-derives `image-reader` as a spawnable agent. Same violation as charter. |
| **C2: Deny Plane write tools by exact name** | The `"plane"` category grants ALL plane tools including writes (`plane_create_issue`, `plane_update_issue`, etc.). PM is read-only on Plane per Cardinal #1 ("external systems (Plane)"). Exact names enumerated at build time (task U-PM-9). See "Plane Write Tool Deny Pattern" below. |
| **Deny `"terminate_instance"`** | PM must not destroy instances — termination is destructive and cascades to grandchildren. PM remains advisory on lifecycle decisions. PM can still `get_instance_info` to check status. |
| **Deny `"council"`** | PM does not convene governor councils. Irrelevant capability. |
| **Keep `"self"` denied** | `self` enables prompt self-modification (`inner_soul`). PM should not rewrite its own prompt. |
| **Keep `"question"` denied** | PM does not use the `ask_questions` pause mechanism — it provides advisory output and dispatch decisions, not interactive Q&A. |
| **Keep `"mcp"` denied** | PM does not need external MCP integrations for strategic oversight + dispatch. |
| **Keep `"bash"`, `"edit_file"`, `"write_file"` denied** | PM remains read-only on code (Cardinal #1, non-negotiable). |
| **Add `"leader"` to `team_members`** | `_check_team_membership` (`_auth.py:43-80`) checks the **caller's** `team_members` list. Without `"leader"`, `spawn_instance("leader")` returns authorization error. PM must NOT be able to spawn other agents (developer, tester, etc.) — only `leader`. |
| **Version `"1.0.0"` → `"2.0.0"`** | Breaking change to agent capabilities (new tools, new team_members, new Cardinals). Follows semver. |

### Tool Expansion Verification

The `instance` category (`@register_tool_category("instance")` in `daemon/tools/instance.py:971`) covers these tools:

| Tool | Line | PM Needs It? | Status |
|------|------|--------------|--------|
| `spawn_instance` | 971 | ✅ Yes — spawn leader instances | Allowed via category |
| `send_message` | 1563 | ✅ Yes — dispatch tasks + reuse instances | Allowed via category |
| `list_instances` | 1762 | ✅ Yes — see what's running | Allowed via category |
| `get_instance_info` | 1775 | ✅ Yes — check leader status | Allowed via category |
| `terminate_instance` | 1741 | ❌ Denied — too dangerous for oversight agent | Denied by exact name |

Deny-wins-over-allow is verified at `daemon/tools/instance.py:226-258`. Denying `terminate_instance` by exact name overrides the `instance` category expansion.

### Plane Write Tool Deny Pattern (C2)

The exact Plane write tool names are not known at plan time (dynamic MCP discovery from the remote server). The deny rule is:

**Rule:** Any `plane_*` tool whose name contains `create`, `update`, `delete`, `add`, `remove`, `set`, `edit`, or `assign` MUST be added to `tools.deny` by exact name.

**Known likely candidates** (verify against actual MCP discovery during implementation, task U-PM-9):
- `plane_create_issue`, `plane_update_issue`, `plane_delete_issue`
- `plane_add_comment`, `plane_remove_comment`
- `plane_create_cycle`, `plane_update_cycle`
- `plane_assign_issue`

**Cardinal #1 basis:** "I never edit, write, commit, or mutate source code, plans, configs, project state, **or external systems (Plane)**." The deny-list enforces this at the meta.json level so it holds even if a skill fails to load.

---

## 3. Instance Reuse Design

### The Key Decision: How PM Tracks "I Already Have a Leader for Task X"

PM needs a mechanism that survives context compaction (PM's conversation history may be compacted between turns) and persists across multiple user interactions.

### Recommendation: shared_meta_kv Registry (Option 2)

**PM maintains a task→leader-instance registry in `shared_meta_kv`.**

### Trade-Off Analysis

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **Option 1: Conversation History** (Leader's pattern) | No new infrastructure; matches leader's proven pattern (workflow.md:141-143) | **Lost across context compaction** — PM's context may be compacted between turns, losing leader instance_ids. Leader avoids this because it dispatches many short-lived tasks within a single turn. PM dispatches fewer, longer-running tasks across many turns. | ❌ Rejected — does not survive compaction |
| **Option 2: shared_meta_kv Registry** | Survives context compaction (fresh-read per turn); purpose-built for cross-turn state; partitioned by `context_key` (PM and its leaders share namespace); no file-write capability needed | Needs lifecycle management (stale entries, cleanup); `shared_meta_kv` is write-capable (but it's bookkeeping, not code/plan/state mutation) | ✅ **Recommended** |
| **Option 3: Todo Graph as Registry** | Integrates with fan-in tracking | Todo graph is designed for task tracking, not instance registry — semantic overload; todo nodes are not structured for instance_id lookup; compaction may also affect todo state | ❌ Rejected — semantic mismatch |

**Why Option 2 over Option 1:** The constraint is explicit — "instance reuse must survive context compaction." PM is strategic (fewer, longer-running tasks across many user turns), unlike leader which has many short-lived dispatches within a single planning/implementation loop. Conversation history retention is unreliable for PM's usage pattern. `shared_meta_kv` is fresh-read on every LLM turn, guaranteeing the registry is always available regardless of compaction state.

**Why Option 2 is acceptable despite being write-capable:** `shared_meta_kv` writes to a KV namespace scoped to PM's `context_key`. It only stores PM's own bookkeeping data (task→instance mappings), never code, plans, or project state. Cardinal #1 ("read-only on code, plans, and project state") is not violated — KV tracking data is none of those.

### Registry Data Structure (W5 — canonical schema in plan-overview.md)

PM stores a JSON array under one meta_key (`"pm_leader_instances"`):

**Key:** `"pm_leader_instances"`

**Value:** JSON array of objects:

```json
[
  {
    "instance_id": "abc123def456",
    "task_area": "Implement OAuth2 authentication flow",
    "status": "active",
    "spawned_at": "2026-08-13T13:00:00Z",
    "last_message_at": "2026-08-13T13:05:00Z"
  },
  {
    "instance_id": "def456ghi789",
    "task_area": "Fix race condition in job processor",
    "status": "completed",
    "spawned_at": "2026-08-13T13:15:00Z",
    "last_message_at": "2026-08-13T13:30:00Z"
  }
]
```

**Field semantics:**
- `instance_id` — UUID of the leader instance (from `spawn_instance` return)
- `task_area` — human-readable description of the task area (for reuse affinity)
- `status` — `"active"` (leader running or awaiting report), `"completed"` (task done), `"failed"` (leader errored)
- `spawned_at` — ISO8601 timestamp when PM spawned the leader
- `last_message_at` — ISO8601 timestamp of last `send_message` to this leader

### Registry Lifecycle

| Event | PM Action | Registry State |
|-------|-----------|----------------|
| **Spawn new leader** | `spawn_instance("leader")` → receive instance_id → append entry → `send_message` → END TURN | `status: "active"` |
| **Leader reports back** | Process report → update entry | `status: "completed"`, update `last_message_at` |
| **Reuse for follow-up** | Read registry → find entry → `send_message(existing_id, ...)` → update entry | `status: "active"`, update `last_message_at` |
| **Leader error** | `get_instance_info` shows ERROR → mark entry → optionally spawn fresh | `status: "failed"` |
| **Task fully done** | No reuse anticipated → mark completed, advise user on cleanup | `status: "completed"` (entry persists for reference) |

### Registry Cleanup Rules

- Entries marked `"completed"` or `"failed"` are **kept for reference** but **not reused** for new tasks.
- Stale entries (where `spawned_at` is > 24 hours old) **can be pruned** by PM on any registry read.
- PM cannot `terminate_instance` (denied) — it recommends cleanup, user decides.

### Write-Ordering Discipline

🔴 **PM MUST write the tracking entry AFTER `spawn_instance` returns** (not before). If PM writes the KV first and is killed before spawn completes, the registry has a phantom instance_id. Writing after spawn ensures the instance_id is valid.

```
CORRECT:  spawn_instance → instance_id returned → shared_meta_kv(set_kv) → send_message → END TURN
WRONG:    shared_meta_kv(set_kv) → spawn_instance → [killed here] → phantom entry
```

If PM is killed between the KV write and `send_message`, the registry has a valid instance_id but no task dispatched. PM can detect this on the next turn (leader is IDLE with no messages) and re-dispatch via `send_message`.

---

## 4. Dispatch Protocol

### Complete PM→Leader Dispatch Workflow

This is the canonical dispatch protocol. It should be encoded as **Flow 5 — Dispatch & Delegation** in `workflow.md`.

### When to Spawn a New Leader

PM identifies strategic work that needs execution when:
- User explicitly requests action ("implement X", "fix Y", "set up Z")
- PM's assessment reveals work that must be done and the user asks PM to proceed
- A prior task's report reveals follow-up work in the same area

### When to Reuse an Existing Leader

PM reuses a COMPLETED leader instance when:
- The follow-up task is **related to the same task area** (same feature, same codebase region, same architectural context)
- The existing leader is **not in ERROR or TERMINATED state** (check via `get_instance_info`)
- The leader's accumulated context from the prior task is **beneficial** (it remembers the codebase area, conventions discovered, decisions made)

PM spawns a **fresh leader** when:
- The task is **unrelated** to any existing task in the registry
- The existing leader is in ERROR state
- The user explicitly requests a fresh start

### Hand-Off Message Format

PM sends strategic context to leader — **what and why, never how**:

```
[Task: <task-name>]

<context>
Strategic context: <1-2 sentences on why this matters — what goal it serves>
Background: <relevant findings from PM's assessment — blockers, risks, decisions pending>
Plan reference: <path to .agents/shared/planning/<feature>/ if a plan exists>
</context>

<task>
<clear, specific description of what needs to be done>
<success criteria — what "done" looks like>
</task>

Execute this. Report back when complete.
```

**Key principles:**
- PM frames the strategic context (why this matters, what's blocking it)
- PM does NOT prescribe the implementation approach — that's leader's job
- PM references existing plans if they exist (`.agents/shared/planning/`)
- PM states success criteria so leader knows when it's done

### Report Handling

When a leader's report arrives (delivered as a new message via DependencyBus):

1. **Update registry** — `shared_meta_kv(set_kv={...})` with `status: "completed"`, `last_report_summary`, `last_report_at`
2. **Assess the report** — Did the leader complete the task? Are there gaps? Follow-up needed?
3. **Report to user** — Deliver the leader's report summary with PM's strategic framing
4. **Decide next step** — Task done? Report and close. Follow-up needed? Reuse same leader. New work? Spawn fresh leader.

### Multi-Task Coordination (Fan-In via Todo Graph)

When PM dispatches **multiple leaders for different tasks** in parallel:

1. **Before dispatching 2+ leaders**, create a todo graph:
   ```
   todo_graph_create(nodes=[
     {"id": "task-a", "text": "Task A: <description>"},
     {"id": "task-b", "text": "Task B: <description>"},
   ])
   ```

2. **As each leader reports back**, mark its node done:
   ```
   todo_graph_update(node_id="task-a", status="done")
   ```

3. **Aggregate only when ALL nodes are done** — use `todo_view()` to verify before composing a consolidated report.

4. **For a single-leader task**, skip the graph — dispatch, wait, report.

### END TURN After Dispatch

After `send_message` to a leader, PM **MUST END ITS TURN**. State the why explicitly in `workflow.md`:

> Holding the turn open blocks report delivery and deadlocks the run. The system resumes my turn automatically when each instance reports.

This follows the `docs/agent-prompt-writing-guide.md §7` convention. The batching rule: for parallel fan-out (2+ leaders in one wave), PM may spawn and send all messages, then END TURN once (per-batch, not per-dispatch).

### Updated Workflow Flow Summary (C4 — canonical numbering from plan-overview.md)

| Flow | Trigger | PM Action | Ends With |
|------|---------|-----------|-----------|
| **Flow 1 — Risk Assessment** | "What's our risk profile?" | Analyze, deliver Terse/Full report | Advisory output (no dispatch) |
| **Flow 2 — Progress Reporting** | "Where are we?" | Analyze, deliver report | Advisory output (no dispatch) |
| **Flow 3 — Scope Assessment** | "Has scope drifted?" | Analyze, deliver report | Advisory output (no dispatch) |
| **Flow 4 — Decision Framing** | "Frame the decision between A and B" | Present options, recommend | Advisory output (no dispatch) |
| **Flow 5 — Dispatch & Delegation** (Phase 3) | "Implement X" / "Fix Y" / "Act on this" | Spawn/reuse leader, dispatch task | **END TURN** after `send_message` |
| **Flow 6 — Roadmap Generation** (Phase 2) | "Give me the roadmap for feature X" | Synthesize timeline from Plane + planning + history | Roadmap template + gantt chart |
| **Flow 7 — Milestone Tracking** (Phase 2) | "Check milestone alignment" | Cross-reference Plane vs internal exit criteria | Milestones table |
| **Flow 8 — Burndown Reporting** (Phase 2) | "Burndown for cycle Z" | Combine Plane velocity + internal events | Line chart + interpretation |

---

## 5. Cardinal Changes

### Current Cardinals (7) — v1

1. Read-only on code, plans, and project state.
2. **No work dispatch — stand-alone.** ← MUST CHANGE
3. Answer in proportion to the question.
4. Evidence-cite every claim.
5. Frame decisions, do not make them.
6. Scope discipline.
7. No secrets in output.

### Proposed Cardinals (7) — v2

> **Canonical text is in `plan-overview.md` → "Canonical Cardinal Set".** The text below is a summary; defer to the overview for exact wording.

1. **Read-only on code, plans, configs, project state, and external systems.** *(W2: extended)* — I never edit, write, commit, or mutate source code, plans, configs, project state, or external systems (Plane). My output is messages and dispatch instructions only.

2. **Dispatch execution to `leader` only.** *(REPLACES v1 #2)* — I may spawn `leader` instances to execute work. I spawn exactly the agents in my `team_members` — currently `leader` only. I never spawn `developer`, `tester`, `reviewer`, or any other specialist directly — that is `leader`'s job. I always END MY TURN after `send_message` and wait for the leader's report (no polling, no looping).

3. **Answer in proportion to the question.** Default Terse; Full or named-flow template only when user asks for depth.

4. **Evidence-cite every claim.** Status, risk, scope, milestone, and burndown bullets carry project history events, critical notes, planning-doc lines, Plane references, or git references. When Plane is unavailable, cite planning docs only and note the gap.

5. **Frame decisions, do not make them.** Surface options with trade-offs and a recommendation; the final call is human. For tactical execution, dispatch to `leader` per Cardinal #2.

6. **Scope discipline.** I do not expand the user's stated question.

7. **No secrets in output.** I never reproduce secrets, API keys, or credentials.

### Rationale for Changes

- **Cardinal #1 is extended** (W2) — adds "and external systems (Plane)" to enforce read-only on Plane at the prompt level. This pairs with the meta.json deny-list for Plane write tools (C2). The "v1 verbatim" label is removed — all text is now the canonical v2 wording.

- **Cardinal #2 is the pivotal change.** It replaces "no dispatch" with "dispatch to leader only." This constrains PM's dispatch scope to a single agent type (`leader`), preventing PM from becoming a second leader. The END TURN clause is cardinalized because holding the turn open after `send_message` deadlocks the run.

- **Cardinal #4 is extended** — adds explicit "Plane unavailable" clause to prevent fabrication.

- **Cardinal #5 parenthetical update** removes the stale "leader decides dispatch" reference. The new parenthetical cross-references Cardinal #2 for the execution boundary.

- **All remaining Cardinals** (#3, #6, #7) are retained with no semantic change.

### Guideline Changes

> **Canonical Guideline text is in `plan-overview.md` → "Canonical Guideline Set".** Summary below; 10 Guidelines total.

**Guideline #8 (Hand-back) — RETIRE.** Replaced with "Dispatch vs advisory mode" (canonical text in overview).

**New Guideline #8 — Dispatch vs Advisory Mode:** If the user asks me to act ("implement X", "fix Y"), I dispatch to `leader` via Flow 5 and END MY TURN. If the user asks me to assess, I deliver analysis and stop.

**Guideline #7 (Skill versioning)** — parenthetical updated: remove "v1 has no skills". Keep version-consistency rule.

**New Guideline #9 — Instance Reuse Discipline:** Before spawning a new leader, check the dispatch registry (`shared_meta_kv` key `"pm_leader_instances"`). If a COMPLETED leader exists for the same task area, reuse it.

**New Guideline #10 — Never Silently Incomplete:** If a dispatched leader fails, apply the escape valve ladder. Never silently skip a failed task.

**Guidelines #1–#6** (Voice, Output shape, Severity, Risk math, Decision framing, When stuck on data) — unchanged from v1.

### workflow.md Closing Section — Replace

**Old closing:**
> Whatever flow I run, I end every reply with the bold inline string **"If you want this acted on, hand to `leader`."** I never spawn an instance. I never write a file. I return only as a message.

**New closing:**
> If the user asked me to act, I have dispatched to `leader` and am awaiting the report. If the user asked me to assess, my analysis is above. I never both dispatch and deliver a full report in the same turn.

---

## 6. Lifecycle Compatibility Verification

### Authorization Gate (`_check_team_membership`)

**Verified:** `daemon/tools/_auth.py:43-192`

- The check reads the **caller's** `meta.json` `team_members` list, not the spawned agent's.
- Adding `"leader"` to PM's `team_members` authorizes `spawn_instance("leader")`.
- PM's `team_members: ["leader"]` means PM can ONLY spawn `leader` — attempting `spawn_instance("developer")` returns authorization error.
- `_check_team_membership` also merges in implied team members from `tools.allow` categories via `TOOL_REQUIRED_AGENTS`. The `instance` category does NOT map to any required agent (it's a tool category, not an agent dependency), so this auto-derive does not expand PM's spawn set beyond `leader`.

**✅ Compatible. No daemon code changes needed.**

### DependencyBus Watcher (Report-Back)

**Verified:** `daemon/tools/instance.py:430-525`

- `send_message` calls `_register_child_completion_watcher(manager, current_instance_id, instance_id, message_id)`.
- The watcher checks `target_instance.parent_id != parent_instance_id` — if they don't match, it's a no-op (not a parent→child send).
- When PM spawns a leader, `leader.parent_id = PM.instance_id` (permanent, never cleared — verified in `architecture-dispatch.md` §5).
- The watcher keys on `source_task_id` (the child's task id from the message). Each `send_message` creates a new task, so reuse creates a new watcher correctly.
- On leader completion, `bus.emit_terminal` fires the `FollowUp(kind="child_complete")`, which resumes PM's turn with the report.

**✅ Compatible. PM as parent works identically to leader→planner parent-child.**

### Instance Reuse (Revive-Fix)

**Verified:** `daemon/services/instance_messaging.py:1486-1510`

- When PM calls `send_message` on a COMPLETED leader, `_prepare_enqueued_message` detects `status == COMPLETED` and transitions it to `RUNNING`.
- The leader's checkpoint, message history, and LangGraph thread persist in the DB and reload on the next `graph.astream`.
- The watcher re-registers via the permanent `instances.parent_id` field (the `instance_hierarchy` working-set row was deleted on completion, but `parent_id` is never cleared).

**✅ Compatible. Reuse is a first-class capability, not a workaround.**

### Status Guard in send_message

**Verified:** `daemon/tools/instance.py:1661-1668`

- `send_message` rejects `TERMINATED` and `ERROR` status only.
- `COMPLETED` is explicitly allowed — PM can `send_message` to a completed leader.
- `RUNNING` or `WAITING_CHILDREN` are rejected by the queue guard (`pending_count > 0 || processing_count > 0` at line 1670-1677), preventing double-dispatch to an active leader.

**✅ Compatible. PM cannot accidentally double-message an active leader.**

### Queue Guard TOCTOU

**Note:** Between `get_queue_stats` and `enqueue_message` in `send_message`, a concurrent enqueue could theoretically appear (ms-scale race window). This is a pre-existing system-level concern, not introduced by PM. For PM's usage pattern (sequential dispatch by default), this is negligible.

### Edge Case: Leader Terminated by Someone Else

If a leader PM spawned gets terminated externally:
- `send_message(terminated_leader_id, ...)` is **blocked** by the status guard (returns error: "Instance is terminated/errored").
- PM must detect this via the returned error string and spawn a fresh leader.
- PM's `shared_meta_kv` registry still references the old instance_id — PM should update it on detection.

### Edge Case: PM Context Compaction

If PM's conversation is compacted between turns:
- `shared_meta_kv` is fresh-read per turn — the registry survives compaction.
- Leader instance_ids are in the registry, not in compacted conversation history.
- PM's todo graph (if created for multi-task tracking) is in DB — survives compaction.

**✅ Mitigated by Option 2 (shared_meta_kv registry).**

### Edge Case: PM Terminated While Leaders Running

- `terminate_instance(PM)` cascades in parallel to all children via `InstanceHierarchy` rows.
- Active leaders are terminated with `terminal_reason="abandoned"`.
- COMPLETED leaders are NOT cascaded (hierarchy row already deleted) — they linger harmlessly in `instances` table.

---

## 7. Fan-In & Escape Valve

### Multi-Leader Tracking

When PM dispatches multiple leaders for different tasks:

```
PM Turn:
  todo_graph_create(nodes=[
    {"id": "task-a", "text": "Task A: implement auth"},
    {"id": "task-b", "text": "Task B: fix job processor bug"},
  ])
  
  spawn_instance("leader", instance_name="auth") → leader-A
  shared_meta_kv(set_kv={...})  # task-a → leader-A
  send_message(leader-A, "implement auth ...")
  
  spawn_instance("leader", instance_name="fix-job") → leader-B
  shared_meta_kv(set_kv={...})  # task-b → leader-B
  send_message(leader-B, "fix job processor ...")
  
  END TURN

[Leader-A completes → PM resumed with report-A]
PM Turn:
  todo_graph_update(node_id="task-a", status="done")
  shared_meta_kv(set_kv={...})  # task-a status → completed
  // task-b still pending — END TURN, wait for report-B

[Leader-B completes → PM resumed with report-B]
PM Turn:
  todo_graph_update(node_id="task-b", status="done")
  shared_meta_kv(set_kv={...})  # task-b status → completed
  // All nodes done — aggregate and report to user
```

### Escape Valve Ladder

When a dispatched leader does not report back or reports an error:

1. **Confirm it's actually stuck.** The leader may simply be slow (it has its own internal loops: planner→reviewer→developer). PM does not poll or sleep — PM ends its turn and waits for the next message. If no message arrives after a reasonable period (next user interaction), PM checks via `get_instance_info`.

2. **One re-dispatch.** If `get_instance_info` shows the leader is in ERROR state, or if the leader's report indicates failure:
   - Spawn ONE replacement leader: `spawn_instance("leader", instance_name="<task-name>-retry")`
   - Send the same task with a note: "Previous attempt failed — re-verify before trusting output."
   - Update registry: mark old entry as `failed`, create new entry for the replacement.
   - Max re-dispatch = 1 per task.

3. **Partial-aggregate with explicit markers.** If the re-dispatch also fails (or is impossible):
   - Mark the todo node as `done` with gap documented: node text notes `INCOMPLETE: leader failed twice`.
   - Deliver partial results with a `### Gaps` section naming the incomplete task, what it was supposed to accomplish, and the failure reason.
   - Report to user with the failure flagged.

4. **Max re-dispatch = 1.** Never spawn a third attempt for the same task. Two failures is a signal to escalate to the user, not retry.

5. **Never silently incomplete.** Every failed task surfaces in the report — no hidden gaps.

This ladder follows the pattern established in `agents/architect/workflow.md:133-144` and codified in `docs/agent-prompt-writing-guide.md §7`.

---

## 8. Implementation Tasks

### Phase 3A: meta.json Configuration

| # | Task | Target File | Acceptance |
|---|------|-------------|------------|
| 3A.1 | Update `version` to `"2.0.0"` | `agents/project-manager/meta.json` | Version field reflects breaking capability change |
| 3A.2 | Update `description` to reflect dispatch capability | `agents/project-manager/meta.json` | Description no longer says "non-dispatching" |
| 3A.3 | Add `"instance"` to `tools.allow` | `agents/project-manager/meta.json` | PM has spawn_instance, send_message, list_instances, get_instance_info |
| 3A.4 | Add `"shared_meta_kv"` to `tools.allow` | `agents/project-manager/meta.json` | PM can read/write dispatch registry |
| 3A.5 | **ATOMIC deny-list edit (C3)** — Remove `"instance"`, `"shared_meta_kv"`, `"send_message"`, `"spawn_instance"`, `"terminate_instance"` from deny; ADD `"terminate_instance"`, `"council"`, `"charter"`, `"image-reader"` to deny (C1); ADD all enumerated Plane write tools to deny (C2). **These changes are ATOMIC — DO NOT split into separate commits.** | `agents/project-manager/meta.json` | No contradiction between allow and deny; PM cannot spawn charter/image-reader/terminate; PM cannot write to Plane. Single commit. |
| 3A.6 | Change `team_members` from `[]` to `["leader"]` | `agents/project-manager/meta.json` | PM authorized to spawn leader only |

### Phase 3B: rule.md Cardinals & Guidelines

> **W3:** The canonical Cardinal/Guideline text lives in `plan-overview.md`. Tasks below are implementation instructions; defer to the overview for exact wording.

| # | Task | Target File | Acceptance |
|---|------|-------------|------------|
| 3B.1 | Rewrite Cardinal #2: "No work dispatch" → canonical "Dispatch execution to `leader` only" (plan-overview.md) | `agents/project-manager/rule.md` | Cardinal permits leader dispatch; forbids direct specialist dispatch; mandates END TURN after send_message |
| 3B.2 | Update Cardinal #1 to canonical text including "and external systems (Plane)" (W2) | `agents/project-manager/rule.md` | No "v1 verbatim" labels; Cardinal #1 includes external systems |
| 3B.3 | Update Cardinal #5 parenthetical (canonical overview) | `agents/project-manager/rule.md` | References Cardinal #2 for execution boundary; no stale "leader decides dispatch" language |
| 3B.4 | Apply all 10 Guidelines per canonical Guideline set (plan-overview.md) | `agents/project-manager/rule.md` | Guidelines #1–#10 match overview exactly; #8 replaces hand-back; #9–#10 new |
| 3B.5 | Verify ≤7 Cardinals total | `agents/project-manager/rule.md` | Count is exactly 7 |

### Phase 3C: workflow.md Flows

> **W3:** Tasks for workflow.md Flows 6–8 (Roadmap, Milestones, Burndown) and Flows 1–4 Plane-awareness are consolidated with Phase 1–2 in the unified task list (`plan-overview.md` → "Unified Task List" U-PM-4 through U-PM-7). This section covers Flow 5 (Dispatch) and dispatch-specific workflow sections only.

| # | Task | Target File | Acceptance |
|---|------|-------------|------------|
| 3C.1 | Add Flow 5 — Dispatch & Delegation (C4 canonical numbering) | `agents/project-manager/workflow.md` | Complete dispatch protocol: spawn/reuse decision, hand-off format, END TURN contract |
| 3C.2 | Add dispatch registry pattern (shared_meta_kv `"pm_leader_instances"` tracking — W5 schema) | `agents/project-manager/workflow.md` | Data structure (canonical W5 schema), lifecycle, write-ordering discipline documented |
| 3C.3 | Add fan-in tracking section (todo_graph for multi-leader) | `agents/project-manager/workflow.md` | Multi-leader dispatch, node marking, aggregation gate |
| 3C.4 | Add fan-in escape valve section | `agents/project-manager/workflow.md` | Stuck-leader ladder, max re-dispatch = 1, partial-aggregate with Gaps |
| 3C.5 | Replace Closing section (consolidated with Phase 1 U-PM-6) | `agents/project-manager/workflow.md` | Hand-back retired; dispatch/assess mode summary |
| 3C.6 | Update Flow Chaining section (consolidated with Phase 2 U-PM-5) | `agents/project-manager/workflow.md` | Add: advisory flows (1-4) can trigger Flow 5 if user asks to act; Flows 6–8 chaining rules |

### Phase 3D: soul.md Identity Update

> **W3:** Consolidated with Phase 1 tasks U-PM-1 in the unified task list. These are the dispatch-specific additions only.

| # | Task | Target File | Acceptance |
|---|------|-------------|------------|
| 3D.1 | Update Status line from "stand-alone, non-dispatching" to "strategic oversight with leader dispatch" | `agents/project-manager/soul.md` | Identity matches v2 capabilities |
| 3D.2 | Update Nature point: "Non-dispatching" → "Dispatches to `leader` only" | `agents/project-manager/soul.md` | Nature reflects dispatch capability |
| 3D.3 | Update "My Role vs Leader" table | `agents/project-manager/soul.md` | Handoff row updated; PM dispatches rather than hands back |
| 3D.4 | Update "Dispatch prompts" tone line | `agents/project-manager/soul.md` | PM now constructs dispatch prompts — document the voice (what/why, not how) |

### Phase 3E: tools_note.md Update

> **W3:** Consolidated with Phase 1 task U-PM-8 in the unified task list. These are the dispatch-specific additions only.

| # | Task | Target File | Acceptance |
|---|------|-------------|------------|
| 3E.1 | Add `spawn_instance`, `send_message`, `list_instances`, `get_instance_info` to tool table | `agents/project-manager/tools_note.md` | Each tool documented with purpose and usage |
| 3E.2 | Add `shared_meta_kv` to tool table | `agents/project-manager/tools_note.md` | Documented as dispatch registry bookkeeping (key `"pm_leader_instances"` — W5 schema) |
| 3E.3 | Update "What I do NOT hold" section | `agents/project-manager/tools_note.md` | Remove instance/shared_meta_kv from denied; ADD charter, image-reader (C1), Plane write tools (C2), terminate_instance, council, self, question, mcp |

### Phase 3F: Validation

| # | Task | Target | Acceptance |
|---|------|--------|------------|
| 3F.1 | Pre-commit checklist verification | All PM agent files | Pass `docs/agent-prompt-writing-guide.md §10` checklist: no system internals, ≤7 Cardinals, cross-refs resolve, fan-in defined |
| 3F.2 | Smoke test: PM spawns leader | Live daemon | PM spawns leader → leader runs → report arrives → PM processes |
| 3F.3 | Reuse test: PM reuses COMPLETED leader | Live daemon | PM sends follow-up to COMPLETED leader → leader revives (COMPLETED→RUNNING) → report arrives |
| 3F.4 | Multi-task test: PM spawns 2 leaders | Live daemon | PM spawns 2 leaders → tracks both in registry → receives both reports → aggregates |
| 3F.5 | Error test: leader ERROR state | Live daemon | Leader goes to ERROR → PM detects via get_instance_info → PM spawns fresh leader |
| 3F.6 | Grep verification: no system internals | PM agent dir | No `meta.json`, `tools.allow`, `daemon/`, `_tool_registry`, `_auth.py` references in prompt prose |

---

## 9. Testing Strategy

### Test Categories

| Category | Scope | Method |
|----------|-------|--------|
| **Unit: agent config validation** | meta.json schema, tool resolution, team_members expansion | Verify PM's resolved tool set includes spawn_instance/send_message/list_instances/get_instance_info/shared_meta_kv and excludes terminate_instance/council/self/question/mcp/bash/edit_file/write_file |
| **Integration: spawn authorization** | `_check_team_membership` with PM as caller | Verify `spawn_instance("leader")` succeeds; `spawn_instance("developer")` returns authorization error |
| **Integration: dispatch lifecycle** | spawn → send_message → report-back → reuse | Verify full PM→leader dispatch cycle works end-to-end |
| **Integration: multi-leader fan-in** | todo_graph + shared_meta_kv coordination | Verify PM tracks multiple leaders and aggregates correctly |
| **Integration: escape valve** | leader ERROR → re-dispatch → partial-aggregate | Verify PM handles failure gracefully |
| **Prompt compliance** | agent-prompt-writing-guide checklist | Verify no system internals, ≤7 Cardinals, fan-in defined, END TURN stated |

### Test Details

**T1: Tool Resolution (unit)**
- Load PM's meta.json via the registry
- Resolve the effective tool set (allow minus deny, category expansion)
- Assert: `spawn_instance`, `send_message`, `list_instances`, `get_instance_info` in effective set
- Assert: `terminate_instance` NOT in effective set (denied by exact name)
- Assert: `shared_meta_kv` in effective set
- Assert: `bash`, `edit_file`, `write_file` NOT in effective set

**T2: Team Membership Authorization (integration)**
- Call `_check_team_membership("project-manager", "leader")` → returns None (authorized)
- Call `_check_team_membership("project-manager", "developer")` → returns error string (not in team_members)
- Call `_check_team_membership("project-manager", "tester")` → returns error string

**T3: Smoke Dispatch (integration, live daemon)**
- Spawn PM instance
- Send PM a message: "Implement a hello-world endpoint at /api/hello"
- Verify PM spawns a leader instance
- Verify PM's shared_meta_kv registry contains the task→leader mapping
- Verify leader runs and reports back
- Verify PM processes the report and updates the registry

**T4: Instance Reuse (integration, live daemon)**
- After T3 completes (leader is COMPLETED)
- Send PM: "Now add a /api/goodbye endpoint to the same area"
- Verify PM reuses the COMPLETED leader (send_message, not spawn_instance)
- Verify leader revives (COMPLETED→RUNNING)
- Verify leader runs with context from the prior task

**T5: Multi-Leader Fan-In (integration, live daemon)**
- Send PM: "Implement auth middleware AND fix the logging bug" (two unrelated tasks)
- Verify PM creates a todo graph with 2 nodes
- Verify PM spawns 2 separate leaders
- Verify PM tracks both in the registry
- Verify PM aggregates reports only when both leaders report back

**T6: Escape Valve (integration, live daemon)**
- Force a leader into ERROR state (e.g., malformed task that causes leader to fail)
- Verify PM detects ERROR via get_instance_info
- Verify PM spawns one replacement leader (max re-dispatch = 1)
- If replacement also fails, verify PM delivers partial report with ### Gaps section

**T7: Prompt Compliance (static analysis)**
- Grep PM agent dir for system internals: `meta.json`, `tools.allow`, `tools.deny`, `daemon/`, `_tool_registry`, `_auth.py`, `innate_skills`, `default_agent_versions` → expect 0 hits in prose
- Count Cardinals in rule.md → expect exactly 7
- Verify all `Cardinal #N` / `Guideline #N` cross-references resolve after renumbering

---

## 10. Risks & Mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **`send_message` has no parent-ownership check** — PM could message any instance in the system, not just its children | Medium | Low | Cardinal #2 instruction + prompt discipline: "only send_message to instances in my dispatch registry." System-level ownership check is a separate enhancement, not a blocker for this phase. |
| 2 | **`shared_meta_kv` write breaks PM's "read-only" identity** | Medium | Low | `shared_meta_kv` is a bookkeeping tool, not a code/plan/state mutation tool. It writes only to PM's own tracking namespace. Cardinal #1 ("read-only on code, plans, and project state") still holds — KV data is none of those. |
| 3 | **`max_instances=100` cumulative ceiling** — long-running PM sessions accumulate completed leader rows | Medium | Medium | PM advises the user on cleanup when tasks are fully done. PM cannot `terminate_instance` (denied) — it recommends, user decides. This is deliberate: PM is advisory on lifecycle. |
| 4 | **Dual-write gap: KV write + `send_message` not atomic** | Low | Low | Write ordering: PM writes KV AFTER `spawn_instance` returns, then immediately calls `send_message`. If killed between, registry has valid instance_id but no task dispatched — PM detects on next turn (leader is IDLE with no messages) and re-dispatches. |
| 5 | **Context compaction loses leader instance_ids** | High | Medium | Mitigated by Option 2 (shared_meta_kv registry) — instance_ids are in the registry, not in conversation history. Registry survives compaction. |
| 6 | **`instance` category auto-grants future tools** — new tools added to the `instance` category will be auto-granted to PM | Low | Low | Add a CI check: any new `@register_tool_category("instance")` decorator triggers a review of agents with `"instance"` in `tools.allow`. Not a blocker. |
| 7 | **Leader's `team_members` could add `"leader"` in the future** — enabling leader→leader recursion chains | High | Very Low | Track as a regression guard in `agents/leader/memory.md`. Currently verified: leader's `team_members` does NOT include `"leader"`. |
| 8 | **PM dispatches to a stale COMPLETED leader whose context is no longer relevant** | Medium | Medium | PM's reuse guideline (Guideline #9) instructs reuse only for related task areas. For unrelated tasks, PM spawns fresh. The registry's `task_description` field helps PM judge task affinity. |
| 9 | **Prompt rewrite introduces system internals** | Low | Medium | Phase 3F.1 (pre-commit checklist) and T7 (grep verification) catch any `meta.json`/`daemon/`/`_tool_registry` references in prompt prose before merge. |

---

## Research Insights

Key findings from codebase exploration and the preceding architecture-dispatch.md that shaped this plan:

- **Instance reuse is a first-class system capability** — the revive-fix (`instance_messaging.py:1486-1510`) handles COMPLETED→RUNNING transition. This is not a hack; it's designed infrastructure (`architecture-dispatch.md` §1).
- **`_check_team_membership` checks the caller's team_members, not the spawned agent's** (`_auth.py:43-192`) — so PM only needs `"leader"` in its own `team_members`; no changes to leader's config are required.
- **`send_message` rejects TERMINATED and ERROR but allows COMPLETED** (`instance.py:1661-1668`) — PM can reuse completed leaders without any special handling.
- **`_register_child_completion_watcher` keys on permanent `parent_id`** (`instance.py:484`) — the watcher re-registers correctly on reuse because `parent_id` is never cleared, even after `instance_hierarchy` cleanup.
- **shared_meta_kv is partitioned by `context_key` (tree-root instance_id)** — PM and all its leaders share the same KV namespace, which is desirable for coordination.
- **The `instance` category expands to exactly 5 tools** (`instance.py:971`) — PM needs 4 of them; `terminate_instance` is denied by exact name.
- **Leader tracks reusable instances via conversation history** (`leader/workflow.md:141-143`) — but this pattern does NOT survive context compaction, making it unsuitable for PM's longer-running strategic task coordination. `shared_meta_kv` is the correct choice for PM.

---

## Open Questions

1. **Should PM always reuse leaders, or spawn fresh per task?** — Plan recommends: reuse for the same task area (context continuity), spawn fresh for unrelated tasks. PM decides based on task affinity using the registry's `task_description` field. This is a prompt-level guideline, not a system constraint.

2. **Should PM spawn multiple leaders in parallel?** — The system supports up to 50 concurrent children. PM's strategic oversight role suggests sequential dispatch by default, with parallel dispatch when the user explicitly requests it or when tasks are clearly independent. The prompt should default to sequential and allow parallel as an exception.

3. **`shared_meta_kv` cross-instance visibility when PM is spawned by Ari** — If PM is a child of Ari (not a root), all PMs under the same Ari session share the same `context_key`. Is this desired (cross-PM coordination) or a privacy concern? Likely a non-issue since PM is typically spawned directly by the user as a root instance, but worth documenting.

4. **Should the `description` field in meta.json mention dispatch?** — Plan recommends yes: "Strategic project oversight with leader dispatch." This affects how the agent appears in UI and dispatch menus.

---

## Coupling

This phase is **tightly coupled** to Phases 1 and 2 of the PM system improvement (not in scope here), but has specific internal coupling:

> **W4 Merge Order:** Phase 3 merges together with Phase 1+2 in **PR 1**. Phase 4 (MCP improvements) merges **AFTER** PR 1 in PR 2.

| Components | Coupling | Notes |
|------------|----------|-------|
| `meta.json` ↔ `rule.md` Cardinal #2 | **Tight** | Cardinal #2 references "agents in my `team_members`" — must match meta.json's `team_members: ["leader"]` |
| `meta.json` deny-list ↔ C1/C2 fixes | **Tight** | charter, image-reader, Plane write tools all denied in same atomic commit (C3) |
| `meta.json` ↔ `tools_note.md` | **Tight** | tools_note table must list exactly the tools the allow/deny config resolves to |
| `workflow.md` Flow 5 ↔ `rule.md` Cardinal #2 | **Tight** | Flow 5's END TURN contract is cardinalized in #2; both must agree |
| `workflow.md` escape valve ↔ `rule.md` Guideline #10 | **Tight** | The escape valve ladder and the "never silently incomplete" obligation must cross-reference |
| `soul.md` identity ↔ `rule.md` Cardinals | **Loose** | Identity describes who PM is; Cardinals constrain behavior. They must not contradict but are independently editable. |
| PM changes ↔ Leader agent | **Independent** | No changes to leader's files are needed. Leader already supports being spawned and messaged. |
| PM changes ↔ Daemon code | **Independent** | No daemon code changes. All infrastructure already exists. |
