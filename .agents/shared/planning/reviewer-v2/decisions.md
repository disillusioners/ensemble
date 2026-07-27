# Architecture Decisions: Reviewer v2

## D1: Reviewer is a Dispatcher/Controller (Not a Direct Reviewer)

**Decision:** Reviewer[v2] plans reviews and delegates all analysis to worker instances and governor council. It never analyzes code directly.

**Rationale:**
- Mirrors the proven tester pattern (test-leader + worker dispatch)
- Enables clean skill attribution (one skill per worker = clean metrics)
- Reviewer stays focused on coordination, aggregation, and reporting
- Allows parallel review of independent modules

**Alternatives Considered:**
| Alternative | Rejected Because |
|-------------|------------------|
| Reviewer reads code directly + summarizes | Violates separation of concerns; reviewer becomes bottleneck; no parallelism; no skill evolution data |
| Reviewer uses opencode (v1 approach) | Explicitly forbidden by requirements; opencode is heavy external dependency |

---

## D2: Use `convene_council` for Deep Review (Not `spawn_councilor` Directly)

**Decision:** Reviewer[v2] invokes the governor council via the `convene_council` tool, which spawns a governor child instance that then convenes councilors.

**Rationale (verified from source code):**

Examined `daemon/tools/instance.py` lines 901-956:

```python
@register_tool_category("council")
@tool
async def convene_council(
    councilor_agent_id: str,
    request: str,
    models: list[str] | None = None,
    max_councilors: int | None = None,
    instance_name: str | None = None,
) -> dict:
    # convene_council requires "governor" in the caller's team_members.
    membership_error = _check_team_membership(caller_agent_id, "governor")
    ...
    # NO W1 identity guard: any caller authorized by team_members may convene.
    gov_instance_id, _ = manager.spawn_instance(
        agent_id="governor",
        parent_id=current_instance_id,
        instance_name=instance_name,
    )
    await manager.enqueue_message(instance_id=gov_instance_id, message=message_text)
```

Key facts:
1. `convene_council` has **NO identity guard** — unlike `spawn_councilor` which requires `caller_agent_id == "governor"`
2. It only requires `"governor"` in the caller's team_members (line 924)
3. Required params: `councilor_agent_id`, `request`. Optional: `models`, `max_councilors` (controls councilors WITHIN one council, NOT the number of councils), `instance_name`
4. It spawns a governor child and enqueues the review request
5. The governor then convenes the actual councilors using its own `spawn_councilor` tool
6. The result arrives **asynchronously** as a completion report

**Why not `spawn_councilor` directly?**
`spawn_councilor` is identity-guarded: `if caller_agent_id != "governor": raise ValueError(...)`. Only the governor agent itself can call it. Reviewer is not the governor. `convene_council` is the designed entry point for non-governor agents to access the council system.

**Alternatives Considered:**
| Alternative | Rejected Because |
|-------------|------------------|
| `spawn_councilor` directly | Identity guard raises ValueError: "council tools are restricted to the governor agent" |
| Spawn governor as team member + send_message | Reinvents what convene_council already does; more fragile; loses the structured council request format |
| Keep opencode council (council=True) | Explicitly forbidden by requirements; opencode dependency |

---

## D3: `"council"` in tools.allow Auto-Implies Governor in team_members

**Decision:** Reviewer[v2] declares `"council"` in `tools.allow`. This auto-implies `"governor"` in its effective team_members. We also list `"governor"` explicitly in team_members for clarity.

**Rationale (verified from source code):**

Examined `daemon/tools/_auth.py` lines 35-40:
```python
TOOL_REQUIRED_AGENTS: dict[str, list[str]] = {
    "knowledge": ["explorer", "kb-writer"],
    "chart": ["charter"],
    "image": ["image-reader"],
    "council": ["governor"],   # ← council category requires governor
}
```

And lines 126-131 (auto-derivation):
```python
implied_members: list[str] = []
if caller_meta.tools and caller_meta.tools.allow:
    for category, required_agents in TOOL_REQUIRED_AGENTS.items():
        if category in caller_meta.tools.allow:
            implied_members.extend(required_agents)
```

