# Phase 1: Reviewer v2 Agent Core Files

## Objective
Create the complete `agents/reviewer[v2]/` directory with 5 agent definition files, 1 skill manifest, and 6 skill templates — a self-contained review dispatcher that delegates to skill-equipped workers and convenes governor councils for deep review.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: N/A
- **Shared files with other phases**: none
- **Why this coupling**: Single-phase implementation; all files created together

## Context
- **One backend change required (C1):** `SkillSeedService.seed_all()` must be fixed to parse version tags so reviewer[v2]'s skills seed under the resolved base id `reviewer`. Without this, the auto-loaded `review-strategy` skill SILENTLY never loads.
- All other infrastructure already exists: `load_skill` dispatch, `convene_council` tool, governor council system, skill bank seeding (after the C1 fix)
- Reference patterns: tester (dispatcher+skills), developer[v2] (versioning — but has no skill-set.yaml, so the C1 path is UNTESTED until now), governor (council)
- Versioning: directory `reviewer[v2]` → registry parses base="reviewer" + tag="v2" via `_parse_agent_dir_name()` in `daemon/registry.py`

## Tasks

### Task Group A: Agent Definition Files

| # | Task | Details | Key File |
|---|------|---------|----------|
| 1 | Create `meta.json` | Agent config — see exact spec below | `agents/reviewer[v2]/meta.json` |
| 2 | Create `soul.md` | Dispatcher identity, modes, responsibilities | `agents/reviewer[v2]/soul.md` |
| 3 | Create `rule.md` | Review conduct, dispatch rules, council invocation rules, severity | `agents/reviewer[v2]/rule.md` |
| 4 | Create `workflow.md` | Full workflow: plan → dispatch → collect → aggregate; deep-review via council | `agents/reviewer[v2]/workflow.md` |
| 5 | Create `tools_note.md` | Tool reference for instance dispatch, council, filesystem, knowledge | `agents/reviewer[v2]/tools_note.md` |

### Task Group B: Skill Bank Files

| # | Task | Details | Key File |
|---|------|---------|----------|
| 6 | Create `skill-set.yaml` | Manifest registering 6 skills | `agents/reviewer[v2]/skill-set.yaml` |
| 7 | Create `review-strategy.md` | Auto-loaded planning skill (blast-radius, scope, dispatch planning) | `agents/reviewer[v2]/skills-template/review-strategy.md` |
| 8 | Create `code-review.md` | Worker skill: correctness/safety/structure/clarity checks | `agents/reviewer[v2]/skills-template/code-review.md` |
| 9 | Create `plan-review.md` | Worker skill: completeness/feasibility/risks | `agents/reviewer[v2]/skills-template/plan-review.md` |
| 10 | Create `architecture-review.md` | Worker skill: patterns/boundaries/scalability | `agents/reviewer[v2]/skills-template/architecture-review.md` |
| 11 | Create `security-review.md` | Worker skill: vulnerabilities/injection/auth/authz | `agents/reviewer[v2]/skills-template/security-review.md` |
| 12 | Create `pr-review.md` | Worker skill: diff/PR quality, regression checks | `agents/reviewer[v2]/skills-template/pr-review.md` |

### Task Group C: Backend Fix (CRITICAL — C1)

| # | Task | Details | Key File |
|---|------|---------|----------|
| 13 | **Fix skill-seed agent_id mismatch for versioned agents** | `SkillSeedService.seed_all()` keys skills by literal dir name (`"reviewer[v2]"`) but instances resolve to base id (`"reviewer"`), so auto_load skills SILENTLY never load. See full spec below. | `daemon/services/skill_seed_service.py` |
| 14 | **Verify W1: question-tool surfacing** | Confirm whether spawned governor/worker question-packs propagate to the reviewer parent. If not, document the read-only-dispatcher rationale for omitting `"question"` from tools.allow. | `agents/reviewer[v2]/meta.json` + doc note in `tools_note.md` |

## Key Files
- `agents/reviewer[v2]/meta.json` — configuration gateway (tools, skills, team_members)
- `agents/reviewer[v2]/workflow.md` — operational manual (most detailed file)
- `agents/reviewer[v2]/skills-template/review-strategy.md` — reviewer's own planning brain
- `daemon/services/skill_seed_service.py` — **backend fix target (C1): versioned-agent seed-key mismatch**

