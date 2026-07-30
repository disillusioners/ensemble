# Plan Overview: Create Developer[v2] and Planner[v2] Agents

## Objective

Create two new v2 agents — **Developer[v2]** (coder orchestrator) and **Planner[v2]** (research + plan dispatcher) — following the established worker-dispatch pattern from reviewer[v2] and approver[v2]. Both fully remove opencode, adopt the skill triad (`skill_injection` + `dynamic-skill` + `no_force_explore`), and dispatch work to team members via `send_message(load_skill=...)`.

## Scope Assessment

**LARGE** — Two complete v2 agent definitions (7 files + skill-set.yaml + N skill templates each). Each agent requires: meta.json, soul.md, rule.md, workflow.md, tools_note.md, skill-set.yaml, and 5–6 skill template files. Pattern is well-established (reviewer[v2], approver[v2]) so implementation risk is low, but volume is high (~20 files total).

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Pattern Reference**: `agents/reviewer[v2]/` and `agents/approver[v2]/` (full 7-file v2 structure)
- **Base Agent References**: `agents/developer/`, `agents/planner/`, `agents/coder/`, `agents/worker/`

## V2 Agent Architecture (Established Pattern)

### File Structure (per agent)
```
agents/<agent>[v2]/
├── meta.json              # Configuration: id, version, tools, team_members, skill triad
├── soul.md                # Identity, nature, personality, workflow overview + mermaid chart
├── rule.md                # Numbered rules/constraints
├── workflow.md            # Detailed process: dispatch patterns, decision trees, templates
├── tools_note.md          # Tool usage rationale
├── skill-set.yaml         # Skill declarations (agent_id uses BASE id, NOT versioned)
└── skills-template/       # Skill content files loaded via load_skill
    ├── <strategy-skill>.md     # auto_load: true — dispatch planning
    └── <execution-skill>.md    # auto_load: false — per-worker execution skills
```

### Key v2 Conventions
| Convention | Detail |
|-----------|--------|
| Directory naming | `agent[v2]` (bracket notation) |
| meta.json `id` | BASE id only (e.g., `"developer"`, NOT `"developer[v2]"`) |
| meta.json `version` | `"2.0.0"` |
| skill-set.yaml `agent_id` | BASE id (matches runtime resolution; critical for skill bank) |
| Skill triad | `skill_injection: true` + `dynamic-skill` in `innate_skills` + `no_force_explore: true` |
| No opencode | Removed from `innate_skills`, `tools.allow`, all references |
| Worker-dispatch | `spawn_instance` + `send_message(load_skill=...)` + END TURN |
| Skill-per-worker | One skill per worker for clean attribution |
| `context_injection` | `{"heuristic_match_shared_md_files": true}` |

### meta.json Standard Fields
```json
{
  "id": "<base-id>",
  "name": "<Display Name>",
  "description": "<one-line role description>",
  "icon": "<emoji>",
  "color": "accent-<name>",
  "version": "2.0.0",
  "innate_skills": ["todo", "chart", "dynamic-skill"],
  "skill_injection": true,
  "no_force_explore": true,
  "context_injection": { "heuristic_match_shared_md_files": true },
  "tools": { "allow": ["instance", ...] },
  "team_members": ["...", "..."]
}
```

---

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Developer[v2] — Coder Orchestrator | Build complete Developer[v2] agent (9 files + 5 skills) | None | — | 2–3h |
| 2 | Planner[v2] — Research & Plan Dispatcher | Build complete Planner[v2] agent (9 files + 5 skills) | None | — | 2–3h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| Phase 1 ↔ Phase 2 | **independent** | Different directories, different files, no shared code. Both follow the same external pattern (reviewer/approver v2) but do not reference each other. |

**⚠️ Both phases CAN run in parallel** — they touch entirely separate directories (`agents/developer[v2]/` vs `agents/planner[v2]/`) with no shared files or APIs.

---

## Agent Architecture Summary

### Agent 1: Developer[v2] — CODER ORCHESTRATOR

| Aspect | Specification |
|--------|---------------|
| **Purpose** | Main development agent orchestrating coding work via coder + worker delegation |
| **Architecture** | Two-tier dispatch: Coder for complex/multi-file tasks, Worker (with skills) for quick/review/commit tasks |
| **team_members** | `["coder", "worker"]` |
| **Tools** | `instance` + `bash` + `proc` + `filesystem` + `time` + `self` + `help` + `image` + `knowledge` + `mcp` + `context` + `shared_context` + `git` |
| **NO** | opencode (anywhere) |
| **Skills** | dev-strategy (auto_load) + code-implementation, code-fix, code-refactor, git-commit, quick-review |
| **Fallback** | Worker WITHOUT skill + detailed request for unknown/general scenarios |

### Agent 2: Planner[v2] — RESEARCH + PLAN DISPATCHER

| Aspect | Specification |
|--------|---------------|
| **Purpose** | Strategic planning agent using explore (research) + worker (skill-equipped execution) |
| **Architecture** | Explore for codebase investigation, Worker with skills for plan creation/analysis |
| **team_members** | `["worker", "explorer"]` (NO coder) |
| **Tools** | `instance` + `bash` + `proc` + `filesystem` + `time` + `self` + `help` + `image` + `knowledge` + `mcp` + `context` + `shared_context` |
| **NO** | opencode, coder, direct code writing |
| **Skills** | plan-strategy (auto_load) + plan-creation, roadmap-strategy, requirements-analysis, technical-analysis, feasibility-study |
| **Fallback** | Worker WITHOUT skill + detailed request for unknown/general scenarios |

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Skill-seed versioned agent dir gotcha: skill bank keys by literal dir name (`developer[v2]`) but instances store `agent_id=developer` | Medium | Known pattern — cross-agent fallback covers load_skill; auto_load may miss. Test auto_load skills after seeding. Reference: `skill-seed-versioned-agent-dir-gotcha` |
| load_skill doesn't create SkillUsageRecord (feedback loop breakage) | Low | Known systemic bug affecting all v2 agents. Not blocking — documented in approver/reviewer v2 already |
| Developer[v2] has both `coder` AND `worker` team members — dispatch decision complexity | Medium | Clear decision tree in soul.md + workflow.md: coder = complex/multi-file, worker = quick/single-file/skill-based. Explicit rules in rule.md |
| Planner[v2] excludes coder — team_members must be exactly `["worker", "explorer"]` | Low | meta.json explicitly lists only worker + explorer. rule.md enforces "never spawn coder" |
| Skill content quality for new skills (code-implementation, plan-creation, etc.) | Medium | Follow code-review.md / approval-strategy.md template depth: frontmatter + role definition + pre-execution self-check + focus areas + mandatory output format + skill_feedback instruction |

## Success Criteria

- [ ] `agents/developer[v2]/` contains all 7 required files + skill-set.yaml + 5 skill templates
- [ ] `agents/planner[v2]/` contains all 7 required files + skill-set.yaml + 5 skill templates
- [ ] Both meta.json files use `version: "2.0.0"`, base id, skill triad, no opencode
- [ ] Both skill-set.yaml files use base `agent_id` (NOT versioned)
- [ ] Both soul.md files contain mermaid workflow charts
- [ ] Both rule.md files have numbered rules with dispatch discipline + END TURN rule
- [ ] Both workflow.md files have dispatch patterns, decision trees, skill selection guides
- [ ] Both tools_note.md files document instance dispatch + END TURN rationale
- [ ] All skill templates have frontmatter + role definition + output format + skill_feedback instruction
- [ ] No references to opencode anywhere in any file

## Tracking
- Created: 2026-07-30
- Last Updated: 2026-07-30
- Status: draft