So declaring `"council"` in `tools.allow` makes `tools.allow` the single source of truth — `governor` is automatically in the effective team_members allow-set. The explicit `team_members: ["governor"]` entry is redundant but improves readability.

---

## D4: Default Councilor Agent = `wanderer` (Read-Only Investigator)

**Decision:** When reviewer[v2] convenes a council, it defaults to `councilor_agent_id="wanderer"`.

**Rationale:**
| Candidate | Pros | Cons |
|-----------|------|------|
| **`wanderer`** ✅ | Purpose-built read-only investigator; has filesystem+grep+glob+explore+web search; designed for deep investigation; no mutation tools | None significant |
| `coder` | Knows code patterns | Has write tools — councilors should be read-only; governor enforces read-only directive but coder's nature invites mutation |
| `reviewer` (self) | Already a reviewer | **Recursion risk** — if reviewer is a councilor, and reviewer convenes councils, infinite recursion possible |
| `developer` | Knows codebase | Has write tools; too execution-focused |

`wanderer`'s meta.json confirms: read-only tools (`bash`, `filesystem`, `explore`, `mcp`, `rag`, `instance`), team_members=["coder"] for bounded delegation. It's the ideal read-only analysis councilor.

**Note:** The governor's workflow always prepends a read-only directive to every councilor dispatch, so even coder would be instructed to be read-only. But wanderer's default tool surface is naturally read-only, making enforcement cleaner.

---

## D5: 6 Review Skills (1 Planning + 5 Execution)

**Decision:** Create 6 skills — 1 auto-loaded strategy skill for the reviewer + 5 on-demand execution skills for workers.

**Rationale:**
- Mirrors tester's proven pattern: 1 auto-loaded `test-strategy` (planning) + 8 execution skills
- Reviewer's `review-strategy` is auto-loaded into the reviewer's own context (never dispatched to workers)
- Each execution skill is loaded on exactly ONE worker via `load_skill` — clean attribution
- Skills are evolvable: skill_feedback drives improvement, skill_search enables discovery

**Skill taxonomy:**

| Skill | Category | auto_load | Used By | Purpose |
|-------|----------|-----------|---------|---------|
| review-strategy | planning | true | Reviewer (self) | Scope, blast-radius, dispatch planning |
| code-review | execution | false | Worker | Correctness/safety/structure/clarity |
| plan-review | execution | false | Worker | Completeness/feasibility/risks |
| architecture-review | execution | false | Worker | Patterns/boundaries/scalability |
| security-review | execution | false | Worker | Vulnerabilities/injection/auth |
| pr-review | execution | false | Worker | Diff quality/regressions |

**Why these 5 execution skills?**
1. **code-review** — the most common request; covers general code quality
2. **plan-review** — reviewer[v1] already had this mode; plans need completeness/feasibility checks
3. **architecture-review** — reviewer[v1] had this; component design and boundaries need specialized focus
4. **security-review** — distinct enough focus (OWASP, injection, crypto) to warrant a dedicated skill
5. **pr-review** — the diff-focused workflow is distinct from full code review; focuses on changes, regressions, commit hygiene

**Alternatives Considered:**
| Alternative | Rejected Because |
|-------------|------------------|
| 1 generic "review" skill | Too broad; workers lack focused guidance; no skill evolution granularity |
| 10+ hyper-specific skills | Over-fragmentation; maintenance burden; diminishing returns |
| No skills (reviewer does everything) | Violates dispatcher pattern; no skill evolution; no parallelism |

---

## D6: Keep `"dynamic-skill"` in innate_skills + skill_injection: true

**Decision:** Reviewer[v2] has `innate_skills: ["todo", "chart", "dynamic-skill"]` and `skill_injection: true`.

