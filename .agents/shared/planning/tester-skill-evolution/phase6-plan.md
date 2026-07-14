# Phase 6: Tester Wiring

## Objective

Update the tester agent's `meta.json` to enable skill injection and add the `dynamic-skill` innate skill, activating the two-layer skill model.

## Coupling

- **Depends on**: Phase 5 (auto_load prompt section must be ready), Phase 7 (innate skill updates)
- **Coupling type**: loose — only flips config flags; the code from P5/P7 must exist but there's no runtime code dependency
- **Shared files with other phases**: `agents/tester/meta.json`
- **Shared APIs/interfaces**: `AgentMetadata.skill_injection` field, `AgentMetadata.innate_skills` array
- **Why this coupling**: Enabling skill_injection activates the injection pipeline (needs P4 clone + P5 auto_load); adding dynamic-skill innate skill (P7 updates its content)

## Context

### Current tester/meta.json

```json
{
  "id": "tester",
  "name": "Tester",
  "description": "Writes and runs tests, reports results",
  "icon": "🧪",
  "color": "accent-green",
  "innate_skills": ["opencode", "test-pack", "todo"],
  "tools": {
    "allow": ["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context", "shared_context", "db"]
  },
  "team_members": ["explorer"]
}
```

### Required Changes

1. Add `"skill_injection": true` — enables the injection pipeline for this agent
2. Add `"dynamic-skill"` to `innate_skills` array — teaches the agent about dynamic skill tools

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update meta.json | Add skill_injection + dynamic-skill | `agents/tester/meta.json` |
| 2 | Verify registry resolution | AgentMetadata resolves with skill_injection=true | Manual check / `tests/test_registry_skill_injection.py` |

## Detailed Changes

### 6.1 meta.json Update

**File**: `agents/tester/meta.json`

```json
{
  "id": "tester",
  "name": "Tester",
  "description": "Writes and runs tests, reports results",
  "icon": "🧪",
  "color": "accent-green",
  "innate_skills": ["opencode", "test-pack", "todo", "dynamic-skill"],
  "skill_injection": true,
  "tools": {
    "allow": ["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context", "shared_context", "db"]
  },
  "team_members": ["explorer"]
}
```

Changes:
- `innate_skills`: `["opencode", "test-pack", "todo"]` → `["opencode", "test-pack", "todo", "dynamic-skill"]`
- **NEW**: `"skill_injection": true`

### 6.2 Impact on System Behavior

| Change | Effect |
|--------|--------|
| `"skill_injection": true` | `instance_messaging.py:1915` activates the injection pipeline for tester instances |
| `"dynamic-skill"` in innate_skills | `load_agent_skills()` loads `agents/_prompt_system/innate-skills/dynamic-skill/skill.md` into system prompt section #4 |
| Combined | Tester gets BOTH auto_load skills (section #4.5) AND on-demand skill injection (HumanMessage before user messages) |

### 6.3 Tool Category Check

The `dynamic-skill` innate skill provides 6 tools: `skill_search`, `skill_list`, `skill_view`, `skill_create`, `skill_fix`, `skill_feedback`.

Check if `dynamic-skill` needs to be in `INNATE_SKILL_TOOL_CATEGORIES` mapping in `daemon/tools/instance.py:52`. If the dynamic-skill tools are in the "dynamic-skill" tool category, they need to be auto-granted.

**File**: `daemon/tools/instance.py:52`

Current mapping:
```python
INNATE_SKILL_TOOL_CATEGORIES = {
    "opencode": ["external_opencode"],
    "chart": ["chart"],
}
```

Check whether `dynamic-skill` tools are registered under a "dynamic-skill" category. If yes, add:
```python
INNATE_SKILL_TOOL_CATEGORIES = {
    "opencode": ["external_opencode"],
    "chart": ["chart"],
    "dynamic-skill": ["dynamic-skill"],  # If tools are categorized this way
}
```

**Investigate before implementing**: Check how dynamic-skill tools are categorized in the tool registry. If they're already accessible via the `tools.allow` list or a different mechanism, no change needed.

## Key Files

- `agents/tester/meta.json` — configuration update
- `daemon/tools/instance.py:52` — tool category mapping (verify, may not need change)
- `daemon/registry.py:98` — `AgentMetadata.skill_injection` field (already exists)

## Constraints

- This is a **non-destructive** change — adding fields doesn't break existing behavior
- If `skill_evolution` config is not set, the injection pipeline is a no-op even with `skill_injection: true` (all services are None)
- The `dynamic-skill` innate skill file must exist at `agents/_prompt_system/innate-skills/dynamic-skill/skill.md` (it already does)

## Test Strategy

**Extend**: `tests/test_registry_skill_injection.py`

Verify:
1. Tester agent resolves with `skill_injection=True`
2. Tester agent's `innate_skills` includes `dynamic-skill`
3. System prompt for tester includes the dynamic-skill innate skill content

### P6+P7 Integration Test (W5)

Phase 6 (meta.json wiring) and Phase 7 (innate skill updates) must be tested **together** to verify the full chain works:

**NEW**: `tests/test_tester_skill_chain.py` (or extend existing tester tests)

Test cases:
1. **Full prompt composition** — spawn tester instance → system prompt contains:
   - test-pack innate skill WITH "Evolvable Skills" reference section (P7)
   - dynamic-skill innate skill WITH "Two Load Modes" section (P7)
   - auto_load skills section (if skills seeded + cloned: P3+P4+P5)
2. **Skill injection active** — send message to tester → injection pipeline triggers (P6 enables it)
3. **Dynamic-skill tools available** — tester has skill_search, skill_list, etc. in tool list
4. **End-to-end flow** — seed bank → spawn tester → verify auto_load section → send task → verify on-demand injection

## Deliverables

- [ ] `agents/tester/meta.json` has `"skill_injection": true`
- [ ] `agents/tester/meta.json` has `"dynamic-skill"` in `innate_skills`
- [ ] Registry resolves tester with correct metadata
- [ ] (If needed) `INNATE_SKILL_TOOL_CATEGORIES` updated for dynamic-skill