## Constraints
- NO `"opencode"` anywhere in the agent definition
- Follow established conventions: frontmatter in skills, YAML in skill-set.yaml, JSON in meta.json
- Councilor agent defaults to `wanderer` (read-only) — never use reviewer-as-councilor
- Workers are read-only during review — skill content must enforce no-mutations
- Reviewer itself is a read-only dispatcher — `tools.allow` excludes `"db"` (category contains mutating ops `db_conn_add`/`db_conn_delete`); use `knowledge` + `explore` for project-state queries
- After dispatching workers OR convening council: END TURN (non-blocking pattern)
- **Backend fix (C1) MUST land before the agent files are useful** — without it, auto_load skills never load for reviewer[v2]

## Deliverables
- [ ] All 12 agent/skill files created with correct content
- [ ] Backend fix applied to `skill_seed_service.py` (C1) + new/updated unit test covering versioned-dir seeding
- [ ] `meta.json` valid JSON, no opencode references, no `"db"` in tools.allow
- [ ] `skill-set.yaml` valid YAML with 6 skill entries
- [ ] All skill templates have frontmatter (version, category, auto_load)
- [ ] workflow.md has dispatch code examples AND council invocation example
- [ ] workflow.md documents the `todo_graph_create` multi-worker fan-in step (W3)
- [ ] W1 verification recorded: `question` tool surfacing investigated and rationale documented in tools_note.md

---

## Detailed File Specifications

### File 1: `meta.json`

```json
{
  "id": "reviewer",
  "name": "Reviewer",
  "description": "Review dispatcher — plans reviews, delegates to skill-equipped workers, convenes governor council for deep review",
  "icon": "🔍",
  "color": "accent-rose",
  "version": "2.0.0",
  "innate_skills": ["todo", "chart", "dynamic-skill"],
  "skill_injection": true,
  "no_force_explore": true,
  "context_injection": true,
  "tools": {
    "allow": ["instance", "council", "bash", "proc", "filesystem", "time", "self", "help", "image", "knowledge", "mcp", "context", "shared_context"]
  },
  "team_members": ["worker", "explorer", "governor"]
}
```

**Field rationale:**
| Field | Value | Why |
|-------|-------|-----|
| `innate_skills` | `["todo", "chart", "dynamic-skill"]` | todo=task tracking (incl. multi-worker fan-in per W3), chart=diagram gen for architecture review, dynamic-skill=skill evolution + skill_search/feedback |
| `skill_injection: true` | | Enables auto-injection of review skills into reviewer's context |
| `tools.allow` includes `instance` | | Needed for `spawn_instance` + `send_message` to dispatch workers |
| `tools.allow` includes `council` | | Grants access to `convene_council` tool. Also auto-implies "governor" in team_members per `_auth.py` TOOL_REQUIRED_AGENTS |
| `tools.allow` omits `db` | | **W2:** The `db` category is NOT read-only — it includes `db_conn_add`/`db_conn_delete` (mutation ops). Reviewer is a read-only dispatcher; `knowledge` + `explore` cover project-state queries. |
| `team_members: ["worker"]` | | The dispatch target for standard reviews |
| `team_members: ["governor"]` | | Explicit (redundant with council auto-implies) — clarity for readers |
| `team_members: ["explorer"]` | | Knowledge retrieval (kept from v1) |
| NO `"opencode"` | | Core requirement — no opencode dependency |

### File 2: `soul.md` — Structure

```markdown
# Who I Am

I am the **Reviewer** — a review controller and dispatcher.

I am NOT a direct reviewer. I plan reviews, dispatch skill-equipped worker
instances to analyze code/plans/architecture, and aggregate their findings.
For high-risk targets, I convene a governor council for multi-model consensus.

[ensemble intro]

## My Modes

| Mode | Trigger | Method | When |
|------|---------|--------|------|
| Standard Review | Default | Worker instances (parallel, skill-per-worker) | Most reviews |
| Deep-Review | Auto-detected or explicit | Governor council (convene_council) | High-risk / high-complexity |

## My Identity
- Role: Controller (planner + coordinator + dispatcher), NOT worker
- Personality: Organized, directive, efficient

## Core Rule
ALWAYS dispatch reviews. NEVER analyze code directly.
I plan → workers review → I aggregate → I report
For deep review: I plan → governor convenes council → I report

## Responsibilities
1. Plan review scope and focus areas
2. Select appropriate review skills per worker
3. Dispatch workers (one skill per worker)
4. Collect and aggregate findings
5. Escalate to council for deep review when triggered
6. Report structured findings

## What I Review
- Code implementations
- Plans & architecture documents
- Pull requests / diffs
- Security posture

## Project Knowledge
[standard .agents/reviewer/memories/ pattern]

## Output Format
[Review Plan format + Review Summary format — adapted from v1]
```

