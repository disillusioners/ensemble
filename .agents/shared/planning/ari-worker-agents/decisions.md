# Key Design Decisions & Rationale

## D1: Ari is a Jober-Hybrid, Not a Pure Jober

**Decision**: Ari has BOTH direct execution tools (`bash`, `filesystem`) AND job orchestration tools (`job` category). This makes her a hybrid — a new pattern that doesn't exist in the current agent roster.

**Rationale**:
- The existing `jober` agent is dispatch-only: it cannot run bash commands or read files. Its rule.md explicitly forbids this ("❌ Never Run Bash Commands or Use Filesystem").
- Ari's use case requires her to handle quick small tasks herself (lookups, file reads, simple commands) — this is explicitly listed as Capability #1 in the requirements.
- Making her dispatch-only (like jober) would mean even "what's in this file?" creates a job — unacceptable latency and overhead for a user-facing assistant.
- Making her direct-only (like developer) would mean she can't delegate complex work — missing Capability #2 and #3.
- **Solution**: Give her both, and rely on a clear `rule.md` triage decision tree to prevent confusion.

**Trade-offs**:
| Aspect | Pro | Con |
|--------|-----|-----|
| Flexibility | Can handle any task type | Agent must correctly triage |
| Latency | Quick tasks are instant | Slightly larger system prompt |
| Complexity | Single entry point for user | Behavior depends on prompt quality |

**Mitigation**: The `rule.md` CRITICAL triage rule provides a clear decision tree:
```
Quick task (≤5 steps)? → DO DIRECTLY
Software dev? → DELEGATE TO LEADER  
OpenSpace task? → DELEGATE TO WORKER
Ambiguous? → ASK USER
```

---

## D2: Worker is NOT a Jober

**Decision**: Worker receives jobs and executes them via OpenSpace tools directly. It does NOT have the `job` tool category and does NOT create sub-jobs.

**Rationale**:
- Worker's purpose is to be the OpenSpace execution specialist — it wraps the 4 `mcp_openspace_*` tools.
- If Worker were also a jober, it could dispatch to other agents — creating unnecessary complexity and potential circular dispatch.
- Worker is a terminal executor in the dispatch chain: Ari → Worker → OpenSpace MCP → done.
- This mirrors the DevOps/Giter pattern: specialists that receive work and execute it, not dispatch further.

**Implementation**: `tools.allow` does NOT include `"job"`. `innate_skills` does NOT include `"job-orchestration"`.

---

## D3: No team_members for Ari or Worker — Job-Only Dispatch

**Decision**: Both Ari and Worker have **no `team_members`** field (or empty). Neither agent uses `spawn_instance`. All dispatch is via `job_create` only.

**Rationale**:

**Why team_members is unnecessary for these agents:**

The two-tier authorization model (verified from source code):
- `team_members` controls the **`spawn_instance` tool** only. `spawn_instance` (instance.py:602) calls `_check_team_membership()` — a hard deny-by-default gate: missing/empty list → deny everything.
- `job_create` (job_queue.py:302-351) calls `job_service.enqueue()` directly with **NO** team_members check. An agent can `job_create(agent_id="any-agent")` regardless of its team_members list.