**Rationale:**
- `"dynamic-skill"` enables the 6 skill tools: skill_search, skill_list, skill_view, skill_create, skill_fix, skill_feedback
- `skill_injection: true` allows the runtime to auto-inject relevant review skills into the reviewer's context
- Workers (which also have `dynamic-skill`) will auto-inject relevant skills too
- This enables the skill evolution system: workers report `skill_feedback(usefulness, improvement_note)` which drives skill improvement over time
- `"chart"` kept for architecture reviews that may benefit from diagram generation
- `"todo"` kept for task tracking during complex multi-worker reviews

**From tester's proven setup:**
Tester has `"innate_skills": ["opencode", "test-pack", "todo", "dynamic-skill"]` and `"skill_injection": true`. Reviewer[v2] follows the same pattern but replaces `"opencode"` with review-specific skills and removes `"test-pack"`.

---

## D7: Remove `"opencode"` from innate_skills Entirely

**Decision:** Reviewer[v2] has NO opencode skill. No `external_opencode_*` tool calls anywhere in the agent definition.

**Rationale:**
- Core requirement from user: "Remove opencode dependency — no more opencode sessions for analysis"
- The opencode skill (at `agents/_prompt_system/innate-skills/opencode/skill.md`) is what teaches agents to use `external_opencode_*` tools
- Without it in innate_skills, the reviewer's system prompt won't include opencode instructions
- Workers handle all analysis with filesystem+bash tools directly
- For very large codebases, the reviewer spawns multiple workers partitioned by module (same as tester spawns multiple test-pack workers)

**Impact:** Reviewer[v2] loses the ability to delegate to opencode's orchestrator agent. This is intentional — workers with review skills provide a lighter-weight, more controllable, and skill-evolvable alternative.

---

## D8: Workers Are Read-Only During Reviews

**Decision:** Review skills (code-review, plan-review, etc.) enforce read-only behavior. Workers analyze and report findings but do NOT modify files.

**Rationale:**
- Reviews are inherently read-only — you analyze, you report, you don't change
- Parallel workers writing would cause conflicts (same concern as governor council)
- The worker agent's SemiAuto mode already gates destructive operations, but review skills make this explicit
- Each review skill includes a read-only directive in its content

**Contrast with tester:** Tester's workers DO modify code (quick-fix skill writes fixes). Review workers never modify — they only report findings. The reviewer (or a downstream agent) decides what to act on.

---

## D9: Non-Blocking Dispatch Pattern (END TURN After Dispatch)

**Decision:** After dispatching workers (spawn_instance + send_message) OR convening a council, the reviewer ENDS ITS TURN. It does NOT poll for results.

**Rationale (from tester workflow.md line 67):**
> "After send_message, END YOUR TURN. Do NOT poll get_instance_info, do NOT sleep/bash waiting for the worker. The system resumes your turn automatically the moment each worker reports — you will receive every worker's report as a new message. Holding your turn open blocks report delivery and deadlocks the run."

This same pattern applies to `convene_council`:
- `convene_council` is explicitly non-blocking (returns immediately with `{"status": "convened", "hint": "Watch for the completion report"}`)
- The governor processes the council asynchronously
- The synthesized result arrives as a new message to the reviewer

**Workflow shape:**
```
Reviewer plans → dispatches workers/council → ENDS TURN
                                          ↓
Workers complete → reports arrive as new messages → Reviewer aggregates → reports
```

---

## Summary: How the Architecture Replaces v1

| v1 (opencode-based) | v2 (worker+skill+council) |
|---------------------|---------------------------|
| `external_opencode_init_session` + `send_message` | `spawn_instance(agent="worker")` + `send_message(load_skill="...")` |
| Parallel opencode sessions (`review-<area>`) | Parallel worker instances (`review-worker-<area>`) |
| `council=True` on opencode send_message | `convene_council(councilor_agent_id="wanderer", ...)` |
| `opencode-skill` wait tools | END TURN — async report delivery |
| `review-aggregate` opencode session | Reviewer aggregates directly (it's the controller) |
| `review-deep` opencode council session | Governor council spawned via `convene_council` |
| opencode analyzes code | Worker with review skill analyzes code |
| No skill evolution | skill_feedback drives review skill improvement |
| Heavy external dependency (opencode service) | Lightweight native dispatch (worker instances) |
