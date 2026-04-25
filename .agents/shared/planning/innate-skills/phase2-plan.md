# Phase 2: Update Agent Configs

## Objective

Add the `innate_skills` field to all agent `meta.json` files, declaring which centralized skills each agent should load. This can run in parallel with Phase 1.

## Coupling

- **Depends on**: None (skill names are known from exploration)
- **Coupling type**: loose with Phase 1 (only needs skill names, not files)
- **Shared files with other phases**: `agents/*/meta.json` (Phase 4 verifies these)
- **Shared APIs/interfaces**: None
- **Why this coupling**: Phase 3 reads the `innate_skills` field; Phase 4 verifies correctness

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update `coder/meta.json` | Add `"innate_skills": ["opencode"]` | `agents/coder/meta.json` |
| 2 | Update `reviewer/meta.json` | Add `"innate_skills": ["opencode"]` | `agents/reviewer/meta.json` |
| 3 | Update `tester/meta.json` | Add `"innate_skills": ["opencode", "test-pack"]` | `agents/tester/meta.json` |
| 4 | Update `planner/meta.json` | Add `"innate_skills": ["opencode"]` | `agents/planner/meta.json` |
| 5 | Update `tidier/meta.json` | Add `"innate_skills": ["opencode"]` | `agents/tidier/meta.json` |
| 6 | Update `approver/meta.json` | Add `"innate_skills": ["opencode"]` | `agents/approver/meta.json` |
| 7 | Update `leader/meta.json` | Add `"innate_skills": ["coordination"]` | `agents/leader/meta.json` |
| 8 | Update `jober/meta.json` | Add `"innate_skills": ["job-orchestration"]` | `agents/jober/meta.json` |

## Mapping Table

| Agent | `innate_skills` Value | Current Skills |
|-------|-----------------------|----------------|
| coder | `["opencode"]` | `skills/opencode/` |
| reviewer | `["opencode"]` | `skills/opencode/` |
| tester | `["opencode", "test-pack"]` | `skills/opencode/`, `skills/test-pack/` |
| planner | `["opencode"]` | `skills/opencode/` |
| tidier | `["opencode"]` | `skills/opencode/` |
| approver | `["opencode"]` | `skills/opencode/` |
| leader | `["coordination"]` | `skills/coordination/` |
| jober | `["job-orchestration"]` | `skills/job-orchestration/` |
| giter | *(omit field)* | *(none)* |
| _mother | *(omit field)* | *(none, system)* |
| _inner_soul | *(no meta.json)* | *(none, system)* |
| _baby_template | *(omit field)* | *(none, template)* |

**Note on ordering**: The `innate_skills` array order matters for prompt composition. List skills in **alphabetical order** to match current behavior where `sorted(skills_dir.iterdir())` produces alphabetical results. For tester: `["opencode", "test-pack"]` (already alphabetical).

## Key Files

- `agents/coder/meta.json` — current: `{"id":"coder", "name":"Coder", ...}`
- `agents/reviewer/meta.json`
- `agents/tester/meta.json`
- `agents/planner/meta.json`
- `agents/tidier/meta.json`
- `agents/approver/meta.json`
- `agents/leader/meta.json`
- `agents/jober/meta.json`

## Constraints

- **Preserve all existing fields** in `meta.json` — only add `innate_skills`
- **Alphabetical order** within `innate_skills` array
- **Omit field entirely** for agents with no skills (giter, _mother, _baby_template) — cleaner than empty array
- **No schema change required**: `AgentMetadata` already has `model_config = ConfigDict(extra="ignore")`, so the new field will be silently accepted. However, the loader needs to read it (Phase 3).

## Deliverables

- [ ] All 8 agent `meta.json` files updated with correct `innate_skills` arrays
- [ ] Agents without skills have no `innate_skills` field
- [ ] JSON is valid (no syntax errors)
- [ ] Alphabetical ordering within each array
