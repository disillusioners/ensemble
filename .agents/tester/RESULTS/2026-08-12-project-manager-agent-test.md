# Test Report: Project Manager Agent

Date: 2026-08-12
Instance IDs: a03e7e52-5704-48ab-b2e2-c8ce5a0bc527 (write), 3ca8f07f-ca98-4535-abdc-9550ddca541e (run)

## Summary
- Total: 51 | Passed: 51 | Failed: 0 | Errors: 0 | Skipped: 0
- Unit Tests: 51 tests (pure file + registry parsing, no DB/daemon)
- Quick Fixes Applied: 0 (agent files were clean)
- Quarantined: 0

## Scope Decision
> New standalone agent at `agents/project-manager/` with 5 files (meta.json, soul.md, rule.md, workflow.md, tools_note.md). No existing tests. Created `tests/unit/test_project_manager_agent.py` covering 5 categories: meta.json schema, tool allowance security, agent discovery, convention compliance, and prompt composition. Scoped to this agent only — no cross-module dependencies, no integration tests (standalone agent, deferred to manual smoke testing).

## Test Coverage by Category

### 1. meta.json Schema & Configuration Validation (14 tests) — ✅ PASS
- Valid JSON, all required fields present
- `id == "project-manager"`, `name == "Project Manager"`, `version == "1.0.0"`
- `team_members == []` (stand-alone)
- `skill_injection == false`
- `context_injection.heuristic_match_shared_md_files == true`
- Agent id matches directory name

### 2. Tool Allowance Correctness — Security (7 tests) — ✅ PASS
- **No write-capable tool in `tools.allow`** — KNOWN_WRITE_TOOLS ∩ allow = ∅
- **All write paths in `tools.deny`** — covers edit_file, write_file, bash, spawn_instance, send_message, terminate_instance, experience, instance, self, shared_meta_kv, mcp, question
- **All project write tools in `tools.deny`** — project_create, project_delete, project_update, project_set_status, project_cn_add/remove, project_history_add/delete
- **No overlap** — allow ∩ deny = ∅
- **Read-only tools present in allow** — project_get, project_list, project_search, explore, project_cn_list, project_history_list
- **All allow entries resolve** — verified against CATEGORY_MODULES + factory tool names

### 3. Agent Discovery (5 tests) — ✅ PASS
- Not in SKIP_DIRS
- AgentRegistry.discover() finds "project-manager"
- Present in list_all()
- Metadata fields correct (id, name, version)
- AgentMetadata.model_validate() succeeds

### 4. Convention Compliance (18 tests) — ✅ PASS
- All 4 prompt files exist and non-empty
- **No forbidden system tokens** (parametrized: meta.json, tools.allow, tools.deny, daemon/, _tool_registry, skill-set.yaml, agent_id=, seed_all, innate_skills, default_agent_versions) — 0 hits across all .md files
- **No provenance markers** (parametrized: TODO, FIXME, HACK, DRAFT, PLACEHOLDER, XXX) — 0 hits
- **Cardinal count exactly 7** (convention ≤7)
- First-person voice present in soul.md
- All 4 workflow flows present (Risk Assessment, Progress Reporting, Scope Assessment, Decision Framing)
- Tool justification table present in tools_note.md

### 5. Prompt Composition (7 tests) — ✅ PASS
- System prompt assembles from all files without error
- soul.md has Tone/Voice directive
- rule.md states read-only constraint
- rule.md states no-dispatch constraint
- Cross-doc linking verified

## Execution Details
- **Runtime**: 1.05s
- **Framework**: pytest
- **DB**: None required (pure file + registry parsing)
- **Warning**: 1 pre-existing langchain_core Pydantic V1 / Python 3.14 incompatibility (not related to this test)

## Issues Found
None. The project-manager agent definition was clean and fully conformant to all conventions. No quick-fixes were needed.

## Documentation Updated
- [x] PACKS.md — added `project_manager_agent_unit_test` entry, updated total count (252)
- [x] RESULTS/2026-08-12-project-manager-agent-test.md — this report

---

### Overall Status
- Unit Tests: ✅ PASS (51/51)
- **Testing Complete**: ✅ READY
