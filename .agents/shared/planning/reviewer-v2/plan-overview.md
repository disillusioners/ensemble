# Plan Overview: Reviewer v2 — Worker+Skill+Council Architecture

## Objective
Replace the opencode-based reviewer with a dispatcher architecture that delegates reviews to skill-equipped worker instances (standard reviews) and to the governor council (deep reviews), following the proven tester pattern.

## Scope Assessment
**MEDIUM** — One agent directory (`agents/reviewer[v2]/`) with 5 core files + skill-set.yaml + 6 skill templates, plus **one small backend Python fix** (`daemon/services/skill_seed_service.py`, ~5 lines — see Task 13 / C1). All other needed infrastructure already exists: `load_skill` dispatch, `convene_council` tool, governor council system, skill bank seeding (after the C1 fix). The backend fix is **mandatory**: without it, versioned agents' auto_load skills seed under the wrong key and silently never load.

## Context
- Project: agents-ensemble
- Working Directory: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- Versioning: `agents/reviewer[v2]/` parses as base="reviewer" + tag="v2" via registry.py `_parse_agent_dir_name()`
- Activation: After creating files, set `version_tag="v2"` for reviewer via settings API (`POST /settings/agent-versions`)

## Key Architecture Decisions (see decisions.md for full rationale)

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Reviewer is a **dispatcher/controller**, never a direct reviewer | Mirrors tester's test-leader pattern; clean skill attribution |
| D2 | **`convene_council`** for deep review (not `spawn_councilor` directly) | `convene_council` has NO identity guard — any agent with `"council"` in tools.allow can use it. It spawns a governor child which convenes councilors. |
| D3 | **`"council"` in tools.allow** auto-implies `"governor"` in team_members | Confirmed in `_auth.py` TOOL_REQUIRED_AGENTS map: `{"council": ["governor"]}`. No need to duplicate governor in team_members, but we do for clarity. |
| D4 | Default councilor agent = **`wanderer`** (read-only investigation specialist) | Purpose-built for read-only code analysis; avoids recursion risk of using reviewer-as-councilor |
| D5 | **6 review skills**: 1 auto-loaded strategy + 5 worker-execution skills | Mirrors tester's 1 planning skill + N execution skills pattern |
| D6 | Keep `"dynamic-skill"` in innate_skills + `skill_injection: true` | Enables skill evolution, skill_search/feedback tools for workers |
| D7 | **Remove `"opencode"` from innate_skills** entirely | Core requirement — no opencode dependency |

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Agent Core Files + Backend Fix | Create the 5 agent definition files + skill-set.yaml + skill templates in `agents/reviewer[v2]/` AND fix `skill_seed_service.py` (C1) | None | — (root) | 2.5-3.5h |

> **Task ordering within Phase 1:** Task 13 (backend C1 fix) should land first or alongside the agent files — the agent is non-functional (auto_load skill never loads) without it. The backend fix is independent of the agent markdown files (different files), so a developer could implement both in parallel, but verify the fix before testing the agent end-to-end.

### Coupling Assessment
Single phase — all files are created together as a coherent unit. No multi-phase dependencies.

## Files to Create (Phase 1)

### Agent Definition Files (`agents/reviewer[v2]/`)
| File | Purpose |
|------|---------|
| `meta.json` | Agent metadata: tools, innate_skills, team_members, version |
| `soul.md` | Identity: review controller/dispatcher, never direct reviewer |
| `rule.md` | Rules: dispatch conduct, council invocation, finding severity |
| `workflow.md` | Workflow: plan → dispatch workers → collect → aggregate; deep-review via council |
| `tools_note.md` | Tool reference: instance dispatch, council, filesystem, knowledge |

