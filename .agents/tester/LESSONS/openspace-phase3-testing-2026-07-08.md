# OpenSpace MCP Integration Phase 3 — Testing Lessons

## Date: 2026-07-08

### Feature Overview
Phase 3 added an innate skill (`openspace`), feature documentation, and updated integration/API docs for the OpenSpace MCP integration. This phase is purely instructional/documentation — no new tool execution code (that was Phase 1-2).

### Key Findings

#### 1. Innate Skill Loading Architecture Verified
- `load_agent_skills()` in `daemon/loader.py:268-298` auto-discovers skill directories by matching the `innate_skills` list in agent config
- Skills stored centrally at `agents/_prompt_system/innate-skills/{name}/skill.md`
- `compose_system_prompt()` includes innate skills as section #4 in 11-section pipeline
- **No coupling to `INNATE_SKILL_TOOL_CATEGORIES`** — the loader never consults it
- "openspace" is correctly NOT in `INNATE_SKILL_TOOL_CATEGORIES` (instructional-only skill)

#### 2. Graceful Degradation Pattern
- Declared-but-missing skills log a WARNING and are silently dropped — they do NOT raise or abort prompt composition
- Empty `innate_skills: []` falls through to legacy `skills/` directory scan (treated as "absent")
- `expand_allow_for_innate_skills()` uses `.get(skill, [])` for unknown skills — safe no-op

#### 3. Content Quality Verified
- All 4 MCP tools (`mcp_openspace_execute_task`, `mcp_openspace_search_skills`, `mcp_openspace_fix_skill`, `mcp_openspace_upload_skill`) documented
- Cost warning about `execute_task` double LLM cost is explicit and thorough
- Format consistent with existing innate skills (opencode, chart)
- ENV vars and transport modes accurately documented across all files

#### 4. Cross-File Consistency Check
- `mcp-integration.md` and `api-reference.md` config keys (`openspace_model`, `openspace_max_iterations`, `openspace_backend_scope`) are identical — no drift
- Tool ordering in api-reference.md differs slightly from skill.md (execute_task first vs search_skills first) — cosmetic only

### Test Coverage Added
- `tests/unit/test_openspace_skill_loading.py` — 17 tests covering discovery, composition inclusion, composition exclusion, and tool-categories decoupling
- Commit: `89a9d451`

### Non-blocking Observations
- `OPENSPACE_LLM_API_KEY` documented as "Yes (STDIO mode)" but actually needed in any mode where OpenSpace's LLM agent runs
- `pkill -f "openspace.mcp_server"` in docs is safe (scoped) but could use a safety comment
- Related footer paths in skill.md imply agent root but skills live under `_prompt_system/innate-skills/`