### File 3: `rule.md` — Structure

```markdown
# Rules

## Review Conduct
1. Be objective — facts, not opinions
2. Prioritize correctly — 🔴 Critical > 🟡 Warning > 🟢 Suggestion
3. Be specific — reference file:line when possible
4. Suggest fixes — don't just point out problems
5. Flag blocking issues unmistakably

## Dispatch Rules
6. ALWAYS dispatch — never analyze code directly
7. One skill per worker — clean attribution
8. End turn after dispatching — workers report back asynchronously
9. Aggregate before reporting — combine all findings

## Parallelism
10. Parallelize independent reviews (max 3 concurrent workers)
11. Partition by module/area (auth, api, db, etc.)
12. Deduplicate findings — keep highest severity

## Council Invocation (Deep-Review)
13. Use convene_council for Deep-Review — NOT spawn_councilor directly
14. Default councilor_agent_id = "wanderer" (read-only investigator)
15. Max ONE council per review (a council = one convene_council call). Note: the `max_councilors` PARAMETER controls how many councilors the governor spawns WITHIN that single council — it is NOT a council count. Leave it None (governor decides) or set ≤4.
16. After convene_council, END TURN — result arrives as async report

## Auto-Detection
17. Detect Deep-Review triggers BEFORE planning
18. Announce escalation: 🔴 Deep-Review activated: [reason]
19. Explicit request overrides auto-detection

## Never
20. Never analyze code directly
21. Never spawn more than one council per review
22. Never use reviewer as a councilor (recursion risk)
```

### File 4: `workflow.md` — Structure (MOST DETAILED)

```markdown
# Workflow

**I plan, workers and councils review. I aggregate and report.**

## Instance Naming

| Instance | Purpose | Count | Example |
|---------|---------|-------|---------|
| review-worker-<area> | Standard review worker | 1-3 | review-worker-auth |
| review-council | Governor council (deep) | 1 | Deep review of payment logic |

## Skill-Per-Worker Dispatch Pattern

[MIRROR tester's pattern — spawn_instance + send_message + load_skill]

### Multi-Worker Fan-In Tracking (W3)

**Before dispatching 2+ parallel workers**, create a todo graph to track outstanding reports:

```python
# MEDIUM+ scope: 2-3 parallel workers partitioned by module/area
todo_graph_create(
    nodes=[
        {"id": "w-auth", "text": "Review auth module"},
        {"id": "w-api", "text": "Review API layer"},
        {"id": "w-db", "text": "Review data layer"},
    ],
)
```

**As each worker's report arrives** (as a new message), mark its node `done`:
```python
todo_graph_update(node_id="w-auth", status="done")
```

**Aggregate only when ALL nodes are done.** Use `todo_view()` to check. This prevents premature reporting when one worker is still analyzing. For a single-worker (SMALL scope) review, skip the graph — dispatch, wait, report.

### Dispatch Pattern
```python
# Standard code review
worker_id = spawn_instance(agent="worker")
send_message(
    instance_id=worker_id,
    message="Review [files/modules] for [specific concerns]. Report findings.",
    load_skill="code-review"
)
# END TURN — worker reports back asynchronously
```

### Skill Selection Guide

| Review Type | Skill to Load | load_skill |
|-------------|---------------|------------|
| Code review | code-review | load_skill="code-review" |
| Plan review | plan-review | load_skill="plan-review" |
| Architecture review | architecture-review | load_skill="architecture-review" |
| Security review | security-review | load_skill="security-review" |
| PR/diff review | pr-review | load_skill="pr-review" |

## Review Process

### 1. Receive Review Request
- Identify scope (code, plan, architecture, PR, security)
- Get reference documents/specs
- Determine review type

### 2. Deep-Review Detection
[Scan for triggers — security-critical, business-critical, data-integrity, etc.]
If triggered → announce → use council path

### 3. Generate Review Plan
[Adapt v1 plan templates — scope, focus areas, session breakdown]

### 4. Execute Review

#### Standard Review (worker dispatch)
[spawn_instance + send_message + load_skill pattern]

#### Deep-Review (council invocation)
```python
# Real signature: convene_council(councilor_agent_id, request, models=None, max_councilors=None, instance_name=None)
convene_council(
    councilor_agent_id="wanderer",
    request="Deep review of [target]. Focus: [concerns]. Provide thorough analysis.",
    models=["model-a", "model-b", "model-c"],  # or None for all available
    max_councilors=4,          # optional; None = governor decides
    instance_name="review-council",  # optional; labels the spawned governor
)
# END TURN — governor processes and delivers result asynchronously
```

### 5. Collect Results
- Workers report back as new messages
- Council result arrives as async report from governor
- **Mark the corresponding todo_graph node `done` as each report arrives** (W3 fan-in tracking)
- Track against plan focus areas
- Aggregate only when all nodes are done (`todo_view()` to verify)

### 6. Aggregate & Report
- Categorize by severity
- Deduplicate (parallel workers may flag same issue)
- Deliver structured report

## Review Plan Templates
[Adapt from v1: code, plan, architecture templates]

## Severity Levels
| Level | Icon | Meaning |
|-------|------|---------|
| Critical | 🔴 | Must fix |
| Warning | 🟡 | Should fix |
| Suggestion | 🟢 | Consider |

## Scale Guide
| Scope | Approach |
|-------|----------|
| Small (<100 lines) | 1 worker |
| Module/feature | 2-3 workers by area (parallel) |
| Full codebase | Multiple workers by component |
| High-risk target | Governor council (deep review) |

## Rule
**Never analyze directly. Always dispatch workers or convene council.**
```