### Skill Bank Files (`agents/reviewer[v2]/`)
| File | Purpose | auto_load |
|------|---------|-----------|
| `skill-set.yaml` | Skill manifest for DB seeding | — |
| `skills-template/review-strategy.md` | Reviewer's OWN planning skill (blast-radius, scope, dispatch planning) | **true** |
| `skills-template/code-review.md` | Worker skill: review code for correctness/safety/structure/clarity | false |
| `skills-template/plan-review.md` | Worker skill: review plans for completeness/feasibility/risks | false |
| `skills-template/architecture-review.md` | Worker skill: review architecture for patterns/boundaries/scalability | false |
| `skills-template/security-review.md` | Worker skill: review for vulnerabilities/injection/auth/authz | false |
| `skills-template/pr-review.md` | Worker skill: review git diffs/PRs for quality/regressions | false |

## meta.json Design (Key Configuration)

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

**Key changes from v1:**
- ❌ Removed `"opencode"` from innate_skills
- ✅ Added `"dynamic-skill"` to innate_skills (skill evolution)
- ✅ Added `"council"` to tools.allow (enables `convene_council`; auto-implies governor in team_members)
- ✅ Added `"instance"` to tools.allow (spawn_instance + send_message for worker dispatch)
- ❌ **Omitted `"db"`** from tools.allow — the `db` category includes mutating ops (`db_conn_add`/`db_conn_delete`); reviewer is a read-only dispatcher (W2). Use `knowledge` + `explore` for project-state queries.
- ✅ Added `"worker"` to team_members (the dispatch target)
- ✅ Added `"governor"` to team_members (explicit; also auto-implied by council category)
- ✅ Kept `"explorer"` for knowledge retrieval
- ✅ Added `skill_injection: true`, `context_injection: true`

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **C1: skill-seed keys skills under `"reviewer[v2]"` instead of `"reviewer"`** — auto_load skills silently never load | **high** | **Fix `skill_seed_service.py:259` to parse version tag via `_parse_agent_dir_name`** (Task 13). Add regression test. First versioned agent to exercise this path (developer[v2] has no skill-set.yaml). |
| Councilor recursion if councilor_agent_id="reviewer" | high | Default to `wanderer` as councilor — purpose-built read-only investigator, no dispatch logic |
| `convene_council` is non-blocking — reviewer must wait for async report | medium | Document in workflow: after convene_council, END TURN; report arrives as new message (same pattern as worker dispatch) |
| Skill bank not seeded on first run | low | skill-set.yaml is auto-scanned at startup by skill_seed_service.py; restart or call seeding API |
| Reviewer lacks opencode for heavy file analysis | medium | Workers have filesystem+bash tools; can read/grep/glob directly. For very large codebases, spawn multiple workers partitioned by module. |
| Version activation confusion (v1 still active) | low | Document: set version_tag="v2" via settings API to activate; v1 remains as fallback |

## Success Criteria
- [ ] `agents/reviewer[v2]/` directory exists with all 12 files
- [ ] **`skill_seed_service.py` C1 fix applied** — versioned-agent skills seed under base id `"reviewer"`
- [ ] **Regression test added** — `get_auto_load_by_agent("reviewer")` returns the auto-loaded skill after seeding from a `[v2]` dir
- [ ] `meta.json` has NO `"opencode"` in innate_skills
- [ ] `meta.json` has `"council"` in tools.allow
- [ ] `meta.json` does NOT have `"db"` in tools.allow (read-only dispatcher — W2)
- [ ] `skill-set.yaml` registers 6 skills (1 auto_load + 5 on-demand)
- [ ] `workflow.md` documents worker dispatch pattern (spawn_instance + send_message + load_skill)
- [ ] `workflow.md` documents council invocation via `convene_council`
- [ ] `workflow.md` documents the `todo_graph_create` multi-worker fan-in step (W3)
- [ ] `soul.md` establishes dispatcher identity (never direct reviewer)
- [ ] W1 rationale documented in tools_note.md (question-tool omission)
- [ ] After version_tag activation, reviewer[v2] can dispatch a code-review worker
- [ ] After version_tag activation, reviewer[v2] can convene a council for deep review

## Tracking
- Created: 2026-07-27
- Last Updated: 2026-07-27 (Revision: addressed C1 critical fix + W1-W3 warnings + S1 signature fix per source-code review)
- Status: draft (revised)