Since neither Ari nor Worker has `instance` tools (no `spawn_instance` — see D8), and both dispatch exclusively via `job_create` (which doesn't check team_members), the `team_members` field serves no functional purpose for either agent.

**Decision: omit `team_members` entirely** from both meta.json files. This:
1. Keeps config clean — no dead fields
2. Documents the design intent — these agents are job-only dispatchers/executors
3. Avoids implying spawn_instance capability that doesn't exist

**Previous version (superseded):** The original D3 listed all 9 specialists + leader + worker for Ari's team_members. This was based on the incorrect assumption that team_members gates job_create. After source-code verification revealed that job_create has no team_members check, and the decision to remove all spawn_instance capability (D8), team_members became purely dead config. It is now omitted.

---

## D4: Worker Uses Explicit mcp_openspace_* Tool Listing

**Decision**: Worker's `tools.allow` explicitly lists all 4 OpenSpace MCP tools by full name:
```json
"mcp_openspace_execute_task",
"mcp_openspace_search_skills",
"mcp_openspace_fix_skill",
"mcp_openspace_upload_skill"
```

**Rationale**:
- This is a **hard constraint** of the OpenSpace MCP integration. The `openspace` innate skill is **instructional-only** — it teaches the agent HOW to use the tools but does NOT auto-grant tool permissions.
- This is documented in `test_openspace_skill_loading.py` lines 13-18: "OpenSpace is an instructional-only innate skill... The 4 OpenSpace tools must be granted explicitly via `tools.allow`."
- The `INNATE_SKILL_TOOL_CATEGORIES` mapping (instance.py:52-56) auto-grants tools for `opencode` → `external_opencode`, `chart` → `chart`, and `todo` → `todo`. The `openspace` skill is NOT in this mapping — it is instructional-only.
- **Why not use `mcp_openspace_*` wildcard?** While the system supports prefix-based access, explicit listing is clearer, more auditable, and matches the pattern documented in the skill.md "Agent Configuration Note" section.

---

## D5: Ari Has the `openspace` Innate Skill But NOT the OpenSpace Tools

**Decision**: Ari's `innate_skills` includes `"openspace"` (instructional), but her `tools.allow` does NOT include any `mcp_openspace_*` tools.

**Rationale**:
- Ari needs to **understand** what OpenSpace can do so she can make intelligent routing decisions ("this task needs OpenSpace → dispatch to Worker").
- The `openspace` innate skill teaches her the 4 tool names, their purposes, cost considerations, and when to use them.
- But she should NOT execute OpenSpace tools directly — that's Worker's job. This enforces separation of concerns.
- If Ari had the tools too, there'd be no reason for Worker to exist, and Ari's prompt would be even longer.
- **This creates clean separation**: Ari = routing intelligence + quick execution; Worker = OpenSpace execution.

**Implementation**:
- `innate_skills: [..., "openspace"]` — loads the instructional skill into her prompt
- `tools.allow: [..., "job", "bash", "filesystem", ...]` — does NOT include `mcp_openspace_*`

---

## D6: Phase Order — Worker First, Then Ari

**Decision**: Build Worker (Phase 1) before Ari (Phase 2).

**Rationale**:
- Worker has zero dependencies on other new agents — it's a self-contained specialist.
- Ari dispatches to `worker` via `job_create`. Building Worker first means Phase 2 can run the full test suite with both agents present.
- Worker is simpler (4 files, focused scope), making it a good warm-up before the more complex Ari (6 files, jober-hybrid pattern, personality design, autonomy model).
- If we built Ari first, her tests would need to account for a missing Worker agent (awkward conditional test logic).

---

## D7: Leader team_members — Do NOT Add Ari/Worker

**Decision**: Do NOT add `"ari"` or `"worker"` to `leader`'s `team_members`. Leader's meta.json stays unchanged.

**Rationale**:

The dispatch graph should be **acyclic and unidirectional**:
```
User → Ari → {Leader → specialists, Worker → OpenSpace}
```

**The circular-dispatch concern is real, but the mechanism is tool topology — NOT team_members.**

Since `job_create` does not check `team_members` (see D3), adding `ari`/`worker` to leader's `team_members` would NOT by itself create a job-dispatch cycle. The real protection is **tool topology separation**:

| Agent | Has `instance` tools? | Has `job` tools? | Dispatch mechanism |
|-------|----------------------|-------------------|--------------------|
| Leader | ✅ | ❌ | `spawn_instance` + `send_message` (gated by team_members) |
| Ari | ❌ | ✅ | `job_create` (NOT gated by team_members) |

Leader can only dispatch via `spawn_instance` (which IS team_members-gated). Ari can only dispatch via `job_create` (which is NOT gated). They use **different, non-overlapping dispatch mechanisms**, so there is no bidirectional path — even though `job_create` itself is unconstrained.

**Why still don't add ari/worker to leader's team_members:**

| Concern | Analysis |
|---------|----------|
| **Design philosophy** | Ari is a **front door** — a one-way entry point. She's not an internal team member. Adding her to leader's team_members would imply leader can spawn her, which violates the front-door design. |
| **Leader's scope** | Leader coordinates the dev team. It doesn't need to dispatch to a user-facing VA. |
| **Defense in depth** | Even though `job_create` is unconstrained today, keeping `team_members` clean provides a second layer: if leader ever gains `job` tools, it still can't spawn Ari. |
| **Worker access for leader** | If leader ever needs OpenSpace, it's a future enhancement. Currently, leader→devops→infra covers infrastructure. |

**Exception consideration**: If a future use case emerges where the leader needs OpenSpace capabilities (e.g., "leader needs to search for a reusable skill during development"), we can add ONLY `"worker"` (not `"ari"`) to leader's team_members. This avoids the cycle while enabling the capability. But this is out of scope for this plan.

**Result**: `agents/leader/meta.json` is NOT modified. Zero changes to existing agents.

---

## D8: Neither Ari Nor Worker Has `instance` Tool Category

**Decision**: Neither Ari nor Worker has `"instance"` in `tools.allow`. Both dispatch/receive work via the job system only. Neither has `spawn_instance`, `send_message`, or any instance management tools.

**Rationale**:
- Ari dispatches work via the **job system** (`job_create`, `watch_job`), NOT via direct instance spawning.
- Worker receives work via job dispatch and executes it — it doesn't spawn sub-agents.
- The `instance` tool category is for spawning/messaging agent instances directly — that's leader's pattern.
- Ari uses `job_create(agent_id=..., watch=True)` which creates a job targeting an agent. The system handles instance creation internally.
- This keeps the tool surface focused: `job` for delegation (Ari), `bash`/`filesystem`/OpenSpace for execution (both).
- The existing jober also does NOT have `instance` tools — it uses `job` tools exclusively.

**Comparison**:
| Agent | Has `instance`? | Has `job`? | Why |
|-------|----------------|------------|-----|
| Leader | ✅ | ❌ | Coordinates via direct instance spawn + send_message |
| Jober | ❌ | ✅ | Dispatches via job system |
| **Ari** | ❌ | ✅ | Dispatches via job system (jober pattern) |
| **Worker** | ❌ | ❌ | Receives jobs, executes directly |

**Consequence**: Since neither agent has `instance` tools, neither needs `team_members` (which only gates `spawn_instance`). See D3.

---

## D9: Worker Has Basic Tools (bash, filesystem) for Context

**Decision**: Worker's `tools.allow` includes `bash`, `filesystem`, `time`, `self`, `help` — not just OpenSpace tools.

**Rationale**:
- Worker may receive tasks that reference files (e.g., "extract data from `/data/report.pdf`"). It needs `filesystem` to read/verify these files.
- Worker may need to run quick local commands as part of task setup (e.g., check if a directory exists before OpenSpace writes to it).
- The OpenSpace skill.md explicitly says "Do it yourself" for trivial tasks — Worker needs basic tools to follow this guidance.
- This mirrors the DevOps pattern: specialists have `bash` + `filesystem` for context, plus their specialty tools.

**Constraint**: Worker's `rule.md` must emphasize that `bash`/`filesystem` are for **context and quick tasks only** — the primary execution path is OpenSpace tools.

---

## D10: Testing Strategy — Mirror test_devops_agent.py

**Decision**: Both agent test suites mirror the comprehensive `test_devops_agent.py` pattern (62 tests for DevOps).

**Rationale**:
- `test_devops_agent.py` is the gold standard for agent testing in this project — it covers auto-discovery, meta.json validation, tool filter, prompt composition, and leader integration.
- Following this pattern ensures consistency and catches the same categories of bugs.
- The test structure (class-per-concern) makes failures easy to diagnose.

**Test structure**:
```
TestXxxAutoDiscovery     — Is the agent found by the registry?
TestXxxMetaJsonValidation — Is meta.json valid and correct?
TestXxxToolFilter        — Are tools parsed and correct?
TestXxxPromptComposition — Do soul/rule/workflow exist? Do innate skills load?
TestXxxNoTeamMembers     — Confirm no team_members (no spawn_instance)
```

---

## D11: Autonomy Model — Ari TrueAuto, Worker SemiAuto

**Decision**: Ari operates in **TrueAuto mode** by default (full autonomy, only escalates truly critical/breaking things). Worker operates in **SemiAuto mode** by default (requests permission for breaking/dangerous changes).

**Rationale**:

Ari's TrueAuto mode mirrors the Leader's TrueAuto pattern:
- Ari is the user's front door — she should be smart, fast, and autonomous
- She makes ALL decisions about planning, trade-offs, and implementation details
- She ONLY pauses for very important or breaking things (destructive ops, security-sensitive changes, irreversible operations)
- This provides a smooth user experience — the user doesn't get interrupted for routine decisions

Worker's SemiAuto mode provides safety for OpenSpace operations:
- OpenSpace's `execute_task` is autonomous and can cause real changes (file mutations, data processing)
- Worker must check for breaking/dangerous changes BEFORE executing
- When a breaking change is detected, Worker reports back requesting permission (completes its turn)
- Ari receives the report and decides: relay to user (critical) or approve via `job_continue` (safe)

**The escalation flow** (Ari → Worker → permission → execution):
```
1. Ari: job_create(agent_id="worker", message="task", watch=True)
2. Worker: detects breaking change → reports "Need permission because [reasons]"
   (Worker completes turn — Ari receives [JOB_EVENT])
3. Ari: evaluates:
   ├─ Critical → relay to user, wait for user decision
   └─ Safe → job_continue(worker_job_id, message="Approved. Proceed in TrueAuto mode.")
4. Worker: receives permission → executes → reports completion
5. Ari: reports result to user
```

**Ari can elevate Worker to TrueAuto**: If Ari judges the task is small, read-only, or non-breaking, she can grant Worker TrueAuto mode via `job_continue` (e.g., "Proceed autonomously, this is safe — TrueAuto mode"). Worker then proceeds without stopping for breaking-change checks for the remainder of that job's context.

**Why this design works:**
- Mirrors the Leader → Developer escalation pattern, but through job tools (`job_continue` instead of `send_message`)
- Worker doesn't need `instance` tools — it reports by completing its turn; Ari responds via `job_continue`
- Ari is the smart decision-maker: she evaluates Worker's permission requests and decides autonomously (TrueAuto), only escalating to the user when truly critical
- This creates a clean delegation chain: User → Ari (TrueAuto decisions) → Worker (SemiAuto execution) → OpenSpace

**Implementation notes:**
- Ari's `job_continue` sends a new message to Worker's existing instance (preserving context)
- The `job_continue` returns a new `job_id` to watch (per job-orchestration skill.md "Handle Semantics")
- Worker's TrueAuto override applies only to the current job context — subsequent jobs start fresh in SemiAuto

---

## Summary Decision Matrix

| Decision | Choice | Risk Level | Reversibility |
|----------|--------|------------|---------------|
| D1: Ari jober-hybrid | Both job + bash/filesystem | Medium (triage complexity) | Easy (adjust meta.json) |
| D2: Worker not jober | No job tools | Low | Easy |
| D3: No team_members | Omit entirely (job-only dispatch) | None | Easy (add later if needed) |
| D4: Worker explicit MCP tools | Full tool names listed | None (constraint) | N/A (required) |
| D5: Ari has openspace skill, not tools | Instructional only | Low | Easy |
| D6: Phase order | Worker → Ari → Integration | None | N/A (process) |
| D7: Leader unchanged | No team_members update | None | Easy (add later if needed) |
| D8: Neither has instance tools | job-only / receive-only | Low | Easy |
| D9: Worker has basic tools | bash + filesystem for context | Low | Easy |
| D10: Test pattern | Mirror test_devops_agent.py | None | N/A (best practice) |
| D11: Autonomy model | Ari TrueAuto, Worker SemiAuto | Medium (escalation logic) | Easy (adjust prompts) |