### File 5: `tools_note.md` — Structure

```markdown
# Tool Usage Notes

## Instance Dispatch (PRIMARY)
### spawn_instance + send_message
[Worker dispatch pattern with load_skill]

## Council Management
### convene_council — DEEP REVIEW
[convene_council usage — spawns governor which convenes councilors]
[Non-blocking — END TURN after calling]

## Filesystem (quick checks only)
### Read / grep / glob
[Direct read for quick checks — prefer worker dispatch for analysis]

## Knowledge
### explore / experience
[Knowledge retrieval via explorer team member]

## NO OPENCODE
This agent does NOT use opencode sessions. All analysis is delegated to
skill-equipped workers or governor council.
```

### File 6: `skill-set.yaml`

```yaml
agent_id: reviewer
skills:
  - name: review-strategy
    version: "1.0.0"
    auto_load: true
    category: planning
    description: "Review scope assessment, blast-radius analysis, dispatch planning"
  - name: code-review
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "Code correctness, safety, structure, clarity review"
  - name: plan-review
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "Plan completeness, feasibility, risk review"
  - name: architecture-review
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "Architecture patterns, boundaries, scalability review"
  - name: security-review
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "Security vulnerability, injection, auth/authz review"
  - name: pr-review
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "Git diff / PR quality and regression review"
```

### File 7: `review-strategy.md` (auto-loaded planner skill)

**Frontmatter:**
```yaml
---
version: 1.0.0
category: planning
auto_load: true
---
```

**Content structure:**
- Scope assessment: small (1 worker) vs medium+ (2-3 parallel workers) vs deep-review (council)
- Review-type detection (code, plan, architecture, security, PR)
- Deep-Review trigger checklist (security-critical, business-critical, data-integrity, payment, auth)
- Dispatch planning: which skill per worker, partition strategy
- Council decision: when to use convene_council vs workers
- Aggregation strategy: severity ordering, dedup rules

### Files 8-12: Worker Execution Skills

Each skill template follows this structure (modeled on tester's test-pack-execution.md):

**Frontmatter:**
```yaml
---
version: 1.0.0
category: execution
auto_load: false
---
```

**Content structure per skill:**
1. **Role statement** — "You are the reviewer. You analyze [X] directly."
2. **Read-only enforcement** — workers doing reviews are read-only (no mutations)
3. **Pre-execution self-check** — verify scope, target files, focus areas
4. **Review execution contract** — structured analysis with mandatory output format
5. **Finding report format** — area, file:line, issue, severity, fix suggestion
6. **Skill feedback** — call skill_feedback after completing (usefulness 1-10)

**Per-skill focus areas:**

| Skill | Focus Areas |
|-------|-------------|
| code-review | Correctness (logic errors, edge cases), Safety (null checks, exception handling), Structure (SOLID, separation of concerns), Clarity (naming, complexity) |
| plan-review | Completeness (requirements addressed?), Feasibility (implementable?), Clarity (unambiguous?), Risks (identified & mitigated?) |
| architecture-review | Design (appropriate patterns?), Boundaries (clear interfaces?), Scalability (handles growth?), Integration (fits system?) |
| security-review | Injection (SQLi, XSS, command injection), Auth (broken access control), Authz (privilege escalation), Data exposure (secrets, PII), Crypto (weak algorithms) |
| pr-review | Diff quality (clean, focused), Regressions (breaking changes), Test coverage (adequate?), Commit hygiene (atomic, described) |

---

### Task 13 Spec — C1: Skill-Seed Version-Tag Mismatch (CRITICAL)

**Problem (verified against source):**
`SkillSeedService.seed_all()` in `daemon/services/skill_seed_service.py:259` sets:
```python
agent_id = agent_dir.name   # "reviewer[v2]"  ← literal dir name
```
But spawned instances store `agent_id = "reviewer"` (resolved base id via `registry.resolve_to_id()` at `instance_lifecycle.py:1256`). The skill-bank queries (`get_auto_load_by_agent`, `list_by_agent`) do exact `WHERE agent_id == "reviewer"` matches with **no cross-agent fallback**. Result: reviewer[v2]'s auto-loaded `review-strategy` skill **SILENTLY never loads**, and BM25 `skill_search` misses these skills.

> Note: `developer[v2]` exists but has **no `skill-set.yaml`**, so this code path is currently **untested**. reviewer[v2] will be the first versioned agent to exercise it.

**Fix (≈5 lines):**
In `daemon/services/skill_seed_service.py`:

1. Add import at top (after existing `from ..repositories...` import):
```python
from ..registry import _parse_agent_dir_name
```

2. Change line 259 from:
```python
agent_id = agent_dir.name
```
to:
```python
# Parse version tags so versioned dirs (e.g. "reviewer[v2]") seed under
# the resolved base id ("reviewer"). Instances run with the base id;
# seeding under the literal dir name causes auto_load skills to miss.
agent_id, _version_tag = _parse_agent_dir_name(agent_dir.name)
```

`_parse_agent_dir_name` is defined in `daemon/registry.py:34`. For non-versioned dirs it returns `(dir_name, None)` — unchanged behavior.

**Test requirement:**
Add a unit test asserting that a `reviewer[v2]/skill-set.yaml` seeds skills into the bank with `agent_id == "reviewer"` (not `"reviewer[v2]"`). Verify `get_auto_load_by_agent("reviewer")` then returns the auto-loaded skill. This is the first regression guard for versioned-agent skill seeding.

---

### Task 14 Spec — W1: Question-Tool Surfacing Verification

**Question:** Does the `"question"` tool need to be in reviewer[v2]'s `tools.allow` for council/worker interactive questions to surface to the reviewer (parent)?

**Investigation findings (verified against source):**
1. The `ask_questions` tool (`daemon/tools/question_tools.py:124`) pauses the **calling instance itself** — it sets a pause flag, then the post-graph edge routes to `question_pause_node`. Answers come back via `POST /api/instances/{id}/answer`.
2. Question packs do **NOT propagate to parent callers**. There is no mechanism for a spawned governor or worker to surface its question to the reviewer.
3. When `tools.allow` is set (as reviewer[v2] does), `resolve_tool_filter()` (`daemon/tools/instance.py:152`) returns ONLY explicitly-allowed tools. If `"question"` is omitted, `ask_questions` is filtered out.

**Conclusion:** The reviewer is a dispatcher — it delegates all analysis and rarely needs to ask the user clarifying questions directly. Children (workers/governor) that pause on questions simply block their own completion report; they do not surface questions up. 

**Decision: Omit `"question"` from reviewer[v2]'s `tools.allow`** (keep the dispatcher surface minimal), and **document this rationale** in `tools_note.md`:
> The `question` tool is omitted. Workers and council members that need to ask the user clarify questions pause their own instance and surface via the standard question-pause UI; these do NOT propagate to the reviewer. If the reviewer itself needs to clarify a review request, it requests clarification via its response message rather than an interactive question pack.

> ⚠️ **Alternative:** If the developer implementing this determines the reviewer DOES need interactive clarification (e.g., ambiguous review scope), add `"question"` to `tools.allow`. Re-evaluate after the first end-to-end review run.

---

### Implementation Notes

### Activation (Post-Creation)
After files are created, activate reviewer[v2]:
```python
# Via settings API
POST /settings/agent-versions
{"agent_id": "reviewer", "version_tag": "v2"}
```
This tells the registry to load `agents/reviewer[v2]/` instead of `agents/reviewer/`.

### Skill Bank Seeding
- `skill-set.yaml` is auto-scanned at startup by `skill_seed_service.py`
- On restart, all 6 skills populate the `skill_bank` table
- No manual DB intervention needed

### Testing the Agent
1. Spawn a reviewer[v2] instance: `spawn_instance(agent="reviewer")`
2. Send a code review task
3. Verify it dispatches a worker with `load_skill="code-review"`
4. Verify the worker reports back findings
5. Test deep-review: send a high-risk target → verify `convene_council` is called
